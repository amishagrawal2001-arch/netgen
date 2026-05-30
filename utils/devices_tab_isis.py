"""ISIS-related functionality for DevicesTab.

This module contains all ISIS-specific methods extracted from devices_tab.py
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

logger = logging.getLogger(__name__)


class ISISHandler:
    """Handler class for ISIS-related functionality in DevicesTab."""
    
    def __init__(self, parent_tab):
        """Initialize ISIS handler with reference to parent DevicesTab.
        
        Args:
            parent_tab: The DevicesTab instance that owns this handler.
        """
        self.parent = parent_tab
    

    def setup_isis_subtab(self):
        """Setup the ISIS sub-tab with ISIS-specific functionality."""
        layout = QVBoxLayout(self.parent.isis_subtab)
        # Tight chrome — see BGP subtab for rationale.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # ISIS Neighbors Table with requested columns
        isis_headers = ["Device", "ISIS Status", "Neighbor Type", "Neighbor Hostname", "Interface", "ISIS Area", "Level", "ISIS Net", "System ID", "Hello Interval", "Multiplier"]
        self.parent.isis_table = QTableWidget(0, len(isis_headers))
        self.parent.isis_table.setHorizontalHeaderLabels(isis_headers)
        
        # Set column widths for better visibility
        self.parent.isis_table.setColumnWidth(0, 120)  # Device
        self.parent.isis_table.setColumnWidth(1, 100)  # ISIS Status
        self.parent.isis_table.setColumnWidth(2, 120)  # Neighbor Type
        self.parent.isis_table.setColumnWidth(3, 150)  # Neighbor Hostname
        self.parent.isis_table.setColumnWidth(4, 100)  # Interface
        self.parent.isis_table.setColumnWidth(5, 120)  # ISIS Area
        self.parent.isis_table.setColumnWidth(6, 80)   # Level
        self.parent.isis_table.setColumnWidth(7, 200)  # ISIS Net
        self.parent.isis_table.setColumnWidth(8, 120)  # System ID
        self.parent.isis_table.setColumnWidth(9, 100)  # Hello Interval
        self.parent.isis_table.setColumnWidth(10, 100)  # Multiplier
        
        # Enable inline editing for the ISIS table
        self.parent.isis_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.parent.isis_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect cell changed signal for inline editing
        self.parent.isis_table.cellChanged.connect(self.on_isis_table_cell_changed)
        
        # Section header removed — tab name + column headers carry it.
        layout.addWidget(self.parent.isis_table)

        # v0.2.88: empty-state placeholder.
        try:
            from widgets.empty_state_overlay import EmptyStateOverlay
            self.parent.isis_empty_state = EmptyStateOverlay(
                self.parent.isis_table,
                "No IS-IS neighbours configured.\n\n"
                "Add one with the Add button below, or configure "
                "IS-IS on a device via the main Devices table "
                "(right-click → Edit → enable IS-IS)."
            )
        except Exception:
            pass  # overlay is advisory; never block sub-tab render

        # IS-IS action bar — unified chrome with Devices + BGP + OSPF.
        from PyQt5.QtWidgets import QFrame
        from PyQt5.QtCore import Qt
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border-top: 1px solid #e5e7eb; border-radius: 0; }"
        )
        isis_controls = QHBoxLayout(action_bar)
        isis_controls.setAlignment(Qt.AlignLeft)
        isis_controls.setSpacing(6)
        isis_controls.setContentsMargins(6, 4, 6, 4)

        BTN_BASE = (
            "QPushButton {"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
            "QPushButton:pressed { background-color: #e2e8f0; }"
            "QPushButton:disabled { background-color: #f9fafb; border-color: #e5e7eb; }"
        )
        BTN_APPLY = (
            "QPushButton {"
            "  border: 1px solid #2563eb;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  color: #1d4ed8;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #eff6ff; border-color: #1d4ed8; }"
            "QPushButton:pressed { background-color: #dbeafe; }"
        )
        BTN_W, BTN_H, ICON_PX = 28, 24, 14

        def load_icon(filename: str) -> QIcon:
            from utils.qicon_loader import qicon
            return qicon("resources", f"icons/{filename}")

        def _isis_btn(icon_name, tooltip, style=BTN_BASE):
            b = QPushButton()
            if icon_name:
                b.setIcon(load_icon(icon_name))
                b.setIconSize(QSize(ICON_PX, ICON_PX))
            b.setFixedSize(BTN_W, BTN_H)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(style)
            return b

        # Config group (left)
        self.parent.add_isis_button     = _isis_btn("add.png",    "Add IS-IS")
        self.parent.edit_isis_button    = _isis_btn("edit.png",   "Edit ISIS Configuration")
        self.parent.delete_isis_button  = _isis_btn("remove.png", "Delete ISIS Configuration")

        # Runtime group (right)
        self.parent.apply_isis_button   = _isis_btn("apply.png",   "Apply ISIS configurations to server", style=BTN_APPLY)
        self.parent.isis_start_button   = _isis_btn("start.png",   "Start IS-IS")
        self.parent.isis_stop_button    = _isis_btn("stop.png",    "Stop IS-IS")
        self.parent.isis_refresh_button = _isis_btn("refresh.png", "Refresh ISIS Status")

        # Wire signals
        self.parent.add_isis_button.clicked.connect(self.prompt_add_isis)
        self.parent.edit_isis_button.clicked.connect(self.prompt_edit_isis)
        self.parent.delete_isis_button.clicked.connect(self.prompt_delete_isis)
        self.parent.apply_isis_button.clicked.connect(self.apply_isis_configurations)
        self.parent.isis_start_button.clicked.connect(self.start_isis_protocol)
        self.parent.isis_stop_button.clicked.connect(self.stop_isis_protocol)
        self.parent.isis_refresh_button.clicked.connect(self.refresh_isis_status)

        for b in (self.parent.add_isis_button, self.parent.edit_isis_button,
                  self.parent.delete_isis_button):
            isis_controls.addWidget(b)

        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        isis_controls.addSpacing(4)
        isis_controls.addWidget(sep)
        isis_controls.addSpacing(4)

        for b in (self.parent.apply_isis_button, self.parent.isis_start_button,
                  self.parent.isis_stop_button, self.parent.isis_refresh_button):
            isis_controls.addWidget(b)

        isis_controls.addStretch(1)
        layout.addWidget(action_bar)


    def prompt_edit_isis(self):
        """Edit ISIS configuration for selected device."""
        selected_items = self.parent.isis_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select an ISIS configuration to edit.")
            return

        # Get unique rows from selection
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if len(selected_rows) > 1:
            QMessageBox.warning(self.parent, "Multiple Selection", "Please select only one ISIS configuration to edit.")
            return
        
        row = list(selected_rows)[0]
        device_name = self.parent.isis_table.item(row, 0).text()  # Device column
        
        # Find the device in all_devices using safe helper
        device_info = self.parent._find_device_by_name(device_name)
        
        # Check if ISIS is configured
        protocols = device_info.get("protocols", [])
        is_isis_configured = False
        if isinstance(protocols, list):
            # protocols is a list like ["OSPF", "BGP", "ISIS"]
            is_isis_configured = "ISIS" in protocols or "IS-IS" in protocols
        elif isinstance(protocols, dict):
            # Old format: protocols is a dict
            is_isis_configured = "IS-IS" in protocols or "ISIS" in protocols
        
        if not device_info or not is_isis_configured:
            QMessageBox.warning(self.parent, "No ISIS Configuration", f"No ISIS configuration found for device '{device_name}'.")
            return

        # Get current ISIS configuration
        # protocols is a list (e.g., ["OSPF", "BGP", "ISIS"]), not a dict
        # ISIS config is stored separately in isis_config or is_is_config
        current_isis = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})

        # Create dialog with current ISIS configuration in edit mode
        from widgets.add_isis_dialog import AddIsisDialog
        dialog = AddIsisDialog(self.parent, device_name, edit_mode=True, isis_config=current_isis)
        
        if dialog.exec_() != dialog.Accepted:
            return

        new_isis_config = dialog.get_values()
        
        # Update the device with new ISIS configuration
        if isinstance(device_info["protocols"], dict):
            device_info["protocols"]["IS-IS"] = new_isis_config
        else:
            device_info["isis_config"] = new_isis_config
        
        # Update the ISIS table
        self.update_isis_table()
        
        # Save session
        if hasattr(self.parent.main_window, "save_session"):
            self.parent.main_window.save_session()


    def prompt_delete_isis(self):
        """Delete ISIS configuration for selected device."""
        selected_items = self.parent.isis_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select an ISIS configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.parent.isis_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self.parent, "Confirm Deletion", 
                                   f"Are you sure you want to delete ISIS configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices using safe helper
        device_info = self.parent._find_device_by_name(device_name)
        
        if device_info and "protocols" in device_info and "IS-IS" in device_info["protocols"]:
            # Check if ISIS is already marked for removal
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            if isinstance(isis_config, dict) and isis_config.get("_marked_for_removal"):
                QMessageBox.information(self.parent, "Already Marked for Removal", 
                                      f"ISIS configuration for '{device_name}' is already marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")
                return
            
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove ISIS configuration from server first
                server_url = self.parent.get_server_url()
                if server_url:
                    try:
                        # Call server ISIS cleanup endpoint
                        response = requests.post(f"{server_url}/api/isis/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            logger.info(f"ISIS configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            logger.error(f"Server ISIS cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Network error removing ISIS from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    logger.warning("No server URL available, removing ISIS configuration locally only")
            
            # Mark ISIS for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if isinstance(device_info["protocols"], dict):
                device_info["protocols"]["IS-IS"] = {"_marked_for_removal": True}
            else:
                device_info["isis_config"] = {"_marked_for_removal": True}
            
            # Update the ISIS table to show the device as marked for removal
            self.update_isis_table()
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()
            
            QMessageBox.information(self.parent, "ISIS Configuration Marked for Removal", 
                                  f"ISIS configuration for '{device_name}' has been marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self.parent, "No ISIS Configuration", f"No ISIS configuration found for device '{device_name}'.")


    def apply_isis_configurations(self):
        """Apply ISIS configurations to the server for selected ISIS table rows."""
        server_url = self.parent.get_server_url()
        if not server_url:
            QMessageBox.critical(self.parent, "No Server", "No server selected.")
            return

        # Get selected rows from the ISIS table
        selected_items = self.parent.isis_table.selectedItems()
        selected_devices = []
        
        if selected_items:
            # Get unique device names from selected ISIS table rows
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.parent.isis_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find the devices in all_devices
            for device_name in selected_device_names:
                for iface, devices in self.parent.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            selected_devices.append(device)
                            break

        # Handle both ISIS application and removal
        devices_to_apply_isis = []  # Devices that need ISIS configuration applied
        devices_to_remove_isis = []  # Devices that need ISIS configuration removed
        
        if selected_items:
            # If ISIS table rows are selected, process only those devices
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.parent.isis_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find devices and determine if they need ISIS applied or removed
            for device_name in selected_device_names:
                for iface, devices in self.parent.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                            if isis_config:
                                if isis_config.get("_marked_for_removal"):
                                    # Device is marked for ISIS removal
                                    devices_to_remove_isis.append(device)
                                else:
                                    # Device needs ISIS configuration applied
                                    devices_to_apply_isis.append(device)
        else:
            # If no ISIS table rows are selected, process all devices with ISIS configurations
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                    if isis_config:
                        if isis_config.get("_marked_for_removal"):
                            devices_to_remove_isis.append(device)
                        else:
                            devices_to_apply_isis.append(device)

        # Apply ISIS configurations
        if devices_to_apply_isis:
            self._apply_isis_to_devices(devices_to_apply_isis, server_url)
        
        # Remove ISIS configurations
        if devices_to_remove_isis:
            self._remove_isis_from_devices(devices_to_remove_isis, server_url)


    def _apply_isis_to_devices(self, devices, server_url):
        """Apply ISIS configuration to the specified devices."""
        try:
            for device in devices:
                device_id = device.get("device_id")
                device_name = device.get("Device Name", "Unknown")
                # Resolve server URL per device based on its TG/interface selection
                per_device_server_url = self.parent._get_server_url_from_interface(device.get("Interface", "")) or server_url
                # Use the canonical key name for ISIS configuration
                isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                # Fallback: some legacy structures may store under protocols -> ISIS
                if not isis_config and isinstance(device.get("protocols"), dict):
                    proto = device.get("protocols", {})
                    isis_config = proto.get("ISIS", {}) or proto.get("isis", {})
                
                if not device_id or not isis_config:
                    continue
                
                # Prepare ISIS configuration data using the configure endpoint (similar to OSPF)
                isis_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": device.get("Interface", ""),
                    "vlan": device.get("VLAN", "0"),
                    "ipv4": device.get("IPv4", ""),
                    "ipv6": device.get("IPv6", ""),
                    "ipv4_gateway": device.get("IPv4 Gateway", ""),
                    "ipv6_gateway": device.get("IPv6 Gateway", ""),
                    "isis_config": isis_config
                }
                
                # Ensure per-device server URL exists
                if not per_device_server_url:
                    logger.info(f"[ISIS POST] No server URL resolved for device '{device_name}'. Skipping.")
                    continue

                post_url = f"{per_device_server_url}/api/device/isis/configure"
                # Client-side debug of outgoing request
                try:
                    logger.info(f"[ISIS POST] URL: {post_url}")
                    logger.info(f"[ISIS POST] Payload: {isis_data}")
                except Exception:
                    pass

                # Send ISIS configuration to server using configure endpoint
                try:
                    response = requests.post(post_url, json=isis_data, timeout=30)
                except Exception as e:
                    logger.error(f"[ISIS POST] Exception posting to {post_url}: {e}")
                    continue
                
                if response.status_code == 200:
                    logger.info(f"ISIS configuration applied to server for {device_name}")
                else:
                    try:
                        error_msg = response.json().get("error", response.text)
                    except Exception:
                        error_msg = response.text
                    logger.error(f"Failed to apply ISIS configuration for {device_name} (status {response.status_code}): {error_msg}")
            
            # Refresh ISIS table after applying configurations
            self.update_isis_table()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error applying ISIS configurations: {str(e)}")
            QMessageBox.critical(self.parent, "Network Error", f"Failed to apply ISIS configurations: {str(e)}")


    def _remove_isis_from_devices(self, devices, server_url):
        """Remove ISIS configuration from the specified devices."""
        try:
            for device in devices:
                device_id = device.get("device_id")
                device_name = device.get("Device Name", "Unknown")
                isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                
                if not device_id:
                    continue
                
                # Try to remove ISIS configuration from server first (for Docker-based devices)
                server_removal_success = False
                if server_url:
                    try:
                        # Prepare ISIS removal data
                        isis_data = {
                            "device_id": device_id,
                            "device_name": device_name,
                            "isis_config": isis_config
                        }
                        
                        # Send ISIS removal request to server
                        response = requests.post(f"{server_url}/api/device/isis/stop", 
                                               json=isis_data, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            logger.info(f"ISIS configuration removed from server for {device_name}")
                            server_removal_success = True
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            logger.error(f"Server ISIS removal failed for {device_name}: {error_msg}")
                            # Continue with local removal even if server fails
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Network error removing ISIS from server for {device_name}: {str(e)}")
                        # Continue with local removal even if server fails
                else:
                    logger.warning(f"No server URL available, removing ISIS configuration locally only for {device_name}")
                
                # Always remove ISIS configuration from device data (local removal)
                # This ensures the configuration is removed regardless of server status
                if isinstance(device.get("protocols"), dict):
                    device["protocols"].pop("IS-IS", None)
                    logger.info(f"ISIS configuration removed locally for {device_name} (dict format)")
                else:
                    device.pop("is_is_config", None)
                    # Remove IS-IS from protocols list
                    protocols = device.get("protocols", [])
                    if isinstance(protocols, list) and "IS-IS" in protocols:
                        protocols.remove("IS-IS")
                        logger.info(f"ISIS configuration removed locally for {device_name} (list format)")
            
            # Refresh ISIS table after removing configurations
            self.update_isis_table()
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()
            
        except Exception as e:
            logger.error(f"Error removing ISIS configurations: {str(e)}")
            QMessageBox.critical(self.parent, "Error", f"Failed to remove ISIS configurations: {str(e)}")


    def refresh_isis_status(self):
        """Refresh IS-IS neighbor status.

        First kicks a server-side force-check so the DB reflects live
        isisd state (Up / Init / Down, system-id, hold-time, etc.),
        then re-renders the table from the (now-fresh) DB. Without the
        force-check the button only re-displayed whatever the periodic
        monitor last wrote — same stale-cache UX bug we fixed for ARP.
        """
        try:
            import requests
            logger.info("[ISIS REFRESH] Refreshing ISIS status from database...")

            # Step 1 — server-side force-check. Non-fatal on failure;
            # we still re-render whatever's in the DB.
            try:
                server_url = self.parent.get_server_url(silent=True)
                if server_url:
                    fc = requests.post(f"{server_url}/api/isis/monitor/force-check", timeout=15)
                    if fc.status_code == 200:
                        logger.info("[ISIS REFRESH] force-check OK")
                    else:
                        logger.warning(f"[ISIS REFRESH] force-check returned HTTP {fc.status_code}")
            except Exception as exc:
                logger.warning(f"[ISIS REFRESH] force-check failed (will show cached state): {exc}")

            # Update the ISIS table which fetches status from database
            self.update_isis_table()
            logger.info("[ISIS REFRESH] ISIS status refreshed successfully")
        except Exception as e:
            logger.error(f"[ISIS REFRESH ERROR] Error refreshing ISIS status: {e}")


    def update_isis_table(self):
        """Update ISIS table with data from devices and ISIS status from database."""
        # Don't rebuild while the user has an inline editor open in this
        # table — IS-IS deliberately exposes editable cells (ISIS Net,
        # System ID, Hello Interval, Multiplier), and a rebuild would
        # discard an in-progress edit. The periodic ISIS monitor repaints
        # on its next pass once the editor closes; explicit refreshes run
        # with no editor open, so they're unaffected. (Same class of fix
        # as the Streams table.)
        try:
            from utils.qt_table_guard import table_has_open_editor
            if table_has_open_editor(self.parent.isis_table):
                return
        except Exception:
            pass

        # Block cellChanged while we rebuild — without this, every setItem fires
        # the on_isis_table_cell_changed handler, which interprets each populate
        # write as a user edit and triggers save_session(). With the periodic
        # ISIS monitor on (auto-started for any device with "IS-IS" in its
        # protocols list), this caused save_session to fire every 20s.
        signals_were_blocked = self.parent.isis_table.signalsBlocked()
        self.parent.isis_table.blockSignals(True)
        try:
            # Get selected interfaces from server_tree (same logic as device table)
            selected_interfaces = set()
            tree = self.parent.main_window.server_tree
            for item in tree.selectedItems():
                parent = item.parent()
                if parent:
                    # Extract TG ID from custom widget (QWidget with QLabel)
                    tg_id = None
                    tg_id_widget = tree.itemWidget(parent, 0)
                    if tg_id_widget:
                        tg_id_label = tg_id_widget.findChild(QLabel)
                        if tg_id_label:
                            tg_id = tg_id_label.text().strip()
                    
                    # Fallback: extract from server_interfaces using parent index
                    if not tg_id:
                        parent_index = tree.indexOfTopLevelItem(parent)
                        if parent_index >= 0 and hasattr(self.parent.main_window, "server_interfaces"):
                            if parent_index < len(self.parent.main_window.server_interfaces):
                                server = self.parent.main_window.server_interfaces[parent_index]
                                tg_id = f"TG {server.get('tg_id', '0')}"
                    
                    # If still no TG ID, try text(0) as last resort
                    if not tg_id:
                        tg_id = parent.text(0).strip()
                    
                    port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                    if tg_id and port_name:
                        selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
            
            # print(f"[DEBUG ISIS TABLE] Selected interfaces: {selected_interfaces}")
            # if not selected_interfaces:
            #     print(f"[DEBUG ISIS TABLE] No interfaces selected, showing all devices")
            
            self.parent.isis_table.setRowCount(0)
            
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
                    # Check if device has IS-IS protocol configured
                    device_protocols = device.get("protocols", [])
                    if isinstance(device_protocols, list) and ("IS-IS" in device_protocols or "ISIS" in device_protocols):
                        # New format: protocols is a list, config is in separate field
                        # Check both isis_config and is_is_config for backward compatibility
                        isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                    elif isinstance(device_protocols, dict) and "IS-IS" in device_protocols:
                        # Old format: protocols is a dict
                        isis_config = device_protocols["IS-IS"]
                    else:
                        continue  # Skip devices without IS-IS
                    
                    device_name = device.get("Device Name", "")
                    device_id = device.get("device_id", "")
                    
                    # Check if ISIS is marked for removal
                    is_marked_for_removal = isinstance(isis_config, dict) and isis_config.get("_marked_for_removal", False)
                    
                    # Debug logs disabled
                    
                    # Get ISIS status from database
                    isis_status_data = self.parent._get_isis_status_from_database(device_id)
                    
                    # Get ISIS configuration flags
                    # Default to True if not set, to ensure rows are shown (can be inferred from device IPs)
                    ipv4_enabled = isis_config.get("ipv4_enabled") if isis_config else None
                    if ipv4_enabled is None:
                        # Try to infer from device's IP addresses
                        if device.get("ipv4_address") or device.get("IPv4 Address"):
                            ipv4_enabled = True
                        else:
                            # Default to True to ensure rows are shown
                            ipv4_enabled = True
                    
                    ipv6_enabled = isis_config.get("ipv6_enabled") if isis_config else None
                    if ipv6_enabled is None:
                        # Try to infer from device's IP addresses
                        if device.get("ipv6_address") or device.get("IPv6 Address"):
                            ipv6_enabled = True
                        else:
                            # Default to True to ensure rows are shown
                            ipv6_enabled = True
                    
                    # Get device VLAN interface from ISIS config
                    device_interface = isis_config.get("interface", iface)
                    # If interface is not in config, try to construct from VLAN
                    if not device_interface or device_interface == iface:
                        device_vlan = device.get("VLAN", "0")
                        if device_vlan and device_vlan != "0":
                            device_interface = f"vlan{device_vlan}"
                        else:
                            device_interface = iface
                    
                    # Debug logs disabled
                    
                    # Create rows for each ISIS neighbor or device status
                    if isis_status_data and isis_status_data.get("neighbors") and not is_marked_for_removal:
                        # Get neighbors from database
                        neighbors = isis_status_data.get("neighbors", [])
                        
                        # Create separate rows for IPv4 and IPv6 (similar to OSPF)
                        protocols_to_show = []
                        if ipv4_enabled:
                            protocols_to_show.append("IPv4")
                        if ipv6_enabled:
                            protocols_to_show.append("IPv6")
                        
                        # If no protocols are explicitly enabled, show both or Unknown
                        if not protocols_to_show:
                            # Check if neighbor has IPv4 or IPv6 addresses
                            if neighbors:
                                neighbor = neighbors[0]
                                if neighbor.get("ipv4_address"):
                                    protocols_to_show.append("IPv4")
                                if neighbor.get("ipv6_global") or neighbor.get("ipv6_link_local"):
                                    protocols_to_show.append("IPv6")
                            if not protocols_to_show:
                                protocols_to_show = ["Unknown"]
                        
                        # Show each protocol type (IPv4/IPv6) as separate row
                        for protocol_type in protocols_to_show:
                            # Get neighbor info for this protocol type
                            neighbor = neighbors[0] if neighbors else {}
                            
                            # Determine ISIS status based on neighbor state
                            isis_status = neighbor.get("state", "Down")
                            if isis_status.lower() in ["up", "established"]:
                                isis_status_display = "Up"
                            elif isis_status.lower() in ["down"]:
                                isis_status_display = "Down"
                            else:
                                isis_status_display = isis_status
                            
                            # Get neighbor info based on protocol type
                            if protocol_type == "IPv4":
                                neighbor_addr = neighbor.get("ipv4_address", "N/A")
                            elif protocol_type == "IPv6":
                                neighbor_addr = neighbor.get("ipv6_global", neighbor.get("ipv6_link_local", "N/A"))
                            else:
                                neighbor_addr = "N/A"
                            
                            row = self.parent.isis_table.rowCount()
                            self.parent.isis_table.insertRow(row)
                            
                            # Device
                            self.parent.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                            
                            # ISIS Status (with icon)
                            self.parent.set_isis_status_icon(row, isis_status_display, f"ISIS {isis_status_display}")
                            
                            # Neighbor Type (IPv4 or IPv6)
                            self.parent.isis_table.setItem(row, 2, QTableWidgetItem(protocol_type))
                            
                            # Neighbor Hostname
                            neighbor_hostname = neighbor.get("system_id", neighbor.get("hostname", "N/A"))
                            self.parent.isis_table.setItem(row, 3, QTableWidgetItem(neighbor_hostname))
                            
                            # Interface - show device VLAN interface
                            self.parent.isis_table.setItem(row, 4, QTableWidgetItem(device_interface))
                            
                            # ISIS Area
                            area = neighbor.get("area", isis_config.get("area_id", ""))
                            self.parent.isis_table.setItem(row, 5, QTableWidgetItem(area))
                            
                            # Level
                            level = neighbor.get("level", isis_config.get("level", "Level-2"))
                            self.parent.isis_table.setItem(row, 6, QTableWidgetItem(level))
                            
                            # ISIS Net (editable)
                            isis_net = neighbor.get("net", isis_config.get("area_id", ""))
                            isis_net_item = QTableWidgetItem(isis_net)
                            isis_net_item.setFlags(isis_net_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 7, isis_net_item)
                            
                            # System ID (editable)
                            # Always use the device's own System ID from isis_config, not from neighbor data
                            # The neighbor's system_id field might contain hostname (e.g., "san-q5130e-04")
                            # which is not in the correct XXXX.XXXX.XXXX format
                            system_id = isis_config.get("system_id", "")
                            system_id_item = QTableWidgetItem(system_id)
                            system_id_item.setFlags(system_id_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 8, system_id_item)
                            
                            # Hello Interval (editable)
                            hello_interval = neighbor.get("hello_interval", isis_config.get("hello_interval", "10"))
                            hello_interval_item = QTableWidgetItem(str(hello_interval))
                            hello_interval_item.setFlags(hello_interval_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 9, hello_interval_item)
                            
                            # Multiplier (editable)
                            multiplier = neighbor.get("hello_multiplier", isis_config.get("hello_multiplier", "3"))
                            multiplier_item = QTableWidgetItem(str(multiplier))
                            multiplier_item.setFlags(multiplier_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 10, multiplier_item)
                    else:
                        # No neighbors found or marked for removal, show device status
                        row = self.parent.isis_table.rowCount()
                        self.parent.isis_table.insertRow(row)
                        
                        # Device
                        self.parent.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                        
                        # ISIS Status (with icon)
                        if is_marked_for_removal:
                            isis_status = "Marked for Removal"
                            self.parent.set_isis_status_icon(row, "Marked for Removal", "ISIS Marked for Removal")
                        else:
                            if isis_status_data:
                                if isis_status_data.get("isis_established"):
                                    isis_status = "Established"
                                elif isis_status_data.get("isis_running"):
                                    isis_status = "Starting"
                                else:
                                    isis_status = "Down"
                            else:
                                # No DB status yet (device not applied) → show Down (red)
                                isis_status = "Down"
                            self.parent.set_isis_status_icon(row, isis_status, f"ISIS {isis_status}")
                        
                        # Neighbor Type
                        if is_marked_for_removal:
                            self.parent.isis_table.setItem(row, 2, QTableWidgetItem("Pending Removal"))
                        else:
                            # Show separate rows for IPv4 and IPv6 if enabled
                            ipv4_enabled = isis_config.get("ipv4_enabled", False) if isis_config else False
                            ipv6_enabled = isis_config.get("ipv6_enabled", False) if isis_config else False
                            
                            if ipv4_enabled or ipv6_enabled:
                                # Show protocol type based on enabled flags
                                if ipv4_enabled and ipv6_enabled:
                                    # Show first row as IPv4, will create another row for IPv6 below
                                    self.parent.isis_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                elif ipv4_enabled:
                                    self.parent.isis_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                elif ipv6_enabled:
                                    self.parent.isis_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                                else:
                                    self.parent.isis_table.setItem(row, 2, QTableWidgetItem("No Neighbors"))
                            else:
                                self.parent.isis_table.setItem(row, 2, QTableWidgetItem("No Neighbors"))
                        
                        # Interface - show device VLAN interface instead of physical interface
                        device_interface = isis_config.get("interface", iface)
                        # If interface is not in config, try to construct from VLAN
                        if not device_interface or device_interface == iface:
                            device_vlan = device.get("VLAN", "0")
                            if device_vlan and device_vlan != "0":
                                device_interface = f"vlan{device_vlan}"
                            else:
                                device_interface = iface
                        # Debug logs disabled
                        
                        # Neighbor Type for first row is already set above based on enabled AFs
                        # If neither IPv4 nor IPv6 is enabled, show N/A
                        if not (ipv4_enabled or ipv6_enabled):
                            self.parent.isis_table.setItem(row, 2, QTableWidgetItem("No Neighbors"))
                        
                        # Neighbor Hostname (for first row - no neighbor, show N/A)
                        self.parent.isis_table.setItem(row, 3, QTableWidgetItem("N/A"))
                        
                        # Interface (for first row)
                        self.parent.isis_table.setItem(row, 4, QTableWidgetItem(device_interface))
                        
                        # ISIS Area (for first row)
                        self.parent.isis_table.setItem(row, 5, QTableWidgetItem(isis_config.get("area_id", "")))
                        
                        # Level (for first row)
                        self.parent.isis_table.setItem(row, 6, QTableWidgetItem(isis_config.get("level", "Level-2")))
                        
                        # ISIS Net (for first row) - editable
                        isis_net_item = QTableWidgetItem(isis_config.get("area_id", ""))
                        isis_net_item.setFlags(isis_net_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                        self.parent.isis_table.setItem(row, 7, isis_net_item)
                        
                        # System ID (for first row) - editable
                        system_id_item = QTableWidgetItem(isis_config.get("system_id", ""))
                        system_id_item.setFlags(system_id_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                        self.parent.isis_table.setItem(row, 8, system_id_item)
                        
                        # Hello Interval (for first row) - editable
                        hello_interval_item = QTableWidgetItem(str(isis_config.get("hello_interval", "10")))
                        hello_interval_item.setFlags(hello_interval_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                        self.parent.isis_table.setItem(row, 9, hello_interval_item)
                        
                        # Multiplier (for first row) - editable
                        multiplier_item = QTableWidgetItem(str(isis_config.get("hello_multiplier", "3")))
                        multiplier_item.setFlags(multiplier_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                        self.parent.isis_table.setItem(row, 10, multiplier_item)
                        
                        # If both IPv4 and IPv6 are enabled, create a second row for IPv6
                        if ipv4_enabled and ipv6_enabled and not is_marked_for_removal:
                            row = self.parent.isis_table.rowCount()
                            self.parent.isis_table.insertRow(row)
                            
                            # Device
                            self.parent.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                            
                            # ISIS Status (with icon) - same as IPv4 row
                            if isis_status_data:
                                if isis_status_data.get("isis_established"):
                                    isis_status = "Established"
                                elif isis_status_data.get("isis_running"):
                                    isis_status = "Starting"
                                else:
                                    isis_status = "Down"
                            else:
                                # No DB status yet (device not applied) → show Down (red)
                                isis_status = "Down"
                            self.parent.set_isis_status_icon(row, isis_status, f"ISIS {isis_status}")
                            
                            # Neighbor Type - IPv6
                            self.parent.isis_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                            
                            # Neighbor Hostname (for IPv6 row - no neighbor, show N/A)
                            self.parent.isis_table.setItem(row, 3, QTableWidgetItem("N/A"))
                            
                            # Interface
                            self.parent.isis_table.setItem(row, 4, QTableWidgetItem(device_interface))
                            
                            # ISIS Area
                            self.parent.isis_table.setItem(row, 5, QTableWidgetItem(isis_config.get("area_id", "")))
                            
                            # Level
                            self.parent.isis_table.setItem(row, 6, QTableWidgetItem(isis_config.get("level", "Level-2")))
                            
                            # ISIS Net - editable
                            isis_net_item = QTableWidgetItem(isis_config.get("area_id", ""))
                            isis_net_item.setFlags(isis_net_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 7, isis_net_item)
                            
                            # System ID - editable
                            system_id_item = QTableWidgetItem(isis_config.get("system_id", ""))
                            system_id_item.setFlags(system_id_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 8, system_id_item)
                            
                            # Hello Interval - editable
                            hello_interval_item = QTableWidgetItem(str(isis_config.get("hello_interval", "10")))
                            hello_interval_item.setFlags(hello_interval_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 9, hello_interval_item)
                            
                            # Multiplier - editable
                            multiplier_item = QTableWidgetItem(str(isis_config.get("hello_multiplier", "3")))
                            multiplier_item.setFlags(multiplier_item.flags() | Qt.ItemIsEditable)  # Ensure editable
                            self.parent.isis_table.setItem(row, 10, multiplier_item)
                    
        except Exception as e:
            logger.error(f"Error updating ISIS table: {e}")
        finally:
            # Restore previous signal-block state so we don't accidentally leave
            # signals blocked for caller code that depended on them.
            self.parent.isis_table.blockSignals(signals_were_blocked)


    def set_isis_status_icon(self, row, status, tooltip):
        """Set ISIS status icon for a table row."""
        try:
            # Helper function to load icons
            def load_icon(filename: str) -> QIcon:
                from utils.qicon_loader import qicon
                return qicon("resources", f"icons/{filename}")
            
            # Determine icon based on ISIS status
            status_lower = status.lower()

            if status_lower in ["up", "running", "established"]:
                icon = load_icon("green_dot.png")
            elif status_lower in ["starting"]:
                icon = load_icon("yellow_dot.png")
            elif status_lower in ["down", "stopped", "idle"]:
                icon = load_icon("red_dot.png")
            elif status_lower in ["stopping"]:
                icon = load_icon("yellow_dot.png")
            elif status_lower in ["marked for removal"]:
                icon = load_icon("orange_dot.png")
            else:
                icon = load_icon("orange_dot.png")
            
            # Create item with icon
            item = QTableWidgetItem()
            item.setIcon(icon)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # Make ISIS Status column non-editable
            self.parent.isis_table.setItem(row, 1, item)
            
        except Exception as e:
            logger.error(f"Error setting ISIS status icon: {e}")
            # Fallback to text
            self.parent.isis_table.setItem(row, 1, QTableWidgetItem(status))


    def _get_isis_status_from_database(self, device_id: str) -> dict:
        """Get ISIS status from database for a device."""
        try:
            server_url = self.parent.get_server_url(silent=True)
            if not server_url or not device_id:
                return {}
            
            # Get device information from database
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=1)
            if response.status_code == 200:
                device_data = response.json()
                
                # Extract ISIS status information
                isis_status = {
                    "isis_running": device_data.get('isis_running', False),
                    "isis_established": device_data.get('isis_established', False),
                    "isis_state": device_data.get('isis_state', 'Unknown'),
                    "neighbors": []
                }
                
                # Parse ISIS neighbors if available
                isis_neighbors = device_data.get('isis_neighbors')
                if isis_neighbors:
                    try:
                        if isinstance(isis_neighbors, str):
                            import json
                            neighbors_data = json.loads(isis_neighbors)
                        else:
                            neighbors_data = isis_neighbors
                        
                        if isinstance(neighbors_data, list):
                            for neighbor in neighbors_data:
                                neighbor_info = {
                                    "state": neighbor.get("state", "Down"),
                                    "type": neighbor.get("type", "Unknown"),
                                    "interface": neighbor.get("interface", ""),
                                    "area": neighbor.get("area", ""),
                                    "level": neighbor.get("level", ""),
                                    "net": neighbor.get("net", ""),
                                    "system_id": neighbor.get("system_id", ""),
                                    "priority": neighbor.get("priority", ""),
                                    "uptime": neighbor.get("uptime", "")
                                }
                                isis_status["neighbors"].append(neighbor_info)
                    except Exception as e:
                        logger.error(f"Error parsing ISIS neighbors: {e}")
                
                return isis_status
            else:
                return {}
                
        except Exception:
            # Don't print debug errors to reduce spam
            return {}

    # ---------- Utilities ----------


    def _safe_update_isis_table(self):
        """Safely update ISIS table (for parallel execution)."""
        try:
            logger.info("[PROTOCOL REFRESH] Refreshing ISIS table...")
            self.update_isis_table()
        except Exception as e:
            logging.error(f"[ISIS REFRESH ERROR] {e}")


    def _apply_isis_to_server_sync(self, server_url, device_info):
        """Apply ISIS configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            
            # Get ISIS config - handle both isis_config and is_is_config keys, and old dict format
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            if not isis_config:
                # Try old format for backward compatibility
                protocols = device_info.get("protocols", {})
                if isinstance(protocols, dict):
                    isis_config = protocols.get("ISIS", {}) or protocols.get("IS-IS", {}) or protocols.get("isis", {})
            
            if not isis_config:
                return True  # No ISIS config to apply
            
            # Prepare ISIS payload using the configure endpoint
            isis_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "isis_config": isis_config
            }
            
            # Make synchronous request to the configure endpoint
            response = requests.post(f"{server_url}/api/device/isis/configure", json=isis_payload, timeout=30)
            if response.status_code == 200:
                return True

            # Surface the actual server error so the caller can show
            # something more useful than "ISIS configuration failed
            # (check server logs)".
            err_msg = f"HTTP {response.status_code}"
            try:
                body = response.json()
                if isinstance(body, dict):
                    err_msg = body.get("error") or body.get("message") or err_msg
                    details = body.get("details") or body.get("stderr")
                    if details:
                        err_msg = f"{err_msg} — {str(details)[:200]}"
            except Exception:
                if response.text:
                    err_msg = f"{err_msg}: {response.text[:200]}"
            full_err = f"ISIS configure: {err_msg}"
            logger.error(f"[ISIS APPLY] {device_name} configure failed → {full_err}")
            device_info["_apply_error"] = full_err
            return False

        except Exception as e:
            logger.error(f"Exception in sync ISIS apply for '{device_name}': {e}")
            device_info["_apply_error"] = f"ISIS configure exception: {e}"
            return False
    
    

    def on_isis_table_cell_changed(self, row, column):
        """Handle cell changes in ISIS table - handles inline editing of ISIS Net, System ID, Hello Interval, and Multiplier."""
        # Only process editable columns: ISIS Net (6), System ID (7), Hello Interval (8), Multiplier (9)
        if column not in [6, 7, 8, 9]:
            return
        
        # Prevent infinite loops by checking if we're already processing a cell change
        # This can happen when update_isis_table() programmatically updates cells
        if hasattr(self, '_processing_isis_cell_change') and self.parent._processing_isis_cell_change:
            return
        self.parent._processing_isis_cell_change = True
        
        try:
            # Get table items with null checks
            device_item = self.parent.isis_table.item(row, 0)
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
            
            # Check if ISIS is configured
            protocols = device_info.get("protocols", []) if device_info else []
            is_isis_configured = False
            if isinstance(protocols, list):
                is_isis_configured = "ISIS" in protocols or "IS-IS" in protocols
            elif isinstance(protocols, dict):
                is_isis_configured = "IS-IS" in protocols or "ISIS" in protocols
            
            if device_info and is_isis_configured:
                # Handle both old format (dict) and new format (list)
                if isinstance(protocols, dict):
                    isis_config = protocols.get("IS-IS", {}) or protocols.get("ISIS", {})
                else:
                    # Check both isis_config and is_is_config for backward compatibility
                    isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
                
                # Get current ISIS config to preserve all fields
                # Make a deep copy to preserve all original values, including ipv4_enabled and ipv6_enabled
                current_isis_config = isis_config.copy() if isis_config else {}
                
                # Initialize isis_config to current_isis_config as starting point for updates
                # This ensures we always have a valid config to update
                isis_config = current_isis_config.copy()
                
                # Ensure we preserve ipv4_enabled and ipv6_enabled from the original config
                # If they're not in the config, try to infer from device's IP addresses
                # Default to True if device has IP addresses, to ensure rows are shown
                if "ipv4_enabled" not in current_isis_config:
                    # Try to get from device's IPv4 address
                    if device_info.get("ipv4_address") or device_info.get("IPv4 Address"):
                        current_isis_config["ipv4_enabled"] = True
                    else:
                        # Default to True if we can't determine, to ensure rows are shown
                        current_isis_config["ipv4_enabled"] = True
                if "ipv6_enabled" not in current_isis_config:
                    # Try to get from device's IPv6 address
                    if device_info.get("ipv6_address") or device_info.get("IPv6 Address"):
                        current_isis_config["ipv6_enabled"] = True
                    else:
                        # Default to True if we can't determine, to ensure rows are shown
                        current_isis_config["ipv6_enabled"] = True
                
                # Detect which address family is selected (IPv4 or IPv6) from the table row
                neighbor_type_item = self.parent.isis_table.item(row, 2)  # Column 2 is "Neighbor Type"
                if neighbor_type_item:
                    protocol_type = neighbor_type_item.text().strip()
                    is_ipv6 = protocol_type == "IPv6"
                else:
                    # Fallback: assume IPv4 if not found
                    is_ipv6 = False
                
                if column == 7:  # ISIS Net changed (column 7, after adding Neighbor Hostname)
                    isis_net_item = self.parent.isis_table.item(row, 7)

                    if isis_net_item:
                        new_isis_net = isis_net_item.text().strip()

                        # v0.2.86: lifted hand-rolled hardcoded-6-part
                        # check into utils.isis_net.validate_isis_net.
                        # The new helper:
                        #   - accepts variable-length area IDs (RFC 1195
                        #     allows 8-20 total bytes)
                        #   - enforces NSEL=00 (the old check missed this)
                        #   - flags non-hex chars + odd hex counts with
                        #     actionable messages
                        # Partial input (typing in progress) is detected
                        # via the "too short" / "odd hex-character count"
                        # error subtypes — those don't pop the modal so
                        # the operator can keep typing.
                        if new_isis_net:
                            from utils.isis_net import validate_isis_net
                            err = validate_isis_net(new_isis_net)
                            looks_partial = err is not None and (
                                "too short" in err.lower()
                                or "odd hex-character count" in err
                            )
                            if err is None:
                                # Validation passed — update the config,
                                # preserving ipv4/ipv6 enable flags.
                                if "area_id" not in isis_config or isis_config.get("area_id") != new_isis_net:
                                    isis_config["area_id"] = new_isis_net
                                if "ipv4_enabled" not in isis_config:
                                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", True)
                                if "ipv6_enabled" not in isis_config:
                                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", True)
                            elif looks_partial:
                                # Mid-keystroke — allow it, validate
                                # again on next edit / submit.
                                isis_config = current_isis_config.copy()
                                isis_config["area_id"] = new_isis_net
                                if "ipv4_enabled" not in isis_config:
                                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                                if "ipv6_enabled" not in isis_config:
                                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                            else:
                                # Definitely invalid (non-hex chars, NSEL
                                # != 00, > 20 bytes…) — show error and
                                # revert.
                                QMessageBox.warning(self.parent, "Invalid ISIS Net Format",
                                                  f"'{new_isis_net}' is not a valid ISIS Network Entity Title (NET).\n\n"
                                                  f"Format: AFI + Area + System ID + NSEL=00 (8-20 bytes).\n"
                                                  f"Example: 49.0001.0000.0000.0001.00\n\n"
                                                  f"Error: {err}")
                                try:
                                    original_net = current_isis_config.get("area_id", "")
                                    if isis_net_item:  # Check if item still exists
                                        isis_net_item.setText(original_net)
                                except RuntimeError:
                                    # Item was deleted, ignore
                                    pass
                                return
                    else:
                        # Empty value - allow it
                        # Preserve all existing fields
                        isis_config = current_isis_config.copy()
                        isis_config["area_id"] = new_isis_net
                        # Ensure ipv4_enabled and ipv6_enabled are preserved
                        if "ipv4_enabled" not in isis_config:
                            isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                        if "ipv6_enabled" not in isis_config:
                            isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                
                elif column == 8:  # System ID changed (column 8, after adding Neighbor Hostname)
                    system_id_item = self.parent.isis_table.item(row, 8)
                    
                    if system_id_item:
                        new_system_id = system_id_item.text().strip()
                    
                    # Only validate if the field is not empty and seems complete (has 2 dots)
                    # This allows partial input during typing
                    if new_system_id:
                        # Split by dots
                        parts = new_system_id.split(".")
                        # Only validate if it looks like a complete System ID (3 parts)
                        if len(parts) == 3:
                            try:
                                # Validate each part
                                for i, part in enumerate(parts):
                                    if not part:
                                        raise ValueError(f"Part {i+1} cannot be empty")
                                    
                                    # Each part must be exactly 4 hexadecimal digits (XXXX format)
                                    if len(part) != 4:
                                        raise ValueError(f"Part {i+1} '{part}' must be exactly 4 hexadecimal digits (XXXX format)")
                                    
                                    # Each part should be hexadecimal (0-9, A-F, case-insensitive)
                                    try:
                                        int(part, 16)
                                    except ValueError:
                                        raise ValueError(f"Part {i+1} '{part}' is not valid hexadecimal. Must be 4 hexadecimal digits (0-9, A-F)")
                                
                                # Convert to uppercase for consistency (ISIS System ID is typically uppercase)
                                normalized_system_id = ".".join(part.upper() for part in parts)
                                if normalized_system_id != new_system_id:
                                    # Update the table cell with uppercase version
                                    new_system_id = normalized_system_id
                                    if system_id_item:
                                        try:
                                            system_id_item.setText(normalized_system_id)
                                        except RuntimeError:
                                            pass
                                
                                # Validation passed - update the config
                                # Preserve all existing fields, especially ipv4_enabled and ipv6_enabled
                                # Note: isis_config was already initialized from current_isis_config at line 7372
                                # So we just need to update the system_id field (don't copy again)
                                isis_config["system_id"] = normalized_system_id
                                
                                # Debug: Log the update
                                # Debug logs disabled
                                
                                # Ensure ipv4_enabled and ipv6_enabled are preserved
                                if "ipv4_enabled" not in isis_config:
                                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", True)
                                if "ipv6_enabled" not in isis_config:
                                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", True)
                            except ValueError as e:
                                # Only show error if it's clearly invalid (not just incomplete)
                                # Check if it's a partial input (has dots but not 3 parts)
                                if len(parts) < 3 and "." in new_system_id:
                                    # Partial input - allow it, don't validate yet
                                    # Preserve all existing fields
                                    # Note: isis_config was already initialized from current_isis_config at line 7372
                                    # So we just need to update the system_id field (don't copy again)
                                    isis_config["system_id"] = new_system_id
                                    # Ensure ipv4_enabled and ipv6_enabled are preserved
                                    if "ipv4_enabled" not in isis_config:
                                        isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", True)
                                    if "ipv6_enabled" not in isis_config:
                                        isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", True)
                                else:
                                    # Invalid format - show error and revert
                                    QMessageBox.warning(self.parent, "Invalid System ID Format", 
                                                      f"'{new_system_id}' is not a valid ISIS System ID.\n\n"
                                                      f"System ID must be in format: XXXX.XXXX.XXXX\n"
                                                      f"Where each XXXX is exactly 4 hexadecimal digits (0-9, A-F)\n"
                                                      f"Example: 0000.0000.0001 or AAAA.BBBB.CCCC\n\n"
                                                      f"Error: {str(e)}")
                                    # Revert to original value - check if item still exists
                                    try:
                                        original_system_id = current_isis_config.get("system_id", "")
                                        if system_id_item:  # Check if item still exists
                                            system_id_item.setText(original_system_id)
                                    except RuntimeError:
                                        # Item was deleted, ignore
                                        pass
                                    return
                        else:
                            # Partial input (doesn't have 3 parts yet) - allow it
                            # Preserve all existing fields
                            # Note: isis_config was already initialized from current_isis_config at line 7372
                            # So we just need to update the system_id field (don't copy again)
                            isis_config["system_id"] = new_system_id
                            # Ensure ipv4_enabled and ipv6_enabled are preserved
                            if "ipv4_enabled" not in isis_config:
                                isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", True)
                            if "ipv6_enabled" not in isis_config:
                                isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", True)
                    else:
                        # Empty value - allow it
                        # Preserve all existing fields
                        # Note: isis_config was already initialized from current_isis_config at line 7372
                        # So we just need to update the system_id field (don't copy again)
                        isis_config["system_id"] = new_system_id
                        # Ensure ipv4_enabled and ipv6_enabled are preserved
                        if "ipv4_enabled" not in isis_config:
                            isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", True)
                        if "ipv6_enabled" not in isis_config:
                            isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", True)
                
                elif column == 9:  # Hello Interval changed (column 9, after adding Neighbor Hostname)
                    hello_interval_item = self.parent.isis_table.item(row, 9)
                    
                    if hello_interval_item:
                        hello_interval = hello_interval_item.text().strip()
                    
                    # Validate Hello Interval (1-65535 seconds)
                    # Allow empty or partial input during typing
                    if hello_interval:
                        try:
                            interval_value = int(hello_interval)
                            if interval_value < 1 or interval_value > 65535:
                                raise ValueError("Hello Interval out of range")
                            # Validation passed - update the config
                            isis_config["hello_interval"] = hello_interval
                        except ValueError as e:
                            # Check if it's a partial number (could be valid once complete)
                            if hello_interval.isdigit() or (hello_interval.startswith('-') and hello_interval[1:].isdigit()):
                                # Partial number - allow it, don't validate yet
                                # Preserve all existing fields
                                isis_config = current_isis_config.copy()
                                isis_config["hello_interval"] = hello_interval
                                # Ensure ipv4_enabled and ipv6_enabled are preserved
                                if "ipv4_enabled" not in isis_config:
                                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                                if "ipv6_enabled" not in isis_config:
                                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                            else:
                                # Invalid format - show error and revert
                                QMessageBox.warning(self.parent, "Invalid Hello Interval", 
                                                  f"'{hello_interval}' is not a valid Hello Interval.\n"
                                                  f"Hello Interval must be between 1 and 65535 seconds.")
                                # Revert to original value - check if item still exists
                                try:
                                    original_hello_interval = str(current_isis_config.get("hello_interval", "10"))
                                    if hello_interval_item:  # Check if item still exists
                                        hello_interval_item.setText(original_hello_interval)
                                except RuntimeError:
                                    # Item was deleted, ignore
                                    pass
                                return
                    else:
                        # Empty value - allow it
                        # Preserve all existing fields
                        isis_config = current_isis_config.copy()
                        isis_config["hello_interval"] = hello_interval
                        # Ensure ipv4_enabled and ipv6_enabled are preserved
                        if "ipv4_enabled" not in isis_config:
                            isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                        if "ipv6_enabled" not in isis_config:
                            isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                
                elif column == 10:  # Multiplier changed (column 10, after adding Neighbor Hostname)
                    multiplier_item = self.parent.isis_table.item(row, 10)
                    
                    if multiplier_item:
                        multiplier = multiplier_item.text().strip()
                    
                    # Validate Multiplier (1-100)
                    # Allow empty or partial input during typing
                    if multiplier:
                        try:
                            multiplier_value = int(multiplier)
                            if multiplier_value < 1 or multiplier_value > 100:
                                raise ValueError("Multiplier out of range")
                            # Validation passed - update the config
                            # Preserve all existing fields, especially ipv4_enabled and ipv6_enabled
                            isis_config = current_isis_config.copy()
                            isis_config["hello_multiplier"] = multiplier
                            
                            # Ensure ipv4_enabled and ipv6_enabled are preserved
                            if "ipv4_enabled" not in isis_config:
                                isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                            if "ipv6_enabled" not in isis_config:
                                isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                        except ValueError as e:
                            # Check if it's a partial number (could be valid once complete)
                            if multiplier.isdigit() or (multiplier.startswith('-') and multiplier[1:].isdigit()):
                                # Partial number - allow it, don't validate yet
                                # Preserve all existing fields
                                isis_config = current_isis_config.copy()
                                isis_config["hello_multiplier"] = multiplier
                                # Ensure ipv4_enabled and ipv6_enabled are preserved
                                if "ipv4_enabled" not in isis_config:
                                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                                if "ipv6_enabled" not in isis_config:
                                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                            else:
                                # Invalid format - show error and revert
                                QMessageBox.warning(self.parent, "Invalid Multiplier", 
                                                  f"'{multiplier}' is not a valid Multiplier.\n"
                                                  f"Multiplier must be between 1 and 100.")
                                # Revert to original value - check if item still exists
                                try:
                                    original_multiplier = str(current_isis_config.get("hello_multiplier", "3"))
                                    if multiplier_item:  # Check if item still exists
                                        multiplier_item.setText(original_multiplier)
                                except RuntimeError:
                                    # Item was deleted, ignore
                                    pass
                                return
                    else:
                        # Empty value - allow it
                        # Preserve all existing fields
                        isis_config = current_isis_config.copy()
                        isis_config["hello_multiplier"] = multiplier
                        # Ensure ipv4_enabled and ipv6_enabled are preserved
                        if "ipv4_enabled" not in isis_config:
                            isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                        if "ipv6_enabled" not in isis_config:
                            isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
            
                # Always ensure ipv4_enabled and ipv6_enabled are set before updating
                # This prevents them from being lost during updates
                if "ipv4_enabled" not in isis_config:
                    isis_config["ipv4_enabled"] = current_isis_config.get("ipv4_enabled", False)
                if "ipv6_enabled" not in isis_config:
                    isis_config["ipv6_enabled"] = current_isis_config.get("ipv6_enabled", False)
                
                # Ensure isis_config is initialized (should be set by column handlers above)
                # Only initialize if not already set by column handlers (don't overwrite updates)
                # Check if area_id was updated by a column handler (e.g., ISIS Net column)
                area_id_was_updated = False
                if isis_config and "area_id" in isis_config:
                    # Check if the area_id in isis_config differs from current_isis_config
                    if isis_config.get("area_id") != current_isis_config.get("area_id"):
                        area_id_was_updated = True
                        # Debug logs disabled
                
                if not isis_config:
                    isis_config = current_isis_config.copy()
                elif not area_id_was_updated and "area_id" not in isis_config and current_isis_config.get("area_id"):
                    # Only restore area_id if it wasn't set by a column handler
                    isis_config["area_id"] = current_isis_config.get("area_id")
                    # Debug logs disabled
                
                # Debug logs disabled
                
                # Update the device using the protocol update method
                # Note: This will update both is_is_config and isis_config for backward compatibility
                # Use a flag to prevent infinite recursion
                if not getattr(self, '_updating_isis_protocol', False):
                    self.parent._updating_isis_protocol = True
                    try:
                        self.parent._update_device_protocol(device_name, "IS-IS", isis_config)
                        # Save session only once, not on every recursive call
                        if hasattr(self.parent.main_window, "save_session"):
                            self.parent.main_window.save_session()
                    finally:
                        self.parent._updating_isis_protocol = False
        finally:
            # Always clear the processing flag, even if there was an error
            if hasattr(self, '_processing_isis_cell_change'):
                self.parent._processing_isis_cell_change = False


    def prompt_add_isis(self):
        """Add IS-IS configuration to selected device."""
        selected_items = self.parent.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select a device to add IS-IS configuration.")
            return

        row = selected_items[0].row()
        device_name = self.parent.devices_table.item(row, self.parent.COL["Device Name"]).text()
        
        # Find the device's interface from all_devices
        device_interface = None
        device_vlan = None
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_vlan = device.get("VLAN", "")
                    # Use VLAN interface (e.g., vlan21) instead of physical interface
                    if device_vlan:
                        device_interface = f"vlan{device_vlan}"
                    else:
                        device_interface = device.get("Interface", "")
                    break
            if device_interface:
                break
        
        # Create ISIS config with the VLAN interface
        isis_config = {"interface": device_interface} if device_interface else {}
        
        from widgets.add_isis_dialog import AddIsisDialog
        dialog = AddIsisDialog(self.parent, device_name, edit_mode=False, isis_config=isis_config)
        if dialog.exec_() != dialog.Accepted:
            return

        isis_config = dialog.get_values()
        
        # Update the device with IS-IS configuration
        self.parent._update_device_protocol(row, "IS-IS", isis_config)


    def _cleanup_isis_table_for_device(self, device_id, device_name):
        """Clean up ISIS table entries for a removed device."""
        try:
            logger.debug(f"[DEBUG ISIS CLEANUP] Cleaning up ISIS entries for device '{device_name}' (ID: {device_id})")
            
            # Remove ISIS table rows that match this device
            rows_to_remove = []
            for row in range(self.parent.isis_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.parent.isis_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    logger.debug(f"[DEBUG ISIS CLEANUP] Found ISIS row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.parent.isis_table.removeRow(row)
                logger.debug(f"[DEBUG ISIS CLEANUP] Removed ISIS table row {row}")
            
            # Also clean up ISIS protocol data from device protocols
            # Remove ISIS protocol from the device in all_devices
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove IS-IS from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list):
                                if "IS-IS" in device["protocols"]:
                                    device["protocols"].remove("IS-IS")
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed IS-IS protocol from device '{device_name}'")
                                elif "ISIS" in device["protocols"]:
                                    device["protocols"].remove("ISIS")
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed ISIS protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict):
                                # Handle old format for backward compatibility
                                if "IS-IS" in device["protocols"]:
                                    del device["protocols"]["IS-IS"]
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed IS-IS protocol from device '{device_name}' (old format)")
                                elif "ISIS" in device["protocols"]:
                                    del device["protocols"]["ISIS"]
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed ISIS protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    logger.debug(f"[DEBUG ISIS CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove isis_config and is_is_config if they exist
                        if "isis_config" in device:
                            del device["isis_config"]
                            logger.debug(f"[DEBUG ISIS CLEANUP] Removed isis_config from device '{device_name}'")
                        if "is_is_config" in device:
                            del device["is_is_config"]
                            logger.debug(f"[DEBUG ISIS CLEANUP] Removed is_is_config from device '{device_name}'")
                        break
            
            logger.debug(f"[DEBUG ISIS CLEANUP] Removed {len(rows_to_remove)} ISIS entries for device '{device_name}'")
            
        except Exception as e:
            logger.error(f"Failed to cleanup ISIS entries for device '{device_name}': {e}")

    # ---------- Table refresh ----------


    def start_isis_protocol(self):
        """Start IS-IS protocol for selected devices."""
        self.parent._toggle_protocol_action("IS-IS", starting=True)


    def stop_isis_protocol(self):
        """Stop IS-IS protocol for selected devices."""
        self.parent._toggle_protocol_action("IS-IS", starting=False)


    def start_isis_monitoring(self):
        """Start periodic ISIS status monitoring."""
        if not self.parent.isis_monitoring_active:
            self.parent.isis_monitoring_active = True
            self.parent.isis_monitoring_timer.start(20000)  # Check every 20 seconds to match OSPF
            logger.info("[ISIS MONITORING] Started periodic ISIS status monitoring")
        else:
            logger.info("[ISIS MONITORING] Already active")
    

    def stop_isis_monitoring(self):
        """Stop periodic ISIS status monitoring."""
        if self.parent.isis_monitoring_active:
            self.parent.isis_monitoring_active = False
            self.parent.isis_monitoring_timer.stop()
            logger.info("[ISIS MONITORING] Stopped periodic ISIS status monitoring")
        else:
            logger.info("[ISIS MONITORING] Already stopped")
    

    def periodic_isis_status_check(self):
        """Periodic ISIS status check - called by timer."""
        # Symmetry with OSPF's periodic_ospf_status_check: refuse to run if the
        # user has stopped monitoring. Without this guard the timer keeps
        # firing forever once start_isis_monitoring was ever called.
        if not getattr(self.parent, 'isis_monitoring_active', False):
            return
        try:
            # Get devices that ACTUALLY have ISIS configured — not just devices
            # whose protocols list mentions "IS-IS". Many devices end up with
            # "IS-IS" listed in protocols from earlier sessions or templates
            # but have no isis_config / is_is_config dict, meaning the user
            # never finished configuring the protocol. Periodic monitoring on
            # such devices is just noise.
            def has_real_isis_config(device):
                if not device.get("protocols") or "IS-IS" not in device.get("protocols", []):
                    return False
                cfg = device.get("isis_config") or device.get("is_is_config") or {}
                if not isinstance(cfg, dict):
                    return False
                # area_id is the minimum required ISIS config; if it's missing,
                # the device hasn't been configured for ISIS in any meaningful way.
                return bool(cfg.get("area_id"))

            isis_devices = []
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    if has_real_isis_config(device):
                        isis_devices.append(device)

            if not isis_devices:
                # Nothing to monitor; auto-stop so the timer doesn't keep firing.
                logger.info("[ISIS MONITORING] No devices with ISIS configured - stopping monitoring")
                if hasattr(self, 'stop_isis_monitoring'):
                    self.stop_isis_monitoring()
                return

            # Skip the table refresh + log when no server is online — the table
            # would just paint stale state and the log line is misleading
            # ("Periodic ISIS status check" sounds like we hit the server,
            # but it's actually a local model refresh).
            mw = getattr(self.parent, 'main_window', None)
            online_servers = [
                s for s in (getattr(mw, 'server_interfaces', []) or [])
                if s.get('online')
            ]
            if not online_servers:
                logger.debug(
                    f"[ISIS MONITORING] Skipping check — {len(isis_devices)} ISIS device(s) "
                    "but no servers online"
                )
                return

            # Heartbeat at INFO — only fires when there are real-config ISIS
            # devices (gated above) so it confirms monitoring is alive without
            # being chatty for misconfigured / orphan-config devices.
            logger.info(f"[ISIS MONITORING] Periodic ISIS status check for {len(isis_devices)} devices")
            # Use QTimer.singleShot to defer table update and avoid blocking UI thread
            # This ensures the periodic check doesn't block the UI during table updates
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.update_isis_table)  # Defer to next event loop iteration

        except Exception as e:
            logger.error(f"[ISIS MONITORING ERROR] Error in periodic ISIS status check: {e}")
    
