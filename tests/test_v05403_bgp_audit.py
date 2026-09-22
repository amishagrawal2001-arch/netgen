"""v0.5.403 — utils/bgp.py protocol config audit (5 items).

  T1  _resolve_bgp_context fail-closed on VRF probe failure.
  T2  _exec_vtysh_lines + execute_vtysh_command parse `%` error markers.
  T3  stop_bgp actually issues `no router bgp` via vtysh.
  T4  advertise_bgp_routes route-map name uses counter + uuid suffix.
  T5  cleanup_device_routes enumerates real route-maps (no vtysh glob).
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


def _strip_comments(src: str) -> str:
    _no_triple = re.sub(r'"""[\s\S]*?"""', '', src)
    _no_triple = re.sub(r"'''[\s\S]*?'''", '', _no_triple)
    return "\n".join(
        _line for _line in _no_triple.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_bgp_ast_parses():
    ast.parse(_read("utils/bgp.py"))


# ─── T1: VRF probe fail-closed ───


def test_t1_marker_present():
    src = _read("utils/bgp.py")
    assert "v0.5.403 (audit bgp T1)" in src


def test_t1_no_silent_downgrade_to_non_vrf():
    src = _read("utils/bgp.py")
    _idx = src.index("def _resolve_bgp_context")
    _end = src.index("def _exec_vtysh_lines", _idx)
    body = src[_idx:_end]
    # Wraps subprocess in try/except (was bare before)
    assert "except Exception as _probe_exc:" in body
    # Returns an error string when the probe fails
    assert "Refusing to silently downgrade" in body
    # The router_clause is set to vrf form ONLY when probe succeeds
    assert 'router_clause = f"router bgp {asn} vrf {vrf_name}"' in body


# ─── T2: vtysh error-marker parsing ───


def test_t2_marker_present():
    src = _read("utils/bgp.py")
    assert "v0.5.403 (audit bgp T2)" in src


def test_t2_vtysh_output_helper_defined():
    src = _read("utils/bgp.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
    assert "_VTYSH_ERROR_MARKERS" in src
    # Key markers listed
    _idx = src.index("_VTYSH_ERROR_MARKERS")
    body = src[_idx:_idx + 1500]
    for _mark in (
        "% Unknown command",
        "% Malformed",
        "% Same as remote-as",
        "% Cannot",
        "% Failed",
    ):
        assert _mark in body


def test_t2_exec_vtysh_lines_uses_scanner():
    src = _read("utils/bgp.py")
    _idx = src.index("def _exec_vtysh_lines")
    _end = src.index("def advertise_bgp_routes", _idx) if "def advertise_bgp_routes" in src[_idx:] else _idx + 2500
    body = src[_idx:_end]
    assert "_vtysh_output_has_error(output)" in body


def test_t2_execute_vtysh_command_uses_scanner():
    src = _read("utils/bgp.py")
    # execute_vtysh_command lives higher in the file — grab its body
    _idx = src.index("def execute_vtysh_command")
    _end = src.index("def safe_vtysh_command", _idx)
    body = src[_idx:_end]
    assert "_vtysh_output_has_error(output_str)" in body


# ─── T3: stop_bgp real teardown ───


def test_t3_marker_present():
    src = _read("utils/bgp.py")
    assert "v0.5.403 (audit bgp T3)" in src


def test_t3_stop_bgp_issues_vtysh_teardown():
    src = _read("utils/bgp.py")
    _idx = src.index("def stop_bgp(")
    _end = src.index("def cleanup_device_routes", _idx)
    body = src[_idx:_end]
    # Resolves context, issues `no <router_clause>`, uses _exec_vtysh_lines
    assert "_resolve_bgp_context(device_id)" in body
    assert 'f"no {router_clause}"' in body
    assert "_exec_vtysh_lines(container, _teardown_lines)" in body


# ─── T4: route-map name uniqueness ───


def test_t4_marker_present():
    src = _read("utils/bgp.py")
    assert "v0.5.403 (audit bgp T4)" in src


def test_t4_route_map_name_uses_counter_and_uuid():
    src = _read("utils/bgp.py")
    # Counter helper defined
    assert "def _next_route_map_counter() -> int:" in src
    # Uses itertools.count + threading.Lock
    _idx = src.index("_ROUTE_MAP_COUNTER = _itertools.count(1)")
    body = src[_idx:_idx + 800]
    assert "_ROUTE_MAP_COUNTER_LOCK = _threading.Lock()" in body

    # advertise_bgp_routes uses the counter + uuid suffix
    _idx2 = src.index("def advertise_bgp_routes")
    _end2 = src.index("def withdraw_bgp_routes", _idx2)
    body2 = src[_idx2:_end2]
    assert "_next_route_map_counter()" in body2
    assert "uuid.uuid4().hex[:6]" in body2


def test_t4_runtime_route_map_counter_monotonic():
    from utils.bgp import _next_route_map_counter
    _seen = {_next_route_map_counter() for _ in range(50)}
    # All 50 values must be distinct
    assert len(_seen) == 50


# ─── T5: route-map cleanup enumerate ───


def test_t5_marker_present():
    src = _read("utils/bgp.py")
    assert "v0.5.403 (audit bgp T5)" in src


def test_t5_cleanup_no_vtysh_glob():
    """The pre-fix `no route-map RM_<devid>_*` glob syntax is gone.
    New code enumerates via `show running-config | include ^route-map RM_...`."""
    src = _read("utils/bgp.py")
    _idx = src.index("v0.5.403 (audit bgp T5)")
    body = src[_idx:_idx + 3500]
    # No glob-style teardown line
    _stripped = _strip_comments(body)
    assert "no route-map RM_" not in _stripped or "no route-map {_n}" in _stripped
    # Enumerate via show running-config | include
    assert "show running-config | include ^route-map" in body


# ─── version guard ───


def test_pyproject_at_least_0603():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 403)


# ─── regression guards ───


def test_v0402_quick_get_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.402 (audit startup S1" in src


def test_v0401_r4_cpu_vendor_guard_intact():
    src = _read("traffic_client/dpdk_menu_actions.py")
    assert "v0.5.401 (audit menu R4)" in src
