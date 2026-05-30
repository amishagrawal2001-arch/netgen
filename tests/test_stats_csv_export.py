"""Tests for the Statistics CSV export (v0.2.58).

Two layers:
  * `_dump_table_to_csv` — pure-function-ish staticmethod on the stats
    mixin. Drive it directly with a tiny QTableWidget; assert the
    header, the row data, and the empty-table / hidden-row edge cases.
  * `export_statistics_csv` — end-to-end. Mock the QFileDialog out so
    pytest doesn't block, point it at a tmp path, exercise it via the
    real mixin, and parse the resulting CSV.
"""

import csv
import io
import os

import pytest
from PyQt5.QtWidgets import (QTableWidget, QTableWidgetItem,
                             QComboBox, QCheckBox)

from traffic_client.statistics_section import TrafficGenClientStatisticsSection


def _make_table(headers, rows):
    """Build a QTableWidget with the given headers + list-of-lists rows.

    Cell `None` becomes a missing item (left as default). Cell strings
    become QTableWidgetItems. Tuple ('combo', value) and ('check', bool)
    become cellWidgets so we can test the widget-fallback path.
    """
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            if isinstance(v, tuple) and v and v[0] == "combo":
                cb = QComboBox(); cb.addItems([v[1]])
                cb.setCurrentText(v[1])
                t.setCellWidget(r, c, cb)
            elif isinstance(v, tuple) and v and v[0] == "check":
                cb = QCheckBox(); cb.setChecked(bool(v[1]))
                t.setCellWidget(r, c, cb)
            else:
                t.setItem(r, c, QTableWidgetItem(str(v)))
    return t


def _dump(table, section="Section"):
    buf = io.StringIO()
    w = csv.writer(buf)
    TrafficGenClientStatisticsSection._dump_table_to_csv(w, section, table)
    return buf.getvalue()


# ────────────────────────────────────────────────────── _dump_table_to_csv
def test_dump_writes_section_marker_and_header(qapp):
    """Every section starts with a `# Section: …` comment and then the
    column-header row — so the file remains self-describing."""
    t = _make_table(["a", "b"], [["1", "2"]])
    out = _dump(t, "Interface Statistics")
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["# Section: Interface Statistics"]
    assert rows[1] == ["a", "b"]
    assert rows[2] == ["1", "2"]


def test_dump_empty_table_writes_no_rows_marker(qapp):
    """Empty tables get a `# (no rows)` marker — the section is still
    visible in the file even when nothing has flowed."""
    t = _make_table(["a", "b"], [])
    rows = list(csv.reader(io.StringIO(_dump(t))))
    assert rows[1] == ["a", "b"]
    assert rows[2] == ["# (no rows)"]


def test_dump_none_table_writes_unavailable_marker(qapp):
    """None tables (mixin not initialised yet) write a `# (table not
    available)` line so the section is still acknowledged."""
    rows = list(csv.reader(io.StringIO(_dump(None, "Stream Statistics"))))
    assert rows[0] == ["# Section: Stream Statistics"]
    assert rows[1] == ["# (table not available)"]


def test_dump_missing_item_emits_empty_cell(qapp):
    """A missing QTableWidgetItem in the middle of a row writes "",
    not None — pure-text CSV consumers shouldn't see Python repr."""
    t = _make_table(["a", "b", "c"], [["x", None, "z"]])
    rows = list(csv.reader(io.StringIO(_dump(t))))
    assert rows[2] == ["x", "", "z"]


def test_dump_skips_hidden_rows(qapp):
    """Hidden rows (e.g. filtered out) must NOT be exported."""
    t = _make_table(["a"], [["keep"], ["hidden"], ["keep2"]])
    t.setRowHidden(1, True)
    rows = list(csv.reader(io.StringIO(_dump(t))))
    data_rows = [r for r in rows[2:] if r and not r[0].startswith("#")]
    assert data_rows == [["keep"], ["keep2"]]


def test_dump_reads_cell_widget_combo_and_checkbox(qapp):
    """Cells holding a QComboBox / QCheckBox (no QTableWidgetItem) must
    serialise via currentText() / isChecked()."""
    t = _make_table(
        ["combo", "check"],
        [[("combo", "Yes"), ("check", True)],
         [("combo", "No"),  ("check", False)]],
    )
    rows = list(csv.reader(io.StringIO(_dump(t))))
    assert rows[2] == ["Yes", "yes"]
    assert rows[3] == ["No", "no"]


# ────────────────────────────────────────────── export_statistics_csv (e2e)
def test_export_statistics_csv_writes_full_file(qapp, monkeypatch, tmp_path):
    """End-to-end: stub the file dialog to return a tmp path, call the
    real `export_statistics_csv`, then parse the resulting file and
    check the header block + both sections are present."""
    from PyQt5.QtWidgets import QFileDialog, QMessageBox

    out_path = str(tmp_path / "stats.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out_path, "CSV Files (*.csv)")))
    # Silence the success popup.
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    # Build a real mixin instance with two minimal tables attached.
    inst = TrafficGenClientStatisticsSection.__new__(TrafficGenClientStatisticsSection)
    inst.statistics_table = _make_table(
        ["Interface", "Sent Frames", "Received Frames"],
        [["TG 0 - ens1f0", "100", "98"]],
    )
    inst.stream_statistics_table = _make_table(
        ["Stream Name", "TX Count", "RX Count", "Latency (μs)"],
        [["str1", "1000", "1000", "12.3"]],
    )
    inst.server_interfaces = [{"tg_id": "0", "address": "http://1.1.1.1", "online": True}]

    inst.export_statistics_csv()
    assert os.path.exists(out_path), "export did not create the file"
    with open(out_path) as f:
        content = f.read()
    # Header block
    assert "# netgen statistics export" in content
    assert "# exported_at:" in content
    assert "# server: TG 0 http://1.1.1.1" in content
    # Two sections + their data
    assert "# Section: Interface Statistics" in content
    assert "TG 0 - ens1f0,100,98" in content
    assert "# Section: Stream Statistics" in content
    assert "str1,1000,1000,12.3" in content
