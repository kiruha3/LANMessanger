"""Правая информационная панель: детали выбранного чата (узел/комната).

Данные — только реальные состояния из engine. Панель скрыта, когда чат
не выбран; кнопка-зажим сворачивает её в узкую полосу.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme


class InfoPanel(QWidget):
    """Инфо-панель справа от чата. refresh() дергается таймером MainWindow."""

    collapsed_changed = pyqtSignal(bool)

    def __init__(self, engine, chat_panel):
        super().__init__()
        self.engine = engine
        self.chat_panel = chat_panel
        self.collapsed = False
        self._auto_hidden = False  # окно сузилось < 950px (управляет MainWindow)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setFixedWidth(24)
        self.collapse_btn.setToolTip("Свернуть/развернуть панель")
        self.collapse_btn.clicked.connect(self.toggle_collapsed)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight:bold;")
        self.title_label.setWordWrap(True)
        self.hub_label = QLabel("")
        self.status_label = QLabel("")
        self.channel_label = QLabel("")
        self.tunnel_label = QLabel("")
        self.tunnel_label.setWordWrap(True)
        for lbl in (self.hub_label, self.status_label,
                    self.channel_label, self.tunnel_label):
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)

        self.members_list = QListWidget()
        self.members_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.mute_btn = QPushButton("Уведомления")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Звук и тосты о новых сообщениях этого чата")
        self.mute_btn.toggled.connect(self._mute_toggled)

        self.invite_btn = QPushButton("Пригласить")
        self.invite_btn.setToolTip("Код-приглашение в комнату для пересылки")
        self.invite_btn.clicked.connect(self.chat_panel._show_invite)
        self.rdp_btn = QPushButton("Подключиться по RDP")
        self.rdp_btn.setToolTip(
            "Проброс удалённого рабочего стола через канал мессенджера")
        self.rdp_btn.clicked.connect(self.chat_panel._toggle_rdp)

        self.content = QWidget()
        content_lay = QVBoxLayout(self.content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.addWidget(self.title_label)
        content_lay.addWidget(self.hub_label)
        content_lay.addWidget(self.status_label)
        content_lay.addWidget(self.channel_label)
        content_lay.addWidget(self.tunnel_label)
        content_lay.addWidget(self.mute_btn)
        content_lay.addWidget(self.members_list, 1)
        content_lay.addWidget(self.invite_btn)
        content_lay.addWidget(self.rdp_btn)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.collapse_btn)
        top.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addLayout(top)
        lay.addWidget(self.content, 1)

        self.setMinimumWidth(0)
        self.setVisible(False)
        self.refresh()

    # --- видимость / сворачивание ---

    def toggle_collapsed(self):
        strip = self.collapsed or self._auto_hidden
        if strip:
            # открываем: если панель была авто-скрыта узким окном — считаем
            # это явным желанием пользователя и снимаем авто-скрытие
            self.collapsed = False
            self._auto_hidden = False
        else:
            self.collapsed = True
        self._apply_state()
        self.collapsed_changed.emit(self.collapsed or self._auto_hidden)

    def set_auto_hidden(self, hidden: bool):
        """Авто-скрытие при узком окне (состояние collapse не трогаем)."""
        if self._auto_hidden != hidden:
            self._auto_hidden = hidden
            self._apply_state()

    @property
    def auto_hidden(self) -> bool:
        return self._auto_hidden

    def _apply_state(self):
        key = self.chat_panel.key
        self.setVisible(bool(key))
        strip = self.collapsed or self._auto_hidden
        self.content.setVisible(not strip)
        color = theme.bubbles()["ts"]
        direction = "right" if strip else "left"
        self.collapse_btn.setIcon(theme.make_triangle_icon(direction, color))
        self.setMaximumWidth(36 if strip else 16777215)

    # --- содержимое из engine ---

    def refresh(self):
        key = self.chat_panel.key
        if not key:
            self._apply_state()
            return
        if key.startswith("room:"):
            self._fill_room(key)
        else:
            self._fill_node(key)
        self._apply_state()

    def _room_entry(self, key: str):
        rest = key[len("room:"):]
        if "/" not in rest:
            return None, None, None
        hub_key, room = rest.rsplit("/", 1)
        entry = next(
            (r for r in getattr(self.engine, "my_rooms", [])
             if r.get("hub_key") == hub_key and r.get("room") == room),
            None)
        return entry, hub_key, room

    def _fill_room(self, key: str):
        entry, hub_key, room = self._room_entry(key)
        members = (entry or {}).get("members") or []
        hub_name = (entry or {}).get("hub_name") or hub_key or "—"
        self.title_label.setText(f"Комната: {room or key}")
        self.hub_label.setText(f"хаб: {hub_name}")
        self.hub_label.show()
        self.status_label.setText(f"участников: {len(members)}")
        self.status_label.show()
        self.channel_label.hide()
        self.tunnel_label.hide()
        if self.members_list.count() != len(members) or \
                [self.members_list.item(i).text()
                 for i in range(self.members_list.count())] != list(members):
            self.members_list.clear()
            for name in members:
                item = QListWidgetItem(str(name))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.members_list.addItem(item)
        self.members_list.show()
        self.mute_btn.hide()
        self.invite_btn.show()
        self.rdp_btn.hide()

    def _fill_node(self, key: str):
        node = self.engine.node_by_key(key)
        name = node.name if node else key
        self.title_label.setText(name)
        addr = f"{node.ip}:{node.tcp_port}" if node else key
        self.hub_label.setText(addr)
        self.hub_label.show()
        online = bool(node and node.online)
        self.status_label.setText("в сети" if online else "не в сети")
        self.status_label.setStyleSheet(
            f"color:{theme.node_colors()['online' if online else 'offline']}")
        self.status_label.show()
        connected = self.engine.connections.is_connected(key)
        self.channel_label.setText(
            f"канал: {'установлен' if connected else 'нет'}")
        self.channel_label.show()
        port = self.engine.tunnel.rdp_port(key)
        self.tunnel_label.setText(
            f"туннель активен: 127.0.0.1:{port}" if port else "туннель: нет")
        self.tunnel_label.show()
        self.members_list.hide()
        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(not self.engine.is_chat_muted(key))
        self.mute_btn.blockSignals(False)
        self.mute_btn.show()
        self.invite_btn.hide()
        self.rdp_btn.setVisible(self.engine.rdp_enabled)
        self.rdp_btn.setEnabled(online)
        self.rdp_btn.setText(
            "Закрыть RDP-туннель" if port else "Подключиться по RDP")

    def _mute_toggled(self, checked: bool):
        key = self.chat_panel.key
        if key and not key.startswith("room:"):
            self.engine.set_chat_muted(key, not checked)
