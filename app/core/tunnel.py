"""Проброс TCP-портов через канал мессенджера (для RDP и т.п.).

Инициатор: слушает 127.0.0.1:<local_port>, при подключении открывает
stream до узла. Принимающая сторона: подключается к своему
127.0.0.1:<target_port> и гонит данные в обе стороны.
"""

import base64
import socket
import threading

from ..net import protocol
from ..net.connections import ConnectionManager, PeerConn

CHUNK = 32 * 1024
ALLOWED_REMOTE_PORTS = {3389}  # только RDP
OPEN_TIMEOUT = 6.0


class Stream:
    def __init__(self, sid: str, sock: socket.socket, pc: PeerConn):
        self.id = sid
        self.sock = sock
        self.pc = pc
        self.lock = threading.Lock()
        self.alive = True

    def send(self, data: bytes):
        with self.lock:
            self.sock.sendall(data)

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


class TunnelManager:
    def __init__(self, connections: ConnectionManager):
        self.cm = connections
        connections.on_stream = self._on_stream
        self.enabled = True  # выкл = не принимать туннели к нам (RDP отключён)
        self.tunnels: dict[str, dict] = {}      # key узла -> состояние
        self.streams: dict[str, Stream] = {}    # stream_id -> Stream
        self._lock = threading.Lock()
        self._open_events: dict[str, tuple] = {}

    # --- инициатор ---

    def open_rdp(self, key: str, ip: str, port: int, local_port: int = 3390,
                 remote_port: int = 3389) -> tuple[bool, str]:
        if key in self.tunnels:
            return True, str(self.tunnels[key]["port"])
        if not self.cm.get_or_dial(key, ip, port):
            return False, "нет соединения с узлом"
        listener = None
        for lp in range(local_port, local_port + 10):
            try:
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", lp))
                listener.listen(4)
                break
            except OSError:
                listener = None
        if listener is None:
            return False, "не удалось занять локальный порт"
        actual_port = listener.getsockname()[1]
        self.tunnels[key] = {
            "listener": listener, "port": actual_port,
            "remote_port": remote_port, "ip": ip, "node_port": port,
            "streams": set(),
        }
        threading.Thread(target=self._accept_loop, args=(key, listener),
                         daemon=True).start()
        return True, str(actual_port)

    def close_rdp(self, key: str):
        state = self.tunnels.pop(key, None)
        if not state:
            return
        try:
            state["listener"].close()
        except OSError:
            pass
        with self._lock:
            sids = [sid for sid in state["streams"] if sid in self.streams]
        for sid in sids:
            stream = self.streams.get(sid)
            if stream:
                self._close_stream(stream, notify=True)

    def rdp_port(self, key: str) -> int | None:
        state = self.tunnels.get(key)
        return state["port"] if state else None

    def _accept_loop(self, key: str, listener: socket.socket):
        while key in self.tunnels:
            try:
                sock, _ = listener.accept()
            except OSError:
                break
            threading.Thread(target=self._initiate_stream, args=(key, sock),
                             daemon=True).start()

    def _initiate_stream(self, key: str, sock: socket.socket):
        state = self.tunnels.get(key)
        if not state:
            sock.close()
            return
        pc = self.cm.get_or_dial(key, state["ip"], state["node_port"])
        if not pc:
            sock.close()
            return
        sid = protocol.new_id()
        ev = threading.Event()
        res: dict = {}
        with self._lock:
            self._open_events[sid] = (ev, res)
        try:
            pc.send_packet("stream_open", id=sid, target_port=state["remote_port"])
        except OSError:
            sock.close()
            with self._lock:
                self._open_events.pop(sid, None)
            return
        if not ev.wait(OPEN_TIMEOUT) or not res.get("ok"):
            sock.close()
            with self._lock:
                self._open_events.pop(sid, None)
            return
        with self._lock:
            self._open_events.pop(sid, None)
        stream = Stream(sid, sock, pc)
        with self._lock:
            self.streams[sid] = stream
            state["streams"].add(sid)
        self._pump_out(stream)

    # --- общий насос: локальный сокет -> канал ---

    def _pump_out(self, stream: Stream):
        try:
            while stream.alive:
                data = stream.sock.recv(CHUNK)
                if not data:
                    break
                stream.pc.send_packet(
                    "stream_data", id=stream.id,
                    data=base64.b64encode(data).decode("ascii"))
        except OSError:
            pass
        self._close_stream(stream, notify=True)

    def _close_stream(self, stream: Stream, notify: bool):
        with self._lock:
            existed = self.streams.pop(stream.id, None)
        if existed is None:
            return
        if notify:
            try:
                stream.pc.send_packet("stream_close", id=stream.id)
            except OSError:
                pass
        stream.close()

    # --- приём кадров канала ---

    def _on_stream(self, pkt: dict, pc: PeerConn):
        ptype = pkt["type"]
        sid = pkt.get("id", "")
        if ptype == "stream_open":
            self._respond_stream(pkt, pc)
        elif ptype == "stream_open_ack":
            with self._lock:
                entry = self._open_events.get(sid)
            if entry:
                entry[1].update(pkt)
                entry[0].set()
        elif ptype == "stream_data":
            stream = self.streams.get(sid)
            if stream:
                try:
                    stream.send(base64.b64decode(pkt.get("data", "")))
                except OSError:
                    self._close_stream(stream, notify=True)
        elif ptype == "stream_close":
            stream = self.streams.pop(sid, None)
            if stream:
                stream.close()

    def _respond_stream(self, pkt: dict, pc: PeerConn):
        """Мы — принимающая сторона: подключаемся к своему localhost:target."""
        sid = pkt.get("id", "")
        if not self.enabled:
            pc.send_packet("stream_open_ack", id=sid, ok=False,
                           error="RDP отключён на удалённом компьютере")
            return
        target = int(pkt.get("target_port", 0))
        if target not in ALLOWED_REMOTE_PORTS:
            pc.send_packet("stream_open_ack", id=sid, ok=False,
                           error="port not allowed")
            return
        try:
            sock = socket.create_connection(("127.0.0.1", target), timeout=3)
        except OSError:
            pc.send_packet("stream_open_ack", id=sid, ok=False,
                           error="target unreachable")
            return
        pc.send_packet("stream_open_ack", id=sid, ok=True)
        stream = Stream(sid, sock, pc)
        with self._lock:
            self.streams[sid] = stream
        threading.Thread(target=self._pump_out, args=(stream,),
                         daemon=True).start()
