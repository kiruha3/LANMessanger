"""Светлая/тёмная тема: QSS на всё приложение + цвета пузырей чата."""

_current = "light"

DARK_QSS = """
QWidget { background: #2b2b2b; color: #e6e6e6; }
QLineEdit, QTextBrowser, QListWidget {
    background: #1f1f1f; color: #e6e6e6;
    border: 1px solid #444; border-radius: 4px;
}
QPushButton {
    background: #3c3c3c; color: #e6e6e6;
    border: 1px solid #555; border-radius: 4px; padding: 4px 10px;
}
QPushButton:hover { background: #4a4a4a; }
QPushButton:disabled { color: #777; }
QCheckBox, QLabel { background: transparent; }
QMenu { background: #2b2b2b; color: #e6e6e6; border: 1px solid #555; }
QMenu::item:selected { background: #4a4a4a; }
QToolTip { background: #1f1f1f; color: #e6e6e6; border: 1px solid #555; }
QSplitter::handle { background: #444; }
"""

BUBBLES = {
    "light": {"out": "#d6eaff", "in": "#eeeeee", "ts": "#888888"},
    "dark": {"out": "#2b5278", "in": "#3a3a3a", "ts": "#999999"},
}


def current() -> str:
    return _current


def apply(app, theme_name: str):
    global _current
    _current = "dark" if theme_name == "dark" else "light"
    app.setStyleSheet(DARK_QSS if _current == "dark" else "")


def bubbles() -> dict:
    return BUBBLES[_current]
