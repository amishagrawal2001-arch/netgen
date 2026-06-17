"""v0.5.176: lat test capture + tolerant BW regex for read_bw.

Operator: "check the report, seems read_bw and send_lat is not
working correctly also check other Test types if they are
working fine."

Two bugs fixed:

  1. **send_lat / write_lat / read_lat reported as broken.** The
     Blast dialog only stashed _client_bw / _client_msgrate
     when the client side finished. It never captured
     _client_lat_avg_us / lat_min / lat_max / lat_p99. The
     run-log iter rows therefore had no `lat_avg_us` field, the
     report's `has_lat = any(... "lat_avg_us" ...)` dispatch
     fell through to BW rendering, and the operator saw an
     all-dashes BW table for every latency test.

  2. **read_bw reported as broken.** Some perftest builds emit
     the BW-peak column as `N/A` (or `-`) for `ib_read_bw`
     because peak isn't computed for one-sided ops. The strict
     `[\\d.]+` peak regex rejected the entire data row, leaving
     final_bw_avg_gbps as None. Report showed all dashes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ───── Lat capture in Blast dialog ──────────────────────────────


def test_dialog_initializes_lat_capture_fields():
    """The _client_lat_* instance vars must be reset in
    _proceed_with_start so a 2nd run in the same dialog session
    doesn't carry stale lat from a previous run."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    idx = src.find("def _proceed_with_start")
    body = src[idx:idx + 6000]
    assert "self._client_lat_avg_us = None" in body
    assert "self._client_lat_min_us = None" in body
    assert "self._client_lat_max_us = None" in body
    assert "self._client_lat_p99_us = None" in body


def test_dialog_captures_lat_from_client_job():
    """When the client side reports finished_at, the dialog must
    also stash final_lat_avg_us / min / max / p99 from the job
    dict so they can be threaded into the run-log entry."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    # Find the v0.5.176 capture block.
    idx = src.find("v0.5.176: also stash lat fields")
    assert idx > 0
    body = src[idx:idx + 1500]
    assert "_client_lat_avg_us = job.get(\"final_lat_avg_us\")" in body
    assert "_client_lat_min_us = job.get(\"final_lat_min_us\")" in body
    assert "_client_lat_max_us = job.get(\"final_lat_max_us\")" in body
    assert "_client_lat_p99_us = job.get(\"final_lat_p99_us\")" in body


def test_iter_results_carry_lat_fields():
    """Per-iter results must include lat fields so iterate-N lat
    runs surface every iter's lat in the run-log."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    iter_append_idx = src.find("self._iteration_results.append(")
    body = src[iter_append_idx:iter_append_idx + 1500]
    assert '"lat_avg_us":' in body
    assert '"lat_min_us":' in body
    assert '"lat_max_us":' in body
    assert '"lat_p99_us":' in body


def test_run_log_rows_forward_lat_fields():
    """The for-loop over iter_results that builds the rows array
    must forward lat fields when present so the report renderer
    sees them."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    assert 'row["lat_avg_us"] = r.get("lat_avg_us")' in src
    assert 'row["lat_p99_us"] = r.get("lat_p99_us")' in src


def test_summary_aggregates_lat_when_present():
    """For lat runs the summary must carry lat_avg_us /
    lat_min_us / lat_max_us so the report's headline can show
    the across-iters average µs."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    # The aggregator branch:
    assert 'lats = [r["lat_avg_us"] for r in rows' in src
    assert '"lat_avg_us": sum(lats) / len(lats)' in src


# ───── Lat-aware results card ───────────────────────────────────


def test_results_card_dispatches_on_lat_vs_bw():
    """The post-run summary card must detect lat-run vs bw-run
    and render µs / Gbps accordingly."""
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    assert "def _render_lat_results_card(" in src
    assert "is_lat_run" in src


def test_lat_card_renders_avg_and_p99_with_us_units():
    src = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
    idx = src.find("def _render_lat_results_card")
    body = src[idx:idx + 3000]
    # Marquee: avg µs.
    assert "µs avg" in body
    # Shotgun: p99 µs.
    assert "µs p99" in body


# ───── read_bw tolerant regex ───────────────────────────────────


def test_bw_regex_accepts_numeric_peak():
    """Sanity: existing numeric peak rows still match."""
    from utils.rdma_perf import _RE_BW_DATA_ROW
    line = " 65536   2093706   0.0   68.59   0.13083 "
    m = _RE_BW_DATA_ROW.match(line)
    assert m is not None
    assert m.group("peak") == "0.0"
    assert m.group("avg") == "68.59"
    assert m.group("mrate") == "0.13083"


def test_bw_regex_accepts_na_peak_for_read_bw():
    """v0.5.176: peak can be 'N/A' on some perftest builds.
    Used to fail the entire match — now it parses cleanly with
    None for the peak field."""
    from utils.rdma_perf import _RE_BW_DATA_ROW
    line = " 65536   1234567   N/A   172.22   0.3285 "
    m = _RE_BW_DATA_ROW.match(line)
    assert m is not None
    assert m.group("peak") == "N/A"
    assert m.group("avg") == "172.22"
    assert m.group("mrate") == "0.3285"


def test_bw_regex_accepts_dash_peak():
    """Some builds emit a bare `-`. Same fix applies."""
    from utils.rdma_perf import _RE_BW_DATA_ROW
    line = " 65536   1234567   -   200.00   0.4000 "
    m = _RE_BW_DATA_ROW.match(line)
    assert m is not None
    assert m.group("peak") == "-"


def test_bw_parser_tolerates_non_numeric_peak_in_stdout_reader():
    """When the regex matches but peak is 'N/A', the stdout
    reader must NOT crash on float('N/A'). It assigns None for
    peak and continues populating avg + msgrate."""
    src = (REPO / "utils" / "rdma_perf.py").read_text()
    # The new defensive parse block.
    assert "v0.5.176: peak column may be 'N/A'" in src
    # And the try/except.
    assert "job.final_bw_peak_gbps = float(peak_raw)" in src


# ───── Cross-test-type smoke: cmd builder still produces valid args ─


def test_perftest_cmd_for_each_supported_test_type():
    """Sanity: every test_id in _SUPPORTED_TESTS produces a cmd
    starting with the correct tool binary and a -p port arg.
    Catches any future regression where a test type gets
    accidentally dropped from the cmd builder."""
    from utils.rdma_perf import _build_perftest_cmd, _SUPPORTED_TESTS
    base_opts = {
        "device": "mlx5_0", "ib_port": 1, "gid_index": 3,
        "msg_size": 65536, "duration": 30, "mtu": 5,
        "peer_addr": "10.0.0.1",
    }
    for test_id, expected_tool in _SUPPORTED_TESTS.items():
        if test_id.startswith("atomic"):
            continue
        cmd = _build_perftest_cmd(
            f"/usr/bin/{expected_tool}", "client", test_id,
            18515, base_opts,
        )
        assert cmd[0].endswith(expected_tool), (
            f"{test_id}: cmd[0]={cmd[0]!r} doesn't end with "
            f"{expected_tool!r}")
        assert "-p" in cmd
        assert "10.0.0.1" in cmd  # peer_addr is the client tail
        # _bw tests get --report_gbits; _lat tests must NOT.
        if test_id.endswith("_bw"):
            assert "--report_gbits" in cmd, (
                f"{test_id}: missing --report_gbits")
        else:
            assert "--report_gbits" not in cmd, (
                f"{test_id}: should not have --report_gbits")
