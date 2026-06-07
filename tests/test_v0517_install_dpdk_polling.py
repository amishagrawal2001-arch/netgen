"""Regression tests for v0.5.17: Make DPDK Ready wizard must wait
for install_dpdk.sh to actually finish, not just for the spawn 200.

Operator-reported:
  > tried making server ready with dpdk using make server dpdk ready
  > and completed all the steps, however verify installation, shows
  > below..
  >
  > DPDK Libraries: ✗
  > DPDK Packet Generator (tx_worker): ✗
  > Hugepages: ✗
  > Kernel Modules: ✓

Root cause: the wizard's `_on_step_done()` callback fired the moment
/api/admin/install_dpdk returned 200. That endpoint spawns
install_dpdk.sh in the background and returns immediately with a
log_path — it does NOT block until the install completes (which
takes 5-10 minutes on a fresh host).

The wizard marched forward through ALLOCATE_HUGEPAGES / LOAD_VFIO /
BIND_INTERFACE while the install was still running (or had already
failed silently). All shown ✓, but DPDK absent at the end.

v0.5.17 fix: when the step is INSTALL_DPDK, after the 200, start a
QTimer that polls /api/admin/install_dpdk/log every 5s until
`running=false`. Check return_code: 0 → advance; non-zero → mark
failed with the exit code in the detail pane.
"""
from __future__ import annotations

import re
from pathlib import Path


_WIZARD = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


def test_on_step_done_special_cases_install_dpdk():
    """`_on_step_done` must NOT call `row.set_state("ok")` immediately
    for INSTALL_DPDK — the 200 only means the script spawned, not
    that it succeeded."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_step_done[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_on_step_done not found"
    body = m.group(0)
    # Must check action.kind == ActionKind.INSTALL_DPDK BEFORE the
    # generic ok-then-advance path.
    assert "ActionKind.INSTALL_DPDK" in body, (
        "_on_step_done doesn't special-case INSTALL_DPDK. The "
        "immediate 200 from spawn would be treated as success."
    )
    # The special-case branch must NOT set state to ok yet.
    install_branch = re.search(
        r"if\s+action\.kind\s*==\s*ActionKind\.INSTALL_DPDK[\s\S]+?return",
        body,
    )
    assert install_branch, (
        "_on_step_done lacks the INSTALL_DPDK branch with early return."
    )
    branch_body = install_branch.group(0)
    assert "set_state(\"ok\")" not in branch_body and \
           "set_state('ok')" not in branch_body, (
        "INSTALL_DPDK branch sets row state to ok prematurely. "
        "Should stay 'running' until install_dpdk.sh exits."
    )
    # And it must start the polling helper.
    assert "_start_install_dpdk_poll" in branch_body, (
        "INSTALL_DPDK branch doesn't call _start_install_dpdk_poll "
        "— without that, the wizard never finds out when install "
        "actually completes."
    )


def test_start_install_dpdk_poll_uses_qtimer():
    """Polling needs a QTimer (not a synchronous loop) so the UI
    stays responsive during the 5-10 min install."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _start_install_dpdk_poll[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_start_install_dpdk_poll helper not found"
    body = m.group(0)
    assert "QTimer" in body, (
        "_start_install_dpdk_poll doesn't use QTimer — would block "
        "the UI."
    )
    # Cadence must be reasonable — too tight = noise, too loose =
    # operator sees a hung dialog. 5 s is a sane default.
    assert re.search(r"setInterval\(\s*(?:5000|3000|10000)\s*\)", body), (
        "_start_install_dpdk_poll uses an odd polling cadence — "
        "expected 5000 ms (5 s) range."
    )


def test_poll_endpoint_is_admin_install_dpdk_log():
    """Polls must hit /api/admin/install_dpdk/log — that's the
    endpoint with `running` and `return_code` fields."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _poll_install_dpdk_log[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_poll_install_dpdk_log not found"
    body = m.group(0)
    assert "/api/admin/install_dpdk/log" in body, (
        "_poll_install_dpdk_log doesn't query "
        "/api/admin/install_dpdk/log — wrong endpoint."
    )


def test_response_handler_advances_on_rc_zero():
    """When return_code == 0, the handler must set row state to ok
    and call _advance() to continue with subsequent wizard steps."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_on_install_dpdk_log_response not found"
    body = m.group(0)
    # rc == 0 path:
    assert re.search(
        r"rc\s*==\s*0[\s\S]+?set_state\(['\"]ok['\"]\)[\s\S]+?_advance",
        body,
    ) or re.search(
        r"rc\s*==\s*0[\s\S]+?_step_idx\s*\+=",
        body,
    ), (
        "Response handler doesn't advance on rc == 0. Wizard would "
        "stall after install completes successfully."
    )


def test_response_handler_fails_on_nonzero_rc():
    """Non-zero return_code = install failed. Must surface that with
    a red detail pane + retry button, not silently advance."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Must mention exit code in the failure message.
    assert "exit" in body.lower() or "rc" in body, (
        "Response handler doesn't surface the exit code on failure. "
        "Operator sees 'failed' with no clue what went wrong."
    )
    # Must call set_state with fail.
    assert "set_state(\"fail\"" in body or "set_state('fail'" in body, (
        "Response handler doesn't mark row as fail on non-zero rc."
    )
    # Must offer Retry.
    assert "Retry" in body, (
        "Response handler doesn't show a Retry button on install "
        "failure — operator has to close+reopen the wizard."
    )


def test_response_handler_keeps_polling_while_running():
    """While `running=True`, just update progress and return without
    advancing the wizard state."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Must check `data.get("running")` and early-return.
    assert re.search(
        r"data\.get\(['\"]running['\"]\)[\s\S]{0,200}?return",
        body,
    ), (
        "Response handler doesn't keep polling while running. "
        "Either advances early (wrong) or never advances (hung)."
    )


def test_response_handler_tolerates_transient_errors():
    """Single HTTP errors against the log endpoint shouldn't kill
    the install — the install_dpdk.sh script is still running on
    the server. Tolerate up to N consecutive errors before giving
    up."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert "consecutive_errors" in body or "_install_poll_consecutive" in body, (
        "Response handler doesn't track consecutive HTTP errors. "
        "One network blip would abort an otherwise-fine install."
    )
    # Threshold check (something like >= 3).
    assert re.search(
        r"consecutive_errors\s*>=\s*[2-5]",
        body,
    ), (
        "Response handler doesn't have a consecutive-errors threshold. "
        "Either gives up too eagerly (1 blip = fail) or never "
        "(infinite loop)."
    )


def test_stop_install_dpdk_poll_is_idempotent():
    """The cleanup helper must tolerate being called multiple times
    (e.g. timer fires after we already stopped). Otherwise we get
    NoneType errors during shutdown."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _stop_install_dpdk_poll[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_stop_install_dpdk_poll helper not found"
    body = m.group(0)
    # Must use try/except OR getattr fallback when stopping the timer.
    assert "try:" in body or "getattr" in body, (
        "_stop_install_dpdk_poll isn't idempotent — would crash if "
        "called after timer already stopped."
    )


def test_pyproject_version_at_least_0517():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 17), (
        f"Version {m.group(1)} < 0.5.17"
    )
