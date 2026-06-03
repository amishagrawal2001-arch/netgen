"""Shared 'Filter: <input>' bar for the BGP / OSPF / ISIS / DHCP /
VXLAN sub-tabs (v0.3.11).

The Devices, Streams, L2 Emulation, and Stateful TCP tabs already
ship a filter input above their tables — built one-off in each tab
file. The five sub-tabs under the Devices view did NOT, which broke
the user's reasonable expectation that every browsable table has a
filter where the others do (top-of-table, same border + focus style,
substring match in-place via setRowHidden).

This module exposes ``make_table_filter_row`` so each sub-tab can
adopt the convention without 5x boilerplate. The returned QLineEdit
has its ``apply_filter`` callable attached so the host can re-invoke
it after a periodic table rebuild — without that the rebuild
un-hides every row and the operator sees flickering reappear while
typing.

Design notes:
  * Match-time column resolution by header text — survives column
    reorders without an invalidation hook.
  * cellWidget fallback (combo / label) so rows whose filter-target
    column hosts a widget (not a plain QTableWidgetItem) still
    match — same behaviour as the Streams-tab filter.
  * Case-insensitive substring match. Empty needle = show all.
  * No persistence — needle lives only for the session, intentional
    so a fresh client launch doesn't surprise the operator with a
    hidden subset of rows they don't remember filtering.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QTableWidget, QWidget,
)


def make_table_filter_row(
    *,
    table: QTableWidget,
    columns: Iterable[str],
    placeholder: str,
    tooltip: str = "",
) -> Tuple[QHBoxLayout, QLineEdit]:
    """Build a 'Filter: <input>' QHBoxLayout to sit ABOVE ``table``.

    :param table: the QTableWidget whose rows the filter should hide.
    :param columns: header texts of the columns to substring-match
        against. Resolved at apply time so column reorders survive.
    :param placeholder: greyed-out hint text inside the input.
    :param tooltip: optional hover tooltip on the input.
    :returns: (row_layout, line_edit). Add ``row_layout`` to the
        parent QVBoxLayout before adding the table. The host should
        keep the returned QLineEdit reachable (e.g. as
        ``self._<name>_filter_input``) so it can re-apply the filter
        after a rebuild via ``line_edit.apply_filter()``.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)

    lbl = QLabel("Filter:")
    lbl.setStyleSheet("color: #6b7280; font-size: 11px;")

    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.setFixedHeight(22)
    edit.setMaximumWidth(320)
    edit.setStyleSheet(
        "QLineEdit { border: 1px solid #cbd5e1; border-radius: 4px;"
        "  padding: 0 6px; font-size: 12px; background: #ffffff; }"
        "QLineEdit:focus { border-color: #2563eb; }"
    )
    if tooltip:
        edit.setToolTip(tooltip)

    wanted = set(columns)

    def _apply(*_args) -> None:
        # Defensive — host may tear down the table before the edit
        # widget. setRowHidden on a deleted C++ object raises; bail.
        try:
            n_rows = table.rowCount()
        except RuntimeError:
            return
        needle = (edit.text() or "").strip().lower()

        # Resolve column indices at apply time so a header reorder
        # (rare but possible if a future commit shuffles labels) doesn't
        # silently filter the wrong columns.
        cols = []
        for c in range(table.columnCount()):
            hi = table.horizontalHeaderItem(c)
            if hi is not None and hi.text() in wanted:
                cols.append(c)

        for r in range(n_rows):
            if not needle:
                table.setRowHidden(r, False)
                continue
            match = False
            for c in cols:
                item = table.item(r, c)
                if item is None:
                    # Cell may host a widget (e.g. inline combo for an
                    # Enabled / Status column) — fall back to its
                    # accessible text.
                    w = table.cellWidget(r, c)
                    if w is not None:
                        txt = (
                            w.currentText().lower()
                            if hasattr(w, "currentText")
                            else (w.text().lower()
                                  if hasattr(w, "text") else "")
                        )
                        if txt and needle in txt:
                            match = True
                            break
                    continue
                if needle in (item.text() or "").lower():
                    match = True
                    break
            table.setRowHidden(r, not match)

    edit.textChanged.connect(_apply)
    # Stash the apply callable on the widget so the host can re-invoke
    # it after a periodic rebuild without holding a separate reference.
    edit.apply_filter = _apply  # type: ignore[attr-defined]

    row.addWidget(lbl)
    row.addWidget(edit)
    row.addStretch(1)
    return row, edit


def reapply_filter(line_edit: QLineEdit) -> None:
    """Defensively re-invoke the filter on a host-owned filter input.

    Use from a sub-tab's table-rebuild path so an active filter
    survives the rebuild (without this, every row un-hides when the
    rebuild runs and the operator sees a flicker while typing).

    Safe to call on a None/missing widget — silently no-ops. The
    `apply_filter` attribute is attached by ``make_table_filter_row``;
    inputs created elsewhere fall through harmlessly.
    """
    if line_edit is None:
        return
    fn = getattr(line_edit, "apply_filter", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:
        # Filter is purely advisory chrome; never let a stale C++
        # pointer or other Qt teardown surface to the caller.
        pass
