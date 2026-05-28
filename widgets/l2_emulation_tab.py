"""L2 Emulation tab — GUI surface for the L2 / multicast frame generators.

Mirrors what the CLI gets via `netgen-cli l2 start-*`: pick a protocol,
fill in a small form, click Start. A live session table below shows
every running emitter with its counter — frames sent, bytes sent, last
error, uptime.

Protocols today:
  * LACP (802.1AX)
  * LLDP (802.1AB)
  * VRRP v2 / v3 (RFC 3768 / 5798)
  * IGMP v2 / v3 (RFC 2236 / 3376)
  * PIM Hello (RFC 7761 §4.3)

Backend: `server/l2_routes.py` → `utils/l2_protocols.py`. The tab is
a thin REST client — no scapy import here, no business logic.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QHeaderView, QGroupBox, QDoubleSpinBox,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- fetch worker


class _JsonFetchWorker(QThread):
    """Same pattern as topology_tab's worker — keeps the GUI thread
    alive while /api/l2/sessions resolves.

    Emits `failed(short_msg, http_code)` so the consumer can react
    differently to 404 (server too old) vs 401 (auth failed) vs a
    raw connection error. We deliberately don't pass the response
    body through — Flask's default 404 HTML page would land in the
    GUI info label otherwise.
    """

    finished_ok = pyqtSignal(object, int)
    failed = pyqtSignal(str, int)   # short_msg, http_code (0 if no response)

    def __init__(self, url: str, timeout_s: float = 5.0):
        super().__init__()
        self._url = url
        self._timeout = timeout_s

    def run(self):
        token = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            r = requests.get(self._url, headers=headers, timeout=self._timeout)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}", 0)
            return
        if r.status_code != 200:
            self.failed.emit(f"HTTP {r.status_code}", r.status_code)
            return
        try:
            self.finished_ok.emit(r.json(), r.status_code)
        except Exception as exc:
            self.failed.emit(f"bad JSON: {exc}", r.status_code)


# ====================================================================
# Per-protocol config dialog
# ====================================================================


class _L2ConfigDialog(QDialog):
    """One dialog, multiple protocol-specific field sets via
    QStackedWidget. Pick a protocol → the matching form shows; the
    common fields (iface, duration_s) stay always-visible at the top.

    Returns the start-body dict via `accepted_payload()` after exec_().
    """

    PROTOCOLS = [
        ("lacp", "LACP — Slow Protocols LAG partner"),
        ("lldp", "LLDP — Neighbour discovery advertiser"),
        ("vrrp", "VRRP — First-hop redundancy"),
        ("igmp", "IGMP — Multicast group reports"),
        ("pim",  "PIM Hello — Multicast adjacency"),
    ]

    def __init__(self, parent=None, default_iface: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Start L2 emulation session")
        self.setMinimumWidth(540)
        self._payload: Optional[Dict[str, Any]] = None

        outer = QVBoxLayout(self)

        # Protocol picker
        top_form = QFormLayout()
        self._proto_combo = QComboBox()
        for key, label in self.PROTOCOLS:
            self._proto_combo.addItem(label, key)
        self._proto_combo.currentIndexChanged.connect(self._on_protocol_changed)
        top_form.addRow("Protocol:", self._proto_combo)

        self._iface_input = QLineEdit(default_iface or "eth0")
        self._iface_input.setPlaceholderText(
            "Network interface (eth0, ens1, …) — requires CAP_NET_RAW / root"
        )
        top_form.addRow("Interface:", self._iface_input)

        self._duration_spin = QDoubleSpinBox()
        self._duration_spin.setRange(0.0, 86400.0)
        self._duration_spin.setSpecialValueText("forever")
        self._duration_spin.setSuffix(" s")
        self._duration_spin.setValue(0.0)   # 0 = forever
        top_form.addRow("Duration:", self._duration_spin)

        outer.addLayout(top_form)

        # Per-protocol stack
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_lacp_panel())
        self._stack.addWidget(self._build_lldp_panel())
        self._stack.addWidget(self._build_vrrp_panel())
        self._stack.addWidget(self._build_igmp_panel())
        self._stack.addWidget(self._build_pim_panel())
        outer.addWidget(self._stack, 1)

        # Hint footer
        hint = QLabel(
            "After clicking Start, watch the session table for the "
            "'last_error' column — root/CAP_NET_RAW failures surface there."
        )
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Start")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    def _on_protocol_changed(self, idx: int):
        self._stack.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    def _build_lacp_panel(self) -> QWidget:
        w = QGroupBox("LACP parameters")
        f = QFormLayout(w)
        self._lacp_system_mac = QLineEdit("00:11:22:33:44:01")
        self._lacp_system_priority = QSpinBox()
        self._lacp_system_priority.setRange(0, 65535)
        self._lacp_system_priority.setValue(32768)
        self._lacp_key = QSpinBox()
        self._lacp_key.setRange(0, 65535)
        self._lacp_key.setValue(1)
        self._lacp_port_priority = QSpinBox()
        self._lacp_port_priority.setRange(0, 65535)
        self._lacp_port_priority.setValue(32768)
        self._lacp_port_number = QSpinBox()
        self._lacp_port_number.setRange(0, 65535)
        self._lacp_port_number.setValue(1)
        self._lacp_state = QSpinBox()
        self._lacp_state.setRange(0, 255)
        self._lacp_state.setValue(0x05)
        self._lacp_state.setToolTip(
            "Bits: 0x01=Activity, 0x02=Timeout, 0x04=Aggregation, "
            "0x08=Sync, 0x10=Collecting, 0x20=Distributing"
        )
        self._lacp_fast = QCheckBox("Fast (1s) cadence — default is 30s")

        f.addRow("System MAC:", self._lacp_system_mac)
        f.addRow("System Priority:", self._lacp_system_priority)
        f.addRow("Key:", self._lacp_key)
        f.addRow("Port Priority:", self._lacp_port_priority)
        f.addRow("Port Number:", self._lacp_port_number)
        f.addRow("State (bits):", self._lacp_state)
        f.addRow("", self._lacp_fast)
        return w

    def _build_lldp_panel(self) -> QWidget:
        w = QGroupBox("LLDP parameters")
        f = QFormLayout(w)
        self._lldp_chassis_id = QLineEdit("netgen-host")
        self._lldp_port_id = QLineEdit("eth0")
        self._lldp_system_name = QLineEdit("netgen")
        self._lldp_system_description = QLineEdit("Netgen L2 emulator")
        self._lldp_ttl = QSpinBox()
        self._lldp_ttl.setRange(0, 65535)
        self._lldp_ttl.setValue(120)
        self._lldp_interval = QDoubleSpinBox()
        self._lldp_interval.setRange(1.0, 3600.0)
        self._lldp_interval.setValue(30.0)
        self._lldp_interval.setSuffix(" s")
        self._lldp_src_mac = QLineEdit("00:11:22:33:44:02")

        f.addRow("Chassis ID:", self._lldp_chassis_id)
        f.addRow("Port ID:", self._lldp_port_id)
        f.addRow("System Name:", self._lldp_system_name)
        f.addRow("System Description:", self._lldp_system_description)
        f.addRow("TTL:", self._lldp_ttl)
        f.addRow("Interval:", self._lldp_interval)
        f.addRow("Source MAC:", self._lldp_src_mac)
        return w

    def _build_vrrp_panel(self) -> QWidget:
        w = QGroupBox("VRRP parameters")
        f = QFormLayout(w)
        self._vrrp_version = QComboBox()
        self._vrrp_version.addItem("v3", 3)
        self._vrrp_version.addItem("v2 (IPv4 only)", 2)
        self._vrrp_family = QComboBox()
        self._vrrp_family.addItem("IPv4", "ipv4")
        self._vrrp_family.addItem("IPv6 (v3 only)", "ipv6")
        self._vrrp_vrid = QSpinBox()
        self._vrrp_vrid.setRange(1, 255)
        self._vrrp_vrid.setValue(1)
        self._vrrp_priority = QSpinBox()
        self._vrrp_priority.setRange(1, 255)
        self._vrrp_priority.setValue(100)
        self._vrrp_priority.setToolTip(
            "1-254: non-owner; 255 = owner of the virtual IP (highest preempt)"
        )
        self._vrrp_virtual_ips = QLineEdit("192.168.1.254")
        self._vrrp_virtual_ips.setPlaceholderText(
            "Comma-separated virtual IPs (e.g. 192.168.1.254,192.168.1.253)"
        )
        self._vrrp_src_ip = QLineEdit("10.0.0.1")
        self._vrrp_src_mac = QLineEdit("00:11:22:33:44:03")
        self._vrrp_interval = QDoubleSpinBox()
        self._vrrp_interval.setRange(0.1, 60.0)
        self._vrrp_interval.setValue(1.0)
        self._vrrp_interval.setSuffix(" s")

        f.addRow("Version:", self._vrrp_version)
        f.addRow("Family:", self._vrrp_family)
        f.addRow("VRID:", self._vrrp_vrid)
        f.addRow("Priority:", self._vrrp_priority)
        f.addRow("Virtual IPs:", self._vrrp_virtual_ips)
        f.addRow("Source IP:", self._vrrp_src_ip)
        f.addRow("Source MAC:", self._vrrp_src_mac)
        f.addRow("Advertisement interval:", self._vrrp_interval)
        return w

    def _build_igmp_panel(self) -> QWidget:
        w = QGroupBox("IGMP parameters")
        f = QFormLayout(w)
        self._igmp_version = QComboBox()
        self._igmp_version.addItem("v2 (RFC 2236)", 2)
        self._igmp_version.addItem("v3 (RFC 3376)", 3)
        self._igmp_group = QLineEdit("239.1.1.1")
        self._igmp_type_code = QLineEdit("")
        self._igmp_type_code.setPlaceholderText(
            "(default: 0x16 Report for v2, 0x22 for v3 — override to 0x17 for Leave, 0x11 for Query)"
        )
        self._igmp_src_ip = QLineEdit("10.0.0.10")
        self._igmp_src_mac = QLineEdit("00:11:22:33:44:04")
        self._igmp_interval = QDoubleSpinBox()
        self._igmp_interval.setRange(1.0, 3600.0)
        self._igmp_interval.setValue(60.0)
        self._igmp_interval.setSuffix(" s")

        f.addRow("Version:", self._igmp_version)
        f.addRow("Group:", self._igmp_group)
        f.addRow("Type code (override):", self._igmp_type_code)
        f.addRow("Source IP:", self._igmp_src_ip)
        f.addRow("Source MAC:", self._igmp_src_mac)
        f.addRow("Interval:", self._igmp_interval)
        return w

    def _build_pim_panel(self) -> QWidget:
        w = QGroupBox("PIM Hello parameters")
        f = QFormLayout(w)
        self._pim_hold_time = QSpinBox()
        self._pim_hold_time.setRange(0, 65535)
        self._pim_hold_time.setValue(105)
        self._pim_hold_time.setToolTip("Recommended: 3.5 × hello interval")
        self._pim_dr_priority = QSpinBox()
        self._pim_dr_priority.setRange(0, 2_000_000_000)
        self._pim_dr_priority.setValue(1)
        self._pim_generation_id = QLineEdit("0xABCDEF01")
        self._pim_generation_id.setPlaceholderText("Hex (0xABCDEF01) or decimal")
        self._pim_src_ip = QLineEdit("10.0.0.20")
        self._pim_src_mac = QLineEdit("00:11:22:33:44:05")
        self._pim_interval = QDoubleSpinBox()
        self._pim_interval.setRange(1.0, 3600.0)
        self._pim_interval.setValue(30.0)
        self._pim_interval.setSuffix(" s")

        f.addRow("Hold time:", self._pim_hold_time)
        f.addRow("DR priority:", self._pim_dr_priority)
        f.addRow("Generation ID:", self._pim_generation_id)
        f.addRow("Source IP:", self._pim_src_ip)
        f.addRow("Source MAC:", self._pim_src_mac)
        f.addRow("Interval:", self._pim_interval)
        return w

    # ------------------------------------------------------------------
    def _on_accept(self):
        proto = self._proto_combo.currentData()
        iface = self._iface_input.text().strip()
        if not iface:
            QMessageBox.warning(self, "Missing field", "Interface is required.")
            return

        body: Dict[str, Any] = {"iface": iface}
        duration = self._duration_spin.value()
        if duration > 0:
            body["duration_s"] = duration

        if proto == "lacp":
            body.update({
                "system_mac": self._lacp_system_mac.text().strip(),
                "system_priority": self._lacp_system_priority.value(),
                "key": self._lacp_key.value(),
                "port_priority": self._lacp_port_priority.value(),
                "port_number": self._lacp_port_number.value(),
                "state": self._lacp_state.value(),
                "fast": self._lacp_fast.isChecked(),
            })
        elif proto == "lldp":
            body.update({
                "chassis_id": self._lldp_chassis_id.text().strip(),
                "port_id": self._lldp_port_id.text().strip(),
                "system_name": self._lldp_system_name.text().strip(),
                "system_description": self._lldp_system_description.text().strip(),
                "ttl_s": self._lldp_ttl.value(),
                "interval_s": self._lldp_interval.value(),
                "src_mac": self._lldp_src_mac.text().strip(),
            })
        elif proto == "vrrp":
            vips = [
                v.strip() for v in self._vrrp_virtual_ips.text().split(",")
                if v.strip()
            ]
            if not vips:
                QMessageBox.warning(
                    self, "Missing field",
                    "At least one virtual IP is required."
                )
                return
            body.update({
                "version": self._vrrp_version.currentData(),
                "family": self._vrrp_family.currentData(),
                "vrid": self._vrrp_vrid.value(),
                "priority": self._vrrp_priority.value(),
                "virtual_ips": vips,
                "src_ip": self._vrrp_src_ip.text().strip(),
                "src_mac": self._vrrp_src_mac.text().strip(),
                "interval_s": self._vrrp_interval.value(),
            })
        elif proto == "igmp":
            body.update({
                "version": self._igmp_version.currentData(),
                "group": self._igmp_group.text().strip(),
                "src_ip": self._igmp_src_ip.text().strip(),
                "src_mac": self._igmp_src_mac.text().strip(),
                "interval_s": self._igmp_interval.value(),
            })
            tc = self._igmp_type_code.text().strip()
            if tc:
                try:
                    body["type_code"] = int(tc, 0)
                except ValueError:
                    QMessageBox.warning(
                        self, "Invalid input",
                        f"Type code {tc!r} is not a valid integer."
                    )
                    return
        elif proto == "pim":
            try:
                gen_id = int(self._pim_generation_id.text().strip(), 0)
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid input",
                    f"Generation ID must be hex (0x...) or decimal."
                )
                return
            body.update({
                "hold_time": self._pim_hold_time.value(),
                "dr_priority": self._pim_dr_priority.value(),
                "generation_id": gen_id,
                "src_ip": self._pim_src_ip.text().strip(),
                "src_mac": self._pim_src_mac.text().strip(),
                "interval_s": self._pim_interval.value(),
            })

        self._payload = {"protocol": proto, "body": body}
        self.accept()

    def accepted_payload(self) -> Optional[Dict[str, Any]]:
        return self._payload


# ====================================================================
# Main tab
# ====================================================================


class L2EmulationTab(QWidget):
    """L2 frame generator + multicast session manager.

    Exposes the `/api/l2/*` REST surface as a GUI: pick a protocol →
    configure → Start; live session table below shows every running
    emitter and lets you Stop them. Refresh runs on a 3-second timer.
    """

    POLL_INTERVAL_MS = 3000
    COLUMNS = [
        "Session ID", "Protocol", "Iface", "Running",
        "Frames Sent", "Bytes Sent", "Last Error", "Uptime (s)",
    ]

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self._parent_window = parent_window
        self._fetch_worker: Optional[QThread] = None
        self._sessions_cache: List[Dict[str, Any]] = []
        # Flipped to True when the server returns 404 for /api/l2/*.
        # Slows the poll timer and intercepts Start clicks. Flipped
        # back automatically when the server starts responding again.
        self._unsupported: bool = False
        self._unsupported_reason: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()

        self._start_btn = QPushButton("Start emulation…")
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "font-weight: 600; padding: 5px 14px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        toolbar.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop selected")
        self._stop_btn.clicked.connect(self._on_stop_selected)
        toolbar.addWidget(self._stop_btn)

        self._stop_all_btn = QPushButton("Stop all")
        self._stop_all_btn.setToolTip(
            "Stop every L2 emulation session on this server."
        )
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        toolbar.addWidget(self._stop_all_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)

        self._info_label = QLabel("No sessions loaded.")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        toolbar.addWidget(self._info_label)

        toolbar.addStretch(1)

        protocols_legend = QLabel(
            "<span style='font-size:11px;color:#6b7280;'>"
            "Supports: <b>LACP</b> · <b>LLDP</b> · <b>VRRP</b> · "
            "<b>IGMP</b> · <b>PIM Hello</b></span>"
        )
        protocols_legend.setTextFormat(Qt.RichText)
        toolbar.addWidget(protocols_legend)

        outer.addLayout(toolbar)

        # Session table
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 250)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 100)
        self._table.setColumnWidth(7, 90)
        outer.addWidget(self._table, 1)

        # Hint footer
        hint = QLabel(
            "L2 frame generators need <code>CAP_NET_RAW</code> on Linux or root on macOS. "
            "If you see <code>PermissionError</code> in Last Error, the worker stopped — "
            "fix the server-side permissions and retry."
        )
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Auto-refresh timer — light, just polls /api/l2/sessions.
        self._timer = QTimer()
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        # First refresh after a short delay so the main window is up
        # before we hit the server.
        QTimer.singleShot(300, self.refresh)

    # ------------------------------------------------------------------
    def _get_server_url(self) -> Optional[str]:
        # Re-use the devices tab's resolver — same path topology uses.
        try:
            dt = getattr(self._parent_window, "devices_tab", None)
            if dt and hasattr(dt, "get_server_url"):
                return dt.get_server_url(silent=True)
        except Exception:
            pass
        return os.environ.get(
            "NETGEN_SERVER_URL", "http://localhost:5050"
        ).rstrip("/")

    def _auth_headers(self) -> Dict[str, str]:
        tok = os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
        return {"Authorization": f"Bearer {tok}"} if tok else {}

    # ------------------------------------------------------------------
    def refresh(self):
        """Pull /api/l2/sessions and re-render the table."""
        url = self._get_server_url()
        if not url:
            self._info_label.setText("No server URL configured.")
            return
        # Don't stack fetches.
        try:
            if self._fetch_worker is not None and self._fetch_worker.isRunning():
                return
        except RuntimeError:
            self._fetch_worker = None

        worker = _JsonFetchWorker(f"{url}/api/l2/sessions", timeout_s=3.0)
        # Pin a process-global strong ref so the worker survives the
        # QThread teardown race (PyQt5 5.15.11 + Python 3.14). See
        # utils/qthread_keepalive.py. NB: the QThread.start monkeypatch
        # installed at client launch also does this, but call it
        # explicitly so the L2 tab is safe even when launched stand-alone.
        try:
            from utils.qthread_keepalive import keep
            keep(worker)
        except Exception:
            pass
        worker.finished_ok.connect(self._on_refresh_ok)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(self._on_worker_finished)
        self._fetch_worker = worker
        worker.start()

    def _on_worker_finished(self):
        # Just clear the guard so the next refresh can spawn. Do NOT call
        # worker.deleteLater() here — `finished` fires the instant run()
        # returns, while Qt's internal QThreadPrivate teardown is still
        # settling. deleteLater() at that point destroys the C++ QThread
        # mid-teardown → "QThread: Destroyed while thread is still
        # running" → SIGABRT (confirmed culprit of the client's
        # startup crash on PyQt5 5.15.11 + Python 3.14). Lifetime is now
        # owned by the global keepalive registry, which trims workers
        # only after they've been finished >30s — long past the race.
        self._fetch_worker = None

    def _on_refresh_ok(self, payload, _code: int):
        sessions = (payload or {}).get("sessions") or []
        self._sessions_cache = sessions
        self._render_sessions(sessions)
        running = sum(1 for s in sessions if s.get("running"))
        self._info_label.setText(
            f"{len(sessions)} session(s) · {running} running"
        )
        # If a previous 404 had us in degraded mode, restore the full
        # poll cadence and re-enable Start.
        self._exit_unsupported_mode()

    def _on_refresh_failed(self, msg: str, http_code: int):
        """Categorise the failure so the operator sees something
        actionable instead of a Flask 404 HTML body."""
        if http_code == 404:
            # The /api/l2/* Blueprint isn't registered on this server.
            # Most common cause: the server is running 0.2.3 or older.
            self._enter_unsupported_mode(
                "Server doesn't expose /api/l2/* — upgrade to "
                "netgen-server ≥ 0.2.4 to use L2 emulation."
            )
            return
        if http_code == 401 or http_code == 403:
            self._info_label.setText(
                f"Auth failed ({http_code}). Set NETGEN_AUTH_TOKEN "
                "(operator or admin role) and restart the client."
            )
            return
        # Plain connect / timeout / DNS errors → show as-is but capped
        # so a multi-KB error blob doesn't wreck the layout.
        self._info_label.setText(f"Fetch failed: {msg[:120]}")

    def _enter_unsupported_mode(self, reason: str):
        """The server doesn't support L2 emulation. Slow the poll
        cadence and tell the operator why.

        We deliberately keep the Start button ENABLED — disabling it
        silently is bad UX because the operator clicks it, nothing
        happens, and they don't connect that to the small text in the
        status label. Instead, Start is intercepted in
        `_on_start_clicked` and shows the same message in a modal
        QMessageBox the moment the operator clicks."""
        if getattr(self, "_unsupported", False):
            self._unsupported_reason = reason
            return
        self._unsupported = True
        self._unsupported_reason = reason
        self._info_label.setText(reason)
        # Tooltip on the button for hover-discovery.
        self._start_btn.setToolTip(reason)
        # Slow the timer way down — once a minute is plenty for
        # detecting that the operator upgraded the server.
        try:
            self._timer.setInterval(60_000)
        except Exception:
            pass
        # Make sure the table is empty so a stale row doesn't suggest
        # a session exists when the surface is actually unavailable.
        self._render_sessions([])

    def _exit_unsupported_mode(self):
        """The server is responding to /api/l2/sessions again — restore
        normal poll cadence."""
        if not getattr(self, "_unsupported", False):
            return
        self._unsupported = False
        self._unsupported_reason = ""
        self._start_btn.setToolTip("")
        try:
            self._timer.setInterval(self.POLL_INTERVAL_MS)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _render_sessions(self, sessions: List[Dict[str, Any]]):
        self._table.setRowCount(len(sessions))
        for row, sess in enumerate(sessions):
            counters = sess.get("counters", {}) or {}
            session_id = sess.get("session_id", "")
            running = bool(sess.get("running"))

            def _set(col, text, color: Optional[QColor] = None):
                item = QTableWidgetItem(str(text))
                if color is not None:
                    item.setForeground(color)
                self._table.setItem(row, col, item)

            _set(0, session_id)
            _set(1, str(sess.get("protocol", "")).upper())
            _set(2, sess.get("iface", ""))
            _set(3, "Running" if running else "Stopped",
                 QColor("#16a34a") if running else QColor("#94a3b8"))
            _set(4, counters.get("frames_sent", 0))
            _set(5, counters.get("bytes_sent", 0))
            err = counters.get("last_error") or ""
            _set(6, err[:80], QColor("#dc2626") if err else None)
            uptime = counters.get("uptime_s", 0)
            _set(7, f"{uptime:.1f}" if uptime else "0.0")

    # ------------------------------------------------------------------
    def _on_start_clicked(self):
        # If the refresh path has already flagged this server as not
        # supporting L2, short-circuit BEFORE opening the dialog. No
        # point filling out 8 fields just to fail at POST time.
        if getattr(self, "_unsupported", False):
            QMessageBox.information(
                self, "L2 emulation unavailable",
                self._unsupported_reason or
                "The server doesn't expose /api/l2/* — upgrade to "
                "netgen-server ≥ 0.2.4 to use L2 emulation."
            )
            return

        url = self._get_server_url()
        if not url:
            QMessageBox.warning(self, "No server", "No server URL configured.")
            return

        default_iface = self._guess_default_iface()
        dlg = _L2ConfigDialog(self, default_iface=default_iface)
        if dlg.exec_() != QDialog.Accepted:
            return
        payload = dlg.accepted_payload()
        if not payload:
            return
        proto = payload["protocol"]
        body = payload["body"]

        try:
            r = requests.post(
                f"{url}/api/l2/{proto}/start",
                json=body, headers=self._auth_headers(), timeout=15,
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "Start failed", f"Request failed: {exc}"
            )
            return

        if r.status_code == 200:
            QTimer.singleShot(150, self.refresh)
            return

        # Non-200 — categorise the same way the refresh path does so
        # the operator doesn't see Flask's default 404 HTML page in a
        # QMessageBox.
        if r.status_code == 404:
            # Server doesn't have /api/l2/* — also flip the tab into
            # unsupported mode so subsequent clicks are intercepted.
            msg = (
                "The server doesn't expose /api/l2/* — upgrade to "
                "netgen-server ≥ 0.2.4 to use L2 emulation."
            )
            self._enter_unsupported_mode(msg)
            QMessageBox.information(self, "L2 emulation unavailable", msg)
            return
        if r.status_code in (401, 403):
            QMessageBox.warning(
                self, "Authentication failed",
                f"Server returned {r.status_code}. Set "
                f"NETGEN_AUTH_TOKEN with operator-or-admin role and "
                f"restart the client."
            )
            return
        # Try to surface the server's JSON error, else just the code.
        # Drop the Flask HTML body — never shown to the operator.
        try:
            err = r.json().get("error") or r.text[:200]
        except Exception:
            err = r.text[:200] if r.text else f"(no body)"
        QMessageBox.warning(
            self, "Start failed",
            f"HTTP {r.status_code}: {err}"
        )

    def _guess_default_iface(self) -> str:
        """Pick a sane default interface for the dialog. Walks the
        first online server's interface list (if cached), else falls
        back to eth0."""
        try:
            mw = self._parent_window
            servers = getattr(mw, "server_interfaces", []) or []
            for s in servers:
                ifaces = s.get("interfaces") or []
                if ifaces:
                    first = ifaces[0]
                    if isinstance(first, dict):
                        return first.get("name") or "eth0"
                    if isinstance(first, str):
                        return first
        except Exception:
            pass
        return "eth0"

    # ------------------------------------------------------------------
    def _on_stop_selected(self):
        url = self._get_server_url()
        if not url:
            return
        rows = sorted({i.row() for i in self._table.selectionModel().selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self, "Stop selected",
                "Select one or more session rows first."
            )
            return
        sids = []
        for row in rows:
            it = self._table.item(row, 0)
            if it and it.text():
                sids.append(it.text())
        for sid in sids:
            try:
                requests.post(
                    f"{url}/api/l2/stop",
                    json={"session_id": sid},
                    headers=self._auth_headers(),
                    timeout=5,
                )
            except Exception as exc:
                logger.debug(f"[L2] stop {sid} failed: {exc}")
        QTimer.singleShot(150, self.refresh)

    def _on_stop_all(self):
        url = self._get_server_url()
        if not url:
            return
        confirm = QMessageBox.question(
            self, "Stop all L2 sessions",
            "Stop every running L2 emulation session on the server?\n"
            "This affects every operator currently using the L2 emulator.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            requests.post(
                f"{url}/api/l2/stop", json={},
                headers=self._auth_headers(), timeout=10,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Stop all", f"Request failed: {exc}")
            return
        QTimer.singleShot(150, self.refresh)

    # ------------------------------------------------------------------
    def cleanup_threads(self):
        """Stop the poll timer + drop the fetch worker on app close."""
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            if self._fetch_worker is not None:
                if self._fetch_worker.isRunning():
                    self._fetch_worker.wait(1000)
        except RuntimeError:
            pass
