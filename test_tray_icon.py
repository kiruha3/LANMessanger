import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication
from app.ui.app_icon import messenger_icon, unread_badge

APP = QApplication.instance() or QApplication([])


class TrayIconTests(unittest.TestCase):
    def test_message_notifications_do_not_double_count_messages(self):
        notes = [{'kind':'message', 'read':False}, {'kind':'update', 'read':False}, {'kind':'reminder', 'read':True}]
        self.assertEqual(unread_badge({'peer':2, 'muted':1}, notes), (3, 1))
        self.assertEqual(unread_badge({'peer':0}, [{'kind':'update','read':True}]), (0, 0))

    def test_tray_badge_changes_icon_at_small_sizes(self):
        normal = messenger_icon()
        badge = messenger_icon(4)
        for size in (16, 20, 24, 32, 48, 256):
            self.assertFalse(normal.pixmap(size, size).isNull())
            self.assertNotEqual(normal.pixmap(size, size).toImage(), badge.pixmap(size, size).toImage())


if __name__ == '__main__': unittest.main()
