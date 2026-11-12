"""Local Pi process, isolated AI history, runtime discovery and protected API keys."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal


PI_VERSION = "0.73.1"
PROVIDERS = {
    "openai": ("OpenAI — API", "OPENAI_API_KEY"),
    "openai-codex": ("OpenAI — аккаунт ChatGPT", ""),
    "anthropic": ("Anthropic", "ANTHROPIC_API_KEY"),
    "google": ("Google — API", "GEMINI_API_KEY"),
    "github-copilot": ("GitHub Copilot — аккаунт", ""),
    "lanmessenger-api": ("Совместимый API", "LANMESSENGER_AI_KEY"),
}


def application_folder() -> str:
    if not getattr(sys, "frozen", False):
        return str(Path.cwd().resolve())
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return str(parent.parent)
    return str(executable.parent)


class JsonLines:
    def __init__(self):
        self.buffer = b""

    def feed(self, data: bytes) -> list[dict]:
        self.buffer += data
        if len(self.buffer) > 32 * 1024 * 1024:
            raise ValueError("Слишком большой ответ Pi")
        records = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Некорректный JSONL от Pi") from exc
            if not isinstance(item, dict):
                raise ValueError("Pi вернул не объект JSON")
            records.append(item)
        return records


class AIStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"
        self.config = {"provider": "openai-codex", "model": "", "endpoint": "", "runtime": ""}
        self.chats = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.config.update({k: str(v) for k, v in data["config"].items() if k in self.config})
                self.chats = data["chats"]
                if not isinstance(self.chats, list):
                    raise ValueError("Invalid chat list")
                for chat in self.chats:
                    if not isinstance(chat.get("messages"), list) or not chat.get("id"):
                        raise ValueError("Invalid chat")
                    if chat.get("running"):
                        chat["interrupted"] = True
                        chat["running"] = False
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError(f"Не удалось прочитать историю ИИ: {self.path}. Файл сохранён без изменений.") from exc

    def new_chat(self, folder: str) -> dict:
        chat_id = str(uuid.uuid4())
        sessions = self.root / "sessions"
        sessions.mkdir(exist_ok=True)
        chat = {"id": chat_id, "folder": str(Path(folder).resolve()), "title": "Новый чат",
                "session": str(sessions / f"{chat_id}.jsonl"), "messages": [],
                "running": False, "interrupted": False}
        self.chats.insert(0, chat)
        self.save()
        return chat

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"config": self.config, "chats": self.chats}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


class KeyVault:
    """DPAPI on Windows; session-only credentials on other platforms."""
    def __init__(self, root: Path):
        self.path = root / "api-key.dpapi"

    @staticmethod
    def _crypt(raw: bytes, decrypt=False) -> bytes:
        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]
        buffer = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        dll = ctypes.windll.crypt32
        if decrypt:
            ok = dll.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
        else:
            ok = dll.CryptProtectData(ctypes.byref(source), "LANMessenger AI", None, None, None, 1, ctypes.byref(output))
        if not ok:
            raise OSError("Не удалось защитить API-ключ средствами Windows")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            ctypes.windll.kernel32.LocalFree(output.data)

    def save(self, provider: str, key: str):
        if sys.platform != "win32":
            raise OSError("На этой системе ключ доступен только до выхода")
        self.path.write_bytes(self._crypt(json.dumps({"provider": provider, "key": key}).encode()))

    def load(self) -> dict:
        if sys.platform != "win32" or not self.path.exists():
            return {}
        try:
            return json.loads(self._crypt(self.path.read_bytes(), True))
        except (OSError, ValueError, UnicodeError):
            return {}

    def clear(self):
        self.path.unlink(missing_ok=True)


def runtime_command(configured: str = "") -> tuple[str, list[str], Path]:
    """Resolve node and the actual JS entry, never execute a .cmd through a shell."""
    node = shutil.which("node")
    if not node:
        raise ValueError("Не найден Node.js. Установите Node.js 22 LTS и перезапустите приложение.")
    candidates = []
    if configured.strip():
        selected = Path(configured.strip()).expanduser()
        candidates.extend([selected / "dist" / "cli.js", selected / "node_modules/@mariozechner/pi-coding-agent/dist/cli.js", selected])
    home = Path(application_folder())
    for base in (home, Path(__file__).resolve().parents[2], Path(os.environ.get("APPDATA", str(Path.home()))) / "npm"):
        candidates.extend([base / "ai-runtime/node_modules/@mariozechner/pi-coding-agent/dist/cli.js",
                           base / "dist/ai-runtime/node_modules/@mariozechner/pi-coding-agent/dist/cli.js",
                           base / "node_modules/@mariozechner/pi-coding-agent/dist/cli.js"])
    pi = shutil.which("pi")
    if pi:
        candidates.append(Path(pi).parent / "node_modules/@mariozechner/pi-coding-agent/dist/cli.js")
    for candidate in candidates:
        if candidate.is_file() and candidate.name == "cli.js":
            return node, [str(candidate.resolve())], candidate.resolve().with_name("index.js")
    raise ValueError(f"Не найден Pi. Установите: npm install -g @mariozechner/pi-coding-agent@{PI_VERSION}; либо укажите папку пакета в подключении.")


def prepare_environment(root: Path, config: dict, key: str) -> dict:
    agent = root / "pi"
    agent.mkdir(exist_ok=True)
    if sys.platform == "win32":
        shell = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if not shell.exists() and not shutil.which("bash"):
            raise ValueError("Pi нужен Git Bash. Установите Git for Windows и перезапустите приложение.")
    auth_path = agent / "auth.json"
    if key and auth_path.exists():
        try:
            stored = json.loads(auth_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError("Не удалось прочитать подключённый аккаунт Pi") from exc
        if config["provider"] in stored:
            raise ValueError("Для использования API-ключа сначала нажмите «Выйти из аккаунта» у этого провайдера: сохранённый аккаунт Pi имеет приоритет.")
    env = {"PI_CODING_AGENT_DIR": str(agent)}
    variable = PROVIDERS.get(config["provider"], ("", ""))[1]
    if key and variable:
        env[variable] = key
    if config["provider"] == "lanmessenger-api":
        from urllib.parse import urlsplit
        endpoint = config.get("endpoint", "").strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Укажите HTTP(S)-адрес совместимого API, например https://server/v1")
        model = config.get("model", "").strip()
        if not model:
            raise ValueError("Укажите идентификатор модели")
        models = {"providers": {"lanmessenger-api": {"baseUrl": endpoint, "api": "openai-completions",
                  "apiKey": "LANMESSENGER_AI_KEY", "authHeader": True,
                  "models": [{"id": model, "contextWindow": 128000, "maxTokens": 16384}]}}}
        (agent / "models.json").write_text(json.dumps(models), encoding="utf-8")
    return env


class PiProcess(QObject):
    received = pyqtSignal(dict)
    error = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    ready_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._finished)
        self.ready = self.busy = False
        self._closing = False
        self._parser = JsonLines()
        self._stderr = ""
        self._secrets = []
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)
        self._stopping = False

    def start(self, program: str, args: list[str], cwd: str, env: dict):
        self.shutdown()
        self._closing = False
        self._parser = JsonLines()
        self._stderr = ""
        self._stopping = False
        self._secrets = [v for k, v in env.items() if "KEY" in k and v]
        environment = QProcessEnvironment.systemEnvironment()
        for name, value in env.items():
            environment.insert(name, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(cwd)
        self.process.start(program, args)
        self._deadline.start(30000)

    def _started(self):
        # Pi can emit agent_end before automatic retry/compaction continues.
        self.command({"type": "set_auto_retry", "enabled": False})

    def command(self, value: dict):
        if self.process.state() != QProcess.ProcessState.Running:
            raise RuntimeError("Pi не запущен")
        if value.get("type") == "get_messages":
            self._deadline.start(15000)
        self.process.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))

    def send_prompt(self, text: str):
        if not self.ready or self.busy:
            raise RuntimeError("Дождитесь готовности или остановки агента")
        self._set_busy(True)
        self._stopping = False
        self.command({"id": str(uuid.uuid4()), "type": "prompt", "message": text})
        self._deadline.start(45000)

    def abort(self):
        if self.busy and not self._stopping:
            self._stopping = True
            self.command({"type": "abort"})
            self._deadline.start(5000)

    def _set_busy(self, value):
        if self.busy != value:
            self.busy = value
            self.busy_changed.emit(value)

    def _set_ready(self, value):
        if self.ready != value:
            self.ready = value
            self.ready_changed.emit(value)

    def redact(self, text):
        for key in self._secrets:
            text = text.replace(key, "<ключ скрыт>")
        return text

    def _read(self):
        try:
            records = self._parser.feed(bytes(self.process.readAllStandardOutput()))
        except ValueError as exc:
            self.error.emit(str(exc))
            self.shutdown()
            return
        for record in records:
            kind = record.get("type")
            if kind == "response":
                command = record.get("command")
                if not record.get("success", False):
                    self._deadline.stop()
                    self._set_busy(False)
                    self.error.emit(self.redact(str(record.get("error", "Ошибка Pi"))))
                elif command == "set_auto_retry":
                    self.command({"type": "set_auto_compaction", "enabled": False})
                elif command == "set_auto_compaction":
                    self.command({"type": "get_state"})
                elif command == "get_state":
                    self._deadline.stop()
                    self._set_ready(True)
                elif command == "get_messages":
                    self._deadline.stop()
                elif command == "prompt" and not self._stopping:
                    self._deadline.stop()
            elif kind == "agent_end":
                self._deadline.stop()
                self._set_busy(False)
                self._stopping = False
            elif kind == "agent_start" and not self._stopping:
                self._deadline.stop()
            self.received.emit(record)

    def _read_stderr(self):
        self._stderr = (self._stderr + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))[-4000:]

    def _process_error(self, _error):
        if not self._closing:
            self._deadline.stop()
            self._set_ready(False)
            self._set_busy(False)
            self.error.emit(self.redact(self.process.errorString()))

    def _finished(self, code, _status):
        self._read_stderr()
        self._deadline.stop()
        self._set_ready(False)
        self._set_busy(False)
        if not self._closing:
            detail = self.redact(self._stderr.strip())
            self.error.emit(f"Pi завершился (код {code}). {detail}".strip())

    def _timeout(self):
        message = "Остановка Pi превысила время ожидания; процесс завершён." if self._stopping else "Pi не подтвердил команду за отведённое время."
        self.shutdown()
        self.error.emit(message)

    def shutdown(self):
        self._closing = True
        self._deadline.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            if self.busy:
                try:
                    self.command({"type": "abort"})
                except RuntimeError:
                    pass
            pid = self.process.processId()
            if sys.platform == "win32" and pid:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.process.kill()
            self.process.waitForFinished(2000)
        self._set_ready(False)
        self._set_busy(False)


def auth_helper_path() -> Path:
    return Path(__file__).with_name("ai_auth.mjs")
