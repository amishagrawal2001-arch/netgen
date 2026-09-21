"""v0.5.394 — RFC 2544 sync HTTP + streams deferred tail (5 items).

  K1  RFC 2544 dialog: 6 sync HTTP calls wrapped in _RfcHttpWorker(QThread).
  K2  stop_stream: name-fallback refuses to guess on duplicate matches.
  K3  _apply_stream_body: row-index fallback skipped under sort/filter.
  K4  server_tree D5 restore: adds setSelected + scrollToItem + focus re-anchor.
  K5  _stop_stream_by_id: only paints red + mutates status when HTTP ok.
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


def test_rfc2544_ast_parses():
    ast.parse(_read("widgets/rfc2544_dialog.py"))


def test_stream_logic_ast_parses():
    ast.parse(_read("traffic_client/stream_logic.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


# ─── K1: RFC 2544 sync HTTP → QThread worker ───


def test_k1_marker_present():
    src = _read("widgets/rfc2544_dialog.py")
    assert "v0.5.394 (audit rfc2544 K1)" in src


def test_k1_worker_class_defined():
    src = _read("widgets/rfc2544_dialog.py")
    assert "class _RfcHttpWorker(QThread):" in src
    # Signal shape matches (status, err, body)
    _cls_idx = src.index("class _RfcHttpWorker(QThread):")
    body = src[_cls_idx:_cls_idx + 2500]
    assert "done = pyqtSignal(int, str, str)" in body
    assert 'requests.get(self._url, timeout=self._timeout)' in body
    assert 'requests.post(' in body


def test_k1_keepalive_and_release_helpers():
    src = _read("widgets/rfc2544_dialog.py")
    assert "def _keepalive_worker(self, worker):" in src
    assert "def _release_worker(self, worker):" in src
    assert "self._live_workers = []" in src


def test_k1_five_call_sites_use_worker():
    """5 of the 6 original sync HTTP calls now spawn _RfcHttpWorker
    (Start, poll, Stop, export CSV, export HTML). The 6th
    (cleanup-on-close) uses threading.Thread instead."""
    src = _read("widgets/rfc2544_dialog.py")
    _worker_spawns = src.count("_RfcHttpWorker(")
    # Class def + 5 call sites
    assert _worker_spawns >= 6, f"Expected ≥6 _RfcHttpWorker references; got {_worker_spawns}"


def test_k1_response_slots_defined():
    src = _read("widgets/rfc2544_dialog.py")
    for _slot in ("_on_start_response", "_on_poll_response",
                  "_on_stop_response", "_on_export_csv_response",
                  "_on_export_html_response"):
        assert f"def {_slot}(self" in src, f"missing slot: {_slot}"


def test_k1_cleanup_uses_daemon_thread_not_sync_post():
    """closeEvent's stop POST must be off the UI thread AND
    decoupled from the dying dialog widget's Qt lifecycle."""
    src = _read("widgets/rfc2544_dialog.py")
    _idx = src.index("def _stop_test_and_cleanup_timer")
    body = src[_idx:_idx + 2500]
    # No bare sync requests.post — uses threading.Thread(daemon=True)
    assert "threading.Thread(" in body
    assert "daemon=True" in body


def test_k1_poll_in_flight_guard():
    """Two poll ticks arriving 2 s apart against a wedged server must
    not stack workers — the second tick is skipped."""
    src = _read("widgets/rfc2544_dialog.py")
    _idx = src.index("def _poll_progress(self):")
    body = src[_idx:_idx + 2500]
    assert '_poll_in_flight' in body
    assert 'self._poll_in_flight = True' in body


def test_k1_no_lingering_sync_calls_in_qt_slots():
    """Every `requests.get/post` outside the _RfcHttpWorker class must
    be inside a threading.Thread body (cleanup). No other bare sync
    HTTP call remains in the Rfc2544Dialog class scope. Comments
    referencing 'requests.get(...)' don't count."""
    src = _read("widgets/rfc2544_dialog.py")
    _cls_end = src.index("class Rfc2544Dialog(QDialog):")
    _dlg_body = src[_cls_end:]
    # Strip comment lines and docstrings before counting so we don't
    # trip on a comment that mentions requests.get.
    _stripped = "\n".join(
        _line for _line in _dlg_body.split("\n")
        if not _line.lstrip().startswith("#")
    )
    _bare_get = len(re.findall(r"requests\.get\(", _stripped))
    _bare_post = len(re.findall(r"requests\.post\(", _stripped))
    assert _bare_get == 0, f"lingering requests.get in dialog body: {_bare_get}"
    assert _bare_post <= 1, f"lingering requests.post in dialog body: {_bare_post}"


