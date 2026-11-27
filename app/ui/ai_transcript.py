"""Native incremental chat rows; expandable activity follows bb's timeline UI.

Adapted presentation: Copyright (c) 2026 Michael Yong, MIT (licenses/bb-MIT.txt).
"""
import json
import math
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPixmap, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from ..core.ai_images import image_bytes

from . import theme
from .ai_welcome import AIWelcome


STATUS = {"queued": "Ожидает", "running": "Выполняется…", "completed": "Завершено",
          "failed": "Ошибка", "stopped": "Остановлено", "interrupted": "Прервано"}
IMAGE_STATUS = {"submitting": "Отправка", "queued": "В очереди", "processing": "Генерируется",
                "completed": "Готово", "failed": "Ошибка", "stopped": "Ожидание остановлено",
                "cancelled": "Отменено", "interrupted": "Проверить результат"}
TOOL_NAMES = {"bash": "Команда", "read": "Чтение", "write": "Запись", "edit": "Изменение",
              "grep": "Поиск", "find": "Поиск файлов", "ls": "Список файлов",
              "generate_image": "Запрос на генерацию", "get_image_status": "Проверка очереди",
              "ask_agent": "Вопрос другому агенту", "read_chat": "Чтение ИИ-чата",
              "list_chats": "Поиск ИИ-чатов", "list_image_models": "Модели изображений",
              "cancel_image_generation": "Запрос отмены генерации", "list_agent_models": "Модели агентов"}


