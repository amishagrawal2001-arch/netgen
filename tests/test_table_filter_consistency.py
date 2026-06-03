"""v0.3.11 — table-filter consistency across every browsable
data-table tab.

History (why this test file exists):

  * Pre-v0.3.11, the Devices tab had a preflight bar + filter row
    stacked vertically, eating ~65 px of chrome and squeezing the
    table to one visible row.
  * The Streams tab had a top filter (added in the same release) AND
    a leftover "Search..." box at the bottom action bar that filtered
    the same `stream_table` — two inputs, one target, classic
    inconsistency.
  * The Stats dock's `stream_filter_edit` sat in the bottom action
    bar next to Export CSV but filtered a table ABOVE it.
  * The BGP / OSPF / ISIS / DHCP / VXLAN sub-tabs had no filter at
    all — the only tables in the app without one.

v0.3.11 closes all of that with:
  1. `PreflightBar.add_inline_widget()` to fold Devices' filter input
     onto the preflight bar's row instead of stacking another row.
  2. Bottom Streams `search_box` deleted; `_stream_filter_input` is
     the single source of truth for the streams config table.
  3. Stats-dock `stream_filter_edit` moved into the Stream Statistics
     subtab content, above the table it actually filters.
  4. Shared `utils.table_filter_bar.make_table_filter_row` helper used
     by all 5 sub-tabs (BGP / OSPF / ISIS / DHCP / VXLAN) so they all
     get a filter above the table, with the same border + focus style
     + cell-widget fallback + apply-after-rebuild hook.

This file pins the contract so a future refactor can't silently
regress to the inconsistent state. Tests are source-grep style where
practical (cheap, no Qt boot) and live-construction where a behaviour
needs verifying (filter actually hides rows).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO = Path(__file__).resolve().parent.parent


# ────────────────────────────── source-grep contracts

@pytest.fixture(scope="module")
def streams_src():
    return (REPO / "traffic_client" / "stream_control.py").read_text()


@pytest.fixture(scope="module")
def server_section_src():
    return (REPO / "traffic_client" / "server_section.py").read_text()


@pytest.fixture(scope="module")
def stats_src():
    return (REPO / "traffic_client" / "statistics_section.py").read_text()


@pytest.fixture(scope="module")
def devices_src():
    return (REPO / "widgets" / "devices_tab.py").read_text()


@pytest.fixture(scope="module")
def preflight_src():
    return (REPO / "widgets" / "preflight_bar.py").read_text()


# ────────────────────────────── batch #1 — Devices preflight squeeze

def test_preflight_bar_exposes_add_inline_widget(preflight_src):
    """PreflightBar gained the method that lets the Devices tab fold
    its filter onto the preflight bar's row instead of stacking
    another row beneath it. Without this, the Devices tab regresses
    to the 65 px chrome squeeze that left only one row visible."""
    assert "def add_inline_widget(" in preflight_src, (
        "PreflightBar.add_inline_widget missing — the Devices filter "
        "would have nowhere to inline-attach to and would have to "
        "stack a separate row again"
    )


def test_preflight_bar_margins_tightened(preflight_src):
    """Bar margins are 8/2/8/2 not 8/4/8/4 — the 4 px on each axis
    was 8 px of vertical chrome the Devices tab couldn't spare."""
    assert "setContentsMargins(8, 2, 8, 2)" in preflight_src, (
        "preflight bar margins regressed to a thicker value"
    )


def test_devices_filter_inlines_onto_preflight_bar(devices_src):
    """Devices tab must call preflight_bar.add_inline_widget() with
    its filter input rather than addLayout-ing a standalone filter
    row beneath the preflight bar."""
    assert "preflight_bar.add_inline_widget" in devices_src or \
           "bar.add_inline_widget" in devices_src, (
        "Devices tab no longer inlines its filter onto the preflight "
        "bar — vertical squeeze will return"
    )
    # The fallback standalone-row path is intentionally kept as
    # defensive guard (only fires when preflight bar fails to
    # construct); pin its presence so it survives refactors.
    assert "filter_row = QHBoxLayout()" in devices_src, (
        "Devices fallback standalone filter row removed — preflight "
        "bar construction failure now leaves Devices with no filter"
    )


