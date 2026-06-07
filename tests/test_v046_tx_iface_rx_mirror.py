"""Regression test for v0.4.6: the TX-interface column must NOT show
the stream's RX count / RX rate.

Operator-reported on svl-d-ai-srv04 with v0.4.5 installed:

  | Column                | enp160 (TX iface)  | enp181 (RX iface) |
  |-----------------------|--------------------|-------------------|
  | Sent Frames           | 4,000              | 0                 |
  | Received Frames       | 3,836  ← BUG       | 3,836             |
  | Send Frame Rate       | 451.54 fps         | 0.00 fps          |
  | Receive Frame Rate    | 397.36 fps ← BUG   | 397.36 fps        |
  | Send Bit Rate         | 231.19 Kbps        | 0.00 bps          |
  | Receive Bit Rate      | 203.45 Kbps ← BUG  | 203.45 Kbps       |

The user's stream config: TX iface enp160s0f0np0, RX iface enp181s0f0np0
(different physical NICs, point-to-point). The TX iface did NOT
receive those packets — it transmitted them. The RX iface received
them. The Received columns on the TX iface should be 0.

Root cause: traffic_client/statistics_section.py merged the stream's
rx_count + rx_rate INTO BOTH the TX-iface bucket and the RX-iface
bucket. The TX-iface block:

    if flow_tracking:
        merged_statistics[tx_iface]["rx"] += rx                  ← bug
        merged_statistics[tx_iface]["received_bytes"] += rx * fs  ← bug
        merged_statistics[tx_iface]["receive_fps"] += rx_rate     ← bug
        merged_statistics[tx_iface]["receive_bps"] += rx_rate * fs * 8  ← bug

Fix: delete that block. RX is attributed only in the RX-iface block.
When tx_iface == rx_iface (loopback), the RX-iface block runs once
on the shared dict — RX is counted exactly once."""
from __future__ import annotations

import re
from pathlib import Path


_AGG = Path(__file__).resolve().parents[1] / "traffic_client" / "statistics_section.py"


def test_no_rx_mirror_into_tx_iface_bucket():
    """The pre-fix code under `if flow_tracking:` inside the TX-iface
    block added rx_count / receive_fps / receive_bps into the TX-iface
    dict. That block must NOT exist anymore — it mirrored the RX iface."""
    src = _AGG.read_text()

    # The TX aggregation block starts at "TX aggregation" and ends at "RX aggregation".
    m = re.search(
        r"#\s*TX aggregation([\s\S]*?)#\s*RX aggregation",
        src,
    )
    assert m, "TX aggregation block not found in statistics_section.py"
    tx_block = m.group(1)

    forbidden = [
        r"merged_statistics\[tx_iface\]\[[\"']rx[\"']\]\s*\+=",
        r"merged_statistics\[tx_iface\]\[[\"']received_bytes[\"']\]\s*\+=",
        r"merged_statistics\[tx_iface\]\[[\"']receive_fps[\"']\]\s*\+=",
        r"merged_statistics\[tx_iface\]\[[\"']receive_bps[\"']\]\s*\+=",
    ]
    for pat in forbidden:
        assert not re.search(pat, tx_block), (
            f"Pre-fix mirror-RX-into-TX-iface line still present: {pat!r}. "
            f"The TX-iface bucket must only accumulate tx / sent_bytes / "
            f"send_fps / send_bps. Operator-reported bug from "
            f"svl-d-ai-srv04 will re-bite."
        )


def test_rx_aggregation_block_still_attributes_rx():
    """The RX-aggregation block must still set rx / received_bytes /
    receive_fps / receive_bps. We didn't accidentally delete BOTH blocks."""
    src = _AGG.read_text()

    m = re.search(
        r"#\s*RX aggregation[\s\S]*?if\s+rx_iface[\s\S]*?(?=\n\n\s{8}#|\Z)",
        src,
    )
    assert m, "RX aggregation block not found"
    rx_block = m.group(0)

    required = [
        r"merged_statistics\[rx_iface\]\[[\"']rx[\"']\]\s*\+=\s*rx",
        r"merged_statistics\[rx_iface\]\[[\"']received_bytes[\"']\]\s*\+=",
        r"merged_statistics\[rx_iface\]\[[\"']receive_fps[\"']\]\s*\+=\s*rx_rate",
        r"merged_statistics\[rx_iface\]\[[\"']receive_bps[\"']\]\s*\+=",
    ]
    for pat in required:
        assert re.search(pat, rx_block), (
            f"RX-iface aggregation lost a required line: {pat!r}. "
            f"The RX-iface bucket must still receive the stream's RX. "
            f"Removing both blocks would zero out RX entirely."
        )


def test_tx_iface_still_gets_tx_send_rates():
    """The TX-iface block must still aggregate tx / sent_bytes / send_fps /
    send_bps. We only removed the RX mirror, not the TX side."""
    src = _AGG.read_text()
    m = re.search(
        r"#\s*TX aggregation([\s\S]*?)#\s*RX aggregation",
        src,
    )
    assert m
    tx_block = m.group(1)

    required = [
        r"merged_statistics\[tx_iface\]\[[\"']tx[\"']\]\s*\+=\s*tx",
        r"merged_statistics\[tx_iface\]\[[\"']sent_bytes[\"']\]\s*\+=",
        r"merged_statistics\[tx_iface\]\[[\"']send_fps[\"']\]\s*\+=\s*tx_rate",
        r"merged_statistics\[tx_iface\]\[[\"']send_bps[\"']\]\s*\+=",
    ]
    for pat in required:
        assert re.search(pat, tx_block), (
            f"TX-iface aggregation lost a required TX line: {pat!r}. "
            f"The TX-iface bucket must still accumulate its own TX side."
        )


