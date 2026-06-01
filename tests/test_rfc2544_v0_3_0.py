"""RFC 2544 wizard — v0.3.0 audit closure regression pins.

The dialog itself is heavy to construct headlessly (needs server
URL, async polling, etc.), so the overrides + wiring are pinned
with source-grep assertions. The HTML builder is a pure function
and gets actual behaviour tests.

What's pinned:
  * `closeEvent`, `reject`, `accept` overrides exist and route
    through the same is-running-test confirmation gate so the X
    button, Esc, and Close button all surface the orphan-test
    warning.
  * `_stop_test_and_cleanup_timer` is called on every close path
    so the QTimer doesn't leak and `/api/rfc2544/stop` fires.
  * Live MAC + IPv4 validators are wired via textChanged.
  * `_on_start` carries a pre-submit backstop that re-runs the
    same validators against the field values and refuses with a
    QMessageBox if any fail.
  * Ctrl+Return shortcut bound on the dialog.
  * `build_rfc2544_html_report` emits a PASS badge when
    `max_no_drop_pps > 0` and a FAIL badge when it's 0, plus a
    "N/M frame sizes passed" summary line.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
RFC_FILE = REPO / "widgets" / "rfc2544_dialog.py"


@pytest.fixture(scope="module")
def src():
    return RFC_FILE.read_text()


# ─────────────────────────────────────── close-gate overrides
@pytest.mark.parametrize("method", ["closeEvent", "reject", "accept"])
def test_v0_3_0_close_path_has_running_test_gate(src, method):
    """Each of the three close paths must:
      1. Define the method (override default Qt behaviour).
      2. Call `_is_test_running` to decide whether to prompt.
      3. Reach `_stop_test_and_cleanup_timer` on the proceed
         branch so the QTimer + server-side test don't orphan."""
    body = re.search(
        rf"def {method}\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None, f"{method} override missing"
    text = body.group(0)
    assert "_is_test_running" in text, (
        f"{method} doesn't check _is_test_running — "
        f"v0.3.0 close-while-test-running gate regressed"
    )
    assert "_stop_test_and_cleanup_timer" in text, (
        f"{method} doesn't call _stop_test_and_cleanup_timer — "
        f"the QTimer or server-side test could orphan on close"
    )


def test_v0_3_0_cleanup_helper_stops_timer_and_posts_stop(src):
    """`_stop_test_and_cleanup_timer` must:
      - Stop `_poll_timer` (the leak fix).
      - POST to /api/rfc2544/stop (the orphan-test fix)."""
    body = re.search(
        r"def _stop_test_and_cleanup_timer\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "_poll_timer.stop" in text, "timer-stop missing"
    assert "/api/rfc2544/stop" in text, (
        "POST to /api/rfc2544/stop missing — server-side test "
        "would orphan after dialog close"
    )


def test_v0_3_0_is_test_running_uses_timer_active(src):
    """The running-state probe must check the timer's active
    state, not a separate flag that could drift."""
    body = re.search(
        r"def _is_test_running\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "isActive" in text, (
        "_is_test_running should derive from the poll timer's "
        "isActive() so it can't lie about the running state"
    )


# ─────────────────────────────────────── live validators
def test_v0_3_0_imports_stream_input_validators(src):
    assert "from utils.stream_input import" in src
    assert "validate_mac" in src
    assert "validate_ipv4" in src


def test_v0_3_0_wire_live_validators_method_exists(src):
    """The textChanged wiring must live in a named method so it's
    visible in tracebacks + the next audit can find it."""
    assert re.search(
        r"^    def _wire_live_validators\(self", src,
        flags=re.MULTILINE,
    ), "_wire_live_validators method missing"


def test_v0_3_0_wire_live_validators_covers_all_4_fields(src):
    body = re.search(
        r"def _wire_live_validators\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    text = body.group(0)
    for attr in ("mac_src_field", "mac_dst_field",
                 "ip_src_field", "ip_dst_field"):
        assert attr in text, (
            f"{attr} missing from live-validator wiring — that "
            f"field will silently accept garbage"
        )


def test_v0_3_0_on_start_carries_validation_backstop(src):
    """Even with live validators, _on_start must re-check at
    submit time — the operator can ignore the red border and
    click anyway."""
    body = re.search(
        r"def _on_start\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    text = body.group(0)
    assert "validate_mac" in text and "validate_ipv4" in text, (
        "_on_start must re-validate MAC + IPv4 fields as a "
        "backstop — live red borders are advisory, not enforcing"
    )


# ─────────────────────────────────────── Ctrl+Return
def test_v0_3_0_ctrl_return_shortcut_wired(src):
    body = re.search(
        r"def _build_ui\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    text = body.group(0)
    assert "Key_Return" in text, (
        "Ctrl+Return shortcut not wired in _build_ui"
    )
    assert "QShortcut" in text


# ─────────────────────────────────────── HTML PASS/FAIL behaviour
def test_v0_3_0_html_report_marks_pass_when_pps_positive():
    from widgets.rfc2544_dialog import build_rfc2544_html_report
    rows = [{
        "frame_size": 64,
        "max_no_drop_pps": 14_880_000,
        "max_no_drop_gbps": 10.0,
        "pct_of_line_rate": 100,
        "attempts": [1, 2, 3],
        "latency": {"p50_us": 1.2, "p95_us": 5.4, "p99_us": 12.0},
    }]
    html = build_rfc2544_html_report({"target_loss_pct": 0.0}, rows)
    assert "PASS" in html
    # Summary line should say 1/1 passed.
    assert "1/1" in html


def test_v0_3_0_html_report_marks_fail_when_pps_zero():
    from widgets.rfc2544_dialog import build_rfc2544_html_report
    rows = [{
        "frame_size": 1518,
        "max_no_drop_pps": 0,
        "max_no_drop_gbps": 0,
        "pct_of_line_rate": 0,
        "attempts": [1, 2],
    }]
    html = build_rfc2544_html_report({"target_loss_pct": 0.0}, rows)
    assert "FAIL" in html
    assert "0/1" in html


def test_v0_3_0_html_report_mixes_pass_and_fail():
    from widgets.rfc2544_dialog import build_rfc2544_html_report
    rows = [
        {"frame_size": 64, "max_no_drop_pps": 14_880_000,
         "max_no_drop_gbps": 10.0, "pct_of_line_rate": 100,
         "attempts": [1, 2, 3]},
        {"frame_size": 1518, "max_no_drop_pps": 0,
         "max_no_drop_gbps": 0, "pct_of_line_rate": 0,
         "attempts": [1, 2]},
    ]
    html = build_rfc2544_html_report({"target_loss_pct": 0.0}, rows)
    assert "PASS" in html and "FAIL" in html
    # 1 of 2 passed.
    assert "1/2" in html


def test_v0_3_0_html_report_summary_carries_target_loss():
    """The summary line must quote the target_loss_pct so a
    reader knows what threshold the verdict was computed against."""
    from widgets.rfc2544_dialog import build_rfc2544_html_report
    rows = [{"frame_size": 64, "max_no_drop_pps": 1, "max_no_drop_gbps": 0,
             "pct_of_line_rate": 0, "attempts": []}]
    html = build_rfc2544_html_report({"target_loss_pct": 0.5}, rows)
    # The target loss appears both in the params block AND the
    # summary line. Check the summary phrasing specifically.
    assert "target loss" in html.lower()
    assert "0.5" in html


def test_v0_3_0_html_report_empty_rows_still_safe():
    """No results (test cancelled before any frame size completed)
    should produce a clean 'no results' message, not crash on the
    summary computation."""
    from widgets.rfc2544_dialog import build_rfc2544_html_report
    html = build_rfc2544_html_report({"target_loss_pct": 0.0}, [])
    assert "no results" in html.lower()
    # No PASS/FAIL badges when there's nothing to verdict.
    assert "PASS" not in html and "FAIL" not in html
