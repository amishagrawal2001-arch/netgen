"""GUI for EVPN Type-2 bulk injection (v0.2.63).

Companion to the v0.2.62 server endpoints. Two halves:

* **Inject form** — VXLAN iface, base MAC, optional base IP + remote
  VTEP + L3 iface, count. Submits to ``/api/evpn/type2/inject``.
* **Active injections table** — what's currently registered on the
  server; one "Clear" button per row, plus a Refresh.

The dialog is non-modal-friendly (no QDialog.exec blocking on success),
the HTTP layer is request-driven (no QThread — calls are short and
synchronous, with a small status label that goes amber/red on failure),
and every widget references are kept on ``self`` so the headless tests
can drive them without touching the network.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import requests
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


class EvpnInjectDialog(QDialog):
    """Standalone dialog for EVPN Type-2 bulk inject + clear.

    Pass `server_url` (e.g. ``http://srv02:5050``) so the dialog
    knows where to POST. `default_iface` pre-fills the VXLAN
    interface field — typically the iface of the row the user
    right-clicked, plumbed by the caller.
    """

    COLUMNS = ["inject_id", "iface", "L3 iface", "remote VTEP", "count", ""]
    COL_ID, COL_IFACE, COL_L3, COL_VTEP, COL_COUNT, COL_CLEAR = range(6)

    def __init__(self, parent: Optional[QWidget] = None, *,
                 server_url: str = "", default_iface: str = ""):
        super().__init__(parent)
        self.setWindowTitle("EVPN Type-2 Bulk Inject")
        self.setMinimumWidth(640)
        self.server_url = (server_url or "").rstrip("/")
        self._build_ui(default_iface)
        # Auto-load the active-injections list on open so the user can
        # see what's already running before they add more.
        self.refresh_active()

    # ────────────────────────────────────────────────────────────── UI
    def _build_ui(self, default_iface: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # Inject form
        form_box = QGroupBox("Inject")
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignRight)

        self.iface_field = QLineEdit(default_iface)
        self.iface_field.setPlaceholderText("vxlan100")
        self.iface_field.setToolTip(
            "VXLAN interface to populate the bridge FDB on. The MAC entries "
            "are attached here; FRR/zebra reads them and BGP advertises Type-2."
        )

        self.base_mac_field = QLineEdit("aa:bb:cc:00:00:01")
        self.base_mac_field.setToolTip(
            "First MAC in the synthetic range. Subsequent MACs increment by 1."
        )

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 1_000_000)
        self.count_spin.setValue(100)
        self.count_spin.setToolTip(
            "Number of consecutive entries to generate from base_mac (and "
            "base_ip if set). 10 000+ scales fine; the kernel is the floor."
        )

        self.base_ip_field = QLineEdit("")
        self.base_ip_field.setPlaceholderText("(optional — leave blank for MAC-only Type-2)")
        self.base_ip_field.setToolTip(
            "Optional starting IPv4 for MAC+IP Type-2 sub-routes. Increments "
            "in lockstep with the MAC range. Blank = MAC-only entries."
        )

        self.remote_vtep_field = QLineEdit("")
        self.remote_vtep_field.setPlaceholderText("(optional remote VTEP IP)")
        self.remote_vtep_field.setToolTip(
            "When set, every FDB entry is attached to this remote VTEP via "
            "`bridge fdb … dst <vtep>`. The advertised Type-2 carries this "
            "as the next-hop. Leave blank for local-only learning."
        )

        self.l3_iface_field = QLineEdit("")
        self.l3_iface_field.setPlaceholderText("(optional — bridge / SVI interface for IP→MAC)")
        self.l3_iface_field.setToolTip(
            "Interface where `ip neigh add` entries land (typically the "
            "bridge/SVI for the VNI). Defaults to the VXLAN interface."
        )

        form.addRow("VXLAN interface:", self.iface_field)
        form.addRow("Base MAC:",        self.base_mac_field)
        form.addRow("Count:",           self.count_spin)
        form.addRow("Base IP:",         self.base_ip_field)
        form.addRow("Remote VTEP IP:",  self.remote_vtep_field)
        form.addRow("L3 interface:",    self.l3_iface_field)

        outer.addWidget(form_box)

        # Inject button + result line
        action_row = QHBoxLayout()
        self.inject_btn = QPushButton("Inject")
        self.inject_btn.setCursor(Qt.PointingHandCursor)
        self.inject_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "font-weight: 600; padding: 5px 18px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
        self.inject_btn.clicked.connect(self._on_inject)
        action_row.addWidget(self.inject_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.status_label.setWordWrap(True)
        action_row.addWidget(self.status_label, 1)
        outer.addLayout(action_row)

        # Active injections
        active_box = QGroupBox("Active injections on this server")
        ab_layout = QVBoxLayout(active_box)

        refresh_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_active)
        refresh_row.addWidget(self.refresh_btn)
        refresh_row.addStretch(1)
        ab_layout.addLayout(refresh_row)

        self.active_table = QTableWidget(0, len(self.COLUMNS))
        self.active_table.setHorizontalHeaderLabels(self.COLUMNS)
        self.active_table.verticalHeader().setVisible(False)
        self.active_table.horizontalHeader().setStretchLastSection(False)
        self.active_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        ab_layout.addWidget(self.active_table)

        outer.addWidget(active_box, 1)

        # Close button
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        outer.addLayout(close_row)

    # ───────────────────────────────────────────────────────── helpers
    def _auth_headers(self) -> Dict[str, str]:
        tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    def _set_status(self, text: str, *, ok: bool = True, warn: bool = False) -> None:
        if warn:
            color = "#b45309"     # amber
        elif ok:
            color = "#15803d"     # green
        else:
            color = "#b91c1c"     # red
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(text)

    # ─────────────────────────────────────────────────── form → payload
    def build_inject_payload(self) -> Optional[Dict[str, Any]]:
        """Read the form and return the body for /api/evpn/type2/inject,
        or None when validation fails (and shows a warning)."""
        iface = self.iface_field.text().strip()
        base_mac = self.base_mac_field.text().strip()
        if not iface or not base_mac:
            QMessageBox.warning(
                self, "Missing field",
                "VXLAN interface and Base MAC are required."
            )
            return None
        body: Dict[str, Any] = {
            "iface":    iface,
            "base_mac": base_mac,
            "count":    int(self.count_spin.value()),
        }
        base_ip = self.base_ip_field.text().strip()
        if base_ip:
            body["base_ip"] = base_ip
        vtep = self.remote_vtep_field.text().strip()
        if vtep:
            body["remote_vtep_ip"] = vtep
        l3 = self.l3_iface_field.text().strip()
        if l3:
            body["l3_iface"] = l3
        return body

    # ──────────────────────────────────────────────────────── HTTP ops
    def _on_inject(self) -> None:
        if not self.server_url:
            QMessageBox.warning(self, "No server",
                                "No server URL configured for this dialog.")
            return
        body = self.build_inject_payload()
        if body is None:
            return
        try:
            r = requests.post(
                f"{self.server_url}/api/evpn/type2/inject",
                json=body, headers=self._auth_headers(), timeout=30,
            )
        except Exception as exc:
            self._set_status(f"Request failed: {exc}", ok=False)
            return
        if r.status_code != 200:
            # Surface the server's JSON error message if it sent one.
            try:
                err = (r.json() or {}).get("error") or r.text[:200]
            except Exception:
                err = r.text[:200] or f"(no body)"
            self._set_status(f"HTTP {r.status_code}: {err}", ok=False)
            return
        try:
            data = r.json() or {}
        except Exception as exc:
            self._set_status(f"Bad JSON in response: {exc}", ok=False)
            return
        ok_n   = int(data.get("ok_count", 0))
        fail_n = int(data.get("failed_count", 0))
        total  = int(data.get("count", 0))
        if fail_n == 0:
            self._set_status(
                f"Injected {total} entry/entries — {ok_n} kernel command(s) OK"
            )
        else:
            self._set_status(
                f"Injected {total} entry/entries with {fail_n} kernel error(s); "
                f"see /api/evpn/type2/inject response for details.",
                warn=True,
            )
        # Refresh the active-injections list so the new batch appears.
        self.refresh_active()

    def refresh_active(self) -> None:
        if not self.server_url:
            return
        try:
            r = requests.get(
                f"{self.server_url}/api/evpn/type2/list",
                headers=self._auth_headers(), timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[EVPN INJECT GUI] list failed: {exc}")
            return
        if r.status_code != 200:
            return
        try:
            data = r.json() or {}
        except Exception:
            return
        self._populate_active(data.get("injections") or [])

    def _populate_active(self, items: List[Dict[str, Any]]) -> None:
        self.active_table.setRowCount(len(items))
        for row, it in enumerate(items):
            inj_id = str(it.get("inject_id", ""))
            self.active_table.setItem(row, self.COL_ID,
                                      QTableWidgetItem(inj_id[:8] + "…"))
            self.active_table.item(row, self.COL_ID).setToolTip(inj_id)
            self.active_table.setItem(row, self.COL_IFACE,
                                      QTableWidgetItem(str(it.get("iface", ""))))
            self.active_table.setItem(row, self.COL_L3,
                                      QTableWidgetItem(str(it.get("l3_iface") or "—")))
            self.active_table.setItem(row, self.COL_VTEP,
                                      QTableWidgetItem(str(it.get("remote_vtep_ip") or "—")))
            ci = QTableWidgetItem(str(it.get("count", 0)))
            ci.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.active_table.setItem(row, self.COL_COUNT, ci)
            # Per-row Clear button — explicit local binding of inj_id
            # so the lambda doesn't capture the loop-variable.
            clear_btn = QPushButton("Clear")
            clear_btn.setStyleSheet(
                "QPushButton { color: #b91c1c; border: 1px solid #fca5a5; "
                "padding: 1px 8px; border-radius: 4px; } "
                "QPushButton:hover { background-color: #fef2f2; }"
            )
            clear_btn.clicked.connect(
                (lambda _checked=False, _iid=inj_id: self._clear_one(_iid))
            )
            self.active_table.setCellWidget(row, self.COL_CLEAR, clear_btn)

    def _clear_one(self, inject_id: str) -> None:
        if not self.server_url:
            return
        try:
            r = requests.post(
                f"{self.server_url}/api/evpn/type2/clear",
                json={"inject_id": inject_id},
                headers=self._auth_headers(), timeout=10,
            )
        except Exception as exc:
            self._set_status(f"Clear failed: {exc}", ok=False)
            return
        if r.status_code != 200:
            self._set_status(
                f"Clear failed: HTTP {r.status_code} {r.text[:120]}",
                ok=False,
            )
            return
        try:
            data = r.json() or {}
        except Exception:
            data = {}
        fail_n = int(data.get("failed_count", 0))
        if fail_n == 0:
            self._set_status(f"Cleared inject {inject_id[:8]}…")
        else:
            self._set_status(
                f"Cleared inject {inject_id[:8]}… with "
                f"{fail_n} kernel error(s) (entries may have been "
                f"already gone).", warn=True,
            )
        self.refresh_active()


def show_evpn_inject_dialog(parent: Optional[QWidget],
                            server_url: str,
                            default_iface: str = "") -> None:
    """Convenience launcher used by the VXLAN sub-tab's action bar."""
    dlg = EvpnInjectDialog(parent, server_url=server_url,
                           default_iface=default_iface)
    dlg.exec_()
