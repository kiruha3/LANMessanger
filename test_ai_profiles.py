import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from app.core.ai import AIStore, KeyVault, auth_helper_path, process_environment, runtime_command
from app.core.ai_images import HORDE_MODEL, HORDE_URL
from app.core.ai_timeline import Timeline
from app.ui.ai_tab import AITab

APP = QApplication.instance() or QApplication([])


class ProfileTests(unittest.TestCase):
    def test_image_tool_account_and_model_are_independent_per_text_account_and_restore(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.model.setEditText("text-model")
                first_chat = tab.current
                pictures = tab.store.add_profile("Запасной генератор")
                pictures["config"].update(provider="lanmessenger-images", endpoint="https://images.example/v1", model="image-default")
                pictures["api_models"] = [{"id": "image-default"}, {"id": "image-alternative"}]
                tab._fill_accounts()
                tab.image_tool_account.setCurrentIndex(tab.image_tool_account.findData(pictures["id"]))
                self.assertEqual(tab.image_tool_model.currentText(), "")
                self.assertGreaterEqual(tab.image_tool_model.findText("image-alternative"), 0)
                tab.image_tool_model.setEditText("image-alternative")
                tab._persist_connection()
                first_config = dict(tab.store.config)
                other = tab.store.add_profile("Другой текстовый аккаунт")
                tab._fill_accounts()
                tab._new_chat()
                tab.account.setCurrentIndex(tab.account.findData(other["id"]))
                self.assertEqual(tab.image_tool_account.currentData(), "")
                self.assertEqual(tab.image_tool_model.currentText(), "")
                tab.image_tool_model.setEditText("different-horde-model")
                tab._persist_connection()
                tab._select_chat(tab.store.chats.index(first_chat))
                self.assertEqual(tab.image_tool_account.currentData(), pictures["id"])
                self.assertEqual(tab.image_tool_model.currentText(), "image-alternative")
                self.assertEqual(tab.store.config["image_account"], first_config["image_account"])
                self.assertEqual(pictures["config"]["model"], "image-default")
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.image_tool_account.currentData(), pictures["id"])
                self.assertEqual(restored.image_tool_model.currentText(), "image-alternative")
                self.assertEqual(restored.model.currentText(), "text-model")
            finally:
                restored.shutdown(); restored.close()

    def test_missing_image_tool_account_does_not_silently_fall_back_to_horde(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.store.config["image_account"] = "unavailable-image-account"
                tab._fill_image_tool_accounts()
                tab._persist_connection()
                self.assertEqual(tab.image_tool_account.currentData(), "unavailable-image-account")
                self.assertIn("недоступен", tab.image_tool_account.currentText())
                tab._run().config = dict(tab.store.config)
                tab._run().image_connections = tab._image_connections()
                self.assertFalse(any(c["default"] for c in tab._run().image_connections))
                with patch.object(tab.rpc, "command") as reply:
                    tab._agent_image_request({"id": "missing", "placeholder": json.dumps({"prompt": "cat"})})
                    result = json.loads(reply.call_args.args[0]["value"])
                    self.assertIn("image gen — аккаунт", result["error"])
                self.assertEqual(len(tab.store.chats), 1)
            finally:
                tab.shutdown(); tab.close()

    def test_chats_restore_different_models_of_the_same_account_before_debounce_and_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                first = tab.current
                tab.model.setEditText("first-chat-model")
                tab._new_chat()
                second = tab.current
                self.assertEqual(tab.model.currentText(), "first-chat-model")
                tab.model.setEditText("second-chat-model")
                tab._select_chat(tab.store.chats.index(first))
                self.assertEqual(tab.model.currentText(), "first-chat-model")
                tab._select_chat(tab.store.chats.index(second))
                self.assertEqual(tab.model.currentText(), "second-chat-model")
                tab._select_chat(tab.store.chats.index(first))
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.current["id"], first["id"])
                self.assertEqual(restored.model.currentText(), "first-chat-model")
                restored._select_chat(next(i for i, c in enumerate(restored.store.chats) if c["id"] == second["id"]))
                self.assertEqual(restored.model.currentText(), "second-chat-model")
            finally:
                restored.shutdown(); restored.close()

    def test_chats_restore_text_and_image_accounts_with_their_own_keys_and_models(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                text_chat = tab.current
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.key.setText("session-only-text-key")
                tab.model.setEditText("text-chat-model")
                tab._new_chat()
                image_chat = tab.current
                tab._select_free_images()
                image_profile = tab.store.active_profile
                self.assertEqual(tab.provider.currentData(), "ai-horde")
                self.assertEqual(tab.key.text(), "")
                tab._select_chat(tab.store.chats.index(text_chat))
                self.assertEqual(tab.store.active_profile, "default")
                self.assertEqual(tab.provider.currentData(), "lanmessenger-api")
                self.assertEqual(tab.model.currentText(), "text-chat-model")
                self.assertEqual(tab.key.text(), "session-only-text-key")
                tab._select_chat(tab.store.chats.index(image_chat))
                self.assertEqual(tab.store.active_profile, image_profile)
                self.assertEqual(tab.model.currentText(), HORDE_MODEL)
                self.assertEqual(tab.key.text(), "")
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.store.active_profile, image_profile)
                self.assertEqual(restored.model.currentText(), HORDE_MODEL)
                restored._select_chat(next(i for i, c in enumerate(restored.store.chats) if c["id"] == text_chat["id"]))
                self.assertEqual(restored.provider.currentData(), "lanmessenger-api")
                self.assertEqual(restored.model.currentText(), "text-chat-model")
                self.assertEqual(restored.key.text(), "")
            finally:
                restored.shutdown(); restored.close()

    def test_legacy_chat_adopts_current_selection_and_model_is_remembered_after_editing(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            store.config["model"] = "legacy-model"
            chat = store.new_chat(root)
            chat.pop("ai_selection")
            store.save()
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(tab.model.currentText(), "legacy-model")
                tab.model.setEditText("new-model")
            finally:
                tab.shutdown(); tab.close()
            saved = AIStore(root).chats[0]
            self.assertEqual(saved["ai_selection"], {"profile_id": "default", "model": "new-model"})

    def test_legacy_chat_restores_last_request_account_and_model(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            horde = store.ensure_horde_profile()
            chat = store.new_chat(root)
            chat.pop("ai_selection")
            chat.update(profile_id=horde["id"], model=HORDE_MODEL)
            store.save()
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(tab.store.active_profile, horde["id"])
                self.assertEqual(tab.provider.currentData(), "ai-horde")
                self.assertEqual(tab.model.currentText(), HORDE_MODEL)
            finally:
                tab.shutdown(); tab.close()

    def test_fresh_install_includes_ready_horde_without_credentials_or_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            horde = next(p for p in store.profiles if p["config"]["provider"] == "ai-horde")
            self.assertEqual(horde["config"]["model"], HORDE_MODEL)
            self.assertEqual(horde["config"]["endpoint"], HORDE_URL)
            self.assertEqual(horde["api_models"][0]["id"], HORDE_MODEL)
            self.assertEqual(store.active_profile, "default")
            store.select_profile(horde["id"])
            self.assertEqual(KeyVault(store.profile_root()).load(), {})
            store.save()
            restored = AIStore(root)
            self.assertEqual(restored.config["provider"], "ai-horde")
            self.assertEqual(len([p for p in restored.profiles if p["config"]["provider"] == "ai-horde"]), 1)

    def test_upgrade_adds_horde_without_changing_existing_selection_or_custom_profile(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "state.json")
            path.write_text(json.dumps({"config": {"provider": "openai", "model": "legacy-model"}, "chats": []}))
            store = AIStore(root)
            self.assertEqual(store.config["model"], "legacy-model")
            horde = store.ensure_horde_profile()
            store.select_profile(horde["id"])
            horde["name"] = "Мои картинки"
            store.config["model"] = "custom-horde-model"
            store.save()
            restored = AIStore(root)
            self.assertEqual(restored.config["model"], "custom-horde-model")
            self.assertEqual(restored.ensure_horde_profile()["name"], "Мои картинки")
            self.assertEqual(len(restored.profiles), 2)

    def test_free_image_shortcut_selects_preconfigured_account_without_a_request(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.show()
            try:
                with patch.object(tab.api_catalog, "fetch") as fetch, patch.object(tab.images, "generate") as generate:
                    tab.output.welcome.free_image_btn.click()
                    self.assertEqual(tab.provider.currentData(), "ai-horde")
                    self.assertEqual(tab.model.currentText(), HORDE_MODEL)
                    self.assertEqual(tab.key.text(), "")
                    self.assertFalse(tab.install_panel.isVisible())
                    self.assertTrue(tab.send_btn.isEnabled())
                    self.assertEqual(tab.input.toPlainText(), "")
                    fetch.assert_not_called()
                    generate.assert_not_called()
            finally:
                tab.shutdown()
                tab.close()

    def test_legacy_connection_and_credentials_keep_their_paths(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root, "state.json")
            state.write_text(json.dumps({"config": {"provider": "openai", "model": "legacy-model"}, "chats": []}))
            store = AIStore(root)
            self.assertEqual(store.active_profile, "default")
            self.assertEqual(store.profile_root(), Path(root))
            self.assertEqual(store.config["model"], "legacy-model")
            store.save()
            self.assertEqual(AIStore(root).config["model"], "legacy-model")

    def test_profiles_have_independent_models_and_auth_directories(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            store.config.update(provider="openai-codex", model="first-model")
            first_root = store.profile_root()
            second = store.add_profile("Рабочий аккаунт")
            store.select_profile(second["id"])
            second_root = store.profile_root()
            store.config["model"] = "second-model"
            store.save()
            self.assertNotEqual(first_root, second_root)
            restored = AIStore(root)
            self.assertEqual(restored.active_profile, second["id"])
            self.assertEqual(restored.config["model"], "second-model")
            restored.select_profile("default")
            self.assertEqual(restored.config["model"], "first-model")

    def test_account_selection_restores_models_and_session_only_keys(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.provider.setCurrentIndex(tab.provider.findData("lanmessenger-api"))
                tab.endpoint.setText("http://127.0.0.1:30001/v1")
                tab.key.setText("first-test-key")
                tab.model.setEditText("first-model")
                tab._api_models = [{"id": "first-model", "owner": "first"}]
                with patch("app.ui.ai_tab.QInputDialog.getText", return_value=("Рабочий", True)):
                    tab.account_add.click()
                second = tab.store.active_profile
                self.assertNotEqual(second, "default")
                self.assertEqual(tab.key.text(), "")
                tab.endpoint.setText("http://127.0.0.1:30002/v1")
                tab.key.setText("second-test-key")
                tab.model.setEditText("second-model")
                tab._api_models = [{"id": "second-model", "owner": "second"}]
                tab.account.setCurrentIndex(tab.account.findData("default"))
                self.assertEqual(tab.model.currentText(), "first-model")
                self.assertEqual(tab.key.text(), "first-test-key")
                self.assertEqual(tab._api_models, [{"id": "first-model", "owner": "first"}])
                tab.account.setCurrentIndex(tab.account.findData(second))
                self.assertEqual(tab.key.text(), "second-test-key")
                self.assertEqual(tab.model.currentText(), "second-model")
                tab._controls(True)
                self.assertFalse(tab.account.isEnabled())
                self.assertFalse(tab.model.isEnabled())
            finally:
                tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.store.active_profile, second)
                self.assertEqual(restored.model.currentText(), "second-model")
                self.assertEqual(restored.key.text(), "")
                self.assertEqual(restored._api_models, [{"id": "second-model", "owner": "second"}])
                self.assertGreater(restored.model.count(), 0)
            finally:
                restored.shutdown(); restored.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_saved_keys_do_not_cross_account_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            KeyVault(store.profile_root()).save("openai", "first-fake-key")
            second = store.add_profile("Second")
            store.select_profile(second["id"])
            vault = KeyVault(store.profile_root())
            self.assertEqual(vault.load(), {})
            vault.save("openai", "second-fake-key")
            vault.clear()
            store.select_profile("default")
            self.assertEqual(KeyVault(store.profile_root()).load()["key"], "first-fake-key")

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_saved_keys_restore_in_the_ui_and_empty_field_removes_saved_key(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            tab.provider.setCurrentIndex(tab.provider.findData("openai"))
            tab.key.setText("first-fake-key")
            tab.remember.setChecked(True)
            with patch("app.ui.ai_tab.QInputDialog.getText", return_value=("Second", True)):
                tab.account_add.click()
            second = tab.store.active_profile
            tab.key.setText("second-fake-key")
            tab.remember.setChecked(True)
            tab.shutdown(); tab.close()
            restored = AITab(data_dir=root, work_dir=root)
            try:
                self.assertEqual(restored.key.text(), "second-fake-key")
                restored.account.setCurrentIndex(restored.account.findData("default"))
                self.assertEqual(restored.key.text(), "first-fake-key")
                restored.key.clear()
                restored._save_connection()
                self.assertEqual(restored.vault.load(), {})
                restored.account.setCurrentIndex(restored.account.findData(second))
                self.assertEqual(restored.key.text(), "second-fake-key")
            finally:
                restored.shutdown(); restored.close()

    def test_oauth_helper_uses_only_selected_profile_storage(self):
        try:
            node, _args, module = runtime_command()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as root:
            store = AIStore(root)
            first = store.profile_root() / "pi"
            first.mkdir()
            credentials = {"openai-codex": {"type": "oauth", "access": "fake-access", "refresh": "fake-refresh", "expires": 9999999999999}}
            path = first / "auth.json"
            path.write_text(json.dumps(credentials))
            second = store.add_profile("Second")
            store.select_profile(second["id"])
            other = store.profile_root() / "pi"
            other.mkdir()
            for agent, expected in ((first, ["openai-codex"]), (other, [])):
                environment = process_environment({"PI_CODING_AGENT_DIR": str(agent)})
                env = dict(entry.split("=", 1) for entry in environment.toStringList())
                result = subprocess.run([node, str(auth_helper_path()), str(module), str(agent)],
                                        input=json.dumps({"type": "list", "provider": "openai-codex"}) + "\n",
                                        capture_output=True, text=True, encoding="utf-8", env=env, cwd=root, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                catalog = json.loads(result.stdout.strip())
                self.assertEqual(catalog["authenticated"], expected)
                self.assertTrue(all(m["provider"] == "openai-codex" for m in catalog["models"]))
            self.assertEqual(json.loads(path.read_text()), credentials)

    def test_ambient_keys_and_node_preloads_are_not_inherited(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "ambient-fake-key", "GH_TOKEN": "ambient-fake-token", "NODE_OPTIONS": "--import=unsafe.mjs"}):
            environment = process_environment({"ANTHROPIC_API_KEY": "selected-fake-key"})
            self.assertFalse(environment.contains("OPENAI_API_KEY"))
            self.assertFalse(environment.contains("GH_TOKEN"))
            self.assertFalse(environment.contains("NODE_OPTIONS"))
            self.assertEqual(environment.value("ANTHROPIC_API_KEY"), "selected-fake-key")
            env = dict(entry.split("=", 1) for entry in environment.toStringList())
            result = subprocess.run([sys.executable, "-c", 'import os,json;print(json.dumps([os.getenv("OPENAI_API_KEY"),os.getenv("GH_TOKEN"),os.getenv("NODE_OPTIONS"),os.getenv("ANTHROPIC_API_KEY")]))'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(json.loads(result.stdout), [None, None, None, "selected-fake-key"])

    def test_lost_session_keeps_history_across_reconciliation_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.current["messages"] = [{"role": "user", "text": "old request"}, {"role": "assistant", "text": "old answer"}]
                tab.timeline = Timeline(tab.current["messages"], tab.rpc.redact)
                tab._event({"type": "response", "command": "get_messages", "success": True, "data": {"messages": []}})
                tab._event({"type": "response", "command": "get_messages", "success": True, "data": {"messages": [{"role": "user", "content": "new request"}]}})
                self.assertEqual(sum(m.get("text") == "old answer" for m in tab.current["messages"]), 1)
                self.assertEqual(tab.current["messages"][-1]["text"], "new request")
            finally:
                tab.shutdown(); tab.close()
            stored = AIStore(root)
            self.assertTrue(any(m.get("text") == "old answer" for m in stored.chats[0]["messages"]))

    def test_provider_error_is_saved_as_failure(self):
        with tempfile.TemporaryDirectory() as root:
            tab = AITab(data_dir=root, work_dir=root)
            try:
                tab.current["running"] = True
                tab._event({"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "errorMessage": "401 test key rejected", "content": []}})
                tab._event({"type": "agent_end"})
                self.assertEqual(tab.current["outcome"], "failed")
                self.assertTrue(tab.current["interrupted"])
                self.assertIn("401", tab.status.text())
                self.assertNotIn("Задача завершена", tab.status.text())
            finally:
                tab.shutdown(); tab.close()
            self.assertEqual(AIStore(root).chats[0]["outcome"], "failed")

    def test_incomplete_chat_schema_is_reported_without_overwriting(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "state.json")
            text = json.dumps({"config": {}, "chats": [{"id": "broken", "messages": []}]})
            path.write_text(text)
            tab = AITab(data_dir=root, work_dir=root)
            try:
                self.assertIsNone(tab.store)
                self.assertFalse(tab.send_btn.isEnabled())
                self.assertEqual(path.read_text(), text)
            finally:
                tab.shutdown(); tab.close()


if __name__ == "__main__":
    unittest.main()
