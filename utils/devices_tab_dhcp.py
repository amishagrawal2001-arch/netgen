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

    def __init__(self, parent, server_url: str, device_name: str, existing_selection: Optional[Dict] = None):
        super().__init__(parent)
        self.server_url = server_url
        self.device_name = device_name
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
                "No DHCP pools found.\nUse 'Manage DHCP Pools' to create pools before attaching.",
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
        self.gateway_override_edit.setPlaceholderText("Leave blank to use pool-defined gateway")
        self.gateway_override_edit.setFixedWidth(220)
        options_layout.addWidget(self.gateway_override_edit)
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
        layout.addWidget(QLabel("DHCP Status"))
        layout.addWidget(self.parent.dhcp_table)

        # Controls
        controls = QHBoxLayout()
        controls.setAlignment(Qt.AlignLeft)

        def load_icon(filename: str):
            from utils.qicon_loader import qicon

            return qicon("resources", f"icons/{filename}")

        self.parent.dhcp_refresh_button = QPushButton()
        self.parent.dhcp_refresh_button.setIcon(load_icon("refresh.png"))
        self.parent.dhcp_refresh_button.setIconSize(QSize(16, 16))
        self.parent.dhcp_refresh_button.setFixedSize(32, 28)
        self.parent.dhcp_refresh_button.setToolTip("Refresh DHCP status")
        self.parent.dhcp_refresh_button.clicked.connect(self.refresh_dhcp_status)

        controls.addWidget(self.parent.dhcp_refresh_button)

        self.parent.dhcp_manage_button = QPushButton()
        self.parent.dhcp_manage_button.setIcon(load_icon("edit.png"))
        self.parent.dhcp_manage_button.setIconSize(QSize(16, 16))
        self.parent.dhcp_manage_button.setFixedSize(32, 28)
        self.parent.dhcp_manage_button.setToolTip("Manage DHCP Pools")
        self.parent.dhcp_manage_button.clicked.connect(self.manage_dhcp_pools)
        controls.addWidget(self.parent.dhcp_manage_button)

        self.parent.dhcp_attach_button = QPushButton()
        self.parent.dhcp_attach_button.setIcon(load_icon("readd.png"))
        self.parent.dhcp_attach_button.setIconSize(QSize(16, 16))
        self.parent.dhcp_attach_button.setFixedSize(32, 28)
        self.parent.dhcp_attach_button.setToolTip("Attach DHCP pools to the selected server")
        self.parent.dhcp_attach_button.clicked.connect(self.attach_dhcp_pools)
        controls.addWidget(self.parent.dhcp_attach_button)

        self.parent.dhcp_apply_button = QPushButton()
        self.parent.dhcp_apply_button.setIcon(load_icon("apply.png"))
        self.parent.dhcp_apply_button.setIconSize(QSize(16, 16))
        self.parent.dhcp_apply_button.setFixedSize(32, 28)
        self.parent.dhcp_apply_button.setToolTip("Apply attached DHCP pools on the selected server")
        self.parent.dhcp_apply_button.clicked.connect(self.apply_dhcp_pools)
        controls.addWidget(self.parent.dhcp_apply_button)

        controls.addStretch()
        layout.addLayout(controls)

        # Kick off an initial status refresh once the UI finishes rendering
        QTimer.singleShot(200, self.refresh_dhcp_status)

    def refresh_dhcp_status(self):
        """Fetch DHCP status from server and update table."""
        try:
            server_url = self.parent.get_server_url(silent=True)
            if not server_url:
                logging.debug("[DHCP UI] No server URL configured")
                return
            response = requests.get(f"{server_url}/api/device/dhcp/status", timeout=5)
            if response.status_code != 200:
                logging.warning(f"[DHCP UI] Failed to fetch status: {response.status_code} {response.text}")
                return
            payload = response.json()
            devices = payload.get("devices", [])
            self._populate_dhcp_table(devices)
        except Exception as exc:
            logging.error(f"[DHCP UI] Exception refreshing DHCP status: {exc}")

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

        try:
            response = requests.post(
                f"{server_url}/api/device/dhcp/server/attach_pools",
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            logging.error("[DHCP UI] Failed to attach DHCP pools for %s: %s", device_id, exc)
            QMessageBox.warning(self.parent, "Request Failed", str(exc))
            return

        if response.status_code != 200:
            error_message = response.text
            try:
                error_json = response.json()
                error_message = error_json.get("error", error_message)
            except ValueError:
                pass
            title = "DHCP Detach Failed" if selection.get("detach_all") else "DHCP Pool Update Failed"
            message = error_message or ("Failed to detach DHCP pools." if selection.get("detach_all") else "Failed to update DHCP server pools.")
            QMessageBox.warning(self.parent, title, message)
            return

        if selection.get("detach_all"):
            QMessageBox.information(
                self.parent,
                "DHCP Pools Detached",
                "All DHCP pools have been detached from the server.",
            )
        else:
            QMessageBox.information(
                self.parent,
                "DHCP Pools Attached",
                "The selected DHCP pools have been attached successfully.",
            )
        self.refresh_dhcp_status()

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

        # Handle case where no pools are attached - ensure DHCP server is stopped
        if not primary_pool:
            # Send detach_all request to ensure server is stopped
            payload = {
                "device_id": device_id,
                "detach_all": True,
            }
            try:
                response = requests.post(
                    f"{server_url}/api/device/dhcp/server/attach_pools",
                    json=payload,
                    timeout=20,
                )
            except requests.RequestException as exc:
                logging.error("[DHCP UI] Failed to stop DHCP server for %s: %s", device_id, exc)
                QMessageBox.warning(self.parent, "Request Failed", str(exc))
                return

            if response.status_code != 200:
                error_message = response.text
                try:
                    error_json = response.json()
                    error_message = error_json.get("error", error_message)
                except ValueError:
                    pass
                QMessageBox.warning(
                    self.parent,
                    "DHCP Server Stop Failed",
                    error_message or "Failed to stop DHCP server.",
                )
                return

            QMessageBox.information(
                self.parent,
                "DHCP Server Stopped",
                "No pools are attached. DHCP server has been stopped.",
            )
            self.refresh_dhcp_status()
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

        try:
            response = requests.post(
                f"{server_url}/api/device/dhcp/server/attach_pools",
                json=payload,
                timeout=20,
            )
        except requests.RequestException as exc:
            logging.error("[DHCP UI] Failed to apply DHCP pools for %s: %s", device_id, exc)
            QMessageBox.warning(self.parent, "Request Failed", str(exc))
            return

        if response.status_code != 200:
            error_message = response.text
            try:
                error_json = response.json()
                error_message = error_json.get("error", error_message)
            except ValueError:
                pass
            QMessageBox.warning(
                self.parent,
                "DHCP Apply Failed",
                error_message or "Failed to apply DHCP server pools.",
            )
            return

        QMessageBox.information(
            self.parent,
            "DHCP Pools Applied",
            "Attached DHCP pools have been applied to the server.",
        )
        self.refresh_dhcp_status()

