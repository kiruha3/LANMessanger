"""Локальная история переписки — один файл history.db (SQLite) рядом с приложением."""

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
            "  text TEXT, timestamp INTEGER, status TEXT)")
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_key ON messages(key, timestamp)")
        self._db.commit()

    def add(self, key: str, msg):
        with self._lock:
            self._db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                (key, msg.id, msg.direction, msg.author, msg.text,
                 msg.timestamp, msg.status))
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
                "SELECT id, direction, author, text, timestamp, status"
                " FROM messages WHERE key=? ORDER BY timestamp DESC LIMIT ?",
                (key, limit)).fetchall()
        rows.reverse()
        return rows

    def close(self):
        with self._lock:
            self._db.close()
