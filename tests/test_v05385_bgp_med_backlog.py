"""v0.5.385 — BGP audit MED backlog (5 items).

  A1  VRF probe timeout + no silent default-VRF fallback.
  A2  save_bgp_route_pools_batch preserves increment_type.
  A3  DELETE /api/bgp/pools/<name> in-use guard.
  A4  _add_bgp_route VRF-scoped ip route.
  A5  Cleanup handles dual-stack + comma-list neighbors.
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


# ─── A1: VRF probe timeout + defensive skip ───


def test_a1_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.385 (audit BGP-A1)" in src


def test_a1_probe_has_timeout():
    """subprocess.run for the VRF probe must pass timeout=2."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("v0.5.385 (audit BGP-A1)")
    body = src[_idx:_idx + 4000]
    # subprocess.run with timeout=2
    assert re.search(
        r'subprocess\.run\(\s*\[\s*"ip"\s*,\s*"-o"\s*,\s*"link"\s*,'
        r'\s*"show"\s*,\s*vrf_name\s*\][^)]*timeout\s*=\s*2',
        body,
        re.DOTALL,
    )


def test_a1_probe_failure_returns_false_not_default_vrf():
    """On timeout/exception, must `return False` — NOT fall
    through to `router bgp <asn>` (default VRF)."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("v0.5.385 (audit BGP-A1)")
    body = src[_idx:_idx + 4000]
    assert "TimeoutExpired" in body
    assert "_probe_failed = True" in body
    assert "if _probe_failed:" in body
    assert "return False" in body


def test_a1_stderr_distinguishes_absence_from_failure():
    """Nonzero returncode with 'does not exist' / 'cannot find' in
    stderr = legitimate absence (vrf_exists stays False, no skip)."""
    src = _read("utils/frr_docker.py")
    _idx = src.index("v0.5.385 (audit BGP-A1)")
    body = src[_idx:_idx + 4000]
    assert "does not exist" in body
    assert "cannot find" in body


# ─── A2: increment_type on batch save ───


def test_a2_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A2)" in src


def test_a2_validated_pool_includes_increment_type():
    src = _read("run_tgen_server.py")
    _idx = src.index("def save_bgp_route_pools_batch")
    _end = _idx + 3500
    body = src[_idx:_end]
    assert '"increment_type": pool.get("increment_type", "host")' in body
    # Also address_family for import fidelity
    assert '"address_family": pool.get("address_family", "ipv4")' in body


# ─── A3: DELETE pool in-use check ───


def test_a3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A3)" in src


def test_a3_query_device_route_pools_before_delete():
    """The DELETE endpoint must query device_route_pools for any
    matching attachment BEFORE calling remove_route_pool."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def delete_bgp_route_pool(pool_name):")
    _end = _idx + 3500
    body = src[_idx:_end]
    assert "SELECT device_id, neighbor_ip FROM device_route_pools" in body
    assert "WHERE pool_name = ?" in body
    # In-use → 409
    assert "), 409" in body
    # ?force=true bypass
    assert '_force = str(request.args.get("force", "")).lower()' in body


def test_a3_force_param_bypasses_check():
    src = _read("run_tgen_server.py")
    _idx = src.index("def delete_bgp_route_pool(pool_name):")
    _end = _idx + 3500
    body = src[_idx:_end]
    # force=true skips the guard block
    assert "if not _force:" in body


# ─── A4: _add_bgp_route VRF suffix ───


def test_a4_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A4)" in src


def test_a4_probes_vrf_and_appends_suffix():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.385 (audit BGP-A4)")
    body = src[_idx:_idx + 3000]
    assert "_fm.vrf_name_for_device(device_id)" in body
    # Same timeout as A1
    assert "timeout=2" in body
    # Suffix appended to the route command
    assert "_vrf_route_suffix" in body
    assert "ip route 0.0.0.0/0 {gateway}{_vrf_route_suffix}" in body


def test_a4_uses_singleton_frr_manager():
    """Should use the frr_manager singleton (v0.5.380 M2 pattern),
    not FRRDockerManager() per call."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.385 (audit BGP-A4)")
    body = src[_idx:_idx + 3000]
    assert "from utils.frr_docker import frr_manager as _fm" in body
    assert "FRRDockerManager()" not in body


# ─── A5: dual-stack + comma-list cleanup ───


def test_a5_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.385 (audit BGP-A5)" in src


def test_a5_split_helper_defined():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.385 (audit BGP-A5)")
    body = src[_idx:_idx + 4000]
    assert "def _split_neighbor_field(raw):" in body
    # Splits on comma and semicolon
    assert 're.split(r"[,;]"' in body


def test_a5_collects_all_neighbors():
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.385 (audit BGP-A5)")
    body = src[_idx:_idx + 4000]
    assert "_v4_neighbors = _split_neighbor_field(bgp_config.get" in body
    assert "_v6_neighbors = _split_neighbor_field(bgp_config.get" in body
    assert "all_configured_neighbors = _v4_neighbors + _v6_neighbors" in body


def test_a5_cleanup_loops_all_neighbors():
    """The cleanup branch (no route_pools_per_neighbor) must loop
    all_configured_neighbors, not just the first-picked one."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.385 (audit BGP-A5)")
    body = src[_idx:_idx + 5000]
    assert "for _n in all_configured_neighbors:" in body
    assert "device_db.remove_device_route_pools(device_id, _n)" in body


def test_a5_split_helper_behavioral():
    """Behavioral: split_helper handles v4, v6, comma+space, empty."""
    _src = _read("run_tgen_server.py")
    _idx = _src.index("def _split_neighbor_field(raw):")
    _end = _src.index("_v4_neighbors = ", _idx)
    _snippet = "import re\n" + _src[_idx:_end]
    _ns = {}
    exec(_snippet, _ns)
    _split = _ns["_split_neighbor_field"]
    assert _split("10.0.0.1") == ["10.0.0.1"]
    assert _split("10.0.0.1, 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]
    assert _split("2001::1;2001::2") == ["2001::1", "2001::2"]
    assert _split("") == []
    assert _split(None) == []
    assert _split("  10.0.0.1  ") == ["10.0.0.1"]


# ─── version guard ───


def test_pyproject_version_at_least_0585():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 385)


# ─── regression guards ───


def test_v0384_z1_pl_rm_names_intact():
    src = _read("run_tgen_server.py")
    assert "def _bgp_pl_rm_names(neighbor_ip):" in src


def test_v0384_z2_device_config_lock_intact():
    src = _read("run_tgen_server.py")
    assert "def _bgp_device_config_lock(device_id):" in src


def test_v0384_z3_bgp_neighbors_vrf_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit BGP-Z3)" in src


def test_v0384_z4_ospf_partial_apply_clamp_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit OSPF-Z4)" in src


def test_v0350_dhcp_delete_guard_intact():
    """A3 mirrors the v0.5.350 DHCP guard pattern — the DHCP one
    must still be present."""
    src = _read("run_tgen_server.py")
    assert "v0.5.350 (audit delete-pool-no-in-use-check)" in src
