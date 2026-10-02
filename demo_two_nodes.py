"""Локальный прогон: два узла на одной машине обнаруживают друг друга и расходятся."""

import time

from app.net.discovery import DiscoveryService
from app.net import protocol
from app.net.client import send_message
from app.net.server import MessageServer


def check_protocol():
    pkt = protocol.parse_packet(
        protocol.make_packet("announce", node="x", name="T", msg_port=1),
        protocol.TYPES_UDP,
    )
    assert pkt["type"] == "announce"

    dec = protocol.FrameDecoder()
    f1 = protocol.encode_frame(b'{"a":1}')
    f2 = protocol.encode_frame(b'{"b":2}')
    # кусочная склейка: половина первого фрейма, потом остаток + второй целиком
    frames = dec.feed(f1[:3])
    assert frames == []
    frames = dec.feed(f1[3:] + f2)
    assert frames == [b'{"a":1}', b'{"b":2}']
    print("protocol: OK")


def check_discovery():
    events = []
    a = DiscoveryService(
        "Alice", udp_port=46001, tcp_port=46101,
        targets=[("127.0.0.1", 46002)], interval=0.3, node_timeout=1.0,
        on_node_new=lambda n: events.append(f"A:+{n.name}"),
        on_node_gone=lambda n: events.append(f"A:-{n.name}"),
    )
    b = DiscoveryService(
        "Bob", udp_port=46002, tcp_port=46102,
        targets=[("127.0.0.1", 46001)], interval=0.3, node_timeout=1.0,
    )
    a.start()
    b.start()
    time.sleep(1.5)

    seen_by_a = [n.name for n in a.registry.snapshot() if n.online]
    seen_by_b = [n.name for n in b.registry.snapshot() if n.online]
    assert "Bob" in seen_by_a, f"Alice не видит Bob: {seen_by_a}"
    assert "Alice" in seen_by_b, f"Bob не видит Alice: {seen_by_b}"
    print("discovery: узлы видят друг друга OK")

    b.stop()
    time.sleep(0.5)
    gone = [e for e in events if e == "A:-Bob"]
    assert gone, f"Alice не получила bye от Bob: {events}"
    assert not any(n.online for n in a.registry.snapshot()), "Bob должен быть offline"
    print("discovery: bye -> мгновенный offline OK")

    a.stop()
    print("discovery: OK")


def check_messaging():
    received = []
    srv_bob = MessageServer("Bob", tcp_port=46102,
                            on_message=lambda pkt, ip: received.append((pkt["from"], pkt["text"])))
    srv_bob.start()
    time.sleep(0.2)

    msg_id, ok = send_message("127.0.0.1", 46102, "Alice", "Привет, Боб!")
    assert ok, "сообщение не доставлено (нет ack)"
    assert received == [("Alice", "Привет, Боб!")], f"Боб получил не то: {received}"
    print("messaging: доставка + ack OK")

    _, ok = send_message("127.0.0.1", 46999, "Alice", "есть кто?", timeout=0.5)
    assert not ok, "отправка на закрытый порт должна вернуть «не доставлено»"
    print("messaging: узел офлайн -> «не доставлено» OK")

    srv_bob.stop()
    print("messaging: OK")


if __name__ == "__main__":
    check_protocol()
    check_discovery()
    check_messaging()
    print("Все проверки шагов 0-4 пройдены.")
