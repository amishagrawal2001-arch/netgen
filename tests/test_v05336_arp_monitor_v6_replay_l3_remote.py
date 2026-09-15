"""v0.5.336 — arp_monitor's v6 anchor replay skips L3-remote pools
(relay mode), mirroring the v0.5.335 carve-out in start_dhcp_server.

**Regression guard for v0.5.335.** Without this, the arp_monitor's
tick would re-anchor a pool-subnet IP onto the server's iface every
minute — silently undoing the v0.5.335 sweep that ran at Apply
time. Any relay-mode DHCPv6 server (like srv06 device5) would
regress within one poll interval.

The v4 side has had the equivalent `relay_return_hop` short-circuit
via `_ensure_ipv4_address`'s internal guard since v0.5.284 (ARP-J5)
+ v0.5.295. The v6 path had no such guard because `_ensure_ipv6_
address` is a thin wrapper with no relay-mode awareness. The
replay caller needs to gate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _arp_monitor_src():
    return (_REPO / "utils" / "arp_monitor.py").read_text()


def test_marker_present():
    src = _arp_monitor_src()
    assert "v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)" in src


def test_replay_uses_pool_net_overlaps_for_l3_remote_check():
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 6000]
    # Must build IPv6Network for the pool and iface subnets, then
    # use .overlaps() — a bare string prefix compare would mis-
    # identify canonical vs uncompressed forms.
    assert "IPv6Network" in body
    assert "_pool_net6.overlaps(_n)" in body


def test_replay_excludes_link_local_from_iface_subnets():
    """fe80::/10 is kernel-managed and always present — including
    it in the iface subnet set would flip every device to
    "direct-attached" and defeat the guard. Must skip link-local."""
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 6000]
    assert "is_link_local" in body


def test_replay_skips_anchor_when_l3_remote():
    """When `_pool_is_l3_remote_v6` is True, the replay must NOT
    call `_ensure_ipv6_address`. The regression this guards against
    is exactly that: v0.5.335 sweeps the stale anchor at Apply, and
    then this replay used to add it right back on the next tick."""
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 8000]
    # Locate `if _pool_is_l3_remote_v6:` and inspect the branch.
    remote_if = body.index("if _pool_is_l3_remote_v6:")
    # The branch must end with `continue` (loop-skip) — not fall
    # through — so the _ensure_ipv6_address call below never fires
    # for relay-mode devices.
    branch_tail = body[remote_if:remote_if + 800]
    assert "continue" in branch_tail
    # And no `_ensure_ipv6_address(` inside the branch.
    end_of_branch = branch_tail.index("continue")
    assert "_ensure_ipv6_address" not in branch_tail[:end_of_branch]


def test_replay_still_anchors_direct_attached():
    """Direct-attached devices (pool subnet overlaps iface's own
    subnet) must still hit the `_ensure_ipv6_address` replay — the
    v0.5.309 anchor-drift-recovery reason is real for them."""
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 8000]
    # Both the L3-remote skip and the anchor call must exist.
    assert "_ensure_ipv6_address(" in body


def test_uses_parse_ipv6_helper_from_utils_dhcp():
    """Use the same `_parse_ipv6` helper that start_dhcp_server uses.
    Two parsers → subtle drift the day one is fixed and the other
    isn't."""
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 6000]
    assert "from utils.dhcp import _parse_ipv6" in body


def test_v0_5_335_marker_still_intact():
    """The sister-file fix (v0.5.335 relay-mode carve-out in
    start_dhcp_server) must still be in place — v0.5.336 depends
    on the same L3-remote pattern being present in the codebase."""
    src = (_REPO / "utils" / "dhcp.py").read_text()
    assert "v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)" in src


def test_comment_names_regression_risk():
    """Comment must explicitly say the pre-fix behavior would
    silently regress v0.5.335 — future maintainers need to see
    WHY this guard exists, not just what it does."""
    src = _arp_monitor_src()
    idx = src.index("v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)")
    body = src[idx:idx + 3000]
    assert "v0.5.335" in body
    assert "regress" in body.lower() or "undoes" in body.lower() or "undo" in body.lower()


def test_arp_monitor_ast_parses():
    import ast
    ast.parse(_arp_monitor_src())
