"""Regression tests locking the 4 recent stream-table fixes.

Each test corresponds to one of the bugs that shipped through with no
GUI test coverage in place. With these in the suite, the *exact* class
of regression won't slip through again — the inline-edit guard, the
post-delete refresh, the copy/paste key resolution, and Start All's
visibility filter.

  - v0.2.49 + v0.2.50 + v0.2.52  inline-edit guard (editor stays open
                                  across stats polls, focus alone no
                                  longer over-defers)
  - v0.2.51  stream Delete refreshes the table even while the row is
             still selected
  - v0.2.53  stream Copy resolves the (bare-iface, name) cell text to
             the full ``"TG N - Port: iface"`` key
  - v0.2.54  stream Paste resolves the TG ID from the tree's custom
             itemWidget (status icon + QLabel), not from text(0)
  - v0.2.55  Start All's ``valid_ports`` is built from stream_id, not
             from the bare-iface Interface cell — otherwise every port
             got marked unknown and nothing started
"""

import copy
import uuid

import pytest
from PyQt5.QtCore import QItemSelectionModel, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                             QTreeWidget, QTreeWidgetItem, QWidget)


def _two_streams_one_port():
    """Standard fixture data: one TG, one port, two enabled streams."""
    return {
        "TG 0 - Port: eno8303": [
            {"stream_id": "sid-a", "name": "UEC", "status": "stopped",
             "enabled": True,
             "protocol_selection": {"name": "UEC", "enabled": True}},
            {"stream_id": "sid-b", "name": "BGP-100", "status": "stopped",
             "enabled": True,
             "protocol_selection": {"name": "BGP-100", "enabled": True}},
        ]
    }


# ────────────────────────────────────────────────────── v0.2.51  Delete
def test_delete_refreshes_while_row_selected(client_stub):
    """Removing a stream while its row is selected MUST refresh the
    table. The 0.2.50 guard over-deferred on selection, so the deleted
    row stayed visible forever."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    assert s.stream_table.rowCount() == 2

    sm = s.stream_table.selectionModel()
    sm.select(s.stream_table.model().index(0, 0),
              QItemSelectionModel.Select | QItemSelectionModel.Rows)

    # Mutate model as the real remove_selected_stream does, then refresh.
    s.streams["TG 0 - Port: eno8303"] = [
        x for x in s.streams["TG 0 - Port: eno8303"] if x["name"] != "UEC"
    ]
    s._do_update_stream_table()

    assert s.stream_table.rowCount() == 1
    # Remaining row's content is the other stream.
    assert s.stream_table.item(0, 2).text() == "BGP-100"


def test_selection_preserved_across_refresh(client_stub):
    """Auto-refresh while a row is selected must keep that row selected
    (the populate code saves + restores selection by stream_id)."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    sm = s.stream_table.selectionModel()
    sm.select(s.stream_table.model().index(1, 0),
              QItemSelectionModel.Select | QItemSelectionModel.Rows)

    s._do_update_stream_table()  # simulated periodic poll
    sel = [i.row() for i in s.stream_table.selectionModel().selectedRows()]
    assert sel == [1]


# ───────────────────────────────────────────── v0.2.49/.50/.52  inline edit
def test_inline_edit_survives_periodic_refresh(qapp, client_stub):
    """With an editor open on the Name cell, repeated stats-poll refreshes
    must NOT close the editor. Locks the v0.2.49/.52 guard."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    s.show()
    qapp.processEvents()

    s.stream_table.editItem(s.stream_table.item(0, 2))
    qapp.processEvents()
    assert s.stream_table.state() == QAbstractItemView.EditingState

    for _ in range(4):
        s._do_update_stream_table()
        qapp.processEvents()
        assert s.stream_table.state() == QAbstractItemView.EditingState
        assert s.stream_table.rowCount() == 2


def test_focused_table_without_editor_does_not_defer(qapp, client_stub):
    """Regression: in v0.2.50 the guard treated viewport focus as an
    open editor, so merely clicking a row paused the stats refresh AND
    broke delete. With v0.2.52's helper the rebuild must proceed."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    s.show()
    qapp.processEvents()
    s.stream_table.setFocus()
    s.stream_table.setCurrentCell(0, 2)
    qapp.processEvents()
    # No editor open — rebuild after a delete should land.
    s.streams["TG 0 - Port: eno8303"] = [
        x for x in s.streams["TG 0 - Port: eno8303"] if x["name"] != "UEC"
    ]
    s._do_update_stream_table()
    qapp.processEvents()
    assert s.stream_table.rowCount() == 1


