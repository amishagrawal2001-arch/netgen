"""Regression test for v0.5.20: when install_dpdk.sh fails, the
wizard must render the log tail INLINE so operators can diagnose
without a second ssh round-trip.

Operator-reported (after v0.5.17 + v0.5.18 + v0.5.19 shipped):
  > trying to setup dpdk, failing with exit code 1
  [screenshot showing wizard correctly caught the failure but
   the operator was left with only "exit 1" — had to ssh in to
   tail /tmp/netgen_install_dpdk_*.log to find the real cause]

The v0.5.17 polling fix worked: wizard caught the actual exit code
instead of falsely claiming success. But "exit 1" alone doesn't
tell the operator WHY. The server's response to
/api/admin/install_dpdk/log already includes the last 64 KiB of
log; we just weren't rendering it.

v0.5.20: when rc != 0, the failure-handling branch:
  1. Reads `log` field from the response (already there)
  2. Renders last 30 lines in a <pre> block in the detail pane
  3. Shows the log path so operators can ssh + tail for full context
  4. HTML-escapes log content so stray '<' / '>' don't break render

Same pattern as v0.5.11's _verify_running() diagnostic dump (which
showed journalctl + port check + legacy svc inline on /api/health
timeout). Different surface, same principle: don't make the
operator hunt for the cause they already have.
"""
from __future__ import annotations

import re
from pathlib import Path


_WIZARD = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


def test_failure_branch_renders_log_tail():
    """The rc != 0 branch must include log content from the
    response, not just the exit code."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    assert m, "_on_install_dpdk_log_response not found"
    body = m.group(0)
    # Must read the `log` field from response.
    assert re.search(r'data\.get\(["\']log["\']\)', body), (
        "Failure branch doesn't read data.get('log') — operator "
        "sees only 'exit N' with no log context."
    )
    # Must render it (look for `tail_lines` or similar handling).
    assert "tail_lines" in body or "splitlines" in body, (
        "Failure branch doesn't extract log lines — log field is "
        "fetched but not rendered."
    )


def test_failure_branch_renders_log_path():
    """The operator might want to ssh in for the full log — show
    the path so they don't have to guess."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(r'data\.get\(["\']log_path["\']\)', body), (
        "Failure branch doesn't surface log_path. Operators have "
        "to guess where /tmp/netgen_install_dpdk_*.log lives."
    )


def test_failure_branch_escapes_html_in_log():
    """install_dpdk.sh emits build output that may contain '<' / '>'
    (template params, redirect syntax, etc.). Render unescaped and
    QTextBrowser interprets them as HTML tags, breaking the layout."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Must escape & and < (the two characters that need HTML escaping).
    assert "&amp;" in body and "&lt;" in body, (
        "Failure branch doesn't HTML-escape log content. Build "
        "output with '<' or '&' would corrupt the dialog render."
    )


def test_failure_branch_caps_log_lines():
    """Don't dump the full 64 KiB of log into a dialog QLabel —
    that's typically several screens of build spew. Cap to ~30
    most-recent lines (the actual error usually appears near the
    end of the log)."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Looking for the last-N-lines slicing pattern.
    assert re.search(
        r"splitlines\(\)\[-\d+:\]|\[:?-\d+:\]",
        body,
    ), (
        "Failure branch doesn't cap log to last-N lines. Operators "
        "see hundreds of lines of build spew instead of the actual "
        "error near the end."
    )


def test_failure_branch_still_offers_retry():
    """A retry button must still be available after the inline log
    tail addition — the operator's workflow is 'see what went wrong,
    fix it, hit retry'."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert "Retry" in body and "_run_btn" in body, (
        "Failure branch dropped the Retry button. Operators would "
        "have to close + reopen the wizard to try again."
    )


def test_pyproject_version_at_least_0520():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 20), (
        f"Version {m.group(1)} < 0.5.20"
    )
