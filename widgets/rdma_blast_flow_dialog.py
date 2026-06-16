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
from PyQt5.QtGui import QFont, QTextCursor
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


def _detect_same_subnet_trap(srv_probe: dict, cli_probe: dict):
    """v0.5.152: given two `/api/rdma/probe` responses, detect if
    their kernel ifaces share an IPv4 subnet — the classic same-
    host loopback routing trap that prevents QP→RTR.

    Returns (trapped: bool, shared_net: str | None). When True,
    `shared_net` is the offending CIDR network string (e.g.
    "10.10.0.0/24") to surface in the confirm dialog.

    Treats DOWN ports as "no trap" — there's a different problem
    to fix first (link state). v0.5.153: `_detect_start_blockers`
    catches DOWN ports and missing IPs separately.
    """
    import ipaddress as _ip
    srv_ips = (srv_probe or {}).get("ip_addresses") or []
    cli_ips = (cli_probe or {}).get("ip_addresses") or []
    if not srv_ips or not cli_ips:
        return False, None
    srv_nets = set()
    for cidr in srv_ips:
        # Skip IPv6 — RoCEv2-IPv4 GIDs are the common case and
        # IPv6 GIDs don't suffer this trap in the same way.
        if ":" in cidr.split("/")[0]:
            continue
        try:
            srv_nets.add(str(_ip.IPv4Interface(cidr).network))
        except Exception:
            continue
    for cidr in cli_ips:
        if ":" in cidr.split("/")[0]:
            continue
        try:
            net = str(_ip.IPv4Interface(cidr).network)
        except Exception:
            continue
        if net in srv_nets:
            return True, net
    return False, None


