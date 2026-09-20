"""v0.5.355 — RDMA/traffic HIGH bundle: three correctness fixes.

Post-v0.5.354 fresh audit surfaced three HIGH-severity defects in
the RDMA + traffic-gen paths. Each fix touches an independent file;
all three carry the `v0.5.355 (audit ...)` marker for grep-audit
traceability.

A1 — RoCEv2 + ibperf branch tracker leak
    `multithreaded_traffic_gen.py::start_traffic` at the
    `l4_sel == "RoCEv2" and use_ibperf=True` branch called
    `start_ibperf_server` and `return`ed without touching the
    tracker, wiring stop_event, or reacting to the shim's
    returned status. Tracker row + RX sniffer leaked for the
    process lifetime; a subsequent Stop couldn't reach the
    perftest daemon (nothing polled stop_event); restart of the
    same stream added a duplicate row.
    Fix: route through v0.5.141's `register_perftest_with_tracker`
    so stop_event → `stop_perftest(job_id)` via the poll thread
    and the tracker row clears when `finished_at` populates. Also
    react to the shim's status so a start failure marks the row
    stopped instead of leaving it "running" forever.

A2 — ibperf shim passes msg_size as a string
    `utils/rdma_perf.py::start_ibperf_server` sent
    `"msg_size": "32K"` to perftest. perftest's `-s` only accepts
    a decimal (strtol) — operator got a 32-BYTE test (or a hard
    reject on stricter builds). The caller in
    multithreaded_traffic_gen.py (see A1) ignored the returned
    dict, so failure was invisible client-side.
    Fix: pass an integer byte count (`32 * 1024`) so historical
    intent matches actual behavior.

A3 — `cleanup_test_config` drops record on any-entry error
    `utils/rdma_test_ifaces.py::cleanup_test_config` never re-
    appended a matched record to `keep` when any `ip addr del`
    failed. State silently forgot the record + its still-live
    IPs → later cleanup pass had no memory to retry against, and
    a preflight run while the iface was flapped/down became
    unrecoverable.
    Fix: build a per-record `_surviving` list of entries that
    FAILED to delete; keep the record with only those entries so
    a retry pass can find them; drop the record entirely only
    when every entry cleaned successfully.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel):
    return (_REPO / rel).read_text()


def test_all_v0_5_355_markers_present():
    assert "v0.5.355 (audit rdma-ibperf-tracker-leak)" in _read("multithreaded_traffic_gen.py")
    assert "v0.5.355 (audit rdma-ibperf-msg-size-string)" in _read("utils/rdma_perf.py")
    assert "v0.5.355 (audit rdma-cleanup-drop-on-err)" in _read("utils/rdma_test_ifaces.py")


# --- A1: RoCEv2 + ibperf tracker wiring ---


def test_A1_ibperf_branch_captures_shim_result():
    """The branch must capture start_ibperf_server's return value —
    pre-fix, the call site was a bare statement, so the shim's
    error status was invisible."""
    src = _read("multithreaded_traffic_gen.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-tracker-leak)")
    body = src[marker_idx:marker_idx + 5000]
    # Result variable is assigned + checked, not discarded.
    assert "_shim_result = start_ibperf_server(" in body
    assert '_shim_result.get("status"' in body


def test_A1_ibperf_branch_calls_on_stream_stopped_on_error():
    """When the shim reports non-'started' status, the branch
    MUST call `on_stream_stopped` so the tracker row doesn't
    linger forever."""
    src = _read("multithreaded_traffic_gen.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-tracker-leak)")
    body = src[marker_idx:marker_idx + 5000]
    # Error path calls on_stream_stopped.
    assert 'on_stream_stopped(interface, stream_id, reason="error")' in body


def test_A1_ibperf_branch_registers_with_tracker():
    """Success path MUST hand off to
    `register_perftest_with_tracker` — that's the helper that
    wires stop_event → stop_perftest(job_id) via the poll
    thread. Without this, Stop can't reach the perftest daemon."""
    src = _read("multithreaded_traffic_gen.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-tracker-leak)")
    body = src[marker_idx:marker_idx + 5000]
    assert "register_perftest_with_tracker(" in body
    assert 'from utils.rdma_stream_engine import register_perftest_with_tracker' in body


def test_A1_ibperf_branch_passes_stop_event_and_job_id():
    """Both must be passed to the tracker registration — job_id
    for stop_perftest, stop_event for the poll thread's cancel
    check. If either is missing the wiring is dead."""
    src = _read("multithreaded_traffic_gen.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-tracker-leak)")
    body = src[marker_idx:marker_idx + 5000]
    # register_perftest_with_tracker takes them as kwargs.
    assert "stop_event=stop_event" in body
    assert 'job_id=_shim_result["job_id"]' in body


def test_A1_ibperf_branch_stops_shim_job_if_tracker_wiring_raises():
    """A raise inside `register_perftest_with_tracker` (e.g.
    import failure) must not leave the shim's perftest daemon
    orphaned. The except handler must call `stop_perftest` on
    the job_id before marking the stream stopped."""
    src = _read("multithreaded_traffic_gen.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-tracker-leak)")
    body = src[marker_idx:marker_idx + 5000]
    assert "from utils.rdma_perf import stop_perftest" in body
    assert "stop_perftest(_shim_result[" in body


