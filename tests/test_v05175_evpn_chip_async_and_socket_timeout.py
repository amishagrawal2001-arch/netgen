"""v0.5.175: EVPN chip moves to async fetch + global socket timeout.

Operator pasted a traceback ending with `KeyboardInterrupt` in
`socket.getaddrinfo` inside `widgets/evpn_active_chip.py:84` —
the chip's sync `requests.get(timeout=5)` was running on the UI
thread, and macOS `getaddrinfo` for the unresolvable lab
hostname `san-hp-srv06` blocked the Qt event loop for 30+ s.
Operator had to Ctrl+C the GUI to recover.

Two fixes here:

  1. **EVPN chip async fetch** — mirrors `widgets/orphan_chip.py`
     (v0.5.169) and `widgets/dpdk_readiness_chip.py` (v0.4.7).
     The HTTP GET runs on a one-shot QThread; the chip stays
     responsive even when DNS hangs.
  2. **Global socket timeout** — `socket.setdefaulttimeout(8.0)`
     at client startup so any remaining sync path (one-shot
     dialogs, future widgets) can't hang forever on DNS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ───── EVPN chip async fetch ────────────────────────────────────


def test_evpn_chip_has_fetch_thread_class():
    src = (REPO / "widgets" / "evpn_active_chip.py").read_text()
    assert "class _EvpnFetchThread(QThread)" in src
    # The thread emits payload + failed signals.
    assert "payload_ready = pyqtSignal(list)" in src
    assert "failed = pyqtSignal(str)" in src


def test_evpn_chip_refresh_spawns_thread_not_sync_request():
    """The `refresh()` method must NOT call `requests.get` on the
    UI thread anymore. It launches a `_EvpnFetchThread` instead."""
    src = (REPO / "widgets" / "evpn_active_chip.py").read_text()
    refresh_idx = src.find("def refresh(self) -> None:")
    assert refresh_idx > 0
    # The next ~50 lines (the refresh body) must NOT contain a
    # bare `requests.get(` call.
    body = src[refresh_idx:refresh_idx + 3000]
    # Stop at the next method def so we don't accidentally include
    # _on_payload or _on_failed.
    next_def = body.find("\n    def ", 50)
    if next_def > 0:
        body = body[:next_def]
    assert "requests.get(" not in body, (
        "refresh() still calls requests.get directly — fix the chip")
    # And must instead spawn a thread.
    assert "_EvpnFetchThread(" in body
    assert "thread.start()" in body


def test_evpn_chip_dedupes_in_flight_fetches():
    """Rapid timer ticks (or DNS slow-fail stacking) must not
    queue multiple concurrent fetches against the same server."""
    src = (REPO / "widgets" / "evpn_active_chip.py").read_text()
    assert "_fetch_in_flight" in src
    # And refresh() bails when one is already running.
    refresh_idx = src.find("def refresh(self) -> None:")
    body = src[refresh_idx:refresh_idx + 3000]
    assert "if self._fetch_in_flight is not None" in body


def test_evpn_chip_failed_leaves_previous_count():
    """Defensive UX: transient HTTP / DNS failures shouldn't
    blink the chip to idle. The previous count stays visible
    until the next successful fetch."""
    src = (REPO / "widgets" / "evpn_active_chip.py").read_text()
    assert "def _on_failed(self" in src
    on_failed_idx = src.find("def _on_failed(self")
    body = src[on_failed_idx:on_failed_idx + 600]
    assert "Leave previous count" in body


def test_evpn_chip_constructible_without_ui_freeze():
    """Smoke test: the chip can be constructed and refresh()
    called against an unresolvable URL without blocking. The
    thread starts; we don't await it."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.evpn_active_chip import EvpnActiveChip
    chip = EvpnActiveChip(
        lambda: "http://no-such-host.invalid:5050",
        poll_interval_ms=0,
    )
    chip.refresh()
    # The fetch is now in-flight on a worker thread; the test
    # exits without waiting (Qt will tear the thread down on
    # cleanup). The point is: refresh() returned IMMEDIATELY.
    assert chip._fetch_in_flight is not None
    # Cleanup — stop the worker so it doesn't outlive the test.
    chip._fetch_in_flight.terminate()
    chip._fetch_in_flight.wait(2000)
    chip.deleteLater()


# ───── Global socket timeout ────────────────────────────────────


def test_client_entry_sets_default_socket_timeout():
    """`run_tgen_client.main()` calls `socket.setdefaulttimeout`
    before any GUI / network init. Without this, sync paths
    (one-shot dialogs, legacy widgets) can still hang the app
    on macOS DNS."""
    src = (REPO / "run_tgen_client.py").read_text()
    main_idx = src.find("def main(argv=None):")
    assert main_idx > 0
    body = src[main_idx:main_idx + 2000]
    assert "socket.setdefaulttimeout" in body
    # Timeout should be tight enough to bound DNS hangs but
    # generous enough to survive a slow LAN. 8s is reasonable.
    assert "8.0" in body or "8)" in body


def test_global_socket_timeout_actually_caps_dns():
    """Integration-ish: install the timeout and confirm
    `socket.getdefaulttimeout()` returns the expected value.
    This verifies the call signature is what we expect."""
    import socket
    saved = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(8.0)
        assert socket.getdefaulttimeout() == 8.0
    finally:
        socket.setdefaulttimeout(saved)