def _detect_start_blockers(srv_probe: dict, cli_probe: dict):
    """v0.5.153: broader detection. Returns (reason, detail) where
    reason is one of:
      * `"probe_failed"` — either probe had an error key (network
        flake, server down, etc).
      * `"down_port"`    — at least one port is not ACTIVE; perftest
        will fail at QP→RTR with no auto-fix possible.
      * `"missing_ip"`   — at least one iface has no IPv4 address;
        perftest needs an IP for RoCEv2 GID resolution. AUTO-fixable
        via test_ifaces/configure.
      * `"same_subnet"`  — both ifaces in one IPv4 subnet on the
        same host; routing-via-lo trap. AUTO-fixable.
      * `None`           — clean; proceed with start.

    `detail` is a short string the confirm dialog substitutes into
    its message (the shared subnet, the DOWN port name, etc).

    Order matters: probe_failed and down_port are surfaced BEFORE
    missing_ip / same_subnet because they can't be auto-fixed.
    """
    # 1. Probe errors take precedence.
    for side, p in (("server", srv_probe), ("client", cli_probe)):
        if not p or p.get("error"):
            return "probe_failed", f"{side}: {p.get('error') if p else 'no data'}"
    # 2. DOWN ports — no auto-fix.
    for side, p in (("server", srv_probe), ("client", cli_probe)):
        state = (p.get("state") or "").upper()
        if state and state != "ACTIVE":
            return "down_port", (
                f"{p.get('hca', '?')} on {side} is {state}"
            )
    # 3. Missing IPv4 — auto-fixable.
    def _has_v4(p):
        for cidr in p.get("ip_addresses") or []:
            if ":" not in cidr.split("/")[0]:
                return True
        return False
    for side, p in (("server", srv_probe), ("client", cli_probe)):
        if not _has_v4(p):
            return "missing_ip", (
                f"{p.get('kernel_iface', '?')} on {side} has no IPv4"
            )
    # 4. Same-subnet trap.
    trapped, net = _detect_same_subnet_trap(srv_probe, cli_probe)
    if trapped:
        return "same_subnet", net
    return None, None


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
        # v0.5.155 parallel-worker BW scaling. Worker 0 stays in
        # `_server_job_id`/`_client_job_id` (back-compat with all
        # the existing single-worker code paths). Workers 1..N-1
        # land in `_extra_workers`, each as
        # {server: jid, client: jid, cpu_pin: int, worker_idx: int}.
        # Poll/stop loops iterate worker 0 + extras. Stats display
        # prefixes extras with [worker N].
        self._extra_workers: list = []
        # Cached `/api/rdma/host_info` snapshot (populated lazily
        # when the operator opens the Max-BW picker).
        self._host_info_cache: Optional[dict] = None
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
        # v0.5.150: state_ids from any pre-flight Apply. Cleaned
        # up on dialog close so test IPs never outlive the test.
        self._preflight_state_ids: set = set()

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

        # v0.5.149: inline hint clarifying that the "device"
        # combos are RDMA HCA names (mlx5_0, rocep…), not the
        # Ethernet iface picker used elsewhere in the GUI. Same
        # clarification the Topology dialog gained in v0.5.143.
        _hca_hint = QLabel(
            "<span style='color:#64748b; font-size:11px;'>"
            "<b>device</b> = RDMA HCA name (e.g. <code>mlx5_0</code>) "
            "— this is the InfiniBand verbs device, NOT an Ethernet "
            "interface (<code>ens2f0np0</code>). perftest addresses "
            "the HCA directly via libibverbs."
            "</span>"
        )
        _hca_hint.setWordWrap(True)
        dev_grid.addWidget(_hca_hint, 2, 0, 1, 4)

        # v0.5.147: Loopback shortcut. Same-host loopback is the
        # canonical RDMA smoke test (`ib_send_bw -d mlx5_0` on both
        # sides). Operator wanted one-click setup rather than
        # picking the same device twice manually. The button copies
        # the server-side selection (device + IB port) onto the
        # client side. Only meaningful when server and client TG
        # are the same host — disabled otherwise via the
        # `same_tg` check above.
        self._loopback_btn = QPushButton(
            "↔  Use server device for loopback "
            "(same HCA on both sides)"
        )
        self._loopback_btn.setToolTip(
            "Mirrors the server-side device + IB port onto the "
            "client side. The canonical RDMA smoke test: both "
            "perftest processes use the SAME HCA, the verbs layer "
            "bounces packets internally, no wire/switch needed.\n\n"
            "Use this first when troubleshooting — if same-HCA "
            "loopback fails, the issue is in the RDMA stack itself "
            "(GID, port state, driver) not link reachability."
        )
        self._loopback_btn.setEnabled(same_tg)
        if not same_tg:
            self._loopback_btn.setToolTip(
                "Disabled: loopback requires server and client TG "
                "to be the same host. Select one TG in the server "
                "tree and reopen this dialog."
            )
        self._loopback_btn.clicked.connect(self._mirror_server_to_client)
        dev_grid.addWidget(self._loopback_btn, 3, 0, 1, 2)

        # v0.5.149: Two-HCA same-host shortcut. Matches the toggle
        # added to the Topology dialog's same-host picker in
        # v0.5.148. When the operator wants to test the path
        # between sibling RoCE devices on one box (rocep…f0 ↔
        # rocep…f1), this button picks the NEXT available device
        # on the client side relative to the server's pick.
        self._other_hca_btn = QPushButton(
            "↔  Use OTHER HCA (same host two-port test)"
        )
        self._other_hca_btn.setToolTip(
            "Same TG, DIFFERENT HCAs. Picks the next available "
            "device on the client side (e.g. server=rocep…f0 → "
            "client=rocep…f1). Tests the wire/driver path between "
            "two RoCE devices on one host — requires a loopback "
            "cable, shared switch, or firmware internal port-to-"
            "port loopback.\n\n"
            "Use this AFTER same-HCA loopback succeeds. If "
            "loopback works but this fails, the RDMA stack is "
            "healthy and the issue is link reachability between "
            "the two HCAs (PFC config, cabling, GID mismatch)."
        )
        self._other_hca_btn.setEnabled(same_tg)
        if not same_tg:
            self._other_hca_btn.setToolTip(
                "Disabled: same-host two-HCA test requires server "
                "and client TG to be the same host. Select one "
                "TG in the server tree and reopen this dialog."
            )
        self._other_hca_btn.clicked.connect(self._pick_other_hca_for_client)
        dev_grid.addWidget(self._other_hca_btn, 3, 2, 1, 2)

        dev_grid.setColumnStretch(1, 1)
        root.addWidget(dev_box)

        # ── Test parameters — 2-column grid. Was an 8-row vertical
        # QFormLayout; now 4 rows of (label/widget, label/widget)
        # pairs + 1 row for the two checkboxes. Same widgets, same
        # tooltips, same handlers; just denser.
        test_box = QGroupBox("Test parameters")
        tg = QGridLayout(test_box)
        tg.setHorizontalSpacing(8)
        # v0.5.152: compact further — operator wanted more vertical
        # room for the Live stats panel below. Tighten spacings and
        # margins; shrink the spinbox fixed widths.
        # v0.5.160: operator screenshot showed v0.5.159's bump to
        # 8 was adding visible whitespace, not the kissing I
        # imagined. Back to 2 (the v0.5.152 baseline). Buttons in
        # row 2/3 (Verify, Max BW) get their row height capped
        # below so they don't push the rows taller than the bare
        # spinbox rows.
        tg.setVerticalSpacing(2)
        tg.setHorizontalSpacing(6)
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
        self._msg_size_spin.setFixedWidth(100)
        self._msg_size_spin.setToolTip(
            "Bytes per posted operation (-s). Larger = higher BW per op, "
            "fewer ops/sec. 64 KiB is perftest's own default and a good "
            "balance for RoCE/IB."
        )

        self._tx_depth_spin = QSpinBox()
        self._tx_depth_spin.setRange(1, 4096)
        self._tx_depth_spin.setValue(_DEFAULT_TX_DEPTH)
        self._tx_depth_spin.setFixedWidth(100)
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
        self._qp_count_spin.setFixedWidth(100)
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
        self._gid_index_spin.setFixedWidth(100)
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
        self._duration_spin.setFixedWidth(100)
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
        # v0.5.151: pair the QP spinbox with a "❓ Verify" button
        # that pops a help dialog containing concrete `rdma
        # resource show qp` commands pre-filled with the currently-
        # selected HCAs. Lets the operator confirm that perftest
        # is actually using N QPs without leaving netgen.
        _qp_row = QWidget()
        _qph = QHBoxLayout(_qp_row)
        _qph.setContentsMargins(0, 0, 0, 0)
        _qph.setSpacing(4)
        _qph.addWidget(self._qp_count_spin)
        self._qp_verify_btn = QPushButton("❓ Verify")
        # v0.5.159: was setFixedWidth(78) which clipped "Verify" on
        # macOS Big Sur+ once the system font went taller. Use a
        # min width and let Qt size up naturally for the platform's
        # font metrics.
        self._qp_verify_btn.setMinimumWidth(96)
        # v0.5.160: cap height so the QP-count row matches the
        # bare-spinbox row heights above and below. QPushButton's
        # default sizeHint includes more vertical padding than
        # QSpinBox on macOS — without this cap the QP count row
        # ends up ~6 px taller than the others.
        self._qp_verify_btn.setMaximumHeight(28)
        self._qp_verify_btn.setToolTip(
            "Open a help panel showing commands to verify that "
            "perftest is actually creating qp_count QPs on the "
            "selected HCAs. Commands are pre-filled with the "
            "host + device + port you picked above."
        )
        self._qp_verify_btn.clicked.connect(self._show_qp_verify_help)
        _qph.addWidget(self._qp_verify_btn)
        _qph.addStretch(1)
        tg.addWidget(_qp_row, 2, 1)
        tg.addWidget(QLabel("GID index:"), 2, 2, Qt.AlignRight)
        tg.addWidget(self._gid_index_spin, 2, 3)
        # Row 3: Duration | Parallel workers (v0.5.155)
        tg.addWidget(QLabel("Duration:"),  3, 0, Qt.AlignRight)
        tg.addWidget(self._duration_spin,  3, 1)
        # v0.5.155: Parallel workers spinbox + 🚀 Max BW button.
        # perftest is single-threaded — for genuine multi-core BW
        # scaling you spawn N processes, each pinned to a
        # different core via taskset. The button auto-picks
        # NUMA-local cores so the worker's CPU + RAM + HCA all
        # share a NUMA node (avoids the cross-NUMA penalty).
        tg.addWidget(QLabel("Parallel workers:"), 3, 2, Qt.AlignRight)
        self._parallel_workers_spin = QSpinBox()
        self._parallel_workers_spin.setRange(1, 64)
        self._parallel_workers_spin.setValue(1)
        self._parallel_workers_spin.setFixedWidth(100)
        self._parallel_workers_spin.setToolTip(
            "Number of perftest processes spawned in parallel, "
            "each pinned to a different CPU core via taskset. For "
            "genuine BW scaling on a single HCA — perftest itself "
            "is single-threaded, so >1 here is the ONLY way to "
            "exceed single-core line rate.\n\n"
            "Click 🚀 Max BW to auto-pick the optimal count based "
            "on the HCA's NUMA topology."
        )
        _pw_row = QWidget()
        _pwh = QHBoxLayout(_pw_row)
        _pwh.setContentsMargins(0, 0, 0, 0)
        _pwh.setSpacing(4)
        _pwh.addWidget(self._parallel_workers_spin)
        self._max_bw_btn = QPushButton("🚀 Max BW")
        # v0.5.159: was setFixedWidth(86) — too narrow on macOS,
        # clipped "Max BW". Use min width so Qt picks per-platform
        # font metrics.
        self._max_bw_btn.setMinimumWidth(108)
        # v0.5.160: cap height so the Parallel-workers row matches
        # the bare-spinbox row heights. Same reason as the QP
        # Verify button cap.
        self._max_bw_btn.setMaximumHeight(28)
        self._max_bw_btn.setToolTip(
            "Query the server's NUMA topology, find the HCA's "
            "home node, and set Parallel workers = number of CPU "
            "cores on that node. Workers run with "
            "`numactl --cpunodebind --membind taskset -c <core>` "
            "so CPU, RAM, and PCIe path all align with the HCA."
        )
        self._max_bw_btn.clicked.connect(self._on_max_bw_clicked)
        _pwh.addWidget(self._max_bw_btn)
        _pwh.addStretch(1)
        tg.addWidget(_pw_row, 3, 3)
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
        # v0.5.150: pre-flight check button. Probes both endpoints
        # for port state / IPs / GIDs / same-subnet trap and
        # offers to apply temporary test IPs.
        self._preflight_btn = QPushButton("🔍 Pre-flight check")
        self._preflight_btn.setToolTip(
            "Probe both endpoints for port state, link layer, "
            "IP addresses, and RoCEv2 GIDs. Detects the same-host "
            "same-subnet routing trap and offers temporary test "
            "IPs (runtime only — gone on reboot)."
        )
        self._preflight_btn.clicked.connect(self._on_preflight_clicked)
        action_row.addWidget(self._preflight_btn)
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
        # v0.5.152: bumped min-height 160 → 280 + addWidget stretch
        # factor 1 so the panel claims the freed-up vertical room
        # from the compacted test-params section above.
        stats_box = QGroupBox("Live stats")
        sv = QVBoxLayout(stats_box)
        sv.setContentsMargins(6, 4, 6, 6)
        self._stats_view = QTextEdit()
        self._stats_view.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        self._stats_view.setFont(mono)
        # v0.5.159: bumped 280 → 320. With 16 parallel workers the
        # "[worker N] both halves running" lines push the [client]
        # done summary below the visible area; the extra ~40 px
        # lets the operator see the BW row without scrolling.
        self._stats_view.setMinimumHeight(320)
        self._stats_view.setPlaceholderText(
            "Stats appear here once perftest is running on both sides."
        )
        # v0.5.159: auto-scroll to bottom on every append. Without
        # this, the QTextEdit only scrolls when the scrollbar is
        # already at max — long histories from the running-tick
        # spam left the [client] done line clipped at the bottom.
        self._stats_view.textChanged.connect(self._scroll_stats_to_bottom)
        sv.addWidget(self._stats_view)
        root.addWidget(stats_box, 1)

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

    # ───────── v0.5.159 stats view auto-scroll

    def _scroll_stats_to_bottom(self) -> None:
        """Pin the QTextEdit to the document end every time the
        contents change. The default Qt behavior only auto-scrolls
        when the bar is already at the bottom; after the running-
        tick spam (~270s × 2 sides ≈ 270 lines) operators want the
        final BW row visible without manually scrolling.

        v0.5.161: moved from `verticalScrollBar().setValue(max)`
        to `moveCursor(End) + QTimer.singleShot(0, ensureCursor
        Visible)`. The scrollbar's maximum is computed BEFORE the
        last wrapped line is laid out — the synchronous setValue
        was missing the wrap-line's vertical room, so the
        "MsgRate=…" continuation got clipped. Deferring to a
        zero-tick timer lets Qt's document layout settle first."""
        try:
            cur = self._stats_view.textCursor()
            cur.movePosition(QTextCursor.End)
            self._stats_view.setTextCursor(cur)
            QTimer.singleShot(
                0, self._stats_view.ensureCursorVisible)
        except Exception:
            pass

    # ───────── v0.5.150 pre-flight check

    def _on_preflight_clicked(self) -> None:
        """Open the RDMA pre-flight dialog. Probes both endpoints,
        surfaces port state / IPs / GIDs / same-subnet trap,
        offers temporary test IPs."""
        srv_dev = self._server_device_combo.currentData()
        cli_dev = self._client_device_combo.currentData()
        if not srv_dev or not cli_dev:
            QMessageBox.information(
                self, "Pre-flight check",
                "Pick a device on both sides first (combos are "
                "still probing).",
            )
            return
        endpoints = [
            ("Server", self._server_tg_url, srv_dev,
             int(self._server_port_spin.value())),
            ("Client", self._client_tg_url, cli_dev,
             int(self._client_port_spin.value())),
        ]
        from widgets.rdma_preflight_dialog import RdmaPreflightDialog
        # Same-host config goes through whichever TG actually
        # owns the kernel ifaces. For Blast that's the server's
        # TG URL (server and client are on the same host in the
        # interesting case).
        dlg = RdmaPreflightDialog(
            endpoints=endpoints,
            config_url=self._server_tg_url,
            parent=self,
        )
        dlg.exec_()
        # If the operator applied a test config, remember the
        # state_id so we clean it up on dialog close / Stop. (We
        # don't clean immediately — they may want to actually
        # run the perftest while the test IPs are live.)
        sid = dlg.applied_state_id()
        if sid:
            # v0.5.152: respect the "📌 Keep" checkbox. When set,
            # we DON'T track the state_id — closeEvent's auto-
            # cleanup will skip it. Operator must clean manually
            # later (Pre-flight → Clean up applied / Tools →
            # cleanup orphans / reboot).
            if dlg.keep_applied():
                self._set_status_ok(
                    f"Pre-flight applied test IPs (state_id="
                    f"{sid[:8]}). 📌 Keep is ON — cleanup will "
                    f"NOT fire on close; clean manually."
                )
            else:
                self._preflight_state_ids.add(sid)
                self._set_status_ok(
                    f"Pre-flight applied test IPs (state_id="
                    f"{sid[:8]}). Cleanup will fire on close."
                )

    # ───────── v0.5.152 auto-detect same-subnet trap on Start

    def _auto_probe_then_start(
        self, *, server_dev: str, client_dev: str,
    ) -> None:
        """Probe both endpoints via /api/rdma/probe, decide whether
        the same-subnet trap is in play, and either:
          * proceed directly with start (trap NOT present),
          * pop a 3-way confirm: Apply & Start / Continue / Cancel.
        """
        self._probe_buffer: Dict[str, Dict[str, Any]] = {}
        self._probe_targets = [
            ("server", self._server_tg_url, server_dev,
             int(self._server_port_spin.value())),
            ("client", self._client_tg_url, client_dev,
             int(self._client_port_spin.value())),
        ]

        def _done(side: str, data, err):
            self._probe_buffer[side] = data or {"error": err}
            if len(self._probe_buffer) < len(self._probe_targets):
                return
            self._on_auto_probe_complete(server_dev, client_dev)

        for side, url, hca, port in self._probe_targets:
            _get_async(
                self,
                f"{url.rstrip('/')}/api/rdma/probe?device={hca}&port={port}",
                lambda data, err, _s=side: _done(_s, data, err),
                timeout=4.0,
            )

    def _on_auto_probe_complete(
        self, server_dev: str, client_dev: str,
    ) -> None:
        """Both probes returned. v0.5.153: use the broader
        `_detect_start_blockers()` instead of subnet-only check —
        also catches DOWN ports, missing IPs, and probe failures
        and routes each to a contextual confirm."""
        srv_probe = self._probe_buffer.get("server", {}) or {}
        cli_probe = self._probe_buffer.get("client", {}) or {}
        reason, detail = _detect_start_blockers(srv_probe, cli_probe)
        if reason is None:
            self._proceed_with_start(server_dev, client_dev)
            return

        srv_iface = srv_probe.get("kernel_iface") or "<server-iface>"
        cli_iface = cli_probe.get("kernel_iface") or "<client-iface>"
        dlg = _StartBlockerConfirmDialog(
            reason=reason, detail=detail,
            srv_iface=srv_iface, cli_iface=cli_iface,
            parent=self,
        )
        result = dlg.exec_()
        choice = dlg.choice()
        if result != QDialog.Accepted or choice == "cancel":
            self._start_btn.setEnabled(True)
            self._set_status_ok("Start cancelled.")
            return
        if choice == "continue":
            self._proceed_with_start(server_dev, client_dev)
            return
        if choice == "open_preflight":
            # Operator wants to investigate manually — open the
            # Pre-flight dialog and re-enable Start. They can
            # apply fixes and click Start again.
            self._start_btn.setEnabled(True)
            self._set_status_ok("Opening Pre-flight…")
            self._on_preflight_clicked()
            return
        # choice == "apply" — only valid for missing_ip and
        # same_subnet. For down_port / probe_failed the dialog
        # doesn't offer Apply, so the operator can only Continue
        # / Cancel / Open Pre-flight.
        self._apply_test_ips_then_start(
            srv_iface=srv_iface, cli_iface=cli_iface,
            server_dev=server_dev, client_dev=client_dev,
        )

    def _apply_test_ips_then_start(
        self, *,
        srv_iface: str, cli_iface: str,
        server_dev: str, client_dev: str,
    ) -> None:
        """Auto-pick CIDRs, VALIDATE, then POST configure. On
        success track the state_id and proceed with Start.

        v0.5.153 fix: previous version POSTed configure directly
        without validating. If 10.43.0.0/24 was already a kernel
        route, the apply failed AFTER the operator already
        committed to Start. Now we validate first and refuse on
        any hard error issue (route overlap, existing IP, bad
        CIDR), telling the operator to open Pre-flight for manual
        resolution.
        """
        cidr_pair = ("10.42.0.1/24", "10.43.0.1/24")
        ifaces = [
            {"name": srv_iface, "cidr": cidr_pair[0]},
            {"name": cli_iface, "cidr": cidr_pair[1]},
        ]
        self._set_status_ok("Validating test IPs…")

        def _on_validated(data, err):
            if err:
                self._start_btn.setEnabled(True)
                self._set_status_error(
                    f"Validate failed: {err}. Open Pre-flight."
                )
                return
            if not (data or {}).get("ok"):
                issues = (data or {}).get("issues") or []
                err_lines = [
                    f"{i.get('iface', '?')}: {i.get('message', '')}"
                    for i in issues
                    if i.get("severity") == "error"
                ]
                msg = "; ".join(err_lines) or "validation failed"
                self._start_btn.setEnabled(True)
                self._set_status_error(
                    f"Auto-apply blocked: {msg}. Open Pre-flight "
                    f"to pick non-conflicting CIDRs."
                )
                return
            # Validation passed — now configure.
            self._set_status_ok("Applying temporary test IPs…")
            _post_async(
                self,
                f"{self._server_tg_url}/api/rdma/test_ifaces/configure",
                {"ifaces": ifaces, "disable_rp_filter": True},
                _on_applied,
            )

        def _on_applied(data, err):
            if err or not (data or {}).get("ok"):
                msg = err or (data or {}).get(
                    "error", "configure failed")
                self._start_btn.setEnabled(True)
                self._set_status_error(
                    f"Auto-apply failed: {msg}. Open Pre-flight "
                    f"to fix manually."
                )
                return
            sid = (data or {}).get("state_id")
            if sid:
                self._preflight_state_ids.add(sid)
            self._set_status_ok(
                f"Test IPs applied (state_id={sid[:8] if sid else '?'}). "
                f"Starting perftest…"
            )
            self._proceed_with_start(server_dev, client_dev)

        _post_async(
            self,
            f"{self._server_tg_url}/api/rdma/test_ifaces/validate",
            {"ifaces": ifaces}, _on_validated,
        )

    # ───────── v0.5.155 Parallel workers + 🚀 Max BW

    def _on_max_bw_clicked(self) -> None:
        """Query /api/rdma/host_info on the server-side TG and
        auto-pick the optimal worker count via
        `pick_workers_for_hca`. Sets the spinbox; doesn't fire
        Start (operator confirms via the Start button)."""
        hca = self._server_device_combo.currentData()
        if not hca:
            self._set_status_error(
                "Pick a server-side device first.")
            return
        self._set_status_ok("Querying host topology…")

        def _on_info(data, err):
            if err or not data:
                self._set_status_error(
                    f"host_info failed: {err or 'no data'}. Set "
                    f"Parallel workers manually.")
                return
            self._host_info_cache = data
            try:
                from utils.rdma_host_info import pick_workers_for_hca
                pick = pick_workers_for_hca(
                    hca=hca, requested=None, info=data)
            except Exception as exc:
                self._set_status_error(
                    f"pick_workers failed: {exc}")
                return
            wc = int(pick.get("worker_count") or 1)
            # Cap to the spinbox max.
            wc = max(1, min(wc, 64))
            self._parallel_workers_spin.setValue(wc)
            self._set_status_ok(
                f"🚀 {pick.get('reason', f'{wc} worker(s)')}"
            )

        _get_async(
            self,
            f"{self._server_tg_url}/api/rdma/host_info",
            _on_info, timeout=4.0,
        )

    def _start_extra_workers(
        self, *,
        test_id: str,
        server_dev: str, client_dev: str,
        opts: Dict[str, Any],
    ) -> None:
        """v0.5.155: after worker 0's (server, client) pair is up
        and running, fan out workers 1..N-1 with cpu_pin set per
        worker. Workers run concurrently — total BW = sum of all
        worker BWs.

        Uses the cached host_info (populated by the 🚀 Max BW
        button) when present to pick NUMA-local cores. Falls back
        to linear cores 0..N-1 when no cache is available.
        """
        worker_count = int(self._parallel_workers_spin.value())
        if worker_count <= 1:
            return  # single-worker case — nothing to do here.

        # Pick the CPU IDs for workers 1..N-1.
        info = self._host_info_cache
        numa_pin = None
        fallback_reason = None
        if info:
            try:
                from utils.rdma_host_info import pick_workers_for_hca
                pick = pick_workers_for_hca(
                    hca=server_dev,
                    requested=worker_count,
                    info=info,
                )
                cpus = pick.get("cpus") or list(range(worker_count))
                numa_pin = pick.get("numa_pin")
                if numa_pin is None:
                    fallback_reason = (
                        f"HCA {server_dev} not in host's NUMA map — "
                        f"linear CPU ordering, no NUMA pin"
                    )
            except Exception as exc:
                cpus = list(range(worker_count))
                fallback_reason = f"pick_workers_for_hca failed: {exc}"
        else:
            cpus = list(range(worker_count))
            fallback_reason = (
                "no host_info cached (operator skipped 🚀 Max BW) — "
                "linear CPU ordering, no NUMA pin. Cross-NUMA RAM "
                "access may cap aggregate BW."
            )
        # v0.5.158: surface the fallback to the operator so a slow
        # run doesn't get misdiagnosed as a perftest / wire issue.
        if fallback_reason:
            self._stats_view.append(
                f"[workers] ⚠ {fallback_reason}"
            )
        # v0.5.157: clamp every CPU ID to the host's actual
        # cpu_count-1 ceiling. When the host_info cache is empty
        # OR pick_workers_for_hca falls back to linear range, we
        # can otherwise pin to a CPU that doesn't exist (e.g.
        # operator typed 32 workers on a 16-core host) — taskset
        # then errors out with "Invalid argument" and the worker
        # fails to start. Clamping here makes that case spawn N
        # workers on the same top CPU (still useful — perftest
        # picks distinct QPs) rather than failing silently.
        cpu_count = None
        if info:
            cpu_count = info.get("cpu_count")
        if isinstance(cpu_count, int) and cpu_count > 0:
            cpus = [min(int(c), cpu_count - 1) for c in cpus]

        from urllib.parse import urlparse as _urlparse
        # Worker 0 already running (started via the main flow).
        # Spawn 1..N-1 here.
        for worker_idx in range(1, worker_count):
            cpu = cpus[worker_idx] if worker_idx < len(cpus) else worker_idx
            worker_handshake = f"{self._handshake_id}-w{worker_idx}"
            base_port = int(opts.get("base_port") or 0)
            extra_port = base_port + worker_idx if base_port else None

            srv_body = {
                "role": "server",
                "test": test_id,
                "device": server_dev,
                "ib_port": int(self._server_port_spin.value()),
                "handshake_id": worker_handshake,
                "note": f"Blast RDMA Flow / {test_id} / worker {worker_idx}",
                "cpu_pin": cpu,
                "numa_pin": numa_pin,
                **opts,
            }
            if extra_port:
                srv_body["listen_port"] = extra_port

            def _on_extra_server_started(
                data, err,
                _w=worker_idx, _cpu=cpu,
                _client_dev=client_dev, _test_id=test_id,
                _hand=worker_handshake, _opts=opts,
            ):
                if err or not data or data.get("status") != "started":
                    self._stats_view.append(
                        f"[worker {_w}/server] start failed: "
                        f"{err or (data and data.get('error'))}"
                    )
                    return
                srv_jid = data.get("job_id")
                cli_body = {
                    "role": "client",
                    "test": _test_id,
                    "device": _client_dev,
                    "ib_port": int(self._client_port_spin.value()),
                    "handshake_id": _hand,
                    "note": f"Blast RDMA Flow / {_test_id} / worker {_w}",
                    "cpu_pin": _cpu,
                    "numa_pin": numa_pin,
                    "peer_addr": _urlparse(self._server_tg_url).hostname,
                    **_opts,
                }

                def _on_extra_client_started(
                    cdata, cerr, _w2=_w, _srv_jid=srv_jid,
                    _cpu2=_cpu,
                ):
                    if cerr or not cdata or cdata.get("status") != "started":
                        self._stats_view.append(
                            f"[worker {_w2}/client] start failed: "
                            f"{cerr or (cdata and cdata.get('error'))}"
                        )
                        return
                    cli_jid = cdata.get("job_id")
                    self._extra_workers.append({
                        "worker_idx": _w2,
                        "cpu_pin": _cpu2,
                        "server": _srv_jid,
                        "client": cli_jid,
                        "server_finished": False,
                        "client_finished": False,
                    })
                    self._stats_view.append(
                        f"[worker {_w2}] both halves running "
                        f"(cpu={_cpu2}, server_job={_srv_jid[:8]} "
                        f"client_job={cli_jid[:8]})"
                    )

                _post_async(
                    self,
                    f"{self._client_tg_url}/api/rdma/perftest/start",
                    cli_body, _on_extra_client_started,
                )

            _post_async(
                self,
                f"{self._server_tg_url}/api/rdma/perftest/start",
                srv_body, _on_extra_server_started,
            )

    # ───────── v0.5.151 QP-verify help

    def _show_qp_verify_help(self) -> None:
        """Pop a help panel with concrete `rdma resource show qp`
        commands the operator can run to verify that perftest is
        actually using qp_count QPs.

        Pre-fills commands with the currently-selected (host, HCA,
        IB port, qp_count). Falls back to `<hca>` / `<server>`
        placeholders when nothing is picked yet — the panel is
        still informative as a reference doc.
        """
        from urllib.parse import urlparse

        def _host(url: str) -> str:
            try:
                return urlparse(url).hostname or url
            except Exception:
                return url

        srv_host = _host(self._server_tg_url)
        cli_host = _host(self._client_tg_url)
        srv_dev = self._server_device_combo.currentData() or "<server-hca>"
        cli_dev = self._client_device_combo.currentData() or "<client-hca>"
        srv_port = int(self._server_port_spin.value())
        cli_port = int(self._client_port_spin.value())
        qp_n = int(self._qp_count_spin.value())
        same_host = srv_host == cli_host

        dlg = _QpVerifyHelpDialog(
            srv_host=srv_host, cli_host=cli_host,
            srv_dev=srv_dev, cli_dev=cli_dev,
            srv_port=srv_port, cli_port=cli_port,
            qp_n=qp_n, same_host=same_host,
            parent=self,
        )
        dlg.exec_()

    def _pick_other_hca_for_client(self) -> None:
        """v0.5.149: same-host two-HCA shortcut. Picks the device
        AFTER the server-side selection on the client combo. Wraps
        to index 0 if the server picked the last device.

        Skips placeholder entries (userData=None) so the operator
        doesn't accidentally end up with `(probing…)` or
        `(no HCAs)` selected.

        If the client combo only has one real entry, no-op — the
        same-host two-HCA test is impossible with a single HCA;
        the operator would just see same-HCA loopback.
        """
        srv_dev = self._server_device_combo.currentData()
        if not srv_dev:
            return
        # Find the server's index on the client combo.
        srv_idx = self._client_device_combo.findData(srv_dev)
        if srv_idx < 0:
            # Server-side device not present on client (race or
            # asymmetric probe response). Fall back to mirror — at
            # least the operator gets a valid same-HCA loopback.
            self._mirror_server_to_client()
            return
        # Count real devices (skipping placeholders).
        real_indices = [
            i for i in range(self._client_device_combo.count())
            if self._client_device_combo.itemData(i) is not None
        ]
        if len(real_indices) < 2:
            # Only one real HCA — two-HCA test is meaningless.
            return
        # Pick the next real device after srv_idx (wrap).
        pos_in_reals = real_indices.index(srv_idx) if srv_idx in real_indices else 0
        target = real_indices[(pos_in_reals + 1) % len(real_indices)]
        self._client_device_combo.setCurrentIndex(target)
        # IB port: keep client port at its current value. Sibling
        # ports of a dual-port NIC are exposed as separate HCAs
        # with their own ib_port=1 — the spinbox stays at 1.

    def _mirror_server_to_client(self) -> None:
        """v0.5.147 Loopback button: copy the server-side device
        selection + IB port onto the client side. The canonical
        RDMA smoke test runs both perftest processes against the
        same HCA — verbs bounces internally, no fabric required.

        If the server combo hasn't probed yet (`(probing…)` /
        userData is None), no-op silently — the user can click
        again once devices show up.
        """
        srv_dev = self._server_device_combo.currentData()
        if not srv_dev:
            return
        idx = self._client_device_combo.findData(srv_dev)
        if idx < 0:
            # Client side hasn't received this device yet (still
            # probing). Add a placeholder so we don't drop the
            # selection — _on_devices_resp will pick the right
            # index on its next pass.
            self._client_device_combo.addItem(srv_dev, userData=srv_dev)
            idx = self._client_device_combo.findData(srv_dev)
        self._client_device_combo.setCurrentIndex(idx)
        self._client_port_spin.setValue(self._server_port_spin.value())

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

        # v0.5.152 Option C-B: auto-detect the same-subnet routing
        # trap BEFORE firing perftest. The trap is a same-host
        # configuration where the kernel routes intra-host traffic
        # via lo and the QP can't reach RTR. We hit this in the
        # operator's srv06 testing twice — once because the pre-
        # flight test IPs were cleaned up between runs.
        #
        # Skip the probe if either:
        #   * we already have applied test IPs in this dialog
        #     session (the operator went through Pre-flight already),
        #   * server and client TGs are different hosts (same-host
        #     trap is impossible),
        #   * the operator just unchecked the auto-detect option
        #     (configurable; see _preflight_autocheck_default).
        same_host = (self._server_tg_url == self._client_tg_url)
        already_applied = bool(self._preflight_state_ids)
        if same_host and not already_applied:
            self._set_status_ok(
                "Probing endpoints for same-subnet trap…"
            )
            self._start_btn.setEnabled(False)
            self._auto_probe_then_start(
                server_dev=server_dev,
                client_dev=client_dev,
            )
            return
        # No same-host trap risk OR operator already fixed it via
        # Pre-flight — go straight to the perftest start.
        self._proceed_with_start(server_dev, client_dev)

    def _proceed_with_start(self, server_dev: str, client_dev: str) -> None:
        """Actually fire the perftest start sequence. Extracted from
        the original `_on_start_clicked` body so the v0.5.152 auto-
        trap-detect can defer this step behind an optional confirm
        dialog."""
        test_id = self._test_combo.currentData()
        self._handshake_id = str(uuid.uuid4())
        opts = self._common_opts()
        # v0.5.155: stash so `_on_client_started` can fan out extras
        # for parallel-worker BW scaling.
        self._last_start_params = {
            "test_id": test_id,
            "server_dev": server_dev,
            "client_dev": client_dev,
            "opts": dict(opts),
        }
        # Reset extra-workers tracking for this run.
        self._extra_workers = []
        # v0.5.157: also reset the TOTAL-emit guard and worker-0
        # final stats so each new Start gets a clean slate. Without
        # these, a 2nd Start in the same dialog session would skip
        # the TOTAL line (idempotency guard from the prior run) and
        # carry stale worker-0 BW/MsgRate into the new sum.
        self._total_emitted = False
        self._client_bw = None
        self._client_msgrate = None
        # v0.5.159 REGRESSION FIX: v0.5.157 cleared the host-info
        # cache here on every Start. That was wrong — host_info is
        # a HOST-LEVEL fact (NUMA topology + full hca_numa map for
        # every HCA on the box). Clearing it meant the operator's
        # 🚀 Max BW click was silently discarded by the next Start,
        # _start_extra_workers fell back to list(range(N)) with no
        # numa_pin, and the v0.5.158 "(operator skipped 🚀 Max BW)"
        # warning fired even when they hadn't. Leave the cache
        # alone — if the operator wants fresh info they click 🚀
        # again.

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
        # v0.5.155: fan out workers 1..N-1 for parallel BW scaling.
        # No-op when Parallel workers = 1.
        try:
            wc = int(self._parallel_workers_spin.value())
        except (AttributeError, RuntimeError):
            wc = 1
        if wc > 1 and getattr(self, "_last_start_params", None):
            p = self._last_start_params
            self._stats_view.append(
                f"[worker 0] both halves running "
                f"(server_job={self._server_job_id[:8]} "
                f"client_job={self._client_job_id[:8]}). "
                f"Spawning {wc - 1} extra worker(s)…"
            )
            self._start_extra_workers(
                test_id=p["test_id"],
                server_dev=p["server_dev"],
                client_dev=p["client_dev"],
                opts=p["opts"],
            )

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

    def _poll_extras(self) -> None:
        """v0.5.155: poll every extra worker's (server, client)
        pair. On completion we let _on_job_resp track per-worker
        finished state via the worker dict's flags."""
        for w in list(self._extra_workers):
            if w.get("server"):
                _get_async(
                    self,
                    f"{self._server_tg_url}/api/rdma/perftest/job/{w['server']}",
                    lambda data, err, _w=w:
                        self._on_extra_job_resp("server", _w, data, err),
                )
            if w.get("client"):
                _get_async(
                    self,
                    f"{self._client_tg_url}/api/rdma/perftest/job/{w['client']}",
                    lambda data, err, _w=w:
                        self._on_extra_job_resp("client", _w, data, err),
                )

    def _on_extra_job_resp(
        self, side: str, w: Dict[str, Any],
        data: Optional[dict], err: str,
    ) -> None:
        """Per-extra-worker completion tracking. Renders a short
        line when each half finishes; sums BW into the TOTAL once
        every worker is done.

        v0.5.159 CRITICAL FIX: the response from
        /api/rdma/perftest/job/<id> is `{"job": {...}}`. Pre-fix we
        read `data.get("finished_at")` directly — which was always
        None, so extras NEVER transitioned to finished, _maybe_
        emit_total never fired the TOTAL line, and per-worker done
        rows were silently swallowed. `_on_job_resp` for worker 0
        unwraps `data["job"]` correctly; mirror that here.
        """
        if err or not data:
            return
        job = data.get("job") or {}
        if job.get("finished_at") is not None:
            done_key = f"{side}_finished"
            if not w.get(done_key):
                w[done_key] = True
                wi = w.get("worker_idx", "?")
                rc = job.get("returncode")
                bw = job.get("final_bw_avg_gbps")
                mr = job.get("final_msg_rate_mpps")
                self._stats_view.append(
                    f"[worker {wi}/{side}] done (rc={rc})  "
                    f"BW avg={bw} Gbps  MsgRate={mr} Mpps"
                )
                # Stash final stats for the TOTAL summary.
                w.setdefault(f"{side}_bw", bw)
                w.setdefault(f"{side}_msgrate", mr)
                w[f"{side}_rc"] = rc
                self._maybe_emit_total()

    def _maybe_emit_total(self) -> None:
        """If every (worker 0 + extras) half is done, append a
        TOTAL row summing BW + MsgRate. Idempotent — guarded by
        `_total_emitted` so re-polls don't append duplicates."""
        if getattr(self, "_total_emitted", False):
            return
        # Worker 0 done?
        if not (self._server_finished and self._client_finished):
            return
        # Every extra worker done on both halves?
        for w in self._extra_workers:
            if not (w.get("server_finished") and w.get("client_finished")):
                return
        # Compute the TOTAL. Only sum the CLIENT-side BW (the
        # server-side BW row from perftest is usually identical to
        # the client's; double-counting would mislead).
        # v0.5.157: include worker 0's client BW + MsgRate in the
        # sum so operators see one grand-total line instead of
        # having to add the "[client] done" row to a separate
        # "extras-only" row manually.
        total_bw = 0.0
        total_mr = 0.0
        n = 0
        w0_bw = getattr(self, "_client_bw", None)
        w0_mr = getattr(self, "_client_msgrate", None)
        if isinstance(w0_bw, (int, float)):
            total_bw += float(w0_bw)
            n += 1
        if isinstance(w0_mr, (int, float)):
            total_mr += float(w0_mr)
        for w in self._extra_workers:
            bw = w.get("client_bw")
            mr = w.get("client_msgrate")
            if isinstance(bw, (int, float)):
                total_bw += float(bw)
                n += 1
            if isinstance(mr, (int, float)):
                total_mr += float(mr)
        if n == 0:
            return
        self._stats_view.append(
            f"\n[TOTAL across {n} worker(s)] "
            f"BW={total_bw:.2f} Gbps  "
            f"MsgRate={total_mr:.4f} Mpps"
        )
        self._total_emitted = True

    def _poll_jobs(self) -> None:
        # v0.5.155: poll extras alongside worker 0.
        self._poll_extras()
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
                # v0.5.157: capture worker-0 client final stats so
                # the TOTAL summary can include this worker, not
                # just extras 1..N-1. The extras pipeline already
                # stores client_bw / client_msgrate on each extra-
                # worker dict; worker 0 lives on the parent dialog
                # state, mirror the same field names.
                if job.get("final_bw_avg_gbps") is not None:
                    self._client_bw = job.get("final_bw_avg_gbps")
                if job.get("final_msg_rate_mpps") is not None:
                    self._client_msgrate = job.get("final_msg_rate_mpps")

        s_done = (self._server_job_id is None) or self._server_finished
        c_done = (self._client_job_id is None) or self._client_finished
        if s_done and c_done:
            self._on_both_finished()
        # v0.5.157: worker 0 done might be the last thing we were
        # waiting for to emit the TOTAL line. _maybe_emit_total is
        # idempotent — the early-return on _total_emitted protects
        # against double-emission.
        self._maybe_emit_total()

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
            # v0.5.149: drop the 120-char client clip. v0.5.146's
            # server-side `_format_rc_error` already filters the
            # config-dump banner and clips its inner tail to ~400
            # chars — clipping AGAIN here threw away whatever real
            # diagnostic survived the filter. The QTextEdit already
            # wraps + scrolls, so a multi-line error is rendered
            # cleanly without the operator having to dig into the
            # server log.
            chunk += f"\n  err={job.get('error')}"
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
        # v0.5.153: Stop also fires preflight cleanup. Previously
        # only closeEvent did, so operator hitting Stop and leaving
        # the dialog open kept stale test IPs around indefinitely.
        # Cleanup is idempotent; re-cleaning a state_id is a no-op.
        self._cleanup_preflight_state_ids()
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
        # v0.5.155: stop every extra worker.
        for w in self._extra_workers:
            for url, jid in (
                (self._server_tg_url, w.get("server")),
                (self._client_tg_url, w.get("client")),
            ):
                if jid:
                    _post_async(
                        self,
                        f"{url}/api/rdma/perftest/stop",
                        {"job_id": jid, "forget_pair": True},
                        lambda *_: None,
                    )
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._set_status_warn("Stop requested. All workers will tear down.")
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
        # v0.5.150: clean up any pre-flight test IPs the operator
        # applied during this dialog's lifetime. Fire-and-forget
        # — operator already accepted the dialog close, no point
        # blocking on the cleanup result.
        self._cleanup_preflight_state_ids()
        # v0.5.157: drop all multi-worker state so a reopen via
        # the menu lands on a clean dialog. Without this, a 2nd
        # session would carry stale worker dicts in _extra_workers
        # (polled forever against a closed perftest job_id) and the
        # TOTAL-emit guard would suppress the next Start's summary.
        # v0.5.159: keep _host_info_cache across close — but
        # that's a non-issue here since closeEvent destroys the
        # widget anyway. The other resets ARE still needed for
        # in-session safety; we just don't need to special-case
        # _host_info_cache because Qt's deletion handles it.
        self._extra_workers = []
        self._total_emitted = False
        self._client_bw = None
        self._client_msgrate = None
        event.accept()

    def _cleanup_preflight_state_ids(self) -> None:
        """POST cleanup for every applied state_id. Idempotent —
        re-cleaning a state_id that's already gone is a no-op
        on the server side."""
        sids = list(self._preflight_state_ids)
        if not sids:
            return
        self._preflight_state_ids.clear()
        for sid in sids:
            try:
                _post_async(
                    self,
                    f"{self._server_tg_url}/api/rdma/test_ifaces/cleanup",
                    {"state_id": sid},
                    lambda *_a: None,
                )
            except Exception:
                pass


