"""EVPN active-injections chip for the VXLAN sub-tab (v0.2.78).

The EVPN Inject dialog (widgets/evpn_inject_dialog.py) lists active
Type-2 + Type-5 injections, but operators had to open the dialog just
to find out whether anything was running. This chip lives on the
VXLAN sub-tab header and shows the live count at a glance:

  * **gray** — none active (or no server)
  * **violet** — N active (Type-2 + Type-5 combined)

Click the chip to open the EVPN Inject dialog directly — no menu
hunt needed.

Polls ``/api/evpn/type2/list`` every 30 s; the endpoint returns BOTH
Type-2 and Type-5 entries (cross-kind aliasing landed in v0.2.67).
Defensively quiet: HTTP failure leaves the chip in its previous
state and logs a debug line.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

import requests
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QWidget


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 30_000


class EvpnActiveChip(QLabel):
    """Click-to-open active-injections indicator."""

    clicked = pyqtSignal()  # listener wires up its EVPN dialog opener

    _STATES = {
        "idle":   {"bg": "#f1f5f9", "fg": "#475569", "border": "#cbd5e1"},
        "active": {"bg": "#f5f3ff", "fg": "#5b21b6", "border": "#c4b5fd"},
    }

    def __init__(self,
                 server_url_provider: Callable[[], Optional[str]],
                 parent: Optional[QWidget] = None,
                 *, poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS):
        super().__init__(parent)
        self._get_server_url = server_url_provider
        self._poll_interval_ms = int(poll_interval_ms)
        self._count = 0
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            "Click to open EVPN Inject (Type-2 / Type-5 bulk inject).\n"
            "Counts both kinds across the selected server."
        )
        self._paint(0)

        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_interval_ms
                                or DEFAULT_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        if self._poll_interval_ms > 0:
            self._timer.start()
        # First-refresh kick a moment after construction so the chip
        # shows real numbers without blocking the GUI startup.
        QTimer.singleShot(300, self.refresh)

    # ────────────────────────────────────────────────── public API
    def refresh(self) -> None:
        """Fetch /api/evpn/type2/list, recount, repaint. Safe to call
        from any context — never raises."""
        url = self._get_server_url() or ""
        if not url:
            # No server → reset to idle but keep cursor + tooltip so a
            # click still tries to open the dialog (which will then
            # show its own "select a server" warning).
            self._paint(0)
            return
        try:
            r = requests.get(
                f"{url.rstrip('/')}/api/evpn/type2/list",
                headers=_auth_headers(), timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[EVPN CHIP] fetch failed: {exc}")
            return
        if r.status_code != 200:
            logger.debug(f"[EVPN CHIP] HTTP {r.status_code}")
            return
        try:
            payload = r.json() or {}
        except Exception:
            return
        items: List[Dict[str, Any]] = (
            payload.get("injections") or payload.get("items") or []
        )
        self._paint(len(items) if isinstance(items, list) else 0)

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass

    def count(self) -> int:
        """Last-seen active count — for tests."""
        return self._count

    # ──────────────────────────────────────────────── internals
    def mousePressEvent(self, ev):
        # Any click opens the dialog — operator doesn't have to aim
        # at a button.
        try:
            self.clicked.emit()
        finally:
            super().mousePressEvent(ev)

    def _paint(self, count: int) -> None:
        self._count = max(0, int(count))
        if self._count > 0:
            palette = self._STATES["active"]
            text = f"⚡ EVPN: {self._count} active"
        else:
            palette = self._STATES["idle"]
            text = "⚡ EVPN: idle"
        self.setText(text)
        self.setStyleSheet(
            f"background: {palette['bg']}; color: {palette['fg']}; "
            f"border: 1px solid {palette['border']}; "
            f"padding: 1px 10px; border-radius: 9px; "
            f"font-size: 11px; font-weight: 600;"
        )


def _auth_headers() -> Dict[str, str]:
    tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}
