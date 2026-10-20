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
