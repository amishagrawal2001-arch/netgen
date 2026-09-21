"""v0.5.374 — 2 bug fixes + 2 admin console cards.

E1  DHCP restart async QThread + Cancel (main-thread freeze fix)
E2  Stream stats numeric sort (NumericSortItem)
E3  /admin Upgrade Wheel card (wires existing v0.5.23 endpoints)
E4  /admin Restart Server button + running-version drift indicator

DHCP-restart QThread refactor closes the last client-side UI-
freeze finding from the v0.5.372 audit. Admin console coverage
gaps for Cache Flush / Journal / Streams / FRR / Chassis remain
in the deferred queue for v0.5.375+.
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


def test_all_files_ast_parse():
    for _f in ("run_tgen_server.py",
               "utils/devices_tab_dhcp.py",
               "traffic_client/statistics_section.py"):
        ast.parse(_read(_f))


# ─── E1: DHCP restart async ───


def test_e1_marker_present():
    src = _read("utils/devices_tab_dhcp.py")
    assert "v0.5.374 (audit dhcp-restart-ui-freeze)" in src


def test_e1_worker_class_defined():
    """A QThread subclass must wrap the requests.post so the main
    thread doesn't freeze during the ≤60s server-side cycle."""
    src = _read("utils/devices_tab_dhcp.py")
    assert "class RestartDHCPWorker(QThread):" in src
    # Signals for both success + error branches.
    assert "finished_ok = pyqtSignal(object)" in src
    assert "finished_err = pyqtSignal(str)" in src


def test_e1_cancel_button_added():
    """The QProgressDialog Cancel arg must be a real "Cancel"
    string now (was None pre-fix)."""
    src = _read("utils/devices_tab_dhcp.py")
    # Look inside the specific block we changed. The pre-fix `None`
    # remains as an argument in other progress dialogs; here we
    # care about the DHCP-restart-worker one.
    m = re.search(
        r"v0\.5\.374 \(audit dhcp-restart-ui-freeze\)[\s\S]{0,3500}?_prog\.canceled\.connect",
        src,
    )
    assert m, "DHCP restart Cancel wiring not found"
    body = m.group(0)
    assert '"Cancel"' in body


def test_e1_worker_started_and_awaited():
    """The worker must be .start()ed and the modal dialog exec_()
    used to block on this window (not the whole app) until the
    worker finishes or the operator cancels."""
    src = _read("utils/devices_tab_dhcp.py")
    m = re.search(
        r"v0\.5\.374 \(audit dhcp-restart-ui-freeze\)[\s\S]{0,4500}?resp = _result_box",
        src,
    )
    assert m
    body = m.group(0)
    assert "_worker.start()" in body
    assert "_prog.exec_()" in body
    assert "_worker.finished_ok.connect" in body
    assert "_worker.finished_err.connect" in body
    # Anti-GC guard (matches the refresh path's pattern).
    assert "isFinished()" in body or "wait(" in body


# ─── E2: Stream stats numeric sort ───


def test_e2_marker_present():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.374 (audit stream-stats-string-sort)" in src


def test_e2_numeric_sort_item_subclass_defined():
    src = _read("traffic_client/statistics_section.py")
    assert "class NumericSortItem(QTableWidgetItem):" in src
    # Custom __lt__ must exist so Qt sorts by the numeric backing.
    assert "def __lt__(self, other):" in src


def test_e2_helper_factory_defined():
    src = _read("traffic_client/statistics_section.py")
    assert "def _make_numeric_item(display, value):" in src
    # Attaches numeric via Qt.UserRole.
    assert "setData(Qt.UserRole" in src


def test_e2_all_6_numeric_columns_use_helper():
    """TX Count, RX Count, TX Rate, RX Rate, TX bps, RX bps —
    all six numeric cells in the stream stats row must build via
    _make_numeric_item (not the plain QTableWidgetItem(display)
    that was silently text-sorted)."""
    src = _read("traffic_client/statistics_section.py")
    # Section runs from "TX Count" comment through the RX Bit Rate
    # setItem — 6 helper calls inside that span.
    m = re.search(
        r"# TX Count[\s\S]+?self\.stream_statistics_table\.setItem\(row, 8",
        src,
    )
    assert m
    body = m.group(0)
    _count = body.count("_make_numeric_item(")
    assert _count == 6, (
        f"Expected 6 numeric cells wired through helper; got {_count}"
    )


# ─── E3: Upgrade Wheel card ───


def test_e3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.374 (audit admin-upgrade-wheel-card)" in src


