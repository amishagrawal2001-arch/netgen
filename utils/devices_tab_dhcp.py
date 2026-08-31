"""DHCP-related functionality for DevicesTab."""

import ipaddress
import json
import logging
from typing import List, Dict, Optional, Any

import requests
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class DHCPPoolDialog(QDialog):
    """Dialog to create or edit a DHCP pool definition."""

    def __init__(self, parent=None, defaults: Dict = None, is_edit: bool = False):
        super().__init__(parent)
        self.defaults = defaults or {}
        self.is_edit = is_edit
        self.setWindowTitle("Edit DHCP Pool" if is_edit else "Add DHCP Pool")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.pool_name_edit = QLineEdit(self.defaults.get("name", ""))
        self.pool_name_edit.setPlaceholderText("e.g. lab_pool_1")
        if self.is_edit:
            self.pool_name_edit.setReadOnly(True)
        form.addRow("Pool Name:", self.pool_name_edit)

        self.pool_start_edit = QLineEdit(self.defaults.get("pool_start", ""))
        self.pool_start_edit.setPlaceholderText("e.g. 192.168.50.10")
        form.addRow("Pool Start:", self.pool_start_edit)

        self.pool_end_edit = QLineEdit(self.defaults.get("pool_end", ""))
        self.pool_end_edit.setPlaceholderText("e.g. 192.168.50.200")
        form.addRow("Pool End:", self.pool_end_edit)

        self.gateway_edit = QLineEdit(self.defaults.get("gateway", ""))
        self.gateway_edit.setPlaceholderText("Router IP (optional)")
        form.addRow("Gateway:", self.gateway_edit)

        self.lease_time_spin = QSpinBox()
        self.lease_time_spin.setRange(0, 604800)
        self.lease_time_spin.setValue(int(self.defaults.get("lease_time", 0) or 0))
        self.lease_time_spin.setSpecialValueText("Default")
        self.lease_time_spin.setToolTip("Lease time in seconds (0 uses container default)")
        form.addRow("Lease Time (s):", self.lease_time_spin)

        self.gateway_route_edit = QLineEdit()
        existing_routes = self.defaults.get("gateway_routes") or self.defaults.get("gateway_route")
        if isinstance(existing_routes, (list, tuple)):
            self.gateway_route_edit.setText(", ".join(existing_routes))
        elif isinstance(existing_routes, str):
            self.gateway_route_edit.setText(existing_routes)
        self.gateway_route_edit.setPlaceholderText("Comma-separated CIDRs (optional)")
        form.addRow("Gateway Route(s):", self.gateway_route_edit)

        self.description_edit = QLineEdit(self.defaults.get("description", ""))
        self.description_edit.setPlaceholderText("Friendly description (optional)")
        form.addRow("Description:", self.description_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> str:
        name = self.pool_name_edit.text().strip()
        if not name:
            return "Pool name is required."

        pool_start = self.pool_start_edit.text().strip()
        pool_end = self.pool_end_edit.text().strip()
        if not pool_start or not pool_end:
            return "Pool start and end addresses are required."
        try:
            start_ip = ipaddress.IPv4Address(pool_start)
            end_ip = ipaddress.IPv4Address(pool_end)
            if int(start_ip) > int(end_ip):
                return "Pool start IP must be less than or equal to pool end IP."
        except ValueError as exc:
            return f"Invalid pool address: {exc}"

        # v0.5.230 (audit U client-8): validate the gateway address.
        # Pre-fix, arbitrary strings were accepted and saved to the
        # server → dnsmasq refused to launch later with an opaque
        # config error.
        gateway_val = self.gateway_edit.text().strip()
        if gateway_val:
            try:
                ipaddress.IPv4Address(gateway_val)
            except ValueError as exc:
                return f"Invalid gateway address '{gateway_val}': {exc}"

        routes_text = self.gateway_route_edit.text().strip()
        if routes_text:
            for token in routes_text.replace(";", ",").split(","):
                route_val = token.strip()
                if not route_val:
                    continue
                try:
                    ipaddress.ip_network(route_val, strict=False)
                except ValueError as exc:
                    return f"Invalid gateway route '{route_val}': {exc}"
        return ""

    def accept(self):
        error_msg = self._validate()
        if error_msg:
            QMessageBox.warning(self, "Invalid Input", error_msg)
            return
        super().accept()

    def get_payload(self) -> Dict:
        routes = []
        routes_text = self.gateway_route_edit.text().strip()
        if routes_text:
            for token in routes_text.replace(";", ",").split(","):
                value = token.strip()
                if value:
                    routes.append(value)
        payload = {
            "name": self.pool_name_edit.text().strip(),
            "pool_start": self.pool_start_edit.text().strip(),
            "pool_end": self.pool_end_edit.text().strip(),
            "gateway": self.gateway_edit.text().strip(),
            "gateway_routes": routes,
            "description": self.description_edit.text().strip(),
        }
        lease_time = int(self.lease_time_spin.value())
        if lease_time > 0:
            payload["lease_time"] = lease_time
        else:
            payload["lease_time"] = None
        return payload


class ManageDHCPPoolsDialog(QDialog):
    """Dialog to view, create, edit, and delete DHCP pool definitions."""

    def __init__(self, parent, server_url: str):
        super().__init__(parent)
        self.server_url = server_url
        self.pools: List[Dict] = []
        self.setWindowTitle("Manage DHCP Pools")
        self.resize(820, 520)
        self._build_ui()
        self.load_pools()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info_label = QLabel(
            "Define reusable DHCP pools that can be attached to devices.\n"
            "Each pool contains a start/end address range, optional gateway, and optional gateway routes."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #555; background: #f3f3f3; padding: 6px; border-radius: 3px;")
        layout.addWidget(info_label)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Name",
                "Pool Start",
                "Pool End",
                "Gateway",
                "Gateway Routes",
                "Lease Time (s)",
                "Description",
                "Created",
                "Updated",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for idx in range(1, 7):
            header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.edit_selected_pool)
        layout.addWidget(self.table)

        button_bar = QHBoxLayout()
        self.add_button = QPushButton("Add Pool")
        self.add_button.clicked.connect(self.add_pool)
        self.edit_button = QPushButton("Edit Pool")
        self.edit_button.clicked.connect(self.edit_selected_pool)
        self.delete_button = QPushButton("Delete Pool")
        self.delete_button.clicked.connect(self.delete_selected_pool)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.load_pools)
        button_bar.addWidget(self.add_button)
        button_bar.addWidget(self.edit_button)
        button_bar.addWidget(self.delete_button)
        button_bar.addStretch()
        button_bar.addWidget(self.refresh_button)
        layout.addLayout(button_bar)

        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_layout.addWidget(close_button)
        layout.addLayout(close_layout)

    def load_pools(self):
        """Load pools from the backend and populate the table."""
        try:
            response = requests.get(f"{self.server_url}/api/dhcp/pools", timeout=10)
            if response.status_code != 200:
                QMessageBox.warning(self, "Load Failed", response.text or "Unable to fetch DHCP pools.")
                return
            data = response.json()
            base_pools = data.get("pools", [])
            device_defaults = self._fetch_device_default_pools()
            self.pools = base_pools + device_defaults
            self.populate_table()
        except Exception as exc:
            logging.error("[DHCP UI] Failed to load DHCP pools: %s", exc)
            QMessageBox.warning(self, "Load Failed", str(exc))

    def _fetch_device_default_pools(self) -> List[Dict[str, Any]]:
        """Gather default DHCP pools derived from device configurations."""
        default_entries: List[Dict[str, Any]] = []
        try:
            resp = requests.get(f"{self.server_url}/api/device/database/devices", timeout=10)
            if resp.status_code != 200:
                return default_entries
            payload = resp.json()
            devices = payload.get("devices", [])
        except Exception as exc:
            logging.warning("[DHCP UI] Failed to fetch device defaults for pools: %s", exc)
            return default_entries

        for device in devices:
            dhcp_mode = (device.get("dhcp_mode") or "").lower()
            if dhcp_mode != "server":
                continue

            dhcp_config = device.get("dhcp_config") or {}
            if isinstance(dhcp_config, str):
                try:
                    dhcp_config = json.loads(dhcp_config) if dhcp_config else {}
                except Exception:
                    dhcp_config = {}
            if not isinstance(dhcp_config, dict):
                continue

            pool_start = dhcp_config.get("pool_start")
            pool_end = dhcp_config.get("pool_end")
            if not (pool_start and pool_end):
                continue

            device_name = device.get("device_name") or "Unnamed Device"
            pool_name = device_name
            gateway_value = dhcp_config.get("gateway") or device.get("ipv4_gateway") or ""
            gateway_routes = dhcp_config.get("gateway_route_normalized") or dhcp_config.get("gateway_route")
            if isinstance(gateway_routes, str):
                gateway_routes = [gateway_routes]
            elif not isinstance(gateway_routes, (list, tuple)):
                gateway_routes = []

            lease_time = dhcp_config.get("lease_time")
            if isinstance(lease_time, str) and lease_time.isdigit():
                lease_time_value = int(lease_time)
            else:
                lease_time_value = lease_time or ""

            default_entries.append(
                {
                    "name": pool_name,
                    "pool_start": pool_start,
                    "pool_end": pool_end,
                    "gateway": gateway_value,
                    "gateway_routes": gateway_routes,
                    "lease_time": lease_time_value,
                    "description": f"Default pool for device '{device_name}'",
                    "created_at": device.get("created_at") or "",
                    "updated_at": device.get("updated_at") or "",
                    "__source": "device-default",
                    "__device_id": device.get("device_id"),
                    "__device_name": device_name,
                }
            )
        return default_entries

    def populate_table(self):
        self.table.setRowCount(0)
        for pool in self.pools:
            row = self.table.rowCount()
            self.table.insertRow(row)
            display = [
                pool.get("name", ""),
                pool.get("pool_start", ""),
                pool.get("pool_end", ""),
                pool.get("gateway", "") or "",
                ", ".join(pool.get("gateway_routes") or []),
                str(pool.get("lease_time") or ""),
                pool.get("description", "") or "",
                pool.get("created_at", "") or "",
                pool.get("updated_at", "") or "",
            ]
            for col, value in enumerate(display):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, pool)
                if pool.get("__source") == "device-default":
                    tooltip = "Default DHCP pool derived from device configuration."
                    if pool.get("__device_name"):
                        tooltip += f" Device: {pool['__device_name']}"
                    item.setToolTip(tooltip)
                self.table.setItem(row, col, item)

    def _selected_pool(self) -> Optional[Dict]:
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected:
            return None
        row = selected[0].row()
        item = self.table.item(row, 2)
        if not item:
            return None
        return item.data(Qt.UserRole)

    def add_pool(self):
        dialog = DHCPPoolDialog(self, defaults={})
        if dialog.exec_() != QDialog.Accepted:
            return
        payload = dialog.get_payload()
        try:
            response = requests.post(f"{self.server_url}/api/dhcp/pools", json=payload, timeout=10)
        except Exception as exc:
            logging.error("[DHCP UI] Failed to create DHCP pool: %s", exc)
            QMessageBox.warning(self, "Create Failed", str(exc))
            return
        if response.status_code not in (200, 201):
            message = response.text
            try:
                message = response.json().get("error", message)
            except Exception:
                pass
            QMessageBox.warning(self, "Create Failed", message or "Failed to create DHCP pool.")
            return
        self.load_pools()

    def edit_selected_pool(self):
        pool = self._selected_pool()
        if not pool:
            QMessageBox.information(self, "Select Pool", "Select a DHCP pool to edit.")
            return
        if pool.get("__source") == "device-default":
            QMessageBox.information(
                self,
                "Read Only Pool",
                "Default pools come from device configurations.\n"
                "Edit the device's DHCP settings to change this range.",
            )
            return
        defaults = {
            "name": pool.get("name"),
            "pool_start": pool.get("pool_start"),
            "pool_end": pool.get("pool_end"),
            "gateway": pool.get("gateway") or "",
            "lease_time": pool.get("lease_time") or 0,
            "gateway_routes": pool.get("gateway_routes") or [],
            "description": pool.get("description") or "",
        }
        dialog = DHCPPoolDialog(self, defaults=defaults, is_edit=True)
        if dialog.exec_() != QDialog.Accepted:
            return
        payload = dialog.get_payload()
        # Remove immutable fields / convert lease_time None
        update_payload = {
            "pool_start": payload["pool_start"],
            "pool_end": payload["pool_end"],
            "gateway": payload["gateway"],
            "gateway_routes": payload["gateway_routes"],
            "description": payload["description"],
        }
        if payload.get("lease_time"):
            update_payload["lease_time"] = payload["lease_time"]
        else:
            update_payload["lease_time"] = None
        try:
            response = requests.put(
                f"{self.server_url}/api/dhcp/pools/{pool.get('name')}",
                json=update_payload,
                timeout=10,
            )
        except Exception as exc:
            logging.error("[DHCP UI] Failed to update DHCP pool '%s': %s", pool.get("name"), exc)
            QMessageBox.warning(self, "Update Failed", str(exc))
            return
        if response.status_code != 200:
            message = response.text
            try:
                message = response.json().get("error", message)
            except Exception:
                pass
        else:
            self.load_pools()
            return
        QMessageBox.warning(self, "Update Failed", message or "Failed to update DHCP pool.")

    def delete_selected_pool(self):
        pool = self._selected_pool()
        if not pool:
            QMessageBox.information(self, "Select Pool", "Select a DHCP pool to delete.")
            return
        if pool.get("__source") == "device-default":
            QMessageBox.information(
                self,
                "Read Only Pool",
                "Default pools attached to devices cannot be deleted here.\n"
                "Remove or edit the DHCP configuration from the device instead.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Delete DHCP Pool",
            f"Are you sure you want to delete DHCP pool '{pool.get('name')}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            response = requests.delete(
                f"{self.server_url}/api/dhcp/pools/{pool.get('name')}",
                timeout=10,
            )
        except Exception as exc:
            logging.error("[DHCP UI] Failed to delete DHCP pool '%s': %s", pool.get("name"), exc)
            QMessageBox.warning(self, "Delete Failed", str(exc))
            return
        if response.status_code != 200:
            message = response.text
            try:
                message = response.json().get("error", message)
            except Exception:
                pass
            QMessageBox.warning(self, "Delete Failed", message or "Failed to delete DHCP pool.")
            return
        self.load_pools()


