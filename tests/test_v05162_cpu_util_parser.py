"""v0.5.162: perftest data-row parser tolerates --cpu_util column.

Operator: enabled "CPU util" checkbox; every BW run reported
None across the board even though rc=0. perftest with --cpu_util
appends a 6th column to the BW row (and an extra trailing column
to the Lat row). The pre-v0.5.162 regex was anchored to the
5-column / no-cpu_util shape, so the row never matched and the
final_* fields stayed None.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_perf import _RE_BW_DATA_ROW, _RE_LAT_DATA_ROW


def test_bw_row_without_cpu_util_still_matches():
    """Baseline — 5 columns, no `--cpu_util`."""
    line = "  65536    5244904     171.55      171.21      0.327198"
    m = _RE_BW_DATA_ROW.match(line)
    assert m is not None
    assert m.group("bytes") == "65536"
    assert m.group("iters") == "5244904"
    assert m.group("peak") == "171.55"
    assert m.group("avg") == "171.21"
    assert m.group("mrate") == "0.327198"


def test_bw_row_with_cpu_util_now_matches():
    """v0.5.162 fix — 6 columns with `--cpu_util`. Pre-fix this
    regex returned None and final_bw_avg_gbps stayed None."""
    line = "  65536    5244904     171.55      171.21      0.327198     12.34"
    m = _RE_BW_DATA_ROW.match(line)
    assert m is not None
    assert m.group("avg") == "171.21"
    assert m.group("mrate") == "0.327198"
    assert m.group("cpu_util") == "12.34"


def test_lat_row_without_cpu_util_still_matches():
    line = "  2     1000     1.50     2.10     5.30     2.95     0.12     7.40     8.10"
    m = _RE_LAT_DATA_ROW.match(line)
    assert m is not None
    assert m.group("tavg") == "2.95"


def test_lat_row_with_cpu_util_now_matches():
    """Lat tests get an extra trailing column with `--cpu_util`
    too — same regex-anchor problem; same tolerance fix."""
    line = "  2     1000     1.50     2.10     5.30     2.95     0.12     7.40     8.10     34.56"
    m = _RE_LAT_DATA_ROW.match(line)
    assert m is not None
    assert m.group("tavg") == "2.95"
    assert m.group("cpu_util") == "34.56"
