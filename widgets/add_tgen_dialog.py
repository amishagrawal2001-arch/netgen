"""Add TGEN Chassis dialog — Spirent-style history + connect form.

Replaces the legacy two-step QInputDialog flow (address, port) with a
persistent chassis manager:

  - Top: recent connections list — address, label, port, last-connected
    timestamp, reachable LED. Double-click to connect.
  - Middle: connection form (address / port / label / HTTPS / auth
    token) — pre-fills when a history row is selected.
  - Buttons: Connect, Test All, Remove, Cancel.

History lives at ~/.netgen/chassis_history.json (auto-created). Format:

  [
    {
      "address": "svl-hp-ai-srv04",
      "port": 5050,
      "label": "Lab 4 (Mellanox CX-7)",
      "scheme": "http",
      "auth_token": "",          # never persisted — kept blank
      "last_connected": "2026-05-16T20:14:31",
      "connect_count": 12
    },
    ...
  ]

Auth tokens are NOT persisted — re-entered per session. Everything
else is plain JSON so it survives client upgrades / reinstalls.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QAbstractItemView,
)


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".netgen")
HISTORY_FILE = os.path.join(HISTORY_DIR, "chassis_history.json")


def _load_history() -> list:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict) and e.get("address")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def _save_history(entries: list) -> None:
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        # Never persist auth tokens
        clean = []
        for e in entries:
            ec = dict(e)
            ec["auth_token"] = ""
            clean.append(ec)
        with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
    except OSError:
        pass


def record_connection(
    address: str,
    port: int,
    label: str = "",
    scheme: str = "http",
) -> None:
    """Bump or insert an entry after a successful connect."""
    entries = _load_history()
    now = datetime.now().isoformat(timespec="seconds")
    addr_norm = (address or "").strip().lower()
    for e in entries:
        if e.get("address", "").lower() == addr_norm and int(e.get("port", 0)) == int(port):
            e["last_connected"] = now
            e["connect_count"] = int(e.get("connect_count", 0)) + 1
            if label:
                e["label"] = label
            e["scheme"] = scheme
            break
    else:
        entries.insert(0, {
            "address": address,
            "port": port,
            "label": label,
            "scheme": scheme,
            "auth_token": "",
            "last_connected": now,
            "connect_count": 1,
        })
    # Cap to 50 entries; sort by last_connected desc
    entries.sort(key=lambda x: x.get("last_connected", ""), reverse=True)
    _save_history(entries[:50])


# ---------------------------------------------------------------------------
# Background reachability probe
# ---------------------------------------------------------------------------


class ReachabilityWorker(QThread):
    """Pings /api/health on a list of (row, full_url) tuples concurrently."""

    result = pyqtSignal(int, bool, str)  # (row, ok, detail)

    def __init__(self, targets: list):
        super().__init__()
        self.targets = list(targets)  # [(row_idx, full_url), ...]
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            import requests
        except Exception as e:
            for row, _ in self.targets:
                self.result.emit(row, False, f"requests missing: {e}")
            return
        for row, url in self.targets:
            if self._stop:
                return
            ok, detail = False, ""
            try:
                r = requests.get(f"{url}/api/health", timeout=3)
                ok = r.status_code == 200
                detail = f"HTTP {r.status_code}"
            except Exception as e:
                detail = str(e).split("\n", 1)[0][:80]
            self.result.emit(row, ok, detail)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class AddTGenDialog(QDialog):
    """Spirent-style chassis manager.

    Returns a list of selected connections in `chosen_connections`. The
    list is empty when the user only edited history without choosing
    to connect (e.g. clicked Add to History a few times then Close).
    Each entry: {url, label, auth_token, address, port, scheme}.

    Legacy single-target attributes (`chosen_url`, `chosen_label`,
    `chosen_auth`) are populated from the first entry of the list so
    callers from earlier in this branch keep working unchanged.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add TGEN Chassis")
        self.setMinimumSize(760, 600)

        self._entries: list = _load_history()
        self._worker: Optional[ReachabilityWorker] = None

        # Multi-target result list — one entry per chassis the user
        # wants to connect to. Empty when user closed without
        # connecting (e.g. just added to history).
        self.chosen_connections: list = []

        # Back-compat shims — first chosen_connections entry mirrored here
        self.chosen_url: Optional[str] = None
        self.chosen_label: str = ""
        self.chosen_auth: str = ""
        self.chosen_address: str = ""
        self.chosen_port: int = 5050
        self.chosen_scheme: str = "http"

        self._build_ui()
        self._populate_history_table()
        # Auto-probe reachability on open — operators expect to see live LEDs
        # without having to click anything.
        if self._entries:
            self._start_reachability_probe()

    # -- UI build ------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "Pick a chassis from your recent connections, or enter a new "
            "one below. Double-click a row to connect immediately."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # History table
        hist_box = QGroupBox("Recent connections")
        hb = QVBoxLayout(hist_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "●", "Address", "Port", "Label", "Last connected", "× connects",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # ExtendedSelection = Ctrl/Shift-click for multi-select. Lets the
        # operator pick 3-4 chassis from history and Connect them all in
        # one shot.
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 28)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        hb.addWidget(self.table)

        hist_btns = QHBoxLayout()
        self.connect_selected_btn = QPushButton("Connect Selected")
        self.connect_selected_btn.setToolTip(
            "Connect to every chassis selected in the table above. "
            "Ctrl/Shift-click to select multiple rows."
        )
        self.connect_selected_btn.clicked.connect(self._connect_selected_from_history)
        self.connect_selected_btn.setEnabled(False)
        self.remove_btn = QPushButton("Remove from history")
        self.remove_btn.clicked.connect(self._remove_selected_from_history)
        self.test_btn = QPushButton("Test all")
        self.test_btn.clicked.connect(self._start_reachability_probe)
        hist_btns.addWidget(self.connect_selected_btn)
        hist_btns.addWidget(self.remove_btn)
        hist_btns.addWidget(self.test_btn)
        hist_btns.addStretch(1)
        hb.addLayout(hist_btns)

        root.addWidget(hist_box, 1)

        # Form
        form_box = QGroupBox("Connect to")
        form = QFormLayout(form_box)

        self.address_in = QLineEdit()
        self.address_in.setPlaceholderText("svl-hp-ai-srv04  or  10.83.6.94")
        form.addRow("Address:", self.address_in)

        port_row = QHBoxLayout()
        self.port_in = QSpinBox()
        self.port_in.setRange(1, 65535)
        self.port_in.setValue(5050)
        port_row.addWidget(self.port_in)
        self.https_cb = QCheckBox("Use HTTPS")
        port_row.addWidget(self.https_cb)
        port_row.addStretch(1)
        form.addRow("Port:", port_row)

        self.label_in = QLineEdit()
        self.label_in.setPlaceholderText("(optional — \"Lab 4 Mellanox CX-7\")")
        form.addRow("Label:", self.label_in)

        self.auth_in = QLineEdit()
        self.auth_in.setEchoMode(QLineEdit.Password)
        self.auth_in.setPlaceholderText("(only if NETGEN_AUTH_TOKEN is set on server)")
        form.addRow("Auth token:", self.auth_in)

        self.status_lbl = QLabel(" ")
        self.status_lbl.setStyleSheet("color:#475569; font-size:11px;")
        form.addRow(self.status_lbl)

        root.addWidget(form_box)

        # Bottom buttons. Three actions, plus Close:
        #
        #   Add to History  — form contents → history, no connect, stays open
        #                     (build up a list of chassis without committing)
        #   Connect & Add   — form contents → history + connect to it, closes
        #                     (the common single-shot case)
        #   Close           — exits without connecting; any "Add to History"
        #                     entries already made are persisted
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        self.add_history_btn = QPushButton("Add to History")
        self.add_history_btn.setToolTip(
            "Save the chassis above to your history list without "
            "connecting. Useful for building up a list of known "
            "chassis before deciding which to connect to."
        )
        self.add_history_btn.clicked.connect(self._add_to_history_only)
        btns.addButton(self.add_history_btn, QDialogButtonBox.ActionRole)

        self.connect_btn = QPushButton("Connect && Add")
        self.connect_btn.setDefault(True)
        self.connect_btn.setToolTip(
            "Save to history AND connect to the chassis above. Closes "
            "the dialog. (For multiple chassis, use 'Connect Selected' "
            "in the table area above.)"
        )
        self.connect_btn.clicked.connect(self._do_connect)
        btns.addButton(self.connect_btn, QDialogButtonBox.AcceptRole)

        btns.rejected.connect(self._close_without_connecting)
        root.addWidget(btns)

        # Hook up table selection → enable/disable Connect Selected
        self.table.itemSelectionChanged.connect(self._update_connect_selected_btn)

    # -- History table -------------------------------------------------------

    def _populate_history_table(self) -> None:
        self.table.setRowCount(0)
        for i, e in enumerate(self._entries):
            self.table.insertRow(i)
            led = QTableWidgetItem("?")
            led.setTextAlignment(Qt.AlignCenter)
            led.setForeground(QColor("#9ca3af"))
            led.setToolTip("Reachability unknown — click Test all")
            self.table.setItem(i, 0, led)
            self.table.setItem(i, 1, QTableWidgetItem(e.get("address", "")))
            self.table.setItem(i, 2, QTableWidgetItem(str(e.get("port", 5050))))
            self.table.setItem(i, 3, QTableWidgetItem(e.get("label", "")))
            last = e.get("last_connected", "")
            self.table.setItem(i, 4, QTableWidgetItem(_pretty_age(last)))
            self.table.setItem(i, 5, QTableWidgetItem(str(e.get("connect_count", 0))))

    def _on_row_selected(self) -> None:
        # Auto-fill form from the first selected row. With multi-select
        # this isn't precisely "the" selected row, but it's a useful hint
        # that lets the operator tweak the form (e.g. change port) before
        # clicking Add to History.
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        if i < 0 or i >= len(self._entries):
            return
        e = self._entries[i]
        self.address_in.setText(e.get("address", ""))
        try:
            self.port_in.setValue(int(e.get("port", 5050)))
        except (TypeError, ValueError):
            self.port_in.setValue(5050)
        self.label_in.setText(e.get("label", ""))
        self.https_cb.setChecked(e.get("scheme", "http") == "https")
        # Don't restore auth token — never persisted.

    def _update_connect_selected_btn(self) -> None:
        count = len(self.table.selectionModel().selectedRows())
        self.connect_selected_btn.setEnabled(count > 0)
        if count <= 1:
            self.connect_selected_btn.setText("Connect Selected")
        else:
            self.connect_selected_btn.setText(f"Connect {count} Selected")

    def _on_row_double_clicked(self, _item) -> None:
        # Same effect as clicking Connect with the selected row pre-filled.
        # Always treats double-click as a single-chassis connect — operator
        # used the "Connect Selected" button explicitly if they meant the
        # multi-select case.
        self._on_row_selected()
        self._do_connect()

    def _remove_selected_from_history(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        if i < 0 or i >= len(self._entries):
            return
        e = self._entries[i]
        ret = QMessageBox.question(
            self, "Remove from history",
            f"Remove {e.get('address')}:{e.get('port')} from history?\n"
            "This does not affect connected servers.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        del self._entries[i]
        _save_history(self._entries)
        self._populate_history_table()

    # -- Reachability probe --------------------------------------------------

    def _start_reachability_probe(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        targets = []
        for i, e in enumerate(self._entries):
            scheme = e.get("scheme", "http")
            targets.append((i, f"{scheme}://{e.get('address')}:{int(e.get('port', 5050))}"))
            led = self.table.item(i, 0)
            if led:
                led.setText("…")
                led.setForeground(QColor("#9ca3af"))
                led.setToolTip("Probing…")
        if not targets:
            return
        self.test_btn.setEnabled(False)
        self.status_lbl.setText(f"Probing {len(targets)} chassis...")
        self._worker = ReachabilityWorker(targets)
        self._worker.result.connect(self._on_probe_result)
        self._worker.finished.connect(self._on_probe_finished)
        self._worker.start()

    def _on_probe_result(self, row: int, ok: bool, detail: str) -> None:
        led = self.table.item(row, 0)
        if not led:
            return
        if ok:
            led.setText("✓")
            led.setForeground(QColor("#15803d"))
            led.setToolTip(f"Online — {detail}")
        else:
            led.setText("✗")
            led.setForeground(QColor("#dc2626"))
            led.setToolTip(f"Unreachable — {detail}")

    def _on_probe_finished(self) -> None:
        self.test_btn.setEnabled(True)
        self.status_lbl.setText(" ")

    # -- Form-side actions ---------------------------------------------------

    def _parse_form(self) -> Optional[dict]:
        """Validate + normalize the connection form. Returns a dict or None."""
        address = (self.address_in.text() or "").strip()
        if not address:
            QMessageBox.warning(self, "Missing", "Enter a chassis address.")
            return None
        # Strip scheme if user pasted one
        address = re.sub(r"^https?://", "", address, flags=re.I)
        address = address.rstrip("/")
        # Allow user-pasted "host:port" form
        if ":" in address and not address.endswith("]"):
            host, _, port_str = address.rpartition(":")
            try:
                self.port_in.setValue(int(port_str))
                address = host
            except ValueError:
                pass

        port = int(self.port_in.value())
        scheme = "https" if self.https_cb.isChecked() else "http"
        label = (self.label_in.text() or "").strip()
        auth = (self.auth_in.text() or "").strip()
        return {
            "url": f"{scheme}://{address}:{port}",
            "address": address,
            "port": port,
            "scheme": scheme,
            "label": label,
            "auth_token": auth,
        }

    def _add_to_history_only(self) -> None:
        """Save form contents to history but don't connect. Stays open."""
        entry = self._parse_form()
        if entry is None:
            return
        record_connection(
            entry["address"], entry["port"],
            label=entry["label"], scheme=entry["scheme"],
        )
        # Reload from disk so the table reflects the new entry
        self._entries = _load_history()
        self._populate_history_table()
        self.status_lbl.setText(
            f"Added {entry['address']}:{entry['port']} to history."
        )
        # Clear the form so the operator can type another one without
        # accidentally re-adding the same chassis
        self.address_in.clear()
        self.label_in.clear()
        self.auth_in.clear()
        self.address_in.setFocus()
        # Re-probe so the new entry gets a LED
        self._start_reachability_probe()

    def _do_connect(self) -> None:
        """Form contents → history + connect to it. Closes the dialog."""
        entry = self._parse_form()
        if entry is None:
            return
        record_connection(
            entry["address"], entry["port"],
            label=entry["label"], scheme=entry["scheme"],
        )
        self._set_results([entry])
        self.accept()

    def _connect_selected_from_history(self) -> None:
        """Connect to every row currently selected in the history table."""
        rows = sorted(r.row() for r in self.table.selectionModel().selectedRows())
        if not rows:
            QMessageBox.warning(
                self, "No selection",
                "Select one or more rows in the table first."
            )
            return
        chosen = []
        for i in rows:
            if 0 <= i < len(self._entries):
                e = self._entries[i]
                chosen.append({
                    "url": f"{e.get('scheme', 'http')}://{e.get('address')}:{int(e.get('port', 5050))}",
                    "address": e.get("address", ""),
                    "port": int(e.get("port", 5050)),
                    "scheme": e.get("scheme", "http"),
                    "label": e.get("label", ""),
                    # Auth tokens aren't persisted — operator either enters
                    # one in the form (and uses Connect & Add) or leaves
                    # bulk-connected chassis token-less.
                    "auth_token": "",
                })
                record_connection(
                    e.get("address", ""), int(e.get("port", 5050)),
                    label=e.get("label", ""), scheme=e.get("scheme", "http"),
                )
        self._set_results(chosen)
        self.accept()

    def _close_without_connecting(self) -> None:
        """Cancel / Close button — exit with empty result."""
        self.chosen_connections = []
        self.reject()

    def _set_results(self, entries: list) -> None:
        """Populate result attributes from a list of parsed entries."""
        self.chosen_connections = list(entries)
        if entries:
            first = entries[0]
            self.chosen_url = first["url"]
            self.chosen_label = first.get("label", "")
            self.chosen_auth = first.get("auth_token", "")
            self.chosen_address = first.get("address", "")
            self.chosen_port = int(first.get("port", 5050))
            self.chosen_scheme = first.get("scheme", "http")

    # -- Cleanup -------------------------------------------------------------

    def closeEvent(self, e):
        if self._worker and self._worker.isRunning():
            try:
                self._worker.stop()
                self._worker.wait(1500)
            except Exception:
                pass
        super().closeEvent(e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pretty_age(iso: str) -> str:
    """Turn an ISO timestamp into '2 min ago' / '3 days ago' / etc."""
    if not iso:
        return "—"
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso[:19]
    delta = datetime.now() - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{days // 365}y ago"
