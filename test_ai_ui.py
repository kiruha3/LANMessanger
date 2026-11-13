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
    def test_reasoning_level_saved_passed_to_pi_and_effective_level_shown(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(tab.thinking.currentData(), "auto")
                tab.thinking.setCurrentIndex(tab.thinking.findData("xhigh"))
                tab.model.setEditText("fake-model")
                tab.input.setPlainText("hello")
                with patch("app.ui.ai_tab.runtime_command", return_value=("fake-node", ["cli.js"], Path("fake.js"))), patch.object(tab.rpc, "start") as start:
                    tab.send_btn.click()
                    args = start.call_args.args[1]
                    self.assertEqual(args[args.index("--thinking") + 1], "xhigh")
                tab._event({"type": "response", "command": "get_state", "success": True,
                            "data": {"thinkingLevel": "high", "model": {"reasoning": True}}})
                self.assertIn("Высокий", tab.thinking_status.text())
                self.assertIn("Очень высокий", tab.thinking_status.text())
            finally:
                tab.shutdown()
                tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.thinking.currentData(), "xhigh")
            finally:
                restored.shutdown()
                restored.close()

    def test_welcome_is_ui_only_and_returns_for_new_chat(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertFalse(tab.output.welcome.isHidden())
                self.assertEqual(tab.current["messages"], [])
                self.assertEqual(tab.output.toPlainText(), "")
                tab.output.welcome.connect_btn.click()
                self.assertTrue(tab.connection_btn.isChecked())
                tab.current["messages"].append({"role": "user", "text": "hello"})
                tab._render()
                self.assertTrue(tab.output.welcome.isHidden())
                self.assertEqual(tab.output.layout.indexOf(next(iter(tab.output.rows.values()))), 1)
                tab._new_chat()
                self.assertFalse(tab.output.welcome.isHidden())
                self.assertEqual(tab.current["messages"], [])
            finally:
                tab.shutdown()
                tab.close()

    def test_reference_catalog_sections_and_zero_prices(self):
        from app.ui.ai_welcome import load_catalog
        catalog = load_catalog()
        models = [m for section in catalog["sections"] for m in section["models"]]
        self.assertEqual(len(models), 28)
        self.assertEqual([s["title"] for s in catalog["sections"]],
                         ["OpenAI", "Anthropic", "Google", "Google — изображения", "xAI"])
        terra = next(m for m in models if m["name"] == "GPT-6.1 Terra")
        self.assertEqual([terra[k] for k in ("input", "cache", "output")], [80, 4, 400])
        self.assertEqual(sum(m["cache"] == 0 for m in models), 3)
        self.assertTrue(all(m["use"] for m in models))

    def test_custom_discovery_groups_exact_ids_and_invalidates_on_credentials_change(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText("https://example.org/v1")
                tab.key.setText("synthetic-key")
                tab.model.setEditText("manual-id")
                with patch.object(tab.api_catalog, "fetch") as fetch:
                    tab.catalog_btn.click()
                    fetch.assert_called_once_with("https://example.org/v1", "synthetic-key")
                tab.api_catalog.loaded.emit([{"id": "exact/id", "owner": "owner"}])
                self.assertEqual(tab.model.currentText(), "manual-id")
                self.assertGreaterEqual(tab.model.findText("exact/id"), 0)
                self.assertFalse(tab.model.model().item(0).isEnabled())
                tab.model.setCurrentIndex(tab.model.findText("exact/id"))
                self.assertGreaterEqual(tab.model.findText("exact/id"), 0)
                tab.key.setText("new-key")
                self.assertEqual(tab.model.count(), 0)
                self.assertEqual(tab.model.currentText(), "exact/id")
                tab.api_catalog.failed.emit("Unavailable; enter manually")
                self.assertTrue(tab.model.isEditable())
                self.assertIn("manually", tab.auth_info.text())
            finally:
                tab.shutdown()
                tab.close()

    def test_trailing_whitespace_does_not_expand_streamed_message(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.current["messages"].extend([
                    {"id": "answer", "role": "assistant", "text": "Done" + "\n \t" * 500},
                    {"id": "blank", "role": "thinking", "text": "\n \t" * 500, "status": "running"}])
                tab._render()
                self.assertNotIn("blank", tab.output.rows)
                self.assertEqual(tab.output.rows["answer"].body.toPlainText(), "Done")
                self.assertLess(tab.output.rows["answer"].body.height(), 100)
                self.assertTrue(tab.current["messages"][0]["text"].endswith("\t"))
            finally:
                tab.shutdown()
                tab.close()

    def test_expanded_tool_output_uses_only_transcript_scrollbar(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(800, 600)
            tab.show()
            try:
                tab.current["messages"].append({"id": "tool", "role": "tool", "text": "bash\n{}",
                                                "result": "command output\n" * 120})
                tab._render()
                tab.output.rows["tool"].header.click()
                for _ in range(5):
                    APP.processEvents()
                body = tab.output.rows["tool"].body
                self.assertGreater(body.height(), 320)
                self.assertEqual(body.verticalScrollBar().maximum(), 0)
                self.assertGreater(tab.output.verticalScrollBar().maximum(), 0)
                from PyQt6.QtCore import QPoint, QPointF, Qt
                from PyQt6.QtGui import QWheelEvent
                scroll = tab.output.verticalScrollBar()
                scroll.setValue(0)
                position = QPointF(20, 20)
                event = QWheelEvent(position, QPointF(body.viewport().mapToGlobal(QPoint(20, 20))),
                                    QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                QApplication.sendEvent(body.viewport(), event)
                APP.processEvents()
                self.assertGreater(scroll.value(), 0)
            finally:
                tab.shutdown()
                tab.close()

    def test_provider_failure_does_not_report_unexecuted_tool_as_completed(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab._event({"type": "message_start", "message": {"role": "assistant"}})
                tab._event({"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                    "errorMessage": "provider disconnected", "content": [
                        {"type": "toolCall", "id": "not-run", "name": "write", "arguments": {"path": "example.txt"}}]}})
                tab._event({"type": "agent_end"})
                tool = next(m for m in tab.current["messages"] if m["role"] == "tool")
                self.assertEqual(tool["status"], "interrupted")
            finally:
                tab.shutdown()
                tab.close()

    def test_native_rows_stream_markdown_thinking_and_tool_details(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab._event({"type": "message_start", "message": {"role": "assistant"}})
                tab._render()
                self.assertEqual(len(tab.output.rows), 0)
                tab._event({"type": "message_update", "assistantMessageEvent": {
                    "type": "thinking_delta", "contentIndex": 0, "delta": "checking"}})
                tab._event({"type": "message_update", "assistantMessageEvent": {
                    "type": "text_delta", "contentIndex": 1, "delta": "**answer**\n\n```html\n<script>inert</script>\n```"}})
                tab._event({"type": "tool_execution_start", "toolCallId": "call", "toolName": "write",
                            "args": {"path": "sun.html", "content": "very long payload"}})
                tab._render()
                rows = list(tab.output.rows.values())
                self.assertEqual(len(rows), 3)
                self.assertIn("checking", rows[0].body.toPlainText())
                self.assertTrue(rows[0].header.isChecked())
                self.assertIn("font-weight", rows[1].body.toHtml())
                self.assertNotIn("<script>", rows[1].body.toHtml())
                self.assertIn("sun.html", rows[2].header.text())
                self.assertNotIn("very long payload", rows[2].header.text())
                self.assertFalse(rows[2].header.isChecked())
                rows[2].header.click()
                self.assertTrue(rows[2].header.isChecked())
                self.assertIn("very long payload", rows[2].body.toPlainText())
                tab._event({"type": "tool_execution_end", "toolCallId": "call", "isError": True,
                            "result": {"content": [{"type": "text", "text": "permission denied"}]}})
                tab._render()
                self.assertIs(list(tab.output.rows.values())[0], rows[0])
                self.assertIs(list(tab.output.rows.values())[2], rows[2])
                self.assertTrue(rows[2].header.isChecked())
                self.assertIn("Ошибка", rows[2].header.text())
                self.assertIn("permission denied", rows[2].body.toPlainText())
            finally:
                tab.shutdown()
                tab.close()

    def test_new_content_scrolls_down_but_expanding_history_does_not(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(800, 600)
            tab.show()
            try:
                tab.current["messages"].extend([
                    {"id": "user", "role": "user", "text": "Read history"},
                    {"id": "tool", "role": "tool", "text": "read\n{}", "result": "history\n" * 60},
                    {"id": "text", "role": "assistant", "text": "line\n\n" * 80}])
                tab._render()
                wait_for(lambda: tab.output.verticalScrollBar().maximum() > 0)
                scroll = tab.output.verticalScrollBar()
                scroll.setValue(0)
                tab.output.rows["tool"].header.click()
                APP.processEvents()
                self.assertEqual(scroll.value(), 0)
                tab._event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "new answer"}})
                tab._render()
                wait_for(lambda: scroll.value() == scroll.maximum())
            finally:
                tab.shutdown()
                tab.close()

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
