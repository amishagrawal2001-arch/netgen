"""v0.5.277 — ARP gateway check: flip from ping-first to
neigh-first, fix VRF-detect Docker init race. Source-level
regression assertions."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()


# --- ARP-H1: neigh-first ordering ---------------------------------


def test_gateway_check_calls_neigh_before_ping():
    """The IPv4 gateway block must call `_neigh_state_ok` FIRST,
    with ping only running as arp-warm when the initial neigh
    check misses. Ordering: neigh → (if miss) ping-warm → neigh."""
    idx = SRV.find("# Check gateway connectivity")
    end = SRV.find("# v0.5.193: `requires_ipv6`", idx)
    body = SRV[idx:end]
    assert "v0.5.277 (ARP-H1)" in body
    # First `_neigh_state_ok` call precedes the first ping.
    first_neigh = body.find("_neigh_state_ok(ipv4_gateway")
    first_ping = body.find('subprocess.run(\n')
    # We can't reliably find "the first ping" by regex; instead
    # walk the source to find the earliest `subprocess.run(...
    # "ping"` line.
    first_ping = body.find('"ping"')
    assert 0 < first_neigh < first_ping, (
        "neigh check must precede ping (arp-warm) — see ARP-H1"
    )


def test_gateway_check_records_which_path_won():
    """`details.gateway_check_path` records `neigh_cache_hit` /
    `neigh_after_arp_warm` / `neigh_still_incomplete` so operators
    can see WHY the gateway resolved (or didn't)."""
    idx = SRV.find("v0.5.277 (ARP-H1)")
    body = SRV[idx:idx + 12000]
    assert '"gateway_check_path"' in body
    # The three possible win states.
    for state in ("neigh_cache_hit", "neigh_after_arp_warm",
                  "neigh_still_incomplete"):
        assert f'"{state}"' in body, (
            f"missing gateway_check_path state {state!r}"
        )


def test_gateway_ping_arp_warm_ignores_exit_code():
    """The arp-warm ping's exit code is IRRELEVANT — Junos may
    drop the echo but still answer ARP. The check must be
    re-neigh, not re-ping-exit-code."""
    idx = SRV.find("v0.5.277 (ARP-H1)")
    body = SRV[idx:idx + 12000]
    # After the arp-warm ping, we must re-call _neigh_state_ok,
    # NOT check the ping's returncode again.
    warm_idx = body.find('"gateway_arp_warm"')
    reneigh_idx = body.find(
        "_neigh_state_ok(ipv4_gateway, family=\"ipv4\")", warm_idx,
    )
    assert 0 < warm_idx < reneigh_idx, (
        "must re-check neigh after arp-warm, not re-check ping"
    )


def test_gateway_check_still_dumps_neigh_output_on_failure():
    """The v0.5.272 diagnostic (dump `ip neigh show` output when
    the gateway remains unresolved) survives the refactor."""
    idx = SRV.find("v0.5.277 (ARP-H1)")
    body = SRV[idx:idx + 12000]
    assert '"gateway_neigh"' in body
    assert '"ip", "neigh", "show", "to", ipv4_gateway' in body


# --- ARP-H2: VRF-detect Docker init race -------------------------


def test_vrf_detect_uses_lazy_frr_manager_singleton():
    """Pre-fix imported `FRRDockerManager` and instantiated it on
    every call. New code uses the module-level `frr_manager` lazy
    proxy — no per-call Docker connect."""
    idx = SRV.find("v0.5.277 (ARP-H2)")
    assert idx > 0, "ARP-H2 marker missing"
    body = SRV[idx:idx + 2000]
    # Import the proxy, not the class.
    assert "from utils.frr_docker import frr_manager" in body
    assert "_frr.vrf_name_for_device(device_id)" in body
    # The old `FRRDockerManager()` constructor call is gone from
    # LIVE code in this block. Comment references (documenting the
    # pre-fix behavior) are fine — anchor to non-comment lines.
    live = [ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("FRRDockerManager()" in ln for ln in live), (
        "live call to FRRDockerManager() still present in ARP-H2 "
        "block; must use the frr_manager singleton"
    )


def test_vrf_detect_timeout_raised_to_5s():
    """2s was too tight — under load `docker.from_env()` alone
    exceeded that. Raised to 5s to give the netlink probe headroom."""
    idx = SRV.find("v0.5.277 (ARP-H2)")
    body = SRV[idx:idx + 2000]
    # `ip -o link show <vrf>` with timeout=5.
    assert "timeout=5" in body
    # And the previous timeout=2 is gone from THIS block.
    lines_with_timeout_2 = [
        ln for ln in body.splitlines()
        if "timeout=2" in ln and not ln.lstrip().startswith("#")
    ]
    assert not lines_with_timeout_2, (
        f"live timeout=2 in ARP-H2 block: {lines_with_timeout_2!r}"
    )


def test_vrf_detect_logs_warning_when_probe_returns_nonzero():
    """Silent-orange debug aid: when the derived VRF name doesn't
    resolve to an interface, log a warning telling the operator
    the ARP probe ran in default netns. Otherwise a green BGP
    session with orange gateway is unexplained."""
    idx = SRV.find("v0.5.277 (ARP-H2)")
    body = SRV[idx:idx + 2500]
    assert "derived" in body and "vrf name" in body
    assert "default netns" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 277)