# ────────────────────────────── batch #2 — Streams / Stats filter consolidation

def test_streams_tab_has_top_filter_input(streams_src):
    """The Streams configuration table got its own top filter in
    v0.3.11, matching the convention of every other config table."""
    assert "self._stream_filter_input" in streams_src
    assert "_apply_stream_table_filter" in streams_src


def test_streams_tab_bottom_search_box_removed(streams_src):
    """The old bottom "Search..." `search_box` was a duplicate of the
    new top filter (filtered the same table). Pin its removal so a
    revert can't sneak back in."""
    assert "self.search_box = QLineEdit()" not in streams_src, (
        "bottom Streams search_box reintroduced — duplicate of "
        "_stream_filter_input on the same table"
    )
    # Placeholder call is the simpler tell — pin its absence so the
    # old `setPlaceholderText("Search...")` can't sneak back in. The
    # literal "Search..." string survives in an explanatory comment;
    # don't ban that, just the actual call.
    assert 'setPlaceholderText("Search...")' not in streams_src, (
        'setPlaceholderText("Search...") returned to stream_control.py; '
        "the old bottom search_box is back"
    )


def test_server_section_search_term_is_empty_string(server_section_src):
    """The dead `search_term` filter branch in `_do_update_stream_table`
    was neutered to `search_term = ""` (the top filter handles it via
    setRowHidden now). The branch structure stays so the merge surface
    is small; pin the literal."""
    assert 'search_term = ""' in server_section_src, (
        "_do_update_stream_table no longer reads search_term as empty "
        "— the old search_box-driven filter logic is back"
    )


def test_stream_table_reapplies_filter_after_rebuild(server_section_src):
    """`_do_update_stream_table`'s finally block must call
    `_apply_stream_table_filter` so an active filter survives the
    periodic 0.5 s rebuild — without this the operator sees rows
    flicker reappear while typing."""
    assert "_apply_stream_table_filter" in server_section_src, (
        "stream rebuild path no longer reapplies the top filter — "
        "filter will drop on every refresh tick"
    )


def test_stats_dock_filter_lives_above_its_table(stats_src):
    """`stream_filter_edit` must be added to `stream_stats_layout`
    (subtab content) BEFORE `stream_statistics_table` — same
    convention as every other tab. Previously the filter was in the
    bottom action bar next to Export CSV."""
    # Construction order is the simplest pin: the filter's QLineEdit
    # is built before the line that adds the table to the subtab.
    pos_filter = stats_src.find("self.stream_filter_edit = QLineEdit()")
    pos_table_add = stats_src.find(
        "stream_stats_layout.addWidget(self.stream_statistics_table)"
    )
    assert pos_filter > 0 and pos_table_add > 0
    assert pos_filter < pos_table_add, (
        "stream_filter_edit no longer constructed before the stream "
        "stats table is added to its subtab — filter is back in the "
        "wrong place"
    )


def test_stats_dock_filter_added_to_subtab_layout(stats_src):
    """The v0.3.11 placement uses `stream_stats_layout.addLayout(...)`
    for the filter row. Pin that specific call so a future edit can't
    silently re-add it to `button_layout` instead."""
    assert "stream_stats_layout.addLayout(_stream_filter_row)" in stats_src


# ────────────────────────────── batch #3 — 5 sub-tab filters via shared helper

SUBTAB_SPECS = [
    # (handler_path, handler_class_name, setup_method,
    #  parent_attr_name, parent_subtab_attr)
    ("utils/devices_tab_bgp.py",   "BGPHandler",
     "setup_bgp_subtab",   "_bgp_filter_input",   "bgp_subtab"),
    ("utils/devices_tab_ospf.py",  "OSPFHandler",
     "setup_ospf_subtab",  "_ospf_filter_input",  "ospf_subtab"),
    ("utils/devices_tab_isis.py",  "ISISHandler",
     "setup_isis_subtab",  "_isis_filter_input",  "isis_subtab"),
    ("utils/devices_tab_dhcp.py",  "DHCPHandler",
     "setup_dhcp_subtab",  "_dhcp_filter_input",  "dhcp_subtab"),
    ("utils/devices_tab_vxlan.py", "VXLANHandler",
     "setup_vxlan_subtab", "_vxlan_filter_input", "vxlan_subtab"),
]


