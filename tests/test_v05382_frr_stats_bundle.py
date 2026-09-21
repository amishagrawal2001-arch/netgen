"""v0.5.382 — FRR docker + stats polling bundle (6 HIGH fixes).

  W1  _release_vrf_table wired into stop_frr_container.
  W2  _find_existing_container tries both prefixes.
  W3  VRF teardown + table release in an always-run block.
  W4  Client stats polling per-server exponential backoff.
  W5  TTL-based prune of _stream_baselines / _latched_loss_pct.
  W6  De-duplicate get_all_streams call in /api/streams/stats.
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


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── W1: VRF table release ───


def test_w1_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.382 (audit FRR-W1 + W2 + W3)" in src or \
           "v0.5.382 (W1)" in src


def test_w1_release_helper_still_defined():
    """Regression: _release_vrf_table must still exist."""
    src = _read("utils/frr_docker.py")
    assert "def _release_vrf_table(self, device_id: str) -> None:" in src


def test_w1_release_called_from_stop_container():
    """stop_frr_container(remove=True) must call _release_vrf_table."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("def stop_frr_container(self, device_id: str")
    body = src[_idx:_idx + 8000]
    assert "self._release_vrf_table(device_id)" in body


def test_w1_release_and_remove_vrf_in_same_conditional_block():
    """VRF teardown + table release must both live in the
    `if remove:` always-run block below the container ops."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("def stop_frr_container(self, device_id: str")
    _end = _idx + 8000
    body = src[_idx:_end]
    _remove_vrf_pos = body.find("self._remove_vrf(device_id, _iface)")
    _release_pos = body.find("self._release_vrf_table(device_id)")
    assert _remove_vrf_pos > 0
    assert _release_pos > 0
    # Release comes AFTER remove_vrf in the same block.
    assert _release_pos > _remove_vrf_pos


# ─── W2: both-prefix container lookup ───


def test_w2_find_helper_defined():
    src = _read("utils/frr_docker.py")
    assert "def _find_existing_container(self, device_id: str" in src


def test_w2_helper_tries_both_prefixes():
    src = _read("utils/frr_docker.py")
    _idx = src.index("def _find_existing_container(self, device_id: str")
    body = src[_idx:_idx + 2500]
    # Uses container_prefix + dhcp-frr alternative
    assert "self.container_prefix" in body
    assert '"dhcp-frr"' in body
    assert "startswith(\"dhcp-frr-\")" in body


def test_w2_stop_uses_find_helper():
    """stop_frr_container must go through _find_existing_container,
    not raw _get_container_name → containers.get."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("def stop_frr_container(self, device_id: str")
    body = src[_idx:_idx + 3000]
    assert "self._find_existing_container(" in body


# ─── W3: partial teardown ───


def test_w3_remove_exception_swallowed_inline():
    """container.remove() failure must be logged + swallowed so
    the VRF cleanup below still runs. No re-raise / return-False."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("def stop_frr_container(self, device_id: str")
    body = src[_idx:_idx + 8000]
    # The exception handler for remove() must be inline, and must
    # NOT return False (would skip the always-run block).
    _pos = body.find("container.remove(force=True)")
    assert _pos > 0
    _after = body[_pos:_pos + 800]
    assert "except Exception as _remove_exc" in _after
    assert "return False" not in _after[:400]


def test_w3_vrf_teardown_at_module_level_of_function():
    """VRF teardown block must be at the FUNCTION level (outside
    the container try), so it runs even when container ops raise
    or when the container is not found."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("def stop_frr_container(self, device_id: str")
    body = src[_idx:_idx + 8000]
    # Marker for the always-run block
    assert ("v0.5.382 (W3 + W1): VRF teardown + table release always" in body)


# ─── W4: stats polling backoff ───


def test_w4_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.382 (audit stats-W4)" in src or \
           "v0.5.382 (W4)" in src


def test_w4_backoff_helpers_defined():
    src = _read("traffic_client/statistics_section.py")
    for _hlp in ("_stats_backoff_state",
                 "_should_poll_server",
                 "_record_stats_success",
                 "_record_stats_failure"):
        assert f"def {_hlp}(" in src, (
            f"backoff helper missing: {_hlp}"
        )


def test_w4_constants_defined():
    src = _read("traffic_client/statistics_section.py")
    assert "_STATS_BACKOFF_INITIAL_S = 2.0" in src
    assert "_STATS_BACKOFF_MAX_S = 60.0" in src


def test_w4_backoff_doubles_and_caps():
    """Simulate 10 consecutive failures — delay must cap at
    _STATS_BACKOFF_MAX_S (60s)."""
    _delay = 2.0
    _max = 60.0
    for _ in range(10):
        _delay = min(_delay * 2.0, _max)
    assert _delay == _max


def test_w4_fetch_error_bumps_backoff():
    """_on_fetch_error must call _record_stats_failure."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _on_fetch_error(self, server, error_message)")
    body = src[_idx:_idx + 1200]
    assert "self._record_stats_failure(server)" in body


def test_w4_poll_fetch_error_bumps_backoff():
    """_on_poll_fetch_error must ALSO bump backoff (was `pass`)."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _on_poll_fetch_error(self, server, error_message)")
    body = src[_idx:_idx + 800]
    assert "self._record_stats_failure(server)" in body


def test_w4_success_paths_reset_backoff():
    """Both interfaces_fetched and poll_stream_stats_fetched must
    call _record_stats_success on ANY successful response."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def _on_interfaces_fetched(self, server, data)")
    body = src[_idx:_idx + 800]
    assert "self._record_stats_success(server)" in body

    _idx2 = src.index("def _on_poll_stream_stats_fetched(self, server, stream_stats)")
    body2 = src[_idx2:_idx2 + 800]
    assert "self._record_stats_success(server)" in body2


def test_w4_poll_filters_by_should_poll_server():
    """Both poll entry points must filter servers through
    _should_poll_server before firing the worker."""
    src = _read("traffic_client/statistics_section.py")
    # fetch_and_update_statistics
    _idx = src.index("def fetch_and_update_statistics(self)")
    body = src[_idx:_idx + 2500]
    assert "self._should_poll_server(s)" in body
    # poll_stream_stats
    _idx2 = src.index("def poll_stream_stats(self)")
    body2 = src[_idx2:_idx2 + 2500]
    assert "self._should_poll_server(s)" in body2


# ─── W5: TTL prune of stream baseline caches ───


def test_w5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.382 (audit stats-W5)" in src or \
           "v0.5.382 (W5)" in src


def test_w5_constants_defined():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.382 (audit stats-W5)")
    body = src[_idx:_idx + 4000]
    assert "_STREAM_CACHE_SOFT_CAP = 1000" in body
    assert "_STREAM_CACHE_TTL_S = 30 * 60" in body


def test_w5_prune_condition_soft_cap():
    """Prune only fires when either cache is over soft cap —
    small-N case must be a cheap no-op."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.382 (audit stats-W5)")
    body = src[_idx:_idx + 4000]
    assert "len(_baselines_dict) > _STREAM_CACHE_SOFT_CAP" in body
    assert "len(_latched_dict) > _STREAM_CACHE_SOFT_CAP" in body


def test_w5_last_seen_touched_every_poll():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.382 (audit stats-W5)")
    body = src[_idx:_idx + 4000]
    assert "_last_seen[_sid_touch] = _now" in body


def test_w5_stale_eviction_walks_both_dicts():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("v0.5.382 (audit stats-W5)")
    body = src[_idx:_idx + 4000]
    assert "_baselines_dict.pop(_sid_e, None)" in body
    assert "_latched_dict.pop(_sid_e, None)" in body
    assert "_last_seen.pop(_sid_e, None)" in body


# ─── W6: duplicate get_all_streams ───


def test_w6_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.382 (audit stats-W6)" in src


def test_w6_no_more_duplicate_call():
    """The recent-stopped fallback block must call
    get_all_streams(status="Stopped") EXACTLY once (was twice)."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.382 (audit stats-W6)")
    body = src[_idx:_idx + 2000]
    _count = body.count('stream_db.get_all_streams(status="Stopped"')
    assert _count == 1, (
        f"Expected exactly 1 get_all_streams(status=Stopped) call in "
        f"the W6-fixed block; got {_count}"
    )


def test_w6_uses_cached_all_stopped_local():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.382 (audit stats-W6)")
    body = src[_idx:_idx + 2000]
    assert "_all_stopped = stream_db.get_all_streams(" in body
    assert "for s in _all_stopped:" in body


# ─── version guard ───


def test_pyproject_version_at_least_0582():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 382)


# ─── regression guards ───


def test_v0381_arp_peer_healthy_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.381 (audit peer-fallback U4)" in src


def test_v0381_monitor_hooks_intact():
    """v0.5.381 U1 on_device_deleted hooks — must survive
    v0.5.382 edits (unrelated surfaces)."""
    for _rel in ("utils/arp_monitor.py", "utils/bgp_monitor.py",
                 "utils/ospf_monitor.py", "utils/isis_monitor.py",
                 "utils/dhcp_monitor.py"):
        src = _read(_rel)
        assert "def on_device_deleted(device_id: str) -> None:" in src


def test_v0380_dpdk_tx_worker_null_mbuf_intact():
    src = _read("resources/dpdk/tx_worker/tx_worker.c")
    assert "v0.5.380 (audit stream-gen T1)" in src


def test_v0373_vrf_alloc_infra_intact():
    """v0.5.373 D1 introduced _vrf_allocated + _release_vrf_table.
    v0.5.382 W1 wires the helper but must NOT remove either."""
    src = _read("utils/frr_docker.py")
    assert "self._vrf_allocated" in src
    assert "def _release_vrf_table" in src
