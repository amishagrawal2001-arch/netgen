"""Shared guard for periodic table rebuilds vs. in-progress inline edits.

Several tables in the GUI (Streams, and the BGP / OSPF / IS-IS protocol
tables) are inline-editable AND periodically rebuilt by monitoring/stats
polls. A rebuild (`setRowCount(0)` + re-`setItem`) issued while the user
has a cell editor open discards the edit and closes the editor.

`table_has_open_editor()` is the single, reliable "is an editor open in
this table right now?" predicate used to defer such rebuilds. It checks
two independent signals because on PyQt5 5.15.11 + Python 3.14 we have
seen `state() == EditingState` come back False even while an editor is
genuinely open:

  1. the view's edit state, and
  2. whether the application's focused widget is a descendant of the
     table — an inline editor (the QLineEdit/QComboBox Qt spawns over the
     cell) lives inside the table's viewport, so it shows up as a child.

Deliberately does NOT consider selection or table focus: selection is
restored across rebuilds by the callers, and guarding on selection breaks
explicit user actions (e.g. delete-then-refresh runs while the row is
still selected). See the stream-table regression fixed in 0.2.51.
"""

from __future__ import annotations


def table_has_open_editor(table) -> bool:
    """True iff `table` currently has an inline cell editor open.

    Safe to call on any QTableWidget/QTableView; returns False on any
    error so a guard built on it fails open (i.e. still rebuilds) rather
    than wedging the table.
    """
    if table is None:
        return False
    try:
        from PyQt5.QtWidgets import QAbstractItemView, QApplication

        try:
            if table.state() == QAbstractItemView.EditingState:
                return True
        except Exception:
            pass

        fw = QApplication.focusWidget()
        if fw is None:
            return False
        # The cell editor Qt spawns is a CHILD of the table's viewport.
        # Mere focus on the table itself or its viewport (e.g. right after
        # clicking a row) must NOT count as "editing" — otherwise the
        # guard would defer refreshes whenever the table is focused, which
        # is exactly the over-broad behaviour that broke stream delete.
        # Only a genuine editor child (not the viewport) counts.
        try:
            viewport = table.viewport()
        except Exception:
            viewport = None
        if fw is table or fw is viewport:
            return False
        if table.isAncestorOf(fw):
            return True
    except Exception:
        return False
    return False
