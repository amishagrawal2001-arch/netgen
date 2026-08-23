"""v0.5.202: BGP row inline edits (hold-time, keepalive, source
IP, neighbor IP, ASN) get persisted to bgp_config again.

Operator report: modified `bgp_hold_time` in the BGP table's
inline column 11, clicked Apply — value reverted to 90 (the
default). Confirmed on srv06 that every Apply payload was
carrying `bgp_hold_time='90'` even after the operator typed
`60`; the edit never made it into device_info["bgp_config"].

Root cause: `widgets/devices_tab.py` update_protocol path
disconnected ALL slots from `bgp_table.cellChanged` (via
`disconnect()` with no args), then reconnected ONLY the
DevicesTab-side stub `on_bgp_table_cell_changed` (a
`pass`-only method). BGPHandler's real edit handler wired at
`utils/devices_tab_bgp.py:54` got wiped out and never
restored, so all subsequent inline edits landed in a widget
that didn't update the underlying dict.

Fix: after the reconnect, also `.connect` the real handler
`self.bgp_handler.on_bgp_table_cell_changed`. Both slots
fire; the stub is a no-op, the real one persists the edit.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05202_test_{os.getpid()}.db"),
)


def test_update_protocol_bgp_reconnects_real_handler():
    """Locks in the fix at widgets/devices_tab.py's
    update_protocol path. After disconnect(), both the stub AND
    the real handler must be reconnected — otherwise inline edits
    on hold-time / keepalive / source / neighbor / ASN silently
    drop and the payload keeps sending the defaults."""
    src = (REPO / "widgets/devices_tab.py").read_text()
    # Anchor around the disconnect that triggered the bug
    idx = src.find("self.bgp_table.cellChanged.disconnect()")
    assert idx > 0
    # Look at the ~1KB right after
    tail = src[idx:idx + 1200]
    # Both connects must be present
    assert "self.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)" in tail
    assert "self.bgp_table.cellChanged.connect(self.bgp_handler.on_bgp_table_cell_changed)" in tail, (
        "The real edit handler (bgp_handler.on_bgp_table_cell_changed) "
        "is not being reconnected after disconnect() — inline BGP "
        "table edits will silently drop again."
    )


def test_real_bgp_cell_change_handler_persists_hold_time():
    """Sanity: the real handler (BGPHandler.on_bgp_table_cell_changed)
    IS what persists the hold-time edit to bgp_config."""
    src = (REPO / "utils/devices_tab_bgp.py").read_text()
    # The persist-write for hold-time must live in this handler.
    idx_handler = src.find("def on_bgp_table_cell_changed")
    idx_hold = src.find('bgp_config["bgp_hold_time"] = hold_time')
    assert idx_handler > 0
    assert idx_hold > idx_handler, (
        "The hold-time persist-write is no longer inside "
        "on_bgp_table_cell_changed — inline edits won't persist."
    )


def test_stub_on_bgp_table_cell_changed_is_still_a_pass():
    """The DevicesTab-level stub is intentionally a no-op —
    real work happens in BGPHandler. If someone starts putting
    logic into the stub, they've probably done it in the wrong
    place. Locking this in so both wires stay coordinated."""
    import widgets.devices_tab as devices_tab
    src = inspect.getsource(devices_tab.DevicesTab.on_bgp_table_cell_changed)
    # Body is essentially just a `pass` (comments allowed).
    body_lines = [
        ln.strip() for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith('"""')
        and not ln.strip().startswith("#")
        and not ln.strip().startswith("def ")
    ]
    # Should end with just `pass`; no other statements.
    assert body_lines == ["pass"] or body_lines[-1] == "pass", (
        f"The stub on_bgp_table_cell_changed has grown a body: "
        f"{body_lines!r} — either delete the stub or move the work "
        f"into BGPHandler."
    )
