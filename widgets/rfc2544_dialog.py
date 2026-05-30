# widgets/rfc2544_dialog.py
"""
RFC 2544 Throughput Test dialog.

Spirent / Ixia have full-featured RFC 2544 wizards (throughput, latency,
frame loss, back-to-back). This implementation covers the throughput test
(§26.1) — binary-search for the max no-drop rate at each of the 7
standard frame sizes — which is the most commonly run benchmark.

The actual binary search runs server-side via /api/rfc2544/start; the
client kicks it off and polls /api/rfc2544/progress for live results.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import requests
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QDoubleSpinBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QGroupBox, QMessageBox, QFileDialog, QHeaderView,
)

logger = logging.getLogger(__name__)

# Standard RFC 2544 frame sizes (bytes). Section 9 of the RFC.
RFC2544_FRAME_SIZES = [64, 128, 256, 512, 1024, 1280, 1518]


class Rfc2544Dialog(QDialog):
    """Standalone dialog. Lives outside the stream editor since it
    drives the test directly via REST against the server."""

    def __init__(self, parent=None, server_url: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("RFC 2544 Throughput Test")
        self.setGeometry(200, 200, 820, 640)
        self.setMinimumSize(720, 480)
        self.server_url = (server_url or "").rstrip("/")
        self._poll_timer: Optional[QTimer] = None
        self._build_ui()

    # ------------------------------------------------------------- UI

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Parameters group
        params_box = QGroupBox("Test Parameters")
        params_layout = QFormLayout(params_box)

        self.tx_iface_field = QLineEdit("enp181s0f0np0")
        self.tx_iface_field.setToolTip(
            "Interface that will transmit traffic. Must be DPDK-capable "
            "(libdpdk + tx_worker built) for the line-rate tests."
        )
        params_layout.addRow("TX interface:", self.tx_iface_field)

        self.rx_iface_field = QLineEdit("")
        self.rx_iface_field.setPlaceholderText("(defaults to TX iface — loopback)")
        params_layout.addRow("RX interface:", self.rx_iface_field)

        self.mac_src_field = QLineEdit("aa:bb:cc:dd:ee:01")
        self.mac_dst_field = QLineEdit("aa:bb:cc:dd:ee:02")
        params_layout.addRow("Source MAC:", self.mac_src_field)
        params_layout.addRow("Destination MAC:", self.mac_dst_field)

        self.ip_src_field = QLineEdit("10.0.0.1")
        self.ip_dst_field = QLineEdit("10.0.0.2")
        params_layout.addRow("Source IPv4:", self.ip_src_field)
        params_layout.addRow("Destination IPv4:", self.ip_dst_field)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(2, 600)
        self.duration_spin.setValue(10)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setToolTip(
            "Per-step traffic duration. RFC 2544 recommends 60s for trial "
            "runs and longer for certification; 10s is a fast sanity check."
        )
        params_layout.addRow("Duration per step:", self.duration_spin)

        self.loss_spin = QDoubleSpinBox()
        self.loss_spin.setRange(0.0, 50.0)
        self.loss_spin.setSingleStep(0.1)
        self.loss_spin.setValue(0.0)
        self.loss_spin.setSuffix(" %")
        self.loss_spin.setToolTip(
            "Acceptable loss percentage for a step to be considered "
            "'passing'. Default 0.0 (strict, per RFC 2544)."
        )
        params_layout.addRow("Target loss:", self.loss_spin)

        self.resolution_spin = QSpinBox()
        self.resolution_spin.setRange(1000, 10_000_000)
        self.resolution_spin.setSingleStep(10_000)
        self.resolution_spin.setValue(100_000)
        self.resolution_spin.setSuffix(" pps")
        self.resolution_spin.setToolTip(
            "Binary-search resolution. Search stops when the rate band "
            "is narrower than this. Smaller = more accurate, longer test."
        )
        params_layout.addRow("Binary-search resolution:", self.resolution_spin)

        self.dpdk_checkbox = QCheckBox("Use DPDK tx_worker (recommended)")
        self.dpdk_checkbox.setChecked(True)
        params_layout.addRow("", self.dpdk_checkbox)

        # RFC 2544 §26.2 — capture one-way latency at the determined
        # throughput rate. Embeds NLAT timestamps in each frame so the
        # rx-side sampler can decode them. Default off so the baseline
        # §26.1 throughput test stays bit-for-bit identical to the prior
        # release; opt in for full latency results.
        self.latency_checkbox = QCheckBox(
            "Capture latency (RFC 2544 §26.2 — adds NLAT timestamps)"
        )
        self.latency_checkbox.setChecked(False)
        self.latency_checkbox.setToolTip(
            "Embed NLAT one-way-latency timestamps in each frame so the "
            "RX side can compute min / p50 / p95 / p99 / max latency. "
            "Requires the latency sampler to be running on the RX side "
            "(auto-started by the server)."
        )
        params_layout.addRow("", self.latency_checkbox)

        root.addWidget(params_box)

        # Control buttons
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start Test")
        self.start_btn.setStyleSheet(
            "QPushButton { border: 1px solid #2563eb; border-radius: 5px; "
            "padding: 6px 14px; background: #ffffff; color: #1d4ed8; "
            "font-weight: 600; }"
            "QPushButton:hover { background: #eff6ff; }"
            "QPushButton:disabled { color: #9ca3af; border-color: #cbd5e1; }"
        )
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        # Stop button — cooperative cancel of an in-flight RFC 2544
        # test. Server flips stop_requested=True and halts the active
        # stream so the runner thread exits within ~0.5s. Disabled
        # until Start fires.
        self.stop_btn = QPushButton("Stop Test")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { border: 1px solid #dc2626; border-radius: 5px; "
            "padding: 6px 14px; background: #ffffff; color: #dc2626; "
            "font-weight: 600; }"
            "QPushButton:hover { background: #fef2f2; }"
            "QPushButton:disabled { color: #9ca3af; border-color: #cbd5e1; }"
        )
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_csv)
        btn_row.addWidget(self.export_btn)

        # HTML report — self-contained, browser-printable test report with
        # params + results table. No PDF dep; the browser's "Print → Save
        # as PDF" handles the PDF need.
        self.export_html_btn = QPushButton("Export HTML Report")
        self.export_html_btn.setEnabled(False)
        self.export_html_btn.setToolTip(
            "Save a single-file HTML report (test parameters + full results "
            "table). Open in any browser and Print → Save as PDF for a "
            "formal test deliverable."
        )
        self.export_html_btn.clicked.connect(self._on_export_html)
        btn_row.addWidget(self.export_html_btn)

        btn_row.addStretch(1)
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: #6b7280;")
        btn_row.addWidget(self.status_label)
        root.addLayout(btn_row)

        # Results table — one row per frame size. New in 0.2.59: three
        # latency columns (p50/p95/p99 µs) populated from each progress
        # entry's `latency` dict when Capture-latency was on. Render "—"
        # when the dict is missing or the field is None (old server, or
        # capture-latency disabled), so the columns are forward-compatible.
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(8)
        self.results_table.setHorizontalHeaderLabels([
            "Frame size (B)", "Max no-drop pps", "Throughput (Gbps)",
            "% of line rate", "Attempts",
            "Lat p50 (µs)", "Lat p95 (µs)", "Lat p99 (µs)",
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.setRowCount(len(RFC2544_FRAME_SIZES))
        for i, fs in enumerate(RFC2544_FRAME_SIZES):
            item = QTableWidgetItem(str(fs))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.results_table.setItem(i, 0, item)
            for c in range(1, self.results_table.columnCount()):
                self.results_table.setItem(i, c, QTableWidgetItem("—"))
        root.addWidget(self.results_table, 1)

        # Close button
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    # ------------------------------------------------------------- Logic

    def _on_start(self):
        if not self.server_url:
            QMessageBox.warning(self, "No server",
                                "No server URL — can't run the test.")
            return
        params = {
            "tx_iface": self.tx_iface_field.text().strip(),
            "rx_iface": self.rx_iface_field.text().strip() or None,
            "frame_sizes": RFC2544_FRAME_SIZES,
            "duration_per_step": self.duration_spin.value(),
            "target_loss_pct": self.loss_spin.value(),
            "resolution_pps": self.resolution_spin.value(),
            "mac_src": self.mac_src_field.text().strip(),
            "mac_dst": self.mac_dst_field.text().strip(),
            "ip_src": self.ip_src_field.text().strip(),
            "ip_dst": self.ip_dst_field.text().strip(),
            "dpdk_enable": self.dpdk_checkbox.isChecked(),
            "capture_latency": self.latency_checkbox.isChecked(),
        }
        url = f"{self.server_url}/api/rfc2544/start"
        try:
            r = requests.post(url, json=params, timeout=10)
            data = r.json()
        except Exception as e:
            QMessageBox.warning(self, "Start failed", f"{e}")
            return
        if not data.get("ok"):
            QMessageBox.warning(self, "Start refused",
                                data.get("error") or "Unknown error")
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.status_label.setText("Test running…")
        self.status_label.setStyleSheet("color: #1d4ed8; font-weight: 600;")
        # Reset results across ALL data columns (8 in 0.2.59).
        for i in range(len(RFC2544_FRAME_SIZES)):
            for c in range(1, self.results_table.columnCount()):
                self.results_table.item(i, c).setText("—")
        # Poll every 2s
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_progress)
        self._poll_timer.start()
        # Kick once immediately so status updates fast
        self._poll_progress()

    def _poll_progress(self):
        try:
            r = requests.get(f"{self.server_url}/api/rfc2544/progress", timeout=5)
            data = r.json()
        except Exception as e:
            logger.debug(f"[RFC 2544] poll failed: {e}")
            return

        # Update results table
        results = data.get("progress") or []
        for entry in results:
            fs = entry.get("frame_size")
            try:
                row = RFC2544_FRAME_SIZES.index(fs)
            except ValueError:
                continue
            pps = entry.get("max_no_drop_pps", 0)
            gbps = entry.get("max_no_drop_gbps", 0)
            pct = entry.get("pct_of_line_rate", 0)
            attempts = len(entry.get("attempts") or [])
            self.results_table.item(row, 1).setText(f"{int(pps):,}")
            self.results_table.item(row, 2).setText(f"{gbps:.2f}")
            self.results_table.item(row, 3).setText(f"{pct:.1f}%")
            self.results_table.item(row, 4).setText(str(attempts))
            # Latency columns — 0.2.59 added them; pre-0.2.59 servers
            # don't supply the dict, so an "—" fallback keeps the table
            # usable against any server version.
            lat = entry.get("latency") or {}
            for col, key in ((5, "p50_us"), (6, "p95_us"), (7, "p99_us")):
                v = lat.get(key)
                self.results_table.item(row, col).setText(
                    f"{v:.1f}" if isinstance(v, (int, float)) else "—"
                )

        # Status line
        cs = data.get("current_step")
        if cs:
            self.status_label.setText(
                f"Testing {cs.get('frame_size')}B — {cs.get('phase', '')}"
            )

        # Done?
        if not data.get("running"):
            if self._poll_timer:
                self._poll_timer.stop()
                self._poll_timer = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            err = data.get("error")
            if err:
                self.status_label.setText(f"Failed: {err}")
                self.status_label.setStyleSheet("color: #dc2626; font-weight: 600;")
            else:
                self.status_label.setText(
                    f"Done — {len(results)}/{len(RFC2544_FRAME_SIZES)} frame sizes"
                )
                self.status_label.setStyleSheet("color: #15803d; font-weight: 600;")
                self.export_btn.setEnabled(bool(results))
                self.export_html_btn.setEnabled(bool(results))

    def _on_stop(self):
        """Cooperative cancel — POST /api/rfc2544/stop and let the next
        poll tick discover that running=False. Disable the button so
        the user can't double-click while the server is processing."""
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopping…")
        self.status_label.setStyleSheet("color: #b91c1c; font-weight: 600;")
        try:
            r = requests.post(
                f"{self.server_url}/api/rfc2544/stop", timeout=5
            )
            if not r.ok:
                logger.warning(f"[RFC 2544] stop returned {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"[RFC 2544] stop request failed: {e}")
            # Don't re-enable Stop — the user will see status from the
            # next poll. If the test is genuinely stuck, restarting
            # the server is the escape hatch.

    def _on_export_csv(self):
        # Timestamped default filename — operator hits Save without
        # typing, and re-exports don't silently overwrite the previous
        # one. Same YYYY-MM-DD_HH-MM-SS convention as the HTML export
        # below + preflight findings export (v0.2.74).
        import datetime as _dt
        default_name = (f"rfc2544_results_"
                        f"{_dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export RFC 2544 Results", default_name,
            "CSV (*.csv)"
        )
        if not path:
            return
        try:
            r = requests.get(f"{self.server_url}/api/rfc2544/progress", timeout=5)
            data = r.json()
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")
            return
        rows = data.get("progress") or []
        try:
            with open(path, "w") as f:
                # 0.2.59: added p50/p95/p99 latency columns. Empty when
                # capture-latency was off or the server doesn't supply
                # them (pre-0.2.59).
                f.write("frame_size_bytes,max_no_drop_pps,throughput_gbps,"
                        "pct_of_line_rate,line_rate_pps,attempts,"
                        "lat_p50_us,lat_p95_us,lat_p99_us\n")
                for entry in rows:
                    lat = entry.get("latency") or {}
                    def _fmt(v):
                        return f"{v:.2f}" if isinstance(v, (int, float)) else ""
                    f.write(
                        f"{entry.get('frame_size')},"
                        f"{entry.get('max_no_drop_pps')},"
                        f"{entry.get('max_no_drop_gbps')},"
                        f"{entry.get('pct_of_line_rate')},"
                        f"{entry.get('line_rate_pps')},"
                        f"{len(entry.get('attempts') or [])},"
                        f"{_fmt(lat.get('p50_us'))},"
                        f"{_fmt(lat.get('p95_us'))},"
                        f"{_fmt(lat.get('p99_us'))}\n"
                    )
            QMessageBox.information(self, "Export complete",
                                    f"Wrote {len(rows)} row(s) to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")


    # ──────────────────────────────────────────────────── HTML report
    def _on_export_html(self):
        """Save a single-file, self-contained HTML test report. Pulls the
        latest /api/rfc2544/progress + the dialog's params, formats them
        into a printable layout, writes the file. Browser's Print → Save
        as PDF handles the PDF need without adding a PDF dep."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        import datetime as _dt

        # YYYY-MM-DD_HH-MM-SS — matches the CSV export above and the
        # preflight findings export (v0.2.74) so all of netgen's
        # exports sort naturally next to each other on disk.
        path, _ = QFileDialog.getSaveFileName(
            self, "Export RFC 2544 Report",
            f"rfc2544_report_{_dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.html",
            "HTML (*.html)"
        )
        if not path:
            return
        try:
            r = requests.get(f"{self.server_url}/api/rfc2544/progress", timeout=5)
            data = r.json()
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"Could not fetch progress: {e}")
            return
        rows = data.get("progress") or []
        params = self._current_params_for_report()
        html = build_rfc2544_html_report(params, rows,
                                         server_url=self.server_url)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            QMessageBox.information(
                self, "Export complete",
                f"Wrote HTML report to:\n{path}\n\n"
                f"Open in a browser; Print → Save as PDF for a PDF version."
            )
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")

    def _current_params_for_report(self) -> dict:
        """Snapshot of the dialog's current parameter widgets. Used by
        the HTML report; kept separate from `_on_start`'s payload so the
        report can include user-friendly labels (e.g. "10 s") rather
        than raw ints."""
        return {
            "tx_iface": self.tx_iface_field.text().strip(),
            "rx_iface": (self.rx_iface_field.text().strip()
                         or self.tx_iface_field.text().strip() + " (loopback)"),
            "mac_src": self.mac_src_field.text().strip(),
            "mac_dst": self.mac_dst_field.text().strip(),
            "ip_src":  self.ip_src_field.text().strip(),
            "ip_dst":  self.ip_dst_field.text().strip(),
            "duration_per_step": self.duration_spin.value(),
            "target_loss_pct":   self.loss_spin.value(),
            "resolution_pps":    self.resolution_spin.value(),
            "dpdk_enable":       self.dpdk_checkbox.isChecked(),
            "capture_latency":   self.latency_checkbox.isChecked(),
        }


def build_rfc2544_html_report(params: dict, rows: list,
                              server_url: str = "") -> str:
    """Render a single self-contained HTML report. Pure-function — easy
    to unit-test without spinning up the dialog.

    Layout:
      1. Header (test name + timestamp).
      2. Parameter block (two-column key/value).
      3. Results table (one row per frame size, all 8 columns).
      4. Summary footer (best throughput, server URL).

    No external CSS / JS / images so the file is a fully portable
    deliverable. Print-friendly: a single @media print rule shrinks the
    margins for clean PDF export.
    """
    import datetime as _dt
    import html as _h

    def esc(v):
        return _h.escape(str(v if v is not None else ""))

    def fmt_lat(lat, key):
        v = (lat or {}).get(key)
        return f"{v:.1f}" if isinstance(v, (int, float)) else "—"

    pieces = []
    pieces.append("<!doctype html><html><head><meta charset='utf-8'>")
    pieces.append("<title>RFC 2544 Throughput Test Report</title>")
    pieces.append("""
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
             sans-serif; color: #111827; margin: 36px; max-width: 1100px; }
      h1   { margin: 0 0 4px 0; font-size: 22px; color: #0f172a; }
      .sub { color: #64748b; font-size: 13px; margin-bottom: 24px; }
      h2   { font-size: 14px; color: #475569; margin: 24px 0 8px 0;
             text-transform: uppercase; letter-spacing: 0.5px; }
      table.kv { border-collapse: collapse; }
      table.kv td { padding: 4px 14px 4px 0; font-size: 13px; vertical-align: top; }
      table.kv td.k { color: #64748b; }
      table.kv td.v { color: #0f172a; font-family: 'SF Mono', Menlo, monospace; }
      table.results { border-collapse: collapse; width: 100%; margin-top: 8px; }
      table.results th, table.results td {
          border: 1px solid #e2e8f0; padding: 6px 10px;
          font-size: 12px; text-align: right;
      }
      table.results th { background: #f1f5f9; color: #475569; font-weight: 600; }
      table.results td:first-child, table.results th:first-child { text-align: center; }
      .foot { color: #94a3b8; font-size: 11px; margin-top: 28px;
              border-top: 1px solid #e2e8f0; padding-top: 12px; }
      @media print { body { margin: 16px; } }
    </style></head><body>""")

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pieces.append(f"<h1>RFC 2544 Throughput Test Report</h1>")
    pieces.append(f"<div class='sub'>Generated {esc(now)}"
                  + (f" · {esc(server_url)}" if server_url else "")
                  + "</div>")

    # Parameters
    pieces.append("<h2>Test parameters</h2><table class='kv'>")
    label_map = [
        ("TX interface",         "tx_iface"),
        ("RX interface",         "rx_iface"),
        ("Source MAC",           "mac_src"),
        ("Destination MAC",      "mac_dst"),
        ("Source IPv4",          "ip_src"),
        ("Destination IPv4",     "ip_dst"),
        ("Duration per step (s)", "duration_per_step"),
        ("Target loss (%)",      "target_loss_pct"),
        ("Resolution (pps)",     "resolution_pps"),
        ("DPDK tx_worker",       "dpdk_enable"),
        ("Capture latency",      "capture_latency"),
    ]
    for label, key in label_map:
        pieces.append(f"<tr><td class='k'>{esc(label)}</td>"
                      f"<td class='v'>{esc(params.get(key))}</td></tr>")
    pieces.append("</table>")

    # Results table
    pieces.append("<h2>Results</h2>")
    if not rows:
        pieces.append("<p style='color:#dc2626;'>(no results — test did not complete)</p>")
    else:
        pieces.append("<table class='results'><thead><tr>"
                      "<th>Frame size (B)</th>"
                      "<th>Max no-drop pps</th>"
                      "<th>Throughput (Gbps)</th>"
                      "<th>% of line rate</th>"
                      "<th>Attempts</th>"
                      "<th>Lat p50 (µs)</th>"
                      "<th>Lat p95 (µs)</th>"
                      "<th>Lat p99 (µs)</th>"
                      "</tr></thead><tbody>")
        for entry in rows:
            lat = entry.get("latency") or {}
            pct = entry.get("pct_of_line_rate")
            pps = entry.get("max_no_drop_pps") or 0
            gbps = entry.get("max_no_drop_gbps") or 0
            pieces.append(
                "<tr>"
                f"<td>{esc(entry.get('frame_size'))}</td>"
                f"<td>{int(pps):,}</td>"
                f"<td>{gbps:.2f}</td>"
                f"<td>{esc(pct)}%</td>"
                f"<td>{esc(len(entry.get('attempts') or []))}</td>"
                f"<td>{fmt_lat(lat, 'p50_us')}</td>"
                f"<td>{fmt_lat(lat, 'p95_us')}</td>"
                f"<td>{fmt_lat(lat, 'p99_us')}</td>"
                "</tr>")
        pieces.append("</tbody></table>")

    # Footer summary
    if rows:
        best = max(rows, key=lambda e: e.get("max_no_drop_gbps") or 0)
        pieces.append("<div class='foot'>"
                      f"Best throughput: <b>{best.get('max_no_drop_gbps')} Gbps</b> "
                      f"at frame size <b>{best.get('frame_size')} B</b>. "
                      f"Tested {len(rows)} frame size(s)."
                      "</div>")
    pieces.append("</body></html>")
    return "".join(pieces)


def show_rfc2544_dialog(parent, server_url):
    """Convenience launcher — usable from a menu action."""
    dlg = Rfc2544Dialog(parent, server_url=server_url)
    dlg.exec_()