class MessageBody(QTextBrowser):
    def __init__(self, folder, parent=None, allow_local_links=True):
        super().__init__(parent)
        self.allow_local_links = allow_local_links
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.document().setBaseUrl(QUrl.fromLocalFile(str(Path(folder).resolve()) + "/"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        scheme = resolved.scheme().lower()
        if scheme in ("http", "https") or (scheme == "file" and self.allow_local_links):
            QDesktopServices.openUrl(resolved)

    def set_content(self, text, markdown=False):
        # Streaming providers can emit long whitespace tails; keep the raw transcript intact.
        text = text.rstrip()
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

    def _fit_height(self):
        self.document().setTextWidth(max(40, self.viewport().width()))
        height = math.ceil(self.document().size().height()) + 8
        self.setFixedHeight(max(32, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def wheelEvent(self, event):
        # The message fits its document; let the enclosing transcript handle the wheel.
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                QApplication.sendEvent(parent.viewport(), event)
                return
            parent = parent.parentWidget()
        event.ignore()


class GeneratedImage(QWidget):
    def __init__(self, path, image_root, parent=None):
        super().__init__(parent)
        self.path = None
        self.pixmap = QPixmap()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.preview)
        try:
            candidate = Path(path).resolve()
            if image_root is None or not candidate.is_relative_to(Path(image_root).resolve()) or candidate.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("Недопустимый путь изображения")
            data = candidate.read_bytes()
            image_bytes(data)
            self.pixmap.loadFromData(data)
            self.path = candidate
        except (OSError, ValueError, TypeError):
            self.preview.setText("Изображение недоступно. Возможно, файл был перемещён или удалён.")
            self.preview.setWordWrap(True)
            return
        actions = QHBoxLayout()
        open_btn = QPushButton("Открыть")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path))))
        save_btn = QPushButton("Сохранить как…")
        save_btn.clicked.connect(self._save_as)
        copy_btn = QPushButton("Копировать картинку")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setPixmap(self.pixmap))
        for button in (open_btn, save_btn, copy_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(max(40, min(640, self.width())), 420,
                                        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(scaled)
            self.preview.setFixedHeight(scaled.height())

    def _save_as(self):
        path, _filter = QFileDialog.getSaveFileName(self, "Сохранить картинку", str(self.path), "Изображения (*.png *.jpg *.webp)")
        if path and Path(path).resolve() != self.path:
            try:
                shutil.copyfile(self.path, path)
            except OSError as exc:
                self.preview.setToolTip(f"Не удалось сохранить: {exc}")


class TranscriptRow(QFrame):
    expansion_changed = pyqtSignal()
    open_chat = pyqtSignal(str)
    open_agent_message = pyqtSignal(str, str)

    def __init__(self, message, folder, parent=None, image_root=None):
        super().__init__(parent)
        self.role = message.get("role", "system")
        self._snapshot = None
        self._manual_expansion = None
        self._image_root = image_root
        self._image_paths = []
        self._image_widgets = []
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
        elif self.role in ("image_activity", "agent_link"):
            self.header = QLabel()
            self.header.setWordWrap(True)
            self.header.setTextFormat(Qt.TextFormat.PlainText)
            self.header.setStyleSheet(f"color: {colors['accent']}; font-weight: bold;")
            self.setStyleSheet(f"TranscriptRow {{ border: 1px solid {colors['in']}; border-radius: 9px; }}")
        else:
            self.header = QLabel({"user": "Вы", "assistant": "Агент", "system": "Состояние"}.get(self.role, "Сообщение"))
            self.header.setTextFormat(Qt.TextFormat.PlainText)
            self.header.setStyleSheet(f"color: {colors['accent']}; font-weight: bold;")
            if self.role == "user":
                self.setStyleSheet(f"TranscriptRow {{ background: {colors['out']}; border-radius: 6px; }}")
        layout.addWidget(self.header)
        self.body = MessageBody(folder, parent=self, allow_local_links=not message.get("text_chat"))
        layout.addWidget(self.body)
        if self.role == "image_activity":
            open_source = QPushButton("Открыть чат генерации")
            open_source.clicked.connect(lambda: self.open_chat.emit(message["job_chat_id"]))
            layout.addWidget(open_source, alignment=Qt.AlignmentFlag.AlignLeft)
        if self.role == "agent_link":
            self.agent_result_toggle = QToolButton()
            self.agent_result_toggle.setText("Результат инструмента")
            self.agent_result_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.agent_result_toggle.setCheckable(True)
            self.agent_result_toggle.setArrowType(Qt.ArrowType.RightArrow)
            self.agent_result = MessageBody(folder, parent=self)
            self.agent_result.hide()
            self.agent_result_toggle.toggled.connect(self._toggle_agent_result)
            layout.addWidget(self.agent_result_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(self.agent_result)
            open_peer = QPushButton("К запросу" if message.get("incoming") else "Открыть ответный чат")
            open_peer.clicked.connect(lambda: self.open_agent_message.emit(message["peer_chat"], message["peer_message"]))
            layout.addWidget(open_peer, alignment=Qt.AlignmentFlag.AlignLeft)
        self.update_message(message)

    def _toggle(self, expanded):
        self._manual_expansion = expanded
        self.expansion_changed.emit()
        self.body.setVisible(expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _toggle_agent_result(self, expanded):
        self.expansion_changed.emit()
        self.agent_result.setVisible(expanded)
        self.agent_result_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def update_message(self, message):
        snapshot = json.dumps(message, ensure_ascii=False, sort_keys=True)
        if snapshot == self._snapshot:
            return False
        self._snapshot = snapshot
        text = message.get("text", "")
        if self.role == "image_activity":
            status = IMAGE_STATUS.get(message.get("status"), "Неизвестно")
            self.header.setText(f"Генерация изображений · {status}")
        elif self.role == "agent_link":
            title = "Вопрос от" if message.get("incoming") else "Запрос к"
            status = {"waiting": "В работе" if message.get("incoming") else "Ждёт ответа",
                      "timeout": "Время ожидания истекло"}.get(message["status"], STATUS.get(message["status"], ""))
            self.header.setText(f"{title}: {message['peer_title']} · {status}")
            result = message.get("result", "")
            self.agent_result_toggle.setVisible(bool(result))
            self.agent_result.set_content(result)
            self.agent_result.setVisible(bool(result) and self.agent_result_toggle.isChecked())
        elif self.role == "user":
            status = message.get("status")
            label, tip = {
                "queued": ("В очереди", "Агент получит сообщение после завершения текущей задачи"),
                "steering": ("После инструмента", "Агент получит уточнение после текущего действия"),
                "redirecting": ("Уточнение", "Текущий ответ прерывается, чтобы применить уточнение"),
            }.get(status, ("", ""))
            self.header.setText(f"Вы · {label}" if label else "Вы")
            self.header.setToolTip(tip)
        elif self.role == "assistant":
            name = "Модель" if message.get("text_chat") else "Агент"
            interrupted = message.get("status") in ("stopped", "failed", "interrupted") if message.get("text_chat") else message.get("status") == "stopped"
            self.header.setText(f"{name} · Ответ прерван" if interrupted else name)
        if message.get("text_chat") and self.role in ("user", "assistant"):
            in_context = message.get("in_context", False)
            if self.property("inContext") != in_context:
                self.setProperty("inContext", in_context)
                colors = theme.bubbles()
                border = colors["accent"] if in_context else "transparent"
                background = colors["out"] if self.role == "user" else "transparent"
                self.setStyleSheet(f"TranscriptRow {{ background: {background}; border: 1px solid {border}; border-radius: 6px; }}")
            if message.get("context_start"):
                self.header.setText(self.header.text() + " · Начало контекста")
            self.header.setToolTip("Входит в контекст следующего запроса" if in_context else "")
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
        paths = message.get("images", []) if self.role in ("assistant", "user") else []
        if not isinstance(paths, list):
            paths = []
        paths = [p for p in paths[:4] if isinstance(p, str)]
        if paths != self._image_paths:
            for widget in self._image_widgets:
                self.layout().removeWidget(widget)
                widget.deleteLater()
            self._image_paths = paths
            self._image_widgets = [GeneratedImage(path, self._image_root, self) for path in paths]
            for widget in self._image_widgets:
                self.layout().addWidget(widget)
        return True


class AITranscript(QScrollArea):
    open_chat = pyqtSignal(str)
    open_agent_message = pyqtSignal(str, str)

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.image_root = None
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
        self.welcome = AIWelcome()
        self.welcome.expansion_changed.connect(self._expanding)
        self.layout.addWidget(self.welcome)
        self.text_welcome = QLabel("Введите вопрос. Аккаунт и модель можно выбрать в настройках рядом с полем ввода.",
                                   self.container)
        self.text_welcome.setWordWrap(True)
        self.text_welcome.setStyleSheet(f"color: {theme.bubbles()['ts']}; padding: 8px;")
        self.text_welcome.hide()
        self.activity = QFrame()
        activity_layout = QHBoxLayout(self.activity)
        activity_layout.setContentsMargins(12, 6, 12, 6)
        self.activity_label = QLabel()
        self.activity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.activity_label.setWordWrap(True)
        self.activity_label.setStyleSheet(f"color: {theme.bubbles()['ts']};")
        activity_layout.addWidget(self.activity_label, 1)
        self.activity.hide()
        self.layout.addWidget(self.activity)
        self.layout.addStretch(1)
        self.setWidget(self.container)
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)

    def set_activity(self, text, active):
        changed = text != self.activity_label.text() or active == self.activity.isHidden()
        self.activity_label.setText(text)
        self.activity.setVisible(active)
        if active and changed:
            self._follow_end = True
            self.layout.activate()
            QTimer.singleShot(0, self._scroll_to_end)

    def _range_changed(self, _minimum, maximum):
        if self._follow_end:
            self.verticalScrollBar().setValue(maximum)

    def _expanding(self):
        self._follow_end = False

    def _scroll_to_end(self):
        if self._follow_end:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def set_messages(self, messages, scroll_to_end=True, text_chat=False):
        welcome_visible = not any(m.get("role") in ("user", "agent_link") for m in messages)
        welcome = self.text_welcome if text_chat else self.welcome
        previous = self.layout.itemAt(0).widget()
        if previous is not welcome:
            previous.hide()
            self.layout.removeWidget(previous)
            self.layout.insertWidget(0, welcome)
        returning_to_welcome = welcome_visible and welcome.isHidden()
        welcome.setVisible(welcome_visible)
        if welcome_visible:
            self._follow_end = False
            if returning_to_welcome:
                self.verticalScrollBar().setValue(0)
        visible = [message for message in messages if message.get("role") in ("tool", "image_activity", "agent_link")
                   or message.get("text", "").strip() or message.get("images")]
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
                row = TranscriptRow(message, self.folder, image_root=self.image_root)
                row.expansion_changed.connect(self._expanding)
                row.open_chat.connect(self.open_chat.emit)
                row.open_agent_message.connect(self.open_agent_message.emit)
                self.rows[key] = row
            else:
                changed = row.update_message(message) or changed
            if order != self._order:
                self.layout.insertWidget(index + 1, row)
        self._order = order
        if changed and scroll_to_end and not welcome_visible:
            self._follow_end = True
            self.layout.activate()
            self._scroll_to_end()
            QTimer.singleShot(0, self._scroll_to_end)

    def reveal_message(self, message_id):
        row = self.rows.get(message_id)
        if row:
            self._follow_end = False
            self.layout.activate()
            self.ensureWidgetVisible(row, 0, 12)

    def toPlainText(self):
        return "\n\n".join(self.rows[key].body.toPlainText() for key in self._order)

    def toHtml(self):
        return "\n".join(self.rows[key].body.toHtml() for key in self._order)
