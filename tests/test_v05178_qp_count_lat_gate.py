"""v0.5.178: cmd builder must not pass `-q N` to `*_lat` tests.

perftest rejects -q on the latency binaries with:

    Multiple QPs only available on bw tests

…and exits rc=1 before emitting a single data row. Pre-fix, the
Blast dialog's qp_count spinbox value rode through to lat tests
too — operator hit this on srv06 after running a BW sweep with
qp_count=8 and then switching to send_lat + sweep, which failed
immediately with the error above.

The fix gates `-q` on test.endswith("_bw"). These tests pin the
gate so it can't silently regress.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.rdma_perf import _build_perftest_cmd


COMMON_OPTS = {
    "device": "mlx5_0", "ib_port": 1, "gid_index": 3,
    "peer_addr": "10.0.0.1",
}


def _cmd_for(test: str, qp_count: int) -> list:
    return _build_perftest_cmd(
        tool_path=f"/usr/bin/ib_{test}",
        role="client",
        test=test,
        listen_port=18516,
        opts={**COMMON_OPTS, "qp_count": qp_count, "duration": 30},
    )


# ───────── lat tests must NOT get -q ─────────

def test_send_lat_no_q_flag_even_with_qp_count_8():
    cmd = _cmd_for("send_lat", 8)
    assert "-q" not in cmd, (
        f"send_lat got -q (perftest rejects): {cmd}")


def test_write_lat_no_q_flag_even_with_qp_count_64():
    cmd = _cmd_for("write_lat", 64)
    assert "-q" not in cmd


def test_read_lat_no_q_flag_even_with_qp_count_2():
    cmd = _cmd_for("read_lat", 2)
    assert "-q" not in cmd


# ───────── bw tests still get -q when > 1 ─────────

def test_send_bw_gets_q_when_qp_count_8():
    cmd = _cmd_for("send_bw", 8)
    assert "-q" in cmd
    assert cmd[cmd.index("-q") + 1] == "8"


def test_write_bw_gets_q_when_qp_count_64():
    cmd = _cmd_for("write_bw", 64)
    assert "-q" in cmd
    assert cmd[cmd.index("-q") + 1] == "64"


def test_read_bw_gets_q_when_qp_count_2():
    cmd = _cmd_for("read_bw", 2)
    assert "-q" in cmd
    assert cmd[cmd.index("-q") + 1] == "2"


# ───────── qp_count = 1 never gets -q (regardless of suffix) ─────────

def test_send_bw_no_q_when_qp_count_1():
    """qp_count=1 is perftest's default; passing `-q 1`
    explicitly is redundant noise, the existing builder skips
    it. Confirm the fix preserves that."""
    cmd = _cmd_for("send_bw", 1)
    assert "-q" not in cmd


def test_send_lat_no_q_when_qp_count_1():
    cmd = _cmd_for("send_lat", 1)
    assert "-q" not in cmd


# ───────── interaction with sweep mode ─────────

def test_sweep_lat_with_high_qp_count_still_no_q():
    """v0.5.178 regression case from srv06: operator ticked
    Sweep on send_lat with qp_count=8 left over from a BW run.
    Pre-fix, perftest exited rc=1 immediately. Post-fix the cmd
    has `-a -n N` but no `-q`."""
    cmd = _build_perftest_cmd(
        tool_path="/usr/bin/ib_send_lat",
        role="client",
        test="send_lat",
        listen_port=18516,
        opts={
            **COMMON_OPTS,
            "qp_count": 8,
            "sweep_sizes": True,
            "iterations_per_size": 5000,
        },
    )
    assert "-a" in cmd
    assert "-q" not in cmd, (
        f"send_lat sweep got -q (perftest will exit rc=1): "
        f"{cmd}")
    n_idx = cmd.index("-n")
    assert cmd[n_idx + 1] == "5000"
