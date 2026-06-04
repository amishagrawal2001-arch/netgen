"""Tests for utils/rdma_perf.py — v0.3.12.

Mocks all OS interactions (sysfs reads, shutil.which, subprocess.Popen)
so the suite runs on macOS / Linux without RDMA hardware or rdma-core.
"""
from __future__ import annotations

import os
import threading
import time
from unittest import mock
from unittest.mock import patch

import pytest

from utils import rdma_perf


# ─────────────────────────────────────────── argv builder

def test_build_perftest_cmd_server_minimal():
    cmd = rdma_perf._build_perftest_cmd(
        "/usr/bin/ib_send_bw", "server", "send_bw", 18515, {},
    )
    assert cmd[0] == "/usr/bin/ib_send_bw"
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "18515"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "1"
    # No peer addr on server side
    assert all(not c.startswith("10.") for c in cmd[-3:])


def test_build_perftest_cmd_client_full_opts():
    opts = {
        "device": "mlx5_1", "ib_port": 2, "gid_index": 3,
        "msg_size": 65536, "qp_count": 4, "duration": 60,
        "mtu": 5, "tx_depth": 256, "rx_depth": 256,
        "bidirectional": True, "use_event": True, "inline": 0,
        "cq_mod": 64, "cpu_util": True, "report_gbits": True,
        "peer_addr": "10.0.0.99",
    }
    cmd = rdma_perf._build_perftest_cmd(
        "/usr/bin/ib_write_bw", "client", "write_bw", 18999, opts,
    )
    # Verify presence of every flag we set
    assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "mlx5_1"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "2"
    assert "-x" in cmd and cmd[cmd.index("-x") + 1] == "3"
    assert "-s" in cmd and cmd[cmd.index("-s") + 1] == "65536"
    assert "-q" in cmd and cmd[cmd.index("-q") + 1] == "4"
    assert "-D" in cmd and cmd[cmd.index("-D") + 1] == "60"
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "5"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "256"
    assert "--rx_depth" in cmd
    assert "-b" in cmd  # bidirectional, valid for _bw
    assert "-e" in cmd
    assert "--cpu_util" in cmd
    assert "--report_gbits" in cmd
    # Client → peer addr is the last positional arg.
    assert cmd[-1] == "10.0.0.99"


def test_build_perftest_cmd_lat_rejects_bidirectional_flag():
    """Bidirectional is meaningless for *_lat — the builder must not
    pass -b even if the operator ticked the box."""
    cmd = rdma_perf._build_perftest_cmd(
        "/usr/bin/ib_send_lat", "client", "send_lat", 18000,
        {"peer_addr": "10.0.0.2", "bidirectional": True},
    )
    assert "-b" not in cmd


def test_build_perftest_cmd_duration_over_iterations():
    """When both are set, duration wins (perftest rejects both together)."""
    cmd = rdma_perf._build_perftest_cmd(
        "/usr/bin/ib_send_bw", "server", "send_bw", 18000,
        {"duration": 30, "iterations": 1000},
    )
    assert "-D" in cmd
    assert "-n" not in cmd


# ─────────────────────────────────────────── validation

def test_start_perftest_rejects_bad_role():
    r = rdma_perf.start_perftest("observer", "send_bw", {})
    assert r["status"] == "error"
    assert "role" in r["error"]


def test_start_perftest_rejects_bad_test():
    r = rdma_perf.start_perftest("server", "send_potato", {})
    assert r["status"] == "error"
    assert "test must be one of" in r["error"]


def test_start_perftest_client_requires_peer_addr():
    r = rdma_perf.start_perftest("client", "send_bw", {"device": "mlx5_0"})
    assert r["status"] == "error"
    assert "peer_addr" in r["error"]


def test_start_perftest_surfaces_missing_install():
    """When perftest is not installed, start returns an actionable error
    instead of crashing on shutil.which → None."""
    with patch("utils.rdma_perf.perftest_installed",
               return_value={"installed": False, "tools": {}, "version": None}):
        r = rdma_perf.start_perftest("client", "send_bw",
                                     {"peer_addr": "10.0.0.2"})
    assert r["status"] == "error"
    assert "perftest not installed" in r["error"]


