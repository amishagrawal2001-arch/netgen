#menu_actions.py#
import json, os, requests
import subprocess
from urllib.parse import urlparse
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView, QLabel
from PyQt5.QtWidgets import QTableWidgetItem
import uuid
from PyQt5.QtCore import Qt, QTimer
import logging

logger = logging.getLogger(__name__)


def sanitize_for_json(obj):
    """Recursively convert non-serializable objects to JSON-safe formats."""
    # Check for PyQt objects first (they can't be serialized)
    if hasattr(obj, '__class__'):
        obj_type = str(type(obj))
        if 'PyQt5' in obj_type or 'QWidget' in obj_type or 'QTreeWidgetItem' in obj_type or 'QLabel' in obj_type:
            # Skip PyQt objects - return a placeholder
            return f"<PyQt-object: {type(obj).__name__}>"
    
    if isinstance(obj, dict):
        # Filter out PyQt objects from dictionaries
        result = {}
        for k, v in obj.items():
            if hasattr(v, '__class__'):
                v_type = str(type(v))
                if 'PyQt5' in v_type or 'QWidget' in v_type or 'QTreeWidgetItem' in v_type:
                    continue  # Skip PyQt objects
            result[k] = sanitize_for_json(v)
        return result
    elif isinstance(obj, list):
        # Filter out PyQt objects from lists
        result = []
        for v in obj:
            if hasattr(v, '__class__'):
                v_type = str(type(v))
                if 'PyQt5' in v_type or 'QWidget' in v_type or 'QTreeWidgetItem' in v_type:
                    continue  # Skip PyQt objects
            result.append(sanitize_for_json(v))
        return result
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    elif hasattr(obj, "text") and callable(getattr(obj, "text", None)):
        # Handles QLabel, QLineEdit, etc. - but check if it's PyQt first
        try:
            # Try calling text() without arguments
            return obj.text()
        except TypeError:
            # If it requires arguments (like QTreeWidgetItem.text(column)), skip it
            return f"<non-serializable: {type(obj).__name__}>"
    elif hasattr(obj, "__str__"):
        return str(obj)
    else:
        return f"<non-serializable: {type(obj).__name__}>"

