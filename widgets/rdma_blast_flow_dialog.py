"""Blast a RDMA Flow dialog — v0.3.12.

Two-TG (or loopback) perftest orchestrator. The standalone "Blast a
RDMA Flow" entrypoint from the RDMA menu: pick a server-side TG +
RDMA device, pick a client-side TG + device, pick a test (Send /
Write / Read × BW / Latency), click Start. We:

  1. Generate a shared handshake_id (uuid4).
  2. POST /api/rdma/perftest/start to the server-side TG with
     role="server"+handshake_id. It returns listen_addr+listen_port.
  3. POST /api/rdma/perftest/start to the client-side TG with
     role="client"+handshake_id+peer_addr=<server's listen_addr>.
  4. Both halves run independently (perftest's own TCP handshake
     completes over the listen_port). We poll
     GET /api/rdma/perftest/job/<job_id> on each TG every 2 s and
     render running BW (Gbps) / message rate (Mpps) / latency (µs).
  5. Stop button → POST /api/rdma/perftest/stop on both TGs +
     DELETE /api/rdma/handshakes/<id> for cleanup.

Same multi-instance shape as Blast a DPDK Flow:
  * Non-modal (operator works the main window while the test runs).
  * Cascade window positions when multiple dialogs are open.
  * Sibling-iface conflict warning (same device, two perftest jobs
    on one HCA — drivers usually handle this fine but it's worth a
    heads-up).

No DPDK make-ready prereq — RDMA is always-on once rdma-core is
loaded, so this dialog is a pure orchestrator. If perftest isn't
installed on either TG, we surface that as a blocking error before
the operator touches any other field.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)


logger = logging.getLogger(__name__)


# ─────────────────────────────────── defaults

# Match the Blast DPDK Flow / template library defaults so packet
# captures from all three line-rate test paths align on the wire.
_DEFAULT_MSG_SIZE = 65536           # 64 KiB — perftest's own default
_DEFAULT_QP_COUNT = 1
_DEFAULT_DURATION_SECS = 30
_DEFAULT_MTU_CODE = 5               # perftest -m 5 → 4096 B payload MTU
_DEFAULT_TX_DEPTH = 128
_DEFAULT_GID_INDEX = 3              # RoCEv2-IPv4 on Mellanox; operator can override

# Test catalog: id → (display label, group)
# Atomic ops deliberately omitted from v1 — too HCA-specific (many
# adapters reject them, surfacing as a confusing perftest exit-1).
_TESTS: List[Tuple[str, str, str]] = [
    ("send_bw",   "Send — Bandwidth",       "Bandwidth"),
    ("write_bw",  "RDMA Write — Bandwidth", "Bandwidth"),
    ("read_bw",   "RDMA Read — Bandwidth",  "Bandwidth"),
    ("send_lat",  "Send — Latency",         "Latency"),
    ("write_lat", "RDMA Write — Latency",   "Latency"),
    ("read_lat",  "RDMA Read — Latency",    "Latency"),
]

# perftest -m encoding (only the values that are universally accepted).
_MTU_OPTIONS: List[Tuple[int, str]] = [
    (1,  "256 B"),
    (2,  "512 B"),
    (3,  "1024 B"),
    (4,  "2048 B"),
    (5,  "4096 B  (default)"),
]

# v0.3.19: tooltip on the IB-port spinbox in the Devices group.
# The field maps to perftest's -i flag and matters only on the
# uncommon HCAs where ONE IB device exposes MULTIPLE physical ports.
# Modern Mellanox CX-5/6/7 + Bluefield expose each port as its OWN
# device (mlx5_0, mlx5_1, …), so the spinbox should stay at 1 for
# the vast majority of users — this tooltip explains why.
_PORT_FIELD_TOOLTIP = (
    "Physical port on the HCA (perftest -i <N>).\n\n"
    "Almost always 1 on modern Mellanox hardware:\n"
    "ConnectX-5/6/7 + Bluefield expose each physical port as its "
    "OWN IB device (mlx5_0, mlx5_1, …), so picking the device "
    "already picks the port. Leave this at 1.\n\n"
    "Only bump above 1 if your HCA exposes multiple ports under "
    "ONE device (older ConnectX-3 in dual-port mode, some pure-IB "
    "firmware, or Intel irdma cards). In that case the device "
    "combo's label shows port2=ACTIVE alongside port1=ACTIVE."
)


# ─────────────────────────────────── small per-side helper

def _api_worker():
    """Late import — the worker lives next to the menu handler, which
    in turn imports widgets. Late import breaks the cycle."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


def _post_async(parent: QObject, url: str, body: dict, on_done) -> None:
    """Fire-and-forget POST via the reusable _DpdkApiWorker QThread.
    on_done(data: Optional[dict], err: str)."""
    W = _api_worker()
    w = W("POST", url, json=body, timeout=15)
    w.done.connect(on_done)
    # Hold a ref on the parent so GC doesn't kill it mid-flight.
    if not hasattr(parent, "_rdma_workers"):
        parent._rdma_workers = set()
    parent._rdma_workers.add(w)
    w.done.connect(lambda *_a, _w=w: parent._rdma_workers.discard(_w))
    w.start()


