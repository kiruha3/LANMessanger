from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                            QVBoxLayout, QWidget)

from ..core.ai_plugins import PLUGINS
from . import theme


def panel_title(text):
    label = QLabel(text)
    label.setStyleSheet("font-size: 18px; font-weight: bold;")
    label.setWordWrap(True)
    return label


class AIPluginCatalog(QWidget):
    configure = pyqtSignal(str)
    open_chat_tools = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        colors = theme.bubbles()
        panel = "#202a35" if theme.current() == "dark" else "#f4f7fa"
        self.setStyleSheet(f"QFrame[pluginPanel=true] {{ background: {panel}; border: 1px solid {colors['in']}; border-radius: 9px; }}")

        navigation = QFrame()
        navigation.setProperty("pluginPanel", True)
        navigation.setFixedWidth(180)
        nav = QVBoxLayout(navigation)
        nav.addWidget(panel_title("Плагины"))
        self.filters = {}
        for category in ("Каталог", "Изображения", "Чаты"):
            button = QPushButton(category)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, current=category: self._filter(current))
            self.filters[category] = button
            nav.addWidget(button)
        nav.addStretch(1)
        nav.addWidget(QLabel(f"Встроено: {len(PLUGINS)}"))
        layout.addWidget(navigation)

        center = QVBoxLayout()
        center.addWidget(panel_title("Инструменты для ваших задач"))
        subtitle = QLabel("Выберите, какие инструменты агент может использовать в каждом чате.")
        subtitle.setWordWrap(True)
        center.addWidget(subtitle)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти плагин…")
        self.search.setAccessibleName("Поиск плагинов")
        self.search.textChanged.connect(lambda _text: self._filter(self.category))
        center.addWidget(self.search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setContentsMargins(0, 4, 2, 4)
        self.cards = {}
        for plugin_id, (title, summary, category, _tools) in PLUGINS.items():
            card = QFrame()
            card.setProperty("pluginPanel", True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.addWidget(panel_title(title))
            description = QLabel(summary)
            description.setWordWrap(True)
            card_layout.addWidget(description)
            action_row = QHBoxLayout()
            badge = QLabel(f"Встроенный · {category}")
            badge.setWordWrap(True)
            action_row.addWidget(badge, 1)
            action = QPushButton("Подробнее")
            action.setAccessibleName(f"Подробнее: {title}")
            action.clicked.connect(lambda _checked, key=plugin_id: self._select(key))
            action_row.addWidget(action)
            card_layout.addLayout(action_row)
            cards_layout.addWidget(card)
            self.cards[plugin_id] = card
        self.empty = QLabel("Ничего не найдено")
        cards_layout.addWidget(self.empty)
        cards_layout.addStretch(1)
        scroll.setWidget(cards)
        center.addWidget(scroll, 1)
        layout.addLayout(center, 1)

        details = QFrame()
        details.setProperty("pluginPanel", True)
        details.setFixedWidth(245)
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        self.detail_title = panel_title("")
        self.detail_text = QLabel()
        self.detail_text.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_text.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_text)
        detail_layout.addStretch(1)
        self.settings_button = QPushButton("Аккаунт и модель")
        self.settings_button.clicked.connect(lambda: self.configure.emit(self.selected))
        detail_layout.addWidget(self.settings_button)
        open_tools = QPushButton("Инструменты текущего чата")
        open_tools.clicked.connect(self.open_chat_tools.emit)
        detail_layout.addWidget(open_tools)
        layout.addWidget(details)
        self._filter("Каталог")
        self._select("images")

    def _filter(self, category):
        self.category = category
        for label, button in self.filters.items():
            button.setChecked(label == category)
        search = self.search.text().strip().casefold()
        found = False
        for plugin_id, card in self.cards.items():
            title, summary, group, _tools = PLUGINS[plugin_id]
            visible = (category in ("Каталог", group) and search in f"{title} {summary} {group}".casefold())
            card.setVisible(visible)
            found |= visible
        self.empty.setVisible(not found)

    def _select(self, plugin_id):
        self.selected = plugin_id
        title, summary, _group, _tools = PLUGINS[plugin_id]
        self.detail_title.setText(title)
        hint = ("Аккаунт и модель задаются для текущего текстового подключения. Ручная генерация работает отдельно."
                if plugin_id == "images" else "Агент обращается к другим чатам только по вашему запросу.")
        self.detail_text.setText(f"{summary}.\n\n{hint}")
        self.settings_button.setVisible(plugin_id == "images")
