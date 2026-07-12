"""Top-of-window license banner.

Shown when the license needs attention: ≤7 days from expiry,
inside the post-expiry grace period, or license invalid entirely.
Dismissible per-day via a QSettings marker so it doesn't nag on
every restart but reasserts at the next daily boundary.

Hidden entirely when the license is healthy (>7 days remaining)
so it doesn't take up chrome for happy customers.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QWidget,
)

from utils.license import License


_DISMISS_KEY = "license_banner_dismissed_date"


class LicenseBanner(QWidget):
    """Compact banner meant to sit at the top of the main window
    below the menu bar. Emits `renew_clicked` when the Renew
    button is pressed so the main window can route to the License
    Status dialog (or open a buy URL directly)."""

    renew_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = QSettings("Netgen", "netgen-client")
        self._build_ui()
        self.setVisible(False)

    def _build_ui(self) -> None:
        self.setMaximumHeight(36)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 8, 4)
        row.setSpacing(10)
        self._icon = QLabel("⚠")
        self._icon.setStyleSheet(
            "color: white; font-weight: 700; font-size: 14px;")
        row.addWidget(self._icon)
        self._text = QLabel("")
        self._text.setStyleSheet(
            "color: white; font-weight: 600; font-size: 12px;")
        self._text.setWordWrap(True)
        row.addWidget(self._text, 1)
        self._renew_btn = QPushButton("Renew License…")
        self._renew_btn.setCursor(Qt.PointingHandCursor)
        self._renew_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(255,255,255,0.15); color: white;"
            "  padding: 4px 12px; font-size: 11px; font-weight: 600;"
            "  border: 1px solid rgba(255,255,255,0.35);"
            "  border-radius: 3px;"
            "}"
            "QPushButton:hover { background: rgba(255,255,255,0.25); }"
        )
        self._renew_btn.clicked.connect(self.renew_clicked.emit)
        row.addWidget(self._renew_btn)
        dismiss = QPushButton("Dismiss")
        dismiss.setCursor(Qt.PointingHandCursor)
        dismiss.setStyleSheet(
            "QPushButton {"
            "  background: transparent; color: white;"
            "  padding: 4px 8px; font-size: 11px;"
            "  border: none;"
            "}"
            "QPushButton:hover { text-decoration: underline; }"
        )
        dismiss.clicked.connect(self._on_dismiss)
        row.addWidget(dismiss)

    def refresh(self, license: Optional[License] = None) -> None:
        """Recompute visibility + colour. Call at startup and
        after every license mutation (activate/deactivate/renew).
        Also safe to call from a periodic timer."""
        if license is None:
            from utils.license import load as _load
            license = _load()
        # Dismissed today already? Skip until the next day.
        today = _dt.date.today().isoformat()
        if self._settings.value(_DISMISS_KEY, "") == today:
            self.setVisible(False)
            return
        text, colour = self._describe(license)
        if not text:
            self.setVisible(False)
            return
        self._text.setText(text)
        self.setStyleSheet(f"background: {colour};")
        self.setVisible(True)

    # ── labelling ────────────────────────────────────

    def _describe(self, lic: License) -> tuple[str, str]:
        """Return (banner_text, background_colour). Empty text →
        hide the banner."""
        if not lic.is_valid:
            if lic.reason == "no license":
                return ("", "")  # Handled by blocking dialog at boot.
            return (
                f"License problem: {lic.reason}. Some features are "
                "greyed out until this is resolved.",
                "#b91c1c",
            )
        # Valid.
        if lic.in_grace_period():
            note = lic.notes[0] if lic.notes else "Renew now."
            return (note, "#b91c1c")
        days = lic.days_until_expiry()
        if lic.is_trial() and days is not None and days <= 7:
            return (
                f"Trial expires in {days + 1} day(s). Load a paid "
                "license to keep the licensed features working.",
                "#b45309",
            )
        if not lic.is_trial() and days is not None and days <= 7:
            return (
                f"License expires in {days + 1} day(s). Click "
                "Renew to open the purchase portal.",
                "#b45309",
            )
        return ("", "")

    def _on_dismiss(self) -> None:
        self._settings.setValue(
            _DISMISS_KEY, _dt.date.today().isoformat())
        self.setVisible(False)
