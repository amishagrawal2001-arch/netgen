"""v0.5.424 — Clear-All leaves OSPF table stuck "Up"/green while BGP + device chips go red (LL1 + LL2).

Operator reproduction (post-v0.5.423 KK1 upgrade):
Tools → Clear All Devices → typed CLEAR → device + BGP chips go red
as expected, but OSPF rows stay green/"Up" indefinitely. Added-after
BGP in the dev timeline so the client's OSPF plumbing never got the
same post-remove refresh wiring.

Two interlocking bugs:

LL1  widgets/devices_tab.py `reload_devices_from_server` ghost-prune
     is doubly broken. The v0.5.387 C5 block gates on
     `isinstance(_cache, list)` — but `all_devices` is a DICT
     (iface → [devices]) everywhere else in the client (:4525,
     :4759, update_ospf_table). The prune has NEVER actually run.
     On top of that, the "safety rail" `and merged_seen_ids` treats
     "server returned 200 with []" (Clear-All!) the same as "server
     unreachable" — so Clear-All never prunes even once the dict
     check is fixed. LL1 switches to `isinstance(_cache, dict)`,
     walks `all_devices.items()`, drops ghost devices + empty iface
     buckets, and gates on a new `any_server_ok` flag set when a
     server responds 200 cleanly (independent of row count).

LL2  traffic_client/main.py `clear_all_devices_on_server` success
     slot only called `reload_devices_from_server()`, which refreshes
     only the device table. The OSPF table has its own painter
     (`update_ospf_table`) that iterates `all_devices`. With LL1
     fixing the cache emptying, LL2 kicks the per-table painters
     (OSPF, BGP, ISIS) so all three tables clear together instead
     of waiting for OSPF's 20s monitor tick.
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


def test_devices_tab_ast_parses():
    ast.parse(_read("widgets/devices_tab.py"))


def test_main_py_ast_parses():
    ast.parse(_read("traffic_client/main.py"))


# ─── LL1: ghost-prune dict-correct + any_server_ok gate ───


def test_ll1_marker_present():
    assert "v0.5.424 (audit stream-LL1)" in _read("widgets/devices_tab.py")


def test_ll1_any_server_ok_tracked():
    """A new `any_server_ok` flag must be set when a server response
    comes back without `worker.error`. Pre-fix there was no way to
    distinguish server-returned-empty from server-errored, which was
    the whole reason Clear-All never pruned."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def reload_devices_from_server")
    _end = src.index("def _ensure_sse_worker", _idx)
    body = src[_idx:_end]
    assert "any_server_ok = False" in body
    assert "any_server_ok = True" in body


def test_ll1_prune_uses_isinstance_dict_not_list():
    """The ghost-prune gate must check `isinstance(_cache, dict)` —
    `all_devices` is a dict keyed by iface, so the pre-fix `list`
    check never matched and the prune never fired."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def reload_devices_from_server")
    _end = src.index("def _ensure_sse_worker", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    # Live code uses the dict check.
    assert "isinstance(_cache, dict)" in _live
    # The pre-fix list check is gone from live code (comment may
    # mention it in LL1's rationale block — that's fine).
    assert "isinstance(_cache, list)" not in _live, (
        "pre-fix list check still in live code — LL1 regressed"
    )


def test_ll1_prune_gated_on_any_server_ok_not_merged_seen_ids():
    """The prune guard must be `any_server_ok` not `merged_seen_ids`
    — otherwise Clear-All (which returns `[]`) never prunes."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def reload_devices_from_server")
    _end = src.index("def _ensure_sse_worker", _idx)
    body = src[_idx:_end]
    _live = _strip_comments(body)
    assert "isinstance(_cache, dict) and any_server_ok" in _live


def test_ll1_prune_walks_dict_buckets():
    """The prune must iterate `_cache.items()` (dict) and drop empty
    iface buckets after sweeping — otherwise the client UI still
    hands buckets with no devices to downstream iterators and some
    code paths NullOp on an empty bucket."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def reload_devices_from_server")
    _end = src.index("def _ensure_sse_worker", _idx)
    body = src[_idx:_end]
    assert "for _iface, _bucket in list(_cache.items()):" in body
    assert "_empty_ifaces" in body
    assert "_cache.pop(_ei, None)" in body


def test_ll1_prune_preserves_needs_apply_and_is_new():
    """In-flight local edits (`_needs_apply`) and freshly-added rows
    (`_is_new`) must be kept even when not seen on the server, so a
    reload during a mid-edit doesn't nuke the operator's work."""
    src = _read("widgets/devices_tab.py")
    _idx = src.index("def reload_devices_from_server")
    _end = src.index("def _ensure_sse_worker", _idx)
    body = src[_idx:_end]
    assert "_dev.get(\"_needs_apply\") or _dev.get(\"_is_new\")" in body


# ─── LL2: clear-all slot kicks protocol-table painters ───


def test_ll2_marker_present():
    assert "v0.5.424 (audit stream-LL2)" in _read("traffic_client/main.py")


def test_ll2_slot_refreshes_ospf_bgp_isis_tables():
    """The Clear-All success slot must call update_ospf_table,
    update_bgp_table, and update_isis_table (where present) in
    addition to reload_devices_from_server, so the protocol tables
    don't lag behind the device table."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def clear_all_devices_on_server")
    body = src[_idx:_idx + 8000]
    assert "update_ospf_table" in body
    assert "update_bgp_table" in body
    assert "update_isis_table" in body
    # Each must be reached via `getattr(..., _meth, None)` + callable
    # guard so a client build without one of the methods doesn't
    # crash.
    assert "getattr(self.devices_tab, _meth, None)" in body
    assert "callable(_fn)" in body


def test_ll2_table_refresh_sits_in_on_done_slot_not_sync_path():
    """The protocol-table refresh must be inside `_on_done` (the
    QThread result slot added by KK1), not in the pre-worker sync
    path — otherwise it runs before the server has actually cleared
    the devices and paints green from pre-clear state."""
    src = _read("traffic_client/main.py")
    _idx = src.index("def _on_done(")
    _end = _idx + 4000
    body = src[_idx:_end]
    assert "update_ospf_table" in body
    assert "update_bgp_table" in body


# ─── Version guard ───


def test_pyproject_at_least_0624():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 424)


# ─── regression guards ───


def test_v0423_kk1_async_worker_intact():
    src = _read("traffic_client/main.py")
    assert "v0.5.423 (audit stream-KK1)" in src
    assert "class _ClearAllWorker(QThread):" in src


def test_v0422_jj2_closure_intact():
    src = _read("traffic_client/server_section.py")
    assert "def _refresh_this_iface_only(" in src


def test_v0421_ii1_col1_match_intact():
    src = _read("traffic_client/server_section.py")
    assert 'srv_label = (srv_item.text(1) or "").lower()' in src
