"""v0.5.380 — traffic-gen fix bundle (T1-T5).

  T1  DPDK tx_worker NULL mbuf: crash + mempool leak on jumbo
      frames (>~2100B). Compact + free.
  T2  max_packets overshoots by full batch. Clamp `to_send`
      pre-send.
  T3  RFC 2544 reproducibility — seed per-stream RNG when
      `random_seed`/`frame_random_seed` is set.
  T4  `_apply_frame_size` silently oversize. Truncate Raw tail +
      always WARN.
  T5  RX drain race — coordinate via `rx_drained_event` instead
      of two blind 2s sleeps.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_multithreaded_ast_parses():
    ast.parse(_read("multithreaded_traffic_gen.py"))


def test_generic_ast_parses():
    ast.parse(_read("utils/generic.py"))


# ─── T1: DPDK tx_worker NULL mbuf ───


def test_t1_marker_present():
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    assert "v0.5.380 (audit stream-gen T1)" in src
    # 2 sites: build loop + tx_burst
    assert src.count("v0.5.380 (audit stream-gen T1)") >= 2


def test_t1_null_mbuf_freed_immediately():
    """When rte_pktmbuf_append returns NULL, the mbuf must be
    freed BEFORE we continue — no more leaking to the mempool."""
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    # Locate the NULL path and confirm rte_pktmbuf_free appears
    # inside its block (search a bounded window).
    _idx = src.index("v0.5.380 (audit stream-gen T1): jumbo-frame")
    body = src[_idx:_idx + 3500]
    assert "rte_pktmbuf_free(m)" in body
    assert "local_dropped++" in body


def test_t1_no_stray_null_assign_in_build_loop():
    """No stray `pkts[i]=NULL` remains in EXECUTABLE code.
    Ignore comment-block occurrences by stripping /* … */ blocks
    before scanning."""
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    # Strip C multi-line comments (non-greedy) so a comment that
    # describes the OLD bug pattern doesn't trip us.
    _stripped = re.sub(r"/\*[\s\S]*?\*/", "", src)
    # Also strip single-line // comments
    _stripped = re.sub(r"//[^\n]*", "", _stripped)
    assert "pkts[i]=NULL" not in _stripped
    assert "pkts[i] = NULL" not in _stripped


def test_t1_compacted_built_counter():
    """The compaction pattern: track `built`, only pass it to
    tx_burst — never the raw `need` (which may reference freed
    slots)."""
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    _idx = src.index("v0.5.380 (audit stream-gen T1): jumbo-frame")
    body = src[_idx:_idx + 3500]
    assert "uint16_t built = 0" in body
    assert "pkts[built++] = m" in body


def test_t1_tx_burst_uses_built_not_need():
    """The transmit call must use `built` — passing `need` would
    dereference potentially-freed slots."""
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    m = re.search(
        r"rte_eth_tx_burst\(port_id,\s*queue_id,\s*pkts,\s*built\)",
        src,
    )
    assert m, "tx_burst must pass `built` (compacted count) not `need`"
    # The drop-free loop should also iterate up to `built`
    assert re.search(r"for\s*\(\s*uint16_t\s+j\s*=\s*nb\s*;\s*j\s*<\s*built\s*;", src)


# ─── T2: max_packets overshoot ───


def test_t2_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.380 (audit stream-gen T2)" in src
    # helper def + 4 call sites
    assert src.count("v0.5.380 (audit stream-gen T2)") >= 5


def test_t2_clamp_helper_defined():
    src = _read("multithreaded_traffic_gen.py")
    assert "def _clamp_to_max(pkt_list)" in src
    _idx = src.index("def _clamp_to_max(pkt_list)")
    body = src[_idx:_idx + 1200]
    # Uses max_packets closure + get_tx_count_by_id
    assert "max_packets" in body
    assert "get_tx_count_by_id" in body
    # Returns trimmed list (not None) so callers can still
    # length-check + increment tx by len(to_send)
    assert "pkt_list[:_remaining]" in body


def test_t2_all_four_engines_call_clamp():
    """RoCEv2 + UEC + ARP + generic Scapy branches must each call
    `_clamp_to_max` before their sendp."""
    src = _read("multithreaded_traffic_gen.py")
    _count = src.count("to_send = _clamp_to_max(to_send)")
    assert _count >= 4, (
        f"Expected ≥4 _clamp_to_max call sites "
        f"(RoCEv2 + UEC + ARP + Generic); got {_count}"
    )


def test_t2_clamp_helper_empty_when_at_max():
    """When tx_count already at max_packets, clamp returns []."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def _clamp_to_max(pkt_list)")
    body = src[_idx:_idx + 1200]
    assert "if _remaining <= 0" in body
    assert "return []" in body


# ─── T3: reproducibility ───


def test_t3_marker_present():
    src = _read("utils/generic.py")
    assert "v0.5.380 (audit stream-gen T3)" in src


def test_t3_helper_defined():
    src = _read("utils/generic.py")
    assert "def _get_frame_rng(stream_data" in src
    _idx = src.index("def _get_frame_rng(stream_data")
    body = src[_idx:_idx + 1500]
    # honors both key names
    assert "frame_random_seed" in body
    assert "random_seed" in body
    # cached by (stream_id, seed) so restarts of same stream
    # replay the same distribution
    assert "_FRAME_RNG_CACHE" in body
    # falls back to module random when no seed
    assert "return random" in body


