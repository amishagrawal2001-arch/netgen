"""v0.5.338 — v4→v6 DHCP parity fixes, MED-severity bundle.

Part 3 of 4 in the v4→v6 DHCP parity audit follow-up (v0.5.336-339).

Fixes:

**A. Stale-dhcp6c sweep (Gap #5)** — mirror v0.5.240's
`_kill_stale_dhclients` for dhcp6c. Pre-fix, the v6 client path
did a bare `pkill -f "dhcp6c.*{interface}"` — same unanchored
substring match v0.5.218 fix M rejected (vlan1 matches vlan10).
Also a single pkill doesn't reliably clear orphaned dhcp6c
processes across Restart cycles.

**B. v6 VRF connected-route post-check (Gap #7)** — mirror
v0.5.275's `_vrf_has_connected_route` for v6. When the kernel
skips auto-installing the connected route for a VRF-slaved
interface, dnsmasq binds successfully but every egress from the
VRF fails silently.

**C. v6 local-table `/128` host-route probe (Gap #8)** — mirror
v0.5.282's local-table probe for v6. When the kernel skips
`local <ip>/128 dev lo table local`, inbound frames to the
server's own v6 get treated as martian and dropped. Rare on
modern kernels but silently lethal.

Note: Gap #6 (v6 anchor failure surfacing via `dhcp_last_error`)
was folded into v0.5.337 as part of the DAD-before-anchor rewrite
— the `if not _v6_anchor_ok:` branch surfaces the error the same
way v0.5.222 does for v4. Not repeated here.
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


# -----------------------------------------------------------------
# A. Stale-dhcp6c sweep
# -----------------------------------------------------------------

def test_A_marker_present():
    src = _dhcp_src()
    assert "v0.5.338 (audit dhcpv6-stragglers-sweep)" in src


def test_A_kill_stale_dhcp6c_helper_defined():
    src = _dhcp_src()
    assert "def _kill_stale_dhcp6c(" in src


def test_A_kill_stale_dhcp6c_uses_whole_token_match():
    """Same anti-`vlan1-matches-vlan10` guard as v0.5.218 fix M for
    dhclient. Substring match with `pkill -f 'dhcp6c.*{interface}'`
    would false-positive."""
    src = _dhcp_src()
    fn_idx = src.index("def _kill_stale_dhcp6c(")
    body = src[fn_idx:fn_idx + 4000]
    assert '["pgrep", "-a", "-f", "dhcp6c"]' in body
    # Whole-token argv match.
    assert "if interface in argv:" in body
    # SIGKILL survivors (same as v0.5.240's dhclient sweep).
    assert '["kill", "-9"' in body


def test_A_start_dhcp_client_calls_stale_sweep():
    """The bare `pkill -f "dhcp6c.*{interface}"` must be REPLACED
    with `_kill_stale_dhcp6c(...)`. Regression guard so the
    unanchored substring form doesn't creep back in."""
    src = _dhcp_src()
    # The buggy shape must be gone from the start_dhcp_client path.
    assert 'pkill", "-f", f"dhcp6c.*{interface}"' not in src, (
        "buggy pkill substring form still present"
    )
    # And the new helper is called.
    assert "_kill_stale_dhcp6c(interface" in src


# -----------------------------------------------------------------
# B. v6 VRF connected-route post-check
# -----------------------------------------------------------------

def test_B_marker_present():
    src = _dhcp_src()
    assert "v0.5.338 (audit dhcpv6-vrf-connected-route-check)" in src


def test_B_vrf_has_connected_route_v6_helper_defined():
    src = _dhcp_src()
    assert "def _vrf_has_connected_route_v6(" in src


def test_B_helper_uses_ip_dash_6_route_show():
    """Must query the VRF's IPv6 table specifically with `ip -6
    route show <subnet> vrf <name>` — not just `ip route show`
    which returns v4."""
    src = _dhcp_src()
    fn_idx = src.index("def _vrf_has_connected_route_v6(")
    body = src[fn_idx:fn_idx + 2000]
    assert '"ip", "-6", "route", "show"' in body
    assert '"vrf", vrf_name' in body