class TrafficGenClientMenuAction():
    def add_server_interface(self):
        """Add a new server interface."""
        server_url, ok = QInputDialog.getText(self, "Add Server", "Enter Server Address (e.g., 127.0.0.1):")
        if not ok or not server_url.strip():
            return

        port, ok = QInputDialog.getText(self, "Add Port", "Enter Port (default: 80):")
        port = port.strip() if port else "80"

        try:
            full_url = f"http://{server_url.strip()}:{int(port)}"
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a valid number.")
            return

        if full_url not in [server["address"] for server in self.server_interfaces]:
            tg_id = len(self.server_interfaces)  # Assign the next TG ID
            server_entry = {"tg_id": tg_id, "address": full_url, "online": True}
            self.server_interfaces.append(server_entry)
            
            # Remove from removed_servers if it was previously removed
            if full_url in self.removed_servers:
                self.removed_servers.discard(full_url)
                logger.info(f"[ADD SERVER] Removed {full_url} from removed_servers (server was re-added)")
            
            # Update ServerManager if available
            if hasattr(self, "server_manager"):
                from utils.server_manager import ServerManager
                server_id = ServerManager._extract_server_id_from_url(full_url)
                self.server_manager.register_server(
                    server_id=server_id,
                    address=full_url,
                    tg_id=tg_id,
                    online=True
                )
                logger.info(f"[ADD SERVER] Registered server {server_id} in ServerManager")
            
            self.update_server_tree()
            self.save_server_interfaces()
        else:
            QMessageBox.warning(self, "Duplicate Server", "This server is already added.")
    def remove_selected_server(self):
        """Remove the currently selected server(s) from the tree."""
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a server to remove.")
            return

        for item in selected_items:
            if item.parent() is None:  # Ensure it's a top-level item (server)
                server_address = item.text(1)  # Server address column
                tg_id = item.text(0)  # TG ID column

                # Add server to removed_servers set
                self.removed_servers.add(server_address)

                # Remove the server and its ports from the server interfaces
                # Update ServerManager if available
                if hasattr(self, "server_manager"):
                    from utils.server_manager import ServerManager
                    server_id = ServerManager._extract_server_id_from_url(server_address)
                    self.server_manager.unregister_server(server_id)
                    logger.info(f"[REMOVE SERVER] Unregistered server {server_id} from ServerManager")
                
                self.server_interfaces = [
                    server for server in self.server_interfaces if server["address"] != server_address
                ]

                # Remove related entries from removed_interfaces
                self.removed_interfaces = {
                    port for port in self.removed_interfaces if not port.startswith(f"{tg_id} - ")
                }

                # Remove the selected server from selected_servers if applicable
                self.selected_servers = [
                    server for server in self.selected_servers if server["address"] != server_address
                ]

                # Remove the server item from the tree
                index = self.server_tree.indexOfTopLevelItem(item)
                self.server_tree.takeTopLevelItem(index)

                logger.info(f"Removed server: {server_address} and all associated ports.")

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)
        self.save_server_interfaces()

        # QMessageBox.information(self, "Server Removed", "Selected server(s) and associated ports removed successfully.")
    
    def readd_servers_dialog(self):
        """Display a dialog to re-add removed servers."""
        if not self.removed_servers:
            QMessageBox.information(self, "No Removed Servers", "No servers have been removed.")
            return
            
        dialog = QDialog(self)
        dialog.setWindowTitle("Re-add Servers")
        dialog.setGeometry(300, 300, 500, 300)

        layout = QVBoxLayout(dialog)

        # List widget to display removed servers with checkboxes
        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        layout.addWidget(list_widget)

        # Populate the list widget with removed servers
        for server_address in sorted(self.removed_servers):
            item = QListWidgetItem(server_address)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            list_widget.addItem(item)

        # Confirm and Cancel buttons
        button_layout = QHBoxLayout()
        confirm_button = QPushButton("Re-add Selected Servers")
        confirm_button.clicked.connect(lambda: self.readd_servers(list_widget, dialog))
        button_layout.addWidget(confirm_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        dialog.exec()
    
    def readd_servers(self, list_widget, dialog):
        """Re-add the selected servers from the dialog."""
        readded_servers = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server_address = item.text().strip()
                
                # Remove from removed_servers set
                self.removed_servers.discard(server_address)
                
                # Add back to server_interfaces with a new TG ID
                tg_id = len(self.server_interfaces)  # Assign the next available TG ID
                server_entry = {"tg_id": tg_id, "address": server_address, "online": True}
                self.server_interfaces.append(server_entry)
                
                # Update ServerManager if available
                if hasattr(self, "server_manager"):
                    from utils.server_manager import ServerManager
                    server_id = ServerManager._extract_server_id_from_url(server_address)
                    self.server_manager.register_server(
                        server_id=server_id,
                        address=server_address,
                        tg_id=tg_id,
                        online=True
                    )
                    logger.info(f"[READD SERVER] Registered server {server_id} in ServerManager")
                
                readded_servers.append(server_address)

        if readded_servers:
            logger.info(f"Re-added servers: {readded_servers}")
            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            self.update_server_tree()  # Update the server tree
            QMessageBox.information(self, "Servers Re-added", f"Re-added servers: {', '.join(readded_servers)}")
        else:
            QMessageBox.information(self, "No Servers Selected", "No servers were selected to re-add.")

        dialog.accept()
    def load_server_interfaces(self):
        """Load server interfaces from a file and assign TG IDs."""
        try:
            from utils.path_utils import get_ostg_data_directory
            data_dir = get_ostg_data_directory()
            server_file = os.path.join(data_dir, "server_interfaces.txt")
            
            with open(server_file, "r") as f:
                servers = [line.strip() for line in f.readlines()]
            self.server_interfaces = [{"tg_id": i, "address": server} for i, server in enumerate(servers)]
            logger.info(f"Loaded servers: {self.server_interfaces}")
        except FileNotFoundError:
            logger.info("server_interfaces.txt not found. Starting with an empty server list.")
            self.server_interfaces = []
    def save_server_interfaces(self):
        """Save the server interfaces to a file."""
        try:
            from utils.path_utils import get_ostg_data_directory
            data_dir = get_ostg_data_directory()
            server_file = os.path.join(data_dir, "server_interfaces.txt")
            
            with open(server_file, "w") as f:
                for server in self.server_interfaces:
                    f.write(f"{server['address']}\n")
            logger.info("Server interfaces saved successfully.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save server interfaces: {e}")
    def save_session(self, blocking: bool = False):
        """Save the current session to a JSON file.

        Args:
            blocking: When True, perform the save synchronously on the caller thread.
                      When False (default), run the save in a background QThread.
        """
        import traceback
        import time
        # Starting save_session()
        
        current_time = time.time()
        # Prevent new async saves while shutting down; blocking saves still allowed
        if getattr(self, "_is_closing", False) and not blocking:
            return
        
        # Check if another save is already in progress
        # Use a lock to prevent race conditions
        if not hasattr(self, '_save_lock'):
            from threading import Lock
            self._save_lock = Lock()
        
        with self._save_lock:
            in_progress = getattr(self, '_save_in_progress', False)
            worker_exists = hasattr(self, '_save_worker') and self._save_worker is not None
            worker_running = False
            if worker_exists:
                try:
                    # Double-check worker is still valid (might have been deleted)
                    if self._save_worker is not None:
                        worker_running = self._save_worker.isRunning()
                except RuntimeError:
                    # Worker was deleted, treat as not running
                    worker_running = False
                    self._save_worker = None
                    worker_exists = False
        
        if in_progress or worker_running:
            if blocking:
                # Wait for the existing background worker to finish before proceeding
                if worker_exists and worker_running and self._save_worker is not None:
                    logger.info("[SAVE SESSION] Waiting for existing background save to finish (blocking request)...")
                    self._save_worker.quit()  # Request thread to stop
                    if not self._save_worker.wait(5000):
                        logger.warning("[SAVE SESSION WARNING] Background save did not finish within timeout; forcing termination.")
                        self._save_worker.terminate()
                        self._save_worker.wait(1000)
                if worker_exists and self._save_worker is not None:
                    try:
                        if not self._save_worker.isRunning():
                            self._save_worker.deleteLater()
                    except RuntimeError:
                        pass
                    self._save_worker = None
                self._save_in_progress = False
            else:
                # Non-blocking call while a save is already in progress – skip
                # print("[SAVE SESSION] Save already in progress, skipping duplicate save request")
                return
        
        # Throttle rapid save calls (only for non-blocking saves)
        if not blocking:
            # Check if this is a duplicate save call within a short time window
            if hasattr(self, '_last_save_time') and (current_time - self._last_save_time) < 0.5:
                # print("[SAVE SESSION] Skipping duplicate save call (throttled)")
                return
        # Update timestamp for throttling (both blocking and non-blocking)
        self._last_save_time = current_time
        
        # Re-evaluate worker flags after potential cleanup above
        with self._save_lock:
            worker_exists = hasattr(self, '_save_worker') and self._save_worker is not None
            worker_running = False
            if worker_exists:
                try:
                    if self._save_worker is not None:
                        worker_running = self._save_worker.isRunning()
                except RuntimeError:
                    worker_running = False
                    self._save_worker = None
                    worker_exists = False
        
        # CRITICAL: Extract PyQt widget data in main thread before starting worker
        # PyQt widgets cannot be accessed from worker threads
        protocol_data = {}
        if hasattr(self, "devices_tab") and self.devices_tab is not None:
            if hasattr(self.devices_tab, "bgp_table"):
                protocol_data["bgp"] = self._extract_table_data(self.devices_tab.bgp_table)
            if hasattr(self.devices_tab, "ospf_table"):
                protocol_data["ospf"] = self._extract_table_data(self.devices_tab.ospf_table)
            if hasattr(self.devices_tab, "isis_table"):
                protocol_data["isis"] = self._extract_table_data(self.devices_tab.isis_table)
            if hasattr(self.devices_tab, "dhcp_table"):
                protocol_data["dhcp"] = self._extract_table_data(self.devices_tab.dhcp_table)
        
        # If we're running a blocking save during shutdown, wait for any existing worker
        if blocking:
            try:
                if worker_exists and worker_running and self._save_worker is not None:
                    logger.info("[SAVE SESSION] Waiting for existing background save to finish...")
                    self._save_worker.quit()  # Request thread to stop
                    if not self._save_worker.wait(5000):
                        logger.warning("[SAVE SESSION WARNING] Background save did not finish within timeout; forcing termination.")
                        self._save_worker.terminate()
                        self._save_worker.wait(1000)
                if worker_exists and self._save_worker is not None:
                    try:
                        if not self._save_worker.isRunning():
                            self._save_worker.deleteLater()
                    except RuntimeError:
                        pass
                    self._save_worker = None
                self._save_in_progress = True
                success, message = self._save_session_impl(protocol_data)
            except Exception as e:
                traceback.print_exc()
                success, message = False, str(e)
            finally:
                self._save_in_progress = False
            if success:
                logger.info(f"[SAVE SESSION] {message}")
            else:
                logger.error(f"[SAVE SESSION ERROR] {message}")
            return success, message
        
        self._save_in_progress = True
        
        # Run save operation in separate thread to avoid blocking UI
        from PyQt5.QtCore import QThread, pyqtSignal
        
        class SaveSessionWorker(QThread):
            finished = pyqtSignal(bool, str)  # success, message
            
            def __init__(self, main_window, protocol_data):
                super().__init__()
                self.main_window = main_window
                self.protocol_data = protocol_data
            
            def run(self):
                """Run the actual save operation in this thread."""
                try:
                    success, message = self.main_window._save_session_impl(self.protocol_data)
                    self.finished.emit(success, message)
                except Exception as e:
                    self.finished.emit(False, str(e))
        
        # Start the save worker thread
        # CRITICAL: Check if worker exists and is valid before checking isRunning()
        # The worker might have been deleted via deleteLater() but not yet cleaned up
        # Clean up previous worker if it exists and is finished
        with self._save_lock:
            if worker_exists and not worker_running:
                try:
                    # Previous worker finished, ensure it's fully stopped before cleanup
                    worker = self._save_worker
                    self._save_worker = None  # Clear reference first
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    if not worker.isRunning():
                        worker.deleteLater()
                except RuntimeError:
                    # Object already deleted, ignore
                    pass
                except Exception as exc:
                    logger.info(f"[SAVE SESSION] Error cleaning up previous worker: {exc}")
            
            # CRITICAL: Don't create a new worker if one is already running
            # This prevents multiple workers from being created during rapid save calls
            if worker_exists and worker_running:
                # Worker already running, skip creating a new one
                logger.info("[SAVE SESSION] Save worker already running, skipping duplicate save request")
                return True, "Save already in progress"
            
            # Create new worker if needed
            if not worker_exists or self._save_worker is None:
                self._save_worker = SaveSessionWorker(self, protocol_data)
                # CRITICAL: Set parent to ensure proper cleanup
                self._save_worker.setParent(self)
                # CRITICAL: Connect finished signal to our handler (NOT deleteLater - handle cleanup manually)
                self._save_worker.finished.connect(self._on_save_finished)
                self._save_worker.start()
            else:
                # Worker already running, just mark as in progress
                pass
    
    def _on_save_finished(self, success, message):
        """Handle save completion (called from worker thread via signal)."""
        self._save_in_progress = False
        if success:
            logger.info(f"[SAVE SESSION] {message}")
        else:
            logger.error(f"[SAVE SESSION ERROR] {message}")
        
        # CRITICAL: Properly clean up the worker thread
        # Wait for thread to finish, then schedule deletion
        if hasattr(self, '_save_worker') and self._save_worker is not None:
            worker = self._save_worker
            self._save_worker = None  # Clear reference immediately to prevent reuse
            
            # Ensure thread has finished before scheduling deletion
            if worker.isRunning():
                # Thread should have finished by now, but wait just in case
                worker.wait(100)  # Short wait to ensure thread is done
            
            # Schedule deletion in main thread
            from PyQt5.QtCore import QTimer
            def cleanup_worker():
                try:
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    worker.deleteLater()
                except RuntimeError:
                    # Worker already deleted, ignore
                    pass
            
            QTimer.singleShot(50, cleanup_worker)
    
    def _save_session_impl(self, protocol_data=None):
        """Internal implementation of save_session (runs in worker thread).
        
        Args:
            protocol_data: Pre-extracted protocol table data (extracted in main thread)
        """
        import traceback
        import time
        
        updated_streams = {}
        if hasattr(self, "ensure_unique_stream_ids"):
            self.ensure_unique_stream_ids()
        # Serialize stream data
        for port, stream_list in self.streams.items():
            updated_streams[port] = []
            for stream in stream_list:
                if hasattr(stream, "get_stream_details"):
                    stream_data = stream.get_stream_details()
                else:
                    stream_data = stream
                updated_streams[port].append(sanitize_for_json(stream_data))

        # ✅ Extract device data from all_devices data structure including protocol configurations
        device_rows = {}
        if hasattr(self, "devices_tab") and self.devices_tab is not None:
            # Use the all_devices data structure which has the complete device information
            # Get the current state directly from the main_window (same as device removal uses)
            all_devices = getattr(self, "all_devices", {})
            # Convert from interface-based structure to device name-based structure
            for iface, devices in all_devices.items():
                for device in devices:
                    device_name = device.get("Device Name", "")
                    if device_name:
                        # Use device name as key instead of MAC address
                        device_rows[device_name] = device
            
            # Use pre-extracted protocol data (extracted in main thread to avoid PyQt widget access from worker thread)
            if protocol_data is None:
                protocol_data = {}
        else:
            # No devices_tab found
            if protocol_data is None:
                protocol_data = {}

        # Track removed devices for session synchronization
        removed_devices = getattr(self, 'removed_devices', [])
        # Found removed devices to save

        # Get BGP route pools if defined
        bgp_route_pools = getattr(self, 'bgp_route_pools', [])
        
        # Determine which servers to save:
        # - If server was provided via CLI, check if servers were modified
        # - If servers were added/removed via UI, save current server_interfaces
        # - Otherwise, preserve original servers from session.json (CLI mode)
        # - Ensure ServerManager is in sync before saving
        if hasattr(self, "server_manager") and self.server_interfaces:
            # Sync ServerManager with current server_interfaces
            # Reinitialize to ensure consistency (clear_existing=True to prevent duplicates)
            self.server_manager.initialize_from_server_interfaces(self.server_interfaces, clear_existing=True)
            logger.info(f"[SAVE SESSION] Synced ServerManager with {len(self.server_interfaces)} server(s)")
        
        # Check if we should preserve original servers (CLI mode without modifications)
        preserve_original = False
        if getattr(self, 'server_url_from_cli', False) and hasattr(self, 'original_session_servers'):
            # In CLI mode, only preserve original if server count hasn't changed
            # This allows saving when user adds/removes servers via UI
            original_count = len(self.original_session_servers)
            current_count = len(self.server_interfaces)
            if original_count == current_count:
                # Count matches, check if addresses are the same
                original_addresses = {s.get("address") for s in self.original_session_servers}
                current_addresses = {s.get("address") for s in self.server_interfaces}
                if original_addresses == current_addresses:
                    preserve_original = True
        
        if preserve_original:
            # CLI mode: preserve original servers from session.json (no changes made)
            servers_to_save = self.original_session_servers
            logger.info(f"[SAVE SESSION] Preserving {len(servers_to_save)} original server(s) from session.json (CLI mode, no changes)")
        else:
            # Normal mode or CLI mode with modifications: save current servers
            # Clean server_interfaces to remove PyQt widget objects before saving
            servers_to_save = []
            for server in self.server_interfaces:
                # Create a clean copy without PyQt objects
                clean_server = {
                    "tg_id": server.get("tg_id", 0),
                    "address": server.get("address", ""),
                    "online": server.get("online", True),
                    "interfaces": server.get("interfaces", [])
                }
                # Only include interfaces if it's a list/dict (not a PyQt object)
                if isinstance(clean_server["interfaces"], (list, dict)):
                    servers_to_save.append(clean_server)
                else:
                    clean_server["interfaces"] = []
                    servers_to_save.append(clean_server)
            
            if getattr(self, 'server_url_from_cli', False):
                logger.info(f"[SAVE SESSION] Saving {len(servers_to_save)} current server(s) (CLI mode, servers modified)")
            else:
                logger.info(f"[SAVE SESSION] Saving {len(servers_to_save)} current server(s)")
        
        # Clean up removed_servers - remove any servers that are currently in server_interfaces
        # This ensures that if a server was previously removed but then re-added, it won't be in removed_servers
        current_server_addresses = {s.get("address") for s in servers_to_save}
        cleaned_removed_servers = {addr for addr in self.removed_servers if addr not in current_server_addresses}
        
        # Assemble session data
        session_data = {
            "servers": sanitize_for_json(servers_to_save),
            "removed_interfaces": list(self.removed_interfaces),
            "removed_servers": list(cleaned_removed_servers),  # Save cleaned removed servers
            "selected_servers": [s["address"] for s in getattr(self, "selected_servers", [])],
            "streams": updated_streams,
            "devices": sanitize_for_json(device_rows),
            "removed_devices": sanitize_for_json(removed_devices),
            "protocols": sanitize_for_json(protocol_data) if protocol_data else {},
            "bgp_route_pools": sanitize_for_json(bgp_route_pools)  # Save global route pools
        }
        
        # Store current device state for change tracking
        self.last_saved_devices = device_rows.copy()

        # Save to disk using proper path utilities
        try:
            from utils.path_utils import get_session_file_path
            session_file = get_session_file_path()
            # Writing to session file
            with open(session_file, "w") as f:
                json.dump(session_data, f, indent=2)
            return True, "✅ Session saved successfully"
        except Exception as e:
            return False, f"[❌] Failed to save session: {e}"

    def _cleanup_removed_devices_from_server(self, removed_device_ids, all_loaded_devices):
        """Clean up removed devices from server during session loading."""
        try:
            logger.debug(f"[DEBUG CLEANUP] Starting server cleanup for {len(removed_device_ids)} removed devices")
            
            # Get server URL
            if not hasattr(self, "devices_tab") or not self.devices_tab:
                logger.debug(f"[DEBUG CLEANUP] No devices_tab available")
                return
            
            server_url = self.devices_tab.get_server_url(silent=True)
            if not server_url:
                logger.debug(f"[DEBUG CLEANUP] No server URL available")
                return
            
            import requests
            
            # Track devices that were successfully cleaned up or don't exist anymore
            devices_to_remove_from_session = []
            
            for device_id in removed_device_ids:
                try:
                    # Find device info for this ID
                    device_info = None
                    device_name = "Unknown"
                    for name, info in all_loaded_devices.items():
                        if info.get("device_id") == device_id:
                            device_info = info
                            device_name = name
                            break
                    
                    # If device info not found, it means the device was already removed from session
                    # or doesn't exist. We should remove it from removed_devices list.
                    if not device_info:
                        logger.debug(f"[DEBUG CLEANUP] Device info not found for ID: {device_id} - already removed or doesn't exist")
                        devices_to_remove_from_session.append(device_id)
                        continue
                    
                    logger.debug(f"[DEBUG CLEANUP] Cleaning up removed device: {device_name} (ID: {device_id})")
                    
                    # Clean up device-specific IPs from server
                    iface_label = device_info.get("Interface", "")
                    iface_norm = self.devices_tab._normalize_iface_label(iface_label)
                    vlan = device_info.get("VLAN", "0")
                    
                    cleanup_payload = {
                        "interface": iface_norm,
                        "vlan": vlan,
                        "cleanup_only": True,
                        "device_specific": True,
                        "device_id": device_id,
                        "device_name": device_name
                    }
                    
                    cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
                    cleanup_success = cleanup_resp.status_code == 200
                    if cleanup_success:
                        logger.debug(f"[DEBUG CLEANUP] Successfully cleaned up IPs for device: {device_name}")
                    else:
                        # If device doesn't exist (404), it's already cleaned up
                        if cleanup_resp.status_code == 404:
                            logger.debug(f"[DEBUG CLEANUP] Device {device_name} doesn't exist on server (already removed)")
                            cleanup_success = True  # Treat as success since device is already gone
                        else:
                            logger.error(f"[DEBUG CLEANUP] Cleanup failed for {device_name}: {cleanup_resp.status_code}")
                    
                    # Also call the device remove API for protocol cleanup
                    remove_payload = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": iface_norm,
                        "vlan": vlan,
                        "ipv4": device_info.get("IPv4", ""),
                        "ipv6": device_info.get("IPv6", ""),
                        "protocols": device_info.get("Protocols", "").split(",") if device_info.get("Protocols") else []
                    }
                    
                    remove_resp = requests.post(f"{server_url}/api/device/remove", json=remove_payload, timeout=10)
                    remove_success = remove_resp.status_code == 200
                    if remove_success:
                        logger.debug(f"[DEBUG CLEANUP] Successfully removed device protocols: {device_name}")
                    else:
                        # If device doesn't exist (404), it's already removed
                        if remove_resp.status_code == 404:
                            logger.debug(f"[DEBUG CLEANUP] Device {device_name} doesn't exist on server (already removed)")
                            remove_success = True  # Treat as success since device is already gone
                        else:
                            logger.error(f"[DEBUG CLEANUP] Remove API failed for {device_name}: {remove_resp.status_code}")
                    
                    # If cleanup was successful (or device doesn't exist), remove from session
                    if cleanup_success or remove_success:
                        devices_to_remove_from_session.append(device_id)
                        
                except Exception as e:
                    logger.error(f"Failed to cleanup device {device_id}: {e}")
            
            # Remove successfully cleaned devices from session.json
            if devices_to_remove_from_session:
                try:
                    from utils.path_utils import get_session_file_path
                    session_file = get_session_file_path()
                    with open(session_file, "r") as f:
                        session_data = json.load(f)
                    
                    removed_devices = session_data.get("removed_devices", [])
                    # Remove devices that were successfully cleaned up
                    original_count = len(removed_devices)
                    session_data["removed_devices"] = [
                        dev_id for dev_id in removed_devices 
                        if dev_id not in devices_to_remove_from_session
                    ]
                    removed_count = original_count - len(session_data["removed_devices"])
                    
                    if removed_count > 0:
                        with open(session_file, "w") as f:
                            json.dump(session_data, f, indent=2)
                        logger.debug(f"[DEBUG CLEANUP] Removed {removed_count} device(s) from session.json removed_devices list")
                except Exception as e:
                    logger.error(f"Failed to update session.json: {e}")
            
            logger.debug(f"[DEBUG CLEANUP] Completed server cleanup for removed devices")
            
        except Exception as e:
            logger.error(f"Failed to cleanup removed devices from server: {e}")

    def _extract_table_data(self, table):
        """Extract data from a QTableWidget and return as list of dictionaries."""
        if not table:
            return []
        
        data = []
        headers = []
        
        # Get headers
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item else f"Column_{col}")
        
        # Get row data
        for row in range(table.rowCount()):
            row_data = {}
            for col in range(table.columnCount()):
                item = table.item(row, col)
                row_data[headers[col]] = item.text() if item else ""
            data.append(row_data)
        
        return data

    def _load_protocol_data(self, protocol_data):
        """Load protocol data into the respective tables."""
        if not hasattr(self, "devices_tab") or not self.devices_tab:
            return
        
        # Load BGP data
        if "bgp" in protocol_data and hasattr(self.devices_tab, "bgp_table"):
            self._populate_table_from_data(self.devices_tab.bgp_table, protocol_data["bgp"])
        
        # Load OSPF data
        if "ospf" in protocol_data and hasattr(self.devices_tab, "ospf_table"):
            self._populate_table_from_data(self.devices_tab.ospf_table, protocol_data["ospf"])
        
        # Load ISIS data
        if "isis" in protocol_data and hasattr(self.devices_tab, "isis_table"):
            self._populate_table_from_data(self.devices_tab.isis_table, protocol_data["isis"])

    def _populate_table_from_data(self, table, data):
        """Populate a QTableWidget with data from a list of dictionaries."""
        if not table or not data:
            return
        
        # Clear existing data
        table.setRowCount(0)
        
        # Set row count
        table.setRowCount(len(data))
        
        # Get headers
        headers = []
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item else f"Column_{col}")
        
        # Populate data
        for row, row_data in enumerate(data):
            for col, header in enumerate(headers):
                if header in row_data:
                    item = QTableWidgetItem(str(row_data[header]))
                    table.setItem(row, col, item)

    def _sanitize_loaded_streams(self, valid_ports: set):
        """
        In-place cleanup on self.streams after JSON load:
          - Drop ports not in valid_ports (if valid_ports is non-empty)
          - Ensure each stream has protocol_selection + name
          - Ensure unique stream_id (create if missing; repair if dup)
        """
        # 1) Filter ports to only valid_ports (if we were able to discover any)
        # Handle both formats: "TG 0 - ens5np0" and "TG 0 - Port: ens5np0"
        if valid_ports:
            # Create normalized set that includes both formats
            normalized_valid_ports = set(valid_ports)
            for p in valid_ports:
                if " - " in p:
                    parts = p.split(" - ", 1)
                    if len(parts) == 2:
                        # Add format with "Port:" prefix
                        normalized_valid_ports.add(f"{parts[0]} - Port: {parts[1]}")
                        # Add format without "Port:" prefix (if it has "Port:")
                        if parts[1].startswith("Port: "):
                            normalized_valid_ports.add(f"{parts[0]} - {parts[1].replace('Port: ', '')}")
            
            # Filter streams, matching against normalized port names
            filtered_streams = {}
            for port_key, stream_list in self.streams.items():
                # Check if port_key matches any normalized valid port
                if port_key in normalized_valid_ports:
                    filtered_streams[port_key] = stream_list
                else:
                    # Try to normalize port_key and match
                    if " - " in port_key:
                        parts = port_key.split(" - ", 1)
                        if len(parts) == 2:
                            # Create normalized versions
                            key_with_port = f"{parts[0]} - Port: {parts[1].replace('Port: ', '')}"
                            key_without_port = f"{parts[0]} - {parts[1].replace('Port: ', '')}"
                            if key_with_port in normalized_valid_ports or key_without_port in normalized_valid_ports:
                                filtered_streams[port_key] = stream_list
            
            self.streams = filtered_streams

        # 2) Normalize structure + IDs
        seen_ids = set()
        repaired = 0

        for port, lst in list(self.streams.items()):
            if not isinstance(lst, list):
                # Corrupt shape; drop it to be safe
                logger.warning(f"[WARN] Streams for '{port}' not a list; dropping.")
                del self.streams[port]
                continue

            for s in lst:
                # Ensure protocol_selection dict
                ps = s.setdefault("protocol_selection", {})

                # Ensure name
                nm = ps.get("name") or s.get("name")
                if not nm:
                    # Assign a readable default if missing
                    nm = f"str{len(seen_ids) + 1}"
                ps["name"] = nm
                s["name"] = nm

                # Ensure status (nice-to-have)
                s.setdefault("status", "stopped")

                # Ensure stream_id uniqueness
                sid = s.get("stream_id")
                if not sid or sid in seen_ids:
                    # allocate a new id
                    if hasattr(self, "_alloc_stream_id"):
                        sid = self._alloc_stream_id()
                    else:
                        import uuid
                        sid = str(uuid.uuid4())
                    s["stream_id"] = sid
                    repaired += 1
                seen_ids.add(sid)

        if repaired:
            logger.info(f"[STREAM-ID] Repaired/created {repaired} stream_id(s) during load.")

    def _fetch_interfaces_async(self, address, server):
        """Fetch interfaces from server in a real background thread.

        Previously this name was misleading — the function was called
        from QTimer.singleShot which runs on the Qt event loop (the UI
        thread), and the body did a synchronous requests.get with a
        2s timeout. Offline / unreachable servers (especially ones
        that RST the connection mid-handshake — see user-reported
        "Connection reset by peer") froze the UI for the full timeout.

        Now: real QThread off the UI thread, results delivered via a
        signal back to the main thread.
        """
        from PyQt5.QtCore import QThread, pyqtSignal, QTimer

        class _MenuFetchWorker(QThread):
            done = pyqtSignal(bool, int, list, str)  # (ok, status, interfaces, err)

            def __init__(self, url, conn_mgr):
                super().__init__()
                self._url = url
                self._conn_mgr = conn_mgr

            def run(self):
                try:
                    if self._conn_mgr is not None:
                        r = self._conn_mgr.get(
                            f"{self._url}/api/interfaces", timeout=2
                        )
                    else:
                        import requests
                        r = requests.get(
                            f"{self._url}/api/interfaces", timeout=2
                        )
                    if r.status_code == 200:
                        try:
                            from traffic_client.server_section import _filter_internal_ifaces
                            self.done.emit(True, 200, _filter_internal_ifaces(r.json()) or [], "")
                            return
                        except Exception as fexc:
                            self.done.emit(False, r.status_code, [], f"parse: {fexc}")
                            return
                    self.done.emit(False, r.status_code, [], "")
                except Exception as exc:
                    self.done.emit(False, 0, [], str(exc))

        conn_mgr = getattr(self, "connection_manager", None)
        worker = _MenuFetchWorker(address, conn_mgr)

        # Hold a strong ref; Qt will GC the thread otherwise.
        if not hasattr(self, "_menu_iface_workers"):
            self._menu_iface_workers = []
        self._menu_iface_workers.append(worker)

        def _on_done(ok, status, ifaces, err, srv=server, addr=address):
            if ok:
                srv["interfaces"] = ifaces
                srv["online"] = True
                if hasattr(self, "update_server_tree"):
                    QTimer.singleShot(0, self.update_server_tree)
                logger.info(f"Fetched {len(ifaces)} interfaces from {addr} (async)")
            else:
                srv["online"] = False
                if hasattr(self, "failed_servers"):
                    if srv not in self.failed_servers:
                        self.failed_servers.append(srv)
                if err:
                    logger.warning(f"Error fetching interfaces from {addr} (async): {err}")
                elif status:
                    logger.warning(f"{addr} returned HTTP {status}")
                if hasattr(self, "update_server_tree"):
                    QTimer.singleShot(0, self.update_server_tree)

        worker.done.connect(_on_done)
        worker.finished.connect(
            lambda w=worker: self._menu_iface_workers.remove(w)
            if w in self._menu_iface_workers else None
        )
        worker.start()
    
    def load_session(self, skip_servers=False):
        """Load the session from a JSON file.
        
        Args:
            skip_servers: If True, skip loading servers from session.json (useful when server is provided via CLI)
        """
        try:
            from utils.path_utils import get_session_file_path
            session_file = get_session_file_path()
            with open(session_file, "r") as f:
                session_data = json.load(f)
            
            logger.debug(f"[DEBUG SESSION] Loaded session.json with {len(session_data.get('devices', {}))} devices")

            # Load removed servers and removed interfaces
            self.removed_servers = set(session_data.get("removed_servers", []))
            removed_interfaces_raw = session_data.get("removed_interfaces", [])
            
            # Clean up old format entries (e.g., " - ens14f1" without TG ID)
            # These are invalid and should be removed
            self.removed_interfaces = set()
            for iface in removed_interfaces_raw:
                # Only keep entries that have the correct format "TG X - portname"
                if iface and "TG " in iface and " - " in iface and not iface.startswith(" - "):
                    self.removed_interfaces.add(iface)
                else:
                    logger.debug(f"[DEBUG LOAD] Skipping invalid removed interface format: {iface}")
            
            # Always preserve original servers from session.json (for saving later in CLI mode)
            session_servers = session_data.get("servers", [])
            self.original_session_servers = session_servers.copy()
            
            # Only load servers from session if skip_servers is False
            if not skip_servers:
                # Load servers from session, but preserve any servers added via command line
                # and exclude servers that were previously removed
                existing_server_urls = {server["address"] for server in self.server_interfaces}
            
                # Add session servers that aren't already present and weren't removed
                for server in session_servers:
                    server_address = server["address"]
                    if server_address not in existing_server_urls and server_address not in self.removed_servers:
                        self.server_interfaces.append(server)
                        logger.debug(f"[DEBUG LOAD] Added server {server_address} from session")
                        
                        # Update ServerManager if available (will be initialized later, but this ensures consistency)
                        if hasattr(self, "server_manager"):
                            from utils.server_manager import ServerManager
                            server_id = ServerManager._extract_server_id_from_url(server_address)
                            tg_id = server.get("tg_id", 0)
                            self.server_manager.register_server(
                                server_id=server_id,
                                address=server_address,
                                tg_id=tg_id,
                                online=server.get("online", True),
                                interfaces=server.get("interfaces", [])
                            )
                    elif server_address in self.removed_servers:
                        logger.debug(f"[DEBUG LOAD] Skipped removed server {server_address}")
            else:
                logger.debug(f"[DEBUG LOAD] Skipped loading servers from session.json (server provided via CLI)")
                logger.debug(f"[DEBUG LOAD] Preserved {len(self.original_session_servers)} original server(s) from session.json for future saves")
            
            # Cancel pending auto-stop timers BEFORE clearing self.streams
            # — otherwise they fire later against vanished stream_ids.
            if hasattr(self, "_cancel_all_auto_stop_timers"):
                self._cancel_all_auto_stop_timers()
            self.streams = {}
            self.failed_servers = []
            self.all_devices = {}  # Initialize all_devices

            # Discover valid interfaces from online servers (deferred to avoid blocking startup)
            # Use cached interfaces from session.json if available, otherwise fetch asynchronously
            valid_ports = set()
            for server in self.server_interfaces:
                tg_id = f"TG {server.get('tg_id', '0')}"
                address = server.get("address")
                
                # First, try to use cached interfaces from session.json (fast, no network call)
                cached_interfaces = server.get("interfaces", [])
                if cached_interfaces:
                    # Use cached interfaces immediately (non-blocking)
                    server["online"] = True
                    for iface in cached_interfaces:
                        iface_name = iface if isinstance(iface, str) else iface.get('name', '')
                        if iface_name:
                            port_name_simple = f"{tg_id} - {iface_name}"
                            port_name_with_port = f"{tg_id} - Port: {iface_name}"
                            if port_name_simple not in self.removed_interfaces and port_name_with_port not in self.removed_interfaces:
                                valid_ports.add(port_name_simple)
                                valid_ports.add(port_name_with_port)
                    # Schedule async refresh of interfaces (non-blocking)
                    from PyQt5.QtCore import QTimer
                    def refresh_interfaces():
                        self._fetch_interfaces_async(address, server)
                    QTimer.singleShot(100, refresh_interfaces)
                else:
                    # No cached interfaces - mark as offline for now, will be fetched asynchronously
                    server["online"] = False
                    # Schedule async fetch (non-blocking)
                    from PyQt5.QtCore import QTimer
                    def fetch_interfaces():
                        self._fetch_interfaces_async(address, server)
                    QTimer.singleShot(100, fetch_interfaces)

            # Load streams (raw)
            loaded_streams = session_data.get("streams", {})
            if not isinstance(loaded_streams, dict):
                logger.warning("Session 'streams' malformed; starting with empty.")
                loaded_streams = {}

            self.streams = loaded_streams

            # 🔧 Sanitize + filter + de-dup IDs
            self._sanitize_loaded_streams(valid_ports)

            # Extra safety sweep (your existing guard)
            if hasattr(self, "ensure_unique_stream_ids"):
                self.ensure_unique_stream_ids()

            # Load BGP route pools from session
            self.bgp_route_pools = session_data.get("bgp_route_pools", [])
            logger.debug(f"[DEBUG LOAD] Loaded {len(self.bgp_route_pools)} BGP route pool(s)")
            
            # Load devices from session
            loaded_devices = session_data.get("devices", {})
            removed_devices = session_data.get("removed_devices", [])  # List of device IDs that were removed
            
            if isinstance(loaded_devices, dict):
                # Convert from device name-based structure back to interface-based structure
                self.all_devices = {}
                loaded_count = 0
                removed_count = 0
                
                for device_name, device_info in loaded_devices.items():
                    device_id = device_info.get("device_id", "")
                    
                    # Skip devices that were marked as removed
                    if device_id in removed_devices:
                        logger.debug(f"[DEBUG LOAD] Skipping removed device: {device_name} (ID: {device_id})")
                        removed_count += 1
                        continue
                    
                    # Convert old single-tunnel VXLAN config to tunnels format for consistency
                    vxlan_config = device_info.get("vxlan_config", {})
                    if vxlan_config and isinstance(vxlan_config, dict):
                        if "tunnels" not in vxlan_config:
                            # Old format: single tunnel dict, convert to tunnels format
                            logger.debug(f"[DEBUG LOAD] Converting old VXLAN config format to tunnels format for {device_name}")
                            device_info["vxlan_config"] = {"tunnels": [vxlan_config]}
                    
                    iface = device_info.get("Interface", "")
                    if iface:
                        if iface not in self.all_devices:
                            self.all_devices[iface] = []
                        self.all_devices[iface].append(device_info)
                        loaded_count += 1
                
                # Clean up removed devices from server if any were found
                if removed_devices and hasattr(self, "devices_tab") and self.devices_tab:
                    logger.debug(f"[DEBUG LOAD] Found {len(removed_devices)} removed devices in session - cleaning up from server")
                    self._cleanup_removed_devices_from_server(removed_devices, loaded_devices)
                
                # Load BGP protocols from session
                session_bgp_protocols = session_data.get("protocols", {}).get("bgp", [])
                if session_bgp_protocols:
                    logger.debug(f"[DEBUG LOAD] Found {len(session_bgp_protocols)} BGP protocols in session")
                
                # Update devices tab with loaded devices
                if hasattr(self, "devices_tab") and self.devices_tab:
                    self.devices_tab.all_devices = self.all_devices.copy()
                    self.devices_tab.update_device_table(self.all_devices)
                    # Update BGP table with loaded BGP configurations
                    self.devices_tab.update_bgp_table()
                    logger.debug(f"[DEBUG LOAD] Loaded {loaded_count} devices from session, skipped {removed_count} removed devices")
            else:
                logger.debug("[DEBUG LOAD] No valid devices found in session")
                self.all_devices = {}

            # Restore selected servers
            sel_addrs = set(session_data.get("selected_servers", []))
            self.selected_servers = [s for s in self.server_interfaces if s.get("address") in sel_addrs]

            # Refresh UI
            if hasattr(self, "update_server_tree"):
                self.update_server_tree()
            if hasattr(self, "update_stream_table"):
                self.update_stream_table()

            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            # Note: Repairs are done in memory only, user can save manually if needed

            stream_count = sum(len(v) for v in self.streams.values())
            port_count = len(self.streams)
            logger.info(f"Session loaded: {stream_count} streams across {port_count} ports.")
            if stream_count > 0:
                logger.debug(f"[DEBUG LOAD] Stream port keys: {list(self.streams.keys())}")
            if valid_ports:
                logger.debug(f"[DEBUG LOAD] Valid ports discovered: {sorted(valid_ports)}")
            
            # Auto-start enabled streams that were previously running (after server restart or client restart)
            # Wait a moment for servers to be ready, then start enabled streams
            if stream_count > 0 and hasattr(self, "start_all_streams"):
                from PyQt5.QtCore import QTimer
                # Count enabled streams that should be auto-started
                enabled_streams_count = 0
                for port_label, stream_list in self.streams.items():
                    for stream in stream_list:
                        # Check if stream is enabled (either explicitly or was running)
                        is_enabled = stream.get("enabled", False)
                        if not is_enabled:
                            # Check protocol_selection
                            ps = stream.get("protocol_selection", {})
                            is_enabled = ps.get("enabled", False)
                        # Also check if stream was running (status="running")
                        was_running = stream.get("status", "").lower() == "running"
                        if is_enabled or was_running:
                            enabled_streams_count += 1
                
                if enabled_streams_count > 0:
                    logger.info(f"[AUTO-START] Found {enabled_streams_count} enabled/running stream(s) to auto-start")
                    # Delay auto-start to ensure servers and UI are ready (wait 3 seconds)
                    # This gives time for:
                    # - Server connections to be established
                    # - Stream table to be populated
                    # - Statistics polling to initialize
                    QTimer.singleShot(3000, lambda: self._auto_start_streams_from_session())
                else:
                    logger.info(f"[AUTO-START] No enabled streams found to auto-start")
        
        except FileNotFoundError:
            logger.info("No session file found. Starting fresh.")
            self._initialize_empty_session()
    
    def _auto_start_streams_from_session(self):
        """Auto-start enabled streams that were loaded from session.json."""
        # Safety checks: ensure UI is fully initialized before attempting auto-start
        if not hasattr(self, "start_all_streams"):
            logger.info("[AUTO-START] start_all_streams method not available")
            return
        
        # Ensure stream_table exists and is initialized
        if not hasattr(self, "stream_table") or self.stream_table is None:
            logger.info("[AUTO-START] Stream table not initialized yet, skipping auto-start")
            return
        
        # Check if any servers are online
        online_servers = [s for s in getattr(self, "server_interfaces", []) if s.get("online", False)]
        if not online_servers:
            logger.info("[AUTO-START] No online servers available, skipping auto-start")
            return
        
        # Check if there are any enabled streams to start
        enabled_count = 0
        streams_to_start = []
        for port_label, stream_list in getattr(self, "streams", {}).items():
            for stream in stream_list:
                # Check if stream is enabled
                is_enabled = stream.get("enabled", False)
                if not is_enabled:
                    ps = stream.get("protocol_selection", {})
                    is_enabled = ps.get("enabled", False)
                # Also check if stream was running (status="running")
                was_running = stream.get("status", "").lower() == "running"
                if is_enabled or was_running:
                    enabled_count += 1
                    streams_to_start.append((port_label, stream))
        
        if enabled_count > 0:
            logger.info(f"[AUTO-START] Found {enabled_count} enabled stream(s) to auto-start")
            # Use QTimer to defer execution to next event loop cycle to ensure UI is ready
            from PyQt5.QtCore import QTimer
            try:
                # Defer to next event loop cycle to ensure UI is fully ready
                QTimer.singleShot(100, lambda: self._do_auto_start_streams(streams_to_start))
            except Exception as e:
                logger.info(f"[AUTO-START] ❌ Error scheduling auto-start: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.info(f"[AUTO-START] No enabled streams to auto-start")
    
    def _do_auto_start_streams(self, streams_to_start):
        """Actually perform the auto-start of streams."""
        try:
            # Double-check UI is ready
            if not hasattr(self, "stream_table") or self.stream_table is None:
                logger.info("[AUTO-START] Stream table still not ready, aborting auto-start")
                return
            
            logger.info(f"[AUTO-START] Auto-starting {len(streams_to_start)} enabled stream(s) from session.json...")
            self.start_all_streams()
            logger.info(f"[AUTO-START] ✅ Auto-start completed")
        except Exception as e:
            logger.info(f"[AUTO-START] ❌ Error during auto-start: {e}")
            import traceback
            traceback.print_exc()
    def reset_session(self):
        """Reset the session data to default."""
        if hasattr(self, "_cancel_all_auto_stop_timers"):
            self._cancel_all_auto_stop_timers()
        self.server_interfaces = []
        self.streams = {}
        self.removed_interfaces = set()
        self.selected_servers = []
        if hasattr(self, "update_server_tree"):
            self.update_server_tree()
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
    def save_removed_interfaces(self):
        """Save removed interfaces to a file."""
        try:
            with open("removed_interfaces.txt", "w") as f:
                for interface in self.removed_interfaces:
                    f.write(f"{interface}\n")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save removed interfaces: {e}")
    def save_interfaces(self):
        """Save the current list of interfaces to a file."""
        try:
            # Collect current interfaces from the statistics table
            interfaces = [self.statistics_table.horizontalHeaderItem(col).text()
                          for col in range(self.statistics_table.columnCount())]

            # Save the interfaces to a file
            with open("interfaces.txt", "w") as f:
                for interface in interfaces:
                    f.write(f"{interface}\n")

            QMessageBox.information(self, "Save Successful", "Current interfaces have been saved.")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"An error occurred while saving: {str(e)}")
    def load_removed_interfaces(self):
        """Load removed interfaces from a file."""
        try:
            with open("removed_interfaces.txt", "r") as f:
                self.removed_interfaces = {line.strip() for line in f.readlines()}
        except FileNotFoundError:
            self.removed_interfaces = set()
    def get_server_interfaces_for_tg(self, tg_id):
        """Filter the full server_interfaces list to get ports only for the selected TG."""
        for server in getattr(self, "server_interfaces", []):
            if str(server.get("tg_id")) == str(tg_id):
                return [server]
        return []
    def make_failed_servers_online(self):
        """Retry connection to offline servers manually via menu - only for selected servers."""
        if not hasattr(self, "failed_servers") or not self.failed_servers:
            QMessageBox.information(self, "No Servers Recovered", "No offline servers were recorded as failed.")
            return
        
        # Get selected servers from checkboxes
        selected_servers = getattr(self, "selected_servers", [])
        selected_tg_ids = self.get_selected_tg_ids()
        logger.info(f"[MAKE SERVER ONLINE] Selected TG IDs: {selected_tg_ids}")
        logger.info(f"[MAKE SERVER ONLINE] Selected servers count: {len(selected_servers)}")
        
        if not selected_servers:
            QMessageBox.information(self, "No Servers Selected", "Please select servers using the checkboxes to retry connections.")
            return
        
        # Filter failed servers to only include selected ones
        selected_failed_servers = []
        for server in self.failed_servers:
            if server in selected_servers:
                selected_failed_servers.append(server)
        
        if not selected_failed_servers:
            QMessageBox.information(self, "No Selected Servers Failed", "None of the selected servers are currently offline.")
            return
        
        logger.info(f"Attempting reconnection to {len(selected_failed_servers)} selected failed server(s): " +
              ", ".join([s.get('address', 'Unknown') for s in selected_failed_servers]))
        
        any_reconnected = False

        # Probe every selected failed server in parallel on background
        # threads — the previous implementation did sync requests.get
        # with a 2s timeout on the UI thread, so retrying N offline
        # servers froze the app for N × 2s. Deliver the per-server
        # results back to the main thread via a signal and tally once
        # all probes have completed.
        from PyQt5.QtCore import QThread, pyqtSignal

        class _RetryAllWorker(QThread):
            done = pyqtSignal(object, bool)  # (server_dict, ok)

            def __init__(self, server, conn_mgr):
                super().__init__()
                self._server = server
                self._conn_mgr = conn_mgr

            def run(self):
                url = self._server.get("address")
                try:
                    if self._conn_mgr is not None:
                        r = self._conn_mgr.get(
                            f"{url}/api/interfaces", timeout=2
                        )
                    else:
                        r = requests.get(
                            f"{url}/api/interfaces", timeout=2
                        )
                    self.done.emit(self._server, r.status_code == 200)
                except Exception as exc:
                    logger.debug(f"Retry probe failed for {url}: {exc}")
                    self.done.emit(self._server, False)

        if not hasattr(self, "_menu_retry_workers"):
            self._menu_retry_workers = []
        # Track completion + outcome so we can show a single summary
        # dialog once all probes return.
        progress = {"done": 0, "total": len(selected_failed_servers), "ok": 0}

        def _on_one_done(server, ok, w=None):
            progress["done"] += 1
            address = server.get("address")
            if ok:
                server["online"] = True
                self.update_server_status_icon(server, True)
                progress["ok"] += 1
                logger.info(f"Selected server {address} is now online.")
                if server in self.failed_servers:
                    self.failed_servers.remove(server)
                if hasattr(self, "server_retry_worker") and self.server_retry_worker:
                    self.server_retry_worker.remove_failed_server(server)
            else:
                logger.error(f"Still failed to connect to selected server {address}")
            if progress["done"] == progress["total"]:
                if progress["ok"]:
                    QMessageBox.information(self, "Servers Updated", "Some servers are now back online.")
                    self.update_server_tree()
                    self.fetch_and_update_statistics()
                else:
                    QMessageBox.information(self, "No Servers Recovered", "No offline servers could be brought online.")

        conn_mgr = getattr(self, "connection_manager", None)
        for server in selected_failed_servers[:]:
            address = server.get("address")
            logger.info(f"Trying to bring selected server {address} online...")
            worker = _RetryAllWorker(server, conn_mgr)
            self._menu_retry_workers.append(worker)
            worker.done.connect(_on_one_done)
            worker.finished.connect(
                lambda w=worker: self._menu_retry_workers.remove(w)
                if w in self._menu_retry_workers else None
            )
            worker.start()
    
    def get_selected_tg_ids(self):
        """Get list of TG IDs for currently selected servers."""
        selected_servers = getattr(self, "selected_servers", [])
        tg_ids = []
        for server in selected_servers:
            tg_id = server.get("tg_id")
            if tg_id is not None:
                tg_ids.append(f"TG {tg_id}")
        return tg_ids

    def _initialize_empty_session(self):
        """Initialize an empty session with default values."""
        logger.info("Initializing an empty session.")
        if hasattr(self, "_cancel_all_auto_stop_timers"):
            self._cancel_all_auto_stop_timers()
        # Don't overwrite server_interfaces if servers were added via command line
        if not self.server_interfaces:
            self.server_interfaces = []
        self.streams = {}
        self.removed_interfaces = set()
        self.removed_servers = set()  # Initialize removed servers
        self.selected_servers = []

        # Check if servers are reachable and update their status
        if self.server_interfaces:
            logger.info(f"Checking {len(self.server_interfaces)} server(s) for connectivity...")
            for server in self.server_interfaces:
                address = server.get("address")
                if self.is_reachable(address):
                    logger.info(f"Server {address} is reachable")
                    server["online"] = True
                    try:
                        # Use shorter timeout to prevent hanging during initialization
                        r = requests.get(f"{address}/api/interfaces", timeout=2)
                        r.raise_for_status()
                        interfaces = r.json()
                        server["interfaces"] = interfaces  # Store interfaces for update_server_tree
                        logger.info(f"Fetched {len(interfaces)} interfaces from {address}")
                    except Exception as e:
                        logger.error(f"Error fetching interfaces from {address}: {e}")
                        server["online"] = False
                else:
                    logger.error(f"Server {address} is unreachable")
                    server["online"] = False

        # Update UI components to reflect the reset state
        if hasattr(self, "update_server_tree"):
            self.update_server_tree()
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
        logger.info("Empty session initialized.")
    def is_reachable(self, server_url, timeout=2):
        """Check if a traffic generator server is reachable."""
        try:
            response = requests.get(f"{server_url}/api/ping", timeout=timeout)
            return response.status_code == 200
        except Exception:
            return False
    def restart_server(self):
        """Restart TGEN service on selected server(s) via SSH/systemctl."""
        if not hasattr(self, "server_interfaces") or not self.server_interfaces:
            QMessageBox.warning(self, "No Servers", "No servers are configured.")
            return

        # Get selected servers from tree or use all servers
        selected_items = self.server_tree.selectedItems() if hasattr(self, "server_tree") else []
        
        servers_to_restart = []
        
        if selected_items:
            # Get servers from selected items
            for item in selected_items:
                parent = item.parent()
                if parent:
                    # This is an interface item, get the server from parent
                    parent_index = self.server_tree.indexOfTopLevelItem(parent)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        if server not in servers_to_restart:
                            servers_to_restart.append(server)
                else:
                    # This is a server item
                    server_index = self.server_tree.indexOfTopLevelItem(item)
                    if server_index >= 0 and server_index < len(self.server_interfaces):
                        server = self.server_interfaces[server_index]
                        if server not in servers_to_restart:
                            servers_to_restart.append(server)
        
        # If no servers selected, show dialog to select servers
        if not servers_to_restart:
            dialog = QDialog(self)
            dialog.setWindowTitle("Restart TGEN Service")
            dialog.setGeometry(300, 300, 500, 400)
            
            layout = QVBoxLayout(dialog)
            
            label = QLabel("Select server(s) to restart TGEN service:")
            layout.addWidget(label)
            
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
            layout.addWidget(list_widget)
            
            # Populate with all servers
            for server in self.server_interfaces:
                address = server.get("address", "Unknown")
                tg_id = server.get("tg_id", "?")
                online_status = "Online" if server.get("online", False) else "Offline"
                item_text = f"TG {tg_id}: {address} ({online_status})"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, server)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                list_widget.addItem(item)
            
            button_layout = QHBoxLayout()
            restart_button = QPushButton("Restart Selected")
            restart_button.clicked.connect(lambda: self._restart_selected_servers(list_widget, dialog))
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(restart_button)
            button_layout.addWidget(cancel_button)
            layout.addLayout(button_layout)
            
            dialog.exec()
            return
        
        # Restart selected servers
        self._restart_servers_list(servers_to_restart)

    def _restart_selected_servers(self, list_widget, dialog):
        """Restart servers selected in the dialog."""
        servers_to_restart = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server = item.data(Qt.UserRole)
                if server:
                    servers_to_restart.append(server)
        
        dialog.accept()
        
        if not servers_to_restart:
            QMessageBox.information(self, "No Selection", "Please select at least one server to restart.")
            return
        
        self._restart_servers_list(servers_to_restart)

    def _restart_servers_list(self, servers):
        """Restart a list of servers via SSH/systemctl."""
        if not servers:
            return
        
        # Confirm restart
        server_names = [f"TG {s.get('tg_id', '?')}: {s.get('address', 'Unknown')}" for s in servers]
        reply = QMessageBox.question(
            self,
            "Confirm TGEN Restart",
            f"Are you sure you want to restart the TGEN service on the following server(s)?\n\n" + "\n".join(server_names) + "\n\nThis will:\n• Restart the netgen-server service\n• Stop all running streams\n• Service will be back online in a few seconds",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Restart each server
        results = []
        for server in servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Extract hostname from URL (e.g., "http://svl-hp-ai-srv04:5051" -> "svl-hp-ai-srv04")
            try:
                parsed = urlparse(address)
                hostname = parsed.hostname
                if not hostname:
                    # Fallback: try to extract from address string
                    if "://" in address:
                        hostname = address.split("://")[1].split(":")[0]
                    else:
                        hostname = address.split(":")[0]
            except Exception as e:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not parse server address '{address}': {e}")
                continue
            
            if not hostname:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not extract hostname from '{address}'")
                continue
            
            # Restart via SSH. Try the canonical netgen-server unit first;
            # fall back to the legacy ostg-server name for hosts that haven't
            # been migrated yet (kept for at least one release of overlap).
            try:
                # Server-side: prefer netgen-server, fall back to ostg-server
                # if that unit isn't installed. Single SSH round-trip.
                remote = (
                    "if systemctl list-unit-files netgen-server.service "
                    "| grep -q netgen-server; then "
                    "  systemctl restart netgen-server; "
                    "elif systemctl list-unit-files ostg-server.service "
                    "| grep -q ostg-server; then "
                    "  systemctl restart ostg-server; "
                    "else "
                    "  echo 'Neither netgen-server nor ostg-server unit found' >&2; exit 1; "
                    "fi"
                )
                cmd = ["ssh", f"root@{hostname}", remote]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    results.append(f"✅ TG {tg_id} ({hostname}): Restarted successfully")
                    logger.info(f"[RESTART SERVER] Successfully restarted server TG {tg_id} on {hostname}")
                else:
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    results.append(f"❌ TG {tg_id} ({hostname}): Failed - {error_msg}")
                    logger.error(f"[RESTART SERVER] Failed to restart server TG {tg_id} on {hostname}: {error_msg}")
            except subprocess.TimeoutExpired:
                results.append(f"⏱️ TG {tg_id} ({hostname}): Timeout (server may be restarting)")
                logger.info(f"[RESTART SERVER] Timeout restarting server TG {tg_id} on {hostname}")
            except FileNotFoundError:
                results.append(f"❌ TG {tg_id} ({hostname}): SSH not found (install OpenSSH client)")
                logger.info(f"[RESTART SERVER] SSH command not found - OpenSSH client may not be installed")
            except Exception as e:
                results.append(f"❌ TG {tg_id} ({hostname}): Error - {str(e)}")
                logger.info(f"[RESTART SERVER] Error restarting server TG {tg_id} on {hostname}: {e}")
        
        # Show results
        result_text = "\n".join(results)
        QMessageBox.information(
            self,
            "TGEN Restart Results",
            f"TGEN service restart results:\n\n{result_text}\n\nNote: Services may take a few seconds to come back online."
        )
        
        # Refresh server status after a delay
        if hasattr(self, "update_server_tree"):
            QTimer.singleShot(3000, self.update_server_tree)
    
    def reboot_server(self):
        """Reboot selected server(s) via SSH/reboot command."""
        if not hasattr(self, "server_interfaces") or not self.server_interfaces:
            QMessageBox.warning(self, "No Servers", "No servers are configured.")
            return
        
        # Get selected servers from tree or use all servers
        selected_items = self.server_tree.selectedItems() if hasattr(self, "server_tree") else []
        
        servers_to_reboot = []
        
        if selected_items:
            # Get servers from selected items
            for item in selected_items:
                parent = item.parent()
                if parent:
                    # This is an interface item, get the server from parent
                    parent_index = self.server_tree.indexOfTopLevelItem(parent)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        if server not in servers_to_reboot:
                            servers_to_reboot.append(server)
                else:
                    # This is a server item
                    server_index = self.server_tree.indexOfTopLevelItem(item)
                    if server_index >= 0 and server_index < len(self.server_interfaces):
                        server = self.server_interfaces[server_index]
                        if server not in servers_to_reboot:
                            servers_to_reboot.append(server)
        
        # If no servers selected, show dialog to select servers
        if not servers_to_reboot:
            dialog = QDialog(self)
            dialog.setWindowTitle("Reboot Physical Server")
            dialog.setGeometry(300, 300, 500, 400)
            
            layout = QVBoxLayout(dialog)
            
            label = QLabel("Select physical server(s) to reboot:")
            layout.addWidget(label)
            
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
            layout.addWidget(list_widget)
            
            # Populate with all servers
            for server in self.server_interfaces:
                address = server.get("address", "Unknown")
                tg_id = server.get("tg_id", "?")
                online_status = "Online" if server.get("online", False) else "Offline"
                item_text = f"TG {tg_id}: {address} ({online_status})"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, server)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                list_widget.addItem(item)
            
            button_layout = QHBoxLayout()
            reboot_button = QPushButton("Reboot")
            reboot_button.clicked.connect(lambda: self._reboot_selected_servers(list_widget, dialog))
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(dialog.reject)
            button_layout.addWidget(reboot_button)
            button_layout.addWidget(cancel_button)
            layout.addLayout(button_layout)
            
            dialog.exec()
            return
        
        # Reboot selected servers
        self._reboot_servers_list(servers_to_reboot)
    
    def _reboot_selected_servers(self, list_widget, dialog):
        """Reboot servers selected in the dialog."""
        servers_to_reboot = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                server = item.data(Qt.UserRole)
                if server:
                    servers_to_reboot.append(server)
        
        dialog.accept()
        
        if not servers_to_reboot:
            QMessageBox.information(self, "No Selection", "Please select at least one server to reboot.")
            return
        
        self._reboot_servers_list(servers_to_reboot)
    
    def _reboot_servers_list(self, servers):
        """Reboot a list of servers via SSH/reboot command."""
        if not servers:
            return
        
        # Confirm reboot with strong warning
        server_names = [f"TG {s.get('tg_id', '?')}: {s.get('address', 'Unknown')}" for s in servers]
        reply = QMessageBox.warning(
            self,
            "Confirm Physical Server Reboot",
            f"⚠️ WARNING: This will REBOOT the entire physical server(s)!\n\n"
            f"Are you sure you want to reboot the following physical server(s)?\n\n" + 
            "\n".join(server_names) + 
            "\n\nThis will:\n"
            "• Stop all running streams\n"
            "• Disconnect all network connections\n"
            "• Require several minutes (3-5 minutes) for the server to come back online\n"
            "• Reset all hardware and firmware\n\n"
            "This is typically needed to fix hardware/firmware issues (e.g., Broadcom NIC firmware).",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Reboot each server
        results = []
        for server in servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Extract hostname from URL
            try:
                parsed = urlparse(address)
                hostname = parsed.hostname
                if not hostname:
                    if "://" in address:
                        hostname = address.split("://")[1].split(":")[0]
                    else:
                        hostname = address.split(":")[0]
            except Exception as e:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not parse server address '{address}': {e}")
                continue
            
            if not hostname:
                QMessageBox.warning(self, "Invalid Server Address", f"Could not extract hostname from '{address}'")
                continue
            
            # Reboot via SSH
            try:
                # Use reboot command (with nohup to ensure it completes even if SSH disconnects)
                cmd = ["ssh", f"root@{hostname}", "reboot"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                
                # Note: reboot command may disconnect SSH immediately, so we check if command was accepted
                # Exit code 255 usually means SSH disconnected (which is expected during reboot)
                if result.returncode == 0 or result.returncode == 255:
                    results.append(f"✅ TG {tg_id} ({hostname}): Reboot initiated successfully")
                    logger.info(f"[REBOOT SERVER] Successfully initiated reboot for server TG {tg_id} on {hostname}")
                else:
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                    results.append(f"❌ TG {tg_id} ({hostname}): Failed - {error_msg}")
                    logger.error(f"[REBOOT SERVER] Failed to reboot server TG {tg_id} on {hostname}: {error_msg}")
            except subprocess.TimeoutExpired:
                # Timeout is actually expected - SSH disconnects when server reboots
                results.append(f"✅ TG {tg_id} ({hostname}): Reboot initiated (SSH disconnected - expected)")
                logger.info(f"[REBOOT SERVER] Reboot initiated for server TG {tg_id} on {hostname} (SSH timeout expected)")
            except FileNotFoundError:
                results.append(f"❌ TG {tg_id} ({hostname}): SSH not found (install OpenSSH client)")
                logger.info(f"[REBOOT SERVER] SSH command not found - OpenSSH client may not be installed")
            except Exception as e:
                results.append(f"❌ TG {tg_id} ({hostname}): Error - {str(e)}")
                logger.info(f"[REBOOT SERVER] Error rebooting server TG {tg_id} on {hostname}: {e}")
        
        # Show results
        result_text = "\n".join(results)
        QMessageBox.information(
            self,
            "Physical Server Reboot Results",
            result_text + "\n\n⚠️ IMPORTANT:\n"
            "• Physical servers are now rebooting\n"
            "• This will take 3-5 minutes\n"
            "• All network connections will be lost\n"
            "• Wait 3-5 minutes before checking server status\n"
            "• After reboot, hardware/firmware issues should be resolved\n"
            "• Interfaces should appear in 'ip link show' after reboot"
        )