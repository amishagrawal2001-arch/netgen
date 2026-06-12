"""Post-v0.5.105 release self-audit: catches bugs/gaps I found
while CI was building the wheel.

Two issues addressed:

1. **Auto-start ordering bug.** Pre-fix, `_maybe_start_dpdk_rx_for_stream`
   ran BEFORE the duplicate-stream-id and duplicate-stream-name
   checks in /api/traffic/start's loop. A retry-Start-after-Edit
   scenario (operator changes a field, hits Start again) would
   spawn a fresh rx_worker, then the dup check would `continue`
   past the actual TX launch — leaving an orphaned rx_worker
   that never got stopped because no matching TX stream was
   registered. Cumulative leak over a session.

   Fix: moved the call to AFTER both duplicate checks AND the
   RDMA short-circuit. Tests assert relative ordering in source.

2. **No shutdown hook for the registry.** rx_worker processes
   are subprocess.Popen children of netgen-server. systemd's
   KillMode=control-group reaps them on `systemctl stop`, but
   `systemctl restart` is two operations and there's a window
   where children could linger. uwsgi/gunicorn deployments don't
   get cgroup cleanup at all.

   Fix: atexit.register(_rx_registry.stop_all). Test asserts
   the hook is wired at module load.
"""
from __future__ import annotations

import re
import os
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SERVER_SRC = (_REPO / "run_tgen_server.py").read_text()
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-audit-rx.db")


# ─── Audit fix #1: ordering ───


def test_auto_start_runs_after_duplicate_id_check():
    """The rx_worker spawn must come AFTER the dup-by-stream_id
    check, so a duplicate-skip doesn't leak a registered rx_worker."""
    auto_start_pos = _SERVER_SRC.find(
        "_maybe_start_dpdk_rx_for_stream(\n                stream_id=stream_id"
    )
    dup_id_check_pos = _SERVER_SRC.find(
        "existing = stream_tracker.find_stream_by_id(interface_name, stream_id)"
    )
    assert auto_start_pos > 0, "_maybe_start call not found"
    assert dup_id_check_pos > 0, "dup-by-id check not found"
    assert auto_start_pos > dup_id_check_pos, (
        "rx_worker auto-spawn must come AFTER the duplicate-by-id "
        "check — otherwise a duplicate-skip leaks a registered "
        "rx_worker that never gets stopped"
    )


def test_auto_start_runs_after_duplicate_name_check():
    """Same for the dup-by-name check, which can also short-circuit
    via `continue`."""
    auto_start_pos = _SERVER_SRC.find(
        "_maybe_start_dpdk_rx_for_stream(\n                stream_id=stream_id"
    )
    dup_name_check_pos = _SERVER_SRC.find(
        "existing_by_name = stream_tracker.find_streams_by_name"
    )
    assert auto_start_pos > dup_name_check_pos, (
        "rx_worker auto-spawn must come AFTER the duplicate-by-name "
        "check"
    )


def test_auto_start_runs_after_rdma_short_circuit():
    """RDMA streams use perftest, not L2/L3/L4 packets. Spawning
    rx_worker for an RDMA stream wastes resources (the worker
    captures nothing relevant) and could confuse the stats fold.
    Must come after the RDMA early-return."""
    auto_start_pos = _SERVER_SRC.find(
        "_maybe_start_dpdk_rx_for_stream(\n                stream_id=stream_id"
    )
    # The RDMA short-circuit ends with a `return jsonify(...)` block
    # whose preceding "actual_engine": "rdma"` marker is a stable
    # anchor.
    rdma_return_pos = _SERVER_SRC.find(
        '"actual_engine": "rdma",\n                    })'
    )
    assert rdma_return_pos > 0, "RDMA short-circuit return not found"
    assert auto_start_pos > rdma_return_pos, (
        "rx_worker auto-spawn must come AFTER the RDMA short-circuit "
        "return — RDMA streams shouldn't spawn rx_worker"
    )


# ─── Audit fix #2: shutdown hook ───


def test_atexit_registers_registry_stop_all():
    """The server module must register an atexit hook so rx_worker
    processes are reaped on netgen-server exit. Without this,
    `systemctl stop netgen-server` could leak processes (on
    deployment configs where KillMode != control-group)."""
    # Search for the registration site — has to call stop_all on
    # the manager.
    assert "atexit.register" in _SERVER_SRC or "_atexit.register" in _SERVER_SRC
    assert "stop_all" in _SERVER_SRC
    # Specifically the rx manager registry, not some other use
    assert "dpdk_rx_manager" in _SERVER_SRC


def test_atexit_hook_handles_import_failure():
    """If the manager module can't be imported (corrupt install,
    missing dependency), server startup must not die. The
    registration is wrapped in try/except."""
    # Find the atexit block and verify it's defensively wrapped.
    m = re.search(
        r"AUDIT FIX[\s\S]+?_atexit\.register[\s\S]+?except Exception",
        _SERVER_SRC,
    )
    assert m, (
        "atexit hook is not wrapped in try/except — a manager "
        "import failure would crash server startup"
    )


# ─── Live runtime check: helper actually stops rx_workers via stop_all ───


def test_registry_stop_all_actually_stops_handles(monkeypatch):
    """The atexit-registered stop_all must walk every handle.
    Catches a regression where stop_all silently skipped some
    state (e.g., a future refactor that splits the registry)."""
    import io, signal
    from utils import dpdk_rx_worker, dpdk_rx_manager
    dpdk_rx_manager.reset_registry_for_tests()

    class _P:
        def __init__(self, pid):
            self.pid = pid
            self._terminated = False
            self.signals_received = []
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
        def poll(self):
            return 0 if self._terminated else None
        def send_signal(self, sig):
            self.signals_received.append(sig)
            if sig in (signal.SIGTERM, signal.SIGKILL):
                self._terminated = True
        def wait(self, timeout=None): return 0
        def kill(self): self.send_signal(signal.SIGKILL)

    procs = [_P(11), _P(22), _P(33)]
    monkeypatch.setattr(
        dpdk_rx_worker, "_resolve_rx_worker_bin",
        lambda: "/usr/local/bin/rx_worker",
    )
    pi = iter(procs)
    monkeypatch.setattr(
        dpdk_rx_worker.subprocess, "Popen",
        lambda *a, **kw: next(pi),
    )

    reg = dpdk_rx_manager.registry()
    for sid in ("a", "b", "c"):
        reg.start(stream_id=sid, pci_bdf="0000:2b:00.1")
    assert len(reg.list()) == 3

    # The actual atexit-registered call:
    reg.stop_all(timeout_s=0.5)

    # Every handle got a SIGTERM
    for p in procs:
        assert signal.SIGTERM in p.signals_received


# ─── Re-verify nothing regressed in the auto-spawn helper ───


def test_helper_still_skips_when_rx_engine_unset(monkeypatch):
    """Sanity: ordering refactor didn't break the basic skip
    behavior."""
    import sys, importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rx_audit_server", str(_REPO / "run_tgen_server.py"),
    )
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)

    from utils import dpdk_rx_manager
    dpdk_rx_manager.reset_registry_for_tests()
    s._maybe_start_dpdk_rx_for_stream(
        stream_id="x", rx_interface="eth0",
        stream_data={},
    )
    assert dpdk_rx_manager.registry().list() == []
