"""v0.5.401 — menu_actions.py + dpdk_menu_actions.py audit.

  R1  import_devices_from_file async + confirmation.
  R2  _reboot_servers_list dispatched to _ServerListActionWorker.
  R3  _restart_servers_list dispatched to _ServerListActionWorker.
  R4  CPU-vendor probe failure aborts flow (no silent 'intel' default).
  R5  _perform_configure_iommu uses _DpdkApiWorker + _handle_iommu_result.
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
    """Drop full-line python comments AND triple-quoted docstrings
    so `requests.post(...)` mentioned in a docstring or comment
    doesn't create a false positive."""
    # Strip triple-quoted blocks first (both """ and ''')
    _no_triple = re.sub(r'"""[\s\S]*?"""', '', src)
    _no_triple = re.sub(r"'''[\s\S]*?'''", '', _no_triple)
    return "\n".join(
        _line for _line in _no_triple.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_menu_actions_ast_parses():
    ast.parse(_read("traffic_client/menu_actions.py"))


def test_dpdk_menu_actions_ast_parses():
    ast.parse(_read("traffic_client/dpdk_menu_actions.py"))


# ─── Shared worker class ───


def test_server_list_action_worker_defined():
    src = _read("traffic_client/menu_actions.py")
    assert "class _ServerListActionWorker(QThread):" in src
    _idx = src.index("class _ServerListActionWorker(QThread):")
    body = src[_idx:_idx + 2000]
    assert "per_server = pyqtSignal(str)" in body
    assert "finished_all = pyqtSignal(list)" in body
    assert "def run(self):" in body


# ─── R1: import_devices async ───


def test_r1_marker_present():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.401 (audit menu R1)" in src


def test_r1_import_devices_confirms_and_uses_worker():
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("def import_devices_from_file(self)")
    _end = src.index("def save_session", _idx)
    body = src[_idx:_end]
    # Confirmation dialog before firing
    assert "Confirm device import" in body
    # Uses _DpdkApiWorker
    assert "_DpdkApiWorker" in body
    # _keepalive_worker pinning
    assert "self._keepalive_worker(_worker)" in body
    # Response slot distinguishes network vs server error
    assert "Import failed (network)" in body


def test_r1_no_sync_requests_post_in_import():
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("def import_devices_from_file(self)")
    _end = src.index("def save_session", _idx)
    body = _strip_comments(src[_idx:_end])
    # No sync `requests.post(` in the handler body (the timeout=300
    # was the freeze source).
    assert "requests.post(" not in body


# ─── R2: reboot_servers_list async ───


def test_r2_marker_present():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.401 (audit menu R2)" in src


def test_r2_reboot_extracted_and_dispatched():
    src = _read("traffic_client/menu_actions.py")
    _list_idx = src.index("def _reboot_servers_list(self, servers):")
    _list_end = src.index("def _reboot_single_server(self, server):", _list_idx)
    _list_body = src[_list_idx:_list_end]
    # Uses the shared worker
    assert "_ServerListActionWorker(servers, self._reboot_single_server)" in _list_body
    assert "_worker.finished_all.connect(" in _list_body

    # Helper method exists and is a plain method (returns strings)
    assert "def _reboot_single_server(self, server):" in src


def test_r2_helper_returns_strings_no_qt():
    """_reboot_single_server runs on the worker thread — must NOT
    touch Qt widgets. Enforce by scanning for QMessageBox in the
    helper body."""
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("def _reboot_single_server(self, server):")
    # Bound at end-of-file or next top-level def
    _next = src.find("\n    def ", _idx + 50)
    body = src[_idx:_next] if _next != -1 else src[_idx:]
    body = _strip_comments(body)
    assert "QMessageBox" not in body, (
        "_reboot_single_server touches QMessageBox — worker-thread "
        "code must return strings, not open modals"
    )


# ─── R3: restart_servers_list async ───


def test_r3_marker_present():
    src = _read("traffic_client/menu_actions.py")
    assert "v0.5.401 (audit menu R3)" in src


def test_r3_restart_extracted_and_dispatched():
    src = _read("traffic_client/menu_actions.py")
    _list_idx = src.index("def _restart_servers_list(self, servers):")
    _list_end = src.index("def _restart_single_server(self, server):", _list_idx)
    _list_body = src[_list_idx:_list_end]
    assert "_ServerListActionWorker(servers, self._restart_single_server)" in _list_body
    assert "_worker.finished_all.connect(" in _list_body


def test_r3_helper_returns_strings_no_qt():
    src = _read("traffic_client/menu_actions.py")
    _idx = src.index("def _restart_single_server(self, server):")
    _end = src.index("def reboot_server(self):", _idx) if "def reboot_server" in src[_idx:] else _idx + 4000
    body = _strip_comments(src[_idx:_end])
    assert "QMessageBox" not in body


# ─── R4: CPU-vendor probe failure aborts ───


def test_r4_marker_present():
    src = _read("traffic_client/dpdk_menu_actions.py")
    assert "v0.5.401 (audit menu R4)" in src


def test_r4_no_silent_intel_default():
    """The pre-fix `cpu_vendor = 'intel'  # default` and the
    swallowed except:pass are gone. Vendor must be either 'intel'
    or 'amd' from a successful probe, else the flow aborts with a
    QMessageBox and returns."""
    src = _read("traffic_client/dpdk_menu_actions.py")
    _idx = src.index("v0.5.401 (audit menu R4)")
    body = src[_idx:_idx + 3500]
    # New pattern: cpu_vendor starts None, only set from a valid response
    assert "cpu_vendor = None" in body
    assert 'if _v in ("intel", "amd"):' in body
    # Aborts with clear error when vendor unknown
    assert "Cannot determine CPU vendor" in body
    assert "Refusing to guess" in body
    # And when /api/dpdk/status is non-200
    assert "Cannot determine IOMMU status" in body


# ─── R5: configure_iommu async ───


def test_r5_marker_present():
    src = _read("traffic_client/dpdk_menu_actions.py")
    assert "v0.5.401 (audit menu R5)" in src


def test_r5_uses_dpdk_api_worker():
    src = _read("traffic_client/dpdk_menu_actions.py")
    _idx = src.index("def _perform_configure_iommu(self")
    _end = src.index("def _handle_iommu_result(self", _idx)
    body = src[_idx:_end]
    # No sync requests.post in the handler body (worker takes over)
    _stripped = _strip_comments(body)
    assert "requests.post(" not in _stripped
    # Uses _DpdkApiWorker
    assert "_DpdkApiWorker(" in body
    assert "self._track_dpdk_worker(worker)" in body


def test_r5_result_handler_distinguishes_error_kinds():
    src = _read("traffic_client/dpdk_menu_actions.py")
    _idx = src.index("def _handle_iommu_result(self")
    _end = _idx + 3500
    body = src[_idx:_end]
    # Network error branch
    assert "IOMMU configure failed (network)" in body
    # Server 5xx / non-200 branch
    assert "IOMMU configure failed (server)" in body
    # 200 + success:false branch
    assert "acknowledged the request but reported" in body


# ─── version guard ───


def test_pyproject_at_least_0601():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 401)


# ─── regression guards ───


def test_v0400_q1_aggregated_call_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.400 (audit stats-Q1)" in src


def test_v0399_p2_status_clear_intact():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P2)" in src


def test_v0398_o1_iso_cutoff_intact():
    src = _read("utils/stream_database.py")
    assert "timedelta(days=days)" in src


def test_v0395_stream_dialog_async_helper_intact():
    src = _read("widgets/stream_dialog.py")
    assert "def _stream_async_get(self, url, on_ok, on_err=None, timeout=3.0):" in src
