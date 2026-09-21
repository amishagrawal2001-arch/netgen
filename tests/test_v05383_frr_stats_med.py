"""v0.5.383 — FRR + stats MED backlog (5 items).

  X1  start_frr_container per-device start lock.
  X2  device_manager 4 sites → frr_manager singleton.
  X3  remove_device_protocols now calls stop_frr_container.
  X4  StreamTracker.add_stream O(n²) → O(1) sidecar set.
  X5  Ghost row flicker after Delete Stream fixed.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_frr_docker_ast_parses():
    ast.parse(_read("utils/frr_docker.py"))


def test_device_manager_ast_parses():
    ast.parse(_read("utils/device_manager.py"))


def test_traffic_gen_ast_parses():
    ast.parse(_read("multithreaded_traffic_gen.py"))


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── X1: start lock ───


def test_x1_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.383 (audit FRR-X1)" in src
    # helper + lock helper + acquire in start_frr_container + finally
    assert src.count("v0.5.383 (audit FRR-X1)") >= 3


def test_x1_start_lock_dict_defined():
    src = _read("utils/frr_docker.py")
    assert "self._start_locks:" in src
    assert "self._start_locks_meta" in src


def test_x1_lock_helper_defined():
    src = _read("utils/frr_docker.py")
    assert "def _start_lock_for(self, device_id: str)" in src
    _idx = src.index("def _start_lock_for(self, device_id: str)")
    body = src[_idx:_idx + 800]
    assert "self._start_locks_meta" in body
    assert "self._start_locks[device_id]" in body


def test_x1_start_container_acquires_and_finally_releases():
    src = _read("utils/frr_docker.py")
    _idx = src.index("def start_frr_container(self, device_id: str")
    _end = src.index("def _configure_interfaces(", _idx)
    body = src[_idx:_end]
    # acquires at top
    assert "_start_lock = self._start_lock_for(device_id)" in body
    assert "_start_lock.acquire()" in body
    # release in finally so every early-return + raise unlocks
    assert "finally:" in body
    assert "_start_lock.release()" in body


# ─── X2: device_manager singleton ───


def test_x2_marker_present():
    src = _read("utils/device_manager.py")
    assert "v0.5.383 (audit FRR-X2)" in src
    # 4 sites should each carry a marker
    assert src.count("v0.5.383 (audit FRR-X2)") >= 4


def test_x2_singleton_import_replaces_per_call_instantiation():
    """Every previous `FRRDockerManager()` call site in
    device_manager.py must now import the module singleton."""
    src = _read("utils/device_manager.py")
    # Zero remaining direct instantiations
    _stripped = re.sub(r"#[^\n]*", "", src)
    _stripped = re.sub(r'""".*?"""', "", _stripped, flags=re.DOTALL)
    assert "FRRDockerManager()" not in _stripped, (
        "device_manager still has FRRDockerManager() in executable "
        "code — singleton pattern only partially applied"
    )
    # Singleton import present at 4+ sites
    _import_count = src.count("from utils.frr_docker import frr_manager")
    assert _import_count >= 4, (
        f"Expected ≥4 singleton imports; got {_import_count}"
    )


# ─── X3: remove_device_protocols → stop_frr_container ───


def test_x3_marker_present():
    src = _read("utils/device_manager.py")
    assert "v0.5.383 (audit FRR-X3)" in src


def test_x3_calls_stop_frr_container():
    """remove_device_protocols must end with a stop_frr_container
    call so the container + VRF + table alloc are all cleaned."""
    src = _read("utils/device_manager.py")
    _idx = src.index("def remove_device_protocols(device_data:")
    _end = src.index("\n    @staticmethod\n", _idx + 1) if "@staticmethod" in src[_idx + 100:] else len(src)
    body = src[_idx:_end]
    assert "stop_frr_container(" in body
    assert "remove=True" in body
    assert "v0.5.383 (audit FRR-X3)" in body


def test_x3_uses_singleton_not_per_call():
    """The stop_frr_container call must go through the singleton,
    not a fresh FRRDockerManager() (would collide with X2)."""
    src = _read("utils/device_manager.py")
    _idx = src.index("v0.5.383 (audit FRR-X3)")
    body = src[_idx:_idx + 2500]
    assert "from utils.frr_docker import frr_manager" in body
    assert "FRRDockerManager()" not in body


# ─── X4: add_stream O(1) ───


def test_x4_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.383 (audit stats-X4)" in src
    # ctor + add_stream + remove_stream_by_id
    assert src.count("v0.5.383 (audit stats-X4)") >= 3


def test_x4_sidecar_set_defined_in_ctor():
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("class StreamTracker")
    body = src[_idx:_idx + 2500]
    assert "self._stream_keys = set()" in body


def test_x4_add_stream_uses_set_membership():
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def add_stream(self, stream):")
    body = src[_idx:_idx + 3500]
    # Set membership check
    assert "if _key in self._stream_keys:" in body
    # Set add (idempotent)
    assert "self._stream_keys.add(_key)" in body
    # No more list-comp rebuild inside add_stream
    _prewrite = body[:body.index("self.active_streams.append(")]
    assert "self.active_streams = [" not in _prewrite


def test_x4_remove_stream_uses_set_discard():
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def remove_stream_by_id(self, interface, stream_id):")
    _end = _idx + 2500
    body = src[_idx:_end]
    assert "self._stream_keys.discard((interface, stream_id))" in body
    # No more list-comp rebuild in remove_stream_by_id
    assert "self.active_streams = [" not in body


def test_x4_set_membership_behavioral():
    """Behavioral: adding same (iface, sid) twice → one row + set has one entry."""
    from multithreaded_traffic_gen import StreamTracker
    _t = StreamTracker()
    _t.add_stream({
        "stream_id": "sX4a", "interface": "eth0",
        "stream_name": "s1", "stop_event": None,
        "rx_thread": None, "rx_interface": "eth1",
        "flow_tracking_enabled": False, "future": None,
        "frame_size": 64,
    })
    _t.add_stream({
        "stream_id": "sX4a", "interface": "eth0",
        "stream_name": "s1", "stop_event": None,
        "rx_thread": None, "rx_interface": "eth1",
        "flow_tracking_enabled": False, "future": None,
        "frame_size": 64,
    })
    # Dup → dedup → still 1 row + 1 set entry
    _matching = [s for s in _t.active_streams
                 if s["interface"] == "eth0" and s["stream_id"] == "sX4a"]
    assert len(_matching) == 1
    assert ("eth0", "sX4a") in _t._stream_keys
    # Remove → both gone
    _t.remove_stream_by_id("eth0", "sX4a")
    assert ("eth0", "sX4a") not in _t._stream_keys
    _matching_after = [s for s in _t.active_streams
                       if s["interface"] == "eth0" and s["stream_id"] == "sX4a"]
    assert len(_matching_after) == 0


# ─── X5: ghost row flicker ───


def test_x5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.383 (audit stats-X5)" in src


def test_x5_filter_scans_stream_table_for_known_ids():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.383 (audit stats-X5)")
    body = src[_idx:_idx + 3500]
    assert "_known_ids = set()" in body
    assert "self.stream_table.item(_row, 2)" in body
    # UserRole holds the stream_id
    assert "data(Qt.UserRole)" in body


def test_x5_filters_pending_before_render():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.383 (audit stats-X5)")
    body = src[_idx:_idx + 3500]
    assert "_filtered = [s for s in self._pending_poll_stream_stats" in body
    assert "s.get(\"stream_id\") in _known_ids" in body
    # Uses filtered list, not the raw pending list
    assert "self.update_stream_statistics_table(_filtered)" in body


def test_x5_fail_safe_passes_through_unchanged():
    """If the scan can't complete OR returns empty, pass through
    unchanged so we never accidentally hide legitimate rows."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.383 (audit stats-X5)")
    body = src[_idx:_idx + 3500]
    assert "_known_ids = None" in body
    assert "if _known_ids is not None and _known_ids:" in body
    assert "_filtered = self._pending_poll_stream_stats" in body


# ─── version guard ───


def test_pyproject_version_at_least_0583():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 383)


# ─── regression guards ───


def test_v0382_frr_teardown_bundle_intact():
    src = _read("utils/frr_docker.py")
    assert "v0.5.382 (audit FRR-W1 + W2 + W3)" in src
    assert "_find_existing_container" in src


def test_v0382_stats_polling_backoff_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.382 (audit stats-W4)" in src or "v0.5.382 (W4)" in src
    assert "_STATS_BACKOFF_INITIAL_S = 2.0" in src


def test_v0380_tracker_row_has_rx_drained_event():
    """v0.5.380 T5's rx_drained_event field must still be added
    to every new row — X4 didn't accidentally strip it."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def add_stream(self, stream):")
    body = src[_idx:_idx + 4000]
    assert '"rx_drained_event": threading.Event()' in body


def test_v0373_vrf_alloc_infra_intact():
    src = _read("utils/frr_docker.py")
    assert "self._vrf_allocated" in src
    assert "self._vrf_alloc_lock" in src
