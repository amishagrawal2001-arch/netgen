"""Preserve QTableWidget sort state across rebuilds (v0.2.91).

Both the Devices table and the BGP-neighbours table follow the
"toggle sort off → setRowCount → repopulate → toggle sort on" pattern
to avoid Qt re-sorting rows mid-populate (each `setItem` would
trigger a re-sort if sorting were active, producing duplicate / wrong
data in cells).

The pattern is correct for the row-data shuffle, but it loses the
operator's chosen sort column + direction. Click "Sort by State",
hit Apply, the rebuild blows away the sort and the rows snap back
to insertion order. Annoying enough that operators don't bother
sorting at all.

This helper captures the header's sort indicator before the rebuild
and re-applies it after, so the rows end up in the operator's
chosen order with no extra UI work at the call sites.

Pure-function — takes the table, returns / consumes a small tuple.
No Qt-quirky inheritance; existing rebuild blocks get one line of
``state = capture_sort_state(table)`` at the top and
``restore_sort_state(table, state)`` at the bottom.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt5.QtCore import Qt


# A captured sort snapshot is (column_index_or_-1, Qt.SortOrder).
# -1 means "no sort indicator was set" → don't try to re-apply.
SortState = Tuple[int, Qt.SortOrder]


def capture_sort_state(table) -> SortState:
    """Snapshot ``table``'s current sort column + direction.

    Returns ``(-1, Qt.AscendingOrder)`` when no sort indicator is
    set (the typical fresh-table case). Defensive against C++-side
    teardown / missing horizontalHeader: returns the sentinel on
    any access failure.
    """
    try:
        header = table.horizontalHeader()
        col = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        # Qt returns -1 when no indicator has been clicked yet.
        if col is None:
            return (-1, Qt.AscendingOrder)
        return (int(col), order)
    except Exception:
        return (-1, Qt.AscendingOrder)


def restore_sort_state(table, state: Optional[SortState]) -> None:
    """Re-apply a previously-captured ``state``.

    No-op when ``state`` is None or carries column -1 (operator
    never set a sort indicator). Otherwise calls
    ``sortByColumn(col, order)`` which both updates the indicator
    and re-sorts the model with the freshly-populated rows in the
    desired order.

    Defensive: any access failure (table destroyed mid-rebuild,
    etc.) is swallowed — sort restoration is a polish step, not a
    correctness step.
    """
    if state is None:
        return
    col, order = state
    if col is None or col < 0:
        return
    try:
        table.sortByColumn(int(col), order)
    except Exception:
        return