# --- A2: msg_size as integer ---


def test_A2_msg_size_is_integer_not_string():
    """Pre-fix `"msg_size": "32K"` — perftest -s can't parse that.
    Now must be an integer byte count. Structural: no `"32K"`
    string literal anywhere near the shim's opts dict, and
    `msg_size` is followed by an integer expression."""
    src = _read("utils/rdma_perf.py")
    fn_idx = src.index("def start_ibperf_server(")
    # `start_ibperf_server` sits at the bottom of the file; no `def`
    # follows it. Take a bounded window so the test doesn't require
    # a next-def sentinel.
    body = src[fn_idx:fn_idx + 4000]
    # The old string literal must be gone.
    assert '"msg_size": "32K"' not in body, (
        'msg_size was still literally "32K" — perftest -s cannot '
        'parse that. Must be an integer.'
    )
    # The new integer value (32 * 1024 = 32768) must be present.
    assert "32 * 1024" in body or "32768" in body


def test_A2_marker_notes_perftest_strtol_reason():
    """The marker comment must explain WHY the string form
    broke — future maintainers need to see that perftest's -s
    only accepts a decimal via strtol, otherwise someone might
    "helpfully" restore the string form for readability."""
    src = _read("utils/rdma_perf.py")
    marker_idx = src.index("v0.5.355 (audit rdma-ibperf-msg-size-string)")
    body = src[marker_idx:marker_idx + 1500]
    assert "strtol" in body or "decimal" in body


# --- A3: cleanup_test_config retains records with failed entries ---


def test_A3_cleanup_tracks_surviving_entries():
    """The fix hinges on a per-record `_surviving` list — entries
    whose `ip addr del` failed. Structural check that the
    variable exists and gets populated when an entry errors."""
    src = _read("utils/rdma_test_ifaces.py")
    fn_idx = src.index("def cleanup_test_config(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "_surviving" in body
    # Populated when the entry failed.
    assert "_surviving.append(entry)" in body


def test_A3_cleanup_keeps_record_when_any_entry_survived():
    """When at least one entry failed, the record must be
    re-appended to `keep` with only the surviving (failed) entries.
    Structural: an `if _surviving:` branch that appends a copy of
    the record with `applied` replaced by `_surviving`."""
    src = _read("utils/rdma_test_ifaces.py")
    fn_idx = src.index("def cleanup_test_config(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "if _surviving:" in body
    # Retained record's applied list becomes the surviving entries.
    assert '_retained["applied"] = _surviving' in body
    # And gets appended to keep.
    assert "keep.append(_retained)" in body


def test_A3_cleanup_still_drops_fully_cleaned_records():
    """A record whose entries ALL cleaned successfully must not
    stay in state — that's how the function has always signaled
    "clean state". Structural: nothing in the fix should
    unconditionally keep matched records."""
    src = _read("utils/rdma_test_ifaces.py")
    fn_idx = src.index("def cleanup_test_config(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    # No blanket `keep.append(rec)` on the state_id-matches path.
    # `keep.append(rec)` should only appear on the state_id-does-
    # not-match short-circuit, and the surviving-record branch
    # uses `keep.append(_retained)`.
    marker_idx = body.index("v0.5.355 (audit rdma-cleanup-drop-on-err)")
    fix_body = body[marker_idx:]
    assert "keep.append(rec)" not in fix_body, (
        "The fix must not unconditionally keep every matched "
        "record — cleaned records must still be dropped."
    )


# --- AST parse safety on all three touched files ---


def test_touched_files_ast_parse():
    import ast
    for rel in (
        "multithreaded_traffic_gen.py",
        "utils/rdma_perf.py",
        "utils/rdma_test_ifaces.py",
    ):
        ast.parse(_read(rel))


# --- Regression guards ---


def test_v0_5_141_register_perftest_with_tracker_still_exists():
    """A1's fix relies on v0.5.141's shared tracker helper. If
    that helper ever gets removed, A1 becomes an import error
    at runtime."""
    src = _read("utils/rdma_stream_engine.py")
    assert "def register_perftest_with_tracker(" in src


def test_start_ibperf_server_still_returns_dict_with_job_id():
    """A1 depends on the shim returning `{"status": "started",
    "job_id": ...}` on success — pre-existing contract. If a
    future refactor changes the shape without updating A1, the
    tracker wiring will KeyError."""
    src = _read("utils/rdma_perf.py")
    fn_idx = src.index("def start_ibperf_server(")
    # `start_ibperf_server` sits at the bottom of the file; no `def`
    # follows it. Take a bounded window so the test doesn't require
    # a next-def sentinel.
    body = src[fn_idx:fn_idx + 4000]
    # start_perftest returns a dict already exposing job_id;
    # the shim propagates it verbatim.
    assert "start_perftest(" in body
    assert "job_id" in body


def test_stop_perftest_still_defined():
    """A1's tracker-wiring failure path calls `stop_perftest`."""
    src = _read("utils/rdma_perf.py")
    assert "def stop_perftest(" in src
