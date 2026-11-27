"""AI tab smoke test with a real local fake RPC child, no external model calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
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
    def test_plain_chat_sends_highlighted_context_over_http_and_reopens_without_pi(self):
        requests = []
        release_stream = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), payload))
                second_request = len(requests) == 2
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                answer = "Частичный ответ" if second_request else "Первый ответ"
                event = {"choices": [{"delta": {"content": answer}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode())
                self.wfile.flush()
                if not second_request:
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    release_stream.wait(10)

        with (tempfile.TemporaryDirectory() as root,
              ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server,
              patch("app.ui.ai_tab.rpc_command", side_effect=AssertionError("Plain chat must never start Pi"))):
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            tab = AITab(data_dir=root, work_dir=root)
            tab.show()
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.key.setText("fixture-key")
                tab.model.setEditText("fixture-model")
                tab.new_btn.click()
                self.assertEqual(tab.current.get("mode"), "text")
                self.assertTrue(tab.chat_install_btn.isHidden())
                self.assertTrue(tab.tools_btn.isHidden())
                self.assertFalse(tab.context_answers.isHidden())
                tab.context_answers.setValue(1)
                tab.input.setPlainText("Первый вопрос")
                tab.send_btn.click()
                wait_for(lambda: tab.current.get("outcome") == "completed")
                question, answer = tab.current["messages"]
                self.assertFalse(tab.output.rows[question["id"]].property("inContext"))
                self.assertTrue(tab.output.rows[answer["id"]].property("inContext"))
                self.assertIn("Начало контекста", tab.output.rows[answer["id"]].header.text())
                tab.context_answers.setValue(0)
                self.assertTrue(tab.output.rows[question["id"]].property("inContext"))
                tab.context_answers.setValue(1)
                tab.input.setPlainText("Новый вопрос")
                tab.send_btn.click()
                wait_for(lambda: tab.current["messages"][-1].get("text") == "Частичный ответ")
                self.assertTrue(tab.stop_btn.isVisible())
                tab.stop_btn.click()
                self.assertEqual(tab.current.get("outcome"), "stopped")
                self.assertEqual(tab.current["messages"][-1]["status"], "stopped")
                self.assertEqual(requests[1], ("/v1/chat/completions", "Bearer fixture-key", {
                    "model": "fixture-model", "stream": True, "messages": [
                        {"role": "assistant", "content": "Первый ответ"},
                        {"role": "user", "content": "Новый вопрос"},
                    ],
                }))
                chat_id = tab.current["id"]
                tab.agent_btn.click()
                self.assertEqual(tab.current.get("mode"), "agent")
                self.assertTrue(tab.context_answers.isHidden())
                tab._select_chat(next(i for i, chat in enumerate(tab.store.chats) if chat["id"] == chat_id))
            finally:
                release_stream.set()
                tab.shutdown(); tab.close()
                server.shutdown()
                worker.join()
            reopened = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(reopened.current["id"], chat_id)
                self.assertEqual(reopened.context_answers.value(), 1)
                self.assertEqual(reopened.current["messages"][-1]["text"], "Частичный ответ")
            finally:
                reopened.shutdown(); reopened.close()

    def test_role_inspector_shows_role_model_and_capabilities(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.current["role_snapshot"] = {"name": "Координатор", "capabilities": ["delegate"]}
                tab.current["capabilities"] = ["delegate"]
                tab.current["model"] = "role-model"
                tab._render()
                self.assertIn("Координатор", tab.role_inspector.text())
                self.assertIn("role-model", tab.role_inspector.text())
                self.assertIn("delegate", tab.role_inspector.text())
            finally:
                tab.shutdown(); tab.close()

    def test_context_usage_is_scoped_to_selected_run_and_resets_on_switch(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                first = tab.current
                second = tab.store.new_chat(root)
                tab._fill_chats()
                tab._context_usage_changed({"tokens": 1200, "contextWindow": 8000, "percent": 15})
                self.assertIn("1 200", tab.context_label.text())
                first_run = tab._selected_run
                tab._select_chat(next(i for i, chat in enumerate(tab.store.chats) if chat["id"] == second["id"]))
                self.assertEqual(tab.context_label.text(), "Контекст: ?")
                second_run = tab._selected_run
                tab._event_run = first_run
                tab._context_usage_changed({"tokens": 7000, "contextWindow": 8000, "percent": 87.5})
                tab._event_run = None
                self.assertEqual(tab.context_label.text(), "Контекст: ?")
                tab._event_run = second_run
                tab._context_usage_changed({})
                tab._event_run = None
                self.assertEqual(tab.context_label.text(), "Контекст: ?")
                tab._event_run = second_run
                tab._context_usage_changed({"tokens": 400, "contextWindow": 8000, "percent": 5})
                tab._event_run = None
                self.assertIn("400", tab.context_label.text())
                tab._select_chat(next(i for i, chat in enumerate(tab.store.chats) if chat["id"] == first["id"]))
                self.assertIn("7 000", tab.context_label.text())
            finally:
                tab._event_run = None
                tab.shutdown(); tab.close()

    def test_rename_menu_updates_tree_header_link_and_reopens(self):
        from PyQt6.QtWidgets import QMenu

        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(900, 550)
            tab.show()
            try:
                source = tab.current
                child = tab.store.new_chat(root)
                child.update(delegated=True, parent_chat_id=source["id"])
                tab.store.agent_links["rename-test"] = dict(id="rename-test", source=source["id"],
                    target=child["id"], question="Вопрос", status="waiting")
                tab._fill_chats()
                tab._render()
                tree = tab.chat_list
                row = tree.items[child["id"]]
                tree.scrollToItem(row)
                APP.processEvents()
                with patch("app.ui.ai_tab.QInputDialog.getText", side_effect=[("   ", True), ("  Редактор  ", True)]), \
                     patch("app.ui.ai_tab.QMessageBox.warning") as warning:
                    tree.customContextMenuRequested.emit(tree.visualItemRect(row).center())
                    menu = next(menu for menu in tree.findChildren(QMenu) if menu.isVisible())
                    next(action for action in menu.actions() if action.text() == "Переименовать чат…").trigger()
                    menu.close()
                    wait_for(lambda: child["title"] == "Редактор")
                    warning.assert_called_once()
                self.assertIs(tab.current, source)
                self.assertIs(tree.items[child["id"]], row)
                self.assertIn("Редактор", row.text(0))
                self.assertIn("Редактор", tab.agent_link_status.text())
                self.assertIn("Редактор", tab.output.rows["rename-test:out"].header.text())
                tab._open_ai_chat(child["id"])
                self.assertEqual(tab.chat_title.text(), "Редактор")
                with patch("app.ui.ai_tab.QInputDialog.getText", return_value=("Не применять", False)):
                    tab._prompt_rename_chat(child["id"])
                self.assertEqual(child["title"], "Редактор")
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                restored._open_ai_chat(child["id"])
                self.assertEqual(restored.chat_title.text(), "Редактор")
            finally:
                restored.shutdown(); restored.close()

    def test_rename_rpc_uses_bound_caller_not_selected_chat_or_payload(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                caller = tab._run()
                child, grandchild, peer, image = [tab.store.new_chat(root) for _ in range(4)]
                child.update(delegated=True, parent_chat_id=caller.chat["id"])
                grandchild.update(delegated=True, parent_chat_id=child["id"])
                image.update(parent_chat_id=caller.chat["id"])
                tab.store.agent_links["peer"] = dict(id="peer", source=caller.chat["id"],
                    target=peer["id"], question="Вопрос", status="completed")
                tab._fill_chats()
                tab._open_ai_chat(child["id"])

                def rename(run=caller, **params):
                    with patch.object(run.rpc, "command") as reply:
                        run.rpc.received.emit({"type": "extension_ui_request", "method": "input",
                            "title": "lanmessenger.rename_chat", "id": "test", "placeholder": json.dumps(params)})
                    reply.assert_called_once()
                    return json.loads(reply.call_args.args[0]["value"])

                self.assertEqual(rename(title="Новый чат"), {"id": caller.chat["id"], "title": "Новый чат"})
                self.assertTrue(caller.chat["title_custom"])
                self.assertEqual(rename(chat_id=child["id"], title=" Исполнитель ")["title"], "Исполнитель")
                self.assertIs(tab.current, child)
                self.assertEqual(tab.chat_title.text(), "Исполнитель")
                for forbidden in (grandchild["id"], peer["id"], image["id"], "missing"):
                    self.assertIn("error", rename(chat_id=forbidden, title="Чужой"))
                self.assertIn("error", rename(tab._selected_run, chat_id=caller.chat["id"], title="Родитель"))
                self.assertIn("error", rename(chat_id=grandchild["id"], caller_id=child["id"], title="Подмена"))
                for invalid in (" ", "x" * 81, "a\nb", "a\u2028b", "a\x00b", 123, None):
                    self.assertIn("error", rename(chat_id=child["id"], title=invalid))
                self.assertEqual(child["title"], "Исполнитель")
                self.assertEqual(peer["title"], "Новый чат")
                caller.chat["disabled_plugins"] = ["chats"]
                self.assertIn("error", rename(title="Запрещено"))
            finally:
                tab.shutdown(); tab.close()

    def test_rename_through_real_pi_rpc(self):
        import threading
        from http.server import ThreadingHTTPServer
        from test_ai_pi import LocalAPI
        from app.core.ai import rpc_command

        try:
            rpc_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_name = "rename_chat"
            server.tool_arguments = {"title": "Координатор"}
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.key.setText("local-rename-test-key")
                tab.model.setEditText("test-model")
                tab.input.setPlainText("Переименуй этот чат")
                tab.send_btn.click()
                wait_for(lambda: tab.current.get("outcome") == "completed", 30)
                self.assertIn("rename_chat", [tool["function"]["name"] for tool in server.requests[0]["tools"]])
                self.assertEqual(tab.current["title"], "Координатор")
                self.assertEqual(tab.chat_title.text(), "Координатор")
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_agent_card_waits_until_click_finishes_to_open_collapsed_branch(self):
        from PyQt6.QtWidgets import QPushButton

        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(862, 552)
            tab.show()
            try:
                source = tab.current
                target = tab.store.new_chat(root)
                target.update(delegated=True, parent_chat_id=source["id"])
                tab.store.agent_links["route"] = {
                    "id": "route", "source": source["id"], "target": target["id"],
                    "question": "Проверка", "status": "completed",
                }
                tab._fill_chats()
                tab._render()
                branch = tab.chat_list.items[source["id"]]
                branch.setExpanded(False)
                button = next(button for button in tab.output.rows["route:out"].findChildren(QPushButton)
                              if button.text() == "Открыть ответный чат")
                tab.output.ensureWidgetVisible(button)
                APP.processEvents()
                during_click = []
                button.clicked.connect(lambda: during_click.append(tab.current["id"]))
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertEqual(during_click, [source["id"]])
                wait_for(lambda: tab.current is target)
                self.assertTrue(branch.isExpanded())
                self.assertIs(tab.chat_list.currentItem(), tab.chat_list.items[target["id"]])
                self.assertIn("route:in", tab.output.rows)
            finally:
                tab.shutdown()
                tab.close()

    def test_selecting_linked_chat_keeps_tree_columns_and_row_positions_stable(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(862, 478)
            tab.show()
            try:
                unrelated = tab.current
                source = tab.store.new_chat(root)
                target = tab.store.new_chat(root)
                target.update(delegated=True, parent_chat_id=source["id"])
                tab.store.agent_links["route"] = {
                    "id": "route", "source": source["id"], "target": target["id"],
                    "question": "Проверка", "status": "completed",
                }
                tab._fill_chats()
                APP.processEvents()
                tree = tab.chat_list
                items = [tree.topLevelItem(0), tree.items[source["id"]], tree.items[target["id"]]]

                def positions():
                    return [tree.visualRect(tree.indexFromItem(item, 0)).left() for item in items]

                before = positions()
                gutter = tree.columnViewportPosition(0)
                for chat, linked in ((target, True), (unrelated, False), (source, True)):
                    index = tree.indexFromItem(tree.items[chat["id"]], 0)
                    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualRect(index).center())
                    APP.processEvents()
                    self.assertIs(tab.current, chat)
                    self.assertEqual(bool(tree.routes), linked)
                    self.assertEqual(positions(), before)
                    self.assertEqual(tree.columnViewportPosition(0), gutter)
                self.assertLessEqual(gutter, 12)
            finally:
                tab.shutdown()
                tab.close()

    def test_agent_link_menu_focuses_overflow_without_moving_existing_chats(self):
        from PyQt6.QtWidgets import QMenu

        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(1100, 640)
            tab.show()
            try:
                current = tab.current
                others = [tab.store.new_chat(root) for _ in range(49)]
                for index, chat in enumerate(others):
                    chat["title"] = f"Исполнитель {index}"
                for index, chat in enumerate(others[:4]):
                    link_id = f"request-{index}"
                    tab.store.agent_links[link_id] = {"id": link_id, "source": current["id"], "target": chat["id"],
                                                     "question": "Проверка", "status": "completed"}
                tab.store.agent_links["reverse"] = {"id": "reverse", "source": others[0]["id"], "target": current["id"],
                                                    "question": "Встречный запрос", "status": "completed"}
                tab._fill_chats()
                tab._render()
                self.assertEqual(len(tab.chat_list.items), 50)
                self.assertEqual(tab.agent_links_btn.text(), "Связи: 5 · +2")
                self.assertEqual(len(tab.chat_list.routes), 3)
                QTest.mouseClick(tab.agent_links_btn, Qt.MouseButton.LeftButton)
                menu = next(menu for menu in tab.findChildren(QMenu) if menu.isVisible())
                action = next(action for action in menu.actions() if action.text() == "К: Исполнитель 0")
                action.trigger()
                menu.close()
                wait_for(lambda: tab._focused_agent_link == "request-0")
                self.assertEqual(tab._focused_agent_link, "request-0")
                self.assertEqual(tab.chat_list.routes[0]["target"], others[0]["id"])
                self.assertTrue(any(route["source"] == others[0]["id"] for route in tab.chat_list.routes))
                self.assertIs(tab.current, current)
            finally:
                tab.shutdown()
                tab.close()

    def test_async_delegation_returns_ids_and_late_follow_up_is_correlated(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                parent = tab._run()
                tab.model.setEditText("parent-model")
                tab._save_connection()
                with patch.object(tab, "_start_text_run", side_effect=tab._begin_task):
                    with patch.object(parent.rpc, "command") as command:
                        tab._dispatch_run(parent, tab._agent_request, {"title": "lanmessenger.ask_agent", "id": "tool-async",
                            "placeholder": json.dumps({"question": "Сделай позже", "wait_for_result": False})})
                        accepted = json.loads(command.call_args.args[0]["value"])
                self.assertEqual(accepted["status"], "accepted")
                self.assertRegex(accepted["request_id"], r"^[0-9a-f-]{36}$")
                self.assertRegex(accepted["chat_id"], r"^[0-9a-f-]{36}$")
                request = parent.agent_requests["tool-async"].agent_parent_request
                self.assertEqual(request["request_id"], accepted["request_id"])
                self.assertFalse(request["wait_for_result"])
                child = parent.agent_requests["tool-async"]
                self.assertIsNotNone(child.agent_parent_request)
                with patch.object(child.rpc, "command"), patch.object(parent.rpc, "command") as late:
                    tab._dispatch_run(child, tab._finish_text_run, "completed")
                    notice = late.call_args.args[0]
                    self.assertEqual(notice["type"], "follow_up")
                    self.assertIn(accepted["request_id"], notice["message"])
                    self.assertIn(accepted["chat_id"], notice["message"])
            finally:
                tab.shutdown(); tab.close()

    def test_late_async_completion_uses_child_context_after_switch_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                parent = tab._run()
                tab.model.setEditText("parent-model")
                tab._save_connection()
                other = tab.store.new_chat(root)
                tab._fill_chats()
                with patch.object(tab, "_start_text_run", side_effect=tab._begin_task), patch.object(parent.rpc, "command") as accepted:
                    tab._dispatch_run(parent, tab._agent_request, {"title": "lanmessenger.ask_agent", "id": "late",
                        "placeholder": json.dumps({"question": "child", "wait_for_result": False})})
                child = next(run for run in tab._runs.values() if run is not parent and run.chat.get("delegated"))
                child_chat_id = child.chat["id"]
                request_id = child.agent_parent_request["request_id"]
                tab._open_ai_chat(other["id"])
                with patch.object(parent.rpc, "command") as command:
                    tab._dispatch_run(child, tab._finish_text_run, "completed")
                    msg = command.call_args.args[0]
                    self.assertEqual(msg["chat_id"], child_chat_id)
                    self.assertEqual(msg["request_id"], request_id)
                    key = msg["completion_key"]
                    command.reset_mock()
                    tab._dispatch_run(child, tab._finish_agent_request, "completed")
                    command.assert_not_called()
                    self.assertEqual(key, msg["completion_key"])
            finally:
                tab.shutdown(); tab.close()

    def test_two_async_delegations_are_independent_and_correlated(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                parent = tab._run()
                tab.model.setEditText("parent-model")
                tab._save_connection()
                requests = []
                with patch.object(tab, "_start_text_run", side_effect=tab._begin_task), patch.object(parent.rpc, "command") as command:
                    for tool_id, question in (("tool-1", "Sol-1"), ("tool-2", "Sol-2")):
                        tab._dispatch_run(parent, tab._agent_request, {"title": "lanmessenger.ask_agent", "id": tool_id,
                            "placeholder": json.dumps({"question": question, "wait_for_result": False})})
                        accepted = json.loads(command.call_args.args[0]["value"])
                        requests.append((tool_id, accepted))
                self.assertEqual(len(parent.agent_requests), 2)
                self.assertEqual(len({item[1]["request_id"] for item in requests}), 2)
                self.assertEqual(len({item[1]["chat_id"] for item in requests}), 2)
                children = list(parent.agent_requests.values())
                self.assertTrue(all(child.agent_parent_request["wait_for_result"] is False for child in children))
                self.assertTrue(all(tab.store.agent_links[child.agent_parent_request["link_id"]]["state"] == "running" for child in children))
                with patch.object(parent.rpc, "command") as late:
                    for child, expected in reversed(list(zip(children, requests))):
                        with patch.object(child.rpc, "command"):
                            tab._dispatch_run(child, tab._finish_text_run, "completed")
                        msg = late.call_args.args[0]
                        self.assertEqual(msg["type"], "follow_up")
                        self.assertEqual(msg["request_id"], expected[1]["request_id"])
                        self.assertEqual(msg["chat_id"], expected[1]["chat_id"])
                self.assertEqual(parent.agent_requests, {})
            finally:
                tab.shutdown(); tab.close()

    def test_existing_delegation_rejects_unrelated_chat(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                parent = tab._run()
                other = tab.store.new_chat(root)
                with patch.object(parent.rpc, "command") as command:
                    tab._dispatch_run(parent, tab._agent_request, {"title": "lanmessenger.ask_agent", "id": "tool-peer",
                        "placeholder": json.dumps({"question": "Нет", "chat_id": other["id"], "wait_for_result": False})})
                    result = json.loads(command.call_args.args[0]["value"])
                self.assertIn("error", result)
                self.assertEqual(parent.agent_requests, {})
            finally:
                tab.shutdown(); tab.close()

    def test_agent_links_preserve_nested_chats_directions_and_waiting_after_selection(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(1100, 640)
            tab.show()
            try:
                tab.model.setEditText("test-model")
                tab._save_connection()
                parent = tab._run()
                existing = tab.store.new_chat(root)
                existing.update(title="Тесты", ai_selection=dict(parent.chat["ai_selection"]))

                def ask(run, request_id, **params):
                    tab._dispatch_run(run, tab._agent_request, {
                        "title": "lanmessenger.ask_agent", "id": request_id,
                        "placeholder": json.dumps({"question": "Проверь список", **params})})
                    return run.agent_requests[request_id]

                with patch.object(tab, "_start_text_run", side_effect=tab._begin_task):
                    child = ask(parent, "first")
                    grandchild = ask(child, "nested")
                    peer = ask(child, "existing", chat_id=existing["id"])
                child_id = child.chat["id"]
                grandchild_id = grandchild.chat["id"]
                tab._open_ai_chat(child_id)
                APP.processEvents()
                self.assertEqual(tab.chat_list.items[child_id].parent(), tab.chat_list.items[parent.chat["id"]])
                self.assertEqual(tab.chat_list.items[grandchild_id].parent(), tab.chat_list.items[child_id])
                self.assertIsNone(tab.chat_list.items[existing["id"]].parent().parent())
                self.assertIn("Ждёт: 2", tab.chat_list.items[child_id].text(0))
                self.assertIn("По запросу", tab.agent_link_status.text())
                self.assertEqual(len(tab.chat_list.routes), 3)
                self.assertTrue(all(route["waiting"] for route in tab.chat_list.routes))
                outbound = next(link for link in tab.store.agent_links.values() if link["target"] == grandchild_id)
                self.assertIn(outbound["id"] + ":out", tab.output.rows)
                tab._open_agent_message(grandchild_id, outbound["id"] + ":in")
                self.assertEqual(tab.current["id"], grandchild_id)
                self.assertIn("Вопрос от", tab.output.rows[outbound["id"] + ":in"].header.text())
                with patch.object(child.rpc, "command"):
                    tab._dispatch_run(grandchild, tab._finish_text_run, "completed")
                    tab._dispatch_run(peer, tab._finish_text_run, "failed")
                self.assertEqual(outbound["status"], "completed")
                tab._open_ai_chat(child_id)
                self.assertNotIn("Ждёт", tab.agent_link_status.text())
            finally:
                tab.shutdown()
                tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.current["id"], child_id)
                self.assertEqual(len(restored.store.agent_links), 3)
                self.assertEqual({link["status"] for link in restored.store.agent_links.values()},
                                 {"completed", "failed", "interrupted"})
                self.assertIn(outbound["id"] + ":out", restored.output.rows)
            finally:
                restored.shutdown()
                restored.close()

    def test_tool_switches_are_independent_and_survive_reopening(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                first_id = tab.current['id']
                self.assertTrue(tab.plugin_checks['images'].isChecked())
                tab.plugin_checks['images'].click()
                self.assertFalse(tab.plugin_checks['images'].isChecked())
                tab._new_chat()
                self.assertTrue(tab.plugin_checks['images'].isChecked())
                tab._open_ai_chat(first_id)
                self.assertFalse(tab.plugin_checks['images'].isChecked())
            finally:
                tab.shutdown()
                tab.close()
            reopened = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(reopened.current['id'], first_id)
                self.assertFalse(reopened.plugin_checks['images'].isChecked())
                self.assertTrue(reopened.plugin_checks['chats'].isChecked())
            finally:
                reopened.shutdown()
                reopened.close()

    def test_active_agent_accepts_a_new_message_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(1200, 640)
            tab.show()
            try:
                APP.processEvents()
                tab._begin_task('Первая задача')
                tab.rpc.ready = True
                tab.rpc._set_busy(True)
                APP.processEvents()
                self.assertTrue(tab.send_btn.isEnabled())
                tab.input.setPlainText('А вот ещё условие')
                with patch.object(tab.rpc, 'command') as command:
                    QTest.mouseClick(tab.send_btn, Qt.MouseButton.LeftButton)
                command.assert_not_called()
                self.assertEqual([row['text'] for row in tab.current['messages']], ['Первая задача'])
                request_id = next(iter(tab._run().followups))
                queued = tab.output.rows.get(request_id)
                self.assertIsNotNone(queued, 'Дополнение должно появиться сразу после клика')
                self.assertIn('В очереди', queued.header.text())
                with patch.object(tab.rpc, 'command') as command:
                    tab._event({'type': 'turn_end'})
                command.assert_not_called()
                tab.input.setPlainText('Новый черновик')
                tab.rpc._set_busy(False)
                with patch.object(tab.rpc, 'command') as command:
                    tab._event({'type': 'agent_end'})
                self.assertEqual(command.call_args.args[0]['message'], 'А вот ещё условие')
                self.assertEqual([row['text'] for row in tab.current['messages']], ['Первая задача', 'А вот ещё условие'])
                self.assertIs(tab.output.rows[request_id], queued)
                self.assertEqual(queued.header.text(), 'Вы')
                self.assertEqual(tab.input.toPlainText(), 'Новый черновик')
            finally:
                tab.shutdown()
                tab.close()

    def test_followup_pill_sits_above_the_composer_without_moving_send_arrow(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(1200, 640)
            tab.show()
            try:
                APP.processEvents()
                self.assertFalse(tab.followup_actions.isVisible())
                composer_geometry = tab.composer.geometry()
                send_position = tab.send_btn.mapTo(tab, tab.send_btn.rect().center())
                tab._begin_task('Первая задача')
                tab.rpc.ready = True
                tab.rpc._set_busy(True)
                tab._event({'type': 'agent_start'})
                APP.processEvents()
                self.assertTrue(tab.followup_actions.isVisible())
                self.assertIs(tab.followup_actions.parentWidget(), tab.composer.parentWidget())
                pill_geometry = tab.followup_actions.geometry()
                self.assertLess(pill_geometry.bottom(), composer_geometry.top())
                self.assertEqual(pill_geometry.right(), composer_geometry.right())
                self.assertLess(pill_geometry.width(), composer_geometry.width() / 2)
                self.assertEqual(tab.composer.geometry(), composer_geometry)
                self.assertTrue(tab.clarify_btn.isEnabled())
                self.assertTrue(tab.queue_btn.isEnabled())
                self.assertTrue(tab.stop_btn.isVisible())
                self.assertIs(tab.childAt(send_position), tab.send_btn)
                tab.rpc._set_busy(False)
                tab._event({'type': 'agent_end'})
                APP.processEvents()
                self.assertFalse(tab.followup_actions.isVisible())
                self.assertEqual(tab.composer.geometry(), composer_geometry)
            finally:
                tab.shutdown()
                tab.close()

    def test_rejected_queued_message_is_not_left_waiting(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab._begin_task('Первая задача')
                tab.rpc.ready = True
                tab.rpc._set_busy(True)
                tab._event({'type': 'agent_start'})
                tab.input.setPlainText('А вот ещё условие')
                tab._event({'type': 'tool_execution_start', 'toolCallId': 'read-fixture', 'toolName': 'read'})
                with patch.object(tab.rpc, 'command') as command:
                    tab.clarify_btn.click()
                self.assertEqual(command.call_args.args[0]['type'], 'steer')
                request_id = command.call_args.args[0]['id']
                self.assertIn(request_id, tab.output.rows)
                self.assertIn('После инструмента', tab.output.rows[request_id].header.text())
                tab._event({'type': 'response', 'command': 'steer', 'success': False, 'id': request_id})
                self.assertNotIn(request_id, tab.output.rows)
                self.assertEqual([row['text'] for row in tab.current['messages'] if row['role'] != 'tool'],
                                 ['Первая задача', 'Дополнение не доставлено: А вот ещё условие'])
            finally:
                tab.shutdown()
                tab.close()

    def test_manual_stop_never_automatically_sends_waiting_messages(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab._begin_task('Первая задача')
                tab.rpc.ready = True
                tab.rpc._set_busy(True)
                self.assertFalse(tab.clarify_btn.isEnabled())
                tab.input.setPlainText('Сообщение на потом')
                tab.queue_btn.click()
                with patch.object(tab.rpc, 'command') as command:
                    tab.stop_btn.click()
                    tab.rpc._set_busy(False)
                    tab._event({'type': 'agent_end'})
                command.assert_called_once_with({'type': 'abort'})
                self.assertEqual(tab.current['outcome'], 'stopped')
                self.assertFalse(tab._run().followups)
                self.assertEqual(tab.current['messages'][-1]['text'], 'Дополнение не доставлено: Сообщение на потом')
            finally:
                tab.shutdown()
                tab.close()

    def test_pinned_chat_stays_above_newer_chats_and_pin_menu_and_restart_preserve_selection(self):
        from PyQt6.QtWidgets import QMenu
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.show()
            try:
                old = tab.current
                old["last_activity"] = 10
                tab._new_chat()
                new = tab.current
                new["last_activity"] = 20
                tab._fill_chats()
                group = tab.chat_list.topLevelItem(0)
                item = next(group.child(index) for index in range(group.childCount())
                            if group.child(index).data(0, Qt.ItemDataRole.UserRole) == old["id"])
                tab._chat_context_menu(tab.chat_list.visualItemRect(item).center())
                menu = next(menu for menu in tab.chat_list.findChildren(QMenu) if menu.actions())
                self.assertEqual(menu.actions()[0].text(), "Закрепить чат")
                menu.actions()[0].trigger()
                menu.close()
                self.assertTrue(old["pinned"])
                group = tab.chat_list.topLevelItem(0)
                self.assertEqual(group.child(0).data(0, Qt.ItemDataRole.UserRole), old["id"])
                self.assertEqual(tab.current["id"], new["id"])
                self.assertIn("Закреплён", group.child(0).toolTip(0))
                tab.shutdown()
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                group = restored.chat_list.topLevelItem(0)
                self.assertEqual(group.child(0).data(0, Qt.ItemDataRole.UserRole), old["id"])
                restored._toggle_chat_pin(old["id"])
                group = restored.chat_list.topLevelItem(0)
                self.assertEqual(group.child(0).data(0, Qt.ItemDataRole.UserRole), new["id"])
                self.assertEqual(restored.current["id"], new["id"])
            finally:
                restored.shutdown(); restored.close()

    def test_unexpected_ai_callback_error_interrupts_only_its_task_and_saves_redacted_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab._run().config = dict(tab.store.config)
                tab._run().sent_images = []
                tab._begin_task("test callback")
                background = tab._run()
                background.key = "callback-private-key"
                tab._new_chat()
                other = tab._run()
                other.chat["running"] = True
                background.rpc.received.emit({"type": "tool_execution_end", "toolCallId": "bad-result",
                                              "toolName": "get_image_status", "result": {"content": None}})
                self.assertEqual(background.chat["outcome"], "failed")
                self.assertFalse(background.chat["running"])
                self.assertTrue(other.chat["running"])
                self.assertIs(tab.current, other.chat)
                self.assertIsNone(tab._event_run)
                self.assertIn("Внутренняя ошибка", background.chat["last_error"])
                diagnostic = Path(root, "errors.log").read_text(encoding="utf-8")
                self.assertIn("TypeError", diagnostic)
                self.assertNotIn(background.key, diagnostic)
            finally:
                tab.shutdown(); tab.close()

    def test_chat_list_orders_latest_messages_first_without_reordering_on_open_and_restores(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                old = tab.current
                old["last_activity"] = 10
                tab._new_chat()
                new = tab.current
                new["last_activity"] = 20
                tab._fill_chats()

                def order():
                    group = tab.chat_list.topLevelItem(0)
                    return [group.child(index).data(0, Qt.ItemDataRole.UserRole) for index in range(group.childCount())]

                self.assertEqual(order(), [new["id"], old["id"]])
                tab._select_chat(tab.store.chats.index(old))
                self.assertEqual(order(), [new["id"], old["id"]])
                tab._run().sent_images = []
                with patch("app.ui.ai_tab.time.time", return_value=30):
                    tab._begin_task("новое сообщение")
                self.assertEqual(order(), [old["id"], new["id"]])
                self.assertEqual(tab.current["id"], old["id"])
                tab.current["running"] = False
                tab.shutdown()
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                group = restored.chat_list.topLevelItem(0)
                self.assertEqual(group.child(0).data(0, Qt.ItemDataRole.UserRole), old["id"])
                self.assertEqual(group.child(1).data(0, Qt.ItemDataRole.UserRole), new["id"])
            finally:
                restored.shutdown(); restored.close()

    def test_chat_list_pulses_running_tasks_highlights_unread_results_and_restores_them(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.show()
            try:
                tab._run().config = dict(tab.store.config)
                tab._begin_task("Фоновая задача")
                background = tab._run()
                tab._new_chat()
                group = tab.chat_list.topLevelItem(0)
                item = next(group.child(i) for i in range(group.childCount())
                            if group.child(i).data(0, Qt.ItemDataRole.UserRole) == background.chat["id"])
                self.assertEqual(item.data(0, Qt.ItemDataRole.UserRole + 1), "running")
                self.assertIn("работает", item.text(0))
                self.assertTrue(tab._chat_pulse_timer.isActive())
                before = item.icon(0).pixmap(14, 14).toImage()
                tab._pulse_running_chats()
                self.assertNotEqual(before, item.icon(0).pixmap(14, 14).toImage())
                tab._dispatch_run(background, tab._event, {"type": "agent_end"})
                group = tab.chat_list.topLevelItem(0)
                item = next(group.child(i) for i in range(group.childCount())
                            if group.child(i).data(0, Qt.ItemDataRole.UserRole) == background.chat["id"])
                self.assertEqual(item.data(0, Qt.ItemDataRole.UserRole + 1), "ready")
                self.assertIn("готово", item.text(0))
                self.assertTrue(item.font(0).bold())
                self.assertFalse(tab._chat_pulse_timer.isActive())
                self.assertTrue(background.chat["unread_result"])
                background_id = background.chat["id"]
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                chat = next(c for c in restored.store.chats if c["id"] == background_id)
                self.assertTrue(chat["unread_result"])
                restored._select_chat(restored.store.chats.index(chat))
                self.assertFalse(chat["unread_result"])
                group = restored.chat_list.topLevelItem(0)
                item = next(group.child(i) for i in range(group.childCount())
                            if group.child(i).data(0, Qt.ItemDataRole.UserRole) == background_id)
                self.assertEqual(item.data(0, Qt.ItemDataRole.UserRole + 1), "idle")
                self.assertFalse(item.font(0).bold())
                restored._run().config = dict(restored.store.config)
                restored._begin_task("Задача с ошибкой")
                restored._new_chat()
                restored._dispatch_run(restored._runs[background_id], restored._error, "network fixture failure")
                self.assertTrue(chat["unread_result"])
                group = restored.chat_list.topLevelItem(0)
                item = next(group.child(i) for i in range(group.childCount())
                            if group.child(i).data(0, Qt.ItemDataRole.UserRole) == background_id)
                self.assertEqual(item.data(0, Qt.ItemDataRole.UserRole + 1), "failed")
            finally:
                restored.shutdown(); restored.close()

    def test_parallel_chats_route_pending_prompts_streams_and_stop_independently(self):
        from test_ai import FAKE
        with tempfile.TemporaryDirectory() as root:
            script = Path(root, "parallel.py")
            script.write_text(FAKE.replace('"Привет\\u2028мир"', 'cmd["message"]').replace(
                'cmd["message"] != "wait"', 'not cmd["message"].startswith("wait")'), encoding="utf-8")
            tab = AITab(data_dir=str(Path(root, "data")), work_dir=root)
            try:
                tab.model.setEditText("alpha-model")
                first = tab.current
                with patch("app.ui.ai_tab.rpc_command", return_value=(sys.executable, [str(script)], script)):
                    tab.input.setPlainText("wait alpha")
                    tab._send()
                    first_run = tab._selected_run
                    tab._new_chat()
                    second = tab.current
                    tab.model.setEditText("beta-model")
                    tab.input.setPlainText("wait beta")
                    tab._send()
                    second_run = tab._selected_run
                wait_for(lambda: first_run.rpc.busy and second_run.rpc.busy
                         and any(m.get("text") == "wait alpha" and m.get("role") == "assistant" for m in first["messages"])
                         and any(m.get("text") == "wait beta" and m.get("role") == "assistant" for m in second["messages"]))
                self.assertEqual(first["model"], "alpha-model")
                self.assertEqual(second["model"], "beta-model")
                self.assertTrue(tab.chat_list.isEnabled())
                self.assertTrue(tab.new_btn.isEnabled())
                tab._stop()
                wait_for(lambda: not second_run.rpc.busy)
                self.assertTrue(first_run.rpc.busy)
                self.assertEqual(second["outcome"], "stopped")
                tab._select_chat(tab.store.chats.index(first))
                self.assertEqual(tab.model.currentText(), "alpha-model")
                self.assertTrue(tab.stop_btn.isVisibleTo(tab))
                self.assertNotIn("wait beta", tab.output.toPlainText())
                tab._stop()
                wait_for(lambda: not first_run.rpc.busy)
                self.assertEqual(first["outcome"], "stopped")
            finally:
                tab.shutdown(); tab.close()

    def test_composer_grows_with_wrapped_text_and_returns_to_one_line(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.resize(900, 700)
                tab.show()
                APP.processEvents()
                compact = tab.input.height()
                tab.input.setPlainText("line\n" * 3 + "line")
                APP.processEvents()
                self.assertGreater(tab.input.height(), compact * 2)
                tab.input.setPlainText("line\n" * 40)
                APP.processEvents()
                maximum = tab.input.height()
                self.assertLessEqual(maximum, compact * 6)
                tab.input.setPlainText("wrapped words " * 35)
                APP.processEvents()
                self.assertGreater(tab.input.height(), compact)
                self.assertLessEqual(tab.input.height(), maximum)
                tab.input.clear()
                APP.processEvents()
                self.assertEqual(tab.input.height(), compact)
                self.assertIs(tab.model.parentWidget(), tab.composer)
            finally:
                tab.shutdown(); tab.close()

    def test_activity_bar_shows_current_action_and_cancels_in_the_transcript(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.show()
                tab.current["running"] = True
                tab._controls(True)
                tab._event({"type": "tool_execution_start", "toolCallId": "read-test", "toolName": "read", "args": {"path": "report.txt"}})
                self.assertFalse(tab.output.activity.isHidden())
                self.assertIn("Читает файл: report.txt", tab.output.activity_label.text())
                with patch.object(tab.rpc, "abort") as abort:
                    tab.stop_btn.click()
                    abort.assert_called_once()
                self.assertIn("Останавливает", tab.output.activity_label.text())
                self.assertFalse(tab.stop_btn.isEnabled())
                tab._event({"type": "agent_end"})
                self.assertTrue(tab.output.activity.isHidden())
                self.assertEqual(tab.current["outcome"], "stopped")
            finally:
                tab.shutdown(); tab.close()

    def test_two_instruction_modes_are_saved_and_passed_to_next_task(self):
        with tempfile.TemporaryDirectory() as root:
            tab=AITab(data_dir=root,work_dir=root)
            try:
                self.assertEqual(tab.response_style.count(),2)
                tab.response_style.setCurrentIndex(tab.response_style.findData('without_explanations'))
                tab.store.config['instructions_without_explanations']='Custom concise instruction'
                tab.model.setEditText('test-model')
                tab.input.setPlainText('task')
                with patch('app.ui.ai_tab.rpc_command',return_value=('node',['cli'],Path(root,'index.js'))), patch.object(tab.rpc,'start') as start:
                    tab._send()
                    args=start.call_args.args[1]
                    self.assertEqual(args[args.index('--append-system-prompt')+1],'Custom concise instruction')
                tab._pending=None
                tab.response_style.setCurrentIndex(tab.response_style.findData('with_explanations'))
                tab.store.config['instructions_with_explanations']='Custom progress instruction'
                tab._save_connection()
            finally:
                tab.shutdown();tab.close()
            restored=AITab(data_dir=root,work_dir=root)
            try:
                self.assertEqual(restored.response_style.currentData(),'with_explanations')
                self.assertEqual(restored.store.config['instructions_without_explanations'],'Custom concise instruction')
                self.assertEqual(restored.store.config['instructions_with_explanations'],'Custom progress instruction')
                restored._controls(True)
                self.assertFalse(restored.response_style.isEnabled())
                self.assertFalse(restored.instructions_btn.isEnabled())
            finally:
                restored.shutdown();restored.close()

    def test_instruction_editor_save_cancel_and_reset(self):
        from app.ui.ai_instructions import InstructionsDialog
        from PyQt6.QtWidgets import QDialogButtonBox,QDialog
        dialog=InstructionsDialog('Mode','custom','default')
        try:
            dialog.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
            self.assertEqual(dialog.editor.toPlainText(),'default')
            dialog.editor.setPlainText('edited')
            dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
            self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
            dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
            self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
        finally:
            dialog.close()

    def test_text_and_thinking_render_before_running_tool_finishes(self):
        with tempfile.TemporaryDirectory() as root:
            tab=AITab(data_dir=root,work_dir=root)
            try:
                tab.show()
                tab.current['running']=True
                tab._event({'type':'message_start','message':{'role':'assistant'}})
                for kind,index,text in [('thinking_delta',0,'Проверяю варианты'),('text_delta',1,'Сначала проверю файлы.')]:
                    tab._event({'type':'message_update','assistantMessageEvent':{'type':kind,'contentIndex':index,'delta':text}})
                tab._event({'type':'tool_execution_start','toolCallId':'running-tool','toolName':'bash','args':{'command':'long task'}})
                wait_for(lambda: 'Сначала проверю файлы.' in tab.output.toPlainText()
                         and all(row.isVisible() for row in tab.output.rows.values()))
                self.assertTrue(tab.current['running'])
                self.assertEqual(tab.current['messages'][-1]['status'],'running')
                thinking = next(row for row in tab.output.rows.values() if row.role=='thinking')
                assistant = next(row for row in tab.output.rows.values() if row.role=='assistant')
                self.assertTrue(thinking.body.isVisible())
                self.assertTrue(assistant.body.isVisible())
                self.assertIn('Проверяю варианты',thinking.body.toPlainText())
            finally:
                tab.shutdown();tab.close()

    def test_installation_blocks_chat_selection_and_runtime_controls(self):
        with tempfile.TemporaryDirectory() as root, patch('app.ui.ai_tab.RuntimeInstaller') as factory:
            factory.return_value.isRunning.return_value = True
            tab=AITab(data_dir=root,work_dir=root)
            try:
                tab._install_runtime()
                self.assertFalse(tab.connection_panel.isEnabled())
                self.assertFalse(tab.chat_list.isEnabled())
                tab._select_chat(0)
                self.assertFalse(tab.send_btn.isEnabled())
            finally:
                tab.shutdown(); tab.close()

    def test_incomplete_managed_runtime_offers_repair(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root,'runtime'); runtime.mkdir()
            with patch('app.core.ai_installer.runtime_folder',return_value=runtime), patch('app.ui.ai_tab.rpc_command',return_value=('node', ['cli'], runtime/'index.js')):
                tab=AITab(data_dir=str(Path(root,'data')),work_dir=root)
                try:
                    self.assertFalse(tab.install_panel.isHidden())
                    self.assertIsNone(tab.installer)
                    tab.install_panel.hide()
                    tab._auth_failed('missing component')
                    self.assertFalse(tab.install_panel.isHidden())
                finally:
                    tab.shutdown(); tab.close()

    def test_missing_runtime_is_offered_in_chat_before_model_and_not_for_horde(self):
        with (tempfile.TemporaryDirectory() as root,
              patch('app.ui.ai_tab.supported', return_value=True),
              patch('app.core.ai_installer.runtime_folder', return_value=Path(root, 'missing-runtime')),
              patch('app.core.ai.shutil.which', return_value=None),
              patch('app.ui.ai_tab.RuntimeInstaller') as factory):
            tab = AITab(data_dir=root, work_dir=root)
            tab.resize(860, 550)
            tab.show()
            try:
                APP.processEvents()
                install = next((button for button in tab.findChildren(type(tab.install_btn))
                                if button.text() == 'Установить компоненты ИИ' and button.isVisible()), None)
                self.assertIsNotNone(install, 'Установку должно быть видно прямо в чате')
                self.assertFalse(tab.model.currentText().strip())
                tab.input.setPlainText('Первый текстовый запрос')
                QTest.mouseClick(tab.send_btn, Qt.MouseButton.LeftButton)
                self.assertIn('компоненты ИИ', tab.status.text())
                self.assertEqual(tab.input.toPlainText(), 'Первый текстовый запрос')
                self.assertEqual(tab.current["messages"], [])
                self.assertIsNone(tab.installer)
                factory.assert_not_called()

                tab._select_free_images()
                self.assertFalse(install.isVisible())
                with patch.object(tab, "_send_image") as image:
                    QTest.mouseClick(tab.send_btn, Qt.MouseButton.LeftButton)
                    image.assert_called_once()
                tab.account.setCurrentIndex(tab.account.findData("default"))
                self.assertTrue(install.isVisible())
                QTest.mouseClick(install, Qt.MouseButton.LeftButton)
                factory.return_value.start.assert_called_once()
                self.assertTrue(tab.settings_dialog.isVisible())
                self.assertTrue(tab.install_progress.isVisible())
                self.assertEqual(tab.input.toPlainText(), 'Первый текстовый запрос')
            finally:
                if tab.installer:
                    tab.installer.isRunning.return_value = False
                tab.shutdown(); tab.close()

    def test_api_disconnect_clears_memory_and_saved_key_without_pi(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText("https://example.invalid/v1")
                tab.model.setEditText("model-id")
                tab.key.setText("synthetic-key")
                if sys.platform == "win32":
                    tab.remember.setChecked(True)
                    tab._save_connection()
                    self.assertTrue(tab.vault.path.exists())
                self.assertTrue(tab.login_btn.isHidden())
                self.assertEqual(tab.logout_btn.text(), "Отключить API-ключ")
                with patch.object(tab, "_start_auth", side_effect=AssertionError("API disconnect must not need Pi")):
                    tab.logout_btn.click()
                self.assertEqual(tab.key.text(), "")
                self.assertFalse(tab.remember.isChecked())
                self.assertFalse(tab.vault.path.exists())
                self.assertFalse(tab.logout_btn.isEnabled())
                self.assertEqual(tab.endpoint.text(), "https://example.invalid/v1")
                self.assertEqual(tab.model.currentText(), "model-id")
                self.assertIn("отключён", tab.auth_info.text())
            finally:
                tab.shutdown()
                tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.key.text(), "")
            finally:
                restored.shutdown()
                restored.close()

    def test_account_logout_confirmation_survives_catalog_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                events = b'{"type":"logged_out","provider":"openai-codex"}\n' + b'{"type":"catalog","models":[],"providers":[{"id":"openai-codex"}],"authenticated":[]}\n'
                with patch.object(tab.auth, "readAllStandardOutput", return_value=events), patch.object(tab, "_auth_command"):
                    tab._auth_read()
                self.assertIn("вышли из аккаунта", tab.auth_info.text())
            finally:
                tab.shutdown()
                tab.close()

    def test_reasoning_level_saved_passed_to_pi_and_effective_level_shown(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(tab.thinking.currentData(), "auto")
                tab.thinking.setCurrentIndex(tab.thinking.findData("xhigh"))
                tab.model.setEditText("fake-model")
                tab.input.setPlainText("hello")
                with patch("app.ui.ai_tab.rpc_command", return_value=("fake-node", ["cli.js"], Path("fake.js"))), patch.object(tab.rpc, "start") as start:
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
                self.assertTrue(tab.settings_dialog.isVisible())
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
                scroll.setValue(0)
                tab.current["messages"].append({"id": "new-message", "role": "assistant", "text": "Новое сообщение"})
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
                with patch("app.ui.ai_tab.rpc_command", return_value=(sys.executable, [str(script)], script)):
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
            self.assertIn(root, tab.folder_label.toolTip())
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

    def test_settings_model_and_footer_stay_in_sync_and_save_without_request(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.connection_btn.click()
                self.assertTrue(tab.settings_dialog.isVisible())
                tab.settings_model.setEditText("model-from-settings")
                self.assertEqual(tab.model.currentText(), "model-from-settings")
                tab.model.setEditText("model-from-footer")
                self.assertEqual(tab.settings_model.currentText(), "model-from-footer")
                tab.settings_dialog.close()
                self.assertEqual(tab.store.config["model"], "model-from-footer")
                self.assertFalse(tab.rpc.ready)
                tab._controls(True)
                self.assertFalse(tab.settings_model.isEnabled())
                self.assertFalse(tab.workspace_btn.isEnabled())
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.settings_model.currentText(), "model-from-footer")
            finally:
                restored.shutdown(); restored.close()

    def test_projects_group_chats_and_restore_selected_chat_and_folder(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = Path(root, "first"), Path(root, "second")
            first.mkdir(); second.mkdir()
            data = str(Path(root, "data"))
            tab = AITab(data_dir=data, work_dir=str(first))
            try:
                first_id = tab.current["id"]
                with patch("app.ui.ai_tab.QFileDialog.getExistingDirectory", return_value=str(second)):
                    tab.project_btn.click()
                second_id = tab.current["id"]
                tab._new_chat()
                self.assertEqual(tab.chat_list.topLevelItemCount(), 2)
                project = next(tab.chat_list.topLevelItem(i) for i in range(2)
                               if tab.chat_list.topLevelItem(i).toolTip(0) == str(second))
                self.assertEqual(project.text(0), "second (2)")
                self.assertEqual(project.childCount(), 2)
                first_index = next(i for i, chat in enumerate(tab.store.chats) if chat["id"] == first_id)
                tab._select_chat(first_index)
                self.assertEqual(tab.work_dir, str(first))
                self.assertEqual(tab.output.folder, str(first))
                self.assertTrue(tab.send_btn.isEnabled())
                self.assertEqual(tab.workspace_path.text(), str(first))
                tab._pending = "starting"
                tab._open_project(str(second))
                self.assertEqual(tab.current["id"], first_id)
                tab._pending = None
                self.assertNotEqual(first_id, second_id)
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=data, work_dir=str(second))
            try:
                self.assertEqual(restored.current["id"], first_id)
                self.assertEqual(restored.work_dir, str(first))
                self.assertEqual(len(restored.store.chats), 3)
                self.assertEqual(restored.chat_list.currentItem().data(0, Qt.ItemDataRole.UserRole), first_id)
            finally:
                restored.shutdown(); restored.close()

    def test_real_pi_uses_project_folder_and_recovers_from_unavailable_model(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from test_ai_pi import LocalAPI
        from app.core.ai import rpc_command
        try:
            rpc_command()
        except ValueError as exc:
            self.skipTest(str(exc))

        class ProjectAPI(LocalAPI):
            def do_GET(self):
                payload = json.dumps({"data": [{"id": "test-model", "owned_by": "local"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.server.reject_model:
                    self.rfile.read(int(self.headers["Content-Length"]))
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"model unavailable","type":"model_not_found"}}')
                else:
                    super().do_POST()

        with tempfile.TemporaryDirectory() as root:
            first, second = Path(root, "first"), Path(root, "second")
            first.mkdir(); second.mkdir()
            data = str(Path(root, "data"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), ProjectAPI)
            server.requests = []
            server.reject_model = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            tab = AITab(data_dir=data, work_dir=str(first))
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.key.setText("local-project-test-key")
                tab.model.setEditText("unavailable-model")
                tab.input.setPlainText("Create the file")
                tab.send_btn.click()
                wait_for(lambda: tab.current.get("outcome") == "failed", 30)
                self.assertIn("шестерёнку", tab.status.text())
                self.assertIn("Загрузить модели", tab.status.text())
                self.assertFalse(tab.error_settings_btn.isHidden())
                tab.error_settings_btn.click()
                self.assertTrue(tab.settings_dialog.isVisible())
                tab.catalog_btn.click()
                wait_for(lambda: tab.settings_model.findText("test-model") >= 0)
                tab.settings_model.setCurrentIndex(tab.settings_model.findText("test-model"))
                self.assertEqual(tab.model.currentText(), "test-model")
                server.reject_model = False
                tab._new_chat()
                first_id = tab.current["id"]
                tab.input.setPlainText("Create the file")
                tab.send_btn.click()
                wait_for(lambda: tab.current.get("outcome") == "completed", 30)
                self.assertEqual((first / "agent-proof.txt").read_text(), "created by real Pi")
                tab._open_project(str(second))
                second_id = tab.current["id"]
                tab.input.setPlainText("Create the file")
                tab.send_btn.click()
                wait_for(lambda: tab.current.get("outcome") == "completed", 30)
                self.assertEqual((second / "agent-proof.txt").read_text(), "created by real Pi")
                self.assertFalse(Path(data, "agent-proof.txt").exists())
                self.assertNotEqual(first_id, second_id)
                self.assertTrue(Path(tab.current["session"]).exists())
                request_count = len(server.requests)
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close(); thread.join(timeout=2)
            restored = AITab(data_dir=data, work_dir=str(first))
            try:
                self.assertEqual(restored.current["id"], second_id)
                self.assertEqual(restored.work_dir, str(second))
                self.assertIn("Файл создан", restored.output.toPlainText())
                self.assertFalse(restored.rpc.ready)
                self.assertEqual(len(server.requests), request_count)
            finally:
                restored.shutdown(); restored.close()

    def test_missing_project_folder_does_not_enable_execution(self):
        from app.core.ai import AIStore
        with tempfile.TemporaryDirectory() as root:
            data = str(Path(root, "data"))
            store = AIStore(data)
            store.new_chat(str(Path(root, "other")))
            tab = AITab(data_dir=data, work_dir=root)
            self.assertFalse(tab.send_btn.isEnabled())
            self.assertIn("папка этого чата недоступна", tab.status.text())
            tab.shutdown()
            tab.close()


if __name__ == "__main__":
    unittest.main()
