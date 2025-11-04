# stream_control.py
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QComboBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QMessageBox, QDialog
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtCore import QTimer
import uuid
import re
import requests

from widgets.stream_dialog import AddStreamDialog
from utils.qicon_loader import qicon, r_icon


class TrafficGenClientStreamControl:
    def __init__(self):
        pass

    def setup_stream_section(self, parent_widget):
        layout = QVBoxLayout(parent_widget)

        # --- Stream Control + Search Bar Layout ---
        control_layout = QHBoxLayout()

        self.start_stream_button = QPushButton()
        self.start_stream_button.setIcon(QIcon(r_icon("icons/start.png")))
        self.start_stream_button.setIconSize(QSize(16, 16))
        self.start_stream_button.setToolTip("Start Selected streams")
        self.start_stream_button.clicked.connect(self.start_stream)
        control_layout.addWidget(self.start_stream_button)

        self.stop_stream_button = QPushButton()
        self.stop_stream_button.setIcon(QIcon(r_icon("icons/stop.png")))
        self.stop_stream_button.setIconSize(QSize(16, 16))
        self.stop_stream_button.setToolTip("Stop Selected streams")
        self.stop_stream_button.clicked.connect(self.stop_stream)
        control_layout.addWidget(self.stop_stream_button)

        # Single Start/Stop ALL toggle
        self.all_streams_toggle_btn = QPushButton()
        self.all_streams_toggle_btn.setIconSize(QSize(16, 16))
        self.all_streams_toggle_btn.setToolTip("Start ALL enabled streams")
        self.all_streams_toggle_btn.clicked.connect(self._toggle_all_streams)

        # 👇 set a default icon right away (so it’s visible at first paint)
        _default_icon = QIcon(r_icon("icons/startallstream.png"))
        if _default_icon.isNull():
            # fallback to text if the file isn't found (helps during dev)
            self.all_streams_toggle_btn.setText("Start All")
        else:
            self.all_streams_toggle_btn.setIcon(_default_icon)

        control_layout.addWidget(self.all_streams_toggle_btn)

        # Let the UI settle, then compute the real state (running/not running)
        QTimer.singleShot(0, self.update_all_streams_toggle_ui)

        self.apply_stream_button = QPushButton()
        self.apply_stream_button.setIcon(QIcon(r_icon("icons/apply.png")))
        self.apply_stream_button.setIconSize(QSize(16, 16))
        self.apply_stream_button.setToolTip("Apply stream changes and restart traffic")
        self.apply_stream_button.clicked.connect(self.apply_stream)
        control_layout.addWidget(self.apply_stream_button)

        control_layout.addStretch(1)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search...")
        self.search_box.setFixedWidth(200)
        self.search_box.returnPressed.connect(self.update_stream_table)
        self.search_box.textChanged.connect(self.update_stream_table)
        control_layout.addWidget(self.search_box)

        clear_search_btn = QPushButton("❌")
        clear_search_btn.setFixedWidth(30)
        clear_search_btn.setToolTip("Clear search")
        clear_search_btn.clicked.connect(lambda: self.search_box.setText(""))
        control_layout.addWidget(clear_search_btn)

        layout.addLayout(control_layout)

        # --- Stream Table ---
        self.stream_table = QTableWidget()
        self.stream_table.setColumnCount(16)
        self.stream_table.setHorizontalHeaderLabels([
            "Status", "Interface", "Name", "Enabled", "Details", "Frame Type",
            "Min Size", "Max Size", "Fixed Size", "L1", "VLAN", "L2", "L3", "L4", "RX Port",
            "Flow Tracking"
        ])
        self.stream_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.stream_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)

        # ✅ ensure multi-select starts/stops work even if user clicks cells
        self.stream_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stream_table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.stream_table.itemChanged.connect(self.handle_inline_edit)
        layout.addWidget(self.stream_table)

        # --- Stream Action Buttons ---
        button_layout = QHBoxLayout()

        add_stream_button = QPushButton(" Add")
        add_stream_button.setIcon(QIcon(r_icon("icons/add.png")))
        add_stream_button.setIconSize(QSize(16, 16))
        add_stream_button.clicked.connect(self.open_add_stream_dialog)
        button_layout.addWidget(add_stream_button)

        edit_stream_button = QPushButton(" Edit")
        edit_stream_button.setIcon(QIcon(r_icon("icons/edit.png")))
        edit_stream_button.setIconSize(QSize(16, 16))
        edit_stream_button.clicked.connect(self.edit_selected_stream)
        button_layout.addWidget(edit_stream_button)

        remove_stream_button = QPushButton(" Delete")
        remove_stream_button.setIcon(QIcon(r_icon("icons/Trash.png")))
        remove_stream_button.setIconSize(QSize(16, 16))
        remove_stream_button.clicked.connect(self.remove_selected_stream)
        button_layout.addWidget(remove_stream_button)


        button_layout.addStretch(1)
        layout.addLayout(button_layout)

    def setup_stream_start_stop_buttons(self):
        """Set up Start and Stop Stream buttons."""
        button_layout = QHBoxLayout()

        self.start_stream_button = QPushButton("Start Stream")
        self.start_stream_button.clicked.connect(self.start_stream)
        button_layout.addWidget(self.start_stream_button)

        self.stop_stream_button = QPushButton("Stop Stream")
        self.stop_stream_button.clicked.connect(self.stop_stream)
        button_layout.addWidget(self.stop_stream_button)

        self.apply_stream_button = QPushButton("Apply Stream")
        self.apply_stream_button.clicked.connect(self.apply_stream)
        button_layout.addWidget(self.apply_stream_button)

        button_layout.addStretch()
        return button_layout

    # ---------- table edit handlers ----------
    def handle_inline_edit(self, item):
        """
        Reliable inline edit handler:
          - Locates the stream by stream_id stored on the Name cell (col 2, Qt.UserRole).
          - Falls back to (port, name) if stream_id isn't present.
          - Updates self.streams first (source of truth), then normalizes the cell UI.
          - Avoids re-entrancy with QSignalBlocker and self._populating_table flag.
        """
        # Ignore programmatic changes during table population
        if getattr(self, "_populating_table", False):
            return

        from PyQt5.QtCore import QSignalBlocker

        row = item.row()
        col = item.column()

        # Retrieve the Name cell (col 2) where we stash stream_id
        name_item = self.stream_table.item(row, 2)
        if not name_item:
            return

        stream_id = name_item.data(Qt.UserRole)

        # Locate the stream
        port = None
        stream = None
        if stream_id:
            # Preferred: lookup by stream_id
            for p, lst in getattr(self, "streams", {}).items():
                for s in lst:
                    if s.get("stream_id") == stream_id:
                        port, stream = p, s
                        break
                if stream:
                    break
        if not stream:
            # Fallback: use (port, name)
            port_item = self.stream_table.item(row, 1)
            if not port_item:
                return
            port = port_item.text().strip()
            current_name = name_item.text().strip()
            for s in self.streams.get(port, []):
                if s.get("protocol_selection", {}).get("name") == current_name:
                    stream = s
                    break
            if not stream:
                return

        ps = stream.setdefault("protocol_selection", {})

        # --- Column-specific updates ---
        if col == 2:
            # Name
            new_name = item.text().strip()
            if not new_name:
                # Revert to previous name if empty
                prev = ps.get("name", stream.get("name", ""))
                with QSignalBlocker(self.stream_table):
                    item.setText(prev)
                return
            # Update model first
            ps["name"] = new_name
            stream["name"] = new_name
            # Normalize UI text (no-op for valid name, but keeps things consistent)
            with QSignalBlocker(self.stream_table):
                item.setText(new_name)

        elif col == 3:
            # Enabled (typed Yes/No if not a combo)
            raw = item.text().strip().lower()
            val = raw in ("yes", "true", "1", "on", "y")
            ps["enabled"] = val
            stream["enabled"] = val
            # Normalize UI
            with QSignalBlocker(self.stream_table):
                item.setText("Yes" if val else "No")

        elif col == 8:
            # Fixed Size (must be positive integer)
            text = item.text().strip()
            try:
                size = int(text)
                if size <= 0:
                    raise ValueError
            except Exception:
                prev = int(ps.get("frame_size") or stream.get("frame_size") or 64)
                with QSignalBlocker(self.stream_table):
                    item.setText(str(prev))
                QMessageBox.warning(self, "Invalid Input", "Frame size must be a positive integer.")
                return
            # Update model first
            ps["frame_size"] = str(size)
            stream["frame_size"] = str(size)
            # Normalize UI
            with QSignalBlocker(self.stream_table):
                item.setText(str(size))

        elif col == 15:
            # Flow Tracking (typed Yes/No if not a combo)
            raw = item.text().strip().lower()
            val = raw in ("yes", "true", "1", "on", "y")
            ps["flow_tracking_enabled"] = val
            stream["flow_tracking_enabled"] = val
            # Normalize UI
            with QSignalBlocker(self, ):
                item.setText("Yes" if val else "No")

        else:
            # Non-editable/unsupported column; ignore
            return

        # Optional: persist & notify server without repainting the whole table
        if hasattr(self, "send_inline_update_to_server") and port:
            try:
                self.send_inline_update_to_server(port, stream)
            except Exception as e:
                print(f"[WARN] send_inline_update_to_server failed: {e}")

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)



    def handle_flow_tracking_change(self, value, row, port=None):
        """
        Flow Tracking combo change handler.
        Keeps model and UI in sync and updates both protocol_selection and top-level keys.
        """
        from PyQt5.QtCore import QSignalBlocker

        # Normalize input to boolean
        val = str(value).strip().lower() in ("yes", "true", "1", "on", "y")

        # Get stream_id from Name cell (col 2)
        name_item = self.stream_table.item(row, 2)
        if not name_item:
            return
        stream_id = name_item.data(Qt.UserRole)

        # Locate stream by ID (preferred)
        stream = None
        resolved_port = None
        if stream_id:
            for p, lst in getattr(self, "streams", {}).items():
                for s in lst:
                    if s.get("stream_id") == stream_id:
                        stream = s
                        resolved_port = p
                        break
                if stream:
                    break

        # Fallback: locate by (port, name) if no/unknown ID
        if not stream:
            if port is None:
                port_item = self.stream_table.item(row, 1)
                if not port_item:
                    return
                resolved_port = port_item.text().strip()
            else:
                resolved_port = port
            current_name = name_item.text().strip()
            for s in self.streams.get(resolved_port, []):
                if s.get("protocol_selection", {}).get("name") == current_name:
                    stream = s
                    break
            if not stream:
                return

        # Update both protocol_selection and top-level flags
        ps = stream.setdefault("protocol_selection", {})
        ps["flow_tracking_enabled"] = val
        stream["flow_tracking_enabled"] = val

        # Normalize the combo text without re-triggering
        combo = self.stream_table.cellWidget(row, 15)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentText("Yes" if val else "No")
            combo.blockSignals(False)

        # Persist / notify if hooks exist
        if hasattr(self, "send_inline_update_to_server") and resolved_port:
            try:
                self.send_inline_update_to_server(resolved_port, stream)
            except Exception as e:
                print(f"[WARN] send_inline_update_to_server failed: {e}")

        # Session save removed - only save on explicit user action (Save Session menu or Apply button)

    def handle_enabled_combo_change(self, value, row):
        """Handle change in Enabled combo box and update stream state."""
        interface_item = self.stream_table.item(row, 1)
        name_item = self.stream_table.item(row, 2)
        if not interface_item or not name_item:
            return

        port = interface_item.text().strip()
        stream_name = name_item.text().strip()
        new_enabled = value.strip().lower() == "yes"

        for stream in self.streams.get(port, []):
            if stream.get("name") == stream_name or stream.get("protocol_selection", {}).get("name") == stream_name:
                stream["enabled"] = new_enabled
                print(f"🔄 Stream '{stream_name}' on {port} enabled set to {new_enabled}")
                self.send_inline_update_to_server(port, stream)
                break

    def update_rx_port(self, port, stream, new_rx):
        """Update rx_port value for the stream."""
        stream["rx_port"] = new_rx.strip()
        print(f"🔁 Updated rx_port for stream '{stream.get('name')}' on {port} to {new_rx}")

    def update_stream_status(self, row, color):
        """Update the stream status icon for a specific row."""
        status_icon = QIcon(r_icon(f"icons/{color}_dot.png"))
        status_item = QTableWidgetItem()
        status_item.setIcon(status_icon)
        status_item.setFlags(Qt.ItemIsEnabled)  # read-only
        self.stream_table.setItem(row, 0, status_item)

    # ---------- copy/paste & CRUD ----------

    def _get_stream_by_port_and_name(self, port: str, stream_name: str):
        """Return the stream dict under `port` whose protocol_selection.name == stream_name."""
        for s in self.streams.get(port, []):
            if s.get("protocol_selection", {}).get("name") == stream_name:
                return s
        return None

    def _collect_selected_table_rows(self):
        """Return list of distinct integer row indices currently selected in the table."""
        # selectedRows() is already row-based due to SelectRows mode,
        # but make it robust if someone changes selection behavior later.
        rows = {idx.row() for idx in self.stream_table.selectionModel().selectedRows()}
        if not rows:
            # Fallback in case selection behavior changes to cells
            rows = {i.row() for i in self.stream_table.selectionModel().selectedIndexes()}
        return sorted(rows)

    def _next_global_str_number(self, used_numbers: set) -> int:
        """Find the next available integer for names 'str<N>' across ALL ports."""
        n = 1
        while n in used_numbers:
            n += 1
        return n

    def _gather_used_str_numbers(self) -> set:
        """Scan all stream names across all ports to collect used numbers for 'str<N>'."""
        used = set()
        for stream_list in self.streams.values():
            for s in stream_list:
                nm = s.get("protocol_selection", {}).get("name", "")
                m = re.fullmatch(r"str(\d+)", nm)
                if m:
                    try:
                        used.add(int(m.group(1)))
                    except ValueError:
                        pass
        return used





    def copy_selected_stream(self):
        rows = self._collect_selected_table_rows()
        if not rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more streams to copy.")
            return

        copied = []
        import copy
        for r in rows:
            iface_item = self.stream_table.item(r, 1)
            name_item = self.stream_table.item(r, 2)
            if not iface_item or not name_item:
                continue
            port = iface_item.text().strip()
            stream_name = name_item.text().strip()
            src = self._get_stream_by_port_and_name(port, stream_name)
            if src:
                c = copy.deepcopy(src)
                # ✅ strip any existing ids to avoid accidental reuse
                c.pop("stream_id", None)
                ps = c.get("protocol_selection", {})
                ps.pop("stream_id", None)
                copied.append(c)

        if not copied:
            QMessageBox.warning(self, "Copy Streams", "Unable to resolve the selected streams to copy.")
            return

        self.copied_streams = copied
        if len(copied) == 1:
            self.copied_stream = copied[0]
        else:
            if hasattr(self, "copied_stream"):
                delattr(self, "copied_stream")

        print(f"[COPY] Prepared {len(self.copied_streams)} stream(s) for paste.")

    def paste_stream_to_interface(self):
        # Accept legacy single-copy clipboard if multi-copy is not present
        if not hasattr(self, 'copied_streams') or not self.copied_streams:
            if hasattr(self, 'copied_stream') and self.copied_stream:
                self.copied_streams = [self.copied_stream]
            else:
                QMessageBox.warning(self, "No Stream Copied", "Please copy one or more streams first.")
                return

        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a TG port to paste the stream(s).")
            return

        selected_item = selected_items[0]
        parent_item = selected_item.parent()
        if parent_item is None:
            QMessageBox.warning(self, "Invalid Selection", "Please select a TG port, not a server.")
            return

        # Properly define all names used below
        tg_id = parent_item.text(0).strip()  # e.g., "TG 0"
        port_label = selected_item.text(0).strip()  # e.g., "Port: enp13s0f0np0"
        tx_port_name = port_label.replace("Port: ", "").strip()
        full_port_name = f"{tg_id} - {port_label}"  # e.g., "TG 0 - Port: enp13s0f0np0"

        if full_port_name not in self.streams:
            self.streams[full_port_name] = []

        # Global name allocator for str<N> and a local set to prevent same-op ID collisions
        used_numbers = self._gather_used_str_numbers()
        local_used_ids = set()

        import copy

        pasted_count = 0
        for src in self.copied_streams:
            dst = copy.deepcopy(src)

            # Strip any stale IDs in payload
            dst.pop("stream_id", None)
            if "protocol_selection" in dst:
                dst["protocol_selection"].pop("stream_id", None)

            # Allocate a new unique display name (str<N>) across all ports
            n = self._next_global_str_number(used_numbers)
            used_numbers.add(n)
            new_name = f"str{n}"

            ps = dst.setdefault("protocol_selection", {})
            ps["name"] = new_name
            ps["enabled"] = True

            # Set RX port to the full "TG X - Port: iface" label for consistency
            rx_full = f"{tg_id} - Port: {tx_port_name}"
            ps["rx_port"] = rx_full

            # Top-level mirrors
            dst["name"] = new_name
            dst["enabled"] = True
            dst["status"] = "stopped"
            dst["rx_port"] = rx_full

            # Allocate a new stream_id with local collision guard
            new_id = self._alloc_stream_id(extra_used=local_used_ids) if hasattr(self, "_alloc_stream_id") else str(
                uuid.uuid4())
            dst["stream_id"] = new_id
            local_used_ids.add(new_id)

            self.streams[full_port_name].append(dst)
            pasted_count += 1
            print(f"[PASTE] '{new_name}' -> {full_port_name}")

        # Clean up legacy single-copy to avoid stale state
        if hasattr(self, "copied_stream"):
            delattr(self, "copied_stream")

        # Final safety sweep and UI refresh
        if hasattr(self, "ensure_unique_stream_ids"):
            self.ensure_unique_stream_ids()
        self.update_stream_table()
        QMessageBox.information(self, "Paste Complete", f"Pasted {pasted_count} stream(s) to {full_port_name}.")

    def _all_stream_ids(self) -> set:
        ids = set()
        for lst in getattr(self, "streams", {}).values():
            for s in lst:
                sid = s.get("stream_id")
                if sid:
                    ids.add(sid)
        return ids
    def _alloc_stream_id(self, extra_used: set = None) -> str:
        """
        Return a new UUID string not present in current streams nor in extra_used.
        extra_used is a per-operation reservation set (e.g., within one multi-paste).
        """
        import uuid
        existing = set(self._all_stream_ids())
        if extra_used:
            existing |= set(extra_used)
        sid = str(uuid.uuid4())
        while sid in existing:
            sid = str(uuid.uuid4())
        return sid


    def ensure_unique_stream_ids(self, fix: bool = True) -> int:
        """
        Ensures every stream across all ports has a unique stream_id.
        Returns the count of IDs it created/repaired.
        """
        seen = set()
        repaired = 0
        for port, lst in getattr(self, "streams", {}).items():
            for s in lst:
                sid = s.get("stream_id")
                if (not sid) or (sid in seen):
                    if fix:
                        sid = self._alloc_stream_id(extra_used=seen)
                        s["stream_id"] = sid
                        repaired += 1
                seen.add(s.get("stream_id"))
        if repaired:
            print(f"[STREAM-ID] Repaired {repaired} missing/duplicate stream_id(s).")
        return repaired

    def open_add_stream_dialog(self):
        print(f"[DEBUG STREAM] Add stream dialog requested")
        print(f"[DEBUG STREAM] Has server_tree: {hasattr(self, 'server_tree')}")
        if hasattr(self, 'server_tree'):
            print(f"[DEBUG STREAM] server_tree is not None: {self.server_tree is not None}")
        
        if not hasattr(self, 'server_tree') or self.server_tree is None:
            QMessageBox.warning(self, "Server Tree Error", "Server tree is not available. Please restart the application.")
            return
            
        selected_items = self.server_tree.selectedItems()
        print(f"[DEBUG STREAM] Selected items count: {len(selected_items)}")
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a TG port to add a stream.")
            return

        selected_item = selected_items[0]
        parent_item = selected_item.parent()

        if parent_item is None:
            QMessageBox.warning(self, "Invalid Selection", "Please select a TG port, not a server.")
            return

        tg_id = parent_item.text(0).replace("TG ", "").strip()
        port_name = selected_item.text(0).replace("Port: ", "").strip()
        # Remove radio symbol if present
        if port_name.startswith("• ") or port_name.startswith("● "):
            port_name = port_name[2:]  # Remove bullet prefix
        full_port_name = f"TG {tg_id} - Port: {port_name}"
        print(f"[DEBUG STREAM] Selected interface: {port_name}")
        print(f"[DEBUG STREAM] Full port name: {full_port_name}")

        # Collect RX ports from online TGs
        server_interfaces = []
        for srv in self.server_interfaces:
            if not srv.get("online", True):
                continue
            tg = srv.get("tg_id", "0")
            try:
                r = requests.get(f"{srv['address']}/api/interfaces", timeout=5)
                r.raise_for_status()
                interfaces = r.json()
                ports = []
                for iface in interfaces:
                    name = iface["name"]
                    if name == "lo":
                        print(f"[DEBUG STREAM] Skipping loopback interface: {name}")
                    elif name == port_name:
                        print(f"[DEBUG STREAM] Skipping selected TX interface: {name} (same as RX)")
                    else:
                        port_entry = f"TG {tg} - Port: {name}"
                        ports.append(port_entry)
                        print(f"[DEBUG] Adding RX Port: {port_entry}")
                server_interfaces.append({"tg_id": tg, "ports": ports})
            except Exception as e:
                print(f"❌ Failed to fetch RX interfaces from {srv['address']}: {e}")

        new_stream_id = str(uuid.uuid4())
        new_stream_data = {"stream_id": new_stream_id}
        print(f"[DEBUG STREAM] Creating dialog for port: {full_port_name}")
        print(f"[DEBUG STREAM] Server interfaces count: {len(server_interfaces)}")
        dialog = AddStreamDialog(self, full_port_name, server_interfaces=server_interfaces, stream_data=new_stream_data)
        print(f"[DEBUG STREAM] Dialog created, about to show...")

        result = dialog.exec()
        print(f"[DEBUG STREAM] Dialog result: {result} (QDialog.Accepted={QDialog.Accepted})")
        if result == QDialog.Accepted:
            stream_details = dialog.get_stream_details()
            if not stream_details.get("rx_port"):
                stream_details["rx_port"] = f"TG {tg_id} - Port: {port_name}"
            #stream_details["stream_id"] = stream_details.get("stream_id") or new_stream_id
            stream_details["stream_id"] = self._alloc_stream_id()

            if full_port_name not in self.streams:
                self.streams[full_port_name] = []

            protocol_section = stream_details.setdefault("protocol_selection", {})
            existing_names = [
                s.get("protocol_selection", {}).get("name", "") for s in self.streams[full_port_name]
            ]
            stream_name = protocol_section.get("name", "").strip()
            if not stream_name or stream_name in existing_names:
                base = "Stream"
                idx = 1
                while f"{base}_{idx}" in existing_names:
                    idx += 1
                stream_name = f"{base}_{idx}"

            protocol_section["name"] = stream_name
            stream_details["name"] = stream_name

            self.streams[full_port_name].append(stream_details)
            self.ensure_unique_stream_ids()
            print(f"[DEBUG] Stream added for {full_port_name}:", stream_details)
            print(f"[DEBUG STREAM] Total streams in self.streams: {sum(len(streams) for streams in self.streams.values())}")
            print(f"[DEBUG STREAM] Streams for this port: {len(self.streams[full_port_name])}")
            self.update_stream_table()

    def edit_selected_stream(self):
        selected_rows = self.stream_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select a stream to edit.")
            return

        try:
            row = selected_rows[0].row()
            interface_item = self.stream_table.item(row, 1)
            stream_name_item = self.stream_table.item(row, 2)
            if not interface_item or not stream_name_item:
                raise ValueError("The selected row does not contain valid stream data.")

            tx_port = interface_item.text().strip()
            stream_name = stream_name_item.text().strip()
            if tx_port not in self.streams:
                raise KeyError(f"TX Port '{tx_port}' not found in streams dictionary.")

            original = next(
                (s for s in self.streams[tx_port] if s.get("protocol_selection", {}).get("name") == stream_name),
                None
            )
            if not original:
                raise KeyError(f"Stream '{stream_name}' not found under '{tx_port}'.")

            import copy
            stream_data = copy.deepcopy(original)

            # flatten protocol_selection into top-level for dialog convenience
            protocol_section = stream_data.get("protocol_selection", {})
            for k, v in protocol_section.items():
                if k not in stream_data:
                    stream_data[k] = v

            tx_port_name = tx_port.split(" - Port:")[-1].strip()
            server_interfaces = []
            for srv in self.server_interfaces:
                if not srv.get("online", True):
                    continue
                tid = srv.get("tg_id", "0")
                try:
                    r = requests.get(f"{srv['address']}/api/interfaces", timeout=5)
                    rx_ports = []
                    for iface in r.json():
                        name = iface["name"]
                        if name != "lo" and name != tx_port_name:
                            rx_ports.append(name)
                    server_interfaces.append({"tg_id": tid, "ports": rx_ports})
                except Exception as e:
                    print(f"❌ Failed to fetch RX interfaces from {srv['address']}: {e}")

            dialog = AddStreamDialog(
                parent=self, interface=tx_port, stream_data=stream_data, server_interfaces=server_interfaces
            )

            if dialog.exec() == QDialog.Accepted:
                edited = dialog.get_stream_details()
                edited_rx = edited.get("rx_port")
                if not edited:
                    QMessageBox.warning(self, "Edit Stream", "No changes were made.")
                    return
                if not edited_rx or edited_rx == "Same as TX Port":
                    edited["rx_port"] = tx_port

                updated = {
                    "stream_id": original.get("stream_id"),
                    "status": original.get("status", "stopped"),
                    "rx_port": edited.get("rx_port", tx_port),
                    "flow_tracking_enabled": edited.get("flow_tracking_enabled", False),
                    "protocol_selection": {},
                    "protocol_data": edited.get("protocol_data", {}),
                    "rocev2": edited.get("rocev2", {}),
                    "uec": edited.get("uec", {}),
                    "override_settings": edited.get("override_settings", {}),
                    "stream_rate_control": edited.get("stream_rate_control", {})
                }

                for k, v in edited.items():
                    if k not in updated and k not in {
                        "protocol_data", "rocev2", "uec", "override_settings",
                        "stream_rate_control", "rx_port", "stream_id", "status", "flow_tracking_enabled"
                    }:
                        updated["protocol_selection"][k] = v

                if "flow_tracking_enabled" in edited:
                    updated["protocol_selection"]["flow_tracking_enabled"] = edited["flow_tracking_enabled"]
                updated["flow_tracking_enabled"] = edited.get("flow_tracking_enabled", False)
                updated["protocol_selection"]["name"] = stream_name

                for i, s in enumerate(self.streams[tx_port]):
                    if s.get("protocol_selection", {}).get("name") == stream_name:
                        self.streams[tx_port][i] = updated
                        break

                self.update_stream_table()
                print(f"[✅] Stream '{stream_name}' updated successfully.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to edit the stream: {e}")

    def remove_selected_stream(self):
        selected_rows = self.stream_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select a stream to remove.")
            return

        try:
            for row in selected_rows:
                r = row.row()
                interface_item = self.stream_table.item(r, 1)
                stream_name_item = self.stream_table.item(r, 2)
                if not interface_item or not stream_name_item:
                    QMessageBox.critical(self, "Error", "Invalid selection. Missing interface or stream name.")
                    continue

                interface = interface_item.text()
                stream_name = stream_name_item.text()
                print(f"Removing stream '{stream_name}' from interface '{interface}'")

                if interface not in self.streams:
                    QMessageBox.warning(self, "Error", f"Interface '{interface}' not found.")
                    continue

                self.streams[interface] = [
                    s for s in self.streams[interface]
                    if s.get("protocol_selection", {}).get("name") != stream_name
                ]

            # Session save removed - only save on explicit user action (Save Session menu or Apply button)
            self.update_stream_table()
            QMessageBox.information(self, "Stream Removed", "Selected streams have been removed.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while removing the stream: {e}")
