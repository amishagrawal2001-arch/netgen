"""v0.5.362 — ISIS MED bundle: four fixes across `utils/isis.py`
and `utils/isis_monitor.py`.

Post-v0.5.361 audit tail. Each fix carries the shared marker
`v0.5.362 (audit isis-...)` plus its own sub-slug so a grep
finds all four sites.

A2 — DB-fallback default enabled BOTH AFs
    Pre-fix, on ANY exception the fallback set
    `enable_ipv4=True; enable_ipv6=True`. A transient sqlite lock
    during Start ISIS on a v4-only device pushed
    `ipv6 router isis CORE` onto an interface that had no v6
    address; adjacency never came up over v6, `isis_state` stuck
    at Starting. Fix: prefer isis_config's `ipv4_enabled` /
    `ipv6_enabled` (v0.5.205 populates these); final fallback is
    v4-only, not both.

A3 — 20s sync sleep inside Flask worker
    `max_retries=10 × retry_delay=2s = 20s` of `time.sleep` in
    `configure_isis_neighbor`, called synchronously from a Flask
    HTTP route. UI spinner appeared frozen; concurrent Apply on
    a second device queued 20s behind. Fix: cap total wait at
    ~5s (5 retries × 1s delay) which is enough headroom for
    "container just started"; slower startups fail the check and
    log "not ready, proceeding anyway", same recovery as before.

A5 — Monitor shutdown blocked on in-flight docker exec_runs
    `ThreadPoolExecutor(...) as ex:` block exit waits for every
    submitted future. Futures are docker `exec_run` calls with
    no timeout. Operator hitting Stop Monitor with ~100 devices
    blocked shutdown 10s+. Fix: create executor manually, on
    stop_event flip call `executor.shutdown(wait=False,
    cancel_futures=True)` so queued futures cancel and running
    ones don't block shutdown.

A7 — shell=True with interpolated container_id
    `subprocess.run(f"docker exec {container_id} ...", shell=True)`
    interpolates `container_id` into a shell string. Unlikely
    exploitable today (container_id is a UUID), but a future
    refactor that lets user text through would open a shell-
    injection path. Fix: switch to argv-list form (`shell=False`
    default).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _isis_src():
    return (_REPO / "utils" / "isis.py").read_text()


def _isis_monitor_src():
    return (_REPO / "utils" / "isis_monitor.py").read_text()


def test_all_markers_present():
    src = _isis_src()
    mon = _isis_monitor_src()
    assert "v0.5.362 (audit isis-shell-true-interpolation, A7)" in src
    assert "v0.5.362 (audit isis-db-fallback-double-af, A2)" in src
    assert "v0.5.362 (audit isis-configure-blocks-flask-20s, A3)" in src
    assert "v0.5.362 (audit isis-monitor-shutdown-hang, A5)" in mon


# --- A2: DB-fallback default ---


def test_A2_fallback_prefers_isis_config_flags():
    """The fallback branch must read isis_config's ipv4_enabled /
    ipv6_enabled BEFORE defaulting to v4-only. If neither flag is
    set in config, fall back to v4-only (NOT both, which was the
    pre-fix bug)."""
    src = _isis_src()
    marker_idx = src.index("v0.5.362 (audit isis-db-fallback-double-af, A2)")
    body = src[marker_idx:marker_idx + 3500]
    assert 'isis_config.get("ipv4_enabled")' in body
    assert 'isis_config.get("ipv6_enabled")' in body
    # And the final default is v4-only, not both.
    assert "enable_ipv4 = True" in body
    assert "enable_ipv6 = False" in body


def test_A2_no_longer_defaults_both_af_to_true_in_except():
    """Regression guard: the pre-fix
    `enable_ipv4 = True; enable_ipv6 = True` pair inside the
    except must be gone."""
    src = _isis_src()
    fn_idx = src.index("def start_isis_neighbor(")
    body = src[fn_idx:]
    # Find the except block only.
    marker_idx = body.index("v0.5.362 (audit isis-db-fallback-double-af, A2)")
    except_body = body[marker_idx:marker_idx + 3500]
    # The pre-fix "enable both" pattern was two consecutive lines.
    # Post-fix, `enable_ipv6 = True` should not appear as an
    # unconditional assignment inside the except; only conditional
    # via `_cfg_v6` should assign it.
    _code_only = "\n".join(
        _l for _l in except_body.splitlines()
        if not _l.lstrip().startswith("#") and "logger" not in _l
    )
    # Count unconditional "enable_ipv6 = True" — should be 0 at
    # column 12 (inside except).
    _bare = [
        _l for _l in _code_only.splitlines()
        if _l.strip() == "enable_ipv6 = True"
    ]
    assert not _bare, (
        f"pre-fix `enable_ipv6 = True` line survived inside the "
        f"except block: {_bare}"
    )


# --- A3: Flask-blocking retry ---


def test_A3_max_retries_reduced():
    """Pre-fix max_retries=10 × retry_delay=2s = 20s of blocking
    sleep. Post-fix must cap under 10s (5 × 1s = 5s sleep budget)."""
    src = _isis_src()
    marker_idx = src.index("v0.5.362 (audit isis-configure-blocks-flask-20s, A3)")
    body = src[marker_idx:marker_idx + 1500]
    assert "max_retries = 5" in body
    assert "retry_delay = 1" in body


def test_A3_no_pre_fix_20s_config():
    """Regression guard: pre-fix `max_retries = 10` + `retry_delay
    = 2` must be gone from configure_isis_neighbor's body."""
    src = _isis_src()
    fn_idx = src.index("def configure_isis_neighbor(")
    _next = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:_next]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "max_retries = 10" not in _code_only
    assert "retry_delay = 2" not in _code_only


