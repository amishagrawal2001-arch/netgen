"""v0.5.278 — Gateway-orange class, take 6: three belt-and-
suspenders fallbacks after v0.5.277 didn't stick for BGP peers
on srv06. Source-level assertions on the endpoint block."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()


def _endpoint_body():
    idx = SRV.find("# Check gateway connectivity")
    assert idx > 0
    end = SRV.find("# v0.5.193: `requires_ipv6`", idx)
    return SRV[idx:end]


# --- ARP-H5: any-protocol-established short-circuit --------------


def test_any_protocol_established_short_circuits_gateway_check():
    """If ANY routing protocol (BGP / OSPF / ISIS) has an
    Established/Full/Up adjacency, the gateway IS resolved by
    definition — the protocol handshake required ARP to work.
    Operator may configure any / none / all three; any of them
    being up is sufficient."""
    body = _endpoint_body()
    assert "v0.5.278" in body
    assert "ARP-H5" in body
    # All five relevant flags are consulted. Some are on long
    # `device.get(\n    "…"\n)` lines so match the key string
    # itself rather than a same-line `device.get("…")` form.
    for key in (
        '"bgp_established"',
        '"bgp_ipv4_established"',
        '"ospf_established"',
        '"ospf_ipv4_established"',
        '"isis_established"',
    ):
        assert key in body, f"short-circuit does not consult {key}"
    # Path marker: built via f-string interpolation on
    # _short_circuit_proto, so one line covers all three.
    assert 'f"{_short_circuit_proto}_established_short_circuit"' in body
    # Each protocol is assigned as a proto name earlier — the
    # assignment lines must be present so the operator knows
    # which protocols contribute.
    for proto in ("bgp", "ospf", "isis"):
        assert f'_short_circuit_proto = "{proto}"' in body, (
            f"missing short-circuit proto assignment for {proto}"
        )


def test_short_circuit_precedes_neigh_check():
    """The short-circuit must fire BEFORE the neigh check so
    devices with any working adjacency never wait on flaky
    neigh probes."""
    body = _endpoint_body()
    # `_short_circuit_proto` gets assigned before the neigh
    # cache check.
    sc_idx = body.find("_short_circuit_proto = None")
    neigh_idx = body.find('"neigh_cache_hit"')
    assert 0 < sc_idx < neigh_idx, (
        "any-protocol short-circuit must be evaluated before neigh_cache_hit"
    )


def test_short_circuit_names_the_winning_protocol():
    """`gateway_check_path` records which protocol short-circuited
    the check (bgp / ospf / isis) so operators can trace the
    decision back — helps debug when the short-circuit fires but
    the actual ARP entry is broken (rare but possible: routing
    stack cached a MAC that has since aged out at L2)."""
    body = _endpoint_body()
    assert "_short_circuit_proto" in body
    # Path built with the proto name interpolated.
    assert 'f"{_short_circuit_proto}_established_short_circuit"' in body


# --- ARP-H3: arping as arp-warm ----------------------------------


def test_arp_warm_prefers_arping_over_ping():
    """`arping -c 1 -w 2 -I <iface> <gw>` is L2 and bypasses IP
    routing — needed when the VRF's routing table is missing the
    connected route."""
    body = _endpoint_body()
    assert "ARP-H3" in body
    assert '"arping"' in body
    assert '"-c", "1"' in body
    assert '"-w", "2"' in body
    assert '"-I", _iface_for_arp' in body
    # And path markers surface which arp-warm ran.
    assert '"arping-ok"' in body
    assert '"arping-fail"' in body
    assert '"arping-not-installed"' in body


def test_arp_warm_falls_back_to_ping_when_arping_missing():
    """Some base images don't ship iputils-arping. When arping
    isn't found (or fails), fall back to the historical ping-warm
    so behavior on those hosts is no worse than v0.5.277."""
    body = _endpoint_body()
    # The fallback branch triggers on skip / not-installed / fail.
    assert 'in ("skip", "arping-not-installed",' in body
    assert '"arping-fail"' in body
    # And still runs a ping.
    assert '"ping"' in body


def test_arp_warm_catches_arping_not_installed_via_filenotfounderror():
    """`FileNotFoundError` is what subprocess.run raises when the
    executable is missing on PATH — that's the signal we need to
    fall back cleanly rather than reporting a generic error."""
    body = _endpoint_body()
    assert "except FileNotFoundError" in body


# --- ARP-H4: unwrapped-neigh fallback ----------------------------


def test_unwrapped_neigh_retry_when_vrf_wrap_returns_empty():
    """The v0.5.277 neigh check uses `ip vrf exec ... ip neigh
    show`. Netlink IS namespace-scoped but not cgroup-scoped, so
    unwrapping should be equivalent — but belt-and-suspenders
    covers kernel edge cases."""
    body = _endpoint_body()
    assert "ARP-H4" in body
    assert '"neigh_no_vrf_fallback"' in body
    # Same fallback re-runs after arp-warm.
    assert '"neigh_after_arp_warm_no_vrf"' in body
    # Implementation: save/restore ping_prefix around the retry so
    # subsequent code paths see the correct value.
    assert "_saved_prefix = ping_prefix" in body
    assert "ping_prefix = _saved_prefix" in body


# --- Diagnostic dump: both wrapped and unwrapped ------------------


def test_neigh_diagnostic_dump_splits_vrf_vs_host_view():
    """When the gateway is still unresolved, dump the raw
    `ip neigh show` output from BOTH the VRF-wrapped and
    unwrapped contexts so kernel-context differences show."""
    body = _endpoint_body()
    assert '"gateway_neigh_vrf"' in body
    assert '"gateway_neigh_host"' in body


def test_back_compat_gateway_neigh_key_still_populated():
    """v0.5.272-v0.5.277 clients read `gateway_neigh`. v0.5.278
    splits it into vrf/host but must keep the old key as an alias
    so pre-upgrade clients don't lose the diagnostic."""
    body = _endpoint_body()
    assert '"gateway_neigh"' in body
    assert 'arp_results["details"].get("gateway_neigh_vrf"' in body


# --- Path enumeration ---------------------------------------------


def test_all_win_states_documented_and_present():
    """Every path the endpoint can take must be enumerated as a
    `gateway_check_path` string constant so operators can grep the
    docs when a specific state confuses them."""
    body = _endpoint_body()
    # The short-circuit path is built by f-string interpolation
    # (`f"{proto}_established_short_circuit"`) so it doesn't
    # appear as a literal per-protocol — instead, check for the
    # f-string template.
    assert 'f"{_short_circuit_proto}_established_short_circuit"' in body
    # The remaining states are literals.
    for state in (
        "neigh_cache_hit",
        "neigh_no_vrf_fallback",
        "neigh_after_arp_warm",
        "neigh_after_arp_warm_no_vrf",
        "neigh_still_incomplete",
    ):
        assert f'"{state}"' in body, (
            f"gateway_check_path state {state!r} missing"
        )


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 278)
