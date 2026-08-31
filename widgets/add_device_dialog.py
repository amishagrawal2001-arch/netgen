from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QCheckBox,
    QSpinBox,
    QGroupBox,
    QDialogButtonBox,
    QWidget,
    QMessageBox,
    QLabel,
    QComboBox,
    QScrollArea,
)
from PyQt5.QtGui import QIntValidator, QRegExpValidator
from PyQt5.QtCore import QRegExp, Qt
import uuid
import ipaddress


class AddDeviceDialog(QDialog):
    def __init__(self, parent=None, default_iface="", mode="add",
                 existing_devices=None, exclude_device_id=None):
        """AddDeviceDialog also serves as the Edit dialog (same form,
        same fields). `mode` controls user-facing labels:

          mode="add"  → "Add New Device" title, "Add Device" button
          mode="edit" → "Edit Device" title, "Update Device" button

        Behavior is identical otherwise; the caller is responsible
        for pre-filling fields when editing.

        `existing_devices` (optional) is a list of device dicts from
        the server (usually the caller's cached device list). When
        provided in "add" mode, the loopback IPv4/IPv6 fields seed to
        the next-available value instead of the static default, so
        the user doesn't accidentally reuse device1's loopback for
        device2 and break OSPF adjacency. In both modes, validate_
        and_accept refuses to submit a collision. `exclude_device_id`
        is the device_id to skip during collision detection (self on
        edit — otherwise the dialog would flag the value being edited
        as a collision with itself).
        """
        super().__init__(parent)
        self.mode = (mode or "add").lower()
        # Kept for validate_and_accept and pre-fill helpers.
        self._existing_devices = list(existing_devices or [])
        self._exclude_device_id = exclude_device_id
        if self.mode == "edit":
            self.setWindowTitle("Edit Device")
        else:
            self.setWindowTitle("Add New Device")
        self.setMinimumSize(1000, 700)  # Further increased width for better field spacing
        self.resize(1000, 700)  # Set initial size with wider width

        self.default_iface = default_iface
        self._vxlan_local_default = ""
        self._vxlan_remote_default = ""
        self._vxlan_remote_base = "192.168.250.1"  # Base remote loopback IP for VXLAN
        self._vxlan_bridge_svi_default = "10.0.0.100/24"
        self._vxlan_bridge_svi_last_default = ""
        
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
        self.scroll_layout.setContentsMargins(2, 5, 5, 5)  # Reduce left margin further
        
        # Setup form content
        self.setup_basic_device_form()
        
        # Set scroll content
        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)
        
        # Add button box. Button label depends on mode: "Add Device"
        # for a new device, "Update Device" when editing an existing
        # one. Cancel is unchanged.
        self.button_box = QDialogButtonBox()
        ok_label = "Update Device" if self.mode == "edit" else "Add Device"
        self.ok_button = self.button_box.addButton(ok_label, QDialogButtonBox.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        # v0.5.226: upstream router config hint. Opens a modal with
        # Juniper / Cisco IOS / Arista tabs rendering paste-ready
        # config for whichever router is on the other end of the
        # link. Reads live from whatever the operator has entered so
        # far, so it works pre- or post-Save.
        self.upstream_hint_button = self.button_box.addButton(
            "Upstream Config Hint…", QDialogButtonBox.ActionRole,
        )
        self.upstream_hint_button.setToolTip(
            "Show Juniper / Cisco IOS / Arista config snippets to paste "
            "onto the upstream router that this device is peering with."
        )

        self.ok_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)
        self.upstream_hint_button.clicked.connect(self._open_upstream_hint_dialog)

        self.main_layout.addWidget(self.button_box)
        self.setLayout(self.main_layout)

    def setup_basic_device_form(self):
        """Setup a well-organized form for basic device information."""
        # ── Template picker (one-click preset profiles) ─────────────
        # Appears at the very top so operators can pick a role
        # (eBGP peer, OSPFv2 backbone, DHCP client, ...) and have the
        # form pre-fill in one click. Skip-applicable: any template
        # field whose widget doesn't exist on this build is silently
        # ignored, so old templates work after a form rearrangement.
        try:
            from utils.device_templates import list_templates
            self._templates_meta = list_templates()
        except Exception:
            self._templates_meta = []
        if self._templates_meta and self.mode != "edit":
            template_group = QGroupBox("Quick start from template")
            template_layout = QHBoxLayout(template_group)
            template_layout.setContentsMargins(8, 6, 8, 6)

            self.template_combo = QComboBox()
            self.template_combo.addItem("— Custom (no template) —", "")
            for t in self._templates_meta:
                self.template_combo.addItem(t["title"], t["key"])
            template_layout.addWidget(QLabel("Template:"))
            template_layout.addWidget(self.template_combo, 1)

            self._template_summary_label = QLabel(
                "Pick a template to pre-fill the form, then tweak as needed."
            )
            self._template_summary_label.setStyleSheet(
                "color: #6b7280; font-size: 11px;"
            )
            self._template_summary_label.setWordWrap(True)

            self.template_combo.currentIndexChanged.connect(
                self._on_template_changed
            )
            self.scroll_layout.addWidget(template_group)
            self.scroll_layout.addWidget(self._template_summary_label)

        # Interface Configuration Group
        interface_group = QGroupBox("Interface Configuration")
        interface_layout = QFormLayout(interface_group)
        interface_layout.setSpacing(10)
        interface_layout.setHorizontalSpacing(5)  # Reduce gap between label and field
        interface_layout.setContentsMargins(5, 10, 5, 10)  # Reduce left margin

        # Device name
        self.device_name_input = QLineEdit()
        self.device_name_input.setPlaceholderText("Optional - leave empty for device1, device2, etc.")
        interface_layout.addRow("Device Name:", self.device_name_input)

        # Interface, VLAN-ID, MAC Address, and MTU in one row
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
        
        # MTU field
        self.mtu_input = QLineEdit("1500")
        self.mtu_input.setPlaceholderText("MTU")
        self.mtu_input.setValidator(QIntValidator(68, 9216, self))
        self.mtu_input.setMinimumWidth(60)
        self.mtu_input.setMaximumWidth(80)
        self.mtu_input.setToolTip("Interface MTU (68-9216 bytes). Default: 1500")
        
        # Add fields to horizontal layout
        interface_vlan_mac_layout.addWidget(QLabel("Interface:"))
        interface_vlan_mac_layout.addWidget(self.iface_input)
        interface_vlan_mac_layout.addWidget(QLabel("VLAN-ID:"))
        interface_vlan_mac_layout.addWidget(self.vlan_input)
        interface_vlan_mac_layout.addWidget(QLabel("MAC Address:"))
        interface_vlan_mac_layout.addWidget(self.mac_input)
        interface_vlan_mac_layout.addWidget(QLabel("MTU:"))
        interface_vlan_mac_layout.addWidget(self.mtu_input)
        interface_vlan_mac_layout.addStretch()
        
        interface_layout.addRow("", interface_vlan_mac_layout)

        self.scroll_layout.addWidget(interface_group)

        # IP Configuration Group
        ip_group = QGroupBox("IP Configuration")
        ip_layout = QFormLayout(ip_group)
        ip_layout.setSpacing(10)
        ip_layout.setHorizontalSpacing(5)  # Reduce gap between label and field
        ip_layout.setContentsMargins(5, 10, 5, 10)  # Reduce left margin

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
        self.ipv4_input.textChanged.connect(self._on_ipv4_text_changed)
        
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
        self.ipv4_gateway_input.textChanged.connect(self._on_ipv4_gateway_changed)
        
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
        
        # Loopback IP fields — on Add with known peers, seed to the
        # next-available value to prevent duplicate router-ids. Falls
        # back to the classic defaults on Edit or with no peer list.
        loopback_layout = QHBoxLayout()
        _lb4_default = "192.255.0.1"
        _lb6_default = "2001:ff00::1"
        if self.mode != "edit" and self._existing_devices:
            try:
                from utils.address_collision import (
                    next_available_loopback_ipv4,
                    next_available_loopback_ipv6,
                )
                _lb4_default = next_available_loopback_ipv4(self._existing_devices)
                _lb6_default = next_available_loopback_ipv6(self._existing_devices)
            except Exception:
                # Fail-open to the static default so a bad import
                # never blocks opening the dialog.
                pass
        self.loopback_ipv4_input = QLineEdit(_lb4_default)
        self.loopback_ipv4_input.setPlaceholderText("e.g., 192.255.0.1")
        self.loopback_ipv4_input.setMinimumWidth(120)
        self.loopback_ipv4_input.setMaximumWidth(150)

        self.loopback_ipv6_input = QLineEdit(_lb6_default)
        self.loopback_ipv6_input.setPlaceholderText("e.g., 2001:ff00::1")
        self.loopback_ipv6_input.setEnabled(False)
        self.loopback_ipv6_input.setMinimumWidth(200)
        self.loopback_ipv6_input.setMaximumWidth(250)
        
        loopback_layout.addWidget(QLabel("Loopback IPv4:"))
        loopback_layout.addWidget(self.loopback_ipv4_input)
        loopback_layout.addWidget(QLabel("Loopback IPv6:"))
        loopback_layout.addWidget(self.loopback_ipv6_input)
        loopback_layout.addStretch()
        # Connect loopback IPv4 changes to refresh VXLAN defaults
        self.loopback_ipv4_input.textChanged.connect(self._refresh_vxlan_defaults_from_ipv4)
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
        
        self.vxlan_enable_checkbox = QCheckBox("VXLAN")
        self.vxlan_enable_checkbox.setChecked(False)
        self.vxlan_enable_checkbox.toggled.connect(self._on_protocol_enabled_changed)

        enable_layout.addWidget(self.bgp_enable_checkbox)
        enable_layout.addWidget(self.ospf_enable_checkbox)
        enable_layout.addWidget(self.isis_enable_checkbox)
        enable_layout.addWidget(self.dhcp_enable_checkbox)
        enable_layout.addWidget(self.rocev2_enable_checkbox)
        enable_layout.addWidget(self.vxlan_enable_checkbox)
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

        # BGP address-family toggles
        self.bgp_toggle_container = QWidget()
        bgp_toggle_layout = QHBoxLayout(self.bgp_toggle_container)
        bgp_toggle_layout.setContentsMargins(0, 0, 0, 0)
        bgp_toggle_layout.setSpacing(10)
        self.bgp_toggle_ipv4 = QCheckBox("IPv4")
        self.bgp_toggle_ipv4.setChecked(True)
        self.bgp_toggle_ipv4.toggled.connect(self._on_bgp_toggle_changed)
        self.bgp_toggle_ipv6 = QCheckBox("IPv6")
        self.bgp_toggle_ipv6.setChecked(True)
        self.bgp_toggle_ipv6.toggled.connect(self._on_bgp_toggle_changed)
        bgp_toggle_layout.addWidget(self.bgp_toggle_ipv4)
        bgp_toggle_layout.addWidget(self.bgp_toggle_ipv6)
        bgp_toggle_layout.addStretch()
        dropdown_layout.addWidget(self.bgp_toggle_container)
        self.bgp_toggle_container.setVisible(False)

        # OSPF address-family toggles
        self.ospf_toggle_container = QWidget()
        ospf_toggle_layout = QHBoxLayout(self.ospf_toggle_container)
        ospf_toggle_layout.setContentsMargins(0, 0, 0, 0)
        ospf_toggle_layout.setSpacing(10)
        self.ospf_toggle_ipv4 = QCheckBox("IPv4")
        self.ospf_toggle_ipv4.setChecked(True)
        self.ospf_toggle_ipv4.toggled.connect(self._on_ospf_toggle_changed)
        self.ospf_toggle_ipv6 = QCheckBox("IPv6")
        self.ospf_toggle_ipv6.setChecked(True)
        self.ospf_toggle_ipv6.toggled.connect(self._on_ospf_toggle_changed)
        ospf_toggle_layout.addWidget(self.ospf_toggle_ipv4)
        ospf_toggle_layout.addWidget(self.ospf_toggle_ipv6)
        ospf_toggle_layout.addStretch()
        dropdown_layout.addWidget(self.ospf_toggle_container)
        self.ospf_toggle_container.setVisible(False)

        # ISIS address-family toggles
        self.isis_toggle_container = QWidget()
        isis_toggle_layout = QHBoxLayout(self.isis_toggle_container)
        isis_toggle_layout.setContentsMargins(0, 0, 0, 0)
        isis_toggle_layout.setSpacing(10)
        self.isis_toggle_ipv4 = QCheckBox("IPv4")
        self.isis_toggle_ipv4.setChecked(True)
        self.isis_toggle_ipv4.toggled.connect(self._on_isis_toggle_changed)
        self.isis_toggle_ipv6 = QCheckBox("IPv6")
        self.isis_toggle_ipv6.setChecked(True)
        self.isis_toggle_ipv6.toggled.connect(self._on_isis_toggle_changed)
        isis_toggle_layout.addWidget(self.isis_toggle_ipv4)
        isis_toggle_layout.addWidget(self.isis_toggle_ipv6)
        isis_toggle_layout.addStretch()
        dropdown_layout.addWidget(self.isis_toggle_container)
        self.isis_toggle_container.setVisible(False)
        self._isis_ipv4_enabled_state = True
        self._isis_ipv6_enabled_state = True

        self.dhcp_toggle_container = QWidget()
        toggle_layout = QHBoxLayout(self.dhcp_toggle_container)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(10)
        self.dhcp_ipv4_enabled_checkbox = QCheckBox("IPv4")
        self.dhcp_ipv4_enabled_checkbox.setChecked(True)
        self.dhcp_ipv4_enabled_checkbox.toggled.connect(self._update_dhcp_field_states)
        self.dhcp_ipv6_enabled_checkbox = QCheckBox("IPv6")
        self.dhcp_ipv6_enabled_checkbox.setChecked(False)
        self.dhcp_ipv6_enabled_checkbox.toggled.connect(self._update_dhcp_field_states)
        toggle_layout.addWidget(self.dhcp_ipv4_enabled_checkbox)
        toggle_layout.addWidget(self.dhcp_ipv6_enabled_checkbox)
        toggle_layout.addStretch()
        dropdown_layout.addWidget(self.dhcp_toggle_container)

        self.dhcp_mode_container = QWidget()
        mode_form_layout = QFormLayout(self.dhcp_mode_container)
        mode_form_layout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.dhcp_mode_combo = QComboBox()
        self.dhcp_mode_combo.addItems(["Server", "Client"])
        self.dhcp_mode_combo.currentTextChanged.connect(self._on_dhcp_mode_changed)
        mode_form_layout.addRow("DHCP Mode:", self.dhcp_mode_combo)
        dropdown_layout.addWidget(self.dhcp_mode_container)

        self.dhcp_mode_container.setVisible(False)
        self.dhcp_toggle_container.setVisible(False)

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
        config_layout.addWidget(self.vxlan_config_widget)
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
        self.increment_checkbox_vxlan = QCheckBox("VXLAN")
        
        # Connect individual checkboxes to update "Enable All" state
        self.increment_checkbox_mac.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_ipv4.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_ipv6.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_gateway.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_vlan.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_loopback.toggled.connect(self._on_individual_checkbox_toggled)
        self.increment_checkbox_vxlan.toggled.connect(self._on_individual_checkbox_toggled)
        
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
        checkbox_count_layout.addWidget(self.increment_checkbox_vxlan)
        checkbox_count_layout.addSpacing(20)  # Add some space before count
        checkbox_count_layout.addWidget(QLabel("Count:"))
        checkbox_count_layout.addWidget(self.increment_count)
        checkbox_count_layout.addStretch()  # Push everything to the left
        
        increment_layout.addRow("Increment:", checkbox_count_layout)
        
        # Address Increment Selection - Split into two rows for better layout
        
        # Create a vertical layout to hold both rows
        increment_selection_container = QVBoxLayout()
        increment_selection_container.setSpacing(5)
        
        # First row: IP and Loopback fields (IPv4, IPv6, Loopback IPv4, Loopback IPv6, MAC, Gateway)
        increment_selection_row1 = QHBoxLayout()
        increment_selection_row1.setSpacing(10)
        
        # Second row: VXLAN fields only
        increment_selection_row2 = QHBoxLayout()
        increment_selection_row2.setSpacing(10)
        
        # IPv4 Octet Selection
        ipv4_label = QLabel("IPv4:")
        self.ipv4_octet_combo = QComboBox()
        self.ipv4_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        # Default to the 3rd octet — walks the /24 network portion so
        # each multi-device-on-the-same-NIC gets its own subnet
        # (192.168.0.2 → 192.168.1.2 → 192.168.2.2), which is the
        # standard pattern when paired with VLAN increment. Host-portion
        # (4th octet) increment would put every device on the same L3
        # network, defeating the point.
        self.ipv4_octet_combo.setCurrentIndex(1)
        self.ipv4_octet_combo.setFixedWidth(70)
        self.ipv4_octet_combo.setToolTip("Select which octet to increment (e.g., 192.168.X.2)")
        
        # IPv6 Hextet Selection
        ipv6_label = QLabel("IPv6:")
        self.ipv6_hextet_combo = QComboBox()
        self.ipv6_hextet_combo.addItems(["8th", "7th", "6th", "5th", "4th", "3rd", "2nd", "1st"])
        # Default to the 3rd hextet — IPv6 equivalent of varying the
        # subnet portion of a /48 (so each device lands on its own
        # /64 within a /48): 2001:db8::1 → 2001:db8:1::1 → 2001:db8:2::1.
        # Matches the IPv4 "3rd octet" default above.
        self.ipv6_hextet_combo.setCurrentIndex(5)
        self.ipv6_hextet_combo.setFixedWidth(70)
        self.ipv6_hextet_combo.setToolTip("Select which hextet to increment (e.g., 2001:db8:X::1)")
        
        # MAC Byte Selection
        mac_label = QLabel("MAC:")
        self.mac_byte_combo = QComboBox()
        self.mac_byte_combo.addItems(["6th", "5th", "4th", "3rd", "2nd", "1st"])
        # Default to the 6th (last) byte. Unlike IP, where "network
        # portion" means a different subnet per device, MAC has no
        # network/host split — the OUI (first 3 bytes) identifies the
        # vendor and should stay constant; the last 3 bytes are the
        # device-unique NIC ID. Walking the last byte keeps the OUI
        # intact and just enumerates devices (00:11:22:33:44:55 → :56 → …).
        self.mac_byte_combo.setCurrentIndex(0)
        self.mac_byte_combo.setFixedWidth(70)
        self.mac_byte_combo.setToolTip("Select which byte to increment (e.g., 00:11:22:33:44:XX)")
        
        # Gateway Octet Selection
        gateway_label = QLabel("Gateway:")
        self.gateway_octet_combo = QComboBox()
        self.gateway_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        # Default to the 3rd octet — gateway must move with the device's
        # subnet, so it matches the IPv4 combo's "3rd octet" default
        # (192.168.0.1 → 192.168.1.1 → 192.168.2.1).
        self.gateway_octet_combo.setCurrentIndex(1)
        self.gateway_octet_combo.setFixedWidth(70)
        self.gateway_octet_combo.setToolTip("Select which octet to increment (e.g., 192.168.X.1)")
        
        # Loopback IP Octet/Hextet Selection
        loopback_ipv4_label = QLabel("Loopback IPv4:")
        self.loopback_ipv4_octet_combo = QComboBox()
        self.loopback_ipv4_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        # Default to the 3rd octet — keeps loopback assignment aligned
        # with the device's subnet pattern (192.255.0.1 → 192.255.1.1 →
        # 192.255.2.1). Old index `3` mapped to the 1st octet and the
        # comment claiming "4th octet" was simply wrong.
        self.loopback_ipv4_octet_combo.setCurrentIndex(1)
        self.loopback_ipv4_octet_combo.setFixedWidth(70)
        self.loopback_ipv4_octet_combo.setToolTip("Select which octet to increment for loopback IPv4 (e.g., 192.255.X.1)")

        loopback_ipv6_label = QLabel("Loopback IPv6:")
        self.loopback_ipv6_hextet_combo = QComboBox()
        self.loopback_ipv6_hextet_combo.addItems(["8th", "7th", "6th", "5th", "4th", "3rd", "2nd", "1st"])
        # Default to the 3rd hextet — IPv6 counterpart of the loopback
        # IPv4 "3rd octet" default; varies the network portion so each
        # device gets a distinct /64 loopback range. Old index `7`
        # mapped to the 1st hextet, opposite of its comment's intent.
        self.loopback_ipv6_hextet_combo.setCurrentIndex(5)
        self.loopback_ipv6_hextet_combo.setFixedWidth(70)
        self.loopback_ipv6_hextet_combo.setToolTip("Select which hextet to increment for loopback IPv6 (e.g., 2001:ff00:X::1)")
        
        # VXLAN Increment Selection
        vxlan_vni_label = QLabel("VNI:")
        self.vxlan_vni_increment_combo = QComboBox()
        self.vxlan_vni_increment_combo.addItems(["+1", "+10", "+100", "+1000"])
        self.vxlan_vni_increment_combo.setCurrentIndex(0)  # Default to +1
        self.vxlan_vni_increment_combo.setFixedWidth(70)
        self.vxlan_vni_increment_combo.setToolTip("Increment step for VNI (e.g., 5000, 5001, 5002...)")
        
        vxlan_local_label = QLabel("VXLAN Local:")
        self.vxlan_local_octet_combo = QComboBox()
        self.vxlan_local_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        # Default to the 3rd octet — keeps each VXLAN tunnel's local
        # endpoint on a distinct underlay subnet, consistent with the
        # IPv4 and gateway "3rd octet" defaults.
        self.vxlan_local_octet_combo.setCurrentIndex(1)
        self.vxlan_local_octet_combo.setFixedWidth(70)
        self.vxlan_local_octet_combo.setToolTip("Select which octet to increment for VXLAN local endpoint (e.g., 192.255.X.1)")

        vxlan_remote_label = QLabel("VXLAN Remote:")
        self.vxlan_remote_octet_combo = QComboBox()
        self.vxlan_remote_octet_combo.addItems(["4th", "3rd", "2nd", "1st"])
        # Default to the 3rd octet — see VXLAN local combo above.
        self.vxlan_remote_octet_combo.setCurrentIndex(1)
        self.vxlan_remote_octet_combo.setFixedWidth(70)
        self.vxlan_remote_octet_combo.setToolTip("Select which octet to increment for VXLAN remote endpoint (e.g., 192.168.X.1)")
        
        vxlan_udp_label = QLabel("VXLAN UDP:")
        self.vxlan_udp_increment_combo = QComboBox()
        self.vxlan_udp_increment_combo.addItems(["+1", "+10", "+100"])
        self.vxlan_udp_increment_combo.setCurrentIndex(0)  # Default to +1
        self.vxlan_udp_increment_combo.setFixedWidth(70)
        self.vxlan_udp_increment_combo.setToolTip("Increment step for UDP port (e.g., 4789, 4790, 4791...)")
        
        # First row: IP and Loopback fields (IPv4, IPv6, Loopback IPv4, Loopback IPv6, MAC, Gateway)
        increment_selection_row1.addWidget(ipv4_label)
        increment_selection_row1.addWidget(self.ipv4_octet_combo)
        increment_selection_row1.addWidget(ipv6_label)
        increment_selection_row1.addWidget(self.ipv6_hextet_combo)
        increment_selection_row1.addWidget(loopback_ipv4_label)
        increment_selection_row1.addWidget(self.loopback_ipv4_octet_combo)
        increment_selection_row1.addWidget(loopback_ipv6_label)
        increment_selection_row1.addWidget(self.loopback_ipv6_hextet_combo)
        increment_selection_row1.addWidget(mac_label)
        increment_selection_row1.addWidget(self.mac_byte_combo)
        increment_selection_row1.addWidget(gateway_label)
        increment_selection_row1.addWidget(self.gateway_octet_combo)
        increment_selection_row1.addStretch()
        
        # Second row: VXLAN fields only
        increment_selection_row2.addWidget(vxlan_vni_label)
        increment_selection_row2.addWidget(self.vxlan_vni_increment_combo)
        increment_selection_row2.addWidget(vxlan_local_label)
        increment_selection_row2.addWidget(self.vxlan_local_octet_combo)
        increment_selection_row2.addWidget(vxlan_remote_label)
        increment_selection_row2.addWidget(self.vxlan_remote_octet_combo)
        increment_selection_row2.addWidget(vxlan_udp_label)
        increment_selection_row2.addWidget(self.vxlan_udp_increment_combo)
        increment_selection_row2.addStretch()
        
        # Add both rows to the container
        increment_selection_container.addLayout(increment_selection_row1)
        increment_selection_container.addLayout(increment_selection_row2)
        
        # Create a widget to hold the container layout
        increment_selection_widget = QWidget()
        increment_selection_widget.setLayout(increment_selection_container)
        
        increment_layout.addRow("Position:", increment_selection_widget)
        
        # Add all groups to scroll layout
        self.scroll_layout.addWidget(interface_group)
        self.scroll_layout.addWidget(ip_group)
        self.scroll_layout.addWidget(protocol_group)
        self.scroll_layout.addWidget(increment_group)
        

    def _create_protocol_config_widgets(self):
        """Create protocol-specific configuration widgets."""
        # BGP Configuration Widget with 2-column layout
        self.bgp_config_widget = QWidget()
        bgp_main_layout = QVBoxLayout(self.bgp_config_widget)
        bgp_main_layout.setSpacing(10)
        bgp_main_layout.setContentsMargins(0, 0, 0, 0)

        # Create three-column layout for first row (Use Loopback, Local AS, Remote AS)
        bgp_row1_layout = QHBoxLayout()
        bgp_row1_layout.setSpacing(20)
        
        # Left: Use Loopback checkbox
        bgp_loopback_layout = QFormLayout()
        bgp_loopback_layout.setSpacing(8)
        self.bgp_use_loopback_checkbox = QCheckBox()
        self.bgp_use_loopback_checkbox.setToolTip("Use loopback IP address instead of interface IP for BGP neighbor establishment (update-source)")
        self.bgp_use_loopback_checkbox.setEnabled(False)
        self.bgp_use_loopback_checkbox.toggled.connect(self._on_bgp_use_loopback_toggled)
        bgp_loopback_layout.addRow("Use Loopback:", self.bgp_use_loopback_checkbox)
        
        # Middle: Local AS
        bgp_local_layout = QFormLayout()
        bgp_local_layout.setSpacing(8)
        self.bgp_local_as_input = QLineEdit("65000")
        self.bgp_local_as_input.setPlaceholderText("Local AS Number")
        self.bgp_local_as_input.setValidator(QIntValidator(1, 2147483647, self))
        self.bgp_local_as_input.setEnabled(False)
        # Connect Local AS changes to sync Remote AS when VXLAN is enabled
        self.bgp_local_as_input.textChanged.connect(self._on_bgp_local_as_changed)
        bgp_local_layout.addRow("Local AS:", self.bgp_local_as_input)
        
        # Right: Remote AS
        bgp_remote_as_layout = QFormLayout()
        bgp_remote_as_layout.setSpacing(8)
        self.bgp_remote_as_input = QLineEdit("65001")
        self.bgp_remote_as_input.setPlaceholderText("Remote AS Number")
        self.bgp_remote_as_input.setValidator(QIntValidator(1, 2147483647, self))
        self.bgp_remote_as_input.setEnabled(False)
        bgp_remote_as_layout.addRow("Remote AS:", self.bgp_remote_as_input)
        
        # Add to first row
        bgp_row1_layout.addLayout(bgp_loopback_layout, 1)
        bgp_row1_layout.addLayout(bgp_local_layout, 1)
        bgp_row1_layout.addLayout(bgp_remote_as_layout, 1)
        bgp_main_layout.addLayout(bgp_row1_layout)
        
        # Create two-column layout for second row (IPv4 and IPv6 Remote Loopback IPs)
        bgp_row2_layout = QHBoxLayout()
        bgp_row2_layout.setSpacing(20)
        
        # Left: IPv4 Remote Loopback IP
        bgp_ipv4_loopback_layout = QFormLayout()
        bgp_ipv4_loopback_layout.setSpacing(8)
        self.bgp_remote_loopback_ip_input = QLineEdit("192.168.250.1")
        self.bgp_remote_loopback_ip_input.setPlaceholderText("Remote Loopback IP (e.g., 192.168.250.1)")
        self.bgp_remote_loopback_ip_input.setEnabled(False)
        bgp_ipv4_loopback_layout.addRow("IPv4 Remote Loopback:", self.bgp_remote_loopback_ip_input)
        
        # Right: IPv6 Remote Loopback IP
        bgp_ipv6_loopback_layout = QFormLayout()
        bgp_ipv6_loopback_layout.setSpacing(8)
        self.bgp_remote_loopback_ipv6_input = QLineEdit("2001:ff00:250::1")
        self.bgp_remote_loopback_ipv6_input.setPlaceholderText("Remote Loopback IPv6 (e.g., 2001:ff00:250::1)")
        self.bgp_remote_loopback_ipv6_input.setEnabled(False)
        bgp_ipv6_loopback_layout.addRow("IPv6 Remote Loopback:", self.bgp_remote_loopback_ipv6_input)
        
        # Add to second row
        bgp_row2_layout.addLayout(bgp_ipv4_loopback_layout, 1)
        bgp_row2_layout.addLayout(bgp_ipv6_loopback_layout, 1)
        bgp_main_layout.addLayout(bgp_row2_layout)

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

        # IPv4 configuration container
        self.dhcp_ipv4_container = QWidget()
        dhcp_ipv4_layout = QHBoxLayout(self.dhcp_ipv4_container)
        dhcp_ipv4_layout.setSpacing(20)

        dhcp_left_layout = QFormLayout()
        dhcp_left_layout.setSpacing(8)

        # DHCP server defaults live in the 172.16.30.0/24 range (v0.5.225).
        # This is a deliberate offset from the regular device-interface
        # default (192.168.0.0/24) so a DHCP-server device doesn't share
        # a subnet with regular BGP/OSPF devices — makes the "which /24
        # is the DHCP subnet" question unambiguous in the lab.
        self.dhcp_pool_start_input = QLineEdit("172.16.30.10")
        self.dhcp_pool_start_input.setPlaceholderText("Pool Start")
        self.dhcp_pool_start_input.setEnabled(False)
        dhcp_left_layout.addRow("Pool Start:", self.dhcp_pool_start_input)

        self.dhcp_gateway_route_input = QLineEdit("172.16.30.0/24")
        self.dhcp_gateway_route_input.setPlaceholderText("Gateway Route CIDR (e.g. 172.16.30.0/24)")
        self.dhcp_gateway_route_input.setEnabled(False)
        dhcp_left_layout.addRow("Gateway Route:", self.dhcp_gateway_route_input)

        dhcp_right_layout = QFormLayout()
        dhcp_right_layout.setSpacing(8)

        self.dhcp_pool_end_input = QLineEdit("172.16.30.200")
        self.dhcp_pool_end_input.setPlaceholderText("Pool End")
        self.dhcp_pool_end_input.setEnabled(False)
        dhcp_right_layout.addRow("Pool End:", self.dhcp_pool_end_input)

        self.dhcp_lease_time_input = QLineEdit("3600")
        self.dhcp_lease_time_input.setPlaceholderText("seconds")
        self.dhcp_lease_time_input.setEnabled(False)
        dhcp_right_layout.addRow("Lease Time:", self.dhcp_lease_time_input)

        dhcp_ipv4_layout.addLayout(dhcp_left_layout, 1)
        dhcp_ipv4_layout.addLayout(dhcp_right_layout, 1)

        # IPv6 configuration container arranged horizontally
        self.dhcp_ipv6_container = QWidget()
        dhcp_ipv6_main_layout = QHBoxLayout(self.dhcp_ipv6_container)
        dhcp_ipv6_main_layout.setSpacing(20)

        dhcp_ipv6_left = QFormLayout()
        dhcp_ipv6_left.setSpacing(8)

        self.dhcp6_pool_start_input = QLineEdit("2001:db8::100")
        self.dhcp6_pool_start_input.setPlaceholderText("IPv6 Pool Start")
        self.dhcp6_pool_start_input.setEnabled(False)
        dhcp_ipv6_left.addRow("IPv6 Pool Start:", self.dhcp6_pool_start_input)

        self.dhcp6_pool_end_input = QLineEdit("2001:db8::1ff")
        self.dhcp6_pool_end_input.setPlaceholderText("IPv6 Pool End")
        self.dhcp6_pool_end_input.setEnabled(False)
        dhcp_ipv6_left.addRow("IPv6 Pool End:", self.dhcp6_pool_end_input)

        self.dhcp6_prefix_input = QLineEdit("64")
        self.dhcp6_prefix_input.setPlaceholderText("Prefix Length (e.g. 64)")
        self.dhcp6_prefix_input.setEnabled(False)
        dhcp_ipv6_left.addRow("IPv6 Prefix:", self.dhcp6_prefix_input)

        dhcp_ipv6_right = QFormLayout()
        dhcp_ipv6_right.setSpacing(8)

        self.dhcp6_server_ip_input = QLineEdit("2001:db8::1")
        self.dhcp6_server_ip_input.setPlaceholderText("Server IPv6 Address (RA source)")
        self.dhcp6_server_ip_input.setEnabled(False)
        dhcp_ipv6_right.addRow("Server IPv6:", self.dhcp6_server_ip_input)

        self.dhcp6_gateway_input = QLineEdit()
        self.dhcp6_gateway_input.setPlaceholderText("Upstream IPv6 Gateway (optional)")
        self.dhcp6_gateway_input.setEnabled(False)
        dhcp_ipv6_right.addRow("IPv6 Gateway:", self.dhcp6_gateway_input)

        self.dhcp6_gateway_route_input = QLineEdit()
        self.dhcp6_gateway_route_input.setPlaceholderText("IPv6 Gateway Routes (comma separated, optional)")
        self.dhcp6_gateway_route_input.setEnabled(False)
        dhcp_ipv6_right.addRow("IPv6 Routes:", self.dhcp6_gateway_route_input)

        self.dhcp6_lease_time_input = QLineEdit("3600")
        self.dhcp6_lease_time_input.setPlaceholderText("IPv6 Lease Time (seconds)")
        self.dhcp6_lease_time_input.setEnabled(False)
        dhcp_ipv6_right.addRow("IPv6 Lease Time:", self.dhcp6_lease_time_input)

        dhcp_ipv6_main_layout.addLayout(dhcp_ipv6_left, 1)
        dhcp_ipv6_main_layout.addLayout(dhcp_ipv6_right, 1)

        dhcp_main_layout.addWidget(self.dhcp_ipv4_container)
        dhcp_main_layout.addWidget(self.dhcp_ipv6_container)

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

        # VXLAN Configuration Widget
        self.vxlan_config_widget = QWidget()
        vxlan_main_layout = QVBoxLayout(self.vxlan_config_widget)
        vxlan_main_layout.setSpacing(4)
        vxlan_main_layout.setContentsMargins(0, 0, 0, 0)

        # Row 1: VNI | UDP Port
        vni_udp_row = QHBoxLayout()
        vni_udp_row.setSpacing(10)
        
        vni_label = QLabel("VNI:")
        self.vxlan_vni_input = QLineEdit("5000")
        self.vxlan_vni_input.setPlaceholderText("1-16777215")
        self.vxlan_vni_input.setValidator(QIntValidator(1, 16777215, self))
        self.vxlan_vni_input.setEnabled(False)
        vni_udp_row.addWidget(vni_label)
        vni_udp_row.addWidget(self.vxlan_vni_input)
        vni_udp_row.addStretch()
        
        udp_label = QLabel("UDP Port:")
        self.vxlan_udp_port_input = QLineEdit("4789")
        self.vxlan_udp_port_input.setPlaceholderText("UDP Port (default 4789)")
        self.vxlan_udp_port_input.setValidator(QIntValidator(1, 65535, self))
        self.vxlan_udp_port_input.setEnabled(False)
        vni_udp_row.addWidget(udp_label)
        vni_udp_row.addWidget(self.vxlan_udp_port_input)
        vxlan_main_layout.addLayout(vni_udp_row)

        # Row 2: Local Endpoint | Remote Endpoint
        endpoint_row = QHBoxLayout()
        endpoint_row.setSpacing(10)
        
        local_label = QLabel("Local Endpoint:")
        self.vxlan_local_ip_input = QLineEdit()
        self.vxlan_local_ip_input.setPlaceholderText("Local VTEP IP (optional)")
        self.vxlan_local_ip_input.setEnabled(False)
        endpoint_row.addWidget(local_label)
        endpoint_row.addWidget(self.vxlan_local_ip_input)
        endpoint_row.addStretch()
        
        remote_label = QLabel("Remote Endpoint(s):")
        self.vxlan_remote_input = QLineEdit()
        self.vxlan_remote_input.setPlaceholderText("Remote VTEP IPs (comma separated)")
        self.vxlan_remote_input.setEnabled(False)
        endpoint_row.addWidget(remote_label)
        endpoint_row.addWidget(self.vxlan_remote_input)
        vxlan_main_layout.addLayout(endpoint_row)

        # Row 3: Bridge SVI IP | VLAN ID(VLAN->VNI)
        svi_vlan_row = QHBoxLayout()
        svi_vlan_row.setSpacing(10)
        
        svi_label = QLabel("Bridge SVI IP:")
        self.vxlan_bridge_svi_ip_input = QLineEdit()
        self.vxlan_bridge_svi_ip_input.setPlaceholderText("e.g., 10.0.0.100/24 (optional)")
        self.vxlan_bridge_svi_ip_input.setEnabled(False)
        svi_vlan_row.addWidget(svi_label)
        svi_vlan_row.addWidget(self.vxlan_bridge_svi_ip_input)
        svi_vlan_row.addStretch()
        
        vlan_id_label = QLabel("VLAN ID (VLAN→VNI):")
        self.vxlan_vlan_id_input = QLineEdit()
        self.vxlan_vlan_id_input.setPlaceholderText("VLAN ID (optional)")
        self.vxlan_vlan_id_input.setValidator(QIntValidator(1, 4094, self))
        self.vxlan_vlan_id_input.setEnabled(False)
        svi_vlan_row.addWidget(vlan_id_label)
        svi_vlan_row.addWidget(self.vxlan_vlan_id_input)
        vxlan_main_layout.addLayout(svi_vlan_row)

        # Row 4: (Some Text) | (Maps VLAN to VNI for VLAN-aware VXLAN)
        help_row = QHBoxLayout()
        help_row.setSpacing(10)
        
        left_help = QLabel("")
        left_help.setStyleSheet("font-size: 9px; color: gray;")
        left_help.setEnabled(False)
        help_row.addWidget(left_help)
        help_row.addStretch()
        
        vlan_id_help = QLabel("(Maps VLAN to VNI for VLAN-aware VXLAN)")
        vlan_id_help.setStyleSheet("font-size: 9px; color: gray;")
        vlan_id_help.setEnabled(False)
        help_row.addWidget(vlan_id_help)
        vxlan_main_layout.addLayout(help_row)

        # Initially hide all protocol config widgets
        self.bgp_config_widget.setVisible(False)
        self.ospf_config_widget.setVisible(False)
        self.isis_config_widget.setVisible(False)
        self.dhcp_config_widget.setVisible(False)
        self.vxlan_config_widget.setVisible(False)
        self.rocev2_config_widget.setVisible(False)

        self._update_dhcp_field_states()
        self._update_protocol_toggle_visibility()

    def _on_template_changed(self, idx: int):
        """Apply the selected template's defaults to the form.

        Updates the summary line and walks the template registry to
        push each field into the matching widget. The 'Custom' option
        is a sentinel — it leaves the form alone for from-scratch
        entry.
        """
        key = self.template_combo.itemData(idx)
        if not key:
            self._template_summary_label.setText(
                "Pick a template to pre-fill the form, then tweak as needed."
            )
            return
        # Find the summary text from the metadata cache.
        meta = next((t for t in self._templates_meta if t["key"] == key), None)
        if meta:
            self._template_summary_label.setText(meta["summary"])
        try:
            from utils.device_templates import apply_to_dialog
            apply_to_dialog(self, key)
        except Exception as exc:
            self._template_summary_label.setText(
                f"Template '{key}' failed to apply: {exc}"
            )

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
        
        # Add iBGP and eBGP as separate protocol options when BGP is enabled
        if self.bgp_enable_checkbox.isChecked():
            enabled_protocols.append("iBGP")
            enabled_protocols.append("eBGP")
        if self.ospf_enable_checkbox.isChecked():
            enabled_protocols.append("OSPF")
        if self.isis_enable_checkbox.isChecked():
            enabled_protocols.append("ISIS")
        if self.dhcp_enable_checkbox.isChecked():
            enabled_protocols.append("DHCP")
        if self.rocev2_enable_checkbox.isChecked():
            enabled_protocols.append("ROCEv2")
        if self.vxlan_enable_checkbox.isChecked():
            enabled_protocols.append("VXLAN")
        
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
        else:
            self._update_dhcp_controls_visibility()
        self._update_protocol_toggle_visibility()

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
            self.vxlan_config_widget.setVisible(False)
            self.rocev2_config_widget.setVisible(False)
            
            # Disable all protocol fields first
            self._disable_all_protocol_fields()
            
            # Show the selected protocol config widget and enable its fields
            if protocol in ["iBGP", "eBGP"]:
                if self.bgp_enable_checkbox.isChecked():
                    self.bgp_config_widget.setVisible(True)
                    self._enable_bgp_fields(protocol)
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
            elif protocol == "VXLAN" and self.vxlan_enable_checkbox.isChecked():
                self.vxlan_config_widget.setVisible(True)
                self._enable_vxlan_fields()
            self._update_protocol_toggle_visibility()
            if protocol == "DHCP":
                self._update_dhcp_field_states()
        finally:
            self._suppress_protocol_change = False

    def _toggle_ip_fields(self):
        """Enable/disable IP fields based on checkbox state.

        Also re-populates any cleared field with its canonical default
        when the user enables the corresponding address family. The
        DHCP-server-mode path clears IPv4/IPv6 fields explicitly
        (line ~1322 below); without this restore, toggling back out
        of DHCP and re-ticking the address-family checkbox left the
        user staring at empty inputs — they'd have to remember the
        defaults and re-type them, which was the user-reported gap
        ("when ipv6 is enabled on device enable preconfigured ipv6
        address and gateway, similar to ipv4 address").
        """
        ipv4_on = self.ipv4_checkbox.isChecked()
        ipv6_on = self.ipv6_checkbox.isChecked()

        # Enable/disable inputs
        self.ipv4_input.setEnabled(ipv4_on)
        self.ipv4_mask_input.setEnabled(ipv4_on)
        self.ipv4_gateway_input.setEnabled(ipv4_on)
        self.ipv6_input.setEnabled(ipv6_on)
        self.ipv6_mask_input.setEnabled(ipv6_on)
        self.ipv6_gateway_input.setEnabled(ipv6_on)
        self.loopback_ipv4_input.setEnabled(ipv4_on)
        self.loopback_ipv6_input.setEnabled(ipv6_on)

        # Re-populate canonical defaults if the user just enabled an
        # address family with empty fields. The defaults match the
        # initial QLineEdit constructor values so an unticked-then-
        # reticked checkbox returns to the same state a fresh dialog
        # would show.
        IPV4_DEFAULTS = {
            self.ipv4_input:         "192.168.0.2",
            self.ipv4_mask_input:    "24",
            self.ipv4_gateway_input: "192.168.0.1",
            self.loopback_ipv4_input: "192.255.0.1",
        }
        IPV6_DEFAULTS = {
            self.ipv6_input:         "2001:db8::2",
            self.ipv6_mask_input:    "64",
            self.ipv6_gateway_input: "2001:db8::1",
            self.loopback_ipv6_input: "2001:ff00::1",
        }

        def _restore_if_empty(family_on, defaults):
            if not family_on:
                return
            for widget, default_value in defaults.items():
                if not widget.text().strip():
                    widget.setText(default_value)

        _restore_if_empty(ipv4_on, IPV4_DEFAULTS)
        _restore_if_empty(ipv6_on, IPV6_DEFAULTS)
        # OSPF IPv4/IPv6 toggles are now in Select Protocol section, no need to sync here
        # No manual underlay selection; it matches the main interface automatically.

    def _disable_all_protocol_fields(self):
        """Disable all protocol configuration fields."""
        # BGP fields
        self.bgp_local_as_input.setEnabled(False)
        self.bgp_remote_as_input.setEnabled(False)
        if hasattr(self, "bgp_use_loopback_checkbox"):
            self.bgp_use_loopback_checkbox.setEnabled(False)
        if hasattr(self, "bgp_remote_loopback_ip_input"):
            self.bgp_remote_loopback_ip_input.setEnabled(False)
        if hasattr(self, "bgp_remote_loopback_ipv6_input"):
            self.bgp_remote_loopback_ipv6_input.setEnabled(False)
        # BGP IPv4/IPv6 toggles are now in Select Protocol section
        
        # OSPF fields
        self.ospf_area_id_input.setEnabled(False)
        self.ospf_router_id_input.setEnabled(False)
        self.ospf_hello_interval_input.setEnabled(False)
        self.ospf_dead_interval_input.setEnabled(False)
        self.ospf_graceful_restart_checkbox.setEnabled(False)
        # OSPF IPv4/IPv6 toggles are now in Select Protocol section
        
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

        # VXLAN fields
        if hasattr(self, "vxlan_vni_input"):
            self.vxlan_vni_input.setEnabled(False)
        if hasattr(self, "vxlan_remote_input"):
            self.vxlan_remote_input.setEnabled(False)
        if hasattr(self, "vxlan_local_ip_input"):
            self.vxlan_local_ip_input.setEnabled(False)
        if hasattr(self, "vxlan_bridge_svi_ip_input"):
            self.vxlan_bridge_svi_ip_input.setEnabled(False)
        if hasattr(self, "vxlan_underlay_iface_input"):
            self.vxlan_underlay_iface_input.setEnabled(False)
        if hasattr(self, "vxlan_udp_port_input"):
            self.vxlan_udp_port_input.setEnabled(False)
        if hasattr(self, "vxlan_vlan_id_input"):
            self.vxlan_vlan_id_input.setEnabled(False)

    def _on_bgp_local_as_changed(self):
        """Handle Local AS changes - sync Remote AS when iBGP is selected."""
        # Get current protocol selection
        current_protocol = self.protocol_dropdown.currentText() if hasattr(self, "protocol_dropdown") else ""
        if current_protocol == "iBGP" and self.bgp_enable_checkbox.isChecked():
            local_as = self.bgp_local_as_input.text().strip()
            if local_as:
                self.bgp_remote_as_input.setText(local_as)
    
    def _on_bgp_use_loopback_toggled(self, checked):
        """Enable/disable Remote Loopback IP inputs based on 'Use Loopback IP' checkbox."""
        if hasattr(self, "bgp_remote_loopback_ip_input"):
            self.bgp_remote_loopback_ip_input.setEnabled(checked)
        if hasattr(self, "bgp_remote_loopback_ipv6_input"):
            self.bgp_remote_loopback_ipv6_input.setEnabled(checked)
    
    def _enable_bgp_fields(self, protocol="eBGP"):
        """Enable BGP configuration fields.
        
        Args:
            protocol: "iBGP" or "eBGP" - determines if Remote AS should be synced
        """
        self.bgp_local_as_input.setEnabled(True)
        # If iBGP is selected, Remote AS should be disabled and auto-synced to Local AS
        if protocol == "iBGP":
            # Sync Remote AS to Local AS for iBGP
            local_as = self.bgp_local_as_input.text().strip()
            if local_as:
                self.bgp_remote_as_input.setText(local_as)
            self.bgp_remote_as_input.setEnabled(False)
            self.bgp_remote_as_input.setToolTip("iBGP requires same ASN - Remote AS automatically matches Local AS")
        else:
            # For eBGP, Remote AS is independent
            self.bgp_remote_as_input.setEnabled(True)
            self.bgp_remote_as_input.setToolTip("")
        # Enable "Use Loopback IP" checkbox and Remote Loopback IP field
        if hasattr(self, "bgp_use_loopback_checkbox"):
            self.bgp_use_loopback_checkbox.setEnabled(True)
        if hasattr(self, "bgp_remote_loopback_ip_input"):
            self.bgp_remote_loopback_ip_input.setEnabled(self.bgp_use_loopback_checkbox.isChecked())
        if hasattr(self, "bgp_remote_loopback_ipv6_input"):
            self.bgp_remote_loopback_ipv6_input.setEnabled(self.bgp_use_loopback_checkbox.isChecked())
        # BGP IPv4/IPv6 toggles are now in Select Protocol section, no need to sync
        self._on_bgp_toggle_changed()

    def _enable_ospf_fields(self):
        """Enable OSPF configuration fields."""
        self.ospf_area_id_input.setEnabled(True)
        self.ospf_router_id_input.setEnabled(True)
        self.ospf_hello_interval_input.setEnabled(True)
        self.ospf_dead_interval_input.setEnabled(True)
        self.ospf_graceful_restart_checkbox.setEnabled(True)
        # OSPF IPv4/IPv6 toggles are now in Select Protocol section, no need to sync
        self._on_ospf_toggle_changed()

    def _enable_isis_fields(self):
        """Enable ISIS configuration fields."""
        self.isis_area_id_input.setEnabled(True)
        self.isis_system_id_input.setEnabled(True)
        self.isis_hello_interval_input.setEnabled(True)
        if hasattr(self, "isis_toggle_ipv4"):
            self.isis_toggle_ipv4.blockSignals(True)
            self.isis_toggle_ipv4.setChecked(self._isis_ipv4_enabled_state)
            self.isis_toggle_ipv4.blockSignals(False)
        if hasattr(self, "isis_toggle_ipv6"):
            self.isis_toggle_ipv6.blockSignals(True)
            self.isis_toggle_ipv6.setChecked(self._isis_ipv6_enabled_state)
            self.isis_toggle_ipv6.blockSignals(False)
        self._on_isis_toggle_changed()

    def _enable_dhcp_fields(self):
        """Enable DHCP configuration fields."""
        self.dhcp_mode_combo.setEnabled(True)
        self._on_dhcp_mode_changed()

    def _update_dhcp_controls_visibility(self):
        """Show or hide DHCP mode/toggle controls based on current protocol selection."""
        show_dhcp_controls = (
            self.dhcp_enable_checkbox.isChecked()
            and self.protocol_dropdown.currentText() == "DHCP"
        )
        current_mode = self.dhcp_mode_combo.currentText().strip().lower() if hasattr(self, "dhcp_mode_combo") else "server"
        if hasattr(self, "dhcp_mode_container"):
            self.dhcp_mode_container.setVisible(show_dhcp_controls)
        if hasattr(self, "dhcp_toggle_container"):
            show_toggle = show_dhcp_controls and current_mode in {"server", "client"}
            self.dhcp_toggle_container.setVisible(show_toggle)

    def _update_protocol_toggle_visibility(self):
        """Manage visibility of protocol-specific address-family toggles."""
        protocol = self.protocol_dropdown.currentText() if hasattr(self, "protocol_dropdown") else ""

        if hasattr(self, "bgp_toggle_container"):
            self.bgp_toggle_container.setVisible(
                protocol in ["iBGP", "eBGP"] and self.bgp_enable_checkbox.isChecked()
            )
        if hasattr(self, "ospf_toggle_container"):
            self.ospf_toggle_container.setVisible(
                protocol == "OSPF" and self.ospf_enable_checkbox.isChecked()
            )
        if hasattr(self, "isis_toggle_container"):
            self.isis_toggle_container.setVisible(
                protocol == "ISIS" and self.isis_enable_checkbox.isChecked()
            )

        self._update_dhcp_controls_visibility()

    def _update_dhcp_field_states(self):
        """Enable or disable DHCP sub-fields based on mode and IPv4/IPv6 toggles."""
        mode = self.dhcp_mode_combo.currentText().strip().lower() if hasattr(self, "dhcp_mode_combo") else "client"
        is_server = mode == "server"

        ipv4_toggle = self.dhcp_ipv4_enabled_checkbox.isChecked()
        ipv6_toggle = self.dhcp_ipv6_enabled_checkbox.isChecked()

        ipv4_active = is_server and ipv4_toggle
        ipv6_active = is_server and ipv6_toggle

        for widget in (
            self.dhcp_pool_start_input,
            self.dhcp_pool_end_input,
            self.dhcp_lease_time_input,
            self.dhcp_gateway_route_input,
        ):
            widget.setEnabled(ipv4_active)

        for widget in (
            self.dhcp6_pool_start_input,
            self.dhcp6_pool_end_input,
            self.dhcp6_prefix_input,
            self.dhcp6_server_ip_input,
            self.dhcp6_gateway_input,
            self.dhcp6_gateway_route_input,
            self.dhcp6_lease_time_input,
        ):
            widget.setEnabled(ipv6_active)

        # Populate sensible defaults when enabling IPv6 DHCP for the first time
        if ipv6_active:
            if not self.dhcp6_pool_start_input.text().strip():
                self.dhcp6_pool_start_input.setText("2001:db8::100")
            if not self.dhcp6_pool_end_input.text().strip():
                self.dhcp6_pool_end_input.setText("2001:db8::1ff")
            if not self.dhcp6_prefix_input.text().strip():
                self.dhcp6_prefix_input.setText("64")
            if not self.dhcp6_server_ip_input.text().strip():
                self.dhcp6_server_ip_input.setText("2001:db8::1")
            if not self.dhcp6_lease_time_input.text().strip():
                self.dhcp6_lease_time_input.setText("3600")

        if hasattr(self, "dhcp_ipv4_container"):
            self.dhcp_ipv4_container.setVisible(ipv4_active)
        if hasattr(self, "dhcp_ipv6_container"):
            self.dhcp_ipv6_container.setVisible(ipv6_active)
        self._update_protocol_toggle_visibility()

    def _on_bgp_toggle_changed(self):
        # BGP IPv4/IPv6 toggles are now in Select Protocol section, no sync needed
        pass

    def _on_ospf_toggle_changed(self):
        # OSPF IPv4/IPv6 toggles are now in Select Protocol section, no sync needed
        pass

    def _on_isis_toggle_changed(self):
        self._isis_ipv4_enabled_state = self.isis_toggle_ipv4.isChecked()
        self._isis_ipv6_enabled_state = self.isis_toggle_ipv6.isChecked()

    def _on_dhcp_mode_changed(self, mode=None):
        """Enable or disable DHCP server fields based on selected mode."""
        if mode is None:
            mode = self.dhcp_mode_combo.currentText() if hasattr(self, "dhcp_mode_combo") else "Client"
        is_server = mode.lower() == "server"
        is_client = mode.lower() == "client"
        # v0.5.230 (audit U client-5): honor the DHCP IPv4 sub-checkbox
        # when Server mode is selected. Pre-fix, switching combo to
        # Server blanket-enabled the IPv4 pool fields even when
        # dhcp_ipv4_enabled_checkbox was off — the operator ended up
        # with pool fields that couldn't actually take effect.
        _ipv4_af_on = (
            self.dhcp_ipv4_enabled_checkbox.isChecked()
            if hasattr(self, "dhcp_ipv4_enabled_checkbox") else True
        )
        enable_server_fields = (
            is_server
            and self.dhcp_mode_combo.isEnabled()
            and _ipv4_af_on
        )
        for widget in (self.dhcp_pool_start_input, self.dhcp_pool_end_input, self.dhcp_lease_time_input, self.dhcp_gateway_route_input):
            widget.setEnabled(enable_server_fields)
        if enable_server_fields:
            # DHCP server template subnet — 172.16.30.0/24 (v0.5.225).
            # Kept in lockstep with the constructor defaults above; both
            # sites populate empty fields with the same values.
            if not self.dhcp_pool_start_input.text().strip():
                self.dhcp_pool_start_input.setText("172.16.30.10")
            if not self.dhcp_pool_end_input.text().strip():
                self.dhcp_pool_end_input.setText("172.16.30.200")
            if not self.dhcp_gateway_route_input.text().strip():
                self.dhcp_gateway_route_input.setText("172.16.30.0/24")
            if hasattr(self, "ipv4_gateway_input") and not self.ipv4_gateway_input.text().strip():
                self.ipv4_gateway_input.setText("172.16.30.1")
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
                # OSPF IPv4/IPv6 toggles are now in Select Protocol section
                if hasattr(self, "ospf_toggle_ipv4"):
                    self.ospf_toggle_ipv4.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                if hasattr(self, "ospf_toggle_ipv6"):
                    self.ospf_toggle_ipv6.setChecked(True)
                if self.bgp_enable_checkbox.isChecked():
                    self.bgp_enable_checkbox.setChecked(False)
                self.bgp_enable_checkbox.setEnabled(False)
            else:
                self.ipv4_checkbox.setEnabled(True)
                self.ipv6_checkbox.setEnabled(True)
                self.ipv4_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                self.ipv6_checkbox.setChecked(getattr(self, "_dhcp_prev_ipv6_checked", False))
                # Restore previously remembered protocol states
                # OSPF IPv4/IPv6 toggles are now in Select Protocol section
                if hasattr(self, "ospf_toggle_ipv4"):
                    self.ospf_toggle_ipv4.setChecked(getattr(self, "_dhcp_prev_ipv4_checked", True))
                if hasattr(self, "ospf_toggle_ipv6"):
                    self.ospf_toggle_ipv6.setChecked(getattr(self, "_dhcp_prev_ipv6_checked", False))
                self.bgp_enable_checkbox.setEnabled(True)
                self.bgp_enable_checkbox.setChecked(getattr(self, "_dhcp_prev_bgp_checked", False))
                self._dhcp_prev_bgp_checked = self.bgp_enable_checkbox.isChecked()
                self._dhcp_prev_ipv4_checked = self.ipv4_checkbox.isChecked()
                self._dhcp_prev_ipv6_checked = self.ipv6_checkbox.isChecked()
        # Update protocol list after any forced toggles
        if not getattr(self, "_dhcp_suppress_updates", False):
            self._on_protocol_enabled_changed()
        self._update_dhcp_field_states()

    def _enable_rocev2_fields(self):
        """Enable ROCEv2 configuration fields."""
        self.rocev2_priority_input.setEnabled(True)
        self.rocev2_dscp_input.setEnabled(True)
        self.rocev2_udp_port_input.setEnabled(True)

    def _enable_vxlan_fields(self):
        """Enable VXLAN configuration fields."""
        self.vxlan_vni_input.setEnabled(True)
        self.vxlan_remote_input.setEnabled(True)
        self.vxlan_local_ip_input.setEnabled(True)
        self.vxlan_udp_port_input.setEnabled(True)
        self.vxlan_vlan_id_input.setEnabled(True)
        if hasattr(self, "vxlan_bridge_svi_ip_input"):
            self.vxlan_bridge_svi_ip_input.setEnabled(True)
            self._apply_default_vxlan_bridge_svi()
        self._apply_default_vxlan_endpoints()

    def _apply_default_vxlan_endpoints(self):
        """Populate VXLAN local/remote endpoints from loopback IPv4 and default remote loopback (192.168.250.1)."""
        self._refresh_vxlan_defaults_from_ipv4(force=True)

    def _apply_default_vxlan_bridge_svi(self, force=False):
        """Populate default Bridge SVI IP when VXLAN is enabled."""
        if not hasattr(self, "vxlan_bridge_svi_ip_input"):
            return
        current_value = self.vxlan_bridge_svi_ip_input.text().strip()
        if force or not current_value or current_value == self._vxlan_bridge_svi_last_default:
            self.vxlan_bridge_svi_ip_input.setText(self._vxlan_bridge_svi_default)
            self._vxlan_bridge_svi_last_default = self._vxlan_bridge_svi_default

    def _refresh_vxlan_defaults_from_ipv4(self, force=False):
        """Refresh VXLAN defaults when IPv4, gateway, or loopback changes."""
        if not self.vxlan_enable_checkbox.isChecked():
            return
        # Use loopback IPv4 for local endpoint instead of interface IPv4
        loopback_ipv4 = self.loopback_ipv4_input.text().strip() if self.ipv4_checkbox.isChecked() else ""
        # For remote endpoint, use default remote loopback starting from 192.168.250.1
        # This will be incremented for multiple devices during creation
        remote_loopback = self._vxlan_remote_base

        current_local = self.vxlan_local_ip_input.text().strip()
        if force or not current_local or current_local == self._vxlan_local_default:
            self.vxlan_local_ip_input.setText(loopback_ipv4)
            self._vxlan_local_default = loopback_ipv4

        current_remote = self.vxlan_remote_input.text().strip()
        if force or not current_remote or current_remote == self._vxlan_remote_default:
            self.vxlan_remote_input.setText(remote_loopback)
            self._vxlan_remote_default = remote_loopback

    def _on_ipv4_text_changed(self, _text):
        self._refresh_vxlan_defaults_from_ipv4()

    def _on_ipv4_gateway_changed(self, _text):
        self._refresh_vxlan_defaults_from_ipv4()

    def _on_enable_all_toggled(self, checked):
        """Handle Enable All checkbox toggle."""
        # Block signals to prevent recursive calls
        self.increment_checkbox_mac.blockSignals(True)
        self.increment_checkbox_ipv4.blockSignals(True)
        self.increment_checkbox_ipv6.blockSignals(True)
        self.increment_checkbox_gateway.blockSignals(True)
        self.increment_checkbox_vlan.blockSignals(True)
        self.increment_checkbox_loopback.blockSignals(True)
        self.increment_checkbox_vxlan.blockSignals(True)
        
        # Set all individual checkboxes to the same state
        self.increment_checkbox_mac.setChecked(checked)
        self.increment_checkbox_ipv4.setChecked(checked)
        self.increment_checkbox_ipv6.setChecked(checked)
        self.increment_checkbox_gateway.setChecked(checked)
        self.increment_checkbox_vlan.setChecked(checked)
        self.increment_checkbox_loopback.setChecked(checked)
        self.increment_checkbox_vxlan.setChecked(checked)
        
        # Unblock signals
        self.increment_checkbox_mac.blockSignals(False)
        self.increment_checkbox_ipv4.blockSignals(False)
        self.increment_checkbox_ipv6.blockSignals(False)
        self.increment_checkbox_gateway.blockSignals(False)
        self.increment_checkbox_vlan.blockSignals(False)
        self.increment_checkbox_loopback.blockSignals(False)
        self.increment_checkbox_vxlan.blockSignals(False)

    def _on_individual_checkbox_toggled(self):
        """Handle individual checkbox toggle to update Enable All state."""
        # Check if all individual checkboxes are checked
        all_checked = (self.increment_checkbox_mac.isChecked() and
                      self.increment_checkbox_ipv4.isChecked() and
                      self.increment_checkbox_ipv6.isChecked() and
                      self.increment_checkbox_gateway.isChecked() and
                      self.increment_checkbox_vlan.isChecked() and
                      self.increment_checkbox_loopback.isChecked() and
                      self.increment_checkbox_vxlan.isChecked())
        
        # Check if any individual checkbox is checked
        any_checked = (self.increment_checkbox_mac.isChecked() or
                      self.increment_checkbox_ipv4.isChecked() or
                      self.increment_checkbox_ipv6.isChecked() or
                      self.increment_checkbox_gateway.isChecked() or
                      self.increment_checkbox_vlan.isChecked() or
                      self.increment_checkbox_loopback.isChecked() or
                      self.increment_checkbox_vxlan.isChecked())
        
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

            # v0.2.86: validate before constructing the full NET.
            # allow_short_area=True because this dialog accepts the
            # AFI.Area shortcut and pads it; the validator treats
            # anything 2-14 bytes as valid short form. Bail out of
            # submit on hard-invalid input (non-hex chars, NSEL!=00,
            # > 20 bytes…) so the operator sees a clear reason
            # instead of FRR rejecting it 5 seconds later.
            try:
                from utils.isis_net import validate_isis_net
                _isis_err = validate_isis_net(area_id_input,
                                              allow_short_area=True)
                if _isis_err:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "Invalid ISIS Area ID / NET",
                        f"'{area_id_input}' is not a valid ISIS Area ID "
                        f"or NET.\n\n"
                        f"Accepted: short form 'AFI.Area' (e.g. "
                        f"'49.0001') which is padded to a full NET, OR "
                        f"a complete NET like '49.0001.0000.0000.0001.00'.\n\n"
                        f"Error: {_isis_err}"
                    )
                    return None
            except Exception:
                # Helper missing or import failed — don't block the
                # operator. Server-side validation still catches it.
                pass

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
                "ipv4_enabled": self.isis_toggle_ipv4.isChecked() if hasattr(self, "isis_toggle_ipv4") else True,
                "ipv6_enabled": self.isis_toggle_ipv6.isChecked() if hasattr(self, "isis_toggle_ipv6") else True
            }
        
        if self.bgp_enable_checkbox.isChecked():
            # Determine BGP mode from protocol dropdown
            bgp_mode = "eBGP"  # Default
            if selected_protocol == "iBGP":
                bgp_mode = "iBGP"
            elif selected_protocol == "eBGP":
                bgp_mode = "eBGP"
            
            # Determine neighbor IP based on "Use Loopback IP" checkbox
            use_loopback_ip = self.bgp_use_loopback_checkbox.isChecked() if hasattr(self, "bgp_use_loopback_checkbox") else False
            
            # Determine IPv4 neighbor IP
            if use_loopback_ip and hasattr(self, "bgp_remote_loopback_ip_input"):
                # Use remote loopback IP when "Use Loopback IP" is checked
                neighbor_ipv4 = self.bgp_remote_loopback_ip_input.text().strip() or "192.168.250.1"
            else:
                # Default: use IPv4 gateway as neighbor IP
                neighbor_ipv4 = ipv4_gateway
            
            # Determine IPv6 neighbor IP
            if use_loopback_ip and hasattr(self, "bgp_remote_loopback_ipv6_input"):
                # Use remote loopback IPv6 when "Use Loopback IP" is checked
                neighbor_ipv6 = self.bgp_remote_loopback_ipv6_input.text().strip() or "2001:ff00:250::1"
            else:
                # Default: use IPv6 gateway as neighbor IP
                neighbor_ipv6 = ipv6_gateway
            
            bgp_config = {
                "bgp_asn": self.bgp_local_as_input.text().strip(),
                "bgp_remote_asn": self.bgp_remote_as_input.text().strip(),
                "mode": bgp_mode,
                "bgp_keepalive": "30",
                "bgp_hold_time": "90",
                "ipv4_enabled": self.bgp_toggle_ipv4.isChecked() if hasattr(self, "bgp_toggle_ipv4") else True,
                "ipv6_enabled": self.bgp_toggle_ipv6.isChecked() if hasattr(self, "bgp_toggle_ipv6") else True,
                "local_ip": ipv4,  # Use device IP as local IP
                "peer_ip": neighbor_ipv4,  # Use remote loopback IP or gateway IP based on checkbox
                "bgp_neighbor_ipv4": neighbor_ipv4,  # Set neighbor IP for BGP configuration
                "bgp_neighbor_ipv6": neighbor_ipv6,  # Set IPv6 neighbor IP for BGP configuration
                "use_loopback_ip": use_loopback_ip,
                "bgp_remote_loopback_ip": self.bgp_remote_loopback_ip_input.text().strip() if hasattr(self, "bgp_remote_loopback_ip_input") else "192.168.250.1",
                "bgp_remote_loopback_ipv6": self.bgp_remote_loopback_ipv6_input.text().strip() if hasattr(self, "bgp_remote_loopback_ipv6_input") else "2001:ff00:250::1"
            }
        
        if self.ospf_enable_checkbox.isChecked():
            # Construct VLAN interface name for OSPF configuration
            base_interface = self.iface_input.text().strip()
            vlan_id = self.vlan_input.text().strip() or "0"
            if vlan_id != "0":
                ospf_interface = f"vlan{vlan_id}"
            else:
                ospf_interface = base_interface
            
            area_id_raw = self.ospf_area_id_input.text().strip() or "0.0.0.0"
            # v0.2.87: validate + normalise. Operators routinely type
            # the plain-integer shortcut ("1" for area 1); normalise
            # to the dotted-decimal FRR puts on the wire so the
            # stored config matches what `show ip ospf` later reports.
            # Bail out of submit on hard-invalid input so the operator
            # sees a clear reason instead of FRR rejecting it later.
            try:
                from utils.ospf_area import validate_ospf_area_id
                _ok, _norm, _err = validate_ospf_area_id(area_id_raw)
                if not _ok:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "Invalid OSPF Area ID",
                        f"'{area_id_raw}' is not a valid OSPF area "
                        f"ID.\n\n"
                        f"Accepted forms:\n"
                        f"  • Integer 0–4294967295 (e.g. '0', '1')\n"
                        f"  • Dotted-decimal A.B.C.D (e.g. "
                        f"'0.0.0.0', '0.0.0.1')\n\n"
                        f"Error: {_err}"
                    )
                    return None
                area_id = _norm  # canonical dotted-decimal
            except Exception:
                # Helper missing — fall back to the raw value.
                area_id = area_id_raw
            ospf_config = {
                "area_id": area_id,
                "area_id_ipv4": area_id,  # Initialize from area_id
                "area_id_ipv6": area_id,  # Initialize from area_id
                "router_id": self.ospf_router_id_input.text().strip(),
                "hello_interval": self.ospf_hello_interval_input.text().strip() or "10",
                "dead_interval": self.ospf_dead_interval_input.text().strip() or "40",
                "graceful_restart": self.ospf_graceful_restart_checkbox.isChecked(),
                "interface": ospf_interface,
                "ipv4_enabled": self.ospf_toggle_ipv4.isChecked() if hasattr(self, "ospf_toggle_ipv4") else True,
                "ipv6_enabled": self.ospf_toggle_ipv6.isChecked() if hasattr(self, "ospf_toggle_ipv6") else True
            }

        if is_dhcp_enabled:
            dhcp_config = {"mode": dhcp_mode_text}
            dhcp_config["ipv4_enabled"] = self.dhcp_ipv4_enabled_checkbox.isChecked()
            dhcp_config["ipv6_enabled"] = self.dhcp_ipv6_enabled_checkbox.isChecked()

            # Derive interface hint for DHCP helpers
            base_interface = self.iface_input.text().strip()
            vlan_id = (self.vlan_input.text().strip() or "0")
            if base_interface:
                dhcp_interface = f"vlan{vlan_id}" if vlan_id and vlan_id != "0" else base_interface
                dhcp_config["interface"] = dhcp_interface

            if dhcp_mode_text == "server":
                ipv4_enabled = dhcp_config["ipv4_enabled"]
                ipv6_enabled = dhcp_config["ipv6_enabled"]

                # v0.5.229 (audit B5): emit ALL scalar keys the dialog
                # owns even when empty. Pre-fix, blank fields didn't
                # reach the merge, so "cleared field" silently fell
                # back to the stored value. Now empty → explicit clear
                # (widgets/devices_tab._merge_dhcp_configs writes the
                # empty value through).
                if ipv4_enabled:
                    pool_start = self.dhcp_pool_start_input.text().strip()
                    pool_end = self.dhcp_pool_end_input.text().strip()
                    lease_time = self.dhcp_lease_time_input.text().strip()
                    gateway_value = ipv4_gateway or ""
                    gateway_route_value = self.dhcp_gateway_route_input.text().strip()

                    dhcp_config["pool_start"] = pool_start
                    dhcp_config["pool_end"] = pool_end
                    dhcp_config["pool_range"] = (
                        f"{pool_start}-{pool_end}" if pool_start and pool_end else ""
                    )
                    dhcp_config["lease_time"] = lease_time
                    dhcp_config["gateway"] = gateway_value
                    dhcp_config["gateway_route"] = gateway_route_value

                if ipv6_enabled:
                    pool6_start = self.dhcp6_pool_start_input.text().strip()
                    pool6_end = self.dhcp6_pool_end_input.text().strip()
                    prefix6 = self.dhcp6_prefix_input.text().strip()
                    lease6 = self.dhcp6_lease_time_input.text().strip()
                    server6 = self.dhcp6_server_ip_input.text().strip()
                    gateway6 = self.dhcp6_gateway_input.text().strip()
                    routes6_text = self.dhcp6_gateway_route_input.text().strip()

                    dhcp_config["ipv6_pool_start"] = pool6_start
                    dhcp_config["ipv6_pool_end"] = pool6_end
                    dhcp_config["ipv6_prefix"] = prefix6
                    dhcp_config["ipv6_pool_range"] = (
                        f"{pool6_start}-{pool6_end}" if pool6_start and pool6_end else ""
                    )
                    dhcp_config["ipv6_lease_time"] = lease6
                    dhcp_config["ipv6_server_ip"] = server6
                    dhcp_config["ipv6_gateway"] = gateway6
                    if routes6_text:
                        routes_tokens = [
                            token.strip()
                            for token in routes6_text.replace(";", ",").split(",")
                            if token.strip()
                        ]
                        dhcp_config["ipv6_gateway_route"] = routes_tokens
                    else:
                        # explicit clear
                        dhcp_config["ipv6_gateway_route"] = []
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
                # v0.5.217 (audit fix B): do NOT wipe bgp_config just
                # because DHCP-client is on. Pre-fix, `bgp_config = {}`
                # silently dropped whatever BGP config the operator
                # enabled alongside DHCP-client — downstream treats
                # empty-dict as "no BGP" and the protocol never comes
                # up, matching a real complaint on JNPR-MAC-HWXVX1.
                # If both are checked, that's the operator's explicit
                # intent (DHCP for the interface address, static
                # loopbacks + BGP over the leased link). Surface a
                # warning so they see the interaction rather than
                # silently losing config.
                if bgp_config:
                    try:
                        from PyQt5.QtWidgets import QMessageBox
                        QMessageBox.warning(
                            self,
                            "DHCP-client + BGP",
                            "This device has both DHCP-client mode and BGP "
                            "enabled. The interface IPv4/IPv6 addresses will "
                            "be leased from DHCP, but BGP will still be "
                            "configured against those leased addresses. Ensure "
                            "the BGP neighbor and local addresses are "
                            "reachable once the lease arrives.",
                        )
                    except Exception:
                        # Non-GUI test paths — best effort only.
                        pass
                if ospf_config:
                    # Preserve intent to run OSPF even though address will arrive via DHCP
                    ospf_config["ipv4_enabled"] = self.ospf_toggle_ipv4.isChecked() if hasattr(self, "ospf_toggle_ipv4") else True
                    ospf_config["ipv6_enabled"] = self.ospf_toggle_ipv6.isChecked() if hasattr(self, "ospf_toggle_ipv6") else True
                if isis_config:
                    isis_config["ipv4_enabled"] = self.isis_toggle_ipv4.isChecked() if hasattr(self, "isis_toggle_ipv4") else True
                    isis_config["ipv6_enabled"] = self.isis_toggle_ipv6.isChecked() if hasattr(self, "isis_toggle_ipv6") else False

        return (
            self.device_name_input.text().strip(),
            self.iface_input.text().strip(),
            self.mac_input.text().strip(),
            ipv4,
            ipv6,
            ipv4_mask,
            ipv6_mask,
            self.vlan_input.text().strip(),
            self.mtu_input.text().strip() or "1500",  # MTU field, default to 1500
            ipv4_gateway,
            ipv6_gateway,
            self.increment_checkbox_mac.isChecked(),
            self.increment_checkbox_ipv4.isChecked(),
            self.increment_checkbox_ipv6.isChecked(),
            self.increment_checkbox_gateway.isChecked(),
            self.increment_checkbox_vlan.isChecked(),
            self.increment_checkbox_vxlan.isChecked(),
            self.increment_count.value(),
            ospf_config,
            bgp_config,
            dhcp_config,
            self.ipv4_octet_combo.currentIndex(),    # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.ipv6_hextet_combo.currentIndex(),   # 0=8th, 1=7th, ..., 7=1st
            self.mac_byte_combo.currentIndex(),      # 0=6th, 1=5th, ..., 5=1st
            self.gateway_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            False,  # incr_dhcp_pool - no longer supported
            2,  # dhcp_pool_octet_index - default to 2nd octet (not used when incr_dhcp_pool is False)
            self.increment_checkbox_loopback.isChecked(),  # Loopback increment checkbox
            self.loopback_ipv4_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.loopback_ipv6_hextet_combo.currentIndex(),  # 0=8th, 1=7th, ..., 7=1st
            loopback_ipv4,
            loopback_ipv6,
            isis_config,
            self.vxlan_vni_increment_combo.currentIndex(),  # 0=+1, 1=+10, 2=+100, 3=+1000
            self.vxlan_local_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.vxlan_remote_octet_combo.currentIndex(),  # 0=4th, 1=3rd, 2=2nd, 3=1st
            self.vxlan_udp_increment_combo.currentIndex()  # 0=+1, 1=+10, 2=+100
        )

    def get_vxlan_config(self):
        """Return VXLAN configuration if enabled."""
        config = self._collect_vxlan_config()
        return config if config else None

    def set_vxlan_values(self, vxlan_config):
        """Populate VXLAN fields when editing device."""
        if not vxlan_config:
            self.vxlan_enable_checkbox.setChecked(False)
            self.vxlan_vni_input.clear()
            self.vxlan_remote_input.clear()
            self.vxlan_local_ip_input.clear()
            if hasattr(self, "vxlan_underlay_iface_input"):
                self.vxlan_underlay_iface_input.setText(self.iface_input.text())
            self.vxlan_udp_port_input.setText("4789")
            self.vxlan_vlan_id_input.clear()
            if hasattr(self, "vxlan_bridge_svi_ip_input"):
                self.vxlan_bridge_svi_ip_input.clear()
            return

        self.vxlan_enable_checkbox.setChecked(True)
        vni = vxlan_config.get("vni")
        self.vxlan_vni_input.setText(str(vni) if vni is not None else "")

        remote_values = vxlan_config.get("remote_peers") or vxlan_config.get("remote_endpoints") or []
        if isinstance(remote_values, list):
            self.vxlan_remote_input.setText(", ".join(remote_values))
        else:
            self.vxlan_remote_input.setText(str(remote_values))

        self.vxlan_local_ip_input.setText(vxlan_config.get("local_ip", ""))

        udp_port = vxlan_config.get("udp_port")
        self.vxlan_udp_port_input.setText(str(udp_port) if udp_port else "4789")
        
        vlan_id = vxlan_config.get("vlan_id") or vxlan_config.get("vxlan_vlan_id")
        self.vxlan_vlan_id_input.setText(str(vlan_id) if vlan_id else "")
        
        # Bridge SVI configuration
        if hasattr(self, "vxlan_bridge_svi_ip_input"):
            bridge_svi_ip = vxlan_config.get("bridge_svi_ip") or vxlan_config.get("vxlan_bridge_svi_ip") or vxlan_config.get("bridge_ip")
            # If bridge_svi_subnet exists separately, combine with IP
            bridge_svi_subnet = vxlan_config.get("bridge_svi_subnet") or vxlan_config.get("vxlan_bridge_subnet") or vxlan_config.get("bridge_subnet")
            if bridge_svi_ip and bridge_svi_subnet and '/' not in bridge_svi_ip:
                # Combine IP and subnet if not already combined
                if '/' in bridge_svi_subnet:
                    # Extract prefix length from subnet
                    prefix = bridge_svi_subnet.split('/')[-1]
                    bridge_svi_ip = f"{bridge_svi_ip}/{prefix}"
                else:
                    bridge_svi_ip = f"{bridge_svi_ip}/{bridge_svi_subnet}"
            self.vxlan_bridge_svi_ip_input.setText(bridge_svi_ip if bridge_svi_ip else "")

    def _collect_vxlan_config(self):
        if not getattr(self, "vxlan_enable_checkbox", None):
            return {}
        if not self.vxlan_enable_checkbox.isChecked():
            return {}

        vni_text = self.vxlan_vni_input.text().strip()
        remote_text = self.vxlan_remote_input.text().strip()
        local_ip = self.vxlan_local_ip_input.text().strip()
        udp_port_text = self.vxlan_udp_port_input.text().strip()
        vlan_id_text = self.vxlan_vlan_id_input.text().strip()
        bridge_svi_ip_text = self.vxlan_bridge_svi_ip_input.text().strip() if hasattr(self, "vxlan_bridge_svi_ip_input") else ""

        config = {"enabled": True}
        if vni_text:
            config["vni"] = int(vni_text)

        remote_peers = [
            token.strip()
            for token in remote_text.replace(";", ",").split(",")
            if token.strip()
        ]
        if remote_peers:
            config["remote_peers"] = remote_peers

        if local_ip:
            config["local_ip"] = local_ip
        
        if vlan_id_text:
            try:
                config["vlan_id"] = int(vlan_id_text)
            except ValueError:
                pass  # Invalid VLAN ID, skip it
        
        # Bridge SVI configuration - extract IP and subnet from single field
        if bridge_svi_ip_text:
            # If format is "IP/subnet" (e.g., "10.0.0.100/24"), split it
            if '/' in bridge_svi_ip_text:
                parts = bridge_svi_ip_text.split('/', 1)
                config["bridge_svi_ip"] = parts[0]
                config["bridge_svi_subnet"] = parts[1]
            else:
                # Just IP provided, use default /24 subnet
                config["bridge_svi_ip"] = bridge_svi_ip_text
                config["bridge_svi_subnet"] = "24"
        
        base_interface = self.iface_input.text().strip()
        vlan_id = self.vlan_input.text().strip() or "0"
        if base_interface:
            config["underlay_interface"] = base_interface
        overlay_iface = base_interface
        if vlan_id and vlan_id != "0":
            overlay_iface = f"vlan{vlan_id}"
        config["overlay_interface"] = overlay_iface
        if udp_port_text:
            config["udp_port"] = int(udp_port_text)

        return config
    
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
    
    def _open_upstream_hint_dialog(self):
        """Snapshot the dialog's current state into a device_data dict
        and open the Juniper / Cisco IOS / Arista hint modal.

        Reads live — the operator gets whatever they've typed so far,
        even if it hasn't been saved yet. Works pre- and post-Save.
        """
        try:
            device_data = self._snapshot_for_upstream_hint()
        except Exception:
            device_data = {}
        try:
            from widgets.upstream_hint_dialog import UpstreamHintDialog
        except Exception as exc:
            try:
                QMessageBox.warning(
                    self, "Upstream Hint",
                    f"Could not load upstream-hint dialog: {exc}",
                )
            finally:
                return
        UpstreamHintDialog(device_data, self).exec_()

    def _snapshot_for_upstream_hint(self) -> dict:
        """Read every field the hint renderer needs. Silent about
        fields the dialog doesn't have — the renderer's alternate-
        key lookup treats missing keys as empty."""
        def txt(name):
            w = getattr(self, name, None)
            try:
                return w.text().strip() if w is not None else ""
            except Exception:
                return ""

        def checked(name):
            w = getattr(self, name, None)
            try:
                return bool(w.isChecked()) if w is not None else False
            except Exception:
                return False

        ipv4_on = checked("ipv4_checkbox")
        ipv6_on = checked("ipv6_checkbox")

        data = {
            "device_name":     txt("device_name_input"),
            "interface":       txt("iface_input"),
            "vlan":            txt("vlan_input"),
            "mac_address":     txt("mac_input"),
            "ipv4_address":    txt("ipv4_input") if ipv4_on else "",
            "ipv4_mask":       txt("ipv4_mask_input") or "24",
            "ipv4_gateway":    txt("ipv4_gateway_input") if ipv4_on else "",
            "ipv6_address":    txt("ipv6_input") if ipv6_on else "",
            "ipv6_mask":       txt("ipv6_mask_input") or "64",
            "ipv6_gateway":    txt("ipv6_gateway_input") if ipv6_on else "",
            "loopback_ipv4":   txt("loopback_ipv4_input"),
            "loopback_ipv6":   txt("loopback_ipv6_input"),
        }

        if checked("bgp_enable_checkbox"):
            data["bgp_config"] = {
                "bgp_local_as":  txt("bgp_local_as_input"),
                "bgp_remote_asn": txt("bgp_remote_as_input"),
                "bgp_hold_time": txt("bgp_hold_time_input") or "90",
                "bgp_keepalive": txt("bgp_keepalive_input") or "30",
                "ipv4_enabled":  ipv4_on,
                "ipv6_enabled":  ipv6_on,
            }

        if checked("ospf_enable_checkbox"):
            data["ospf_config"] = {
                "area_id":        txt("ospf_area_id_input") or "0.0.0.0",
                "hello_interval": txt("ospf_hello_interval_input") or "10",
                "dead_interval":  txt("ospf_dead_interval_input") or "40",
                "ipv4_enabled":   ipv4_on,
                "ipv6_enabled":   ipv6_on,
            }

        if checked("isis_enable_checkbox"):
            data["isis_config"] = {
                "isis_area":  txt("isis_area_input") or "CORE",
                "isis_net":   txt("isis_net_input"),
                "isis_level": (
                    self.isis_level_combo.currentText()
                    if hasattr(self, "isis_level_combo") else "level-2-only"
                ),
            }

        return data

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

        # Audit LOW #15: device names flow into shell-like contexts on
        # the server side (FRR container names, sysctl, ip/route
        # commands), so accept only an obviously safe subset of
        # characters. Empty name is fine — the model assigns a
        # default ("device1", "device2", …); we only need to police
        # explicit user input.
        if device_name:
            if len(device_name) > 64:
                QMessageBox.warning(
                    self, "Validation Error",
                    "Device name must be 64 characters or fewer.",
                )
                return
            import re as _re
            if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", device_name):
                QMessageBox.warning(
                    self, "Validation Error",
                    "Device name may only contain letters, digits, '.', "
                    "'-', and '_', and must start with a letter or digit. "
                    "Spaces, slashes, and shell metacharacters are not "
                    "allowed (the name is used in shell commands on the "
                    "server side).",
                )
                return

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

        if self.vxlan_enable_checkbox.isChecked():
            vni_text = self.vxlan_vni_input.text().strip()
            remote_text = self.vxlan_remote_input.text().strip()
            local_ip = self.vxlan_local_ip_input.text().strip()
            udp_port_text = self.vxlan_udp_port_input.text().strip() or "4789"

            if not vni_text:
                QMessageBox.warning(self, "Validation Error", "VXLAN requires a VNI.")
                return
            try:
                vni_val = int(vni_text)
                if vni_val < 1 or vni_val > 16777215:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Validation Error", "VNI must be an integer between 1 and 16777215.")
                return

            remote_peers = [
                token.strip()
                for token in remote_text.replace(";", ",").split(",")
                if token.strip()
            ]
            if not remote_peers:
                QMessageBox.warning(self, "Validation Error", "Provide at least one remote VXLAN endpoint.")
                return
            for endpoint in remote_peers:
                try:
                    ipaddress.ip_address(endpoint)
                except ValueError:
                    QMessageBox.warning(self, "Validation Error", f"Invalid VXLAN remote endpoint '{endpoint}'.")
                    return

            if local_ip:
                try:
                    ipaddress.ip_address(local_ip)
                except ValueError:
                    QMessageBox.warning(self, "Validation Error", "VXLAN local endpoint IP is invalid.")
                    return

            try:
                udp_port_val = int(udp_port_text)
                if udp_port_val < 1 or udp_port_val > 65535:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Validation Error", "VXLAN UDP port must be between 1 and 65535.")
                return

        # v0.5.229 (audit B3): IPv4 pool + gateway-route validation
        # (parity with the IPv6 block below — pre-fix IPv6 had all
        # three checks, IPv4 had zero). Rejects pool_start="abc",
        # pool_start > pool_end, and malformed gateway_route CIDR
        # so dnsmasq doesn't crash opaquely later with device stuck
        # in State=Failed.
        if (
            self.dhcp_enable_checkbox.isChecked()
            and self.dhcp_mode_combo.currentText().strip().lower() == "server"
            and self.dhcp_ipv4_enabled_checkbox.isChecked()
        ):
            v4_pool_start = self.dhcp_pool_start_input.text().strip()
            v4_pool_end = self.dhcp_pool_end_input.text().strip()
            v4_gw_route = self.dhcp_gateway_route_input.text().strip()
            v4_lease = self.dhcp_lease_time_input.text().strip()
            if v4_pool_start and v4_pool_end:
                try:
                    _s = ipaddress.IPv4Address(v4_pool_start)
                    _e = ipaddress.IPv4Address(v4_pool_end)
                    if int(_s) > int(_e):
                        QMessageBox.warning(
                            self, "Validation Error",
                            f"DHCP IPv4 Pool Start ({v4_pool_start}) must be ≤ "
                            f"Pool End ({v4_pool_end}). dnsmasq refuses to launch "
                            "with an inverted range.",
                        )
                        return
                except ipaddress.AddressValueError:
                    QMessageBox.warning(
                        self, "Validation Error",
                        f"Invalid DHCP IPv4 pool address "
                        f"(start='{v4_pool_start}', end='{v4_pool_end}').",
                    )
                    return
            if v4_gw_route:
                try:
                    ipaddress.ip_network(v4_gw_route, strict=False)
                except ValueError as exc:
                    QMessageBox.warning(
                        self, "Validation Error",
                        f"DHCP IPv4 Gateway Route '{v4_gw_route}' is not a "
                        f"valid CIDR: {exc}",
                    )
                    return
            if v4_lease:
                # v0.5.229 (audit U client-2): reject non-int / non-
                # positive lease times before dnsmasq gets them.
                try:
                    _lt = int(v4_lease)
                    if _lt < 60 or _lt > 4294967295:
                        raise ValueError("out of range")
                except ValueError:
                    QMessageBox.warning(
                        self, "Validation Error",
                        f"DHCP IPv4 Lease Time '{v4_lease}' must be a "
                        "positive integer between 60 and 4294967295 seconds.",
                    )
                    return

        if (
            self.dhcp_enable_checkbox.isChecked()
            and self.dhcp_mode_combo.currentText().strip().lower() == "server"
            and self.dhcp_ipv6_enabled_checkbox.isChecked()
        ):
            pool6_start = self.dhcp6_pool_start_input.text().strip()
            pool6_end = self.dhcp6_pool_end_input.text().strip()
            prefix6 = self.dhcp6_prefix_input.text().strip()
            server6 = self.dhcp6_server_ip_input.text().strip()
            gateway6 = self.dhcp6_gateway_input.text().strip()

            if not pool6_start or not pool6_end or not prefix6:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "IPv6 DHCP requires pool start, pool end, and prefix length.",
                )
                return
            try:
                start_addr = ipaddress.IPv6Address(pool6_start)
                end_addr = ipaddress.IPv6Address(pool6_end)
                if int(start_addr) > int(end_addr):
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        "IPv6 pool start must be less than or equal to pool end.",
                    )
                    return
            except ipaddress.AddressValueError:
                QMessageBox.warning(self, "Validation Error", "Invalid IPv6 pool start or end address.")
                return
            try:
                prefix_val = int(prefix6)
                if prefix_val < 0 or prefix_val > 128:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Validation Error", "IPv6 prefix must be between 0 and 128.")
                return
            if server6:
                try:
                    ipaddress.IPv6Address(server6)
                except ipaddress.AddressValueError:
                    QMessageBox.warning(self, "Validation Error", "Invalid server IPv6 address.")
                    return
            if gateway6:
                try:
                    ipaddress.IPv6Address(gateway6)
                except ipaddress.AddressValueError:
                    QMessageBox.warning(self, "Validation Error", "Invalid IPv6 gateway address.")
                    return
            routes6_text = self.dhcp6_gateway_route_input.text().strip()
            if routes6_text:
                for token in routes6_text.replace(";", ",").split(","):
                    value = token.strip()
                    if not value:
                        continue
                    try:
                        ipaddress.ip_network(value, strict=False)
                    except ValueError as exc:
                        QMessageBox.warning(
                            self,
                            "Validation Error",
                            f"Invalid IPv6 gateway route '{value}': {exc}",
                        )
                        return
        
        # Duplicate-address gate. Advisory on the client (the server
        # rejects with HTTP 409 as the backstop) so the operator sees
        # the error before the round-trip. Only fires when the caller
        # supplied a peer list — otherwise we can't compare.
        if self._existing_devices:
            try:
                from utils.address_collision import (
                    find_conflict,
                    find_iface_vlan_conflict,
                )
                loopback_ipv4_val = self.loopback_ipv4_input.text().strip()
                loopback_ipv6_val = (
                    self.loopback_ipv6_input.text().strip()
                    if self.loopback_ipv6_input.isEnabled() else ""
                )
                _vlan_val = self.vlan_input.text().strip() if hasattr(self, "vlan_input") else ""
                _iface_val = iface.strip()
                # normalize "TG 0 - Port: ensXfYnpZ" → "ensXfYnpZ" so
                # the L2-scoped check matches how the server stores it.
                if " - " in _iface_val:
                    _iface_val = _iface_val.split(" - ", 1)[-1].strip()
                if ":" in _iface_val:
                    _iface_val = _iface_val.rsplit(":", 1)[-1].strip()
                _iface_val = _iface_val.split()[-1] if _iface_val.split() else _iface_val
                # (interface, vlan) collision check — mirrors the server
                # gate at run_tgen_server.py:4265-4301. Two devices on
                # the same NIC + same VLAN would share the same L2/L3
                # segment and collide on BGP TCP/179, OSPF raw sockets,
                # and ISIS PF_PACKET binds.
                _iface_vlan_hit = find_iface_vlan_conflict(
                    _iface_val, _vlan_val, self._existing_devices,
                    exclude_id=self._exclude_device_id,
                )
                if _iface_vlan_hit:
                    _peer_id, _peer_name = _iface_vlan_hit
                    QMessageBox.warning(
                        self,
                        "Duplicate Interface + VLAN",
                        f"Interface '{_iface_val}' with VLAN "
                        f"'{_vlan_val or '0'}' is already in use by "
                        f"device '{_peer_name or _peer_id}'.\n\n"
                        f"Two devices can share a physical NIC only via "
                        f"different VLAN tags — same (interface, VLAN) "
                        f"would collide on BGP TCP/179, OSPF raw sockets, "
                        f"and ISIS PF_PACKET binds. Change the VLAN tag "
                        f"or pick a different interface.",
                    )
                    return
                _dup_checks = [
                    ("loopback_ipv4", loopback_ipv4_val, None, None),
                    ("loopback_ipv6", loopback_ipv6_val, None, None),
                    ("ipv4_address",  ipv4,              _iface_val, _vlan_val),
                    ("ipv6_address",  ipv6,              _iface_val, _vlan_val),
                ]
                for _field, _val, _if, _vl in _dup_checks:
                    if not _val:
                        continue
                    _hit = find_conflict(
                        _field, _val, self._existing_devices,
                        exclude_id=self._exclude_device_id,
                        interface=_if, vlan_id=_vl,
                    )
                    if _hit:
                        _peer_id, _peer_name = _hit
                        _label = _field.replace("_", " ")
                        QMessageBox.warning(
                            self,
                            "Duplicate Address",
                            f"{_label} '{_val}' is already in use by "
                            f"device '{_peer_name or _peer_id}'.\n\n"
                            f"Duplicate loopback IPs break OSPF adjacency "
                            f"(both routers get the same router-id) and "
                            f"duplicate interface IPs cause ARP collisions. "
                            f"Pick a different {_label}.",
                        )
                        return
            except Exception:
                # Fail-open on validator errors — server 409 remains.
                pass

        # v0.5.227: DHCP-server mode requires a pool range. Pre-fix,
        # Save+Apply of a Server-mode device with the pool fields
        # blank (or with the DHCP IPv4 subcheckbox off but mode still
        # Server) landed a config with no pool_start/pool_end. dnsmasq
        # refused to launch, the "No Pool" state kicked in, and the
        # operator wondered why the server was down (Server Down /
        # No Pool distinction from v0.5.223 didn't help if the client
        # let a fundamentally unbootable config through). Catch it
        # here so the operator sees the actual problem before submit.
        if (hasattr(self, "dhcp_enable_checkbox")
                and self.dhcp_enable_checkbox.isChecked()
                and hasattr(self, "dhcp_mode_combo")
                and self.dhcp_mode_combo.currentText().strip().lower() == "server"):
            v4_on = (
                hasattr(self, "dhcp_ipv4_enabled_checkbox")
                and self.dhcp_ipv4_enabled_checkbox.isChecked()
            )
            v6_on = (
                hasattr(self, "dhcp_ipv6_enabled_checkbox")
                and self.dhcp_ipv6_enabled_checkbox.isChecked()
            )
            if not v4_on and not v6_on:
                QMessageBox.warning(
                    self, "DHCP Server: no address family",
                    "DHCP mode is Server but neither IPv4 nor IPv6 is "
                    "enabled — dnsmasq needs at least one pool to serve.\n\n"
                    "Tick 'IPv4' or 'IPv6' under DHCP and set a pool range.",
                )
                return
            v4_ok = True
            if v4_on:
                pool_start = self.dhcp_pool_start_input.text().strip()
                pool_end = self.dhcp_pool_end_input.text().strip()
                v4_ok = bool(pool_start and pool_end)
                if not v4_ok:
                    QMessageBox.warning(
                        self, "DHCP Server: pool range required",
                        "DHCP IPv4 is enabled on a Server-mode device "
                        "but Pool Start / Pool End is blank — dnsmasq "
                        "will refuse to launch and the device will "
                        "sit in 'No Pool' state.\n\n"
                        "Set both Pool Start and Pool End (e.g. "
                        "172.16.30.10 and 172.16.30.200) before Save.",
                    )
                    return
            if v6_on:
                pool6_start = (
                    self.dhcp6_pool_start_input.text().strip()
                    if hasattr(self, "dhcp6_pool_start_input") else ""
                )
                pool6_end = (
                    self.dhcp6_pool_end_input.text().strip()
                    if hasattr(self, "dhcp6_pool_end_input") else ""
                )
                if not (pool6_start and pool6_end):
                    QMessageBox.warning(
                        self, "DHCP Server: IPv6 pool range required",
                        "DHCP IPv6 is enabled on a Server-mode device "
                        "but IPv6 Pool Start / End is blank — dnsmasq "
                        "will refuse to launch.\n\n"
                        "Set both IPv6 Pool Start and Pool End before "
                        "Save.",
                    )
                    return

        # All validations passed
        self.accept()
