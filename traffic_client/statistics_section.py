#statistics_section.py#

from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox
from PyQt5.QtGui import QColor
import requests

class TrafficGenClientStatisticsSection():
    def setup_traffic_statistics_section(self):
        self.statistics_group = QGroupBox("Traffic Statistics")
        layout = QVBoxLayout()

        # Statistics Table
        self.statistics_table = QTableWidget()
        self.statistics_table.setRowCount(10)
        self.statistics_table.setColumnCount(0)
        self.statistics_table.setVerticalHeaderLabels([
            "Status", "Sent Frames", "Received Frames", "Sent Bytes", "Received Bytes",
            "Send Frame Rate (fps)", "Receive Frame Rate (fps)", "Send Bit Rate (bps)",
            "Receive Bit Rate (bps)", "Errors"
        ])
        layout.addWidget(self.statistics_table)
        # Clear Stats Button
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.clear_stats_button_traffic = QPushButton("Clear Stats")
        self.clear_stats_button_traffic.setFixedWidth(120)
        self.clear_stats_button_traffic.clicked.connect(self.clear_cached_statistics)
        button_layout.addWidget(self.clear_stats_button_traffic)
        layout.addLayout(button_layout)

        self.statistics_group.setLayout(layout)
        self.splitter.addWidget(self.statistics_group)
    def fetch_and_update_statistics(self):
        """Fetch traffic statistics from all servers and display for selected ones."""
        if not self.server_interfaces:
            print("No servers available. Clearing traffic statistics.")
            self.clear_statistics_table()
            return

        merged_statistics = {}

        # Step 1: Initialize interface stats
        for server in self.server_interfaces:
            if not server.get("online", True):
                continue

            tg_id = server.get("tg_id")
            server_address = server.get("address")

            try:
                # Use connection manager for better connection handling
                if hasattr(self, 'connection_manager') and self.connection_manager:
                    response = self.connection_manager.get(f"{server_address}/api/interfaces", timeout=2)
                else:
                    response = requests.get(f"{server_address}/api/interfaces", timeout=2)
                if response.status_code == 200:
                    interfaces = response.json()
                    server["online"] = True
                    if server in self.failed_servers:
                        self.failed_servers.remove(server)
                    self.update_server_status_icon(server, True)

                    for interface in interfaces:
                        iface_name = f"TG {tg_id} - {interface['name']}"
                        if iface_name in self.removed_interfaces:
                            continue

                        merged_statistics[iface_name] = {
                            "status": interface.get("status", "N/A"),
                            "tx": 0,
                            "rx": 0,
                            "sent_bytes": 0,
                            "received_bytes": 0,
                            "send_fps": 0,
                            "receive_fps": 0,
                            "send_bps": 0,
                            "receive_bps": 0,
                            "errors": interface.get("errors", 0),
                            "streams": {}
                        }
                else:
                    raise Exception(f"HTTP {response.status_code}")
            except Exception as e:
                print(f"Interface stats fetch failed for {server_address}: {e}")
                server["online"] = False
                self.update_server_status_icon(server, False)
                if server not in self.failed_servers:
                    self.failed_servers.append(server)
                    print(f"[SERVER OFFLINE] Added {server_address} to failed_servers list")
                    print(f"[SERVER OFFLINE] failed_servers count: {len(self.failed_servers)}")

        # Step 2: Process stream stats
        for server in self.server_interfaces:
            if not server.get("online", True):
                continue

            tg_id = server.get("tg_id")
            server_address = server["address"]

            try:
                response = requests.get(f"{server_address}/api/streams/stats", timeout=2)
                if response.status_code == 200:
                    stream_stats = response.json().get("active_streams", [])
                    print(f"stream_stats: {stream_stats}")
                    for stream in stream_stats:
                        tx_port = stream.get("interface")
                        rx_port_raw = stream.get("rx_interface") or stream.get("rx_port")
                        stream_name = stream.get("stream_name", "Unnamed")
                        tx = stream.get("tx_count", 0)
                        rx = stream.get("rx_count", 0)
                        stream_id = stream.get("stream_id")
                        flow_tracking = stream.get("flow_tracking_enabled", False)

                        tx_iface = f"TG {tg_id} - {tx_port}"
                        rx_port_clean = rx_port_raw.split(":")[-1].strip() if rx_port_raw else None
                        rx_iface = f"TG {tg_id} - {rx_port_clean}" if rx_port_clean else None

                        # TX aggregation
                        if tx_iface in merged_statistics:
                            stream_entry = merged_statistics[tx_iface]["streams"].setdefault(stream_name, {})
                            stream_entry["tx_count"] = tx
                            stream_entry["stream_id"] = stream_id
                            stream_entry["flow_tracking_enabled"] = flow_tracking

                            merged_statistics[tx_iface]["tx"] += tx
                            merged_statistics[tx_iface]["sent_bytes"] += tx * 64
                            merged_statistics[tx_iface]["send_fps"] += tx // 10
                            merged_statistics[tx_iface]["send_bps"] += tx * 64 * 8

                        # RX aggregation
                        if rx_iface and rx_iface in merged_statistics:
                            merged_statistics[rx_iface]["rx"] += rx
                            merged_statistics[rx_iface]["received_bytes"] += rx * 64
                            merged_statistics[rx_iface]["receive_fps"] += rx // 10
                            merged_statistics[rx_iface]["receive_bps"] += rx * 64 * 8

                            stream_entry = merged_statistics[rx_iface]["streams"].setdefault(stream_name, {})
                            stream_entry["rx_count"] = rx
                            stream_entry["stream_id"] = stream_id
                            stream_entry["flow_tracking_enabled"] = flow_tracking
            except Exception as e:
                print(f"Stream stats fetch failed for {server_address}: {e}")
                server["online"] = False
                self.update_server_status_icon(server, False)
                if server not in self.failed_servers:
                    self.failed_servers.append(server)

        # Step 3: Filter by selected TGs
        selected_tg_ids = {f"TG {s['tg_id']}" for s in self.selected_servers}
        filtered_statistics = {
            iface: stats for iface, stats in merged_statistics.items()
            if iface.split(" - ")[0] in selected_tg_ids
        }

        if filtered_statistics:
            for iface, stats in filtered_statistics.items():
                prev = self._last_statistics.get(iface, {}) if hasattr(self, "_last_statistics") else {}

                # Preserve previous values if missing
                for key in ["tx", "rx", "sent_bytes", "received_bytes", "send_fps", "receive_fps", "send_bps",
                            "receive_bps"]:
                    if stats.get(key, 0) == 0 and prev.get(key, 0) > 0:
                        stats[key] = prev[key]

                # Merge previous stream stats
                prev_streams = prev.get("streams", {})
                if not stats["streams"]:
                    stats["streams"] = prev_streams.copy()
                else:
                    for sname, sdata in prev_streams.items():
                        if sname not in stats["streams"]:
                            stats["streams"][sname] = sdata

            self.update_statistics_table(filtered_statistics)
            self._last_statistics = filtered_statistics.copy()
        elif hasattr(self, "_last_statistics") and self._last_statistics:
            # Only update if we have meaningful statistics to display
            # Skip the "No new statistics" message to reduce console spam
            self.update_statistics_table(self._last_statistics)
        else:
            self.clear_statistics_table()

        offline_servers = [s for s in self.server_interfaces if s.get("online") is False]
        # Reduced debug output to prevent UI spam
        # print(f"[MENU DEBUG] Total servers: {len(self.server_interfaces)}")
        # print(f"[MENU DEBUG] Offline servers: {len(offline_servers)}")
        # print(f"[MENU DEBUG] Failed servers: {len(self.failed_servers)}")
        
        if offline_servers:
            # print(f"[MENU DEBUG] Found {len(offline_servers)} offline servers, enabling 'Make Server Online' menu")
            self.enable_make_server_online_menu()
        elif hasattr(self, 'make_server_online_action'):
            # print(f"[MENU DEBUG] All servers online, disabling 'Make Server Online' menu")
            self.make_server_online_action.setEnabled(False)
    def poll_stream_stats(self):
        for server in self.selected_servers:
            if not server.get("online", True):
                continue

            url = f"{server['address']}/api/streams/stats"
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    data = response.json()
                    self.update_per_stream_statistics(data.get("active_streams", []))
                else:
                    raise Exception(f"HTTP {response.status_code}")
            except Exception as e:
                print(f"❌ Failed to fetch /api/streams/stats from {url}: {e}")
                #self.mark_server_offline(server, "poll_stream_stats failure")
    def update_per_stream_statistics(self, stream_stats):
        print(f"[DEBUG] update_per_stream_statistics() called with {len(stream_stats)} entries")

        stat_map = {entry.get("stream_id"): entry for entry in stream_stats if entry.get("stream_id")}

        for row in range(self.stream_table.rowCount()):
            stream_name_item = self.stream_table.item(row, 2)
            interface_item = self.stream_table.item(row, 1)

            if not stream_name_item or not interface_item:
                continue

            stream_name = stream_name_item.text().strip()
            interface = interface_item.text().strip()

            # Normalize interface name for matching
            matched_iface = None
            if interface in self.streams:
                matched_iface = interface
            else:
                base = interface.split('.')[0] if '.' in interface else interface
                for k in self.streams:
                    if k.startswith(base):
                        matched_iface = k
                        break

            if not matched_iface:
                continue

            matched_streams = self.streams.get(matched_iface, [])

            for stream in matched_streams:
                if stream.get("name") == stream_name:
                    stream_id = stream.get("stream_id")
                    if stream_id and stream_id in stat_map:
                        stream["status"] = "running"
                        self.update_stream_status(row, "green")
                    else:
                        stream["status"] = "stopped"
                        self.update_stream_status(row, "red")
                    break

    def update_statistics_table(self, statistics):
        """Update the traffic statistics table with per-interface and per-stream stats."""
        self.statistics_table.clearContents()

        base_rows = [
            "Status", "Sent Frames", "Received Frames", "Sent Bytes", "Received Bytes",
            "Send Frame Rate (fps)", "Receive Frame Rate (fps)", "Send Bit Rate (bps)",
            "Receive Bit Rate (bps)", "Errors", "Stream TX", "Stream RX", "Loss %"
        ]

        # Determine max number of stream rows needed
        max_streams = max(
            len(stats.get("streams", {})) for stats in statistics.values()
        ) if statistics else 0

        total_rows = len(base_rows) + max_streams
        self.statistics_table.setRowCount(total_rows)
        self.statistics_table.setColumnCount(len(statistics))

        self.statistics_table.setVerticalHeaderLabels(
            base_rows + [f"Stream {i + 1} TX→RX" for i in range(max_streams)]
        )
        self.statistics_table.setHorizontalHeaderLabels(statistics.keys())

        for col, (iface_name, stats) in enumerate(statistics.items()):
            # Populate base rows
            self.statistics_table.setItem(0, col, QTableWidgetItem(stats.get("status", "N/A")))
            self.statistics_table.setItem(1, col, QTableWidgetItem(str(stats.get("tx", 0))))
            self.statistics_table.setItem(2, col, QTableWidgetItem(str(stats.get("rx", 0))))
            self.statistics_table.setItem(3, col, QTableWidgetItem(str(stats.get("sent_bytes", 0))))
            self.statistics_table.setItem(4, col, QTableWidgetItem(str(stats.get("received_bytes", 0))))
            self.statistics_table.setItem(5, col, QTableWidgetItem(str(stats.get("send_fps", 0))))
            self.statistics_table.setItem(6, col, QTableWidgetItem(str(stats.get("receive_fps", 0))))
            self.statistics_table.setItem(7, col, QTableWidgetItem(str(stats.get("send_bps", 0))))
            self.statistics_table.setItem(8, col, QTableWidgetItem(str(stats.get("receive_bps", 0))))
            self.statistics_table.setItem(9, col, QTableWidgetItem(str(stats.get("errors", 0))))

            stream_tx_total = 0
            stream_rx_total = 0
            stream_row = len(base_rows)

            for stream_name, stream_stats in stats.get("streams", {}).items():
                tx = stream_stats.get("tx_count", 0)
                rx = stream_stats.get("rx_count", None)
                flow_tracking = stream_stats.get("flow_tracking_enabled", False)

                # Default values
                tx_val = tx
                rx_val = rx if rx is not None else ("0" if flow_tracking else "N/A")

                # Compute total loss only when applicable
                if isinstance(tx, int):
                    stream_tx_total += tx
                if isinstance(rx, int):
                    stream_rx_total += rx

                display_text = f"{tx_val} → {rx_val}"
                item = QTableWidgetItem(display_text)

                # Highlight total loss
                if isinstance(tx, int) and tx > 0 and isinstance(rx, int) and rx == 0:
                    item.setForeground(QColor("red"))

                self.statistics_table.setItem(stream_row, col, item)
                stream_row += 1

            # Loss %
            loss_pct = ((stream_tx_total - stream_rx_total) / stream_tx_total * 100) if stream_tx_total else 0.0
            self.statistics_table.setItem(10, col, QTableWidgetItem(str(stream_tx_total)))
            self.statistics_table.setItem(11, col, QTableWidgetItem(str(stream_rx_total)))
            self.statistics_table.setItem(12, col, QTableWidgetItem(f"{loss_pct:.2f}%"))

        print(f"✅ Traffic statistics updated: {len(statistics)} interfaces, {max_streams} max streams.")


    def clear_cached_statistics(self):
        print("[INFO] Manually clearing cached traffic statistics.")
        if hasattr(self, '_last_statistics'):
            del self._last_statistics
        if hasattr(self, '_last_stream_stats'):
            del self._last_stream_stats
        self.clear_statistics_table()
    def clear_statistics_table(self):
        """Clear the traffic statistics table."""
        self.statistics_table.clearContents()
        self.statistics_table.setColumnCount(0)
        self.statistics_table.setRowCount(10)  # Reset rows for default structure
        #print("Traffic statistics cleared.")
    def enable_make_server_online_menu(self):
        """Enable the 'Make Server Online' menu item to allow user-initiated retry."""
        if hasattr(self, 'make_server_online_action'):
            self.make_server_online_action.setEnabled(True)