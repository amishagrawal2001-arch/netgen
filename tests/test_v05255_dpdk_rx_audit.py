"""v0.5.255 — DPDK rx_worker audit: 7 correctness fixes across
`utils/dpdk_rx_worker.py` and `utils/dpdk_rx_manager.py`.

Fixes:
- RX-1  Missing LD_LIBRARY_PATH → librte_*.so load errors on
        rebuilt-DPDK hosts. Fix: call
        `resolve_dpdk_ld_library_path` from the tx module.
- RX-2  Missing RTE_DISABLE_MEMPOOL_OPS default. Fix: setdefault.
- RX-3  `systemd-run --no-block` reparents rx_worker → proc.pid
        is the (already-exited) wrapper → Stop targets a dead
        PID → orphan. Fix: `systemctl stop <unit>.scope` first,
        then `killpg(pgid, SIGTERM)` fallback with SIGKILL
        escalation; track `unit` and `pgid` in `RxHandle`;
        add `start_new_session=True` at Popen.
- RX-4  `dst_port=0` / `src_port=0` reached the C worker, which
        then rejects every real frame. Fix: coerce <= 0 → None
        inside `start_rx_worker` so both entry points are safe.
- RX-5  `file_prefix = f"rxw_{stream_id[:8]}_..."` collides on
        UUID prefix + ms-timestamp. Fix: append
        `secrets.token_hex(3)`.
- RX-6  `is_running()` returned False while the real worker was
        still alive (cascade of RX-3). Fix: check systemd scope
        + heartbeat freshness before falling back to wrapper PID.
- RX-7  `RxRegistry.start` held the lock across `Popen +
        systemd-run` (200-800 ms). Fix: 3-phase reservation via
        `_SPAWNING` sentinel; lock released for Phase 2 Popen.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
RX = (REPO / "utils" / "dpdk_rx_worker.py").read_text()
MGR = (REPO / "utils" / "dpdk_rx_manager.py").read_text()


# --- RX-1: LD_LIBRARY_PATH auto-discovery --------------------------


def test_rx_launcher_imports_ld_library_helper():
    assert "from utils.dpdk_tx_worker import resolve_dpdk_ld_library_path" in RX
    assert "audit RX-1" in RX


def test_rx_launcher_passes_env_to_popen():
    idx = RX.find("proc = subprocess.Popen(")
    assert idx > 0
    body = RX[idx:idx + 500]
    assert "env=child_env" in body


def test_rx_launcher_calls_resolve_dpdk_ld_library_path():
    idx = RX.find("resolve_dpdk_ld_library_path(")
    assert idx > 0
    body = RX[idx:idx + 300]
    assert 'child_env.get("LD_LIBRARY_PATH", "")' in body


# --- RX-2: RTE_DISABLE_MEMPOOL_OPS default -------------------------


def test_rx_launcher_sets_mempool_ops_default():
    assert 'child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")' in RX


# --- RX-3: Stop reworked (scope stop → killpg → SIGKILL escalation)


def test_rx_handle_tracks_pgid_and_unit():
    # New fields on the dataclass.
    assert "pgid: Optional[int] = None" in RX
    assert "unit: Optional[str] = None" in RX


def test_popen_uses_start_new_session():
    idx = RX.find("proc = subprocess.Popen(")
    body = RX[max(0, idx - 800):idx + 500]
    assert "start_new_session=True" in body
    assert "audit RX-3" in body


def test_stop_rx_worker_prefers_systemctl_scope():
    idx = RX.find("def stop_rx_worker(")
    end = RX.find("\ndef ", idx + 1)
    body = RX[idx:end if end > 0 else idx + 5000]
    assert "audit RX-3" in body
    assert "stop_scope_for_stream" in body
    assert 'role="rx"' in body


def test_stop_rx_worker_falls_back_to_killpg():
    idx = RX.find("def stop_rx_worker(")
    end = RX.find("\ndef ", idx + 1)
    body = RX[idx:end if end > 0 else idx + 5000]
    assert "os.killpg(handle.pgid, signal.SIGTERM)" in body
    assert "os.killpg(handle.pgid, signal.SIGKILL)" in body
    # Live `proc.send_signal(signal.SIGTERM)` on its own is gone —
    # only inside the fallback branch (else after killpg).
    live_bare = [
        line for line in body.splitlines()
        if line.lstrip() == "handle.proc.send_signal(signal.SIGTERM)"
    ]
    # It DOES still appear once, inside the pgid-None fallback.
    assert len(live_bare) == 1


def test_stop_escalates_to_sigkill_after_timeout():
    idx = RX.find("def stop_rx_worker(")
    end = RX.find("\ndef ", idx + 1)
    body = RX[idx:end if end > 0 else idx + 5000]
    assert "escalating" in body.lower()
    # Wait/timeout gate is present.
    assert "handle.proc.wait(timeout=timeout_s)" in body
    assert "except subprocess.TimeoutExpired:" in body


# --- RX-4: port 0 coercion -----------------------------------------


def test_start_rx_worker_coerces_port_zero_to_none():
    idx = RX.find("audit RX-4")
    assert idx > 0
    body = RX[idx:idx + 1500]
    # BOTH dst_port and src_port are coerced.
    assert "if dst_port is not None and int(dst_port) <= 0:" in body
    assert "if src_port is not None and int(src_port) <= 0:" in body
    # And the fields become None (i.e., no --dst-port on the argv).
    assert "dst_port = None" in body
    assert "src_port = None" in body


def test_argv_omits_port_flags_when_port_is_none():
    """The existing guard `if dst_port is not None:` in the argv
    builder must still be intact — the coercion above sets them
    to None, and the guard is what actually drops the flag."""
    idx = RX.find("if dst_port is not None:")
    assert idx > 0
    body = RX[idx:idx + 500]
    assert 'app.extend(["--dst-port"' in body


# --- RX-5: file_prefix uniqueness ----------------------------------


def test_file_prefix_appends_random_suffix():
    idx = RX.find("audit RX-5")
    assert idx > 0
    body = RX[idx:idx + 800]
    assert "import secrets as _secrets" in body
    assert "_secrets.token_hex(3)" in body


def test_file_prefix_still_greppable_by_stream_id_prefix():
    """The `rxw_{stream_id[:8]}_...` shape is preserved — the
    audit-fix ADDS a suffix, doesn't rename the pattern."""
    idx = RX.find("audit RX-5")
    body = RX[idx:idx + 800]
    assert 'f"rxw_{stream_id[:8]}_' in body


