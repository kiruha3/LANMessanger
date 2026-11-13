"""Actual installed Pi against a deterministic local OpenAI API; performs a file tool."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication
from app.core.ai import PiProcess, prepare_environment, runtime_command

APP = QCoreApplication.instance() or QCoreApplication([])


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Pi integration timed out")


class LocalAPI(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if not any(m.get("role") == "tool" for m in request["messages"]):
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_write", "type": "function",
                     "function": {"name": "write", "arguments": json.dumps({"path": "agent-proof.txt", "content": "created by real Pi"})}}]}
            reason = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Файл создан локальным агентом."}
            reason = "stop"
        for value in ({"id": "local-test", "object": "chat.completion.chunk", "model": "test-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "local-test", "object": "chat.completion.chunk", "model": "test-model", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}):
            self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class RealPiTests(unittest.TestCase):
    def test_real_pi_writes_file_and_restores_session_without_replay(self):
        try:
            program, cli, _module = runtime_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            rpc = PiProcess()
            events, errors = [], []
            rpc.received.connect(events.append)
            rpc.error.connect(errors.append)
            config = {"provider": "lanmessenger-api", "model": "test-model", "endpoint": f"http://127.0.0.1:{server.server_port}/v1", "thinking": "xhigh"}
            env = prepare_environment(Path(root), config, "local-test-key")
            session = str(Path(root, "session.jsonl"))
            args = cli + ["--mode", "rpc", "--provider", "lanmessenger-api", "--model", "test-model", "--thinking", "xhigh", "--session", session, "--no-extensions", "--no-skills"]
            try:
                rpc.start(program, args, root, env)
                wait_for(lambda: rpc.ready or errors)
                self.assertEqual(errors, [])
                rpc.send_prompt("Создай agent-proof.txt")
                wait_for(lambda: any(e.get("type") == "agent_end" for e in events) or errors)
                self.assertEqual(errors, [])
                self.assertEqual(Path(root, "agent-proof.txt").read_text(), "created by real Pi")
                self.assertTrue(any(e.get("type") == "tool_execution_end" for e in events))
                self.assertEqual(len(server.requests), 2)
                self.assertEqual([r.get("reasoning_effort") for r in server.requests], ["xhigh", "xhigh"])
                for level, expected in (("high", "high"), ("off", "none")):
                    events.clear()
                    rpc.command({"type": "set_thinking_level", "level": level})
                    wait_for(lambda: any(e.get("command") == "set_thinking_level" for e in events) or errors)
                    self.assertEqual(errors, [])
                    rpc.send_prompt("Проверка уровня размышлений")
                    wait_for(lambda: any(e.get("type") == "agent_end" for e in events) or errors)
                    self.assertEqual(errors, [])
                    self.assertEqual(server.requests[-1].get("reasoning_effort"), expected)
                rpc.shutdown()
                events.clear()
                rpc.start(program, args, root, env)
                wait_for(lambda: rpc.ready or errors)
                self.assertEqual(errors, [])
                rpc.command({"type": "get_messages"})
                wait_for(lambda: any(e.get("command") == "get_messages" for e in events))
                messages = next(e["data"]["messages"] for e in events if e.get("command") == "get_messages")
                self.assertEqual(sum(m.get("role") == "user" for m in messages), 3)
                self.assertEqual(len(server.requests), 4)
                self.assertTrue(Path(session).exists())
            finally:
                rpc.shutdown()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
