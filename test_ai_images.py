import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication
from app.core.ai_images import CLOUDFLARE_MODEL, ImageGenerator, image_content, wait_for_image_cancellations
from app.ui.ai_tab import AITab

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Image request did not complete")


class ImageTests(unittest.TestCase):
    def test_real_agent_receives_live_image_model_catalog_without_generating_or_exposing_keys(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root, patch("app.ui.ai_tab.HORDE_URL", self.base):
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_name = "list_image_models"
            server.tool_arguments = {}
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                horde = tab.store.ensure_horde_profile()
                tab._profile_keys[horde["id"]] = "private-horde-key"
                threading.Thread(target=server.serve_forever, daemon=True).start()
                parent = tab.current
                tab.input.setPlainText("Какие модели картинок доступны в AI Horde?")
                tab._send()
                wait_for(lambda: parent.get("outcome") in ("completed", "failed"), timeout=30)
                self.assertEqual(parent.get("outcome"), "completed", parent.get("last_error"))
                tool_message = next(m for m in server.requests[1]["messages"] if m.get("role") == "tool")
                catalog = json.loads(tool_message["content"])
                self.assertEqual(catalog["source"], "live")
                self.assertEqual([m["id"] for m in catalog["models"]], ["Dreamshaper", "Portrait Fixture"])
                self.assertEqual(catalog["account"]["id"], horde["id"])
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.requests[0][1], "/status/models?type=image")
                self.assertNotIn("authorization", self.requests[0][2])
                self.assertNotIn("private-horde-key", json.dumps(server.requests))
                self.assertEqual(len(tab.store.chats), 1)
                self.assertEqual(tab.model.currentText(), "test-model")
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()

    def test_image_model_catalog_routes_selected_account_filters_pages_and_marks_cached_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                profile = tab.store.add_profile("Images catalog")
                profile["config"].update(provider="lanmessenger-images", endpoint=self.base + "/v1", model="portrait-a")
                profile["api_models"] = [{"id": "saved-only"}]
                tab._profile_keys[profile["id"]] = "catalog-private-key"
                tab._fill_accounts()
                tab.image_tool_account.setCurrentIndex(tab.image_tool_account.findData(profile["id"]))
                tab._persist_connection()
                run = tab._run()
                run.config = dict(tab.store.config)
                run.image_connections = tab._image_connections()
                with patch.object(run.rpc, "command") as reply:
                    tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.list_image_models",
                                "id": "live", "placeholder": json.dumps({"query": "portrait", "offset": 1, "limit": 1})})
                    tab._new_chat()
                    selected = tab.current
                    wait_for(lambda: reply.called)
                    result = json.loads(reply.call_args.args[0]["value"])
                    self.assertEqual(result["total"], 2)
                    self.assertEqual([m["id"] for m in result["models"]], ["portrait-b"])
                    self.assertFalse(result["models"][0]["available"])
                    self.assertEqual([m["id"] for m in run.image_connections[-1]["models"]], ["portrait-a", "portrait-b"])
                    self.assertEqual(self.requests[-1][2]["authorization"], "Bearer catalog-private-key")
                    self.assertIs(tab.current, selected)
                    self.catalog_error = True
                    reply.reset_mock()
                    tab._dispatch_run(run, tab._event, {"type": "extension_ui_request", "method": "input",
                                          "title": "lanmessenger.list_image_models", "id": "saved", "placeholder": "{}"})
                    wait_for(lambda: reply.called)
                    saved = json.loads(reply.call_args.args[0]["value"])
                    self.assertEqual(saved["source"], "saved")
                    self.assertIn("устаревшим", saved["warning"])
                    self.assertEqual([m["id"] for m in saved["models"]], ["portrait-a", "portrait-b"])
                    self.assertNotIn("catalog-private-key", str(saved))
                    self.assertFalse(run.image_catalog_requests)
            finally:
                tab.shutdown(); tab.close()

    def test_stopping_agent_cancels_pending_catalog_and_ignores_late_result(self):
        with tempfile.TemporaryDirectory() as root, patch("app.ui.ai_tab.HORDE_URL", self.base):
            self.catalog_gate = threading.Event()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                run = tab._run()
                run.image_connections = tab._image_connections()
                with patch.object(run.rpc, "command") as reply, patch.object(run.rpc, "abort"):
                    tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.list_image_models",
                                "id": "catalog", "placeholder": "{}"})
                    wait_for(lambda: bool(self.requests))
                    tab._stop()
                    self.assertFalse(run.image_catalog_requests)
                    self.catalog_gate.set()
                    tab._image_models_result("catalog", [{"id": "late-model"}])
                    reply.assert_not_called()
            finally:
                self.catalog_gate.set()
                tab.shutdown(); tab.close()

    def test_real_agent_uses_configured_image_tool_provider_and_switches_without_changing_text_model(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_name = "generate_image"
            server.tool_arguments = {"wait_for_result": True, "prompt": "a cat on the moon"}
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                threading.Thread(target=server.serve_forever, daemon=True).start()
                for index in range(2):
                    profile = tab.store.add_profile(f"Images {index}")
                    profile["config"].update(provider="lanmessenger-images", endpoint=self.base + "/v1", model=f"default-image-{index}")
                    tab._profile_keys[profile["id"]] = f"image-fixture-key-{index}"
                    tab._fill_accounts()
                    tab.image_tool_account.setCurrentIndex(tab.image_tool_account.findData(profile["id"]))
                    if index == 0:
                        tab.image_tool_model.setEditText("override-image")
                    else:
                        tab._new_chat()
                    parent = tab.current
                    tab.input.setPlainText("Нарисуй кота настроенным инструментом картинок")
                    tab._send()
                    wait_for(lambda: parent.get("outcome") in ("completed", "failed"), timeout=30)
                    self.assertEqual(parent.get("outcome"), "completed", parent.get("last_error"))
                    child = next(c for c in tab.store.chats if c.get("parent_chat_id") == parent["id"])
                    self.assertEqual(child["profile_id"], profile["id"])
                    self.assertEqual(child["model"], "override-image" if index == 0 else "default-image-1")
                    self.assertEqual(tab.model.currentText(), "test-model")
                    self.assertEqual(tab.provider.currentData(), "lanmessenger-api")
                    self.assertEqual(self.requests[-1][2]["authorization"], f"Bearer image-fixture-key-{index}")
                    self.assertEqual(self.requests[-1][3]["model"], child["model"])
                    tool = next(t["function"] for t in server.requests[-2]["tools"] if t["function"]["name"] == "generate_image")
                    self.assertIn(profile["id"], tool["description"])
                    self.assertIn('"default":true', tool["description"])
                self.assertEqual(sum(r[0] == "POST" for r in self.requests), 2)
                self.assertTrue(tab.rpc.ready)
                profile["config"]["model"] = "changed-underlying-model"
                tab._profile_keys[profile["id"]] = "rotated-image-key"
                server.force_tool_requests = {len(server.requests) + 1}
                tab.input.setPlainText("Нарисуй ещё раз после обновления подключения картинок")
                tab._send()
                wait_for(lambda: sum(r[0] == "POST" for r in self.requests) == 3 and not parent.get("running"), timeout=30)
                self.assertEqual(parent.get("outcome"), "completed", parent.get("last_error"))
                self.assertEqual(self.requests[-1][3]["model"], "changed-underlying-model")
                self.assertEqual(self.requests[-1][2]["authorization"], "Bearer rotated-image-key")
                tool = next(t["function"] for t in server.requests[-2]["tools"] if t["function"]["name"] == "generate_image")
                self.assertIn("changed-underlying-model", tool["description"])
                self.assertNotIn("image-fixture-key", json.dumps(server.requests))
                self.assertNotIn("rotated-image-key", json.dumps(server.requests))
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()

    def test_stopping_parent_cancels_only_its_child_image_job(self):
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            self.done = False
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                parent.config = dict(tab.store.config)
                parent.profile_id = tab.store.active_profile
                parent.chat["running"] = True
                parent.rpc.busy = True
                parent.image_connections = tab._image_connections()
                tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.generate_image",
                            "id": "queued-child", "placeholder": json.dumps({"wait_for_result": True, "prompt": "cat"})})
                child = parent.image_tool_requests["queued-child"]["children"][0]
                wait_for(lambda: bool(child.chat.get("image_job")))
                tab._new_chat()
                other = tab.current
                tab._select_chat(tab.store.chats.index(parent.chat))
                with patch.object(parent.rpc, "abort") as abort:
                    tab._stop()
                    abort.assert_called_once()
                self.assertFalse(child.images.busy)
                self.assertEqual(child.chat["outcome"], "stopped")
                self.assertNotIn("image_job", child.chat)
                self.assertEqual(parent.image_tool_requests, {})
                self.assertEqual(other["messages"], [])
            finally:
                tab.shutdown(); tab.close()

    def test_agent_generates_in_visible_child_chat_and_gets_image_content_without_switching_model(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_name = "generate_image"
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                profile = tab.store.add_profile("Картинки fixture")
                profile["config"].update(provider="lanmessenger-images", endpoint=self.base + "/v1", model="fixture-image")
                tab._profile_keys[profile["id"]] = "image-fixture-key"
                server.tool_arguments = {"wait_for_result": True, "prompt": "a ginger cat on the moon", "account": profile["id"], "model": "fixture-image"}
                threading.Thread(target=server.serve_forever, daemon=True).start()
                parent = tab.current
                tab.input.setPlainText("Нарисуй кота через Картинки fixture")
                tab._send()
                wait_for(lambda: parent.get("outcome") in ("completed", "failed"), timeout=30)
                self.assertEqual(parent.get("outcome"), "completed", parent.get("last_error"))
                self.assertIs(tab.current, parent)
                self.assertEqual(tab.model.currentText(), "test-model")
                child = next(c for c in tab.store.chats if c.get("parent_chat_id") == parent["id"])
                self.assertEqual(child["model"], "fixture-image")
                self.assertEqual(child["outcome"], "completed")
                self.assertTrue(child["messages"][-1]["images"])
                self.assertTrue(any(m.get("source_chat_id") == child["id"] for m in parent["messages"]))
                activity = next(m for m in parent["messages"] if m.get("role") == "image_activity")
                self.assertEqual((activity["status"], activity["job_chat_id"]), ("completed", child["id"]))
                self.assertIn("Готово", tab.output.rows[activity["id"]].header.text())
                tab.output.rows[activity["id"]].open_chat.emit(child["id"])
                self.assertIs(tab.current, child)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.requests[0][2]["authorization"], "Bearer image-fixture-key")
                self.assertEqual(len(server.requests), 2)
                self.assertIn("image_url", json.dumps(server.requests[1]["messages"]))
                self.assertNotIn("image-fixture-key", json.dumps(server.requests))
                tab._select_chat(tab.store.chats.index(child))
                self.assertEqual(tab.model.currentText(), "fixture-image")
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()

    def test_real_pi_accepts_an_active_followup_and_reloads_the_per_chat_tool_selection(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            release = threading.Event()

            def hold_first_response(_request, count):
                if count == 1:
                    release.wait(10)

            server.tool_plan = hold_first_response
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                tab.plugin_checks["images"].click()
                tab.input.setPlainText("Первая задача")
                tab._send()
                wait_for(lambda: len(server.requests) == 1, timeout=20)
                names = {entry["function"]["name"] for entry in server.requests[0]["tools"]}
                self.assertNotIn("generate_image", names)
                self.assertIn("ask_agent", names)
                tab.input.setPlainText("А вот ещё условие")
                tab._send()
                self.assertEqual(list(tab._run().followups.values()), ["А вот ещё условие"])
                self.assertEqual(len(server.requests), 1)
                release.set()
                wait_for(lambda: len(server.requests) == 2 and not tab.current.get("running"), timeout=20)
                self.assertEqual(tab.current["outcome"], "completed", tab.current.get("last_error"))
                self.assertTrue(any("А вот ещё условие" in json.dumps(request["messages"], ensure_ascii=False)
                                    for request in server.requests[1:]))
                self.assertEqual([row["text"] for row in tab.current["messages"] if row["role"] == "user"],
                                 ["Первая задача", "А вот ещё условие"])
                tab.plugin_checks["images"].click()
                tab.input.setPlainText("Ещё запрос")
                tab._send()
                wait_for(lambda: len(server.requests) >= 3 and not tab.current.get("running"), timeout=20)
                names = {entry["function"]["name"] for entry in server.requests[-1]["tools"]}
                self.assertIn("generate_image", names)
                self.assertEqual([row["text"] for row in tab.current["messages"] if row["role"] == "user"],
                                 ["Первая задача", "А вот ещё условие", "Ещё запрос"])
            finally:
                release.set()
                tab.shutdown()
                tab.close()
                server.shutdown()
                server.server_close()

    def test_real_pi_clarification_interrupts_stream_before_queued_request(self):
        from app.core.ai_timeline import message_text
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_plan = lambda *_args: None
            release = threading.Event()
            server.after_delta = lambda number: release.wait(20) if number == 1 else None
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                tab.input.setPlainText("Первая задача")
                tab._send()
                wait_for(lambda: any(row["role"] == "assistant" for row in tab.current["messages"]), timeout=10)
                tab.input.setPlainText("Это после задачи")
                tab.send_btn.click()
                tab.input.setPlainText("Уточнение к текущей задаче")
                tab.clarify_btn.click()
                wait_for(lambda: len(server.requests) == 3 and not tab.current.get("running"), timeout=10)
                self.assertFalse(release.is_set(), "Новый запрос должен начаться до завершения исходного потока")
                self.assertEqual(tab.current["outcome"], "completed", tab.current.get("last_error"))
                self.assertEqual([next(message_text(row) for row in reversed(request["messages"]) if row["role"] == "user")
                                  for request in server.requests], ["Первая задача", "Уточнение к текущей задаче", "Это после задачи"])
                self.assertEqual([row["role"] for row in tab.current["messages"]], ["user", "assistant"] * 3)
                self.assertEqual(tab.current["messages"][1]["text"], "Файл создан локальным агентом.")
                self.assertEqual(tab.current["messages"][1]["status"], "stopped")
                session = [json.loads(line) for line in Path(tab.current["session"]).read_text(encoding="utf-8").splitlines()]
                aborted = next(entry["message"] for entry in session if entry.get("message", {}).get("stopReason") == "aborted")
                self.assertEqual(message_text(aborted), "Файл создан локальным агентом.")
            finally:
                release.set()
                tab.shutdown()
                tab.close()
                server.shutdown()
                server.server_close()
            reopened = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                self.assertEqual([row["text"] for row in reopened.current["messages"] if row["role"] == "user"],
                                 ["Первая задача", "Уточнение к текущей задаче", "Это после задачи"])
                self.assertEqual(reopened.current["messages"][1]["text"], "Файл создан локальным агентом.")
                self.assertEqual(reopened.output.rows[reopened.current["messages"][1]["id"]].header.text(), "Агент · Ответ прерван")
            finally:
                reopened.shutdown()
                reopened.close()

    def test_image_paste_drop_send_and_preview_survive_session_reconciliation(self):
        from PyQt6.QtCore import QMimeData, QUrl
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                picture = QImage.fromData(self.png)
                pasted = QMimeData()
                pasted.setImageData(picture)
                tab.input.insertFromMimeData(pasted)
                source = Path(self.temp.name, "drop.webp")
                self.assertTrue(picture.save(str(source), "WEBP"))
                dropped = QMimeData()
                dropped.setUrls([QUrl.fromLocalFile(str(source))])
                tab.input.insertFromMimeData(dropped)
                self.assertEqual(len(tab._run().attachments), 2)
                paths = list(tab._run().attachments)
                self.assertEqual(image_content(paths[1])["mimeType"], "image/png")
                tab.model.setEditText("vision-test")
                tab.rpc.ready = True
                tab.input.setPlainText("Что на картинках?")
                with patch.object(tab.rpc, "send_prompt") as send:
                    tab._send()
                    self.assertEqual(len(send.call_args.args[1]), 2)
                    self.assertTrue(all(image["data"] for image in send.call_args.args[1]))
                tab._event({"type": "agent_end"})
                tab._event({"type": "response", "command": "get_messages", "success": True,
                            "data": {"messages": [{"role": "user", "content": [{"type": "text", "text": "Что на картинках?"}]}]}})
                self.assertEqual(tab.current["messages"][0]["images"], paths)
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                self.assertEqual(restored.current["messages"][0]["images"], paths)
                self.assertEqual(len(next(iter(restored.output.rows.values()))._image_widgets), 2)
            finally:
                restored.shutdown(); restored.close()

    def test_real_agent_submits_different_models_before_first_result_and_relays_each_image(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            self.parallel_jobs = True
            self.done = False
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_name = "generate_image"
            server.tool_arguments = {"wait_for_result": True, "prompt": "a cat on the moon", "models": ["slow-model", "fast-model"]}
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                threading.Thread(target=server.serve_forever, daemon=True).start()
                parent = tab._run()
                tab.input.setPlainText("Нарисуй кота двумя разными моделями параллельно")
                tab._send()
                wait_for(lambda: len(self.jobs) == 2, timeout=30)
                batch = next(iter(parent.image_tool_requests.values()))
                slow, fast = batch["children"]
                wait_for(lambda: all(c.chat.get("image_job") for c in (slow, fast)))
                self.assertTrue(all(c.images.busy for c in (slow, fast)))
                self.assertEqual(len(server.requests), 1)
                self.assertIs(tab.current, parent.chat)
                self.done_models.add("fast-model")
                fast.images._check()
                wait_for(lambda: fast.chat.get("outcome") == "completed")
                self.assertTrue(slow.images.busy)
                self.assertEqual([m["source_chat_id"] for m in parent.chat["messages"] if m.get("source_chat_id")], [fast.chat["id"]])
                self.assertEqual(len(server.requests), 1)
                self.done_models.add("slow-model")
                slow.images._check()
                wait_for(lambda: parent.chat.get("outcome") in ("completed", "failed"), timeout=30)
                self.assertEqual(parent.chat["outcome"], "completed", parent.chat.get("last_error"))
                self.assertEqual(parent.image_tool_requests, {})
                self.assertEqual(tab.model.currentText(), "test-model")
                self.assertEqual(sum(r[0] == "POST" for r in self.requests), 2)
                self.assertEqual(json.dumps(server.requests[1]["messages"]).count('"type": "image_url"'), 2)
                self.assertTrue(all(c.chat["outcome"] == "completed" for c in (slow, fast)))
                child_ids = [c.chat["id"] for c in (slow, fast)]
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()
            restored = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                self.assertEqual(sum(bool(m.get("source_chat_id")) for m in restored.current["messages"]), 2)
                for chat_id, model in zip(child_ids, ("slow-model", "fast-model")):
                    chat = next(c for c in restored.store.chats if c["id"] == chat_id)
                    restored._select_chat(restored.store.chats.index(chat))
                    self.assertEqual(restored.model.currentText(), model)
                    self.assertTrue(Path(chat["messages"][-1]["images"][0]).is_file())
            finally:
                restored.shutdown(); restored.close()

    def test_real_agent_independent_tool_calls_overlap_and_one_failure_does_not_block_another(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            self.parallel_jobs = True
            self.done = False
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            server.tool_calls = [{"index": i, "id": f"call_image_{i}", "type": "function",
                                  "function": {"name": "generate_image", "arguments": json.dumps({"wait_for_result": True, "prompt": "cat", "model": model})}}
                                 for i, model in enumerate(("failed-model", "good-model"))]
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                threading.Thread(target=server.serve_forever, daemon=True).start()
                parent = tab._run()
                tab.input.setPlainText("Нарисуй кота разными моделями")
                tab._send()
                wait_for(lambda: len(self.jobs) == 2, timeout=30)
                self.assertEqual(len(parent.image_tool_requests), 2)
                children = [b["children"][0] for b in parent.image_tool_requests.values()]
                wait_for(lambda: all(c.chat.get("image_job") for c in children))
                failed = next(c for c in children if c.config["model"] == "failed-model")
                good = next(c for c in children if c.config["model"] == "good-model")
                self.failed_models.add("failed-model")
                failed.images._check()
                wait_for(lambda: failed.chat.get("outcome") == "failed")
                self.assertTrue(good.images.busy)
                self.assertEqual(len(parent.image_tool_requests), 1)
                self.done_models.add("good-model")
                good.images._check()
                wait_for(lambda: parent.chat.get("outcome") in ("completed", "failed"), timeout=30)
                self.assertEqual(parent.chat["outcome"], "completed", parent.chat.get("last_error"))
                self.assertEqual(good.chat["outcome"], "completed")
                self.assertEqual(parent.image_tool_requests, {})
                self.assertEqual(sum(r[0] == "POST" for r in self.requests), 2)
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()

    def test_stopping_parent_cancels_all_pending_models_and_leaves_other_chats_running(self):
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            self.parallel_jobs = True
            self.done = False
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                parent.config = dict(tab.store.config)
                parent.profile_id = tab.store.active_profile
                parent.chat["running"] = True
                parent.rpc.busy = True
                parent.image_connections = tab._image_connections()
                tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.generate_image",
                            "id": "batch", "placeholder": json.dumps({"wait_for_result": True, "prompt": "cat", "models": ["model-a", "model-b"]})})
                children = parent.image_tool_requests["batch"]["children"]
                wait_for(lambda: all(c.chat.get("image_job") for c in children))
                tab._new_chat()
                other = tab._run()
                tab.model.setEditText("other-model")
                other.config = dict(tab.store.config, model="other-model", provider="ai-horde")
                tab._dispatch_run(other, tab._begin_task, "cat", True)
                tab._dispatch_run(other, other.images.generate, "ai-horde", "", "other-model", "", "cat", self.temp.name)
                wait_for(lambda: len(self.jobs) == 3)
                tab._select_chat(tab.store.chats.index(parent.chat))
                with patch.object(parent.rpc, "abort"):
                    tab._stop()
                self.assertTrue(all(not c.images.busy and c.chat["outcome"] == "stopped" for c in children))
                self.assertEqual(parent.image_tool_requests, {})
                self.assertTrue(other.images.busy)
                self.assertTrue(other.chat["running"])
                wait_for(lambda: sum(r[0] == "DELETE" for r in self.requests) == 2)
            finally:
                tab.shutdown(); tab.close()

    def test_stopping_one_model_in_batch_keeps_other_running_and_returns_partial_result(self):
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            self.parallel_jobs = True
            self.done = False
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                parent.config = dict(tab.store.config)
                parent.profile_id = tab.store.active_profile
                parent.chat["running"] = True
                parent.rpc.busy = True
                parent.image_connections = tab._image_connections()
                tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.generate_image",
                            "id": "batch", "placeholder": json.dumps({"wait_for_result": True, "prompt": "cat", "models": ["model-a", "model-b"]})})
                first, second = parent.image_tool_requests["batch"]["children"]
                wait_for(lambda: all(c.chat.get("image_job") for c in (first, second)))
                tab._select_chat(tab.store.chats.index(first.chat))
                with patch.object(parent.rpc, "command") as reply:
                    tab._stop()
                    self.assertTrue(second.images.busy)
                    self.assertEqual(first.chat["outcome"], "stopped")
                    reply.assert_not_called()
                    self.done_models.add("model-b")
                    second.images._check()
                    wait_for(lambda: second.chat.get("outcome") == "completed")
                    reply.assert_called_once()
                    results = json.loads(reply.call_args.args[0]["value"])["results"]
                    self.assertIn("error", results[0])
                    self.assertEqual(results[0]["model"], "model-a")
                    self.assertTrue(results[1]["images"])
                    self.assertEqual(results[1]["model"], "model-b")
                    self.assertEqual(parent.image_tool_requests, {})
            finally:
                tab.shutdown(); tab.close()

    def test_duplicate_or_invalid_model_batch_does_not_submit_any_image_request(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab._run().image_connections = tab._image_connections()
                for models in (["model-a", " model-a "], [], ["a"] * 5, [""]):
                    with patch.object(tab.rpc, "command") as reply:
                        tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger.generate_image",
                                    "id": "invalid", "placeholder": json.dumps({"wait_for_result": True, "prompt": "cat", "models": models})})
                        self.assertIn("error", json.loads(reply.call_args.args[0]["value"]))
                    self.assertEqual(tab._run().image_tool_requests, {})
                self.assertEqual(len(tab.store.chats), 1)
                self.assertEqual(self.requests, [])
            finally:
                tab.shutdown(); tab.close()

    def test_read_other_ai_chat_returns_saved_text_and_images_without_changing_selection(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                original = tab.current
                tab._attach_data(self.png)
                path = tab._run().attachments[0]
                original["title"] = "Другой ИИ-чат"
                original["messages"].append({"role": "assistant", "text": "orange cat", "images": [path]})
                tab._new_chat()
                selected = tab.current
                for name, args in (("list_chats", {"query": "Другой"}), ("read_chat", {"chat_id": original["id"]})):
                    with patch.object(tab.rpc, "command") as reply:
                        tab._event({"type": "extension_ui_request", "method": "input", "title": "lanmessenger." + name,
                                    "id": "read-fixture", "placeholder": json.dumps(args)})
                        result = json.loads(reply.call_args.args[0]["value"])
                        self.assertNotIn("error", result)
                        self.assertIs(tab.current, selected)
                        if name == "read_chat":
                            self.assertEqual(result["messages"][0]["text"], "orange cat")
                            self.assertTrue(result["images"][0]["data"])
                        else:
                            self.assertEqual(result["chats"][0]["id"], original["id"])
            finally:
                tab.shutdown(); tab.close()

    def setUp(self):
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("#d66e21"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.png = bytes(data)
        self.requests = []
        self.payload = {"data": [{"b64_json": base64.b64encode(self.png).decode()}]}
        self.done = True
        self.check_count = 0
        self.job_id = "06b2a70c-2d3e-4210-b088-8708b93a9db9"
        self.parallel_jobs = False
        self.jobs = {}
        self.done_models = set()
        self.failed_models = set()
        self.catalog_error = False
        self.catalog_gate = None
        self.submit_gate = None
        self.delete_gate = None
        self.delete_status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, payload, status=None):
                self.send_response(status or (503 if owner.catalog_error and "models" in self.path else 200))
                self.end_headers()
                try:
                    self.wfile.write(json.dumps(payload).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.path.endswith("/generate/async"):
                    job_id = str(uuid.uuid4()) if owner.parallel_jobs else owner.job_id
                    owner.jobs[job_id] = body["models"][0]
                    if owner.submit_gate:
                        owner.submit_gate.wait(4)
                    self.respond({"id": job_id})
                else:
                    self.respond(owner.payload)

            def do_DELETE(self):
                owner.requests.append(("DELETE", self.path, {k.lower(): v for k, v in self.headers.items()}, None))
                if owner.delete_gate:
                    owner.delete_gate.wait(4)
                self.respond({}, owner.delete_status)

            def do_GET(self):
                owner.requests.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}, None))
                if "models" in self.path:
                    if owner.catalog_gate:
                        owner.catalog_gate.wait(5)
                    if self.path == "/status/models?type=image":
                        self.respond([{"name": "Dreamshaper", "count": 3}, {"name": "Portrait Fixture", "count": 1},
                                      {"name": "Offline", "count": 0}])
                    else:
                        self.respond({"data": [{"id": "portrait-a", "available": True},
                                              {"id": "portrait-b", "available": False, "unavailableReason": "NO_ACCESS"}]})
                elif "/generate/check/" in self.path:
                    owner.check_count += 1
                    model = owner.jobs.get(self.path.rsplit("/", 1)[-1])
                    self.respond({"done": owner.done or model in owner.done_models, "faulted": model in owner.failed_models,
                                  "queue_position": 3, "wait_time": 10})
                elif "/generate/status/" in self.path:
                    self.respond({"generations": [{"img": base64.b64encode(owner.png).decode()}]})
                elif self.path == "/asset-redirect":
                    self.send_response(302)
                    self.send_header("Location", "/asset")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(owner.png)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.temp = tempfile.TemporaryDirectory()
        self.generator = ImageGenerator()
        self.results = []
        self.errors = []
        self.generator.completed.connect(self.results.append)
        self.generator.failed.connect(self.errors.append)

    def tearDown(self):
        self.generator.cancel(remote=False)
        APP.processEvents()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def generate(self):
        self.errors.clear()
        self.generator.generate("lanmessenger-images", self.base + "/v1", "image-model", "synthetic-key", "cat on the moon", self.temp.name)
        wait_for(lambda: self.results or self.errors)

    def test_image_post_and_saved_original(self):
        self.generate()
        self.assertFalse(self.errors)
        self.assertEqual(Path(self.results[0][0]).read_bytes(), self.png)
        method, path, headers, body = self.requests[0]
        self.assertEqual((method, path), ("POST", "/v1/images/generations"))
        self.assertEqual(headers["authorization"], "Bearer synthetic-key")
        self.assertEqual(body, {"model": "image-model", "prompt": "cat on the moon", "n": 1, "response_format": "b64_json"})

    def test_download_and_redirect_do_not_forward_credentials(self):
        self.payload = {"data": [{"url": self.base + "/asset-redirect"}]}
        self.generate()
        self.assertFalse(self.errors)
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(all("authorization" not in headers for method, _path, headers, _body in self.requests if method == "GET"))

    def test_bad_image_and_error_do_not_retry_or_expose_key(self):
        for payload in ({"data": []}, {"data": [{"b64_json": "<html>synthetic-key"}]}, {"error": "synthetic-key"}):
            self.payload = payload
            self.generate()
            self.assertFalse(self.results)
            self.assertNotIn("synthetic-key", str(self.errors))
        self.assertEqual(len(self.requests), 3)

    def test_horde_generation_and_resume_reuse_job(self):
        with patch("app.core.ai_images.HORDE_URL", self.base):
            self.generator.generate("ai-horde", "", "Dreamshaper", "", "cat", self.temp.name)
            wait_for(lambda: self.results or self.errors)
            self.assertFalse(self.errors)
            self.assertEqual(self.requests[0][2]["apikey"], "0000000000")
            self.generator.resume({"provider": "ai-horde", "id": self.job_id}, self.temp.name)
            wait_for(lambda: len(self.results) == 2 or self.errors)
        self.assertEqual(sum(r[0] == "POST" for r in self.requests), 1)
        self.assertEqual(sum("/generate/status/" in r[1] for r in self.requests), 2)

    def test_exit_waits_for_remote_cancellation_of_all_children_and_saved_unopened_jobs(self):
        self.done = False
        self.parallel_jobs = True
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                parent.config = dict(tab.store.config)
                parent.profile_id = tab.store.active_profile
                parent.image_connections = tab._image_connections()
                parent.chat["running"] = True
                tab._agent_image_request({"id": "exit-batch", "placeholder": json.dumps({
                    "prompt": "cat", "models": ["model-a", "model-b"], "wait_for_result": True})})
                children = parent.image_tool_requests["exit-batch"]["children"]
                wait_for(lambda: all(child.chat.get("image_job") for child in children))
                saved = tab.store.new_chat(self.temp.name)
                saved.update(provider="ai-horde", model="saved", image_job={"provider": "ai-horde", "id": str(uuid.uuid4())})
                expected = {child.chat["image_job"]["id"] for child in children} | {saved["image_job"]["id"]}
                tab.shutdown()
                deleted = [request[1].rsplit("/", 1)[-1] for request in self.requests if request[0] == "DELETE"]
                self.assertEqual(set(deleted), expected)
                self.assertEqual(len(deleted), 3)
                self.assertTrue(all(not child.images.cancellation_pending for child in children))
                restored = AITab(data_dir=root, work_dir=self.temp.name)
                try:
                    for chat in restored.store.chats:
                        if chat["id"] in {child.chat["id"] for child in children} | {saved["id"]}:
                            self.assertNotIn("image_job", chat)
                            self.assertEqual(chat["outcome"], "stopped")
                            self.assertFalse(chat["running"])
                finally:
                    restored.shutdown(); restored.close()
            finally:
                tab.shutdown(); tab.close()

    def test_exit_during_horde_submission_cancels_the_returned_job_without_duplicate_post(self):
        self.submit_gate = threading.Event()
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("ai-horde"))
                tab.model.setEditText("Dreamshaper")
                tab.input.setPlainText("cat")
                tab._send()
                wait_for(lambda: any(request[0] == "POST" for request in self.requests))
                threading.Timer(.1, self.submit_gate.set).start()
                tab.shutdown()
                self.assertEqual(sum(request[0] == "POST" for request in self.requests), 1)
                self.assertEqual(sum(request[0] == "DELETE" for request in self.requests), 1)
                self.assertEqual(tab.current["outcome"], "stopped")
                self.assertNotIn("image_job", tab.current)
            finally:
                self.submit_gate.set()
                tab.shutdown(); tab.close()

    def test_unconfirmed_exit_cancellation_preserves_job_for_later_check(self):
        self.done = False
        self.delete_status = 503
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("ai-horde"))
                tab.model.setEditText("Dreamshaper")
                tab.input.setPlainText("cat")
                tab._send()
                wait_for(lambda: tab.current.get("image_job"))
                tab.shutdown()
                self.assertEqual(tab.current["image_job"]["id"], self.job_id)
                self.assertTrue(tab.current["interrupted"])
                self.assertIn("не подтверждена", tab.current["messages"][-1]["text"])
            finally:
                tab.shutdown(); tab.close()

    def test_cancellation_wait_has_a_shared_deadline(self):
        self.delete_gate = threading.Event()
        with patch("app.core.ai_images.HORDE_URL", self.base):
            self.generator.cancel(job={"provider": "ai-horde", "id": self.job_id})
            started = time.monotonic()
            wait_for_image_cancellations([self.generator], timeout_ms=80)
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(self.generator.cancellation_pending)
            self.delete_gate.set()
            wait_for(lambda: not self.generator.cancellation_pending)
            self.assertIn(self.job_id, self.generator.cancelled_jobs)

    def test_horde_parallel_jobs_are_submitted_at_service_rate_without_waiting_for_images(self):
        self.done = False
        self.parallel_jobs = True
        generators = [ImageGenerator() for _ in range(4)]
        moments = []
        with patch("app.core.ai_images.HORDE_URL", self.base):
            try:
                for index, generator in enumerate(generators):
                    generator.job_started.connect(lambda _job: moments.append(time.monotonic()))
                    generator.generate("ai-horde", "", f"model-{index}", "", "cat", self.temp.name)
                wait_for(lambda: len(moments) == 4)
                self.assertTrue(all(generator.busy for generator in generators))
                self.assertEqual(len(self.jobs), 4)
                self.assertGreaterEqual(moments[-1] - moments[0], 1.45)
                self.assertEqual(sum(request[0] == "POST" for request in self.requests), 4)
            finally:
                for generator in generators:
                    generator.cancel()
                wait_for_image_cancellations(generators)

    def test_horde_submission_spacing_survives_a_delayed_event_loop(self):
        generators = [ImageGenerator() for _ in range(4)]
        moments = []
        with patch.object(ImageGenerator, "_request", side_effect=lambda *args, **kwargs: moments.append(time.monotonic())):
            try:
                for index, generator in enumerate(generators):
                    generator.generate("ai-horde", "", f"model-{index}", "", "cat", self.temp.name)
                time.sleep(1.2)
                APP.processEvents()
                wait_for(lambda: len(moments) == 4)
                self.assertTrue(all(generator.busy for generator in generators))
                self.assertTrue(all(later - earlier >= .5 for earlier, later in zip(moments, moments[1:])))
            finally:
                for generator in generators:
                    generator.cancel(remote=False)

    def test_real_agent_receives_queue_checks_status_and_cancels_its_job(self):
        from test_ai_pi import LocalAPI
        self.done = False
        with tempfile.TemporaryDirectory() as root, patch("app.core.ai_images.HORDE_URL", self.base):
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            snapshots = []

            def plan(request, number):
                if number == 1:
                    name, args = "generate_image", {"prompt": "cat"}
                elif number in (2, 3):
                    content = next(message["content"] for message in reversed(request["messages"]) if message.get("role") == "tool")
                    snapshot = json.loads(content[content.index("{"):])["results"][0] if number == 3 else json.loads(content[content.index("{"):])
                    snapshots.append(snapshot)
                    name = "get_image_status" if number == 2 else "cancel_image_generation"
                    args = {"chat_ids": [snapshot["chat_id"]]}
                    if number == 2:
                        args["wait_seconds"] = 0
                else:
                    return None
                return [{"index": 0, "id": f"call_{number}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]

            server.tool_plan = plan
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("text-fixture-key")
                tab.input.setPlainText("Нарисуй кота, но если очередь больше пяти секунд, отмени")
                tab._send()
                wait_for(lambda: tab.current.get("outcome") in ("completed", "failed"), timeout=30)
                self.assertEqual(tab.current["outcome"], "completed", tab.current.get("last_error"))
                self.assertEqual(len(snapshots), 2)
                self.assertTrue(all(snapshot["queue"]["estimated_wait_seconds"] == 10 for snapshot in snapshots))
                self.assertTrue(all(snapshot["queue"]["queue_position"] == 3 for snapshot in snapshots))
                self.assertTrue(all(snapshot["state"] == "queued" for snapshot in snapshots))
                wait_for(lambda: any(request[0] == "DELETE" for request in self.requests))
                self.assertEqual(sum(request[0] == "POST" for request in self.requests), 1)
                child = next(chat for chat in tab.store.chats if chat.get("parent_chat_id"))
                self.assertEqual(child["outcome"], "stopped")
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()

    def test_real_agent_asks_a_new_model_then_the_same_existing_chat_and_receives_answers(self):
        from test_ai_pi import LocalAPI
        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            answers = []
            tool_errors = []

            def plan(request, _number):
                if request["model"] == "worker-model":
                    return None
                count = sum(item["model"] == "test-model" for item in server.requests)
                if count == 1:
                    args = {"question": "Назови проверочный ответ", "model": "worker-model"}
                else:
                    tool_message = next(message for message in reversed(request["messages"]) if message.get("role") == "tool")
                    try:
                        answer = json.loads(tool_message["content"])
                    except ValueError:
                        tool_errors.append(tool_message["content"])
                        return None
                    answers.append(answer)
                    if count > 2:
                        return None
                    args = {"question": "Продолжи предыдущий ответ", "chat_id": answer["chat_id"]}
                return [{"index": 0, "id": f"ask_{count}", "type": "function",
                         "function": {"name": "ask_agent", "arguments": json.dumps(args)}}]

            server.tool_plan = plan
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText("test-model")
                tab.key.setText("delegation-fixture-key")
                parent = tab.current
                tab.input.setPlainText("Спроси другую модель и задай ей уточнение в том же чате")
                tab._send()
                wait_for(lambda: parent.get("outcome") in ("completed", "failed"), timeout=40)
                self.assertEqual(parent.get("outcome"), "completed", parent.get("last_error"))
                self.assertEqual(tool_errors, [])
                self.assertEqual(len(answers), 2)
                self.assertEqual(answers[0]["chat_id"], answers[1]["chat_id"])
                self.assertTrue(all(answer["status"] == "completed" and answer["answer"] for answer in answers))
                worker_calls = [item for item in server.requests if item["model"] == "worker-model"]
                self.assertEqual(len(worker_calls), 2)
                self.assertGreater(sum(message["role"] == "user" for message in worker_calls[1]["messages"]), 1)
                self.assertIs(tab.current, parent)
                self.assertEqual(tab.model.currentText(), "test-model")
                self.assertNotIn("delegation-fixture-key", json.dumps(server.requests))
                child = next(chat for chat in tab.store.chats if chat.get("delegated"))
                self.assertEqual(child["model"], "worker-model")
                self.assertEqual(child["outcome"], "completed")
                child_id = child["id"]
                links = list(tab.store.agent_links.values())
                self.assertEqual([link["tool_id"] for link in links], ["ask_1", "ask_2"])
                self.assertTrue(all(link["status"] == "completed" and link["target"] == child_id for link in links))
                tab._open_ai_chat(child_id)
                for link in links:
                    self.assertIn("Вопрос от", tab.output.rows[link["id"] + ":in"].header.text())
                self.assertNotIn("Вопрос агента из чата", tab.output.toPlainText())
                tab._open_ai_chat(parent["id"])
                self.assertEqual(len(tab.chat_list.routes), 1)
                self.assertEqual(tab.chat_list.routes[0]["count"], 2)
                self.assertFalse(tab.chat_list.routes[0]["waiting"])
                self.assertTrue(all(link["id"] + ":out" in tab.output.rows for link in links))
            finally:
                tab.shutdown(); tab.close()
                server.shutdown(); server.server_close()
            restored = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                child = next(chat for chat in restored.store.chats if chat["id"] == child_id)
                self.assertEqual(sum(message.get("role") == "user" for message in child["messages"]), 2)
                self.assertEqual(child["ai_selection"]["model"], "worker-model")
                self.assertEqual(len(restored.store.agent_links), 2)
            finally:
                restored.shutdown(); restored.close()

    def test_real_pi_nested_agents_relay_answers_and_preserve_both_transcript_directions(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QPushButton
        from test_ai_pi import LocalAPI

        with tempfile.TemporaryDirectory() as root:
            server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
            server.requests = []
            received = {}
            release = threading.Event()
            reached_leaf = threading.Event()
            models = ("coordinator-model", "team-model", "worker-model")
            questions = ("Проверь вывод команды", "Посчитай три плюс два")
            leaf_reply = "Исполнитель: 3 + 2 = 5."
            team_reply = "Руководитель: исполнитель подтвердил число 5."
            final_reply = "Координатор: получил число 5 от команды."
            replies = dict(zip(models, (final_reply, team_reply, leaf_reply)))

            def plan(request, _number):
                model = request["model"]
                if model == models[2]:
                    reached_leaf.set()
                    release.wait(30)
                    return None
                tool = next((message for message in reversed(request["messages"]) if message["role"] == "tool"), None)
                if tool:
                    received[model] = tool["content"]
                    return None
                depth = models.index(model)
                args = {"question": questions[depth], "model": models[depth + 1]}
                return [{"index": 0, "id": f"nested_{depth}", "type": "function",
                         "function": {"name": "ask_agent", "arguments": json.dumps(args)}}]

            def click_chat(tab, chat):
                item = tab.chat_list.items[chat["id"]]
                tab.chat_list.scrollToItem(item)
                APP.processEvents()
                rect = tab.chat_list.visualRect(tab.chat_list.indexFromItem(item, 0))
                QTest.mouseClick(tab.chat_list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
                wait_for(lambda: tab.current is chat)
                self.assertIs(tab.current, chat)

            def click_card(tab, message_id, label):
                button = next(button for button in tab.output.rows[message_id].findChildren(QPushButton)
                              if button.text() == label)
                tab.output.ensureWidgetVisible(button)
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                APP.processEvents()

            server.tool_plan = plan
            server.reply_text = lambda request: replies[request["model"]]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            tab.resize(862, 552)
            tab.show()
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText(f"http://127.0.0.1:{server.server_port}/v1")
                tab.model.setEditText(models[0])
                tab.key.setText("nested-fixture-key")
                coordinator = tab.current
                tab.input.setPlainText("Запусти проверку цепочки агентов")
                QTest.mouseClick(tab.send_btn, Qt.MouseButton.LeftButton)
                wait_for(lambda: reached_leaf.is_set() and len(tab.store.agent_links) == 2, timeout=30)

                first, second = tab.store.agent_links.values()
                chats = {chat["id"]: chat for chat in tab.store.chats}
                team, worker = chats[first["target"]], chats[second["target"]]
                self.assertEqual(first["source"], coordinator["id"])
                self.assertEqual(second["source"], team["id"])
                for parent, child in ((coordinator, team), (team, worker)):
                    self.assertEqual(child["parent_chat_id"], parent["id"])
                    self.assertEqual(tab.chat_list.items[child["id"]].parent(), tab.chat_list.items[parent["id"]])
                self.assertEqual([first["status"], second["status"]], ["waiting", "waiting"])
                click_chat(tab, team)
                self.assertEqual(len(tab.chat_list.routes), 2)
                self.assertTrue(all(route["waiting"] for route in tab.chat_list.routes))
                self.assertIn("По запросу", tab.agent_link_status.text())
                self.assertIn("Ждёт ответ от", tab.agent_link_status.text())
                self.assertEqual(tab.output.rows[first["id"] + ":in"].body.toPlainText(), questions[0])
                team_item = tab.chat_list.items[team["id"]]
                worker_index = tab.chat_list.indexFromItem(tab.chat_list.items[worker["id"]], 0)
                team_item.setExpanded(False)
                APP.processEvents()
                self.assertFalse(tab.chat_list.visualRect(worker_index).isValid())
                click_card(tab, second["id"] + ":out", "Открыть ответный чат")
                self.assertIs(tab.current, worker)
                self.assertTrue(team_item.isExpanded())
                self.assertEqual(tab.output.rows[second["id"] + ":in"].body.toPlainText(), questions[1])
                click_card(tab, second["id"] + ":in", "К запросу")
                self.assertIs(tab.current, team)

                release.set()
                wait_for(lambda: coordinator.get("outcome") == "completed", timeout=25)
                self.assertEqual(json.loads(received[models[1]])["answer"], leaf_reply)
                self.assertEqual(json.loads(received[models[0]])["answer"], team_reply)
                self.assertEqual([first["status"], second["status"]], ["completed", "completed"])
                self.assertTrue(all(not route["waiting"] for route in tab.chat_list.routes))
                self.assertFalse(tab.agent_link_status.isVisible())
                self.assertIn(team_reply, tab.output.toPlainText())
                calls = len(server.requests)
            finally:
                release.set()
                tab.shutdown()
                tab.close()
                server.shutdown()
                server.server_close()

            reopened = AITab(data_dir=root, work_dir=self.temp.name)
            reopened.resize(862, 552)
            reopened.show()
            try:
                self.assertEqual(reopened.current["id"], team["id"])
                self.assertEqual(len(reopened.chat_list.routes), 2)
                click_card(reopened, first["id"] + ":in", "К запросу")
                self.assertEqual(reopened.current["id"], coordinator["id"])
                self.assertIn(final_reply, reopened.output.toPlainText())
                click_card(reopened, first["id"] + ":out", "Открыть ответный чат")
                click_card(reopened, second["id"] + ":out", "Открыть ответный чат")
                self.assertEqual(reopened.current["id"], worker["id"])
                self.assertIn(leaf_reply, reopened.output.toPlainText())
                self.assertEqual(len(server.requests), calls)
            finally:
                reopened.shutdown()
                reopened.close()

    def test_agent_questions_reject_busy_self_draft_image_accounts_and_excessive_depth(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                other = tab.store.new_chat(self.temp.name)
                other.update(running=True, ai_selection=dict(parent.chat["ai_selection"]))
                horde = tab.store.ensure_horde_profile()
                for params in ({"chat_id": parent.chat["id"]}, {"chat_id": other["id"]}, {"account": horde["id"]}):
                    with patch.object(parent.rpc, "command") as reply:
                        tab._agent_request({"title": "lanmessenger.ask_agent", "id": "invalid",
                                            "placeholder": json.dumps(dict(params, question="test"))})
                        self.assertIn("error", json.loads(reply.call_args.args[0]["value"]))
                other["running"] = False
                other_run = tab._make_run(other)
                other_run.draft = "сохранить черновик"
                tab._runs[other["id"]] = other_run
                with patch.object(parent.rpc, "command") as reply:
                    tab._agent_request({"title": "lanmessenger.ask_agent", "id": "draft",
                                        "placeholder": json.dumps({"chat_id": other["id"], "question": "test"})})
                    self.assertIn("черновик", json.loads(reply.call_args.args[0]["value"])["error"])
                parent.agent_depth = 2
                with patch.object(parent.rpc, "command") as reply:
                    tab._agent_request({"title": "lanmessenger.ask_agent", "id": "deep", "placeholder": '{"question":"test"}'})
                    self.assertIn("уровней", json.loads(reply.call_args.args[0]["value"])["error"])
                self.assertEqual(parent.agent_requests, {})
                self.assertEqual(tab.store.agent_links, {})
            finally:
                tab.shutdown(); tab.close()

    def test_parent_stop_cancels_owned_text_requests_and_timeout_returns_error(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                parent = tab._run()
                tab.model.setEditText("test-model")
                tab._save_connection()

                def pending(_text):
                    tab._run().pending = "question"

                with patch.object(tab, "_start_text_run", side_effect=pending), patch.object(parent.rpc, "command") as reply:
                    tab._agent_request({"title": "lanmessenger.ask_agent", "id": "timeout", "placeholder": '{"question":"test"}'})
                    child = parent.agent_requests["timeout"]
                    tab._dispatch_run(child, tab._agent_timeout)
                    self.assertEqual(json.loads(reply.call_args.args[0]["value"])["status"], "timeout")
                    self.assertEqual(child.chat["outcome"], "stopped")
                    tab._agent_request({"title": "lanmessenger.ask_agent", "id": "stop", "placeholder": '{"question":"test"}'})
                    child = parent.agent_requests["stop"]
                    with patch.object(parent.rpc, "abort"):
                        tab._stop()
                    self.assertEqual(child.chat["outcome"], "stopped")
                    self.assertIsNone(child.agent_parent_request)
                    self.assertEqual(parent.agent_requests, {})
                    self.assertEqual([link["status"] for link in tab.store.agent_links.values()], ["timeout", "stopped"])
            finally:
                tab.shutdown(); tab.close()

    def test_cancelled_queue_cannot_append_a_late_image(self):
        self.done = False
        with patch("app.core.ai_images.HORDE_URL", self.base):
            self.generator.generate("ai-horde", "", "Dreamshaper", "", "cat", self.temp.name)
            wait_for(lambda: self.check_count > 0)
            self.generator.cancel()
            wait_for(lambda: any(r[0] == "DELETE" for r in self.requests))
        self.assertFalse(self.generator.busy)
        self.assertFalse(self.results)
        self.assertFalse(self.errors)

    def test_cloudflare_flux_uses_account_token_and_decodes_result(self):
        self.payload = {"success": True, "result": {"image": base64.b64encode(self.png).decode()}}
        account = "a" * 32
        with patch("app.core.ai_images.CLOUDFLARE_URL", self.base + "/accounts"):
            self.generator.generate("cloudflare-images", account, CLOUDFLARE_MODEL, "synthetic-key", "cat", self.temp.name)
            wait_for(lambda: self.results or self.errors)
        self.assertFalse(self.errors)
        self.assertEqual(self.requests[0][1], "/accounts/" + account + "/ai/run/" + CLOUDFLARE_MODEL)
        self.assertEqual(self.requests[0][3], {"prompt": "cat", "steps": 4})

    def test_ui_saves_image_and_restores_preview_without_pi(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            tab.show()
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-images"))
                tab.endpoint.setText(self.base + "/v1")
                tab.key.setText("synthetic-key")
                tab.model.setEditText("image-model")
                tab.input.setPlainText("cat")
                with patch("app.ui.ai_tab.rpc_command", side_effect=AssertionError("Images must not start Pi")):
                    tab._send()
                    wait_for(lambda: not tab.images.busy)
                self.assertEqual(tab.current["outcome"], "completed")
                self.assertTrue(tab.current["messages"][-1]["images"])
                self.assertNotIn("synthetic-key", Path(root, "state.json").read_text(encoding="utf-8"))
            finally:
                tab.shutdown()
                tab.close()
            restored = AITab(data_dir=root, work_dir=self.temp.name)
            restored.show()
            APP.processEvents()
            try:
                message = restored.current["messages"][-1]
                preview = restored.output.rows[message["id"]]._image_widgets[0]
                self.assertIsNotNone(preview.path)
                self.assertFalse(preview.pixmap.isNull())
            finally:
                restored.shutdown()
                restored.close()

    def test_unavailable_model_is_explained_without_sending(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=self.temp.name)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-images"))
                tab._api_models = [{"id": "locked", "available": False, "unavailable_reason": "FIRST_TOP_UP_REQUIRED"}]
                tab.model.setEditText("locked")
                tab.input.setPlainText("cat")
                tab._send()
                self.assertIn("первое пополнение", tab.status.text())
                self.assertFalse(self.requests)
            finally:
                tab.shutdown()
                tab.close()


if __name__ == "__main__":
    unittest.main()
