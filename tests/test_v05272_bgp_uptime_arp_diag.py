"""v0.5.272 — BGP state parser: >24h FRR uptimes must resolve
Established, not fall through to Unknown/Idle. Plus IPv4 gateway
ARP diagnostic parity with IPv6."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()


# --- BGP-G1: uptime-shape parser ---------------------------------


def test_bgp_uptime_helper_defined_and_used():
    """The helper `_uptime_indicates_up` centralises the sentinel
    check so both call sites can't drift apart again."""
    assert "v0.5.272 (BGP-G1)" in SRV
    assert "def _uptime_indicates_up" in SRV
    # Both branches call the helper — the primary prefix-count
    # detection and the last-resort fallback.
    idx = SRV.find("v0.5.272 (BGP-G1)")
    body = SRV[idx:idx + 4000]
    # Uses in prefix-count branch AND in the last-resort fallback.
    assert body.count("_uptime_indicates_up(uptime)") >= 2


def test_bgp_no_more_colon_uptime_check():
    """The pre-fix `":" in uptime` short-circuit that missed FRR
    uptimes of shape "2d21h47m" / "1w2d3h" must be gone from the
    parser (comments referencing it as history are fine)."""
    idx = SRV.find("def get_device_bgp_status")
    end = SRV.find("\n@app.route(", idx + 1)
    body = SRV[idx:end]
    live = [ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any('":" in uptime' in ln for ln in live), (
        "the pre-fix colon-only uptime check must be gone from live "
        "code — see BGP-G1 rationale in the CHANGELOG"
    )


def test_bgp_uptime_helper_rejects_sentinels():
    """The helper must reject the four sentinels FRR uses to say
    'never up'."""
    idx = SRV.find("def _uptime_indicates_up")
    end = SRV.find("\n", SRV.find("return", idx))
    body = SRV[idx:end + 200]
    # The sentinel set that must be rejected.
    for sentinel in ('"00:00:00"', '"never"', '"0"', '"-"'):
        assert sentinel in body, (
            f"{sentinel} missing from uptime-sentinel rejection set"
        )


def test_bgp_state_parser_documents_three_frr_uptime_shapes():
    """The v0.5.272 comment must enumerate FRR's three uptime
    shapes so a future reader knows why the colon check was
    wrong."""
    idx = SRV.find("v0.5.272 (BGP-G1)")
    body = SRV[idx:idx + 2000]
    # Two of the three FRR uptime shapes are cited in the comment.
    assert 'HH:MM:SS' in body
    assert 'XdYYhZZm' in body or 'XwYdZZh' in body


# --- ARP-G2: IPv4 gateway `ip neigh` dump on double-fail ---------


def test_ipv4_gateway_dumps_neigh_when_both_ping_and_neigh_fail():
    """v0.5.272 (ARP-G2): parity with the IPv6 branch — the IPv4
    gateway path must dump the raw `ip neigh show` output into
    details.gateway_neigh when both ping and the neigh fallback
    fail. Pre-fix only IPv6 carried this diagnostic; IPv4 left
    the operator flying blind."""
    idx = SRV.find("v0.5.272 (ARP-G2)")
    assert idx > 0, "ARP-G2 marker missing"
    body = SRV[idx:idx + 2000]
    # The subprocess call runs `ip neigh show to <gateway>` under
    # the same VRF prefix used by everything else in the endpoint.
    assert 'list(ping_prefix)' in body
    assert '"ip", "neigh", "show", "to", ipv4_gateway' in body
    # And the result lands in details.gateway_neigh (parity with
    # details.ipv6_neigh from v0.5.262 ARP-8).
    assert '"gateway_neigh"' in body


def test_ipv4_gateway_neigh_dump_only_runs_on_arp_fail():
    """The neigh dump must NOT run on the happy path (ping ok, or
    ping fail + neigh fallback succeeded). That would be wasted
    subprocess overhead + noisy details for every poll."""
    # Anchor the slice on the `if ipv4_gateway:` block so the
    # search sees the whole branch: happy path (ping ok), fallback
    # (neigh ok), and double-failure (dump lands here).
    branch_idx = SRV.find("# Check gateway connectivity")
    assert branch_idx > 0
    branch_end = SRV.find("# v0.5.193: `requires_ipv6`", branch_idx)
    body = SRV[branch_idx:branch_end]
    # arp_gateway_resolved is set to False in the double-fail
    # branch; the neigh dump must appear AFTER that assignment.
    gate_idx = body.find('arp_results["arp_gateway_resolved"] = False')
    dump_idx = body.find('"gateway_neigh"')
    assert 0 < gate_idx < dump_idx, (
        "the neigh dump must be nested inside the double-failure "
        "branch (after arp_gateway_resolved=False)"
    )
    # And the marker for the v0.5.272 fix itself sits between the
    # False assignment and the dump — so the reader hits the
    # rationale before the mechanics.
    marker_idx = body.find("v0.5.272 (ARP-G2)")
    assert gate_idx < marker_idx < dump_idx


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 272)
