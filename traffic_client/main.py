# traffic_client/main.py
import logging
import os

# Configure logging early - set to INFO to reduce verbosity
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Disable DEBUG messages from urllib3 and other verbose libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
# urllib3.connectionpool emits a WARNING for every retry attempt
# ("Retrying (Retry(total=2, ...)) after connection broken by ..."). On a busy
# server those fire 5-10x/min as requests cross their timeouts before the
# retry layer recovers them. The retries are working as designed; the warnings
# are internal chatter. Push to ERROR — final, unrecoverable failures still
# surface through application code (which logs them at INFO/ERROR itself).
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget, QSplitter,
    QMenu, QAction, QApplication, QDockWidget
)

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QKeySequence

# Ensure Qt knows about QVector<int> when signals cross threads (older PyQt builds may lack the helper)
if hasattr(QtCore, "qRegisterMetaType"):
    QtCore.qRegisterMetaType('QVector<int>')
from widgets.devices_tab import DevicesTab
from widgets.topology_tab import TopologyTab
from capture_client import PacketCaptureClient
from traffic_client.menu_actions import TrafficGenClientMenuAction
from traffic_client.packet_capture import TrafficGenClientPacketCapture
from traffic_client.server_section import TrafficGenClientServerSection
from traffic_client.statistics_section import TrafficGenClientStatisticsSection
from traffic_client.stream_logic import TrafficGenClientStreamLogic
from traffic_client.stream_control import TrafficGenClientStreamControl
from traffic_client.dpdk_menu_actions import TrafficGenClientDPDKMenuActions
from traffic_client.rdma_menu_actions import TrafficGenClientRDMAMenuActions
from traffic_client.server_retry_workers import ServerRetryWorker, HealthCheckWorker, ConnectionManager
from utils.server_manager import ServerManager
from utils.device_server_migration import DeviceServerMigration


