from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QHeaderView, QTreeWidget

from . import theme


class AIChatTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.items = {}
        self.routes = []
        self.setColumnCount(2)
        header = self.header()
        header.moveSection(1, 0)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(12)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setTreePosition(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setColumnWidth(1, 12)

    def set_routes(self, routes):
        self.routes = routes
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.routes:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(event.rect())
        dark = theme.current() == "dark"
        endpoints = {}
        for route in self.routes:
            endpoints.setdefault(route["source"], set()).add("out")
            endpoints.setdefault(route["target"], set()).add("in")
        marks = []
        for index, route in enumerate(self.routes):
            source = self.visualRect(self.indexFromItem(self.items[route["source"]], 0))
            target = self.visualRect(self.indexFromItem(self.items[route["target"]], 0))
            if source.isEmpty() or target.isEmpty() or source == target:
                continue
            source_y = source.center().y() - (3 if len(endpoints[route["source"]]) > 1 else 0)
            target_y = target.center().y() + (3 if len(endpoints[route["target"]]) > 1 else 0)
            lane = 3 + index * 4
            source_x = source.left() + 5
            target_x = target.left() + 5
            turn = 4 if target_y > source_y else -4
            path = QPainterPath(QPointF(source_x, source_y))
            path.lineTo(lane + 4, source_y)
            path.quadTo(lane, source_y, lane, source_y + turn)
            path.lineTo(lane, target_y - turn)
            path.quadTo(lane, target_y, lane + 4, target_y)
            path.lineTo(target_x - 3, target_y)
            if route["waiting"]:
                color = QColor("#f6c577" if dark else "#8c4b0b")
            elif route["focused"]:
                color = QColor("#72e0d2" if dark else "#087d72")
            else:
                color = QColor("#79a9a4" if dark else "#537c77")
            pen = QPen(color, 2.1 if route["focused"] else 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if route["waiting"]:
                pen.setDashPattern([2.3, 2.1])
            painter.setPen(pen)
            painter.drawPath(path)
            marks.append((source_x, source_y, target_x, target_y, color, route["count"]))
        for source_x, source_y, target_x, target_y, color, count in marks:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(source_x, source_y), 2.5, 2.5)
            painter.drawPolygon(QPolygonF([QPointF(target_x, target_y),
                                           QPointF(target_x - 6, target_y - 3.5),
                                           QPointF(target_x - 6, target_y + 3.5)]))
            if count > 1:
                label = f"×{count}"
                painter.setPen(color)
                painter.drawText(QPointF(target_x + 1, target_y - 5), label)
        painter.end()
