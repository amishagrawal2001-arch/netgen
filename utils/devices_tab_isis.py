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
        # v0.5.213: added "Route Pools" column (parity with OSPF) so
        # attached pool names for each address family are visible.
        isis_headers = ["Device", "ISIS Status", "Neighbor Type", "Neighbor Hostname", "Interface", "ISIS Area", "Level", "ISIS Net", "System ID", "Hello Interval", "Multiplier", "Route Pools"]
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
        self.parent.isis_table.setColumnWidth(11, 180)  # Route Pools
        
        # Enable inline editing for the ISIS table
        self.parent.isis_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.parent.isis_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect cell changed signal for inline editing
        self.parent.isis_table.cellChanged.connect(self.on_isis_table_cell_changed)
        
        # v0.3.11: filter input ABOVE the table — parity with the
        # other sub-tabs. Substring match across Device / Interface /
        # Area / Level / System ID columns.
        try:
            from utils.table_filter_bar import make_table_filter_row
            _isis_filter_row, self.parent._isis_filter_input = (
                make_table_filter_row(
                    table=self.parent.isis_table,
                    columns=(
                        "Device", "Neighbor Type", "Neighbor Hostname",
                        "Interface", "ISIS Area", "Level", "ISIS Net",
                        "System ID",
                    ),
                    placeholder=(
                        "Device / Interface / Area / Level / NET …"
                    ),
                    tooltip=(
                        "Substring filter — Device / Interface / Area / "
                        "Level / NET / System ID. Case-insensitive."
                    ),
                )
            )
            layout.addLayout(_isis_filter_row)
        except Exception as _e:
            import logging as _lg
            _lg.warning(f"[ISIS TAB] filter row unavailable: {_e}")

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

        # v0.2.95: Delete-key shortcut + right-click context menu.
        # Same pattern that landed on the BGP / OSPF sub-tabs.
        try:
            from PyQt5.QtWidgets import QShortcut, QMenu
            from PyQt5.QtGui import QKeySequence
            from PyQt5.QtCore import Qt as _Qt
            _isis_del = QShortcut(
                QKeySequence(_Qt.Key_Delete), self.parent.isis_table,
            )
            _isis_del.setContext(_Qt.WidgetShortcut)
            _isis_del.activated.connect(self.prompt_delete_isis)

            self.parent.isis_table.setContextMenuPolicy(_Qt.CustomContextMenu)
            def _on_isis_ctx(pos):
                menu = QMenu(self.parent.isis_table)
                act_refresh = menu.addAction("Refresh IS-IS table")
                act_apply   = menu.addAction("Apply IS-IS configurations")
                menu.addSeparator()
                act_delete  = menu.addAction("Delete selected IS-IS")
                act = menu.exec_(self.parent.isis_table.viewport().mapToGlobal(pos))
                if act is act_refresh:
                    try: self.update_isis_table()
                    except Exception: pass
                elif act is act_apply:
                    try: self.apply_isis_configurations()
                    except Exception: pass
                elif act is act_delete:
                    try: self.prompt_delete_isis()
                    except Exception: pass
            self.parent.isis_table.customContextMenuRequested.connect(_on_isis_ctx)
        except Exception:
            pass

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
        self.parent.add_isis_button                = _isis_btn("add.png",    "Add IS-IS")
        self.parent.edit_isis_button               = _isis_btn("edit.png",   "Edit ISIS Configuration")
        self.parent.delete_isis_button             = _isis_btn("remove.png", "Delete ISIS Configuration")
        # v0.5.213: Attach/Detach Route Pools — parity with OSPF UI.
        self.parent.attach_isis_route_pools_button = _isis_btn("readd.png",  "Attach Route Pools to IS-IS Device")
        self.parent.detach_isis_route_pools_button = _isis_btn("remove.png", "Detach Route Pools from IS-IS Device")

        # Runtime group (right)
        self.parent.apply_isis_button   = _isis_btn("apply.png",   "Apply ISIS configurations to server", style=BTN_APPLY)
        self.parent.isis_start_button   = _isis_btn("start.png",   "Start IS-IS")
        self.parent.isis_stop_button    = _isis_btn("stop.png",    "Stop IS-IS")
        self.parent.isis_refresh_button = _isis_btn("refresh.png", "Refresh ISIS Status")

        # Wire signals
        self.parent.add_isis_button.clicked.connect(self.prompt_add_isis)
        self.parent.edit_isis_button.clicked.connect(self.prompt_edit_isis)
        self.parent.delete_isis_button.clicked.connect(self.prompt_delete_isis)
        self.parent.attach_isis_route_pools_button.clicked.connect(self.prompt_attach_route_pools)
        self.parent.detach_isis_route_pools_button.clicked.connect(self.prompt_detach_route_pools)
        self.parent.apply_isis_button.clicked.connect(self.apply_isis_configurations)
        self.parent.isis_start_button.clicked.connect(self.start_isis_protocol)
        self.parent.isis_stop_button.clicked.connect(self.stop_isis_protocol)
        self.parent.isis_refresh_button.clicked.connect(self.refresh_isis_status)

        for b in (self.parent.add_isis_button, self.parent.edit_isis_button,
                  self.parent.delete_isis_button,
                  self.parent.attach_isis_route_pools_button,
                  self.parent.detach_isis_route_pools_button):
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
        """Delete ISIS configuration for the row-selected device / AF.

        v0.5.207: parity with the OSPF v0.5.205 fix. Pre-fix
        the handler read only column 0 (device name) and always
        fired `/api/isis/cleanup` for the whole device, so a
        click on the IPv6 topology row also tore down the IPv4
        adjacency. ISIS multi-topology emits one row per AF
        (utils/devices_tab_isis.py `update_isis_table` around
        line 856-861) so this bug hits exactly like the OSPF
        one did.

        New semantics:
          * If both AFs are enabled and only one row is being
            deleted: flip that AF's `*_enabled` flag off, save
            session, refresh table. `/api/isis/cleanup` is
            SKIPPED — whole-device cleanup would drop the
            surviving AF's adjacency too. Next Apply ISIS
            Configuration reconciles via the v0.5.200 cleanup-
            then-configure path.
          * Last enabled AF being deleted: full removal, same
            shape as pre-fix.
        """
        selected_items = self.parent.isis_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self.parent, "No Selection", "Please select an ISIS configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.parent.isis_table.item(row, 0).text()  # Device column
        # Column 2 is Neighbor Type (IPv4 / IPv6) — see
        # `isis_headers` at line 39. A blank cell (older session
        # / mid-rebuild race) falls through to full-removal so
        # nothing gets silently orphaned.
        protocol_type_item = self.parent.isis_table.item(row, 2)
        protocol_type = protocol_type_item.text() if protocol_type_item else ""

        # Find the device in all_devices using safe helper
        device_info = self.parent._find_device_by_name(device_name)

        if not (device_info and "protocols" in device_info
                and "IS-IS" in device_info["protocols"]):
            QMessageBox.warning(self.parent, "No ISIS Configuration",
                                f"No ISIS configuration found for device '{device_name}'.")
            return

        # Check if ISIS is already marked for removal
        isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {}) or {}
        if isinstance(isis_config, dict) and isis_config.get("_marked_for_removal"):
            QMessageBox.information(self.parent, "Already Marked for Removal",
                                    f"ISIS configuration for '{device_name}' is already marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")
            return

        # Determine current AF state — mirror the fallback in
        # update_isis_table so behavior is consistent across
        # legacy (no flags) and v0.5.207+ (explicit flags) configs.
        device_ipv4 = bool(device_info.get("ipv4_address") or
                           device_info.get("IPv4 Address") or
                           (device_info.get("IPv4", "") or "").strip())
        device_ipv6 = bool(device_info.get("ipv6_address") or
                           device_info.get("IPv6 Address") or
                           (device_info.get("IPv6", "") or "").strip())
        ipv4_enabled = isis_config.get("ipv4_enabled")
        if ipv4_enabled is None:
            ipv4_enabled = device_ipv4
        ipv6_enabled = isis_config.get("ipv6_enabled")
        if ipv6_enabled is None:
            ipv6_enabled = device_ipv6
        both_enabled = bool(ipv4_enabled) and bool(ipv6_enabled)

        # Per-AF disable path — only when BOTH AFs are enabled
        # and the operator clicked exactly one of the two rows.
        if both_enabled and protocol_type in ("IPv4", "IPv6"):
            reply = QMessageBox.question(
                self.parent, "Confirm Deletion",
                f"Disable {protocol_type} IS-IS for '{device_name}'?\n\n"
                f"The other address family will keep running.\n"
                f"Click 'Apply ISIS Configuration' after to sync.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            new_config = dict(isis_config)
            if protocol_type == "IPv4":
                new_config["ipv4_enabled"] = False
            else:
                new_config["ipv6_enabled"] = False
            device_info["isis_config"] = new_config
            # Backward-compat mirror (`is_is_config` is used by
            # some legacy code paths — see line 317).
            device_info["is_is_config"] = new_config

            self.update_isis_table()
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()

            QMessageBox.information(
                self.parent, f"{protocol_type} IS-IS Disabled",
                f"{protocol_type} IS-IS for '{device_name}' has been "
                f"disabled. Click 'Apply ISIS Configuration' to sync "
                f"the change to the server."
            )
            return

        # Full removal path — deleting the last enabled AF, or
        # a row with no AF scope info. Same shape as pre-fix.
        reply = QMessageBox.question(
            self.parent, "Confirm Deletion",
            f"Are you sure you want to delete ISIS configuration for '{device_name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        device_id = device_info.get("device_id")
        if device_id:
            server_url = self.parent.get_server_url()
            if server_url:
                try:
                    response = requests.post(f"{server_url}/api/isis/cleanup",
                                             json={"device_id": device_id},
                                             timeout=10)
                    if response.status_code == 200:
                        logger.info(f"ISIS configuration removed from server for {device_name}")
                    else:
                        error_msg = response.json().get("error", "Unknown error")
                        logger.error(f"Server ISIS cleanup failed for {device_name}: {error_msg}")
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Network error removing ISIS from server for {device_name}: {str(e)}")
            else:
                logger.warning("No server URL available, removing ISIS configuration locally only")

        if isinstance(device_info["protocols"], dict):
            device_info["protocols"]["IS-IS"] = {"_marked_for_removal": True}
        else:
            device_info["isis_config"] = {"_marked_for_removal": True}

        self.update_isis_table()
        if hasattr(self.parent.main_window, "save_session"):
            self.parent.main_window.save_session()

        QMessageBox.information(self.parent, "ISIS Configuration Marked for Removal",
                                f"ISIS configuration for '{device_name}' has been marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")


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

        # v0.5.214: run the network-heavy apply+remove work in a
        # background QThread with a modal QProgressDialog wrapping
        # both. Pre-fix the two `_apply_isis_to_devices` /
        # `_remove_isis_from_devices` calls ran synchronously in
        # the UI thread — for a handful of devices they blocked
        # the whole client for the duration of every `requests
        # .post(..., timeout=30)`, and the operator saw a frozen
        # window with no indication that anything was in flight.
        # OSPF (utils/devices_tab_ospf.py:2563) and BGP (utils/
        # devices_tab_bgp.py:2135) already wrap apply this way;
        # ISIS was the parity gap. Operator report on JNPR-MAC-
        # HWXVX1 2026-08-23: "when applied isis config, apply
        # config progress bar is not visible similar to ospf and
        # bgp."
        if not devices_to_apply_isis and not devices_to_remove_isis:
            return

        from PyQt5.QtCore import QThread, pyqtSignal
        from PyQt5.QtWidgets import QProgressDialog

        class ApplyISISWorker(QThread):
            finished = pyqtSignal(dict)  # {apply, remove} → each: {results, success, failed}

            def __init__(self, handler, apply_list, remove_list, url):
                super().__init__()
                self.handler = handler
                self.apply_list = apply_list
                self.remove_list = remove_list
                self.url = url

            def run(self):
                out = {"apply": None, "remove": None}
                if self.apply_list:
                    out["apply"] = self.handler._apply_isis_network(
                        self.apply_list, self.url)
                if self.remove_list:
                    out["remove"] = self.handler._remove_isis_network(
                        self.remove_list, self.url)
                self.finished.emit(out)

        # Progress dialog — modal, immediate, no cancel button
        # (matches OSPF; interrupting a partially-applied config
        # leaves the FRR container in a half-configured state).
        summary_msg = "Applying ISIS configurations..."
        if devices_to_apply_isis and devices_to_remove_isis:
            summary_msg = (
                f"Applying ISIS ({len(devices_to_apply_isis)}) + "
                f"removing ({len(devices_to_remove_isis)})..."
            )
        elif devices_to_remove_isis and not devices_to_apply_isis:
            summary_msg = f"Removing ISIS from {len(devices_to_remove_isis)} device(s)..."
        else:
            summary_msg = f"Applying ISIS to {len(devices_to_apply_isis)} device(s)..."

        progress = QProgressDialog(summary_msg, "Cancel", 0, 0, self.parent)
        progress.setWindowModality(2)  # Qt.WindowModal
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()

        worker = ApplyISISWorker(self, devices_to_apply_isis,
                                 devices_to_remove_isis, server_url)
        worker.setParent(self.parent)
        worker.finished.connect(
            lambda out: self._on_isis_apply_finished(out, progress))
        # OSPF's guard against "QThread: Destroyed while thread is
        # still running" on PyQt5 5.15.11 + Python 3.14 — hold a
        # ref on `self._isis_apply_workers` for lifetime pinning.
        worker.start()

        if not hasattr(self, "_isis_apply_workers"):
            self._isis_apply_workers = []
        self._isis_apply_workers.append(worker)

        def _still_running(w):
            try:
                return w.isRunning()
            except RuntimeError:
                return False
        self._isis_apply_workers = [
            w for w in self._isis_apply_workers if _still_running(w)]


    def _apply_isis_to_devices(self, devices, server_url):
        """Apply ISIS configuration to the specified devices.

        v0.2.93: collect per-device results into the same shape BGP
        and OSPF use, then surface them through MultiDeviceResultsDialog
        so all 5 protocols' apply-completion UX is consistent. Old
        behaviour was silent-on-success / QMessageBox.critical on
        network error only — operators had no confirmation per
        device.
        """
        # Per-device result strings, prefixed with the same emoji the
        # MultiDeviceResultsDialog colour-codes on (✅ = green, ❌ = red,
        # ⚠️ = orange, ℹ️ = blue, ⏱️ = purple).
        results = []
        success_count = 0
        failed_count = 0
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
                    results.append(
                        f"ℹ️ {device_name}: skipped (missing "
                        f"device_id or isis_config)"
                    )
                    continue

                # Prepare ISIS configuration data using the configure endpoint (similar to OSPF)
                # v0.5.213: include route_pools_per_area + all_route_pools
                # so the server-side `configure_isis_route_advertisement`
                # can generate the static routes / prefix-list / route-map
                # for pools the operator attached via the Attach Route
                # Pools UI. Same shape OSPF uses.
                all_route_pools = getattr(self.parent.main_window, 'bgp_route_pools', [])
                isis_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": device.get("Interface", ""),
                    "vlan": device.get("VLAN", "0"),
                    "ipv4": device.get("IPv4", ""),
                    "ipv6": device.get("IPv6", ""),
                    "ipv4_gateway": device.get("IPv4 Gateway", ""),
                    "ipv6_gateway": device.get("IPv6 Gateway", ""),
                    "isis_config": isis_config,
                    "route_pools_per_area": {},  # Will be populated by server from isis_config["route_pools"]
                    "all_route_pools": all_route_pools,
                }
                
                # Ensure per-device server URL exists
                if not per_device_server_url:
                    logger.info(f"[ISIS POST] No server URL resolved for device '{device_name}'. Skipping.")
                    results.append(
                        f"ℹ️ {device_name}: skipped (no server URL "
                        f"resolved from interface)"
                    )
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
                    results.append(
                        f"❌ {device_name}: network error — {e}"
                    )
                    failed_count += 1
                    continue

                if response.status_code == 200:
                    logger.info(f"ISIS configuration applied to server for {device_name}")
                    results.append(
                        f"✅ {device_name}: ISIS configuration applied"
                    )
                    success_count += 1
                else:
                    try:
                        error_msg = response.json().get("error", response.text)
                    except Exception:
                        error_msg = response.text
                    logger.error(f"Failed to apply ISIS configuration for {device_name} (status {response.status_code}): {error_msg}")
                    # Cap error message at 200 chars so the dialog stays
                    # readable; full text is in the server logs.
                    short = (error_msg or "")[:200]
                    results.append(
                        f"❌ {device_name}: HTTP {response.status_code} — {short}"
                    )
                    failed_count += 1

            # Refresh ISIS table after applying configurations.
            # NB: preflight bar kick_refresh is wired at the outer
            # wrapper (widgets/devices_tab.py:apply_isis_configurations
            # → line ~2370). Adding one here too would be redundant —
            # the wrapper fires after this returns regardless.
            self.update_isis_table()

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error applying ISIS configurations: {str(e)}")
            results.append(f"❌ Network error applying ISIS configurations: {e}")
            failed_count += 1

        # v0.2.93: show MultiDeviceResultsDialog — same UX BGP and OSPF
        # already use. Only when we actually processed something; an
        # empty selection should stay silent.
        if results:
            try:
                from widgets.devices_tab import MultiDeviceResultsDialog
                summary = (
                    f"Applied ISIS configuration: "
                    f"{success_count} succeeded, {failed_count} failed"
                )
                title = (
                    "ISIS Configuration Applied"
                    if failed_count == 0
                    else "ISIS Configuration Partially Applied"
                )
                MultiDeviceResultsDialog(
                    title, summary, results, self.parent
                ).exec_()
            except Exception as dlg_exc:
                # Dialog failure should never block the apply — log
                # and fall through.
                logger.warning(
                    f"[ISIS APPLY] could not show results dialog: {dlg_exc}"
                )


    def _remove_isis_from_devices(self, devices, server_url):
        """Remove ISIS configuration from the specified devices.

        v0.2.93: same MultiDeviceResultsDialog treatment as the
        apply path — per-device results collected and shown at the
        end so operators see what actually happened.
        """
        results = []
        success_count = 0
        failed_count = 0
        try:
            for device in devices:
                device_id = device.get("device_id")
                device_name = device.get("Device Name", "Unknown")
                isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})

                if not device_id:
                    results.append(
                        f"ℹ️ {device_name}: skipped (no device_id)"
                    )
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

                # Record per-device result. Server-side removal +
                # local removal both happen — call out which path
                # succeeded for transparency.
                if server_removal_success:
                    results.append(
                        f"✅ {device_name}: ISIS removed (server + local)"
                    )
                    success_count += 1
                else:
                    # Local removal worked even if server removal
                    # didn't — typical for non-Docker devices or when
                    # the FRR container's already down.
                    results.append(
                        f"⚠️ {device_name}: ISIS removed locally only "
                        f"(server removal skipped or failed; check logs)"
                    )
                    success_count += 1  # local removal IS a success

            # Refresh ISIS table after removing configurations
            self.update_isis_table()

            # Save session
            if hasattr(self.parent.main_window, "save_session"):
                self.parent.main_window.save_session()

        except Exception as e:
            logger.error(f"Error removing ISIS configurations: {str(e)}")
            results.append(f"❌ Error removing ISIS configurations: {e}")
            failed_count += 1

        # Per-device results dialog (v0.2.93).
        if results:
            try:
                from widgets.devices_tab import MultiDeviceResultsDialog
                summary = (
                    f"Removed ISIS configuration: "
                    f"{success_count} succeeded, {failed_count} failed"
                )
                title = (
                    "ISIS Configuration Removed"
                    if failed_count == 0
                    else "ISIS Configuration Removal Partial"
                )
                MultiDeviceResultsDialog(
                    title, summary, results, self.parent
                ).exec_()
            except Exception as dlg_exc:
                logger.warning(
                    f"[ISIS REMOVE] could not show results dialog: {dlg_exc}"
                )


    # ── v0.5.214: network-only helpers for the threaded apply path ──
    # These do the exact same HTTP work `_apply_isis_to_devices` /
    # `_remove_isis_from_devices` do but WITHOUT touching Qt (no
    # MultiDeviceResultsDialog.exec_(), no update_isis_table()) — so
    # they're safe to invoke from an ApplyISISWorker.run() on a
    # background thread. The UI wrappers above still work for any
    # direct caller; the new `apply_isis_configurations` calls
    # these instead and handles UI on the finished signal.

    def _apply_isis_network(self, devices, server_url):
        """Run the ISIS-apply HTTP calls; return
        `{"results": [...], "success_count": N, "failed_count": N}`.
        Safe to call from a QThread.run()."""
        results = []
        success_count = 0
        failed_count = 0
        for device in devices:
            device_id = device.get("device_id")
            device_name = device.get("Device Name", "Unknown")
            per_device_server_url = self.parent._get_server_url_from_interface(
                device.get("Interface", "")) or server_url

            isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
            if not isis_config and isinstance(device.get("protocols"), dict):
                proto = device.get("protocols", {})
                isis_config = proto.get("ISIS", {}) or proto.get("isis", {})

            if not device_id or not isis_config:
                results.append(
                    f"ℹ️ {device_name}: skipped (missing "
                    f"device_id or isis_config)"
                )
                continue

            all_route_pools = getattr(self.parent.main_window,
                                      'bgp_route_pools', [])
            isis_data = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device.get("Interface", ""),
                "vlan": device.get("VLAN", "0"),
                "ipv4": device.get("IPv4", ""),
                "ipv6": device.get("IPv6", ""),
                "ipv4_gateway": device.get("IPv4 Gateway", ""),
                "ipv6_gateway": device.get("IPv6 Gateway", ""),
                "isis_config": isis_config,
                "route_pools_per_area": {},
                "all_route_pools": all_route_pools,
            }

            if not per_device_server_url:
                results.append(
                    f"ℹ️ {device_name}: skipped (no server URL "
                    f"resolved from interface)"
                )
                continue

            post_url = f"{per_device_server_url}/api/device/isis/configure"
            try:
                response = requests.post(post_url, json=isis_data, timeout=30)
            except Exception as e:
                logger.error(f"[ISIS POST] Exception posting to {post_url}: {e}")
                results.append(f"❌ {device_name}: network error — {e}")
                failed_count += 1
                continue

            if response.status_code == 200:
                results.append(f"✅ {device_name}: ISIS configuration applied")
                success_count += 1
            else:
                try:
                    error_msg = response.json().get("error", response.text)
                except Exception:
                    error_msg = response.text
                short = (error_msg or "")[:200]
                results.append(
                    f"❌ {device_name}: HTTP {response.status_code} — {short}")
                failed_count += 1

        return {"results": results, "success_count": success_count,
                "failed_count": failed_count}

    def _remove_isis_network(self, devices, server_url):
        """Run the ISIS-remove HTTP calls + local-only cleanup;
        return `{"results": [...], "success_count": N,
        "failed_count": N}`. Safe to call from a QThread.run().

        Local-only cleanup (mutating the client's `all_devices`
        dict) is still done here rather than deferred to the UI
        thread — dict mutation isn't a Qt call and doesn't need
        the main thread. If concurrency ever becomes an issue
        this'd need moving to `_on_isis_apply_finished`.
        """
        results = []
        success_count = 0
        failed_count = 0
        for device in devices:
            device_id = device.get("device_id")
            device_name = device.get("Device Name", "Unknown")
            isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})

            if not device_id:
                results.append(f"ℹ️ {device_name}: skipped (no device_id)")
                continue

            server_removal_success = False
            if server_url:
                try:
                    isis_data = {"device_id": device_id,
                                 "device_name": device_name,
                                 "isis_config": isis_config}
                    response = requests.post(f"{server_url}/api/device/isis/stop",
                                             json=isis_data, timeout=10)
                    if response.status_code == 200:
                        server_removal_success = True
                    else:
                        try:
                            error_msg = response.json().get("error", "Unknown error")
                        except Exception:
                            error_msg = response.text
                        logger.error(f"Server ISIS removal failed for {device_name}: {error_msg}")
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Network error removing ISIS from server for {device_name}: {e}")

            # Always remove from client's all_devices — legacy behavior.
            if isinstance(device.get("protocols"), dict):
                device["protocols"].pop("IS-IS", None)
            else:
                device.pop("is_is_config", None)
                device.pop("isis_config", None)
                protocols = device.get("protocols", [])
                if isinstance(protocols, list) and "IS-IS" in protocols:
                    protocols.remove("IS-IS")

            if server_removal_success:
                results.append(f"✅ {device_name}: ISIS removed (server + local)")
            else:
                results.append(
                    f"⚠️ {device_name}: ISIS removed locally only "
                    f"(server removal skipped or failed; check logs)")
            success_count += 1

        return {"results": results, "success_count": success_count,
                "failed_count": failed_count}

    def _on_isis_apply_finished(self, out, progress):
        """UI-thread handler: close progress, refresh table, show
        MultiDeviceResultsDialog(s). Called via the ApplyISISWorker's
        `finished` signal so all Qt calls stay on the main thread.
        """
        try:
            progress.close()
        except Exception:
            pass

        # Reap our worker keepalive list.
        if hasattr(self, "_isis_apply_workers"):
            def _still_running(w):
                try:
                    return w.isRunning()
                except RuntimeError:
                    return False
            self._isis_apply_workers = [
                w for w in self._isis_apply_workers if _still_running(w)]

        # Refresh table once (used to happen inside both underlying
        # methods; now consolidated).
        try:
            self.update_isis_table()
        except Exception as exc:
            logger.warning(f"[ISIS APPLY] table refresh failed: {exc}")
        if hasattr(self.parent, "main_window") and hasattr(
                self.parent.main_window, "save_session"):
            try:
                self.parent.main_window.save_session()
            except Exception:
                pass

        # Results dialogs — apply and remove get separate summaries
        # (same shape the old sync path used).
        from widgets.devices_tab import MultiDeviceResultsDialog
        for kind, payload in (("apply", out.get("apply")),
                              ("remove", out.get("remove"))):
            if not payload or not payload.get("results"):
                continue
            success = payload["success_count"]
            failed = payload["failed_count"]
            if kind == "apply":
                verb, past = "Applied", "Applied"
                title_ok = "ISIS Configuration Applied"
                title_partial = "ISIS Configuration Partially Applied"
            else:
                verb, past = "Removed", "Removed"
                title_ok = "ISIS Configuration Removed"
                title_partial = "ISIS Configuration Removal Partial"
            summary = f"{past} ISIS configuration: {success} succeeded, {failed} failed"
            title = title_ok if failed == 0 else title_partial
            try:
                MultiDeviceResultsDialog(title, summary,
                                         payload["results"], self.parent).exec_()
            except Exception as dlg_exc:
                logger.warning(
                    f"[ISIS APPLY] could not show {kind} results dialog: {dlg_exc}")


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
                    
                    # Get ISIS configuration flags.
                    # v0.5.207: fixed the fallback. Pre-fix the
                    # `else` limbs also set True unconditionally
                    # ("Default to True to ensure rows are shown"),
                    # so single-stack devices got phantom rows for
                    # the AF they couldn't run. New (v0.5.207+)
                    # ISIS configs always carry explicit flags
                    # from the dialog; this fallback only fires
                    # for legacy configs, where inferring from
                    # device address presence matches reality.
                    ipv4_enabled = isis_config.get("ipv4_enabled") if isis_config else None
                    if ipv4_enabled is None:
                        ipv4_enabled = bool(device.get("ipv4_address") or
                                            device.get("IPv4 Address"))

                    ipv6_enabled = isis_config.get("ipv6_enabled") if isis_config else None
                    if ipv6_enabled is None:
                        ipv6_enabled = bool(device.get("ipv6_address") or
                                            device.get("IPv6 Address"))
                    
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

                            # Route Pools (column 11) — attached pool names
                            # for this specific address family. v0.5.213
                            # parity with OSPF (`ospf_config["route_pools"]`
                            # dict with "IPv4"/"IPv6" keys).
                            self._set_isis_route_pools_cell(row, isis_config, protocol_type)
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

                        # Route Pools (column 11) — attached pool names
                        # for this AF. v0.5.213 parity with OSPF. On the
                        # marked-for-removal row it stays blank so the
                        # UI reads correctly ("Pending Removal").
                        if is_marked_for_removal:
                            self._set_isis_route_pools_cell(row, isis_config, None)
                        else:
                            # First row is IPv4 when both are enabled,
                            # else whichever single AF is enabled.
                            first_row_af = "IPv4" if ipv4_enabled else ("IPv6" if ipv6_enabled else None)
                            self._set_isis_route_pools_cell(row, isis_config, first_row_af)

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

                            # Route Pools (column 11) — IPv6 second-row.
                            # v0.5.213 parity with OSPF.
                            self._set_isis_route_pools_cell(row, isis_config, "IPv6")

        except Exception as e:
            logger.error(f"Error updating ISIS table: {e}")
        finally:
            # Restore previous signal-block state so we don't accidentally leave
            # signals blocked for caller code that depended on them.
            self.parent.isis_table.blockSignals(signals_were_blocked)
            # v0.3.11: reapply substring filter so it survives this rebuild.
            try:
                from utils.table_filter_bar import reapply_filter
                reapply_filter(getattr(self.parent, "_isis_filter_input", None))
            except Exception:
                pass


    def _set_isis_route_pools_cell(self, row, isis_config, protocol_type):
        """Populate the Route Pools column (index 11) for one ISIS row.

        v0.5.213: mirrors the OSPF handler's per-row Route Pools
        render (utils/devices_tab_ospf.py:680-697). Reads the
        `route_pools` dict from `isis_config` under the AF key
        ("IPv4"/"IPv6"), joins with ", ", and drops a tooltip.

        Args:
            row: table row index
            isis_config: the device's ISIS config dict (may be empty)
            protocol_type: "IPv4", "IPv6", or None (marked-for-removal
                / no-AF rows get an empty cell)
        """
        route_pools_str = ""
        if isis_config and protocol_type in ("IPv4", "IPv6") and "route_pools" in isis_config:
            route_pools = isis_config.get("route_pools", {})
            if isinstance(route_pools, dict):
                pools_for_family = route_pools.get(protocol_type, [])
                if isinstance(pools_for_family, list):
                    route_pools_str = ", ".join(pools_for_family) if pools_for_family else ""
            elif isinstance(route_pools, list):
                # Old format — attribute the flat list to IPv4 only so
                # we don't double-count when both rows are rendered.
                if protocol_type == "IPv4":
                    route_pools_str = ", ".join(route_pools) if route_pools else ""

        pool_item = QTableWidgetItem(route_pools_str)
        if protocol_type in ("IPv4", "IPv6"):
            pool_item.setToolTip(
                f"Attached route pools for {protocol_type}: "
                f"{route_pools_str if route_pools_str else 'None'}"
            )
        self.parent.isis_table.setItem(row, 11, pool_item)


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
            # v0.5.213: same route_pools_per_area / all_route_pools
            # additions as the sync apply path — server needs both to
            # rebuild the per-pool prefix-list + route-map.
            all_route_pools = getattr(self.parent.main_window, 'bgp_route_pools', [])
            isis_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "isis_config": isis_config,
                "route_pools_per_area": {},  # populated server-side from isis_config["route_pools"]
                "all_route_pools": all_route_pools,
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


    def prompt_attach_route_pools(self):
        """Open dialog to attach route pools to selected IS-IS devices.

        v0.5.213: ported from utils/devices_tab_ospf.py:prompt_attach_route_pools.
        Column layout differs: ISIS neighbor-type is column 2 (OSPF has
        it at column 3). Also preserves the ISIS backward-compat mirror
        `is_is_config` on every write.
        """
        # Get selection from ISIS table (not devices table)
        selected_items = self.parent.isis_table.selectedItems()
        if not selected_items:
            # No rows selected - select all rows
            total_rows = self.parent.isis_table.rowCount()
            if total_rows > 0:
                self.parent.isis_table.selectAll()
                logger.info(f"[ISIS TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self.parent, "No IS-IS Devices", "No IS-IS devices are configured. Please add IS-IS configuration first.")
                return

        # Reuse the BGP route-pool store (same shared pool table used by
        # BGP and OSPF; no separate ISIS pool store).
        if not hasattr(self.parent.main_window, 'bgp_route_pools'):
            self.parent.main_window.bgp_route_pools = []

        available_pools = self.parent.main_window.bgp_route_pools

        if not available_pools:
            QMessageBox.warning(self.parent, "No Route Pools",
                              "No route pools have been defined.\n\n"
                              "Please use \U0001F5C2️ 'Manage Route Pools' button (in Devices tab) to create pools first.")
            return

        # Collect all selected IS-IS devices with their address families
        selected_devices = []
        processed_devices = set()

        for item in selected_items:
            row = item.row()
            device_name = self.parent.isis_table.item(row, 0).text()  # Device column
            # Column 2 is Neighbor Type in the ISIS table (OSPF uses col 3).
            neighbor_type_item = self.parent.isis_table.item(row, 2)
            neighbor_type = neighbor_type_item.text() if neighbor_type_item else "IPv4"

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
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue

            # Ensure device_info is a dictionary - handle list case
            if not isinstance(device_info, dict):
                logger.warning(f"[ISIS ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                if isinstance(device_info, list) and len(device_info) > 0:
                    logger.info(f"[ISIS ROUTE POOLS] Attempting to extract dict from list...")
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        continue
                else:
                    continue

            if not isinstance(device_info, dict):
                logger.error(f"[ISIS ROUTE POOLS] Final check failed: device_info is still not a dict for '{device_name}'")
                continue

            # Check that IS-IS is in the protocols list. Accept both
            # "IS-IS" and "ISIS" — both spellings appear in the codebase.
            protocols = device_info.get("protocols", [])
            if isinstance(protocols, str):
                try:
                    import json
                    protocols = json.loads(protocols)
                except Exception:
                    protocols = []

            has_isis = False
            if isinstance(protocols, list):
                has_isis = "IS-IS" in protocols or "ISIS" in protocols
            elif isinstance(protocols, dict):
                has_isis = "IS-IS" in protocols or "ISIS" in protocols
            if not has_isis:
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Device '{device_name}' does not have IS-IS configured")
                continue

            # Get the actual IS-IS configuration (support both storage keys).
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            if not isis_config:
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Device '{device_name}' does not have IS-IS configuration")
                continue

            selected_devices.append({
                "device_name": device_name,
                "device_info": device_info,
                "isis_config": isis_config,
                "address_family": neighbor_type,
            })

        if not selected_devices:
            QMessageBox.warning(self.parent, "No Valid IS-IS Devices",
                              "No valid IS-IS devices found in the selection.")
            return

        # Single-device path: reuse the shared AttachRoutePoolsDialog.
        if len(selected_devices) == 1:
            device_data = selected_devices[0]
            device_name = device_data["device_name"]
            isis_config = device_data["isis_config"]
            address_family = device_data.get("address_family", "IPv4")

            route_pools_dict = isis_config.get("route_pools", {})
            if isinstance(route_pools_dict, list):
                route_pools_dict = {"IPv4": route_pools_dict, "IPv6": []}
            elif not isinstance(route_pools_dict, dict):
                route_pools_dict = {}

            attached_pool_names = route_pools_dict.get(address_family, [])
            if not isinstance(attached_pool_names, list):
                attached_pool_names = []

            # AttachRoutePoolsDialog filters pools by the ipv4_enabled /
            # ipv6_enabled hints — pass a synthetic config that flags the
            # AF the operator picked, same shape the OSPF handler uses.
            isis_config_for_dialog = {
                "ipv4_enabled": address_family == "IPv4",
                "ipv6_enabled": address_family == "IPv6",
            }

            from widgets.add_bgp_route_dialog import AttachRoutePoolsDialog
            dialog = AttachRoutePoolsDialog(self.parent,
                                            device_name=f"{device_name} ({address_family})",
                                            available_pools=available_pools,
                                            attached_pools=attached_pool_names,
                                            bgp_config=isis_config_for_dialog)
            if dialog.exec_() != dialog.Accepted:
                return

            selected_pools = dialog.get_attached_pools()

            if "route_pools" not in isis_config or not isinstance(isis_config["route_pools"], dict):
                existing_list = isis_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    isis_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [],
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    isis_config["route_pools"] = {"IPv4": [], "IPv6": []}

            isis_config["route_pools"][address_family] = selected_pools

            # Keep the legacy `is_is_config` mirror in sync so any code
            # path that still reads it (see prompt_delete_isis line ~317)
            # sees the same pool set.
            device_data["device_info"]["isis_config"] = isis_config
            device_data["device_info"]["is_is_config"] = isis_config
            device_data["device_info"]["_needs_apply"] = True

            self.parent.main_window.save_session()
            self.update_isis_table()

            total_routes = 0
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break

            logger.info(f"[ISIS ROUTE POOLS] Attached {len(selected_pools)} pool(s) ({total_routes} routes) to IS-IS device '{device_name}'")
            QMessageBox.information(self.parent, "Route Pools Attached",
                                  f"Attached {len(selected_pools)} route pool(s) to IS-IS device.\n\n"
                                  f"Device: {device_name}\n"
                                  f"Total routes to advertise: {total_routes}\n\n"
                                  f"Click 'Apply IS-IS' to configure routes on server.")
            return

        # Multi-device path: bulk dialog grouped by AF.
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QDialogButtonBox, QCheckBox, QGroupBox

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
                self.setWindowTitle("Attach Route Pools to Multiple IS-IS Configurations")
                self.setFixedSize(650, 500)
                self.setup_ui()

            def setup_ui(self):
                layout = QVBoxLayout(self)

                devices_group = QGroupBox("Selected IS-IS Configurations")
                devices_layout = QVBoxLayout(devices_group)

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

                devices_text = f"Selected {len(self.selected_devices)} IS-IS configuration(s):\n" + "\n".join(devices_text_parts)
                devices_label = QLabel(devices_text)
                devices_label.setWordWrap(True)
                devices_layout.addWidget(devices_label)
                layout.addWidget(devices_group)

                filtered_pools = []
                for pool in self.available_pools:
                    pool_af = pool.get("address_family", "").lower()
                    if not pool_af:
                        subnet = pool.get("subnet", "")
                        pool_af = "ipv6" if ":" in subnet else "ipv4"
                    pool_af_isis = "IPv4" if pool_af == "ipv4" else "IPv6"
                    if pool_af_isis in self.address_families:
                        filtered_pools.append(pool)

                pools_group = QGroupBox(f"Available Route Pools (for {', '.join(sorted(self.address_families))})")
                pools_layout = QVBoxLayout(pools_group)

                if not filtered_pools:
                    no_pools_label = QLabel(f"No route pools available for {', '.join(sorted(self.address_families))}.\n\n"
                                          f"Please create pools matching these address families first.")
                    no_pools_label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
                    no_pools_label.setAlignment(Qt.AlignCenter)
                    pools_layout.addWidget(no_pools_label)
                    self.pools_list = None
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

                self.summary_label = QLabel()
                self.summary_label.setStyleSheet("background: #e8f4f8; padding: 10px; border-radius: 3px;")
                if self.pools_list is not None:
                    self.pools_list.itemSelectionChanged.connect(self.update_summary)
                self.update_summary()
                layout.addWidget(self.summary_label)

                button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                if self.pools_list is None:
                    button_box.button(QDialogButtonBox.Ok).setEnabled(False)
                    button_box.button(QDialogButtonBox.Ok).setToolTip("No route pools available for selected address families")
                else:
                    button_box.button(QDialogButtonBox.Ok).setEnabled(True)
                    button_box.button(QDialogButtonBox.Ok).setToolTip("Click OK to attach selected pools (or deselect all to remove pools)")
                button_box.accepted.connect(self.accept)
                button_box.rejected.connect(self.reject)
                layout.addWidget(button_box)

            def update_summary(self):
                if self.pools_list is None:
                    self.summary_label.setText("No pools available")
                    return
                selected_items = self.pools_list.selectedItems()
                selected_count = len(selected_items)
                total_routes = 0
                for item in selected_items:
                    text = item.text()
                    parts = text.split(" (")
                    if len(parts) >= 2:
                        count_part = parts[1].split(" routes")[0]
                        try:
                            total_routes += int(count_part)
                        except Exception:
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

        dialog = BulkAttachRoutePoolsDialog(self.parent, selected_devices, available_pools, address_families_in_selection)
        if dialog.exec_() != dialog.Accepted:
            return

        selected_pools = dialog.get_selected_pools()

        if not selected_pools:
            if dialog.pools_list is None:
                address_families_str = ", ".join(sorted(address_families_in_selection))
                QMessageBox.warning(self.parent, "No Pools Available",
                                  f"No route pools are available for {address_families_str}.\n\n"
                                  f"Please create pools matching these address families first.")
                return
            # User intentionally deselected all — treat as bulk detach.
            removed_count = 0
            ipv4_removed = 0
            ipv6_removed = 0

            for device_data in selected_devices:
                device_name = device_data["device_name"]
                isis_config = device_data["isis_config"]
                address_family = device_data.get("address_family", "IPv4")

                if "route_pools" not in isis_config or not isinstance(isis_config["route_pools"], dict):
                    existing_list = isis_config.get("route_pools", [])
                    if isinstance(existing_list, list):
                        isis_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [],
                                                       "IPv6": existing_list if address_family == "IPv6" else []}
                    else:
                        isis_config["route_pools"] = {"IPv4": [], "IPv6": []}

                existing_pools = isis_config["route_pools"].get(address_family, [])
                if existing_pools:
                    removed_count += 1
                    if address_family == "IPv4":
                        ipv4_removed += 1
                    else:
                        ipv6_removed += 1
                    isis_config["route_pools"][address_family] = []
                    device_data["device_info"]["isis_config"] = isis_config
                    device_data["device_info"]["is_is_config"] = isis_config
                    device_data["device_info"]["_needs_apply"] = True

            if removed_count > 0:
                self.parent.main_window.save_session()
                self.update_isis_table()

                removed_parts = []
                if ipv4_removed > 0:
                    removed_parts.append(f"IPv4: {ipv4_removed} configuration(s)")
                if ipv6_removed > 0:
                    removed_parts.append(f"IPv6: {ipv6_removed} configuration(s)")
                removed_text = "\n".join(removed_parts) if removed_parts else "No configurations"

                logger.info(f"[ISIS ROUTE POOLS] Removed all pools from {removed_count} IS-IS configuration(s): {removed_text}")
                QMessageBox.information(self.parent, "Route Pools Removed",
                                      f"Successfully removed all route pools from {removed_count} IS-IS configuration(s):\n\n"
                                      f"{removed_text}\n\n"
                                      f"Click 'Apply IS-IS' to update configuration on server.")
            else:
                QMessageBox.information(self.parent, "No Pools to Remove",
                                      "No route pools were attached to the selected configurations.")
            return

        # Split selected pools by AF and push them into each device's
        # matching AF slot.
        pools_by_af = {"IPv4": [], "IPv6": []}
        for pool_name in selected_pools:
            for pool in available_pools:
                if pool["name"] == pool_name:
                    pool_af = pool.get("address_family", "").lower()
                    if not pool_af:
                        subnet = pool.get("subnet", "")
                        pool_af = "ipv6" if ":" in subnet else "ipv4"
                    pool_af_isis = "IPv4" if pool_af == "ipv4" else "IPv6"
                    pools_by_af[pool_af_isis].append(pool_name)
                    break

        total_devices = 0
        total_routes = 0
        devices_by_name = {}
        ipv4_count = 0
        ipv6_count = 0

        for device_data in selected_devices:
            device_name = device_data["device_name"]
            isis_config = device_data["isis_config"]
            address_family = device_data.get("address_family", "IPv4")

            pools_for_this_af = pools_by_af.get(address_family, [])
            if not pools_for_this_af:
                logger.info(f"[ISIS ROUTE POOLS] Skipping {device_name} ({address_family}) - no pools selected for this address family")
                continue

            if "route_pools" not in isis_config or not isinstance(isis_config["route_pools"], dict):
                existing_list = isis_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    isis_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [],
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    isis_config["route_pools"] = {"IPv4": [], "IPv6": []}

            isis_config["route_pools"][address_family] = pools_for_this_af
            device_data["device_info"]["isis_config"] = isis_config
            device_data["device_info"]["is_is_config"] = isis_config
            device_data["device_info"]["_needs_apply"] = True

            if device_name not in devices_by_name:
                devices_by_name[device_name] = True
                total_devices += 1

            if address_family == "IPv4":
                ipv4_count += 1
            else:
                ipv6_count += 1

            for pool_name in pools_for_this_af:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break

        self.parent.main_window.save_session()
        self.update_isis_table()

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

        logger.info(f"[ISIS ROUTE POOLS] Attached pools to {total_devices} IS-IS device(s): {summary_text}")
        QMessageBox.information(self.parent, "Route Pools Attached",
                              f"Successfully attached route pools to {total_devices} IS-IS configuration(s):\n\n"
                              f"{summary_text}\n\n"
                              f"Total routes to advertise: {total_routes}\n\n"
                              f"Click 'Apply IS-IS' to configure routes on server.")


    def prompt_detach_route_pools(self):
        """Detach route pools from selected IS-IS configurations.

        v0.5.213: ported from utils/devices_tab_ospf.py:prompt_detach_route_pools.
        ISIS column layout: Neighbor Type is column 2 (OSPF has it at
        column 3). Preserves the `is_is_config` legacy mirror.
        """
        selected_items = self.parent.isis_table.selectedItems()
        if not selected_items:
            total_rows = self.parent.isis_table.rowCount()
            if total_rows > 0:
                self.parent.isis_table.selectAll()
                logger.info(f"[ISIS TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self.parent, "No IS-IS Devices", "No IS-IS devices are configured.")
                return

        selected_devices = []
        processed_devices = set()

        for item in selected_items:
            row = item.row()
            device_name = self.parent.isis_table.item(row, 0).text()  # Device column
            # Column 2 for Neighbor Type in ISIS table (OSPF uses col 3).
            neighbor_type_item = self.parent.isis_table.item(row, 2)
            neighbor_type = neighbor_type_item.text() if neighbor_type_item else "IPv4"

            clean_device_name = device_name.split(" (")[0].strip()
            if clean_device_name != device_name:
                device_name = clean_device_name

            device_key = f"{device_name}:{neighbor_type}"
            if device_key in processed_devices:
                continue
            processed_devices.add(device_key)

            device_info = self.parent._find_device_by_name(device_name)
            if not device_info:
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue

            if not isinstance(device_info, dict):
                logger.warning(f"[ISIS ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                if isinstance(device_info, list) and len(device_info) > 0:
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        continue
                else:
                    continue

            if not isinstance(device_info, dict):
                continue

            protocols = device_info.get("protocols", [])
            if isinstance(protocols, str):
                try:
                    import json
                    protocols = json.loads(protocols)
                except Exception:
                    protocols = []

            has_isis = False
            if isinstance(protocols, list):
                has_isis = "IS-IS" in protocols or "ISIS" in protocols
            elif isinstance(protocols, dict):
                has_isis = "IS-IS" in protocols or "ISIS" in protocols
            if not has_isis:
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Device '{device_name}' does not have IS-IS configured")
                continue

            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            if not isis_config:
                logger.warning(f"[ISIS ROUTE POOLS] Warning: Device '{device_name}' does not have IS-IS configuration")
                continue

            route_pools_data = isis_config.get("route_pools", {})
            has_pools = False
            if isinstance(route_pools_data, dict):
                pools_for_family = route_pools_data.get(neighbor_type, [])
                has_pools = bool(pools_for_family and len(pools_for_family) > 0)
            elif isinstance(route_pools_data, list):
                has_pools = bool(neighbor_type == "IPv4" and route_pools_data and len(route_pools_data) > 0)

            if not has_pools:
                continue

            selected_devices.append({
                "device_name": device_name,
                "device_info": device_info,
                "isis_config": isis_config,
                "address_family": neighbor_type,
            })

        if not selected_devices:
            QMessageBox.information(self.parent, "No Route Pools",
                                  "No route pools are attached to the selected IS-IS configurations.")
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
                                        f"Detach all route pools from {len(selected_devices)} IS-IS configuration(s)?\n\n"
                                        f"Selected configurations:\n{summary_text}\n\n"
                                        f"This will remove all attached route pools for these configurations.",
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        total_detached = 0
        ipv4_count = 0
        ipv6_count = 0

        for device_data in selected_devices:
            device_name = device_data["device_name"]
            isis_config = device_data["isis_config"]
            address_family = device_data.get("address_family", "IPv4")

            if "route_pools" not in isis_config or not isinstance(isis_config["route_pools"], dict):
                existing_list = isis_config.get("route_pools", [])
                if isinstance(existing_list, list):
                    isis_config["route_pools"] = {"IPv4": existing_list if address_family == "IPv4" else [],
                                                   "IPv6": existing_list if address_family == "IPv6" else []}
                else:
                    isis_config["route_pools"] = {"IPv4": [], "IPv6": []}

            if address_family in isis_config["route_pools"]:
                pools_count = len(isis_config["route_pools"][address_family])
                isis_config["route_pools"][address_family] = []
                total_detached += 1

                if address_family == "IPv4":
                    ipv4_count += 1
                else:
                    ipv6_count += 1

                logger.info(f"[ISIS ROUTE POOLS] Detached {pools_count} pool(s) from {device_name} ({address_family})")

            device_data["device_info"]["isis_config"] = isis_config
            device_data["device_info"]["is_is_config"] = isis_config
            device_data["device_info"]["_needs_apply"] = True

        self.parent.main_window.save_session()
        self.update_isis_table()

        summary_parts = []
        if ipv4_count > 0:
            summary_parts.append(f"IPv4: {ipv4_count} configuration(s)")
        if ipv6_count > 0:
            summary_parts.append(f"IPv6: {ipv6_count} configuration(s)")

        summary_text = "\n".join(summary_parts) if summary_parts else "No configurations"

        logger.info(f"[ISIS ROUTE POOLS] Detached route pools from {total_detached} IS-IS configuration(s): {summary_text}")
        QMessageBox.information(self.parent, "Route Pools Detached",
                              f"Successfully detached route pools from {total_detached} IS-IS configuration(s):\n\n"
                              f"{summary_text}\n\n"
                              f"Click 'Apply IS-IS' to update configuration on server.")


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
    
