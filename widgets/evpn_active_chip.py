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
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QWidget


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 30_000


class _EvpnFetchThread(QThread):
    """v0.5.175: off-UI-thread fetcher for the EVPN active list.

    Pre-fix, `EvpnActiveChip.refresh()` ran `requests.get(timeout=5)`
    DIRECTLY on the UI thread every 30 s. When the configured
    server name didn't resolve (lab box off network, VPN dropped),
    `getaddrinfo()` blocked for the OS-level DNS timeout — 30+ s
    on macOS — and the entire Qt event loop froze. Operator hit
    Ctrl+C to recover. Traceback bottomed out in `socket.py`.

    Same pattern as `widgets/dpdk_readiness_chip.py` (v0.4.7) and
    `widgets/orphan_chip.py` (v0.5.169) — the fetch runs on a
    one-shot QThread; the chip stays responsive. The thread's
    finished signal carries the parsed JSON payload back to the
    UI thread for repaint.
    """

    payload_ready = pyqtSignal(list)   # items list (possibly empty)
    failed = pyqtSignal(str)            # error string

    def __init__(self, url: str, *, timeout_s: float = 5.0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = url
        self._timeout_s = timeout_s

    def run(self) -> None:
        try:
            r = requests.get(
                self._url, headers=_auth_headers(),
                timeout=self._timeout_s)
            if r.status_code != 200:
                self.failed.emit(f"HTTP {r.status_code}")
                return
            payload = r.json() or {}
            items = (payload.get("injections")
                     or payload.get("items") or [])
            self.payload_ready.emit(
                items if isinstance(items, list) else [])
        except Exception as exc:
            self.failed.emit(str(exc))


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
        # v0.5.175: dedup so rapid timer ticks don't stack fetches
        # while one is in flight (would happen when DNS slow-fails).
        self._fetch_in_flight: Optional[_EvpnFetchThread] = None
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
        """Fetch /api/evpn/type2/list async, recount, repaint. Safe
        to call from any context — never raises, never blocks the
        UI thread."""
        url = self._get_server_url() or ""
        if not url:
            # No server → reset to idle but keep cursor + tooltip so a
            # click still tries to open the dialog (which will then
            # show its own "select a server" warning).
            self._paint(0)
            return
        # v0.5.175: skip if a previous fetch is still running — it
        # will land soon enough; another concurrent thread would
        # only waste a connection.
        if self._fetch_in_flight is not None:
            return
        full_url = f"{url.rstrip('/')}/api/evpn/type2/list"
        thread = _EvpnFetchThread(full_url, parent=self)
        thread.payload_ready.connect(self._on_payload)
        thread.failed.connect(self._on_failed)
        # Cleanup signals — both clear the in-flight guard AND
        # schedule deleteLater. Connection order matters: clear
        # first so a new refresh() can fire before the wrapper
        # actually deletes.
        thread.finished.connect(self._clear_in_flight)
        thread.finished.connect(thread.deleteLater)
        self._fetch_in_flight = thread
        thread.start()

    def _on_payload(self, items: list) -> None:
        self._paint(len(items))

    def _on_failed(self, err: str) -> None:
        logger.debug(f"[EVPN CHIP] fetch failed: {err}")
        # Leave previous count in place — defensive: if we're seeing
        # transient failures, don't blink the chip to idle. Reset
        # only when the operator explicitly clears the server URL.

    def _clear_in_flight(self) -> None:
        self._fetch_in_flight = None

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
