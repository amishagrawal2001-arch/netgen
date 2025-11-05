from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, 
                             QLineEdit, QCheckBox, QGroupBox, QDialogButtonBox, 
                             QWidget, QMessageBox)
import ipaddress


class AddOspfDialog(QDialog):
    def __init__(self, parent=None, device_name="", edit_mode=False, ospf_config=None):
        super().__init__(parent)
        self.edit_mode = edit_mode
        self.ospf_config = ospf_config or {}
        title = f"Edit OSPF Configuration - {device_name}" if edit_mode else f"Add OSPF Configuration - {device_name}"
        self.setWindowTitle(title)
        self.setFixedSize(400, 350)
        self.device_name = device_name
        
        self.layout = QVBoxLayout()
        self.setup_ospf_form()
        
        self.button_box = QDialogButtonBox()
        button_text = "Update OSPF" if edit_mode else "Add OSPF"
        self.ok_button = self.button_box.addButton(button_text, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_ospf_form(self):
        """Setup OSPF configuration form."""
        form_widget = QWidget()
        layout = QFormLayout(form_widget)

        # IPv4 Area ID - pre-populate from config if editing
        area_id_default = self.ospf_config.get("area_id", "0.0.0.0")  # Legacy support
        ipv4_area_id_default = self.ospf_config.get("ipv4_area_id", area_id_default)
        self.ipv4_area_id_input = QLineEdit(ipv4_area_id_default)
        self.ipv4_area_id_input.setPlaceholderText("e.g., 0.0.0.0")
        layout.addRow("IPv4 Area ID:", self.ipv4_area_id_input)
        
        # IPv6 Area ID - pre-populate from config if editing
        ipv6_area_id_default = self.ospf_config.get("ipv6_area_id", area_id_default)
        self.ipv6_area_id_input = QLineEdit(ipv6_area_id_default)
        self.ipv6_area_id_input.setPlaceholderText("e.g., 0.0.0.0")
        layout.addRow("IPv6 Area ID:", self.ipv6_area_id_input)

        # Graceful Restart - pre-populate from config if editing
        self.graceful_restart_checkbox = QCheckBox("Enable Graceful Restart")
        graceful_restart_default = self.ospf_config.get("graceful_restart", False)
        self.graceful_restart_checkbox.setChecked(graceful_restart_default)
        layout.addRow("Graceful Restart:", self.graceful_restart_checkbox)

        # Additional OSPF options can be added here
        options_group = QGroupBox("Additional Options")
        options_layout = QFormLayout(options_group)
        
        # Router ID (optional) - pre-populate from config if editing
        router_id_default = self.ospf_config.get("router_id", "")
        self.router_id_input = QLineEdit(router_id_default)
        self.router_id_input.setPlaceholderText("Auto-assigned if empty")
        options_layout.addRow("Router ID:", self.router_id_input)
        
        # Hello interval - pre-populate from config if editing
        hello_interval_default = self.ospf_config.get("hello_interval", "10")
        self.hello_interval_input = QLineEdit(hello_interval_default)
        self.hello_interval_input.setPlaceholderText("seconds")
        options_layout.addRow("Hello Interval:", self.hello_interval_input)
        
        # Dead interval - pre-populate from config if editing
        dead_interval_default = self.ospf_config.get("dead_interval", "40")
        self.dead_interval_input = QLineEdit(dead_interval_default)
        self.dead_interval_input.setPlaceholderText("seconds")
        options_layout.addRow("Dead Interval:", self.dead_interval_input)
        
        layout.addRow(options_group)
        
        self.layout.addWidget(form_widget)

    def get_values(self):
        """Get OSPF configuration values."""
        ipv4_area_id = self.ipv4_area_id_input.text().strip()
        ipv6_area_id = self.ipv6_area_id_input.text().strip()
        # If both are the same, also set legacy area_id for backward compatibility
        area_id = ipv4_area_id if ipv4_area_id == ipv6_area_id else None
        result = {
            "ipv4_area_id": ipv4_area_id,
            "ipv6_area_id": ipv6_area_id,
            "graceful_restart": self.graceful_restart_checkbox.isChecked(),
            "router_id": self.router_id_input.text().strip(),
            "hello_interval": self.hello_interval_input.text().strip(),
            "dead_interval": self.dead_interval_input.text().strip()
        }
        # Add legacy area_id if both are the same (for backward compatibility)
        if area_id:
            result["area_id"] = area_id
        return result

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate OSPF configuration."""
        # Validate IPv4 Area ID format
        ipv4_area_id = self.ipv4_area_id_input.text().strip()
        if ipv4_area_id:
            try:
                # Check if it's a valid IP address format
                ipaddress.IPv4Address(ipv4_area_id)
            except Exception:
                try:
                    # Check if it's a decimal number
                    area_num = int(ipv4_area_id)
                    if area_num < 0 or area_num > 4294967295:
                        raise ValueError("Area ID out of range")
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv4 Area ID", "IPv4 Area ID must be a valid IPv4 address or decimal number (0-4294967295).")
                    return False
        
        # Validate IPv6 Area ID format
        ipv6_area_id = self.ipv6_area_id_input.text().strip()
        if ipv6_area_id:
            try:
                # Check if it's a valid IP address format
                ipaddress.IPv4Address(ipv6_area_id)
            except Exception:
                try:
                    # Check if it's a decimal number
                    area_num = int(ipv6_area_id)
                    if area_num < 0 or area_num > 4294967295:
                        raise ValueError("Area ID out of range")
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv6 Area ID", "IPv6 Area ID must be a valid IPv4 address or decimal number (0-4294967295).")
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
