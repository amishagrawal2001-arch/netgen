"""OSPF-related functionality for DevicesTab.

This module contains all OSPF-specific methods extracted from devices_tab.py
to improve code organization and maintainability.
"""

from PyQt5.QtWidgets import (
    QTableWidgetItem, QMessageBox, QDialog, QTableWidget, 
    QPushButton, QVBoxLayout, QHBoxLayout, QLabel
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon
import requests
import logging


class OSPFHandler:
    """Handler class for OSPF-related functionality in DevicesTab."""
    
    def __init__(self, parent_tab):
        """Initialize OSPF handler with reference to parent DevicesTab.
        
        Args:
            parent_tab: The DevicesTab instance that owns this handler.
        """
        self.parent = parent_tab
    
    def setup_ospf_subtab(self):
        """Setup the OSPF sub-tab with OSPF-specific functionality."""
        from utils.qicon_loader import qicon
        
        layout = QVBoxLayout(self.parent.ospf_subtab)
        
        # OSPF Neighbors Table
        ospf_headers = ["Device", "OSPF Status", "Area ID", "Neighbor Type", "Interface", "Neighbor ID", "State", "Priority", "Dead Timer", "Uptime", "Graceful Restart"]
        self.parent.ospf_table = QTableWidget(0, len(ospf_headers))
        self.parent.ospf_table.setHorizontalHeaderLabels(ospf_headers)
        
        # Enable inline editing for the OSPF table
        self.parent.ospf_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.parent.ospf_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect cell changed signal for inline editing
        self.parent.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
        
        layout.addWidget(QLabel("OSPF Neighbors"))
        layout.addWidget(self.parent.ospf_table)
        
        # OSPF Controls
        ospf_controls = QHBoxLayout()
        
        # Add OSPF button
        def load_icon(filename: str) -> QIcon:
            return qicon("resources", f"icons/{filename}")
        
        self.parent.add_ospf_button = QPushButton()
        self.parent.add_ospf_button.setIcon(load_icon("add.png"))
        self.parent.add_ospf_button.setIconSize(QSize(16, 16))
        self.parent.add_ospf_button.setFixedSize(32, 28)
        self.parent.add_ospf_button.setToolTip("Add OSPF")
        self.parent.add_ospf_button.clicked.connect(self.prompt_add_ospf)
        
        self.parent.edit_ospf_button = QPushButton()
        self.parent.edit_ospf_button.setIcon(load_icon("edit.png"))
        self.parent.edit_ospf_button.setIconSize(QSize(16, 16))
        self.parent.edit_ospf_button.setFixedSize(32, 28)
        self.parent.edit_ospf_button.setToolTip("Edit OSPF Configuration")
        self.parent.edit_ospf_button.clicked.connect(self.prompt_edit_ospf)
        
        self.parent.delete_ospf_button = QPushButton()
        self.parent.delete_ospf_button.setIcon(load_icon("remove.png"))
        self.parent.delete_ospf_button.setIconSize(QSize(16, 16))
        self.parent.delete_ospf_button.setFixedSize(32, 28)
        self.parent.delete_ospf_button.setToolTip("Delete OSPF Configuration")
        self.parent.delete_ospf_button.clicked.connect(self.prompt_delete_ospf)
        
        self.parent.ospf_refresh_button = QPushButton()
        self.parent.ospf_refresh_button.setIcon(load_icon("refresh.png"))
        self.parent.ospf_refresh_button.setIconSize(QSize(16, 16))
        self.parent.ospf_refresh_button.setFixedSize(32, 28)
        self.parent.ospf_refresh_button.setToolTip("Refresh OSPF Status")
        self.parent.ospf_refresh_button.clicked.connect(self.refresh_ospf_status)
        
        # OSPF Start/Stop buttons
        self.parent.ospf_start_button = QPushButton()
        self.parent.ospf_start_button.setIcon(load_icon("start.png"))
        self.parent.ospf_start_button.setIconSize(QSize(16, 16))
        self.parent.ospf_start_button.setFixedSize(32, 28)
        self.parent.ospf_start_button.setToolTip("Start OSPF")
        self.parent.ospf_start_button.clicked.connect(self.start_ospf_protocol)
        
        self.parent.ospf_stop_button = QPushButton()
        self.parent.ospf_stop_button.setIcon(load_icon("stop.png"))
        self.parent.ospf_stop_button.setIconSize(QSize(16, 16))
        self.parent.ospf_stop_button.setFixedSize(32, 28)
        self.parent.ospf_stop_button.setToolTip("Stop OSPF")
        self.parent.ospf_stop_button.clicked.connect(self.stop_ospf_protocol)
        
        self.parent.apply_ospf_button = QPushButton()
        self.parent.apply_ospf_button.setIcon(load_icon("apply.png"))
        self.parent.apply_ospf_button.setIconSize(QSize(16, 16))
        self.parent.apply_ospf_button.setFixedSize(32, 28)
        self.parent.apply_ospf_button.setToolTip("Apply OSPF Configuration to FRR")
        self.parent.apply_ospf_button.clicked.connect(self.apply_ospf_configurations)
        
        ospf_controls.addWidget(self.parent.add_ospf_button)
        ospf_controls.addWidget(self.parent.edit_ospf_button)
        ospf_controls.addWidget(self.parent.delete_ospf_button)
        ospf_controls.addWidget(self.parent.apply_ospf_button)
        ospf_controls.addWidget(self.parent.ospf_start_button)
        ospf_controls.addWidget(self.parent.ospf_stop_button)
        ospf_controls.addWidget(self.parent.ospf_refresh_button)
        ospf_controls.addStretch()
        layout.addLayout(ospf_controls)
    
    def refresh_ospf_status(self):
        """Refresh OSPF neighbor status from server."""
        try:
            print("[OSPF REFRESH] Refreshing OSPF status from database...")
            # Update the OSPF table which fetches status from database
            self.update_ospf_table()
            print("[OSPF REFRESH] OSPF status refreshed successfully")
        except Exception as e:
            print(f"[OSPF REFRESH ERROR] Error refreshing OSPF status: {e}")
    
    def update_ospf_table(self):
        """Update OSPF table with data from devices."""
        # Auto-start OSPF monitoring if we have OSPF devices and monitoring is not active
        if not self.parent.ospf_monitoring_active:
            has_ospf_devices = False
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    device_protocols = device.get("protocols", [])
                    if isinstance(device_protocols, str):
                        try:
                            import json
                            device_protocols = json.loads(device_protocols)
                        except:
                            device_protocols = []
                    
                    if "OSPF" in device_protocols:
                        has_ospf_devices = True
                        break
                if has_ospf_devices:
                    break
            
            if has_ospf_devices:
                print("[OSPF AUTO-START] Auto-starting OSPF monitoring for existing OSPF devices")
                self.start_ospf_monitoring()
            
            # Auto-start ISIS monitoring if ISIS devices exist
            isis_devices_exist = any(
                device.get("protocols") and "IS-IS" in device.get("protocols", {})
                for devices in self.parent.main_window.all_devices.values()
                for device in devices
            )
            if isis_devices_exist:
                print("[ISIS AUTO-START] Auto-starting ISIS monitoring for existing ISIS devices")
                # Note: This would need to be handled by ISIS handler
                if hasattr(self.parent, 'start_isis_monitoring'):
                    self.parent.start_isis_monitoring()
        
        try:
            # Get selected interfaces from server_tree (same logic as device table)
            selected_interfaces = set()
            if hasattr(self.parent.main_window, 'server_tree') and self.parent.main_window.server_tree:
                tree = self.parent.main_window.server_tree
                for item in tree.selectedItems():
                    parent = item.parent()
                    if parent:
                        tg_id = parent.text(0).strip()
                        port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                        selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
            
            self.parent.ospf_table.setRowCount(0)
            
            # Use same filtering logic as device table - show only selected interfaces
            interfaces_to_show = selected_interfaces if selected_interfaces else list(self.parent.main_window.all_devices.keys())
            for iface in interfaces_to_show:
                # Check both new format and old format for backward compatibility
                devices = self.parent.main_window.all_devices.get(iface, [])
                if not devices:
                    # Try old format with "Port:" and bullet
                    old_format = iface.replace(" - ", " - Port: • ")
                    devices = self.parent.main_window.all_devices.get(old_format, [])
                if not devices:
                    continue
                    
                for device in devices:
                    # Check for OSPF in protocols list (new format) or ospf_config field
                    has_ospf = False
                    ospf_config = {}
                    
                    # Check new format: protocols is a list like ["BGP", "OSPF"]
                    if "protocols" in device and isinstance(device["protocols"], list) and "OSPF" in device["protocols"]:
                        has_ospf = True
                        ospf_config = device.get("ospf_config", {})
                    # Check old format: protocols is a dict like {"OSPF": {...}}
                    elif "protocols" in device and isinstance(device["protocols"], dict) and "OSPF" in device["protocols"]:
                        has_ospf = True
                        ospf_config = device["protocols"]["OSPF"]
                    
                    if has_ospf:
                        device_name = device.get("Device Name", "")
                        device_id = device.get("device_id", "")
                        
                        # Get the actual OSPF interface name from ospf_config
                        ospf_interface = "Unknown"
                        if ospf_config and "interface" in ospf_config:
                            ospf_interface = ospf_config["interface"]
                            
                            # If VLAN is 0, use the physical interface name instead of vlan0
                            vlan_id = device.get("VLAN", "0")
                            if vlan_id == "0" and ospf_interface.startswith("vlan"):
                                # Extract physical interface from the iface string (e.g., "TG 1 - ens4np0" -> "ens4np0")
                                iface_parts = iface.split(" - ")
                                if len(iface_parts) >= 2:
                                    ospf_interface = iface_parts[1]  # Get the physical interface name
                        
                        # Get Area ID from OSPF config
                        # Support separate area IDs for IPv4 and IPv6, with backward compatibility
                        area_id = ospf_config.get("area_id", "0.0.0.0") if ospf_config else "0.0.0.0"
                        
                        # Get OSPF configuration flags
                        ipv4_enabled = ospf_config.get("ipv4_enabled", False) if ospf_config else False
                        ipv6_enabled = ospf_config.get("ipv6_enabled", False) if ospf_config else False
                        
                        # Try to get actual OSPF status from database via server
                        ospf_status_data = {}
                        ospf_data = {}  # Initialize ospf_data to avoid NameError
                        try:
                            server_url = self.parent.get_server_url(silent=True)
                            if server_url and device_id:
                                response = requests.get(f"{server_url}/api/ospf/status/database/{device_id}", timeout=5)
                                if response.status_code == 200:
                                    data = response.json()
                                    ospf_data = data.get("ospf_status", {})
                                    if ospf_data:
                                        neighbors = ospf_data.get("neighbors", [])
                                        # Group neighbors by type (IPv4/IPv6)
                                        for neighbor in neighbors:
                                            neighbor_type = neighbor.get("type", "Unknown")
                                            if neighbor_type not in ospf_status_data:
                                                ospf_status_data[neighbor_type] = []
                                            ospf_status_data[neighbor_type].append(neighbor)
                        except Exception:
                            # Don't print debug errors to reduce spam
                            pass
                        
                        # Create separate rows for IPv4 and IPv6 OSPF if enabled
                        protocols_to_show = []
                        if ipv4_enabled:
                            protocols_to_show.append("IPv4")
                        if ipv6_enabled:
                            protocols_to_show.append("IPv6")
                        
                        # If no protocols are explicitly enabled, show as Unknown
                        if not protocols_to_show:
                            protocols_to_show = ["Unknown"]
                        
                        for protocol_type in protocols_to_show:
                            # Get status for this protocol type
                            neighbors_for_type = ospf_status_data.get(protocol_type, [])
                            
                            # Determine OSPF status based on neighbors or overall OSPF status
                            if neighbors_for_type:
                                # Use the first neighbor's information for this type
                                neighbor = neighbors_for_type[0]
                                neighbor_id = neighbor.get("neighbor_id", "N/A")
                                state = neighbor.get("state", "Down")
                                priority = neighbor.get("priority", "1")
                                dead_timer = neighbor.get("dead_time", "40")
                                ospf_status = "Up" if any(state.startswith(s) for s in ["Full", "2-Way"]) else "Down"
                            else:
                                # No neighbors found for this type - check if OSPF is running
                                neighbor_id = "N/A"
                                state = "No Neighbors"
                                priority = "N/A"
                                dead_timer = "N/A"
                                
                                # Check if OSPF is running for this protocol type
                                if protocol_type == "IPv4" and ospf_data.get("ospf_ipv4_running", False):
                                    ospf_status = "Running"
                                elif protocol_type == "IPv6" and ospf_data.get("ospf_ipv6_running", False):
                                    ospf_status = "Running"
                                else:
                                    ospf_status = "Down"
                            
                            # Get uptime for this protocol type
                            uptime = "N/A"
                            if neighbors_for_type:
                                # Use neighbor uptime if available
                                neighbor = neighbors_for_type[0]
                                uptime = neighbor.get("up_time", "N/A")
                            else:
                                # Fall back to process uptime if no neighbors
                                if protocol_type == "IPv4" and ospf_data.get("ospf_ipv4_uptime"):
                                    uptime = ospf_data.get("ospf_ipv4_uptime")
                                elif protocol_type == "IPv6" and ospf_data.get("ospf_ipv6_uptime"):
                                    uptime = ospf_data.get("ospf_ipv6_uptime")
                            
                            row = self.parent.ospf_table.rowCount()
                            self.parent.ospf_table.insertRow(row)
                            
                            # Get the area ID for this specific address family
                            # Support separate area IDs for IPv4 and IPv6, with backward compatibility
                            if protocol_type == "IPv6":
                                display_area_id = ospf_config.get("area_id_ipv6") or area_id
                            else:
                                display_area_id = ospf_config.get("area_id_ipv4") or area_id
                            
                            # Get graceful restart status for this specific address family
                            # Support separate graceful restart for IPv4 and IPv6, with backward compatibility
                            if protocol_type == "IPv6":
                                graceful_restart = ospf_config.get("graceful_restart_ipv6") or ospf_config.get("graceful_restart", False) if ospf_config else False
                            else:
                                graceful_restart = ospf_config.get("graceful_restart_ipv4") or ospf_config.get("graceful_restart", False) if ospf_config else False
                            graceful_restart_text = "Yes" if graceful_restart else "No"
                            
                            self.parent.ospf_table.setItem(row, 0, QTableWidgetItem(device_name))  # Device
                            # Set OSPF status icon instead of text
                            self.set_ospf_status_icon(row, ospf_status, f"OSPF {ospf_status}")
                            self.parent.ospf_table.setItem(row, 2, QTableWidgetItem(display_area_id))      # Area ID
                            self.parent.ospf_table.setItem(row, 3, QTableWidgetItem(protocol_type)) # Neighbor Type
                            self.parent.ospf_table.setItem(row, 4, QTableWidgetItem(ospf_interface)) # Interface
                            self.parent.ospf_table.setItem(row, 5, QTableWidgetItem(neighbor_id))   # Neighbor ID
                            self.parent.ospf_table.setItem(row, 6, QTableWidgetItem(state))         # State
                            self.parent.ospf_table.setItem(row, 7, QTableWidgetItem(priority))     # Priority
                            self.parent.ospf_table.setItem(row, 8, QTableWidgetItem(dead_timer))   # Dead Timer
                            self.parent.ospf_table.setItem(row, 9, QTableWidgetItem(uptime))        # Uptime
                            self.parent.ospf_table.setItem(row, 10, QTableWidgetItem(graceful_restart_text))  # Graceful Restart
        except Exception as e:
            print(f"Error updating OSPF table: {e}")
    
    def _safe_update_ospf_table(self):
        """Safely update OSPF table (for parallel execution)."""
        try:
            print("[PROTOCOL REFRESH] Refreshing OSPF table...")
            self.update_ospf_table()
        except Exception as e:
            logging.error(f"[OSPF REFRESH ERROR] {e}")
    
    def set_ospf_status_icon(self, row: int, ospf_status: str, status_text: str = None):
        """Set OSPF status icon in the OSPF Status column based on OSPF status."""
        col = 1  # OSPF Status column (0-indexed)
        
        # Create item with icon only, no text
        item = QTableWidgetItem("")  # Empty text, icon only
        
        # Determine icon based on OSPF status
        if ospf_status == "Up":
            icon = self.parent.green_dot
            tooltip = status_text or "OSPF Up"
        elif ospf_status == "Running":
            icon = self.parent.orange_dot
            tooltip = status_text or "OSPF Running (No Neighbors)"
        elif ospf_status == "Down":
            icon = self.parent.red_dot
            tooltip = status_text or "OSPF Down"
        else:
            icon = self.parent.red_dot
            tooltip = status_text or f"OSPF Status: {ospf_status}"
        
        item.setIcon(icon)
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # Make OSPF Status column non-editable
        self.parent.ospf_table.setItem(row, col, item)
    
    def _apply_ospf_to_server_sync(self, server_url, device_info):
        """Apply OSPF configuration synchronously (for use in background workers)."""
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            
            # Get OSPF config - handle both old dict format and new separate config format
            ospf_config = device_info.get("ospf_config", {})
            if not ospf_config:
                # Try old format for backward compatibility
                protocols = device_info.get("protocols", {})
                if isinstance(protocols, dict):
                    ospf_config = protocols.get("OSPF", {})
            
            if not ospf_config:
                return True  # No OSPF config to apply
            
            # Prepare OSPF payload using the configure endpoint
            ospf_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "ospf_config": ospf_config,
                "route_pools_per_area": {},  # No route pools attached initially
                "all_route_pools": []  # Include all route pools for generation
            }
            
            # Make synchronous request to the configure endpoint
            response = requests.post(f"{server_url}/api/device/ospf/configure", json=ospf_payload, timeout=30)
            return response.status_code == 200
                
        except Exception as e:
            print(f"[ERROR] Exception in sync OSPF apply for '{device_name}': {e}")
            return False
    
    def prompt_add_ospf(self):
        """Add OSPF configuration to selected device."""
        selected_items = self.parent.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select a device to add OSPF configuration.")
            return

        row = selected_items[0].row()
        device_name = self.parent.devices_table.item(row, self.parent.COL["Device Name"]).text()
        
        from widgets.add_ospf_dialog import AddOspfDialog
        dialog = AddOspfDialog(self.parent, device_name)
        if dialog.exec_() != dialog.Accepted:
            return

        ospf_config = dialog.get_values()
        
        # Update the device with OSPF configuration
        self.parent._update_device_protocol(row, "OSPF", ospf_config)
    
    def prompt_edit_ospf(self):
        """Edit OSPF configuration for selected device."""
        selected_items = self.parent.ospf_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select an OSPF configuration to edit.")
            return

        row = selected_items[0].row()
        device_name = self.parent.ospf_table.item(row, 0).text()  # Device column
        protocol_type_item = self.parent.ospf_table.item(row, 3)  # Neighbor Type column (IPv4 or IPv6)
        protocol_type = protocol_type_item.text() if protocol_type_item else "Unknown"
        
        # Verify that we have a valid protocol type
        if protocol_type not in ["IPv4", "IPv6"]:
            QMessageBox.warning(self.parent, "Invalid Selection", 
                              f"Could not determine address family for selected row. Protocol type: {protocol_type}")
            return
        
        # Find the device in all_devices
        device_info = None
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        if not device_info:
            QMessageBox.warning(self.parent, "Device Not Found", f"Device '{device_name}' not found.")
            return
        
        # Get current OSPF configuration
        # protocols is a list (e.g., ["OSPF", "BGP", "ISIS"]), not a dict
        # OSPF config is stored separately in ospf_config
        current_ospf_config = device_info.get("ospf_config", {})
        
        # Determine which address family we're editing
        is_ipv6 = protocol_type == "IPv6"
        
        # Get the current area ID for the selected address family
        # Support both old format (single area_id) and new format (separate area_id_ipv4/area_id_ipv6)
        if is_ipv6:
            current_area_id = current_ospf_config.get("area_id_ipv6") or current_ospf_config.get("area_id", "0.0.0.0")
        else:
            current_area_id = current_ospf_config.get("area_id_ipv4") or current_ospf_config.get("area_id", "0.0.0.0")
        
        # Create a temporary config for the dialog with the current area ID and graceful restart for this address family
        dialog_config = current_ospf_config.copy()
        dialog_config["area_id"] = current_area_id
        
        # Get the current graceful restart for the selected address family
        # Support separate graceful restart for IPv4 and IPv6, with backward compatibility
        if is_ipv6:
            current_graceful_restart = current_ospf_config.get("graceful_restart_ipv6") or current_ospf_config.get("graceful_restart", False)
        else:
            current_graceful_restart = current_ospf_config.get("graceful_restart_ipv4") or current_ospf_config.get("graceful_restart", False)
        
        dialog_config["graceful_restart"] = current_graceful_restart
        
        # Create and show OSPF dialog
        from widgets.add_ospf_dialog import AddOspfDialog
        dialog = AddOspfDialog(self.parent, device_name, dialog_config)
        
        if dialog.exec_() == QDialog.Accepted:
            ospf_config = dialog.get_values()
            new_area_id = ospf_config.get("area_id", "0.0.0.0")
            new_graceful_restart = ospf_config.get("graceful_restart", False)
            
            # Preserve existing OSPF config fields that are not in the dialog
            # (ipv4_enabled, ipv6_enabled, interface, etc.)
            if current_ospf_config:
                ospf_config.setdefault("ipv4_enabled", current_ospf_config.get("ipv4_enabled", False))
                ospf_config.setdefault("ipv6_enabled", current_ospf_config.get("ipv6_enabled", False))
                ospf_config.setdefault("interface", current_ospf_config.get("interface", ""))
                
                # Update only the area ID for the selected address family
                # First, preserve or initialize both area IDs from existing config
                # Get the base area_id from config (for backward compatibility)
                base_area_id = current_ospf_config.get("area_id", "0.0.0.0")
                
                # Preserve or initialize IPv4 area ID
                if "area_id_ipv4" in current_ospf_config:
                    ospf_config["area_id_ipv4"] = current_ospf_config["area_id_ipv4"]
                else:
                    # If IPv4 area ID doesn't exist, initialize it from base area_id
                    ospf_config["area_id_ipv4"] = base_area_id
                
                # Preserve or initialize IPv6 area ID
                if "area_id_ipv6" in current_ospf_config:
                    ospf_config["area_id_ipv6"] = current_ospf_config["area_id_ipv6"]
                else:
                    # If IPv6 area ID doesn't exist, initialize it from base area_id
                    ospf_config["area_id_ipv6"] = base_area_id
                
                # Now update only the area ID for the selected address family
                if is_ipv6:
                    ospf_config["area_id_ipv6"] = new_area_id
                    # Keep area_id_ipv4 unchanged (already set above)
                else:
                    ospf_config["area_id_ipv4"] = new_area_id
                    # Keep area_id_ipv6 unchanged (already set above)
                
                # Update the old area_id field only if both address families use the same area
                # Otherwise, keep it for backward compatibility with the last updated one
                if ospf_config.get("area_id_ipv4") == ospf_config.get("area_id_ipv6"):
                    ospf_config["area_id"] = ospf_config["area_id_ipv4"]
                else:
                    # If they differ, keep area_id as the one that was just updated
                    ospf_config["area_id"] = new_area_id
                
                # Update only the graceful restart for the selected address family
                # Preserve or initialize both graceful restart flags from existing config
                # Get the base graceful_restart from config (for backward compatibility)
                base_graceful_restart = current_ospf_config.get("graceful_restart", False)
                
                # Preserve or initialize IPv4 graceful restart
                if "graceful_restart_ipv4" in current_ospf_config:
                    ospf_config["graceful_restart_ipv4"] = current_ospf_config["graceful_restart_ipv4"]
                else:
                    # If IPv4 graceful restart doesn't exist, initialize it from base graceful_restart
                    ospf_config["graceful_restart_ipv4"] = base_graceful_restart
                
                # Preserve or initialize IPv6 graceful restart
                if "graceful_restart_ipv6" in current_ospf_config:
                    ospf_config["graceful_restart_ipv6"] = current_ospf_config["graceful_restart_ipv6"]
                else:
                    # If IPv6 graceful restart doesn't exist, initialize it from base graceful_restart
                    ospf_config["graceful_restart_ipv6"] = base_graceful_restart
                
                # Now update only the graceful restart for the selected address family
                if is_ipv6:
                    ospf_config["graceful_restart_ipv6"] = new_graceful_restart
                    # Keep graceful_restart_ipv4 unchanged (already set above)
                else:
                    ospf_config["graceful_restart_ipv4"] = new_graceful_restart
                    # Keep graceful_restart_ipv6 unchanged (already set above)
                
                # Update the old graceful_restart field only if both address families use the same setting
                # Otherwise, keep it for backward compatibility with the last updated one
                if ospf_config.get("graceful_restart_ipv4") == ospf_config.get("graceful_restart_ipv6"):
                    ospf_config["graceful_restart"] = ospf_config["graceful_restart_ipv4"]
                else:
                    # If they differ, keep graceful_restart as the one that was just updated
                    ospf_config["graceful_restart"] = new_graceful_restart
            
            # Update the device with OSPF configuration
            # Pass device_name instead of row since row is from OSPF table, not devices table
            self.parent._update_device_protocol(device_name, "OSPF", ospf_config)
    
    def prompt_delete_ospf(self):
        """Delete OSPF configuration for selected device."""
        selected_items = self.parent.ospf_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select an OSPF configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.parent.ospf_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self.parent, "Confirm Deletion", 
                                   f"Are you sure you want to delete OSPF configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices and remove OSPF configuration
        device_info = None
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        # Check if device has OSPF configuration (handle both old and new formats)
        has_ospf = False
        if device_info and "protocols" in device_info:
            if isinstance(device_info["protocols"], list) and "OSPF" in device_info["protocols"]:
                has_ospf = True
            elif isinstance(device_info["protocols"], dict) and "OSPF" in device_info["protocols"]:
                has_ospf = True
        
        if has_ospf:
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove OSPF configuration from server first
                server_url = self.parent.get_server_url()
                if server_url:
                    try:
                        # Call server OSPF cleanup endpoint
                        response = requests.post(f"{server_url}/api/ospf/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            print(f"✅ OSPF configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            print(f"⚠️ Server OSPF cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        print(f"⚠️ Network error removing OSPF from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    print("⚠️ No server URL available, removing OSPF configuration locally only")
            
            # Mark OSPF for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if "protocols" in device_info:
                if isinstance(device_info["protocols"], list):
                    # New format: protocols is a list like ["BGP", "OSPF"]
                    if "OSPF" in device_info["protocols"]:
                        device_info["protocols"].remove("OSPF")
                    # Mark OSPF config for removal
                    device_info["ospf_config"] = {"_marked_for_removal": True}
                elif isinstance(device_info["protocols"], dict):
                    # Old format: protocols is a dict like {"OSPF": {...}}
                    if isinstance(device_info["protocols"], dict):
                        device_info["protocols"]["OSPF"] = {"_marked_for_removal": True}
                    else:
                        device_info["ospf_config"] = {"_marked_for_removal": True}
            
            # Update the OSPF table to show the device as marked for removal
            self.update_ospf_table()
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()
            
            QMessageBox.information(self.parent, "OSPF Configuration Marked for Removal", 
                                  f"OSPF configuration for '{device_name}' has been marked for removal. Click 'Apply OSPF Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self.parent, "No OSPF Configuration", f"No OSPF configuration found for device '{device_name}'.")
    
    def on_ospf_table_cell_changed(self, row, column):
        """Handle cell changes in OSPF table - handles inline editing of Area ID and Graceful Restart."""
        # Only process Area ID (column 2) and Graceful Restart (column 10) columns
        if column not in [2, 10]:
            return
        
        # Get table items with null checks
        device_item = self.parent.ospf_table.item(row, 0)
        if not device_item:
            return
        device_name = device_item.text()  # Device name column
        
        # Find the device in all_devices
        device_info = None
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        if device_info and "protocols" in device_info and "OSPF" in device_info["protocols"]:
            # Handle both old format (dict) and new format (list)
            if isinstance(device_info["protocols"], dict):
                ospf_config = device_info["protocols"]["OSPF"]
            else:
                ospf_config = device_info.get("ospf_config", {})
            
            # Detect which address family is selected (IPv4 or IPv6)
            neighbor_type_item = self.parent.ospf_table.item(row, 3)  # Column 3 is "Neighbor Type"
            if neighbor_type_item:
                protocol_type = neighbor_type_item.text().strip()
                is_ipv6 = protocol_type == "IPv6"
            else:
                # Fallback: assume IPv4 if not found
                is_ipv6 = False
            
            if column == 2:  # Area ID changed (column 2)
                area_id_item = self.parent.ospf_table.item(row, 2)
                
                if area_id_item:
                    new_area_id = area_id_item.text().strip()
                    
                    # Validate area ID format (supports both decimal and dotted-decimal)
                    try:
                        if new_area_id:
                            # Try to parse as decimal (0-4294967295)
                            try:
                                area_decimal = int(new_area_id)
                                if area_decimal < 0 or area_decimal > 4294967295:
                                    raise ValueError("Area ID out of range")
                            except ValueError:
                                # Try to parse as dotted-decimal (A.B.C.D)
                                try:
                                    parts = new_area_id.split(".")
                                    if len(parts) != 4:
                                        raise ValueError("Invalid dotted-decimal format")
                                    for part in parts:
                                        if not (0 <= int(part) <= 255):
                                            raise ValueError("Octet out of range")
                                except (ValueError, AttributeError):
                                    raise ValueError("Invalid area ID format")
                    except ValueError as e:
                        QMessageBox.warning(self.parent, "Invalid Area ID", 
                                          f"'{new_area_id}' is not a valid OSPF area ID.\n"
                                          f"Area ID must be:\n"
                                          f"- Decimal: 0-4294967295\n"
                                          f"- Dotted-decimal: A.B.C.D (each octet 0-255)")
                        # Revert to original value
                        if is_ipv6:
                            original_area = ospf_config.get("area_id_ipv6") or ospf_config.get("area_id", "0.0.0.0")
                        else:
                            original_area = ospf_config.get("area_id_ipv4") or ospf_config.get("area_id", "0.0.0.0")
                        area_id_item.setText(original_area)
                        return
                    
                    # Update only the selected address family's area ID
                    if is_ipv6:
                        ospf_config["area_id_ipv6"] = new_area_id
                        # Update generic area_id only if both are the same
                        if ospf_config.get("area_id_ipv4") == new_area_id:
                            ospf_config["area_id"] = new_area_id
                    else:
                        ospf_config["area_id_ipv4"] = new_area_id
                        # Update generic area_id only if both are the same
                        if ospf_config.get("area_id_ipv6") == new_area_id:
                            ospf_config["area_id"] = new_area_id
            
            elif column == 10:  # Graceful Restart changed (column 10)
                graceful_restart_item = self.parent.ospf_table.item(row, 10)
                
                if graceful_restart_item:
                    graceful_restart_text = graceful_restart_item.text().strip().lower()
                    
                    # Validate graceful restart value (Yes/No)
                    if graceful_restart_text not in ["yes", "no", "true", "false", "1", "0", ""]:
                        QMessageBox.warning(self.parent, "Invalid Graceful Restart Value", 
                                          f"'{graceful_restart_text}' is not a valid graceful restart value.\n"
                                          f"Please use: Yes, No, True, False, 1, or 0")
                        # Revert to original value
                        if is_ipv6:
                            original_gr = ospf_config.get("graceful_restart_ipv6", False)
                        else:
                            original_gr = ospf_config.get("graceful_restart_ipv4", False)
                        graceful_restart_item.setText("Yes" if original_gr else "No")
                        return
                    
                    # Convert text to boolean
                    graceful_restart = graceful_restart_text in ["yes", "true", "1"]
                    
                    # Update only the selected address family's graceful restart
                    if is_ipv6:
                        ospf_config["graceful_restart_ipv6"] = graceful_restart
                        # Update generic graceful_restart only if both are the same
                        if ospf_config.get("graceful_restart_ipv4") == graceful_restart:
                            ospf_config["graceful_restart"] = graceful_restart
                    else:
                        ospf_config["graceful_restart_ipv4"] = graceful_restart
                        # Update generic graceful_restart only if both are the same
                        if ospf_config.get("graceful_restart_ipv6") == graceful_restart:
                            ospf_config["graceful_restart"] = graceful_restart
            
            # Update the device protocol configuration
            self.parent._update_device_protocol(device_name, "OSPF", ospf_config)
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()
    
    def apply_ospf_configurations(self):
        """Apply OSPF configurations to the server for selected OSPF table rows."""
        server_url = self.parent.get_server_url()
        if not server_url:
            QMessageBox.critical(self.parent, "No Server", "No server selected.")
            return

        # Get selected rows from the OSPF table
        selected_items = self.parent.ospf_table.selectedItems()
        
        # Handle both OSPF application and removal
        devices_to_apply_ospf = []  # Devices that need OSPF configuration applied
        devices_to_remove_ospf = []  # Devices that need OSPF configuration removed
        
        if selected_items:
            # If OSPF table rows are selected, process only those devices
            # Track selected rows with their address family (IPv4/IPv6)
            selected_rows = {}  # {device_name: {row_index, protocol_type}}
            for item in selected_items:
                row = item.row()
                device_name = self.parent.ospf_table.item(row, 0).text()  # Device column
                protocol_type_item = self.parent.ospf_table.item(row, 3)  # Neighbor Type column (IPv4 or IPv6)
                protocol_type = protocol_type_item.text() if protocol_type_item else "Unknown"
                
                if device_name not in selected_rows:
                    selected_rows[device_name] = []
                selected_rows[device_name].append({
                    "row": row,
                    "protocol_type": protocol_type
                })
            
            # Find devices and determine if they need OSPF applied or removed
            # Group by device and track which address families are selected
            for device_name, row_info_list in selected_rows.items():
                for iface, devices in self.parent.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            ospf_config = device.get("ospf_config", {})
                            if ospf_config:
                                if ospf_config.get("_marked_for_removal"):
                                    # Device is marked for OSPF removal
                                    devices_to_remove_ospf.append(device)
                                else:
                                    # Device has normal OSPF config - needs application
                                    # Store which address families are selected for this device
                                    # Deduplicate the address families (in case multiple cells in same row are selected)
                                    selected_afs = list(set([ri["protocol_type"] for ri in row_info_list]))
                                    device["_selected_address_families"] = selected_afs
                                    devices_to_apply_ospf.append(device)
                            else:
                                # Device was in OSPF table but no longer has OSPF config - needs removal
                                devices_to_remove_ospf.append(device)
                            break
        else:
            # If no OSPF table rows selected, show message
            QMessageBox.information(self.parent, "No Selection", 
                                  "Please select OSPF table rows to apply configuration.")
            return

        # Check if we have any work to do
        if not devices_to_apply_ospf and not devices_to_remove_ospf:
            QMessageBox.information(self.parent, "No OSPF Changes", 
                                  "No OSPF configurations to apply or remove.")
            return

        # Apply OSPF configurations
        success_count = 0
        failed_devices = []
        
        # Handle OSPF application
        for device_info in devices_to_apply_ospf:
            device_name = device_info.get("Device Name", "Unknown")
            device_id = device_info.get("device_id")
            
            if not device_id:
                failed_devices.append(f"{device_name}: Missing device ID")
                continue
                
            try:
                # Prepare OSPF configuration payload
                ospf_config = device_info.get("ospf_config", {}).copy()
                
                # If specific address families are selected, add a flag to indicate which ones to configure
                # This prevents the server from removing configurations for the other address family
                selected_address_families = device_info.get("_selected_address_families", [])
                if selected_address_families:
                    # Add a flag to indicate which address families should be configured in this apply
                    # The server will use this to only configure the selected families without removing others
                    ospf_config["_apply_address_families"] = selected_address_families
                    # Preserve the existing enabled flags to prevent removal of other address family
                    # Don't modify ipv4_enabled or ipv6_enabled - keep them as they are
                
                payload = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": device_info.get("Interface", ""),
                    "vlan": device_info.get("VLAN", "0"),
                    "ipv4": device_info.get("IPv4", ""),
                    "ipv6": device_info.get("IPv6", ""),
                    "ospf_config": ospf_config
                }
                
                # Send OSPF configuration to server
                # Use longer timeout (30s) since OSPF configuration may take time
                response = requests.post(f"{server_url}/api/device/ospf/configure", 
                                       json=payload, timeout=30)
                
                if response.status_code == 200:
                    success_count += 1
                    print(f"✅ OSPF configuration applied for {device_name}")
                else:
                    error_msg = response.json().get("error", "Unknown error")
                    failed_devices.append(f"{device_name}: {error_msg}")
                    print(f"❌ Failed to apply OSPF for {device_name}: {error_msg}")
                    
            except requests.exceptions.RequestException as e:
                failed_devices.append(f"{device_name}: Network error - {str(e)}")
                print(f"❌ Network error applying OSPF for {device_name}: {str(e)}")
            except Exception as e:
                failed_devices.append(f"{device_name}: {str(e)}")
                print(f"❌ Error applying OSPF for {device_name}: {str(e)}")

        # Handle OSPF removal
        removal_success_count = 0
        removal_failed_devices = []
        
        for device_info in devices_to_remove_ospf:
            device_name = device_info.get("Device Name", "Unknown")
            device_id = device_info.get("device_id")
            
            if not device_id:
                removal_failed_devices.append(f"{device_name}: Missing device ID")
                continue
                
            try:
                # Call OSPF cleanup endpoint to remove OSPF configuration
                response = requests.post(f"{server_url}/api/ospf/cleanup", 
                                       json={"device_id": device_id}, 
                                       timeout=30)
                
                if response.status_code == 200:
                    removal_success_count += 1
                    print(f"✅ OSPF configuration removed for {device_name}")
                    
                    # Remove OSPF configuration from client data after successful server removal
                    if "protocols" in device_info:
                        if isinstance(device_info["protocols"], dict):
                            if device_info["protocols"].get("OSPF", {}).get("_marked_for_removal"):
                                del device_info["protocols"]["OSPF"]
                        else:
                            if device_info.get("ospf_config", {}).get("_marked_for_removal"):
                                del device_info["ospf_config"]
                            # If no other protocols, remove the protocols key entirely
                            if "protocols" in device_info and not device_info["protocols"]:
                                del device_info["protocols"]
                else:
                    error_msg = response.json().get("error", "Unknown error")
                    removal_failed_devices.append(f"{device_name}: {error_msg}")
                    print(f"❌ Failed to remove OSPF for {device_name}: {error_msg}")
                    
            except requests.exceptions.RequestException as e:
                removal_failed_devices.append(f"{device_name}: Network error - {str(e)}")
                print(f"❌ Network error removing OSPF for {device_name}: {str(e)}")
            except Exception as e:
                removal_failed_devices.append(f"{device_name}: {str(e)}")
                print(f"❌ Error removing OSPF for {device_name}: {str(e)}")

        # Show results - combine application and removal results
        total_success = success_count + removal_success_count
        total_failed = len(failed_devices) + len(removal_failed_devices)
        total_operations = len(devices_to_apply_ospf) + len(devices_to_remove_ospf)
        
        if total_operations == 0:
            QMessageBox.information(self.parent, "No OSPF Operations", "No OSPF operations to perform.")
            return
        
        # Build result messages
        all_results = []
        
        # Add OSPF application results
        if devices_to_apply_ospf:
            for device_info in devices_to_apply_ospf:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in failed_devices]:
                    all_results.append(f"✅ Applied OSPF to {device_name}")
        
        # Add OSPF removal results  
        if devices_to_remove_ospf:
            for device_info in devices_to_remove_ospf:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in removal_failed_devices]:
                    all_results.append(f"✅ Removed OSPF from {device_name}")
        
        # Add failed operations
        all_results.extend([f"❌ {failed}" for failed in failed_devices])
        all_results.extend([f"❌ {failed}" for failed in removal_failed_devices])
        
        # Show appropriate dialog
        if total_success == total_operations:
            # All successful
            if len(devices_to_apply_ospf) > 0 and len(devices_to_remove_ospf) > 0:
                title = "OSPF Operations Completed"
                message = f"Successfully applied OSPF to {success_count} device(s) and removed OSPF from {removal_success_count} device(s)."
            elif len(devices_to_apply_ospf) > 0:
                title = "OSPF Applied Successfully"
                message = f"OSPF configuration applied successfully for {success_count} device(s)."
            else:
                title = "OSPF Removed Successfully"
                message = f"OSPF configuration removed successfully from {removal_success_count} device(s)."
            
            QMessageBox.information(self.parent, title, message)
        elif total_success > 0:
            # Partial success - use scrollable dialog
            from widgets.devices_tab import MultiDeviceResultsDialog
            dialog = MultiDeviceResultsDialog(
                "OSPF Operations Partially Completed", 
                f"Completed {total_success} of {total_operations} OSPF operations.",
                all_results,
                self.parent
            )
            dialog.exec_()
        else:
            # All failed - use scrollable dialog
            from widgets.devices_tab import MultiDeviceResultsDialog
            dialog = MultiDeviceResultsDialog(
                "OSPF Operations Failed", 
                "Failed to complete any OSPF operations.",
                all_results,
                self.parent
            )
            dialog.exec_()

        # Update OSPF table to reflect any changes
        self.update_ospf_table()

    def start_ospf_protocol(self):
        """Start OSPF protocol for selected devices."""
        self.parent._toggle_protocol_action("OSPF", starting=True)

    def stop_ospf_protocol(self):
        """Stop OSPF protocol for selected devices."""
        self.parent._toggle_protocol_action("OSPF", starting=False)
    
    def start_ospf_monitoring(self):
        """Start periodic OSPF status monitoring."""
        if not self.parent.ospf_monitoring_active:
            self.parent.ospf_monitoring_active = True
            self.parent.ospf_monitoring_timer.start(20000)  # Check every 20 seconds to reduce UI load
            print("[OSPF MONITORING] Started periodic OSPF status monitoring")
        else:
            print("[OSPF MONITORING] Already active")
    
    def stop_ospf_monitoring(self):
        """Stop periodic OSPF status monitoring."""
        if self.parent.ospf_monitoring_active:
            self.parent.ospf_monitoring_active = False
            self.parent.ospf_monitoring_timer.stop()
            print("[OSPF MONITORING] Stopped periodic OSPF status monitoring")
        else:
            print("[OSPF MONITORING] Already stopped")
    
    def periodic_ospf_status_check(self):
        """Periodic OSPF status check for all devices with OSPF configured."""
        if not self.parent.ospf_monitoring_active:
            return
        
        # Check if any devices have OSPF configured
        devices_with_ospf = []
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                device_protocols = device.get("protocols", [])
                if isinstance(device_protocols, str):
                    try:
                        import json
                        device_protocols = json.loads(device_protocols)
                    except:
                        device_protocols = []
                
                if "OSPF" in device_protocols:
                    devices_with_ospf.append(device)
        
        # If no devices have OSPF configured, stop monitoring
        if not devices_with_ospf:
            print("[OSPF MONITORING] No devices with OSPF configured - stopping monitoring")
            self.stop_ospf_monitoring()
            return
            
        # Update OSPF table which will refresh all OSPF statuses
        self.update_ospf_table()
        print(f"[OSPF MONITORING] Periodic OSPF status check completed for {len(devices_with_ospf)} devices")
    
    def _cleanup_ospf_table_for_device(self, device_id, device_name):
        """Clean up OSPF table entries for a removed device."""
        try:
            print(f"[DEBUG OSPF CLEANUP] Cleaning up OSPF entries for device '{device_name}' (ID: {device_id})")
            
            # Remove OSPF table rows that match this device
            rows_to_remove = []
            for row in range(self.parent.ospf_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.parent.ospf_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    print(f"[DEBUG OSPF CLEANUP] Found OSPF row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.parent.ospf_table.removeRow(row)
                print(f"[DEBUG OSPF CLEANUP] Removed OSPF table row {row}")
            
            # Also clean up OSPF protocol data from device protocols
            # Remove OSPF protocol from the device in all_devices
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove OSPF from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list) and "OSPF" in device["protocols"]:
                                device["protocols"].remove("OSPF")
                                print(f"[DEBUG OSPF CLEANUP] Removed OSPF protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG OSPF CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict) and "OSPF" in device["protocols"]:
                                # Handle old format for backward compatibility
                                del device["protocols"]["OSPF"]
                                print(f"[DEBUG OSPF CLEANUP] Removed OSPF protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG OSPF CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove ospf_config if it exists
                        if "ospf_config" in device:
                            del device["ospf_config"]
                            print(f"[DEBUG OSPF CLEANUP] Removed ospf_config from device '{device_name}'")
                        break
            
            print(f"[DEBUG OSPF CLEANUP] Removed {len(rows_to_remove)} OSPF entries for device '{device_name}'")
            
        except Exception as e:
            print(f"[ERROR] Failed to cleanup OSPF entries for device '{device_name}': {e}")

