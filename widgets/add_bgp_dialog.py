from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, 
                             QLineEdit, QComboBox, QGroupBox, QDialogButtonBox, 
                             QWidget, QMessageBox, QCheckBox, QPushButton, QSpinBox, QLabel)
from PyQt5.QtGui import QIntValidator
from PyQt5.QtCore import Qt
import ipaddress


class AddBgpDialog(QDialog):
    def __init__(self, parent=None, device_name="", edit_mode=False, device_ipv4="", device_ipv6="", gateway_ipv4="", gateway_ipv6=""):
        super().__init__(parent)
        self.edit_mode = edit_mode
        title = f"Edit BGP Configuration - {device_name}" if edit_mode else f"Add BGP Configuration - {device_name}"
        self.setWindowTitle(title)
        self.setMinimumSize(600, 700)
        self.resize(600, 700)
        self.device_name = device_name
        self.device_ipv4 = device_ipv4
        self.device_ipv6 = device_ipv6
        self.gateway_ipv4 = gateway_ipv4
        self.gateway_ipv6 = gateway_ipv6
        
        self.layout = QVBoxLayout()
        self.setup_bgp_form()
        
        self.button_box = QDialogButtonBox()
        button_text = "Update BGP" if edit_mode else "Add BGP"
        self.ok_button = self.button_box.addButton(button_text, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.layout.addWidget(self.button_box)
        self.setLayout(self.layout)

    def setup_bgp_form(self):
        """Setup BGP configuration form."""
        form_widget = QWidget()
        layout = QVBoxLayout(form_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)

        # BGP Mode
        bgp_mode_layout = QHBoxLayout()
        bgp_mode_layout.addWidget(QLabel("BGP Mode:"))
        self.bgp_mode_combo = QComboBox()
        self.bgp_mode_combo.addItems(["eBGP", "iBGP"])
        bgp_mode_layout.addWidget(self.bgp_mode_combo)
        bgp_mode_widget = QWidget()
        bgp_mode_widget.setLayout(bgp_mode_layout)
        layout.addWidget(bgp_mode_widget)

        # ASN Configuration
        local_asn_layout = QHBoxLayout()
        local_asn_layout.addWidget(QLabel("Local ASN:"))
        self.bgp_asn_input = QLineEdit("65000")
        self.bgp_asn_input.setValidator(QIntValidator(1, 2147483647))  # Max 32-bit signed int
        local_asn_layout.addWidget(self.bgp_asn_input)
        local_asn_widget = QWidget()
        local_asn_widget.setLayout(local_asn_layout)
        layout.addWidget(local_asn_widget)
        
        remote_asn_layout = QHBoxLayout()
        remote_asn_layout.addWidget(QLabel("Remote ASN:"))
        self.bgp_remote_asn_input = QLineEdit("65001")
        self.bgp_remote_asn_input.setValidator(QIntValidator(1, 2147483647))  # Max 32-bit signed int
        remote_asn_layout.addWidget(self.bgp_remote_asn_input)
        remote_asn_widget = QWidget()
        remote_asn_widget.setLayout(remote_asn_layout)
        layout.addWidget(remote_asn_widget)

        # Protocol selection checkboxes
        protocol_layout = QHBoxLayout()
        protocol_layout.addWidget(QLabel("Protocols:"))
        self.ipv4_enabled = QCheckBox("Enable IPv4 BGP")
        self.ipv6_enabled = QCheckBox("Enable IPv6 BGP")
        self.ipv4_enabled.setChecked(True)  # Default to IPv4 enabled
        protocol_layout.addWidget(self.ipv4_enabled)
        protocol_layout.addWidget(self.ipv6_enabled)
        protocol_layout.addStretch()
        protocol_widget = QWidget()
        protocol_widget.setLayout(protocol_layout)
        layout.addWidget(protocol_widget)

        # BGP Timer Configuration
        self.timer_group = QGroupBox("BGP Timer Configuration")
        self.timer_layout = QFormLayout(self.timer_group)
        self.timer_layout.setLabelAlignment(Qt.AlignRight)
        self.timer_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # Keepalive timer
        self.bgp_keepalive_input = QSpinBox()
        self.bgp_keepalive_input.setMinimum(1)
        self.bgp_keepalive_input.setMaximum(65535)
        self.bgp_keepalive_input.setValue(30)  # Default 30 seconds
        self.bgp_keepalive_input.setSuffix(" seconds")
        self.timer_layout.addRow("Keepalive Timer:", self.bgp_keepalive_input)
        
        # Hold-time timer
        self.bgp_hold_time_input = QSpinBox()
        self.bgp_hold_time_input.setMinimum(3)
        self.bgp_hold_time_input.setMaximum(65535)
        self.bgp_hold_time_input.setValue(90)  # Default 90 seconds
        self.bgp_hold_time_input.setSuffix(" seconds")
        self.timer_layout.addRow("Hold-time Timer:", self.bgp_hold_time_input)

        # IPv4 BGP Configuration
        self.ipv4_bgp_group = QGroupBox("IPv4 BGP Configuration")
        self.ipv4_bgp_layout = QFormLayout(self.ipv4_bgp_group)
        self.ipv4_bgp_layout.setLabelAlignment(Qt.AlignRight)
        self.ipv4_bgp_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # IPv4 Neighbor IP (should be gateway, not device IP)
        neighbor_ipv4_default = self.gateway_ipv4 if self.gateway_ipv4 else "192.168.0.1"
        self.bgp_neighbor_ipv4_input = QLineEdit(neighbor_ipv4_default)
        self.ipv4_bgp_layout.addRow("Neighbor IPv4:", self.bgp_neighbor_ipv4_input)
        
        # IPv4 Increment Controls
        ipv4_increment_layout = QHBoxLayout()
        self.ipv4_increment_checkbox = QCheckBox("Increment IPv4")
        self.ipv4_octet_combo = QComboBox()
        self.ipv4_octet_combo.addItems(["Last Octet", "3rd Octet", "2nd Octet", "1st Octet"])
        self.ipv4_increment_count = QSpinBox()
        self.ipv4_increment_count.setMinimum(1)
        self.ipv4_increment_count.setMaximum(100)
        self.ipv4_increment_count.setValue(1)
        self.ipv4_increment_count.setEnabled(False)
        
        ipv4_increment_layout.addWidget(self.ipv4_increment_checkbox)
        ipv4_increment_layout.addWidget(QLabel("Octet:"))
        ipv4_increment_layout.addWidget(self.ipv4_octet_combo)
        ipv4_increment_layout.addWidget(QLabel("Count:"))
        ipv4_increment_layout.addWidget(self.ipv4_increment_count)
        ipv4_increment_layout.addStretch()
        
        self.ipv4_bgp_layout.addRow("Increment:", ipv4_increment_layout)
        
        # IPv4 Source IP
        source_ipv4_default = self.device_ipv4 if self.device_ipv4 else "192.168.0.2"
        self.bgp_update_source_ipv4_input = QLineEdit(source_ipv4_default)
        self.ipv4_bgp_layout.addRow("Source IPv4:", self.bgp_update_source_ipv4_input)
        
        # Connect IPv4 increment checkbox
        self.ipv4_increment_checkbox.toggled.connect(self.toggle_ipv4_increment)

        # IPv6 BGP Configuration
        self.ipv6_bgp_group = QGroupBox("IPv6 BGP Configuration")
        self.ipv6_bgp_layout = QFormLayout(self.ipv6_bgp_group)
        self.ipv6_bgp_layout.setLabelAlignment(Qt.AlignRight)
        self.ipv6_bgp_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # IPv6 Neighbor IP (should be gateway, not device IP)
        neighbor_ipv6_default = self.gateway_ipv6 if self.gateway_ipv6 else "2001:db8::1"
        self.bgp_neighbor_ipv6_input = QLineEdit(neighbor_ipv6_default)
        self.ipv6_bgp_layout.addRow("Neighbor IPv6:", self.bgp_neighbor_ipv6_input)
        
        # IPv6 Increment Controls
        ipv6_increment_layout = QHBoxLayout()
        self.ipv6_increment_checkbox = QCheckBox("Increment IPv6")
        self.ipv6_segment_combo = QComboBox()
        self.ipv6_segment_combo.addItems(["Last Segment", "4th Segment", "3rd Segment", "2nd Segment"])
        self.ipv6_increment_count = QSpinBox()
        self.ipv6_increment_count.setMinimum(1)
        self.ipv6_increment_count.setMaximum(100)
        self.ipv6_increment_count.setValue(1)
        self.ipv6_increment_count.setEnabled(False)
        
        ipv6_increment_layout.addWidget(self.ipv6_increment_checkbox)
        ipv6_increment_layout.addWidget(QLabel("Segment:"))
        ipv6_increment_layout.addWidget(self.ipv6_segment_combo)
        ipv6_increment_layout.addWidget(QLabel("Count:"))
        ipv6_increment_layout.addWidget(self.ipv6_increment_count)
        ipv6_increment_layout.addStretch()
        
        self.ipv6_bgp_layout.addRow("Increment:", ipv6_increment_layout)
        
        # IPv6 Source IP
        source_ipv6_default = self.device_ipv6 if self.device_ipv6 else "2001:db8::2"
        self.bgp_update_source_ipv6_input = QLineEdit(source_ipv6_default)
        self.ipv6_bgp_layout.addRow("Source IPv6:", self.bgp_update_source_ipv6_input)
        
        # Connect IPv6 increment checkbox
        self.ipv6_increment_checkbox.toggled.connect(self.toggle_ipv6_increment)

        layout.addWidget(self.timer_group)
        layout.addWidget(self.ipv4_bgp_group)
        layout.addWidget(self.ipv6_bgp_group)

        # Connect checkbox signals to enable/disable sections
        self.ipv4_enabled.toggled.connect(self.toggle_ipv4_section)
        self.ipv6_enabled.toggled.connect(self.toggle_ipv6_section)
        
        self.layout.addWidget(form_widget)

    def toggle_ipv4_section(self, enabled):
        """Enable/disable IPv4 BGP configuration section."""
        self.ipv4_bgp_group.setEnabled(enabled)
        if not enabled:
            # Clear IPv4 fields when disabled
            self.bgp_neighbor_ipv4_input.clear()
            self.bgp_update_source_ipv4_input.clear()

    def toggle_ipv6_section(self, enabled):
        """Enable/disable IPv6 BGP configuration section."""
        self.ipv6_bgp_group.setEnabled(enabled)
        if not enabled:
            # Clear IPv6 fields when disabled
            self.bgp_neighbor_ipv6_input.clear()
            self.bgp_update_source_ipv6_input.clear()

    def toggle_ipv4_increment(self, enabled):
        """Enable/disable IPv4 increment controls."""
        self.ipv4_octet_combo.setEnabled(enabled)
        self.ipv4_increment_count.setEnabled(enabled)

    def toggle_ipv6_increment(self, enabled):
        """Enable/disable IPv6 increment controls."""
        self.ipv6_segment_combo.setEnabled(enabled)
        self.ipv6_increment_count.setEnabled(enabled)

    def get_values(self):
        """Get BGP configuration values."""
        config = {
            "bgp_mode": self.bgp_mode_combo.currentText(),
            "bgp_asn": self.bgp_asn_input.text().strip(),
            "bgp_remote_asn": self.bgp_remote_asn_input.text().strip(),
            "bgp_keepalive": str(self.bgp_keepalive_input.value()),
            "bgp_hold_time": str(self.bgp_hold_time_input.value()),
            "ipv4_enabled": self.ipv4_enabled.isChecked(),
            "ipv6_enabled": self.ipv6_enabled.isChecked()
        }
        
        # Only include IPv4 configuration if enabled
        if self.ipv4_enabled.isChecked():
            # Generate IPv4 neighbor IPs based on increment settings
            if self.ipv4_increment_checkbox.isChecked():
                ipv4_neighbors = self._generate_ipv4_neighbors()
                config["bgp_neighbor_ipv4"] = ",".join(ipv4_neighbors) if ipv4_neighbors else ""
            else:
                config["bgp_neighbor_ipv4"] = self.bgp_neighbor_ipv4_input.text().strip()
            config["bgp_update_source_ipv4"] = self.bgp_update_source_ipv4_input.text().strip()
        else:
            config["bgp_neighbor_ipv4"] = ""
            config["bgp_update_source_ipv4"] = ""
        
        # Only include IPv6 configuration if enabled
        if self.ipv6_enabled.isChecked():
            # Generate IPv6 neighbor IPs based on increment settings
            if self.ipv6_increment_checkbox.isChecked():
                ipv6_neighbors = self._generate_ipv6_neighbors()
                config["bgp_neighbor_ipv6"] = ",".join(ipv6_neighbors) if ipv6_neighbors else ""
            else:
                config["bgp_neighbor_ipv6"] = self.bgp_neighbor_ipv6_input.text().strip()
            config["bgp_update_source_ipv6"] = self.bgp_update_source_ipv6_input.text().strip()
        else:
            config["bgp_neighbor_ipv6"] = ""
            config["bgp_update_source_ipv6"] = ""
        
        return config

    def accept(self):
        """Validate and accept the dialog."""
        if not self._validate():
            return
        super().accept()

    def _validate(self):
        """Validate BGP configuration."""
        # Check if at least one protocol is enabled
        if not self.ipv4_enabled.isChecked() and not self.ipv6_enabled.isChecked():
            QMessageBox.warning(self, "No Protocol Selected", "Please enable at least one BGP protocol (IPv4 or IPv6).")
            return False

        try:
            # Validate ASNs
            asn_local = int(self.bgp_asn_input.text())
            asn_remote = int(self.bgp_remote_asn_input.text())
            if asn_local <= 0 or asn_remote <= 0:
                raise ValueError("ASN must be positive")
        except Exception:
            QMessageBox.warning(self, "Invalid BGP ASN", "Local and Remote ASN must be positive integers.")
            return False

        # Validate IPv4 BGP fields if enabled
        if self.ipv4_enabled.isChecked():
            neigh_ipv4 = self.bgp_neighbor_ipv4_input.text().strip()
            if not neigh_ipv4:
                QMessageBox.warning(self, "Missing IPv4 Neighbor IP", "Please provide an IPv4 neighbor address when IPv4 BGP is enabled.")
                return False
            
            try:
                ipaddress.IPv4Address(neigh_ipv4)
            except Exception:
                QMessageBox.warning(self, "Invalid IPv4 Neighbor IP", "Please provide a valid IPv4 neighbor address.")
                return False

            src_ipv4 = self.bgp_update_source_ipv4_input.text().strip()
            if src_ipv4:  # Source IP is optional
                try:
                    ipaddress.IPv4Address(src_ipv4)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv4 Source IP", "IPv4 BGP Source IP must be a valid IPv4 address.")
                    return False

        # Validate IPv6 BGP fields if enabled
        if self.ipv6_enabled.isChecked():
            neigh_ipv6 = self.bgp_neighbor_ipv6_input.text().strip()
            if not neigh_ipv6:
                QMessageBox.warning(self, "Missing IPv6 Neighbor IP", "Please provide an IPv6 neighbor address when IPv6 BGP is enabled.")
                return False
            
            try:
                ipaddress.IPv6Address(neigh_ipv6)
            except Exception:
                QMessageBox.warning(self, "Invalid IPv6 Neighbor IP", "Please provide a valid IPv6 neighbor address.")
                return False

            src_ipv6 = self.bgp_update_source_ipv6_input.text().strip()
            if src_ipv6:  # Source IP is optional
                try:
                    ipaddress.IPv6Address(src_ipv6)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv6 Source IP", "IPv6 BGP Source IP must be a valid IPv6 address.")
                    return False

        return True

    def _generate_ipv4_neighbors(self):
        """Generate IPv4 neighbor IPs based on increment settings."""
        base_ip = self.bgp_neighbor_ipv4_input.text().strip()
        if not base_ip:
            base_ip = "192.168.0.2"
        
        # Validate the base IPv4 address first
        try:
            ipaddress.IPv4Address(base_ip)
        except ipaddress.AddressValueError:
            print(f"Warning: Invalid base IPv4 address '{base_ip}', using default '192.168.0.2'")
            base_ip = "192.168.0.2"
        
        count = self.ipv4_increment_count.value()
        octet_choice = self.ipv4_octet_combo.currentText()
        
        # Determine which octet to increment
        octet_index = {"1st Octet": 0, "2nd Octet": 1, "3rd Octet": 2, "Last Octet": 3}[octet_choice]
        
        try:
            # Use ipaddress module for proper parsing
            ipv4_obj = ipaddress.IPv4Address(base_ip)
            ip_parts = str(ipv4_obj).split(".")
            
            neighbors = []
            for i in range(count):
                # Create a copy of the IP parts
                current_parts = ip_parts.copy()
                
                # Increment the specified octet
                current_octet = int(current_parts[octet_index])
                new_octet = current_octet + i
                
                # Handle overflow (reset to 1 if it goes beyond 254)
                if new_octet > 254:
                    new_octet = (new_octet - 1) % 254 + 1
                
                current_parts[octet_index] = str(new_octet)
                new_ip = ".".join(current_parts)
                
                # Validate the generated IP
                try:
                    ipaddress.IPv4Address(new_ip)
                    neighbors.append(new_ip)
                except ipaddress.AddressValueError:
                    print(f"Warning: Generated invalid IPv4 address '{new_ip}', skipping")
                    continue
            
            return neighbors
        except (ValueError, IndexError, KeyError) as e:
            print(f"Error generating IPv4 neighbors: {e}")
            return [base_ip]

    def _generate_ipv6_neighbors(self):
        """Generate IPv6 neighbor IPs based on increment settings."""
        base_ip = self.bgp_neighbor_ipv6_input.text().strip()
        if not base_ip:
            base_ip = "2001:db8::2"
        
        # Validate the base IPv6 address first
        try:
            ipaddress.IPv6Address(base_ip)
        except ipaddress.AddressValueError:
            print(f"Warning: Invalid base IPv6 address '{base_ip}', using default '2001:db8::2'")
            base_ip = "2001:db8::2"
        
        count = self.ipv6_increment_count.value()
        segment_choice = self.ipv6_segment_combo.currentText()
        
        try:
            # Parse IPv6 address - use ipaddress module for proper parsing
            ipv6_obj = ipaddress.IPv6Address(base_ip)
            # Convert to full format (8 segments, no compression)
            full_ipv6 = ipv6_obj.exploded
            segments = full_ipv6.split(":")
            
            # Determine which segment to increment
            segment_index = {"2nd Segment": 1, "3rd Segment": 2, "4th Segment": 3, "Last Segment": -1}[segment_choice]
            
            neighbors = []
            for i in range(count):
                # Create a copy of the segments
                current_segments = segments.copy()
                
                # Increment the specified segment
                current_segment = int(current_segments[segment_index], 16)
                new_segment = current_segment + i
                
                # Handle overflow (reset to 1 if it goes beyond FFFF)
                if new_segment > 0xFFFF:
                    new_segment = (new_segment - 1) % 0xFFFF + 1
                
                current_segments[segment_index] = f"{new_segment:04x}"
                
                # Reconstruct IPv6 address and compress it
                ipv6_full = ":".join(current_segments)
                try:
                    # Use ipaddress module to properly compress the address
                    ipv6_obj = ipaddress.IPv6Address(ipv6_full)
                    ipv6_compressed = ipv6_obj.compressed
                    neighbors.append(ipv6_compressed)
                except ipaddress.AddressValueError:
                    # Fallback to full format if compression fails
                    neighbors.append(ipv6_full)
            
            return neighbors
        except (ValueError, IndexError, KeyError) as e:
            print(f"Error generating IPv6 neighbors: {e}")
            return [base_ip]
