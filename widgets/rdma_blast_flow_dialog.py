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

        self._build_ui()
        self._probe_both_sides()

    # ─────────── construction

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        hdr = QLabel(
            "<b>Blast a RDMA Flow</b><br/>"
            "<span style='color:#475569;'>"
            "perftest orchestrator. Spawns the matching ib_*_bw / ib_*_lat "
            "tool on each side via /api/rdma/perftest/start. Both halves "
            "share a handshake_id so the GUI can correlate them. "
            "No effect on the Streams tab — these are ephemeral "
            "perftest invocations, not stream-engine streams."
            "</span>"
        )
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        # ── TG endpoints group (URLs are passed in by the menu
        # action; we DISPLAY them here so the operator knows which
        # side is which, but we don't make them editable. To run
        # against a different pair, close this and pick from the
        # server tree.)
        tg_box = QGroupBox("Endpoints")
        tg_grid = QGridLayout(tg_box)
        tg_grid.setHorizontalSpacing(8)
        tg_grid.setVerticalSpacing(6)
        tg_grid.addWidget(QLabel("Server TG:"), 0, 0, Qt.AlignRight)
        tg_grid.addWidget(QLabel(f"<code>{self._server_tg_label} → {self._server_tg_url}</code>"), 0, 1)
        tg_grid.addWidget(QLabel("Client TG:"), 1, 0, Qt.AlignRight)
        same_tg = self._server_tg_url == self._client_tg_url
        client_label = (
            f"<code>{self._client_tg_label} → {self._client_tg_url}</code>"
            + ("  <i>(same as server — loopback test)</i>" if same_tg else "")
        )
        tg_grid.addWidget(QLabel(client_label), 1, 1)
        root.addWidget(tg_box)

        # ── Device picks. Two combos — one per side. Populated
        # asynchronously by _probe_both_sides() which calls
        # /api/rdma/devices on each TG.
        dev_box = QGroupBox("RDMA Devices")
        dev_grid = QGridLayout(dev_box)
        dev_grid.setHorizontalSpacing(8)
        dev_grid.setVerticalSpacing(8)
        dev_grid.addWidget(QLabel("Server device:"), 0, 0, Qt.AlignRight)
        self._server_device_combo = QComboBox()
        self._server_device_combo.setMinimumWidth(220)
        self._server_device_combo.addItem("(probing…)", userData=None)
        dev_grid.addWidget(self._server_device_combo, 0, 1)
        dev_grid.addWidget(QLabel("IB port:"), 0, 2, Qt.AlignRight)
        self._server_port_spin = QSpinBox()
        self._server_port_spin.setRange(1, 8)
        self._server_port_spin.setValue(1)
        dev_grid.addWidget(self._server_port_spin, 0, 3)

        dev_grid.addWidget(QLabel("Client device:"), 1, 0, Qt.AlignRight)
        self._client_device_combo = QComboBox()
        self._client_device_combo.setMinimumWidth(220)
        self._client_device_combo.addItem("(probing…)", userData=None)
        dev_grid.addWidget(self._client_device_combo, 1, 1)
        dev_grid.addWidget(QLabel("IB port:"), 1, 2, Qt.AlignRight)
        self._client_port_spin = QSpinBox()
        self._client_port_spin.setRange(1, 8)
        self._client_port_spin.setValue(1)
        dev_grid.addWidget(self._client_port_spin, 1, 3)
        root.addWidget(dev_box)

        # ── Test + params group.
        test_box = QGroupBox("Test")
        form = QFormLayout(test_box)
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        self._test_combo = QComboBox()
        for tid, label, _group in _TESTS:
            self._test_combo.addItem(label, userData=tid)
        self._test_combo.setToolTip(
            "ib_send_bw / ib_write_bw / ib_read_bw drive bandwidth tests.\n"
            "ib_send_lat / ib_write_lat / ib_read_lat drive latency tests "
            "(single-op ping-pong; pps will be low — that's the point)."
        )
        form.addRow("Test type:", self._test_combo)

        self._msg_size_spin = QSpinBox()
        self._msg_size_spin.setRange(2, 16 * 1024 * 1024)
        self._msg_size_spin.setSingleStep(1024)
        self._msg_size_spin.setValue(_DEFAULT_MSG_SIZE)
        self._msg_size_spin.setSuffix(" B")
        self._msg_size_spin.setToolTip(
            "Bytes per posted operation (-s). Larger = higher BW per op, "
            "fewer ops/sec. 64 KiB is perftest's own default and a good "
            "balance for RoCE/IB."
        )
        form.addRow("Message size:", self._msg_size_spin)

        self._qp_count_spin = QSpinBox()
        self._qp_count_spin.setRange(1, 1024)
        self._qp_count_spin.setValue(_DEFAULT_QP_COUNT)
        self._qp_count_spin.setToolTip(
            "Parallel QPs (-q). Increase to scale across multiple CPU "
            "cores on the HCA. >1 changes the BW report to per-QP "
            "totals; check perftest output before interpreting."
        )
        form.addRow("QP count:", self._qp_count_spin)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(1, 3600)
        self._duration_spin.setValue(_DEFAULT_DURATION_SECS)
        self._duration_spin.setSuffix(" sec")
        self._duration_spin.setToolTip(
            "Test duration in seconds (-D). When set, takes precedence "
            "over a fixed iteration count."
        )
        form.addRow("Duration:", self._duration_spin)

        self._mtu_combo = QComboBox()
        for code, label in _MTU_OPTIONS:
            self._mtu_combo.addItem(label, userData=code)
        self._mtu_combo.setCurrentIndex(len(_MTU_OPTIONS) - 1)  # 4096 B
        self._mtu_combo.setToolTip(
            "perftest -m. MUST match the HCA's active_mtu — pick a value "
            "ABOVE active_mtu and the run fails with a clear error from "
            "perftest. Default 4096 B works on most modern RoCE adapters."
        )
        form.addRow("MTU:", self._mtu_combo)

        self._tx_depth_spin = QSpinBox()
        self._tx_depth_spin.setRange(1, 4096)
        self._tx_depth_spin.setValue(_DEFAULT_TX_DEPTH)
        self._tx_depth_spin.setToolTip(
            "TX queue depth (-t). 128 is perftest's default; raise for "
            "more in-flight ops if the BW row shows the link unsaturated."
        )
        form.addRow("TX depth:", self._tx_depth_spin)

        self._gid_index_spin = QSpinBox()
        self._gid_index_spin.setRange(0, 255)
        self._gid_index_spin.setValue(_DEFAULT_GID_INDEX)
        self._gid_index_spin.setToolTip(
            "GID index (-x). On Mellanox RoCEv2-IPv4 the default is "
            "usually 3; check `show_gids` or the Devices list above. "
            "Wrong GID index → 'Unable to perform connection' from "
            "perftest."
        )
        form.addRow("GID index:", self._gid_index_spin)

        self._bidir_check = QCheckBox("Bidirectional (-b)")
        self._bidir_check.setToolTip(
            "Run BW in both directions simultaneously. Only meaningful "
            "for the _bw tests."
        )
        form.addRow("", self._bidir_check)

        self._cpu_util_check = QCheckBox("Report CPU utilisation (--cpu_util)")
        form.addRow("", self._cpu_util_check)

        root.addWidget(test_box)

        # ── Start / Stop + status.
        action_row = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        action_row.addWidget(self._start_btn)
        action_row.addWidget(self._stop_btn)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._status_label = QLabel(
            "<span style='color:#64748b;'>Idle. Pick a device on each "
            "side and click Start.</span>"
        )
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

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
            return
        installed = bool(data.get("installed"))
        if not installed:
            self._set_status_error(
                f"perftest is NOT installed on {side} TG. "
                "Install with `apt install perftest` (or your distro's "
                "equivalent) and reopen."
            )
            self._start_btn.setEnabled(False)

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
            label = f"{name}  [{link}, {rate}, port1={state}]"
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
        self._render_job_into_stats(side, job)
        # Stop polling once BOTH sides are done.
        s_done = (self._server_job_id is None
                  or self._is_finished(side, job, "server"))
        c_done = (self._client_job_id is None
                  or self._is_finished(side, job, "client"))
        if s_done and c_done:
            self._on_both_finished()

    def _is_finished(self, side: str, job: dict, want_side: str) -> bool:
        if side != want_side:
            return False
        return job.get("finished_at") is not None

    def _render_job_into_stats(self, side: str, job: dict) -> None:
        """Append a one-line summary to the live stats panel."""
        if not job:
            return
        run_state = "running" if job.get("running") else f"done (rc={job.get('returncode')})"
        is_lat = (job.get("test") or "").endswith("_lat")
        if is_lat:
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
        perftest hammering the wire."""
        if self._server_job_id or self._client_job_id:
            self._on_stop_clicked()
        event.accept()
