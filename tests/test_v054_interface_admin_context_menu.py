"""v0.5.4 — right-click on a TGEN-list interface item shows a
Set Online / Set Offline context menu.

Operator request: bring an interface up or down without dropping
to a shell. The server already runs as root; same HTTP-first
pattern as v0.5.2 reboot and v0.5.3 restart-service.

Test contracts:

  1. Server has POST /api/interfaces/<iface>/admin that:
     * Validates iface against /sys/class/net/ (no injection)
     * Strict whitelist: state must be "up" or "down"
     * Uses `ip link set -- <iface> <state>` so a malicious
       interface name starting with '-' can't be parsed as an
       ip-link option
     * Returns operstate so the client shows the operator the
       observed kernel state, not just the request
     * 404 on unknown iface, 400 on bad state

  2. Client's server_tree has setContextMenuPolicy(CustomContext-
     Menu) wired to a handler.

  3. The handler only shows the menu for INTERFACE items
     (parent != None) — not for server-level items.

  4. Set Offline shows a confirmation (it's destructive — stops
     streams); Set Online does not (idempotent, safe).
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"
_CLIENT = (
    Path(__file__).resolve().parents[1]
    / "traffic_client" / "server_section.py"
)


# ─────────────────────────── server endpoint ──────────────────────────


def test_interface_admin_endpoint_registered():
    """POST /api/interfaces/<iface>/admin must exist. Without it
    the client menu items have nothing to call."""
    src = _SERVER.read_text()
    assert '@app.route("/api/interfaces/<iface>/admin", methods=["POST"])' in src, (
        "POST /api/interfaces/<iface>/admin endpoint missing. "
        "Right-click menu has nothing to call."
    )


def test_interface_admin_validates_iface_against_sysfs():
    """The interface name in the URL is operator-controlled. We
    must not pass it into `ip link set` without first checking
    it's a real kernel interface — `/sys/class/net/<name>` is the
    canonical existence test."""
    src = _SERVER.read_text()
    m = re.search(
        r"def interface_admin\(iface\)[\s\S]+?(?=\n@app\.route|\nclass )",
        src,
    )
    assert m, "interface_admin body not found"
    body = m.group(0)
    assert "/sys/class/net/" in body, (
        "interface_admin doesn't validate iface against "
        "/sys/class/net/. Operator-controlled URL component "
        "would flow straight into `ip link set`."
    )
    # Must return 404 if not found.
    assert "404" in body and "not found" in body.lower(), (
        "interface_admin missing 404 path on unknown iface"
    )


def test_interface_admin_strict_state_whitelist():
    """state must be exactly 'up' or 'down'. Anything else gets
    a 400 — no arbitrary `ip link set <iface> <whatever>` paths."""
    src = _SERVER.read_text()
    m = re.search(
        r"def interface_admin\(iface\)[\s\S]+?(?=\n@app\.route|\nclass )",
        src,
    )
    body = m.group(0)
    # The check must reject anything that's not exactly up/down.
    assert re.search(r"state\s+not\s+in\s+\(['\"]up['\"]\s*,\s*['\"]down['\"]", body), (
        "interface_admin's state validation isn't a tuple "
        "whitelist of exactly ('up', 'down') — arbitrary states "
        "could reach `ip link set`."
    )


def test_interface_admin_uses_double_dash_to_separate_args():
    """`ip link set` parses dashes as options. An iface named
    `-rf` would be eaten as an option. `--` between subcommand
    and args fixes it."""
    src = _SERVER.read_text()
    m = re.search(
        r"def interface_admin\(iface\)[\s\S]+?(?=\n@app\.route|\nclass )",
        src,
    )
    body = m.group(0)
    # Look for the argv list with `--` after "set".
    assert re.search(r'"ip",\s*"link",\s*"set",\s*"--"', body) or \
           re.search(r"'ip',\s*'link',\s*'set',\s*'--'", body), (
        "interface_admin's `ip link set` argv doesn't include "
        "`--` after 'set'. An iface named '-rf' (or any name "
        "starting with '-') would be parsed as a flag."
    )


def test_interface_admin_returns_observed_operstate():
    """The response must include kernel-observed `operstate`,
    not just echo what was requested. Operator needs to see
    what actually happened (e.g. requested up, kernel said
    DORMANT because no link)."""
    src = _SERVER.read_text()
    m = re.search(
        r"def interface_admin\(iface\)[\s\S]+?(?=\n@app\.route|\nclass )",
        src,
    )
    body = m.group(0)
    assert "operstate" in body, (
        "interface_admin doesn't read /sys/class/net/<iface>/operstate "
        "— client can't show the operator the kernel's view."
    )


# ─────────────────────────── client context menu ──────────────────────


def test_server_tree_has_custom_context_menu_policy():
    """The server_tree's contextMenuPolicy must be CustomContext-
    Menu so right-clicks fire customContextMenuRequested. Without
    this the menu never opens."""
    src = _CLIENT.read_text()
    assert "self.server_tree.setContextMenuPolicy(Qt.CustomContextMenu)" in src, (
        "server_tree's context menu policy isn't set to "
        "CustomContextMenu — right-clicks won't trigger our handler."
    )
    assert "self.server_tree.customContextMenuRequested.connect" in src, (
        "customContextMenuRequested signal isn't connected to "
        "anything — the handler exists but never fires."
    )


def test_context_menu_handler_skips_server_level_items():
    """The menu should only appear for INTERFACE (child) items.
    Right-clicking a server-level row should do nothing (the
    Reboot/Restart actions live in the AddTGenDialog now)."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _on_server_tree_context_menu\(self, pos\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_on_server_tree_context_menu body not found"
    body = m.group(0)
    # Must check for None parent and bail out.
    assert re.search(r"parent\s*=\s*item\.parent\(\)", body), (
        "Context menu handler doesn't read item.parent() — can't "
        "distinguish server (top-level) from interface (child)."
    )
    # Match `if parent is None:` followed eventually by `return`
    # within the next few lines (comments between are fine).
    assert re.search(
        r"if parent is None:[\s\S]{0,500}?return",
        body,
    ), (
        "Context menu handler doesn't `return` when parent is "
        "None — right-clicking a server row would still show the "
        "interface admin menu (confusing UX)."
    )


