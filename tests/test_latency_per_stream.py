"""v0.3.5 — per-stream latency histogram.

Pre-v0.3.5 the LatencySampler maintained one histogram per RX
interface. Two concurrent streams on the same iface got their
latency samples merged into one mixed blob — the operator saw
one set of p50/p95/p99 numbers that wasn't meaningful for
either stream individually.

v0.3.5 adds per-stream histograms by extracting the
`[<stream_id>(/q<queue>)?#<seq>]` signature from the packet
body past the NLAT header (same signature the v0.3.4 RX
sniffer uses) and bucketing samples accordingly. Aggregate
behaviour is unchanged for backward compatibility.

These tests pin:
  * `LatencySampler.stats_by_stream()` exists and returns a
    dict keyed by extracted stream_id.
  * Aggregate `.stats()` still includes all samples (backward
    compat).
  * The extractor regex tolerates both Scapy and DPDK packet
    formats.
  * Packets without a signature don't populate per-stream
    buckets (flow_tracking=off case stays clean).
  * The extractor handles the cross-stream-bleed case: two
    streams' samples on the same sampler end up in two
    separate buckets, not mixed.
"""

import struct
import threading
from collections import deque

import pytest

from utils.latency_sampler import (
    LatencySampler,
    LatencyStats,
    NLAT_HDR_LEN,
    NLAT_MAGIC,
    NLAT_STRUCT,
    _SIG_EXTRACT_RE,
)


# ─────────────────────────────────────── extractor regex
@pytest.mark.parametrize("body, expected_sid", [
    # Scapy format
    (b"\x00" * NLAT_HDR_LEN + b"[stream-alpha#42]", b"stream-alpha"),
    (b"\x00" * NLAT_HDR_LEN + b"[abc#0]", b"abc"),
    # DPDK format (with /q segment)
    (b"\x00" * NLAT_HDR_LEN + b"[stream-alpha/q0#42]", b"stream-alpha"),
    (b"\x00" * NLAT_HDR_LEN + b"[stream-alpha/q15#9999]", b"stream-alpha"),
    # Padding around the signature
    (b"\x00" * NLAT_HDR_LEN + b"garbage[stream-beta#1]more", b"stream-beta"),
])
def test_v0_3_5_extractor_captures_stream_id(body, expected_sid):
    m = _SIG_EXTRACT_RE.search(body, NLAT_HDR_LEN)
    assert m is not None
    assert m.group(1) == expected_sid


def test_v0_3_5_extractor_doesnt_match_when_no_signature():
    """Pure NLAT-only packet (capture_latency=on, flow_tracking=off)
    must not match. Per-stream buckets stay empty in that mode."""
    body = b"\x00" * NLAT_HDR_LEN + b"plain UDP payload no brackets"
    assert _SIG_EXTRACT_RE.search(body, NLAT_HDR_LEN) is None


def test_v0_3_5_extractor_rejects_malformed_q_segment():
    """Malformed `[stream/q#seq]` (no digits after /q) must not
    match — same defensive logic the v0.3.4 sniffer uses."""
    body = b"\x00" * NLAT_HDR_LEN + b"[stream-x/q#0]"
    m = _SIG_EXTRACT_RE.search(body, NLAT_HDR_LEN)
    # Either no match OR the captured group must NOT be "stream-x"
    # alone with a `/q` present in the body — implementation choice
    # is the former.
    assert m is None or b"/q" not in body[m.start():m.end()]


# ─────────────────────────────────────── sampler integration
def _make_sampler():
    """Build a sampler without spinning up the actual sniff
    thread. We exercise `_on_packet` directly with crafted
    objects that have the right shape."""
    return LatencySampler(iface="lo", udp_port=4791, window_size=1000)


class _FakeUDPPkt:
    """Minimal scapy-pkt-like shim. The sampler only does
    `pkt.haslayer(UDP)` and `bytes(pkt[UDP].payload)` — both
    re-imports of scapy.layers.inet.UDP inside the method. We
    bypass scapy by patching haslayer + the [UDP] accessor."""
    def __init__(self, payload_bytes):
        self._payload = payload_bytes

    def haslayer(self, _cls):
        return True

    def __getitem__(self, _cls):
        class _U:
            pass
        u = _U()
        # Scapy's UDP.payload is a Raw or bytes-castable object.
        u.payload = self._payload
        return u


