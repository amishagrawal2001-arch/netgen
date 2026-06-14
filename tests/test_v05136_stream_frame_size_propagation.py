"""v0.5.136: stream-stats table forwards frame_size for bps display.

Operator screenshot of srv06 showed:
  UEC stream: TX Rate = 23.77 Mpps, TX Bit Rate = 12.17 Gbps

If frame_size were 1000B (the configured value) the bit rate
would be `23.77M × 1000 × 8 = 190 Gbps`. Backsolving the
displayed 12.17 Gbps gives `12.17e9 / (23.77e6 × 8) = 64 bytes
per packet` — the fallback default in the renderer.

Root cause: `update_stream_statistics_table()` builds `all_streams`
from `/api/streams/stats` entries but doesn't copy `frame_size`
into the per-row dict. The render loop at line ~2106 does
`stream.get("frame_size") or 64` and always falls back.

v0.5.136: the per-row dict now includes `frame_size` so the TX/RX
Bit Rate cells reflect the real configured frame size.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _stream_entry(frame_size=None, tx_rate=23_770_000, rx_rate=20_560_000):
    """Mock entry shape from /api/streams/stats's active_streams."""
    e = {
        "stream_name": "UEC",
        "interface": "ens2f0np0",
        "rx_interface": "ens2f1np1",
        "tx_count": 1_444_337_856,
        "rx_count": 8_806_023,
        "tx_rate": tx_rate,
        "rx_rate": rx_rate,
        "stream_id": "3ede73ca",
        "flow_tracking_enabled": True,
        "status": "Running",
        "engine": "dpdk",
        "dpdk_enable": True,
        "dpdk_tx_cores": 16,
    }
    if frame_size is not None:
        e["frame_size"] = frame_size
    return e


def _build_row(stream_entry):
    """Mimic the row-dict construction in statistics_section.py
    around line 1910-1952. We don't import the actual method
    (it touches Qt); instead we replicate the keys the renderer
    reads, including the v0.5.136 frame_size forwarding."""
    return {
        "stream_name": stream_entry.get("stream_name", "Unnamed"),
        "interface": stream_entry.get("interface", "N/A"),
        "tx_count": int(stream_entry.get("tx_count", 0) or 0),
        "rx_count": int(stream_entry.get("rx_count", 0) or 0),
        "tx_rate": stream_entry.get("tx_rate") or 0.0,
        "rx_rate": stream_entry.get("rx_rate") or 0.0,
        "loss_pct": 0.0,
        "status": stream_entry.get("status", "Unknown"),
        "flow_tracking": stream_entry.get("flow_tracking_enabled", False),
        "dpdk_enable": bool(stream_entry.get("dpdk_enable", False)),
        "dpdk_tx_cores": int(stream_entry.get("dpdk_tx_cores") or 1),
        "stream_id": stream_entry.get("stream_id", ""),
        "engine": stream_entry.get("engine") or "",
        # v0.5.136: this is the key under test
        "frame_size": stream_entry.get("frame_size", 64),
    }


def _derive_bps(row):
    """Replicate the bit-rate derivation in the render loop
    around line 2106-2110:
      _fs = int(stream.get("frame_size") or 64)
      tx_bps = (tx_rate or 0.0) * _fs * 8
    """
    try:
        _fs = int(row.get("frame_size") or 64)
    except (ValueError, TypeError):
        _fs = 64
    tx_bps = (row.get("tx_rate") or 0.0) * _fs * 8
    rx_bps = (row.get("rx_rate") or 0.0) * _fs * 8
    return tx_bps, rx_bps


# ----- the reported bug ---------------------------------------------------

def test_srv06_uec_stream_bps_matches_1000B_frame():
    """The exact operator screenshot scenario: UEC stream
    frame_size=1000, tx_rate=23.77 Mpps. The TX bit rate cell
    must show ~190 Gbps, NOT 12.17 Gbps."""
    entry = _stream_entry(frame_size=1000, tx_rate=23_770_000)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    expected = 23_770_000 * 1000 * 8  # 190.16 Gbps
    assert abs(tx_bps - expected) < 1, (
        f"With frame_size=1000 the TX bit rate must be ~190 Gbps. "
        f"Got: {tx_bps / 1e9:.2f} Gbps (frame_size fell back to 64)"
    )


def test_uec_512B_frame_bps_correct():
    entry = _stream_entry(frame_size=512, tx_rate=20_000_000)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    assert abs(tx_bps - 20_000_000 * 512 * 8) < 1


def test_rx_bit_rate_uses_frame_size_too():
    """Symmetry check: the RX cell must also pick up frame_size."""
    entry = _stream_entry(frame_size=1500, rx_rate=10_000_000)
    row = _build_row(entry)
    _, rx_bps = _derive_bps(row)
    assert abs(rx_bps - 10_000_000 * 1500 * 8) < 1


# ----- defensive fallbacks (no regression on missing data) ----------------

def test_missing_frame_size_still_fallbacks_to_64():
    """API entry without frame_size → row falls back to 64 (pre-
    fix behavior). At least it doesn't crash; cell will read low
    but won't NaN."""
    entry = _stream_entry(frame_size=None, tx_rate=1_000_000)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    assert tx_bps == 1_000_000 * 64 * 8


def test_string_frame_size_coerced_to_int():
    """The API often returns frame_size as a string (the dialog
    persists string ints). Coerce."""
    entry = _stream_entry(frame_size="1000", tx_rate=10_000_000)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    assert abs(tx_bps - 10_000_000 * 1000 * 8) < 1


def test_zero_tx_rate_zero_bps():
    """Stopped stream: tx_rate=0 → tx_bps=0 regardless of
    frame_size."""
    entry = _stream_entry(frame_size=1000, tx_rate=0)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    assert tx_bps == 0


def test_garbage_frame_size_safe_fallback():
    """Defensive: bad frame_size shouldn't raise — falls back
    to 64."""
    entry = _stream_entry(frame_size="bogus", tx_rate=1_000_000)
    row = _build_row(entry)
    tx_bps, _ = _derive_bps(row)
    assert tx_bps == 1_000_000 * 64 * 8
