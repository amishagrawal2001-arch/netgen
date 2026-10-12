"""v0.5.426 — Unreachable-server UI-freeze cleanup (NN1 + NN2 + NN3 + NN4 + NN5).

Operator report: "when app not able to connect server, it hangs/
freezes intermittently" — specifically on (a) clicking something in
the Server tree / Devices tab, and (b) switching between TGs or
tabs. The audit sweep found 15+ sync-HTTP sites on UI-thread
handlers; this slice fixes the 5 most-hot and user-visible.

  NN1  `_initialize_arp_status_from_database` chain in
       widgets/devices_tab.py — the per-device ARP probe loop called
       `_check_individual_arp_resolution` (sync `requests.get(
       timeout=(3, 15))`) on the UI thread. Fires on EVERY TG click
       via `_on_server_selection_changed_combined → update_device_
       table → QTimer.singleShot(0, _initialize_arp_status_from_
       database)`. On N running devices with offline server =
       N × 3 s UI freeze. Exact match for the operator's complaint.
       Fix: new `_ArpInitWorker(QThread)` emits per-device results
       to a slot that re-matches rows by device_name (defensive
       against mid-flight table rebuilds); prior workers are
       cancelled when the user clicks another TG.

  NN2  `_toggle_protocol_action` BGP/OSPF/ISIS Start/Stop button:
       pre-fix `requests.post(timeout=10)` per device synchronously.
       N devices × 10 s freeze on an unreachable server. Fix:
       build job list on UI thread, dispatch to `_ProtoToggleWorker
       (QThread)`, deliver `(success_count, total)` to a slot that
       runs the post-loop table refresh + monitoring start/stop.

  NN3  BGP / OSPF / ISIS "Refresh" button sync force-check POSTs
       (and the selective GET loop in BGP) — all three protocols
       had the same shape: a `requests.post(timeout=15)` on the UI
       thread froze the client for 15 s per click when the server
       was unreachable. Fix: three per-protocol async workers
       (`_BgpRefreshWorker`, `_OspfForceCheckWorker`,
       `_IsisForceCheckWorker`), each re-renders its table from
       cache immediately AND re-renders a second time when the
       probe completes.

  NN4  `widgets/preflight_bar.py:PreflightBar.refresh` sync
       `requests.get(timeout=5)` fired from the 60-second QTimer,
       the manual Refresh button, and every Apply-triggered
       `kick_refresh()`. 5 s UI freeze per trigger on an unreachable
       server. Fix: `_PreflightFetchWorker(QThread)` + slot calls
       `_apply_summary` on the main thread.

  NN5  `traffic_client/server_section.py:_set_interface_admin_state`
       sync `requests.post(timeout=5)` for the right-click → Online/
       Offline action. 5 s UI freeze per click. Fix: `_AdminStateWorker
       (QThread)` + slot preserves the full v0.5.422 JJ1 cache
       mutation + JJ2 three staggered single-iface refetches. The
       JJ2 closure's inner refetch also moves into a nested
       QThread so a slow /api/interfaces GET during the 500ms/3s/8s
       staggered window doesn't freeze either.

All five fixes mirror the KK1 / K1 / L1-L5 / S1-S5 QThread-with-
done-signal pattern already established in prior slices. Keepalive
registered via `utils.qthread_keepalive.keep` on each worker to
dodge the PyQt5 5.15 GC race.
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


# ─── AST sanity (all 6 touched files) ───


def test_devices_tab_ast_parses():
    ast.parse(_read("widgets/devices_tab.py"))


def test_server_section_ast_parses():
    ast.parse(_read("traffic_client/server_section.py"))


def test_preflight_bar_ast_parses():
    ast.parse(_read("widgets/preflight_bar.py"))


def test_bgp_tab_ast_parses():
    ast.parse(_read("utils/devices_tab_bgp.py"))


def test_ospf_tab_ast_parses():
    ast.parse(_read("utils/devices_tab_ospf.py"))


def test_isis_tab_ast_parses():
    ast.parse(_read("utils/devices_tab_isis.py"))


# ─── NN1: ARP initializer loop → QThread ───


def test_nn1_marker_present():
    assert "v0.5.426 (audit stream-NN1)" in _read("widgets/devices_tab.py")


def test_nn1_arp_init_worker_defined_and_wired():
    """`_initialize_arp_status_from_database` must define
    `_ArpInitWorker(QThread)` and start it. The pre-fix shape (sync
    `_check_individual_arp_resolution` directly in the loop) must
    not call `self.set_status_icon` from the top-level body
    anymore — the slot calls it."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def _initialize_arp_status_from_database")
    _end = src.index("def _on_individual_arp_result", _idx)
    body = src[_idx:_end]
    assert "class _ArpInitWorker(QThread):" in body
    assert "result = pyqtSignal(" in body
    assert "worker.start()" in body
    assert "from utils.qthread_keepalive import keep" in body


