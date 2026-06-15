"""widgets/rdma_topology_dialog.py — v0.4.0 RDMA Topology Test dialog.

The N×M companion to RdmaBlastFlowDialog. Closes the largest
functional gap vs. Ixia/Spirent for RDMA testing in a software-only
setup (see Help → Install Guide §10d for the comparison).

Operator workflow:

    1. Tools → RDMA → Topology Test…
    2. Pick a topology shape (single / fan-in / fan-out / mesh / pairwise)
    3. Enter SERVER endpoints (one per line: ``<tg_url> <device>`` [opts])
    4. Enter CLIENT endpoints similarly
    5. Adjust the shared workload params (msg_size, qp_count, …)
    6. Click Start → dialog spawns N×M perftest pairs via existing
       /api/rdma/perftest/start endpoints, aggregates stats in a
       per-pair grid with a TOTAL roll-up row.

Endpoint line format (one per line in the QPlainTextEdit):

    http://host:5050 mlx5_0
    http://host:5050 mlx5_0 port=1 gid=3
    http://host:5050 mlx5_0 port=2 gid=0 label=my-server

Lines starting with ``#`` are comments. Blank lines ignored.
"""
from __future__ import annotations

import shlex
import uuid
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from utils.rdma_topology import (
    ALL_SHAPES, DEFAULT_BASE_LISTEN_PORT, RdmaPairPlan,
    RdmaTopologyEndpoint, RdmaTopologySpec,
    SHAPE_FAN_IN, SHAPE_FAN_OUT, SHAPE_MESH, SHAPE_PAIRWISE, SHAPE_SINGLE,
    aggregate_stats, client_start_payload, expand_pairs,
    server_start_payload, validate_spec,
)
from widgets.rdma_blast_flow_dialog import (
    _DEFAULT_DURATION_SECS, _DEFAULT_GID_INDEX, _DEFAULT_MSG_SIZE,
    _DEFAULT_MTU_CODE, _DEFAULT_QP_COUNT, _DEFAULT_TX_DEPTH,
    _MTU_OPTIONS, _PORT_FIELD_TOOLTIP, _TESTS,
    _get_async, _post_async,
)


_SHAPE_LABELS = [
    (SHAPE_SINGLE,   "Single (1 ↔ 1)"),
    (SHAPE_FAN_IN,   "Fan-in (N clients → 1 server)"),
    (SHAPE_FAN_OUT,  "Fan-out (1 server → N clients)"),
    (SHAPE_MESH,     "Mesh (N × M cross-product)"),
    (SHAPE_PAIRWISE, "Pairwise (N ↔ N parallel)"),
]


# ─────────────────────────────────── endpoint-line parser ──────────────


