"""Status-bar pill showing the client's license state.

Colour scheme mirrors the other status-bar chips
(`DpdkReadinessChip`, `OrphanChip`):

  * green  — active paid license, > 30 days remaining
  * amber  — active license expiring in ≤ 30 days OR trial mode
  * red    — no license / expired / invalid

Click → open Help → License Status… so the operator can inspect
details, deactivate, or paste a new key.

Refreshes every 60 s. The license file rarely changes at runtime,
but a periodic re-read catches expiry rollover without needing a
client restart.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QWidget

from utils.license import License, load as load_license


logger = logging.getLogger(__name__)


_STYLE_BASE = (
    "QLabel {"
    "  padding: 2px 10px;"
    "  border-radius: 10px;"
    "  font-size: 11px; font-weight: 600;"
    "  border: 1px solid transparent;"
    "}"
    "QLabel:hover {"
    "  border-color: rgba(0, 0, 0, 0.25);"
    "}"
)

_STYLE_GREEN = _STYLE_BASE + \
    "QLabel { background: #dcfce7; color: #166534; }"
_STYLE_AMBER = _STYLE_BASE + \
    "QLabel { background: #fef3c7; color: #92400e; }"
_STYLE_RED = _STYLE_BASE + \
    "QLabel { background: #fee2e2; color: #b91c1c; }"


class LicenseChip(QLabel):
    """Compact status pill wired into the main-window statusBar."""

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(self.cursor())
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)   # every 60 s
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Recompute label + style from `~/.netgen/license.jwt`
        (or trial file). Never raises."""
        try:
            lic = load_license()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[license-chip] load failed: %s", exc)
            self._paint_red("License: error", str(exc))
            return
        label, tooltip, kind = self._describe(lic)
        if kind == "green":
            self.setStyleSheet(_STYLE_GREEN)
        elif kind == "amber":
            self.setStyleSheet(_STYLE_AMBER)
        else:
            self.setStyleSheet(_STYLE_RED)
        self.setText(label)
        self.setToolTip(tooltip)

    # ── labelling ────────────────────────────────────

    def _describe(self, lic: License) -> tuple[str, str, str]:
        """Return (short_label, hover_tooltip, colour_kind)."""
        if not lic.is_valid:
            if lic.reason == "no license":
                return (
                    "⚠ Unlicensed",
                    "No license loaded. Restart the client to "
                    "activate. Help → License Status… for details.",
                    "red",
                )
            if "trial expired" in lic.reason:
                return (
                    "⛔ Trial expired",
                    "Your 30-day trial has ended. Click to open "
                    "License Status and paste a paid license.",
                    "red",
                )
            if lic.reason == "expired" or "expired" in lic.reason:
                return (
                    "⛔ License expired",
                    f"License problem: {lic.reason}. Click to fix.",
                    "red",
                )
            return (
                "⛔ License invalid",
                f"License problem: {lic.reason}. Click to fix.",
                "red",
            )
        # Valid.
        # v0.5.183 grace: valid-but-past-end_date. Paint red and
        # show the note verbatim so the operator sees "renew in N"
        # in the tooltip.
        if lic.in_grace_period():
            return (
                "⛔ Grace period",
                (lic.notes[0] if lic.notes else
                 "License expired — running under grace. Click to renew."),
                "red",
            )
        days = lic.days_until_expiry()
        if lic.is_trial():
            days_txt = (f" · {days + 1} day(s) left"
                        if days is not None else "")
            return (
                f"⏱ Trial{days_txt}",
                (f"Trial license active{days_txt}. Click to view "
                 "details or paste a paid license."),
                "amber",
            )
        # Paid.
        if days is not None and days <= 30:
            return (
                f"⚠ License · {days + 1}d",
                (f"License active — expires in {days + 1} day(s). "
                 "Click to view details."),
                "amber",
            )
        return (
            "✓ Licensed",
            (f"License active — {lic.billing_type or 'paid'}, "
             f"{lic.license_type or 'individual'}. "
             "Click to view details."),
            "green",
        )

    def _paint_red(self, label: str, tooltip: str) -> None:
        self.setStyleSheet(_STYLE_RED)
        self.setText(label)
        self.setToolTip(tooltip)

    # ── click passthrough ────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == 1:  # Qt.LeftButton
            self.clicked.emit()
        super().mousePressEvent(event)
