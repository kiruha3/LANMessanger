"""Integrated local Pi chat, connection settings and account login."""
import json
import math
import os
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QProcess, QProcessEnvironment, QSize, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QInputDialog, QMenu, QProgressBar, QScrollArea, QSplitter, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.ai import (AIStore, JsonLines, KeyVault, PiProcess, PROVIDERS, THINKING_LEVELS, RESPONSE_STYLES,
                       application_folder, auth_helper_path, prepare_environment, runtime_command,
                       rpc_command, process_environment)
from ..core.history import base_dir
from ..core.ai_timeline import Timeline, message_text, restore_messages
from ..core.ai_plugins import PLUGINS, disabled_plugins, disabled_tools
from ..core.ai_catalog import APIModelCatalog
from ..core.ai_images import (CLOUDFLARE_MODEL, HORDE_MODEL, HORDE_URL, IMAGE_PROVIDERS, ImageGenerator,
                              image_bytes, image_content, wait_for_image_cancellations)
from ..core.ai_installer import RuntimeInstaller, runtime_environment, supported
from .ai_transcript import AITranscript
from .ai_instructions import InstructionsDialog
from .ai_plugins import panel_title
from .switch import Switch
from . import theme


class AIChatInput(QPlainTextEdit):
    submitted = pyqtSignal()
    image_received = pyqtSignal(object)
    files_received = pyqtSignal(list)

    def insertFromMimeData(self, source):
        if source.hasUrls():
            files = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
            if files:
                self.files_received.emit(files)
                return
        if source.hasImage():
            self.image_received.emit(source.imageData())
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            self.insertFromMimeData(event.mimeData())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fitting = False
        self.textChanged.connect(self._fit_height)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        if self._fitting:
            return
        self._fitting = True
        try:
            self.document().setTextWidth(max(40, self.viewport().width()))
            line = self.fontMetrics().lineSpacing()
            padding = math.ceil(2 * self.document().documentMargin()) + 2 * self.frameWidth()
            height = math.ceil(self.document().size().height()) * line + padding
            self.setFixedHeight(max(line + padding, min(6 * line + padding, height)))
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not event.isAutoRepeat():
                self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AIModelCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.bubbles()["text"] if self.isEnabled() else theme.bubbles()["ts"]))
        x, y = self.width() - 12, self.height() / 2
        painter.drawPolygon(QPolygonF([QPointF(x - 4, y - 2), QPointF(x + 4, y - 2), QPointF(x, y + 3)]))
        painter.end()


def action_icon(stop=False):
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#161a22"))
    if stop:
        painter.drawRoundedRect(5, 5, 10, 10, 1, 1)
    else:
        painter.drawPolygon(QPolygonF([QPointF(10, 3), QPointF(3, 11), QPointF(7, 11),
                                      QPointF(7, 17), QPointF(13, 17), QPointF(13, 11), QPointF(17, 11)]))
    painter.end()
    return QIcon(pixmap)


def settings_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.bubbles()["text"]))
    painter.translate(12, 12)
    for angle in range(0, 360, 45):
        painter.save()
        painter.rotate(angle)
        painter.drawRoundedRect(-2, -10, 4, 6, 1, 1)
        painter.restore()
    painter.drawEllipse(QPointF(0, 0), 7, 7)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(QPointF(0, 0), 3, 3)
    painter.end()
    return QIcon(pixmap)


