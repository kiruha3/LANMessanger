import socket
import time

from . import protocol
from .constants import MAX_TCP_PAYLOAD


def send_message(ip: str, port: int, sender: str, text: str,
                 timeout: float = 3.0, msg_port: int = None) -> tuple[str, bool]:
    """Отправить сообщение узлу. Возвращает (msg_id, доставлено)."""
    msg_id = protocol.new_id()
    data = protocol.encode_frame(
        protocol.make_message(sender, text, msg_id=msg_id, msg_port=msg_port))
    try:
        with socket.create_connection((ip, port), timeout=timeout) as conn:
            conn.sendall(data)
            decoder = protocol.FrameDecoder()
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return msg_id, False
                conn.settimeout(remaining)
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    return msg_id, False
                if not chunk:
                    return msg_id, False
                for frame in decoder.feed(chunk):
                    try:
                        pkt = protocol.parse_packet(frame, protocol.TYPES_TCP,
                                                    max_size=MAX_TCP_PAYLOAD)
                    except protocol.ProtocolError:
                        continue
                    if pkt["type"] == "ack" and pkt.get("ack_for") == msg_id:
                        return msg_id, True
    except OSError:
        return msg_id, False