def test_e3_html_elements_present():
    src = _read("run_tgen_server.py")
    for _id in ("btn-upgrade-wheel", "upgrade-wheel-file",
                "p-upgrade-state", "p-upgrade-started",
                "p-upgrade-wheel-name", "p-upgrade-restart",
                "upgrade-progress"):
        assert f'id="{_id}"' in src, (
            f"Upgrade Wheel card missing element {_id}"
        )


def test_e3_click_handler_uploads_multipart():
    """The click handler must build a FormData with a `wheel`
    field and POST to /api/admin/upgrade_wheel (multipart)."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r"btn-upgrade-wheel'\)\.addEventListener[\s\S]+?_upgradePollTimer = setInterval",
        src,
    )
    assert m
    body = m.group(0)
    assert "new FormData()" in body
    assert "_fd.append('wheel'" in body
    assert "/api/admin/upgrade_wheel" in body
    # Confirm dialog before upload (destructive action).
    assert "if (!confirm(" in body


def test_e3_poll_function_defined():
    """The polling function must call /api/admin/upgrade_wheel/log
    every 2 s and update the UI's log + status elements."""
    src = _read("run_tgen_server.py")
    assert "async function _pollUpgradeLog()" in src
    m = re.search(
        r"async function _pollUpgradeLog\(\)[\s\S]{0,3000}?^\s{4}\}",
        src,
        re.MULTILINE,
    )
    assert m
    body = m.group(0)
    assert "/api/admin/upgrade_wheel/log" in body
    assert "_stopUpgradePoll" in body


# ─── E4: Restart Server + running-version ───


def test_e4_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.374 (audit admin-server-card-restart-button)" in src


def test_e4_restart_button_in_html():
    src = _read("run_tgen_server.py")
    assert 'id="btn-restart-server"' in src


def test_e4_running_version_row_in_html():
    src = _read("run_tgen_server.py")
    assert 'id="p-svc-running-ver"' in src


def test_e4_admin_health_exposes_running_version():
    """api_admin_health must include netgen_version +
    netgen_running_version + netgen_restart_pending in its
    payload so the Server card can render drift."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return jsonify\(out\)",
        src,
    )
    assert m
    body = m.group(0)
    assert '"netgen_version"' in body
    assert '"netgen_running_version"' in body
    assert '"netgen_restart_pending"' in body


def test_e4_click_handler_posts_and_polls_health():
    """The button handler must POST /api/system/restart_service
    and poll /api/health until the server comes back up."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r"btn-restart-server'\)\.addEventListener[\s\S]{0,3000}?const _reconnect",
        src,
    )
    assert m
    body = m.group(0)
    assert "/api/system/restart_service" in body
    assert "if (!confirm(" in body
    # Post-restart /api/health probe loop.
    _tail = src[m.end():m.end() + 1500]
    assert "/api/health" in _tail


def test_e4_running_version_drift_amber():
    """The JS render for the Running version row must go amber
    when installed != running (v0.5.370 B8 restart_pending
    signal)."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r"p-svc-running-ver[\s\S]{0,1500}?var\(--warn\)",
        src,
    )
    assert m, (
        "Running version row doesn't switch to warn color on drift"
    )


# ─── version guard ───


def test_pyproject_version_at_least_0574():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 374), (
        f"Version {m.group(1)} < 0.5.374"
    )


# ─── regression guards ───


def test_v0373_vrf_allocator_intact():
    """v0.5.373 D1 VRF allocator + persistence still there."""
    src = _read("utils/frr_docker.py")
    assert "v0.5.373 (audit vrf-table-id-collision)" in src
    assert "_vrf_allocated" in src


def test_v0372_c1_url_guard_intact():
    src = _read("run_tgen_client.py")
    assert "v0.5.372 (audit client-auth-token-leak-monkey-patch)" in src


def test_v0371_install_dpdk_marker_intact():
    dpdk = (_REPO / "resources" / "dpdk" / "install_dpdk.sh").read_text()
    assert "v0.5.371 (audit install-dpdk-must-not-fail)" in dpdk


def test_v0370_b8_health_running_version_still_there():
    """The /api/health running_version fields from v0.5.370 B8
    must still be present — E4 added them to /admin/health too."""
    src = _read("run_tgen_server.py")
    m = re.search(
        r"def api_health\(\)[\s\S]+?return jsonify\(\{[\s\S]+?\}\)",
        src,
    )
    body = m.group(0)
    assert '"running_version"' in body


def test_v0367_rdma_install_button_intact():
    """v0.5.367 RDMA install button + handler must survive since
    v0.5.374 touched the same block."""
    src = _read("run_tgen_server.py")
    assert 'id="btn-install-rdma"' in src
    assert "_pollRdmaLog" in src
