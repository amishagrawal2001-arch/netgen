"""v0.5.128: `_resolve_count_flags()` reads count fields from both
top-level (API-direct convention) and protocol_data.{ipv4,udp}
(dialog convention).

The block in run_stream() forwards --src/dst-{ip,port}-count
flags to tx_worker for per-flow randomization. Without these
flags, RSS sees a single 5-tuple and lands every packet on
queue 0 → multi-queue rx_worker provides zero benefit. The
operator's complaint today: set `dst_port_count=64` in the UI,
saw `ps aux | grep tx_worker` showing no `--dst-port-count` arg,
rx_pps stuck at 5 Mpps even after v0.5.127's rx_queues=8.

Pre-fix the resolver only read `stream_data["dst_port_count"]`
top-level. The dialog stores its value under
`protocol_data.udp.udp_destination_port_count` (longer name).
v0.5.128 looks in both; top-level wins when set.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _resolve(stream_data):
    from utils.dpdk_tx_worker import _resolve_count_flags
    return _resolve_count_flags(stream_data)


def _dialog_shape(*, dst_port_count=1, src_port_count=1,
                  src_ip_count=1, dst_ip_count=1):
    """Stream JSON shaped the way Edit-Save produces it. The
    field names mirror the actual dialog → protocol_data
    persistence we observed in the journal."""
    return {
        "engine": "dpdk", "dpdk_enable": True,
        "protocol_data": {
            "udp": {
                "udp_source_port": "222",
                "udp_destination_port": "1111",
                "udp_source_port_count": str(src_port_count),
                "udp_destination_port_count": str(dst_port_count),
            },
            "ipv4": {
                "ipv4_source": "10.0.0.1",
                "ipv4_destination": "10.0.0.2",
                "ipv4_source_increment_count": str(src_ip_count),
                "ipv4_destination_increment_count": str(dst_ip_count),
            },
        },
    }


# ----- The reported srv06 bug ------------------------------------------------


def test_dst_port_count_from_protocol_data_propagates():
    """The bug. dialog set
    protocol_data.udp.udp_destination_port_count=64; pre-fix the
    helper returned []."""
    flags = _resolve(_dialog_shape(dst_port_count=64))
    assert "--dst-port-count" in flags, (
        f"missing --dst-port-count when "
        f"udp_destination_port_count=64. Got: {flags}"
    )
    assert flags[flags.index("--dst-port-count") + 1] == "64"


def test_src_port_count_from_protocol_data_propagates():
    flags = _resolve(_dialog_shape(src_port_count=32))
    assert "--src-port-count" in flags
    assert flags[flags.index("--src-port-count") + 1] == "32"


def test_src_ip_count_from_protocol_data_propagates():
    flags = _resolve(_dialog_shape(src_ip_count=4))
    assert "--src-ip-count" in flags
    assert flags[flags.index("--src-ip-count") + 1] == "4"


def test_dst_ip_count_from_protocol_data_propagates():
    flags = _resolve(_dialog_shape(dst_ip_count=8))
    assert "--dst-ip-count" in flags
    assert flags[flags.index("--dst-ip-count") + 1] == "8"


# ----- Backward compat -------------------------------------------------------


def test_top_level_short_form_still_works():
    """API-direct callers using the old short names at top level
    must keep working."""
    s = _dialog_shape()
    s["dst_port_count"] = 16
    flags = _resolve(s)
    assert "--dst-port-count" in flags
    assert flags[flags.index("--dst-port-count") + 1] == "16"


def test_top_level_takes_precedence():
    """When both top-level and protocol_data have values, top-
    level wins (explicit user intent)."""
    s = _dialog_shape(dst_port_count=64)
    s["dst_port_count"] = 8
    flags = _resolve(s)
    assert flags[flags.index("--dst-port-count") + 1] == "8"


def test_count_one_or_zero_yields_no_flag():
    """Default (no randomization) → no flag."""
    assert _resolve(_dialog_shape(dst_port_count=1)) == []
    assert _resolve(_dialog_shape(dst_port_count=0)) == []


def test_invalid_value_silently_skips_flag():
    """Garbage in count fields shouldn't crash."""
    s = _dialog_shape()
    s["protocol_data"]["udp"]["udp_destination_port_count"] = "bogus"
    assert _resolve(s) == []


def test_all_four_at_once():
    """Real-world stress: every count field set; helper emits all
    four flags in order."""
    s = _dialog_shape(
        src_ip_count=2, dst_ip_count=2,
        src_port_count=32, dst_port_count=64,
    )
    flags = _resolve(s)
    for needed in ("--src-ip-count", "--dst-ip-count",
                   "--src-port-count", "--dst-port-count"):
        assert needed in flags, f"missing {needed} in {flags}"


def test_no_protocol_data_no_flags():
    """Defensive: a stream config without protocol_data shouldn't
    crash the helper."""
    assert _resolve({"engine": "dpdk"}) == []
