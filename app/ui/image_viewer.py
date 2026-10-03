from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRect, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

PANEL_MAX_W = 100000  # фактически без лимита: панель = весь чат


class ImageViewer(QFrame):
    """Выезжающая справа панель просмотра картинки на всю область чата."""

    def __init__(self, parent):
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self._zoom: float | None = None  # None = вписать в панель
        self.setStyleSheet("QFrame { background: rgba(24,24,24,235); }"
                           "QLabel { color: #eee; background: transparent; }"
                           "QPushButton { background: #3c3c3c; color: #eee;"
                           " border: 1px solid #555; border-radius: 4px;"
                           " padding: 3px 10px; }")
        self.hide()

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedWidth(36)
        self.close_btn.clicked.connect(self.slide_out)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_fit_btn = QPushButton("По размеру")
        self.zoom_100_btn = QPushButton("1:1")
        self.zoom_out_btn.clicked.connect(lambda: self._set_zoom(-1))
        self.zoom_in_btn.clicked.connect(lambda: self._set_zoom(1))
        self.zoom_fit_btn.clicked.connect(lambda: self._set_zoom(0))
        self.zoom_100_btn.clicked.connect(lambda: self._set_zoom(100))

        top = QHBoxLayout()
        top.addWidget(QLabel("Просмотр"), 1)
        for b in (self.zoom_out_btn, self.zoom_in_btn,
                  self.zoom_fit_btn, self.zoom_100_btn, self.close_btn):
            top.addWidget(b)

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.img_label)
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().installEventFilter(self)
        self._drag_pos = None

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.scroll, 1)

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def panel_width(self) -> int:
        return self.parent().width()

    def open_image(self, path: str):
        self._pix = QPixmap(path)
        if self._pix.isNull():
            return
        self._zoom = None
        self._update_pixmap()
        self.slide_in()

    def slide_in(self):
        w = self.panel_width()
        h = self.parent().height()
        self.setGeometry(self.parent().width(), 0, w, h)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(QRect(self.parent().width() - w, 0, w, h))
        self._anim.start()
        self.setFocus()

    def slide_out(self):
        w = self.width()
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(QRect(self.parent().width(), 0, w,
                                     self.parent().height()))
        self._anim.finished.connect(self._after_close)
        self._anim.start()

    def _after_close(self):
        self.hide()
        try:
            self._anim.finished.disconnect(self._after_close)
        except TypeError:
            pass

    def dock(self):
        """Держать панель во всю область чата, в т.ч. при ресайзе/развороте."""
        if not self.isVisible():
            return
        if self._anim.state() == QPropertyAnimation.State.Running:
            return
        w = self.panel_width()
        self.setGeometry(self.parent().width() - w, 0, w,
                         self.parent().height())
        self._update_pixmap()

    def eventFilter(self, obj, event):
        """Перетаскивание картинки ЛКМ при зуме."""
        if obj is self.scroll.viewport():
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._drag_pos = event.position()
                self.scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            elif (event.type() == QEvent.Type.MouseMove
                    and self._drag_pos is not None):
                delta = event.position() - self._drag_pos
                self._drag_pos = event.position()
                h = self.scroll.horizontalScrollBar()
                v = self.scroll.verticalScrollBar()
                h.setValue(h.value() - int(delta.x()))
                v.setValue(v.value() - int(delta.y()))
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_pos = None
                self.scroll.viewport().unsetCursor()
        return super().eventFilter(obj, event)

    def _set_zoom(self, action: int):
        if self._pix is None:
            return
        if action == 0:
            self._zoom = None
        elif action == 100:
            self._zoom = 1.0
        else:
            base = self._zoom or self._fit_zoom()
            self._zoom = max(0.05, min(8.0,
                                       base * (1.25 if action > 0 else 0.8)))
        self._update_pixmap()

    def _fit_zoom(self) -> float:
        area = self.scroll.viewport().size()
        if self._pix is None or area.width() < 10:
            return 1.0
        return min(area.width() / self._pix.width(),
                   area.height() / self._pix.height(), 1.0)

    def _update_pixmap(self):
        if self._pix is None:
            return
        factor = self._zoom if self._zoom is not None else self._fit_zoom()
        w = max(1, int(self._pix.width() * factor))
        h = max(1, int(self._pix.height() * factor))
        scaled = self._pix.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.img_label.setPixmap(scaled)
        # меньше вьюпорта — центрируем; больше — скролл/драг
        vw = self.scroll.viewport().width()
        vh = self.scroll.viewport().height()
        self.img_label.resize(max(w, vw), max(h, vh))

    def wheelEvent(self, event):
        self._set_zoom(1 if event.angleDelta().y() > 0 else -1)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.slide_out()
