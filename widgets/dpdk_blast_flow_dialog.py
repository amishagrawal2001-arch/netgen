"""'Blast a Flow' dialog — v0.3.11 Phase 3.

Composition of:
  1. The orchestrator dialog (Phase 2) that makes DPDK ready.
  2. A sample-stream POST that fires a 1500-byte UDP flow at line
     rate on the freshly-bound NIC the instant binding succeeds.

End-state: from operator click to packets flying is ONE click +
one NIC pick.

The sample stream is ephemeral — it lives on the server side
(POST /api/traffic/start) but doesn't enter the client's local
``self.streams`` dict. That means it won't appear in the Streams
tab; this is deliberate for the v1 'smoke test / demo button'
shape. Stopping requires either the in-dialog Stop button (the
common path) or a server-side restart. The dialog disclaims this
in the post-start summary so the operator knows what they're
looking at.

Future direction (v0.3.12+): wire the started stream into the
local streams dict so it shows up in the Streams tab and stays
controllable from there.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from widgets.dpdk_make_ready_dialog import MakeDpdkReadyDialog


logger = logging.getLogger(__name__)


def _api_worker():
    """Late import — same lazy pattern as the orchestrator dialog
    to avoid circular imports."""
    from traffic_client.dpdk_menu_actions import _DpdkApiWorker
    return _DpdkApiWorker


# ─────────────────────────────────── defaults

# v0.3.11 line-rate tuning: defaults chosen so a one-click blast
# on a freshly-bound NIC ACTUALLY HITS the wire's line rate, not
# just "blasts packets." See _LINE_RATE_RATIONALE block below.
DEFAULT_FRAME_SIZE = 1500         # Ethernet MTU — see rationale
DEFAULT_SRC_IP = "10.0.0.1"
DEFAULT_DST_IP = "10.0.0.2"
DEFAULT_DST_UDP_PORT = 9999
DEFAULT_RATE_PPS = 0              # 0 = line rate (no DPDK cap)

# v0.3.11 bug fix: tx_worker requires src_mac, dst_mac, src_ip,
# dst_ip — if any are missing it logs "missing required fields"
# (utils/dpdk_tx_worker.py:230) and exits with code 2. The launch
# path doesn't surface the exit code, so the stream registers in
# stream_tracker (engine reports "DPDK") but tx_count stays at 0.
# User reported: "Blast a Flow shows blasting but 0 packets".
#
# Line-rate-tuned defaults:
#   * src_mac — locally-administered unicast (02:xx prefix; no
#     vendor-OUI collision).
#   * dst_mac — locally-administered UNICAST (02:00:00:00:00:02).
#     Was broadcast ff:ff:ff:ff:ff:ff for "no ARP needed"; switched
#     to unicast because broadcast frames trigger special handling
#     on many drivers (software filter, rate cap) and rarely hit
#     actual wire line rate. Unicast frames go straight through
#     the NIC's TX path at full speed. No ARP needed either way —
#     the sender hard-codes the dst MAC.
DEFAULT_SRC_MAC = "02:00:00:00:00:01"
DEFAULT_DST_MAC = "02:00:00:00:00:02"

# ───── _LINE_RATE_RATIONALE ─────
# Why DEFAULT_FRAME_SIZE = 1500 (changed from 64):
#
#   Frame  |  pps for 100G  | Single-core tx_worker | Hits 100G?
#   -------+----------------+-----------------------+------------
#     64 B |    148.8 Mpps  |     ~46 Mpps          |  NO (~23 G)
#    128 B |     84.4 Mpps  |     ~46 Mpps          |  NO (~47 G)
#    512 B |     23.5 Mpps  |     ~46 Mpps          | barely
#   1500 B |      8.2 Mpps  |     ~46 Mpps          |  YES (line)
#   9000 B |      1.4 Mpps  |     ~46 Mpps          |  YES (trivial)
#
# 64 B is the IETF RFC 2544 stress test (max pps) — useful when
# you specifically want to find out where the NIC tops out, but
# it's the WORST default for a one-click "did line rate work?"
# demo. The user's earlier blast showed 23 Gbps on a 100G NIC at
# 64 B (single-core tx_worker capped at ~46 Mpps); switching to
# 1500 B requires only 8.2 Mpps for full 100 Gbps line rate,
# which a single tx_worker thread hits comfortably. Operators
# who want the 64 B stress test override in the Sample Stream
# group — the frame size field accepts 60..9216.
# ────────────────────────────────


def _build_sample_stream(
    iface_key: str,
    *,
    frame_size: int = DEFAULT_FRAME_SIZE,
    src_ip: str = DEFAULT_SRC_IP,
    dst_ip: str = DEFAULT_DST_IP,
    dst_port: int = DEFAULT_DST_UDP_PORT,
    rate_pps: int = DEFAULT_RATE_PPS,
    src_mac: str = DEFAULT_SRC_MAC,
    dst_mac: str = DEFAULT_DST_MAC,
) -> dict:
    """Build the minimal stream dict that /api/traffic/start accepts.

    v0.3.11: top-level ``mac_source_address`` / ``mac_destination_
    address`` / ``src_ip`` / ``dst_ip`` are CRITICAL — tx_worker's
    ``_resolve_l2_l3_l4`` (utils/dpdk_tx_worker.py:925) reads from
    top-level OR ``protocol_data.{mac,ipv4,udp}``. It does NOT
    read ``protocol_selection`` (that's a GUI-only key). Pre-fix,
    this builder only populated protocol_selection — tx_worker saw
    all four required fields as missing and exited with code 2
    immediately after launch. Stream tracker still reported it as
    Running, so the dialog thought everything was fine.
    """
    stream_id = str(uuid.uuid4())
    return {
        "name": "blast-flow",
        "stream_name": "blast-flow",
        "stream_id": stream_id,
        "enabled": True,
        "status": "stopped",
        "engine": "dpdk",
        "rx_port": iface_key,  # loopback rx on same iface
        # ── tx_worker-readable fields (top level) ──
        # See _resolve_l2_l3_l4 in utils/dpdk_tx_worker.py:925.
        "mac_source_address": src_mac,
        "mac_destination_address": dst_mac,
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "udp_source_port": 1234,
        "udp_destination_port": dst_port,
        "vlan_id": None,
        "frame_size": frame_size,
        # protocol_selection — GUI cosmetic shape (stream-stats
        # display reads it). Keep in sync with top-level so what
        # the operator sees matches what tx_worker actually sends.
        "protocol_selection": {
            "name": "blast-flow",
            "enabled": True,
            "frame_type": "fixed",
            "fixed_size": frame_size,
            "L1": "Ethernet",
            "L2": "Ethernet",
            "L3": "IPv4",
            "L4": "UDP",
            "src_mac": src_mac,
            "dst_mac": dst_mac,
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "rate_pps": rate_pps,  # 0 = line rate
            "dpdk_enable": True,
        },
    }


# ─────────────────────────────────── dialog


class DpdkBlastFlowDialog(MakeDpdkReadyDialog):
    """Make DPDK ready + start a sample 1500 B UDP line-rate flow.

    Adds three things on top of the parent:
      * Title says "Blast a Flow".
      * After the parent emits ``finished_ready(iface)``, kick a
        POST to /api/traffic/start with a sample stream.
      * Once the stream is running, the dialog stays open showing
        a Stop button and the stream's tracking ID.
    """

    def __init__(self, server_url: str, **kw):
        super().__init__(server_url, **kw)
        self.setWindowTitle("Blast a DPDK Flow")
        # v0.3.11 modality flip: the parent (MakeDpdkReadyDialog)
        # sets ApplicationModal so its short setup flow blocks the
        # rest of the GUI. Blast a Flow REUSES that dialog but
        # also keeps running indefinitely while traffic blasts —
        # the operator legitimately wants to look at stream stats,
        # check the chassis table, or kick off something else in
        # the main window while the flow is in flight. Override
        # to NonModal so the main window stays interactive. The
        # flow continues even if focus moves away; closing the
        # dialog still issues the stop request via closeEvent.
        self.setWindowModality(Qt.NonModal)
        self._active_stream_id: Optional[str] = None
        self._active_iface: Optional[str] = None
        # v0.3.11 multi-dialog safety: the menu handler sets this
        # to a callable that returns the set of ifaces already
        # claimed by sibling Blast Flow dialogs. We use it in
        # _on_dpdk_ready to warn before starting a second
        # tx_worker on the same NIC (DPDK PMD lock contention or
        # silent throughput halving). Default no-op so direct
        # construction (tests, standalone use) still works.
        self._sibling_iface_provider = lambda: set()
        # v0.3.11: live tx_count / tx_rate poll. The main window's
        # iface stats table reads kernel counters (psutil) — for
        # Mellanox bifurcated NICs, DPDK transmits via RDMA verbs
        # which BYPASS the kernel, so the table stays at 0 even
        # when tx_worker is hammering line rate. Operator then sees
        # "Flow blasting" in the dialog but 0 fps in the table and
        # concludes the flow isn't working. We poll
        # /api/streams/stats here so the dialog itself surfaces
        # the actual per-stream TX from stream_tracker (which
        # tx_worker reports into) — visible regardless of NIC type.
        self._stats_timer: Optional[QTimer] = None
        self._last_tx_count: int = 0
        # v0.3.11: tx-stall counter — number of consecutive 2s polls
        # where the stream was in the tracker but tx_count stayed
        # at 0. After 3 ticks (~6s) we warn the operator that
        # tx_worker likely crashed at launch (the MAC-fields bug
        # the same release fixed was the symptom; future field-set
        # regressions will surface the same way).
        self._tx_stall_ticks: int = 0

        # Tweak the header to reflect the additional step.
        # The parent's first widget is the intro QLabel.
        if self.layout().count() > 0:
            first_item = self.layout().itemAt(0)
            first_label = first_item.widget() if first_item else None
            if isinstance(first_label, QLabel):
                first_label.setText(
                    "<b>Blast a DPDK Flow</b><br/>"
                    "<span style='color:#475569;'>One-click smoke test. "
                    "Makes DPDK ready, then starts a 1500-byte UDP "
                    "flow at line rate on the NIC you bind (1500 B is "
                    "the Ethernet MTU — sized so a single tx_worker "
                    "core hits actual wire speed on 10/25/100 G NICs; "
                    "drop frame size to 64 for a max-pps stress test "
                    "instead). The flow runs "
                    "on the server until you click Stop or close this "
                    "dialog. The flow is NOT visible in the Streams "
                    "tab — it's an ephemeral demo stream.</span>"
                )

        # Pre-bind config group — sample-stream parameters the
        # operator can tune before clicking Run. Defaults are sane;
        # most users won't touch them.
        cfg = QGroupBox("Sample Stream")
        # v0.3.11 layout fix: switched from a stack of QHBoxLayout
        # rows (which let long QLineEdit text bleed visually into
        # the next column's label because we never set field
        # borders) to a 4-column grid. Columns: label | field |
        # label | field. The grid auto-aligns labels right + fields
        # left at consistent column widths, so "Dst IP:" can never
        # overlap "10.0.0.1" again. Also gives every QLineEdit a
        # visible border via inline stylesheet so the widget bounds
        # are obvious even in macOS Sonoma's flat theme.
        cv = QGridLayout(cfg)
        cv.setHorizontalSpacing(8)
        # v0.3.11 layout follow-up #2 — vSpacing bumped 6→10. The
        # previous 6 px got VISUALLY EATEN: QGridLayout sizes rows
        # by QLabel.sizeHint() (~19 px) and ignores QLineEdit's
        # setMinimumHeight(24) for row-height calc. The line edits
        # then OVERFLOWED their cells by 5 px each, leaving only
        # ~1 px between the IP row and the MAC row — the user's
        # screenshot showed two line edits effectively touching,
        # reading as "overlapping fields." Fixed by (a) bumping
        # vSpacing so even with overflow the rows stay readable,
        # and (b) setting an EXPLICIT minimum row height on the
        # line-edit rows below (the real fix — the grid then
        # allocates 24 px per row and the spacing isn't consumed).
        cv.setVerticalSpacing(10)
        # Column stretch: labels stay tight, fields share the
        # remaining width 1:1 so the dialog can resize cleanly.
        cv.setColumnStretch(0, 0)
        cv.setColumnStretch(1, 1)
        cv.setColumnStretch(2, 0)
        cv.setColumnStretch(3, 1)

        # v0.3.11 layout follow-up: DROPPED the custom QLineEdit
        # stylesheet (`border + border-radius + padding`) that the
        # previous fix added — on macOS Sonoma it interacted badly
        # with native rendering, causing the field text to look
        # struck-through with weird horizontal artifacts. Native
        # macOS QLineEdit styling already has a crisp visible
        # border, so the original "no visible borders" complaint
        # from earlier turns out to have been a misread of the
        # cramped-row layout, not a missing border.
        #
        # Min widths stay — without them, post-bind reflow could
        # still shrink fields below readable. Min heights are
        # explicit too so the line edit can never compress its
        # text vertically (which was what caused the strikethrough-
        # looking artifacts in the user's screenshot).
        _LINE_EDIT_MIN_HEIGHT = 24
        _IP_MIN_WIDTH = 130    # fits 'ddd.ddd.ddd.ddd/dd' with padding
        _MAC_MIN_WIDTH = 160   # fits 'ff:ff:ff:ff:ff:ff' with padding

        def _style_lineedit(le: QLineEdit, min_width: int) -> None:
            """Apply the standard sizing — no stylesheet, just
            min-width + fixed height. Native macOS / Qt rendering
            handles the border + selection color correctly.

            setFixedHeight (not setMinimumHeight) so QGridLayout
            sees an authoritative size for row-height calc.
            setMinimumHeight does NOT update sizeHint, and the
            grid was sizing rows by QLabel.sizeHint (~19 px),
            letting line edits overflow their cells by 5 px each
            and eating the vertical spacing between rows."""
            le.setMinimumWidth(min_width)
            le.setFixedHeight(_LINE_EDIT_MIN_HEIGHT)

        # Row 0: frame size + dst port
        cv.addWidget(QLabel("Frame size (B):"),  0, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._frame_size = QSpinBox()
        self._frame_size.setMinimum(60)
        self._frame_size.setMaximum(9216)
        self._frame_size.setValue(DEFAULT_FRAME_SIZE)
        self._frame_size.setToolTip(
            "Frame size in bytes (60–9216).\n"
            "• 1500 (default) — Ethernet MTU, sized for actual line "
            "rate. A single tx_worker core hits 100 Gbps at this "
            "size (8.2 Mpps).\n"
            "• 64 — RFC 2544 max-pps stress test. Caps at ~46 Mpps "
            "single-core (~23 Gbps on a 100 G NIC). Use only when "
            "you specifically want to find the pps ceiling.\n"
            "• 9000 — jumbo; requires matching MTU on the NIC and "
            "peer or the frame gets dropped."
        )
        cv.addWidget(self._frame_size, 0, 1)
        cv.addWidget(QLabel("Dst UDP port:"),   0, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._dst_port = QSpinBox()
        self._dst_port.setMinimum(1)
        self._dst_port.setMaximum(65535)
        self._dst_port.setValue(DEFAULT_DST_UDP_PORT)
        cv.addWidget(self._dst_port, 0, 3)

        # Row 1: src IP + dst IP
        cv.addWidget(QLabel("Src IP:"),  1, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._src_ip = QLineEdit(DEFAULT_SRC_IP)
        _style_lineedit(self._src_ip, _IP_MIN_WIDTH)
        cv.addWidget(self._src_ip, 1, 1)
        cv.addWidget(QLabel("Dst IP:"),  1, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._dst_ip = QLineEdit(DEFAULT_DST_IP)
        _style_lineedit(self._dst_ip, _IP_MIN_WIDTH)
        cv.addWidget(self._dst_ip, 1, 3)

        # Row 2: src MAC + dst MAC. tx_worker REQUIRES src+dst MAC
        # — missing them silently kills the flow at launch. Defaults
        # are locally-administered src + UNICAST dst (was broadcast
        # pre-v0.3.11 line-rate tuning — broadcast frames trigger
        # driver-side special handling and rarely hit line rate).
        # The hard-coded dst MAC means no ARP is needed regardless
        # — override with the peer's MAC for a targeted unicast
        # test where you want the peer to actually receive.
        cv.addWidget(QLabel("Src MAC:"), 2, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._src_mac = QLineEdit(DEFAULT_SRC_MAC)
        _style_lineedit(self._src_mac, _MAC_MIN_WIDTH)
        self._src_mac.setToolTip(
            "Source MAC for transmitted frames. Default is a "
            "locally-administered address (02:xx prefix) — no "
            "vendor-OUI collision."
        )
        cv.addWidget(self._src_mac, 2, 1)
        cv.addWidget(QLabel("Dst MAC:"), 2, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._dst_mac = QLineEdit(DEFAULT_DST_MAC)
        _style_lineedit(self._dst_mac, _MAC_MIN_WIDTH)
        self._dst_mac.setToolTip(
            "Destination MAC. Default is locally-administered "
            "UNICAST (02:00:00:00:00:02) — chosen for max line "
            "rate (broadcast frames trigger driver-side special "
            "handling that often caps wire speed). The sender "
            "hard-codes this MAC so no ARP is needed regardless. "
            "Override with the peer's MAC if you want the peer "
            "to actually receive the frames."
        )
        cv.addWidget(self._dst_mac, 2, 3)

        # CRITICAL: equalize ROW HEIGHTS for line-edit rows.
        # QGridLayout sizes each row by the TALLEST item in it, but
        # in practice with QLineEdit setMinimumHeight(24) and
        # QLabel default sizeHint (~19 px), Qt was taking the
        # label's smaller hint and the line edits OVERFLOWED their
        # cell — eating 5 px of the next row's vertical spacing.
        # Net visual in the user's screenshot: IP and MAC line
        # edits with only ~1 px between them, reading as one tall
        # box (the reported "overlapping fields").
        # Fix in three layers so any one of them is enough:
        #   1. setRowMinimumHeight on the line-edit rows
        #   2. Bump every label in those rows to the same min
        #      height so BOTH column-0 and column-1 widgets agree
        #      on the row height the grid should allocate.
        #   3. vSpacing bumped to 10 (above) for extra breathing
        #      room even if a renderer is slightly off.
        _ROW_MIN = _LINE_EDIT_MIN_HEIGHT + 4   # 28 px
        for _row in (1, 2):
            cv.setRowMinimumHeight(_row, _ROW_MIN)
            for _col in (0, 2):
                _item = cv.itemAtPosition(_row, _col)
                if _item is not None and _item.widget() is not None:
                    _item.widget().setMinimumHeight(_LINE_EDIT_MIN_HEIGHT)

        # Row 3: rate (spans both field columns since it's just one
        # widget; label spans label columns).
        cv.addWidget(QLabel("Rate (pps, 0 = line rate):"),
                     3, 0, 1, 2, Qt.AlignRight | Qt.AlignVCenter)
        self._rate_pps = QSpinBox()
        self._rate_pps.setMinimum(0)
        self._rate_pps.setMaximum(50_000_000)
        self._rate_pps.setSingleStep(100_000)
        self._rate_pps.setValue(DEFAULT_RATE_PPS)
        self._rate_pps.setSpecialValueText("0 (line rate)")
        cv.addWidget(self._rate_pps, 3, 2, 1, 2)

        # Insert the sample-stream group right before the action
        # list (which is at index N-3: list, detail, buttonbox).
        # The parent's layout order is:
        #   0 hdr, 1 server, 2 hugepage row, 3 list, 4 detail, 5 buttons
        # We slot Sample Stream after hugepage row (index 3, pushes
        # the list down).
        self.layout().insertWidget(3, cfg)
        # CRITICAL: the parent dialog (MakeDpdkReadyDialog) sized
        # itself for its original child list; inserting Sample
        # Stream after-the-fact does NOT trigger Qt to recompute
        # the minimum dialog height. Without an explicit bump,
        # the dialog stays at the OLD minimum and there isn't
        # enough vertical space for all widgets — the Sample
        # Stream group then either gets squeezed (compressed row
        # spacing → IP/MAC line edits visually touch) OR forces
        # its min height and OVERFLOWS into the next widget (the
        # action list renders ON TOP of the Rate row, which is
        # what the user's latest screenshot showed).
        # Fix: grow the DIALOG (not the group) so every widget
        # gets its natural sizeHint and nothing has to overflow.
        # sizeHint() is reliable here because every child widget
        # has been added by this point.
        _needed = self.sizeHint().height()
        if _needed > self.minimumHeight():
            self.setMinimumHeight(_needed)
        # Also nudge current size up if it's still below the new
        # minimum — show() will respect minimum but for code that
        # reads size() before show(), this gives a sane value.
        if self.height() < _needed:
            self.resize(self.width(), _needed)

        # v0.3.11: live TX stats label — sits just above the buttons.
        # Hidden until the flow starts; populated by the 2 s poll
        # against /api/streams/stats. For Mellanox bifurcated NICs
        # this is the ONLY place the operator sees confirmation that
        # packets are actually flowing (kernel counters miss them).
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "QLabel { background: #f0fdf4; border: 1px solid #bbf7d0; "
            "border-radius: 4px; padding: 6px 10px; color: #166534; "
            "font-family: Menlo, Monaco, monospace; font-size: 12px; }"
        )
        # v0.3.11 layout follow-up: wordWrap so the long stats text
        # ("TX: 46 Mpps · 23.3 Gbps · 2,193,742,528 packets total ·
        # engine: DPDK") doesn't force the dialog wider when it
        # first appears. Wrapping keeps the dialog stable so the
        # Sample Stream grid above never reflows.
        self._stats_label.setWordWrap(True)
        self._stats_label.hide()
        # Insert just before the button box (last item).
        self.layout().insertWidget(self.layout().count() - 1, self._stats_label)

        # Listen for the parent's success signal — fires after the
        # full ready plan completes with a bound NIC.
        self.finished_ready.connect(self._on_dpdk_ready)

    # ─────────────────────────────── multi-dialog coordination

    def set_sibling_iface_provider(self, provider) -> None:
        """Inject a zero-arg callable that returns a set of ifaces
        currently bound by SIBLING Blast Flow dialogs. The menu
        handler wires this so each dialog can warn before starting
        a second tx_worker on a NIC another dialog already owns.

        Kept as a setter (not a constructor kwarg) so existing
        call sites — tests that construct DpdkBlastFlowDialog
        directly — don't break. Default is a no-op set."""
        self._sibling_iface_provider = provider

    # ─────────────────────────────── flow start

    def _on_dpdk_ready(self, iface: str) -> None:
        """Called when the parent dialog finishes the ready plan
        with a successful bind. Kick the sample-stream POST."""
        # v0.3.11 multi-dialog safety: if a sibling Blast Flow
        # dialog already has a stream running on this iface,
        # warn the operator. Two tx_worker processes on the same
        # NIC port hit DPDK PMD lock contention OR silently
        # halve throughput as they share TX queue bandwidth —
        # either way the operator sees "blasting" but the
        # numbers don't match expectations.
        try:
            claimed = set(self._sibling_iface_provider() or ())
        except Exception:
            claimed = set()
        if iface in claimed:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Interface already in use")
            box.setText(
                f"<b>{iface}</b> is already being blasted by another "
                f"Blast a Flow window."
            )
            box.setInformativeText(
                "Starting a second tx_worker on the same NIC will "
                "either fail with a DPDK PMD lock error or silently "
                "halve throughput as the two flows share TX bandwidth. "
                "<br><br>Recommended: cancel and pick a different NIC. "
                "Continue anyway only if you specifically want to test "
                "shared-port contention."
            )
            box.setStandardButtons(
                QMessageBox.Cancel | QMessageBox.Ignore
            )
            box.setDefaultButton(QMessageBox.Cancel)
            choice = box.exec_()
            if choice != QMessageBox.Ignore:
                self._detail.setText(
                    f"<span style='color:#b91c1c;'>Cancelled — "
                    f"{iface} already in use by another Blast a "
                    f"Flow window.</span>"
                )
                return
        self._active_iface = iface
        self._detail.setText(
            f"<b>DPDK ready.</b> Starting sample flow on "
            f"<code>{iface}</code>…"
        )

        # Compose the streams dict the server expects.
        stream = _build_sample_stream(
            iface,
            frame_size=self._frame_size.value(),
            src_ip=self._src_ip.text().strip() or DEFAULT_SRC_IP,
            dst_ip=self._dst_ip.text().strip() or DEFAULT_DST_IP,
            dst_port=self._dst_port.value(),
            rate_pps=self._rate_pps.value(),
            src_mac=self._src_mac.text().strip() or DEFAULT_SRC_MAC,
            dst_mac=self._dst_mac.text().strip() or DEFAULT_DST_MAC,
        )
        self._active_stream_id = stream["stream_id"]
        payload = {"streams": {iface: [stream]}}

        Worker = _api_worker()
        w = Worker(
            method="POST",
            url=f"{self._server_url}/api/traffic/start",
            json=payload,
            timeout=30,
        )
        w.setParent(self)
        w.done.connect(self._on_blast_started)
        self._workers.append(w)
        w.start()

    def _on_blast_started(self, data, err) -> None:
        if err:
            self._detail.setText(
                f"<span style='color:#b91c1c;'>Sample flow failed to "
                f"start: {err}</span>"
            )
            QMessageBox.warning(
                self, "Flow Start Failed",
                f"DPDK is ready, but the sample flow couldn't start.\n\n"
                f"Error: {err}\n\n"
                f"You can still configure your own stream in the "
                f"Streams tab.",
            )
            return

        # Success — replace the Done/Close buttons with a Stop button
        # so the operator can shut the flow off without diving into
        # the Streams tab.
        self._detail.setText(
            f"<span style='color:#166534;'><b>✓ Flow blasting on "
            f"{self._active_iface}.</b></span> "
            f"{self._frame_size.value()}-byte UDP packets "
            f"{self._src_ip.text()} → "
            f"{self._dst_ip.text()}:{self._dst_port.value()}. "
            f"Click <b>Stop Flow</b> to halt, or close this dialog "
            f"(the flow keeps running until then)."
        )
        self._run_btn.setText("Stop Flow")
        self._run_btn.setEnabled(True)
        try:
            self._run_btn.clicked.disconnect()
        except TypeError:
            pass
        self._run_btn.clicked.connect(self._on_stop_flow)

        # v0.3.11: kick the per-stream stats poll. 2 s cadence
        # matches the rest of the app's stats refresh; cheap GET so
        # no rate-limit concerns. Stats label was hidden during
        # setup; reveal + start populating.
        self._stats_label.setText(
            "Waiting for first stats sample (≤2 s)…"
        )
        self._stats_label.show()
        self._last_tx_count = 0
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(2000)
        self._stats_timer.timeout.connect(self._poll_stream_stats)
        self._stats_timer.start()
        # Fire one immediately so the operator doesn't wait the
        # full 2 s for first feedback.
        QTimer.singleShot(200, self._poll_stream_stats)

    def _poll_stream_stats(self):
        """Fetch /api/streams/stats and find our blast-flow stream's
        tx_count/tx_rate. Refreshes the stats label. Best-effort —
        HTTP failure silently skips this tick (operator will see
        the value stop updating, which is itself a signal).
        """
        if not self._active_stream_id:
            return
        Worker = _api_worker()
        w = Worker(
            method="GET",
            url=f"{self._server_url}/api/streams/stats?status=Running",
            timeout=5,
        )
        w.setParent(self)
        w.done.connect(self._on_stream_stats)
        self._workers.append(w)
        w.start()

    def _on_stream_stats(self, data, err):
        if err or not data:
            return
        active = (data or {}).get("active_streams") or []
        ours = next(
            (s for s in active
             if isinstance(s, dict)
             and s.get("stream_id") == self._active_stream_id),
            None,
        )
        if not ours:
            self._stats_label.setText(
                f"<b>⚠ Stream not in tracker yet</b> — the server "
                f"hasn't reported per-stream stats for "
                f"{self._active_stream_id[:8]}… yet. If this "
                f"persists, tx_worker may have failed to launch — "
                f"check the server log."
            )
            self._stats_label.setStyleSheet(
                "QLabel { background: #fffbeb; border: 1px solid "
                "#fcd34d; border-radius: 4px; padding: 6px 10px; "
                "color: #92400e; font-family: Menlo, Monaco, monospace; "
                "font-size: 12px; }"
            )
            return
        tx_count = int(ours.get("tx_count") or 0)
        tx_rate = float(ours.get("tx_rate") or 0.0)
        frame_size = int(ours.get("frame_size") or 64)

        # v0.3.11: tx-stall watchdog. Stream is in the tracker but
        # not transmitting → tx_worker probably exited at launch.
        # Most common cause was the missing src_mac/dst_mac/src_ip/
        # dst_ip bug (now fixed). Warn after ~6 s of zero TX so
        # future regressions surface here instead of silently
        # showing 0 packets forever.
        if tx_count == 0 and tx_rate == 0:
            self._tx_stall_ticks += 1
            if self._tx_stall_ticks >= 3:
                self._stats_label.setStyleSheet(
                    "QLabel { background: #fef2f2; border: 1px "
                    "solid #fca5a5; border-radius: 4px; padding: "
                    "6px 10px; color: #b91c1c; font-family: Menlo, "
                    "Monaco, monospace; font-size: 12px; }"
                )
                self._stats_label.setText(
                    f"<b>⛔ Stream is registered but not "
                    f"transmitting</b> after ~{self._tx_stall_ticks * 2} s. "
                    f"tx_worker likely failed to launch. Check the "
                    f"server log for 'missing required fields' or "
                    f"DPDK init errors. Common causes: NIC link "
                    f"down, hugepages not allocated for the NIC's "
                    f"NUMA node, mlx5 PMD missing rdma-core."
                )
                return
        else:
            # Got real TX — reset the stall counter.
            self._tx_stall_ticks = 0
        # bps from pps × frame-size × 8. tx_rate from the server is
        # already in pps (per the schema comment "tx_rate" =
        # packets per second).
        tx_bps = tx_rate * frame_size * 8
        engine_badge = (
            "<span style='color:#166534;'>DPDK</span>"
            if ours.get("dpdk_enable")
            else "<span style='color:#b45309;'>Scapy (fell back)</span>"
        )
        # Pretty-print the rate.
        def _fmt_bps(b):
            for unit in ("bps", "Kbps", "Mbps", "Gbps"):
                if b < 1000:
                    return f"{b:.1f} {unit}"
                b /= 1000
            return f"{b:.1f} Tbps"
        def _fmt_pps(p):
            for unit in ("pps", "Kpps", "Mpps"):
                if p < 1000:
                    return f"{p:.0f} {unit}"
                p /= 1000
            return f"{p:.2f} Gpps"
        # Restore the green styling in case it was flipped to amber
        # by a prior "not in tracker" tick.
        self._stats_label.setStyleSheet(
            "QLabel { background: #f0fdf4; border: 1px solid "
            "#bbf7d0; border-radius: 4px; padding: 6px 10px; "
            "color: #166534; font-family: Menlo, Monaco, "
            "monospace; font-size: 12px; }"
        )
        self._stats_label.setText(
            f"<b>TX:</b> {_fmt_pps(tx_rate)}  ·  "
            f"{_fmt_bps(tx_bps)}  ·  "
            f"{tx_count:,} packets total  ·  "
            f"engine: {engine_badge}"
        )
        self._last_tx_count = tx_count

    def _stop_stats_poll(self):
        """Halt the stats poll timer. Safe to call from anywhere."""
        if self._stats_timer is not None:
            try:
                self._stats_timer.stop()
            except Exception:
                pass
            self._stats_timer = None

    def _on_stop_flow(self) -> None:
        if not self._active_stream_id or not self._active_iface:
            return
        self._run_btn.setEnabled(False)
        self._detail.setText("Stopping sample flow…")
        Worker = _api_worker()
        # v0.3.11 bug fix: /api/traffic/stop's schema is asymmetric
        # with /api/traffic/start. Start uses {"streams": {iface:
        # [stream_dict, …]}} (dict keyed by interface). Stop uses
        # {"streams": [{interface, stream_id}, …]} (LIST of entries).
        # Pre-fix we sent the dict shape — server iterated keys
        # (interface strings) and entry.get("interface") returned
        # None on every iteration, so no streams were stopped and
        # tx_worker kept hammering at line rate. Operator saw
        # "Sample flow stopped" in the dialog (we only checked the
        # HTTP error) while the iface stats kept climbing.
        payload = {
            "streams": [{
                "interface": self._active_iface,
                "stream_id": self._active_stream_id,
                "name": "blast-flow",
            }],
        }
        w = Worker(
            method="POST",
            url=f"{self._server_url}/api/traffic/stop",
            json=payload,
            timeout=15,
        )
        w.setParent(self)
        w.done.connect(self._on_blast_stopped)
        self._workers.append(w)
        w.start()

    def _on_blast_stopped(self, data, err) -> None:
        if err:
            self._detail.setText(
                f"<span style='color:#b91c1c;'>Stop failed: {err}</span>"
            )
            self._run_btn.setEnabled(True)
            return
        # v0.3.11: verify the server actually stopped something
        # rather than trusting the 200. /api/traffic/stop returns
        # {"stopped": [{interface, stream_id}, …]} — empty list
        # means the request was malformed or the stream wasn't
        # in the tracker. Without this check the dialog used to
        # claim success while tx_worker kept hammering line rate.
        stopped_list = (data or {}).get("stopped") or []
        if not stopped_list:
            # Server accepted the request but stopped zero streams.
            # Most likely cause: stream_id didn't match anything in
            # stream_tracker, OR the payload shape regression (v0.3.11
            # fixed the shape — this guard catches a future drift).
            err_msg = (
                (data or {}).get("error")
                or f"server stopped 0 of 1 stream(s) — request "
                   f"shape may be wrong, or stream_id "
                   f"{(self._active_stream_id or '')[:8]}… not in "
                   f"the tracker"
            )
            self._detail.setText(
                f"<span style='color:#b91c1c;'>Stop reported success "
                f"but didn't actually stop the flow: {err_msg}. "
                f"tx_worker may still be transmitting — check Tools "
                f"→ DPDK → Status, or kill manually on the server: "
                f"<code>sudo pkill -f '--stream-id "
                f"{self._active_stream_id or ''}'</code></span>"
            )
            self._run_btn.setEnabled(True)
            return
        self._detail.setText(
            "<span style='color:#166534;'>✓ Sample flow stopped. "
            "Click <b>Start Flow Again</b> to send another with "
            "the same settings (or tweak the Sample Stream fields "
            "above first).</span>"
        )
        # v0.3.11: don't lock the operator out of restarting. Flip
        # the button from "Stop Flow" → "Start Flow Again", clear
        # the stream-id so a fresh start generates a new one, and
        # re-wire the click handler. Same dialog, same NIC, same
        # bound state — no need to re-run the whole orchestrator.
        self._run_btn.setText("Start Flow Again")
        self._run_btn.setEnabled(True)
        try:
            self._run_btn.clicked.disconnect()
        except TypeError:
            pass
        self._run_btn.clicked.connect(self._on_restart_flow)
        # v0.3.11: stop the stats poll + show the final totals so
        # the operator sees the lifetime counters one last time
        # rather than the label going blank.
        self._stop_stats_poll()
        if self._last_tx_count > 0:
            self._stats_label.setText(
                f"<b>Stopped.</b> Final TX: "
                f"{self._last_tx_count:,} packets. Start Again to "
                f"send another batch."
            )
        # Active stream id retired — _on_restart_flow generates a
        # fresh UUID via _build_sample_stream when it fires.
        self._active_stream_id = None

    def _on_restart_flow(self) -> None:
        """Re-launch the sample flow on the same bound NIC. Same
        path as the initial _on_dpdk_ready (which fires after a
        successful bind), but skips the orchestrator's NIC-pick
        step because we already have the bound iface."""
        if not self._active_iface:
            # Shouldn't happen — we only get here from the stop
            # path, which preserves _active_iface. Defensive: bail
            # if it's gone for some reason.
            QMessageBox.warning(
                self, "Restart Flow",
                "No bound NIC remembered — re-open Blast a Flow "
                "from the menu to run the orchestrator again.",
            )
            return
        # Reset the stall watchdog so the previous run's counters
        # don't bleed into the restart's "stalled?" decision.
        self._tx_stall_ticks = 0
        self._last_tx_count = 0
        # _on_dpdk_ready does the whole start-flow sequence —
        # builds a fresh sample-stream dict with a new UUID and
        # POSTs /api/traffic/start.
        self._on_dpdk_ready(self._active_iface)

    # ─────────────────────────────── window close cleanup

    def closeEvent(self, ev):
        """If the operator closes the dialog mid-flow, send a Stop
        on the way out so we don't leak server-side traffic.

        v0.3.11: fire-and-forget via a daemon thread instead of a
        blocking ``requests.post`` on the GUI thread — a 5-second
        timeout against an unreachable server used to freeze the
        close visibly. Daemon thread dies with the process if the
        send never completes; for the common case (server reachable)
        the stop lands within ~50ms. Best-effort by design — we
        accept the rare orphaned server-side flow over a frozen
        client UI.

        Also halts the stats-poll timer FIRST so it doesn't fire
        against a half-destroyed widget mid-close (Qt would warn).
        """
        self._stop_stats_poll()
        if self._active_stream_id and self._active_iface:
            stream_id = self._active_stream_id
            iface = self._active_iface
            url = f"{self._server_url}/api/traffic/stop"

            def _send_stop():
                try:
                    import requests
                    # v0.3.11: list-of-entries shape — matches
                    # /api/traffic/stop's schema. See _on_stop_flow
                    # for the full rationale (dict vs list bug).
                    requests.post(
                        url,
                        json={
                            "streams": [{
                                "interface": iface,
                                "stream_id": stream_id,
                                "name": "blast-flow",
                            }],
                        },
                        # Tight timeout — the daemon thread won't
                        # block process exit anyway, but bounding
                        # it avoids accumulating zombie threads
                        # against a known-dead server.
                        timeout=1.5,
                    )
                except Exception as exc:
                    logger.debug(
                        f"[BLAST FLOW] stop-on-close failed: {exc}"
                    )

            import threading
            threading.Thread(
                target=_send_stop,
                name="blast-flow-stop-on-close",
                daemon=True,
            ).start()
        super().closeEvent(ev)
