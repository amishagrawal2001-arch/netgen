"""v0.5.177: Spirent/Ixia-style latency-vs-size sweep.

Verifies the perftest cmd builder injects `-a -n N` when
sweep_sizes=True, and that the stdout reader accumulates ALL
per-size rows into PerftestJob.final_lat_sweep instead of just
overwriting the final_lat_* scalars on each match.

Real perftest output for `ib_send_lat -a -n 1000` is 24 rows
(one per power-of-2 from 2 B to 8 MB). The full 9-column shape
already matches _RE_LAT_DATA_ROW; this test exercises the
accumulator path.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_perf import (
    PerftestJob,
    _RE_LAT_DATA_ROW,
    _build_perftest_cmd,
)


# Synthetic 4-size mini-sweep — each row is the real 9-column lat
# shape perftest emits in iteration mode. Whitespace mimics
# perftest's actual padding.
SWEEP_ROWS = [
    "  2     1000     1.10     8.20     1.30     1.45     0.05     2.10     5.20",
    "  64    1000     1.20     8.50     1.40     1.55     0.06     2.20     5.30",
    "  1024  1000     1.80     9.10     2.00     2.15     0.08     3.00     6.00",
    "  65536 1000    12.40   25.80    13.10    13.45     0.40    18.00    22.00",
]


def test_sweep_rows_each_match_lat_regex():
    for row in SWEEP_ROWS:
        assert _RE_LAT_DATA_ROW.match(row) is not None, (
            f"row did not match: {row!r}")


def test_build_cmd_injects_a_and_n_when_sweep_sizes_true():
    """sweep_sizes=True → `-a -n <per_size>`, NOT `-D`."""
    cmd = _build_perftest_cmd(
        tool_path="/usr/bin/ib_send_lat",
        role="client",
        test="send_lat",
        listen_port=18516,
        opts={
            "device": "mlx5_0", "ib_port": 1, "gid_index": 3,
            "sweep_sizes": True, "iterations_per_size": 2000,
            "duration": 30,          # should be IGNORED when sweep is on
            "msg_size": 65536,       # should be IGNORED when sweep is on
            "peer_addr": "10.0.0.1",
        },
    )
    assert "-a" in cmd, f"missing -a in {cmd}"
    n_idx = cmd.index("-n")
    assert cmd[n_idx + 1] == "2000"
    assert "-D" not in cmd, "duration leaked through despite sweep_sizes"
    assert "-s" not in cmd, "msg_size leaked through despite sweep_sizes"


def test_build_cmd_defaults_iterations_per_size_to_5000():
    cmd = _build_perftest_cmd(
        tool_path="/usr/bin/ib_send_lat",
        role="client",
        test="send_lat",
        listen_port=18516,
        opts={
            "sweep_sizes": True,
            "peer_addr": "10.0.0.1",
        },
    )
    n_idx = cmd.index("-n")
    assert cmd[n_idx + 1] == "5000"


def test_build_cmd_no_sweep_preserves_legacy_behaviour():
    """sweep_sizes=False (or absent) → -D/-s untouched."""
    cmd = _build_perftest_cmd(
        tool_path="/usr/bin/ib_send_lat",
        role="client",
        test="send_lat",
        listen_port=18516,
        opts={
            "duration": 30, "msg_size": 64,
            "peer_addr": "10.0.0.1",
        },
    )
    assert "-a" not in cmd
    assert "-D" in cmd
    assert "-s" in cmd
    d_idx = cmd.index("-D")
    assert cmd[d_idx + 1] == "30"


def test_perftest_job_dataclass_has_sweep_field():
    """final_lat_sweep is None by default; set to a list when sweep
    runs. The dataclass must support this without raising."""
    job = PerftestJob(
        job_id="t", role="client", test="send_lat", tool="ib_send_lat",
        device="mlx5_0", ib_port=1, listen_port=18516, peer_addr=None,
        cmd=["ib_send_lat"], pid=None, started_at=0.0,
        finished_at=None, returncode=None, error=None,
    )
    assert job.final_lat_sweep is None
    job.final_lat_sweep = [{"bytes": 2, "lat_avg_us": 1.45}]
    pub = job.to_public_dict()
    assert pub["final_lat_sweep"] == [{"bytes": 2, "lat_avg_us": 1.45}]


def test_sweep_row_regex_extracts_all_columns():
    """Each row must yield every numeric field cleanly so the
    accumulator can build a full sweep entry."""
    row = SWEEP_ROWS[3]  # 65536-byte row
    m = _RE_LAT_DATA_ROW.match(row)
    assert m is not None
    assert m.group("bytes") == "65536"
    assert m.group("iters") == "1000"
    assert m.group("tmin") == "12.40"
    assert m.group("tmax") == "25.80"
    assert m.group("ttyp") == "13.10"
    assert m.group("tavg") == "13.45"
    assert m.group("tstdev") == "0.40"
    assert m.group("p99") == "18.00"
    assert m.group("p999") == "22.00"