def parse_endpoint_line(line: str) -> Optional[RdmaTopologyEndpoint]:
    """Parse one endpoint line. Returns None for blank/comment lines.
    Raises ValueError on malformed lines (caller catches + surfaces
    in the dialog).

    Grammar (loose):
        <tg_url> <device> [key=value ...]
    Supported keys: port, gid, label.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    try:
        tokens = shlex.split(s)
    except ValueError as e:
        raise ValueError(f"shell-style parse error: {e}")
    if len(tokens) < 2:
        raise ValueError(
            f"need at least <tg_url> <device>; got {len(tokens)} token(s)"
        )
    tg_url, device = tokens[0], tokens[1]
    if not (tg_url.startswith("http://") or tg_url.startswith("https://")):
        raise ValueError(f"tg_url must start with http(s)://; got {tg_url!r}")
    if not device:
        raise ValueError("device cannot be empty")

    port, gid, label = 1, 3, None
    for kv in tokens[2:]:
        if "=" not in kv:
            raise ValueError(
                f"trailing token {kv!r} not in key=value form"
            )
        k, v = kv.split("=", 1)
        if k == "port":
            try:
                port = int(v)
            except ValueError:
                raise ValueError(f"port must be int; got {v!r}")
        elif k == "gid":
            try:
                gid = int(v)
            except ValueError:
                raise ValueError(f"gid must be int; got {v!r}")
        elif k == "label":
            label = v
        else:
            raise ValueError(
                f"unknown key {k!r} (want one of: port, gid, label)"
            )
    return RdmaTopologyEndpoint(
        tg_url=tg_url, device=device, ib_port=port, gid_index=gid,
        label=label,
    )


def parse_endpoint_block(text: str) -> Tuple[List[RdmaTopologyEndpoint], List[str]]:
    """Parse a multi-line block. Returns (endpoints, errors).
    Errors are per-line — one entry per malformed line. The dialog
    surfaces them in the status banner so the operator can fix all
    at once."""
    eps: List[RdmaTopologyEndpoint] = []
    errs: List[str] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        try:
            ep = parse_endpoint_line(raw)
        except ValueError as e:
            errs.append(f"line {i}: {e}")
            continue
        if ep is not None:
            eps.append(ep)
    return eps, errs


# ─────────────────────────────────── the dialog ────────────────────────


class RdmaTopologyDialog(QDialog):
    """N×M RDMA test orchestrator. Same workload knobs as the
    single-pair RdmaBlastFlowDialog, but with endpoint groups +
    a topology shape + per-pair stats aggregation."""

    def __init__(
        self,
        parent=None,
        *,
        known_servers: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("RDMA Topology Test")
        self.setMinimumSize(720, 640)
        self._plans: List[RdmaPairPlan] = []
        # Per-pair job ids: {pair_index: {"server": job_id, "client": job_id}}
        self._pair_jobs: Dict[int, Dict[str, Optional[str]]] = {}
        # Last-known job dicts keyed by job_id (for aggregate_stats).
        self._latest_jobs: Dict[str, Optional[dict]] = {}
        # Polling.
        self._poll_timer: Optional[QTimer] = None
        # v0.5.143: list of (url, label) tuples for the "Pick from
        # servers…" picker. Populated by the menu handler from the
        # main window's registered server set. Empty list = no picker
        # button rendered (operator still types lines by hand).
        self._known_servers: List[Tuple[str, str]] = list(known_servers or [])
        # v0.5.150: per-TG state_ids from any pre-flight Apply.
        # Cleaned up on dialog close.
        self._preflight_state_ids: Dict[str, set] = {}

        self._build_ui()

    # ────────────────────────── UI construction ───────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Same compact GroupBox stylesheet as the single-pair dialog.
        self.setStyleSheet(
            "QGroupBox {"
            "  font-weight: 600; color: #334155;"
            "  border: 1px solid #cbd5e1; border-radius: 4px;"
            "  margin-top: 9px; padding: 6px 8px 8px 8px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin; subcontrol-position: top left;"
            "  left: 8px; padding: 0 4px;"
            "}"
        )

        # Header
        hdr = QLabel(
            "<span style='font-size:13px; font-weight:600; color:#0f172a;'>"
            "RDMA Topology Test</span>"
            "&nbsp;&nbsp;"
            "<span style='color:#64748b; font-size:11px;'>"
            "N×M perftest orchestrator — endpoint groups + topology shape, "
            "aggregated stats. See Help → Install Guide §10d for the "
            "Ixia comparison."
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        # ── Topology shape picker + pair-count preview
        shape_box = QGroupBox("Topology")
        sh = QHBoxLayout(shape_box)
        sh.setContentsMargins(8, 4, 8, 4)
        sh.setSpacing(12)
        self._shape_group = QButtonGroup(self)
        self._shape_buttons: Dict[str, QRadioButton] = {}
        for sid, slabel in _SHAPE_LABELS:
            rb = QRadioButton(slabel)
            self._shape_group.addButton(rb)
            self._shape_buttons[sid] = rb
            rb.toggled.connect(self._refresh_pair_count)
            sh.addWidget(rb)
        # Default: mesh — the most general shape, useful demo
        self._shape_buttons[SHAPE_MESH].setChecked(True)
        sh.addStretch(1)
        self._pair_count_label = QLabel("<span style='color:#475569;'>0 pairs</span>")
        sh.addWidget(self._pair_count_label)
        root.addWidget(shape_box)

        # ── Endpoint editors — two side-by-side text panes
        endpoints_box = QGroupBox("Endpoints (one per line: <tg_url> <device> [port=N] [gid=N] [label=NAME])")
        eg = QGridLayout(endpoints_box)
        eg.setContentsMargins(8, 4, 8, 6)
        eg.setHorizontalSpacing(8)
        eg.setVerticalSpacing(4)

        # v0.5.143: Each side gets a header row with a "Pick from
        # servers…" button so the operator doesn't have to type
        # `http://srv01:5050 mlx5_0` by hand. The picker fetches
        # /api/rdma/devices on each known TG and presents checkboxes
        # for every (server, HCA) pair.
        def _make_side_header(title: str, side: str) -> QWidget:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(QLabel(f"<b>{title}</b>"))
            h.addStretch(1)
            btn = QPushButton("Pick from servers…")
            btn.setEnabled(bool(self._known_servers))
            if not self._known_servers:
                btn.setToolTip(
                    "No registered TGs visible from this dialog. "
                    "Add servers via the main window's Server Tree, "
                    "then reopen this dialog to use the picker."
                )
            else:
                btn.setToolTip(
                    "Browse RDMA HCAs on every registered TG and "
                    "append selected (server, device) lines to this "
                    "list."
                )
            btn.clicked.connect(lambda _=False, s=side: self._open_endpoint_picker(s))
            h.addWidget(btn)
            return box

        eg.addWidget(_make_side_header("Server endpoints", "server"), 0, 0)
        eg.addWidget(_make_side_header("Client endpoints", "client"), 0, 1)

        # v0.5.147: one-click loopback row (added at the BOTTOM of
        # the endpoints box, below the hint label — see the
        # eg.addWidget(loopback_row, 3, ...) call after the hint).
        # Pick a single (TG, HCA) and have both editors set to the
        # same line. The canonical RDMA smoke test — if this fails,
        # the problem is in the RDMA stack itself (GID / port state
        # / driver) rather than link reachability between two
        # ports.
        self._loopback_btn = QPushButton(
            "↔  Same-host test (loopback or two HCAs)"
        )
        self._loopback_btn.setEnabled(bool(self._known_servers))
        self._loopback_btn.setToolTip(
            "Opens a same-host RDMA test picker with two modes:\n\n"
            "• Same-HCA loopback (default) — both sides bind to "
            "the same HCA. perftest's verbs layer bounces packets "
            "internally; no wire, switch, or peer NIC needed. "
            "Use this first when troubleshooting — if it fails, "
            "the issue is in the driver / GID / port state.\n\n"
            "• Two HCAs same host (toggle) — server and client "
            "perftest bind to different RoCE devices on one TG "
            "(e.g. rocep43s0f0 ↔ rocep43s0f1). Exercises the "
            "wire/driver path between sibling devices. Requires a "
            "loopback cable, shared switch, or firmware internal "
            "port-to-port loopback."
        )
        if not self._known_servers:
            self._loopback_btn.setToolTip(
                "Disabled: no registered TGs visible. Add servers "
                "via the main window's Server Tree, then reopen "
                "this dialog."
            )
        self._loopback_btn.clicked.connect(self._open_loopback_picker)

        self._server_edit = QPlainTextEdit()
        self._server_edit.setPlaceholderText(
            "http://srv01:5050 mlx5_0\n"
            "http://srv01:5050 mlx5_1\n"
            "# comment lines start with #"
        )
        self._server_edit.setMaximumHeight(110)
        self._server_edit.textChanged.connect(self._refresh_pair_count)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._server_edit.setFont(mono)

        self._client_edit = QPlainTextEdit()
        self._client_edit.setPlaceholderText(
            "http://srv04:5050 mlx5_0\n"
            "http://srv05:5050 mlx5_0\n"
        )
        self._client_edit.setMaximumHeight(110)
        self._client_edit.textChanged.connect(self._refresh_pair_count)
        self._client_edit.setFont(mono)

        eg.addWidget(self._server_edit, 1, 0)
        eg.addWidget(self._client_edit, 1, 1)

        # v0.5.143: tiny inline note clarifying that the second token
        # is the RDMA HCA name (mlx5_0, mlx5_1, …), NOT an Ethernet
        # interface (ens2f0np0). perftest addresses the HCA directly
        # via libibverbs — the Ethernet iface dropdowns elsewhere in
        # the GUI (DPDK / scapy) are not the same thing.
        hint = QLabel(
            "<span style='color:#64748b; font-size:11px;'>"
            "<b>device</b> = RDMA HCA name (e.g. <code>mlx5_0</code>) — "
            "this is the InfiniBand verbs device, NOT an Ethernet "
            "interface (<code>ens2f0np0</code>). perftest addresses "
            "the HCA directly via libibverbs."
            "</span>"
        )
        hint.setWordWrap(True)
        eg.addWidget(hint, 2, 0, 1, 2)

        # v0.5.147: loopback row sits at the BOTTOM of the
        # endpoints box, right-aligned so it sits below the
        # Client editor without crowding the labels above.
        loopback_row = QWidget()
        _lh = QHBoxLayout(loopback_row)
        _lh.setContentsMargins(0, 0, 0, 0)
        _lh.addStretch(1)
        _lh.addWidget(self._loopback_btn)
        eg.addWidget(loopback_row, 3, 0, 1, 2)

        eg.setColumnStretch(0, 1)
        eg.setColumnStretch(1, 1)
        root.addWidget(endpoints_box)

        # ── Shared workload params — same compact 2-column grid as
        # the single-pair dialog
        test_box = QGroupBox("Shared workload (applies to every pair)")
        tg = QGridLayout(test_box)
        tg.setContentsMargins(8, 4, 8, 4)
        tg.setHorizontalSpacing(8)
        tg.setVerticalSpacing(4)

        self._test_combo = QComboBox()
        for tid, label, _group in _TESTS:
            self._test_combo.addItem(label, userData=tid)

        self._mtu_combo = QComboBox()
        for code, label in _MTU_OPTIONS:
            self._mtu_combo.addItem(label, userData=code)
        self._mtu_combo.setCurrentIndex(len(_MTU_OPTIONS) - 1)

        self._msg_size_spin = QSpinBox()
        self._msg_size_spin.setRange(2, 16 * 1024 * 1024)
        self._msg_size_spin.setSingleStep(1024)
        self._msg_size_spin.setValue(_DEFAULT_MSG_SIZE)
        self._msg_size_spin.setSuffix(" B")
        self._msg_size_spin.setFixedWidth(120)

        self._tx_depth_spin = QSpinBox()
        self._tx_depth_spin.setRange(1, 4096)
        self._tx_depth_spin.setValue(_DEFAULT_TX_DEPTH)
        self._tx_depth_spin.setFixedWidth(120)

        self._qp_count_spin = QSpinBox()
        self._qp_count_spin.setRange(1, 131072)
        self._qp_count_spin.setValue(_DEFAULT_QP_COUNT)
        self._qp_count_spin.setFixedWidth(120)

        self._gid_index_spin = QSpinBox()
        self._gid_index_spin.setRange(0, 255)
        self._gid_index_spin.setValue(_DEFAULT_GID_INDEX)
        self._gid_index_spin.setFixedWidth(120)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 3600)
        self._duration_spin.setValue(_DEFAULT_DURATION_SECS)
        self._duration_spin.setSuffix(" sec")
        self._duration_spin.setFixedWidth(120)

        self._base_port_spin = QSpinBox()
        self._base_port_spin.setRange(1024, 65000)
        self._base_port_spin.setValue(DEFAULT_BASE_LISTEN_PORT)
        self._base_port_spin.setFixedWidth(120)
        self._base_port_spin.setToolTip(
            "Base TCP port for the FIRST pair's perftest control "
            "channel. Each subsequent pair gets base + index. "
            "Default 18516 (perftest default 18515 + 1)."
        )

        self._bidir_check = QCheckBox("Bidirectional (-b)")
        self._cpu_util_check = QCheckBox("Report CPU utilisation (--cpu_util)")

        # 2-col grid
        tg.addWidget(QLabel("Test type:"), 0, 0, Qt.AlignRight)
        tg.addWidget(self._test_combo,     0, 1)
        tg.addWidget(QLabel("MTU:"),       0, 2, Qt.AlignRight)
        tg.addWidget(self._mtu_combo,      0, 3)
        tg.addWidget(QLabel("Message size:"), 1, 0, Qt.AlignRight)
        tg.addWidget(self._msg_size_spin,     1, 1)
        tg.addWidget(QLabel("TX depth:"),     1, 2, Qt.AlignRight)
        tg.addWidget(self._tx_depth_spin,     1, 3)
        tg.addWidget(QLabel("QP count:"),  2, 0, Qt.AlignRight)
        tg.addWidget(self._qp_count_spin,  2, 1)
        tg.addWidget(QLabel("GID index:"), 2, 2, Qt.AlignRight)
        tg.addWidget(self._gid_index_spin, 2, 3)
        tg.addWidget(QLabel("Duration:"),  3, 0, Qt.AlignRight)
        tg.addWidget(self._duration_spin,  3, 1)
        tg.addWidget(QLabel("Base port:"), 3, 2, Qt.AlignRight)
        tg.addWidget(self._base_port_spin, 3, 3)
        cb_row = QHBoxLayout()
        cb_row.setSpacing(16)
        cb_row.addWidget(self._bidir_check)
        cb_row.addWidget(self._cpu_util_check)
        cb_row.addStretch(1)
        cb_holder = QWidget()
        cb_holder.setLayout(cb_row)
        tg.addWidget(cb_holder, 4, 0, 1, 4)
        tg.setColumnStretch(1, 1)
        tg.setColumnStretch(3, 1)
        root.addWidget(test_box)

        # ── Action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        # v0.5.150: pre-flight check button. Probes every parsed
        # endpoint, surfaces same-subnet trap + DOWN ports, and
        # offers temporary test IPs.
        self._preflight_btn = QPushButton("🔍 Pre-flight check")
        self._preflight_btn.setToolTip(
            "Probe every endpoint for port state, link layer, IP "
            "addresses, and RoCEv2 GIDs. Detects the same-host "
            "same-subnet routing trap and offers temporary test "
            "IPs (runtime only — gone on reboot)."
        )
        self._preflight_btn.clicked.connect(self._on_preflight_clicked)
        action_row.addWidget(self._preflight_btn)
        self._start_btn = QPushButton("Start topology")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn = QPushButton("Stop all")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        action_row.addWidget(self._start_btn)
        action_row.addWidget(self._stop_btn)
        action_row.addSpacing(8)
        self._status_label = QLabel(
            "<span style='color:#64748b;'>"
            "Idle. Add endpoints + click Start topology.</span>"
        )
        self._status_label.setWordWrap(True)
        action_row.addWidget(self._status_label, 1)
        root.addLayout(action_row)

        # ── Per-pair stats grid + TOTAL row
        stats_box = QGroupBox("Per-pair stats")
        sv = QVBoxLayout(stats_box)
        sv.setContentsMargins(8, 4, 8, 4)
        self._stats_table = QTableWidget(0, 6)
        self._stats_table.setHorizontalHeaderLabels(
            ["#", "Server", "Client", "State", "BW Gbps", "MsgRate Mpps"]
        )
        self._stats_table.verticalHeader().setVisible(False)
        self._stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self._stats_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self._stats_table.setMinimumHeight(160)
        sv.addWidget(self._stats_table)
        self._total_label = QLabel(
            "<span style='color:#475569;'>(no run yet)</span>"
        )
        self._total_label.setStyleSheet("padding: 4px 0;")
        sv.addWidget(self._total_label)
        root.addWidget(stats_box, 1)

        self._refresh_pair_count()

    # ────────────────────────── pair-count preview ───────────────────

    def _current_shape(self) -> str:
        for sid, rb in self._shape_buttons.items():
            if rb.isChecked():
                return sid
        return SHAPE_MESH

    # ────────────────────────── v0.5.150 pre-flight ─────────────────

    def _on_preflight_clicked(self) -> None:
        """Parse the current endpoint editors, open the pre-flight
        dialog for them. Same-host groups share one TG URL for
        the test-IP config endpoint."""
        srv_eps, srv_errs = parse_endpoint_block(self._server_edit.toPlainText())
        cli_eps, cli_errs = parse_endpoint_block(self._client_edit.toPlainText())
        if srv_errs or cli_errs:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Pre-flight check",
                "Fix endpoint parse errors before running pre-flight."
                + ("\n\nServer:\n" + "\n".join(srv_errs) if srv_errs else "")
                + ("\n\nClient:\n" + "\n".join(cli_errs) if cli_errs else ""),
            )
            return
        if not srv_eps or not cli_eps:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Pre-flight check",
                "Fill in at least one server endpoint and one "
                "client endpoint.",
            )
            return
        endpoints: List[Tuple[str, str, str, int]] = []
        for i, ep in enumerate(srv_eps):
            endpoints.append(
                (f"Server {i}", ep.tg_url, ep.device, ep.ib_port))
        for i, ep in enumerate(cli_eps):
            endpoints.append(
                (f"Client {i}", ep.tg_url, ep.device, ep.ib_port))
        # Group by tg_url so we apply test IPs ONCE per host. The
        # operator can re-open the preflight if they want to apply
        # per a different TG.
        by_url: Dict[str, List[Tuple[str, str, str, int]]] = {}
        for label, url, hca, port in endpoints:
            by_url.setdefault(url, []).append((label, url, hca, port))
        from widgets.rdma_preflight_dialog import RdmaPreflightDialog
        for url, eps in by_url.items():
            dlg = RdmaPreflightDialog(
                endpoints=eps,
                config_url=url,
                parent=self,
            )
            dlg.exec_()
            sid = dlg.applied_state_id()
            if sid:
                # v0.5.152: honor the "📌 Keep" checkbox — don't
                # track the state_id for auto-cleanup when set.
                if dlg.keep_applied():
                    self._pair_count_label.setText(
                        f"<span style='color:#0369a1;'>"
                        f"Pre-flight applied IPs on {url} "
                        f"(state_id={sid[:8]}). 📌 Keep ON — "
                        f"manual cleanup required."
                        f"</span>"
                    )
                else:
                    self._preflight_state_ids.setdefault(
                        url, set()).add(sid)
                    self._pair_count_label.setText(
                        f"<span style='color:#15803d;'>"
                        f"Pre-flight applied test IPs on {url} "
                        f"(state_id={sid[:8]}). Cleanup on close."
                        f"</span>"
                    )

    # ────────────────────────── v0.5.147 loopback picker ──────────────

    def _open_loopback_picker(self) -> None:
        """One-click same-host setup. Two modes inside the picker:

          * Same-HCA loopback (default) — both endpoint editors
            get the SAME line. Canonical RDMA smoke test.
          * Two-HCA same-host (toggle in picker) — server and
            client editors get DIFFERENT device tokens on the same
            TG URL. Exercises the wire/driver path between sibling
            HCAs (e.g. `rocep…f0` ↔ `rocep…f1`).

        Either way: focused smoke test, replaces existing editor
        content rather than appending."""
        if not self._known_servers:
            return
        picker = _LoopbackPickerDialog(
            servers=self._known_servers,
            parent=self,
        )
        if picker.exec_() != QDialog.Accepted:
            return
        srv_line, cli_line = picker.selected_lines()
        if not srv_line or not cli_line:
            return
        self._server_edit.setPlainText(srv_line)
        self._client_edit.setPlainText(cli_line)

    # ────────────────────────── v0.5.143 endpoint picker ──────────────

    def _open_endpoint_picker(self, side: str) -> None:
        """Open the multi-server endpoint picker for the given side.

        side: "server" or "client" — determines which QPlainTextEdit
        receives the appended lines on accept.
        """
        if side not in ("server", "client"):
            return
        if not self._known_servers:
            return
        target = self._server_edit if side == "server" else self._client_edit
        title = (
            "Pick Server endpoints" if side == "server"
            else "Pick Client endpoints"
        )
        picker = _EndpointPickerDialog(
            servers=self._known_servers,
            title=title,
            parent=self,
        )
        if picker.exec_() != QDialog.Accepted:
            return
        chosen = picker.selected_lines()
        if not chosen:
            return
        existing = target.toPlainText().rstrip()
        merged = chosen if not existing else existing + "\n" + "\n".join(chosen)
        if existing:
            target.setPlainText(merged)
        else:
            target.setPlainText("\n".join(chosen))

    def _refresh_pair_count(self) -> None:
        """Live-update the "X pairs" label as the operator types or
        switches shape. Catches obvious mismatches early
        (e.g. shape=single but 3 endpoints listed)."""
        # Guard: this slot can fire from setChecked() during _build_ui
        # before _pair_count_label / the text editors exist. Safe to
        # bail in that window — the call after _build_ui finishes will
        # render the correct initial state.
        if not hasattr(self, "_pair_count_label"):
            return
        if not hasattr(self, "_server_edit") or not hasattr(self, "_client_edit"):
            return
        srv_eps, srv_errs = parse_endpoint_block(self._server_edit.toPlainText())
        cli_eps, cli_errs = parse_endpoint_block(self._client_edit.toPlainText())
        all_errs = (
            [f"server: {e}" for e in srv_errs]
            + [f"client: {e}" for e in cli_errs]
        )
        if all_errs:
            self._pair_count_label.setText(
                f"<span style='color:#b91c1c;'>"
                f"parse error: {all_errs[0]}</span>"
            )
            return
        if not srv_eps or not cli_eps:
            self._pair_count_label.setText(
                "<span style='color:#64748b;'>0 pairs</span>"
            )
            return
        # Trial expansion to count pairs (validation may still fail)
        try:
            spec = self._spec_from_ui(srv_eps, cli_eps)
            err = validate_spec(spec)
            if err:
                self._pair_count_label.setText(
                    f"<span style='color:#b45309;'>{err}</span>"
                )
                return
            plans = expand_pairs(spec)
            self._pair_count_label.setText(
                f"<span style='color:#0f766e; font-weight:600;'>"
                f"{len(plans)} pair{'s' if len(plans) != 1 else ''}</span>"
            )
        except ValueError as e:
            self._pair_count_label.setText(
                f"<span style='color:#b45309;'>{e}</span>"
            )

    # ────────────────────────── spec building ────────────────────────

    def _common_opts(self) -> dict:
        """Shared workload params passed to every per-pair start."""
        return {
            "msg_size": int(self._msg_size_spin.value()),
            "qp_count": int(self._qp_count_spin.value()),
            "duration": int(self._duration_spin.value()),
            "mtu": int(self._mtu_combo.currentData() or _DEFAULT_MTU_CODE),
            "tx_depth": int(self._tx_depth_spin.value()),
            "gid_index": int(self._gid_index_spin.value()),
            "bidirectional": bool(self._bidir_check.isChecked()),
            "cpu_util": bool(self._cpu_util_check.isChecked()),
            "report_gbits": True,
        }

    def _spec_from_ui(self, srv_eps, cli_eps) -> RdmaTopologySpec:
        return RdmaTopologySpec(
            shape=self._current_shape(),
            server_endpoints=srv_eps,
            client_endpoints=cli_eps,
            test=str(self._test_combo.currentData() or "send_bw"),
            workload_opts=self._common_opts(),
            base_listen_port=int(self._base_port_spin.value()),
        )

    # ────────────────────────── start / stop ─────────────────────────

    def _set_status_error(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#b91c1c;'>{text}</span>"
        )

    def _set_status_ok(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#15803d;'>{text}</span>"
        )

    def _set_status_neutral(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#475569;'>{text}</span>"
        )

    def _on_start_clicked(self) -> None:
        srv_eps, srv_errs = parse_endpoint_block(self._server_edit.toPlainText())
        cli_eps, cli_errs = parse_endpoint_block(self._client_edit.toPlainText())
        if srv_errs:
            self._set_status_error(f"server endpoints — {srv_errs[0]}")
            return
        if cli_errs:
            self._set_status_error(f"client endpoints — {cli_errs[0]}")
            return
        spec = self._spec_from_ui(srv_eps, cli_eps)
        err = validate_spec(spec)
        if err:
            self._set_status_error(err)
            return
        try:
            plans = expand_pairs(spec)
        except ValueError as e:
            self._set_status_error(str(e))
            return

        # Reset per-run state
        self._plans = plans
        self._pair_jobs = {p.pair_index: {"server": None, "client": None}
                           for p in plans}
        self._latest_jobs = {}
        self._populate_stats_table_skeleton(plans)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._set_status_neutral(
            f"Starting {len(plans)} pair{'s' if len(plans) != 1 else ''}…"
        )

        # Fire server-side starts first; client-side starts go in the
        # server-response callbacks so we know the listen_port lined
        # up before the client tries to dial.
        test = spec.test
        workload = spec.workload_opts
        for plan in plans:
            body = server_start_payload(plan, test, workload)
            url = f"{plan.server.tg_url}/api/rdma/perftest/start"
            _post_async(
                self, url, body,
                lambda data, err, _plan=plan: self._on_server_started(_plan, data, err, test, workload),
            )

    def _on_server_started(self, plan: RdmaPairPlan, data: Optional[dict],
                           err: str, test: str, workload: dict) -> None:
        if err or not data or data.get("status") != "started":
            self._set_status_error(
                f"pair #{plan.pair_index}: server start failed — "
                f"{err or (data and data.get('error')) or 'no data'}"
            )
            self._mark_pair_failed(plan.pair_index, "server start failed")
            return
        self._pair_jobs[plan.pair_index]["server"] = data.get("job_id")

        # Now fire the client side. peer_addr = host portion of the
        # server's tg_url (perftest control channel binds there).
        peer = plan.server.tg_url.replace("http://", "").replace("https://", "")
        peer = peer.split(":")[0]
        body = client_start_payload(plan, test, workload, peer_addr=peer)
        url = f"{plan.client.tg_url}/api/rdma/perftest/start"
        _post_async(
            self, url, body,
            lambda d, e, _plan=plan: self._on_client_started(_plan, d, e),
        )

    def _on_client_started(self, plan: RdmaPairPlan, data: Optional[dict],
                           err: str) -> None:
        if err or not data or data.get("status") != "started":
            self._set_status_error(
                f"pair #{plan.pair_index}: client start failed — "
                f"{err or (data and data.get('error')) or 'no data'}"
            )
            self._mark_pair_failed(plan.pair_index, "client start failed")
            return
        self._pair_jobs[plan.pair_index]["client"] = data.get("job_id")
        # Once we have at least one pair fully started, start polling
        # (cheap to call multiple times — the timer-create is guarded).
        self._maybe_start_poll()
        # Update status with progress
        started = sum(
            1 for v in self._pair_jobs.values()
            if v.get("server") and v.get("client")
        )
        self._set_status_neutral(
            f"{started} / {len(self._plans)} pairs started…"
        )

    def _mark_pair_failed(self, pair_index: int, reason: str) -> None:
        if pair_index >= self._stats_table.rowCount():
            return
        self._stats_table.setItem(
            pair_index, 3, QTableWidgetItem(f"FAILED: {reason}")
        )

    def _on_stop_clicked(self) -> None:
        # Best-effort stop on every job we know about. Don't wait for
        # responses — the operator wants the dialog state cleaned up
        # immediately.
        for jobs in self._pair_jobs.values():
            for side, job_id in jobs.items():
                if not job_id:
                    continue
                pair_plan = next(
                    (p for p in self._plans
                     if self._pair_jobs[p.pair_index].get(side) == job_id),
                    None,
                )
                if pair_plan is None:
                    continue
                tg = (pair_plan.server.tg_url if side == "server"
                      else pair_plan.client.tg_url)
                _post_async(
                    self, f"{tg}/api/rdma/perftest/stop",
                    {"job_id": job_id}, lambda *_: None,
                )
        self._stop_poll()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._set_status_neutral("Stop requested — see per-pair grid.")

    # ────────────────────────── stats table ──────────────────────────

    def _populate_stats_table_skeleton(self, plans: List[RdmaPairPlan]) -> None:
        self._stats_table.setRowCount(len(plans))
        for p in plans:
            self._stats_table.setItem(p.pair_index, 0,
                                       QTableWidgetItem(str(p.pair_index)))
            self._stats_table.setItem(p.pair_index, 1,
                                       QTableWidgetItem(p.server.display()))
            self._stats_table.setItem(p.pair_index, 2,
                                       QTableWidgetItem(p.client.display()))
            self._stats_table.setItem(p.pair_index, 3,
                                       QTableWidgetItem("queued"))
            self._stats_table.setItem(p.pair_index, 4, QTableWidgetItem("—"))
            self._stats_table.setItem(p.pair_index, 5, QTableWidgetItem("—"))
        self._total_label.setText(
            f"<span style='color:#475569;'>"
            f"{len(plans)} pair{'s' if len(plans) != 1 else ''} queued. "
            f"Waiting for first stats…</span>"
        )

    def _maybe_start_poll(self) -> None:
        if self._poll_timer is not None:
            return
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_all_jobs)
        self._poll_timer.start(2000)
        self._poll_all_jobs()

    def _stop_poll(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    def _poll_all_jobs(self) -> None:
        """Poll every known job. The aggregator handles missing data
        cleanly — pairs that haven't started yet or that errored out
        just don't contribute to the totals."""
        for plan in self._plans:
            for side in ("server", "client"):
                job_id = self._pair_jobs[plan.pair_index].get(side)
                if not job_id:
                    continue
                tg = (plan.server.tg_url if side == "server"
                      else plan.client.tg_url)
                url = f"{tg}/api/rdma/perftest/job/{job_id}"
                _get_async(
                    self, url,
                    lambda data, err, _jid=job_id: self._on_job_resp(_jid, data, err),
                    timeout=3.0,
                )
        # Refresh aggregate row from whatever's already cached.
        self._refresh_totals()

    def _on_job_resp(self, job_id: str, data: Optional[dict], err: str) -> None:
        if err or not data:
            return
        job = data.get("job") or {}
        self._latest_jobs[job_id] = job
        # Update the corresponding row's State / BW / MsgRate cells.
        for plan in self._plans:
            jobs = self._pair_jobs[plan.pair_index]
            if jobs.get("server") != job_id and jobs.get("client") != job_id:
                continue
            self._update_pair_row(plan.pair_index)
            break
        self._refresh_totals()
        # If all pairs are done, stop polling.
        if self._all_pairs_done():
            self._stop_poll()
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._set_status_ok(
                f"All {len(self._plans)} pair(s) finished."
            )

    def _update_pair_row(self, pair_index: int) -> None:
        jobs_info = self._pair_jobs[pair_index]
        # State: prefer CLIENT side's view since it's the one driving traffic.
        client_job = self._latest_jobs.get(jobs_info.get("client") or "")
        server_job = self._latest_jobs.get(jobs_info.get("server") or "")
        primary = client_job or server_job
        if not primary:
            return
        if primary.get("running"):
            state = "running"
        elif primary.get("returncode") == 0:
            state = "done (rc=0)"
        else:
            rc = primary.get("returncode")
            state = f"done (rc={rc})" if rc is not None else "?"
        bw = primary.get("final_bw_avg_gbps")
        mr = primary.get("final_msg_rate_mpps")
        self._stats_table.setItem(pair_index, 3, QTableWidgetItem(state))
        self._stats_table.setItem(
            pair_index, 4,
            QTableWidgetItem(f"{bw:.2f}" if isinstance(bw, (int, float)) else "—"),
        )
        self._stats_table.setItem(
            pair_index, 5,
            QTableWidgetItem(f"{mr:.4f}" if isinstance(mr, (int, float)) else "—"),
        )

    def _refresh_totals(self) -> None:
        # Use CLIENT-side jobs for aggregation (those report the
        # actual sent throughput; server-side mirrors but is the same
        # number).
        client_jobs = []
        for plan in self._plans:
            cj_id = self._pair_jobs[plan.pair_index].get("client")
            if cj_id:
                client_jobs.append(self._latest_jobs.get(cj_id))
        is_lat = self._test_combo.currentData() and \
                 str(self._test_combo.currentData()).endswith("_lat")
        agg = aggregate_stats(client_jobs, is_lat=bool(is_lat))
        parts = [
            f"{agg['pair_count']} pair{'s' if agg['pair_count'] != 1 else ''}",
            f"{agg['pairs_running']} running",
            f"{agg['pairs_done']} done",
        ]
        if not is_lat and agg["total_bw_avg_gbps"] is not None:
            parts.append(
                f"<b>TOTAL BW: {agg['total_bw_avg_gbps']:.2f} Gbps</b>"
            )
            if agg["total_msg_rate_mpps"] is not None:
                parts.append(
                    f"MsgRate: {agg['total_msg_rate_mpps']:.4f} Mpps"
                )
        elif is_lat and agg["weighted_lat_avg_us"] is not None:
            parts.append(
                f"<b>WEIGHTED LAT AVG: {agg['weighted_lat_avg_us']:.3f} µs</b>"
            )
        if agg.get("any_error"):
            parts.append(
                f"<span style='color:#b91c1c;'>err: {agg['any_error']}</span>"
            )
        self._total_label.setText(
            "<span style='color:#1f2937;'>" + " &nbsp;|&nbsp; ".join(parts)
            + "</span>"
        )

    def _all_pairs_done(self) -> bool:
        if not self._plans:
            return False
        for plan in self._plans:
            jobs = self._pair_jobs[plan.pair_index]
            for side in ("server", "client"):
                job_id = jobs.get(side)
                if not job_id:
                    continue  # never started — skip
                j = self._latest_jobs.get(job_id)
                if not j or j.get("finished_at") is None:
                    return False
        return True

    def closeEvent(self, event) -> None:
        """Stop the poll timer on close so Qt doesn't deliver ticks
        to a deleted widget (the SIGABRT-pattern lesson).

        v0.5.150: also clean up any pre-flight test IPs we applied.
        Fire-and-forget per TG URL."""
        self._stop_poll()
        for url, sids in self._preflight_state_ids.items():
            for sid in sids:
                try:
                    _post_async(
                        self,
                        f"{url.rstrip('/')}/api/rdma/test_ifaces/cleanup",
                        {"state_id": sid},
                        lambda *_a: None,
                    )
                except Exception:
                    pass
        self._preflight_state_ids.clear()
        super().closeEvent(event)


