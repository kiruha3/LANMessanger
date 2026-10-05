"""Светлая/тёмная тема: QSS на всё приложение + цвета пузырей чата."""

import os

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

_current = "light"

ACCENT = "#0d9488"

LIGHT_QSS = """
QWidget { background: #f4f4f4; color: #1f1f1f; }
QLineEdit, QTextBrowser, QListWidget {
    background: #ffffff; color: #1f1f1f;
    border: 1px solid #d0d0d0; border-radius: 6px;
}
QLineEdit { padding: 3px 6px; }
QLineEdit:focus, QTextBrowser:focus, QListWidget:focus { border: 1px solid #0d9488; }
QListWidget::item { padding: 4px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #0d9488; color: #ffffff; }
QListWidget::item:hover:!selected { background: #e0f2f0; }
QTreeWidget {
    background: #ffffff; color: #1f1f1f;
    border: 1px solid #d0d0d0; border-radius: 6px;
}
QTreeWidget::item:selected { background: #0d9488; color: #ffffff; }
QTreeWidget::item:hover:!selected { background: #e0f2f0; }
QPushButton {
    background: #ffffff; color: #1f1f1f;
    border: 1px solid #c8c8c8; border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { background: #ecf7f5; border-color: #0d9488; }
QPushButton:pressed { background: #c8e8e4; }
QPushButton:checked { background: #0d9488; color: #ffffff; border-color: #0d9488; }
QPushButton:disabled { color: #999999; background: #f0f0f0; }
QCheckBox, QLabel { background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #a0a0a0; border-radius: 3px; background: #ffffff;
}
QMenu { background: #ffffff; color: #1f1f1f; border: 1px solid #d0d0d0; }
QMenu::item:selected { background: #0d9488; color: #ffffff; }
QToolTip { background: #ffffff; color: #1f1f1f; border: 1px solid #c8c8c8; }
QSplitter::handle { background: #e0e0e0; }
QCalendarWidget QHeaderView::section {
    background: #e0f2f0; color: #1f1f1f; border: none; padding: 4px;
    font-weight: bold; border-bottom: 2px solid #0d9488;
}
QCalendarWidget QTableView { background: #ffffff; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #c8c8c8; border-radius: 5px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #0d9488; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

DARK_QSS = """
QWidget { background: #161a22; color: #d7dde5; }
QLineEdit, QTextBrowser, QListWidget {
    background: #1f2630; color: #d7dde5;
    border: 1px solid #2a3441; border-radius: 6px;
}
QLineEdit { padding: 3px 6px; }
QLineEdit:focus, QTextBrowser:focus, QListWidget:focus { border: 1px solid #14b8a6; }
QListWidget::item { padding: 4px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #0d9488; color: #ffffff; }
QListWidget::item:hover:!selected { background: #232c38; }
QTreeWidget {
    background: #1f2630; color: #d7dde5;
    border: 1px solid #2a3441; border-radius: 6px;
}
QTreeWidget::item:selected { background: #0d9488; color: #ffffff; }
QTreeWidget::item:hover:!selected { background: #232c38; }
QPushButton {
    background: #232c38; color: #d7dde5;
    border: 1px solid #2a3441; border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { background: #28323e; border-color: #14b8a6; }
QPushButton:pressed { background: #134e4a; }
QPushButton:checked { background: #0d9488; color: #ffffff; border-color: #14b8a6; }
QPushButton:disabled { color: #5b6672; background: #1c232c; }
QCheckBox, QLabel { background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #3a4552; border-radius: 3px; background: #1f2630;
}
QMenu { background: #1f2630; color: #d7dde5; border: 1px solid #2a3441; }
QMenu::item:selected { background: #0d9488; color: #ffffff; }
QToolTip { background: #1f2630; color: #d7dde5; border: 1px solid #2a3441; }
QSplitter::handle { background: #2a3441; }
QCalendarWidget QHeaderView::section {
    background: #16302e; color: #d7dde5; border: none; padding: 4px;
    font-weight: bold; border-bottom: 2px solid #14b8a6;
}
QCalendarWidget QTableView { background: #1f2630; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #3a4552; border-radius: 5px; min-height: 30px; min-width: 30px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #14b8a6; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

BUBBLES = {
    "light": {"out": "#cdeee9", "in": "#eeeeee", "ts": "#888888",
              "accent": "#0d9488", "text": "#1f1f1f"},
    "dark": {"out": "#134e4a", "in": "#232c38", "ts": "#8b95a1",
             "accent": "#2dd4bf", "text": "#d7dde5"},
}

NODE_COLORS = {
    "light": {"online": "#0d9488", "offline": "#999999", "host": "#bbbbbb"},
    "dark": {"online": "#2dd4bf", "offline": "#5b6672", "host": "#4a5560"},
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


def make_monitor_icon(color: str = "#8a8a8a", size: int = 20) -> QIcon:
    """Иконка-монитор для кнопки RDP (на Win10 нет цветных эмодзи — рисуем)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 20.0
    pen = QPen(QColor(color))
    pen.setWidthF(1.6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2 * s, 2.5 * s, 16 * s, 11 * s), 2 * s, 2 * s)
    p.drawLine(int(10 * s), int(13.5 * s), int(10 * s), int(16.5 * s))
    p.drawLine(int(6 * s), int(17 * s), int(14 * s), int(17 * s))
    p.end()
    return QIcon(pm)


def make_triangle_icon(direction: str = "left", color: str = "#8a8a8a",
                       size: int = 14) -> QIcon:
    """Треугольник-стрелка для кнопки сворачивания панели.

    Символы «◀/▶» на части систем (Win10) рендерятся пустым квадратом,
    поэтому рисуем кодом. direction: "left" (свернуть) или "right".
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 14.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    if direction == "left":
        pts = [(9.5, 3.0), (9.5, 11.0), (4.0, 7.0)]
    else:
        pts = [(4.5, 3.0), (4.5, 11.0), (10.0, 7.0)]
    poly = QPolygonF([QPointF(x * s, y * s) for x, y in pts])
    p.drawPolygon(poly)
    p.end()
    return QIcon(pm)
