"""TLS на канал (шаг 2) + PSK для P2P (шаг 3).

Сценарии:
- unit: ensure_self_signed_cert создаёт hub.crt/hub.key, fingerprint
  детерминирован для того же файла, разные сертификаты — разные отпечатки.
- A(tls) + B(tls): канал TLS (SSLSocket с обеих сторон), сообщение доходит.
- peek-ветка: plain-клиент C (tls_enabled=False) доходит до TLS-сервера A.
- fingerprint пира попадает в known_fingerprints (+ уведомление, settings.json).
- PSK: A и B с network_psk="x" обмениваются и читают; B с psk="y" видит
  строку-предупреждение, а не текст.
"""

import json
import os
import shutil
import ssl
import time

from app.core.engine import Engine
from app.core.history import History
from app.net import crypto

for f in ("settings.json", "history.db", "hub.crt", "hub.key"):
    try:
        os.remove(f)
    except OSError:
        pass
os.makedirs("test_tmp_rooms", exist_ok=True)
for f in os.listdir("test_tmp_rooms"):  # чистка прошлых прогонов этого теста
    if f.startswith("tls_"):
        os.remove(os.path.join("test_tmp_rooms", f))
shutil.rmtree("test_tmp_rooms/tlscert", ignore_errors=True)
shutil.rmtree("test_tmp_rooms/tlscert2", ignore_errors=True)

# --- unit: сертификат и отпечаток ---
crt, key = crypto.ensure_self_signed_cert("test_tmp_rooms/tlscert")
assert os.path.exists(crt) and os.path.exists(key), "файлы сертификата не созданы"
fp1 = crypto.cert_fingerprint(crt)
assert len(fp1) == 64 and all(c in "0123456789abcdef" for c in fp1)
crt2, _ = crypto.ensure_self_signed_cert("test_tmp_rooms/tlscert")  # повторно
assert crt2 == crt and crypto.cert_fingerprint(crt2) == fp1, \
    "fingerprint не детерминирован для того же файла"
other_crt, _ = crypto.ensure_self_signed_cert("test_tmp_rooms/tlscert2")
assert crypto.cert_fingerprint(other_crt) != fp1, "разные сертификаты — один fp?"
ctx = crypto.server_ctx(crt, key)
assert ctx.protocol == ssl.PROTOCOL_TLS_SERVER
cctx = crypto.client_ctx()
assert not cctx.check_hostname and cctx.verify_mode == ssl.CERT_NONE
print("tls: unit-тесты сертификата OK")


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


A_KEY = "127.0.0.1:46811"
B_KEY = "127.0.0.1:46812"
C_KEY = "127.0.0.1:46813"
E_KEY = "127.0.0.1:46815"
WARN = "[не удалось расшифровать — неверный сетевой пароль]"

