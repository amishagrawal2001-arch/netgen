"""v0.5.309 — make DHCPv6 lease end-to-end reliable.

Operator ask post-v0.5.308 (which fixed the last v4 leak into the
v6-only server iface): "make sure when DHCPv6 client is active,
DHCPv6 server is able to lease v6 IPs — in the past we had this
class of problem for v4 (the v0.5.287-301 saga) and want the same
level of due diligence for v6."

Audit surfaced 3 gaps blocking end-to-end DHCPv6 lease reliability;
v0.5.309 ships fixes for the two highest-impact ones:

  A. RA/autoconf race on the client interface. Server dnsmasq
     emits Router Advertisement (`enable-ra`); client kernel does
     SLAAC from the PIO and generates a global v6 address
     ALONGSIDE the dhcp6c-obtained lease. `_pick_global_ipv6`
     picks in `ip addr show` order and may pick the SLAAC address
     (MAC-derived, NOT from the pool) instead of the actual
     lease — user-visible symptom: `dhcp_lease_ip6` in the DB
     and the Devices tab's IPv6 column show
     `2001:db8:30::5e25:73ff:fe3f:3056` (SLAAC EUI-64) instead of
     `2001:db8:30::100` (pool lease).
     Fix: sysctl accept_ra=0 + autoconf=0 on the client iface
     right before spawning dhcp6c. dhcp6c-client-mode devices are
     explicitly delegating v6 addressing to dhcp6c; RA/SLAAC state
     is noise.

  B. v6 anchor-replay in arp_monitor. utils/arp_monitor.py
     periodic anchor-replay only imported and called
     `_ensure_ipv4_address`. For a v6-only DHCPv6-server device
     the v4 pool guard at :483 short-circuited the WHOLE device
     before any v6 anchor replay could run — so on netgen-server
     restart the v6 anchor silently vanished, dnsmasq's v6 socket
     failed, and clients stopped getting leases.
     Fix: (1) widen the pool-empty guard to only skip when BOTH
     v4 AND v6 pools are absent; (2) add a symmetric v6 replay
     block that imports `_ensure_ipv6_address`, derives the v6
     server anchor from `ipv6_server_ip` (or first host of the
     pool /prefix), and calls the helper.

Deferred (follow-up):
  C. DAD-wait for v6 anchor in _ensure_ipv6_address (parallel of
     v0.5.290 for v4). Kernel DAD usually settles fast enough
     that dnsmasq bind succeeds on retry; only bites on collision.
     Edge-case, no evidence of hitting it on srv06 yet.

Source-level checks only; actual dhcp6c ↔ dnsmasq lease exchange
lives on srv06.
"""
from __future__ import annotations

from pathlib import Path

_DHCP_PY = Path(__file__).resolve().parents[1] / "utils" / "dhcp.py"
_ARP_MON_PY = Path(__file__).resolve().parents[1] / "utils" / "arp_monitor.py"


def _dhcp_src() -> str:
    return _DHCP_PY.read_text()


def _arp_mon_src() -> str:
    return _ARP_MON_PY.read_text()


# ─────────────────────────── Fix A: RA/autoconf suppression


def test_marker_A_present_before_dhcp6c_spawn():
    src = _dhcp_src()
    assert "v0.5.309 (audit dhcpv6-lease-e2e)" in src
    # The suppression block must sit between _flush_ipv6 and the
    # dhcp6c launch (line-order matters — if it lands AFTER dhcp6c
    # starts, the SLAAC address is already there).
    flush_idx = src.index("_flush_ipv6(interface, container=container)")
    fix_idx = src.index("v0.5.309 (audit dhcpv6-lease-e2e): disable SLAAC")
    dhcp6c_idx = src.index('_command_exists("dhcp6c"')
    assert flush_idx < fix_idx < dhcp6c_idx, (
        "sysctl block must sit between _flush_ipv6 and dhcp6c spawn"
    )


