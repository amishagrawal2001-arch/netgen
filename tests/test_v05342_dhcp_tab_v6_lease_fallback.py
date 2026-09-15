"""v0.5.342 — DHCP tab Lease IP / Gateway columns fall back to v6
lease fields when v4 lease is empty. Mirror of v0.5.294 for the
DHCP subtab.

Operator on srv06: device6 (v6-only DHCP client on vlan40) finally
leased `2001:db8:30::18a` end-to-end (verified via `ip -6 addr
show vlan40` + `/api/device/dhcp/status` returned `lease_ip6=
2001:db8:30::18a`). But the DHCP tab's Lease IP column stayed
blank — the render code only read `entry["lease_ip"]` (v4 field),
never falling back to `lease_ip6`. Same class of bug that v0.5.294
fixed on the Devices tab.

Fix: when `lease_ip` is empty and `lease_ip6` is populated, render
`<addr>/<prefix> (v6)` (matches the `(leased)` suffix pattern
v0.5.294 uses). Same fallback for the Gateway column via
`lease_gateway6`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _src():
    return (_REPO / "utils" / "devices_tab_dhcp.py").read_text()


def test_marker_present():
    assert "v0.5.342 (audit dhcpv6-lease-visibility-tab)" in _src()


def test_fallback_uses_lease_ip6_when_v4_empty():
    """The client branch (non-server) must consult `lease_ip6` +
    `lease_prefix6` when `lease_ip` is empty."""
    src = _src()
    idx = src.index("v0.5.342 (audit dhcpv6-lease-visibility-tab)")
    body = src[idx:idx + 3000]
    assert 'entry.get("lease_ip6")' in body
    assert 'entry.get("lease_prefix6")' in body
    # Rendered form must include the `(v6)` marker so a dual-stack
    # row's v6 fallback is visually distinct from a v4 lease.
    assert "(v6)" in body


def test_gateway_fallback_uses_lease_gateway6():
    src = _src()
    idx = src.index("v0.5.342 (audit dhcpv6-lease-visibility-tab)")
    body = src[idx:idx + 3000]
    assert 'entry.get("lease_gateway6")' in body


def test_v4_lease_still_wins_when_both_present():
    """Regression guard: for dual-stack clients (both leases
    populated), the Lease IP column MUST show the v4 lease
    unmodified — matches pre-v0.5.342 behavior for v4-leased rows.
    The v6 fallback only fires when v4 is empty."""
    src = _src()
    idx = src.index("v0.5.342 (audit dhcpv6-lease-visibility-tab)")
    body = src[idx:idx + 3000]
    # Guard shape:
    assert "if _v4_lease:" in body
    assert "elif _v6_lease:" in body


def test_server_branch_untouched():
    """The server-mode branch (v0.5.229 rendering `server_interface_ip`
    + served gateway) must NOT be swapped for the v6 fallback path.
    Regression guard against a stray edit."""
    src = _src()
    # Server-branch marker still present:
    assert "v0.5.229 (audit U client-13)" in src
    assert "_server_ip = (" in src


def test_v0_5_294_marker_still_intact():
    """v0.5.294 (Devices tab lease-visibility) is the shape v0.5.342
    mirrors — must still be in place elsewhere in the codebase."""
    devices_src = (_REPO / "widgets" / "devices_tab.py").read_text()
    assert "v0.5.294" in devices_src


def test_devices_tab_dhcp_ast_parses():
    import ast
    ast.parse(_src())
