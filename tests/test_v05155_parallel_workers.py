"""v0.5.155: Parallel workers + 🚀 Max BW for true single-HCA BW
scaling.

Operator: "how can we add parallel cpus for BW scale ?"
       → "go max" (Layer 2 + Layer 3 in one ship).

perftest is single-threaded, so single-process QP scaling tops out
on one CPU core. v0.5.155 spawns N independent perftest processes
per side, each pinned to a different core via
`numactl --cpunodebind --membind taskset -c <N>`. Pinning all
workers to the HCA's home NUMA node aligns CPU + RAM + PCIe path
(avoids the cross-NUMA penalty from v0.5.131).

Three layers (all in this one ship):
  1. Server cpu_pin + numa_pin params on /api/rdma/perftest/start.
  2. /api/rdma/host_info route exposing NUMA topology.
  3. Client "Parallel workers" spinbox + "🚀 Max BW" button that
     auto-picks the NUMA-local CPU count.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_PERF = (REPO / "utils" / "rdma_perf.py").read_text()
SRC_SERVER = (REPO / "run_tgen_server.py").read_text()
SRC_DLG = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()


# ───── Layer 2: server cpu_pin + numa_pin ────────────────────────────────


def test_perftest_spawn_supports_cpu_pin():
    """start_perftest pulls cpu_pin from opts and wraps the
    command in `taskset -c <N>` before subprocess.Popen."""
    assert 'cpu_pin = opts.get("cpu_pin")' in SRC_PERF
    assert '"taskset", "-c", str(int(cpu_pin))' in SRC_PERF


def test_perftest_spawn_supports_numa_pin():
    """numa_pin → `numactl --cpunodebind=<N> --membind=<N>`."""
    assert 'numa_pin = opts.get("numa_pin")' in SRC_PERF
    assert 'f"--cpunodebind={int(numa_pin)}"' in SRC_PERF
    assert 'f"--membind={int(numa_pin)}"' in SRC_PERF


def test_pin_order_numactl_then_taskset():
    """Composition order: `numactl … -- taskset -c <N> perftest …`.
    numactl is the outermost wrapper because it constrains the
    set of CPUs/memory nodes; taskset further narrows to ONE
    specific core within that set."""
    # numa_pin wrapping happens BEFORE cpu_pin wrapping in the
    # source — so cpu_pin ends up as the OUTER (leftmost) wrapper
    # at runtime. That's what we want (taskset first, then
    # numactl pinned by it).
    perf = SRC_PERF
    # Both blocks present in the start_perftest function body.
    assert 'if numa_pin is not None:' in perf
    assert 'if cpu_pin is not None:' in perf
    numa_pos = perf.index('if numa_pin is not None:')
    cpu_pos = perf.index('if cpu_pin is not None:')
    # numa_pin wrapper installed first → taskset prepended after =
    # taskset is the leftmost (outermost) argv prefix at exec time.
    assert numa_pos < cpu_pos


# ───── Layer 2: host_info helper + route ─────────────────────────────────


def test_host_info_module_exists():
    """The pure helper at utils/rdma_host_info.py imports cleanly
    and exposes the three primary functions."""
    from utils.rdma_host_info import (
        host_info, pick_workers_for_hca, _parse_cpu_list,
    )
    assert callable(host_info)
    assert callable(pick_workers_for_hca)


def test_parse_cpu_list_ranges_and_singletons():
    from utils.rdma_host_info import _parse_cpu_list
    assert _parse_cpu_list("0-3") == [0, 1, 2, 3]
    assert _parse_cpu_list("0,2,4") == [0, 2, 4]
    assert _parse_cpu_list("0-3,8,10-12") == [0, 1, 2, 3, 8, 10, 11, 12]
    assert _parse_cpu_list("") == []


def test_pick_workers_uses_numa_local_cores():
    """Given an HCA on NUMA node 0 with 12 cores, the picker
    returns 12 workers pinned to node 0."""
    from utils.rdma_host_info import pick_workers_for_hca
    info = {
        "cpu_count": 24,
        "numa_nodes": [
            {"node_id": 0, "cpus": list(range(12))},
            {"node_id": 1, "cpus": list(range(12, 24))},
        ],
        "hca_numa": {"mlx5_0": 0, "mlx5_1": 1},
    }
    pick = pick_workers_for_hca("mlx5_0", info=info)
    assert pick["worker_count"] == 12
    assert pick["numa_pin"] == 0
    assert pick["cpus"] == list(range(12))


def test_pick_workers_falls_back_when_hca_unknown():
    """HCA name not in hca_numa → no NUMA pin, use a flat list."""
    from utils.rdma_host_info import pick_workers_for_hca
    info = {
        "cpu_count": 8,
        "numa_nodes": [{"node_id": 0, "cpus": list(range(8))}],
        "hca_numa": {},
    }
    pick = pick_workers_for_hca("rocep_unknown", info=info)
    assert pick["worker_count"] >= 1
    assert pick["numa_pin"] is None


def test_pick_workers_caps_at_16():
    """Past 16 the marginal BW return drops sharply and HCA QP
    context cache thrashes. v0.5.155 caps at 16."""
    from utils.rdma_host_info import pick_workers_for_hca
    info = {
        "cpu_count": 64,
        "numa_nodes": [{"node_id": 0, "cpus": list(range(64))}],
        "hca_numa": {"mlx5_0": 0},
    }
    pick = pick_workers_for_hca("mlx5_0", info=info)
    assert pick["worker_count"] == 16


def test_pick_workers_respects_requested_below_cap():
    """If the operator asks for N workers and N <= NUMA-local
    CPU count, honor it."""
    from utils.rdma_host_info import pick_workers_for_hca
    info = {
        "cpu_count": 24,
        "numa_nodes": [{"node_id": 0, "cpus": list(range(12))}],
        "hca_numa": {"mlx5_0": 0},
    }
    pick = pick_workers_for_hca("mlx5_0", requested=4, info=info)
    assert pick["worker_count"] == 4
    assert pick["cpus"] == [0, 1, 2, 3]


def test_host_info_route_exists():
    """GET /api/rdma/host_info is wired."""
    assert '@app.route("/api/rdma/host_info"' in SRC_SERVER
    assert "def api_rdma_host_info" in SRC_SERVER


def test_host_info_route_returns_default_on_failure():
    """Sysfs missing should never raise — route returns the safe
    {cpu_count:1, numa_nodes:[], hca_numa:{}} skeleton."""
    body = re.search(
        r"def api_rdma_host_info\(\):.*?(?=\n@app\.route)",
        SRC_SERVER, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert '"cpu_count": 1' in text
    assert '"numa_nodes": []' in text


# ───── Layer 3: client UI ────────────────────────────────────────────────


def test_parallel_workers_spinbox_added():
    """New `_parallel_workers_spin` next to QP count."""
    assert "self._parallel_workers_spin = QSpinBox()" in SRC_DLG
    # Range 1-64 (the dialog cap; actual runtime cap = host cpu_count).
    assert "self._parallel_workers_spin.setRange(1, 64)" in SRC_DLG


def test_max_bw_button_added():
    """🚀 Max BW button next to the spinbox."""
    assert "🚀 Max BW" in SRC_DLG
    assert "_max_bw_btn.clicked.connect(self._on_max_bw_clicked)" in SRC_DLG


def test_max_bw_handler_calls_host_info_then_picker():
    """The handler queries /api/rdma/host_info and feeds the
    result into pick_workers_for_hca."""
    body = _extract_method(SRC_DLG, "_on_max_bw_clicked")
    assert "/api/rdma/host_info" in body
    assert "pick_workers_for_hca" in body


def test_extra_workers_state_initialized():
    """The dialog tracks workers 1..N-1 in _extra_workers list.
    Worker 0 still uses _server_job_id/_client_job_id."""
    assert "self._extra_workers" in SRC_DLG
    assert "self._host_info_cache" in SRC_DLG


def test_proceed_with_start_stashes_params_and_clears_extras():
    """Each new Start resets _extra_workers and stashes
    _last_start_params so the fan-out in _on_client_started has
    what it needs."""
    body = _extract_method(SRC_DLG, "_proceed_with_start")
    assert "self._last_start_params" in body
    assert "self._extra_workers = []" in body


def test_on_client_started_fans_out_when_workers_gt_1():
    """After worker 0 is up, _on_client_started spawns extras."""
    body = _extract_method(SRC_DLG, "_on_client_started")
    assert "_parallel_workers_spin.value()" in body
    assert "_start_extra_workers(" in body


def test_start_extra_workers_method_exists():
    assert "def _start_extra_workers(" in SRC_DLG


def test_extra_workers_get_cpu_pin_and_numa_pin():
    """Each extra worker POST includes cpu_pin (per-worker
    distinct) AND numa_pin (shared across workers, matches HCA's
    home node)."""
    body = _extract_method(SRC_DLG, "_start_extra_workers")
    assert '"cpu_pin": cpu' in body
    assert '"numa_pin": numa_pin' in body


def test_extra_workers_use_distinct_listen_ports():
    """Workers can't share a listen_port (perftest control TCP).
    Each gets base_port + worker_idx."""
    body = _extract_method(SRC_DLG, "_start_extra_workers")
    assert "extra_port = base_port + worker_idx" in body


def test_extra_workers_use_distinct_handshakes():
    """Each worker pair coordinates via its own handshake_id —
    netgen's handshake broker would otherwise route worker 1's
    server-side info to worker 0's client."""
    body = _extract_method(SRC_DLG, "_start_extra_workers")
    assert "f\"{self._handshake_id}-w{worker_idx}\"" in body


# ───── Poll + Stop refactors ────────────────────────────────────────────


def test_poll_jobs_polls_extras_too():
    body = _extract_method(SRC_DLG, "_poll_jobs")
    assert "_poll_extras()" in body


def test_poll_extras_iterates_extra_workers():
    body = _extract_method(SRC_DLG, "_poll_extras")
    assert "for w in list(self._extra_workers)" in body
    # Polls both halves.
    assert 'w.get("server")' in body
    assert 'w.get("client")' in body


def test_stop_stops_extras_too():
    body = _extract_method(SRC_DLG, "_on_stop_clicked")
    assert "for w in self._extra_workers" in body


def test_total_summary_emitted_on_completion():
    """When all worker halves finish, emit a TOTAL line summing
    BW + MsgRate across workers."""
    assert "_maybe_emit_total" in SRC_DLG
    body = _extract_method(SRC_DLG, "_maybe_emit_total")
    assert "TOTAL" in body
    assert "total_bw" in body


# ───── helpers ──────────────────────────────────────────────────────────


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)
