#statistics_section.py#

from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox, QLabel, QTabWidget, QWidget, QSizePolicy
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QFontMetrics
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPointF
import requests
import logging
import time as _time
from collections import deque

logger = logging.getLogger(__name__)


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

        # Determine Y max across all visible series
        y_max = 0.0
        for ts, vals in self._samples:
            if ts < t_min:
                continue
            for v in vals.values():
                if v > y_max:
                    y_max = v
        if y_max <= 0:
            y_max = 1.0  # avoid div-by-zero; chart will be flat
        # Round y_max up to a "nice" number
        # Pick a power-of-10 step that divides y_max into 4 ticks
        import math
        log = math.log10(max(y_max, 1.0))
        step = 10 ** math.floor(log) / 2.0
        while step * 8 < y_max:
            step *= 2
        y_top = step * 4

        # Axes
        axis_pen = QPen(QColor("#cbd5e1"))
        axis_pen.setWidth(1)
        p.setPen(axis_pen)
        p.drawLine(plot_x0, plot_y0, plot_x0, plot_y0 + plot_h)  # Y axis
        p.drawLine(plot_x0, plot_y0 + plot_h,
                   plot_x0 + plot_w, plot_y0 + plot_h)  # X axis

        # Y-axis labels (4 ticks)
        label_font = QFont(self.font()); label_font.setPointSize(9)
        p.setFont(label_font)
        p.setPen(QColor("#6b7280"))
        for i in range(5):
            y_val = y_top * i / 4
            y_px = plot_y0 + plot_h - int(plot_h * i / 4)
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
                        self.stream_stats_fetched.emit(server, stream_stats)
                    else:
                        self.fetch_error.emit(server, f"HTTP {response.status_code}")
                except Exception as e:
                    self.fetch_error.emit(server, str(e))
        
        self.finished.emit()


