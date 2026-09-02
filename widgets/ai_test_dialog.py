"""
AI Test Framework Dialog (stub)

Minimal placeholder so the "AI Test" button in the Devices tab can open
a dialog without raising ModuleNotFoundError. The real test framework
will replace this file once implemented.
"""
# v0.5.245-followup (audit AI-*)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


class AITestFrameworkDialog(QDialog):
    """Placeholder AI Test Framework dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Test Framework")
        self.setMinimumSize(420, 180)

        self._device_id = ""

        layout = QVBoxLayout(self)

        title = QLabel("AI Test Framework")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        msg = QLabel(
            "AI Test Framework - not yet implemented.\n\n"
            "This feature is scaffolded but the backend and UI are still\n"
            "under development. Close this dialog to return to the app."
        )
        msg.setAlignment(Qt.AlignLeft)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        layout.addStretch()

        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

    def set_device_id(self, device_id):
        """Record device id for when the real framework lands."""
        self._device_id = device_id or ""
