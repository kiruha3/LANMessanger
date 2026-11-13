"""Native incremental chat rows; expandable activity follows bb's timeline UI.

Adapted presentation: Copyright (c) 2026 Michael Yong, MIT (licenses/bb-MIT.txt).
"""
import json
import math
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from . import theme


STATUS = {"queued": "Ожидает", "running": "Выполняется…", "completed": "Завершено",
          "failed": "Ошибка", "stopped": "Остановлено", "interrupted": "Прервано"}
TOOL_NAMES = {"bash": "Команда", "read": "Чтение", "write": "Запись", "edit": "Изменение",
              "grep": "Поиск", "find": "Поиск файлов", "ls": "Список файлов"}


class MessageBody(QTextBrowser):
    def __init__(self, folder, compact=False, parent=None):
        super().__init__(parent)
        self.compact = compact
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.document().setBaseUrl(QUrl.fromLocalFile(str(Path(folder).resolve()) + "/"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if compact else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        colors = theme.bubbles()
        self.setStyleSheet(f"QTextBrowser {{ background: transparent; color: {colors['text']}; border: none; }}")
        self.document().setDefaultStyleSheet(
            f"body {{ color: {colors['text']}; }} a {{ color: {colors['accent']}; }} "
            "pre, code { font-family: Consolas, monospace; } pre { white-space: pre-wrap; }")

    def loadResource(self, resource_type, name):
        # The conversation contains text/code, not automatically loaded remote or local images.
        if resource_type == QTextDocument.ResourceType.ImageResource:
            return None
        return super().loadResource(resource_type, name)

    def _open_link(self, url):
        resolved = self.document().baseUrl().resolved(url)
        if resolved.scheme().lower() in ("http", "https", "file"):
            QDesktopServices.openUrl(resolved)

    def set_content(self, text, markdown=False):
        if markdown:
            self.document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                                        | QTextDocument.MarkdownFeature.MarkdownNoHTML)
            # Qt's Markdown importer uses a fixed blue for links, unreadable on a dark background.
            block = self.document().begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isAnchor():
                        cursor = QTextCursor(self.document())
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                        format_ = QTextCharFormat()
                        format_.setForeground(QColor(theme.bubbles()["accent"]))
                        cursor.mergeCharFormat(format_)
                    iterator += 1
                block = block.next()
        else:
            self.setPlainText(text)
        self._fit_height()
        if self.compact:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _fit_height(self):
        self.document().setTextWidth(max(40, self.viewport().width()))
        height = math.ceil(self.document().size().height()) + 8
        self.setFixedHeight(max(32, min(height, 320) if self.compact else height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class TranscriptRow(QFrame):
    expansion_changed = pyqtSignal()

    def __init__(self, message, folder, parent=None):
        super().__init__(parent)
        self.role = message.get("role", "system")
        self._snapshot = None
        self._manual_expansion = None
        self.expandable = self.role in ("tool", "thinking")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        colors = theme.bubbles()
        if self.expandable:
            self.header = QToolButton()
            self.header.setCheckable(True)
            self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.header.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.header.setStyleSheet(f"QToolButton {{ border: none; text-align: left; color: {colors['ts']}; padding: 3px; }}")
            self.header.toggled.connect(self._toggle)
            self.setStyleSheet(f"TranscriptRow {{ border: 1px solid {colors['in']}; border-radius: 6px; }}")
        else:
            self.header = QLabel({"user": "Вы", "assistant": "Агент", "system": "Состояние"}.get(self.role, "Сообщение"))
            self.header.setTextFormat(Qt.TextFormat.PlainText)
            self.header.setStyleSheet(f"color: {colors['accent']}; font-weight: bold;")
            if self.role == "user":
                self.setStyleSheet(f"TranscriptRow {{ background: {colors['out']}; border-radius: 6px; }}")
        layout.addWidget(self.header)
        self.body = MessageBody(folder, compact=self.expandable, parent=self)
        layout.addWidget(self.body)
        self.update_message(message)

    def _toggle(self, expanded):
        self._manual_expansion = expanded
        self.expansion_changed.emit()
        self.body.setVisible(expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def update_message(self, message):
        snapshot = json.dumps(message, ensure_ascii=False, sort_keys=True)
        if snapshot == self._snapshot:
            return False
        self._snapshot = snapshot
        text = message.get("text", "")
        if self.expandable:
            status = message.get("status", "completed")
            if self.role == "thinking":
                title = "Размышления"
            else:
                name = message.get("tool_name") or text.split("\n", 1)[0] or "Инструмент"
                args = message.get("args", {})
                target = (args.get("command") or args.get("path") or args.get("pattern") or "") if isinstance(args, dict) else ""
                target = str(target).replace("\n", " ")
                title = TOOL_NAMES.get(name, name) + (f": {target[:100]}" if target else "")
                text += "\n\n" + message.get("result", "")
            self.header.setText(f"{title} · {STATUS.get(status, status)}")
            self.header.setToolTip(title)
            colors = theme.bubbles()
            color = "#ef4444" if status == "failed" else colors["accent"] if status == "running" else colors["ts"]
            self.header.setStyleSheet(f"QToolButton {{ border: none; text-align: left; color: {color}; padding: 3px; }}")
            expanded = self._manual_expansion
            if expanded is None:
                expanded = self.role == "thinking" and status == "running"
            self.header.blockSignals(True)
            self.header.setChecked(expanded)
            self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
            self.header.blockSignals(False)
            self.body.setVisible(expanded)
        self.body.set_content(text, markdown=self.role == "assistant")
        return True


class AITranscript(QScrollArea):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.rows = {}
        self._order = []
        self._follow_end = True
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(8)
        self.layout.addStretch(1)
        self.setWidget(self.container)
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)

    def _range_changed(self, _minimum, maximum):
        if self._follow_end:
            self.verticalScrollBar().setValue(maximum)

    def _expanding(self):
        self._follow_end = False

    def _scroll_to_end(self):
        if self._follow_end:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def set_messages(self, messages, scroll_to_end=True):
        visible = [message for message in messages
                   if message.get("role") == "tool" or message.get("text")]
        order = [message["id"] for message in visible]
        changed = order != self._order
        for key in set(self.rows) - set(order):
            row = self.rows.pop(key)
            self.layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for index, message in enumerate(visible):
            key = message["id"]
            row = self.rows.get(key)
            if row is None:
                row = TranscriptRow(message, self.folder)
                row.expansion_changed.connect(self._expanding)
                self.rows[key] = row
            else:
                changed = row.update_message(message) or changed
            if order != self._order:
                self.layout.insertWidget(index, row)
        self._order = order
        if changed and scroll_to_end:
            self._follow_end = True
            self.layout.activate()
            self._scroll_to_end()
            QTimer.singleShot(0, self._scroll_to_end)

    def toPlainText(self):
        return "\n\n".join(self.rows[key].body.toPlainText() for key in self._order)

    def toHtml(self):
        return "\n".join(self.rows[key].body.toHtml() for key in self._order)
