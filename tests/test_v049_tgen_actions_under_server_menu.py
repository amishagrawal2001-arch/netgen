"""v0.4.9 — "Add TGEN Chassis" and "Remove TGEN Chassis" moved from
the File menu to the top of the Server menu.

Operator-reported: kept looking for the TGen-add action under
Server, since every other TGen-management action (Make Online /
Mark Offline / Restart Service / Reboot Physical) already lives
there. Top of the File menu was a leftover from when the app had
only "Add Chassis" + "Save Session" as its first two menu items
and the distinction didn't matter.

This test pins the source layout — both actions are constructed
inside the Server-menu block, with `server_menu.addAction(...)`
calls (NOT `file_menu.addAction(...)`). A future refactor that
moves them back to File would break the test here, not at the
operator's chair.
"""
from __future__ import annotations

import re
from pathlib import Path


_MAIN = Path(__file__).resolve().parents[1] / "traffic_client" / "main.py"


def _server_menu_block(src: str) -> str:
    """Return the block of source between the Server-menu marker
    and the Capture-menu marker. Captures every addAction call
    inside the Server menu setup."""
    m = re.search(
        r"# Server menu[\s\S]+?(?=# Capture menu)",
        src,
    )
    assert m, "Server-menu block not found"
    return m.group(0)


def _file_menu_block(src: str) -> str:
    """Return the File-menu block up to the Server-menu marker."""
    m = re.search(
        r"# File menu[\s\S]+?(?=# Server menu)",
        src,
    )
    assert m, "File-menu block not found"
    return m.group(0)


def test_add_chassis_action_constructed_under_server_menu():
    """The QAction with text 'Add TGEN Chassis...' must be created
    AND added to the Server menu — not the File menu."""
    src = _MAIN.read_text()
    server_block = _server_menu_block(src)
    file_block = _file_menu_block(src)

    assert 'QAction("Add TGEN Chassis...", self)' in server_block, (
        "Add TGEN Chassis action isn't constructed inside the Server-"
        "menu block. Operators kept hunting for it under Server "
        "since every other TGen-management action lives there."
    )
    # The same construction must NOT happen in the File menu
    # (otherwise we'd have two duplicate actions).
    assert 'QAction("Add TGEN Chassis...", self)' not in file_block, (
        "Add TGEN Chassis action is constructed inside BOTH menus. "
        "v0.4.9 moved it OUT of File and INTO Server — pick one. "
        "Duplicate construction would put the action in two menus."
    )
    assert "server_menu.addAction(add_server_action)" in server_block, (
        "add_server_action variable wasn't added to server_menu — "
        "the QAction exists but is orphaned."
    )


def test_remove_chassis_action_constructed_under_server_menu():
    src = _MAIN.read_text()
    server_block = _server_menu_block(src)
    file_block = _file_menu_block(src)

    assert 'QAction("Remove TGEN Chassis", self)' in server_block, (
        "Remove TGEN Chassis action isn't constructed inside the "
        "Server-menu block."
    )
    assert 'QAction("Remove TGEN Chassis", self)' not in file_block, (
        "Remove TGEN Chassis action is constructed inside BOTH menus."
    )
    assert "server_menu.addAction(remove_server_action)" in server_block, (
        "remove_server_action wasn't added to server_menu."
    )


def test_ctrlN_shortcut_moved_with_add_chassis_action():
    """Ctrl+N was bound to Add TGEN Chassis when it lived in File.
    The shortcut should travel WITH the action, not stay behind as
    a phantom binding."""
    src = _MAIN.read_text()
    server_block = _server_menu_block(src)
    # The setShortcut call must be inside the Server-menu block,
    # adjacent to the add_server_action construction.
    assert re.search(
        r'add_server_action\s*=\s*QAction\("Add TGEN Chassis\.\.\.[\s\S]{0,200}?'
        r'add_server_action\.setShortcut\(QKeySequence\("Ctrl\+N"\)\)',
        server_block,
    ), (
        "Ctrl+N shortcut for Add TGEN Chassis didn't move with the "
        "action to the Server menu. Either the shortcut was dropped "
        "or it's still wired to a phantom File-menu binding."
    )


def test_file_menu_no_longer_adds_chassis_actions():
    """The File menu must NOT add Add/Remove TGEN Chassis. A
    leftover `file_menu.addAction(add_server_action)` line would
    put the action in both menus — confusing UX."""
    src = _MAIN.read_text()
    file_block = _file_menu_block(src)
    forbidden_in_file = [
        "file_menu.addAction(add_server_action)",
        "file_menu.addAction(remove_server_action)",
    ]
    for pat in forbidden_in_file:
        assert pat not in file_block, (
            f"File menu still contains {pat!r} — the action ends up "
            f"in both File AND Server. Remove the file_menu.addAction "
            f"call when moving to Server."
        )


def test_add_chassis_appears_above_make_online_in_server_menu():
    """v0.4.9 places the Add/Remove pair at the TOP of the Server
    menu, above 'Make Selected Servers Online'. Pin the ordering
    so a future addAction refactor doesn't accidentally bury them
    at the bottom of the menu."""
    src = _MAIN.read_text()
    server_block = _server_menu_block(src)

    add_idx = server_block.find("server_menu.addAction(add_server_action)")
    remove_idx = server_block.find("server_menu.addAction(remove_server_action)")
    online_idx = server_block.find("self.make_server_online_action = QAction")

    assert add_idx >= 0 and remove_idx >= 0 and online_idx >= 0
    assert add_idx < remove_idx < online_idx, (
        f"Server-menu ordering wrong: Add Chassis must come before "
        f"Remove Chassis must come before Make Online. Got positions "
        f"add={add_idx}, remove={remove_idx}, online={online_idx}."
    )


def test_separator_between_chassis_pair_and_online_actions():
    """A separator between the chassis-pair (Add/Remove) and the
    online-state actions (Make Online / Mark Offline) makes the
    menu visually clear: TGen-list management above, per-TGen state
    below. Pin its presence."""
    src = _MAIN.read_text()
    server_block = _server_menu_block(src)

    # Need a server_menu.addSeparator() AFTER remove_server_action
    # but BEFORE make_server_online_action.
    remove_idx = server_block.find("server_menu.addAction(remove_server_action)")
    sep_idx = server_block.find("server_menu.addSeparator()", remove_idx)
    online_idx = server_block.find(
        "self.make_server_online_action = QAction", remove_idx,
    )

    assert sep_idx > remove_idx, (
        "No separator after the chassis-add/remove pair — Make "
        "Online sits directly below Remove with no visual break."
    )
    assert sep_idx < online_idx, (
        "Separator placement wrong: should be between Remove (above) "
        "and Make Online (below). Got remove < online < separator."
    )
