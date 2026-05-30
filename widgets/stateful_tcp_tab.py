"""Stateful TCP tab — GUI surface for the real-socket TCP traffic generator.

Mirrors what the CLI gets via `netgen-cli tcp start-*`: pick a role (client
or server), pick a protocol (raw / http), optionally wrap in TLS, fire.
A live session table below shows every running session with its counters —
conns_established, bytes_tx/rx, avg RTT, uptime — and per-row Stop.

v1 scope:
  * Roles: client + server.
  * Protocols: raw + http. (DNS / SIP deferred to v2 — they need a CLI
    flag addition too so they ship together.)
  * TLS: client (verify + SNI) and server (cert + key).
  * VRF: passed through to the worker; the API-side helper degrades
    gracefully on macOS / non-root Linux (the warning lands in
    `last_error` and the session still runs).

Backend: `server/stateful_tcp_routes.py` → `utils/stateful_tcp.py`.
This tab is a thin REST client — no socket import here, no business
logic. Structurally a sibling of `widgets/l2_emulation_tab.py`; lift
that file for the patterns we don't restate (auth-header, parent-window
URL resolver, QThread keepalive, 404-graceful unsupported mode, etc.).
"""

from __future__ import annotations

import ipaddress as _ipaddress
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


def _bold_font() -> QFont:
    """A semibold font for the role / protocol badge cells."""
    f = QFont()
    f.setBold(True)
    return f


# ──────────────────────────────────────────────── input validators


def _validate_ip(value: str) -> Optional[str]:
    """Return None if `value` parses as v4 or v6, else a reason string.
    Also accepts `0.0.0.0` (server bind-any) and `::` per ipaddress."""
    if not value:
        return "IP address is empty"
    try:
        _ipaddress.ip_address(value.strip())
    except (ValueError, TypeError):
        return f"'{value}' isn't a valid IP — must be IPv4 or IPv6."
    return None


def _validate_port(n: int) -> Optional[str]:
    """Return None if `n` is a usable port. 0 is rejected — the server
    treats 0 as 'pick one' for OS-allocated ephemeral, but we don't
    surface that knob in the GUI and a 0 here is almost certainly a
    leftover from a cleared field."""
    if n is None:
        return "Port is empty"
    if not (1 <= int(n) <= 65535):
        return f"Port must be between 1 and 65535 (got {n})."
    return None


def _is_loopback(ip: str) -> bool:
    """Cheap check used by the dialog's amber warning on `interval_s=0`."""
    try:
        return _ipaddress.ip_address((ip or "").strip()).is_loopback
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- fetch worker


class _JsonFetchWorker(QThread):
    """Same shape as the L2 tab's worker — keeps the GUI thread alive
    while /api/stateful_tcp/sessions resolves. Emits
    `failed(short_msg, http_code)` so the consumer can react differently
    to 404 (server too old) vs 401/403 (auth) vs raw connect errors.
    Don't pass HTML response bodies through — Flask's 404 page would
    land in the GUI info label otherwise."""

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
# Config dialog
# ====================================================================


