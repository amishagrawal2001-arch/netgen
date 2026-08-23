"""v0.5.203: Add-OSPF / Add-ISIS now refresh their protocol table
so the new config shows up immediately, and cellChanged still
persists inline edits afterwards.

Operator report: added OSPF config via the dialog, clicked Add,
saw nothing appear in the OSPF table. Confirmed via source
inspection: `widgets/devices_tab.py::_update_device_protocol`
was `pass` for OSPF and ISIS — updated `device_info["ospf_config"]`
but never called `update_ospf_table()`, so the table stayed
stale until a periodic tick (30-60s later, if ever).

Fix: mirror the BGP branch — disconnect+refresh+reconnect
BOTH the DevicesTab-level stub AND the real handler in
OSPFHandler/ISISHandler. Prevents the same "inline edits stop
persisting after the first protocol update" bug that BGP had
in v0.5.202.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05203_test_{os.getpid()}.db"),
)


def _protocol_branches_src():
    """Extract just the tail of _update_device_protocol where the
    refresh-per-protocol dispatch lives — anchor on the BGP
    disconnect (that's the unique marker for this dispatch)."""
    src = (REPO / "widgets/devices_tab.py").read_text()
    idx = src.find("self.bgp_table.cellChanged.disconnect()")
    assert idx > 0
    return src[idx:idx + 3000]


def test_ospf_branch_of_update_protocol_refreshes_table():
    """The OSPF branch of _update_device_protocol must call
    update_ospf_table() so the added config is visible to the
    operator immediately — not `pass`."""
    branch_src = _protocol_branches_src()
    assert "self.update_ospf_table()" in branch_src, (
        "OSPF branch of _update_device_protocol is not calling "
        "update_ospf_table — Add OSPF still won't show in the table."
    )


def test_ospf_reconnects_both_stub_and_real_handler():
    """After disconnect(), both slots must be reconnected — else
    OSPF row inline edits get the same disconnect-bug BGP hit."""
    branch_src = _protocol_branches_src()
    assert "self.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)" in branch_src
    assert "self.ospf_table.cellChanged.connect(self.ospf_handler.on_ospf_table_cell_changed)" in branch_src


def test_isis_branch_of_update_protocol_refreshes_table():
    branch_src = _protocol_branches_src()
    assert "self.update_isis_table()" in branch_src


def test_isis_reconnects_both_stub_and_real_handler():
    branch_src = _protocol_branches_src()
    assert "self.isis_table.cellChanged.connect(self.on_isis_table_cell_changed)" in branch_src
    assert "self.isis_table.cellChanged.connect(self.isis_handler.on_isis_table_cell_changed)" in branch_src


def test_bgp_fix_from_v05202_still_present():
    """Regression guard: v0.5.202's BGP fix should still be in
    place — both stub + real handler reconnect after disconnect."""
    branch_src = _protocol_branches_src()
    assert "self.bgp_table.cellChanged.connect(self.bgp_handler.on_bgp_table_cell_changed)" in branch_src
