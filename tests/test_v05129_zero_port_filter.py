"""v0.5.129: auto-lifecycle treats port 0 as "no filter" not "port 0".

The DPDK rx_worker takes optional `--src-port N` / `--dst-port N`
flags. Omitting the flag means "match any port"; passing the
flag with N requires the wire packet's port to equal N.

The stream dialog persists UDP port fields as "0" for streams
whose L4 isn't actually UDP (UEC emulation, ICMP — they fill in
their own protocol field, not the udp.* fields). Pre-v0.5.129
the auto-lifecycle `_maybe_start_dpdk_rx_for_stream` would read
those zeros, pass `--dst-port 0 --src-port 0` to rx_worker, and
the worker would then require every frame to literally have
port=0 → silently rejected everything → rx_count stuck at 0.

Caught on srv06 today: UEC stream's tx_worker was sending UDP
src=1234 dst=4791 (its real test flow), rx_worker's filter was
src=0 dst=0, matched_pkts stayed at 0 despite TX firing at
17 Mpps and the wire definitely delivering.

Fix: coerce port 0 → None at the lifecycle layer. None tells
rx_worker to skip the port-filter clause entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _capture_kwargs(stream_data):
    """Run the auto-lifecycle and snapshot the kwargs passed to
    rx_registry.start(). Returns the dst_port / src_port the
    rx_worker would be launched with."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    captured = {}
    fake_reg = MagicMock()
    fake_reg.start.side_effect = lambda **kw: captured.update(kw) or {"pid": 1}
    fake_reg._handles = {}

    with patch("utils.nic_counters.iface_to_pci_bdf",
               return_value="0000:2b:00.0"), \
         patch("utils.dpdk_rx_manager.registry",
               MagicMock(return_value=fake_reg)):
        _maybe_start_dpdk_rx_for_stream(
            stream_id="s1", rx_interface="ens2f1np1",
            stream_data=stream_data,
        )
    return captured


def _stream(*, dst_port="0", src_port="0"):
    """Stream JSON shaped the way the dialog produces it for a
    non-UDP stream — UDP port fields exist but hold '0'."""
    return {
        "engine": "dpdk", "rx_engine": "dpdk", "dpdk_enable": True,
        "interface": "ens2f0np0",
        "protocol_data": {
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
            "udp": {
                "udp_source_port": str(src_port),
                "udp_destination_port": str(dst_port),
            },
            "vlan": {"vlan_id": "1"},
        },
        "VLAN": "Untagged",
    }


def test_zero_dst_port_yields_no_filter():
    """The reported srv06 UEC bug. dst_port=0 in dialog → was
    passed literally to rx_worker → rejected every frame."""
    kw = _capture_kwargs(_stream(dst_port="0"))
    assert kw["dst_port"] is None, (
        f"dst_port=0 must be coerced to None. Got: {kw['dst_port']!r}"
    )


def test_zero_src_port_yields_no_filter():
    kw = _capture_kwargs(_stream(src_port="0"))
    assert kw["src_port"] is None


def test_both_zero_yields_no_filters():
    """Common case for UEC / ICMP streams: both ports default
    to 0. Neither filter clause should reach rx_worker."""
    kw = _capture_kwargs(_stream(dst_port="0", src_port="0"))
    assert kw["dst_port"] is None
    assert kw["src_port"] is None


def test_real_port_propagates():
    """Don't over-narrow the fix — real UDP streams must still
    get their port filter on rx_worker so the BPF stays tight."""
    kw = _capture_kwargs(_stream(dst_port="4791", src_port="1234"))
    assert kw["dst_port"] == 4791
    assert kw["src_port"] == 1234


def test_missing_udp_section_handled_safely():
    """Defensive: a stream config without protocol_data.udp
    shouldn't crash the helper. Both ports → None."""
    s = _stream()
    del s["protocol_data"]["udp"]
    kw = _capture_kwargs(s)
    assert kw["dst_port"] is None
    assert kw["src_port"] is None


def test_garbage_port_coerced_to_none():
    """'abc' or '99999' (out of range) → None."""
    kw = _capture_kwargs(_stream(dst_port="abc"))
    assert kw["dst_port"] is None
    kw = _capture_kwargs(_stream(dst_port="99999"))
    assert kw["dst_port"] is None


def test_one_is_a_real_port_and_passes_through():
    """Port 1 IS a valid UDP port (privileged but real). Don't
    over-narrow — only 0 means 'no filter'."""
    kw = _capture_kwargs(_stream(dst_port="1"))
    assert kw["dst_port"] == 1


def test_max_port_passes_through():
    kw = _capture_kwargs(_stream(dst_port="65535"))
    assert kw["dst_port"] == 65535