def test_nn1_arp_init_worker_cancels_on_prior():
    """When the user rapid-fires TG clicks, each click starts a
    new worker — the previous one must be cancelled so late
    results from the stale worker don't paint rows that no longer
    belong to the current TG."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def _initialize_arp_status_from_database")
    _end = src.index("def _on_individual_arp_result", _idx)
    body = src[_idx:_end]
    assert "prev = getattr(self, \"_arp_init_worker\", None)" in body
    assert "prev.cancelled = True" in body


# ─── NN2: _toggle_protocol_action → QThread ───


def test_nn2_marker_present():
    assert "v0.5.426 (audit stream-NN2)" in _read("widgets/devices_tab.py")


def test_nn2_proto_toggle_worker_defined_and_wired():
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def _toggle_protocol_action")
    # Scan forward to the next top-of-class method.
    _after = src[_idx:]
    _end_rel = _after.find("\n    def ", 100)
    body = _after[: _end_rel if _end_rel > 0 else 20000]
    assert "class _ProtoToggleWorker(QThread):" in body
    assert "done = pyqtSignal(int, int)" in body
    assert "_tw.start()" in body
    # Pre-fix sync requests.post is gone from the loop body
    _live = _strip_comments(body)
    # The worker's run() still contains it (that's correct — it's
    # the per-device post INSIDE the thread). But the UI-thread
    # loop section (BEFORE `class _ProtoToggleWorker`) must not.
    _pre_worker = body[: body.index("class _ProtoToggleWorker(QThread):")]
    _pre_worker_live = _strip_comments(_pre_worker)
    assert "requests.post(" not in _pre_worker_live, (
        "sync requests.post still runs before the worker — UI will freeze"
    )


# ─── NN3: refresh buttons async in BGP / OSPF / ISIS ───


def test_nn3_marker_present_in_all_three():
    assert "v0.5.426 (audit stream-NN3)" in _read("utils/devices_tab_bgp.py")
    assert "v0.5.426 (audit stream-NN3)" in _read("utils/devices_tab_ospf.py")
    assert "v0.5.426 (audit stream-NN3)" in _read("utils/devices_tab_isis.py")


def test_nn3_bgp_refresh_async():
    src = _read("utils/devices_tab_bgp.py")
    _idx = src.index("def refresh_bgp_status")
    _after = src[_idx:]
    _end = _after.find("\n    def ", 100)
    body = _after[: _end if _end > 0 else 20000]
    assert "class _BgpRefreshWorker(QThread):" in body
    assert "_w.start()" in body
    _live = _strip_comments(body)
    # Pre-fix sync GET loop + force-check POST must be gone from
    # the UI-thread body (inside the worker's run() is fine — but
    # that's inside the `class _BgpRefreshWorker`).
    _pre_worker = body[: body.index("class _BgpRefreshWorker(QThread):")]
    _pw_live = _strip_comments(_pre_worker)
    assert "requests.get(" not in _pw_live
    assert "requests.post(" not in _pw_live


def test_nn3_ospf_refresh_async():
    src = _read("utils/devices_tab_ospf.py")
    _idx = src.index("def refresh_ospf_status")
    _after = src[_idx:]
    _end = _after.find("\n    def ", 100)
    body = _after[: _end if _end > 0 else 20000]
    assert "class _OspfForceCheckWorker(QThread):" in body
    _pre_worker = body[: body.index("class _OspfForceCheckWorker(QThread):")]
    _live = _strip_comments(_pre_worker)
    assert "requests.post(" not in _live


def test_nn3_isis_refresh_async():
    src = _read("utils/devices_tab_isis.py")
    _idx = src.index("def refresh_isis_status")
    _after = src[_idx:]
    _end = _after.find("\n    def ", 100)
    body = _after[: _end if _end > 0 else 20000]
    assert "class _IsisForceCheckWorker(QThread):" in body
    _pre_worker = body[: body.index("class _IsisForceCheckWorker(QThread):")]
    _live = _strip_comments(_pre_worker)
    assert "requests.post(" not in _live


# ─── NN4: preflight_bar.refresh → QThread ───


def test_nn4_marker_present():
    assert "v0.5.426 (audit stream-NN4)" in _read("widgets/preflight_bar.py")


def test_nn4_preflight_fetch_worker_defined():
    src = _read("widgets/preflight_bar.py")
    _idx = src.index("def refresh(self) -> None:")
    _after = src[_idx:]
    _end = _after.find("\n    def ", 100)
    body = _after[: _end if _end > 0 else 10000]
    assert "class _PreflightFetchWorker(QThread):" in body
    _pre_worker = body[: body.index("class _PreflightFetchWorker(QThread):")]
    # Strip docstring (triple-quoted block) + # comments. The NN4
    # rationale docstring deliberately references the pre-fix
    # `requests.get(timeout=5)` pattern, which would false-trip a
    # naive scan of the pre-worker body.
    _parts = _pre_worker.split('"""')
    _no_doc = _parts[0] + "".join(_parts[2:]) if len(_parts) >= 3 else _pre_worker
    _live = _strip_comments(_no_doc)
    assert "requests.get(" not in _live


