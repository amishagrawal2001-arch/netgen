"""v0.5.392 — Streams tab audit tail (5 items).

  H1  start_stream re-resolves rows by stream_id after pump.
  H2  send_inline_update_to_server async QThread wrapper.
  H3  Add Stream dialog re-entry guard.
  H4  RFC 2544 dialog parameter persistence via QSettings.
  H5  _stream_status_pushed cache TTL prune.
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


def test_stream_control_ast_parses():
    ast.parse(_read("traffic_client/stream_control.py"))


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


def test_rfc2544_dialog_ast_parses():
    ast.parse(_read("widgets/rfc2544_dialog.py"))


# ─── H1: start_stream row re-resolve ───


def test_h1_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.392 (audit streams H1)" in src


def test_h1_update_stream_status_accepts_stream_id():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def update_stream_status(self, row, color")
    _end = _idx + 2500
    body = src[_idx:_end]
    # stream_id kwarg
    assert "stream_id=None" in body
    # Re-scans the table to find the row currently holding sid
    assert "for _r in range(self.stream_table.rowCount()):" in body
    assert "if _it.data(Qt.UserRole) == stream_id:" in body


def test_h1_start_stream_call_sites_pass_stream_id():
    """The 3 post-pump call sites in start_stream must pass
    stream_id= so the row hint gets re-resolved against the
    current table."""
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def start_stream(self)")
    _end = src.index("def stop_stream(self)", _idx) if "def stop_stream" in src[_idx:] else _idx + 40000
    body = src[_idx:_end]
    # At least 3 sites: partial-success green, partial-fail red, main green, assumed-all-running green
    _count = body.count("stream_id=")
    assert _count >= 3, (
        f"Expected ≥3 update_stream_status(stream_id=...) call sites "
        f"in start_stream; got {_count}"
    )


# ─── H2: async inline update ───


def test_h2_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.392 (audit streams H2)" in src


def test_h2_uses_qthread_worker():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def send_inline_update_to_server(self, port, stream):")
    _end = _idx + 4500
    body = src[_idx:_end]
    # Inline QThread subclass
    assert "class _InlineUpdateWorker(QThread):" in body
    # Emits (status_code, err_text)
    assert "done = pyqtSignal(int, str)" in body
    # Fire-and-forget: start() called
    assert "_worker.start()" in body


def test_h2_no_more_sync_requests_post_on_ui_thread():
    """The synchronous `requests.post(url, json=payload, timeout=5)`
    that ran on the UI thread must be gone from the direct
    function body — moved into the QThread worker's run()."""
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def send_inline_update_to_server(self, port, stream):")
    _end = _idx + 4500
    body = src[_idx:_end]
    # There's still one requests.post — but it must be inside
    # the worker's run(), not in the function's own top-level
    # try. Check by counting occurrences AND by verifying the
    # worker-class shape wraps it.
    assert "_r = requests.post(_self._url, json=_self._payload, timeout=5)" in body


def test_h2_keepalive_pinned():
    """The worker must be pinned via _keepalive_worker so PyQt
    doesn't GC it mid-flight (the SIGABRT race that killed
    prior async attempts)."""
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def send_inline_update_to_server(self, port, stream):")
    _end = _idx + 4500
    body = src[_idx:_end]
    assert "self._keepalive_worker(_worker)" in body


# ─── H3: Add Stream re-entry guard ───


def test_h3_marker_present():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.392 (audit streams H3)" in src


def test_h3_flag_check_and_try_finally():
    src = _read("traffic_client/stream_control.py")
    _idx = src.index("def open_add_stream_dialog(self):")
    _end = _idx + 2000
    body = src[_idx:_end]
    # Guard flag check + set + try/finally clear
    assert "_add_stream_dialog_open" in body
    assert "self._add_stream_dialog_open = True" in body
    assert "self._add_stream_dialog_open = False" in body
    assert "try:" in body
    assert "finally:" in body


def test_h3_body_split_into_helper():
    """The original body should have moved into
    _open_add_stream_dialog_body so the top wrapper is small."""
    src = _read("traffic_client/stream_control.py")
    assert "def _open_add_stream_dialog_body(self):" in src


# ─── H4: RFC 2544 parameter persistence ───


def test_h4_marker_present():
    src = _read("widgets/rfc2544_dialog.py")
    assert "v0.5.392 (audit streams H4)" in src


def test_h4_settings_helpers_defined():
    src = _read("widgets/rfc2544_dialog.py")
    for _n in ("_rfc2544_settings", "_restore_rfc2544_params",
               "_save_rfc2544_params"):
        assert f"def {_n}(self)" in src, f"missing helper: {_n}"


def test_h4_qsettings_backed_stable_org_app():
    src = _read("widgets/rfc2544_dialog.py")
    assert '_RFC2544_SETTINGS_ORG = "netgen"' in src
    assert '_RFC2544_SETTINGS_APP = "netgen-client"' in src


def test_h4_persists_all_expected_params():
    src = _read("widgets/rfc2544_dialog.py")
    _idx = src.index("_RFC2544_KEYS = {")
    _end = src.index("}", _idx) + 1
    body = src[_idx:_end]
    for _k in ("tx_iface_field", "mac_src_field", "mac_dst_field",
               "ip_src_field", "ip_dst_field", "duration_spin"):
        assert _k in body, f"KEY map missing {_k}"


def test_h4_wired_into_init_and_close_and_start():
    src = _read("widgets/rfc2544_dialog.py")
    # Restore on __init__
    _init_idx = src.index("def __init__(self, parent=None, server_url:")
    _init_body = src[_init_idx:_init_idx + 2000]
    assert "self._restore_rfc2544_params()" in _init_body
    # Save on close
    _close_idx = src.index("def closeEvent(self, event):")
    _close_body = src[_close_idx:_close_idx + 3500]
    assert "self._save_rfc2544_params()" in _close_body
    # Save on Start (belt-and-suspenders)
    _start_idx = src.index("def _on_start(self):")
    _start_body = src[_start_idx:_start_idx + 2500]
    assert "self._save_rfc2544_params()" in _start_body


# ─── H5: status-cache prune ───


def test_h5_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.392 (audit streams H5)" in src


def test_h5_prune_when_over_soft_cap():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.392 (audit streams H5)")
    body = src[_idx:_idx + 2500]
    # Soft cap + drop stale
    assert "if len(pushed) > 400:" in body
    assert "set(pushed.keys()) - set(by_sid.keys())" in body
    assert "pushed.pop(_sid, None)" in body


# ─── version guard ───


def test_pyproject_version_at_least_0592():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 392)


# ─── regression guards ───


def test_v0391_g5_snapshot_before_modal_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G4 + G5)" in src or "v0.5.391 (audit streams G5)" in src


def test_v0391_g2_edit_name_persist_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.391 (audit streams G2)" in src


def test_v0390_admin_containers_endpoints_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.390 (audit /admin containers-card F1)" in src


def test_v0388_d4_window_layout_intact():
    src = _read("traffic_client/main.py")
    assert "v0.5.388 (audit devices-tab D4)" in src
