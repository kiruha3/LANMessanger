from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QCheckBox

from . import theme

TRACK_W, TRACK_H = 40, 20
KNOB = 16
TEXT_GAP = 10

_OFF_COLOR = {"light": "#b9b9b9", "dark": "#555555"}


class Switch(QCheckBox):
    """Переключатель-свитч: рисуется кодом, без файлов и картинок."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)

    def sizeHint(self) -> QSize:
        w = TRACK_W + (self.fontMetrics().horizontalAdvance(self.text()) + TEXT_GAP
                       if self.text() else 0)
        return QSize(w + 6, 26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        top = (self.height() - TRACK_H) / 2

        track = QRectF(2, top, TRACK_W, TRACK_H)
        painter.setPen(Qt.PenStyle.NoPen)
        off = QColor(_OFF_COLOR["dark" if theme.current() == "dark" else "light"])
        painter.setBrush(QColor(theme.ACCENT) if on else off)
        painter.drawRoundedRect(track, TRACK_H / 2, TRACK_H / 2)

        knob_x = track.right() - KNOB - 2 if on else track.left() + 2
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, top + 2, KNOB, KNOB))

        if self.text():
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(
                self.rect().adjusted(TRACK_W + TEXT_GAP, 0, 0, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )
        painter.end()
