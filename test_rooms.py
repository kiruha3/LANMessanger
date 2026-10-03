"""Тест комнат через хаб: 3 движка (Hub, Alice, Bob) на loopback.

Сценарии: join, members у всех троих, сообщение A -> B и H ровно один раз
(дедуп по id), leave B, персистентность комнат в settings.json у Alice.
"""

import os
import time

from app.core.engine import Engine

for f in ("settings.json", "history.db"):
    try:
        os.remove(f)
    except OSError:
        pass

HUB_KEY = "127.0.0.1:46711"
ROOM = "test"
CHAT_KEY = f"room:{HUB_KEY}/{ROOM}"

h = Engine("Hub", udp_port=46701, tcp_port=46711,
           targets=[("127.0.0.1", 46702), ("127.0.0.1", 46703)])
a = Engine("Alice", udp_port=46702, tcp_port=46712,
           targets=[("127.0.0.1", 46701)])
b = Engine("Bob", udp_port=46703, tcp_port=46713,
           targets=[("127.0.0.1", 46701)])
h.start()
a.start()
b.start()


def members_of(engine, hub_key, room, timeout=8.0):
    """members записи комнаты; ждём, пока появятся (discovery + hub_members)."""
    deadline = time.time() + timeout
    while True:
        for r in engine.my_rooms:
            if r["hub_key"] == hub_key and r["room"] == room:
                if r["members"]:
                    return r["members"]
        if time.time() > deadline:
            return None
        time.sleep(0.2)


def wait_for(cond, timeout=8.0, what=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.2)
    raise AssertionError(f"timeout: {what}")


try:
    time.sleep(1.0)  # discovery: узлы видят друг друга
    # --- join ---
    assert a.join_room(HUB_KEY, ROOM), "A: join_room не удался"
    assert b.join_room(HUB_KEY, ROOM), "B: join_room не удался"
    print("rooms: join A и B OK")

    for eng, key, nick in ((a, HUB_KEY, "Alice"), (b, HUB_KEY, "Bob"),
                           (h, HUB_KEY, "Hub")):
        m = members_of(eng, key, ROOM)
        assert m is not None, f"{nick}: нет записи о комнате"
        assert set(m) == {"Alice", "Bob", "Hub"}, f"{nick}: members = {m}"
    print("rooms: members = {Alice, Bob, Hub} у всех троих OK")

    # --- сообщение A -> B и H, ровно один раз ---
    msg = a.send_room(HUB_KEY, ROOM, "всем привет")
    assert msg.status == "delivered", f"send_room: {msg.status}"
    wait_for(lambda: any(m.text == "всем привет" for m in b.chat(CHAT_KEY)),
             what="B не получил сообщение комнаты")
    wait_for(lambda: any(m.text == "всем привет" for m in h.chat(CHAT_KEY)),
             what="H не получил сообщение комнаты")

    # дедуп: тот же hub_msg с тем же id ещё раз через хаб
    with a.connections._lock:
        pc = a.connections.conns.get(HUB_KEY)
    pc.send_packet("hub_msg", room=ROOM, id=msg.id, **{"from": "Alice"},
                   text="всем привет", timestamp=msg.timestamp)
    time.sleep(0.8)
    texts_b = [m.text for m in b.chat(CHAT_KEY)]
    texts_h = [m.text for m in h.chat(CHAT_KEY)]
    assert texts_b.count("всем привет") == 1, f"B: дубль! {texts_b}"
    assert texts_h.count("всем привет") == 1, f"H: дубль! {texts_h}"
    assert b.unread_count(CHAT_KEY) == 1
    print("rooms: сообщение A дошло до B и H ровно один раз (дедуп) OK")

    # --- leave B ---
    b.leave_room(HUB_KEY, ROOM)
    wait_for(lambda: set(members_of(a, HUB_KEY, ROOM, 0.1) or ()) == {"Alice", "Hub"},
             what="members у A не обновились после leave B")
    mh = members_of(h, HUB_KEY, ROOM, 0.1)
    assert set(mh) == {"Alice", "Hub"}, f"H: members = {mh}"
    assert not any(r["room"] == ROOM for r in b.my_rooms), "B: комната не убрана"
    print("rooms: leave B -> members обновились у всех OK")

    # --- персистентность: перезапуск A, комната восстанавливается ---
    a.stop()
    a2 = Engine("Alice", udp_port=46702, tcp_port=46712,
                targets=[("127.0.0.1", 46701)])
    a2.start()
    wait_for(
        lambda: "Alice" in (members_of(a2, HUB_KEY, ROOM, 0.1) or ()),
        timeout=25.0, what="A2 не пере-join'ил комнату после перезапуска")
    mh = members_of(h, HUB_KEY, ROOM, 0.1)
    assert "Alice" in mh, f"H: нет Alice после перезапуска: {mh}"
    print("rooms: персистентность — A2 восстановил комнату из settings OK")

    a2.stop()
    b.stop()
    h.stop()
    print("OK")
except BaseException:
    for eng in {id(x): x for x in (h, a, b, locals().get("a2")) if x}.values():
        try:
            eng.stop()
        except Exception:
            pass
    raise
