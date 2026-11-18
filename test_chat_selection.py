import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QMenu

from app.core.engine import ChatMessage
from app.ui.chat_window import BubbleDelegate, ChatHistory, ChatPanel, PAD_X, PAD_Y, _layout

APP = QApplication.instance() or QApplication([])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.history = ChatHistory()
        self.history.setItemDelegate(BubbleDelegate(self.history))
        self.history.resize(650, 450)
        self.history.show()
        self.message = ("in", "Собеседник", "Нужный фрагмент текста", time.time(), "received", None, None, "test-id")
        self.item = self.add_message(self.history, self.message)

    def tearDown(self):
        self.history.close()

    @staticmethod
    def add_message(history, message):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, message)
        history.addItem(item)
        APP.processEvents()
        history.doItemsLayout()
        return item

    @staticmethod
    def text_point(history, item, position):
        msg = item.data(Qt.ItemDataRole.UserRole)
        rect = history.visualItemRect(item)
        _, doc, width, _, reply_h = _layout(msg, rect.width())
        block = doc.findBlock(position)
        relative = position - block.position()
        line = block.layout().lineForTextPosition(relative)
        x = line.cursorToX(relative)[0]
        y = doc.documentLayout().blockBoundingRect(block).top() + line.y() + line.height() / 2
        left = rect.right() - width - 8 if msg[0] == "out" else rect.left() + 8
        return QPoint(round(left + PAD_X + x), round(rect.top() + 2 + PAD_Y + reply_h + y))

    @classmethod
    def select_fragment(cls, history, item, fragment, reverse=False):
        _, doc, *_ = _layout(item.data(Qt.ItemDataRole.UserRole), history.visualItemRect(item).width())
        cursor = doc.find(fragment)
        assert not cursor.isNull()
        first = cls.text_point(history, item, cursor.selectionStart())
        last = cls.text_point(history, item, cursor.selectionEnd())
        if reverse:
            first, last = last, first
        QTest.mousePress(history.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(history.viewport(), last)
        QTest.mouseRelease(history.viewport(), Qt.MouseButton.LeftButton, pos=last)
        APP.processEvents()

    def test_mouse_selection_copies_only_the_fragment(self):
        self.select_fragment(self.history, self.item, "фрагмент")
        self.assertEqual(self.history.selected_text(), "фрагмент")
        QTest.keyClick(self.history, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(APP.clipboard().text(), "фрагмент")

    def test_reverse_selection_handles_outgoing_unicode_and_quotes(self):
        message = ("out", "Я", "Привет 👋 нужный фрагмент", time.time(), "read", None, ("Друг", "Цитата"), "unicode-id")
        item = self.add_message(self.history, message)
        self.select_fragment(self.history, item, "нужный", reverse=True)
        self.assertEqual(self.history.selected_text(), "нужный")

    def test_double_click_selects_a_word(self):
        _, doc, *_ = _layout(self.message, self.history.visualItemRect(self.item).width())
        position = doc.find("фрагмент").selectionStart() + 3
        QTest.mouseDClick(self.history.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.text_point(self.history, self.item, position))
        self.assertEqual(self.history.selected_text(), "фрагмент")

    def test_multiline_selection_preserves_line_breaks(self):
        msg = ("in", "Друг", "Первая строка\nВторая строка", time.time(), "received", None, None, "multiline-id")
        item = self.add_message(self.history, msg)
        _, doc, *_ = _layout(msg, self.history.visualItemRect(item).width())
        start = self.text_point(self.history, item, doc.find("Первая").selectionStart())
        end = self.text_point(self.history, item, doc.find("Вторая").selectionEnd())
        QTest.mousePress(self.history.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.history.viewport(), end)
        QTest.mouseRelease(self.history.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.history.selected_text(), "Первая строка\nВторая")

    def test_image_click_is_not_intercepted_by_text_selection(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "image.png")
            image = QImage(40, 40, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.green)
            image.save(str(path))
            with patch("app.ui.chat_window.Engine.img_path", return_value=str(path)):
                msg = ("in", "Друг", "Подпись", time.time(), "received", "image.png", None, "image-id")
                item = self.add_message(self.history, msg)
                _, doc, *_ = _layout(msg, self.history.visualItemRect(item).width())
                position = doc.find("\ufffc").selectionStart()
                point = self.text_point(self.history, item, position) + QPoint(20, 0)
                clicked = QSignalSpy(self.history.itemClicked)
                QTest.mouseClick(self.history.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.assertEqual(len(clicked), 1)
                self.assertEqual(self.history.selected_text(), "")

    def test_refresh_keeps_selection_and_context_menu_copies_it(self):
        engine = MagicMock()
        engine.node_by_key.return_value = None
        engine.tunnel.rdp_port.return_value = None
        engine.chat.return_value = [ChatMessage("id", "in", "Друг", "Нужный фрагмент текста", int(time.time()))]
        panel = ChatPanel(engine)
        panel.resize(800, 450)
        panel.show()
        try:
            panel.set_key("peer")
            APP.processEvents()
            item = panel.history.item(panel.history.count() - 1)
            self.select_fragment(panel.history, item, "фрагмент")
            engine.chat.return_value[0].status = "read"
            panel._refresh()
            APP.processEvents()
            self.assertEqual(panel.history.selected_text(), "фрагмент")
            item = panel.history.item(panel.history.count() - 1)
            with patch.object(QMenu, "exec", lambda menu, _pos: menu.actions()[0]):
                panel._history_menu(panel.history.visualItemRect(item).center())
            self.assertEqual(APP.clipboard().text(), "фрагмент")
            panel.set_key("another-peer")
            self.assertEqual(panel.history.selected_text(), "")
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
