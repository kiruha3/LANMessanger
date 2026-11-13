"""Scalable messenger artwork shared by the window, tray and Windows executable."""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap


def unread_badge(messages: dict, notifications: list) -> tuple[int, int]:
    return (sum(max(0, count) for count in list(messages.values())),
            sum(1 for note in list(notifications) if not note.get('read') and note.get('kind') != 'message'))


def icon_pixmap(size=64, count=0):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#2367DC'))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 12, 12)
    font = QFont('Segoe UI'); font.setPixelSize(25); font.setBold(True); p.setFont(font)
    p.setPen(QColor('#FFFFFF'))
    p.drawText(QRectF(4, 4, 56, 56), Qt.AlignmentFlag.AlignCenter, 'LM')
    if count > 0:
        # At 16px, prefer a clear dot; at higher DPI include the number.
        p.setPen(QPen(QColor('#FFFFFF'), 2.5))
        p.setBrush(QColor('#D9364F'))
        p.drawEllipse(QRectF(39, 1, 24, 24))
        if size >= 24:
            p.setPen(QColor('#FFFFFF'))
            font = QFont('Segoe UI'); font.setPixelSize(13 if count > 9 else 16); font.setBold(True); p.setFont(font)
            p.drawText(QRectF(39, 1, 24, 24), Qt.AlignmentFlag.AlignCenter, str(count) if count < 100 else '99+')
    p.end()
    return pm


def messenger_icon(count=0) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(icon_pixmap(size, count))
    return icon
