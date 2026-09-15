"""v0.5.346 — DHCPv6 client sets accept_ra=2 (not 0) so kernel
still processes RAs for on-link + default route.

v0.5.309 set BOTH accept_ra=0 AND autoconf=0 before spawning
dhcp6c/dhclient -6, aiming to prevent SLAAC from racing the
authoritative DHCPv6 lease. But `accept_ra=0` also blocks the
kernel from installing the RA-derived on-link `<prefix>/64
proto ra` connected route AND the RA-derived default route.

Operator on srv06 2026-09-15: device6 leased 2001:db8:30::1f3
via DHCPv6, but VRF's IPv6 route table had ONLY:
    fe80::/64 dev vlan40 proto kernel
    2001:db8:30::1f3 dev vlan40 proto kernel
No `2001:db8:30::/64` on-link route, no default via QFX. Client
could reach only itself. Manual `sysctl net.ipv6.conf.vlan40.
accept_ra=2` + Router Solicit → routes appeared, ping to
2001:db8:20::2 succeeded.

Root cause: netgen sets `forwarding=1` on VRF-slaved interfaces
(needed for BGP/OSPF); Linux's default is `accept_ra=1 if
forwarding=0, else 0`. So even without v0.5.309's explicit set,
the kernel wouldn't accept RAs. We need `accept_ra=2` explicitly
to override.

Fix: keep `autoconf=0` (blocks SLAAC — v0.5.309's original intent
is preserved) but change `accept_ra` from `0` to `2` (accept RAs
even with forwarding=1). Kernel now installs on-link + default
routes from the RA; dhcp6c stays authoritative for addresses.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def test_marker_present():
    assert "v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)" in _dhcp_src()


def test_accept_ra_set_to_2_not_0():
    """The kernel needs `accept_ra=2` to override the `forwarding=1`
    default that flips accept_ra to 0."""
    src = _dhcp_src()
    idx = src.index("v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)")
    body = src[idx:idx + 4000]
    # The sysctl call for accept_ra must set value = "2".
    assert '(f"net.ipv6.conf.{interface}.accept_ra", "2")' in body


def test_autoconf_still_disabled():
    """v0.5.309's original intent — block SLAAC — must be
    preserved. autoconf=0 keeps dhcp6c authoritative for
    addresses even while accept_ra=2 lets the kernel process
    the on-link + default route from RA."""
    src = _dhcp_src()
    idx = src.index("v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)")
    body = src[idx:idx + 4000]
    assert '(f"net.ipv6.conf.{interface}.autoconf", "0")' in body


def test_old_accept_ra_zero_form_gone():
    """Regression guard: the old `sysctl accept_ra=0` shape must
    not survive — that's what caused the bug."""
    src = _dhcp_src()
    idx = src.index("v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)")
    body = src[idx:idx + 4000]
    # The old for-loop used one shared value "0"; the new loop
    # pairs each sysctl with its own value.
    assert '"{_sysctl_key}=0"' not in body
    assert 'f"{_sysctl_key}={_sysctl_val}"' in body


def test_comment_names_operator_incident():
    src = _dhcp_src()
    idx = src.index("v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)")
    body = src[idx:idx + 4000]
    assert "srv06" in body or "device6" in body
    # Explains the two symptoms.
    assert "on-link" in body.lower()
    assert "default" in body.lower()
    assert "forwarding" in body.lower()


def test_v0_5_309_marker_preserved_as_context():
    """v0.5.346 supersedes v0.5.309 for the accept_ra half. The
    comment must reference v0.5.309 so a future reader sees WHY
    the change happened."""
    src = _dhcp_src()
    idx = src.index("v0.5.346 (audit dhcpv6-client-accept-ra-vs-autoconf)")
    body = src[idx:idx + 4000]
    assert "v0.5.309" in body


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
