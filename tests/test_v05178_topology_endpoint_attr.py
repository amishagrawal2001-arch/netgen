"""v0.5.178: Topology dialog's _append_run_log_entry must read
ep.device, NOT ep.hca.

Crash on srv06:

    File ".../widgets/rdma_topology_dialog.py", line 1725, in _append_run_log_entry
        key = (side, ep.tg_url, ep.hca)
    AttributeError: 'RdmaTopologyEndpoint' object has no attribute 'hca'

The dataclass field is `device` (defined in utils/rdma_topology.py).
Pre-fix code read a non-existent `hca` attribute. Every Topology
test crashed on the first poll response after Start.

This test reproduces the bug at the dataclass + closure level
without dragging in the whole Qt dialog. If anyone reintroduces
`ep.hca` in the dedup loop, this test catches it.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_topology import RdmaTopologyEndpoint


def test_endpoint_has_device_not_hca():
    """Pin the dataclass schema. If someone renames `device` to
    `hca` (or back), they must update the dialog too."""
    ep = RdmaTopologyEndpoint(
        tg_url="http://srv:5050", device="mlx5_0",
        ib_port=1, gid_index=3,
    )
    assert ep.device == "mlx5_0"
    assert not hasattr(ep, "hca"), (
        "RdmaTopologyEndpoint grew an `hca` attribute — update "
        "the Topology dialog dedup loop too.")


def test_dialog_source_uses_device_not_hca_in_dedup_loop():
    """Grep-style guard against regressing the fix. We pin the
    EXACT shape of the line that crashed on srv06 so a future
    refactor can't silently put `ep.hca` back.

    Strip comment lines first — the fix block carries a
    "pre-fix code read ep.hca" note that would false-positive a
    naive substring scan."""
    src = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "ep.hca" not in code, (
        "ep.hca reintroduced as live code in rdma_topology_"
        "dialog.py — this WILL AttributeError on Topology "
        "Start (see srv06 crash 2026-06-17). Use ep.device.")
    # The fix's anchor must still be there — assert at least one
    # `ep.device` access in the file so a future cleanup doesn't
    # remove it without also removing the dedup loop.
    assert "ep.device" in code


def test_dedup_loop_simulation():
    """Mirror the dedup logic shape with real endpoints and
    confirm the (side, tg_url, device) key forms cleanly."""
    eps = [
        RdmaTopologyEndpoint(
            tg_url="http://srv:5050", device="mlx5_0"),
        RdmaTopologyEndpoint(
            tg_url="http://srv:5050", device="mlx5_0"),  # dup
        RdmaTopologyEndpoint(
            tg_url="http://srv:5050", device="mlx5_3"),
    ]
    seen = set()
    deduped = []
    for ep in eps:
        key = ("server", ep.tg_url, ep.device)  # the fixed access
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ep)
    assert len(deduped) == 2
    assert {ep.device for ep in deduped} == {"mlx5_0", "mlx5_3"}
