"""DPDK readiness chip — small status indicator for the main window.

Lives in the QMainWindow's status bar so the operator can tell at a
glance whether DPDK will work on the currently-selected server, BEFORE
they enable Use-DPDK on a stream and watch it silently fall back to
Scapy because hugepages aren't allocated.

Tri-state:
  * **green** — every subsystem is up (libdpdk, tx_worker binary,
    hugepages allocated, IOMMU on, vfio-pci loaded). The "Use DPDK"
    checkbox will actually do something.
  * **amber** — partial: tx_worker + libdpdk present, but missing
    hugepages / IOMMU / vfio-pci. Some NICs (mlx5) work without those
    so DPDK might still run; others will fail.
  * **red** — unusable: tx_worker binary missing or libdpdk not
    installed. Enabling Use-DPDK guarantees a fall-back.
  * **gray** — unknown: no server selected or HTTP failure. We don't
    badger the operator about a flaky link.

Polls ``/api/dpdk/status`` every 30 seconds on a slow timer; also
exposes ``refresh()`` so e.g. the bind/unbind flow can poke it.
Defensively quiet on HTTP failure — leaves the chip in its previous
state and logs a debug line, never a modal.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

import requests
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QWidget


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 30_000


class DpdkReadinessChip(QLabel):
    """One-glance readiness indicator.

    v0.3.11: clicking the chip emits ``clicked`` so the host (main
    window) can hook it up to open the Make DPDK Ready orchestrator.
    Cursor changes to pointing-hand to advertise the affordance —
    previously the chip looked clickable but did nothing.
    """

    # Emitted on left-click. Host wires this to the Make DPDK Ready
    # menu handler so a single chip click opens the fix flow.
    clicked = pyqtSignal()

    # Tri-state colour palettes — match the preflight bar's palette
    # convention so the two widgets feel related.
    _STATES = {
        "green":   {"bg": "#f0fdf4", "fg": "#166534", "border": "#bbf7d0"},
        "amber":   {"bg": "#fffbeb", "fg": "#b45309", "border": "#fcd34d"},
        "red":     {"bg": "#fef2f2", "fg": "#b91c1c", "border": "#fca5a5"},
        "gray":    {"bg": "#f1f5f9", "fg": "#475569", "border": "#cbd5e1"},
    }

    def __init__(self,
                 server_url_provider: Callable[[], Optional[str]],
                 parent: Optional[QWidget] = None,
                 *, poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS):
        super().__init__(parent)
        self._get_server_url = server_url_provider
        self._poll_interval_ms = int(poll_interval_ms)
        self._last_payload: Dict[str, Any] = {}
        self._state = "gray"
        self.setAlignment(Qt.AlignCenter)
        # v0.3.11: pointer cursor advertises clickability.
        self.setCursor(Qt.PointingHandCursor)
        self._paint("gray", "DPDK: —", "No server selected yet.")

        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_interval_ms or DEFAULT_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        if self._poll_interval_ms > 0:
            self._timer.start()
        # Kick a first refresh shortly after construction so the chip
        # shows real state instead of "—" on startup. Mirror's the
        # preflight bar's 300 ms singleShot.
        QTimer.singleShot(300, self.refresh)

    # ──────────────────────────────────────────────── public API
    def refresh(self) -> None:
        """Fetch /api/dpdk/status and repaint. Safe to call from any
        context — never raises, never blocks."""
        url = self._get_server_url() or ""
        if not url:
            self._paint("gray", "DPDK: —", "No server selected yet.")
            return
        try:
            r = requests.get(
                f"{url.rstrip('/')}/api/dpdk/status",
                headers=_auth_headers(), timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[DPDK CHIP] fetch failed: {exc}")
            return
        if r.status_code != 200:
            logger.debug(f"[DPDK CHIP] HTTP {r.status_code}")
            return
        try:
            payload = r.json() or {}
        except Exception:
            return
        self._apply(payload)

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass

    def state(self) -> str:
        """Current state tag for tests."""
        return self._state

    # ─────────────────────────────────────────────── internals
    def _apply(self, payload: Dict[str, Any]) -> None:
        self._last_payload = payload
        state, headline, tip = classify_dpdk_status(payload)
        self._state = state
        self._paint(state, headline, tip)

    def mousePressEvent(self, ev):
        """v0.3.11: emit clicked on left-click so the host can
        bridge to Make DPDK Ready. Right-click ignored — reserved
        for a future context menu (Refresh, Show details, etc).
        """
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def _paint(self, state: str, text: str, tooltip: str) -> None:
        palette = self._STATES.get(state, self._STATES["gray"])
        # Leading dot character so the colour-coded shape reads at
        # eye-glance distance even when the operator's not focused
        # on the bar.
        self.setText(f"●  {text}")
        self.setStyleSheet(
            f"background: {palette['bg']}; color: {palette['fg']}; "
            f"border: 1px solid {palette['border']}; "
            f"padding: 1px 10px; border-radius: 9px; "
            f"font-size: 11px; font-weight: 600;"
        )
        self.setToolTip(tooltip)


# ────────────────────────────────────────────────── classification
def classify_dpdk_status(payload: Dict[str, Any]) -> "tuple[str, str, str]":
    """Pure function — takes the /api/dpdk/status JSON and returns
    ``(state, headline, tooltip)``.

    ``state`` is one of ``"green" | "amber" | "red" | "gray"``. The
    headline is the short text the chip shows; the tooltip lists each
    subsystem's state so a hover answers "why amber?".

    The hard requirements for any DPDK usage are libdpdk + tx_worker;
    missing either is **red**. Hugepages / IOMMU / vfio-pci are
    "usually required" — many mlx5 (Mellanox) NICs work without them
    via the kernel driver, so we call that combination **amber** not
    red.
    """
    libdpdk = bool(payload.get("dpdk_installed"))
    tx_worker = bool(payload.get("tx_worker_exists"))
    hugepages = bool(payload.get("hugepages_configured"))
    iommu = bool(payload.get("iommu_enabled"))
    vfio_pci = bool(payload.get("vfio_pci_loaded"))

    # Detail rows for the tooltip — built incrementally so the order
    # is stable.
    rows = []
    libdpdk_label = "ok"
    if libdpdk and payload.get("dpdk_version"):
        libdpdk_label = f"ok (v{payload['dpdk_version']})"
    elif not libdpdk:
        libdpdk_label = "missing"
    rows.append(("DPDK libraries", libdpdk_label))

    tx_worker_label = "ok"
    if tx_worker and payload.get("tx_worker_built"):
        # v0.2.77: include the binary's build date so an operator
        # who upgraded libdpdk can spot the ABI staleness at a
        # glance ("tx_worker built 2024-06 against libdpdk 22.11,
        # now running 23.11" = rebuild required).
        tx_worker_label = f"ok (built {payload['tx_worker_built']})"
    elif not tx_worker:
        tx_worker_label = "missing"
    rows.append(("tx_worker binary", tx_worker_label))

    rows.append((
        "Hugepages",
        f"ok ({payload.get('hugepages_available', 0)} × "
        f"{payload.get('hugepage_size', '?')})" if hugepages else "not allocated"
    ))
    iommu_detail = payload.get("iommu_details") or ("on" if iommu else "off")
    rows.append(("IOMMU", iommu_detail))
    rows.append(("vfio-pci", "loaded" if vfio_pci else "not loaded"))

    # v0.3.11 fix #5: surface the bound-NIC count in the tooltip so
    # operators know their bound NICs aren't lost — they're just
    # invisible from psutil (and therefore the server tree) once
    # vfio-pci owns them. The bound list IS available via Tools →
    # DPDK → Status… and from /api/dpdk/interfaces.
    interfaces = payload.get("interfaces") or []
    bound_count = sum(
        1 for i in interfaces
        if isinstance(i, dict)
        and "vfio" in str(i.get("driver", "")).lower()
    )
    if bound_count:
        rows.append((
            "Bound NICs",
            f"{bound_count} (not shown in Server tree — Tools→DPDK→Status)",
        ))

    tip = "DPDK readiness:\n" + "\n".join(
        f"  • {k}: {v}" for k, v in rows
    )
    # v0.3.11 fix #1: advertise the click-to-fix affordance.
    tip += "\n\nClick this chip to open Make DPDK Ready…"

    if not libdpdk or not tx_worker:
        missing = []
        if not libdpdk:
            missing.append("libdpdk")
        if not tx_worker:
            missing.append("tx_worker")
        return ("red",
                f"DPDK: unavailable ({', '.join(missing)})",
                tip + "\n\nUse-DPDK on streams will silently fall back "
                      "to Scapy.")

    if hugepages and iommu and vfio_pci:
        return ("green", "DPDK: ready", tip)

    # libdpdk + tx_worker present, but at least one of the "usually
    # required" subsystems is missing. Mellanox / mlx5 NICs don't need
    # the others — call it degraded, not unusable.
    return ("amber", "DPDK: degraded",
            tip + "\n\nSome NICs (Mellanox / mlx5) work without "
                  "hugepages / vfio. Others won't — check the "
                  "specific interface state in Tools → DPDK.")


def _auth_headers() -> Dict[str, str]:
    tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}
