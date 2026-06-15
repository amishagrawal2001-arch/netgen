#statistics_section.py#

from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox, QLabel, QTabWidget, QWidget, QSizePolicy, QLineEdit
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QFontMetrics
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPointF
import requests
import logging
import time as _time
from collections import deque

# v0.2.99: shared sort-state helper. The stream-stats table has
# setSortingEnabled(True) but the periodic refresh rebuilds via
# setRowCount(0) + per-row setItem, which Qt would re-sort on EVERY
# setItem call (the same scramble v0.2.92 fixed for Devices tables).
# Capture before the rebuild, restore after.
from utils.table_sort_state import capture_sort_state, restore_sort_state

logger = logging.getLogger(__name__)


# v0.5.144: iface-level packet-loss helper.
#
# Why this exists (and why v0.5.139's per-stream rx_count aggregation
# was wrong): when a stream is blasted at line rate, the RX-engine
# sniffer (scapy / DPDK rx_worker / etc.) drops a large fraction of
# what actually arrives on the wire. So `stream.rx_count` reports
# ~5M while the iface PHY counter shows ~820M actually received.
# v0.5.139 fed `stream.rx_count` into the iface's `rx_for_loss`,
# producing the operator-reported "99.37% loss" on a link that was
# really losing ~1%.
#
# The fix: ground-truth iface loss on the iface PHY counters that
# v0.5.135 already populates in /api/interfaces (interface.tx,
# interface.rx — these are `tx_packets_phy` / `rx_packets_phy` from
# ethtool -S when the NIC is Mellanox). Pair the two halves of a
# back-to-back link via the streams that traverse them, and display
# the SAME pair_loss number on both halves.
def compute_iface_pair_loss(own_phy_tx, own_phy_rx, peer_phy_tx, peer_phy_rx):
    """Pure helper: given the iface's own PHY counters and its peer's,
    return (lost, loss_pct).

    Args:
      own_phy_tx, own_phy_rx: this iface's PHY TX/RX counts.
      peer_phy_tx, peer_phy_rx: the peer iface's PHY TX/RX counts.
        For a loopback (no peer), pass own_phy_tx / own_phy_rx as the
        peer values too.

    Returns:
      (lost, loss_pct). `loss_pct` is in 0..100 and 0.0 when there's
      no TX activity to measure against. Negative diffs clamp to 0 —
      the wire can't deliver more than was sent; any "excess RX" is
      noise (CRC frames the PHY counter also bins).
    """
    pair_tx = max(int(own_phy_tx or 0), int(peer_phy_tx or 0))
    pair_rx = max(int(own_phy_rx or 0), int(peer_phy_rx or 0))
    lost = max(0, pair_tx - pair_rx)
    if pair_tx <= 0:
        return 0, 0.0
    loss_pct = (lost / pair_tx) * 100.0
    return lost, loss_pct


