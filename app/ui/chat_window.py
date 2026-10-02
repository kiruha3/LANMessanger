import html
import subprocess
import threading
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import theme

STATUS_MARKS = {"sending": "…", "delivered": "✓✓", "failed": "✗"}


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

        self.history = QTextBrowser()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Сообщение… (Enter — отправить)")
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
        self.rdp_btn.setEnabled(False)

    def _set_enabled(self, on: bool):
        self.input.setEnabled(on)
        self.send_btn.setEnabled(on)
        self.rdp_btn.setEnabled(on)

    def _node(self):
        return self.engine.node_by_key(self.key) if self.key else None

    # --- RDP-туннель ---

    def _toggle_rdp(self):
        node = self._node()
        if not node:
            return
        active_port = self.engine.tunnel.rdp_port(self.key)
        if active_port:
            self.engine.tunnel.close_rdp(self.key)
            self._refresh(force=True)
            return
        ok, info = self.engine.tunnel.open_rdp(self.key, node.ip, node.tcp_port)
        if not ok:
            QMessageBox.warning(self, "RDP", f"Не удалось открыть туннель: {info}")
            return
        self._refresh(force=True)
        try:
            subprocess.Popen(["mstsc", f"/v:127.0.0.1:{info}"])
        except OSError:
            QMessageBox.information(
                self, "RDP",
                f"Туннель открыт. Подключитесь вручную: mstsc /v:127.0.0.1:{info}")

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
        port = self.engine.tunnel.rdp_port(self.key)
        self.rdp_btn.setText(f"RDP: 127.0.0.1:{port} ✕" if port else "RDP")

        msgs = self.engine.chat(self.key)
        signature = (len(msgs), tuple(m.status for m in msgs))
        if force or signature != self._rendered:
            bar = self.history.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 20
            prev_value = bar.value()
            self._rendered = signature
            self.history.setHtml(self._render(msgs))
            if force or at_bottom:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(prev_value)  # не дёргаем скролл, если пользователь читает выше
        if self.window().isActiveWindow():
            self.engine.mark_read(self.key)

    def _render(self, msgs) -> str:
        colors = theme.bubbles()
        out_bg, in_bg, ts_color = colors["out"], colors["in"], colors["ts"]
        parts = []
        for m in msgs:
            author = html.escape(m.author)
            text = html.escape(m.text)
            ts = time.strftime("%H:%M:%S", time.localtime(m.timestamp))
            if m.direction == "out":
                mark = STATUS_MARKS.get(m.status, "")
                parts.append(
                    f'<div style="margin:4px 0; text-align:right">'
                    f'<span style="color:{ts_color}">{ts}</span> <b>{author}</b><br>'
                    f'<span style="background:{out_bg}; padding:2px 6px; border-radius:6px">{text}</span>'
                    f' <span style="color:{ts_color}">{mark}</span></div>'
                )
            else:
                parts.append(
                    f'<div style="margin:4px 0">'
                    f'<b>{author}</b> <span style="color:{ts_color}">{ts}</span><br>'
                    f'<span style="background:{in_bg}; padding:2px 6px; border-radius:6px">{text}</span></div>'
                )
        return "".join(parts) or '<i style="color:#888">Сообщений пока нет</i>'