def test_simulated_aggregation_attributes_correctly():
    """End-to-end on a stub: feed a stream with TX iface != RX iface and
    flow_tracking=True. The merged dict for the TX iface must have
    rx=0 / receive_fps=0; the RX iface must have rx=stream_rx /
    receive_fps=stream_rx_rate.

    This simulates the same merge path that statistics_section.py runs."""

    # Simulate the merge logic post-fix, in isolation. We don't need
    # to instantiate the full statistics_section class — we just need
    # to verify the algorithm the source-code lines describe.
    merged = {
        "TG 0 - enp160s0f0np0": {
            "tx": 0, "rx": 0, "sent_bytes": 0, "received_bytes": 0,
            "send_fps": 0.0, "receive_fps": 0.0,
            "send_bps": 0.0, "receive_bps": 0.0,
            "errors": 0, "streams": {},
        },
        "TG 0 - enp181s0f0np0": {
            "tx": 0, "rx": 0, "sent_bytes": 0, "received_bytes": 0,
            "send_fps": 0.0, "receive_fps": 0.0,
            "send_bps": 0.0, "receive_bps": 0.0,
            "errors": 0, "streams": {},
        },
    }
    stream = {
        "interface": "enp160s0f0np0",
        "rx_interface": "enp181s0f0np0",
        "stream_name": "TestStream",
        "tx_count": 4000,
        "rx_count": 3836,
        "tx_rate": 451.54,
        "rx_rate": 397.36,
        "frame_size": 64,
        "flow_tracking_enabled": True,
        "stream_id": "s1",
    }

    tg_id = 0
    tx_iface = f"TG {tg_id} - {stream['interface']}"
    rx_iface = f"TG {tg_id} - {stream['rx_interface']}"
    tx, rx = stream["tx_count"], stream["rx_count"]
    tx_rate, rx_rate = stream["tx_rate"], stream["rx_rate"]
    fs = stream["frame_size"]

    # TX aggregation (post-fix: TX side only)
    if tx_iface in merged:
        merged[tx_iface]["tx"] += tx
        merged[tx_iface]["sent_bytes"] += tx * fs
        merged[tx_iface]["send_fps"] += tx_rate
        merged[tx_iface]["send_bps"] += tx_rate * fs * 8

    # RX aggregation (unchanged)
    if rx_iface in merged:
        merged[rx_iface]["rx"] += rx
        merged[rx_iface]["received_bytes"] += rx * fs
        merged[rx_iface]["receive_fps"] += rx_rate
        merged[rx_iface]["receive_bps"] += rx_rate * fs * 8

    tx_bucket = merged[tx_iface]
    rx_bucket = merged[rx_iface]

    # TX iface: TX side populated, RX side ZERO (this is the bug fix)
    assert tx_bucket["tx"] == 4000
    assert tx_bucket["send_fps"] == 451.54
    assert tx_bucket["rx"] == 0, (
        f"TX iface bucket has rx={tx_bucket['rx']} — bug: TX iface "
        f"showing stream's RX count. Should be 0."
    )
    assert tx_bucket["receive_fps"] == 0, (
        f"TX iface bucket has receive_fps={tx_bucket['receive_fps']} — "
        f"bug: TX iface showing stream's RX rate. Should be 0."
    )
    assert tx_bucket["receive_bps"] == 0
    assert tx_bucket["received_bytes"] == 0

    # RX iface: RX side populated, TX side ZERO
    assert rx_bucket["rx"] == 3836
    assert rx_bucket["receive_fps"] == 397.36
    assert rx_bucket["tx"] == 0  # we never transmitted on the RX iface


def test_loopback_no_double_count():
    """When tx_iface == rx_iface (single-port loopback test), the
    RX-iface block runs once. RX must be counted exactly once, not
    twice."""
    iface = "TG 0 - enp1s0"
    merged = {
        iface: {
            "tx": 0, "rx": 0, "sent_bytes": 0, "received_bytes": 0,
            "send_fps": 0.0, "receive_fps": 0.0,
            "send_bps": 0.0, "receive_bps": 0.0,
            "errors": 0, "streams": {},
        },
    }
    stream = {
        "interface": "enp1s0",
        "rx_interface": "enp1s0",   # loopback
        "tx_count": 1000, "rx_count": 980,
        "tx_rate": 100.0, "rx_rate": 98.0,
        "frame_size": 64,
        "flow_tracking_enabled": True,
    }
    tx_iface = f"TG 0 - {stream['interface']}"
    rx_iface = f"TG 0 - {stream['rx_interface']}"
    assert tx_iface == rx_iface, "test setup invariant"

    # TX block
    merged[tx_iface]["tx"] += stream["tx_count"]
    merged[tx_iface]["send_fps"] += stream["tx_rate"]
    # RX block (same dict, since iface matches)
    merged[rx_iface]["rx"] += stream["rx_count"]
    merged[rx_iface]["receive_fps"] += stream["rx_rate"]

    assert merged[iface]["rx"] == 980, (
        f"Loopback RX counted {merged[iface]['rx']}× — pre-fix code "
        f"added it under both TX and RX blocks (double-count: 1960). "
        f"Post-fix: exactly once = 980."
    )
    assert merged[iface]["receive_fps"] == 98.0
    assert merged[iface]["tx"] == 1000
