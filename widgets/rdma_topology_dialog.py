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
from typing import Any, Dict, List, Optional, Tuple

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
    # v0.5.156: reuse Blast's blocker detection + confirm dialog
    # to give Topology the same v0.5.152/v0.5.153 auto-fix UX.
    _detect_start_blockers, _StartBlockerConfirmDialog,
)


_SHAPE_LABELS = [
    (SHAPE_SINGLE,   "Single  1↔1"),
    (SHAPE_FAN_IN,   "Fan-in  N→1"),
    (SHAPE_FAN_OUT,  "Fan-out  1→N"),
    (SHAPE_MESH,     "Mesh  N×M"),
    (SHAPE_PAIRWISE, "Pairwise  N↔N"),
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
        # v0.5.160 followup: tighter root margins + spacing so the
        # dialog packs more densely on a 1280×800 laptop screen.
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # v0.5.160 followup: tighter group-box chrome — smaller
        # top margin (was 9 → 7), tighter inner padding
        # (was 6/8/8/8 → 4/6/6/6). Each saved px multiplies across
        # the four group boxes.
        self.setStyleSheet(
            "QGroupBox {"
            "  font-weight: 600; color: #334155;"
            "  border: 1px solid #cbd5e1; border-radius: 4px;"
            "  margin-top: 7px; padding: 4px 6px 6px 6px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin; subcontrol-position: top left;"
            "  left: 8px; padding: 0 4px;"
            "}"
        )

        # v0.5.160 followup: dropped the verbose header banner —
        # the window title already says "RDMA Topology Test", and
        # the Install Guide §10d reference belongs in the Help
        # menu, not stealing a row of vertical real estate.

        # ── Topology shape picker + pair-count preview
        shape_box = QGroupBox("Topology")
        sh = QHBoxLayout(shape_box)
        sh.setContentsMargins(6, 2, 6, 2)
        sh.setSpacing(10)
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
        # v0.5.160 followup: shorter title; format hint lives in
        # the textarea placeholder where the operator actually
        # needs it.
        endpoints_box = QGroupBox("Endpoints")
        eg = QGridLayout(endpoints_box)
        eg.setContentsMargins(8, 4, 8, 4)
        eg.setHorizontalSpacing(8)
        eg.setVerticalSpacing(3)

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
        self._loopback_btn = QPushButton("↔  Same-host test…")
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

        # v0.5.160 followup: shorter placeholders (the format
        # hint moved into the textarea so it disappears once
        # operator starts typing) + tighter max-height.
        self._server_edit = QPlainTextEdit()
        self._server_edit.setPlaceholderText(
            "http://srv01:5050 mlx5_0  [port=1 gid=3 label=NAME]\n"
            "# blank/# lines ignored"
        )
        self._server_edit.setMaximumHeight(80)
        self._server_edit.textChanged.connect(self._refresh_pair_count)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._server_edit.setFont(mono)

        self._client_edit = QPlainTextEdit()
        self._client_edit.setPlaceholderText(
            "http://srv04:5050 mlx5_0\n"
            "http://srv05:5050 mlx5_0"
        )
        self._client_edit.setMaximumHeight(80)
        self._client_edit.textChanged.connect(self._refresh_pair_count)
        self._client_edit.setFont(mono)

        eg.addWidget(self._server_edit, 1, 0)
        eg.addWidget(self._client_edit, 1, 1)

        # v0.5.160 followup: dropped the long inline HCA-vs-iface
        # note (now lives in the textarea tooltips below and in
        # Help → Install Guide §10d). The compact footer row
        # carries just the same-host shortcut + a one-line
        # device-clarification hint.
        self._server_edit.setToolTip(
            "device = RDMA HCA name (e.g. mlx5_0) — the InfiniBand "
            "verbs device, NOT an Ethernet interface (ens2f0np0). "
            "perftest addresses the HCA directly via libibverbs."
        )
        self._client_edit.setToolTip(self._server_edit.toolTip())

        footer_row = QWidget()
        _fh = QHBoxLayout(footer_row)
        _fh.setContentsMargins(0, 0, 0, 0)
        _fh.setSpacing(6)
        _fh.addWidget(QLabel(
            "<span style='color:#64748b; font-size:11px;'>"
            "device = RDMA HCA (mlx5_0), not iface (ens2f0np0)."
            "</span>"
        ))
        _fh.addStretch(1)
        _fh.addWidget(self._loopback_btn)
        eg.addWidget(footer_row, 2, 0, 1, 2)

        eg.setColumnStretch(0, 1)
        eg.setColumnStretch(1, 1)
        root.addWidget(endpoints_box)

        # ── Shared workload params — same compact 2-column grid as
        # the single-pair dialog
        test_box = QGroupBox("Workload (per pair)")
        tg = QGridLayout(test_box)
        # v0.5.159: bumped vertical spacing 4 → 8 so spinbox
        # baselines no longer kiss.
        # v0.5.160: operator wanted "more compact" — dial vertical
        # back to 4 (the v0.5.156 baseline) and tighten margins.
        # The Retina-kissing was on Blast's busier layout; Topology
        # has fewer rows so 4 is fine.
        tg.setContentsMargins(8, 4, 8, 4)
        tg.setHorizontalSpacing(8)
        tg.setVerticalSpacing(4)

        self._test_combo = QComboBox()
        for tid, label, _group in _TESTS:
            self._test_combo.addItem(label, userData=tid)
        # v0.5.160 followup: cap so the col 1 stretch doesn't pull
        # the combo across half the dialog. Content is short
        # ("Send — Bandwidth" et al.); 300 px is plenty.
        self._test_combo.setMaximumWidth(300)

        self._mtu_combo = QComboBox()
        for code, label in _MTU_OPTIONS:
            self._mtu_combo.addItem(label, userData=code)
        self._mtu_combo.setCurrentIndex(len(_MTU_OPTIONS) - 1)
        self._mtu_combo.setMaximumWidth(300)

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

        # v0.5.160 followup: shorter labels — perftest flags live in
        # tooltips. The bare "Bidirectional" / "CPU util" is the
        # operator-facing name.
        self._bidir_check = QCheckBox("Bidirectional")
        self._bidir_check.setToolTip("perftest -b")
        self._cpu_util_check = QCheckBox("CPU util")
        self._cpu_util_check.setToolTip("perftest --cpu_util")

        # v0.5.156 Slice A: Parallel workers — per-pair worker
        # count for true multi-core BW scaling. Each pair spawns
        # K perftest processes per side, each pinned to a
        # different CPU core. With M pairs and K workers each,
        # total perftest processes = 2 × M × K — the picker caps
        # this against NUMA-local core count.
        self._parallel_workers_spin = QSpinBox()
        self._parallel_workers_spin.setRange(1, 64)
        self._parallel_workers_spin.setValue(1)
        self._parallel_workers_spin.setMinimumWidth(80)
        self._parallel_workers_spin.setToolTip(
            "Per-pair worker count. Each pair spawns N perftest "
            "processes on each side, each pinned to a different "
            "CPU core via taskset. For true BW scaling beyond "
            "single-core line rate.\n\n"
            "Total perftest processes = 2 × (pair count) × "
            "(workers). Click 🚀 Max BW to auto-pick a value that "
            "fits the HCA's NUMA-local core count."
        )
        self._max_bw_btn = QPushButton("🚀 Max BW")
        # v0.5.159: was setFixedWidth(82) which clipped "Max BW"
        # on macOS. Use min width so Qt sizes for the platform.
        self._max_bw_btn.setMinimumWidth(108)
        # v0.5.160: cap height so the workers row matches the
        # bare-spinbox row heights.
        self._max_bw_btn.setMaximumHeight(28)
        self._max_bw_btn.setToolTip(
            "Query the first endpoint's host topology, find its "
            "HCA's NUMA-local core count, divide by pair count, "
            "and set Workers = the result. Pinning all workers "
            "to the HCA's home NUMA node aligns CPU + RAM + PCIe."
        )
        self._max_bw_btn.clicked.connect(self._on_max_bw_clicked)

        # v0.5.160: number of full topology-run iterations. Each
        # iteration spawns every pair fresh (new perftest, new
        # handshakes, new ports = base + iter * pair_count) and
        # records one row per pair in the Per-pair stats table,
        # labeled "#<iter>.<pair>". After all iterations finish,
        # a Σ summary row shows avg / min / max BW across them.
        self._iterations_spin = QSpinBox()
        self._iterations_spin.setRange(1, 1000)
        self._iterations_spin.setValue(1)
        self._iterations_spin.setMinimumWidth(80)
        self._iterations_spin.setToolTip(
            "Number of full topology runs. Each iteration re-runs "
            "every pair, recording a new row in the Per-pair stats "
            "table. After all iterations finish, a Σ summary row "
            "shows avg / min / max BW across them.\n\n"
            "Useful for variance characterization (RoCE BW can "
            "swing 5–10% between runs from fabric / NIC PFC "
            "interactions). 5–20 iterations is a sensible range."
        )

        # Cache for /api/rdma/host_info. Populated lazily when the
        # 🚀 Max BW button is clicked.
        self._host_info_cache: Dict[str, Any] = {}

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
        # v0.5.156: parallel workers row
        # v0.5.160: Iterations spinbox added alongside Parallel
        # workers — operator wanted N-run iteration support so
        # variance can be characterized in one click.
        tg.addWidget(QLabel("Parallel workers:"), 4, 0, Qt.AlignRight)
        _pw_row = QWidget()
        _pwh = QHBoxLayout(_pw_row)
        _pwh.setContentsMargins(0, 0, 0, 0)
        _pwh.setSpacing(4)
        _pwh.addWidget(self._parallel_workers_spin)
        _pwh.addWidget(self._max_bw_btn)
        _pwh.addStretch(1)
        tg.addWidget(_pw_row, 4, 1)
        tg.addWidget(QLabel("Iterations:"), 4, 2, Qt.AlignRight)
        tg.addWidget(self._iterations_spin, 4, 3)
        cb_row = QHBoxLayout()
        cb_row.setSpacing(16)
        cb_row.addWidget(self._bidir_check)
        cb_row.addWidget(self._cpu_util_check)
        cb_row.addStretch(1)
        cb_holder = QWidget()
        cb_holder.setLayout(cb_row)
        tg.addWidget(cb_holder, 5, 0, 1, 4)
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
        stats_box = QGroupBox("Results")
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
        # v0.5.160 followup: 360 was excessive on the empty state.
        # Drop to 200 (enough to comfortably show ~8 rows) and let
        # the stretch=1 below grow it when needed. Stretch already
        # claims any freed vertical room.
        self._stats_table.setMinimumHeight(200)
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

        # v0.5.156 Slice A: probe same-host pairs for DOWN ports /
        # missing IPs / same-subnet trap BEFORE firing perftest.
        # If a blocker is detected, surface the same confirm
        # dialog Blast uses (v0.5.152/v0.5.153). Skip if every
        # pair is cross-host (trap impossible) OR the operator
        # already applied IPs via Pre-flight.
        same_host_plans = [
            p for p in plans if p.server.tg_url == p.client.tg_url
        ]
        already_applied = bool(self._preflight_state_ids)
        if same_host_plans and not already_applied:
            self._set_status_neutral(
                "Probing endpoints for same-subnet trap / DOWN port…"
            )
            self._start_btn.setEnabled(False)
            self._topology_probe_then_start(plans, spec, same_host_plans[0])
            return
        # No same-host trap risk or operator already fixed it →
        # proceed directly.
        self._proceed_with_topology_start(plans, spec)

    def _topology_probe_then_start(
        self, plans: List[RdmaPairPlan],
        spec: RdmaTopologySpec,
        sample_plan: RdmaPairPlan,
    ) -> None:
        """v0.5.156: probe the FIRST same-host pair's two endpoints.
        If a blocker is detected, pop the confirm dialog; otherwise
        proceed. We sample one pair rather than all pairs because:
          (a) blockers tend to be host-wide (DOWN port, missing
              IP, subnet config), not pair-specific,
          (b) probing all pairs would compound latency for the
              common "many same-host pairs" mesh case."""
        url = sample_plan.server.tg_url
        srv_hca = sample_plan.server.device
        cli_hca = sample_plan.client.device
        srv_port = sample_plan.server.ib_port
        cli_port = sample_plan.client.ib_port

        self._topology_probe_buf: Dict[str, Dict[str, Any]] = {}

        def _done(side: str, data, err):
            self._topology_probe_buf[side] = data or {"error": err}
            if len(self._topology_probe_buf) < 2:
                return
            self._topology_on_probe_complete(plans, spec, sample_plan)

        for side, hca, port in (
            ("server", srv_hca, srv_port),
            ("client", cli_hca, cli_port),
        ):
            _get_async(
                self,
                f"{url.rstrip('/')}/api/rdma/probe?device={hca}&port={port}",
                lambda data, err, _s=side: _done(_s, data, err),
                timeout=4.0,
            )

    def _topology_on_probe_complete(
        self,
        plans: List[RdmaPairPlan],
        spec: RdmaTopologySpec,
        sample_plan: RdmaPairPlan,
    ) -> None:
        srv = self._topology_probe_buf.get("server") or {}
        cli = self._topology_probe_buf.get("client") or {}
        reason, detail = _detect_start_blockers(srv, cli)
        if reason is None:
            self._proceed_with_topology_start(plans, spec)
            return

        srv_iface = srv.get("kernel_iface") or "<server-iface>"
        cli_iface = cli.get("kernel_iface") or "<client-iface>"
        dlg = _StartBlockerConfirmDialog(
            reason=reason, detail=detail,
            srv_iface=srv_iface, cli_iface=cli_iface,
            parent=self,
        )
        result = dlg.exec_()
        choice = dlg.choice()
        if result != QDialog.Accepted or choice == "cancel":
            self._start_btn.setEnabled(True)
            self._set_status_neutral("Start cancelled.")
            return
        if choice == "continue":
            self._proceed_with_topology_start(plans, spec)
            return
        if choice == "open_preflight":
            self._start_btn.setEnabled(True)
            self._set_status_neutral("Opening Pre-flight…")
            self._on_preflight_clicked()
            return
        # choice == "apply" — auto-pick CIDRs, configure on the
        # sample plan's TG (host-wide fix), then proceed.
        self._topology_apply_test_ips_then_start(
            plans, spec, sample_plan, srv_iface, cli_iface,
        )

    def _topology_apply_test_ips_then_start(
        self,
        plans: List[RdmaPairPlan],
        spec: RdmaTopologySpec,
        sample_plan: RdmaPairPlan,
        srv_iface: str,
        cli_iface: str,
    ) -> None:
        url = sample_plan.server.tg_url
        ifaces = [
            {"name": srv_iface, "cidr": "10.42.0.1/24"},
            {"name": cli_iface, "cidr": "10.43.0.1/24"},
        ]
        self._set_status_neutral("Validating test IPs…")

        def _on_validated(data, err):
            if err or not (data or {}).get("ok"):
                issues = (data or {}).get("issues") or []
                err_lines = [
                    f"{i.get('iface', '?')}: {i.get('message', '')}"
                    for i in issues
                    if i.get("severity") == "error"
                ]
                msg = "; ".join(err_lines) or (
                    err or "validation failed")
                self._start_btn.setEnabled(True)
                self._set_status_error(
                    f"Auto-apply blocked: {msg}. Open Pre-flight "
                    f"to pick non-conflicting CIDRs."
                )
                return
            self._set_status_neutral("Applying temporary test IPs…")
            _post_async(
                self,
                f"{url.rstrip('/')}/api/rdma/test_ifaces/configure",
                {"ifaces": ifaces, "disable_rp_filter": True},
                _on_applied,
            )

        def _on_applied(data, err):
            if err or not (data or {}).get("ok"):
                msg = err or (data or {}).get(
                    "error", "configure failed")
                self._start_btn.setEnabled(True)
                self._set_status_error(
                    f"Auto-apply failed: {msg}. Open Pre-flight."
                )
                return
            sid = (data or {}).get("state_id")
            if sid:
                self._preflight_state_ids.setdefault(
                    url, set()).add(sid)
            self._set_status_neutral(
                f"Test IPs applied (state_id="
                f"{sid[:8] if sid else '?'}). Starting topology…"
            )
            self._proceed_with_topology_start(plans, spec)

        _post_async(
            self,
            f"{url.rstrip('/')}/api/rdma/test_ifaces/validate",
            {"ifaces": ifaces}, _on_validated,
        )

    def _proceed_with_topology_start(
        self,
        plans: List[RdmaPairPlan],
        spec: RdmaTopologySpec,
    ) -> None:
        """v0.5.156: extracted from `_on_start_clicked` so the
        auto-detect can defer this step behind an optional confirm
        dialog. v0.5.160: now sets up an iteration loop instead of
        firing perftest once. _run_one_iteration() does the actual
        per-pair start; we re-enter it from `_on_job_resp` after
        each iteration completes."""
        self._plans = plans
        self._spec = spec
        self._iterations_total = max(1, int(self._iterations_spin.value()))
        self._iteration_idx = 0
        # Reset the Stop flag so a previous run's Stop click
        # doesn't suppress this run's iteration loop.
        self._stop_requested = False
        # Accumulated per-iteration snapshots — used to render the
        # Σ summary row at the end.
        self._iteration_results: List[List[Dict[str, Any]]] = []
        # v0.5.157: drop the previous run's per-pair extras so
        # polling loops don't keep ticking against the previous
        # run's stale job_ids.
        if hasattr(self, "_pair_extra_workers"):
            self._pair_extra_workers.clear()
        # v0.5.159 REGRESSION FIX: v0.5.157 also reset
        # _host_info_cache = {} here, which silently discarded the
        # operator's 🚀 Max BW click. host_info is a HOST-LEVEL
        # snapshot (NUMA topology + hca_numa map for all HCAs);
        # reusing it across Starts is correct. Leave the cache.
        # Clear the table for a fresh run.
        self._stats_table.setRowCount(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._run_one_iteration()

    def _run_one_iteration(self) -> None:
        """v0.5.160: start one iteration of the topology run.
        Appends one row per pair to the stats table labeled
        `#<iter>.<pair>`, then fires the server-side perftest
        starts. When every pair completes, `_on_job_resp` snapshots
        the iteration's results and either calls back here (next
        iteration) or emits the Σ summary row."""
        plans = self._plans
        spec = self._spec
        # Fresh per-iteration job state — old job_ids must not
        # leak into the next iteration's polling.
        self._pair_jobs = {p.pair_index: {"server": None, "client": None}
                           for p in plans}
        self._latest_jobs = {}
        # Append one row per pair, capturing the base row so
        # _update_pair_row can write to the right spot.
        self._current_iter_base_row = self._stats_table.rowCount()
        self._populate_stats_table_skeleton(plans)
        self._stats_table.scrollToBottom()
        self._set_status_neutral(
            f"Iteration {self._iteration_idx + 1}/"
            f"{self._iterations_total} — starting "
            f"{len(plans)} pair{'s' if len(plans) != 1 else ''}…"
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
        # v0.5.156 Slice A: fan out parallel workers for this pair.
        try:
            wc = int(self._parallel_workers_spin.value())
        except (AttributeError, RuntimeError):
            wc = 1
        if wc > 1:
            self._start_pair_extra_workers(plan, wc)

    # ─────────── v0.5.156 Slice A: parallel workers per pair

    def _on_max_bw_clicked(self) -> None:
        """Probe the first server endpoint's host topology, derive
        per-pair worker count = floor(NUMA-local cores / pair_count).
        Capped at 16. Falls back to 1 on probe failure."""
        srv_eps, srv_errs = parse_endpoint_block(self._server_edit.toPlainText())
        cli_eps, cli_errs = parse_endpoint_block(self._client_edit.toPlainText())
        if srv_errs or cli_errs or not srv_eps:
            self._set_status_error(
                "Fill in valid endpoints first.")
            return
        # Number of pairs the shape will produce — drives the cap.
        try:
            spec = self._spec_from_ui(srv_eps, cli_eps)
            plans = expand_pairs(spec)
            pair_count = max(1, len(plans))
        except Exception:
            pair_count = 1
        sample = srv_eps[0]
        url = sample.tg_url.rstrip("/")
        hca = sample.device

        self._set_status_neutral("Querying host topology…")

        def _on_info(data, err, _hca=hca, _pair_count=pair_count):
            if err or not data:
                self._set_status_error(
                    f"host_info failed: {err or 'no data'}. "
                    f"Set Parallel workers manually."
                )
                return
            self._host_info_cache = data
            try:
                from utils.rdma_host_info import pick_workers_for_hca
                pick = pick_workers_for_hca(
                    hca=_hca, requested=None, info=data)
            except Exception as exc:
                self._set_status_error(f"pick_workers failed: {exc}")
                return
            # Divide NUMA-local cores by pair count so we don't
            # oversubscribe (each pair will spawn `per_pair` workers
            # on each side).
            per_pair = max(1, int(pick.get("worker_count") or 1)
                           // _pair_count)
            per_pair = min(per_pair, 16)
            self._parallel_workers_spin.setValue(per_pair)
            self._set_status_neutral(
                f"🚀 {per_pair} worker(s)/pair × {_pair_count} "
                f"pair(s) = {per_pair * _pair_count} total. "
                f"{pick.get('reason', '')}"
            )

        _get_async(self, f"{url}/api/rdma/host_info",
                   _on_info, timeout=4.0)

    def _start_pair_extra_workers(
        self, plan: RdmaPairPlan, worker_count: int,
    ) -> None:
        """Spawn workers 1..N-1 for this pair. Each gets a unique
        cpu_pin, shared numa_pin (matches plan.server's HCA), and
        unique listen_port (collision-safe across pairs — see
        port-scheme note below)."""
        info = self._host_info_cache or {}
        numa_pin = None
        cpus: List[int] = []
        fallback_reason = None
        try:
            from utils.rdma_host_info import pick_workers_for_hca
            pick = pick_workers_for_hca(
                hca=plan.server.device,
                requested=worker_count,
                info=info,
            )
            numa_pin = pick.get("numa_pin")
            cpus = pick.get("cpus") or list(range(worker_count))
            if not info:
                fallback_reason = (
                    "no host_info cached (operator skipped 🚀 Max BW) — "
                    "linear CPU ordering, no NUMA pin"
                )
            elif numa_pin is None:
                fallback_reason = (
                    f"HCA {plan.server.device} not in host's NUMA map — "
                    f"linear CPU ordering, no NUMA pin"
                )
        except Exception as exc:
            cpus = list(range(worker_count))
            fallback_reason = f"pick_workers_for_hca failed: {exc}"
        # v0.5.158: surface the fallback so cross-NUMA penalty
        # doesn't get misdiagnosed as a wire issue.
        # v0.5.159: was self._stats_view.append — that attribute
        # only exists on the Blast dialog. Topology has _stats_table
        # + _status_label. Use the status label (rendered in red
        # by _set_status_error so the operator sees it) — falling
        # back to NUMA-blind workers is a soft warning, not an
        # error, but operator-visible matters more than the colour
        # nuance.
        if fallback_reason:
            try:
                self._set_status_error(
                    f"⚠ pair #{plan.pair_index}: {fallback_reason}"
                )
            except Exception:
                pass
        # v0.5.157: clamp to cpu_count - 1 so a high worker_count
        # on a small-core host doesn't ask taskset for a CPU that
        # doesn't exist (matches the Blast _start_extra_workers
        # fix). Multiple workers can land on the same top CPU —
        # perftest's QP-per-worker isolation still gives us real
        # parallelism even if the taskset arg collides.
        cpu_count = info.get("cpu_count")
        if isinstance(cpu_count, int) and cpu_count > 0:
            cpus = [min(int(c), cpu_count - 1) for c in cpus]

        if not hasattr(self, "_pair_extra_workers"):
            self._pair_extra_workers: Dict[int, List[Dict[str, Any]]] = {}
        self._pair_extra_workers.setdefault(plan.pair_index, [])

        spec_workload = (self._plans[0]
                         and self._plans[0])  # placeholder to satisfy lint
        # Re-derive workload from UI (the same path
        # _proceed_with_topology_start uses).
        test_id = self._test_combo.currentData()

        peer_host = plan.server.tg_url.replace("http://", "") \
            .replace("https://", "").split(":")[0]

        # v0.5.160 followup CRASH FIX: pre-fix referenced
        # `plan.base_listen_port`, which doesn't exist —
        # base_listen_port lives on the SPEC (config), not the
        # plan (per-pair assignment). The plan has `listen_port`
        # (= spec.base_listen_port + pair_index). That AttributeError
        # killed every multi-pair multi-worker run.
        # Port scheme: stride each extra worker by `pair_count` to
        # avoid colliding with the NEXT pair's listen_port. Pair P
        # worker W gets port `plan.listen_port + W * pair_count`,
        # so the worker-0 ports (W=0) are the existing
        # base + P assignments, and worker-W ports for any pair
        # land in their own stride band.
        pair_count = max(1, len(self._plans))
        for worker_idx in range(1, worker_count):
            cpu = cpus[worker_idx] if worker_idx < len(cpus) else worker_idx
            extra_port = plan.listen_port + worker_idx * pair_count
            worker_handshake = (
                f"{plan.pair_index}-w{worker_idx}-"
                f"{uuid.uuid4().hex[:6]}"
            )

            srv_body = {
                "role": "server",
                "test": test_id,
                "device": plan.server.device,
                "ib_port": plan.server.ib_port,
                "handshake_id": worker_handshake,
                "note": f"Topology pair {plan.pair_index} / worker {worker_idx}",
                "listen_port": extra_port,
                "cpu_pin": cpu,
                "numa_pin": numa_pin,
                "gid_index": plan.server.gid_index,
            }

            def _on_extra_srv(
                data, err,
                _w=worker_idx, _cpu=cpu, _plan=plan,
                _hand=worker_handshake, _peer=peer_host,
                _port=extra_port,
            ):
                if err or not data or data.get("status") != "started":
                    return
                srv_jid = data.get("job_id")
                cli_body = {
                    "role": "client",
                    "test": test_id,
                    "device": _plan.client.device,
                    "ib_port": _plan.client.ib_port,
                    "handshake_id": _hand,
                    "note": f"Topology pair {_plan.pair_index} / worker {_w}",
                    "cpu_pin": _cpu,
                    "numa_pin": numa_pin,
                    "peer_addr": _peer,
                    "peer_port": _port,
                    "gid_index": _plan.client.gid_index,
                }

                def _on_extra_cli(cdata, cerr, _w2=_w, _srv_jid=srv_jid,
                                  _plan2=_plan):
                    if cerr or not cdata or cdata.get("status") != "started":
                        return
                    self._pair_extra_workers[_plan2.pair_index].append({
                        "worker_idx": _w2,
                        "server": _srv_jid,
                        "client": cdata.get("job_id"),
                    })

                _post_async(
                    self,
                    f"{plan.client.tg_url}/api/rdma/perftest/start",
                    cli_body, _on_extra_cli,
                )

            _post_async(
                self,
                f"{plan.server.tg_url}/api/rdma/perftest/start",
                srv_body, _on_extra_srv,
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
        # v0.5.160: also signal the iteration loop in _on_job_resp
        # to NOT spawn the next iteration when this one's pairs
        # finish.
        self._stop_requested = True
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
        """v0.5.160: append rows for THIS iteration. The table now
        carries every iteration's results stacked vertically — each
        pair's row sits at `_current_iter_base_row + pair_index`."""
        base = self._current_iter_base_row
        self._stats_table.setRowCount(base + len(plans))
        iter_idx = self._iteration_idx
        # In single-iteration mode use just the pair index for
        # back-compat. With Iterations > 1 use `<iter>.<pair>`.
        single_iter = self._iterations_total == 1
        for p in plans:
            row = base + p.pair_index
            label = (str(p.pair_index) if single_iter
                     else f"{iter_idx}.{p.pair_index}")
            self._stats_table.setItem(row, 0, QTableWidgetItem(label))
            self._stats_table.setItem(row, 1,
                                       QTableWidgetItem(p.server.display()))
            self._stats_table.setItem(row, 2,
                                       QTableWidgetItem(p.client.display()))
            self._stats_table.setItem(row, 3, QTableWidgetItem("queued"))
            self._stats_table.setItem(row, 4, QTableWidgetItem("—"))
            self._stats_table.setItem(row, 5, QTableWidgetItem("—"))
        if single_iter:
            self._total_label.setText(
                f"<span style='color:#475569;'>"
                f"{len(plans)} pair{'s' if len(plans) != 1 else ''}"
                f" queued. Waiting for first stats…</span>"
            )
        else:
            self._total_label.setText(
                f"<span style='color:#475569;'>"
                f"Iteration {iter_idx + 1}/{self._iterations_total} — "
                f"{len(plans)} pair{'s' if len(plans) != 1 else ''}"
                f" queued.</span>"
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
        # If all pairs are done, the iteration is complete.
        if self._all_pairs_done():
            self._stop_poll()
            self._snapshot_iteration_results()
            self._iteration_idx += 1
            stop_requested = getattr(self, "_stop_requested", False)
            if (self._iteration_idx < self._iterations_total
                    and not stop_requested):
                # Brief pause so the user sees the iteration's
                # done state, then kick off the next.
                QTimer.singleShot(500, self._run_one_iteration)
            else:
                # All iterations finished (or operator hit Stop).
                self._append_summary_row()
                self._start_btn.setEnabled(True)
                self._stop_btn.setEnabled(False)
                self._stop_requested = False
                msg = (f"All {self._iteration_idx} iteration(s) × "
                       f"{len(self._plans)} pair(s) finished.")
                if stop_requested:
                    msg = f"Stopped after {self._iteration_idx} iteration(s)."
                self._set_status_ok(msg)

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
        # v0.5.160: rows for iteration N start at
        # `_current_iter_base_row`. Earlier iterations' rows are
        # below; never overwrite them.
        row = getattr(self, "_current_iter_base_row", 0) + pair_index
        self._stats_table.setItem(row, 3, QTableWidgetItem(state))
        self._stats_table.setItem(
            row, 4,
            QTableWidgetItem(f"{bw:.2f}" if isinstance(bw, (int, float)) else "—"),
        )
        self._stats_table.setItem(
            row, 5,
            QTableWidgetItem(f"{mr:.4f}" if isinstance(mr, (int, float)) else "—"),
        )

    def _snapshot_iteration_results(self) -> None:
        """v0.5.160: capture the just-finished iteration's per-pair
        client-side stats. Used to render the Σ summary row at the
        end of the full run."""
        snap: List[Dict[str, Any]] = []
        for plan in self._plans:
            cj_id = self._pair_jobs[plan.pair_index].get("client")
            job = self._latest_jobs.get(cj_id) if cj_id else None
            snap.append({
                "iter": self._iteration_idx,
                "pair_index": plan.pair_index,
                "bw": (job.get("final_bw_avg_gbps")
                       if isinstance(job, dict) else None),
                "msgrate": (job.get("final_msg_rate_mpps")
                            if isinstance(job, dict) else None),
                "rc": (job.get("returncode")
                       if isinstance(job, dict) else None),
            })
        self._iteration_results.append(snap)

    def _append_summary_row(self) -> None:
        """v0.5.160: render a Σ row showing avg / min / max BW and
        MsgRate across all (iteration, pair) samples. Skipped for
        single-iteration runs (would just duplicate the lone row)."""
        results = getattr(self, "_iteration_results", [])
        if not results or self._iterations_total < 2:
            return
        bws = [r["bw"] for snap in results for r in snap
               if isinstance(r.get("bw"), (int, float))]
        mrs = [r["msgrate"] for snap in results for r in snap
               if isinstance(r.get("msgrate"), (int, float))]
        if not bws and not mrs:
            return
        row = self._stats_table.rowCount()
        self._stats_table.setRowCount(row + 1)
        self._stats_table.setItem(row, 0, QTableWidgetItem("Σ"))
        self._stats_table.setItem(
            row, 1, QTableWidgetItem(f"{len(results)} iterations"))
        self._stats_table.setItem(
            row, 2,
            QTableWidgetItem(f"{len(bws)} samples"))
        self._stats_table.setItem(row, 3, QTableWidgetItem("summary"))
        if bws:
            avg = sum(bws) / len(bws)
            self._stats_table.setItem(
                row, 4,
                QTableWidgetItem(
                    f"avg={avg:.2f} "
                    f"min={min(bws):.2f} "
                    f"max={max(bws):.2f}"
                ),
            )
        if mrs:
            avg = sum(mrs) / len(mrs)
            self._stats_table.setItem(
                row, 5,
                QTableWidgetItem(
                    f"avg={avg:.4f} "
                    f"min={min(mrs):.4f} "
                    f"max={max(mrs):.4f}"
                ),
            )
        self._stats_table.scrollToBottom()

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
        # v0.5.157: drop per-pair extras so a reopen via the menu
        # starts clean.
        # v0.5.159: dropped the _host_info_cache = {} reset — Qt
        # destroys the widget on close anyway, and the cache is
        # host-level (stable across sessions).
        if hasattr(self, "_pair_extra_workers"):
            self._pair_extra_workers.clear()
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
