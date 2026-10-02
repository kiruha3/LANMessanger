"""Smoke-тест UI: два узла + окна, обмен сообщением, непрочитанные.
Запускается offscreen (без реального экрана): QT_QPA_PLATFORM=offscreen."""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app.core.engine import Engine

app = QApplication(sys.argv)

a = Engine("Alice", udp_port=46201, tcp_port=46211, targets=[("127.0.0.1", 46202)])
b = Engine("Bob", udp_port=46202, tcp_port=46212, targets=[("127.0.0.1", 46201)])
a.start()
b.start()

from app.ui.main_window import MainWindow

win = MainWindow(a)
win.show()

state = {"chat": None}


def step0_manual_peer():
    # ручное добавление по IP (как белый адрес) — узел должен появиться и ожить
    b.add_manual_peer("127.0.0.1", udp_port=46201, tcp_port=46211)
    assert b.node_by_key("127.0.0.1:46211") is not None
    print("ui smoke: ручной пир добавлен")


def step1_send():
    # Bob шлёт Alice сообщение (синхронно, с ack)
    msg = b.send("127.0.0.1:46211", "127.0.0.1", 46211, "Привет из smoke-теста")
    assert msg.status == "delivered", f"не доставлено: {msg.status}"
    print("ui smoke: сообщение Bob -> Alice доставлено")


def step2_open_chat():
    key = "127.0.0.1:46212"
    assert a.unread_count(key) == 1, f"непрочитанные: {a.unread_count(key)}"
    win.select_chat(key)
    assert win.chat_panel.key == key, "чат не открылся в правой панели"
    assert a.unread_count(key) == 0, "непрочитанные не сброшены при открытии чата"
    print("ui smoke: чат открыт, непрочитанные сброшены")


def step3_reply():
    # Alice отвечает Bob через UI-движок
    msgs = a.chats["127.0.0.1:46212"]
    assert any(m.direction == "in" and "smoke" in m.text for m in msgs)
    reply = a.send("127.0.0.1:46212", "127.0.0.1", 46212, "И тебе привет")
    assert reply.status == "delivered"
    assert any(m.direction == "in" for m in b.chats["127.0.0.1:46211"])
    print("ui smoke: ответ Alice -> Bob доставлен")


def finish():
    names_a = [n.name for n in a.nodes()]
    assert "Bob" in names_a, f"Alice не видит Bob: {names_a}"
    manual = b.node_by_key("127.0.0.1:46211")
    assert manual and manual.online, "ручной пир не ожил"
    print("ui smoke: ручной пир онлайн (announce/response по прямому IP)")
    a.stop()
    b.stop()
    print("ui smoke: OK")
    app.quit()


QTimer.singleShot(300, step0_manual_peer)
QTimer.singleShot(800, step1_send)
QTimer.singleShot(1300, step2_open_chat)
QTimer.singleShot(1800, step3_reply)
QTimer.singleShot(2500, finish)
sys.exit(app.exec())
