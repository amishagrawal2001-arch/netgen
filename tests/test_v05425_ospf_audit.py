"""v0.5.425 — utils/ospf.py audit (MM1 + MM2 + MM4) + remove-selected
devices UserRole fast-path (MM5).

Context: today's user-reported "Clear-All leaves OSPF chip stuck
green" was fixed CLIENT-SIDE in v0.5.424 (LL1+LL2). This slice
closes the loop SERVER-SIDE: the `cleanup_device_routes` +
`remove_ospf_config` + `cleanup_all_ospf_routes` functions were
dead stubs that only did `del OSPF_INSTANCES[device_id]` and
returned True — the FRR container's running-config still carried
`router ospf vrf X`, `router ospf6 vrf X`, interface-level `ip
ospf area`, graceful-restart, redistribute, etc. On container
restart or `device_id` reuse OSPF came back up with the stale
config.

  MM1  `_full_ospf_teardown` helper that delegates to
       `_stop_ospf_locked` (router-level `no router ospf{vrf}` +
       `no router ospf6{vrf}` + `write memory` under per-device
       lock) plus a second vtysh pass for interface-level
       `no ip/ipv6 ospf area <id>`. `cleanup_device_routes`,
       `remove_ospf_config`, and `cleanup_all_ospf_routes` all
       route through it. `cleanup_all_*` iterates every tracked
       device before clearing the registry.

  MM2  `_configure_ospf_neighbor_locked`'s success branch now
       scans vtysh output for `%` error markers via
       `_vtysh_output_has_error`. Pre-fix a vtysh `% Unknown
       command` on exit 0 (typoed area_id, never-created
       interface, missing daemon) wrote
       `OSPF_INSTANCES[device_id]["active"] = True` and the UI
       showed "✅ configured" while nothing had actually been
       applied — same K5 lie as v0.5.403 BGP T2 pre-fix.

  MM4  Deleted legacy host-side helpers `start_ospf`,
       `build_ospf_cmd`, `build_ospf_stop_cmd`. Pre-fix their
       argv construction `['vtysh'] + [f'-c "{line}"' for line
       in cmds]` baked LITERAL double-quotes into each argv
       element — fed to `subprocess.run(argv, shell=False)` by
       `device_manager._run`, vtysh received `-c "configure
       terminal"` as one argument and misinterpreted it. Also
       `build_ospf_cmd` emitted `network 0.0.0.0/0 area X` (VRF-
       wide catch-all) and `build_ospf_stop_cmd` emitted
       `no router ospf{vrf}` (nukes the entire OSPF process).
       Dead paths now; `start_device_protocols` /
       `stop_device_protocols` OSPF branches are logged no-ops
       — real start/stop flows via `/api/device/ospf/{start,
       stop}` → `ospf.start_ospf_neighbor` /
       `ospf.stop_ospf_neighbor`.

  MM5  `widgets/devices_tab.py:9778` `remove_selected_devices`
       UserRole fast-path: pre-fix iterated `getattr(main_window,
       "all_devices", [])` as if it were a list — but
       `all_devices` is a dict (iface → [devices]) and iterating
       a dict yields its KEYS (iface label strings), so
       `isinstance(_d, dict)` was always False and the fast-path
       was dead code. Comment at :9759-9763 states the stash
       exists so table rebuilds don't misdirect the delete — but
       the stash was never consulted. Walk `.values()` + nested
       buckets.
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
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_ospf_py_ast_parses():
    ast.parse(_read("utils/ospf.py"))


def test_device_manager_ast_parses():
    ast.parse(_read("utils/device_manager.py"))


def test_devices_tab_ast_parses():
    ast.parse(_read("widgets/devices_tab.py"))


# ─── MM1: cleanup funcs no longer stubs ───


def test_mm1_marker_present():
    src = _read("utils/ospf.py")
    assert "v0.5.425 (audit stream-MM1)" in src


def test_mm1_full_ospf_teardown_defined():
    """A shared `_full_ospf_teardown(device_id)` helper must exist
    so cleanup_device_routes / remove_ospf_config / cleanup_all_
    ospf_routes converge on one real impl."""
    src = _read("utils/ospf.py")
    assert "def _full_ospf_teardown(device_id):" in src


def test_mm1_full_ospf_teardown_uses_stop_ospf_locked():
    """The helper must delegate to the already-robust
    `_stop_ospf_locked` for router-level cleanup, under the
    per-device lock."""
    src = _read("utils/ospf.py")
    _idx = src.index("def _full_ospf_teardown(device_id):")
    _end = src.index("def cleanup_device_routes", _idx)
    body = src[_idx:_end]
    assert "_ospf_device_lock(device_id)" in body
    assert "_stop_ospf_locked(device_id, _iface)" in body


def test_mm1_full_ospf_teardown_issues_interface_level_cleanup():
    """The helper must additionally emit per-interface
    `no ip ospf area` / `no ipv6 ospf6 area` since
    `_stop_ospf_locked` only drops the router-level config."""
    src = _read("utils/ospf.py")
    _idx = src.index("def _full_ospf_teardown(device_id):")
    _end = src.index("def cleanup_device_routes", _idx)
    body = src[_idx:_end]
    assert "no ip ospf area" in body
    assert "no ipv6 ospf6 area" in body
    assert "interface {_iface}" in body


def test_mm1_cleanup_device_routes_delegates():
    """`cleanup_device_routes` must call `_full_ospf_teardown` —
    pre-fix it only did `del OSPF_INSTANCES[device_id]`."""
    src = _read("utils/ospf.py")
    _idx = src.index("def cleanup_device_routes(device_id):")
    _end = src.index("def remove_ospf_config", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    assert "_full_ospf_teardown(device_id)" in _live
    # The pre-fix shape (just OSPF_INSTANCES mutation + return) is
    # gone from live code.
    assert "del OSPF_INSTANCES[device_id]" not in _live


def test_mm1_remove_ospf_config_delegates():
    """`remove_ospf_config` same shape."""
    src = _read("utils/ospf.py")
    _idx = src.index("def remove_ospf_config(device_id):")
    _end = src.index("def cleanup_all_ospf_routes", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    assert "_full_ospf_teardown(device_id)" in _live
    assert "del OSPF_INSTANCES[device_id]" not in _live


def test_mm1_cleanup_all_ospf_routes_iterates_before_clear():
    """`cleanup_all_ospf_routes` must iterate OSPF_INSTANCES
    device-by-device through `_full_ospf_teardown` BEFORE
    clearing the dict. Pre-fix it only did `.clear()` and left
    every FRR container running OSPF."""
    src = _read("utils/ospf.py")
    _idx = src.index("def cleanup_all_ospf_routes():")
    body = src[_idx:_idx + 2000]
    # Strip the docstring so sentinels inside it don't collide
    # with real code positions.
    _no_doc = _strip_comments(body)
    # Split at triple-quote blocks to drop the docstring body.
    _parts = _no_doc.split('"""')
    _code = _parts[0] + "".join(_parts[2:]) if len(_parts) >= 3 else _no_doc
    assert "_full_ospf_teardown(_did)" in _code
    assert "OSPF_INSTANCES.clear()" in _code
    assert _code.index("_full_ospf_teardown(_did)") < _code.index("OSPF_INSTANCES.clear()")


