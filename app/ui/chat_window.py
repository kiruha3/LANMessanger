import html
import subprocess
import threading
import time

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QTextDocument
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.engine import Engine
from . import theme

STATUS_MARKS = {"sending": "…", "delivered": "✓✓", "failed": "✗"}
MAX_BUBBLE_RATIO = 0.65
PAD_X, PAD_Y = 12, 8


class ChatInput(QLineEdit):
    """Поле ввода: Ctrl+V с картинкой из буфера отправляет её как сообщение."""

    image_pasted = pyqtSignal(object)  # QImage

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            img = QApplication.clipboard().image()
            if not img.isNull():
                self.image_pasted.emit(img)
                return
        super().keyPressEvent(event)


def _layout(msg, avail_w: int):
    """Единый расчёт: документ, ширина и высота пузыря."""
    direction, author, text, ts, status, img = msg
    colors = theme.bubbles()
    ts_col = colors["ts"]
    time_str = time.strftime("%H:%M", time.localtime(ts))
    body = html.escape(text).replace("\n", "<br>")
    if img:
        path = Engine.img_path(img).replace("\\", "/")
        body += f'<br><img src="file:///{path}" width="280">'
    mark = f" {STATUS_MARKS.get(status, '')}" if direction == "out" else ""
    meta = f' <span style="color:{ts_col}; font-size:8pt">{time_str}{mark}</span>'
    if direction == "in":
        body = f'<b style="color:{colors["accent"]}">{html.escape(author)}</b><br>' + body

    max_w = max(120, int(avail_w * MAX_BUBBLE_RATIO))
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setHtml(f'<span style="color:{colors["text"]}">{body}</span>{meta}')
    doc.setTextWidth(max_w - 2 * PAD_X)
    content_w = min(max_w, int(doc.idealWidth() + 0.5) + 2 * PAD_X)
    doc.setTextWidth(content_w - 2 * PAD_X)
    content_h = int(doc.size().height() + 0.5) + 2 * PAD_Y
    return doc, content_w, content_h


