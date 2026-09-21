"""v0.5.390 — /admin Containers card (3 items).

  F1  GET /api/admin/containers — enumerate with device_id, vrf, iface, has_db_row.
  F2  POST /api/admin/containers/remove — selective per-container remove via stop_frr_container.
  F3  /admin Containers card UI — table + checkboxes + confirm.
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


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


# ─── F1: list endpoint ───


def test_f1_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.390 (audit /admin containers-card F1)" in src


def test_f1_route_registered_viewer():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/admin/containers",\s*methods=\["GET"\]\)'
        r'\s*\n\s*@require_role\(\s*[\'"]viewer[\'"]\s*\)',
        src,
    )
    assert m, "GET /api/admin/containers must be viewer-gated"


def test_f1_uses_list_all_containers_helper():
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_list_containers():")
    body = src[_idx:_idx + 3500]
    assert "list_all_containers as _list_all" in body
    # Enriches with vrf + interface + has_db_row
    assert "vrf_name_for_device(_did)" in body
    assert '"has_db_row"' in body
    assert '"interface": _iface' in body


def test_f1_db_ids_precomputed_not_per_row_query():
    """Should build a single set of DB device_ids up-front, not
    call get_device per container (N+1 anti-pattern)."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_list_containers():")
    body = src[_idx:_idx + 3500]
    assert "_db_ids = set()" in body
    assert "for _d in device_db.get_all_devices():" in body


# ─── F2: remove endpoint ───


def test_f2_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.390 (audit /admin containers-card F2)" in src


def test_f2_route_registered_admin():
    src = _read("run_tgen_server.py")
    m = re.search(
        r'@app\.route\("/api/admin/containers/remove",\s*methods=\["POST"\]\)'
        r'\s*\n\s*@require_role\(\s*[\'"]admin[\'"]\s*\)',
        src,
    )
    assert m, "POST /api/admin/containers/remove must be admin-gated"


def test_f2_uses_stop_frr_container():
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_remove_containers():")
    body = src[_idx:_idx + 3500]
    assert "_fm.stop_frr_container(_did, None, remove=True)" in body


def test_f2_whitelist_prefixes():
    """Refuses non-managed container names (defense-in-depth
    against a POST that names an unrelated container)."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_remove_containers():")
    body = src[_idx:_idx + 3500]
    assert '_MANAGED_PREFIXES = ("ostg-frr-", "dhcp-frr-")' in body
    assert "not a " in body  # "not a netgen-managed container name"


def test_f2_returns_per_container_results():
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_remove_containers():")
    body = src[_idx:_idx + 3500]
    assert '"success_count": _succ' in body
    assert '"failure_count": _fail' in body
    assert '"results": _results' in body


def test_f2_empty_names_returns_400():
    src = _read("run_tgen_server.py")
    _idx = src.index("def admin_remove_containers():")
    body = src[_idx:_idx + 3500]
    assert "Provide non-empty `names` list" in body
    assert "}), 400" in body


# ─── F3: UI card ───


def test_f3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.390 (audit admin-containers-card)" in src
    # HTML block + JS block
    assert src.count("v0.5.390 (audit admin-containers-card)") >= 2


def test_f3_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-refresh-containers", "btn-remove-containers",
                "containers-count", "containers-remove-result",
                "containers-table-wrap"):
        assert f'id="{_id}"' in src, (
            f"Containers card missing element {_id}"
        )


def test_f3_remove_button_starts_disabled():
    """The Remove Selected button must render disabled until
    the operator checks at least one row."""
    src = _read("run_tgen_server.py")
    assert 'id="btn-remove-containers" disabled' in src


def test_f3_js_refresh_fetches_endpoint():
    src = _read("run_tgen_server.py")
    _idx = src.index("async function _refreshContainers()")
    body = src[_idx:_idx + 4500]
    assert "/api/admin/containers" in body
    # Renders checkbox per row
    assert 'class="container-select"' in body
    # Select-all wired
    assert 'id="container-select-all"' in body


def test_f3_js_remove_confirms_and_posts():
    src = _read("run_tgen_server.py")
    _idx = src.index("async function _removeSelectedContainers()")
    body = src[_idx:_idx + 4500]
    # Confirmation prompt
    assert "window.confirm(" in body
    # POST to remove endpoint
    assert "'/api/admin/containers/remove'" in body
    assert "method: 'POST'" in body
    # Refreshes table after remove
    assert "_refreshContainers" in body


def test_f3_js_button_state_helper():
    src = _read("run_tgen_server.py")
    assert "function _updateRemoveButtonState()" in src
    _idx = src.index("function _updateRemoveButtonState()")
    body = src[_idx:_idx + 800]
    assert ".container-select:checked" in body


def test_f3_dom_ready_auto_loads():
    src = _read("run_tgen_server.py")
    _idx = src.index("async function _refreshContainers()")
    _end = _idx + 8000
    body = src[_idx:_end]
    assert "'containers-table-wrap'" in body
    assert "DOMContentLoaded" in body


# ─── version guard ───


def test_pyproject_version_at_least_0590():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 390)


# ─── regression guards ───


def test_v0389_e4_mac_regex_intact():
    src = _read("widgets/add_device_dialog.py")
    assert "v0.5.389 (audit devices-tab E4)" in src


def test_v0388_d4_layout_persistence_intact():
    src = _read("traffic_client/main.py")
    assert "v0.5.388 (audit devices-tab D4)" in src


def test_v0382_w1_release_vrf_table_still_wired():
    """F2 depends on stop_frr_container's release_vrf_table
    fan-out (v0.5.382 W1). Regression guard."""
    src = _read("utils/frr_docker.py")
    assert "self._release_vrf_table(device_id)" in src


def test_v0382_w2_find_existing_container_still_present():
    """F2 relies on the both-prefix lookup (v0.5.382 W2) so
    the operator can nuke either dhcp-frr- or ostg-frr-
    containers regardless of current dhcp_mode."""
    src = _read("utils/frr_docker.py")
    assert "def _find_existing_container(self, device_id: str" in src


def test_v0379_streams_card_intact():
    """Sibling admin card — must not be affected by F3 insertion."""
    src = _read("run_tgen_server.py")
    assert 'id="btn-refresh-streams"' in src
