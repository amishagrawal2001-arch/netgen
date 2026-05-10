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

        btn_row.addStretch(1)
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: #6b7280;")
        btn_row.addWidget(self.status_label)
        root.addLayout(btn_row)

        # Results table — one row per frame size
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels([
            "Frame size (B)", "Max no-drop pps", "Throughput (Gbps)",
            "% of line rate", "Attempts",
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.setRowCount(len(RFC2544_FRAME_SIZES))
        for i, fs in enumerate(RFC2544_FRAME_SIZES):
            item = QTableWidgetItem(str(fs))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.results_table.setItem(i, 0, item)
            for c in range(1, 5):
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
        # Reset results
        for i in range(len(RFC2544_FRAME_SIZES)):
            for c in range(1, 5):
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Export RFC 2544 Results", "rfc2544_results.csv",
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
                f.write("frame_size_bytes,max_no_drop_pps,throughput_gbps,"
                        "pct_of_line_rate,line_rate_pps,attempts\n")
                for entry in rows:
                    f.write(
                        f"{entry.get('frame_size')},"
                        f"{entry.get('max_no_drop_pps')},"
                        f"{entry.get('max_no_drop_gbps')},"
                        f"{entry.get('pct_of_line_rate')},"
                        f"{entry.get('line_rate_pps')},"
                        f"{len(entry.get('attempts') or [])}\n"
                    )
            QMessageBox.information(self, "Export complete",
                                    f"Wrote {len(rows)} row(s) to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"{e}")


def show_rfc2544_dialog(parent, server_url):
    """Convenience launcher — usable from a menu action."""
    dlg = Rfc2544Dialog(parent, server_url=server_url)
    dlg.exec_()
