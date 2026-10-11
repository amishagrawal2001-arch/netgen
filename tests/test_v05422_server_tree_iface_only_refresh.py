"""v0.5.422 — Server Tree iface-only refresh (JJ1 + JJ2).

Follow-up to v0.5.421 II1 + II2. The II2 fix (invalidate
`server["interfaces"]` + rely on 3 staggered `update_server_tree`
calls at 500ms / 3s / 8s to render fresh data) worked for the chip
color but caused the FULL QTreeWidget to clear and rebuild 3 times
in the 8-second window — operator saw the entire interface list
visibly collapse and re-expand on every Online/Offline click.

  JJ1  Replace the cache invalidation (`_srv["interfaces"] = None`)
       in `_set_interface_admin_state`'s HTTP-200 success path with
       in-place mutation of JUST the matching iface's `status` /
       `operstate` fields. The cached server dict stays intact; a
       later full `update_server_tree` triggered by any other code
       path renders the correct state.

  JJ2  Replace the 3 staggered
       `QTimer.singleShot(..., self.update_server_tree)` calls with
       `_refresh_this_iface_only`, a closure that fetches
       `/api/interfaces`, finds the single iface, mutates the
       cached entry in-place, and calls
       `self._update_iface_icon_for_operstate(_srv_addr, _iface,
       _new_ops)` to repaint JUST that iface's chip. No tree
       rebuild. The 500ms / 3s / 8s stagger is preserved (v0.5.43
       rationale — slow Mellanox 100G/200G carrier negotiation).

Both edits land in traffic_client/server_section.py; no server-side
change.
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


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── JJ1: cache mutation replaces invalidation ───


def test_jj1_marker_present():
    assert "v0.5.422 (audit stream-JJ1" in _read("traffic_client/server_section.py")


def test_jj1_admin_success_mutates_iface_in_place_not_invalidates():
    """The HTTP-200 branch of `_set_interface_admin_state` must now
    mutate the cached iface's status/operstate fields in-place; it
    must NOT set `_srv["interfaces"] = None` (v0.5.421 II2 pattern)
    because that forces 3 full tree rebuilds via the staggered
    update_server_tree calls."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    # The v0.5.421 II2 cache invalidation line must be gone from
    # live code (may still appear in a comment, which is fine).
    assert '_srv["interfaces"] = None' not in _live, (
        "v0.5.421 II2 full-cache invalidation is still live — JJ1 regressed"
    )
    # The in-place mutation must be present.
    assert '_ent["operstate"] = operstate' in body
    assert 'if _ent.get("name") == iface:' in body


# ─── JJ2: single-iface refetch closure replaces tree-rebuild timers ───


def test_jj2_marker_present():
    assert "v0.5.422 (audit stream-JJ2)" in _read("traffic_client/server_section.py")


def test_jj2_refresh_this_iface_only_closure_defined():
    """`_refresh_this_iface_only` closure must be defined inside
    `_set_interface_admin_state`'s success path."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    assert "def _refresh_this_iface_only(" in body
    # Closure must capture server_addr + iface via default args so
    # late-fired timers don't see mutated locals.
    assert "_srv_addr=server_addr" in body
    assert "_iface=iface" in body


def test_jj2_closure_calls_single_iface_icon_paint():
    """The closure must repaint JUST the one iface's chip via
    `_update_iface_icon_for_operstate` — NOT trigger a full tree
    rebuild via `update_server_tree`."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _refresh_this_iface_only")
    _end = src.index("QTimer.singleShot(500,", _idx)
    body = src[_idx:_end]
    assert "self._update_iface_icon_for_operstate(" in body
    assert "self.update_server_tree" not in body, (
        "closure must not call update_server_tree — defeats JJ2"
    )


def test_jj2_closure_fetches_api_interfaces():
    """The closure must GET /api/interfaces with a short timeout —
    the single-iface payload is the authoritative source of
    operstate (same endpoint `update_server_tree` would hit, but
    we extract only the one iface's entry)."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _refresh_this_iface_only")
    _end = src.index("QTimer.singleShot(500,", _idx)
    body = src[_idx:_end]
    assert "/api/interfaces" in body
    assert "timeout=3" in body


def test_jj2_three_staggered_refreshes_fire_closure_not_tree_rebuild():
    """The 500ms / 3s / 8s QTimer schedule from v0.5.43 is preserved
    — but each timer now fires `_refresh_this_iface_only` (JJ2
    closure), NOT `self.update_server_tree`."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    assert "QTimer.singleShot(500, _refresh_this_iface_only)" in _live
    assert "QTimer.singleShot(3000, _refresh_this_iface_only)" in _live
    assert "QTimer.singleShot(8000, _refresh_this_iface_only)" in _live
    # The pre-fix shape (v0.5.421 II2 + v0.5.43) scheduled
    # self.update_server_tree directly — must be gone from live code.
    assert "QTimer.singleShot(500, self.update_server_tree)" not in _live, (
        "JJ2 regressed: 500ms timer still rebuilds whole tree"
    )
    assert "QTimer.singleShot(3000, self.update_server_tree)" not in _live
    assert "QTimer.singleShot(8000, self.update_server_tree)" not in _live


# ─── Smoke: II1 chain remains intact ───


def test_v0421_ii1_col1_match_intact():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _update_iface_icon_for_operstate")
    _end = src.index("def _set_interface_admin_state", _idx)
    body = src[_idx:_end]
    assert 'srv_label = (srv_item.text(1) or "").lower()' in body


# ─── version guard ───


def test_pyproject_at_least_0622():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 422)


# ─── regression guards ───


def test_v0421_ii1_marker_intact():
    assert "v0.5.421 (audit stream-II1)" in _read("traffic_client/server_section.py")


def test_v0420_hh1_vrf_slaved_del_guard_intact():
    src = _read("utils/dhcp.py")
    assert "v0.5.420 (audit stream-HH1)" in src


def test_v0419_frr_docker_scanner_intact():
    src = _read("utils/frr_docker.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src


def test_v0418_gg1_init_marker_intact():
    src = _read("utils/frr_docker.py")
    assert "v0.5.418 (audit stream-GG1)" in src
