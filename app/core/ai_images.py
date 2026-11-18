import base64
import binascii
import json
import math
import re
import time
from pathlib import Path
import uuid

from PyQt6.QtCore import QByteArray, QBuffer, QEventLoop, QIODevice, QObject, QSize, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QImageReader
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


IMAGE_PROVIDERS = {"ai-horde", "lanmessenger-images", "cloudflare-images"}
HORDE_URL = "https://aihorde.net/api/v2"
HORDE_MODEL = "Dreamshaper"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts"
CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
_next_horde_submission = 0.0


def image_bytes(data):
    if not data or len(data) > 24 * 1024 * 1024:
        raise ValueError("Пустое или слишком большое изображение (предел 24 МиБ).")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    suffix = bytes(reader.format()).decode("ascii", errors="ignore").lower()
    if suffix not in ("png", "jpeg", "jpg", "webp") or not size.isValid() or size.width() * size.height() > 32_000_000:
        raise ValueError("API вернул неподдерживаемое изображение. Нужен PNG, JPEG или WebP до 32 мегапикселей.")
    if reader.read().isNull():
        raise ValueError("Не удалось прочитать изображение из ответа API.")
    return "jpg" if suffix == "jpeg" else suffix


def image_content(path):
    if Path(path).stat().st_size > 24 * 1024 * 1024:
        raise ValueError("Картинка превышает 24 МиБ.")
    data = Path(path).read_bytes()
    image_bytes(data)
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if max(size.width(), size.height()) > 1024:
        reader.setScaledSize(size.scaled(QSize(1024, 1024), Qt.AspectRatioMode.KeepAspectRatio))
    picture = reader.read()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if picture.isNull() or not picture.save(buffer, "PNG"):
        raise ValueError("Не удалось подготовить картинку для модели.")
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(bytes(buffer.data())).decode("ascii")}


