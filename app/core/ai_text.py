import json

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


DEFAULT_CONTEXT_ANSWERS = 10


def context_messages(messages, answers):
    selected = []
    for message in reversed(messages):
        if (message.get("role") not in ("user", "assistant") or not message.get("text", "").strip()
                or message.get("status") == "running" or message.get("image_message") or message.get("images")):
            continue
        selected.append(message)
        if answers and message["role"] == "assistant":
            answers -= 1
            if not answers:
                break
    return list(reversed(selected))


def chat_endpoint(endpoint):
    url = QUrl(endpoint.strip())
    if (not url.isValid() or url.scheme() not in ("http", "https") or not url.host()
            or url.userInfo() or url.hasQuery() or url.hasFragment()):
        raise ValueError("Укажите URL совместимого API под шестерёнкой, например https://server/v1.")
    url.setPath(url.path().rstrip("/") + "/chat/completions")
    return url


class TextCompletion(QObject):
    delta = pyqtSignal(str)
    completed = pyqtSignal()
    failed = pyqtSignal(str)
    MAX_BUFFER = 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._reply = None

    @property
    def busy(self):
        return self._reply is not None

    def start(self, url, model, key, messages):
        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(120_000)
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"text/event-stream, application/json")
        if key:
            request.setRawHeader(b"Authorization", ("Bearer " + key).encode())
        payload = {"model": model, "stream": True, "messages": messages}
        self._line = b""
        self._event = bytearray()
        self._body = bytearray()
        self._reason = None
        self._has_text = False
        reply = self.manager.post(request, json.dumps(payload, ensure_ascii=False).encode())
        self._reply = reply
        reply.setReadBufferSize(self.MAX_BUFFER)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))

    def cancel(self):
        reply, self._reply = self._reply, None
        if reply is not None:
            if not reply.isFinished():
                reply.abort()
            reply.deleteLater()

    def _fail(self, text):
        self.cancel()
        self.failed.emit(text)

    def _read(self, reply):
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        data = bytes(reply.readAll())
        content_type = bytes(reply.rawHeader(b"Content-Type")).lower()
        if status != 200 or b"application/json" in content_type:
            if len(self._body) + len(data) > self.MAX_BUFFER:
                self._fail("Ответ API слишком большой.")
                return
            self._body.extend(data)
            return
        *lines, self._line = (self._line + data).split(b"\n")
        if len(self._line) > self.MAX_BUFFER:
            self._fail("Событие потока API слишком большое.")
            return
        for line in lines:
            if reply is not self._reply:
                return
            self._read_line(line.rstrip(b"\r"))

    def _read_line(self, line):
        if not line:
            if self._event:
                event = bytes(self._event).strip()
                self._event.clear()
                if event == b"[DONE]":
                    self._complete()
                else:
                    self._decode(event)
        elif line == b"data" or line.startswith(b"data:"):
            self._event.extend(line[5:].removeprefix(b" ") + b"\n")
            if len(self._event) > self.MAX_BUFFER:
                self._fail("Событие потока API слишком большое.")

    @staticmethod
    def _error_text(error):
        message = error.get("message", "") if isinstance(error, dict) else error
        code = error.get("code", "") if isinstance(error, dict) else ""
        return " ".join(str(value)[:400] for value in (code, message) if value)

    def _decode(self, data):
        try:
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError()
            if payload.get("error"):
                self._fail("Ошибка API. " + self._error_text(payload["error"]))
                return
            choices = payload.get("choices") or []
            if not choices:
                return
            choice = choices[0]
            text = (choice.get("delta") or choice.get("message") or {}).get("content")
            self._reason = choice.get("finish_reason") or self._reason
        except (ValueError, KeyError, IndexError, AttributeError, TypeError, RecursionError):
            self._fail("API вернул некорректный ответ.")
            return
        if isinstance(text, str) and text:
            self._has_text |= bool(text.strip())
            self.delta.emit(text)

    def _complete(self):
        if not self._has_text:
            self._fail("Модель не вернула текстовый ответ.")
            return
        self.cancel()
        self.completed.emit()

    def _finished(self, reply):
        if reply is not self._reply:
            return
        self._read(reply)
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status != 200:
            if status is None:
                self._fail("Ошибка соединения с API. " + reply.errorString())
                return
            try:
                detail = self._error_text(json.loads(self._body).get("error", ""))
            except (ValueError, AttributeError, RecursionError):
                detail = ""
            self._fail(f"Ошибка API (HTTP {status}). {detail}".rstrip())
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail("Поток ответа прерван. " + reply.errorString())
            return
        if self._body:
            self._decode(self._body)
        else:
            self._read_line(self._line.rstrip(b"\r"))
            if reply is self._reply:
                self._read_line(b"")
        if reply is not self._reply:
            return
        if self._reason or self._body:
            self._complete()
        else:
            self._fail("Поток API оборвался до завершения ответа.")
