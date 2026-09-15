"""v0.5.337 — three v4→v6 DHCP parity fixes bundled.

Part 2 of 4 in the v4→v6 DHCP parity audit follow-up (v0.5.336-339).

Fixes:

**A. `_v6_probe_conflict` + `_v6_dad_poll` (Gap #2)** — mirror
v0.5.290/291 DAD-before-anchor for v6. `_ensure_ipv6_address` was
running `ip -6 addr add` blindly with no `dadfailed` poll. External
devices claiming the same v6 (relay agent IP on the same L2)
trigger DAD-failed but netgen never noticed → dnsmasq bound to a
dead address.

**B. v6 first-host ≠ gateway skip (Gap #3)** — mirror v0.5.287
Fix A for v6. `start_dhcp_server`'s direct-attached branch picked
`_hosts6[0]` unconditionally. When the operator's `ipv6_gateway`
IS the first host (common relay-agent shape — QFX irb.30 =
2001:db8:30::1, pool starts at ::100), netgen claimed the
gateway's IP on its own iface.

**C. DHCPv6 SOLICIT in-flight monitor gate (Gap #4)** — mirror
v0.5.229 audit B1 for v6. `_is_dhcp6c_running` helper added,
snapshot flips state to "Soliciting" when dhcp6c is running with
no lease yet, and dhcp_monitor treats "Soliciting" as
mid-handshake (skip restart). Pre-fix, a v6-only client mid-SOLICIT
looked like "No Lease" → monitor restarted every poll → SOLICIT
never converged.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def _monitor_src():
    return (_REPO / "utils" / "dhcp_monitor.py").read_text()


# -----------------------------------------------------------------
# A. DAD-before-anchor for v6
# -----------------------------------------------------------------

def test_A_marker_present():
    src = _dhcp_src()
    assert "v0.5.337 (audit dhcpv6-dad-before-anchor)" in src


def test_A_v6_probe_conflict_helper_defined():
    src = _dhcp_src()
    assert "def _v6_probe_conflict(" in src


def test_A_v6_dad_poll_helper_defined():
    src = _dhcp_src()
    assert "def _v6_dad_poll(" in src


def test_A_start_dhcp_server_calls_probe_before_anchor():
    """The order must be: probe → (skip on conflict OR anchor +
    post-poll). Cannot be probe-after-anchor (that's identical to
    just polling and doesn't prevent the bind race).

    Anchor to `def start_dhcp_server(` because the v0.5.337 marker
    string is intentionally reused between the helper docstrings
    and the call site — .index() would return the helper's first."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    # probe call site:
    probe_idx = body.index("_v6_probe_conflict(")
    # find the specific in-body _ensure_ipv6_address after the probe
    ensure_idx = body.index("_ensure_ipv6_address(", probe_idx)
    assert probe_idx < ensure_idx, (
        "v0.5.337: probe must happen BEFORE the anchor add, not after"
    )
    # And the poll happens after the successful add:
    poll_idx = body.index("_v6_dad_poll(", ensure_idx)
    assert ensure_idx < poll_idx


def test_A_probe_conflict_surfaces_via_dhcp_last_error():
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    # When _v6_probe_conflict returns non-empty, the code writes a
    # dhcp_last_error entry so the operator sees WHY the anchor
    # was refused.
    assert "if _v6_conflict:" in body
    assert '"dhcp_last_error"' in body


def test_A_probe_uses_ndisc6_with_ping6_fallback():
    """ndisc6 is the primary DAD probe (matches kernel semantics);
    `ping -6` is the fallback when ndisc6 isn't installed."""
    src = _dhcp_src()
    fn_idx = src.index("def _v6_probe_conflict(")
    body = src[fn_idx:fn_idx + 4000]
    assert '"ndisc6"' in body
    # Fallback path uses `ping -6` (v0.5.331 portable form).
    assert '"ping", "-6"' in body


def test_A_dad_poll_removes_on_dadfailed():
    src = _dhcp_src()
    fn_idx = src.index("def _v6_dad_poll(")
    body = src[fn_idx:fn_idx + 4000]
    assert '"dadfailed"' in body
    assert "_remove_ipv6_address" in body


# -----------------------------------------------------------------
# B. v6 first-host ≠ gateway skip
# -----------------------------------------------------------------

def test_B_marker_present():
    src = _dhcp_src()
    assert "v0.5.337 (audit dhcpv6-server-ip-gateway-collision)" in src


def test_B_iterates_hosts_and_skips_gateway():
    """Same shape as v0.5.287 Fix A on the v4 side — iterate
    hosts(), take first non-gateway. Straight `_hosts6[0]` grab
    must be gone."""
    src = _dhcp_src()
    idx = src.index("v0.5.337 (audit dhcpv6-server-ip-gateway-collision)")
    body = src[idx:idx + 4000]
    # New shape: for-loop iteration with gateway comparison.
    assert "for _h6 in _hosts6:" in body
    assert "_h6 != _gw6_addr" in body


def test_B_parses_ipv6_gateway_to_address():
    src = _dhcp_src()
    idx = src.index("v0.5.337 (audit dhcpv6-server-ip-gateway-collision)")
    body = src[idx:idx + 4000]
    assert "ipaddress.IPv6Address(ipv6_gateway)" in body


def test_B_warns_when_every_pool_host_is_the_gateway():
    """Degenerate case — /127 pool where every host equals the
    gateway. Must NOT crash; must log a warning and skip the anchor.

    Search for "non-gateway host" (without leading "no ") because
    the source literal is split across two adjacent string tokens
    with whitespace between; `.index` on the joined phrase would
    miss it in the source file."""
    src = _dhcp_src()
    idx = src.index("v0.5.337 (audit dhcpv6-server-ip-gateway-collision)")
    body = src[idx:idx + 4000]
    # Warning path must exist:
    assert "non-gateway host" in body


# -----------------------------------------------------------------
# C. DHCPv6 SOLICIT in-flight monitor gate
# -----------------------------------------------------------------

def test_C_marker_present_in_dhcp():
    src = _dhcp_src()
    assert "v0.5.337 (audit dhcpv6-monitor-in-flight-gate)" in src


def test_C_marker_present_in_monitor():
    src = _monitor_src()
    assert "v0.5.337 (audit dhcpv6-monitor-in-flight-gate)" in src


def test_C_is_dhcp6c_running_helper_defined():
    src = _dhcp_src()
    assert "def _is_dhcp6c_running(" in src


def test_C_is_dhcp6c_running_uses_whole_token_match():
    """Same anti-`eth1-matches-eth10` guard as v0.5.218 fix M for
    dhclient. Substring match with `pkill -f 'dhcp6c.*{interface}'`
    would false-positive `vlan1` for `vlan10`."""
    src = _dhcp_src()
    fn_idx = src.index("def _is_dhcp6c_running(")
    body = src[fn_idx:fn_idx + 2000]
    # Uses pgrep -a -f (like _is_dhclient_running).
    assert '["pgrep", "-a", "-f", "dhcp6c"]' in body
    # And matches interface as a whole argv token.
    assert "if interface in argv:" in body


def test_C_snapshot_flips_state_to_soliciting_when_dhcp6c_running():
    """Anchor to `def get_dhcp_client_snapshot(` — the v0.5.337
    marker appears twice (once in the `_is_dhcp6c_running` helper
    docstring, once at the snapshot's call site) and `.index()`
    would return the first, which is upstream of the snapshot code."""
    src = _dhcp_src()
    fn_idx = src.index("def get_dhcp_client_snapshot(")
    body = src[fn_idx:]
    # Marker must appear inside the snapshot function.
    assert "v0.5.337 (audit dhcpv6-monitor-in-flight-gate)" in body
    assert '"Soliciting"' in body
    # Gate: only override state when v6 is enabled + no v6 lease.
    assert "_v6_enabled" in body
    assert "not ip6_info" in body


def test_C_monitor_treats_soliciting_as_mid_handshake():
    """The monitor's DORA-in-flight gate must include "Soliciting"
    so a mid-SOLICIT v6 client doesn't get restarted."""
    src = _monitor_src()
    idx = src.index("v0.5.337 (audit dhcpv6-monitor-in-flight-gate)")
    body = src[idx:idx + 2000]
    assert '"Soliciting"' in body
    # And it stays in the _mid_handshake tuple.
    assert '_mid_handshake = _state in' in body


def test_C_v0_5_229_marker_still_intact():
    """The v4 audit-B1 marker must still be present — v0.5.337 is
    additive to that fix, not a replacement."""
    src = _monitor_src()
    assert "v0.5.229 (audit B1)" in src


# -----------------------------------------------------------------
# Cross-cutting
# -----------------------------------------------------------------

def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())


def test_monitor_ast_parses():
    import ast
    ast.parse(_monitor_src())


def test_v0_5_336_marker_still_intact():
    src = (_REPO / "utils" / "arp_monitor.py").read_text()
    assert "v0.5.336 (audit dhcpv6-monitor-replay-l3-remote)" in src


def test_v0_5_335_marker_still_intact():
    src = _dhcp_src()
    assert "v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)" in src


def test_v0_5_290_marker_still_intact():
    """v4-side DAD-before-anchor (v0.5.290) must still be in place —
    v0.5.337 is the v6 mirror, not a replacement."""
    src = _dhcp_src()
    assert "v0.5.290" in src


def test_v0_5_287_fix_a_marker_still_intact():
    """v4-side first-host-≠-gateway (v0.5.287 Fix A) must still be
    in place."""
    src = _dhcp_src()
    assert "v0.5.287" in src
