"""DPDK Diagnostics dialog — v0.5.18.

Single dialog that merges the previous two-menu-item UX:
  * Status (was Tools → DPDK → Status...) — full per-subsystem state
  * Verify (was Tools → DPDK → Verify Installation) — quick 4-check view

Tabs for each view; both auto-fetch on open via the existing
_DpdkApiWorker async pattern (no UI thread blocking on /api/dpdk/*
calls). Read-only — for fixing issues the operator opens ★ Setup DPDK.

Operator-reported confusion pre-v0.5.18: "Is Verify Installation
different from Status? Which one do I use?" Answer was: both are
read-only diagnostics, Verify checks 4 things, Status shows the full
picture. Merging them removes the choice + frees a menu slot.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout,
)

from traffic_client.dpdk_menu_actions import _DpdkApiWorker


logger = logging.getLogger(__name__)


class DpdkDiagnosticsDialog(QDialog):
    """Read-only DPDK status + verify diagnostics dialog.

    Args:
        server_url: base URL of the target server (e.g.
            "http://san-hp-srv06:5050"). Required.
        auth_token: optional auth token; passed through to the worker.
        parent: Qt parent (typically the main window).
    """

    def __init__(
        self,
        server_url: str,
        auth_token: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._server_url = (server_url or "").rstrip("/")
        self._auth_token = auth_token
        self._workers: list = []

        self.setWindowTitle(f"DPDK Diagnostics — {self._server_url}")
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>Read-only DPDK diagnostics for {self._server_url}.</b><br>"
            f"<span style='color:#6b7280;'>To fix issues, close this "
            f"dialog and open <b>Tools → DPDK → ★ Setup DPDK</b>.</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._tabs = QTabWidget()
        self._status_view = QTextBrowser()
        self._status_view.setOpenExternalLinks(False)
        self._verify_view = QTextBrowser()
        self._verify_view.setOpenExternalLinks(False)
        self._tabs.addTab(self._status_view, "Status (full)")
        self._tabs.addTab(self._verify_view, "Verify (quick check)")
        layout.addWidget(self._tabs, 1)

        # Initial placeholder while we fetch.
        loading_html = (
            "<p style='color:#6b7280;'>Loading…</p>"
        )
        self._status_view.setHtml(loading_html)
        self._verify_view.setHtml(loading_html)

        # Buttons row: Refresh + Close.
        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip(
            "Re-fetch both Status and Verify from the server."
        )
        self._refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self) -> None:
        """Kick off both fetches in parallel via async workers."""
        self._refresh_btn.setEnabled(False)
        self._fetch_status()
        self._fetch_verify()

    def _fetch_status(self) -> None:
        # v0.5.18: check the shared 30s TTL cache first — if the
        # status-bar chip just polled, we can render instantly
        # instead of burning another round-trip.
        try:
            from traffic_client.dpdk_menu_actions import get_cached_dpdk_status
            cached = get_cached_dpdk_status(self._server_url)
        except Exception:
            cached = None
        if cached is not None:
            self._on_status_ready(cached, "")
            return
        w = _DpdkApiWorker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/status",
            timeout=10,
        )
        w.done.connect(self._on_status_ready)
        w.setParent(self)
        self._workers.append(w)
        w.start()

    def _fetch_verify(self) -> None:
        w = _DpdkApiWorker(
            method="GET",
            url=f"{self._server_url}/api/dpdk/verify",
            timeout=10,
        )
        w.done.connect(self._on_verify_ready)
        w.setParent(self)
        self._workers.append(w)
        w.start()

    # ─────────────────────────────────────────────────── status render

    def _on_status_ready(self, data, err) -> None:
        self._refresh_btn.setEnabled(True)
        if err:
            self._status_view.setHtml(
                f"<p style='color:#b91c1c;'><b>Status fetch failed:</b> "
                f"{err}</p>"
            )
            return
        # v0.5.18: populate the TTL cache so other surfaces
        # (status-bar chip, Make DPDK Ready survey) reuse this.
        try:
            from traffic_client.dpdk_menu_actions import cache_dpdk_status
            cache_dpdk_status(self._server_url, data or {})
        except Exception:
            pass
        self._status_view.setHtml(self._render_status_html(data or {}))

    def _render_status_html(self, status: dict) -> str:
        """Render the full /api/dpdk/status payload as an HTML table."""
        def cell(ok):
            if ok is True:
                return "<span style='color:#15803d; font-weight:600;'>✓</span>"
            if ok is False:
                return "<span style='color:#b91c1c; font-weight:600;'>✗</span>"
            return "<span style='color:#9ca3af;'>—</span>"

        # v0.5.39: surface hugetlbfs mount state separately. Hugepages
        # can be allocated in sysfs (✓ Hugepages configured) but the
        # /mnt/huge mount can be missing (✗ Hugepages mounted) after
        # a reboot if /etc/fstab wasn't updated — DPDK then fails
        # with "no free hugepages" even though /proc/meminfo says
        # HugePages_Total > 0.
        rows = [
            ("DPDK installed (libdpdk)", status.get("dpdk_installed")),
            ("tx_worker binary", status.get("tx_worker_exists")),
            ("Hugepages configured", status.get("hugepages_configured")),
            ("Hugepages mounted (/mnt/huge)", status.get("hugepages_mounted")),
            ("IOMMU enabled in kernel", status.get("iommu_enabled")),
            ("vfio module loaded", status.get("vfio_loaded")),
            ("vfio-pci module loaded", status.get("vfio_pci_loaded")),
        ]
        nbound = status.get("bound_interfaces_count")
        if nbound is not None:
            rows.append((f"NICs bound to DPDK", nbound))

        html = ["<table cellpadding='6' style='font-size:12px;'>"]
        for label, value in rows:
            if isinstance(value, bool) or value is None:
                v = cell(value)
            else:
                v = str(value)
            html.append(
                f"<tr><td style='color:#374151;'>{label}</td>"
                f"<td>{v}</td></tr>"
            )
        html.append("</table>")

        # Optional extras: DPDK version + IOMMU details.
        extras = []
        if status.get("dpdk_version"):
            extras.append(
                f"<b>DPDK version:</b> {status['dpdk_version']}"
            )
        if status.get("iommu_details"):
            extras.append(
                f"<b>IOMMU:</b> {status['iommu_details']}"
            )
        if status.get("hugepages_count"):
            extras.append(
                f"<b>Hugepages:</b> {status['hugepages_count']} pages allocated"
            )
        if extras:
            html.append("<p style='margin-top:10px;'>" +
                        "<br>".join(extras) + "</p>")

        return "<style>td{vertical-align:top;}</style>" + "".join(html)

    # ─────────────────────────────────────────────────── verify render

    def _on_verify_ready(self, data, err) -> None:
        if err:
            self._verify_view.setHtml(
                f"<p style='color:#b91c1c;'><b>Verify fetch failed:</b> "
                f"{err}</p>"
            )
            return
        self._verify_view.setHtml(self._render_verify_html(data or {}))

    def _render_verify_html(self, verify: dict) -> str:
        """Render /api/dpdk/verify (4-check summary)."""
        def cell(ok):
            if ok is True:
                return "<span style='color:#15803d; font-weight:600;'>✓</span>"
            return "<span style='color:#b91c1c; font-weight:600;'>✗</span>"

        rows = [
            ("DPDK Libraries", verify.get("dpdk_libraries")),
            ("DPDK Packet Generator (tx_worker)",
             verify.get("tx_worker_binary")),
            ("Hugepages", verify.get("hugepages")),
            ("Kernel Modules", verify.get("kernel_modules")),
        ]
        html = ["<table cellpadding='6' style='font-size:12px;'>"]
        for label, value in rows:
            html.append(
                f"<tr><td style='color:#374151;'>{label}</td>"
                f"<td>{cell(bool(value))}</td></tr>"
            )
        html.append("</table>")

        messages = verify.get("messages") or []
        if messages:
            html.append(
                "<p style='margin-top:14px;'><b>Details:</b></p>"
                "<ul style='margin-top:4px;'>"
            )
            for m in messages:
                # Color-code: ✓-ish messages green, ✗-ish red.
                color = "#374151"
                low = str(m).lower()
                if any(t in low for t in ("not found", "not configured", "missing", "fail")):
                    color = "#b91c1c"
                elif any(t in low for t in ("found", "configured", "loaded", "version")):
                    color = "#15803d"
                html.append(
                    f"<li style='color:{color};'>{m}</li>"
                )
            html.append("</ul>")

        return "<style>td{vertical-align:top;}</style>" + "".join(html)