class TrafficGenClientStatisticsSection():
    def setup_traffic_statistics_section(self):
        self.statistics_group = QGroupBox("Traffic Statistics")
        layout = QVBoxLayout()

        # Create tab widget for statistics
        self.statistics_tab_widget = QTabWidget()
        
        # Tab 1: Interface Statistics
        interface_stats_tab = QWidget()
        interface_stats_layout = QVBoxLayout(interface_stats_tab)
        
        # Interface Statistics Table
        self.statistics_table = QTableWidget()
        self.statistics_table.setRowCount(10)
        self.statistics_table.setColumnCount(0)
        self.statistics_table.setVerticalHeaderLabels([
            "Status", "Sent Frames", "Received Frames", "Sent Bytes", "Received Bytes",
            "Send Frame Rate (fps)", "Receive Frame Rate (fps)", "Send Bit Rate (bps)",
            "Receive Bit Rate (bps)", "Errors"
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
                padding: 8px 10px;
                border: 1px solid #cbd5e1;
                border-left: none;
                border-top: none;
                font-weight: 700;
                font-size: 12px;
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
        
        interface_stats_layout.addWidget(self.statistics_table)
        interface_stats_layout.setContentsMargins(0, 0, 0, 0)
        
        # Tab 2: Stream Statistics
        stream_stats_tab = QWidget()
        stream_stats_layout = QVBoxLayout(stream_stats_tab)
        
        # Stream Statistics Table
        self.stream_statistics_table = QTableWidget()
        self.stream_statistics_table.setColumnCount(12)
        self.stream_statistics_table.setHorizontalHeaderLabels([
            "Stream Name", "Interface", "Engine", "TX Count", "RX Count",
            "TX Rate", "RX Rate", "TX Bit Rate", "RX Bit Rate",
            "Loss %", "Status", "Flow Tracking",
        ])
        self.stream_statistics_table.setStyleSheet(table_style)
        self.stream_statistics_table.setAlternatingRowColors(True)
        self.stream_statistics_table.setFont(font)
        self.stream_statistics_table.setSortingEnabled(True)
        self.stream_statistics_table.verticalHeader().setDefaultSectionSize(32)
        self.stream_statistics_table.verticalHeader().setMinimumSectionSize(28)
        
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
        
        # Tab styling — bumped for visibility:
        # - tab font 11px → 13px so labels are easier to read
        # - active-tab bottom-border 2px → 3px and a brighter blue so the
        #   selected tab is unmistakable
        # - inactive-tab text contrast strengthened (#4b5563 → #374151)
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
                padding: 8px 20px;
                margin-right: 2px;
                font-weight: 600;
                font-size: 13px;
                min-width: 170px;
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
        
        layout.addWidget(self.statistics_tab_widget)
        
        # Clear Stats Button
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.clear_stats_button_traffic = QPushButton("Clear Stats")
        self.clear_stats_button_traffic.setFixedWidth(120)
        self.clear_stats_button_traffic.clicked.connect(self.clear_cached_statistics)
        button_layout.addWidget(self.clear_stats_button_traffic)
        layout.addLayout(button_layout)

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
            logger.info("No servers available. Clearing traffic statistics.")
            self.clear_statistics_table()
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
        self._stats_worker.interfaces_fetched.connect(self._on_interfaces_fetched)
        self._stats_worker.stream_stats_fetched.connect(self._on_stream_stats_fetched)
        self._stats_worker.fetch_error.connect(self._on_fetch_error)
        self._stats_worker.finished.connect(self._on_stats_fetch_finished)
        
        # Reset pending data
        self._pending_stats_data = {}
        self._pending_stream_stats = []
        
        self._stats_worker.start()
    
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

                    if flow_tracking:
                        merged_statistics[tx_iface]["rx"] += rx
                        merged_statistics[tx_iface]["received_bytes"] += rx * frame_size
                        merged_statistics[tx_iface]["receive_fps"] += rx_rate
                        merged_statistics[tx_iface]["receive_bps"] += rx_rate * frame_size * 8

                # RX aggregation
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
                for key in ("tx", "rx", "sent_bytes", "received_bytes"):
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
            # Skip the "No new statistics" message to reduce console spam
            self.update_statistics_table(self._last_statistics)
            self._push_chart_sample(self._last_statistics)
        else:
            self.clear_statistics_table()
        
        # Always update stream statistics table with all collected streams (even if empty, to clear table)
        logger.debug(f"[DEBUG STREAM STATS] Calling update_stream_statistics_table with {len(all_stream_stats)} stream(s)")
        self.update_stream_statistics_table(all_stream_stats)
        
        # Also refresh stream table to show updated statistics
        # This ensures the stream table updates periodically along with traffic statistics
        # Call _do_update_stream_table directly to bypass debouncing for periodic updates
        if hasattr(self, "_do_update_stream_table"):
            # Reset the populating flag to ensure updates can happen
            if hasattr(self, "_populating_table") and self._populating_table:
                # print(f"[STATS] Stream table was populating, resetting flag")
                self._populating_table = False
            # print(f"[STATS] Refreshing stream table from fetch_and_update_statistics() - calling _do_update_stream_table()")
            # Call directly instead of using timer - the statistics update is already async
            # This ensures the stream table updates every time statistics are fetched
            try:
                # Check if _populating_table is blocking us
                if hasattr(self, "_populating_table") and self._populating_table:
                    # print(f"[STATS WARNING] Stream table is already populating, skipping this update")
                    pass
                else:
                    # print(f"[STATS] Calling _do_update_stream_table() now...")
                    self._do_update_stream_table()
                    # print(f"[STATS] _do_update_stream_table() completed")
            except Exception as e:
                logger.error(f"[STATS ERROR] Failed to update stream table: {e}")
                import traceback
                traceback.print_exc()
        elif hasattr(self, "update_stream_table"):
            from PyQt5.QtCore import QTimer
            # print(f"[STATS] Refreshing stream table (fallback) from fetch_and_update_statistics()")
            QTimer.singleShot(10, lambda: self.update_stream_table())

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

            # Normalize interface name for matching
            # Table shows just "ens5np0", but streams keys are "TG 0 - Port: ens5np0" or "TG 0 - ens5np0"
            matched_iface = None
            if interface in self.streams:
                matched_iface = interface
            else:
                # Extract just the interface name (remove VLAN suffix if present)
                base_interface = interface.split('.')[0] if '.' in interface else interface
                # Try to find matching port key in streams
                for k in self.streams:
                    # Normalize port key: "TG 0 - Port: ens5np0" -> "ens5np0", "TG 0 - ens5np0" -> "ens5np0"
                    port_key_normalized = k.replace("Port: ", "").split(" - ")[-1] if " - " in k else k
                    if port_key_normalized == base_interface or port_key_normalized == interface:
                        matched_iface = k
                        break
                    # Also try partial match
                    if base_interface in port_key_normalized or port_key_normalized in base_interface:
                        matched_iface = k
                        break

            if not matched_iface:
                logger.info(f"[UPDATE STATS] No match found for interface '{interface}' in streams (available keys: {list(self.streams.keys())[:3]}...)")
                continue

            matched_streams = self.streams.get(matched_iface, [])

            # Get stream_id from table item (more reliable than matching by name)
            stream_id_from_table = stream_name_item.data(Qt.UserRole) if stream_name_item else None
            
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
            "Receive Bit Rate (bps)", "Errors"
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

        # print(f"✅ Traffic statistics updated: {len(statistics)} interfaces, {max_streams} max streams.")

    def update_stream_statistics_table(self, stream_stats_list):
        """Update the stream statistics table with detailed per-stream information."""
        if not hasattr(self, "stream_statistics_table") or self.stream_statistics_table is None:
            logger.debug(f"[DEBUG STREAM STATS] stream_statistics_table not found or not initialized")
            return
        
        # Set column count first (12 columns: Engine between Interface and
        # TX Count; TX Bit Rate / RX Bit Rate inserted after the pps Rate
        # columns so the live throughput readout matches the Interface
        # Statistics tab without flipping tabs).
        try:
            self.stream_statistics_table.setColumnCount(12)
            self.stream_statistics_table.setHorizontalHeaderLabels([
                "Stream Name", "Interface", "Engine", "TX Count", "RX Count",
                "TX Rate", "RX Rate", "TX Bit Rate", "RX Bit Rate",
                "Loss %", "Status", "Flow Tracking",
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
            
            # Calculate loss percentage
            if isinstance(tx_count, int) and tx_count > 0:
                if isinstance(rx_count, int):
                    loss_pct = ((tx_count - rx_count) / tx_count * 100) if tx_count > 0 else 0.0
                else:
                    loss_pct = 100.0 if flow_tracking else 0.0
            else:
                loss_pct = 0.0
            
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
                "stream_id": stream_id
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

            # Engine — show DPDK queue count if multi-queue, else "Scapy"
            if stream.get("dpdk_enable"):
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
            engine_item.setToolTip(
                f"Engine: {'DPDK tx_worker' if stream.get('dpdk_enable') else 'Scapy/kernel'}"
                + (f"\nTX queues: {stream.get('dpdk_tx_cores', 1)}" if stream.get("dpdk_enable") else "")
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
            rx_item = QTableWidgetItem(rx_display)
            rx_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if stream["flow_tracking"] and isinstance(rx_count, int) and stream["tx_count"] > 0 and rx_count == 0:
                rx_item.setForeground(QColor("#ef4444"))  # Red for 100% loss
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

            # Loss % — only meaningful when flow tracking is on AND the
            # stream is actively running. A non-flow-tracked stream has no
            # way to know rx_count, so reporting "100% loss" on those
            # (which the previous code did, in red) was misleading. Show
            # a muted "—" instead.
            loss_pct = stream["loss_pct"]
            stream_running = str(stream.get("status", "")).lower() == "running"
            if not stream["flow_tracking"]:
                loss_text = "—"
                loss_color = QColor("#9ca3af")  # muted gray
            elif not stream_running and stream["tx_count"] == 0:
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

            self.stream_statistics_table.setItem(row, 9, loss_item)

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
            self.stream_statistics_table.setItem(row, 10, status_item)

            # Flow Tracking
            flow_tracking_item = QTableWidgetItem("Yes" if stream["flow_tracking"] else "No")
            flow_tracking_item.setTextAlignment(Qt.AlignCenter)
            self.stream_statistics_table.setItem(row, 11, flow_tracking_item)
        
        # Resize columns to fit content
        self.stream_statistics_table.resizeColumnsToContents()

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
        # 9 Loss %, 10 Status, 11 Flow Tracking.
        if hasattr(self, "stream_statistics_table") and self.stream_statistics_table is not None:
            zero_for_col = {
                3: "0", 4: "0",                          # counts
                5: "0.00 pps", 6: "0.00 pps",            # rates (pps)
                7: "0.00 bps", 8: "0.00 bps",            # rates (bps)
                9: "0.00%",                              # loss
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
                            # Reset loss% color (was red/yellow/green)
                            if c == 7:
                                item.setForeground(QColor("#10b981"))
            except Exception as e:
                logger.debug(f"[CLEAR STATS] stream table reset failed: {e}")
        
        #print("Traffic statistics cleared.")
    def enable_make_server_online_menu(self):
        """Enable the 'Make Server Online' menu item to allow user-initiated retry."""
        if hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(True)