engines = []
a = b = c = d = e = None
try:
    a = make_engine("A", 46801, 46811, [("127.0.0.1", 46802)], "tls_a.db")
    b = make_engine("B", 46802, 46812, [("127.0.0.1", 46801)], "tls_b.db")
    engines += [a, b]
    assert a.tls_enabled and b.tls_enabled, "tls_enabled должен быть True по умолчанию"
    assert a.connections.tls_cert and a.connections.tls_outbound, \
        "TLS не применён к ConnectionManager"
    a.start()
    b.start()
    # движки делят один каталог -> один hub.crt; эталон — его отпечаток
    ROOT_FP = crypto.cert_fingerprint("hub.crt")

    # --- A(tls) + B(tls): канал TLS, сообщение доходит ---
    wait_for(lambda: a.connections.is_connected(B_KEY)
             and b.connections.is_connected(A_KEY),
             what="A и B не соединились")
    with a.connections._lock:
        pc_ab = a.connections.conns.get(B_KEY)
    with b.connections._lock:
        pc_ba = b.connections.conns.get(A_KEY)
    assert isinstance(pc_ab.sock, ssl.SSLSocket), f"A: канал не TLS ({pc_ab.sock})"
    assert isinstance(pc_ba.sock, ssl.SSLSocket), f"B: канал не TLS ({pc_ba.sock})"
    print("tls: A<->B соединение TLS (SSLSocket с обеих сторон) OK")

    m = a.send(B_KEY, "127.0.0.1", 46812, "привет по TLS")
    assert m.status == "delivered", f"A->B: {m.status}"
    wait_for(lambda: any(x.text == "привет по TLS" and x.direction == "in"
                         for x in b.chat(A_KEY)),
             what="B не получил сообщение по TLS")
    print("tls: сообщение по TLS-каналу дошло OK")

    # --- fingerprint пира в known_fingerprints (косвенно: A и B уже пinned) ---
    wait_for(lambda: B_KEY in a.known_fingerprints
             and A_KEY in b.known_fingerprints,
             what="отпечатки не записались после TLS-дозвона")
    assert a.known_fingerprints[B_KEY] == ROOT_FP, \
        "fp пира не совпал с fp его сертификата"
    print("tls: known_fingerprints у A/B OK")

    # --- peek-ветка: старый plain-клиент до TLS-сервера ---
    c = make_engine("C", 46803, 46813, [("127.0.0.1", 46801)], "tls_c.db")
    c.set_tls_enabled(False)
    engines.append(c)
    c.start()
    m = c.send(A_KEY, "127.0.0.1", 46811, "я старый клиент")
    assert m.status == "delivered", f"C(plain)->A(tls): {m.status}"
    wait_for(lambda: any(x.text == "я старый клиент" for x in a.chat(C_KEY)),
             what="A не принял plain-сообщение от C")
    with a.connections._lock:
        pc_ac = a.connections.conns.get(C_KEY)
    if pc_ac is not None:
        assert not isinstance(pc_ac.sock, ssl.SSLSocket), \
            "plain-клиент не должен оказаться в TLS"
    print("tls: peek-ветка, plain-клиент до TLS-сервера OK")

    # --- отдельная пара D/E без auto-connect: fingerprint + уведомление ---
    # общий settings.json уже содержит tls_enabled=false от C — включаем явно
    d = make_engine("D", 46804, 46814, [], "tls_d.db")
    e = make_engine("E", 46805, 46815, [], "tls_e.db")
    d.set_tls_enabled(True)
    e.set_tls_enabled(True)
    d.known_fingerprints.clear()  # в файле — чужие записи (общий settings.json)
    engines += [d, e]
    d.start()
    e.start()
    m = d.send(E_KEY, "127.0.0.1", 46815, "проверка отпечатка")
    assert m.status == "delivered", f"D->E: {m.status}"
    assert d.known_fingerprints.get(E_KEY) == ROOT_FP, \
        f"D не запомнил отпечаток E: {d.known_fingerprints}"
    assert any(n["title"] == "Отпечаток TLS" for n in d.notifications), \
        "нет уведомления об отпечатке"
    with open("settings.json", encoding="utf-8") as f:
        saved = json.load(f).get("known_fingerprints", {})
    assert saved.get(E_KEY) == ROOT_FP, \
        "known_fingerprints не сохранились в settings.json"
    print("tls: fingerprint сохранён + уведомление + settings.json OK")

    # --- PSK для P2P: одинаковый пароль ---
    a.set_network_psk("x")
    b.set_network_psk("x")
    m = a.send(B_KEY, "127.0.0.1", 46812, "psk-секрет")
    assert m.status == "delivered", f"A->B psk: {m.status}"
    assert m.text == "psk-секрет", "локальная копия должна быть в открытом виде"
    wait_for(lambda: any(x.text == "psk-секрет" and x.direction == "in"
                         for x in b.chat(A_KEY)),
             what="B не прочитал PSK-сообщение")
    print("tls: PSK — обмен с одинаковым паролем OK")

    # --- PSK: неверный пароль у получателя ---
    b.set_network_psk("y")
    m = a.send(B_KEY, "127.0.0.1", 46812, "тайный текст")
    assert m.status == "delivered", f"A->B psk2: {m.status}"
    wait_for(lambda: any(x.text == WARN for x in b.chat(A_KEY)),
             what="B нет строки-предупреждения")
    assert not any(x.text == "тайный текст" and x.direction == "in"
                   for x in b.chat(A_KEY)), \
        "B видит открытый текст с неверным сетевым паролем!"
    print("tls: PSK — неверный пароль: предупреждение, не текст OK")

    for eng in engines:
        eng.stop()
    for f in ("settings.json", "history.db", "hub.crt", "hub.key"):
        try:
            os.remove(f)
        except OSError:
            pass
    for f in os.listdir("test_tmp_rooms"):
        if f.startswith("tls_"):
            os.remove(os.path.join("test_tmp_rooms", f))
    shutil.rmtree("test_tmp_rooms/tlscert", ignore_errors=True)
    shutil.rmtree("test_tmp_rooms/tlscert2", ignore_errors=True)
    print("OK")
except BaseException:
    for eng in engines:
        try:
            eng.stop()
        except Exception:
            pass
    raise