class BubbleDelegate(QStyledItemDelegate):
    """Рисует сообщение как пузырь Telegram: свои справа, чужие слева."""

    def paint(self, painter: QPainter, option, index):
        msg = index.data(Qt.ItemDataRole.UserRole)
        direction = msg[0]
        colors = theme.bubbles()
        rect = option.rect
        doc, content_w, content_h = _layout(msg, rect.width())

        if direction == "out":
            bx = rect.right() - content_w - 8
            bg = colors["out"]
        else:
            bx = rect.left() + 8
            bg = colors["in"]

        bubble = QRectF(bx, rect.top() + 2, content_w, content_h)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(bubble.adjusted(-1, -1, 1, 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(bubble, 10, 10)
        painter.translate(bubble.left() + PAD_X, bubble.top() + PAD_Y)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        msg = index.data(Qt.ItemDataRole.UserRole)
        w = option.rect.width() or 400
        _, _, content_h = _layout(msg, w)
        return QSize(w, content_h + 6)


class ChatPanel(QWidget):
    """Правая панель: переписка с выбранным узлом (как чат в Telegram)."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.key: str | None = None
        self._rendered = None

        self.header = QLabel("Выберите чат слева")
        self.header.setStyleSheet("font-weight:bold; padding:6px;")
        self.rdp_btn = QPushButton("RDP")
        self.rdp_btn.setToolTip("Проброс удалённого рабочего стола через канал мессенджера")
        self.rdp_btn.setEnabled(False)
        self.rdp_btn.clicked.connect(self._toggle_rdp)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.header, 1)
        header_row.addWidget(self.rdp_btn)

        self.history = QListWidget()
        self.history.setItemDelegate(BubbleDelegate(self.history))
        self.history.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history.setSpacing(2)
        self.history.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

        self.input = ChatInput()
        self.input.setPlaceholderText("Сообщение… (Enter — отправить, Ctrl+V — картинка)")
        self.input.image_pasted.connect(self._send_image)
        self.send_btn = QPushButton("Отправить")

        bottom = QHBoxLayout()
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_row)
        layout.addWidget(self.history, 1)
        layout.addLayout(bottom)

        self.input.returnPressed.connect(self._send)
        self.send_btn.clicked.connect(self._send)
        self._set_enabled(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(400)

        from .image_viewer import ImageViewer

        self.viewer = ImageViewer(self)
        self.history.itemClicked.connect(self._maybe_open_image)

    def _maybe_open_image(self, item):
        msg = item.data(Qt.ItemDataRole.UserRole)
        if msg and msg[5]:  # img
            self.viewer.open_image(Engine.img_path(msg[5]))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "viewer"):
            self.viewer.dock()

    def set_key(self, key: str | None):
        self.key = key
        self._rendered = None
        if key:
            self.engine.mark_read(key)
            self._set_enabled(True)
        self._refresh(force=True)

    def show_hint(self, text: str):
        self.key = None
        self.header.setText(text)
        self.history.clear()
        self._set_enabled(False)
        self.rdp_btn.setEnabled(False)

    def _set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.send_btn.setEnabled(on)
        self.rdp_btn.setEnabled(on and self._room_parts() is None)

    def _room_parts(self):
        """Для ключа room:<hub_key>/<room> вернуть (hub_key, room), иначе None."""
        if self.key and self.key.startswith("room:"):
            rest = self.key[len("room:"):]
            if "/" in rest:
                hub_key, room = rest.rsplit("/", 1)
                return hub_key, room
        return None

    def _node(self):
        if not self.key or self.key.startswith("room:"):
            return None
        return self.engine.node_by_key(self.key)

    # --- RDP-туннель ---

    def _toggle_rdp(self):
        node = self._node()
        if not node:
            return
        if not node.online:
            QMessageBox.warning(
                self, "RDP",
                f"{node.name} сейчас не в сети — компьютер выключен "
                f"или приложение там не запущено.")
            return
        active_port = self.engine.tunnel.rdp_port(self.key)
        if active_port:
            self.engine.tunnel.close_rdp(self.key)
            self._refresh(force=True)
            return
        ok, info, session_id = self.engine.tunnel.open_rdp(
            self.key, node.ip, node.tcp_port, shadow=self.engine.shadow_rdp)
        if not ok:
            if "нет соединения" in str(info):
                info = (f"не удалось подключиться к {node.ip}:{node.tcp_port} — "
                        f"компьютер выключен, приложение не запущено "
                        f"или сеть недоступна")
            QMessageBox.warning(self, "RDP", f"Не удалось открыть туннель: {info}")
            return
        self._refresh(force=True)
        QMessageBox.information(
            self, "RDP",
            f"Туннель открыт к {node.name}: mstsc на 127.0.0.1:{info}")
        if session_id:
            args = ["mstsc", f"/shadow:{session_id}", "/control",
                    f"/v:127.0.0.1:{info}"]
        else:
            args = ["mstsc", f"/v:127.0.0.1:{info}"]
        try:
            subprocess.Popen(args)
        except OSError:
            QMessageBox.information(
                self, "RDP",
                f"Туннель открыт. Подключитесь вручную: {' '.join(args)}")

    def _send(self):
        text = self.input.text().strip()
        if not text or not self.key:
            return
        parts = self._room_parts()
        if parts:
            hub_key, room = parts
            self.input.clear()
            threading.Thread(
                target=self.engine.send_room,
                args=(hub_key, room, text),
                daemon=True,
            ).start()
            self._refresh(force=True)
            return
        node = self._node()
        if not node:
            return
        self.input.clear()
        msg = self.engine.add_outgoing(self.key, text)
        threading.Thread(
            target=self.engine.deliver,
            args=(self.key, msg, node.ip, node.tcp_port),
            daemon=True,
        ).start()
        self._refresh(force=True)

    def _send_image(self, qimg):
        if qimg.isNull() or not self.key:
            return
        parts = self._room_parts()
        node = None if parts else self._node()
        if not parts and not node:
            return
        if max(qimg.width(), qimg.height()) > 1600:
            qimg = qimg.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        import os
        from ..net import protocol
        from ..core.history import base_dir

        name = f"{protocol.new_id()}.png"
        os.makedirs(os.path.join(base_dir(), "images"), exist_ok=True)
        qimg.save(Engine.img_path(name), "PNG")
        if parts:
            hub_key, room = parts
            threading.Thread(
                target=self.engine.send_room,
                args=(hub_key, room, ""),
                kwargs={"img": name},
                daemon=True,
            ).start()
        else:
            msg = self.engine.add_outgoing(self.key, "", img=name)
            threading.Thread(
                target=self.engine.deliver,
                args=(self.key, msg, node.ip, node.tcp_port),
                daemon=True,
            ).start()
        self._refresh(force=True)

    def _refresh(self, force: bool = False):
        if not self.key:
            return
        parts = self._room_parts()
        if parts:
            hub_key, room = parts
            info = next(
                (r for r in getattr(self.engine, "my_rooms", [])
                 if r.get("hub_key") == hub_key and r.get("room") == room),
                None)
            count = len(info.get("members") or []) if info else 0
            hub_name = info.get("hub_name") if info else None
            title = f"🏠 {room} — {count} участников"
            if hub_name:
                title += f" (хаб: {hub_name})"
            self.header.setText(title)
            self.rdp_btn.setVisible(False)
        else:
            node = self._node()
            name = node.name if node else self.key
            status = "в сети" if (node and node.online) else "не в сети"
            self.header.setText(f"{name}  —  {status}")
            port = self.engine.tunnel.rdp_port(self.key)
            if port:
                self.rdp_btn.setText(f"RDP: 127.0.0.1:{port} → {name} ✕")
                self.rdp_btn.setToolTip(f"Туннель к {name} ({node.ip if node else ''})")
            else:
                self.rdp_btn.setText("RDP")
            self.rdp_btn.setVisible(self.engine.rdp_enabled)

        msgs = self.engine.chat(self.key)
        signature = (len(msgs), tuple(m.status for m in msgs))
        if force or signature != self._rendered:
            bar = self.history.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 20
            prev_value = bar.value()
            self._rendered = signature
            self._rebuild(msgs)
            if force or at_bottom:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(prev_value)  # не дёргаем скролл при чтении выше
        if self.window().isActiveWindow():
            self.engine.mark_read(self.key)

    def _rebuild(self, msgs):
        self.history.clear()
        for m in msgs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole,
                         (m.direction, m.author, m.text, m.timestamp,
                          m.status, m.img))
            self.history.addItem(item)