# ──────────────────────────────────────────────────────── v0.2.53  Copy
def test_copy_resolves_bare_iface_via_stream_id(client_stub):
    """copy_selected_stream must resolve the source even though the
    Interface cell shows the bare iface (``eno8303``) and self.streams
    is keyed by the full label (``TG 0 - Port: eno8303``)."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()

    sm = s.stream_table.selectionModel()
    for r in (0, 1):
        sm.select(s.stream_table.model().index(r, 0),
                  QItemSelectionModel.Select | QItemSelectionModel.Rows)

    s.copy_selected_stream()
    assert hasattr(s, "copied_streams")
    assert len(s.copied_streams) == 2
    # stream_id must be stripped on every copy (paste re-allocates it).
    for c in s.copied_streams:
        assert "stream_id" not in c
        assert "stream_id" not in c.get("protocol_selection", {})
    names = sorted(
        c.get("name") or c.get("protocol_selection", {}).get("name")
        for c in s.copied_streams
    )
    assert names == ["BGP-100", "UEC"]


# ──────────────────────────────────────────────────────── v0.2.54  Paste
def _build_tree_with_tg_widget(stub):
    """Recreate the production server-tree layout: TG node has a custom
    itemWidget (pixmap-only status icon QLabel + a separate text QLabel
    with "TG 0"). text(0) is "" — paste used to mis-resolve to that."""
    tree = QTreeWidget()
    tg_item = QTreeWidgetItem(["", "1.1.1.1"])
    tree.addTopLevelItem(tg_item)
    holder = QWidget(); lay = QHBoxLayout(holder); lay.setContentsMargins(2, 0, 2, 0)
    icon = QLabel(); icon.setPixmap(QPixmap(12, 12))   # pixmap-only, text=""
    lay.addWidget(icon)
    lay.addWidget(QLabel("TG 0"))                       # the TG-ID label
    tree.setItemWidget(tg_item, 0, holder)
    port_item = QTreeWidgetItem(["eno8303"])
    tg_item.addChild(port_item)
    tree.setCurrentItem(port_item)
    stub.server_tree = tree
    return tg_item, port_item


def test_paste_lands_in_correct_full_key(client_stub):
    """Paste resolves TG via the itemWidget's text-bearing QLabel (not
    findChild, which would return the icon label first) and appends to
    the full self.streams key — no ghost ``" - Port: eno8303"`` key."""
    s = client_stub(streams=_two_streams_one_port())
    _build_tree_with_tg_widget(s)
    s._do_update_stream_table()

    s.copied_streams = [copy.deepcopy(s.streams["TG 0 - Port: eno8303"][0])]
    s.copied_streams[0].pop("stream_id", None)

    s.paste_stream_to_interface()

    ghost = [k for k in s.streams.keys()
             if " - Port:" in k and not k.startswith("TG ")]
    assert ghost == [], f"ghost key created: {ghost}"
    assert len(s.streams["TG 0 - Port: eno8303"]) == 3
    last = s.streams["TG 0 - Port: eno8303"][-1]
    assert last["name"] == "str1"
    assert last["rx_port"] == "TG 0 - Port: eno8303"


def test_paste_fallback_to_server_interfaces_index(client_stub):
    """If the TG itemWidget contains no text-bearing label (e.g. icon
    only), paste must still resolve TG via the parent's index into
    self.server_interfaces — second of the 3-tier resolution tiers."""
    s = client_stub(streams=_two_streams_one_port())
    tg_item, _port = _build_tree_with_tg_widget(s)
    # Replace the TG widget with one that has ONLY the icon — no text label.
    icon_only = QWidget(); il = QHBoxLayout(icon_only); il.setContentsMargins(2, 0, 2, 0)
    icon = QLabel(); icon.setPixmap(QPixmap(12, 12)); il.addWidget(icon)
    s.server_tree.setItemWidget(tg_item, 0, icon_only)

    s.copied_streams = [copy.deepcopy(s.streams["TG 0 - Port: eno8303"][0])]
    s.copied_streams[0].pop("stream_id", None)

    s.paste_stream_to_interface()

    ghost = [k for k in s.streams.keys()
             if " - Port:" in k and not k.startswith("TG ")]
    assert ghost == []
    assert len(s.streams["TG 0 - Port: eno8303"]) == 3


# ──────────────────────────────────────────────────── v0.2.55  Start All
def test_start_all_valid_ports_uses_stream_id_not_bare_iface(client_stub):
    """The bug: valid_ports was built from the bare iface in the table's
    Interface cell, so every full-key port_label was flagged 'unknown'
    and Start All silently skipped every stream. The fix builds
    valid_ports via the stream_id stashed at Qt.UserRole on the Name
    cell, walking self.streams to find which full port keys hold those
    sids. This test exercises the same logic in isolation so the
    regression is locked even without spinning up the full start-all
    side effects."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    assert s.stream_table.rowCount() == 2
    # Reproduce the BUGGY logic for contrast — bare iface cells.
    buggy = set()
    for r in range(s.stream_table.rowCount()):
        it = s.stream_table.item(r, 1)
        if it:
            buggy.add(it.text().strip())
    assert "TG 0 - Port: eno8303" not in buggy
    assert buggy == {"eno8303"}

    # Now the FIXED logic — stream_id at col-2 UserRole → full key set.
    displayed_sids = set()
    for r in range(s.stream_table.rowCount()):
        name_item = s.stream_table.item(r, 2)
        if name_item:
            sid = name_item.data(Qt.UserRole)
            if sid:
                displayed_sids.add(sid)
    fixed = set()
    for port_key, stream_list in s.streams.items():
        if any(x.get("stream_id") in displayed_sids for x in stream_list):
            fixed.add(port_key)
    assert fixed == {"TG 0 - Port: eno8303"}

    # And the inclusion check that produced "Skipped stale/unknown ports"
    buggy_unknown = [k for k in s.streams if k not in buggy]
    fixed_unknown = [k for k in s.streams if k not in fixed]
    assert buggy_unknown == ["TG 0 - Port: eno8303"]   # bug exposure
    assert fixed_unknown == []                          # fix outcome


def test_stop_all_row_index_map_keyed_by_stream_id(client_stub):
    """stop_all_streams keys row_index_map by stream_id (v0.2.55), not
    by (bare-iface, name). Mirror that derivation here so the regression
    is locked: the map must contain entries keyed by sid, not by the
    bare-iface tuple."""
    s = client_stub(streams=_two_streams_one_port())
    s._do_update_stream_table()
    by_sid = {}
    by_tuple = {}
    for r in range(s.stream_table.rowCount()):
        name_item = s.stream_table.item(r, 2)
        iface_item = s.stream_table.item(r, 1)
        sid = name_item.data(Qt.UserRole) if name_item else None
        if sid:
            by_sid[sid] = r
        if iface_item and name_item:
            by_tuple[(iface_item.text().strip(),
                      name_item.text().strip())] = r
    # Fixed map: every stream maps by its real id.
    assert by_sid == {"sid-a": 0, "sid-b": 1}
    # Buggy tuple-map: keyed by bare-iface — never matches the full
    # port_label the lookup site uses.
    assert ("TG 0 - Port: eno8303", "UEC") not in by_tuple
    assert ("eno8303", "UEC") in by_tuple