def test_t3_apply_frame_size_uses_seeded_rng():
    """Both random.randint AND random.random call sites now use
    the per-stream RNG, not module-level random."""
    src = _read("utils/generic.py")
    # Find the Random branch inside _apply_frame_size
    _idx = src.index("def _apply_frame_size")
    body = src[_idx:_idx + 5000]
    # Uses _rng.randint / _rng.random (not random.randint /
    # random.random). Both must switch.
    assert "_rng.randint(frame_min, frame_max)" in body
    assert "_rng.random()" in body
    # Backward-compat guard: `_rng = _get_frame_rng(stream_data)`
    # must be resolved before the frame_type branches.
    assert "_rng = _get_frame_rng(stream_data)" in body


def test_t3_seed_produces_deterministic_distribution():
    """Behavioral: same seed → same sequence of frame sizes."""
    from utils.generic import _get_frame_rng
    rng_a = _get_frame_rng({"random_seed": 42, "stream_id": "test_seed_a"})
    rng_b = _get_frame_rng({"random_seed": 42, "stream_id": "test_seed_b"})
    # Different stream_ids → different cache slots → different
    # streams, but both seeded with 42 so first draw is identical.
    _a = [rng_a.randint(64, 1518) for _ in range(5)]
    _b = [rng_b.randint(64, 1518) for _ in range(5)]
    assert _a == _b, (
        f"Seed=42 must produce deterministic sequence; got a={_a}, b={_b}"
    )


def test_t3_no_seed_returns_module_random():
    """Backward-compat: when no seed key, returns module random."""
    import random as _rand
    from utils.generic import _get_frame_rng
    assert _get_frame_rng({}) is _rand
    assert _get_frame_rng({"random_seed": 0}) is _rand
    assert _get_frame_rng({"random_seed": None}) is _rand


# ─── T4: oversize base packet warning + truncation ───


def test_t4_marker_present():
    src = _read("utils/generic.py")
    assert "v0.5.380 (audit stream-gen T4)" in src


def test_t4_oversize_branch_warns():
    """The `elif current_size > target_frame_size` branch must
    exist AND log a warning."""
    src = _read("utils/generic.py")
    _idx = src.index("def _apply_frame_size")
    body = src[_idx:_idx + 6000]
    assert "elif current_size > target_frame_size" in body
    # Two log.warning sites — one for successful truncation,
    # one for the give-up-and-warn case.
    _warn_count = body.count("logging.warning(")
    assert _warn_count >= 2, (
        f"Expected ≥2 warning sites for oversize branch; got {_warn_count}"
    )


def test_t4_truncation_attempts_raw_tail():
    """When Raw is present + large enough, we trim its trailing
    bytes rather than replacing/dropping other layers."""
    src = _read("utils/generic.py")
    _idx = src.index("def _apply_frame_size")
    body = src[_idx:_idx + 6000]
    assert "if Raw in pkt:" in body
    # Slice to keep the pre-excess portion of Raw payload
    assert "_load[:len(_load) - _excess]" in body


def test_t4_warns_when_no_raw_to_trim():
    """When no Raw payload big enough to absorb the excess,
    we still warn — silent skew is the pre-fix bug."""
    src = _read("utils/generic.py")
    _idx = src.index("v0.5.380 (audit stream-gen T4)")
    body = src[_idx:_idx + 3000]
    assert "calculate_interval bit-rate" in body


# ─── T5: RX drain race ───


def test_t5_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.380 (audit stream-gen T5)" in src


def test_t5_tracker_row_has_event():
    """New `rx_drained_event` field on every stream tracker row."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def add_stream(self, stream)")
    body = src[_idx:_idx + 3000]
    assert '"rx_drained_event": threading.Event()' in body


def test_t5_signal_helper_defined():
    src = _read("multithreaded_traffic_gen.py")
    assert "def signal_rx_drained(self, stream_id)" in src
    _idx = src.index("def signal_rx_drained(self, stream_id)")
    body = src[_idx:_idx + 1200]
    # Iterates active_streams, sets event on matching row
    assert "s.get(\"stream_id\") == stream_id" in body
    assert "_ev.set()" in body


def test_t5_stopper_signals_after_stop():
    """The RX sniffer's stopper() must call `signal_rx_drained`
    AFTER sniffer.stop(), not before."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def stopper():")
    body = src[_idx:_idx + 4000]
    _stop_pos = body.find("sniffer.stop()")
    _signal_pos = body.find("tracker.signal_rx_drained(stream_id)")
    assert _stop_pos > 0
    assert _signal_pos > 0
    assert _signal_pos > _stop_pos, (
        "signal_rx_drained must be called AFTER sniffer.stop()"
    )


def test_t5_on_stream_stopped_waits_on_event():
    """on_stream_stopped now waits on rx_drained_event instead of
    the blind 2s sleep. Fallback preserves legacy behavior."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def on_stream_stopped(interface, stream_id")
    body = src[_idx:_idx + 5000]
    assert "_drained_evt.wait(timeout=5.0)" in body
    # Fallback branch preserves the pre-v0.5.380 sleep for rows
    # missing the event (upgrade compat)
    assert "time.sleep(2)" in body


def test_t5_timeout_bounds_the_wait():
    """5s cap so a wedged libpcap thread doesn't leak the
    tracker row indefinitely."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("v0.5.380 (audit stream-gen T5): coordinate RX drain")
    body = src[_idx:_idx + 3000]
    assert "timeout=5.0" in body
    # Timeout message so operators see when the wait bailed
    assert "RX drain timeout" in body


# ─── version guard ───


def test_pyproject_version_at_least_0580():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 380), (
        f"Version {m.group(1)} < 0.5.380"
    )


# ─── regression guards ───


def test_v0265_stop_event_set_preserved():
    """v0.5.265 F1: on_stream_stopped must still call stop_event.set()."""
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.265 (audit stream-gen F1)" in src


def test_v0353_ibperf_tracker_preserved():
    src = _read("multithreaded_traffic_gen.py")
    assert "register_perftest_with_tracker" in src
