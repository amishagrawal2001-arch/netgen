"""
OSPF Route Pool Management Dialog
Similar to BGP route pool management but for OSPF areas
"""

import json
import logging
import requests
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, 
                             QTableWidgetItem, QPushButton, QLabel, QMessageBox,
                             QHeaderView, QCheckBox, QComboBox, QLineEdit, 
                             QFormLayout, QGroupBox, QDialogButtonBox, QWidget,
                             QTabWidget, QTextEdit, QSpinBox, QFrame)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette
from typing import Dict, List, Any, Optional

class ManageOspfRoutePoolsDialog(QDialog):
    """Dialog for managing OSPF route pools."""
    
    def __init__(self, parent=None, device_name="", device_id="", ospf_config=None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage OSPF Route Pools - {device_name}")
        self.setFixedSize(800, 600)
        self.device_name = device_name
        self.device_id = device_id
        self.ospf_config = ospf_config or {}
        self.route_pools = []
        self.available_pools = []
        
        self.setup_ui()
        self.load_available_pools()
        
    def setup_ui(self):
        """Setup the user interface."""
        layout = QVBoxLayout()
        
        # Title
        title_label = QLabel(f"OSPF Route Pool Management - {self.device_name}")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # OSPF Configuration Info
        config_group = QGroupBox("OSPF Configuration")
        config_layout = QFormLayout(config_group)
        
        area_id = self.ospf_config.get("area_id", "0.0.0.0")
        router_id = self.ospf_config.get("router_id", "Auto-assigned")
        
        config_layout.addRow("Area ID:", QLabel(area_id))
        config_layout.addRow("Router ID:", QLabel(router_id))
        
        layout.addWidget(config_group)
        
        # Available Pools Section
        available_group = QGroupBox("Available Route Pools")
        available_layout = QVBoxLayout(available_group)
        
        # Pool selection table
        self.pools_table = QTableWidget()
        self.pools_table.setColumnCount(6)
        self.pools_table.setHorizontalHeaderLabels([
            "Select", "Pool Name", "Subnet", "Count", "Type", "Address Family"
        ])
        
        # Set column widths
        header = self.pools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        
        self.pools_table.setColumnWidth(0, 60)
        self.pools_table.setColumnWidth(3, 80)
        self.pools_table.setColumnWidth(4, 100)
        self.pools_table.setColumnWidth(5, 120)
        
        available_layout.addWidget(self.pools_table)
        
        # Add selected pools button
        add_button = QPushButton("Add Selected Pools to OSPF")
        add_button.clicked.connect(self.add_selected_pools)
        available_layout.addWidget(add_button)
        
        layout.addWidget(available_group)
        
        # Attached Pools Section
        attached_group = QGroupBox("Attached Route Pools")
        attached_layout = QVBoxLayout(attached_group)
        
        self.attached_table = QTableWidget()
        self.attached_table.setColumnCount(5)
        self.attached_table.setHorizontalHeaderLabels([
            "Pool Name", "Subnet", "Count", "Type", "Actions"
        ])
        
        # Set column widths
        attached_header = self.attached_table.horizontalHeader()
        attached_header.setSectionResizeMode(0, QHeaderView.Stretch)
        attached_header.setSectionResizeMode(1, QHeaderView.Stretch)
        attached_header.setSectionResizeMode(2, QHeaderView.Fixed)
        attached_header.setSectionResizeMode(3, QHeaderView.Fixed)
        attached_header.setSectionResizeMode(4, QHeaderView.Fixed)
        
        self.attached_table.setColumnWidth(2, 80)
        self.attached_table.setColumnWidth(3, 100)
        self.attached_table.setColumnWidth(4, 100)
        
        attached_layout.addWidget(self.attached_table)
        
        layout.addWidget(attached_group)
        
        # Buttons
        button_box = QDialogButtonBox()
        apply_button = button_box.addButton("Apply Configuration", QDialogButtonBox.AcceptRole)
        cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        apply_button.clicked.connect(self.apply_configuration)
        cancel_button.clicked.connect(self.reject)
        
        layout.addWidget(button_box)
        
        self.setLayout(layout)
        
    def load_available_pools(self):
        """Load available route pools from the server."""
        try:
            response = requests.get("http://localhost:5051/api/ospf/pools", timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.available_pools = data.get("pools", [])
                self.populate_pools_table()
            else:
                QMessageBox.warning(self, "Error", f"Failed to load route pools: {response.status_code}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load route pools: {str(e)}")
    
    def populate_pools_table(self):
        """Populate the available pools table."""
        self.pools_table.setRowCount(len(self.available_pools))
        
        for row, pool in enumerate(self.available_pools):
            # Checkbox
            checkbox = QCheckBox()
            self.pools_table.setCellWidget(row, 0, checkbox)
            
            # Pool name
            self.pools_table.setItem(row, 1, QTableWidgetItem(pool["name"]))
            
            # Subnet
            self.pools_table.setItem(row, 2, QTableWidgetItem(pool["subnet"]))
            
            # Count
            self.pools_table.setItem(row, 3, QTableWidgetItem(str(pool["count"])))
            
            # Type
            increment_type = pool.get("increment_type", "host")
            self.pools_table.setItem(row, 4, QTableWidgetItem(increment_type.title()))
            
            # Address family
            address_family = self._detect_address_family(pool["subnet"])
            self.pools_table.setItem(row, 5, QTableWidgetItem(address_family.upper()))
    
    def _detect_address_family(self, subnet: str) -> str:
        """Detect if subnet is IPv4 or IPv6."""
        if ":" in subnet:
            return "ipv6"
        else:
            return "ipv4"
    
    def add_selected_pools(self):
        """Add selected pools to the attached pools table."""
        selected_pools = []
        
        for row in range(self.pools_table.rowCount()):
            checkbox = self.pools_table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                pool_name = self.pools_table.item(row, 1).text()
                subnet = self.pools_table.item(row, 2).text()
                count = self.pools_table.item(row, 3).text()
                increment_type = self.pools_table.item(row, 4).text().lower()
                
                # Find the full pool data
                pool_data = None
                for pool in self.available_pools:
                    if pool["name"] == pool_name:
                        pool_data = pool
                        break
                
                if pool_data:
                    selected_pools.append(pool_data)
        
        # Add to attached pools
        for pool in selected_pools:
            self.add_pool_to_table(pool)
        
        # Clear selections
        for row in range(self.pools_table.rowCount()):
            checkbox = self.pools_table.cellWidget(row, 0)
            if checkbox:
                checkbox.setChecked(False)
    
    def add_pool_to_table(self, pool_data):
        """Add a pool to the attached pools table."""
        row = self.attached_table.rowCount()
        self.attached_table.insertRow(row)
        
        # Pool name
        self.attached_table.setItem(row, 0, QTableWidgetItem(pool_data["name"]))
        
        # Subnet
        self.attached_table.setItem(row, 1, QTableWidgetItem(pool_data["subnet"]))
        
        # Count
        self.attached_table.setItem(row, 2, QTableWidgetItem(str(pool_data["count"])))
        
        # Type
        increment_type = pool_data.get("increment_type", "host")
        self.attached_table.setItem(row, 3, QTableWidgetItem(increment_type.title()))
        
        # Remove button
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: self.remove_pool(row))
        self.attached_table.setCellWidget(row, 4, remove_button)
        
        # Store pool data
        pool_entry = {
            "name": pool_data["name"],
            "subnet": pool_data["subnet"],
            "count": pool_data["count"],
            "increment_type": increment_type
        }
        self.route_pools.append(pool_entry)
    
    def remove_pool(self, row):
        """Remove a pool from the attached pools table."""
        if 0 <= row < self.attached_table.rowCount():
            self.attached_table.removeRow(row)
            if row < len(self.route_pools):
                self.route_pools.pop(row)
    
    def apply_configuration(self):
        """Apply the OSPF route pool configuration."""
        if not self.route_pools:
            QMessageBox.information(self, "No Pools", "No route pools selected for OSPF.")
            return
        
        # Show configuration summary
        area_id = self.ospf_config.get("area_id", "0.0.0.0")
        summary = f"OSPF Area: {area_id}\n"
        summary += f"Route Pools: {len(self.route_pools)}\n\n"
        
        for pool in self.route_pools:
            summary += f"• {pool['name']}: {pool['subnet']} ({pool['count']} routes, {pool['increment_type']})\n"
        
        reply = QMessageBox.question(
            self, "Apply Configuration", 
            f"Apply this OSPF route pool configuration?\n\n{summary}",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.accept()


class AttachOspfRoutePoolsDialog(QDialog):
    """Dialog for attaching route pools to OSPF areas."""
    
    def __init__(self, parent=None, device_name="", device_id="", ospf_config=None, available_pools=None, attached_pools=None):
        super().__init__(parent)
        self.setWindowTitle(f"Attach Route Pools to OSPF - {device_name}")
        self.setFixedSize(550, 450)
        self.device_name = device_name
        self.device_id = device_id
        self.ospf_config = ospf_config or {}
        self.available_pools = available_pools or []
        self.attached_pool_names = attached_pools or []  # List of pool names attached to this device
        self.selected_pools = []
        
        self.setup_ui()
        if self.available_pools:
            self.populate_pools_table(self.available_pools)
        
    def setup_ui(self):
        """Setup the user interface."""
        layout = QVBoxLayout()
        
        # Title
        title_label = QLabel(f"Attach Route Pools to OSPF - {self.device_name}")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # OSPF Configuration Info
        config_group = QGroupBox("OSPF Configuration")
        config_layout = QFormLayout(config_group)
        
        area_id = self.ospf_config.get("area_id", "0.0.0.0")
        router_id = self.ospf_config.get("router_id", "Auto-assigned")
        
        config_layout.addRow("Area ID:", QLabel(area_id))
        config_layout.addRow("Router ID:", QLabel(router_id))
        
        layout.addWidget(config_group)
        
        # Available Pools Section
        pools_group = QGroupBox("Available Route Pools")
        pools_layout = QVBoxLayout(pools_group)
        
        # Pool selection table
        self.pools_table = QTableWidget()
        self.pools_table.setColumnCount(6)
        self.pools_table.setHorizontalHeaderLabels([
            "Select", "Pool Name", "Subnet", "Count", "Type", "Address Family"
        ])
        
        # Set column widths
        header = self.pools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        
        self.pools_table.setColumnWidth(0, 60)
        self.pools_table.setColumnWidth(3, 80)
        self.pools_table.setColumnWidth(4, 100)
        self.pools_table.setColumnWidth(5, 120)
        
        pools_layout.addWidget(self.pools_table)
        
        layout.addWidget(pools_group)
        
        # Summary label
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("background: #e8f4f8; padding: 10px; border-radius: 3px;")
        self.update_summary()
        layout.addWidget(self.summary_label)
        
        # Connect checkboxes to update summary
        # Note: We'll update the summary in populate_pools_table after creating checkboxes
        
        # Buttons
        button_box = QDialogButtonBox()
        attach_button = button_box.addButton("Apply", QDialogButtonBox.AcceptRole)
        cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        attach_button.clicked.connect(self.attach_selected_pools)
        cancel_button.clicked.connect(self.reject)
        
        layout.addWidget(button_box)
        
        self.setLayout(layout)
        
    def load_available_pools(self):
        """Load available route pools from the server."""
        try:
            response = requests.get("http://localhost:5051/api/ospf/pools", timeout=10)
            if response.status_code == 200:
                data = response.json()
                pools = data.get("pools", [])
                self.populate_pools_table(pools)
            else:
                QMessageBox.warning(self, "Error", f"Failed to load route pools: {response.status_code}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load route pools: {str(e)}")
    
    def populate_pools_table_from_dict(self, pools):
        """Populate the pools table from a list of dicts (for compatibility with BGP pools)."""
        self.populate_pools_table(pools)
    
    def populate_pools_table(self, pools):
        """Populate the available pools table."""
        self.pools_table.setRowCount(len(pools))
        
        for row, pool in enumerate(pools):
            # Checkbox
            checkbox = QCheckBox()
            # Pre-check if this pool is attached
            checkbox.setChecked(pool["name"] in self.attached_pool_names)
            # Connect checkbox to update summary
            checkbox.toggled.connect(self.update_summary)
            self.pools_table.setCellWidget(row, 0, checkbox)
            
            # Pool name
            self.pools_table.setItem(row, 1, QTableWidgetItem(pool["name"]))
            
            # Subnet
            self.pools_table.setItem(row, 2, QTableWidgetItem(pool["subnet"]))
            
            # Count
            self.pools_table.setItem(row, 3, QTableWidgetItem(str(pool["count"])))
            
            # Type
            increment_type = pool.get("increment_type", "host")
            self.pools_table.setItem(row, 4, QTableWidgetItem(increment_type.title()))
            
            # Address family
            subnet = pool.get("subnet", "")
            pool_af = pool.get("address_family", None)
            if not pool_af or pool_af == "":
                pool_af = "ipv6" if ":" in subnet else "ipv4"
            elif isinstance(pool_af, str):
                pool_af = pool_af.lower().strip()
            else:
                pool_af = str(pool_af).lower()
            self.pools_table.setItem(row, 5, QTableWidgetItem(pool_af.upper()))
        
        # Update summary after populating
        self.update_summary()
    
    def update_summary(self):
        """Update summary label with selected pools info."""
        if not hasattr(self, 'pools_table'):
            return
        
        selected_count = 0
        total_routes = 0
        
        for row in range(self.pools_table.rowCount()):
            checkbox = self.pools_table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                selected_count += 1
                # Get route count from the table
                try:
                    route_count = int(self.pools_table.item(row, 3).text())
                    total_routes += route_count
                except (ValueError, AttributeError):
                    pass
        
        if selected_count == 0:
            self.summary_label.setText("ℹ️ No route pools selected - device will not advertise any routes")
        else:
            self.summary_label.setText(f"✅ Selected {selected_count} pool(s) → Total {total_routes} routes")
    
    def attach_selected_pools(self):
        """Attach selected pools to OSPF."""
        selected_pools = []
        
        for row in range(self.pools_table.rowCount()):
            checkbox = self.pools_table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                pool_name = self.pools_table.item(row, 1).text()
                selected_pools.append(pool_name)
        
        # Allow empty selection (to detach all pools)
        self.selected_pools = selected_pools
        self.accept()
    
    def get_selected_pools(self):
        """Get the list of selected pool names."""
        return self.selected_pools


