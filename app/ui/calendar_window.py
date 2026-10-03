import time

from PyQt6.QtCore import QDate, QDateTime, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme


class CalendarView(QCalendarWidget):
    """Календарь с явной раскраской: чужие месяцы, выходные, сегодня."""

    def paintCell(self, painter: QPainter, rect, date: QDate):
        dark = theme.current() == "dark"
        cur = date.month() == self.monthShown() and date.year() == self.yearShown()
        today = date == QDate.currentDate()
        selected = date == self.selectedDate()
        weekend = date.dayOfWeek() >= 6

        if selected:
            bg, fg = QColor(theme.ACCENT), QColor("#ffffff")
        elif not cur:
            bg = QColor("#242424" if dark else "#e9e9e9")
            fg = QColor("#5a5a5a" if dark else "#a8a8a8")
        elif weekend:
            bg = QColor("#332626" if dark else "#fdeeee")
            fg = QColor("#e57373" if dark else "#c0392b")
        else:
            bg = QColor("#1f1f1f" if dark else "#ffffff")
            fg = QColor("#e6e6e6" if dark else "#1f1f1f")

        painter.save()
        painter.fillRect(rect, bg)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))
        if today and not selected:
            painter.setPen(QColor(theme.ACCENT))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.restore()


class EventDialog(QDialog):
    def __init__(self, parent, engine, event: dict | None = None,
                 default_date: QDate | None = None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Событие" if event is None else "Изменить событие")
        self.setMinimumWidth(320)

        self.title_edit = QLineEdit(event["title"] if event else "")
        self.title_edit.setPlaceholderText("Название события")

        if event:
            default_dt = QDateTime.fromSecsSinceEpoch(event["ts"])
        elif default_date:
            default_dt = default_date.startOfDay().addSecs(10 * 3600)
        else:
            default_dt = QDateTime.currentDateTime().addSecs(3600)
        self.dt_edit = QDateTimeEdit(default_dt)
        self.dt_edit.setCalendarPopup(True)

        self.remind_spin = QSpinBox()
        self.remind_spin.setRange(0, 40320)
        self.remind_spin.setSuffix(" мин до начала")
        self.remind_spin.setValue(event["remind_min"] if event else 15)

        self.all_box = QCheckBox("Для всех узлов сети")
        self.members = QListWidget()
        self.members.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.members.setMaximumHeight(110)
        for node in engine.nodes():
            QListWidgetItem(node.name, self.members)
        if event:
            parts = event.get("participants") or []
            self.all_box.setChecked("all" in parts)
            for i in range(self.members.count()):
                item = self.members.item(i)
                item.setSelected(item.text() in parts)
        self.members.setEnabled(not self.all_box.isChecked())
        self.all_box.toggled.connect(lambda on: self.members.setEnabled(not on))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Название:"))
        layout.addWidget(self.title_edit)
        layout.addWidget(QLabel("Когда:"))
        layout.addWidget(self.dt_edit)
        layout.addWidget(QLabel("Напомнить:"))
        layout.addWidget(self.remind_spin)
        layout.addWidget(self.all_box)
        layout.addWidget(QLabel("Участники (если не для всех):"))
        layout.addWidget(self.members)
        layout.addWidget(buttons)

    def fields(self) -> dict:
        participants = ["all"] if self.all_box.isChecked() else [
            item.text() for item in self.members.selectedItems()]
        return {"title": self.title_edit.text().strip(),
                "ts": self.dt_edit.dateTime().toSecsSinceEpoch(),
                "remind_min": self.remind_spin.value(),
                "participants": participants}


class CalendarWindow(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._sig = None
        self.setWindowTitle("Календарь")
        self.resize(640, 420)

        self.calendar = CalendarView()
        self.calendar.clicked.connect(lambda _: self._refresh(force=True))
        self.calendar.activated.connect(lambda _: self._add())  # двойной клик — создать

        self.events_list = QListWidget()
        self.events_list.itemDoubleClicked.connect(self._edit_selected)

        self.add_btn = QPushButton("Добавить")
        self.edit_btn = QPushButton("Изменить")
        self.del_btn = QPushButton("Удалить")
        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit_selected)
        self.del_btn.clicked.connect(self._delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.edit_btn)
        btns.addWidget(self.del_btn)

        right = QVBoxLayout()
        right.addWidget(QLabel("Все события (по датам):"))
        right.addWidget(self.events_list, 1)
        right.addLayout(btns)

        layout = QHBoxLayout(self)
        layout.addWidget(self.calendar)
        layout.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1500)
        self._refresh(force=True)

    def _refresh(self, force: bool = False):
        evs = sorted(self.engine.visible_events(), key=lambda e: e["ts"])
        sig = (tuple(e["id"] for e in evs),
               tuple(e["updated_at"] for e in evs),
               self.calendar.selectedDate().toJulianDay())
        if not force and sig == self._sig:
            return
        self._sig = sig

        bar = self.events_list.verticalScrollBar()
        scroll_pos = bar.value()
        sel_id = self._selected_id()

        self.events_list.clear()
        today_jd = self.calendar.selectedDate().toJulianDay()
        target_item = None
        cur_day = None
        for ev in evs:
            day = time.localtime(ev["ts"])
            day_jd = QDate(day.tm_year, day.tm_mon, day.tm_mday).toJulianDay()
            if day_jd != cur_day:
                cur_day = day_jd
                label = time.strftime("%d.%m.%Y", day)
                if day_jd == QDate.currentDate().toJulianDay():
                    label += "  —  сегодня"
                sep = QListWidgetItem(label)
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                sep.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                sep.setForeground(QColor(theme.ACCENT))
                self.events_list.addItem(sep)
                if day_jd == today_jd:
                    target_item = sep

            when = time.strftime("%H:%M", day)
            scope = "все" if "all" in (ev.get("participants") or []) \
                else ", ".join(ev.get("participants") or []) or "личное"
            item = QListWidgetItem(
                f"{when} — {ev['title']}  ({ev['creator']}; {scope})")
            item.setData(Qt.ItemDataRole.UserRole, ev["id"])
            if ev["id"] == sel_id:
                self.events_list.setCurrentItem(item)
            self.events_list.addItem(item)

        if target_item is not None:
            self.events_list.scrollToItem(
                target_item, QListWidget.ScrollHint.PositionAtTop)
        else:
            bar.setValue(scroll_pos)

    def _selected_id(self) -> str | None:
        item = self.events_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add(self):
        dlg = EventDialog(self, self.engine,
                          default_date=self.calendar.selectedDate())
        if dlg.exec():
            f = dlg.fields()
            if not f["title"]:
                return
            self.engine.add_event(**f)
            self._refresh(force=True)

    def _edit_selected(self):
        ev_id = self._selected_id()
        if not ev_id:
            return
        ev = next((e for e in self.engine.visible_events()
                   if e["id"] == ev_id), None)
        if not ev:
            return
        if ev["creator"] != self.engine.name:
            QMessageBox.information(self, "Событие",
                                    "Изменить может только автор события.")
            return
        dlg = EventDialog(self, self.engine, ev)
        if dlg.exec():
            f = dlg.fields()
            if not f["title"]:
                return
            self.engine.update_event(ev_id, **f)
            self._refresh(force=True)

    def _delete_selected(self):
        ev_id = self._selected_id()
        if not ev_id:
            return
        self.engine.delete_event(ev_id)
        self._refresh(force=True)