def test_context_menu_offers_both_up_and_down():
    """Two QActions: Online and Offline. Without either one the
    menu is half a feature.

    v0.5.5 note: labels were simplified from
    `Set <iface> Online` to just `Online` — the operator
    already sees the interface name in the row they
    right-clicked. The tooltip still echoes the full target.
    Test updated to pin the clean label."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _on_server_tree_context_menu\(self, pos\)[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    # Match QAction("Online", ...) and QAction("Offline", ...)
    assert re.search(r'QAction\(\s*["\']Online["\']\s*,', body), (
        "Context menu missing the Online QAction with the clean "
        "label. v0.5.5 dropped the `Set <iface>` prefix; expected "
        "QAction(\"Online\", ...)."
    )
    assert re.search(r'QAction\(\s*["\']Offline["\']\s*,', body), (
        "Context menu missing the Offline QAction with the clean "
        "label. Expected QAction(\"Offline\", ...)."
    )
    # Forbid the verbose v0.5.4 form so a refactor that re-adds
    # the prefix surfaces here.
    assert "Set {iface_name} Online" not in body, (
        "Context menu re-introduced the verbose `Set <iface> "
        "Online` label. v0.5.5 simplified to bare `Online`."
    )


def test_offline_requires_confirmation_online_does_not():
    """Set Offline stops streams using the interface — must
    require confirmation. Set Online is idempotent + safe — no
    confirmation needed (extra dialog would slow down operators
    bringing things up after maintenance)."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _set_interface_admin_state\(self,[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_set_interface_admin_state body not found"
    body = m.group(0)
    # The 'down' branch must call QMessageBox.warning (or
    # equivalent).
    assert re.search(r'if state == ["\']down["\']\s*:\s*\n[\s\S]{0,200}QMessageBox\.warning', body), (
        "Set Offline doesn't surface a confirmation dialog. "
        "Operator clicks once → streams stop. UX trap."
    )


def test_handler_surfaces_404_with_upgrade_hint():
    """If the operator targets a pre-v0.5.4 server, the endpoint
    returns 404. Surface a clear 'upgrade to v0.5.4' hint
    instead of a confusing generic error."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _set_interface_admin_state\(self,[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    assert "404" in body and "v0.5.4" in body, (
        "Handler doesn't surface a v0.5.4 upgrade hint on HTTP "
        "404 — operator gets confused 'admin failed' error "
        "with no actionable next step."
    )


def test_success_path_triggers_tree_refresh():
    """After flipping admin state, refresh the tree so the
    operator SEES the new state without manually re-clicking
    the chassis."""
    src = _CLIENT.read_text()
    m = re.search(
        r"def _set_interface_admin_state\(self,[\s\S]+?(?=\n    def )",
        src,
    )
    body = m.group(0)
    assert "update_server_tree" in body, (
        "Success path doesn't refresh the server tree — operator "
        "won't see the new interface state until they re-click "
        "the chassis."
    )
