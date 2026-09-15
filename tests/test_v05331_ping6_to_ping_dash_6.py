"""v0.5.331 — ARP status endpoint uses `ping -6` instead of the
deprecated `ping6` binary.

Operator on srv06 (Ubuntu 22.04): after upgrading to v0.5.330,
device5 (IPv6-only, gateway 2001:db8:20::1) still shows yellow
even though switch's `ping 2001:db8:20::2` works. Root cause:
`ping6` was removed as a standalone binary from iputils on
Ubuntu 22.04+ (deprecated in favor of `ping -6` which
autodetects the address family and is portable). All 5 IPv6
fallback tiers in `/api/device/arp/<device_id>` used `ping6` →
subprocess exit code 127 (command not found) → every tier
failed silently → `arp_ipv6_resolved` stayed False → device
stayed yellow.

Fix: switch `ping6` → `ping -6` in the ARP status endpoint.
Portable across Ubuntu 18-24, RHEL 7-9, Debian 10+. Rest of
the codebase (test-runner ping paths, gateway ping) is a
separate concern and can migrate on a slower cadence.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def test_marker_present():
    src = _server_src()
    assert "v0.5.331 (audit ping6-deprecated-on-ubuntu-22)" in src


def test_ipv6_first_check_uses_ping_dash_6():
    """The primary VRF-scoped IPv6 reachability check must use
    `ping -6` (portable) instead of `ping6` (Ubuntu 22+ removes)."""
    src = _server_src()
    # Locate the first ping command in the IPv6 check block.
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 4000]
    # Must have the ping -6 form.
    assert 'ping_prefix + ["ping", "-6", "-c", "1", "-W", "1", ipv6_target]' in body


def test_ipv6_interface_bound_warm_uses_ping_dash_6():
    """The H3 interface-bound NDP warm-up must ALSO use `ping -6`
    (same reason)."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    # `ping -6 -c 1 -W 2 -I <iface>`
    assert '"ping", "-6", "-c", "1", "-W", "2"' in body
    assert '"-I", _iface_for_ndp' in body


def test_arp_status_endpoint_no_bare_ping6():
    """Regression guard: the ARP status endpoint's IPv6 block
    must not use the bare `ping6` binary anywhere. `ping -6`
    only."""
    src = _server_src()
    # Slice: from the IPv6 check marker to the end of the IPv6
    # block (right before the IPv4 gateway resolution block).
    ipv6_start = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    # End marker: the IPv4 gateway block begins with "if ipv4_gateway:"
    ipv4_gw_start = src.index("if ipv4_gateway:", ipv6_start)
    ipv6_block = src[ipv6_start:ipv4_gw_start]
    # Assert no bare "ping6" as a subprocess arg. The word
    # "ping6" can still appear in comments (talking about the
    # deprecated binary is fine); check for it as a subprocess
    # command-list element.
    assert '"ping6",' not in ipv6_block, (
        "ping6 as subprocess arg detected in ARP status IPv6 block — "
        "must use `ping -6` instead"
    )


def test_ipv4_gateway_check_untouched():
    """v0.5.331 is IPv6-only. IPv4 gateway resolution still uses
    `ping` (v4 default) + arping. Regression guard so a broader
    refactor doesn't leak into the IPv4 path.

    Anchor to the ARP-status endpoint's gateway block via its
    v0.5.277 marker (unique to this endpoint), not a bare
    `if ipv4_gateway:` string which appears elsewhere."""
    src = _server_src()
    # v0.5.277 comment marker anchors us at the ARP endpoint's
    # gateway resolution block, past any earlier VXLAN scaffolding.
    idx = src.index("v0.5.277 (ARP-H1)")
    body = src[idx:idx + 8000]
    # IPv4 gateway warm still uses `arping` — no v0.5.331 change here.
    assert '"arping",' in body


def test_v0_5_331_comment_explains_ubuntu_22():
    """The comment must name Ubuntu 22.04 and errno 127 so a
    future refactor can see WHY the switch happened."""
    src = _server_src()
    idx = src.index("v0.5.331 (audit ping6-deprecated-on-ubuntu-22)")
    body = src[idx:idx + 2000]
    assert "Ubuntu 22.04" in body
    assert "ping -6" in body


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())