@pytest.mark.parametrize(
    "path,_cls,_setup,attr,_subtab", SUBTAB_SPECS,
    ids=[s[1] for s in SUBTAB_SPECS],
)
def test_subtab_imports_shared_filter_helper(
    path, _cls, _setup, attr, _subtab,
):
    """Each sub-tab uses the shared `make_table_filter_row` helper.
    If a sub-tab ever stops importing it, that's the signal the filter
    was deleted or reverted to a one-off implementation."""
    src = (REPO / path).read_text()
    assert "from utils.table_filter_bar import make_table_filter_row" in src, (
        f"{path}: not importing the shared filter helper any more"
    )
    # Reapply must also be imported (in the rebuild path).
    assert "from utils.table_filter_bar import reapply_filter" in src, (
        f"{path}: reapply_filter import missing — filter will drop "
        f"on every table rebuild"
    )


@pytest.mark.parametrize(
    "_path,_cls,_setup,attr,_subtab", SUBTAB_SPECS,
    ids=[s[1] for s in SUBTAB_SPECS],
)
def test_subtab_stores_filter_input_on_parent(
    _path, _cls, _setup, attr, _subtab,
):
    """Each sub-tab must stash its filter QLineEdit as
    `self.parent.<name>_filter_input` so `reapply_filter` can find
    it from the rebuild path. The reapply helper is None-safe but a
    missing attribute means the filter silently drops on rebuild."""
    src = (REPO / _path).read_text()
    assert f"self.parent.{attr}" in src, (
        f"{_path}: parent.{attr} no longer assigned — reapply hook "
        f"can't find the filter input on rebuild"
    )


@pytest.mark.parametrize(
    "_path,cls,setup,attr,subtab_attr", SUBTAB_SPECS,
    ids=[s[1] for s in SUBTAB_SPECS],
)
def test_subtab_setup_creates_filter_input_live(
    qapp, _path, cls, setup, attr, subtab_attr,
):
    """Live construction test — instantiate the handler against a
    stub parent and confirm the filter input lands on the parent
    with a usable `apply_filter` attribute. Catches breakage that
    pure source-grep would miss (e.g. a try/except that silently
    swallows the filter construction)."""
    from PyQt5.QtWidgets import QWidget
    import importlib

    mod_name = _path.replace("/", ".").replace(".py", "")
    handler_cls = getattr(importlib.import_module(mod_name), cls)

    class _Parent(QWidget):
        def __init__(self):
            super().__init__()
            # Every sub-tab needs its own QWidget container plus a
            # handful of handler-method stubs.
            setattr(self, subtab_attr, QWidget())
            self.main_window = None

        def __getattr__(self, name):
            # No-op fallback for handler methods we didn't stub
            # (delete shortcuts, refresh, apply, etc).
            return lambda *a, **k: None

    parent = _Parent()
    handler = handler_cls(parent)
    try:
        getattr(handler, setup)()
    except Exception:
        # Setup may fail at later wiring steps (icons, dialogs) when
        # the parent is a stub — we only care about the filter input
        # which gets created early.
        pass
    assert attr in parent.__dict__, (
        f"{setup}: parent.{attr} not created at all"
    )
    edit = parent.__dict__[attr]
    assert hasattr(edit, "apply_filter"), (
        f"{setup}: {attr} present but apply_filter callable not "
        f"attached — reapply on rebuild will silently no-op"
    )


# ────────────────────────────── shared helper unit tests

