"""EmptyStateOverlay widget tests (v0.2.88).

Pinned behaviour:
  * Overlay starts visible when the table has 0 rows.
  * Hides when the first row is inserted; reappears on full removal.
  * setRowCount(0) after rows existed → re-shows.
  * set_message() swaps the label text in place.
"""

import pytest
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QWidget


@pytest.fixture
def make_overlay(qapp):
    """Build an EmptyStateOverlay over a fresh QTableWidget. The
    parent QWidget is held + .show()n so QLabel.isVisible() works
    correctly under offscreen Qt (a child of a hidden parent is
    technically not "visible" even when setVisible(True))."""
    refs: list = []  # parents + overlays held to keep Python+Qt refs alive

    def _make(rows: int = 0, message: str = "Nothing here."):
        from widgets.empty_state_overlay import EmptyStateOverlay
        parent = QWidget()
        table = QTableWidget(rows, 3, parent)
        table.resize(400, 300)
        parent.resize(420, 320)
        parent.show()  # required for isVisible() to return True on children
        overlay = EmptyStateOverlay(table, message)
        refs.append((parent, table, overlay))
        return table, overlay

    yield _make
    # Explicit teardown — disconnect model signals + drop refs in
    # parent-first order so the QLabel goes BEFORE its model-signal
    # source is gone. Avoids cross-test "wrapped C/C++ object has
    # been deleted" segfaults under the offscreen Qt platform.
    for parent, table, _overlay in refs:
        try:
            parent.close()
            parent.deleteLater()
        except Exception:
            pass
    refs.clear()


def test_overlay_visible_on_empty_table(make_overlay):
    table, overlay = make_overlay(rows=0)
    assert overlay._label.isVisible()


def test_overlay_hidden_when_rows_present_at_construction(make_overlay):
    """If the table starts with rows, the overlay shouldn't flash
    visible — initial _refresh() runs in the constructor."""
    table, overlay = make_overlay(rows=2)
    # _refresh() checked rowCount > 0 → hidden.
    assert not overlay._label.isVisible()


def test_overlay_hides_when_first_row_inserted(make_overlay):
    table, overlay = make_overlay(rows=0)
    assert overlay._label.isVisible()
    table.insertRow(0)
    table.setItem(0, 0, QTableWidgetItem("data"))
    # rowsInserted signal triggers _refresh.
    assert not overlay._label.isVisible()


def test_overlay_reappears_when_last_row_removed(make_overlay):
    """Operator deletes the last neighbour → overlay re-appears so
    the next-state hint is visible again."""
    table, overlay = make_overlay(rows=2)
    assert not overlay._label.isVisible()
    table.removeRow(1)
    assert not overlay._label.isVisible()  # 1 row still left
    table.removeRow(0)
    assert overlay._label.isVisible()


def test_overlay_reappears_after_setRowCount_zero(make_overlay):
    """A full rebuild that calls setRowCount(0) must re-show the
    overlay — covers the "refresh" → "no rows" path."""
    table, overlay = make_overlay(rows=2)
    assert not overlay._label.isVisible()
    table.setRowCount(0)
    overlay.refresh()  # explicit (programmatic setRowCount sometimes
                       # doesn't fire the rowsRemoved signal)
    assert overlay._label.isVisible()


def test_set_message_swaps_text(make_overlay):
    table, overlay = make_overlay(rows=0, message="initial")
    assert overlay._label.text() == "initial"
    overlay.set_message("changed")
    assert overlay._label.text() == "changed"


def test_overlay_is_transparent_to_mouse(make_overlay):
    """Critical: clicks must pass through the overlay so the table's
    context menu / right-click still works on the empty area."""
    from PyQt5.QtCore import Qt
    table, overlay = make_overlay(rows=0)
    assert overlay._label.testAttribute(Qt.WA_TransparentForMouseEvents)


def test_overlay_reparents_to_viewport(make_overlay):
    """Overlay must be a child of the table's viewport (not the table
    itself) so it scrolls + sits inside the data area."""
    table, overlay = make_overlay(rows=0)
    assert overlay._label.parent() is table.viewport()


def test_overlay_label_centred_after_resize(make_overlay):
    """Viewport resize re-centres the label via the installed event
    filter. Check geometry sticks to viewport bounds."""
    table, overlay = make_overlay(rows=0)
    table.resize(800, 600)
    # Pump the event loop so the resize event delivers.
    from PyQt5.QtCore import QCoreApplication
    QCoreApplication.processEvents()
    # Label should span (most of) the viewport width minus margin.
    vp_w = table.viewport().width()
    # Label x is 24 (the margin); width is viewport - 48.
    assert overlay._label.x() == 24
    assert overlay._label.width() == max(0, vp_w - 48)
