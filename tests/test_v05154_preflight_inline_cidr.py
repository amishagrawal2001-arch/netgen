"""v0.5.154: Pre-flight Test CIDR + Notes inline in the probe table.

Operator: "allow user to modify ips in the Endpoint probe table
itself insted of seprate temp ip config section."

v0.5.150-v0.5.153 had two parallel views of the same endpoint:
the read-only "Endpoint probes" table on top and the editable
"Temporary IP configuration" grid below. v0.5.154 folds them into
one — Test CIDR and Notes become columns 7 and 8 of the probe
table, with QLineEdit / QLabel cell widgets. Single source of
truth per row; less scrolling; cleaner mental model.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "widgets" / "rdma_preflight_dialog.py").read_text()


# ───── Probe table grew Test CIDR + Notes columns ───────────────────────


def test_probe_table_column_count_bumped_to_9():
    """Was 7 (Endpoint, HCA, Iface, Port state, Link, IPs,
    RoCEv2 GIDs); now 9 with Test CIDR + Notes appended."""
    assert "self._probe_table.setColumnCount(9)" in SRC


def test_probe_table_headers_include_test_cidr_and_notes():
    """Column 7 (Test CIDR) and 8 (Notes) — the operator-editable
    columns. Existing IPs is its own column at index 5 so the
    operator sees old AND proposed side by side."""
    assert "Existing IPs" in SRC
    # New columns at the right edge.
    body = _extract_method(SRC, "_build_ui")
    headers_match = re.search(
        r"setHorizontalHeaderLabels\(\[(.*?)\]\)",
        body, flags=re.DOTALL,
    )
    assert headers_match is not None
    headers = headers_match.group(1)
    # "Test CIDR" and "Notes" both present.
    assert "Test CIDR" in headers
    assert "Notes" in headers
    # And appear AFTER "RoCEv2 GIDs" (column 6).
    g_pos = headers.index("RoCEv2 GIDs")
    t_pos = headers.index("Test CIDR")
    n_pos = headers.index("Notes")
    assert g_pos < t_pos < n_pos


# ───── Separate cfg_box is gone ─────────────────────────────────────────


def test_temporary_ip_config_groupbox_dropped():
    """The standalone 'Temporary IP configuration' GroupBox is
    gone — its only role (host the cidr/notes grid) is now the
    probe table itself."""
    assert "Temporary IP configuration (runtime only" not in SRC
    assert "cfg_box = QGroupBox(" not in SRC


def test_config_grid_dropped():
    """The QGridLayout `_config_grid` that held the old form is
    gone. Replaced by cell widgets in the probe table."""
    assert "self._config_grid" not in SRC


def test_btn_row_added_to_root_not_cfg():
    """Validate / Apply / Cleanup buttons now sit at the dialog
    root level (under the verdict), not inside the dropped
    cfg_box."""
    body = _extract_method(SRC, "_build_ui")
    # The new layout uses `root.addLayout(btn_row)` directly.
    assert "root.addLayout(btn_row)" in body


# ───── Cell widgets installed in the probe table ────────────────────────


def test_cidr_cell_uses_setcellwidget():
    """Each iface gets a QLineEdit at column 7 via setCellWidget
    (instead of being placed in a separate grid)."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert "self._probe_table.setCellWidget(row_idx, 7, cidr_edit)" in body


def test_note_cell_uses_setcellwidget():
    """Notes label at column 8 of the probe table."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert "self._probe_table.setCellWidget(row_idx, 8, note_lbl)" in body


def test_config_rows_keep_iface_and_row_idx():
    """Tracking refs stay so collect/clear/issues keep working
    across the layout change. row_idx replaces the old
    iface_label widget ref."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert '"row_idx": row_idx' in body
    assert '"cidr_edit": cidr_edit' in body
    assert '"note": note_lbl' in body


# ───── Auto-suggest behavior preserved from v0.5.153 ────────────────────


def test_inline_still_skips_existing_v4_with_note():
    """v0.5.153's "already has IPv4 (X); leave empty to skip"
    note must still appear — just now inside a cell widget
    instead of a grid label."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert "already has IPv4" in body
    assert "leave empty to skip" in body


def test_inline_avoids_subnet_collisions():
    """v0.5.153's collision avoidance via `proposed_nets` must
    survive the layout change."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert "proposed_nets" in body
    assert "next_octet" in body


def test_status_shows_all_clear_when_nothing_needed():
    """When all ifaces already have IPs, status banner explains
    'nothing to apply' so the empty cell widgets don't read as a
    bug."""
    body = _extract_method(SRC, "_populate_config_rows")
    assert "needs_fix" in body
    assert "non-conflicting subnets" in body


# ───── Validate / Apply paths still work with the new widget locations ──


def test_collect_entries_reads_from_inline_cidr_edit():
    """`_collect_entries` walks `_config_rows` and reads the
    QLineEdit's text — works regardless of whether the widget
    is in a grid or a table cell."""
    body = _extract_method(SRC, "_collect_entries")
    assert 'r["cidr_edit"].text().strip()' in body


def test_clear_notes_restores_default_border():
    """v0.5.154: when the QLineEdit lives in a table cell, the
    "no error" baseline style needs to be restored explicitly
    (otherwise the last red/amber border persists across
    re-validates)."""
    body = _extract_method(SRC, "_clear_notes")
    assert 'r["cidr_edit"].setStyleSheet(' in body
    assert "border: 1px solid #cbd5e1" in body or "cbd5e1" in body


def test_apply_issues_uses_qss_block_syntax():
    """Cell-widget QLineEdits need the `QLineEdit { … }` selector
    in setStyleSheet (a bare `border: …;` doesn't always apply
    when the widget is hosted in a QTableWidget cell). Operator
    must see the red/amber border on validation failure."""
    body = _extract_method(SRC, "_apply_issues_to_rows")
    assert "QLineEdit" in body
    assert "border" in body


# ───── helpers ──────────────────────────────────────────────────────────


def _extract_method(src: str, name: str) -> str:
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"def {name}(...) not found"
    return m.group(0)
