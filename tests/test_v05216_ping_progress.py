"""v0.5.216: Ping test in Devices tab now shows a
QProgressDialog with per-device progress and runs in a
QThread — parity with OSPF/BGP/ISIS Apply.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: "also add a
progress for ping test under devices tab." Pre-fix
`ping_selected_device` ran the per-device ping loop
synchronously in the UI thread. Each `requests.post(...,
timeout=15)` blocked the client, so pinging N devices froze
the whole app for up to N*15 s with no visual feedback.

Fix:
- Extract the ping loop into a `PingWorker(QThread)` with
  `progress = pyqtSignal(int, str)` (index, device_name) and
  `finished = pyqtSignal(list, int, int, int)` (results,
  success, failed, arp_not_resolved).
- Show a determinate `QProgressDialog(0..N)` with a Cancel
  button. Ping is safe to interrupt (no server-side state
  left half-configured), so Cancel is enabled unlike Apply.
- The finished handler closes the progress dialog and shows
  the same `MultiDeviceResultsDialog` the sync path used.
- Worker keepalive on `self._ping_workers` mirrors the
  PyQt5 5.15.11 + Python 3.14 SIGABRT guard that OSPF/BGP/
  ISIS use.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05216_test_{os.getpid()}.db"),
)


def _ping_body() -> str:
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    idx = src.find("def ping_selected_device")
    assert idx >= 0, "ping_selected_device moved"
    # Next top-level def marks the end.
    tail = src[idx:]
    end = tail.find("\n    def ", 100)
    return tail[:end] if end > 0 else tail


def test_ping_shows_progress_dialog():
    body = _ping_body()
    assert "QProgressDialog" in body, (
        "ping_selected_device no longer shows a QProgressDialog — "
        "operator's original UX complaint is back"
    )
    # Determinate progress: max = total_devices, not the
    # infinite (0, 0) form Apply uses. Ping duration is
    # bounded so a real progress bar is more useful.
    assert re.search(r"QProgressDialog\([^)]+,\s*0\s*,\s*total_devices", body), (
        "progress dialog is no longer determinate (0..total_devices) — "
        "regressed to infinite spinner"
    )


def test_ping_runs_in_qthread_worker():
    body = _ping_body()
    assert "class PingWorker" in body, (
        "PingWorker QThread class missing — ping blocks the UI thread"
    )
    assert "QThread" in body
    assert "pyqtSignal(list, int, int, int)" in body, (
        "finished signal shape changed — result dialog dispatch depends on it"
    )
    assert "pyqtSignal(int, str)" in body, (
        "progress signal shape changed — per-device index+name update is gone"
    )


def test_ping_cancel_button_wired():
    body = _ping_body()
    # Cancel button is intentional for ping (unlike apply);
    # canceled.connect must wire to a stop callback that flips
    # the worker's stop flag.
    assert "canceled.connect" in body, (
        "Cancel button not wired — operator can't abort a long ping run"
    )
    assert "worker.stop()" in body, (
        "Cancel doesn't call worker.stop() — the flag never flips"
    )


def test_ping_worker_checks_stop_flag_in_loop():
    body = _ping_body()
    # The stop check must be inside the per-device loop, not
    # only at the top of run(); otherwise cancel takes effect
    # only after the whole batch finishes (defeating the point).
    idx = body.find("class PingWorker")
    assert idx >= 0
    worker_body = body[idx:idx + 4000]
    run_idx = worker_body.find("def run(self):")
    assert run_idx >= 0
    run_body = worker_body[run_idx:run_idx + 3500]
    assert "for idx, job in enumerate(self.jobs):" in run_body
    assert "if self._should_stop:" in run_body, (
        "worker's per-iteration stop check is missing — Cancel "
        "won't actually stop until the whole batch finishes"
    )


def test_ping_finished_handler_closes_progress_and_shows_dialog():
    body = _ping_body()
    # The wired _on_finished callback must close the progress
    # dialog and show MultiDeviceResultsDialog.
    assert "def _on_finished(results, success, failed, arp_missing):" in body
    finished_idx = body.find("def _on_finished")
    finished_body = body[finished_idx:finished_idx + 2000]
    assert "progress.close()" in finished_body
    assert "MultiDeviceResultsDialog(" in finished_body


def test_ping_worker_keepalive_list():
    body = _ping_body()
    assert "_ping_workers" in body, (
        "no worker keepalive list — PyQt5 5.15.11 + Python 3.14 "
        "will abort with 'QThread: Destroyed while thread is still running'"
    )


def test_ping_progress_dialog_shows_per_device_label():
    """The progress dialog must update its label as each device
    is pinged (e.g., 'Pinging 3 of 5: device2') — otherwise the
    dialog is basically a spinner with no useful info."""
    body = _ping_body()
    assert "progress.setLabelText" in body, (
        "progress dialog label never updates — operator sees a static "
        "'Pinging N devices...' with no per-device feedback"
    )
    assert '"Pinging {idx + 1} of {total_devices}: {device_name}"' in body \
        or 'Pinging {idx + 1} of {total_devices}' in body, (
        "per-device progress label format changed — verify it still "
        "communicates progress"
    )
