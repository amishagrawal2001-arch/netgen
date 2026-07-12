"""Help → License Status… dialog — read-only view of the currently
loaded license plus a Deactivate button.

Activation itself happens in the blocking
`widgets.license_activation_dialog.LicenseActivationDialog` at
startup (v0.5.183). This dialog is post-activation only: it shows
the operator what license they're running under and lets them
remove it (which forces re-activation on next launch)."""
from __future__ import annotations

from typing import Optional

import os as _os

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QFont
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

_BUY_URL = _os.environ.get(
    "NETGEN_LICENSE_BUY_URL",
    "https://tlink.io/netgen",
)

from utils.license import (
    License, load as load_license,
    machine_fingerprint, remove as remove_license,
)


class LicenseDialog(QDialog):
    """Read-only status dialog."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Netgen License")
        self.setMinimumWidth(560)
        self._license: License = load_license()
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse)
        outer.addWidget(self._status_label)

        details = QFormLayout()
        details.setSpacing(6)
        self._email_val = QLabel("—")
        self._type_val = QLabel("—")
        self._billing_val = QLabel("—")
        self._start_val = QLabel("—")
        self._end_val = QLabel("—")
        self._session_val = QLabel("—")
        self._fp_val = QLabel("—")
        mono = QFont()
        mono.setStyleHint(QFont.Monospace)
        mono.setFamily("Menlo")
        for v in (self._email_val, self._type_val,
                  self._billing_val, self._start_val,
                  self._end_val, self._session_val, self._fp_val):
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._fp_val.setFont(mono)
        details.addRow("Email:", self._email_val)
        details.addRow("License type:", self._type_val)
        details.addRow("Billing:", self._billing_val)
        details.addRow("Starts:", self._start_val)
        details.addRow("Entitlement ends:", self._end_val)
        details.addRow("Session expires:", self._session_val)
        details.addRow("Device fingerprint:", self._fp_val)
        outer.addLayout(details)

        self._features_label = QLabel()
        self._features_label.setStyleSheet(
            "color: #475569; font-size: 12px;")
        self._features_label.setWordWrap(True)
        outer.addWidget(self._features_label)

        buttons = QDialogButtonBox()
        # v0.5.183: Renew — open the buy URL. Shown always but
        # highlighted amber when license is ≤30 days or in-grace.
        self._renew_btn = QPushButton("Renew License…")
        self._renew_btn.clicked.connect(self._on_renew)
        buttons.addButton(self._renew_btn,
                          QDialogButtonBox.ActionRole)
        self._deactivate_btn = QPushButton("Deactivate")
        self._deactivate_btn.clicked.connect(self._on_deactivate)
        buttons.addButton(self._deactivate_btn,
                          QDialogButtonBox.DestructiveRole)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addButton(close_btn, QDialogButtonBox.RejectRole)
        outer.addWidget(buttons)

    def _refresh(self) -> None:
        lic = self._license
        renew_urgent = False
        if lic.is_valid:
            days = lic.days_until_expiry()
            if lic.in_grace_period():
                self._status_label.setText(
                    f"<b style='color:#b91c1c;'>Grace period — "
                    f"{lic.notes[0] if lic.notes else 'renew now'}</b>")
                renew_urgent = True
            elif days is not None and days < 30:
                self._status_label.setText(
                    f"<b style='color:#b45309;'>License active — "
                    f"expires in {days} day(s).</b>")
                renew_urgent = True
            else:
                self._status_label.setText(
                    "<b style='color:#047857;'>License active.</b>")
        elif lic.reason == "no license":
            self._status_label.setText(
                "<b style='color:#b91c1c;'>No license loaded.</b> "
                "Restart the client to activate.")
        else:
            self._status_label.setText(
                f"<b style='color:#b91c1c;'>License problem: "
                f"{lic.reason}.</b>")
            renew_urgent = True
        # v0.5.183: highlight Renew when the operator is close to
        # (or past) expiry so the CTA stands out.
        if renew_urgent:
            self._renew_btn.setStyleSheet(
                "QPushButton {"
                "  background: #1e40af; color: white;"
                "  padding: 6px 14px; font-weight: 600;"
                "  border: none; border-radius: 4px;"
                "}"
                "QPushButton:hover { background: #1e3a8a; }"
            )
        else:
            self._renew_btn.setStyleSheet("")

        self._email_val.setText(lic.email or "—")
        self._type_val.setText(lic.license_type or "—")
        self._billing_val.setText(lic.billing_type or "—")
        self._start_val.setText(
            lic.start_date.isoformat() if lic.start_date else "—")
        self._end_val.setText(
            lic.end_date.isoformat() if lic.end_date else "—")
        self._session_val.setText(
            lic.expiry.isoformat(timespec="minutes")
            if lic.expiry else "—")
        # Show local device fingerprint. Copy-able so the operator
        # can paste it into the license-server admin UI when they
        # need to issue a device-bound offline code.
        self._fp_val.setText(machine_fingerprint())

        if lic.is_valid:
            self._features_label.setText(
                "Unlocked features: DPDK Blast, RDMA Blast, "
                "RDMA Topology, RFC 2544."
            )
        else:
            self._features_label.setText(
                "Locked features: DPDK Blast, RDMA Blast, "
                "RDMA Topology, RFC 2544."
            )
        self._deactivate_btn.setEnabled(bool(lic.jwt_token))

    def _on_renew(self) -> None:
        """Open the buy URL in the default browser."""
        QDesktopServices.openUrl(QUrl(_BUY_URL))

    def _on_deactivate(self) -> None:
        if not self._license.jwt_token:
            return
        confirm = QMessageBox.question(
            self, "Deactivate license",
            "Remove the saved license? You'll need to re-activate "
            "on next launch.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            remove_license()
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not deactivate", str(exc))
            return
        self._license = load_license()
        self._refresh()


def show_license_dialog(parent: Optional[QWidget] = None) -> None:
    dlg = LicenseDialog(parent)
    dlg.exec_()