class _StatefulTcpConfigDialog(QDialog):
    """One dialog, two role-specific field sets via QStackedWidget.
    Common settings (protocol, duration, TLS toggle) sit above; the
    matching client- or server-only form shows below. TLS expands an
    extra group inline. Returns the start-body dict via
    `accepted_payload()` after exec_().

    The payload has shape `{"role": "client"|"server", "body": {...}}`.
    The tab pumps that through to `/api/stateful_tcp/start` with `role`
    embedded in the body — that matches the existing CLI wrapper at
    netgen_cli.py:cmd_tcp.
    """

    PROTOCOLS = [
        ("raw",  "Raw — N bytes payload, optional echo"),
        ("http", "HTTP/1.1 — POST framed, status-code binned"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start stateful-TCP session")
        self.setMinimumWidth(560)
        self._payload: Optional[Dict[str, Any]] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # Header.
        hdr = QLabel("Start stateful-TCP session")
        hdr.setStyleSheet(
            "font-size: 15px; font-weight: 600; color: #0f172a;"
        )
        outer.addWidget(hdr)
        sub = QLabel(
            "Pick a role and protocol. Sessions complete real TCP "
            "handshakes — middleboxes / NAT / proxies see actual "
            "connection state."
        )
        sub.setStyleSheet("color: #64748b; font-size: 11px;")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # ── Role selector ────────────────────────────────────────────
        role_box = QGroupBox("Role")
        role_lay = QHBoxLayout(role_box)
        role_lay.setContentsMargins(10, 6, 10, 6)
        self._role_client = QRadioButton("Client (drive traffic at a target)")
        self._role_server = QRadioButton("Server (listen for incoming connections)")
        self._role_client.setChecked(True)
        role_group = QButtonGroup(self)
        role_group.addButton(self._role_client)
        role_group.addButton(self._role_server)
        self._role_client.toggled.connect(self._on_role_changed)
        role_lay.addWidget(self._role_client)
        role_lay.addWidget(self._role_server)
        role_lay.addStretch(1)
        outer.addWidget(role_box)

        # ── Common settings ──────────────────────────────────────────
        common_box = QGroupBox("Common")
        common_form = QFormLayout(common_box)
        common_form.setLabelAlignment(Qt.AlignRight)

        self._proto_combo = QComboBox()
        for key, label in self.PROTOCOLS:
            self._proto_combo.addItem(label, key)
        self._proto_combo.currentIndexChanged.connect(self._on_protocol_changed)
        common_form.addRow("Protocol:", self._proto_combo)

        self._tls_check = QCheckBox("Wrap connection in TLS")
        self._tls_check.toggled.connect(self._on_tls_toggled)
        common_form.addRow("TLS:", self._tls_check)

        outer.addWidget(common_box)

        # ── Role-specific stack ──────────────────────────────────────
        self._role_stack = QStackedWidget()
        self._role_stack.addWidget(self._build_client_panel())   # idx 0
        self._role_stack.addWidget(self._build_server_panel())   # idx 1
        outer.addWidget(self._role_stack)

        # ── TLS group (hidden when checkbox is off) ──────────────────
        self._tls_group = self._build_tls_group()
        self._tls_group.setVisible(False)
        outer.addWidget(self._tls_group)

        # ── Buttons ──────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Ok).setText("Start")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

        # Initial visibility wiring.
        self._on_role_changed()
        self._on_protocol_changed()

    # ----- panel builders ---------------------------------------------

    def _build_client_panel(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(0, 4, 0, 4)

        self._cli_dst_ip = QLineEdit()
        self._cli_dst_ip.setPlaceholderText("10.0.0.5  (or 127.0.0.1 for loopback)")
        self._cli_dst_ip.textChanged.connect(self._update_loopback_warning)
        form.addRow("Destination IP:", self._cli_dst_ip)

        self._cli_dst_port = QSpinBox()
        self._cli_dst_port.setRange(1, 65535)
        self._cli_dst_port.setValue(5001)
        form.addRow("Destination port:", self._cli_dst_port)

        self._cli_src_ip = QLineEdit()
        self._cli_src_ip.setPlaceholderText("optional — pin client source IP")
        form.addRow("Source IP:", self._cli_src_ip)

        self._cli_vrf = QLineEdit()
        self._cli_vrf.setPlaceholderText(
            "optional — Linux VRF / iface (no-op on macOS, falls back to "
            "default routing table)"
        )
        form.addRow("VRF:", self._cli_vrf)

        self._cli_duration = QDoubleSpinBox()
        self._cli_duration.setRange(0.1, 86400.0)
        self._cli_duration.setSuffix(" s")
        self._cli_duration.setValue(30.0)
        form.addRow("Duration:", self._cli_duration)

        self._cli_payload = QSpinBox()
        self._cli_payload.setRange(0, 1024 * 1024)
        self._cli_payload.setValue(1024)
        self._cli_payload.setSuffix(" B")
        form.addRow("Payload bytes:", self._cli_payload)

        self._cli_concurrency = QSpinBox()
        self._cli_concurrency.setRange(1, 256)
        self._cli_concurrency.setValue(1)
        form.addRow("Concurrency:", self._cli_concurrency)

        self._cli_interval = QDoubleSpinBox()
        self._cli_interval.setRange(0.0, 60.0)
        self._cli_interval.setDecimals(3)
        self._cli_interval.setValue(0.0)
        self._cli_interval.setSuffix(" s")
        self._cli_interval.valueChanged.connect(self._update_loopback_warning)
        form.addRow("Interval per conn:", self._cli_interval)

        # Loopback / TIME_WAIT warning — non-blocking amber. Wired to
        # both `dst_ip` and `interval_s` changes; surfaces the bug
        # documented in API_GUIDE §8 → "Loopback testing caveat".
        self._loopback_warn = QLabel("")
        self._loopback_warn.setWordWrap(True)
        self._loopback_warn.setStyleSheet(
            "color: #b45309; background: #fef3c7; border: 1px solid #fde68a; "
            "border-radius: 4px; padding: 4px 8px; font-size: 11px;"
        )
        self._loopback_warn.setVisible(False)
        form.addRow("", self._loopback_warn)

        self._cli_expect_echo = QCheckBox("Expect echo (recv & count response)")
        self._cli_expect_echo.setChecked(True)
        form.addRow("", self._cli_expect_echo)

        self._cli_connect_timeout = QDoubleSpinBox()
        self._cli_connect_timeout.setRange(0.1, 120.0)
        self._cli_connect_timeout.setValue(5.0)
        self._cli_connect_timeout.setSuffix(" s")
        form.addRow("Connect timeout:", self._cli_connect_timeout)

        return w

    def _build_server_panel(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(0, 4, 0, 4)

        self._srv_listen_ip = QLineEdit("0.0.0.0")
        self._srv_listen_ip.setPlaceholderText("0.0.0.0  (bind any) — or a specific local IP")
        form.addRow("Listen IP:", self._srv_listen_ip)

        self._srv_listen_port = QSpinBox()
        self._srv_listen_port.setRange(1, 65535)
        self._srv_listen_port.setValue(5001)
        form.addRow("Listen port:", self._srv_listen_port)

        self._srv_vrf = QLineEdit()
        self._srv_vrf.setPlaceholderText("optional — Linux VRF / iface")
        form.addRow("VRF:", self._srv_vrf)

        self._srv_mode = QComboBox()
        self._srv_mode.addItems(["echo", "discard"])
        self._srv_mode.setToolTip(
            "echo: read full client message, write it back (pairs with "
            "client expect_echo). discard: drain & close — useful when "
            "the client is measuring tx throughput in isolation."
        )
        form.addRow("Mode:", self._srv_mode)

        self._srv_response_bytes = QSpinBox()
        self._srv_response_bytes.setRange(0, 1024 * 1024)
        self._srv_response_bytes.setValue(1024)
        self._srv_response_bytes.setSuffix(" B")
        self._srv_response_bytes.setToolTip(
            "HTTP body size when protocol=http. Ignored for protocol=raw."
        )
        form.addRow("Response bytes (HTTP):", self._srv_response_bytes)

        return w

    def _build_tls_group(self) -> QGroupBox:
        box = QGroupBox("TLS")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        # Client-side TLS fields ----
        self._tls_verify = QCheckBox(
            "Verify cert + hostname (defaults off for self-signed test certs)"
        )
        form.addRow("", self._tls_verify)

        self._tls_sni = QLineEdit()
        self._tls_sni.setPlaceholderText("optional — SNI / hostname check (defaults to dst-ip)")
        form.addRow("SNI hostname:", self._tls_sni)

        # Server-side TLS fields ----
        cert_row = QHBoxLayout()
        self._tls_cert = QLineEdit()
        self._tls_cert.setPlaceholderText("/path/to/cert.pem  (required for server TLS)")
        cert_browse = QPushButton("Browse…")
        cert_browse.clicked.connect(
            lambda: self._browse_file(self._tls_cert, "Choose cert PEM")
        )
        cert_row.addWidget(self._tls_cert, 1)
        cert_row.addWidget(cert_browse)
        cert_wrap = QWidget(); cert_wrap.setLayout(cert_row)
        self._tls_cert_label = QLabel("Cert (server):")
        form.addRow(self._tls_cert_label, cert_wrap)

        key_row = QHBoxLayout()
        self._tls_key = QLineEdit()
        self._tls_key.setPlaceholderText("/path/to/key.pem  (required for server TLS)")
        key_browse = QPushButton("Browse…")
        key_browse.clicked.connect(
            lambda: self._browse_file(self._tls_key, "Choose key PEM")
        )
        key_row.addWidget(self._tls_key, 1)
        key_row.addWidget(key_browse)
        key_wrap = QWidget(); key_wrap.setLayout(key_row)
        self._tls_key_label = QLabel("Key (server):")
        form.addRow(self._tls_key_label, key_wrap)

        return box

    # ----- live UX wiring ---------------------------------------------

    def _on_role_changed(self, *_):
        is_client = self._role_client.isChecked()
        self._role_stack.setCurrentIndex(0 if is_client else 1)
        # Toggle client-only vs server-only TLS rows.
        self._tls_verify.setVisible(is_client)
        self._tls_sni.setVisible(is_client)
        # cert / key only matter for server-side TLS; hide on client.
        for w in (self._tls_cert, self._tls_cert_label,
                  self._tls_key, self._tls_key_label):
            w.setVisible(not is_client)
        # Reset visibility of the loopback warning when switching to
        # server (it only applies to client).
        if not is_client:
            self._loopback_warn.setVisible(False)
        else:
            self._update_loopback_warning()

    def _on_protocol_changed(self, *_):
        proto = self._proto_combo.currentData()
        # response_bytes is only meaningful for HTTP server; grey it out
        # for raw so the operator doesn't mistake it for a noop knob.
        self._srv_response_bytes.setEnabled(proto == "http")

    def _on_tls_toggled(self, on: bool):
        self._tls_group.setVisible(on)
        # Re-apply role visibility — switching TLS on after a role
        # change needs to refresh which TLS rows show.
        self._on_role_changed()

    def _update_loopback_warning(self, *_):
        if not self._role_client.isChecked():
            self._loopback_warn.setVisible(False)
            return
        dst = self._cli_dst_ip.text().strip()
        interval = self._cli_interval.value()
        if _is_loopback(dst) and interval < 0.005:
            self._loopback_warn.setText(
                "⚠ Loopback + interval=0 will exhaust the kernel "
                "ephemeral-port range within seconds (EADDRNOTAVAIL). "
                "Set interval to ≥ 0.02 s for loopback runs."
            )
            self._loopback_warn.setVisible(True)
        else:
            self._loopback_warn.setVisible(False)

    def _browse_file(self, target: QLineEdit, caption: str):
        path, _ = QFileDialog.getOpenFileName(
            self, caption, "", "PEM files (*.pem *.crt *.key);;All files (*)"
        )
        if path:
            target.setText(path)

    # ----- accept / payload assembly ----------------------------------

    def _on_accept(self):
        is_client = self._role_client.isChecked()
        proto = self._proto_combo.currentData()
        tls = self._tls_check.isChecked()

        def _reject(reason: str) -> None:
            QMessageBox.warning(self, "Invalid input", reason)

        if is_client:
            dst_ip = self._cli_dst_ip.text().strip()
            err = _validate_ip(dst_ip)
            if err:
                _reject(f"Destination IP: {err}")
                return
            err = _validate_port(self._cli_dst_port.value())
            if err:
                _reject(f"Destination port: {err}")
                return
            src_ip = self._cli_src_ip.text().strip()
            if src_ip:
                err = _validate_ip(src_ip)
                if err:
                    _reject(f"Source IP: {err}")
                    return
            body: Dict[str, Any] = {
                "role": "client",
                "dst_ip": dst_ip,
                "dst_port": self._cli_dst_port.value(),
                "duration_s": self._cli_duration.value(),
                "payload_bytes": self._cli_payload.value(),
                "concurrency": self._cli_concurrency.value(),
                "interval_s": self._cli_interval.value(),
                "expect_echo": self._cli_expect_echo.isChecked(),
                "connect_timeout_s": self._cli_connect_timeout.value(),
                "protocol": proto,
                "tls": tls,
            }
            if src_ip:
                body["src_ip"] = src_ip
            vrf = self._cli_vrf.text().strip()
            if vrf:
                body["vrf"] = vrf
            if tls:
                body["tls_verify"] = self._tls_verify.isChecked()
                sni = self._tls_sni.text().strip()
                if sni:
                    body["tls_server_hostname"] = sni
        else:
            listen_ip = self._srv_listen_ip.text().strip()
            err = _validate_ip(listen_ip)
            if err:
                _reject(f"Listen IP: {err}")
                return
            err = _validate_port(self._srv_listen_port.value())
            if err:
                _reject(f"Listen port: {err}")
                return
            if tls:
                cert = self._tls_cert.text().strip()
                key = self._tls_key.text().strip()
                if not cert or not key:
                    _reject(
                        "Server TLS requires both a cert PEM and a key PEM "
                        "path. Use the Browse… buttons to pick them."
                    )
                    return
                # Cert/key path existence — soft-check so the operator
                # spots a typo before the API rejects it. The actual load
                # still happens server-side.
                for label, path in (("Cert", cert), ("Key", key)):
                    if not os.path.isfile(path):
                        _reject(
                            f"{label} file not found at {path}. The "
                            "server reads this path directly, so it must "
                            "be readable from the server's filesystem."
                        )
                        return
            body = {
                "role": "server",
                "listen_ip": listen_ip,
                "listen_port": self._srv_listen_port.value(),
                "mode": self._srv_mode.currentText(),
                "protocol": proto,
                "tls": tls,
            }
            if proto == "http":
                body["response_bytes"] = self._srv_response_bytes.value()
            vrf = self._srv_vrf.text().strip()
            if vrf:
                body["vrf"] = vrf
            if tls:
                body["tls_cert"] = self._tls_cert.text().strip()
                body["tls_key"] = self._tls_key.text().strip()

        self._payload = {"role": ("client" if is_client else "server"),
                         "body": body}
        self.accept()

    def accepted_payload(self) -> Optional[Dict[str, Any]]:
        return self._payload


# ====================================================================
# Main tab
# ====================================================================


class StatefulTcpTab(QWidget):
    """Stateful-TCP session manager.

    Exposes the `/api/stateful_tcp/*` REST surface as a GUI tab — same
    UX shape as L2 Emulation: action bar → Start dialog → session table
    that polls every 3 s. Per-row Stop, Stop selected, Stop all.
    """

    POLL_INTERVAL_MS = 3000

    # Column order: glance-first (status / role / protocol / target),
    # then numerics aligned right, then long opaque SID, then per-row
    # Stop. Last Error is intentionally NOT a column — it can be 200
    # chars and would push everything else off-screen. It's surfaced
    # via the Status cell's tooltip (matches the help-guide entry).
    COLUMNS = [
        "Status", "Role", "Protocol", "Target",
        "Conns", "Bytes TX", "Bytes RX", "Avg RTT", "Uptime",
        "Session ID", "",
    ]
    COL_STATUS, COL_ROLE, COL_PROTO, COL_TARGET = 0, 1, 2, 3
    COL_CONNS, COL_TX, COL_RX, COL_RTT, COL_UPTIME = 4, 5, 6, 7, 8
    COL_SID, COL_ACTION = 9, 10

    # Two-tone role badge — client = blue, server = violet. Matches the
    # L2 tab's per-protocol colour convention without being noisy.
    _ROLE_COLORS = {
        "client": "#2563eb",
        "server": "#7c3aed",
    }
    _PROTO_COLORS = {
        "raw":  "#334155",
        "http": "#0891b2",
        "dns":  "#16a34a",   # accepted via API even if dialog hides it
        "sip":  "#db2777",
    }

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self._parent_window = parent_window
        self._fetch_worker: Optional[QThread] = None
        self._sessions_cache: List[Dict[str, Any]] = []
        self._unsupported: bool = False
        self._unsupported_reason: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)

        # ── Action bar ────────────────────────────────────────────────
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border: 1px solid #e5e7eb; border-bottom: none; }"
        )
        bar = QHBoxLayout(action_bar)
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(6)

        _BTN_H = 24
        self._start_btn = QPushButton("Start session…")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setFixedHeight(_BTN_H)
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "font-weight: 600; padding: 0 14px; border-radius: 5px; } "
            "QPushButton:hover { background-color: #15803d; }"
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        bar.addWidget(self._start_btn)

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
            "Stop every stateful-TCP session on this server."
        )
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        bar.addWidget(self._stop_all_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setFixedHeight(_BTN_H)
        self._refresh_btn.setStyleSheet(_neutral_css)
        self._refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self._refresh_btn)

        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self._info_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar.addWidget(self._info_label, 1)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter: role / protocol / target / sid…")
        self._filter_edit.setFixedHeight(_BTN_H)
        self._filter_edit.setFixedWidth(240)
        self._filter_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #cbd5e1; border-radius: 4px; "
            "padding: 2px 6px; }"
        )
        self._filter_edit.textChanged.connect(self._apply_session_filter)
        bar.addWidget(self._filter_edit)

        self._count_chip = QLabel("—")
        self._count_chip.setAlignment(Qt.AlignCenter)
        self._set_count_chip(0, 0)
        bar.addWidget(self._count_chip)

        outer.addWidget(action_bar)

        # ── Session table ─────────────────────────────────────────────
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setColumnWidth(self.COL_STATUS, 96)
        self._table.setColumnWidth(self.COL_ROLE, 70)
        self._table.setColumnWidth(self.COL_PROTO, 86)
        self._table.setColumnWidth(self.COL_TARGET, 170)
        self._table.setColumnWidth(self.COL_CONNS, 80)
        self._table.setColumnWidth(self.COL_TX, 92)
        self._table.setColumnWidth(self.COL_RX, 92)
        self._table.setColumnWidth(self.COL_RTT, 84)
        self._table.setColumnWidth(self.COL_UPTIME, 84)
        self._table.setColumnWidth(self.COL_SID, 132)
        self._table.setColumnWidth(self.COL_ACTION, 60)
        # Right-align numeric headers to sit over right-aligned values.
        for col in (self.COL_CONNS, self.COL_TX, self.COL_RX,
                    self.COL_RTT, self.COL_UPTIME):
            hi = self._table.horizontalHeaderItem(col)
            if hi is not None:
                hi.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._table.setToolTip(
            "Hover the Status cell of any row to see the full counter "
            "breakdown (protocol-specific bins + last_error). The bytes "
            "columns are formatted (KB / MB) — exact values are in the "
            "tooltip too."
        )
        outer.addWidget(self._table, 1)

        # ── Poll timer ────────────────────────────────────────────────
        self._timer = QTimer()
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(300, self.refresh)

    # ------------------------------------------------------------------
    def _get_server_url(self) -> Optional[str]:
        """Reuse the devices tab's resolver — same path the L2 tab uses."""
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
        """Pull /api/stateful_tcp/sessions and re-render the table."""
        url = self._get_server_url()
        if not url:
            self._info_label.setText("No server URL configured.")
            return
        try:
            if self._fetch_worker is not None and self._fetch_worker.isRunning():
                return
        except RuntimeError:
            self._fetch_worker = None

        worker = _JsonFetchWorker(
            f"{url}/api/stateful_tcp/sessions", timeout_s=3.0
        )
        # Mirror the L2 tab's keepalive pin — prevents the PyQt5 5.15.11
        # + Python 3.14 QThread teardown race. See
        # utils/qthread_keepalive.py.
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
        # Just clear the guard; lifetime is owned by the keepalive
        # registry. See L2 tab's note for why we don't deleteLater().
        self._fetch_worker = None

    def _on_refresh_ok(self, payload, _code: int):
        sessions = (payload or {}).get("sessions") or []
        self._sessions_cache = sessions
        self._render_sessions(sessions)
        running = sum(1 for s in sessions if s.get("running"))
        self._set_count_chip(running, len(sessions))
        self._info_label.setText("")
        self._info_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self._exit_unsupported_mode()

    def _set_count_chip(self, running: int, total: int):
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
        if http_code == 404:
            self._enter_unsupported_mode(
                "Server doesn't expose /api/stateful_tcp/* — upgrade "
                "netgen-server to a build that includes the stateful-TCP "
                "blueprint."
            )
            return
        if http_code in (401, 403):
            self._info_label.setStyleSheet("color: #b45309; font-size: 11px;")
            self._info_label.setText(
                f"Auth failed ({http_code}). Set NETGEN_AUTH_TOKEN "
                "(operator or admin role) and restart the client."
            )
            return
        self._info_label.setStyleSheet("color: #b45309; font-size: 11px;")
        self._info_label.setText(f"Fetch failed: {msg[:120]}")

    def _enter_unsupported_mode(self, reason: str):
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
        self._start_btn.setToolTip(reason)
        try:
            self._timer.setInterval(60_000)
        except Exception:
            pass
        self._render_sessions([])

    def _exit_unsupported_mode(self):
        if not getattr(self, "_unsupported", False):
            return
        self._unsupported = False
        self._unsupported_reason = ""
        self._start_btn.setToolTip("")
        try:
            self._timer.setInterval(self.POLL_INTERVAL_MS)
        except Exception:
            pass

    # ----- formatters --------------------------------------------------

    @staticmethod
    def _fmt_count(n) -> str:
        try:
            return f"{int(n):,}"
        except (TypeError, ValueError):
            return str(n)

    @staticmethod
    def _fmt_bytes(n) -> str:
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

    @staticmethod
    def _fmt_rtt_ms(counters: Dict[str, Any]) -> str:
        """Prefer kernel RTT (TCP_INFO) when populated — it's more
        precise than the userspace average. Both default to 0 off-Linux
        or when no samples have landed yet."""
        krtt_us = counters.get("avg_kernel_rtt_us") or 0
        if krtt_us:
            return f"{(krtt_us / 1000.0):.2f} ms"
        rtt_ms = counters.get("avg_rtt_ms") or 0
        if rtt_ms:
            return f"{rtt_ms:.2f} ms"
        return "—"

    def _build_target_label(self, sess: Dict[str, Any]) -> str:
        """`<ip>:<port>` for whichever role this session is."""
        cfg = sess.get("config") or {}
        role = (sess.get("role") or "").lower()
        if role == "server":
            return f"{cfg.get('listen_ip', '?')}:{cfg.get('listen_port', '?')}"
        return f"{cfg.get('dst_ip', '?')}:{cfg.get('dst_port', '?')}"

    def _build_status_tooltip(self, sess: Dict[str, Any]) -> str:
        """Full breakdown for the Status cell tooltip — the place where
        every protocol-specific counter bin and last_error surface."""
        c = sess.get("counters") or {}
        cfg = sess.get("config") or {}
        proto = (sess.get("protocol") or cfg.get("protocol") or "raw").lower()
        lines = [
            f"conns_attempted={self._fmt_count(c.get('conns_attempted', 0))}",
            f"conns_established={self._fmt_count(c.get('conns_established', 0))}",
            f"conns_failed={self._fmt_count(c.get('conns_failed', 0))}",
            f"bytes_tx={self._fmt_bytes(c.get('bytes_tx', 0))}  bytes_rx={self._fmt_bytes(c.get('bytes_rx', 0))}",
            f"handshake_ms={c.get('avg_handshake_ms', 0):.2f}  rtt_ms={c.get('avg_rtt_ms', 0):.2f} (n={c.get('rtt_samples', 0)})",
        ]
        if c.get("kernel_rtt_samples"):
            lines.append(
                f"kernel_rtt_us={c.get('avg_kernel_rtt_us', 0):.0f} "
                f"(TCP_INFO, n={c.get('kernel_rtt_samples', 0)})"
            )
        if c.get("retransmits_total"):
            lines.append(f"retransmits_total={c.get('retransmits_total')}")
        if proto == "http":
            lines.append(
                f"http: 2xx={c.get('http_status_2xx', 0)} "
                f"other={c.get('http_status_other', 0)}"
            )
        elif proto == "dns":
            lines.append(
                f"dns: noerror={c.get('dns_noerror', 0)} "
                f"nxdomain={c.get('dns_nxdomain', 0)} "
                f"servfail={c.get('dns_servfail', 0)} "
                f"other={c.get('dns_other', 0)}"
            )
        elif proto == "sip":
            lines.append(
                f"sip: 2xx={c.get('sip_2xx', 0)} 3xx={c.get('sip_3xx', 0)} "
                f"4xx={c.get('sip_4xx', 0)} 5xx={c.get('sip_5xx', 0)} "
                f"other={c.get('sip_other', 0)}"
            )
        err = c.get("last_error") or ""
        if err:
            lines.append(f"last_error: {err}")
        return "\n".join(lines)

    def _render_sessions(self, sessions: List[Dict[str, Any]]):
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)

        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(sessions))
        for row, sess in enumerate(sessions):
            counters = sess.get("counters", {}) or {}
            session_id = str(sess.get("session_id", ""))
            role = (sess.get("role") or "").lower()
            cfg = sess.get("config") or {}
            proto = (sess.get("protocol") or cfg.get("protocol") or "raw").lower()
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
                    item.setData(Qt.UserRole, session_id)
                self._table.setItem(row, col, item)

            # Status pill — coloured dot + word. Tooltip on this cell
            # carries the FULL counter dump (see _build_status_tooltip).
            _set(self.COL_STATUS,
                 ("● Running" if running else "● Stopped"),
                 color=QColor("#16a34a") if running else QColor("#94a3b8"),
                 align=Qt.AlignCenter, sid=True,
                 tooltip=self._build_status_tooltip(sess))

            # Role badge — client = blue, server = violet.
            _set(self.COL_ROLE, role.upper() if role else "—",
                 color=QColor(self._ROLE_COLORS.get(role, "#334155")),
                 align=Qt.AlignCenter, font=_bold_font())

            # Protocol badge — TLS marker glued on so the operator can
            # tell at a glance if encryption is wrapping the protocol.
            tls = bool(cfg.get("tls"))
            proto_text = (proto + "+tls").upper() if tls else proto.upper()
            _set(self.COL_PROTO, proto_text,
                 color=QColor(self._PROTO_COLORS.get(proto, "#334155")),
                 align=Qt.AlignCenter, font=_bold_font())

            target = self._build_target_label(sess)
            target_tip = (
                f"vrf={cfg.get('vrf') or '—'}  "
                f"src_ip={cfg.get('src_ip') or '—'}"
                if role == "client"
                else f"vrf={cfg.get('vrf') or '—'}  mode={cfg.get('mode') or '—'}"
            )
            _set(self.COL_TARGET, target, tooltip=target_tip)

            _set(self.COL_CONNS,
                 self._fmt_count(counters.get("conns_established", 0)),
                 align=Qt.AlignRight)

            # Exact bytes go into the tooltip so the formatted column
            # stays glanceable but the operator can still pull raw
            # numbers without going to the API.
            tx = counters.get("bytes_tx", 0)
            rx = counters.get("bytes_rx", 0)
            _set(self.COL_TX, self._fmt_bytes(tx),
                 align=Qt.AlignRight, tooltip=f"{int(tx):,} bytes")
            _set(self.COL_RX, self._fmt_bytes(rx),
                 align=Qt.AlignRight, tooltip=f"{int(rx):,} bytes")

            _set(self.COL_RTT, self._fmt_rtt_ms(counters),
                 align=Qt.AlignRight)

            _set(self.COL_UPTIME,
                 self._fmt_uptime(counters.get("uptime_s", 0)),
                 align=Qt.AlignRight)

            short_sid = (session_id[:10] + "…") if len(session_id) > 11 else session_id
            _set(self.COL_SID, short_sid, font=mono,
                 color=QColor("#64748b"), tooltip=session_id)

            if running:
                btn = QPushButton("Stop")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(
                    "QPushButton { background-color: #ffffff; "
                    "color: #b91c1c; border: 1px solid #fca5a5; "
                    "padding: 1px 8px; border-radius: 4px; "
                    "font-size: 10px; } "
                    "QPushButton:hover { background-color: #fef2f2; }"
                )
                # Default-arg lambda captures THIS row's sid (closure-
                # in-loop trap otherwise).
                btn.clicked.connect(
                    (lambda _checked=False, _sid=session_id:
                     self._stop_session_by_id(_sid))
                )
                self._table.setCellWidget(row, self.COL_ACTION, btn)
            else:
                self._table.setItem(row, self.COL_ACTION, QTableWidgetItem(""))

        self._table.setSortingEnabled(was_sorting)
        self._apply_session_filter()

    def _apply_session_filter(self, *_args) -> None:
        needle = ""
        try:
            needle = self._filter_edit.text().strip().lower()
        except Exception:
            return
        for row in range(self._table.rowCount()):
            if not needle:
                self._table.setRowHidden(row, False)
                continue
            hay: List[str] = []
            for col in (self.COL_ROLE, self.COL_PROTO,
                        self.COL_TARGET, self.COL_SID):
                item = self._table.item(row, col)
                if item is not None:
                    hay.append(item.text().lower())
                    ud = item.data(Qt.UserRole)
                    if isinstance(ud, str):
                        hay.append(ud.lower())
            self._table.setRowHidden(row, not any(needle in h for h in hay))

    # ----- stop paths --------------------------------------------------

    def _stop_session_by_id(self, session_id: str) -> None:
        if not session_id:
            return
        url = self._get_server_url()
        if not url:
            return
        try:
            requests.post(
                f"{url.rstrip('/')}/api/stateful_tcp/stop",
                json={"session_id": session_id},
                headers=self._auth_headers(), timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[STATEFUL TCP] per-row stop failed for {session_id}: {exc}")
        QTimer.singleShot(150, self.refresh)

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
        sids: List[str] = []
        for row in rows:
            it = self._table.item(row, self.COL_STATUS)
            if it is None:
                continue
            sid = it.data(Qt.UserRole)
            if sid:
                sids.append(str(sid))
        for sid in sids:
            try:
                requests.post(
                    f"{url}/api/stateful_tcp/stop",
                    json={"session_id": sid},
                    headers=self._auth_headers(), timeout=5,
                )
            except Exception as exc:
                logger.debug(f"[STATEFUL TCP] stop {sid} failed: {exc}")
        QTimer.singleShot(150, self.refresh)

    def _on_stop_all(self):
        url = self._get_server_url()
        if not url:
            return
        confirm = QMessageBox.question(
            self, "Stop all stateful-TCP sessions",
            "Stop every running stateful-TCP session on the server?\n"
            "This affects every operator currently using the feature.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            requests.post(
                f"{url}/api/stateful_tcp/stop", json={},
                headers=self._auth_headers(), timeout=10,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Stop all", f"Request failed: {exc}")
            return
        QTimer.singleShot(150, self.refresh)

    # ----- start path --------------------------------------------------

    def _on_start_clicked(self):
        if getattr(self, "_unsupported", False):
            QMessageBox.information(
                self, "Stateful TCP unavailable",
                self._unsupported_reason or
                "The server doesn't expose /api/stateful_tcp/* — upgrade "
                "netgen-server to a build that includes the blueprint."
            )
            return

        url = self._get_server_url()
        if not url:
            QMessageBox.warning(self, "No server", "No server URL configured.")
            return

        dlg = _StatefulTcpConfigDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        payload = dlg.accepted_payload()
        if not payload:
            return
        body = payload["body"]

        try:
            r = requests.post(
                f"{url}/api/stateful_tcp/start",
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

        if r.status_code == 404:
            msg = (
                "The server doesn't expose /api/stateful_tcp/* — upgrade "
                "netgen-server to a build that includes the blueprint."
            )
            self._enter_unsupported_mode(msg)
            QMessageBox.information(self, "Stateful TCP unavailable", msg)
            return
        if r.status_code in (401, 403):
            QMessageBox.warning(
                self, "Authentication failed",
                f"Server returned {r.status_code}. Set "
                f"NETGEN_AUTH_TOKEN with operator-or-admin role and "
                f"restart the client."
            )
            return
        try:
            err = r.json().get("error") or r.text[:200]
        except Exception:
            err = r.text[:200] if r.text else "(no body)"
        QMessageBox.warning(
            self, "Start failed",
            f"HTTP {r.status_code}: {err}"
        )

    # ------------------------------------------------------------------
    def cleanup_threads(self):
        """Stop the poll timer + drain the fetch worker on app close."""
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