# ─────────────────────────────────────────── perftest_installed probe

def test_perftest_installed_all_missing(monkeypatch):
    monkeypatch.setattr(rdma_perf.shutil, "which", lambda _: None)
    out = rdma_perf.perftest_installed()
    assert out["installed"] is False
    assert all(v is None for v in out["tools"].values())
    assert out["version"] is None


def test_perftest_installed_partial(monkeypatch):
    """Some tools present, some not — installed=True, version probed."""
    def fake_which(name):
        return f"/usr/bin/{name}" if name in ("ib_send_bw", "ib_write_bw") else None
    monkeypatch.setattr(rdma_perf.shutil, "which", fake_which)

    def fake_run(*a, **kw):
        class FakeRes:
            stdout = "perftest 6.2-1\n"
            stderr = ""
        return FakeRes()
    monkeypatch.setattr(rdma_perf.subprocess, "run", fake_run)

    out = rdma_perf.perftest_installed()
    assert out["installed"] is True
    assert out["tools"]["send_bw"] == "/usr/bin/ib_send_bw"
    assert out["tools"]["read_lat"] is None
    assert out["version"] == "6.2-1"


# ─────────────────────────────────────────── device discovery

def test_list_rdma_devices_empty_when_no_sysfs():
    """On macOS / containers without /sys/class/infiniband, returns []."""
    with patch("os.path.isdir", return_value=False):
        out = rdma_perf.list_rdma_devices()
    assert out == []


def test_list_rdma_devices_mocked_mellanox(tmp_path, monkeypatch):
    """Synthesize a one-port mlx5_0 under tmp_path and verify parsing."""
    fake_root = tmp_path / "infiniband"
    fake_root.mkdir()
    dev = fake_root / "mlx5_0"
    dev.mkdir()
    (dev / "board_id").write_text("MT_0000000838\n")
    (dev / "fw_ver").write_text("28.36.1010\n")
    (dev / "node_guid").write_text("0x9803:9b03:00d0:1234\n")
    port = dev / "ports" / "1"
    port.mkdir(parents=True)
    (port / "state").write_text("4: ACTIVE\n")
    (port / "phys_state").write_text("5: LinkUp\n")
    (port / "link_layer").write_text("Ethernet\n")
    (port / "rate").write_text("100 Gb/sec (4X EDR)\n")
    (port / "active_mtu").write_text("5: 4096\n")
    (port / "lid").write_text("0x0001\n")
    gids = port / "gids"
    gids.mkdir()
    (gids / "0").write_text("0000:0000:0000:0000:0000:0000:0000:0000\n")
    (gids / "1").write_text("fe80:0000:0000:0000:9803:9bff:fe03:1234\n")
    (gids / "3").write_text("0000:0000:0000:0000:0000:ffff:0a00:0001\n")

    monkeypatch.setattr(rdma_perf, "_IB_SYSFS_ROOT", str(fake_root))
    devs = rdma_perf.list_rdma_devices()
    assert len(devs) == 1
    d = devs[0]
    assert d.name == "mlx5_0"
    assert d.vendor.startswith("MT_")
    assert d.fw_version == "28.36.1010"
    assert len(d.ports) == 1
    p = d.ports[0]
    assert p.port == 1
    assert p.state == "ACTIVE"
    assert p.link_layer == "Ethernet"
    assert p.mtu == 4096
    assert p.lid == 1
    # Zero GID skipped, two valid GIDs picked up.
    assert len(p.gids) == 2


# ─────────────────────────────────────────── reader-thread parsing