def _craft_nlat_payload(tx_ns_offset_from_now: int, suffix: bytes):
    """Build a UDP payload: NLAT header (16 bytes) + arbitrary suffix.
    `tx_ns_offset_from_now` is added to time.monotonic_ns() to control
    the latency the sampler computes — pass 0 for ~0 latency, negative
    for "older" packet."""
    import time as _t
    tx_ns = _t.monotonic_ns() + tx_ns_offset_from_now
    hdr = struct.pack(NLAT_STRUCT, NLAT_MAGIC, 0, tx_ns)
    return hdr + suffix


def test_v0_3_5_per_stream_buckets_populated_on_signed_packet():
    s = _make_sampler()
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-1_000_000, b"[stream-A#1]"),  # -1ms ago
    ))
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-2_000_000, b"[stream-A#2]"),
    ))
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-3_000_000, b"[stream-B/q0#1]"),  # DPDK
    ))
    by_stream = s.stats_by_stream()
    assert "stream-A" in by_stream
    assert "stream-B" in by_stream
    # Two samples in A, one in B.
    assert by_stream["stream-A"]["window_samples"] == 2
    assert by_stream["stream-B"]["window_samples"] == 1


def test_v0_3_5_aggregate_still_counts_all_samples():
    """Backward compat: pre-v0.3.5 callers using .stats() get the
    aggregate over all streams. Don't break them."""
    s = _make_sampler()
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-1_000_000, b"[stream-A#1]"),
    ))
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-2_000_000, b"[stream-B#1]"),
    ))
    agg = s.stats()
    # 2 NLAT-decoded samples total. Aggregate includes both.
    assert agg["window_samples"] == 2


def test_v0_3_5_unsigned_packet_skips_per_stream_but_hits_aggregate():
    """capture_latency=on, flow_tracking=off case: NLAT-decoded
    sample lands in the aggregate but doesn't populate any
    per-stream bucket."""
    s = _make_sampler()
    s._on_packet(_FakeUDPPkt(
        _craft_nlat_payload(-1_000_000, b"plain payload no brackets"),
    ))
    agg = s.stats()
    by_stream = s.stats_by_stream()
    assert agg["window_samples"] == 1
    assert by_stream == {}


def test_v0_3_5_per_stream_doesnt_mix_samples_across_streams():
    """The core bug: pre-v0.3.5 two streams sharing an iface got
    one mixed histogram. Now they must be separate."""
    s = _make_sampler()
    # 10 samples for A at -1ms, 10 for B at -10ms.
    for i in range(10):
        s._on_packet(_FakeUDPPkt(
            _craft_nlat_payload(-1_000_000, f"[stream-A#{i}]".encode()),
        ))
        s._on_packet(_FakeUDPPkt(
            _craft_nlat_payload(-10_000_000, f"[stream-B#{i}]".encode()),
        ))
    by_stream = s.stats_by_stream()
    # Each bucket has 10 samples.
    assert by_stream["stream-A"]["window_samples"] == 10
    assert by_stream["stream-B"]["window_samples"] == 10
    # And the p50 latencies are clearly different (1ms vs 10ms).
    a_p50 = by_stream["stream-A"]["p50_us"]
    b_p50 = by_stream["stream-B"]["p50_us"]
    # Tolerate scheduling noise but A must be < B by a wide margin.
    assert a_p50 < b_p50, (
        f"per-stream histograms must not bleed: stream-A p50 "
        f"{a_p50}us should be << stream-B p50 {b_p50}us"
    )


def test_v0_3_5_per_stream_lock_protects_concurrent_inserts():
    """Sanity: hammer the sampler from N threads with new stream
    IDs; the dict iteration in stats_by_stream() must complete
    without RuntimeError (dict mutated during iter) or
    KeyError."""
    s = _make_sampler()

    def worker(n):
        for i in range(50):
            s._on_packet(_FakeUDPPkt(
                _craft_nlat_payload(
                    -1_000_000,
                    f"[stream-{n}-{i}#{i}]".encode(),
                ),
            ))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    # Read snapshot concurrently with writes — must not raise.
    for _ in range(10):
        snap = s.stats_by_stream()
        assert isinstance(snap, dict)
    for t in threads:
        t.join(timeout=5)
    # Final snapshot should have many entries.
    final = s.stats_by_stream()
    assert len(final) >= 6  # at least one per worker


def test_v0_3_5_stats_method_unchanged_signature():
    """Pin that `.stats()` still returns the legacy dict shape so
    pre-v0.3.5 GUI / API callers don't break."""
    s = _make_sampler()
    out = s.stats()
    # Must have the legacy keys.
    for key in ("samples_seen", "samples_decoded", "samples_skipped",
                "window_samples", "min_us", "avg_us", "p50_us",
                "p95_us", "p99_us", "max_us"):
        assert key in out, f"legacy stats() shape missing {key!r}"
