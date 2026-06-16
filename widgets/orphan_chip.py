"""v0.5.169: status-bar chip that surfaces orphan tx/rx_worker
processes the operator hasn't noticed yet.

Without this chip, orphans only become visible when:
  * The operator clicks Stop All (which now sweeps them; v0.5.168).
  * The operator clicks Start on a NIC that collides with one
    (pre-flight refuses; v0.5.168).

Both are reactive. Operators want to KNOW about the orphan
before it eats throughput on their next test. A small permanent
status-bar pill (`🧟 2 orphans`) gives them that visibility —
clicking it pops a small dialog enumerating them with a Reap
button.

Polls `/api/streams/orphans` every 10 s per registered server URL.
Empty across all servers = chip hidden.

This module is otherwise a near-clone of widgets/dpdk_readiness_chip.py
(same QThread fetcher, same chip styling, same singleton-fetch
dedup). The chip is wired into the main window's status bar via
traffic_client/main.py.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import requests
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


DEFAULT_POLL_INTERVAL_MS = 10_000   # 10 s — orphan detection is
                                    # cheap on the server (one /proc
                                    # walk) but spamming faster offers
                                    # nothing.


class _OrphanFetchThread(QThread):
    """One-shot QThread that probes one server's
    /api/streams/orphans endpoint. Emits the parsed orphan list
    on success, or an empty list on any failure."""

    payload_ready = pyqtSignal(str, list)  # (server_url, orphans)

    def __init__(self, server_url: str,
                 *, timeout_s: float = 4.0,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = server_url
        self._timeout_s = timeout_s

    def run(self) -> None:
        try:
            full = (f"{self._url.rstrip('/')}/api/streams/orphans")
            r = requests.get(full, timeout=self._timeout_s)
            if not r.ok:
                self.payload_ready.emit(self._url, [])
                return
            data = r.json() or {}
            self.payload_ready.emit(
                self._url, data.get("orphans", []) or [])
        except Exception as exc:
            logger.debug(
                f"[ORPHAN CHIP] fetch {self._url}: {exc}")
            self.payload_ready.emit(self._url, [])


class OrphanChip(QLabel):
    """One-glance orphan-count indicator.

    Hidden when no orphans are known. Shows `🧟 N orphans` (red
    background) when any are detected. Click opens a dialog
    enumerating them per server with a Reap All button.

    The chip is fed by a callable `server_urls_provider` so the
    main window can change the registered server set without
    re-creating the chip."""

    # Emitted on click — host wires this to open the orphan dialog
    # (or chip can open it directly via the bundled show_dialog()).
    clicked = pyqtSignal()

    _ACTIVE_CSS = (
        "QLabel {"
        "  background: #fef2f2;"
        "  border: 1px solid #fca5a5;"
        "  color: #b91c1c;"
        "  padding: 2px 8px;"
        "  border-radius: 8px;"
        "  font-size: 11px;"
        "  font-weight: 600;"
        "}"
    )

    def __init__(
        self,
        server_urls_provider: Callable[[], List[str]],
        *,
        parent: Optional[QWidget] = None,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ):
        super().__init__(parent)
        self._get_server_urls = server_urls_provider
        self._poll_interval_ms = int(poll_interval_ms)
        # Per-server orphan lists. Aggregated for the chip count
        # and exposed to the reap dialog.
        self._orphans_by_server: Dict[str, List[dict]] = {}
        # In-flight fetch dedup: one per server URL.
        self._in_flight: Dict[str, _OrphanFetchThread] = {}
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(self._ACTIVE_CSS)
        self.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setInterval(
            self._poll_interval_ms or DEFAULT_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        if self._poll_interval_ms > 0:
            self._timer.start()
        QTimer.singleShot(500, self.refresh)

    # ──────────────────────────────────────────────── public API

    def refresh(self) -> None:
        """Re-probe every registered server. The fetches run on
        background QThreads; results land back on the UI thread
        via signal. Empty server set hides the chip."""
        urls = list(self._get_server_urls() or [])
        if not urls:
            self._orphans_by_server.clear()
            self._repaint()
            return
        # Drop entries for servers we no longer know about.
        stale = [u for u in self._orphans_by_server if u not in urls]
        for u in stale:
            self._orphans_by_server.pop(u, None)
        for url in urls:
            if url in self._in_flight:
                continue  # let the in-flight fetch land
            thread = _OrphanFetchThread(url, parent=self)
            thread.payload_ready.connect(self._on_payload)
            thread.finished.connect(
                lambda u=url: self._in_flight.pop(u, None))
            thread.finished.connect(thread.deleteLater)
            self._in_flight[url] = thread
            thread.start()

    def show_dialog(self) -> None:
        """Open the orphan-list dialog. Public so the host can wire
        an explicit menu action ("Show orphan workers…")."""
        if not self._aggregate_total():
            return
        dlg = OrphanReapDialog(
            self._orphans_by_server, parent=self.window())
        dlg.exec_()
        # Refresh after the dialog closes — operator may have
        # reaped some.
        QTimer.singleShot(300, self.refresh)

    # ──────────────────────────────────────────────── internals

    def _on_payload(self, server_url: str, orphans: list) -> None:
        if orphans:
            self._orphans_by_server[server_url] = orphans
        else:
            self._orphans_by_server.pop(server_url, None)
        self._repaint()

    def _aggregate_total(self) -> int:
        return sum(len(v) for v in self._orphans_by_server.values())

    def _repaint(self) -> None:
        total = self._aggregate_total()
        if total == 0:
            self.setVisible(False)
            return
        self.setText(
            f"🧟 {total} orphan{'s' if total != 1 else ''}"
        )
        srvs = list(self._orphans_by_server.keys())
        self.setToolTip(
            "Untracked tx/rx_worker processes on:\n  - "
            + "\n  - ".join(srvs)
            + "\n\nClick to enumerate + reap.")
        self.setVisible(True)

    # Qt event hooks
    def mousePressEvent(self, ev):  # noqa: N802 — Qt naming
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            self.show_dialog()
        super().mousePressEvent(ev)


class OrphanReapDialog(QDialog):
    """Modal that lists orphan workers (per server) and offers a
    one-click Reap. Each row is a (PID, role, stream-id, BDF,
    etime) cell line."""

    def __init__(
        self,
        orphans_by_server: Dict[str, List[dict]],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Orphan workers")
        self.setMinimumWidth(720)
        self._orphans_by_server = dict(orphans_by_server)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Untracked tx/rx_worker processes detected on the "
            "registered TGs. These leak CPU + PCIe on their NIC "
            "and drop other tests' throughput. Reap them?"
        ))

        # Flat table — easier than a tree for one-click reaps.
        cols = ("Server", "PID", "Role", "Stream-id",
                "BDF", "Elapsed", "Cmdline")
        self._table = QTableWidget(self)
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._populate_rows()
        root.addWidget(self._table)

        btns = QDialogButtonBox(
            QDialogButtonBox.Cancel, parent=self)
        self._reap_btn = QPushButton("🧹 Reap all")
        self._reap_btn.setStyleSheet(
            "QPushButton {"
            "  background: #b91c1c; color: #fff;"
            "  padding: 6px 14px; border-radius: 4px;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #991b1b; }"
        )
        self._reap_btn.clicked.connect(self._on_reap_all)
        btns.addButton(self._reap_btn, QDialogButtonBox.AcceptRole)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _populate_rows(self) -> None:
        rows = []
        for srv, orphs in self._orphans_by_server.items():
            for o in orphs:
                rows.append((srv, o))
        self._table.setRowCount(len(rows))
        for r, (srv, o) in enumerate(rows):
            sid = (o.get("stream_id") or "—")
            sid_short = sid[:8] + "…" if len(sid) > 9 else sid
            etime = o.get("etime_seconds")
            elapsed = f"{etime}s" if isinstance(etime, int) else "?"
            cells = [
                str(srv),
                str(o.get("pid") or "?"),
                str(o.get("role") or "?"),
                sid_short,
                str(o.get("bdf") or "?"),
                elapsed,
                (o.get("cmdline") or "")[:200],
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                self._table.setItem(r, c, item)
        self._table.resizeColumnsToContents()

    def _on_reap_all(self) -> None:
        """Fire one reap request per server, then close."""
        from PyQt5.QtWidgets import QMessageBox
        results = []
        for srv, orphs in self._orphans_by_server.items():
            pids = [o.get("pid") for o in orphs
                    if isinstance(o.get("pid"), int)]
            if not pids:
                continue
            ok, detail = self._reap_one_server(srv, pids)
            results.append((srv, ok, detail))
        any_fail = any(not ok for _, ok, _ in results)
        if any_fail:
            body = "\n".join(
                f"• {srv}: {detail}" for srv, ok, detail in results)
            QMessageBox.warning(
                self, "Some reaps failed",
                "Reap returned errors for:\n\n" + body)
        self.accept()

    @staticmethod
    def _reap_one_server(server_url: str, pids: list) -> tuple:
        url = (f"{server_url.rstrip('/')}/api/streams/orphans/reap")
        try:
            r = requests.post(url, json={"pids": list(pids)},
                              timeout=8)
            if not r.ok:
                return (False, f"HTTP {r.status_code}")
            data = r.json() or {}
            failed = data.get("failed") or []
            if failed:
                return (False, f"failed PIDs: {failed}")
            return (True, "ok")
        except Exception as exc:
            return (False, str(exc))
