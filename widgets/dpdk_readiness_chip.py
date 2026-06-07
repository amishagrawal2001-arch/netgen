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
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import QLabel, QWidget


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 30_000


# v0.4.7: one-shot background thread that fetches /api/dpdk/status
# off the UI thread. Pre-v0.4.7 the chip called requests.get() inline
# in refresh() — a 5-sec timeout on the UI thread froze the whole
# event loop when the server was slow / unreachable, and changing
# TG selection felt sluggish because the chip's next poll would fire
# against the new (possibly unreachable) URL and block clicks.
#
# Operator-reported: "dpdk check is slow when moving selection from
# one TG to another TG". Symptom was the 5-sec timeout × any sluggish
# response = visible UI hitches mid-click. Fix: move the fetch to
# this thread; the chip emits a signal back to the UI thread when
# the payload arrives.
class _DpdkStatusFetchThread(QThread):
    """Fetch `/api/dpdk/status` off the UI thread, emit the payload."""

    payload_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, full_url: str, parent: Optional[QWidget] = None,
                 *, timeout_s: float = 5.0):
        super().__init__(parent)
        self._url = full_url
        self._timeout_s = timeout_s

    def run(self) -> None:
        try:
            r = requests.get(
                self._url, headers=_auth_headers(), timeout=self._timeout_s,
            )
        except Exception as exc:
            self.failed.emit(f"fetch failed: {exc}")
            return
        if r.status_code != 200:
            self.failed.emit(f"HTTP {r.status_code}")
            return
        try:
            payload = r.json() or {}
        except Exception as exc:
            self.failed.emit(f"json decode failed: {exc}")
            return
        self.payload_ready.emit(payload)


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
        # v0.4.7: in-flight fetch deduplication. Rapid TG-selection
        # changes (operator clicking through 4 TGs in 2 sec) used to
        # stack 4 synchronous fetches on the UI thread. Now refresh()
        # checks this guard and skips spawning a new thread if one
        # is still in flight — the in-flight one's result lands soon
        # enough that a fresh fetch is wasted.
        self._fetch_in_flight: Optional[_DpdkStatusFetchThread] = None
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
    def refresh(self, synchronous: bool = False) -> None:
        """Fetch /api/dpdk/status and repaint.

        v0.4.7: async by default. The fetch runs on a background
        QThread; the result lands back via signal on the UI thread.
        Operator-reported pre-v0.4.7: "dpdk check is slow when moving
        selection from one TG to another TG" — root cause was a
        synchronous `requests.get(..., timeout=5)` on the UI thread,
        which froze the Qt event loop for up to 5 sec when the
        server was sluggish or unreachable.

        ``synchronous=True`` preserves the old blocking path; used
        by tests that want to mock ``requests.get`` and assert the
        chip state immediately after refresh(). Production callers
        (timer, selection-change hook) should never pass it.
        """
        url = self._get_server_url() or ""
        if not url:
            self._paint("gray", "DPDK: —", "No server selected yet.")
            return

        full_url = f"{url.rstrip('/')}/api/dpdk/status"

        if synchronous:
            # Legacy blocking path — tests only. Production never
            # hits this because synchronous defaults to False.
            self._refresh_blocking(full_url)
            return

        # Dedupe: if a fetch is already in flight, skip. The in-flight
        # one's result will repaint the chip within a few seconds; a
        # second concurrent thread would just waste a connection.
        if self._fetch_in_flight is not None:
            return

        thread = _DpdkStatusFetchThread(full_url, parent=self)
        thread.payload_ready.connect(self._on_async_payload)
        thread.failed.connect(self._on_async_failed)
        # Self-cleanup. Both QThread cleanup AND clearing the dedup
        # guard fire on the SAME `finished` signal — Qt delivers
        # signal slots in connection order so deleteLater() runs
        # after _clear_in_flight (which only touches the bool).
        thread.finished.connect(self._clear_in_flight)
        thread.finished.connect(thread.deleteLater)
        self._fetch_in_flight = thread
        thread.start()

    def _refresh_blocking(self, full_url: str) -> None:
        """Pre-v0.4.7 sync path — kept for tests that monkeypatch
        ``requests.get``. Never called by production code."""
        try:
            r = requests.get(
                full_url, headers=_auth_headers(), timeout=5,
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

    # ─────────────────────────────── async result slots
    def _on_async_payload(self, payload: Dict[str, Any]) -> None:
        """Slot — runs on UI thread when the worker delivers JSON."""
        self._apply(payload)

    def _on_async_failed(self, reason: str) -> None:
        """Slot — silent on failure, just like the pre-v0.4.7 sync
        path. The chip holds its previous state so a flaky link
        doesn't flap the colour back to gray."""
        logger.debug(f"[DPDK CHIP] async fetch: {reason}")

    def _clear_in_flight(self) -> None:
        """Slot — runs on UI thread after the worker thread finishes
        (whether success or failure). Drops the dedup guard so the
        next refresh() can spawn a fresh worker."""
        self._fetch_in_flight = None

    def stop(self) -> None:
        """Stop the periodic poll and wait briefly for any in-flight
        worker thread to finish. v0.4.7: previously stop() only
        stopped the QTimer — any worker still running at chip
        destruction time triggered Qt's "QThread destroyed while
        still running" abort. Bounded 2-sec wait covers a normal
        HTTP timeout; if the worker is stuck longer, we'd rather
        emit one cleanup warning than block the app forever."""
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            t = self._fetch_in_flight
            if t is not None:
                # requestInterruption is advisory; the worker is just
                # a one-shot HTTP fetch with a 5-sec request timeout,
                # so we wait for natural completion. 2 sec is enough
                # for a healthy fetch + comfortably under the 5-sec
                # HTTP timeout for a stuck one.
                if hasattr(t, "requestInterruption"):
                    t.requestInterruption()
                if hasattr(t, "wait"):
                    t.wait(2000)
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
        # v0.5.18: populate the shared TTL cache so the Diagnostics
        # dialog / Make DPDK Ready / etc. can render instantly from
        # the same data the chip just polled.
        try:
            from traffic_client.dpdk_menu_actions import cache_dpdk_status
            # _server_url is the per-chip target; only cache when set.
            url = getattr(self, "_server_url", None)
            if url:
                cache_dpdk_status(url, payload)
        except Exception:
            pass
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

    # v0.5.18: lead with a one-line "what's missing" summary so
    # the operator doesn't have to read 5 rows to figure out the
    # problem. Examples:
    #   "Missing: DPDK libs, tx_worker — install required"
    #   "Missing: hugepages, IOMMU — most NICs need these"
    #   (omitted entirely if everything's fine)
    missing_items = []
    if not libdpdk:
        missing_items.append("DPDK libs")
    if not tx_worker:
        missing_items.append("tx_worker")
    if not hugepages:
        missing_items.append("hugepages")
    if not iommu:
        missing_items.append("IOMMU")
    if not vfio_pci:
        missing_items.append("vfio-pci")
    summary_line = ""
    if missing_items:
        summary_line = (
            f"Missing: {', '.join(missing_items)} — "
            f"click chip to open ★ Setup DPDK\n\n"
        )

    tip = summary_line + "DPDK readiness:\n" + "\n".join(
        f"  • {k}: {v}" for k, v in rows
    )
    # v0.3.11 fix #1: advertise the click-to-fix affordance.
    # v0.5.18: only repeat the call-to-action if we didn't already
    # surface it in the summary line.
    if not summary_line:
        tip += "\n\nClick this chip to open ★ Setup DPDK…"

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