class ImageGenerator(QObject):
    completed = pyqtSignal(list)
    failed = pyqtSignal(str)
    activity = pyqtSignal(str)
    job_started = pyqtSignal(dict)
    job_cancelled = pyqtSignal(dict)
    queue_updated = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.busy = False
        self._reply = None
        self._operation = 0
        self._job = None
        self._key = ""
        self._submitting = False
        self._cancel_on_submit = False
        self._cancellations = {}
        self.cancelled_jobs = set()
        self._submission = None
        self._submit_timer = QTimer(self)
        self._submit_timer.setSingleShot(True)
        self._submit_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._submit_timer.timeout.connect(self._submit_horde)
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._check)

    def generate(self, provider, endpoint, model, key, prompt, output):
        if self.busy or self.cancellation_pending:
            raise ValueError("Дождитесь завершения текущей генерации.")
        if provider not in IMAGE_PROVIDERS or not model.strip() or not prompt.strip():
            raise ValueError("Выберите модель изображения и напишите описание картинки.")
        if provider != "ai-horde" and not key.strip():
            raise ValueError("Для Images API нужен API-ключ. Укажите его под шестерёнкой рядом с моделью.")
        if provider == "lanmessenger-images":
            url = QUrl(endpoint.strip())
            if not url.isValid() or url.scheme() not in ("http", "https") or not url.host() or url.userInfo() or url.hasQuery() or url.hasFragment():
                raise ValueError("Укажите URL Images API вида https://server/v1 без логина, параметров и фрагмента.")
        if provider == "cloudflare-images":
            if not re.fullmatch(r"[a-fA-F0-9]{32}", endpoint.strip()):
                raise ValueError("Укажите Account ID Cloudflare (32 символа) под шестерёнкой рядом с моделью.")
            if model != CLOUDFLARE_MODEL:
                raise ValueError("Для Cloudflare выберите FLUX.1 Schnell через «Загрузить модели».")
            if len(prompt) > 2048:
                raise ValueError("Cloudflare FLUX принимает описание до 2048 символов.")
        self._begin(output, key)
        self._endpoint = HORDE_URL if provider == "ai-horde" else endpoint.strip().rstrip("/")
        payload = {"model": model, "prompt": prompt, "n": 1, "response_format": "b64_json"}
        path = "/images/generations"
        headers = {"Authorization": "Bearer " + key.strip()}
        if provider == "ai-horde":
            payload = {"prompt": prompt, "models": [model], "params": {"width": 512, "height": 512, "steps": 20, "n": 1},
                       "nsfw": False, "censor_nsfw": True, "trusted_workers": True, "r2": True, "shared": False}
            path = "/generate/async"
            headers = {"apikey": key.strip() or "0000000000"}
        elif provider == "cloudflare-images":
            self._endpoint = CLOUDFLARE_URL + "/" + endpoint.strip()
            path = "/ai/run/" + CLOUDFLARE_MODEL
            payload = {"prompt": prompt, "steps": 4}
        self.activity.emit("Отправляет запрос на изображение…")
        self._submitting = provider == "ai-horde"
        if provider == "ai-horde":
            self._submission = (self._endpoint + path, payload, headers)
            self._submit_horde()
            return
        self._request(self._endpoint + path, "POST", payload, headers,
                      self._queued if provider == "ai-horde" else self._cloudflare_result if provider == "cloudflare-images" else self._compatible_result, timeout_ms=180000)

    def _submit_horde(self):
        global _next_horde_submission
        if not self.busy or not self._submission:
            return
        now = time.monotonic()
        delay = _next_horde_submission - now
        if delay > 0:
            self.activity.emit("Отправляет запрос с учётом ограничения частоты AI Horde…")
            self._submit_timer.start(math.ceil(delay * 1000))
            return
        _next_horde_submission = now + .55
        submission, self._submission = self._submission, None
        url, payload, headers = submission
        self._request(url, "POST", payload, headers, self._queued, timeout_ms=180000)

    def _begin(self, output, key):
        self._operation += 1
        self.busy = True
        self._job = None
        self._cancel_on_submit = False
        self._key = key.strip()
        self._started_at = time.time()
        self._output = Path(output).resolve()

    def resume(self, job, output):
        if self.busy or self.cancellation_pending:
            return
        job_id = str(uuid.UUID(job["id"]))
        if job.get("provider") != "ai-horde":
            raise ValueError("Это задание не поддерживает получение результата по ID.")
        self._begin(output, "")
        self._endpoint = HORDE_URL
        self._job = {"provider": "ai-horde", "id": job_id}
        self._check()

    @property
    def cancellation_pending(self):
        return bool(self._cancellations or (self._cancel_on_submit and self._reply is not None))

    def cancel(self, remote=True, job=None):
        job = self._job or job
        self.busy = False
        self._poll.stop()
        self._submit_timer.stop()
        self._submission = None
        if remote and not job and self._submitting and self._reply is not None:
            self._cancel_on_submit = True
            return
        self._operation += 1
        self._submitting = False
        self._cancel_on_submit = False
        reply, self._reply = self._reply, None
        if reply:
            reply.abort()
            reply.deleteLater()
        self._key = ""
        self._job = None
        if remote and job and job.get("provider") == "ai-horde":
            try:
                job_id = str(uuid.UUID(job["id"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                return
            if job_id in self.cancelled_jobs or job_id in self._cancellations.values():
                return
            request = QNetworkRequest(QUrl(HORDE_URL + "/generate/status/" + job_id))
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            request.setTransferTimeout(15000)
            reply = self.manager.deleteResource(request)
            self._cancellations[reply] = job_id

            def finish():
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status and 200 <= status < 300 and reply.error() == QNetworkReply.NetworkError.NoError:
                    self.cancelled_jobs.add(job_id)
                    self.job_cancelled.emit({"provider": "ai-horde", "id": job_id})
                self._cancellations.pop(reply, None)
                reply.deleteLater()

            reply.finished.connect(finish)

    def _request(self, url, method, payload, headers, callback, binary=False, timeout_ms=30000, redirects=0):
        target = QUrl(url)
        if not target.isValid() or target.scheme() not in ("http", "https") or not target.host() or target.userInfo() or target.hasFragment():
            self._fail("Некорректный HTTP(S)-адрес API или изображения.")
            return
        request = QNetworkRequest(target)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(timeout_ms)
        request.setRawHeader(b"User-Agent", b"LANMessenger/0.23.0")
        request.setRawHeader(b"Client-Agent", b"LANMessenger:0.23.0:https://github.com/kiruha3/LANMessanger")
        request.setRawHeader(b"Accept", b"image/png,image/jpeg,image/webp" if binary else b"application/json")
        for name, value in headers.items():
            request.setRawHeader(name.encode("ascii"), value.encode("utf-8"))
        if method == "POST":
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            reply = self.manager.post(request, json.dumps(payload).encode("utf-8"))
        else:
            reply = self.manager.get(request)
        self._reply = reply
        operation = self._operation
        maximum = (24 if binary else 40) * 1024 * 1024
        reply.setReadBufferSize(maximum + 1)
        data = bytearray()
        timer = QTimer(reply)
        timer.setSingleShot(True)

        def read():
            if operation != self._operation or self._reply is not reply:
                return
            data.extend(bytes(reply.readAll()))
            if len(data) > maximum:
                self._fail("Ответ изображения превышает допустимый размер.")

        def finish():
            if operation != self._operation or self._reply is not reply:
                return
            read()
            if self._reply is not reply:
                return
            timer.stop()
            self._reply = None
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            location = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
            if binary and status in (301, 302, 303, 307, 308) and location and redirects < 3:
                redirect = target.resolved(location)
                reply.deleteLater()
                if target.scheme() == "https" and redirect.scheme() != "https":
                    self._fail("Сервис перенаправил изображение на незащищённый адрес.")
                    return
                self._request(redirect.toString(), "GET", None, {}, callback, binary=True, redirects=redirects + 1)
                return
            error = reply.error()
            reply.deleteLater()
            try:
                if not status or not 200 <= status < 300 or error != QNetworkReply.NetworkError.NoError:
                    detail = ""
                    try:
                        problem = json.loads(data)
                        detail = problem.get("message") or problem.get("error", "")
                        if isinstance(detail, dict):
                            detail = detail.get("message") or detail.get("code") or ""
                    except (ValueError, TypeError, AttributeError):
                        pass
                    raise ValueError(f"Генерация изображения: HTTP {status or '—'}. {str(detail)[:1000]}".strip())
                callback(bytes(data) if binary else json.loads(data))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, binascii.Error) as exc:
                self._fail(str(exc))

        reply.readyRead.connect(read)
        reply.finished.connect(finish)
        timer.timeout.connect(lambda: self._fail("Истекло время ожидания изображения. Повторный запрос автоматически не отправляется."))
        timer.start(timeout_ms)

    def _fail(self, text):
        if not self.busy:
            return
        safe = text.replace(self._key, "<ключ скрыт>") if self._key else text
        self.cancel(remote=False)
        self.failed.emit(safe)

    def _queued(self, payload):
        self._submitting = False
        job_id = str(uuid.UUID(payload["id"]))
        self._job = {"provider": "ai-horde", "id": job_id}
        self.job_started.emit(dict(self._job))
        if self._cancel_on_submit:
            self.cancel()
            return
        self._check()

    def _check(self):
        if self.busy and self._job:
            self._request(HORDE_URL + "/generate/check/" + self._job["id"], "GET", None, {}, self._checked)

    def _checked(self, payload):
        if payload.get("faulted"):
            raise ValueError("AI Horde не смог выполнить генерацию. Можно отправить новый запрос вручную.")
        if payload.get("done"):
            self.activity.emit("Получает готовую картинку…")
            self._request(HORDE_URL + "/generate/status/" + self._job["id"], "GET", None, {}, self._horde_result)
            return
        self.queue_updated.emit({"state": "processing" if payload.get("processing") else "queued",
                                 "queue_position": payload.get("queue_position"),
                                 "estimated_wait_seconds": payload.get("wait_time"),
                                 "elapsed_seconds": round(time.time() - self._started_at),
                                 "is_possible": payload.get("is_possible"), "checked_at": time.time()})
        if payload.get("is_possible") is False:
            self.activity.emit("AI Horde: ожидает появления свободного исполнителя для этой модели…")
        else:
            position = payload.get("queue_position", "—")
            seconds = payload.get("wait_time", "—")
            stage = "Рисует" if payload.get("processing") else "В очереди"
            self.activity.emit(f"AI Horde: {stage.lower()} · место {position} · оценка сервиса ~{seconds} с")
        self._poll.start(5000)

    def _horde_result(self, payload):
        results = payload.get("generations", [])
        if not results:
            raise ValueError("AI Horde завершил задание без изображения.")
        result = results[0]
        if result.get("censored"):
            raise ValueError("AI Horde скрыл изображение своим фильтром. Измените описание и отправьте новый запрос.")
        self._receive_image(result["img"])

    def _compatible_result(self, payload):
        results = payload.get("data", [])
        if not results:
            raise ValueError("API вернул ответ без изображения.")
        result = results[0]
        self._receive_image(result.get("b64_json") or result.get("url") or "")

    def _cloudflare_result(self, payload):
        if payload.get("success") is False:
            raise ValueError("Cloudflare отклонил генерацию. Проверьте Account ID, токен с правом Workers AI Read и дневную квоту.")
        self._receive_image(payload.get("result", {}).get("image", ""))

    def _receive_image(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("API не вернул данные изображения.")
        if value.startswith(("https://", "http://")):
            self.activity.emit("Скачивает изображение…")
            self._request(value, "GET", None, {}, self._save_image, binary=True)
        else:
            self._save_image(base64.b64decode(value, validate=True))

    def _save_image(self, data):
        suffix = image_bytes(data)
        self._output.mkdir(parents=True, exist_ok=True)
        path = self._output / f"{uuid.uuid4()}.{suffix}"
        path.write_bytes(data)
        self.busy = False
        self._job = None
        self._key = ""
        self.completed.emit([str(path)])


def wait_for_image_cancellations(generators, timeout_ms=5000):
    if not any(generator.cancellation_pending for generator in generators):
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if not any(generator.cancellation_pending for generator in generators) else None)
    poll.start(20)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(timeout_ms)
    loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    poll.stop()
    deadline.stop()
