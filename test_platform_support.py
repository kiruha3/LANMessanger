"""Platform boundaries: preserve portable Windows data and isolate the Mac bundle."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.core import engine as engine_module
from app.core.engine import ChatMessage, Engine
from app.core.history import History, base_dir
from app.core.tunnel import TunnelManager
from app.ui.settings_dialog import SettingsDialog, set_shadow_policy


@contextmanager
def engine_in(work, platform):
    """Use real settings/SQLite without networking or the user's working data."""
    settings = work / "settings.json"
    settings.write_text(json.dumps({
        "tls_enabled": False,
        "rdp_enabled": True,
        "scan_enabled": True,
        "shadow_rdp": True,
    }), encoding="utf-8")
    previous = os.getcwd()
    os.chdir(work)
    try:
        with patch.object(sys, "platform", platform), \
                patch.object(engine_module, "SETTINGS_FILE", str(settings)):
            engine = Engine("Platform test")
            try:
                yield engine
            finally:
                if engine.history:
                    engine.history._db.close()
    finally:
        os.chdir(previous)


class StorageTests(unittest.TestCase):
    def test_windows_frozen_keeps_database_next_to_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.object(sys, "platform", "win32"), \
                    patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", str(folder / "LANMessenger.exe")):
                self.assertEqual(Path(base_dir()), folder)
                history = History()
                self.assertEqual(Path(history.path), folder / "history.db")
                history._db.close()

    def test_mac_frozen_history_survives_restart_outside_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bundle = home / "LANMessenger.app" / "Contents" / "MacOS"
            bundle.mkdir(parents=True)
            expected = home / "Library" / "Application Support" / "LANMessenger"
            with patch.object(sys, "platform", "darwin"), \
                    patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", str(bundle / "LANMessenger")), \
                    patch("app.core.history.os.path.expanduser", return_value=str(home)):
                first = History()
                first.add("peer", ChatMessage("saved", "in", "Alice", "keep me", 1))
                first._db.close()
                second = History()
                try:
                    self.assertEqual(Path(second.path), expected / "history.db")
                    self.assertEqual(second.load("peer")[0][3], "keep me")
                    self.assertEqual(list(bundle.iterdir()), [])
                finally:
                    second._db.close()

    def test_mac_source_keeps_test_data_in_working_directory(self):
        with patch.object(sys, "platform", "darwin"), \
                patch.object(sys, "frozen", False, create=True):
            self.assertEqual(base_dir(), os.getcwd())


class PlatformUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_restore_from_tray_setting_is_opt_in_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp),'win32') as engine:
            self.assertFalse(engine.open_on_message)
            dialog=SettingsDialog(None,engine)
            try:
                dialog.open_message_box.setChecked(True)
                with patch('app.ui.settings_dialog.autostart.enable'), patch('app.ui.settings_dialog.autostart.disable'), patch('app.ui.settings_dialog.theme.apply'):
                    dialog._apply()
                engine.open_on_message=False
                engine.load_settings()
                self.assertTrue(engine.open_on_message)
            finally:
                dialog.close()

    def test_restore_from_tray_on_message_respects_setting_and_mute(self):
        from app.ui.main_window import MainWindow
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp),'win32') as engine:
            window=MainWindow(engine)
            key='127.0.0.1:45678'
            engine.chats[key]=[ChatMessage('incoming','in','Peer','hello',1)]
            engine.unread[key]=1
            try:
                window.hide()
                with patch.object(window,'_show_from_tray') as show, patch.object(window,'select_chat') as select, patch('app.ui.main_window.QApplication.beep'):
                    window._on_new_message(key)
                    show.assert_not_called()
                    engine.open_on_message=True
                    window._on_new_message(key)
                    show.assert_called_once(); select.assert_called_once_with(key)
                    show.reset_mock(); select.reset_mock()
                    window.show()
                    window._on_new_message(key)
                    show.assert_not_called(); select.assert_not_called()
                    window.showMinimized()
                    window._on_new_message(key)
                    show.assert_called_once(); select.assert_called_once_with(key)
                    show.reset_mock(); select.reset_mock()
                    window.hide()
                    window.mute_action.setChecked(True)
                    window._on_new_message(key)
                    show.assert_not_called()
                    window.mute_action.setChecked(False)
                    engine.muted_chats.add(key)
                    window._on_new_message(key)
                    show.assert_not_called()
            finally:
                window.ai_tab.shutdown()
                window._quitting=True; window.tray.hide(); window.close()

    def test_general_settings_have_scrollable_zones_and_fixed_ok_button(self):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QScrollArea
        from app.ui import theme
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp), "win32") as engine:
            engine.known_fingerprints = {f"peer-{i}": "abcdef" * 12 for i in range(50)}
            dialog = SettingsDialog(None, engine)
            try:
                dialog.setFont(QFont("Segoe UI", 10))
                dialog.setStyleSheet(theme.LIGHT_QSS)
                dialog.resize(480, 360)
                dialog.show()
                for _ in range(10):
                    self.app.processEvents()
                self.assertEqual([dialog.tabs.tabText(i) for i in range(dialog.tabs.count())],
                                 ["Общие", "Сеть", "Защита", "RDP", "Уведомления"])
                self.assertLessEqual(dialog.height(), 360)
                self.assertLessEqual(dialog.width(), 480)
                self.assertLessEqual(dialog.buttons.mapTo(dialog, QPoint()).y() + dialog.buttons.height(), dialog.height())
                for index in range(dialog.tabs.count()):
                    dialog.tabs.setCurrentIndex(index)
                    for _ in range(6):
                        self.app.processEvents()
                    page = dialog.tabs.widget(index)
                    self.assertIsInstance(page, QScrollArea)
                    self.assertEqual(page.horizontalScrollBar().maximum(), 0, dialog.tabs.tabText(index))
                    if index == 2:
                        self.assertGreater(page.verticalScrollBar().maximum(), 0)
                    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                    self.assertEqual(page.verticalScrollBar().value(), page.verticalScrollBar().maximum())
                dialog.tabs.setCurrentIndex(3)
                for _ in range(6):
                    self.app.processEvents()
                self.assertGreaterEqual(dialog.shadow_box.height(), dialog.shadow_box.heightForWidth(dialog.shadow_box.width()))
            finally:
                dialog.close()

    def test_settings_keep_edits_when_switching_zones_and_apply_all_tabs(self):
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp), "win32") as engine:
            dialog = SettingsDialog(None, engine)
            try:
                dialog.tabs.setCurrentIndex(1)
                dialog.manual_ip_box.setChecked(True)
                dialog.tabs.setCurrentIndex(2)
                dialog.psk_edit.setText("synthetic-network-password")
                dialog.tabs.setCurrentIndex(4)
                dialog.url_edit.setText("https://example.invalid/releases")
                dialog.tabs.setCurrentIndex(0)
                self.assertTrue(dialog.manual_ip_box.isChecked())
                self.assertEqual(dialog.psk_edit.text(), "synthetic-network-password")
                with patch("app.ui.settings_dialog.autostart.enable"), patch("app.ui.settings_dialog.autostart.disable"), patch("app.ui.settings_dialog.theme.apply"):
                    dialog._apply()
                self.assertTrue(engine.manual_ip_enabled)
                self.assertEqual(engine.network_psk, "synthetic-network-password")
                self.assertEqual(engine.update_url, "https://example.invalid/releases")
            finally:
                dialog.close()

    def test_mac_ignores_saved_windows_features_and_disables_controls(self):
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp), "darwin") as engine:
            for feature in ("rdp_enabled", "shadow_rdp", "scan_enabled"):
                engine.set_feature(feature, True)
                self.assertFalse(getattr(engine, feature), feature)
            self.assertFalse(engine.tunnel.enabled)
            dialog = SettingsDialog(None, engine)
            try:
                for control in (dialog.autostart_box, dialog.rdp_box, dialog.shadow_box,
                                dialog.shadow_btn, dialog.scan_box):
                    self.assertFalse(control.isEnabled())
                self.assertFalse(set_shadow_policy(True))
            finally:
                dialog.close()

    def test_windows_features_and_controls_remain_available(self):
        with tempfile.TemporaryDirectory() as tmp, engine_in(Path(tmp), "win32") as engine:
            for feature in ("rdp_enabled", "shadow_rdp", "scan_enabled"):
                self.assertTrue(getattr(engine, feature), feature)
            self.assertTrue(engine.tunnel.enabled)
            dialog = SettingsDialog(None, engine)
            try:
                for control in (dialog.autostart_box, dialog.rdp_box, dialog.shadow_box,
                                dialog.shadow_btn, dialog.scan_box):
                    self.assertTrue(control.isEnabled())
            finally:
                dialog.close()


class SessionTests(unittest.TestCase):
    def test_mac_replies_to_windows_shadow_request_instead_of_crashing(self):
        class Peer:
            def send_packet(self, packet_type, **fields):
                self.reply = (packet_type, fields)

        class Connections:
            on_stream = None

        peer = Peer()
        tunnel = TunnelManager(Connections())
        with patch.object(sys, "platform", "darwin"):
            tunnel._respond_session_query({"id": "query"}, peer)
        self.assertEqual(peer.reply[0], "stream_session")
        self.assertFalse(peer.reply[1]["ok"])
        self.assertEqual(peer.reply[1]["id"], "query")


if __name__ == "__main__":
    unittest.main()
