"""AI tab smoke test with a real local fake RPC child, no external model calls."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from app.ui.ai_tab import AITab

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("UI did not reach expected state")


class AITabTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_connection_and_key_survive_restart_without_sending(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
            tab.endpoint.setText("https://example.invalid/v1")
            tab.model.setEditText("test-model")
            tab.key.setText("synthetic-test-key")
            tab.remember.setChecked(True)
            tab.shutdown()
            tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.provider.currentData(), "lanmessenger-api")
                self.assertEqual(restored.endpoint.text(), "https://example.invalid/v1")
                self.assertEqual(restored.model.currentText(), "test-model")
                self.assertEqual(restored.key.text(), "synthetic-test-key")
                self.assertTrue(restored.remember.isChecked())
            finally:
                restored.shutdown()
                restored.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_remember_toggle_saves_and_removes_key_without_sending(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("openai"))
                tab.key.setText("synthetic-test-key")
                tab.remember.setChecked(True)
                wait_for(lambda: tab.vault.path.exists())
                self.assertEqual(tab.vault.load().get("key"), "synthetic-test-key")
                tab.remember.setChecked(False)
                wait_for(lambda: not tab.vault.path.exists())
                self.assertEqual(tab.key.text(), "synthetic-test-key")
            finally:
                tab.shutdown()
                tab.close()

    def test_only_supported_account_login_is_offered(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.provider.setCurrentIndex(tab.provider.findData("google"))
            self.assertFalse(tab.login_btn.isEnabled())
            tab.provider.setCurrentIndex(tab.provider.findData("openai-codex"))
            self.assertTrue(tab.login_btn.isEnabled())
            tab.shutdown()

    def test_cancelled_task_is_labelled_stopped(self):
        from test_ai import FAKE
        with tempfile.TemporaryDirectory() as root:
            script = Path(root, "fake.py")
            script.write_text(FAKE, encoding="utf-8")
            tab = AITab(data_dir=str(Path(root, "data")), work_dir=root)
            tab.rpc.start(sys.executable, [str(script)], root, {})
            wait_for(lambda: tab.rpc.ready)
            tab.input.setPlainText("wait")
            tab.send_btn.click()
            wait_for(lambda: "Привет" in tab.output.toPlainText())
            tab.stop_btn.click()
            wait_for(lambda: not tab.rpc.busy)
            status = tab.status.text().lower()
            tab.shutdown()
            self.assertIn("останов", status)

    def test_oauth_in_progress_blocks_task_submission(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab._logging_in = True
            tab._controls(False)
            self.assertFalse(tab.send_btn.isEnabled())
            self.assertFalse(tab.new_btn.isEnabled())
            self.assertFalse(tab.chat_list.isEnabled())
            tab.shutdown()

    def test_resume_reconciles_from_pi_before_sending_next_prompt(self):
        from test_ai import FAKE
        from app.core.ai import AIStore
        with tempfile.TemporaryDirectory() as root:
            script = Path(root, "fake.py")
            script.write_text(FAKE.replace('"messages":[]', '"messages":[{"role":"assistant","content":[{"type":"text","text":"complete restored response"}]}]'), encoding="utf-8")
            data = str(Path(root, "data"))
            store = AIStore(data)
            chat = store.new_chat(root)
            chat["messages"].append({"role": "assistant", "text": "stale partial"})
            store.save()
            tab = AITab(data_dir=data, work_dir=root)
            tab.model.setEditText("fake")
            tab.input.setPlainText("hello")
            try:
                with patch("app.ui.ai_tab.runtime_command", return_value=(sys.executable, [str(script)], script)):
                    tab.send_btn.click()
                wait_for(lambda: not tab.rpc.busy and not tab._pending)
                APP.processEvents()
                tab._render()
                self.assertIn("complete restored response", tab.output.toPlainText())
                self.assertNotIn("stale partial", tab.output.toPlainText())
            finally:
                tab.shutdown()

    def test_stream_chat_and_restore_without_replaying(self):
        from test_ai import FAKE
        with tempfile.TemporaryDirectory() as root:
            script = Path(root, "fake.py")
            script.write_text(FAKE, encoding="utf-8")
            tab = AITab(data_dir=str(Path(root, "data")), work_dir=root)
            tab.show()
            self.assertIn(root, tab.folder_label.text())
            self.assertFalse(tab.stop_btn.isEnabled())
            tab.rpc.start(sys.executable, [str(script)], root, {})
            wait_for(lambda: tab.rpc.ready)
            tab.input.setPlainText("hello <script>alert(1)</script>")
            tab.send_btn.click()
            wait_for(lambda: "Привет" in tab.output.toPlainText())
            wait_for(lambda: not tab.rpc.busy)
            self.assertTrue(tab.send_btn.isEnabled())
            self.assertFalse(tab.stop_btn.isEnabled())
            self.assertIn("<script>alert(1)</script>", tab.output.toPlainText())
            self.assertNotIn("<script>", tab.output.toHtml())
            tab.shutdown()
            tab.close()
            restored = AITab(data_dir=str(Path(root, "data")), work_dir=root)
            self.assertIn("Привет", restored.output.toPlainText())
            self.assertFalse(restored.rpc.ready)
            self.assertFalse(restored.rpc.busy)
            restored.shutdown()
            restored.close()

    def test_old_folder_history_does_not_enable_execution(self):
        from app.core.ai import AIStore
        with tempfile.TemporaryDirectory() as root:
            data = str(Path(root, "data"))
            store = AIStore(data)
            store.new_chat(str(Path(root, "other")))
            tab = AITab(data_dir=data, work_dir=root)
            self.assertFalse(tab.send_btn.isEnabled())
            self.assertIn("другой папк", tab.status.text())
            tab.shutdown()
            tab.close()


if __name__ == "__main__":
    unittest.main()