# --- RX-6: is_running() checks scope + heartbeat -------------------


def test_is_running_checks_systemctl_first():
    idx = RX.find("def is_running(self)")
    end = RX.find("\n    def ", idx + 1)
    body = RX[idx:end if end > 0 else idx + 3000]
    assert "audit RX-3" in body
    assert '"systemctl", "is-active"' in body
    assert 'f"{self.unit}.scope"' in body


def test_is_running_falls_back_to_wrapper_poll():
    idx = RX.find("def is_running(self)")
    end = RX.find("\n    def ", idx + 1)
    body = RX[idx:end if end > 0 else idx + 3000]
    # Last-resort branch still checks proc.poll().
    assert "return self.proc.poll() is None" in body


# --- RX-7: registry lock split into 3 phases -----------------------


def test_registry_defines_spawning_sentinel():
    assert "_SPAWNING = object()" in MGR
    assert "audit RX-7" in MGR


def test_start_releases_lock_before_popen():
    idx = MGR.find("def start(")
    end = MGR.find("\n    def ", idx + 1)
    body = MGR[idx:end if end > 0 else idx + 4000]
    # Two `with self._lock:` blocks visible in start()'s body:
    # Phase 1 (reserve) and Phase 3 (swap). Phase 2 (Popen) has NO
    # lock context, so the count is 2 or 3 (including the failure-
    # cleanup lock re-acquire), but never 1.
    lock_blocks = body.count("with self._lock:")
    assert lock_blocks >= 2, f"expected >=2 lock blocks, saw {lock_blocks}"
    assert "self._handles[stream_id] = self._SPAWNING" in body
    # The actual spawn call must NOT be inside a `with self._lock:` block.
    spawn_idx = body.find("handle = start_rx_worker(")
    assert spawn_idx > 0
    # Walk backwards from spawn_idx for the nearest lock/unlock — if
    # `with self._lock:` appears without the closing dedent between
    # it and the spawn, we'd be inside the lock. Cheap heuristic:
    # PHASE 2 comment must sit between the last lock line and the
    # spawn call.
    preceding = body[:spawn_idx]
    last_lock = preceding.rfind("with self._lock:")
    phase2_marker = preceding.find("PHASE 2", last_lock)
    assert phase2_marker > 0, (
        "spawn call must be outside the Phase 1 lock — Phase 2 marker "
        "missing"
    )


def test_start_swaps_sentinel_after_spawn():
    idx = MGR.find("def start(")
    body = MGR[idx:idx + 4000]
    # Phase 3 swap.
    assert "self._handles[stream_id] = handle" in body


def test_start_releases_reservation_on_spawn_failure():
    idx = MGR.find("def start(")
    body = MGR[idx:idx + 4000]
    # Except-clause pops the sentinel so a retry can proceed.
    assert "except Exception:" in body
    assert "self._handles.pop(stream_id, None)" in body


def test_list_skips_spawning_sentinel():
    idx = MGR.find("def list(self)")
    end = MGR.find("\n    def ", idx + 1)
    body = MGR[idx:end if end > 0 else idx + 3000]
    assert "if h is self._SPAWNING:" in body
    assert '"status": "spawning"' in body


def test_latest_for_ignores_spawning_sentinel():
    idx = MGR.find("def latest_for(")
    end = MGR.find("\n    def ", idx + 1)
    body = MGR[idx:end if end > 0 else idx + 2000]
    assert "handle is self._SPAWNING" in body


def test_stop_handles_sentinel():
    idx = MGR.find("def stop(self, stream_id")
    end = MGR.find("\n    def ", idx + 1)
    body = MGR[idx:end if end > 0 else idx + 3000]
    assert "handle is self._SPAWNING" in body
    assert '"status": "spawning"' in body


# --- Metadata -------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 255)
