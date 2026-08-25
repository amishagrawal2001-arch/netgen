"""v0.5.217: DHCP audit bundle — 8 bugs off the DHCP walkthrough.

See CHANGELOG.md ##0.5.217 for the full pre-fix / fix
narrative for each of the eight bugs (A-H). These tests are
source-level lock-ins — grep-style assertions against the
patched files — that fail loudly if a future refactor loses
the fix. That model is deliberately blunt but has caught
every regression across v0.5.207-v0.5.216 without needing a
running server.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05217_test_{os.getpid()}.db"),
)


# ---------------------------------------------------------------------------
# Bug A — Edit Device: pre-fill DHCP fields + merge on Save (not overwrite).
# ---------------------------------------------------------------------------

def _devices_tab_src() -> str:
    return (REPO / "widgets" / "devices_tab.py").read_text()


def test_A_edit_dialog_prefills_dhcp_mode_combo():
    """Pre-fix: dhcp_mode_combo defaulted to 'Server'. Post-fix: the
    Edit-Device dialog reads the stored mode and calls
    setCurrentIndex on the mode combo before exec_()."""
    src = _devices_tab_src()
    assert "dialog.dhcp_mode_combo.findText(combo_text)" in src, (
        "Edit-Device dialog no longer pre-fills dhcp_mode_combo — "
        "opening Edit on a CLIENT device will re-default to Server"
    )
    assert "dialog.dhcp_mode_combo.setCurrentIndex" in src, (
        "dhcp_mode_combo pre-fill removed"
    )


def test_A_edit_dialog_prefills_pool_and_af_checkboxes():
    """Pre-fix: pool inputs kept the placeholder literals
    (192.168.30.10/200) and the AF checkboxes defaulted. Post-fix:
    Edit-Device populates them from existing_dhcp."""
    src = _devices_tab_src()
    for widget_name in (
        "dhcp_ipv4_enabled_checkbox",
        "dhcp_ipv6_enabled_checkbox",
        "dhcp_pool_start_input",
        "dhcp_pool_end_input",
        "dhcp6_pool_start_input",
        "dhcp6_pool_end_input",
        "dhcp_gateway_route_input",
    ):
        assert f"dialog.{widget_name}" in src, (
            f"Edit-Device DHCP pre-fill no longer touches {widget_name} — "
            f"Save-with-no-changes will re-write wrong pool/AF values"
        )


def test_A_edit_save_merges_dhcp_configs_not_overwrite():
    """Pre-fix: `device_info["dhcp_config"] = dhcp_config` clobbered
    all server-populated arrays. Post-fix: the Edit path routes the
    dialog's return through `_merge_dhcp_configs(existing, new)`."""
    src = _devices_tab_src()
    # Look for the merge call in the Edit save path — not the
    # 5757 refresh path. We anchor on the audit-fix-A comment.
    assert "audit fix A" in src, "audit fix A comment removed"
    assert "self._merge_dhcp_configs(" in src, (
        "_merge_dhcp_configs helper is no longer called in the Edit "
        "save path — whole-blob overwrite has returned"
    )
    # There must be no `device_info["dhcp_config"] = dhcp_config`
    # bare assignment inside the Edit-Save protocol block. The
    # helper call must precede the assignment.
    # Grep for the raw pattern.
    assert re.search(
        r"merged_dhcp\s*=\s*self\._merge_dhcp_configs\(\s*existing_dhcp_for_merge,\s*dhcp_config",
        src,
    ), "Edit-Save merge call shape changed — assertion needs updating"


# ---------------------------------------------------------------------------
# Bug B — DHCP-client mode silently wipes BGP config.
# ---------------------------------------------------------------------------

def _add_device_src() -> str:
    return (REPO / "widgets" / "add_device_dialog.py").read_text()


