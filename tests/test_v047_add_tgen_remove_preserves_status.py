"""Regression test for v0.4.7: Remove-from-history in the AddTGenDialog
must NOT wipe the connection status (LED / version / health) of the
other chassis still in the table.

Operator-reported on the AddTGenDialog: had 3 chassis in the history
table, all probed (✓ green LEDs, version + health populated), then
clicked Remove on one of them — the other two LEDs reverted to "?"
gray, version went back to "?", health went back to "—". Looked like
the other chassis lost their connection.

Root cause: ``_remove_selected_from_history`` called
``_populate_history_table()`` after deleting the entry. That helper
``setRowCount(0)``s the whole table and rebuilds every row's items
from scratch — the rebuilt rows start with placeholder
``QTableWidgetItem("?")`` because the per-row LED / version / health
state lives only in the QTableWidget items (the probe slots set
``led.setText("✓")`` / ``ver_item.setText("0.4.5")`` on the existing
items after the table was built). Nothing in ``self._entries`` carries
the runtime probe verdict, so a repopulate has no way to restore it.

Fix: surgical ``self.table.removeRow(i)``. Qt shifts subsequent rows
up by one and leaves their items untouched. ``self._entries`` is
already mutated and persisted before the table operation, so model
and view stay in sync.
"""
from __future__ import annotations

import re
from pathlib import Path


_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_tgen_dialog.py"


def test_remove_uses_surgical_removeRow_not_full_repopulate():
    """The remove handler must call ``self.table.removeRow(i)`` —
    NOT ``self._populate_history_table()`` (which would reset the
    LED / version / health columns on every remaining row)."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _remove_selected_from_history\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_remove_selected_from_history not found"
    body = m.group(0)

    assert "self.table.removeRow(" in body, (
        "_remove_selected_from_history doesn't call self.table.removeRow(). "
        "Without it, the remove path falls back to a full table rebuild "
        "(or to nothing), wiping connection status on every other row."
    )
    # Match the CALL site (`self._populate_history_table()`), not
    # any comment text that explains why we're avoiding it.
    assert "self._populate_history_table()" not in body, (
        "_remove_selected_from_history still calls "
        "self._populate_history_table() — that wipes LED / version / "
        "health on every remaining row. Operator-reported bug v0.4.7: "
        "'other chassis lost the connection status'."
    )


def test_remove_still_deletes_underlying_entry_and_saves():
    """Surgical removeRow must NOT skip mutating the entries list or
    saving to disk. If the model isn't updated, the row would
    reappear after closing and reopening the dialog."""
    src = _DIALOG.read_text()
    m = re.search(
        r"def _remove_selected_from_history\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m
    body = m.group(0)
    assert "del self._entries[i]" in body, (
        "_remove_selected_from_history doesn't delete from "
        "self._entries — the table view would diverge from the model."
    )
    assert "_save_history(self._entries)" in body, (
        "_remove_selected_from_history doesn't persist via "
        "_save_history — the removed entry would reappear on next "
        "dialog open."
    )


def test_other_rows_keep_their_status_items_after_remove(qapp, monkeypatch, tmp_path):
    """End-to-end: build a real AddTGenDialog with 3 entries, manually
    set ✓ / version / health items on rows 0+2, remove row 1, then
    verify rows 0 (now still row 0) and 2 (now row 1) keep their
    LED text, version text, and health text. This catches a refactor
    that swapped removeRow back to a full rebuild."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QTableWidgetItem, QMessageBox
    from widgets import add_tgen_dialog as mod

    # Sandbox the history file so the test doesn't pollute or
    # read the operator's real ~/.netgen/chassis_history.json. The
    # module exposes the path as a module-level constant.
    fake_history = tmp_path / "chassis_history.json"
    monkeypatch.setattr(mod, "HISTORY_FILE", str(fake_history))
    monkeypatch.setattr(mod, "HISTORY_DIR", str(tmp_path))
    # Auto-confirm the Remove? message box so the test doesn't hang
    # on a modal.
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.Yes,
    )

    dlg = mod.AddTGenDialog()
    try:
        # Seed three entries with probe-style state.
        dlg._entries = [
            {"scheme": "http", "address": "srv01", "port": 5050, "label": "A"},
            {"scheme": "http", "address": "srv02", "port": 5050, "label": "B"},
            {"scheme": "http", "address": "srv03", "port": 5050, "label": "C"},
        ]
        dlg._populate_history_table()
        # Simulate a successful probe — flip rows 0 and 2 to ✓ green,
        # version 0.4.5, health "Healthy". This mirrors what the
        # _on_probe_result slot does on a real probe.
        for row in (0, 2):
            led = dlg.table.item(row, 0)
            led.setText("✓")
            led.setForeground(QColor("#15803d"))
            ver = dlg.table.item(row, 4)
            ver.setText("0.4.5")
            health = dlg.table.item(row, 5)
            health.setText("Healthy")
            health.setForeground(QColor("#15803d"))

        # Pre-fix snapshot of what we'll check survives the remove.
        before = {
            "row0_led": dlg.table.item(0, 0).text(),
            "row0_ver": dlg.table.item(0, 4).text(),
            "row0_health": dlg.table.item(0, 5).text(),
            "row2_led": dlg.table.item(2, 0).text(),
            "row2_ver": dlg.table.item(2, 4).text(),
            "row2_health": dlg.table.item(2, 5).text(),
        }
        assert before["row0_led"] == "✓"
        assert before["row2_led"] == "✓"

        # Select row 1 (srv02 / "B") and remove it.
        dlg.table.selectRow(1)
        dlg._remove_selected_from_history()

        # The table now has 2 rows. Row 0 should still be srv01 (✓);
        # row 1 should be the former row 2 (srv03, ✓).
        assert dlg.table.rowCount() == 2, (
            f"removeRow left {dlg.table.rowCount()} rows in the table — "
            f"expected 2 (started with 3, removed 1)."
        )
        assert dlg._entries[0]["address"] == "srv01"
        assert dlg._entries[1]["address"] == "srv03"

        # Crucial assertion: the surviving rows kept their probe state.
        assert dlg.table.item(0, 0).text() == "✓", (
            f"Row 0 (srv01) lost its LED after removing srv02 — got "
            f"{dlg.table.item(0, 0).text()!r}, expected ✓. The remove "
            f"path is wiping connection status on other rows again."
        )
        assert dlg.table.item(0, 4).text() == "0.4.5", (
            "Row 0 lost its version column on remove"
        )
        assert dlg.table.item(0, 5).text() == "Healthy", (
            "Row 0 lost its health column on remove"
        )
        # The row formerly at index 2 is now at index 1 — Qt's
        # removeRow shifts. Its probe state must come with it.
        assert dlg.table.item(1, 0).text() == "✓", (
            f"Row 2's content (srv03) lost its LED after shifting "
            f"up to row 1 — got {dlg.table.item(1, 0).text()!r}. The "
            f"shifted row must inherit the surviving items unchanged."
        )
        assert dlg.table.item(1, 4).text() == "0.4.5"
        assert dlg.table.item(1, 5).text() == "Healthy"
    finally:
        dlg.deleteLater()
