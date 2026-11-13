import sys

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from ..core import autostart
from . import theme
from .switch import Switch

TS_KEY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"


def set_shadow_policy(enabled: bool) -> bool:
    """Shadow=2 (теневое с согласием) / Shadow=0 (запрещено).
    Запуск через runas — Windows покажет UAC."""
    if sys.platform != "win32":
        return False
    import ctypes

    value = 2 if enabled else 0
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "reg",
        f'add "{TS_KEY}" /v Shadow /t REG_DWORD /d {value} /f',
        None, 0)
    return rc > 32


class SettingsDialog(QDialog):
    """General settings split into scrollable zones; actions stay outside pages."""

    def __init__(self, parent: QWidget, engine):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Настройки")
        self.setMinimumSize(320, 220)
        available = self.screen().availableGeometry()
        self.resize(min(720, max(320, available.width() - 40)),
                    min(480, max(240, available.height() - 60)))

        # --- общие ---
        self.autostart_box = Switch("Запускать с Windows")
        self.autostart_box.setChecked(autostart.is_enabled())
        self.theme_box = Switch("Тёмная тема")
        self.theme_box.setChecked(engine.theme == "dark")
        self.url_edit = QLineEdit(engine.update_url)
        self.url_edit.setPlaceholderText("Ссылка на новые версии (для уведомлений)")
        self.text_edit = QLineEdit(engine.update_text)
        self.text_edit.setPlaceholderText("Текст уведомления: {name} {ver} {my} {url}")

        # --- сеть ---
        self.acceptall_box = Switch("Принимать из любых сетей (VPN/белый IP)")
        self.acceptall_box.setChecked(engine.accept_all())

        # --- шифрование ---
        self.tls_box = Switch("TLS-канал к узлам (рекомендуется)")
        self.tls_box.setChecked(getattr(engine, "tls_enabled", True))
        self.psk_edit = QLineEdit(getattr(engine, "network_psk", ""))
        self.psk_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.psk_edit.setPlaceholderText("Сетевой пароль (PSK для P2P)")
        fps = getattr(engine, "known_fingerprints", None) or {}
        if fps:
            fp_text = "\n".join(f"{key}: {fp[:16]}…" for key, fp in fps.items())
        else:
            fp_text = "нет"
        fp_label = QLabel(f"Известные отпечатки:\n{fp_text}")
        fp_label.setWordWrap(True)

        # --- фичефлаги ---
        self.rdp_box = Switch("RDP-туннель (кнопка в чате + приём туннелей)")
        self.rdp_box.setChecked(engine.rdp_enabled)
        self.shadow_box = Switch("RDP: совместный сеанс (не выкидывать пользователя)")
        self.shadow_box.setChecked(engine.shadow_rdp)
        self.shadow_btn = QPushButton()
        self.shadow_btn.clicked.connect(self._toggle_shadow_policy)
        self._shadow_btn_update()
        self.scan_box = Switch("Сканер сети (кнопка «Обновить скан сети»)")
        self.scan_box.setChecked(engine.scan_enabled)
        self.hub_box = Switch("Принимать комнаты (быть хабом)")
        self.hub_box.setChecked(getattr(engine, "hub_enabled", True))
        self.push_box = Switch("Уведомления на телефон (ntfy, порт 8087)")
        self.push_box.setChecked(engine.push_status()[0])
        self.manual_ip_box = Switch("Ручное добавление по IP")
        self.manual_ip_box.setChecked(getattr(engine, "manual_ip_enabled", False))
        self.room_join_box = Switch("Вход в комнаты (комната@IP-хаба)")
        self.room_join_box.setChecked(getattr(engine, "room_join_enabled", False))
        self.notify_box = Switch("Информационные уведомления (новые версии у узлов)")
        self.notify_box.setChecked(engine.notify_update)
        self.test_btn = QPushButton("Проверить уведомление")
        self.test_btn.setToolTip("Показать уведомление о версии так, как его увидят другие пользователи.")
        self.test_btn.clicked.connect(self._test_notification)
        rdp_controls = [self.rdp_box, self.shadow_box, self.shadow_btn]

        if sys.platform == "darwin":
            self.autostart_box.setText("Автозапуск (только Windows)")
            for control in (self.autostart_box, self.rdp_box, self.shadow_box,
                            self.shadow_btn, self.scan_box):
                control.setEnabled(False)
                control.setToolTip("Недоступно в экспериментальной версии для macOS")
            note = QLabel("macOS: RDP, автозапуск и сканер сети пока недоступны. "
                          "Автоматическое обнаружение мессенджеров работает отдельно.")
            note.setWordWrap(True)
            rdp_controls.append(note)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._add_zone("Общие", [self.autostart_box, self.theme_box])
        self._add_zone("Сеть", [self.acceptall_box, self.scan_box, self.hub_box,
                                self.manual_ip_box, self.room_join_box])
        self._add_zone("Защита", [self.tls_box, QLabel("Сетевой пароль (PSK для P2P):"),
                                  self.psk_edit, QLabel("Одинаковый у всех участников сети."), fp_label])
        self._add_zone("RDP", rdp_controls)
        self._add_zone("Уведомления", [self.push_box, self.notify_box,
                                       QLabel("Ссылка на новые версии:"), self.url_edit,
                                       QLabel("Текст уведомления о версии:"), self.text_edit, self.test_btn])
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self.buttons.accepted.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.buttons)

    def _add_zone(self, title, controls):
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        for control in controls:
            if isinstance(control, QLabel):
                control.setWordWrap(True)
                control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(control)
        layout.addStretch(1)
        page.setWidget(content)
        self.tabs.addTab(page, title)

    def _test_notification(self):
        """Показать, как выглядит уведомление о новой версии у других."""
        from .. import __version__
        from ..core.engine import DEFAULT_UPDATE_URL

        engine = self.engine
        url = self.url_edit.text().strip() or DEFAULT_UPDATE_URL
        text = self.text_edit.text().strip()
        try:
            text = text.format(name=engine.name, ver=__version__,
                               my="0.0.0", url=url)
        except (KeyError, IndexError):
            QMessageBox.warning(self, "Тест уведомления",
                                "В шаблоне ошибка. Доступны: {name} {ver} {my} {url}")
            return
        engine.add_notification(f"Новая версия {__version__} (тест)", text,
                                link=url, kind="update")
        QMessageBox.information(self, "Тест уведомления",
                                "Отправлено в центр уведомлений — "
                                "смотрите колокольчик 🔔 справа вверху.")

    def _shadow_btn_update(self):
        if sys.platform == "darwin":
            self.shadow_btn.setText("Совместный сеанс: только Windows")
            return
        from ..core.tunnel import _shadow_allowed

        if _shadow_allowed():
            self.shadow_btn.setText("Запретить совместный сеанс")
        else:
            self.shadow_btn.setText("Разрешить совместный сеанс")
        self.shadow_btn.setToolTip(
            "Политика Shadow в реестре. Изменение через UAC "
            "(права администратора).")

    def _toggle_shadow_policy(self):
        from ..core.tunnel import _shadow_allowed

        enable = not _shadow_allowed()
        if set_shadow_policy(enable):
            QMessageBox.information(
                self, "Совместное подключение",
                ("Команда отправлена. Если подтвердили UAC — теперь "
                 + ("РАЗРЕШЕНО." if enable else "ЗАПРЕЩЕНО.")))
        else:
            QMessageBox.warning(
                self, "Совместное подключение",
                "Не удалось выполнить (отменён UAC?). Вручную:\n"
                f"reg add \"{TS_KEY}\" /v Shadow /t REG_DWORD "
                f"/d {2 if enable else 0} /f")
        self._shadow_btn_update()

    def _apply(self):
        engine = self.engine
        if sys.platform == "win32":
            if self.autostart_box.isChecked():
                autostart.enable()
            else:
                autostart.disable()

        engine.set_accept_all(self.acceptall_box.isChecked())
        if hasattr(engine, "set_tls_enabled"):
            engine.set_tls_enabled(self.tls_box.isChecked())
        if hasattr(engine, "set_network_psk"):
            engine.set_network_psk(self.psk_edit.text().strip())
        engine.set_feature("rdp_enabled", self.rdp_box.isChecked())
        engine.set_feature("shadow_rdp", self.shadow_box.isChecked())
        engine.set_feature("scan_enabled", self.scan_box.isChecked())
        engine.set_feature("hub_enabled", self.hub_box.isChecked())
        engine.set_push(self.push_box.isChecked())
        engine.set_feature("manual_ip_enabled", self.manual_ip_box.isChecked())
        engine.set_feature("room_join_enabled", self.room_join_box.isChecked())
        engine.set_notify_update(self.notify_box.isChecked())
        url = self.url_edit.text().strip()
        if url and url != engine.update_url:
            engine.set_update_url(url)
        text = self.text_edit.text().strip()
        if text and text != engine.update_text:
            engine.set_update_text(text)

        theme_name = "dark" if self.theme_box.isChecked() else "light"
        theme.apply(QApplication.instance(), theme_name)
        engine.set_theme(theme_name)

        self.accept()