def test_B_dhcp_client_no_longer_wipes_bgp_config():
    """Pre-fix: `if dhcp_mode_text == "client": ... bgp_config = {}`.
    Post-fix: bgp_config stays intact."""
    src = _add_device_src()
    # Locate the client branch and make sure it does NOT contain
    # `bgp_config = {}`.
    m = re.search(
        r'if dhcp_mode_text == "client":(.+?)return\s+\(',
        src, re.DOTALL,
    )
    assert m, "get_values() DHCP-client branch shape changed"
    client_branch = m.group(1)
    # Strip comment lines so the historical footgun reference in the
    # audit-fix-B comment doesn't count as a live assignment.
    code_lines = [
        ln for ln in client_branch.splitlines()
        if not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "bgp_config = {}" not in code, (
        "DHCP-client branch still wipes bgp_config — Bug B is back"
    )


def test_B_dhcp_client_bgp_warning_references_bgp():
    """When both DHCP-client and BGP are set, a QMessageBox.warning
    references BGP so the operator sees what's about to happen."""
    src = _add_device_src()
    m = re.search(
        r'if dhcp_mode_text == "client":(.+?)return\s+\(',
        src, re.DOTALL,
    )
    assert m, "get_values() DHCP-client branch shape changed"
    branch = m.group(1)
    assert "QMessageBox.warning" in branch, (
        "DHCP-client + BGP no longer shows a warning dialog"
    )
    assert "BGP" in branch, "Warning message no longer mentions BGP"


# ---------------------------------------------------------------------------
# Bug C — Device Remove leaks dnsmasq when top-level dhcp_mode blank.
# ---------------------------------------------------------------------------

def _run_tgen_server_src() -> str:
    return (REPO / "run_tgen_server.py").read_text()


def test_C_device_remove_dhcp_mode_uses_dhcp_config_fallback():
    """Pre-fix: line 6374 was
        dhcp_mode_remove = (device_info.get("dhcp_mode") or "").lower()
    Post-fix: mirrors line 6333's dhcp_cfg fallback."""
    src = _run_tgen_server_src()
    # There should NOT be a bare `dhcp_mode_remove =
    # (device_info.get("dhcp_mode") or "").lower()` sitting alone
    # inside the /api/device/remove handler right before the
    # `if dhcp_mode_remove in ("client", "server")` check. The
    # only bare-column variant may still appear inside the else
    # branch at line 6335.
    # Instead we anchor on the audit-fix-C marker.
    assert "audit fix C" in src, "audit fix C comment removed"
    # The dhcp_config fallback path.
    assert re.search(
        r"dhcp_mode_remove\s*=\s*\(\s*\n?\s*dhcp_cfg_for_remove\.get\(\"mode\"\)\s*\n?\s*or\s+device_info\.get\(\"dhcp_mode\"\)",
        src,
    ), "dhcp_mode_remove is no longer computed with the dhcp_config fallback"


# ---------------------------------------------------------------------------
# Bug D — stop_dhcp_server unconditionally returns success.
# ---------------------------------------------------------------------------

def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def test_D_stop_dhcp_server_collects_and_returns_failures():
    src = _dhcp_src()
    # Slice out stop_dhcp_server for a focused check.
    idx = src.find("def stop_dhcp_server(")
    assert idx >= 0, "stop_dhcp_server missing"
    tail = src[idx:]
    end = tail.find("\ndef ", 100)
    body = tail[:end] if end > 0 else tail
    assert "failures: List[str] = []" in body, (
        "stop_dhcp_server no longer accumulates a failures list"
    )
    assert 'return {"success": True}' in body, "unexpected — sanity anchor"
    assert re.search(
        r'if failures:\s*\n\s*return\s+\{\s*"success":\s*False',
        body,
    ), (
        "stop_dhcp_server no longer returns success=False when any "
        "sub-step failed — Bug D is back"
    )
    # And the per-failure sites must warn + append.
    assert "failures.append" in body, "no failures.append found — Bug D regressed"
    # dnsmasq stop failure should now warn, not debug.
    assert re.search(
        r'logger\.warning\("\[DHCP\] Failed to stop dnsmasq',
        body,
    ), "stop dnsmasq failure log level no longer warning"


# ---------------------------------------------------------------------------
# Bug E — DHCP monitor polls both client AND server modes.
# ---------------------------------------------------------------------------

def _dhcp_monitor_src() -> str:
    return (REPO / "utils" / "dhcp_monitor.py").read_text()


def test_E_monitor_get_dhcp_devices_includes_both_modes():
    src = _dhcp_monitor_src()
    assert "def _get_dhcp_devices(" in src, (
        "_get_dhcp_devices renamed helper missing — Bug E fix removed"
    )
    # It must accept both client and server.
    m = re.search(
        r"def _get_dhcp_devices\(self\)(.+?)def ",
        src, re.DOTALL,
    )
    assert m, "_get_dhcp_devices shape changed"
    body = m.group(1)
    assert 'in ("client", "server")' in body, (
        "_get_dhcp_devices no longer includes server-mode devices"
    )


def test_E_monitor_has_server_mode_branch():
    src = _dhcp_monitor_src()
    assert "def _check_server_device(" in src, (
        "_check_server_device helper missing — server-mode probe gone"
    )
    # Server branch must call the helper and set "Server Running"
    # or "Server Down".
    assert '"Server Running"' in src, "Server Running state string missing"
    assert '"Server Down"' in src, "Server Down state string missing"
    # And it must dispatch inside the main loop.
    assert re.search(
        r'if mode == "server":\s*\n\s*try:\s*\n\s*self\._check_server_device',
        src,
    ), "main loop no longer dispatches to _check_server_device"


# ---------------------------------------------------------------------------
# Bug F — start_dhcp_server writes dhcp_state="Failed" before every
#          failure return.
# ---------------------------------------------------------------------------

def test_F_start_dhcp_server_marks_failed_on_every_error_return():
    src = _dhcp_src()
    idx = src.find("def start_dhcp_server(")
    assert idx >= 0
    tail = src[idx:]
    end = tail.find("\ndef ", 100)
    body = tail[:end] if end > 0 else tail
    # Count return {"success": False and count preceding
    # `dhcp_state": "Failed"` writes. Each failure return should be
    # immediately preceded by a Failed-state write.
    fail_returns = [
        m.start() for m in re.finditer(
            r'return\s+\{\s*"success":\s*False', body
        )
    ]
    assert fail_returns, "start_dhcp_server has no failure returns to check"
    for pos in fail_returns:
        # Look back up to 500 chars for a state marker. v0.5.223
        # refined the "no pool" branch to write dhcp_state="No Pool"
        # instead of "Failed" so operators can distinguish
        # config-incomplete from real dnsmasq crashes. Either
        # state satisfies bug F's original invariant (some state
        # gets written before the failure return; DB doesn't
        # linger on a stale prior reading).
        window = body[max(0, pos - 500):pos]
        assert (
            '"dhcp_state": "Failed"' in window
            or '"dhcp_state": "No Pool"' in window
        ), (
            "start_dhcp_server has a `return {\"success\": False}` "
            "without a preceding `dhcp_state=\"Failed\"` or "
            "\"No Pool\" DB write — Bug F is back "
            "(window near offset %d)" % pos
        )


# ---------------------------------------------------------------------------
# Bug G — dhcp_manual_override end-to-end.
# ---------------------------------------------------------------------------

def test_G_schema_migration_for_dhcp_manual_override():
    src = (REPO / "utils" / "device_database.py").read_text()
    assert "dhcp_manual_override" in src, (
        "device_database.py no longer references dhcp_manual_override"
    )
    assert "ALTER TABLE devices ADD COLUMN dhcp_manual_override BOOLEAN" in src, (
        "dhcp_manual_override column migration missing"
    )
    assert "ALTER TABLE devices ADD COLUMN dhcp_manual_override_time TIMESTAMP" in src, (
        "dhcp_manual_override_time column migration missing"
    )
    # And it must be in the update_device field_mapping so writes
    # actually persist.
    assert "'dhcp_manual_override': 'dhcp_manual_override'" in src, (
        "dhcp_manual_override missing from update_device field_mapping"
    )
    assert "'dhcp_manual_override_time': 'dhcp_manual_override_time'" in src, (
        "dhcp_manual_override_time missing from update_device field_mapping"
    )


def test_G_stop_dhcp_services_writes_manual_override():
    src = _dhcp_src()
    idx = src.find("def stop_dhcp_services(")
    assert idx >= 0
    tail = src[idx:]
    end = tail.find("\ndef ", 100)
    body = tail[:end] if end > 0 else tail
    assert '"dhcp_manual_override": True' in body, (
        "stop_dhcp_services no longer stamps dhcp_manual_override=True "
        "— monitor will resurrect stopped DHCP within 60s (Bug G)"
    )
    assert '"dhcp_manual_override_time"' in body, (
        "stop_dhcp_services no longer stamps dhcp_manual_override_time"
    )


def test_G_monitor_honours_manual_override_with_120s_guard():
    src = _dhcp_monitor_src()
    assert "_MANUAL_OVERRIDE_WINDOW_SECONDS = 120" in src, (
        "manual-override window is no longer 120s — Bug G weakened"
    )
    assert "def _manual_override_active(" in src, (
        "_manual_override_active helper missing"
    )
    # It must actually be called inside _check_clients (or the
    # server-mode probe path). Slice from _check_clients definition
    # to end-of-file (it's the last method).
    idx = src.find("def _check_clients(self)")
    assert idx >= 0, "_check_clients missing"
    check_body = src[idx:]
    assert "self._manual_override_active(device)" in check_body, (
        "_check_clients no longer consults _manual_override_active — "
        "Bug G is back"
    )


def test_G_monitor_clears_override_on_takeover():
    src = _dhcp_monitor_src()
    # Two independent takeover sites must clear the fields:
    # 1) The manual_override_active helper on expiry.
    # 2) The client-mode snapshot write when override was set.
    # 3) The server-mode check_server_device DB write.
    assert '"dhcp_manual_override": False' in src, (
        "monitor never clears dhcp_manual_override — takeover flag stays "
        "on forever"
    )
    assert '"dhcp_manual_override_time": None' in src, (
        "monitor never clears dhcp_manual_override_time"
    )


# ---------------------------------------------------------------------------
# Bug H — restart-storm backoff.
# ---------------------------------------------------------------------------

def test_H_monitor_tracks_restart_attempts_per_device():
    src = _dhcp_monitor_src()
    assert "self._dhcp_restart_attempts" in src, (
        "_dhcp_restart_attempts dict missing — Bug H fix removed"
    )
    # Threshold + window constants must exist.
    assert "_BACKOFF_THRESHOLD = 3" in src, "backoff threshold changed"
    assert "_BACKOFF_MAX_SECONDS = 1800" in src, "backoff cap changed"


def test_H_backoff_gate_uses_exponential_delay():
    src = _dhcp_monitor_src()
    # The exponential is `self.check_interval * (2 ** excess)`
    # capped at _BACKOFF_MAX_SECONDS.
    assert re.search(
        r"self\.check_interval\s*\*\s*\(2\s*\*\*\s*excess\)",
        src,
    ), "backoff formula no longer exponential — Bug H weakened"
    assert "min(self.check_interval" in src, (
        "backoff cap no longer applied via min()"
    )


def test_H_successful_lease_resets_counter():
    src = _dhcp_monitor_src()
    assert "def _note_leased(" in src, "_note_leased helper missing"
    # It must pop the device_id from the tracker.
    idx = src.find("def _note_leased(")
    assert idx >= 0, "_note_leased missing"
    tail = src[idx:]
    # Scope to the next method definition inside the class (2-space
    # indented `def ` under class body — anywhere from 0 to 8 chars
    # of leading whitespace).
    end_match = re.search(r"\n {0,8}def ", tail[10:])
    body = tail[: (10 + end_match.start())] if end_match else tail
    assert "self._dhcp_restart_attempts.pop(device_id" in body, (
        "_note_leased no longer clears the restart-attempt counter"
    )


# ---------------------------------------------------------------------------
# Bonus smoke tests — the changed files still parse.
# ---------------------------------------------------------------------------

def test_all_changed_files_still_parse():
    """Guard against a stray syntax error in the batched edits.
    Full imports would require Qt / SQLite / etc., so we compile
    the AST only."""
    import ast
    for rel in (
        "widgets/devices_tab.py",
        "widgets/add_device_dialog.py",
        "run_tgen_server.py",
        "utils/dhcp.py",
        "utils/dhcp_monitor.py",
        "utils/device_database.py",
    ):
        p = REPO / rel
        try:
            ast.parse(p.read_text(), filename=str(p))
        except SyntaxError as exc:
            raise AssertionError(f"{rel} no longer parses: {exc}") from exc


def test_version_bumped():
    """v0.5.217 must have shipped — accepts 0.5.217 or any later
    release (post-bumps to 0.5.218+ would otherwise break this
    test forever).
    """
    py = (REPO / "pyproject.toml").read_text()
    m = re.search(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', py)
    assert m, "pyproject.toml has no parseable version line"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 217), (
        f"pyproject.toml version {major}.{minor}.{patch} is below "
        f"the v0.5.217 audit ship — this bundle never landed"
    )
