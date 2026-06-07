"""Regression tests for v0.5.16: stop the "LED stays green + app
freezes" bug class after a server reboot.

Operator-reported:
  > after server reboot from the add tgen dialog, app started
  > freezing intermittently and server status still shows green

Two root causes:

  1. `poll_server_health()` caught exceptions and `pass`ed —
     server.online stayed True, the LED stayed green for the
     entire 3-5 minute reboot window.

  2. ConnectionManager.get() used Retry(total=3, backoff_factor=1)
     = up to ~7 s per request when the server was dead. With
     stats pollers firing every 2 s and health pollers every 30 s,
     workers piled up and the UI froze intermittently as signal
     handlers stacked.

v0.5.16 fixes:

  a. ConnectionManager.quick_get() — bypasses the retry adapter
     entirely. Used by polling code paths where fast failure
     beats blocking retry.

  b. poll_server_health() flips server.online=False after N=2
     consecutive failures (using quick_get). LED goes red
     ~60 s after the server stops responding.

  c. AddTGenDialog.server_rebooted signal — emitted on
     /api/system/reboot 200. The main window's _on_server_rebooted
     handler flips the matching server offline IMMEDIATELY so
     pollers stop spamming the dead host (no waiting for the
     fail-count threshold).
"""
from __future__ import annotations

import re
from pathlib import Path


_CONN_MGR = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "server_retry_workers.py"
)
_SERVER_SECTION = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "server_section.py"
)
_ADD_TGEN = (
    Path(__file__).resolve().parents[1]
    / "widgets" / "add_tgen_dialog.py"
)
_MENU_ACTIONS = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "menu_actions.py"
)


# ─────────────────────────────────── ConnectionManager.quick_get


