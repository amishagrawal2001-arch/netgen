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
        print("[VXLAN TAB] Starting refresh_vxlan_table")
        logging.debug("[VXLAN TAB] Starting refresh_vxlan_table")
        server_url = self.parent.get_server_url(silent=True)
        
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
            except Exception as exc:
                logging.warning("[VXLAN TAB] Error fetching VXLAN data from database: %s", exc)

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
        
        # Add local devices that aren't in database yet
        for device in devices_from_local:
            device_id = device.get("device_id")
            device_name = device.get("device_name")
            key = device_id or device_name
            if key and key not in device_map:
                device_map[key] = device
                print(f"[VXLAN TAB] Added local-only device to map: {device_name}")
            elif key in device_map:
                # Merge: Combine tunnels from both DB and local (support multiple tunnels)
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
                
                # Merge tunnels: prefer local tunnels, but keep DB tunnels that don't exist locally
                merged_tunnels = []
                local_vnis = {t.get("vni") for t in local_tunnels if isinstance(t, dict) and t.get("vni")}
                
                # Add all local tunnels first (they take precedence)
                merged_tunnels.extend(local_tunnels)
                
                # Add DB tunnels that aren't in local (by VNI)
                for db_tunnel in db_tunnels:
                    if isinstance(db_tunnel, dict) and db_tunnel.get("vni") not in local_vnis:
                        merged_tunnels.append(db_tunnel)
                
                if merged_tunnels:
                    print(f"[VXLAN TAB] Merging: Combined {len(local_tunnels)} local + {len(db_tunnels)} DB = {len(merged_tunnels)} total tunnels for {device_name}")
                    db_device["vxlan_config"] = {"tunnels": merged_tunnels}
                    if not db_device.get("vxlan_state") or db_device.get("vxlan_state") == "Disabled":
                        db_device["vxlan_state"] = "Pending"
                elif local_tunnels:
                    # Only local tunnels
                    print(f"[VXLAN TAB] Merging: Using {len(local_tunnels)} local tunnel(s) for {device_name}")
                    db_device["vxlan_config"] = {"tunnels": local_tunnels}
                    if not db_device.get("vxlan_state") or db_device.get("vxlan_state") == "Disabled":
                        db_device["vxlan_state"] = "Pending"
                elif db_tunnels:
                    # Only DB tunnels
                    print(f"[VXLAN TAB] Merging: Using {len(db_tunnels)} DB tunnel(s) for {device_name}")
                    db_device["vxlan_config"] = {"tunnels": db_tunnels}

        devices = list(device_map.values())
        print(f"[VXLAN TAB] Total devices after merge: {len(devices)} (DB: {len(devices_from_db)}, Local: {len(devices_from_local)})")
        logging.debug(f"[VXLAN TAB] Total devices after merge: {len(devices)} (DB: {len(devices_from_db)}, Local: {len(devices_from_local)})")

        rows = []
        for device in devices:
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
                    # Check if it has actual VXLAN settings (not just metadata)
                    if any(k in vxlan_cfg for k in ['vni', 'local_ip', 'remote_peers', 'bridge_svi_ip']):
                        tunnels = [vxlan_cfg]
                        print(f"[VXLAN TAB] Device {device.get('device_name')}: Found 1 tunnel in old format (single dict)")
            
            # If no tunnels found, check other indicators
            if not tunnels:
                cfg_enabled = bool(device.get("vxlan_enabled"))
                has_status = bool(device.get("vxlan_state") or device.get("vxlan_interface"))
                if not (cfg_enabled or has_status):
                    print(f"[VXLAN TAB] Skipping device {device.get('device_name')} - no VXLAN tunnels/config/status")
                    continue
            
            # Create one row per tunnel
            for tunnel_idx, tunnel_cfg in enumerate(tunnels):
                if isinstance(tunnel_cfg, dict) and tunnel_cfg:
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
        print(f"[VXLAN TAB] Table geometry: {self.parent.vxlan_table.geometry()}")
        print(f"[VXLAN TAB] Table size: {self.parent.vxlan_table.size()}")
        if final_row_count > 0:
            # Check first row has data
            device_col = self.parent.VXLAN_COL.get("Device")
            status_col = self.parent.VXLAN_COL.get("Status")
            first_row_device = self.parent.vxlan_table.item(0, device_col) if device_col is not None else None
            first_row_status = self.parent.vxlan_table.item(0, status_col) if status_col is not None else None
            print(f"[VXLAN TAB] First row device item: {first_row_device.text() if first_row_device else 'None'}")
            print(f"[VXLAN TAB] First row status item: {first_row_status is not None}")
            # Check all columns in first row
            for col_name, col_idx in self.parent.VXLAN_COL.items():
                item = self.parent.vxlan_table.item(0, col_idx)
                print(f"[VXLAN TAB] First row, col '{col_name}' (idx {col_idx}): {item.text() if item else 'None'}")
        logging.debug(f"[VXLAN TAB] Refresh complete, table now has {final_row_count} rows")

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
            print(f"[VXLAN TAB] _append_row: Setting status '{status_text}' at row {row}, col {status_col_idx}")
            self.parent.vxlan_table.setItem(row, status_col_idx, status_item)

        def _set(col, value):
            col_idx = self.parent.VXLAN_COL.get(col)
            if col_idx is None:
                print(f"[VXLAN TAB] _append_row: WARNING - Column '{col}' not found in VXLAN_COL")
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
            device_interface = device.get("vxlan_interface", "")
            # If it's a comma-separated list, try to extract the relevant one based on VNI
            if "," in device_interface and isinstance(vxlan_cfg, dict):
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
        print(f"[VXLAN TAB] _append_row: Completed row {row}, table now has {self.parent.vxlan_table.rowCount()} rows")

    def _cleanup_vxlan_table_for_device(self, device_id, device_name):
        """Clean up VXLAN table entries for a removed device."""
        try:
            print(f"[DEBUG VXLAN CLEANUP] Cleaning up VXLAN entries for device '{device_name}' (ID: {device_id})")
            
            # Remove VXLAN table rows that match this device
            rows_to_remove = []
            for row in range(self.parent.vxlan_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.parent.vxlan_table.item(row, self.parent.VXLAN_COL.get("Device", 0))
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

