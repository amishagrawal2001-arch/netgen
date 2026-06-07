"""v0.5.3 — Restart TGEN Service + Reboot Physical Server moved
from the Server menu to the Add TGEN Chassis dialog.

Operator-stated rationale: the AddTGenDialog already shows the
per-chassis health LED, version, and health columns (v0.2.33 +
v0.2.34). Operators looking at chassis state are the same people
wanting to restart/reboot — keeping those actions in a global
menu meant a context jump (select in tree → open menu → click)
where a single dialog could do both.

This release ALSO adds POST /api/system/restart_service to mirror
the v0.5.2 reboot endpoint. The Server-menu version of Restart
suffered the same `ssh root@host` silent-failure pattern as the
old reboot path. HTTP-first dispatch in the new dialog buttons
eliminates that class of bug for v0.5.3+ servers.

Test contracts pinned:

  1. POST /api/system/restart_service exists on the server.
  2. AddTGenDialog has restart_btn + reboot_btn instances wired
     to handlers that select the row's entry, POST the HTTP
     endpoint, and fall back gracefully on 404 / non-200.
  3. Server menu no longer constructs the two QActions.
  4. Add / Remove TGEN Chassis stay in the Server menu (v0.4.9
     contract preserved).
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_tgen_dialog.py"
_MAIN = Path(__file__).resolve().parents[1] / "traffic_client" / "main.py"


# ─────────────────────── server endpoint ──────────────────────────────


def test_restart_service_endpoint_exists():
    """POST /api/system/restart_service must exist. The
    AddTGenDialog button POSTs to it as the primary action."""
    src = _SERVER.read_text()
    assert '@app.route("/api/system/restart_service", methods=["POST"])' in src, (
        "POST /api/system/restart_service missing. Dialog's "
        "restart button can't reach the server without it."
    )


def test_restart_service_uses_popen_with_delay():
    """The endpoint must Popen-and-return so the HTTP 200 reaches
    the client BEFORE systemctl restarts the Flask process that
    owns the request. Same lesson as the v0.5.2 reboot endpoint."""
    src = _SERVER.read_text()
    m = re.search(
        r"def system_restart_service\(\)[\s\S]+?(?=\n@app\.route|\nclass )",
        src,
    )
    assert m, "system_restart_service body not found"
    body = m.group(0)
    assert "subprocess.Popen" in body, (
        "restart_service uses subprocess.run instead of Popen — "
        "systemctl would kill the Flask thread before HTTP 200 "
        "reaches the client."
    )
    # Must include a sleep so the response lands first.
    assert "sleep" in body, (
        "restart_service has no `sleep N` delay — the systemctl "
        "restart could fire before the response is on the wire."
    )
    # And both unit names (netgen-server + ostg-server fallback)
    assert "netgen-server" in body and "ostg-server" in body, (
        "restart_service doesn't try BOTH netgen-server and "
        "ostg-server — pre-rebrand hosts won't restart."
    )


# ─────────────────────── dialog buttons ───────────────────────────────


def test_dialog_has_restart_and_reboot_buttons():
    """The AddTGenDialog must construct both buttons + add them to
    the history-action button row."""
    src = _DIALOG.read_text()
    assert 'self.restart_btn = QPushButton("Restart TGEN Service")' in src, (
        "AddTGenDialog missing self.restart_btn — the v0.4.9 "
        "Server-menu version was removed; without this the action "
        "is GONE from the GUI entirely."
    )
    assert 'self.reboot_btn = QPushButton("Reboot Physical Server")' in src, (
        "AddTGenDialog missing self.reboot_btn"
    )
    # And both must be added to hist_btns (the button row).
    assert "hist_btns.addWidget(self.restart_btn)" in src
    assert "hist_btns.addWidget(self.reboot_btn)" in src


def test_dialog_buttons_wire_to_handlers():
    """Each button's clicked signal must connect to the right
    handler — empty connect() would be a silent no-op."""
    src = _DIALOG.read_text()
    assert "self.restart_btn.clicked.connect(self._restart_tgen_service)" in src
    assert "self.reboot_btn.clicked.connect(self._reboot_physical_server)" in src


def test_dialog_restart_handler_uses_http_primary():
    """Pre-v0.5.3 the Server-menu version used `ssh root@host
    systemctl restart ...` and had the same silent-failure pattern
    as the old reboot path. The new dialog handler must POST to
    /api/system/restart_service first."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _restart_tgen_service\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_restart_tgen_service body not found"
    body = m.group(0)
    assert "/api/system/restart_service" in body, (
        "Restart handler doesn't POST /api/system/restart_service "
        "— operator hits the silent-failure SSH path again."
    )
    # Must also handle 404 gracefully (pre-v0.5.3 server) with a
    # clear upgrade hint, not a confusing crash.
    assert "404" in body and "v0.5.3" in body, (
        "Restart handler doesn't surface a clear 'upgrade to v0.5.3+' "
        "message on 404. Operators on mixed-version fleets get "
        "confused errors instead of actionable next-step."
    )


