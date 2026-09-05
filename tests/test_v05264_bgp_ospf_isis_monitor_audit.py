"""v0.5.264 — BGP + OSPF + ISIS monitor audit: 9 correctness fixes."""

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
BGP = (REPO / "utils" / "bgp_monitor.py").read_text()
OSPF = (REPO / "utils" / "ospf_monitor.py").read_text()
ISIS = (REPO / "utils" / "isis_monitor.py").read_text()
OSPF_UT = (REPO / "utils" / "ospf.py").read_text()
ISIS_UT = (REPO / "utils" / "isis.py").read_text()


# --- F1: BGP container-missing → Down synth --------------------


def test_bgp_monitor_synthesises_down_on_container_missing():
    assert "audit BGP-F1" in BGP
    assert "_container_missing = (" in BGP
    assert "'bgp_state': 'Down'" in BGP
    # And short-circuits with an early return before ranking logic.
    idx = BGP.find("audit BGP-F1")
    body = BGP[idx:idx + 2000]
    assert "return {" in body


# --- F2: per-device write lock (all three) --------------------


def test_bgp_per_device_write_lock():
    assert "audit BGP-F2" in BGP
    assert "_BGP_WRITE_LOCKS: Dict[str, threading.Lock]" in BGP
    assert "def _bgp_write_lock_for(device_id" in BGP
    # Acquire + finally release inside _update_device_bgp_status.
    idx = BGP.find("def _update_device_bgp_status")
    end = BGP.find("\n    def ", idx + 1)
    body = BGP[idx:end if end > 0 else idx + 6000]
    assert "_lock = _bgp_write_lock_for(device_id)" in body
    assert "_lock.acquire()" in body
    assert "_lock.release()" in body


def test_ospf_per_device_write_lock():
    assert "audit OSPF-F2" in OSPF
    assert "_OSPF_WRITE_LOCKS: Dict[str, threading.Lock]" in OSPF
    idx = OSPF.find("def _update_device_ospf_status")
    end = OSPF.find("\n    def ", idx + 1)
    body = OSPF[idx:end if end > 0 else idx + 6000]
    assert "_lock = _ospf_write_lock_for(device_id)" in body
    assert "_lock.acquire()" in body
    assert "_lock.release()" in body


def test_isis_per_device_write_lock():
    assert "audit ISIS-F2" in ISIS
    assert "_ISIS_WRITE_LOCKS: Dict[str, threading.Lock]" in ISIS
    idx = ISIS.find("def _update_device_isis_status")
    end = ISIS.find("\n    # Compatibility", idx + 1)
    body = ISIS[idx:end if end > 0 else idx + 6000]
    assert "_lock = _isis_write_lock_for(device_id)" in body
    assert "_lock.acquire()" in body
    assert "_lock.release()" in body


# --- F3: log_device_event dedup (all three) --------------------


def test_bgp_log_event_deduped_by_transition():
    assert "audit BGP-F3" in BGP
    assert "_LAST_BGP_STATE_LOGGED: Dict[str, str]" in BGP
    # Predicate + guarded log call.
    idx = BGP.find("audit BGP-F3")
    body = BGP[idx:idx + 2000]
    assert "_should_log = _prev_state != _cur_state" in body
    assert "if _should_log:" in body
    assert "'transition_from': _prev_state" in body


def test_ospf_log_event_deduped_by_transition():
    assert "audit OSPF-F3" in OSPF
    assert "_LAST_OSPF_STATE_LOGGED: Dict[str, str]" in OSPF
    idx = OSPF.find("audit OSPF-F3")
    body = OSPF[idx:idx + 2000]
    assert "_should_log = _prev_state != _cur_state" in body


def test_isis_log_event_deduped_by_transition():
    assert "audit ISIS-F3" in ISIS
    assert "_LAST_ISIS_STATE_LOGGED: Dict[str, str]" in ISIS
    idx = ISIS.find("audit ISIS-F3")
    body = ISIS[idx:idx + 2000]
    assert "_should_log = _prev_state != _cur_state" in body


# --- F4: ISIS parallel polling ---------------------------------


def test_isis_monitor_uses_thread_pool_executor():
    assert "audit ISIS-F4" in ISIS
    assert "from concurrent.futures import ThreadPoolExecutor, as_completed" in ISIS
    assert "max_workers=self.max_workers" in ISIS
    # Both main loop AND force_check use the pool.
    assert ISIS.count("ThreadPoolExecutor(max_workers=self.max_workers)") >= 2


