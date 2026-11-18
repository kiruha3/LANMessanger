"""Asynchronous OpenAI-compatible model discovery; never persist credentials."""
import json

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class APIModelCatalog(QObject):
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    MAX_BYTES = 2 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._reply = None
        self.busy = False

    def cancel(self):
        reply, self._reply = self._reply, None
        if reply:
            reply.abort()
            reply.deleteLater()
        if self.busy:
            self.busy = False
            self.busy_changed.emit(False)

    def fetch(self, endpoint, key, timeout_ms=15000, catalog_type="chat"):
        self.cancel()
        url = QUrl(endpoint.strip())
        if (not url.isValid() or url.scheme() not in ("http", "https")
                or not url.host() or url.userInfo() or url.hasQuery() or url.hasFragment()):
            raise ValueError("Укажите URL API вида https://server/v1 без логина, параметров и фрагмента.")
        url.setPath(url.path().rstrip("/") + ("/images/models" if catalog_type == "images" else "/models"))
        if catalog_type == "horde":
            url.setQuery("type=image")
        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"LANMessenger/0.23.0")
        if key.strip():
            request.setRawHeader(b"Authorization", ("Bearer " + key.strip()).encode("utf-8"))
        reply = self.manager.get(request)
        reply.setReadBufferSize(self.MAX_BYTES + 1)
        self._reply = reply
        self.busy = True
        self.busy_changed.emit(True)
        buffer = bytearray()
        timer = QTimer(reply)
        timer.setSingleShot(True)

        def fail(message):
            if self._reply is reply:
                self.cancel()
                self.failed.emit(message + " Модель можно ввести вручную.")

        def read():
            if self._reply is not reply:
                return
            buffer.extend(bytes(reply.readAll()))
            if len(buffer) > self.MAX_BYTES:
                fail("Список моделей слишком большой.")

        def finish():
            if self._reply is not reply:
                return
            timer.stop()
            read()
            if self._reply is not reply:
                return
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status != 200:
                fail(f"Не удалось загрузить модели (HTTP {status or '—'}). Проверьте URL и ключ.")
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                fail("Ошибка соединения с API.")
                return
            try:
                payload = json.loads(buffer)
                entries = payload if catalog_type == "horde" else payload["data"]
                if not isinstance(entries, list):
                    raise ValueError()
                models = {}
                for item in entries:
                    if catalog_type == "horde" and isinstance(item, dict):
                        if not isinstance(item.get("count"), (int, float)) or item["count"] <= 0:
                            continue
                        item = {"id": item.get("name"), "owned_by": "AI Horde", "available": True}
                    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                        owner = item.get("owned_by", "")
                        model = {"id": item["id"], "owner": owner if isinstance(owner, str) else ""}
                        if isinstance(item.get("available"), bool):
                            model["available"] = item["available"]
                            reason = item.get("unavailableReason", "")
                            model["unavailable_reason"] = reason if isinstance(reason, str) else ""
                        models.setdefault(item["id"], model)
                if not models:
                    raise ValueError()
            except (ValueError, KeyError, TypeError, RecursionError):
                fail("API вернул пустой или неподдерживаемый список моделей.")
                return
            self._reply = None
            self.busy = False
            reply.deleteLater()
            self.busy_changed.emit(False)
            self.loaded.emit([models[key] for key in sorted(models)])

        reply.readyRead.connect(read)
        reply.finished.connect(finish)
        timer.timeout.connect(lambda: fail("Истекло время ожидания списка моделей."))
        timer.start(timeout_ms)
