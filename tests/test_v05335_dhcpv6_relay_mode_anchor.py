"""v0.5.335 — DHCPv6 server auto-anchor must skip when the pool
subnet is L3-REMOTE from the server's own interface (relay mode).

Operator on srv06 2026-09-15: device5 on vlan20 (2001:db8:20::2/64)
configured as DHCPv6 server for pool 2001:db8:30::100-1ff/64. The
DHCPv6 clients live on vlan40 behind the QFX switch acting as a
DHCPv6 relay (irb.40 = 2001:db8:30::1/64). The relay was correctly
forwarding solicits to 2001:db8:20::2, but the client never leased.

Root cause: `start_dhcp_server` had the v0.5.230 auto-anchor
running unconditionally — it added `2001:db8:30::1/64` (first host
of the pool subnet) to the server's vlan20 interface. Two effects:

1. Duplicate address on the L2: srv06's vlan20 and the QFX's
   irb.40 both claimed 2001:db8:30::1.
2. Kernel installed `2001:db8:30::/64 dev vlan20` connected route
   in the default table. When dnsmasq (bound to vlan20 via
   bind-dynamic) sent the relay-reply back to 2001:db8:30::1, the
   kernel resolved that address via the newly-added connected
   route → ND on vlan20 → answered ITSELF → the reply never left
   srv06 → the DHCPv6 client on vlan40 never got an offer.

The v4 side has had this same relay-mode carve-out since v0.5.245
(`relay_return_hop`) — the v6 side never got the equivalent.

Fix: detect L3-remote pools by comparing the pool's IPv6 network
with the interface's own IPv6 subnets. If NONE overlap, we're in
relay mode → skip the anchor. Direct-attached devices (server +
clients on the same L2) still get the anchor — v0.5.230's original
intent — because their pool subnet DOES overlap with the iface's
own subnet.

Also sweeps any leftover pool-subnet address a pre-v0.5.335 apply
may have already attached (relay-mode is deterministic, so
anything in the pool subnet on the iface at this point can only be
buggy leftover state).
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


def test_marker_present():
    src = _dhcp_src()
    assert "v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)" in src


def test_pool_remote_detection_uses_ipv6network_overlaps():
    """Detection must compare full IPv6Network objects (`.overlaps()`)
    — a bare string prefix compare would misidentify overlapping /64s
    with different string forms (e.g. `2001:db8::` vs
    `2001:0db8::`)."""
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    # v0.5.339 added the device_db-based path (`_pool_net.overlaps(_dev_net)`)
    # PLUS kept the runtime-iface fallback (`_pool_net.overlaps(_n)`).
    # Body widened from 6000 → 12000 chars so both paths land in slice.
    body = src[idx:idx + 12000]
    assert "ipaddress.IPv6Network" in body
    # Either the device-db comparison or the iface fallback qualifies.
    assert (
        "_pool_net.overlaps(_dev_net)" in body
        or "_pool_net.overlaps(_n)" in body
    )


def test_pool_remote_check_excludes_link_local():
    """Link-local IPv6s (fe80::/10) are kernel-managed and always
    present — including them in the "iface subnets" set would make
    every check see a non-overlap → every device would be treated
    as relay-mode. Guard: skip link-local when building the iface
    subnet list."""
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    body = src[idx:idx + 12000]
    assert "is_link_local" in body


def test_relay_mode_branch_skips_ensure_ipv6_address():
    """The relay-mode branch (`if _pool_is_l3_remote:`) must NOT
    call `_ensure_ipv6_address` — that's the whole point of the
    fix. `_ensure_ipv6_address` still runs on the direct-attached
    branch (else)."""
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    # Body widened 8000 → 16000: v0.5.337/339 added ~150 lines of
    # device_db lookup + DAD probe + gateway-skip iteration between
    # the marker and the `else:` (direct-attached) branch.
    body = src[idx:idx + 16000]
    # Locate the `if _pool_is_l3_remote:` and `else:` boundaries.
    relay_if = body.index("if _pool_is_l3_remote:")
    else_idx = body.index("else:", relay_if)
    relay_branch = body[relay_if:else_idx]
    direct_branch = body[else_idx:else_idx + 8000]
    # Relay branch must NOT anchor.
    assert "_ensure_ipv6_address" not in relay_branch, (
        "v0.5.335 relay-mode branch must not call _ensure_ipv6_address"
    )
    # Direct-attached branch must still anchor (v0.5.230 kept intact).
    # v0.5.337 wrapped the call in a DAD-probe if/else and reformatted
    # it multi-line, so match on the bare function name.
    assert "_ensure_ipv6_address(" in direct_branch, (
        "direct-attached branch (post-else) must keep the "
        "v0.5.230 _ensure_ipv6_address call"
    )


def test_relay_mode_branch_sweeps_stale_pool_subnet_anchors():
    """Cleanup path for pre-v0.5.335 buggy state — devices that
    were applied before this fix have `2001:db8:30::1/64` (or
    similar) stuck on vlan20. The relay-mode branch must
    `_remove_ipv6_address` any iface IPv6 that lives in the pool
    subnet."""
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    # Widened 8000 → 16000 for the same v0.5.337/339 growth.
    body = src[idx:idx + 16000]
    relay_if = body.index("if _pool_is_l3_remote:")
    else_idx = body.index("else:", relay_if)
    relay_branch = body[relay_if:else_idx]
    assert "_remove_ipv6_address" in relay_branch, (
        "v0.5.335 relay-mode branch must sweep any stale pool-"
        "subnet address left by a pre-fix apply"
    )
    # And must check membership via IPv6Address in _pool_net (not
    # bare string compare).
    assert "in _pool_net" in relay_branch


def test_direct_attached_still_derives_first_host_when_no_server_ip():
    """The v0.5.230 auto-derive (first host of pool subnet →
    ipv6_server_ip) must SURVIVE for direct-attached devices — that
    branch's original purpose was to avoid a `bind_interfaces
    failed: no interface with matching address` crash. v0.5.337
    replaced the naive `_hosts6[0]` with a gateway-skip iterator
    (`for _h6 in _hosts6:`) but the derivation path itself must
    still exist."""
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    body = src[idx:idx + 16000]
    # Grab the `else:` for `if _pool_is_l3_remote:` — same
    # boundary logic as the other two tests so we look at the
    # direct-attached branch, not the earlier device_db else.
    relay_if = body.index("if _pool_is_l3_remote:")
    else_idx = body.index("else:", relay_if)
    direct_branch = body[else_idx:else_idx + 8000]
    # v0.5.357 (audit v6-hosts-generator-explosion): `_hosts6 =
    # list(_v6_net.hosts())` was replaced with direct arithmetic
    # (`network_address + 1` / `+ 2`) because `list(hosts())` on a
    # /64 tries to materialize 2^64-2 addresses and hangs the
    # process. The `_hosts6` list still exists; assert its
    # presence + the v0.5.337 iterator only.
    assert "_hosts6 = " in direct_branch, (
        "v0.5.230 auto-derive (hosts of pool subnet) must still "
        "fire on the direct-attached branch"
    )
    assert "for _h6 in _hosts6" in direct_branch, (
        "v0.5.337 gateway-skip iterator must still fire on the "
        "direct-attached branch"
    )
    # Regression guard: the v0.5.357 fix must not have accidentally
    # restored the exploding `list(_v6_net.hosts())` form. Strip
    # comment lines so the v0.5.357 fix's own historical context
    # (which mentions the buggy call by name) doesn't false-positive.
    _code_only = "\n".join(
        _l for _l in direct_branch.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "list(_v6_net.hosts())" not in _code_only, (
        "v0.5.357: `list(_v6_net.hosts())` regressed — a /64 pool "
        "would hang the process for practical eternity"
    )


def test_comment_names_operator_incident():
    src = _dhcp_src()
    idx = src.index("v0.5.335 (audit dhcpv6-relay-mode-anchor-collision)")
    body = src[idx:idx + 4000]
    # Names srv06, device5, and vlan20/vlan40 subnets.
    assert "srv06" in body or "device5" in body
    assert "2001:db8:20" in body
    assert "2001:db8:30" in body
    # Explains the two symptoms.
    assert "Duplicate address" in body or "duplicate address" in body.lower()
    assert "connected route" in body.lower()
    # Explicit parity note with v0.5.245 v4 side.
    assert "v0.5.245" in body


def test_v0_5_230_marker_still_present():
    """The v0.5.230 auto-derive comment must stay in the direct-
    attached branch — it explains why we still fall back to the
    first host, which is the correct behavior for that mode."""
    src = _dhcp_src()
    assert "v0.5.230 (audit P server-10)" in src


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())


def test_v0_5_334_marker_still_intact():
    """v0.5.334 (client status ipv4-required-on-ipv6-only) must
    still be present. Regression guard so a mismerge of the
    v0.5.335 fix doesn't accidentally back out the v0.5.334
    client-side change."""
    src = (_REPO / "widgets" / "devices_tab.py").read_text()
    assert "v0.5.334 (audit ipv4-required-on-ipv6-only)" in src
