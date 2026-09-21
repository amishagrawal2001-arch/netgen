"""v0.5.395 — Add Stream dialog: 5 sync HTTP GETs → async worker.

  L1  _recommend_tx_cores DPDK button.
  L2  populate_stream_fields dst-MAC prefill.
  L3  _fetch_rx_engine_advice lazy-cache converted to async.
  L4  _on_autopopulate_dst_mac.
  L5  _fetch_iface_mac_from_server + _on_autopopulate_src_mac.
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
    """Drop full-line python comments so 'requests.get' in a
    comment doesn't create false positives when we assert the
    absence of sync HTTP calls."""
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_stream_dialog_ast_parses():
    ast.parse(_read("widgets/stream_dialog.py"))


# ─── Marker + helper ───


def test_marker_present():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L1-L5)" in src


def test_async_helper_defined():
    src = _read("widgets/stream_dialog.py")
    assert "def _stream_async_get(self, url, on_ok, on_err=None, timeout=3.0):" in src
    assert "def _stream_dialog_workers_set(self):" in src
    # Uses _DpdkApiWorker from dpdk_menu_actions (no new QThread subclass)
    assert "from traffic_client.dpdk_menu_actions import _DpdkApiWorker" in src
    assert '_DpdkApiWorker("GET"' in src


def test_helper_pins_worker_and_strips_meta_status():
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _stream_async_get(self, url, on_ok")
    body = src[_idx:_idx + 4500]
    # Worker pinned + released
    assert "_live.add(_w)" in body
    assert "_live.discard(_w)" in body
    # deleteLater on completion
    assert "_w.deleteLater()" in body
    # Reads _status_code from worker metadata
    assert '_status_code' in body


# ─── L1 ───


def test_l1_marker_and_worker_use():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L1)" in src
    _idx = src.index("v0.5.395 (audit dialogs L1)")
    body = src[_idx:_idx + 3000]
    assert "urlencode" in body
    assert "self._stream_async_get(" in body
    assert "_rec_ok" in body
    assert "_rec_err" in body


def test_l1_no_lingering_sync_get_in_recommend_body():
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _recommend_tx_cores")
    _end = src.index("def _show_dpdk_usage_guide", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "requests.get(" not in body, (
        "sync requests.get(...) still lives inside _recommend_tx_cores"
    )


# ─── L2 ───


def test_l2_marker_and_prefill_async():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L2)" in src
    _idx = src.index("v0.5.395 (audit dialogs L2)")
    body = src[_idx:_idx + 2500]
    # Callback shape
    assert "_pf_ok" in body
    assert "_pf_err" in body
    # Guard against clobbering manual edit while fetch was in flight
    assert 'if _cur == "" or _cur == "00:00:00:00:00:00"' in body


# ─── L3 ───


def test_l3_marker_and_lazy_cache():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L3)" in src
    _idx = src.index("def _fetch_rx_engine_advice(self, on_ready=None):")
    _end = src.index("def _refresh_rx_engine_advice", _idx)
    body = src[_idx:_end]
    # Cache hit → sync return
    assert "if self._rx_engine_advice_cache is not None:" in body
    # In-flight dedup
    assert "_rx_engine_advice_in_flight" in body
    # Strips worker metadata before caching
    assert 'if not k.startswith("_")' in body
    # Fires the async helper
    assert "self._stream_async_get(" in body


def test_l3_callers_pass_on_ready():
    src = _read("widgets/stream_dialog.py")
    _refresh_idx = src.index("def _refresh_rx_engine_advice(self):")
    _refresh_body = src[_refresh_idx:_refresh_idx + 1500]
    assert "on_ready=self._refresh_rx_engine_advice" in _refresh_body

    _apply_idx = src.index("def _maybe_apply_rx_engine_default(self, stream_data):")
    _apply_body = src[_apply_idx:_apply_idx + 2000]
    assert "on_ready=lambda:" in _apply_body
    assert "_maybe_apply_rx_engine_default(stream_data)" in _apply_body


def test_l3_no_lingering_sync_get_in_fetch_body():
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _fetch_rx_engine_advice(self, on_ready=None):")
    _end = src.index("def _refresh_rx_engine_advice", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "requests.get(" not in body and "_req.get(" not in body


# ─── L4 ───


def test_l4_marker_and_async_use():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L4)" in src
    _idx = src.index("v0.5.395 (audit dialogs L4)")
    body = src[_idx:_idx + 2500]
    assert "_dst_ok" in body
    assert "_dst_err" in body
    assert "self._stream_async_get(" in body


def test_l4_no_lingering_sync_get_in_autopopulate_dst_body():
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _on_autopopulate_dst_mac(self):")
    _end = src.index("def _refresh_mac_mismatch_warning", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "requests.get(" not in body and "_req.get(" not in body


# ─── L5 ───


def test_l5_marker_and_lazy_cache():
    src = _read("widgets/stream_dialog.py")
    assert "v0.5.395 (audit dialogs L5)" in src
    _idx = src.index("def _fetch_iface_mac_from_server(self, on_ready=None):")
    _end = src.index("def _fetch_rx_engine_advice", _idx)
    body = src[_idx:_end]
    assert "if self._cached_iface_mac:" in body
    assert "_iface_mac_fetch_in_flight" in body
    assert "self._stream_async_get(" in body


def test_l5_src_mac_pending_flag_terminates_loop():
    """The pending-sentinel prevents an infinite re-invocation loop
    when the async fetch permanently fails."""
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _on_autopopulate_src_mac(self):")
    _end = src.index("def _resolve_rx_iface_name", _idx)
    body = src[_idx:_end]
    assert "_last_click_pending_src_mac" in body
    # Terminal path shows the error label after the flag was set
    assert "self._mac_mismatch_label.show()" in body
    assert 'on_ready=self._on_autopopulate_src_mac' in body


def test_l5_no_lingering_sync_get_in_fetch_body():
    src = _read("widgets/stream_dialog.py")
    _idx = src.index("def _fetch_iface_mac_from_server(self, on_ready=None):")
    _end = src.index("def _fetch_rx_engine_advice", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "requests.get(" not in body and "_req.get(" not in body


# ─── version guard ───


def test_pyproject_at_least_0595():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 395)


# ─── regression guards ───


def test_v0394_k1_rfc2544_worker_intact():
    src = _read("widgets/rfc2544_dialog.py")
    assert "class _RfcHttpWorker(QThread):" in src


def test_v0394_k5_stop_stream_by_id_ok_gate_intact():
    src = _read("traffic_client/stream_logic.py")
    assert "v0.5.394 (audit streams K5)" in src


def test_v0393_j1_server_counter_trust_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.393 (audit streams J1)" in src


def test_v0392_h1_row_reresolve_intact():
    src = _read("traffic_client/stream_control.py")
    assert "v0.5.392 (audit streams H1)" in src
