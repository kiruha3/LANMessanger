import ipaddress
import threading
import time

from PyQt6.QtCore import Qt, QRectF, QSize, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..net import scanner
from ..net.constants import is_private_ip
from . import theme
from .chat_window import ChatPanel


def make_icon(color: str = "#1a7f37") -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pm)


def make_bell_icon(count: int = 0, size: int = 28) -> QIcon:
    """Колокольчик, нарисованный кодом (на Win10 нет эмодзи 🔔)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    s = size / 32.0
    p.setBrush(QColor("#3390ec" if count else "#8a8a8a"))
    p.drawPie(int(6 * s), int(5 * s), int(20 * s), int(20 * s), 0, 180 * 16)
    p.drawRect(int(6 * s), int(15 * s), int(20 * s), int(8 * s))
    p.drawRect(int(4 * s), int(23 * s), int(24 * s), int(3 * s))
    p.drawEllipse(int(13 * s), int(26 * s), int(6 * s), int(5 * s))
    if count:
        p.setBrush(QColor("#d32f2f"))
        p.drawEllipse(int(17 * s), 0, int(15 * s), int(15 * s))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(int(17 * s), 0, int(15 * s), int(15 * s)),
                   Qt.AlignmentFlag.AlignCenter, str(min(count, 99)))
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    message_received = pyqtSignal(str)   # key чата (мост из сетевых потоков)
    host_found = pyqtSignal(str, str)    # ip, hostname (живые строки скана)
    scan_done = pyqtSignal()
    tunnel_error = pyqtSignal(str, str)  # key, текст ошибки туннеля
    reminder = pyqtSignal(str, str)      # заголовок, текст напоминания
    notif_received = pyqtSignal()        # новое уведомление в центре
    rooms_changed = pyqtSignal()         # состав/список комнат изменился (из engine)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._quitting = False
        self._tray_hint_shown = False
        self._public_warned = False
        self._scanning = False
        self._scanned: dict[str, str] = {}  # ip -> hostname (устройства без мессенджера)

        self.setWindowTitle(f"LAN Messenger {__version__} — {engine.name}")
        self.setWindowIcon(make_icon())
        self.resize(860, 520)

        # --- левая панель ---
        self.name_edit = QLineEdit(engine.name)
        self.name_edit.setPlaceholderText("Моё имя в сети")
        self.name_edit.editingFinished.connect(self._name_changed)

        self.rejected_label = QLabel("")
        self.rejected_label.setStyleSheet("color:#c0392b")
        self.rejected_label.hide()
        self.rejected_btn = QPushButton("Разрешить")
        self.rejected_btn.hide()
        self.rejected_btn.clicked.connect(self._allow_rejected)
        rejected_row = QHBoxLayout()
        rejected_row.addWidget(self.rejected_label, 1)
        rejected_row.addWidget(self.rejected_btn)

        self.addip_edit = QLineEdit()
        self.addip_edit.setPlaceholderText("IP вручную, напр. 192.168.0.5")
        self.addip_edit.returnPressed.connect(self._add_manual_peer)
        self.addip_btn = QPushButton("+")
        self.addip_btn.setFixedWidth(32)
        self.addip_btn.setToolTip("Добавить узел по IP")
        self.addip_btn.clicked.connect(self._add_manual_peer)
        addip_row = QHBoxLayout()
        addip_row.addWidget(self.addip_edit, 1)
        addip_row.addWidget(self.addip_btn)

        self.room_edit = QLineEdit()
        self.room_edit.setPlaceholderText(
            "комната[:пароль]@IP-хаба, напр. rzhd:secret@192.168.0.5")
        self.room_edit.returnPressed.connect(self._join_room)
        self.room_btn = QPushButton("+")
        self.room_btn.setFixedWidth(32)
        self.room_btn.setToolTip("Войти в комнату на хабе")
        self.room_btn.clicked.connect(self._join_room)
        room_row = QHBoxLayout()
        room_row.addWidget(self.room_edit, 1)
        room_row.addWidget(self.room_btn)

        # строки «IP вручную» и «комната@IP» скрыты по умолчанию (флаги)
        self._addip_widgets = [self.addip_edit, self.addip_btn]
        self._room_widgets = [self.room_edit, self.room_btn]
        for w in self._addip_widgets + self._room_widgets:
            w.hide()

        self.node_list = QTreeWidget()
        self.node_list.setHeaderHidden(True)
        self.node_list.setIndentation(14)
        self.node_list.itemClicked.connect(self._select)
        self.node_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.node_list.customContextMenuRequested.connect(self._tree_context_menu)

        self.sort_btn = QPushButton()
        self.sort_btn.setToolTip("Порядок списка: клик — следующий режим")
        self.sort_btn.clicked.connect(self._cycle_sort)
        self._update_sort_btn()

        self.scan_btn = QPushButton("Обновить скан сети")
        self.scan_btn.clicked.connect(lambda: self.start_scan(force=True))
        self.scan_btn.setVisible(engine.scan_enabled)
        self.settings_btn = QPushButton("Настройки…")
        self.settings_btn.clicked.connect(self._open_settings)
        self.exit_btn = QPushButton("Выход")
        self.exit_btn.setToolTip("Завершить процесс полностью (не сворачивать в трей)")
        self.exit_btn.clicked.connect(self._exit_clicked)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.settings_btn, 1)
        bottom_row.addWidget(self.exit_btn, 1)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Моё имя:"))
        left_layout.addWidget(self.name_edit)
        left_layout.addLayout(addip_row)
        left_layout.addLayout(room_row)
        left_layout.addLayout(rejected_row)
        left_layout.addWidget(self.sort_btn)
        left_layout.addWidget(self.node_list, 1)
        left_layout.addWidget(self.scan_btn)
        left_layout.addLayout(bottom_row)
        left = QWidget()
        left.setLayout(left_layout)
        self._apply_feature_visibility()

        # --- правая панель ---
        self.chat_panel = ChatPanel(engine)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.chat_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 560])

        from .calendar_window import CalendarWindow
        from .help_tab import HelpTab

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Чаты")
        self.tabs.addTab(CalendarWindow(engine), "Календарь")
        self.tabs.addTab(HelpTab(), "Помощь")
        self.setCentralWidget(self.tabs)

        # колокольчик уведомлений в правом верхнем углу вкладок
        self.notif_btn = QPushButton()
        self.notif_btn.setFixedWidth(46)
        self.notif_btn.setIcon(make_bell_icon(0))
        self.notif_btn.setToolTip("Уведомления")
        self.notif_btn.clicked.connect(self._toggle_notif_panel)
        self.tabs.setCornerWidget(self.notif_btn, Qt.Corner.TopRightCorner)
        self._notif_panel = None

        self._setup_tray()
        self.engine.on_message_event = lambda key, msg: self.message_received.emit(key)
        self.message_received.connect(self._on_new_message)
        self.host_found.connect(self._on_host_found)
        self.scan_done.connect(self._on_scan_done)
        self.engine.on_tunnel_error = lambda key, err: self.tunnel_error.emit(key, err)
        self.tunnel_error.connect(self._show_tunnel_error)
        self.engine.on_reminder = self._on_reminder
        self.reminder.connect(self._show_reminder)
        self.engine.on_notification = lambda note: self.notif_received.emit()
        self.notif_received.connect(self._update_notif_badge)
        self.engine.on_rooms_changed = lambda: self.rooms_changed.emit()
        self.rooms_changed.connect(self._on_rooms_changed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    # --- трей ---

    def _apply_feature_visibility(self):
        """Показать/скрыть продвинутые поля по флагам (без перезапуска)."""
        for w in self._addip_widgets:
            w.setVisible(getattr(self.engine, "manual_ip_enabled", False))
        for w in self._room_widgets:
            w.setVisible(getattr(self.engine, "room_join_enabled", False))

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        menu.addAction("Открыть", self._show_from_tray)
        self.mute_action = menu.addAction("Тихий режим (до перезапуска)")
        self.mute_action.setCheckable(True)
        menu.addAction("Выйти", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(f"LAN Messenger {__version__} — {self.engine.name}")
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._open_last_message)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _open_last_message(self):
        """Клик по тосту — открыть чат с последним входящим сообщением."""
        self._show_from_tray()
        self.tabs.setCurrentIndex(0)
        key = getattr(self, "_last_msg_key", None)
        if key:
            self.select_chat(key)

    # --- центр уведомлений (колокольчик) ---

    def _update_notif_badge(self):
        count = self.engine.unread_notifications()
        self.notif_btn.setIcon(make_bell_icon(count))
        self.notif_btn.setIconSize(QSize(28, 28))

    def _toggle_notif_panel(self):
        if self._notif_panel and self._notif_panel.isVisible():
            self._notif_panel.hide()
            return
        # обычный дочерний виджет (НЕ Qt.Popup — Popup захватывает ввод
        # модально и вешает приложение, если поверх открывается QMessageBox)
        if self._notif_panel is None:
            frame = QFrame(self)
            frame.setStyleSheet("QFrame { border: 1px solid #888; }")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(4, 4, 4, 4)
            self._notif_list = QListWidget()
            self._notif_list.setWordWrap(True)
            self._notif_list.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._notif_list.itemClicked.connect(self._notif_clicked)
            close_btn = QPushButton("✕")
            close_btn.setFixedWidth(32)
            close_btn.clicked.connect(frame.hide)
            top = QHBoxLayout()
            top.addWidget(QLabel("Уведомления"), 1)
            top.addWidget(close_btn)
            lay.addLayout(top)
            lay.addWidget(self._notif_list)
            frame.setFixedWidth(380)
            frame.setMaximumHeight(420)
            self._notif_panel = frame

        self._notif_list.clear()
        notes = list(reversed(self.engine.notifications[-50:]))
        for n in notes:
            when = time.strftime("%d.%m %H:%M", time.localtime(n["ts"]))
            link_mark = "  [ссылка]" if n.get("link") else ""
            item = QListWidgetItem(
                f"{when}  {n['title']}\n{n['text']}{link_mark}")
            item.setData(Qt.ItemDataRole.UserRole, n)
            item.setSizeHint(QSize(-1, 48))  # две строки: время+заголовок / текст
            self._notif_list.addItem(item)
        if not notes:
            item = QListWidgetItem("Уведомлений нет")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(-1, 32))
            self._notif_list.addItem(item)

        rows = max(1, self._notif_list.count())
        h = min(420, 44 + rows * 48 + 12)
        self._notif_panel.setFixedSize(380, max(120, h))
        x = max(0, self.width() - self._notif_panel.width() - 8)
        self._notif_panel.move(x, self.tabs.pos().y() + 36)
        self._notif_panel.show()
        self._notif_panel.raise_()
        self.engine.mark_notifications_read()
        self._update_notif_badge()

    def _notif_clicked(self, item):
        note = item.data(Qt.ItemDataRole.UserRole)
        if note and note.get("link"):
            QDesktopServices.openUrl(QUrl(note["link"]))
            self._notif_panel.hide()

    def _quit(self):
        self._quitting = True
        QApplication.quit()

    def _exit_clicked(self):
        answer = QMessageBox.question(
            self, "Выход",
            "Завершить LAN Messenger полностью?\n"
            "Сообщения и туннели перестанут приниматься.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._quit()

    # --- события ---

    def _on_new_message(self, key: str):
        self._last_msg_key = key
        self.refresh()
        if self._muted():
            return
        if self.isVisible() and self.chat_panel.key == key and self.isActiveWindow():
            return
        node = self.engine.node_by_key(key)
        name = node.name if node else key
        msgs = self.engine.chat(key)
        text = msgs[-1].text[:200] if msgs else ""
        self.engine.add_notification(f"Сообщение от {name}", text,
                                     kind="message")
        QApplication.beep()
        if self.tray.isVisible():
            self.tray.showMessage(
                name, text,
                QSystemTrayIcon.MessageIcon.Information, 5000,
            )

    def _muted(self) -> bool:
        return getattr(self, "mute_action", None) is not None \
            and self.mute_action.isChecked()

    def _on_reminder(self, ev: dict):
        when = time.strftime("%d.%m %H:%M", time.localtime(ev["ts"]))
        self.reminder.emit(f"Напоминание: {ev['title']}", f"{when} — {ev['title']}")

    def _show_reminder(self, title: str, text: str):
        if self._muted():
            return
        QApplication.beep()
        if self.tray.isVisible():
            self.tray.showMessage(title, text,
                                  QSystemTrayIcon.MessageIcon.Information, 8000)

    def _name_changed(self):
        name = self.name_edit.text().strip()
        if name and name != self.engine.name:
            self.engine.set_name(name)
            self.setWindowTitle(f"LAN Messenger {__version__} — {name}")
            self.tray.setToolTip(f"LAN Messenger {__version__} — {name}")

    def _open_settings(self):
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self, self.engine)
        if dlg.exec():
            self.scan_btn.setVisible(self.engine.scan_enabled)
            self._apply_feature_visibility()

    def _show_tunnel_error(self, key: str, text: str):
        node = self.engine.node_by_key(key)
        name = node.name if node else key
        QMessageBox.warning(self, "RDP-туннель", f"{name}:\n{text}")

    def _allow_rejected(self):
        ips = self.engine.rejected_ips()
        if not ips:
            return
        ip = ips[0]
        self.engine.allow_ip(ip)
        self.engine.dismiss_rejected(ip)
        self.engine.add_manual_peer(ip)
        self.refresh()

    def _refresh_rejected(self):
        ips = self.engine.rejected_ips()
        if ips:
            self.rejected_label.setText(f"Отклонено: {ips[0]}")
            self.rejected_label.show()
            self.rejected_btn.show()
        else:
            self.rejected_label.hide()
            self.rejected_btn.hide()

    # --- ручное добавление по IP (в т.ч. белый) ---

    def _add_manual_peer(self):
        text = self.addip_edit.text().strip()
        if not text:
            return
        try:
            ip = str(ipaddress.ip_address(text))
        except ValueError:
            QMessageBox.warning(self, "Неверный IP", f"«{text}» — не похоже на IP-адрес.")
            return
        self.engine.add_manual_peer(ip)
        self.addip_edit.clear()
        if not is_private_ip(ip) and not self._public_warned:
            self._public_warned = True
            QMessageBox.information(
                self, "Публичный IP",
                "Вы добавили публичный (белый) IP.\n\n"
                "Соединение возможно, если на той стороне запущен этот же "
                "мессенджер и порт TCP 45678 открыт/проброшен. Учтите: "
                "сообщения пока передаются без шифрования.",
            )
        self.refresh()
        self.select_chat(f"{ip}:45678")

    # --- вход в комнату на хабе ---

    def _join_room(self):
        text = self.room_edit.text().strip()
        if not text:
            return
        if "@" not in text:
            QMessageBox.warning(
                self, "Комната",
                "Формат: комната[:пароль]@IP-хаба, напр. rzhd@192.168.0.5 "
                "или rzhd:secret@192.168.0.5")
            return
        left, _, ip = text.rpartition("@")
        left, ip = left.strip(), ip.strip()
        # "комната:пароль" — пароль отделяется по ПЕРВОМУ ":" (может сам
        # содержать ":", но не "@"); пустой пароль = без пароля
        password = None
        room = left
        if ":" in left:
            room, _, pw = left.partition(":")
            room = room.strip()
            password = pw or None
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            QMessageBox.warning(self, "Неверный IP", f"«{ip}» — не похоже на IP-адрес.")
            return
        if not room:
            QMessageBox.warning(self, "Комната", "Имя комнаты пустое.")
            return
        join_room = getattr(self.engine, "join_room", None)
        if join_room is None:
            QMessageBox.warning(self, "Комната",
                                "Эта версия ядра не поддерживает комнаты.")
            return
        self.engine.add_manual_peer(ip)
        hub_key = f"{ip}:45678"
        try:
            ok = join_room(hub_key, room, password=password)
        except TypeError:
            ok = join_room(hub_key, room)  # ядро без поддержки паролей
        if not ok:
            QMessageBox.warning(
                self, "Комната",
                f"Не удалось подключиться к хабу {ip}.\n"
                "Проверьте, что там запущен мессенджер и порт TCP 45678 доступен.")
            return
        self.room_edit.clear()
        self._sig = None  # в дереве появилась комната
        self.refresh()
        self.select_chat(f"room:{hub_key}/{room}")

    def _on_rooms_changed(self):
        self._sig = None  # состав/список комнат изменился — перерисовать
        self.refresh()

    # --- скан сети (фон, живые строки) ---

    def start_scan(self, force: bool = False):
        if self._scanning:
            return
        self._scanning = True
        if force:
            self._scanned.clear()
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Сканирование…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            self._scan_results = scanner.scan(
                on_host=lambda ip, hn, mac: self.host_found.emit(ip, hn))
        except Exception:
            self._scan_results = []
        finally:
            self.scan_done.emit()

    def _on_host_found(self, ip: str, hostname: str):
        if any(n.ip == ip for n in self.engine.nodes()):
            return  # это узел мессенджера, он и так в списке
        self._scanned[ip] = hostname
        self.refresh()

    def _on_scan_done(self):
        self._scanning = False
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Обновить скан сети")
        # итог с фильтром «фантомов»: пересобираем список устройств
        node_ips = {n.ip for n in self.engine.nodes()}
        self._scanned = {
            r["ip"]: r["hostname"]
            for r in getattr(self, "_scan_results", [])
            if r["ip"] not in node_ips
        }
        self.refresh()

    # --- сортировка списка ---

    SORT_MODES = ["status", "name", "ip"]
    SORT_LABELS = {"status": "⇅ Сорт: в сети сверху",
                   "name": "⇅ Сорт: по имени",
                   "ip": "⇅ Сорт: по IP"}

    def _cycle_sort(self):
        modes = self.SORT_MODES
        nxt = modes[(modes.index(self.engine.sort_mode) + 1) % len(modes)]
        self.engine.set_sort_mode(nxt)
        self._update_sort_btn()
        self._sig = None  # принудительная перерисовка
        self.refresh()

    def _update_sort_btn(self):
        self.sort_btn.setText(self.SORT_LABELS.get(
            self.engine.sort_mode, self.SORT_LABELS["status"]))

    def _sorted_nodes(self, nodes):
        mode = self.engine.sort_mode
        if mode == "name":
            return sorted(nodes, key=lambda n: (n.name.lower(), n.ip))
        if mode == "ip":
            return sorted(nodes, key=lambda n: tuple(int(p) for p in n.ip.split(".")))
        return nodes  # status: registry уже сортирует онлайн первыми

    # --- дерево узлов, комнат и устройств ---

    @staticmethod
    def _room_key(room_info: dict) -> str:
        return f"room:{room_info.get('hub_key', '')}/{room_info.get('room', '')}"

    def refresh(self):
        self._refresh_rejected()
        nodes = self._sorted_nodes(self.engine.nodes())
        rooms = list(getattr(self.engine, "my_rooms", []))
        rooms_by_hub: dict[str, list[dict]] = {}
        for r in rooms:
            rooms_by_hub.setdefault(r.get("hub_key", ""), []).append(r)

        def node_unread(node):
            # свои непрочитанные + непрочитанные в комнатах этого хаба
            total = self.engine.unread_count(node.key)
            for r in rooms_by_hub.get(node.key, []):
                total += self.engine.unread_count(self._room_key(r))
            return total

        # чаты с непрочитанными — наверх (FR: новое сообщение поднимает чат)
        nodes = ([n for n in nodes if node_unread(n)]
                 + [n for n in nodes if not node_unread(n)])
        node_keys = {n.key for n in nodes}
        node_ips = {n.ip for n in nodes}
        orphan_rooms = [r for r in rooms if r.get("hub_key") not in node_keys]
        scanned = {ip: hn for ip, hn in self._scanned.items() if ip not in node_ips}
        signature = (
            tuple((n.key, n.name, n.online, self.engine.unread_count(n.key))
                  for n in nodes),
            tuple(sorted(
                (r.get("hub_key", ""), r.get("room", ""),
                 len(r.get("members") or []),
                 self.engine.unread_count(self._room_key(r)))
                for r in rooms)),
            tuple(sorted(scanned.items())),
        )
        if signature == getattr(self, "_sig", None):
            return  # ничего не изменилось — не перерисовываем (иначе прыгает)
        self._sig = signature

        colors = theme.node_colors()
        bar = self.node_list.verticalScrollBar()
        scroll_pos = bar.value()
        selected = None
        item = self.node_list.currentItem()
        if item:
            selected = item.data(0, Qt.ItemDataRole.UserRole)

        self.node_list.clear()
        restore_item = None
        for node in nodes:
            unread = self.engine.unread_count(node.key)
            badge = f"  [{unread}]" if unread else ""
            text = f"● {node.name}  ({node.ip}){badge}"
            top = QTreeWidgetItem([text])
            top.setData(0, Qt.ItemDataRole.UserRole, node.key)
            color = QColor(colors["online"] if node.online else colors["offline"])
            top.setForeground(0, color)
            self.node_list.addTopLevelItem(top)
            if node.key == selected:
                restore_item = top
            node_rooms = rooms_by_hub.get(node.key, [])
            for r in node_rooms:
                rk = self._room_key(r)
                runread = self.engine.unread_count(rk)
                rbadge = f"  [{runread}]" if runread else ""
                child = QTreeWidgetItem(
                    [f"[комната] {r.get('room', '')} ({len(r.get('members') or [])}){rbadge}"])
                child.setData(0, Qt.ItemDataRole.UserRole, rk)
                child.setForeground(0, color)
                top.addChild(child)
                if rk == selected:
                    restore_item = child
            if node_rooms:
                top.setExpanded(True)  # хаб с комнатами развёрнут по умолчанию

        for r in orphan_rooms:
            rk = self._room_key(r)
            runread = self.engine.unread_count(rk)
            rbadge = f"  [{runread}]" if runread else ""
            text = f"[комната] {r.get('room', '')} (через {r.get('hub_key', '')}){rbadge}"
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.ItemDataRole.UserRole, rk)
            item.setForeground(0, QColor(colors["offline"]))
            self.node_list.addTopLevelItem(item)
            if rk == selected:
                restore_item = item

        for ip, hostname in sorted(scanned.items(),
                                   key=lambda kv: tuple(int(p) for p in kv[0].split("."))):
            text = f"◌ {ip}  {hostname or ''} — нет мессенджера"
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.ItemDataRole.UserRole, f"host:{ip}")
            item.setForeground(0, QColor(colors["host"]))
            self.node_list.addTopLevelItem(item)
            if f"host:{ip}" == selected:
                restore_item = item

        if self.node_list.topLevelItemCount() == 0:
            item = QTreeWidgetItem(["Поиск узлов и устройств…"])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.node_list.addTopLevelItem(item)
        if restore_item is not None:
            self.node_list.setCurrentItem(restore_item)
        bar.setValue(scroll_pos)

    def _select(self, item, column: int = 0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data.startswith("host:"):
            ip = data[5:]
            self.chat_panel.show_hint(f"{ip} — на устройстве нет мессенджера")
        else:
            self.chat_panel.set_key(data)

    def _tree_context_menu(self, pos):
        item = self.node_list.itemAt(pos)
        if not item:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        menu = QMenu(self)
        if key.startswith("room:"):
            menu.addAction("Покинуть комнату",
                           lambda: self._leave_room(key))
        else:
            node = self.engine.node_by_key(key)
            if node and node.ip in self.engine._manual_peers():
                menu.addAction("Удалить из списка",
                               lambda: self._remove_peer(node.ip))
        if menu.actions():
            menu.exec(self.node_list.viewport().mapToGlobal(pos))

    def _leave_room(self, key: str):
        hub_key, _, room = key[len("room:"):].rpartition("/")
        self.engine.leave_room(hub_key, room)
        self._sig = None
        self.refresh()

    def _remove_peer(self, ip: str):
        self.engine.remove_manual_peer(ip)
        self._sig = None
        self.refresh()

    def _find_tree_item(self, key: str):
        for i in range(self.node_list.topLevelItemCount()):
            top = self.node_list.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole) == key:
                return top
            for j in range(top.childCount()):
                child = top.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == key:
                    return child
        return None

    def select_chat(self, key: str):
        item = self._find_tree_item(key)
        if item is not None:
            self.node_list.setCurrentItem(item)
            self._select(item)
            return
        self.chat_panel.set_key(key)

    def closeEvent(self, event):
        if self._quitting or not self.tray.isVisible():
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(
                "LAN Messenger",
                "Приложение свёрнуто в трей и продолжает принимать сообщения.",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )
