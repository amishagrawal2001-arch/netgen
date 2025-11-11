from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, 
                             QLineEdit, QCheckBox, QSpinBox, QGroupBox, 
                             QDialogButtonBox, QWidget, QMessageBox, QLabel, QComboBox, QScrollArea)
from PyQt5.QtGui import QIntValidator, QRegExpValidator
from PyQt5.QtCore import QRegExp, Qt
import uuid
import ipaddress


class AddDeviceDialog(QDialog):
    def __init__(self, parent=None, default_iface=""):
        super().__init__(parent)
        self.setWindowTitle("Add New Device")
        self.setMinimumSize(1000, 700)  # Further increased width for better field spacing
        self.resize(1000, 700)  # Set initial size with wider width

        self.default_iface = default_iface
        
        # Main layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Create scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create scroll content widget
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setSpacing(10)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        
        # Setup form content
        self.setup_basic_device_form()
        
        # Set scroll content
        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)
        
        # Add button box
        self.button_box = QDialogButtonBox()
        self.ok_button = self.button_box.addButton("Add Device", QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        
        self.ok_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)
        
        self.main_layout.addWidget(self.button_box)
        self.setLayout(self.main_layout)

    def setup_basic_device_form(self):
        """Setup a well-organized form for basic device information."""
        # Interface Configuration Group
        interface_group = QGroupBox("Interface Configuration")
        interface_layout = QFormLayout(interface_group)
        interface_layout.setSpacing(10)

        # Device name
        self.device_name_input = QLineEdit()
        self.device_name_input.setPlaceholderText("Optional - leave empty for device1, device2, etc.")
        interface_layout.addRow("Device Name:", self.device_name_input)

        # Interface, VLAN-ID, and MAC Address in one row
        interface_vlan_mac_layout = QHBoxLayout()
        
        # Interface field
        self.iface_input = QLineEdit()
        self.iface_input.setText(self.default_iface)
        self.iface_input.setPlaceholderText("TG X - Port: <iface>")
        self.iface_input.setMinimumWidth(150)
        
        # VLAN ID field
        self.vlan_input = QLineEdit("0")
        self.vlan_input.setPlaceholderText("VLAN ID")
        self.vlan_input.setValidator(QIntValidator(0, 4094, self))
        self.vlan_input.setMinimumWidth(80)
        
        # MAC Address field
        self.mac_input = QLineEdit("00:11:22:33:44:55")
        self.mac_input.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        mac_re = QRegExp(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
        self.mac_input.setValidator(QRegExpValidator(mac_re, self))
        self.mac_input.setMinimumWidth(150)
        
        # Add fields to horizontal layout
        interface_vlan_mac_layout.addWidget(QLabel("Interface:"))
        interface_vlan_mac_layout.addWidget(self.iface_input)
        interface_vlan_mac_layout.addWidget(QLabel("VLAN-ID:"))
        interface_vlan_mac_layout.addWidget(self.vlan_input)
        interface_vlan_mac_layout.addWidget(QLabel("MAC Address:"))
        interface_vlan_mac_layout.addWidget(self.mac_input)
        interface_vlan_mac_layout.addStretch()
        
        interface_layout.addRow("", interface_vlan_mac_layout)

        self.scroll_layout.addWidget(interface_group)

        # IP Configuration Group
        ip_group = QGroupBox("IP Configuration")
        ip_layout = QFormLayout(ip_group)
        ip_layout.setSpacing(10)

        # IP Version selection
        self.ipv4_checkbox = QCheckBox("IPv4")
        self.ipv6_checkbox = QCheckBox("IPv6")
        self.ipv4_checkbox.setChecked(True)
        self.ipv4_checkbox.toggled.connect(self._toggle_ip_fields)
        self.ipv6_checkbox.toggled.connect(self._toggle_ip_fields)
        
        ip_version_layout = QHBoxLayout()
        ip_version_layout.addWidget(self.ipv4_checkbox)
        ip_version_layout.addWidget(self.ipv6_checkbox)
        ip_version_layout.addStretch()
        ip_layout.addRow("IP Version:", ip_version_layout)

        # IPv4 fields in one row with proper sizing
        ipv4_layout = QHBoxLayout()
        self.ipv4_input = QLineEdit("192.168.0.2")
        self.ipv4_input.setPlaceholderText("IPv4 Address")
        self.ipv4_input.setMinimumWidth(120)  # Size for IPv4 format (xxx.xxx.xxx.xxx)
        self.ipv4_input.setMaximumWidth(150)
        
        self.ipv4_mask_input = QLineEdit("24")
        self.ipv4_mask_input.setValidator(QIntValidator(0, 32, self))
        self.ipv4_mask_input.setPlaceholderText("Mask")
        self.ipv4_mask_input.setMinimumWidth(40)  # Size for mask (0-32)
        self.ipv4_mask_input.setMaximumWidth(60)
        
        self.ipv4_gateway_input = QLineEdit("192.168.0.1")
        self.ipv4_gateway_input.setPlaceholderText("Gateway")
        self.ipv4_gateway_input.setEnabled(True)
        self.ipv4_gateway_input.setMinimumWidth(120)  # Size for IPv4 format
        self.ipv4_gateway_input.setMaximumWidth(150)
        
        ipv4_layout.addWidget(QLabel("IPv4 Address:"))
        ipv4_layout.addWidget(self.ipv4_input)
        ipv4_layout.addWidget(QLabel("IPv4 Mask:"))
        ipv4_layout.addWidget(self.ipv4_mask_input)
        ipv4_layout.addWidget(QLabel("IPv4 Gateway:"))
        ipv4_layout.addWidget(self.ipv4_gateway_input)
        ipv4_layout.addStretch()  # Add stretch to align with IP Version
        ip_layout.addRow("", ipv4_layout)

        # IPv6 fields in one row with proper sizing
        ipv6_layout = QHBoxLayout()
        self.ipv6_input = QLineEdit("2001:db8::2")
        self.ipv6_input.setPlaceholderText("IPv6 Address")
        self.ipv6_input.setEnabled(False)
        self.ipv6_input.setMinimumWidth(200)  # Size for IPv6 format (xxxx:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx:xxxx)
        self.ipv6_input.setMaximumWidth(250)
        
        self.ipv6_mask_input = QLineEdit("64")
        self.ipv6_mask_input.setValidator(QIntValidator(0, 128, self))
        self.ipv6_mask_input.setPlaceholderText("Mask")
        self.ipv6_mask_input.setEnabled(False)
        self.ipv6_mask_input.setMinimumWidth(40)  # Size for mask (0-128)
        self.ipv6_mask_input.setMaximumWidth(60)
        
        self.ipv6_gateway_input = QLineEdit("2001:db8::1")
        self.ipv6_gateway_input.setPlaceholderText("Gateway")
        self.ipv6_gateway_input.setEnabled(False)
        self.ipv6_gateway_input.setMinimumWidth(200)  # Size for IPv6 format
        self.ipv6_gateway_input.setMaximumWidth(250)
        
        ipv6_layout.addWidget(QLabel("IPv6 Address:"))
        ipv6_layout.addWidget(self.ipv6_input)
        ipv6_layout.addWidget(QLabel("IPv6 Mask:"))
        ipv6_layout.addWidget(self.ipv6_mask_input)
        ipv6_layout.addWidget(QLabel("IPv6 Gateway:"))
        ipv6_layout.addWidget(self.ipv6_gateway_input)
        ipv6_layout.addStretch()  # Add stretch to align with IP Version
        ip_layout.addRow("", ipv6_layout)
        
        # Loopback IP fields
        loopback_layout = QHBoxLayout()
        self.loopback_ipv4_input = QLineEdit("192.255.0.1")
        self.loopback_ipv4_input.setPlaceholderText("e.g., 192.255.0.1")
        self.loopback_ipv4_input.setMinimumWidth(120)
        self.loopback_ipv4_input.setMaximumWidth(150)
        
        self.loopback_ipv6_input = QLineEdit("2001:ff00::1")
        self.loopback_ipv6_input.setPlaceholderText("e.g., 2001:ff00::1")
        self.loopback_ipv6_input.setEnabled(False)
        self.loopback_ipv6_input.setMinimumWidth(200)
        self.loopback_ipv6_input.setMaximumWidth(250)
        
        loopback_layout.addWidget(QLabel("Loopback IPv4:"))
        loopback_layout.addWidget(self.loopback_ipv4_input)
        loopback_layout.addWidget(QLabel("Loopback IPv6:"))
        loopback_layout.addWidget(self.loopback_ipv6_input)
        loopback_layout.addStretch()
        ip_layout.addRow("", loopback_layout)
        
        self.scroll_layout.addWidget(ip_group)

        # Protocol Configuration Group
        protocol_group = QGroupBox("Protocol Configuration")
        protocol_main_layout = QVBoxLayout(protocol_group)
        protocol_main_layout.setSpacing(10)

        # Top Section: Enable Protocol Checkboxes
        enable_section = QGroupBox("Enable Protocols")
        enable_layout = QHBoxLayout(enable_section)
        enable_layout.setSpacing(15)
        
        # Create checkboxes for all protocols
        self.bgp_enable_checkbox = QCheckBox("BGP")
        self.bgp_enable_checkbox.setChecked(False)
        self.bgp_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)
        
        self.ospf_enable_checkbox = QCheckBox("OSPF")
        self.ospf_enable_checkbox.setChecked(False)
        self.ospf_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)
        
        self.isis_enable_checkbox = QCheckBox("ISIS")
        self.isis_enable_checkbox.setChecked(False)
        self.isis_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)
        
        self.dhcp_enable_checkbox = QCheckBox("DHCP")
        self.dhcp_enable_checkbox.setChecked(False)
        self.dhcp_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)
        
        self.rocev2_enable_checkbox = QCheckBox("ROCEv2")
        self.rocev2_enable_checkbox.setChecked(False)
        self.rocev2_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)
        
        enable_layout.addWidget(self.bgp_enable_checkbox)
        enable_layout.addWidget(self.ospf_enable_checkbox)
        enable_layout.addWidget(self.isis_enable_checkbox)
        enable_layout.addWidget(self.dhcp_enable_checkbox)
        enable_layout.addWidget(self.rocev2_enable_checkbox)
        enable_layout.addStretch()
        
        # Track previous checkbox state for DHCP mode toggling
        self._dhcp_prev_ipv4_checked = self.ipv4_checkbox.isChecked()
        self._dhcp_prev_ipv6_checked = self.ipv6_checkbox.isChecked()
        self._dhcp_prev_bgp_checked = self.bgp_enable_checkbox.isChecked()
        self._dhcp_suppress_updates = False

        # Middle Section: Left (Dropdown) and Right (Configuration)
        middle_section = QWidget()
        middle_layout = QHBoxLayout(middle_section)
        middle_layout.setSpacing(20)
        
        # Left Section: Protocol Dropdown
        dropdown_section = QGroupBox("Select Protocol")
        dropdown_layout = QVBoxLayout(dropdown_section)
        
        self.protocol_dropdown = QComboBox()
        self.protocol_dropdown.setMinimumWidth(150)  # Reduced width for more compact layout
        self.protocol_dropdown.currentTextChanged.connect(self._on_protocol_changed)
        dropdown_layout.addWidget(self.protocol_dropdown)
        dropdown_layout.addStretch()
        
        # Right Section: Protocol Configuration
        config_section = QGroupBox("Protocol Configuration")
        config_layout = QVBoxLayout(config_section)
        
        # Create protocol-specific configuration widgets
        self._create_protocol_config_widgets()
        config_layout.addWidget(self.bgp_config_widget)
        config_layout.addWidget(self.ospf_config_widget)
        config_layout.addWidget(self.isis_config_widget)
        config_layout.addWidget(self.dhcp_config_widget)
        config_layout.addWidget(self.rocev2_config_widget)
        
        # Add sections to middle layout
        middle_layout.addWidget(dropdown_section, 1)  # Left section (smaller)
        middle_layout.addWidget(config_section, 3)    # Right section (wider)
        
        # Add all sections to main layout
        protocol_main_layout.addWidget(enable_section)    # Top section
        protocol_main_layout.addWidget(middle_section)    # Middle section
        
        self.scroll_layout.addWidget(protocol_group)

        # Increment Options Group
        increment_group = QGroupBox("Increment Options")
        increment_layout = QFormLayout(increment_group)
        increment_layout.setSpacing(8)
        
        # Checkboxes and count in a horizontal layout
        checkbox_count_layout = QHBoxLayout()
        
        # Enable All checkbox
        self.increment_enable_all = QCheckBox("Enable All")
        self.increment_enable_all.setChecked(False)
        self.increment_enable_all.toggled.connect(self._on_enable_all_toggled)
        
        # Individual increment checkboxes
        self.increment_checkbox_mac = QCheckBox("MAC")
        self.increment_checkbox_ipv4 = QCheckBox("IPv4")
        self.increment_checkbox_ipv6 = QCheckBox("IPv6")
        self.increment_checkbox_gateway = QCheckBox("Gateway")
        self.increment_checkbox_vlan = QCheckBox("VLAN")
        self.increment_checkbox_loopback = QCheckBox("Loopback")
        
        # Connect individual checkboxes to update "Enable All" state
        self.increment_checkbox_mac.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_ipv4.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_ipv6.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_gateway.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_vlan.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_loopback.toggled.connect(self._on_individual_checkbox_toggled)
        
        # Count field
        self.increment_count = QSpinBox()
        self.increment_count.setMinimum(1)
        self.increment_count.setMaximum(10000)
        self.increment_count.setValue(2)  # Default to 2
        self.increment_count.setFixedWidth(80)
        
        # Add all to the same row
        checkbox_count_layout.addWidget(self.increment_enable_all)
        checkbox_count_layout.addWidget(self.increment_checkbox_mac)
        checkbox_count_layout.addWidget(self.increment_checkbox_ipv4)
        checkbox_count_layout.addWidget(self.increment_checkbox_ipv6)
        checkbox_count_layout.addWidget(self.increment_checkbox_gateway)
        checkbox_count_layout.addWidget(self.increment_checkbox_vlan)
        checkbox_count_layout.addWidget(self.increment_checkbox_loopback)
        checkbox_count_layout.addSpacing(20)  # Add some space before count
        checkbox_count_layout.addWidget(QLabel("Count:"))
        checkbox_count_layout.addWidget(self.increment_count)
        checkbox_count_layout.addStretch()  # Push everything to the left
        
        increment_layout.addRow("Increment:", checkbox_count_layout)
        
        # Address Increment Selection - All in one row for compact layout
        
        increment_selection_layout = QHBoxLayout()
        
        # IPv4 Octet Selection
        ipv4_label = QLabel("IPv4:")
        self.ipv4_octet_combo = QComboBox()
        self.ipv4_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        self.ipv4_octet_combo.setCurrentIndex(2)  # Default to 2nd octet
        self.ipv4_octet_combo.setFixedWidth(70)
        self.ipv4_octet_combo.setToolTip("Select which octet to increment (e.g., 192.168.X.0)")
        
        # IPv6 Hextet Selection
        ipv6_label = QLabel("IPv6:")
        self.ipv6_hextet_combo = QComboBox()
        self.ipv6_hextet_combo.addItems(["8th", "7th", "6th", "5th", "4th", "3rd", "2nd", "1st"])
        self.ipv6_hextet_combo.setCurrentIndex(6)  # Default to 2nd hextet
        self.ipv6_hextet_combo.setFixedWidth(70)
        self.ipv6_hextet_combo.setToolTip("Select which hextet to increment (e.g., fe80::X:0000)")
        
        # MAC Byte Selection
        mac_label = QLabel("MAC:")
        self.mac_byte_combo = QComboBox()
        self.mac_byte_combo.addItems(["6th", "5th", "4th", "3rd", "2nd", "1st"])
        self.mac_byte_combo.setCurrentIndex(4)  # Default to 2nd byte
        self.mac_byte_combo.setFixedWidth(70)
        self.mac_byte_combo.setToolTip("Select which byte to increment (e.g., 00:XX:22:33:44:55)")
        
        # Gateway Octet Selection
        gateway_label = QLabel("Gateway:")
        self.gateway_octet_combo = QComboBox()
        self.gateway_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        self.gateway_octet_combo.setCurrentIndex(2)  # Default to 2nd octet
        self.gateway_octet_combo.setFixedWidth(70)
        self.gateway_octet_combo.setToolTip("Select which octet to increment (e.g., 192.168.X.1)")
        
        # Loopback IP Octet/Hextet Selection
        loopback_ipv4_label = QLabel("Loopback IPv4:")
        self.loopback_ipv4_octet_combo = QComboBox()
        self.loopback_ipv4_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        self.loopback_ipv4_octet_combo.setCurrentIndex(3)  # Default to 4th octet (127.0.0.X)
        self.loopback_ipv4_octet_combo.setFixedWidth(70)
        self.loopback_ipv4_octet_combo.setToolTip("Select which octet to increment for loopback IPv4 (e.g., 127.0.0.X)")
        
        loopback_ipv6_label = QLabel("Loopback IPv6:")
        self.loopback_ipv6_hextet_combo = QComboBox()
        self.loopback_ipv6_hextet_combo.addItems(["8th", "7th", "6th", "5th", "4th", "3rd", "2nd", "1st"])
        self.loopback_ipv6_hextet_combo.setCurrentIndex(7)  # Default to 8th hextet (::X)
        self.loopback_ipv6_hextet_combo.setFixedWidth(70)
        self.loopback_ipv6_hextet_combo.setToolTip("Select which hextet to increment for loopback IPv6 (e.g., ::X)")
        
        # Add all to single row (will wrap if needed)
        increment_selection_layout.addWidget(ipv4_label)
        increment_selection_layout.addWidget(self.ipv4_octet_combo)
        increment_selection_layout.addSpacing(10)
        increment_selection_layout.addWidget(ipv6_label)
        increment_selection_layout.addWidget(self.ipv6_hextet_combo)
        increment_selection_layout.addSpacing(10)
        increment_selection_layout.addWidget(mac_label)
        increment_selection_layout.addWidget(self.mac_byte_combo)
        increment_selection_layout.addSpacing(10)
        increment_selection_layout.addWidget(gateway_label)
        increment_selection_layout.addWidget(self.gateway_octet_combo)
        increment_selection_layout.addSpacing(10)
        increment_selection_layout.addWidget(loopback_ipv4_label)
        increment_selection_layout.addWidget(self.loopback_ipv4_octet_combo)
        increment_selection_layout.addSpacing(10)
        increment_selection_layout.addWidget(loopback_ipv6_label)
        increment_selection_layout.addWidget(self.loopback_ipv6_hextet_combo)
        increment_selection_layout.addStretch()
        
        increment_layout.addRow("Position:", increment_selection_layout)
        
        # Add all groups to scroll layout
        self.scroll_layout.addWidget(interface_group)
        self.scroll_layout.addWidget(ip_group)
        self.scroll_layout.addWidget(protocol_group)
        self.scroll_layout.addWidget(increment_group)

    def _create_protocol_config_widgets(self):
        """Create protocol-specific configuration widgets."""
        # BGP Configuration Widget with multi-column layout
        self.bgp_config_widget = QWidget()
        bgp_main_layout = QVBoxLayout(self.bgp_config_widget)
        bgp_main_layout.setSpacing(10)
        bgp_main_layout.setContentsMargins(0, 0, 0, 0)

        # Create two-column layout
        bgp_columns_layout = QHBoxLayout()
        bgp_columns_layout.setSpacing(20)
        
        # Left column
        bgp_left_layout = QFormLayout()
        bgp_left_layout.setSpacing(8)
        
        self.bgp_local_as_input = QLineEdit("65000")
        self.bgp_local_as_input.setPlaceholderText("Local AS Number")
        self.bgp_local_as_input.setValidator(QIntValidator(1, 2147483647, self))
        self.bgp_local_as_input.setEnabled(False)
        bgp_left_layout.addRow("Local AS:", self.bgp_local_as_input)

        self.bgp_remote_as_input = QLineEdit("65001")
        self.bgp_remote_as_input.setPlaceholderText("Remote AS Number")
        self.bgp_remote_as_input.setValidator(QIntValidator(1, 2147483647, self))
        self.bgp_remote_as_input.setEnabled(False)
        bgp_left_layout.addRow("Remote AS:", self.bgp_remote_as_input)
        
        # Right column
        bgp_right_layout = QFormLayout()
        bgp_right_layout.setSpacing(8)
        
        bgp_protocol_layout = QHBoxLayout()
        self.bgp_ipv4_enabled = QCheckBox("IPv4")
        self.bgp_ipv6_enabled = QCheckBox("IPv6")
        self.bgp_ipv4_enabled.setChecked(True)
        self.bgp_ipv6_enabled.setChecked(True)
        self.bgp_ipv4_enabled.setEnabled(False)
        self.bgp_ipv6_enabled.setEnabled(False)
        bgp_protocol_layout.addWidget(self.bgp_ipv4_enabled)
        bgp_protocol_layout.addWidget(self.bgp_ipv6_enabled)
        bgp_right_layout.addRow("Protocols:", bgp_protocol_layout)
        
        # Add columns to main layout
        bgp_columns_layout.addLayout(bgp_left_layout, 1)
        bgp_columns_layout.addLayout(bgp_right_layout, 1)
        bgp_main_layout.addLayout(bgp_columns_layout)

        # OSPF Configuration Widget with multi-column layout
        self.ospf_config_widget = QWidget()
        ospf_main_layout = QVBoxLayout(self.ospf_config_widget)
        ospf_main_layout.setSpacing(10)
        ospf_main_layout.setContentsMargins(0, 0, 0, 0)

        # Create two-column layout
        ospf_columns_layout = QHBoxLayout()
        ospf_columns_layout.setSpacing(20)
        
        # Left column
        ospf_left_layout = QFormLayout()
        ospf_left_layout.setSpacing(8)
        
        self.ospf_area_id_input = QLineEdit("0.0.0.0")
        self.ospf_area_id_input.setPlaceholderText("Area ID")
        self.ospf_area_id_input.setEnabled(False)
        ospf_left_layout.addRow("Area ID:", self.ospf_area_id_input)
        
        self.ospf_router_id_input = QLineEdit()
        self.ospf_router_id_input.setPlaceholderText("Auto-assigned")
        self.ospf_router_id_input.setEnabled(False)
        ospf_left_layout.addRow("Router ID:", self.ospf_router_id_input)
        
        # Right column
        ospf_right_layout = QFormLayout()
        ospf_right_layout.setSpacing(8)
        
        self.ospf_hello_interval_input = QLineEdit("10")
        self.ospf_hello_interval_input.setPlaceholderText("seconds")
        self.ospf_hello_interval_input.setEnabled(False)
        ospf_right_layout.addRow("Hello Interval:", self.ospf_hello_interval_input)
        
        self.ospf_dead_interval_input = QLineEdit("40")
        self.ospf_dead_interval_input.setPlaceholderText("seconds")
        self.ospf_dead_interval_input.setEnabled(False)
        ospf_right_layout.addRow("Dead Interval:", self.ospf_dead_interval_input)
        
        # Bottom row for graceful restart (spans both columns)
        self.ospf_graceful_restart_checkbox = QCheckBox("Enable Graceful Restart")
        self.ospf_graceful_restart_checkbox.setEnabled(False)
        
        # Add columns to main layout
        ospf_columns_layout.addLayout(ospf_left_layout, 1)
        ospf_columns_layout.addLayout(ospf_right_layout, 1)
        ospf_main_layout.addLayout(ospf_columns_layout)
        ospf_main_layout.addWidget(self.ospf_graceful_restart_checkbox)

        # Address family selection for OSPF
        ospf_af_layout = QHBoxLayout()
        ospf_af_layout.setSpacing(8)
        self.ospf_ipv4_enabled_checkbox = QCheckBox("IPv4")
        self.ospf_ipv6_enabled_checkbox = QCheckBox("IPv6")
        self.ospf_ipv4_enabled_checkbox.setChecked(True)
        self.ospf_ipv6_enabled_checkbox.setChecked(False)
        self.ospf_ipv4_enabled_checkbox.setEnabled(False)
        self.ospf_ipv6_enabled_checkbox.setEnabled(False)
        ospf_af_layout.addWidget(self.ospf_ipv4_enabled_checkbox)
        ospf_af_layout.addWidget(self.ospf_ipv6_enabled_checkbox)
        ospf_af_layout.addStretch()
        ospf_main_layout.addLayout(ospf_af_layout)

        # ISIS Configuration Widget with multi-column layout
        self.isis_config_widget = QWidget()
        isis_main_layout = QVBoxLayout(self.isis_config_widget)
        isis_main_layout.setSpacing(10)
        isis_main_layout.setContentsMargins(0, 0, 0, 0)

        # Create two-column layout
        isis_columns_layout = QHBoxLayout()
        isis_columns_layout.setSpacing(20)
        
        # Left column
        isis_left_layout = QFormLayout()
        isis_left_layout.setSpacing(8)
        
        self.isis_area_id_input = QLineEdit("49.0001")
        self.isis_area_id_input.setPlaceholderText("Area ID")
        self.isis_area_id_input.setEnabled(False)
        isis_left_layout.addRow("Area ID:", self.isis_area_id_input)
        
        # Right column
        isis_right_layout = QFormLayout()
        isis_right_layout.setSpacing(8)
        
        self.isis_system_id_input = QLineEdit()
        self.isis_system_id_input.setPlaceholderText("Auto-assigned")
        self.isis_system_id_input.setEnabled(False)
        isis_right_layout.addRow("System ID:", self.isis_system_id_input)
        
        self.isis_hello_interval_input = QLineEdit("10")
        self.isis_hello_interval_input.setPlaceholderText("seconds")
        self.isis_hello_interval_input.setEnabled(False)
        isis_right_layout.addRow("Hello Interval:", self.isis_hello_interval_input)
        
        # Add columns to main layout
        isis_columns_layout.addLayout(isis_left_layout, 1)
        isis_columns_layout.addLayout(isis_right_layout, 1)
        isis_main_layout.addLayout(isis_columns_layout)

        # DHCP Configuration Widget with multi-column layout
        self.dhcp_config_widget = QWidget()
        dhcp_main_layout = QVBoxLayout(self.dhcp_config_widget)
        dhcp_main_layout.setSpacing(10)
        dhcp_main_layout.setContentsMargins(0, 0, 0, 0)

        dhcp_mode_layout = QHBoxLayout()
        self.dhcp_mode_combo = QComboBox()
        self.dhcp_mode_combo.addItems(["Server", "Client"])
        self.dhcp_mode_combo.currentTextChanged.connect(self._on_dhcp_mode_changed)
        dhcp_mode_layout.addWidget(QLabel("Mode:"))
        dhcp_mode_layout.addWidget(self.dhcp_mode_combo)
        dhcp_mode_layout.addStretch()
        dhcp_main_layout.addLayout(dhcp_mode_layout)

        # Create two-column layout
        dhcp_columns_layout = QHBoxLayout()
        dhcp_columns_layout.setSpacing(20)
        
        # Left column
        dhcp_left_layout = QFormLayout()
        dhcp_left_layout.setSpacing(8)
        
        self.dhcp_pool_start_input = QLineEdit("192.168.30.10")
        self.dhcp_pool_start_input.setPlaceholderText("Pool Start")
        self.dhcp_pool_start_input.setEnabled(False)
        dhcp_left_layout.addRow("Pool Start:", self.dhcp_pool_start_input)
        self.dhcp_gateway_route_input = QLineEdit("192.168.30.0/24")
        self.dhcp_gateway_route_input.setPlaceholderText("Gateway Route CIDR (e.g. 192.168.30.0/24)")
        self.dhcp_gateway_route_input.setEnabled(False)
        dhcp_left_layout.addRow("Gateway Route:", self.dhcp_gateway_route_input)
        
        # Right column
        dhcp_right_layout = QFormLayout()
        dhcp_right_layout.setSpacing(8)
        
        self.dhcp_pool_end_input = QLineEdit("192.168.30.200")
        self.dhcp_pool_end_input.setPlaceholderText("Pool End")
        self.dhcp_pool_end_input.setEnabled(False)
        dhcp_right_layout.addRow("Pool End:", self.dhcp_pool_end_input)
        
        self.dhcp_lease_time_input = QLineEdit("3600")
        self.dhcp_lease_time_input.setPlaceholderText("seconds")
        self.dhcp_lease_time_input.setEnabled(False)
        dhcp_right_layout.addRow("Lease Time:", self.dhcp_lease_time_input)
        
        # Add columns to main layout
        dhcp_columns_layout.addLayout(dhcp_left_layout, 1)
        dhcp_columns_layout.addLayout(dhcp_right_layout, 1)
        dhcp_main_layout.addLayout(dhcp_columns_layout)

        # ROCEv2 Configuration Widget with multi-column layout
        self.rocev2_config_widget = QWidget()
        rocev2_main_layout = QVBoxLayout(self.rocev2_config_widget)
        rocev2_main_layout.setSpacing(10)
        rocev2_main_layout.setContentsMargins(0, 0, 0, 0)

        # Create two-column layout
        rocev2_columns_layout = QHBoxLayout()
        rocev2_columns_layout.setSpacing(20)
        
        # Left column
        rocev2_left_layout = QFormLayout()
        rocev2_left_layout.setSpacing(8)
        
        self.rocev2_priority_input = QLineEdit("0")
        self.rocev2_priority_input.setPlaceholderText("Priority")
        self.rocev2_priority_input.setEnabled(False)
        rocev2_left_layout.addRow("Priority:", self.rocev2_priority_input)
        
        # Right column
        rocev2_right_layout = QFormLayout()
        rocev2_right_layout.setSpacing(8)
        
        self.rocev2_dscp_input = QLineEdit("46")
        self.rocev2_dscp_input.setPlaceholderText("DSCP")
        self.rocev2_dscp_input.setEnabled(False)
        rocev2_right_layout.addRow("DSCP:", self.rocev2_dscp_input)
        
        self.rocev2_udp_port_input = QLineEdit("4791")
        self.rocev2_udp_port_input.setPlaceholderText("UDP Port")
        self.rocev2_udp_port_input.setEnabled(False)
        rocev2_right_layout.addRow("UDP Port:", self.rocev2_udp_port_input)
        
        # Add columns to main layout
        rocev2_columns_layout.addLayout(rocev2_left_layout, 1)
        rocev2_columns_layout.addLayout(rocev2_right_layout, 1)
        rocev2_main_layout.addLayout(rocev2_columns_layout)

        # Initially hide all protocol config widgets
        self.bgp_config_widget.setVisible(False)
        self.ospf_config_widget.setVisible(False)
        self.isis_config_widget.setVisible(False)
        self.dhcp_config_widget.setVisible(False)
        self.rocev2_config_widget.setVisible(False)

    def _on_protocol_enabled_changed(self):
        """Handle protocol enable/disable checkbox changes."""
        # Remember latest protocol selections when DHCP is in server mode so they can
        # be restored if the user temporarily switches to client mode.
        if hasattr(self, "dhcp_mode_combo") and self.dhcp_mode_combo.currentText().lower() != "client":
            self._dhcp_prev_bgp_checked = self.bgp_enable_checkbox.isChecked()
            self._dhcp_prev_ipv4_checked = self.ipv4_checkbox.isChecked()
            self._dhcp_prev_ipv6_checked = self.ipv6_checkbox.isChecked()

        # Update dropdown with only enabled protocols
        enabled_protocols = []
        
        if self.bgp_enable_checkbox.isChecked():
            enabled_protocols.append("BGP")
        if self.ospf_enable_checkbox.isChecked():
            enabled_protocols.append("OSPF")
        if self.isis_enable_checkbox.isChecked():
            enabled_protocols.append("ISIS")
        if self.dhcp_enable_checkbox.isChecked():
            enabled_protocols.append("DHCP")
        if self.rocev2_enable_checkbox.isChecked():
            enabled_protocols.append("ROCEv2")
        
        # Update dropdown
        current_selection = self.protocol_dropdown.currentText()
        self.protocol_dropdown.clear()
        self.protocol_dropdown.addItems(enabled_protocols)
        
        # Restore selection if it's still available
        if current_selection in enabled_protocols:
            self.protocol_dropdown.setCurrentText(current_selection)
        elif enabled_protocols:
            self.protocol_dropdown.setCurrentText(enabled_protocols[0])
        
        # Trigger protocol change to show/hide config
        if enabled_protocols:
            self._on_protocol_changed(self.protocol_dropdown.currentText())

    def _on_protocol_changed(self, protocol):
        """Handle protocol dropdown change."""
        if getattr(self, "_suppress_protocol_change", False):
            return
        self._suppress_protocol_change = True
        try:
        # Hide all protocol config widgets and disable all fields
            self.bgp_config_widget.setVisible(False)
            self.ospf_config_widget.setVisible(False)
            self.isis_config_widget.setVisible(False)
            self.dhcp_config_widget.setVisible(False)
            self.rocev2_config_widget.setVisible(False)
            
            # Disable all protocol fields first
            self._disable_all_protocol_fields()
            
            # Show the selected protocol config widget and enable its fields
            if protocol == "BGP" and self.bgp_enable_checkbox.isChecked():
                self.bgp_config_widget.setVisible(True)
                self._enable_bgp_fields()
            elif protocol == "OSPF" and self.ospf_enable_checkbox.isChecked():
                self.ospf_config_widget.setVisible(True)
                self._enable_ospf_fields()
            elif protocol == "ISIS" and self.isis_enable_checkbox.isChecked():
                self.isis_config_widget.setVisible(True)
                self._enable_isis_fields()
            elif protocol == "DHCP" and self.dhcp_enable_checkbox.isChecked():
                self.dhcp_config_widget.setVisible(True)
                self._enable_dhcp_fields()
            elif protocol == "ROCEv2" and self.rocev2_enable_checkbox.isChecked():
                self.rocev2_config_widget.setVisible(True)
                self._enable_rocev2_fields()
        finally:
            self._suppress_protocol_change = False

    def _toggle_ip_fields(self):
        """Enable/disable IP fields based on checkbox state."""
        self.ipv4_input.setEnabled(self.ipv4_checkbox.isChecked())
        self.ipv4_mask_input.setEnabled(self.ipv4_checkbox.isChecked())
        self.ipv4_gateway_input.setEnabled(self.ipv4_checkbox.isChecked())
        self.ipv6_input.setEnabled(self.ipv6_checkbox.isChecked())
        self.ipv6_mask_input.setEnabled(self.ipv6_checkbox.isChecked())
        self.ipv6_gateway_input.setEnabled(self.ipv6_checkbox.isChecked())
        self.loopback_ipv4_input.setEnabled(self.ipv4_checkbox.isChecked())
        self.loopback_ipv6_input.setEnabled(self.ipv6_checkbox.isChecked())
        if self.ospf_ipv4_enabled_checkbox.isEnabled():
            self.ospf_ipv4_enabled_checkbox.setChecked(self.ipv4_checkbox.isChecked())
        if self.ospf_ipv6_enabled_checkbox.isEnabled() and self.ipv6_checkbox.isEnabled():
            self.ospf_ipv6_enabled_checkbox.setChecked(self.ipv6_checkbox.isChecked())

    def _disable_all_protocol_fields(self):
        """Disable all protocol configuration fields."""
        # BGP fields
        self.bgp_local_as_input.setEnabled(False)
        self.bgp_remote_as_input.setEnabled(False)
        self.bgp_ipv4_enabled.setEnabled(False)
        self.bgp_ipv6_enabled.setEnabled(False)
        
        # OSPF fields
        self.ospf_area_id_input.setEnabled(False)
        self.ospf_router_id_input.setEnabled(False)
        self.ospf_hello_interval_input.setEnabled(False)
        self.ospf_dead_interval_input.setEnabled(False)
        self.ospf_graceful_restart_checkbox.setEnabled(False)
        self.ospf_ipv4_enabled_checkbox.setEnabled(False)
        self.ospf_ipv6_enabled_checkbox.setEnabled(False)
        
        # ISIS fields
        self.isis_area_id_input.setEnabled(False)
        self.isis_system_id_input.setEnabled(False)
        self.isis_hello_interval_input.setEnabled(False)
        
        # DHCP fields
        self.dhcp_pool_start_input.setEnabled(False)
        self.dhcp_pool_end_input.setEnabled(False)
        self.dhcp_lease_time_input.setEnabled(False)
        self.dhcp_gateway_route_input.setEnabled(False)
        self.dhcp_mode_combo.setEnabled(False)
        
        # ROCEv2 fields
        self.rocev2_priority_input.setEnabled(False)
        self.rocev2_dscp_input.setEnabled(False)
        self.rocev2_udp_port_input.setEnabled(False)

    def _enable_bgp_fields(self):
        """Enable BGP configuration fields."""
        self.bgp_local_as_input.setEnabled(True)
        self.bgp_remote_as_input.setEnabled(True)
        self.bgp_ipv4_enabled.setEnabled(True)
        self.bgp_ipv6_enabled.setEnabled(True)

    def _enable_ospf_fields(self):
        """Enable OSPF configuration fields."""
        self.ospf_area_id_input.setEnabled(True)
        self.ospf_router_id_input.setEnabled(True)
        self.ospf_hello_interval_input.setEnabled(True)
        self.ospf_dead_interval_input.setEnabled(True)
        self.ospf_graceful_restart_checkbox.setEnabled(True)
        self.ospf_ipv4_enabled_checkbox.setEnabled(True)
        self.ospf_ipv6_enabled_checkbox.setEnabled(True)

    def _enable_isis_fields(self):
        """Enable ISIS configuration fields."""
        self.isis_area_id_input.setEnabled(True)
        self.isis_system_id_input.setEnabled(True)
        self.isis_hello_interval_input.setEnabled(True)

    def _enable_dhcp_fields(self):
        """Enable DHCP configuration fields."""
        self.dhcp_mode_combo.setEnabled(True)
        self._on_dhcp_mode_changed()

    def _on_dhcp_mode_changed(self, mode=None):
        """Enable or disable DHCP server fields based on selected mode."""
        if mode is None:
            mode = self.dhcp_mode_combo.currentText() if hasattr(self, "dhcp_mode_combo") else "Client"
        is_server = mode.lower() == "server"
        is_client = mode.lower() == "client"
        enable_server_fields = is_server and self.dhcp_mode_combo.isEnabled()
        for widget in (self.dhcp_pool_start_input, self.dhcp_pool_end_input, self.dhcp_lease_time_input, self.dhcp_gateway_route_input):
            widget.setEnabled(enable_server_fields)
        if enable_server_fields:
            if not self.dhcp_pool_start_input.text().strip():
                self.dhcp_pool_start_input.setText("192.168.30.10")
            if not self.dhcp_pool_end_input.text().strip():
                self.dhcp_pool_end_input.setText("192.168.30.200")
            if not self.dhcp_gateway_route_input.text().strip():
                self.dhcp_gateway_route_input.setText("192.168.30.0/24")
            if hasattr(self, "ipv4_gateway_input") and not self.ipv4_gateway_input.text().strip():
                self.ipv4_gateway_input.setText("192.168.30.1")
        if hasattr(self, "ipv4_checkbox") and hasattr(self, "ipv6_checkbox"):
            if is_client and self.dhcp_mode_combo.isEnabled():
                self._dhcp_prev_ipv4_checked = self.ipv4_checkbox.isChecked()
                self._dhcp_prev_ipv6_checked = True
                self._dhcp_prev_bgp_checked = self.bgp_enable_checkbox.isChecked()
                self.ipv4_checkbox.setChecked(False)
                self.ipv4_checkbox.setEnabled(False)
                self.ipv4_input.clear()
                self.ipv4_mask_input.clear()
                self.ipv4_gateway_input.clear()
                self.loopback_ipv4_input.clear()
                self.ipv6_checkbox.setChecked(False)
                self.ipv6_checkbox.setEnabled(False)
                self.ipv6_input.clear()
                self.ipv6_mask_input.clear()
                self.ipv6_gateway_input.clear()
                self.loopback_ipv6_input.clear()
                if hasattr(self, "ospf_ipv4_enabled_checkbox"):
                    self.ospf_ipv4_enabled_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                if hasattr(self, "ospf_ipv6_enabled_checkbox"):
                    self.ospf_ipv6_enabled_checkbox.setChecked(True)
                if self.bgp_enable_checkbox.isChecked():
                    self.bgp_enable_checkbox.setChecked(False)
                self.bgp_enable_checkbox.setEnabled(False)
            else:
                self.ipv4_checkbox.setEnabled(True)
                self.ipv6_checkbox.setEnabled(True)
                self.ipv4_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                self.ipv6_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv6_checked", False))
                # Restore previously remembered protocol states
                if hasattr(self, "ospf_ipv4_enabled_checkbox"):
                    self.ospf_ipv4_enabled_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                if hasattr(self, "ospf_ipv6_enabled_checkbox"):
                    self.ospf_ipv6_enabled_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv6_checked", False))
                self.bgp_enable_checkbox.setEnabled(True)
                self.bgp_enable_checkbox.setChecked(getattr(self, "_dhcp_prev_bgp_checked", False))
                self._dhcp_prev_bgp_checked = self.bgp_enable_checkbox.isChecked()
                self._dhcp_prev_ipv4_checked = self.ipv4_checkbox.isChecked()
                self._dhcp_prev_ipv6_checked = self.ipv6_checkbox.isChecked()
        # Update protocol list after any forced toggles
        if not getattr(self, "_dhcp_suppress_updates", False):
            self._on_protocol_enabled_changed()

    def _enable_rocev2_fields(self):
        """Enable ROCEv2 configuration fields."""
        self.rocev2_priority_input.setEnabled(True)
        self.rocev2_dscp_input.setEnabled(True)
        self.rocev2_udp_port_input.setEnabled(True)

    def _on_enable_all_toggled(self, checked):
        """Handle Enable All checkbox toggle."""
        # Block signals to prevent recursive calls
        self.increment_checkbox_mac.blockSignals(True)
        self.increment_checkbox_ipv4.blockSignals(True)
        self.increment_checkbox_ipv6.blockSignals(True)
        self.increment_checkbox_gateway.blockSignals(True)
        self.increment_checkbox_vlan.blockSignals(True)
        self.increment_checkbox_loopback.blockSignals(True)
        
        # Set all individual checkboxes to the same state
        self.increment_checkbox_mac.setChecked(checked)
        self.increment_checkbox_ipv4.setChecked(checked)
        self.increment_checkbox_ipv6.setChecked(checked)
        self.increment_checkbox_gateway.setChecked(checked)
        self.increment_checkbox_vlan.setChecked(checked)
        self.increment_checkbox_loopback.setChecked(checked)
        
        # Unblock signals
        self.increment_checkbox_mac.blockSignals(False)
        self.increment_checkbox_ipv4.blockSignals(False)
        self.increment_checkbox_ipv6.blockSignals(False)
        self.increment_checkbox_gateway.blockSignals(False)
        self.increment_checkbox_vlan.blockSignals(False)
        self.increment_checkbox_loopback.blockSignals(False)

    def _on_individual_checkbox_toggled(self):
        """Handle individual checkbox toggle to update Enable All state."""
        # Check if all individual checkboxes are checked
        all_checked = (self.increment_checkbox_mac.isChecked() and
                      self.increment_checkbox_ipv4.isChecked() and
                      self.increment_checkbox_ipv6.isChecked() and
                      self.increment_checkbox_gateway.isChecked() and
                      self.increment_checkbox_vlan.isChecked() and
                      self.increment_checkbox_loopback.isChecked())
        
        # Check if any individual checkbox is checked
        any_checked = (self.increment_checkbox_mac.isChecked() or
                      self.increment_checkbox_ipv4.isChecked() or
                      self.increment_checkbox_ipv6.isChecked() or
                      self.increment_checkbox_gateway.isChecked() or
                      self.increment_checkbox_vlan.isChecked() or
                      self.increment_checkbox_loopback.isChecked())
        
        # Block signals to prevent recursive calls
        self.increment_enable_all.blockSignals(True)
        
        # Update Enable All checkbox state
        if all_checked:
            self.increment_enable_all.setChecked(True)
        elif not any_checked:
            self.increment_enable_all.setChecked(False)
        # If some but not all are checked, leave Enable All unchecked
        
        # Unblock signals
        self.increment_enable_all.blockSignals(False)

    def get_values(self):
        """Get all form values."""
        ipv4 = self.ipv4_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6 = self.ipv6_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        ipv4_mask = self.ipv4_mask_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6_mask = self.ipv6_mask_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        ipv4_gateway = self.ipv4_gateway_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6_gateway = self.ipv6_gateway_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        loopback_ipv4 = self.loopback_ipv4_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        loopback_ipv6 = self.loopback_ipv6_input.text().strip() if self.ipv6_checkbox.isChecked() else ""

        # Protocol configuration based on enabled protocols and dropdown selection
        selected_protocol = self.protocol_dropdown.currentText()
        ospf_config = None
        bgp_config = None
        isis_config = None
        dhcp_config = None
        dhcp_mode_text = ""
        is_dhcp_enabled = self.dhcp_enable_checkbox.isChecked()
        if is_dhcp_enabled:
            if hasattr(self, "dhcp_mode_combo"):
                dhcp_mode_text = self.dhcp_mode_combo.currentText().strip().lower()
            dhcp_mode_text = dhcp_mode_text or "client"

        # Create config for each enabled protocol (can have both BGP, OSPF, and ISIS)
        if self.isis_enable_checkbox.isChecked():
            # Construct VLAN interface name for ISIS configuration (similar to OSPF)
            base_interface = self.iface_input.text().strip()
            vlan_id = self.vlan_input.text().strip() or "0"
            if vlan_id != "0":
                isis_interface = f"vlan{vlan_id}"
            else:
                isis_interface = base_interface
            
            # Get system ID or use auto-assigned if empty
            system_id = self.isis_system_id_input.text().strip()
            if not system_id:
                system_id = "0000.0000.0001"  # Default system ID
            
            # Get area_id - if it's a short format like "49.0001", construct full NET format
            area_id_input = self.isis_area_id_input.text().strip() or "49.0001"
            # If area_id is short format (e.g., "49.0001"), construct full NET format
            if area_id_input and len(area_id_input.split('.')) <= 2:
                # Short format like "49.0001" -> "49.0001.0000.0000.0001.00"
                area_id = f"{area_id_input}.0000.0000.0001.00"
            else:
                # Already in full format
                area_id = area_id_input
            
            isis_config = {
                "area_id": area_id,
                "system_id": system_id,
                "level": "Level-2",  # Default level (dialog doesn't have level field, so use default)
                "hello_interval": self.isis_hello_interval_input.text().strip() or "10",
                "hello_multiplier": "3",  # Default (dialog doesn't have this field)
                "metric": "10",  # Default (dialog doesn't have this field)
                "interface": isis_interface,
                "ipv4_enabled": self.ospf_ipv4_enabled_checkbox.isChecked(),
                "ipv6_enabled": self.ospf_ipv6_enabled_checkbox.isChecked()
            }
        
        if self.bgp_enable_checkbox.isChecked():
            bgp_config = {
                "bgp_asn": self.bgp_local_as_input.text().strip(),
                "bgp_remote_asn": self.bgp_remote_as_input.text().strip(),
                "mode": "eBGP",
                "bgp_keepalive": "30",
                "bgp_hold_time": "90",
                "ipv4_enabled": self.bgp_ipv4_enabled.isChecked(),
                "ipv6_enabled": self.bgp_ipv6_enabled.isChecked(),
                "local_ip": ipv4,  # Use device IP as local IP
                "peer_ip": ipv4_gateway  # Use IPv4 gateway as peer IP
            }
        
        if self.ospf_enable_checkbox.isChecked():
            # Construct VLAN interface name for OSPF configuration
            base_interface = self.iface_input.text().strip()
            vlan_id = self.vlan_input.text().strip() or "0"
            if vlan_id != "0":
                ospf_interface = f"vlan{vlan_id}"
            else:
                ospf_interface = base_interface
            
            area_id = self.ospf_area_id_input.text().strip() or "0.0.0.0"
            ospf_config = {
                "area_id": area_id,
                "area_id_ipv4": area_id,  # Initialize from area_id
                "area_id_ipv6": area_id,  # Initialize from area_id
                "router_id": self.ospf_router_id_input.text().strip(),
                "hello_interval": self.ospf_hello_interval_input.text().strip() or "10",
                "dead_interval": self.ospf_dead_interval_input.text().strip() or "40",
                "graceful_restart": self.ospf_graceful_restart_checkbox.isChecked(),
                "interface": ospf_interface,
                "ipv4_enabled": self.ospf_ipv4_enabled_checkbox.isChecked(),
                "ipv6_enabled": self.ospf_ipv6_enabled_checkbox.isChecked()
            }

        if is_dhcp_enabled:
            dhcp_config = {"mode": dhcp_mode_text}

            # Derive interface hint for DHCP helpers
            base_interface = self.iface_input.text().strip()
            vlan_id = (self.vlan_input.text().strip() or "0")
            if base_interface:
                dhcp_interface = f"vlan{vlan_id}" if vlan_id and vlan_id != "0" else base_interface
                dhcp_config["interface"] = dhcp_interface

            if dhcp_mode_text == "server":
                pool_start = self.dhcp_pool_start_input.text().strip()
                pool_end = self.dhcp_pool_end_input.text().strip()
                lease_time = self.dhcp_lease_time_input.text().strip()
                gateway_value = ipv4_gateway or ""
                gateway_route_value = self.dhcp_gateway_route_input.text().strip()

                if pool_start:
                    dhcp_config["pool_start"] = pool_start
                if pool_end:
                    dhcp_config["pool_end"] = pool_end
                if pool_start and pool_end:
                    dhcp_config["pool_range"] = f"{pool_start}-{pool_end}"
                if lease_time:
                    dhcp_config["lease_time"] = lease_time
                if gateway_value:
                    dhcp_config["gateway"] = gateway_value
                if gateway_route_value:
                    dhcp_config["gateway_route"] = gateway_route_value
            else:
                # Allow hostname hint for dhclient; fall back to device name
                hostname = self.device_name_input.text().strip()
                if hostname:
                    dhcp_config["hostname"] = hostname

            if dhcp_mode_text == "client":
                # Ignore user-provided static IP settings when DHCP client mode is selected
                ipv4 = ""
                ipv6 = ""
                ipv4_mask = ""
                ipv6_mask = ""
                ipv4_gateway = ""
                ipv6_gateway = ""
                bgp_config = {}
                if ospf_config:
                    # Preserve intent to run OSPF even though address will arrive via DHCP
                    ospf_config["ipv4_enabled"] = self.ospf_ipv4_enabled_checkbox.isChecked()
                    ospf_config["ipv6_enabled"] = self.ospf_ipv6_enabled_checkbox.isChecked()
                if isis_config:
                    isis_config["ipv4_enabled"] = self.ospf_ipv4_enabled_checkbox.isChecked()
                    isis_config["ipv6_enabled"] = self.ospf_ipv6_enabled_checkbox.isChecked()

        return (
            self.device_name_input.text().strip(),
            self.iface_input.text().strip(),
            self.mac_input.text().strip(),
            ipv4,
            ipv6,
            ipv4_mask,
            ipv6_mask,
            self.vlan_input.text().strip(),
            ipv4_gateway,
            ipv6_gateway,
            self.increment_checkbox_mac.isChecked(),
            self.increment_checkbox_ipv4.isChecked(),
            self.increment_checkbox_ipv6.isChecked(),
            self.increment_checkbox_gateway.isChecked(),
            self.increment_checkbox_vlan.isChecked(),
            self.increment_count.value(),
            ospf_config,
            bgp_config,
            dhcp_config,
            self.ipv4_octet_combo.currentIndex(),    # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.ipv6_hextet_combo.currentIndex(),   # 0=8th, 1=7th, ..., 7=1st
            self.mac_byte_combo.currentIndex(),      # 0=6th, 1=5th, ..., 5=1st
            self.gateway_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.increment_checkbox_loopback.isChecked(),  # Loopback increment checkbox
            self.loopback_ipv4_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.loopback_ipv6_hextet_combo.currentIndex(),  # 0=8th, 1=7th, ..., 7=1st
            loopback_ipv4,
            loopback_ipv6,
            isis_config
        )
    
    def validate_gateway_subnet(self, device_ip, device_mask, gateway):
        """Validate that the gateway is in the same subnet as the device IP."""
        if not gateway or not device_ip or device_mask is None:
            return True, ""  # Empty values are allowed
        
        try:
            if '.' in device_ip:  # IPv4
                device_network = ipaddress.IPv4Network(f"{device_ip}/{device_mask}", strict=False)
                gateway_ip = ipaddress.IPv4Address(gateway)
                if gateway_ip not in device_network:
                    return False, f"Gateway {gateway} is not in the same subnet as device IP {device_ip}/{device_mask}"
            else:  # IPv6
                device_network = ipaddress.IPv6Network(f"{device_ip}/{device_mask}", strict=False)
                gateway_ip = ipaddress.IPv6Address(gateway)
                if gateway_ip not in device_network:
                    return False, f"Gateway {gateway} is not in the same subnet as device IP {device_ip}/{device_mask}"
            
            return True, ""
        except (ipaddress.AddressValueError, ValueError) as e:
            return False, f"Invalid IP address or network configuration: {e}"
    
    def validate_and_accept(self):
        """Validate the form data before accepting the dialog."""
        # Get form values
        device_name = self.device_name_input.text().strip()
        iface = self.iface_input.text().strip()
        mac = self.mac_input.text().strip()
        ipv4 = self.ipv4_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6 = self.ipv6_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        ipv4_mask = self.ipv4_mask_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6_mask = self.ipv6_mask_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        # Gateway values will be extracted separately below
        
        # Basic validation - device name is now optional (will use default names)
        
        if not iface:
            QMessageBox.warning(self, "Validation Error", "Interface is required.")
            return
        
        if not mac:
            QMessageBox.warning(self, "Validation Error", "MAC address is required.")
            return
        
        # Get gateway values
        ipv4_gateway = self.ipv4_gateway_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        ipv6_gateway = self.ipv6_gateway_input.text().strip() if self.ipv6_checkbox.isChecked() else ""
        
        # Validate IP addresses and subnet consistency
        if ipv4:
            try:
                ipaddress.IPv4Address(ipv4)
                if ipv4_mask:
                    try:
                        mask_val = int(ipv4_mask)
                        if mask_val < 0 or mask_val > 32:
                            QMessageBox.warning(self, "Validation Error", "IPv4 mask must be between 0 and 32.")
                            return
                        
                        # Validate IPv4 gateway subnet
                        if ipv4_gateway:
                            is_valid, error_msg = self.validate_gateway_subnet(ipv4, mask_val, ipv4_gateway)
                            if not is_valid:
                                QMessageBox.warning(self, "IPv4 Gateway Validation Error", error_msg)
                                return
                    except ValueError:
                        QMessageBox.warning(self, "Validation Error", "IPv4 mask must be a valid number.")
                        return
            except ipaddress.AddressValueError:
                QMessageBox.warning(self, "Validation Error", "Invalid IPv4 address format.")
                return
        
        if ipv6:
            try:
                ipaddress.IPv6Address(ipv6)
                if ipv6_mask:
                    try:
                        mask_val = int(ipv6_mask)
                        if mask_val < 0 or mask_val > 128:
                            QMessageBox.warning(self, "Validation Error", "IPv6 mask must be between 0 and 128.")
                            return
                        
                        # Validate IPv6 gateway subnet
                        if ipv6_gateway:
                            is_valid, error_msg = self.validate_gateway_subnet(ipv6, mask_val, ipv6_gateway)
                            if not is_valid:
                                QMessageBox.warning(self, "IPv6 Gateway Validation Error", error_msg)
                                return
                    except ValueError:
                        QMessageBox.warning(self, "Validation Error", "IPv6 mask must be a valid number.")
                        return
            except ipaddress.AddressValueError:
                QMessageBox.warning(self, "Validation Error", "Invalid IPv6 address format.")
                return
        
        # Validate gateway formats if provided
        if ipv4_gateway:
            try:
                ipaddress.IPv4Address(ipv4_gateway)
            except ipaddress.AddressValueError:
                QMessageBox.warning(self, "Validation Error", "Invalid IPv4 gateway address format.")
                return
                
        if ipv6_gateway:
            try:
                ipaddress.IPv6Address(ipv6_gateway)
            except ipaddress.AddressValueError:
                QMessageBox.warning(self, "Validation Error", "Invalid IPv6 gateway address format.")
                return
        
        # All validations passed
        self.accept()