class ThroughputChart(QWidget):
    """Lightweight rolling-line chart for live throughput.

    No matplotlib / pyqtgraph dep — pure QPainter. Holds a rolling
    deque of (timestamp, dict) samples, draws one line per interface,
    auto-scales Y, slides a fixed-width time window.

    add_sample(iface_to_value, ts) is called from the existing polling
    tick; paintEvent renders.
    """

    # Window of time to show on the X axis (seconds).
    WINDOW_SEC = 60
    # Distinct line colors cycled through interfaces (in selection order).
    PALETTE = [
        QColor("#2563eb"),  # blue
        QColor("#16a34a"),  # green
        QColor("#dc2626"),  # red
        QColor("#d97706"),  # amber
        QColor("#7c3aed"),  # purple
        QColor("#0891b2"),  # cyan
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        # samples: deque of (timestamp_seconds, {iface_name: bps_value})
        self._samples = deque(maxlen=300)  # ~5 min at 1 Hz
        # Title + Y-axis unit derived from current series ("Gbps" / "Mbps" / "fps").
        self._title = "Aggregate TX Bit Rate (last 60s)"
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Fill background through stylesheet so it matches the surrounding tabs.
        self.setStyleSheet("background: #ffffff;")

    def add_sample(self, iface_to_bps, ts=None):
        """Add a sample point. iface_to_bps is {iface_name: bps_value}.
        Multiple interfaces can be tracked simultaneously."""
        if ts is None:
            ts = _time.time()
        self._samples.append((ts, dict(iface_to_bps or {})))
        self.update()  # schedule paint

    def clear_samples(self):
        self._samples.clear()
        self.update()

    def remove_iface_by_prefix(self, prefix):
        """Drop every iface whose name starts with `prefix` from every
        historical sample. Used when a TGen chassis is removed so its
        per-interface lines vanish from the chart immediately instead
        of taking up to WINDOW_SEC to slide out of the visible window.
        """
        if not prefix:
            return
        new_samples = deque(maxlen=self._samples.maxlen)
        for ts, vals in self._samples:
            if not isinstance(vals, dict):
                new_samples.append((ts, vals))
                continue
            filtered = {k: v for k, v in vals.items()
                        if not (isinstance(k, str) and k.startswith(prefix))}
            new_samples.append((ts, filtered))
        self._samples = new_samples
        self.update()

    def _samples_iface_history(self):
        """Set of iface names that have appeared in any visible sample.

        Used by the polling code to keep an iface charted (with value 0)
        even after it stops sending, until it slides out of the window —
        otherwise the line just disappears mid-chart, which looks like a
        bug rather than "traffic stopped."
        """
        if not self._samples:
            return set()
        now = self._samples[-1][0]
        t_min = now - self.WINDOW_SEC
        seen = set()
        for ts, vals in self._samples:
            if ts < t_min:
                continue
            seen.update(vals.keys())
        return seen

    @staticmethod
    def _format_bps(v):
        if v <= 0:
            return "0"
        if v >= 1e9:
            return f"{v / 1e9:.1f} Gbps"
        if v >= 1e6:
            return f"{v / 1e6:.1f} Mbps"
        if v >= 1e3:
            return f"{v / 1e3:.1f} Kbps"
        return f"{v:.0f} bps"

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # Background already set via stylesheet but redraw for safety
        p.fillRect(0, 0, w, h, QColor("#ffffff"))

        # Layout
        margin_l, margin_r = 70, 16
        margin_t, margin_b = 26, 28
        plot_x0 = margin_l
        plot_y0 = margin_t
        plot_w = max(1, w - margin_l - margin_r)
        plot_h = max(1, h - margin_t - margin_b)

        # Title
        p.setPen(QColor("#1f2937"))
        title_font = QFont(self.font())
        title_font.setPointSize(11)
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(margin_l, 18, self._title)

        # No data yet
        if not self._samples:
            p.setPen(QColor("#9ca3af"))
            small = QFont(self.font()); small.setPointSize(10)
            p.setFont(small)
            p.drawText(plot_x0 + plot_w // 2 - 80, plot_y0 + plot_h // 2,
                       "Waiting for samples…")
            return

        # Determine time window
        now = self._samples[-1][0]
        t_min = now - self.WINDOW_SEC
        # Iface ordering = first-seen order across the visible window
        iface_order = []
        seen = set()
        for ts, vals in self._samples:
            if ts < t_min:
                continue
            for iface in vals:
                if iface not in seen:
                    seen.add(iface)
                    iface_order.append(iface)

        # Determine Y max across all visible series. We want the SUM
        # across ifaces (when stacked) but here each series is drawn
        # separately, so y_max is the max single-series value.
        y_max = 0.0
        for ts, vals in self._samples:
            if ts < t_min:
                continue
            for v in vals.values():
                if v > y_max:
                    y_max = v
        if y_max <= 0:
            y_max = 1.0  # avoid div-by-zero; chart will be flat

        # Round y_max up to a chart-friendly top with ~5% headroom so
        # the line doesn't kiss the top edge.
        #
        # OLD bug: the loop was `while step * 8 < y_max: step *= 2`
        # but y_top was `step * 4`. For y_max = 400 Gbps, step = 50,
        # `step * 8 = 400 < 400` is false → loop exits → y_top = 200.
        # Result: the line shot off the top of a chart whose Y axis
        # claimed to top out at 200 Gbps.
        #
        # New: pick the smallest "nice" multiple of a power of 10 that
        # is >= y_max with headroom, from the set {1, 2, 2.5, 5, 10}.
        # All five divide cleanly into 5 intervals so tick labels
        # come out as round numbers (0/100/200/300/400/500 for a
        # 500 Gbps top, etc.).
        import math
        target = y_max * 1.05
        log = math.log10(max(target, 1.0))
        pow10 = 10 ** math.floor(log)
        y_top = 10 * pow10  # fallback / decade-overflow
        for m in (1.0, 2.0, 2.5, 5.0, 10.0):
            candidate = m * pow10
            if candidate >= target:
                y_top = candidate
                break

        # Axes
        axis_pen = QPen(QColor("#cbd5e1"))
        axis_pen.setWidth(1)
        p.setPen(axis_pen)
        p.drawLine(plot_x0, plot_y0, plot_x0, plot_y0 + plot_h)  # Y axis
        p.drawLine(plot_x0, plot_y0 + plot_h,
                   plot_x0 + plot_w, plot_y0 + plot_h)  # X axis

        # Y-axis labels — 5 intervals (6 labels including 0). With the
        # {1, 2, 2.5, 5, 10} × 10^N y_top picker above this gives clean
        # tick values: top=100→step=20, top=200→step=40, top=250→step=50,
        # top=500→step=100, top=1000→step=200.
        label_font = QFont(self.font()); label_font.setPointSize(9)
        p.setFont(label_font)
        p.setPen(QColor("#6b7280"))
        N_TICKS = 5
        for i in range(N_TICKS + 1):
            y_val = y_top * i / N_TICKS
            y_px = plot_y0 + plot_h - int(plot_h * i / N_TICKS)
            p.drawText(4, y_px + 4, self._format_bps(y_val))
            # Faint grid line
            grid_pen = QPen(QColor("#f3f4f6"))
            grid_pen.setWidth(1)
            p.setPen(grid_pen)
            if i > 0:
                p.drawLine(plot_x0 + 1, y_px, plot_x0 + plot_w, y_px)
            p.setPen(QColor("#6b7280"))

        # X-axis labels (-60s, -45s, -30s, -15s, now)
        for i, off in enumerate((-60, -45, -30, -15, 0)):
            x_px = plot_x0 + int(plot_w * (i / 4))
            label = "now" if off == 0 else f"-{-off}s"
            p.drawText(x_px - 12, plot_y0 + plot_h + 18, label)

        # Plot series
        for idx, iface in enumerate(iface_order):
            color = self.PALETTE[idx % len(self.PALETTE)]
            line_pen = QPen(color)
            line_pen.setWidth(2)
            p.setPen(line_pen)
            points = []
            for ts, vals in self._samples:
                if ts < t_min:
                    continue
                v = vals.get(iface, 0.0)
                x = plot_x0 + int(plot_w * (ts - t_min) / self.WINDOW_SEC)
                y = plot_y0 + plot_h - int(plot_h * (v / y_top)) if y_top > 0 else plot_y0 + plot_h
                points.append(QPointF(x, y))
            if len(points) >= 2:
                for i in range(len(points) - 1):
                    p.drawLine(points[i], points[i + 1])

        # Legend (top-right)
        legend_x = plot_x0 + plot_w - 200
        legend_y = plot_y0 + 4
        p.setFont(label_font)
        for idx, iface in enumerate(iface_order[:5]):  # cap legend at 5
            color = self.PALETTE[idx % len(self.PALETTE)]
            p.setPen(QPen(color, 3))
            p.drawLine(legend_x, legend_y + 6, legend_x + 14, legend_y + 6)
            p.setPen(QColor("#1f2937"))
            short = iface.split(" - ")[-1] if " - " in iface else iface
            p.drawText(legend_x + 18, legend_y + 10, short[:18])
            legend_y += 16

        p.end()


class StatisticsFetchWorker(QThread):
    """Background worker for fetching statistics to prevent UI blocking."""
    
    # Signals
    interfaces_fetched = pyqtSignal(dict, dict)  # (server, interfaces_data)
    stream_stats_fetched = pyqtSignal(dict, list)  # (server, stream_stats)
    fetch_error = pyqtSignal(dict, str)  # (server, error_message)
    finished = pyqtSignal()  # Signal when all fetches complete
    
    def __init__(self, servers, fetch_type="both", connection_manager=None, parent=None):
        super().__init__(parent)
        self.servers = servers  # List of server dicts
        self.fetch_type = fetch_type  # "interfaces", "streams", or "both"
        self.connection_manager = connection_manager
        self._should_stop = False
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Fetch statistics from all servers in background thread."""
        for server in self.servers:
            if self._should_stop:
                break
            
            if not server.get("online", True):
                continue
            
            server_address = server.get("address")
            
            # Fetch interfaces if needed.
            # Timeouts bumped from 2s/1s to 4s/3s — at 1s the polling timer was
            # racing the server while it was busy writing stream stats DB entries
            # (~1000 pps active streams), causing constant
            # "Read timed out" retry warnings even on a healthy server.
            if self.fetch_type in ("interfaces", "both"):
                try:
                    if self.connection_manager:
                        response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=4)
                    else:
                        response = requests.get(f"{server_address}/api/interfaces", timeout=4)

                    if response.status_code == 200:
                        interfaces = response.json()
                        self.interfaces_fetched.emit(server, {"interfaces": interfaces, "status_code": 200})
                    else:
                        self.fetch_error.emit(server, f"HTTP {response.status_code}")
                except Exception as e:
                    self.fetch_error.emit(server, str(e))

            # Fetch stream stats if needed
            if self.fetch_type in ("streams", "both"):
                try:
                    if self.connection_manager:
                        response = self.connection_manager.get(f"{server_address}/api/streams/stats", timeout=3)
                    else:
                        response = requests.get(f"{server_address}/api/streams/stats", timeout=3)

                    if response.status_code == 200:
                        stream_stats = response.json().get("active_streams", [])
                        # Annotate each stream with latency stats from the
                        # server's per-iface sampler. We do one fetch per
                        # unique iface in the response (cached locally for
                        # this tick) so a 16-stream / 1-iface test only
                        # makes a single extra HTTP call. The endpoint is
                        # idempotent — it lazily starts the sampler on
                        # first call, then returns cached rolling stats.
                        latency_by_iface = {}
                        unique_ifaces = {
                            s.get("interface") for s in stream_stats
                            if s.get("interface")
                            and (s.get("enable_timestamps") or s.get("latency_enabled"))
                        }
                        for iface in unique_ifaces:
                            if self._should_stop:
                                break
                            try:
                                url = f"{server_address}/api/latency/stats?iface={iface}"
                                if self.connection_manager:
                                    lat_resp = self.connection_manager.get(url, timeout=2)
                                else:
                                    lat_resp = requests.get(url, timeout=2)
                                if lat_resp.status_code == 200:
                                    latency_by_iface[iface] = lat_resp.json()
                            except Exception:
                                # Sampler unavailable / endpoint missing on
                                # an older server — leave the cell as "—"
                                # rather than failing the whole stats poll.
                                pass
                        for s in stream_stats:
                            iface = s.get("interface")
                            if iface and iface in latency_by_iface:
                                iface_blob = latency_by_iface[iface]
                                # v0.3.5: prefer the per-stream
                                # snapshot when the server returns
                                # one for this stream's ID. Pre-
                                # v0.3.5 the server only returned an
                                # iface-aggregate latency dict — two
                                # streams on the same iface got the
                                # SAME mixed-samples blob. Falls
                                # back to the aggregate when:
                                #   * server is older (no `streams`
                                #     field in response)
                                #   * no signature seen for this sid
                                #     yet (flow_tracking=off or
                                #     warmup window)
                                streams_map = (
                                    iface_blob.get("streams") or {}
                                ) if isinstance(iface_blob, dict) else {}
                                sid = (s.get("stream_id")
                                       or s.get("id") or "")
                                per_stream = (streams_map.get(sid)
                                              if sid else None)
                                if per_stream:
                                    s["_latency"] = per_stream
                                else:
                                    s["_latency"] = iface_blob
                        self.stream_stats_fetched.emit(server, stream_stats)
                    else:
                        self.fetch_error.emit(server, f"HTTP {response.status_code}")
                except Exception as e:
                    self.fetch_error.emit(server, str(e))
        
        self.finished.emit()


class TrafficGenClientStatisticsSection():
    def setup_traffic_statistics_section(self):
        self.statistics_group = QGroupBox("Traffic Statistics")
        # Pull the QGroupBox's internal padding right in. Default Qt
        # leaves ~16px on each side which is the "gray gap" the user
        # sees around the Clear Stats button — even with a tight
        # button_layout, the parent group still pads its contents.
        # Trim to almost-flush so all spare height inside the dock
        # goes to the tables, not to chrome.
        self.statistics_group.setStyleSheet(
            "QGroupBox { margin: 0; padding: 0 4px 2px 4px; border: none; }"
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Affordance hint — tells the user they can detach the stats
        # dock into a floating window (a QDockWidget feature that's
        # technically discoverable via drag-the-titlebar or the
        # float-button in the title chrome, but most users don't
        # find it without a nudge). Subtle styling so it doesn't
        # compete with the actual data tables for attention.
        self.stats_dock_hint = QLabel(
            "💡  Double-click the title bar (or drag it) to pop the "
            "Traffic Statistics out into a separate window."
        )
        self.stats_dock_hint.setStyleSheet(
            "QLabel {"
            "  color: #6b7280;"
            "  font-size: 11px;"
            "  font-style: italic;"
            "  padding: 4px 10px;"
            "  background-color: #f9fafb;"
            "  border-left: 3px solid #93c5fd;"
            "  border-radius: 2px;"
            "}"
        )
        self.stats_dock_hint.setToolTip(
            "Click the 'X' icon in the title bar to close the pane.\n"
            "Bring it back any time via View → Traffic Statistics Pane,\n"
            "or use 'Re-dock Traffic Statistics' if it gets stranded."
        )
        layout.addWidget(self.stats_dock_hint)

        # Create tab widget for statistics
        self.statistics_tab_widget = QTabWidget()
        
        # Tab 1: Interface Statistics
        interface_stats_tab = QWidget()
        interface_stats_layout = QVBoxLayout(interface_stats_tab)
        
        # Interface Statistics Table
        self.statistics_table = QTableWidget()
        self.statistics_table.setRowCount(12)
        self.statistics_table.setColumnCount(0)
        self.statistics_table.setVerticalHeaderLabels([
            "Status", "Sent Frames", "Received Frames", "Sent Bytes", "Received Bytes",
            "Send Frame Rate (fps)", "Receive Frame Rate (fps)", "Send Bit Rate (bps)",
            "Receive Bit Rate (bps)", "Errors", "Packets Lost", "Loss %"
        ])
        
        # Apply professional styling — bumped for readability:
        # body 11px → 13px, header 11px → 12px, header bg/contrast strengthened
        # so column titles stand out from cells. Cell padding 4/8 → 6/10 gives
        # rows more breathing room on a 1080p screen.
        table_style = """
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f5f7fa;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                font-size: 13px;
                outline: none;
                color: #111827;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #1e40af;
            }
            QTableWidget::item {
                padding: 6px 10px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
            QTableWidget::item:hover:!selected {
                background-color: #eef2f7;
            }
            QHeaderView::section {
                background-color: #e5e7eb;
                padding: 3px 8px;
                border: 1px solid #cbd5e1;
                border-left: none;
                border-top: none;
                font-weight: 700;
                font-size: 11px;
                color: #1f2937;
                letter-spacing: 0.3px;
            }
            QHeaderView::section:first {
                border-left: 1px solid #cbd5e1;
            }
            QTableCornerButton::section {
                background-color: #e5e7eb;
                border: 1px solid #cbd5e1;
            }
        """
        self.statistics_table.setStyleSheet(table_style)
        self.statistics_table.setAlternatingRowColors(True)

        # Set font — bumped 10pt → 12pt for live throughput readouts.
        # The stats dock is where you check whether traffic is hitting
        # line rate; cell text needs to be readable across the room.
        font = QFont()
        font.setFamily("Monaco, Consolas, 'Courier New', monospace")
        font.setPointSize(12)
        self.statistics_table.setFont(font)
        # Taller rows so 12pt monospace doesn't clip and rows are easier
        # to track row-by-row when scanning multiple interfaces.
        self.statistics_table.verticalHeader().setDefaultSectionSize(32)
        self.statistics_table.verticalHeader().setMinimumSectionSize(28)
        # Cap horizontal-header height so font-metric quirks can't push
        # it taller than the styled padding implies.
        self.statistics_table.horizontalHeader().setFixedHeight(22)
        
        interface_stats_layout.addWidget(self.statistics_table)
        interface_stats_layout.setContentsMargins(0, 0, 0, 0)
        
        # Tab 2: Stream Statistics
        stream_stats_tab = QWidget()
        stream_stats_layout = QVBoxLayout(stream_stats_tab)
        
        # Stream Statistics Table
        self.stream_statistics_table = QTableWidget()
        self.stream_statistics_table.setColumnCount(13)
        self.stream_statistics_table.setHorizontalHeaderLabels([
            "Stream Name", "Interface", "Engine", "TX Count", "RX Count",
            "TX Rate", "RX Rate", "TX Bit Rate", "RX Bit Rate",
            "Latency (μs)", "Loss %", "Status", "Flow Tracking",
        ])
        self.stream_statistics_table.setStyleSheet(table_style)
        self.stream_statistics_table.setAlternatingRowColors(True)
        self.stream_statistics_table.setFont(font)
        self.stream_statistics_table.setSortingEnabled(True)
        self.stream_statistics_table.verticalHeader().setDefaultSectionSize(32)
        self.stream_statistics_table.verticalHeader().setMinimumSectionSize(28)
        self.stream_statistics_table.horizontalHeader().setFixedHeight(22)

        # v0.3.11: filter input ABOVE the Stream Statistics table
        # (matches the Devices / L2 Emulation / Stateful TCP / Streams
        # configuration-tab convention). Previously this widget sat in
        # the dock's bottom action bar next to Export CSV, which the
        # user flagged as inconsistent — and which placed a "filter"
        # control nowhere near the table it actually filtered. State
        # lives on `self._stream_filter_needle` (lazy-init below) so
        # the widget can be torn down + rebuilt without losing the
        # current filter value across construction orders.
        if not hasattr(self, "_stream_filter_needle"):
            self._stream_filter_needle = ""
        _stream_filter_row = QHBoxLayout()
        _stream_filter_row.setContentsMargins(0, 0, 0, 2)
        _stream_filter_row.setSpacing(6)
        _stream_filter_label = QLabel("Filter:")
        _stream_filter_label.setStyleSheet(
            "color: #6b7280; font-size: 11px;"
        )
        self.stream_filter_edit = QLineEdit()
        self.stream_filter_edit.setPlaceholderText(
            "Stream Name / Interface / Engine …"
        )
        self.stream_filter_edit.setClearButtonEnabled(True)
        self.stream_filter_edit.setFixedHeight(22)
        self.stream_filter_edit.setMaximumWidth(280)
        self.stream_filter_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #cbd5e1; border-radius: 4px;"
            "  padding: 0 6px; font-size: 12px; background: #ffffff; }"
            "QLineEdit:focus { border-color: #2563eb; }"
        )
        self.stream_filter_edit.setToolTip(
            "Hide rows in the Stream Statistics table whose name / "
            "interface / engine don't match. Case-insensitive substring."
        )
        self.stream_filter_edit.textChanged.connect(
            self._on_stream_filter_changed
        )
        _stream_filter_row.addWidget(_stream_filter_label)
        _stream_filter_row.addWidget(self.stream_filter_edit)
        _stream_filter_row.addStretch(1)
        stream_stats_layout.addLayout(_stream_filter_row)

        stream_stats_layout.addWidget(self.stream_statistics_table)
        stream_stats_layout.setContentsMargins(0, 0, 0, 0)
        
        # Tab 3: Live throughput chart — rolling 60s line chart of per-iface
        # TX bit-rate. Lightweight pure-QPainter implementation; no extra deps.
        live_chart_tab = QWidget()
        live_chart_layout = QVBoxLayout(live_chart_tab)
        live_chart_layout.setContentsMargins(4, 4, 4, 4)
        self.live_throughput_chart = ThroughputChart(live_chart_tab)
        live_chart_layout.addWidget(self.live_throughput_chart)

        # Add tabs to tab widget
        self.statistics_tab_widget.addTab(interface_stats_tab, "Interface Statistics")
        self.statistics_tab_widget.addTab(stream_stats_tab, "Stream Statistics")
        self.statistics_tab_widget.addTab(live_chart_tab, "Live Chart")
        # Don't auto-expand tabs to fill the bar — Qt's expand mode shrinks
        # text when the bar is narrow, which clipped "Interface Statistics"
        # to "nterface Statisti..." after the 13px font bump. Let each tab
        # size to its label + the min-width set in CSS instead.
        self.statistics_tab_widget.tabBar().setExpanding(False)
        self.statistics_tab_widget.tabBar().setUsesScrollButtons(True)
        self.statistics_tab_widget.tabBar().setElideMode(Qt.ElideNone)
        
        # Tab styling — compact:
        # - padding tightened 8/20 → 4/12 so tabs aren't oversized chips
        # - min-width dropped 170 → 0 (let labels size to content)
        # - font 13 → 12 (still legible but takes less vertical room)
        # Active tab keeps its 3px bottom-border + brighter blue so the
        # selected tab is still unmistakable.
        self.statistics_tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #eef2f7;
                color: #374151;
                border: 1px solid #cbd5e1;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                padding: 4px 12px;
                margin-right: 2px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1d4ed8;
                border-bottom: 3px solid #2563eb;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
        """)
        
        layout.addWidget(self.statistics_tab_widget, 1)  # stretch=1: tabs take all spare vertical room

        # Clear Stats button row — kept compact so it doesn't eat
        # vertical space inside the dock. Tight contentsMargins, a
        # fixed-height button, and zero spacing keep this row at
        # ~26px tall instead of the ~50px default Qt would give a
        # QHBoxLayout with a button.
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 2, 0, 2)
        button_layout.setSpacing(0)
        button_layout.addStretch(1)

        self.clear_stats_button_traffic = QPushButton("Clear Stats")
        self.clear_stats_button_traffic.setFixedSize(110, 24)
        self.clear_stats_button_traffic.setCursor(Qt.PointingHandCursor)
        self.clear_stats_button_traffic.setToolTip(
            "Tare counters/byte totals to zero (baseline subtract). "
            "Per-second rates are instantaneous — no baseline needed."
        )
        # Match the action-bar's neutral-white + thin-gray-border style
        # (BTN_BASE in stream_control.py) so the Clear Stats button reads
        # as the same family of control. Slightly tinted hover to give a
        # subtle "destructive" cue without shouting — clearing is
        # unrecoverable, but it's only a visual reset (the underlying
        # cumulative counters survive).
        self.clear_stats_button_traffic.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  color: #374151;"
            "  font-size: 11px;"
            "  font-weight: 500;"
            "  padding: 0 8px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #fef2f2;"   # subtle red tint
            "  border-color: #f87171;"
            "  color: #b91c1c;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #fee2e2;"
            "  border-color: #ef4444;"
            "}"
            "QPushButton:disabled {"
            "  background-color: #f9fafb;"
            "  border-color: #e5e7eb;"
            "  color: #9ca3af;"
            "}"
        )
        self.clear_stats_button_traffic.clicked.connect(self.clear_cached_statistics)
        button_layout.addWidget(self.clear_stats_button_traffic)

        # Export CSV — dumps the currently visible stats tables to a
        # single CSV with one section per table (Interface Statistics,
        # then Stream Statistics). Useful for attaching a snapshot to a
        # test report without screenshotting. Sits flush against the
        # Clear Stats button so the row stays compact.
        self.export_stats_button = QPushButton("Export CSV")
        self.export_stats_button.setFixedSize(110, 24)
        self.export_stats_button.setCursor(Qt.PointingHandCursor)
        self.export_stats_button.setToolTip(
            "Save the current Interface + Stream statistics tables to a "
            "CSV file. Captures whatever is on screen right now — clear "
            "first if you want a fresh baseline."
        )
        # Neutral style — non-destructive. Blue-tinted hover to read as
        # primary-ish without competing with Apply elsewhere.
        self.export_stats_button.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  color: #374151;"
            "  font-size: 11px;"
            "  font-weight: 500;"
            "  padding: 0 8px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #eff6ff;"
            "  border-color: #60a5fa;"
            "  color: #1d4ed8;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #dbeafe;"
            "  border-color: #2563eb;"
            "}"
        )
        self.export_stats_button.clicked.connect(self.export_statistics_csv)
        button_layout.addWidget(self.export_stats_button)

        # ── v0.2.99: pause / last-refresh pair ─────────────────────────
        # Pause toggle + last-refresh chip stay in this bottom action
        # bar (next to Clear Stats / Export CSV) — they're dock-scoped,
        # not table-scoped. The v0.2.99 "filter / pause / last-refresh
        # trio" was split in v0.3.11: the substring filter moved to
        # sit ABOVE the Stream Statistics table (its real target) so
        # the dock matches the same "filter above table" convention as
        # the Devices / L2 / Stateful TCP / Streams configuration tabs.
        # State init — lazy, since this class is used as a mixin and
        # has no __init__ of its own.
        if not hasattr(self, "_refresh_paused"):
            self._refresh_paused = False

        # Pause-refresh toggle — flips `self._refresh_paused`. The two
        # update_* paths check the flag at entry and bail early if
        # paused. The polling timers in main.py keep firing (cheap)
        # but the GUI stops updating, which is what the operator
        # actually wants when grabbing a screenshot.
        self.pause_refresh_button = QPushButton("Pause")
        self.pause_refresh_button.setCheckable(True)
        self.pause_refresh_button.setFixedSize(72, 24)
        self.pause_refresh_button.setCursor(Qt.PointingHandCursor)
        self.pause_refresh_button.setToolTip(
            "Pause / resume the 2-second statistics refresh.\n"
            "Polling continues server-side; only the GUI freezes.\n"
            "Useful for screenshotting a stable snapshot."
        )
        self.pause_refresh_button.setStyleSheet(
            "QPushButton { border: 1px solid #cbd5e1; border-radius: 5px; "
            "background-color: #ffffff; color: #374151; font-size: 11px; "
            "font-weight: 500; padding: 0 8px; }"
            "QPushButton:hover { background-color: #f1f5f9; }"
            "QPushButton:checked { background-color: #fef3c7; "
            "border-color: #f59e0b; color: #92400e; font-weight: 600; }"
        )
        self.pause_refresh_button.toggled.connect(
            self._on_refresh_pause_toggled
        )
        button_layout.addWidget(self.pause_refresh_button)

        # Last-refresh time chip — updated whenever
        # update_stream_statistics_table finishes a non-paused rebuild.
        # Format: "Updated HH:MM:SS". Empty until the first refresh.
        self.last_refresh_label = QLabel("")
        self.last_refresh_label.setStyleSheet(
            "QLabel { color: #6b7280; font-size: 10px; padding: 0 6px; }"
        )
        self.last_refresh_label.setToolTip(
            "Time of the most recent successful statistics fetch.\n"
            "Stale (>5 s) text turns amber to flag a poll wedge."
        )
        button_layout.addWidget(self.last_refresh_label)

        layout.addLayout(button_layout, 0)  # stretch=0: never grows

        self.statistics_group.setLayout(layout)
        # NOTE: parent layout placement happens in main.py (currently a QDockWidget;
        # historically was self.splitter.addWidget). Don't attach here — main owns
        # the layout decision so we can switch container types without touching
        # this file again.

        # Initialize worker for background statistics fetching
        self._stats_worker = None
        self._poll_worker = None
        self._pending_stats_data = {}  # Store data from worker before processing
        self._pending_stream_stats = []  # Store stream stats from worker
        self._pending_poll_stream_stats = []  # Store poll stream stats from worker
    
    def fetch_and_update_statistics(self):
        """Fetch traffic statistics from all servers and display for selected ones."""
        if not self.server_interfaces:
            logger.info("No servers available. Wiping traffic statistics.")
            # Hard-reset, not soft clear — there are no servers, so the
            # column structure ("TG 0 - eth0", ...) should go too.
            # clear_statistics_table only zeros the cells; it deliberately
            # preserves columns for the 'Clear Stats' button flow.
            if hasattr(self, "reset_statistics_table_structure"):
                self.reset_statistics_table_structure()
            else:
                self.clear_statistics_table()
            # Also drop the persistent fallback cache so a subsequent
            # add-server doesn't re-display the old columns from
            # _last_statistics on first paint.
            self._last_statistics = {}
            return
        
        # If worker is already running, skip this call to prevent overlapping
        if self._stats_worker and self._stats_worker.isRunning():
            return
        
        # Start background worker to fetch data
        online_servers = [s for s in self.server_interfaces if s.get("online", True)]
        if not online_servers:
            return
        
        self._stats_worker = StatisticsFetchWorker(
            online_servers,
            fetch_type="both",
            connection_manager=getattr(self, 'connection_manager', None)
        )
        # CRITICAL: Qt-parent ownership. Without this, the next polling
        # cycle's `self._stats_worker = StatisticsFetchWorker(...)` drops
        # the only Python ref to the PREVIOUS worker. If that previous
        # worker's run() just returned but Qt's internal post-run cleanup
        # is still in flight, Python's GC destroys the C++ QThread and
        # the destructor sees isRunning() still true → "QThread:
        # Destroyed while thread is still running" → SIGABRT. The
        # isRunning() check above guards the common case but races on
        # the run-just-returned window. setParent moves ownership to Qt
        # entirely; Python GC of the wrapper is a no-op and Qt's
        # deleteLater (via finished→_on_stats_fetch_finished) handles
        # destruction cleanly on the event loop.
        # Permanent keepalive — see _keepalive_worker in menu_actions.py.
        # setParent + deleteLater proved unreliable on PyQt5 5.15.11 +
        # Python 3.14; a permanent ref (trimmed >30s after finish) is the
        # only thing that reliably dodges the destructor race.
        if hasattr(self, "_keepalive_worker"):
            self._keepalive_worker(self._stats_worker)
        self._stats_worker.interfaces_fetched.connect(self._on_interfaces_fetched)
        self._stats_worker.stream_stats_fetched.connect(self._on_stream_stats_fetched)
        self._stats_worker.fetch_error.connect(self._on_fetch_error)
        self._stats_worker.finished.connect(self._on_stats_fetch_finished)

        # Reset pending data
        self._pending_stats_data = {}
        self._pending_stream_stats = []

        self._stats_worker.start()
    
    def reset_statistics_table_structure(self):
        """Hard-reset: wipe columns + headers from both stats tables.

        Distinct from clear_statistics_table (soft-clear used by the
        'Clear Stats' button), which deliberately preserves the
        per-interface column structure so the user doesn't see the
        table briefly empty between Clear Stats and the next poll.

        Use this when the columns *should* go away — TGen removed,
        all servers offline, fresh session start. Without this, the
        column headers ("TG 0 - ens5np0", ...) keep showing labels
        for chassis that no longer exist.
        """
        try:
            if hasattr(self, "statistics_table") and self.statistics_table is not None:
                self.statistics_table.setColumnCount(0)
                self.statistics_table.setHorizontalHeaderLabels([])
        except Exception as e:
            logger.debug(f"[RESET STATS] interface table reset failed: {e}")
        try:
            if hasattr(self, "stream_statistics_table") and self.stream_statistics_table is not None:
                self.stream_statistics_table.setRowCount(0)
        except Exception as e:
            logger.debug(f"[RESET STATS] stream table reset failed: {e}")
        try:
            chart = getattr(self, "live_throughput_chart", None)
            if chart is not None and hasattr(chart, "clear_samples"):
                chart.clear_samples()
        except Exception as e:
            logger.debug(f"[RESET STATS] chart clear failed: {e}")

    def prune_server_stats(self, server_address, tg_id):
        """Drop every cache + table cell belonging to a removed TGen.

        Called from menu_actions.remove_selected_server() so the stats
        view doesn't keep showing rows for a chassis the operator just
        removed. Without this, removed-server interfaces linger in the
        Interface Statistics + Stream Statistics tables for up to one
        full refresh cycle (and forever in the chart) because:

          1. `_pending_stats_data` is keyed by server_address — gets
             reset on the *next* fetch, not the current one.
          2. `_last_statistics` is the fallback cache used when the
             current poll returns empty for an interface — it would
             keep re-displaying the removed TG's rows indefinitely.
          3. `_iface_baselines` / `_stream_baselines` would skew Clear
             Stats math if the same TG iface name is re-added later
             on a different chassis.

        Best-effort — silently no-ops on attributes that don't exist
        on this MainWindow instance (some are lazily initialized).
        """
        tg_prefix = f"TG {tg_id} - " if tg_id is not None else None

        # 1. Pending fetch buffers
        try:
            self._pending_stats_data.pop(server_address, None)
        except (AttributeError, TypeError):
            pass
        try:
            self._pending_stream_stats = [
                s for s in getattr(self, "_pending_stream_stats", [])
                if str(s.get("_tg_id", "")) != str(tg_id)
            ]
        except (AttributeError, TypeError):
            pass
        try:
            self._pending_poll_stream_stats = [
                s for s in getattr(self, "_pending_poll_stream_stats", [])
                if str(s.get("_tg_id", "")) != str(tg_id)
            ]
        except (AttributeError, TypeError):
            pass

        # 2. Persistent display caches keyed by "TG <id> - <iface>"
        if tg_prefix is not None:
            for cache_attr in ("_last_statistics", "_iface_baselines",
                               "_stream_baselines", "_latched_loss_pct"):
                try:
                    cache = getattr(self, cache_attr, None)
                    if isinstance(cache, dict):
                        stale = [k for k in cache if isinstance(k, str) and k.startswith(tg_prefix)]
                        for k in stale:
                            cache.pop(k, None)
                except Exception:
                    pass

        # 3. self.streams is the source-of-truth dict for the Stream
        #    configuration table on the main page. It's keyed by port
        #    labels like "TG 1 - eth0" AND "TG 1 - Port: eth0" (two
        #    historical formats coexist). Without pruning here, removed-
        #    TG streams keep rendering in the stream table for the rest
        #    of the session.
        if tg_prefix is not None:
            try:
                streams = getattr(self, "streams", None)
                if isinstance(streams, dict):
                    # Match both "TG 1 - " and "TG 1 - Port: " forms
                    bare_prefix = f"TG {tg_id} - "
                    port_prefix = f"TG {tg_id} - Port: "
                    stale_ports = [
                        k for k in streams
                        if isinstance(k, str) and (k.startswith(bare_prefix) or k.startswith(port_prefix))
                    ]
                    for k in stale_ports:
                        streams.pop(k, None)
                    if stale_ports:
                        logger.info(
                            f"[STATS PRUNE] Dropped {len(stale_ports)} stream config entries for TG {tg_id}"
                        )
            except Exception as e:
                logger.debug(f"[STATS PRUNE] self.streams cleanup failed: {e}")

        # 4. Live throughput chart — drop iface lines so they vanish
        #    immediately rather than scrolling out over WINDOW_SEC.
        if tg_prefix is not None:
            try:
                chart = getattr(self, "live_throughput_chart", None)
                if chart is not None and hasattr(chart, "remove_iface_by_prefix"):
                    chart.remove_iface_by_prefix(tg_prefix)
            except Exception as e:
                logger.debug(f"[STATS PRUNE] chart prune failed: {e}")

        # 5. Force an immediate redraw with the pruned caches so the
        #    operator sees the rows disappear instantly (not 2-5 s
        #    later when the next refresh tick fires).
        #
        # Key subtlety: clear_statistics_table is a SOFT clear (zeros
        # cells, keeps columns) intended for the 'Clear Stats' button.
        # When pruning removes the last TG, we want a HARD reset that
        # actually wipes the column structure too — otherwise the
        # column headers "TG 0 - ens5np0" etc. linger forever showing
        # chassis that no longer exist. Use reset_statistics_table_structure
        # for that path instead.
        try:
            remaining_servers = getattr(self, "server_interfaces", None) or []
            if remaining_servers and hasattr(self, "_last_statistics") and self._last_statistics:
                self.update_statistics_table(self._last_statistics)
            else:
                self.reset_statistics_table_structure()
        except Exception:
            pass
        try:
            self.update_stream_statistics_table(
                getattr(self, "_pending_poll_stream_stats", [])
                or getattr(self, "_pending_stream_stats", [])
            )
        except Exception:
            pass
        # Refresh the main Streams config table so removed-TG rows disappear
        try:
            if hasattr(self, "_do_update_stream_table"):
                if hasattr(self, "_populating_table"):
                    self._populating_table = False
                self._do_update_stream_table()
            elif hasattr(self, "update_stream_table"):
                self.update_stream_table()
        except Exception as e:
            logger.debug(f"[STATS PRUNE] stream table refresh failed: {e}")

        logger.info(
            f"[STATS PRUNE] Removed TG {tg_id} ({server_address}) from stats caches + tables"
        )

    def _on_interfaces_fetched(self, server, data):
        """Handle interfaces data fetched from background worker."""
        interfaces = data.get("interfaces", [])
        tg_id = server.get("tg_id")
        server_address = server.get("address")
        
        server["online"] = True
        if server in self.failed_servers:
            self.failed_servers.remove(server)
        self.update_server_status_icon(server, True)
        
        # Store interfaces data for processing (use server address as key since dicts are unhashable)
        if server_address not in self._pending_stats_data:
            self._pending_stats_data[server_address] = {"server": server, "interfaces": [], "streams": []}
        self._pending_stats_data[server_address]["interfaces"] = interfaces
    
    def _on_stream_stats_fetched(self, server, stream_stats):
        """Handle stream stats fetched from background worker."""
        tg_id = server.get("tg_id")
        server_address = server.get("address")
        
        # Add TG ID to each stream
        for stream in stream_stats:
            stream["_tg_id"] = tg_id
        
        # Store stream stats for processing (use server address as key since dicts are unhashable)
        if server_address not in self._pending_stats_data:
            self._pending_stats_data[server_address] = {"server": server, "interfaces": [], "streams": []}
        self._pending_stats_data[server_address]["streams"] = stream_stats
        self._pending_stream_stats.extend(stream_stats)
    
    def _on_fetch_error(self, server, error_message):
        """Handle fetch error from background worker."""
        server_address = server.get("address")
        logger.error(f"Stats fetch failed for {server_address}: {error_message}")
        server["online"] = False
        self.update_server_status_icon(server, False)
        if server not in self.failed_servers:
            self.failed_servers.append(server)
    
    def _on_stats_fetch_finished(self):
        """Process all fetched data and update UI when worker finishes."""
        merged_statistics = {}
        all_stream_stats = []

        # Step 1: Process interfaces data from worker
        for server_address, data in self._pending_stats_data.items():
            server = data.get("server")
            tg_id = server.get("tg_id") if server else None
            interfaces = data.get("interfaces", [])
            
            for interface in interfaces:
                iface_name = f"TG {tg_id} - {interface['name']}"
                if iface_name in self.removed_interfaces:
                    continue

                merged_statistics[iface_name] = {
                    "status": interface.get("status", "N/A"),
                    "tx": 0,
                    "rx": 0,
                    "sent_bytes": 0,
                    "received_bytes": 0,
                    "send_fps": 0,
                    "receive_fps": 0,
                    "send_bps": 0,
                    "receive_bps": 0,
                    "errors": interface.get("errors", 0),
                    # v0.5.144: iface PHY counters from the server
                    # (post-v0.5.135 these are tx_packets_phy /
                    # rx_packets_phy on Mellanox NICs — wire-truth,
                    # not undercounted-by-sniffer). The loss row
                    # renderer pairs these across the streams'
                    # tx_iface ↔ rx_iface and displays a single
                    # pair-loss number on BOTH halves.
                    "phy_tx": int(interface.get("tx", 0) or 0),
                    "phy_rx": int(interface.get("rx", 0) or 0),
                    "peer_ifaces": set(),
                    # v0.5.139: per-iface cumulative loss aggregation.
                    # Retained for back-compat (other callers may
                    # still inspect them), but no longer used by the
                    # iface loss renderer — v0.5.144 uses phy_tx/rx +
                    # peer pairing instead.
                    "tx_for_loss": 0,
                    "rx_for_loss": 0,
                    "streams": {}
                }
        
        # Step 2: Process stream stats from worker
        for server_address, data in self._pending_stats_data.items():
            server = data.get("server")
            tg_id = server.get("tg_id") if server else None
            stream_stats = data.get("streams", [])
            
            logger.debug(f"[DEBUG STREAM STATS] Got {len(stream_stats)} stream(s) from {server_address}")
            # Update stream objects with latest statistics
            self.update_per_stream_statistics(stream_stats)
            all_stream_stats.extend(stream_stats)
            
            # Process stream statistics for merged_statistics
            for stream in stream_stats:
                tx_port = stream.get("interface")
                rx_port_raw = stream.get("rx_interface") or stream.get("rx_port")
                stream_name = stream.get("stream_name", "Unnamed")
                tx = stream.get("tx_count", 0)
                rx = stream.get("rx_count", 0)
                stream_id = stream.get("stream_id")
                flow_tracking = stream.get("flow_tracking_enabled", False)

                tx_iface = f"TG {tg_id} - {tx_port}"
                rx_port_clean = rx_port_raw.split(":")[-1].strip() if rx_port_raw else None
                rx_iface = f"TG {tg_id} - {rx_port_clean}" if rx_port_clean else None

                # v0.5.144: record the pair so the loss renderer can
                # ground-truth iface loss against the peer's PHY RX,
                # not the stream's undercounted rx_count.
                if (rx_iface
                        and tx_iface in merged_statistics
                        and rx_iface in merged_statistics):
                    merged_statistics[tx_iface]["peer_ifaces"].add(rx_iface)
                    merged_statistics[rx_iface]["peer_ifaces"].add(tx_iface)

                # The server reports per-stream tx_rate / rx_rate already in
                # frames-per-second (delta-based, computed from successive
                # tracker reads). Use those for the rate columns; the prior
                # code used `tx_count // 10` and `tx_count * size * 8` which
                # are cumulative quantities, not rates — that produced
                # impossible values like "561 Gbps" on a 400G link.
                def _f(v, default=0.0):
                    try:
                        return float(v) if v is not None else default
                    except (ValueError, TypeError):
                        return default

                tx_rate = _f(stream.get("tx_rate"))  # fps
                rx_rate = _f(stream.get("rx_rate"))  # fps

                # TX aggregation
                if tx_iface in merged_statistics:
                    stream_entry = merged_statistics[tx_iface]["streams"].setdefault(stream_name, {})
                    stream_entry["tx_count"] = tx
                    stream_entry["rx_count"] = rx if flow_tracking else None
                    stream_entry["stream_id"] = stream_id
                    stream_entry["flow_tracking_enabled"] = flow_tracking

                    frame_size = stream.get("frame_size", 64)
                    try:
                        frame_size = int(frame_size)
                    except (ValueError, TypeError):
                        frame_size = 64

                    merged_statistics[tx_iface]["tx"] += tx
                    merged_statistics[tx_iface]["sent_bytes"] += tx * frame_size
                    # Rates: use server's delta-computed tx_rate (fps) and
                    # derive bps from it. L2 frame bits = bytes * 8.
                    merged_statistics[tx_iface]["send_fps"] += tx_rate
                    merged_statistics[tx_iface]["send_bps"] += tx_rate * frame_size * 8

                    # v0.5.139: contribute this stream's TX/RX counts to
                    # the iface's loss totals. The TX side knows what was
                    # sent; we pair it with the same stream's rx at the
                    # rx_iface block below. The cumulative deltas persist
                    # after stream stop.
                    merged_statistics[tx_iface]["tx_for_loss"] += tx
                    if flow_tracking and isinstance(rx, int):
                        merged_statistics[tx_iface]["rx_for_loss"] += rx

                    # v0.4.6: DO NOT add rx_count / rx_rate into the
                    # TX-interface bucket. The pre-fix block did that
                    # under `if flow_tracking:` — operator-reported on
                    # svl-d-ai-srv04 that the TX iface's Received Frames /
                    # Receive Frame Rate columns ended up mirroring the
                    # RX iface (both columns showed identical 3,836 /
                    # 397.36 fps for a stream where TX iface =
                    # enp160s0f0np0 and RX iface = enp181s0f0np0).
                    # The TX iface did not actually receive those
                    # packets — the RX iface did. The RX-aggregation
                    # block below correctly attributes them.
                    # If tx_iface == rx_iface (loopback / single-port
                    # test), the RX-aggregation block runs against the
                    # same dict and adds RX exactly once — no
                    # double-count.

                # RX aggregation — the only place rx / received_bytes /
                # receive_fps / receive_bps get added.
                if rx_iface and rx_iface in merged_statistics:
                    frame_size = stream.get("frame_size", 64)
                    try:
                        frame_size = int(frame_size)
                    except (ValueError, TypeError):
                        frame_size = 64

                    merged_statistics[rx_iface]["rx"] += rx
                    merged_statistics[rx_iface]["received_bytes"] += rx * frame_size
                    merged_statistics[rx_iface]["receive_fps"] += rx_rate
                    merged_statistics[rx_iface]["receive_bps"] += rx_rate * frame_size * 8

                    # v0.5.139: same loss totals on the RX-iface column so
                    # operators see the SAME "lost N packets" number under
                    # both ifaces of a back-to-back pair (the streams sent
                    # X, the peer received Y → lost X-Y, both sides agree).
                    merged_statistics[rx_iface]["tx_for_loss"] += tx
                    if flow_tracking and isinstance(rx, int):
                        merged_statistics[rx_iface]["rx_for_loss"] += rx

                    stream_entry = merged_statistics[rx_iface]["streams"].setdefault(stream_name, {})
                    stream_entry["rx_count"] = rx
                    stream_entry["stream_id"] = stream_id
                    stream_entry["flow_tracking_enabled"] = flow_tracking

        # Step 3: Filter by selected TGs (from checkboxes) AND currently selected TG in tree
        selected_tg_ids = {f"TG {s['tg_id']}" for s in self.selected_servers}
        
        # Also include TG from currently selected item in server tree
        if hasattr(self, "server_tree"):
            selected_items = self.server_tree.selectedItems()
            if selected_items:
                selected_item = selected_items[0]
                # If a port is selected, get the parent (TG server)
                server_item = selected_item.parent() if selected_item.parent() else selected_item
                
                # Try to get TG ID from the server item widget (same pattern as elsewhere in codebase)
                tg_widget = self.server_tree.itemWidget(server_item, 0)
                tg_id = None
                if tg_widget:
                    # Find all QLabel children and get the one with TG ID text (not the status icon)
                    labels = tg_widget.findChildren(QLabel)
                    for label in labels:
                        label_text = label.text()
                        if label_text and label_text.startswith("TG "):
                            tg_id = label_text.replace("TG ", "").strip()
                            break
                
                # Fallback: try to get from server_interfaces by matching address or index
                if not tg_id:
                    server_address = server_item.text(1)
                    if server_address:
                        for server in self.server_interfaces:
                            if server.get("address") == server_address:
                                tg_id = str(server.get("tg_id", ""))
                                break
                    # Last resort: use index
                    if not tg_id:
                        parent_index = self.server_tree.indexOfTopLevelItem(server_item)
                        if parent_index >= 0 and parent_index < len(self.server_interfaces):
                            tg_id = str(self.server_interfaces[parent_index].get("tg_id", ""))
                
                if tg_id:
                    selected_tg_ids.add(f"TG {tg_id}")
        
        filtered_statistics = {
            iface: stats for iface, stats in merged_statistics.items()
            if iface.split(" - ")[0] in selected_tg_ids
        }

        if filtered_statistics:
            for iface, stats in filtered_statistics.items():
                prev = self._last_statistics.get(iface, {}) if hasattr(self, "_last_statistics") else {}

                # Cumulative-counter preservation only. The kernel's tx_packets,
                # rx_packets, tx_bytes, rx_bytes monotonically grow while the
                # interface is up — if a single fetch happens to return 0
                # (server momentarily slow, partial response), reusing the
                # previous value avoids flicker. We do NOT preserve the four
                # *_fps / *_bps rate columns: rates are instantaneous, and the
                # natural value when traffic isn't flowing IS 0. Preserving
                # them was the bug that left "32 Mfps / 395 Gbps" visible
                # after the user clicked Stop.
                for key in ("tx", "rx", "sent_bytes", "received_bytes",
                           # v0.5.144: PHY counters drive the loss
                           # row — same monotonic invariant, same
                           # flicker-protection treatment.
                           "phy_tx", "phy_rx"):
                    if stats.get(key, 0) == 0 and prev.get(key, 0) > 0:
                        stats[key] = prev[key]

                # Merge previous stream stats
                prev_streams = prev.get("streams", {})
                if not stats["streams"]:
                    stats["streams"] = prev_streams.copy()
                else:
                    for sname, sdata in prev_streams.items():
                        if sname not in stats["streams"]:
                            stats["streams"][sname] = sdata

            self.update_statistics_table(filtered_statistics)
            self._last_statistics = filtered_statistics.copy()
            # Push a sample to the live chart — one bps value per interface.
            self._push_chart_sample(filtered_statistics)
        elif hasattr(self, "_last_statistics") and self._last_statistics:
            # Only update if we have meaningful statistics to display
            # Skip the "No new statistics" message to reduce console spam.
            #
            # Also: if all known TGens have been removed (server_interfaces
            # empty), don't render the stale _last_statistics — that was
            # what caused removed-chassis columns to linger forever
            # ("TG 0 - ens5np0" still visible after the last TG was
            # removed). Hard-reset the table structure instead.
            remaining = getattr(self, "server_interfaces", None) or []
            if not remaining:
                if hasattr(self, "reset_statistics_table_structure"):
                    self.reset_statistics_table_structure()
                else:
                    self.clear_statistics_table()
                self._last_statistics = {}
            else:
                self.update_statistics_table(self._last_statistics)
                self._push_chart_sample(self._last_statistics)
        else:
            if hasattr(self, "reset_statistics_table_structure"):
                self.reset_statistics_table_structure()
            else:
                self.clear_statistics_table()
        
        # Always update stream statistics table with all collected streams (even if empty, to clear table)
        logger.debug(f"[DEBUG STREAM STATS] Calling update_stream_statistics_table with {len(all_stream_stats)} stream(s)")
        self.update_stream_statistics_table(all_stream_stats)
        
        # Periodic refresh of the Streams table's Status column. The
        # ONLY thing on this table that changes due to a stats poll is
        # the col-0 Status icon — everything else is configuration.
        # Previously this called _do_update_stream_table() (full
        # setRowCount(0) + re-setItem of every cell), which interacted
        # badly with selection / inline-editing / display-derived key
        # lookups and produced the regression cascade fixed in
        # 0.2.51/.53/.54/.55. Now we surgically repaint just the rows
        # whose status changed, identified by stream_id — no rebuild,
        # no key reconstruction. Structural changes (add / edit /
        # remove / apply / start / stop) still call
        # _do_update_stream_table from their own code paths.
        try:
            if hasattr(self, "_refresh_stream_status_in_place"):
                self._refresh_stream_status_in_place()
            elif hasattr(self, "update_stream_table"):
                # Defensive fallback only if the in-place method is
                # missing for some reason (mixin not loaded).
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(10, lambda: self.update_stream_table())
        except Exception as e:
            logger.error(f"[STATS ERROR] Failed to refresh stream status: {e}")

        offline_servers = [s for s in self.server_interfaces if s.get("online") is False]
        # Reduced debug output to prevent UI spam
        # print(f"[MENU DEBUG] Total servers: {len(self.server_interfaces)}")
        # print(f"[MENU DEBUG] Offline servers: {len(offline_servers)}")
        # print(f"[MENU DEBUG] Failed servers: {len(self.failed_servers)}")
        
        if offline_servers:
            # print(f"[MENU DEBUG] Found {len(offline_servers)} offline servers, enabling 'Make Server Online' menu")
            self.enable_make_server_online_menu()
        elif hasattr(self, 'make_server_online_action'):
            # print(f"[MENU DEBUG] All servers online, disabling 'Make Server Online' menu")
            self.make_server_online_action.setEnabled(False)
    
    def poll_stream_stats(self):
        # If worker is already running, skip this call to prevent overlapping
        if self._poll_worker and self._poll_worker.isRunning():
            return
        
        # Always poll from all online servers to get latest statistics
        servers_to_poll = []
        if hasattr(self, "server_interfaces"):
            servers_to_poll = [s for s in self.server_interfaces if s.get("online", True)]
        
        # Also include selected servers (checkboxes) if any
        if hasattr(self, "selected_servers") and self.selected_servers:
            for server in self.selected_servers:
                if server not in servers_to_poll:
                    servers_to_poll.append(server)
        
        # Also include TG from currently selected item in server tree
        if hasattr(self, "server_tree"):
            selected_items = self.server_tree.selectedItems()
            if selected_items:
                selected_item = selected_items[0]
                server_item = selected_item.parent() if selected_item.parent() else selected_item
                server_address = server_item.text(1)
                if server_address:
                    for server in self.server_interfaces:
                        if server.get("address") == server_address and server not in servers_to_poll:
                            servers_to_poll.append(server)
                            break
        
        if not servers_to_poll:
            if hasattr(self, "update_stream_table"):
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.update_stream_table())
            return
        
        # Start background worker to fetch stream stats
        self._poll_worker = StatisticsFetchWorker(
            servers_to_poll,
            fetch_type="streams",
            connection_manager=getattr(self, 'connection_manager', None)
        )
        # Permanent keepalive — see _keepalive_worker in menu_actions.py.
        if hasattr(self, "_keepalive_worker"):
            self._keepalive_worker(self._poll_worker)
        self._poll_worker.stream_stats_fetched.connect(self._on_poll_stream_stats_fetched)
        self._poll_worker.fetch_error.connect(self._on_poll_fetch_error)
        self._poll_worker.finished.connect(self._on_poll_finished)

        # Reset pending data
        self._pending_poll_stream_stats = []

        self._poll_worker.start()
    
    def _on_poll_stream_stats_fetched(self, server, stream_stats):
        """Handle stream stats fetched from poll worker."""
        tg_id = server.get("tg_id")
        for stream in stream_stats:
            stream["_tg_id"] = tg_id
        self._pending_poll_stream_stats.extend(stream_stats)
        self.update_per_stream_statistics(stream_stats)
    
    def _on_poll_fetch_error(self, server, error_message):
        """Handle poll fetch error."""
        # Just log, don't mark server offline for poll errors
        pass
    
    def _on_poll_finished(self):
        """Process polled stream stats and update UI when worker finishes."""
        # Update stream statistics table
        logger.debug(f"[DEBUG STREAM STATS POLL] Calling update_stream_statistics_table with {len(self._pending_poll_stream_stats)} stream(s)")
        self.update_stream_statistics_table(self._pending_poll_stream_stats)
    def update_per_stream_statistics(self, stream_stats):
        # print(f"[DEBUG] update_per_stream_statistics() called with {len(stream_stats)} entries")

        stat_map = {entry.get("stream_id"): entry for entry in stream_stats if entry.get("stream_id")}
        
        # Track if any stream status changed
        status_changed = False

        for row in range(self.stream_table.rowCount()):
            stream_name_item = self.stream_table.item(row, 2)
            interface_item = self.stream_table.item(row, 1)

            if not stream_name_item or not interface_item:
                continue

            stream_name = stream_name_item.text().strip()
            interface = interface_item.text().strip()
            # Read the stream_id stash up front — used both for the iface
            # resolution below and for the per-stream match further down.
            stream_id_from_table = stream_name_item.data(Qt.UserRole)

            # Resolve the row to a streams[] key. The Streams table collapses
            # multiple streams sharing one port into a header row + "↳"
            # continuation rows; the continuation row's text is literally
            # "↳" with the real iface in the cell tooltip. This poll tick
            # was spamming "No match found for interface '↳'" every 2s on
            # any port with >1 stream until we taught it the layout.
            #
            # Three-tier resolution, same pattern as Edit/Remove:
            #   1. stream_id stashed on the name cell → walk self.streams
            #      to find the owning port.
            #   2. Continuation row → read iface from tooltip.
            #   3. Header row → match the visible text.
            matched_iface = None
            if stream_id_from_table:
                for p, lst in self.streams.items():
                    if any(s.get("stream_id") == stream_id_from_table for s in lst):
                        matched_iface = p
                        break

            if not matched_iface:
                # Continuation rows hide the iface in the tooltip.
                resolve_text = interface
                if resolve_text == "↳":
                    resolve_text = (interface_item.toolTip() or "").strip()

                if resolve_text in self.streams:
                    matched_iface = resolve_text
                else:
                    base_interface = resolve_text.split('.')[0] if '.' in resolve_text else resolve_text
                    for k in self.streams:
                        port_key_normalized = k.replace("Port: ", "").split(" - ")[-1] if " - " in k else k
                        if port_key_normalized == base_interface or port_key_normalized == resolve_text:
                            matched_iface = k
                            break
                        if base_interface and (base_interface in port_key_normalized or port_key_normalized in base_interface):
                            matched_iface = k
                            break

            if not matched_iface:
                logger.info(f"[UPDATE STATS] No match found for interface '{interface}' in streams (available keys: {list(self.streams.keys())[:3]}...)")
                continue

            matched_streams = self.streams.get(matched_iface, [])

            # stream_id_from_table was already read above for the iface
            # resolution; reuse it here for the per-stream match.
            for stream in matched_streams:
                # Match by stream_id first (most reliable), then fall back to name
                stream_id = stream.get("stream_id")
                stream_name_match = stream.get("name") == stream_name or stream.get("protocol_selection", {}).get("name") == stream_name
                
                # Only update if stream_id matches OR (if no stream_id in table, match by name)
                if stream_id_from_table:
                    # If table has stream_id, use it for matching (most reliable)
                    if stream_id != stream_id_from_table:
                        continue
                elif not stream_name_match:
                    # If no stream_id in table, match by name (fallback)
                    continue
                
                old_status = stream.get("status", "stopped")
                
                # Get status from server response (this is the source of truth)
                server_status = None
                if stream_id and stream_id in stat_map:
                    stat_entry = stat_map[stream_id]
                    server_status = stat_entry.get("status", "Unknown").lower()
                    # Update stream object with latest statistics
                    stream["tx_count"] = stat_entry.get("tx_count", 0)
                    stream["rx_count"] = stat_entry.get("rx_count", 0)
                    stream["tx_rate"] = stat_entry.get("tx_rate", 0.0)
                    stream["rx_rate"] = stat_entry.get("rx_rate", 0.0)
                elif stream_id_from_table and stream_id_from_table in stat_map:
                    # If stream_id from table matches, use that
                    stat_entry = stat_map[stream_id_from_table]
                    server_status = stat_entry.get("status", "Unknown").lower()
                    stream["tx_count"] = stat_entry.get("tx_count", 0)
                    stream["rx_count"] = stat_entry.get("rx_count", 0)
                    stream["tx_rate"] = stat_entry.get("tx_rate", 0.0)
                    stream["rx_rate"] = stat_entry.get("rx_rate", 0.0)
                
                # Determine status based on server's status field (not just presence in stat_map)
                if server_status == "running":
                    new_status = "running"
                    stream["status"] = new_status
                    self.update_stream_status(row, "green")
                elif server_status == "stopped":
                    new_status = "stopped"
                    stream["status"] = new_status
                    # Zero out rates for stopped streams
                    stream["tx_rate"] = 0.0
                    stream["rx_rate"] = 0.0
                    self.update_stream_status(row, "red")
                elif (stream_id and stream_id in stat_map) or (stream_id_from_table and stream_id_from_table in stat_map):
                    # Fallback: if status not provided but stream is in stats, assume running
                    new_status = "running"
                    stream["status"] = new_status
                    self.update_stream_status(row, "green")
                else:
                    # Stream not in stats at all - definitely stopped
                    new_status = "stopped"
                    stream["status"] = new_status
                    stream["tx_rate"] = 0.0
                    stream["rx_rate"] = 0.0
                    self.update_stream_status(row, "red")
                
                if old_status != new_status:
                    status_changed = True
                break  # Only update the first matching stream
        
        # If status changed, refresh the entire stream table to show updated information
        if status_changed and hasattr(self, "update_stream_table"):
            # Use QTimer to avoid blocking
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.update_stream_table())

    def _push_chart_sample(self, statistics):
        """Feed the live throughput chart from a merged-statistics dict.

        For each interface, push its current send_bps. The chart widget
        slides a 60s window and auto-scales Y, so just hand it raw bps.
        """
        chart = getattr(self, "live_throughput_chart", None)
        if chart is None or not isinstance(statistics, dict):
            return
        try:
            iface_to_bps = {}
            for iface_name, stats in statistics.items():
                if not isinstance(stats, dict):
                    continue
                # Only chart interfaces that actually have traffic flowing.
                # Avoids cluttering the legend with idle ports.
                bps = float(stats.get("send_bps") or 0.0)
                if bps > 0 or iface_name in chart._samples_iface_history():
                    iface_to_bps[iface_name] = bps
            chart.add_sample(iface_to_bps)
        except Exception as e:
            logger.debug(f"[CHART] sample push failed: {e}")

    def update_statistics_table(self, statistics):
        """Update the traffic statistics table with per-interface and per-stream stats."""
        # v0.2.99: pause-refresh gate. Same flag as the stream-stats
        # path checks; freezes both tables together so a screenshot
        # gets a consistent snapshot.
        if getattr(self, "_refresh_paused", False):
            return
        self.statistics_table.clearContents()

        def format_number(num):
            """Format number with commas for readability."""
            try:
                return f"{int(num):,}"
            except (ValueError, TypeError):
                return str(num)
        
        def format_bytes(bytes_val):
            """Format bytes with appropriate unit."""
            try:
                bytes_val = int(bytes_val)
                if bytes_val >= 1_000_000_000:
                    return f"{bytes_val / 1_000_000_000:.2f} GB"
                elif bytes_val >= 1_000_000:
                    return f"{bytes_val / 1_000_000:.2f} MB"
                elif bytes_val >= 1_000:
                    return f"{bytes_val / 1_000:.2f} KB"
                else:
                    return f"{bytes_val} B"
            except (ValueError, TypeError):
                return str(bytes_val)
        
        def format_rate(rate_val, unit="fps"):
            """Format rate with appropriate unit."""
            try:
                rate_val = float(rate_val)
                if unit == "bps":
                    if rate_val >= 1_000_000_000:
                        return f"{rate_val / 1_000_000_000:.2f} Gbps"
                    elif rate_val >= 1_000_000:
                        return f"{rate_val / 1_000_000:.2f} Mbps"
                    elif rate_val >= 1_000:
                        return f"{rate_val / 1_000:.2f} Kbps"
                    else:
                        return f"{rate_val:.2f} bps"
                else:  # fps
                    if rate_val >= 1_000_000:
                        return f"{rate_val / 1_000_000:.2f} Mfps"
                    elif rate_val >= 1_000:
                        return f"{rate_val / 1_000:.2f} Kfps"
                    else:
                        return f"{rate_val:.2f} fps"
            except (ValueError, TypeError):
                return str(rate_val)

        base_rows = [
            "Status", "Sent Frames", "Received Frames", "Sent Bytes", "Received Bytes",
            "Send Frame Rate (fps)", "Receive Frame Rate (fps)", "Send Bit Rate (bps)",
            "Receive Bit Rate (bps)", "Errors", "Packets Lost", "Loss %"
        ]

        total_rows = len(base_rows)
        self.statistics_table.setRowCount(total_rows)
        self.statistics_table.setColumnCount(len(statistics))

        self.statistics_table.setVerticalHeaderLabels(base_rows)
        header_labels = list(statistics.keys())
        self.statistics_table.setHorizontalHeaderLabels(header_labels)

        # Make sure column headers don't clip at narrow pane widths.
        # Use a per-column min width derived from the header text, and expose the
        # full interface name as a hover tooltip on the header.
        # NOTE: QFontMetrics off the header widget reports the *widget* font,
        # which is smaller than the bold 12px CSS-applied font we actually
        # render. Build a metrics object with the rendered font (12pt bold)
        # so the calc isn't undersized — that's what was clipping
        # "enp181s0f0np0" to "p181s0f0np" after the visibility bump.
        from PyQt5.QtGui import QFontMetrics
        header_view = self.statistics_table.horizontalHeader()
        header_font = QFont(header_view.font())
        header_font.setPointSize(max(header_font.pointSize(), 12))
        header_font.setBold(True)
        fm = QFontMetrics(header_font)
        for col, label in enumerate(header_labels):
            header_item = self.statistics_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setToolTip(label)
            # +36 for left/right padding + sort indicator + breathing room
            # (was +24 — too tight for the 13px body / 12px bold header pair).
            min_width = fm.horizontalAdvance(label) + 36
            self.statistics_table.setColumnWidth(col, max(min_width, 140))

        # Per-interface baselines from the most recent Clear Stats click.
        # Subtract from cumulative columns so they appear to start from 0.
        iface_baselines = getattr(self, "_iface_baselines", {})

        def adjusted(iface_name, stats, key):
            try:
                v = int(stats.get(key, 0) or 0)
            except (ValueError, TypeError):
                v = 0
            base = iface_baselines.setdefault(iface_name, {})
            try:
                bv = int(base.get(key, 0) or 0)
            except (ValueError, TypeError):
                bv = 0
            # Counter-reset detection. The current cumulative can drop
            # below the captured baseline when:
            #   - the stream is stopped + restarted (tracker tx_count
            #     starts again from 0, sometimes from a different stream
            #     id but aggregated under the same iface);
            #   - the kernel netdev gets reset (interface flap);
            #   - the server restarts and re-derives counters.
            # When that happens, just plain "v - bv" stays negative
            # forever (clamped at 0) until the new cumulative grows
            # past the old baseline — which can take ages on a fresh
            # stream. Detect the reset and re-baseline to v so the
            # display starts counting up from 0 again.
            if v < bv:
                base[key] = v
                return 0
            return v - bv

        for col, (iface_name, stats) in enumerate(statistics.items()):
            # (0) Status - with color coding
            status = stats.get("status", "N/A")
            status_item = QTableWidgetItem(status)
            if status.lower() == "up":
                status_item.setForeground(QColor("#10b981"))  # Green
            elif status.lower() == "down":
                status_item.setForeground(QColor("#ef4444"))  # Red
            else:
                status_item.setForeground(QColor("#6b7280"))  # Gray
            status_item.setFont(QFont("", 12, QFont.Bold))
            self.statistics_table.setItem(0, col, status_item)

            # (1) Sent Frames (baseline-subtracted)
            tx_item = QTableWidgetItem(format_number(adjusted(iface_name, stats, "tx")))
            tx_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.statistics_table.setItem(1, col, tx_item)

            # (2) Received Frames (baseline-subtracted)
            rx_item = QTableWidgetItem(format_number(adjusted(iface_name, stats, "rx")))
            rx_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.statistics_table.setItem(2, col, rx_item)

            # (3) Sent Bytes (baseline-subtracted)
            sent_bytes_item = QTableWidgetItem(format_bytes(adjusted(iface_name, stats, "sent_bytes")))
            sent_bytes_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.statistics_table.setItem(3, col, sent_bytes_item)

            # (4) Received Bytes (baseline-subtracted)
            recv_bytes_item = QTableWidgetItem(format_bytes(adjusted(iface_name, stats, "received_bytes")))
            recv_bytes_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.statistics_table.setItem(4, col, recv_bytes_item)
            
            # The four rate rows are the operationally-critical ones —
            # bold them and tint TX values blue so live throughput jumps
            # off the page even at a glance from across the room.
            rate_send_color = QColor("#1d4ed8")  # blue for TX rates
            rate_recv_color = QColor("#111827")  # darker neutral for RX

            # (5) Send Frame Rate
            send_fps_item = QTableWidgetItem(format_rate(stats.get("send_fps", 0), "fps"))
            send_fps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            send_fps_item.setFont(QFont("Monaco, Consolas, monospace", 12, QFont.Bold))
            send_fps_item.setForeground(rate_send_color)
            self.statistics_table.setItem(5, col, send_fps_item)

            # (6) Receive Frame Rate
            recv_fps_item = QTableWidgetItem(format_rate(stats.get("receive_fps", 0), "fps"))
            recv_fps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            recv_fps_item.setForeground(rate_recv_color)
            self.statistics_table.setItem(6, col, recv_fps_item)

            # (7) Send Bit Rate
            send_bps_item = QTableWidgetItem(format_rate(stats.get("send_bps", 0), "bps"))
            send_bps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            send_bps_item.setFont(QFont("Monaco, Consolas, monospace", 12, QFont.Bold))
            send_bps_item.setForeground(rate_send_color)
            self.statistics_table.setItem(7, col, send_bps_item)

            # (8) Receive Bit Rate
            recv_bps_item = QTableWidgetItem(format_rate(stats.get("receive_bps", 0), "bps"))
            recv_bps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            recv_bps_item.setForeground(rate_recv_color)
            self.statistics_table.setItem(8, col, recv_bps_item)
            
            # (9) Errors (baseline-subtracted) - with color coding
            errors = adjusted(iface_name, stats, "errors")
            errors_item = QTableWidgetItem(format_number(errors))
            errors_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if errors > 0:
                errors_item.setForeground(QColor("#ef4444"))  # Red for errors
            self.statistics_table.setItem(9, col, errors_item)

            # (10) Packets Lost — v0.5.144 rewrite: ground-truth iface
            # loss on PHY counters + cross-iface pair detection.
            #
            # v0.5.139 used `tx_for_loss / rx_for_loss` aggregated
            # from per-stream tx_count / rx_count. That's wrong: the
            # stream's rx_count comes from the RX engine (scapy
            # sniffer / DPDK rx_worker) which DROPS under line-rate
            # blast — operator reported 824M "lost" out of 830M when
            # the wire actually delivered ~820M. The iface PHY
            # counters (post-v0.5.135) see real frames on the wire.
            #
            # The pair logic: each iface knows its peer ifaces (built
            # in stream loop above from tx_iface ↔ rx_iface). Loss
            # on a pair = max(self.phy_tx, peer.phy_tx) - max(self.phy_rx,
            # peer.phy_rx). Same number on both halves.
            own_phy_tx = stats.get("phy_tx", 0)
            own_phy_rx = stats.get("phy_rx", 0)
            peers = stats.get("peer_ifaces") or set()

            # v0.5.145 hotfix: peer lookup uses the renderer's input
            # dict (`statistics`), not `filtered_statistics` /
            # `merged_statistics`. Those names live in
            # `_on_stats_fetch_finished` — they're out of scope here
            # (NameError on cold start, operator-reported).
            peer_phy_tx = 0
            peer_phy_rx = 0
            for peer_name in peers:
                peer = statistics.get(peer_name)
                if not peer:
                    continue
                peer_phy_tx = max(peer_phy_tx, peer.get("phy_tx", 0))
                peer_phy_rx = max(peer_phy_rx, peer.get("phy_rx", 0))

            lost, loss_pct_iface = compute_iface_pair_loss(
                own_phy_tx, own_phy_rx, peer_phy_tx, peer_phy_rx,
            )

            # When neither this iface nor any peer has TX activity,
            # the loss row is meaningless — show em-dashes. This
            # avoids the historical "100% loss on pure-RX ifaces"
            # confusion.
            has_traffic = max(own_phy_tx, peer_phy_tx) > 0
            lost_text = format_number(lost) if has_traffic else "—"
            lost_item = QTableWidgetItem(lost_text)
            lost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if has_traffic and lost > 0:
                lost_item.setForeground(QColor("#ef4444"))  # red — same as errors
            self.statistics_table.setItem(10, col, lost_item)

            # (11) Loss % — same source numbers, formatted as percent.
            if has_traffic:
                loss_text = f"{loss_pct_iface:.2f}%"
            else:
                loss_pct_iface = 0.0
                loss_text = "—"
            loss_pct_item = QTableWidgetItem(loss_text)
            loss_pct_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            # Same color scale as the stream-stats Loss % cell for
            # consistency: >50% red, >10% amber, >0% muted dark.
            if loss_pct_iface > 50:
                loss_pct_item.setForeground(QColor("#ef4444"))
                loss_pct_item.setFont(QFont("", 11, QFont.Bold))
            elif loss_pct_iface > 10:
                loss_pct_item.setForeground(QColor("#f59e0b"))
            elif loss_pct_iface > 0:
                loss_pct_item.setForeground(QColor("#374151"))
            self.statistics_table.setItem(11, col, loss_pct_item)

        # print(f"✅ Traffic statistics updated: {len(statistics)} interfaces, {max_streams} max streams.")

    def update_stream_statistics_table(self, stream_stats_list):
        """Update the stream statistics table with detailed per-stream information."""
        if not hasattr(self, "stream_statistics_table") or self.stream_statistics_table is None:
            logger.debug(f"[DEBUG STREAM STATS] stream_statistics_table not found or not initialized")
            return

        # v0.2.99: pause-refresh gate. The polling timers in main.py
        # keep firing (cheap) but we skip the rebuild so the table
        # contents stay frozen while the operator grabs a screenshot.
        if getattr(self, "_refresh_paused", False):
            return

        # Set column count first (13 columns: Engine between Interface and
        # TX Count; TX Bit Rate / RX Bit Rate inserted after the pps Rate
        # columns so the live throughput readout matches the Interface
        # Statistics tab without flipping tabs; Latency (μs) inserted after
        # RX Bit Rate, populated from /api/latency/stats per interface).
        try:
            # v0.2.99: capture sort state + disable sorting BEFORE the
            # rebuild. setSortingEnabled(True) makes Qt re-sort on every
            # subsequent setItem call — that scramble is the bug v0.2.92
            # fixed for the Devices tables; same fix applies here.
            _sort_state = capture_sort_state(self.stream_statistics_table)
            _was_sorting = self.stream_statistics_table.isSortingEnabled()
            self.stream_statistics_table.setSortingEnabled(False)

            self.stream_statistics_table.setColumnCount(13)
            self.stream_statistics_table.setHorizontalHeaderLabels([
                "Stream Name", "Interface", "Engine", "TX Count", "RX Count",
                "TX Rate", "RX Rate", "TX Bit Rate", "RX Bit Rate",
                "Latency (μs)", "Loss %", "Status", "Flow Tracking",
            ])

            self.stream_statistics_table.setRowCount(0)
        except Exception as e:
            logger.debug(f"[DEBUG STREAM STATS] Error initializing stream_statistics_table: {e}")
            return
        
        if not stream_stats_list:
            logger.debug(f"[DEBUG STREAM STATS] stream_stats_list is empty")
            return
        
        logger.debug(f"[DEBUG STREAM STATS] Updating table with {len(stream_stats_list)} stream(s)")
        
        def format_number(num):
            """Format number with commas for readability."""
            try:
                return f"{int(num):,}"
            except (ValueError, TypeError):
                return str(num)
        
        def format_rate(rate_val):
            """Format rate with appropriate unit."""
            try:
                # Handle None, 0, or empty values
                if rate_val is None:
                    return "0.00 pps"
                rate_val = float(rate_val)
                if rate_val == 0.0:
                    return "0.00 pps"
                if rate_val >= 1_000_000:
                    return f"{rate_val / 1_000_000:.2f} Mpps"
                elif rate_val >= 1_000:
                    return f"{rate_val / 1_000:.2f} Kpps"
                else:
                    return f"{rate_val:.2f} pps"
            except (ValueError, TypeError) as e:
                logger.debug(f"[DEBUG STREAM STATS] Error formatting rate {rate_val}: {e}")
                return "0.00 pps"
        
        # Process all streams from all servers
        all_streams = []
        for stream in stream_stats_list:
            stream_name = stream.get("stream_name", "Unnamed")
            interface = stream.get("interface", "N/A")
            stream_baselines = getattr(self, "_stream_baselines", {})
            sid = stream.get("stream_id")
            sb = stream_baselines.setdefault(sid, {}) if sid else {}
            try:
                raw_tx = int(stream.get("tx_count", 0) or 0)
            except (ValueError, TypeError):
                raw_tx = 0
            try:
                raw_rx = int(stream.get("rx_count", 0) or 0)
            except (ValueError, TypeError):
                raw_rx = 0
            # Subtract Clear Stats baseline. If the tracker reset (stream
            # stop/start, server restart) made the current count drop below
            # the captured baseline, re-baseline to the new low so the
            # display starts climbing from 0 again instead of staying
            # stuck at zero forever.
            base_tx = int(sb.get("tx_count", 0) or 0)
            base_rx = int(sb.get("rx_count", 0) or 0)
            if raw_tx < base_tx:
                sb["tx_count"] = raw_tx
                base_tx = raw_tx
            if raw_rx < base_rx:
                sb["rx_count"] = raw_rx
                base_rx = raw_rx
            tx_count = raw_tx - base_tx
            rx_count = raw_rx - base_rx
            # Handle tx_rate and rx_rate - they may be None, 0.0, or a float
            tx_rate = stream.get("tx_rate")
            if tx_rate is None:
                tx_rate = 0.0
            rx_rate = stream.get("rx_rate")
            if rx_rate is None:
                rx_rate = 0.0
            
            # Debug: log rates for troubleshooting
            if tx_rate and tx_rate > 0:
                logger.debug(f"[DEBUG STREAM STATS] Stream '{stream_name}': tx_rate={tx_rate} pps")
            if rx_rate and rx_rate > 0:
                logger.debug(f"[DEBUG STREAM STATS] Stream '{stream_name}': rx_rate={rx_rate} pps")
            if tx_rate == 0.0 and rx_rate == 0.0:
                logger.debug(f"[DEBUG STREAM STATS] Stream '{stream_name}': Both rates are 0.0 (tx_count={tx_count}, rx_count={rx_count})")
            flow_tracking = stream.get("flow_tracking_enabled", False)
            status = stream.get("status", "Unknown")
            stream_id = stream.get("stream_id", "")
            tg_id = stream.get("_tg_id")  # Get TG ID from stream (added during collection)
            
            # Calculate loss percentage.
            # v0.3.7: when tx_count == 0 (warmup window or just-started
            # stream that hasn't TX'd a packet yet) report None instead
            # of 0.0. Pre-v0.3.7 the 0.0 rendered as "0.00%" in green,
            # which read as "perfect zero loss" — exactly the opposite
            # of what's true (no packets sent yet, can't measure).
            # The renderer at line ~2108 now treats None as the muted
            # "—" placeholder so the operator sees "not yet measured"
            # instead of false-positive green.
            #
            # v0.5.137/138/140: rate-based loss + Spirent-style latch.
            #
            # When the stream is running with both rates observable,
            # compute rate-based loss `(tx_rate - rx_rate) / tx_rate`
            # — that answers the operator's real question, "what
            # fraction of what I'm sending is being dropped?"
            #
            # v0.5.140 — Spirent-style latching: when the stream is
            # not actively running (rates went to 0, or rx_rate is
            # None), KEEP showing the last observed loss value
            # instead of dropping to "—". Spirent panels behave the
            # same way: the loss column freezes on the final value
            # so the operator can see how the test ended without
            # racing to read it before the cell wipes.
            #
            # Cache is keyed by stream_id and cleared when:
            #   - The operator clicks Clear Stats (cache purged
            #     alongside _stream_baselines / _iface_baselines).
            #   - tx_count drops below the previous reading
            #     (counter reset = stream restart = new session).
            _latched = getattr(self, "_latched_loss_pct", None)
            if _latched is None:
                self._latched_loss_pct = {}
                _latched = self._latched_loss_pct

            # v0.5.142: defensive init — `_stream_baselines` is
            # normally set by the Clear Stats handler in main.py,
            # but on a cold-start client (no Clear Stats clicked
            # yet) it doesn't exist. The bookkeeping for the loss
            # latch's counter-reset detection needs a dict to
            # hang `_last_tx_for_latch` off; create it lazily.
            _baselines = getattr(self, "_stream_baselines", None)
            if not isinstance(_baselines, dict):
                self._stream_baselines = {}
                _baselines = self._stream_baselines

            # Detect counter reset → new session → drop the latch.
            _prev_tx = _baselines.setdefault(
                stream_id, {}).get("_last_tx_for_latch")
            if (stream_id and _prev_tx is not None
                    and isinstance(raw_tx, int) and raw_tx < _prev_tx):
                _latched.pop(stream_id, None)
            if stream_id:
                _baselines.setdefault(stream_id, {})[
                    "_last_tx_for_latch"] = raw_tx

            if (isinstance(tx_count, int) and tx_count > 0
                    and tx_rate and tx_rate > 0
                    and rx_rate is not None):
                # Running stream with both rates. Clamp the rare
                # rx_rate > tx_rate case (sample-window phase
                # offset between rx_worker and tx_worker poll
                # cycles) to 0 so we don't flash a negative
                # "loss" that confuses the operator.
                _diff = tx_rate - rx_rate
                loss_pct = max(0.0, (_diff / tx_rate) * 100.0)
                if stream_id:
                    _latched[stream_id] = loss_pct
            elif stream_id and stream_id in _latched:
                # Spirent-style latch: surface the final loss seen
                # during the most recent running window. Stays put
                # until the operator clears stats or restarts the
                # stream (counter reset above).
                loss_pct = _latched[stream_id]
            else:
                # Never had a running sample → no honest loss to
                # report. Renderer shows the muted "—" placeholder.
                loss_pct = None
            
            # Format interface name with TG ID if available
            if tg_id is not None:
                interface_display = f"TG {tg_id} - {interface}"
            else:
                interface_display = interface
            
            all_streams.append({
                "stream_name": stream_name,
                "interface": interface_display,
                "tx_count": tx_count,
                "rx_count": rx_count,
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
                "loss_pct": loss_pct,
                "status": status,
                "flow_tracking": flow_tracking,
                "dpdk_enable": bool(stream.get("dpdk_enable", False)),
                "dpdk_tx_cores": int(stream.get("dpdk_tx_cores") or 1),
                "stream_id": stream_id,
                # v0.2.77: runtime engine + fallback markers from the
                # stats endpoint. Set ONLY when the launcher had to swap
                # engines mid-flight (e.g. tx_worker rc=100); absent in
                # the common case.
                "runtime_engine": stream.get("runtime_engine"),
                "runtime_fallback_reason": stream.get("runtime_fallback_reason"),
                # v0.3.12: forward the requested engine + RDMA config so
                # the engine-column renderer (line ~1950) can pick up
                # "RDMA Send"/"RDMA Write"/etc. labels instead of falling
                # through to the default "Scapy" branch for engine=rdma
                # streams.
                "engine": stream.get("engine") or "",
                "rdma": stream.get("rdma") or {},
                # Latency-related: raw iface (for the per-iface latency
                # join), the enable_timestamps flag (so the cell can show
                # "off" when the stream wasn't sent with --enable-timestamps),
                # and the per-iface latency stats blob the worker stuffed
                # onto this entry.
                "_raw_iface": interface,
                "enable_timestamps": bool(stream.get("enable_timestamps") or stream.get("latency_enabled")),
                "_latency": stream.get("_latency"),
                # v0.5.115: server-side wire-delivery warning. Set
                # by /api/streams/stats when TX is firing but RX
                # is essentially zero on the configured rx_iface.
                # The RX-count cell renderer surfaces this as an
                # amber ⚠ prefix with the summary in the tooltip.
                # Absent in the common case — when RX matches TX
                # the warning is None.
                "wire_delivery_warning": stream.get("wire_delivery_warning"),
                # v0.5.136: frame_size for the TX/RX Bit Rate cells.
                # Pre-fix the render loop fell back to 64 → on srv06
                # a 1000-byte UEC stream firing at 23.77 Mpps showed
                # "12.17 Gbps" (23.77M × 64 × 8) instead of the
                # actual ~190 Gbps. /api/streams/stats already
                # includes frame_size; just forward it.
                "frame_size": stream.get("frame_size", 64),
            })
        
        # Sort by interface, then by stream name
        all_streams.sort(key=lambda x: (x["interface"], x["stream_name"]))
        
        # Populate table
        self.stream_statistics_table.setRowCount(len(all_streams))
        
        for row, stream in enumerate(all_streams):
            # Stream Name
            name_item = QTableWidgetItem(stream["stream_name"])
            name_item.setData(Qt.UserRole, stream["stream_id"])
            self.stream_statistics_table.setItem(row, 0, name_item)
            
            # Interface
            iface_item = QTableWidgetItem(stream["interface"])
            self.stream_statistics_table.setItem(row, 1, iface_item)

            # Engine — show DPDK queue count if multi-queue, else "Scapy".
            # v0.2.77: when the server marks a RUNTIME fallback (the
            # launcher had to swap engines mid-flight, e.g. tx_worker
            # rc=100), render "Scapy ⚠ (was DPDK)" + the reason in the
            # tooltip so the operator doesn't have to grep journalctl
            # to find out why throughput is half of what it should be.
            #
            # v0.3.12: also recognise runtime_engine == "rdma" so per-
            # stream RDMA (Engine: RDMA (perftest)) renders as such
            # instead of falling through to the "Scapy" default branch.
            # Read the requested engine from stream_data.engine (new
            # field) with the legacy dpdk_enable fallback.
            runtime_engine = stream.get("runtime_engine")
            fallback_reason = stream.get("runtime_fallback_reason")
            requested_engine = (stream.get("engine") or "").strip().lower()
            dpdk_requested = (requested_engine == "dpdk"
                              or bool(stream.get("dpdk_enable")))
            rdma_requested = (requested_engine == "rdma"
                              or runtime_engine == "rdma")
            if runtime_engine == "scapy" and dpdk_requested:
                engine_label = "Scapy ⚠ (was DPDK)"
                engine_color = QColor("#b45309")  # Amber — degraded
            elif rdma_requested:
                # v0.3.12: perftest-driven stream. Label with the
                # test variant when known so the operator can tell
                # send_bw vs write_lat at a glance.
                rdma_cfg = stream.get("rdma") or {}
                test = (rdma_cfg.get("test") or "").strip()
                test_short = {
                    "send_bw": "Send",   "write_bw": "Write",   "read_bw": "Read",
                    "send_lat": "SendL", "write_lat": "WriteL", "read_lat": "ReadL",
                }.get(test, "")
                engine_label = f"RDMA {test_short}".strip()
                engine_color = QColor("#7c3aed")  # Purple — distinct from DPDK blue
            elif dpdk_requested:
                tx_cores = int(stream.get("dpdk_tx_cores") or 1)
                if tx_cores > 1:
                    engine_label = f"DPDK ×{tx_cores}"
                else:
                    engine_label = "DPDK"
                engine_color = QColor("#1d4ed8")  # Blue for DPDK
            else:
                engine_label = "Scapy"
                engine_color = QColor("#6b7280")  # Gray
            engine_item = QTableWidgetItem(engine_label)
            engine_item.setTextAlignment(Qt.AlignCenter)
            engine_item.setForeground(engine_color)
            engine_item.setFont(QFont("", 12, QFont.Bold))
            if fallback_reason:
                engine_item.setToolTip(
                    f"Engine: Scapy (runtime fallback)\n\n"
                    f"Reason: {fallback_reason}\n\n"
                    f"Originally requested DPDK; the launcher had to "
                    f"swap engines mid-flight. The stream is still "
                    f"sending — just on a slower engine than expected."
                )
            else:
                engine_item.setToolTip(
                    f"Engine: {'DPDK tx_worker' if dpdk_requested else 'Scapy/kernel'}"
                    + (f"\nTX queues: {stream.get('dpdk_tx_cores', 1)}"
                       if dpdk_requested else "")
                )
            self.stream_statistics_table.setItem(row, 2, engine_item)

            # TX Count
            tx_item = QTableWidgetItem(format_number(stream["tx_count"]))
            tx_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.stream_statistics_table.setItem(row, 3, tx_item)

            # RX Count
            rx_count = stream["rx_count"]
            if rx_count is None:
                rx_display = "N/A"
            else:
                rx_display = format_number(rx_count)
            # v0.5.115: prefix ⚠ when the server's switch-cap
            # detector flagged this stream — TX is firing but RX
            # is essentially zero. Tooltip carries the full
            # summary (named causes ordered by what we hit in
            # the srv06 saga). Cell stays in the same column so
            # row layout is unaffected; the indicator is purely
            # informational.
            wdw = stream.get("wire_delivery_warning")
            if wdw and isinstance(wdw, dict):
                rx_display = f"⚠ {rx_display}"
            rx_item = QTableWidgetItem(rx_display)
            rx_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if stream["flow_tracking"] and isinstance(rx_count, int) and stream["tx_count"] > 0 and rx_count == 0:
                rx_item.setForeground(QColor("#ef4444"))  # Red for 100% loss
            if wdw and isinstance(wdw, dict):
                # Amber wins over the red "100% loss" foreground
                # because the warning IS the explanation for the
                # loss — both stem from the same observation but
                # the warning gives the operator something to
                # act on instead of just stating the symptom.
                rx_item.setForeground(QColor("#b45309"))
                summary = wdw.get("summary") or "Wire is dropping frames."
                rx_item.setToolTip(
                    f"⚠ Wire delivery warning\n\n{summary}\n\n"
                    f"See Help → DPDK Workflow Guide → "
                    f"Troubleshooting: RX = 0 with DPDK."
                )
            self.stream_statistics_table.setItem(row, 4, rx_item)

            # TX Rate — bold + blue to match the Interface Statistics tab's
            # Send Frame Rate row, so the live throughput readout looks the
            # same regardless of which tab the user happens to be on.
            tx_rate = stream.get("tx_rate")
            if tx_rate is None or tx_rate == 0.0:
                tx_rate_display = "0.00 pps"
            else:
                tx_rate_display = format_rate(tx_rate)
            tx_rate_item = QTableWidgetItem(tx_rate_display)
            tx_rate_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tx_rate_item.setFont(QFont("Monaco, Consolas, monospace", 12, QFont.Bold))
            tx_rate_item.setForeground(QColor("#1d4ed8"))  # Blue for TX
            self.stream_statistics_table.setItem(row, 5, tx_rate_item)

            # RX Rate — same monospace as TX but regular weight + darker
            # neutral, matching the Receive Frame Rate row in Interface Stats.
            rx_rate = stream.get("rx_rate")
            if rx_rate is None or rx_rate == 0.0:
                rx_rate_display = "0.00 pps"
            else:
                rx_rate_display = format_rate(rx_rate)
            rx_rate_item = QTableWidgetItem(rx_rate_display)
            rx_rate_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rx_rate_item.setForeground(QColor("#111827"))
            self.stream_statistics_table.setItem(row, 6, rx_rate_item)

            # TX Bit Rate / RX Bit Rate — derived from the per-second rate
            # and the stream's frame_size (rate × bytes × 8). Bit-rate
            # auto-formats to Gbps/Mbps/Kbps the same way the Interface
            # Statistics tab does, so a 100G line-rate stream shows
            # "~395 Gbps" right alongside its "32.79 Mpps".
            try:
                _fs = int(stream.get("frame_size") or 64)
            except (ValueError, TypeError):
                _fs = 64
            tx_bps_val = (tx_rate or 0.0) * _fs * 8
            rx_bps_val = (rx_rate or 0.0) * _fs * 8

            def _format_bps(v):
                if not v or v <= 0:
                    return "0.00 bps"
                if v >= 1_000_000_000:
                    return f"{v / 1_000_000_000:.2f} Gbps"
                if v >= 1_000_000:
                    return f"{v / 1_000_000:.2f} Mbps"
                if v >= 1_000:
                    return f"{v / 1_000:.2f} Kbps"
                return f"{v:.2f} bps"

            # TX Bit Rate — bold + blue (paired with TX Rate at col 5).
            tx_bps_item = QTableWidgetItem(_format_bps(tx_bps_val))
            tx_bps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tx_bps_item.setFont(QFont("Monaco, Consolas, monospace", 12, QFont.Bold))
            tx_bps_item.setForeground(QColor("#1d4ed8"))
            self.stream_statistics_table.setItem(row, 7, tx_bps_item)

            # RX Bit Rate — neutral, matches RX Rate styling.
            rx_bps_item = QTableWidgetItem(_format_bps(rx_bps_val))
            rx_bps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rx_bps_item.setForeground(QColor("#111827"))
            self.stream_statistics_table.setItem(row, 8, rx_bps_item)

            # Latency (μs) — sourced from /api/latency/stats per-interface,
            # joined onto the stream by its raw iface name (the polling
            # worker stuffs `_latency` onto each stream entry before this
            # function sees it). The number itself is p50 in microseconds;
            # tooltip carries the full min/avg/p50/p99/max + sample count
            # so a hover tells the whole story without widening the column.
            #
            # Three display states:
            #   - "—"  : sampler not running for this iface (or no samples
            #             yet). Muted gray.
            #   - "X.YY us" : real reading, color-coded by magnitude
            #             (green <100us, amber <1ms, red ≥1ms).
            #   - "off" : stream wasn't sent with --enable-timestamps and
            #             therefore can't have NLAT-tagged frames. Muted.
            lat = stream.get("_latency") or {}
            ts_enabled = bool(stream.get("enable_timestamps") or stream.get("latency_enabled"))
            p50 = lat.get("p50_us")
            if not ts_enabled and not p50:
                lat_text = "off"
                lat_color = QColor("#9ca3af")
                lat_tip = ("Latency timestamps disabled for this stream.\n"
                           "Re-create the stream with the 'Enable timestamps' "
                           "checkbox set to measure one-way latency.")
            elif p50 is None:
                lat_text = "—"
                lat_color = QColor("#9ca3af")
                lat_tip = ("Sampler not yet returning samples for this "
                           "interface.\nThe RX-side server starts the "
                           "sampler on first /api/latency/stats query — "
                           "wait one polling tick.")
            else:
                if p50 < 100:
                    lat_color = QColor("#10b981")
                elif p50 < 1000:
                    lat_color = QColor("#f59e0b")
                else:
                    lat_color = QColor("#ef4444")
                lat_text = f"{p50:.1f}"
                # p95 was added in 0.2.58 — most SLAs are stated in p95
                # rather than p50 or p99, so surface it in the tooltip
                # too. Format defensively in case an old server (no p95
                # in its snapshot dict) is on the other end.
                p95 = lat.get("p95_us")
                p95_line = (f"  p95  = {p95:.2f} us\n"
                            if isinstance(p95, (int, float)) else "")
                lat_tip = (
                    f"One-way latency over last {lat.get('window_samples', 0)} samples:\n"
                    f"  min  = {lat.get('min_us'):.2f} us\n"
                    f"  avg  = {lat.get('avg_us'):.2f} us\n"
                    f"  p50  = {lat.get('p50_us'):.2f} us  (this cell)\n"
                    f"{p95_line}"
                    f"  p99  = {lat.get('p99_us'):.2f} us\n"
                    f"  max  = {lat.get('max_us'):.2f} us\n"
                    f"\n"
                    f"Total NLAT-decoded frames seen by sampler: "
                    f"{lat.get('samples_decoded', 0)}\n"
                    f"Frames skipped (no NLAT magic / too short): "
                    f"{lat.get('samples_skipped', 0)}"
                )
            lat_item = QTableWidgetItem(lat_text)
            lat_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lat_item.setFont(QFont("Monaco, Consolas, monospace", 12, QFont.Bold))
            lat_item.setForeground(lat_color)
            lat_item.setToolTip(lat_tip)
            self.stream_statistics_table.setItem(row, 9, lat_item)

            # Loss % — only meaningful when flow tracking is on AND the
            # stream is actively running. A non-flow-tracked stream has no
            # way to know rx_count, so reporting "100% loss" on those
            # (which the previous code did, in red) was misleading. Show
            # a muted "—" instead.
            # v0.3.7: also treat loss_pct=None as "not yet measured" so
            # a running stream in its warmup window (tx_count=0) shows
            # "—" instead of a false-positive "0.00% green" reading.
            loss_pct = stream["loss_pct"]
            stream_running = str(stream.get("status", "")).lower() == "running"
            if not stream["flow_tracking"]:
                loss_text = "—"
                loss_color = QColor("#9ca3af")  # muted gray
            elif not stream_running and stream["tx_count"] == 0:
                loss_text = "—"
                loss_color = QColor("#9ca3af")
            elif loss_pct is None:
                # v0.3.7: tx_count is 0 (warmup) — no basis to compute.
                loss_text = "—"
                loss_color = QColor("#9ca3af")
            else:
                loss_text = f"{loss_pct:.2f}%"
                if loss_pct > 50:
                    loss_color = QColor("#ef4444")  # Red for >50% loss
                elif loss_pct > 10:
                    loss_color = QColor("#f59e0b")  # Orange for >10%
                elif loss_pct > 0:
                    loss_color = QColor("#fbbf24")  # Yellow for >0%
                else:
                    loss_color = QColor("#10b981")  # Green for 0% loss
            loss_item = QTableWidgetItem(loss_text)
            loss_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            loss_item.setFont(QFont("", 12, QFont.Bold))
            loss_item.setForeground(loss_color)

            self.stream_statistics_table.setItem(row, 10, loss_item)

            # Status
            status = stream["status"]
            status_item = QTableWidgetItem(status)
            if status.lower() == "running":
                status_item.setForeground(QColor("#10b981"))  # Green
            elif status.lower() == "stopped":
                status_item.setForeground(QColor("#6b7280"))  # Gray
            else:
                status_item.setForeground(QColor("#ef4444"))  # Red
            status_item.setFont(QFont("", 12, QFont.Bold))
            self.stream_statistics_table.setItem(row, 11, status_item)

            # Flow Tracking
            flow_tracking_item = QTableWidgetItem("Yes" if stream["flow_tracking"] else "No")
            flow_tracking_item.setTextAlignment(Qt.AlignCenter)
            self.stream_statistics_table.setItem(row, 12, flow_tracking_item)

        # Resize columns to fit content
        self.stream_statistics_table.resizeColumnsToContents()

        # v0.2.99: restore sort indicator + re-apply filter + update
        # the last-refresh chip. The capture happened at the top of
        # this method; restore here so the operator's chosen sort
        # column persists across the 2 s rebuild.
        try:
            self.stream_statistics_table.setSortingEnabled(_was_sorting)
            restore_sort_state(self.stream_statistics_table, _sort_state)
        except (NameError, Exception):
            # _sort_state / _was_sorting weren't set (early-return
            # path above). Nothing to restore.
            pass
        try:
            self._apply_stream_filter()
        except Exception:
            pass
        try:
            self._update_last_refresh_chip()
        except Exception:
            pass

    # ─────────────────────────────────────── v0.2.99 helpers
    def _on_stream_filter_changed(self, text):
        """Cache the needle and re-hide rows. Lower-cased here so the
        per-row walk in `_apply_stream_filter` doesn't have to do it
        N times per refresh."""
        self._stream_filter_needle = (text or "").strip().lower()
        self._apply_stream_filter()

    def _apply_stream_filter(self):
        """Walk the stream-statistics table and hide rows that don't
        contain the cached needle in any of: Stream Name (col 0),
        Interface (col 1), Engine (col 2). Empty needle = show all."""
        if not hasattr(self, "stream_statistics_table") \
                or self.stream_statistics_table is None:
            return
        needle = getattr(self, "_stream_filter_needle", "")
        table = self.stream_statistics_table
        for row in range(table.rowCount()):
            if not needle:
                table.setRowHidden(row, False)
                continue
            match = False
            for col in (0, 1, 2):
                item = table.item(row, col)
                if item is not None and needle in item.text().lower():
                    match = True
                    break
            table.setRowHidden(row, not match)

    def _on_refresh_pause_toggled(self, checked):
        """Pause button flip. The two update_* paths check
        `self._refresh_paused` at entry and bail early when True.
        Button label flips so the operator can see at a glance whether
        the GUI is live or frozen."""
        self._refresh_paused = bool(checked)
        try:
            self.pause_refresh_button.setText("Resume" if checked else "Pause")
        except Exception:
            pass
        # When resuming, force-fire an immediate refresh of the chip so
        # the operator sees a fresh timestamp instead of the stale one
        # the dock froze on.
        if not checked:
            try:
                self._update_last_refresh_chip()
            except Exception:
                pass

    def _update_last_refresh_chip(self):
        """Stamp the action-bar chip with the current wall-clock time.
        Called at the end of every successful (non-paused) rebuild of
        the stream table — that's the operator's primary "is this
        fresh?" signal. Format: ``Updated HH:MM:SS``."""
        if not hasattr(self, "last_refresh_label") \
                or self.last_refresh_label is None:
            return
        now = _time.localtime()
        ts = f"{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"
        try:
            self.last_refresh_label.setText(f"Updated {ts}")
            # Reset to neutral grey (the colour is amber when the chip
            # has been frozen by the pause toggle for > 5 s; otherwise
            # plain grey).
            self.last_refresh_label.setStyleSheet(
                "QLabel { color: #6b7280; font-size: 10px; padding: 0 6px; }"
            )
        except Exception:
            pass

    def export_statistics_csv(self):
        """Dump the visible Interface + Stream statistics tables to a
        CSV file the operator picks via a save dialog.

        Layout: a small header block (timestamp + per-server addresses)
        followed by two sections, each preceded by a ``# Section: ...``
        comment row and that table's header row. Empty tables write the
        header + a ``# (no rows)`` comment instead of being skipped, so
        the file structure is self-describing even mid-test before any
        traffic has flowed.

        This captures the snapshot currently on screen — it does NOT
        re-poll the servers. For a fresh baseline, click Clear Stats
        first, wait one poll, then export.
        """
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        import csv
        import datetime as _dt
        import os as _os

        default_name = (
            "netgen-stats-"
            + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".csv"
        )
        default_dir = _os.path.expanduser("~/Downloads")
        default_path = _os.path.join(default_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Statistics", default_path, "CSV Files (*.csv)"
        )
        if not path:
            return   # user cancelled

        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                # Header block
                w.writerow([f"# netgen statistics export"])
                w.writerow([f"# exported_at: {_dt.datetime.now().isoformat(timespec='seconds')}"])
                servers = getattr(self, "server_interfaces", []) or []
                for s in servers:
                    w.writerow([
                        f"# server: TG {s.get('tg_id','?')} "
                        f"{s.get('address','?')} "
                        f"online={s.get('online', True)}"
                    ])
                w.writerow([])
                # Section 1 — Interface Statistics
                self._dump_table_to_csv(w, "Interface Statistics",
                                       getattr(self, "statistics_table", None))
                w.writerow([])
                # Section 2 — Stream Statistics
                self._dump_table_to_csv(w, "Stream Statistics",
                                       getattr(self, "stream_statistics_table", None))
        except Exception as exc:
            QMessageBox.critical(
                self, "Export Failed",
                f"Could not write CSV to:\n{path}\n\n{type(exc).__name__}: {exc}"
            )
            logger.error(f"[EXPORT] CSV export failed: {exc}")
            return

        QMessageBox.information(
            self, "Export Complete", f"Statistics written to:\n{path}"
        )
        logger.info(f"[EXPORT] Wrote statistics CSV: {path}")

    @staticmethod
    def _dump_table_to_csv(writer, section_name, table):
        """Helper: serialize a QTableWidget to the given csv.writer.

        Writes a section comment, the header row, then each visible (not
        hidden) row as plain cell text. Cell widgets that aren't items
        (combos, checkboxes) are read via their `currentText()` or
        `isChecked()` if available; otherwise the cell renders as empty.
        """
        writer.writerow([f"# Section: {section_name}"])
        if table is None or table.columnCount() == 0:
            writer.writerow([f"# (table not available)"])
            return
        headers = []
        for c in range(table.columnCount()):
            hi = table.horizontalHeaderItem(c)
            headers.append(hi.text() if hi else f"col{c}")
        writer.writerow(headers)
        n_rows = table.rowCount()
        if n_rows == 0:
            writer.writerow([f"# (no rows)"])
            return
        for r in range(n_rows):
            if table.isRowHidden(r):
                continue
            row = []
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if item is not None:
                    row.append(item.text())
                    continue
                # Cell widget fallback — best-effort for combos / checkboxes.
                w_ = table.cellWidget(r, c)
                if w_ is None:
                    row.append("")
                    continue
                if hasattr(w_, "currentText"):
                    row.append(w_.currentText())
                elif hasattr(w_, "isChecked"):
                    row.append("yes" if w_.isChecked() else "no")
                else:
                    row.append("")
            writer.writerow(row)

    def clear_cached_statistics(self):
        """'Clear Stats' implemented as a baseline tare.

        The server reports kernel netdev counters (tx_packets, rx_packets,
        bytes, errors) which are cumulative since interface up — they can't
        be reset from userspace without ifconfig down/up. Same goes for
        tracker-driven stream tx_count / rx_count: the hot path increments
        them, no API to zero them out.

        Instead, snapshot the current cumulative values and remember them as
        baselines. update_statistics_table / update_stream_statistics_table
        subtract these baselines on each refresh so the display *appears* to
        reset to 0 and counts up from there. Stream/interface rates (fps,
        bps) are instantaneous, not cumulative — no baseline needed.
        """
        logger.info("[INFO] Clearing displayed traffic statistics (taring baselines).")

        # Per-interface baselines for cumulative columns (Sent Frames,
        # Received Frames, Sent Bytes, Received Bytes, Errors). Source the
        # raw cumulative values from _last_statistics — that's what
        # update_statistics_table sees on the next poll, so subtracting
        # this snapshot will correctly reset deltas to 0.
        self._iface_baselines = {}
        last = getattr(self, "_last_statistics", None) or {}
        for iface_name, stats in last.items():
            if not isinstance(stats, dict):
                continue
            self._iface_baselines[iface_name] = {
                "tx": int(stats.get("tx", 0) or 0),
                "rx": int(stats.get("rx", 0) or 0),
                "sent_bytes": int(stats.get("sent_bytes", 0) or 0),
                "received_bytes": int(stats.get("received_bytes", 0) or 0),
                "errors": int(stats.get("errors", 0) or 0),
            }

        # Per-stream baselines for the Stream Statistics tab.
        self._stream_baselines = {}
        last_stream = getattr(self, "_last_stream_stats", None) or []
        for s in last_stream:
            if not isinstance(s, dict):
                continue
            sid = s.get("stream_id")
            if sid:
                self._stream_baselines[sid] = {
                    "tx_count": int(s.get("tx_count", 0) or 0),
                    "rx_count": int(s.get("rx_count", 0) or 0),
                }

        # Visual immediate reset — overwrite each numeric cell with its
        # zero-display placeholder so the user sees the click took effect
        # right away, before the next poll repopulates with tared values.
        self.clear_statistics_table()
    def clear_statistics_table(self):
        """Visually zero every numeric cell in both stats tables.

        Previously this called setColumnCount(0) which destroyed the
        per-interface columns until the next poll rebuilt them — looked
        like "Clear Stats half-worked, rates still showing." Now we keep
        structure intact and overwrite each numeric cell with its
        zero-display string. The next polling tick (~1s later) re-fills
        with the now-tared values.

        Status cells, interface labels, and stream names are preserved —
        they're metadata, not stats.
        """
        # ---- Interface Statistics tab ----
        # Row layout: 0 Status (preserve), 1 Sent Frames, 2 Received Frames,
        # 3 Sent Bytes, 4 Received Bytes, 5 Send Frame Rate, 6 Receive Frame
        # Rate, 7 Send Bit Rate, 8 Receive Bit Rate, 9 Errors.
        zero_for_row = {
            1: "0", 2: "0",            # frame counts
            3: "0 B", 4: "0 B",        # byte counts
            5: "0.00 fps", 6: "0.00 fps",
            7: "0.00 bps", 8: "0.00 bps",
            9: "0",                    # errors
        }
        try:
            rows = self.statistics_table.rowCount()
            cols = self.statistics_table.columnCount()
            for r in range(rows):
                if r == 0:  # Status row — keep "up"/"down" intact
                    continue
                placeholder = zero_for_row.get(r)
                if placeholder is None:
                    continue
                for c in range(cols):
                    item = self.statistics_table.item(r, c)
                    if item is None:
                        item = QTableWidgetItem(placeholder)
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        self.statistics_table.setItem(r, c, item)
                    else:
                        item.setText(placeholder)
                        # Drop any prior color (red errors, etc.)
                        item.setForeground(QColor("#1f2937"))
        except Exception as e:
            logger.debug(f"[CLEAR STATS] interface table reset failed: {e}")

        # ---- Stream Statistics tab ----
        # Column layout: 0 Stream Name, 1 Interface, 2 Engine, 3 TX Count,
        # 4 RX Count, 5 TX Rate, 6 RX Rate, 7 TX Bit Rate, 8 RX Bit Rate,
        # 9 Latency (μs), 10 Loss %, 11 Status, 12 Flow Tracking.
        if hasattr(self, "stream_statistics_table") and self.stream_statistics_table is not None:
            zero_for_col = {
                3: "0", 4: "0",                          # counts
                5: "0.00 pps", 6: "0.00 pps",            # rates (pps)
                7: "0.00 bps", 8: "0.00 bps",            # rates (bps)
                9: "—",                                  # latency
                10: "0.00%",                             # loss
            }
            try:
                rows = self.stream_statistics_table.rowCount()
                cols = self.stream_statistics_table.columnCount()
                for r in range(rows):
                    for c in range(cols):
                        placeholder = zero_for_col.get(c)
                        if placeholder is None:
                            continue
                        item = self.stream_statistics_table.item(r, c)
                        if item is None:
                            item = QTableWidgetItem(placeholder)
                            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                            self.stream_statistics_table.setItem(r, c, item)
                        else:
                            item.setText(placeholder)
                            # Reset loss% color (was red/yellow/green) at
                            # the new col 10. Latency cell color reset
                            # happens naturally on the next poll tick.
                            if c == 10:
                                item.setForeground(QColor("#10b981"))
                            elif c == 9:
                                item.setForeground(QColor("#9ca3af"))
            except Exception as e:
                logger.debug(f"[CLEAR STATS] stream table reset failed: {e}")
        
        #print("Traffic statistics cleared.")
    def enable_make_server_online_menu(self):
        """Enable the 'Make Server Online' menu item to allow user-initiated retry."""
        if hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(True)