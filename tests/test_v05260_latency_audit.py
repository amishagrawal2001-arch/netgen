"""v0.5.260 — one-way latency sampler audit: 7 correctness fixes."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
LAT = (REPO / "utils" / "latency_sampler.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- latency-2: deque race fixed via .copy() ----------------------


def test_snapshot_uses_deque_copy_not_bare_iteration():
    assert "audit latency-2" in LAT
    # Bare `sorted(self._latencies)` is gone as a LIVE call (comment
    # prose may still mention it as pre-fix documentation).
    live_bare = [
        line for line in LAT.splitlines()
        if "sorted(self._latencies)" in line
        and not line.lstrip().startswith("#")
    ]
    assert live_bare == [], f"bare sorted still live: {live_bare!r}"
    # .copy() before sort is present.
    assert "self._latencies.copy()" in LAT
    assert "sorted(snap)" in LAT


# --- latency-3: docstring updated ---------------------------------


def test_docstring_marks_cross_host_unsupported():
    assert "audit latency-3" in LAT
    assert "NOT SUPPORTED" in LAT
    # CLOCK_TAI is the way out — reference it as future direction.
    assert "CLOCK_TAI" in LAT


# --- latency-4: impossible-latency bucket separate ----------------


def test_samples_impossible_latency_field_defined():
    assert "audit latency-4" in LAT
    assert "samples_impossible_latency" in LAT
    # Snapshot dict includes it (so operators can distinguish
    # skew from wrong-magic in the API response).
    assert '"samples_impossible_latency": self.samples_impossible_latency' in LAT


def test_impossible_latency_incremented_not_samples_skipped():
    """The clock-skew clamp branch bumps samples_impossible_latency,
    not samples_skipped."""
    idx = LAT.find("if latency_ns < 0 or latency_ns > 60 * 10**9:")
    body = LAT[idx:idx + 800]
    assert "self.stats_obj.samples_impossible_latency += 1" in body
    # And doesn't ALSO bump samples_skipped in that branch.
    assert "self.stats_obj.samples_skipped += 1" not in body


def test_impossible_latency_logs_periodically():
    """Rate-limited WARNING gives operators visible signal for
    the failure mode."""
    # The log-emit lives in _on_packet, not the LatencyStats
    # dataclass — anchor on the log message directly.
    assert "impossible latency" in LAT
    assert "% 1000" in LAT


# --- latency-5: sampler status exposed ----------------------------


def test_sampler_has_status_field():
    assert "audit latency-5" in LAT
    assert "self.status: str = \"starting\"" in LAT


def test_stats_response_includes_status():
    idx = LAT.find("def stats(self)")
    end = LAT.find("\n    def ", idx + 1)
    body = LAT[idx:end if end > 0 else idx + 500]
    assert 'out["status"] = self.status' in body


def test_run_sets_status_on_error_paths():
    idx = LAT.find("def _run(self):")
    end = LAT.find("\n\n# ---", idx + 1)
    if end < 0:
        end = idx + 3000
    body = LAT[idx:end]
    assert 'self.status = "iface_not_found"' in body
    assert 'crashed:' in body
    assert 'self.status = "running"' in body


# --- latency-6: LatencyStats.reset() -----------------------------


def test_latency_stats_reset_defined():
    assert "audit latency-6" in LAT
    assert "def reset(self):" in LAT
    assert "self._latencies.clear()" in LAT


def test_latency_stats_reset_runtime():
    """Reset must actually clear the counters + deque."""
    import sys
    sys.path.insert(0, str(REPO))
    try:
        from utils.latency_sampler import LatencyStats
        s = LatencyStats()
        s.add(1_000_000)
        s.add(2_500_000)
        assert s.samples_decoded == 2
        s.reset()
        snap = s.snapshot()
        assert snap["samples_decoded"] == 0
        assert snap["window_samples"] == 0
        assert snap["min_us"] is None
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


# --- latency-7: cache key (iface, udp_port) -----------------------


def test_sampler_cache_key_is_tuple():
    idx = SERVER.find("def _get_or_start_latency_sampler(iface, udp_port=4791):")
    end = SERVER.find("\n\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 2500]
    assert "audit latency-7" in body
    assert "key = (iface, int(udp_port)" in body
    assert "_LATENCY_SAMPLERS.get(key)" in body
    assert "_LATENCY_SAMPLERS[key] = s" in body


def test_latency_stop_walks_all_ports_for_iface():
    idx = SERVER.find("def latency_stop():")
    end = SERVER.find("\n\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 2000]
    assert "audit latency-7" in body
    assert "_keys_to_stop" in body


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 260)
