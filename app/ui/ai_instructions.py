"""Editable per-style agent instructions; changes are applied only on Save."""
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

MAX_INSTRUCTION_CHARS = 12000


class InstructionsDialog(QDialog):
    def __init__(self, label, text, default, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Инструкция — {label}')
        self.setMinimumSize(320, 220)
        available = self.screen().availableGeometry()
        self.resize(min(680, max(320, available.width()-40)), min(420, max(240, available.height()-60)))
        layout=QVBoxLayout(self)
        hint=QLabel('Инструкция выбранного режима. Применяется к следующей задаче; изменения сохраняются только по кнопке «Сохранить».')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor=QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setPlaceholderText('Дополнительные указания агенту')
        layout.addWidget(self.editor,1)
        self.count=QLabel()
        layout.addWidget(self.count)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        reset=self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        reset.setText('Вернуть стандартную')
        reset.clicked.connect(lambda: self.editor.setPlainText(default))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.editor.textChanged.connect(self._count)
        self._count()
        layout.addWidget(self.buttons)

    def _count(self):
        length=len(self.editor.toPlainText())
        self.count.setText(f'{length} / {MAX_INSTRUCTION_CHARS} символов')
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(length<=MAX_INSTRUCTION_CHARS)