# ─────────────────────────────────── v0.5.143 endpoint picker dialog ──


class _EndpointPickerDialog(QDialog):
    """Multi-server RDMA endpoint picker.

    Renders a tree: one top-level row per known TG, lazily populated
    with one checkable child per RDMA HCA via /api/rdma/devices. On
    accept, ``selected_lines()`` returns the list of
    ``"<tg_url> <device>"`` strings ready to drop into the parent
    dialog's QPlainTextEdit.

    Stays narrow on purpose: device name only. Operators who need
    port/gid/label can still type those by hand after pasting — the
    common case (one port, one GID per HCA) doesn't need ceremony.
    """

    def __init__(
        self,
        servers: List[Tuple[str, str]],
        *,
        title: str = "Pick RDMA endpoints",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(540, 420)
        self._servers: List[Tuple[str, str]] = list(servers)
        self._selected: List[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        hdr = QLabel(
            "<span style='color:#475569;'>"
            "Select one or more <b>(server, HCA)</b> pairs. Accepted "
            "rows append to the endpoint list as "
            "<code>&lt;tg_url&gt; &lt;device&gt;</code> lines."
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Endpoint", "State", "Vendor / FW"])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        root.addWidget(self._tree, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #64748b; font-size: 11px;")
        root.addWidget(self._status)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        for url, label in self._servers:
            self._add_server_row(url, label)

        # Kick off the probes after the dialog is shown so we don't
        # block exec_.
        QTimer.singleShot(0, self._probe_all)

    def _add_server_row(self, url: str, label: str) -> None:
        item = QTreeWidgetItem([f"{label} — {url}", "(probing…)", ""])
        item.setData(0, Qt.UserRole, ("server", url))
        # Top-level rows aren't selectable/checkable — only the
        # devices under them are.
        item.setExpanded(True)
        self._tree.addTopLevelItem(item)

    def _probe_all(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            _, url = item.data(0, Qt.UserRole)
            self._probe_one(item, url)

    def _probe_one(self, parent_item: QTreeWidgetItem, url: str) -> None:
        # Late import — avoid pulling _DpdkApiWorker at module-load
        # time so pure-test envs without PyQt's network plumbing
        # can still import this module.
        from widgets.rdma_blast_flow_dialog import _get_async

        def _on_done(data, err, _parent=parent_item, _url=url):
            # Qt may have already deleted the dialog by the time the
            # response lands (closeEvent during in-flight probe).
            try:
                parent_text = _parent.text(0)
            except RuntimeError:
                return
            if err:
                _parent.setText(1, "error")
                _parent.setToolTip(1, str(err))
                self._set_status(
                    f"{_url}: {err}", error=True,
                )
                return
            devices = (data or {}).get("devices") or []
            if not devices:
                _parent.setText(1, "no HCAs")
                return
            _parent.setText(1, f"{len(devices)} HCA(s)")
            for dev in devices:
                name = dev.get("name", "?")
                vendor = dev.get("vendor", "") or ""
                fw = dev.get("fw_version", "") or ""
                ports = dev.get("ports") or []
                if ports:
                    p = ports[0]
                    state = (p.get("state") or "").upper()
                    rate = p.get("rate") or ""
                    state_str = f"{state} {rate}".strip()
                else:
                    state_str = ""
                child = QTreeWidgetItem([
                    name, state_str,
                    (vendor + " / " + fw).strip(" /"),
                ])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, ("device", _url, name))
                _parent.addChild(child)
            _parent.setExpanded(True)

        _get_async(
            self, f"{url.rstrip('/')}/api/rdma/devices", _on_done,
            timeout=6.0,
        )

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        colour = "#b91c1c" if error else "#64748b"
        self._status.setText(
            f"<span style='color:{colour};'>{msg}</span>"
        )

    def _on_accept(self) -> None:
        chosen: List[str] = []
        for i in range(self._tree.topLevelItemCount()):
            srv = self._tree.topLevelItem(i)
            for j in range(srv.childCount()):
                child = srv.child(j)
                if child.checkState(0) != Qt.Checked:
                    continue
                data = child.data(0, Qt.UserRole)
                if not data or data[0] != "device":
                    continue
                _, url, dev = data
                chosen.append(f"{url} {dev}")
        self._selected = chosen
        self.accept()

    def selected_lines(self) -> List[str]:
        return list(self._selected)


# ─────────────────────────────────── v0.5.147 loopback picker dialog ──


class _LoopbackPickerDialog(QDialog):
    """Same-host RDMA test picker — covers two related modes:

      1. **Same-HCA loopback** (default): the canonical smoke test.
         Both perftest sides use the SAME HCA on the same TG.
         Verbs bounces internally; no wire needed.
      2. **Same-host different HCAs** (toggle the checkbox):
         server + client perftest processes run on the same TG
         but bind to DIFFERENT HCAs (e.g. dual-port NIC
         `rocep43s0f0` ↔ `rocep43s0f1`). Exercises the wire +
         driver path between sibling devices. Whether this works
         depends on whether the two ports are cabled / share a
         switch / have firmware internal-loopback enabled.

    Returns one or two `<tg_url> <hca>` lines via
    `selected_lines() → (server_line, client_line)`. In same-HCA
    mode both strings are identical; in two-HCA mode they differ
    only in the device token.
    """

    def __init__(
        self,
        servers: List[Tuple[str, str]],
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Same-host RDMA Test — Pick HCA(s)")
        self.setMinimumWidth(460)
        self._servers = list(servers)
        # v0.5.148: tuple of two lines. In same-HCA mode they're
        # identical; in different-HCA mode they share the URL but
        # have different device tokens.
        self._chosen_lines: Tuple[str, str] = ("", "")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        hdr = QLabel(
            "<span style='color:#475569;'>"
            "Pick one <b>(TG, HCA)</b> for a same-HCA loopback "
            "(default), or check <b>two HCAs (same host)</b> to "
            "exercise the wire/driver path between two RoCE "
            "devices on one TG (e.g. <code>rocep43s0f0</code> "
            "↔ <code>rocep43s0f1</code>)."
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        grid.addWidget(QLabel("Server (TG):"), 0, 0, Qt.AlignRight)
        self._server_combo = QComboBox()
        for url, label in self._servers:
            self._server_combo.addItem(f"{label} — {url}", userData=url)
        self._server_combo.currentIndexChanged.connect(self._probe_devices)
        grid.addWidget(self._server_combo, 0, 1, 1, 2)

        # v0.5.148: server-side HCA combo. Renamed from the v0.5.147
        # `_device_combo` so the two-HCA mode can refer to it
        # unambiguously.
        grid.addWidget(QLabel("Server HCA:"), 1, 0, Qt.AlignRight)
        self._server_device_combo = QComboBox()
        self._server_device_combo.setMinimumWidth(220)
        self._server_device_combo.addItem("(probing…)", userData=None)
        grid.addWidget(self._server_device_combo, 1, 1, 1, 2)

        # v0.5.148: mode toggle. Hidden by default — operator opts
        # in. When checked the client HCA row enables.
        self._two_hca_check = QCheckBox(
            "Use a DIFFERENT HCA on the client side "
            "(same-host two-port test)"
        )
        self._two_hca_check.setToolTip(
            "Server and client perftest run on the same TG but "
            "bind to different RDMA HCAs. Tests the path between "
            "two RoCE devices on one host (loopback cable, shared "
            "switch, or firmware internal port-to-port "
            "loopback). Use this when you've confirmed single-HCA "
            "loopback works and want to validate the wire too."
        )
        self._two_hca_check.toggled.connect(self._on_two_hca_toggled)
        grid.addWidget(self._two_hca_check, 2, 0, 1, 3)

        # v0.5.148: client-side HCA combo, only enabled when the
        # two-HCA checkbox is set.
        self._client_hca_label = QLabel("Client HCA:")
        self._client_hca_label.setEnabled(False)
        grid.addWidget(self._client_hca_label, 3, 0, Qt.AlignRight)
        self._client_device_combo = QComboBox()
        self._client_device_combo.setMinimumWidth(220)
        self._client_device_combo.setEnabled(False)
        self._client_device_combo.addItem("(probing…)", userData=None)
        grid.addWidget(self._client_device_combo, 3, 1, 1, 2)

        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #64748b; font-size: 11px;")
        root.addWidget(self._status)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._ok_btn = btns.button(QDialogButtonBox.Ok)
        self._ok_btn.setEnabled(False)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # Kick off the first probe.
        QTimer.singleShot(0, self._probe_devices)

    def _on_two_hca_toggled(self, checked: bool) -> None:
        """v0.5.148: enable/disable the client HCA row when the
        toggle changes. We auto-pick a sensible default for the
        client side (the NEXT device after the server's pick) so
        the operator doesn't have to think about it for the common
        dual-port case."""
        self._client_hca_label.setEnabled(checked)
        self._client_device_combo.setEnabled(checked)
        if checked:
            # Default: pick a different device than the server's
            # current selection when there are at least two.
            self._auto_pick_client_device()
        self._refresh_ok_button()

    def _auto_pick_client_device(self) -> None:
        """When two-HCA mode is enabled, pre-select the next
        device after the server's pick — common dual-port case
        (rocep…f0 + rocep…f1) becomes one-click."""
        if self._client_device_combo.count() == 0:
            return
        srv_dev = self._server_device_combo.currentData()
        if not srv_dev:
            return
        srv_idx = self._client_device_combo.findData(srv_dev)
        # Pick the next index; wrap to 0 if at end.
        if srv_idx < 0:
            target = 0
        else:
            target = (srv_idx + 1) % self._client_device_combo.count()
        # Skip placeholder items (userData=None).
        for off in range(self._client_device_combo.count()):
            idx = (target + off) % self._client_device_combo.count()
            if self._client_device_combo.itemData(idx) is not None:
                self._client_device_combo.setCurrentIndex(idx)
                break

    def _refresh_ok_button(self) -> None:
        """OK enabled when:
          * server HCA combo has a real selection, AND
          * if two-HCA mode is on, client HCA combo also has a
            real selection that's different from the server's.
        """
        srv_dev = self._server_device_combo.currentData()
        if not srv_dev:
            self._ok_btn.setEnabled(False)
            return
        if not self._two_hca_check.isChecked():
            self._ok_btn.setEnabled(True)
            return
        cli_dev = self._client_device_combo.currentData()
        if not cli_dev:
            self._ok_btn.setEnabled(False)
            return
        # Same device on both sides in two-HCA mode is a UX bug —
        # operator wanted DIFFERENT HCAs. Fall back to disabling
        # OK with a status hint rather than silently writing the
        # same line twice.
        if srv_dev == cli_dev:
            self._status.setText(
                "<span style='color:#b91c1c;'>"
                "Two-HCA mode needs different devices — pick a "
                "different Client HCA, or uncheck the box to use "
                "the same HCA on both sides."
                "</span>"
            )
            self._ok_btn.setEnabled(False)
            return
        self._ok_btn.setEnabled(True)

    def _probe_devices(self) -> None:
        """Fetch /api/rdma/devices on the currently-selected TG.
        Repopulates BOTH device combos (server + client) with the
        response. v0.5.148: was single-combo before."""
        if not hasattr(self, "_server_device_combo"):
            return
        from widgets.rdma_blast_flow_dialog import _get_async

        url = self._server_combo.currentData()
        if not url:
            return
        self._server_device_combo.clear()
        self._server_device_combo.addItem("(probing…)", userData=None)
        self._client_device_combo.clear()
        self._client_device_combo.addItem("(probing…)", userData=None)
        self._ok_btn.setEnabled(False)

        def _on_done(data, err, _url=url):
            try:
                self._server_device_combo.count()
            except RuntimeError:
                return
            self._server_device_combo.clear()
            self._client_device_combo.clear()
            if err:
                self._server_device_combo.addItem(
                    f"error: {err}", userData=None)
                self._client_device_combo.addItem(
                    f"error: {err}", userData=None)
                self._status.setText(
                    f"<span style='color:#b91c1c;'>{_url}: {err}</span>"
                )
                self._ok_btn.setEnabled(False)
                return
            devices = (data or {}).get("devices") or []
            if not devices:
                self._server_device_combo.addItem(
                    "(no HCAs)", userData=None)
                self._client_device_combo.addItem(
                    "(no HCAs)", userData=None)
                self._status.setText(
                    "<span style='color:#b91c1c;'>"
                    "No RDMA HCAs on this TG — verify RDMA is "
                    "installed (Tools → RDMA → Setup RDMA…)."
                    "</span>"
                )
                self._ok_btn.setEnabled(False)
                return
            for dev in devices:
                name = dev.get("name", "?")
                ports = dev.get("ports") or []
                state = ""
                if ports:
                    p = ports[0]
                    state = f"  ({(p.get('state') or '').upper()})"
                self._server_device_combo.addItem(
                    f"{name}{state}", userData=name)
                self._client_device_combo.addItem(
                    f"{name}{state}", userData=name)
            # v0.5.148: hint when only one HCA is present — the
            # two-HCA mode is meaningless then. Don't disable
            # the checkbox (operator might want to swap TGs);
            # just nudge.
            if len(devices) < 2:
                self._status.setText(
                    f"<span style='color:#f59e0b;'>"
                    f"{len(devices)} HCA on {_url}. "
                    f"Two-HCA mode needs at least 2 HCAs on the "
                    f"same TG."
                    f"</span>"
                )
            else:
                self._status.setText(
                    f"<span style='color:#64748b;'>"
                    f"{len(devices)} HCA(s) on {_url}."
                    f"</span>"
                )
            # If two-HCA mode is already on, re-pick the client
            # default (the response just arrived).
            if self._two_hca_check.isChecked():
                self._auto_pick_client_device()
            # And signal-wire the server combo so the auto-pick
            # tracks the operator's choice.
            try:
                self._server_device_combo.currentIndexChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._server_device_combo.currentIndexChanged.connect(
                self._on_server_device_changed)
            self._client_device_combo.currentIndexChanged.connect(
                self._refresh_ok_button)
            self._refresh_ok_button()

        _get_async(self, f"{url.rstrip('/')}/api/rdma/devices",
                   _on_done, timeout=6.0)

    def _on_server_device_changed(self, *_args) -> None:
        """When the operator changes the server-side HCA, if
        two-HCA mode is on, slide the client pick to the next
        device. Same one-click ergonomics."""
        if self._two_hca_check.isChecked():
            self._auto_pick_client_device()
        self._refresh_ok_button()

    def _on_accept(self) -> None:
        url = self._server_combo.currentData()
        srv_dev = self._server_device_combo.currentData()
        if not url or not srv_dev:
            return
        srv_line = f"{url} {srv_dev}"
        if self._two_hca_check.isChecked():
            cli_dev = self._client_device_combo.currentData()
            if not cli_dev or cli_dev == srv_dev:
                # Defensive — _refresh_ok_button should have
                # blocked this, but don't trust UI invariants.
                return
            cli_line = f"{url} {cli_dev}"
        else:
            cli_line = srv_line
        self._chosen_lines = (srv_line, cli_line)
        self.accept()

    def selected_lines(self) -> Tuple[str, str]:
        """Return the (server_line, client_line) pair. In same-HCA
        loopback mode both strings are identical."""
        return self._chosen_lines
