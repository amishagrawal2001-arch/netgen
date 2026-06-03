"""v0.3.11 — Save Session file enhancements.

The Save Session UX got three coupled additions in v0.3.11:

  A. Save Session As… / Load Session From… file pickers — the active
     session is no longer locked to ``session.json`` in the data dir;
     operators can keep named snapshots ("baseline-evpn.json",
     "stress-1500.json") and Ctrl+S writes to whichever file is
     currently active.

  C. Title-bar shows the active session basename + a dirty marker
     (``*``) while a save is in flight or pending. Operators can tell
     which file they're editing without trawling the File menu, and
     can tell whether their in-memory state has been persisted yet.

  D. File → Recent Sessions submenu (last 5, persisted across
     restarts) + a brief status-bar toast on every successful save
     ("Saved 18 devices, 42 streams to baseline-evpn.json").

This test file pins the mechanics so a refactor can't silently
regress to the single-fixed-slot world.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


REPO = Path(__file__).resolve().parent.parent
MENU_ACTIONS = REPO / "traffic_client" / "menu_actions.py"
MAIN = REPO / "traffic_client" / "main.py"
RECENT_HELPER = REPO / "utils" / "recent_sessions.py"


# ─────────────────────────────────── recent_sessions helper unit tests

def test_recent_sessions_helper_exists():
    assert RECENT_HELPER.exists(), (
        "utils/recent_sessions.py missing — Recent Sessions menu has "
        "no backing store"
    )


def _patched_data_dir(tmp_path):
    """Repoint get_ostg_data_directory at a tmp dir so the helper
    writes its JSON there instead of the real ostg data folder."""
    from utils import path_utils
    return patch.object(
        path_utils, "get_ostg_data_directory",
        lambda: str(tmp_path),
    )


def test_recent_sessions_roundtrip(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        # Create three real files so the dead-link filter doesn't
        # strip them.
        paths = []
        for n in ("a.json", "b.json", "c.json"):
            p = tmp_path / n
            p.write_text("{}")
            paths.append(str(p))

        assert recent_sessions.load_recent() == []
        for p in paths:
            recent_sessions.add_recent(p)
        # Most-recent-first ordering — reverse of insertion.
        assert recent_sessions.load_recent() == list(reversed(paths))


def test_recent_sessions_dedup_moves_to_front(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        a = tmp_path / "a.json"; a.write_text("{}")
        b = tmp_path / "b.json"; b.write_text("{}")
        recent_sessions.add_recent(str(a))
        recent_sessions.add_recent(str(b))
        # Re-add a — should jump back to the front, not duplicate.
        recent_sessions.add_recent(str(a))
        got = recent_sessions.load_recent()
        assert got[0] == str(a)
        assert len(got) == 2


def test_recent_sessions_capped_at_max(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        for i in range(recent_sessions.MAX_RECENT + 7):
            p = tmp_path / f"f{i}.json"
            p.write_text("{}")
            recent_sessions.add_recent(str(p))
        assert len(recent_sessions.load_recent()) == recent_sessions.MAX_RECENT


def test_recent_sessions_filters_dead_links(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        a = tmp_path / "a.json"; a.write_text("{}")
        b = tmp_path / "b.json"; b.write_text("{}")
        recent_sessions.add_recent(str(a))
        recent_sessions.add_recent(str(b))
        # Delete one file — load_recent should skip it (operator
        # moved/deleted the snapshot since last open).
        a.unlink()
        got = recent_sessions.load_recent()
        assert str(a) not in got
        assert str(b) in got


def test_recent_sessions_add_empty_is_noop(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        # File picker returns "" on cancel — add_recent must not
        # poison the list with an empty entry.
        before = recent_sessions.load_recent()
        recent_sessions.add_recent("")
        recent_sessions.add_recent(None)
        after = recent_sessions.load_recent()
        assert before == after


def test_recent_sessions_load_handles_corruption(tmp_path):
    with _patched_data_dir(tmp_path):
        from utils import recent_sessions
        # Corrupt the recent-sessions JSON; load_recent must return
        # [] rather than raising — the File menu must always open.
        rf = tmp_path / "recent_sessions.json"
        rf.write_text("{not valid json")
        assert recent_sessions.load_recent() == []


# ─────────────────────────────────── source-grep contracts

@pytest.fixture(scope="module")
def menu_src():
    return MENU_ACTIONS.read_text()


@pytest.fixture(scope="module")
def main_src():
    return MAIN.read_text()


def test_save_session_as_method_defined(menu_src):
    assert re.search(
        r"^    def save_session_as\(self\)",
        menu_src, flags=re.MULTILINE,
    ), "save_session_as method missing"


def test_load_session_from_method_defined(menu_src):
    assert re.search(
        r"^    def load_session_from\(self\)",
        menu_src, flags=re.MULTILINE,
    ), "load_session_from method missing"


def test_load_session_accepts_path_kwarg(menu_src):
    """load_session(skip_servers=False, session_file_path=None) so
    Load From… can thread a custom path through without rewiring
    every internal caller."""
    body = re.search(
        r"def load_session\(self,[^)]*\)", menu_src,
    )
    assert body is not None
    assert "session_file_path" in body.group(0), (
        "load_session lost the session_file_path kwarg — Load From… "
        "and Recent Sessions can't repoint the active file"
    )


def test_save_session_impl_writes_to_current_session_path(menu_src):
    """The disk write must use `_current_session_path` (with the
    default-path fallback). Without this, Save As silently writes
    back to session.json instead of the chosen file."""
    # Pin the resolution snippet — exact phrasing intentional so a
    # cosmetic edit can't silently revert it.
    assert "_current_session_path" in menu_src, (
        "_current_session_path resolution missing from menu_actions.py"
    )
    # The pattern: `getattr(self, "_current_session_path", None) or
    # get_session_file_path()`. Pin both halves.
    assert re.search(
        r'getattr\(self,\s*"_current_session_path"',
        menu_src,
    ), "default-path fallback expression broken"


def test_save_session_impl_records_summary(menu_src):
    """`_last_save_summary` must be populated at write time so the
    post-save toast can quote {devices, streams, path} without
    re-walking the live structures."""
    assert "_last_save_summary" in menu_src
    # Pin the keys that _post_save_toast reads.
    for key in ('"path"', '"devices"', '"streams"'):
        assert key in menu_src, (
            f"_last_save_summary missing the {key} field — toast will "
            f"display a stale count"
        )


def test_post_save_toast_method_defined(menu_src):
    assert re.search(
        r"^    def _post_save_toast\(self\)",
        menu_src, flags=re.MULTILINE,
    )


def test_update_window_title_method_defined(menu_src):
    assert re.search(
        r"^    def _update_window_title\(self\)",
        menu_src, flags=re.MULTILINE,
    )


def test_rebuild_recent_sessions_menu_method_defined(menu_src):
    assert re.search(
        r"^    def _rebuild_recent_sessions_menu\(self\)",
        menu_src, flags=re.MULTILINE,
    )


def test_save_finished_clears_dirty_marker(menu_src):
    """_on_save_finished must call _update_window_title so the
    dirty-marker asterisk clears once the worker reports done."""
    body = re.search(
        r"def _on_save_finished\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    assert "_update_window_title" in body.group(0), (
        "_on_save_finished doesn't refresh the title — dirty marker "
        "stays on screen forever after a save"
    )


def test_save_finished_posts_toast_on_real_save(menu_src):
    """The toast should fire on real saves, NOT on the 'no changes
    — skipped' hash short-circuit (otherwise the trailing-edge
    coalesce double-toasts)."""
    body = re.search(
        r"def _on_save_finished\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "_post_save_toast" in text, "toast hook missing from _on_save_finished"
    # Pin that the toast call is in the "real save" branch, not the
    # no-op branch. The structure is `if ... no changes ... else:
    # logger.info ... _post_save_toast()`.
    no_change_idx = text.find('"no changes — skipped"')
    toast_idx = text.find("_post_save_toast")
    assert no_change_idx > 0 and toast_idx > 0
    assert toast_idx > no_change_idx, (
        "_post_save_toast call moved above the no-changes branch — "
        "trailing-edge no-ops would now double-toast"
    )


# ─────────────────────────────────── main.py menu wiring

def test_main_initializes_current_session_path(main_src):
    """main.py __init__ must set _current_session_path before the
    menu is built so the title bar shows the right basename on first
    paint."""
    assert "self._current_session_path" in main_src, (
        "main.py no longer initializes _current_session_path — "
        "title bar will be missing the session basename"
    )
    # The init should reference get_session_file_path as the default.
    assert "get_session_file_path" in main_src, (
        "main.py no longer imports the default session file resolver"
    )


def test_main_adds_save_as_menu_action(main_src):
    assert "Save Session As" in main_src, (
        "'Save Session As…' menu entry missing"
    )
    assert "Ctrl+Shift+S" in main_src, (
        "Ctrl+Shift+S shortcut for Save As missing"
    )
    assert "save_session_as" in main_src, (
        "Save As action not wired to save_session_as handler"
    )


def test_main_adds_load_from_menu_action(main_src):
    assert "Load Session From" in main_src
    assert "load_session_from" in main_src


def test_main_creates_recent_sessions_submenu(main_src):
    assert "recent_sessions_menu" in main_src, (
        "recent_sessions_menu attribute not created on main window"
    )
    assert "_rebuild_recent_sessions_menu" in main_src, (
        "main.py doesn't kick the initial Recent Sessions build"
    )


# ─────────────────────────────────── live behaviour

def test_update_window_title_includes_basename(qapp, tmp_path):
    """_update_window_title bakes the basename into
    _base_window_title so the existing _update_section_size_readout
    appends dimensions to the right base."""
    from traffic_client.menu_actions import TrafficGenClientMenuAction
    from PyQt5.QtWidgets import QMainWindow

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h._current_session_path = str(tmp_path / "baseline.json")
    h._save_in_progress = False
    h._save_pending = False
    h._update_window_title()
    assert "baseline.json" in h._base_window_title
    assert "*" not in h._base_window_title  # not dirty


def test_update_window_title_shows_dirty_marker(qapp, tmp_path):
    from traffic_client.menu_actions import TrafficGenClientMenuAction
    from PyQt5.QtWidgets import QMainWindow

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h._current_session_path = str(tmp_path / "stress.json")
    h._save_in_progress = True  # save in flight
    h._save_pending = False
    h._update_window_title()
    assert "stress.json" in h._base_window_title
    assert h._base_window_title.endswith("*"), (
        "dirty marker missing while _save_in_progress is True"
    )

    # Clear in-flight, set pending — still dirty.
    h._save_in_progress = False
    h._save_pending = True
    h._update_window_title()
    assert h._base_window_title.endswith("*")

    # Neither — clean.
    h._save_pending = False
    h._update_window_title()
    assert not h._base_window_title.endswith("*")


def test_post_save_toast_writes_to_status_bar(qapp, tmp_path):
    from traffic_client.menu_actions import TrafficGenClientMenuAction
    from PyQt5.QtWidgets import QMainWindow

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h._last_save_summary = {
        "path": str(tmp_path / "evpn.json"),
        "devices": 18,
        "streams": 42,
    }
    h._post_save_toast()
    msg = h.statusBar().currentMessage()
    assert "18" in msg and "42" in msg
    assert "evpn.json" in msg, f"basename not in toast: {msg!r}"


def test_post_save_toast_uses_singular_plural_correctly(qapp, tmp_path):
    from traffic_client.menu_actions import TrafficGenClientMenuAction
    from PyQt5.QtWidgets import QMainWindow

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h._last_save_summary = {
        "path": str(tmp_path / "x.json"),
        "devices": 1, "streams": 1,
    }
    h._post_save_toast()
    msg = h.statusBar().currentMessage()
    assert "1 device," in msg, f"singular 'device' missing: {msg!r}"
    assert "1 stream " in msg, f"singular 'stream' missing: {msg!r}"


# ─────────────────────────────────── v0.3.11 follow-up: bug fixes
#
# User reported "trying to load saved session, not seeing saved
# configs" immediately after the Save/Load As… work landed. Two
# real bugs surfaced and both shipped fixes pinned below:
#
#   1. load_session() only called update_bgp_table() — OSPF / ISIS /
#      DHCP / VXLAN tabs stayed stale after a load. The fix iterates
#      every protocol-tab refresh handler.
#
#   2. The startup-merge path appended servers from session_data to
#      self.server_interfaces instead of replacing them, leaving
#      ghosts when Load From… loaded a different snapshot. Same for
#      removed_* sets and the save-hash. The fix marks
#      `session_file_path` loads as full replaces.


def test_load_session_refreshes_all_protocol_tabs(menu_src):
    """The post-load refresh loop must hit every protocol sub-tab,
    not just BGP. Pin the handler names so a future commit can't
    silently drop OSPF / ISIS / DHCP / VXLAN."""
    body = re.search(
        r"def load_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    for handler in (
        "update_bgp_table",
        "update_ospf_table",
        "update_isis_table",
        "refresh_dhcp_status",
        "refresh_vxlan_table",
    ):
        assert handler in text, (
            f"load_session no longer refreshes {handler!r} — that "
            f"protocol tab will show stale state after a load"
        )


def test_load_session_routes_dhcp_vxlan_via_handlers(menu_src):
    """BGP / OSPF / ISIS have wrapper methods on devices_tab itself.
    DHCP and VXLAN refresh methods live ONLY on the handler — calling
    `self.devices_tab.refresh_dhcp_status()` silently no-ops because
    the attribute doesn't exist (the original "fix" attempt had this
    exact bug). Pin that the loader routes through the handler
    attributes for those two."""
    body = re.search(
        r"def load_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # Both handler attribute names must appear in the refresh table
    # so the call goes to `devices_tab.dhcp_handler.refresh_dhcp_status`
    # not the missing `devices_tab.refresh_dhcp_status`.
    assert '"dhcp_handler"' in text, (
        "load_session routes DHCP refresh through devices_tab directly "
        "— refresh_dhcp_status doesn't exist there, must go via "
        "dhcp_handler"
    )
    assert '"vxlan_handler"' in text, (
        "load_session routes VXLAN refresh through devices_tab directly "
        "— refresh_vxlan_table doesn't exist there, must go via "
        "vxlan_handler"
    )


def test_load_session_repairs_malformed_device_interface(menu_src):
    """Real user-reported bug: a saved session had device Interface
    ' - ens5np0' (missing the 'TG N - ' prefix). load_session bucketed
    the device under that malformed key, then update_device_table /
    update_bgp_table couldn't find it because every other lookup uses
    the 'TG X - port' form. Result: device in `all_devices` but
    invisible everywhere in the UI.

    Pin the three-tier repair so it can't silently regress:
      1. suffix-match against `_suffix_to_canonical`
      2. single-server fallback (this is the tier that fixed the
         actual user file — their saved port didn't even exist on
         the current server's reported interfaces)
      3. give-up + warn
    """
    body = re.search(
        r"def load_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # Tier 1: suffix-to-canonical map built from server_interfaces.
    assert "_suffix_to_canonical" in text, (
        "load_session no longer builds the suffix-to-canonical map "
        "— tier-1 Interface repair is gone"
    )
    # Tier 2: single-server fallback (the most-impactful tier — the
    # actual user file landed here when its saved port wasn't in any
    # current server's interfaces list).
    assert "len(self.server_interfaces) == 1" in text, (
        "load_session no longer has the single-server fallback for "
        "Interface repair — devices saved against a different port "
        "topology will silently vanish from every tab"
    )
    # Pin the rewrite-log line so the repair leaves an audit trail
    # the operator can grep for when debugging.
    assert "Repaired malformed" in text, (
        "load_session no longer logs the Interface repair — operators "
        "debugging 'where did my device go' lose the breadcrumb"
    )


def test_load_session_repair_against_user_reported_file(qapp, tmp_path):
    """Construct a session file matching the shape of the file the
    user reported failing (1 device with Interface=' - ens5np0',
    1 server with tg_id=1 whose interfaces list does NOT contain
    ens5np0) and confirm `load_session` lands the device under
    'TG 1 - ens5np0' (the single-server fallback tier)."""
    from PyQt5.QtWidgets import QMainWindow
    from unittest.mock import patch

    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({
        "servers": [{
            "address": "http://lab:5050",
            "tg_id": 1,
            "online": True,
            "interfaces": ["eno8303", "br0", "lo"],  # NOT ens5np0
        }],
        "devices": {
            "device1": {
                "Device Name": "device1",
                "Interface": " - ens5np0",  # malformed
                "device_id": "abc-123",
                "Status": "Stopped",
                "IPv4": "10.0.0.1",
                "MAC Address": "00:11:22:33:44:55",
                "protocols": {"BGP": {}},
                "bgp_config": {"bgp_asn": "65000"},
            },
        },
        "streams": {},
        "removed_servers": [],
        "removed_interfaces": [],
        "removed_devices": [],
        "selected_servers": [],
        "bgp_route_pools": [],
        "protocols": {},
    }))

    from traffic_client.menu_actions import TrafficGenClientMenuAction

    class _StubServerManager:
        def register_server(self, *a, **k): pass
        @staticmethod
        def _extract_server_id_from_url(url): return url
        def initialize_from_server_interfaces(self, *a, **k): pass

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        def __init__(self):
            super().__init__()
            self.streams = {}
            self.failed_servers = []
            self.server_interfaces = []
            self.removed_servers = set()
            self.removed_interfaces = set()
            self.selected_servers = []
            self.all_devices = {}
            self.devices_tab = None  # skip the protocol-refresh loop
            self.bgp_route_pools = []
            self.server_url_from_cli = False
            self.server_manager = _StubServerManager()
            self._current_session_path = str(session_file)

        # Stubs for whatever load_session reaches into.
        def __getattr__(self, name):
            return lambda *a, **k: None

    h = _Host()
    # Skip async iface fetch + auto-start path — those need a server
    # we don't have. Patch them to no-ops.
    with patch.object(h, "_fetch_interfaces_async", lambda *a, **k: None), \
         patch.object(h, "_auto_start_streams_from_session", lambda *a, **k: None):
        h.load_session(session_file_path=str(session_file))

    # Device should now be under the canonical key.
    keys = list(h.all_devices.keys())
    assert "TG 1 - ens5np0" in keys, (
        f"Expected device repaired to 'TG 1 - ens5np0', "
        f"got keys: {keys}"
    )
    devices_under_canonical = h.all_devices["TG 1 - ens5np0"]
    assert len(devices_under_canonical) == 1
    assert devices_under_canonical[0]["Device Name"] == "device1"
    # The device dict's own Interface field must also be rewritten in
    # place so a subsequent save persists the canonical form.
    assert devices_under_canonical[0]["Interface"] == "TG 1 - ens5np0"


# ─────────────────────────────────── v0.3.11 follow-up: auto-save opt-in
#
# User asked: "Make auto-save opt-in". The pre-v0.3.11 behaviour
# wrote to disk on every edit (BGP / OSPF / ISIS / DHCP / VXLAN
# apply, stream remove, inline cell edits) via ~30 call sites that
# each invoke `self.save_session()`. The problem: removing a row
# while experimenting silently committed the deletion to the active
# session file. v0.3.11 puts a single gate at the entry to
# `save_session(manual=False)` — when `_auto_save_enabled` is False
# (the new default), edit-triggered saves no-op. Only deliberate
# user actions (Ctrl+S, Save As…) bypass the gate via `manual=True`.


def test_save_session_accepts_manual_kwarg(menu_src):
    """save_session signature must include `manual: bool = False`
    so the auto-save gate is reachable from both edit handlers
    (default) and user-initiated callers (Ctrl+S, Save As)."""
    body = re.search(
        r"def save_session\(self,[^)]*\)", menu_src,
    )
    assert body is not None
    assert "manual" in body.group(0), (
        "save_session lost the manual kwarg — Ctrl+S and Save As "
        "can't bypass the auto-save-disabled gate"
    )


def test_save_session_early_returns_when_auto_disabled(menu_src):
    """Auto-save gate must short-circuit BEFORE any worker-thread
    setup. Pin the literal so a refactor that moves the check past
    the throttle / lock logic doesn't accidentally let auto-saves
    through."""
    body = re.search(
        r"def save_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # Gate must reference both `manual` and `_auto_save_enabled`.
    assert "if not manual and not getattr(self, \"_auto_save_enabled\"" in text, (
        "auto-save gate logic missing from save_session"
    )


def test_toggle_auto_save_enabled_method_defined(menu_src):
    assert re.search(
        r"^    def toggle_auto_save_enabled\(self,",
        menu_src, flags=re.MULTILINE,
    ), "toggle_auto_save_enabled handler missing"


def test_toggle_handler_persists_via_qsettings(menu_src):
    """The toggle must persist to QSettings so the choice survives
    a restart — otherwise the operator's preference is forgotten
    every launch."""
    body = re.search(
        r"def toggle_auto_save_enabled\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "QSettings" in text and 'setValue("auto_save_enabled"' in text, (
        "toggle no longer persists to QSettings — preference is lost "
        "on restart"
    )


def test_save_session_as_passes_manual_true(menu_src):
    """Save As must call save_session with manual=True so it's never
    suppressed by the auto-save-disabled gate. Without this kwarg
    Save As silently no-ops when auto-save is off — exactly the
    confusion this whole feature is trying to prevent."""
    body = re.search(
        r"def save_session_as\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "manual=True" in text, (
        "save_session_as no longer passes manual=True — Save As "
        "silently no-ops when the auto-save toggle is off"
    )


def test_main_initializes_auto_save_from_qsettings(main_src):
    """main.py __init__ must read the persisted auto-save preference
    so the operator's choice survives a restart. Default is False
    (opt-in)."""
    assert "_auto_save_enabled" in main_src, (
        "main.py no longer initializes _auto_save_enabled"
    )
    # Pin the QSettings read with the expected default (False).
    assert 'QSettings().value(' in main_src and \
           '"auto_save_enabled"' in main_src, (
        "main.py no longer reads auto_save_enabled from QSettings"
    )


def test_main_adds_auto_save_menu_action(main_src):
    """File menu must offer a checkable Auto-save Session action
    wired to the toggle handler."""
    assert "Auto-save Session" in main_src, (
        "Auto-save Session menu entry missing"
    )
    assert "setCheckable(True)" in main_src, (
        "Auto-save action no longer checkable — operator can't "
        "tell or change the state"
    )
    assert "toggle_auto_save_enabled" in main_src, (
        "Auto-save action not wired to the toggle handler"
    )


def test_main_ctrl_s_passes_manual_true(main_src):
    """The Save Session menu action (Ctrl+S) must pass manual=True
    when it calls save_session, otherwise pressing Ctrl+S silently
    no-ops when auto-save is off."""
    # Look for the lambda or wrapper around save_session_action's
    # triggered.connect.
    body = re.search(
        r"save_session_action\.triggered\.connect\([^)]*\)",
        main_src,
    )
    assert body is not None, "save_session_action.triggered.connect missing"
    assert "manual=True" in body.group(0), (
        "Save Session (Ctrl+S) doesn't pass manual=True — pressing "
        "Ctrl+S silently no-ops when auto-save is off"
    )


def test_main_initializes_unsaved_edits_flag(main_src):
    """main.py __init__ must initialize `_has_unsaved_edits = False`
    so the closeEvent guard and title asterisk have a defined state
    from boot."""
    assert "self._has_unsaved_edits = False" in main_src, (
        "main.py no longer initializes _has_unsaved_edits — the "
        "closeEvent prompt and title asterisk will misfire"
    )


def test_close_event_prompts_on_unsaved_edits(main_src):
    """closeEvent must check (_has_unsaved_edits AND
    NOT _auto_save_enabled), prompt with QMessageBox offering Save /
    Discard / Cancel, and honor Cancel via event.ignore()."""
    body = re.search(
        r"def closeEvent\(self,.*?(?=\n    def |\Z)",
        main_src, flags=re.DOTALL,
    )
    assert body is not None, "closeEvent not found"
    text = body.group(0)
    # The gate condition
    assert "_has_unsaved_edits" in text
    assert "_auto_save_enabled" in text
    # All three buttons offered
    for btn in ("QMessageBox.Save", "QMessageBox.Discard",
                "QMessageBox.Cancel"):
        assert btn in text, (
            f"closeEvent prompt no longer offers {btn} — operator "
            f"loses an exit choice"
        )
    # Cancel must call event.ignore() so the app keeps running.
    assert "event.ignore()" in text, (
        "closeEvent no longer respects Cancel — app exits even when "
        "operator changes their mind"
    )
    # Save must call save_session(blocking=True, manual=True) so it
    # bypasses the auto-save gate.
    assert "manual=True" in text, (
        "closeEvent's Save branch no longer passes manual=True — "
        "auto-save-disabled gate would suppress the save and lose "
        "the operator's work"
    )


def test_save_session_gate_marks_dirty(menu_src):
    """The auto-save-disabled gate must set `_has_unsaved_edits=True`
    BEFORE returning early. Without this, edits with auto-save OFF
    silently accumulate with no visible signal (no title asterisk,
    no closeEvent prompt)."""
    body = re.search(
        r"def save_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # Pin both the assignment and the gate context (so a refactor
    # that moves the assignment OUTSIDE the gate fires this test).
    gate_block = re.search(
        r"if not manual and not getattr\(self,\s*\"_auto_save_enabled\".*?(?=\n            current_time|\Z)",
        text, flags=re.DOTALL,
    )
    assert gate_block is not None, "auto-save gate block not found"
    assert "self._has_unsaved_edits = True" in gate_block.group(0), (
        "auto-save gate no longer sets _has_unsaved_edits — close "
        "prompt and title asterisk won't fire on suppressed edits"
    )


def test_save_success_clears_dirty_flag(menu_src):
    """Both save paths (async _on_save_finished and blocking inline)
    must clear `_has_unsaved_edits = False` on success — otherwise
    the title asterisk and close prompt persist after a successful
    save."""
    # Async path
    async_body = re.search(
        r"def _on_save_finished\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert async_body is not None
    assert "self._has_unsaved_edits = False" in async_body.group(0), (
        "_on_save_finished no longer clears _has_unsaved_edits — "
        "title asterisk lingers after every successful auto-save"
    )
    # Blocking path inside save_session — the inline-success branch.
    save_body = re.search(
        r"def save_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert save_body is not None
    assert save_body.group(0).count("self._has_unsaved_edits = False") >= 1, (
        "blocking save success branch no longer clears "
        "_has_unsaved_edits — Ctrl+S succeeds but the title "
        "asterisk and close prompt continue to fire"
    )


def test_title_includes_unsaved_edits_in_dirty_check(menu_src):
    """`_update_window_title` must factor `_has_unsaved_edits` into
    the dirty marker, alongside the existing in-flight/pending
    save signals."""
    body = re.search(
        r"def _update_window_title\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    assert "_has_unsaved_edits" in text, (
        "title-bar dirty check no longer reads _has_unsaved_edits "
        "— operator gets no visible cue that edits are pending"
    )


def test_unsaved_edits_flag_live_behavior(qapp):
    """End-to-end:
      * auto-OFF + edit → _has_unsaved_edits=True
      * manual save → _has_unsaved_edits=False
      * auto-ON + edit + successful save → _has_unsaved_edits=False
    """
    from PyQt5.QtWidgets import QMainWindow
    from traffic_client.menu_actions import TrafficGenClientMenuAction

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h.streams = {}
    h.all_devices = {}
    h.devices_tab = None
    h.server_interfaces = []
    h._current_session_path = "/tmp/x.json"
    h._auto_save_enabled = False
    h._has_unsaved_edits = False
    h._save_session_impl = lambda *a, **k: (True, "ok")

    # Edit-suppressed save marks dirty
    h.save_session()
    assert h._has_unsaved_edits is True

    # Manual save clears it
    h.save_session(manual=True, blocking=True)
    assert h._has_unsaved_edits is False

    # Auto-save also clears it
    h._auto_save_enabled = True
    h.save_session()  # marks dirty briefly (but goes through to save)
    h.save_session(blocking=True)  # forces sync completion
    assert h._has_unsaved_edits is False


def test_auto_save_gate_live_behavior(qapp):
    """End-to-end: with `_auto_save_enabled=False` and `manual=False`,
    save_session must NOT invoke _save_session_impl. With either
    `_auto_save_enabled=True` or `manual=True`, it MUST invoke it."""
    from PyQt5.QtWidgets import QMainWindow
    from traffic_client.menu_actions import TrafficGenClientMenuAction

    class _Host(TrafficGenClientMenuAction, QMainWindow):
        pass

    h = _Host()
    h.streams = {}
    h.all_devices = {}
    h.devices_tab = None
    h.server_interfaces = []

    call_log = []
    h._save_session_impl = lambda *a, **k: (
        call_log.append("impl") or (True, "fake")
    )

    # Case 1: gate off, no manual → suppressed
    h._auto_save_enabled = False
    h.save_session()
    assert call_log == [], (
        f"auto-save fired despite disabled: {call_log}"
    )

    # Case 2: gate off, manual=True → fires
    h.save_session(manual=True, blocking=True)
    assert call_log == ["impl"], (
        f"manual save did not fire: {call_log}"
    )
    call_log.clear()

    # Case 3: gate on, no manual → fires
    h._auto_save_enabled = True
    h.save_session(blocking=True)
    assert call_log == ["impl"], (
        f"auto-save did not fire when enabled: {call_log}"
    )
    call_log.clear()

    # Case 4: gate flipped back off, no manual → suppressed again
    h._auto_save_enabled = False
    h.save_session()
    assert call_log == [], (
        f"auto-save fired after toggle-off: {call_log}"
    )


def test_devices_tab_lacks_dhcp_vxlan_refresh_wrappers():
    """Sanity check: confirm devices_tab.py really does NOT have
    wrapper methods for refresh_dhcp_status / refresh_vxlan_table.
    If a future commit adds them, the routing-via-handler logic in
    load_session is harmless but no longer strictly necessary — and
    this test will fire to remind us to simplify the call sites."""
    src = (REPO / "widgets" / "devices_tab.py").read_text()
    # Look for top-level methods (4-space indent, `def name(self...`).
    assert not re.search(
        r"^    def refresh_dhcp_status\(self", src, flags=re.MULTILINE,
    ), (
        "devices_tab.py gained a refresh_dhcp_status wrapper — the "
        "load_session routing table can be simplified, and this "
        "guard test should be deleted"
    )
    assert not re.search(
        r"^    def refresh_vxlan_table\(self", src, flags=re.MULTILINE,
    ), (
        "devices_tab.py gained a refresh_vxlan_table wrapper — same "
        "simplification + delete-this-test note"
    )


def test_load_session_explicit_path_clears_state(menu_src):
    """When `session_file_path` is supplied (Load From… / Recent
    Sessions / programmatic open), load_session must wipe the
    server / removed-set / save-hash state so the new snapshot
    REPLACES the old one instead of merging onto it."""
    body = re.search(
        r"def load_session\(self.*?(?=\n    def |\Z)",
        menu_src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # All four state-resets must appear inside the load_session body.
    # Pin the literal assignments so a refactor that drops one falls
    # over this test.
    for reset in (
        "self.server_interfaces = []",
        "self.removed_servers = set()",
        "self.removed_interfaces = set()",
        "self._last_session_hash = None",
    ):
        assert reset in text, (
            f"load_session no longer resets {reset!r} on explicit-path "
            f"load — Load From… will leak previous-session state"
        )
