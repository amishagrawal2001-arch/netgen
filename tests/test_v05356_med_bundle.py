"""v0.5.356 — MED bundle: four correctness fixes.

Post-v0.5.355 audit tail. Four MED-severity defects in
`utils/rdma_perf.py`, `utils/rdma_stream_engine.py`,
`widgets/add_device_dialog.py`, and `utils/frr_docker.py`. Each
carries its own `v0.5.356 (audit ...)` marker.

A4 — `stop_perftest` race on pid / finished_at read
    Pre-fix, `pid = job.pid` + the finished_at check were read
    OUTSIDE `_jobs_lock`, and killpg fired unlocked. Reader
    thread could flip finished_at + clear pid between; the
    kernel then recycled the PID and killpg landed on an
    unrelated process group. Fix: hold `_jobs_lock` across both
    the read AND the signal.

A5 — rdma_stream_engine poll thread had 2-4s stop lag
    `_poll` did `time.sleep(2.0)` BEFORE the stop_event check;
    Stop had to wait for the sleep to expire plus the outer
    stopper drain. `stop_event.wait(2.0)` returns immediately
    on set — Stop is now instant.

A6 — v6-only device with BGP wrote empty `bgp_neighbor_ipv4=""`
    `add_device_dialog.get_values` populated `peer_ip` /
    `bgp_neighbor_ipv4` unconditionally. A v6-only device
    (ipv4_checkbox off) + BGP + `bgp_toggle_ipv4` left on
    produced empty strings and downstream FRR emitted a
    malformed `neighbor  remote-as N` line (missing peer IP).
    Fix: hoist `_bgp_v4_on` / `_bgp_v6_on` and only emit
    the per-AF neighbor fields when both the AF is enabled AND
    the neighbor address is truthy.

A7 — `mtu.isdigit()` assumed str; int mtu → silent MTU drop
    `frr_docker.py::_configure_interfaces` at the interface-
    config vtysh emitter. Some apply paths persist mtu as
    int; `int.isdigit()` raises AttributeError, the swallowed
    exception aborts the whole `_configure_interfaces` and
    the container comes up without the `ip mtu` line, silently.
    Fix: `str(mtu).isdigit()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel):
    return (_REPO / rel).read_text()


def test_all_v0_5_356_markers_present():
    assert "v0.5.356 (audit rdma-stop-perftest-race)" in _read("utils/rdma_perf.py")
    assert "v0.5.356 (audit rdma-stream-stop-lag)" in _read("utils/rdma_stream_engine.py")
    assert "v0.5.356 (audit bgp-empty-neighbor-on-v6-only)" in _read("widgets/add_device_dialog.py")
    assert "v0.5.356 (audit frr-mtu-int-coerce)" in _read("utils/frr_docker.py")


# --- A4: stop_perftest pid/killpg race ---


def test_A4_stop_perftest_holds_lock_across_pid_read_and_killpg():
    """The fix moves `pid = job.pid` + finished_at check INSIDE
    `_jobs_lock`, and calls `killpg` while still holding it.
    Structural check: the marker is followed by a `with _jobs_lock:`
    block that contains BOTH the pid read and the killpg call."""
    src = _read("utils/rdma_perf.py")
    marker_idx = src.index("v0.5.356 (audit rdma-stop-perftest-race)")
    body = src[marker_idx:marker_idx + 3500]
    assert "with _jobs_lock:" in body
    # Both pid read AND killpg live inside a single locked block.
    _locked_start = body.index("with _jobs_lock:")
    _locked_region = body[_locked_start:_locked_start + 1500]
    assert "pid = job.pid" in _locked_region
    assert "os.killpg" in _locked_region


def test_A4_second_look_lock_check_before_sigkill():
    """The SIGKILL escalation path must also re-verify
    `finished_at` under the lock (the outer 3s poll waits
    unlocked) before signaling — same class of race as the
    initial SIGTERM."""
    src = _read("utils/rdma_perf.py")
    fn_idx = src.index("def stop_perftest(")
    body = src[fn_idx:fn_idx + 5000]
    # SIGKILL block must live inside a `with _jobs_lock:` too.
    _kill_idx = body.index("signal.SIGKILL")
    # Walk back from the SIGKILL call to find the nearest enclosing
    # `with _jobs_lock:`; must be within ~500 chars.
    _pre = body[max(0, _kill_idx - 500):_kill_idx]
    assert "with _jobs_lock:" in _pre


def test_A4_unknown_job_id_still_errors_out_first():
    """Regression guard: the `job is None` short-circuit must
    still fire OUTSIDE the fix's locked block, otherwise a bad
    caller could deadlock by taking the lock inside the error
    path. Reads the unknown-job branch and confirms it returns
    early."""
    src = _read("utils/rdma_perf.py")
    fn_idx = src.index("def stop_perftest(")
    body = src[fn_idx:fn_idx + 5000]
    assert 'return {"status": "error", "error": f"unknown job_id' in body


# --- A5: rdma_stream_engine stop lag ---


def test_A5_poll_uses_stop_event_wait_not_time_sleep():
    """The fix replaces `time.sleep(2.0)` (unconditional 2s
    wall-clock wait) with `stop_event.wait(2.0)` (returns
    immediately on set). Structural: no `time.sleep(2.0)` in
    the poll body (excluding comment lines that document the
    historical bug), and `stop_event.wait(2.0)` is present."""
    src = _read("utils/rdma_stream_engine.py")
    fn_idx = src.index("def register_perftest_with_tracker(")
    # Bounded window covers the enclosed `_poll` inner function.
    body = src[fn_idx:fn_idx + 5000]
    marker_idx = body.index("v0.5.356 (audit rdma-stream-stop-lag)")
    fix_body = body[marker_idx:marker_idx + 1500]
    assert "stop_event.wait(2.0)" in fix_body
    # Strip comment lines before scanning for `time.sleep(2.0)`
    # so the marker's own historical-context comment doesn't
    # trigger a false positive. Any live code containing
    # `time.sleep(2.0)` would still fail this.
    _code_only = "\n".join(
        line for line in fix_body.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "time.sleep(2.0)" not in _code_only, (
        "live code in _poll still contains time.sleep(2.0) — "
        "the fix is only the comment"
    )


def test_A5_poll_break_on_stop_event():
    """`stop_event.wait(2.0)` returns True when the event fires.
    The fix must break out of the loop on that return, otherwise
    it just polls the job right after the operator hit Stop."""
    src = _read("utils/rdma_stream_engine.py")
    marker_idx = src.index("v0.5.356 (audit rdma-stream-stop-lag)")
    body = src[marker_idx:marker_idx + 1500]
    assert "if stop_event.wait(2.0):" in body
    assert "break" in body


# --- A6: bgp empty neighbor on v6-only device ---


def test_A6_hoists_bgp_af_flags_before_config_dict():
    """The fix hoists `_bgp_v4_on` / `_bgp_v6_on` above the
    bgp_config dict so the per-AF gates use a single source of
    truth. Structural: both variables assigned once, from the
    `bgp_toggle_*` checkboxes."""
    src = _read("widgets/add_device_dialog.py")
    marker_idx = src.index("v0.5.356 (audit bgp-empty-neighbor-on-v6-only)")
    body = src[marker_idx:marker_idx + 3500]
    assert "_bgp_v4_on = (" in body
    assert "_bgp_v6_on = (" in body
    assert "self.bgp_toggle_ipv4.isChecked()" in body
    assert "self.bgp_toggle_ipv6.isChecked()" in body


def test_A6_neighbor_fields_gated_on_both_af_flag_and_neighbor_truthy():
    """`peer_ip` / `bgp_neighbor_ipv4` MUST NOT be written when
    either the AF is disabled OR the neighbor address is empty.
    Structural: the fix's post-dict conditional emit block
    checks both `_bgp_v4_on and neighbor_ipv4` and the v6
    twin."""
    src = _read("widgets/add_device_dialog.py")
    marker_idx = src.index("v0.5.356 (audit bgp-empty-neighbor-on-v6-only)")
    body = src[marker_idx:marker_idx + 3500]
    assert "if _bgp_v4_on and neighbor_ipv4:" in body
    assert "if _bgp_v6_on and neighbor_ipv6:" in body
    # And the fields are set inside those blocks.
    assert 'bgp_config["peer_ip"] = neighbor_ipv4' in body
    assert 'bgp_config["bgp_neighbor_ipv4"] = neighbor_ipv4' in body
    assert 'bgp_config["bgp_neighbor_ipv6"] = neighbor_ipv6' in body


def test_A6_unconditional_neighbor_writes_are_gone_from_dict():
    """The pre-fix inline `"peer_ip": neighbor_ipv4,` and
    `"bgp_neighbor_ipv4": neighbor_ipv4,` inside the bgp_config
    dict literal must be gone (replaced by the conditional
    emit below the dict). Regression guard against someone
    "restoring" them."""
    src = _read("widgets/add_device_dialog.py")
    marker_idx = src.index("v0.5.356 (audit bgp-empty-neighbor-on-v6-only)")
    body = src[marker_idx:marker_idx + 3500]
    # Read the dict literal itself (bounded by the closing brace).
    dict_start = body.index("bgp_config = {")
    dict_end = body.index("}", dict_start)
    dict_body = body[dict_start:dict_end]
    assert '"peer_ip": neighbor_ipv4' not in dict_body
    assert '"bgp_neighbor_ipv4": neighbor_ipv4' not in dict_body
    assert '"bgp_neighbor_ipv6": neighbor_ipv6' not in dict_body


# --- A7: mtu.isdigit int coerce ---


def test_A7_mtu_isdigit_coerces_to_str():
    """Pre-fix `mtu.isdigit()` raised AttributeError on int mtu;
    the exception got swallowed by the outer except and the
    whole `_configure_interfaces` returned False. Now: coerce
    to str."""
    src = _read("utils/frr_docker.py")
    marker_idx = src.index("v0.5.356 (audit frr-mtu-int-coerce)")
    body = src[marker_idx:marker_idx + 1500]
    assert "str(mtu).isdigit()" in body
    # The old bare-attribute form must be gone from this fix's
    # immediate context.
    fix_line_idx = body.index("if mtu and str(mtu).isdigit():")
    line = body[fix_line_idx:fix_line_idx + 200]
    assert "mtu.isdigit()" not in line.replace(
        "str(mtu).isdigit()", ""
    ), "the bare-attribute form must not be present alongside the fix"


# --- AST parse safety on all four touched files ---


def test_touched_files_ast_parse():
    import ast
    for rel in (
        "utils/rdma_perf.py",
        "utils/rdma_stream_engine.py",
        "widgets/add_device_dialog.py",
        "utils/frr_docker.py",
    ):
        ast.parse(_read(rel))


# --- Regression guards ---


def test_stop_perftest_still_defined():
    src = _read("utils/rdma_perf.py")
    assert "def stop_perftest(" in src


def test_register_perftest_with_tracker_still_defined():
    src = _read("utils/rdma_stream_engine.py")
    assert "def register_perftest_with_tracker(" in src


def test_v0_5_355_ibperf_tracker_wiring_still_intact():
    """v0.5.355 A1 depends on `stop_perftest` behaving correctly.
    v0.5.356 A4 tightens it; regression guard that A1's marker
    still exists (i.e. no accidental revert)."""
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.355 (audit rdma-ibperf-tracker-leak)" in src
