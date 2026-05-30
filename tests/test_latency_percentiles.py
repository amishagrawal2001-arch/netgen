"""Tests for the latency-sampler percentile additions (v0.2.58).

Locks p95 alongside the existing p50/p99 and the relative ordering
contract (min ≤ p50 ≤ p95 ≤ p99 ≤ max). Pure-Python, no Qt.
"""

import pytest

from utils.latency_sampler import LatencyStats


def test_empty_snapshot_has_p95_key_and_is_none():
    """Empty windows return all percentile keys as None — important so
    downstream code (GUI cell + CSV export) can `lat.get('p95_us')`
    without branching on key presence."""
    snap = LatencyStats().snapshot()
    for key in ("min_us", "avg_us", "p50_us", "p95_us", "p99_us", "max_us"):
        assert key in snap, f"missing key {key} in empty snapshot"
        assert snap[key] is None
    assert snap["window_samples"] == 0


def test_percentile_ordering_on_uniform_samples():
    """min ≤ p50 ≤ p95 ≤ p99 ≤ max must hold for any sample set."""
    stats = LatencyStats()
    # 1000 samples 1..1000 microseconds (in ns).
    for i in range(1, 1001):
        stats.add(i * 1000)
    s = stats.snapshot()
    assert s["window_samples"] == 1000
    assert s["min_us"] <= s["p50_us"] <= s["p95_us"] <= s["p99_us"] <= s["max_us"]
    # Nearest-rank percentile sanity — within 1us of the analytic value.
    assert abs(s["min_us"] - 1.0) < 1e-6
    assert abs(s["max_us"] - 1000.0) < 1e-6
    assert abs(s["p50_us"] - 501.0) < 1.0   # n//2 → index 500 → 501us
    assert abs(s["p95_us"] - 951.0) < 1.0   # int(1000*0.95)=950 → 951us
    assert abs(s["p99_us"] - 991.0) < 1.0   # int(1000*0.99)=990 → 991us


def test_single_sample_all_percentiles_equal():
    """A single sample puts every percentile (and min/max) at the same
    value — no IndexError on degenerate windows."""
    stats = LatencyStats()
    stats.add(12345)   # 12.345 us in ns
    s = stats.snapshot()
    assert s["window_samples"] == 1
    for key in ("min_us", "avg_us", "p50_us", "p95_us", "p99_us", "max_us"):
        assert abs(s[key] - 12.345) < 1e-6


def test_p95_distinct_from_p50_and_p99_on_skewed_data():
    """On skewed data, p95 should land strictly between p50 and p99 —
    proves we didn't accidentally alias them."""
    stats = LatencyStats()
    # 100 samples: mostly small (1us), with a few large outliers.
    for _ in range(95):
        stats.add(1_000)            # 1 us
    for v in (50_000, 100_000, 200_000, 500_000, 1_000_000):
        stats.add(v)               # 50us .. 1000us
    s = stats.snapshot()
    assert s["p50_us"] < s["p95_us"] < s["p99_us"]
