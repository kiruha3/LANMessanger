import json
import os
import threading
from dataclasses import dataclass

from ..net import protocol
from ..net.connections import ConnectionManager
from ..net.constants import TCP_PORT, UDP_PORT, is_private_ip
from ..net.discovery import DiscoveryService, Node
from .history import History, base_dir

SETTINGS_FILE = os.path.join(base_dir(), "settings.json")
DEFAULT_UDP_PORT = UDP_PORT
DEFAULT_TCP_PORT = TCP_PORT


@dataclass
class ChatMessage:
    id: str
    direction: str  # "in" | "out"
    author: str
    text: str
    timestamp: int
    status: str = "received"  # out: sending -> delivered | failed


class Engine:
    """Ядро: discovery + постоянные соединения, чаты в памяти (без БД)."""

    def __init__(self, name: str, udp_port: int = UDP_PORT, tcp_port: int = TCP_PORT,
                 targets: list | None = None):
        self.name = name
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.chats: dict[str, list[ChatMessage]] = {}
        self.unread: dict[str, int] = {}
        self._lock = threading.Lock()
        self.on_message_event = None  # callback(key, ChatMessage) для UI
        try:
            self.history = History()
        except Exception:
            self.history = None  # БД недоступна — работаем в памяти

        self.discovery = DiscoveryService(
            name, udp_port=udp_port, tcp_port=tcp_port, targets=targets,
        )
        self.connections = ConnectionManager(
            name, tcp_port=tcp_port,
            on_message=self._on_message,
        )
        self.load_settings()

    def chat(self, key: str) -> list[ChatMessage]:
        """История чата: из памяти, при первом обращении — подгрузка из БД."""
        with self._lock:
            if key in self.chats:
                return self.chats[key]
        msgs = []
        if self.history:
            msgs = [ChatMessage(id=r[0], direction=r[1], author=r[2],
                                text=r[3], timestamp=r[4], status=r[5])
                    for r in self.history.load(key)]
        with self._lock:
            return self.chats.setdefault(key, msgs)

    def start(self):
        self.connections.start()
        self.discovery.start()

    def stop(self):
        self.discovery.stop()
        self.connections.stop()
        self.save_settings()
        if self.history:
            self.history.close()

    def set_name(self, name: str):
        self.name = name
        self.discovery.name = name
        self.connections.name = name
        self.save_settings()

    def nodes(self) -> list[Node]:
        return self.discovery.registry.snapshot()

    def node_by_key(self, key: str) -> Node | None:
        for n in self.discovery.registry.snapshot():
            if n.key == key:
                return n
        return None

    def add_manual_peer(self, ip: str, udp_port: int = None,
                        tcp_port: int = None, save: bool = True) -> Node:
        """Добавить узел вручную по IP (в т.ч. белому). Возвращает узел.

        Для публичного IP дополнительно разрешает входящие с него."""
        udp_port = udp_port or DEFAULT_UDP_PORT
        tcp_port = tcp_port or DEFAULT_TCP_PORT
        self.discovery.add_target(ip, udp_port)
        if not is_private_ip(ip):
            self.connections.allow_ip(ip)
        node = self.discovery.registry.add_placeholder(ip, tcp_port, name=ip)
        if save:
            peers = self._manual_peers()
            if ip not in peers:
                peers.append(ip)
            self.save_settings(extra={"manual_peers": peers})
        return node

    # --- отправка ---

    def add_outgoing(self, key: str, text: str) -> ChatMessage:
        msg = ChatMessage(
            id=protocol.new_id(), direction="out", author=self.name,
            text=text, timestamp=protocol.now(), status="sending",
        )
        with self._lock:
            self.chats.setdefault(key, []).append(msg)
        if self.history:
            self.history.add(key, msg)
        return msg

    def deliver(self, key: str, msg: ChatMessage, ip: str, port: int):
        db_id = msg.id
        msg_id, ok = self.connections.send(key, ip, port, msg.text)
        msg.id = msg_id
        msg.status = "delivered" if ok else "failed"
        if self.history:
            self.history.update_status(key, db_id, msg.status)

    def send(self, key: str, ip: str, port: int, text: str) -> ChatMessage:
        """Синхронная отправка (для консоли/тестов)."""
        msg = self.add_outgoing(key, text)
        self.deliver(key, msg, ip, port)
        return msg

    # --- приём ---

    def _on_message(self, pkt: dict, ip: str):
        port = int(pkt.get("msg_port") or TCP_PORT)
        key = f"{ip}:{port}"
        msg = ChatMessage(
            id=pkt.get("id", protocol.new_id()), direction="in",
            author=str(pkt.get("from") or ip), text=str(pkt.get("text") or ""),
            timestamp=int(pkt.get("timestamp") or protocol.now()),
        )
        with self._lock:
            self.chats.setdefault(key, []).append(msg)
            self.unread[key] = self.unread.get(key, 0) + 1
        if self.history:
            self.history.add(key, msg)
        if self.on_message_event:
            self.on_message_event(key, msg)

    # --- непрочитанные ---

    def mark_read(self, key: str):
        with self._lock:
            self.unread[key] = 0

    def unread_count(self, key: str) -> int:
        return self.unread.get(key, 0)

    # --- фильтр входящих / отклонённые ---

    def set_accept_all(self, on: bool):
        self.connections.accept_all = on
        self.save_settings()

    def accept_all(self) -> bool:
        return self.connections.accept_all

    def allow_ip(self, ip: str):
        self.connections.allow_ip(ip)
        self.save_settings()

    def rejected_ips(self) -> list[str]:
        seen = []
        for ip in reversed(self.connections.rejected):
            if ip not in seen:
                seen.append(ip)
        return seen

    def dismiss_rejected(self, ip: str):
        self.connections.rejected = type(self.connections.rejected)(
            (x for x in self.connections.rejected if x != ip), maxlen=20)

    # --- настройки (просто JSON-файл, не БД) ---

    def _manual_peers(self) -> list[str]:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return list(json.load(f).get("manual_peers", []))
        except (OSError, json.JSONDecodeError):
            return []

    def save_settings(self, extra: dict | None = None):
        data = {
            "name": self.name,
            "accept_all": self.connections.accept_all,
            "allowed_ips": sorted(self.connections.allowed_ips),
            "manual_peers": self._manual_peers(),
        }
        if extra:
            data.update(extra)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass

    def load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self.connections.accept_all = bool(data.get("accept_all", False))
        for ip in data.get("allowed_ips", []):
            self.connections.allow_ip(str(ip))
        for ip in data.get("manual_peers", []):
            self.add_manual_peer(str(ip), save=False)

    @staticmethod
    def load_saved_name() -> str | None:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                name = json.load(f).get("name")
                return str(name) if name else None
        except (OSError, json.JSONDecodeError):
            return None
