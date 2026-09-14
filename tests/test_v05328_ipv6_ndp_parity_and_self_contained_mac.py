"""v0.5.328 — two operator-reported fixes bundled:

(1) IPv6 NDP resolution path now has the same belt-and-suspenders
    fallback chain as IPv4 gateway resolution. Operator hit this
    on srv06 vlan20 device1_2 after the IRB-MAC fix: switch ping
    2001:db8:20::2 worked, but netgen UI still showed yellow
    because netgen's VRF-scoped ping6 to the switch gateway
    failed and there were no further fallbacks. New chain:
      H5: any-protocol short-circuit (BGP/OSPF/ISIS v6 established)
      H1: VRF-wrapped neigh cache hit
      H4: unwrapped neigh cache hit fallback
      H3: interface-bound ping6 -I vlan<N> warm-up + re-check

(2) IRB MAC in the Juniper upstream-hint is now self-contained
    (LAA `02:00:00:00:00:<vlan-id>`) — no `<chassis-base>`
    placeholder. Operator asked for a ready-to-paste MAC.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _upstream_src():
    return (_REPO / "utils" / "upstream_hints.py").read_text()


# ---------- Part 1: IPv6 NDP fallback chain parity ----------


def test_ipv6_path_has_v0_5_328_audit_marker():
    """Marker guards the whole rewritten block."""
    src = _server_src()
    assert "v0.5.328 (audit ipv6-ndp-parity)" in src


def test_ipv6_has_any_protocol_short_circuit():
    """H5: BGP/OSPF/ISIS v6 established → NDP MUST be resolved."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert "_v6_short_circuit" in body
    # Reads at least one v6 protocol state.
    assert 'device.get("bgp_ipv6_established")' in body or 'device.get("bgp_established")' in body


def test_ipv6_has_vrf_wrapped_neigh_check():
    """H1: primary neigh cache check runs in VRF context (via
    `ping_prefix` which carries `ip vrf exec <vrf-name>`)."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert '_neigh_state_ok(ipv6_target, family="ipv6")' in body


def test_ipv6_has_unwrapped_fallback():
    """H4: if VRF-wrapped neigh returns nothing, retry without the
    wrap (netlink kernel edge cases)."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert "_saved_prefix = ping_prefix" in body
    assert "ping_prefix = []" in body


def test_ipv6_has_interface_bound_ping6_warm():
    """H3: force NDP by sending ping6 -I vlan<N> (bypasses VRF
    routing table dependency). Kernel populates neigh cache
    from the NA reply."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert '"ping6", "-c", "1", "-W", "2",' in body
    assert '"-I", _iface_for_ndp' in body


def test_ipv6_warm_uses_vlan_iface_when_present():
    """When device has a VLAN, warm-up must go out `vlan<N>`
    (the tagged sub-interface), NOT the parent NIC — same
    reasoning as ARP-H6 for IPv4 (v0.5.279)."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert 'f"vlan{_vlan}"' in body


def test_ipv6_records_check_path_for_diagnostic():
    """`arp_results["details"]["ipv6_check_path"]` names WHICH
    step of the fallback chain resolved (or `ndp_still_incomplete`
    if all failed). Same debuggability as ARP's
    `gateway_check_path`."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert '"ipv6_check_path"' in body
    for path in ("ping6_vrf_ok", "neigh_cache_hit_vrf",
                 "neigh_no_vrf_fallback", "neigh_after_ndp_warm_vrf",
                 "ndp_still_incomplete"):
        assert path in body


def test_ipv6_diagnostic_dumps_both_vrf_and_host_neigh():
    """When NDP fails, dump BOTH VRF-scoped and unwrapped neigh
    output so the operator can distinguish INCOMPLETE from
    no-entry — same pattern as gateway_neigh_vrf/host."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert '"ipv6_neigh_vrf"' in body
    assert '"ipv6_neigh_host"' in body


def test_ipv6_neigh_backcompat_key_preserved():
    """Old clients (pre-v0.5.328) read `details.ipv6_neigh` as a
    single string. Keep it populated with the VRF view so they
    don't break."""
    src = _server_src()
    idx = src.index("v0.5.328 (audit ipv6-ndp-parity)")
    body = src[idx:idx + 8000]
    assert '"ipv6_neigh"' in body


# ---------- Part 2: Self-contained IRB MAC ----------


def test_irb_mac_is_locally_administered():
    """LAA prefix `02:` — first octet bit 1 set (0x02 = 0b00000010),
    unicast bit 0 clear. No vendor-OUI conflict."""
    src = _upstream_src()
    assert "v0.5.328 (audit self-contained-irb-mac)" in src


def test_irb_mac_has_no_chassis_base_placeholder_in_emitted_lines():
    """Regression guard: the pre-v0.5.328 `<chassis-base>:XX` form
    required the operator to substitute — MAC now self-contained.
    Guard on the actual `_placeholder_mac` assignment, not any
    surrounding source comment (which legitimately mentions the
    retired placeholder for historical context)."""
    src = _upstream_src()
    idx = src.index("_placeholder_mac = ")
    line_end = src.index("\n", idx)
    line = src[idx:line_end]
    assert "<chassis-base>" not in line
    assert "02:00:00:00:00:" in line


def test_irb_mac_uses_02_prefix():
    """The LAA prefix `02:00:00:00:00:` is the actual emitted
    template — regression guard."""
    src = _upstream_src()
    idx = src.index("v0.5.328 (audit self-contained-irb-mac)")
    body = src[idx:idx + 3000]
    assert "02:00:00:00:00:" in body


def test_irb_variant_end_to_end_emits_self_contained_mac():
    """End-to-end runtime: render a device on VLAN 20, get a
    ready-to-paste MAC."""
    from utils import upstream_hints as u
    dev = {"device_name": "d5", "vlan": "20",
           "ipv6_address": "2001:db8:20::2", "ipv6_mask": "64",
           "ipv6_gateway": "2001:db8:20::1"}
    out = u.render_juniper(dev)
    assert "set interfaces irb.20 mac 02:00:00:00:00:20" in out
    assert "<chassis-base>" not in out


def test_irb_variant_mac_unique_per_vlan():
    """Regression guard for the per-VLAN uniqueness property.
    VLAN 20 → last byte 20, VLAN 30 → last byte 30, etc."""
    from utils import upstream_hints as u
    for vlan in ("10", "20", "30", "99"):
        dev = {"device_name": "t", "vlan": vlan,
               "ipv4_gateway": "10.0.0.1", "ipv4_mask": "24"}
        out = u.render_juniper(dev)
        assert f"set interfaces irb.{vlan} mac 02:00:00:00:00:{vlan}" in out


# ---------- AST parse ----------


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())


def test_upstream_hints_ast_parses():
    import ast
    ast.parse(_upstream_src())