class _FakeProc:
    """Minimal subprocess.Popen substitute that feeds lines + waits."""
    def __init__(self, lines, returncode=0):
        self._lines = list(lines)
        self.returncode = returncode
        self._pid = 0
        self.stdout = self  # iter over self yields lines

    def __iter__(self):
        return iter(self._lines)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_reader_thread_parses_bw_data_row():
    job = rdma_perf.PerftestJob(
        job_id="t1", role="client", test="send_bw", tool="/u/x",
        device="mlx5_0", ib_port=1, listen_port=18515, peer_addr="10.0.0.2",
        cmd=[], pid=1, started_at=time.time(), finished_at=None,
        returncode=None, error=None,
    )
    lines = [
        " local address: LID 0x0001 QPN 0x000014 PSN 0x9b8a5f",
        " remote address: LID 0x0002 QPN 0x000015 PSN 0x88af3c",
        " #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]",
        " 65536      1000           96.43              96.40                0.18",
        "---------------------------------------------------------------------------------",
    ]
    proc = _FakeProc(lines)
    rdma_perf._reader_thread(job, proc)
    assert job.local_qpn == "0x000014"
    assert job.local_psn == "0x9b8a5f"
    assert job.remote_qpn == "0x000015"
    assert job.final_msg_size_bytes == 65536
    assert job.final_iterations == 1000
    assert job.final_bw_peak_gbps == 96.43
    assert job.final_bw_avg_gbps == 96.40
    assert job.final_msg_rate_mpps == 0.18
    assert job.finished_at is not None
    assert job.returncode == 0


def test_reader_thread_parses_lat_data_row():
    job = rdma_perf.PerftestJob(
        job_id="t2", role="client", test="send_lat", tool="/u/x",
        device="mlx5_0", ib_port=1, listen_port=18516, peer_addr="10.0.0.2",
        cmd=[], pid=1, started_at=time.time(), finished_at=None,
        returncode=None, error=None,
    )
    lines = [
        " #bytes     #iterations    t_min[usec]   t_max[usec]   t_typical[usec]   t_avg[usec]   t_stdev[usec]   99% percentile[usec]   99.9% percentile[usec]",
        " 2          1000           1.50          15.20         1.80              2.10          0.45            3.20                  4.50",
    ]
    proc = _FakeProc(lines)
    rdma_perf._reader_thread(job, proc)
    assert job.final_msg_size_bytes == 2
    assert job.final_iterations == 1000
    assert job.final_lat_min_us == 1.50
    assert job.final_lat_max_us == 15.20
    assert job.final_lat_avg_us == 2.10
    assert job.final_lat_p99_us == 3.20


def test_reader_thread_records_error_on_nonzero_rc():
    job = rdma_perf.PerftestJob(
        job_id="t3", role="server", test="send_bw", tool="/u/x",
        device="mlx5_0", ib_port=1, listen_port=18517, peer_addr=None,
        cmd=[], pid=1, started_at=time.time(), finished_at=None,
        returncode=None, error=None,
    )
    lines = [
        "Failed to bind addr: Address already in use",
        "FATAL: bind failed",
    ]
    proc = _FakeProc(lines, returncode=1)
    rdma_perf._reader_thread(job, proc)
    assert job.returncode == 1
    assert job.error is not None
    assert "rc=1" in job.error


# ─────────────────────────────────────────── job registry lifecycle

def test_job_registry_round_trip(monkeypatch):
    # Stub perftest_installed so start_perftest doesn't reject for missing.
    monkeypatch.setattr(rdma_perf, "perftest_installed",
                        lambda: {"installed": True,
                                 "tools": {"send_bw": "/usr/bin/ib_send_bw"},
                                 "version": "6.2"})

    # Stub Popen so we don't actually fork.
    class FakePopen:
        def __init__(self, *a, **kw):
            self.pid = 12345
            self.stdout = iter([])  # no output
            self.returncode = 0
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(rdma_perf.subprocess, "Popen", FakePopen)

    r = rdma_perf.start_perftest("client", "send_bw",
                                 {"peer_addr": "10.0.0.2", "device": "mlx5_0"})
    assert r["status"] == "started"
    jid = r["job_id"]
    # Visible in list + get
    jobs = rdma_perf.list_perftest_jobs()
    assert any(j["job_id"] == jid for j in jobs)
    j = rdma_perf.get_perftest_job(jid)
    assert j is not None
    assert j["test"] == "send_bw"
    # Wait briefly for the reader thread to finish (it has nothing to read).
    time.sleep(0.2)
    # Stop returns noop (already finished from no output) — but also
    # should not crash.
    out = rdma_perf.stop_perftest(jid)
    assert out["status"] in ("stopped", "noop")
    # Unknown job → error
    out = rdma_perf.stop_perftest("not-a-job")
    assert out["status"] == "error"