def test_B_call_site_installs_route_when_missing():
    """When the probe returns False, the code must install the
    route explicitly. `ip -6 route add <net> dev <if> proto kernel
    metric 256 vrf <name>` — matches v4 shape modulo the -6 flag.

    Anchor to `def start_dhcp_server(` because the marker appears
    twice (helper docstring + call site) and `.index()` returns the
    first."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    assert "_vrf_has_connected_route_v6(" in body
    assert '"ip", "-6", "route", "add"' in body
    assert '"vrf", _vrf_v6' in body


# -----------------------------------------------------------------
# C. v6 local-table /128 host-route probe
# -----------------------------------------------------------------

def test_C_marker_present():
    src = _dhcp_src()
    assert "v0.5.338 (audit dhcpv6-local-host-route-check)" in src


def test_C_local_host_route_helper_defined():
    src = _dhcp_src()
    assert "def _v6_has_local_host_route(" in src


def test_C_helper_queries_local_table_with_128_prefix():
    src = _dhcp_src()
    fn_idx = src.index("def _v6_has_local_host_route(")
    body = src[fn_idx:fn_idx + 2000]
    assert '"table", "local"' in body
    assert "/128" in body


def test_C_call_site_installs_local_when_missing():
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    assert "_v6_has_local_host_route(" in body
    # Install shape: `ip -6 route add local <ip>/128 dev <if> table local`.
    assert '"local", f"{_v6_ip}/128"' in body
    assert '"table", "local"' in body


# -----------------------------------------------------------------
# Cross-cutting
# -----------------------------------------------------------------

def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())


def test_v0_5_337_markers_still_intact():
    """v0.5.337's three fixes must still be present — v0.5.338 is
    additive to the DAD/gateway/monitor work."""
    src = _dhcp_src()
    assert "v0.5.337 (audit dhcpv6-dad-before-anchor)" in src
    assert "v0.5.337 (audit dhcpv6-server-ip-gateway-collision)" in src
    assert "v0.5.337 (audit dhcpv6-monitor-in-flight-gate)" in src


def test_v4_helpers_still_intact():
    """v0.5.240 (_kill_stale_dhclients), v0.5.275
    (_vrf_has_connected_route), v0.5.282 (v4 local-table probe)
    must all still be in place — v0.5.338 mirrors them, doesn't
    replace them."""
    src = _dhcp_src()
    assert "def _kill_stale_dhclients(" in src
    assert "def _vrf_has_connected_route(" in src


def test_D_mask_reconcile_marker_present():
    """Gap #9 (v6 mask reconcile — mirror of v0.5.236 for v4)
    bundled into v0.5.338. Prevents anchoring a narrower prefix
    than the iface's existing /N when the pool subnet is
    contained in it."""
    src = _dhcp_src()
    assert "v0.5.338 (audit dhcpv6-mask-reconcile)" in src


def test_D_mask_reconcile_lives_in_ensure_ipv6_address():
    """The reconcile must live INSIDE `_ensure_ipv6_address` — that's
    the single-choke-point where every v6 anchor add happens. Any
    other location would miss some code path."""
    src = _dhcp_src()
    fn_idx = src.index("def _ensure_ipv6_address(")
    body = src[fn_idx:fn_idx + 4000]
    assert "v0.5.338 (audit dhcpv6-mask-reconcile)" in body


def test_D_mask_reconcile_prefers_iface_prefix():
    """The reconcile must OVERRIDE the caller's `prefix` with the
    iface's declared prefix when the target address falls inside
    the iface's declared subnet."""
    src = _dhcp_src()
    fn_idx = src.index("def _ensure_ipv6_address(")
    body = src[fn_idx:fn_idx + 4000]
    # The assignment must happen — `prefix = str(_e_pfx)`.
    assert "prefix = str(_e_pfx)" in body


def test_D_mask_reconcile_skips_link_local():
    """fe80::/10 addresses are always present and unrelated to the
    pool anchor — they must be excluded from the reconcile."""
    src = _dhcp_src()
    fn_idx = src.index("def _ensure_ipv6_address(")
    body = src[fn_idx:fn_idx + 4000]
    assert "is_link_local" in body


def test_gap_6_folded_into_v0_5_337():
    """Gap #6 (v6 anchor failure surfacing via dhcp_last_error) was
    folded into v0.5.337's DAD-before-anchor rewrite. Verify the
    `if not _v6_anchor_ok:` branch surfaces the error."""
    src = _dhcp_src()
    fn_idx = src.index("def start_dhcp_server(")
    body = src[fn_idx:]
    assert "if not _v6_anchor_ok:" in body
    # And the branch writes to dhcp_last_error.
    idx = body.index("if not _v6_anchor_ok:")
    branch = body[idx:idx + 3000]
    assert '"dhcp_last_error"' in branch
