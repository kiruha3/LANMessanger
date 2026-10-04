"""Локальная история переписки — один файл history.db (SQLite) рядом с приложением."""

import json
import os
import sqlite3
import sys
import threading


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


class History:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(base_dir(), "history.db")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "  key TEXT, id TEXT, direction TEXT, author TEXT,"
            "  text TEXT, timestamp INTEGER, status TEXT, img TEXT)")
        cols = [r[1] for r in self._db.execute("PRAGMA table_info(messages)")]
        if "img" not in cols:
            self._db.execute("ALTER TABLE messages ADD COLUMN img TEXT")
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_key ON messages(key, timestamp)")
        self._db.commit()
        self.init_events()  # таблица events нужна уже при открытии UI

    def add(self, key: str, msg):
        with self._lock:
            self._db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                (key, msg.id, msg.direction, msg.author, msg.text,
                 msg.timestamp, msg.status, msg.img))
            self._db.commit()

    def update_status(self, key: str, msg_id: str, status: str):
        with self._lock:
            self._db.execute(
                "UPDATE messages SET status=? WHERE key=? AND id=?",
                (status, key, msg_id))
            self._db.commit()

    def load(self, key: str, limit: int = 500) -> list[tuple]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, direction, author, text, timestamp, status, img"
                " FROM messages WHERE key=? ORDER BY timestamp DESC LIMIT ?",
                (key, limit)).fetchall()
        rows.reverse()
        return rows

    # --- события календаря ---

    def init_events(self):
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "  id TEXT PRIMARY KEY, title TEXT, ts INTEGER,"
                "  remind_min INTEGER, creator TEXT, participants TEXT,"
                "  updated_at INTEGER, deleted INTEGER DEFAULT 0,"
                "  reminded INTEGER DEFAULT 0)")
            self._db.commit()

    def upsert_event(self, ev: dict):
        """Конфликт: побеждает запись с большим updated_at (FR-40)."""
        with self._lock:
            cur = self._db.execute(
                "SELECT updated_at FROM events WHERE id=?", (ev["id"],)).fetchone()
            if cur and cur[0] >= ev["updated_at"]:
                return False
            self._db.execute(
                "INSERT OR REPLACE INTO events"
                " (id, title, ts, remind_min, creator, participants,"
                "  updated_at, deleted, reminded) VALUES (?,?,?,?,?,?,?,?,"
                " COALESCE((SELECT reminded FROM events WHERE id=?), 0))",
                (ev["id"], ev["title"], ev["ts"], ev["remind_min"],
                 ev["creator"], json.dumps(ev.get("participants", []),
                                           ensure_ascii=False),
                 ev["updated_at"], int(ev.get("deleted", 0)), ev["id"]))
            self._db.commit()
            return True

    def all_events(self, include_deleted: bool = False) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, ts, remind_min, creator, participants,"
                " updated_at, deleted, reminded FROM events").fetchall()
        out = []
        for r in rows:
            out.append({"id": r[0], "title": r[1], "ts": r[2],
                        "remind_min": r[3], "creator": r[4],
                        "participants": json.loads(r[5] or "[]"),
                        "updated_at": r[6], "deleted": bool(r[7]),
                        "reminded": bool(r[8])})
        return out if include_deleted else [e for e in out if not e["deleted"]]

    def due_events(self, now: int) -> list[dict]:
        return [e for e in self.all_events()
                if not e["reminded"]
                and e["ts"] - e["remind_min"] * 60 <= now < e["ts"] + 3600]

    def mark_reminded(self, event_id: str):
        with self._lock:
            self._db.execute("UPDATE events SET reminded=1 WHERE id=?",
                             (event_id,))
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()
