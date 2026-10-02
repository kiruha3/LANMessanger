import socket
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import protocol
from .constants import (
    ANNOUNCE_INTERVAL,
    BROADCAST_ADDR,
    NODE_TIMEOUT,
    TCP_PORT,
    UDP_PORT,
)


@dataclass
class Node:
    node_id: str
    ip: str
    name: str
    tcp_port: int
    last_seen: float = field(default_factory=time.time)
    online: bool = True

    @property
    def key(self) -> str:
        return f"{self.ip}:{self.tcp_port}"


class NodeRegistry:
    def __init__(self):
        self._nodes: dict[str, Node] = {}
        self._lock = threading.Lock()

    def upsert(self, node_id: str, ip: str, name: str, tcp_port: int) -> tuple[Node, bool]:
        with self._lock:
            key = f"{ip}:{tcp_port}"
            node = self._nodes.get(key)
            is_new = node is None
            if is_new:
                node = Node(node_id=node_id, ip=ip, name=name, tcp_port=tcp_port)
                self._nodes[key] = node
            node.node_id = node_id
            node.name = name
            node.last_seen = time.time()
            node.online = True
            return node, is_new

    def mark_gone(self, ip: str, tcp_port: int) -> Node | None:
        with self._lock:
            node = self._nodes.get(f"{ip}:{tcp_port}")
            if node and node.online:
                node.online = False
                return node
            return None

    def add_placeholder(self, ip: str, tcp_port: int, name: str = "") -> Node:
        """Узел, добавленный вручную по IP: offline, пока не ответит на announce."""
        with self._lock:
            key = f"{ip}:{tcp_port}"
            node = self._nodes.get(key)
            if node is None:
                node = Node(node_id="", ip=ip, name=name or ip, tcp_port=tcp_port,
                            online=False)
                node.last_seen = 0.0
                self._nodes[key] = node
            return node

    def sweep(self, timeout: float) -> list[Node]:
        cutoff = time.time() - timeout
        gone = []
        with self._lock:
            for node in self._nodes.values():
                if node.online and node.last_seen < cutoff:
                    node.online = False
                    gone.append(node)
        return gone

    def snapshot(self) -> list[Node]:
        """Список узлов без дублей: один и тот же клиент (node_id) может
        анонсироваться с нескольких адресов (белый IP, VPN, LAN) —
        оставляем самую свежую запись. Сортировка: онлайн, потом офлайн."""
        with self._lock:
            nodes = list(self._nodes.values())
        best: dict[str, Node] = {}
        result: list[Node] = []
        for n in nodes:
            if not n.node_id:
                result.append(n)  # ручной пир без id — как есть
                continue
            cur = best.get(n.node_id)
            if (cur is None
                    or (n.online and not cur.online)
                    or (n.online == cur.online and n.last_seen > cur.last_seen)):
                best[n.node_id] = n
        result.extend(best.values())
        return sorted(result,
                      key=lambda n: (not n.online, n.name.lower(), n.ip))


class DiscoveryService:
    """UDP discovery: announce / response / bye в локальной подсети."""

    def __init__(
        self,
        name: str,
        udp_port: int = UDP_PORT,
        tcp_port: int = TCP_PORT,
        targets: list | None = None,
        interval: float = ANNOUNCE_INTERVAL,
        node_timeout: float = NODE_TIMEOUT,
        on_node_new=None,
        on_node_update=None,
        on_node_gone=None,
    ):
        self.name = name
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.targets = targets or [(BROADCAST_ADDR, udp_port)]
        self.interval = interval
        self.node_timeout = node_timeout
        self.on_node_new = on_node_new
        self.on_node_update = on_node_update
        self.on_node_gone = on_node_gone

        self.node_id = uuid.uuid4().hex
        self.registry = NodeRegistry()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", self.udp_port))
        sock.settimeout(1.0)
        self._sock = sock
        self._stop.clear()
        for target_fn in (self._send_loop, self._recv_loop, self._sweep_loop):
            t = threading.Thread(target=target_fn, daemon=True)
            t.start()
            self._threads.append(t)

    def add_target(self, ip: str, port: int):
        """Добавить узел вручную (если broadcast недоступен или белый IP)."""
        if (ip, port) not in self.targets:
            self.targets.append((ip, port))
        if self._sock and not self._stop.is_set():
            self._announce()

    def stop(self):
        if self._stop.is_set():
            return
        if self._sock:
            self._broadcast(protocol.make_packet(
                "bye", node=self.node_id, name=self.name, msg_port=self.tcp_port,
            ))
        self._stop.set()
        if self._sock:
            self._sock.close()
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()

    def _broadcast(self, data: bytes):
        for addr, port in self.targets:
            try:
                self._sock.sendto(data, (addr, port))
            except OSError:
                pass

    def _announce(self):
        self._broadcast(protocol.make_packet(
            "announce", node=self.node_id, name=self.name, msg_port=self.tcp_port,
        ))

    def _send_loop(self):
        self._announce()
        while not self._stop.wait(self.interval):
            self._announce()

    def _recv_loop(self):
        while not self._stop.is_set():
            try:
                data, (ip, port) = self._sock.recvfrom(protocol.MAX_UDP_PACKET + 64)
            except socket.timeout:
                continue
            except OSError:
                # Windows: ICMP port unreachable от прошлого sendto приходит
                # как WSAECONNRESET на recvfrom — игнорируем; при остановке
                # сокет закрыт — выходим.
                if self._stop.is_set():
                    break
                continue
            try:
                pkt = protocol.parse_packet(data, protocol.TYPES_UDP)
            except protocol.ProtocolError:
                continue
            if pkt.get("node") == self.node_id:
                continue
            self._handle(pkt, ip, port)

    def _handle(self, pkt: dict, ip: str, port: int):
        ptype = pkt["type"]
        tcp_port = int(pkt.get("msg_port") or TCP_PORT)
        name = str(pkt.get("name") or ip)
        if ptype in ("announce", "response"):
            node, is_new = self.registry.upsert(pkt.get("node", ""), ip, name, tcp_port)
            if is_new and self.on_node_new:
                self.on_node_new(node)
            elif not is_new and self.on_node_update:
                self.on_node_update(node)
            if ptype == "announce":
                reply = protocol.make_packet(
                    "response", node=self.node_id, name=self.name, msg_port=self.tcp_port,
                )
                try:
                    self._sock.sendto(reply, (ip, port))
                except OSError:
                    pass
        elif ptype == "bye":
            node = self.registry.mark_gone(ip, tcp_port)
            if node and self.on_node_gone:
                self.on_node_gone(node)

    def _sweep_loop(self):
        while not self._stop.wait(min(5.0, self.node_timeout / 2)):
            for node in self.registry.sweep(self.node_timeout):
                if self.on_node_gone:
                    self.on_node_gone(node)
