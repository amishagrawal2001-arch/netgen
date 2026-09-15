"""v0.5.339 — L3-remote detection uses the DEVICE's declared
`ipv6_address` as the source of truth, NOT the iface's runtime v6
subnets. Fixes chicken-and-egg where the stale anchor v0.5.335
was meant to sweep makes the iface CONTAIN the pool subnet, so
runtime-based detection flips to direct-attached and the sweep
never runs.

Operator on srv06 2026-09-15 (post-v0.5.338 upgrade):
- Device5: vlan20, ipv6=`2001:db8:20::2/64`, pool
  `2001:db8:30::100-1ff/64` (L3-remote per operator intent).
- Pre-fix state: `2001:db8:30::1/64` was stuck on vlan20 from a
  pre-v0.5.335 apply.
- arp_monitor's v0.5.336 replay probed vlan20's runtime subnets
  → saw both `2001:db8:20::/64` AND `2001:db8:30::/64` → thought
  pool overlaps → flipped `_pool_is_l3_remote_v6` False → RE-
  ANCHORED `2001:db8:30::1/64` on every tick, cementing the bug.
- start_dhcp_server had the same chicken-and-egg AND its v0.5.337
  DAD probe (`ping -6 -I vlan20 2001:db8:30::1`) reached self via
  the stale connected route and returned a false-positive
  "duplicate address" verdict → dnsmasq bind refused → nothing
  worked.

Fix: use `device_db.get_device(device_id)["ipv6_address"]` as the
authoritative subnet source in BOTH `start_dhcp_server` and
`arp_monitor._replay_dhcp_anchors`. Operator intent is invariant
to runtime anchor drift.

Also adds a tick-time sweep to arp_monitor's L3-remote branch —
existing devices with stale anchors get healed autonomously on
the next monitor tick, no manual Apply required.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def _arp_src():
    return (_REPO / "utils" / "arp_monitor.py").read_text()


def test_marker_present_in_dhcp():
    assert "v0.5.339 (audit dhcpv6-l3remote-authoritative-source)" in _dhcp_src()


def test_marker_present_in_arp_monitor():
    assert "v0.5.339 (audit dhcpv6-l3remote-authoritative-source)" in _arp_src()


# --- start_dhcp_server side ---

def test_start_dhcp_server_reads_device_row_ipv6_address():
    """The L3-remote detection must call `device_db.get_device(
    device_id)` and read `ipv6_address` — that's what makes it
    authoritative instead of runtime-corrupted."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    # Marker lands inside the function.
    assert "v0.5.339 (audit dhcpv6-l3remote-authoritative-source)" in body
    # device_db lookup + ipv6_address read.
    assert "device_db.get_device(device_id)" in body
    assert '_device_row.get("ipv6_address")' in body


def test_start_dhcp_server_uses_device_subnet_for_overlap():
    """The overlap check must compare pool_net against the DEVICE's
    subnet (built from device_db.ipv6_address), not iface's runtime
    subnets."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    # A device-subnet-based overlap must exist.
    assert "_pool_net.overlaps(_dev_net)" in body


def test_start_dhcp_server_falls_back_to_runtime_when_no_device_row():
    """When device_db has no row or the row has no ipv6_address
    (test harness / dry-run), the code must fall back to the
    v0.5.335 runtime-iface check. Regression guard so test callers
    without a real device_db don't crash."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    # The v0.5.335 runtime probe must still be reachable.
    assert "_parse_ipv6(interface, container=container)" in body


# --- arp_monitor side ---

def test_arp_monitor_reads_device_row_ipv6_address():
    src = _arp_src()
    marker = "v0.5.339 (audit dhcpv6-l3remote-authoritative-source)"
    idx = src.index(marker)
    body = src[idx:idx + 6000]
    # The device row is already the `_dev` loop variable — must
    # read ipv6_address from it.
    assert '_dev.get("ipv6_address")' in body


def test_arp_monitor_uses_device_subnet_for_overlap():
    """The overlap call may be line-broken in source, so check for
    `_pool_net6.overlaps(` followed later by `_dev_net` in the same
    block."""
    src = _arp_src()
    marker = "v0.5.339 (audit dhcpv6-l3remote-authoritative-source)"
    idx = src.index(marker)
    body = src[idx:idx + 6000]
    call_idx = body.index("_pool_net6.overlaps(")
    # `_dev_net` must be the argument (may be split across lines).
    after = body[call_idx:call_idx + 200]
    assert "_dev_net" in after


def test_arp_monitor_tick_sweep_removes_stale_pool_subnet_anchors():
    """The tick-time autonomous sweep must call `_remove_ipv6_address`
    for every non-link-local iface v6 address that falls in the pool
    subnet. Without this, a device that was applied pre-v0.5.335
    would still need a manual Apply to clean up — but v0.5.339 is
    supposed to heal it on the next arp_monitor tick."""
    src = _arp_src()
    # Sweep marker at the tick-time healing site.
    assert "v0.5.339 swept" in src
    # And uses _remove_ipv6_address from utils.dhcp.
    assert "_remove_ipv6_address as _rmv6" in src


def test_arp_monitor_sweep_uses_pool_net_membership():
    src = _arp_src()
    marker = "v0.5.339 swept"
    idx = src.index(marker)
    body = src[max(0, idx - 3000):idx + 500]
    # Membership check must use `_pool_net6` (not raw string compare).
    assert "in _pool_net6" in body


def test_arp_monitor_sweep_excludes_link_local():
    src = _arp_src()
    marker = "v0.5.339 swept"
    idx = src.index(marker)
    body = src[max(0, idx - 3000):idx + 500]
    assert "is_link_local" in body


# --- cross-cutting ---

def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())


def test_arp_ast_parses():
    import ast
    ast.parse(_arp_src())


def test_v0_5_335_and_v0_5_336_markers_intact():
    """v0.5.339 is a REFINEMENT of v0.5.335 and v0.5.336 — the
    original markers must still live in the codebase."""
    assert "v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)" in _dhcp_src()
    assert "v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)" in _arp_src()
