from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, 
                             QLineEdit, QComboBox, QGroupBox, QDialogButtonBox, 
                             QWidget, QMessageBox)
from PyQt5.QtGui import QIntValidator
import re


class AddIsisDialog(QDialog):
    def __init__(self, parent=None, device_name="", edit_mode=False, isis_config=None):
        super().__init__(parent)
        self.edit_mode = edit_mode
        self.isis_config = isis_config or {}
        title = f"Edit IS-IS Configuration - {device_name}" if edit_mode else f"Add IS-IS Configuration - {device_name}"
        self.setWindowTitle(title)
        self.setFixedSize(400, 350)
        self.device_name = device_name
        
        self.layout = QVBoxLayout()
        self.setup_isis_form()
        
        self.button_box = QDialogButtonBox()
        button_text = "Update IS-IS" if edit_mode else "Add IS-IS"
        self.ok_button = self.button_box.addButton(button_text, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_isis_form(self):
        """Setup IS-IS configuration form."""
        form_widget = QWidget()
        layout = QFormLayout(form_widget)

        # Area ID (Network Entity Title)
        area_id_default = self.isis_config.get("area_id", "49.0001.0000.0000.0001.00")
        self.area_id_input = QLineEdit(area_id_default)
        self.area_id_input.setPlaceholderText("e.g., 49.0001.0000.0000.0001.00")
        layout.addRow("Area ID (NET):", self.area_id_input)

        # System ID
        system_id_default = self.isis_config.get("system_id", "0000.0000.0001")
        self.system_id_input = QLineEdit(system_id_default)
        self.system_id_input.setPlaceholderText("e.g., 0000.0000.0001")
        layout.addRow("System ID:", self.system_id_input)

        # Level
        self.level_combo = QComboBox()
        self.level_combo.addItems(["Level-1", "Level-2", "Level-1-2"])
        level_default = self.isis_config.get("level", "Level-2")
        if level_default in ["Level-1", "Level-2", "Level-1-2"]:
            self.level_combo.setCurrentText(level_default)
        layout.addRow("Level:", self.level_combo)

        # Additional IS-IS options
        options_group = QGroupBox("Additional Options")
        options_layout = QFormLayout(options_group)
        
        # Hello interval
        hello_interval_default = self.isis_config.get("hello_interval", "10")
        self.hello_interval_input = QLineEdit(hello_interval_default)
        self.hello_interval_input.setValidator(QIntValidator(1, 65535))
        self.hello_interval_input.setPlaceholderText("seconds")
        options_layout.addRow("Hello Interval:", self.hello_interval_input)
        
        # Hello multiplier
        hello_multiplier_default = self.isis_config.get("hello_multiplier", "3")
        self.hello_multiplier_input = QLineEdit(hello_multiplier_default)
        self.hello_multiplier_input.setValidator(QIntValidator(1, 100))
        options_layout.addRow("Hello Multiplier:", self.hello_multiplier_input)
        
        # Metric
        metric_default = self.isis_config.get("metric", "10")
        self.metric_input = QLineEdit(metric_default)
        self.metric_input.setValidator(QIntValidator(1, 16777215))
        options_layout.addRow("Interface Metric:", self.metric_input)
        
        # Interface (read-only, based on device VLAN)
        interface_default = self.isis_config.get("interface", self._get_device_interface())
        self.interface_input = QLineEdit(interface_default)
        self.interface_input.setReadOnly(True)
        self.interface_input.setStyleSheet("background-color: #f0f0f0;")
        options_layout.addRow("Interface:", self.interface_input)
        
        layout.addRow(options_group)
        
        self.layout.addWidget(form_widget)

    def get_values(self):
        """Get IS-IS configuration values."""
        return {
            "area_id": self.area_id_input.text().strip(),
            "system_id": self.system_id_input.text().strip(),
            "level": self.level_combo.currentText(),
            "hello_interval": self.hello_interval_input.text().strip(),
            "hello_multiplier": self.hello_multiplier_input.text().strip(),
            "metric": self.metric_input.text().strip(),
            "interface": self.interface_input.text().strip()
        }

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate IS-IS configuration."""
        # Validate Area ID (NET) format
        area_id = self.area_id_input.text().strip()
        if area_id:
            # NET format: XX.XXXX.XXXX.XXXX.XXXX.XX (13 octets)
            if not re.match(r'^[0-9A-Fa-f]{2}(\.[0-9A-Fa-f]{4}){4}\.[0-9A-Fa-f]{2}$', area_id):
                QMessageBox.warning(self, "Invalid Area ID", "Area ID (NET) must be in format XX.XXXX.XXXX.XXXX.XXXX.XX")
                return False

        # Validate System ID format
        system_id = self.system_id_input.text().strip()
        if system_id:
            # System ID format: XXXX.XXXX.XXXX (6 octets)
            if not re.match(r'^[0-9A-Fa-f]{4}(\.[0-9A-Fa-f]{4}){2}$', system_id):
                QMessageBox.warning(self, "Invalid System ID", "System ID must be in format XXXX.XXXX.XXXX")
                return False

        # Validate numeric fields
        try:
            hello_interval = int(self.hello_interval_input.text() or "10")
            hello_multiplier = int(self.hello_multiplier_input.text() or "3")
            metric = int(self.metric_input.text() or "10")
            
            if hello_interval <= 0 or hello_multiplier <= 0 or metric <= 0:
                raise ValueError("Values must be positive")
        except Exception as e:
            QMessageBox.warning(self, "Invalid Values", f"Invalid numeric values: {e}")
            return False

        return True
    
    def _get_device_interface(self):
        """Get the device interface based on device data."""
        # Try to get the interface from the current ISIS config first
        if self.isis_config and "interface" in self.isis_config:
            return self.isis_config["interface"]
        
        # If not available, return empty string (will be set by the calling code)
        return ""
