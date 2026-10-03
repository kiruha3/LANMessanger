import time

from PyQt6.QtCore import QDate, QDateTime, Qt, QTimer
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


class EventDialog(QDialog):
    def __init__(self, parent, engine, event: dict | None = None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Событие" if event is None else "Изменить событие")
        self.setMinimumWidth(320)

        self.title_edit = QLineEdit(event["title"] if event else "")
        self.title_edit.setPlaceholderText("Название события")

        self.dt_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.dt_edit.setCalendarPopup(True)
        if event:
            self.dt_edit.setDateTime(QDateTime.fromSecsSinceEpoch(event["ts"]))

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

        self.calendar = QCalendarWidget()
        self.calendar.clicked.connect(lambda _: self._refresh(force=True))

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
        right.addWidget(QLabel("События выбранного дня:"))
        right.addWidget(self.events_list, 1)
        right.addLayout(btns)

        layout = QHBoxLayout(self)
        layout.addWidget(self.calendar)
        layout.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1500)
        self._refresh(force=True)

    def _day_range(self) -> tuple[int, int]:
        d: QDate = self.calendar.selectedDate()
        start = d.startOfDay().toSecsSinceEpoch()
        return start, start + 86400

    def _events_for_day(self) -> list[dict]:
        start, end = self._day_range()
        evs = [e for e in self.engine.visible_events()
               if start <= e["ts"] < end]
        return sorted(evs, key=lambda e: e["ts"])

    def _refresh(self, force: bool = False):
        evs = self._events_for_day()
        sig = (tuple(e["id"] for e in evs),
               tuple(e["updated_at"] for e in evs),
               self.calendar.selectedDate().toJulianDay())
        if not force and sig == self._sig:
            return
        self._sig = sig
        self.events_list.clear()
        for ev in evs:
            when = time.strftime("%H:%M", time.localtime(ev["ts"]))
            scope = "все" if "all" in (ev.get("participants") or []) \
                else ", ".join(ev.get("participants") or []) or "личное"
            text = f"{when} — {ev['title']}  ({ev['creator']}; {scope})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ev["id"])
            self.events_list.addItem(item)

    def _selected_id(self) -> str | None:
        item = self.events_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add(self):
        dlg = EventDialog(self, self.engine)
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
