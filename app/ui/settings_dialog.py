from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import autostart
from . import theme
from .switch import Switch

TS_KEY = r"HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"


def enable_shadow_policy() -> bool:
    """Shadow=2 (теневое подключение с согласием пользователя).
    Запуск через runas — Windows покажет UAC."""
    import ctypes

    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "reg",
        f'add "{TS_KEY}" /v Shadow /t REG_DWORD /d 2 /f',
        None, 0)
    return rc > 32


class SettingsDialog(QDialog):
    """Настройки: общие, сеть, фичефлаги."""

    def __init__(self, parent: QWidget, engine):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(340)

        # --- общие ---
        general = QGroupBox("Общие")
        self.autostart_box = Switch("Запускать с Windows")
        self.autostart_box.setChecked(autostart.is_enabled())
        self.theme_box = Switch("Тёмная тема")
        self.theme_box.setChecked(engine.theme == "dark")
        self.url_edit = QLineEdit(engine.update_url)
        self.url_edit.setPlaceholderText("Ссылка на новые версии (для уведомлений)")
        gl = QVBoxLayout(general)
        gl.addWidget(self.autostart_box)
        gl.addWidget(self.theme_box)
        gl.addWidget(self.url_edit)

        # --- сеть ---
        net = QGroupBox("Сеть")
        self.acceptall_box = Switch("Принимать из любых сетей (VPN/белый IP)")
        self.acceptall_box.setChecked(engine.accept_all())
        nl = QVBoxLayout(net)
        nl.addWidget(self.acceptall_box)

        # --- фичефлаги ---
        feat = QGroupBox("Функции")
        self.rdp_box = Switch("RDP-туннель (кнопка в чате + приём туннелей)")
        self.rdp_box.setChecked(engine.rdp_enabled)
        self.shadow_box = Switch("RDP: совместный сеанс (не выкидывать пользователя)")
        self.shadow_box.setChecked(engine.shadow_rdp)
        self.shadow_btn = QPushButton("Разрешить совместное подключение на этом ПК…")
        self.shadow_btn.setToolTip(
            "Разовая настройка: параметр Shadow=2 в реестре.\n"
            "Понадобится подтверждение UAC (права администратора).")
        self.shadow_btn.clicked.connect(self._enable_shadow)
        self.scan_box = Switch("Сканер сети (кнопка «Обновить скан сети»)")
        self.scan_box.setChecked(engine.scan_enabled)
        self.push_box = Switch("Уведомления на телефон (ntfy, порт 8087)")
        self.push_box.setChecked(engine.push_status()[0])
        fl = QVBoxLayout(feat)
        fl.addWidget(self.rdp_box)
        fl.addWidget(self.shadow_box)
        fl.addWidget(self.shadow_btn)
        fl.addWidget(self.scan_box)
        fl.addWidget(self.push_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addWidget(general)
        layout.addWidget(net)
        layout.addWidget(feat)
        layout.addWidget(buttons)

    def _enable_shadow(self):
        if enable_shadow_policy():
            QMessageBox.information(
                self, "Совместное подключение",
                "Команда отправлена. Если подтвердили UAC — "
                "теневое подключение к этому ПК разрешено.")
        else:
            QMessageBox.warning(
                self, "Совместное подключение",
                "Не удалось выполнить (отменён UAC?). Можно вручную:\n"
                f"reg add \"{TS_KEY}\" /v Shadow /t REG_DWORD /d 2 /f")

    def _apply(self):
        engine = self.engine
        if self.autostart_box.isChecked():
            autostart.enable()
        else:
            autostart.disable()

        engine.set_accept_all(self.acceptall_box.isChecked())
        engine.set_feature("rdp_enabled", self.rdp_box.isChecked())
        engine.set_feature("shadow_rdp", self.shadow_box.isChecked())
        engine.set_feature("scan_enabled", self.scan_box.isChecked())
        engine.set_push(self.push_box.isChecked())

        theme_name = "dark" if self.theme_box.isChecked() else "light"
        theme.apply(QApplication.instance(), theme_name)
        engine.set_theme(theme_name)
        url = self.url_edit.text().strip()
        if url and url != engine.update_url:
            engine.update_url = url
            engine.save_settings()

        self.accept()
