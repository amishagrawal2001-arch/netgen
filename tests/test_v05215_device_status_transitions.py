"""v0.5.215: Device Status column transitions from Starting →
Running reliably; refresh reflects DB truth even on
client-side timeout.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: stopped a
device via "Stop Selected Devices", protocols correctly
showed stopped. Started via "Start Selected Devices",
protocols came back up — but the device row's Status column
stayed on the yellow "Starting..." dot indefinitely. Manual
Refresh revealed the row was actually "Stopped" (though
protocols were green).

Two overlapping bugs made the UI stale:

1. **Worker didn't sync in-memory Status before emitting.**
   `DeviceOperationWorker.run()` emitted the
   `device_status_updated(row, "Starting", ...)` signal but
   didn't flip `device_info["Status"]` to match. The periodic
   `poll_device_status` (line ~10058) only refreshes rows
   whose in-memory Status is "Running" or "Starting" — if
   Status was still "Stopped" from the prior stop, the poll
   skipped the row and the "Starting..." row text lingered.

2. **`_on_device_operation_finished` gated the DB refresh
   on `if successful_count > 0`.** On a 30 s client-side
   timeout the POST raised → failed_count++, successful_count
   stayed 0 → NO `_refresh_device_table_from_database` call →
   the row never got to see the true DB state. The server may
   have completed the start and written status="Running" but
   the client never asked.

Fixes:
- Worker sets `device_info["Status"] = "Starting"` /
  `"Stopping"` synchronously before emitting.
- `poll_device_status` also picks up "Stopping" (not just
  "Starting"/"Running") so both transients get refreshed.
- `_on_device_operation_finished` always kicks a DB refresh,
  success or fail. Protocol-tab + DHCP refreshes still gated
  on success.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05215_test_{os.getpid()}.db"),
)


def _devices_tab_src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


# ─────────────────────────────────────────────────────────────────────
# Fix 1: worker syncs in-memory Status before emit
# ─────────────────────────────────────────────────────────────────────

def test_start_worker_updates_device_info_status_before_emit():
    """The `device_info["Status"] = "Starting"` line must appear
    BEFORE the `device_status_updated.emit(row, "Starting", ...)`
    line in the 'start' branch of DeviceOperationWorker.run()."""
    src = _devices_tab_src()
    idx = src.find("if self.operation_type == 'start':")
    assert idx >= 0, "start branch marker moved"
    body = src[idx:idx + 2500]
    starting_write_idx = body.find('device_info["Status"] = "Starting"')
    starting_emit_idx = body.find('device_status_updated.emit(row, "Starting"')
    assert starting_write_idx > 0, (
        "start worker no longer flips device_info Status before emit — "
        "poll_device_status will skip the row and 'Starting...' text "
        "will linger"
    )
    assert starting_emit_idx > 0
    assert starting_write_idx < starting_emit_idx, (
        "device_info Status write must land BEFORE the emit (Qt "
        "signal is queued but the poll reads the dict directly)"
    )


def test_stop_worker_updates_device_info_status_before_emit():
    """Same discipline for the stop branch."""
    src = _devices_tab_src()
    idx = src.find("elif self.operation_type == 'stop':")
    assert idx >= 0
    body = src[idx:idx + 2500]
    stopping_write_idx = body.find('device_info["Status"] = "Stopping"')
    stopping_emit_idx = body.find('device_status_updated.emit(row, "Stopping"')
    assert stopping_write_idx > 0, (
        "stop worker no longer flips device_info Status before emit"
    )
    assert stopping_emit_idx > 0
    assert stopping_write_idx < stopping_emit_idx


# ─────────────────────────────────────────────────────────────────────
# Fix 2: poll picks up transient Stopping too
# ─────────────────────────────────────────────────────────────────────

def test_poll_device_status_refreshes_stopping_too():
    """The poll's rows_to_refresh must include Status="Stopping",
    not just "Starting"/"Running" — otherwise a stop that got
    wedged in the transient state stays wedged forever."""
    src = _devices_tab_src()
    idx = src.find("def poll_device_status")
    assert idx >= 0
    body = src[idx:idx + 2000]
    # New form uses `status in ("Starting", "Stopping")`.
    assert re.search(r'status\s+in\s+\(\s*["\']Starting["\']\s*,\s*["\']Stopping["\']', body), (
        "poll_device_status no longer picks up 'Stopping' — "
        "transient wedge would stay orange forever"
    )


# ─────────────────────────────────────────────────────────────────────
# Fix 3: DB refresh always fires, not gated on success
# ─────────────────────────────────────────────────────────────────────

def test_on_device_operation_finished_always_refreshes_db():
    """The `_refresh_device_table_from_database(selected_rows)`
    call must NOT be inside the `if successful_count > 0:`
    branch — on a client-side timeout success_count stays 0
    and the row never re-syncs with DB truth."""
    src = _devices_tab_src()
    idx = src.find("def _on_device_operation_finished")
    assert idx >= 0
    body = src[idx:idx + 4000]
    # The refresh call must appear at unconditional indent
    # (outside any `if successful_count > 0:` block). Find both
    # markers and verify the refresh comes BEFORE the success
    # gate.
    refresh_idx = body.find("_refresh_device_table_from_database")
    success_gate_idx = body.find("if successful_count > 0:")
    assert refresh_idx > 0, "_refresh_device_table_from_database call missing"
    assert success_gate_idx > 0, "success-gate landmark missing (test needs updating)"
    assert refresh_idx < success_gate_idx, (
        "DB refresh is still inside/after the success-gate — on "
        "client-side timeouts the row won't re-sync with DB truth "
        "and 'Starting...' will linger"
    )
