# traffic_client/main.py
import logging

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
from capture_client import PacketCaptureClient
from traffic_client.menu_actions import TrafficGenClientMenuAction
from traffic_client.packet_capture import TrafficGenClientPacketCapture
from traffic_client.server_section import TrafficGenClientServerSection
from traffic_client.statistics_section import TrafficGenClientStatisticsSection
from traffic_client.stream_logic import TrafficGenClientStreamLogic
from traffic_client.stream_control import TrafficGenClientStreamControl
from traffic_client.dpdk_menu_actions import TrafficGenClientDPDKMenuActions
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
):
    # AI menu actions will be added via mixin
    pass
    def __init__(self, server_url=None, server_explicitly_provided=False):
        super().__init__()
        self.setWindowTitle("Netgen Traffic Generator")
        self.setGeometry(100, 100, 1400, 800)

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

        # Tabs on the right
        self.tab_widget = QTabWidget()
        self.streams_tab = QWidget()
        self.devices_tab = DevicesTab(self)
        self.tab_widget.addTab(self.streams_tab, "Streams")
        self.tab_widget.addTab(self.devices_tab, "Devices")
        self.top_section.addWidget(self.tab_widget)

        self.main_layout.addWidget(self.top_section)

        # Statistics section lives in a QDockWidget so the user can drag it out
        # into a floating window and drag it back to re-dock. Standard Qt dock
        # title bar has float (✥) and close (×) buttons.
        self.setup_traffic_statistics_section()
        self.statistics_dock = QDockWidget("Traffic Statistics", self)
        self.statistics_dock.setObjectName("trafficStatisticsDock")  # Required for saveState/restoreState
        self.statistics_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.statistics_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        # The QGroupBox is now the dock's content; its existing title becomes
        # redundant with the dock's title bar. Strip the QGroupBox title so we
        # don't show "Traffic Statistics" twice stacked on top of each other.
        if hasattr(self.statistics_group, "setTitle"):
            self.statistics_group.setTitle("")
        self.statistics_dock.setWidget(self.statistics_group)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.statistics_dock)
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
                tg_id = len(self.server_interfaces)  # Assign the next TG ID
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

    def closeEvent(self, event):
        """Handle application close event - cleanup threads and resources."""
        if self._is_closing:
            # Already in the process of closing, ignore
            event.accept()
            return
            
        self._is_closing = True
        
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

        # File menu
        file_menu = QMenu("&File", self)
        menu_bar.addMenu(file_menu)

        add_server_action = QAction("Add TGEN Chassis...", self)
        add_server_action.setShortcut(QKeySequence("Ctrl+N"))
        add_server_action.triggered.connect(self.add_server_interface)
        file_menu.addAction(add_server_action)

        remove_server_action = QAction("Remove TGEN Chassis", self)
        remove_server_action.triggered.connect(self.remove_selected_server)
        file_menu.addAction(remove_server_action)

        file_menu.addSeparator()

        save_session_action = QAction("Save Session", self)
        save_session_action.setShortcut(QKeySequence.Save)
        save_session_action.triggered.connect(self.save_session)
        file_menu.addAction(save_session_action)

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

        # Server menu — chassis-level actions (online state, restart, reboot)
        server_menu = QMenu("&Server", self)
        menu_bar.addMenu(server_menu)

        self.make_server_online_action = QAction("Make Selected Servers Online", self)
        self.make_server_online_action.setEnabled(False)
        # Tooltip explains why the action is disabled until a failed server is selected
        self.make_server_online_action.setToolTip(
            "Select an offline Tgen chassis in the server pane to enable this action."
        )
        self.make_server_online_action.triggered.connect(self.make_failed_servers_online)
        server_menu.addAction(self.make_server_online_action)

        server_menu.addSeparator()

        restart_tgen_action = QAction("Restart TGEN Service...", self)
        restart_tgen_action.triggered.connect(self.restart_server)
        server_menu.addAction(restart_tgen_action)

        reboot_server_action = QAction("Reboot Physical Server...", self)
        reboot_server_action.setToolTip(
            "Reboots the entire physical chassis. Requires explicit confirmation."
        )
        reboot_server_action.triggered.connect(self.reboot_server)
        server_menu.addAction(reboot_server_action)

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

        dpdk_status_action = QAction("Status...", self)
        dpdk_status_action.setToolTip(
            "Show DPDK installation, hugepage, IOMMU, and per-NIC binding status on the selected server."
        )
        dpdk_status_action.triggered.connect(self.show_dpdk_status)
        dpdk_menu.addAction(dpdk_status_action)

        dpdk_bind_action = QAction("Bind Interface...", self)
        dpdk_bind_action.setToolTip(
            "Detach a NIC from the kernel driver and bind it to vfio-pci so DPDK can drive it. "
            "Requires IOMMU enabled."
        )
        dpdk_bind_action.triggered.connect(self.bind_interface_to_dpdk)
        dpdk_menu.addAction(dpdk_bind_action)

        dpdk_unbind_action = QAction("Unbind Interface...", self)
        dpdk_unbind_action.setToolTip(
            "Release a DPDK-bound NIC back to the kernel network driver."
        )
        dpdk_unbind_action.triggered.connect(self.unbind_interface_from_dpdk)
        dpdk_menu.addAction(dpdk_unbind_action)

        dpdk_menu.addSeparator()

        dpdk_verify_action = QAction("Verify Installation", self)
        dpdk_verify_action.setToolTip(
            "Check that DPDK binaries, drivers, and tx_worker are present on the server."
        )
        dpdk_verify_action.triggered.connect(self.verify_dpdk)
        dpdk_menu.addAction(dpdk_verify_action)

        dpdk_hugepages_action = QAction("Configure Hugepages...", self)
        dpdk_hugepages_action.setToolTip(
            "Reserve hugepages required by DPDK. System-wide setting that affects "
            "memory available to other workloads (VMs, containers)."
        )
        dpdk_hugepages_action.triggered.connect(self.configure_hugepages)
        dpdk_menu.addAction(dpdk_hugepages_action)

        dpdk_menu.addSeparator()

        dpdk_iommu_action = QAction("Configure IOMMU...", self)
        dpdk_iommu_action.setToolTip(
            "Enable IOMMU in the bootloader (intel_iommu=on / amd_iommu=on). Requires a server reboot."
        )
        dpdk_iommu_action.triggered.connect(self.configure_iommu)
        dpdk_menu.addAction(dpdk_iommu_action)

        dpdk_load_modules_action = QAction("Load VFIO Modules", self)
        dpdk_load_modules_action.setToolTip(
            "modprobe vfio, vfio-pci, and vfio_iommu_type1 on the selected server."
        )
        dpdk_load_modules_action.triggered.connect(self.load_vfio_modules)
        dpdk_menu.addAction(dpdk_load_modules_action)

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

    def show_install_guide(self):
        """Open the Installation Guide dialog from the Help menu."""
        try:
            from widgets.stream_dialog import show_install_guide
            show_install_guide(self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help unavailable", f"Could not open guide: {e}")

    def copy_selected_device(self):
        """Copy the selected device - delegate to devices tab."""
        self.devices_tab.copy_selected_device()

    def paste_device_to_interface(self):
        """Paste device to selected interface - delegate to devices tab."""
        self.devices_tab.paste_device_to_interface()
