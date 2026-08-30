"""Modal dialog that shows vendor-specific upstream-router config
hints generated from a netgen device's own settings.

Three tabs: Juniper (JunOS `set` syntax), Cisco IOS, Arista EOS.
Each tab has a monospace read-only text area and a Copy button.
The device data passed in can be either the DB-format dict from
the server or the display-format dict from the client table —
utils.upstream_hints handles both via alternate-key lookup.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from utils.upstream_hints import render_all


class UpstreamHintDialog(QDialog):
    """Modal — shows the three vendor snippets in tabs, with Copy buttons.

    ``device_data`` is a dict pulled straight from the client's
    all_devices cache or the server's /api/device/database/devices
    response; both formats are supported by utils.upstream_hints.
    """

    def __init__(self, device_data: dict, parent=None):
        super().__init__(parent)
        name = (
            device_data.get("device_name")
            or device_data.get("Device Name")
            or "device"
        )
        self.setWindowTitle(f"Upstream Router Config Hint — {name}")
        self.resize(720, 560)

        snippets = render_all(device_data)

        self.tabs = QTabWidget(self)
        for label, key in (
            ("Juniper (JunOS)", "juniper"),
            ("Cisco IOS", "cisco"),
            ("Arista EOS", "arista"),
        ):
            self.tabs.addTab(self._build_tab(snippets.get(key, "")), label)

        note = QLabel(
            "Paste these onto your upstream router / switch to peer "
            "with this netgen device. The physical uplink placeholder "
            "(ge-0/0/0, GigabitEthernet0/0, Ethernet1) must be edited "
            "to match your actual interface before you commit."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280; font-size: 12px; padding: 4px 2px;")

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _build_tab(self, text: str) -> QWidget:
        page = QWidget(self.tabs)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)

        editor = QPlainTextEdit(text or "(nothing to render — device has no interface, BGP, OSPF, or IS-IS config yet)", page)
        editor.setReadOnly(True)
        # Monospaced so alignment reads correctly.
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        editor.setFont(mono)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setTabChangesFocus(True)
        layout.addWidget(editor, stretch=1)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy to clipboard", page)
        copy_btn.clicked.connect(lambda: self._copy(editor.toPlainText(), copy_btn))
        row.addStretch()
        row.addWidget(copy_btn)
        layout.addLayout(row)
        return page

    def _copy(self, text: str, button: QPushButton) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.clipboard().setText(text or "")
        # Momentary feedback so the operator knows the click landed.
        original = button.text()
        button.setText("Copied ✓")
        button.setEnabled(False)
        try:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(1200, lambda: (button.setText(original), button.setEnabled(True)))
        except Exception:
            # Widget torn down before the timer fires — ignore.
            button.setText(original)
            button.setEnabled(True)
