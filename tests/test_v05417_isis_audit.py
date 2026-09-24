"""v0.5.417 — utils/isis.py audit fixes (9 HIGH).

  FF1  hello_interval / hello_multiplier / metric now emitted to vtysh
       via _isis_interface_timer_lines (per-interface).
  FF2  _isis_vrf_suffix fails CLOSED on VRF probe failure (+ 5s timeout)
       via IsisVrfProbeError.
  FF3  vtysh error-marker scanner across configure/start/stop paths.
  FF4  stop_isis_neighbor actually issues `no router isis CORE{vrf}`
       and persists with `write memory` (was silent no-op on router).
  FF5  Hardcoded `49.0001.0000.0000.0001.00` NET fallback removed from
       all 4 sites (configure, start, stop, and the start-time
       `no net …` clean-up); now surfaced through _resolve_isis_net
       which raises IsisNetError instead.
  FF6  start path no longer unconditionally sends the hardcoded NET
       clean-up line; NET comes from validated operator input only.
  FF7  Hardcoded `vlan20` interface fallback removed from start + stop.
  FF8  Per-device lock via _isis_device_lock context manager, mirroring
       v0.5.383 X1 (BGP) and v0.5.416 EE11 (OSPF).
  FF9  NET address validated via utils.isis_net.validate_isis_net at
       the top of configure and start; malformed NETs are rejected
       before we touch vtysh.
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


def test_isis_ast_parses():
    ast.parse(_read("utils/isis.py"))


# ─── FF1: per-interface timers + metric wired into vtysh ───


def test_ff1_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF1)" in src


def test_ff1_helper_defined_and_emits_all_three_lines():
    src = _read("utils/isis.py")
    assert "def _isis_interface_timer_lines(" in src
    # The three per-interface commands the operator's UI collects.
    assert '" isis hello-interval {' in src
    assert '" isis hello-multiplier {' in src
    assert '" isis metric {' in src


def test_ff1_timer_helper_called_from_configure_and_start():
    src = _read("utils/isis.py")
    # configure + start both feed the interface block through the
    # helper so the same three commands hit vtysh on Apply and on
    # Start.
    assert src.count("_isis_interface_timer_lines(isis_config)") >= 2


# ─── FF2: VRF probe fail-closed ───


def test_ff2_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF2)" in src


def test_ff2_vrf_probe_has_timeout():
    src = _read("utils/isis.py")
    _idx = src.index("def _isis_vrf_suffix")
    _end = src.index("class IsisNetError", _idx) if "class IsisNetError" in src[_idx:] else len(src)
    body = src[_idx:_end]
    assert "timeout=5" in body


def test_ff2_vrf_probe_raises_on_failure():
    src = _read("utils/isis.py")
    assert "class IsisVrfProbeError(RuntimeError):" in src
    _idx = src.index("def _isis_vrf_suffix")
    _end = src.index("class IsisNetError", _idx) if "class IsisNetError" in src[_idx:] else len(src)
    body = src[_idx:_end]
    # Timeout + generic-exception + rc-mismatch all raise.
    assert body.count("raise IsisVrfProbeError(") >= 3


def test_ff2_probe_wired_into_configure_start_stop():
    src = _read("utils/isis.py")
    # Each of the three locked bodies catches the probe failure and
    # returns False, rather than silently falling back.
    assert src.count("except IsisVrfProbeError as _vrf_exc:") >= 3


# ─── FF3: vtysh error-marker scanner ───


def test_ff3_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF3)" in src


def test_ff3_scanner_helper_defined():
    src = _read("utils/isis.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
    assert "_VTYSH_ERROR_MARKERS" in src
    assert '"% Unknown command"' in src
    assert '"% Malformed"' in src


def test_ff3_scanner_wired_into_all_three_paths():
    src = _read("utils/isis.py")
    # Configure + start + stop all check the output for error
    # markers before reporting success.
    assert src.count("_vtysh_output_has_error(") >= 4


# ─── FF4: stop actually stops ───


def test_ff4_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF4)" in src


def test_ff4_stop_issues_no_router_isis_and_write_memory():
    src = _read("utils/isis.py")
    _idx = src.index("def _stop_isis_neighbor_locked")
    _end = src.index("def get_isis_neighbor_uptime", _idx) if "def get_isis_neighbor_uptime" in src[_idx:] else len(src)
    body = src[_idx:_end]
    assert 'f"no router isis CORE{_vrf_suffix}"' in body
    assert '"write memory"' in body


# ─── FF5 + FF9: hardcoded NET fallback removed everywhere ───


def test_ff5_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF5" in src


def _strip_comments(src: str) -> str:
    """Drop `#` comment lines so audit checks can tell explanatory
    comments (which may reference the pre-fix literal) apart from
    actual live code."""
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


def test_ff5_no_more_hardcoded_net_in_locked_bodies():
    """The three locked bodies must not fall back to the shared
    `49.0001.0000.0000.0001.00` default in live code. Comments
    referencing the pre-fix literal are fine (see FF5 explanations);
    the risk was `area_id = ... or "49.0001…"` assignments and the
    `"no net 49.0001…"` vtysh line."""
    src = _read("utils/isis.py")

    _c_start = src.index("def _configure_isis_neighbor_locked")
    _c_end = src.index("def start_isis_neighbor", _c_start)
    configure_body = _strip_comments(src[_c_start:_c_end])

    _s_start = src.index("def _start_isis_neighbor_locked")
    _s_end = src.index("def stop_isis_neighbor", _s_start)
    start_body = _strip_comments(src[_s_start:_s_end])

    _sp_start = src.index("def _stop_isis_neighbor_locked")
    _sp_end = src.index("def get_isis_neighbor_uptime", _sp_start) if "def get_isis_neighbor_uptime" in src[_sp_start:] else len(src)
    stop_body = _strip_comments(src[_sp_start:_sp_end])

    _hardcoded = "49.0001.0000.0000.0001.00"
    assert _hardcoded not in configure_body
    assert _hardcoded not in start_body
    assert _hardcoded not in stop_body


def test_ff5_resolve_net_helper_defined():
    src = _read("utils/isis.py")
    assert "def _resolve_isis_net(area_id: Any" in src
    assert "class IsisNetError" in src


def test_ff9_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF9)" in src or "FF9" in src


def test_ff9_calls_validate_isis_net():
    src = _read("utils/isis.py")
    # The validator from utils/isis_net.py is imported and used
    # inside the resolve helper.
    assert "from utils.isis_net import validate_isis_net" in src
    assert "validate_isis_net(_raw)" in src


def test_ff5_configure_and_start_call_resolve_helper():
    src = _read("utils/isis.py")
    # Both configure + start invoke _resolve_isis_net so a missing
    # or malformed operator NET fails loud instead of silently.
    assert src.count("_resolve_isis_net(") >= 2


# ─── FF6: start no longer sends `no net 49.0001…` ───


def test_ff6_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF6)" in src


def test_ff6_start_body_has_no_hardcoded_no_net():
    src = _read("utils/isis.py")
    _s_start = src.index("def _start_isis_neighbor_locked")
    _s_end = src.index("def stop_isis_neighbor", _s_start)
    body = _strip_comments(src[_s_start:_s_end])
    # The pre-fix line was: "no net 49.0001.0000.0000.0001.00"
    # Comments referring back to it in the FF6 explanation are fine.
    assert '"no net 49.0001' not in body


# ─── FF7: no more hardcoded vlan20 ───


def test_ff7_marker_present():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF7)" in src


def test_ff7_no_hardcoded_vlan20_in_start_or_stop_bodies():
    src = _read("utils/isis.py")

    _s_start = src.index("def _start_isis_neighbor_locked")
    _s_end = src.index("def stop_isis_neighbor", _s_start)
    start_body = src[_s_start:_s_end]

    _sp_start = src.index("def _stop_isis_neighbor_locked")
    _sp_end = src.index("def get_isis_neighbor_uptime", _sp_start) if "def get_isis_neighbor_uptime" in src[_sp_start:] else len(src)
    stop_body = src[_sp_start:_sp_end]

    # No literal "vlan20" as a hard-coded fallback in either body.
    # The device may still land on a vlan-numbered iface derived
    # from device_data — that path is fine; we only forbid the
    # bare literal.
    assert '"vlan20"' not in start_body
    assert '"vlan20"' not in stop_body


# ─── FF8: per-device lock ───


def test_ff8_marker_present():
    src = _read("utils/isis.py")
    assert src.count("v0.5.417 (audit stream-FF8)") >= 3


def test_ff8_lock_helper_defined():
    src = _read("utils/isis.py")
    assert "def _isis_device_lock(device_id: Optional[str]):" in src
    assert "_ISIS_DEVICE_LOCKS" in src
    assert "class _NullLock:" in src


def test_ff8_lock_used_in_all_three_public_wrappers():
    src = _read("utils/isis.py")
    # Three public wrappers each open the lock context.
    assert src.count("with _isis_device_lock(device_id):") >= 3


# ─── Helper unit tests ───


def test_resolve_isis_net_rejects_empty():
    """v0.5.417 FF5 — no hardcoded fallback for empty NET."""
    from utils.isis import _resolve_isis_net, IsisNetError
    for _bad in ("", "   ", None):
        try:
            _resolve_isis_net(_bad, device_id="dev1")
        except IsisNetError:
            continue
        raise AssertionError(f"expected IsisNetError for {_bad!r}")


def test_resolve_isis_net_rejects_malformed():
    """v0.5.417 FF9 — validator rejects malformed NET."""
    from utils.isis import _resolve_isis_net, IsisNetError
    # Non-hex char inside the hex part
    try:
        _resolve_isis_net("49.0001.zzzz.0000.0001.00", device_id="dev1")
    except IsisNetError:
        pass
    else:
        raise AssertionError("expected IsisNetError for non-hex NET")


def test_resolve_isis_net_accepts_valid():
    """v0.5.417 FF9 — validator accepts a proper 10-byte NET."""
    from utils.isis import _resolve_isis_net
    got = _resolve_isis_net("49.0001.0000.0000.0002.00", device_id="dev1")
    assert got == "49.0001.0000.0000.0002.00"


def test_timer_lines_all_three_present_when_all_set():
    from utils.isis import _isis_interface_timer_lines
    lines = _isis_interface_timer_lines({
        "hello_interval": "5",
        "hello_multiplier": "4",
        "metric": "12",
    })
    assert " isis hello-interval 5" in lines
    assert " isis hello-multiplier 4" in lines
    assert " isis metric 12" in lines


def test_timer_lines_skip_blanks():
    from utils.isis import _isis_interface_timer_lines
    lines = _isis_interface_timer_lines({
        "hello_interval": "",
        "hello_multiplier": None,
        "metric": "  ",
    })
    assert lines == []


def test_isis_device_lock_is_context_manager_for_missing_device():
    """No device_id → dummy no-op lock that still supports `with`."""
    from utils.isis import _isis_device_lock
    with _isis_device_lock(None):
        pass


def test_isis_device_lock_returns_same_lock_per_device():
    """Same device_id → same Lock instance so nested calls actually
    serialize."""
    from utils.isis import _isis_device_lock
    a = _isis_device_lock("dev-A")
    b = _isis_device_lock("dev-A")
    c = _isis_device_lock("dev-B")
    assert a is b
    assert a is not c


# ─── version guard ───


def test_pyproject_at_least_0617():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 417)


# ─── regression guards ───


def test_v0416_ospf_ee1_intact():
    src = _read("utils/ospf.py")
    assert "v0.5.416 (audit stream-EE1)" in src


def test_v0403_bgp_t2_scanner_intact():
    src = _read("utils/bgp.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
