"""v0.5.311 — ARP IPv4 metric now targets the gateway, not own IP.

Operator flagged the v0.5.310 tooltip fix: "why pinging local ip,
ARP should be for Gateway ip". Correct — ARP is by definition
for REMOTE peers. Pinging own IP doesn't involve ARP at all
(kernel routes via lo through the local table).

Pre-v0.5.311 code (run_tgen_server.py:/api/device/arp/<id>):

    if ipv4_address:                                # ← own IP
        result = subprocess.run(
            ping_prefix + ["ping", "-c", "1", "-W", "1", ipv4_address],
            ...
        )

Meanwhile the IPv6 branch already had the right shape:

    if ipv6_address or ipv6_gateway:
        ipv6_target = ipv6_gateway or ipv6_address  # ← gateway wins
        ping6_cmd = ping_prefix + ["ping6", "-c", "1", "-W", "1", ipv6_target]

v0.5.311 makes the IPv4 branch symmetric with IPv6: prefer
gateway as the target, fall back to own IP only when no gateway
is configured. Aligns the metric with what its name promises.

v0.5.310 Fix B (VRF local host-route install in
utils/frr_docker._create_vrf) stays in place — it's no longer in
the metric's critical path, but the local-route drift is a real
underlying kernel state issue that could bite other VRF-scoped
code (traffic-gen socket source-address bind, monitor probes).
Kept as defense-in-depth.

Tooltip in widgets/devices_tab.py reverts the "self-check"
phrasing (which was v0.5.310's honest name for the misfeatured
metric) back to "IPv4 ARP failed" — but now the wording is
accurate because the metric IS targeting the gateway.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─────────────────────────── server-side check


def test_marker_present_in_server():
    assert "v0.5.311 (audit arp-ipv4-semantic-fix)" in _read("run_tgen_server.py")


def test_ipv4_target_prefers_gateway_with_own_ip_fallback():
    """Ipv4 branch of /api/device/arp/<id> now derives
    ipv4_target = ipv4_gateway or ipv4_address — mirror of the
    ipv6 branch's `ipv6_target = ipv6_gateway or ipv6_address`."""
    src = _read("run_tgen_server.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert "ipv4_target = ipv4_gateway or ipv4_address" in body
    # And the ping now targets ipv4_target (not the raw
    # ipv4_address like pre-fix).
    assert '"ping", "-c", "1", "-W", "1", ipv4_target' in body


def test_ipv4_branch_guard_widens_to_include_gateway():
    """Pre-fix `if ipv4_address:` only fired when the device had
    its own IPv4; a device with only a gateway (rare but possible
    for DHCP-client-not-yet-leased) skipped the check. New guard
    `if ipv4_gateway or ipv4_address:` matches the v6 branch."""
    src = _read("run_tgen_server.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert "if ipv4_gateway or ipv4_address:" in body


def test_neigh_fallback_targets_gateway_too():
    """The v0.5.254 neighbor-cache fallback (ping-fail → check
    neigh table) must ALSO consult the neigh entry for the same
    target the ping went to — otherwise a resolved gateway ARP
    would be missed and we'd flag orange despite the peer being
    resolvable."""
    src = _read("run_tgen_server.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert '_neigh_state_ok(ipv4_target, family="ipv4")' in body


def test_ipv4_target_surfaced_in_details_for_tooltip():
    """Endpoint surfaces the actual ipv4_target in
    arp_results.details.ipv4_target so the client-side tooltip
    can show operators what was pinged."""
    src = _read("run_tgen_server.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert 'arp_results["details"]["ipv4_target"] = ipv4_target' in body


# ─────────────────────────── client-side tooltip


def test_tooltip_marker_present():
    assert "v0.5.311 (audit arp-ipv4-semantic-fix)" in _read("widgets/devices_tab.py")


def test_tooltip_wording_matches_gateway_semantic():
    """Tooltip now says "IPv4 ARP failed — gateway not reachable
    via ARP" — accurate because the metric IS targeting the
    gateway now."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert "IPv4 ARP failed" in body
    assert "gateway" in body


def test_tooltip_success_matches_gateway_semantic():
    """Success tooltip clarifies what "resolved" means now:
    "IPv4 ARP resolved (gateway reachable)"."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert '"IPv4 ARP resolved (gateway reachable)"' in body


def test_tooltip_surfaces_target_and_ping_details():
    """The failure tooltip includes the actual target IP + ping
    result + VRF name from the endpoint's details dict."""
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.311 (audit arp-ipv4-semantic-fix)")
    body = src[idx:idx + 3000]
    assert '_details.get("ipv4_target")' in body
    assert '_details.get("ipv4_ping")' in body
    assert '_details.get("vrf")' in body


# ─────────────────────────── AST parse


def test_edited_files_ast_parse():
    """v0.5.300 lesson."""
    import ast
    for rel in ("run_tgen_server.py", "widgets/devices_tab.py"):
        ast.parse(_read(rel))
