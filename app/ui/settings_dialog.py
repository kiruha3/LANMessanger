from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from ..core import autostart
from . import theme


class SettingsDialog(QDialog):
    """Настройки: общие, сеть, фичефлаги."""

    def __init__(self, parent: QWidget, engine):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(340)

        # --- общие ---
        general = QGroupBox("Общие")
        self.autostart_box = QCheckBox("Запускать с Windows")
        self.autostart_box.setChecked(autostart.is_enabled())
        self.theme_box = QCheckBox("Тёмная тема")
        self.theme_box.setChecked(engine.theme == "dark")
        gl = QVBoxLayout(general)
        gl.addWidget(self.autostart_box)
        gl.addWidget(self.theme_box)

        # --- сеть ---
        net = QGroupBox("Сеть")
        self.acceptall_box = QCheckBox("Принимать из любых сетей (VPN/белый IP)")
        self.acceptall_box.setChecked(engine.accept_all())
        nl = QVBoxLayout(net)
        nl.addWidget(self.acceptall_box)

        # --- фичефлаги ---
        feat = QGroupBox("Функции")
        self.rdp_box = QCheckBox("RDP-туннель (кнопка в чате + приём туннелей)")
        self.rdp_box.setChecked(engine.rdp_enabled)
        self.scan_box = QCheckBox("Сканер сети (кнопка «Обновить скан сети»)")
        self.scan_box.setChecked(engine.scan_enabled)
        self.push_box = QCheckBox("Уведомления на телефон (ntfy, порт 8087)")
        self.push_box.setChecked(engine.push_status()[0])
        fl = QVBoxLayout(feat)
        fl.addWidget(self.rdp_box)
        fl.addWidget(self.scan_box)
        fl.addWidget(self.push_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addWidget(general)
        layout.addWidget(net)
        layout.addWidget(feat)
        layout.addWidget(buttons)

    def _apply(self):
        engine = self.engine
        if self.autostart_box.isChecked():
            autostart.enable()
        else:
            autostart.disable()

        engine.set_accept_all(self.acceptall_box.isChecked())
        engine.set_feature("rdp_enabled", self.rdp_box.isChecked())
        engine.set_feature("scan_enabled", self.scan_box.isChecked())
        engine.set_push(self.push_box.isChecked())

        theme_name = "dark" if self.theme_box.isChecked() else "light"
        theme.apply(QApplication.instance(), theme_name)
        engine.set_theme(theme_name)

        self.accept()
