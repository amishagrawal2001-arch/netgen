from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLineEdit, QComboBox, QGroupBox, QDialogButtonBox,
    QWidget, QMessageBox, QCheckBox, QSpinBox, QLabel, QSizePolicy
)
from PyQt5.QtGui import QIntValidator
from PyQt5.QtCore import Qt
import ipaddress


class AddVxlanDialog(QDialog):
    def __init__(self, parent=None, device_name="", edit_mode=False, device_ipv4="", loopback_ipv4=""):
        super().__init__(parent)
        self.edit_mode = edit_mode
        title = f"Edit VXLAN Configuration - {device_name}" if edit_mode else f"Add VXLAN Tunnel - {device_name}"
        self.setWindowTitle(title)
        self.setMinimumSize(650, 650)
        self.resize(650, 650)
        self.device_name = device_name
        self.device_ipv4 = device_ipv4
        self.loopback_ipv4 = loopback_ipv4 or device_ipv4
        
        self.layout = QVBoxLayout()
        self.setup_vxlan_form()
        
        self.button_box = QDialogButtonBox()
        button_text = "Update VXLAN" if edit_mode else "Add VXLAN"
        self.ok_button = self.button_box.addButton(button_text, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_vxlan_form(self):
        """Setup VXLAN configuration form for Approach 2 (VLAN-aware bridge)."""
        form_widget = QWidget()
        layout = QVBoxLayout(form_widget)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 8, 10, 8)

        # VXLAN Basic Configuration
        basic_group = QGroupBox("VXLAN Basic Configuration")
        basic_main_layout = QVBoxLayout(basic_group)
        basic_main_layout.setSpacing(4)
        basic_main_layout.setContentsMargins(10, 12, 10, 8)
        
        # Two-column layout
        basic_row_layout = QHBoxLayout()
        basic_row_layout.setSpacing(5)
        
        # Left column
        basic_left_form = QFormLayout()
        basic_left_form.setSpacing(4)
        basic_left_form.setLabelAlignment(Qt.AlignRight)
        
        self.vni_input = QLineEdit("5000")
        self.vni_input.setPlaceholderText("1-16777215")
        self.vni_input.setValidator(QIntValidator(1, 16777215, self))
        basic_left_form.addRow("VNI:", self.vni_input)
        
        # Right column
        basic_right_form = QFormLayout()
        basic_right_form.setSpacing(4)
        basic_right_form.setLabelAlignment(Qt.AlignRight)
        
        self.udp_port_input = QLineEdit("4789")
        self.udp_port_input.setPlaceholderText("UDP Port (default: 4789)")
        self.udp_port_input.setValidator(QIntValidator(1, 65535, self))
        basic_right_form.addRow("UDP Port:", self.udp_port_input)
        
        basic_row_layout.addLayout(basic_left_form, 1)
        basic_row_layout.addLayout(basic_right_form, 1)
        basic_main_layout.addLayout(basic_row_layout)
        
        layout.addWidget(basic_group)

        # VXLAN Endpoints
        endpoint_group = QGroupBox("VXLAN Endpoints")
        endpoint_main_layout = QVBoxLayout(endpoint_group)
        endpoint_main_layout.setSpacing(4)
        endpoint_main_layout.setContentsMargins(10, 12, 10, 8)
        
        # Two-column layout
        endpoint_row_layout = QHBoxLayout()
        endpoint_row_layout.setSpacing(10)
        
        # Left column
        endpoint_left_form = QFormLayout()
        endpoint_left_form.setSpacing(4)
        endpoint_left_form.setLabelAlignment(Qt.AlignRight)
        
        local_default = self.loopback_ipv4 if self.loopback_ipv4 else "192.255.0.1"
        self.local_endpoint_input = QLineEdit(local_default)
        self.local_endpoint_input.setPlaceholderText("Local VTEP IP (e.g., 192.255.0.1)")
        endpoint_left_form.addRow("Local Endpoint:", self.local_endpoint_input)
        
        # Right column
        endpoint_right_form = QFormLayout()
        endpoint_right_form.setSpacing(4)
        endpoint_right_form.setLabelAlignment(Qt.AlignRight)
        
        self.remote_endpoint_input = QLineEdit("192.168.250.1")
        self.remote_endpoint_input.setPlaceholderText("Remote VTEP IP (e.g., 192.168.250.1)")
        endpoint_right_form.addRow("Remote Endpoint:", self.remote_endpoint_input)
        
        endpoint_row_layout.addLayout(endpoint_left_form, 1)
        endpoint_row_layout.addLayout(endpoint_right_form, 1)
        endpoint_main_layout.addLayout(endpoint_row_layout)
        
        layout.addWidget(endpoint_group)

        # Approach 2: VLAN-Aware Bridge Configuration
        bridge_group = QGroupBox("VLAN-Aware Bridge Configuration (Approach 2)")
        bridge_main_layout = QVBoxLayout(bridge_group)
        bridge_main_layout.setSpacing(4)
        bridge_main_layout.setContentsMargins(10, 12, 10, 8)
        
        # Two-column layout
        bridge_row_layout = QHBoxLayout()
        bridge_row_layout.setSpacing(10)
        
        # Left column
        bridge_left_form = QFormLayout()
        bridge_left_form.setSpacing(4)
        bridge_left_form.setLabelAlignment(Qt.AlignRight)
        
        self.vlan_id_input = QLineEdit()
        self.vlan_id_input.setPlaceholderText("VLAN ID (1-4094, optional)")
        self.vlan_id_input.setValidator(QIntValidator(1, 4094, self))
        bridge_left_form.addRow("VLAN ID (VLAN→VNI):", self.vlan_id_input)
        
        # Right column
        bridge_right_form = QFormLayout()
        bridge_right_form.setSpacing(4)
        bridge_right_form.setLabelAlignment(Qt.AlignRight)
        
        self.bridge_svi_ip_input = QLineEdit("10.0.0.100/24")
        self.bridge_svi_ip_input.setPlaceholderText("Bridge SVI IP (CIDR, e.g., 10.0.0.100/24)")
        bridge_right_form.addRow("Bridge SVI IP:", self.bridge_svi_ip_input)
        
        bridge_row_layout.addLayout(bridge_left_form, 1)
        bridge_row_layout.addLayout(bridge_right_form, 1)
        bridge_main_layout.addLayout(bridge_row_layout)
        
        # Help text inside the group box
        help_label = QLabel("(Maps VLAN to VNI for VLAN-aware VXLAN)")
        help_label.setStyleSheet("color: gray; font-style: italic;")
        bridge_main_layout.addWidget(help_label)
        
        layout.addWidget(bridge_group)

        # Increment Options (for adding multiple tunnels)
        increment_group = QGroupBox("Increment Options (for Multiple Tunnels)")
        increment_layout = QVBoxLayout(increment_group)
        increment_layout.setSpacing(5)
        increment_layout.setContentsMargins(10, 12, 10, 8)
        
        # Enable increment checkbox
        self.increment_checkbox = QCheckBox("Enable Increment (Add Multiple Tunnels)")
        increment_layout.addWidget(self.increment_checkbox)
        
        # Two-column layout for increment controls
        increment_columns_layout = QHBoxLayout()
        increment_columns_layout.setSpacing(20)
        
        # Left column
        increment_left_layout = QFormLayout()
        increment_left_layout.setSpacing(4)
        increment_left_layout.setLabelAlignment(Qt.AlignRight)
        
        # VNI increment
        self.vni_increment_combo = QComboBox()
        self.vni_increment_combo.addItems(["+1", "+10", "+100", "+1000"])
        self.vni_increment_combo.setCurrentIndex(0)
        self.vni_increment_combo.setEnabled(False)
        self.vni_increment_combo.setFixedWidth(80)
        increment_left_layout.addRow("VNI Increment:", self.vni_increment_combo)
        
        # VLAN ID increment
        self.vlan_id_increment_combo = QComboBox()
        self.vlan_id_increment_combo.addItems(["+1", "+10", "+100"])
        self.vlan_id_increment_combo.setCurrentIndex(0)
        self.vlan_id_increment_combo.setEnabled(False)
        self.vlan_id_increment_combo.setFixedWidth(80)
        increment_left_layout.addRow("VLAN ID Increment:", self.vlan_id_increment_combo)
        
        # Right column
        increment_right_layout = QFormLayout()
        increment_right_layout.setSpacing(4)
        increment_right_layout.setLabelAlignment(Qt.AlignRight)
        
        # Bridge SVI IP increment
        self.svi_ip_octet_combo = QComboBox()
        self.svi_ip_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        self.svi_ip_octet_combo.setCurrentIndex(2)  # Default to 3rd octet
        self.svi_ip_octet_combo.setEnabled(False)
        self.svi_ip_octet_combo.setFixedWidth(80)
        increment_right_layout.addRow("Bridge SVI IP Increment:", self.svi_ip_octet_combo)
        
        # Count
        self.increment_count = QSpinBox()
        self.increment_count.setMinimum(1)
        self.increment_count.setMaximum(100)
        self.increment_count.setValue(1)
        self.increment_count.setEnabled(False)
        self.increment_count.setFixedWidth(80)
        increment_right_layout.addRow("Count:", self.increment_count)
        
        # Add columns to main layout
        increment_columns_layout.addLayout(increment_left_layout, 1)
        increment_columns_layout.addLayout(increment_right_layout, 1)
        increment_layout.addLayout(increment_columns_layout)
        
        layout.addWidget(increment_group)
        
        # Connect increment checkbox
        self.increment_checkbox.toggled.connect(self.toggle_increment_controls)

        self.layout.addWidget(form_widget)

    def toggle_increment_controls(self, enabled):
        """Enable/disable increment controls."""
        self.vni_increment_combo.setEnabled(enabled)
        self.vlan_id_increment_combo.setEnabled(enabled)
        self.increment_count.setEnabled(enabled)
        self.svi_ip_octet_combo.setEnabled(enabled)

    def get_values(self):
        """Get VXLAN configuration values."""
        config = {
            "vni": int(self.vni_input.text().strip()) if self.vni_input.text().strip() else None,
            "udp_port": int(self.udp_port_input.text().strip()) if self.udp_port_input.text().strip() else 4789,
            "local_ip": self.local_endpoint_input.text().strip(),
            "remote_peers": [ip.strip() for ip in self.remote_endpoint_input.text().strip().split(",") if ip.strip()],
            "bridge_svi_ip": self.bridge_svi_ip_input.text().strip() if self.bridge_svi_ip_input.text().strip() else None,
        }
        
        # VLAN ID (optional - for Approach 2)
        vlan_id_text = self.vlan_id_input.text().strip()
        if vlan_id_text:
            config["vlan_id"] = int(vlan_id_text)
        
        # Increment settings
        if self.increment_checkbox.isChecked():
            config["increment"] = {
                "enabled": True,
                "vni_increment": [1, 10, 100, 1000][self.vni_increment_combo.currentIndex()],
                "vlan_id_increment": [1, 10, 100][self.vlan_id_increment_combo.currentIndex()],
                "count": self.increment_count.value(),
                "svi_ip_octet": ["4th", "3rd", "2nd", "1st"][self.svi_ip_octet_combo.currentIndex()],
            }
        
        return config

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate VXLAN configuration."""
        # Validate VNI
        vni_text = self.vni_input.text().strip()
        if not vni_text:
            QMessageBox.warning(self, "Missing VNI", "Please provide a VNI (1-16777215).")
            return False
        
        try:
            vni = int(vni_text)
            if vni < 1 or vni > 16777215:
                raise ValueError("VNI out of range")
        except ValueError:
            QMessageBox.warning(self, "Invalid VNI", "VNI must be an integer between 1 and 16777215.")
            return False
        
        # Validate Local Endpoint
        local_ip = self.local_endpoint_input.text().strip()
        if not local_ip:
            QMessageBox.warning(self, "Missing Local Endpoint", "Please provide a Local VTEP IP address.")
            return False
        
        try:
            ipaddress.IPv4Address(local_ip)
        except Exception:
            QMessageBox.warning(self, "Invalid Local Endpoint", "Local Endpoint must be a valid IPv4 address.")
            return False
        
        # Validate Remote Endpoint
        remote_ip = self.remote_endpoint_input.text().strip()
        if not remote_ip:
            QMessageBox.warning(self, "Missing Remote Endpoint", "Please provide a Remote VTEP IP address.")
            return False
        
        # Validate each remote IP (comma-separated)
        remote_ips = [ip.strip() for ip in remote_ip.split(",") if ip.strip()]
        for ip in remote_ips:
            try:
                ipaddress.IPv4Address(ip)
            except Exception:
                QMessageBox.warning(self, "Invalid Remote Endpoint", f"Remote Endpoint '{ip}' must be a valid IPv4 address.")
                return False
        
        # Validate VLAN ID if provided
        vlan_id_text = self.vlan_id_input.text().strip()
        if vlan_id_text:
            try:
                vlan_id = int(vlan_id_text)
                if vlan_id < 1 or vlan_id > 4094:
                    raise ValueError("VLAN ID out of range")
            except ValueError:
                QMessageBox.warning(self, "Invalid VLAN ID", "VLAN ID must be an integer between 1 and 4094.")
                return False
        
        # Validate Bridge SVI IP if provided
        bridge_svi_ip_text = self.bridge_svi_ip_input.text().strip()
        if bridge_svi_ip_text:
            try:
                # Parse CIDR notation
                if "/" in bridge_svi_ip_text:
                    ipaddress.IPv4Interface(bridge_svi_ip_text)
                else:
                    ipaddress.IPv4Address(bridge_svi_ip_text)
            except Exception:
                QMessageBox.warning(self, "Invalid Bridge SVI IP", "Bridge SVI IP must be a valid IPv4 address or CIDR notation (e.g., 10.0.0.100/24).")
                return False
        
        # Validate UDP Port
        udp_port_text = self.udp_port_input.text().strip()
        if udp_port_text:
            try:
                udp_port = int(udp_port_text)
                if udp_port < 1 or udp_port > 65535:
                    raise ValueError("UDP Port out of range")
            except ValueError:
                QMessageBox.warning(self, "Invalid UDP Port", "UDP Port must be an integer between 1 and 65535.")
                return False

        return True

