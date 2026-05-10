#server_section.py#
import logging
import requests

logger = logging.getLogger(__name__)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QStackedWidget, QSpinBox,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox, QWidget,
    QHeaderView, QRadioButton, QGroupBox, QGridLayout, QTabWidget, QScrollArea, QCheckBox,
    QInputDialog, QSplitter, QAction, QMenu, QAbstractItemView, QSizePolicy, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QSpacerItem, QFileDialog, QMessageBox
)
from PyQt5.QtGui import QIcon, QPixmap, QColor, QFont
from PyQt5.QtCore import Qt, QSize, QTimer


from utils.qicon_loader import qicon, r_icon


def _make_enabled_cell(initial_state: bool):
    """Build a centred QCheckBox inside a container suitable for a QTableWidget cell.

    Returns (container_widget, checkbox) so callers can wire signals on the checkbox
    while installing the container via setCellWidget. A bare QCheckBox sits flush-left
    in a cell, which looks unprofessional; the wrapper centres it instead.
    """
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignCenter)
    checkbox = QCheckBox()
    checkbox.blockSignals(True)
    checkbox.setChecked(bool(initial_state))
    checkbox.blockSignals(False)
    layout.addWidget(checkbox)
    return container, checkbox


def _read_enabled_cell(cell_widget) -> bool:
    """Read the on/off state of an Enabled cell, tolerating both the new
    QCheckBox-in-container layout and any legacy QComboBox cells."""
    if cell_widget is None:
        return False
    # New layout: QWidget container holding a QCheckBox
    checkbox = cell_widget.findChild(QCheckBox) if hasattr(cell_widget, "findChild") else None
    if checkbox is not None:
        return checkbox.isChecked()
    # Legacy layout: bare QComboBox with Yes/No
    if isinstance(cell_widget, QComboBox):
        return cell_widget.currentText().strip().lower() in ("yes", "true", "1")
    return False


