import ipaddress
import threading
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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


class MainWindow(QMainWindow):
    message_received = pyqtSignal(str)   # key чата (мост из сетевых потоков)
    host_found = pyqtSignal(str, str)    # ip, hostname (живые строки скана)
    scan_done = pyqtSignal()
    tunnel_error = pyqtSignal(str, str)  # key, текст ошибки туннеля
    reminder = pyqtSignal(str, str)      # заголовок, текст напоминания

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

        self.node_list = QListWidget()
        self.node_list.itemClicked.connect(self._select)

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
        left_layout.addLayout(rejected_row)
        left_layout.addWidget(self.sort_btn)
        left_layout.addWidget(self.node_list, 1)
        left_layout.addWidget(self.scan_btn)
        left_layout.addLayout(bottom_row)
        left = QWidget()
        left.setLayout(left_layout)

        # --- правая панель ---
        self.chat_panel = ChatPanel(engine)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.chat_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 560])

        from .calendar_window import CalendarWindow

        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Чаты")
        self.tabs.addTab(CalendarWindow(engine), "Календарь")
        self.setCentralWidget(self.tabs)

        self._setup_tray()
        self.engine.on_message_event = lambda key, msg: self.message_received.emit(key)
        self.message_received.connect(self._on_new_message)
        self.host_found.connect(self._on_host_found)
        self.scan_done.connect(self._on_scan_done)
        self.engine.on_tunnel_error = lambda key, err: self.tunnel_error.emit(key, err)
        self.tunnel_error.connect(self._show_tunnel_error)
        self.engine.on_reminder = self._on_reminder
        self.reminder.connect(self._show_reminder)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    # --- трей ---

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
        self.tray.messageClicked.connect(self._show_from_tray)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
        self.refresh()
        if self._muted():
            return
        if self.isVisible() and self.chat_panel.key == key and self.isActiveWindow():
            return
        node = self.engine.node_by_key(key)
        name = node.name if node else key
        msgs = self.engine.chat(key)
        text = msgs[-1].text[:200] if msgs else ""
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

    # --- список узлов и устройств ---

    def refresh(self):
        self._refresh_rejected()
        nodes = self._sorted_nodes(self.engine.nodes())
        node_ips = {n.ip for n in nodes}
        scanned = {ip: hn for ip, hn in self._scanned.items() if ip not in node_ips}
        signature = (
            tuple((n.key, n.name, n.online, self.engine.unread_count(n.key))
                  for n in nodes),
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
            selected = item.data(Qt.ItemDataRole.UserRole)

        self.node_list.clear()
        for node in nodes:
            unread = self.engine.unread_count(node.key)
            badge = f"  [{unread}]" if unread else ""
            text = f"● {node.name}  ({node.ip}){badge}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, node.key)
            item.setForeground(QColor(colors["online"] if node.online
                                      else colors["offline"]))
            self.node_list.addItem(item)
            if node.key == selected:
                self.node_list.setCurrentItem(item)

        for ip, hostname in sorted(scanned.items(),
                                   key=lambda kv: tuple(int(p) for p in kv[0].split("."))):
            text = f"◌ {ip}  {hostname or ''} — нет мессенджера"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, f"host:{ip}")
            item.setForeground(QColor(colors["host"]))
            self.node_list.addItem(item)
            if f"host:{ip}" == selected:
                self.node_list.setCurrentItem(item)

        if self.node_list.count() == 0:
            item = QListWidgetItem("Поиск узлов и устройств…")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.node_list.addItem(item)
        bar.setValue(scroll_pos)

    def _select(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data.startswith("host:"):
            ip = data[5:]
            self.chat_panel.show_hint(f"{ip} — на устройстве нет мессенджера")
        else:
            self.chat_panel.set_key(data)

    def select_chat(self, key: str):
        for i in range(self.node_list.count()):
            item = self.node_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == key:
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
