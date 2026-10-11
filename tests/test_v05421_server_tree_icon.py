"""v0.5.421 — Server Tree iface-icon stuck green after Online/Offline (II1 + II2).

Operator reproduction on srv06 2026-10-11:
 * Right-click ens2f1np1 → Offline → confirmation Yes → POST succeeded
   (HTTP 200, kernel operstate flipped to "down").
 * Server-side `/api/interfaces` correctly returned `status=down`.
 * Client's iface chip STAYED GREEN forever.

Two stacked client-side bugs:

  II1  _update_iface_icon_for_operstate matched the server row's
       `text(0)`, but col 0 is populated by `setItemWidget(..., 0,
       tg_id_widget)` so `text(0)` is ALWAYS "". `server_addr not in
       ""` was True → every server row skipped → iface icon never
       updated via the admin-fast-path.

  II2  update_server_tree only re-fetches /api/interfaces when
       `server["interfaces"]` is None (server_section.py:1686-87).
       The 3 scheduled refreshes at 500ms / 3s / 8s re-rendered from
       the SAME cached dict (still carrying `status="up"` from before
       the Offline POST) → chip stayed green until some external
       poller eventually invalidated the cache. Fix: clear the cache
       in the admin-success path so the staggered refreshes actually
       hit the server.

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


# ─── II1: _update_iface_icon_for_operstate matches col 1 ───


def test_ii1_marker_present():
    assert "v0.5.421 (audit stream-II1)" in _read("traffic_client/server_section.py")


def test_ii1_server_match_reads_column_1():
    """The server-row match in `_update_iface_icon_for_operstate`
    must read `srv_item.text(1)` (Server Address column), not
    `.text(0)` (empty when the TG ID is set via setItemWidget)."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _update_iface_icon_for_operstate")
    _end = src.index("def _set_interface_admin_state", _idx)
    body = src[_idx:_end]
    assert "srv_label = (srv_item.text(1) or \"\").lower()" in body, (
        "server-row match should read text(1); still reads text(0)?"
    )
    # The pre-fix line is gone from live code (may remain in a
    # comment, which is fine).
    assert "srv_label = (srv_item.text(0) or \"\").lower()" not in _strip_comments(body), (
        "pre-fix text(0) read still in live code — II1 regressed"
    )


# ─── II2: admin-success invalidates cached interfaces ───


def test_ii2_marker_present():
    assert "v0.5.421 (audit stream-II2)" in _read("traffic_client/server_section.py")


def test_ii2_admin_success_clears_server_interfaces_cache():
    """`_set_interface_admin_state`'s HTTP-200 branch must clear
    `server["interfaces"]` for the matching server BEFORE the three
    scheduled refreshes fire — otherwise update_server_tree re-renders
    from the stale cached list and the icon stays on the pre-click
    color forever."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    assert "server_interfaces" in body
    assert "_srv[\"interfaces\"] = None" in body
    # The invalidation must sit BEFORE the staggered QTimer schedules,
    # otherwise the 500ms refresh fires with the stale cache intact.
    _inv_pos = body.index("_srv[\"interfaces\"] = None")
    _timer_pos = body.index("QTimer.singleShot(500,")
    assert _inv_pos < _timer_pos, (
        "cache invalidation must precede the scheduled refreshes"
    )


# ─── Smoke: the admin-success path still schedules the 3 refreshes ───


def test_ii2_three_staggered_refreshes_still_scheduled():
    """v0.5.43 landed 500ms + 3s + 8s refreshes to catch slow Mellanox
    carrier negotiation. II2 adds a cache-invalidation step but must
    not drop the staggered schedule."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    assert "QTimer.singleShot(500, self.update_server_tree)" in body
    assert "QTimer.singleShot(3000, self.update_server_tree)" in body
    assert "QTimer.singleShot(8000, self.update_server_tree)" in body


# ─── version guard ───


def test_pyproject_at_least_0621():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 421)


# ─── regression guards ───


def test_v0420_hh1_vrf_slaved_del_guard_intact():
    src = _read("utils/dhcp.py")
    assert "v0.5.420 (audit stream-HH1)" in src


def test_v0419_frr_docker_scanner_intact():
    src = _read("utils/frr_docker.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src


def test_v0418_gg1_init_marker_intact():
    src = _read("utils/frr_docker.py")
    assert "v0.5.418 (audit stream-GG1)" in src
