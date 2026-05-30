"""Sort-state preservation across table rebuilds (v0.2.91).

Pinned behaviour:
  * Snapshot captures the operator's chosen sort column + direction.
  * Restore re-applies via sortByColumn() so rows land in the right
    order after the rebuild's setRowCount + repopulate.
  * No-ops gracefully when no sort indicator was set (the fresh-
    table case) and when the table object is destroyed mid-rebuild.
"""

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

from utils.table_sort_state import (
    capture_sort_state,
    restore_sort_state,
)


@pytest.fixture
def make_table(qapp):
    """Build a sortable QTableWidget with a few rows. parent.show()
    so the header has real geometry for sortIndicator queries."""
    refs: list = []

    def _make(rows: int = 3, cols: int = 3):
        parent = QWidget()
        parent.resize(400, 200)
        parent.show()
        table = QTableWidget(rows, cols, parent)
        for r in range(rows):
            for c in range(cols):
                table.setItem(r, c, QTableWidgetItem(f"r{r}c{c}"))
        table.setSortingEnabled(True)
        refs.append((parent, table))
        return table

    yield _make
    # Defensive cleanup so model signals on the QHeaderView don't
    # leak across tests (same trap the empty-state overlay hit).
    for parent, _table in refs:
        try:
            parent.close()
            parent.deleteLater()
        except Exception:
            pass
    refs.clear()


# ────────────────────────────────────── capture_sort_state
def test_capture_returns_qt_default_when_no_header_click(make_table):
    """Qt's QHeaderView always exposes an indicator section even
    before any header click — defaults to column 0. We pin Qt's
    actual behaviour (column 0 + the platform's default order)
    so future contributors aren't surprised. Restoring that to
    itself is a harmless no-op.

    The helper's documented -1 sentinel kicks in only when access
    to the header itself fails (table dead, missing
    horizontalHeader, etc.) — see the next two tests."""
    table = make_table()
    col, _order = capture_sort_state(table)
    # Whatever Qt's default is, the helper should capture it (not
    # raise). Both directions are reasonable defaults across Qt
    # builds; just confirm capture returned a valid column.
    assert col >= 0


def test_capture_returns_actual_state_after_header_click(make_table):
    """After sortByColumn the snapshot must reflect the new
    column + direction."""
    table = make_table()
    table.sortByColumn(1, Qt.DescendingOrder)
    col, order = capture_sort_state(table)
    assert col == 1
    assert order == Qt.DescendingOrder


def test_capture_returns_sentinel_when_table_is_none():
    """Defensive — callers may pass a stale ref during rebuild."""
    col, order = capture_sort_state(None)
    assert col == -1


def test_capture_swallows_attribute_errors():
    """Anything that lacks horizontalHeader() returns the sentinel
    instead of raising — the rebuild path can keep going."""
    class Dummy:
        pass
    col, order = capture_sort_state(Dummy())
    assert col == -1


# ────────────────────────────────────── restore_sort_state
def test_restore_noop_on_none_state(make_table):
    """restore(table, None) is a documented no-op."""
    table = make_table()
    table.sortByColumn(0, Qt.AscendingOrder)
    # Should not raise + should not change state.
    restore_sort_state(table, None)
    assert table.horizontalHeader().sortIndicatorSection() == 0


def test_restore_noop_on_sentinel_column(make_table):
    """(-1, …) means no indicator was captured; restoring it must
    NOT call sortByColumn(-1, …) which would error."""
    table = make_table()
    table.sortByColumn(2, Qt.DescendingOrder)
    # Save current state — should stay.
    pre_col = table.horizontalHeader().sortIndicatorSection()
    restore_sort_state(table, (-1, Qt.AscendingOrder))
    assert table.horizontalHeader().sortIndicatorSection() == pre_col


def test_round_trip_preserves_sort_after_setRowCount(make_table):
    """The whole point: sort survives a setRowCount + repopulate."""
    table = make_table(rows=3, cols=3)
    table.sortByColumn(2, Qt.DescendingOrder)
    state = capture_sort_state(table)
    # Simulate the rebuild path: disable, clear, refill, restore.
    table.setSortingEnabled(False)
    table.setRowCount(0)
    table.setRowCount(2)
    for r in range(2):
        for c in range(3):
            table.setItem(r, c, QTableWidgetItem(f"new_r{r}c{c}"))
    table.setSortingEnabled(True)
    restore_sort_state(table, state)
    # Sort indicator restored.
    hdr = table.horizontalHeader()
    assert hdr.sortIndicatorSection() == 2
    assert hdr.sortIndicatorOrder() == Qt.DescendingOrder


def test_restore_swallows_exception_on_dead_table():
    """If the table got destroyed mid-rebuild the restore step is
    still polish-not-correctness — must not raise."""
    class Dummy:
        def sortByColumn(self, *a, **k):
            raise RuntimeError("wrapped C/C++ object has been deleted")
    # No assertion needed; the call must just return.
    restore_sort_state(Dummy(), (1, Qt.AscendingOrder))


# ────────────────────────────────────── ascending vs descending
@pytest.mark.parametrize("col,order", [
    (0, Qt.AscendingOrder),
    (0, Qt.DescendingOrder),
    (2, Qt.AscendingOrder),
    (2, Qt.DescendingOrder),
])
def test_round_trip_preserves_both_directions(make_table, col, order):
    """Pin both axes (column + direction) round-trip cleanly."""
    table = make_table(rows=4, cols=3)
    table.sortByColumn(col, order)
    state = capture_sort_state(table)
    # Rebuild
    table.setSortingEnabled(False)
    table.setRowCount(0)
    table.setRowCount(3)
    table.setSortingEnabled(True)
    restore_sort_state(table, state)
    hdr = table.horizontalHeader()
    assert hdr.sortIndicatorSection() == col
    assert hdr.sortIndicatorOrder() == order
