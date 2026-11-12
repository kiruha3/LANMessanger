"""Local AI state and RPC integration; no provider credentials or network required."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication

from app.core.ai import AIStore, JsonLines, KeyVault, PiProcess, application_folder, prepare_environment

APP = QCoreApplication.instance() or QCoreApplication([])


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for child process")


FAKE = r'''
import json, sys
def emit(value):
    raw = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    sys.stdout.buffer.write(raw[:7]); sys.stdout.buffer.flush()
    sys.stdout.buffer.write(raw[7:]); sys.stdout.buffer.flush()
automatic = {"set_auto_retry":True, "set_auto_compaction":True}
for line in sys.stdin.buffer:
    cmd = json.loads(line)
    kind = cmd["type"]
    if kind in automatic:
        automatic[kind] = cmd["enabled"]
        emit({"type":"response", "command":kind, "success":True})
    elif kind == "get_state":
        emit({"type":"response", "command":kind, "success":True, "data":{"isStreaming":False}})
    elif kind == "get_messages":
        emit({"type":"response", "command":kind, "success":True, "data":{"messages":[]}})
    elif kind == "prompt":
        if cmd["message"] == "crash": sys.exit(3)
        ok = cmd["message"] != "reject" and not any(automatic.values())
        emit({"type":"response", "id":cmd["id"], "command":kind, "success":ok, "error":"rejected"})
        if ok:
            emit({"type":"agent_start"})
            emit({"type":"message_update", "assistantMessageEvent":{"type":"text_delta", "delta":"Привет\u2028мир"}})
            if cmd["message"] != "wait": emit({"type":"agent_end", "messages":[]})
    elif kind == "abort":
        emit({"type":"response", "command":"abort", "success":True})
        emit({"type":"agent_end", "messages":[]})
'''


class AIStateTests(unittest.TestCase):
    def test_lf_framing_preserves_fragmented_utf8_and_unicode_separator(self):
        parser = JsonLines()
        raw = (json.dumps({"text": "Я\u2028ты"}, ensure_ascii=False) + "\n{}\r\n").encode()
        result = []
        for byte in raw:
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual(result, [{"text": "Я\u2028ты"}, {}])

    def test_invalid_protocol_is_reported(self):
        with self.assertRaises(ValueError):
            JsonLines().feed(b"not json\n")

    def test_exe_folder_ignores_shortcut_cwd(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "C:/Tools/Messenger/LANMessenger.exe"), patch.object(sys, "platform", "win32"):
            self.assertEqual(application_folder(), str(Path("C:/Tools/Messenger").resolve()))

    def test_mac_folder_is_outside_bundle(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "/Applications/LANMessenger.app/Contents/MacOS/LANMessenger"), patch.object(sys, "platform", "darwin"):
            self.assertEqual(application_folder(), str(Path("/Applications").resolve()))

    def test_sessions_are_per_chat_and_interrupted_work_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            first = store.new_chat(root)
            second = store.new_chat(root)
            self.assertNotEqual(first["session"], second["session"])
            first["running"] = True
            first["messages"].append({"role": "user", "text": "do something"})
            store.save()
            restored = AIStore(root)
            chat = next(c for c in restored.chats if c["id"] == first["id"])
            self.assertFalse(chat["running"])
            self.assertTrue(chat["interrupted"])
            self.assertEqual(len(chat["messages"]), 1)

    def test_corrupt_history_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root, "state.json")
            target.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                AIStore(root)
            self.assertEqual(target.read_text(), "broken")

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_saved_key_is_encrypted_and_provider_bound(self):
        with tempfile.TemporaryDirectory() as root:
            vault = KeyVault(Path(root))
            vault.save("openai", "test-private-value")
            self.assertNotIn(b"test-private-value", vault.path.read_bytes())
            self.assertEqual(vault.load(), {"provider": "openai", "key": "test-private-value"})
            vault.clear()
            self.assertEqual(vault.load(), {})

    def test_custom_endpoint_uses_child_environment_without_saving_key(self):
        with tempfile.TemporaryDirectory() as root:
            env = prepare_environment(Path(root), {"provider": "lanmessenger-api", "model": "local", "endpoint": "http://127.0.0.1:8080/v1"}, "private-value")
            self.assertEqual(env["LANMESSENGER_AI_KEY"], "private-value")
            config = Path(root, "pi", "models.json").read_text()
            self.assertNotIn("private-value", config)
            self.assertIn("LANMESSENGER_AI_KEY", config)

    def test_saved_account_cannot_silently_override_typed_api_key(self):
        with tempfile.TemporaryDirectory() as root:
            agent = Path(root, "pi")
            agent.mkdir()
            (agent / "auth.json").write_text(json.dumps({"anthropic": {"type": "oauth", "access": "old-account"}}))
            with self.assertRaises(ValueError):
                prepare_environment(Path(root), {"provider": "anthropic"}, "new-api-key")


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name, "fake.py")
        self.script.write_text(FAKE, encoding="utf-8")
        self.rpc = PiProcess()
        self.events, self.errors = [], []
        self.rpc.received.connect(self.events.append)
        self.rpc.error.connect(self.errors.append)
        self.rpc.start(sys.executable, [str(self.script)], self.temp.name, {})
        wait_for(lambda: self.rpc.ready)

    def tearDown(self):
        self.rpc.shutdown()
        self.temp.cleanup()

    def test_stream_and_finished_state(self):
        self.rpc.send_prompt("hello")
        wait_for(lambda: any(e["type"] == "agent_end" for e in self.events))
        self.assertFalse(self.rpc.busy)
        self.assertEqual(self.errors, [])
        self.assertTrue(any(e.get("assistantMessageEvent", {}).get("delta") == "Привет\u2028мир" for e in self.events))

    def test_abort_waits_for_agent_end_and_releases_next_prompt(self):
        self.rpc.send_prompt("wait")
        wait_for(lambda: any(e["type"] == "agent_start" for e in self.events))
        self.assertTrue(self.rpc.busy)
        with self.assertRaises(RuntimeError):
            self.rpc.send_prompt("duplicate")
        self.rpc.abort()
        wait_for(lambda: not self.rpc.busy)
        self.assertTrue(any(e["type"] == "agent_end" for e in self.events))
        self.rpc.send_prompt("hello")
        wait_for(lambda: not self.rpc.busy)

    def test_rejection_and_crash_do_not_leave_ui_busy(self):
        self.rpc.send_prompt("reject")
        wait_for(lambda: bool(self.errors))
        self.assertFalse(self.rpc.busy)
        self.errors.clear()
        self.rpc.send_prompt("crash")
        wait_for(lambda: bool(self.errors))
        self.assertFalse(self.rpc.busy)
        self.assertFalse(self.rpc.ready)


if __name__ == "__main__":
    unittest.main()
