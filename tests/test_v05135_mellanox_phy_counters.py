"""v0.5.135: /api/interfaces prefers Mellanox PHY counters when set.

The pre-fix `/api/interfaces` only read `psutil.net_io_counters()`
which goes through `/proc/net/dev` — the kernel netdev path.

On srv06 with DPDK actively TX'ing, the kernel TX counter is
blind to DPDK PMD traffic. Operator saw the iface stats panel
report TX bps near zero while stream stats showed 100+ Gbps.

`ethtool -S <iface>` exposes Mellanox-specific PHY-layer counters
(`rx_packets_phy`, `tx_packets_phy`, `rx_bytes_phy`,
`tx_bytes_phy`) that count ALL packets at the hardware layer —
DPDK PMD + scapy + LLDP, regardless of which queues are bound to
what. v0.5.135 prefers these when present.

On srv06 over the iface lifetime:
  kernel tx_bytes   =       404 MB
  PHY    tx_bytes   =   109.5 TB    ← right
  kernel tx_packets =       3.2 M
  PHY    tx_packets =     140 B     ← right
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _fake_ethtool_output(rx_pkts, tx_pkts, rx_bytes, tx_bytes):
    """Mimic an `ethtool -S` stdout block with both kernel-style
    fields and the Mellanox PHY ones. The parser must pick only
    the `_phy` keys."""
    return f"""NIC statistics:
     rx_packets: 1000
     rx_bytes: 64000
     tx_packets: 500
     tx_bytes: 32000
     tx_packets_phy: {tx_pkts}
     rx_packets_phy: {rx_pkts}
     tx_bytes_phy: {tx_bytes}
     rx_bytes_phy: {rx_bytes}
     rx_crc_errors_phy: 0
     tx_multicast_phy: 100
"""


def test_phy_parser_extracts_only_phy_keys():
    """Stream of kernel + PHY counters in ethtool output. Parser
    keeps only the four `_phy` keys we care about."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = _fake_ethtool_output(
        rx_pkts=981_000_000_000, tx_pkts=140_000_000_000,
        rx_bytes=950_000_000_000_000, tx_bytes=109_000_000_000_000,
    )
    with patch("subprocess.run", return_value=fake):
        result = _mellanox_phy_counters("ens2f0np0")
    assert result == {
        "rx_packets_phy": 981_000_000_000,
        "tx_packets_phy": 140_000_000_000,
        "rx_bytes_phy":   950_000_000_000_000,
        "tx_bytes_phy":   109_000_000_000_000,
    }


def test_phy_parser_returns_none_when_ethtool_missing():
    """No ethtool binary → FileNotFoundError → None. Caller
    falls back to kernel netdev counts (pre-v0.5.135 behavior)."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _mellanox_phy_counters("ens2f0np0") is None


def test_phy_parser_returns_none_on_nonzero_exit():
    """ethtool returned an error (e.g., iface doesn't support PHY
    counters — Intel cards don't): None."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 1
    fake.stdout = ""
    with patch("subprocess.run", return_value=fake):
        assert _mellanox_phy_counters("eth0") is None


def test_phy_parser_returns_none_when_no_phy_keys():
    """Driver supports `ethtool -S` but doesn't emit `_phy` fields
    (most non-Mellanox NICs). Parser returns None — caller falls
    back to kernel counters."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = """NIC statistics:
     rx_packets: 1234
     tx_packets: 5678
     rx_bytes: 100000
     tx_bytes: 200000
"""
    with patch("subprocess.run", return_value=fake):
        assert _mellanox_phy_counters("eth0") is None


def test_phy_parser_ignores_malformed_lines():
    """Defensive: a line where the right-hand side isn't numeric
    is skipped rather than raising."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = """NIC statistics:
     tx_packets_phy: not-a-number
     rx_packets_phy: 500
     tx_bytes_phy: 600
"""
    with patch("subprocess.run", return_value=fake):
        result = _mellanox_phy_counters("ens2f0np0")
    # Garbage tx_packets_phy skipped; the two valid ones kept.
    assert result == {"rx_packets_phy": 500, "tx_bytes_phy": 600}


def test_phy_parser_caches_briefly():
    """The cache TTL prevents fork storms when the GUI polls every
    second. Second call within TTL doesn't invoke subprocess."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = _fake_ethtool_output(1, 1, 1, 1)
    call_count = {"n": 0}

    def _counting_run(*args, **kwargs):
        call_count["n"] += 1
        return fake

    with patch("subprocess.run", side_effect=_counting_run):
        a = _mellanox_phy_counters("ens2f0np0")
        b = _mellanox_phy_counters("ens2f0np0")
        c = _mellanox_phy_counters("ens2f0np0")
    assert a == b == c
    assert call_count["n"] == 1, (
        f"Cache should suppress repeat ethtool calls in the TTL "
        f"window. Got {call_count['n']} calls."
    )


def test_phy_parser_separate_caches_per_iface():
    """Different ifaces → separate cache entries. Both should hit
    ethtool independently."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    _PHY_CTRS_CACHE.clear()
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = _fake_ethtool_output(10, 10, 10, 10)
    call_count = {"n": 0}

    def _counting_run(*args, **kwargs):
        call_count["n"] += 1
        return fake

    with patch("subprocess.run", side_effect=_counting_run):
        _mellanox_phy_counters("ens2f0np0")
        _mellanox_phy_counters("ens2f1np1")
    assert call_count["n"] == 2


def test_phy_parser_handles_timeout():
    """A blocked ethtool call (rare but possible if the driver is
    wedged) shouldn't hang the API forever — the 2s timeout fires
    and we return None."""
    from run_tgen_server import _mellanox_phy_counters, _PHY_CTRS_CACHE
    import subprocess
    _PHY_CTRS_CACHE.clear()
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired("ethtool", 2.0)):
        assert _mellanox_phy_counters("ens2f0np0") is None
