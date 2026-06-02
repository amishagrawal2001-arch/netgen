"""v0.3.10 — Server menu "Mark Selected Servers Offline" action.

User asked for a way to manually mark a TG chassis as offline
from the Server menu. Pre-v0.3.10 only the inverse existed
("Make Selected Servers Online" — for reconnecting failed
TGs). v0.3.10 adds the mark-offline action with behaviour:

  * Validates a selection exists; surfaces an info dialog
    otherwise.
  * Filters to currently-online servers; surfaces a
    different info dialog if every selected TG is already
    offline.
  * Confirms via QMessageBox.question before mutating state.
  * On Yes: sets `server["online"] = False`, adds to
    `failed_servers`, calls `update_server_status_icon` which
    triggers the v0.3.9 iface-cascade.
  * NOT persistent — next health probe flips reachable
    servers back online. Tooltip + confirm dialog document
    this trade-off.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO = Path(__file__).resolve().parent.parent
MAIN_PY = REPO / "traffic_client" / "main.py"
MENU_ACTIONS_PY = REPO / "traffic_client" / "menu_actions.py"


# ─────────────────────────────────────── wiring
def test_v0_3_10_action_exists_in_server_menu():
    """The QAction must be added to the Server menu next to the
    existing online-toggle action so operators discover it
    naturally."""
    src = MAIN_PY.read_text()
    # Action defined.
    assert "self.mark_server_offline_action = QAction(" in src, (
        "mark_server_offline_action QAction not defined"
    )
    # Label is the operator-facing text.
    assert '"Mark Selected Servers Offline"' in src
    # Added to the server menu.
    assert "server_menu.addAction(self.mark_server_offline_action)" in src
    # Wired to the handler.
    assert "self.mark_selected_servers_offline" in src


def test_v0_3_10_action_enabled_by_default():
    """Always-enabled — handler validates selection. Tying
    enablement to a selection-change subscription was rejected
    as over-engineering for marginal UX value."""
    src = MAIN_PY.read_text()
    body = re.search(
        r'self\.mark_server_offline_action = QAction\(.*?'
        r'self\.mark_server_offline_action\.triggered\.connect',
        src, flags=re.DOTALL,
    )
    assert body is not None
    block = body.group(0)
    assert "setEnabled(True)" in block, (
        "mark_server_offline_action should be enabled by default"
    )


def test_v0_3_10_handler_method_defined():
    """The handler must be a named method (visible in tracebacks,
    findable in future audits)."""
    src = MENU_ACTIONS_PY.read_text()
    assert re.search(
        r"^    def mark_selected_servers_offline\(self\)",
        src, flags=re.MULTILINE,
    ), "mark_selected_servers_offline method missing"


# ─────────────────────────────────────── handler behaviour
def _load_handler():
    """Pull the handler out of menu_actions.TrafficGenClientMenuAction
    as an unbound method so we can call it against a mock self."""
    from traffic_client.menu_actions import TrafficGenClientMenuAction
    return TrafficGenClientMenuAction.mark_selected_servers_offline


def test_v0_3_10_no_selection_shows_info_dialog():
    handler = _load_handler()
    instance = MagicMock()
    instance.selected_servers = []
    with patch("PyQt5.QtWidgets.QMessageBox.information") as info, \
         patch("PyQt5.QtWidgets.QMessageBox.question") as question:
        handler(instance)
    assert info.called, "no-selection path didn't show the info dialog"
    assert not question.called, (
        "no-selection path should NOT prompt for confirmation — "
        "there's nothing to confirm"
    )


def test_v0_3_10_all_already_offline_shows_info_dialog():
    handler = _load_handler()
    instance = MagicMock()
    instance.selected_servers = [
        {"address": "http://a:5050", "online": False},
        {"address": "http://b:5050", "online": False},
    ]
    with patch("PyQt5.QtWidgets.QMessageBox.information") as info, \
         patch("PyQt5.QtWidgets.QMessageBox.question") as question:
        handler(instance)
    assert info.called, (
        "all-already-offline path didn't show the info dialog"
    )
    # Specifically the "already offline" message, not the no-selection one.
    args = info.call_args[0]
    title = args[1] if len(args) >= 2 else ""
    assert "Online" in title or "Offline" in title, (
        f"info dialog title doesn't reference online/offline: {title!r}"
    )


def test_v0_3_10_user_declines_confirm_no_state_mutation():
    handler = _load_handler()
    server = {"address": "http://lab:5050", "online": True}
    instance = MagicMock()
    instance.selected_servers = [server]
    instance.failed_servers = []
    with patch("PyQt5.QtWidgets.QMessageBox.question",
               return_value=MagicMock()) as question:
        # Default-No on the real dialog returns QMessageBox.No.
        # Force the return value so the handler treats it as decline.
        from PyQt5.QtWidgets import QMessageBox
        question.return_value = QMessageBox.No
        handler(instance)
    # Server state unchanged.
    assert server["online"] is True, (
        "user declined but server was still marked offline — "
        "confirmation gate doesn't work"
    )
    assert server not in instance.failed_servers


def test_v0_3_10_user_confirms_marks_online_servers_offline():
    handler = _load_handler()
    server_a = {"address": "http://a:5050", "online": True}
    server_b = {"address": "http://b:5050", "online": True}
    server_c = {"address": "http://c:5050", "online": False}  # already off
    instance = MagicMock()
    instance.selected_servers = [server_a, server_b, server_c]
    instance.failed_servers = []
    from PyQt5.QtWidgets import QMessageBox
    with patch("PyQt5.QtWidgets.QMessageBox.question",
               return_value=QMessageBox.Yes):
        handler(instance)
    # Both previously-online servers now marked offline.
    assert server_a["online"] is False, (
        "server_a still online after confirmation"
    )
    assert server_b["online"] is False, (
        "server_b still online after confirmation"
    )
    # Already-offline server untouched (filtered out before the
    # confirmation walks the list).
    assert server_c["online"] is False  # unchanged
    # Both went into failed_servers.
    assert server_a in instance.failed_servers
    assert server_b in instance.failed_servers
    # update_server_status_icon was called for each marked server.
    calls = instance.update_server_status_icon.call_args_list
    addresses_called = {c.args[0]["address"] for c in calls}
    assert addresses_called == {"http://a:5050", "http://b:5050"}, (
        f"unexpected update_server_status_icon calls: {addresses_called}"
    )
    # Each call passed is_online=False.
    for c in calls:
        assert c.args[1] is False


def test_v0_3_10_user_confirms_enables_make_online_action():
    """After marking servers offline, the inverse action ('Make
    Selected Servers Online') must become enabled so the operator
    can flip them back without restarting the client."""
    handler = _load_handler()
    server = {"address": "http://lab:5050", "online": True}
    instance = MagicMock()
    instance.selected_servers = [server]
    instance.failed_servers = []
    from PyQt5.QtWidgets import QMessageBox
    with patch("PyQt5.QtWidgets.QMessageBox.question",
               return_value=QMessageBox.Yes):
        handler(instance)
    instance.make_server_online_action.setEnabled.assert_called_with(True)


def test_v0_3_10_user_confirms_refreshes_server_tree():
    """The tree rebuild ensures the new offline parent dot + the
    v0.3.9 iface-children cascade both render together."""
    handler = _load_handler()
    server = {"address": "http://lab:5050", "online": True}
    instance = MagicMock()
    instance.selected_servers = [server]
    instance.failed_servers = []
    from PyQt5.QtWidgets import QMessageBox
    with patch("PyQt5.QtWidgets.QMessageBox.question",
               return_value=QMessageBox.Yes):
        handler(instance)
    instance.update_server_tree.assert_called_once()
