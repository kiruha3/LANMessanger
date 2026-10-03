import json
import os
import threading
import time
from dataclasses import dataclass

from .. import __version__
from ..net import protocol
from ..net.connections import ConnectionManager
from ..net.constants import TCP_PORT, UDP_PORT, is_private_ip
from ..net.discovery import DiscoveryService, Node
from .history import History, base_dir
from .push import PushServer
from .tunnel import TunnelManager

SETTINGS_FILE = os.path.join(base_dir(), "settings.json")
DEFAULT_UDP_PORT = UDP_PORT
DEFAULT_TCP_PORT = TCP_PORT
DEFAULT_UPDATE_URL = "https://github.com/kiruha3/LANMessanger/releases/latest"
DEFAULT_UPDATE_TEXT = ("У {name} новая версия {ver} (у вас {my}). "
                       "Скачать: {url}")


@dataclass
class ChatMessage:
    id: str
    direction: str  # "in" | "out"
    author: str
    text: str
    timestamp: int
    status: str = "received"  # out: sending -> delivered | failed
    img: str | None = None    # имя файла в images/ (картинка из буфера)


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
        self._push_enabled = False
        self._push_port = 8087
        self._push_topic = "lanalerts"
        self._wd_stop = threading.Event()
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
        self.tunnel = TunnelManager(self.connections)
        self.on_tunnel_error = None  # callback(key, текст) для UI
        self.tunnel.on_error = self._tunnel_error_relay
        self.on_reminder = None        # callback(event dict) — напоминание
        self.on_events_changed = None  # callback() — события изменились
        self.on_notification = None    # callback(note dict) — колокольчик
        self.notifications: list[dict] = []
        self._notified_versions: set[str] = set()
        self.update_url = DEFAULT_UPDATE_URL
        self.update_text = DEFAULT_UPDATE_TEXT
        self.notify_update = True
        self.connections.on_connect = self._on_peer_connected
        self.connections.on_events = self._on_events_packet
        self._remind_interval = 30.0
        self._dial_attempts: dict[str, float] = {}
        self.discovery.on_node_new = self._auto_connect
        self.discovery.on_node_update = self._auto_connect
        self.theme = "light"
        self.push: PushServer | None = None
        self.rdp_enabled = True
        self.scan_enabled = True
        self.shadow_rdp = False  # RDP: совместный сеанс (shadow) — по умолчанию выкл
        self.sort_mode = "status"  # status | name | ip
        self.load_settings()

    def _tunnel_error_relay(self, key: str, text: str):
        self.add_notification("RDP-туннель", text)
        if self.on_tunnel_error:
            self.on_tunnel_error(key, text)

    # --- центр уведомлений ---

    def add_notification(self, title: str, text: str, link: str = None,
                         kind: str = "info"):
        # дедупликация одинаковых уведомлений
        for n in self.notifications:
            if n["title"] == title and n["text"] == text and not n["read"]:
                return
        note = {"id": protocol.new_id(), "ts": protocol.now(), "title": title,
                "text": text, "link": link, "kind": kind, "read": False}
        self.notifications.append(note)
        if self.on_notification:
            self.on_notification(note)

    def unread_notifications(self) -> int:
        return sum(1 for n in self.notifications if not n["read"])

    def mark_notifications_read(self):
        for n in self.notifications:
            n["read"] = True

    def _check_peer_version(self, node: Node):
        """У пира новее версия -> информационное уведомление.
        Ссылка — от уведомляющего клиента (его update_url из announce),
        иначе наша. Текст — редактируемый шаблон в настройках."""
        if not self.notify_update or not node.version:
            return
        if node.version in self._notified_versions:
            return
        try:
            newer = tuple(int(x) for x in node.version.split(".")) > \
                    tuple(int(x) for x in __version__.split("."))
        except (ValueError, AttributeError):
            return
        if not newer:
            return
        self._notified_versions.add(node.version)
        url = node.update_url or self.update_url
        template = node.update_text or self.update_text
        try:
            text = template.format(name=node.name, ver=node.version,
                                   my=__version__, url=url)
        except (KeyError, IndexError):
            text = self.update_text.format(name=node.name, ver=node.version,
                                           my=__version__, url=url)
        self.add_notification(f"Новая версия {node.version}", text,
                              link=url, kind="update")

    # --- календарь: события, рассылка, напоминания ---

    @staticmethod
    def _event_out(ev: dict) -> dict:
        return {k: ev[k] for k in ("id", "title", "ts", "remind_min",
                                   "creator", "participants", "updated_at",
                                   "deleted")}

    def add_event(self, title: str, ts: int, remind_min: int = 15,
                  participants: list | None = None) -> dict | None:
        if not self.history:
            return None
        ev = {"id": protocol.new_id(), "title": title, "ts": int(ts),
              "remind_min": int(remind_min), "creator": self.name,
              "participants": participants or [], "updated_at": protocol.now(),
              "deleted": False}
        self.history.upsert_event(ev)
        self.connections.broadcast_packet("event_add", **self._event_out(ev))
        if self.on_events_changed:
            self.on_events_changed()
        return ev

    def update_event(self, event_id: str, **fields):
        if not self.history:
            return
        cur = [e for e in self.history.all_events() if e["id"] == event_id]
        if not cur:
            return
        ev = cur[0]
        ev.update(fields)
        ev["updated_at"] = protocol.now()
        self.history.upsert_event(ev)
        self.connections.broadcast_packet("event_add", **self._event_out(ev))
        if self.on_events_changed:
            self.on_events_changed()

    def delete_event(self, event_id: str):
        """Мягкое удаление: tombstone, иначе событие воскреснет при sync."""
        self.update_event(event_id, deleted=True)

    def visible_events(self) -> list[dict]:
        if not self.history:
            return []
        return [e for e in self.history.all_events()
                if self._visible_to_me(e)]

    def _visible_to_me(self, ev: dict) -> bool:
        if ev["creator"] == self.name:
            return True
        parts = ev.get("participants") or []
        return "all" in parts or self.name in parts

    def _on_events_packet(self, pkt: dict, pc):
        ptype = pkt["type"]
        changed = False
        if ptype == "event_add":
            ev = {k: pkt.get(k) for k in ("id", "title", "ts", "remind_min",
                                          "creator", "participants",
                                          "updated_at", "deleted")}
            changed = bool(self.history and self.history.upsert_event(ev))
        elif ptype == "event_sync":
            for raw in pkt.get("events", []):
                ev = {k: raw.get(k) for k in ("id", "title", "ts",
                                              "remind_min", "creator",
                                              "participants", "updated_at",
                                              "deleted")}
                if self.history and self.history.upsert_event(ev):
                    changed = True
        if changed and self.on_events_changed:
            self.on_events_changed()

    def _on_peer_connected(self, key: str):
        """Новый канал — отдать свои события (включая tombstones)."""
        def _send():
            time.sleep(0.5)
            with self.connections._lock:
                pc = self.connections.conns.get(key)
            if not (pc and pc.alive and self.history):
                return
            evs = [self._event_out(e)
                   for e in self.history.all_events(include_deleted=True)]
            if evs:
                try:
                    pc.send_packet("event_sync", events=evs)
                except OSError:
                    pass
        threading.Thread(target=_send, daemon=True).start()

    def _reminder_loop(self):
        """Раз в remind_interval сек: события в окне напоминания → алерт.
        Флаг reminded в БД защищает от дублей (в отличие от чистого поллинга)."""
        while not self._wd_stop.wait(self._remind_interval):
            for ev in self.history.due_events(protocol.now()):
                if not self._visible_to_me(ev):
                    continue
                self.history.mark_reminded(ev["id"])
                self.add_notification(
                    f"Напоминание: {ev['title']}",
                    time.strftime("%d.%m %H:%M", time.localtime(ev["ts"])),
                    kind="reminder")
                if self.on_reminder:
                    self.on_reminder(ev)

    def _auto_connect(self, node: Node):
        """Канал к узлу поднимается сам, как только он обнаружен:
        достучаться сможет хотя бы одна из сторон."""
        self._check_peer_version(node)
        if not node.online or self.connections.is_connected(node.key):
            return
        now = protocol.now()
        if now - self._dial_attempts.get(node.key, 0) < 30:
            return
        self._dial_attempts[node.key] = now
        threading.Thread(target=self.connections.get_or_dial,
                         args=(node.key, node.ip, node.tcp_port),
                         daemon=True).start()

    def chat(self, key: str) -> list[ChatMessage]:
        """История чата: из памяти, при первом обращении — подгрузка из БД."""
        with self._lock:
            if key in self.chats:
                return self.chats[key]
        msgs = []
        if self.history:
            msgs = [ChatMessage(id=r[0], direction=r[1], author=r[2],
                                text=r[3], timestamp=r[4], status=r[5],
                                img=r[6] if len(r) > 6 else None)
                    for r in self.history.load(key)]
        with self._lock:
            return self.chats.setdefault(key, msgs)

    def start(self):
        self.connections.start()
        self.discovery.start()
        self._wd_stop.clear()
        threading.Thread(target=self._channel_watchdog, daemon=True).start()
        if self.history:
            self.history.init_events()
            threading.Thread(target=self._reminder_loop, daemon=True).start()

    def stop(self):
        self._wd_stop.set()
        self.discovery.stop()
        self.connections.stop()
        if self.push:
            self.push.stop()
        self.save_settings()
        if self.history:
            self.history.close()

    def _channel_watchdog(self):
        """Тестовый сигнал: сразу и далее каждые 15 сек пытаемся поднять
        канал со всеми известными узлами (даже offline — вдруг достучимся).
        Кто может достучаться — тот и подключается, ждать сообщения не нужно."""
        while True:
            for node in self.discovery.registry.snapshot():
                if self.connections.is_connected(node.key):
                    continue
                now = protocol.now()
                if now - self._dial_attempts.get(node.key, 0) < 30:
                    continue
                self._dial_attempts[node.key] = now
                threading.Thread(target=self.connections.get_or_dial,
                                 args=(node.key, node.ip, node.tcp_port),
                                 daemon=True).start()
            if self._wd_stop.wait(15):
                return

    def set_name(self, name: str):
        self.name = name
        self.discovery.name = name
        self.connections.name = name
        self.save_settings()

    def nodes(self) -> list[Node]:
        nodes = self.discovery.registry.snapshot()
        for n in nodes:
            if not n.online and self.connections.is_connected(n.key):
                n.online = True  # живой TCP-канал = узел в сети, даже без UDP
        return nodes

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

    def add_outgoing(self, key: str, text: str, img: str = None) -> ChatMessage:
        msg = ChatMessage(
            id=protocol.new_id(), direction="out", author=self.name,
            text=text, timestamp=protocol.now(), status="sending", img=img,
        )
        with self._lock:
            self.chats.setdefault(key, []).append(msg)
        if self.history:
            self.history.add(key, msg)
        return msg

    def deliver(self, key: str, msg: ChatMessage, ip: str, port: int):
        db_id = msg.id
        img_b64 = None
        if msg.img:
            try:
                import base64
                with open(self.img_path(msg.img), "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("ascii")
            except OSError:
                msg.status = "failed"
                return
        msg_id, ok = self.connections.send(key, ip, port, msg.text, img=img_b64)
        msg.id = msg_id
        msg.status = "delivered" if ok else "failed"
        if self.history:
            self.history.update_status(key, db_id, msg.status)

    @staticmethod
    def img_path(name: str) -> str:
        return os.path.join(base_dir(), "images", name)

    def save_incoming_img(self, msg_id: str, b64: str) -> str | None:
        import base64
        try:
            os.makedirs(os.path.join(base_dir(), "images"), exist_ok=True)
            name = f"{msg_id}.png"
            with open(self.img_path(name), "wb") as f:
                f.write(base64.b64decode(b64))
            return name
        except (OSError, ValueError):
            return None

    def send(self, key: str, ip: str, port: int, text: str) -> ChatMessage:
        """Синхронная отправка (для консоли/тестов)."""
        msg = self.add_outgoing(key, text)
        self.deliver(key, msg, ip, port)
        return msg

    # --- приём ---

    def _on_message(self, pkt: dict, ip: str):
        port = int(pkt.get("msg_port") or TCP_PORT)
        key = f"{ip}:{port}"
        img = None
        if pkt.get("img"):
            img = self.save_incoming_img(str(pkt.get("id")), pkt["img"])
        msg = ChatMessage(
            id=pkt.get("id", protocol.new_id()), direction="in",
            author=str(pkt.get("from") or ip), text=str(pkt.get("text") or ""),
            timestamp=int(pkt.get("timestamp") or protocol.now()),
            img=img,
        )
        with self._lock:
            self.chats.setdefault(key, []).append(msg)
            self.unread[key] = self.unread.get(key, 0) + 1
        if self.history:
            self.history.add(key, msg)
        if self.push:
            self.push.broadcast(msg.author, msg.text)
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

    def set_theme(self, theme_name: str):
        self.theme = theme_name
        self.save_settings()

    def set_update_url(self, url: str):
        self.update_url = url
        self.discovery.update_url = url  # уезжает в announce сразу
        self.save_settings()

    def set_update_text(self, text: str):
        self.update_text = text
        self.discovery.update_text = text  # уезжает в announce сразу
        self.save_settings()

    def set_notify_update(self, on: bool):
        self.notify_update = bool(on)
        self.save_settings()

    def set_feature(self, name: str, on: bool):
        """Фичефлаг: rdp_enabled / scan_enabled."""
        setattr(self, name, bool(on))
        if name == "rdp_enabled":
            self.tunnel.enabled = bool(on)
        self.save_settings()

    def set_sort_mode(self, mode: str):
        self.sort_mode = mode
        self.save_settings()

    # --- push на телефон (ntfy) ---

    def set_push(self, enabled: bool, port: int = 8087, topic: str = "lanalerts"):
        if self.push:
            self.push.stop()
            self.push = None
        self._push_enabled = enabled
        self._push_port = port
        self._push_topic = topic
        if enabled:
            self.push = PushServer(port=port, topic=topic)
            if not self.push.start():
                self.push = None
                self._push_enabled = False
        self.save_settings()

    def push_status(self) -> tuple[bool, int, str]:
        return (bool(self.push), self._push_port, self._push_topic)

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
            "theme": self.theme,
            "rdp_enabled": self.rdp_enabled,
            "scan_enabled": self.scan_enabled,
            "shadow_rdp": self.shadow_rdp,
            "sort_mode": self.sort_mode,
            "update_url": self.update_url,
            "update_text": self.update_text,
            "notify_update": self.notify_update,
            "push": {
                "enabled": self._push_enabled,
                "port": self._push_port,
                "topic": self._push_topic,
            },
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
        self.theme = str(data.get("theme", "light"))
        self.set_feature("rdp_enabled", bool(data.get("rdp_enabled", True)))
        self.scan_enabled = bool(data.get("scan_enabled", True))
        self.shadow_rdp = bool(data.get("shadow_rdp", False))
        self.sort_mode = str(data.get("sort_mode", "status"))
        self.update_url = str(data.get("update_url") or DEFAULT_UPDATE_URL)
        self.update_text = str(data.get("update_text") or DEFAULT_UPDATE_TEXT)
        self.notify_update = bool(data.get("notify_update", True))
        self.discovery.update_url = self.update_url
        self.discovery.update_text = self.update_text
        push = data.get("push") or {}
        if push.get("enabled"):
            self.set_push(True, int(push.get("port", 8087)),
                          str(push.get("topic", "lanalerts")))
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