# ─── NN5: _set_interface_admin_state → QThread ───


def test_nn5_marker_present():
    assert "v0.5.426 (audit stream-NN5)" in _read("traffic_client/server_section.py")


def test_nn5_admin_state_worker_defined_and_wired():
    """`_set_interface_admin_state` must define `_AdminStateWorker
    (QThread)`, with its POST inside `run()`. The sync `_requests.
    post` in the UI-thread section must be gone."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    assert "class _AdminStateWorker(QThread):" in body
    assert "done = pyqtSignal(bool, int, object, str)" in body
    assert "_worker.start()" in body
    # The pre-fix sync `_requests.post(url, json=` on the UI thread
    # body (before the class def) must be gone.
    _pre_worker = body[: body.index("class _AdminStateWorker(QThread):")]
    _live = _strip_comments(_pre_worker)
    assert "_requests.post(url, json=" not in _live, (
        "sync _requests.post still runs before the worker"
    )


def test_nn5_preserves_jj1_cache_mutation_in_slot():
    """The v0.5.422 JJ1 in-place cache mutation must still happen
    on the HTTP-200 path — now inside the `_on_done` slot."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _set_interface_admin_state")
    _end = src.index("def _on_server_selection_changed_combined", _idx)
    body = src[_idx:_end]
    assert '_ent["operstate"] = operstate' in body
    assert "def _refresh_this_iface_only(" in body


def test_nn5_jj2_closure_refetch_is_itself_async():
    """The JJ2 single-iface refetch closure also moves to a nested
    QThread (`_IfaceRefetchWorker`). Pre-NN5 it ran `requests.get(
    timeout=3)` synchronously via `QTimer.singleShot`, so an
    unreachable server stalled the UI three times (500ms, 3s, 8s)
    per Online/Offline click. The staggered timer fires are
    preserved."""
    src = _read("traffic_client/server_section.py")
    _idx = src.index("def _refresh_this_iface_only")
    _end = src.index("QTimer.singleShot(500, _refresh_this_iface_only)", _idx)
    body = src[_idx:_end]
    assert "class _IfaceRefetchWorker" in body
    assert "QThread" in body
    # The staggered schedule still fires 500ms/3s/8s after the slot.
    after = src[_end:_end + 500]
    assert "QTimer.singleShot(500, _refresh_this_iface_only)" in after
    assert "QTimer.singleShot(3000, _refresh_this_iface_only)" in after
    assert "QTimer.singleShot(8000, _refresh_this_iface_only)" in after


# ─── version guard ───


def test_pyproject_at_least_0626():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 426)


# ─── regression guards for prior slices ───


def test_v0425_mm1_full_ospf_teardown_intact():
    src = _read("utils/ospf.py")
    assert "def _full_ospf_teardown(device_id):" in src


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
