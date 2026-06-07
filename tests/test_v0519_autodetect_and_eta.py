"""Regression tests for v0.5.19 — Tier 2 DPDK UX:
  7. Auto-detect "DPDK not ready" on server-connect → non-blocking
     suggestion to run ★ Setup DPDK.
  8. Live elapsed time + ETA in Make DPDK Ready during install.

Both close gaps the v0.5.18 audit identified but didn't fix:
* operators didn't know DPDK was a separate setup step (the
  symptom that started the cascade today)
* the install_dpdk.sh polling shows phase % but not "how much
  longer" — operators staring at "Step 5/8: Building DPDK · ninja
  47%" for 10 minutes don't know if they're 1 min or 7 min from
  done.
"""
from __future__ import annotations

import re
from pathlib import Path


_MENU_ACTIONS = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "menu_actions.py"
)
_WIZARD = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "dpdk_make_ready_dialog.py"
)


# ─────────────────────────── auto-detect banner (item 7)


def test_add_server_triggers_dpdk_autodetect():
    """When a new server connects, the menu_actions code must
    probe /api/dpdk/status to see if DPDK is set up. This is what
    surfaces the suggestion banner."""
    src = _MENU_ACTIONS.read_text()
    assert "_check_dpdk_and_suggest_setup" in src, (
        "menu_actions doesn't define _check_dpdk_and_suggest_setup "
        "— no DPDK auto-detect on server add."
    )
    # Add-server path must invoke it (per added URL).
    assert re.search(
        r"for\s+url\s+in\s+added[\s\S]{0,500}?_check_dpdk_and_suggest_setup",
        src,
    ), (
        "Add-server flow doesn't iterate `added` URLs through "
        "_check_dpdk_and_suggest_setup. Auto-detect won't fire."
    )


def test_autodetect_uses_async_worker():
    """Probe must run async (the operator just clicked Add and
    expects the dialog to close — blocking would freeze UI for the
    probe's 4 s timeout)."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _check_dpdk_and_suggest_setup[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_check_dpdk_and_suggest_setup not found"
    body = m.group(0)
    assert "_DpdkApiWorker" in body, (
        "Auto-detect doesn't use _DpdkApiWorker — would block the "
        "UI thread on the probe."
    )
    assert "done.connect" in body, (
        "Auto-detect doesn't wire a done callback."
    )


def test_autodetect_uses_is_dpdk_ready_helper():
    """Don't reimplement readiness criteria — reuse the same
    `is_dpdk_ready(status)` function the wizard uses so we stay
    consistent."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _on_dpdk_probe_for_suggest[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_on_dpdk_probe_for_suggest callback not found"
    body = m.group(0)
    assert "is_dpdk_ready" in body, (
        "Probe callback doesn't use is_dpdk_ready() — risk of "
        "drift between wizard's notion of 'ready' and the banner's."
    )


def test_autodetect_offers_setup_now_button():
    """The banner must offer a one-click path to ★ Setup DPDK,
    not just inform the operator. Otherwise it's nagging."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _on_dpdk_probe_for_suggest[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(r'addButton\(\s*"Setup Now"', body), (
        "Probe callback doesn't add a 'Setup Now' button. Banner "
        "would only inform, not offer action."
    )
    assert "show_dpdk_make_ready_dialog" in body, (
        "Setup Now button doesn't route to "
        "show_dpdk_make_ready_dialog."
    )


def test_autodetect_failure_is_silent():
    """The auto-detect probe is opportunistic — if it fails (network
    blip, server too old without /api/dpdk/status), don't badger
    the operator with an error popup. They didn't ask for this
    probe."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _on_dpdk_probe_for_suggest[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(
        r"if\s+err:[\s\S]{0,200}?return",
        body,
    ), (
        "Probe callback doesn't early-return on err — would surface "
        "a probe-failed dialog to the operator who never asked."
    )


# ─────────────────────────── live ETA in install (item 8)


def test_install_poll_records_start_time():
    """_start_install_dpdk_poll must capture monotonic start time
    so each subsequent poll can compute elapsed / ETA."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _start_install_dpdk_poll[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    assert m, "_start_install_dpdk_poll not found"
    body = m.group(0)
    assert "monotonic" in body, (
        "_start_install_dpdk_poll doesn't capture time.monotonic() "
        "— no baseline for elapsed/ETA computation."
    )


def test_install_poll_response_shows_elapsed_and_eta():
    """The poll-response handler must include elapsed and ETA in the
    detail-pane bits when overall_pct is meaningful."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert "monotonic" in body, (
        "Response handler doesn't compute elapsed from monotonic "
        "start time."
    )
    assert "elapsed" in body, (
        "Response handler doesn't add elapsed to detail pane."
    )
    assert "ETA" in body, (
        "Response handler doesn't add ETA — operator still doesn't "
        "know how much longer to wait."
    )


def test_eta_only_computed_after_5_percent():
    """ETA before ~5% is wildly inaccurate (apt + clone don't move
    overall_pct). Gate the ETA calc on overall_pct >= 5 so we don't
    show '60 min remaining' at minute 1."""
    src = _WIZARD.read_text()
    m = re.search(
        r"def _on_install_dpdk_log_response[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(
        r"overall_pct\s*>=\s*[2-9]\d?",
        body,
    ) or re.search(
        r"overall_pct\s+and\s+overall_pct\s*>=\s*[2-9]",
        body,
    ), (
        "ETA isn't gated on overall_pct ≥ ~5%. Risk of garbage ETAs "
        "at the start of the install."
    )


def test_fmt_mmss_helper_exists():
    """Helper for formatting m:ss should be a module-level function
    so it's testable + reusable."""
    src = _WIZARD.read_text()
    assert "def _fmt_mmss" in src, (
        "_fmt_mmss helper missing — m:ss formatting should be a "
        "named function not inlined."
    )


def test_fmt_mmss_pads_seconds_correctly():
    """Verify the helper produces 0:05 not 0:5 for sub-10 second
    values. Importable + testable."""
    from widgets.dpdk_make_ready_dialog import _fmt_mmss
    assert _fmt_mmss(5) == "0:05"
    assert _fmt_mmss(65) == "1:05"
    assert _fmt_mmss(0) == "0:00"
    assert _fmt_mmss(600) == "10:00"
    assert _fmt_mmss(-5) == "0:00", (
        "Negative input should clamp to 0:00 (defensive against "
        "monotonic-clock weirdness in tests)."
    )


def test_pyproject_version_at_least_0519():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 19), (
        f"Version {m.group(1)} < 0.5.19"
    )
