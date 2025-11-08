# traffic_client/main.py
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget, QSplitter,
    QMenu, QAction, QApplication
)

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer, Qt

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
from traffic_client.server_retry_workers import ServerRetryWorker, HealthCheckWorker, ConnectionManager


class TrafficGeneratorClient(
    QMainWindow,
    TrafficGenClientMenuAction,
    TrafficGenClientPacketCapture,
    TrafficGenClientServerSection,
    TrafficGenClientStatisticsSection,
    TrafficGenClientStreamLogic,
    TrafficGenClientStreamControl,
):
    def __init__(self, server_url=None, server_explicitly_provided=False):
        super().__init__()
        self.setWindowTitle("Traffic Generator Client")
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

        # Root layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        self.setup_menu_bar()

        # Split layout: top section (server + tabs) and bottom (statistics)
        self.splitter = QSplitter(Qt.Vertical)
        self.top_section = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.top_section)

        # Server section on the left
        self.setup_server_section()

        # Tabs on the right
        self.tab_widget = QTabWidget()
        self.streams_tab = QWidget()
        self.devices_tab = DevicesTab(self)
        self.tab_widget.addTab(self.streams_tab, "Streams")
        self.tab_widget.addTab(self.devices_tab, "Devices")
        self.top_section.addWidget(self.tab_widget)

        # Statistics section at the bottom
        self.setup_traffic_statistics_section()
        self.splitter.addWidget(self.statistics_group)

        # Add top + bottom sections to main layout
        self.main_layout.addWidget(self.splitter)

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
            print(f"✅ Using server from command line: {self.server_url} (TG {tg_id})")
            print(f"ℹ️  Skipped loading servers from session.json (only connecting to {self.server_url})")
        
        # Load devices from session.json on startup (after all UI components are set up)
        # If server was provided via CLI, skip loading servers from session.json
        # If no server was provided via CLI, load all TGs from session.json
        self.load_session(skip_servers=self.server_url_from_cli)
        
        # Log summary of loaded servers
        if not self.server_url_from_cli:
            if self.server_interfaces:
                print(f"✅ Loaded {len(self.server_interfaces)} TG(s) from session.json:")
                for server in self.server_interfaces:
                    print(f"   - TG {server.get('tg_id', '?')}: {server.get('address', 'N/A')}")
            else:
                print(f"ℹ️  No TGs found in session.json or all were previously removed")
        
        # If server URL was set via environment variable or default (but not CLI), add it now
        if not self.server_url_from_cli and self.server_url and self.server_url not in [server["address"] for server in self.server_interfaces]:
            # Server URL was set via environment variable or default, but not CLI
            # Check if this server was previously removed
            if self.server_url not in self.removed_servers:
                tg_id = len(self.server_interfaces)  # Assign the next TG ID
                server_entry = {"tg_id": tg_id, "address": self.server_url, "online": True}
                self.server_interfaces.append(server_entry)
                print(f"✅ Automatically added server: {self.server_url} (TG {tg_id})")
            else:
                print(f"⚠️ Server {self.server_url} was previously removed, not adding automatically")
        
        # Update server tree after servers are populated (especially important for CLI-provided server)
        if hasattr(self, 'update_server_tree'):
            self.update_server_tree()
            print(f"✅ Server tree updated on startup with {len(self.server_interfaces)} server(s)")
        
        # Initialize retry workers after session is loaded and servers are populated
        self._initialize_retry_workers()
        self._check_initial_server_status()

    def closeEvent(self, event):
        """Handle application close event - cleanup threads and resources."""
        if self._is_closing:
            # Already in the process of closing, ignore
            return
            
        self._is_closing = True
        print("[CLEANUP] Application closing, cleaning up threads...")
        print(f"[CLEANUP] Active thread summary -> "
              f"operation_worker={getattr(self.devices_tab, 'operation_worker', None)}, "
              f"arp_worker={getattr(self.devices_tab, 'arp_check_worker', None)}, "
              f"bulk_arp_worker={getattr(self.devices_tab, 'bulk_arp_worker', None)}, "
              f"retry_worker_running={self.server_retry_worker.isRunning() if self.server_retry_worker else 'N/A'}, "
              f"health_worker_running={self.health_check_worker.isRunning() if self.health_check_worker else 'N/A'}, "
              f"save_worker_running={getattr(self, '_save_worker', None).isRunning() if getattr(self, '_save_worker', None) else 'N/A'}")
        
        # Stop all timers first
        if hasattr(self, 'devices_tab') and self.devices_tab:
            if hasattr(self.devices_tab, 'status_timer') and self.devices_tab.status_timer:
                print("[CLEANUP] Stopping status timer...")
                self.devices_tab.status_timer.stop()
        
        # Clean up devices tab threads
        if hasattr(self, 'devices_tab') and self.devices_tab:
            print("[CLEANUP] Invoking devices_tab.cleanup_threads()...")
            self.devices_tab.cleanup_threads()
            print("[CLEANUP] Completed devices_tab.cleanup_threads()")
        
        # Clean up any stream timers
        if hasattr(self, '_stop_timers'):
            for timer in self._stop_timers.values():
                if timer and hasattr(timer, 'stop'):
                    timer.stop()
            self._stop_timers.clear()
        
        # Clean up retry workers
        print("[CLEANUP] Stopping retry workers...")
        if self.server_retry_worker:
            self.server_retry_worker.stop()
            self.server_retry_worker.wait(3000)  # Wait up to 3 seconds
        if self.health_check_worker:
            self.health_check_worker.stop()
            self.health_check_worker.wait(3000)  # Wait up to 3 seconds
        
        # Close connection manager
        if self.connection_manager:
            self.connection_manager.close()
        
        print("[CLEANUP] Retry workers stopped")
        print(f"[CLEANUP] Post-stop thread status -> "
              f"retry_worker_running={self.server_retry_worker.isRunning() if self.server_retry_worker else 'N/A'}, "
              f"health_worker_running={self.health_check_worker.isRunning() if self.health_check_worker else 'N/A'}")
        
        # Save session before closing (blocking to avoid lingering worker threads)
        try:
            result = self.save_session(blocking=True)
            if isinstance(result, tuple):
                success, message = result
                if not success:
                    print(f"[CLEANUP] Session save reported error during shutdown: {message}")
        except Exception as e:
            print(f"[CLEANUP] Failed to save session: {e}")
        finally:
            save_worker = getattr(self, "_save_worker", None)
            print(f"[CLEANUP] Save worker cleanup state -> exists={bool(save_worker)}, "
                  f"isRunning={save_worker.isRunning() if save_worker else 'N/A'}")
        
        # Force quit the application after a short delay to allow cleanup
        self._schedule_force_quit()
        event.ignore()  # Don't accept the event yet, wait for force_quit
    
    def _schedule_force_quit(self, delay=100):
        """Schedule force quit after optional delay, avoiding duplicate scheduling."""
        if self._force_quit_called:
            return
        print("[CLEANUP] Cleanup completed, forcing application exit...")
        QTimer.singleShot(delay, self.force_quit)
    
    def force_quit(self):
        """Force quit the application after cleanup."""
        if self._force_quit_called:
            return
        self._force_quit_called = True
        print("[CLEANUP] Force quitting application...")
        QApplication.quit()
        
        # Update server tree to show server and its interfaces
        if self.server_interfaces:
            self.update_server_tree()
        
        # Populate device table after session is loaded
        if hasattr(self, 'devices_tab') and self.devices_tab:
            self.devices_tab.populate_device_table()
            print("✅ Server tree updated on startup")
        
        # Manual discovery will be triggered by user clicking Start button

        # Start timers for polling stats (optimized interval)
        self.timer = QTimer()
        self.timer.timeout.connect(self.fetch_and_update_statistics)
        self.timer.start(10000)  # every 10s to reduce UI load

        self.stream_stats_timer = QTimer()
        self.stream_stats_timer.timeout.connect(self.poll_stream_stats)
        self.stream_stats_timer.start(5000)  # every 5s to reduce UI load
        
        # Retry workers are now initialized after session loading

    def _initialize_retry_workers(self):
        """Initialize the retry and health check workers."""
        try:
            print("[RETRY WORKERS] Initializing enhanced server retry system...")
            print(f"[RETRY WORKERS] Server interfaces count: {len(self.server_interfaces)}")
            
            # Initialize health check worker (disabled temporarily to prevent freezing)
            # self.health_check_worker = HealthCheckWorker(self.server_interfaces)
            # self.health_check_worker.health_status_updated.connect(self._on_server_health_updated)
            # self.health_check_worker.server_interfaces_updated.connect(self._on_server_interfaces_updated)
            # self.health_check_worker.start()
            print("[RETRY WORKERS] Health check worker disabled temporarily")
            
            # Initialize retry worker (disabled temporarily to prevent freezing)
            # self.server_retry_worker = ServerRetryWorker([])
            # self.server_retry_worker.server_reconnected.connect(self._on_server_reconnected)
            # self.server_retry_worker.server_still_failed.connect(self._on_server_still_failed)
            # self.server_retry_worker.retry_progress.connect(self._on_retry_progress)
            # self.server_retry_worker.start()
            print("[RETRY WORKERS] Server retry worker disabled temporarily")
            
            print("[RETRY WORKERS] Enhanced retry system initialized successfully")
        except Exception as e:
            print(f"[RETRY WORKERS ERROR] Failed to initialize retry workers: {e}")
            import traceback
            print(f"[RETRY WORKERS ERROR] Traceback: {traceback.format_exc()}")

    def _check_initial_server_status(self):
        """Check initial server status and enable menu if servers are offline."""
        try:
            print("[INITIAL STATUS CHECK] Checking initial server status...")
            offline_servers = [s for s in self.server_interfaces if s.get("online") is False]
            print(f"[INITIAL STATUS CHECK] Found {len(offline_servers)} offline servers")
            
            if offline_servers:
                print("[INITIAL STATUS CHECK] Enabling 'Make Server Online' menu")
                if hasattr(self, 'make_server_online_action'):
                    self.make_server_online_action.setEnabled(True)
            else:
                print("[INITIAL STATUS CHECK] All servers online, menu remains disabled")
        except Exception as e:
            print(f"[INITIAL STATUS CHECK ERROR] Error checking initial status: {e}")

    def _on_server_health_updated(self, server, is_online):
        """Handle server health status updates."""
        server_address = server.get("address")
        print(f"[HEALTH UPDATE] Server {server_address}: {'online' if is_online else 'offline'}")
        
        # Update server status icon
        if hasattr(self, 'update_server_status_icon'):
            self.update_server_status_icon(server, is_online)
        
        # If server came back online, remove from failed list
        if is_online and server in self.failed_servers:
            self.failed_servers.remove(server)
            print(f"[HEALTH UPDATE] Removed {server_address} from failed servers list")
            
            # Update "Make Server Online" menu state
            if not self.failed_servers and hasattr(self, 'make_server_online_action'):
                self.make_server_online_action.setEnabled(False)
        
        # If server went offline, add to failed list
        elif not is_online and server not in self.failed_servers:
            self.failed_servers.append(server)
            print(f"[HEALTH UPDATE] Added {server_address} to failed servers list")
            
            # Add to retry worker
            if self.server_retry_worker:
                self.server_retry_worker.add_failed_server(server)
            
            # Update "Make Server Online" menu state
            if hasattr(self, 'make_server_online_action'):
                self.make_server_online_action.setEnabled(True)

    def _on_server_interfaces_updated(self, server, interfaces):
        """Handle server interfaces updates."""
        server["interfaces"] = interfaces
        print(f"[INTERFACES UPDATE] Updated interfaces for {server.get('address')}: {len(interfaces)} interfaces")

    def _on_server_reconnected(self, server):
        """Handle successful server reconnection."""
        server_address = server.get("address")
        print(f"[RETRY SUCCESS] ✅ Server {server_address} reconnected successfully!")
        
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
        print(f"[RETRY FAILED] ❌ Server {server_address} still failed: {error_message}")

    def _on_retry_progress(self, server_address, status_message):
        """Handle retry progress updates."""
        print(f"[RETRY PROGRESS] {server_address}: {status_message}")

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
            print(f"[ARP Status Check] Error: {e}")

    def setup_menu_bar(self):
        """Set up the menu bar for server and stream management."""
        menu_bar = self.menuBar()

        # File menu
        file_menu = QMenu("File", self)
        menu_bar.addMenu(file_menu)

        add_server_action = QAction("Add Tgen Chassis", self)
        add_server_action.triggered.connect(self.add_server_interface)
        file_menu.addAction(add_server_action)

        remove_server_action = QAction("Remove Tgen Chassis", self)
        remove_server_action.triggered.connect(self.remove_selected_server)
        file_menu.addAction(remove_server_action)

        save_session_action = QAction("Save Session", self)
        save_session_action.triggered.connect(self.save_session)
        file_menu.addAction(save_session_action)

        self.make_server_online_action = QAction("Make Selected Servers Online", self)
        self.make_server_online_action.setEnabled(False)
        self.make_server_online_action.triggered.connect(self.make_failed_servers_online)
        file_menu.addAction(self.make_server_online_action)

        # Capture menu
        capture_menu = QMenu("Capture", self)
        menu_bar.addMenu(capture_menu)

        self.start_capture_action = QAction("Start Packet Capture", self)
        self.start_capture_action.triggered.connect(self.start_packet_capture)
        capture_menu.addAction(self.start_capture_action)

        self.stop_capture_action = QAction("Stop Packet Capture", self)
        self.stop_capture_action.triggered.connect(self.stop_packet_capture)
        self.stop_capture_action.setEnabled(False)
        capture_menu.addAction(self.stop_capture_action)

        # Edit menu
        edit_menu = QMenu("Edit", self)
        menu_bar.addMenu(edit_menu)

        copy_stream_action = QAction("Copy Stream", self)
        copy_stream_action.triggered.connect(self.copy_selected_stream)
        edit_menu.addAction(copy_stream_action)

        paste_stream_action = QAction("Paste Stream", self)
        paste_stream_action.triggered.connect(self.paste_stream_to_interface)
        edit_menu.addAction(paste_stream_action)

        # Add separator for device actions
        edit_menu.addSeparator()

        # Device copy/paste actions
        copy_device_action = QAction("Copy Device", self)
        copy_device_action.triggered.connect(self.copy_selected_device)
        edit_menu.addAction(copy_device_action)

        paste_device_action = QAction("Paste Device", self)
        paste_device_action.triggered.connect(self.paste_device_to_interface)
        edit_menu.addAction(paste_device_action)

    def copy_selected_device(self):
        """Copy the selected device - delegate to devices tab."""
        self.devices_tab.copy_selected_device()

    def paste_device_to_interface(self):
        """Paste device to selected interface - delegate to devices tab."""
        self.devices_tab.paste_device_to_interface()
