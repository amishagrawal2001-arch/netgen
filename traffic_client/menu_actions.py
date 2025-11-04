#menu_actions.py#
import json, os, requests
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView
from PyQt5.QtWidgets import QTableWidgetItem
import uuid
from PyQt5.QtCore import Qt



def sanitize_for_json(obj):
    """Recursively convert non-serializable objects to JSON-safe formats."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    elif hasattr(obj, "text") and callable(obj.text):
        # Handles QLabel, QLineEdit, etc.
        return obj.text()
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
            self.server_interfaces.append({"tg_id": tg_id, "address": full_url})
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

                print(f"Removed server: {server_address} and all associated ports.")

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
                self.server_interfaces.append({"tg_id": tg_id, "address": server_address})
                readded_servers.append(server_address)

        if readded_servers:
            print(f"Re-added servers: {readded_servers}")
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
            print(f"Loaded servers: {self.server_interfaces}")
        except FileNotFoundError:
            print("server_interfaces.txt not found. Starting with an empty server list.")
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
            print("Server interfaces saved successfully.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not save server interfaces: {e}")
    def save_session(self):
        """Save the current session to a JSON file."""
        import traceback
        import time
        # Starting save_session()
        
        # Check if this is a duplicate save call within a short time window
        current_time = time.time()
        if hasattr(self, '_last_save_time') and (current_time - self._last_save_time) < 1.0:
            # Skipping duplicate save call
            return
        self._last_save_time = current_time
        
        # Check if another save is already in progress
        if hasattr(self, '_save_in_progress') and self._save_in_progress:
            # Save already in progress
            return
        self._save_in_progress = True
        
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
            
            # Also save protocol-specific data (BGP, OSPF, ISIS tables)
            protocol_data = {}
            if hasattr(self.devices_tab, "bgp_table"):
                protocol_data["bgp"] = self._extract_table_data(self.devices_tab.bgp_table)
            if hasattr(self.devices_tab, "ospf_table"):
                protocol_data["ospf"] = self._extract_table_data(self.devices_tab.ospf_table)
            if hasattr(self.devices_tab, "isis_table"):
                protocol_data["isis"] = self._extract_table_data(self.devices_tab.isis_table)
        else:
            # No devices_tab found
            pass

        # Track removed devices for session synchronization
        removed_devices = getattr(self, 'removed_devices', [])
        # Found removed devices to save

        # Get BGP route pools if defined
        bgp_route_pools = getattr(self, 'bgp_route_pools', [])
        
        # Determine which servers to save:
        # - If server was provided via CLI, preserve original servers from session.json
        # - Otherwise, save current server_interfaces
        if getattr(self, 'server_url_from_cli', False) and hasattr(self, 'original_session_servers'):
            # CLI mode: preserve original servers from session.json
            servers_to_save = self.original_session_servers
            print(f"[SAVE SESSION] Preserving {len(servers_to_save)} original server(s) from session.json (CLI mode)")
        else:
            # Normal mode: save current servers
            servers_to_save = self.server_interfaces
            print(f"[SAVE SESSION] Saving {len(servers_to_save)} current server(s)")
        
        # Assemble session data
        session_data = {
            "servers": sanitize_for_json(servers_to_save),
            "removed_interfaces": list(self.removed_interfaces),
            "removed_servers": list(self.removed_servers),  # Save removed servers
            "selected_servers": [s["address"] for s in getattr(self, "selected_servers", [])],
            "streams": updated_streams,
            "devices": sanitize_for_json(device_rows),
            "removed_devices": sanitize_for_json(removed_devices),
            "protocols": sanitize_for_json(protocol_data) if 'protocol_data' in locals() else {},
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
            print(f"✅ Session saved successfully")
        except Exception as e:
            print(f"[❌] Failed to save session: {e}")
        finally:
            # Always clear the save lock
            self._save_in_progress = False

    def _cleanup_removed_devices_from_server(self, removed_device_ids, all_loaded_devices):
        """Clean up removed devices from server during session loading."""
        try:
            print(f"[DEBUG CLEANUP] Starting server cleanup for {len(removed_device_ids)} removed devices")
            
            # Get server URL
            if not hasattr(self, "devices_tab") or not self.devices_tab:
                print(f"[DEBUG CLEANUP] No devices_tab available")
                return
            
            server_url = self.devices_tab.get_server_url(silent=True)
            if not server_url:
                print(f"[DEBUG CLEANUP] No server URL available")
                return
            
            import requests
            
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
                    
                    if not device_info:
                        print(f"[DEBUG CLEANUP] Device info not found for ID: {device_id}")
                        continue
                    
                    print(f"[DEBUG CLEANUP] Cleaning up removed device: {device_name} (ID: {device_id})")
                    
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
                    if cleanup_resp.status_code == 200:
                        print(f"[DEBUG CLEANUP] Successfully cleaned up IPs for device: {device_name}")
                    else:
                        print(f"[DEBUG CLEANUP] Cleanup failed for {device_name}: {cleanup_resp.status_code}")
                    
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
                    if remove_resp.status_code == 200:
                        print(f"[DEBUG CLEANUP] Successfully removed device protocols: {device_name}")
                    else:
                        print(f"[DEBUG CLEANUP] Remove API failed for {device_name}: {remove_resp.status_code}")
                        
                except Exception as e:
                    print(f"[ERROR] Failed to cleanup device {device_id}: {e}")
            
            print(f"[DEBUG CLEANUP] Completed server cleanup for removed devices")
            
        except Exception as e:
            print(f"[ERROR] Failed to cleanup removed devices from server: {e}")

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
        if valid_ports:
            self.streams = {p: lst for p, lst in self.streams.items() if p in valid_ports}

        # 2) Normalize structure + IDs
        seen_ids = set()
        repaired = 0

        for port, lst in list(self.streams.items()):
            if not isinstance(lst, list):
                # Corrupt shape; drop it to be safe
                print(f"[WARN] Streams for '{port}' not a list; dropping.")
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
            print(f"[STREAM-ID] Repaired/created {repaired} stream_id(s) during load.")

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
            
            print(f"[DEBUG SESSION] Loaded session.json with {len(session_data.get('devices', {}))} devices")

            # Load removed servers and removed interfaces
            self.removed_servers = set(session_data.get("removed_servers", []))
            self.removed_interfaces = set(session_data.get("removed_interfaces", []))
            
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
                        print(f"[DEBUG LOAD] Added server {server_address} from session")
                    elif server_address in self.removed_servers:
                        print(f"[DEBUG LOAD] Skipped removed server {server_address}")
            else:
                print(f"[DEBUG LOAD] Skipped loading servers from session.json (server provided via CLI)")
                print(f"[DEBUG LOAD] Preserved {len(self.original_session_servers)} original server(s) from session.json for future saves")
            
            self.streams = {}
            self.failed_servers = []
            self.all_devices = {}  # Initialize all_devices

            # Discover valid interfaces from online servers
            valid_ports = set()
            for server in self.server_interfaces:
                tg_id = f"TG {server.get('tg_id', '0')}"
                address = server.get("address")
                if not self.is_reachable(address):
                    print(f"❌ Server unreachable: {address}")
                    server["online"] = False
                    self.failed_servers.append(server)
                    continue
                try:
                    r = requests.get(f"{address}/api/interfaces", timeout=5)
                    r.raise_for_status()
                    interfaces = r.json()
                    server["online"] = True
                    for iface in interfaces:
                        port_name = f"{tg_id} - {iface['name']}"
                        if port_name not in self.removed_interfaces:
                            valid_ports.add(port_name)
                except Exception as e:
                    print(f"⚠️ Error fetching interfaces from {address}: {e}")
                    server["online"] = False
                    self.failed_servers.append(server)

            # Load streams (raw)
            loaded_streams = session_data.get("streams", {})
            if not isinstance(loaded_streams, dict):
                print("⚠️ Session 'streams' malformed; starting with empty.")
                loaded_streams = {}

            self.streams = loaded_streams

            # 🔧 Sanitize + filter + de-dup IDs
            self._sanitize_loaded_streams(valid_ports)

            # Extra safety sweep (your existing guard)
            if hasattr(self, "ensure_unique_stream_ids"):
                self.ensure_unique_stream_ids()

            # Load BGP route pools from session
            self.bgp_route_pools = session_data.get("bgp_route_pools", [])
            print(f"[DEBUG LOAD] Loaded {len(self.bgp_route_pools)} BGP route pool(s)")
            
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
                        print(f"[DEBUG LOAD] Skipping removed device: {device_name} (ID: {device_id})")
                        removed_count += 1
                        continue
                    
                    iface = device_info.get("Interface", "")
                    if iface:
                        if iface not in self.all_devices:
                            self.all_devices[iface] = []
                        self.all_devices[iface].append(device_info)
                        loaded_count += 1
                
                # Clean up removed devices from server if any were found
                if removed_devices and hasattr(self, "devices_tab") and self.devices_tab:
                    print(f"[DEBUG LOAD] Found {len(removed_devices)} removed devices in session - cleaning up from server")
                    self._cleanup_removed_devices_from_server(removed_devices, loaded_devices)
                
                # Load BGP protocols from session
                session_bgp_protocols = session_data.get("protocols", {}).get("bgp", [])
                if session_bgp_protocols:
                    print(f"[DEBUG LOAD] Found {len(session_bgp_protocols)} BGP protocols in session")
                
                # Update devices tab with loaded devices
                if hasattr(self, "devices_tab") and self.devices_tab:
                    self.devices_tab.all_devices = self.all_devices.copy()
                    self.devices_tab.update_device_table(self.all_devices)
                    # Update BGP table with loaded BGP configurations
                    self.devices_tab.update_bgp_table()
                    print(f"[DEBUG LOAD] Loaded {loaded_count} devices from session, skipped {removed_count} removed devices")
            else:
                print("[DEBUG LOAD] No valid devices found in session")
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

            print(
                f"✅ Session loaded: {sum(len(v) for v in self.streams.values())} streams across {len(self.streams)} ports.")

        except FileNotFoundError:
            print("No session file found. Starting fresh.")
            self._initialize_empty_session()
        except Exception as e:
            print(f"❌ Failed to load session: {e}")
            self._initialize_empty_session()
    def reset_session(self):
        """Reset the session data to default."""
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
        print(f"[MAKE SERVER ONLINE] Selected TG IDs: {selected_tg_ids}")
        print(f"[MAKE SERVER ONLINE] Selected servers count: {len(selected_servers)}")
        
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
        
        print(f"🛠️ Attempting reconnection to {len(selected_failed_servers)} selected failed server(s): " +
              ", ".join([s.get('address', 'Unknown') for s in selected_failed_servers]))
        
        any_reconnected = False

        for server in selected_failed_servers[:]:  # Iterate over a copy since we may modify it
            address = server.get("address")
            print(f"🔄 Trying to bring selected server {address} online...")

            try:
                # Use connection manager if available
                if hasattr(self, 'connection_manager') and self.connection_manager:
                    response = self.connection_manager.get(f"{address}/api/interfaces", timeout=2)
                else:
                    response = requests.get(f"{address}/api/interfaces", timeout=2)
                
                if response.status_code == 200:
                    server["online"] = True
                    self.update_server_status_icon(server, True)
                    print(f"✅ Selected server {address} is now online.")
                    any_reconnected = True
                    self.failed_servers.remove(server)  # ✅ Remove from failed list
                    
                    # Remove from retry worker if it exists
                    if hasattr(self, 'server_retry_worker') and self.server_retry_worker:
                        self.server_retry_worker.remove_failed_server(server)
                else:
                    print(f"❌ Selected server {address} still unreachable (status {response.status_code})")
            except requests.RequestException as e:
                print(f"❌ Still failed to connect to selected server {address}: {e}")

        if any_reconnected:
            QMessageBox.information(self, "Servers Updated", "Some servers are now back online.")
            self.update_server_tree()
            self.fetch_and_update_statistics()
        else:
            QMessageBox.information(self, "No Servers Recovered", "No offline servers could be brought online.")
    
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
        print("Initializing an empty session.")
        # Don't overwrite server_interfaces if servers were added via command line
        if not self.server_interfaces:
            self.server_interfaces = []
        self.streams = {}
        self.removed_interfaces = set()
        self.removed_servers = set()  # Initialize removed servers
        self.selected_servers = []

        # Check if servers are reachable and update their status
        if self.server_interfaces:
            print(f"Checking {len(self.server_interfaces)} server(s) for connectivity...")
            for server in self.server_interfaces:
                address = server.get("address")
                if self.is_reachable(address):
                    print(f"✅ Server {address} is reachable")
                    server["online"] = True
                    try:
                        # Use shorter timeout to prevent hanging during initialization
                        r = requests.get(f"{address}/api/interfaces", timeout=2)
                        r.raise_for_status()
                        interfaces = r.json()
                        server["interfaces"] = interfaces  # Store interfaces for update_server_tree
                        print(f"✅ Fetched {len(interfaces)} interfaces from {address}")
                    except Exception as e:
                        print(f"❌ Error fetching interfaces from {address}: {e}")
                        server["online"] = False
                else:
                    print(f"❌ Server {address} is unreachable")
                    server["online"] = False

        # Update UI components to reflect the reset state
        if hasattr(self, "update_server_tree"):
            self.update_server_tree()
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()
        print("Empty session initialized.")
    def is_reachable(self, server_url, timeout=2):
        """Check if a traffic generator server is reachable."""
        try:
            response = requests.get(f"{server_url}/api/ping", timeout=timeout)
            return response.status_code == 200
        except Exception:
            return False