# stream_dialog.py
import logging
import os
import uuid

logger = logging.getLogger(__name__)

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QStackedWidget, QSpinBox,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox, QWidget, QMessageBox,
    QHeaderView, QRadioButton, QGroupBox, QGridLayout, QTabWidget, QScrollArea, QCheckBox, QInputDialog, QSplitter,
    QAction, QMenu, QAbstractItemView, QSizePolicy, QTreeWidget, QTreeWidgetItem, QTextEdit, QSpacerItem, QFileDialog
)
from PyQt5.QtCore import QTimer, Qt, QRegExp, QSize, QItemSelectionModel, QDateTime
from PyQt5.QtGui import QIntValidator, QBrush, QRegExpValidator, QIcon, QValidator, QPixmap, QColor


class Unsigned32BitValidator(QValidator):
    """Custom validator for 32-bit unsigned integers."""
    def validate(self, input, pos):
        if not input:  # Allow empty field for user input
            return QValidator.Intermediate, input, pos
        try:
            value = int(input)
            if 0 <= value <= 4294967295:
                return QValidator.Acceptable, input, pos
            return QValidator.Invalid, input, pos
        except ValueError:
            return QValidator.Invalid, input, pos


class AddStreamDialog(QDialog):
    def __init__(self, parent=None, interface=None, stream_data=None, server_interfaces=None):
        super().__init__(parent)
        self.tx_port = interface or ""
        self.tx_port_name = self.tx_port.split(" - Port:")[-1].strip() if self.tx_port else ""
        self.stream_data = stream_data or {}
        self.server_interfaces = server_interfaces or []

        self.setWindowTitle("Add/Edit Traffic Stream")
        self.setGeometry(200, 200, 1000, 700)
        self.setMinimumSize(900, 500)  # Reduced minimum width
        self.setMaximumSize(1200, 750)  # Reduced maximum width
        # Set smaller base font size
        font = self.font()
        font.setPointSize(10)  # Reduced from default 13
        self.setFont(font)
        
        # Apply professional styling
        self._apply_professional_styling()

        # Tabs
        self.tabs = QTabWidget()
        # Enable scroll buttons for main tabs if they overflow
        self.tabs.setUsesScrollButtons(True)

        # Protocol Selection Tab
        self.protocol_tab = QWidget()
        self.protocol_tab_layout = QVBoxLayout()
        self.protocol_tab.setLayout(self.protocol_tab_layout)
        self.setup_protocol_selection_tab()

        # Protocol Data Tab
        self.protocol_data_tab = QWidget()
        self.protocol_data_layout = QVBoxLayout()
        self.protocol_data_tab.setLayout(self.protocol_data_layout)
        self.setup_protocol_data_tab()

        # Packet View Tab
        self.packet_view_tab = QWidget()
        self.packet_view_layout = QVBoxLayout()
        self.packet_view_tab.setLayout(self.packet_view_layout)
        self.setup_packet_view_tab()

        # Stream Control Tab
        self.stream_control_tab = QWidget()
        self.setup_stream_control_tab()

        # Variable Fields Tab (placeholder)
        self.variable_fields_tab = QWidget()
        self.setup_variable_fields_tab()

        # PCAP Replay Tab
        self.pcap_tab = QWidget()
        self.setup_pcap_tab()

        # Add tabs (order matters for initial wiring)
        self.tabs.addTab(self.protocol_tab, "Protocol Selection")
        self.tabs.addTab(self.protocol_data_tab, "Protocol Data")
        self.tabs.addTab(self.variable_fields_tab, "Variable Fields")
        self.tabs.addTab(self.stream_control_tab, "Stream Control")
        self.tabs.addTab(self.packet_view_tab, "Packet View")
        self.tabs.addTab(self.pcap_tab, "PCAP Replay")

        # Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.tabs)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Main Layout
        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.scroll_area)

        # Buttons with professional styling
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        
        # Style the buttons - more compact
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        if ok_button:
            ok_button.setText("Save")
            ok_button.setMinimumWidth(80)
            ok_button.setMinimumHeight(26)
        if cancel_button:
            cancel_button.setText("Cancel")
            cancel_button.setMinimumWidth(80)
            cancel_button.setMinimumHeight(26)
        
        self.main_layout.addWidget(self.buttons)
        self.setLayout(self.main_layout)

        # Populate RX list after protocol tab exists
        self.populate_rx_ports(self.tx_port_name)

        # Populate existing data (edit case)
        if self.stream_data:
            self.populate_stream_fields(self.stream_data)

        # Dynamic updates for Packet View
        self.connect_protocol_data_to_packet_view()

    def _apply_professional_styling(self):
        """Apply professional styling to the dialog."""
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                font-size: 10px;
            }
            
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                top: -1px;
            }
            
            QTabBar::tab {
                background-color: #f3f4f6;
                color: #4b5563;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                padding: 4px 8px;
                margin-right: 1px;
                font-weight: 500;
                font-size: 9px;
                min-width: 60px;
                max-width: 80px;
            }
            
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #ffffff;
                top: -1px;
            }
            
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1f2937;
                border-bottom: 2px solid #3b82f6;
                font-weight: 600;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #e5e7eb;
                color: #374151;
            }
            
            QGroupBox {
                font-weight: 600;
                font-size: 11px;
                color: #1f2937;
                border: 2px solid #e5e7eb;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                padding-bottom: 8px;
                background-color: #f9fafb;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                background-color: #ffffff;
                color: #1f2937;
            }
            
            QFormLayout {
                spacing: 12px;
            }
            
            QFormLayout QLabel {
                color: #374151;
                font-weight: 500;
                font-size: 10px;
                min-width: 120px;
            }
            
            QLineEdit, QComboBox, QSpinBox, QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 4px 8px;
                background-color: #ffffff;
                color: #1f2937;
                font-size: 10px;
                min-height: 18px;
            }
            
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {
                border: 2px solid #3b82f6;
                background-color: #f0f9ff;
            }
            
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover {
                border: 1px solid #9ca3af;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #6b7280;
                width: 0;
                height: 0;
            }
            
            QComboBox QAbstractItemView {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                selection-background-color: #dbeafe;
                selection-color: #1e40af;
                padding: 4px;
            }
            
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: 600;
                font-size: 10px;
                min-height: 28px;
            }
            
            QPushButton:hover {
                background-color: #2563eb;
            }
            
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            
            QPushButton:disabled {
                background-color: #9ca3af;
                color: #d1d5db;
            }
            
            QDialogButtonBox QPushButton[text="Cancel"] {
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
            }
            
            QDialogButtonBox QPushButton[text="Cancel"]:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            
            QDialogButtonBox QPushButton[text="Save"] {
                background-color: #3b82f6;
                color: #ffffff;
            }
            
            QDialogButtonBox QPushButton[text="Save"]:hover {
                background-color: #2563eb;
            }
            
            QCheckBox, QRadioButton {
                color: #374151;
                font-weight: 500;
                font-size: 10px;
                spacing: 6px;
            }
            
            QCheckBox::indicator, QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid #d1d5db;
                border-radius: 3px;
                background-color: #ffffff;
            }
            
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {
                background-color: #3b82f6;
                border-color: #3b82f6;
            }
            
            QCheckBox::indicator:checked {
                background-color: #3b82f6;
                border-color: #3b82f6;
            }
            
            QCheckBox::indicator:hover, QRadioButton::indicator:hover {
                border-color: #3b82f6;
            }
            
            QScrollArea {
                border: none;
                background-color: #ffffff;
            }
            
            QScrollBar:vertical {
                border: none;
                background-color: #f3f4f6;
                width: 12px;
                border-radius: 6px;
            }
            
            QScrollBar::handle:vertical {
                background-color: #d1d5db;
                border-radius: 6px;
                min-height: 30px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #9ca3af;
            }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                background-color: #ffffff;
                gridline-color: #f3f4f6;
            }
            
            QTableWidget::item {
                padding: 4px;
                border: none;
                font-size: 10px;
            }
            
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
            
            QHeaderView::section {
                background-color: #f9fafb;
                color: #374151;
                padding: 6px;
                border: none;
                border-bottom: 2px solid #e5e7eb;
                font-weight: 600;
                font-size: 10px;
            }
        """)

    # ----------------------------- Tabs & Sections -----------------------------

    '''def setup_variable_fields_tab(self):
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Variable Fields configuration goes here."))
        self.variable_fields_tab.setLayout(layout)'''

    def setup_variable_fields_tab(self):
        """
        Variable Fields: add a simple engine selector toggle that maps to
        stream_data['dpdk_enable'] (bool). Default is Scapy/kernel path.
        """
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        header = QLabel("<h3 style='color: #1f2937; margin-bottom: 8px;'>Runtime Engine</h3>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        # DPDK toggle
        self.dpdk_enable_checkbox = QCheckBox("Use DPDK (tx_worker)")
        self.dpdk_enable_checkbox.setToolTip(
            "Enable the high-performance DPDK-based tx_worker backend.\n"
            "Hints:\n"
            "• mlx5/NVIDIA: runs with the kernel driver (no vfio).\n"
            "• Broadcom NetXtreme-E / Thor2: binds to vfio-pci.\n"
            "• For 100Gbps+: Auto-enables multi-instance mode.\n"
            "• For 400Gbps: Uses 8-16 instances for line rate.\n"
            "See README for prerequisites."
        )
        layout.addWidget(self.dpdk_enable_checkbox)
        
        # Multi-instance DPDK option (for high rates)
        self.dpdk_multi_instance_checkbox = QCheckBox("Force Multi-Instance DPDK (100Gbps+)")
        self.dpdk_multi_instance_checkbox.setToolTip(
            "Force multi-instance mode for high-rate traffic.\n"
            "Automatically enabled for rates > 50M pps or line rate.\n"
            "Launches multiple tx_worker instances to saturate high-speed NICs.\n"
            "Recommended for 100Gbps and 400Gbps ports."
        )
        layout.addWidget(self.dpdk_multi_instance_checkbox)

        # Small helper text
        hint = QLabel(
            "When enabled, this stream will be transmitted by the DPDK worker.\n"
            "Multi-instance mode is auto-enabled for high rates (100Gbps+).\n"
            "Otherwise the Scapy/kernel path is used."
        )
        hint.setStyleSheet("color: #6b7280; font-size: 12px; padding-top: 8px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        
        # Read More button
        read_more_button = QPushButton("Read More: How to Use DPDK for Packet Generation")
        read_more_button.setStyleSheet("text-align: left; padding: 5px; color: #3b82f6;")
        read_more_button.clicked.connect(self._show_dpdk_usage_guide)
        layout.addWidget(read_more_button)

        # Read More button
        read_more_button = QPushButton("Read More: How to Use DPDK for Packet Generation")
        read_more_button.setStyleSheet("text-align: left; padding: 8px; color: #3b82f6; font-weight: 500;")
        read_more_button.clicked.connect(self._show_dpdk_usage_guide)
        layout.addWidget(read_more_button)
        
        layout.addStretch(1)
        self.variable_fields_tab.setLayout(layout)
    
    def _show_dpdk_usage_guide(self):
        """Show comprehensive guide on how to use DPDK for packet generation."""
        dialog = QDialog(self)
        dialog.setWindowTitle("How to Use DPDK for Packet Generation")
        dialog.setGeometry(300, 300, 800, 700)
        
        layout = QVBoxLayout(dialog)
        
        # Scrollable content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(600)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # Title
        title = QLabel("DPDK Packet Generation Guide")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1e40af; margin-bottom: 15px;")
        content_layout.addWidget(title)
        
        # Overview
        overview_label = QLabel("Overview")
        overview_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(overview_label)
        
        overview_text = QLabel(
            "DPDK (Data Plane Development Kit) enables high-performance packet generation "
            "at line rates (100Gbps, 400Gbps). When enabled, packets are generated by the "
            "DPDK tx_worker application instead of Scapy, providing 10-100x better performance."
        )
        overview_text.setWordWrap(True)
        overview_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(overview_text)
        
        # Prerequisites
        prereq_label = QLabel("Prerequisites (Complete Before Enabling DPDK)")
        prereq_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(prereq_label)
        
        prereq_text = QLabel(
            "1. DPDK Installation:\n"
            "   • DPDK libraries installed (verify via DPDK → Verify Installation)\n"
            "   • tx_worker binary built and available\n\n"
            "2. Hugepages Configuration:\n"
            "   • Configure hugepages: DPDK → Configure Hugepages\n"
            "   • Recommended: 4096 pages (8GB) for 100-400Gbps\n"
            "   • Verify: DPDK → Verify Installation should show ✓\n\n"
            "3. VFIO Modules (for Broadcom/Intel/AMD NICs):\n"
            "   • Load VFIO modules: DPDK → Load VFIO Modules\n"
            "   • Verify: DPDK → Status should show modules loaded\n\n"
            "4. IOMMU Enabled (for Broadcom/Intel/AMD NICs):\n"
            "   • Enable IOMMU: DPDK → Configure IOMMU\n"
            "   • Requires server reboot\n"
            "   • Verify: DPDK → Status should show IOMMU enabled\n\n"
            "5. NIC Binding:\n"
            "   • Bind interface to DPDK: DPDK → Bind Interface to DPDK\n"
            "   • Interface will disappear from 'ip link show'\n"
            "   • This is normal - DPDK now controls the interface"
        )
        prereq_text.setWordWrap(True)
        prereq_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(prereq_text)
        
        # Step-by-step guide
        steps_label = QLabel("Step-by-Step: Enabling DPDK for a Stream")
        steps_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(steps_label)
        
        steps_text = QLabel(
            "1. Prepare the System:\n"
            "   • Complete all prerequisites above\n"
            "   • Verify DPDK Status shows all ✓\n\n"
            "2. Bind Interface to DPDK:\n"
            "   • DPDK → Bind Interface to DPDK\n"
            "   • Select the interface you want to use\n"
            "   • Interface will be bound to vfio-pci (or mlx5 for NVIDIA)\n\n"
            "3. Create/Edit Stream:\n"
            "   • Click 'Add Stream' or 'Edit Stream'\n"
            "   • Configure your packet (L2, L3, L4, etc.)\n"
            "   • Set your desired rate (PPS or Line Rate)\n\n"
            "4. Enable DPDK:\n"
            "   • Go to 'Variable Fields' tab\n"
            "   • Check 'Use DPDK (tx_worker)'\n"
            "   • For 100Gbps+: Optionally check 'Force Multi-Instance DPDK'\n\n"
            "5. Save and Start:\n"
            "   • Click 'Save' to save the stream\n"
            "   • Click 'Start Stream' to begin packet generation\n"
            "   • DPDK will handle packet transmission at high rates"
        )
        steps_text.setWordWrap(True)
        steps_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(steps_text)
        
        # When to use DPDK
        when_label = QLabel("When to Use DPDK vs Scapy")
        when_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(when_label)
        
        when_text = QLabel(
            "Use DPDK When:\n"
            "  ✓ Need high packet rates (> 1M pps)\n"
            "  ✓ Targeting 100Gbps or 400Gbps line rates\n"
            "  ✓ Low latency is critical\n"
            "  ✓ Testing high-speed network equipment\n\n"
            "Use Scapy When:\n"
            "  ✓ Low to moderate rates (< 1M pps)\n"
            "  ✓ Need complex packet manipulation\n"
            "  ✓ Testing with standard kernel networking\n"
            "  ✓ DPDK setup is not available"
        )
        when_text.setWordWrap(True)
        when_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(when_text)
        
        # Performance expectations
        perf_label = QLabel("Performance Expectations")
        perf_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(perf_label)
        
        perf_text = QLabel(
            "Scapy (Kernel Path):\n"
            "  • Typical: 50K - 500K pps\n"
            "  • Maximum: ~1M pps\n"
            "  • Limited by kernel network stack\n\n"
            "DPDK Single-Instance:\n"
            "  • Typical: 1M - 10M pps\n"
            "  • Maximum: ~50M pps\n"
            "  • Good for 10-100Gbps rates\n\n"
            "DPDK Multi-Instance (Auto-enabled for 100Gbps+):\n"
            "  • Typical: 10M - 100M+ pps\n"
            "  • Can achieve line rate (100Gbps, 400Gbps)\n"
            "  • Uses multiple CPU cores and instances"
        )
        perf_text.setWordWrap(True)
        perf_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(perf_text)
        
        # NIC-specific notes
        nic_label = QLabel("NIC-Specific Notes")
        nic_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(nic_label)
        
        nic_text = QLabel(
            "NVIDIA/Mellanox (mlx5):\n"
            "  • Works with kernel driver (mlx5_core)\n"
            "  • No VFIO binding required\n"
            "  • DPDK mlx5 PMD uses kernel driver\n\n"
            "Broadcom NetXtreme-E:\n"
            "  • Requires VFIO binding (vfio-pci)\n"
            "  • Requires IOMMU enabled\n"
            "  • Interface disappears from 'ip link show'\n\n"
            "Intel/AMD NICs:\n"
            "  • Requires VFIO binding (vfio-pci)\n"
            "  • Requires IOMMU enabled\n"
            "  • Interface disappears from 'ip link show'"
        )
        nic_text.setWordWrap(True)
        nic_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(nic_text)
        
        # Troubleshooting
        trouble_label = QLabel("Troubleshooting")
        trouble_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(trouble_label)
        
        trouble_text = QLabel(
            "If DPDK stream fails to start:\n"
            "  • Check DPDK Status: DPDK → Status\n"
            "  • Verify hugepages: Should show configured\n"
            "  • Verify VFIO modules: Should show loaded\n"
            "  • Verify IOMMU: Should show enabled (for Broadcom/Intel/AMD)\n"
            "  • Verify interface binding: Should be bound to vfio-pci\n"
            "  • Check server logs: journalctl -u ostg-server -n 50\n\n"
            "If performance is low:\n"
            "  • Enable 'Force Multi-Instance DPDK' for high rates\n"
            "  • Ensure sufficient CPU cores available\n"
            "  • Check CPU affinity and NUMA nodes\n"
            "  • Verify hugepages are sufficient (4096+ pages)"
        )
        trouble_text.setWordWrap(True)
        trouble_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(trouble_text)
        
        content_layout.addStretch()
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        
        # Close button
        button_layout = QHBoxLayout()
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()

    def _apply_rate_type_ui_state(self):
        """Enable only the input relevant to current Rate Type."""
        if not hasattr(self, "rate_type_dropdown"):
            return
        rt = self.rate_type_dropdown.currentText()

        # default: everything off
        self.stream_pps_rate.setEnabled(False)
        self.stream_bit_rate.setEnabled(False)
        self.stream_load_percentage.setEnabled(False)

        if rt == "Packets Per Second (PPS)":
            self.stream_pps_rate.setEnabled(True)
        elif rt == "Bit Rate":
            self.stream_bit_rate.setEnabled(True)
        elif rt == "Load (%)":
            self.stream_load_percentage.setEnabled(True)
        elif rt == "Line Rate":
            # keep all disabled
            pass

    def build_rate_control(self) -> dict:
        """Return a normalized rate control dict for the server/client logic."""
        kind = self.rate_type_dropdown.currentText()
        mode_map = {
            "Packets Per Second (PPS)": "pps",
            "Bit Rate": "bitrate",
            "Load (%)": "load",
            "Line Rate": "line",
        }
        mode = mode_map.get(kind, "pps")

        # Safe parsing
        def as_int(widget, default=0):
            txt = (widget.text() or "").strip()
            try:
                return int(txt)
            except Exception:
                return default

        rc = {"mode": mode}
        if mode == "pps":
            rc["pps"] = as_int(self.stream_pps_rate, 0)
        elif mode == "bitrate":
            rc["mbps"] = as_int(self.stream_bit_rate, 0)  # keep as Mbps in UI
        elif mode == "load":
            rc["percent"] = as_int(self.stream_load_percentage, 0)
        else:  # line
            rc["line_rate"] = True

        # Duration
        dur_mode = self.duration_mode_dropdown.currentText()
        if dur_mode == "Seconds":
            rc["duration"] = {"mode": "seconds", "seconds": as_int(self.stream_duration_field, 10)}
        else:
            rc["duration"] = {"mode": "continuous"}

        return rc



    def setup_stream_control_tab(self):
        """Sets up the Stream Control Tab with rate control and duration settings."""
        control_layout = QVBoxLayout()
        control_layout.setSpacing(20)
        control_layout.setContentsMargins(20, 20, 20, 20)

        # --- Rate Control ---
        rate_group = QGroupBox("Rate Control")
        rate_layout = QFormLayout()
        rate_layout.setSpacing(12)
        rate_layout.setContentsMargins(16, 20, 16, 16)

        self.rate_type_dropdown = QComboBox()
        self.rate_type_dropdown.addItems([
            "Packets Per Second (PPS)",
            "Bit Rate",
            "Load (%)",
            "Line Rate"
        ])
        rate_layout.addRow("Rate Type:", self.rate_type_dropdown)

        self.stream_pps_rate = QLineEdit("1000")
        self.stream_pps_rate.setValidator(QIntValidator(1, 1_000_000_000))
        rate_layout.addRow("Packets Per Second (PPS):", self.stream_pps_rate)

        self.stream_bit_rate = QLineEdit("100")  # Mbps
        self.stream_bit_rate.setValidator(QIntValidator(1, 1_000_000))
        rate_layout.addRow("Bit Rate (Mbps):", self.stream_bit_rate)

        self.stream_load_percentage = QLineEdit("50")
        self.stream_load_percentage.setValidator(QIntValidator(1, 100))
        rate_layout.addRow("Load (%):", self.stream_load_percentage)

        def _apply_rate_type_ui_state():
            """Enable only the input relevant to current Rate Type."""
            rt = self.rate_type_dropdown.currentText()

            # Turn everything off first
            self.stream_pps_rate.setEnabled(False)
            self.stream_bit_rate.setEnabled(False)
            self.stream_load_percentage.setEnabled(False)

            if rt == "Packets Per Second (PPS)":
                self.stream_pps_rate.setEnabled(True)
            elif rt == "Bit Rate":
                self.stream_bit_rate.setEnabled(True)
            elif rt == "Load (%)":
                self.stream_load_percentage.setEnabled(True)
            elif rt == "Line Rate":
                # all remain disabled
                pass

        self.rate_type_dropdown.currentTextChanged.connect(lambda _: _apply_rate_type_ui_state())

        rate_group.setLayout(rate_layout)
        control_layout.addWidget(rate_group)

        # --- Duration Control ---
        duration_group = QGroupBox("Duration Control")
        duration_layout = QFormLayout()
        duration_layout.setSpacing(12)
        duration_layout.setContentsMargins(16, 20, 16, 16)

        self.duration_mode_dropdown = QComboBox()
        self.duration_mode_dropdown.addItems(["Continuous", "Seconds"])
        duration_layout.addRow("Duration Mode:", self.duration_mode_dropdown)

        self.stream_duration_field = QLineEdit("10")
        self.stream_duration_field.setValidator(QIntValidator(1, 86_400))
        duration_layout.addRow("Duration (Seconds):", self.stream_duration_field)

        def _apply_duration_ui_state():
            """Enable seconds field only when Duration Mode == Seconds."""
            self.stream_duration_field.setEnabled(self.duration_mode_dropdown.currentText() == "Seconds")

        self.duration_mode_dropdown.currentTextChanged.connect(lambda _: _apply_duration_ui_state())

        duration_group.setLayout(duration_layout)
        control_layout.addWidget(duration_group)

        control_layout.addStretch(1)
        self.stream_control_tab.setLayout(control_layout)

        # Initialize UI states after widgets exist
        QTimer.singleShot(0, _apply_rate_type_ui_state)
        QTimer.singleShot(0, _apply_duration_ui_state)

    def setup_protocol_selection_tab(self):
        self.protocol_tab_layout.setContentsMargins(6, 10, 10, 10)  # Reduced left margin to move content left
        self.protocol_tab_layout.setSpacing(6)  # Reduced spacing between sections

        # Basics - Improved layout with better organization
        basics_group = QGroupBox("Basics")
        basics_layout = QGridLayout()
        basics_layout.setContentsMargins(10, 8, 10, 8)  # Reduced margins
        basics_layout.setHorizontalSpacing(8)  # Reduced horizontal spacing
        basics_layout.setVerticalSpacing(6)  # Reduced vertical spacing
        basics_layout.setColumnStretch(1, 1)
        basics_layout.setColumnStretch(3, 1)
        basics_layout.setColumnStretch(5, 1)

        self.stream_name = QLineEdit()
        self.stream_name.setMinimumWidth(200)
        self.enabled_checkbox = QCheckBox("Enabled")
        self.details_field = QLineEdit()
        self.details_field.setMinimumWidth(200)

        self.rx_port_dropdown = QComboBox()
        self.rx_port_dropdown.setMinimumWidth(200)
        self.rx_port_dropdown.addItem("Same as TX Port")

        self.flow_tracking_checkbox = QCheckBox("Enable Flow Tracking")
        self.flow_tracking_checkbox.setChecked(False)

        # Note: DPDK checkbox state will be set in populate_stream_fields()
        # when stream_data is available

        # Row 0: Name and Enabled
        basics_layout.addWidget(QLabel("Name:"), 0, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.stream_name, 0, 1)
        basics_layout.addWidget(QLabel("Enabled:"), 0, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.enabled_checkbox, 0, 3)
        
        # Row 1: Details and RX Port
        basics_layout.addWidget(QLabel("Details:"), 1, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.details_field, 1, 1)
        basics_layout.addWidget(QLabel("RX Port:"), 1, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.rx_port_dropdown, 1, 3)
        
        # Row 2: Flow Tracking
        basics_layout.addWidget(QLabel("Flow Tracking:"), 2, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        basics_layout.addWidget(self.flow_tracking_checkbox, 2, 1)

        basics_group.setLayout(basics_layout)
        self.protocol_tab_layout.addWidget(basics_group)

        # Frame Length - Improved layout with reduced spacing
        frame_length_group = QGroupBox("Frame Length (including FCS)")
        frame_length_layout = QGridLayout()
        frame_length_layout.setContentsMargins(0, 8, 10, 8)  # No left margin to indent content fully to the left
        frame_length_layout.setSpacing(6)  # Reduced spacing
        frame_length_layout.setHorizontalSpacing(0)  # No horizontal spacing between columns
        # Don't stretch label columns (0, 2) - keep them compact
        frame_length_layout.setColumnStretch(0, 0)  # Label column - no stretch
        frame_length_layout.setColumnStretch(1, 1)  # Input column - stretch
        frame_length_layout.setColumnStretch(2, 0)  # Label column - no stretch
        frame_length_layout.setColumnStretch(3, 1)  # Input column - stretch
        # Remove minimum width - let labels size naturally to their content

        self.frame_type = QComboBox()
        self.frame_type.addItems(["Fixed", "Random", "IMIX"])
        self.frame_type.setMinimumWidth(120)
        self.frame_type.setMaximumWidth(200)
        self.frame_min = QLineEdit("64")
        self.frame_min.setMinimumWidth(80)
        self.frame_min.setMaximumWidth(120)
        self.frame_max = QLineEdit("1518")
        self.frame_max.setMinimumWidth(80)
        self.frame_max.setMaximumWidth(120)
        self.frame_size = QLineEdit("64")
        self.frame_size.setMinimumWidth(80)
        self.frame_size.setMaximumWidth(120)
        self.frame_min.setValidator(QIntValidator(64, 1518))
        self.frame_max.setValidator(QIntValidator(64, 1518))
        self.frame_size.setValidator(QIntValidator(64, 1518))

        # Create labels with minimal spacing
        frame_type_label = QLabel("Frame Type:")
        frame_type_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(frame_type_label, 0, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_type, 0, 1)
        
        fixed_size_label = QLabel("Fixed Size:")
        fixed_size_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(fixed_size_label, 0, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_size, 0, 3)
        
        min_label = QLabel("Min:")
        min_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(min_label, 1, 0, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_min, 1, 1)
        
        max_label = QLabel("Max:")
        max_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        frame_length_layout.addWidget(max_label, 1, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        frame_length_layout.addWidget(self.frame_max, 1, 3)
        
        frame_length_group.setLayout(frame_length_layout)
        # Remove any extra margins from the group box
        frame_length_group.setContentsMargins(0, 0, 0, 0)
        self.protocol_tab_layout.addWidget(frame_length_group)
        
        # Connect frame_type change to enable/disable relevant fields
        def _apply_frame_type_ui_state():
            """Enable/disable frame size fields based on selected frame type."""
            frame_type = self.frame_type.currentText()
            if frame_type == "Fixed":
                # Fixed: Enable Fixed Size, disable Min/Max
                self.frame_size.setEnabled(True)
                self.frame_min.setEnabled(False)
                self.frame_max.setEnabled(False)
            elif frame_type == "Random":
                # Random: Enable Min/Max, disable Fixed Size
                self.frame_size.setEnabled(False)
                self.frame_min.setEnabled(True)
                self.frame_max.setEnabled(True)
            elif frame_type == "IMIX":
                # IMIX: Disable all (uses fixed distribution)
                self.frame_size.setEnabled(False)
                self.frame_min.setEnabled(False)
                self.frame_max.setEnabled(False)
        
        self.frame_type.currentTextChanged.connect(lambda _: _apply_frame_type_ui_state())
        # Initialize UI state
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, _apply_frame_type_ui_state)

        # Protocol Stack Sections - Improved layout with better spacing and alignment
        protocol_stack_group = QGroupBox("Protocol Stack")
        protocol_stack_layout = QGridLayout()
        protocol_stack_layout.setContentsMargins(12, 10, 12, 10)  # Reduced vertical margins
        protocol_stack_layout.setSpacing(6)  # Reduced spacing
        protocol_stack_layout.setHorizontalSpacing(8)  # Reduced horizontal spacing
        protocol_stack_layout.setVerticalSpacing(6)  # Reduced vertical spacing
        # Make columns equal width
        for col in range(5):
            protocol_stack_layout.setColumnStretch(col, 1)

        # Helper function to create consistent group boxes
        def create_protocol_group(title, options, checked_index=0):
            group = QGroupBox(title)
            layout = QVBoxLayout()
            layout.setContentsMargins(8, 8, 8, 8)  # Reduced margins
            layout.setSpacing(3)  # Reduced spacing
            radio_buttons = []
            for i, option in enumerate(options):
                rb = QRadioButton(option)
                rb.setStyleSheet("font-size: 10px; padding: 1px;")  # Reduced padding
                if i == checked_index:
                    rb.setChecked(True)
                layout.addWidget(rb)
                radio_buttons.append(rb)
            group.setLayout(layout)
            group.setMinimumWidth(120)
            group.setMaximumWidth(180)
            return group, radio_buttons

        # L1
        l1_group, l1_buttons = create_protocol_group("L1", ["None", "MAC", "RAW"])
        self.l1_none, self.l1_mac, self.l1_raw = l1_buttons
        protocol_stack_layout.addWidget(l1_group, 0, 0)

        # VLAN
        vlan_group, vlan_buttons = create_protocol_group("VLAN", ["Untagged", "Tagged", "Stacked"])
        self.vlan_untagged, self.vlan_tagged, self.vlan_stacked = vlan_buttons
        protocol_stack_layout.addWidget(vlan_group, 0, 1)

        # L2
        l2_group, l2_buttons = create_protocol_group("L2", ["None", "Ethernet II", "MPLS"])
        self.l2_none, self.l2_ethernet, self.l2_mpls = l2_buttons
        protocol_stack_layout.addWidget(l2_group, 0, 2)

        # L4 (first instance - top row)
        l4_group_1, l4_buttons_1 = create_protocol_group("L4", ["None", "ICMP", "IGMP", "TCP", "UDP"])
        self.l4_none_1, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp = l4_buttons_1
        protocol_stack_layout.addWidget(l4_group_1, 0, 3)

        # Payload
        payload_group, payload_buttons = create_protocol_group("Payload", ["None", "Pattern", "Random", "From File"])
        self.payload_none, self.payload_pattern, self.payload_random, self.payload_from_file = payload_buttons
        self.payload_hex = self.payload_from_file  # Map From File to hex for compatibility
        protocol_stack_layout.addWidget(payload_group, 0, 4)

        # L3 (second row)
        l3_group, l3_buttons = create_protocol_group("L3", ["None", "ARP", "IPv4", "IPv6"])
        self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6 = l3_buttons
        protocol_stack_layout.addWidget(l3_group, 1, 0)

        # L4 (second instance - second row)
        l4_group_2, l4_buttons_2 = create_protocol_group("L4", ["None", "RoCEv2", "UEC"])
        self.l4_none_2, self.l4_rocev2, self.l4_uec = l4_buttons_2
        protocol_stack_layout.addWidget(l4_group_2, 1, 1)

        protocol_stack_group.setLayout(protocol_stack_layout)
        self.protocol_tab_layout.addWidget(protocol_stack_group)
        # Don't add stretch to prevent content from being cut off

        # VLAN Toggle section
        for rb in [self.vlan_untagged, self.vlan_tagged, self.vlan_stacked]:
            rb.toggled.connect(self.refresh_vlan_section)

        QTimer.singleShot(0, self.refresh_vlan_section)  # initial sync

        # L3 Toggle section
        for rb in [self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6]:
            rb.toggled.connect(self.refresh_l3_sections)
        QTimer.singleShot(0, self.refresh_l3_sections)

        # L4 toggle section - connect both L4 groups
        # Create a unified l4_none reference for backward compatibility
        self.l4_none = self.l4_none_1  # Use first L4 None as primary
        for rb in (self.l4_none_1, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp):
            rb.toggled.connect(self.refresh_l4_sections)
        for rb in (self.l4_none_2, self.l4_rocev2, self.l4_uec):
            rb.toggled.connect(self.refresh_l4_sections)
        QTimer.singleShot(0, self.refresh_l4_sections)

    def _create_scrollable_tab(self, section_group, tab_name):
        """Helper method to create a scrollable tab with a section group."""
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create content widget - remove width constraints to fill available space
        content_widget = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(8)
        
        # Set size policy for the group box to expand and fill available width
        section_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        
        # Add the section group
        content_layout.addWidget(section_group)
        content_layout.addStretch()
        
        content_widget.setLayout(content_layout)
        scroll_area.setWidget(content_widget)
        
        return scroll_area
    
    def setup_protocol_data_tab(self):
        self.protocol_data_layout.setSpacing(8)
        self.protocol_data_layout.setContentsMargins(10, 10, 10, 10)
        
        # Create a tab widget for sub-tabs under Protocol Data
        self.protocol_data_tabs = QTabWidget()
        self.protocol_data_tabs.setTabPosition(QTabWidget.North)
        # Enable scrolling for tabs when they overflow
        self.protocol_data_tabs.setUsesScrollButtons(True)
        # Set tab bar to wrap or use elide mode
        tab_bar = self.protocol_data_tabs.tabBar()
        tab_bar.setElideMode(Qt.ElideRight)  # Elide text if too long
        tab_bar.setExpanding(False)  # Don't expand tabs to fill space
        
        # Create individual scrollable tabs for each section
        # MAC Tab
        self.add_mac_section()
        mac_scroll = self._create_scrollable_tab(self.mac_group, "MAC")
        self.protocol_data_tabs.addTab(mac_scroll, "MAC")
        
        # ARP Tab
        self.add_arp_section()
        arp_scroll = self._create_scrollable_tab(self.arp_group, "ARP")
        self.protocol_data_tabs.addTab(arp_scroll, "ARP")
        
        # VLAN Tab
        self.add_vlan_section()
        vlan_scroll = self._create_scrollable_tab(self.vlan_group, "VLAN")
        self.protocol_data_tabs.addTab(vlan_scroll, "VLAN")
        
        # IPv4 Tab
        self.add_ipv4_section()
        ipv4_scroll = self._create_scrollable_tab(self.ipv4_group, "IPv4")
        self.protocol_data_tabs.addTab(ipv4_scroll, "IPv4")
        
        # IPv6 Tab
        self.add_ipv6_section()
        ipv6_scroll = self._create_scrollable_tab(self.ipv6_group, "IPv6")
        self.protocol_data_tabs.addTab(ipv6_scroll, "IPv6")
        
        # TCP Tab
        self.add_tcp_section()
        tcp_scroll = self._create_scrollable_tab(self.tcp_group, "TCP")
        self.protocol_data_tabs.addTab(tcp_scroll, "TCP")
        
        # UDP Tab
        self.add_udp_section()
        udp_scroll = self._create_scrollable_tab(self.udp_group, "UDP")
        self.protocol_data_tabs.addTab(udp_scroll, "UDP")
        
        # MPLS Tab
        self.add_mpls_section()
        mpls_scroll = self._create_scrollable_tab(self.mpls_group, "MPLS")
        self.protocol_data_tabs.addTab(mpls_scroll, "MPLS")
        
        # Payload Tab
        self.add_payload_data_section()
        payload_scroll = self._create_scrollable_tab(self.payload_group, "Payload")
        self.protocol_data_tabs.addTab(payload_scroll, "Payload")
        
        # RoCEv2 Tab
        self.add_rocev2_section()
        rocev2_scroll = self._create_scrollable_tab(self.rocev2_group, "RoCEv2")
        self.protocol_data_tabs.addTab(rocev2_scroll, "RoCEv2")
        
        # UEC Tab
        self.add_uec_section()
        uec_scroll = self._create_scrollable_tab(self.uec_group, "UEC")
        self.protocol_data_tabs.addTab(uec_scroll, "UEC")
        
        # Apply compact styling to protocol data tabs to prevent overflow
        self.protocol_data_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background-color: #f3f4f6;
                color: #4b5563;
                border: 1px solid #d1d5db;
                border-bottom: none;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                padding: 3px 6px;
                margin-right: 1px;
                font-weight: 500;
                font-size: 9px;
                min-width: 45px;
                max-width: 65px;
            }
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #1f2937;
                border-bottom: 2px solid #3b82f6;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background-color: #e5e7eb;
                color: #374151;
            }
            QTabBar::scroller {
                width: 20px;
            }
        """)
        
        # Add the tab widget to the main protocol data layout
        self.protocol_data_layout.addWidget(self.protocol_data_tabs)

    # ----------------------------- RX ports -----------------------------

    def populate_rx_ports(self, tx_port_name):
        try:
            if not hasattr(self, 'rx_port_dropdown'):
                return

            self.rx_port_dropdown.clear()
            self.rx_port_dropdown.addItem("Same as TX Port")

            tx_clean = tx_port_name.split(" - Port:")[-1].strip()

            full_rx_ports = []
            for server in self.server_interfaces:
                tg_id = server.get("tg_id", "0")
                ports = server.get("ports", [])
                for port in ports:
                    port_clean = port.split(" - Port:")[-1].strip()
                    if port_clean != tx_clean:
                        full_rx_ports.append(f"TG {tg_id} - Port: {port_clean}")

            for label in sorted(full_rx_ports):
                self.rx_port_dropdown.addItem(label)

            # Preselect existing
            if self.stream_data:
                rx_port = self.stream_data.get("rx_port")
                if rx_port:
                    idx = self.rx_port_dropdown.findText(rx_port)
                    if idx != -1:
                        self.rx_port_dropdown.setCurrentIndex(idx)
        except Exception as e:
            logger.error("populate_rx_ports: %s", e)

    # ----------------------------- MAC / VLAN / IPv4 / IPv6 / TCP / UDP / MPLS / Payload -----------------------------

    def toggle_mac_fields(self, mode, count_field, step_field):
        if mode == "Fixed":
            count_field.setText("1")
            step_field.setText("1")
            count_field.setDisabled(True)
            step_field.setDisabled(True)
        else:
            count_field.setEnabled(True)
            step_field.setEnabled(True)

    def add_mac_section(self):
        mac_group = QGroupBox("MAC (Media Access Protocol)")
        mac_layout = QGridLayout()
        mac_layout.setSpacing(8)
        # Make columns expand to fill available space
        mac_layout.setColumnStretch(0, 0)  # Label column - fixed
        mac_layout.setColumnStretch(1, 0)  # Mode dropdown - fixed
        mac_layout.setColumnStretch(2, 2)  # Address field - expand more
        mac_layout.setColumnStretch(3, 0)  # Count label - fixed
        mac_layout.setColumnStretch(4, 0)  # Count field - fixed
        mac_layout.setColumnStretch(5, 0)  # Step label - fixed
        mac_layout.setColumnStretch(6, 0)  # Step field - fixed
        mac_layout.setColumnMinimumWidth(2, 140)  # Minimum width for address field

        # Destination - reorganize to fit better
        mac_layout.addWidget(QLabel("Destination:"), 0, 0)
        self.mac_destination_mode = QComboBox()
        self.mac_destination_mode.addItems(["Fixed", "Increment", "Decrement"])
        self.mac_destination_mode.setMinimumWidth(90)
        self.mac_destination_mode.setMaximumWidth(120)
        self.mac_destination_address = QLineEdit("00:00:00:00:00:00")
        self.mac_destination_address.setMinimumWidth(140)
        self.mac_destination_count = QLineEdit("16")
        self.mac_destination_count.setValidator(QIntValidator(1, 1_000_000))  # Allow up to 1 million MAC addresses
        self.mac_destination_count.setMinimumWidth(50)
        self.mac_destination_count.setMaximumWidth(80)
        self.mac_destination_step = QLineEdit("1")
        self.mac_destination_step.setMinimumWidth(50)
        self.mac_destination_step.setMaximumWidth(80)
        mac_layout.addWidget(self.mac_destination_mode, 0, 1)
        mac_layout.addWidget(self.mac_destination_address, 0, 2)
        mac_layout.addWidget(QLabel("Count:"), 0, 3)
        mac_layout.addWidget(self.mac_destination_count, 0, 4)
        mac_layout.addWidget(QLabel("Step:"), 0, 5)
        mac_layout.addWidget(self.mac_destination_step, 0, 6)
        self.mac_destination_mode.currentTextChanged.connect(
            lambda mode: self.toggle_mac_fields(mode, self.mac_destination_count, self.mac_destination_step)
        )

        # Source - reorganize to fit better
        mac_layout.addWidget(QLabel("Source:"), 1, 0)
        self.mac_source_mode = QComboBox()
        self.mac_source_mode.addItems(["Fixed", "Increment", "Decrement", "Resolve"])
        self.mac_source_mode.setMinimumWidth(90)
        self.mac_source_mode.setMaximumWidth(120)
        self.mac_source_address = QLineEdit("00:00:00:00:00:00")
        self.mac_source_address.setMinimumWidth(140)
        self.mac_source_count = QLineEdit("16")
        self.mac_source_count.setValidator(QIntValidator(1, 1_000_000))  # Allow up to 1 million MAC addresses
        self.mac_source_count.setMinimumWidth(50)
        self.mac_source_count.setMaximumWidth(80)
        self.mac_source_step = QLineEdit("1")
        self.mac_source_step.setMinimumWidth(50)
        self.mac_source_step.setMaximumWidth(80)
        mac_layout.addWidget(self.mac_source_mode, 1, 1)
        mac_layout.addWidget(self.mac_source_address, 1, 2)
        mac_layout.addWidget(QLabel("Count:"), 1, 3)
        mac_layout.addWidget(self.mac_source_count, 1, 4)
        mac_layout.addWidget(QLabel("Step:"), 1, 5)
        mac_layout.addWidget(self.mac_source_step, 1, 6)
        self.mac_source_mode.currentTextChanged.connect(
            lambda mode: self.toggle_mac_fields(mode, self.mac_source_count, self.mac_source_step)
        )

        # Info
        mac_info_label = QLabel(
            "To use MAC resolution, configure a corresponding device on the port with matching VLAN and IP."
        )
        mac_info_label.setWordWrap(True)
        mac_layout.addWidget(mac_info_label, 2, 0, 1, 7)

        mac_group.setLayout(mac_layout)
        # Store reference for grid layout organization
        self.mac_group = mac_group

    def add_arp_section(self):
        """Add ARP (L2.5) configuration group."""
        self.arp_group = QGroupBox("ARP")
        arp_layout = QGridLayout()
        arp_layout.setSpacing(8)
        # Make columns expand to fill available space
        arp_layout.setColumnStretch(0, 0)  # Label column - fixed
        arp_layout.setColumnStretch(1, 2)  # MAC/IP fields - expand more
        arp_layout.setColumnStretch(2, 0)  # Label column - fixed
        arp_layout.setColumnStretch(3, 2)  # IP fields - expand more
        arp_layout.setColumnMinimumWidth(1, 140)
        arp_layout.setColumnMinimumWidth(3, 110)

        # Operation: Request/Reply
        arp_layout.addWidget(QLabel("Operation:"), 0, 0)
        self.arp_operation = QComboBox()
        self.arp_operation.addItems(["Request", "Reply"])
        self.arp_operation.setMinimumWidth(100)
        self.arp_operation.setMaximumWidth(120)
        arp_layout.addWidget(self.arp_operation, 0, 1)

        # Sender MAC / IP
        arp_layout.addWidget(QLabel("Sender MAC:"), 1, 0)
        self.arp_sender_mac = QLineEdit("00:11:22:33:44:55")
        self.arp_sender_mac.setMinimumWidth(140)
        arp_layout.addWidget(self.arp_sender_mac, 1, 1)

        arp_layout.addWidget(QLabel("Sender IP (IPv4):"), 1, 2)
        self.arp_sender_ip = QLineEdit("0.0.0.0")
        self.arp_sender_ip.setMinimumWidth(110)
        self.arp_sender_ip.setMaximumWidth(180)
        arp_layout.addWidget(self.arp_sender_ip, 1, 3)

        # Target MAC / IP
        arp_layout.addWidget(QLabel("Target MAC:"), 2, 0)
        self.arp_target_mac = QLineEdit("ff:ff:ff:ff:ff:ff")
        self.arp_target_mac.setMinimumWidth(140)
        arp_layout.addWidget(self.arp_target_mac, 2, 1)

        arp_layout.addWidget(QLabel("Target IP (IPv4):"), 2, 2)
        self.arp_target_ip = QLineEdit("0.0.0.0")
        self.arp_target_ip.setMinimumWidth(110)
        self.arp_target_ip.setMaximumWidth(180)
        arp_layout.addWidget(self.arp_target_ip, 2, 3)

        # (Optional) Add validators for MAC/IPv4 here

        self.arp_group.setLayout(arp_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state
        try:
            self.arp_group.setEnabled(self.l3_arp.isChecked())
        except Exception:
            pass
    def add_mpls_section(self):
        mpls_group = QGroupBox("MPLS")
        mpls_layout = QGridLayout()
        mpls_layout.setContentsMargins(12, 12, 12, 12)
        mpls_layout.setSpacing(8)
        # Make columns expand to fill available space
        for col in range(6):
            if col % 2 == 0:  # Label columns
                mpls_layout.setColumnStretch(col, 0)
            else:  # Input columns
                mpls_layout.setColumnStretch(col, 1)

        self.mpls_label_field = QLineEdit("16")
        self.mpls_label_field.setValidator(QIntValidator(0, 1_048_575))
        self.mpls_ttl_field = QLineEdit("64")
        self.mpls_ttl_field.setValidator(QIntValidator(0, 255))
        self.mpls_experimental_field = QLineEdit("0")
        self.mpls_experimental_field.setValidator(QIntValidator(0, 7))

        mpls_layout.addWidget(QLabel("Label:"), 0, 0)
        mpls_layout.addWidget(self.mpls_label_field, 0, 1)
        mpls_layout.addWidget(QLabel("TTL:"), 0, 2)
        mpls_layout.addWidget(self.mpls_ttl_field, 0, 3)
        mpls_layout.addWidget(QLabel("Experimental:"), 0, 4)
        mpls_layout.addWidget(self.mpls_experimental_field, 0, 5)
        
        # Add minimum size constraints to MPLS fields (no max to allow expansion)
        self.mpls_label_field.setMinimumWidth(60)
        self.mpls_ttl_field.setMinimumWidth(60)
        self.mpls_experimental_field.setMinimumWidth(60)

        mpls_group.setLayout(mpls_layout)
        mpls_group.setMaximumHeight(70)
        self.mpls_group = mpls_group

    def add_vlan_section(self):
        """Adds the VLAN section to the Protocol Data tab and wires enable/disable."""
        self.vlan_group = QGroupBox("VLAN")
        vlan_layout = QGridLayout()
        vlan_layout.setSpacing(8)
        # Make columns expand to fill available space
        for col in range(8):
            if col % 2 == 0:  # Label columns
                vlan_layout.setColumnStretch(col, 0)
            else:  # Input columns
                vlan_layout.setColumnStretch(col, 1)
        vlan_layout.setColumnMinimumWidth(1, 60)
        vlan_layout.setColumnMinimumWidth(3, 60)
        vlan_layout.setColumnMinimumWidth(5, 60)

        # VLAN ID, Priority, CFI/DEI, and Override TPID in the same row
        vlan_layout.addWidget(QLabel("VLAN ID:"), 0, 0)
        self.vlan_id_field = QLineEdit("10")
        self.vlan_id_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.vlan_id_field, 0, 1)

        vlan_layout.addWidget(QLabel("Priority:"), 0, 2)
        self.priority_field = QComboBox()
        self.priority_field.addItems([str(i) for i in range(8)])
        self.priority_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.priority_field, 0, 3)

        vlan_layout.addWidget(QLabel("CFI/DEI:"), 0, 4)
        self.cfi_dei_field = QComboBox()
        self.cfi_dei_field.addItems(["0", "1"])
        self.cfi_dei_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.cfi_dei_field, 0, 5)

        self.override_tpid_checkbox = QCheckBox("Override TPID")
        vlan_layout.addWidget(self.override_tpid_checkbox, 0, 6)

        self.tpid_field = QLineEdit("81 00")
        self.tpid_field.setDisabled(True)
        self.tpid_field.setMinimumWidth(60)
        vlan_layout.addWidget(self.tpid_field, 0, 7)

        # Connect checkbox to enable/disable TPID field
        self.override_tpid_checkbox.toggled.connect(self.tpid_field.setEnabled)

        # Increment VLAN Option
        self.vlan_increment_checkbox = QCheckBox("Increment VLAN")
        vlan_layout.addWidget(self.vlan_increment_checkbox, 1, 0)

        self.vlan_increment_value = QLineEdit("1")
        self.vlan_increment_value.setValidator(QIntValidator(1, 4094))
        self.vlan_increment_value.setDisabled(True)
        vlan_layout.addWidget(QLabel("Increment Value"), 1, 1)
        vlan_layout.addWidget(self.vlan_increment_value, 1, 2)

        self.vlan_increment_count = QLineEdit("1")
        self.vlan_increment_count.setValidator(QIntValidator(1, 4094))
        self.vlan_increment_count.setDisabled(True)
        vlan_layout.addWidget(QLabel("Increment Count"), 1, 3)
        vlan_layout.addWidget(self.vlan_increment_count, 1, 4)

        # Enable increment fields only when the checkbox is checked
        self.vlan_increment_checkbox.toggled.connect(
            lambda checked: (
                self.vlan_increment_value.setEnabled(checked),
                self.vlan_increment_count.setEnabled(checked),
            )
        )

        self.vlan_group.setLayout(vlan_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state (enabled only if Tagged or Stacked)
        try:
            self.vlan_group.setEnabled(self.vlan_tagged.isChecked() or self.vlan_stacked.isChecked())
        except Exception:
            self.vlan_group.setEnabled(False)

    def refresh_vlan_section(self):
        """Enable VLAN config only when VLAN selection is Tagged or Stacked."""
        enabled = False
        try:
            enabled = self.vlan_tagged.isChecked() or self.vlan_stacked.isChecked()
        except Exception:
            pass
        if hasattr(self, "vlan_group"):
            self.vlan_group.setEnabled(bool(enabled))

    def add_ipv4_section(self):
        """Adds the IPv4 section to the Protocol Data tab."""
        self.ipv4_group = QGroupBox("Internet Protocol ver 4")
        ipv4_layout = QGridLayout()
        ipv4_layout.setSpacing(8)
        ipv4_layout.setColumnStretch(1, 1)  # Allow IP field to expand
        ipv4_layout.setColumnMinimumWidth(1, 110)  # Minimum width for IP field

        # Source IP
        ipv4_layout.addWidget(QLabel("Source IP:"), 0, 0)
        self.source_field = QLineEdit("0.0.0.0")
        self.source_field.setMinimumWidth(110)
        ipv4_layout.addWidget(self.source_field, 0, 1)

        self.source_mode_dropdown = QComboBox()
        self.source_mode_dropdown.addItems(["Fixed", "Increment"])
        self.source_mode_dropdown.setMinimumWidth(90)
        self.source_mode_dropdown.setMaximumWidth(110)
        ipv4_layout.addWidget(self.source_mode_dropdown, 0, 2)

        self.source_increment_step = QLineEdit("1")
        self.source_increment_step.setValidator(QIntValidator(1, 255))
        self.source_increment_step.setMinimumWidth(50)
        self.source_increment_step.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Step:"), 0, 3)
        ipv4_layout.addWidget(self.source_increment_step, 0, 4)

        self.source_increment_count = QLineEdit("1")
        self.source_increment_count.setValidator(QIntValidator(1, 255))
        self.source_increment_count.setMinimumWidth(50)
        self.source_increment_count.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Count:"), 0, 5)
        ipv4_layout.addWidget(self.source_increment_count, 0, 6)

        self.source_mode_dropdown.currentIndexChanged.connect(
            lambda idx: (
                self.source_increment_step.setEnabled(idx == 1),
                self.source_increment_count.setEnabled(idx == 1)
            )
        )

        # Destination IP
        ipv4_layout.addWidget(QLabel("Destination IP:"), 1, 0)
        self.destination_field = QLineEdit("0.0.0.0")
        self.destination_field.setMinimumWidth(110)
        ipv4_layout.addWidget(self.destination_field, 1, 1)

        self.destination_mode_dropdown = QComboBox()
        self.destination_mode_dropdown.addItems(["Fixed", "Increment"])
        self.destination_mode_dropdown.setMinimumWidth(90)
        self.destination_mode_dropdown.setMaximumWidth(110)
        ipv4_layout.addWidget(self.destination_mode_dropdown, 1, 2)

        self.destination_increment_step = QLineEdit("1")
        self.destination_increment_step.setValidator(QIntValidator(1, 255))
        self.destination_increment_step.setMinimumWidth(50)
        self.destination_increment_step.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Step:"), 1, 3)
        ipv4_layout.addWidget(self.destination_increment_step, 1, 4)

        self.destination_increment_count = QLineEdit("1")
        self.destination_increment_count.setValidator(QIntValidator(1, 255))
        self.destination_increment_count.setMinimumWidth(50)
        self.destination_increment_count.setMaximumWidth(70)
        ipv4_layout.addWidget(QLabel("Count:"), 1, 5)
        ipv4_layout.addWidget(self.destination_increment_count, 1, 6)

        self.destination_mode_dropdown.currentIndexChanged.connect(
            lambda idx: (
                self.destination_increment_step.setEnabled(idx == 1),
                self.destination_increment_count.setEnabled(idx == 1)
            )
        )

        # Misc
        ipv4_layout.addWidget(QLabel("TTL"), 2, 0)
        self.ttl_field = QLineEdit("64")
        self.ttl_field.setValidator(QIntValidator(1, 255))
        ipv4_layout.addWidget(self.ttl_field, 2, 1)

        self.df_checkbox = QCheckBox("Don't Fragment (DF)")
        ipv4_layout.addWidget(self.df_checkbox, 2, 2)

        self.mf_checkbox = QCheckBox("More Fragments (MF)")
        ipv4_layout.addWidget(self.mf_checkbox, 2, 3)

        ipv4_layout.addWidget(QLabel("Fragment Offset"), 2, 4)
        self.fragment_offset_field = QLineEdit("0")
        self.fragment_offset_field.setValidator(QIntValidator(0, 8191))
        ipv4_layout.addWidget(self.fragment_offset_field, 2, 5)

        ipv4_layout.addWidget(QLabel("Identification"), 2, 6)
        self.identification_field = QLineEdit("0000")
        self.identification_field.setValidator(QIntValidator(0, 65535))
        ipv4_layout.addWidget(self.identification_field, 2, 7)

        # ToS / DSCP / Custom
        tos_label = QLabel("ToS/DSCP Mode")
        tos_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        ipv4_layout.addWidget(tos_label, 3, 0)

        self.tos_dscp_custom_mode = QComboBox()
        self.tos_dscp_custom_mode.addItems(["TOS", "DSCP", "Custom"])
        self.tos_dscp_custom_mode.setFixedWidth(100)
        ipv4_layout.addWidget(self.tos_dscp_custom_mode, 3, 1)

        self.tos_dscp_custom_stack = QStackedWidget()
        ipv4_layout.addWidget(self.tos_dscp_custom_stack, 3, 2, 1, 3)

        ecn_label = QLabel("ECN")
        ecn_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        ipv4_layout.addWidget(ecn_label, 3, 5)

        self.ecn_dropdown = QComboBox()
        self.ecn_dropdown.addItems(["CE", "Not-ECT", "ECT(1)", "ECT(0)"])
        self.ecn_dropdown.setFixedWidth(100)
        ipv4_layout.addWidget(self.ecn_dropdown, 3, 6)

        # TOS widget
        tos_widget = QWidget()
        tos_layout = QHBoxLayout(tos_widget)
        tos_layout.setContentsMargins(0, 0, 0, 0)
        self.tos_dropdown = QComboBox()
        self.tos_dropdown.addItems([
            "Routine", "Priority", "Immediate", "Flash", "Flash Override",
            "Critical", "Internetwork Control", "Network Control"
        ])
        self.tos_dropdown.setFixedWidth(150)
        tos_layout.addWidget(self.tos_dropdown)

        # DSCP widget
        dscp_widget = QWidget()
        dscp_layout = QHBoxLayout(dscp_widget)
        dscp_layout.setContentsMargins(0, 0, 0, 0)
        self.dscp_dropdown = QComboBox()
        self.dscp_dropdown.addItems([
            "cs0", "cs1", "cs2", "cs3", "cs4", "cs5", "cs6", "cs7",
            "af11", "af12", "af13", "af21", "af22", "af23",
            "af31", "af32", "af33", "af41", "af42", "af43", "ef"
        ])
        self.dscp_dropdown.setFixedWidth(150)
        dscp_layout.addWidget(self.dscp_dropdown)

        # Custom widget
        custom_widget = QWidget()
        custom_layout = QHBoxLayout(custom_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_tos_field = QLineEdit("")
        self.custom_tos_field.setPlaceholderText("Custom ToS (0-255)")
        self.custom_tos_field.setValidator(QIntValidator(0, 255))
        self.custom_tos_field.setFixedWidth(150)
        custom_layout.addWidget(self.custom_tos_field)

        self.tos_dscp_custom_stack.addWidget(tos_widget)  # index 0
        self.tos_dscp_custom_stack.addWidget(dscp_widget)  # index 1
        self.tos_dscp_custom_stack.addWidget(custom_widget)  # index 2

        self.tos_dscp_custom_mode.currentIndexChanged.connect(
            lambda idx: self.tos_dscp_custom_stack.setCurrentIndex(idx)
        )

        # Assemble group
        self.ipv4_group.setLayout(ipv4_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enable/disable of increment fields (Fixed by default)
        self.source_increment_step.setEnabled(False)
        self.source_increment_count.setEnabled(False)
        self.destination_increment_step.setEnabled(False)
        self.destination_increment_count.setEnabled(False)

        # Initial enabled state of the whole IPv4 group per L3 radios
        try:
            self.ipv4_group.setEnabled(self.l3_ipv4.isChecked())
        except Exception:
            self.ipv4_group.setEnabled(False)

    def add_ipv6_section(self):
        """Adds the IPv6 section to the Protocol Data tab."""
        self.ipv6_group = QGroupBox("IPv6")
        ipv6_layout = QGridLayout()

        # Source Address + mode
        ipv6_layout.addWidget(QLabel("Source Address"), 0, 0)
        self.ipv6_source_field = QLineEdit("2001:db8::1")
        ipv6_layout.addWidget(self.ipv6_source_field, 0, 1)

        ipv6_layout.addWidget(QLabel("Source Mode"), 0, 2)
        self.ipv6_source_mode_dropdown = QComboBox()
        self.ipv6_source_mode_dropdown.addItems(["Fixed", "Increment"])
        ipv6_layout.addWidget(self.ipv6_source_mode_dropdown, 0, 3)

        ipv6_layout.addWidget(QLabel("Source Step"), 0, 4)
        self.ipv6_source_increment_step = QLineEdit("1")
        self.ipv6_source_increment_step.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_source_increment_step, 0, 5)

        ipv6_layout.addWidget(QLabel("Source Count"), 0, 6)
        self.ipv6_source_increment_count = QLineEdit("1")
        self.ipv6_source_increment_count.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_source_increment_count, 0, 7)

        # Destination Address + mode
        ipv6_layout.addWidget(QLabel("Destination Address"), 1, 0)
        self.ipv6_destination_field = QLineEdit("2001:db8::2")
        ipv6_layout.addWidget(self.ipv6_destination_field, 1, 1)

        ipv6_layout.addWidget(QLabel("Destination Mode"), 1, 2)
        self.ipv6_destination_mode_dropdown = QComboBox()
        self.ipv6_destination_mode_dropdown.addItems(["Fixed", "Increment"])
        ipv6_layout.addWidget(self.ipv6_destination_mode_dropdown, 1, 3)

        ipv6_layout.addWidget(QLabel("Destination Step"), 1, 4)
        self.ipv6_destination_increment_step = QLineEdit("1")
        self.ipv6_destination_increment_step.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_destination_increment_step, 1, 5)

        ipv6_layout.addWidget(QLabel("Destination Count"), 1, 6)
        self.ipv6_destination_increment_count = QLineEdit("1")
        self.ipv6_destination_increment_count.setDisabled(True)
        ipv6_layout.addWidget(self.ipv6_destination_increment_count, 1, 7)

        # Misc
        ipv6_layout.addWidget(QLabel("Traffic Class"), 2, 0)
        self.ipv6_traffic_class_field = QLineEdit("0")
        self.ipv6_traffic_class_field.setValidator(QIntValidator(0, 255))
        ipv6_layout.addWidget(self.ipv6_traffic_class_field, 2, 1)

        ipv6_layout.addWidget(QLabel("Flow Label"), 2, 2)
        self.ipv6_flow_label_field = QLineEdit("0")
        self.ipv6_flow_label_field.setValidator(QIntValidator(0, 1_048_575))
        ipv6_layout.addWidget(self.ipv6_flow_label_field, 2, 3)

        ipv6_layout.addWidget(QLabel("Hop Limit"), 2, 4)
        self.ipv6_hop_limit_field = QLineEdit("64")
        self.ipv6_hop_limit_field.setValidator(QIntValidator(0, 255))
        ipv6_layout.addWidget(self.ipv6_hop_limit_field, 2, 5)

        # Mode toggles for increments
        self.ipv6_source_mode_dropdown.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.ipv6_source_increment_step, self.ipv6_source_increment_count
            )
        )
        self.ipv6_destination_mode_dropdown.currentTextChanged.connect(
            lambda mode: self.update_increment_fields(
                mode, self.ipv6_destination_increment_step, self.ipv6_destination_increment_count
            )
        )

        # Assemble group
        self.ipv6_group.setLayout(ipv6_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        # Initial enabled state of the whole IPv6 group per L3 radios
        try:
            self.ipv6_group.setEnabled(self.l3_ipv6.isChecked())
        except Exception:
            self.ipv6_group.setEnabled(False)

    def refresh_l3_sections(self):
        ipv4_on = hasattr(self, "l3_ipv4") and self.l3_ipv4.isChecked()
        ipv6_on = hasattr(self, "l3_ipv6") and self.l3_ipv6.isChecked()
        arp_on = hasattr(self, "l3_arp") and self.l3_arp.isChecked()

        if hasattr(self, "ipv4_group"):
            self.ipv4_group.setEnabled(ipv4_on)
        if hasattr(self, "ipv6_group"):
            self.ipv6_group.setEnabled(ipv6_on)
        if hasattr(self, "arp_group"):
            self.arp_group.setEnabled(arp_on)

    def update_increment_fields(self, mode, step_field, count_field):
        is_increment = mode == "Increment"
        step_field.setEnabled(is_increment)
        count_field.setEnabled(is_increment)

    def add_tcp_section(self):
        def validate_u32(field):
            try:
                v = int(field.text())
                if not (0 <= v <= 0xFFFFFFFF):
                    raise ValueError
            except ValueError:
                field.setText("0")

        self.tcp_group = QGroupBox("Transmission Control Protocol (stateless)")
        tcp_layout = QGridLayout()

        # Src port override + increment
        self.override_source_port_checkbox = QCheckBox("Override Source Port")
        tcp_layout.addWidget(self.override_source_port_checkbox, 0, 0)
        self.source_port_field = QLineEdit("0")
        self.source_port_field.setValidator(QIntValidator(0, 65535))
        self.source_port_field.setDisabled(True)
        tcp_layout.addWidget(self.source_port_field, 0, 1)
        self.override_source_port_checkbox.toggled.connect(self.source_port_field.setEnabled)

        self.increment_tcp_source_checkbox = QCheckBox("Increment Source Port")
        tcp_layout.addWidget(self.increment_tcp_source_checkbox, 0, 2)
        self.tcp_source_increment_step = QLineEdit("1")
        self.tcp_source_increment_step.setValidator(QIntValidator(1, 65535))
        self.tcp_source_increment_step.setDisabled(True)
        tcp_layout.addWidget(QLabel("Step"), 0, 3)
        tcp_layout.addWidget(self.tcp_source_increment_step, 0, 4)
        self.tcp_source_increment_count = QLineEdit("1")
        self.tcp_source_increment_count.setValidator(QIntValidator(1, 65535))
        self.tcp_source_increment_count.setDisabled(True)
        tcp_layout.addWidget(QLabel("Count"), 0, 5)
        tcp_layout.addWidget(self.tcp_source_increment_count, 0, 6)
        self.increment_tcp_source_checkbox.toggled.connect(
            lambda checked: [
                self.tcp_source_increment_step.setEnabled(checked),
                self.tcp_source_increment_count.setEnabled(checked),
            ]
        )

        # Dst port override + increment
        self.override_destination_port_checkbox = QCheckBox("Override Destination Port")
        tcp_layout.addWidget(self.override_destination_port_checkbox, 1, 0)
        self.destination_port_field = QLineEdit("0")
        self.destination_port_field.setValidator(QIntValidator(0, 65535))
        self.destination_port_field.setDisabled(True)
        tcp_layout.addWidget(self.destination_port_field, 1, 1)
        self.override_destination_port_checkbox.toggled.connect(self.destination_port_field.setEnabled)

        self.increment_tcp_destination_checkbox = QCheckBox("Increment Destination Port")
        tcp_layout.addWidget(self.increment_tcp_destination_checkbox, 1, 2)
        self.tcp_destination_increment_step = QLineEdit("1")
        self.tcp_destination_increment_step.setValidator(QIntValidator(1, 65535))
        self.tcp_destination_increment_step.setDisabled(True)
        tcp_layout.addWidget(QLabel("Step"), 1, 3)
        tcp_layout.addWidget(self.tcp_destination_increment_step, 1, 4)
        self.tcp_destination_increment_count = QLineEdit("1")
        self.tcp_destination_increment_count.setValidator(QIntValidator(1, 65535))
        self.tcp_destination_increment_count.setDisabled(True)
        tcp_layout.addWidget(QLabel("Count"), 1, 5)
        tcp_layout.addWidget(self.tcp_destination_increment_count, 1, 6)
        self.increment_tcp_destination_checkbox.toggled.connect(
            lambda checked: [
                self.tcp_destination_increment_step.setEnabled(checked),
                self.tcp_destination_increment_count.setEnabled(checked),
            ]
        )

        # Seq/Ack/Window/Checksum
        tcp_layout.addWidget(QLabel("Seq No"), 2, 0)
        self.sequence_number_field = QLineEdit("129018")
        tcp_layout.addWidget(self.sequence_number_field, 2, 1)
        self.sequence_number_field.editingFinished.connect(lambda: validate_u32(self.sequence_number_field))

        tcp_layout.addWidget(QLabel("Ack No"), 2, 2)
        self.acknowledgement_number_field = QLineEdit("0")
        tcp_layout.addWidget(self.acknowledgement_number_field, 2, 3)
        self.acknowledgement_number_field.editingFinished.connect(lambda: validate_u32(self.acknowledgement_number_field))

        tcp_layout.addWidget(QLabel("Window"), 2, 4)
        self.window_field = QLineEdit("1024")
        self.window_field.setValidator(QIntValidator(1, 65535))
        tcp_layout.addWidget(self.window_field, 2, 5)

        self.override_checksum_checkbox = QCheckBox("Override Checksum")
        tcp_layout.addWidget(self.override_checksum_checkbox, 2, 6)
        self.tcp_checksum_field = QLineEdit("B3 E7")
        self.tcp_checksum_field.setDisabled(True)
        tcp_layout.addWidget(self.tcp_checksum_field, 2, 7)
        self.override_checksum_checkbox.toggled.connect(self.tcp_checksum_field.setEnabled)

        # Flags
        flags_group = QGroupBox("Flags")
        flags_layout = QGridLayout()
        self.flag_urg = QCheckBox("URG")
        self.flag_ack = QCheckBox("ACK")
        self.flag_psh = QCheckBox("PSH")
        self.flag_rst = QCheckBox("RST")
        self.flag_syn = QCheckBox("SYN")
        self.flag_fin = QCheckBox("FIN")
        for i, w in enumerate([self.flag_urg, self.flag_ack, self.flag_psh, self.flag_rst, self.flag_syn, self.flag_fin]):
            flags_layout.addWidget(w, i // 3, i % 3)
        flags_group.setLayout(flags_layout)
        tcp_layout.addWidget(flags_group, 4, 0, 1, 6)

        self.tcp_group.setLayout(tcp_layout)
        try:
            self.tcp_group.setEnabled(self.l4_tcp.isChecked())
        except AttributeError:
            self.tcp_group.setEnabled(False)
        # Widget will be added to grid layout in setup_protocol_data_tab

    def add_udp_section(self):
        self.udp_group = QGroupBox("User Datagram Protocol (stateless)")
        layout = QGridLayout()

        # Src override + increment
        self.override_udp_source_port_checkbox = QCheckBox("Override Source Port")
        layout.addWidget(self.override_udp_source_port_checkbox, 0, 0)
        self.udp_source_port_field = QLineEdit("0")
        self.udp_source_port_field.setValidator(QIntValidator(0, 65535))
        self.udp_source_port_field.setDisabled(True)
        layout.addWidget(self.udp_source_port_field, 0, 1)
        self.override_udp_source_port_checkbox.toggled.connect(self.udp_source_port_field.setEnabled)

        self.udp_increment_source_checkbox = QCheckBox("Increment Source Port")
        layout.addWidget(self.udp_increment_source_checkbox, 0, 2)
        self.udp_source_increment_step = QLineEdit("1")
        self.udp_source_increment_step.setValidator(QIntValidator(1, 65535))
        self.udp_source_increment_step.setDisabled(True)
        layout.addWidget(QLabel("Step"), 0, 3)
        layout.addWidget(self.udp_source_increment_step, 0, 4)
        self.udp_source_increment_count = QLineEdit("1")
        self.udp_source_increment_count.setValidator(QIntValidator(1, 65535))
        self.udp_source_increment_count.setDisabled(True)
        layout.addWidget(QLabel("Count"), 0, 5)
        layout.addWidget(self.udp_source_increment_count, 0, 6)
        self.udp_increment_source_checkbox.toggled.connect(
            lambda checked: [
                self.udp_source_increment_step.setEnabled(checked),
                self.udp_source_increment_count.setEnabled(checked),
            ]
        )

        # Dst override + increment
        self.override_udp_destination_port_checkbox = QCheckBox("Override Destination Port")
        layout.addWidget(self.override_udp_destination_port_checkbox, 1, 0)
        self.udp_destination_port_field = QLineEdit("0")
        self.udp_destination_port_field.setValidator(QIntValidator(0, 65535))
        self.udp_destination_port_field.setDisabled(True)
        layout.addWidget(self.udp_destination_port_field, 1, 1)
        self.override_udp_destination_port_checkbox.toggled.connect(self.udp_destination_port_field.setEnabled)

        self.udp_increment_destination_checkbox = QCheckBox("Increment Destination Port")
        layout.addWidget(self.udp_increment_destination_checkbox, 1, 2)
        self.udp_destination_increment_step = QLineEdit("1")
        self.udp_destination_increment_step.setValidator(QIntValidator(1, 65535))
        self.udp_destination_increment_step.setDisabled(True)
        layout.addWidget(QLabel("Step"), 1, 3)
        layout.addWidget(self.udp_destination_increment_step, 1, 4)
        self.udp_destination_increment_count = QLineEdit("1")
        self.udp_destination_increment_count.setValidator(QIntValidator(1, 65535))
        self.udp_destination_increment_count.setDisabled(True)
        layout.addWidget(QLabel("Count"), 1, 5)
        layout.addWidget(self.udp_destination_increment_count, 1, 6)
        self.udp_increment_destination_checkbox.toggled.connect(
            lambda checked: [
                self.udp_destination_increment_step.setEnabled(checked),
                self.udp_destination_increment_count.setEnabled(checked),
            ]
        )

        # Checksum
        self.override_udp_checksum_checkbox = QCheckBox("Override Checksum")
        layout.addWidget(self.override_udp_checksum_checkbox, 2, 0)
        self.udp_checksum_field = QLineEdit("")
        self.udp_checksum_field.setDisabled(True)
        layout.addWidget(self.udp_checksum_field, 2, 1)
        self.override_udp_checksum_checkbox.toggled.connect(self.udp_checksum_field.setEnabled)

        # Presets
        layout.addWidget(QLabel("Preset:"), 2, 2)
        self.udp_preset_combo = QComboBox()
        self.udp_preset_combo.addItems([
            "Custom",
            "BOOTP/DHCPv4 (client→server 68→67)",
            "BOOTP/DHCPv4 (server→client 67→68)",
            "DHCPv6 (546→547)",
            "DNS (53)",
            "TFTP (69)",
            "NTP (123)",
            "RADIUS Auth (1812)",
            "RADIUS Acct (1813)",
            "SIP (5060)",
            "VXLAN (4789)",
            "QUIC (443/UDP)",
            "Syslog (514)"
        ])
        layout.addWidget(self.udp_preset_combo, 2, 3, 1, 2)

        def apply_udp_preset(_):
            preset = self.udp_preset_combo.currentText()
            is_custom = preset.startswith("Custom")
            self.override_udp_source_port_checkbox.setChecked(not is_custom)
            self.override_udp_destination_port_checkbox.setChecked(not is_custom)

            mapping = {
                "BOOTP/DHCPv4 (client→server 68→67)": (68, 67),
                "BOOTP/DHCPv4 (server→client 67→68)": (67, 68),
                "DHCPv6 (546→547)": (546, 547),
                "DNS (53)": (0, 53),
                "TFTP (69)": (0, 69),
                "NTP (123)": (0, 123),
                "RADIUS Auth (1812)": (0, 1812),
                "RADIUS Acct (1813)": (0, 1813),
                "SIP (5060)": (0, 5060),
                "VXLAN (4789)": (0, 4789),
                "QUIC (443/UDP)": (0, 443),
                "Syslog (514)": (0, 514),
            }
            if preset in mapping:
                s, d = mapping[preset]
                if s == 0:
                    s = int(self.udp_source_port_field.text() or "0")
                self.udp_source_port_field.setText(str(s))
                self.udp_destination_port_field.setText(str(d))

            self.udp_bootp_enable_checkbox.setChecked(
                preset.startswith("BOOTP") or preset.startswith("DHCPv6")
            )

        self.udp_preset_combo.currentIndexChanged.connect(apply_udp_preset)

        # BOOTP/DHCP helper
        bootp_group = QGroupBox("BOOTP / DHCP Options (optional)")
        bootp_layout = QGridLayout()

        self.udp_bootp_enable_checkbox = QCheckBox("Enable BOOTP/DHCP template")
        bootp_layout.addWidget(self.udp_bootp_enable_checkbox, 0, 0, 1, 2)

        bootp_layout.addWidget(QLabel("Message Type"), 1, 0)
        self.bootp_msg_type = QComboBox()
        self.bootp_msg_type.addItems(
            ["DHCPDISCOVER", "DHCPOFFER", "DHCPREQUEST", "DHCPACK", "DHCPNAK", "BOOTREQUEST", "BOOTREPLY"])
        bootp_layout.addWidget(self.bootp_msg_type, 1, 1)

        bootp_layout.addWidget(QLabel("Transaction ID (hex)"), 1, 2)
        self.bootp_xid = QLineEdit("0x12345678")
        bootp_layout.addWidget(self.bootp_xid, 1, 3)

        bootp_layout.addWidget(QLabel("Client MAC"), 2, 0)
        self.bootp_client_mac = QLineEdit("00:11:22:33:44:55")
        bootp_layout.addWidget(self.bootp_client_mac, 2, 1)

        bootp_layout.addWidget(QLabel("Flags (hex)"), 2, 2)
        self.bootp_flags = QLineEdit("0x0000")
        bootp_layout.addWidget(self.bootp_flags, 2, 3)

        labels = ["ciaddr", "yiaddr", "siaddr", "giaddr"]
        defaults = ["0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0"]
        self.bootp_addrs = {}
        row = 3
        for i, (lab, dflt) in enumerate(zip(labels, defaults)):
            bootp_layout.addWidget(QLabel(lab.upper()), row + i // 2, (i % 2) * 2)
            field = QLineEdit(dflt)
            self.bootp_addrs[lab] = field
            bootp_layout.addWidget(field, row + i // 2, (i % 2) * 2 + 1)

        bootp_layout.addWidget(QLabel("Hostname (opt 12)"), 5, 0)
        self.bootp_hostname = QLineEdit("")
        bootp_layout.addWidget(self.bootp_hostname, 5, 1)

        bootp_layout.addWidget(QLabel("Param Req List (opt 55, CSV)"), 5, 2)
        self.bootp_prl = QLineEdit("1,3,6,15,28,51,58,59")
        bootp_layout.addWidget(self.bootp_prl, 5, 3)

        bootp_group.setLayout(bootp_layout)
        # default disabled until checkbox ticked
        for w in bootp_group.findChildren(QWidget):
            if w is not self.udp_bootp_enable_checkbox:
                w.setEnabled(False)

        def toggle_bootp(enabled: bool):
            for w in bootp_group.findChildren(QWidget):
                if w is not self.udp_bootp_enable_checkbox:
                    w.setEnabled(enabled)

        self.udp_bootp_enable_checkbox.toggled.connect(toggle_bootp)

        layout.addWidget(bootp_group, 3, 0, 1, 7)

        self.udp_group.setLayout(layout)
        self.protocol_data_layout.addWidget(self.udp_group)

    def refresh_l4_sections(self):
        tcp_on = hasattr(self, "l4_tcp") and self.l4_tcp.isChecked()
        udp_on = hasattr(self, "l4_udp") and self.l4_udp.isChecked()
        roce_on = hasattr(self, "l4_rocev2") and self.l4_rocev2.isChecked()
        uec_on = hasattr(self, "l4_uec") and self.l4_uec.isChecked()
        embed_roce = hasattr(self, "uec_enable_rocev2_checkbox") and self.uec_enable_rocev2_checkbox.isChecked()

        if hasattr(self, "tcp_group"):    self.tcp_group.setEnabled(tcp_on)
        if hasattr(self, "udp_group"):    self.udp_group.setEnabled(udp_on)
        if hasattr(self, "rocev2_group"): self.rocev2_group.setEnabled(roce_on or (uec_on and embed_roce))
        if hasattr(self, "uec_group"):    self.uec_group.setEnabled(uec_on)
        # If ARP is selected at L3, disable all explicit L4 groups (no TCP/UDP over ARP)
        if hasattr(self, "l3_arp") and self.l3_arp.isChecked():
            if hasattr(self, "tcp_group"):  self.tcp_group.setEnabled(False)
            if hasattr(self, "udp_group"):  self.udp_group.setEnabled(False)
    def add_rocev2_section(self):
        self.rocev2_group = QGroupBox("RoCEv2 (RDMA over Converged Ethernet v2)")
        rocev2_layout = QGridLayout()

        rocev2_layout.addWidget(QLabel("Traffic Class (0–7):"), 0, 0)
        self.rocev2_traffic_class = QComboBox()
        self.rocev2_traffic_class.addItems([str(i) for i in range(8)])
        rocev2_layout.addWidget(self.rocev2_traffic_class, 0, 1)

        rocev2_layout.addWidget(QLabel("Flow Label (Hex):"), 0, 2)
        self.rocev2_flow_label = QLineEdit("000000")
        self.rocev2_flow_label.setMaxLength(6)
        rocev2_layout.addWidget(self.rocev2_flow_label, 0, 3)

        rocev2_layout.addWidget(QLabel("Source QP:"), 0, 4)
        self.rocev2_source_qp = QLineEdit("0")
        self.rocev2_source_qp.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_source_qp, 0, 5)

        rocev2_layout.addWidget(QLabel("Destination QP:"), 0, 6)
        self.rocev2_destination_qp = QLineEdit("0")
        self.rocev2_destination_qp.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_destination_qp, 0, 7)

        rocev2_layout.addWidget(QLabel("Source GID:"), 1, 0)
        self.rocev2_source_gid = QLineEdit("0:0:0:0:0:ffff:192.168.0.2")
        rocev2_layout.addWidget(self.rocev2_source_gid, 1, 1, 1, 3)

        rocev2_layout.addWidget(QLabel("Source GID Step:"), 1, 4)
        self.rocev2_source_gid_step = QLineEdit("0:0:0:0:0:0:0:1")
        rocev2_layout.addWidget(self.rocev2_source_gid_step, 1, 5, 1, 3)

        rocev2_layout.addWidget(QLabel("GID Source Mode:"), 2, 0)
        self.rocev2_gid_source_mode = QComboBox()
        self.rocev2_gid_source_mode.addItems(["Fixed", "Increment"])
        rocev2_layout.addWidget(self.rocev2_gid_source_mode, 2, 1)

        rocev2_layout.addWidget(QLabel("GID Source Step:"), 2, 2)
        self.rocev2_gid_source_step = QLineEdit("1")
        self.rocev2_gid_source_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_source_step, 2, 3)

        rocev2_layout.addWidget(QLabel("GID Source Count:"), 2, 4)
        self.rocev2_gid_source_count = QLineEdit("1")
        self.rocev2_gid_source_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_source_count, 2, 5)

        rocev2_layout.addWidget(QLabel("Destination GID:"), 3, 0)
        self.rocev2_destination_gid = QLineEdit("0:0:0:0:0:ffff:192.168.0.3")
        rocev2_layout.addWidget(self.rocev2_destination_gid, 3, 1, 1, 3)

        rocev2_layout.addWidget(QLabel("Destination GID Step:"), 3, 4)
        self.rocev2_destination_gid_step = QLineEdit("0:0:0:0:0:0:0:1")
        rocev2_layout.addWidget(self.rocev2_destination_gid_step, 3, 5, 1, 3)

        rocev2_layout.addWidget(QLabel("GID Destination Mode:"), 4, 0)
        self.rocev2_gid_destination_mode = QComboBox()
        self.rocev2_gid_destination_mode.addItems(["Fixed", "Increment"])
        rocev2_layout.addWidget(self.rocev2_gid_destination_mode, 4, 1)

        rocev2_layout.addWidget(QLabel("GID Destination Step:"), 4, 2)
        self.rocev2_gid_destination_step = QLineEdit("1")
        self.rocev2_gid_destination_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_destination_step, 4, 3)

        rocev2_layout.addWidget(QLabel("GID Destination Count:"), 4, 4)
        self.rocev2_gid_destination_count = QLineEdit("1")
        self.rocev2_gid_destination_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_gid_destination_count, 4, 5)

        rocev2_layout.addWidget(QLabel("Opcode:"), 5, 0)
        self.rocev2_opcode = QComboBox()
        self.rocev2_opcode.addItems([
            "SendOnly", "SendOnlySolicited", "SendLast", "SendLastSolicited",
            "RDMAWrite", "RDMAWriteOnlyImm", "RDMAReadRequest", "RDMAReadResponse",
            "AtomicCompareSwap", "AtomicFetchAdd", "CNP"
        ])
        rocev2_layout.addWidget(self.rocev2_opcode, 5, 1)

        rocev2_layout.addWidget(QLabel("Solicited Event:"), 5, 2)
        self.rocev2_solicited_event = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_solicited_event, 5, 3)

        rocev2_layout.addWidget(QLabel("Migration Req:"), 5, 4)
        self.rocev2_migration_req = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_migration_req, 5, 5)

        rocev2_layout.addWidget(QLabel("QP Count:"), 5, 6)
        self.rocev2_qp_count = QLineEdit("1")
        self.rocev2_qp_count.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_qp_count, 5, 7)

        rocev2_layout.addWidget(QLabel("Increment QP:"), 6, 0)
        self.rocev2_qp_increment = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_qp_increment, 6, 1)

        rocev2_layout.addWidget(QLabel("QP Increment Step:"), 6, 2)
        self.rocev2_qp_increment_step = QLineEdit("1")
        self.rocev2_qp_increment_step.setValidator(Unsigned32BitValidator())
        rocev2_layout.addWidget(self.rocev2_qp_increment_step, 6, 3)

        rocev2_layout.addWidget(QLabel("Send CNP:"), 6, 4)
        self.rocev2_send_cnp = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_send_cnp, 6, 5)

        rocev2_layout.addWidget(QLabel("Increment Source GID:"), 6, 6)
        self.rocev2_increment_source_gid = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_increment_source_gid, 6, 7)

        rocev2_layout.addWidget(QLabel("Increment Destination GID:"), 7, 0)
        self.rocev2_increment_destination_gid = QCheckBox()
        rocev2_layout.addWidget(self.rocev2_increment_destination_gid, 7, 1)

        self.rocev2_use_perf_server = QCheckBox("Use RoCEv2 Performance Server (ib_write_bw)")
        rocev2_layout.addWidget(self.rocev2_use_perf_server, 7, 2, 1, 3)

        self.rocev2_group.setLayout(rocev2_layout)
        self.protocol_data_layout.addWidget(self.rocev2_group)

        try:
            roce_selected = self.l4_rocev2.isChecked()
            uec_selected_and_embed = hasattr(self, "uec_enable_rocev2_checkbox") and \
                                     self.l4_uec.isChecked() and self.uec_enable_rocev2_checkbox.isChecked()
            self.rocev2_group.setEnabled(bool(roce_selected or uec_selected_and_embed))
        except Exception:
            self.rocev2_group.setEnabled(False)

    def add_uec_section(self):
        self.uec_group = QGroupBox("Ultra Ethernet Consortium (UEC)")
        uec_layout = QGridLayout()

        self.uec_qp_start_field = QLineEdit("1000")
        self.uec_qp_end_field = QLineEdit("1010")
        self.uec_qp_start_field.setValidator(QIntValidator(0, 2 ** 24 - 1))
        self.uec_qp_end_field.setValidator(QIntValidator(0, 2 ** 24 - 1))
        uec_layout.addWidget(QLabel("QP Start:"), 0, 0)
        uec_layout.addWidget(self.uec_qp_start_field, 0, 1)
        uec_layout.addWidget(QLabel("QP End:"), 0, 2)
        uec_layout.addWidget(self.uec_qp_end_field, 0, 3)

        self.uec_pasid_start_field = QLineEdit("5000")
        self.uec_pasid_end_field = QLineEdit("5010")
        self.uec_pasid_start_field.setValidator(QIntValidator(0, 2 ** 20 - 1))
        self.uec_pasid_end_field.setValidator(QIntValidator(0, 2 ** 20 - 1))
        uec_layout.addWidget(QLabel("PASID Start:"), 1, 0)
        uec_layout.addWidget(self.uec_pasid_start_field, 1, 1)
        uec_layout.addWidget(QLabel("PASID End:"), 1, 2)
        uec_layout.addWidget(self.uec_pasid_end_field, 1, 3)

        self.uec_ecn_combo_box = QComboBox()
        self.uec_ecn_combo_box.addItems(["Not-ECT", "ECT(1)", "ECT(0)", "CE"])
        uec_layout.addWidget(QLabel("ECN:"), 2, 0)
        uec_layout.addWidget(self.uec_ecn_combo_box, 2, 1)

        self.uec_flow_label_field = QLineEdit("0")
        self.uec_flow_label_field.setValidator(QIntValidator(0, 1_048_575))
        uec_layout.addWidget(QLabel("Flow Label:"), 2, 2)
        uec_layout.addWidget(self.uec_flow_label_field, 2, 3)

        self.uec_enable_spray_checkbox = QCheckBox("Enable QP/PASID Spray")
        uec_layout.addWidget(self.uec_enable_spray_checkbox, 3, 0, 1, 2)

        self.uec_enable_rocev2_checkbox = QCheckBox("Include RoCEv2 inside UEC frame")
        uec_layout.addWidget(self.uec_enable_rocev2_checkbox, 3, 2, 1, 2)
        self.uec_enable_rocev2_checkbox.toggled.connect(self.refresh_l4_sections)

        self.uec_group.setLayout(uec_layout)
        # Widget will be added to grid layout in setup_protocol_data_tab

        try:
            self.uec_group.setEnabled(self.l4_uec.isChecked())
        except Exception:
            self.uec_group.setEnabled(False)

    def add_payload_data_section(self):
        payload_group = QGroupBox("Payload Data")
        payload_layout = QVBoxLayout()
        self.payload_data_field = QLineEdit("0000")
        payload_layout.addWidget(QLabel("Data:"))
        payload_layout.addWidget(self.payload_data_field)
        payload_group.setLayout(payload_layout)
        self.payload_group = payload_group

    # ----------------------------- PCAP Tab -----------------------------

    def setup_pcap_tab(self):
        outer_layout = QVBoxLayout()
        outer_layout.setSpacing(20)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        pcap_group = QGroupBox("PCAP Replay Settings")
        pcap_form_layout = QFormLayout()
        pcap_form_layout.setSpacing(12)
        pcap_form_layout.setContentsMargins(16, 20, 16, 16)

        self.enable_pcap_checkbox = QCheckBox("Enable PCAP Replay")
        self.enable_pcap_checkbox.stateChanged.connect(self.toggle_pcap_controls)
        outer_layout.addWidget(self.enable_pcap_checkbox)

        pcap_file_layout = QHBoxLayout()
        self.pcap_file_path = QLineEdit()
        self.pcap_file_path.setPlaceholderText("Path to PCAP file")
        self.browse_pcap_button = QPushButton("Browse")
        self.browse_pcap_button.clicked.connect(self.browse_pcap_file)
        pcap_file_layout.addWidget(self.pcap_file_path)
        pcap_file_layout.addWidget(self.browse_pcap_button)
        pcap_form_layout.addRow("PCAP File:", pcap_file_layout)

        self.pcap_metadata_label = QLabel()
        self.pcap_metadata_label.setWordWrap(True)
        self.pcap_metadata_label.setStyleSheet("color: gray;")
        pcap_form_layout.addRow("", self.pcap_metadata_label)

        self.pcap_loop_count = QSpinBox()
        self.pcap_loop_count.setRange(1, 1_000_000)
        self.pcap_loop_count.setValue(1)
        pcap_form_layout.addRow("Loop Count:", self.pcap_loop_count)

        self.pcap_rate_mode = QComboBox()
        self.pcap_rate_mode.addItems(["Original Timing", "Fixed Delay", "Inter-Packet Gap"])
        pcap_form_layout.addRow("Replay Rate Mode:", self.pcap_rate_mode)

        pcap_group.setLayout(pcap_form_layout)
        outer_layout.addWidget(pcap_group)
        self.pcap_tab.setLayout(outer_layout)
        self.toggle_pcap_controls()

        self.pcap_file_path.textChanged.connect(self.validate_pcap_file)

    def toggle_pcap_controls(self):
        enabled = self.enable_pcap_checkbox.isChecked()
        for w in (self.pcap_file_path, self.browse_pcap_button, self.pcap_loop_count, self.pcap_rate_mode):
            w.setEnabled(enabled)
        self.pcap_metadata_label.setVisible(enabled)
        if not enabled:
            self.pcap_metadata_label.clear()

    def browse_pcap_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PCAP File", "", "PCAP Files (*.pcap *.pcapng);;All Files (*)")
        if path:
            self.pcap_file_path.setText(path)

    def validate_pcap_file(self):
        path = self.pcap_file_path.text().strip()
        if os.path.isfile(path):
            size = os.path.getsize(path)
            modified = QDateTime.fromSecsSinceEpoch(int(os.path.getmtime(path))).toString("yyyy-MM-dd HH:mm:ss")
            name = os.path.basename(path)
            self.pcap_metadata_label.setText(f"📄 <b>{name}</b> — {size:,} bytes, modified: {modified}")
        else:
            self.pcap_metadata_label.setText("❌ File not found or invalid.")

    # ----------------------------- Packet View -----------------------------

    def setup_packet_view_tab(self):
        self.packet_view_layout.setSpacing(20)
        self.packet_view_layout.setContentsMargins(20, 20, 20, 20)
        self.packet_tree = QTreeWidget()
        self.packet_tree.setHeaderLabels(["Protocol Layer", "Configuration Details"])
        self.packet_view_layout.addWidget(self.packet_tree)
        self.tabs.currentChanged.connect(self.handle_tab_changed)

    def handle_tab_changed(self, index):
        if self.tabs.tabText(index) == "Packet View":
            self.populate_packet_view(self.get_stream_details())

    def connect_protocol_data_to_packet_view(self):
        # Basic lightweight strategy: refresh tree when the Packet View tab is shown (above),
        # and also on some common edits to keep it reasonably live.
        for w in [
            self.stream_name, self.details_field, self.frame_min, self.frame_max, self.frame_size,
            self.source_field, self.destination_field, self.ttl_field, self.identification_field,
            self.ipv6_source_field, self.ipv6_destination_field, self.ipv6_hop_limit_field,
            self.source_port_field, self.destination_port_field, self.udp_source_port_field, self.udp_destination_port_field
        ]:
            try:
                w.textChanged.connect(lambda *_: self._refresh_packet_view_if_visible())
            except Exception:
                pass
        for w in [
            self.l1_none, self.l1_mac, self.l1_raw,
            self.vlan_untagged, self.vlan_tagged, self.vlan_stacked,
            self.l2_none, self.l2_ethernet, self.l2_mpls,
            self.l3_none, self.l3_arp, self.l3_ipv4, self.l3_ipv6,
            self.l4_none_1, self.l4_none_2, self.l4_icmp, self.l4_igmp, self.l4_tcp, self.l4_udp, self.l4_rocev2, self.l4_uec,
            self.payload_none, self.payload_pattern, self.payload_from_file,
        ]:
            try:
                w.toggled.connect(lambda *_: self._refresh_packet_view_if_visible())
            except Exception:
                pass

    def _refresh_packet_view_if_visible(self):
        if self.tabs.tabText(self.tabs.currentIndex()) == "Packet View":
            self.populate_packet_view(self.get_stream_details())

    # ----------------------------- Populate / Collect -----------------------------

    def _resolve_dpdk_enable(self, stream_data: dict) -> bool:
        """Return True if any legacy or new field says 'use DPDK'."""

        def truthy(v):
            s = str(v).strip().lower()
            return s in ("1", "true", "yes", "on", "dpdk")

        return any((
            truthy(stream_data.get("dpdk_enable", False)),
            truthy(stream_data.get("engine", "")),  # "dpdk" supported
            truthy(stream_data.get("protocol_selection", {}).get("dpdk_enable", False)),
            truthy(stream_data.get("variable_fields", {}).get("dpdk_enable", False)),
        ))

    def populate_stream_fields(self, stream_data=None):
        stream_data = stream_data or {}
        # Basics
        self.stream_name.setText(stream_data.get("name", ""))
        self.enabled_checkbox.setChecked(stream_data.get("enabled", False))
        self.details_field.setText(stream_data.get("details", ""))
        self.flow_tracking_checkbox.setChecked(stream_data.get("flow_tracking_enabled", False))

        # restore DPDK toggle from any supported location
        if hasattr(self, "dpdk_enable_checkbox"):
            self.dpdk_enable_checkbox.setChecked(self._resolve_dpdk_enable(stream_data))
        if hasattr(self, "dpdk_multi_instance_checkbox"):
            self.dpdk_multi_instance_checkbox.setChecked(
                bool(stream_data.get("dpdk_multi_instance", False))
            )


        # Frame Length
        self.frame_type.setCurrentText(stream_data.get("frame_type", "Fixed"))
        self.frame_min.setText(stream_data.get("frame_min", "64"))
        self.frame_max.setText(stream_data.get("frame_max", "1518"))
        self.frame_size.setText(stream_data.get("frame_size", "64"))

        # L1/L2/L3/L4/Payload
        l1 = stream_data.get("L1", "None")
        self.l1_none.setChecked(l1 == "None")
        self.l1_mac.setChecked(l1 == "MAC")
        self.l1_raw.setChecked(l1 == "RAW")

        vlan_sel = stream_data.get("VLAN", "Untagged")
        self.vlan_untagged.setChecked(vlan_sel == "Untagged")
        self.vlan_tagged.setChecked(vlan_sel == "Tagged")
        self.vlan_stacked.setChecked(vlan_sel == "Stacked")

        l2 = stream_data.get("L2", "None")
        self.l2_none.setChecked(l2 == "None")
        self.l2_ethernet.setChecked(l2 == "Ethernet II")
        self.l2_mpls.setChecked(l2 == "MPLS")

        l3 = stream_data.get("L3", "None")
        self.l3_none.setChecked(l3 == "None")
        self.l3_arp.setChecked(l3 == "ARP")
        self.l3_ipv4.setChecked(l3 == "IPv4")
        self.l3_ipv6.setChecked(l3 == "IPv6")

        l4 = stream_data.get("L4", "None")
        # Update both L4 groups
        self.l4_none_1.setChecked(l4 == "None")
        self.l4_none_2.setChecked(l4 == "None")
        self.l4_icmp.setChecked(l4 == "ICMP")
        self.l4_igmp.setChecked(l4 == "IGMP")
        self.l4_tcp.setChecked(l4 == "TCP")
        self.l4_udp.setChecked(l4 == "UDP")
        self.l4_rocev2.setChecked(l4 == "RoCEv2")
        self.l4_uec.setChecked(l4 == "UEC")


        # MAC detailed
        mac_data = stream_data.get("protocol_data", {}).get("mac", {})
        try:
            # Destination
            self.mac_destination_mode.setCurrentText(mac_data.get("mac_destination_mode", "Fixed"))
            self.mac_destination_address.setText(mac_data.get("mac_destination_address", "00:00:00:00:00:00"))
            self.mac_destination_count.setText(mac_data.get("mac_destination_count", "16"))
            self.mac_destination_step.setText(mac_data.get("mac_destination_step", "1"))
            # enable/disable count/step per mode
            self.toggle_mac_fields(self.mac_destination_mode.currentText(),
                                   self.mac_destination_count, self.mac_destination_step)

            # Source
            self.mac_source_mode.setCurrentText(mac_data.get("mac_source_mode", "Fixed"))
            self.mac_source_address.setText(mac_data.get("mac_source_address", "00:00:00:00:00:00"))
            self.mac_source_count.setText(mac_data.get("mac_source_count", "16"))
            self.mac_source_step.setText(mac_data.get("mac_source_step", "1"))
            # enable/disable count/step per mode
            self.toggle_mac_fields(self.mac_source_mode.currentText(),
                                   self.mac_source_count, self.mac_source_step)
        except Exception as e:
            logger.warning("populate_stream_fields: failed to load MAC section: %s", e)

        # ARP restore
        arp = (stream_data.get("protocol_data", {}) or {}).get("arp", {})
        self.arp_operation.setCurrentText(arp.get("arp_operation", "Request"))
        self.arp_sender_mac.setText(arp.get("arp_sender_mac", "00:11:22:33:44:55"))
        self.arp_sender_ip.setText(arp.get("arp_sender_ip", "0.0.0.0"))
        self.arp_target_mac.setText(arp.get("arp_target_mac", "ff:ff:ff:ff:ff:ff"))
        self.arp_target_ip.setText(arp.get("arp_target_ip", "0.0.0.0"))
        # VLAN detailed
        vlan_data = stream_data.get("protocol_data", {}).get("vlan", {})
        self.vlan_increment_checkbox.setChecked(vlan_data.get("vlan_increment", False))
        self.vlan_increment_value.setText(vlan_data.get("vlan_increment_value", "1"))
        self.vlan_increment_count.setText(vlan_data.get("vlan_increment_count", "1"))
        self.vlan_increment_value.setEnabled(vlan_data.get("vlan_increment", False))
        self.vlan_increment_count.setEnabled(vlan_data.get("vlan_increment", False))
        self.priority_field.setCurrentText(vlan_data.get("vlan_priority", "0"))
        self.cfi_dei_field.setCurrentText(vlan_data.get("vlan_cfi_dei", "0"))
        self.vlan_id_field.setText(vlan_data.get("vlan_id", "1"))
        self.tpid_field.setText(vlan_data.get("vlan_tpid", "81 00"))
        self.override_tpid_checkbox.setChecked(stream_data.get("override_settings", {}).get("override_vlan_tpid", False))

        # MPLS
        mpls_data = stream_data.get("protocol_data", {}).get("mpls", {})
        self.mpls_label_field.setText(mpls_data.get("mpls_label", "16"))
        self.mpls_ttl_field.setText(mpls_data.get("mpls_ttl", "64"))
        self.mpls_experimental_field.setText(mpls_data.get("mpls_experimental", "0"))

        # TCP
        tcp_data = stream_data.get("protocol_data", {}).get("tcp", {})
        ov = stream_data.get("override_settings", {})
        self.override_source_port_checkbox.setChecked(ov.get("override_source_tcp_port", False))
        self.source_port_field.setText(tcp_data.get("tcp_source_port", "0"))
        self.source_port_field.setEnabled(self.override_source_port_checkbox.isChecked())

        self.increment_tcp_source_checkbox.setChecked(tcp_data.get("tcp_increment_source_port", False))
        self.tcp_source_increment_step.setText(tcp_data.get("tcp_source_port_step", "1"))
        self.tcp_source_increment_count.setText(tcp_data.get("tcp_source_port_count", "1"))
        self.tcp_source_increment_step.setEnabled(self.increment_tcp_source_checkbox.isChecked())
        self.tcp_source_increment_count.setEnabled(self.increment_tcp_source_checkbox.isChecked())

        self.override_destination_port_checkbox.setChecked(ov.get("override_destination_tcp_port", False))
        self.destination_port_field.setText(tcp_data.get("tcp_destination_port", "0"))
        self.destination_port_field.setEnabled(self.override_destination_port_checkbox.isChecked())

        self.increment_tcp_destination_checkbox.setChecked(tcp_data.get("tcp_increment_destination_port", False))
        self.tcp_destination_increment_step.setText(tcp_data.get("tcp_destination_port_step", "1"))
        self.tcp_destination_increment_count.setText(tcp_data.get("tcp_destination_port_count", "1"))
        self.tcp_destination_increment_step.setEnabled(self.increment_tcp_destination_checkbox.isChecked())
        self.tcp_destination_increment_count.setEnabled(self.increment_tcp_destination_checkbox.isChecked())

        self.override_checksum_checkbox.setChecked(ov.get("override_checksum", False))
        self.tcp_checksum_field.setText(tcp_data.get("tcp_checksum", "B3 E7"))
        self.tcp_checksum_field.setEnabled(self.override_checksum_checkbox.isChecked())

        flags = [f.strip().upper() for f in tcp_data.get("tcp_flags", "").split(",")] if tcp_data.get("tcp_flags") else []
        self.flag_urg.setChecked("URG" in flags)
        self.flag_ack.setChecked("ACK" in flags)
        self.flag_psh.setChecked("PSH" in flags)
        self.flag_rst.setChecked("RST" in flags)
        self.flag_syn.setChecked("SYN" in flags)
        self.flag_fin.setChecked("FIN" in flags)

        # UDP
        udp = stream_data.get("protocol_data", {}).get("udp", {})
        self.override_udp_source_port_checkbox.setChecked(ov.get("override_source_udp_port", False))
        self.udp_source_port_field.setText(udp.get("udp_source_port", "0"))
        self.udp_source_port_field.setEnabled(self.override_udp_source_port_checkbox.isChecked())

        self.udp_increment_source_checkbox.setChecked(udp.get("udp_increment_source_port", False))
        self.udp_source_increment_step.setText(udp.get("udp_source_port_step", "1"))
        self.udp_source_increment_count.setText(udp.get("udp_source_port_count", "1"))
        self.udp_source_increment_step.setEnabled(self.udp_increment_source_checkbox.isChecked())
        self.udp_source_increment_count.setEnabled(self.udp_increment_source_checkbox.isChecked())

        self.override_udp_destination_port_checkbox.setChecked(ov.get("override_destination_udp_port", False))
        self.udp_destination_port_field.setText(udp.get("udp_destination_port", "0"))
        self.udp_destination_port_field.setEnabled(self.override_udp_destination_port_checkbox.isChecked())

        self.udp_increment_destination_checkbox.setChecked(udp.get("udp_increment_destination_port", False))
        self.udp_destination_increment_step.setText(udp.get("udp_destination_port_step", "1"))
        self.udp_destination_increment_count.setText(udp.get("udp_destination_port_count", "1"))
        self.udp_destination_increment_step.setEnabled(self.udp_increment_destination_checkbox.isChecked())
        self.udp_destination_increment_count.setEnabled(self.udp_increment_destination_checkbox.isChecked())

        self.override_udp_checksum_checkbox.setChecked(ov.get("override_udp_checksum", False))
        self.udp_checksum_field.setText(udp.get("udp_checksum", ""))
        self.udp_checksum_field.setEnabled(self.override_udp_checksum_checkbox.isChecked())

        self.udp_preset_combo.setCurrentText(udp.get("udp_preset", "Custom"))
        self.udp_bootp_enable_checkbox.setChecked(udp.get("udp_bootp_enabled", False))
        self.bootp_msg_type.setCurrentText(udp.get("bootp_msg_type", "DHCPDISCOVER"))
        self.bootp_xid.setText(udp.get("bootp_xid", "0x12345678"))
        self.bootp_client_mac.setText(udp.get("bootp_client_mac", "00:11:22:33:44:55"))
        self.bootp_flags.setText(udp.get("bootp_flags", "0x0000"))
        self.bootp_addrs["ciaddr"].setText(udp.get("bootp_ciaddr", "0.0.0.0"))
        self.bootp_addrs["yiaddr"].setText(udp.get("bootp_yiaddr", "0.0.0.0"))
        self.bootp_addrs["siaddr"].setText(udp.get("bootp_siaddr", "0.0.0.0"))
        self.bootp_addrs["giaddr"].setText(udp.get("bootp_giaddr", "0.0.0.0"))
        self.bootp_hostname.setText(udp.get("bootp_hostname", ""))
        self.bootp_prl.setText(udp.get("bootp_prl", "1,3,6,15,28,51,58,59"))

        # RoCEv2
        rocev2 = stream_data.get("protocol_data", {}).get("rocev2", {})
        self.rocev2_traffic_class.setCurrentText(rocev2.get("rocev2_traffic_class", "0"))
        self.rocev2_flow_label.setText(rocev2.get("rocev2_flow_label", "000000"))
        self.rocev2_source_gid.setText(rocev2.get("rocev2_source_gid", "0:0:0:0:0:ffff:192.168.0.2"))
        self.rocev2_destination_gid.setText(rocev2.get("rocev2_destination_gid", "0:0:0:0:0:ffff:192.168.0.3"))
        self.rocev2_increment_source_gid.setChecked(rocev2.get("rocev2_increment_source_gid", False))
        self.rocev2_source_gid_step.setText(rocev2.get("rocev2_source_gid_step", "1"))
        self.rocev2_increment_destination_gid.setChecked(rocev2.get("rocev2_increment_destination_gid", False))
        self.rocev2_destination_gid_step.setText(rocev2.get("rocev2_destination_gid_step", "1"))
        self.rocev2_source_qp.setText(rocev2.get("rocev2_source_qp", "0"))
        self.rocev2_destination_qp.setText(rocev2.get("rocev2_destination_qp", "0"))
        self.rocev2_opcode.setCurrentText(rocev2.get("rocev2_opcode", "SendOnly"))
        self.rocev2_solicited_event.setChecked(rocev2.get("rocev2_solicited_event", False))
        self.rocev2_migration_req.setChecked(rocev2.get("rocev2_migration_req", False))
        self.rocev2_qp_count.setText(rocev2.get("rocev2_qp_count", "1"))
        self.rocev2_qp_increment.setChecked(rocev2.get("rocev2_qp_increment", False))
        self.rocev2_qp_increment_step.setText(rocev2.get("rocev2_qp_increment_step", "1"))
        self.rocev2_gid_source_mode.setCurrentText(rocev2.get("rocev2_gid_source_mode", "Fixed"))
        self.rocev2_gid_source_step.setText(rocev2.get("rocev2_gid_source_step", "1"))
        self.rocev2_gid_source_count.setText(rocev2.get("rocev2_gid_source_count", "1"))
        self.rocev2_gid_destination_mode.setCurrentText(rocev2.get("rocev2_gid_destination_mode", "Fixed"))
        self.rocev2_gid_destination_step.setText(rocev2.get("rocev2_gid_destination_step", "1"))
        self.rocev2_gid_destination_count.setText(rocev2.get("rocev2_gid_destination_count", "1"))
        self.rocev2_send_cnp.setChecked(rocev2.get("send_cnp", False))

        # UEC
        uec = stream_data.get("protocol_data", {}).get("uec", {})
        self.uec_qp_start_field.setText(uec.get("qp_start", "1000"))
        self.uec_qp_end_field.setText(uec.get("qp_end", "1010"))
        self.uec_pasid_start_field.setText(uec.get("pasid_start", "5000"))
        self.uec_pasid_end_field.setText(uec.get("pasid_end", "5010"))
        self.uec_ecn_combo_box.setCurrentText(uec.get("ecn", "Not-ECT"))
        self.uec_flow_label_field.setText(uec.get("flow_label", "0"))
        self.uec_enable_spray_checkbox.setChecked(uec.get("enable_spray", False))
        self.uec_enable_rocev2_checkbox.setChecked(uec.get("enable_rocev2", False))

        # Payload
        payload_value = stream_data.get("Payload", "None")
        self.payload_none.setChecked(payload_value == "None")
        self.payload_pattern.setChecked(payload_value == "Pattern")
        # Map "Hex Dump" to "From File" for backward compatibility
        self.payload_from_file.setChecked(payload_value == "Hex Dump" or payload_value == "From File")
        self.payload_random.setChecked(payload_value == "Random")

        # IPv4 detailed
        ipv4_data = stream_data.get("protocol_data", {}).get("ipv4", {})
        self.source_field.setText(ipv4_data.get("ipv4_source", "10.0.0.1"))
        self.destination_field.setText(ipv4_data.get("ipv4_destination", "11.0.0.2"))
        self.ttl_field.setText(ipv4_data.get("ipv4_ttl", "64"))
        self.identification_field.setText(ipv4_data.get("ipv4_identification", "0000"))
        self.df_checkbox.setChecked(ipv4_data.get("ipv4_df", False))
        self.mf_checkbox.setChecked(ipv4_data.get("ipv4_mf", False))
        self.fragment_offset_field.setText(ipv4_data.get("ipv4_fragment_offset", "0"))

        src_mode = ipv4_data.get("ipv4_source_mode", "Fixed")
        self.source_mode_dropdown.setCurrentText(src_mode)
        self.source_increment_step.setEnabled(src_mode == "Increment")
        self.source_increment_count.setEnabled(src_mode == "Increment")
        self.source_increment_step.setText(ipv4_data.get("ipv4_source_increment_step", "1"))
        self.source_increment_count.setText(ipv4_data.get("ipv4_source_increment_count", "1"))

        dst_mode = ipv4_data.get("ipv4_destination_mode", "Fixed")
        self.destination_mode_dropdown.setCurrentText(dst_mode)
        self.destination_increment_step.setEnabled(dst_mode == "Increment")
        self.destination_increment_count.setEnabled(dst_mode == "Increment")
        self.destination_increment_step.setText(ipv4_data.get("ipv4_destination_increment_step", "1"))
        self.destination_increment_count.setText(ipv4_data.get("ipv4_destination_increment_count", "1"))

        tos_dscp_mode = ipv4_data.get("tos_dscp_mode", "TOS")
        self.tos_dscp_custom_mode.setCurrentText(tos_dscp_mode)
        if tos_dscp_mode == "TOS":
            self.tos_dropdown.setCurrentText(ipv4_data.get("ipv4_tos", "Routine"))
        elif tos_dscp_mode == "DSCP":
            self.dscp_dropdown.setCurrentText(ipv4_data.get("ipv4_dscp", "cs0"))
            self.ecn_dropdown.setCurrentText(ipv4_data.get("ipv4_ecn", "Not-ECT"))
        elif tos_dscp_mode == "Custom":
            self.custom_tos_field.setText(ipv4_data.get("ipv4_custom_tos", ""))
        self.ecn_dropdown.setCurrentText(ipv4_data.get("ipv4_ecn", "Not-ECT"))

        # IPv6 detailed
        ipv6_data = stream_data.get("protocol_data", {}).get("ipv6", {})
        self.ipv6_source_field.setText(ipv6_data.get("ipv6_source", "2001:db8::1"))
        s_mode = ipv6_data.get("ipv6_source_mode", "Fixed")
        self.ipv6_source_mode_dropdown.setCurrentText(s_mode)
        self.ipv6_source_increment_step.setEnabled(s_mode == "Increment")
        self.ipv6_source_increment_count.setEnabled(s_mode == "Increment")
        self.ipv6_source_increment_step.setText(ipv6_data.get("ipv6_source_increment_step", "1"))
        self.ipv6_source_increment_count.setText(ipv6_data.get("ipv6_source_increment_count", "1"))
        self.ipv6_destination_field.setText(ipv6_data.get("ipv6_destination", "2001:db8::2"))
        d_mode = ipv6_data.get("ipv6_destination_mode", "Fixed")
        self.ipv6_destination_mode_dropdown.setCurrentText(d_mode)
        self.ipv6_destination_increment_step.setEnabled(d_mode == "Increment")
        self.ipv6_destination_increment_count.setEnabled(d_mode == "Increment")
        self.ipv6_destination_increment_step.setText(ipv6_data.get("ipv6_destination_increment_step", "1"))
        self.ipv6_destination_increment_count.setText(ipv6_data.get("ipv6_destination_increment_count", "1"))
        self.ipv6_traffic_class_field.setText(ipv6_data.get("ipv6_traffic_class", "0"))
        self.ipv6_flow_label_field.setText(ipv6_data.get("ipv6_flow_label", "0"))
        self.ipv6_hop_limit_field.setText(ipv6_data.get("ipv6_hop_limit", "64"))

        # Rate/Duration
        self.rate_type_dropdown.setCurrentText(stream_data.get("stream_rate_type", "Packets Per Second (PPS)"))
        self.stream_pps_rate.setText(stream_data.get("stream_pps_rate", "1000"))
        self.stream_bit_rate.setText(stream_data.get("stream_bit_rate", "100"))
        self.stream_load_percentage.setText(stream_data.get("stream_load_percentage", "50"))

        duration_mode = stream_data.get("stream_duration_mode", "Continuous")
        self.duration_mode_dropdown.setCurrentText(duration_mode)
        if duration_mode == "Seconds":
            self.stream_duration_field.setText(stream_data.get("stream_duration_seconds", "10"))
        else:
            self.stream_duration_field.clear()

        # RX port
        rx_port_value = stream_data.get("rx_port", "Same as TX Port")
        idx = self.rx_port_dropdown.findText(rx_port_value)
        if idx != -1:
            self.rx_port_dropdown.setCurrentIndex(idx)
        else:
            self.rx_port_dropdown.setCurrentText("Same as TX Port")

        QTimer.singleShot(0, self.refresh_l4_sections)

    # ----------------------------- Build stream dict -----------------------------

    def _selected_l1(self):
        if self.l1_mac.isChecked(): return "MAC"
        if self.l1_raw.isChecked(): return "RAW"
        return "None"

    def _selected_vlan(self):
        if self.vlan_tagged.isChecked(): return "Tagged"
        if self.vlan_stacked.isChecked(): return "Stacked"
        return "Untagged"

    def _selected_l2(self):
        if self.l2_ethernet.isChecked(): return "Ethernet II"
        if self.l2_mpls.isChecked(): return "MPLS"
        return "None"

    def _selected_l3(self):
        if self.l3_ipv4.isChecked(): return "IPv4"
        if self.l3_ipv6.isChecked(): return "IPv6"
        if self.l3_arp.isChecked():  return "ARP"
        return "None"

    def _selected_l4(self):
        # Check both L4 groups
        if self.l4_tcp.isChecked():    return "TCP"
        if self.l4_udp.isChecked():    return "UDP"
        if self.l4_rocev2.isChecked(): return "RoCEv2"
        if self.l4_uec.isChecked():    return "UEC"
        if self.l4_icmp.isChecked():   return "ICMP"
        if self.l4_igmp.isChecked():   return "IGMP"
        return "None"

    def _selected_payload(self):
        if self.payload_pattern.isChecked(): return "Pattern"
        if self.payload_from_file.isChecked(): return "From File"  # Map to "Hex Dump" for backward compatibility if needed
        if self.payload_random.isChecked(): return "Random"
        return "None"

    def _collect_vlan_pd(self):
        return {
            "vlan_id": self.vlan_id_field.text().strip() or "1",
            "vlan_priority": self.priority_field.currentText(),
            "vlan_cfi_dei": self.cfi_dei_field.currentText(),
            "vlan_tpid": self.tpid_field.text().strip() or "81 00",
            "vlan_increment": self.vlan_increment_checkbox.isChecked(),
            "vlan_increment_value": self.vlan_increment_value.text().strip() or "1",
            "vlan_increment_count": self.vlan_increment_count.text().strip() or "1",
        }

    def _collect_ipv4_pd(self):
        return {
            "ipv4_source": self.source_field.text().strip() or "0.0.0.0",
            "ipv4_source_mode": self.source_mode_dropdown.currentText(),
            "ipv4_source_increment_step": self.source_increment_step.text().strip() or "1",
            "ipv4_source_increment_count": self.source_increment_count.text().strip() or "1",
            "ipv4_destination": self.destination_field.text().strip() or "0.0.0.0",
            "ipv4_destination_mode": self.destination_mode_dropdown.currentText(),
            "ipv4_destination_increment_step": self.destination_increment_step.text().strip() or "1",
            "ipv4_destination_increment_count": self.destination_increment_count.text().strip() or "1",
            "ipv4_ttl": self.ttl_field.text().strip() or "64",
            "ipv4_df": self.df_checkbox.isChecked(),
            "ipv4_mf": self.mf_checkbox.isChecked(),
            "ipv4_fragment_offset": self.fragment_offset_field.text().strip() or "0",
            "ipv4_identification": self.identification_field.text().strip() or "0000",
            "tos_dscp_mode": self.tos_dscp_custom_mode.currentText(),
            "ipv4_tos": self.tos_dropdown.currentText() if self.tos_dscp_custom_mode.currentText() == "TOS" else "",
            "ipv4_dscp": self.dscp_dropdown.currentText() if self.tos_dscp_custom_mode.currentText() == "DSCP" else "",
            "ipv4_custom_tos": self.custom_tos_field.text().strip() if self.tos_dscp_custom_mode.currentText() == "Custom" else "",
            "ipv4_ecn": self.ecn_dropdown.currentText(),
        }

    def _collect_ipv6_pd(self):
        return {
            "ipv6_source": self.ipv6_source_field.text().strip() or "2001:db8::1",
            "ipv6_source_mode": self.ipv6_source_mode_dropdown.currentText(),
            "ipv6_source_increment_step": self.ipv6_source_increment_step.text().strip() or "1",
            "ipv6_source_increment_count": self.ipv6_source_increment_count.text().strip() or "1",
            "ipv6_destination": self.ipv6_destination_field.text().strip() or "2001:db8::2",
            "ipv6_destination_mode": self.ipv6_destination_mode_dropdown.currentText(),
            "ipv6_destination_increment_step": self.ipv6_destination_increment_step.text().strip() or "1",
            "ipv6_destination_increment_count": self.ipv6_destination_increment_count.text().strip() or "1",
            "ipv6_traffic_class": self.ipv6_traffic_class_field.text().strip() or "0",
            "ipv6_flow_label": self.ipv6_flow_label_field.text().strip() or "0",
            "ipv6_hop_limit": self.ipv6_hop_limit_field.text().strip() or "64",
        }

    def _collect_arp_pd(self):
        """Collect ARP fields."""
        return {
            "arp_operation": self.arp_operation.currentText() if hasattr(self, "arp_operation") else "Request",
            "arp_sender_mac": self.arp_sender_mac.text().strip() if hasattr(self, "arp_sender_mac") else "",
            "arp_sender_ip": self.arp_sender_ip.text().strip() if hasattr(self, "arp_sender_ip") else "0.0.0.0",
            "arp_target_mac": self.arp_target_mac.text().strip() if hasattr(self, "arp_target_mac") else "",
            "arp_target_ip": self.arp_target_ip.text().strip() if hasattr(self, "arp_target_ip") else "0.0.0.0",
        }
    def _collect_tcp_pd(self):
        flags = []
        if self.flag_urg.isChecked(): flags.append("URG")
        if self.flag_ack.isChecked(): flags.append("ACK")
        if self.flag_psh.isChecked(): flags.append("PSH")
        if self.flag_rst.isChecked(): flags.append("RST")
        if self.flag_syn.isChecked(): flags.append("SYN")
        if self.flag_fin.isChecked(): flags.append("FIN")
        return {
            "tcp_source_port": self.source_port_field.text().strip() or "0",
            "tcp_source_port_step": self.tcp_source_increment_step.text().strip() or "1",
            "tcp_source_port_count": self.tcp_source_increment_count.text().strip() or "1",
            "tcp_increment_source_port": self.increment_tcp_source_checkbox.isChecked(),
            "tcp_destination_port": self.destination_port_field.text().strip() or "0",
            "tcp_destination_port_step": self.tcp_destination_increment_step.text().strip() or "1",
            "tcp_destination_port_count": self.tcp_destination_increment_count.text().strip() or "1",
            "tcp_increment_destination_port": self.increment_tcp_destination_checkbox.isChecked(),
            "tcp_sequence_number": self.sequence_number_field.text().strip() or "0",
            "tcp_acknowledgement_number": self.acknowledgement_number_field.text().strip() or "0",
            "tcp_window": self.window_field.text().strip() or "1024",
            "tcp_checksum": self.tcp_checksum_field.text().strip(),
            "tcp_flags": ",".join(flags),
        }

    def _collect_udp_pd(self):
        return {
            "udp_source_port": self.udp_source_port_field.text().strip() or "0",
            "udp_source_port_step": self.udp_source_increment_step.text().strip() or "1",
            "udp_source_port_count": self.udp_source_increment_count.text().strip() or "1",
            "udp_increment_source_port": self.udp_increment_source_checkbox.isChecked(),
            "udp_destination_port": self.udp_destination_port_field.text().strip() or "0",
            "udp_destination_port_step": self.udp_destination_increment_step.text().strip() or "1",
            "udp_destination_port_count": self.udp_destination_increment_count.text().strip() or "1",
            "udp_increment_destination_port": self.udp_increment_destination_checkbox.isChecked(),
            "udp_checksum": self.udp_checksum_field.text().strip(),
            "udp_preset": self.udp_preset_combo.currentText(),
            "udp_bootp_enabled": self.udp_bootp_enable_checkbox.isChecked(),
            "bootp_msg_type": self.bootp_msg_type.currentText(),
            "bootp_xid": self.bootp_xid.text().strip(),
            "bootp_client_mac": self.bootp_client_mac.text().strip(),
            "bootp_flags": self.bootp_flags.text().strip(),
            "bootp_ciaddr": self.bootp_addrs["ciaddr"].text().strip(),
            "bootp_yiaddr": self.bootp_addrs["yiaddr"].text().strip(),
            "bootp_siaddr": self.bootp_addrs["siaddr"].text().strip(),
            "bootp_giaddr": self.bootp_addrs["giaddr"].text().strip(),
            "bootp_hostname": self.bootp_hostname.text().strip(),
            "bootp_prl": self.bootp_prl.text().strip(),
        }

    def _collect_rocev2_pd(self):
        return {
            "rocev2_traffic_class": self.rocev2_traffic_class.currentText(),
            "rocev2_flow_label": self.rocev2_flow_label.text().strip() or "000000",
            "rocev2_source_gid": self.rocev2_source_gid.text().strip(),
            "rocev2_destination_gid": self.rocev2_destination_gid.text().strip(),
            "rocev2_increment_source_gid": self.rocev2_increment_source_gid.isChecked(),
            "rocev2_source_gid_step": self.rocev2_source_gid_step.text().strip() or "1",
            "rocev2_increment_destination_gid": self.rocev2_increment_destination_gid.isChecked(),
            "rocev2_destination_gid_step": self.rocev2_destination_gid_step.text().strip() or "1",
            "rocev2_source_qp": self.rocev2_source_qp.text().strip() or "0",
            "rocev2_destination_qp": self.rocev2_destination_qp.text().strip() or "0",
            "rocev2_opcode": self.rocev2_opcode.currentText(),
            "rocev2_solicited_event": self.rocev2_solicited_event.isChecked(),
            "rocev2_migration_req": self.rocev2_migration_req.isChecked(),
            "rocev2_qp_count": self.rocev2_qp_count.text().strip() or "1",
            "rocev2_qp_increment": self.rocev2_qp_increment.isChecked(),
            "rocev2_qp_increment_step": self.rocev2_qp_increment_step.text().strip() or "1",
            "rocev2_gid_source_mode": self.rocev2_gid_source_mode.currentText(),
            "rocev2_gid_source_step": self.rocev2_gid_source_step.text().strip() or "1",
            "rocev2_gid_source_count": self.rocev2_gid_source_count.text().strip() or "1",
            "rocev2_gid_destination_mode": self.rocev2_gid_destination_mode.currentText(),
            "rocev2_gid_destination_step": self.rocev2_gid_destination_step.text().strip() or "1",
            "rocev2_gid_destination_count": self.rocev2_gid_destination_count.text().strip() or "1",
            "send_cnp": self.rocev2_send_cnp.isChecked(),
        }

    def _collect_uec_pd(self):
        return {
            "qp_start": self.uec_qp_start_field.text().strip() or "1000",
            "qp_end": self.uec_qp_end_field.text().strip() or "1010",
            "pasid_start": self.uec_pasid_start_field.text().strip() or "5000",
            "pasid_end": self.uec_pasid_end_field.text().strip() or "5010",
            "ecn": self.uec_ecn_combo_box.currentText(),
            "flow_label": self.uec_flow_label_field.text().strip() or "0",
            "enable_spray": self.uec_enable_spray_checkbox.isChecked(),
            "enable_rocev2": self.uec_enable_rocev2_checkbox.isChecked(),
        }

    def _collect_mac_pd(self):
        return {
            "mac_destination_mode": self.mac_destination_mode.currentText(),
            "mac_destination_address": self.mac_destination_address.text().strip(),
            "mac_destination_count": self.mac_destination_count.text().strip() or "16",
            "mac_destination_step": self.mac_destination_step.text().strip() or "1",
            "mac_source_mode": self.mac_source_mode.currentText(),
            "mac_source_address": self.mac_source_address.text().strip(),
            "mac_source_count": self.mac_source_count.text().strip() or "16",
            "mac_source_step": self.mac_source_step.text().strip() or "1",
        }

    def get_stream_details(self):
        """Collect all dialog fields into a single stream_details dict."""
        # ---------- basics ----------
        name = (self.stream_name.text().strip() if hasattr(self, "stream_name") else "") or "Stream"
        enabled = self.enabled_checkbox.isChecked() if hasattr(self, "enabled_checkbox") else False
        details = self.details_field.text().strip() if hasattr(self, "details_field") else ""
        rx_pick = self.rx_port_dropdown.currentText().strip() if hasattr(self,
                                                                         "rx_port_dropdown") else "Same as TX Port"
        flow_tracking = self.flow_tracking_checkbox.isChecked() if hasattr(self, "flow_tracking_checkbox") else False

        # frame
        frame_type = self.frame_type.currentText() if hasattr(self, "frame_type") else "Fixed"
        frame_min = self.frame_min.text().strip() if hasattr(self, "frame_min") else "64"
        frame_max = self.frame_max.text().strip() if hasattr(self, "frame_max") else "1518"
        frame_size = self.frame_size.text().strip() if hasattr(self, "frame_size") else "64"

        # helpers
        def chosen(pairs):
            for label, w in pairs:
                if hasattr(self, w) and getattr(self, w).isChecked():
                    return label
            return pairs[0][0]

        L1 = chosen([("None", "l1_none"), ("MAC", "l1_mac"), ("RAW", "l1_raw")])
        VLAN_sel = chosen([("Untagged", "vlan_untagged"), ("Tagged", "vlan_tagged"), ("Stacked", "vlan_stacked")])
        L2 = chosen([("None", "l2_none"), ("Ethernet II", "l2_ethernet"), ("MPLS", "l2_mpls")])
        L3 = chosen([("None", "l3_none"), ("ARP", "l3_arp"), ("IPv4", "l3_ipv4"), ("IPv6", "l3_ipv6")])
        # Check both L4 groups
        L4 = None
        for name, attr in [("ICMP", "l4_icmp"), ("IGMP", "l4_igmp"), ("TCP", "l4_tcp"), ("UDP", "l4_udp")]:
            if getattr(self, attr).isChecked():
                L4 = name
                break
        if not L4:
            for name, attr in [("RoCEv2", "l4_rocev2"), ("UEC", "l4_uec")]:
                if getattr(self, attr).isChecked():
                    L4 = name
                    break
        if not L4:
            L4 = "None"
        Payload = chosen([("None", "payload_none"), ("Pattern", "payload_pattern"), ("From File", "payload_from_file"), ("Random", "payload_random")])

        # ---------- PCAP ----------
        pcap_stream = {
            "pcap_enabled": getattr(self, "enable_pcap_checkbox", None).isChecked() if hasattr(self,
                                                                                               "enable_pcap_checkbox") else False,
            "pcap_file_path": getattr(self, "pcap_file_path", None).text().strip() if hasattr(self,
                                                                                              "pcap_file_path") else "",
            "pcap_loop_count": getattr(self, "pcap_loop_count", None).value() if hasattr(self,
                                                                                         "pcap_loop_count") else 1,
            "pcap_rate_mode": getattr(self, "pcap_rate_mode", None).currentText() if hasattr(self,
                                                                                             "pcap_rate_mode") else "Original Timing",
        }

        # ---------- protocol_data (only fill what exists to avoid AttributeError) ----------
        protocol_data = {}

        # MAC
        if hasattr(self, "mac_destination_address"):
            protocol_data["mac"] = self._collect_mac_pd()


        # VLAN
        if hasattr(self, "vlan_id_field"):
            protocol_data["vlan"] = {
                "vlan_id": self.vlan_id_field.text().strip(),
                "vlan_priority": self.priority_field.currentText() if hasattr(self, "priority_field") else "0",
                "vlan_cfi_dei": self.cfi_dei_field.currentText() if hasattr(self, "cfi_dei_field") else "0",
                "vlan_increment": self.vlan_increment_checkbox.isChecked() if hasattr(self,
                                                                                      "vlan_increment_checkbox") else False,
                "vlan_increment_value": self.vlan_increment_value.text().strip() if hasattr(self,
                                                                                            "vlan_increment_value") else "1",
                "vlan_increment_count": self.vlan_increment_count.text().strip() if hasattr(self,
                                                                                            "vlan_increment_count") else "1",
                "vlan_tpid": self.tpid_field.text().strip() if hasattr(self, "tpid_field") else "81 00",
            }

        # MPLS
        if hasattr(self, "mpls_label_field"):
            protocol_data["mpls"] = {
                "mpls_label": self.mpls_label_field.text().strip(),
                "mpls_ttl": self.mpls_ttl_field.text().strip(),
                "mpls_experimental": self.mpls_experimental_field.text().strip(),
            }

        # IPv4
        if hasattr(self, "source_field"):
            protocol_data["ipv4"] = {
                "ipv4_source": self.source_field.text().strip(),
                "ipv4_destination": self.destination_field.text().strip() if hasattr(self,
                                                                                     "destination_field") else "0.0.0.0",
                "ipv4_source_mode": self.source_mode_dropdown.currentText() if hasattr(self,
                                                                                       "source_mode_dropdown") else "Fixed",
                "ipv4_source_increment_step": self.source_increment_step.text().strip() if hasattr(self,
                                                                                                   "source_increment_step") else "1",
                "ipv4_source_increment_count": self.source_increment_count.text().strip() if hasattr(self,
                                                                                                     "source_increment_count") else "1",
                "ipv4_destination_mode": self.destination_mode_dropdown.currentText() if hasattr(self,
                                                                                                 "destination_mode_dropdown") else "Fixed",
                "ipv4_destination_increment_step": self.destination_increment_step.text().strip() if hasattr(self,
                                                                                                             "destination_increment_step") else "1",
                "ipv4_destination_increment_count": self.destination_increment_count.text().strip() if hasattr(self,
                                                                                                               "destination_increment_count") else "1",
                "ipv4_ttl": self.ttl_field.text().strip() if hasattr(self, "ttl_field") else "64",
                "ipv4_df": self.df_checkbox.isChecked() if hasattr(self, "df_checkbox") else False,
                "ipv4_mf": self.mf_checkbox.isChecked() if hasattr(self, "mf_checkbox") else False,
                "ipv4_fragment_offset": self.fragment_offset_field.text().strip() if hasattr(self,
                                                                                             "fragment_offset_field") else "0",
                "ipv4_identification": self.identification_field.text().strip() if hasattr(self,
                                                                                           "identification_field") else "0000",
                "tos_dscp_mode": self.tos_dscp_custom_mode.currentText() if hasattr(self,
                                                                                    "tos_dscp_custom_mode") else "TOS",
                "ipv4_tos": self.tos_dropdown.currentText() if hasattr(self, "tos_dropdown") else "Routine",
                "ipv4_dscp": self.dscp_dropdown.currentText() if hasattr(self, "dscp_dropdown") else "cs0",
                "ipv4_custom_tos": self.custom_tos_field.text().strip() if hasattr(self, "custom_tos_field") else "",
                "ipv4_ecn": self.ecn_dropdown.currentText() if hasattr(self, "ecn_dropdown") else "Not-ECT",
            }

        # IPv6
        if hasattr(self, "ipv6_source_field"):
            protocol_data["ipv6"] = {
                "ipv6_source": self.ipv6_source_field.text().strip(),
                "ipv6_destination": self.ipv6_destination_field.text().strip() if hasattr(self,
                                                                                          "ipv6_destination_field") else "2001:db8::2",
                "ipv6_source_mode": self.ipv6_source_mode_dropdown.currentText() if hasattr(self,
                                                                                            "ipv6_source_mode_dropdown") else "Fixed",
                "ipv6_source_increment_step": self.ipv6_source_increment_step.text().strip() if hasattr(self,
                                                                                                        "ipv6_source_increment_step") else "1",
                "ipv6_source_increment_count": self.ipv6_source_increment_count.text().strip() if hasattr(self,
                                                                                                          "ipv6_source_increment_count") else "1",
                "ipv6_destination_mode": self.ipv6_destination_mode_dropdown.currentText() if hasattr(self,
                                                                                                      "ipv6_destination_mode_dropdown") else "Fixed",
                "ipv6_destination_increment_step": self.ipv6_destination_increment_step.text().strip() if hasattr(self,
                                                                                                                  "ipv6_destination_increment_step") else "1",
                "ipv6_destination_increment_count": self.ipv6_destination_increment_count.text().strip() if hasattr(
                    self, "ipv6_destination_increment_count") else "1",
                "ipv6_traffic_class": self.ipv6_traffic_class_field.text().strip() if hasattr(self,
                                                                                              "ipv6_traffic_class_field") else "0",
                "ipv6_flow_label": self.ipv6_flow_label_field.text().strip() if hasattr(self,
                                                                                        "ipv6_flow_label_field") else "0",
                "ipv6_hop_limit": self.ipv6_hop_limit_field.text().strip() if hasattr(self,
                                                                                      "ipv6_hop_limit_field") else "64",
            }

        # TCP
        if hasattr(self, "source_port_field"):
            flags = []
            for label, attr in [("URG", "flag_urg"), ("ACK", "flag_ack"), ("PSH", "flag_psh"),
                                ("RST", "flag_rst"), ("SYN", "flag_syn"), ("FIN", "flag_fin")]:
                if hasattr(self, attr) and getattr(self, attr).isChecked():
                    flags.append(label)
            protocol_data["tcp"] = {
                "tcp_source_port": self.source_port_field.text().strip(),
                "tcp_destination_port": self.destination_port_field.text().strip() if hasattr(self,
                                                                                              "destination_port_field") else "0",
                "tcp_increment_source_port": self.increment_tcp_source_checkbox.isChecked() if hasattr(self,
                                                                                                       "increment_tcp_source_checkbox") else False,
                "tcp_source_port_step": self.tcp_source_increment_step.text().strip() if hasattr(self,
                                                                                                 "tcp_source_increment_step") else "1",
                "tcp_source_port_count": self.tcp_source_increment_count.text().strip() if hasattr(self,
                                                                                                   "tcp_source_increment_count") else "1",
                "tcp_increment_destination_port": self.increment_tcp_destination_checkbox.isChecked() if hasattr(self,
                                                                                                                 "increment_tcp_destination_checkbox") else False,
                "tcp_destination_port_step": self.tcp_destination_increment_step.text().strip() if hasattr(self,
                                                                                                           "tcp_destination_increment_step") else "1",
                "tcp_destination_port_count": self.tcp_destination_increment_count.text().strip() if hasattr(self,
                                                                                                             "tcp_destination_increment_count") else "1",
                "tcp_sequence_number": self.sequence_number_field.text().strip() if hasattr(self,
                                                                                            "sequence_number_field") else "0",
                "tcp_acknowledgement_number": self.acknowledgement_number_field.text().strip() if hasattr(self,
                                                                                                          "acknowledgement_number_field") else "0",
                "tcp_window": self.window_field.text().strip() if hasattr(self, "window_field") else "1024",
                "tcp_checksum": self.tcp_checksum_field.text().strip() if hasattr(self, "tcp_checksum_field") else "",
                "tcp_flags": ", ".join(flags),
            }

        # UDP
        if hasattr(self, "udp_source_port_field"):
            protocol_data["udp"] = {
                "udp_source_port": self.udp_source_port_field.text().strip(),
                "udp_destination_port": self.udp_destination_port_field.text().strip() if hasattr(self,
                                                                                                  "udp_destination_port_field") else "0",
                "udp_increment_source_port": self.udp_increment_source_checkbox.isChecked() if hasattr(self,
                                                                                                       "udp_increment_source_checkbox") else False,
                "udp_source_port_step": self.udp_source_increment_step.text().strip() if hasattr(self,
                                                                                                 "udp_source_increment_step") else "1",
                "udp_source_port_count": self.udp_source_increment_count.text().strip() if hasattr(self,
                                                                                                   "udp_source_increment_count") else "1",
                "udp_increment_destination_port": self.udp_increment_destination_checkbox.isChecked() if hasattr(self,
                                                                                                                 "udp_increment_destination_checkbox") else False,
                "udp_destination_port_step": self.udp_destination_increment_step.text().strip() if hasattr(self,
                                                                                                           "udp_destination_increment_step") else "1",
                "udp_destination_port_count": self.udp_destination_increment_count.text().strip() if hasattr(self,
                                                                                                             "udp_destination_increment_count") else "1",
                "udp_checksum": self.udp_checksum_field.text().strip() if hasattr(self, "udp_checksum_field") else "",
                "udp_preset": self.udp_preset_combo.currentText() if hasattr(self, "udp_preset_combo") else "Custom",
                "udp_bootp_enabled": self.udp_bootp_enable_checkbox.isChecked() if hasattr(self,
                                                                                           "udp_bootp_enable_checkbox") else False,
                "bootp_msg_type": self.bootp_msg_type.currentText() if hasattr(self,
                                                                               "bootp_msg_type") else "DHCPDISCOVER",
                "bootp_xid": self.bootp_xid.text().strip() if hasattr(self, "bootp_xid") else "",
                "bootp_client_mac": self.bootp_client_mac.text().strip() if hasattr(self, "bootp_client_mac") else "",
                "bootp_flags": self.bootp_flags.text().strip() if hasattr(self, "bootp_flags") else "0x0000",
                "bootp_ciaddr": self.bootp_addrs["ciaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_yiaddr": self.bootp_addrs["yiaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_siaddr": self.bootp_addrs["siaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_giaddr": self.bootp_addrs["giaddr"].text().strip() if hasattr(self,
                                                                                     "bootp_addrs") else "0.0.0.0",
                "bootp_hostname": self.bootp_hostname.text().strip() if hasattr(self, "bootp_hostname") else "",
                "bootp_prl": self.bootp_prl.text().strip() if hasattr(self, "bootp_prl") else "",
            }

        # RoCEv2
        if hasattr(self, "rocev2_traffic_class"):
            protocol_data["rocev2"] = {
                "rocev2_traffic_class": self.rocev2_traffic_class.currentText(),
                "rocev2_flow_label": self.rocev2_flow_label.text().strip(),
                "rocev2_source_gid": self.rocev2_source_gid.text().strip(),
                "rocev2_destination_gid": self.rocev2_destination_gid.text().strip(),
                "rocev2_increment_source_gid": self.rocev2_increment_source_gid.isChecked(),
                "rocev2_source_gid_step": self.rocev2_source_gid_step.text().strip(),
                "rocev2_increment_destination_gid": self.rocev2_increment_destination_gid.isChecked(),
                "rocev2_destination_gid_step": self.rocev2_destination_gid_step.text().strip(),
                "rocev2_source_qp": self.rocev2_source_qp.text().strip(),
                "rocev2_destination_qp": self.rocev2_destination_qp.text().strip(),
                "rocev2_opcode": self.rocev2_opcode.currentText(),
                "rocev2_solicited_event": self.rocev2_solicited_event.isChecked(),
                "rocev2_migration_req": self.rocev2_migration_req.isChecked(),
                "rocev2_qp_count": self.rocev2_qp_count.text().strip(),
                "rocev2_qp_increment": self.rocev2_qp_increment.isChecked(),
                "rocev2_qp_increment_step": self.rocev2_qp_increment_step.text().strip(),
                "rocev2_gid_source_mode": self.rocev2_gid_source_mode.currentText(),
                "rocev2_gid_source_step": self.rocev2_gid_source_step.text().strip(),
                "rocev2_gid_source_count": self.rocev2_gid_source_count.text().strip(),
                "rocev2_gid_destination_mode": self.rocev2_gid_destination_mode.currentText(),
                "rocev2_gid_destination_step": self.rocev2_gid_destination_step.text().strip(),
                "rocev2_gid_destination_count": self.rocev2_gid_destination_count.text().strip(),
                "send_cnp": self.rocev2_send_cnp.isChecked() if hasattr(self, "rocev2_send_cnp") else False,
            }

        # UEC
        if hasattr(self, "uec_qp_start_field"):
            protocol_data["uec"] = {
                "qp_start": self.uec_qp_start_field.text().strip(),
                "qp_end": self.uec_qp_end_field.text().strip(),
                "pasid_start": self.uec_pasid_start_field.text().strip(),
                "pasid_end": self.uec_pasid_end_field.text().strip(),
                "ecn": self.uec_ecn_combo_box.currentText() if hasattr(self, "uec_ecn_combo_box") else "Not-ECT",
                "flow_label": self.uec_flow_label_field.text().strip() if hasattr(self,
                                                                                  "uec_flow_label_field") else "0",
                "enable_spray": self.uec_enable_spray_checkbox.isChecked() if hasattr(self,
                                                                                      "uec_enable_spray_checkbox") else False,
                "enable_rocev2": self.uec_enable_rocev2_checkbox.isChecked() if hasattr(self,
                                                                                        "uec_enable_rocev2_checkbox") else False,
            }
        # ARP
        if hasattr(self, "arp_group"):
            protocol_data["arp"] = self._collect_arp_pd()

        # override flags
        override_settings = {
            "override_source_tcp_port": getattr(self, "override_source_port_checkbox", None).isChecked() if hasattr(
                self, "override_source_port_checkbox") else False,
            "override_destination_tcp_port": getattr(self, "override_destination_port_checkbox",
                                                     None).isChecked() if hasattr(self,
                                                                                  "override_destination_port_checkbox") else False,
            "override_checksum": getattr(self, "override_checksum_checkbox", None).isChecked() if hasattr(self,
                                                                                                          "override_checksum_checkbox") else False,
            "override_source_udp_port": getattr(self, "override_udp_source_port_checkbox", None).isChecked() if hasattr(
                self, "override_udp_source_port_checkbox") else False,
            "override_destination_udp_port": getattr(self, "override_udp_destination_port_checkbox",
                                                     None).isChecked() if hasattr(self,
                                                                                  "override_udp_destination_port_checkbox") else False,
            "override_udp_checksum": getattr(self, "override_udp_checksum_checkbox", None).isChecked() if hasattr(self,
                                                                                                                  "override_udp_checksum_checkbox") else False,
        }

        # rate controls (flat, plus a nested summary)
        rate_type = self.rate_type_dropdown.currentText() if hasattr(self,
                                                                     "rate_type_dropdown") else "Packets Per Second (PPS)"
        pps = (self.stream_pps_rate.text().strip() if hasattr(self, "stream_pps_rate") else "1000")
        br_mbps = (self.stream_bit_rate.text().strip() if hasattr(self, "stream_bit_rate") else "100")
        load_pct = (self.stream_load_percentage.text().strip() if hasattr(self, "stream_load_percentage") else "50")
        duration_mode = self.duration_mode_dropdown.currentText() if hasattr(self,
                                                                             "duration_mode_dropdown") else "Continuous"
        duration_seconds = (self.stream_duration_field.text().strip() if hasattr(self,
                                                                                 "stream_duration_field") else "10") if duration_mode == "Seconds" else None

        # final object
        stream_details = {
            "name": name,
            "enabled": enabled,
            "details": details,
            "rx_port": rx_pick,  # "Same as TX Port" is OK; caller can replace with TX when needed
            "flow_tracking_enabled": flow_tracking,
            "dpdk_enable": bool(getattr(self, "dpdk_enable_checkbox", None) and self.dpdk_enable_checkbox.isChecked()),
            "dpdk_multi_instance": bool(getattr(self, "dpdk_multi_instance_checkbox", None) and self.dpdk_multi_instance_checkbox.isChecked()),
            "frame_type": frame_type,
            "frame_min": frame_min,
            "frame_max": frame_max,
            "frame_size": frame_size,

            "L1": L1,
            "VLAN": VLAN_sel,
            "L2": L2,
            "L3": L3,
            "L4": L4,
            "Payload": Payload,

            "pcap_stream": pcap_stream,  # top-level for server-side convenience
            "protocol_data": protocol_data,
            "override_settings": override_settings,

            # keep the historical flat fields too (some code reads these)
            "stream_rate_type": rate_type,
            "stream_pps_rate": pps,
            "stream_bit_rate": br_mbps,
            "stream_load_percentage": load_pct,
            "stream_duration_mode": duration_mode,
            "stream_duration_seconds": duration_seconds if duration_mode == "Seconds" else None,
        }

        if duration_mode == "Seconds":
            stream_details["stream_duration_seconds"] = duration_seconds

        # duplicate a light 'protocol_selection' view (several callers expect it)
        stream_details["protocol_selection"] = {
            "name": name,
            "enabled": enabled,
            "details": details,
            "frame_type": frame_type,
            "frame_min": frame_min,
            "frame_max": frame_max,
            "frame_size": frame_size,
            "L1": L1,
            "VLAN": VLAN_sel,
            "L2": L2,
            "L3": L3,
            "L4": L4,
            "Payload": Payload,
            "flow_tracking_enabled": flow_tracking,
            "dpdk_enable": stream_details["dpdk_enable"],
            "pcap_stream": pcap_stream,  # keep here too for backward-compat populate
        }

        return stream_details

    # ----------------------------- Packet View rendering -----------------------------

    def populate_packet_view(self, stream_data=None):
        self.packet_tree.clear()
        if not isinstance(stream_data, dict):
            return

        # Show engine selection
        engine_item = QTreeWidgetItem([
            "Engine",
            "DPDK (tx_worker)" if stream_data.get("dpdk_enable") else "Scapy / Kernel"
        ])
        self.packet_tree.addTopLevelItem(engine_item)

        def getpd(section, key, default=None):
            return stream_data.get("protocol_data", {}).get(section, {}).get(key, default)

        # Shortcuts
        L2 = stream_data.get("L2", "None")
        VLAN_sel = stream_data.get("VLAN", "Untagged")
        L3 = stream_data.get("L3", "None")
        L4 = stream_data.get("L4", "None")
        Payload = stream_data.get("Payload", "None")

        # MAC
        if L2 != "None":
            mac_item = QTreeWidgetItem(["MAC (Media Access)", ""])
            mac_item.addChild(QTreeWidgetItem([
                "Destination",
                f"{getpd('mac','mac_destination_mode','Fixed')} - {getpd('mac','mac_destination_address','00:00:00:00:00:00')}"
            ]))
            mac_item.addChild(QTreeWidgetItem([
                "Source",
                f"{getpd('mac','mac_source_mode','Fixed')} - {getpd('mac','mac_source_address','00:00:00:00:00:00')}"
            ]))
            self.packet_tree.addTopLevelItem(mac_item)

        # VLAN
        if VLAN_sel != "Untagged":
            vlan_item = QTreeWidgetItem(["VLAN", f"{VLAN_sel}"])
            vlan_item.addChild(QTreeWidgetItem(["VLAN ID", getpd("vlan", "vlan_id", "1")]))
            vlan_item.addChild(QTreeWidgetItem(["Priority", getpd("vlan", "vlan_priority", "0")]))
            vlan_item.addChild(QTreeWidgetItem(["CFI/DEI", getpd("vlan", "vlan_cfi_dei", "0")]))
            vlan_item.addChild(QTreeWidgetItem(["TPID", getpd("vlan", "vlan_tpid", "81 00")]))
            self.packet_tree.addTopLevelItem(vlan_item)

        # MPLS
        if L2 == "MPLS":
            mpls_item = QTreeWidgetItem(["MPLS", ""])
            mpls_item.addChild(QTreeWidgetItem(["Label", getpd("mpls", "mpls_label", "16")]))
            mpls_item.addChild(QTreeWidgetItem(["TTL", getpd("mpls", "mpls_ttl", "64")]))
            mpls_item.addChild(QTreeWidgetItem(["Experimental", getpd("mpls", "mpls_experimental", "0")]))
            self.packet_tree.addTopLevelItem(mpls_item)

        # L3
        if L3 != "None":
            l3_item = QTreeWidgetItem(["L3 (Network Layer)", L3])
            if L3 == "IPv4":
                l3_item.addChild(QTreeWidgetItem(["Source", getpd("ipv4", "ipv4_source", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["Destination", getpd("ipv4", "ipv4_destination", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["ToS/DSCP Mode", getpd("ipv4", "tos_dscp_mode", "TOS")]))
                l3_item.addChild(QTreeWidgetItem(["ECN", getpd("ipv4", "ipv4_ecn", "Not-ECT")]))
                l3_item.addChild(QTreeWidgetItem(["TTL", getpd("ipv4", "ipv4_ttl", "64")]))
            elif L3 == "IPv6":
                l3_item.addChild(QTreeWidgetItem(["Source", getpd("ipv6", "ipv6_source", "2001:db8::1")]))
                l3_item.addChild(QTreeWidgetItem(["Destination", getpd("ipv6", "ipv6_destination", "2001:db8::2")]))
                l3_item.addChild(QTreeWidgetItem(["Traffic Class", getpd("ipv6", "ipv6_traffic_class", "0")]))
                l3_item.addChild(QTreeWidgetItem(["Flow Label", getpd("ipv6", "ipv6_flow_label", "0")]))
                l3_item.addChild(QTreeWidgetItem(["Hop Limit", getpd("ipv6", "ipv6_hop_limit", "64")]))
            elif L3 == "ARP":
                l3_item.addChild(QTreeWidgetItem(["Operation", getpd("arp", "arp_operation", "Request")]))
                l3_item.addChild(QTreeWidgetItem(["Sender MAC", getpd("arp", "arp_sender_mac", "00:11:22:33:44:55")]))
                l3_item.addChild(QTreeWidgetItem(["Sender IP", getpd("arp", "arp_sender_ip", "0.0.0.0")]))
                l3_item.addChild(QTreeWidgetItem(["Target MAC", getpd("arp", "arp_target_mac", "ff:ff:ff:ff:ff:ff")]))
                l3_item.addChild(QTreeWidgetItem(["Target IP", getpd("arp", "arp_target_ip", "0.0.0.0")]))
            self.packet_tree.addTopLevelItem(l3_item)

        # L4
        if L4 != "None":
            l4_item = QTreeWidgetItem(["L4 (Transport Layer)", L4])
            if L4 == "TCP":
                l4_item.addChild(QTreeWidgetItem(["Src Port", getpd("tcp", "tcp_source_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst Port", getpd("tcp", "tcp_destination_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Flags", getpd("tcp", "tcp_flags", "") or "—"]))
            elif L4 == "UDP":
                l4_item.addChild(QTreeWidgetItem(["Src Port", getpd("udp", "udp_source_port", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst Port", getpd("udp", "udp_destination_port", "0")]))
                preset = getpd("udp", "udp_preset", "Custom")
                l4_item.addChild(QTreeWidgetItem(["Preset", preset]))
                if getpd("udp", "udp_bootp_enabled", False):
                    l4_item.addChild(QTreeWidgetItem(["BOOTP/DHCP", getpd("udp", "bootp_msg_type", "DHCPDISCOVER")]))
            elif L4 == "RoCEv2":
                l4_item.addChild(QTreeWidgetItem(["Traffic Class", getpd("rocev2", "rocev2_traffic_class", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Flow Label", getpd("rocev2", "rocev2_flow_label", "000000")]))
                l4_item.addChild(QTreeWidgetItem(["Src GID", getpd("rocev2", "rocev2_source_gid", "")]))
                l4_item.addChild(QTreeWidgetItem(["Dst GID", getpd("rocev2", "rocev2_destination_gid", "")]))
                l4_item.addChild(QTreeWidgetItem(["Src QP", getpd("rocev2", "rocev2_source_qp", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Dst QP", getpd("rocev2", "rocev2_destination_qp", "0")]))
                l4_item.addChild(QTreeWidgetItem(["Send CNP", "Yes" if getpd("rocev2", "send_cnp", False) else "No"]))
            elif L4 == "UEC":
                l4_item.addChild(QTreeWidgetItem(["QP Range", f"{getpd('uec','qp_start','1000')}–{getpd('uec','qp_end','1010')}"]))
                l4_item.addChild(QTreeWidgetItem(["PASID Range", f"{getpd('uec','pasid_start','5000')}–{getpd('uec','pasid_end','5010')}"]))
                l4_item.addChild(QTreeWidgetItem(["ECN", getpd("uec", "ecn", "Not-ECT")]))
                l4_item.addChild(QTreeWidgetItem(["Flow Label", getpd("uec", "flow_label", "0")]))
                if getpd("uec", "enable_rocev2", False):
                    l4_item.addChild(QTreeWidgetItem(["Embedded", "RoCEv2"]))
            self.packet_tree.addTopLevelItem(l4_item)

        # Payload
        if Payload != "None":
            p = QTreeWidgetItem(["Payload", Payload])
            p.addChild(QTreeWidgetItem(["Data", stream_data.get("protocol_data", {}).get("payload_data", {}).get("data", "")]))
            self.packet_tree.addTopLevelItem(p)
