"""Мини push-сервер для телефонов: SSE-эндпоинт, совместимый с ntfy.

На телефоне ставится приложение ntfy, в нём добавляется сервер
http://<ip-этого-ПК>:<port> и подписка на топик. При новом сообщении
в мессенджере сюда уходит событие — телефон показывает уведомление.
Всё локально, без интернета и аккаунтов.
"""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class PushServer:
    def __init__(self, port: int = 8087, topic: str = "lanalerts"):
        self.port = port
        self.topic = topic.strip("/") or "lanalerts"
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._server:
            return True
        push = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.strip("/").split("?")[0]
                if path == f"{push.topic}/sse" or path == f"{push.topic}/json":
                    self._subscribe()
                elif path == "health":
                    self._plain(200, "ok")
                else:
                    self._plain(404, "not found")

            def _plain(self, code, text):
                body = text.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _subscribe(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                q: queue.Queue = queue.Queue(maxsize=100)
                with push._lock:
                    push._subscribers.append(q)
                try:
                    while True:
                        try:
                            event = q.get(timeout=25)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(f"data: {event}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with push._lock:
                        if q in push._subscribers:
                            push._subscribers.remove(q)

        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError:
            self._server = None
            return False
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def broadcast(self, title: str, message: str):
        if not self._server:
            return
        event = json.dumps({
            "id": hex(int(time.time() * 1000))[2:],
            "time": int(time.time()),
            "event": "message",
            "topic": self.topic,
            "title": title,
            "message": message[:250],
        }, ensure_ascii=False)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
