import socket
import threading

from . import protocol
from .constants import MAX_TCP_PAYLOAD, TCP_PORT, is_private_ip


class MessageServer:
    """TCP-сервер: приём сообщений, ответ ack."""

    def __init__(self, name: str, tcp_port: int = TCP_PORT, on_message=None, on_ack=None):
        self.name = name
        self.tcp_port = tcp_port
        self.on_message = on_message
        self.on_ack = on_ack
        self.allowed_ips: set[str] = set()  # публичные IP, добавленные вручную
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._accept_thread: threading.Thread | None = None

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.tcp_port))
        sock.listen(16)
        sock.settimeout(1.0)
        self._sock = sock
        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self):
        self._stop.set()
        if self._sock:
            self._sock.close()
        if self._accept_thread:
            self._accept_thread.join(timeout=2)

    def allow_ip(self, ip: str):
        self.allowed_ips.add(ip)

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not (is_private_ip(addr[0]) or addr[0] in self.allowed_ips):
                conn.close()
                continue
            threading.Thread(target=self._client_loop, args=(conn, addr), daemon=True).start()

    def _client_loop(self, conn: socket.socket, addr):
        decoder = protocol.FrameDecoder()
        try:
            while not self._stop.is_set():
                data = conn.recv(65536)
                if not data:
                    break
                try:
                    frames = decoder.feed(data)
                except protocol.ProtocolError:
                    break
                for frame in frames:
                    try:
                        pkt = protocol.parse_packet(frame, protocol.TYPES_TCP, max_size=MAX_TCP_PAYLOAD)
                    except protocol.ProtocolError:
                        continue
                    self._dispatch(pkt, addr[0], conn)
        except OSError:
            pass
        finally:
            conn.close()

    def _dispatch(self, pkt: dict, ip: str, conn: socket.socket):
        if pkt["type"] == "msg":
            ack = protocol.make_ack(self.name, pkt["id"])
            try:
                conn.sendall(protocol.encode_frame(ack))
            except OSError:
                pass
            if self.on_message:
                self.on_message(pkt, ip)
        elif pkt["type"] == "ack":
            if self.on_ack:
                self.on_ack(pkt, ip)