# ─────────────────────────────────── v0.5.151 QP-verify help dialog ──


class _QpVerifyHelpDialog(QDialog):
    """Modal showing how to verify perftest is using the operator's
    requested QP count. Substitutes the current dialog state
    (host, HCA, IB port, qp_count) into the command templates so
    copy-paste is one click.

    Pure reference doc — no async calls, no side effects. Closes
    the SSH-vs-curl gap left by v0.5.150: operators have a
    button-driven test runner AND a button-driven verification
    path.
    """

    def __init__(
        self,
        *,
        srv_host: str, cli_host: str,
        srv_dev: str, cli_dev: str,
        srv_port: int, cli_port: int,
        qp_n: int, same_host: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Verify Active QPs")
        self.setMinimumSize(720, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "<span style='font-size:13px; font-weight:600; color:#0f172a;'>"
            "Verify Active QPs</span>"
            "&nbsp;&nbsp;"
            "<span style='color:#64748b; font-size:11px;'>"
            f"qp_count = <b>{qp_n}</b>. perftest doesn't print its "
            "QP table by default — these commands let you see it "
            "from the outside while the test is running."
            "</span>"
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Cache the SSH/raw command pairs so the buttons can copy
        # exactly what's rendered.
        self._cmd_pairs: list = []

        def _add_section(
            title: str, blurb: str, commands: list
        ) -> None:
            box = QGroupBox(title)
            v = QVBoxLayout(box)
            v.setContentsMargins(8, 4, 8, 6)
            v.setSpacing(4)
            blurb_lbl = QLabel(
                f"<span style='color:#475569; font-size:11px;'>"
                f"{blurb}</span>"
            )
            blurb_lbl.setWordWrap(True)
            v.addWidget(blurb_lbl)
            for cmd in commands:
                row = QHBoxLayout()
                row.setSpacing(6)
                edit = QLineEdit(cmd)
                edit.setReadOnly(True)
                edit.setFont(QFont("Menlo"))
                edit.setStyleSheet(
                    "background:#f1f5f9; border:1px solid #cbd5e1; "
                    "padding:4px; border-radius:3px;"
                )
                row.addWidget(edit, 1)
                btn = QPushButton("Copy")
                btn.setFixedWidth(54)
                btn.clicked.connect(
                    lambda _=False, e=edit: self._copy_to_clipboard(e.text()))
                row.addWidget(btn)
                v.addLayout(row)
                self._cmd_pairs.append(cmd)
            root.addWidget(box)

        # Build the ssh-wrapped form for each command. If the
        # operator is on the same host (rare but possible), still
        # show ssh — they can drop the prefix mentally.
        def _ssh(host: str, cmd: str) -> str:
            return f"ssh {host} '{cmd}'"

        # ── Section 1: count.
        _add_section(
            "1. Count active QPs",
            (
                f"Should be ≈ <b>{qp_n}</b> on each side (perftest "
                f"adds 1 control QP during setup). Run WHILE the "
                f"test is running."
            ),
            [
                _ssh(srv_host,
                     f"rdma resource show qp link {srv_dev}/{srv_port} | wc -l"),
            ] + ([
                _ssh(cli_host,
                     f"rdma resource show qp link {cli_dev}/{cli_port} | wc -l"),
            ] if not same_host or srv_dev != cli_dev else []),
        )

        # ── Section 2: detail.
        _add_section(
            "2. QP detail (state, PD, owning process)",
            (
                "Each row is one QP. Look for <code>state RTS</code> "
                "(Ready To Send) on every QP — anything stuck in "
                "<code>INIT</code> or <code>RTR</code> is failing "
                "to transition. <code>pid</code> should be perftest."
            ),
            [
                _ssh(srv_host,
                     f"rdma resource show qp link {srv_dev}/{srv_port} -d"),
            ] + ([
                _ssh(cli_host,
                     f"rdma resource show qp link {cli_dev}/{cli_port} -d"),
            ] if not same_host or srv_dev != cli_dev else []),
        )

        # ── Section 3: JSON (machine-parseable).
        _add_section(
            "3. JSON (for scripts)",
            (
                "Same info as section 2 but JSON. Useful when "
                "scripting verification or piping into <code>jq</code>."
            ),
            [
                _ssh(srv_host,
                     f"rdma resource show qp link {srv_dev}/{srv_port} -jp"),
            ],
        )

        # ── Section 4: perftest verbose.
        _add_section(
            "4. perftest verbose mode (-v)",
            (
                "Alternative: re-run with <code>-v</code> and "
                "perftest itself will print every QP's QPN as it "
                "creates them. Pass via the API escape hatch:"
            ),
            [
                (
                    f"curl -X POST http://{srv_host}:5050"
                    f"/api/rdma/perftest/start "
                    f"-H 'Content-Type: application/json' "
                    f"-d '{{\"role\":\"server\",\"device\":\"{srv_dev}\","
                    f"\"test\":\"send_bw\",\"qp_count\":{qp_n},"
                    f"\"perf_extra\":[\"-v\"]}}'"
                ),
                (
                    f"# Then fetch stdout_tail from the job "
                    f"endpoint:\n"
                    f"curl http://{srv_host}:5050/api/rdma/perftest/job/<job_id>"
                ),
            ],
        )

        # ── Section 5: cross-check via PHY counters.
        _add_section(
            "5. Wire-level cross-check (ethtool PHY counters)",
            (
                "Independent confirmation that the QPs are doing "
                "work. Sample twice, 5s apart; delta / 5 = pps. "
                "Should match the perftest result row's MsgRate."
            ),
            [
                _ssh(srv_host,
                     f"ethtool -S $(ls /sys/class/infiniband/{srv_dev}"
                     f"/device/net | head -1) "
                     f"| grep -E 'tx_packets_phy|tx_bytes_phy'"),
            ],
        )

        # Footer: "copy all" + close.
        btn_row = QHBoxLayout()
        copy_all = QPushButton("Copy all commands")
        copy_all.setToolTip(
            "Copy every command above to the clipboard, one per "
            "line. Useful for pasting into a single SSH session."
        )
        copy_all.clicked.connect(lambda: self._copy_to_clipboard(
            "\n".join(self._cmd_pairs)))
        btn_row.addWidget(copy_all)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        try:
            from PyQt5.QtWidgets import QApplication
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(text)
        except Exception:
            pass


# ─────────────────────────────────── v0.5.152 same-subnet trap confirm ──


class _StartBlockerConfirmDialog(QDialog):
    """v0.5.153: generalized from v0.5.152's
    `_SameSubnetTrapConfirmDialog`. Pops on Start when the auto-
    probe detects any of four blockers:

      * `probe_failed` — one of the probes errored (timeout, server
        down, etc). Only Cancel / Continue offered (no auto-fix).
      * `down_port`    — at least one port is not ACTIVE. Only
        Cancel / Continue (auto-fix can't bring a link up).
      * `missing_ip`   — at least one iface has no IPv4. Apply auto-
        configures test IPs (auto-fixable).
      * `same_subnet`  — same-subnet routing trap. Apply auto-
        reconfigures with non-conflicting CIDRs (auto-fixable).

    Operator's choice via `choice()` after `exec_()`: one of
    "apply" / "continue" / "cancel". `apply` is only offered for
    auto-fixable reasons.
    """

    _TITLES = {
        "probe_failed": "Endpoint probe failed",
        "down_port":    "Port is not ACTIVE",
        "missing_ip":   "Missing IPv4 address",
        "same_subnet":  "Same-subnet routing trap",
    }

    _AUTOFIXABLE = {"missing_ip", "same_subnet"}

    def __init__(
        self,
        *,
        reason: str,
        detail: str,
        srv_iface: str,
        cli_iface: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self._TITLES.get(reason, "Start blocker"))
        self.setMinimumWidth(560)
        self._choice = "cancel"
        self._reason = reason

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel(
            f"<span style='font-size:13px; font-weight:600; "
            f"color:#b45309;'>"
            f"⚠️  {self._TITLES.get(reason, 'Start blocker')}</span>"
        )
        root.addWidget(title)

        body_text = self._body_for(
            reason, detail, srv_iface, cli_iface)
        body = QLabel(body_text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        root.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)

        btn_row.addStretch(1)

        continue_btn = QPushButton("Continue anyway")
        continue_btn.setToolTip(
            "Skip the auto-fix and run perftest as configured. "
            "It will almost certainly fail at QP→RTR, but useful "
            "if you have an out-of-band fix or want to capture "
            "the failure for debugging."
        )
        continue_btn.clicked.connect(self._on_continue)
        btn_row.addWidget(continue_btn)

        if reason in self._AUTOFIXABLE:
            apply_btn = QPushButton("Apply && Start")
            apply_btn.setDefault(True)
            apply_btn.setStyleSheet(
                "QPushButton { background-color: #0ea5e9; "
                "color: white; padding: 4px 12px; "
                "border-radius: 3px; font-weight: 600; }"
            )
            apply_btn.clicked.connect(self._on_apply)
            btn_row.addWidget(apply_btn)
        else:
            # Non-fixable — surface an Open Pre-flight affordance
            # so the operator can investigate manually. (We don't
            # auto-open it from here — keep the choice in the
            # operator's hands.)
            open_btn = QPushButton("Open Pre-flight…")
            open_btn.setToolTip(
                "Close this dialog and inspect the probe details "
                "(port state, GIDs, IPs) via the Pre-flight panel."
            )
            open_btn.clicked.connect(self._on_open_preflight)
            btn_row.addWidget(open_btn)

        root.addLayout(btn_row)

    @staticmethod
    def _body_for(
        reason: str, detail: str,
        srv_iface: str, cli_iface: str,
    ) -> str:
        if reason == "probe_failed":
            return (
                f"<p>One of the endpoint probes failed:</p>"
                f"<pre>{detail}</pre>"
                f"<p>perftest needs a working probe response to "
                f"validate the setup. Common causes: TG server "
                f"down, network flake, RDMA stack not installed. "
                f"Open Pre-flight to inspect both endpoints, or "
                f"Continue anyway to fire perftest blind.</p>"
            )
        if reason == "down_port":
            return (
                f"<p>{detail}.</p>"
                f"<p>perftest can't bring a QP to RTR through a "
                f"port without link carrier. This isn't auto-"
                f"fixable from netgen — bring the link up "
                f"(<code>ip link set &lt;iface&gt; up</code>, "
                f"check cable / SFP / switch port) and try "
                f"again.</p>"
            )
        if reason == "missing_ip":
            return (
                f"<p>{detail}.</p>"
                f"<p>perftest needs an IPv4 address on the kernel "
                f"iface to form a RoCEv2 GID. Without one, the QP "
                f"can't resolve the peer's GID and fails at RTR.</p>"
                f"<p><b>Apply &amp; Start</b> adds a temporary "
                f"test IPv4 (<code>&lt;iface&gt;:ng</code> labeled, "
                f"runtime-only) and starts perftest. Cleanup runs "
                f"when this dialog closes.</p>"
            )
        if reason == "same_subnet":
            return (
                f"<p>Both kernel ifaces share IPv4 subnet "
                f"<code>{detail}</code>:</p>"
                f"<ul>"
                f"<li><code>{srv_iface}</code> (server)</li>"
                f"<li><code>{cli_iface}</code> (client)</li>"
                f"</ul>"
                f"<p>Linux routes traffic between them via "
                f"<code>lo</code> instead of out the wire → "
                f"perftest's QP can't reach RTR.</p>"
                f"<p><b>Apply &amp; Start</b> adds test IPs on "
                f"different subnets "
                f"(<code>10.42.0.1/24</code> + "
                f"<code>10.43.0.1/24</code>), validates them "
                f"server-side, then starts perftest. Cleanup runs "
                f"on dialog close.</p>"
            )
        return f"<p>{detail}</p>"

    def _on_apply(self) -> None:
        self._choice = "apply"
        self.accept()

    def _on_continue(self) -> None:
        self._choice = "continue"
        self.accept()

    def _on_cancel(self) -> None:
        self._choice = "cancel"
        self.reject()

    def _on_open_preflight(self) -> None:
        """For non-auto-fixable reasons. Caller can detect this
        via `choice() == 'open_preflight'` and route the operator
        into the Pre-flight dialog."""
        self._choice = "open_preflight"
        self.accept()

    def choice(self) -> str:
        return self._choice
