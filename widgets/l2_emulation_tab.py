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
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHeaderView, QGroupBox, QDoubleSpinBox,
)


logger = logging.getLogger(__name__)


def _bold_font() -> QFont:
    """A semibold font for the protocol-badge cell."""
    f = QFont()
    f.setBold(True)
    return f


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
        self.setMinimumWidth(560)
        self._payload: Optional[Dict[str, Any]] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # Dialog header — title + one-line purpose.
        hdr = QLabel("Start L2 / multicast emulation")
        hdr.setStyleSheet(
            "font-size: 15px; font-weight: 600; color: #0f172a;"
        )
        outer.addWidget(hdr)
        sub = QLabel(
            "Pick a protocol and tune its parameters. The frame egresses the "
            "chosen interface on the server."
        )
        sub.setStyleSheet("color: #64748b; font-size: 11px;")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # Common settings (apply to every protocol) live in their own box
        # so the protocol-specific stack reads as a distinct second section.
        common_box = QGroupBox("Common settings")
        top_form = QFormLayout(common_box)
        top_form.setLabelAlignment(Qt.AlignRight)
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

        # Inline 802.1Q encapsulation (Spirent-style) — applies to ALL
        # protocols. 0 = untagged. When set, the frame egresses VLAN-tagged
        # straight off the chosen interface; no pre-created vlanN subif
        # needed. PCP is the 802.1p priority (0-7).
        self._vlan_id_spin = QSpinBox()
        self._vlan_id_spin.setRange(0, 4094)
        self._vlan_id_spin.setValue(0)
        self._vlan_id_spin.setSpecialValueText("untagged")
        self._vlan_id_spin.setToolTip(
            "802.1Q VLAN ID for the emulated frames. 0 = untagged. Set this "
            "to send tagged frames without creating a vlanN subinterface."
        )
        top_form.addRow("VLAN ID:", self._vlan_id_spin)

        self._vlan_pcp_spin = QSpinBox()
        self._vlan_pcp_spin.setRange(0, 7)
        self._vlan_pcp_spin.setValue(0)
        self._vlan_pcp_spin.setToolTip("802.1p priority (PCP), 0-7. Only used when VLAN ID > 0.")
        top_form.addRow("VLAN PCP:", self._vlan_pcp_spin)

        outer.addWidget(common_box)

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
            "After clicking Start, watch the session table's <b>Last Error</b> "
            "column — root / <code>CAP_NET_RAW</code> failures surface there."
        )
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("Start")
        ok_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "font-weight: 600; padding: 5px 18px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
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
        self._vrrp_src_mac = QLineEdit("")
        self._vrrp_src_mac.setPlaceholderText(
            "auto — VRRP virtual MAC 00:00:5e:00:01:<vrid> (leave blank)"
        )
        self._vrrp_src_mac.setToolTip(
            "Leave blank to source advertisements from the RFC 5798 virtual "
            "router MAC (00:00:5e:00:01:<vrid> for IPv4, …:02:<vrid> for IPv6) "
            "— what a real VRRP master uses. Only set this to override."
        )
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
        # Inline 802.1Q tag (applies to every protocol). 0 = untagged →
        # don't send the field so the server default (untagged) applies.
        vlan_id = self._vlan_id_spin.value()
        if vlan_id > 0:
            body["vlan_id"] = vlan_id
            body["vlan_pcp"] = self._vlan_pcp_spin.value()

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
    # Column order is glance-first: status & protocol up front, the long
    # opaque Session ID near the end, Last Error stretches to fill.
    COLUMNS = [
        "Status", "Protocol", "Interface", "VLAN",
        "Frames TX", "Failed", "Bytes TX", "Uptime",
        "Session ID", "Last Error",
    ]
    COL_STATUS, COL_PROTO, COL_IFACE, COL_VLAN = 0, 1, 2, 3
    COL_FRAMES, COL_FAILED, COL_BYTES, COL_UPTIME = 4, 5, 6, 7
    COL_SID, COL_ERR = 8, 9

    # Per-protocol accent colour for the badge text.
    _PROTO_COLORS = {
        "lacp": "#7c3aed",   # violet
        "lldp": "#0891b2",   # cyan
        "vrrp": "#ca8a04",   # amber
        "igmp": "#2563eb",   # blue
        "pim":  "#db2777",   # pink
    }

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
        # Tight chrome to match the Devices tab — action bar + table read
        # as one panel. No big banner: the QTabWidget tab label already
        # says "L2 Emulation".
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)

        # ── Action bar ────────────────────────────────────────────────
        # Light-grey strip with the controls, mirroring devices_tab's
        # action_bar. Sits directly on top of the table (bottom border
        # only) so the two read as a single panel.
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border: 1px solid #e5e7eb; border-bottom: none; }"
        )
        bar = QHBoxLayout(action_bar)
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(6)

        _BTN_H = 24
        self._start_btn = QPushButton("Start emulation…")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setFixedHeight(_BTN_H)
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "font-weight: 600; padding: 0 14px; border-radius: 5px; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        bar.addWidget(self._start_btn)

        # Neutral / danger buttons share a consistent flat style.
        _neutral_css = (
            "QPushButton { background-color: #ffffff; color: #334155; "
            "border: 1px solid #cbd5e1; padding: 0 12px; border-radius: 5px; } "
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; } "
            "QPushButton:disabled { color: #cbd5e1; border-color: #e5e7eb; }"
        )
        _danger_css = (
            "QPushButton { background-color: #ffffff; color: #b91c1c; "
            "border: 1px solid #fca5a5; padding: 0 12px; border-radius: 5px; } "
            "QPushButton:hover { background-color: #fef2f2; }"
        )

        self._stop_btn = QPushButton("Stop selected")
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setFixedHeight(_BTN_H)
        self._stop_btn.setStyleSheet(_neutral_css)
        self._stop_btn.clicked.connect(self._on_stop_selected)
        bar.addWidget(self._stop_btn)

        self._stop_all_btn = QPushButton("Stop all")
        self._stop_all_btn.setCursor(Qt.PointingHandCursor)
        self._stop_all_btn.setFixedHeight(_BTN_H)
        self._stop_all_btn.setStyleSheet(_danger_css)
        self._stop_all_btn.setToolTip(
            "Stop every L2 emulation session on this server."
        )
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        bar.addWidget(self._stop_all_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setFixedHeight(_BTN_H)
        self._refresh_btn.setStyleSheet(_neutral_css)
        self._refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self._refresh_btn)

        # Status / notice line — transient errors and unsupported-server
        # notices land here. Sits between the buttons and the chip.
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self._info_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar.addWidget(self._info_label, 1)

        # Live count chip — running / total. Updated each poll.
        self._count_chip = QLabel("—")
        self._count_chip.setAlignment(Qt.AlignCenter)
        self._set_count_chip(0, 0)
        bar.addWidget(self._count_chip)

        outer.addWidget(action_bar)

        # ── Session table ─────────────────────────────────────────────
        # Plain default Qt table chrome, matching the Devices / BGP /
        # OSPF / IS-IS tables. Compact row height; rich cell content
        # (status pill, protocol badge, formatted counters) is applied
        # per-item in _render_sessions.
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setColumnWidth(self.COL_STATUS, 96)
        self._table.setColumnWidth(self.COL_PROTO, 86)
        self._table.setColumnWidth(self.COL_IFACE, 110)
        self._table.setColumnWidth(self.COL_VLAN, 92)
        self._table.setColumnWidth(self.COL_FRAMES, 108)
        self._table.setColumnWidth(self.COL_FAILED, 72)
        self._table.setColumnWidth(self.COL_BYTES, 100)
        self._table.setColumnWidth(self.COL_UPTIME, 92)
        self._table.setColumnWidth(self.COL_SID, 132)
        # Right-align the numeric-column headers so they sit over their
        # right-aligned values.
        for col in (self.COL_FRAMES, self.COL_FAILED,
                    self.COL_BYTES, self.COL_UPTIME):
            hi = self._table.horizontalHeaderItem(col)
            if hi is not None:
                hi.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # The permission note that used to be a footer row now lives as a
        # tooltip on the table — keeps the chrome to just the action bar.
        self._table.setToolTip(
            "L2 frame generators need CAP_NET_RAW on Linux or root on macOS. "
            "If you see PermissionError in Last Error, the worker stopped — "
            "fix the server-side permissions and retry."
        )
        outer.addWidget(self._table, 1)

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
        self._set_count_chip(running, len(sessions))
        # Clear any stale error/notice now that the server is healthy.
        self._info_label.setText("")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        # If a previous 404 had us in degraded mode, restore the full
        # poll cadence and re-enable Start.
        self._exit_unsupported_mode()

    def _set_count_chip(self, running: int, total: int):
        """Render the header status chip: green when something's running,
        slate when idle. Reads at a glance — 'N running · M total'."""
        if running > 0:
            bg, fg, dot = "#dcfce7", "#166534", "#16a34a"
        elif total > 0:
            bg, fg, dot = "#f1f5f9", "#475569", "#94a3b8"
        else:
            bg, fg, dot = "#f1f5f9", "#94a3b8", "#cbd5e1"
        self._count_chip.setText(
            f"<span style='color:{dot};'>●</span> "
            f"<b>{running}</b> running · {total} total"
        )
        self._count_chip.setTextFormat(Qt.RichText)
        self._count_chip.setStyleSheet(
            f"background: {bg}; color: {fg}; font-size: 11px; "
            f"padding: 2px 10px; border-radius: 9px;"
        )

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
            self._info_label.setStyleSheet("color: #b45309; font-size: 11px;")
            self._info_label.setText(
                f"Auth failed ({http_code}). Set NETGEN_AUTH_TOKEN "
                "(operator or admin role) and restart the client."
            )
            return
        # Plain connect / timeout / DNS errors → show as-is but capped
        # so a multi-KB error blob doesn't wreck the layout.
        self._info_label.setStyleSheet("color: #b45309; font-size: 11px;")
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
        self._info_label.setStyleSheet("color: #b45309; font-size: 11px;")
        self._info_label.setText(reason)
        self._count_chip.setText("unavailable")
        self._count_chip.setStyleSheet(
            "background: #fef3c7; color: #b45309; font-size: 11px; "
            "padding: 2px 10px; border-radius: 9px;"
        )
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
    @staticmethod
    def _fmt_count(n) -> str:
        """Thousands-separated integer; '0' falls through unchanged."""
        try:
            return f"{int(n):,}"
        except (TypeError, ValueError):
            return str(n)

    @staticmethod
    def _fmt_bytes(n) -> str:
        """Human-readable byte size (B/KB/MB/GB/TB)."""
        try:
            b = float(n)
        except (TypeError, ValueError):
            return str(n)
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024.0:
                return f"{int(b)} {unit}" if unit == "B" else f"{b:.1f} {unit}"
            b /= 1024.0
        return f"{b:.1f} TB"

    @staticmethod
    def _fmt_uptime(s) -> str:
        """Compact h/m/s uptime, e.g. '2h 5m 9s' or '47s'."""
        try:
            total = int(float(s))
        except (TypeError, ValueError):
            return "—"
        if total <= 0:
            return "0s"
        h, rem = divmod(total, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {sec}s"
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"

    def _render_sessions(self, sessions: List[Dict[str, Any]]):
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)

        self._table.setRowCount(len(sessions))
        for row, sess in enumerate(sessions):
            counters = sess.get("counters", {}) or {}
            config = sess.get("config", {}) or {}
            session_id = str(sess.get("session_id", ""))
            proto = str(sess.get("protocol", "")).lower()
            running = bool(sess.get("running"))

            def _set(col, text, *, color: Optional[QColor] = None,
                     align=None, font: Optional[QFont] = None,
                     tooltip: Optional[str] = None, sid: bool = False):
                item = QTableWidgetItem(str(text))
                if color is not None:
                    item.setForeground(color)
                if align is not None:
                    item.setTextAlignment(align | Qt.AlignVCenter)
                if font is not None:
                    item.setFont(font)
                if tooltip is not None:
                    item.setToolTip(tooltip)
                if sid:
                    # Stash the full session_id so Stop-selected can
                    # recover it regardless of the displayed column text.
                    item.setData(Qt.UserRole, session_id)
                self._table.setItem(row, col, item)

            # Status pill — coloured dot + word, the most glanceable cell.
            _set(self.COL_STATUS,
                 ("● Running" if running else "● Stopped"),
                 color=QColor("#16a34a") if running else QColor("#94a3b8"),
                 align=Qt.AlignCenter, sid=True)

            # Protocol badge — uppercase, per-protocol accent colour.
            _set(self.COL_PROTO, proto.upper(),
                 color=QColor(self._PROTO_COLORS.get(proto, "#334155")),
                 align=Qt.AlignCenter, font=_bold_font())

            _set(self.COL_IFACE, sess.get("iface", ""))

            # VLAN — show tag + PCP, or a subtle 'untagged'.
            vlan_id = config.get("vlan_id")
            if vlan_id:
                pcp = config.get("vlan_pcp") or 0
                vlan_txt = f"{vlan_id}" + (f" · p{pcp}" if pcp else "")
                _set(self.COL_VLAN, vlan_txt, align=Qt.AlignCenter,
                     color=QColor("#0f172a"))
            else:
                _set(self.COL_VLAN, "untagged", align=Qt.AlignCenter,
                     color=QColor("#cbd5e1"))

            _set(self.COL_FRAMES, self._fmt_count(counters.get("frames_sent", 0)),
                 align=Qt.AlignRight)

            failed = counters.get("frames_failed", 0) or 0
            _set(self.COL_FAILED, self._fmt_count(failed),
                 align=Qt.AlignRight,
                 color=QColor("#dc2626") if failed else QColor("#94a3b8"))

            _set(self.COL_BYTES, self._fmt_bytes(counters.get("bytes_sent", 0)),
                 align=Qt.AlignRight)

            _set(self.COL_UPTIME, self._fmt_uptime(counters.get("uptime_s", 0)),
                 align=Qt.AlignRight)

            # Session ID — monospaced, truncated, full value on hover.
            short_sid = (session_id[:10] + "…") if len(session_id) > 11 else session_id
            _set(self.COL_SID, short_sid, font=mono,
                 color=QColor("#64748b"), tooltip=session_id)

            err = counters.get("last_error") or ""
            _set(self.COL_ERR, err[:120],
                 color=QColor("#dc2626") if err else None,
                 tooltip=err or None)

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

    @staticmethod
    def _skip_as_default_iface(name: str) -> bool:
        """True for interfaces that must never be the L2 default.

        L2 emulation frames (LACP/LLDP/VRRP/IGMP/PIM) have to egress a
        real NIC toward the switch — loopback delivers them nowhere. The
        server's /api/interfaces list frequently returns 'lo' FIRST, so
        the old `ifaces[0]` pick defaulted the dialog to loopback. Skip
        loopback and obvious virtual/non-egress devices.
        """
        n = (name or "").strip().lower()
        if not n:
            return True
        if n in ("lo", "lo0", "loopback"):
            return True
        return n.startswith(("vrf-", "docker", "br-", "veth", "virbr", "tap", "tun"))

    def _guess_default_iface(self) -> str:
        """Pick a sane default EGRESS interface for the dialog: the first
        non-loopback, non-virtual interface from the first online server's
        cached list. Falls back to eth0 if none is cached."""
        try:
            mw = self._parent_window
            servers = getattr(mw, "server_interfaces", []) or []
            for s in servers:
                for ent in (s.get("interfaces") or []):
                    name = ent.get("name") if isinstance(ent, dict) else ent
                    if name and not self._skip_as_default_iface(name):
                        return name
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
            it = self._table.item(row, self.COL_STATUS)
            if it is None:
                continue
            # Full session_id is stashed in UserRole (the Status cell shows
            # a pill, not the ID — the ID lives in its own column now).
            sid = it.data(Qt.UserRole)
            if sid:
                sids.append(str(sid))
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