class AITab(QWidget):
    open_plugin_catalog = pyqtSignal()

    @property
    def current(self):
        return self._event_run.chat if self._event_run else self._current

    @current.setter
    def current(self, value):
        self._current = value

    def _run(self):
        return self._event_run or self._selected_run

    @property
    def rpc(self):
        return self._run().rpc

    @property
    def images(self):
        return self._run().images

    @property
    def timeline(self):
        return self._run().timeline

    @timeline.setter
    def timeline(self, value):
        self._run().timeline = value

    @property
    def _pending(self):
        return self._run().pending

    @_pending.setter
    def _pending(self, value):
        self._run().pending = value

    @property
    def _stopped(self):
        return self._run().stopped

    @_stopped.setter
    def _stopped(self, value):
        self._run().stopped = value

    @property
    def _task_error(self):
        return self._run().error

    @_task_error.setter
    def _task_error(self, value):
        self._run().error = value

    def _make_run(self, chat):
        run = SimpleNamespace(chat=chat, rpc=PiProcess(self), images=ImageGenerator(self),
                              timeline=None, pending=None, stopped=False, error="", activity=("", False),
                              config={}, key="", profile_id="", image_tool_requests={}, image_catalog_requests={},
                              image_status_requests={}, agent_requests={}, agent_parent_request=None, agent_depth=0,
                              draft="", disabled_tools=(), steering={}, followups={}, redirect=None,
                              using_tools=False, agent_active=False)
        run.attachments = []
        run.sent_images = []
        for signal, handler in ((run.rpc.received, self._event), (run.rpc.error, self._error),
                                (run.rpc.busy_changed, self._busy_changed), (run.rpc.ready_changed, self._ready_changed),
                                (run.images.completed, self._image_completed), (run.images.failed, self._image_failed),
                                (run.images.job_started, self._image_queued),
                                (run.images.queue_updated, self._image_queue_updated),
                                (run.images.job_cancelled, self._image_cancelled)):
            signal.connect(lambda value, run=run, handler=handler: self._dispatch_run(run, handler, value))
        run.images.activity.connect(lambda text: self._dispatch_run(run, self._set_activity, text, True))
        return run

    def _dispatch_run(self, run, handler, *args):
        previous = self._event_run
        self._event_run = run
        try:
            return handler(*args)
        except Exception as exc:
            if getattr(run, "handling_error", False):
                return
            run.handling_error = True
            try:
                detail = run.rpc.redact(traceback.format_exc())
                secrets = [run.key, *self._profile_keys.values()]
                secrets += [connection["key"] for connection in (getattr(run, "image_connections", None) or [])]
                for secret in secrets:
                    if secret:
                        detail = detail.replace(secret, "<ключ скрыт>")
                if self.store:
                    path = self.store.root / "errors.log"
                    try:
                        prior_log = path.read_text(encoding="utf-8")[-48000:] if path.exists() else ""
                        path.write_text(prior_log + "\n" + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n" + detail,
                                        encoding="utf-8")
                    except OSError:
                        pass
                message = "Внутренняя ошибка обработки задачи. Задача прервана, история сохранена. " + type(exc).__name__
                try:
                    self._error(message)
                except Exception:
                    if run.chat:
                        run.chat.update(running=False, interrupted=True, outcome="failed", last_error=message)
                try:
                    run.rpc.shutdown()
                except RuntimeError:
                    pass
            finally:
                run.handling_error = False
        finally:
            self._event_run = previous

    def _visible_run(self):
        return self._event_run is None or self._event_run is self._selected_run

    def _set_activity(self, text, active):
        self._run().activity = (text, active)
        if self._visible_run():
            self.output.set_activity(text, active)

    def __init__(self, parent=None, data_dir=None, work_dir=None):
        super().__init__(parent)
        self._event_run = None
        self._runs = {}
        self._selected_run = self._make_run(None)
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
        self._auth_notice = None
        self._logging_in = False
        self._stopped = False
        self._task_error = ""
        self._profile_keys = {}
        self._loading_profile = False
        self._restoring_chat = False
        self._closed = False
        self.installer = None
        self._installing = False
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

        self.folder_label = QPushButton()
        self.folder_label.setMaximumWidth(150)
        self.folder_label.setText(self.folder_label.fontMetrics().elidedText(self.work_dir, Qt.TextElideMode.ElideMiddle, 180))
        self.folder_label.setToolTip(f"Рабочая папка: {self.work_dir}")
        self.folder_label.setAccessibleName(f"Рабочая папка: {self.work_dir}")
        self.folder_label.setStyleSheet(f"QPushButton {{ color: {theme.bubbles()['ts']}; background: transparent; border: none; padding: 2px; }}")
        self.folder_label.clicked.connect(self._choose_folder)
        self.status = QLabel("Подключите аккаунт или API-ключ и выберите модель.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.hide()
        self.error_settings_btn = QPushButton("Открыть настройки ИИ")
        self.error_settings_btn.clicked.connect(self._open_connection)
        self.error_settings_btn.hide()
        self.image_resume_btn = QPushButton("Получить результат генерации")
        self.image_resume_btn.clicked.connect(self._resume_image)
        self.image_resume_btn.hide()
        self.chat_list = QTreeWidget()
        self.chat_list.setHeaderHidden(True)
        self.chat_list.setIndentation(16)
        self.chat_list.setIconSize(QSize(28, 16))
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_context_menu)
        self._chat_pulse = False
        self._chat_status_icons = {}
        self._chat_pulse_timer = QTimer(self)
        self._chat_pulse_timer.setInterval(700)
        self._chat_pulse_timer.timeout.connect(self._pulse_running_chats)
        self.new_btn = QPushButton("Новый чат")
        self.new_btn.clicked.connect(self._new_chat)
        self.project_btn = QPushButton("Добавить папку…")
        self.project_btn.clicked.connect(self._choose_folder)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.new_btn)
        left_layout.addWidget(self.project_btn)
        left_layout.addWidget(self.chat_list, 1)

        self.connection_btn = QPushButton()
        self.connection_btn.setIcon(settings_icon())
        self.connection_btn.setFixedSize(30, 30)
        self.connection_btn.setAccessibleName("Настройки ИИ")
        self.connection_btn.setToolTip("Настройки ИИ: аккаунты, модели, подключение и инструкции")
        self.connection_btn.setStyleSheet("QPushButton { background: transparent; border: none; padding: 3px; }")
        self.connection_btn.clicked.connect(self._open_connection)
        self.account = QComboBox()
        self.account.setMinimumWidth(150)
        self.account_add = QPushButton("Добавить аккаунт")
        self.account_add.clicked.connect(self._add_account)
        self.account_rename = QPushButton("Переименовать")
        self.account_rename.clicked.connect(self._rename_account)
        self.response_style=QComboBox()
        for key,(label,_prompt) in RESPONSE_STYLES.items():
            self.response_style.addItem(label,key)
        self.response_style.setToolTip('Пояснения агента между этапами работы. Выбор применяется к следующей задаче.')
        self.instructions_btn=QPushButton('Инструкция…')
        self.instructions_btn.clicked.connect(self._edit_instructions)
        self.connection_panel = QWidget()
        form = QFormLayout(self.connection_panel)
        self.provider = QComboBox()
        for key, (label, _variable) in PROVIDERS.items():
            self.provider.addItem(label, key)
        self.model = AIModelCombo()
        self.model.setMinimumWidth(150)
        self.model.setMaximumWidth(300)
        self.model.setToolTip("Модель выбранного аккаунта. Можно выбрать ID или ввести вручную.")
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.lineEdit().setPlaceholderText("Выберите или введите модель")
        self.settings_model = QComboBox()
        self.settings_model.setEditable(True)
        self.settings_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.settings_model.currentTextChanged.connect(self.model.setEditText)
        self.model.currentTextChanged.connect(self._sync_settings_model)
        self.thinking = QComboBox()
        for level, label in THINKING_LEVELS.items():
            self.thinking.addItem(label, level)
        self.thinking_status = QLabel("Уровень зависит от модели и API; больше размышлений может увеличить время и стоимость.")
        self.thinking_status.setWordWrap(True)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ; для входа по аккаунту оставьте пустым")
        self.key_visibility = QPushButton("Показать")
        self.key_visibility.setCheckable(True)
        self.key_visibility.setAccessibleName("Показать API-ключ")
        self.key_visibility.toggled.connect(self._set_key_visible)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.key_visibility)
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
        self.key_disconnect_btn = QPushButton("Отключить API-ключ")
        self.key_disconnect_btn.clicked.connect(self._disconnect_key)
        self.cancel_login_btn = QPushButton("Отменить вход")
        self.cancel_login_btn.clicked.connect(self._cancel_login)
        self.cancel_login_btn.hide()
        buttons = QHBoxLayout()
        for button in (self.login_btn, self.logout_btn, self.key_disconnect_btn, self.cancel_login_btn):
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
        form.addRow("Модель", self.settings_model)
        form.addRow("", self.catalog_btn)
        self.endpoint_label = QLabel("URL API")
        form.addRow(self.endpoint_label, self.endpoint)
        form.addRow("API-ключ", key_row)
        form.addRow("", self.remember)
        form.addRow("", self.connection_status)
        form.addRow("Размышления", self.thinking)
        form.addRow("", self.thinking_status)
        form.addRow("Runtime", runtime_row)
        form.addRow(buttons)
        form.addRow(self.auth_info)
        form.addRow(self.auth_open)
        form.addRow(self.auth_input, self.auth_submit)

        self.output = AITranscript(self.work_dir)
        self.output.welcome.connect_requested.connect(self._open_connection)
        self.output.welcome.free_image_requested.connect(self._select_free_images)
        self.input = AIChatInput()
        self.input.setPlaceholderText("Задача для агента. Enter — отправить, Shift+Enter — новая строка")
        self.input.setStyleSheet("QPlainTextEdit { background: transparent; border: none; padding: 0; }")
        self.input.submitted.connect(self._send)
        self.input.image_received.connect(self._attach_image)
        self.input.files_received.connect(self._attach_files)
        self.send_btn = QPushButton()
        self.send_btn.setAccessibleName("Отправить")
        self.send_btn.setToolTip("Отправить сообщение")
        self.send_btn.setIcon(action_icon())
        self.send_btn.clicked.connect(self._send)
        self.stop_btn = QPushButton()
        self.stop_btn.setAccessibleName("Остановить агента")
        self.stop_btn.setToolTip("Остановить агента")
        self.stop_btn.setIcon(action_icon(True))
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)
        self.stop_btn.hide()
        dark = theme.current() == "dark"
        background = "#1f2630" if dark else "#ffffff"
        border = "#3a4552" if dark else "#d0d0d0"
        hover = "#2c3945" if dark else "#ecf7f5"
        accent = "#63d8ca" if dark else "#0f766e"
        self.followup_actions = QFrame()
        self.followup_actions.setObjectName("aiFollowupActions")
        self.followup_actions.setStyleSheet(
            f"QFrame#aiFollowupActions {{ background: {background}; border: 1px solid {border}; border-radius: 15px; }}"
            "QFrame#aiFollowupActions QPushButton { background: transparent; border: 1px solid transparent; border-radius: 11px; padding: 3px 9px; }"
            f"QFrame#aiFollowupActions QPushButton:hover {{ background: {hover}; }}"
            f"QFrame#aiFollowupActions QPushButton:focus {{ border-color: {accent}; }}"
            f"QFrame#aiFollowupActions QPushButton#aiClarifyButton:enabled {{ color: {accent}; }}"
        )
        followup_layout = QHBoxLayout(self.followup_actions)
        followup_layout.setContentsMargins(3, 3, 3, 3)
        followup_layout.setSpacing(2)
        self.clarify_btn = QPushButton("Уточнить сейчас")
        self.clarify_btn.setObjectName("aiClarifyButton")
        self.clarify_btn.setToolTip("Прервать текущий ответ и отправить уточнение. Если работает инструмент — уточнить после его завершения.")
        self.clarify_btn.clicked.connect(self._clarify)
        followup_layout.addWidget(self.clarify_btn)
        self.queue_btn = QPushButton("В очередь")
        self.queue_btn.setToolTip("Отправить после завершения текущей задачи (Enter)")
        self.queue_btn.clicked.connect(self._send)
        followup_layout.addWidget(self.queue_btn)
        self.followup_actions.hide()
        for button in (self.send_btn, self.stop_btn):
            button.setFixedSize(32, 32)
            size_policy = button.sizePolicy()
            size_policy.setRetainSizeWhenHidden(True)
            button.setSizePolicy(size_policy)
            button.setStyleSheet("QPushButton { background: #d7dde5; border: none; border-radius: 16px; padding: 0; } QPushButton:hover { background: #ffffff; } QPushButton:disabled { background: #8b95a1; }")
        self.composer = QFrame()
        self.composer.setObjectName("aiComposer")
        self.composer.setStyleSheet(f"QFrame#aiComposer {{ background: {background}; border: 1px solid {border}; border-radius: 16px; }}")
        self.model.setStyleSheet("QComboBox { background: transparent; border: none; padding: 2px 22px 2px 2px; } QComboBox QLineEdit { background: transparent; border: none; padding: 2px; } QComboBox::drop-down { border: none; width: 22px; } QComboBox::down-arrow { image: none; }")
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(12, 8, 12, 8)
        composer_layout.setSpacing(4)
        self.attachments_row = QHBoxLayout()
        composer_layout.addLayout(self.attachments_row)
        composer_layout.addWidget(self.input)
        footer = QHBoxLayout()
        self.access_label = QLabel("Полный доступ")
        self.access_label.setStyleSheet("color: #d99341; background: transparent;")
        self.access_label.setToolTip("Агент читает и меняет файлы и выполняет команды с правами текущего пользователя, в том числе за пределами рабочей папки.")
        footer.addWidget(self.access_label)
        footer.addWidget(self.folder_label)
        self.attach_btn = QPushButton("+")
        self.attach_btn.setFixedSize(26, 26)
        self.attach_btn.setAccessibleName("Прикрепить картинку")
        self.attach_btn.setToolTip("Прикрепить картинку; также Ctrl+V или перетаскивание")
        self.attach_btn.clicked.connect(self._choose_images)
        footer.addWidget(self.attach_btn)
        self.tools_btn = QPushButton("Инструменты")
        self.tools_btn.setAccessibleName("Инструменты чата")
        self.tools_btn.clicked.connect(lambda: self.show_chat_tools(self.tools_panel.isHidden()))
        footer.addWidget(self.tools_btn)
        footer.addStretch(1)
        footer.addWidget(self.model)
        footer.addWidget(self.connection_btn)
        footer.addWidget(self.stop_btn)
        footer.addWidget(self.send_btn)
        composer_layout.addLayout(footer)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.install_panel = QWidget()
        install_layout = QVBoxLayout(self.install_panel)
        install_layout.setContentsMargins(0, 0, 0, 0)
        self.install_info = QLabel('Для локального агента нужны компоненты ИИ. Установите их одной кнопкой: Node.js, Pi, Git Bash и инструменты поиска. Нужен интернет; файлы сохраняются в профиле Windows.')
        self.install_info.setWordWrap(True)
        install_layout.addWidget(self.install_info)
        self.install_progress = QProgressBar()
        self.install_progress.hide()
        install_layout.addWidget(self.install_progress)
        install_buttons = QHBoxLayout()
        self.install_btn = QPushButton('Установить компоненты ИИ')
        self.install_btn.setEnabled(supported())
        self.install_btn.clicked.connect(self._install_runtime)
        self.install_cancel = QPushButton('Отменить установку')
        self.install_cancel.clicked.connect(self._cancel_install)
        self.install_cancel.hide()
        install_buttons.addWidget(self.install_btn)
        install_buttons.addWidget(self.install_cancel)
        install_layout.addLayout(install_buttons)
        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("Настройки ИИ")
        self.settings_dialog.resize(680, 680)
        settings_layout = QVBoxLayout(self.settings_dialog)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_content = QWidget()
        settings_content_layout = QVBoxLayout(settings_content)
        self.workspace_path = QLineEdit(self.work_dir)
        self.workspace_path.setReadOnly(True)
        self.workspace_btn = QPushButton("Выбрать папку…")
        self.workspace_btn.clicked.connect(self._choose_folder)
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(QLabel("Рабочая папка"))
        workspace_row.addWidget(self.workspace_path, 1)
        workspace_row.addWidget(self.workspace_btn)
        settings_content_layout.addLayout(workspace_row)
        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("Аккаунт"))
        account_row.addWidget(self.account, 1)
        account_row.addWidget(self.account_add)
        account_row.addWidget(self.account_rename)
        settings_content_layout.addLayout(account_row)
        settings_content_layout.addWidget(self.connection_panel)
        self.image_tool_panel = QWidget()
        image_tool_form = QFormLayout(self.image_tool_panel)
        image_tool_form.setContentsMargins(0, 0, 0, 0)
        image_tool_title = QLabel("Инструмент image gen")
        image_tool_title.setStyleSheet("font-weight: bold;")
        image_tool_form.addRow(image_tool_title)
        self.image_tool_account = QComboBox()
        self.image_tool_account.setToolTip("Подключение для генерации картинок текстовым агентом этого аккаунта.")
        self.image_tool_model = QComboBox()
        self.image_tool_model.setEditable(True)
        self.image_tool_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.image_tool_model.lineEdit().setPlaceholderText("Модель из аккаунта картинок")
        image_tool_form.addRow("image gen — аккаунт", self.image_tool_account)
        image_tool_form.addRow("image gen — модель", self.image_tool_model)
        image_tool_hint = QLabel("Выберите аккаунт картинок или добавьте его в списке «Аккаунт» выше. Провайдер, URL и ключ настраиваются в самом аккаунте картинок. Пустая модель использует его модель. Изменения применяются к следующей задаче; при ошибке сервис автоматически не меняется.")
        image_tool_hint.setWordWrap(True)
        image_tool_form.addRow(image_tool_hint)
        settings_content_layout.addWidget(self.image_tool_panel)
        instruction_row = QFormLayout()
        instruction_row.addRow("Пояснения агента", self.response_style)
        instruction_row.addRow("Промпт", self.instructions_btn)
        settings_content_layout.addLayout(instruction_row)
        settings_content_layout.addWidget(self.install_panel)
        settings_content_layout.addStretch(1)
        settings_scroll.setWidget(settings_content)
        settings_layout.addWidget(settings_scroll)
        settings_close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        settings_close.rejected.connect(self.settings_dialog.close)
        settings_layout.addWidget(settings_close)
        self.settings_dialog.finished.connect(self._persist_connection)
        self.settings_dialog.finished.connect(lambda _result: self.key_visibility.setChecked(False))
        self.chat_title = panel_title("")
        self.chat_title.setTextFormat(Qt.TextFormat.PlainText)
        right_layout.addWidget(self.chat_title)
        right_layout.addWidget(self.status)
        right_layout.addWidget(self.error_settings_btn, 0, Qt.AlignmentFlag.AlignLeft)
        right_layout.addWidget(self.image_resume_btn, 0, Qt.AlignmentFlag.AlignLeft)
        right_layout.addWidget(self.output, 1)
        right_layout.addWidget(self.followup_actions, 0, Qt.AlignmentFlag.AlignRight)
        right_layout.addWidget(self.composer)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        self.tools_panel = self._create_chat_tools()
        splitter.addWidget(self.tools_panel)
        splitter.setCollapsible(2, False)
        self.tools_panel.hide()
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 650, 240])
        self.output.open_chat.connect(self._open_ai_chat)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        try:
            self.store = AIStore(data_dir or str(Path(base_dir(), "ai-data")))
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            self.status.show()
            self.send_btn.setEnabled(False)
            self.new_btn.setEnabled(False)
            self.tools_btn.setEnabled(False)
            self.connection_panel.setEnabled(False)
            for widget in (self.account, self.account_add, self.account_rename, self.model, self.catalog_btn,
                           self.response_style, self.instructions_btn, self.project_btn, self.folder_label, self.workspace_btn):
                widget.setEnabled(False)
            return
        self.vault = KeyVault(self.store.profile_root())
        self.output.image_root = self.store.root / "images"
        self._fill_accounts()
        self.image_tool_account.currentIndexChanged.connect(self._image_tool_account_changed)
        self.image_tool_model.currentTextChanged.connect(self._connection_changed)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.store.config["provider"])))
        profile = next(p for p in self.store.profiles if p["id"] == self.store.active_profile)
        self._models = profile.get("models", [])
        self._api_models = profile.get("api_models", [])
        self._fill_models(self.store.config["model"])
        self.endpoint.setText(self.store.config["endpoint"])
        self.runtime.setText(self.store.config["runtime"])
        self.thinking.setCurrentIndex(max(0, self.thinking.findData(self.store.config["thinking"])))
        self.response_style.setCurrentIndex(max(0,self.response_style.findData(self.store.config['response_style'])))
        credential = self.vault.load()
        if credential.get("provider") == self.provider.currentData():
            self.key.setText(credential.get("key", ""))
            self.remember.setChecked(bool(credential.get("key")))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.model.currentTextChanged.connect(self._connection_changed)
        self.thinking.currentIndexChanged.connect(self._thinking_changed)
        self.response_style.currentIndexChanged.connect(self._connection_changed)
        self.endpoint.textChanged.connect(self._connection_changed)
        self.runtime.textChanged.connect(self._connection_changed)
        self.key.textChanged.connect(self._connection_changed)
        self.key.textChanged.connect(self._provider_visibility)
        self.remember.toggled.connect(self._schedule_connection_save)
        self.remember.toggled.connect(self._provider_visibility)
        self._last_provider = self.provider.currentData()
        self._catalog_identity = self._discovery_identity()
        self.account.currentIndexChanged.connect(self._account_changed)
        if not self.store.chats:
            self.store.new_chat(self.work_dir)
        self._fill_chats()
        self.chat_list.currentItemChanged.connect(self._tree_chat_changed)
        index = next((i for i, chat in enumerate(self.store.chats) if chat["id"] == self.store.active_chat), 0)
        self._select_chat(index)
        self._provider_visibility()
        self._check_runtime()

    def _create_chat_tools(self):
        panel = QFrame()
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(310)
        card_color = "#202a35" if theme.current() == "dark" else "#ffffff"
        panel.setStyleSheet(f"QFrame[toolCard=true] {{ background: {card_color}; border: 1px solid {theme.bubbles()['in']}; border-radius: 8px; }}")
        layout = QVBoxLayout(panel)
        heading = QHBoxLayout()
        heading.addWidget(panel_title("Инструменты чата"), 1)
        close = QPushButton("×")
        close.setAccessibleName("Свернуть инструменты чата")
        close.setFixedWidth(30)
        close.clicked.connect(lambda: self.show_chat_tools(False))
        heading.addWidget(close)
        layout.addLayout(heading)
        hint = QLabel("Выбор действует для этого чата со следующего запроса.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.plugin_checks = {}
        for plugin_id, (title, summary, _category, _tools) in PLUGINS.items():
            card = QFrame()
            card.setProperty("toolCard", True)
            entry = QVBoxLayout(card)
            toggle = Switch(title)
            toggle.toggled.connect(lambda enabled, key=plugin_id: self._set_chat_plugin(key, enabled))
            self.plugin_checks[plugin_id] = toggle
            entry.addWidget(toggle)
            detail = QLabel(summary)
            detail.setWordWrap(True)
            entry.addWidget(detail)
            layout.addWidget(card)
        layout.addStretch(1)
        catalog = QPushButton("Открыть каталог")
        catalog.clicked.connect(self.open_plugin_catalog.emit)
        layout.addWidget(catalog)
        return panel

    def show_chat_tools(self, visible=True):
        if self.current:
            self.tools_panel.setVisible(visible)

    def open_plugin_settings(self, plugin_id):
        if self.current and plugin_id == "images":
            self._open_connection()
            if self.image_tool_account.isVisible():
                self.settings_dialog.findChild(QScrollArea).ensureWidgetVisible(self.image_tool_account)
                self.image_tool_account.setFocus()

    def _open_ai_chat(self, chat_id):
        if self.store:
            index = next((index for index, chat in enumerate(self.store.chats) if chat['id'] == chat_id), None)
            if index is not None and self.current is not self.store.chats[index]:
                self._select_chat(index)

    def _sync_chat_tools(self):
        disabled = disabled_plugins(self.current or {})
        for plugin_id, toggle in self.plugin_checks.items():
            prior = toggle.blockSignals(True)
            toggle.setChecked(plugin_id not in disabled)
            toggle.blockSignals(prior)
        self.tools_btn.setText(f"Инструменты {len(PLUGINS) - len(disabled)}")

    def _set_chat_plugin(self, plugin_id, enabled):
        if not self.current:
            return
        disabled = set(disabled_plugins(self.current))
        if enabled:
            disabled.discard(plugin_id)
        else:
            disabled.add(plugin_id)
        self.current['disabled_plugins'] = [key for key in PLUGINS if key in disabled]
        self._save()
        self._sync_chat_tools()

    def _task_busy(self):
        return self.rpc.busy or self.images.busy or bool(self._run().redirect)

    def _select_free_images(self):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        profile = self.store.ensure_horde_profile()
        self._fill_accounts()
        self.account.setCurrentIndex(self.account.findData(profile["id"]))
        self.input.setFocus()

    def _fill_accounts(self):
        self.account.blockSignals(True)
        self.account.clear()
        for profile in self.store.profiles:
            self.account.addItem(profile["name"], profile["id"])
        self.account.setCurrentIndex(self.account.findData(self.store.active_profile))
        self.account.blockSignals(False)
        self._fill_image_tool_accounts()

    def _fill_image_tool_accounts(self):
        self.image_tool_account.blockSignals(True)
        self.image_tool_account.clear()
        self.image_tool_account.addItem("Бесплатный AI Horde (по умолчанию)", "")
        for profile in self.store.profiles:
            if profile["config"]["provider"] in IMAGE_PROVIDERS:
                label = profile["name"] + " — " + PROVIDERS[profile["config"]["provider"]][0]
                self.image_tool_account.addItem(label, profile["id"])
        selected = self.store.config["image_account"]
        index = self.image_tool_account.findData(selected)
        if index < 0:
            self.image_tool_account.addItem("Аккаунт недоступен — выберите другой", selected)
            index = self.image_tool_account.count() - 1
        self.image_tool_account.setCurrentIndex(index)
        self.image_tool_account.blockSignals(False)
        self._fill_image_tool_models(self.store.config["image_model"])

    def _fill_image_tool_models(self, selected=""):
        profile_id = self.image_tool_account.currentData()
        profile = next((p for p in self.store.profiles if p["config"]["provider"] in IMAGE_PROVIDERS
                        and (p["id"] == profile_id if profile_id else p["config"]["provider"] == "ai-horde")), None)
        models = ({p["id"] for p in profile.get("api_models", [])} | {profile["config"]["model"]}) if profile else set()
        self.image_tool_model.blockSignals(True)
        self.image_tool_model.clear()
        self.image_tool_model.addItem("")
        self.image_tool_model.addItems(sorted(m for m in models if m))
        self.image_tool_model.setEditText(selected)
        self.image_tool_model.blockSignals(False)

    def _image_tool_account_changed(self):
        if self._loading_profile or self._restoring_chat:
            return
        self._fill_image_tool_models()
        self._connection_changed()

    def _add_account(self):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        name, accepted = QInputDialog.getText(self, "Добавить аккаунт", "Название аккаунта или API-подключения:")
        if accepted and name.strip():
            try:
                self._save_connection()
                profile = self.store.add_profile(name)
                self._fill_accounts()
                self.account.setCurrentIndex(self.account.findData(profile["id"]))
                self._open_connection()
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def _rename_account(self):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        profile = next(p for p in self.store.profiles if p["id"] == self.store.active_profile)
        name, accepted = QInputDialog.getText(self, "Переименовать аккаунт", "Название:", text=profile["name"])
        if accepted and name.strip():
            if len(name.strip()) > 80:
                self.status.setText("Название аккаунта должно содержать от 1 до 80 символов")
                return
            profile["name"] = name.strip()
            self._save()
            self._fill_accounts()

    def _account_changed(self, _index):
        if self._loading_profile or not self.store:
            return
        profile_id = self.account.currentData()
        if not profile_id or profile_id == self.store.active_profile:
            return
        if ((self._task_busy() or self._pending) and not self._restoring_chat) or self._installing or self._logging_in:
            self._fill_accounts()
            return
        self._connection_save_timer.stop()
        try:
            self._save_connection()
            if not self._restoring_chat:
                self.rpc.shutdown()
            self._stop_auth()
            self.api_catalog.cancel()
            self.store.select_profile(profile_id)
            self.key_visibility.setChecked(False)
            self.vault = KeyVault(self.store.profile_root())
            self._loading_profile = True
            widgets = (self.provider, self.model, self.endpoint, self.runtime, self.thinking,
                       self.response_style, self.key, self.remember)
            for widget in widgets:
                widget.blockSignals(True)
            try:
                config = self.store.config
                self.provider.setCurrentIndex(max(0, self.provider.findData(config["provider"])))
                self.endpoint.setText(config["endpoint"])
                self.runtime.setText(config["runtime"])
                self.thinking.setCurrentIndex(max(0, self.thinking.findData(config["thinking"])))
                self.response_style.setCurrentIndex(max(0, self.response_style.findData(config["response_style"])))
                profile = next(p for p in self.store.profiles if p["id"] == profile_id)
                self._models = profile.get("models", [])
                self._api_models = profile.get("api_models", [])
                self._fill_models(config["model"])
                self._fill_image_tool_accounts()
                credential = self.vault.load()
                saved = credential.get("provider") == config["provider"] and bool(credential.get("key"))
                self.key.setText(credential.get("key", "") if saved else self._profile_keys.get(profile_id, ""))
                self.remember.setChecked(saved)
            finally:
                for widget in widgets:
                    widget.blockSignals(False)
                self._loading_profile = False
            self._auth_notice = None
            self._auth_url = ""
            self.auth_info.setText("Выбрано отдельное подключение. Войдите или задайте ключ и загрузите его модели.")
            self._last_provider = self.provider.currentData()
            self._catalog_identity = self._discovery_identity()
            self._provider_visibility()
            self._persist_connection()
            self._check_runtime()
            self.status.hide()
            self.error_settings_btn.hide()
        except (OSError, ValueError) as exc:
            self.status.setText(f"Не удалось сменить аккаунт: {exc}")
            self._fill_accounts()

    def _check_runtime(self):
        if self.provider.currentData() in IMAGE_PROVIDERS:
            self.install_panel.hide()
            return
        try:
            from ..core.ai_installer import runtime_folder, runtime_complete
            managed = runtime_folder()
            if managed.exists() and not runtime_complete(managed):
                raise ValueError('incomplete managed runtime')
            runtime_command(self.runtime.text())
            # Include the shell check without writing provider settings.
            if os.name == 'nt':
                import shutil
                from ..core.ai_installer import managed_shell
                shells = [managed_shell(), Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe']
                if not any(p.is_file() for p in shells) and not shutil.which('bash'):
                    raise ValueError('missing shell')
        except (OSError, ValueError):
            self.install_panel.show()
        else:
            self.install_panel.hide()

    def _install_runtime(self):
        if self._closed or (self.installer and self.installer.isRunning()) or self._task_busy() or self._pending or self._logging_in:
            return
        self.install_btn.setEnabled(False)
        self._installing = True
        self.rpc.shutdown()
        self._stop_auth()
        self.connection_panel.setEnabled(False)
        self._controls(False)
        self.install_cancel.setEnabled(True)
        self.install_cancel.show()
        self.install_progress.setValue(0)
        self.install_progress.show()
        self.installer = RuntimeInstaller(self)
        self.installer.progress.connect(self._install_progress)
        self.installer.succeeded.connect(self._install_done)
        self.installer.failed.connect(self._install_failed)
        self.installer.start()

    def _install_progress(self, text, percent):
        self.install_info.setText(text)
        self.install_progress.setValue(percent)

    def _cancel_install(self):
        if self.installer:
            self.installer.cancel()
            self.install_cancel.setEnabled(False)
            self.install_info.setText('Отмена установки…')

    def _install_done(self):
        if self._closed:
            return
        self.install_cancel.hide()
        self._installing = False
        self.connection_panel.setEnabled(True)
        self._controls(False)
        self.install_btn.setEnabled(True)
        self.status.setText('Компоненты ИИ установлены. Настройте подключение и отправьте сообщение.')
        self._check_runtime()
        self._open_connection()

    def _install_failed(self, text):
        if self._closed:
            return
        self.install_info.setText(text)
        self._installing = False
        self.connection_panel.setEnabled(True)
        self._controls(False)
        self.install_cancel.hide()
        self.install_progress.hide()
        self.install_btn.setText('Повторить установку компонентов ИИ')
        self.install_btn.setEnabled(supported())

    def _fill_chats(self):
        expanded = {self.chat_list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.chat_list.topLevelItemCount()) if self.chat_list.topLevelItem(i).isExpanded()}
        first_fill = self.chat_list.topLevelItemCount() == 0
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        selected_id = self._current["id"] if self._current else ""
        projects = {}
        projects_in_order = {key: rank for rank, key in enumerate(dict.fromkeys(
            os.path.normcase(str(Path(chat["folder"]).resolve()))
            for chat in sorted(self.store.chats, key=lambda chat: chat.get("last_activity", 0), reverse=True)))}
        for chat in sorted(self.store.chats, key=lambda chat: (
                -projects_in_order[os.path.normcase(str(Path(chat["folder"]).resolve()))],
                bool(chat.get("pinned")), chat.get("last_activity", 0)), reverse=True):
            path = str(Path(chat["folder"]).resolve())
            key = os.path.normcase(path)
            if key not in projects:
                project = QTreeWidgetItem(self.chat_list)
                project.setData(0, Qt.ItemDataRole.UserRole, key)
                project.setToolTip(0, path)
                project.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                project.setExpanded(first_fill or key in expanded)
                projects[key] = project
            project = projects[key]
            item = QTreeWidgetItem(project)
            item.setData(0, Qt.ItemDataRole.UserRole, chat["id"])
            self._style_chat_item(item, chat)
            if chat["id"] == selected_id:
                self.chat_list.setCurrentItem(item)
            if chat["id"] == self.store.active_chat:
                self.chat_list.setCurrentItem(item)
        for project in projects.values():
            folder = Path(project.toolTip(0))
            project.setText(0, f"{folder.name or str(folder)} ({project.childCount()})")
        self.chat_list.blockSignals(False)
        self._sync_chat_pulse()

    def _chat_status_icon(self, state, pinned=False):
        key = (state, self._chat_pulse if state == "running" else False, theme.current(), pinned)
        if key not in self._chat_status_icons:
            dark = theme.current() == "dark"
            color = {"idle": "#8b95a1" if dark else "#737b85", "ready": "#5eead4" if dark else "#087f72",
                     "failed": "#f29b9b" if dark else "#b42318", "running": "#2dd4bf" if dark else "#0d9488"}[state]
            pixmap = QPixmap(28 if pinned else 14, 14)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            shade = QColor(color)
            if state == "running" and not self._chat_pulse:
                shade.setAlpha(70)
            painter.setBrush(shade)
            painter.drawEllipse(3, 3, 8, 8)
            if pinned:
                painter.setBrush(QColor(color))
                painter.translate(20, 6)
                painter.rotate(25)
                painter.drawRoundedRect(-3, -5, 6, 2, 1, 1)
                painter.drawRect(-2, -3, 4, 5)
                painter.drawRoundedRect(-4, 1, 8, 2, 1, 1)
                painter.drawRect(0, 3, 1, 5)
            painter.end()
            self._chat_status_icons[key] = QIcon(pixmap)
        return self._chat_status_icons[key]

    def _style_chat_item(self, item, chat):
        state = ("running" if chat.get("running") else
                 "failed" if chat.get("unread_result") and chat.get("result_status", chat.get("outcome")) == "failed" else
                 "ready" if chat.get("unread_result") else "idle")
        status = {"running": "работает", "ready": "готово", "failed": "ошибка", "idle": ""}[state]
        item.setText(0, chat["title"] + (" · " + status if status else ""))
        item.setData(0, Qt.ItemDataRole.UserRole + 1, state)
        item.setIcon(0, self._chat_status_icon(state, bool(chat.get("pinned"))))
        dark = theme.current() == "dark"
        color = {"idle": "#8b95a1" if dark else "#737b85", "ready": "#5eead4" if dark else "#087f72",
                 "failed": "#f29b9b" if dark else "#b42318", "running": "#d7dde5" if dark else "#1f1f1f"}[state]
        item.setForeground(0, QBrush(QColor(color)))
        background = {"ready": "#153b35" if dark else "#e0f5ed", "failed": "#4b252b" if dark else "#fee4e2"}.get(state)
        item.setBackground(0, QBrush(QColor(background)) if background else QBrush())
        font = item.font(0)
        font.setBold(state in ("ready", "failed"))
        item.setFont(0, font)
        item.setToolTip(0, chat["title"] + ("\nЗакреплён" if chat.get("pinned") else "") + "\n" +
                       (status.capitalize() if status else "Нет новых результатов") + "\n" + chat["folder"])

    def _sync_chat_pulse(self):
        if not self._closed and any(chat.get("running") for chat in self.store.chats):
            if not self._chat_pulse_timer.isActive():
                self._chat_pulse_timer.start()
        else:
            self._chat_pulse_timer.stop()

    def _pulse_running_chats(self):
        self._chat_pulse = not self._chat_pulse
        for i in range(self.chat_list.topLevelItemCount()):
            group = self.chat_list.topLevelItem(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.data(0, Qt.ItemDataRole.UserRole + 1) == "running":
                    chat_id = item.data(0, Qt.ItemDataRole.UserRole)
                    chat = next((chat for chat in self.store.chats if chat["id"] == chat_id), {})
                    item.setIcon(0, self._chat_status_icon("running", bool(chat.get("pinned"))))

    def _chat_context_menu(self, point):
        item = self.chat_list.itemAt(point)
        if not self.store or item is None or item.parent() is None:
            return
        chat_id = item.data(0, Qt.ItemDataRole.UserRole)
        chat = next((chat for chat in self.store.chats if chat["id"] == chat_id), None)
        if chat is None:
            return
        menu = QMenu(self.chat_list)
        action = menu.addAction("Открепить чат" if chat.get("pinned") else "Закрепить чат")
        action.triggered.connect(lambda: self._toggle_chat_pin(chat_id))
        menu.popup(self.chat_list.viewport().mapToGlobal(point))
        menu.aboutToHide.connect(menu.deleteLater)

    def _toggle_chat_pin(self, chat_id):
        chat = next((chat for chat in self.store.chats if chat["id"] == chat_id), None) if self.store else None
        if chat:
            chat["pinned"] = not chat.get("pinned", False)
            self._save()
            self._fill_chats()

    def _mark_chat_result(self, chat, outcome):
        chat["last_activity"] = time.time()
        chat["result_status"] = outcome
        chat["unread_result"] = outcome in ("completed", "failed") and not (chat is self._current and self.isVisible())
        self._fill_chats()

    def _update_chat_item(self, chat):
        for i in range(self.chat_list.topLevelItemCount()):
            group = self.chat_list.topLevelItem(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if item.data(0, Qt.ItemDataRole.UserRole) == chat["id"]:
                    self._style_chat_item(item, chat)
        self._sync_chat_pulse()

    def _tree_chat_changed(self, item, _previous):
        if item is None:
            return
        if item.parent() is None:
            item.setExpanded(True)
            if item.childCount():
                self.chat_list.setCurrentItem(item.child(0))
            return
        chat_id = item.data(0, Qt.ItemDataRole.UserRole)
        index = next((i for i, chat in enumerate(self.store.chats) if chat["id"] == chat_id), -1)
        self._select_chat(index)

    def _choose_folder(self):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        folder = QFileDialog.getExistingDirectory(self.settings_dialog if self.settings_dialog.isVisible() else self,
                                                 "Рабочая папка проекта", self.work_dir)
        if folder:
            self._open_project(folder)

    def _open_project(self, folder):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        path = Path(folder).resolve()
        if not path.is_dir():
            self.status.setText("Рабочая папка недоступна. Нажмите «Добавить папку…» и выберите существующий проект.")
            self.status.show()
            return
        key = os.path.normcase(str(path))
        index = next((i for i, chat in enumerate(self.store.chats)
                      if os.path.normcase(str(Path(chat["folder"]).resolve())) == key), -1)
        if index < 0:
            self._persist_connection()
            self.store.new_chat(str(path))
            index = 0
            self._fill_chats()
        self._select_chat(index)

    def _new_chat(self):
        if self.store and not self._installing and not self._logging_in:
            self._persist_connection()
            self.store.new_chat(self.work_dir)
            self._fill_chats()
            self._select_chat(0)

    def _select_chat(self, index):
        if (not self.store or not 0 <= index < len(self.store.chats) or self._installing
                or self._logging_in):
            return
        self._save_timer.stop()
        self._render_timer.stop()
        if self.current:
            draft = self.input.toPlainText()
            self._selected_run.draft = "" if self._pending == draft else draft
            self._persist_connection()
        self.current = self.store.chats[index]
        if self.current["id"] not in self._runs:
            self._runs[self.current["id"]] = self._make_run(self.current)
        self._selected_run = self._runs[self.current["id"]]
        self._sync_chat_tools()
        self.current["unread_result"] = False
        self._update_chat_item(self.current)
        self._restore_chat_selection()
        self.input.setPlainText(self._selected_run.draft)
        self._render_attachments()
        self.work_dir = str(Path(self.current["folder"]).resolve())
        self.store.active_chat = self.current["id"]
        self.folder_label.setText(self.folder_label.fontMetrics().elidedText(self.work_dir, Qt.TextElideMode.ElideMiddle, 180))
        self.folder_label.setToolTip(f"Рабочая папка: {self.work_dir}. Нажмите, чтобы выбрать проект.")
        self.folder_label.setAccessibleName(f"Рабочая папка: {self.work_dir}")
        self.workspace_path.setText(self.work_dir)
        self.output.folder = self.work_dir
        self.chat_list.blockSignals(True)
        for group_index in range(self.chat_list.topLevelItemCount()):
            group = self.chat_list.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                if child.data(0, Qt.ItemDataRole.UserRole) == self.current["id"]:
                    group.setExpanded(True)
                    self.chat_list.setCurrentItem(child)
        self.chat_list.blockSignals(False)
        if not self.timeline or not self._task_busy():
            self.timeline = Timeline(self.current["messages"], self.rpc.redact)
        if not self.current.get("running"):
            self.timeline.finish("interrupted")
        self._render()
        available = Path(self.work_dir).is_dir()
        self._controls(self._task_busy() or bool(self._pending))
        self.output.set_activity(*self._run().activity)
        self.error_settings_btn.hide()
        self.image_resume_btn.setVisible(bool(self.current.get("image_job")))
        if not available:
            self.status.setText("Рабочая папка этого чата недоступна. История сохранена. Нажмите «Добавить папку…» и выберите существующий проект.")
        elif self.current.get("last_error"):
            self.status.setText(self.current["last_error"])
            self.error_settings_btn.show()
        elif self.current.get("interrupted"):
            self.status.setText("Предыдущая задача прервана. Новый запрос продолжит сохранённую сессию; прежний запрос не повторяется.")
        else:
            self.status.clear()
        self.status.setVisible(not available or bool(self.current.get("interrupted")) or bool(self.current.get("last_error")))
        self._save()

    def _remember_chat_selection(self):
        if self.current and not self._restoring_chat:
            self.current["ai_selection"] = {"profile_id": self.store.active_profile,
                                            "model": self.model.currentText().strip()}

    def _restore_chat_selection(self):
        selection = self.current.get("ai_selection", {"profile_id": self.current.get("profile_id"),
                                                      "model": self.current.get("model")})
        if not isinstance(selection, dict):
            return
        profile_id, model = selection.get("profile_id"), selection.get("model")
        if not isinstance(model, str) or not any(p["id"] == profile_id for p in self.store.profiles):
            self._remember_chat_selection()
            return
        self._restoring_chat = True
        try:
            if self.account.findData(profile_id) < 0:
                self._fill_accounts()
            self.account.setCurrentIndex(self.account.findData(profile_id))
            if self.store.active_profile == profile_id:
                self.model.setEditText(model)
        finally:
            self._restoring_chat = False
        self._persist_connection()

    def _config(self):
        return {"provider": self.provider.currentData(), "model": self.model.currentText().strip(),
                "endpoint": self.endpoint.text().strip(), "runtime": self.runtime.text().strip(),
                "thinking": self.thinking.currentData(), "response_style": self.response_style.currentData(),
                "image_account": self.image_tool_account.currentData() or "",
                "image_model": self.image_tool_model.currentText().strip()}

    def _choose_images(self):
        paths, _filter = QFileDialog.getOpenFileNames(self, "Прикрепить картинки", self.work_dir,
                                                    "Картинки (*.png *.jpg *.jpeg *.webp)")
        self._attach_files(paths)

    def _attach_image(self, picture):
        if not isinstance(picture, (QImage, QPixmap)) or picture.isNull() or picture.width() * picture.height() > 32_000_000:
            self._attachment_error("Картинка слишком большая или повреждена.")
            return
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if picture.save(buffer, "PNG"):
            self._attach_data(bytes(data))

    def _attach_files(self, paths):
        for path in paths:
            try:
                source = Path(path)
                if source.stat().st_size > 24 * 1024 * 1024:
                    raise ValueError("Картинка превышает 24 МиБ.")
                self._attach_data(source.read_bytes())
            except (OSError, ValueError) as exc:
                self._attachment_error(str(exc))

    def _attach_data(self, data):
        if not self.store or not self.current:
            return
        try:
            if self.provider.currentData() in IMAGE_PROVIDERS:
                raise ValueError("Для разбора картинки выберите текстовую модель с поддержкой изображений.")
            if len(self._run().attachments) >= 4:
                raise ValueError("К одному сообщению можно прикрепить до четырёх картинок.")
            suffix = image_bytes(data)
            folder = self.store.root / "images" / self.current["id"]
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid.uuid4()}.{suffix}"
            path.write_bytes(data)
            self._run().attachments.append(str(path))
            self._render_attachments()
        except (OSError, ValueError) as exc:
            self._attachment_error(str(exc))

    def _attachment_error(self, text):
        self.status.setText(text)
        self.status.show()

    def _render_attachments(self):
        while self.attachments_row.count():
            widget = self.attachments_row.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        for index, path in enumerate(self._run().attachments):
            button = QPushButton(f"Картинка {index + 1} ×")
            button.setIcon(QIcon(QPixmap(path)))
            button.setToolTip("Убрать вложение")
            button.clicked.connect(lambda _checked=False, path=path: self._remove_attachment(path))
            self.attachments_row.addWidget(button)
        self.attachments_row.addStretch(1)

    def _remove_attachment(self, path):
        self._run().attachments.remove(path)
        self._render_attachments()

    def _edit_instructions(self):
        if not self.store or self._task_busy() or self._pending or self._installing or self._logging_in:
            return
        style=self.response_style.currentData()
        label,default=RESPONSE_STYLES[style]
        key='instructions_'+style
        dialog=InstructionsDialog(label,self.store.config[key],default,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.store.config[key]=dialog.editor.toPlainText()
            self._connection_changed()
            self._persist_connection()
        dialog.deleteLater()

    def _thinking_changed(self):
        self.thinking_status.setText("Уровень зависит от модели и API; больше размышлений может увеличить время и стоимость.")
        self._connection_changed()

    def _connection_changed(self, *_args):
        if self._loading_profile or self._restoring_chat:
            return
        identity = self._discovery_identity()
        if identity != self._catalog_identity:
            self._catalog_identity = identity
            self.api_catalog.cancel()
            self._api_models = []
            if self.provider.currentData() in ("lanmessenger-api", *IMAGE_PROVIDERS):
                self._fill_models(self.model.currentText())
                self.auth_info.setText("Укажите URL и ключ, затем нажмите «Загрузить модели». ID можно ввести вручную.")
        if not self._task_busy() and not self._pending:
            self.rpc.shutdown()
            self.status.hide()
            self.error_settings_btn.hide()
            if self.current:
                self.current.pop("last_error", None)
        self._schedule_connection_save()

    def _discovery_identity(self):
        return (self.provider.currentData(), self.endpoint.text().strip(), self.key.text().strip())

    def _open_connection(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()
        (self.endpoint if self.provider.currentData() in ("lanmessenger-api", "lanmessenger-images") else self.provider).setFocus()

    def _sync_settings_model(self, text):
        self.settings_model.blockSignals(True)
        self.settings_model.setEditText(text)
        self.settings_model.blockSignals(False)

    def _catalog_busy(self, busy):
        self.catalog_btn.setEnabled(not busy and not self._logging_in)
        self.catalog_btn.setText("Загрузка…" if busy else "Загрузить модели")

    def _catalog_failed(self, text):
        if not self._closed:
            self.auth_info.setText(text)

    def _api_models_loaded(self, models):
        if self._closed or self.provider.currentData() not in ("lanmessenger-api", *IMAGE_PROVIDERS):
            return
        self._api_models = models
        self._fill_models(self.model.currentText())
        self._schedule_connection_save()
        unavailable = sum(m.get("available") is False for m in models)
        self.auth_info.setText(f"API вернул моделей: {len(models)}. Недоступно: {unavailable}. Выберите точный ID в списке или введите его вручную.")

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

    def _set_key_visible(self, visible):
        self.key.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.key_visibility.setText("Скрыть" if visible else "Показать")
        self.key_visibility.setAccessibleName("Скрыть API-ключ" if visible else "Показать API-ключ")

    def _provider_changed(self):
        self._auth_notice = None
        self.key_visibility.setChecked(False)
        self.key.clear()
        self.remember.setChecked(False)
        self._last_provider = self.provider.currentData()
        self._provider_visibility()
        self._fill_models("")
        self._connection_changed()
        if self.provider.currentData() == "ai-horde":
            self.endpoint.setText(HORDE_URL)
            self.model.setEditText(HORDE_MODEL)
            self.auth_info.setText("Бесплатно, без регистрации. Ключ необязателен. Есть очередь; лучше описывать картинку по-английски. Анонимные изображения могут передаваться в открытый датасет.")
        elif self.provider.currentData() == "cloudflare-images":
            self.endpoint.clear()
            self._api_models = [{"id": CLOUDFLARE_MODEL, "owner": "Cloudflare"}]
            self._fill_models(CLOUDFLARE_MODEL)
            self.auth_info.setText("Бесплатная квота Workers AI — 10 000 neurons в сутки. Нужны Account ID и API-токен с правом Workers AI Read. На тарифе Free превышение квоты отклоняется.")
        self._check_runtime()

    def _provider_visibility(self):
        provider = self.provider.currentData()
        account = provider in self._oauth_providers
        has_key = bool(self.key.text().strip()) or self.remember.isChecked()
        self.endpoint.setEnabled(provider in ("lanmessenger-api", "lanmessenger-images", "cloudflare-images"))
        self.endpoint_label.setText("Account ID" if provider == "cloudflare-images" else "URL API")
        self.endpoint.setPlaceholderText("32 символа Account ID из панели Cloudflare" if provider == "cloudflare-images" else "https://server/v1 — для совместимого API")
        image_mode = provider in IMAGE_PROVIDERS
        self.thinking.setEnabled(not image_mode)
        self.response_style.setEnabled(not image_mode)
        self.instructions_btn.setEnabled(not image_mode)
        self.image_tool_panel.setVisible(not image_mode)
        self.access_label.setText("Картинки" if image_mode else "Полный доступ")
        self.access_label.setToolTip("Описание отправляется сервису генерации. Изображение сохраняется в истории ИИ." if image_mode else "Агент читает и меняет файлы и выполняет команды с правами текущего пользователя, в том числе за пределами рабочей папки.")
        self.input.setPlaceholderText("Опишите картинку. Enter — создать, Shift+Enter — новая строка" if image_mode else "Задача для агента. Enter — отправить, Shift+Enter — новая строка")
        self.key.setPlaceholderText("Необязательный ключ AI Horde; без ключа — анонимная очередь" if provider == "ai-horde" else "API-ключ; для входа по аккаунту оставьте пустым")
        self.key.setEnabled(bool(PROVIDERS[provider][1]))
        self.key_visibility.setEnabled(self.key.isEnabled())
        self.login_btn.setVisible(account)
        self.login_btn.setEnabled(account and not self._logging_in)
        self.logout_btn.setText("Выйти из аккаунта" if account else "Отключить API-ключ")
        self.logout_btn.setEnabled(not self._logging_in and (account or has_key))
        self.key_disconnect_btn.setVisible(account and bool(PROVIDERS[provider][1]))
        self.key_disconnect_btn.setEnabled(not self._logging_in and has_key)

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
        profile = next(p for p in self.store.profiles if p["id"] == self.store.active_profile)
        profile["models"] = [m for m in self._models if m.get("provider") == config["provider"]]
        profile["api_models"] = list(self._api_models)
        key = self.key.text().strip()
        self._profile_keys[self.store.active_profile] = key
        if self.remember.isChecked() and key:
            self.vault.save(config["provider"], key)
        else:
            self.vault.clear()
        self._remember_chat_selection()
        self.store.save()
        self.connection_status.setText(
            "Подключение и API-ключ сохранены средствами Windows."
            if self.remember.isChecked() and key else
            "Подключение сохранено. API-ключ — только до выхода из приложения." if key else
            "Подключение сохранено. API-ключ не задан.")
        return config

    def _send(self):
        text = self.input.toPlainText().strip()
        if self._can_followup():
            if self.send_btn.isEnabled() and self._followup_text_ready(text):
                self._run().followups[str(uuid.uuid4())] = text
                self._followup_added()
            return
        if (not text and not self._run().attachments) or not self.store or not self.current or self._task_busy() or self._pending or not self.send_btn.isEnabled():
            return
        text = text or "Опиши прикреплённые изображения."
        try:
            config = self._save_connection()
            self._run().config = dict(self.store.config)
            self._run().key = self.key.text().strip()
            self._run().profile_id = self.store.active_profile
            self._run().sent_images = list(self._run().attachments)
            self._run().prompt_images = [image_content(path) for path in self._run().sent_images]
            image_connections = self._image_connections()
            previous_connections = getattr(self._run(), "image_connections", None)
            if self.rpc.ready and previous_connections is not None and previous_connections != image_connections:
                self.rpc.shutdown()
            self._run().image_connections = image_connections
            if config["provider"] in IMAGE_PROVIDERS:
                if self._run().sent_images:
                    raise ValueError("Для чтения вложений выберите текстовую модель с поддержкой изображений.")
                self._send_image(text, config)
                return
            self._run().agent_depth = 0
            self._start_text_run(text)
        except (OSError, ValueError, RuntimeError) as exc:
            self._error(str(exc))
            self._check_runtime()

    def _start_text_run(self, text):
        run = self._run()
        config = run.config
        excluded = disabled_tools(self.current)
        if self.rpc.ready and run.disabled_tools != excluded:
            self.rpc.shutdown()
        run.disabled_tools = excluded
        if run.timeline is None:
            run.timeline = Timeline(self.current["messages"], self.rpc.redact)
        if self.rpc.ready:
            self._submit(text)
            return
        if not config["model"]:
            raise ValueError("Модель не выбрана. Нажмите шестерёнку рядом с моделью → выберите аккаунт → «Загрузить модели» → выберите доступную модель.")
        folder = self.current["folder"]
        if not Path(folder).is_dir():
            raise ValueError("Рабочая папка недоступна. Нажмите «Добавить папку…» и выберите существующий проект.")
        program, args, _module = rpc_command(config["runtime"])
        root = self.store.root if run.profile_id == "default" else self.store.root / "profiles" / run.profile_id
        env = prepare_environment(root, config, run.key)
        env["LANMESSENGER_IMAGE_ACCOUNTS"] = json.dumps(
            [{"id": p["id"], "name": p["name"], "model": p["config"]["model"], "default": p["default"]}
             for p in run.image_connections], ensure_ascii=False)
        env["LANMESSENGER_AGENT_DEPTH"] = str(run.agent_depth)
        env["LANMESSENGER_DISABLED_TOOLS"] = json.dumps(excluded)
        args += ["--mode", "rpc", "--provider", config["provider"], "--model", config["model"],
                 "--session", self.current["session"], "--no-extensions", "--no-prompt-templates"]
        if config["thinking"] != "auto":
            args += ["--thinking", config["thinking"]]
        instruction = config.get('instructions_' + config['response_style'], '').strip()
        if instruction:
            args += ['--append-system-prompt', instruction]
        self._pending = text
        if self._visible_run():
            self.status.setText("Запуск локального агента…")
            self.status.hide()
        self._controls(True)
        self._set_activity("Запуск локального агента…", True)
        self.rpc.start(program, args, folder, env)

    def _ready_changed(self, ready):
        if ready and self._pending:
            try:
                self.rpc.command({"type": "get_messages"})
            except RuntimeError as exc:
                self._error(str(exc))

    def _submit(self, text):
        self._begin_task(text)
        images = getattr(self._run(), "prompt_images", [])
        if images:
            self.rpc.send_prompt(text, images)
        else:
            self.rpc.send_prompt(text)

    def _begin_task(self, text, image=False, followup_id=None):
        self._stopped = False
        self._task_error = ""
        self._run().using_tools = False
        self._run().agent_active = False
        self.current.pop("last_error", None)
        if image:
            self.current.pop("image_queue", None)
        if self._visible_run():
            self.status.hide()
            self.error_settings_btn.hide()
        attachments = [] if followup_id else self._run().sent_images
        if self._run().agent_parent_request and not followup_id:
            self._run().agent_parent_request["start"] = len(self.current["messages"])
        self.current["messages"].append({"role": "user", "text": self.rpc.redact(text) if followup_id else text,
                                         **({"id": followup_id} if followup_id else {}),
                                         **({"images": list(attachments), "attachment_message": True} if attachments else {}),
                                         **({"image_message": True} if image else {})})
        self.timeline = Timeline(self.current["messages"], self.rpc.redact)
        if self.current["title"] == "Новый чат":
            self.current["title"] = text.replace("\n", " ")[:45]
            self._update_chat_item(self.current)
        self.current["running"] = True
        self.current["unread_result"] = False
        self.current["interrupted"] = False
        self.current["last_activity"] = time.time()
        self.current["provider"] = self._run().config.get("provider", self.provider.currentData())
        self.current["profile_id"] = self._run().profile_id or self.store.active_profile
        self.current["model"] = self._run().config.get("model", self.model.currentText())
        self._update_chat_item(self.current)
        self._fill_chats()
        self._save()
        if not followup_id:
            if self._visible_run() and self.input.toPlainText().strip() == text:
                self.input.clear()
            self._run().draft = ""
        self._run().attachments = [path for path in self._run().attachments if path not in attachments]
        if self._visible_run():
            self._render_attachments()
        self._render()

    def _send_image(self, text, config):
        if self.current.get("image_job"):
            raise ValueError("Предыдущее задание сохранено. Нажмите «Получить результат генерации», чтобы не создавать повторную картинку.")
        if not config["model"]:
            raise ValueError("Выберите модель картинки: шестерёнка рядом с моделью → «Загрузить модели».")
        entry = next((m for m in self._api_models if m["id"] == config["model"]), {})
        if entry.get("available") is False:
            reason = entry.get("unavailable_reason")
            if reason == "FIRST_TOP_UP_REQUIRED":
                raise ValueError("Provod требует первое пополнение для картинок. Бесплатный вариант: шестерёнка → отдельный аккаунт → «AI Horde — бесплатные картинки».")
            raise ValueError("Выбранная модель картинки недоступна для этого аккаунта. Обновите список моделей под шестерёнкой.")
        if config["provider"] == "lanmessenger-images" and not self.key.text().strip():
            raise ValueError("Укажите API-ключ под шестерёнкой рядом с моделью.")
        self.rpc.shutdown()
        self._begin_task(text, image=True)
        self.image_resume_btn.hide()
        self._controls(True)
        self.images.generate(config["provider"], config["endpoint"], config["model"], self.key.text(), text,
                             self.store.root / "images" / self.current["id"])

    def _image_queued(self, job):
        self.current["image_job"] = job
        self._save()

    def _image_cancelled(self, job):
        if (self.current and not self._closed and (self.current.get("image_job") == job
                or (self.current.get("outcome") == "stopped" and not self.current.get("running")))):
            self._update_image_activity("cancelled")
        if self.current and self.current.get("image_job") == job and not self._closed:
            self.current.pop("image_job", None)
            self._save()
            if self._visible_run():
                self.image_resume_btn.hide()

    def _image_queue_updated(self, status):
        if self._closed or not self.current:
            return
        self.current["image_queue"] = status
        queue = []
        if type(status.get("queue_position")) is int and status["queue_position"] > 0:
            queue.append(f"место {status['queue_position']}")
        if type(status.get("estimated_wait_seconds")) is int and status["estimated_wait_seconds"] >= 0:
            queue.append(f"оценка сервиса ~{status['estimated_wait_seconds']} с")
        self._update_image_activity(status.get("state", "queued"), " · ".join(queue))
        self._save()
        request = getattr(self._run(), "parent_request", None)
        if request:
            self._dispatch_run(request["run"], self._report_image_queue, request["id"])

    def _update_image_activity(self, state, detail=""):
        child = self.current
        parent_id = child.get("parent_chat_id") if child else None
        if not parent_id:
            return
        parent = next((chat for chat in self.store.chats if chat["id"] == parent_id), None)
        if not parent:
            return
        card_id = "image-activity:" + child["id"]
        message = next((row for row in parent["messages"] if row.get("id") == card_id), None)
        if message is None:
            message = {"id": card_id, "role": "image_activity", "job_chat_id": child["id"]}
            parent["messages"].append(message)
        model = child.get("model", "Изображение")
        message.update(model=model, status=state, text=model + (" · " + self.rpc.redact(detail) if detail else ""))
        self._save()
        if self._selected_run.chat is parent:
            self._dispatch_run(self._selected_run, self._render)

    def _image_snapshot(self, chat):
        run = self._runs.get(chat["id"])
        state = (chat.get("image_queue", {}).get("state", "submitting") if chat.get("running") else
                 "interrupted" if chat.get("image_job") else chat.get("outcome", "idle"))
        result = {"chat_id": chat["id"], "model": chat.get("model"), "state": state,
                  "parent_chat_id": chat.get("parent_chat_id")}
        queue = chat.get("image_queue")
        if queue:
            result["queue"] = dict(queue, age_seconds=max(0, round(time.time() - queue["checked_at"])),
                                   source="live" if run and run.images.busy else "saved")
        if state == "completed":
            message = next((m for m in reversed(chat["messages"]) if m.get("images") and m.get("image_message")), {})
            result["paths"] = message.get("images", [])
            result["images"] = [image_content(path) for path in result["paths"] if Path(path).is_file()]
        if chat.get("last_error"):
            result["error"] = self.rpc.redact(chat["last_error"])
        if run:
            result["cancellation_pending"] = run.images.cancellation_pending
        return result

    def _report_image_queue(self, request_id):
        batch = self._run().image_tool_requests.get(request_id)
        if not batch or batch["reported"] or batch["wait_for_result"] or not batch["pending"]:
            return
        if any(child.chat["id"] in batch["pending"] and not child.chat.get("image_queue") for child in batch["children"]):
            return
        results = [self._image_snapshot(child.chat) for child in batch["children"]]
        self.rpc.command({"type": "extension_ui_response", "id": request_id,
                          "value": json.dumps({"results": results}, ensure_ascii=False)})
        batch["reported"] = True

    def _image_status_request(self, event):
        try:
            params = json.loads(event.get("placeholder", "{}"))
            ids = params.get("chat_ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or any(not isinstance(value, str) for value in ids):
                raise ValueError("Укажите от одного до четырёх ID чатов генерации.")
            chats = [next((chat for chat in self.store.chats if chat["id"] == chat_id), None) for chat_id in ids]
            if any(chat is None or chat.get("provider") not in IMAGE_PROVIDERS for chat in chats):
                raise ValueError("Чат генерации не найден.")
            cancel = event["title"] == "lanmessenger.cancel_image_generation"
            delay = params.get("wait_seconds", 5) if not cancel else 0
            if type(delay) is not int or not 0 <= delay <= 30:
                raise ValueError("Ожидание проверки — от 0 до 30 секунд.")
            if cancel:
                for chat in chats:
                    if chat.get("parent_chat_id") != self.current["id"]:
                        raise ValueError("Можно отменять только генерации этого исходного чата.")
                for chat in chats:
                    run = self._runs.get(chat["id"])
                    if run and run.images.busy:
                        self._dispatch_run(run, self._stop)
                    elif chat.get("image_job"):
                        run = run or self._make_run(chat)
                        self._runs[chat["id"]] = run
                        run.images.cancel(job=chat["image_job"])
                        chat.update(running=False, interrupted=False, outcome="stopped")
                self._save()
            run = self._run()

            def respond():
                run.image_status_requests.pop(event["id"], None)
                if not self._closed and not run.stopped:
                    try:
                        result = {"results": [self._image_snapshot(chat) for chat in chats]}
                    except (OSError, ValueError, TypeError) as exc:
                        result = {"error": str(exc)}
                    try:
                        run.rpc.command({"type": "extension_ui_response", "id": event["id"],
                                         "value": json.dumps(result, ensure_ascii=False)})
                    except RuntimeError:
                        pass
                timer.deleteLater()

            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._dispatch_run(run, respond))
            run.image_status_requests[event["id"]] = timer
            timer.start(delay * 1000 if any(chat.get("running") for chat in chats) else 0)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.rpc.command({"type": "extension_ui_response", "id": event["id"],
                              "value": json.dumps({"error": str(exc)}, ensure_ascii=False)})

    def _image_connections(self):
        connections = []
        config = self._run().config or self.store.config
        default_id = config.get("image_account", "")
        if not default_id:
            default_id = next((p["id"] for p in self.store.profiles if p["config"]["provider"] == "ai-horde"), "")
        for profile in self.store.profiles:
            if profile["config"]["provider"] not in IMAGE_PROVIDERS:
                continue
            profile_id = profile["id"]
            root = self.store.root if profile_id == "default" else self.store.root / "profiles" / profile_id
            credential = KeyVault(root).load()
            key = self._profile_keys.get(profile_id, "")
            if credential.get("provider") == profile["config"]["provider"] and credential.get("key"):
                key = credential["key"]
            connections.append({"id": profile_id, "name": profile["name"], "config": dict(profile["config"]),
                                "models": list(profile.get("api_models", [])), "key": key, "default": profile_id == default_id})
            if profile_id == default_id and config.get("image_model", "").strip():
                connections[-1]["config"]["model"] = config["image_model"].strip()
        return connections

    def _image_connection(self, account=None):
        connections = getattr(self._run(), "image_connections", [])
        matches = ([p for p in connections if p["id"] == account or p["name"] == account]
                   if account else [p for p in connections if p["default"]])
        if len(matches) != 1:
            raise ValueError("Аккаунт инструмента image gen недоступен или название неоднозначно. Нажмите шестерёнку → выберите текстовый аккаунт → «image gen — аккаунт» и «image gen — модель». Проверьте провайдер, URL и ключ в аккаунте картинок.")
        return matches[0]

    def _image_models_request(self, event):
        try:
            if self._stopped:
                raise ValueError("Агент остановлен; каталог не запрошен.")
            params = json.loads(event.get("placeholder", "{}"))
            if not isinstance(params, dict):
                raise ValueError("Некорректный запрос каталога картинок.")
            query, limit, offset = params.get("query", ""), params.get("limit", 100), params.get("offset", 0)
            if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
                raise ValueError("Укажите query до 200 символов, limit от 1 до 200 и неотрицательный offset.")
            connection = self._image_connection(params.get("account"))
            catalog = APIModelCatalog(self)
            self._run().image_catalog_requests[event["id"]] = {"catalog": catalog, "connection": connection,
                                                              "query": query, "limit": limit, "offset": offset}
            run = self._run()
            catalog.loaded.connect(lambda models: self._dispatch_run(run, self._image_models_result, event["id"], models))
            catalog.failed.connect(lambda error: self._dispatch_run(run, self._image_models_result, event["id"], [], error))
            provider = connection["config"]["provider"]
            if provider == "cloudflare-images":
                self._image_models_result(event["id"], [{"id": CLOUDFLARE_MODEL, "owner": "Cloudflare"}], source="supported")
            elif provider == "ai-horde":
                catalog.fetch(HORDE_URL + "/status", "", catalog_type="horde")
            else:
                catalog.fetch(connection["config"]["endpoint"], connection["key"], catalog_type="images")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if event["id"] in self._run().image_catalog_requests:
                self._image_models_result(event["id"], [], str(exc))
            else:
                self.rpc.command({"type": "extension_ui_response", "id": event["id"],
                                  "value": json.dumps({"error": str(exc)}, ensure_ascii=False)})

    def _image_models_result(self, request_id, models, error="", source="live"):
        request = self._run().image_catalog_requests.pop(request_id, None)
        if not request:
            return
        request["catalog"].cancel()
        request["catalog"].deleteLater()
        if self._closed or self._stopped:
            return
        connection = request["connection"]
        if source == "live" and not error:
            connection["models"] = list(models)
        if error:
            models = connection["models"]
            source = "saved"
        filtered = [model for model in models if request["query"].casefold() in model["id"].casefold()]
        result = {"account": {"id": connection["id"], "name": connection["name"], "provider": connection["config"]["provider"]},
                  "source": source, "total": len(filtered), "offset": request["offset"],
                  "models": filtered[request["offset"]:request["offset"] + request["limit"]]}
        if error:
            result["warning"] = error + " Показан сохранённый список; он может быть неполным и устаревшим."
            if not models:
                result["error"] = error
        if source == "supported":
            result["warning"] = "Модели, поддерживаемые приложением. Доступ и квота проверяются при генерации."
        try:
            self.rpc.command({"type": "extension_ui_response", "id": request_id, "value": json.dumps(result, ensure_ascii=False)})
        except RuntimeError:
            pass

    def _cancel_image_catalogs(self):
        for timer in self._run().image_status_requests.values():
            timer.stop()
            timer.deleteLater()
        self._run().image_status_requests = {}
        requests, self._run().image_catalog_requests = self._run().image_catalog_requests, {}
        for request in requests.values():
            request["catalog"].cancel()
            request["catalog"].deleteLater()

    def _agent_image_request(self, event):
        try:
            if self._stopped:
                raise ValueError("Агент остановлен; генерация не отправлена.")
            if self.images.busy or self.current.get("image_job"):
                raise ValueError("Предыдущее задание картинки ещё не завершено. Получите его результат; повторный запрос не отправлен.")
            params = json.loads(event.get("placeholder", "{}"))
            if not isinstance(params, dict) or not isinstance(params.get("prompt"), str) or not 1 <= len(params["prompt"].strip()) <= 10000:
                raise ValueError("Нужно описание картинки от 1 до 10 000 символов.")
            connection = self._image_connection(params.get("account"))
            config = connection["config"]
            if "models" in params and "model" in params:
                raise ValueError("Укажите либо model, либо список models.")
            models = params.get("models", [params.get("model") or config["model"]])
            if not isinstance(models, list) or not 1 <= len(models) <= 4 or any(not isinstance(m, str) or not m.strip() for m in models):
                raise ValueError("Укажите от одной до четырёх моделей картинок в models.")
            models = [model.strip() for model in models]
            if len(set(models)) != len(models):
                raise ValueError("В models должны быть разные модели без повторов.")
            for model in models:
                entry = next((m for m in connection["models"] if m["id"] == model), {})
                if entry.get("available") is False:
                    raise ValueError("Модель " + model + " недоступна для выбранного аккаунта. Проверьте доступ под шестерёнкой.")
            parent = self._run()
            outstanding = [child for batch in parent.image_tool_requests.values() for child in batch["children"]
                           if child.parent_request]
            if len(outstanding) + len(models) > 4:
                raise ValueError("Уже запущены генерации: одновременно доступны до четырёх картинок в одном чате.")
            if any(child.profile_id == connection["id"] and child.config["model"] in models for child in outstanding):
                raise ValueError("Эта модель уже рисует в данном чате. Повторный запрос не отправлен.")
            batch = {"children": [], "results": {}, "pending": set(), "reported": False,
                     "wait_for_result": params.get("wait_for_result", False)}
            parent.image_tool_requests[event["id"]] = batch
            for model in models:
                active_chat = self.store.active_chat
                chat = self.store.new_chat(self.current["folder"])
                self.store.active_chat = active_chat
                chat.update(parent_chat_id=self.current["id"],
                            ai_selection={"profile_id": connection["id"], "model": model})
                child = self._make_run(chat)
                child.config = {**config, "model": model}
                child.profile_id = connection["id"]
                child.key = connection["key"]
                child.parent_request = {"run": parent, "id": event["id"], "model": model, "child_id": chat["id"]}
                self._runs[chat["id"]] = child
                batch["children"].append(child)
                batch["pending"].add(chat["id"])
                self._dispatch_run(child, self._begin_task, params["prompt"], True)
                chat["title"] = "Картинка · " + model + ": " + params["prompt"].replace("\n", " ")[:35]
                self._dispatch_run(child, self._update_image_activity, "submitting")
            self._fill_chats()
            for child in batch["children"]:
                try:
                    if config["provider"] != "ai-horde":
                        self._dispatch_run(child, self._update_image_activity, "processing")
                    self._dispatch_run(child, child.images.generate, config["provider"], config["endpoint"], child.config["model"],
                                       connection["key"], params["prompt"], self.store.root / "images" / child.chat["id"])
                except (OSError, ValueError) as exc:
                    self._dispatch_run(child, self._image_failed, str(exc))
            if batch["pending"]:
                self._set_activity("Ожидает картинки: " + ", ".join(models) + "…", True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.rpc.command({"type": "extension_ui_response", "id": event["id"],
                              "value": json.dumps({"error": str(exc)}, ensure_ascii=False)})

    def _image_failed(self, text):
        request = getattr(self._run(), "parent_request", None)
        self._update_image_activity("failed", text)
        self._error(text)
        if request:
            self._run().parent_request = None
            self._dispatch_run(request["run"], self._relay_image_result, request, [], text)

    def _relay_image_result(self, request, paths, error=""):
        batch = self._run().image_tool_requests.get(request["id"])
        if self._closed or not batch or request["child_id"] not in batch["pending"]:
            return
        if error:
            result = {"model": request["model"], "error": error}
            self.current["messages"].append({"role": "system", "text": request["model"] + ": " + error})
            self._save()
            self._render()
        else:
            try:
                pictures = [image_content(path) for path in paths]
            except (OSError, ValueError):
                pictures = []
            result = {"paths": paths, "model": request["model"], "images": pictures}
        batch["results"][request["child_id"]] = result
        batch["pending"].remove(request["child_id"])
        if batch["pending"]:
            self._set_activity(f"Картинки: завершено {len(batch['results'])} из {len(batch['children'])}. Ожидает остальные…", True)
            self._report_image_queue(request["id"])
            return
        del self._run().image_tool_requests[request["id"]]
        if batch["reported"]:
            return
        results = [batch["results"][child.chat["id"]] for child in batch["children"]]
        try:
            self.rpc.command({"type": "extension_ui_response", "id": request["id"],
                              "value": json.dumps({"results": results}, ensure_ascii=False)})
            self._set_activity("Передаёт результат генерации агенту…", True)
        except RuntimeError:
            self._error("Результат картинки сохранён в дочернем чате; текстовый агент уже завершился.")

    def _image_completed(self, paths):
        if self._closed or not self.current:
            return
        self.current["messages"].append({"id": str(uuid.uuid4()), "role": "assistant", "text": "Изображение готово.",
                                         "images": paths, "image_message": True, "status": "completed"})
        self.current.pop("image_job", None)
        self.current.pop("last_error", None)
        parent_id = self.current.get("parent_chat_id")
        parent_chat = next((c for c in self.store.chats if c["id"] == parent_id), None)
        if parent_chat:
            self._update_image_activity("completed")
            parent_chat["messages"].append({"id": str(uuid.uuid4()), "role": "assistant", "text": "Изображение · " + self.current["model"] + ". Из чата «" + self.current["title"] + "».",
                                             "images": paths, "image_message": True, "status": "completed",
                                             "source_chat_id": self.current["id"]})
            self._mark_chat_result(parent_chat, "completed")
        self.current.update(running=False, interrupted=False, outcome="completed")
        self._mark_chat_result(self.current, "completed")
        self._set_activity("", False)
        if self._visible_run():
            self.status.hide()
            self.error_settings_btn.hide()
            self.image_resume_btn.hide()
        self._controls(False)
        self._save()
        self._render()
        request = getattr(self._run(), "parent_request", None)
        if request:
            self._run().parent_request = None
            self._dispatch_run(request["run"], self._relay_image_result, request, paths)
        if parent_chat is self._selected_run.chat and parent_chat:
            self._dispatch_run(self._selected_run, self._render)

    def _resume_image(self):
        if not self.current or not self.current.get("image_job") or self._task_busy():
            return
        try:
            self.current.update(running=True, interrupted=False)
            self._update_image_activity("submitting", "Проверяет сохранённое задание")
            self.timeline = Timeline(self.current["messages"], self.rpc.redact)
            self.image_resume_btn.hide()
            self.status.hide()
            self._controls(True)
            self.images.resume(self.current["image_job"], self.store.root / "images" / self.current["id"])
            self._save()
        except (OSError, ValueError, KeyError) as exc:
            self._error(str(exc))

    def _controls(self, active):
        if not self._visible_run():
            return
        active = active or self.images.busy or bool(self._run().redirect)
        for widget in (self.account, self.account_add, self.account_rename, self.model, self.settings_model,
                       self.catalog_btn, self.folder_label, self.project_btn, self.workspace_btn):
            widget.setEnabled(not active and not self._logging_in and not self._installing)
        self.response_style.setEnabled(not active and not self._logging_in and not self._installing and self.provider.currentData() not in IMAGE_PROVIDERS)
        self.instructions_btn.setEnabled(self.response_style.isEnabled())
        show_followups = self.rpc.ready and (self.rpc.busy or bool(self._run().redirect)) and not self.images.busy
        can_followup = self._can_followup()
        self.send_btn.setEnabled((not active or can_followup) and not self._logging_in and not self._installing and bool(self.current) and Path(self.work_dir).is_dir())
        self.send_btn.setAccessibleName("В очередь" if show_followups else "Отправить сообщение")
        self.send_btn.setToolTip(self.queue_btn.toolTip() if show_followups else "Отправить сообщение")
        self.input.setPlaceholderText("Новое сообщение. Enter — в очередь" if show_followups else
                                      "Задача для агента. Enter — отправить, Shift+Enter — новая строка")
        self.followup_actions.setVisible(show_followups)
        self.queue_btn.setEnabled(can_followup and self.send_btn.isEnabled())
        self.clarify_btn.setEnabled(can_followup and self.send_btn.isEnabled() and self._run().agent_active)
        self.stop_btn.setEnabled(active)
        self.stop_btn.setVisible(active)
        self.send_btn.setVisible(not active or show_followups)
        if not active:
            self.output.set_activity("", False)
        self.new_btn.setEnabled(not self._logging_in and not self._installing)
        self.chat_list.setEnabled(not self._logging_in and not self._installing)
        self.connection_panel.setEnabled(not active and not self._installing)
        self.image_tool_panel.setEnabled(not active and not self._logging_in and not self._installing)

    def _busy_changed(self, busy):
        self._controls(busy or bool(self._pending))
        if busy:
            if self._visible_run():
                self.status.setText("Агент выполняет задачу…")
                self.status.hide()
            self._set_activity("Ожидает ответа модели…", True)
        elif self.current:
            # Only agent_end confirms successful completion; process errors mark interruption.
            self._save_timer.start(100)

    def _can_followup(self):
        return (self.rpc.ready and self.rpc.busy and not self._stopped and not self._pending and
                not self.images.busy and not self._run().redirect)

    def _followup_text_ready(self, text):
        if self._run().attachments:
            self.status.setText("Во время задачи можно отправлять только текст. Вложения отправьте после завершения.")
            self.status.show()
            return False
        if not text:
            self.input.setFocus()
        return bool(text)

    def _clarify(self):
        text = self.input.toPlainText().strip()
        if not self._can_followup() or not self.clarify_btn.isEnabled() or not self._followup_text_ready(text):
            return
        run = self._run()
        request_id = str(uuid.uuid4())
        try:
            if run.using_tools or run.steering:
                self.rpc.steer(request_id, text)
                run.steering[request_id] = text
            else:
                self.rpc.abort(request_id)
                run.redirect = (request_id, text)
                self._controls(True)
                self._set_activity("Прерывает ответ для уточнения…", True)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            self.status.show()
            return
        self._followup_added()

    def _followup_added(self):
        self.input.clear()
        self.input.setFocus()
        self._run().draft = ""
        self._render()

    def _start_followup(self, request_id, text):
        self._begin_task(text, followup_id=request_id)
        try:
            self.rpc.send_prompt(text)
        except RuntimeError as exc:
            self._error(str(exc))

    def _take_followups(self):
        run = self._run()
        pending = list(run.steering.values()) + list(run.followups.values())
        if run.redirect:
            pending.insert(0, run.redirect[1])
        run.steering.clear()
        run.followups.clear()
        run.redirect = None
        return pending

    def _steering_not_delivered(self, pending):
        if not pending:
            return
        self.current["messages"].extend({"role": "system", "text": "Дополнение не доставлено: " + self.rpc.redact(text)}
                                        for text in pending)
        self._save()
        self._render()

    def _stop(self):
        self._stopped = True
        self._controls(self._task_busy() or bool(self._pending))
        self._cancel_agent_requests()
        self._cancel_image_catalogs()
        batches = self._run().image_tool_requests
        self._run().image_tool_requests = {}
        for batch in batches.values():
            for child in batch["children"]:
                if child.parent_request:
                    child.parent_request = None
                    self._dispatch_run(child, self._stop)
        if self.images.busy:
            self.images.cancel()
            self.current.pop("image_job", None)
            self.current.update(running=False, interrupted=False, outcome="stopped")
            self._update_image_activity("stopped")
            self.current["unread_result"] = False
            self._set_activity("", False)
            self._update_chat_item(self.current)
            self.current["messages"].append({"role": "system", "text": "Ожидание картинки остановлено. Запрос на отмену отправлен сервису; уже начатая работа может завершиться."})
            self._controls(False)
            self._save()
            self._render()
            request = getattr(self._run(), "parent_request", None)
            if request:
                self._run().parent_request = None
                self._dispatch_run(request["run"], self._relay_image_result, request, [], "Генерация остановлена пользователем.")
            return
        if self._pending:
            self._pending = None
            self.rpc.shutdown()
            self._controls(False)
            self.status.setText("Запуск отменён; запрос не отправлен.")
            if self.current:
                self.current.update(running=False, interrupted=False, outcome="stopped")
                self._update_chat_item(self.current)
                self._save()
            self._finish_agent_request("stopped")
        else:
            self.rpc.abort()
            self.status.setText("Остановка агента…")
            self._set_activity("Останавливает задачу…", True)
            self.stop_btn.setEnabled(False)

    def _error(self, text):
        if self._closed:
            return
        self._steering_not_delivered(self._take_followups())
        self._cancel_agent_requests()
        self._cancel_image_catalogs()
        batches = self._run().image_tool_requests
        self._run().image_tool_requests = {}
        for batch in batches.values():
            for child in batch["children"]:
                if child.parent_request:
                    child.parent_request = None
                    child.images.cancel(remote=False)
                    child.chat.update(running=False, interrupted=True)
                    self._dispatch_run(child, self._update_image_activity, "interrupted")
                    child.activity = ("", False)
                    self._update_chat_item(child.chat)
        if self._pending and not self._run().draft:
            self._run().draft = self._pending
        self._pending = None
        self._task_error = self._error_guidance(text)
        self._set_activity("", False)
        if self.current:
            self.current["last_error"] = self._task_error
        if self.current and self.current.get("running"):
            self.timeline.finish("interrupted")
            self.current["running"] = False
            self.current["interrupted"] = True
            self.current["outcome"] = "failed"
            self._mark_chat_result(self.current, "failed")
            self.current["messages"].append({"role": "system", "text": self._task_error})
            self._save()
            self._render()
        self._controls(False)
        if self._visible_run():
            self.status.setText(self._task_error)
            self.status.show()
            self.error_settings_btn.show()
            self.image_resume_btn.setVisible(bool(self.current and self.current.get("image_job")))
        if self.current:
            self._update_chat_item(self.current)
        self._save()
        self._finish_agent_request("failed")

    def _error_guidance(self, text):
        text = self.rpc.redact(str(text))
        key = self._run().key if self._event_run else self.key.text().strip()
        if key:
            text = text.replace(key, "<ключ скрыт>")
        lower = text.lower()
        if "шестерёнку" in lower or "рабочая папка" in lower or lower.startswith("внутренняя ошибка обработки задачи"):
            return text
        prefix = "Нажмите шестерёнку рядом с моделью → выберите нужный аккаунт. "
        if any(token in lower for token in ("401", "unauthorized", "invalid api key", "authentication", "no api key", "api key not found")):
            tip = "Проверьте API-ключ или нажмите «Войти в аккаунт», затем «Загрузить модели» и выберите модель."
        elif any(token in lower for token in ("403", "forbidden", "permission denied", "access denied")):
            tip = "Проверьте доступ аккаунта к модели, нажмите «Загрузить модели» и выберите доступный ID. При необходимости войдите заново."
        elif any(token in lower for token in ("model", "модель", "404", "not found")):
            tip = "Нажмите «Загрузить модели» и выберите доступную модель. При ручном вводе проверьте точный ID. Для совместимого API проверьте также URL."
        elif any(token in lower for token in ("fetch", "connect", "network", "timeout", "enotfound", "econn", "url", "сети")):
            tip = "Проверьте URL API, подключение к сети и доступность сервера, затем повторите запрос."
        elif any(token in lower for token in ("429", "quota", "billing", "rate limit")):
            tip = "Проверьте лимиты и баланс провайдера или выберите другой аккаунт либо модель, затем повторите запрос."
        else:
            tip = "Проверьте подключение и модель. Если Pi не запускается, проверьте компоненты ИИ и путь Runtime в настройках."
        return f"{prefix}{tip}\nПричина: {text}"

    def _event(self, event):
        if not self.current:
            return
        kind = event.get("type")
        run = self._run()
        if kind in ("agent_start", "agent_end"):
            run.agent_active = kind == "agent_start"
            if run.agent_active:
                self._controls(True)
        if kind == "response" and event.get("command") == "abort" and run.redirect and event.get("id") == run.redirect[0]:
            if event.get("success"):
                if self._stopped:
                    self._finish_text_run("stopped")
                else:
                    request_id, text = run.redirect
                    run.redirect = None
                    self._start_followup(request_id, text)
            return
        if kind == "response" and event.get("command") == "steer":
            if not event.get("success"):
                text = self._run().steering.pop(event.get("id"), None)
                if text is not None:
                    self._steering_not_delivered([text])
            return
        if kind == "message_start" and event.get("message", {}).get("role") == "user":
            text = message_text(event["message"])
            pending = self._run().steering
            request_id = next((key for key, value in pending.items() if text == value), None)
            if request_id:
                pending.pop(request_id)
                self.current["messages"].append({"id": request_id, "role": "user", "text": self.rpc.redact(text)})
                self._save()
                self._render()
        if kind == "extension_ui_request" and event.get("method") == "input" and event.get("title") in ("lanmessenger.ask_agent", "lanmessenger.list_agent_models"):
            self._agent_request(event)
            return
        if kind == "extension_ui_request" and event.get("method") == "input" and event.get("title") == "lanmessenger.list_image_models":
            self._image_models_request(event)
            return
        if kind == "extension_ui_request" and event.get("method") == "input" and event.get("title") in ("lanmessenger.get_image_status", "lanmessenger.cancel_image_generation"):
            self._image_status_request(event)
            return
        if kind == "extension_ui_request" and event.get("method") == "input" and event.get("title") in ("lanmessenger.list_chats", "lanmessenger.read_chat"):
            self._read_chat_request(event)
            return
        if kind == "extension_ui_request" and event.get("method") == "input" and event.get("title") == "lanmessenger.generate_image":
            self._agent_image_request(event)
            return
        messages = self.current["messages"]
        if not self._stopped:
            if kind == "tool_execution_start":
                names = {"read": "Читает файл", "write": "Записывает файл", "edit": "Изменяет файл",
                         "bash": "Выполняет команду", "grep": "Ищет в файлах", "find": "Ищет файлы", "ls": "Просматривает папку"}
                args = event.get("args", {})
                detail = str(args.get("path") or args.get("command") or args.get("pattern") or "")
                activity = names.get(event.get("toolName"), "Выполняет инструмент")
                self._set_activity(self.rpc.redact(activity + (": " + detail[:120] if detail else "…")), True)
            elif kind == "tool_execution_end":
                self._set_activity("Обрабатывает результат действия…", True)
            elif kind == "message_update":
                delta = event.get("assistantMessageEvent", {}).get("type", "")
                if delta.startswith("thinking_"):
                    self._set_activity("Размышляет…", True)
                elif delta.startswith("text_"):
                    self._set_activity("Формирует ответ…", True)
        if kind == "response" and event.get("command") == "get_state" and event.get("success"):
            effective = event.get("data", {}).get("thinkingLevel")
            if effective in THINKING_LEVELS:
                requested = self.thinking.currentData()
                text = f"Уровень Pi: {THINKING_LEVELS[effective]}."
                if requested != "auto" and requested != effective:
                    text += f" Запрошен: {THINKING_LEVELS[requested]}; Pi ограничил уровень возможностями модели."
                elif self.provider.currentData() == "lanmessenger-api" and requested != "auto":
                    text += " Поддержку параметра reasoning_effort проверяет ваш сервер API."
                if self._visible_run():
                    self.thinking_status.setText(text)
        if kind == "response" and event.get("command") == "get_messages" and event.get("success"):
            restored = restore_messages(event.get("data", {}).get("messages", []), self.rpc.redact)
            attachments = [m for m in messages if m.get("attachment_message")]
            for message in restored:
                original = next((m for m in attachments if m.get("role") == message.get("role")
                                 and m.get("text") == message.get("text")), None)
                if original:
                    message.update(images=original["images"], attachment_message=True)
                    attachments.remove(original)
            lost_context = bool(messages) and not restored
            archived = self.current.get("archived_messages", [])
            archived_ids = {m.get("id") for m in archived}
            local_images = [m for m in messages if (m.get("image_message") or m.get("role") == "image_activity")
                            and m.get("id") not in archived_ids]
            if restored and local_images:
                archived = list(archived) + local_images
                self.current["archived_messages"] = archived
            if lost_context:
                archived = list(messages)
                if not archived[-1].get("context_boundary"):
                    archived.append({"role": "system", "context_boundary": True,
                                     "text": "Сессия Pi пуста или потеряна. Переписка выше сохранена; агент начинает новый контекст."})
                self.current["archived_messages"] = archived
            self.current["messages"] = list(archived) + restored
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
        if kind == "tool_execution_start":
            run.using_tools = True
        if kind == "message_start" and event.get("message", {}).get("role") == "assistant":
            run.using_tools = False
        if kind == "message_end" and event.get("message", {}).get("role") == "assistant":
            message = event["message"]
            reason = message.get("stopReason")
            run.using_tools = reason == "toolUse"
            if reason == "error" or (reason == "aborted" and not self._stopped and not run.redirect):
                self._task_error = self.rpc.redact(message.get("errorMessage") or "Провайдер прервал выполнение задачи")
        self.timeline.event(event)
        if kind == "agent_end":
            if run.redirect:
                self.timeline.finish("stopped")
                self._save()
            else:
                outcome = "stopped" if self._stopped else "failed" if self._task_error else "completed"
                self._finish_text_run(outcome)
                if outcome == "completed" and run.followups:
                    request_id = next(iter(run.followups))
                    self._start_followup(request_id, run.followups.pop(request_id))
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

    def _finish_text_run(self, outcome):
        if outcome == "completed":
            pending = list(self._run().steering.values())
            self._run().steering.clear()
        else:
            pending = self._take_followups()
        self._steering_not_delivered(pending)
        self.timeline.finish(outcome)
        self.current.update(running=False, interrupted=outcome == "failed", outcome=outcome)
        self._mark_chat_result(self.current, outcome)
        if outcome == "failed":
            guidance = self._error_guidance(self._task_error)
            self.current["last_error"] = guidance
            self.current["messages"].append({"role": "system", "text": guidance})
            if self._visible_run():
                self.status.setText(guidance)
        elif self._visible_run():
            self.status.setText("Агент остановлен." if outcome == "stopped" else "Задача завершена.")
        if self._visible_run():
            self.status.setVisible(outcome == "failed")
            self.error_settings_btn.setVisible(outcome == "failed")
        self._set_activity("", False)
        self._update_chat_item(self.current)
        self._controls(False)
        self._save()
        self._finish_agent_request(outcome)

    def _cancel_agent_requests(self):
        requests = self._run().agent_requests
        self._run().agent_requests = {}
        for child in requests.values():
            request, child.agent_parent_request = child.agent_parent_request, None
            if request:
                request["timer"].stop()
                request["timer"].deleteLater()
                self._dispatch_run(child, self._stop)

    def _agent_request(self, event):
        try:
            if self._stopped:
                raise ValueError("Исходный агент остановлен.")
            if event["title"] == "lanmessenger.list_agent_models":
                accounts = []
                for profile in self.store.profiles:
                    if profile["config"]["provider"] in IMAGE_PROVIDERS:
                        continue
                    models = [profile["config"].get("model", "")]
                    models += [model["id"] for model in profile.get("api_models", [])]
                    models += [model["id"] for model in profile.get("models", [])
                               if model.get("provider") == profile["config"]["provider"]]
                    accounts.append({"id": profile["id"], "name": profile["name"],
                                     "provider": profile["config"]["provider"],
                                     "models": list(dict.fromkeys(model for model in models if model))[:200],
                                     "catalog_source": "saved"})
                result = {"accounts": accounts}
            else:
                params = json.loads(event.get("placeholder", "{}"))
                question = params.get("question")
                if not isinstance(question, str) or not 1 <= len(question.strip()) <= 10000:
                    raise ValueError("Вопрос агенту должен содержать от 1 до 10 000 символов.")
                parent = self._run()
                if parent.agent_depth >= 2 or len(parent.agent_requests) >= 3:
                    raise ValueError("Доступно до трёх вопросов параллельно и до двух уровней вложенности агентов.")
                timeout = params.get("timeout_seconds", 300)
                if type(timeout) is not int or not 5 <= timeout <= 900:
                    raise ValueError("Ожидание ответа агента — от 5 до 900 секунд.")
                chat = None
                if params.get("chat_id"):
                    if params.get("account") or params.get("model"):
                        raise ValueError("Для существующего чата модель и аккаунт не переопределяются.")
                    chat = next((chat for chat in self.store.chats if chat["id"] == params["chat_id"]), None)
                    if chat is None:
                        raise ValueError("ИИ-чат не найден. Найдите его через list_chats.")
                    existing = self._runs.get(chat["id"])
                    if chat is self.current or chat.get("running") or (existing and (existing.pending or existing.rpc.busy or existing.images.busy or existing.agent_parent_request)):
                        raise ValueError("Чат занят или ожидает этот запрос. Дождитесь завершения либо выберите новый чат.")
                    if existing and (existing.draft.strip() or existing.attachments or
                                     (existing is self._selected_run and self.input.toPlainText().strip())):
                        raise ValueError("В чате есть неотправленный черновик. Выберите новый чат, чтобы его сохранить.")
                    selection = chat.get("ai_selection", {})
                    account = selection.get("profile_id") or chat.get("profile_id")
                    model = selection.get("model") or chat.get("model")
                else:
                    account = params.get("account") or parent.profile_id or self.store.active_profile
                    model = params.get("model")
                profiles = [profile for profile in self.store.profiles if account in (profile["id"], profile["name"])]
                if len(profiles) != 1:
                    raise ValueError("Аккаунт не найден или имя неоднозначно. Используйте ID из list_agent_models.")
                profile = profiles[0]
                config = dict(profile["config"])
                config["model"] = model or config["model"]
                if config["provider"] in IMAGE_PROVIDERS:
                    raise ValueError("Для вопроса нужна текстовая модель. Для картинки используйте generate_image.")
                if not config["model"]:
                    raise ValueError("В выбранном аккаунте не настроена модель. Откройте шестерёнку рядом с моделью.")
                key = self._profile_keys.get(profile["id"], "")
                root = self.store.root if profile["id"] == "default" else self.store.root / "profiles" / profile["id"]
                credential = KeyVault(root).load()
                if credential.get("provider") == config["provider"] and credential.get("key"):
                    key = credential["key"]
                if chat is None:
                    active_chat = self.store.active_chat
                    chat = self.store.new_chat(self.current["folder"])
                    self.store.active_chat = active_chat
                    chat.update(parent_chat_id=self.current["id"], delegated=True,
                                title="Агент · " + config["model"] + ": " + question.replace("\n", " ")[:35],
                                ai_selection={"profile_id": profile["id"], "model": config["model"]})
                child = self._runs.get(chat["id"])
                if child is None:
                    child = self._make_run(chat)
                    self._runs[chat["id"]] = child
                child.rpc.shutdown()
                child.config, child.key, child.profile_id = config, key, profile["id"]
                child.agent_depth = parent.agent_depth + 1
                child.sent_images = []
                child.prompt_images = []
                child.image_connections = self._dispatch_run(child, self._image_connections)
                timer = QTimer(self)
                timer.setSingleShot(True)
                child.agent_parent_request = {"run": parent, "id": event["id"], "timer": timer,
                                              "start": len(chat["messages"])}
                parent.agent_requests[event["id"]] = child
                timer.timeout.connect(lambda: self._dispatch_run(child, self._agent_timeout))
                timer.start(timeout * 1000)
                chat.update(running=True, interrupted=False)
                self._fill_chats()
                self._save()
                self._set_activity("Ожидает ответ из чата «" + chat["title"] + "»…", True)
                try:
                    self._dispatch_run(child, self._start_text_run,
                                       "Вопрос агента из чата «" + self.current["title"] + "»:\n\n" + question.strip())
                except (OSError, ValueError, RuntimeError) as exc:
                    self._dispatch_run(child, self._error, str(exc))
                return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = {"error": str(exc)}
        self.rpc.command({"type": "extension_ui_response", "id": event["id"],
                          "value": json.dumps(result, ensure_ascii=False)})

    def _agent_timeout(self):
        self._finish_agent_request("timeout")
        self._stop()

    def _finish_agent_request(self, outcome):
        child = self._run()
        request, child.agent_parent_request = child.agent_parent_request, None
        if not request:
            return
        request["timer"].stop()
        request["timer"].deleteLater()
        parent = request["run"]
        parent.agent_requests.pop(request["id"], None)
        if self._closed or parent.stopped:
            return
        result = {"chat_id": self.current["id"], "model": child.config["model"], "status": outcome}
        if outcome == "completed":
            answer = "\n\n".join(message.get("text", "") for message in self.current["messages"][request["start"]:]
                                   if message.get("role") == "assistant" and message.get("text"))
            result["answer"] = child.rpc.redact(answer)[-16000:]
            result["truncated"] = len(answer) > 16000
        else:
            result["error"] = child.rpc.redact(self.current.get("last_error") or
                                                ("Истекло время ожидания ответа агента." if outcome == "timeout" else "Задача другого агента остановлена."))
        try:
            parent.rpc.command({"type": "extension_ui_response", "id": request["id"],
                                "value": json.dumps(result, ensure_ascii=False)})
        except RuntimeError:
            pass

    def _read_chat_request(self, event):
        try:
            params = json.loads(event.get("placeholder", "{}"))
            if not isinstance(params, dict):
                raise ValueError("Некорректный запрос чата.")
            if event["title"] == "lanmessenger.list_chats":
                query = str(params.get("query", "")).casefold()
                chats = [c for c in self.store.chats if query in c["title"].casefold()]
                result = {"total": len(chats), "chats": [
                    {k: c.get(k) for k in ("id", "title", "folder", "model", "running", "parent_chat_id", "profile_id", "image_queue")}
                    for c in chats[:50]]}
            else:
                chat = next((c for c in self.store.chats if c["id"] == params.get("chat_id")), None)
                if chat is None:
                    raise ValueError("ИИ-чат не найден. Найдите его ID через list_chats.")
                limit = params.get("limit", 20)
                if not isinstance(limit, int) or not 1 <= limit <= 50:
                    raise ValueError("Можно прочитать от 1 до 50 последних сообщений.")
                messages = [{"role": m.get("role"), "text": self.rpc.redact(str(m.get("text", "")))[:4000],
                             "result": self.rpc.redact(str(m.get("result", "")))[:2000]}
                            for m in chat["messages"][-limit:]]
                images = []
                image_root = (self.store.root / "images").resolve()
                for message in chat["messages"][-limit:]:
                    for value in message.get("images", []):
                        path = Path(value).resolve()
                        if path.is_relative_to(image_root) and path.is_file() and len(images) < 4:
                            images.append(image_content(path))
                result = {"id": chat["id"], "title": chat["title"], "total_messages": len(chat["messages"]),
                          "messages": messages, "images": images, "running": chat.get("running"),
                          "image_queue": chat.get("image_queue")}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result = {"error": str(exc)}
        self.rpc.command({"type": "extension_ui_response", "id": event["id"],
                          "value": json.dumps(result, ensure_ascii=False)})

    def _render(self):
        if not self.current or not self._visible_run():
            return
        self.chat_title.setText(self.current["title"])
        # Also assign stable identities to legacy/system entries appended outside the projection.
        for message in self.current["messages"]:
            if "id" not in message:
                message["id"] = str(uuid.uuid4())
        run = self._run()
        pending = [(request_id, text, "steering") for request_id, text in run.steering.items()]
        if run.redirect:
            pending.append((*run.redirect, "redirecting"))
        pending.extend((request_id, text, "queued") for request_id, text in run.followups.items())
        self.output.set_messages(self.current["messages"] + [
            {"id": request_id, "role": "user", "text": self.rpc.redact(text), "status": status}
            for request_id, text, status in pending])

    def _start_auth(self):
        if self._installing:
            raise ValueError('Дождитесь установки компонентов ИИ.')
        if self.auth.state() != QProcess.ProcessState.NotRunning:
            self._stop_auth()
        config = self._save_connection()
        program, _args, module = runtime_command(config["runtime"])
        agent_dir = self.store.profile_root() / "pi"
        agent_dir.mkdir(exist_ok=True)
        self._auth_parser = JsonLines()
        self._auth_closing = False
        overrides = {"PI_CODING_AGENT_DIR": str(agent_dir), **runtime_environment()}
        variable = PROVIDERS[self.provider.currentData()][1]
        if variable and self.key.text().strip():
            overrides[variable] = self.key.text().strip()
        env = process_environment(overrides)
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
            self._auth_notice = None
            if self.provider.currentData() == "cloudflare-images":
                self._api_models_loaded([{"id": CLOUDFLARE_MODEL, "owner": "Cloudflare"}])
                self.auth_info.setText("FLUX.1 Schnell из документации Cloudflare. Доступ и остаток бесплатной квоты проверяются при генерации по вашему токену.")
                return
            if self.provider.currentData() in ("lanmessenger-api", *IMAGE_PROVIDERS):
                self._persist_connection()
                provider = self.provider.currentData()
                if provider == "ai-horde":
                    self.api_catalog.fetch(HORDE_URL + "/status", "", catalog_type="horde")
                elif provider == "lanmessenger-images":
                    self.api_catalog.fetch(self.endpoint.text(), self.key.text(), catalog_type="images")
                else:
                    self.api_catalog.fetch(self.endpoint.text(), self.key.text())
                self.auth_info.setText("Загрузка моделей из указанного API…")
                return
            self._start_auth()
            self._auth_command({"type": "list", "provider": self.provider.currentData()})
            self.auth_info.setText("Загрузка списка моделей…")
        except (ValueError, OSError) as exc:
            self.auth_info.setText(str(exc))
            self._check_runtime()

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
            self._check_runtime()

    def _logout(self):
        if not self.store or self._logging_in or self._task_busy() or self._pending:
            return
        if self.provider.currentData() not in self._oauth_providers:
            self._disconnect_key()
            return
        try:
            self.rpc.shutdown()
            self._start_auth()
            self._auth_command({"type": "logout", "provider": self.provider.currentData()})
        except (ValueError, OSError) as exc:
            self.auth_info.setText(str(exc))

    def _disconnect_key(self):
        if not self.store or self._logging_in or self._task_busy() or self._pending:
            return
        try:
            self.vault.clear()
            self.rpc.shutdown()
            self.api_catalog.cancel()
            self.remember.setChecked(False)
            self.key.clear()
            self._profile_keys.pop(self.store.active_profile, None)
            self._save_connection()
            self._provider_visibility()
            self.auth_info.setText("API-ключ отключён и удалён из сохранённых данных. Для подключения введите ключ заново.")
        except (ValueError, OSError) as exc:
            self.auth_info.setText(f"Не удалось отключить API-ключ: {exc}")

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
                if self.provider.currentData() in ("lanmessenger-api", *IMAGE_PROVIDERS):
                    continue
                self._fill_models(self.model.currentText())
                self._schedule_connection_save()
                logged = self.provider.currentData() in event.get("authenticated", [])
                notice = self._auth_notice
                self._auth_notice = None
                self.auth_info.setText(notice[1] if notice and notice[0] == self.provider.currentData() else
                                       "Аккаунт подключён." if logged else "Модели загружены. Войдите в аккаунт или введите API-ключ.")
                self._provider_visibility()
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
                message = "Аккаунт подключён." if kind == "logged_in" else "Вы вышли из аккаунта Pi на этом компьютере."
                if kind == "logged_out" and self.key.text().strip():
                    message += " Введённый API-ключ остаётся подключён; отключить его можно отдельной кнопкой."
                self._auth_notice = (event.get("provider", self.provider.currentData()), message)
                self.auth_info.setText(message)
                self._auth_command({"type": "list", "provider": self.provider.currentData()})
                self.rpc.shutdown()
            elif kind == "error":
                self._auth_failed(event.get("message", "Ошибка входа"))

    def _fill_models(self, selected):
        provider = self.provider.currentData()
        self.model.blockSignals(True)
        self.settings_model.blockSignals(True)
        self.model.clear()
        if provider in ("lanmessenger-api", *IMAGE_PROVIDERS):
            models = sorted(self._api_models, key=lambda m: (m.get("owner", ""), m["id"]))
            owner = None
            for model in models:
                group = model.get("owner") or "Другие модели"
                if owner != group:
                    self.model.addItem(group)
                    self.model.model().item(self.model.count() - 1).setEnabled(False)
                    owner = group
                self.model.addItem(model["id"])
                if model.get("available") is False:
                    item = self.model.model().item(self.model.count() - 1)
                    item.setEnabled(False)
                    item.setToolTip("Требуется первое пополнение" if model.get("unavailable_reason") == "FIRST_TOP_UP_REQUIRED" else "Недоступна для этого аккаунта")
            first = next((i for i in range(self.model.count()) if self.model.model().item(i).isEnabled()), -1)
            self.model.setCurrentIndex(first)
        else:
            models = sorted({m["id"] for m in self._models if m["provider"] == provider})
            self.model.addItems(models)
        if selected:
            self.model.setEditText(selected)
        elif not models:
            self.model.setEditText("")
        self.settings_model.clear()
        for i in range(self.model.count()):
            self.settings_model.addItem(self.model.itemText(i))
            self.settings_model.model().item(i).setEnabled(self.model.model().item(i).isEnabled())
        self.settings_model.setEditText(self.model.currentText())
        self.settings_model.blockSignals(False)
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
        self._controls(self._task_busy() or bool(self._pending))
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
            self._check_runtime()

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
        self._chat_pulse_timer.stop()
        self._persist_connection()
        self._closed = True
        self.settings_dialog.close()
        if self.installer and self.installer.isRunning():
            self.installer.cancel()
            self.installer.wait(1200)
        self.api_catalog.cancel()
        self._render_timer.stop()
        self._save_timer.stop()
        runs = list({id(run): run for run in list(self._runs.values()) + [self._selected_run]}.values())
        for chat in self.store.chats if self.store else []:
            if chat.get("image_job") and not any(run.chat is chat for run in runs):
                runs.append(self._make_run(chat))
        image_runs = [run for run in runs if run.images.busy or (run.chat and run.chat.get("image_job"))]
        for run in runs:
            for child in run.agent_requests.values():
                request, child.agent_parent_request = child.agent_parent_request, None
                if request:
                    request["timer"].stop()
                    request["timer"].deleteLater()
            run.agent_requests = {}
            self._dispatch_run(run, self._cancel_image_catalogs)
            run.images.cancel(job=run.chat.get("image_job") if run.chat else None)
            run.image_tool_requests = {}
            run.parent_request = None
            run.pending = None
            if run.chat:
                pending = self._dispatch_run(run, self._take_followups)
                run.chat["messages"].extend({"role": "system", "text": "Дополнение не доставлено: " + run.rpc.redact(text)}
                                            for text in pending)
            if run.chat and run.chat.get("running"):
                if run.timeline:
                    run.timeline.finish("interrupted")
                run.chat["running"] = False
                run.chat["interrupted"] = True
            run.rpc.shutdown()
        wait_for_image_cancellations([run.images for run in runs])
        for run in image_runs:
            job = run.chat.get("image_job")
            confirmed = job and job["id"] in run.images.cancelled_jobs
            if confirmed:
                run.chat.pop("image_job", None)
                run.chat.update(running=False, interrupted=False, outcome="stopped", unread_result=False)
                text = "При выходе из приложения AI Horde подтвердил запрос отмены генерации."
                self._dispatch_run(run, self._update_image_activity, "cancelled")
            else:
                run.chat.update(running=False, interrupted=True)
                self._dispatch_run(run, self._update_image_activity, "interrupted")
                text = ("Приложение закрыто. Отмена генерации на сервере не подтверждена; работа может продолжаться. "
                        "Сохранённое задание можно проверить кнопкой «Получить результат генерации»." if job else
                        "Ожидание изображения прекращено при выходе. Сервер мог продолжить уже отправленную генерацию.")
            run.chat["messages"].append({"role": "system", "text": text})
        self._stop_auth()
        self._save()