class TrafficGenClientServerSection():
    def setup_server_section(self):
        """Set up the server management section."""
        self.server_group = QGroupBox("TGEN")
        layout = QVBoxLayout()
        
        # Set consistent spacing and margins for better visual hierarchy
        layout.setSpacing(10)  # Space between elements
        layout.setContentsMargins(4, 4, 4, 4)  # Balanced padding to match right side sections

        # Server Tree Section
        self.server_tree = QTreeWidget()
        self.server_tree.setColumnCount(3)
        self.server_tree.setHeaderLabels(["TG ID", "Server Address / Interfaces", "Select"])

        # Set header resize modes
        header = self.server_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Fixed)  # TG ID - fixed width
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Server Address - stretch to fill
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # Selected - fixed width
        header.setMinimumSectionSize(30)  # Allow columns to be as small as 30px (for checkbox visibility)
        
        # Improve header styling for better visual hierarchy
        header.setDefaultSectionSize(28)  # Header height (slightly increased for better visibility)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)  # Don't highlight header sections on click

        # Enable extended selection for multiple ports using Ctrl/Command
        self.server_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        
        # Table/Tree improvements for professional appearance
        self.server_tree.setAlternatingRowColors(True)  # Alternating row colors for better readability
        self.server_tree.setRootIsDecorated(True)  # Show expand/collapse indicators
        self.server_tree.setIndentation(15)  # Indentation for child items
        self.server_tree.setAnimated(True)  # Animate expand/collapse
        
        # Improved selection behavior
        self.server_tree.setSelectionBehavior(QAbstractItemView.SelectRows)  # Select entire rows
        
        # Visual styling — bumped for visibility:
        # - tree font 11px → 13px, item min-height 20→24, padding 2/4 → 4/8
        # - header bg/contrast strengthened to match the stats dock so the
        #   server pane and stats dock feel like one cohesive UI
        # - selected-row blue (#5b7fa8) → brighter (#2563eb) to make the
        #   active port unambiguous when scanning multiple TGs
        self.server_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #ffffff;
                alternate-background-color: #f5f7fa;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                font-size: 13px;
                outline: none;
                color: #111827;
            }
            QTreeWidget::item {
                padding: 4px 8px;
                border: none;
                min-height: 24px;
                color: #111827;
            }
            QTreeWidget::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QTreeWidget::item:hover:!selected {
                background-color: #eef2f7;
            }
            QTreeWidget::item:selected:hover {
                background-color: #1d4ed8;
            }
            QHeaderView::section {
                background-color: #e5e7eb;
                padding: 8px 10px;
                border: 1px solid #cbd5e1;
                border-left: none;
                border-top: none;
                font-weight: 700;
                font-size: 12px;
                color: #1f2937;
                letter-spacing: 0.3px;
            }
            QHeaderView::section:first {
                border-left: 1px solid #cbd5e1;
            }
        """)
        
        # Add tree with spacing
        layout.addWidget(self.server_tree, 1)  # Stretch factor for tree to take available space

        # Adjust column widths — keep TG ID compact so the address column has room
        self.server_tree.setColumnWidth(0, 90)   # Icon + short id like "TG 0"
        self.server_tree.setColumnWidth(1, 260)  # Initial width; column is set to Stretch
        self.server_tree.setColumnWidth(2, 60)   # "Select" header is short
        # Address column needs a healthy minimum so the header isn't clipped at narrow splitter widths
        header.setMinimumSectionSize(60)

        # Connect to unified update for stream + device tables
        # Use debounced handler to prevent rapid-fire updates on interface clicks
        self._selection_update_timer = QTimer()
        self._selection_update_timer.setSingleShot(True)
        self._selection_update_timer.timeout.connect(self._on_server_selection_changed_combined)
        self.server_tree.itemSelectionChanged.connect(self._debounced_selection_changed)

        # --- Action Buttons Section (grouped separately, aligned with Streams pattern) ---
        action_button_layout = QHBoxLayout()
        action_button_layout.setAlignment(Qt.AlignLeft)
        action_button_layout.setSpacing(5)  # Spacing between buttons
        action_button_layout.setContentsMargins(0, 5, 0, 0)  # Top margin to separate from tree

        # Add Ports button (action button)
        readd_port_button = QPushButton()
        readd_port_button.setIcon(QIcon(r_icon("icons/readd.png")))
        readd_port_button.setIconSize(QSize(16, 16))
        readd_port_button.setFixedSize(32, 28)
        readd_port_button.setToolTip("Add Ports")
        readd_port_button.clicked.connect(self.readd_ports_dialog)
        action_button_layout.addWidget(readd_port_button)

        # Delete Port button (action button)
        remove_interface_button = QPushButton()
        remove_interface_button.setIcon(QIcon(r_icon("icons/Trash.png")))
        remove_interface_button.setIconSize(QSize(16, 16))
        remove_interface_button.setFixedSize(32, 28)
        remove_interface_button.setToolTip("Delete Port")
        remove_interface_button.clicked.connect(self.remove_selected_interface)
        action_button_layout.addWidget(remove_interface_button)

        # Reset Interface button (action button)
        reset_interface_button = QPushButton()
        reset_icon_path = r_icon("icons/reset_btn.png")
        if reset_icon_path:
            reset_interface_button.setIcon(QIcon(reset_icon_path))
        else:
            # Fallback icon if reset_btn.png doesn't exist
            reset_interface_button.setIcon(QIcon(r_icon("icons/reset.png")))
        reset_interface_button.setIconSize(QSize(16, 16))
        reset_interface_button.setFixedSize(32, 28)
        reset_interface_button.setToolTip("Reset Interface")
        reset_interface_button.clicked.connect(self.reset_selected_interface)
        action_button_layout.addWidget(reset_interface_button)

        # Refresh Interface List button (action button)
        refresh_interface_button = QPushButton()
        refresh_icon_path = r_icon("icons/refresh.png")
        if refresh_icon_path:
            refresh_interface_button.setIcon(QIcon(refresh_icon_path))
        refresh_interface_button.setIconSize(QSize(16, 16))
        refresh_interface_button.setFixedSize(32, 28)
        refresh_interface_button.setToolTip("Refresh Interface List")
        refresh_interface_button.clicked.connect(self.refresh_selected_server_interfaces)
        action_button_layout.addWidget(refresh_interface_button)

        # Stretch to align left (buttons stay on left, space on right)
        action_button_layout.addStretch(1)
        layout.addLayout(action_button_layout)

        # Finalize section
        self.server_group.setLayout(layout)
        self.top_section.addWidget(self.server_group)

        # Populate server tree with current servers and ports
        self.update_server_tree()
        
        # Set column widths after tree is populated (ensures they're applied)
        self.server_tree.setColumnWidth(0, 90)
        self.server_tree.setColumnWidth(1, 260)
        self.server_tree.setColumnWidth(2, 60)

    def _debounced_selection_changed(self):
        """Debounced wrapper for selection change handler to prevent rapid-fire updates."""
        # OPTIMIZATION: Use 0ms delay for instant feedback - QTimer.singleShot(0) defers to next event loop cycle
        # This allows the UI to respond immediately while still preventing excessive updates during rapid clicks
        self._selection_update_timer.stop()
        self._selection_update_timer.start(0)  # 0ms = next event loop cycle - instant but non-blocking
    
    def _on_server_selection_changed_combined(self):
        """Update both stream and device tables on server tree selection change."""
        # Update main window server URL based on selection
        self._update_main_window_server_url()
        
        # OPTIMIZATION: Update device table immediately for instant feedback
        if hasattr(self, "devices_tab") and hasattr(self, "all_devices"):
            self.devices_tab.update_device_table(self.all_devices)
            
            # OPTIMIZATION: Defer protocol table updates to run after device table is displayed
            # This makes the device table appear instantly while protocol tables update in background
            QTimer.singleShot(0, lambda: self._update_protocol_tables())
        
        # OPTIMIZATION: Defer stream table update to avoid blocking
        QTimer.singleShot(0, lambda: self._update_stream_table_if_exists())
        
        # Update statistics when selection changes (to show stats for selected TG)
        if hasattr(self, "fetch_and_update_statistics"):
            QTimer.singleShot(50, lambda: self.fetch_and_update_statistics())
    
    def _update_protocol_tables(self):
        """Update protocol tables in background after device table is shown."""
        if not hasattr(self, "devices_tab"):
            return
        
        # Update protocol tables to show only devices from selected TGen ports
        if hasattr(self.devices_tab, "update_bgp_table"):
            self.devices_tab.update_bgp_table()
        if hasattr(self.devices_tab, "update_ospf_table"):
            self.devices_tab.update_ospf_table()
        if hasattr(self.devices_tab, "update_isis_table"):
            self.devices_tab.update_isis_table()
        # Update VXLAN table to show only devices from selected TGen ports
        if hasattr(self.devices_tab, "vxlan_handler") and hasattr(self.devices_tab.vxlan_handler, "refresh_vxlan_table"):
            self.devices_tab.vxlan_handler.refresh_vxlan_table()
    
    def _update_stream_table_if_exists(self):
        """Update stream table if the method exists."""
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
        """Handle change in Enabled combo box with safety checks to prevent segfaults."""
        # Safety checks: validate row and table state
        if not hasattr(self, "stream_table") or not self.stream_table:
            return
        if row < 0 or row >= self.stream_table.rowCount():
            return
        
        # Don't block during table population - allow user changes
        # The other safety checks (row bounds, streams existence) are sufficient
        
        # Safety check: ensure self.streams exists
        if not hasattr(self, "streams") or not self.streams:
            return
        
        port_item = self.stream_table.item(row, 1)
        name_item = self.stream_table.item(row, 2)
        if not port_item or not name_item:
            return

        port_text = port_item.text().strip()
        name = name_item.text().strip()
        if not name:  # Empty name is invalid
            return
        
        new_flag = value == "Yes"

        # Normalize port_text (remove "Port: " prefix if present)
        normalized_port_text = port_text
        if ":" in normalized_port_text:
            normalized_port_text = normalized_port_text.rsplit(":", 1)[-1].strip()
        if "Port:" in normalized_port_text:
            normalized_port_text = normalized_port_text.replace("Port:", "").strip()

        # Find the matching port key in self.streams (e.g., "TG 0 - Port: ens5np0")
        port_key = None
        try:
            for key in self.streams.keys():
                # Extract interface name from key
                key_interface = key.split(" - ")[-1].replace("Port: ", "").strip()
                if key_interface == normalized_port_text:
                    port_key = key
                    break
        except (AttributeError, RuntimeError) as e:
            # streams dict might be modified during iteration
            return

        if not port_key:
            return

        # Update matching stream in self.streams
        try:
            for stream in self.streams.get(port_key, []):
                if not isinstance(stream, dict):
                    continue
                stream_name = stream.get("name") or stream.get("protocol_selection", {}).get("name")
                if stream_name == name:
                    stream["enabled"] = new_flag
                    if "protocol_selection" in stream:
                        stream["protocol_selection"]["enabled"] = new_flag
                    return
        except (AttributeError, RuntimeError, KeyError) as e:
            # streams dict might be modified during iteration or key might be deleted
            return

    def sync_ui_to_stream_model(self):
        """Force sync of stream table UI values (ComboBoxes) back into self.streams model."""
        for row in range(self.stream_table.rowCount()):
            port_item = self.stream_table.item(row, 1)
            name_item = self.stream_table.item(row, 2)
            if not port_item or not name_item:
                continue

            port_text = port_item.text().strip()
            name = name_item.text().strip()
            
            # Normalize port_text to find the correct port key in self.streams
            normalized_port_text = port_text
            if ":" in normalized_port_text:
                normalized_port_text = normalized_port_text.rsplit(":", 1)[-1].strip()
            if "Port:" in normalized_port_text:
                normalized_port_text = normalized_port_text.replace("Port:", "").strip()
            
            # Find the matching port key in self.streams (e.g., "TG 0 - Port: ens5np0")
            port_key = None
            try:
                for key in self.streams.keys():
                    key_interface = key.split(" - ")[-1].replace("Port: ", "").strip()
                    if key_interface == normalized_port_text:
                        port_key = key
                        break
            except (AttributeError, RuntimeError):
                continue
            
            if not port_key:
                continue
            
            stream_list = self.streams.get(port_key, [])

            for stream in stream_list:
                if not isinstance(stream, dict):
                    continue
                ps = stream.setdefault("protocol_selection", {})
                stream_name = ps.get("name") or stream.get("name", "")
                if stream_name == name:
                    # Enabled checkbox (was a Yes/No combo in older revisions)
                    enabled_widget = self.stream_table.cellWidget(row, 3)
                    if enabled_widget is not None:
                        is_enabled = _read_enabled_cell(enabled_widget)
                        ps["enabled"] = is_enabled
                        stream["enabled"] = is_enabled

                    # Flow Tracking ComboBox
                    flow_widget = self.stream_table.cellWidget(row, 15)
                    if flow_widget:
                        is_flow = flow_widget.currentText().lower() == "yes"
                        ps["flow_tracking_enabled"] = is_flow
                        stream["flow_tracking_enabled"] = is_flow
                    
                    # Frame Size (column 8) - sync from table to model
                    frame_size_item = self.stream_table.item(row, 8)
                    if frame_size_item:
                        frame_size_text = frame_size_item.text().strip()
                        if frame_size_text:
                            try:
                                frame_size_int = int(frame_size_text)
                                if 64 <= frame_size_int <= 9216:
                                    frame_size_str = str(frame_size_int)
                                    ps["frame_size"] = frame_size_str
                                    stream["frame_size"] = frame_size_str
                            except (ValueError, TypeError):
                                pass  # Skip invalid values
                    
                    break  # Found the stream, no need to continue



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
        
        # Import here to avoid repeated imports in lambda/partial calls
        # (already imported above, but keeping for clarity)

        # Guard against re-entrancy while we repopulate
        if not hasattr(self, "_populating_table"):
            self._populating_table = False
        if self._populating_table:
            # print(f"[STREAM TABLE] Skipping update - already populating (flag is True)")
            return  # Already updating, skip this call
        
        # Skip refresh if user is currently interacting with the table (has focus, has selection, or is editing)
        # This prevents selection from being cleared and cell editing from being interrupted
        has_selection = False
        if hasattr(self.stream_table, 'selectionModel') and self.stream_table.selectionModel():
            has_selection = len(self.stream_table.selectionModel().selectedRows()) > 0
        
        # Check if a cell is currently being edited
        # Check table state - EditingState means a cell editor is open
        is_editing = False
        try:
            is_editing = self.stream_table.state() == QAbstractItemView.EditingState
        except Exception:
            pass
        
        # Check if any combo box dropdown is open (columns 3 and 15)
        combo_dropdown_open = False
        try:
            for row in range(self.stream_table.rowCount()):
                # Enabled column (3) is now a checkbox — nothing to "open", so no check needed.
                # Flow Tracking (15) is still a combo and can have an open dropdown we must
                # not refresh through.
                flow_combo = self.stream_table.cellWidget(row, 15)
                if flow_combo and isinstance(flow_combo, QComboBox):
                    if flow_combo.view().isVisible():
                        combo_dropdown_open = True
                        break
        except (AttributeError, RuntimeError, TypeError):
            # If we can't check, assume no dropdown is open
            pass
        
        if self.stream_table.hasFocus() or has_selection or is_editing or combo_dropdown_open:
            # Delay refresh to allow user interaction to complete
            # Use longer delay if combo dropdown is open (user needs time to select)
            delay_ms = 1000 if combo_dropdown_open else 500
            if not hasattr(self, "_pending_stream_refresh"):
                self._pending_stream_refresh = False
            if not self._pending_stream_refresh:
                self._pending_stream_refresh = True
                QTimer.singleShot(delay_ms, lambda: self._do_update_stream_table())
                return
        
        # print(f"[STREAM TABLE] Starting _do_update_stream_table() - _populating_table was False")
        self._populating_table = True
        self._pending_stream_refresh = False  # Clear pending flag
        # Block signals temporarily to prevent itemChanged from firing during population
        # This prevents handle_inline_edit from being called during table refresh
        self.stream_table.blockSignals(True)
        self._last_stream_table_update = time.time()
        # print(f"[STREAM TABLE] Set _populating_table to True, starting table update...")

        # Save current selection before clearing table
        selected_stream_ids = set()
        selected_rows = self.stream_table.selectionModel().selectedRows() if hasattr(self.stream_table, 'selectionModel') else []
        for index in selected_rows:
            name_item = self.stream_table.item(index.row(), 2)  # Name column has stream_id in UserRole
            if name_item:
                stream_id = name_item.data(Qt.UserRole)
                if stream_id:
                    selected_stream_ids.add(stream_id)

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
                        # Extract TG ID from the custom widget in column 0
                        tg_id_widget = self.server_tree.itemWidget(parent, 0)
                        tg_id = None
                        if tg_id_widget:
                            tg_id_label = tg_id_widget.findChild(QLabel)
                            if tg_id_label:
                                tg_id = tg_id_label.text()
                        
                        # Fallback: get from server_interfaces based on parent item index
                        if not tg_id:
                            parent_index = self.server_tree.indexOfTopLevelItem(parent)
                            if parent_index >= 0 and parent_index < len(self.server_interfaces):
                                server = self.server_interfaces[parent_index]
                                tg_id = f"TG {server.get('tg_id', '0')}"
                        
                        if not tg_id:
                            continue  # Skip if we can't determine TG ID
                        
                        port_name = item.text(0).strip()  # Interface name (no bullet prefix since we use icons)
                        full_port_name = f"{tg_id} - {port_name}"
                        selected_ports.append(full_port_name)

            # Step 2: Filter online servers
            online_tg_ids = {f"TG {server['tg_id']}" for server in getattr(self, "server_interfaces", [])
                             if server.get("online", True)}
            if not online_tg_ids:
                logger.info("No online servers available. Skipping stream table update.")
                return
            
            # print(f"[DEBUG STREAM TABLE] Selected ports: {selected_ports}")
            # print(f"[DEBUG STREAM TABLE] Online TG IDs: {online_tg_ids}")
            # print(f"[DEBUG STREAM TABLE] Available streams: {list(getattr(self, 'streams', {}).keys())}")

            row_count = 0

            # Step 3: Build table from model
            for port, streams in getattr(self, "streams", {}).items():
                tg_id = port.split(" - ")[0]
                if tg_id not in online_tg_ids:
                    continue

                # Check if this port matches any selected port (handle both formats)
                # If no ports are selected, show all streams from online servers
                port_matches = True  # Default: show all if nothing selected
                if selected_ports:
                    port_matches = False  # Only filter if ports are selected
                    for selected_port in selected_ports:
                        # Normalize both formats for comparison
                        # Stream key format: "TG 0 - Port: ens5np0" or "TG 0 - ens5np0"
                        # Selected port format: "TG 0 - ens5np0" or "TG 0 - Port: ens5np0"
                        port_normalized = port.replace("Port: ", "").strip()
                        selected_port_normalized = selected_port.replace("Port: ", "").strip()
                        # Also try matching just the port name part
                        port_parts = port_normalized.split(" - ")
                        selected_parts = selected_port_normalized.split(" - ")
                        if len(port_parts) >= 2 and len(selected_parts) >= 2:
                            # Compare TG ID and port name separately
                            if port_parts[0].strip() == selected_parts[0].strip() and port_parts[-1].strip() == selected_parts[-1].strip():
                                port_matches = True
                                break
                        # Fallback: exact match after normalization
                        if port_normalized == selected_port_normalized:
                            port_matches = True
                            break
                    if not port_matches:
                        continue

                # Track whether this row is the first one we render for this port —
                # used below to blank the Interface column on continuation rows so a
                # port with multiple streams reads as one visual group instead of
                # three identical-looking rows.
                _port_first_row_rendered = False

                for stream in streams:
                    ps = stream.get("protocol_selection", {})

                    name_lower = (ps.get("name", "") or stream.get("name", "") or "").lower()
                    l4_lower = (ps.get("L4", "") or "").lower()

                    if search_term and (search_term not in port.lower()
                                        and search_term not in name_lower
                                        and search_term not in l4_lower):
                        continue

                    self.stream_table.insertRow(row_count)
                    is_port_continuation = _port_first_row_rendered
                    _port_first_row_rendered = True

                    # (0) Status (read-only) — icon + hover tooltip + accessible label.
                    # Color alone is insufficient (colourblind users see no signal); the
                    # tooltip and Qt accessibility name carry the meaning in text too.
                    status = stream.get("status", "stopped")
                    if status == "running":
                        status_icon = QIcon(r_icon("icons/green_dot.png"))
                        status_label = "Running"
                    elif status == "rx_tracking":
                        status_icon = QIcon(r_icon("icons/blue_dot.png"))
                        status_label = "Tracking RX"
                    else:
                        status_icon = QIcon(r_icon("icons/red_dot.png"))
                        status_label = "Stopped"
                    status_item = QTableWidgetItem()
                    status_item.setIcon(status_icon)
                    status_item.setFlags(Qt.ItemIsEnabled)
                    status_item.setToolTip(status_label)
                    status_item.setData(Qt.AccessibleTextRole, status_label)
                    self.stream_table.setItem(row_count, 0, status_item)

                    # (1) Interface (read-only) - extract just the interface name
                    # Extract interface name from port (e.g., "TG 0 - Port: ens5np0" -> "ens5np0")
                    interface_name = port.split(" - ")[-1] if " - " in port else port
                    # Remove "Port: " prefix if present
                    if ":" in interface_name:
                        interface_name = interface_name.rsplit(":", 1)[-1].strip()
                    if "Port:" in interface_name:
                        interface_name = interface_name.replace("Port:", "").strip()
                    if is_port_continuation:
                        # Same port as the row above — show a subtle indent dash and
                        # park the full port name in the tooltip so it's still
                        # discoverable on hover.
                        iface_item = QTableWidgetItem("↳")
                        iface_item.setForeground(QColor("#9ca3af"))
                        iface_item.setToolTip(interface_name)
                    else:
                        iface_item = QTableWidgetItem(interface_name)
                        # Visually separate the start of a new port group with a
                        # slightly heavier font so the row reads as a header.
                        header_font = iface_item.font()
                        header_font.setBold(True)
                        iface_item.setFont(header_font)
                    iface_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    self.stream_table.setItem(row_count, 1, iface_item)

                    # Ensure stream_id exists (belt & suspenders)
                    sid = stream.get("stream_id")
                    if not sid and hasattr(self, "_alloc_stream_id"):
                        sid = self._alloc_stream_id()
                        stream["stream_id"] = sid

                    # (2) Name (editable) + stash stream_id
                    name_val = ps.get("name", "") or stream.get("name", "") or ""
                    # Trace: compare against any current text in row 2 to detect
                    # populate-overwriting-an-edit. (Cheap; INFO-level.)
                    existing = self.stream_table.item(row_count, 2)
                    if existing is not None and existing.text().strip() != name_val:
                        logger.info(
                            f"[STREAM TABLE] row={row_count} name in cell={existing.text()!r} "
                            f"!= model={name_val!r} (stream_id={sid!r}) — populate will overwrite"
                        )
                    name_item = QTableWidgetItem(name_val)
                    name_item.setData(Qt.UserRole, sid)
                    name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    self.stream_table.setItem(row_count, 2, name_item)

                    # (3) Enabled via centred checkbox (lighter than the previous Yes/No combo)
                    enabled_raw = stream.get("enabled")
                    if enabled_raw is None:
                        enabled_raw = ps.get("enabled", False)
                    enabled_widget, enabled_checkbox = _make_enabled_cell(bool(enabled_raw))
                    enabled_checkbox.setEnabled(status != "rx_tracking")
                    enabled_checkbox.setToolTip("Include this stream when starting traffic")
                    # Use functools.partial to bind the row at connect time and avoid
                    # late-binding/segfault issues when the table is rebuilt mid-signal.
                    enabled_checkbox.stateChanged.connect(
                        partial(self.handle_enabled_combo_change, row=row_count)
                    )
                    self.stream_table.setCellWidget(row_count, 3, enabled_widget)

                    # (4–13) Protocol fields
                    column_keys = [
                        "details", "frame_type", "frame_min", "frame_max", "frame_size",
                        "L1", "VLAN", "L2", "L3", "L4"
                    ]
                    protocol_data = stream.get("protocol_data", {}) or {}
                    
                    for offset, key in enumerate(column_keys):
                        # Read from protocol_selection first, fallback to top-level stream
                        value = ps.get(key) or stream.get(key, "")
                        col_index = 4 + offset

                        # Normalize frame_size in the model if invalid
                        if key == "frame_size":
                            # Validate frame_size: must be a number between 64 and 9216
                            try:
                                frame_size_int = int(str(value)) if value else 0
                                if frame_size_int < 64 or frame_size_int > 9216:
                                    value = "64"  # Invalid range, use default
                                else:
                                    value = str(frame_size_int)  # Normalize to string
                            except (ValueError, TypeError):
                                value = "64"  # Invalid value, use default
                            ps["frame_size"] = value
                            stream["frame_size"] = value
                        
                        # Normalize frame_type, frame_min, frame_max if missing
                        elif key == "frame_type":
                            if not value:
                                value = "Fixed"
                                ps["frame_type"] = value
                                stream["frame_type"] = value
                        elif key == "frame_min":
                            # Validate frame_min: must be a number between 64 and 9216, and <= frame_max
                            try:
                                frame_min_int = int(str(value)) if value else 0
                                frame_max_val = int(ps.get("frame_max") or stream.get("frame_max") or 1518)
                                
                                if frame_min_int < 64 or frame_min_int > 9216:
                                    value = "64"  # Invalid range, use default
                                elif frame_min_int > frame_max_val:
                                    # frame_min must be <= frame_max, adjust to frame_max if needed
                                    value = str(min(frame_max_val, 9216))  # Ensure at most 9216
                                else:
                                    value = str(frame_min_int)  # Normalize to string
                            except (ValueError, TypeError):
                                value = "64"  # Invalid value, use default
                            ps["frame_min"] = value
                            stream["frame_min"] = value
                        elif key == "frame_max":
                            # Validate frame_max: must be a number between 64 and 9216, and >= frame_min
                            try:
                                frame_max_int = int(str(value)) if value else 0
                                frame_min_val = int(ps.get("frame_min") or stream.get("frame_min") or 64)
                                
                                if frame_max_int < 64 or frame_max_int > 9216:
                                    value = "1518"  # Invalid range, use default
                                elif frame_max_int < frame_min_val:
                                    # frame_max must be >= frame_min, adjust to frame_min if needed
                                    value = str(max(frame_min_val, 64))  # Ensure at least 64
                                else:
                                    value = str(frame_max_int)  # Normalize to string
                            except (ValueError, TypeError):
                                value = "1518"  # Invalid value, use default
                            ps["frame_max"] = value
                            stream["frame_max"] = value
                            
                            # Re-validate frame_min after frame_max is set to ensure consistency
                            # This handles the case where frame_min was set before frame_max
                            frame_min_current = ps.get("frame_min") or stream.get("frame_min")
                            if frame_min_current:
                                try:
                                    frame_min_int = int(str(frame_min_current))
                                    frame_max_int = int(str(value))
                                    if frame_min_int > frame_max_int:
                                        # Adjust frame_min to be <= frame_max
                                        adjusted_frame_min = str(min(frame_max_int, 9216))
                                        ps["frame_min"] = adjusted_frame_min
                                        stream["frame_min"] = adjusted_frame_min
                                        # Update the table item if we're still in the same row
                                        frame_min_col = 4 + column_keys.index("frame_min")
                                        frame_min_item = self.stream_table.item(row_count, frame_min_col)
                                        if frame_min_item:
                                            frame_min_item.setText(adjusted_frame_min)
                                except (ValueError, TypeError):
                                    pass  # Skip if validation fails
                        
                        # For VLAN column: show actual VLAN ID if Tagged
                        elif key == "VLAN":
                            if value == "Tagged":
                                vlan_data = protocol_data.get("vlan", {}) or {}
                                vlan_id = vlan_data.get("vlan_id", "")
                                if vlan_id:
                                    value = str(vlan_id)
                                else:
                                    value = "Tagged"  # Fallback if vlan_id not found
                            # Keep "Untagged" or "Stacked" as-is
                        
                        # For L3 column: show actual IPv4 address if IPv4 is selected
                        elif key == "L3":
                            if value == "IPv4":
                                ipv4_data = protocol_data.get("ipv4", {}) or {}
                                src_ip = ipv4_data.get("ipv4_source", "")
                                dst_ip = ipv4_data.get("ipv4_destination", "")
                                if src_ip and dst_ip:
                                    value = f"{src_ip} → {dst_ip}"
                                elif src_ip:
                                    value = src_ip
                                elif dst_ip:
                                    value = dst_ip
                                # If no IPs found, keep "IPv4" as-is
                            elif value == "IPv6":
                                ipv6_data = protocol_data.get("ipv6", {}) or {}
                                src_ip6 = ipv6_data.get("ipv6_source", "")
                                dst_ip6 = ipv6_data.get("ipv6_destination", "")
                                if src_ip6 and dst_ip6:
                                    value = f"{src_ip6} → {dst_ip6}"
                                elif src_ip6:
                                    value = src_ip6
                                elif dst_ip6:
                                    value = dst_ip6
                                # If no IPs found, keep "IPv6" as-is

                        # Mask size cells that don't apply to the current Frame Type so the
                        # table can't show contradictory information (e.g. Min=64 + Max=1518
                        # alongside Fixed=64 when the frame type is Fixed).
                        current_frame_type = (
                            ps.get("frame_type") or stream.get("frame_type") or "Fixed"
                        )
                        cell_disabled = False
                        if current_frame_type == "Fixed" and key in ("frame_min", "frame_max"):
                            value = "—"
                            cell_disabled = True
                        elif current_frame_type in ("Random", "IMIX") and key == "frame_size":
                            value = "—"
                            cell_disabled = True

                        item = QTableWidgetItem(str(value))

                        if key == "frame_size" and not cell_disabled:
                            # (8) Fixed Size — editable when applicable
                            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                        elif cell_disabled:
                            # Greyed-out: enabled (so it renders) but not selectable/editable.
                            # Don't try to subtract flags via `&  ~Qt.ItemIsEditable` — PyQt5
                            # rejects the resulting int. Just set the bits we want.
                            item.setFlags(Qt.ItemIsEnabled)
                            item.setForeground(QColor("#9ca3af"))
                            item.setToolTip(
                                f"Not applicable when Frame Type is {current_frame_type}"
                            )
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
                    # Read flow_tracking_enabled from model (check both locations)
                    flow_flag = stream.get("flow_tracking_enabled")
                    if flow_flag is None:
                        flow_flag = ps.get("flow_tracking_enabled", False)
                    # Block signals while setting initial value to prevent handler from firing during refresh
                    flow_combo.blockSignals(True)
                    flow_combo.setCurrentText("Yes" if flow_flag else "No")
                    flow_combo.blockSignals(False)
                    # Use partial to bind stable row/port
                    flow_combo.currentTextChanged.connect(
                        partial(self.handle_flow_tracking_change, row=row_count, port=port)
                    )
                    self.stream_table.setCellWidget(row_count, 15, flow_combo)

                    row_count += 1

            if row_count > 0:
                # print(f"Stream table updated with {row_count} rows and 16 columns.")
                
                # Restore selection after repopulating
                if selected_stream_ids:
                    selection_model = self.stream_table.selectionModel()
                    if selection_model:
                        from PyQt5.QtCore import QItemSelectionModel
                        for row in range(self.stream_table.rowCount()):
                            name_item = self.stream_table.item(row, 2)
                            if name_item:
                                stream_id = name_item.data(Qt.UserRole)
                                if stream_id and stream_id in selected_stream_ids:
                                    index = self.stream_table.model().index(row, 0)
                                    selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            else:
                logger.debug("No valid streams to display for selected ports.")

            # Resize-after-fill
            self.stream_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

            # Don't sync UI to model during refresh - handlers already update the model
            # sync_ui_to_stream_model() can overwrite user changes if called during refresh
            # It's only needed when explicitly syncing, not during automatic refreshes

        finally:
            # Re-enable signals and clear the population guard
            self.stream_table.blockSignals(False)
            self._populating_table = False
            # Toggle empty-state overlay (lives on the StreamControl mixin)
            if hasattr(self, "update_stream_empty_state"):
                self.update_stream_empty_state()
            # print(f"[STREAM TABLE] Completed _do_update_stream_table() - set _populating_table to False")

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
        # Prevent recursive refreshes - if we're already updating, skip
        if hasattr(self, "_updating_server_tree") and self._updating_server_tree:
            return
        self._updating_server_tree = True
        
        # OPTIMIZATION: Remember current selection before clearing tree to restore it after update
        # This prevents selection from jumping to TG when user had selected an interface
        current_selection = []
        # Only preserve selection if explicitly requested (via _preserve_selection flag)
        should_preserve = hasattr(self, "_preserve_selection") and self._preserve_selection
        if should_preserve:
            for item in self.server_tree.selectedItems():
                parent = item.parent()
                if parent:
                    # Interface (port) selected
                    server_address = parent.text(1)
                    interface_name = item.text(0).strip()
                    current_selection.append(("interface", server_address, interface_name))
                else:
                    # TG (server) selected
                    server_address = item.text(1)
                    current_selection.append(("server", server_address, None))
        
        # Block signals during tree update to prevent cascading selection change events
        # This is safer than disconnect/connect and automatically restores signals
        self.server_tree.blockSignals(True)
        
        self.server_tree.clear()  # Clear the tree before updating

        if not self.server_interfaces:
            # Clear the update flag and restore signals before returning
            self._updating_server_tree = False
            self.server_tree.blockSignals(False)
            return  # Do not add dummy placeholders

        for i, server in enumerate(self.server_interfaces):
            tg_id = f"TG {server['tg_id']}"
            server_address = server["address"]

            # Create TG ID with status icon on the left using a widget
            is_online = server.get("online", True)
            
            # Create a widget for TG ID column with status icon
            tg_id_widget = QWidget()
            tg_id_layout = QHBoxLayout(tg_id_widget)
            tg_id_layout.setContentsMargins(2, 0, 2, 0)
            tg_id_layout.setSpacing(6)
            
            # Status icon label (icon-based, consistent with Streams table)
            status_label = QLabel()
            status_icon = QIcon(r_icon("icons/green_dot.png" if is_online else "icons/red_dot.png"))
            status_pixmap = status_icon.pixmap(12, 12)  # Reduced icon size for better fit
            status_label.setPixmap(status_pixmap)
            status_label.setFixedSize(14, 14)  # Slightly larger to accommodate icon with padding
            status_label.setAlignment(Qt.AlignCenter)
            status_label.setToolTip("Online" if is_online else "Offline")
            tg_id_layout.addWidget(status_label)
            
            # TG ID text label
            tg_id_text_label = QLabel(tg_id)
            tg_id_layout.addWidget(tg_id_text_label)
            tg_id_layout.addStretch()
            
            server_item = QTreeWidgetItem(["", server_address, ""])
            # Hover tooltip surfaces the full address even when the column is too narrow.
            online_label = "Online" if is_online else "Offline"
            server_item.setToolTip(1, f"{server_address}  ({online_label})")
            self.server_tree.addTopLevelItem(server_item)
            # Set the widget for TG ID column (column 0)
            self.server_tree.setItemWidget(server_item, 0, tg_id_widget)
            
            # Store status label and item for later updates
            server["status_label_widget"] = status_label
            server["status_item"] = server_item

            # Checkbox to select server
            checkbox = QCheckBox()
            checkbox.setChecked(server in self.selected_servers)
            checkbox.setFixedSize(24, 24)  # Set fixed size for checkbox (24x24 is standard checkbox size)
            checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

            """checkbox.stateChanged.connect(
                lambda state, idx=i: self.on_server_checkbox_state_changed(idx, state)
            )"""
            from functools import partial  # ensure this import is at the top

            checkbox.stateChanged.connect(
                partial(self.on_server_checkbox_state_changed, i)
            )
            self.server_tree.setItemWidget(server_item, 2, checkbox)

            # Store status info for later updates
            server["status_item"] = server_item  # Store reference for status updates
            server["is_online"] = is_online

            if not is_online:
                continue

            # Use stored interfaces if available, otherwise fetch them
            interfaces = server.get("interfaces")
            if interfaces is None:
                # Defer interface fetching to avoid blocking UI during startup
                # Use cached interfaces if available, otherwise mark as pending
                if server.get("interfaces"):
                    interfaces = server["interfaces"]
                    logger.debug(f"[SERVER TREE] Using cached interfaces for {server_address}")
                else:
                    # Mark as pending and fetch asynchronously
                    server["online"] = False
                    self.update_server_status_icon(server, False)
                    # Schedule async fetch (non-blocking) - use global QTimer import
                    def fetch_interfaces_async():
                        try:
                            # Use shorter timeout to prevent hanging when server is offline
                            if hasattr(self, 'connection_manager') and self.connection_manager:
                                response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=2)
                            else:
                                response = requests.get(f"{server_address}/api/interfaces", timeout=2)
                            if response.status_code == 200:
                                interfaces = response.json()
                                server["interfaces"] = interfaces  # Store for future use
                                server["online"] = True
                                self.update_server_status_icon(server, True)
                                # Refresh tree to show interfaces
                                if hasattr(self, 'update_server_tree'):
                                    QTimer.singleShot(0, self.update_server_tree)
                                logger.info(f"[SERVER TREE] Fetched {len(interfaces)} interfaces from {server_address} (async)")
                            else:
                                logger.warning(f"[SERVER TREE] Server {server_address} returned status code: {response.status_code}")
                                server["online"] = False
                                self.update_server_status_icon(server, False)
                        except Exception as e:
                            logger.error(f"[SERVER TREE] Error fetching interfaces from {server_address} (async): {e}")
                            server["online"] = False
                            self.update_server_status_icon(server, False)
                    QTimer.singleShot(50, fetch_interfaces_async)
                    continue  # Skip this server for now, will be updated asynchronously
            
            if interfaces:
                    # Track added interface names to prevent duplicates
                    added_interfaces = set()
                    
                    for interface in interfaces:
                        port_name = interface['name']
                        interface_status = interface.get('status', 'up')
                        full_interface_name = f"{tg_id} - {port_name}"  # Removed "Port:" prefix

                        if full_interface_name in self.removed_interfaces:
                            continue  # ✅ Skip previously removed interfaces
                        
                        # Skip if we've already added this interface name (deduplication)
                        if port_name in added_interfaces:
                            continue
                        
                        added_interfaces.add(port_name)

                        # Create interface name with icon-based status indicator
                        port_item = QTreeWidgetItem([port_name, ""])  # Remove bullet prefix, use icon instead
                        
                        # Set status icon (icon-based, consistent with Streams table)
                        if interface_status == 'down':
                            status_icon = QIcon(r_icon("icons/red_dot.png"))
                            tooltip = f"{port_name} - Down"
                        else:
                            status_icon = QIcon(r_icon("icons/green_dot.png"))
                            tooltip = f"{port_name} - Up"
                        
                        # Scale icon to smaller size (12x12)
                        icon_pixmap = status_icon.pixmap(12, 12)
                        scaled_icon = QIcon(icon_pixmap)
                        port_item.setIcon(0, scaled_icon)
                        port_item.setToolTip(0, tooltip)
                        
                        # Increase font size for interface names (child items)
                        font = port_item.font(0)
                        font.setPointSize(12)
                        font.setWeight(QFont.Medium)
                        port_item.setFont(0, font)
                        
                        # Interface display updated
                        server_item.addChild(port_item)

            server["online"] = True
            self.update_server_status_icon(server, True)
        
        # OPTIMIZATION: Restore previous selection if it was preserved
        if current_selection and should_preserve:
            try:
                for selection_type, sel_server_address, interface_name in current_selection:
                    # Normalize addresses for comparison
                    normalized_sel_address = sel_server_address
                    if not normalized_sel_address.startswith(("http://", "https://")):
                        normalized_sel_address = f"http://{normalized_sel_address}"
                    
                    # Find the server item by address
                    for i in range(self.server_tree.topLevelItemCount()):
                        top_item = self.server_tree.topLevelItem(i)
                        item_address = top_item.text(1)
                        normalized_item_address = item_address
                        if not normalized_item_address.startswith(("http://", "https://")):
                            normalized_item_address = f"http://{normalized_item_address}"
                        
                        if normalized_item_address == normalized_sel_address or normalized_item_address == normalized_sel_address.replace("http://", "").replace("https://", ""):
                            # Expand the server item to show interfaces
                            top_item.setExpanded(True)
                            
                            if selection_type == "interface" and interface_name:
                                # Restore interface selection - find the specific interface
                                for j in range(top_item.childCount()):
                                    child_item = top_item.child(j)
                                    child_text = child_item.text(0).strip()
                                    # Remove any icon/prefix from comparison
                                    if child_text == interface_name or child_text.endswith(interface_name):
                                        child_item.setSelected(True)
                                        self.server_tree.setCurrentItem(child_item)
                                        break
                                else:
                                    # Interface not found, fall back to server selection
                                    top_item.setSelected(True)
                                    self.server_tree.setCurrentItem(top_item)
                            else:
                                # Restore server (TG) selection
                                top_item.setSelected(True)
                                self.server_tree.setCurrentItem(top_item)
                            break
            except Exception as e:
                logger.error(f"[SERVER TREE] Error restoring selection: {e}")
        
        # Restore signals after tree is rebuilt and selection is restored
        self.server_tree.blockSignals(False)
        
        # Ensure column widths are maintained after update
        self.server_tree.setColumnWidth(0, 180)
        self.server_tree.setColumnWidth(1, 200)
        self.server_tree.setColumnWidth(2, 75)
        
        # If servers are selected (checkboxes checked), select the first TG in the tree
        # This ensures the stream table is populated on startup
        if hasattr(self, "selected_servers") and self.selected_servers:
            # Select the first selected server's TG item in the tree
            first_selected = self.selected_servers[0]
            selected_address = first_selected.get("address")
            for i in range(self.server_tree.topLevelItemCount()):
                item = self.server_tree.topLevelItem(i)
                if item.text(1) == selected_address:
                    # Select this TG item to trigger selection change handler
                    self.server_tree.setCurrentItem(item)
                    # Trigger selection change handler manually to update tables
                    # QTimer is imported at module level, use it directly
                    def trigger_selection_change():
                        self._on_server_selection_changed_combined()
                    QTimer.singleShot(50, trigger_selection_change)
                    break
        
        # Clear the update flag
        self._updating_server_tree = False
        
        # Clear preserve_selection flag if it was set
        if hasattr(self, "_preserve_selection"):
            self._preserve_selection = False
        
        logger.debug(f"Tree widget updated with {len(self.server_interfaces)} servers")
    '''def update_server_status_icon(self, server, is_online):
        """Helper to update status icon based on online state."""
        status_label = server.get("status_label_widget")
        if status_label:
            icon_path = "resources/icons/green_dot.png" if is_online else "resources/icons/red_dot.png"
            pixmap = QPixmap(icon_path).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            status_label.setPixmap(pixmap)
            status_label.repaint()  # ✅ Force visual refresh'''

    def update_server_status_icon(self, server, is_online):
        """Update the status icon on the left of TG ID (green=online, red=offline) safely."""
        status_label = server.get("status_label_widget")
        if not status_label:
            return

        # Update status icon (icon-based, consistent with Streams table)
        status_icon = QIcon(r_icon("icons/green_dot.png" if is_online else "icons/red_dot.png"))
        status_pixmap = status_icon.pixmap(12, 12)  # Reduced icon size for better fit
        status_label.setPixmap(status_pixmap)
        status_label.setToolTip("Online" if is_online else "Offline")
        server["is_online"] = is_online

    def retry_server_connection(self, server):
        """Retry connecting to the specified server and update its status icon."""
        server_address = server["address"]
        logger.info(f"Manually retrying connection to {server_address}...")
        try:
            if hasattr(self, 'connection_manager') and self.connection_manager:
                response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=2)
            else:
                response = requests.get(f"{server_address}/api/interfaces", timeout=2)
            if response.status_code == 200:
                server["online"] = True
                logger.info(f"Server {server_address} is now online.")

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
        # Status is now in TG ID column, no need to repaint separate label
                        break
            else:
                raise Exception(f"Non-200 status: {response.status_code}")
        except Exception as e:
            logger.error(f"Still failed to connect to {server_address}: {e}")
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
                # Extract TG ID from the custom widget in column 0
                tg_id_widget = self.server_tree.itemWidget(parent_item, 0)
                tg_id = None
                if tg_id_widget:
                    # Find the QLabel containing the TG ID text
                    tg_id_label = tg_id_widget.findChild(QLabel)
                    if tg_id_label:
                        tg_id = tg_id_label.text()  # TG ID (e.g., "TG 0")
                
                # Fallback: get from server_interfaces based on parent item index
                if not tg_id:
                    parent_index = self.server_tree.indexOfTopLevelItem(parent_item)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        tg_id = f"TG {server.get('tg_id', '0')}"
                
                if not tg_id:
                    continue  # Skip if we can't determine TG ID
                
                # Get port name (no bullet prefix since we use icons now)
                port_name = item.text(0)  # Interface name (e.g., "ens14f1")
                full_port_name = f"{tg_id} - {port_name}"

                # Add the full port name to removed interfaces
                self.removed_interfaces.add(full_port_name)
                removed_ports.append(full_port_name)

                # Remove the port from the tree
                index = parent_item.indexOfChild(item)
                if index >= 0:  # Ensure index is valid
                    parent_item.takeChild(index)

        if removed_ports:
            logger.info(f"Removed ports: {', '.join(removed_ports)}")
            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            """QMessageBox.information(
                self,
                "Ports Removed",
                f"The following ports were removed:\n{', '.join(removed_ports)}"
            )"""
        else:
            QMessageBox.warning(self, "No Ports Removed", "No valid ports were selected for removal.")
    
    def refresh_selected_server_interfaces(self):
        """Refresh the interface list for the selected server."""
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "No Selection",
                "Please select a server (TG ID) to refresh its interface list."
            )
            return
        
        # OPTIMIZATION: Remember what was originally selected (TG or interface) to restore exact selection
        original_selection = []
        for item in selected_items:
            parent = item.parent()
            if parent:
                # Interface (port) selected - remember both TG and interface name
                server_address = parent.text(1)
                interface_name = item.text(0).strip()
                original_selection.append(("interface", server_address, interface_name))
            else:
                # TG (server) selected
                server_address = item.text(1)
                original_selection.append(("server", server_address, None))
        
        # Get the selected server (parent item if a port is selected, or the item itself if server is selected)
        selected_item = selected_items[0]
        server_item = selected_item.parent() if selected_item.parent() else selected_item
        
        # Get server address from the selected server
        server_address = server_item.text(1)
        if not server_address:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Invalid Selection",
                "Could not determine server address from selection."
            )
            return
        
        # Normalize server address (ensure it has http:// prefix)
        if not server_address.startswith(("http://", "https://")):
            server_address = f"http://{server_address}"
        
        # Find the server in server_interfaces list and clear its cached interfaces
        server_found = False
        for server in self.server_interfaces:
            if server["address"] == server_address or server["address"] == server_address.replace("http://", "").replace("https://", ""):
                # Clear cached interfaces to force fresh fetch
                if "interfaces" in server:
                    del server["interfaces"]
                server_found = True
                logger.debug(f"[REFRESH INTERFACES] Cleared cached interfaces for server: {server_address}")
                break
        
        if not server_found:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Server Not Found",
                f"Could not find server {server_address} in the server list."
            )
            return
        
        # Refresh the entire server tree (this will fetch fresh interfaces from all servers)
        logger.info(f"[REFRESH INTERFACES] Refreshing interface list for server: {server_address}")
        
        # OPTIMIZATION: Tell update_server_tree to preserve selection (it will handle restoration)
        self._preserve_selection = True
        self.update_server_tree()
        
        # OPTIMIZATION: Restore exact original selection (TG or interface) instead of always selecting TG
        # Block signals during selection restoration to prevent triggering selection change handlers
        self.server_tree.blockSignals(True)
        try:
            for selection_type, sel_server_address, interface_name in original_selection:
                # Normalize addresses for comparison
                normalized_sel_address = sel_server_address
                if not normalized_sel_address.startswith(("http://", "https://")):
                    normalized_sel_address = f"http://{normalized_sel_address}"
                
                # Find the server item by address
                for i in range(self.server_tree.topLevelItemCount()):
                    top_item = self.server_tree.topLevelItem(i)
                    item_address = top_item.text(1)
                    normalized_item_address = item_address
                    if not normalized_item_address.startswith(("http://", "https://")):
                        normalized_item_address = f"http://{normalized_item_address}"
                    
                    if normalized_item_address == normalized_sel_address or normalized_item_address == normalized_sel_address.replace("http://", "").replace("https://", ""):
                        # Expand the server item to show interfaces
                        top_item.setExpanded(True)
                        
                        if selection_type == "interface" and interface_name:
                            # Restore interface selection - find the specific interface
                            for j in range(top_item.childCount()):
                                child_item = top_item.child(j)
                                child_text = child_item.text(0).strip()
                                # Remove any icon/prefix from comparison
                                if child_text == interface_name or child_text.endswith(interface_name):
                                    child_item.setSelected(True)
                                    self.server_tree.setCurrentItem(child_item)
                                    logger.debug(f"[REFRESH INTERFACES] Restored selection to interface: {interface_name} on {item_address}")
                                    break
                            else:
                                # Interface not found, fall back to server selection
                                top_item.setSelected(True)
                                self.server_tree.setCurrentItem(top_item)
                                logger.debug(f"[REFRESH INTERFACES] Interface {interface_name} not found, restored selection to server: {item_address}")
                        else:
                            # Restore server (TG) selection
                            top_item.setSelected(True)
                            self.server_tree.setCurrentItem(top_item)
                            logger.debug(f"[REFRESH INTERFACES] Restored selection to server: {item_address}")
                        break
        finally:
            # Always restore signals after selection restoration
            self.server_tree.blockSignals(False)

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
                
                # Extract TG ID from the custom widget in column 0
                tg_id_widget = self.server_tree.itemWidget(parent_item, 0)
                tg_id = None
                if tg_id_widget:
                    tg_id_label = tg_id_widget.findChild(QLabel)
                    if tg_id_label:
                        tg_id = tg_id_label.text()
                
                # Fallback: get from server_interfaces based on parent item index
                if not tg_id:
                    parent_index = self.server_tree.indexOfTopLevelItem(parent_item)
                    if parent_index >= 0 and parent_index < len(self.server_interfaces):
                        server = self.server_interfaces[parent_index]
                        tg_id = f"TG {server.get('tg_id', '0')}"
                
                if not tg_id:
                    continue  # Skip if we can't determine TG ID
                
                # Get port name (no bullet prefix since we use icons now)
                port_name = item.text(0)  # Interface name (e.g., "ens14f1")
                
                # Normalize interface name - extract base interface
                base_interface = port_name
                if ":" in base_interface:
                    base_interface = base_interface.rsplit(":", 1)[-1].strip()
                
                logger.info(f"[INTERFACE RESET] Resetting interface '{base_interface}' (from port '{port_name}')")
                
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
                        logger.info(f"Interface '{base_interface}' reset successfully")
                    else:
                        error_msg = result_data.get("message", "Unknown error")
                        errors.append(f"{base_interface}: {error_msg}")
                        logger.warning(f"Interface '{base_interface}' reset failed: {error_msg}")
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    errors.append(f"{base_interface}: {error_msg}")
                    logger.error(f"Interface '{base_interface}' reset failed: {error_msg}")

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                errors.append(f"{port_name if 'port_name' in locals() else 'Unknown'}: {error_msg}")
                logger.error(f"Exception resetting interface: {e}")
        
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
                        # Check if port_name exists (no bullet prefix needed with icons)
                        if port_name not in existing_ports:
                            port_item = QTreeWidgetItem([port_name, ""])
                            # Set status icon (icon-based, consistent with Streams table)
                            status_icon = QIcon(r_icon("icons/green_dot.png"))
                            # Scale icon to smaller size (12x12)
                            icon_pixmap = status_icon.pixmap(12, 12)
                            scaled_icon = QIcon(icon_pixmap)
                            port_item.setIcon(0, scaled_icon)
                            port_item.setToolTip(0, f"{port_name} - Up")
                            tg_item.addChild(port_item)
                            readded_ports.append(port)
                            # Remove from removed_interfaces
                            self.removed_interfaces.discard(port)

        if readded_ports:
            logger.info(f"Re-added ports: {readded_ports}")
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
                logger.info(f"Server selected: {server['address']}")
        else:
            if server in self.selected_servers:
                self.selected_servers.remove(server)
                logger.info(f"Server deselected: {server['address']}")
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
        logger.error(f"{reason} for {server['address']}")
        self.update_server_status_icon(server, False)
        if server not in self.failed_servers:
            self.failed_servers.append(server)
        if hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(True)