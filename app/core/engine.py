import collections
import json
import os
import threading
import time
from dataclasses import dataclass

from .. import __version__
from ..net import crypto, protocol
from ..net.connections import ConnectionManager
from ..net.constants import TCP_PORT, UDP_PORT, is_private_ip
from ..net.discovery import DiscoveryService, Node
from .history import History, base_dir
from .push import PushServer
from .tunnel import TunnelManager

SETTINGS_FILE = os.path.join(base_dir(), "settings.json")
DEFAULT_UDP_PORT = UDP_PORT
DEFAULT_TCP_PORT = TCP_PORT
DEFAULT_UPDATE_URL = ("https://github.com/kiruha3/LANMessanger/"
                      "releases/latest/download/LANMessenger.exe")
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
        self.muted_chats: set[str] = set()  # ключи чатов без тостов/звука
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
        self.on_rooms_changed = None   # callback() — комнаты/участники изменились
        self.rooms: dict[str, set[str]] = {}  # комната -> ключи каналов участников
        self.my_rooms: list[dict] = []  # {"hub_key", "hub_name", "room", "members"}
        self._saved_rooms: list[list[str]] = []  # из settings, пере-join в start()
        self.hub_enabled = False  # принимать комнаты (быть хабом) — по свитчу
        self._seen_ids: set[str] = set()
        self._seen_order: collections.deque = collections.deque()
        self.connections.on_hub = self._on_hub
        self.connections.on_disconnect = self._on_peer_disconnected
        self.on_notification = None    # callback(note dict) — колокольчик
        self.notifications: list[dict] = []
        self._notified_versions: set[str] = set()
        self.update_url = DEFAULT_UPDATE_URL
        self.update_text = DEFAULT_UPDATE_TEXT
        self.notify_update = False  # алерты о версиях — по свитчу
        self.connections.on_connect = self._on_peer_connected
        self.connections.on_events = self._on_events_packet
        self._remind_interval = 30.0
        self._dial_attempts: dict[str, float] = {}
        self.discovery.on_node_new = self._auto_connect
        self.discovery.on_node_update = self._auto_connect
        self.theme = "light"
        self.push: PushServer | None = None
        self.rdp_enabled = True
        self.scan_enabled = False  # сканер — по свитчу
        self.shadow_rdp = False  # RDP: совместный сеанс (shadow) — по умолчанию выкл
        self.sort_mode = "status"  # status | name | ip
        self.manual_ip_enabled = False  # поле «IP вручную»
        self.room_join_enabled = False  # поле «комната@IP»
        self.tls_enabled = True    # TLS на TCP-канал (self-signed + пиннинг)
        self.network_psk = ""      # сетевой пароль: PSK-шифрование личных msg
        self.known_fingerprints: dict[str, str] = {}  # key -> отпечаток TLS пира
        self._p2p_key_cache: tuple[str, bytes] | None = None
        self.connections.on_tls_fingerprint = self._on_tls_fingerprint
        self.load_settings()
        self._apply_tls()

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
        """Новый канал — отдать свои события (включая tombstones)
        и перезайти в комнаты этого хаба (после обрыва членство теряется)."""
        def _send():
            time.sleep(0.5)
            with self.connections._lock:
                pc = self.connections.conns.get(key)
            if not (pc and pc.alive):
                return
            # re-join комнат, привязанных к этому хабу
            for entry in self.my_rooms:
                if entry["hub_key"] == key:
                    try:
                        pc.send_packet("hub_join", room=entry["room"])
                    except OSError:
                        pass
            if not self.history:
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
        if self._is_own_key(node.key):
            return  # сам себе хаб не нужен
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
        for hub_key, room, password in self._saved_rooms:
            threading.Thread(target=self._rejoin_loop,
                             args=(hub_key, room, password),
                             daemon=True).start()

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
                if self._is_own_key(node.key):
                    continue  # не дозваниваемся до самих себя
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

    def remove_manual_peer(self, ip: str):
        """Удалить ручной пир из списка и настроек."""
        peers = self._manual_peers()
        if ip in peers:
            peers.remove(ip)
            self.save_settings(extra={"manual_peers": peers})
        self.discovery.targets = [t for t in self.discovery.targets
                                  if t[0] != ip]
        self.discovery.registry.remove(f"{ip}:{DEFAULT_TCP_PORT}")

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
        p2p_key = self._p2p_key()
        img_payload = None
        if msg.img:
            try:
                with open(self.img_path(msg.img), "rb") as f:
                    img_raw = f.read()
            except OSError:
                msg.status = "failed"
                return
            if p2p_key:
                img_payload = crypto.encrypt(p2p_key, img_raw)
            else:
                import base64
                img_payload = base64.b64encode(img_raw).decode("ascii")
        text = msg.text
        if p2p_key:
            text = crypto.encrypt(p2p_key, msg.text.encode("utf-8"))
        msg_id, ok = self.connections.send(key, ip, port, text,
                                           img=img_payload, enc=bool(p2p_key))
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
            data = base64.b64decode(b64)
        except ValueError:
            return None
        return self.save_incoming_img_bytes(msg_id, data)

    def save_incoming_img_bytes(self, msg_id: str, data: bytes) -> str | None:
        try:
            os.makedirs(os.path.join(base_dir(), "images"), exist_ok=True)
            name = f"{msg_id}.png"
            with open(self.img_path(name), "wb") as f:
                f.write(data)
            return name
        except OSError:
            return None

    def send(self, key: str, ip: str, port: int, text: str) -> ChatMessage:
        """Синхронная отправка (для консоли/тестов)."""
        msg = self.add_outgoing(key, text)
        self.deliver(key, msg, ip, port)
        return msg

    # --- приём ---

    def _seen_or_add(self, msg_id: str) -> bool:
        """True, если id уже встречался (дедуп: P2P + hub могут прислать дважды).
        Множество ограничено 5000 id, вытеснение FIFO."""
        with self._lock:
            if msg_id in self._seen_ids:
                return True
            self._seen_ids.add(msg_id)
            self._seen_order.append(msg_id)
            while len(self._seen_order) > 5000:
                self._seen_ids.discard(self._seen_order.popleft())
            return False

    def _on_message(self, pkt: dict, ip: str):
        msg_id = str(pkt.get("id") or protocol.new_id())
        if self._seen_or_add(msg_id):
            return
        port = int(pkt.get("msg_port") or TCP_PORT)
        key = f"{ip}:{port}"
        text = str(pkt.get("text") or "")
        img = None
        if crypto.is_encrypted(pkt):
            # личное сообщение под сетевым паролем (PSK)
            p2p_key = self._p2p_key()
            plain = crypto.decrypt(p2p_key, text) if p2p_key else None
            if plain is None:
                text = "[не удалось расшифровать — неверный сетевой пароль]"
            else:
                text = plain.decode("utf-8", errors="replace")
                if pkt.get("img"):
                    img_raw = crypto.decrypt(p2p_key, pkt["img"])
                    if img_raw is not None:
                        img = self.save_incoming_img_bytes(msg_id, img_raw)
        elif pkt.get("img"):
            img = self.save_incoming_img(msg_id, pkt["img"])
        msg = ChatMessage(
            id=msg_id, direction="in",
            author=str(pkt.get("from") or ip), text=text,
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

    # --- комнаты (хаб + клиент) ---

    @staticmethod
    def room_chat_key(hub_key: str, room: str) -> str:
        return f"room:{hub_key}/{room}"

    def _own_hub_key(self) -> str:
        """Ключ своих комнат у себя (канала к самому себе нет)."""
        return f"127.0.0.1:{self.tcp_port}"

    def _is_own_key(self, key: str) -> bool:
        return key == self._own_hub_key()

    def join_room(self, hub_key: str, room: str, password: str = None) -> bool:
        if self._is_own_key(hub_key):
            # своя комната у себя — дозвон не нужен, только локальная запись
            self._ensure_hub_room(room, password)
            self.save_settings()
            if self.on_rooms_changed:
                self.on_rooms_changed()
            return True
        ip, _, port = hub_key.rpartition(":")
        node = self.node_by_key(hub_key)
        if node is None:
            # узел-хаб должен быть в списке, иначе комнаты станут «сиротами»
            node = self.discovery.registry.add_placeholder(
                ip, int(port), name=ip)
        pc = self.connections.get_or_dial(hub_key, ip, int(port))
        if not (pc and pc.alive):
            return False
        try:
            pc.send_packet("hub_join", room=room)
        except OSError:
            return False
        entry = self._my_room(hub_key, room)
        if not entry:
            self.my_rooms.append({
                "hub_key": hub_key,
                "hub_name": node.name,
                "room": room, "members": [],
                "password": password,
            })
        elif password is not None:
            entry["password"] = password
        self.save_settings()
        if self.on_rooms_changed:
            self.on_rooms_changed()
        return True

    def leave_room(self, hub_key: str, room: str):
        with self.connections._lock:
            pc = self.connections.conns.get(hub_key)
        if pc and pc.alive:
            try:
                pc.send_packet("hub_leave", room=room)
            except OSError:
                pass
        self.my_rooms = [r for r in self.my_rooms
                         if not (r["hub_key"] == hub_key and r["room"] == room)]
        self.save_settings()
        if self.on_rooms_changed:
            self.on_rooms_changed()

    def send_room(self, hub_key: str, room: str, text: str,
                  img: str = None) -> ChatMessage:
        key = self.room_chat_key(hub_key, room)
        msg = self.add_outgoing(key, text, img)
        self._seen_or_add(msg.id)  # своё эхо не показывать повторно
        img_raw = None
        if msg.img:
            try:
                with open(self.img_path(msg.img), "rb") as f:
                    img_raw = f.read()
            except OSError:
                msg.status = "failed"
                if self.history:
                    self.history.update_status(key, msg.id, msg.status)
                return msg
        entry = self._my_room(hub_key, room)
        password = entry.get("password") if entry else None
        fields = {"room": room, "id": msg.id, "from": self.name,
                  "timestamp": msg.timestamp}
        if password:
            rk = crypto.room_key(password, room)
            fields["text"] = crypto.encrypt(rk, text.encode("utf-8"))
            fields["img"] = (crypto.encrypt(rk, img_raw)
                             if img_raw is not None else None)
            fields["enc"] = True
        else:
            import base64
            fields["text"] = text
            fields["img"] = (base64.b64encode(img_raw).decode("ascii")
                             if img_raw is not None else None)
        if hub_key == self._own_hub_key():
            # мы хаб этой комнаты: релеим участникам сами
            ok = self._relay_room_msg(room, None, fields)
        else:
            with self.connections._lock:
                pc = self.connections.conns.get(hub_key)
            ok = False
            if pc and pc.alive:
                try:
                    pc.send_packet("hub_msg", **fields)
                    ok = True
                except OSError:
                    pass
        msg.status = "delivered" if ok else "failed"
        if self.history:
            self.history.update_status(key, msg.id, msg.status)
        return msg

    def _my_room(self, hub_key: str, room: str) -> dict | None:
        for r in self.my_rooms:
            if r["hub_key"] == hub_key and r["room"] == room:
                return r
        return None

    def _ensure_hub_room(self, room: str, password: str = None) -> dict:
        """Хаб сам участник своих комнат: запись в my_rooms + узел своего ПК."""
        own = self._own_hub_key()
        ip, _, port = own.rpartition(":")
        if self.node_by_key(own) is None:
            self.discovery.registry.add_placeholder(
                ip, int(port), name=f"{self.name} (этот ПК)")
        entry = self._my_room(own, room)
        if not entry:
            entry = {"hub_key": own, "hub_name": self.name,
                     "room": room, "members": [], "password": password}
            self.my_rooms.append(entry)
        elif password is not None:
            entry["password"] = password
        return entry

    def _room_member_names(self, room: str) -> list[str]:
        names = [self.name]  # хаб — тоже участник
        with self.connections._lock:
            conns = dict(self.connections.conns)
        for k in sorted(self.rooms.get(room, set())):
            pc = conns.get(k)
            if pc and getattr(pc, "peer_name", None):
                names.append(pc.peer_name)
                continue
            node = self.node_by_key(k)
            names.append(node.name if node else k)
        return names

    def _broadcast_members(self, room: str):
        members = self._room_member_names(room)
        with self.connections._lock:
            targets = [self.connections.conns.get(k)
                       for k in self.rooms.get(room, set())]
        for t in targets:
            if t and t.alive:
                try:
                    t.send_packet("hub_members", room=room, members=members)
                except OSError:
                    pass
        entry = self._ensure_hub_room(room)
        entry["members"] = members
        if self.on_rooms_changed:
            self.on_rooms_changed()

    def _room_remove(self, room: str, key: str):
        members = self.rooms.get(room)
        if not members or key not in members:
            return
        members.discard(key)
        if members:
            self._broadcast_members(room)
        else:
            del self.rooms[room]
            entry = self._my_room(self._own_hub_key(), room)
            if entry:
                entry["members"] = [self.name]
            if self.on_rooms_changed:
                self.on_rooms_changed()

    def _relay_room_msg(self, room: str, sender_key: str | None,
                        fields: dict) -> bool:
        """Релей hub_msg участникам комнаты, кроме отправителя."""
        ok = False
        with self.connections._lock:
            targets = [self.connections.conns.get(k)
                       for k in self.rooms.get(room, set())
                       if k != sender_key]
        for t in targets:
            if t and t.alive:
                try:
                    t.send_packet("hub_msg", **fields)
                    ok = True
                except OSError:
                    pass
        return ok

    def _deliver_room_msg(self, hub_key: str, pkt: dict):
        """Входящее сообщение комнаты -> локальный чат room:<hub_key>/<room>."""
        msg_id = str(pkt.get("id") or protocol.new_id())
        if self._seen_or_add(msg_id):
            return
        room = str(pkt.get("room") or "")
        text = str(pkt.get("text") or "")
        img = None
        if crypto.is_encrypted(pkt):
            entry = self._my_room(hub_key, room)
            password = entry.get("password") if entry else None
            plain = (crypto.decrypt(crypto.room_key(password, room), text)
                     if password else None)
            if plain is None:
                text = "[не удалось расшифровать — неверный пароль]"
            else:
                text = plain.decode("utf-8", errors="replace")
                if pkt.get("img"):
                    img_raw = crypto.decrypt(
                        crypto.room_key(password, room), pkt["img"])
                    if img_raw is not None:
                        img = self.save_incoming_img_bytes(msg_id, img_raw)
        elif pkt.get("img"):
            img = self.save_incoming_img(msg_id, pkt["img"])
        msg = ChatMessage(
            id=msg_id, direction="in",
            author=str(pkt.get("from") or "?"),
            text=text,
            timestamp=int(pkt.get("timestamp") or protocol.now()),
            img=img,
        )
        key = self.room_chat_key(hub_key, room)
        with self._lock:
            self.chats.setdefault(key, []).append(msg)
            self.unread[key] = self.unread.get(key, 0) + 1
        if self.history:
            self.history.add(key, msg)
        if self.push:
            self.push.broadcast(msg.author, msg.text)
        if self.on_message_event:
            self.on_message_event(key, msg)

    def _on_hub(self, pkt: dict, pc):
        ptype = pkt["type"]
        room = str(pkt.get("room") or "")
        if ptype == "hub_members":
            # клиентская сторона: хаб прислал состав комнаты
            entry = self._my_room(pc.key, room)
            if entry:
                entry["members"] = list(pkt.get("members") or [])
                if self.on_rooms_changed:
                    self.on_rooms_changed()
            return
        if ptype == "hub_msg":
            # сначала решаем, кто мы для этого пакета:
            # клиент хаба (доставить себе) или хаб (релей участникам)
            client_entry = self._my_room(pc.key, room)
            if client_entry is not None and pc.key != self._own_hub_key():
                # мы клиент: входящее сообщение от хаба этой комнаты
                self._deliver_room_msg(pc.key, pkt)
            elif self.hub_enabled and pc.key in self.rooms.get(room, set()):
                # мы хаб: релей остальным + локальная копия у себя
                # enc/text/img релеим как есть, не расшифровывая
                fields = {"room": room, "id": pkt.get("id"),
                          "from": pkt.get("from"), "text": pkt.get("text"),
                          "timestamp": pkt.get("timestamp"),
                          "img": pkt.get("img")}
                if crypto.is_encrypted(pkt):
                    fields["enc"] = True
                self._relay_room_msg(room, pc.key, fields)
                self._ensure_hub_room(room)
                self._deliver_room_msg(self._own_hub_key(), pkt)
            elif self.hub_enabled:
                # хаб: отправителя нет в комнате (вылетел при обрыве) —
                # сообщим, клиент сам перезайдёт
                try:
                    pc.send_packet("hub_error", code="not_in_room", room=room)
                except OSError:
                    pass
            return
        if ptype == "hub_error":
            # хаб сказал, что нас нет в комнате — перезаходим
            if pkt.get("code") == "not_in_room":
                entry = self._my_room(pc.key, room)
                if entry:
                    try:
                        pc.send_packet("hub_join", room=room)
                    except OSError:
                        pass
            return
        if not self.hub_enabled:
            return
        if ptype == "hub_join":
            if not room:
                return
            self.rooms.setdefault(room, set()).add(pc.key)
            self._ensure_hub_room(room)
            self._broadcast_members(room)
        elif ptype == "hub_leave":
            self._room_remove(room, pc.key)

    def _on_peer_disconnected(self, key: str):
        """Обрыв канала: участник пропадает из всех комнат (мы как хаб),
        а как клиент — немедленно переподключаемся (не ждём watchdog)."""
        for room in list(self.rooms):
            self._room_remove(room, key)
        if self._wd_stop.is_set():
            return  # сами останавливаемся — не переподключаемся
        self._dial_attempts.pop(key, None)  # сброс троттла — дозвон сразу
        ip, _, port = key.rpartition(":")

        def _redial():
            time.sleep(1.0)
            if not self._wd_stop.is_set():
                self.connections.get_or_dial(key, ip, int(port))
        threading.Thread(target=_redial, daemon=True).start()

    def _rejoin_loop(self, hub_key: str, room: str, password: str = None):
        """Пере-join сохранённой комнаты: ретраи каждые 15 сек до успеха."""
        if self._is_own_key(hub_key):
            return  # к самому себе дозваниваться не надо
        while not self._wd_stop.is_set():
            try:
                if self.join_room(hub_key, room, password):
                    return
            except Exception:
                pass
            self._wd_stop.wait(15)

    # --- непрочитанные ---

    def mark_read(self, key: str):
        with self._lock:
            self.unread[key] = 0

    def unread_count(self, key: str) -> int:
        return self.unread.get(key, 0)

    # --- пер-чатовый мьют уведомлений ---

    def set_chat_muted(self, key: str, muted: bool):
        if muted:
            self.muted_chats.add(key)
        else:
            self.muted_chats.discard(key)
        self.save_settings()

    def is_chat_muted(self, key: str) -> bool:
        return key in self.muted_chats

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

    # --- TLS на канал и сетевой пароль (PSK для P2P) ---

    def _apply_tls(self):
        """Проводит tls_enabled в ConnectionManager: сертификат для входящих
        (генерируется рядом с настройками при первом включении) и флаг
        TLS-first для исходящих (с откатом на plain)."""
        if self.tls_enabled:
            try:
                crt, key = crypto.ensure_self_signed_cert(base_dir())
            except Exception:
                crt = key = None  # нет crypto/прав — приём остаётся plain
            self.connections.tls_cert = crt
            self.connections.tls_key = key
        else:
            self.connections.tls_cert = None
            self.connections.tls_key = None
        self.connections.tls_outbound = self.tls_enabled

    def set_tls_enabled(self, on: bool):
        self.tls_enabled = bool(on)
        self._apply_tls()
        self.save_settings()

    def set_network_psk(self, psk: str):
        self.network_psk = str(psk or "")
        self._p2p_key_cache = None
        self.save_settings()

    def _p2p_key(self) -> bytes | None:
        """Ключ P2P-шифрования из сетевого пароля (кэш — PBKDF2 небыстрый)."""
        if not self.network_psk:
            return None
        if not self._p2p_key_cache or self._p2p_key_cache[0] != self.network_psk:
            self._p2p_key_cache = (self.network_psk,
                                   crypto.room_key(self.network_psk, "lanmsg-p2p"))
        return self._p2p_key_cache[1]

    def _on_tls_fingerprint(self, key: str, fp: str):
        """Новый/сменившийся отпечаток пира: запоминаем (known hosts)
        и показываем уведомление."""
        if self.known_fingerprints.get(key) == fp:
            return
        self.known_fingerprints[key] = fp
        node = self.node_by_key(key)
        who = node.name if node else key
        self.add_notification("Отпечаток TLS", f"{who}: {fp[:16]}...")
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
            "muted_chats": sorted(self.muted_chats),
            "manual_ip_enabled": self.manual_ip_enabled,
            "room_join_enabled": self.room_join_enabled,
            "hub_enabled": self.hub_enabled,
            "tls_enabled": self.tls_enabled,
            "network_psk": self.network_psk,
            "known_fingerprints": self.known_fingerprints,
            "rooms": [[r["hub_key"], r["room"], r.get("password")]
                      for r in self.my_rooms
                      if r["hub_key"] != self._own_hub_key()],
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
        self.scan_enabled = bool(data.get("scan_enabled", False))
        self.shadow_rdp = bool(data.get("shadow_rdp", False))
        self.sort_mode = str(data.get("sort_mode", "status"))
        self.muted_chats = {str(k) for k in (data.get("muted_chats") or [])}
        self.manual_ip_enabled = bool(data.get("manual_ip_enabled", False))
        self.room_join_enabled = bool(data.get("room_join_enabled", False))
        self.hub_enabled = bool(data.get("hub_enabled", False))
        self.tls_enabled = bool(data.get("tls_enabled", True))
        self.network_psk = str(data.get("network_psk", ""))
        self.known_fingerprints = {str(k): str(v) for k, v in
                                   (data.get("known_fingerprints") or {}).items()}
        self._saved_rooms = []
        for item in data.get("rooms", []):
            # формат: [hub_key, room] или [hub_key, room, password]
            hub_key, room = str(item[0]), str(item[1])
            password = (str(item[2]) if len(item) > 2 and item[2] else None)
            self._saved_rooms.append([hub_key, room, password])
        saved_url = str(data.get("update_url") or "")
        # миграция со старых дефолтов (страница релизов) на прямую ссылку
        if saved_url.startswith("https://github.com/kiruha3/LANMessanger/releases"):
            self.update_url = DEFAULT_UPDATE_URL
        else:
            self.update_url = saved_url or DEFAULT_UPDATE_URL
        self.update_text = str(data.get("update_text") or DEFAULT_UPDATE_TEXT)
        self.notify_update = bool(data.get("notify_update", False))
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