def test_helper_hides_non_matching_rows(qapp):
    """Substring match across allowlisted columns, case-insensitive."""
    from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
    from utils.table_filter_bar import make_table_filter_row

    t = QTableWidget(3, 3)
    t.setHorizontalHeaderLabels(["Device", "Port", "State"])
    rows = [
        ("r1", "eth0", "Established"),
        ("r2", "eth1", "Idle"),
        ("r3", "eth0", "Active"),
    ]
    for r, (a, b, c) in enumerate(rows):
        t.setItem(r, 0, QTableWidgetItem(a))
        t.setItem(r, 1, QTableWidgetItem(b))
        t.setItem(r, 2, QTableWidgetItem(c))

    _, edit = make_table_filter_row(
        table=t, columns=("Device", "Port", "State"),
        placeholder="x",
    )

    # Match on Port
    edit.setText("eth0")
    assert [t.isRowHidden(i) for i in range(3)] == [False, True, False]

    # Match on State, mixed case
    edit.setText("IDLE")
    assert [t.isRowHidden(i) for i in range(3)] == [True, False, True]

    # Empty restores all
    edit.setText("")
    assert [t.isRowHidden(i) for i in range(3)] == [False, False, False]


def test_helper_apply_filter_attached_for_reapply(qapp):
    """The QLineEdit must carry `apply_filter` so `reapply_filter`
    can invoke it from a host's rebuild path without holding a
    separate callback reference."""
    from PyQt5.QtWidgets import QTableWidget
    from utils.table_filter_bar import make_table_filter_row, reapply_filter

    t = QTableWidget(0, 1)
    t.setHorizontalHeaderLabels(["x"])
    _, edit = make_table_filter_row(
        table=t, columns=("x",), placeholder="p",
    )
    assert callable(getattr(edit, "apply_filter", None))
    # Defensive None pass is silent
    reapply_filter(None)
    # Real invocation on the stashed callable is silent on empty table
    reapply_filter(edit)


def test_helper_reapply_survives_rebuild(qapp):
    """The whole point of the reapply hook: a rebuild
    (setRowCount(0) + insertRow) un-hides everything; reapplying the
    filter after rebuild restores the hide state."""
    from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
    from utils.table_filter_bar import make_table_filter_row, reapply_filter

    t = QTableWidget(2, 2)
    t.setHorizontalHeaderLabels(["Device", "Port"])
    t.setItem(0, 0, QTableWidgetItem("r1"))
    t.setItem(0, 1, QTableWidgetItem("eth0"))
    t.setItem(1, 0, QTableWidgetItem("r2"))
    t.setItem(1, 1, QTableWidgetItem("eth1"))

    _, edit = make_table_filter_row(
        table=t, columns=("Device", "Port"), placeholder="p",
    )
    edit.setText("eth0")
    assert [t.isRowHidden(i) for i in range(2)] == [False, True]

    # Rebuild — same row data, but fresh insertion path
    t.setRowCount(0)
    for r, (a, b) in enumerate([("r1", "eth0"), ("r2", "eth1")]):
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(a))
        t.setItem(r, 1, QTableWidgetItem(b))
    # Without reapply, the freshly-inserted rows are visible
    assert [t.isRowHidden(i) for i in range(2)] == [False, False]
    # After reapply, the filter takes effect again
    reapply_filter(edit)
    assert [t.isRowHidden(i) for i in range(2)] == [False, True]


def test_helper_cell_widget_fallback(qapp):
    """If a cell holds a widget (e.g. an inline QComboBox) instead of
    a QTableWidgetItem, the filter must still match against the
    widget's accessible text — otherwise rows whose match-column
    happens to host a combo become unfilterable."""
    from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QComboBox
    from utils.table_filter_bar import make_table_filter_row

    t = QTableWidget(2, 2)
    t.setHorizontalHeaderLabels(["Device", "Status"])

    t.setItem(0, 0, QTableWidgetItem("r1"))
    combo0 = QComboBox()
    combo0.addItems(["Enabled", "Disabled"])
    combo0.setCurrentText("Enabled")
    t.setCellWidget(0, 1, combo0)

    t.setItem(1, 0, QTableWidgetItem("r2"))
    combo1 = QComboBox()
    combo1.addItems(["Enabled", "Disabled"])
    combo1.setCurrentText("Disabled")
    t.setCellWidget(1, 1, combo1)

    _, edit = make_table_filter_row(
        table=t, columns=("Device", "Status"), placeholder="p",
    )
    edit.setText("enabled")
    # Row 0 has combo at Enabled, row 1 at Disabled → only row 0 shown
    assert [t.isRowHidden(i) for i in range(2)] == [False, True]