class AttachDHCPPoolsDialog(QDialog):
    """Dialog to attach one or more DHCP pools to a device."""

    def __init__(self, parent, server_url: str, device_name: str, existing_selection: Optional[Dict] = None, device: Optional[Dict] = None):
        super().__init__(parent)
        self.server_url = server_url
        self.device_name = device_name
        # v0.5.230 (audit U client-10): the parent now passes the
        # full device dict so we can pre-populate the gateway
        # override field with the current stored value.
        self.device = device or {}
        self.existing_selection = existing_selection or {"primary": None, "additional": []}
        self.pools: List[Dict] = []
        self.selection: Optional[Dict] = None
        self.primary_group = QButtonGroup(self)
        self.primary_group.setExclusive(True)
        self.setWindowTitle(f"Attach DHCP Pools - {device_name}")
        self.resize(900, 560)
        self._build_ui()
        self.load_pools()
        if not self.pools:
            QMessageBox.information(
                self,
                "No DHCP Pools",
                "No DHCP pools found.\n\nClick the 'Manage Pools' button "
                "in the DHCP subtab toolbar to create a named pool first, "
                "then come back to 'Attach Pool' to hang it on this device.",
            )
            self.reject()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info_label = QLabel(
            "Select one or more DHCP pools to attach to this device. "
            "Mark one pool as the primary range; additional pools are added as supplemental ranges."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #555; background: #f7f7f7; padding: 6px; border-radius: 3px;")
        layout.addWidget(info_label)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Primary",
                "Attach",
                "Name",
                "Pool Start",
                "Pool End",
                "Gateway",
                "Gateway Routes",
                "Lease",
                "Description",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for idx in range(2, 9):
            header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        options_layout = QHBoxLayout()
        self.replace_checkbox = QCheckBox("Replace existing pools")
        self.replace_checkbox.setChecked(True)
        options_layout.addWidget(self.replace_checkbox)
        options_layout.addSpacing(20)
        options_layout.addWidget(QLabel("Gateway Override:"))
        self.gateway_override_edit = QLineEdit()
        # v0.5.230 (audit U client-10): pre-populate with the current
        # stored gateway (from dhcp_config.gateway) so the operator can
        # see + edit + BLANK it. Pre-fix, the field was always empty
        # and the get_payload path only sent the value when truthy —
        # meaning the operator could never CLEAR a previously-set
        # override. Now empty = explicit clear (see get_payload edit
        # below for the paired sender change).
        _current_gw = ""
        try:
            _current_gw = str(
                (self.device or {}).get("dhcp_config", {}).get("gateway") or ""
            )
        except Exception:
            _current_gw = ""
        self.gateway_override_edit.setText(_current_gw)
        self.gateway_override_edit.setPlaceholderText(
            "Blank = clear override; enter an IP to override the pool-defined gateway"
        )
        self.gateway_override_edit.setFixedWidth(240)
        options_layout.addWidget(self.gateway_override_edit)
        _clear_btn = QPushButton("Clear")
        _clear_btn.setToolTip("Clear the gateway override — device will use each pool's own gateway.")
        _clear_btn.clicked.connect(lambda: self.gateway_override_edit.setText(""))
        options_layout.addWidget(_clear_btn)
        options_layout.addStretch()
        layout.addLayout(options_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def load_pools(self):
        try:
            response = requests.get(f"{self.server_url}/api/dhcp/pools", timeout=10)
            if response.status_code != 200:
                QMessageBox.warning(self, "Load Failed", response.text or "Unable to fetch DHCP pools.")
                return
            data = response.json()
            self.pools = data.get("pools", [])
            self.populate_table()
        except Exception as exc:
            logging.error("[DHCP UI] Failed to load DHCP pools: %s", exc)
            QMessageBox.warning(self, "Load Failed", str(exc))

    def populate_table(self):
        self.table.setRowCount(0)
        existing_primary = self.existing_selection.get("primary")
        existing_additional = set(self.existing_selection.get("additional") or [])
        for row, pool in enumerate(self.pools):
            self.table.insertRow(row)

            radio = QRadioButton()
            self.primary_group.addButton(radio, row)
            radio.toggled.connect(lambda checked, r=row: self._on_primary_toggled(r, checked))
            self.table.setCellWidget(row, 0, radio)

            checkbox = QCheckBox()
            checkbox.stateChanged.connect(lambda state, r=row: self._on_attach_toggled(r, state))
            self.table.setCellWidget(row, 1, checkbox)

            display = [
                pool.get("name", ""),
                pool.get("pool_start", ""),
                pool.get("pool_end", ""),
                pool.get("gateway", "") or "",
                ", ".join(pool.get("gateway_routes") or []),
                str(pool.get("lease_time") or ""),
                pool.get("description", "") or "",
            ]
            for col, value in enumerate(display, start=2):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, pool)
                self.table.setItem(row, col, item)

            # Preselect existing assignments
            pool_name = pool.get("name")
            if pool_name == existing_primary:
                radio.setChecked(True)
                checkbox.setChecked(True)
            elif pool_name in existing_additional:
                checkbox.setChecked(True)
        # Ensure at least one primary is selected if possible
        if self.primary_group.checkedId() == -1 and self.table.rowCount() > 0:
            first_button = self.primary_group.button(0)
            if first_button:
                first_button.setChecked(True)
                checkbox = self.table.cellWidget(0, 1)
                if checkbox and not checkbox.isChecked():
                    checkbox.setChecked(True)

    def _on_primary_toggled(self, row: int, checked: bool):
        if checked:
            checkbox = self.table.cellWidget(row, 1)
            if checkbox and not checkbox.isChecked():
                checkbox.setChecked(True)

    def _on_attach_toggled(self, row: int, state: int):
        if state != Qt.Checked:
            button = self.primary_group.button(row)
            if button and button.isChecked():
                button.setChecked(False)

    def get_selection(self) -> Optional[Dict]:
        return self.selection

    def accept(self):
        selected_rows = []
        for row in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(row, 1)
            if checkbox and checkbox.isChecked():
                selected_rows.append(row)

        # Allow detaching all pools if none are selected
        if not selected_rows:
            reply = QMessageBox.question(
                self,
                "Detach All Pools",
                "No pools are selected. This will detach all DHCP pools from this device.\n\nDo you want to continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            
            # Set selection to indicate detach all
            self.selection = {
                "primary_pool": None,
                "additional_pools": [],
                "replace_existing": True,
                "gateway_override": self.gateway_override_edit.text().strip(),
                "detach_all": True,
            }
            super().accept()
            return

        primary_row = self.primary_group.checkedId()
        if primary_row == -1 or primary_row not in selected_rows:
            primary_row = selected_rows[0]
            button = self.primary_group.button(primary_row)
            if button:
                button.setChecked(True)

        primary_pool = self.table.item(primary_row, 2).data(Qt.UserRole)
        additional = []
        for row in selected_rows:
            if row == primary_row:
                continue
            pool = self.table.item(row, 2).data(Qt.UserRole)
            additional.append(pool.get("name"))

        self.selection = {
            "primary_pool": primary_pool.get("name"),
            "additional_pools": [name for name in additional if name],
            "replace_existing": self.replace_checkbox.isChecked(),
            "gateway_override": self.gateway_override_edit.text().strip(),
            "detach_all": False,
        }
        super().accept()


class DHCPHandler:
    """Handler class for DHCP-focused UI interactions."""

    def __init__(self, parent_tab):
        self.parent = parent_tab

    def setup_dhcp_subtab(self):
        """Initialise the DHCP subtabs with table and controls."""
        layout = QVBoxLayout(self.parent.dhcp_subtab)
        # Tight chrome — see BGP subtab for rationale.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        headers = [
            "Device",
            "Interface",
            "VLAN",
            "Mode",
            "Pools",
            "State",
            "Lease IP",
            "Gateway",
            "Last Check",
        ]

        self.parent.dhcp_table = QTableWidget(0, len(headers))
        self.parent.dhcp_table.setHorizontalHeaderLabels(headers)
        self.parent.DHCP_COL = {h: i for i, h in enumerate(headers)}
        self.parent.dhcp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.parent.dhcp_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.parent.dhcp_table.setSelectionMode(QTableWidget.SingleSelection)
        # v0.3.11: filter input ABOVE the table — parity with the
        # other sub-tabs.
        try:
            from utils.table_filter_bar import make_table_filter_row
            _dhcp_filter_row, self.parent._dhcp_filter_input = (
                make_table_filter_row(
                    table=self.parent.dhcp_table,
                    columns=(
                        "Device", "Interface", "VLAN", "Mode",
                        "Pools", "State", "Lease IP", "Gateway",
                    ),
                    placeholder=(
                        "Device / Interface / Pool / State / IP …"
                    ),
                    tooltip=(
                        "Substring filter — Device / Interface / VLAN / "
                        "Mode / Pool / State / IP. Case-insensitive."
                    ),
                )
            )
            layout.addLayout(_dhcp_filter_row)
        except Exception as _e:
            import logging as _lg
            _lg.warning(f"[DHCP TAB] filter row unavailable: {_e}")

        # Section header removed — tab name + table columns are enough.
        layout.addWidget(self.parent.dhcp_table)

        # v0.2.88: empty-state placeholder.
        try:
            from widgets.empty_state_overlay import EmptyStateOverlay
            self.parent.dhcp_empty_state = EmptyStateOverlay(
                self.parent.dhcp_table,
                "No DHCP servers or clients configured.\n\n"
                "Configure DHCP on a device via the main Devices "
                "table (right-click → Edit → enable DHCP). DHCP "
                "pools attached to running servers will appear "
                "here once Apply succeeds."
            )
        except Exception:
            pass  # overlay is advisory; never block sub-tab render

        # v0.2.95: Delete-key shortcut + right-click context menu.
        # Closes the cross-tab keyboard/mouse-RMB consistency sweep.
        # v0.5.218 (audit fix I): pre-fix the Delete-key shortcut
        # was `.connect(self.delete_selected_pool)` — but that
        # method only exists on ManageDHCPPoolsDialog, not on
        # DHCPHandler. The connect raised AttributeError at wiring
        # time, the outer bare `except Exception: pass` swallowed
        # it, and everything below (setContextMenuPolicy + the
        # customContextMenuRequested wire-up) never ran. Symptom:
        # Delete key did nothing on the DHCP subtab, right-click
        # showed no context menu. Fix: wire to a real handler on
        # DHCPHandler (`delete_selected_dhcp_row`, which detaches
        # all pools from the selected device — the sensible per-
        # row "delete" for a DHCP status table), point the menu
        # entry at the same method, and upgrade the outer except
        # to log at ERROR so a future missing-symbol regression
        # surfaces in the logs instead of silently disabling the
        # whole shortcut+menu block.
        try:
            from PyQt5.QtWidgets import QShortcut, QMenu
            from PyQt5.QtGui import QKeySequence
            from PyQt5.QtCore import Qt as _Qt
            _dhcp_del = QShortcut(
                QKeySequence(_Qt.Key_Delete), self.parent.dhcp_table,
            )
            _dhcp_del.setContext(_Qt.WidgetShortcut)
            _dhcp_del.activated.connect(self.delete_selected_dhcp_row)

            self.parent.dhcp_table.setContextMenuPolicy(_Qt.CustomContextMenu)
            def _on_dhcp_ctx(pos):
                menu = QMenu(self.parent.dhcp_table)
                act_refresh = menu.addAction("Refresh DHCP status")
                act_apply   = menu.addAction("Apply DHCP pools")
                menu.addSeparator()
                act_delete  = menu.addAction("Detach pools from selected device")
                act = menu.exec_(self.parent.dhcp_table.viewport().mapToGlobal(pos))
                if act is act_refresh:
                    try: self.refresh_dhcp_status()
                    except Exception as _rc_exc:
                        logging.error(f"[DHCP UI] context-menu Refresh failed: {_rc_exc}")
                elif act is act_apply:
                    try: self.apply_dhcp_pools()
                    except Exception as _ap_exc:
                        logging.error(f"[DHCP UI] context-menu Apply failed: {_ap_exc}")
                elif act is act_delete:
                    try: self.delete_selected_dhcp_row()
                    except Exception as _dl_exc:
                        logging.error(f"[DHCP UI] context-menu Detach failed: {_dl_exc}")
            self.parent.dhcp_table.customContextMenuRequested.connect(_on_dhcp_ctx)
        except Exception as _wire_exc:
            # v0.5.218: log at ERROR so a future missing-symbol wiring
            # regression is visible in logs (the whole shortcut+menu
            # block used to silently disappear). We still `pass`
            # afterwards to keep the app usable — losing keyboard
            # shortcuts on one subtab is not worth crashing over.
            logging.error(
                f"[DHCP UI] failed to wire Delete-key shortcut + context menu: "
                f"{_wire_exc}"
            )

        # DHCP action bar — unified chrome with Devices + BGP + OSPF
        # + ISIS + VXLAN.
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
        # v0.5.228: icon-ONLY buttons here were undiscoverable — operators
        # had no way to see what each 28x24 icon did without hovering, and
        # in practice they missed "Attach DHCP Pools" entirely. Now
        # icon + label, sized to fit the text.
        BTN_H, ICON_PX = 26, 14

        def load_icon(filename: str):
            from utils.qicon_loader import qicon
            return qicon("resources", f"icons/{filename}")

        def _dhcp_btn(icon_name, label, tooltip, style=BTN_BASE):
            b = QPushButton(label)
            b.setIcon(load_icon(icon_name))
            b.setIconSize(QSize(ICON_PX, ICON_PX))
            b.setMinimumHeight(BTN_H)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            # Small horizontal padding so the icon doesn't kiss the text.
            padded = style.replace(
                "  padding: 0px;",
                "  padding: 2px 10px;",
            )
            b.setStyleSheet(padded)
            return b

        # Config group (left) — these are what turns "State: No Pool"
        # into a serving DHCP box. "Manage" defines named pools;
        # "Attach" hangs them off the selected server device.
        self.parent.dhcp_manage_button  = _dhcp_btn(
            "edit.png",  "Manage Pools",
            "Create, edit, or delete named DHCP pools (reusable across devices).",
        )
        self.parent.dhcp_attach_button  = _dhcp_btn(
            "readd.png", "Attach Pool",
            "Attach one or more named DHCP pools to the selected server device. "
            "Use this when the device shows State='No Pool'.",
        )

        # Runtime group (right)
        self.parent.dhcp_apply_button   = _dhcp_btn(
            "apply.png",   "Apply",
            "Apply attached DHCP pools on the selected server (writes dnsmasq config and restarts).",
            style=BTN_APPLY,
        )
        self.parent.dhcp_refresh_button = _dhcp_btn(
            "refresh.png", "Refresh",
            "Refresh DHCP status for all rows.",
        )

        self.parent.dhcp_manage_button.clicked.connect(self.manage_dhcp_pools)
        self.parent.dhcp_attach_button.clicked.connect(self.attach_dhcp_pools)
        self.parent.dhcp_apply_button.clicked.connect(self.apply_dhcp_pools)
        self.parent.dhcp_refresh_button.clicked.connect(self.refresh_dhcp_status)

        for b in (self.parent.dhcp_manage_button, self.parent.dhcp_attach_button):
            controls.addWidget(b)

        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        controls.addSpacing(4)
        controls.addWidget(sep)
        controls.addSpacing(4)

        for b in (self.parent.dhcp_apply_button, self.parent.dhcp_refresh_button):
            controls.addWidget(b)

        controls.addStretch(1)
        layout.addWidget(action_bar)

        # Kick off an initial status refresh once the UI finishes rendering
        QTimer.singleShot(200, self.refresh_dhcp_status)

    def refresh_dhcp_status(self):
        """Fetch DHCP status from server and update table.

        Kicks a server-side force-check first so the DB picks up live
        DHCP state (lease IP, mode, expiry) before the UI re-reads.
        Same pattern as the ARP / BGP / OSPF / ISIS refresh buttons.
        Failure of the force-check is non-fatal — we still render
        whatever is in the DB.

        v0.5.218 (audit fix J): the force-check + status fetch each
        do `requests` with 5-15s timeouts and were being called
        synchronously on the UI thread. On a slow / offline server
        every Refresh click froze the client for up to 20s.
        Wrapped in a QThread + indeterminate QProgressDialog.
        Refresh has no server-side side-effects → Cancel is
        enabled and safe (worker checks _should_stop between the
        force-check and the status GET). Worker keepalive on
        `self._dhcp_workers` mirrors the OSPF/BGP/ISIS SIGABRT
        guard against "QThread: Destroyed while thread is still
        running" on PyQt5 5.15.11 + Python 3.14.
        """
        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            logging.debug("[DHCP UI] No server URL configured")
            return

        from PyQt5.QtCore import QThread, pyqtSignal
        from PyQt5.QtWidgets import QProgressDialog

        class RefreshDHCPWorker(QThread):
            # emits: (devices_list, error_string) — error empty on OK.
            finished = pyqtSignal(list, str)

            def __init__(self, url):
                super().__init__()
                self.url = url
                self._should_stop = False

            def stop(self):
                self._should_stop = True

            def run(self):
                # Step 1 — server-side force-check (non-fatal).
                try:
                    fc = requests.post(
                        f"{self.url}/api/dhcp/monitor/force-check",
                        timeout=15,
                    )
                    if fc.status_code == 200:
                        logging.info("[DHCP UI] force-check OK")
                    else:
                        logging.warning(
                            f"[DHCP UI] force-check returned HTTP {fc.status_code}"
                        )
                except Exception as fc_exc:
                    logging.warning(
                        f"[DHCP UI] force-check failed "
                        f"(will show cached state): {fc_exc}"
                    )

                if self._should_stop:
                    self.finished.emit([], "cancelled")
                    return

                # Step 2 — read (now-fresh) status from server.
                try:
                    response = requests.get(
                        f"{self.url}/api/device/dhcp/status", timeout=5,
                    )
                    if response.status_code != 200:
                        err = (
                            f"HTTP {response.status_code}: "
                            f"{response.text}"
                        )
                        logging.warning(
                            f"[DHCP UI] Failed to fetch status: {err}"
                        )
                        self.finished.emit([], err)
                        return
                    payload = response.json()
                    devices = payload.get("devices", []) or []
                    self.finished.emit(devices, "")
                except Exception as exc:
                    logging.error(
                        f"[DHCP UI] Exception refreshing DHCP status: {exc}"
                    )
                    self.finished.emit([], str(exc))

        progress = QProgressDialog(
            "Refreshing DHCP status...", "Cancel", 0, 0, self.parent,
        )
        progress.setWindowModality(2)  # Qt.WindowModal
        progress.setMinimumDuration(0)
        progress.show()

        worker = RefreshDHCPWorker(server_url)
        worker.setParent(self.parent)

        def _on_cancel():
            worker.stop()
            progress.setLabelText(
                "Cancelling — waiting for current request to finish..."
            )
        progress.canceled.connect(_on_cancel)

        def _on_finished(devices, err):
            try:
                progress.close()
            except Exception:
                pass
            if err and err != "cancelled":
                # v0.5.230 (audit P client-12): pre-fix, refresh errors
                # were silently logged and the table stayed with stale
                # data — the operator had no way to know why the row
                # counts didn't update. Now surface a status-bar
                # message (non-modal, doesn't block workflow) so the
                # user sees the failure.
                try:
                    _msg = f"DHCP status refresh failed: {err}"
                    _sb = getattr(self.parent, "statusBar", None)
                    if callable(_sb):
                        _bar = _sb()
                        if _bar is not None:
                            _bar.showMessage(_msg, 5000)
                    elif hasattr(self.parent, "main_window"):
                        _mw = self.parent.main_window
                        if _mw is not None and hasattr(_mw, "statusBar"):
                            _bar = _mw.statusBar()
                            if _bar is not None:
                                _bar.showMessage(_msg, 5000)
                except Exception:
                    pass
                return
            if err == "cancelled":
                return

            # v0.5.219 (audit fix C5): defer the table populate to
            # the next event-loop tick so it runs AFTER the QThread's
            # finished signal has fully unwound. Matches the deferred-
            # exec pattern applied to _run_dhcp_pool_post; keeps the
            # PyQt5 5.15.11 + Python 3.14 SIGABRT surface consistent
            # across all three v0.5.218-touched finish handlers
            # (Refresh, Attach, Apply).
            def _do_populate():
                try:
                    self._populate_dhcp_table(devices)
                except Exception as exc:
                    logging.error(
                        f"[DHCP UI] Failed to populate DHCP table: {exc}"
                    )
            QTimer.singleShot(0, _do_populate)

        worker.finished.connect(_on_finished)
        worker.start()

        # SIGABRT guard — same pattern OSPF/BGP/ISIS/Ping use.
        if not hasattr(self, "_dhcp_workers"):
            self._dhcp_workers = []
        self._dhcp_workers.append(worker)

        def _still_running(w):
            try:
                return w.isRunning()
            except RuntimeError:
                return False
        self._dhcp_workers = [
            w for w in self._dhcp_workers if _still_running(w)
        ]

    def _populate_dhcp_table(self, rows: List[Dict]):
        """Populate the DHCP table with rows."""
        table = self.parent.dhcp_table
        table.setRowCount(0)

        for entry in rows:
            row = table.rowCount()
            table.insertRow(row)
            self._set_item(row, "Device", entry.get("device_name", ""))
            self._set_item(row, "Interface", entry.get("interface") or entry.get("server_interface") or "")
            vlan_display = entry.get("vlan")
            if vlan_display is None:
                vlan_display = ""
            else:
                vlan_display = str(vlan_display)
            self._set_item(row, "VLAN", vlan_display)
            self._set_item(row, "Mode", (entry.get("mode") or "").title())
            self._set_item(row, "Pools", self._format_pool_names(entry))
            self._set_item(row, "State", entry.get("state", "Unknown"))
            # v0.5.222: when state is Failed, attach the server-side
            # error message as the State cell's tooltip so operators
            # see the actual dnsmasq stderr / config error on hover
            # instead of having to grep netgen-server logs.
            _last_err = (entry.get("last_error") or "").strip()
            if _last_err:
                try:
                    state_item = self.parent.dhcp_table.item(
                        row, self.parent.DHCP_COL["State"])
                    if state_item is not None:
                        state_item.setToolTip(_last_err)
                except Exception:
                    pass
            # v0.5.229 (audit U client-13): the Lease IP + Gateway
            # columns previously ALWAYS read dhcp_lease_* fields —
            # correct for client-mode rows (dhclient wrote them),
            # meaningless for server-mode rows (the server device
            # HAS no lease from anyone else; dhcp_lease_* stays blank
            # or holds stale client-mode data from before the mode
            # flip). For server-mode rows, render the SERVED gateway
            # and its own interface IP instead. Then the operator can
            # read the columns at a glance: client rows show what the
            # dhclient obtained, server rows show what the device is
            # advertising to others.
            _mode = (entry.get("mode") or "").lower()
            if _mode == "server":
                # For server rows, "Lease IP" is meaningless; show
                # the server's own interface IPv4 (the address dnsmasq
                # is bound to). "Gateway" shows what the server hands
                # out to clients.
                _dhcp_cfg = entry.get("dhcp_config") or {}
                if isinstance(_dhcp_cfg, str):
                    try:
                        import json as _json
                        _dhcp_cfg = _json.loads(_dhcp_cfg) if _dhcp_cfg else {}
                    except Exception:
                        _dhcp_cfg = {}
                _server_ip = (
                    entry.get("server_interface_ip")
                    or _dhcp_cfg.get("server_ip")
                    or ""
                )
                _served_gw = _dhcp_cfg.get("gateway") or ""
                self._set_item(row, "Lease IP", _server_ip)
                self._set_item(row, "Gateway", _served_gw)
            else:
                self._set_item(row, "Lease IP", entry.get("lease_ip", ""))
                self._set_item(row, "Gateway", entry.get("lease_gateway", ""))
            self._set_item(row, "Last Check", str(entry.get("last_check") or ""))

            metadata = {
                "device_id": entry.get("device_id"),
                "mode": (entry.get("mode") or "").lower(),
                "entry": entry,
            }
            for column_index in range(table.columnCount()):
                item = table.item(row, column_index)
                if item is not None:
                    item.setData(Qt.UserRole, metadata)

        # v0.3.11: reapply substring filter so it survives this rebuild.
        try:
            from utils.table_filter_bar import reapply_filter
            reapply_filter(getattr(self.parent, "_dhcp_filter_input", None))
        except Exception:
            pass

    def _format_pool_names(self, entry: Dict) -> str:
        """Human readable string for attached pool names or default pool."""
        pool_info = entry.get("pool_names") or {}
        if isinstance(pool_info, str):
            try:
                pool_info = json.loads(pool_info)
            except Exception:
                pool_info = {}
        if not isinstance(pool_info, dict):
            pool_info = {}

        primary = pool_info.get("primary")
        additional = pool_info.get("additional") or []
        display_parts: List[str] = []

        # Show named pools if available
        if primary:
            display_parts.append(f"{primary} (primary)")

        if isinstance(additional, (list, tuple, set)):
            for name in additional:
                if not name:
                    continue
                name_str = str(name)
                if name_str and name_str != primary:
                    display_parts.append(name_str)

        # If no named pools, show default pool (from Add Device dialog)
        if not display_parts:
            default_pool = entry.get("default_pool")
            if default_pool and isinstance(default_pool, dict):
                pool_range = default_pool.get("pool_range") or (
                    f"{default_pool.get('pool_start', '')}-{default_pool.get('pool_end', '')}"
                    if default_pool.get("pool_start") and default_pool.get("pool_end")
                    else ""
                )
                if pool_range:
                    display_parts.append(f"{pool_range} (default)")
                # v0.5.230 (audit U client-9): also render IPv6 pool.
                # Pre-fix, an IPv6-only server row's Pools column was
                # blank because only the IPv4 default_pool was checked.
                pool6_range = default_pool.get("pool6_range") or (
                    f"{default_pool.get('pool6_start', '')}-{default_pool.get('pool6_end', '')}"
                    if default_pool.get("pool6_start") and default_pool.get("pool6_end")
                    else ""
                )
                if pool6_range:
                    display_parts.append(f"{pool6_range} (v6 default)")

        return ", ".join(display_parts) if display_parts else ""

    def _set_item(self, row: int, column_name: str, value: str):
        """Set a table widget item ensuring alignment and tooltips."""
        col_index = self.parent.DHCP_COL[column_name]
        item = QTableWidgetItem(value if value is not None else "")
        item.setToolTip(value if value else "")
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if column_name in {"State", "Mode", "VLAN"}:
            item.setTextAlignment(Qt.AlignCenter)
        self.parent.dhcp_table.setItem(row, col_index, item)

    def _get_selected_metadata(self):
        selection_model = self.parent.dhcp_table.selectionModel()
        if not selection_model:
            return None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return None
        row_index = selected_rows[0].row()
        device_col = self.parent.DHCP_COL.get("Device")
        if device_col is None:
            return None
        item = self.parent.dhcp_table.item(row_index, device_col)
        if not item:
            return None
        metadata = item.data(Qt.UserRole)
        if not metadata:
            return None
        metadata = dict(metadata)
        metadata["row"] = row_index
        return metadata

    def delete_selected_dhcp_row(self):
        """Detach all DHCP pools from the device on the currently
        selected row.

        v0.5.218 (audit fix I): the Delete-key shortcut + context
        menu now route to this method. Pre-fix they pointed at
        `self.delete_selected_pool`, which only exists on the
        modal ManageDHCPPoolsDialog — the wire-up raised
        AttributeError and the whole shortcut+menu block silently
        disabled itself. For a device-oriented status table
        "Delete" naturally means "detach all pools from this
        device"; this is the same effect as opening Attach and
        confirming the detach-all prompt with no selection.
        """
        metadata = self._get_selected_metadata()
        if not metadata:
            QMessageBox.information(
                self.parent, "Select Device",
                "Select a DHCP row first.",
            )
            return

        mode = (metadata.get("mode") or "").lower()
        if mode != "server":
            QMessageBox.warning(
                self.parent, "Invalid Selection",
                "Only DHCP server rows have pools to detach.\n"
                "Client-mode devices manage their own leases.",
            )
            return

        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            QMessageBox.warning(
                self.parent, "Server Unavailable",
                "No server is currently configured.",
            )
            return

        device_id = metadata.get("device_id")
        if not device_id:
            QMessageBox.warning(
                self.parent, "Error",
                "Unable to determine the selected device ID.",
            )
            return

        entry = metadata.get("entry", {}) or {}
        device_name = entry.get("device_name") or device_id
        reply = QMessageBox.question(
            self.parent, "Detach DHCP Pools",
            f"Detach all DHCP pools from '{device_name}'?\n\n"
            f"This will stop the DHCP server on that device.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        payload = {"device_id": device_id, "detach_all": True}
        try:
            response = requests.post(
                f"{server_url}/api/device/dhcp/server/attach_pools",
                json=payload, timeout=20,
            )
        except requests.RequestException as exc:
            logging.error(
                "[DHCP UI] Detach-all failed for %s: %s",
                device_id, exc,
            )
            QMessageBox.warning(self.parent, "Request Failed", str(exc))
            return

        if response.status_code != 200:
            error_message = response.text
            try:
                error_message = response.json().get("error", error_message)
            except ValueError:
                pass
            QMessageBox.warning(
                self.parent, "DHCP Detach Failed",
                error_message or "Failed to detach DHCP pools.",
            )
            return

        QMessageBox.information(
            self.parent, "DHCP Pools Detached",
            f"All DHCP pools detached from '{device_name}'.",
        )
        self.refresh_dhcp_status()

    def manage_dhcp_pools(self):
        """Open the DHCP pool management dialog."""
        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            QMessageBox.warning(
                self.parent,
                "Server Not Configured",
                "Configure a server connection before managing DHCP pools.",
            )
            return

        dialog = ManageDHCPPoolsDialog(self.parent, server_url)
        dialog.exec_()

    def attach_dhcp_pools(self):
        """Attach DHCP pools from the shared catalog to the selected server."""
        metadata = self._get_selected_metadata()
        if not metadata:
            QMessageBox.information(self.parent, "Select Device", "Select a DHCP server row first.")
            return

        if (metadata.get("mode") or "") != "server":
            QMessageBox.warning(
                self.parent,
                "Invalid Selection",
                "Please select a DHCP server entry before attaching a pool.",
            )
            return

        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            QMessageBox.warning(
                self.parent,
                "Server Unavailable",
                "No server is currently configured. Connect to a server before attaching DHCP pools.",
            )
            return

        device_id = metadata.get("device_id")
        if not device_id:
            QMessageBox.warning(self.parent, "Error", "Unable to determine the selected device ID.")
            return

        try:
            device_resp = requests.get(
                f"{server_url}/api/device/database/devices/{device_id}",
                timeout=5,
            )
        except requests.RequestException as exc:
            logging.error("[DHCP UI] Failed to fetch device %s: %s", device_id, exc)
            QMessageBox.warning(self.parent, "Request Failed", str(exc))
            return

        if device_resp.status_code != 200:
            error_text = device_resp.text
            try:
                error_json = device_resp.json()
                error_text = error_json.get("error", error_text)
            except ValueError:
                pass
            QMessageBox.warning(
                self.parent,
                "Device Lookup Failed",
                f"Unable to fetch device details: {error_text}",
            )
            return

        try:
            device_data = device_resp.json()
        except ValueError:
            QMessageBox.warning(self.parent, "Error", "Invalid response received from server.")
            return

        dhcp_config = device_data.get("dhcp_config") or {}
        if isinstance(dhcp_config, str):
            try:
                dhcp_config = json.loads(dhcp_config) if dhcp_config else {}
            except Exception:
                dhcp_config = {}
        if not isinstance(dhcp_config, dict):
            dhcp_config = {}

        existing_selection = {"primary": None, "additional": []}
        existing_pool_names = dhcp_config.get("pool_names")
        if isinstance(existing_pool_names, dict):
            existing_selection["primary"] = existing_pool_names.get("primary")
            additional = existing_pool_names.get("additional") or []
            if isinstance(additional, (list, tuple)):
                existing_selection["additional"] = [str(name) for name in additional if name]
        else:
            if dhcp_config.get("pool_name"):
                existing_selection["primary"] = dhcp_config.get("pool_name")
            additional = dhcp_config.get("additional_pools") or []
            if isinstance(additional, list):
                existing_selection["additional"] = [
                    pool.get("pool_name")
                    for pool in additional
                    if isinstance(pool, dict) and pool.get("pool_name")
                ]

        attach_dialog = AttachDHCPPoolsDialog(
            self.parent,
            server_url,
            metadata.get("entry", {}).get("device_name") or device_data.get("device_name") or "Selected Device",
            existing_selection=existing_selection,
            # v0.5.230 (audit U client-10): pass the full device entry
            # so the Gateway Override field can pre-populate with the
            # currently-stored override value.
            device=metadata.get("entry") or device_data,
        )
        if attach_dialog.exec_() != QDialog.Accepted:
            return

        selection = attach_dialog.get_selection()
        if not selection:
            return

        # Handle detach all pools case
        if selection.get("detach_all"):
            payload = {
                "device_id": device_id,
                "detach_all": True,
            }
        else:
            payload = {
                "device_id": device_id,
                "primary_pool": selection["primary_pool"],
                "additional_pools": selection["additional_pools"],
                "replace_existing": selection["replace_existing"],
            }
            if selection.get("gateway_override"):
                payload["gateway"] = selection["gateway_override"]

        # v0.5.218 (audit fix J): wrap the attach POST (up to 20s
        # per click) in a QThread + indeterminate QProgressDialog.
        # Attach mutates server-side dnsmasq state, so Cancel is
        # DISABLED — matches OSPF/BGP/ISIS Apply policy against
        # interrupting partial applies. Results surface through
        # MultiDeviceResultsDialog on completion.
        self._run_dhcp_pool_post(
            server_url=server_url,
            device_id=device_id,
            payload=payload,
            operation="attach",
            detach_all=bool(selection.get("detach_all")),
        )

    def _run_dhcp_pool_post(self, server_url, device_id, payload,
                            operation, detach_all=False):
        """Common worker-driven POST for attach_dhcp_pools and
        apply_dhcp_pools.

        v0.5.218 (audit fix J): the pre-fix code did
        `requests.post(..., timeout=20)` on the UI thread. On a
        slow / partially-hung server the whole client froze for
        up to 20s per click. Cancel is disabled (matches OSPF/
        BGP/ISIS Apply); a keepalive on `self._dhcp_workers`
        guards against PyQt5 5.15.11 + Python 3.14 SIGABRT on
        premature QThread GC.
        """
        from PyQt5.QtCore import QThread, pyqtSignal
        from PyQt5.QtWidgets import QProgressDialog

        class DHCPPoolPostWorker(QThread):
            # emits: (ok, http_status, error_message, results_json_or_none)
            finished = pyqtSignal(bool, int, str, object)

            def __init__(self, url, body):
                super().__init__()
                self.url = url
                self.body = body

            def run(self):
                try:
                    response = requests.post(
                        f"{self.url}/api/device/dhcp/server/attach_pools",
                        json=self.body, timeout=20,
                    )
                except requests.RequestException as exc:
                    self.finished.emit(False, 0, str(exc), None)
                    return
                if response.status_code != 200:
                    err = response.text
                    try:
                        err = response.json().get("error", err)
                    except ValueError:
                        pass
                    self.finished.emit(
                        False, response.status_code, err or "", None,
                    )
                    return
                try:
                    data = response.json()
                except ValueError:
                    data = None
                self.finished.emit(True, 200, "", data)

        if operation == "attach":
            if detach_all:
                label = "Detaching DHCP pools..."
            else:
                label = "Attaching DHCP pools..."
        else:
            label = "Applying DHCP pools..."

        # Indeterminate; Cancel disabled — same policy as OSPF/BGP/
        # ISIS Apply (interrupting a partial pool apply leaves
        # dnsmasq in a half-configured state).
        progress = QProgressDialog(label, "Cancel", 0, 0, self.parent)
        progress.setWindowModality(2)  # Qt.WindowModal
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()

        worker = DHCPPoolPostWorker(server_url, payload)
        worker.setParent(self.parent)

        def _on_finished(ok, http_status, err, data):
            try:
                progress.close()
            except Exception:
                pass

            # v0.5.219 (audit fix C5): defer any modal dialog exec_()
            # to the next event-loop iteration via QTimer.singleShot.
            # Pre-fix, the modal ran inline while the QThread's
            # finished signal was still unwinding — under PyQt5
            # 5.15.11 + Python 3.14 this ordering has bitten the
            # codebase enough to require SIGABRT guards elsewhere
            # (see the _dhcp_workers keepalive above, and the same
            # deferred-exec pattern already used in OSPF/BGP/ISIS
            # apply finish handlers).
            def _show_modal(dlg_title, dlg_msg, entries, level, do_refresh):
                try:
                    from widgets.devices_tab import MultiDeviceResultsDialog
                    MultiDeviceResultsDialog(
                        dlg_title, dlg_msg, entries, self.parent,
                    ).exec_()
                except Exception:
                    if level == "warning":
                        QMessageBox.warning(self.parent, dlg_title, dlg_msg)
                    else:
                        QMessageBox.information(self.parent, dlg_title, dlg_msg)
                if do_refresh:
                    try:
                        self.refresh_dhcp_status()
                    except Exception:
                        pass
                    if operation == "apply":
                        try:
                            from widgets.preflight_bar import kick_refresh
                            kick_refresh(self.parent)
                        except Exception:
                            pass

            if not ok:
                if operation == "attach":
                    title = ("DHCP Detach Failed" if detach_all
                             else "DHCP Pool Update Failed")
                    default_msg = (
                        "Failed to detach DHCP pools." if detach_all
                        else "Failed to update DHCP server pools."
                    )
                else:
                    title = "DHCP Apply Failed"
                    default_msg = "Failed to apply DHCP server pools."
                fail_msg = err or default_msg
                QTimer.singleShot(0, lambda t=title, m=fail_msg: _show_modal(
                    t,
                    f"{operation.title()} failed for device {device_id}",
                    [f"❌ {device_id}: {m}"],
                    "warning",
                    False,
                ))
                return

            # Success — success dialog + refresh (deferred).
            if operation == "attach":
                if detach_all:
                    title = "DHCP Pools Detached"
                    msg = "All DHCP pools detached from the server."
                else:
                    title = "DHCP Pools Attached"
                    msg = "The selected DHCP pools have been attached."
            else:
                title = "DHCP Pools Applied"
                msg = "Attached DHCP pools have been applied to the server."
            QTimer.singleShot(0, lambda t=title, m=msg: _show_modal(
                t, m, [f"✅ {device_id}: {m}"], "info", True,
            ))

        worker.finished.connect(_on_finished)
        worker.start()

        if not hasattr(self, "_dhcp_workers"):
            self._dhcp_workers = []
        self._dhcp_workers.append(worker)

        def _still_running(w):
            try:
                return w.isRunning()
            except RuntimeError:
                return False
        self._dhcp_workers = [
            w for w in self._dhcp_workers if _still_running(w)
        ]

    def apply_dhcp_pools(self):
        """Reapply the currently attached DHCP pools for the selected server."""
        metadata = self._get_selected_metadata()
        if not metadata:
            QMessageBox.information(self.parent, "Select Device", "Select a DHCP server row first.")
            return

        if (metadata.get("mode") or "") != "server":
            QMessageBox.warning(
                self.parent,
                "Invalid Selection",
                "Please select a DHCP server entry before applying pools.",
            )
            return

        server_url = self.parent.get_server_url(silent=True)
        if not server_url:
            QMessageBox.warning(
                self.parent,
                "Server Unavailable",
                "No server is currently configured. Connect to a server before applying DHCP pools.",
            )
            return

        device_id = metadata.get("device_id")
        if not device_id:
            QMessageBox.warning(self.parent, "Error", "Unable to determine the selected device ID.")
            return

        try:
            device_resp = requests.get(
                f"{server_url}/api/device/database/devices/{device_id}",
                timeout=5,
            )
        except requests.RequestException as exc:
            logging.error("[DHCP UI] Failed to fetch device %s: %s", device_id, exc)
            QMessageBox.warning(self.parent, "Request Failed", str(exc))
            return

        if device_resp.status_code != 200:
            error_text = device_resp.text
            try:
                error_json = device_resp.json()
                error_text = error_json.get("error", error_text)
            except ValueError:
                pass
            QMessageBox.warning(
                self.parent,
                "Device Lookup Failed",
                f"Unable to fetch device details: {error_text}",
            )
            return

        try:
            device_data = device_resp.json()
        except ValueError:
            QMessageBox.warning(self.parent, "Error", "Invalid response received from server.")
            return

        dhcp_config = device_data.get("dhcp_config") or {}
        if isinstance(dhcp_config, str):
            try:
                dhcp_config = json.loads(dhcp_config) if dhcp_config else {}
            except Exception:
                dhcp_config = {}
        if not isinstance(dhcp_config, dict):
            dhcp_config = {}

        pool_names = dhcp_config.get("pool_names") or {}
        if isinstance(pool_names, str):
            try:
                pool_names = json.loads(pool_names) if pool_names else {}
            except Exception:
                pool_names = {}
        if not isinstance(pool_names, dict):
            pool_names = {}

        primary_pool = pool_names.get("primary") or dhcp_config.get("pool_name")
        additional_pools = pool_names.get("additional") or []
        if isinstance(additional_pools, str):
            additional_pools = [additional_pools]
        if not isinstance(additional_pools, (list, tuple, set)):
            additional_pools = []

        # v0.5.218 (audit fix J): the two POST tails below used to
        # each block the UI thread on requests.post(..., timeout=20).
        # Route both through the shared _run_dhcp_pool_post worker
        # (indeterminate progress dialog, Cancel disabled to match
        # OSPF/BGP/ISIS Apply policy). preflight_bar.kick_refresh
        # (v0.2.85) is now handled by the worker's finished handler
        # for the "apply" operation.

        # Handle case where no pools are attached — ensure DHCP
        # server is stopped by sending detach_all.
        if not primary_pool:
            payload = {
                "device_id": device_id,
                "detach_all": True,
            }
            self._run_dhcp_pool_post(
                server_url=server_url,
                device_id=device_id,
                payload=payload,
                operation="apply",
                detach_all=True,
            )
            return

        payload = {
            "device_id": device_id,
            "primary_pool": primary_pool,
            "additional_pools": [
                str(name)
                for name in additional_pools
                if name and str(name) != primary_pool
            ],
            "replace_existing": True,
        }

        gateway_value = dhcp_config.get("gateway")
        if gateway_value:
            payload["gateway"] = gateway_value

        self._run_dhcp_pool_post(
            server_url=server_url,
            device_id=device_id,
            payload=payload,
            operation="apply",
            detach_all=False,
        )