# ─── MM2: configure path error-marker scan ───


def test_mm2_marker_present():
    assert "v0.5.425 (audit stream-MM2)" in _read("utils/ospf.py")


def test_mm2_configure_path_scans_output_markers():
    """The exit-0 success branch of the configure path must call
    `_vtysh_output_has_error(output_str)` and return False on
    match — parity with the start/stop paths which already do."""
    src = _read("utils/ospf.py")
    _idx = src.index("def _configure_ospf_neighbor_locked")
    # Pick a tight sentinel; the OSPF_INSTANCES write happens
    # immediately after the success-marker scan.
    _end = src.index("# Store OSPF instance", _idx)
    body = src[_idx:_end]
    assert "_vtysh_output_has_error(output_str)" in body
    # And the error branch returns False (not just logs).
    _idx2 = body.index("_vtysh_output_has_error(output_str)")
    assert "return False" in body[_idx2:_idx2 + 400]


# ─── MM4: legacy host-side helpers deleted ───


def test_mm4_marker_present():
    assert "v0.5.425 (audit stream-MM4)" in _read("utils/ospf.py")


def test_mm4_legacy_helpers_removed_from_live_code():
    """`build_ospf_cmd`, `build_ospf_stop_cmd`, and the broken
    `start_ospf` host-side placeholder must be gone from live
    code (comments may still reference them in the MM4 rationale
    block)."""
    src = _read("utils/ospf.py")
    _live = _strip_comments(src)
    assert "def build_ospf_cmd(" not in _live
    assert "def build_ospf_stop_cmd(" not in _live
    # The real `stop_ospf` (v0.5.416 EE3) stays — it's the modern
    # per-device-lock delegator. The legacy host-side `start_ospf`
    # placeholder must be gone.
    assert "def start_ospf(device_id, iface, config):" not in _live


def test_mm4_broken_argv_construction_gone():
    """The argv-with-literal-quotes pattern is gone from live
    code (string may remain in comments)."""
    src = _read("utils/ospf.py")
    _live = _strip_comments(src)
    assert "['vtysh'] + [f'-c " not in _live
    assert "'vtysh'] + [f'-c " not in _live


def test_mm4_device_manager_start_stop_ospf_are_logged_noops():
    """`start_device_protocols` + `stop_device_protocols` OSPF
    branches must be logged no-ops referencing the real
    start/stop paths; they must NOT call the deleted helpers."""
    src = _read("utils/device_manager.py")
    _live = _strip_comments(src)
    assert "ospf.build_ospf_cmd" not in _live
    assert "ospf.build_ospf_stop_cmd" not in _live
    # The replacement log lines are present.
    assert "legacy no-op" in src
    assert "start_ospf_neighbor" in src
    assert "stop_ospf_neighbor" in src


# ─── MM5: remove-selected UserRole walks dict buckets ───


def test_mm5_marker_present():
    assert "v0.5.425 (audit stream-MM5)" in _read("widgets/devices_tab.py")


def test_mm5_remove_selected_walks_dict_buckets():
    """The UserRole device_id fast-path in `remove_selected_device`
    must walk `all_devices.values()` + nested buckets. Pre-fix
    iterated the dict directly (yielding keys) with an
    `isinstance(_d, dict)` guard that was always False — dead
    code since the stash was introduced."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def remove_selected_device(")
    # Scan forward to the next method boundary.
    _after = src[_idx:]
    _end = _after.find("\n    def ", 100)
    body = _after[: _end if _end > 0 else 20000]
    assert 'getattr(self.main_window, "all_devices", {}) or {}' in body
    assert "for _bucket in _cache.values():" in body
    assert "isinstance(_bucket, list)" in body
    _live = _strip_comments(body)
    # Pre-fix pattern is gone from live code.
    assert 'getattr(self.main_window, "all_devices", []) or []' not in _live


# ─── Version guard ───


def test_pyproject_at_least_0625():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 425)


# ─── regression guards ───


def test_v0424_ll1_prune_dict_check_intact():
    src = _read("widgets/devices_tab.py")
    assert "isinstance(_cache, dict) and any_server_ok" in src


def test_v0423_kk1_async_worker_intact():
    src = _read("traffic_client/main.py")
    assert "class _ClearAllWorker(QThread):" in src


def test_v0422_jj2_closure_intact():
    src = _read("traffic_client/server_section.py")
    assert "def _refresh_this_iface_only(" in src


def test_v0421_ii1_col1_match_intact():
    src = _read("traffic_client/server_section.py")
    assert 'srv_label = (srv_item.text(1) or "").lower()' in src
