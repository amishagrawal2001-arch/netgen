"""v0.5.118: rx_worker stderr capture + post-spawn liveness check.

Pre-fix: rx_worker stderr was captured to PIPE but never drained.
Workers that emitted >64 KB of stderr blocked. And even when they
didn't, post-mortem diagnosis required journalctl on the host —
because the captured pipe was discarded on process exit.

Fix: a second reader thread drains stderr into a bounded ring,
queryable via handle.stderr_tail(). The manager's list() exposes
it. The auto-lifecycle spawn does a 600 ms liveness check; if
the worker died, it folds the stderr tail into the outcome's
reason so the start response carries the actual death cause.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_handle_carries_stderr_lines_field():
    """RxHandle gained _stderr_lines + _stderr_reader fields and
    a stderr_tail() method. Pin the shape."""
    from utils.dpdk_rx_worker import RxHandle
    # Construct without a real subprocess — only check the dataclass
    # fields exist + the accessor works.
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.poll.return_value = None
    h = RxHandle(
        stream_id="t", proc=mock_proc, cmd=["x"], started_at=0.0,
    )
    assert hasattr(h, "_stderr_lines")
    assert h._stderr_lines == []
    assert hasattr(h, "_stderr_reader")
    assert hasattr(h, "stderr_tail")
    h._stderr_lines = ["a", "b", "c", "d", "e"]
    assert h.stderr_tail(n=3) == ["c", "d", "e"]
    assert h.stderr_tail(n=10) == ["a", "b", "c", "d", "e"]


def test_stderr_tail_bounded_by_constant():
    """STDERR_TAIL_LINES caps the in-memory ring. New lines drop
    the oldest. Pin the cap so a regression doesn't introduce an
    unbounded buffer."""
    from utils.dpdk_rx_worker import STDERR_TAIL_LINES
    assert isinstance(STDERR_TAIL_LINES, int)
    assert 50 <= STDERR_TAIL_LINES <= 1000, (
        f"STDERR_TAIL_LINES = {STDERR_TAIL_LINES} — should be in "
        f"the 50..1000 range. Smaller risks losing the death cause; "
        f"larger wastes memory per dead-worker handle."
    )


def test_manager_list_surfaces_stderr_tail_when_present():
    """Manager's list() exposes stderr_tail + exit_code on each
    handle entry when stderr lines exist. Live workers with no
    stderr (clean startup) don't get the field — keeps the
    common-case response shape unchanged."""
    from utils.dpdk_rx_manager import RxRegistry
    from utils.dpdk_rx_worker import RxHandle
    reg = RxRegistry()
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.poll.return_value = 1   # exited rc=1
    h = RxHandle(
        stream_id="dead", proc=mock_proc, cmd=["x"], started_at=0.0,
    )
    h._stderr_lines = ["EAL: failed to open hugepage", "Cannot init EAL"]
    reg._handles["dead"] = h
    out = reg.list()
    assert len(out) == 1
    entry = out[0]
    assert "stderr_tail" in entry
    assert "Cannot init EAL" in entry["stderr_tail"][-1]
    assert entry["exit_code"] == 1


def test_manager_list_omits_stderr_tail_when_clean():
    """Live worker with no stderr yet — list() entry should not
    carry an empty `stderr_tail` field. Keeps the existing
    behavior for healthy workers."""
    from utils.dpdk_rx_manager import RxRegistry
    from utils.dpdk_rx_worker import RxHandle
    reg = RxRegistry()
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.poll.return_value = None   # still running
    h = RxHandle(
        stream_id="live", proc=mock_proc, cmd=["x"], started_at=0.0,
    )
    # No stderr lines added.
    reg._handles["live"] = h
    out = reg.list()
    assert "stderr_tail" not in out[0]


def test_auto_lifecycle_surfaces_stderr_in_reason_on_quick_death():
    """`_maybe_start_dpdk_rx_for_stream` does a 600 ms liveness
    check after spawn. If the worker died, the outcome's reason
    field carries the stderr tail so /api/traffic/start's response
    surfaces the actual death cause to the operator instead of
    leaving them to journalctl. Pre-fix the outcome said
    actual=dpdk + reason=None even when the worker was dead."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    from utils.dpdk_rx_worker import RxHandle

    # Mock a dead worker that emitted a clear EAL error.
    mock_proc = MagicMock()
    mock_proc.pid = 99
    mock_proc.poll.return_value = 1
    dead_handle = RxHandle(
        stream_id="s1", proc=mock_proc, cmd=["x"], started_at=0.0,
    )
    dead_handle._stderr_lines = [
        "EAL: VFIO support initialized",
        "mlx5_common: probe device(0000:2b:00.1) failed",
        "Cannot init pmd",
    ]

    # Mock the registry: start() returns success + the handle
    # ends up in _handles[s1] (so the liveness check finds it).
    fake_registry = MagicMock()
    fake_registry.start.return_value = {"pid": 99}
    fake_registry._handles = {"s1": dead_handle}
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.1",
    ), patch(
        "utils.dpdk_rx_manager.registry", fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s1", rx_interface="ens2f1np1",
            stream_data={"rx_engine": "dpdk"},
        )

    assert result["requested"] is True
    assert result["actual"] == "scapy", (
        "fast-death worker must be reported as fell-back to "
        "scapy — pre-fix this was reported as actual=dpdk"
    )
    assert "rx_worker died" in result["reason"]
    assert "mlx5_common" in result["reason"], (
        "stderr tail must be folded into the reason so the "
        "operator sees the cause without journalctl"
    )


def test_auto_lifecycle_unchanged_for_healthy_worker():
    """When the post-spawn liveness check passes (worker is
    still running after 600 ms), the outcome is unchanged from
    pre-v0.5.118: actual=dpdk, reason=None, pid set."""
    from run_tgen_server import _maybe_start_dpdk_rx_for_stream
    from utils.dpdk_rx_worker import RxHandle

    mock_proc = MagicMock()
    mock_proc.pid = 77
    mock_proc.poll.return_value = None   # still alive
    live_handle = RxHandle(
        stream_id="s2", proc=mock_proc, cmd=["x"], started_at=0.0,
    )

    fake_registry = MagicMock()
    fake_registry.start.return_value = {"pid": 77}
    fake_registry._handles = {"s2": live_handle}
    fake_registry_factory = MagicMock(return_value=fake_registry)

    with patch(
        "utils.nic_counters.iface_to_pci_bdf",
        return_value="0000:2b:00.1",
    ), patch(
        "utils.dpdk_rx_manager.registry", fake_registry_factory,
    ):
        result = _maybe_start_dpdk_rx_for_stream(
            stream_id="s2", rx_interface="ens2f1np1",
            stream_data={"rx_engine": "dpdk"},
        )

    assert result["actual"] == "dpdk"
    assert result["reason"] is None
    assert result["pid"] == 77
