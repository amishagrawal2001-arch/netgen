#server_section.py#
import requests
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QStackedWidget, QSpinBox,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox, QWidget,
    QHeaderView, QRadioButton, QGroupBox, QGridLayout, QTabWidget, QScrollArea, QCheckBox,
    QInputDialog, QSplitter, QAction, QMenu, QAbstractItemView, QSizePolicy, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QSpacerItem, QFileDialog, QMessageBox
)
from PyQt5.QtGui import QIcon, QPixmap, QColor
from PyQt5.QtCore import Qt, QSize


from utils.qicon_loader import qicon, r_icon

class TrafficGenClientServerSection():
    def setup_server_section(self):
        """Set up the server management section."""
        self.server_group = QGroupBox()
        layout = QVBoxLayout()

        # Server Tree
        self.server_tree = QTreeWidget()
        self.server_tree.setColumnCount(4)
        self.server_tree.setHeaderLabels(["TG ID", "Server Address / Interfaces", "Selected", "Status"])

        # Enable extended selection for multiple ports using Ctrl/Command
        self.server_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.server_tree)

        # Adjust column widths
        self.server_tree.setColumnWidth(0, 150)
        self.server_tree.setColumnWidth(1, 200)
        self.server_tree.setColumnWidth(2, 50)
        self.server_tree.setColumnWidth(3, 80)

        # Connect to unified update for stream + device tables
        self.server_tree.itemSelectionChanged.connect(self._on_server_selection_changed_combined)

        # Buttons for Server Management
        button_layout = QHBoxLayout()

        # Delete Port button
        remove_interface_button = QPushButton(" Delete Port")
        #remove_interface_button.setIcon(QIcon("resources/icons/Trash.png"))
        remove_interface_button.setIcon(QIcon(r_icon("icons/Trash.png")))

        remove_interface_button.setIconSize(QSize(16, 16))
        remove_interface_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        remove_interface_button.clicked.connect(self.remove_selected_interface)
        button_layout.addWidget(remove_interface_button)

        # Spacer
        spacer = QSpacerItem(1, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)
        button_layout.addItem(spacer)

        # Add Ports button
        readd_port_button = QPushButton(" Add Ports")
        #readd_port_button.setIcon(QIcon("resources/icons/readd.png"))
        readd_port_button.setIcon(QIcon(r_icon("icons/readd.png")))
        readd_port_button.setIconSize(QSize(16, 16))
        readd_port_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        readd_port_button.clicked.connect(self.readd_ports_dialog)
        button_layout.addWidget(readd_port_button)

        # Reset Interface button
        reset_interface_button = QPushButton(" Reset Interface")
        reset_icon_path = r_icon("icons/reset.png")
        if reset_icon_path:
            reset_interface_button.setIcon(QIcon(reset_icon_path))
        reset_interface_button.setIconSize(QSize(16, 16))
        reset_interface_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        reset_interface_button.clicked.connect(self.reset_selected_interface)
        button_layout.addWidget(reset_interface_button)

        # Stretch to align left
        button_layout.addStretch(1)
        layout.addLayout(button_layout)

        # Finalize section
        self.server_group.setLayout(layout)
        self.top_section.addWidget(self.server_group)

        # Populate server tree with current servers and ports
        self.update_server_tree()

    def _on_server_selection_changed_combined(self):
        """Update both stream and device tables on server tree selection change."""
        # Update main window server URL based on selection
        self._update_main_window_server_url()
        
        # Update device table
        if hasattr(self, "devices_tab") and hasattr(self, "all_devices"):
            self.devices_tab.update_device_table(self.all_devices)
            
            # Update protocol tables to show only devices from selected TGen ports
            if hasattr(self.devices_tab, "update_bgp_table"):
                self.devices_tab.update_bgp_table()
            if hasattr(self.devices_tab, "update_ospf_table"):
                self.devices_tab.update_ospf_table()
            if hasattr(self.devices_tab, "update_isis_table"):
                self.devices_tab.update_isis_table()

        # Update stream table (this method exists in this mixin)
        if hasattr(self, "update_stream_table"):
            self.update_stream_table()

    def _update_main_window_server_url(self):
        """Update main window server URL based on current server tree selection."""
        try:
            selected_items = self.server_tree.selectedItems()
            if selected_items:
                selected_item = selected_items[0]
                server_item = selected_item.parent() if selected_item.parent() else selected_item
                server_address = server_item.text(1)
                if server_address.startswith(("http://", "https://")):
                    # Update the main window's server URL
                    self.server_url = server_address
                    # print(f"[DEBUG SERVER] Updated main_window.server_url to: {self.server_url}")
                else:
                    pass  # Invalid server address format
            else:
                pass  # No server selected in tree
        except Exception:
            pass  # Error updating server URL





    def handle_enabled_combo_change(self, value, row):
        port_item = self.stream_table.item(row, 1)
        name_item = self.stream_table.item(row, 2)
        if not port_item or not name_item:
            print(f"[WARN] Missing port or stream name at row {row}")
            return

        port = port_item.text()
        name = name_item.text()
        new_flag = value == "Yes"

        # Update matching stream in self.streams
        for stream in self.streams.get(port, []):
            stream_name = stream.get("name") or stream.get("protocol_selection", {}).get("name")
            if stream_name == name:
                stream["enabled"] = new_flag
                if "protocol_selection" in stream:
                    stream["protocol_selection"]["enabled"] = new_flag
                print(f"[DEBUG] Enabled flag updated for stream '{name}' on {port} → {value}")
                return

    def sync_ui_to_stream_model(self):
        """Force sync of stream table UI values (ComboBoxes) back into self.streams model."""
        for row in range(self.stream_table.rowCount()):
            port_item = self.stream_table.item(row, 1)
            name_item = self.stream_table.item(row, 2)
            if not port_item or not name_item:
                continue

            port = port_item.text()
            name = name_item.text()
            stream_list = self.streams.get(port, [])

            for stream in stream_list:
                ps = stream.setdefault("protocol_selection", {})
                if ps.get("name") == name:
                    # Enabled ComboBox
                    enabled_widget = self.stream_table.cellWidget(row, 3)
                    if enabled_widget:
                        is_enabled = enabled_widget.currentText().lower() == "yes"
                        ps["enabled"] = is_enabled
                        stream["enabled"] = is_enabled

                    # Flow Tracking ComboBox
                    flow_widget = self.stream_table.cellWidget(row, 15)
                    if flow_widget:
                        is_flow = flow_widget.currentText().lower() == "yes"
                        ps["flow_tracking_enabled"] = is_flow
                        stream["flow_tracking_enabled"] = is_flow



    def update_stream_table(self):
        """Update the stream table with streams for all selected TG ports (only from online servers)."""
        from PyQt5.QtCore import QSignalBlocker, QTimer
        from functools import partial
        import time

        # Debounce rapid updates - only update if enough time has passed since last update
        current_time = time.time()
        if hasattr(self, "_last_stream_table_update") and (current_time - self._last_stream_table_update) < 0.5:
            # Schedule a delayed update instead of skipping
            if hasattr(self, "_stream_table_update_timer"):
                self._stream_table_update_timer.stop()
            self._stream_table_update_timer = QTimer()
            self._stream_table_update_timer.setSingleShot(True)
            self._stream_table_update_timer.timeout.connect(self._do_update_stream_table)
            self._stream_table_update_timer.start(500)  # 500ms delay
            return
        
        self._do_update_stream_table()

    def _do_update_stream_table(self):
        """Internal method to actually update the stream table."""
        from PyQt5.QtCore import QSignalBlocker
        from functools import partial
        import time

        # Guard against re-entrancy while we repopulate
        if not hasattr(self, "_populating_table"):
            self._populating_table = False
        if self._populating_table:
            return  # Already updating, skip this call
        self._populating_table = True
        self.stream_table.blockSignals(True)
        self._last_stream_table_update = time.time()

        try:
            self.stream_table.setRowCount(0)

            search_term = (self.search_box.text().strip().lower()
                           if hasattr(self, "search_box") and self.search_box else "")

            self.stream_table.setColumnCount(16)
            self.stream_table.setHorizontalHeaderLabels([
                "Status", "Interface", "Name", "Enabled", "Details", "Frame Type",
                "Min Size", "Max Size", "Fixed Size", "L1", "VLAN", "L2", "L3", "L4", "RX Port", "Flow Tracking"
            ])

            # Step 1: Get selected ports (if any)
            selected_ports = []
            if hasattr(self, "server_tree") and self.server_tree:
                selected_items = self.server_tree.selectedItems()
                for item in selected_items:
                    parent = item.parent()
                    if parent:
                        tg_id = parent.text(0)
                        port_name = item.text(0).strip()  # Remove radio button prefix if present
                        if port_name.startswith("• ") or port_name.startswith("● "):
                            port_name = port_name[2:]  # Remove bullet prefix
                        full_port_name = f"{tg_id} - {port_name}"
                        selected_ports.append(full_port_name)

            # Step 2: Filter online servers
            online_tg_ids = {f"TG {server['tg_id']}" for server in getattr(self, "server_interfaces", [])
                             if server.get("online", True)}
            if not online_tg_ids:
                print("No online servers available. Skipping stream table update.")
                return
            
            print(f"[DEBUG STREAM TABLE] Selected ports: {selected_ports}")
            print(f"[DEBUG STREAM TABLE] Online TG IDs: {online_tg_ids}")
            print(f"[DEBUG STREAM TABLE] Available streams: {list(getattr(self, 'streams', {}).keys())}")

            row_count = 0

            # Step 3: Build table from model
            for port, streams in getattr(self, "streams", {}).items():
                tg_id = port.split(" - ")[0]
                if tg_id not in online_tg_ids:
                    continue

                # Check if this port matches any selected port (handle both formats)
                port_matches = False
                if selected_ports:
                    for selected_port in selected_ports:
                        # Convert formats to match
                        port_normalized = port.replace("Port: ", "")
                        selected_port_normalized = selected_port.replace("Port: ", "")
                        if port_normalized == selected_port_normalized:
                            port_matches = True
                            break
                    if not port_matches:
                        continue

                for stream in streams:
                    ps = stream.get("protocol_selection", {})

                    name_lower = (ps.get("name", "") or stream.get("name", "") or "").lower()
                    l4_lower = (ps.get("L4", "") or "").lower()

                    if search_term and (search_term not in port.lower()
                                        and search_term not in name_lower
                                        and search_term not in l4_lower):
                        continue

                    self.stream_table.insertRow(row_count)

                    # (0) Status (read-only)
                    status = stream.get("status", "stopped")
                    if status == "running":
                        status_icon = QIcon(r_icon("icons/green_dot.png"))
                    elif status == "rx_tracking":
                        status_icon = QIcon(r_icon("icons/blue_dot.png"))
                    else:
                        status_icon = QIcon(r_icon("icons/red_dot.png"))
                    status_item = QTableWidgetItem()
                    status_item.setIcon(status_icon)
                    status_item.setFlags(Qt.ItemIsEnabled)
                    self.stream_table.setItem(row_count, 0, status_item)

                    # (1) Interface (read-only) - extract just the interface name
                    # Extract interface name from port (e.g., "TG 0 - eno8303" -> "eno8303")
                    interface_name = port.split(" - ")[-1] if " - " in port else port
                    iface_item = QTableWidgetItem(interface_name)
                    iface_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    self.stream_table.setItem(row_count, 1, iface_item)

                    # Ensure stream_id exists (belt & suspenders)
                    sid = stream.get("stream_id")
                    if not sid and hasattr(self, "_alloc_stream_id"):
                        sid = self._alloc_stream_id()
                        stream["stream_id"] = sid

                    # (2) Name (editable) + stash stream_id
                    name_val = ps.get("name", "") or stream.get("name", "") or ""
                    name_item = QTableWidgetItem(name_val)
                    name_item.setData(Qt.UserRole, sid)
                    name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self.stream_table.setItem(row_count, 2, name_item)

                    # (3) Enabled via combo
                    enabled_combo = QComboBox()
                    enabled_combo.addItems(["Yes", "No"])
                    enabled_raw = stream.get("enabled")
                    if enabled_raw is None:
                        enabled_raw = ps.get("enabled", False)
                    enabled_combo.setCurrentText("Yes" if bool(enabled_raw) else "No")
                    enabled_combo.currentTextChanged.connect(
                        lambda value, row=row_count: self.handle_enabled_combo_change(value, row)
                    )
                    enabled_combo.setEnabled(status != "rx_tracking")
                    self.stream_table.setCellWidget(row_count, 3, enabled_combo)

                    # (4–13) Protocol fields
                    column_keys = [
                        "details", "frame_type", "frame_min", "frame_max", "frame_size",
                        "L1", "VLAN", "L2", "L3", "L4"
                    ]
                    for offset, key in enumerate(column_keys):
                        value = ps.get(key, "")
                        col_index = 4 + offset

                        # Normalize frame_size in the model if invalid
                        if key == "frame_size":
                            if value is None or not str(value).isdigit():
                                value = "64"
                                ps["frame_size"] = value
                                stream["frame_size"] = value

                        item = QTableWidgetItem(str(value))

                        if key == "frame_size":
                            # (8) Fixed Size — editable
                            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        else:
                            # Read-only fields
                            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                        self.stream_table.setItem(row_count, col_index, item)

                    # (14) RX Port (read-only)
                    rx_port = stream.get("rx_port", port)
                    rx_item = QTableWidgetItem(rx_port)
                    rx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    self.stream_table.setItem(row_count, 14, rx_item)

                    # (15) Flow Tracking via combo (unified source)
                    flow_combo = QComboBox()
                    flow_combo.addItems(["Yes", "No"])
                    flow_flag = stream.get("flow_tracking_enabled",
                                           ps.get("flow_tracking_enabled", False))
                    flow_combo.setCurrentText("Yes" if flow_flag else "No")
                    # Use partial to bind stable row/port
                    flow_combo.currentTextChanged.connect(
                        partial(self.handle_flow_tracking_change, row=row_count, port=port)
                    )
                    self.stream_table.setCellWidget(row_count, 15, flow_combo)

                    row_count += 1

            if row_count > 0:
                print(f"Stream table updated with {row_count} rows and 16 columns.")
            else:
                print("No valid streams to display for selected ports.")

            # Resize-after-fill
            self.stream_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

            # Optional: sync any other UI with model
            if hasattr(self, "sync_ui_to_stream_model"):
                self.sync_ui_to_stream_model()

        finally:
            # Re-enable signals and clear the population guard
            self.stream_table.blockSignals(False)
            self._populating_table = False

    def on_server_tree_selection_changed(self):
        if not hasattr(self, "main_window"):
            return
        if not hasattr(self.main_window, "devices_tab"):
            return
        if not hasattr(self.main_window, "all_devices"):
            return

        # Trigger table refresh with correct filtered data
        self.main_window.devices_tab.update_device_table(self.main_window.all_devices)

    def update_server_tree(self):
        """Update the server tree with servers and their ports."""
        self.server_tree.clear()  # Clear the tree before updating

        if not self.server_interfaces:
            return  # Do not add dummy placeholders

        for i, server in enumerate(self.server_interfaces):
            tg_id = f"TG {server['tg_id']}"
            server_address = server["address"]

            server_item = QTreeWidgetItem([tg_id, server_address, "", ""])
            self.server_tree.addTopLevelItem(server_item)

            # Checkbox to select server
            checkbox = QCheckBox()
            checkbox.setChecked(server in self.selected_servers)

            """checkbox.stateChanged.connect(
                lambda state, idx=i: self.on_server_checkbox_state_changed(idx, state)
            )"""
            from functools import partial  # ensure this import is at the top

            checkbox.stateChanged.connect(
                partial(self.on_server_checkbox_state_changed, i)
            )
            self.server_tree.setItemWidget(server_item, 2, checkbox)

            # Label with icon to show server status (Online/Offline)
            status_label = QLabel()
            status_label.setAlignment(Qt.AlignCenter)
            server["status_label_widget"] = status_label  # Store for later update
            self.server_tree.setItemWidget(server_item, 3, status_label)

            # Reflect current status visually
            is_online = server.get("online", True)
            self.update_server_status_icon(server, is_online)

            if not is_online:
                continue

            # Use stored interfaces if available, otherwise fetch them
            interfaces = server.get("interfaces")
            if interfaces is None:
                try:
                    # Use shorter timeout to prevent hanging when server is offline
                    if hasattr(self, 'connection_manager') and self.connection_manager:
                        response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=2)
                    else:
                        response = requests.get(f"{server_address}/api/interfaces", timeout=2)
                    if response.status_code == 200:
                        interfaces = response.json()
                        server["interfaces"] = interfaces  # Store for future use
                        print(f"[SERVER TREE] Fetched {len(interfaces)} interfaces from {server_address}")
                    else:
                        print(f"[SERVER TREE] Server {server_address} returned status code: {response.status_code}")
                        server["online"] = False
                        self.update_server_status_icon(server, False)
                        continue
                except Exception as e:
                    print(f"[SERVER TREE] Error fetching interfaces from {server_address}: {e}")
                    server["online"] = False
                    self.update_server_status_icon(server, False)
                    continue
            
            if interfaces:
                    for interface in interfaces:
                        port_name = interface['name']
                        interface_status = interface.get('status', 'up')
                        full_interface_name = f"{tg_id} - {port_name}"  # Removed "Port:" prefix

                        if full_interface_name in self.removed_interfaces:
                            continue  # ✅ Skip previously removed interfaces

                        # Create interface name with status indicator prefix
                        if interface_status == 'down':
                            interface_display = f"• {port_name}"  # Red bullet for down
                            port_item = QTreeWidgetItem([interface_display, ""])
                            port_item.setForeground(0, QColor(200, 0, 0))  # Darker red for better readability
                        else:
                            interface_display = f"• {port_name}"  # Green bullet for up
                            port_item = QTreeWidgetItem([interface_display, ""])
                            port_item.setForeground(0, QColor(0, 100, 0))  # Darker green for better readability
                        
                        # Interface display updated
                        
                        server_item.addChild(port_item)

            server["online"] = True
            self.update_server_status_icon(server, True)
        
        print(f"[DEBUG] Tree widget updated with {len(self.server_interfaces)} servers")
    '''def update_server_status_icon(self, server, is_online):
        """Helper to update status icon based on online state."""
        status_label = server.get("status_label_widget")
        if status_label:
            icon_path = "resources/icons/green_dot.png" if is_online else "resources/icons/red_dot.png"
            pixmap = QPixmap(icon_path).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            status_label.setPixmap(pixmap)
            status_label.repaint()  # ✅ Force visual refresh'''

    def update_server_status_icon(self, server, is_online):
        """Update the little dot on the server row (green=online, red=offline) safely."""
        status_label = server.get("status_label_widget")
        if not status_label:
            return

        icon_name = "green_dot.png" if is_online else "red_dot.png"

        # 1) Try packaged icon (QIcon)
        try:
            ico = qicon("resources", f"icons/{icon_name}")
        except Exception:
            ico = QIcon()

        # 2) Fallback to absolute path via r_icon() → wrap it into QIcon
        if ico.isNull():
            try:
                path = r_icon(f"icons/{icon_name}")
            except Exception:
                path = ""
            if path:
                ico = QIcon(path)

        # 3) Last resort: paint a colored dot
        if ico.isNull():
            pm = QPixmap(16, 16)
            pm.fill(Qt.transparent)
            from PyQt5.QtGui import QPainter, QColor, QBrush
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(QBrush(QColor(0, 180, 0) if is_online else QColor(200, 0, 0)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(0, 0, 16, 16)
            painter.end()
            status_label.setPixmap(pm)
            status_label.repaint()
            return

        status_label.setPixmap(ico.pixmap(QSize(16, 16)))
        status_label.repaint()

    def retry_server_connection(self, server):
        """Retry connecting to the specified server and update its status icon."""
        server_address = server["address"]
        print(f"Manually retrying connection to {server_address}...")
        try:
            if hasattr(self, 'connection_manager') and self.connection_manager:
                response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=2)
            else:
                response = requests.get(f"{server_address}/api/interfaces", timeout=2)
            if response.status_code == 200:
                server["online"] = True
                print(f"✅ Server {server_address} is now online.")

                # Update the status icon
                self.update_server_status_icon(server, True)

                # Refresh ports only for this server item
                for i in range(self.server_tree.topLevelItemCount()):
                    item = self.server_tree.topLevelItem(i)
                    if item.text(1) == server_address:
                        item.takeChildren()
                        interfaces = response.json()
                        for interface in interfaces:
                            port_name = interface["name"]
                            full_name = f"TG {server['tg_id']} - {port_name}"
                            if full_name not in self.removed_interfaces:
                                port_item = QTreeWidgetItem([port_name, ""])
                                item.addChild(port_item)

                        # ✅ Force UI refresh of the status label
                        status_label = server.get("status_label_widget")
                        if status_label:
                            status_label.repaint()  # This ensures visual update
                        break
            else:
                raise Exception(f"Non-200 status: {response.status_code}")
        except Exception as e:
            print(f"❌ Still failed to connect to {server_address}: {e}")
            server["online"] = False
            self.update_server_status_icon(server, False)

    def remove_selected_interface(self):
        """Remove the selected ports (interfaces) from the server tree."""
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more ports to remove.")
            return

        removed_ports = []  # Track removed ports for feedback

        for item in selected_items:
            parent_item = item.parent()
            if parent_item:  # Only process child items (ports)
                tg_id = parent_item.text(0)  # TG ID (e.g., "TG 0")
                port_name_with_bullet = item.text(0)  # Interface name with bullet (e.g., "• eth0")
                # Remove the bullet prefix to get the actual port name
                port_name = port_name_with_bullet.replace("• ", "", 1)  # Remove bullet prefix
                full_port_name = f"{tg_id} - {port_name}"

                # Add the full port name to removed interfaces
                self.removed_interfaces.add(full_port_name)
                removed_ports.append(full_port_name)

                # Remove the port from the tree
                index = parent_item.indexOfChild(item)
                if index >= 0:  # Ensure index is valid
                    parent_item.takeChild(index)

        if removed_ports:
            print(f"Removed ports: {', '.join(removed_ports)}")
            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            """QMessageBox.information(
                self,
                "Ports Removed",
                f"The following ports were removed:\n{', '.join(removed_ports)}"
            )"""
        else:
            QMessageBox.warning(self, "No Ports Removed", "No valid ports were selected for removal.")
    
    def reset_selected_interface(self):
        """Reset the selected interface on the server - removes all IPs, VLANs, and devices."""
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more interfaces to reset.")
            return
        
        # Filter to only port items (not server items)
        port_items = []
        for item in selected_items:
            parent_item = item.parent()
            if parent_item:  # Only process child items (ports)
                port_items.append(item)
        
        if not port_items:
            QMessageBox.warning(self, "Invalid Selection", "Please select interface(s), not server(s).")
            return
        
        # Confirm reset
        reply = QMessageBox.question(
            self, 
            "Confirm Interface Reset", 
            f"Are you sure you want to reset {len(port_items)} selected interface(s)?\n\n"
            f"This will:\n"
            f"- Remove all IP addresses from the interface(s)\n"
            f"- Remove all VLAN interfaces associated with the interface(s)\n"
            f"- Remove all devices associated with the interface(s) from server and database\n"
            f"- Stop and remove FRR containers for affected devices\n\n"
            f"This action cannot be undone!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Get server URL
        server_url = self.get_server_url(silent=True)
        if not server_url:
            QMessageBox.warning(self, "No Server", "No server is available. Please select a server first.")
            return
        
        # Process each selected interface
        reset_results = []
        errors = []
        
        for item in port_items:
            try:
                parent_item = item.parent()
                if not parent_item:
                    continue
                
                tg_id = parent_item.text(0)  # TG ID (e.g., "TG 0")
                port_name_with_bullet = item.text(0)  # Interface name with bullet (e.g., "• eth0")
                # Remove the bullet prefix to get the actual port name
                port_name = port_name_with_bullet.replace("• ", "", 1).replace("● ", "", 1)
                
                # Normalize interface name - extract base interface
                base_interface = port_name
                if ":" in base_interface:
                    base_interface = base_interface.rsplit(":", 1)[-1].strip()
                
                print(f"[INTERFACE RESET] Resetting interface '{base_interface}' (from port '{port_name}')")
                
                # Call the reset API
                reset_payload = {
                    "interface": base_interface,
                    "remove_vlans": True,
                    "cleanup_physical": True
                }
                
                response = requests.post(f"{server_url}/api/interface/reset", json=reset_payload, timeout=30)
                
                if response.status_code == 200:
                    result_data = response.json()
                    if result_data.get("success"):
                        reset_results.append(f"{base_interface}: {result_data.get('message', 'Reset successful')}")
                        print(f"✅ Interface '{base_interface}' reset successfully")
                    else:
                        error_msg = result_data.get("message", "Unknown error")
                        errors.append(f"{base_interface}: {error_msg}")
                        print(f"⚠️ Interface '{base_interface}' reset failed: {error_msg}")
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    errors.append(f"{base_interface}: {error_msg}")
                    print(f"❌ Interface '{base_interface}' reset failed: {error_msg}")
                    
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                errors.append(f"{port_name if 'port_name' in locals() else 'Unknown'}: {error_msg}")
                print(f"❌ Exception resetting interface: {e}")
        
        # Show results
        if reset_results:
            success_msg = f"Successfully reset {len(reset_results)} interface(s):\n" + "\n".join(reset_results)
            if errors:
                error_msg = f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors)
                QMessageBox.warning(self, "Interface Reset Results", success_msg + error_msg)
            else:
                QMessageBox.information(self, "Interface Reset Successful", success_msg)
            
            # Refresh device tables after reset
            if hasattr(self, "devices_tab") and self.devices_tab:
                self.devices_tab.populate_device_table()
                self.devices_tab.update_bgp_table()
                self.devices_tab.update_ospf_table()
                self.devices_tab.update_isis_table()
        else:
            error_msg = f"Failed to reset all {len(port_items)} interface(s):\n" + "\n".join(errors)
            QMessageBox.critical(self, "Interface Reset Failed", error_msg)
    
    def get_server_url(self, silent=False):
        """Get the current server URL from selected server or main window."""
        try:
            # Try to get from selected server in tree
            selected_items = self.server_tree.selectedItems()
            if selected_items:
                item = selected_items[0]
                parent_item = item.parent()
                if parent_item:
                    # Port item selected - get server from parent
                    server_item = parent_item
                else:
                    # Server item selected
                    server_item = item
                
                server_address = server_item.text(1)  # Server address column
                if server_address and server_address.startswith(("http://", "https://")):
                    return server_address
            
            # Fall back to main window server URL
            if hasattr(self, "main_window") and hasattr(self.main_window, "server_url"):
                return self.main_window.server_url
            
            # Fall back to first selected server
            if hasattr(self, "selected_servers") and self.selected_servers:
                return self.selected_servers[0].get("address")
            
            if not silent:
                QMessageBox.warning(self, "No Server", "No server is available. Please select a server first.")
            return None
        except Exception as e:
            if not silent:
                QMessageBox.warning(self, "Error", f"Failed to get server URL: {e}")
            return None
    
    def readd_ports_dialog(self):
        """Display a dialog to re-add removed ports with checkboxes."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Re-add Ports")
        dialog.setGeometry(300, 300, 400, 400)

        layout = QVBoxLayout(dialog)

        # Tree widget to display available ports grouped by TG with checkboxes
        tree_widget = QTreeWidget()
        tree_widget.setColumnCount(2)
        tree_widget.setHeaderLabels(["TG ID", "Port Name"])
        layout.addWidget(tree_widget)

        # Populate the tree widget with removed ports grouped by TG
        tg_ports_map = {}
        for port in sorted(self.removed_interfaces):  # Sort ports for better grouping
            if " - " in port:
                tg_id, port_name = port.split(" - ", 1)
                tg_ports_map.setdefault(tg_id, []).append(port_name)

        for tg_id, ports in tg_ports_map.items():
            tg_item = QTreeWidgetItem([tg_id, ""])
            tg_item.setFlags(tg_item.flags() & ~Qt.ItemIsSelectable)  # Make TG ID unselectable
            tree_widget.addTopLevelItem(tg_item)
            for port_name in ports:
                port_item = QTreeWidgetItem(["", port_name])
                port_item.setFlags(port_item.flags() | Qt.ItemIsUserCheckable)  # Enable checkbox
                port_item.setCheckState(0, Qt.Unchecked)
                tg_item.addChild(port_item)

        # Confirm and Cancel buttons
        button_layout = QHBoxLayout()
        confirm_button = QPushButton("Re-add Selected Ports")
        confirm_button.clicked.connect(lambda: self.readd_ports_from_tree(tree_widget, dialog))
        button_layout.addWidget(confirm_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        dialog.exec()
    def readd_ports_from_tree(self, tree_widget, dialog):
        """Re-add selected ports based on checkboxes in the tree widget."""
        selected_ports = []

        # Iterate through the tree widget to find checked items
        for i in range(tree_widget.topLevelItemCount()):
            tg_item = tree_widget.topLevelItem(i)
            for j in range(tg_item.childCount()):
                port_item = tg_item.child(j)
                if port_item.checkState(0) == Qt.Checked:  # Check if the port is selected
                    tg_id = tg_item.text(0)
                    port_name = port_item.text(1)
                    selected_ports.append(f"{tg_id} - {port_name}")

        # Re-add the selected ports
        for port in selected_ports:
            if port in self.removed_interfaces:
                self.removed_interfaces.remove(port)

        # Update the server tree and close the dialog
        self.update_server_tree()
        dialog.accept()
    def readd_ports(self, list_widget, dialog):
        """Re-add the selected ports from the dialog."""
        readded_ports = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.Checked:
                port = item.text().strip()
                if " - " in port:  # Ensure correct format
                    tg_id, port_name = port.split(" - ", 1)

                    # Ensure TG ID is properly matched
                    tg_item = None
                    for j in range(self.server_tree.topLevelItemCount()):
                        top_item = self.server_tree.topLevelItem(j)
                        if top_item.text(0) == tg_id:
                            tg_item = top_item
                            break

                    if tg_item:
                        # Avoid duplicates within the TG
                        existing_ports = [tg_item.child(k).text(0) for k in range(tg_item.childCount())]
                        # Check if port_name with bullet prefix exists
                        port_name_with_bullet = f"• {port_name}"
                        if port_name_with_bullet not in existing_ports:
                            port_item = QTreeWidgetItem([port_name_with_bullet, ""])
                            port_item.setForeground(0, QColor(0, 100, 0))  # Green color for up status
                            tg_item.addChild(port_item)
                            readded_ports.append(port)
                            # Remove from removed_interfaces
                            self.removed_interfaces.discard(port)

        if readded_ports:
            print(f"Re-added ports: {readded_ports}")
            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            #QMessageBox.information(self, "Ports Re-added", f"Re-added ports: {', '.join(readded_ports)}")
        else:
            QMessageBox.information(self, "No Ports Selected", "No ports were selected to re-add.")

        dialog.accept()
    def on_server_checkbox_state_changed(self, index, state):
        """Handle state changes for the server selection checkboxes."""
        server = self.server_interfaces[index]
        if state == Qt.Checked:
            if server not in self.selected_servers:
                self.selected_servers.append(server)
                self.server_url = server["address"]  # ✅ Set it here!
                print(f"Server selected: {server['address']}")
        else:
            if server in self.selected_servers:
                self.selected_servers.remove(server)
                print(f"Server deselected: {server['address']}")
                if self.selected_servers:
                    self.server_url = self.selected_servers[0]["address"]
                else:
                    self.server_url = None  # Reset if no server is selected

        # Update statistics when selection changes (non-blocking)
        from PyQt5.QtCore import QTimer
        def delayed_statistics_update():
            self.fetch_and_update_statistics()
        QTimer.singleShot(100, delayed_statistics_update)  # Small delay to prevent UI blocking
    def mark_server_offline(self, server, reason="unknown"):
        """Mark a server as offline and update UI/status tracking."""
        server["online"] = False
        print(f"❌ {reason} for {server['address']}")
        self.update_server_status_icon(server, False)
        if server not in self.failed_servers:
            self.failed_servers.append(server)
        if hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(True)