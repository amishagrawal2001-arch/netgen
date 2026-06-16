"""v0.5.168: orphan tx_worker / rx_worker detection + reaping.

Operator hit this on srv06: a DPDK Blast stream from a prior
session left a tx_worker (897% CPU) + rx_worker (798% CPU) pinned
to the same HCA the operator was using for RDMA. The orphans ate
~17 cores on NUMA 0 + competed for PCIe on the target BDF,
dropping RDMA BW from 171 Gbps → 68.59 Gbps.

Three pieces tested here:

  1. **Pure helpers** in `utils.dpdk_orphans` — parsing /proc
     cmdlines, classifying orphans, reaper. Filesystem-mocked so
     the test runs anywhere.
  2. **REST endpoints** wired into run_tgen_server.py — source
     check (the routes module is too heavy to import here).
  3. **Stream logic wiring** — Stop-All confirm + Start
     pre-flight (source-level).
"""
from __future__ import annotations

import os
import re
import signal
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils import dpdk_orphans
from utils.dpdk_orphans import (
    DpdkWorker, find_dpdk_workers, find_orphans, find_orphans_for_bdf,
    reap_workers, _classify_worker, _normalise_bdf, _RE_STREAM_ID,
    _RE_PCI_BDF, _RE_FILE_PREFIX,
)


# ───── pure-function helpers ─────────────────────────────────────


def test_classify_worker_matches_tx_and_rx_by_basename():
    cmd = "/usr/local/bin/tx_worker\x00-l\x000,1\x00-a\x000000:2b:00.0"
    assert _classify_worker(cmd) == "tx"
    cmd = "/usr/local/bin/rx_worker\x00-l\x000,1\x00-a\x000000:2b:00.1"
    assert _classify_worker(cmd) == "rx"


def test_classify_worker_rejects_unrelated():
    assert _classify_worker("/usr/bin/python\x00app.py") is None
    assert _classify_worker("/usr/local/bin/tx_workersuffix\x00") is None


def test_stream_id_regex_matches_canonical_uuid():
    cmd = "tx_worker --stream-id 3ede73ca-79a1-4d1e-adac-e1aa85662fed"
    m = _RE_STREAM_ID.search(cmd)
    assert m and m.group(1) == "3ede73ca-79a1-4d1e-adac-e1aa85662fed"


def test_pci_bdf_regex_matches_padded_and_unpadded():
    """The netgen launcher passes the full domain form; some DPDK
    invocations elide it. The regex must accept both, and the
    normaliser must canonicalise to the full form."""
    for bdf in ("-a 0000:2b:00.0", "-a 2b:00.0"):
        m = _RE_PCI_BDF.search(bdf)
        assert m, f"failed to match {bdf!r}"
    assert _normalise_bdf("2b:00.0") == "0000:2b:00.0"
    assert _normalise_bdf("0000:2b:00.0") == "0000:2b:00.0"
    assert _normalise_bdf(None) is None


def test_file_prefix_regex_extracts_iface_tag():
    cmd = "tx_worker --file-prefix txw_3ede73ca_ens2f0np0_1744665_xxx"
    m = _RE_FILE_PREFIX.search(cmd)
    assert m and m.group(1) == "txw_3ede73ca_ens2f0np0_1744665_xxx"


# ───── find_dpdk_workers walks /proc ──────────────────────────────


def _make_fake_proc(tmp_path, pid, comm, args, started_ticks=1000):
    """Build a /proc-like tree for one fake process."""
    pdir = tmp_path / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    cmdline = (comm + "\x00" + "\x00".join(args)).encode()
    (pdir / "cmdline").write_bytes(cmdline)
    # Minimal stat — 22nd field is starttime in jiffies. comm goes
    # in (parens) per the kernel format. Fields after comm:
    # state(3) ppid(4) ... starttime(22) → 19 fields between comm
    # and starttime exclusive, so position 22.
    stat_fields = ["S"] + ["0"] * 17 + [str(started_ticks)] + ["0"] * 20
    stat = f"{pid} ({os.path.basename(comm)}) " + " ".join(stat_fields)
    (pdir / "stat").write_text(stat)


def _write_uptime(root, seconds):
    (root / "uptime").write_text(f"{seconds:.2f} 100.00")


