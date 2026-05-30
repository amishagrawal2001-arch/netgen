"""GUI for EVPN Type-2 / Type-5 bulk injection.

v0.2.63 introduced this dialog for Type-2 (MAC/IP) injection. v0.2.67
adds a tab selector and a Type-5 (IP Prefix) form alongside; the
shared Active-injections table now carries a Kind column and the
per-row Clear button routes to the right `/api/evpn/{kind}/clear`
endpoint.

Structure:
  * QTabWidget at the top — "Type-2 (MAC/IP)" + "Type-5 (IP Prefix)";
    each tab is a form with its own Inject button.
  * Shared status row below the tabs (single status_label).
  * Active-injections group: refresh + table with per-row kind-aware
    Clear.

Test contract: every public attribute / method that existed in v0.2.63
(`iface_field`, `base_mac_field`, `count_spin`, `base_ip_field`,
`remote_vtep_field`, `l3_iface_field`, `inject_btn`, `status_label`,
`build_inject_payload`, `_on_inject`, `refresh_active`, `_populate_active`,
`_clear_one`, `refresh_btn`, `active_table`, plus the COL_* constants)
is preserved — only their parent layout changed.
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
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


class EvpnInjectDialog(QDialog):
    """Standalone dialog for EVPN Type-2 / Type-5 bulk inject + clear."""

    # Columns shifted in 0.2.67 to add "kind" between inject_id and iface.
    COLUMNS = ["inject_id", "kind", "iface", "L3 iface",
               "remote VTEP", "count", ""]
    COL_ID, COL_KIND, COL_IFACE, COL_L3, COL_VTEP, COL_COUNT, COL_CLEAR = range(7)

    # Pretty per-kind colours for the Kind cell.
    _KIND_COLORS = {
        "type2": "#2563eb",   # blue
        "type5": "#7c3aed",   # violet
    }

    def __init__(self, parent: Optional[QWidget] = None, *,
                 server_url: str = "", default_iface: str = ""):
        super().__init__(parent)
        self.setWindowTitle("EVPN Bulk Inject (Type-2 / Type-5)")
        self.setMinimumWidth(680)
        self.server_url = (server_url or "").rstrip("/")
        # Cache of the kind for each known inject_id, populated by
        # _populate_active. Lets _clear_one route to the correct
        # /api/evpn/{kind}/clear endpoint without the caller having
        # to thread `kind` through.
        self._row_kinds: Dict[str, str] = {}
        self._build_ui(default_iface)
        self.refresh_active()

    # ────────────────────────────────────────────────────────────── UI
    def _build_ui(self, default_iface: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # Tab selector for the two inject kinds.
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_type2_panel(default_iface),
                         "Type-2 (MAC/IP)")
        self.tabs.addTab(self._build_type5_panel(),
                         "Type-5 (IP Prefix)")
        outer.addWidget(self.tabs)

        # Shared status row — single label both Inject buttons write to.
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

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

    def _build_type2_panel(self, default_iface: str) -> QWidget:
        """Form for the Type-2 (MAC/IP) inject. Same fields as v0.2.63
        — attribute names preserved so existing tests keep working."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 8, 0, 0)

        form = QFormLayout()
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
        page_layout.addLayout(form)

        action_row = QHBoxLayout()
        self.inject_btn = QPushButton("Inject")
        self.inject_btn.setCursor(Qt.PointingHandCursor)
        self.inject_btn.setStyleSheet(_GREEN_BTN_CSS)
        self.inject_btn.clicked.connect(self._on_inject)
        action_row.addWidget(self.inject_btn)
        action_row.addStretch(1)
        page_layout.addLayout(action_row)
        page_layout.addStretch(1)
        return page

    def _build_type5_panel(self) -> QWidget:
        """Form for the Type-5 (IP Prefix) inject — new in 0.2.67.

        Mirrors `/api/evpn/type5/inject`'s body shape: dev, base_prefix,
        prefix_len, count, optional gateway, optional vrf_table.
        """
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 8, 0, 0)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.dev_field = QLineEdit("eth0")
        self.dev_field.setToolTip(
            "Egress interface for the synthetic routes (e.g. the VRF's "
            "uplink). `ip route add … dev <dev>`."
        )

        self.base_prefix_field = QLineEdit("10.100.0.0")
        self.base_prefix_field.setToolTip(
            "First IPv4 prefix. Must be aligned to the chosen prefix_len "
            "boundary (e.g. 10.100.0.0/24, not 10.100.0.5/24)."
        )

        self.prefix_len_spin = QSpinBox()
        self.prefix_len_spin.setRange(1, 32)
        self.prefix_len_spin.setValue(24)
        self.prefix_len_spin.setToolTip(
            "IPv4 prefix length. Successive prefixes step by 2^(32-len) "
            "addresses, so /24 walks 10.100.0.0, 10.100.1.0, …"
        )

        # Distinct attribute name (count_t5_spin) so we don't shadow the
        # Type-2 count_spin the existing tests reference.
        self.count_t5_spin = QSpinBox()
        self.count_t5_spin.setRange(1, 1_000_000)
        self.count_t5_spin.setValue(100)
        self.count_t5_spin.setToolTip(
            "Number of consecutive prefixes to generate. 100 /24s → "
            "10.100.0.0/24 through 10.100.99.0/24."
        )

        self.gateway_field = QLineEdit("")
        self.gateway_field.setPlaceholderText(
            "(optional next-hop — appended as `via <gateway>`)"
        )
        self.gateway_field.setToolTip(
            "When set, every route uses `via <gateway>` instead of being "
            "directly-attached. Leave blank when the prefixes sit on `dev`."
        )

        self.vrf_table_spin = QSpinBox()
        # Kernel table id is u32 but real-world FRR VRFs never get
        # close to 2^31; cap well below QSpinBox's signed-32-bit limit.
        self.vrf_table_spin.setRange(0, 999_999_999)
        self.vrf_table_spin.setValue(0)
        self.vrf_table_spin.setSpecialValueText("main")
        self.vrf_table_spin.setToolTip(
            "Kernel routing-table id for the FRR VRF. 0 → the main table "
            "(no `table` clause). Must match what FRR's VRF is bound to."
        )

        form.addRow("Egress dev:",   self.dev_field)
        form.addRow("Base prefix:",  self.base_prefix_field)
        form.addRow("Prefix length:", self.prefix_len_spin)
        form.addRow("Count:",        self.count_t5_spin)
        form.addRow("Gateway:",      self.gateway_field)
        form.addRow("VRF table id:", self.vrf_table_spin)
        page_layout.addLayout(form)

        action_row = QHBoxLayout()
        self.inject_btn_t5 = QPushButton("Inject")
        self.inject_btn_t5.setCursor(Qt.PointingHandCursor)
        self.inject_btn_t5.setStyleSheet(_GREEN_BTN_CSS)
        self.inject_btn_t5.clicked.connect(self._on_inject_t5)
        action_row.addWidget(self.inject_btn_t5)
        action_row.addStretch(1)
        page_layout.addLayout(action_row)
        page_layout.addStretch(1)
        return page

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

    # ────────────────────────────────────────── Type-2 form → payload
    def build_inject_payload(self) -> Optional[Dict[str, Any]]:
        """Type-2 form → /api/evpn/type2/inject body, or None on validation
        failure. Name kept for v0.2.63 test compatibility."""
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

    # ────────────────────────────────────────── Type-5 form → payload
    def build_inject_payload_t5(self) -> Optional[Dict[str, Any]]:
        """Type-5 form → /api/evpn/type5/inject body, or None on validation
        failure."""
        dev = self.dev_field.text().strip()
        base_prefix = self.base_prefix_field.text().strip()
        if not dev or not base_prefix:
            QMessageBox.warning(
                self, "Missing field",
                "Egress dev and Base prefix are required."
            )
            return None
        body: Dict[str, Any] = {
            "dev":         dev,
            "base_prefix": base_prefix,
            "prefix_len":  int(self.prefix_len_spin.value()),
            "count":       int(self.count_t5_spin.value()),
        }
        gw = self.gateway_field.text().strip()
        if gw:
            body["gateway"] = gw
        vrf = int(self.vrf_table_spin.value())
        if vrf > 0:   # 0 means "main", which we encode as omitting the field
            body["vrf_table"] = vrf
        return body

    # ──────────────────────────────────────────────────────── HTTP ops
    def _on_inject(self) -> None:
        """Type-2 inject — kept as the original v0.2.63 entry point."""
        self._do_inject(
            kind="type2",
            path="/api/evpn/type2/inject",
            body_builder=self.build_inject_payload,
            success_label_fn=lambda total, ok_n: (
                f"Injected {total} Type-2 entry/entries — "
                f"{ok_n} kernel command(s) OK"
            ),
            partial_label_fn=lambda total, fail_n: (
                f"Injected {total} Type-2 entry/entries with "
                f"{fail_n} kernel error(s); see /api/evpn/type2/inject "
                f"response for details."
            ),
        )

    def _on_inject_t5(self) -> None:
        """Type-5 inject — counterpart of _on_inject for the new tab."""
        self._do_inject(
            kind="type5",
            path="/api/evpn/type5/inject",
            body_builder=self.build_inject_payload_t5,
            success_label_fn=lambda total, ok_n: (
                f"Injected {total} Type-5 prefix(es) — "
                f"{ok_n} kernel route(s) OK"
            ),
            partial_label_fn=lambda total, fail_n: (
                f"Injected {total} Type-5 prefix(es) with "
                f"{fail_n} kernel error(s); see /api/evpn/type5/inject "
                f"response for details."
            ),
        )

    def _do_inject(self, *, kind: str, path: str, body_builder,
                   success_label_fn, partial_label_fn) -> None:
        """Shared inject machinery for Type-2 + Type-5. Centralises the
        HTTP try/except / status-colour map so both kinds report errors
        identically."""
        if not self.server_url:
            QMessageBox.warning(self, "No server",
                                "No server URL configured for this dialog.")
            return
        body = body_builder()
        if body is None:
            return
        try:
            r = requests.post(
                f"{self.server_url}{path}",
                json=body, headers=self._auth_headers(), timeout=30,
            )
        except Exception as exc:
            self._set_status(f"Request failed: {exc}", ok=False)
            return
        if r.status_code != 200:
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
            self._set_status(success_label_fn(total, ok_n))
        else:
            self._set_status(partial_label_fn(total, fail_n), warn=True)
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
        self._row_kinds.clear()
        self.active_table.setRowCount(len(items))
        for row, it in enumerate(items):
            inj_id = str(it.get("inject_id", ""))
            kind = str(it.get("kind") or "type2")
            self._row_kinds[inj_id] = kind

            self.active_table.setItem(row, self.COL_ID,
                                      QTableWidgetItem(inj_id[:8] + "…"))
            self.active_table.item(row, self.COL_ID).setToolTip(inj_id)

            # Pretty kind badge — "type-2" / "type-5" with a colour.
            kind_label = "Type-2" if kind == "type2" else "Type-5"
            ki = QTableWidgetItem(kind_label)
            ki.setTextAlignment(Qt.AlignCenter)
            from PyQt5.QtGui import QColor
            ki.setForeground(QColor(self._KIND_COLORS.get(kind, "#475569")))
            self.active_table.setItem(row, self.COL_KIND, ki)

            self.active_table.setItem(row, self.COL_IFACE,
                                      QTableWidgetItem(str(it.get("iface", ""))))
            self.active_table.setItem(row, self.COL_L3,
                                      QTableWidgetItem(str(it.get("l3_iface") or "—")))
            self.active_table.setItem(row, self.COL_VTEP,
                                      QTableWidgetItem(str(it.get("remote_vtep_ip") or "—")))
            ci = QTableWidgetItem(str(it.get("count", 0)))
            ci.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.active_table.setItem(row, self.COL_COUNT, ci)

            # Per-row Clear — explicit default-arg lambda so each button
            # captures its OWN inject_id (regression-locked since v0.2.63).
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

    def _clear_one(self, inject_id: str, kind: Optional[str] = None) -> None:
        """Route the Clear POST to the right /api/evpn/{kind}/clear.

        `kind` defaults to None → look up from the per-row cache populated
        by `_populate_active`, falling back to "type2" so the v0.2.63
        test (`test_clear_one_posts_inject_id`) — which calls this with
        no cache populated — still hits the Type-2 endpoint as before.
        """
        if not self.server_url:
            return
        if kind is None:
            kind = self._row_kinds.get(inject_id, "type2")
        path = f"/api/evpn/{kind}/clear"
        try:
            r = requests.post(
                f"{self.server_url}{path}",
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


# Shared green-button stylesheet — used by both Inject buttons so they
# read as the primary action on each tab.
_GREEN_BTN_CSS = (
    "QPushButton { background-color: #16a34a; color: white; "
    "font-weight: 600; padding: 5px 18px; border-radius: 4px; } "
    "QPushButton:hover { background-color: #15803d; }"
)


def show_evpn_inject_dialog(parent: Optional[QWidget],
                            server_url: str,
                            default_iface: str = "") -> None:
    """Convenience launcher used by the VXLAN sub-tab's action bar."""
    dlg = EvpnInjectDialog(parent, server_url=server_url,
                           default_iface=default_iface)
    dlg.exec_()
