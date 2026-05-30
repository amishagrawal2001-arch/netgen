"""BGP-related functionality for DevicesTab.

This module contains all BGP-specific methods extracted from devices_tab.py
to improve code organization and maintainability.
"""

from PyQt5.QtWidgets import (
    QTableWidgetItem, QMessageBox, QDialog, QTableWidget, 
    QPushButton, QVBoxLayout, QHBoxLayout, QLabel
)
from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
import requests
import logging
logger = logging.getLogger(__name__)
import ipaddress


class BGPHandler:
    """Handler class for BGP-related functionality in DevicesTab."""
    
    def __init__(self, parent_tab):
        """Initialize BGP handler with reference to parent DevicesTab.
        
        Args:
            parent_tab: The DevicesTab instance that owns this handler.
        """
        self.parent = parent_tab
    

    def setup_bgp_subtab(self):
        """Setup the BGP sub-tab with BGP-specific functionality."""
        layout = QVBoxLayout(self.parent.bgp_subtab)
        # Tight chrome — matches the Devices tab so the action bar
        # sits flush against the bottom edge of the panel instead of
        # floating with default Qt margins (~11px on each side).
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # BGP Neighbors Table - each neighbor IP gets its own row
        bgp_headers = ["Device", "BGP Status", "Neighbor Type", "Neighbor IP", "Source IP", "BGP Local AS", "BGP Remote AS", "State", "Routes", "Route Pools", "Keepalive", "Hold-time"]
        self.parent.bgp_table = QTableWidget(0, len(bgp_headers))
        self.parent.bgp_table.setHorizontalHeaderLabels(bgp_headers)
        self.parent.BGP_COL = {h: i for i, h in enumerate(bgp_headers)}
        
        # Enable inline editing for the BGP table
        self.parent.bgp_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.parent.bgp_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect selection changed signal to update attach button state
        self.parent.bgp_table.selectionModel().selectionChanged.connect(self.on_bgp_selection_changed)
        
        # Connect cell changed signal for handling checkbox changes
        self.parent.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)
        
        # Section header removed — tab name + column headers carry it.
        layout.addWidget(self.parent.bgp_table)

        # v0.2.88: empty-state placeholder. A fresh session with no
        # BGP configured used to render this table as a blank
        # rectangle — operators couldn't tell "no BGP yet" from
        # "broken connection".
        try:
            from widgets.empty_state_overlay import EmptyStateOverlay
            self.parent.bgp_empty_state = EmptyStateOverlay(
                self.parent.bgp_table,
                "No BGP neighbours configured.\n\n"
                "Add one with the Add button below, or configure "
                "BGP on a device via the main Devices table "
                "(right-click → Edit → enable BGP)."
            )
        except Exception:
            pass  # overlay is advisory; never block sub-tab render
        
        # BGP action bar — same chrome as the Devices action row
        # (grey footer QFrame, BTN_BASE / BTN_APPLY styles, 28×24 px
        # buttons, vertical divider between config and runtime
        # groups). Previously this was a bare QHBoxLayout of stock
        # QPushButtons at 32×28 with no background, which looked like
        # a different application from the rest of the tab.
        from PyQt5.QtWidgets import QFrame
        from PyQt5.QtCore import Qt
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border-top: 1px solid #e5e7eb; border-radius: 0; }"
        )
        bgp_controls = QHBoxLayout(action_bar)
        bgp_controls.setAlignment(Qt.AlignLeft)
        bgp_controls.setSpacing(6)
        bgp_controls.setContentsMargins(6, 4, 6, 4)

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

        def _bgp_btn(icon_name, tooltip, style=BTN_BASE):
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
        self.parent.add_bgp_button            = _bgp_btn("add.png",     "Add BGP")
        self.parent.edit_bgp_button           = _bgp_btn("edit.png",    "Edit BGP Configuration")
        self.parent.delete_bgp_button         = _bgp_btn("remove.png",  "Delete BGP Configuration")
        self.parent.attach_route_pools_button = _bgp_btn("readd.png",   "Attach Route Pools to BGP Neighbor")

        # Runtime group (right)
        self.parent.apply_bgp_button   = _bgp_btn("apply.png",   "Apply BGP configurations to server", style=BTN_APPLY)
        self.parent.bgp_start_button   = _bgp_btn("start.png",   "Start BGP")
        self.parent.bgp_stop_button    = _bgp_btn("stop.png",    "Stop BGP")
        self.parent.bgp_refresh_button = _bgp_btn("refresh.png", "Refresh BGP Status")

        # Wire signals
        self.parent.add_bgp_button.clicked.connect(self.parent.prompt_add_bgp)
        self.parent.edit_bgp_button.clicked.connect(self.parent.prompt_edit_bgp)
        self.parent.delete_bgp_button.clicked.connect(self.parent.prompt_delete_bgp)
        self.parent.attach_route_pools_button.clicked.connect(self.parent.prompt_attach_route_pools)
        self.parent.apply_bgp_button.clicked.connect(self.parent.apply_bgp_configurations)
        self.parent.bgp_start_button.clicked.connect(self.parent.start_bgp_protocol)
        self.parent.bgp_stop_button.clicked.connect(self.parent.stop_bgp_protocol)
        self.parent.bgp_refresh_button.clicked.connect(self.parent.refresh_bgp_status)

        # Layout: config | sep | runtime  (matches Devices action row)
        for b in (self.parent.add_bgp_button, self.parent.edit_bgp_button,
                  self.parent.delete_bgp_button, self.parent.attach_route_pools_button):
            bgp_controls.addWidget(b)

        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        bgp_controls.addSpacing(4)
        bgp_controls.addWidget(sep)
        bgp_controls.addSpacing(4)

        for b in (self.parent.apply_bgp_button, self.parent.bgp_start_button,
                  self.parent.bgp_stop_button, self.parent.bgp_refresh_button):
            bgp_controls.addWidget(b)

        bgp_controls.addStretch(1)
        layout.addWidget(action_bar)


    def refresh_bgp_status(self):
        """Refresh BGP neighbor status.

        Probe scope:
          * If specific BGP-table rows are selected, hit the
            per-device /api/bgp/status/<id> for each unique device —
            cheaper than re-probing the entire fleet, and writes the
            same fields to the DB on the server side.
          * If nothing is selected, fall back to the
            /api/bgp/monitor/force-check fleet-wide probe.

        Either way, kick the probe before re-rendering the table so
        the UI doesn't just re-display whatever the periodic 30s
        monitor last wrote.
        """
        try:
            import requests
            server_url = self.parent.get_server_url(silent=True)
            if not server_url:
                return

            # Collect device_ids from selected BGP rows (column 0 is
            # the Device-name cell; map name → device_id via the
            # client-side device store).
            selected_device_ids = set()
            try:
                tbl = self.parent.bgp_table
                for it in tbl.selectedItems():
                    row = it.row()
                    name_item = tbl.item(row, 0)
                    if not name_item:
                        continue
                    name = name_item.text().strip()
                    for iface, devs in self.parent.main_window.all_devices.items():
                        for d in devs:
                            if d.get("Device Name") == name and d.get("device_id"):
                                selected_device_ids.add(d["device_id"])
                                break
            except Exception as sel_exc:
                logger.debug(f"[BGP REFRESH] selection scan failed: {sel_exc}")

            if selected_device_ids:
                # Selective per-device path — one GET per unique device.
                for did in selected_device_ids:
                    try:
                        r = requests.get(
                            f"{server_url}/api/bgp/status/{did}", timeout=10
                        )
                        if r.status_code == 200:
                            logger.info(f"[BGP REFRESH] selective probe OK for {did}")
                        else:
                            logger.warning(
                                f"[BGP REFRESH] selective probe HTTP {r.status_code} for {did}"
                            )
                    except Exception as exc:
                        logger.warning(
                            f"[BGP REFRESH] selective probe failed for {did}: {exc}"
                        )
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.parent.update_bgp_table())
                logger.info(
                    f"[BGP REFRESH] BGP status refresh scheduled "
                    f"(selective, {len(selected_device_ids)} device(s))"
                )
                return

            # Step 1 — fire the server-side force-check. Synchronous
            # on the server (probes every BGP device in parallel via
            # docker exec vtysh), normally returns in <1s. Failures
            # are non-fatal: we still refresh the table from cache.
            try:
                fc = requests.post(f"{server_url}/api/bgp/monitor/force-check", timeout=15)
                if fc.status_code == 200:
                    logger.info("[BGP REFRESH] force-check OK")
                else:
                    logger.warning(f"[BGP REFRESH] force-check returned HTTP {fc.status_code}")
            except Exception as exc:
                logger.warning(f"[BGP REFRESH] force-check failed (will show cached state): {exc}")

            # Step 2 — defer the table update so the UI doesn't block
            # on click. By the time the QTimer fires the force-check
            # has already updated the DB.
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.parent.update_bgp_table())
            logger.info("[BGP REFRESH] BGP status refresh scheduled (non-blocking)")
        except Exception as e:
            logger.error(f"Error refreshing BGP status: {e}")


    def on_bgp_selection_changed(self):
        """Update attach button tooltip when selection changes."""
        selection_model = self.parent.bgp_table.selectionModel()
        total_rows = self.parent.bgp_table.rowCount()
        selected_count = len(selection_model.selectedRows()) if selection_model else 0
        
        # Keep the same icon, just update tooltip
        if selected_count == total_rows and total_rows > 0:
            self.parent.attach_route_pools_button.setToolTip("Attach Route Pools to All BGP Neighbors")
        else:
            self.parent.attach_route_pools_button.setToolTip("Attach Route Pools to BGP Neighbor")



    def _get_single_bgp_neighbor_state(self, device_id, neighbor_ip, device_info=None):
        """Helper function to get BGP state for a single neighbor (used in parallel execution)."""
        try:
            return self.parent._get_bgp_neighbor_state(device_id, neighbor_ip, device_info)
        except Exception as e:
            logging.error(f"[BGP PARALLEL] Error getting state for {neighbor_ip}: {e}")
            return "Error"


    def _get_bgp_neighbor_state_from_database(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state from database instead of direct server check"""
        try:
            # First check if device is started (ARP is successful)
            if device_info:
                gateway = device_info.get("Gateway", "")
                device_ip = device_info.get("IPv4", "")
                
                # If no device IP configured, device is not configured
                if not device_ip:
                    return "Device Not Configured"
                
                # If no gateway configured, show BGP status but indicate connectivity issue
                if not gateway:
                        return "Gateway Not Configured"
            
            server_url = self.parent.get_server_url(silent=True)
            if not server_url or not device_id:
                return "Unknown"
            
            # Get device information from database instead of direct BGP status
            try:
                response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=1)
                if response.status_code == 200:
                    device_data = response.json()
                    
                    # Check if device is running
                    device_status = device_data.get('status', 'Unknown')
                    if device_status != 'Running':
                        return "Device Not Started"
                    
                    # Get BGP status from database
                    bgp_ipv4_established = device_data.get('bgp_ipv4_established', False)
                    bgp_ipv6_established = device_data.get('bgp_ipv6_established', False)
                    bgp_ipv4_state = device_data.get('bgp_ipv4_state', 'Unknown')
                    bgp_ipv6_state = device_data.get('bgp_ipv6_state', 'Unknown')
                    last_bgp_check = device_data.get('last_bgp_check', '')
                    
                    # Debug logging for BGP status (reduced to prevent UI spam)
                    # print(f"[BGP STATUS DEBUG] Device {device_id}, Neighbor {neighbor_ip}")
                    
                    # Determine if this is IPv4 or IPv6 neighbor
                    is_ipv6 = ':' in neighbor_ip
                    
                    if is_ipv6:
                        # IPv6 neighbor
                        if bgp_ipv6_established:
                            result_status = "Established"
                        else:
                            result_status = bgp_ipv6_state if bgp_ipv6_state != 'Unknown' else "Idle"
                    else:
                        # IPv4 neighbor
                        if bgp_ipv4_established:
                            result_status = "Established"
                        else:
                            result_status = bgp_ipv4_state if bgp_ipv4_state != 'Unknown' else "Idle"
                    
                    # print(f"[BGP STATUS DEBUG] Returning status: {result_status}")
                    return result_status
                else:
                    return "Unknown"
            except Exception:
                # Don't print debug errors to reduce spam
                return "Unknown"
                
        except Exception as e:
            logger.debug(f"[DEBUG] Error getting BGP state for {neighbor_ip}: {e}")
            return "Unknown"


    def _get_bgp_neighbor_state(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state - now uses database instead of direct server check"""
        return self.parent._get_bgp_neighbor_state_from_database(device_id, neighbor_ip, device_info)


    def update_bgp_table(self, neighbors=None):
        """Update the BGP table with neighbor information - one row per neighbor IP."""
        # Don't rebuild while the user has an inline editor open in this
        # table — a rebuild (setRowCount + setItem) would discard the edit
        # and close the editor. Monitoring repaints on its next pass once
        # the editor closes; explicit refreshes run with no editor open,
        # so they're unaffected. (Same class of fix as the Streams table.)
        try:
            from utils.qt_table_guard import table_has_open_editor
            if table_has_open_editor(self.parent.bgp_table):
                return
        except Exception:
            pass

        # Auto-start BGP monitoring if we have BGP devices and monitoring is not active
        if not self.parent.bgp_monitoring_active:
            has_bgp_devices = False
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    if "protocols" in device and "BGP" in device["protocols"]:
                        # Handle both old format (dict) and new format (list)
                        if isinstance(device["protocols"], dict):
                            bgp_config = device["protocols"]["BGP"]
                        else:
                            bgp_config = device.get("bgp_config", {})
                        
                        if not bgp_config.get("_marked_for_removal", False):
                            has_bgp_devices = True
                            break
                if has_bgp_devices:
                    break
            
            if has_bgp_devices:
                logger.info("[BGP AUTO-START] Auto-starting BGP monitoring for existing BGP devices")
                self.parent.start_bgp_monitoring()

        # Block cellChanged signals while we rebuild the table — without
        # this, every setItem() below fires the on_bgp_table_cell_changed
        # handler, which interprets each populate write as a user edit
        # and triggers save_session(). With the periodic BGP monitor
        # auto-started for any BGP device (it polls every ~20–30s), this
        # caused save_session() to fire every 23s — the periodic save
        # noise the user was seeing in the logs. Mirrors the same guard
        # already applied in update_ospf_table / update_isis_table.
        #
        # QSignalBlocker is RAII-style — restores the previous block
        # state when the variable goes out of scope at function return,
        # so we don't have to wrap the rest of the method in try/finally.
        from PyQt5.QtCore import QSignalBlocker
        _bgp_signal_blocker = QSignalBlocker(self.parent.bgp_table)  # noqa: F841

        if neighbors is not None:
            # Update from server data - one row per neighbor
            # OPTIMIZATION: Use setRowCount with final count instead of clear+insertRow for better performance
            neighbor_count = len(neighbors)
            current_rows = self.parent.bgp_table.rowCount()
            
            # Only resize if needed to avoid unnecessary UI updates
            if neighbor_count != current_rows:
                self.parent.bgp_table.setRowCount(neighbor_count)
            
            # Disable sorting temporarily for faster updates.
            # v0.2.91: also snapshot the operator's chosen sort column
            # + direction so the rebuild doesn't blow away their
            # "Sort by State" / "Sort by Local AS" pick.
            was_sorting_enabled = self.parent.bgp_table.isSortingEnabled()
            from utils.table_sort_state import capture_sort_state
            _bgp_sort_state = capture_sort_state(self.parent.bgp_table)
            if was_sorting_enabled:
                self.parent.bgp_table.setSortingEnabled(False)
            
            for row, neighbor in enumerate(neighbors):
                
                # Debug: Check if neighbor is a dict or list
                if not isinstance(neighbor, dict):
                    logger.warning(f"[BGP TABLE DEBUG] Warning: neighbor is not a dict, it's {type(neighbor)}: {neighbor}")
                    continue
                
                device_name = neighbor.get("device", "Unknown")
                neighbor_ip = neighbor.get("neighbor_ip", "")
                neighbor_type = "IPv6" if ":" in neighbor_ip else "IPv4"
                bgp_status = neighbor.get("state", "Idle")
                
                # Device name (column 0)
                self.parent.bgp_table.setItem(row, 0, QTableWidgetItem(device_name))
                
                # BGP Status (column 1) - Icon only, no text or background color
                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                if bgp_status == "Established":
                    bgp_status_item.setIcon(self.parent.green_dot)
                    bgp_status_item.setToolTip("BGP Established")
                elif bgp_status == "Stopping":
                    bgp_status_item.setIcon(self.parent.yellow_dot)
                    bgp_status_item.setToolTip("BGP Stopping")
                elif bgp_status in ["Idle", "Connect", "Active"]:
                    bgp_status_item.setIcon(self.parent.orange_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                elif bgp_status in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured"]:
                    bgp_status_item.setIcon(self.parent.red_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                else:
                    bgp_status_item.setIcon(self.parent.orange_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                self.parent.bgp_table.setItem(row, 1, bgp_status_item)
                
                # Neighbor Type (column 2)
                self.parent.bgp_table.setItem(row, 2, QTableWidgetItem(neighbor_type))
                
                # Neighbor IP (column 3)
                self.parent.bgp_table.setItem(row, 3, QTableWidgetItem(neighbor_ip))
                
                # Source IP (column 4)
                source_ip = neighbor.get("source_ip", "")
                self.parent.bgp_table.setItem(row, 4, QTableWidgetItem(source_ip))
                
                # Local AS (column 5)
                self.parent.bgp_table.setItem(row, 5, QTableWidgetItem(str(neighbor.get("local_as", ""))))
                
                # Remote AS (column 6)
                self.parent.bgp_table.setItem(row, 6, QTableWidgetItem(str(neighbor.get("remote_as", ""))))
                
                # State (column 7)
                self.parent.bgp_table.setItem(row, 7, QTableWidgetItem(neighbor.get("state", "Idle")))
                
                # Routes (column 8)
                self.parent.bgp_table.setItem(row, 8, QTableWidgetItem(str(neighbor.get("routes", 0))))
                
                # Route Pools (column 9) - Try to find device and get route pools
                # OPTIMIZATION: Cache device lookup to avoid repeated nested loops
                route_pools_str = ""
                if not hasattr(self, '_device_cache'):
                    self._device_cache = {}
                if device_name not in self._device_cache:
                    # Build cache on first access
                    for iface, devices in self.parent.main_window.all_devices.items():
                        for dev in devices:
                            dev_name = dev.get("Device Name")
                            if dev_name:
                                self._device_cache[dev_name] = dev
                
                dev = self._device_cache.get(device_name)
                if dev:
                    bgp_cfg = dev.get("bgp_config", {})
                    route_pools = bgp_cfg.get("route_pools", {}).get(neighbor_ip, [])
                    route_pools_str = ", ".join(route_pools) if route_pools else ""
                pool_item = QTableWidgetItem(route_pools_str)
                pool_item.setToolTip(f"Attached route pools: {route_pools_str if route_pools_str else 'None'}")
                self.parent.bgp_table.setItem(row, 9, pool_item)
                
                # Keepalive (column 10) - Default 30 seconds
                keepalive = neighbor.get("keepalive", "30")
                keepalive_item = QTableWidgetItem(str(keepalive))
                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                self.parent.bgp_table.setItem(row, 10, keepalive_item)
                
                # Hold-time (column 11) - Default 90 seconds
                hold_time = neighbor.get("hold_time", "90")
                hold_time_item = QTableWidgetItem(str(hold_time))
                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                self.parent.bgp_table.setItem(row, 11, hold_time_item)
            
            # Re-enable sorting if it was enabled, then restore the
            # operator's pre-rebuild sort column + direction.
            if was_sorting_enabled:
                self.parent.bgp_table.setSortingEnabled(True)
                from utils.table_sort_state import restore_sort_state
                restore_sort_state(self.parent.bgp_table, _bgp_sort_state)
        else:
            # Update from device configurations - one row per neighbor IP
            try:
                # Updating BGP table from device configurations
                
                # Get selected interfaces from server_tree (same logic as device table)
                selected_interfaces = set()
                if hasattr(self.parent.main_window, 'server_tree') and self.parent.main_window.server_tree:
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
                
                # Using selected interfaces or all devices
                
                self.parent.bgp_table.setRowCount(0)
                
                bgp_device_count = 0
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
                        if "protocols" in device and "BGP" in device["protocols"]:
                            # Handle both old format (dict) and new format (list)
                            if isinstance(device["protocols"], dict):
                                bgp_config = device["protocols"]["BGP"]
                            else:
                                bgp_config = device.get("bgp_config", {})
                            
                            device_name = device.get("Device Name", "")
                            bgp_device_count += 1
                            
                            # Check if device is marked for removal
                            is_marked_for_removal = bgp_config.get("_marked_for_removal", False)
                            if is_marked_for_removal:
                                # Still show the device in the table but mark it as pending removal
                                pass
                            
                            # Get IPv4 neighbor IPs
                            ipv4_neighbors = bgp_config.get("bgp_neighbor_ipv4", "")
                            ipv4_ips = [ip.strip() for ip in ipv4_neighbors.split(",") if ip.strip()] if ipv4_neighbors else []
                            
                            # Get IPv6 neighbor IPs
                            ipv6_neighbors = bgp_config.get("bgp_neighbor_ipv6", "")
                            ipv6_ips = [ip.strip() for ip in ipv6_neighbors.split(",") if ip.strip()] if ipv6_neighbors else []
                            
                            # Create rows for IPv4 neighbors
                            for ipv4_ip in ipv4_ips:
                                row = self.parent.bgp_table.rowCount()
                                self.parent.bgp_table.insertRow(row)
                                
                                # Device name (column 0) - show status for removal
                                display_name = f"{device_name} (Pending Removal)" if is_marked_for_removal else device_name
                                self.parent.bgp_table.setItem(row, 0, QTableWidgetItem(display_name))
                                
                                # BGP Status (column 1) - Icon only, no text or background color
                                bgp_state = self.parent._get_bgp_neighbor_state(device.get("device_id"), ipv4_ip, device)
                                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                                if bgp_state == "Established":
                                    bgp_status_item.setIcon(self.parent.green_dot)
                                    bgp_status_item.setToolTip("BGP Established")
                                elif bgp_state in ["Idle", "Connect", "Active"]:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif bgp_state in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured", "Device Not Started"]:
                                    bgp_status_item.setIcon(self.parent.red_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif "No Gateway" in bgp_state:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(bgp_state)
                                else:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                                self.parent.bgp_table.setItem(row, 1, bgp_status_item)
                                
                                # Neighbor Type (column 2)
                                self.parent.bgp_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                
                                # Neighbor IP (column 3)
                                self.parent.bgp_table.setItem(row, 3, QTableWidgetItem(ipv4_ip))
                                
                                # Source IP (column 4)
                                source_ipv4 = bgp_config.get("bgp_update_source_ipv4", "")
                                self.parent.bgp_table.setItem(row, 4, QTableWidgetItem(source_ipv4))
                                
                                # Local AS (column 5)
                                self.parent.bgp_table.setItem(row, 5, QTableWidgetItem(bgp_config.get("bgp_asn", "")))
                                
                                # Remote AS (column 6)
                                self.parent.bgp_table.setItem(row, 6, QTableWidgetItem(bgp_config.get("bgp_remote_asn", "")))
                                
                                # State (column 7) - get real BGP state
                                self.parent.bgp_table.setItem(row, 7, QTableWidgetItem(bgp_state))
                                
                                # Routes (column 8)
                                self.parent.bgp_table.setItem(row, 8, QTableWidgetItem("0"))
                                
                                # Route Pools (column 9) - show attached pool names
                                route_pools = bgp_config.get("route_pools", {}).get(ipv4_ip, [])
                                pool_names = ", ".join(route_pools) if route_pools else ""
                                pool_item = QTableWidgetItem(pool_names)
                                pool_item.setToolTip(f"Attached route pools: {pool_names if pool_names else 'None'}")
                                self.parent.bgp_table.setItem(row, 9, pool_item)
                                
                                # Keepalive (column 10) - Default 30 seconds
                                keepalive = bgp_config.get("bgp_keepalive", "30")
                                keepalive_item = QTableWidgetItem(str(keepalive))
                                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                                self.parent.bgp_table.setItem(row, 10, keepalive_item)
                                
                                # Hold-time (column 11) - Default 90 seconds
                                hold_time = bgp_config.get("bgp_hold_time", "90")
                                hold_time_item = QTableWidgetItem(str(hold_time))
                                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                                self.parent.bgp_table.setItem(row, 11, hold_time_item)
                            
                            # Create rows for IPv6 neighbors
                            for ipv6_ip in ipv6_ips:
                                row = self.parent.bgp_table.rowCount()
                                self.parent.bgp_table.insertRow(row)
                                
                                # Device name (column 0) - show status for removal
                                display_name = f"{device_name} (Pending Removal)" if is_marked_for_removal else device_name
                                self.parent.bgp_table.setItem(row, 0, QTableWidgetItem(display_name))
                                
                                # BGP Status (column 1) - Icon only, no text or background color
                                bgp_state = self.parent._get_bgp_neighbor_state(device.get("device_id"), ipv6_ip, device)
                                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                                if bgp_state == "Established":
                                    bgp_status_item.setIcon(self.parent.green_dot)
                                    bgp_status_item.setToolTip("BGP Established")
                                elif bgp_state in ["Idle", "Connect", "Active"]:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif bgp_state in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured", "Device Not Started"]:
                                    bgp_status_item.setIcon(self.parent.red_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif "No Gateway" in bgp_state:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(bgp_state)
                                else:
                                    bgp_status_item.setIcon(self.parent.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                                self.parent.bgp_table.setItem(row, 1, bgp_status_item)
                                
                                # Neighbor Type (column 2)
                                self.parent.bgp_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                                
                                # Neighbor IP (column 3)
                                self.parent.bgp_table.setItem(row, 3, QTableWidgetItem(ipv6_ip))
                                
                                # Source IP (column 4)
                                source_ipv6 = bgp_config.get("bgp_update_source_ipv6", "")
                                self.parent.bgp_table.setItem(row, 4, QTableWidgetItem(source_ipv6))
                                
                                # Local AS (column 5)
                                self.parent.bgp_table.setItem(row, 5, QTableWidgetItem(bgp_config.get("bgp_asn", "")))
                                
                                # Remote AS (column 6)
                                self.parent.bgp_table.setItem(row, 6, QTableWidgetItem(bgp_config.get("bgp_remote_asn", "")))
                                
                                # State (column 7) - get real BGP state
                                self.parent.bgp_table.setItem(row, 7, QTableWidgetItem(bgp_state))
                                
                                # Routes (column 8)
                                self.parent.bgp_table.setItem(row, 8, QTableWidgetItem("0"))
                                
                                # Route Pools (column 9) - show attached pool names
                                route_pools = bgp_config.get("route_pools", {}).get(ipv6_ip, [])
                                pool_names = ", ".join(route_pools) if route_pools else ""
                                pool_item = QTableWidgetItem(pool_names)
                                pool_item.setToolTip(f"Attached route pools: {pool_names if pool_names else 'None'}")
                                self.parent.bgp_table.setItem(row, 9, pool_item)
                                
                                # Keepalive (column 10) - Default 30 seconds
                                keepalive = bgp_config.get("bgp_keepalive", "30")
                                keepalive_item = QTableWidgetItem(str(keepalive))
                                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                                self.parent.bgp_table.setItem(row, 10, keepalive_item)
                                
                                # Hold-time (column 11) - Default 90 seconds
                                hold_time = bgp_config.get("bgp_hold_time", "90")
                                hold_time_item = QTableWidgetItem(str(hold_time))
                                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                                self.parent.bgp_table.setItem(row, 11, hold_time_item)
                
                # BGP table updated
            except Exception as e:
                logger.error(f"Error updating BGP table: {e}")
    

    def _safe_update_bgp_table(self):
        """Safely update BGP table (for parallel execution)."""
        try:
            logger.info("[PROTOCOL REFRESH] Refreshing BGP table...")
            self.parent.update_bgp_table()
        except Exception as e:
            logging.error(f"[BGP REFRESH ERROR] {e}")
    

    def _apply_bgp_to_server_sync(self, server_url, device_info):
        """Apply BGP configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            
            # Get BGP config - handle both old dict format and new separate config format
            bgp_config = device_info.get("bgp_config", {})
            if not bgp_config:
                # Try old format for backward compatibility
                protocols = device_info.get("protocols", {})
                if isinstance(protocols, dict):
                    bgp_config = protocols.get("BGP", {})
            
            if not bgp_config:
                return True  # No BGP config to apply
            
            # Get IPv4/IPv6 from device_info, with fallback to database if not available
            # This is important for DHCP server devices where IPv4 might not be in device_info
            ipv4 = device_info.get("IPv4", "")
            ipv6 = device_info.get("IPv6", "")
            
            # If IPv4/IPv6 not in device_info, try to get from database
            if not ipv4 or not ipv6:
                try:
                    import requests
                    device_response = requests.get(f"{server_url}/api/device/{device_id}", timeout=10)
                    if device_response.status_code == 200:
                        device_data = device_response.json()
                        if not ipv4:
                            ipv4 = device_data.get("ipv4_address", "")
                        if not ipv6:
                            ipv6 = device_data.get("ipv6_address", "")
                except Exception as e:
                    logging.warning(f"[BGP APPLY] Could not retrieve device data from server: {e}")
            
            # Prepare BGP payload using the configure endpoint (same as the original)
            bgp_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": ipv4,
                "ipv6": ipv6,
                "ipv4_mask": device_info.get("ipv4_mask", "24"),
                "ipv6_mask": device_info.get("ipv6_mask", "64"),
                "gateway": device_info.get("Gateway", ""),  # Keep for backward compatibility
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),  # Include IPv4 gateway for static route
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),  # Include IPv6 gateway for static route
                "bgp_config": bgp_config,
                "all_route_pools": getattr(self.parent.main_window, 'bgp_route_pools', [])  # Include all route pools for generation
            }
            
            # Make synchronous request to the configure endpoint
            response = requests.post(f"{server_url}/api/device/bgp/configure", json=bgp_payload, timeout=30)
            if response.status_code == 200:
                return True

            # Surface the actual server error so the caller can show
            # something more useful than "BGP configuration failed
            # (check server logs)". Try to extract a structured
            # `error` field first; fall back to the raw response
            # body (truncated). Stashed on device_info["_apply_error"]
            # so devices_tab.py's apply path picks it up.
            err_msg = f"HTTP {response.status_code}"
            try:
                body = response.json()
                if isinstance(body, dict):
                    err_msg = body.get("error") or body.get("message") or err_msg
                    # Some BGP errors carry a `details` field with
                    # the FRR vtysh stderr — include it for context.
                    details = body.get("details") or body.get("stderr")
                    if details:
                        err_msg = f"{err_msg} — {str(details)[:200]}"
            except Exception:
                # Non-JSON or empty body
                if response.text:
                    err_msg = f"{err_msg}: {response.text[:200]}"
            full_err = f"BGP configure: {err_msg}"
            logger.error(
                f"[BGP APPLY] {device_name} configure failed → {full_err}"
            )
            device_info["_apply_error"] = full_err
            return False

        except Exception as e:
            logger.error(f"Exception in sync BGP apply for '{device_name}': {e}")
            device_info["_apply_error"] = f"BGP configure exception: {e}"
            return False
    

    def _set_bgp_interim_stopping_state(self, device_name, selected_neighbors):
        """Set interim 'Stopping' state for selected BGP neighbors."""
        logger.info(f"[BGP INTERIM] Setting 'Stopping' state for device {device_name}, neighbors: {selected_neighbors}")
        
        # Find rows in BGP table that match the device and selected neighbors
        for row in range(self.parent.bgp_table.rowCount()):
            device_item = self.parent.bgp_table.item(row, 0)  # Device column
            neighbor_item = self.parent.bgp_table.item(row, 3)  # Neighbor IP column
            
            if device_item and neighbor_item:
                table_device_name = device_item.text()
                table_neighbor_ip = neighbor_item.text()
                
                # Remove "(Pending Removal)" suffix if present
                if " (Pending Removal)" in table_device_name:
                    table_device_name = table_device_name.replace(" (Pending Removal)", "")
                
                # Check if this row matches our device
                if table_device_name == device_name:
                    # If specific neighbors are selected, only set stopping for those
                    # If no specific neighbors, set stopping for all neighbors of this device
                    if not selected_neighbors or table_neighbor_ip in selected_neighbors:
                        # Set the status to "Stopping" with yellow dot
                        status_item = QTableWidgetItem("")
                        status_item.setIcon(self.parent.yellow_dot)
                        status_item.setToolTip("BGP Stopping")
                        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
                        self.parent.bgp_table.setItem(row, 1, status_item)
                        
                        logger.info(f"[BGP INTERIM] Set 'Stopping' state for {table_device_name} -> {table_neighbor_ip}")


    def prompt_attach_route_pools(self):
        """Open dialog to attach route pools to selected BGP neighbors (Step 2: Attach to BGP)."""
        # Get selection from BGP table (not devices table)
        selected_items = self.parent.bgp_table.selectedItems()
        if not selected_items:
            # No rows selected - select all rows
            total_rows = self.parent.bgp_table.rowCount()
            if total_rows > 0:
                self.parent.bgp_table.selectAll()
                logger.info(f"[BGP TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self.parent, "No BGP Neighbors", "No BGP neighbors are configured. Please add BGP neighbors first.")
                return
        
        # Get available route pools
        if not hasattr(self.parent.main_window, 'bgp_route_pools'):
            self.parent.main_window.bgp_route_pools = []
        
        available_pools = self.parent.main_window.bgp_route_pools
        
        if not available_pools:
            QMessageBox.warning(self.parent, "No Route Pools", 
                              "No route pools have been defined.\n\n"
                              "Please use 🗂️ 'Manage Route Pools' button (in Devices tab) to create pools first.")
            return
        
        # Collect all selected BGP neighbors
        selected_neighbors = []
        processed_devices = set()
        
        for item in selected_items:
            row = item.row()
            device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
            neighbor_ip = self.parent.bgp_table.item(row, 3).text()  # Neighbor IP column
            
            # Clean device name - remove any suffixes like "(Pending Removal)"
            clean_device_name = device_name.split(" (")[0].strip()
            if clean_device_name != device_name:
                device_name = clean_device_name
            
            # Avoid duplicates
            neighbor_key = f"{device_name}:{neighbor_ip}"
            if neighbor_key in processed_devices:
                continue
            processed_devices.add(neighbor_key)
            
            # Find device in all_devices using safe helper
            device_info = self.parent._find_device_by_name(device_name)
            
            if not device_info:
                logger.warning(f"[BGP ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue
            
            # Ensure device_info is a dictionary - handle list case
            if not isinstance(device_info, dict):
                logger.warning(f"[BGP ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                # Try to extract dict from list if it's a list
                if isinstance(device_info, list) and len(device_info) > 0:
                    logger.info(f"[BGP ROUTE POOLS] Attempting to extract dict from list...")
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        logger.info(f"[BGP ROUTE POOLS] Could not extract dict from list for '{device_name}'")
                        continue
                else:
                    continue
            
            # Final check - ensure device_info is now a dict
            if not isinstance(device_info, dict):
                logger.error(f"[BGP ROUTE POOLS] Final check failed: device_info is still not a dict for '{device_name}'")
                continue
            
            # Get BGP config for this device
            # Check if BGP is in the protocols list
            protocols = device_info.get("protocols", [])
            if not isinstance(protocols, list) or "BGP" not in protocols:
                logger.warning(f"[BGP ROUTE POOLS] Warning: Device '{device_name}' does not have BGP configured")
                continue
            
            # Get the actual BGP configuration
            bgp_config = device_info.get("bgp_config", {})
            if not bgp_config:
                logger.warning(f"[BGP ROUTE POOLS] Warning: Device '{device_name}' does not have BGP configuration")
                continue
            
            selected_neighbors.append({
                "device_name": device_name,
                "neighbor_ip": neighbor_ip,
                "device_info": device_info,
                "bgp_config": bgp_config
            })
        
        if not selected_neighbors:
            QMessageBox.warning(self.parent, "No Valid BGP Neighbors", 
                              "No valid BGP neighbors found in the selection.")
            return
        
        # If only one neighbor, use the original dialog
        if len(selected_neighbors) == 1:
            neighbor = selected_neighbors[0]
            device_name = neighbor["device_name"]
            neighbor_ip = neighbor["neighbor_ip"]
            bgp_config = neighbor["bgp_config"]
            
            # Get existing attached pool names for this BGP neighbor
            if "route_pools" not in bgp_config:
                bgp_config["route_pools"] = {}
            
            attached_pool_names = bgp_config["route_pools"].get(neighbor_ip, [])
            
            # Open dialog
            from widgets.add_bgp_route_dialog import AttachRoutePoolsDialog
            dialog = AttachRoutePoolsDialog(self.parent, 
                                            device_name=f"{device_name} → {neighbor_ip}", 
                                            available_pools=available_pools,
                                            attached_pools=attached_pool_names,
                                            bgp_config=bgp_config)
            if dialog.exec_() != dialog.Accepted:
                return
            
            # Get selected pools
            selected_pools = dialog.get_attached_pools()
            
            # Save to BGP config (per neighbor IP)
            bgp_config["route_pools"][neighbor_ip] = selected_pools
            
            # Mark device as needing apply
            neighbor["device_info"]["_needs_apply"] = True
            
            # Save to session
            self.parent.main_window.save_session()
            
            # Refresh BGP table to show updated pool assignments
            self.parent.update_bgp_table()
            
            # Calculate total routes
            total_routes = 0
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
            
            logger.info(f"[BGP ROUTE POOLS] Attached {len(selected_pools)} pool(s) ({total_routes} routes) to BGP neighbor {neighbor_ip} on device '{device_name}'")
            QMessageBox.information(self.parent, "Route Pools Attached", 
                                  f"Attached {len(selected_pools)} route pool(s) to BGP neighbor {neighbor_ip}.\n\n"
                                  f"Device: {device_name}\n"
                                  f"Total routes to advertise: {total_routes}\n\n"
                                  f"Click 'Apply BGP' to configure routes on server.")
            return
        
        # Multiple neighbors selected - show dialog for bulk attachment
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QLabel,
            QListWidget,
            QDialogButtonBox,
            QGroupBox,
        )
        
        class BulkAttachRoutePoolsDialog(QDialog):
            def __init__(self, parent, selected_neighbors, available_pools):
                super().__init__(parent)
                self.selected_neighbors = selected_neighbors
                self.available_pools = available_pools
                self.setWindowTitle("Attach Route Pools to Multiple BGP Neighbors")
                self.setFixedSize(600, 400)
                self.setup_ui()
            
            def setup_ui(self):
                layout = QVBoxLayout(self)
                
                # Selected neighbors info
                neighbors_group = QGroupBox("Selected BGP Neighbors")
                neighbors_layout = QVBoxLayout(neighbors_group)
                
                neighbors_text = f"Selected {len(self.selected_neighbors)} BGP neighbor(s)"
                neighbors_label = QLabel(neighbors_text)
                neighbors_label.setWordWrap(True)
                neighbors_layout.addWidget(neighbors_label)
                layout.addWidget(neighbors_group)
                
                # Available pools
                pools_group = QGroupBox("Available Route Pools")
                pools_layout = QVBoxLayout(pools_group)
                
                self.pools_list = QListWidget()
                self.pools_list.setSelectionMode(QListWidget.MultiSelection)
                
                for pool in self.available_pools:
                    pool_item = f"{pool['name']} - {pool['subnet']} ({pool['count']} routes)"
                    self.pools_list.addItem(pool_item)
                
                pools_layout.addWidget(self.pools_list)
                layout.addWidget(pools_group)
                
                # Buttons
                button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                button_box.accepted.connect(self.accept)
                button_box.rejected.connect(self.reject)
                layout.addWidget(button_box)
            
            def get_selected_pools(self):
                selected_items = self.pools_list.selectedItems()
                selected_pool_names = []
                for item in selected_items:
                    pool_name = item.text().split(" - ")[0]
                    selected_pool_names.append(pool_name)
                return selected_pool_names
        
        # Open bulk dialog
        dialog = BulkAttachRoutePoolsDialog(self.parent, selected_neighbors, available_pools)
        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get selected pools
        selected_pools = dialog.get_selected_pools()
        
        if not selected_pools:
            QMessageBox.warning(self.parent, "No Pools Selected", "Please select at least one route pool to attach.")
            return
        
        # Apply to all selected neighbors
        total_neighbors = 0
        total_routes = 0
        
        for neighbor in selected_neighbors:
            device_name = neighbor["device_name"]
            neighbor_ip = neighbor["neighbor_ip"]
            bgp_config = neighbor["bgp_config"]
            
            # Initialize route_pools if not exists
            if "route_pools" not in bgp_config:
                bgp_config["route_pools"] = {}
            
            # Save to BGP config (per neighbor IP)
            bgp_config["route_pools"][neighbor_ip] = selected_pools
            
            # Mark device as needing apply
            neighbor["device_info"]["_needs_apply"] = True
            
            total_neighbors += 1
            
            # Calculate routes for this neighbor
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
        
        # Save to session
        self.parent.main_window.save_session()
        
        # Refresh BGP table to show updated pool assignments
        self.parent.update_bgp_table()
        
        logger.info(f"[BGP ROUTE POOLS] Attached {len(selected_pools)} pool(s) to {total_neighbors} BGP neighbor(s)")
        QMessageBox.information(self.parent, "Route Pools Attached", 
                              f"Successfully attached {len(selected_pools)} route pool(s) to {total_neighbors} BGP neighbor(s).\n\n"
                              f"Total routes to advertise: {total_routes}\n\n"
                              f"Click 'Apply BGP' to configure routes on server.")


    def prompt_add_bgp(self):
        """Add BGP configuration to selected device."""
        selected_items = self.parent.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select a device to add BGP configuration.")
            return

        row = selected_items[0].row()
        device_name = self.parent.devices_table.item(row, self.parent.COL["Device Name"]).text()
        
        # Get device IP addresses and gateway addresses from the table
        device_ipv4 = self.parent.devices_table.item(row, self.parent.COL["IPv4"]).text() if self.parent.devices_table.item(row, self.parent.COL["IPv4"]) else ""
        device_ipv6 = self.parent.devices_table.item(row, self.parent.COL["IPv6"]).text() if self.parent.devices_table.item(row, self.parent.COL["IPv6"]) else ""
        gateway_ipv4 = self.parent.devices_table.item(row, self.parent.COL["IPv4 Gateway"]).text() if self.parent.devices_table.item(row, self.parent.COL["IPv4 Gateway"]) else ""
        gateway_ipv6 = self.parent.devices_table.item(row, self.parent.COL["IPv6 Gateway"]).text() if self.parent.devices_table.item(row, self.parent.COL["IPv6 Gateway"]) else ""
        
        from widgets.add_bgp_dialog import AddBgpDialog
        dialog = AddBgpDialog(self.parent, device_name, edit_mode=False, device_ipv4=device_ipv4, device_ipv6=device_ipv6, gateway_ipv4=gateway_ipv4, gateway_ipv6=gateway_ipv6)
        if dialog.exec_() != dialog.Accepted:
            return

        bgp_config = dialog.get_values()
        
        # Update the device with BGP configuration
        self.parent._update_device_protocol(row, "BGP", bgp_config)


    def prompt_edit_bgp(self):
        """Edit BGP configuration for selected device."""
        selected_items = self.parent.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select a BGP configuration to edit.")
            return

        # Get unique rows from selection
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if len(selected_rows) > 1:
            QMessageBox.warning(self.parent, "Multiple Selection", "Please select only one BGP configuration to edit.")
            return
        
        row = list(selected_rows)[0]
        device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
        
        # Detect which address family is selected (IPv4 or IPv6)
        neighbor_type_item = self.parent.bgp_table.item(row, 2)  # Column 2 is "Neighbor Type"
        if neighbor_type_item:
            protocol_type = neighbor_type_item.text().strip()
            is_ipv6 = protocol_type == "IPv6"
        else:
            # Fallback: assume IPv4 if not found
            is_ipv6 = False
        
        # Find the device in all_devices using safe helper
        device_info = self.parent._find_device_by_name(device_name)
        
        if not device_info or "protocols" not in device_info or "BGP" not in device_info["protocols"]:
            QMessageBox.warning(self.parent, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")
            return

        # Get device IP addresses and gateway addresses from device_info
        device_ipv4 = device_info.get("IPv4", "")
        device_ipv6 = device_info.get("IPv6", "")
        gateway_ipv4 = device_info.get("IPv4 Gateway", "")
        gateway_ipv6 = device_info.get("IPv6 Gateway", "")

        # Handle both old format (dict) and new format (list)
        if isinstance(device_info["protocols"], dict):
            current_bgp = device_info["protocols"]["BGP"]
        else:
            current_bgp = device_info.get("bgp_config", {})
        
        # Create dialog with current BGP configuration in edit mode
        from widgets.add_bgp_dialog import AddBgpDialog
        dialog = AddBgpDialog(self.parent, device_name, edit_mode=True, device_ipv4=device_ipv4, device_ipv6=device_ipv6, gateway_ipv4=gateway_ipv4, gateway_ipv6=gateway_ipv6)
        
        # Pre-populate the dialog with current values (common fields)
        dialog.bgp_mode_combo.setCurrentText(current_bgp.get("bgp_mode", "eBGP"))
        dialog.bgp_asn_input.setText(current_bgp.get("bgp_asn", ""))
        dialog.bgp_remote_asn_input.setText(current_bgp.get("bgp_remote_asn", ""))
        
        # Pre-populate timer fields
        dialog.bgp_keepalive_input.setValue(int(current_bgp.get("bgp_keepalive", "30")))
        dialog.bgp_hold_time_input.setValue(int(current_bgp.get("bgp_hold_time", "90")))
        
        # Pre-populate only the selected address family's fields
        if is_ipv6:
            # Editing IPv6 row - only show IPv6 fields
            dialog.ipv4_enabled.setChecked(False)
            dialog.ipv6_enabled.setChecked(True)
            
            # Get the specific IPv6 neighbor IP from the selected row
            neighbor_ip_item = self.parent.bgp_table.item(row, 3)  # Column 3 is "Neighbor IP"
            if neighbor_ip_item:
                selected_neighbor_ipv6 = neighbor_ip_item.text().strip()
                # Get all IPv6 neighbors and update the selected one
                all_ipv6_neighbors = [ip.strip() for ip in current_bgp.get("bgp_neighbor_ipv6", "").split(",") if ip.strip()]
                # Pre-populate with all IPv6 neighbors (the selected one will be updated)
                dialog.bgp_neighbor_ipv6_input.setText(current_bgp.get("bgp_neighbor_ipv6", ""))
                dialog.bgp_update_source_ipv6_input.setText(current_bgp.get("bgp_update_source_ipv6", ""))
            else:
                dialog.bgp_neighbor_ipv6_input.setText(current_bgp.get("bgp_neighbor_ipv6", ""))
                dialog.bgp_update_source_ipv6_input.setText(current_bgp.get("bgp_update_source_ipv6", ""))
            
            # Clear IPv4 fields
            dialog.bgp_neighbor_ipv4_input.clear()
            dialog.bgp_update_source_ipv4_input.clear()
        else:
            # Editing IPv4 row - only show IPv4 fields
            dialog.ipv4_enabled.setChecked(True)
            dialog.ipv6_enabled.setChecked(False)
            
            # Get the specific IPv4 neighbor IP from the selected row
            neighbor_ip_item = self.parent.bgp_table.item(row, 3)  # Column 3 is "Neighbor IP"
            if neighbor_ip_item:
                selected_neighbor_ipv4 = neighbor_ip_item.text().strip()
                # Get all IPv4 neighbors and update the selected one
                all_ipv4_neighbors = [ip.strip() for ip in current_bgp.get("bgp_neighbor_ipv4", "").split(",") if ip.strip()]
                # Pre-populate with all IPv4 neighbors (the selected one will be updated)
                dialog.bgp_neighbor_ipv4_input.setText(current_bgp.get("bgp_neighbor_ipv4", ""))
                dialog.bgp_update_source_ipv4_input.setText(current_bgp.get("bgp_update_source_ipv4", ""))
            else:
                dialog.bgp_neighbor_ipv4_input.setText(current_bgp.get("bgp_neighbor_ipv4", ""))
                dialog.bgp_update_source_ipv4_input.setText(current_bgp.get("bgp_update_source_ipv4", ""))
            
            # Clear IPv6 fields
            dialog.bgp_neighbor_ipv6_input.clear()
            dialog.bgp_update_source_ipv6_input.clear()
        
        if dialog.exec_() != dialog.Accepted:
            return

        new_bgp_config = dialog.get_values()
        
        # Preserve existing route pools when editing
        if "route_pools" in current_bgp:
            new_bgp_config["route_pools"] = current_bgp["route_pools"]
        
        # Merge with existing BGP config to preserve the other address family
        merged_bgp_config = current_bgp.copy()
        
        # Only update the selected address family's configuration
        if is_ipv6:
            # Update IPv6 fields only
            merged_bgp_config["bgp_neighbor_ipv6"] = new_bgp_config.get("bgp_neighbor_ipv6", "")
            merged_bgp_config["bgp_update_source_ipv6"] = new_bgp_config.get("bgp_update_source_ipv6", "")
            merged_bgp_config["ipv6_enabled"] = new_bgp_config.get("ipv6_enabled", True)
            # Preserve IPv4 fields
            merged_bgp_config["bgp_neighbor_ipv4"] = current_bgp.get("bgp_neighbor_ipv4", "")
            merged_bgp_config["bgp_update_source_ipv4"] = current_bgp.get("bgp_update_source_ipv4", "")
            merged_bgp_config["ipv4_enabled"] = current_bgp.get("ipv4_enabled", False)
        else:
            # Update IPv4 fields only
            merged_bgp_config["bgp_neighbor_ipv4"] = new_bgp_config.get("bgp_neighbor_ipv4", "")
            merged_bgp_config["bgp_update_source_ipv4"] = new_bgp_config.get("bgp_update_source_ipv4", "")
            merged_bgp_config["ipv4_enabled"] = new_bgp_config.get("ipv4_enabled", True)
            # Preserve IPv6 fields
            merged_bgp_config["bgp_neighbor_ipv6"] = current_bgp.get("bgp_neighbor_ipv6", "")
            merged_bgp_config["bgp_update_source_ipv6"] = current_bgp.get("bgp_update_source_ipv6", "")
            merged_bgp_config["ipv6_enabled"] = current_bgp.get("ipv6_enabled", False)
        
        # Update common fields (these apply to both families)
        merged_bgp_config["bgp_mode"] = new_bgp_config.get("bgp_mode", merged_bgp_config.get("bgp_mode", "eBGP"))
        merged_bgp_config["bgp_asn"] = new_bgp_config.get("bgp_asn", merged_bgp_config.get("bgp_asn", ""))
        merged_bgp_config["bgp_remote_asn"] = new_bgp_config.get("bgp_remote_asn", merged_bgp_config.get("bgp_remote_asn", ""))
        merged_bgp_config["bgp_keepalive"] = new_bgp_config.get("bgp_keepalive", merged_bgp_config.get("bgp_keepalive", "30"))
        merged_bgp_config["bgp_hold_time"] = new_bgp_config.get("bgp_hold_time", merged_bgp_config.get("bgp_hold_time", "90"))
        
        # Update the device with merged BGP configuration
        if isinstance(device_info["protocols"], dict):
            device_info["protocols"]["BGP"] = merged_bgp_config
        else:
            device_info["bgp_config"] = merged_bgp_config
        
        # Update the device using the protocol update method
        self.parent._update_device_protocol(device_name, "BGP", merged_bgp_config)
        
        # Update the BGP table
        self.parent.update_bgp_table()
        
        # Save session
        if hasattr(self.parent.main_window, "save_session"):
            self.parent.main_window.save_session()


    def prompt_delete_bgp(self):
        """Delete BGP configuration for selected device."""
        selected_items = self.parent.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select a BGP configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self.parent, "Confirm Deletion", 
                                   f"Are you sure you want to delete BGP configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices using safe helper
        device_info = self.parent._find_device_by_name(device_name)
        
        if device_info and "protocols" in device_info and "BGP" in device_info["protocols"]:
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove BGP configuration from server first
                server_url = self.parent.get_server_url()
                if server_url:
                    try:
                        # Call server BGP cleanup endpoint
                        response = requests.post(f"{server_url}/api/bgp/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            logger.info(f"BGP configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            logger.error(f"Server BGP cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Network error removing BGP from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    logger.warning("No server URL available, removing BGP configuration locally only")
            
            # Mark BGP for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if isinstance(device_info["protocols"], dict):
                device_info["protocols"]["BGP"] = {"_marked_for_removal": True}
            else:
                device_info["bgp_config"] = {"_marked_for_removal": True}
            
            # Update the BGP table to show the device as marked for removal
            self.parent.update_bgp_table()
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()
            
            QMessageBox.information(self.parent, "BGP Configuration Marked for Removal", 
                                  f"BGP configuration for '{device_name}' has been marked for removal. Click 'Apply BGP Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self.parent, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")


    def on_bgp_table_cell_changed(self, row, column):
        """Handle cell changes in BGP table - handles inline editing with separate rows per neighbor."""
        # Get table items with null checks
        device_item = self.parent.bgp_table.item(row, 0)
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
        
        if device_info and "protocols" in device_info and "BGP" in device_info["protocols"]:
            # Handle both old format (dict) and new format (list)
            if isinstance(device_info["protocols"], dict):
                bgp_config = device_info["protocols"]["BGP"]
            else:
                bgp_config = device_info.get("bgp_config", {})
            
            # Get current neighbor IPs
            ipv4_neighbors = bgp_config.get("bgp_neighbor_ipv4", "")
            ipv4_ips = [ip.strip() for ip in ipv4_neighbors.split(",") if ip.strip()] if ipv4_neighbors else []
            
            ipv6_neighbors = bgp_config.get("bgp_neighbor_ipv6", "")
            ipv6_ips = [ip.strip() for ip in ipv6_neighbors.split(",") if ip.strip()] if ipv6_neighbors else []
            
            if column == 3:  # Neighbor IP changed (column 3 after adding BGP Status)
                neighbor_ip_item = self.parent.bgp_table.item(row, 3)
                neighbor_type_item = self.parent.bgp_table.item(row, 2)
                
                if neighbor_ip_item and neighbor_type_item:
                    neighbor_ip = neighbor_ip_item.text().strip()
                    neighbor_type = neighbor_type_item.text()
                    
                    # Validate IP address format
                    try:
                        if neighbor_type == "IPv4":
                            if neighbor_ip:  # Only validate if not empty
                                ipaddress.IPv4Address(neighbor_ip)
                        elif neighbor_type == "IPv6":
                            if neighbor_ip:  # Only validate if not empty
                                ipaddress.IPv6Address(neighbor_ip)
                    except ipaddress.AddressValueError:
                        QMessageBox.warning(self.parent, f"Invalid {neighbor_type} Address", 
                                          f"'{neighbor_ip}' is not a valid {neighbor_type} address.")
                        # Revert to original value
                        if neighbor_type == "IPv4" and ipv4_ips:
                            neighbor_ip_item.setText(ipv4_ips[0] if ipv4_ips else "")
                        elif neighbor_type == "IPv6" and ipv6_ips:
                            neighbor_ip_item.setText(ipv6_ips[0] if ipv6_ips else "")
                        return
                    
                    if neighbor_type == "IPv4":
                        # Update IPv4 neighbor IPs - find the correct IPv4 row index
                        # IPv4 rows come first, so we need to find which IPv4 row this is
                        ipv4_row_index = 0
                        for i in range(row):
                            if self.parent.bgp_table.item(i, 1) and self.parent.bgp_table.item(i, 1).text() == "IPv4":
                                ipv4_row_index += 1
                        
                        # Replace the IP at the correct IPv4 index
                        if ipv4_row_index < len(ipv4_ips):
                            ipv4_ips[ipv4_row_index] = neighbor_ip
                        else:
                            # If index is beyond current list, add new IP
                            ipv4_ips.append(neighbor_ip)
                        
                        bgp_config["bgp_neighbor_ipv4"] = ",".join(ipv4_ips)
                    elif neighbor_type == "IPv6":
                        # Update IPv6 neighbor IPs - find the correct IPv6 row index
                        # IPv6 rows come after IPv4 rows, so we need to find which IPv6 row this is
                        ipv6_row_index = 0
                        for i in range(row):
                            if self.parent.bgp_table.item(i, 1) and self.parent.bgp_table.item(i, 1).text() == "IPv6":
                                ipv6_row_index += 1
                        
                        # Replace the IP at the correct IPv6 index
                        if ipv6_row_index < len(ipv6_ips):
                            ipv6_ips[ipv6_row_index] = neighbor_ip
                        else:
                            # If index is beyond current list, add new IP
                            ipv6_ips.append(neighbor_ip)
                        
                        bgp_config["bgp_neighbor_ipv6"] = ",".join(ipv6_ips)
            
            elif column == 4:  # Source IP changed (column 4 after adding BGP Status)
                source_ip_item = self.parent.bgp_table.item(row, 4)
                neighbor_type_item = self.parent.bgp_table.item(row, 2)
                
                if source_ip_item and neighbor_type_item:
                    source_ip = source_ip_item.text().strip()
                    neighbor_type = neighbor_type_item.text()
                    
                    # Validate source IP address format
                    try:
                        if neighbor_type == "IPv4":
                            if source_ip:  # Only validate if not empty
                                ipaddress.IPv4Address(source_ip)
                        elif neighbor_type == "IPv6":
                            if source_ip:  # Only validate if not empty
                                ipaddress.IPv6Address(source_ip)
                    except ipaddress.AddressValueError:
                        QMessageBox.warning(self.parent, f"Invalid {neighbor_type} Source Address", 
                                          f"'{source_ip}' is not a valid {neighbor_type} address.")
                        # Revert to original value
                        if neighbor_type == "IPv4":
                            original_source = bgp_config.get("bgp_update_source_ipv4", "")
                            source_ip_item.setText(original_source)
                        elif neighbor_type == "IPv6":
                            original_source = bgp_config.get("bgp_update_source_ipv6", "")
                            source_ip_item.setText(original_source)
                        return
                    
                    if neighbor_type == "IPv4":
                        bgp_config["bgp_update_source_ipv4"] = source_ip
                    elif neighbor_type == "IPv6":
                        bgp_config["bgp_update_source_ipv6"] = source_ip
            
            elif column == 5:  # Local AS changed (column 5 after adding BGP Status)
                local_as_item = self.parent.bgp_table.item(row, 5)
                if local_as_item:
                    local_as = local_as_item.text().strip()
                    
                    # Validate AS number
                    try:
                        if local_as:  # Only validate if not empty
                            asn = int(local_as)
                            if asn <= 0 or asn > 4294967295:  # Valid ASN range
                                raise ValueError("ASN out of range")
                    except ValueError:
                        QMessageBox.warning(self.parent, "Invalid Local AS Number", 
                                          f"'{local_as}' is not a valid AS number (must be 1-4294967295).")
                        # Revert to original value
                        original_asn = bgp_config.get("bgp_asn", "")
                        local_as_item.setText(original_asn)
                        return
                    
                    bgp_config["bgp_asn"] = local_as
            
            elif column == 6:  # Remote AS changed (column 6 after adding BGP Status)
                remote_as_item = self.parent.bgp_table.item(row, 6)
                if remote_as_item:
                    remote_as = remote_as_item.text().strip()
                    
                    # Validate AS number
                    try:
                        if remote_as:  # Only validate if not empty
                            asn = int(remote_as)
                            if asn <= 0 or asn > 4294967295:  # Valid ASN range
                                raise ValueError("ASN out of range")
                    except ValueError:
                        QMessageBox.warning(self.parent, "Invalid Remote AS Number", 
                                          f"'{remote_as}' is not a valid AS number (must be 1-4294967295).")
                        # Revert to original value
                        original_remote_asn = bgp_config.get("bgp_remote_asn", "")
                        remote_as_item.setText(original_remote_asn)
                        return
                    
                    bgp_config["bgp_remote_asn"] = remote_as
            
            elif column == 10:  # Keepalive timer changed (column 10)
                keepalive_item = self.parent.bgp_table.item(row, 10)
                if keepalive_item:
                    keepalive = keepalive_item.text().strip()
                    
                    # Validate keepalive timer
                    try:
                        if keepalive:  # Only validate if not empty
                            timer_value = int(keepalive)
                            if timer_value < 1 or timer_value > 65535:  # Valid keepalive range
                                raise ValueError("Keepalive out of range")
                    except ValueError:
                        QMessageBox.warning(self.parent, "Invalid Keepalive Timer", 
                                          f"'{keepalive}' is not a valid keepalive timer (must be 1-65535 seconds).")
                        # Revert to original value
                        original_keepalive = bgp_config.get("bgp_keepalive", "30")
                        keepalive_item.setText(original_keepalive)
                        return
                    
                    bgp_config["bgp_keepalive"] = keepalive
            
            elif column == 11:  # Hold-time timer changed (column 11)
                hold_time_item = self.parent.bgp_table.item(row, 11)
                if hold_time_item:
                    hold_time = hold_time_item.text().strip()
                    
                    # Validate hold-time timer
                    try:
                        if hold_time:  # Only validate if not empty
                            timer_value = int(hold_time)
                            if timer_value < 3 or timer_value > 65535:  # Valid hold-time range
                                raise ValueError("Hold-time out of range")
                    except ValueError:
                        QMessageBox.warning(self.parent, "Invalid Hold-time Timer", 
                                          f"'{hold_time}' is not a valid hold-time timer (must be 3-65535 seconds).")
                        # Revert to original value
                        original_hold_time = bgp_config.get("bgp_hold_time", "90")
                        hold_time_item.setText(original_hold_time)
                        return
                    
                    bgp_config["bgp_hold_time"] = hold_time
            
            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()


    def _cleanup_bgp_table_for_device(self, device_id, device_name):
        """Clean up BGP table entries for a removed device."""
        try:
            logger.debug(f"[DEBUG BGP CLEANUP] Cleaning up BGP entries for device '{device_name}' (ID: {device_id})")
            
            # Remove BGP table rows that match this device
            rows_to_remove = []
            for row in range(self.parent.bgp_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.parent.bgp_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    logger.debug(f"[DEBUG BGP CLEANUP] Found BGP row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.parent.bgp_table.removeRow(row)
                logger.debug(f"[DEBUG BGP CLEANUP] Removed BGP table row {row}")
            
            # Also clean up BGP protocol data from device protocols
            # Remove BGP protocol from the device in all_devices
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove BGP from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list) and "BGP" in device["protocols"]:
                                device["protocols"].remove("BGP")
                                logger.debug(f"[DEBUG BGP CLEANUP] Removed BGP protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    logger.debug(f"[DEBUG BGP CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict) and "BGP" in device["protocols"]:
                                # Handle old format for backward compatibility
                                del device["protocols"]["BGP"]
                                logger.debug(f"[DEBUG BGP CLEANUP] Removed BGP protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    logger.debug(f"[DEBUG BGP CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove bgp_config if it exists
                        if "bgp_config" in device:
                            del device["bgp_config"]
                            logger.debug(f"[DEBUG BGP CLEANUP] Removed bgp_config from device '{device_name}'")
                        break
            
            logger.debug(f"[DEBUG BGP CLEANUP] Removed {len(rows_to_remove)} BGP entries for device '{device_name}'")
            
        except Exception as e:
            logger.error(f"Failed to cleanup BGP entries for device '{device_name}': {e}")


    def apply_bgp_configurations(self):
        """Apply BGP configurations to the server for selected BGP table rows."""
        server_url = self.parent.get_server_url()
        if not server_url:
            QMessageBox.critical(self.parent, "No Server", "No server selected.")
            return

        # Get selected rows from the BGP table
        selected_items = self.parent.bgp_table.selectedItems()
        selected_devices = []
        
        if selected_items:
            # Get unique device names from selected BGP table rows
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find the devices in all_devices
            for device_name in selected_device_names:
                for iface, devices in self.parent.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            selected_devices.append(device)
                            break

        # Handle both BGP application and removal
        devices_to_apply_bgp = []  # Devices that need BGP configuration applied
        devices_to_remove_bgp = []  # Devices that need BGP configuration removed
        
        if selected_items:
            # If BGP table rows are selected, process only those devices
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find devices and determine if they need BGP applied or removed
            for device_name in selected_device_names:
                for iface, devices in self.parent.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            bgp_config = device.get("bgp_config", {})
                            if bgp_config:
                                if bgp_config.get("_marked_for_removal"):
                                    # Device is marked for BGP removal
                                    devices_to_remove_bgp.append(device)
                                else:
                                    # Device has normal BGP config - needs application
                                    devices_to_apply_bgp.append(device)
                            else:
                                # Device was in BGP table but no longer has BGP config - needs removal
                                devices_to_remove_bgp.append(device)
                            break
        else:
            # If no BGP table rows selected, process all devices
            # Find devices that need BGP applied or removed
            for iface, devices in self.parent.main_window.all_devices.items():
                for device in devices:
                    bgp_config = device.get("bgp_config", {})
                    if bgp_config:
                        if bgp_config.get("_marked_for_removal"):
                            # Device is marked for BGP removal
                            devices_to_remove_bgp.append(device)
                        else:
                            # Device has normal BGP config - needs application
                            devices_to_apply_bgp.append(device)
            
            # Check if we have any work to do
            if not devices_to_apply_bgp and not devices_to_remove_bgp:
                # Check if there are any devices at all
                total_devices = sum(len(devices) for devices in self.parent.main_window.all_devices.values())
                if total_devices == 0:
                    QMessageBox.information(self.parent, "No Devices", 
                                          "No devices found to apply BGP configuration to.")
                    return
                else:
                    # There are devices but none have BGP config
                    QMessageBox.information(self.parent, "No BGP Configuration", 
                                          "No devices have BGP configuration to apply or remove.")
                    return

        # Check if we have any work to do
        if not devices_to_apply_bgp and not devices_to_remove_bgp:
            QMessageBox.information(self.parent, "No BGP Changes", 
                                  "No BGP configurations to apply or remove.")
            return
        
        # Track which address families are selected for each device
        device_address_families = {}  # device_name -> set of address families (IPv4, IPv6)
        
        if selected_items:
            # Track selected address families from BGP table rows
            for item in selected_items:
                row = item.row()
                device_name = self.parent.bgp_table.item(row, 0).text()  # Device column
                neighbor_type_item = self.parent.bgp_table.item(row, 2)  # Column 2 is "Neighbor Type"
                
                if neighbor_type_item:
                    protocol_type = neighbor_type_item.text().strip()
                    if device_name not in device_address_families:
                        device_address_families[device_name] = set()
                    device_address_families[device_name].add(protocol_type)
        
        # CRITICAL: Run BGP apply operations in background thread to prevent UI blocking
        # Use QThread to handle blocking network requests asynchronously
        class ApplyBGPWorker(QThread):
            finished = pyqtSignal(dict)  # Emit results dict when done
            
            def __init__(self, server_url, devices_to_apply_bgp, devices_to_remove_bgp, device_address_families, parent_handler):
                super().__init__()
                self.server_url = server_url
                self.devices_to_apply_bgp = devices_to_apply_bgp
                self.devices_to_remove_bgp = devices_to_remove_bgp
                self.device_address_families = device_address_families
                self.parent_handler = parent_handler
            
            def run(self):
                """Run BGP apply operations in background thread."""
                results = {
                    "success_count": 0,
                    "failed_devices": [],
                    "removal_success_count": 0,
                    "removal_failed_devices": []
                }
        
                # Handle BGP application
                for device_info in self.devices_to_apply_bgp:
                    device_name = device_info.get("Device Name", "Unknown")
                    device_id = device_info.get("device_id")
                    
                    if not device_id:
                        results["failed_devices"].append(f"{device_name}: Missing device ID")
                        continue
                    
                    try:
                        # Prepare BGP configuration payload
                        bgp_config = device_info.get("bgp_config", {}).copy()
                        
                        # If specific address families were selected, add flag to indicate partial apply
                        if device_name in self.device_address_families:
                            selected_families = self.device_address_families[device_name]
                            # Convert to list format expected by server
                            apply_families = []
                            if "IPv4" in selected_families:
                                apply_families.append("ipv4")
                            if "IPv6" in selected_families:
                                apply_families.append("ipv6")
                            
                            if apply_families:
                                bgp_config["_apply_address_families"] = apply_families
                                logging.debug(f"[BGP APPLY] Device {device_name}: Applying only selected address families: {apply_families}")
                        
                        payload = {
                            "device_id": device_id,
                            "device_name": device_name,
                            "interface": device_info.get("Interface", ""),
                            "vlan": device_info.get("VLAN", "0"),
                            "ipv4": device_info.get("IPv4", ""),
                            "ipv6": device_info.get("IPv6", ""),
                            "gateway": device_info.get("Gateway", ""),  # Include gateway for static route
                            "bgp_config": bgp_config,
                            "all_route_pools": getattr(self.parent_handler.parent.main_window, 'bgp_route_pools', []),  # Include all route pools for generation
                        }
                        
                        # Send BGP configuration to server
                        response = requests.post(
                            f"{self.server_url}/api/device/bgp/configure",
                            json=payload,
                            timeout=30,
                        )
                        
                        if response.status_code == 200:
                            results["success_count"] += 1
                            logger.info(f"BGP configuration applied for {device_name}")
                            
                            # After successful BGP configuration, start the BGP service
                            try:
                                start_payload = {
                                    "device_id": device_id,
                                    "device_name": device_name,
                                    "interface": device_info.get("Interface", ""),
                                    "mac": device_info.get("MAC Address", ""),
                                    "vlan": device_info.get("VLAN", "0"),
                                    "ipv4": device_info.get("IPv4", ""),
                                    "ipv6": device_info.get("IPv6", ""),
                                    "protocols": ["BGP"],
                                    "ipv4_mask": device_info.get("ipv4_mask", "24"),
                                    "ipv6_mask": device_info.get("ipv6_mask", "64"),
                                    "bgp_config": bgp_config,
                                }
                                
                                start_response = requests.post(
                                    f"{self.server_url}/api/device/start",
                                    json=start_payload,
                                    timeout=30,
                                )
                                
                                if start_response.status_code == 200:
                                    logger.info(f"BGP service started for {device_name}")
                                else:
                                    logger.error(f"BGP configured but failed to start service for {device_name}")
                                    
                            except Exception as start_error:
                                logger.error(f"BGP configured but failed to start service for {device_name}: {start_error}")
                                
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            results["failed_devices"].append(f"{device_name}: {error_msg}")
                            logger.error(f"Failed to apply BGP for {device_name}: {error_msg}")
                            
                    except requests.exceptions.RequestException as e:
                        results["failed_devices"].append(f"{device_name}: Network error - {str(e)}")
                        logger.error(f"Network error applying BGP for {device_name}: {str(e)}")
                    except Exception as e:
                        results["failed_devices"].append(f"{device_name}: {str(e)}")
                        logger.error(f"Error applying BGP for {device_name}: {str(e)}")

                # Handle BGP removal
                for device_info in self.devices_to_remove_bgp:
                    device_name = device_info.get("Device Name", "Unknown")
                    device_id = device_info.get("device_id")
                    
                    if not device_id:
                        results["removal_failed_devices"].append(f"{device_name}: Missing device ID")
                        continue
                    
                    try:
                        # Call BGP cleanup endpoint to remove BGP configuration
                        response = requests.post(
                            f"{self.server_url}/api/bgp/cleanup",
                            json={"device_id": device_id}, 
                            timeout=30,
                        )
                        
                        if response.status_code == 200:
                            results["removal_success_count"] += 1
                            logger.info(f"BGP configuration removed for {device_name}")
                            
                            # Remove BGP configuration from client data after successful server removal
                            if "protocols" in device_info:
                                if isinstance(device_info["protocols"], dict):
                                    if device_info["protocols"].get("BGP", {}).get("_marked_for_removal"):
                                        del device_info["protocols"]["BGP"]
                                else:
                                    if device_info.get("bgp_config", {}).get("_marked_for_removal"):
                                        del device_info["bgp_config"]
                                    # If no other protocols, remove the protocols key entirely
                                    if not device_info["protocols"]:
                                        del device_info["protocols"]
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            results["removal_failed_devices"].append(f"{device_name}: {error_msg}")
                            logger.error(f"Failed to remove BGP for {device_name}: {error_msg}")
                            
                    except requests.exceptions.RequestException as e:
                        results["removal_failed_devices"].append(f"{device_name}: Network error - {str(e)}")
                        logger.error(f"Network error removing BGP for {device_name}: {str(e)}")
                    except Exception as e:
                        results["removal_failed_devices"].append(f"{device_name}: {str(e)}")
                        logger.error(f"Error removing BGP for {device_name}: {str(e)}")
                
                # Emit results when done
                self.finished.emit(results)
        
        # Show progress dialog while applying
        from PyQt5.QtWidgets import QProgressDialog
        progress = QProgressDialog("Applying BGP configurations...", "Cancel", 0, 0, self.parent)
        progress.setWindowModality(2)  # Qt.WindowModal
        progress.setCancelButton(None)  # Disable cancel button
        progress.setMinimumDuration(0)  # Show immediately
        progress.show()
        
        # Create and start worker thread
        worker = ApplyBGPWorker(server_url, devices_to_apply_bgp, devices_to_remove_bgp, device_address_families, self)
        # CRITICAL: Set parent to ensure proper cleanup
        worker.setParent(self.parent)
        worker.finished.connect(lambda results: self._on_bgp_apply_finished(results, progress, devices_to_apply_bgp, devices_to_remove_bgp))
        # No finished→deleteLater: destroys the C++ QThread mid-teardown
        # → "QThread: Destroyed while thread is still running" SIGABRT on
        # PyQt5 5.15.11 + Python 3.14. The _bgp_apply_workers list below
        # plus the global keepalive registry own the lifetime instead.
        worker.start()
        
        # Store worker reference to prevent garbage collection
        if not hasattr(self, '_bgp_apply_workers'):
            self._bgp_apply_workers = []
        self._bgp_apply_workers.append(worker)
        
        # Clean up finished workers
        # CRITICAL: Wrap isRunning() in try-except to handle deleted workers
        def is_worker_running(w):
            try:
                return w.isRunning()
            except RuntimeError:
                # Worker has been deleted, treat as not running
                return False
        
        self._bgp_apply_workers = [w for w in self._bgp_apply_workers if is_worker_running(w)]
        
        # Return early - results will be handled in _on_bgp_apply_finished
        return
    
    def _on_bgp_apply_finished(self, results, progress, devices_to_apply_bgp, devices_to_remove_bgp):
        """Handle BGP apply completion (called from worker thread via signal)."""
        # Close progress dialog
        progress.close()
        
        # Clean up worker reference
        # CRITICAL: Wrap isRunning() in try-except to handle deleted workers
        if hasattr(self, '_bgp_apply_workers'):
            def is_worker_running(w):
                try:
                    return w.isRunning()
                except RuntimeError:
                    # Worker has been deleted, treat as not running
                    return False
            
            self._bgp_apply_workers = [w for w in self._bgp_apply_workers if is_worker_running(w)]
        
        # Extract results
        success_count = results["success_count"]
        failed_devices = results["failed_devices"]
        removal_success_count = results["removal_success_count"]
        removal_failed_devices = results["removal_failed_devices"]

        # Show results - combine application and removal results
        total_success = success_count + removal_success_count
        total_failed = len(failed_devices) + len(removal_failed_devices)
        total_operations = len(devices_to_apply_bgp) + len(devices_to_remove_bgp)
        
        if total_operations == 0:
            QMessageBox.information(self.parent, "No BGP Operations", "No BGP operations to perform.")
            return
        
        # Build result messages
        all_results = []
        
        # Add BGP application results
        if devices_to_apply_bgp:
            for device_info in devices_to_apply_bgp:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in failed_devices]:
                    all_results.append(f"✅ Applied BGP to {device_name}")
        
        # Add BGP removal results  
        if devices_to_remove_bgp:
            for device_info in devices_to_remove_bgp:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in removal_failed_devices]:
                    all_results.append(f"✅ Removed BGP from {device_name}")
        
        # Add failed operations
        all_results.extend([f"❌ {failed}" for failed in failed_devices])
        all_results.extend([f"❌ {failed}" for failed in removal_failed_devices])
        
        # Show appropriate dialog
        if total_success == total_operations:
            # All successful
            if len(devices_to_apply_bgp) > 0 and len(devices_to_remove_bgp) > 0:
                title = "BGP Operations Completed"
                message = f"Successfully applied BGP to {success_count} device(s) and removed BGP from {removal_success_count} device(s)."
            elif len(devices_to_apply_bgp) > 0:
                title = "BGP Applied Successfully"
                message = f"BGP configuration applied successfully for {success_count} device(s)."
            else:
                title = "BGP Removed Successfully"
                message = f"BGP configuration removed successfully from {removal_success_count} device(s)."
            
            QMessageBox.information(self.parent, title, message)
        elif total_success > 0:
            # Partial success - use scrollable dialog
            from widgets.devices_tab import MultiDeviceResultsDialog
            dialog = MultiDeviceResultsDialog(
                "BGP Operations Partially Completed", 
                f"Completed {total_success} of {total_operations} BGP operations.",
                all_results,
                self.parent
            )
            dialog.exec_()
        else:
            # All failed - use scrollable dialog
            from widgets.devices_tab import MultiDeviceResultsDialog
            dialog = MultiDeviceResultsDialog(
                "BGP Operations Failed", 
                "Failed to complete any BGP operations.",
                all_results,
                self.parent
            )
            dialog.exec_()

        # CRITICAL: Defer table update to prevent UI blocking
        # Use QTimer to defer table update to next event loop iteration
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self.update_bgp_table)


    def start_bgp_protocol(self):
        """Start BGP protocol for selected devices."""
        self.parent._toggle_protocol_action("BGP", starting=True)


    def stop_bgp_protocol(self):
        """Stop BGP protocol for selected devices."""
        self.parent._toggle_protocol_action("BGP", starting=False)


    def start_bgp_monitoring(self):
        """Start periodic BGP status monitoring."""
        if not self.parent.bgp_monitoring_active:
            self.parent.bgp_monitoring_active = True
            self.parent.bgp_monitoring_timer.start(30000)  # Check every 30 seconds to reduce UI load
            # BGP monitoring started
        else:
            # BGP monitoring already active
            pass
    

    def stop_bgp_monitoring(self):
        """Stop periodic BGP status monitoring."""
        if self.parent.bgp_monitoring_active:
            self.parent.bgp_monitoring_active = False
            self.parent.bgp_monitoring_timer.stop()
            # BGP monitoring stopped
        else:
            # BGP monitoring already stopped
            pass
    

    def periodic_bgp_status_check(self):
        """Periodic BGP status check for all devices with BGP configured."""
        if not self.parent.bgp_monitoring_active:
            return
        
        # Check if any devices have BGP configured
        devices_with_bgp = []
        for iface, devices in self.parent.main_window.all_devices.items():
            for device in devices:
                device_protocols = device.get("protocols", {})
                if "BGP" in device_protocols:
                    devices_with_bgp.append(device)
        
        # If no devices have BGP configured, stop monitoring
        if not devices_with_bgp:
            # No devices with BGP configured - stopping monitoring
            self.parent.stop_bgp_monitoring()
            return
            
        # Update BGP table which will refresh all BGP statuses
        self.parent.update_bgp_table()
        # Periodic BGP status check completed
    
