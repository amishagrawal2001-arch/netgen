"""
VXLAN-specific UI logic extracted from devices_tab.py
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

# Logging is configured in traffic_client/main.py
# Just ensure urllib3 doesn't spam DEBUG messages
logging.getLogger('urllib3').setLevel(logging.WARNING)

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
        # Tight chrome — see BGP subtab for rationale.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        table_headers = [
            "Device",
            "Status",
            "VXLAN Interface",
            "Underlay Interface",
            "Overlay Interface",
            "VNI",
            "VLAN ID",
            "VLAN Interface IP",
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

        # v0.3.11: filter input ABOVE the table — parity with the
        # other sub-tabs. VXLAN has a wide 13-column table so the
        # filter is especially valuable here.
        try:
            from utils.table_filter_bar import make_table_filter_row
            _vxlan_filter_row, self.parent._vxlan_filter_input = (
                make_table_filter_row(
                    table=self.parent.vxlan_table,
                    columns=(
                        "Device", "VXLAN Interface", "Underlay Interface",
                        "Overlay Interface", "VNI", "VLAN ID",
                        "VLAN Interface IP", "Local Endpoint",
                        "Remote Endpoint(s)",
                    ),
                    placeholder=(
                        "Device / Interface / VNI / VLAN / Endpoint …"
                    ),
                    tooltip=(
                        "Substring filter — Device / Interface / VNI / "
                        "VLAN / Endpoint columns. Case-insensitive."
                    ),
                )
            )
            layout.addLayout(_vxlan_filter_row)
        except Exception as _e:
            import logging as _lg
            _lg.warning(f"[VXLAN TAB] filter row unavailable: {_e}")

        # Section header removed — tab name + table columns are enough.
        layout.addWidget(self.parent.vxlan_table)

        # v0.2.88: empty-state placeholder.
        try:
            from widgets.empty_state_overlay import EmptyStateOverlay
            self.parent.vxlan_empty_state = EmptyStateOverlay(
                self.parent.vxlan_table,
                "No VXLAN tunnels configured.\n\n"
                "Add a tunnel with the Add button below, or "
                "configure VXLAN on a device via the main Devices "
                "table (right-click → Edit → enable VXLAN). "
                "EVPN Type-2 / Type-5 bulk-inject lives on the "
                "EVPN Inject button (action bar)."
            )
        except Exception:
            pass  # overlay is advisory; never block sub-tab render

        # v0.2.95: Delete-key shortcut + right-click context menu.
        # Same pattern that landed on BGP / OSPF / IS-IS sub-tabs.
        # VXLAN's delete handler removes selected tunnel rows; apply
        # lives on the parent (DevicesTab) so menu reaches into it.
        try:
            from PyQt5.QtWidgets import QShortcut, QMenu
            from PyQt5.QtGui import QKeySequence
            from PyQt5.QtCore import Qt as _Qt
            _vxlan_del = QShortcut(
                QKeySequence(_Qt.Key_Delete), self.parent.vxlan_table,
            )
            _vxlan_del.setContext(_Qt.WidgetShortcut)
            _vxlan_del.activated.connect(self.delete_selected_vxlan_tunnels)

            self.parent.vxlan_table.setContextMenuPolicy(_Qt.CustomContextMenu)
            def _on_vxlan_ctx(pos):
                menu = QMenu(self.parent.vxlan_table)
                act_refresh = menu.addAction("Refresh VXLAN table")
                act_apply   = menu.addAction("Apply VXLAN configurations")
                menu.addSeparator()
                act_delete  = menu.addAction("Delete selected VXLAN tunnel(s)")
                act = menu.exec_(self.parent.vxlan_table.viewport().mapToGlobal(pos))
                if act is act_refresh:
                    try: self.refresh_vxlan_table()
                    except Exception: pass
                elif act is act_apply:
                    try: self.parent.apply_vxlan_configurations()
                    except Exception: pass
                elif act is act_delete:
                    try: self.delete_selected_vxlan_tunnels()
                    except Exception: pass
            self.parent.vxlan_table.customContextMenuRequested.connect(_on_vxlan_ctx)
        except Exception:
            pass

        # VXLAN action bar — unified chrome with Devices + BGP + OSPF + ISIS.
        from PyQt5.QtWidgets import QFrame, QLabel
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border-top: 1px solid #e5e7eb; border-radius: 0; }"
        )
        controls = QHBoxLayout(action_bar)
        controls.setAlignment(Qt.AlignLeft)
        controls.setSpacing(6)
        controls.setContentsMargins(6, 4, 6, 4)

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

        def _vxlan_btn(icon_path, tooltip, style=BTN_BASE):
            b = QPushButton()
            b.setIcon(qicon("resources", icon_path))
            b.setIconSize(QSize(ICON_PX, ICON_PX))
            b.setFixedSize(BTN_W, BTN_H)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(style)
            return b

        # Config group (left)
        self.parent.add_vxlan_button    = _vxlan_btn("icons/add.png",   "Add VXLAN Tunnel")
        self.parent.delete_vxlan_button = _vxlan_btn("icons/Trash.png", "Delete Selected VXLAN Tunnel(s)")

        # Runtime group (right)
        self.parent.apply_vxlan_button  = _vxlan_btn("icons/apply.png",   "Apply VXLAN configurations to server", style=BTN_APPLY)
        refresh_button                  = _vxlan_btn("icons/refresh.png", "Refresh VXLAN status")

        # EVPN Type-2 bulk-inject (v0.2.63). Opens a small dialog that
        # POSTs to /api/evpn/type2/inject + lists active injections. The
        # icon is the generic "advertise" arrow; tooltip carries the
        # plain-English explanation since this is a less-common power
        # user feature.
        evpn_inject_button = _vxlan_btn(
            "icons/start.png",
            "EVPN Type-2 bulk inject — manufacture N synthetic MAC/IP "
            "entries on a VXLAN iface so FRR/BGP advertises them as "
            "Type-2 routes. For scale-testing EVPN peers."
        )

        self.parent.add_vxlan_button.clicked.connect(self.parent.prompt_add_vxlan)
        self.parent.delete_vxlan_button.clicked.connect(self.delete_selected_vxlan_tunnels)
        self.parent.apply_vxlan_button.clicked.connect(self.parent.apply_vxlan_configurations)
        refresh_button.clicked.connect(self.refresh_vxlan_table)
        evpn_inject_button.clicked.connect(self._open_evpn_inject_dialog)

        for b in (self.parent.add_vxlan_button, self.parent.delete_vxlan_button):
            controls.addWidget(b)

        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        controls.addSpacing(4)
        controls.addWidget(sep)
        controls.addSpacing(4)

        for b in (self.parent.apply_vxlan_button, refresh_button,
                  evpn_inject_button):
            controls.addWidget(b)

        controls.addStretch(1)

        # v0.2.78: EVPN active-injections chip. Polls
        # /api/evpn/type2/list every 30s and shows N active records
        # across both kinds. Click opens the EVPN Inject dialog so
        # operators don't have to hunt for the button.
        try:
            from widgets.evpn_active_chip import EvpnActiveChip
            def _resolve_url():
                try:
                    return self.parent.get_server_url(silent=True)
                except Exception:
                    return None
            self.parent.evpn_chip = EvpnActiveChip(
                _resolve_url, parent=action_bar
            )
            self.parent.evpn_chip.clicked.connect(self._open_evpn_inject_dialog)
            controls.addWidget(self.parent.evpn_chip)
        except Exception as _e:
            # Chip is purely advisory — never let it block the action
            # bar from rendering.
            import logging as _lg
            _lg.warning(f"[VXLAN] EVPN active chip unavailable: {_e}")

        layout.addWidget(action_bar)

        # Kick off initial refresh shortly after tab creation
        QTimer.singleShot(200, self.refresh_vxlan_table)
        # Ensure periodic monitoring starts even before VXLAN rows exist
        self.start_monitoring()

    def _open_evpn_inject_dialog(self):
        """Launch the EVPN Type-2 inject dialog (0.2.63).

        Resolves the server URL via the parent Devices tab (same path
        every other VXLAN action uses), then pre-fills the iface from
        the currently-selected VXLAN row when there's exactly one
        selection — operator convenience so they don't retype it.
        """
        try:
            server_url = self.parent.get_server_url(silent=True) \
                if hasattr(self.parent, "get_server_url") else ""
        except Exception:
            server_url = ""
        if not server_url:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.parent, "No server",
                "No server URL configured. Add/select a TG chassis first."
            )
            return

        # Try to pre-fill the iface from the selected VXLAN row. The
        # VXLAN table's column 0 is the device name, but elsewhere we
        # render the VXLAN iface name itself in another column — guard
        # both cases and just leave the field blank if we can't tell.
        default_iface = ""
        try:
            tbl = getattr(self.parent, "vxlan_table", None)
            if tbl is not None:
                rows = sorted({i.row() for i in tbl.selectionModel().selectedIndexes()})
                if len(rows) == 1:
                    headers = [
                        tbl.horizontalHeaderItem(c).text()
                        if tbl.horizontalHeaderItem(c) else ""
                        for c in range(tbl.columnCount())
                    ]
                    # Find the iface column by header text — survives
                    # column reorderings.
                    for label in ("VXLAN Interface", "Interface", "iface"):
                        if label in headers:
                            col = headers.index(label)
                            it = tbl.item(rows[0], col)
                            if it and it.text().strip():
                                default_iface = it.text().strip()
                            break
        except Exception:
            pass

        from widgets.evpn_inject_dialog import EvpnInjectDialog
        dlg = EvpnInjectDialog(self.parent, server_url=server_url,
                               default_iface=default_iface)
        dlg.exec_()

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
            # print("[VXLAN TAB] Starting refresh_vxlan_table")
            logging.debug("[VXLAN TAB] Starting refresh_vxlan_table")
            server_url = self.parent.get_server_url(silent=True)
            
            if not server_url:
                # print("[VXLAN TAB] No server URL available, using local data only")
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
                        # print(f"[VXLAN TAB] Fetched {len(devices_from_db)} devices from database")
                        logging.debug(f"[VXLAN TAB] Fetched {len(devices_from_db)} devices from database")
                    else:
                        # print(f"[VXLAN TAB] Failed to fetch from database: HTTP {response.status_code}")
                        logging.warning(f"[VXLAN TAB] Failed to fetch from database: HTTP {response.status_code}")
                except requests.exceptions.RequestException as exc:
                    # print(f"[VXLAN TAB] Network error fetching VXLAN data from database: {exc}")
                    logging.warning(f"[VXLAN TAB] Network error fetching VXLAN data from database: {exc}")
                except Exception as exc:
                    # print(f"[VXLAN TAB] Error fetching VXLAN data from database: {exc}")
                    logging.warning(f"[VXLAN TAB] Error fetching VXLAN data from database: {exc}")
            else:
                # print("[VXLAN TAB] No server URL, skipping database fetch")
                pass

            # Also check local device data (for unapplied configurations)
            devices_from_local = []
            if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "all_devices"):
                for iface_key, device_list in self.parent.main_window.all_devices.items():
                            for device in device_list:
                                device_name = device.get("Device Name", "Unknown")
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
                                if (vxlan_cfg and isinstance(vxlan_cfg, dict) and len(vxlan_cfg) > 0) or has_vxlan_protocol:
                                    # Create a device dict compatible with database format
                                    # CRITICAL: Store the interface_key from all_devices so we can properly match it later
                                    local_device = {
                                        "device_id": device.get("device_id", ""),
                                        "device_name": device.get("Device Name", ""),
                                        "interface": device.get("Interface", ""),
                                        "interface_key": iface_key,  # Store the actual key from all_devices
                                        "vlan": device.get("VLAN", "0"),
                                        "vxlan_config": vxlan_cfg if vxlan_cfg else {},
                                        "vxlan_state": "Pending",  # Mark as pending until applied
                                    }
                                    devices_from_local.append(local_device)
                                    logging.debug(f"[VXLAN TAB] Found local device with VXLAN: {local_device.get('device_name')}, interface_key: {iface_key}, config keys: {list(vxlan_cfg.keys()) if vxlan_cfg else 'none'}")

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
                        
                        port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix if present
                        if tg_id and port_name:
                            selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
                    else:
                        # This is a parent item (TG) - show all interfaces for this TG
                        # Extract TG ID from custom widget (QWidget with QLabel)
                        tg_id = None
                        tg_id_widget = tree.itemWidget(item, 0)
                        if tg_id_widget:
                            tg_id_label = tg_id_widget.findChild(QLabel)
                            if tg_id_label:
                                tg_id = tg_id_label.text().strip()
                        
                        # Fallback: extract from server_interfaces using item index
                        if not tg_id:
                            item_index = tree.indexOfTopLevelItem(item)
                            if item_index >= 0 and hasattr(self.parent.main_window, "server_interfaces"):
                                if item_index < len(self.parent.main_window.server_interfaces):
                                    server = self.parent.main_window.server_interfaces[item_index]
                                    tg_id = f"TG {server.get('tg_id', '0')}"
                        
                        # If still no TG ID, try text(0) as last resort
                        if not tg_id:
                            tg_id = item.text(0).strip()
                        
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
                    # Use the stored interface_key from all_devices (most reliable)
                    device_interface_key = device.get("interface_key", "")
                    
                    # Normalize interface key matching: extract port name and match against selected interfaces
                    # The interface_key might be " - ens4np0" but selected interface is "TG 0 - ens4np0"
                    port_name = None
                    if device_interface_key:
                        # Extract port name from interface_key (e.g., "ens4np0" from " - ens4np0" or "TG 0 - ens4np0")
                        if " - " in device_interface_key:
                            port_name = device_interface_key.split(" - ")[-1].strip()
                        else:
                            port_name = device_interface_key.strip().lstrip(" - ").strip()
                    
                    # Fallback: try to extract from Interface field if port_name not available
                    if not port_name:
                        device_interface = device.get("Interface", "")
                        if device_interface:
                            port_name = device_interface.strip().lstrip(" - ").strip()
                    
                    # Check if any selected interface matches this port name
                    matches_selected = False
                    if port_name:
                        for selected_iface in interfaces_to_show:
                            # Extract port name from selected interface (e.g., "ens4np0" from "TG 0 - ens4np0")
                            selected_port = selected_iface.split(" - ")[-1].strip() if " - " in selected_iface else selected_iface.strip()
                            if port_name == selected_port or selected_iface.endswith(f" - {port_name}"):
                                matches_selected = True
                                # print(f"[VXLAN TAB] Including local device {device.get('device_name')} - port '{port_name}' matches selected interface '{selected_iface}'")
                                break
                    
                    if not matches_selected:
                        # print(f"[VXLAN TAB] Skipping local device {device.get('device_name')} - port '{port_name}' (from interface_key '{device_interface_key}') not in selected interfaces {interfaces_to_show}")
                        continue  # Skip devices not from selected interfaces
                
                device_id = device.get("device_id")
                device_name = device.get("device_name")
                key = device_id or device_name
                if key and key not in device_map:
                    device_map[key] = device
                    # print(f"[VXLAN TAB] Added local-only device to map: {device_name}")
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
                                # print(f"[VXLAN TAB] Filtering out empty DB tunnel (no VNI) for {device_name}")
                                pass
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
                                # print(f"[VXLAN TAB] Adding new local tunnel VNI {local_vni} not yet in DB")
                    
                    if merged_tunnels:
                        # print(f"[VXLAN TAB] Merging: Using {len(db_tunnels)} DB tunnel(s) + {len([t for t in local_tunnels if isinstance(t, dict) and t.get('vni') not in db_vnis])} new local tunnel(s) = {len(merged_tunnels)} total for {device_name}")
                        db_device["vxlan_config"] = {"tunnels": merged_tunnels}
                        if not db_device.get("vxlan_state") or db_device.get("vxlan_state") == "Disabled":
                            db_device["vxlan_state"] = "Pending"
                    elif db_tunnels:
                        # Only DB tunnels (no new local tunnels)
                        # print(f"[VXLAN TAB] Merging: Using {len(db_tunnels)} DB tunnel(s) for {device_name} (no new local tunnels)")
                        db_device["vxlan_config"] = {"tunnels": db_tunnels}
                    else:
                        # No tunnels in DB and no new local tunnels - clear config
                        # print(f"[VXLAN TAB] Merging: No tunnels in DB or local for {device_name}, clearing config")
                        db_device["vxlan_config"] = {}

            devices = list(device_map.values())
            # print(f"[VXLAN TAB] Total devices after merge: {len(devices)} (DB: {len(devices_from_db)}, Local: {len(devices_from_local)})")
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
                    logging.debug(f"[VXLAN TAB] Selected interfaces: {interfaces_to_show}")
                    logging.debug(f"[VXLAN TAB] Available interface keys in all_devices: {list(self.parent.main_window.all_devices.keys())}")
                    
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
                                    logging.debug(f"[VXLAN TAB] Found matching interface key '{key}' for selected interface '{iface}'")
                                    break
                        
                        logging.debug(f"[VXLAN TAB] Interface '{iface}' has {len(iface_devices)} device(s)")
                        for iface_device in iface_devices:
                            device_id = iface_device.get("device_id")
                            device_name = iface_device.get("Device Name") or iface_device.get("device_name")
                            if device_id:
                                selected_device_ids.add(device_id)
                                logging.debug(f"[VXLAN TAB] Added device_id to filter: {device_id}")
                            if device_name:
                                selected_device_names.add(device_name)
                                logging.debug(f"[VXLAN TAB] Added device_name to filter: {device_name}")
                    
                    logging.debug(f"[VXLAN TAB] Filtering {len(devices)} merged devices using {len(selected_device_ids)} device_id(s) and {len(selected_device_names)} device_name(s)")
                    
                    # Filter merged devices to only include those from selected interfaces
                    for device in devices:
                        dev_id = device.get("device_id")
                        dev_name = device.get("device_name") or device.get("Device Name")
                        match_by_id = dev_id and dev_id in selected_device_ids
                        match_by_name = dev_name and dev_name in selected_device_names
                        if match_by_id or match_by_name:
                            filtered_devices.append(device)
                            logging.debug(f"[VXLAN TAB] Matched device: {dev_name} (id={dev_id}, match_by_id={match_by_id}, match_by_name={match_by_name})")
                        else:
                            logging.debug(f"[VXLAN TAB] Skipped device: {dev_name} (id={dev_id}) - not in selected interfaces")
            else:
                # No interfaces selected, show all devices (same as other tables)
                filtered_devices = devices
            
            # print(f"[VXLAN TAB] Filtered devices: {len(filtered_devices)} (selected interfaces: {len(interfaces_to_show) if interfaces_to_show else 'all'})")

            rows = []
            for device in filtered_devices:
                if not isinstance(device, dict):
                    logging.debug("[VXLAN TAB] Skipping non-dict device entry: %s", device)
                    continue
                
                device_name = device.get('device_name') or device.get('Device Name', 'Unknown')
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
                        logging.debug(f"[VXLAN TAB] Device {device_name}: Found {len(tunnels)} tunnel(s) in list format")
                    elif vxlan_cfg and len(vxlan_cfg) > 0:
                        # Old format: single tunnel dict (backward compatibility)
                        # Check if it has actual VXLAN settings with non-empty values (not just empty keys)
                        has_vni = vxlan_cfg.get('vni') and str(vxlan_cfg.get('vni')).strip()
                        has_local_ip = vxlan_cfg.get('local_ip') and str(vxlan_cfg.get('local_ip')).strip()
                        has_remote_peers = vxlan_cfg.get('remote_peers') and str(vxlan_cfg.get('remote_peers')).strip()
                        has_bridge_svi_ip = vxlan_cfg.get('bridge_svi_ip') and str(vxlan_cfg.get('bridge_svi_ip')).strip()
                        
                        if has_vni or has_local_ip or has_remote_peers or has_bridge_svi_ip:
                            tunnels = [vxlan_cfg]
                            logging.debug(f"[VXLAN TAB] Device {device_name}: Found 1 tunnel in old format (single dict)")
                
                # If no tunnels found, check if we should still show this device
                if not tunnels:
                    cfg_enabled = bool(device.get("vxlan_enabled"))
                    vxlan_state = device.get("vxlan_state", "")
                    has_interface = bool(device.get("vxlan_interface"))
                    
                    # Skip devices where VXLAN was completely removed/disabled
                    # If state is "Disabled", it means VXLAN was removed - don't show in table
                    if vxlan_state == "Disabled":
                        logging.debug(f"[VXLAN TAB] Skipping device {device_name} - VXLAN disabled/removed (state: {vxlan_state})")
                        continue
                    
                    # If no tunnels, no enabled flag, and no interface, skip silently (normal case - device doesn't have VXLAN)
                    if not cfg_enabled and not has_interface:
                        logging.debug(f"[VXLAN TAB] Skipping device {device_name} - no VXLAN configuration")
                        continue
                    
                    # If we reach here, device has VXLAN enabled but no tunnel config yet
                    # Create a placeholder row to show the device is configured for VXLAN
                    logging.debug(f"[VXLAN TAB] Device {device_name}: Creating placeholder row (VXLAN enabled but no tunnel config)")
                    rows.append((device, {}))
                    continue
                
                # Create one row per tunnel
                for tunnel_idx, tunnel_cfg in enumerate(tunnels):
                    if isinstance(tunnel_cfg, dict) and tunnel_cfg:
                        # Skip tunnels with empty VNI or no actual configuration
                        vni = tunnel_cfg.get('vni')
                        if not vni or (isinstance(vni, str) and not vni.strip()) or (vni is None):
                            # print(f"[VXLAN TAB] Skipping tunnel {tunnel_idx+1}/{len(tunnels)} for device {device.get('device_name')} - empty VNI")
                            continue
                        
                        # Create a device copy for this tunnel (so each tunnel gets its own row)
                        tunnel_device = device.copy()
                        # Store the tunnel index for reference
                        tunnel_device["_tunnel_index"] = tunnel_idx
                        tunnel_device["_tunnel_count"] = len(tunnels)
                        rows.append((tunnel_device, tunnel_cfg))
                        logging.debug(f"[VXLAN TAB] Added tunnel {tunnel_idx+1}/{len(tunnels)} for device {device.get('device_name')}, VNI: {tunnel_cfg.get('vni')} (total rows: {len(rows)})")

            # print(f"[VXLAN TAB] Populating table with {len(rows)} rows")
            logging.debug(f"[VXLAN TAB] Populating table with {len(rows)} rows")
            
            # Clear table
            current_row_count = self.parent.vxlan_table.rowCount()
            logging.debug(f"[VXLAN TAB] Clearing table (current rows: {current_row_count})")
            self.parent.vxlan_table.setRowCount(0)
            
            # Add rows
            for idx, (device, vxlan_cfg) in enumerate(rows):
                logging.debug(f"[VXLAN TAB] Adding row {idx+1}/{len(rows)} for device: {device.get('device_name')}, VNI: {vxlan_cfg.get('vni') if isinstance(vxlan_cfg, dict) else 'N/A'}")
                self._append_row(device, vxlan_cfg)

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
            logging.debug(f"[VXLAN TAB] Refresh complete, table now has {final_row_count} rows")
            if final_row_count == 0:
                # Only log when table is empty if there were filtered devices (might indicate an issue)
                if filtered_devices:
                    logging.debug(f"[VXLAN TAB] No VXLAN tunnels found for {len(filtered_devices)} device(s) - this is normal if VXLAN is not configured")
        except Exception as e:
            logger.info(f"[VXLAN TAB] ERROR during refresh: {e}")
            logging.error(f"[VXLAN TAB] ERROR during refresh: {e}", exc_info=True)
            # Show error to user
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self.parent,
                "Refresh Failed",
                f"Failed to refresh VXLAN status table:\n{str(e)}"
            )
        finally:
            # v0.3.11: reapply substring filter so it survives this rebuild.
            try:
                from utils.table_filter_bar import reapply_filter
                reapply_filter(getattr(self.parent, "_vxlan_filter_input", None))
            except Exception:
                pass

    def _append_row(self, device, vxlan_cfg):
        row = self.parent.vxlan_table.rowCount()
        # print(f"[VXLAN TAB] _append_row: Inserting row {row} for device {device.get('device_name') or device.get('Device Name')}")
        self.parent.vxlan_table.insertRow(row)

        device_name = device.get("device_name") or device.get("Device Name") or "Unknown"
        # If multiple tunnels, show tunnel number
        tunnel_count = device.get("_tunnel_count", 1)
        tunnel_index = device.get("_tunnel_index", 0)
        if tunnel_count > 1:
            display_name = f"{device_name} (Tunnel {tunnel_index + 1}/{tunnel_count})"
        else:
            display_name = device_name
        # print(f"[VXLAN TAB] _append_row: Setting device name '{display_name}' at row {row}, col {self.parent.VXLAN_COL['Device']}")
        device_item = QTableWidgetItem(display_name)
        self.parent.vxlan_table.setItem(row, self.parent.VXLAN_COL["Device"], device_item)

        # Set status with both icon and text for better visibility
        state = (device.get("vxlan_state") or "Pending").strip()
        last_error = device.get("vxlan_last_error", "")
        
        # print(f"[VXLAN TAB] _append_row: Setting status for row {row}, state='{state}'")

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
            # print(f"[VXLAN TAB] _append_row: WARNING - Status column not found!")
            pass
        else:
            # Validate column index is within bounds
            max_col = self.parent.vxlan_table.columnCount()
            if status_col_idx < 0 or status_col_idx >= max_col:
                # print(f"[VXLAN TAB] _append_row: WARNING - Status column index {status_col_idx} is out of bounds (max: {max_col-1})")
                pass
            else:
                # print(f"[VXLAN TAB] _append_row: Setting status '{status_text}' at row {row}, col {status_col_idx}")
                self.parent.vxlan_table.setItem(row, status_col_idx, status_item)

        def _set(col, value):
            col_idx = self.parent.VXLAN_COL.get(col)
            if col_idx is None:
                # print(f"[VXLAN TAB] _append_row: WARNING - Column '{col}' not found in VXLAN_COL")
                return
            # Validate column index is within bounds
            max_col = self.parent.vxlan_table.columnCount()
            if col_idx < 0 or col_idx >= max_col:
                # print(f"[VXLAN TAB] _append_row: WARNING - Column index {col_idx} for '{col}' is out of bounds (max: {max_col-1})")
                return
            item = QTableWidgetItem(str(value) if value else "")
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.parent.vxlan_table.setItem(row, col_idx, item)
            # print(f"[VXLAN TAB] _append_row: Set {col}='{value}' at row {row}, col {col_idx}")

        # print(f"[VXLAN TAB] _append_row: vxlan_cfg type: {type(vxlan_cfg)}, keys: {list(vxlan_cfg.keys()) if isinstance(vxlan_cfg, dict) else 'N/A'}")
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
        
        # VLAN ID - from tunnel config
        vlan_id = vxlan_cfg.get("vlan_id") if isinstance(vxlan_cfg, dict) else None
        if vlan_id is None:
            # Fallback to device VLAN if tunnel doesn't have vlan_id
            vlan_id = device.get("vlan", "0")
        _set("VLAN ID", str(vlan_id) if vlan_id else "")
        
        # VLAN Interface IP (Bridge SVI IP) - from tunnel config
        bridge_svi_ip = vxlan_cfg.get("bridge_svi_ip") if isinstance(vxlan_cfg, dict) else None
        if bridge_svi_ip:
            # If it already contains prefix (e.g., "20.0.0.100/24"), use as-is
            # Otherwise, check if bridge_svi_subnet is available
            if '/' not in str(bridge_svi_ip):
                bridge_svi_subnet = vxlan_cfg.get("bridge_svi_subnet") if isinstance(vxlan_cfg, dict) else None
                if bridge_svi_subnet:
                    # Extract prefix from subnet if it's in CIDR format
                    if '/' in str(bridge_svi_subnet):
                        prefix = str(bridge_svi_subnet).split('/')[-1]
                        bridge_svi_ip = f"{bridge_svi_ip}/{prefix}"
                    else:
                        bridge_svi_ip = f"{bridge_svi_ip}/{bridge_svi_subnet}"
                else:
                    # Default to /24 if no subnet specified
                    bridge_svi_ip = f"{bridge_svi_ip}/24"
        _set("VLAN Interface IP", bridge_svi_ip if bridge_svi_ip else "")
        
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
        
        # print(f"[VXLAN TAB] _append_row: Completed row {row}, table now has {self.parent.vxlan_table.rowCount()} rows")

    def _cleanup_vxlan_table_for_device(self, device_id, device_name):
        """Clean up VXLAN table entries for a removed device."""
        try:
            logger.debug(f"[DEBUG VXLAN CLEANUP] Cleaning up VXLAN entries for device '{device_name}' (ID: {device_id})")
            
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
                    logger.debug(f"[DEBUG VXLAN CLEANUP] Found VXLAN row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.parent.vxlan_table.removeRow(row)
                logger.debug(f"[DEBUG VXLAN CLEANUP] Removed VXLAN table row {row}")
            
            logger.debug(f"[DEBUG VXLAN CLEANUP] Successfully cleaned up {len(rows_to_remove)} VXLAN table row(s) for device '{device_name}'")
            
        except Exception as exc:
            logging.warning(f"[VXLAN CLEANUP] Failed to clean up VXLAN table for {device_name}: {exc}")
            logger.debug(f"[DEBUG VXLAN CLEANUP] Error cleaning up VXLAN table: {exc}")
    
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
                
                logger.info(f"[VXLAN DELETE] Deleting tunnel: device={device_name}, VNI={vni}")
                
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
                        logger.info(f"[VXLAN DELETE] Successfully deleted tunnel VNI {vni} for device {device_name}")
                        deleted_count += 1
                        
                        # Remove tunnel from local device data
                        self._remove_tunnel_from_local_device(device_id, device_name, vni)
                        
                        # Collect row for removal (remove in reverse order later)
                        rows_to_remove.append(tunnel_info["row"])
                    else:
                        error_msg = result.get("error", "Unknown error")
                        logger.error(f"[VXLAN DELETE] Failed to delete tunnel VNI {vni}: {error_msg}")
                        failed_count += 1
                else:
                    error_msg = response.text or f"HTTP {response.status_code}"
                    logger.info(f"[VXLAN DELETE] Server error deleting tunnel VNI {vni}: {error_msg}")
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
                logger.info(f"[VXLAN DELETE] Scheduling interface list refresh from server after VXLAN tunnel deletion (delay: 500ms)")
                # Define a callback function to ensure it's called correctly
                def refresh_interfaces():
                    logger.info(f"[VXLAN DELETE] Executing interface list refresh callback")
                    try:
                        if hasattr(self.parent, "main_window") and hasattr(self.parent.main_window, "update_server_tree"):
                            # Clear cached interfaces for all servers to force fresh fetch
                            # This ensures deleted VXLAN/bridge interfaces are removed from the tree
                            if hasattr(self.parent.main_window, "server_interfaces"):
                                for server in self.parent.main_window.server_interfaces:
                                    if "interfaces" in server:
                                        del server["interfaces"]
                                        logger.info(f"[VXLAN DELETE] Cleared cached interfaces for server: {server.get('address', 'unknown')}")
                            
                            # Now refresh the server tree with fresh data
                            self.parent.main_window.update_server_tree()
                            logger.info(f"[VXLAN DELETE] Interface list refresh completed")
                        else:
                            logger.warning(f"[VXLAN DELETE] WARNING: main_window or update_server_tree not available in callback")
                    except Exception as e:
                        logger.info(f"[VXLAN DELETE] ERROR during interface refresh: {e}")
                        logging.error(f"[VXLAN DELETE] ERROR during interface refresh: {e}", exc_info=True)
                QTimer.singleShot(500, refresh_interfaces)
            else:
                logger.warning(f"[VXLAN DELETE] WARNING: Cannot refresh interface list - main_window or update_server_tree not available")
    
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
                        
                        logger.info(f"[VXLAN DELETE] Removed tunnel VNI {vni} from local device {device_name}")
                        return
        except Exception as e:
            logging.error(f"[VXLAN DELETE] Error removing tunnel from local device: {e}")

