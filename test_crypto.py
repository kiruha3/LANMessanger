"""PSK-комнаты: unit-тесты crypto + 4 движка (Hub, Alice, Bob, Carol).

Сценарий: комната "sec" с паролем. Alice -> Bob читается; у хаба в чате —
строка про невозможность расшифровки (пароля нет); Carol с неверным паролем
получает пакет и видит предупреждение, не текст. Плюс персистентность
пароля в settings.json (3-й элемент) и загрузка старого 2-элементного формата.
"""

import base64
import json
import os
import time

from app.core.engine import Engine
from app.core.history import History
from app.net import crypto

for f in ("settings.json", "history.db"):
    try:
        os.remove(f)
    except OSError:
        pass
os.makedirs("test_tmp_rooms", exist_ok=True)
for f in os.listdir("test_tmp_rooms"):  # чистка прошлых прогонов этого теста
    if f.startswith("crypto_"):
        os.remove(os.path.join("test_tmp_rooms", f))

# --- unit: crypto ---
k1 = crypto.room_key("pw", "sec")
assert k1 == crypto.room_key("pw", "sec"), "room_key не детерминирован"
assert k1 != crypto.room_key("pw", "other"), "room_key не зависит от комнаты"
assert k1 != crypto.room_key("pw2", "sec"), "room_key не зависит от пароля"
assert len(k1) == 32
payload = crypto.encrypt(k1, "привет".encode("utf-8"))
assert crypto.decrypt(k1, payload) == "привет".encode("utf-8"), "roundtrip"
assert crypto.decrypt(crypto.room_key("pw", "other"), payload) is None, \
    "чужой ключ должен дать None"
raw = bytearray(base64.b64decode(payload))
raw[-1] ^= 1  # битый пакет
assert crypto.decrypt(k1, base64.b64encode(bytes(raw)).decode()) is None
assert crypto.decrypt(k1, "!!!not-base64!!!") is None
assert crypto.decrypt(k1, base64.b64encode(b"short").decode()) is None
assert crypto.is_encrypted({"enc": True})
assert not crypto.is_encrypted({})
assert not crypto.is_encrypted({"enc": None})
print("crypto: unit-тесты OK")


def make_engine(name, udp, tcp, targets, hist_name):
    e = Engine(name, udp_port=udp, tcp_port=tcp, targets=targets)
    e.history = History(f"test_tmp_rooms/{hist_name}")
    return e


def wait_for(cond, timeout=10.0, what=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.2)
    raise AssertionError(f"timeout: {what}")


HUB_KEY = "127.0.0.1:46731"
ROOM = "sec"
CHAT_KEY = f"room:{HUB_KEY}/{ROOM}"
PASSWORD = "s3cret"
SECRET = "секретное сообщение"
WARN = "[не удалось расшифровать — неверный пароль]"

h = make_engine("Hub", 46721, 46731,
                [("127.0.0.1", 46722), ("127.0.0.1", 46723),
                 ("127.0.0.1", 46724)], "crypto_h.db")
a = make_engine("Alice", 46722, 46732, [("127.0.0.1", 46721)], "crypto_a.db")
b = make_engine("Bob", 46723, 46733, [("127.0.0.1", 46721)], "crypto_b.db")
c = make_engine("Carol", 46724, 46734, [("127.0.0.1", 46721)], "crypto_c.db")
h.hub_enabled = True  # дефолт выкл с 0.18.0
h.start()
a.start()
b.start()
c.start()

try:
    time.sleep(1.0)  # discovery
    assert a.join_room(HUB_KEY, ROOM, password=PASSWORD), "A: join не удался"
    entry = a._my_room(HUB_KEY, ROOM)
    assert entry and entry["password"] == PASSWORD, "A: пароль не запомнен"

    # персистентность: пароль — третий элемент в settings.json
    with open("settings.json", encoding="utf-8") as f:
        rooms = json.load(f).get("rooms", [])
    assert [HUB_KEY, ROOM, PASSWORD] in rooms, f"rooms в settings: {rooms}"
    print("crypto: join A с паролем + пароль в settings.json OK")

    assert b.join_room(HUB_KEY, ROOM, password=PASSWORD), "B: join не удался"
    assert c.join_room(HUB_KEY, ROOM, password="wrong"), "C: join не удался"

    # --- Alice -> все ---
    msg = a.send_room(HUB_KEY, ROOM, SECRET)
    assert msg.status == "delivered", f"send_room: {msg.status}"

    wait_for(lambda: any(m.text == SECRET and m.direction == "in"
                         for m in b.chat(CHAT_KEY)),
             what="B не получил расшифрованное сообщение")
    print("crypto: Bob (верный пароль) прочитал сообщение OK")

    wait_for(lambda: any(m.text == WARN for m in c.chat(CHAT_KEY)),
             what="C не получила строку-предупреждение")
    assert not any(m.text == SECRET for m in c.chat(CHAT_KEY)), \
        "C видит открытый текст с неверным паролем!"
    print("crypto: Carol (неверный пароль) видит предупреждение, не текст OK")

    wait_for(lambda: any(m.text == WARN for m in h.chat(CHAT_KEY)),
             what="Hub: нет строки про невозможность расшифровки")
    assert not any(m.text == SECRET for m in h.chat(CHAT_KEY)), \
        "хаб видит открытый текст!"
    print("crypto: хаб без пароля видит только предупреждение (E2E) OK")

    # своя копия у Alice — в открытом виде
    assert any(m.text == SECRET and m.direction == "out"
               for m in a.chat(CHAT_KEY)), "A: нет своей копии в открытом виде"

    # дедуп: то же сообщение ещё раз через хаб — предупреждение одно
    with c.connections._lock:
        pc = c.connections.conns.get(HUB_KEY)
    rk = crypto.room_key(PASSWORD, ROOM)
    pc.send_packet("hub_msg", room=ROOM, id=msg.id, **{"from": "Alice"},
                   text=crypto.encrypt(rk, SECRET.encode("utf-8")),
                   timestamp=msg.timestamp, enc=True)
    time.sleep(0.8)
    texts_c = [m.text for m in c.chat(CHAT_KEY)]
    assert texts_c.count(WARN) == 1, f"C: дубль! {texts_c}"
    print("crypto: дедуп по id при enc не сломан OK")

    for eng in (c, b, a, h):
        eng.stop()

    # --- старый 2-элементный формат rooms грузится ---
    with open("settings.json", "w", encoding="utf-8") as f:
        json.dump({"name": "Old", "rooms": [[HUB_KEY, ROOM],
                                            [HUB_KEY, "plain", None]]}, f)
    e2 = make_engine("Old", 46741, 46751, [], "crypto_old.db")
    assert e2._saved_rooms == [[HUB_KEY, ROOM, None],
                               [HUB_KEY, "plain", None]], e2._saved_rooms
    e2.history.close()
    print("crypto: старый формат rooms (2 элемента) грузится OK")

    for f in ("settings.json", "history.db"):
        try:
            os.remove(f)
        except OSError:
            pass
    for f in os.listdir("test_tmp_rooms"):
        if f.startswith("crypto_"):
            os.remove(os.path.join("test_tmp_rooms", f))
    print("OK")
except BaseException:
    for eng in {id(x): x for x in (h, a, b, c) if x}.values():
        try:
            eng.stop()
        except Exception:
            pass
    raise