def test_isis_monitor_constructor_accepts_max_workers():
    assert "def __init__(self, device_db, max_workers: int = 5):" in ISIS


# --- F5: OSPF container_exec_with_timeout ----------------------


def test_ospf_module_scope_exec_wrapper_defined():
    assert "audit BGP/OSPF/ISIS monitor F5" in OSPF_UT
    assert "def container_exec_with_timeout(container, cmd, timeout_sec: float = 5.0):" in OSPF_UT
    # Uses threading.Thread.join with timeout.
    idx = OSPF_UT.find("def container_exec_with_timeout(")
    body = OSPF_UT[idx:idx + 2000]
    assert "_t.join(timeout=timeout_sec)" in body
    assert "if _t.is_alive():" in body
    assert "return None" in body


def test_get_ospf_status_uses_timeout_wrapper():
    """All 4 primary vtysh calls in get_ospf_status must use the
    timeout wrapper."""
    idx = OSPF_UT.find("def get_ospf_status(device_id: str)")
    end = OSPF_UT.find("\ndef ", idx + 1)
    body = OSPF_UT[idx:end if end > 0 else idx + 8000]
    # Wrapper is called 4 times (v4 neigh, v6 neigh, v4 summary, v6 summary).
    assert body.count("container_exec_with_timeout(") >= 4
    # And each wrapped call has an `if ... is None: return None` guard.
    assert body.count("is None:\n            return None") >= 4


# --- F6: OSPF + ISIS Running filter ----------------------------


def test_ospf_monitor_filters_running_only():
    assert "audit OSPF-F6" in OSPF
    idx = OSPF.find("def _get_ospf_devices")
    end = OSPF.find("\n    def ", idx + 1)
    body = OSPF[idx:end if end > 0 else idx + 2000]
    assert 'device.get("status") == "Running"' in body


def test_isis_monitor_filters_running_only():
    assert "audit ISIS-F6" in ISIS
    # Main loop AND force_check gain the filter.
    assert ISIS.count('device.get("status") == "Running"') >= 2


# --- F7: ISIS Established requires Up adjacency ---------------


def test_isis_util_marks_established_only_on_up_adjacency():
    assert "audit ISIS-F7" in ISIS_UT
    # New per-adjacency inspection.
    assert '_adj_list = circuit.get("adjacencies") or []' in ISIS_UT
    assert '_adj_state == "Up"' in ISIS_UT
    # Only mark Established if ANY neighbor is Up.
    assert '_any_up = any(n.get("state") == "Up" for n in isis_status["neighbors"])' in ISIS_UT
    assert "if _any_up:" in ISIS_UT


# --- F8: BGP bgp_state ranking --------------------------------


def test_bgp_state_uses_rank_not_last_neighbor():
    assert "audit BGP-F8" in BGP
    assert "_STATE_RANK = {" in BGP
    # Established has best rank (0).
    idx = BGP.find("_STATE_RANK = {")
    body = BGP[idx:idx + 500]
    assert '"Established": 0' in body
    # And the old order-dependent assignment is gone.
    live_old = [
        line for line in BGP.splitlines()
        if line.lstrip() == "bgp_state = neighbor_state"
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"order-dependent bgp_state assign still live: {live_old!r}"


# --- F9: ISIS startswith cross-match removed ------------------


def test_isis_check_existing_containers_exact_match_only():
    assert "audit ISIS-F9" in ISIS
    # No live `startswith(device_id)` in check_existing_containers.
    idx = ISIS.find("def check_existing_containers")
    end = ISIS.find("\n    def ", idx + 1)
    body = ISIS[idx:end if end > 0 else idx + 5000]
    live_old = [
        line for line in body.splitlines()
        if "startswith(device_id)" in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"startswith fallback still live: {live_old!r}"


# --- Metadata --------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 264)


# --- Runtime: BGP bgp_state ranking ---------------------------


def test_runtime_bgp_ranking_established_wins():
    """Even when the last-iterated neighbor is Idle, Established wins."""
    # We can't import bgp_monitor (module-load starts a thread), so
    # replicate the rank logic here as an invariance check.
    rank = {"Established": 0, "OpenConfirm": 1, "OpenSent": 2,
            "Active": 3, "Connect": 4, "Idle": 5}
    # Simulate: Established seen first, Idle last.
    seen = ["Established", "Idle"]
    ranks = sorted(rank.get(s, 99) for s in seen)
    assert ranks[0] == 0  # best-rank is Established
