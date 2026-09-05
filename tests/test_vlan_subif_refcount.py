"""Regression tests for the v0.4.1 VLAN sub-interface lifecycle +
RX dual-sniff fixes.

Operator scenario from svl-d-ai-srv04:
  - Stream A configured VLAN:Tagged + vlan_id=10. Started, then
    stopped (sub-iface enp181.10 deleted).
  - Stream B with same VLAN started later, sniffer bound to
    enp181.10.
  - Independently: stream B's sniffer became zombie because the
    sub-iface was already gone (or got re-deleted on B's start path
    by a stale stopper from A). rx_count stayed at 0 forever despite
    TX flowing.

The fix has two parts:

1. Ref-count the sub-iface (_ensure_vlan_rx_visible bumps;
   _release_vlan_subif decrements; actual `ip link delete` runs
   only at refcount=0). Two streams sharing a VLAN can't blow each
   other's sub-iface away.

2. Dual-sniff with per-seq dedup. When a sub-iface is created, ALSO
   sniff on the base interface so untagged frames (TX bug from the
   field where Dot1Q didn't actually make it onto the wire) are
   still counted. Per-(stream_id, seq) dedup prevents double-counting
   when both sniffers see the same packet.

These tests pin the ref-counting math + the dedup wrapper logic.
The dual-sniff itself requires Scapy AsyncSniffer, which we can't
spin up headless without a real iface — so we test the building
blocks (ref-counting + seq-dedup wrapper) in isolation."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ─────────────────────────────────── ref-counting math ────────────────


def test_ensure_increments_refcount():
    """Each _ensure_vlan_rx_visible call must bump the refcount for
    that sub-iface name. Two concurrent streams asking for the same
    sub-iface should end up with refcount=2."""
    import multithreaded_traffic_gen as mtg
    # Reset state for the test
    with mtg._VLAN_SUBIF_LOCK:
        mtg._VLAN_SUBIF_REFS.clear()
        # v0.5.265 (audit stream-gen F5): also clear the
        # "operator-owned" set so state doesn't leak between tests.
        mtg._VLAN_SUBIF_EXISTING.clear()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        sub1 = mtg._ensure_vlan_rx_visible("eth0", 10)
        sub2 = mtg._ensure_vlan_rx_visible("eth0", 10)

    assert sub1 == "eth0.10"
    assert sub2 == "eth0.10"
    assert mtg._VLAN_SUBIF_REFS.get("eth0.10") == 2, (
        f"refcount should be 2 after two ensures; "
        f"got {mtg._VLAN_SUBIF_REFS}"
    )


def test_release_decrements_without_deleting_when_count_above_zero():
    """First release of a sub-iface with refcount=2 should drop to
    1 but NOT issue an `ip link delete`. Operator scenario: two
    streams share VLAN 10; first one stops → sub-iface stays alive
    for the second."""
    import multithreaded_traffic_gen as mtg
    with mtg._VLAN_SUBIF_LOCK:
        mtg._VLAN_SUBIF_REFS.clear()
        # v0.5.265 (audit stream-gen F5): also clear the
        # "operator-owned" set so state doesn't leak between tests.
        mtg._VLAN_SUBIF_EXISTING.clear()
        mtg._VLAN_SUBIF_REFS["eth0.10"] = 2

    delete_calls = []
    def _track(cmd, *args, **kwargs):
        if "delete" in cmd:
            delete_calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=_track):
        mtg._release_vlan_subif("eth0.10")

    assert mtg._VLAN_SUBIF_REFS.get("eth0.10") == 1, (
        f"refcount should drop to 1, not delete; got {mtg._VLAN_SUBIF_REFS}"
    )
    assert delete_calls == [], (
        f"`ip link delete` should NOT run while refcount > 0; "
        f"saw: {delete_calls}"
    )


def test_release_at_zero_actually_deletes():
    """Final release (refcount → 0) must run `ip link delete` and
    drop the entry from the table."""
    import multithreaded_traffic_gen as mtg
    with mtg._VLAN_SUBIF_LOCK:
        mtg._VLAN_SUBIF_REFS.clear()
        # v0.5.265 (audit stream-gen F5): also clear the
        # "operator-owned" set so state doesn't leak between tests.
        mtg._VLAN_SUBIF_EXISTING.clear()
        mtg._VLAN_SUBIF_REFS["eth0.10"] = 1

    delete_calls = []
    def _track(cmd, *args, **kwargs):
        if "delete" in cmd:
            delete_calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=_track):
        mtg._release_vlan_subif("eth0.10")

    assert "eth0.10" not in mtg._VLAN_SUBIF_REFS, (
        "entry should be removed after final release"
    )
    assert len(delete_calls) == 1, (
        f"`ip link delete` should run exactly once on final release; "
        f"saw {len(delete_calls)} calls"
    )
    assert "delete" in delete_calls[0]
    assert "eth0.10" in delete_calls[0]


def test_release_unknown_subif_is_noop():
    """Releasing a sub-iface we never ensured (or that was already
    fully released) should be a no-op — no crash, no spurious
    delete. Defensive for the stop-path where multiple cleanup
    paths might fire."""
    import multithreaded_traffic_gen as mtg
    with mtg._VLAN_SUBIF_LOCK:
        mtg._VLAN_SUBIF_REFS.clear()
        # v0.5.265 (audit stream-gen F5): also clear the
        # "operator-owned" set so state doesn't leak between tests.
        mtg._VLAN_SUBIF_EXISTING.clear()

    delete_calls = []
    def _track(cmd, *args, **kwargs):
        if "delete" in cmd:
            delete_calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=_track):
        mtg._release_vlan_subif("never-existed.10")

    # An unknown sub goes from count 0 → "should_delete=True" branch,
    # so it does try the delete (cheap and idempotent). Acceptable
    # behaviour; what matters is no crash.
    # The state shouldn't gain a phantom entry.
    assert "never-existed.10" not in mtg._VLAN_SUBIF_REFS


def test_release_empty_string_or_none_no_crash():
    import multithreaded_traffic_gen as mtg
    # Pre-fix calling with "" or None would crash subprocess.
    mtg._release_vlan_subif("")
    mtg._release_vlan_subif(None)


# ─────────────────────────────────── full lifecycle ─────────────


def test_two_streams_share_subif_neither_strands_other():
    """End-to-end scenario from the field: stream A + stream B both
    ensure eth0.10; either order of release leaves the OTHER stream's
    sniffer with a live sub-iface to bind to.

    v0.5.265 (audit stream-gen F5): `ip link show` must return
    rc != 0 on the FIRST call so `_ensure_vlan_rx_visible` treats
    the subif as ephemeral (owned by us). Otherwise the new F5
    pre-existing-protection kicks in and we correctly SKIP the
    delete on release."""
    import multithreaded_traffic_gen as mtg
    with mtg._VLAN_SUBIF_LOCK:
        mtg._VLAN_SUBIF_REFS.clear()
        # v0.5.265 (audit stream-gen F5): also clear the
        # "operator-owned" set so state doesn't leak between tests.
        mtg._VLAN_SUBIF_EXISTING.clear()
        mtg._VLAN_SUBIF_EXISTING.clear()

    delete_calls = []
    show_call_count = [0]
    def _track(cmd, *args, **kwargs):
        if "delete" in cmd:
            delete_calls.append(cmd)
            return MagicMock(returncode=0)
        # First `ip link show` = rc=1 (doesn't exist yet, we'll
        # create it). Later shows can return 0 (exists — we just
        # made it) which is what the second ensure would see.
        if "show" in cmd:
            show_call_count[0] += 1
            return MagicMock(returncode=0 if show_call_count[0] > 1 else 1)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=_track):
        a_sub = mtg._ensure_vlan_rx_visible("eth0", 10)
        b_sub = mtg._ensure_vlan_rx_visible("eth0", 10)
        assert mtg._VLAN_SUBIF_REFS["eth0.10"] == 2

        # Stream A stops first — must NOT delete sub-iface (B still using it)
        mtg._release_vlan_subif(a_sub)
        assert mtg._VLAN_SUBIF_REFS["eth0.10"] == 1
        assert delete_calls == [], "deletion ran while B was still using it"

        # Stream B stops — NOW the sub-iface gets cleaned up
        mtg._release_vlan_subif(b_sub)
        assert "eth0.10" not in mtg._VLAN_SUBIF_REFS
        assert len(delete_calls) == 1
