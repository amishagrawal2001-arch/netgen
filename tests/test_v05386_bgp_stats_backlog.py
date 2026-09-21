"""v0.5.386 — Backlog sweep: OSPF multi-area, FK, VRF clears, docker reconnect, stats perf.

  B1  Multi-area OSPF pools honor per-area assignment.
  B2  attach_route_pools_to_device uses FK-enforcing conn.
  B3  BGP cleanup clear uses VRF scope + canonical IPv6 form.
  B4  FRR docker client reconnect on dockerd restart.
  B5  update_per_stream_statistics O(N²)→O(N) reverse index.
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


def test_device_database_ast_parses():
    ast.parse(_read("utils/device_database.py"))


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


def test_statistics_section_ast_parses():
    ast.parse(_read("traffic_client/statistics_section.py"))


# ─── B1: multi-area OSPF ───


def test_b1_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.386 (audit OSPF-B1)" in src


def test_b1_per_area_normalisation():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.386 (audit OSPF-B1)")
    body = src[_idx:_idx + 5000]
    # Builds a {area_id: pools} dict from the payload
    assert "_per_area = {}" in body
    assert "for _k, _v in route_pools_per_area.items():" in body
    # Preserves keys verbatim except sentinel "default"
    assert '_target_area = _flat_area_id if _k == "default" else _k' in body


def test_b1_iterates_per_area_on_configure():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.386 (audit OSPF-B1)")
    body = src[_idx:_idx + 5000]
    assert "for _apply_area, _apply_pools in _per_area.items():" in body
    # Thread captures area via default arg (avoids Python closure bug)
    assert "area=_apply_area" in body


# ─── B2: FK-enforcing attach ───


def test_b2_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.386 (audit BGP-B2)" in src


def test_b2_uses_connect_fk_on():
    """attach_route_pools_to_device must go through _connect_fk_on
    so PRAGMA foreign_keys=ON fires."""
    src = _read("utils/device_database.py")
    _idx = src.index("def attach_route_pools_to_device")
    # Scan to the NEXT function so we only look at THIS body
    _end = src.index("\n    def ", _idx + 1)
    body = src[_idx:_end]
    assert "self._connect_fk_on()" in body
    # No more raw sqlite3.connect in this function
    assert "with sqlite3.connect(self.db_path)" not in body


def test_b2_catches_integrity_error():
    """The function must catch IntegrityError separately + log the
    actionable 'save pool first' message."""
    src = _read("utils/device_database.py")
    _idx = src.index("def attach_route_pools_to_device")
    _end = _idx + 3500
    body = src[_idx:_end]
    assert "except sqlite3.IntegrityError as _fk_exc:" in body
    assert "do not exist in route_pools" in body


# ─── B3: BGP clear VRF-scoped ───


def test_b3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.386 (audit BGP-B3)" in src


def test_b3_ipv6_uses_canonical_form():
    """New IPv6 clear must use `clear bgp{vrf} ipv6 unicast <n>
    soft out`, not the old `clear ipv6 bgp <n>`."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.386 (audit BGP-B3)")
    body = src[_idx:_idx + 4000]
    # New canonical form
    assert 'clear bgp{_vrf_route_suffix} ipv6 unicast' in body
    # Old non-standard form should be gone from executable code
    _executable = re.sub(r"#[^\n]*", "", body)
    assert 'clear ipv6 bgp' not in _executable


def test_b3_v4_and_v6_include_vrf_suffix():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.386 (audit BGP-B3)")
    body = src[_idx:_idx + 4000]
    # Both clears must interpolate _vrf_route_suffix
    assert 'clear ip bgp{_vrf_route_suffix} {neighbor_ip} soft out' in body
    assert 'clear bgp{_vrf_route_suffix} ipv6 unicast {neighbor_ip} soft out' in body


# ─── B4: docker client reconnect ───


def test_b4_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.386 (audit FRR-B4)" in src


def test_b4_ensure_client_method_defined():
    src = _read("utils/frr_docker.py")
    assert "def _ensure_client(self):" in src
    _idx = src.index("def _ensure_client(self):")
    body = src[_idx:_idx + 2500]
    # Pings before returning
    assert "self.client.ping()" in body
    # Reconnects on ping failure
    assert "self.client = docker.from_env()" in body
    # Cached ping interval (30s cheap-in-common-case)
    assert "_client_ping_interval" in body


def test_b4_wired_into_start_and_stop():
    """Both start_frr_container + stop_frr_container must call
    self._ensure_client() at the top."""
    src = _read("utils/frr_docker.py")
    # start
    _idx_s = src.index("def start_frr_container(self, device_id: str")
    _body_s = src[_idx_s:_idx_s + 2500]
    assert "self._ensure_client()" in _body_s
    # stop
    _idx_x = src.index("def stop_frr_container(self, device_id: str")
    _body_x = src[_idx_x:_idx_x + 2500]
    assert "self._ensure_client()" in _body_x


# ─── B5: stats O(N²)→O(N) ───


def test_b5_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.386 (audit stats-B5)" in src


def test_b5_reverse_index_built_once():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def update_per_stream_statistics(self, stream_stats):")
    _end = _idx + 3000
    body = src[_idx:_end]
    assert "_stream_id_index = {}" in body
    assert "for _p, _lst in self.streams.items():" in body
    assert "for _s in _lst:" in body
    assert "_stream_id_index[_sid] = (_p, _s)" in body


def test_b5_row_loop_uses_index_lookup():
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def update_per_stream_statistics(self, stream_stats):")
    _end = _idx + 6000
    body = src[_idx:_end]
    assert "_hit = _stream_id_index.get(stream_id_from_table)" in body
    assert "matched_iface = _hit[0]" in body


def test_b5_fallback_to_nested_scan_when_index_empty():
    """When index build fails, the legacy nested-scan path stays
    available (`elif not _stream_id_index:`)."""
    src = _read("traffic_client/statistics_section.py")
    _idx = src.index("def update_per_stream_statistics(self, stream_stats):")
    _end = _idx + 6000
    body = src[_idx:_end]
    assert "elif not _stream_id_index:" in body


# ─── version guard ───


def test_pyproject_version_at_least_0586():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 386)


# ─── regression guards ───


def test_v0385_a1_vrf_probe_timeout_intact():
    src = _read("utils/frr_docker.py")
    assert "v0.5.385 (audit BGP-A1)" in src


def test_v0385_a3_pool_in_use_guard_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A3)" in src


def test_v0384_z1_pl_rm_names_intact():
    src = _read("run_tgen_server.py")
    assert "def _bgp_pl_rm_names(neighbor_ip):" in src


def test_v0384_z3_neighbors_vrf_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit BGP-Z3)" in src


def test_v0350_dhcp_delete_guard_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.350 (audit delete-pool-no-in-use-check)" in src
