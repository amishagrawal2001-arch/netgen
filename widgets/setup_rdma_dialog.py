"""v0.5.27 — Setup RDMA wizard.

Companion to MakeDpdkReadyDialog. Drives install_rdma.sh on the
target server via /api/admin/install_rdma + /log polling. Much
simpler than the DPDK wizard:

  - install_rdma is a ~1-2 minute apt-install + kmod-load run
  - No IOMMU reboot prompt (RDMA stack doesn't touch BIOS settings)
  - No NIC bind step (RDMA uses kernel ib_uverbs, not VFIO)
  - No multi-phase plan; just spawn → tail → done

The dialog stays open after install completes so the operator can
see the final ibv_devices output (which lists detected RDMA HCAs).
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout,
)


class _InstallRdmaWorker(QThread):
    """POSTs /api/admin/install_rdma, then signals 'kicked off'."""

    started_ok = pyqtSignal(dict)        # success body
    failed = pyqtSignal(str)             # error string

    def __init__(self, server_url: str, parent=None):
        super().__init__(parent)
        self.server_url = server_url.rstrip("/")

    def run(self) -> None:
        try:
            import requests
        except Exception as e:
            self.failed.emit(f"requests import failed: {e}")
            return
        try:
            r = requests.post(
                f"{self.server_url}/api/admin/install_rdma",
                timeout=15,
            )
        except Exception as e:
            self.failed.emit(f"POST failed: {e}")
            return
        if r.status_code == 409:
            try:
                body = r.json()
            except Exception:
                body = {}
            # Already running — that's fine, we'll just start polling.
            self.started_ok.emit({"started": False, "already_running": True,
                                  "log_path": body.get("log_path")})
            return
        if r.status_code != 200:
            self.failed.emit(
                f"server returned HTTP {r.status_code}: {r.text[:300]}"
            )
            return
        try:
            self.started_ok.emit(r.json())
        except Exception as e:
            self.failed.emit(f"unparseable response: {e}")


class SetupRdmaDialog(QDialog):
    """Wizard for installing the RDMA stack on a netgen server."""

    def __init__(self, server_url: str, parent=None):
        super().__init__(parent)
        self.server_url = server_url.rstrip("/")
        self.setWindowTitle(f"Setup RDMA — {self._short_host()}")
        self.resize(820, 560)

        self._worker = None
        self._poll_timer = None
        self._install_finished = False

        v = QVBoxLayout(self)

        intro = QLabel(
            "<b>Setup RDMA</b><br><br>"
            "Installs the full RDMA stack on <b>" + self._short_host() + "</b>:"
            "<ul>"
            "<li><b>Userspace libs</b> — <code>libibverbs-dev</code>, "
            "<code>librdmacm-dev</code>, <code>libibmad-dev</code>, "
            "<code>libibumad-dev</code>, <code>libibnetdisc-dev</code></li>"
            "<li><b>Stack</b> — <code>rdma-core</code></li>"
            "<li><b>Test tools</b> — <code>perftest</code> (ib_send_bw / "
            "read_bw / write_bw), <code>rdmacm-utils</code> (rping / ucmatose)</li>"
            "<li><b>Utilities</b> — <code>ibverbs-utils</code>, "
            "<code>infiniband-diags</code></li>"
            "<li><b>Python bindings</b> — <code>python3-pyverbs</code></li>"
            "<li><b>Subnet manager</b> — <code>opensm</code> "
            "(installed disabled — enable manually if your IB fabric needs it)</li>"
            "<li><b>Firmware tools</b> — <code>mstflint</code></li>"
            "<li><b>Mellanox dev headers</b> — <code>libmlx5-dev</code> "
            "(ConnectX-4+), <code>libmlx4-dev</code> (ConnectX-3) — "
            "optional, may fail without MOFED repo</li>"
            "</ul>"
            "Loads kernel modules <code>ib_uverbs</code>, <code>rdma_cm</code>, "
            "<code>rdma_ucm</code>, <code>ib_umad</code>, <code>iw_cm</code> "
            "and persists them across reboots.<br><br>"
            "<i>Typical duration: 1-2 minutes.</i>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        v.addWidget(intro)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate; install is short
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.status_label = QLabel("Ready to install.")
        v.addWidget(self.status_label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo, Consolas, monospace", 10))
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        v.addWidget(self.log_view, stretch=1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.install_btn = QPushButton("Install")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._on_install_clicked)
        btns.addWidget(self.install_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        btns.addWidget(self.close_btn)
        v.addLayout(btns)

    def _short_host(self) -> str:
        try:
            p = urlparse(self.server_url)
            return p.hostname or self.server_url
        except Exception:
            return self.server_url

    def _on_install_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.install_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status_label.setText("POSTing /api/admin/install_rdma...")
        self.log_view.clear()

        self._worker = _InstallRdmaWorker(self.server_url, parent=self)
        self._worker.started_ok.connect(self._on_install_started)
        self._worker.failed.connect(self._on_install_failed)
        self._worker.start()

    def _on_install_started(self, body: dict) -> None:
        already = body.get("already_running")
        log_path = body.get("log_path", "")
        if already:
            self.status_label.setText(
                f"RDMA install already running on server. Tailing existing log {log_path}..."
            )
        else:
            self.status_label.setText(
                f"install_rdma.sh started (pid {body.get('pid')}). Tailing log..."
            )
        # Start polling /api/admin/install_rdma/log every 2 s.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start()
        self._poll_log()

    def _on_install_failed(self, err: str) -> None:
        self.progress.setVisible(False)
        self.install_btn.setEnabled(True)
        self.status_label.setText(f"Install failed to start: {err}")
        QMessageBox.warning(self, "Setup RDMA", f"Failed to start install:\n\n{err}")

    def _poll_log(self) -> None:
        try:
            import requests
        except Exception:
            return
        try:
            r = requests.get(
                f"{self.server_url}/api/admin/install_rdma/log",
                timeout=8,
            )
            if r.status_code != 200:
                return
            body = r.json()
        except Exception:
            # Transient — server may be busy. Try again next tick.
            return

        log_text = body.get("log", "")
        # Re-render the whole log each tick — it's small (<10 KB
        # typical). Preserves scroll position only when the user
        # has manually scrolled up.
        if log_text and log_text != self.log_view.toPlainText():
            scrollbar = self.log_view.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
            self.log_view.setPlainText(log_text)
            if at_bottom:
                self.log_view.moveCursor(QTextCursor.End)

        if not body.get("running", False):
            self._install_finished = True
            if self._poll_timer:
                self._poll_timer.stop()
            self.progress.setVisible(False)
            rc = body.get("return_code")
            if rc == 0:
                self.status_label.setText(
                    "✓ RDMA install complete. Review the ibv_devices output above."
                )
                self.install_btn.setText("Re-install")
                self.install_btn.setEnabled(True)
            else:
                self.status_label.setText(
                    f"✗ install_rdma.sh exited rc={rc}. See log above for the failing step."
                )
                self.install_btn.setEnabled(True)

    def reject(self) -> None:  # type: ignore[override]
        # Don't kill the in-flight install — let it finish on the
        # server. Just stop polling and close.
        if self._poll_timer:
            self._poll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(1500)
        super().reject()
