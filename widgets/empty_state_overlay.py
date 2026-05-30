"""EmptyStateOverlay — centred placeholder label for empty tables (v0.2.88).

The 5 protocol sub-tabs on the Devices tab (BGP / OSPF / IS-IS /
VXLAN / DHCP) all show their list as a QTableWidget. Until v0.2.87
a fresh session — or a server with no devices yet — rendered them
as a blank rectangle with no hint of whether that's "nothing
configured yet", "still loading", or "broken connection". Operators
who hadn't built a config before were left wondering.

This widget overlays the table with a centered, dimmed label
whenever ``rowCount() == 0``. The label is a child of the table's
viewport so it scrolls/resizes with the table; we re-center on
viewport resize via a Qt event filter. Show/hide is driven by the
table's ``model().rowsInserted`` / ``rowsRemoved`` signals so the
overlay disappears as soon as the first row appears (and reappears
on the last removal).

Pure UI widget — no Qt-quirky inheritance from QTableWidget, so
existing tables can be retrofitted with one line of construction
plus one line per `setRowCount` site.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtWidgets import QLabel, QTableWidget


class EmptyStateOverlay(QObject):
    """Show a centred placeholder over ``table`` when it has 0 rows.

    The overlay disappears when the first row is inserted and
    reappears when the table empties again. It auto-recenters on
    viewport resize via an event filter.

    Usage:
        overlay = EmptyStateOverlay(
            self.bgp_table,
            "No BGP neighbours configured.\\nClick Add (toolbar) "
            "or right-click a device → Edit → enable BGP.",
        )
        # Optionally call refresh() explicitly after a programmatic
        # setRowCount when the model-changed signals don't fire.
    """

    def __init__(self, table: QTableWidget, message: str,
                 parent: Optional[QObject] = None):
        super().__init__(parent or table)
        self._table = table
        self._label = QLabel(message, table.viewport())
        # Stylesheet matches the rest of the app's muted-text
        # convention; align center; allow word-wrap for long
        # multi-line hints.
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "QLabel { color: #94a3b8; font-size: 12px; "
            "padding: 16px; background: transparent; }"
        )
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        # Keep the label centred on viewport resize.
        table.viewport().installEventFilter(self)
        # Show/hide when rows arrive/leave.
        model = table.model()
        if model is not None:
            model.rowsInserted.connect(self._refresh)
            model.rowsRemoved.connect(self._refresh)
            model.modelReset.connect(self._refresh)
            model.layoutChanged.connect(self._refresh)
        # Initial paint.
        self._refresh()

    # ──────────────────────────────────────────────── event filter
    def eventFilter(self, obj, event) -> bool:
        # Recentre on viewport resize so the label stays put through
        # tab switches + window resizes.
        if event.type() == QEvent.Resize and obj is self._table.viewport():
            self._reposition()
        return False  # don't consume the event

    # ───────────────────────────────────────────────── public
    def refresh(self) -> None:
        """Re-evaluate visibility — useful after a programmatic
        ``setRowCount`` if the model signals didn't fire."""
        self._refresh()

    def set_message(self, message: str) -> None:
        """Swap the placeholder text in place (e.g. to reflect a
        server-connection error vs an empty-but-OK state)."""
        self._label.setText(message)
        self._reposition()

    # ─────────────────────────────────────────────── internals
    def _refresh(self, *_args) -> None:
        # Belt-and-braces: model signals can fire AFTER the table is
        # destroyed (Qt teardown order is parent-after-children for
        # destroy(); but signals queued before that can still arrive).
        # Treat any C++-side death as "nothing to do".
        try:
            is_empty = self._table.rowCount() == 0
        except Exception:
            return
        try:
            self._label.setVisible(is_empty)
            if is_empty:
                self._reposition()
        except RuntimeError:
            # QLabel was deleted (Qt C++ side) before the Python wrapper
            # was GC'd. The signal connection holding us alive will go
            # next; nothing to do.
            return

    def _reposition(self) -> None:
        vp = self._table.viewport()
        # Match the viewport's full size minus a small margin so the
        # label has room to wrap.
        margin = 24
        w = max(0, vp.width() - 2 * margin)
        h = vp.height()
        self._label.setGeometry(margin, 0, w, h)
        self._label.raise_()
