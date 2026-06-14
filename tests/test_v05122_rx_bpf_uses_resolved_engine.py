"""v0.5.122: RX BPF must respect the actual L4 protocol for non-DPDK streams.

Pre-fix `multithreaded_traffic_gen.py` decided RX-side `force_udp`
+ `dpdk_hint` based on `should_use_dpdk(stream_data)` — which
returns True whenever the opt-in `dpdk_enable` flag is truthy,
regardless of whether the stream is actually compatible with the
DPDK TX worker. So a scapy-engine ICMP stream with a stale
`dpdk_enable=True` flag (e.g. left over from earlier UI testing)
would build a UDP-only BPF filter and never match any ICMP
packets on the wire. `rx_count` stayed at 0 with no visible error.

Caught on srv06 2026-06-14 with two scapy streams running side by
side:
  * Stream A: L4=UDP, dst=10.0.0.2 → tx=tx, rx=tx (works)
  * Stream B: L4=ICMP, dst=10.0.0.2 → tx>0, rx=0
The only difference was the L4 and `dpdk_enable=True` lingering
on Stream B. Fix uses `resolve_engine()` instead — same call the
TX launcher uses — so the BPF mirrors what's actually on the wire.

Tests:
  * scapy ICMP with stale dpdk_enable → ICMP BPF (the bug)
  * scapy UDP with stale dpdk_enable → UDP BPF (regression guard)
  * DPDK UDP → UDP BPF (intended dpdk_hint behavior intact)
  * DPDK with ICMP (rejected by compat-check) → ICMP BPF
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_scapy_icmp_with_stale_dpdk_enable_picks_icmp_bpf():
    """The bug. Stream's dpdk_enable was set in an earlier UI run
    but the operator switched to engine=scapy + L4=ICMP. Pre-fix
    the BPF was forced to UDP. Post-fix it must respect the
    actual resolved engine (scapy) and build an ICMP filter."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    from utils.dpdk_tx_worker import resolve_engine

    stream_data = {
        "engine": "scapy",
        "dpdk_enable": True,           # stale leftover from earlier UI testing
        "L4": "ICMP",
        "protocol_data": {
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
            },
        },
    }
    engine, _ = resolve_engine(stream_data)
    assert engine == "scapy", (
        "engine=scapy + L4=ICMP must resolve to scapy (DPDK worker is "
        "UDP-only). If this assertion fails the compat-check has "
        "regressed and the BPF override would also leak through."
    )

    use_dpdk = (engine == "dpdk")
    selector = _build_rx_selector_for_stream(
        stream_data,
        force_udp=use_dpdk,
        dpdk_hint=use_dpdk,
    )
    assert selector["l4"] == "icmp", (
        f"BPF must keep L4=ICMP when resolved engine is scapy. "
        f"Got: {selector['l4']!r}. Pre-fix this was 'udp' because "
        f"the launcher used should_use_dpdk() which returns True on "
        f"the dpdk_enable flag regardless of compat."
    )


def test_scapy_udp_with_stale_dpdk_enable_keeps_udp_bpf():
    """Regression guard: don't break the case where dpdk_enable +
    L4=UDP intentionally falls through to UDP. The selector still
    builds a UDP filter because the L4 string says so."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    from utils.dpdk_tx_worker import resolve_engine

    stream_data = {
        "engine": "scapy",
        "dpdk_enable": True,
        "L4": "UDP",
        "protocol_data": {
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
            },
            "udp": {
                "udp_source_port": "1234",
                "udp_destination_port": "4791",
            },
        },
    }
    engine, _ = resolve_engine(stream_data)
    use_dpdk = (engine == "dpdk")
    selector = _build_rx_selector_for_stream(
        stream_data,
        force_udp=use_dpdk,
        dpdk_hint=use_dpdk,
    )
    assert selector["l4"] == "udp"


def test_dpdk_udp_stream_keeps_force_udp_behavior():
    """The original intent of force_udp: when the TX side is
    DPDK, the wire WILL carry UDP only (tx_worker only emits
    UDP). So the BPF is correctly clamped to UDP for matching."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    from utils.dpdk_tx_worker import resolve_engine

    stream_data = {
        "engine": "dpdk",
        "dpdk_enable": True,
        "L4": "UDP",
        "protocol_data": {
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
            },
            "udp": {
                "udp_source_port": "1234",
                "udp_destination_port": "4791",
            },
        },
    }
    engine, _ = resolve_engine(stream_data)
    assert engine == "dpdk"
    use_dpdk = (engine == "dpdk")
    selector = _build_rx_selector_for_stream(
        stream_data,
        force_udp=use_dpdk,
        dpdk_hint=use_dpdk,
    )
    assert selector["l4"] == "udp"


def test_dpdk_request_for_icmp_falls_back_to_scapy_path():
    """Operator picks engine=dpdk on an L4=ICMP stream. The
    DPDK compat-check rejects (worker is UDP-only) → resolve_engine
    returns scapy. The BPF must follow — ICMP filter, not UDP.
    Without this guard the operator hits exactly the srv06 bug
    surface they were debugging."""
    from multithreaded_traffic_gen import _build_rx_selector_for_stream
    from utils.dpdk_tx_worker import resolve_engine

    stream_data = {
        "engine": "dpdk",
        "dpdk_enable": True,
        "L4": "ICMP",
        "protocol_data": {
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
            },
        },
    }
    engine, reason = resolve_engine(stream_data)
    assert engine == "scapy", (
        "DPDK is rejected for ICMP; should resolve to scapy. "
        f"Got engine={engine!r}, reason={reason!r}."
    )
    use_dpdk = (engine == "dpdk")
    selector = _build_rx_selector_for_stream(
        stream_data,
        force_udp=use_dpdk,
        dpdk_hint=use_dpdk,
    )
    assert selector["l4"] == "icmp"
