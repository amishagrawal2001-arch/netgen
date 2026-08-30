"""
Unified Add Device Dialog
Supports both FRR containers and external devices with AI assistance
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QRadioButton,
    QGroupBox, QMessageBox, QScrollArea, QWidget, QButtonGroup,
    QStackedWidget, QDialogButtonBox, QProgressDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIntValidator
import uuid
import json
import requests
import logging

logger = logging.getLogger(__name__)

# Import existing dialogs
from .add_device_dialog import AddDeviceDialog
from .add_external_device_dialog import AddExternalDeviceDialog


class DeviceDiscoveryWorker(QThread):
    """Worker thread for AI device discovery"""
    device_found = pyqtSignal(dict)
    discovery_complete = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, subnet, server_url):
        super().__init__()
        self.subnet = subnet
        self.server_url = server_url
    
    def run(self):
        """Discover devices on network"""
        try:
            # Call AI discovery API
            response = requests.post(
                f"{self.server_url}/api/ai/device/discover",
                json={"subnet": self.subnet},
                timeout=60
            )
            
            if response.status_code == 200:
                devices = response.json().get("devices", [])
                self.discovery_complete.emit(devices)
            else:
                self.error.emit(f"Discovery failed: {response.status_code}")
        except Exception as e:
            self.error.emit(f"Discovery error: {str(e)}")


class UnifiedAddDeviceDialog(QDialog):
    """
    Unified dialog for adding both FRR containers and external devices
    Provides AI-assisted device discovery and configuration
    """
    
    def __init__(self, parent=None, default_iface="", existing_devices=None):
        super().__init__(parent)
        self.setWindowTitle("Add Device")
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)

        self.default_iface = default_iface
        # Peer list for the inner FRR dialog's duplicate-loopback gate.
        # None → gate disabled (falls back to server 409). Callers that
        # want the client-side pre-fill + inline warning should pass
        # the flattened DB-key device list.
        self._existing_devices = existing_devices
        self.device_type = "frr_container"  # Default
        self.server_url = getattr(parent, 'server_url', 'http://localhost:5051') if parent else 'http://localhost:5051'
        self.discovered_devices = []
        
        # Store sub-dialogs
        self.frr_dialog = None
        self.external_dialog = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup unified UI"""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Add New Device")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Device Type Selection
        type_group = QGroupBox("Device Type")
        type_layout = QVBoxLayout()
        
        self.device_type_group = QButtonGroup()
        
        self.frr_radio = QRadioButton("🐳 FRR Container (Simulated Network Device)")
        self.frr_radio.setChecked(True)
        self.frr_radio.setToolTip("Docker-based FRR container for network simulation")
        
        self.external_radio = QRadioButton("🔌 External Device (Real Switch/Router)")
        self.external_radio.setToolTip("Physical network device (Juniper, Cisco, etc.)")
        
        self.device_type_group.addButton(self.frr_radio, 0)
        self.device_type_group.addButton(self.external_radio, 1)
        
        self.frr_radio.toggled.connect(self.on_device_type_changed)
        self.external_radio.toggled.connect(self.on_device_type_changed)
        
        type_layout.addWidget(self.frr_radio)
        type_layout.addWidget(self.external_radio)
        
        # Info labels
        frr_info = QLabel("• Simulated device in Docker container\n• Full protocol support (BGP, OSPF, IS-IS)\n• Isolated network namespace")
        frr_info.setStyleSheet("color: #666; font-size: 10pt; margin-left: 20px;")
        type_layout.addWidget(frr_info)
        
        external_info = QLabel("• Real network device on your network\n• Connect via SSH, SNMP, REST API\n• AI-assisted discovery and management")
        external_info.setStyleSheet("color: #666; font-size: 10pt; margin-left: 20px;")
        type_layout.addWidget(external_info)
        
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)
        
        # Stacked widget for different forms
        self.form_stack = QStackedWidget()
        
        # FRR Container form (reuse existing dialog's form)
        self.frr_form_widget = QWidget()
        self.frr_form_layout = QVBoxLayout(self.frr_form_widget)
        self.frr_form_layout.addWidget(QLabel("FRR Container Configuration"))
        self.frr_form_layout.addWidget(QLabel("(Use existing AddDeviceDialog form)"))
        self.frr_form_layout.addStretch()
        
        # External Device form (reuse existing dialog's form)
        self.external_form_widget = QWidget()
        self.external_form_layout = QVBoxLayout(self.external_form_widget)
        self.external_form_layout.addWidget(QLabel("External Device Configuration"))
        self.external_form_layout.addWidget(QLabel("(Use existing AddExternalDeviceDialog form)"))
        self.external_form_layout.addStretch()
        
        self.form_stack.addWidget(self.frr_form_widget)
        self.form_stack.addWidget(self.external_form_widget)
        
        layout.addWidget(self.form_stack)
        
        # AI Discovery (for external devices)
        ai_layout = QHBoxLayout()
        self.ai_discover_btn = QPushButton("🤖 AI Discover Devices")
        self.ai_discover_btn.setToolTip("Scan network and auto-discover devices")
        self.ai_discover_btn.clicked.connect(self.ai_discover_devices)
        self.ai_discover_btn.setVisible(False)  # Only for external
        self.ai_discover_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 8px 15px;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        ai_layout.addWidget(self.ai_discover_btn)
        ai_layout.addStretch()
        layout.addLayout(ai_layout)
        
        # Buttons
        button_box = QDialogButtonBox()
        self.add_btn = button_box.addButton("Add Device", QDialogButtonBox.AcceptRole)
        self.cancel_btn = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
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
        self.cancel_btn.clicked.connect(self.reject)
        
        layout.addWidget(button_box)
        
        # Initialize
        self.on_device_type_changed()
    
    def on_device_type_changed(self):
        """Handle device type selection change"""
        if self.external_radio.isChecked():
            self.device_type = "external"
            self.form_stack.setCurrentWidget(self.external_form_widget)
            self.ai_discover_btn.setVisible(True)
        else:
            self.device_type = "frr_container"
            self.form_stack.setCurrentWidget(self.frr_form_widget)
            self.ai_discover_btn.setVisible(False)
    
    def ai_discover_devices(self):
        """AI-powered device discovery"""
        # Get subnet from user
        from PyQt5.QtWidgets import QInputDialog
        
        subnet, ok = QInputDialog.getText(
            self,
            "Network Discovery",
            "Enter subnet to scan (e.g., 192.168.1.0/24):",
            text="192.168.1.0/24"
        )
        
        if not ok or not subnet:
            return
        
        # Show progress
        progress = QProgressDialog("Discovering devices...", "Cancel", 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        
        # Start discovery worker
        self.discovery_worker = DeviceDiscoveryWorker(subnet, self.server_url)
        self.discovery_worker.device_found.connect(self.on_device_discovered)
        self.discovery_worker.discovery_complete.connect(
            lambda devices: self.on_discovery_complete(devices, progress)
        )
        self.discovery_worker.error.connect(
            lambda error: self.on_discovery_error(error, progress)
        )
        self.discovery_worker.start()
    
    def on_device_discovered(self, device_info):
        """Handle discovered device"""
        self.discovered_devices.append(device_info)
    
    def on_discovery_complete(self, devices, progress):
        """Handle discovery completion"""
        progress.close()
        
        if not devices:
            QMessageBox.information(
                self,
                "Discovery Complete",
                "No devices found on the network."
            )
            return
        
        # Show discovered devices dialog
        from PyQt5.QtWidgets import QListWidget
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Discovered Devices")
        dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(dialog)
        
        layout.addWidget(QLabel(f"Found {len(devices)} device(s):"))
        
        device_list = QListWidget()
        for device in devices:
            device_text = f"{device.get('ip', 'Unknown')} - {device.get('vendor', 'Unknown')} ({device.get('type', 'Unknown')})"
            device_list.addItem(device_text)
        
        layout.addWidget(device_list)
        
        buttons = QDialogButtonBox()
        select_btn = buttons.addButton("Select", QDialogButtonBox.AcceptRole)
        cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)
        
        if dialog.exec_() == QDialog.Accepted:
            selected_item = device_list.currentItem()
            if selected_item:
                selected_index = device_list.currentRow()
                selected_device = devices[selected_index]
                self.fill_form_from_discovery(selected_device)
    
    def on_discovery_error(self, error, progress):
        """Handle discovery error"""
        progress.close()
        QMessageBox.warning(self, "Discovery Error", f"Failed to discover devices:\n{error}")
    
    def fill_form_from_discovery(self, device_info):
        """Auto-fill form from discovered device"""
        # Switch to external device type if not already
        if not self.external_radio.isChecked():
            self.external_radio.setChecked(True)
        
        # Create external dialog if not exists
        if not self.external_dialog:
            self.external_dialog = AddExternalDeviceDialog(self)
        
        # Fill fields from discovery
        if hasattr(self.external_dialog, 'host_input'):
            self.external_dialog.host_input.setText(device_info.get('ip', ''))
        
        if hasattr(self.external_dialog, 'device_type_combo'):
            vendor = device_info.get('vendor', '').lower()
            if vendor in ['juniper', 'cisco', 'arista', 'nokia']:
                index = self.external_dialog.device_type_combo.findText(vendor)
                if index >= 0:
                    self.external_dialog.device_type_combo.setCurrentIndex(index)
        
        if hasattr(self.external_dialog, 'connection_method_combo'):
            method = device_info.get('suggested_method', 'ssh')
            index = self.external_dialog.connection_method_combo.findText(method)
            if index >= 0:
                self.external_dialog.connection_method_combo.setCurrentIndex(index)
    
    def add_device(self):
        """Add device based on selected type"""
        if self.device_type == "frr_container":
            # Use existing FRR dialog
            if not self.frr_dialog:
                self.frr_dialog = AddDeviceDialog(
                    self, default_iface=self.default_iface,
                    existing_devices=self._existing_devices,
                )
            
            if self.frr_dialog.exec_() == QDialog.Accepted:
                self.device_data = {
                    "device_type": "frr_container",
                    "frr_data": self.frr_dialog.get_values()
                }
                self.accept()
        else:
            # Use existing external dialog
            if not self.external_dialog:
                self.external_dialog = AddExternalDeviceDialog(self)
            
            if self.external_dialog.exec_() == QDialog.Accepted:
                self.device_data = {
                    "device_type": "external",
                    "external_data": self.external_dialog.get_device_data()
                }
                self.accept()
    
    def get_device_data(self):
        """Get device data"""
        return self.device_data




