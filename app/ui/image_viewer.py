from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

PANEL_RATIO = 0.55
PANEL_MAX_W = 560


class ImageViewer(QFrame):
    """Выезжающая справа панель просмотра картинки с зумом."""

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

        self.img_label = QLabel("колесо мыши — зум")
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.img_label, 1)

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def panel_width(self) -> int:
        return min(PANEL_MAX_W, int(self.parent().width() * PANEL_RATIO))

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
        """Держать панель прижатой к правому краю при ресайзе."""
        if self.isVisible() and not self._anim.state():
            self.setGeometry(self.parent().width() - self.width(), 0,
                             self.width(), self.parent().height())
            self._update_pixmap()

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
        area = self.img_label.size()
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
        self.img_label.setPixmap(self._pix.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def wheelEvent(self, event):
        self._set_zoom(1 if event.angleDelta().y() > 0 else -1)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.slide_out()