class TrafficGeneratorClient(
    QMainWindow,
    TrafficGenClientMenuAction,
    TrafficGenClientPacketCapture,
    TrafficGenClientServerSection,
    TrafficGenClientStatisticsSection,
    TrafficGenClientStreamLogic,
    TrafficGenClientStreamControl,
    TrafficGenClientDPDKMenuActions,
    TrafficGenClientRDMAMenuActions,
):
    # AI menu actions will be added via mixin
    pass
    def __init__(self, server_url=None, server_explicitly_provided=False):
        super().__init__()
        # Stash the canonical title so the resize-driven dimension
        # readout (_update_section_size_readout) can rebuild from a
        # clean base each time instead of appending to an
        # ever-growing string.
        #
        # v0.3.11: the base title now also reflects the active session
        # file basename (Save As / Load From can swap it) and a
        # dirty-marker asterisk when a save is in flight or pending.
        # _update_window_title() recomputes this and triggers the
        # size-readout to re-render; we set the path default here so
        # the title shows the correct file from boot, and the menu
        # actions / save / load paths call _update_window_title()
        # whenever the path or dirty state changes.
        #
        # v0.4.7: restore from QSettings before falling back to the
        # default. Operator-reported: opened the app, expected to see
        # the streams + TGs they'd been working on, got TG 0 with
        # nothing else. Root cause: they'd used Save As / Load From
        # to point the active session at a non-default file (e.g.
        # ~/Desktop/NetGen/session.json), but `_current_session_path`
        # was reset to the default `~/Documents/OSTG/session.json`
        # on every launch — so load_session() always opened the
        # (empty) default file. The user's recent work was on disk,
        # just at a different path the app had forgotten about.
        try:
            from utils.path_utils import get_session_file_path
            from PyQt5.QtCore import QSettings
            import os as _os
            default_path = get_session_file_path()
            persisted = QSettings().value(
                "current_session_path", "", type=str,
            )
            # Honor the persisted path only if it still exists on
            # disk. A moved / deleted file would otherwise wedge the
            # app at startup — better to silently fall back to the
            # default and let the operator pick a new file via Load
            # From… if they want.
            if persisted and _os.path.exists(persisted):
                self._current_session_path = persisted
            else:
                if persisted:
                    logger.info(
                        f"[STARTUP] persisted session path "
                        f"{persisted!r} no longer exists; falling "
                        f"back to default {default_path!r}"
                    )
                self._current_session_path = default_path
        except Exception:
            self._current_session_path = None
        # v0.3.11: auto-save opt-in. Default False so removing a row
        # while experimenting doesn't silently commit the deletion to
        # the active session file. QSettings-persisted so the
        # operator's choice survives a restart. File → Auto-save
        # Session toggles it. When OFF, only Ctrl+S / Save As writes.
        try:
            from PyQt5.QtCore import QSettings
            self._auto_save_enabled = QSettings().value(
                "auto_save_enabled", False, type=bool,
            )
        except Exception:
            self._auto_save_enabled = False
        # v0.3.11: unsaved-edits flag — set True whenever the
        # auto-save gate suppresses a save_session() call, cleared
        # on every successful write. closeEvent reads this to decide
        # whether to prompt "Save before quitting?". Title bar's
        # asterisk also reads it. NOT persisted — a fresh launch is
        # always clean.
        self._has_unsaved_edits = False
        self._base_window_title = "Netgen Traffic Generator"
        # _update_window_title() bakes the session basename into
        # _base_window_title; called here so the first paint shows
        # the right file. Defensive — the helper isn't wired in via
        # mixin yet if mixin order ever changes, so swallow failures.
        try:
            self._update_window_title()
        except Exception:
            self.setWindowTitle(self._base_window_title)
        self.setGeometry(100, 100, 1400, 800)
        # Track which widgets we've installed the section-size event
        # filter on, so we don't double-install if the layout is rebuilt.
        self._section_size_filter_installed = set()

        self.streams = {}
        self._last_statistics = {}
        self._last_stream_stats = {}
        self.server_interfaces = []
        self.failed_servers = []
        self.removed_interfaces = set()
        self.removed_servers = set()  # Track removed servers
        self.selected_servers = []
        self.capture_client = PacketCaptureClient()
        self.capturing_interface = None
        self.capture_filepath = None
        self.copied_stream = None
        self.copied_streams = []  # Initialize copied streams list
        self.all_devices = {}
        self._is_closing = False  # Flag to prevent new operations during shutdown
        self._force_quit_called = False  # Prevent multiple force_quit executions
        
        # Store the server URL for later use (will be added after session is loaded)
        self.server_url = server_url
        # Track if server was explicitly provided via command line (to skip loading from session.json)
        self.server_url_from_cli = server_explicitly_provided
        # Store original servers from session.json to preserve them when saving in CLI mode
        self.original_session_servers = []
        
        # Initialize enhanced connection management
        self.connection_manager = ConnectionManager()
        self.server_retry_worker = None
        self.health_check_worker = None
        self.retry_timer = None
        
        # Initialize ServerManager for multi-server support
        self.server_manager = ServerManager()

        # Root layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        self.setup_menu_bar()

        # DPDK readiness chip in the status bar (right-aligned). Polls
        # /api/dpdk/status every 30 s so an operator never has to ask
        # "is DPDK going to work today?" before enabling Use-DPDK on a
        # stream. v0.2.76 — addresses BLOCKER #3 from the DPDK audit.
        # Guarded — a construction failure can't block the main window.
        try:
            from widgets.dpdk_readiness_chip import DpdkReadinessChip
            def _resolve_url():
                try:
                    return (self.get_server_url(silent=True)
                            if hasattr(self, "get_server_url") else None)
                except Exception:
                    return None
            self.dpdk_chip = DpdkReadinessChip(_resolve_url, parent=self)
            # v0.3.11: click the chip to open Make DPDK Ready. The
            # chip exposes a `clicked` signal so red/amber operators
            # can fix the issue without hunting through Tools→DPDK.
            try:
                self.dpdk_chip.clicked.connect(self.show_dpdk_make_ready_dialog)
            except Exception as _e:
                logging.debug(f"[MAIN] DPDK chip click wiring failed: {_e}")
            self.statusBar().addPermanentWidget(self.dpdk_chip)
        except Exception as _e:
            logging.warning(f"[MAIN] DPDK readiness chip unavailable: {_e}")

        # v0.5.169: orphan-worker status chip. Polls every registered
        # TG's /api/streams/orphans every 10 s, shows "🧟 N orphans"
        # when any are detected. Click → reap dialog. Hidden when
        # zero. Operator no longer needs to ssh + ps to discover
        # untracked tx/rx_worker procs eating their HCAs.
        try:
            from widgets.orphan_chip import OrphanChip

            def _resolve_all_server_urls():
                try:
                    urls = []
                    for s in (getattr(self, "server_interfaces",
                                      None) or []):
                        addr = s.get("address")
                        if addr:
                            urls.append(str(addr))
                    return urls
                except Exception:
                    return []

            self.orphan_chip = OrphanChip(
                _resolve_all_server_urls, parent=self)
            self.statusBar().addPermanentWidget(self.orphan_chip)
        except Exception as _e:
            logging.warning(
                f"[MAIN] Orphan chip unavailable: {_e}")


        # Setup AI menu (if available)
        try:
            from traffic_client.ai_menu_actions import TrafficGenClientAIMenuActions
            # Add AI menu actions as mixin
            TrafficGenClientAIMenuActions.setup_ai_menu(self)
            # Store reference for access
            self.ai_menu_actions = TrafficGenClientAIMenuActions
        except ImportError as e:
            logging.warning(f"AI features not available: {e}")
            pass  # AI features not available

        # Top section: server tree (left) + tabs (right). Lives in the central widget.
        self.top_section = QSplitter(Qt.Horizontal)

        # Server section on the left
        self.setup_server_section()

        # Tabs on the right. Flat tab style — text-only labels with a
        # colored underline on the active tab. Reverted to this from
        # the card-style attempt in e5ba822 per user preference.
        self.tab_widget = QTabWidget()
        self.streams_tab = QWidget()
        self.devices_tab = DevicesTab(self)
        # Topology tab — read-only fabric view derived from device DB.
        # Adds a Refresh button; nothing polls in the background.
        self.topology_tab = TopologyTab(self)
        # L2 Emulation tab — frame generators for LACP / LLDP / VRRP /
        # IGMP / PIM. Backed by /api/l2/* and utils/l2_protocols.py.
        # Import is lazy-guarded so an older server without scapy
        # contrib still launches a client without crashing.
        try:
            from widgets.l2_emulation_tab import L2EmulationTab
            self.l2_emulation_tab = L2EmulationTab(self)
        except Exception as _l2_imp_err:
            logger.warning(f"L2 Emulation tab unavailable: {_l2_imp_err}")
            self.l2_emulation_tab = None
        # Stateful TCP tab — real-socket TCP traffic generator. Same
        # lazy-guard pattern as L2 so an older server's missing
        # /api/stateful_tcp/* doesn't prevent the client from starting;
        # the tab's own refresh path handles 404 → unsupported-mode.
        try:
            from widgets.stateful_tcp_tab import StatefulTcpTab
            self.stateful_tcp_tab = StatefulTcpTab(self)
        except Exception as _tcp_imp_err:
            logger.warning(f"Stateful TCP tab unavailable: {_tcp_imp_err}")
            self.stateful_tcp_tab = None
        self.tab_widget.addTab(self.streams_tab, "Streams")
        self.tab_widget.addTab(self.devices_tab, "Devices")
        self.tab_widget.addTab(self.topology_tab, "Topology")
        if self.l2_emulation_tab is not None:
            self.tab_widget.addTab(self.l2_emulation_tab, "L2 Emulation")
        if self.stateful_tcp_tab is not None:
            self.tab_widget.addTab(self.stateful_tcp_tab, "Stateful TCP")
        self.tab_widget.tabBar().setExpanding(False)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background-color: transparent;
                color: #6b7280;
                border: none;
                padding: 6px 18px;
                margin-right: 2px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                color: #1d4ed8;
                border-bottom: 3px solid #2563eb;
                background-color: transparent;
            }
            QTabBar::tab:hover:!selected {
                color: #1f2937;
            }
        """)
        self.top_section.addWidget(self.tab_widget)

        self.main_layout.addWidget(self.top_section)

        # Statistics section lives in a QDockWidget so the user can drag it out
        # into a floating window and drag it back to re-dock. Standard Qt dock
        # title bar has float (✥) and close (×) buttons.
        self.setup_traffic_statistics_section()
        self.statistics_dock = QDockWidget("Traffic Statistics", self)
        self.statistics_dock.setObjectName("trafficStatisticsDock")  # Required for saveState/restoreState
        # Title-bar tooltip — gives the user a hover affordance for the
        # detach/close behavior. Pairs with the persistent hint banner
        # inside the dock content (see statistics_section.py).
        self.statistics_dock.setToolTip(
            "Double-click to detach this pane into a floating window.\n"
            "Drag the title bar to move/dock it elsewhere.\n"
            "Close with the X — bring it back via View → Traffic Statistics Pane."
        )
        self.statistics_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.statistics_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        # Slimmer dock title bar — default Qt chrome is ~28px tall and
        # the float/close buttons read as oversized X/restore icons in
        # the top-left corner. Tighten the title bar typography +
        # padding so the chrome consumes less vertical space and reads
        # as a section heading rather than an OS window. Features
        # (movable / floatable / closable) are preserved — only the
        # visuals change.
        self.statistics_dock.setStyleSheet("""
            QDockWidget {
                titlebar-close-icon: url(none);
                titlebar-normal-icon: url(none);
                font-weight: 700;
                font-size: 11px;
                color: #6b7280;
            }
            QDockWidget::title {
                background: #f3f4f6;
                padding: 4px 10px;
                border-bottom: 1px solid #cbd5e1;
                text-align: left;
            }
            QDockWidget::close-button, QDockWidget::float-button {
                background: transparent;
                border: none;
                padding: 0px;
                icon-size: 12px;
            }
            QDockWidget::close-button:hover, QDockWidget::float-button:hover {
                background: #e5e7eb;
                border-radius: 3px;
            }
        """)
        # The QGroupBox is now the dock's content; its existing title becomes
        # redundant with the dock's title bar. Strip the QGroupBox title so we
        # don't show "Traffic Statistics" twice stacked on top of each other.
        if hasattr(self.statistics_group, "setTitle"):
            self.statistics_group.setTitle("")
        self.statistics_dock.setWidget(self.statistics_group)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.statistics_dock)
        # Keep the affordance hint in sync with float state — when the
        # dock is detached the user needs the inverse instruction
        # ("drag back / View → Re-dock to attach"). Connecting here
        # rather than inside setup_traffic_statistics_section() because
        # the dock object is owned by main.py.
        self.statistics_dock.topLevelChanged.connect(
            self._on_stats_dock_float_changed
        )
        # Prime the hint to match the current (docked) state.
        self._on_stats_dock_float_changed(self.statistics_dock.isFloating())
        # Default Qt behaviour gives the central widget all the spare
        # vertical real estate; the dock collapses to its content's
        # sizeHint, which in our case is just one stats table row tall.
        # That's the "Traffic stats section is small" complaint.
        #
        # Two-part fix:
        #  1) Set a generous minimumHeight on the dock content so the
        #     stats area is at least readable from the start.
        #  2) Override resizeEvent (below) to call resizeDocks() with
        #     a 50/50 split so window-stretches grow BOTH halves
        #     proportionally instead of giving everything to the top.
        # Calibrated to leave the streams table breathing room above.
        # The dock can grow well past this when the user drags the
        # splitter; this is just the floor.
        self.statistics_group.setMinimumHeight(200)
        self._stats_dock_min_height = 200
        # Keep `self.splitter` as an alias to top_section for any older code that
        # referenced the vertical splitter directly (defensive).
        self.splitter = self.top_section

        # Wire the dock's toggle into View menu so users who close the pane
        # have a way to bring it back. toggleViewAction() is a checkable
        # QAction whose state mirrors the dock's visibility automatically.
        if hasattr(self, "view_menu"):
            stats_toggle = self.statistics_dock.toggleViewAction()
            stats_toggle.setText("Traffic Statistics Pane")
            stats_toggle.setToolTip(
                "Show or hide the Traffic Statistics dock. Drag its title bar "
                "to detach into a floating window; drag back to re-dock."
            )
            self.view_menu.addAction(stats_toggle)

            # Explicit "Re-dock" command. Drag-back-to-edge is the Qt convention
            # but the target zone is small and unreliable on macOS, so users
            # frequently get stuck with a floating window they can't put back.
            redock_stats_action = QAction("Re-dock Traffic Statistics", self)
            redock_stats_action.setToolTip(
                "Force the Traffic Statistics window back into the main window."
            )
            redock_stats_action.triggered.connect(self._redock_statistics)
            self.view_menu.addAction(redock_stats_action)

        # Initialize stream section inside the "Streams" tab
        self.setup_stream_section(self.streams_tab)
        
        # If server was provided via command line, add it BEFORE load_session so it can discover interfaces
        if self.server_url_from_cli and self.server_url:
            # Clear any default servers and add only the CLI-provided server
            self.server_interfaces = []
            tg_id = 0  # Always use TG 0 for CLI-provided server
            # Create server entry with initial online status (will be updated when tree fetches interfaces)
            server_entry = {"tg_id": tg_id, "address": self.server_url, "online": True}
            self.server_interfaces.append(server_entry)
            logger.info(f"Using server from command line: {self.server_url} (TG {tg_id})")
            logger.info(f"Skipped loading servers from session.json (only connecting to {self.server_url})")
        
        # Load devices from session.json on startup (after all UI components are set up)
        # If server was provided via CLI, skip loading servers from session.json
        # If no server was provided via CLI, load all TGs from session.json
        self.load_session(skip_servers=self.server_url_from_cli)
        
        # Log summary of loaded servers
        if not self.server_url_from_cli:
            if self.server_interfaces:
                logger.info(f"Loaded {len(self.server_interfaces)} TG(s) from session.json:")
                for server in self.server_interfaces:
                    logger.info(f"   - TG {server.get('tg_id', '?')}: {server.get('address', 'N/A')}")
            else:
                logger.info(f"No TGs found in session.json or all were previously removed")
        
        # If server URL was set via environment variable or default (but not CLI), add it now
        if not self.server_url_from_cli and self.server_url and self.server_url not in [server["address"] for server in self.server_interfaces]:
            # Server URL was set via environment variable or default, but not CLI
            # Check if this server was previously removed
            if self.server_url not in self.removed_servers:
                # v0.3.16: use _next_tg_id helper instead of len() —
                # see traffic_client/menu_actions.py::_next_tg_id docstring.
                # Loading a session with non-contiguous tg_ids (e.g.
                # one server saved with tg_id=1) + this auto-add path
                # would otherwise collide.
                from traffic_client.menu_actions import _next_tg_id
                tg_id = _next_tg_id(self.server_interfaces)
                server_entry = {"tg_id": tg_id, "address": self.server_url, "online": True}
                self.server_interfaces.append(server_entry)
                logger.info(f"Automatically added server: {self.server_url} (TG {tg_id})")
            else:
                logger.warning(f"Server {self.server_url} was previously removed, not adding automatically")
        
        # Initialize ServerManager from server_interfaces
        if self.server_interfaces:
            self.server_manager.initialize_from_server_interfaces(self.server_interfaces)
            logger.info(f"ServerManager initialized with {len(self.server_manager.servers)} server(s)")
        
        # Migrate devices to server-aware structure
        if self.all_devices:
            DeviceServerMigration.migrate_all_devices(self.all_devices, self.server_manager)
            # Also update devices_tab if it exists
            if hasattr(self, 'devices_tab') and self.devices_tab:
                self.devices_tab.all_devices = self.all_devices.copy()
        
        # Update server tree after servers are populated (especially important for CLI-provided server)
        if hasattr(self, 'update_server_tree'):
            self.update_server_tree()
            logger.info(f"Server tree updated on startup with {len(self.server_interfaces)} server(s)")
            
            # If servers are selected by default, ensure stream table is populated
            # Use QTimer to ensure this happens after the tree is fully built
            if hasattr(self, 'selected_servers') and self.selected_servers:
                QTimer.singleShot(100, lambda: self._update_tables_after_startup())
            elif hasattr(self, 'server_tree'):
                # Even if no servers are selected, check if a TG is selected in the tree
                selected_items = self.server_tree.selectedItems()
                if selected_items:
                    QTimer.singleShot(100, lambda: self._update_tables_after_startup())
        
        # Initialize retry workers after session is loaded and servers are populated
        self._initialize_retry_workers()
        self._check_initial_server_status()
        
        # Start timers for polling stats (optimized interval)
        logger.info("[TIMER INIT] Starting timers...")
        self.timer = QTimer()
        # Timer wrapper for fetch_and_update_statistics
        def fetch_with_debug():
            try:
                self.fetch_and_update_statistics()
            except Exception as e:
                logger.error(f"[TIMER ERROR] Exception in fetch_and_update_statistics: {e}")
                import traceback
                traceback.print_exc()
        self.timer.timeout.connect(fetch_with_debug)
        self.timer.start(2000)  # every 2s to auto-update traffic statistics from database
        # print(f"[TIMER INIT] Traffic statistics timer started (active: {self.timer.isActive()}, interval: {self.timer.interval()}ms)")

        self.stream_stats_timer = QTimer()
        # print(f"[TIMER INIT] Checking for poll_stream_stats method...")
        # Verify the method exists before connecting
        if hasattr(self, 'poll_stream_stats'):
            # print(f"[TIMER INIT] poll_stream_stats method found, connecting timer...")
            # Timer wrapper for poll_stream_stats
            def poll_with_debug():
                try:
                    self.poll_stream_stats()
                except Exception as e:
                    logger.error(f"[TIMER ERROR] Exception in poll_stream_stats: {e}")
                    import traceback
                    traceback.print_exc()
            
            self.stream_stats_timer.timeout.connect(poll_with_debug)
            self.stream_stats_timer.setSingleShot(False)  # Make it repeating
            self.stream_stats_timer.start(2000)  # every 2s to auto-update stream statistics from database
            # print(f"[TIMER] Stream stats timer started (interval: 2000ms, active: {self.stream_stats_timer.isActive()}, singleShot: {self.stream_stats_timer.isSingleShot()})")
        else:
            logger.error(f"[TIMER ERROR] poll_stream_stats method not found! Available methods: {[m for m in dir(self) if 'poll' in m.lower() or 'stream' in m.lower()]}")

        # TGen service-health poll (30s). Refines the per-TG status LED
        # from a 2-state reachability light (green/red) to 3-state
        # (green=healthy, amber=reachable-but-degraded, red=offline) by
        # consulting each online server's /api/admin/health. Heavier than
        # the reachability probe (the endpoint shells out to pkg-config/
        # lsmod/proc), so it runs at a modest 30s cadence in a background
        # thread. Safe re: QThread lifecycle via the global keepalive.
        self.server_health_timer = QTimer()
        if hasattr(self, "poll_server_health"):
            def _poll_server_health_safe():
                try:
                    self.poll_server_health()
                except Exception as e:
                    logger.error(f"[TIMER ERROR] Exception in poll_server_health: {e}")
            self.server_health_timer.timeout.connect(_poll_server_health_safe)
            self.server_health_timer.setSingleShot(False)
            self.server_health_timer.start(30000)  # every 30s
    
    def _update_tables_after_startup(self):
        """Update device and stream tables after startup when servers are selected."""
        # Update device table if devices exist
        if hasattr(self, "devices_tab") and hasattr(self, "all_devices"):
            self.devices_tab.update_device_table(self.all_devices)
        
        # Update stream table to show streams for selected servers
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
        
        # Update statistics (deferred to avoid blocking startup)
        # Use QTimer to defer statistics fetching after UI is fully rendered
        if hasattr(self, "fetch_and_update_statistics"):
            QTimer.singleShot(500, lambda: self.fetch_and_update_statistics())

    def _redock_statistics(self):
        """Force the Traffic Statistics dock back into the main window.

        QDockWidget supports drag-back-to-redock natively, but the target
        zone is small and unreliable on macOS. Users get stranded floating
        windows they can't put back. This action fixes that:
          - If the dock is closed, show it.
          - If the dock is floating, re-attach it to the bottom area.
          - Always re-add via addDockWidget so the placement is explicit.
        """
        if not hasattr(self, "statistics_dock"):
            return
        # addDockWidget is idempotent and also handles the "currently floating"
        # case: it pulls the dock back into the main window's bottom area.
        self.addDockWidget(Qt.BottomDockWidgetArea, self.statistics_dock)
        self.statistics_dock.setFloating(False)
        self.statistics_dock.show()
        self.statistics_dock.raise_()

    def _on_stats_dock_float_changed(self, floating):
        """Swap the hint banner copy when the user detaches/re-attaches.

        Connected to QDockWidget.topLevelChanged. The banner is owned
        by statistics_section.py as self.stats_dock_hint; we just
        rewrite its text + tooltip + accent stripe color so the
        instruction matches the current state.
        """
        hint = getattr(self, "stats_dock_hint", None)
        if hint is None:
            return
        if floating:
            hint.setText(
                "↩  Double-click the title bar (or drag it back) to "
                "re-attach this pane. Or use "
                "View → Re-dock Traffic Statistics."
            )
            # Amber accent stripe so the floating-state hint reads
            # distinct from the docked-state one at a glance.
            accent = "#f59e0b"
            tip = (
                "The pane is currently floating.\n"
                "Drag the title bar back to the bottom edge of the\n"
                "main window to re-dock, or pick\n"
                "View → Re-dock Traffic Statistics."
            )
        else:
            hint.setText(
                "💡  Double-click the title bar (or drag it) to pop the "
                "Traffic Statistics out into a separate window."
            )
            accent = "#93c5fd"
            tip = (
                "Click the 'X' icon in the title bar to close the pane.\n"
                "Bring it back any time via View → Traffic Statistics Pane,\n"
                "or use 'Re-dock Traffic Statistics' if it gets stranded."
            )
        hint.setStyleSheet(
            "QLabel {"
            "  color: #6b7280;"
            "  font-size: 11px;"
            "  font-style: italic;"
            "  padding: 4px 10px;"
            "  background-color: #f9fafb;"
            f"  border-left: 3px solid {accent};"
            "  border-radius: 2px;"
            "}"
        )
        hint.setToolTip(tip)
        # Keep the dock's own title-bar tooltip in sync too — same
        # text as the banner's tooltip so the user gets a consistent
        # message whether they hover the banner or the title bar.
        if hasattr(self, "statistics_dock") and self.statistics_dock is not None:
            self.statistics_dock.setToolTip(tip)

    def _balance_stats_dock(self):
        """Size the Traffic Statistics dock to its calibrated default
        height (465px) — only on initial show.

        Earlier revisions targeted self.height() // 2 (a 50/50 split)
        and re-applied on every window resize. That gave the stats
        dock more room than its content actually needs and ate into
        the streams table. The fixed 465 is the right default for
        the live-stats tables; once set, Qt's natural layout takes
        over (window stretches grow the central widget; the dock
        stays at whatever the user has set). The user can drag the
        splitter for a different ratio at any time.
        """
        if not hasattr(self, "statistics_dock"):
            return
        if self.statistics_dock.isFloating() or not self.statistics_dock.isVisible():
            return
        try:
            target = 465  # matches "Traffic stats = 1500×465" target
            # Don't push the dock taller than 70% of the window — on a
            # very small screen we'd otherwise leave the streams table
            # too cramped to be useful.
            target = min(target, int(self.height() * 0.70))
            self.resizeDocks([self.statistics_dock], [target], Qt.Vertical)
        except Exception as e:
            logger.debug(f"[LAYOUT] resizeDocks failed: {e}")

    def _install_section_size_filter(self):
        """Wire QEvent.Resize on the inner section widgets to the
        title-bar dimension readout.

        QMainWindow.resizeEvent only fires for *window* resizes, so the
        readout was frozen while the user dragged the dock-splitter
        handle in the middle of the window. Installing an event filter
        on the inner widgets (top_section, statistics_group, and the
        dock itself) catches their own resize events too. Idempotent —
        each widget is filter-tracked in self._section_size_filter_installed
        so we don't stack filters if the layout is ever rebuilt.
        """
        from PyQt5.QtCore import QEvent  # local import — keeps top of file tidy
        targets = [
            getattr(self, "top_section", None),
            getattr(self, "statistics_group", None),
            getattr(self, "statistics_dock", None),
        ]
        for w in targets:
            if w is None:
                continue
            if id(w) in self._section_size_filter_installed:
                continue
            w.installEventFilter(self)
            self._section_size_filter_installed.add(id(w))

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        # Refresh the title-bar readout on any inner section's resize.
        # Deferred via singleShot(0) so we read AFTER Qt has applied the
        # geometry change, not in the middle of it.
        if event.type() == QEvent.Resize and id(obj) in self._section_size_filter_installed:
            QTimer.singleShot(0, self._update_section_size_readout)
        return super().eventFilter(obj, event)

    def _update_section_size_readout(self):
        """Append live section dimensions to the window title bar.

        The title becomes:
            'Netgen Traffic Generator  —  Window 1920×1080 │ Top 1920×540 │ Stats 1920×540'

        Updates on every showEvent/resizeEvent so the user can see
        exactly how the layout splits as the window grows. Helpful
        when tuning the stats-dock balance ratio or debugging why
        one half won't shrink past a certain size. Lives in the
        title bar (the only always-visible OS-supplied chrome) so
        we don't have to give up any pixels inside the app for it.
        """
        try:
            win_w, win_h = self.width(), self.height()
            parts = [f"Window {win_w}×{win_h}"]

            top = getattr(self, "top_section", None)
            if top is not None and top.isVisible():
                parts.append(f"Top {top.width()}×{top.height()}")

            sg = getattr(self, "statistics_group", None)
            dock = getattr(self, "statistics_dock", None)
            if sg is not None and dock is not None and dock.isVisible():
                if dock.isFloating():
                    parts.append(f"Stats(floating) {sg.width()}×{sg.height()}")
                else:
                    parts.append(f"Stats {sg.width()}×{sg.height()}")

            base = getattr(self, "_base_window_title", "Netgen Traffic Generator")
            self.setWindowTitle(f"{base}  —  " + "  │  ".join(parts))
        except Exception as e:
            logger.debug(f"[LAYOUT] section-size readout failed: {e}")

    def showEvent(self, event):
        """Re-balance the stats dock once the window is visible.

        Has to happen after Qt has computed the actual window geometry
        — calling resizeDocks() in __init__ before show() has no
        effect because the layout system hasn't run yet.
        """
        super().showEvent(event)
        # Defer one event-loop tick so geometry is settled. QTimer.singleShot(0)
        # runs after the show is complete.
        QTimer.singleShot(0, self._balance_stats_dock)
        # Install the inner-section resize filter once the layout is up
        # (idempotent), then prime the title-bar readout.
        QTimer.singleShot(0, self._install_section_size_filter)
        QTimer.singleShot(0, self._update_section_size_readout)

    def resizeEvent(self, event):
        """Update the title-bar dimension readout on window resize.

        Earlier revisions also called _balance_stats_dock here so
        window-stretches grew the dock proportionally. We've since
        switched to a fixed 465 default — Qt's natural layout on
        window resize is what we want now (extra height goes to the
        central widget; dock stays at its set size). User can drag
        the splitter for a different ratio.
        """
        super().resizeEvent(event)
        # Refresh the title-bar dimension readout deferred so we
        # read AFTER Qt has applied the new geometry.
        QTimer.singleShot(0, self._update_section_size_readout)

    def closeEvent(self, event):
        """Handle application close event - cleanup threads and resources."""
        if self._is_closing:
            # Already in the process of closing, ignore
            event.accept()
            return

        # v0.3.11: unsaved-edits guard. When auto-save is OFF and the
        # user has pending edits, prompt before quitting so they
        # don't silently lose work. Auto-save ON means edits have
        # already been written, so no prompt is needed. The prompt
        # offers three outcomes:
        #
        #   * Save → blocking save, then close. Failure still allows
        #     close (with an error message) since refusing to close
        #     after a save error would trap the operator.
        #   * Discard → close without saving. Edits are lost; explicit
        #     user choice.
        #   * Cancel → ignore the close event; the app keeps running.
        #
        # The check uses _is_closing as a once-per-app guard so we
        # never re-prompt after the user has answered.
        if (getattr(self, "_has_unsaved_edits", False)
                and not getattr(self, "_auto_save_enabled", False)):
            from PyQt5.QtWidgets import QMessageBox
            import os as _os
            _path = getattr(self, "_current_session_path", None)
            _name = _os.path.basename(_path) if _path else "session.json"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Unsaved Edits")
            box.setText("You have unsaved edits in this session.")
            box.setInformativeText(
                f"Auto-save is OFF. Closing now will lose changes "
                f"that haven't been written to {_name}."
            )
            box.setStandardButtons(
                QMessageBox.Save | QMessageBox.Discard
                | QMessageBox.Cancel,
            )
            box.setDefaultButton(QMessageBox.Save)
            choice = box.exec_()
            if choice == QMessageBox.Cancel:
                # Operator changed their mind — abort the close.
                event.ignore()
                return
            if choice == QMessageBox.Save:
                try:
                    success, msg = self.save_session(
                        blocking=True, manual=True,
                    )
                except Exception as exc:
                    success, msg = False, str(exc)
                if not success:
                    # Save failed — let them know but don't trap them
                    # in the app. They explicitly chose Save and we
                    # tried; the prompt is one-shot.
                    QMessageBox.warning(
                        self, "Save Failed",
                        f"Failed to save session before closing:\n\n"
                        f"{msg}\n\nClosing anyway. Edits are lost.",
                    )
            # Discard / Save (success or failed) → fall through to
            # the normal close path.

        self._is_closing = True

        # Persist topology canvas layout so the next session opens with
        # the same node positions. Cheap (QSettings sync) and best-effort.
        try:
            if hasattr(self, "topology_tab") and self.topology_tab:
                self.topology_tab.save_layout()
        except Exception as _exc:
            logger.debug(f"[CLEANUP] topology save_layout: {_exc}")

        # L2 emulation tab — stop the poll timer so it doesn't fire
        # mid-shutdown and queue a fetch against a half-gone server.
        try:
            if getattr(self, "l2_emulation_tab", None):
                self.l2_emulation_tab.cleanup_threads()
        except Exception as _exc:
            logger.debug(f"[CLEANUP] l2_emulation_tab cleanup: {_exc}")

        # Stateful TCP tab — same pattern: drain the 3s poll worker
        # before the QApplication tears the event loop down.
        try:
            if getattr(self, "stateful_tcp_tab", None):
                self.stateful_tcp_tab.cleanup_threads()
        except Exception as _exc:
            logger.debug(f"[CLEANUP] stateful_tcp_tab cleanup: {_exc}")
        
        # CRITICAL: Clean up save worker before closing
        if hasattr(self, '_save_worker') and self._save_worker is not None:
            try:
                save_worker = self._save_worker
                self._save_worker = None  # Clear reference first
                
                if save_worker.isRunning():
                    logger.info("[CLEANUP] Waiting for save worker to finish...")
                    save_worker.quit()  # Request thread to stop
                    if not save_worker.wait(3000):
                        logger.warning("[CLEANUP] Force terminating save worker...")
                        save_worker.terminate()
                        save_worker.wait(1000)
                
                # Only deleteLater after thread has definitely stopped
                try:
                    if not save_worker.isRunning():
                        save_worker.deleteLater()
                    else:
                        logger.warning("[CLEANUP] Save worker still running after cleanup attempt")
                except RuntimeError:
                    # Object already deleted, ignore
                    pass
            except Exception as exc:
                logger.error(f"[CLEANUP] Error cleaning up save worker: {exc}")
        logger.info("[CLEANUP] Application closing, cleaning up threads...")
        save_worker_status = 'N/A'
        try:
            save_worker = getattr(self, '_save_worker', None)
            if save_worker is not None:
                save_worker_status = save_worker.isRunning()
        except (RuntimeError, AttributeError):
            save_worker_status = 'deleted'
        
        retry_worker_status = 'N/A'
        try:
            if self.server_retry_worker:
                retry_worker_status = self.server_retry_worker.isRunning()
        except (RuntimeError, AttributeError):
            retry_worker_status = 'deleted'
        
        health_worker_status = 'N/A'
        try:
            if self.health_check_worker:
                health_worker_status = self.health_check_worker.isRunning()
        except (RuntimeError, AttributeError):
            health_worker_status = 'deleted'
        
        logger.info(f"[CLEANUP] Active thread summary -> "
              f"operation_worker={getattr(self.devices_tab, 'operation_worker', None)}, "
              f"arp_worker={getattr(self.devices_tab, 'arp_check_worker', None)}, "
              f"bulk_arp_worker={getattr(self.devices_tab, 'bulk_arp_worker', None)}, "
              f"retry_worker_running={retry_worker_status}, "
              f"health_worker_running={health_worker_status}, "
              f"save_worker_running={save_worker_status}")
        
        # Stop all timers first
        if hasattr(self, 'devices_tab') and self.devices_tab:
            if hasattr(self.devices_tab, 'status_timer') and self.devices_tab.status_timer:
                logger.info("[CLEANUP] Stopping status timer...")
                self.devices_tab.status_timer.stop()
        # Stop main window timers
        if hasattr(self, 'timer') and self.timer:
            try:
                logger.info("[CLEANUP] Stopping main statistics timer...")
                self.timer.stop()
            except Exception:
                pass
        if hasattr(self, 'stream_stats_timer') and self.stream_stats_timer:
            try:
                logger.info("[CLEANUP] Stopping stream statistics timer...")
                self.stream_stats_timer.stop()
            except Exception:
                pass
        # Stop server section debounce timer if present
        if hasattr(self, '_stream_table_update_timer') and getattr(self, '_stream_table_update_timer', None):
            try:
                logger.info("[CLEANUP] Stopping stream table update timer...")
                self._stream_table_update_timer.stop()
            except Exception:
                pass
        
        # Clean up devices tab threads
        if hasattr(self, 'devices_tab') and self.devices_tab:
            logger.info("[CLEANUP] Invoking devices_tab.cleanup_threads()...")
            self.devices_tab.cleanup_threads()
            logger.info("[CLEANUP] Completed devices_tab.cleanup_threads()")
        
        # Clean up any stream timers
        if hasattr(self, '_stop_timers'):
            for timer in self._stop_timers.values():
                if timer and hasattr(timer, 'stop'):
                    timer.stop()
            self._stop_timers.clear()
        
        # Clean up retry workers
        logger.info("[CLEANUP] Stopping retry workers...")
        if self.server_retry_worker:
            self.server_retry_worker.stop()
            self.server_retry_worker.wait(3000)  # Wait up to 3 seconds
        if self.health_check_worker:
            self.health_check_worker.stop()
            self.health_check_worker.wait(3000)  # Wait up to 3 seconds
        # Ensure any lingering save worker is stopped (belt and suspenders)
        save_worker = getattr(self, "_save_worker", None)
        if save_worker:
            try:
                self._save_worker = None  # Clear reference first
                if save_worker.isRunning():
                    logger.info("[CLEANUP] Waiting for save worker to finish...")
                    save_worker.quit()  # Request thread to stop
                    if not save_worker.wait(3000):
                        logger.warning("[CLEANUP] Force terminating save worker...")
                        save_worker.terminate()
                        save_worker.wait(500)
                
                # Only deleteLater after thread has definitely stopped
                if not save_worker.isRunning():
                    save_worker.deleteLater()
            except RuntimeError:
                # Object already deleted, ignore
                    pass
            except Exception as exc:
                logger.error(f"[CLEANUP] Error in final save worker cleanup: {exc}")
        
        # Close connection manager
        if self.connection_manager:
            self.connection_manager.close()
        
        logger.info("[CLEANUP] Retry workers stopped")
        logger.info(f"[CLEANUP] Post-stop thread status -> "
              f"retry_worker_running={self.server_retry_worker.isRunning() if self.server_retry_worker else 'N/A'}, "
              f"health_worker_running={self.health_check_worker.isRunning() if self.health_check_worker else 'N/A'}")
        
        # Save session before closing (blocking to avoid lingering worker threads)
        try:
            result = self.save_session(blocking=True)
            if isinstance(result, tuple):
                success, message = result
                if not success:
                    logger.error(f"[CLEANUP] Session save reported error during shutdown: {message}")
        except Exception as e:
            logger.error(f"[CLEANUP] Failed to save session: {e}")
        finally:
            save_worker = getattr(self, "_save_worker", None)
            logger.info(f"[CLEANUP] Save worker cleanup state -> exists={bool(save_worker)}, "
                  f"isRunning={save_worker.isRunning() if save_worker else 'N/A'}")
        
        # Force quit the application after a short delay to allow cleanup
        self._schedule_force_quit()
        event.ignore()  # Don't accept the event yet, wait for force_quit
    
    def _schedule_force_quit(self, delay=100):
        """Schedule force quit after optional delay, avoiding duplicate scheduling."""
        if self._force_quit_called:
            return
        logger.info("[CLEANUP] Cleanup completed, forcing application exit...")
        QTimer.singleShot(delay, self.force_quit)
    
    def force_quit(self):
        """Force quit the application after cleanup."""
        if self._force_quit_called:
            return
        self._force_quit_called = True
        logger.info("[CLEANUP] Force quitting application...")
        QApplication.quit()

    def _initialize_retry_workers(self):
        """Initialize the retry and health check workers."""
        try:
            # print("[RETRY WORKERS] Initializing enhanced server retry system...")
            # print(f"[RETRY WORKERS] Server interfaces count: {len(self.server_interfaces)}")
            
            # Initialize health check worker (disabled temporarily to prevent freezing)
            # self.health_check_worker = HealthCheckWorker(self.server_interfaces)
            # self.health_check_worker.health_status_updated.connect(self._on_server_health_updated)
            # self.health_check_worker.server_interfaces_updated.connect(self._on_server_interfaces_updated)
            # self.health_check_worker.start()
            # print("[RETRY WORKERS] Health check worker disabled temporarily")
            
            # Initialize retry worker (disabled temporarily to prevent freezing)
            # self.server_retry_worker = ServerRetryWorker([])
            # self.server_retry_worker.server_reconnected.connect(self._on_server_reconnected)
            # self.server_retry_worker.server_still_failed.connect(self._on_server_still_failed)
            # self.server_retry_worker.retry_progress.connect(self._on_retry_progress)
            # self.server_retry_worker.start()
            # print("[RETRY WORKERS] Server retry worker disabled temporarily")
            
            # print("[RETRY WORKERS] Enhanced retry system initialized successfully")
            pass  # All retry workers are disabled, nothing to initialize
        except Exception as e:
            logger.error(f"[RETRY WORKERS ERROR] Failed to initialize retry workers: {e}")
            import traceback
            logger.error(f"[RETRY WORKERS ERROR] Traceback: {traceback.format_exc()}")

    def _check_initial_server_status(self):
        """Check initial server status and enable menu if servers are offline."""
        try:
            # print("[INITIAL STATUS CHECK] Checking initial server status...")
            offline_servers = [s for s in self.server_interfaces if s.get("online") is False]
            # print(f"[INITIAL STATUS CHECK] Found {len(offline_servers)} offline servers")
            
            if offline_servers:
                # print("[INITIAL STATUS CHECK] Enabling 'Make Server Online' menu")
                if hasattr(self, 'make_server_online_action'):
                    self.make_server_online_action.setEnabled(True)
            else:
                # print("[INITIAL STATUS CHECK] All servers online, menu remains disabled")
                pass
        except Exception as e:
            logger.error(f"[INITIAL STATUS CHECK ERROR] Error checking initial status: {e}")

    def _on_server_health_updated(self, server, is_online):
        """Handle server health status updates."""
        server_address = server.get("address")
        logger.info(f"[HEALTH UPDATE] Server {server_address}: {'online' if is_online else 'offline'}")
        
        # Update server status icon
        if hasattr(self, 'update_server_status_icon'):
            self.update_server_status_icon(server, is_online)
        
        # If server came back online, remove from failed list
        if is_online and server in self.failed_servers:
            self.failed_servers.remove(server)
            logger.info(f"[HEALTH UPDATE] Removed {server_address} from failed servers list")
            
            # Update "Make Server Online" menu state
            if not self.failed_servers and hasattr(self, 'make_server_online_action'):
                self.make_server_online_action.setEnabled(False)
        
        # If server went offline, add to failed list
        elif not is_online and server not in self.failed_servers:
            self.failed_servers.append(server)
            logger.warning(f"[HEALTH UPDATE] Added {server_address} to failed servers list")
            
            # Add to retry worker
            if self.server_retry_worker:
                self.server_retry_worker.add_failed_server(server)
            
            # Update "Make Server Online" menu state
            if hasattr(self, 'make_server_online_action'):
                self.make_server_online_action.setEnabled(True)

    def _on_server_interfaces_updated(self, server, interfaces):
        """Handle server interfaces updates."""
        server["interfaces"] = interfaces
        logger.info(f"[INTERFACES UPDATE] Updated interfaces for {server.get('address')}: {len(interfaces)} interfaces")

    def _on_server_reconnected(self, server):
        """Handle successful server reconnection."""
        server_address = server.get("address")
        logger.info(f"[RETRY SUCCESS] Server {server_address} reconnected successfully!")
        
        # Update server status icon
        if hasattr(self, 'update_server_status_icon'):
            self.update_server_status_icon(server, True)
        
        # Remove from failed list
        if server in self.failed_servers:
            self.failed_servers.remove(server)
        
        # Update "Make Server Online" menu state
        if not self.failed_servers and hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(False)
        
        # Refresh server tree
        if hasattr(self, 'update_server_tree'):
            self.update_server_tree()

    def _on_server_still_failed(self, server, error_message):
        """Handle server still failing after retries."""
        server_address = server.get("address")
        logger.error(f"[RETRY FAILED] Server {server_address} still failed: {error_message}")

    def _on_retry_progress(self, server_address, status_message):
        """Handle retry progress updates."""
        logger.info(f"[RETRY PROGRESS] {server_address}: {status_message}")

    def check_all_device_arp_status(self):
        """Check ARP status for all devices and update UI accordingly."""
        try:
            if not hasattr(self, 'devices_tab') or not hasattr(self.devices_tab, 'devices_table'):
                return
            
            devices_table = self.devices_tab.devices_table
            if not devices_table:
                return
            
            # Check each device in the table
            for row in range(devices_table.rowCount()):
                device_name_item = devices_table.item(row, self.devices_tab.COL["Device Name"])
                if not device_name_item:
                    continue
                
                device_name = device_name_item.text()
                
                # Find device in all_devices data structure
                device_info = None
                for iface, devices in self.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            device_info = device
                            break
                    if device_info:
                        break
                
                if device_info:
                    # Check ARP resolution
                    arp_resolved, arp_status = self.devices_tab._check_arp_resolution_sync(device_info)
                    
                    # Update status icon
                    self.devices_tab.update_device_status_icon(row, arp_resolved)
                    
                    # Update button tooltips (buttons are now separate)
                    if hasattr(self.devices_tab, 'ping_button'):
                        self.devices_tab.ping_button.setToolTip("Ping Test")
                    if hasattr(self.devices_tab, 'arp_button'):
                        self.devices_tab.arp_button.setToolTip("Send ARP")
                            
        except Exception as e:
            logger.error(f"[ARP Status Check] Error: {e}")

    def setup_menu_bar(self):
        """Set up the menu bar for server and stream management."""
        menu_bar = self.menuBar()

        # File menu — session + topology I/O. v0.4.9: Add/Remove
        # TGen Chassis used to live here too (top of File menu), but
        # they're chassis-management actions, not file actions —
        # operators kept hunting for them under Server. Moved to the
        # top of the Server menu (below). The Ctrl+N shortcut moves
        # with them.
        file_menu = QMenu("&File", self)
        menu_bar.addMenu(file_menu)

        save_session_action = QAction("Save Session", self)
        save_session_action.setShortcut(QKeySequence.Save)
        save_session_action.setStatusTip(
            "Save the current session to the active file "
            "(default: session.json in the data dir)"
        )
        # v0.3.11: pass manual=True so the auto-save-disabled gate
        # doesn't suppress an explicit Ctrl+S. Without this kwarg the
        # menu action would silently no-op whenever auto-save is off.
        save_session_action.triggered.connect(
            lambda: self.save_session(manual=True)
        )
        file_menu.addAction(save_session_action)

        # v0.3.11: Save As / Load From / Recent — operator wants
        # named snapshots (baseline / stress / evpn / etc) instead of
        # the single fixed slot. Save As changes the active path so
        # subsequent Ctrl+S writes to the new file. Load From repoints
        # at an existing snapshot.
        save_session_as_action = QAction("Save Session As…", self)
        save_session_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_session_as_action.setStatusTip(
            "Save the current session to a chosen file. The chosen "
            "file becomes the new active session for subsequent saves."
        )
        save_session_as_action.triggered.connect(self.save_session_as)
        file_menu.addAction(save_session_as_action)

        load_session_from_action = QAction("Load Session From…", self)
        load_session_from_action.setStatusTip(
            "Open a previously-saved session file. Becomes the new "
            "active session for subsequent Save operations."
        )
        load_session_from_action.triggered.connect(self.load_session_from)
        file_menu.addAction(load_session_from_action)

        # Recent Sessions — submenu rebuilt after every save/load via
        # `_rebuild_recent_sessions_menu`. Created here so the menu
        # entry exists from boot; populated lazily once the recent
        # list helper has been imported.
        self.recent_sessions_menu = QMenu("Recent Sessions", self)
        file_menu.addMenu(self.recent_sessions_menu)
        try:
            self._rebuild_recent_sessions_menu()
        except Exception as _e:
            import logging as _lg
            _lg.warning(f"[FILE MENU] recent-sessions build failed: {_e}")

        # v0.3.11: Auto-save Session — checkable. Default OFF
        # (initialized from QSettings above). When OFF, only Ctrl+S
        # and Save As… write to disk; edit handlers (BGP / OSPF /
        # ISIS / DHCP / VXLAN apply, stream remove, inline edits)
        # become no-ops at the save layer. When ON, the historical
        # every-edit-writes behaviour returns. Operator's choice
        # persists across restarts.
        self.auto_save_action = QAction("Auto-save Session", self)
        self.auto_save_action.setCheckable(True)
        self.auto_save_action.setChecked(
            getattr(self, "_auto_save_enabled", False),
        )
        self.auto_save_action.setStatusTip(
            "When ON, every edit (apply, stream remove, inline edit) "
            "writes to the active session file. When OFF (default), "
            "only Ctrl+S and Save As… write — safer when experimenting."
        )
        self.auto_save_action.toggled.connect(self.toggle_auto_save_enabled)
        file_menu.addAction(self.auto_save_action)

        file_menu.addSeparator()

        # Topology export / import — round-trip every configured device
        # as a portable JSON file. Pairs with the
        # /api/devices/{export,import} endpoints on the server.
        export_topology_action = QAction("Export Devices…", self)
        export_topology_action.setStatusTip(
            "Save every configured device on the selected server to a JSON file"
        )
        export_topology_action.triggered.connect(self.export_devices_to_file)
        file_menu.addAction(export_topology_action)

        import_topology_action = QAction("Import Devices…", self)
        import_topology_action.setStatusTip(
            "Recreate devices on the selected server from a previously-exported JSON file"
        )
        import_topology_action.triggered.connect(self.import_devices_from_file)
        file_menu.addAction(import_topology_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self)
        settings_action.setStatusTip("Application preferences (poll intervals, defaults)")
        settings_action.triggered.connect(self.open_settings_dialog)
        file_menu.addAction(settings_action)

        # Edit menu
        edit_menu = QMenu("&Edit", self)
        menu_bar.addMenu(edit_menu)

        copy_stream_action = QAction("Copy Stream", self)
        copy_stream_action.setShortcut(QKeySequence.Copy)
        copy_stream_action.triggered.connect(self.copy_selected_stream)
        edit_menu.addAction(copy_stream_action)

        paste_stream_action = QAction("Paste Stream", self)
        paste_stream_action.setShortcut(QKeySequence.Paste)
        paste_stream_action.triggered.connect(self.paste_stream_to_interface)
        edit_menu.addAction(paste_stream_action)

        edit_menu.addSeparator()

        copy_device_action = QAction("Copy Device", self)
        copy_device_action.triggered.connect(self.copy_selected_device)
        edit_menu.addAction(copy_device_action)

        paste_device_action = QAction("Paste Device", self)
        paste_device_action.triggered.connect(self.paste_device_to_interface)
        edit_menu.addAction(paste_device_action)

        # Server menu — chassis-level actions (add/remove, online
        # state, restart, reboot). v0.4.9: Add/Remove TGen Chassis
        # moved here from the File menu — they're chassis-management
        # actions, not file I/O, and operators kept looking for them
        # under Server (where every other TGen action lives). The
        # Ctrl+N shortcut comes along for the ride.
        server_menu = QMenu("&Server", self)
        menu_bar.addMenu(server_menu)

        add_server_action = QAction("Add TGEN Chassis...", self)
        add_server_action.setShortcut(QKeySequence("Ctrl+N"))
        add_server_action.triggered.connect(self.add_server_interface)
        server_menu.addAction(add_server_action)

        remove_server_action = QAction("Remove TGEN Chassis", self)
        remove_server_action.triggered.connect(self.remove_selected_server)
        server_menu.addAction(remove_server_action)

        server_menu.addSeparator()

        self.make_server_online_action = QAction("Make Selected Servers Online", self)
        self.make_server_online_action.setEnabled(False)
        # Tooltip explains why the action is disabled until a failed server is selected
        self.make_server_online_action.setToolTip(
            "Select an offline Tgen chassis in the server pane to enable this action."
        )
        self.make_server_online_action.triggered.connect(self.make_failed_servers_online)
        server_menu.addAction(self.make_server_online_action)

        # v0.3.10: inverse of "Make Selected Servers Online". Operator
        # asked for a way to manually mark a server offline — useful
        # when they want to quickly silence the polling/stats for a
        # specific TG while keeping others active. Not persistent:
        # the next health probe will flip it back if it's actually
        # reachable. Operator can re-mark or simply remove the
        # chassis if they want it permanently silent.
        self.mark_server_offline_action = QAction(
            "Mark Selected Servers Offline", self,
        )
        # Always enabled — the handler validates selection + filters to
        # currently-online TGs, surfacing a clear info dialog if there's
        # nothing to mark. Tying enablement to "at least one online
        # server is selected" would need a selection-change subscription
        # for marginal UX value (operator gets the same outcome either
        # way: click → "select a TG first" or click → confirm).
        self.mark_server_offline_action.setEnabled(True)
        self.mark_server_offline_action.setToolTip(
            "Mark selected online TGen chassis as offline. Next health "
            "probe will flip them back if they're actually reachable. "
            "Use this to quickly silence a TG without removing it."
        )
        self.mark_server_offline_action.triggered.connect(
            self.mark_selected_servers_offline
        )
        server_menu.addAction(self.mark_server_offline_action)

        # v0.5.3: Restart TGEN Service and Reboot Physical Server
        # moved from this Server menu to the AddTGenDialog ("Add
        # TGEN Chassis..."). Both are chassis-specific actions —
        # the AddTGenDialog already shows per-chassis health, LED,
        # and version columns, so it's the natural home. The
        # backing methods (self.restart_server / self.reboot_server)
        # stay in menu_actions.py for backwards compat with any
        # operator scripts that hooked them; only the menu entries
        # are removed.

        # Show tooltips for menu items (Qt hides them by default in menus)
        server_menu.setToolTipsVisible(True)

        # Capture menu
        capture_menu = QMenu("&Capture", self)
        menu_bar.addMenu(capture_menu)

        self.start_capture_action = QAction("Start Packet Capture", self)
        self.start_capture_action.triggered.connect(self.start_packet_capture)
        capture_menu.addAction(self.start_capture_action)

        self.stop_capture_action = QAction("Stop Packet Capture", self)
        self.stop_capture_action.triggered.connect(self.stop_packet_capture)
        self.stop_capture_action.setEnabled(False)
        capture_menu.addAction(self.stop_capture_action)

        # View menu — toggles for detachable/closable panes. Populated later
        # in __init__ once the dock widgets exist (via _wire_view_menu_dock_toggles).
        self.view_menu = QMenu("&View", self)
        menu_bar.addMenu(self.view_menu)

        # Tools menu — host the DPDK and AI Assistant submenus together
        tools_menu = QMenu("&Tools", self)
        menu_bar.addMenu(tools_menu)
        # Stash on self so the AI mixin (added later in __init__) can attach into Tools
        self.tools_menu = tools_menu

        # DPDK lives as a submenu under Tools
        dpdk_menu = QMenu("DPDK", self)
        # Show tooltips on hover (Qt menus hide them by default)
        dpdk_menu.setToolTipsVisible(True)
        tools_menu.addMenu(dpdk_menu)

        # v0.5.18: menu restructured from 10 flat items into 3 tiers
        # (primary / diagnostics / advanced). Pre-v0.5.18 the menu had
        # Quick Start Wizard + Make DPDK Ready as separate top-level
        # items + 5 atomic actions cluttering the bottom — operator
        # confusion about which to pick was operator-reported. After:
        #
        #   ★ Setup DPDK                  — primary entry point
        #     Blast a Flow                — separate use case
        #     ─────────
        #     Diagnostics                 — merged Status + Verify
        #     ─────────
        #     Advanced ▸                  — submenu with atomic actions
        #         Quick Start Wizard      —   guided per-step alternative
        #         Bind / Unbind Interface
        #         Configure Hugepages
        #         Configure IOMMU
        #         Load VFIO Modules

        # ─── primary: one-click setup ──────────────────────────────
        dpdk_setup_action = QAction("★ Setup DPDK...", self)
        dpdk_setup_action.setToolTip(
            "One-click DPDK setup. Surveys the server, runs install → "
            "IOMMU → VFIO → hugepages → NIC bind in sequence. Skips "
            "already-completed steps. Pauses on reboot.\n\n"
            "Total time: ~5-15 min on a fresh host (most of it is the "
            "DPDK source build).\n\n"
            "First-time / guided alternative: Advanced → Quick Start "
            "Wizard."
        )
        dpdk_setup_action.triggered.connect(self.show_dpdk_make_ready_dialog)
        dpdk_menu.addAction(dpdk_setup_action)

        dpdk_blast_flow_action = QAction("Blast a Flow...", self)
        dpdk_blast_flow_action.setToolTip(
            "Demo / smoke-test. Makes DPDK ready, then starts a "
            "64-byte UDP flow at line rate on the bound NIC. One "
            "click from a fresh server to packets flying. The "
            "flow is ephemeral (server-side only, not in the "
            "Streams tab); the dialog has a Stop button."
        )
        dpdk_blast_flow_action.triggered.connect(self.show_dpdk_blast_flow_dialog)
        dpdk_menu.addAction(dpdk_blast_flow_action)

        dpdk_menu.addSeparator()

        # ─── diagnostics (read-only) ───────────────────────────────
        dpdk_diagnostics_action = QAction("Diagnostics...", self)
        dpdk_diagnostics_action.setToolTip(
            "Read-only DPDK status + verify. Two tabs:\n"
            "  • Status — what's installed (libdpdk / IOMMU / VFIO / "
            "hugepages / per-NIC binding)\n"
            "  • Verify — check that DPDK binaries, drivers, and "
            "tx_worker are present and functional\n\n"
            "Doesn't change anything. Use Setup DPDK to fix issues."
        )
        dpdk_diagnostics_action.triggered.connect(self.show_dpdk_diagnostics)
        dpdk_menu.addAction(dpdk_diagnostics_action)

        dpdk_menu.addSeparator()

        # ─── advanced (atomic actions for scripted/manual use) ─────
        dpdk_advanced_menu = QMenu("Advanced", self)
        dpdk_advanced_menu.setToolTipsVisible(True)
        dpdk_advanced_menu.setToolTip(
            "Individual DPDK actions. Use these for scripted setups, "
            "kernel-update recovery, or per-NIC tweaks. For first-time "
            "or routine setup, use ★ Setup DPDK instead."
        )
        dpdk_menu.addMenu(dpdk_advanced_menu)

        dpdk_wizard_action = QAction("Quick Start Wizard...", self)
        dpdk_wizard_action.setToolTip(
            "Guided per-step alternative to ★ Setup DPDK. Same engine, "
            "but laid out as a multi-page wizard with Skip checkboxes "
            "at each step. Use when you want to see/adjust each phase "
            "before committing."
        )
        dpdk_wizard_action.triggered.connect(self.show_dpdk_quick_start_wizard)
        dpdk_advanced_menu.addAction(dpdk_wizard_action)

        dpdk_advanced_menu.addSeparator()

        dpdk_bind_action = QAction("Bind Interface...", self)
        dpdk_bind_action.setToolTip(
            "Detach a NIC from the kernel driver and bind it to vfio-pci so DPDK can drive it. "
            "Requires IOMMU enabled."
        )
        dpdk_bind_action.triggered.connect(self.bind_interface_to_dpdk)
        dpdk_advanced_menu.addAction(dpdk_bind_action)

        dpdk_unbind_action = QAction("Unbind Interface...", self)
        dpdk_unbind_action.setToolTip(
            "Release a DPDK-bound NIC back to the kernel network driver."
        )
        dpdk_unbind_action.triggered.connect(self.unbind_interface_from_dpdk)
        dpdk_advanced_menu.addAction(dpdk_unbind_action)

        # v0.5.34: Configure Hugepages + Configure IOMMU removed from
        # Advanced submenu. Operator request: "make it part of same
        # install process make dpdk ready". Both are already handled
        # by install_dpdk.sh — hugepages at Step 7, IOMMU at Step 7
        # too (via /etc/default/grub edit + offered reboot via
        # MakeDpdkReadyDialog's inline prompt added in v0.5.15).
        # Two parallel entry points were a UX trap: operators ran
        # them standalone, IOMMU edited GRUB but didn't prompt for
        # reboot, then Make DPDK Ready couldn't tell if the manual
        # IOMMU run had been completed → orchestrator misreported
        # IOMMU state. Removing them eliminates the divergence.
        #
        # The standalone handler methods (configure_hugepages /
        # configure_iommu) remain in the codebase for any external
        # caller, but no GUI entry point invokes them anymore.
        # Status surfacing happens via Diagnostics (which reads the
        # SAME state the orchestrator does, so any drift is visible).

        dpdk_load_modules_action = QAction("Load VFIO Modules", self)
        dpdk_load_modules_action.setToolTip(
            "modprobe vfio, vfio-pci, and vfio_iommu_type1 on the selected server. "
            "Useful when IOMMU is configured but kernel modules aren't loaded after "
            "a fresh boot (e.g., custom kernel without auto-load rules). Make DPDK "
            "Ready also runs this as part of Step 8."
        )
        dpdk_load_modules_action.triggered.connect(self.load_vfio_modules)
        dpdk_advanced_menu.addAction(dpdk_load_modules_action)

        # v0.3.12: RDMA submenu — sibling of DPDK. Drives perftest
        # (ib_send_bw / ib_write_bw / ib_read_bw + _lat variants)
        # across one or two selected TGs.
        #
        # v0.5.27: Setup RDMA entry pinned to the top of the menu.
        # Operator request: "rdma install should be separate, it should
        # not be part of dpdk install". Mirrors the DPDK Setup pattern
        # (Setup DPDK is the entry-point of the DPDK submenu).
        rdma_menu = QMenu("RDMA", self)
        rdma_menu.setToolTipsVisible(True)
        tools_menu.addMenu(rdma_menu)

        rdma_setup_action = QAction("Setup RDMA...", self)
        rdma_setup_action.setToolTip(
            "Install the RDMA stack on the selected server: "
            "libibverbs-dev + rdma-core + perftest + ibverbs-utils + "
            "infiniband-diags, plus libmlx5-dev (Mellanox MOFED, "
            "optional). Loads ib_uverbs / rdma_cm / ib_umad kernel "
            "modules and persists them across reboots. Required "
            "before Blast a RDMA Flow / Topology Test on a fresh "
            "server. ~1-2 minute install."
        )
        rdma_setup_action.triggered.connect(self.show_setup_rdma_dialog)
        rdma_menu.addAction(rdma_setup_action)
        rdma_menu.addSeparator()

        rdma_blast_action = QAction("Blast a RDMA Flow...", self)
        rdma_blast_action.setToolTip(
            "Two-TG (or loopback) perftest orchestrator. Pick a server-"
            "side TG + RDMA device, a client-side TG + device, and a "
            "test (Send / Write / Read × BW / Latency). Both halves run "
            "via /api/rdma/perftest/start and report live stats. "
            "Non-modal — open multiple to fan out across NIC pairs."
        )
        rdma_blast_action.triggered.connect(self.show_rdma_blast_flow_dialog)
        rdma_menu.addAction(rdma_blast_action)

        # v0.4.0 RDMA Topology Test — N×M companion to Blast a RDMA Flow.
        # Single dialog drives endpoint groups + a topology shape
        # (fan-in / fan-out / mesh / pairwise) rather than the operator
        # opening one dialog per pair. See Help → Install Guide §10d
        # for the Ixia comparison that motivates this.
        rdma_topology_action = QAction("Topology Test...", self)
        rdma_topology_action.setToolTip(
            "N×M perftest orchestrator. Add server-side + client-side "
            "endpoints to groups, pick a topology shape "
            "(single / fan-in / fan-out / mesh / pairwise), and the "
            "dialog spawns the cross-product of perftest pairs with "
            "aggregated stats. Use for fan-in stress testing, mesh "
            "validation, or any scenario the 1:1 Blast dialog can't "
            "cover in one shot."
        )
        rdma_topology_action.triggered.connect(self.show_rdma_topology_dialog)
        rdma_menu.addAction(rdma_topology_action)

        rdma_menu.addSeparator()

        rdma_devices_action = QAction("RDMA Devices...", self)
        rdma_devices_action.setToolTip(
            "List RDMA HCAs/ports/GIDs from /sys/class/infiniband on the "
            "selected server(s). Useful pre-flight before Blast a RDMA "
            "Flow — checks state=ACTIVE, link_layer (Ethernet vs IB), "
            "rate, MTU, and that perftest is installed."
        )
        rdma_devices_action.triggered.connect(self.show_rdma_devices_dialog)
        rdma_menu.addAction(rdma_devices_action)

        rdma_jobs_action = QAction("RDMA Jobs...", self)
        rdma_jobs_action.setToolTip(
            "List active + recently-finished perftest jobs on the "
            "selected server(s). Annotated with handshake_id so two "
            "halves of the same Blast RDMA Flow show as a pair."
        )
        rdma_jobs_action.triggered.connect(self.show_rdma_jobs_dialog)
        rdma_menu.addAction(rdma_jobs_action)

        tools_menu.addSeparator()

        # RFC 2544 throughput test — standard frame-size sweep that
        # binary-searches the max no-drop rate at each of the 7 standard
        # frame sizes (64/128/256/512/1024/1280/1518 bytes).
        rfc2544_action = QAction("RFC 2544 Throughput Test...", self)
        rfc2544_action.setToolTip(
            "Standard frame-size sweep — finds the max no-drop rate for "
            "each of the 7 RFC 2544 frame sizes via binary search. "
            "Requires a TX/RX port pair with loopback or external cabling."
        )
        rfc2544_action.triggered.connect(self.show_rfc2544_dialog)
        tools_menu.addAction(rfc2544_action)

        # Help menu — guides + about
        # NOTE: on macOS, a menu literally named "Help" gets absorbed into the
        # OS-managed Help menu (alongside the search box). The action stays
        # accessible via F1 — see the explicit self.addAction below.
        help_menu = QMenu("&Help", self)
        menu_bar.addMenu(help_menu)
        help_menu.setToolTipsVisible(True)

        # Install Guide — covers install_ostg_complete.py end-to-end
        # (single-command provisioning, what gets installed, opt-out flags,
        # tolerant-of-failure DPDK build, sanity checks).
        install_guide_action = QAction("Install Guide...", self)
        install_guide_action.setToolTip(
            "Single-command server provisioning: what gets installed, "
            "DPDK runtime build, CLI flags, troubleshooting paths."
        )
        install_guide_action.triggered.connect(self.show_install_guide)
        help_menu.addAction(install_guide_action)
        self.addAction(install_guide_action)

        # API Guide — REST cheatsheet for /api/traffic/* endpoints with
        # worked examples for every packet type (L2-only, IPv4+UDP/TCP/ICMP,
        # IPv6+UDP, VLAN-tagged), Scapy and DPDK paths side by side.
        # Updated in 0.2.68 with sections §24 (EVPN bulk inject),
        # §25 (Preflight), §26 (RFC 2544 latency + HTML report), §27
        # (SR-MPLS stack), plus BFD + QinQ in §23.
        api_guide_action = QAction("API Guide...", self)
        api_guide_action.setToolTip(
            "REST API reference with curl examples for every packet type "
            "(IPv4/IPv6 + UDP/TCP/ICMP, VLAN, line-rate DPDK), stream "
            "control, stats polling, EVPN bulk inject, and preflight."
        )
        api_guide_action.triggered.connect(self.show_api_guide)
        help_menu.addAction(api_guide_action)
        self.addAction(api_guide_action)

        # RDMA Guide — dedicated walkthrough for the RDMA feature set:
        # install / auto-install, device model, both dialogs (Blast a
        # Flow + Topology Test), stats aggregation semantics, Ixia
        # comparison, troubleshooting, REST surface. Added in v0.4.0;
        # previously the Topology + Ixia content lived buried inside
        # the API Guide where operators couldn't find it.
        rdma_guide_action = QAction("RDMA Guide...", self)
        rdma_guide_action.setToolTip(
            "Comprehensive RDMA walkthrough: install/auto-install "
            "(v0.3.18+), device + port model, single-pair Blast vs "
            "N×M Topology Test (v0.4.0+), stats aggregation, "
            "comparison with Ixia/Spirent, troubleshooting, and the "
            "REST surface for scripted runs."
        )
        rdma_guide_action.triggered.connect(self.show_rdma_guide)
        help_menu.addAction(rdma_guide_action)
        self.addAction(rdma_guide_action)

        # Supported Features — the capability matrix. Answers "can
        # this app do X?" — every packet type, protocol, emulator,
        # backend. Distinct from What's-New (release notes) and API
        # Guide (curl reference). Added in 0.2.72.
        capabilities_action = QAction("Supported Features...", self)
        capabilities_action.setToolTip(
            "Comprehensive capability matrix: every packet type "
            "(L2/L3/L4 + encapsulations), every protocol emulator "
            "(LACP/LLDP/VRRP/IGMP/PIM/BFD), routing (BGP/EVPN/"
            "OSPF/IS-IS), VXLAN/EVPN inject, RFC 2544, preflight, "
            "DPDK vs Scapy backends, auth, install/upgrade."
        )
        capabilities_action.triggered.connect(self.show_capabilities_guide)
        help_menu.addAction(capabilities_action)
        self.addAction(capabilities_action)

        # What's New — feature guide describing the GUI changes operators
        # actually see (status chips, L2/EVPN/SR-MPLS dialogs, RFC 2544
        # HTML report, latency p95, …). Lives alongside the API Guide so
        # the operator can switch between "where do I click?" and "what's
        # the curl?" without leaving the Help menu. Added in 0.2.69.
        feature_guide_action = QAction("What's New...", self)
        feature_guide_action.setToolTip(
            "Tour of the user-visible features added since 0.2.41 — "
            "Streams chip, L2 tab redesign, QinQ, BFD, EVPN bulk-inject "
            "GUI, RFC 2544 latency + HTML report, SR-MPLS stack, …"
        )
        feature_guide_action.triggered.connect(self.show_feature_guide)
        help_menu.addAction(feature_guide_action)
        self.addAction(feature_guide_action)

        help_menu.addSeparator()

        # Install / Upgrade Server — drives the server install + upgrade
        # paths without leaving the GUI. Tab 1: upload a wheel to a running
        # server (HTTP). Tab 2: SSH into a bare Linux host and run the
        # full install_ostg_complete.py provisioning flow.
        install_server_action = QAction("Install / Upgrade Server...", self)
        install_server_action.setToolTip(
            "Upload a wheel to upgrade a running netgen-server, OR SSH "
            "into a fresh Linux host and run install_ostg_complete.py "
            "(apt + Docker + FRR + DPDK + systemd)."
        )
        install_server_action.triggered.connect(self.show_install_server_dialog)
        help_menu.addAction(install_server_action)
        self.addAction(install_server_action)

        help_menu.addSeparator()

        dpdk_guide_action = QAction("DPDK Traffic Blast Workflow...", self)
        dpdk_guide_action.setShortcut(QKeySequence("F1"))
        dpdk_guide_action.setToolTip(
            "Step-by-step guide to using DPDK tx_worker for line-rate traffic generation, "
            "including TX core sizing, calibrated performance numbers, and troubleshooting."
        )
        dpdk_guide_action.triggered.connect(self.show_dpdk_workflow_guide)
        help_menu.addAction(dpdk_guide_action)
        # Make F1 accept the shortcut even when the Help menu is hidden
        # (macOS often absorbs the menu, but the action stays connected).
        self.addAction(dpdk_guide_action)

    def show_dpdk_workflow_guide(self):
        """Open the DPDK Workflow Guide dialog from the Help menu."""
        try:
            from widgets.stream_dialog import show_dpdk_usage_guide
            show_dpdk_usage_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def show_install_server_dialog(self):
        """Open the Install / Upgrade Server dialog (Help menu).

        Pre-fills the Server URL field from the currently-connected server
        and the auth token from NETGEN_AUTH_TOKEN env, so the common
        'upgrade the server I'm pointed at' case is one click.
        """
        try:
            from widgets.install_server_dialog import InstallServerDialog
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Install dialog unavailable",
                f"Could not load install dialog: {e}\n\n"
                "If you see 'paramiko import failed' the client venv is "
                "missing paramiko: pip install paramiko"
            )
            return

        # Best-effort pre-fill — both fields are optional and the user
        # can override in the dialog.
        default_url = ""
        try:
            # ServerSection stores the active URL on the main window as
            # self.server_url or similar — try a couple of attribute names.
            for attr in ("server_url", "_server_url", "current_server_url"):
                val = getattr(self, attr, "") or ""
                if val:
                    default_url = str(val)
                    break
        except Exception:
            pass

        default_token = os.environ.get("NETGEN_AUTH_TOKEN", "")

        dlg = InstallServerDialog(
            parent=self,
            default_server_url=default_url,
            default_auth_token=default_token,
        )
        dlg.exec_()

    def show_rfc2544_dialog(self):
        """Open the RFC 2544 throughput test dialog from Tools menu."""
        try:
            from widgets.rfc2544_dialog import Rfc2544Dialog
            # Pick the first online server's address; bail if none.
            srv = next((s for s in getattr(self, "server_interfaces", [])
                        if s.get("online", True)), None)
            if not srv:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "No server online",
                    "Connect to a TGEN server before running the RFC 2544 test."
                )
                return
            dlg = Rfc2544Dialog(self, server_url=str(srv.get("address") or ""))
            dlg.exec_()
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "RFC 2544 unavailable",
                                f"Could not open dialog: {e}")

    def show_install_guide(self):
        """Open the Installation Guide dialog from the Help menu."""
        try:
            from widgets.stream_dialog import show_install_guide
            show_install_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def show_api_guide(self):
        """Open the REST API Guide dialog from the Help menu."""
        try:
            from widgets.stream_dialog import show_api_guide
            show_api_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def show_rdma_guide(self):
        """Open the dedicated RDMA Guide dialog from the Help menu.
        Added in v0.4.0 — separate entry so the Topology + Ixia
        content isn't buried inside the API Guide where operators
        couldn't find it."""
        try:
            from widgets.stream_dialog import show_rdma_guide
            show_rdma_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def show_feature_guide(self):
        """Open the 'What's New' feature guide from the Help menu.
        Companion to the API Guide — describes the GUI changes operators
        actually see, with a where-to-click pointer for each one."""
        try:
            from widgets.stream_dialog import show_feature_guide
            show_feature_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def show_capabilities_guide(self):
        """Open the Supported Features / Capabilities Guide from the
        Help menu. The comprehensive capability matrix — every packet
        type, protocol, emulator, backend the app supports. Distinct
        from What's-New (release notes) and API Guide (curl reference)."""
        try:
            from widgets.stream_dialog import show_capabilities_guide
            show_capabilities_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def copy_selected_device(self):
        """Copy the selected device - delegate to devices tab."""
        self.devices_tab.copy_selected_device()

    def paste_device_to_interface(self):
        """Paste device to selected interface - delegate to devices tab."""
        self.devices_tab.paste_device_to_interface()
