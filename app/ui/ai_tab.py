"""Integrated local Pi chat, connection settings and account login."""
import json
import os
import uuid
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from ..core.ai import (AIStore, JsonLines, KeyVault, PiProcess, PROVIDERS, THINKING_LEVELS,
                       application_folder, auth_helper_path, prepare_environment, runtime_command)
from ..core.history import base_dir
from ..core.ai_timeline import Timeline, restore_messages
from ..core.ai_catalog import APIModelCatalog
from .ai_transcript import AITranscript


class AIChatInput(QPlainTextEdit):
    submitted = pyqtSignal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not event.isAutoRepeat():
                self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AITab(QWidget):
    def __init__(self, parent=None, data_dir=None, work_dir=None):
        super().__init__(parent)
        self.work_dir = str(Path(work_dir or application_folder()).resolve())
        self.store = None
        self.current = None
        self._pending = None
        self.timeline = None
        self._models = []
        self._api_models = []
        self._catalog_identity = None
        self.api_catalog = APIModelCatalog(self)
        self.api_catalog.loaded.connect(self._api_models_loaded)
        self.api_catalog.failed.connect(self._catalog_failed)
        self.api_catalog.busy_changed.connect(self._catalog_busy)
        self._oauth_providers = {"anthropic", "openai-codex", "github-copilot"}
        self._auth_url = ""
        self._logging_in = False
        self._stopped = False
        self._closed = False
        self.rpc = PiProcess(self)
        self.rpc.received.connect(self._event)
        self.rpc.error.connect(self._error)
        self.rpc.busy_changed.connect(self._busy_changed)
        self.rpc.ready_changed.connect(self._ready_changed)
        self.auth = QProcess(self)
        self.auth.readyReadStandardOutput.connect(self._auth_read)
        self.auth.readyReadStandardError.connect(lambda: self.auth.readAllStandardError())
        self.auth.errorOccurred.connect(lambda _error: self._auth_failed(self.auth.errorString()))
        self.auth.finished.connect(self._auth_finished)
        self._auth_parser = JsonLines()
        self._auth_closing = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save)
        self._connection_save_timer = QTimer(self)
        self._connection_save_timer.setSingleShot(True)
        self._connection_save_timer.timeout.connect(self._persist_connection)

        self.folder_label = QLabel(f"Рабочая папка: {self.work_dir}")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status = QLabel("Подключите аккаунт или API-ключ и выберите модель.")
        self.status.setWordWrap(True)
        self.chat_list = QListWidget()
        self.new_btn = QPushButton("Новый чат")
        self.new_btn.clicked.connect(self._new_chat)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.new_btn)
        left_layout.addWidget(self.chat_list, 1)

        self.connection_btn = QPushButton("Подключение модели")
        self.connection_btn.setCheckable(True)
        self.connection_panel = QWidget()
        form = QFormLayout(self.connection_panel)
        self.provider = QComboBox()
        for key, (label, _variable) in PROVIDERS.items():
            self.provider.addItem(label, key)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.lineEdit().setPlaceholderText("Выберите или введите модель")
        self.thinking = QComboBox()
        for level, label in THINKING_LEVELS.items():
            self.thinking.addItem(label, level)
        self.thinking_status = QLabel("Уровень зависит от модели и API; больше размышлений может увеличить время и стоимость.")
        self.thinking_status.setWordWrap(True)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ; для входа по аккаунту оставьте пустым")
        self.remember = QCheckBox("Сохранить API-ключ средствами Windows")
        self.remember.setEnabled(os.name == "nt")
        self.connection_status = QLabel("Настройки подключения сохраняются автоматически.")
        self.connection_status.setWordWrap(True)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("https://server/v1 — для совместимого API")
        self.runtime = QLineEdit()
        self.runtime.setPlaceholderText("Pi определяется автоматически")
        runtime_row = QHBoxLayout()
        runtime_row.addWidget(self.runtime, 1)
        browse = QPushButton("Папка Pi…")
        browse.clicked.connect(self._browse_runtime)
        runtime_row.addWidget(browse)
        self.catalog_btn = QPushButton("Загрузить модели")
        self.catalog_btn.clicked.connect(self._catalog_request)
        self.login_btn = QPushButton("Войти в аккаунт")
        self.login_btn.clicked.connect(self._login)
        self.logout_btn = QPushButton("Выйти из аккаунта")
        self.logout_btn.clicked.connect(self._logout)
        self.cancel_login_btn = QPushButton("Отменить вход")
        self.cancel_login_btn.clicked.connect(self._cancel_login)
        self.cancel_login_btn.hide()
        buttons = QHBoxLayout()
        for button in (self.login_btn, self.logout_btn, self.cancel_login_btn):
            buttons.addWidget(button)
        self.auth_info = QLabel("")
        self.auth_info.setWordWrap(True)
        self.auth_info.setTextFormat(Qt.TextFormat.PlainText)
        self.auth_open = QPushButton("Открыть страницу входа")
        self.auth_open.clicked.connect(self._open_auth_url)
        self.auth_open.hide()
        self.auth_input = QLineEdit()
        self.auth_input.setPlaceholderText("Код, URL или ответ для входа")
        self.auth_submit = QPushButton("Передать ответ")
        self.auth_submit.clicked.connect(self._auth_answer)
        self.auth_input.returnPressed.connect(self._auth_answer)
        self.auth_input.hide()
        self.auth_submit.hide()
        form.addRow("Провайдер", self.provider)
        form.addRow("URL API", self.endpoint)
        form.addRow("API-ключ", self.key)
        form.addRow("", self.remember)
        form.addRow("", self.connection_status)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.catalog_btn)
        form.addRow("Модель", model_row)
        form.addRow("Размышления", self.thinking)
        form.addRow("", self.thinking_status)
        form.addRow("Runtime", runtime_row)
        form.addRow(buttons)
        form.addRow(self.auth_info)
        form.addRow(self.auth_open)
        form.addRow(self.auth_input, self.auth_submit)
        self.connection_panel.hide()
        self.connection_btn.toggled.connect(self.connection_panel.setVisible)

        self.output = AITranscript(self.work_dir)
        self.output.welcome.connect_requested.connect(self._open_connection)
        self.input = AIChatInput()
        self.input.setPlaceholderText("Задача для агента. Enter — отправить, Shift+Enter — новая строка")
        self.input.setMaximumHeight(95)
        self.input.submitted.connect(self._send)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        footer = QHBoxLayout()
        footer.addWidget(self.input, 1)
        footer.addWidget(self.send_btn)
        footer.addWidget(self.stop_btn)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.folder_label)
        right_layout.addWidget(self.connection_btn)
        right_layout.addWidget(self.connection_panel)
        right_layout.addWidget(self.output, 1)
        right_layout.addLayout(footer)
        right_layout.addWidget(self.status)
        privacy = QLabel("Агент работает с вашими правами. Содержимое, нужное для задачи, передаётся выбранному провайдеру.")
        privacy.setWordWrap(True)
        right_layout.addWidget(privacy)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 650])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        try:
            self.store = AIStore(data_dir or str(Path(base_dir(), "ai-data")))
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            self.send_btn.setEnabled(False)
            self.new_btn.setEnabled(False)
            self.connection_panel.setEnabled(False)
            return
        self.vault = KeyVault(self.store.root)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.store.config["provider"])))
        self.model.setEditText(self.store.config["model"])
        self.endpoint.setText(self.store.config["endpoint"])
        self.runtime.setText(self.store.config["runtime"])
        self.thinking.setCurrentIndex(max(0, self.thinking.findData(self.store.config["thinking"])))
        credential = self.vault.load()
        if credential.get("provider") == self.provider.currentData():
            self.key.setText(credential.get("key", ""))
            self.remember.setChecked(bool(credential.get("key")))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.model.currentTextChanged.connect(self._connection_changed)
        self.thinking.currentIndexChanged.connect(self._thinking_changed)
        self.endpoint.textChanged.connect(self._connection_changed)
        self.runtime.textChanged.connect(self._connection_changed)
        self.key.textChanged.connect(self._connection_changed)
        self.remember.toggled.connect(self._schedule_connection_save)
        self._last_provider = self.provider.currentData()
        self._catalog_identity = self._discovery_identity()
        if not self.store.chats:
            self.store.new_chat(self.work_dir)
        self._fill_chats()
        self.chat_list.currentRowChanged.connect(self._select_chat)
        self._select_chat(0)
        self._provider_visibility()

    def _fill_chats(self):
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for chat in self.store.chats:
            item = QListWidgetItem(chat["title"])
            item.setToolTip(chat["folder"])
            self.chat_list.addItem(item)
        self.chat_list.setCurrentRow(0)
        self.chat_list.blockSignals(False)

    def _new_chat(self):
        if self.store and not self.rpc.busy and not self._pending:
            self.store.new_chat(self.work_dir)
            self._fill_chats()
            self._select_chat(0)

    def _select_chat(self, index):
        if not self.store or index < 0:
            return
        self.rpc.shutdown()
        self.current = self.store.chats[index]
        self.timeline = Timeline(self.current["messages"], self.rpc.redact)
        if not self.current.get("running"):
            self.timeline.finish("interrupted")
        self._render()
        same_folder = os.path.normcase(self.current["folder"]) == os.path.normcase(self.work_dir)
        self.send_btn.setEnabled(same_folder and not self._logging_in)
        if not same_folder:
            self.status.setText("Этот чат относится к другой папке. Нажмите «Новый чат» для текущей папки приложения.")
        elif self.current.get("interrupted"):
            self.status.setText("Предыдущая задача прервана. Новый запрос продолжит сохранённую сессию; прежний запрос не повторяется.")
        else:
            self.status.setText("Готов к задаче. Подключение модели — над чатом.")

    def _config(self):
        return {"provider": self.provider.currentData(), "model": self.model.currentText().strip(),
                "endpoint": self.endpoint.text().strip(), "runtime": self.runtime.text().strip(),
                "thinking": self.thinking.currentData()}

    def _thinking_changed(self):
        self.thinking_status.setText("Уровень зависит от модели и API; больше размышлений может увеличить время и стоимость.")
        self._connection_changed()

    def _connection_changed(self, *_args):
        identity = self._discovery_identity()
        if identity != self._catalog_identity:
            self._catalog_identity = identity
            self.api_catalog.cancel()
            self._api_models = []
            if self.provider.currentData() == "lanmessenger-api":
                self._fill_models(self.model.currentText())
                self.auth_info.setText("Укажите URL и ключ, затем нажмите «Загрузить модели». ID можно ввести вручную.")
        if not self.rpc.busy and not self._pending:
            self.rpc.shutdown()
        self._schedule_connection_save()

    def _discovery_identity(self):
        return (self.provider.currentData(), self.endpoint.text().strip(), self.key.text().strip())

    def _open_connection(self):
        self.connection_btn.setChecked(True)
        (self.endpoint if self.provider.currentData() == "lanmessenger-api" else self.provider).setFocus()

    def _catalog_busy(self, busy):
        self.catalog_btn.setEnabled(not busy and not self._logging_in)
        self.catalog_btn.setText("Загрузка…" if busy else "Загрузить модели")

    def _catalog_failed(self, text):
        if not self._closed:
            self.auth_info.setText(text)

    def _api_models_loaded(self, models):
        if self._closed or self.provider.currentData() != "lanmessenger-api":
            return
        self._api_models = models
        self._fill_models(self.model.currentText())
        self._schedule_connection_save()
        self.auth_info.setText(f"API вернул моделей: {len(models)}. Выберите точный ID в списке или введите его вручную.")

    def _schedule_connection_save(self, *_args):
        if self.store and not self._closed:
            self.connection_status.setText("Сохранение подключения…")
            self._connection_save_timer.start(300)

    def _persist_connection(self):
        if not self.store:
            return
        try:
            self._save_connection()
        except (OSError, ValueError) as exc:
            self.connection_status.setText(f"Не удалось сохранить подключение: {exc}")

    def _provider_changed(self):
        self.key.clear()
        self.remember.setChecked(False)
        self._last_provider = self.provider.currentData()
        self._provider_visibility()
        self._fill_models("")
        self._connection_changed()

    def _provider_visibility(self):
        provider = self.provider.currentData()
        self.endpoint.setEnabled(provider == "lanmessenger-api")
        self.key.setEnabled(bool(PROVIDERS[provider][1]))
        self.login_btn.setEnabled(provider in self._oauth_providers and not self._logging_in)

    def _browse_runtime(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка установленного Pi")
        if folder:
            self.runtime.setText(folder)

    def _save(self):
        if self.store:
            try:
                self.store.save()
            except OSError as exc:
                self.status.setText(f"Не удалось сохранить историю ИИ: {exc}")

    def _save_connection(self):
        self._connection_save_timer.stop()
        config = self._config()
        self.store.config.update(config)
        key = self.key.text().strip()
        if self.remember.isChecked() and key:
            self.vault.save(config["provider"], key)
        elif not self.remember.isChecked():
            self.vault.clear()
        self.store.save()
        self.connection_status.setText(
            "Подключение и API-ключ сохранены средствами Windows."
            if self.remember.isChecked() and key else
            "Подключение сохранено. API-ключ — только до выхода из приложения.")
        return config

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text or not self.store or not self.current or self.rpc.busy or self._pending or not self.send_btn.isEnabled():
            return
        try:
            config = self._save_connection()
            if not self.rpc.ready:
                if not config["model"]:
                    raise ValueError("Выберите модель в «Подключение модели» или нажмите «Загрузить модели».")
                program, args, _module = runtime_command(config["runtime"])
                env = prepare_environment(self.store.root, config, self.key.text().strip())
                args += ["--mode", "rpc", "--provider", config["provider"], "--model", config["model"],
                         "--session", self.current["session"], "--no-extensions", "--no-prompt-templates"]
                if config["thinking"] != "auto":
                    args += ["--thinking", config["thinking"]]
                self._pending = text
                self.status.setText("Запуск локального агента…")
                self._controls(True)
                self.rpc.start(program, args, self.work_dir, env)
            else:
                self._submit(text)
        except (OSError, ValueError, RuntimeError) as exc:
            self._error(str(exc))
            self.connection_btn.setChecked(True)

    def _ready_changed(self, ready):
        if ready and self._pending:
            try:
                self.rpc.command({"type": "get_messages"})
            except RuntimeError as exc:
                self._error(str(exc))

    def _submit(self, text):
        self._stopped = False
        self.current["messages"].append({"role": "user", "text": text})
        self.timeline = Timeline(self.current["messages"], self.rpc.redact)
        if self.current["title"] == "Новый чат":
            self.current["title"] = text.replace("\n", " ")[:45]
            self.chat_list.currentItem().setText(self.current["title"])
        self.current["running"] = True
        self.current["interrupted"] = False
        self.current["provider"] = self.provider.currentData()
        self.current["model"] = self.model.currentText()
        self._save()
        self.input.clear()
        self._render()
        self.rpc.send_prompt(text)

    def _controls(self, active):
        self.send_btn.setEnabled(not active and not self._logging_in and bool(self.current) and os.path.normcase(self.current["folder"]) == os.path.normcase(self.work_dir))
        self.stop_btn.setEnabled(active)
        self.new_btn.setEnabled(not active and not self._logging_in)
        self.chat_list.setEnabled(not active and not self._logging_in)
        self.connection_panel.setEnabled(not active)

    def _busy_changed(self, busy):
        self._controls(busy or bool(self._pending))
        if busy:
            self.status.setText("Агент выполняет задачу…")
        elif self.current:
            # Only agent_end confirms successful completion; process errors mark interruption.
            self._save_timer.start(100)

    def _stop(self):
        self._stopped = True
        if self._pending:
            self._pending = None
            self.rpc.shutdown()
            self._controls(False)
            self.status.setText("Запуск отменён; запрос не отправлен.")
        else:
            self.rpc.abort()
            self.status.setText("Остановка агента…")
            self.stop_btn.setEnabled(False)

    def _error(self, text):
        if self._closed:
            return
        self._pending = None
        if self.current and self.current.get("running"):
            self.timeline.finish("interrupted")
            self.current["running"] = False
            self.current["interrupted"] = True
            self.current["messages"].append({"role": "system", "text": self.rpc.redact(text)})
            self._save()
            self._render()
        self._controls(False)
        self.status.setText(self.rpc.redact(text))

    def _event(self, event):
        if not self.current:
            return
        kind = event.get("type")
        messages = self.current["messages"]
        if kind == "response" and event.get("command") == "get_state" and event.get("success"):
            effective = event.get("data", {}).get("thinkingLevel")
            if effective in THINKING_LEVELS:
                requested = self.thinking.currentData()
                text = f"Уровень Pi: {THINKING_LEVELS[effective]}."
                if requested != "auto" and requested != effective:
                    text += f" Запрошен: {THINKING_LEVELS[requested]}; Pi ограничил уровень возможностями модели."
                elif self.provider.currentData() == "lanmessenger-api" and requested != "auto":
                    text += " Поддержку параметра reasoning_effort проверяет ваш сервер API."
                self.thinking_status.setText(text)
        if kind == "response" and event.get("command") == "get_messages" and event.get("success"):
            restored = restore_messages(event.get("data", {}).get("messages", []), self.rpc.redact)
            lost_context = bool(messages) and not restored
            self.current["messages"] = restored
            if lost_context:
                self.current["messages"].append({"role": "system", "text": "Сессия Pi пуста или потеряна. Агент начинает новый контекст."})
            self.timeline = Timeline(self.current["messages"], self.rpc.redact)
            self._save()
            self._render()
            if self._pending:
                text, self._pending = self._pending, None
                try:
                    self._submit(text)
                except RuntimeError as exc:
                    self._error(str(exc))
            return
        self.timeline.event(event)
        if kind == "agent_end":
            self.timeline.finish("stopped" if self._stopped else "completed")
            self.current["running"] = False
            self.current["interrupted"] = False
            self.status.setText("Агент остановлен." if self._stopped else "Задача завершена.")
            self._controls(False)
            self._save()
        elif kind == "extension_ui_request":
            # Discovered extensions are disabled. Never leave an explicit extension dialog hanging.
            method = event.get("method")
            if method in ("select", "confirm", "input", "editor"):
                self.rpc.command({"type": "extension_ui_response", "id": event["id"], "cancelled": True})
        if kind in ("message_start", "message_update", "message_end", "tool_execution_start", "tool_execution_update", "tool_execution_end", "agent_end"):
            if not self._render_timer.isActive():
                self._render_timer.start(50)
            if not self._save_timer.isActive():
                self._save_timer.start(300)

    def _render(self):
        if not self.current:
            return
        # Also assign stable identities to legacy/system entries appended outside the projection.
        for message in self.current["messages"]:
            if "id" not in message:
                message["id"] = str(uuid.uuid4())
        self.output.set_messages(self.current["messages"])

    def _start_auth(self):
        if self.auth.state() != QProcess.ProcessState.NotRunning:
            self._stop_auth()
        config = self._save_connection()
        program, _args, module = runtime_command(config["runtime"])
        agent_dir = self.store.root / "pi"
        agent_dir.mkdir(exist_ok=True)
        self._auth_parser = JsonLines()
        self._auth_closing = False
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PI_CODING_AGENT_DIR", str(agent_dir))
        self.auth.setProcessEnvironment(env)
        self.auth.start(program, [str(auth_helper_path()), str(module), str(agent_dir)])
        if not self.auth.waitForStarted(3000):
            raise ValueError("Не удалось запустить подключение Pi")

    def _auth_command(self, data):
        self.auth.write((json.dumps(data) + "\n").encode("utf-8"))

    def _catalog_request(self):
        if self._logging_in or not self.store:
            return
        try:
            if self.provider.currentData() == "lanmessenger-api":
                self._persist_connection()
                self.api_catalog.fetch(self.endpoint.text(), self.key.text())
                self.auth_info.setText("Загрузка моделей из указанного API…")
                return
            self._start_auth()
            self._auth_command({"type": "list"})
            self.auth_info.setText("Загрузка списка моделей…")
        except (ValueError, OSError) as exc:
            self.auth_info.setText(str(exc))

    def _login(self):
        if self._logging_in or not self.store:
            return
        try:
            self._start_auth()
            self._logging_in = True
            self._controls(False)
            self._auth_command({"type": "login", "provider": self.provider.currentData()})
            self.auth_info.setText("Начинается вход в аккаунт…")
            self.cancel_login_btn.show()
            self.provider.setEnabled(False)
            self.catalog_btn.setEnabled(False)
            self.login_btn.setEnabled(False)
            self.logout_btn.setEnabled(False)
        except (ValueError, OSError) as exc:
            self.auth_info.setText(str(exc))

    def _logout(self):
        try:
            self.rpc.shutdown()
            self._start_auth()
            self._auth_command({"type": "logout", "provider": self.provider.currentData()})
        except (ValueError, OSError) as exc:
            self.auth_info.setText(str(exc))

    def _auth_read(self):
        try:
            events = self._auth_parser.feed(bytes(self.auth.readAllStandardOutput()))
        except ValueError as exc:
            self._auth_failed(str(exc))
            return
        for event in events:
            kind = event.get("type")
            if kind == "catalog":
                self._models = event.get("models", [])
                self._oauth_providers = {p["id"] for p in event.get("providers", [])}
                if self.provider.currentData() == "lanmessenger-api":
                    continue
                self._fill_models(self.model.currentText())
                logged = self.provider.currentData() in event.get("authenticated", [])
                self.auth_info.setText("Аккаунт подключён." if logged else "Модели загружены. Войдите в аккаунт или введите API-ключ.")
            elif kind == "auth":
                url = QUrl(event.get("url", ""))
                if url.scheme() in ("https", "http"):
                    self._auth_url = url.toString()
                    self.auth_open.show()
                    self._open_auth_url()
                self.auth_info.setText(event.get("instructions") or "Завершите вход в браузере.")
            elif kind == "input":
                options = event.get("options") or []
                explanation = event.get("message", "")
                if options:
                    explanation += "\n" + "\n".join(f"{o['id']}: {o['label']}" for o in options)
                self.auth_info.setText(explanation)
                self.auth_input.show()
                self.auth_submit.show()
            elif kind == "progress":
                self.auth_info.setText(event.get("message", ""))
            elif kind in ("logged_in", "logged_out"):
                self._end_login_ui()
                self.auth_info.setText("Аккаунт подключён." if kind == "logged_in" else "Вы вышли из аккаунта.")
                self._auth_command({"type": "list"})
                self.rpc.shutdown()
            elif kind == "error":
                self._auth_failed(event.get("message", "Ошибка входа"))

    def _fill_models(self, selected):
        provider = self.provider.currentData()
        self.model.blockSignals(True)
        self.model.clear()
        if provider == "lanmessenger-api":
            models = sorted(self._api_models, key=lambda m: (m.get("owner", ""), m["id"]))
            owner = None
            for model in models:
                group = model.get("owner") or "Другие модели"
                if owner != group:
                    self.model.addItem(group)
                    self.model.model().item(self.model.count() - 1).setEnabled(False)
                    owner = group
                self.model.addItem(model["id"])
            first = next((i for i in range(self.model.count()) if self.model.model().item(i).isEnabled()), -1)
            self.model.setCurrentIndex(first)
        else:
            models = sorted({m["id"] for m in self._models if m["provider"] == provider})
            self.model.addItems(models)
        if selected:
            self.model.setEditText(selected)
        elif not models:
            self.model.setEditText("")
        self.model.blockSignals(False)

    def _auth_answer(self):
        if self.auth.state() == QProcess.ProcessState.Running:
            self._auth_command({"type": "input", "value": self.auth_input.text().strip()})
            self.auth_input.clear()
            self.auth_input.hide()
            self.auth_submit.hide()

    def _open_auth_url(self):
        if self._auth_url:
            QDesktopServices.openUrl(QUrl(self._auth_url))

    def _end_login_ui(self):
        self._logging_in = False
        self._controls(self.rpc.busy or bool(self._pending))
        self.cancel_login_btn.hide()
        self.auth_open.hide()
        self.auth_input.hide()
        self.auth_submit.hide()
        self.auth_input.clear()
        self.provider.setEnabled(True)
        self.catalog_btn.setEnabled(True)
        self.logout_btn.setEnabled(True)
        self._provider_visibility()

    def _auth_failed(self, text):
        if not self._closed:
            self._end_login_ui()
            self.auth_info.setText(text)

    def _auth_finished(self, code, _status):
        if not self._auth_closing and not self._closed:
            self._auth_failed(f"Подключение Pi завершилось (код {code}). Проверьте runtime и версию Node.js.")

    def _stop_auth(self):
        self._auth_closing = True
        if self.auth.state() != QProcess.ProcessState.NotRunning:
            self.auth.closeWriteChannel()
            if not self.auth.waitForFinished(500):
                self.auth.kill()
                self.auth.waitForFinished(1000)

    def _cancel_login(self):
        self._stop_auth()
        self._end_login_ui()
        self.auth_info.setText("Вход отменён.")

    def shutdown(self):
        if self._closed:
            return
        self._connection_save_timer.stop()
        self._persist_connection()
        self._closed = True
        self.api_catalog.cancel()
        self._render_timer.stop()
        self._save_timer.stop()
        self._pending = None
        if self.current and self.current.get("running"):
            self.timeline.finish("interrupted")
            self.current["running"] = False
            self.current["interrupted"] = True
        self.rpc.shutdown()
        self._stop_auth()
        self._save()
