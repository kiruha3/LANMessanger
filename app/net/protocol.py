import json
import struct
import time
import uuid

from .constants import (
    MAX_MESSAGE_LEN,
    MAX_TCP_PAYLOAD,
    MAX_UDP_PACKET,
    PROTO,
)

TYPES_UDP = {"announce", "response", "bye", "alert"}
TYPES_TCP = {"msg", "ack", "read", "alert", "alert_ack", "hello",
             "stream_open", "stream_open_ack", "stream_data", "stream_close"}


class ProtocolError(ValueError):
    pass


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> int:
    return int(time.time())


def make_packet(ptype: str, **fields) -> bytes:
    pkt = {"proto": PROTO, "type": ptype}
    pkt.update(fields)
    return json.dumps(pkt, ensure_ascii=False).encode("utf-8")


def parse_packet(data: bytes, allowed_types: set, max_size: int = MAX_UDP_PACKET) -> dict:
    if len(data) > max_size:
        raise ProtocolError(f"packet too large: {len(data)} bytes")
    try:
        pkt = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"malformed packet: {exc}") from exc
    if not isinstance(pkt, dict):
        raise ProtocolError("packet is not an object")
    if pkt.get("proto") != PROTO:
        raise ProtocolError("unknown protocol")
    if pkt.get("type") not in allowed_types:
        raise ProtocolError(f"unexpected type: {pkt.get('type')!r}")
    return pkt


def encode_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_TCP_PAYLOAD:
        raise ProtocolError(f"payload too large: {len(payload)} bytes")
    return struct.pack(">I", len(payload)) + payload


class FrameDecoder:
    """Склеивает TCP-поток в отдельные фреймы (4 байта длины + JSON)."""

    def __init__(self, max_size: int = MAX_TCP_PAYLOAD):
        self._buf = bytearray()
        self.max_size = max_size

    def feed(self, data: bytes) -> list:
        self._buf += data
        frames = []
        while True:
            if len(self._buf) < 4:
                break
            (size,) = struct.unpack(">I", self._buf[:4])
            if size > self.max_size:
                raise ProtocolError(f"frame too large: {size} bytes")
            if len(self._buf) < 4 + size:
                break
            frames.append(bytes(self._buf[4:4 + size]))
            del self._buf[:4 + size]
        return frames


def make_message(sender: str, text: str, msg_id: str = None, msg_port: int = None) -> bytes:
    if len(text.encode("utf-8")) > MAX_MESSAGE_LEN:
        raise ProtocolError("message too long")
    fields = {"id": msg_id or new_id(), "from": sender, "timestamp": now(), "text": text}
    if msg_port is not None:
        fields["msg_port"] = msg_port
    return make_packet("msg", **fields)


def make_ack(sender: str, ack_for: str) -> bytes:
    return make_packet("ack", id=new_id(), **{"from": sender}, ack_for=ack_for, timestamp=now())
