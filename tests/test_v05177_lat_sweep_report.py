"""v0.5.177: HTML report renders RFC 2544-style lat-vs-size
sweep — table + inline SVG chart, no JS or external assets.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_report import (
    _fmt_bytes,
    _render_lat_sweep_section,
    _render_lat_sweep_table,
    _render_lat_sweep_chart,
    build_html_report,
)


# Realistic mini-sweep — 6 sizes spanning 3 orders of magnitude.
# Numbers chosen so min < typ < avg < max (perftest's actual
# behaviour) and so the chart's polygon math has real spread.
SWEEP = [
    dict(bytes=2,     iters=5000, lat_min_us=1.10,  lat_typ_us=1.30,
         lat_avg_us=1.45,  lat_stdev_us=0.05, lat_max_us=8.20,
         lat_p99_us=2.10,  lat_p999_us=5.20),
    dict(bytes=64,    iters=5000, lat_min_us=1.20,  lat_typ_us=1.40,
         lat_avg_us=1.55,  lat_stdev_us=0.06, lat_max_us=8.50,
         lat_p99_us=2.20,  lat_p999_us=5.30),
    dict(bytes=1024,  iters=5000, lat_min_us=1.80,  lat_typ_us=2.00,
         lat_avg_us=2.15,  lat_stdev_us=0.08, lat_max_us=9.10,
         lat_p99_us=3.00,  lat_p999_us=6.00),
    dict(bytes=8192,  iters=5000, lat_min_us=3.40,  lat_typ_us=3.55,
         lat_avg_us=3.70,  lat_stdev_us=0.09, lat_max_us=10.20,
         lat_p99_us=4.50,  lat_p999_us=7.20),
    dict(bytes=65536, iters=5000, lat_min_us=12.40, lat_typ_us=13.10,
         lat_avg_us=13.45, lat_stdev_us=0.40, lat_max_us=25.80,
         lat_p99_us=18.00, lat_p999_us=22.00),
    dict(bytes=524288, iters=5000, lat_min_us=85.0, lat_typ_us=87.5,
         lat_avg_us=89.1, lat_stdev_us=1.20, lat_max_us=120.0,
         lat_p99_us=105.0, lat_p999_us=115.0),
]


def test_fmt_bytes_handles_each_decade():
    assert _fmt_bytes(2) == "2 B"
    assert _fmt_bytes(1024) == "1 KiB"
    assert _fmt_bytes(65536) == "64 KiB"
    assert _fmt_bytes(1024 * 1024) == "1 MiB"


def test_sweep_section_empty_when_no_data():
    assert _render_lat_sweep_section([], {}) == ""


def test_sweep_table_renders_every_row_and_column():
    html = _render_lat_sweep_table(SWEEP)
    # Header columns
    for col in ("Size", "Iters", "Min", "Typ", "Avg", "Max",
                "StdDev", "p99", "p99.9"):
        assert f">{col}" in html, f"column missing: {col}"
    # One <tr> per row + 1 header
    assert html.count("<tr>") == len(SWEEP) + 1
    # Largest size present (smoke check it didn't get truncated)
    assert "512 KiB" in html
    # 8 MiB excluded by design (not in fixture); 64 KiB included
    assert "64 KiB" in html
    # Numeric values formatted to 2 dp
    assert ">89.10<" in html  # avg of 524288 row


def test_sweep_chart_has_svg_with_avg_line_and_envelope():
    html = _render_lat_sweep_chart(SWEEP)
    assert "<svg" in html
    # Avg polyline (solid green stroke)
    assert "stroke='#059669'" in html
    # Min-max envelope polygon (light green fill)
    assert "<polygon" in html
    assert "fill='#a7f3d0'" in html
    # p99 dashed line
    assert "stroke-dasharray" in html
    # No external script or img tag — self-contained
    assert "<script" not in html
    assert "<img" not in html


def test_sweep_chart_handles_avg_only_no_min_max():
    """When only avg is set (e.g. duration mode), chart still
    renders the avg line but skips the min/max envelope."""
    rows = [
        dict(bytes=2,     lat_avg_us=1.45),
        dict(bytes=64,    lat_avg_us=1.55),
        dict(bytes=1024,  lat_avg_us=2.15),
    ]
    html = _render_lat_sweep_chart(rows)
    assert "<svg" in html
    assert "stroke='#059669'" in html  # avg line still drawn
    # Envelope polygon absent — no min/max data
    assert "<polygon" not in html


def test_full_report_includes_sweep_section_for_sweep_run():
    """End-to-end: build_html_report() with one run that carries
    lat_sweep produces a report containing the sweep table + chart."""
    run = {
        "kind": "blast",
        "test": "send_lat",
        "started_at": "2026-06-16T18:00:00",
        "params": {
            "test": "ib_send_lat",
            "msg_size": 2, "qp_count": 1, "duration_s": 0,
            "mtu": "5", "iterations_per_size": 5000,
            "sweep_sizes": True,
        },
        "endpoints": {"server": "srv06 mlx5_0",
                      "client": "srv06 mlx5_3"},
        "rows": [],
        "summary": None,
        "lat_sweep": SWEEP,
    }
    html = build_html_report(
        title="RDMA Latency Characterization",
        runs=[run],
        generated_at="2026-06-16T18:30:00",
    )
    assert "Latency vs Message Size" in html
    assert "RFC 2544-style" in html
    assert "Size" in html and "p99.9" in html
    # Chart SVG present
    assert "<svg" in html
    # The single-size headline rows table is still rendered (empty
    # but with the section header); sweep is below it.
    assert html.index("Latency vs Message Size") > html.index("Results")


def test_full_report_omits_sweep_section_for_non_sweep_run():
    """Legacy runs (no lat_sweep) must NOT render an empty
    sweep header — that would clutter every BW report."""
    run = {
        "kind": "blast",
        "test": "send_bw",
        "started_at": "2026-06-16T18:00:00",
        "params": {"test": "ib_send_bw"},
        "endpoints": {"server": "x", "client": "y"},
        "rows": [{"label": "worker 0", "state": "done",
                  "bw_gbps": 171.0, "msgrate_mpps": 0.33}],
        "summary": None,
    }
    html = build_html_report(
        title="Single-size run",
        runs=[run],
        generated_at="2026-06-16T18:30:00",
    )
    assert "Latency vs Message Size" not in html
    assert "RFC 2544-style" not in html