def test_connection_manager_has_quick_get():
    """ConnectionManager.get() blocks for ~7s on a dead server because
    of the Retry(total=3, backoff=1) on the mounted adapter. quick_get
    must bypass that adapter for periodic-poll callers where fast
    failure beats retry-induced UI freezing."""
    src = _CONN_MGR.read_text()
    assert "def quick_get" in src, (
        "ConnectionManager missing quick_get method — pollers will "
        "keep blocking 7s per request on a dead server."
    )
    # Must NOT use self.session (which has the retry adapter).
    m = re.search(
        r"def quick_get[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "quick_get body not found"
    body = m.group(0)
    assert "self.session.get" not in body, (
        "quick_get uses self.session.get — that adapter has the "
        "retry config and would defeat the whole point. Use bare "
        "requests.get instead."
    )
    assert "requests.get" in body, (
        "quick_get doesn't use bare requests.get to bypass the "
        "retry adapter."
    )


# ─────────────────────────────────── poll_server_health flips offline


def test_poll_server_health_uses_quick_get():
    """poll_server_health is a periodic poll — must use quick_get so
    a dead server doesn't burn 7s per probe."""
    src = _SERVER_SECTION.read_text()
    m = re.search(
        r"def poll_server_health[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert "quick_get" in body, (
        "poll_server_health doesn't use quick_get — every probe "
        "burns 7s of retry on a dead server."
    )


def test_poll_server_health_emits_failure_signal():
    """The exception/non-200 path must EMIT (not silently pass) so
    `_apply_server_health` can count failures and flip offline. The
    pre-v0.5.16 code silently pass'ed, leaving the LED green forever."""
    src = _SERVER_SECTION.read_text()
    m = re.search(
        r"def poll_server_health[\s\S]+?(?=^    def )",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Result signal must have 3 args: server, health_or_None, ok bool.
    assert re.search(
        r"result\s*=\s*pyqtSignal\(object,\s*object,\s*bool\)",
        body,
    ), (
        "_ServerHealthWorker.result signal isn't (object, object, "
        "bool) — needs the ok flag so receiver can distinguish "
        "failure from success."
    )
    # Must emit False on except.
    assert re.search(
        r"except[\s\S]+?self\.result\.emit\(srv,\s*None,\s*False\)",
        body,
    ), (
        "poll_server_health except-branch doesn't emit failure. Pre-"
        "v0.5.16 just `pass`ed, leaving LED green during reboot."
    )


def test_apply_server_health_flips_offline_after_n_failures():
    """_apply_server_health(server, health, ok=False) must increment
    health_fail_count; at HEALTH_OFFLINE_AFTER_N_FAILURES, flip the
    server.online=False and trigger update_server_status_icon."""
    src = _SERVER_SECTION.read_text()
    assert "HEALTH_OFFLINE_AFTER_N_FAILURES" in src, (
        "Server section doesn't define HEALTH_OFFLINE_AFTER_N_FAILURES "
        "— the fail-count threshold constant is what flips the LED red."
    )
    m = re.search(
        r"def _apply_server_health[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Must take `ok` arg with default for back-compat.
    assert "ok=True" in body or "ok = True" in body or \
           re.search(r"_apply_server_health\(self,\s*server,\s*health,\s*ok", body), (
        "_apply_server_health signature missing the ok param needed "
        "to distinguish success from failure."
    )
    # Failure branch increments + flips at threshold.
    assert "health_fail_count" in body, (
        "_apply_server_health doesn't track health_fail_count — no "
        "way to flip after N failures."
    )
    assert "online" in body and "False" in body, (
        "_apply_server_health doesn't flip server.online=False on "
        "failure threshold."
    )
    assert "update_server_status_icon" in body, (
        "_apply_server_health doesn't call update_server_status_icon "
        "when flipping — LED won't visually update."
    )


def test_apply_server_health_resets_fail_count_on_success():
    """A successful health probe must reset the fail counter — else
    one transient blip every minute would slowly accumulate to N
    over hours and flip an actually-healthy server offline."""
    src = _SERVER_SECTION.read_text()
    m = re.search(
        r"def _apply_server_health[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(
        r'server\[["\']health_fail_count["\']\]\s*=\s*0',
        body,
    ), (
        "_apply_server_health doesn't reset health_fail_count on "
        "success. Transient blips would accumulate over hours."
    )


# ─────────────────────────────────── AddTGenDialog server_rebooted signal


def test_add_tgen_dialog_declares_server_rebooted_signal():
    """The dialog must declare a server_rebooted signal so the
    parent can hear about successful reboots and mark the host
    offline immediately."""
    src = _ADD_TGEN.read_text()
    assert "server_rebooted = pyqtSignal" in src, (
        "AddTGenDialog doesn't declare server_rebooted signal. "
        "Without it the main window has no idea a host is going "
        "down via the Reboot button."
    )


def test_add_tgen_dialog_emits_signal_on_reboot_200():
    """_reboot_physical_server must emit server_rebooted on HTTP
    200 — that's the only signal main can use to short-circuit
    the pollers."""
    src = _ADD_TGEN.read_text()
    m = re.search(
        r"def _reboot_physical_server[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    # Must emit when status_code == 200. Window {0,2000} because
    # the docstring/comment between the check and the emit can be
    # large; we just need them in the same 200-success branch
    # (before the next `if r.status_code ==` for non-200 cases).
    assert re.search(
        r"status_code\s*==\s*200[\s\S]{0,2000}?server_rebooted\.emit"
        r"[\s\S]{0,500}?return",
        body,
    ), (
        "_reboot_physical_server doesn't emit server_rebooted in "
        "the 200-success branch (before the return). Main window "
        "stays unaware."
    )


# ─────────────────────────────────── Main wires the signal


def test_menu_actions_wires_server_rebooted_signal():
    """The AddTGenDialog instantiation site must connect
    server_rebooted to a handler. Forgetting this connection makes
    the signal useless."""
    src = _MENU_ACTIONS.read_text()
    assert "server_rebooted.connect" in src, (
        "menu_actions doesn't connect AddTGenDialog.server_rebooted. "
        "Signal fires into the void."
    )
    assert "_on_server_rebooted" in src, (
        "menu_actions doesn't reference _on_server_rebooted handler."
    )


def test_on_server_rebooted_flips_matching_server_offline():
    """_on_server_rebooted must find the matching server by address
    substring and flip online=False, then trigger
    update_server_status_icon."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _on_server_rebooted[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    assert m, "_on_server_rebooted handler not found"
    body = m.group(0)
    assert "server_interfaces" in body, (
        "_on_server_rebooted doesn't iterate server_interfaces — "
        "can't find the matching server to mark offline."
    )
    assert re.search(
        r'["\']online["\']\]?\s*=\s*False',
        body,
    ), (
        "_on_server_rebooted doesn't flip server.online=False."
    )
    assert "update_server_status_icon" in body, (
        "_on_server_rebooted doesn't call update_server_status_icon "
        "— LED won't visually flip to red."
    )


def test_on_server_rebooted_resets_health_fail_count():
    """When marking the server offline due to known reboot, reset
    health_fail_count to 0 so when the server comes back online
    the health-poller starts fresh — not stuck at N-1 failures
    that would falsely flip offline on the first network blip."""
    src = _MENU_ACTIONS.read_text()
    m = re.search(
        r"def _on_server_rebooted[\s\S]+?(?=^    def |\Z)",
        src, re.MULTILINE,
    )
    body = m.group(0)
    assert re.search(
        r'["\']health_fail_count["\']\]?\s*=\s*0',
        body,
    ), (
        "_on_server_rebooted doesn't reset health_fail_count. When "
        "the server comes back, one transient blip could re-flip "
        "it offline because the counter started at N-1."
    )


def test_pyproject_version_at_least_0516():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 16), (
        f"Version {m.group(1)} < 0.5.16"
    )
