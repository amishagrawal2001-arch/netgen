"""v0.5.354 — v6 monitor parity: parent-NIC + local-table scanners.

Two v6 sibling scanners in `arp_monitor` mirror the v4 originals
(v0.5.287 Fix C for parent-NIC, v0.5.290 + v0.5.293 for local-table).
Both build on v0.5.352's v6 helper consolidation — the parent-NIC
sweep now has a matching cleanup path (v0.5.352 D6) to hand the
operator; the local-table sweep can auto-delete because v0.5.352 D3
proved the paired install/remove for `local <ip>/128 dev <iface>`.

Two new helpers land in `utils/dhcp.py`:
  * `_iface_ipv6_addresses` — v6 mirror of `_iface_ipv4_addresses`.
  * `_collect_ipv6_anchor_candidates` — v6 mirror of
    `_collect_ipv4_anchor_candidates`.

Two new scanners land in `utils/arp_monitor.py`:
  * `_scan_parent_nic_drift_v6` — WARN-only, same conservatism as
    the v4 sister (v6 addresses may be intentional).
  * `_scan_local_table_drift_v6` — AUTO-DELETE, matches v0.5.293's
    v4 policy (an application-installed local `/128` whose IP isn't
    on the iface is by definition broken kernel state).

Both scanners are wired into the arp_monitor startup block alongside
the v4 siblings.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def _arp_monitor_src():
    return (_REPO / "utils" / "arp_monitor.py").read_text()


def test_all_v0_5_354_markers_present():
    dhcp_src = _dhcp_src()
    arp_src = _arp_monitor_src()
    assert "v0.5.354 (audit dhcpv6-scanner-parity)" in dhcp_src
    assert "v0.5.354 (audit dhcpv6-scanner-parity)" in arp_src


# --- utils/dhcp.py helpers ---


def test_iface_ipv6_addresses_helper_defined():
    """The v6 iface-enumeration helper must exist as a module-level
    function so `arp_monitor` can import it the same way it imports
    the v4 sibling."""
    src = _dhcp_src()
    assert "def _iface_ipv6_addresses(" in src


def test_iface_ipv6_addresses_excludes_link_local():
    """fe80::/10 is kernel-managed and always present — including
    it in the "iface addrs" list would flip every direct-attached
    parent NIC into a false-positive DRIFT warning."""
    src = _dhcp_src()
    idx = src.index("def _iface_ipv6_addresses(")
    body = src[idx:idx + 2000]
    assert "is_link_local" in body


def test_iface_ipv6_addresses_uses_parse_ipv6_helper():
    """Reuse the existing `_parse_ipv6` helper for consistency —
    two parsers → drift the day one gets fixed and the other
    doesn't."""
    src = _dhcp_src()
    idx = src.index("def _iface_ipv6_addresses(")
    body = src[idx:idx + 2000]
    assert "_parse_ipv6(interface" in body


def test_collect_ipv6_anchor_candidates_helper_defined():
    src = _dhcp_src()
    assert "def _collect_ipv6_anchor_candidates(" in src


def test_collect_ipv6_anchor_candidates_iterates_both_key_spellings():
    """Same as v0.5.351 stop-server sweep — config can carry the v6
    pool under `ipv6_pool_start`/`ipv6_prefix` OR legacy
    `pool6_start`/`prefix6`. Must iterate both."""
    src = _dhcp_src()
    idx = src.index("def _collect_ipv6_anchor_candidates(")
    body = src[idx:idx + 3000]
    assert '"ipv6_pool_start"' in body
    assert '"pool6_start"' in body
    assert '"ipv6_prefix"' in body
    assert '"prefix6"' in body


def test_collect_ipv6_anchor_candidates_includes_gateway():
    """Operator may have declared a distinct `ipv6_gateway` that
    `_ensure_ipv6_address` honored via `server_ip = gateway`.
    Must be a candidate for the parent-NIC scan to catch it."""
    src = _dhcp_src()
    idx = src.index("def _collect_ipv6_anchor_candidates(")
    body = src[idx:idx + 3000]
    assert '"ipv6_gateway"' in body


def test_collect_ipv6_anchor_candidates_returns_first_host_of_pool():
    """v0.5.230 auto-derive picks the first host of the pool subnet.
    Candidates must include that host for the sweep to match."""
    src = _dhcp_src()
    idx = src.index("def _collect_ipv6_anchor_candidates(")
    body = src[idx:idx + 3000]
    # Uses `list(_net.hosts())` + `_hosts[0]` — same shape as v4
    # sister uses for its supernet derivation.
    assert "IPv6Network(" in body
    assert ".hosts()" in body


# --- arp_monitor.py scanners ---


def test_scan_parent_nic_drift_v6_defined():
    src = _arp_monitor_src()
    assert "def _scan_parent_nic_drift_v6(" in src


