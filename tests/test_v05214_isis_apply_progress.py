"""v0.5.214: ISIS apply now shows a QProgressDialog and runs in
a background QThread — parity with OSPF (utils/devices_tab_
ospf.py:2563) and BGP (utils/devices_tab_bgp.py:2135).

Operator report on JNPR-MAC-HWXVX1 2026-08-23 (after v0.5.213
route-pool UI landed): "when applied isis config, apply config
progress bar is not visible similar to ospf and bgp." Pre-fix
`apply_isis_configurations` called `_apply_isis_to_devices` /
`_remove_isis_from_devices` synchronously in the UI thread —
each `requests.post(..., timeout=30)` blocked the whole client
for the duration, and there was no visual indication anything
was in flight.

Fix:
- New `_apply_isis_network` and `_remove_isis_network`
  helpers do the HTTP work only (no Qt calls) — safe to run
  on a background thread.
- New `ApplyISISWorker(QThread)` inside
  `apply_isis_configurations` runs them and emits a
  `finished(dict)` signal.
- `QProgressDialog("Applying ISIS configurations...", ...)`
  wraps the run.
- `_on_isis_apply_finished` UI-thread handler closes the
  dialog, refreshes the table, and shows the
  MultiDeviceResultsDialog(s) — all Qt calls stay on the
  main thread.
- Worker keepalive on `self._isis_apply_workers` mirrors
  OSPF's PyQt5 5.15.11 + Python 3.14 SIGABRT guard.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05214_test_{os.getpid()}.db"),
)


def _isis_source() -> str:
    return (REPO / "utils" / "devices_tab_isis.py").read_text()


# ─────────────────────────────────────────────────────────────────────
# apply_isis_configurations shows a QProgressDialog
# ─────────────────────────────────────────────────────────────────────

def test_apply_isis_shows_progress_dialog():
    src = _isis_source()
    idx = src.find("def apply_isis_configurations")
    assert idx >= 0, "apply_isis_configurations moved"
    body = src[idx:idx + 8000]
    assert "QProgressDialog" in body, (
        "apply_isis_configurations no longer shows a QProgressDialog — "
        "operator's original bug is back (UI freezes silently during apply)"
    )
    assert '"Applying ISIS configurations..."' in body \
        or 'Applying ISIS' in body, (
        "progress dialog label doesn't mention ISIS"
    )


def test_apply_isis_runs_in_qthread_worker():
    src = _isis_source()
    idx = src.find("def apply_isis_configurations")
    body = src[idx:idx + 8000]
    assert "class ApplyISISWorker" in body, (
        "ApplyISISWorker QThread class missing — apply blocks the UI thread"
    )
    assert "QThread" in body
    assert "pyqtSignal(dict)" in body
    assert "finished.connect" in body, (
        "worker's finished signal not connected — apply completion "
        "never returns to the UI thread to close the dialog"
    )


def test_apply_isis_worker_uses_network_helpers():
    src = _isis_source()
    idx = src.find("class ApplyISISWorker")
    assert idx >= 0
    body = src[idx:idx + 3000]
    assert "_apply_isis_network" in body, (
        "ApplyISISWorker.run() no longer calls _apply_isis_network — "
        "would fall back to UI-thread calls or skip apply entirely"
    )
    assert "_remove_isis_network" in body, (
        "ApplyISISWorker.run() no longer calls _remove_isis_network"
    )


# ─────────────────────────────────────────────────────────────────────
# Network helpers exist and don't touch Qt
# ─────────────────────────────────────────────────────────────────────

def test_apply_isis_network_helper_exists():
    src = _isis_source()
    assert "def _apply_isis_network(self, devices, server_url)" in src, (
        "_apply_isis_network helper missing — the worker has no thread-"
        "safe way to run the HTTP calls"
    )


def test_remove_isis_network_helper_exists():
    src = _isis_source()
    assert "def _remove_isis_network(self, devices, server_url)" in src


def test_network_helpers_return_dict_shape():
    """Both helpers must return a shape compatible with
    _on_isis_apply_finished (`{results, success_count, failed_count}`)."""
    src = _isis_source()
    for name in ("_apply_isis_network", "_remove_isis_network"):
        idx = src.find(f"def {name}(")
        assert idx >= 0
        body = src[idx:idx + 6000]
        assert '"results"' in body
        assert '"success_count"' in body
        assert '"failed_count"' in body


def test_network_helpers_dont_call_qt_ui():
    """These helpers run on the worker thread — must NOT call
    Qt dialogs or the table refresh. If they did, the whole
    apply-progress fix regresses to unsafe cross-thread Qt
    calls."""
    src = _isis_source()
    for name in ("_apply_isis_network", "_remove_isis_network"):
        idx = src.find(f"def {name}(")
        body = src[idx:idx + 6000]
        # Isolate this function's body (stop at next top-level def).
        next_def = body.find("\n    def ", 100)
        if next_def > 0:
            body = body[:next_def]
        assert "MultiDeviceResultsDialog" not in body, (
            f"{name} calls MultiDeviceResultsDialog from a worker thread — "
            "Qt dialogs must be on the UI thread only"
        )
        assert "self.update_isis_table" not in body, (
            f"{name} refreshes the table from a worker thread — "
            "table rebuild must be on the UI thread only"
        )


# ─────────────────────────────────────────────────────────────────────
# _on_isis_apply_finished does the UI work
# ─────────────────────────────────────────────────────────────────────

def test_on_isis_apply_finished_closes_progress_and_refreshes():
    src = _isis_source()
    idx = src.find("def _on_isis_apply_finished")
    assert idx >= 0, "_on_isis_apply_finished missing"
    body = src[idx:idx + 4000]
    assert "progress.close()" in body
    assert "self.update_isis_table()" in body, (
        "finished handler doesn't refresh the ISIS table"
    )
    assert "MultiDeviceResultsDialog" in body, (
        "finished handler doesn't show the results dialog"
    )


# ─────────────────────────────────────────────────────────────────────
# Worker keepalive list — guards against PyQt5's "QThread destroyed
# while running" SIGABRT (same reason OSPF pins `_ospf_apply_workers`)
# ─────────────────────────────────────────────────────────────────────

def test_worker_keepalive_list_present():
    src = _isis_source()
    idx = src.find("def apply_isis_configurations")
    body = src[idx:idx + 8000]
    assert "_isis_apply_workers" in body, (
        "no worker keepalive list — PyQt5 5.15.11 + Python 3.14 will "
        "abort with 'QThread: Destroyed while thread is still running'"
    )