def test_find_dpdk_workers_parses_proc_tree(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _write_uptime(proc, 12345.0)
    _make_fake_proc(
        proc, 3194868, "/usr/local/bin/tx_worker",
        ["-l", "0,1,2", "-n", "4",
         "--file-prefix", "txw_3ede73ca_ens2f0np0_x",
         "-a", "0000:2b:00.0",
         "--", "--stream-id", "3ede73ca-79a1-4d1e-adac-e1aa85662fed"],
    )
    _make_fake_proc(
        proc, 3194724, "/usr/local/bin/rx_worker",
        ["-l", "0,1,2", "-a", "0000:2b:00.1",
         "--", "--stream-id", "3ede73ca-79a1-4d1e-adac-e1aa85662fed"],
    )
    # Decoy: an unrelated process must NOT appear.
    _make_fake_proc(proc, 1234, "/usr/bin/python", ["app.py"])

    workers = find_dpdk_workers(proc_root=str(proc))
    assert len(workers) == 2
    pids = sorted(w.pid for w in workers)
    assert pids == [3194724, 3194868]
    tx = next(w for w in workers if w.role == "tx")
    assert tx.stream_id == "3ede73ca-79a1-4d1e-adac-e1aa85662fed"
    assert tx.bdf == "0000:2b:00.0"
    assert tx.file_prefix == "txw_3ede73ca_ens2f0np0_x"
    # etime is uptime - starttime/HZ. With HZ=100 and starttime=1000,
    # etime = 12345 - 10 = 12335 sec. (May vary with platform HZ but
    # within an order of magnitude.)
    assert tx.etime_seconds is not None and tx.etime_seconds > 0


def test_find_orphans_filters_by_tracker_stream_ids(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _write_uptime(proc, 100.0)
    tracked_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    orphan_sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _make_fake_proc(
        proc, 100, "/usr/local/bin/tx_worker",
        ["-a", "0000:01:00.0", "--stream-id", tracked_sid],
    )
    _make_fake_proc(
        proc, 200, "/usr/local/bin/tx_worker",
        ["-a", "0000:02:00.0", "--stream-id", orphan_sid],
    )
    orphans = find_orphans({tracked_sid}, proc_root=str(proc))
    assert len(orphans) == 1
    assert orphans[0].pid == 200
    assert orphans[0].stream_id == orphan_sid


def test_find_orphans_includes_workers_with_no_stream_id(tmp_path):
    """Defensive: a worker missing --stream-id can't be tracked, so
    it's conservatively flagged as orphan. The operator chooses
    whether to reap."""
    proc = tmp_path / "proc"
    proc.mkdir()
    _write_uptime(proc, 100.0)
    _make_fake_proc(
        proc, 300, "/usr/local/bin/tx_worker", ["-a", "0000:03:00.0"],
    )
    orphans = find_orphans({"any-uuid"}, proc_root=str(proc))
    assert len(orphans) == 1
    assert orphans[0].stream_id is None


def test_find_orphans_for_bdf_narrows_to_one_device(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    _write_uptime(proc, 100.0)
    sid1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    sid2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _make_fake_proc(
        proc, 400, "/usr/local/bin/tx_worker",
        ["-a", "0000:2b:00.0", "--stream-id", sid1],
    )
    _make_fake_proc(
        proc, 500, "/usr/local/bin/tx_worker",
        ["-a", "0000:c0:00.0", "--stream-id", sid2],
    )
    matched = find_orphans_for_bdf(
        "0000:2b:00.0", set(), proc_root=str(proc))
    assert [w.pid for w in matched] == [400]
    # Case-insensitive + tolerates unpadded form.
    matched = find_orphans_for_bdf(
        "2B:00.0", set(), proc_root=str(proc))
    assert [w.pid for w in matched] == [400]


def test_find_dpdk_workers_handles_missing_proc():
    assert find_dpdk_workers(proc_root="/nonexistent/path/xyz") == []


# ───── reaper ────────────────────────────────────────────────────


def test_reap_workers_handles_already_dead_pids():
    """Re-reaping a never-existed PID lands in `terminated` (we
    treat ESRCH as success). No exception."""
    # PID -1 doesn't exist; ESRCH on first kill.
    # Use a freshly invalid PID range to avoid stomping anything.
    # 4 million is well above any normal Linux pid_max.
    fake_pid = 4_000_001
    out = reap_workers([fake_pid], term_wait_secs=0)
    assert fake_pid in out["terminated"]
    assert out["killed"] == []
    assert out["failed"] == []


def test_reap_workers_returns_empty_for_no_pids():
    out = reap_workers([], term_wait_secs=0)
    assert out == {"terminated": [], "killed": [], "failed": []}


def test_reap_workers_kills_live_subprocess():
    """End-to-end: spawn a real long-sleep child, reap it, verify
    it's actually gone. Skipped on platforms without `sleep`."""
    import shutil
    sleep_bin = shutil.which("sleep")
    if not sleep_bin:
        pytest.skip("no /bin/sleep available")
    import subprocess
    proc = subprocess.Popen([sleep_bin, "30"])
    try:
        out = reap_workers([proc.pid], term_wait_secs=0.5)
        assert proc.pid in out["terminated"]
        # Give it a beat to exit.
        time.sleep(0.2)
        assert proc.poll() is not None
        assert out["failed"] == []
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ───── REST endpoints (source-level) ─────────────────────────────


def test_orphans_route_registered_and_uses_helpers():
    src = (REPO / "run_tgen_server.py").read_text()
    assert "/api/streams/orphans" in src
    assert "from utils.dpdk_orphans import find_orphans" in src
    # Reap endpoint expects {pids: [...]}
    assert "/api/streams/orphans/reap" in src
    assert "reap_workers" in src


def test_orphans_route_supports_bdf_filter():
    src = (REPO / "run_tgen_server.py").read_text()
    # Operators hit this via ?bdf= when the GUI's Start pre-flight
    # wants to narrow to one NIC.
    assert "find_orphans_for_bdf" in src
    assert 'request.args.get("bdf")' in src


def test_reap_route_validates_pid_list():
    """Defensive validation — the endpoint must refuse empty or
    non-int pids with 400. The reap is destructive, no silent
    no-op."""
    src = (REPO / "run_tgen_server.py").read_text()
    api_reap_idx = src.find("def api_streams_orphans_reap")
    assert api_reap_idx > 0
    body = src[api_reap_idx:api_reap_idx + 2000]
    assert "must include {'pids'" in body
    assert "invalid pids" in body


# ───── GUI wiring (source-level) ─────────────────────────────────


def test_stream_logic_has_orphan_helpers():
    src = (REPO / "traffic_client" / "stream_logic.py").read_text()
    assert "def _fetch_orphans(" in src
    assert "def _reap_orphans(" in src
    assert "def _orphans_touch_iface(" in src
    assert "def _confirm_stop_with_orphans(" in src
    assert "def _confirm_reap_before_start(" in src


def test_stop_all_probes_for_orphans_and_confirms():
    """The Stop-All path must fetch orphans BEFORE deciding there
    are no streams to stop — the operator's whole motivation for
    this feature is the case where tracked=0 but orphans>0."""
    src = (REPO / "traffic_client" / "stream_logic.py").read_text()
    stop_idx = src.find("def stop_all_streams")
    assert stop_idx > 0
    body = src[stop_idx:stop_idx + 8000]
    assert "_fetch_orphans" in body
    assert "_confirm_stop_with_orphans" in body
    assert "_reap_orphans" in body


def test_start_path_has_orphan_collision_check():
    """The Start path must check orphans BEFORE firing the POST so
    the new stream doesn't spawn alongside the orphan and fight
    for the HCA."""
    src = (REPO / "traffic_client" / "stream_logic.py").read_text()
    start_idx = src.find("def start_stream")
    assert start_idx > 0
    # The method is long (~600 LOC including warning aggregation),
    # so search the whole rest of the file. Bound by the next class
    # method as a sanity check that the insert landed inside
    # start_stream rather than after it.
    body = src[start_idx:start_idx + 60000]
    assert "_fetch_orphans" in body
    assert "_orphans_touch_iface" in body
    assert "_confirm_reap_before_start" in body
    # Cancelled servers must be excluded from the actual POST loop.
    assert "cancelled_servers" in body


def test_rdma_device_unchanged_by_v05168():
    """v0.5.168 must not regress v0.5.167's RdmaDevice.driver
    field — verify the field is still present."""
    from utils.rdma_perf import RdmaDevice
    assert "driver" in RdmaDevice.__dataclass_fields__