def test_A_disables_both_accept_ra_and_autoconf():
    """accept_ra=0 blocks RA processing entirely; autoconf=0 blocks
    SLAAC only (keeps RA-derived default route learning). Belt and
    suspenders — a v6-only DHCPv6 client should get ALL its v6
    addressing from dhcp6c, RA-derived state is noise."""
    src = _dhcp_src()
    idx = src.index("v0.5.309 (audit dhcpv6-lease-e2e): disable SLAAC")
    body = src[idx:idx + 3000]
    assert "accept_ra" in body
    assert "autoconf" in body
    # Sysctl actually gets set to 0 (not just referenced in a comment).
    assert 'f"{_sysctl_key}=0"' in body


def test_A_is_best_effort_no_lease_block():
    """A sysctl failure (e.g., in a container without net.ipv6
    sysctls exposed) must NOT block the lease request — just log
    at debug level and press on."""
    src = _dhcp_src()
    idx = src.index("v0.5.309 (audit dhcpv6-lease-e2e): disable SLAAC")
    body = src[idx:idx + 3000]
    assert "except Exception" in body
    assert "logger.debug" in body


# ─────────────────────────── Fix B: v6 anchor-replay in arp_monitor


def test_marker_B_present_in_arp_monitor():
    src = _arp_mon_src()
    # Two v0.5.309 markers — one for the widened pool-empty guard,
    # one for the v6 replay block itself. Guard against reversion.
    assert src.count("v0.5.309 (audit dhcpv6-lease-e2e)") >= 2


def test_B_import_includes_ensure_ipv6_address():
    src = _arp_mon_src()
    # The import block at ~line 429 now pulls in both helpers.
    assert "from utils.dhcp import _ensure_ipv4_address, _ensure_ipv6_address" in src


def test_B_pool_empty_guard_covers_both_families():
    """Pre-fix guard `if not (_pool_start and _pool_end): continue`
    skipped v6-only devices. Widened guard checks both v4 AND v6
    pool presence and only skips when BOTH are empty."""
    src = _arp_mon_src()
    assert "_has_v4 = bool(_pool_start and _pool_end)" in src
    assert '_peek_v6_start = str(_dhcp_cfg.get("ipv6_pool_start") or "")' in src
    assert "_peek_v6_end = str(_dhcp_cfg.get(\"ipv6_pool_end\") or \"\")" in src
    assert "if not (_has_v4 or _has_v6):" in src


def test_B_v6_replay_block_calls_ensure_ipv6_address():
    src = _arp_mon_src()
    assert "_ensure_ipv6_address(" in src
    # Anchor is derived from ipv6_server_ip when set, else first
    # host of the pool /prefix (matches start_dhcp_server shape).
    assert 'dhcp_cfg.get("ipv6_server_ip")' in src
    assert "IPv6Network(" in src


def test_B_v6_replay_gated_on_ipv6_enabled_flag():
    """Don't replay v6 anchor for a device that has an ipv6_pool_
    start persisted but ipv6_enabled=False (operator turned v6
    off on a formerly-dual-stack server)."""
    src = _arp_mon_src()
    assert '_v6_enabled_dc = bool(_dhcp_cfg.get("ipv6_enabled", False))' in src
    # And this flag participates in the replay-fire gate.
    idx = src.index("_v6_enabled_dc = bool")
    tail = src[idx:idx + 2000]
    assert "if _v6_enabled_dc and _v6_pool_start and _v6_pool_end:" in tail


def test_v4_replay_gated_on_ipv4_enabled_flag():
    """Symmetric: v4 replay only fires when ipv4_enabled is not
    explicitly False. Otherwise v0.5.308's client-side v4 drop
    would be re-imposed by the monitor for v6-only server
    devices that happen to have persisted v4 pool fields from
    a prior template switch."""
    src = _arp_mon_src()
    assert '_v4_enabled_dc = bool(_dhcp_cfg.get("ipv4_enabled", True))' in src
    idx = src.index("_v4_enabled_dc = bool")
    tail = src[idx:idx + 2000]
    assert "if _pool_start and _pool_end and _v4_enabled_dc:" in tail


# ─────────────────────────── Files ast-parse clean


def test_edited_files_ast_parse():
    """v0.5.300 lesson."""
    import ast
    for path in (_DHCP_PY, _ARP_MON_PY):
        ast.parse(path.read_text())
