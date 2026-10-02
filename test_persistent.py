"""Тест постоянных соединений: обмен в обе стороны, когда «порт открыт» только у одного."""

import time

from app.core.engine import Engine

a = Engine("Alice", udp_port=46601, tcp_port=46611, targets=[("127.0.0.1", 46602)])
b = Engine("Bob", udp_port=46602, tcp_port=46612, targets=[("127.0.0.1", 46601)])
a.start()
b.start()
time.sleep(0.5)

# B -> A: B достукивается до «открытого» порта A и устанавливает соединение
m1 = b.send("127.0.0.1:46611", "127.0.0.1", 46611, "привет от B")
assert m1.status == "delivered", f"B->A: {m1.status}"
print("persistent: B->A по новому соединению OK")

# A -> B: у B «закрыт порт» (A пробует недоступный 46999),
# но ответ должен уйти по соединению, которое установил B
m2 = a.send("127.0.0.1:46612", "127.0.0.1", 46999, "ответ от A")
assert m2.status == "delivered", f"A->B через постоянное соединение: {m2.status}"
time.sleep(0.5)
texts = [m.text for m in b.chats.get("127.0.0.1:46611", [])]
assert "ответ от A" in texts, f"B не получил ответ: {texts}"
print("persistent: A->B по соединению, установленному B, OK (порт B «закрыт»)")

# повторная отправка — переиспользование того же канала
m3 = a.send("127.0.0.1:46612", "127.0.0.1", 46999, "ещё раз")
assert m3.status == "delivered", f"повторная отправка: {m3.status}"
print("persistent: переиспользование соединения OK")

# обычный обмен в LAN тоже работает
m4 = b.send("127.0.0.1:46611", "127.0.0.1", 46611, "снова от B")
assert m4.status == "delivered"
time.sleep(0.3)
texts_a = [m.text for m in a.chats.get("127.0.0.1:46612", [])]
assert "привет от B" in texts_a and "снова от B" in texts_a, texts_a
print("persistent: обычный обмен OK")

a.stop()
b.stop()
print("OK")