def _get_async(parent: QObject, url: str, on_done, *, timeout: float = 5.0) -> None:
    W = _api_worker()
    w = W("GET", url, timeout=timeout)
    w.done.connect(on_done)
    if not hasattr(parent, "_rdma_workers"):
        parent._rdma_workers = set()
    parent._rdma_workers.add(w)
    w.done.connect(lambda *_a, _w=w: parent._rdma_workers.discard(_w))
    w.start()


# Imported lazily to keep the module loadable in pure-test envs.
try:
    from PyQt5.QtCore import QObject  # noqa: F401
except ImportError:  # pragma: no cover
    QObject = object  # type: ignore


# ─────────────────────────────────── dialog


class RdmaBlastFlowDialog(QDialog):
    """Pick two TGs + a test + params, fire perftest on both sides."""

    def __init__(
        self,
        server_tg_url: str,
        client_tg_url: Optional[str] = None,
        *,
        server_tg_label: str = "Server TG",
        client_tg_label: str = "Client TG",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Blast a RDMA Flow")
        self.setMinimumWidth(720)
        # Non-modal so multiple Blast RDMA dialogs can run in parallel
        # (matches Blast DPDK Flow shape; operator can fan out tests
        # across NIC pairs without leaving the menu).
        self.setWindowModality(Qt.NonModal)

        self._server_tg_url = server_tg_url.rstrip("/")
        # client_tg_url defaults to the same TG → loopback test, which
        # perftest fully supports (server + client on same host).
        self._client_tg_url = (client_tg_url or server_tg_url).rstrip("/")
        self._server_tg_label = server_tg_label
        self._client_tg_label = client_tg_label

        self._handshake_id: Optional[str] = None
        self._server_job_id: Optional[str] = None
        self._client_job_id: Optional[str] = None
        # Live stats panel state.
        self._poll_timer: Optional[QTimer] = None
        # Sibling iface guard — wired by the menu handler the same way
        # DpdkBlastFlowDialog does it.
        self._sibling_iface_provider = lambda: set()
        # v0.3.19 perftest-retry state. When the initial probe finds
        # perftest missing on a TG (server or client), we kick off
        # this timer to re-probe every 5 sec for up to 2 min. Covers
        # the window where v0.3.18's server-side auto-install
        # (utils.system_deps.ensure_rdma_userspace_installed) is
        # still landing perftest in the background. Without this,
        # the red banner sticks until operator close+reopen even
        # after the binary lands ~30 sec later.
        self._perftest_retry_timer: Optional[QTimer] = None
        self._perftest_retry_attempts: int = 0
        self._perftest_missing_sides: set = set()
        # v0.3.19 per-side finished tracking + render-dedup. Pre-fix
        # ``_is_finished(side, job, want_side)`` returned False any
        # time ``side != want_side`` — which meant the server's
        # poll callback could only see the server's done state,
        # never the client's, so ``_on_both_finished()`` never fired
        # and the poll timer ran forever, re-appending the same
        # "[server] done (rc=0) ..." line every 2 sec. The fix
        # tracks each side independently on the instance, then the
        # callback ANDs the two flags. _last_rendered_key dedups
        # the chunk render so even an active poll doesn't spam the
        # stats view with identical lines.
        self._server_finished: bool = False
        self._client_finished: bool = False
        self._last_rendered_key: dict = {"server": None, "client": None}

        self._build_ui()
        self._probe_both_sides()

    # ─────────── construction

    def _build_ui(self) -> None:
        # v0.3.19 compact + professional refresh:
        #   - Header trimmed from 4-sentence paragraph to title + 1-line subtitle.
        #   - Tighter group-box stylesheet (smaller titles, 4-px paddings).
        #   - Endpoints: single inline strip (no 2-row grid).
        #   - Devices: 2 rows, fixed-width spinboxes for the IB-port field.
        #   - Test params: 2-column grid (4 rows of pairs + 1 row of checkboxes)
        #     instead of an 8-row vertical form — fits ~40% less vertical space.
        #   - Action row: Start/Stop + inline status label on one row,
        #     reclaims ~20px below.
        # Functionality identical — every spinbox / combo / tooltip preserved.
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Compact group-box stylesheet (apply to all subsequent
        # QGroupBox instances in this dialog).
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
            "QLabel { color: #1f2937; }"
        )

        # ── Header — title + one-line subtitle.
        hdr = QLabel(
            "<span style='font-size:13px; font-weight:600; color:#0f172a;'>"
            "Blast a RDMA Flow</span>"
            "&nbsp;&nbsp;"
            "<span style='color:#64748b; font-size:11px;'>"
            "perftest orchestrator — ib_*_bw / ib_*_lat on each side, "
            "correlated by handshake_id"
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        # ── Endpoints (compact inline strip). When loopback, collapse
        # the Client TG line entirely since it's identical.
        same_tg = self._server_tg_url == self._client_tg_url
        tg_box = QGroupBox("Endpoints")
        tg_grid = QGridLayout(tg_box)
        tg_grid.setHorizontalSpacing(6)
        tg_grid.setVerticalSpacing(2)
        tg_grid.setContentsMargins(8, 4, 8, 4)
        tg_grid.addWidget(QLabel("<b>Server TG</b>"), 0, 0, Qt.AlignRight)
        tg_grid.addWidget(
            QLabel(f"<code>{self._server_tg_label} → {self._server_tg_url}</code>"),
            0, 1,
        )
        if same_tg:
            tg_grid.addWidget(
                QLabel("<span style='color:#64748b;'>"
                       "<i>(loopback — client TG is the same host)</i></span>"),
                1, 0, 1, 2,
            )
        else:
            tg_grid.addWidget(QLabel("<b>Client TG</b>"), 1, 0, Qt.AlignRight)
            tg_grid.addWidget(
                QLabel(f"<code>{self._client_tg_label} → {self._client_tg_url}</code>"),
                1, 1,
            )
        tg_grid.setColumnStretch(1, 1)
        root.addWidget(tg_box)

        # ── Device picks. Two combos, fixed-width port spinboxes.
        dev_box = QGroupBox("RDMA Devices")
        dev_grid = QGridLayout(dev_box)
        dev_grid.setHorizontalSpacing(6)
        dev_grid.setVerticalSpacing(4)
        dev_grid.setContentsMargins(8, 4, 8, 4)
        dev_grid.addWidget(QLabel("Server device:"), 0, 0, Qt.AlignRight)
        self._server_device_combo = QComboBox()
        self._server_device_combo.setMinimumWidth(220)
        self._server_device_combo.addItem("(probing…)", userData=None)
        dev_grid.addWidget(self._server_device_combo, 0, 1)
        dev_grid.addWidget(QLabel("Port:"), 0, 2, Qt.AlignRight)
        self._server_port_spin = QSpinBox()
        self._server_port_spin.setRange(1, 8)
        self._server_port_spin.setValue(1)
        self._server_port_spin.setFixedWidth(56)
        self._server_port_spin.setToolTip(_PORT_FIELD_TOOLTIP)
        dev_grid.addWidget(self._server_port_spin, 0, 3)

        dev_grid.addWidget(QLabel("Client device:"), 1, 0, Qt.AlignRight)
        self._client_device_combo = QComboBox()
        self._client_device_combo.setMinimumWidth(220)
        self._client_device_combo.addItem("(probing…)", userData=None)
        dev_grid.addWidget(self._client_device_combo, 1, 1)
        dev_grid.addWidget(QLabel("Port:"), 1, 2, Qt.AlignRight)
        self._client_port_spin = QSpinBox()
        self._client_port_spin.setRange(1, 8)
        self._client_port_spin.setValue(1)
        self._client_port_spin.setFixedWidth(56)
        self._client_port_spin.setToolTip(_PORT_FIELD_TOOLTIP)
        dev_grid.addWidget(self._client_port_spin, 1, 3)
        dev_grid.setColumnStretch(1, 1)
        root.addWidget(dev_box)

        # ── Test parameters — 2-column grid. Was an 8-row vertical
        # QFormLayout; now 4 rows of (label/widget, label/widget)
        # pairs + 1 row for the two checkboxes. Same widgets, same
        # tooltips, same handlers; just denser.
        test_box = QGroupBox("Test parameters")
        tg = QGridLayout(test_box)
        tg.setHorizontalSpacing(8)
        tg.setVerticalSpacing(4)
        tg.setContentsMargins(8, 4, 8, 4)

        # Pre-build every widget, then place them in pairs.
        self._test_combo = QComboBox()
        for tid, label, _group in _TESTS:
            self._test_combo.addItem(label, userData=tid)
        self._test_combo.setToolTip(
            "ib_send_bw / ib_write_bw / ib_read_bw drive bandwidth tests.\n"
            "ib_send_lat / ib_write_lat / ib_read_lat drive latency tests "
            "(single-op ping-pong; pps will be low — that's the point)."
        )

        self._mtu_combo = QComboBox()
        for code, label in _MTU_OPTIONS:
            self._mtu_combo.addItem(label, userData=code)
        self._mtu_combo.setCurrentIndex(len(_MTU_OPTIONS) - 1)  # 4096 B
        self._mtu_combo.setToolTip(
            "perftest -m. MUST match the HCA's active_mtu — pick a value "
            "ABOVE active_mtu and the run fails with a clear error from "
            "perftest. Default 4096 B works on most modern RoCE adapters."
        )

        self._msg_size_spin = QSpinBox()
        self._msg_size_spin.setRange(2, 16 * 1024 * 1024)
        self._msg_size_spin.setSingleStep(1024)
        self._msg_size_spin.setValue(_DEFAULT_MSG_SIZE)
        self._msg_size_spin.setSuffix(" B")
        self._msg_size_spin.setFixedWidth(120)
        self._msg_size_spin.setToolTip(
            "Bytes per posted operation (-s). Larger = higher BW per op, "
            "fewer ops/sec. 64 KiB is perftest's own default and a good "
            "balance for RoCE/IB."
        )

        self._tx_depth_spin = QSpinBox()
        self._tx_depth_spin.setRange(1, 4096)
        self._tx_depth_spin.setValue(_DEFAULT_TX_DEPTH)
        self._tx_depth_spin.setFixedWidth(120)
        self._tx_depth_spin.setToolTip(
            "TX queue depth (-t). 128 is perftest's default; raise for "
            "more in-flight ops if the BW row shows the link unsaturated."
        )

        self._qp_count_spin = QSpinBox()
        # v0.3.15: raised 1024 → 131072 to match the typical Mellanox
        # ConnectX-7 max_qp ceiling (visible per-device in
        # Tools → RDMA → RDMA Devices).
        self._qp_count_spin.setRange(1, 131072)
        self._qp_count_spin.setValue(_DEFAULT_QP_COUNT)
        self._qp_count_spin.setFixedWidth(120)
        self._qp_count_spin.setToolTip(
            "Parallel QPs (-q). Increase to scale across multiple CPU "
            "cores on the HCA. >1 changes the BW report to per-QP "
            "totals; check perftest output before interpreting.\n\n"
            "Practical envelope:\n"
            "  • 1–16: standard BW scaling\n"
            "  • 32–128: queue saturation, CPU-bound\n"
            "  • 256+: synthetic stress; HCA max_qp shown per device "
            "in Tools → RDMA → RDMA Devices (v0.3.15+)"
        )

        self._gid_index_spin = QSpinBox()
        self._gid_index_spin.setRange(0, 255)
        self._gid_index_spin.setValue(_DEFAULT_GID_INDEX)
        self._gid_index_spin.setFixedWidth(120)
        self._gid_index_spin.setToolTip(
            "GID index (-x). On Mellanox RoCEv2-IPv4 the default is "
            "usually 3; check `show_gids` or the Devices list above. "
            "Wrong GID index → 'Unable to perform connection' from "
            "perftest."
        )

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 3600)
        self._duration_spin.setValue(_DEFAULT_DURATION_SECS)
        self._duration_spin.setSuffix(" sec")
        self._duration_spin.setFixedWidth(120)
        self._duration_spin.setToolTip(
            "Test duration in seconds (-D). When set, takes precedence "
            "over a fixed iteration count."
        )

        self._bidir_check = QCheckBox("Bidirectional (-b)")
        self._bidir_check.setToolTip(
            "Run BW in both directions simultaneously. Only meaningful "
            "for the _bw tests."
        )
        self._cpu_util_check = QCheckBox("Report CPU utilisation (--cpu_util)")

        # Layout — 4 rows × 2 columns + 1 row of checkboxes.
        # Row 0: Test type | MTU
        tg.addWidget(QLabel("Test type:"), 0, 0, Qt.AlignRight)
        tg.addWidget(self._test_combo,     0, 1)
        tg.addWidget(QLabel("MTU:"),       0, 2, Qt.AlignRight)
        tg.addWidget(self._mtu_combo,      0, 3)
        # Row 1: Message size | TX depth
        tg.addWidget(QLabel("Message size:"), 1, 0, Qt.AlignRight)
        tg.addWidget(self._msg_size_spin,     1, 1)
        tg.addWidget(QLabel("TX depth:"),     1, 2, Qt.AlignRight)
        tg.addWidget(self._tx_depth_spin,     1, 3)
        # Row 2: QP count | GID index
        tg.addWidget(QLabel("QP count:"),  2, 0, Qt.AlignRight)
        tg.addWidget(self._qp_count_spin,  2, 1)
        tg.addWidget(QLabel("GID index:"), 2, 2, Qt.AlignRight)
        tg.addWidget(self._gid_index_spin, 2, 3)
        # Row 3: Duration | (free for the bidir checkbox to flow into
        #                    the spacious slot)
        tg.addWidget(QLabel("Duration:"),  3, 0, Qt.AlignRight)
        tg.addWidget(self._duration_spin,  3, 1)
        # Row 4: both checkboxes inline
        cb_row = QHBoxLayout()
        cb_row.setSpacing(16)
        cb_row.addWidget(self._bidir_check)
        cb_row.addWidget(self._cpu_util_check)
        cb_row.addStretch(1)
        cb_holder = QWidget()
        cb_holder.setLayout(cb_row)
        tg.addWidget(cb_holder, 4, 0, 1, 4)
        # Stretch values get the widgets to be wide enough.
        tg.setColumnStretch(1, 1)
        tg.setColumnStretch(3, 1)
        root.addWidget(test_box)

        # ── Action row: Start, Stop, inline status label. One line
        # instead of two (saves vertical space; status reads as part
        # of the action zone, which is the natural place to look
        # after clicking Start).
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._start_btn = QPushButton("Start")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        action_row.addWidget(self._start_btn)
        action_row.addWidget(self._stop_btn)
        action_row.addSpacing(8)
        self._status_label = QLabel(
            "<span style='color:#64748b;'>Idle. Pick a device on each "
            "side and click Start.</span>"
        )
        self._status_label.setWordWrap(True)
        action_row.addWidget(self._status_label, 1)
        root.addLayout(action_row)

        # ── Live stats panel (populated by _poll_jobs).
        stats_box = QGroupBox("Live stats")
        sv = QVBoxLayout(stats_box)
        self._stats_view = QTextEdit()
        self._stats_view.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._stats_view.setFont(mono)
        self._stats_view.setMinimumHeight(160)
        self._stats_view.setPlaceholderText(
            "Stats appear here once perftest is running on both sides."
        )
        sv.addWidget(self._stats_view)
        root.addWidget(stats_box)

        # Close button at the bottom — uses Qt's standard button box so
        # macOS / Windows / X11 conventions all behave correctly.
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    # ─────────── hook called by menu handler

    def set_sibling_iface_provider(self, fn) -> None:
        """Caller passes a 0-arg callable returning the set of
        (tg_url, device) tuples already claimed by sibling Blast RDMA
        dialogs. We use it to warn the operator before starting a
        second perftest run on the same HCA."""
        self._sibling_iface_provider = fn

    # ─────────── probing

    def _probe_both_sides(self) -> None:
        """Fire /api/rdma/perftest/installed + /api/rdma/devices on
        each TG to populate the combos."""
        # Server side.
        _get_async(
            self,
            f"{self._server_tg_url}/api/rdma/perftest/installed",
            lambda data, err: self._on_installed_resp("server", data, err),
        )
        _get_async(
            self,
            f"{self._server_tg_url}/api/rdma/devices",
            lambda data, err: self._on_devices_resp("server", data, err),
        )
        # Client side — skip duplicate request if same URL (loopback).
        if self._client_tg_url != self._server_tg_url:
            _get_async(
                self,
                f"{self._client_tg_url}/api/rdma/perftest/installed",
                lambda data, err: self._on_installed_resp("client", data, err),
            )
            _get_async(
                self,
                f"{self._client_tg_url}/api/rdma/devices",
                lambda data, err: self._on_devices_resp("client", data, err),
            )

    def _on_installed_resp(self, side: str, data: Optional[dict], err: str) -> None:
        if err or not data:
            self._set_status_error(
                f"{side} TG unreachable while probing perftest: "
                f"{err or 'no data'}"
            )
            # Don't start retry — server unreachable, not a transient
            # missing-package situation. Operator needs to fix
            # connectivity manually.
            return
        installed = bool(data.get("installed"))
        if not installed:
            self._set_status_error(
                f"perftest is NOT installed on {side} TG. "
                "Install with `apt install perftest` (or your distro's "
                "equivalent). v0.3.18+ servers auto-install in the "
                "background — this banner will clear automatically "
                "within ~2 min if so."
            )
            self._start_btn.setEnabled(False)
            self._perftest_missing_sides.add(side)
            self._maybe_start_perftest_retry()
            return
        # Success path. If we were retrying because this side was
        # previously missing, that's now resolved.
        self._perftest_missing_sides.discard(side)
        if not self._perftest_missing_sides and self._perftest_retry_timer is not None:
            # Both sides now have perftest — stop retrying, clear the
            # banner, re-enable Start. The device probe (which fired
            # alongside us in _probe_both_sides) will set the proper
            # ready-state status when its devices arrive.
            self._stop_perftest_retry()
            self._status_label.setText("")
            self._start_btn.setEnabled(True)

    def _maybe_start_perftest_retry(self) -> None:
        """Begin re-probing /api/rdma/perftest/installed every 5 sec
        while at least one TG reports perftest missing. Idempotent:
        if a timer is already running (the OTHER side hit the missing
        case first), this call is a no-op.

        Capped at 24 ticks (2 min) — v0.3.18 server-side auto-install
        completes in ~30 sec; if perftest still isn't there after 2 min
        something else is broken (apt failure, kill-switch, wrong
        distro) and re-probing is no longer informative."""
        if self._perftest_retry_timer is not None:
            return
        self._perftest_retry_attempts = 0
        self._perftest_retry_timer = QTimer(self)
        self._perftest_retry_timer.timeout.connect(self._perftest_retry_tick)
        self._perftest_retry_timer.start(5000)  # 5 sec

    def _perftest_retry_tick(self) -> None:
        """One retry beat. Re-probes both sides via the existing
        _probe_both_sides() flow — _on_installed_resp handles success
        (clear banner + stop timer) and continued failure (timer
        keeps firing). Hits the 2-min cap → give up gracefully."""
        self._perftest_retry_attempts += 1
        if self._perftest_retry_attempts > 24:  # 24 * 5s = 2 min
            self._stop_perftest_retry()
            # Leave the existing red banner as-is — operator now
            # knows auto-install didn't land within the expected
            # window and should investigate
            # /var/log/netgen-auto-install.log on the server.
            return
        self._probe_both_sides()

    def _stop_perftest_retry(self) -> None:
        """Idempotent timer teardown — called from success, max-attempts,
        and closeEvent paths. Safe to call when the timer was never
        started."""
        if self._perftest_retry_timer is not None:
            self._perftest_retry_timer.stop()
            self._perftest_retry_timer = None
        # Don't reset _perftest_missing_sides here — that state is
        # cleared incrementally as each side reports installed=True
        # in _on_installed_resp.

    # NOTE: closeEvent override lives further down (~line 848) in this
    # class — see the existing one that handles the "stop the running
    # perftest job on close". The v0.3.19 retry-timer teardown is
    # appended there to avoid two closeEvent defs (Python would let
    # the second silently shadow the first — a real bug that bit us
    # during initial implementation).

    def _on_devices_resp(self, side: str, data: Optional[dict], err: str) -> None:
        combo = (self._server_device_combo if side == "server"
                 else self._client_device_combo)
        # For loopback (same URL), populate both combos from the one
        # response — the second request was suppressed.
        also_combo = (self._client_device_combo if side == "server"
                      and self._server_tg_url == self._client_tg_url
                      else None)

        combo.clear()
        if also_combo is not None:
            also_combo.clear()

        if err or not data:
            combo.addItem(f"(probe failed: {err or 'no data'})", userData=None)
            if also_combo is not None:
                also_combo.addItem(f"(probe failed: {err or 'no data'})", userData=None)
            return

        devices = data.get("devices") or []
        if not devices:
            combo.addItem("(no RDMA devices)", userData=None)
            if also_combo is not None:
                also_combo.addItem("(no RDMA devices)", userData=None)
            return

        for dev in devices:
            name = dev.get("name") or "?"
            ports = dev.get("ports") or []
            # Summarize first port's link_layer + rate for the label so
            # operator can tell RoCEv2 from native IB at a glance.
            first = ports[0] if ports else {}
            link = first.get("link_layer", "?")
            rate = first.get("rate", "?")
            state = first.get("state", "?")
            # v0.3.16+: surface the kernel netdev name(s) so the
            # operator can correlate the abstract `mlx5_N` ID with
            # `ip link` / IP config. Without this, operators see
            # only "mlx5_0", "mlx5_3", "mlx5_5" and have to manually
            # cross-reference each HCA with their network topology
            # to know which port carries the test traffic. Joined
            # with `+` so dual-netdev HCAs (bonded, etc.) show both.
            net_ifaces = dev.get("net_ifaces") or []
            iface_tag = f", iface={'+'.join(net_ifaces)}" if net_ifaces else ""
            label = f"{name}  [{link}, {rate}, port1={state}{iface_tag}]"
            combo.addItem(label, userData=name)
            if also_combo is not None:
                also_combo.addItem(label, userData=name)

    # ─────────── start / stop

    def _set_status_ok(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#15803d;'>{text}</span>")

    def _set_status_warn(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#b45309;'>{text}</span>")

    def _set_status_error(self, text: str) -> None:
        self._status_label.setText(
            f"<span style='color:#b91c1c;'>{text}</span>")

    def _common_opts(self) -> Dict[str, Any]:
        """Compose the test params that go on BOTH sides verbatim."""
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

    def _check_sibling_conflict(self, side: str, tg_url: str, device: str) -> bool:
        """Return True if OK to proceed; False if operator cancelled."""
        sibs = self._sibling_iface_provider() or set()
        if (tg_url, device) in sibs:
            ans = QMessageBox.warning(
                self, "Sibling Blast RDMA Flow",
                f"Another Blast RDMA Flow dialog is already targeting "
                f"<b>{device}</b> on {side} TG.\n\n"
                "Two perftest runs on the same HCA usually work but can "
                "share queue resources and skew each other's BW report. "
                "Proceed?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            return ans == QMessageBox.Yes
        return True

    def _on_start_clicked(self) -> None:
        server_dev = self._server_device_combo.currentData()
        client_dev = self._client_device_combo.currentData()
        if not server_dev:
            self._set_status_error("Pick a server-side RDMA device.")
            return
        if not client_dev:
            self._set_status_error("Pick a client-side RDMA device.")
            return
        if not self._check_sibling_conflict("server", self._server_tg_url, server_dev):
            return
        if not self._check_sibling_conflict("client", self._client_tg_url, client_dev):
            return

        test_id = self._test_combo.currentData()
        self._handshake_id = str(uuid.uuid4())
        opts = self._common_opts()

        # v0.3.19: reset per-side finished tracking + render-dedup so
        # a second run in the same dialog session doesn't see stale
        # state from the previous run (would otherwise short-circuit
        # _on_both_finished() immediately or suppress the first
        # render).
        self._server_finished = False
        self._client_finished = False
        self._last_rendered_key = {"server": None, "client": None}

        # Step 1: tell server TG to listen.
        server_body = {
            "role": "server",
            "test": test_id,
            "device": server_dev,
            "ib_port": int(self._server_port_spin.value()),
            "handshake_id": self._handshake_id,
            "note": f"Blast RDMA Flow / {test_id}",
            **opts,
        }
        self._set_status_ok(
            f"Asking server TG to start perftest server "
            f"(handshake={self._handshake_id[:8]})…"
        )
        self._start_btn.setEnabled(False)
        _post_async(
            self,
            f"{self._server_tg_url}/api/rdma/perftest/start",
            server_body,
            lambda data, err: self._on_server_started(
                data, err, test_id, client_dev, opts,
            ),
        )

    def _on_server_started(
        self,
        data: Optional[dict],
        err: str,
        test_id: str,
        client_dev: str,
        opts: Dict[str, Any],
    ) -> None:
        if err or not data or data.get("status") != "started":
            self._start_btn.setEnabled(True)
            msg = err or (data.get("error") if data else "unknown error")
            self._set_status_error(f"Server-side start failed: {msg}")
            return
        self._server_job_id = data.get("job_id")
        listen_addr = data.get("listen_addr")
        listen_port = data.get("listen_port")
        if not listen_addr:
            self._set_status_warn(
                "Server started but no listen_addr was advertised. "
                "Falling back to the server TG's URL host for client "
                "connect — set peer_addr manually if this fails."
            )
            # crude fallback: pull host from the URL
            try:
                from urllib.parse import urlparse
                listen_addr = urlparse(self._server_tg_url).hostname
            except Exception:
                listen_addr = None
        if not listen_addr:
            self._set_status_error(
                "Server started but cannot resolve peer address. Stopping."
            )
            self._teardown_server_only()
            return

        # Step 2: tell client TG to dial it.
        client_body = {
            "role": "client",
            "test": test_id,
            "device": client_dev,
            "ib_port": int(self._client_port_spin.value()),
            "handshake_id": self._handshake_id,
            "peer_addr": listen_addr,
            "listen_port": listen_port,
            "note": f"Blast RDMA Flow / {test_id}",
            **opts,
        }
        self._set_status_ok(
            f"Server up at {listen_addr}:{listen_port}. Asking client "
            f"TG to connect…"
        )
        _post_async(
            self,
            f"{self._client_tg_url}/api/rdma/perftest/start",
            client_body,
            self._on_client_started,
        )

    def _on_client_started(self, data: Optional[dict], err: str) -> None:
        if err or not data or data.get("status") != "started":
            msg = err or (data.get("error") if data else "unknown error")
            self._set_status_error(
                f"Client-side start failed: {msg}. Server-side job is "
                "still running — clicking Stop will tear both down."
            )
            self._stop_btn.setEnabled(True)
            return
        self._client_job_id = data.get("job_id")
        self._set_status_ok(
            f"Both halves running. handshake={self._handshake_id[:8]} "
            f"server_job={self._server_job_id[:8]} "
            f"client_job={self._client_job_id[:8]}"
        )
        self._stop_btn.setEnabled(True)
        self._start_poll_timer()

    def _start_poll_timer(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_jobs)
        self._poll_timer.start()
        # Kick once immediately so the panel populates before the first
        # 2 s tick.
        self._poll_jobs()

    def _poll_jobs(self) -> None:
        if self._server_job_id:
            _get_async(
                self,
                f"{self._server_tg_url}/api/rdma/perftest/job/{self._server_job_id}",
                lambda data, err: self._on_job_resp("server", data, err),
                timeout=3.0,
            )
        if self._client_job_id:
            _get_async(
                self,
                f"{self._client_tg_url}/api/rdma/perftest/job/{self._client_job_id}",
                lambda data, err: self._on_job_resp("client", data, err),
                timeout=3.0,
            )

    def _on_job_resp(self, side: str, data: Optional[dict], err: str) -> None:
        if err or not data:
            self._stats_view.append(f"[{side}] poll failed: {err or 'no data'}")
            return
        job = data.get("job") or {}

        # v0.3.19: dedup the rendered chunk. Pre-fix the poll appended
        # the SAME "[server] done (rc=0) size=65536B ..." line every
        # 2-sec tick because the chunk content only depends on the
        # job's terminal state (which doesn't change once done). Key
        # off the fields that DO change between meaningful states so
        # we render exactly once per transition (running-with-no-data
        # → running-with-partial-data → done → done-with-error).
        # While running-with-no-data, the elapsed-time changes — we
        # WANT that ticking forward so the operator sees progress.
        if bool(job.get("running")) and not any(
            job.get(k) is not None for k in (
                "final_msg_size_bytes", "final_iterations",
                "final_bw_avg_gbps", "final_bw_peak_gbps",
                "final_msg_rate_mpps",
                "final_lat_avg_us", "final_lat_min_us",
                "final_lat_max_us", "final_lat_p99_us",
            )
        ):
            # Running, no data — let every tick render (elapsed time
            # progress is the point).
            self._render_job_into_stats(side, job)
        else:
            render_key = (
                bool(job.get("running")),
                job.get("returncode"),
                job.get("final_msg_size_bytes"),
                job.get("final_iterations"),
                job.get("final_bw_avg_gbps"),
                job.get("final_msg_rate_mpps"),
                job.get("final_lat_avg_us"),
                job.get("error"),
            )
            if self._last_rendered_key.get(side) != render_key:
                self._render_job_into_stats(side, job)
                self._last_rendered_key[side] = render_key

        # v0.3.19: track each side's finished state on the instance —
        # the previous _is_finished(side, job, want_side) returned
        # False whenever side != want_side, which meant the server's
        # poll callback couldn't see the client's done state and vice
        # versa, so _on_both_finished was never called.
        if job.get("finished_at") is not None:
            if side == "server":
                self._server_finished = True
            elif side == "client":
                self._client_finished = True

        s_done = (self._server_job_id is None) or self._server_finished
        c_done = (self._client_job_id is None) or self._client_finished
        if s_done and c_done:
            self._on_both_finished()

    def _render_job_into_stats(self, side: str, job: dict) -> None:
        """Append a one-line summary to the live stats panel."""
        if not job:
            return
        running = bool(job.get("running"))
        run_state = "running" if running else f"done (rc={job.get('returncode')})"
        is_lat = (job.get("test") or "").endswith("_lat")

        # v0.3.19 fix: perftest's parsed metrics (final_bw_avg_gbps,
        # final_msg_size_bytes, etc.) only populate AFTER perftest
        # emits at least one data row. The setup phase (QP handshake,
        # GID exchange, buffer alloc) can take 2-5 sec on the first
        # sample. Before the parser hits a data row, every final_*
        # field is None — formatting them produces a useless
        # "size=NoneB ... BW avg=None Gbps ... MsgRate=None Mpps"
        # line that operators see for the first several seconds of
        # every run. Detect the no-data-yet state and show a clean
        # message instead.
        if is_lat:
            metrics = (
                job.get("final_msg_size_bytes"),
                job.get("final_iterations"),
                job.get("final_lat_avg_us"),
                job.get("final_lat_min_us"),
                job.get("final_lat_max_us"),
                job.get("final_lat_p99_us"),
            )
        else:
            metrics = (
                job.get("final_msg_size_bytes"),
                job.get("final_iterations"),
                job.get("final_bw_avg_gbps"),
                job.get("final_bw_peak_gbps"),
                job.get("final_msg_rate_mpps"),
            )
        has_data = any(m is not None for m in metrics)

        if running and not has_data:
            # perftest is BATCH-mode by default: it runs the test
            # for `--duration` seconds (or N iterations), then prints
            # the bandwidth + msg-rate summary on a single line AT
            # THE END. There are no per-second data rows during the
            # run. So every poll between t=0 and t=duration sees
            # final_* = None — which the pre-v0.3.19 format string
            # rendered as "size=NoneB iters=None BW avg=None Gbps
            # peak=None MsgRate=None Mpps" on every poll. Show a
            # clean progress line with elapsed time instead.
            started_at = job.get("started_at")
            elapsed = ""
            if isinstance(started_at, (int, float)):
                try:
                    import time as _t
                    elapsed = f" — {int(_t.time() - started_at)}s elapsed"
                except Exception:
                    pass
            chunk = (
                f"[{side}] {run_state}  (perftest emits results on "
                f"completion, not during run{elapsed})"
            )
        elif is_lat:
            chunk = (
                f"[{side}] {run_state}  size={job.get('final_msg_size_bytes')}B  "
                f"iters={job.get('final_iterations')}  "
                f"lat avg={job.get('final_lat_avg_us')}µs  "
                f"min={job.get('final_lat_min_us')}  "
                f"max={job.get('final_lat_max_us')}  "
                f"p99={job.get('final_lat_p99_us')}"
            )
        else:
            chunk = (
                f"[{side}] {run_state}  size={job.get('final_msg_size_bytes')}B  "
                f"iters={job.get('final_iterations')}  "
                f"BW avg={job.get('final_bw_avg_gbps')} Gbps  "
                f"peak={job.get('final_bw_peak_gbps')}  "
                f"MsgRate={job.get('final_msg_rate_mpps')} Mpps"
            )
        if job.get("error"):
            chunk += f"  err={job.get('error')[:120]}"
        self._stats_view.append(chunk)

    def _on_both_finished(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._set_status_ok(
            "Both halves finished. Click Stop to forget the pairing "
            "(or close this dialog)."
        )

    def _on_stop_clicked(self) -> None:
        self._stop_btn.setEnabled(False)
        # Stop both halves; forget pair on each side (idempotent).
        if self._server_job_id:
            _post_async(
                self,
                f"{self._server_tg_url}/api/rdma/perftest/stop",
                {"job_id": self._server_job_id, "forget_pair": True},
                lambda *_: None,
            )
        if self._client_job_id:
            _post_async(
                self,
                f"{self._client_tg_url}/api/rdma/perftest/stop",
                {"job_id": self._client_job_id, "forget_pair": True},
                lambda *_: None,
            )
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._set_status_warn("Stop requested. Both halves will tear down.")
        self._start_btn.setEnabled(True)

    def _teardown_server_only(self) -> None:
        """When server-side started but client-side never did, we still
        owe a stop to the server."""
        if self._server_job_id:
            _post_async(
                self,
                f"{self._server_tg_url}/api/rdma/perftest/stop",
                {"job_id": self._server_job_id, "forget_pair": True},
                lambda *_: None,
            )
        self._start_btn.setEnabled(True)

    # ─────────── close

    def closeEvent(self, event) -> None:
        """Best-effort stop on close so a closed dialog doesn't leave
        perftest hammering the wire.

        v0.3.19: also tear down the perftest-retry timer and the
        live-stats poll timer so Qt doesn't deliver tick events to
        a deleted widget (classic SIGABRT cause we fought in
        v0.2.20–v0.2.24)."""
        if self._server_job_id or self._client_job_id:
            self._on_stop_clicked()
        # Timer teardown — idempotent / safe on never-started timers.
        self._stop_perftest_retry()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        event.accept()
