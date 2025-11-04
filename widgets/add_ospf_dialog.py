from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, 
                             QLineEdit, QCheckBox, QGroupBox, QDialogButtonBox, 
                             QWidget, QMessageBox)
import ipaddress


class AddOspfDialog(QDialog):
    def __init__(self, parent=None, device_name=""):
        super().__init__(parent)
        self.setWindowTitle(f"Add OSPF Configuration - {device_name}")
        self.setFixedSize(400, 300)
        self.device_name = device_name
        
        self.layout = QVBoxLayout()
        self.setup_ospf_form()
        
        self.button_box = QDialogButtonBox()
        self.ok_button = self.button_box.addButton("Add OSPF", QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_ospf_form(self):
        """Setup OSPF configuration form."""
        form_widget = QWidget()
        layout = QFormLayout(form_widget)

        # Area ID
        self.area_id_input = QLineEdit("0.0.0.0")
        self.area_id_input.setPlaceholderText("e.g., 0.0.0.0")
        layout.addRow("Area ID:", self.area_id_input)

        # Graceful Restart
        self.graceful_restart_checkbox = QCheckBox("Enable Graceful Restart")
        layout.addRow("Graceful Restart:", self.graceful_restart_checkbox)

        # Additional OSPF options can be added here
        options_group = QGroupBox("Additional Options")
        options_layout = QFormLayout(options_group)
        
        # Router ID (optional)
        self.router_id_input = QLineEdit()
        self.router_id_input.setPlaceholderText("Auto-assigned if empty")
        options_layout.addRow("Router ID:", self.router_id_input)
        
        # Hello interval
        self.hello_interval_input = QLineEdit("10")
        self.hello_interval_input.setPlaceholderText("seconds")
        options_layout.addRow("Hello Interval:", self.hello_interval_input)
        
        # Dead interval
        self.dead_interval_input = QLineEdit("40")
        self.dead_interval_input.setPlaceholderText("seconds")
        options_layout.addRow("Dead Interval:", self.dead_interval_input)
        
        layout.addRow(options_group)
        
        self.layout.addWidget(form_widget)

    def get_values(self):
        """Get OSPF configuration values."""
        return {
            "area_id": self.area_id_input.text().strip(),
            "graceful_restart": self.graceful_restart_checkbox.isChecked(),
            "router_id": self.router_id_input.text().strip(),
            "hello_interval": self.hello_interval_input.text().strip(),
            "dead_interval": self.dead_interval_input.text().strip()
        }

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate OSPF configuration."""
        # Validate Area ID format
        area_id = self.area_id_input.text().strip()
        if area_id:
            try:
                # Check if it's a valid IP address format
                ipaddress.IPv4Address(area_id)
            except Exception:
                try:
                    # Check if it's a decimal number
                    area_num = int(area_id)
                    if area_num < 0 or area_num > 4294967295:
                        raise ValueError("Area ID out of range")
                except Exception:
                    QMessageBox.warning(self, "Invalid Area ID", "Area ID must be a valid IPv4 address or decimal number (0-4294967295).")
                    return False

        # Validate Router ID if provided
        router_id = self.router_id_input.text().strip()
        if router_id:
            try:
                ipaddress.IPv4Address(router_id)
            except Exception:
                QMessageBox.warning(self, "Invalid Router ID", "Router ID must be a valid IPv4 address.")
                return False

        # Validate intervals
        try:
            hello_interval = int(self.hello_interval_input.text() or "10")
            dead_interval = int(self.dead_interval_input.text() or "40")
            if hello_interval <= 0 or dead_interval <= 0:
                raise ValueError("Intervals must be positive")
            if dead_interval <= hello_interval:
                raise ValueError("Dead interval must be greater than hello interval")
        except Exception as e:
            QMessageBox.warning(self, "Invalid Intervals", f"Invalid interval values: {e}")
            return False

        return True
