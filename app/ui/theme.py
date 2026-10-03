"""Светлая/тёмная тема: QSS на всё приложение + цвета пузырей чата."""

import os

_current = "light"

ACCENT = "#3390ec"

LIGHT_QSS = """
QWidget { background: #f4f4f4; color: #1f1f1f; }
QLineEdit, QTextBrowser, QListWidget {
    background: #ffffff; color: #1f1f1f;
    border: 1px solid #d0d0d0; border-radius: 6px;
}
QLineEdit { padding: 3px 6px; }
QLineEdit:focus, QTextBrowser:focus, QListWidget:focus { border: 1px solid #3390ec; }
QListWidget::item { padding: 4px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #3390ec; color: #ffffff; }
QListWidget::item:hover:!selected { background: #e8f1fc; }
QPushButton {
    background: #ffffff; color: #1f1f1f;
    border: 1px solid #c8c8c8; border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { background: #eef4fb; border-color: #3390ec; }
QPushButton:pressed { background: #dcebfb; }
QPushButton:disabled { color: #999999; background: #f0f0f0; }
QCheckBox, QLabel { background: transparent; }
    width: 15px; height: 15px;
    border: 1px solid #a0a0a0; border-radius: 3px; background: #ffffff;
}
QMenu { background: #ffffff; color: #1f1f1f; border: 1px solid #d0d0d0; }
QMenu::item:selected { background: #3390ec; color: #ffffff; }
QToolTip { background: #ffffff; color: #1f1f1f; border: 1px solid #c8c8c8; }
QSplitter::handle { background: #e0e0e0; }
QCalendarWidget QHeaderView::section {
    background: #e8f1fc; color: #1f1f1f; border: none; padding: 4px;
    font-weight: bold; border-bottom: 2px solid #3390ec;
}
QCalendarWidget QTableView { background: #ffffff; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #c8c8c8; border-radius: 5px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #3390ec; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

DARK_QSS = """
QWidget { background: #2b2b2b; color: #e6e6e6; }
QLineEdit, QTextBrowser, QListWidget {
    background: #1f1f1f; color: #e6e6e6;
    border: 1px solid #444444; border-radius: 6px;
}
QLineEdit { padding: 3px 6px; }
QLineEdit:focus, QTextBrowser:focus, QListWidget:focus { border: 1px solid #3390ec; }
QListWidget::item { padding: 4px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #3390ec; color: #ffffff; }
QListWidget::item:hover:!selected { background: #383838; }
QPushButton {
    background: #3c3c3c; color: #e6e6e6;
    border: 1px solid #555555; border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { background: #4a4a4a; border-color: #3390ec; }
QPushButton:pressed { background: #2b5278; }
QPushButton:disabled { color: #777777; background: #333333; }
QCheckBox, QLabel { background: transparent; }
    width: 15px; height: 15px;
    border: 1px solid #666666; border-radius: 3px; background: #1f1f1f;
}
QMenu { background: #2b2b2b; color: #e6e6e6; border: 1px solid #555555; }
QMenu::item:selected { background: #3390ec; color: #ffffff; }
QToolTip { background: #1f1f1f; color: #e6e6e6; border: 1px solid #555555; }
QSplitter::handle { background: #444444; }
QCalendarWidget QHeaderView::section {
    background: #26334a; color: #e6e6e6; border: none; padding: 4px;
    font-weight: bold; border-bottom: 2px solid #3390ec;
}
QCalendarWidget QTableView { background: #1f1f1f; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #555555; border-radius: 5px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #3390ec; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

BUBBLES = {
    "light": {"out": "#cde6ff", "in": "#eeeeee", "ts": "#888888",
              "accent": "#3390ec", "text": "#1f1f1f"},
    "dark": {"out": "#2b5278", "in": "#3a3a3a", "ts": "#999999",
             "accent": "#6ab3f0", "text": "#e6e6e6"},
}

NODE_COLORS = {
    "light": {"online": "#1a7f37", "offline": "#999999", "host": "#bbbbbb"},
    "dark": {"online": "#3fb950", "offline": "#777777", "host": "#5a5a5a"},
}


def current() -> str:
    return _current


def apply(app, theme_name: str):
    global _current
    _current = "dark" if theme_name == "dark" else "light"
    app.setStyleSheet(DARK_QSS if _current == "dark" else LIGHT_QSS)


def bubbles() -> dict:
    return BUBBLES[_current]


def node_colors() -> dict:
    return NODE_COLORS[_current]