# ─── K2: stop_stream name-fallback duplicate refusal ───


def test_k2_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.394 (audit streams K2)" in src


def test_k2_counts_matches_before_picking():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("v0.5.394 (audit streams K2)")
    body = src[_idx:_idx + 3000]
    # Collects all candidates, checks len
    assert "_candidates = [" in body
    assert "len(_candidates) == 1:" in body
    assert "len(_candidates) > 1:" in body
    assert "Refusing to stop" in body


def test_k2_errors_hoisted_above_loop():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def stop_stream(self)")
    _end = src.index("def _begin_button_feedback", _idx)
    body = src[_idx:_end]
    # Only ONE ACTUAL assignment `errors_for_user = []` within
    # stop_stream (hoisted at the top). Comments mentioning
    # `errors_for_user = []` in backticks don't count.
    _actual_assigns = re.findall(r"^\s+errors_for_user = \[\]", body, re.MULTILINE)
    assert len(_actual_assigns) == 1, (
        f"expected one errors_for_user init inside stop_stream; "
        f"got {len(_actual_assigns)}"
    )


def test_k2_second_site_also_disambiguates():
    """Parity fix: the local-state update block also disambiguates
    duplicate matches instead of blindly picking the first."""
    src = _read("traffic_client/stream_logic.py")
    # The local-state block is between "Update ONLY status locally"
    # and the errors_for_user surface at the end of stop_stream.
    _idx = src.index("Update ONLY status locally")
    _end = src.index("if errors_for_user:", _idx)
    body = src[_idx:_end]
    assert "_local_matches = [" in body
    assert "len(_local_matches) > 1:" in body
    assert "v0.5.394 (audit streams K2)" in body


# ─── K3: _apply_stream_body row-index gate ───


def test_k3_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.394 (audit streams K3)" in src


def test_k3_sort_and_filter_gate():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("v0.5.394 (audit streams K3)")
    body = src[_idx:_idx + 3500]
    # Introspects sort indicator
    assert "isSortIndicatorShown()" in body
    # Scans for filter widgets
    assert "_stream_filter_text" in body
    assert "stream_search_field" in body
    # Only applies fallback when BOTH off
    assert "_table_sorted or _filter_active" in body


# ─── K4: server_tree D5 keyboard-focus restore ───


def test_k4_marker_present():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.394 (audit K4)" in src


def test_k4_selected_plus_scroll_plus_currentindex():
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.394 (audit K4)")
    body = src[_idx:_idx + 4000]
    # setSelected pairs setCurrentItem
    assert "_it.setSelected(True)" in body
    assert "_ch.setSelected(True)" in body
    # scrollToItem
    assert "scrollToItem(_restored_item)" in body
    # selectionModel currentIndex re-anchor
    assert "QItemSelectionModel" in body
    assert "setCurrentIndex(" in body


def test_k4_focus_gated_on_prior_owner():
    """setFocus() must NOT unconditionally steal focus — only if the
    tree was already the focus owner."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("v0.5.394 (audit K4)")
    body = src[_idx:_idx + 4000]
    assert "focusWidget()" in body
    assert "if _focus is self.server_tree:" in body


# ─── K5: _stop_stream_by_id status/paint gated on ok ───


def test_k5_marker_present():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.394 (audit streams K5)" in src


def test_k5_status_and_paint_only_on_ok():
    src = _read("traffic_client/stream_logic.py")
    _idx = src.index("def _stop_stream_by_id(self")
    _end = src.index("def _schedule_stream_auto_stop", _idx)
    body = src[_idx:_end]
    # Structural: `if ok:` gates both the status mutation and the paint
    assert "if ok:" in body
    # Paint call uses stream_id= (v0.5.392 H1 alignment)
    assert 'update_stream_status(row_idx, "red", stream_id=stream_id)' in body
    # The else branch logs a warning and does NOT mutate
    assert "leaving local status" in body


# ─── version guard ───


def test_pyproject_at_least_0594():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 394)


# ─── regression guards ───


def test_v0393_j1_server_counter_trust_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.393 (audit streams J1)" in src


def test_v0393_j2_client_counter_override_intact():
    src = _read("traffic_client/statistics_section.py")
    assert "v0.5.393 (audit streams J2" in src


def test_v0392_h4_rfc2544_qsettings_intact():
    src = _read("widgets/rfc2544_dialog.py")
    assert "v0.5.392 (audit streams H4)" in src


def test_v0388_d5_server_tree_state_intact():
    src = _read("traffic_client/server_section.py")
    assert "v0.5.388 (D5)" in src
