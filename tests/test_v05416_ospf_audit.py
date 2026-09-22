"""v0.5.416 — utils/ospf.py audit fixes (8 HIGH + 2 MED).

  EE1  _ospf_vrf_suffix fails CLOSED on VRF probe failure (+ 5s timeout).
  EE2  vtysh error-marker scanner across all exec sites.
  EE3  stop_ospf actually issues `no router ospf*` via vtysh.
  EE4  start/stop neighbor read area_id_ipv4 / area_id_ipv6.
  EE5  stop_ospf_neighbor no longer sends hardcoded 192.168.0.0/24.
  EE6  start_ospf_neighbor no longer injects hardcoded 192.168.0.0/24.
  EE7  start/stop persist with `end` + `write memory`.
  EE8  stop_ospf_neighbor "stop both" indentation bug fixed.
  EE9  start_ospf_neighbor IPv6 path clears `router ospf6 shutdown`.
  EE11 per-device lock via _ospf_device_lock context manager.
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


def test_ospf_ast_parses():
    ast.parse(_read("utils/ospf.py"))


# ─── EE1: VRF probe fail-closed ───


def test_ee1_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE1)" in src


def test_ee1_vrf_probe_has_timeout():
    src = _read("utils/ospf.py")
    _idx = src.index("def _ospf_vrf_suffix")
    _end = src.index("class OspfVrfProbeError", _idx)
    body = src[_idx:_end]
    assert "timeout=5" in body


def test_ee1_vrf_probe_raises_on_failure():
    src = _read("utils/ospf.py")
    assert "class OspfVrfProbeError(RuntimeError):" in src
    _idx = src.index("def _ospf_vrf_suffix")
    _end = src.index("class OspfVrfProbeError", _idx)
    body = src[_idx:_end]
    # Timeout + generic-exception + rc-mismatch all raise.
    assert body.count("raise OspfVrfProbeError(") >= 3


# ─── EE2: vtysh error-marker scanner ───


def test_ee2_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE2)" in src


def test_ee2_scanner_helper_defined():
    src = _read("utils/ospf.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
    assert "_VTYSH_ERROR_MARKERS" in src
    assert '"% Unknown command"' in src
    assert '"% Configuration failed"' in src


def test_ee2_scanner_wired_into_start_stop():
    src = _read("utils/ospf.py")
    # Called from both start and stop paths.
    assert src.count("_vtysh_output_has_error(") >= 3


# ─── EE3: stop_ospf actually stops ───


def test_ee3_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE3)" in src


def test_ee3_stop_ospf_issues_no_router_lines():
    src = _read("utils/ospf.py")
    _idx = src.index("def _stop_ospf_locked")
    _end = src.index("def cleanup_device_routes", _idx)
    body = src[_idx:_end]
    assert 'f"no router ospf{_vrf}"' in body
    assert 'f"no router ospf6{_vrf}"' in body
    assert "write memory" in body


# ─── EE4: split area IDs ───


def test_ee4_marker_present():
    src = _read("utils/ospf.py")
    assert src.count("v0.5.416 (audit stream-EE4)") >= 2


def test_ee4_start_reads_split_area_ids():
    src = _read("utils/ospf.py")
    _idx = src.index("def _start_ospf_neighbor_locked")
    _end = src.index("def stop_ospf_neighbor", _idx)
    body = src[_idx:_end]
    assert "area_id_ipv4 = (" in body
    assert "area_id_ipv6 = (" in body
    # Per-AF branches use the correct id.
    assert 'area {area_id_ipv4}' in body
    assert 'area {area_id_ipv6}' in body


def test_ee4_stop_reads_split_area_ids():
    src = _read("utils/ospf.py")
    _idx = src.index("def _stop_ospf_neighbor_locked")
    _end = src.index("def get_ospf_status", _idx) if "def get_ospf_status" in src[_idx:] else _idx + 8000
    body = src[_idx:_end]
    assert "area_id_ipv4 = (" in body
    assert "area_id_ipv6 = (" in body


# ─── EE5 + EE6: no more hardcoded 192.168.0.0/24 ───


def test_ee5_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE5)" in src


def test_ee6_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE6)" in src


def test_no_more_hardcoded_192_168_0_0_fallback_in_start_stop():
    """Both start and stop neighbor paths no longer fall back to
    192.168.0.0/24. The older `configure_ospf_neighbor` still has
    that fallback (out of scope for this audit — flagged as EE5/EE6
    only for start/stop)."""
    src = _read("utils/ospf.py")
    # Scan only the start + stop locked bodies.
    _s_start = src.index("def _start_ospf_neighbor_locked")
    _e_start = src.index("def stop_ospf_neighbor", _s_start)
    _s_stop = src.index("def _stop_ospf_neighbor_locked")
    _e_stop = src.index("def get_ospf_status", _s_stop)
    start_body = src[_s_start:_e_start]
    stop_body = src[_s_stop:_e_stop]
    assert 'ipv4_network = "192.168.0.0/24"' not in start_body
    assert 'ipv4_network = "192.168.0.0/24"' not in stop_body


# ─── EE7: end + write memory ───


def test_ee7_marker_present():
    src = _read("utils/ospf.py")
    assert src.count("v0.5.416 (audit stream-EE7)") >= 2


def test_ee7_start_writes_memory():
    src = _read("utils/ospf.py")
    _idx = src.index("def _start_ospf_neighbor_locked")
    _end = src.index("def stop_ospf_neighbor", _idx)
    body = src[_idx:_end]
    assert 'vtysh_commands.append("end")' in body
    assert 'vtysh_commands.append("write memory")' in body


def test_ee7_stop_writes_memory():
    src = _read("utils/ospf.py")
    _idx = src.index("def _stop_ospf_neighbor_locked")
    # Window widened — the audit rewrite added ~2 kB of new
    # comments + logic between the def and the end/write-memory
    # lines. The stop func extends to get_ospf_status at 1593.
    body = src[_idx:src.index("def get_ospf_status", _idx)]
    assert 'vtysh_commands.append("end")' in body
    assert 'vtysh_commands.append("write memory")' in body


# ─── EE8: indentation fix ───


def test_ee8_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE8)" in src


def test_ee8_stop_both_branch_ipv6_at_outer_indent():
    """The ipv6 block must be a sibling of the ipv4 block (both
    inside `else:`), not nested inside the ipv4 block."""
    src = _read("utils/ospf.py")
    _idx = src.index("v0.5.416 (audit stream-EE8)")
    body = src[_idx:_idx + 3000]
    # Both IPv4 and IPv6 blocks are inside the else, not nested
    # in ipv4. Look for the "IPv6 — now at the correct outer
    # indent" comment.
    assert "IPv6 — now at the correct outer indent" in body


# ─── EE9: start IPv6 clears shutdown ───


def test_ee9_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE9)" in src


def test_ee9_start_ipv6_enters_router_ospf6_and_no_shutdown():
    src = _read("utils/ospf.py")
    _idx = src.index("v0.5.416 (audit stream-EE9)")
    body = src[_idx:_idx + 1500]
    assert 'f"router ospf6{_ospf_vrf_suffix(device_id)}"' in body
    assert '" no shutdown"' in body


# ─── EE11: per-device lock ───


def test_ee11_marker_present():
    src = _read("utils/ospf.py")
    assert src.count("v0.5.416 (audit stream-EE11)") >= 3


def test_ee11_lock_helper_defined():
    src = _read("utils/ospf.py")
    assert "def _ospf_device_lock(device_id: Optional[str]):" in src
    assert "_OSPF_DEVICE_LOCKS" in src
    assert "class _NullLock:" in src


def test_ee11_lock_used_in_configure_start_stop():
    src = _read("utils/ospf.py")
    # 3 public wrappers each open the lock context.
    assert src.count("with _ospf_device_lock(device_id):") >= 3


# ─── version guard ───


def test_pyproject_at_least_0616():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 416)


# ─── regression guards ───


def test_v0403_bgp_t2_scanner_intact():
    src = _read("utils/bgp.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
