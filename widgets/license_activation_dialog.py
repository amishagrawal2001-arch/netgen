"""Blocking activation dialog shown at client startup when no
valid license is cached. Modeled after the tlink-netops activation
screenshot: title, subtitle, License Key textarea, Load-file button,
Activate button, Buy link, fine print. Cancel / X = exit the app.

The paste field is a multi-line box because tlink-license-server's
offline codes are RS256 JWTs — hundreds of characters — that won't
fit in a single-line placeholder like the legacy `TLINK-XXXX-XXXX-…`
format.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QDesktopServices
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout,
    QWidget,
)

from utils.license import (
    TRIAL_DAYS, can_start_trial, extract_jwt_from_text,
    machine_fingerprint, save as save_license,
    start_trial as start_trial_license, verify_jwt,
)


# Where "Buy a license" points. Operator-configurable via env var
# so an on-prem deployment can point at their own admin console.
import os as _os
_BUY_URL = _os.environ.get(
    "NETGEN_LICENSE_BUY_URL",
    "https://tlink.io/netgen",
)


class LicenseActivationDialog(QDialog):
    """Modal, blocking. Rejects on Cancel/X (caller should exit)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activate Netgen")
        self.setModal(True)
        self.setMinimumWidth(480)
        # Kill the '?' hint on Windows, keep the close (X) button.
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 20)
        outer.setSpacing(14)

        # Emblem / brand mark — simple diamond in a coloured circle
        # (parity with the screenshot). Rendered as a styled QLabel
        # rather than an SVG asset to avoid a resource file.
        emblem = QLabel("◆", alignment=Qt.AlignCenter)
        emblem.setFixedSize(QSize(48, 48))
        emblem.setStyleSheet(
            "QLabel {"
            "  background: #6366f1; color: white;"
            "  font-size: 22px; font-weight: 600;"
            "  border-radius: 24px;"
            "}"
        )
        emblem_row = QHBoxLayout()
        emblem_row.addStretch(1)
        emblem_row.addWidget(emblem)
        emblem_row.addStretch(1)
        outer.addLayout(emblem_row)

        # Title
        title = QLabel("Activate Netgen", alignment=Qt.AlignCenter)
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        outer.addWidget(title)

        # Subtitle
        subtitle = QLabel(
            "Enter your license key to continue, or start a free trial.",
            alignment=Qt.AlignCenter,
        )
        subtitle.setStyleSheet("color: #475569;")
        outer.addWidget(subtitle)

        # License key label
        key_label = QLabel("LICENSE KEY")
        key_label.setStyleSheet(
            "color: #475569; font-size: 11px; font-weight: 600; "
            "letter-spacing: 0.5px;")
        outer.addWidget(key_label)

        # Multi-line paste field. JWTs are 500+ chars — a single-line
        # QLineEdit wouldn't be useful.
        self._key_edit = QTextEdit()
        self._key_edit.setAcceptRichText(False)
        self._key_edit.setPlaceholderText(
            "Paste your license JWT here "
            "(eyJhbGciOiJSUzI1NiJ9…)"
        )
        mono = QFont()
        mono.setStyleHint(QFont.Monospace)
        mono.setFamily("Menlo")
        mono.setPointSize(11)
        self._key_edit.setFont(mono)
        self._key_edit.setFixedHeight(88)
        self._key_edit.setStyleSheet(
            "QTextEdit {"
            "  background: #f1f5f9; border: 1px solid #cbd5e1;"
            "  border-radius: 4px; padding: 8px;"
            "}"
        )
        outer.addWidget(self._key_edit)

        # Inline error slot
        self._inline_error = QLabel("")
        self._inline_error.setStyleSheet(
            "color: #b91c1c; font-size: 12px;")
        self._inline_error.setWordWrap(True)
        self._inline_error.hide()
        outer.addWidget(self._inline_error)

        # Buttons row: Load file (secondary) + Activate (primary).
        btn_row = QHBoxLayout()
        self._load_btn = QPushButton("Load from file…")
        self._load_btn.clicked.connect(self._on_load_file)
        btn_row.addWidget(self._load_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setDefault(True)
        self._activate_btn.setStyleSheet(
            "QPushButton {"
            "  background: #1e3a8a; color: white; padding: 10px 0;"
            "  font-size: 14px; font-weight: 600; "
            "  border: none; border-radius: 4px;"
            "}"
            "QPushButton:hover { background: #1e40af; }"
            "QPushButton:disabled { background: #94a3b8; }"
        )
        self._activate_btn.clicked.connect(self._on_activate)
        outer.addWidget(self._activate_btn)

        # v0.5.183: self-service trial. Secondary button — the
        # customer runs against the fully-featured build for
        # TRIAL_DAYS before needing a real license.
        self._trial_btn = QPushButton(
            f"Start {TRIAL_DAYS}-day free trial")
        self._trial_btn.setStyleSheet(
            "QPushButton {"
            "  background: white; color: #1e3a8a;"
            "  padding: 8px 0; font-size: 13px; font-weight: 600;"
            "  border: 1px solid #1e3a8a; border-radius: 4px;"
            "}"
            "QPushButton:hover { background: #eff6ff; }"
            "QPushButton:disabled {"
            "  color: #94a3b8; border-color: #cbd5e1;"
            "  background: #f8fafc;"
            "}"
        )
        self._trial_btn.clicked.connect(self._on_start_trial)
        if not can_start_trial():
            self._trial_btn.setEnabled(False)
            self._trial_btn.setToolTip(
                "The trial has already been used on this device.")
        outer.addWidget(self._trial_btn)

        # Buy link — flat, blue text-button that opens the browser.
        buy_btn = QPushButton(f"Buy a license at {_BUY_URL}")
        buy_btn.setFlat(True)
        buy_btn.setStyleSheet(
            "QPushButton {"
            "  color: #1e40af; border: none; background: transparent;"
            "  padding: 4px 0;"
            "}"
            "QPushButton:hover { text-decoration: underline; }"
        )
        buy_btn.setCursor(Qt.PointingHandCursor)
        buy_btn.clicked.connect(self._on_buy)
        outer.addWidget(buy_btn, alignment=Qt.AlignCenter)

        # Show the local device fingerprint so the operator can
        # copy it and send to the license issuer, who needs it to
        # mint a device-bound offline code. tlink-license-server
        # REQUIRES a fingerprint at mint time — the whole 64-char
        # string, not a prefix — so we render it full-width with
        # word-wrap plus a one-click Copy button.
        outer.addSpacing(4)
        fp_header = QLabel(
            "Device fingerprint — share with your license issuer:")
        fp_header.setStyleSheet(
            "color: #64748b; font-size: 11px;")
        outer.addWidget(fp_header)

        fp_hbox = QHBoxLayout()
        fp_hbox.setSpacing(6)
        self._fingerprint = machine_fingerprint()
        fp_value = QLabel(self._fingerprint)
        fp_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fp_value.setWordWrap(True)
        fp_value.setFont(mono)
        fp_value.setStyleSheet(
            "QLabel {"
            "  background: #f8fafc; color: #0f172a;"
            "  border: 1px solid #e2e8f0; border-radius: 3px;"
            "  padding: 4px 6px; font-size: 11px;"
            "}"
        )
        fp_hbox.addWidget(fp_value, 1)

        self._copy_fp_btn = QPushButton("Copy")
        self._copy_fp_btn.setFixedHeight(28)
        self._copy_fp_btn.setStyleSheet(
            "QPushButton {"
            "  padding: 2px 10px; font-size: 11px;"
            "  border: 1px solid #cbd5e1; border-radius: 3px;"
            "  background: white;"
            "}"
            "QPushButton:hover { background: #f1f5f9; }"
        )
        self._copy_fp_btn.clicked.connect(self._on_copy_fingerprint)
        fp_hbox.addWidget(self._copy_fp_btn, 0, Qt.AlignTop)

        # QR toggle — click to show/hide a QR encoding the same
        # fingerprint. License issuer scans instead of copy-paste.
        self._qr_btn = QPushButton("QR")
        self._qr_btn.setFixedHeight(28)
        self._qr_btn.setCheckable(True)
        self._qr_btn.setStyleSheet(
            "QPushButton {"
            "  padding: 2px 10px; font-size: 11px;"
            "  border: 1px solid #cbd5e1; border-radius: 3px;"
            "  background: white;"
            "}"
            "QPushButton:hover { background: #f1f5f9; }"
            "QPushButton:checked { background: #1e40af; color: white; }"
        )
        self._qr_btn.toggled.connect(self._on_toggle_qr)
        fp_hbox.addWidget(self._qr_btn, 0, Qt.AlignTop)

        outer.addLayout(fp_hbox)

        # QR image slot — hidden until the operator asks for it.
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setVisible(False)
        outer.addWidget(self._qr_label)

        # Copyright fine print (parity with the screenshot).
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background: #e5e7eb; max-height: 1px;")
        outer.addWidget(line)
        year = _current_year()
        footer = QLabel(f"(c) {year} Netgen",
                        alignment=Qt.AlignCenter)
        footer.setStyleSheet("color: #94a3b8; font-size: 11px;")
        outer.addWidget(footer)

    # ── actions ──────────────────────────────────────────

    def _on_load_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load license file", str(Path.home()),
            "License files (*.jwt *.json *.txt *.lic);;All files (*)",
        )
        if not path_str:
            return
        try:
            text = Path(path_str).read_text(encoding="utf-8")
        except OSError as exc:
            self._show_error(f"Could not read file: {exc}")
            return
        token = extract_jwt_from_text(text)
        if not token:
            self._show_error(
                "That file doesn't contain a valid JWT.")
            return
        self._key_edit.setPlainText(token)
        self._inline_error.hide()

    def _on_activate(self) -> None:
        raw = self._key_edit.toPlainText().strip()
        if not raw:
            self._show_error("Paste your license key.")
            return
        # Strip any wrapping whitespace / newlines the user's paste
        # may have introduced without breaking the three-segment
        # JWT structure. Newlines inside the segments would kill it.
        raw = raw.replace("\r", "").replace("\n", "").replace(" ", "")
        result = verify_jwt(raw)
        if not result.is_valid:
            self._show_error(f"License rejected: {result.reason}")
            return
        try:
            save_license(raw)
        except OSError as exc:
            self._show_error(f"Could not save license: {exc}")
            return
        # Show a small success confirmation before accepting so the
        # operator has closure on what they activated.
        end = (result.end_date.isoformat() if result.end_date
               else "no end date")
        billing = result.billing_type or "PAID"
        QMessageBox.information(
            self, "License activated",
            f"License activated for {result.email or 'this device'}.\n\n"
            f"Type: {result.license_type}  ·  Billing: {billing}\n"
            f"Entitlement ends: {end}\n\n"
            "Netgen will now start."
        )
        self.accept()

    def _on_start_trial(self) -> None:
        """Kick off a locally-tracked trial. No server interaction —
        writes `~/.netgen/trial.json` + `trial-used.marker`."""
        result = start_trial_license()
        if not result.is_valid:
            self._show_error(
                f"Could not start trial: {result.reason}")
            return
        days = result.days_until_expiry() or 0
        QMessageBox.information(
            self, "Trial started",
            f"{TRIAL_DAYS}-day trial active on this device.\n\n"
            f"Ends: {result.end_date.isoformat()}  "
            f"({days + 1} day(s) from now)\n\n"
            "Netgen will now start. Buy a license any time from "
            "Help → License Status… to keep the licensed features "
            "after the trial ends."
        )
        self.accept()

    def _on_buy(self) -> None:
        QDesktopServices.openUrl(QUrl(_BUY_URL))

    def _on_copy_fingerprint(self) -> None:
        """Copy the full 64-char fingerprint to the system
        clipboard. Give brief visual confirmation on the button so
        the operator knows it worked without a popup."""
        try:
            QApplication.clipboard().setText(self._fingerprint)
        except Exception:
            pass
        # Flash "Copied ✓" for ~1.2 s.
        original = self._copy_fp_btn.text()
        self._copy_fp_btn.setText("Copied ✓")
        self._copy_fp_btn.setEnabled(False)
        from PyQt5.QtCore import QTimer

        def _restore():
            self._copy_fp_btn.setText(original)
            self._copy_fp_btn.setEnabled(True)
        QTimer.singleShot(1200, _restore)

    def _on_toggle_qr(self, checked: bool) -> None:
        """Show/hide a QR code of the fingerprint. Rendered
        on-demand; missing qrcode/Pillow degrade gracefully."""
        if not checked:
            self._qr_label.setVisible(False)
            self._qr_label.clear()
            return
        try:
            import qrcode
            from io import BytesIO
            img = qrcode.make(self._fingerprint,
                              box_size=6, border=2)
            buf = BytesIO()
            img.save(buf, format="PNG")
            from PyQt5.QtGui import QPixmap
            pix = QPixmap()
            pix.loadFromData(buf.getvalue(), "PNG")
            self._qr_label.setPixmap(pix)
            self._qr_label.setVisible(True)
        except ImportError:
            self._qr_label.setText(
                "<i style='color:#94a3b8;font-size:11px;'>"
                "QR unavailable — install 'qrcode' + 'Pillow'."
                "</i>")
            self._qr_label.setVisible(True)
        except Exception as exc:  # noqa: BLE001
            self._qr_label.setText(
                f"<i style='color:#94a3b8;font-size:11px;'>"
                f"QR render failed: {exc}</i>")
            self._qr_label.setVisible(True)

    def _show_error(self, msg: str) -> None:
        self._inline_error.setText(msg)
        self._inline_error.show()


def _current_year() -> int:
    import datetime as _dt
    return _dt.date.today().year


def run_activation_gate(parent: Optional[QWidget] = None) -> bool:
    """Blocking activation gate. Returns True to proceed with the
    normal startup, False to signal "user cancelled — exit".

    Used by `run_tgen_client.main()` between `QApplication(sys.argv)`
    and the main window construction."""
    from utils import license as _lic
    if _lic.is_activated():
        return True
    dlg = LicenseActivationDialog(parent)
    return dlg.exec_() == QDialog.Accepted