def test_scan_parent_nic_drift_v6_is_warn_only():
    """Same conservatism as the v4 sister — v6 addresses may be
    intentional (management, out-of-band). Must NEVER call
    `_remove_ipv6_address` or `ip -6 addr del`."""
    src = _arp_monitor_src()
    idx = src.index("def _scan_parent_nic_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert "_remove_ipv6_address" not in body, (
        "v0.5.354 v6 parent-NIC drift scan must NOT auto-delete — "
        "matches v4 sister's WARN-only policy"
    )
    assert '"ip", "-6", "addr", "del"' not in body


def test_scan_parent_nic_drift_v6_imports_v6_helpers():
    src = _arp_monitor_src()
    idx = src.index("def _scan_parent_nic_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert "_collect_ipv6_anchor_candidates" in body
    assert "_iface_ipv6_addresses" in body
    assert "_iface_parent" in body


def test_scan_parent_nic_drift_v6_only_scans_running_dhcp_servers():
    """Iterating every device would false-positive on client-mode
    devices whose iface has a v6 address by definition (the lease).
    Must gate on `dhcp_mode == server` and `status == Running`."""
    src = _arp_monitor_src()
    idx = src.index("def _scan_parent_nic_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert '"server"' in body
    assert '"Running"' in body


def test_scan_local_table_drift_v6_defined():
    src = _arp_monitor_src()
    assert "def _scan_local_table_drift_v6(" in src


def test_scan_local_table_drift_v6_uses_ip_6_route_show_table_local():
    src = _arp_monitor_src()
    idx = src.index("def _scan_local_table_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert '"ip", "-6", "route", "show", "table", "local"' in body


def test_scan_local_table_drift_v6_auto_deletes_matches_v0_5_293():
    """v0.5.293 established auto-delete for the v4 sister on the
    argument that an application-installed local host route whose
    IP isn't on the iface is by definition broken state. Same
    argument applies to v6 — auto-delete."""
    src = _arp_monitor_src()
    idx = src.index("def _scan_local_table_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert '"ip", "-6", "route", "del", "local"' in body
    assert "auto-cleaned" in body


def test_scan_local_table_drift_v6_skips_link_local_and_lo():
    """Two guards: (a) `lo` — kernel-managed for v6 (`::1/128`
    etc.); (b) `fe80::/10` link-local — kernel-managed on every
    iface, must never be auto-deleted."""
    src = _arp_monitor_src()
    idx = src.index("def _scan_local_table_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    assert '"lo"' in body
    assert "is_link_local" in body


def test_scan_local_table_drift_v6_only_touches_128_hosts():
    """Subnet-shaped `local` entries are rare admin-installed NDP-
    proxy configs and MUST NOT be auto-deleted. Only touch bare-
    host or explicit /128 form."""
    src = _arp_monitor_src()
    idx = src.index("def _scan_local_table_drift_v6(")
    next_def = src.index("    def ", idx + 1)
    body = src[idx:next_def]
    # Guard: skip anything whose prefix isn't 128.
    assert '"128"' in body


# --- Wiring in start() ---


def test_v6_scanners_wired_into_startup_block():
    """Both scanners must be CALLED from the startup block alongside
    the v4 siblings. A defined-but-never-called scanner is dead
    code."""
    src = _arp_monitor_src()
    # Find the outer pre-loop block. Both v4 and v6 sisters live
    # near the top of the start() flow.
    assert "self._scan_parent_nic_drift_v6()" in src
    assert "self._scan_local_table_drift_v6()" in src


def test_v6_scanner_exception_guards_present():
    """The startup block must wrap each scanner in try/except so
    one failing scanner doesn't prevent the others (or the monitor
    loop itself) from starting. Matches v4-sibling pattern."""
    src = _arp_monitor_src()
    # Find each scanner call and verify a nearby except handler.
    idx1 = src.index("self._scan_parent_nic_drift_v6()")
    idx2 = src.index("self._scan_local_table_drift_v6()")
    # Look forward up to 500 chars for the except.
    assert "except Exception" in src[idx1:idx1 + 600]
    assert "except Exception" in src[idx2:idx2 + 600]


# --- Regression guards ---


def test_v4_scanners_still_intact():
    """The whole point of v0.5.354 is v4/v6 parity. If the v4 side
    ever gets refactored away, the argument for symmetry
    disappears."""
    src = _arp_monitor_src()
    assert "def _scan_parent_nic_drift(" in src
    assert "def _scan_local_table_drift(" in src


def test_v0_5_352_helpers_still_intact():
    """The v6 scanners lean on v0.5.352's helper consolidation for
    their remediation (`stop→start triggers v0.5.352 D6 sweep`).
    Guard against v0.5.352 being reverted while v0.5.354 remains."""
    src = _dhcp_src()
    assert "v0.5.352 (audit dhcpv6-helper-post-add-plumbing)" in src
    assert "v0.5.352 (audit stop-server-v6-parent-nic-sweep)" in src


def test_arp_monitor_ast_parses():
    import ast
    ast.parse(_arp_monitor_src())


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