def test_dialog_reboot_handler_uses_http_primary():
    """Same contract as restart — primary HTTP path, 404 hint."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _reboot_physical_server\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_reboot_physical_server body not found"
    body = m.group(0)
    assert "/api/system/reboot" in body, (
        "Reboot handler doesn't POST /api/system/reboot"
    )
    assert "404" in body and "v0.5.2" in body, (
        "Reboot handler doesn't surface a 'upgrade to v0.5.2+' "
        "hint on 404"
    )


def test_dialog_reboot_has_strong_warning_dialog():
    """Physical reboot is much more destructive than a service
    restart — the dialog must show a `QMessageBox.warning` (not
    .question) AND mention the 3-5 minute downtime so the operator
    has appropriate friction before confirming."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _reboot_physical_server\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    assert "QMessageBox.warning" in body, (
        "Reboot confirmation uses something other than "
        "QMessageBox.warning — physical-reboot UX needs the "
        "explicit ⚠ warning icon."
    )
    assert "3-5 minutes" in body or "3 to 5 minutes" in body, (
        "Reboot warning doesn't mention the 3-5 minute downtime"
    )


def test_handler_friendly_no_selection_hint():
    """If the operator clicks Restart/Reboot without selecting a
    row, the handler must show a friendly hint instead of failing
    silently or raising IndexError."""
    src = _DIALOG.read_text()
    for method in ("_restart_tgen_service", "_reboot_physical_server"):
        m = re.search(
            rf"def {method}\(self\)[\s\S]+?(?=\n    def )",
            src,
        )
        assert m
        body = m.group(0)
        assert "Pick a chassis" in body, (
            f"{method} doesn't show a 'pick a chassis first' "
            f"hint when no row is selected. Operator gets a "
            f"silent no-op."
        )


# ─────────────────────── Server menu cleanup ──────────────────────────


def test_server_menu_no_longer_has_restart_or_reboot_actions():
    """The two QActions must be REMOVED from main.py's Server menu
    setup. Leaving them would put the action in BOTH places —
    confusing UX and a maintenance burden."""
    src = _MAIN.read_text()
    server_menu_block = re.search(
        r"# Server menu[\s\S]+?(?=# Capture menu|# .* menu\n)",
        src,
    )
    assert server_menu_block, "Server menu block not located"
    block = server_menu_block.group(0)
    forbidden = [
        'QAction("Restart TGEN Service...", self)',
        'QAction("Reboot Physical Server...", self)',
        'server_menu.addAction(restart_tgen_action)',
        'server_menu.addAction(reboot_server_action)',
    ]
    for pat in forbidden:
        assert pat not in block, (
            f"Server menu still contains {pat!r} after v0.5.3 "
            f"move. The action ends up in BOTH the menu AND the "
            f"AddTGenDialog — confusing UX. Remove the menu entry."
        )


def test_add_remove_chassis_still_under_server_menu():
    """v0.5.3 moves Restart + Reboot OUT of the Server menu, but
    Add / Remove TGEN Chassis must STAY (v0.4.9 contract). Pin
    so a `git revert` of the v0.5.3 change doesn't accidentally
    take the Add/Remove pair with it."""
    src = _MAIN.read_text()
    server_block = re.search(
        r"# Server menu[\s\S]+?(?=# Capture menu)",
        src,
    )
    assert server_block
    block = server_block.group(0)
    # Add / Remove TGEN Chassis stay in Server menu
    assert 'QAction("Add TGEN Chassis...", self)' in block, (
        "Add TGEN Chassis QAction missing from Server menu — "
        "v0.5.3 over-removed; v0.4.9 contract violated."
    )
    assert 'QAction("Remove TGEN Chassis", self)' in block, (
        "Remove TGEN Chassis QAction missing from Server menu"
    )
