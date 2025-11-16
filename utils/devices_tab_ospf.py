"""OSPF-related functionality for DevicesTab.

This module contains all OSPF-specific methods extracted from devices_tab.py
to improve code organization and maintainability.
"""

from PyQt5.QtWidgets import (
    QTableWidgetItem, QMessageBox, QDialog, QTableWidget, 
    QPushButton, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QDialogButtonBox, QCheckBox, QGroupBox
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
        ospf_headers = ["Device", "OSPF Status", "Area ID", "Neighbor Type", "Interface", "Neighbor ID", "State", "Priority", "Dead Timer", "Uptime", "Graceful Restart", "P2P", "Route Pools"]
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
        self.parent.ospf_refresh_button.setToolTip("Refresh/Update OSPF Table (Read-only, no server changes)")
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
        self.parent.apply_ospf_button.setToolTip("Apply Selected OSPF Configurations to Server (Multiple selections supported)")
        self.parent.apply_ospf_button.clicked.connect(self.apply_ospf_configurations)
        
        # Attach Route Pools button (in OSPF tab - device-specific)
        self.parent.attach_route_pools_button = QPushButton()
        self.parent.attach_route_pools_button.setIcon(load_icon("readd.png"))
        self.parent.attach_route_pools_button.setIconSize(QSize(16, 16))
        self.parent.attach_route_pools_button.setFixedSize(32, 28)
        self.parent.attach_route_pools_button.setToolTip("Attach Route Pools to OSPF Device")
        self.parent.attach_route_pools_button.clicked.connect(self.prompt_attach_route_pools)
        
        # Detach Route Pools button (in OSPF tab - device-specific)
        self.parent.detach_route_pools_button = QPushButton()
        self.parent.detach_route_pools_button.setIcon(load_icon("remove.png"))
        self.parent.detach_route_pools_button.setIconSize(QSize(16, 16))
        self.parent.detach_route_pools_button.setFixedSize(32, 28)
        self.parent.detach_route_pools_button.setToolTip("Detach Route Pools from OSPF Device")
        self.parent.detach_route_pools_button.clicked.connect(self.prompt_detach_route_pools)
        
        ospf_controls.addWidget(self.parent.add_ospf_button)
        ospf_controls.addWidget(self.parent.edit_ospf_button)
        ospf_controls.addWidget(self.parent.delete_ospf_button)
        ospf_controls.addWidget(self.parent.apply_ospf_button)
        ospf_controls.addWidget(self.parent.ospf_start_button)
        ospf_controls.addWidget(self.parent.ospf_stop_button)
        ospf_controls.addWidget(self.parent.ospf_refresh_button)
        ospf_controls.addWidget(self.parent.attach_route_pools_button)
        ospf_controls.addWidget(self.parent.detach_route_pools_button)
        ospf_controls.addStretch()
        layout.addLayout(ospf_controls)
    
    def refresh_ospf_status(self):
        """Refresh/Update OSPF table display (read-only, does NOT apply to server)."""
        try:
            print("[OSPF REFRESH] Refreshing OSPF table display from database...")
            # Temporarily disconnect cellChanged signal to prevent issues during refresh
            try:
                self.parent.ospf_table.cellChanged.disconnect()
            except:
                pass  # Signal might not be connected
            
            # Update the OSPF table which fetches status from database
            # This is READ-ONLY - it only updates the display, does NOT apply to server
            self.update_ospf_table()
            
            # Force table to repaint/update
            self.parent.ospf_table.viewport().update()
            self.parent.ospf_table.repaint()
            
            # Reconnect cellChanged signal
            try:
                self.parent.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
            except:
                pass  # Signal might already be connected
            
            print(f"[OSPF REFRESH] OSPF table refreshed successfully (no server changes) - {self.parent.ospf_table.rowCount()} rows displayed")
        except Exception as e:
            print(f"[OSPF REFRESH ERROR] Error refreshing OSPF table: {e}")
            import traceback
            traceback.print_exc()
            # Try to reconnect signal even on error
            try:
                self.parent.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
            except:
                pass
    
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
                        
                        # CRITICAL: Ensure ospf_config is a dict, not a string
                        # Sometimes it might be stored as a JSON string in the database
                        if isinstance(ospf_config, str):
                            try:
                                import json
                                ospf_config = json.loads(ospf_config)
                            except:
                                ospf_config = {}
                        
                        # CRITICAL: Skip database reload in update_ospf_table to prevent UI hangs
                        # Database reload is expensive and blocks the UI thread
                        # Only reload from database when explicitly needed (e.g., after apply)
                        # For normal table updates, use in-memory config which is already up-to-date
                        # This prevents micro hangs on every click or table update
                        has_pending_edits = device.get("_needs_apply", False) or device.get("_ospf_just_edited", False) or device.get("_ospf_just_applied", False)
                        
                        # DISABLED: Database reload in update_ospf_table causes UI hangs
                        # The in-memory config is sufficient for display purposes
                        # Database reload should only happen explicitly after apply operations
                        # if device_id and not has_pending_edits:
                        #     try:
                        #         server_url = self.parent.get_server_url(silent=True)
                        #         if server_url:
                        #             response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=3)
                        #             if response.status_code == 200:
                        #                 db_device = response.json()
                        #                 db_ospf_config = db_device.get("ospf_config", {})
                        #                 if isinstance(db_ospf_config, str):
                        #                     try:
                        #                         import json
                        #                         db_ospf_config = json.loads(db_ospf_config)
                        #                     except:
                        #                         db_ospf_config = {}
                        #                 
                        #                 # Use database config if it exists and is valid
                        #                 if db_ospf_config and isinstance(db_ospf_config, dict):
                        #                     # Update ospf_config with database values
                        #                     ospf_config = db_ospf_config
                        #                     # Also update all_devices to keep it in sync
                        #                     device["ospf_config"] = db_ospf_config
                        #     except Exception:
                        #         # Silently fail - use in-memory config as fallback
                        #         pass
                        
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
                        # CRITICAL: Get area_id as fallback, but always prefer area_id_ipv4/ipv6 for specific address families
                        area_id = ospf_config.get("area_id", "0.0.0.0") if ospf_config else "0.0.0.0"
                        
                        # Get OSPF configuration flags
                        # Default heuristics: if not explicitly set, infer from presence of device IPs
                        device_ipv4 = (device.get("IPv4", "") or "").strip()
                        device_ipv6 = (device.get("IPv6", "") or "").strip()
                        # If not explicitly set in config, infer from presence of device IPs
                        if ospf_config and "ipv4_enabled" in ospf_config:
                            ipv4_enabled = bool(ospf_config.get("ipv4_enabled"))
                        else:
                            ipv4_enabled = bool(device_ipv4)
                        if ospf_config and "ipv6_enabled" in ospf_config:
                            ipv6_enabled = bool(ospf_config.get("ipv6_enabled"))
                        else:
                            ipv6_enabled = bool(device_ipv6)
                        
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
                            # IMPORTANT: Check if key exists, not just truthiness, since "0.0.0.0" is a valid area ID
                            # CRITICAL: Always prefer address-family-specific area ID over generic area_id
                            # CRITICAL: For IPv4, use area_id_ipv4; for IPv6, use area_id_ipv6
                            if protocol_type == "IPv6":
                                # For IPv6, use area_id_ipv6 if it exists, otherwise fall back to area_id
                                if "area_id_ipv6" in ospf_config:
                                    display_area_id = ospf_config.get("area_id_ipv6", "0.0.0.0")
                                else:
                                    # Fall back to generic area_id only if area_id_ipv6 is not set
                                    display_area_id = area_id
                            else:
                                # For IPv4, ALWAYS use area_id_ipv4 if it exists in the config
                                # Do NOT fall back to area_id_ipv6 or area_id unless area_id_ipv4 is missing
                                if "area_id_ipv4" in ospf_config:
                                    # Use area_id_ipv4 directly - don't use get() with default as "0.0.0.0" is valid
                                    display_area_id = ospf_config["area_id_ipv4"]
                                else:
                                    # Fall back to generic area_id only if area_id_ipv4 is not set
                                    display_area_id = area_id
                                    
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
                            
                            # Graceful Restart (column 10) - Checkbox for graceful restart
                            graceful_restart_checkbox = QCheckBox()
                            # Get graceful restart status for this specific address family
                            # Support separate graceful restart for IPv4 and IPv6, with backward compatibility
                            # CRITICAL: Check if key exists first to handle False as a valid value
                            # Using "or" would incorrectly treat False as falsy and fall back to graceful_restart
                            if protocol_type == "IPv6":
                                if "graceful_restart_ipv6" in ospf_config:
                                    # Key exists, use it directly (even if value is False)
                                    graceful_restart = ospf_config["graceful_restart_ipv6"]
                                else:
                                    # Key doesn't exist, fall back to generic graceful_restart
                                    graceful_restart = ospf_config.get("graceful_restart", False) if ospf_config else False
                            else:
                                if "graceful_restart_ipv4" in ospf_config:
                                    # Key exists, use it directly (even if value is False)
                                    graceful_restart = ospf_config["graceful_restart_ipv4"]
                                else:
                                    # Key doesn't exist, fall back to generic graceful_restart
                                    graceful_restart = ospf_config.get("graceful_restart", False) if ospf_config else False
                            graceful_restart_checkbox.setChecked(graceful_restart)
                            graceful_restart_checkbox.setToolTip(f"Enable graceful restart for {protocol_type}")
                            # Store device info in checkbox for later reference
                            graceful_restart_checkbox.setProperty("device_name", device_name)
                            graceful_restart_checkbox.setProperty("protocol_type", protocol_type)
                            graceful_restart_checkbox.setProperty("row", row)
                            # Connect checkbox state change
                            graceful_restart_checkbox.stateChanged.connect(lambda state, cb=graceful_restart_checkbox: self.on_graceful_restart_checkbox_changed(cb, state))
                            self.parent.ospf_table.setCellWidget(row, 10, graceful_restart_checkbox)
                            
                            # P2P (column 11) - Checkbox for point-to-point network type
                            p2p_checkbox = QCheckBox()
                            # Get P2P setting for this address family
                            if protocol_type == "IPv6":
                                p2p_enabled = ospf_config.get("p2p_ipv6", False) if ospf_config else False
                            else:
                                p2p_enabled = ospf_config.get("p2p_ipv4", False) or ospf_config.get("p2p", False) if ospf_config else False
                            p2p_checkbox.setChecked(p2p_enabled)
                            p2p_checkbox.setToolTip(f"Enable point-to-point network type for {protocol_type}")
                            # Store device info in checkbox for later reference
                            p2p_checkbox.setProperty("device_name", device_name)
                            p2p_checkbox.setProperty("protocol_type", protocol_type)
                            p2p_checkbox.setProperty("row", row)
                            # Connect checkbox state change
                            p2p_checkbox.stateChanged.connect(lambda state, cb=p2p_checkbox: self.on_p2p_checkbox_changed(cb, state))
                            self.parent.ospf_table.setCellWidget(row, 11, p2p_checkbox)
                            
                            # Route Pools (column 12) - Show attached pool names for this specific address family
                            route_pools_str = ""
                            if ospf_config and "route_pools" in ospf_config:
                                route_pools = ospf_config.get("route_pools", {})
                                if isinstance(route_pools, dict):
                                    # New format: route_pools = {"IPv4": [...], "IPv6": [...]}
                                    pools_for_family = route_pools.get(protocol_type, [])
                                    if isinstance(pools_for_family, list):
                                        route_pools_str = ", ".join(pools_for_family) if pools_for_family else ""
                                elif isinstance(route_pools, list):
                                    # Old format: route_pools is a list (backward compatibility)
                                    # Only show if it's the first address family (IPv4) to avoid duplication
                                    if protocol_type == "IPv4":
                                        route_pools_str = ", ".join(route_pools) if route_pools else ""
                            
                            pool_item = QTableWidgetItem(route_pools_str if route_pools_str else "")
                            pool_item.setToolTip(f"Attached route pools for {protocol_type}: {route_pools_str if route_pools_str else 'None'}")
                            self.parent.ospf_table.setItem(row, 12, pool_item)  # Route Pools
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
            # Get route pools from ospf_config - support both old list and new dict format
            route_pools_data = ospf_config.get("route_pools", {})
            if isinstance(route_pools_data, list):
                # Old format: convert to dict format
                route_pools_data = {"IPv4": route_pools_data, "IPv6": []}
            elif not isinstance(route_pools_data, dict):
                route_pools_data = {"IPv4": [], "IPv6": []}
            
            # Get all route pools from main window for generation
            all_route_pools = getattr(self.parent.main_window, 'bgp_route_pools', [])
            
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
                "route_pools_per_area": {},  # Will be populated by server from ospf_config["route_pools"]
                "all_route_pools": all_route_pools  # Include all route pools for generation
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
            # CRITICAL: Check if key exists first to handle False as a valid value
            if "graceful_restart_ipv6" in current_ospf_config:
                current_graceful_restart = current_ospf_config["graceful_restart_ipv6"]
            else:
                current_graceful_restart = current_ospf_config.get("graceful_restart", False)
        else:
            # CRITICAL: Check if key exists first to handle False as a valid value
            if "graceful_restart_ipv4" in current_ospf_config:
                current_graceful_restart = current_ospf_config["graceful_restart_ipv4"]
            else:
                current_graceful_restart = current_ospf_config.get("graceful_restart", False)
        
        dialog_config["graceful_restart"] = current_graceful_restart
        
        # Create and show OSPF dialog
        from widgets.add_ospf_dialog import AddOspfDialog
        dialog = AddOspfDialog(self.parent, device_name, dialog_config)
        
        if dialog.exec_() == QDialog.Accepted:
            ospf_config = dialog.get_values()
            new_area_id = ospf_config.get("area_id", "0.0.0.0")
            new_graceful_restart = ospf_config.get("graceful_restart", False)
            
            # Preserve existing OSPF config fields that are not in the dialog
            # (ipv4_enabled, ipv6_enabled, interface, route_pools, etc.)
            if current_ospf_config:
                ospf_config.setdefault("ipv4_enabled", current_ospf_config.get("ipv4_enabled", False))
                ospf_config.setdefault("ipv6_enabled", current_ospf_config.get("ipv6_enabled", False))
                ospf_config.setdefault("interface", current_ospf_config.get("interface", ""))
                # CRITICAL: Preserve route_pools to prevent accidental removal when editing config
                if "route_pools" in current_ospf_config:
                    ospf_config["route_pools"] = current_ospf_config["route_pools"]
                
                # CRITICAL: Preserve P2P settings to prevent accidental removal when editing config
                if "p2p_ipv4" in current_ospf_config:
                    ospf_config["p2p_ipv4"] = current_ospf_config["p2p_ipv4"]
                if "p2p_ipv6" in current_ospf_config:
                    ospf_config["p2p_ipv6"] = current_ospf_config["p2p_ipv6"]
                if "p2p" in current_ospf_config:
                    ospf_config["p2p"] = current_ospf_config["p2p"]  # For backward compatibility
                
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
            
            # Store the updated config temporarily in the device_info for Apply OSPF to use
            # But don't save the session yet - that will happen when Apply OSPF is clicked
            # Update the device data structure in memory only (not saved to session)
            if device_info:
                # Update ospf_config in device_info directly
                device_info["ospf_config"] = ospf_config
                # Mark device as needing apply
                device_info["_needs_apply"] = True
            
            # Update the OSPF table to reflect the changes
            # Temporarily disconnect cellChanged signal to prevent issues during refresh
            try:
                self.parent.ospf_table.cellChanged.disconnect()
            except:
                pass  # Signal might not be connected
            
            self.update_ospf_table()
            
            # Force table to repaint/update
            self.parent.ospf_table.viewport().update()
            self.parent.ospf_table.repaint()
            
            # Reconnect cellChanged signal
            try:
                self.parent.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
            except:
                pass  # Signal might already be connected
            
            print(f"[OSPF EDIT] OSPF configuration updated in table for {device_name} ({protocol_type}) - click 'Apply OSPF' to save and apply to server")
            QMessageBox.information(self.parent, "OSPF Configuration Updated", 
                                  f"OSPF configuration updated for {device_name} ({protocol_type}).\n\n"
                                  f"The changes are shown in the table.\n\n"
                                  f"Click 'Apply OSPF' button to save and apply the configuration to the server.")
    
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
    
    def on_p2p_checkbox_changed(self, checkbox, state):
        """Handle P2P checkbox state change."""
        try:
            device_name = checkbox.property("device_name")
            protocol_type = checkbox.property("protocol_type")
            is_ipv6 = (protocol_type == "IPv6")
            
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
                print(f"[OSPF P2P] Device {device_name} not found")
                return
            
            # Get current OSPF config
            ospf_config = device_info.get("ospf_config", {})
            if not isinstance(ospf_config, dict):
                ospf_config = {}
            
            # Update P2P setting for the selected address family
            p2p_enabled = (state == 2)  # Qt.Checked = 2
            
            # Update only the selected address family's P2P setting
            update_dict = {}
            if is_ipv6:
                update_dict["p2p_ipv6"] = p2p_enabled
            else:
                update_dict["p2p_ipv4"] = p2p_enabled
            
            # Update ospf_config
            ospf_config.update(update_dict)
            
            # Update device_info
            device_info["ospf_config"] = ospf_config
            device_info["_needs_apply"] = True
            
            # Mark as just edited to prevent database reload from overwriting
            device_info["_ospf_just_edited"] = True
            
            print(f"[OSPF P2P] Updated P2P setting for {device_name} ({protocol_type}): {p2p_enabled}")
            print(f"[OSPF EDIT] OSPF configuration updated in table for {device_name} ({protocol_type}) - click 'Apply OSPF' to save and apply to server")
            
        except Exception as e:
            print(f"[OSPF P2P] Error handling P2P checkbox change: {e}")
    
    def on_graceful_restart_checkbox_changed(self, checkbox, state):
        """Handle Graceful Restart checkbox state change."""
        try:
            device_name = checkbox.property("device_name")
            protocol_type = checkbox.property("protocol_type")
            is_ipv6 = (protocol_type == "IPv6")
            
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
                print(f"[OSPF GR] Device {device_name} not found")
                return
            
            # Get current OSPF config
            ospf_config = device_info.get("ospf_config", {})
            if not isinstance(ospf_config, dict):
                ospf_config = {}
            
            # Update graceful restart setting for the selected address family
            graceful_restart_enabled = (state == 2)  # Qt.Checked = 2
            
            # Update only the selected address family's graceful restart setting
            update_dict = {}
            if is_ipv6:
                update_dict["graceful_restart_ipv6"] = graceful_restart_enabled
            else:
                update_dict["graceful_restart_ipv4"] = graceful_restart_enabled
            
            # Update ospf_config
            ospf_config.update(update_dict)
            
            # Update device_info
            device_info["ospf_config"] = ospf_config
            device_info["_needs_apply"] = True
            
            # Mark as just edited to prevent database reload from overwriting
            device_info["_ospf_just_edited"] = True
            
            print(f"[OSPF GR] Updated graceful restart setting for {device_name} ({protocol_type}): {graceful_restart_enabled}")
            print(f"[OSPF EDIT] OSPF configuration updated in table for {device_name} ({protocol_type}) - click 'Apply OSPF' to save and apply to server")
            
        except Exception as e:
            print(f"[OSPF GR] Error handling graceful restart checkbox change: {e}")
    
    def on_ospf_table_cell_changed(self, row, column):
        """Handle cell changes in OSPF table - handles inline editing of Area ID."""
        # Only process Area ID (column 2) - Graceful Restart is now a checkbox
        if column != 2:
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
                ospf_config = device_info["protocols"]["OSPF"].copy() if device_info["protocols"].get("OSPF") else {}
            else:
                ospf_config = device_info.get("ospf_config", {}).copy() if device_info.get("ospf_config") else {}
            
            # Ensure we have a dict to work with
            if not isinstance(ospf_config, dict):
                ospf_config = {}
            
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
                    
                    # CRITICAL: Skip update if area ID is empty or "0.0.0.0" - don't process empty/invalid values
                    # This prevents overwriting valid config with zeros when user is typing or when table shows default
                    # Only skip if the existing config has a non-zero value (to allow setting 0.0.0.0 explicitly)
                    # IMPORTANT: Get existing area from the full existing config, not from ospf_config which might be incomplete
                    if not new_area_id or new_area_id == "0.0.0.0":
                        # Get the full existing config to check current values
                        existing_ospf_config_for_check = None
                        if isinstance(device_info.get("protocols"), dict):
                            existing_ospf_config_for_check = device_info["protocols"].get("OSPF", {})
                        else:
                            existing_ospf_config_for_check = device_info.get("ospf_config", {})
                        
                        # Check if existing config has a non-zero value - if so, skip this update
                        existing_area = None
                        if is_ipv6:
                            existing_area = existing_ospf_config_for_check.get("area_id_ipv6") if existing_ospf_config_for_check else None
                            if not existing_area:
                                existing_area = existing_ospf_config_for_check.get("area_id", "0.0.0.0") if existing_ospf_config_for_check else "0.0.0.0"
                        else:
                            existing_area = existing_ospf_config_for_check.get("area_id_ipv4") if existing_ospf_config_for_check else None
                            if not existing_area:
                                existing_area = existing_ospf_config_for_check.get("area_id", "0.0.0.0") if existing_ospf_config_for_check else "0.0.0.0"
                        
                        # If existing area is non-zero and new area is zero/empty, skip update
                        # This prevents overwriting valid config with zeros
                        if existing_area and existing_area != "0.0.0.0" and (not new_area_id or new_area_id == "0.0.0.0"):
                            return
                    
                    # Update only the selected address family's area ID
                    # CRITICAL: Only update the specific address family's area ID
                    # Do NOT update the other address family or generic area_id unless explicitly needed
                    # Create a minimal update dict with ONLY the field we're changing
                    # IMPORTANT: Do NOT include other fields from ospf_config - only update what we're changing
                    update_dict = {}
                    if is_ipv6:
                        # Only update IPv6 area ID - do NOT touch IPv4 or generic area_id
                        update_dict["area_id_ipv6"] = new_area_id
                    else:
                        # Only update IPv4 area ID and generic area_id (for backward compatibility)
                        # Do NOT touch IPv6 area ID
                        update_dict["area_id_ipv4"] = new_area_id
                        update_dict["area_id"] = new_area_id  # Update generic area_id for backward compatibility
                    
                    # CRITICAL: Replace ospf_config with ONLY the update_dict
                    # This ensures we don't accidentally include area_id_ipv6 or other fields
                    # The merge logic below will handle preserving other fields from existing_ospf_config
                    ospf_config = update_dict.copy()
            
            # Ensure we have the complete ospf_config with all fields before updating
            # Get the current ospf_config from the device to merge properly
            # We need to get the full config from device_info to ensure we preserve all fields
            # CRITICAL: Start with the existing config, not the modified ospf_config
            # This ensures we preserve all fields including the OTHER address family's area ID
            current_ospf_config_full = {}
            
            # Get the full existing config from device_info FIRST
            # CRITICAL: Also check all_devices to get the latest config (device_info might be stale)
            if device_info:
                # Try to get the full existing config from device_info first
                existing_ospf_config = None
                if isinstance(device_info.get("protocols"), dict):
                    existing_ospf_config = device_info["protocols"].get("OSPF", {})
                else:
                    existing_ospf_config = device_info.get("ospf_config", {})
                
                # CRITICAL: Also check all_devices to get the latest config
                # This ensures we have the most up-to-date config, especially after previous edits
                device_name = device_info.get("Device Name", "")
                if device_name:
                    for iface, devices in self.parent.main_window.all_devices.items():
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                # Get the latest ospf_config from all_devices
                                latest_ospf_config = None
                                if isinstance(device.get("protocols"), dict):
                                    latest_ospf_config = device["protocols"].get("OSPF", {})
                                else:
                                    latest_ospf_config = device.get("ospf_config", {})
                                
                                # Use the latest config if available, otherwise use existing_ospf_config
                                if latest_ospf_config and isinstance(latest_ospf_config, dict):
                                    existing_ospf_config = latest_ospf_config
                                break
                        if device.get("Device Name") == device_name:
                            break
                
                if existing_ospf_config and isinstance(existing_ospf_config, dict):
                    # Start with the full existing config to preserve ALL fields
                    current_ospf_config_full = existing_ospf_config.copy()
            
            # CRITICAL: Only apply the specific changes from ospf_config
            # ospf_config should only contain the fields we're updating (not the full config)
            # This ensures we don't accidentally overwrite the other address family's area ID
            if column == 2:  # Area ID edit
                # Only update the specific address family's area ID
                if is_ipv6:
                    # Only update IPv6 area ID - preserve IPv4 area ID from existing config
                    if "area_id_ipv6" in ospf_config:
                        # CRITICAL: Always update IPv6 area ID from ospf_config (which contains the new value)
                        current_ospf_config_full["area_id_ipv6"] = ospf_config["area_id_ipv6"]
                    # Explicitly preserve IPv4 area ID from existing config
                    if existing_ospf_config and "area_id_ipv4" in existing_ospf_config:
                        current_ospf_config_full["area_id_ipv4"] = existing_ospf_config["area_id_ipv4"]
                else:
                    # Only update IPv4 area ID and generic area_id - preserve IPv6 area ID from existing config
                    if "area_id_ipv4" in ospf_config:
                        current_ospf_config_full["area_id_ipv4"] = ospf_config["area_id_ipv4"]
                    if "area_id" in ospf_config:
                        current_ospf_config_full["area_id"] = ospf_config["area_id"]
                    # Explicitly preserve IPv6 area ID from existing config
                    if existing_ospf_config and "area_id_ipv6" in existing_ospf_config:
                        current_ospf_config_full["area_id_ipv6"] = existing_ospf_config["area_id_ipv6"]
            
            # Update the device protocol configuration
            # CRITICAL: Ensure current_ospf_config_full has the correct values before updating
            if column == 2 and is_ipv6:
                # Double-check that area_id_ipv6 is set correctly
                if "area_id_ipv6" in ospf_config:
                    current_ospf_config_full["area_id_ipv6"] = ospf_config["area_id_ipv6"]
            
            # CRITICAL: Defer heavy operations to prevent UI blocking
            # Use QTimer to defer _update_device_protocol and save_session to next event loop iteration
            from PyQt5.QtCore import QTimer
            
            def deferred_update():
                """Defer the update and save operations to prevent UI blocking."""
                try:
                    self.parent._update_device_protocol(device_name, "OSPF", current_ospf_config_full)
                    
                    # Mark device as just edited to prevent table reload from overwriting the edit
                    # This flag will be cleared when the table is refreshed after apply
                    if device_info:
                        device_info["_ospf_just_edited"] = True
                    
                    # Save session (already async, but defer to avoid blocking)
                    if hasattr(self.parent.main_window, "save_session"):
                        self.parent.main_window.save_session()
                except Exception as e:
                    print(f"[OSPF EDIT ERROR] Error in deferred update: {e}")
            
            # Defer to next event loop iteration to prevent UI blocking
            QTimer.singleShot(0, deferred_update)
    
    def prompt_attach_route_pools(self):
        """Open dialog to attach route pools to selected OSPF devices (Step 2: Attach to OSPF)."""
        # Get selection from OSPF table (not devices table)
        selected_items = self.parent.ospf_table.selectedItems()
        if not selected_items:
            # No rows selected - select all rows
            total_rows = self.parent.ospf_table.rowCount()
            if total_rows > 0:
                self.parent.ospf_table.selectAll()
                print(f"[OSPF TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self.parent, "No OSPF Devices", "No OSPF devices are configured. Please add OSPF configuration first.")
                return
        
        # Get available route pools (can reuse BGP route pools or use OSPF-specific)
        if not hasattr(self.parent.main_window, 'bgp_route_pools'):
            self.parent.main_window.bgp_route_pools = []
        
        available_pools = self.parent.main_window.bgp_route_pools
        
        if not available_pools:
            QMessageBox.warning(self.parent, "No Route Pools", 
                              "No route pools have been defined.\n\n"
                              "Please use 🗂️ 'Manage Route Pools' button (in Devices tab) to create pools first.")
            return
        
        # Collect all selected OSPF devices with their address families
        selected_devices = []
        processed_devices = set()
        
        for item in selected_items:
            row = item.row()
            device_name = self.parent.ospf_table.item(row, 0).text()  # Device column
            neighbor_type_item = self.parent.ospf_table.item(row, 3)  # Neighbor Type column (IPv4 or IPv6)
            neighbor_type = neighbor_type_item.text() if neighbor_type_item else "IPv4"  # Default to IPv4
            
            # Clean device name - remove any suffixes like "(Pending Removal)"
            clean_device_name = device_name.split(" (")[0].strip()
            if clean_device_name != device_name:
                device_name = clean_device_name
            
            # Create unique key for device + address family
            device_key = f"{device_name}:{neighbor_type}"
            if device_key in processed_devices:
                continue
            processed_devices.add(device_key)
            
            # Find device in all_devices using safe helper
            device_info = self.parent._find_device_by_name(device_name)
            
            if not device_info:
                print(f"[OSPF ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue
            
            # Ensure device_info is a dictionary - handle list case
            if not isinstance(device_info, dict):
                print(f"[OSPF ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                # Try to extract dict from list if it's a list
                if isinstance(device_info, list) and len(device_info) > 0:
                    print(f"[OSPF ROUTE POOLS] Attempting to extract dict from list...")
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        print(f"[OSPF ROUTE POOLS] Could not extract dict from list for '{device_name}'")
                        continue
                else:
                    continue
            
            # Final check - ensure device_info is now a dict
            if not isinstance(device_info, dict):
                print(f"[OSPF ROUTE POOLS] Final check failed: device_info is still not a dict for '{device_name}'")
                continue
            
            # Get OSPF config for this device
            # Check if OSPF is in the protocols list
            protocols = device_info.get("protocols", [])
            if isinstance(protocols, str):
                try:
                    import json
                    protocols = json.loads(protocols)
                except:
                    protocols = []
            
            if not isinstance(protocols, list) or "OSPF" not in protocols:
                print(f"[OSPF ROUTE POOLS] Warning: Device '{device_name}' does not have OSPF configured")
                continue
            
            # Get the actual OSPF configuration
            ospf_config = device_info.get("ospf_config", {})
            if not ospf_config:
                print(f"[OSPF ROUTE POOLS] Warning: Device '{device_name}' does not have OSPF configuration")
                continue
            
            selected_devices.append({
                "device_name": device_name,
                "device_info": device_info,
                "ospf_config": ospf_config,
                "address_family": neighbor_type  # Store address family for this selection
            })
        
        if not selected_devices:
            QMessageBox.warning(self.parent, "No Valid OSPF Devices", 
                              "No valid OSPF devices found in the selection.")
            return
        
        # If only one device, use the original dialog
        if len(selected_devices) == 1:
            device_data = selected_devices[0]
            device_name = device_data["device_name"]
            ospf_config = device_data["ospf_config"]
            address_family = device_data.get("address_family", "IPv4")
            
            # Get existing attached pool names for this OSPF device and address family
            # Support both old list format and new dict format
            route_pools_dict = ospf_config.get("route_pools", {})
            if isinstance(route_pools_dict, list):
                # Old format: route_pools is a list, convert to dict
                route_pools_dict = {"IPv4": route_pools_dict, "IPv6": []}
            elif not isinstance(route_pools_dict, dict):
                route_pools_dict = {}
            
            # Get pools for this specific address family
            attached_pool_names = route_pools_dict.get(address_family, [])
            if not isinstance(attached_pool_names, list):
                attached_pool_names = []
            
            # Create a mock OSPF config to pass to dialog for address family filtering
            ospf_config_for_dialog = {
                "ipv4_enabled": address_family == "IPv4",
                "ipv6_enabled": address_family == "IPv6"
            }
            
            # Open dialog (reuse BGP dialog as it's generic enough)
            from widgets.add_bgp_route_dialog import AttachRoutePoolsDialog
            dialog = AttachRoutePoolsDialog(self.parent, 
                                            device_name=f"{device_name} ({address_family})", 
                                            available_pools=available_pools,
                                            attached_pools=attached_pool_names,
                                            bgp_config=ospf_config_for_dialog)  # Pass OSPF config for address family filtering
            if dialog.exec_() != dialog.Accepted:
                return
            
            # Get selected pools
            selected_pools = dialog.get_attached_pools()
            
            # Save to OSPF config per address family
            if "route_pools" not in ospf_config or not isinstance(ospf_config["route_pools"], dict):
                # Initialize as dict if not already
                existing_list = ospf_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    ospf_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [], 
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    ospf_config["route_pools"] = {"IPv4": [], "IPv6": []}
            
            # Update only the selected address family's route pools
            ospf_config["route_pools"][address_family] = selected_pools
            
            # Mark device as needing apply
            device_data["device_info"]["_needs_apply"] = True
            
            # Save to session
            self.parent.main_window.save_session()
            
            # Refresh OSPF table to show updated pool assignments
            self.update_ospf_table()
            
            # Calculate total routes
            total_routes = 0
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
            
            print(f"[OSPF ROUTE POOLS] Attached {len(selected_pools)} pool(s) ({total_routes} routes) to OSPF device '{device_name}'")
            QMessageBox.information(self.parent, "Route Pools Attached", 
                                  f"Attached {len(selected_pools)} route pool(s) to OSPF device.\n\n"
                                  f"Device: {device_name}\n"
                                  f"Total routes to advertise: {total_routes}\n\n"
                                  f"Click 'Apply OSPF' to configure routes on server.")
            return
        
        # Multiple devices selected - show dialog for bulk attachment
        # Group selections by address family to show appropriate pools
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QDialogButtonBox, QCheckBox, QGroupBox
        
        # Determine which address families are in the selection
        address_families_in_selection = set()
        devices_by_family = {}
        for device_data in selected_devices:
            address_family = device_data.get("address_family", "IPv4")
            address_families_in_selection.add(address_family)
            if address_family not in devices_by_family:
                devices_by_family[address_family] = []
            devices_by_family[address_family].append(device_data)
        
        class BulkAttachRoutePoolsDialog(QDialog):
            def __init__(self, parent, selected_devices, available_pools, address_families):
                super().__init__(parent)
                self.selected_devices = selected_devices
                self.available_pools = available_pools
                self.address_families = address_families
                self.setWindowTitle("Attach Route Pools to Multiple OSPF Configurations")
                self.setFixedSize(650, 500)
                self.setup_ui()
            
            def setup_ui(self):
                layout = QVBoxLayout(self)
                
                # Selected devices info with address families
                devices_group = QGroupBox("Selected OSPF Configurations")
                devices_layout = QVBoxLayout(devices_group)
                
                # Group by device and address family
                from collections import defaultdict
                devices_by_family = defaultdict(list)
                for device_data in self.selected_devices:
                    device_name = device_data["device_name"]
                    address_family = device_data.get("address_family", "IPv4")
                    devices_by_family[device_name].append(address_family)
                
                devices_text_parts = []
                for device_name, families in sorted(devices_by_family.items()):
                    families_str = ", ".join(sorted(set(families)))
                    devices_text_parts.append(f"  • {device_name}: {families_str}")
                
                devices_text = f"Selected {len(self.selected_devices)} OSPF configuration(s):\n" + "\n".join(devices_text_parts)
                devices_label = QLabel(devices_text)
                devices_label.setWordWrap(True)
                devices_layout.addWidget(devices_label)
                layout.addWidget(devices_group)
                
                # Filter pools by address families in selection
                filtered_pools = []
                for pool in self.available_pools:
                    pool_af = pool.get("address_family", "").lower()
                    if not pool_af:
                        # Detect from subnet
                        subnet = pool.get("subnet", "")
                        pool_af = "ipv6" if ":" in subnet else "ipv4"
                    
                    # Map to OSPF format (IPv4/IPv6)
                    pool_af_ospf = "IPv4" if pool_af == "ipv4" else "IPv6"
                    
                    # Only include pools matching the selected address families
                    if pool_af_ospf in self.address_families:
                        filtered_pools.append(pool)
                
                # Available pools (filtered by address family)
                pools_group = QGroupBox(f"Available Route Pools (for {', '.join(sorted(self.address_families))})")
                pools_layout = QVBoxLayout(pools_group)
                
                if not filtered_pools:
                    no_pools_label = QLabel(f"No route pools available for {', '.join(sorted(self.address_families))}.\n\n"
                                          f"Please create pools matching these address families first.")
                    no_pools_label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
                    no_pools_label.setAlignment(Qt.AlignCenter)
                    pools_layout.addWidget(no_pools_label)
                    self.pools_list = None  # Mark as None so we know there are no pools
                else:
                    self.pools_list = QListWidget()
                    self.pools_list.setSelectionMode(QListWidget.MultiSelection)
                    
                    for pool in filtered_pools:
                        pool_af = pool.get("address_family", "").lower()
                        if not pool_af:
                            subnet = pool.get("subnet", "")
                            pool_af = "ipv6" if ":" in subnet else "ipv4"
                        pool_af_display = pool_af.upper()
                        
                        pool_item = f"{pool['name']} - {pool['subnet']} ({pool['count']} routes) [{pool_af_display}]"
                        self.pools_list.addItem(pool_item)
                    
                    pools_layout.addWidget(self.pools_list)
                
                layout.addWidget(pools_group)
                
                # Summary
                self.summary_label = QLabel()
                self.summary_label.setStyleSheet("background: #e8f4f8; padding: 10px; border-radius: 3px;")
                if self.pools_list is not None:
                    self.pools_list.itemSelectionChanged.connect(self.update_summary)
                self.update_summary()
                layout.addWidget(self.summary_label)
                
                # Buttons
                button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                # Allow OK even if no pools available - user can deselect all to remove pools
                # Only disable if pools_list is None (no pools exist for address families)
                if self.pools_list is None:
                    button_box.button(QDialogButtonBox.Ok).setEnabled(False)
                    button_box.button(QDialogButtonBox.Ok).setToolTip("No route pools available for selected address families")
                else:
                    # Enable OK button - user can deselect all to remove pools
                    button_box.button(QDialogButtonBox.Ok).setEnabled(True)
                    button_box.button(QDialogButtonBox.Ok).setToolTip("Click OK to attach selected pools (or deselect all to remove pools)")
                button_box.accepted.connect(self.accept)
                button_box.rejected.connect(self.reject)
                layout.addWidget(button_box)
            
            def update_summary(self):
                """Update summary with selected pools info."""
                if self.pools_list is None:
                    self.summary_label.setText("No pools available")
                    return
                
                selected_items = self.pools_list.selectedItems()
                selected_count = len(selected_items)
                total_routes = 0
                
                for item in selected_items:
                    # Extract pool name and count
                    text = item.text()
                    parts = text.split(" (")
                    if len(parts) >= 2:
                        count_part = parts[1].split(" routes")[0]
                        try:
                            total_routes += int(count_part)
                        except:
                            pass
                
                if selected_count == 0:
                    self.summary_label.setText("No pools selected (deselect all to remove pools)")
                else:
                    self.summary_label.setText(f"✅ Selected {selected_count} pool(s) → Total {total_routes} routes to advertise")
            
            def get_selected_pools(self):
                if self.pools_list is None:
                    return []
                
                selected_items = self.pools_list.selectedItems()
                selected_pool_names = []
                for item in selected_items:
                    pool_name = item.text().split(" - ")[0]
                    selected_pool_names.append(pool_name)
                return selected_pool_names
        
        # Open bulk dialog
        dialog = BulkAttachRoutePoolsDialog(self.parent, selected_devices, available_pools, address_families_in_selection)
        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get selected pools from dialog
        selected_pools = dialog.get_selected_pools()
        
        # Allow empty selection - this means user wants to remove/detach all pools
        # Only show warning if no pools were available at all (pools_list was None)
        if not selected_pools:
            # Check if pools were available but user just didn't select any
            if dialog.pools_list is None:
                # No pools available for the selected address families
                address_families_str = ", ".join(sorted(address_families_in_selection))
                QMessageBox.warning(self.parent, "No Pools Available", 
                                  f"No route pools are available for {address_families_str}.\n\n"
                                  f"Please create pools matching these address families first.")
                return
            # User intentionally deselected all - this means remove all pools
            # Proceed to clear all pools for selected configurations
            removed_count = 0
            ipv4_removed = 0
            ipv6_removed = 0
            
            for device_data in selected_devices:
                device_name = device_data["device_name"]
                ospf_config = device_data["ospf_config"]
                address_family = device_data.get("address_family", "IPv4")
                
                # Initialize route_pools as dict if needed
                if "route_pools" not in ospf_config or not isinstance(ospf_config["route_pools"], dict):
                    existing_list = ospf_config.get("route_pools", [])
                    if isinstance(existing_list, list):
                        ospf_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [], 
                                                       "IPv6": existing_list if address_family == "IPv6" else []}
                    else:
                        ospf_config["route_pools"] = {"IPv4": [], "IPv6": []}
                
                # Check if there were pools before clearing
                existing_pools = ospf_config["route_pools"].get(address_family, [])
                if existing_pools:
                    removed_count += 1
                    if address_family == "IPv4":
                        ipv4_removed += 1
                    else:
                        ipv6_removed += 1
                    
                    # Clear pools for this address family
                    ospf_config["route_pools"][address_family] = []
                    device_data["device_info"]["_needs_apply"] = True
            
            if removed_count > 0:
                # Save to session
                self.parent.main_window.save_session()
                # Refresh OSPF table
                self.update_ospf_table()
                
                removed_parts = []
                if ipv4_removed > 0:
                    removed_parts.append(f"IPv4: {ipv4_removed} configuration(s)")
                if ipv6_removed > 0:
                    removed_parts.append(f"IPv6: {ipv6_removed} configuration(s)")
                
                removed_text = "\n".join(removed_parts) if removed_parts else "No configurations"
                
                print(f"[OSPF ROUTE POOLS] Removed all pools from {removed_count} OSPF configuration(s): {removed_text}")
                QMessageBox.information(self.parent, "Route Pools Removed", 
                                      f"Successfully removed all route pools from {removed_count} OSPF configuration(s):\n\n"
                                      f"{removed_text}\n\n"
                                      f"Click 'Apply OSPF' to update configuration on server.")
            else:
                # No pools were attached to begin with
                QMessageBox.information(self.parent, "No Pools to Remove", 
                                      "No route pools were attached to the selected configurations.")
            return
        
        # Filter pools by address family and apply separately
        # Group pools by address family
        pools_by_af = {"IPv4": [], "IPv6": []}
        for pool_name in selected_pools:
            # Find pool in available pools to determine address family
            for pool in available_pools:
                if pool["name"] == pool_name:
                    pool_af = pool.get("address_family", "").lower()
                    if not pool_af:
                        subnet = pool.get("subnet", "")
                        pool_af = "ipv6" if ":" in subnet else "ipv4"
                    pool_af_ospf = "IPv4" if pool_af == "ipv4" else "IPv6"
                    pools_by_af[pool_af_ospf].append(pool_name)
                    break
        
        # Apply pools to each device based on their address family
        total_devices = 0
        total_routes = 0
        devices_by_name = {}  # Group by device name to handle multiple address families
        ipv4_count = 0
        ipv6_count = 0
        
        for device_data in selected_devices:
            device_name = device_data["device_name"]
            ospf_config = device_data["ospf_config"]
            address_family = device_data.get("address_family", "IPv4")
            
            # Get pools for this address family
            pools_for_this_af = pools_by_af.get(address_family, [])
            
            if not pools_for_this_af:
                # No pools matching this address family - skip this configuration
                # This can happen if user selected only IPv4 pools but we have an IPv6 config selected
                # or vice versa - this is expected behavior
                print(f"[OSPF ROUTE POOLS] Skipping {device_name} ({address_family}) - no pools selected for this address family")
                continue
            
            # Initialize route_pools as dict if needed
            if "route_pools" not in ospf_config or not isinstance(ospf_config["route_pools"], dict):
                existing_list = ospf_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    ospf_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [], 
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    ospf_config["route_pools"] = {"IPv4": [], "IPv6": []}
            
            # Update only the selected address family's route pools
            ospf_config["route_pools"][address_family] = pools_for_this_af
            
            # Mark device as needing apply
            device_data["device_info"]["_needs_apply"] = True
            
            # Track unique devices and address families
            if device_name not in devices_by_name:
                devices_by_name[device_name] = True
                total_devices += 1
            
            if address_family == "IPv4":
                ipv4_count += 1
            else:
                ipv6_count += 1
            
            # Calculate routes for this address family
            for pool_name in pools_for_this_af:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
        
        # Save to session
        self.parent.main_window.save_session()
        
        # Refresh OSPF table to show updated pool assignments
        self.update_ospf_table()
        
        # Build summary message
        summary_parts = []
        if ipv4_count > 0:
            ipv4_pools = pools_by_af.get("IPv4", [])
            if ipv4_pools:
                summary_parts.append(f"IPv4: {len(ipv4_pools)} pool(s) to {ipv4_count} configuration(s)")
        if ipv6_count > 0:
            ipv6_pools = pools_by_af.get("IPv6", [])
            if ipv6_pools:
                summary_parts.append(f"IPv6: {len(ipv6_pools)} pool(s) to {ipv6_count} configuration(s)")
        
        if not summary_parts:
            QMessageBox.warning(self.parent, "No Pools Attached", 
                              "No route pools were attached.\n\n"
                              "Please ensure you selected pools matching the address families of the selected configurations.")
            return
        
        summary_text = "\n".join(summary_parts)
        
        print(f"[OSPF ROUTE POOLS] Attached pools to {total_devices} OSPF device(s): {summary_text}")
        QMessageBox.information(self.parent, "Route Pools Attached", 
                              f"Successfully attached route pools to {total_devices} OSPF configuration(s):\n\n"
                              f"{summary_text}\n\n"
                              f"Total routes to advertise: {total_routes}\n\n"
                              f"Click 'Apply OSPF' to configure routes on server.")
    
    def prompt_detach_route_pools(self):
        """Detach route pools from selected OSPF configurations."""
        # Get selection from OSPF table (not devices table)
        selected_items = self.parent.ospf_table.selectedItems()
        if not selected_items:
            # No rows selected - select all rows
            total_rows = self.parent.ospf_table.rowCount()
            if total_rows > 0:
                self.parent.ospf_table.selectAll()
                print(f"[OSPF TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self.parent, "No OSPF Devices", "No OSPF devices are configured.")
                return
        
        # Collect all selected OSPF devices with their address families
        selected_devices = []
        processed_devices = set()
        
        for item in selected_items:
            row = item.row()
            device_name = self.parent.ospf_table.item(row, 0).text()  # Device column
            neighbor_type_item = self.parent.ospf_table.item(row, 3)  # Neighbor Type column (IPv4 or IPv6)
            neighbor_type = neighbor_type_item.text() if neighbor_type_item else "IPv4"  # Default to IPv4
            
            # Clean device name - remove any suffixes like "(Pending Removal)"
            clean_device_name = device_name.split(" (")[0].strip()
            if clean_device_name != device_name:
                device_name = clean_device_name
            
            # Create unique key for device + address family
            device_key = f"{device_name}:{neighbor_type}"
            if device_key in processed_devices:
                continue
            processed_devices.add(device_key)
            
            # Find device in all_devices using safe helper
            device_info = self.parent._find_device_by_name(device_name)
            
            if not device_info:
                print(f"[OSPF ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue
            
            # Ensure device_info is a dictionary - handle list case
            if not isinstance(device_info, dict):
                print(f"[OSPF ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                # Try to extract dict from list if it's a list
                if isinstance(device_info, list) and len(device_info) > 0:
                    print(f"[OSPF ROUTE POOLS] Attempting to extract dict from list...")
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        print(f"[OSPF ROUTE POOLS] Could not extract dict from list for '{device_name}'")
                        continue
                else:
                    continue
            
            # Final check - ensure device_info is now a dict
            if not isinstance(device_info, dict):
                print(f"[OSPF ROUTE POOLS] Final check failed: device_info is still not a dict for '{device_name}'")
                continue
            
            # Get OSPF config for this device
            # Check if OSPF is in the protocols list
            protocols = device_info.get("protocols", [])
            if isinstance(protocols, str):
                try:
                    import json
                    protocols = json.loads(protocols)
                except:
                    protocols = []
            
            if not isinstance(protocols, list) or "OSPF" not in protocols:
                print(f"[OSPF ROUTE POOLS] Warning: Device '{device_name}' does not have OSPF configured")
                continue
            
            # Get the actual OSPF configuration
            ospf_config = device_info.get("ospf_config", {})
            if not ospf_config:
                print(f"[OSPF ROUTE POOLS] Warning: Device '{device_name}' does not have OSPF configuration")
                continue
            
            # Check if this address family has any route pools attached
            route_pools_data = ospf_config.get("route_pools", {})
            has_pools = False
            if isinstance(route_pools_data, dict):
                pools_for_family = route_pools_data.get(neighbor_type, [])
                has_pools = bool(pools_for_family and len(pools_for_family) > 0)
            elif isinstance(route_pools_data, list):
                # Old format - only check for IPv4
                has_pools = bool(neighbor_type == "IPv4" and route_pools_data and len(route_pools_data) > 0)
            
            if not has_pools:
                # Skip devices/configurations that don't have pools attached
                continue
            
            selected_devices.append({
                "device_name": device_name,
                "device_info": device_info,
                "ospf_config": ospf_config,
                "address_family": neighbor_type  # Store address family for this selection
            })
        
        if not selected_devices:
            QMessageBox.information(self.parent, "No Route Pools", 
                                  "No route pools are attached to the selected OSPF configurations.")
            return
        
        # Ask for confirmation
        if len(selected_devices) == 1:
            device_data = selected_devices[0]
            device_name = device_data["device_name"]
            address_family = device_data.get("address_family", "IPv4")
            
            reply = QMessageBox.question(self.parent, "Detach Route Pools", 
                                        f"Detach all route pools from {device_name} ({address_family})?\n\n"
                                        f"This will remove all attached route pools for this configuration.",
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
        else:
            # Group by device and address family for summary
            from collections import defaultdict
            devices_by_family = defaultdict(list)
            for device_data in selected_devices:
                device_name = device_data["device_name"]
                address_family = device_data.get("address_family", "IPv4")
                devices_by_family[device_name].append(address_family)
            
            summary_parts = []
            for device_name, families in sorted(devices_by_family.items()):
                families_str = ", ".join(sorted(set(families)))
                summary_parts.append(f"  • {device_name}: {families_str}")
            
            summary_text = "\n".join(summary_parts)
            
            reply = QMessageBox.question(self.parent, "Detach Route Pools", 
                                        f"Detach all route pools from {len(selected_devices)} OSPF configuration(s)?\n\n"
                                        f"Selected configurations:\n{summary_text}\n\n"
                                        f"This will remove all attached route pools for these configurations.",
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Detach pools from all selected configurations
        total_detached = 0
        ipv4_count = 0
        ipv6_count = 0
        
        for device_data in selected_devices:
            device_name = device_data["device_name"]
            ospf_config = device_data["ospf_config"]
            address_family = device_data.get("address_family", "IPv4")
            
            # Initialize route_pools as dict if needed
            if "route_pools" not in ospf_config or not isinstance(ospf_config["route_pools"], dict):
                existing_list = ospf_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    ospf_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [], 
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    ospf_config["route_pools"] = {"IPv4": [], "IPv6": []}
            
            # Clear route pools for this address family only
            if address_family in ospf_config["route_pools"]:
                pools_count = len(ospf_config["route_pools"][address_family])
                ospf_config["route_pools"][address_family] = []
                total_detached += 1
                
                if address_family == "IPv4":
                    ipv4_count += 1
                else:
                    ipv6_count += 1
                
                print(f"[OSPF ROUTE POOLS] Detached {pools_count} pool(s) from {device_name} ({address_family})")
            
            # Mark device as needing apply
            device_data["device_info"]["_needs_apply"] = True
        
        # Save to session
        self.parent.main_window.save_session()
        
        # Refresh OSPF table to show updated pool assignments
        self.update_ospf_table()
        
        # Build summary message
        summary_parts = []
        if ipv4_count > 0:
            summary_parts.append(f"IPv4: {ipv4_count} configuration(s)")
        if ipv6_count > 0:
            summary_parts.append(f"IPv6: {ipv6_count} configuration(s)")
        
        summary_text = "\n".join(summary_parts) if summary_parts else "No configurations"
        
        print(f"[OSPF ROUTE POOLS] Detached route pools from {total_detached} OSPF configuration(s): {summary_text}")
        QMessageBox.information(self.parent, "Route Pools Detached", 
                              f"Successfully detached route pools from {total_detached} OSPF configuration(s):\n\n"
                              f"{summary_text}\n\n"
                              f"Click 'Apply OSPF' to update configuration on server.")
    
    def apply_ospf_configurations(self):
        """Apply OSPF configurations to the server for selected OSPF table rows."""
        server_url = self.parent.get_server_url()
        if not server_url:
            QMessageBox.critical(self.parent, "No Server", "No server selected.")
            return

        # Get selected rows from the OSPF table
        # CRITICAL: Use selectedRanges() to get actual selected rows, not all selected items
        # This ensures we only process explicitly selected rows, not all cells in those rows
        selected_ranges = self.parent.ospf_table.selectedRanges()
        selected_rows_set = set()
        
        # Collect unique row indices from selected ranges
        for range_obj in selected_ranges:
            top_row = range_obj.topRow()
            bottom_row = range_obj.bottomRow()
            for row in range(top_row, bottom_row + 1):
                selected_rows_set.add(row)
        
        # If no rows are explicitly selected, show message
        if not selected_rows_set:
            QMessageBox.information(self.parent, "No Selection", 
                                  "Please select OSPF table rows to apply configuration.")
            return
        
        # Handle both OSPF application and removal
        devices_to_apply_ospf = []  # Devices that need OSPF configuration applied
        devices_to_remove_ospf = []  # Devices that need OSPF configuration removed
        
        # Track selected rows with their address family (IPv4/IPv6)
        selected_rows = {}  # {device_name: {row_index, protocol_type}}
        for row in selected_rows_set:
            device_name_item = self.parent.ospf_table.item(row, 0)  # Device column
            if not device_name_item:
                continue
            device_name = device_name_item.text()
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
                                
                                # Save the configuration to device before applying
                                # This ensures any edits made via Edit OSPF dialog are saved
                                if device.get("_needs_apply"):
                                    # IMPORTANT: Get the latest ospf_config from device_info to ensure we have all updates
                                    # The ospf_config might have been updated in memory but not yet saved
                                    latest_ospf_config = device.get("ospf_config", {})
                                    if latest_ospf_config and isinstance(latest_ospf_config, dict):
                                        # Use the latest config from device_info (which has the updates from Edit dialog)
                                        # This ensures we're using the config that was updated in memory
                                        ospf_config = latest_ospf_config.copy()
                                    
                                    # Update the device protocol configuration to save changes
                                    # This will merge properly and preserve area_id_ipv4 and area_id_ipv6
                                    self.parent._update_device_protocol(device_name, "OSPF", ospf_config)
                                    
                                    # Clear the flag
                                    device["_needs_apply"] = False
                                    
                                    # CRITICAL: Ensure device_info has the updated config after _update_device_protocol
                                    # Re-read from device to get the merged config
                                    for iface2, devices2 in self.parent.main_window.all_devices.items():
                                        for device2 in devices2:
                                            if device2.get("Device Name") == device_name:
                                                updated_ospf_config = device2.get("ospf_config", {})
                                                if updated_ospf_config:
                                                    device["ospf_config"] = updated_ospf_config
                                                    ospf_config = updated_ospf_config.copy()
                                                break
                                        if device.get("ospf_config"):
                                            break
                        else:
                            # Device was in OSPF table but no longer has OSPF config - needs removal
                            devices_to_remove_ospf.append(device)
                        break

        # Check if we have any work to do
        if not devices_to_apply_ospf and not devices_to_remove_ospf:
            QMessageBox.information(self.parent, "No OSPF Changes", 
                                  "No OSPF configurations to apply or remove.")
            return

        # CRITICAL: Run OSPF apply operations in background thread to prevent UI blocking
        # Use QThread to handle blocking network requests asynchronously
        from PyQt5.QtCore import QThread, pyqtSignal
        
        class ApplyOSPFWorker(QThread):
            finished = pyqtSignal(dict)  # Emit results dict when done
            
            def __init__(self, server_url, devices_to_apply_ospf, devices_to_remove_ospf, parent_handler):
                super().__init__()
                self.server_url = server_url
                self.devices_to_apply_ospf = devices_to_apply_ospf
                self.devices_to_remove_ospf = devices_to_remove_ospf
                self.parent_handler = parent_handler
            
            def run(self):
                """Run OSPF apply operations in background thread."""
                results = {
                    "success_count": 0,
                    "failed_devices": [],
                    "removal_success_count": 0,
                    "removal_failed_devices": []
                }
                
                # Handle OSPF application
                for device_info in self.devices_to_apply_ospf:
                    device_name = device_info.get("Device Name", "Unknown")
                    device_id = device_info.get("device_id")
                    
                    if not device_id:
                        results["failed_devices"].append(f"{device_name}: Missing device ID")
                        continue
                        
                    try:
                        # Prepare OSPF configuration payload
                        # CRITICAL: Get the latest ospf_config from the device in all_devices
                        # This ensures we have the most up-to-date config including any inline edits
                        # First, try to get it from device_info (which might be a copy)
                        ospf_config = device_info.get("ospf_config", {}).copy()
                        
                        # Then, re-read from all_devices to ensure we have the latest config
                        # This is important because inline edits update the device in all_devices
                        for iface_check, devices_check in self.parent_handler.parent.main_window.all_devices.items():
                            for device_check in devices_check:
                                if device_check.get("Device Name") == device_name:
                                    latest_ospf_config = device_check.get("ospf_config", {})
                                    if latest_ospf_config and isinstance(latest_ospf_config, dict):
                                        # Use the latest config from all_devices
                                        ospf_config = latest_ospf_config.copy()
                                    break
                            # Check if we found the device
                            for device_check in devices_check:
                                if device_check.get("Device Name") == device_name:
                                    break
                            else:
                                continue
                            break
                        
                        # CRITICAL: When applying only one address family, we need to preserve the other family's enabled status
                        # Load current OSPF config from database to ensure we have the latest ipv4_enabled/ipv6_enabled values
                        # This prevents accidentally disabling the other address family when applying only one
                        # Always load from database to ensure we have the correct enabled flags, even if in-memory config has stale values
                        try:
                            import requests
                            db_response = requests.get(f"{self.server_url}/api/device/database/devices/{device_id}", timeout=5)
                            if db_response.status_code == 200:
                                db_device_data = db_response.json()
                                db_ospf_config = db_device_data.get("ospf_config", {})
                                if db_ospf_config and isinstance(db_ospf_config, dict):
                                    # CRITICAL: Always load enabled flags from database to ensure we have the correct values
                                    # This is especially important when applying route pool changes or only one address family
                                    if "ipv4_enabled" in db_ospf_config:
                                        ospf_config["ipv4_enabled"] = db_ospf_config["ipv4_enabled"]
                                    if "ipv6_enabled" in db_ospf_config:
                                        ospf_config["ipv6_enabled"] = db_ospf_config["ipv6_enabled"]
                                    print(f"[OSPF APPLY DEBUG] Loaded enabled flags from database: ipv4_enabled={ospf_config.get('ipv4_enabled')}, ipv6_enabled={ospf_config.get('ipv6_enabled')}")
                        except Exception as e:
                            # If database load fails, continue with in-memory config
                            print(f"[OSPF APPLY DEBUG] Could not load OSPF config from database: {e}")
                        
                        # CRITICAL: Filter ospf_config to only include fields relevant to selected address families
                        # This prevents sending area_id_ipv4 when only IPv6 is selected, and vice versa
                        selected_address_families = device_info.get("_selected_address_families", [])
                        if selected_address_families:
                            # Create a filtered config with only the fields relevant to selected address families
                            filtered_ospf_config = {}
                            
                            # Always include common fields - CRITICAL: Always include ipv4_enabled and ipv6_enabled
                            # to preserve the other address family's enabled status
                            # Also include route_pools to preserve route pool attachments when editing config
                            common_fields = ["router_id", "hello_interval", "dead_interval", "interface", 
                                           "graceful_restart", "route_pools"]
                            for field in common_fields:
                                if field in ospf_config:
                                    filtered_ospf_config[field] = ospf_config[field]
                            
                            # Always include P2P settings to preserve when editing config
                            if "p2p" in ospf_config:
                                filtered_ospf_config["p2p"] = ospf_config["p2p"]
                            if "p2p_ipv4" in ospf_config:
                                filtered_ospf_config["p2p_ipv4"] = ospf_config["p2p_ipv4"]
                            if "p2p_ipv6" in ospf_config:
                                filtered_ospf_config["p2p_ipv6"] = ospf_config["p2p_ipv6"]
                            
                            # CRITICAL: Always include ipv4_enabled and ipv6_enabled to preserve both address families
                            # Load from ospf_config (which may have been updated from database above)
                            if "ipv4_enabled" in ospf_config:
                                filtered_ospf_config["ipv4_enabled"] = ospf_config["ipv4_enabled"]
                            if "ipv6_enabled" in ospf_config:
                                filtered_ospf_config["ipv6_enabled"] = ospf_config["ipv6_enabled"]
                            
                            # Include address-family-specific fields only for selected families
                            if "IPv4" in selected_address_families:
                                # Include IPv4-specific fields
                                if "graceful_restart_ipv4" in ospf_config:
                                    filtered_ospf_config["graceful_restart_ipv4"] = ospf_config["graceful_restart_ipv4"]
                            
                            if "IPv6" in selected_address_families:
                                # Include IPv6-specific fields
                                if "graceful_restart_ipv6" in ospf_config:
                                    filtered_ospf_config["graceful_restart_ipv6"] = ospf_config["graceful_restart_ipv6"]
                            
                            # Use the filtered config
                            # Note: route_pools is already included in common_fields above
                            ospf_config = filtered_ospf_config
                            
                            # Add a flag to indicate which address families should be configured in this apply
                            # The server will use this to only configure the selected families without removing others
                            ospf_config["_apply_address_families"] = selected_address_families
                            # Preserve the existing enabled flags to prevent removal of other address family
                            # Don't modify ipv4_enabled or ipv6_enabled - keep them as they are
                        else:
                            # If no specific address families selected, send full config (for backward compatibility)
                            pass
                        
                        # DEBUG: Log what we're sending to verify area IDs are included
                        print(f"[OSPF APPLY DEBUG] Sending ospf_config for {device_name}: area_id_ipv4={ospf_config.get('area_id_ipv4')}, area_id_ipv6={ospf_config.get('area_id_ipv6')}, area_id={ospf_config.get('area_id')}, selected_afs={selected_address_families}")
                        
                        # Get route pools from ospf_config - support both old list and new dict format
                        route_pools_data = ospf_config.get("route_pools", {})
                        if isinstance(route_pools_data, list):
                            # Old format: convert to dict format
                            route_pools_data = {"IPv4": route_pools_data, "IPv6": []}
                        elif not isinstance(route_pools_data, dict):
                            route_pools_data = {"IPv4": [], "IPv6": []}
                        
                        # Get all route pools from main window for generation
                        all_route_pools = getattr(self.parent_handler.parent.main_window, 'bgp_route_pools', [])
                        
                        payload = {
                            "device_id": device_id,
                            "device_name": device_name,
                            "interface": device_info.get("Interface", ""),
                            "vlan": device_info.get("VLAN", "0"),
                            "ipv4": device_info.get("IPv4", ""),
                            "ipv6": device_info.get("IPv6", ""),
                            "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                            "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                            "ospf_config": ospf_config,
                            "route_pools_per_area": {},  # Will be populated by server from ospf_config["route_pools"]
                            "all_route_pools": all_route_pools  # Include all route pools for generation
                        }
                        
                        # Send OSPF configuration to server
                        # Use longer timeout (30s) since OSPF configuration may take time
                        response = requests.post(f"{self.server_url}/api/device/ospf/configure", 
                                               json=payload, timeout=30)
                        
                        if response.status_code == 200:
                            results["success_count"] += 1
                            print(f"✅ OSPF configuration applied for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            results["failed_devices"].append(f"{device_name}: {error_msg}")
                            print(f"❌ Failed to apply OSPF for {device_name}: {error_msg}")
                            
                    except requests.exceptions.RequestException as e:
                        results["failed_devices"].append(f"{device_name}: Network error - {str(e)}")
                        print(f"❌ Network error applying OSPF for {device_name}: {str(e)}")
                    except Exception as e:
                        results["failed_devices"].append(f"{device_name}: {str(e)}")
                        print(f"❌ Error applying OSPF for {device_name}: {str(e)}")
                
                # Handle OSPF removal
                for device_info in self.devices_to_remove_ospf:
                    device_name = device_info.get("Device Name", "Unknown")
                    device_id = device_info.get("device_id")
                    
                    if not device_id:
                        results["removal_failed_devices"].append(f"{device_name}: Missing device ID")
                        continue
                        
                    try:
                        # Call OSPF cleanup endpoint to remove OSPF configuration
                        response = requests.post(f"{self.server_url}/api/ospf/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=30)
                        
                        if response.status_code == 200:
                            results["removal_success_count"] += 1
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
                            results["removal_failed_devices"].append(f"{device_name}: {error_msg}")
                            print(f"❌ Failed to remove OSPF for {device_name}: {error_msg}")
                            
                    except requests.exceptions.RequestException as e:
                        results["removal_failed_devices"].append(f"{device_name}: Network error - {str(e)}")
                        print(f"❌ Network error removing OSPF for {device_name}: {str(e)}")
                    except Exception as e:
                        results["removal_failed_devices"].append(f"{device_name}: {str(e)}")
                        print(f"❌ Error removing OSPF for {device_name}: {str(e)}")
                
                # Emit results when done
                self.finished.emit(results)
        
        # Show progress dialog while applying
        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog("Applying OSPF configurations...", "Cancel", 0, 0, self.parent)
        progress.setWindowModality(2)  # Qt.WindowModal
        progress.setCancelButton(None)  # Disable cancel button
        progress.setMinimumDuration(0)  # Show immediately
        progress.show()
        
        # Create and start worker thread
        worker = ApplyOSPFWorker(server_url, devices_to_apply_ospf, devices_to_remove_ospf, self)
        # CRITICAL: Set parent to ensure proper cleanup
        worker.setParent(self.parent)
        worker.finished.connect(lambda results: self._on_ospf_apply_finished(results, progress, devices_to_apply_ospf, devices_to_remove_ospf))
        worker.finished.connect(worker.deleteLater)  # Clean up worker when done
        worker.start()
        
        # Store worker reference to prevent garbage collection
        if not hasattr(self, '_ospf_apply_workers'):
            self._ospf_apply_workers = []
        self._ospf_apply_workers.append(worker)
        
        # Clean up finished workers
        # CRITICAL: Wrap isRunning() in try-except to handle deleted workers
        def is_worker_running(w):
            try:
                return w.isRunning()
            except RuntimeError:
                # Worker has been deleted, treat as not running
                return False
        
        self._ospf_apply_workers = [w for w in self._ospf_apply_workers if is_worker_running(w)]
        
        # Return early - results will be handled in _on_ospf_apply_finished
        return
    
    def _on_ospf_apply_finished(self, results, progress, devices_to_apply_ospf, devices_to_remove_ospf):
        """Handle OSPF apply completion (called from worker thread via signal)."""
        # Close progress dialog
        progress.close()
        
        # Clean up worker reference
        # CRITICAL: Wrap isRunning() in try-except to handle deleted workers
        if hasattr(self, '_ospf_apply_workers'):
            def is_worker_running(w):
                try:
                    return w.isRunning()
                except RuntimeError:
                    # Worker has been deleted, treat as not running
                    return False
            
            self._ospf_apply_workers = [w for w in self._ospf_apply_workers if is_worker_running(w)]
        
        # Extract results
        success_count = results["success_count"]
        failed_devices = results["failed_devices"]
        removal_success_count = results["removal_success_count"]
        removal_failed_devices = results["removal_failed_devices"]
        
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

        # CRITICAL: Defer database reload and table update to prevent UI blocking
        # Use QTimer to defer these operations to next event loop iteration
        from PyQt5.QtCore import QTimer
        import time
        
        def deferred_reload_and_update():
            """Defer database reload and table update to prevent UI blocking."""
            try:
                # Update OSPF table to reflect any changes
                # IMPORTANT: After applying to server, the server saves to database
                # We need to reload device data from database to get the latest saved config
                # But first, ensure our in-memory device data is updated with what we just applied
                # Then refresh the table which reads from all_devices
                
                # Reload device data from database for devices that were successfully applied
                # This ensures we have the latest config that the server saved
                # Use API endpoint instead of direct database access (client can't access server database file)
                server_url = self.parent.get_server_url(silent=True)
                if server_url:
                    try:
                        # Reload device data from database for all devices that were successfully applied
                        # requests is already imported at the top of the file
                        for device_info in devices_to_apply_ospf:
                            device_id = device_info.get("device_id")
                            if device_id:
                                try:
                                    # Get latest device data from database via API
                                    response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
                                    if response.status_code == 200:
                                        db_device = response.json()
                                        # DEBUG: Log what we got from database
                                        db_ospf_config = db_device.get("ospf_config", {})
                                        if isinstance(db_ospf_config, str):
                                            import json
                                            try:
                                                db_ospf_config = json.loads(db_ospf_config)
                                            except:
                                                db_ospf_config = {}
                                        device_name = device_info.get("Device Name", "")
                                        print(f"[OSPF APPLY DEBUG] Reloaded from database for {device_name}: area_id_ipv4={db_ospf_config.get('area_id_ipv4')}, area_id_ipv6={db_ospf_config.get('area_id_ipv6')}, area_id={db_ospf_config.get('area_id')}")
                                        
                                        # Update the device in all_devices with latest data from database
                                        for iface, devices in self.parent.main_window.all_devices.items():
                                            for device in devices:
                                                if device.get("Device Name") == device_name:
                                                    # Update ospf_config from database
                                                    if db_ospf_config:
                                                        device["ospf_config"] = db_ospf_config
                                                    break
                                            if device.get("Device Name") == device_name:
                                                break
                                except requests.exceptions.RequestException as e:
                                    print(f"[OSPF APPLY] Warning: Could not reload device data for {device_id} from API: {e}")
                    except Exception as e:
                        print(f"[OSPF APPLY] Warning: Could not reload device data from database: {e}")
                
                # Clear the _ospf_just_edited flag for all devices after apply
                # This allows the table to reload from database on next refresh
                # IMPORTANT: Mark devices as applied without blocking the UI thread
                # The delay is handled by the _ospf_just_applied flag which prevents reload for 5 seconds
                # This prevents the periodic check from reloading stale data immediately after apply
                current_time = time.time()
                
                for device_info in devices_to_apply_ospf:
                    device_name = device_info.get("Device Name", "")
                    for iface, devices in self.parent.main_window.all_devices.items():
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                # Clear both flags - edits are now applied
                                device.pop("_ospf_just_edited", None)
                                device.pop("_needs_apply", None)
                                # Mark device as just applied to prevent immediate reload
                                device["_ospf_just_applied"] = True
                                device["_ospf_apply_time"] = current_time
                                print(f"[OSPF APPLY DEBUG] Cleared _ospf_just_edited and _needs_apply flags for {device_name}, set _ospf_just_applied=True")
                                break
                        # Check if we found the device
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                break
                        else:
                            continue
                        break
                
                # Temporarily disconnect cellChanged signal to prevent issues during refresh
                try:
                    self.parent.ospf_table.cellChanged.disconnect()
                except:
                    pass  # Signal might not be connected
                
                # Defer table update to prevent UI blocking
                QTimer.singleShot(0, self.update_ospf_table)
            except Exception as e:
                print(f"[OSPF APPLY ERROR] Error in deferred reload and update: {e}")
        
        # Defer to next event loop iteration to prevent UI blocking
        QTimer.singleShot(0, deferred_reload_and_update)

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
        # IMPORTANT: Clear _ospf_just_applied flags before periodic check to allow reload
        # This ensures the periodic check can reload from database after apply has completed
        # BUT: Increase the delay to 5 seconds to ensure server has saved to database
        import time
        current_time = time.time()
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                # Clear _ospf_just_applied flag if it's been more than 5 seconds since apply
                # This prevents race conditions but allows periodic check to work
                # Increased from 2 to 5 seconds to ensure server has saved to database
                # Also clear _ospf_just_edited flag when clearing _ospf_just_applied
                # This ensures the reload can happen after apply is complete
                if device.get("_ospf_just_applied"):
                    apply_time = device.get("_ospf_apply_time", 0)
                    if current_time - apply_time > 5.0:
                        device.pop("_ospf_just_applied", None)
                        device.pop("_ospf_apply_time", None)
                        # Also clear _ospf_just_edited flag to allow reload
                        device.pop("_ospf_just_edited", None)
                        device.pop("_needs_apply", None)
        
        # Use QTimer.singleShot to defer table update and avoid blocking UI thread
        # This ensures the periodic check doesn't block the UI during table updates
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self.update_ospf_table)  # Defer to next event loop iteration
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

