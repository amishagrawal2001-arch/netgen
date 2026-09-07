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
    """v0.5.272 (ARP-G2): the IPv4 gateway path must dump the raw
    `ip neigh show` output into details.gateway_neigh when the
    check remains unresolved (post-arp-warm). Pre-v0.5.272 the
    IPv4 branch had no diagnostic; the operator was flying blind.

    v0.5.277 (ARP-H1): the check order flipped from ping-first
    to neigh-first with ping as arp-warm secondary, and the
    diagnostic dump moved to run whenever the gateway is
    still-unresolved (regardless of which specific path failed).
    Anchor to the v0.5.277 marker since the block was rewritten;
    the diagnostic behavior it guaranteed is preserved."""
    idx = SRV.find("v0.5.277 (ARP-H1)")
    assert idx > 0, "ARP-H1 (successor to ARP-G2) marker missing"
    body = SRV[idx:idx + 4000]
    # The subprocess call runs `ip neigh show to <gateway>` under
    # the same VRF prefix used by everything else in the endpoint.
    assert 'list(ping_prefix)' in body
    assert '"ip", "neigh", "show", "to", ipv4_gateway' in body
    # And the result lands in details.gateway_neigh (parity with
    # details.ipv6_neigh from v0.5.262 ARP-8).
    assert '"gateway_neigh"' in body


def test_ipv4_gateway_neigh_dump_only_runs_on_arp_fail():
    """The neigh dump must NOT run on the happy path (neigh cache
    hit, or neigh resolved after arp-warm). Waste-of-cycles +
    noisy details on every 30s poll otherwise.

    v0.5.277 (ARP-H1): dump is gated by
    `if not arp_results["arp_gateway_resolved"]:` — an explicit
    guard rather than being nested in an else-branch. Same
    invariant, cleaner structure."""
    branch_idx = SRV.find("# Check gateway connectivity")
    assert branch_idx > 0
    branch_end = SRV.find("# v0.5.193: `requires_ipv6`", branch_idx)
    body = SRV[branch_idx:branch_end]
    # The dump is guarded by `if not arp_results["arp_gateway_
    # resolved"]:` — it never runs on the resolved path.
    guard_idx = body.find(
        'if not arp_results["arp_gateway_resolved"]:'
    )
    dump_idx = body.find('"gateway_neigh"')
    assert 0 < guard_idx < dump_idx, (
        "the neigh dump must be nested inside "
        "`if not arp_results['arp_gateway_resolved']:`"
    )


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 272)
