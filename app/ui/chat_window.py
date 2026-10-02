import html
import threading
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

STATUS_MARKS = {"sending": "…", "delivered": "✓✓", "failed": "✗"}


class ChatPanel(QWidget):
    """Правая панель: переписка с выбранным узлом (как чат в Telegram)."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.key: str | None = None
        self._rendered = None

        self.header = QLabel("Выберите чат слева")
        self.header.setStyleSheet("font-weight:bold; padding:6px; border-bottom:1px solid #ccc;")

        self.history = QTextBrowser()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Сообщение… (Enter — отправить)")
        self.send_btn = QPushButton("Отправить")

        bottom = QHBoxLayout()
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.header)
        layout.addWidget(self.history, 1)
        layout.addLayout(bottom)

        self.input.returnPressed.connect(self._send)
        self.send_btn.clicked.connect(self._send)
        self._set_enabled(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(400)

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
        self.history.setHtml("")
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.send_btn.setEnabled(on)

    def _node(self):
        return self.engine.node_by_key(self.key) if self.key else None

    def _send(self):
        text = self.input.text().strip()
        node = self._node()
        if not text or not node:
            return
        self.input.clear()
        msg = self.engine.add_outgoing(self.key, text)
        threading.Thread(
            target=self.engine.deliver,
            args=(self.key, msg, node.ip, node.tcp_port),
            daemon=True,
        ).start()
        self._refresh(force=True)

    def _refresh(self, force: bool = False):
        node = self._node()
        if not self.key:
            return
        name = node.name if node else self.key
        status = "в сети" if (node and node.online) else "не в сети"
        self.header.setText(f"{name}  —  {status}")

        msgs = self.engine.chats.get(self.key, [])
        signature = (len(msgs), tuple(m.status for m in msgs))
        if force or signature != self._rendered:
            self._rendered = signature
            self.history.setHtml(self._render(msgs))
            bar = self.history.verticalScrollBar()
            bar.setValue(bar.maximum())
        if self.window().isActiveWindow():
            self.engine.mark_read(self.key)

    def _render(self, msgs) -> str:
        parts = []
        for m in msgs:
            author = html.escape(m.author)
            text = html.escape(m.text)
            ts = time.strftime("%H:%M:%S", time.localtime(m.timestamp))
            if m.direction == "out":
                mark = STATUS_MARKS.get(m.status, "")
                parts.append(
                    f'<div style="margin:4px 0; text-align:right">'
                    f'<span style="color:#888">{ts}</span> <b>{author}</b><br>'
                    f'<span style="background:#d6eaff; padding:2px 6px; border-radius:6px">{text}</span>'
                    f' <span style="color:#888">{mark}</span></div>'
                )
            else:
                parts.append(
                    f'<div style="margin:4px 0">'
                    f'<b>{author}</b> <span style="color:#888">{ts}</span><br>'
                    f'<span style="background:#eee; padding:2px 6px; border-radius:6px">{text}</span></div>'
                )
        return "".join(parts) or '<i style="color:#888">Сообщений пока нет</i>'
