"""Empty-chat setup and a reference snapshot supplied by the user."""
import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


def load_catalog():
    return json.loads((Path(__file__).resolve().parents[1] / "data" / "ai_catalog.json").read_text(encoding="utf-8"))


def _label(text, bold=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if bold:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    return label


def _price(value):
    return f"{value:,g}".replace(",", " ").replace(".", ",")


class AIWelcome(QWidget):
    connect_requested = pyqtSignal()
    expansion_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(_label("Подключите модель и начните чат", True))
        layout.addWidget(_label(
            "1. Откройте «Подключение модели» и выберите «Совместимый API».\n"
            "2. Укажите URL API (например, https://server/v1) и введите свой ключ.\n"
            "3. Нажмите «Загрузить модели» и выберите доступную модель. Если список недоступен, введите точный ID вручную.\n"
            "4. Напишите задачу в поле внизу. Агент будет работать в папке приложения."))
        self.connect_btn = QPushButton("Настроить подключение")
        self.connect_btn.clicked.connect(self.connect_requested)
        layout.addWidget(self.connect_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(_label("Для встроенных провайдеров можно выбрать API-ключ или поддерживаемый вход по аккаунту."))
        catalog = load_catalog()
        layout.addWidget(_label("Справочник моделей по разделам", True))
        layout.addWidget(_label(
            f"Цены из {catalog['source']} на {catalog['date']}, ₽ за 1 млн токенов: вход / кеш / выход. "
            "Это справочник; точные ID и доступность моделей загружаются из вашего API. "
            "Назначения ниже — ориентиры для пробного выбора, результат сравнивайте на своей задаче."))
        self.sections = []
        for section in catalog["sections"]:
            button = QPushButton(f"{section['title']} ({len(section['models'])})")
            button.setCheckable(True)
            layout.addWidget(button)
            body = QWidget()
            rows = QVBoxLayout(body)
            rows.setContentsMargins(12, 4, 6, 8)
            rows.setSpacing(10)
            for model in section["models"]:
                card = QWidget()
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(0, 0, 0, 0)
                card_layout.setSpacing(3)
                card_layout.addWidget(_label(model["name"], True))
                card_layout.addWidget(_label("Вход: " + _price(model["input"]) + " ₽   ·   Кеш: "
                                            + _price(model["cache"]) + " ₽   ·   Выход: "
                                            + _price(model["output"]) + " ₽"))
                card_layout.addWidget(_label(model["use"]))
                rows.addWidget(card)
            body.hide()
            button.toggled.connect(lambda checked, widget=body: self._toggle(widget, checked))
            layout.addWidget(body)
            self.sections.append((button, body))

    def _toggle(self, body, checked):
        self.expansion_changed.emit()
        body.setVisible(checked)
