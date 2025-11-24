"""
VXLAN-specific UI logic extracted from devices_tab.py
"""

import json
import logging
import requests

# Configure logging to show DEBUG messages in console
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtWidgets import (
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QTreeWidgetItem,
)

from utils.qicon_loader import qicon


class VXLANHandler:
    """Handler for VXLAN status tab."""

    REFRESH_INTERVAL_MS = 10000  # 10 seconds

    def __init__(self, parent_tab):
        self.parent = parent_tab
        self._timer = QTimer(self.parent)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh_vxlan_table)
        self._monitoring_active = False

    def setup_vxlan_subtab(self):
        layout = QVBoxLayout(self.parent.vxlan_subtab)

        table_headers = [
            "Device",
            "Status",
            "VXLAN Interface",
            "Underlay Interface",
            "Overlay Interface",
            "VNI",
            "Local Endpoint",
            "Remote Endpoint(s)",
            "UDP Port",
            "Last Updated",
            "Last Error",
        ]
        self.parent.vxlan_table = QTableWidget(0, len(table_headers))
        self.parent.vxlan_table.setHorizontalHeaderLabels(table_headers)
        self.parent.VXLAN_COL = {h: i for i, h in enumerate(table_headers)}
        self.parent.vxlan_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.parent.vxlan_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout.addWidget(QLabel("VXLAN Tunnel Status"))
        layout.addWidget(self.parent.vxlan_table)

        controls = QHBoxLayout()
        
        # Add VXLAN button
        self.parent.add_vxlan_button = QPushButton()
        self.parent.add_vxlan_button.setIcon(qicon("resources", "icons/add.png"))
        self.parent.add_vxlan_button.setIconSize(QSize(16, 16))
        self.parent.add_vxlan_button.setFixedSize(32, 28)
        self.parent.add_vxlan_button.setToolTip("Add VXLAN Tunnel")
        self.parent.add_vxlan_button.clicked.connect(self.parent.prompt_add_vxlan)
        controls.addWidget(self.parent.add_vxlan_button)
        
        # Apply VXLAN button
        self.parent.apply_vxlan_button = QPushButton()
        self.parent.apply_vxlan_button.setIcon(qicon("resources", "icons/apply.png"))
        self.parent.apply_vxlan_button.setIconSize(QSize(16, 16))
        self.parent.apply_vxlan_button.setFixedSize(32, 28)
        self.parent.apply_vxlan_button.setToolTip("Apply VXLAN configurations to server")
        self.parent.apply_vxlan_button.clicked.connect(self.parent.apply_vxlan_configurations)
        controls.addWidget(self.parent.apply_vxlan_button)
        
        # Delete VXLAN button
        self.parent.delete_vxlan_button = QPushButton()
        self.parent.delete_vxlan_button.setIcon(qicon("resources", "icons/Trash.png"))
        self.parent.delete_vxlan_button.setIconSize(QSize(16, 16))
        self.parent.delete_vxlan_button.setFixedSize(32, 28)
        self.parent.delete_vxlan_button.setToolTip("Delete Selected VXLAN Tunnel(s)")
        self.parent.delete_vxlan_button.clicked.connect(self.delete_selected_vxlan_tunnels)
        controls.addWidget(self.parent.delete_vxlan_button)
        
        refresh_button = QPushButton()
        refresh_button.setIcon(qicon("resources", "icons/refresh.png"))
        refresh_button.setIconSize(QSize(16, 16))
        refresh_button.setFixedSize(32, 28)
        refresh_button.setToolTip("Refresh VXLAN status")
        refresh_button.clicked.connect(self.refresh_vxlan_table)
        controls.addWidget(refresh_button)
        controls.addStretch()
        layout.addLayout(controls)

        # Kick off initial refresh shortly after tab creation
        QTimer.singleShot(200, self.refresh_vxlan_table)
        # Ensure periodic monitoring starts even before VXLAN rows exist
        self.start_monitoring()

    def start_monitoring(self):
        if not self._monitoring_active:
            self._timer.start()
            self._monitoring_active = True

    def stop_monitoring(self):
        if self._monitoring_active:
            self._timer.stop()
            self._monitoring_active = False

    def refresh_vxlan_table(self):
        """Refresh the VXLAN status table from database and local memory."""
        try:
            print("[VXLAN TAB] Starting refresh_vxlan_table")
            logging.debug("[VXLAN TAB] Starting refresh_vxlan_table")
            server_url = self.parent.get_server_url(silent=True)
            
            if not server_url:
                print("[VXLAN TAB] No server URL available, using local data only")
                logging.warning("[VXLAN TAB] No server URL available for refresh")
            
            # Collect devices from both database and local memory
            devices_from_db = []
            if server_url:
                try:
                    response = requests.get(f"{server_url}/api/device/database/devices", timeout=5)
                    if response.status_code == 200:
                        payload = response.json()
                        if isinstance(payload, dict):
                            devices_from_db = payload.get("devices", [])
                        elif isinstance(payload, list):
                            devices_from_db = payload
                        print(f"[VXLAN TAB] Fetched {len(devices_from_db)} devices from database")
                        logging.debug(f"[VXLAN TAB] Fetched {len(devices_from_db)} devices from database")
                    else:
                        print(f"[VXLAN TAB] Failed to fetch from database: HTTP {response.status_code}")
                        logging.warning(f"[VXLAN TAB] Failed to fetch from database: HTTP {response.status_code}")
                except requests.exceptions.RequestException as exc:
                    print(f"[VXLAN TAB] Network error fetching VXLAN data from database: {exc}")
                    logging.warning(f"[VXLAN TAB] Network error fetching VXLAN data from database: {exc}")
                except Exception as exc:
                    print(f"[VXLAN TAB] Error fetching VXLAN data from database: {exc}")
                    logging.warning(f"[VXLAN TAB] Error fetching VXLAN data from database: {exc}")
            else:
                print("[VXLAN TAB] No server URL, skipping database fetch")

            # Also check local device data (for unapplied configurations)
            devices_from_local = []
            if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "all_devices"):
                print(f"[VXLAN TAB] Checking local devices, total interfaces: {len(self.parent.main_window.all_devices)}")
                for iface, device_list in self.parent.main_window.all_devices.items():
                    print(f"[VXLAN TAB] Checking interface: {iface}, devices: {len(device_list)}")
                    for device in device_list:
                        device_name = device.get("Device Name", "Unknown")
                        print(f"[VXLAN TAB] Checking device: {device_name}")
                        # Check for VXLAN config - it's stored as "vxlan_config" key
                        vxlan_cfg = device.get("vxlan_config", {})
                        # Handle case where vxlan_cfg might be a string (from database)
                        if isinstance(vxlan_cfg, str):
                            try:
                                vxlan_cfg = json.loads(vxlan_cfg) if vxlan_cfg else {}
                            except Exception:
                                vxlan_cfg = {}
                        
                        # Also check if VXLAN is in protocols list
                        protocols = device.get("protocols", [])
                        if isinstance(protocols, str):
                            protocols = [p.strip() for p in protocols.split(",") if p.strip()]
                        has_vxlan_protocol = "VXLAN" in protocols
                        
                        # Show device if it has VXLAN config (non-empty dict) or VXLAN in protocols
                        print(f"[VXLAN TAB] Device {device_name}: vxlan_cfg={bool(vxlan_cfg)}, type={type(vxlan_cfg)}, len={len(vxlan_cfg) if isinstance(vxlan_cfg, dict) else 'N/A'}, has_vxlan_protocol={has_vxlan_protocol}")
                        if (vxlan_cfg and isinstance(vxlan_cfg, dict) and len(vxlan_cfg) > 0) or has_vxlan_protocol:
                            # Create a device dict compatible with database format
                            local_device = {
                                "device_id": device.get("device_id", ""),
                                "device_name": device.get("Device Name", ""),
                                "interface": device.get("Interface", ""),
                                "vlan": device.get("VLAN", "0"),
                                "vxlan_config": vxlan_cfg if vxlan_cfg else {},
                                "vxlan_state": "Pending",  # Mark as pending until applied
                            }
                            devices_from_local.append(local_device)
                            print(f"[VXLAN TAB] Found local device with VXLAN: {local_device.get('device_name')}, config keys: {list(vxlan_cfg.keys()) if vxlan_cfg else 'none'}")
                            logging.debug(f"[VXLAN TAB] Found local device with VXLAN: {local_device.get('device_name')}, config keys: {list(vxlan_cfg.keys()) if vxlan_cfg else 'none'}")

            # Merge devices: prefer database entries (they have device_id), but include local-only entries
            device_map = {}
            for device in devices_from_db:
                device_id = device.get("device_id")
                device_name = device.get("device_name")
                key = device_id or device_name
                if key:
                    device_map[key] = device
            
            # Get selected interfaces from server_tree (same logic as device/BGP/OSPF/ISIS tables)
            selected_interfaces = set()
            if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, 'server_tree') and self.parent.main_window.server_tree:
                tree = self.parent.main_window.server_tree
                for item in tree.selectedItems():
                    parent = item.parent()
                    if parent:
                        # This is a child item (interface) - extract TG ID and port name
                        # Extract TG ID from the custom widget in column 0 of parent
                        tg_id_widget = tree.itemWidget(parent, 0)
                        tg_id = None
                        if tg_id_widget:
                            # Find the QLabel containing the TG ID text
                            for child in tg_id_widget.findChildren(QLabel):
                                text = child.text()
                                if text.startswith("TG "):
                                    tg_id = text.strip()
                                    break
                        
                        # Fallback: extract from server_interfaces using parent index
                        if not tg_id:
                            parent_index = tree.indexOfTopLevelItem(parent)
                            if parent_index >= 0 and hasattr(self.parent.main_window, "server_interfaces"):
                                if parent_index < len(self.parent.main_window.server_interfaces):
                                    server = self.parent.main_window.server_interfaces[parent_index]
                                    tg_id = f"TG {server.get('tg_id', '0')}"
                        
                        port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix if present
                        if tg_id and port_name:
                            selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
                    else:
                        # This is a parent item (TG) - show all interfaces for this TG
                        # Extract TG ID from the custom widget in column 0
                        tg_id_widget = tree.itemWidget(item, 0)
                        tg_id = None
                        if tg_id_widget:
                            # Find the QLabel containing the TG ID text
                            for child in tg_id_widget.findChildren(QLabel):
                                text = child.text()
                                if text.startswith("TG "):
                                    tg_id = text.strip()
                                    break
                        
                        # Fallback: extract from server_interfaces using item index
                        if not tg_id:
                            item_index = tree.indexOfTopLevelItem(item)
                            if item_index >= 0 and hasattr(self.parent.main_window, "server_interfaces"):
                                if item_index < len(self.parent.main_window.server_interfaces):
                                    server = self.parent.main_window.server_interfaces[item_index]
                                    tg_id = f"TG {server.get('tg_id', '0')}"
                        
                        # If TG is selected, add all interfaces for this TG
                        if tg_id and hasattr(self.parent.main_window, "all_devices"):
                            for iface_key in self.parent.main_window.all_devices.keys():
                                if iface_key.startswith(f"{tg_id} - "):
                                    selected_interfaces.add(iface_key)
            
            # Filter devices by selected interfaces (same logic as other tables)
            # If interfaces are selected, only show devices from those interfaces
            # If no interfaces selected, show all devices
            interfaces_to_show = selected_interfaces if selected_interfaces else None
            
            # Add local devices that aren't in database yet
            for device in devices_from_local:
                # Filter by interface if interfaces are selected
                if interfaces_to_show:
                    device_interface = device.get("Interface", "")
                    # Match format: "TG X - interface_name"
                    device_interface_key = None
                    if device_interface:
                        # Try to find matching interface in all_devices
                        if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "all_devices"):
                            for iface_key in self.parent.main_window.all_devices.keys():
                                if device_interface in iface_key or iface_key.endswith(f" - {device_interface}"):
                                    device_interface_key = iface_key
                                    break
                    
                    # If we couldn't match, try direct match
                    if not device_interface_key:
                        device_interface_key = device_interface
                    
                    if device_interface_key not in interfaces_to_show:
                        continue  # Skip devices not from selected interfaces
                
                device_id = device.get("device_id")
                device_name = device.get("device_name")
                key = device_id or device_name
                if key and key not in device_map:
                    device_map[key] = device
                    print(f"[VXLAN TAB] Added local-only device to map: {device_name}")
                elif key in device_map:
                    # Device exists in both DB and local - DB is source of truth for applied tunnels
                    # Only merge if local has new tunnels not yet in DB
                    db_device = device_map[key]
                    local_vxlan_cfg = device.get("vxlan_config", {})
                    db_vxlan_cfg = db_device.get("vxlan_config", {})
                    
                    # Parse if strings
                    if isinstance(local_vxlan_cfg, str):
                        try:
                            local_vxlan_cfg = json.loads(local_vxlan_cfg) if local_vxlan_cfg else {}
                        except Exception:
                            local_vxlan_cfg = {}
                    if isinstance(db_vxlan_cfg, str):
                        try:
                            db_vxlan_cfg = json.loads(db_vxlan_cfg) if db_vxlan_cfg else {}
                        except Exception:
                            db_vxlan_cfg = {}
                    
                    # Extract tunnels from both sources
                    local_tunnels = []
                    if isinstance(local_vxlan_cfg, dict):
                        if "tunnels" in local_vxlan_cfg and isinstance(local_vxlan_cfg["tunnels"], list):
                            local_tunnels = local_vxlan_cfg["tunnels"]
                        elif local_vxlan_cfg and any(k in local_vxlan_cfg for k in ['vni', 'local_ip', 'remote_peers', 'bridge_svi_ip']):
                            local_tunnels = [local_vxlan_cfg]
                    
                    db_tunnels = []
                    if isinstance(db_vxlan_cfg, dict):
                        if "tunnels" in db_vxlan_cfg and isinstance(db_vxlan_cfg["tunnels"], list):
                            db_tunnels = db_vxlan_cfg["tunnels"]
                        elif db_vxlan_cfg and any(k in db_vxlan_cfg for k in ['vni', 'local_ip', 'remote_peers', 'bridge_svi_ip']):
                            db_tunnels = [db_vxlan_cfg]
                    
                    # Filter out empty/invalid tunnels from DB (those with empty VNI or no actual config)
                    valid_db_tunnels = []
                    for db_tunnel in db_tunnels:
                        if isinstance(db_tunnel, dict):
                            vni = db_tunnel.get('vni')
                            # Check if VNI is valid (not None, not empty string)
                            if vni and (isinstance(vni, str) and vni.strip()) or (not isinstance(vni, str) and vni):
                                valid_db_tunnels.append(db_tunnel)
                            else:
                                print(f"[VXLAN TAB] Filtering out empty DB tunnel (no VNI) for {device_name}")
                    db_tunnels = valid_db_tunnels
                    
                    # For devices in DB, use DB tunnels as source of truth
                    # Only add local tunnels that are truly new (not in DB by VNI)
                    merged_tunnels = []
                    db_vnis = {t.get("vni") for t in db_tunnels if isinstance(t, dict) and t.get("vni")}
                    
                    # Start with DB tunnels (source of truth for applied tunnels)
                    merged_tunnels.extend(db_tunnels)
                    
                    # Only add local tunnels that don't exist in DB (new, unapplied tunnels)
                    for local_tunnel in local_tunnels:
                        if isinstance(local_tunnel, dict):
                            local_vni = local_tunnel.get("vni")
                            if local_vni and local_vni not in db_vnis:
                                # This is a new tunnel not yet in DB - add it
                                merged_tunnels.append(local_tunnel)
                                print(f"[VXLAN TAB] Adding new local tunnel VNI {local_vni} not yet in DB")
                    
                    if merged_tunnels:
                        print(f"[VXLAN TAB] Merging: Using {len(db_tunnels)} DB tunnel(s) + {len([t for t in local_tunnels if isinstance(t, dict) and t.get('vni') not in db_vnis])} new local tunnel(s) = {len(merged_tunnels)} total for {device_name}")
                        db_device["vxlan_config"] = {"tunnels": merged_tunnels}
                        if not db_device.get("vxlan_state") or db_device.get("vxlan_state") == "Disabled":
                            db_device["vxlan_state"] = "Pending"
                    elif db_tunnels:
                        # Only DB tunnels (no new local tunnels)
                        print(f"[VXLAN TAB] Merging: Using {len(db_tunnels)} DB tunnel(s) for {device_name} (no new local tunnels)")
                        db_device["vxlan_config"] = {"tunnels": db_tunnels}
                    else:
                        # No tunnels in DB and no new local tunnels - clear config
                        print(f"[VXLAN TAB] Merging: No tunnels in DB or local for {device_name}, clearing config")
                        db_device["vxlan_config"] = {}

            devices = list(device_map.values())
            print(f"[VXLAN TAB] Total devices after merge: {len(devices)} (DB: {len(devices_from_db)}, Local: {len(devices_from_local)})")
            logging.debug(f"[VXLAN TAB] Total devices after merge: {len(devices)} (DB: {len(devices_from_db)}, Local: {len(devices_from_local)})")

            # Filter devices by selected interfaces (same logic as other tables)
            # Use same filtering logic as device/BGP/OSPF/ISIS tables
            filtered_devices = []
            if interfaces_to_show:
                # Only show devices from selected interfaces
                # Build a set of device identifiers from selected interfaces
                selected_device_ids = set()
                selected_device_names = set()
                if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "all_devices"):
                    print(f"[VXLAN TAB] Selected interfaces: {interfaces_to_show}")
                    print(f"[VXLAN TAB] Available interface keys in all_devices: {list(self.parent.main_window.all_devices.keys())}")
                    
                    for iface in interfaces_to_show:
                        # Try exact match first
                        iface_devices = self.parent.main_window.all_devices.get(iface, [])
                        
                        # If no exact match, try to find matching interface key
                        # The interface key format might be different (e.g., " - ens4np0" instead of "TG 0 - ens4np0")
                        if not iface_devices:
                            # Extract port name from selected interface (e.g., "ens4np0" from "TG 0 - ens4np0")
                            port_name = iface.split(" - ")[-1] if " - " in iface else iface
                            # Try to find interface key that ends with this port name
                            for key in self.parent.main_window.all_devices.keys():
                                if key.endswith(f" - {port_name}") or key == port_name or key.endswith(port_name):
                                    iface_devices = self.parent.main_window.all_devices.get(key, [])
                                    print(f"[VXLAN TAB] Found matching interface key '{key}' for selected interface '{iface}'")
                                    break
                        
                        print(f"[VXLAN TAB] Interface '{iface}' has {len(iface_devices)} device(s)")
                        for iface_device in iface_devices:
                            device_id = iface_device.get("device_id")
                            device_name = iface_device.get("Device Name") or iface_device.get("device_name")
                            if device_id:
                                selected_device_ids.add(device_id)
                                print(f"[VXLAN TAB] Added device_id to filter: {device_id}")
                            if device_name:
                                selected_device_names.add(device_name)
                                print(f"[VXLAN TAB] Added device_name to filter: {device_name}")
                    
                    print(f"[VXLAN TAB] Filtering {len(devices)} merged devices using {len(selected_device_ids)} device_id(s) and {len(selected_device_names)} device_name(s)")
                    
                    # Filter merged devices to only include those from selected interfaces
                    for device in devices:
                        dev_id = device.get("device_id")
                        dev_name = device.get("device_name") or device.get("Device Name")
                        match_by_id = dev_id and dev_id in selected_device_ids
                        match_by_name = dev_name and dev_name in selected_device_names
                        if match_by_id or match_by_name:
                            filtered_devices.append(device)
                            print(f"[VXLAN TAB] Matched device: {dev_name} (id={dev_id}, match_by_id={match_by_id}, match_by_name={match_by_name})")
                        else:
                            print(f"[VXLAN TAB] Skipped device: {dev_name} (id={dev_id}) - not in selected interfaces")
            else:
                # No interfaces selected, show all devices (same as other tables)
                filtered_devices = devices
            
            print(f"[VXLAN TAB] Filtered devices: {len(filtered_devices)} (selected interfaces: {len(interfaces_to_show) if interfaces_to_show else 'all'})")

            rows = []
            for device in filtered_devices:
                if not isinstance(device, dict):
                    logging.debug("[VXLAN TAB] Skipping non-dict device entry: %s", device)
                    continue
                vxlan_cfg = device.get("vxlan_config")
                try:
                    if isinstance(vxlan_cfg, str):
                        vxlan_cfg = json.loads(vxlan_cfg) if vxlan_cfg else {}
                except Exception:
                    vxlan_cfg = {}

                # Handle multiple tunnels format: {"tunnels": [tunnel1, tunnel2, ...]}
                tunnels = []
                if isinstance(vxlan_cfg, dict):
                    if "tunnels" in vxlan_cfg and isinstance(vxlan_cfg["tunnels"], list):
                        # New format: list of tunnels
                        tunnels = vxlan_cfg["tunnels"]
                        print(f"[VXLAN TAB] Device {device.get('device_name')}: Found {len(tunnels)} tunnel(s) in list format")
                    elif vxlan_cfg and len(vxlan_cfg) > 0:
                        # Old format: single tunnel dict (backward compatibility)
                        # Check if it has actual VXLAN settings with non-empty values (not just empty keys)
                        has_vni = vxlan_cfg.get('vni') and str(vxlan_cfg.get('vni')).strip()
                        has_local_ip = vxlan_cfg.get('local_ip') and str(vxlan_cfg.get('local_ip')).strip()
                        has_remote_peers = vxlan_cfg.get('remote_peers') and str(vxlan_cfg.get('remote_peers')).strip()
                        has_bridge_svi_ip = vxlan_cfg.get('bridge_svi_ip') and str(vxlan_cfg.get('bridge_svi_ip')).strip()
                        
                        if has_vni or has_local_ip or has_remote_peers or has_bridge_svi_ip:
                            tunnels = [vxlan_cfg]
                            print(f"[VXLAN TAB] Device {device.get('device_name')}: Found 1 tunnel in old format (single dict)")
                        else:
                            print(f"[VXLAN TAB] Device {device.get('device_name')}: Skipping empty VXLAN config (has keys but no values)")
                
                # If no tunnels found, check if we should still show this device
                if not tunnels:
                    cfg_enabled = bool(device.get("vxlan_enabled"))
                    vxlan_state = device.get("vxlan_state", "")
                    has_interface = bool(device.get("vxlan_interface"))
                    
                    # Skip devices where VXLAN was completely removed/disabled
                    # If state is "Disabled", it means VXLAN was removed - don't show in table
                    if vxlan_state == "Disabled":
                        print(f"[VXLAN TAB] Skipping device {device.get('device_name')} - VXLAN disabled/removed (state: {vxlan_state})")
                        continue
                    
                    # If no tunnels, no enabled flag, and no interface, skip
                    if not cfg_enabled and not has_interface:
                        print(f"[VXLAN TAB] Skipping device {device.get('device_name')} - no VXLAN tunnels/config/status")
                        continue
                
                # Create one row per tunnel
                for tunnel_idx, tunnel_cfg in enumerate(tunnels):
                    if isinstance(tunnel_cfg, dict) and tunnel_cfg:
                        # Skip tunnels with empty VNI or no actual configuration
                        vni = tunnel_cfg.get('vni')
                        if not vni or (isinstance(vni, str) and not vni.strip()) or (vni is None):
                            print(f"[VXLAN TAB] Skipping tunnel {tunnel_idx+1}/{len(tunnels)} for device {device.get('device_name')} - empty VNI")
                            continue
                        
                        # Create a device copy for this tunnel (so each tunnel gets its own row)
                        tunnel_device = device.copy()
                        # Store the tunnel index for reference
                        tunnel_device["_tunnel_index"] = tunnel_idx
                        tunnel_device["_tunnel_count"] = len(tunnels)
                        rows.append((tunnel_device, tunnel_cfg))
                        print(f"[VXLAN TAB] Added tunnel {tunnel_idx+1}/{len(tunnels)} for device {device.get('device_name')}, VNI: {tunnel_cfg.get('vni')} (total rows: {len(rows)})")

            print(f"[VXLAN TAB] Populating table with {len(rows)} rows")
            logging.debug(f"[VXLAN TAB] Populating table with {len(rows)} rows")
            
            # Clear table
            current_row_count = self.parent.vxlan_table.rowCount()
            print(f"[VXLAN TAB] Clearing table (current rows: {current_row_count})")
            self.parent.vxlan_table.setRowCount(0)
            
            # Add rows
            for idx, (device, vxlan_cfg) in enumerate(rows):
                print(f"[VXLAN TAB] Adding row {idx+1}/{len(rows)} for device: {device.get('device_name')}, VNI: {vxlan_cfg.get('vni') if isinstance(vxlan_cfg, dict) else 'N/A'}")
                self._append_row(device, vxlan_cfg)
                # Verify row was added
                actual_rows = self.parent.vxlan_table.rowCount()
                print(f"[VXLAN TAB] After adding row {idx+1}, table has {actual_rows} rows")

            # Force table update/refresh
            self.parent.vxlan_table.viewport().update()
            self.parent.vxlan_table.update()
            self.parent.vxlan_table.repaint()
            
            # Resize columns to fit content
            self.parent.vxlan_table.resizeColumnsToContents()
            
            # Ensure table is shown and visible
            self.parent.vxlan_table.show()
            
            # Force the parent widget to update
            if hasattr(self.parent, "vxlan_subtab"):
                self.parent.vxlan_subtab.update()
                self.parent.vxlan_subtab.repaint()
            
            # Keep periodic monitoring active; cleanup_threads() will stop it
            if not self._monitoring_active:
                self.start_monitoring()
            
            final_row_count = self.parent.vxlan_table.rowCount()
            print(f"[VXLAN TAB] Refresh complete, table now has {final_row_count} rows")
            print(f"[VXLAN TAB] Table visible: {self.parent.vxlan_table.isVisible()}, enabled: {self.parent.vxlan_table.isEnabled()}")
            if final_row_count > 0:
                # Check first row has data
                device_col = self.parent.VXLAN_COL.get("Device")
                status_col = self.parent.VXLAN_COL.get("Status")
                if device_col is not None and status_col is not None:
                    first_row_device = self.parent.vxlan_table.item(0, device_col)
                    first_row_status = self.parent.vxlan_table.item(0, status_col)
                    print(f"[VXLAN TAB] First row device item: {first_row_device.text() if first_row_device else 'None'}")
                    print(f"[VXLAN TAB] First row status item: {first_row_status is not None}")
            logging.debug(f"[VXLAN TAB] Refresh complete, table now has {final_row_count} rows")
        except Exception as e:
            print(f"[VXLAN TAB] ERROR during refresh: {e}")
            logging.error(f"[VXLAN TAB] ERROR during refresh: {e}", exc_info=True)
            # Show error to user
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.parent,
                "Refresh Failed",
                f"Failed to refresh VXLAN status table:\n{str(e)}"
            )

    def _append_row(self, device, vxlan_cfg):
        row = self.parent.vxlan_table.rowCount()
        print(f"[VXLAN TAB] _append_row: Inserting row {row} for device {device.get('device_name') or device.get('Device Name')}")
        self.parent.vxlan_table.insertRow(row)

        device_name = device.get("device_name") or device.get("Device Name") or "Unknown"
        # If multiple tunnels, show tunnel number
        tunnel_count = device.get("_tunnel_count", 1)
        tunnel_index = device.get("_tunnel_index", 0)
        if tunnel_count > 1:
            display_name = f"{device_name} (Tunnel {tunnel_index + 1}/{tunnel_count})"
        else:
            display_name = device_name
        print(f"[VXLAN TAB] _append_row: Setting device name '{display_name}' at row {row}, col {self.parent.VXLAN_COL['Device']}")
        device_item = QTableWidgetItem(display_name)
        self.parent.vxlan_table.setItem(row, self.parent.VXLAN_COL["Device"], device_item)

        # Set status with both icon and text for better visibility
        state = (device.get("vxlan_state") or "Pending").strip()
        last_error = device.get("vxlan_last_error", "")
        
        print(f"[VXLAN TAB] _append_row: Setting status for row {row}, state='{state}'")

        status_text = ""
        if state.lower() in {"configured", "up", "running"}:
            status_text = "Configured"
            status_item = QTableWidgetItem(status_text)
            status_item.setIcon(self.parent.green_dot)
            status_item.setToolTip("VXLAN Configured")
        elif state.lower() == "error":
            status_text = "Error"
            status_item = QTableWidgetItem(status_text)
            status_item.setIcon(self.parent.red_dot)
            status_item.setToolTip(last_error or "VXLAN Error")
        else:
            # Default to orange dot for Pending/Disabled states
            status_text = state if state else "Pending"
            status_item = QTableWidgetItem(status_text)
            status_item.setIcon(self.parent.orange_dot)
            status_item.setToolTip(f"VXLAN {state}")
        
        status_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        
        status_col_idx = self.parent.VXLAN_COL.get("Status")
        if status_col_idx is None:
            print(f"[VXLAN TAB] _append_row: WARNING - Status column not found!")
        else:
            # Validate column index is within bounds
            max_col = self.parent.vxlan_table.columnCount()
            if status_col_idx < 0 or status_col_idx >= max_col:
                print(f"[VXLAN TAB] _append_row: WARNING - Status column index {status_col_idx} is out of bounds (max: {max_col-1})")
            else:
                print(f"[VXLAN TAB] _append_row: Setting status '{status_text}' at row {row}, col {status_col_idx}")
                self.parent.vxlan_table.setItem(row, status_col_idx, status_item)

        def _set(col, value):
            col_idx = self.parent.VXLAN_COL.get(col)
            if col_idx is None:
                print(f"[VXLAN TAB] _append_row: WARNING - Column '{col}' not found in VXLAN_COL")
                return
            # Validate column index is within bounds
            max_col = self.parent.vxlan_table.columnCount()
            if col_idx < 0 or col_idx >= max_col:
                print(f"[VXLAN TAB] _append_row: WARNING - Column index {col_idx} for '{col}' is out of bounds (max: {max_col-1})")
                return
            item = QTableWidgetItem(str(value) if value else "")
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.parent.vxlan_table.setItem(row, col_idx, item)
            print(f"[VXLAN TAB] _append_row: Set {col}='{value}' at row {row}, col {col_idx}")

        print(f"[VXLAN TAB] _append_row: vxlan_cfg type: {type(vxlan_cfg)}, keys: {list(vxlan_cfg.keys()) if isinstance(vxlan_cfg, dict) else 'N/A'}")
        # Prioritize tunnel-specific interface name over device-level (which is comma-separated for multiple tunnels)
        tunnel_interface = vxlan_cfg.get("vxlan_interface") if isinstance(vxlan_cfg, dict) else None
        if tunnel_interface:
            _set("VXLAN Interface", tunnel_interface)
        else:
            # Fall back to device-level interface (for backward compatibility with single tunnel)
            device_interface = device.get("vxlan_interface") or ""
            # Ensure device_interface is a string (handle None case)
            if not isinstance(device_interface, str):
                device_interface = str(device_interface) if device_interface else ""
            # If it's a comma-separated list, try to extract the relevant one based on VNI
            if device_interface and "," in device_interface and isinstance(vxlan_cfg, dict):
                # For multiple tunnels, we can't determine which interface belongs to which tunnel
                # So just show the first one as a fallback
                _set("VXLAN Interface", device_interface.split(",")[0].strip())
            else:
                _set("VXLAN Interface", device_interface)
        _set("Underlay Interface", vxlan_cfg.get("underlay_interface") if isinstance(vxlan_cfg, dict) else "" or device.get("interface", ""))
        _set("Overlay Interface", vxlan_cfg.get("overlay_interface") if isinstance(vxlan_cfg, dict) else "" or f"vlan{device.get('vlan', '0')}")
        _set("VNI", str(vxlan_cfg.get("vni") or "") if isinstance(vxlan_cfg, dict) else "")
        _set("Local Endpoint", vxlan_cfg.get("local_ip") if isinstance(vxlan_cfg, dict) else "" or device.get("ipv4_address", ""))
        remote_peers = vxlan_cfg.get("remote_peers", []) if isinstance(vxlan_cfg, dict) else []
        _set("Remote Endpoint(s)", ", ".join(remote_peers) if remote_peers else "")
        _set("UDP Port", str(vxlan_cfg.get("udp_port") or "4789") if isinstance(vxlan_cfg, dict) else "4789")
        _set("Last Updated", device.get("vxlan_updated_at", ""))
        _set("Last Error", device.get("vxlan_last_error", ""))
        
        # Store device_id, device_name, and VNI in all items for deletion
        device_id = device.get("device_id", "")
        device_name = device.get("device_name") or device.get("Device Name", "")
        vni = vxlan_cfg.get("vni") if isinstance(vxlan_cfg, dict) else None
        metadata = {
            "device_id": device_id,
            "device_name": device_name,
            "vni": vni,
            "vxlan_cfg": vxlan_cfg if isinstance(vxlan_cfg, dict) else {}
        }
        # Store metadata in all items in this row
        for col_idx in range(self.parent.vxlan_table.columnCount()):
            item = self.parent.vxlan_table.item(row, col_idx)
            if item:
                item.setData(Qt.UserRole, metadata)
        
        print(f"[VXLAN TAB] _append_row: Completed row {row}, table now has {self.parent.vxlan_table.rowCount()} rows")

    def _cleanup_vxlan_table_for_device(self, device_id, device_name):
        """Clean up VXLAN table entries for a removed device."""
        try:
            print(f"[DEBUG VXLAN CLEANUP] Cleaning up VXLAN entries for device '{device_name}' (ID: {device_id})")
            
            # Remove VXLAN table rows that match this device
            rows_to_remove = []
            device_col_idx = self.parent.VXLAN_COL.get("Device", 0)
            max_col = self.parent.vxlan_table.columnCount()
            if device_col_idx < 0 or device_col_idx >= max_col:
                logging.warning(f"[VXLAN CLEANUP] Invalid Device column index {device_col_idx} (max: {max_col-1})")
                return
            
            for row in range(self.parent.vxlan_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.parent.vxlan_table.item(row, device_col_idx)
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    print(f"[DEBUG VXLAN CLEANUP] Found VXLAN row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.parent.vxlan_table.removeRow(row)
                print(f"[DEBUG VXLAN CLEANUP] Removed VXLAN table row {row}")
            
            print(f"[DEBUG VXLAN CLEANUP] Successfully cleaned up {len(rows_to_remove)} VXLAN table row(s) for device '{device_name}'")
            
        except Exception as exc:
            logging.warning(f"[VXLAN CLEANUP] Failed to clean up VXLAN table for {device_name}: {exc}")
            print(f"[DEBUG VXLAN CLEANUP] Error cleaning up VXLAN table: {exc}")
    
    def delete_selected_vxlan_tunnels(self):
        """Delete selected VXLAN tunnel(s) from both client and server."""
        selected_rows = self.parent.vxlan_table.selectedItems()
        if not selected_rows:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self.parent, "No Selection", "Please select one or more VXLAN tunnels to delete.")
            return
        
        # Get unique rows (items can be in same row)
        unique_rows = set()
        tunnels_to_delete = []
        
        for item in selected_rows:
            row = item.row()
            if row in unique_rows:
                continue
            unique_rows.add(row)
            
            # Get metadata from the item
            metadata = item.data(Qt.UserRole)
            if not metadata or not isinstance(metadata, dict):
                logging.warning(f"[VXLAN DELETE] No metadata found for row {row}")
                continue
            
            device_id = metadata.get("device_id")
            device_name = metadata.get("device_name")
            vni = metadata.get("vni")
            vxlan_cfg = metadata.get("vxlan_cfg", {})
            
            if not device_id or not device_name or not vni:
                logging.warning(f"[VXLAN DELETE] Missing required data for row {row}: device_id={device_id}, device_name={device_name}, vni={vni}")
                continue
            
            tunnels_to_delete.append({
                "row": row,
                "device_id": device_id,
                "device_name": device_name,
                "vni": vni,
                "vxlan_cfg": vxlan_cfg
            })
        
        if not tunnels_to_delete:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self.parent, "Invalid Selection", "Could not extract tunnel information from selected rows.")
            return
        
        # Confirm deletion
        from PyQt5.QtWidgets import QMessageBox
        tunnel_list = "\n".join([f"  - {t['device_name']} (VNI: {t['vni']})" for t in tunnels_to_delete])
        reply = QMessageBox.question(
            self.parent,
            "Confirm Deletion",
            f"Are you sure you want to delete the following VXLAN tunnel(s)?\n\n{tunnel_list}\n\nThis will remove the tunnel from both the client and server.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Delete tunnels
        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self.parent, "No Server", "Please select a server before deleting VXLAN tunnels.")
            return
        
        deleted_count = 0
        failed_count = 0
        rows_to_remove = []  # Collect rows to remove after all deletions
        
        for tunnel_info in tunnels_to_delete:
            try:
                device_id = tunnel_info["device_id"]
                device_name = tunnel_info["device_name"]
                vni = tunnel_info["vni"]
                vxlan_cfg = tunnel_info["vxlan_cfg"]
                
                print(f"[VXLAN DELETE] Deleting tunnel: device={device_name}, VNI={vni}")
                
                # Call server API to remove the tunnel
                response = requests.post(
                    f"{server_url}/api/device/vxlan/remove",
                    json={
                        "device_id": device_id,
                        "device_name": device_name,
                        "vni": vni,
                        "vxlan_config": vxlan_cfg
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("success"):
                        print(f"[VXLAN DELETE] Successfully deleted tunnel VNI {vni} for device {device_name}")
                        deleted_count += 1
                        
                        # Remove tunnel from local device data
                        self._remove_tunnel_from_local_device(device_id, device_name, vni)
                        
                        # Collect row for removal (remove in reverse order later)
                        rows_to_remove.append(tunnel_info["row"])
                    else:
                        error_msg = result.get("error", "Unknown error")
                        print(f"[VXLAN DELETE] Failed to delete tunnel VNI {vni}: {error_msg}")
                        failed_count += 1
                else:
                    error_msg = response.text or f"HTTP {response.status_code}"
                    print(f"[VXLAN DELETE] Server error deleting tunnel VNI {vni}: {error_msg}")
                    failed_count += 1
                    
            except Exception as e:
                logging.error(f"[VXLAN DELETE] Exception deleting tunnel: {e}")
                failed_count += 1
        
        # Remove rows in reverse order to avoid index shifting
        for row in sorted(rows_to_remove, reverse=True):
            self.parent.vxlan_table.removeRow(row)
        
        # Show result
        from PyQt5.QtWidgets import QMessageBox
        if deleted_count > 0 and failed_count == 0:
            QMessageBox.information(self.parent, "Success", f"Successfully deleted {deleted_count} VXLAN tunnel(s).")
        elif deleted_count > 0:
            QMessageBox.warning(self.parent, "Partial Success", f"Deleted {deleted_count} tunnel(s), but {failed_count} failed.")
        else:
            QMessageBox.critical(self.parent, "Error", f"Failed to delete {failed_count} tunnel(s).")
        
        # Refresh VXLAN table and interface list
        if deleted_count > 0:
            QTimer.singleShot(100, self.refresh_vxlan_table)
            # Refresh interface list from server to remove deleted VXLAN/bridge interfaces
            # Add a small delay to ensure VXLAN interfaces are fully removed from containers
            if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "update_server_tree"):
                print(f"[VXLAN DELETE] Scheduling interface list refresh from server after VXLAN tunnel deletion (delay: 500ms)")
                # Define a callback function to ensure it's called correctly
                def refresh_interfaces():
                    print(f"[VXLAN DELETE] Executing interface list refresh callback")
                    try:
                        if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "update_server_tree"):
                            # Clear cached interfaces for all servers to force fresh fetch
                            # This ensures deleted VXLAN/bridge interfaces are removed from the tree
                            if hasattr(self.parent.main_window, "server_interfaces"):
                                for server in self.parent.main_window.server_interfaces:
                                    if "interfaces" in server:
                                        del server["interfaces"]
                                        print(f"[VXLAN DELETE] Cleared cached interfaces for server: {server.get('address', 'unknown')}")
                            
                            # Now refresh the server tree with fresh data
                            self.parent.main_window.update_server_tree()
                            print(f"[VXLAN DELETE] Interface list refresh completed")
                        else:
                            print(f"[VXLAN DELETE] WARNING: main_window or update_server_tree not available in callback")
                    except Exception as e:
                        print(f"[VXLAN DELETE] ERROR during interface refresh: {e}")
                        logging.error(f"[VXLAN DELETE] ERROR during interface refresh: {e}", exc_info=True)
                QTimer.singleShot(500, refresh_interfaces)
            else:
                print(f"[VXLAN DELETE] WARNING: Cannot refresh interface list - main_window or update_server_tree not available")
    
    def _remove_tunnel_from_local_device(self, device_id, device_name, vni):
        """Remove a tunnel from local device data."""
        try:
            if not hasattr(self.parent, "main_window") or not hasattr(self.parent.main_window, "all_devices"):
                return
            
            # Find the device in local data
            for iface, device_list in self.parent.main_window.all_devices.items():
                for device in device_list:
                    if device.get("device_id") == device_id or device.get("Device Name") == device_name:
                        vxlan_config = device.get("vxlan_config", {})
                        
                        # Parse if string
                        if isinstance(vxlan_config, str):
                            try:
                                vxlan_config = json.loads(vxlan_config) if vxlan_config else {}
                            except Exception:
                                vxlan_config = {}
                        
                        # Handle multiple tunnels format
                        if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
                            tunnels = vxlan_config.get("tunnels", [])
                            # Remove tunnel with matching VNI
                            vxlan_config["tunnels"] = [t for t in tunnels if isinstance(t, dict) and t.get("vni") != vni]
                            
                            # If no tunnels left, remove VXLAN config entirely
                            if not vxlan_config["tunnels"]:
                                device.pop("vxlan_config", None)
                                # Also remove VXLAN from protocols if present
                                protocols = device.get("protocols", [])
                                if isinstance(protocols, list):
                                    if "VXLAN" in protocols:
                                        protocols.remove("VXLAN")
                                elif isinstance(protocols, str):
                                    protocols = [p.strip() for p in protocols.split(",") if p.strip()]
                                    if "VXLAN" in protocols:
                                        protocols.remove("VXLAN")
                                    device["protocols"] = ",".join(protocols)
                            else:
                                device["vxlan_config"] = vxlan_config
                        elif isinstance(vxlan_config, dict) and vxlan_config.get("vni") == vni:
                            # Old single tunnel format - remove entire config
                            device.pop("vxlan_config", None)
                            # Remove VXLAN from protocols
                            protocols = device.get("protocols", [])
                            if isinstance(protocols, list):
                                if "VXLAN" in protocols:
                                    protocols.remove("VXLAN")
                            elif isinstance(protocols, str):
                                protocols = [p.strip() for p in protocols.split(",") if p.strip()]
                                if "VXLAN" in protocols:
                                    protocols.remove("VXLAN")
                                device["protocols"] = ",".join(protocols)
                        
                        print(f"[VXLAN DELETE] Removed tunnel VNI {vni} from local device {device_name}")
                        return
        except Exception as e:
            logging.error(f"[VXLAN DELETE] Error removing tunnel from local device: {e}")