# --- A5: Monitor shutdown ---


def test_A5_executor_shutdown_cancels_pending_futures():
    """The fix must call `executor.shutdown(wait=False,
    cancel_futures=True)` on the finally path so a Stop Monitor
    with ~100 in-flight futures doesn't block shutdown."""
    src = _isis_monitor_src()
    marker_idx = src.index("v0.5.362 (audit isis-monitor-shutdown-hang, A5)")
    body = src[marker_idx:marker_idx + 3000]
    assert "executor.shutdown(wait=False, cancel_futures=True)" in body
    # Python 3.8 fallback.
    assert "executor.shutdown(wait=False)" in body


def test_A5_result_loop_bails_on_stop_event():
    """The `for fut in as_completed(futures):` loop must check
    `stop_event` between results and break, otherwise the fix's
    shutdown call still waits for one more result to arrive."""
    src = _isis_monitor_src()
    marker_idx = src.index("v0.5.362 (audit isis-monitor-shutdown-hang, A5)")
    body = src[marker_idx:marker_idx + 3000]
    # Find the loop body region.
    _loop_idx = body.index("for fut in as_completed(futures):")
    _loop_body = body[_loop_idx:_loop_idx + 800]
    assert "if self.stop_event.is_set():" in _loop_body
    assert "break" in _loop_body


def test_A5_no_longer_uses_with_context():
    """The pre-fix `with ThreadPoolExecutor(...) as executor:`
    block-exit was the blocker. Structural guard that the fix
    uses manual create + try/finally instead."""
    src = _isis_monitor_src()
    fn_idx = src.index("def _monitor_loop(")
    _next = src.index("\n    def ", fn_idx + 1)
    body = src[fn_idx:_next]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "with ThreadPoolExecutor" not in _code_only
    assert "ThreadPoolExecutor(max_workers=self.max_workers)" in _code_only
    assert "finally:" in _code_only


# --- A7: shell=True → argv-list ---


def test_A7_get_isis_status_uses_argv_not_shell_string():
    """get_isis_status's two docker-exec vtysh calls must be
    argv-list + shell=False. Structural checks:
      1. No `subprocess.run(<shell-string>, shell=True, ...)` in
         the function.
      2. `shell=True` no longer appears in the function body at all.
      3. The neighbor_cmd + summary_cmd variables are assigned to
         list literals whose first element is `"docker"`."""
    import ast
    src = _isis_src()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "get_isis_status":
            continue
        _fn_body_src = ast.unparse(node)
        assert "shell=True" not in _fn_body_src, (
            "get_isis_status still contains shell=True"
        )
        # Structural: neighbor_cmd + summary_cmd assigned to list
        # literals starting with "docker".
        _list_assigns = 0
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            if not (
                len(sub.targets) == 1
                and isinstance(sub.targets[0], ast.Name)
                and sub.targets[0].id in ("neighbor_cmd", "summary_cmd")
            ):
                continue
            if not isinstance(sub.value, ast.List):
                continue
            first = sub.value.elts[0] if sub.value.elts else None
            if (
                isinstance(first, ast.Constant)
                and first.value == "docker"
            ):
                _list_assigns += 1
        assert _list_assigns >= 2, (
            f"expected both neighbor_cmd + summary_cmd assigned as "
            f'["docker", "exec", ...] argv lists, found {_list_assigns}'
        )
        return
    raise AssertionError("get_isis_status not found")


# --- Regression guards ---


def test_v0_5_205_ipv4_enabled_flag_still_in_bgp_config():
    """v0.5.205 populates isis_config's per-AF flags; the A2 fix
    depends on them being there. Regression guard so nothing
    reverts v0.5.205."""
    src = (_REPO / "widgets" / "add_device_dialog.py").read_text()
    assert "isis_toggle_ipv4" in src
    assert "isis_toggle_ipv6" in src


def test_ast_parses():
    import ast
    ast.parse(_isis_src())
    ast.parse(_isis_monitor_src())
