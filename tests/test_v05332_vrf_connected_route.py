"""v0.5.332 — VRF setup installs CONNECTED route in the VRF's
routing table, alongside the v0.5.310 LOCAL route.

Operator on srv06: device5 (IPv6-only, 2001:db8:20::2/64 on
vlan20 in vrf-b710e366aec) stayed yellow even after v0.5.331
(ping6 → ping -6 fix). Root cause: on IPv6, when an address
is added to an interface BEFORE VRF enslavement, the kernel
installs the CONNECTED route (`2001:db8:20::/64 dev vlan20
proto kernel metric 256`) in the DEFAULT table (254) and
enslavement doesn't reliably migrate it to the VRF's table.
Netgen's ARP monitor's `ip vrf exec <vrf> ping -6 <gw>`
returned "Network is unreachable" — the VRF has no route for
the /64. All 5 v0.5.328 fallback tiers failed → yellow icon.

Fix: extend v0.5.310's local-route install in `_create_vrf`
to ALSO install the connected route in the VRF's table. Works
for both IPv4 (needs `scope link`) and IPv6 (default scope).
Idempotent — swallows `File exists`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _frr_docker_src():
    return (_REPO / "utils" / "frr_docker.py").read_text()


def test_marker_present():
    src = _frr_docker_src()
    assert "v0.5.332 (audit vrf-connected-route-missing)" in src


def test_connected_route_command_shape():
    """The added command must be `ip -[46] route add <cidr> dev
    <iface> table <vrf_table> proto kernel scope link metric 256`
    (matching what the kernel would install in the default table
    if it wasn't for VRF enslavement)."""
    src = _frr_docker_src()
    idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    body = src[idx:idx + 6000]
    # The IPv4 form uses `scope link` (required by kernel for on-
    # link routes); IPv6 form drops scope (defaults to global).
    assert '"proto", "kernel"' in body
    assert '"scope", "link"' in body
    assert '"metric", "256"' in body
    assert '"table", str(vrf_table)' in body


def test_uses_ipaddress_module_for_network_computation():
    """`_cidr` is `<addr>/<pfx>`; we need the NETWORK address
    (e.g. `2001:db8:20::2/64` → `2001:db8:20::/64`). Must go
    through `ipaddress.IPvN_Network(strict=False).__str__()`."""
    src = _frr_docker_src()
    idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    body = src[idx:idx + 6000]
    assert "_ipa2.IPv4Network(_cidr, strict=False)" in body
    assert "_ipa2.IPv6Network(_cidr, strict=False)" in body


def test_ipv6_scope_link_fallback():
    """IPv6 rejects `scope link` on connected routes (kernel
    enforces scope global for IPv6 on-link). If the first attempt
    fails, retry without `scope link` — this is the shape the
    kernel actually accepts for IPv6."""
    src = _frr_docker_src()
    idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    body = src[idx:idx + 6000]
    # Fallback attempt without scope link.
    assert "_conn_cmd_v6" in body
    # And the fallback command must NOT have `scope link`.
    fb_idx = body.index("_conn_cmd_v6 = [")
    fb_end = body.index("]", fb_idx)
    fallback_block = body[fb_idx:fb_end]
    assert '"scope"' not in fallback_block


def test_swallows_file_exists_idempotency():
    """Re-running `_create_vrf` on an already-configured device
    must not spam warnings — the connected route is already
    there. Swallow `File exists` from stderr."""
    src = _frr_docker_src()
    idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    body = src[idx:idx + 6000]
    assert '"File exists" not in (_rc.stderr or "")' in body
    assert '"File exists" not in (_rc2.stderr or "")' in body


def test_runs_alongside_v0_5_310_local_route_install():
    """The v0.5.310 LOCAL route install must STAY (it's still
    needed for self-ping-in-VRF). v0.5.332 ADDS the connected
    route, doesn't replace the local."""
    src = _frr_docker_src()
    # v0.5.310 marker still present.
    assert "v0.5.310 (audit vrf-local-host-route-drift)" in src
    # Both installs live inside the same for-loop iteration —
    # local first, then connected. Verify order.
    local_idx = src.index('"local", f"{_addr}/{_host_pfx}"')
    conn_idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    assert local_idx < conn_idx, (
        "v0.5.332 connected-route install must run AFTER v0.5.310 "
        "local-route install (same loop iteration)"
    )


def test_both_families_covered():
    """The v0.5.332 install runs inside the `for _fam_flag in
    ('-4', '-6'):` loop, so both IPv4 AND IPv6 get the
    connected-route install. Regression guard against a future
    refactor that guards on v4 only or v6 only."""
    src = _frr_docker_src()
    # Locate the fam-flag loop that wraps the address probe.
    loop_idx = src.index('for _fam_flag in ("-4", "-6"):')
    # And the v0.5.332 marker sits INSIDE that loop.
    marker_idx = src.index("v0.5.332 (audit vrf-connected-route-missing)")
    # marker is after the loop start. And there's no `def ` between
    # them (still in same function).
    between = src[loop_idx:marker_idx]
    assert "def " not in between, (
        "v0.5.332 install moved outside the fam-flag loop — must run "
        "for both IPv4 and IPv6"
    )


def test_frr_docker_ast_parses():
    import ast
    ast.parse(_frr_docker_src())
