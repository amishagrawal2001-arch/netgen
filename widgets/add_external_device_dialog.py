"""
Add External Device Dialog
Allows users to add external network devices (non-FRR containers)
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QGroupBox, QTextEdit, QMessageBox, QScrollArea, QWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
import uuid
import json

logger = None
try:
    import logging
    logger = logging.getLogger(__name__)
except Exception:
    pass


class AddExternalDeviceDialog(QDialog):
    """Dialog for adding external network devices"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add External Device")
        self.setMinimumSize(800, 600)
        self.resize(900, 700)
        
        self.device_data = {}
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components"""
        layout = QVBoxLayout(self)
        
        # Scroll area for long forms
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        
        # Title
        title = QLabel("Add External Network Device")
        title_font = title.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        scroll_layout.addWidget(title)
        
        # Device Information
        device_group = QGroupBox("Device Information")
        device_layout = QFormLayout()
        
        self.device_name_input = QLineEdit()
        self.device_name_input.setPlaceholderText("e.g., Core-Switch-01")
        device_layout.addRow("Device Name:", self.device_name_input)
        
        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems([
            "juniper",
            "cisco",
            "arista",
            "nokia",
            "other"
        ])
        device_layout.addRow("Device Type:", self.device_type_combo)
        
        self.device_id_input = QLineEdit()
        self.device_id_input.setPlaceholderText("Auto-generated if empty")
        device_layout.addRow("Device ID (optional):", self.device_id_input)
        
        device_group.setLayout(device_layout)
        scroll_layout.addWidget(device_group)
        
        # Connection Information
        connection_group = QGroupBox("Connection Information")
        connection_layout = QFormLayout()
        
        self.connection_method_combo = QComboBox()
        self.connection_method_combo.addItems(["ssh", "snmp", "rest", "netconf"])
        self.connection_method_combo.currentTextChanged.connect(self.on_connection_method_changed)
        connection_layout.addRow("Connection Method:", self.connection_method_combo)
        
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("192.168.1.1 or hostname")
        connection_layout.addRow("Host/IP:", self.host_input)
        
        self.port_input = QLineEdit()
        self.port_input.setValidator(QIntValidator(1, 65535))
        self.port_input.setText("22")  # Default SSH port
        connection_layout.addRow("Port:", self.port_input)
        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Username for SSH/REST")
        connection_layout.addRow("Username:", self.username_input)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password (optional if using key)")
        connection_layout.addRow("Password:", self.password_input)
        
        self.ssh_key_input = QLineEdit()
        self.ssh_key_input.setPlaceholderText("/path/to/ssh/key (optional)")
        connection_layout.addRow("SSH Key Path:", self.ssh_key_input)
        
        self.snmp_community_input = QLineEdit()
        self.snmp_community_input.setPlaceholderText("public")
        self.snmp_community_input.setText("public")
        connection_layout.addRow("SNMP Community:", self.snmp_community_input)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("API key for REST API")
        connection_layout.addRow("API Key:", self.api_key_input)
        
        connection_group.setLayout(connection_layout)
        scroll_layout.addWidget(connection_group)
        
        # Network Configuration (Optional)
        network_group = QGroupBox("Network Configuration (Optional)")
        network_layout = QFormLayout()
        
        self.ipv4_input = QLineEdit()
        self.ipv4_input.setPlaceholderText("192.168.1.1/24")
        network_layout.addRow("IPv4 Address:", self.ipv4_input)
        
        self.ipv6_input = QLineEdit()
        self.ipv6_input.setPlaceholderText("2001:db8::1/64")
        network_layout.addRow("IPv6 Address:", self.ipv6_input)
        
        network_group.setLayout(network_layout)
        scroll_layout.addWidget(network_group)
        
        # Test Connection
        test_btn = QPushButton("🔍 Test Connection")
        test_btn.clicked.connect(self.test_connection)
        scroll_layout.addWidget(test_btn)
        
        scroll_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        button_layout.addStretch()
        
        self.add_btn = QPushButton("Add Device")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 8px 20px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.add_btn.clicked.connect(self.add_device)
        button_layout.addWidget(self.add_btn)
        
        layout.addLayout(button_layout)
        
        # Update UI based on connection method
        self.on_connection_method_changed()
    
    def on_connection_method_changed(self):
        """Update UI based on selected connection method"""
        method = self.connection_method_combo.currentText()
        
        # Show/hide relevant fields
        self.username_input.setVisible(method in ["ssh", "rest", "netconf"])
        self.password_input.setVisible(method in ["ssh", "rest", "netconf"])
        self.ssh_key_input.setVisible(method == "ssh")
        self.snmp_community_input.setVisible(method == "snmp")
        self.api_key_input.setVisible(method == "rest")
        
        # Set default ports
        if method == "ssh" or method == "netconf":
            self.port_input.setText("22")
        elif method == "snmp":
            self.port_input.setText("161")
        elif method == "rest":
            self.port_input.setText("443")
    
    def test_connection(self):
        """Test connection to external device"""
        host = self.host_input.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing Host", "Please enter host/IP address")
            return
        
        method = self.connection_method_combo.currentText()
        
        # Simple ping test
        import subprocess
        result = subprocess.run(
            ["ping", "-c", "2", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            QMessageBox.information(self, "Connection Test", f"✅ Device {host} is reachable!")
        else:
            QMessageBox.warning(self, "Connection Test", f"❌ Device {host} is not reachable\n\nCheck:\n- Network connectivity\n- Firewall rules\n- Device is powered on")
    
    def add_device(self):
        """Add external device"""
        device_name = self.device_name_input.text().strip()
        if not device_name:
            QMessageBox.warning(self, "Missing Name", "Please enter device name")
            return
        
        host = self.host_input.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing Host", "Please enter host/IP address")
            return
        
        # Generate device ID if not provided
        device_id = self.device_id_input.text().strip()
        if not device_id:
            device_id = str(uuid.uuid4())
        
        # Build connection info
        connection_info = {
            "connection_method": self.connection_method_combo.currentText(),
            "host": host,
            "port": int(self.port_input.text()) if self.port_input.text() else 22,
        }
        
        method = connection_info["connection_method"]
        if method in ["ssh", "rest", "netconf"]:
            connection_info["username"] = self.username_input.text().strip()
            if self.password_input.text():
                connection_info["password"] = self.password_input.text()
            if self.ssh_key_input.text() and method == "ssh":
                connection_info["ssh_key"] = self.ssh_key_input.text().strip()
        elif method == "snmp":
            connection_info["snmp_community"] = self.snmp_community_input.text().strip() or "public"
        elif method == "rest":
            if self.api_key_input.text():
                connection_info["api_key"] = self.api_key_input.text().strip()
        
        # Build device data
        self.device_data = {
            "device_id": device_id,
            "device_name": device_name,
            "device_type": self.device_type_combo.currentText(),
            "connection_method": connection_info["connection_method"],
            "connection_host": host,
            "connection_port": connection_info["port"],
            "connection_username": connection_info.get("username"),
            "connection_info": json.dumps(connection_info),
            "ipv4_address": self.ipv4_input.text().strip() or None,
            "ipv6_address": self.ipv6_input.text().strip() or None,
        }
        
        self.accept()
    
    def get_device_data(self):
        """Get device data"""
        return self.device_data




