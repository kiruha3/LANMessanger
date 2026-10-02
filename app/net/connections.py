"""Постоянные TCP-соединения с пирами.

Кто может достучаться — тот устанавливает соединение (с hello-рукопожатием).
Дальше оба участника пишут по нему в обе стороны: достаточно открытого
порта у ОДНОЙ из сторон. Совместим со старым режимом «соединение на
сообщение»: если первый фрейм не hello, а msg — обрабатываем как раньше.
"""

import socket
import threading

from . import protocol
from .constants import MAX_TCP_PAYLOAD, TCP_PORT, is_private_ip

HELLO_TIMEOUT = 5.0
DIAL_TIMEOUT = 3.0


class PeerConn:
    def __init__(self, sock: socket.socket, key: str):
        self.sock = sock
        self.key = key
        self.lock = threading.Lock()
        self.alive = True

    def send_frame(self, data: bytes):
        with self.lock:
            self.sock.sendall(data)

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


class ConnectionManager:
    def __init__(self, name: str, tcp_port: int = TCP_PORT, on_message=None):
        self.name = name
        self.tcp_port = tcp_port
        self.on_message = on_message
        self.allowed_ips: set[str] = set()
        self.conns: dict[str, PeerConn] = {}
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None

    def allow_ip(self, ip: str):
        self.allowed_ips.add(ip)

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
        with self._lock:
            conns = list(self.conns.values())
            self.conns.clear()
        for pc in conns:
            pc.close()
        if self._accept_thread:
            self._accept_thread.join(timeout=2)

    # --- приём входящих ---

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
            threading.Thread(target=self._inbound, args=(conn, addr), daemon=True).start()

    def _inbound(self, conn: socket.socket, addr):
        """Первый фрейм: hello (новый режим) или msg (старый клиент)."""
        conn.settimeout(HELLO_TIMEOUT)
        decoder = protocol.FrameDecoder()
        try:
            data = conn.recv(65536)
            if not data:
                conn.close()
                return
            frames = decoder.feed(data)
            if not frames:
                conn.close()
                return
            first = self._parse(frames[0])
            if first is None:
                conn.close()
                return
            conn.settimeout(None)
            if first["type"] == "hello":
                peer_port = int(first.get("msg_port") or TCP_PORT)
                key = f"{addr[0]}:{peer_port}"
                self._register(key, conn, decoder, frames[1:])
            else:
                # старый клиент: одно сообщение — один коннект, регистрировать нечего
                self._handle_msg(first, addr[0], conn)
                self._legacy_read_loop(conn, decoder, frames[1:], addr[0])
        except (OSError, protocol.ProtocolError):
            conn.close()

    def _legacy_read_loop(self, conn, decoder, buffered, ip):
        try:
            frames = buffered
            while True:
                for frame in frames:
                    pkt = self._parse(frame)
                    if pkt and pkt["type"] == "msg":
                        self._handle_msg(pkt, ip, conn)
                data = conn.recv(65536)
                if not data:
                    break
                frames = decoder.feed(data)
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            conn.close()

    # --- исходящие ---

    def _dial(self, ip: str, port: int, key: str) -> PeerConn | None:
        try:
            sock = socket.create_connection((ip, port), timeout=DIAL_TIMEOUT)
            sock.settimeout(None)
            hello = protocol.encode_frame(protocol.make_packet(
                "hello", name=self.name, msg_port=self.tcp_port))
            sock.sendall(hello)
        except OSError:
            return None
        pc = PeerConn(sock, key)
        self._register(key, sock, protocol.FrameDecoder(), [], pc=pc)
        return pc

    def _register(self, key, conn, decoder, buffered, pc=None):
        pc = pc or PeerConn(conn, key)
        with self._lock:
            old = self.conns.get(key)
            self.conns[key] = pc
        if old and old is not pc:
            old.close()
        threading.Thread(target=self._reader, args=(pc, decoder, buffered),
                         daemon=True).start()
        return pc

    def _reader(self, pc: PeerConn, decoder: protocol.FrameDecoder, buffered):
        try:
            frames = buffered
            while not self._stop.is_set():
                for frame in frames:
                    pkt = self._parse(frame)
                    if pkt:
                        self._dispatch(pkt, pc)
                data = pc.sock.recv(65536)
                if not data:
                    break
                frames = decoder.feed(data)
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            pc.alive = False
            with self._lock:
                if self.conns.get(pc.key) is pc:
                    del self.conns[pc.key]
            pc.close()

    # --- отправка ---

    def send(self, key: str, ip: str, port: int, text: str,
             timeout: float = 3.0) -> tuple[str, bool]:
        """Отправить сообщение. Сначала — по живому соединению (неважно,
        кто его установил), иначе — пробуем подключиться сами."""
        msg_id = protocol.new_id()
        frame = protocol.encode_frame(protocol.make_message(
            self.name, text, msg_id=msg_id, msg_port=self.tcp_port))

        with self._lock:
            pc = self.conns.get(key)
        if not (pc and pc.alive):
            pc = self._dial(ip, port, key)
        if pc is None:
            return msg_id, False

        ev = threading.Event()
        with self._lock:
            self._pending[msg_id] = ev
        try:
            pc.send_frame(frame)
        except OSError:
            with self._lock:
                self._pending.pop(msg_id, None)
            return msg_id, False
        ok = ev.wait(timeout)
        with self._lock:
            self._pending.pop(msg_id, None)
        return msg_id, ok

    # --- разбор пакетов ---

    def _parse(self, frame: bytes) -> dict | None:
        try:
            return protocol.parse_packet(frame, protocol.TYPES_TCP,
                                         max_size=MAX_TCP_PAYLOAD)
        except protocol.ProtocolError:
            return None

    def _dispatch(self, pkt: dict, pc: PeerConn):
        if pkt["type"] == "msg":
            self._handle_msg(pkt, pc.key.rsplit(":", 1)[0], pc)
        elif pkt["type"] == "ack":
            ev = self._pending.get(pkt.get("ack_for", ""))
            if ev:
                ev.set()

    def _handle_msg(self, pkt: dict, ip: str, conn_or_pc):
        ack = protocol.encode_frame(protocol.make_ack(self.name, pkt["id"]))
        try:
            if isinstance(conn_or_pc, PeerConn):
                conn_or_pc.send_frame(ack)
            else:
                conn_or_pc.sendall(ack)
        except OSError:
            pass
        if self.on_message:
            self.on_message(pkt, ip)
