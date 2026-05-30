"""Tests for ``utils.qt_table_guard.table_has_open_editor`` (v0.2.52, .54).

Locks the invariant that the helper returns True ONLY when a real inline
cell editor is open — never on mere table/viewport focus or selection,
both of which broke stream delete in 0.2.50/.51.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from utils.qt_table_guard import table_has_open_editor


def _make_table_with_focus_neighbour(qapp):
    """Build a 2x2 table next to a button so we can test focus transitions."""
    holder = QWidget()
    lay = QVBoxLayout(holder)
    table = QTableWidget(2, 2)
    table.setEditTriggers(QAbstractItemView.DoubleClicked)
    for r in range(2):
        for c in range(2):
            it = QTableWidgetItem(f"r{r}c{c}")
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            table.setItem(r, c, it)
    btn = QPushButton("elsewhere")
    lay.addWidget(table)
    lay.addWidget(btn)
    holder.show()
    qapp.processEvents()
    return holder, table, btn


def test_none_safe(qapp):
    assert table_has_open_editor(None) is False


def test_unfocused_no_editor_is_false(qapp):
    """Cold table — never focused, no editor — must return False."""
    holder = QWidget(); table = QTableWidget(1, 1)
    QVBoxLayout(holder).addWidget(table)
    assert table_has_open_editor(table) is False


def test_table_focused_without_editor_is_false(qapp):
    """Regression for the v0.2.50 over-defer: when the table (its
    viewport) merely has focus but no editor, the helper used to return
    True — that broke stream delete (the guard saw 'editing' and skipped
    the post-delete refresh). Must be False."""
    holder, table, _btn = _make_table_with_focus_neighbour(qapp)
    table.setFocus()
    qapp.processEvents()
    assert table_has_open_editor(table) is False, \
        "viewport focus must NOT count as editing"


def test_open_editor_is_true(qapp):
    """An actual inline editor (child of the viewport, not the viewport
    itself) must register as open."""
    holder, table, _btn = _make_table_with_focus_neighbour(qapp)
    table.editItem(table.item(0, 0))
    qapp.processEvents()
    assert table_has_open_editor(table) is True


def test_focus_moves_away_returns_false(qapp):
    """After committing the edit and moving focus elsewhere, the helper
    must return False so periodic refreshes resume."""
    holder, table, btn = _make_table_with_focus_neighbour(qapp)
    table.editItem(table.item(0, 0))
    qapp.processEvents()
    # Move focus to the button and close any editor
    btn.setFocus()
    table.setCurrentCell(-1, -1)
    qapp.processEvents()
    assert table_has_open_editor(table) is False
