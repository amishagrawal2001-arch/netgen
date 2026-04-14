# traffic_client/dpdk_menu_actions.py
import logging
import requests
from requests.exceptions import ConnectionError, Timeout
from PyQt5.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QLabel, QAbstractItemView, QComboBox,
    QTextEdit, QScrollArea, QCheckBox, QWidget, QApplication
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

logger = logging.getLogger(__name__)


class TrafficGenClientDPDKMenuActions():
    """DPDK menu actions for the traffic generator client."""
    
    def show_dpdk_status(self):
        """Show DPDK status for selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        # Use a custom dialog with scrollable text and inline buttons
        dialog = QDialog(self)
        dialog.setWindowTitle("DPDK Status")
        dialog.setGeometry(300, 300, 700, 500)
        
        layout = QVBoxLayout(dialog)
        
        # Parse results and create formatted display with inline buttons
        status_widgets = []
        server_status_map = {}
        
        for i, server in enumerate(selected_servers):
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Check if server is online before making API call
            if not server.get("online", True):
                server_label = QLabel(f"TG {tg_id} ({address}):")
                server_label.setStyleSheet("font-weight: bold; font-size: 12px; color: red;")
                layout.addWidget(server_label)
                text_edit = QTextEdit()
                text_edit.setReadOnly(True)
                text_edit.setPlainText(f"Server is offline or unreachable.")
                text_edit.setFontFamily("Courier")
                text_edit.setFontPointSize(9)
                layout.addWidget(text_edit)
                layout.addWidget(QLabel(""))  # Spacer
                continue
            
            # Get status data for this server (single API call, reduced timeout)
            status_data = None
            try:
                response = requests.get(f"{address}/api/dpdk/status", timeout=3)
                if response.status_code == 200:
                    status_data = response.json()
                    server_status_map[address] = (server, status_data)
            except requests.exceptions.ConnectionError:
                status_data = None
            except requests.exceptions.Timeout:
                status_data = None
            except Exception:
                status_data = None
            
            # Create server section
            server_label = QLabel(f"TG {tg_id} ({address}):")
            server_label.setStyleSheet("font-weight: bold; font-size: 12px;")
            layout.addWidget(server_label)
            
            # Create scrollable text area for status
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            if status_data:
                status_text = self._format_dpdk_status(status_data)
                text_edit.setPlainText(status_text)
            else:
                text_edit.setPlainText("Failed to get status from server")
            text_edit.setFontFamily("Courier")
            text_edit.setFontPointSize(9)
            text_edit.setMaximumHeight(200)
            layout.addWidget(text_edit)
            
            # Add inline Fix button if IOMMU is not enabled
            if status_data and not status_data.get('iommu_enabled', False):
                warning_layout = QHBoxLayout()
                warning_label = QLabel("WARNING: IOMMU is not enabled. Enable in GRUB:")
                warning_label.setStyleSheet("color: red; font-weight: bold;")
                warning_layout.addWidget(warning_label)
                
                # Show IOMMU parameters
                cpu_vendor = "intel"  # default
                try:
                    cpu_response = requests.get(f"{address}/api/dpdk/cpu-vendor", timeout=3)
                    if cpu_response.status_code == 200:
                        cpu_data = cpu_response.json()
                        cpu_vendor = cpu_data.get('vendor', 'intel').lower()
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    pass
                except Exception:
                    pass
                
                iommu_params = f"{cpu_vendor}_iommu=on iommu=pt" if cpu_vendor in ["intel", "amd"] else "intel_iommu=on iommu=pt"
                params_label = QLabel(f"{iommu_params}")
                font = QFont("Courier")
                params_label.setFont(font)
                warning_layout.addWidget(params_label)
                
                # Fix button inline
                fix_button = QPushButton("[FIX]")
                fix_button.setStyleSheet("background-color: #dc2626; color: white; font-weight: bold; padding: 5px 15px;")
                fix_button.clicked.connect(lambda checked, s=server, d=dialog: self._handle_fix_iommu_from_status(s, d))
                warning_layout.addWidget(fix_button)
                
                layout.addLayout(warning_layout)
            
            # Add Load Modules button if VFIO modules are not loaded
            if status_data and not status_data.get('vfio_pci_loaded', False):
                modules_layout = QVBoxLayout()
                
                # Warning message with Read More button
                warning_layout = QHBoxLayout()
                warning_label = QLabel("⚠ VFIO modules are not loaded")
                warning_label.setStyleSheet("color: orange; font-weight: bold; font-size: 11px;")
                warning_layout.addWidget(warning_label)
                
                # Read More button
                read_more_button = QPushButton("📖 Read More")
                read_more_button.setStyleSheet("background-color: #3b82f6; color: white; font-size: 9px; padding: 3px 10px; border-radius: 3px;")
                read_more_button.setMaximumWidth(90)
                read_more_button.clicked.connect(lambda: self._show_vfio_info_dialog())
                warning_layout.addWidget(read_more_button)
                warning_layout.addStretch()  # Push button to the left
                
                modules_layout.addLayout(warning_layout)
                
                # Manual command instructions
                manual_label = QLabel("Manual command (SSH to server):")
                manual_label.setStyleSheet("font-size: 10px; color: #666;")
                modules_layout.addWidget(manual_label)
                
                # Command text with copy button
                command_layout = QHBoxLayout()
                command_text = QLabel("sudo modprobe vfio && sudo modprobe vfio-pci")
                command_text.setStyleSheet("font-family: 'Courier New', monospace; font-size: 10px; background-color: #f3f4f6; padding: 5px; border: 1px solid #d1d5db; border-radius: 3px;")
                command_text.setWordWrap(False)
                command_layout.addWidget(command_text)
                
                # Copy to clipboard button
                copy_button = QPushButton("📋 Copy")
                copy_button.setStyleSheet("background-color: #6b7280; color: white; font-size: 9px; padding: 3px 8px; border-radius: 3px;")
                copy_button.setMaximumWidth(60)
                copy_button.clicked.connect(lambda: self._copy_to_clipboard("sudo modprobe vfio && sudo modprobe vfio-pci"))
                command_layout.addWidget(copy_button)
                
                modules_layout.addLayout(command_layout)
                
                # Load Modules button
                button_layout = QHBoxLayout()
                load_label = QLabel("Or load modules automatically:")
                load_label.setStyleSheet("font-size: 10px; color: #666;")
                button_layout.addWidget(load_label)
                
                load_modules_button = QPushButton("[LOAD MODULES]")
                load_modules_button.setStyleSheet("background-color: #f59e0b; color: white; font-weight: bold; padding: 5px 15px; border-radius: 3px;")
                load_modules_button.clicked.connect(lambda checked, s=server, d=dialog: self._handle_load_modules_from_status(s, d))
                button_layout.addWidget(load_modules_button)
                
                modules_layout.addLayout(button_layout)
                layout.addLayout(modules_layout)
            
            layout.addWidget(QLabel(""))  # Spacer between servers
        
        # OK button at bottom
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(self, "Copied", f"Command copied to clipboard:\n\n{text}")
    
    def _show_vfio_info_dialog(self):
        """Show informational dialog about VFIO modules."""
        dialog = QDialog(self)
        dialog.setWindowTitle("About VFIO Modules")
        dialog.setGeometry(300, 300, 700, 600)
        
        layout = QVBoxLayout(dialog)
        
        # Title
        title_label = QLabel("What is VFIO?")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e40af; margin-bottom: 10px;")
        layout.addWidget(title_label)
        
        # Scrollable content area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(450)
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # What is VFIO section
        what_label = QLabel("What is VFIO?")
        what_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(what_label)
        
        what_text = QLabel(
            "VFIO (Virtual Function I/O) is a Linux kernel framework that enables secure device "
            "passthrough to user-space applications. It provides:\n\n"
            "• Security: IOMMU isolation prevents devices from accessing unauthorized memory\n"
            "• Performance: Direct hardware access reduces kernel overhead\n"
            "• Flexibility: Supports virtualization and user-space drivers like DPDK"
        )
        what_text.setWordWrap(True)
        what_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(what_text)
        
        # VFIO Modules section
        modules_label = QLabel("VFIO Modules")
        modules_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(modules_label)
        
        modules_text = QLabel(
            "Two main modules are required:\n\n"
            "1. vfio (Core Module)\n"
            "   • Base framework for device passthrough\n"
            "   • Manages IOMMU groups and device isolation\n"
            "   • Provides infrastructure for secure device access\n\n"
            "2. vfio-pci (PCI Device Driver)\n"
            "   • PCI-specific driver for VFIO\n"
            "   • Binds to PCI devices (like network cards)\n"
            "   • Required for DPDK to bind NICs to user-space"
        )
        modules_text.setWordWrap(True)
        modules_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; font-family: 'Courier New', monospace; margin-bottom: 15px;")
        content_layout.addWidget(modules_text)
        
        # Why VFIO for DPDK section
        why_label = QLabel("Why VFIO for DPDK?")
        why_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(why_label)
        
        why_text = QLabel(
            "DPDK needs direct hardware access for high-performance packet processing:\n\n"
            "Traditional Kernel Drivers:\n"
            "  Packets → Kernel Network Stack → User Application (Slower)\n\n"
            "DPDK with VFIO:\n"
            "  Packets → Direct to User-Space Application (Much Faster)\n\n"
            "This enables:\n"
            "• Line-rate performance (100Gbps, 400Gbps)\n"
            "• Low latency packet processing\n"
            "• Bypassing kernel overhead"
        )
        why_text.setWordWrap(True)
        why_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(why_text)
        
        # Requirements section
        req_label = QLabel("Requirements")
        req_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(req_label)
        
        req_text = QLabel(
            "To use VFIO with DPDK, you need:\n\n"
            "1. IOMMU Enabled ✓\n"
            "   • Intel: intel_iommu=on iommu=pt in GRUB\n"
            "   • AMD: amd_iommu=on iommu=pt in GRUB\n\n"
            "2. VFIO Modules Loaded\n"
            "   • sudo modprobe vfio\n"
            "   • sudo modprobe vfio-pci\n\n"
            "3. Device Bound to VFIO\n"
            "   • Unbind from kernel driver\n"
            "   • Bind to vfio-pci driver"
        )
        req_text.setWordWrap(True)
        req_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(req_text)
        
        # Note about Mellanox
        note_label = QLabel("Note: Mellanox/NVIDIA NICs")
        note_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; margin-top: 10px;")
        content_layout.addWidget(note_label)
        
        note_text = QLabel(
            "Some NICs (like Mellanox/NVIDIA) don't require VFIO:\n"
            "• They use the mlx5_core kernel driver\n"
            "• DPDK's mlx5 PMD works with the kernel driver\n"
            "• No VFIO binding needed\n\n"
            "For Broadcom, Intel, and AMD NICs, VFIO is typically required."
        )
        note_text.setWordWrap(True)
        note_text.setStyleSheet("font-size: 11px; color: #4b5563; line-height: 1.5; margin-bottom: 15px;")
        content_layout.addWidget(note_text)
        
        content_layout.addStretch()
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        
        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.setStyleSheet("background-color: #3b82f6; color: white; padding: 8px 20px; border-radius: 4px;")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _handle_load_modules_from_status(self, server, status_dialog):
        """Handle Load Modules button click from status dialog."""
        address = server.get("address", "")
        
        # Confirm action
        reply = QMessageBox.question(
            self,
            "Load VFIO Modules",
            f"This will load the VFIO kernel modules (vfio-pci and vfio) on:\n\n"
            f"Server: {address}\n\n"
            f"These modules are required for DPDK to bind NICs to vfio-pci driver.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            # Define refresh callback to reopen status dialog
            def refresh_status():
                status_dialog.accept()  # Close current dialog
                # Longer delay to ensure modules are fully registered in kernel
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(1500, lambda: self.show_dpdk_status())
            
            self._perform_load_modules(address, refresh_callback=refresh_status)
    
    def _handle_fix_iommu_from_status(self, server, status_dialog):
        """Handle Fix IOMMU button click from status dialog."""
        status_dialog.accept()  # Close status dialog first
        
        address = server.get("address", "")
        
        # Get current IOMMU status
        try:
            response = requests.get(f"{address}/api/dpdk/status", timeout=3)
            if response.status_code == 200:
                status_data = response.json()
                current_iommu_enabled = status_data.get('iommu_enabled', False)
                iommu_details = status_data.get('iommu_details', '')
                
                # Detect CPU vendor
                cpu_vendor = "intel"  # default
                try:
                    cpu_vendor_response = requests.get(f"{address}/api/dpdk/cpu-vendor", timeout=3)
                    if cpu_vendor_response.status_code == 200:
                        cpu_vendor_data = cpu_vendor_response.json()
                        cpu_vendor = cpu_vendor_data.get('vendor', 'intel').lower()
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    pass
            else:
                QMessageBox.warning(self, "Error", f"Failed to get server status: HTTP {response.status_code}")
                return
        except requests.exceptions.ConnectionError:
            QMessageBox.warning(self, "Error", f"Server is unreachable: {address}")
            return
        except requests.exceptions.Timeout:
            QMessageBox.warning(self, "Error", f"Server request timed out: {address}")
            return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to get server status: {str(e)}")
            return
        
        # Show IOMMU configuration dialog
        config_dialog = self._create_iommu_dialog(current_iommu_enabled, iommu_details, cpu_vendor)
        if config_dialog.exec() == QDialog.Accepted:
            enable_iommu = config_dialog.enable_iommu
            reboot_after = config_dialog.reboot_after
            if enable_iommu is not None:
                self._perform_configure_iommu(address, enable_iommu, cpu_vendor, reboot_after)
    
    def _format_dpdk_status(self, status_data):
        """Format DPDK status data for display."""
        lines = []
        lines.append(f"DPDK Installed: {'Yes' if status_data.get('dpdk_installed') else 'No'}")
        tx_worker_status = 'Found' if status_data.get('tx_worker_exists') else 'Not Found'
        lines.append(f"DPDK Packet Generator (tx_worker): {tx_worker_status}")
        lines.append(f"Hugepages Configured: {'Yes' if status_data.get('hugepages_configured') else 'No'}")
        
        if status_data.get('hugepages_configured'):
            lines.append(f"  - Available: {status_data.get('hugepages_available', 0)}")
            lines.append(f"  - Size: {status_data.get('hugepage_size', 'N/A')}")
        
        # IOMMU status (critical for vfio-pci binding)
        iommu_enabled = status_data.get('iommu_enabled', False)
        iommu_details = status_data.get('iommu_details', 'Unknown')
        iommu_status = '✓ Enabled' if iommu_enabled else '✗ Not Enabled'
        lines.append(f"\nIOMMU Status: {iommu_status}")
        lines.append(f"  - {iommu_details}")
        
        if not iommu_enabled:
            lines.append(f"")
            lines.append(f"  WARNING: IOMMU is required for vfio-pci binding (Broadcom/Intel/AMD NICs)")
            lines.append(f"  WARNING: Enable in GRUB: intel_iommu=on iommu=pt (Intel) or amd_iommu=on iommu=pt (AMD)")
            lines.append(f"  WARNING: Then reboot the server")
        
        # VFIO module status
        vfio_pci_loaded = status_data.get('vfio_pci_loaded', False)
        vfio_loaded = status_data.get('vfio_loaded', False)
        lines.append(f"\nVFIO Modules:")
        lines.append(f"  - vfio-pci: {'✓ Loaded' if vfio_pci_loaded else '✗ Not Loaded'}")
        lines.append(f"  - vfio: {'✓ Loaded' if vfio_loaded else '✗ Not Loaded'}")
        
        if not vfio_pci_loaded and iommu_enabled:
            lines.append(f"")
            lines.append(f"  WARNING: VFIO modules are not loaded")
            lines.append(f"  Manual command (SSH to server):")
            lines.append(f"    sudo modprobe vfio")
            lines.append(f"    sudo modprobe vfio-pci")
            lines.append(f"  Or use the [LOAD MODULES] button below")
        
        interfaces = status_data.get('interfaces', [])
        if interfaces:
            lines.append(f"\nInterfaces ({len(interfaces)}):")
            for iface in interfaces:
                pci = iface.get('pci', 'N/A')
                driver = iface.get('driver', 'N/A')
                status = iface.get('status', 'N/A')
                vendor = iface.get('vendor', 'N/A')
                lines.append(f"  - {iface.get('name', 'N/A')}: PCI={pci}, Driver={driver}, Status={status}, Vendor={vendor}")
        
        return "\n".join(lines)
    
    def bind_interface_to_dpdk(self):
        """Bind an interface to DPDK."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        # For now, support single server selection
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for binding operations.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # Get available interfaces from server
        try:
            response = requests.get(f"{address}/api/dpdk/interfaces", timeout=3)
            if response.status_code != 200:
                QMessageBox.warning(self, "Error", f"Failed to get interfaces: HTTP {response.status_code}")
                return
            
            interfaces_data = response.json()
            all_interfaces = interfaces_data.get('interfaces', [])
            
            # Filter to only show interfaces that are NOT bound to DPDK
            # (kernel-bound interfaces and unbound interfaces can be bound to DPDK)
            bindable_interfaces = []
            for iface in all_interfaces:
                driver = iface.get('driver', '')
                # Exclude DPDK-bound interfaces (they're already bound)
                if driver not in ['vfio-pci', 'uio_pci_generic']:
                    bindable_interfaces.append(iface)
            
            if not bindable_interfaces:
                QMessageBox.information(
                    self, 
                    "No Interfaces", 
                    "No interfaces available for DPDK binding.\n\n"
                    "All interfaces are already bound to DPDK, or no interfaces are available."
                )
                return
            
            # Show interface selection dialog
            dialog = self._create_interface_selection_dialog(bindable_interfaces, "Bind Interface to DPDK")
            if dialog.exec() == QDialog.Accepted:
                selected_interface = dialog.selected_interface
                if selected_interface:
                    self._perform_bind(address, selected_interface, force=False)
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error", f"Server is unreachable: {address}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error", f"Server request timed out: {address}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get interfaces: {str(e)}")
    
    def unbind_interface_from_dpdk(self):
        """Unbind an interface from DPDK or restore unbound interface to kernel driver."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for unbinding operations.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # Get all interfaces from server (including unbound)
        try:
            response = requests.get(f"{address}/api/dpdk/interfaces", timeout=3)
            if response.status_code != 200:
                QMessageBox.warning(self, "Error", f"Failed to get interfaces: HTTP {response.status_code}")
                return
            
            interfaces_data = response.json()
            interfaces = interfaces_data.get('interfaces', [])
            
            # Filter to DPDK-bound interfaces AND unbound interfaces (for restoration)
            dpdk_interfaces = []
            unbound_interfaces = []
            
            for iface in interfaces:
                driver = iface.get('driver', '')
                status = iface.get('status', '')
                
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    # DPDK-bound interface - can be unbound
                    dpdk_interfaces.append(iface)
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    # Unbound interface - can be restored to kernel driver
                    if iface.get('kernel_driver'):
                        unbound_interfaces.append(iface)
            
            # Combine both lists for the dialog
            all_interfaces = dpdk_interfaces + unbound_interfaces
            
            if not all_interfaces:
                QMessageBox.information(
                    self, 
                    "No Interfaces", 
                    "No interfaces are currently bound to DPDK, and no unbound interfaces can be restored.\n\n"
                    "To restore an unbound interface, it must have a kernel driver available."
                )
                return
            
            # Show interface selection dialog with appropriate title
            title = "Unbind from DPDK / Restore to Kernel"
            dialog = self._create_interface_selection_dialog(all_interfaces, title)
            if dialog.exec() == QDialog.Accepted:
                selected_interface = dialog.selected_interface
                if selected_interface:
                    self._perform_unbind(address, selected_interface)
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error", f"Server is unreachable: {address}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error", f"Server request timed out: {address}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get interfaces: {str(e)}")
    
    def verify_dpdk(self):
        """Verify DPDK installation on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        results = []
        for server in selected_servers:
            address = server.get("address", "")
            tg_id = server.get("tg_id", "?")
            
            # Check if server is online
            if not server.get("online", True):
                results.append(f"TG {tg_id} ({address}): Server is offline or unreachable")
                continue
            
            try:
                response = requests.get(f"{address}/api/dpdk/verify", timeout=15)
                if response.status_code == 200:
                    verify_data = response.json()
                    results.append(f"TG {tg_id} ({address}):\n{self._format_verify_results(verify_data)}")
                else:
                    results.append(f"TG {tg_id} ({address}): Failed to verify (HTTP {response.status_code})")
            except Exception as e:
                results.append(f"TG {tg_id} ({address}): Error - {str(e)}")
        
        msg = QMessageBox(self)
        msg.setWindowTitle("DPDK Verification")
        msg.setText("\n\n".join(results))
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()
    
    def _format_verify_results(self, verify_data):
        """Format DPDK verification results for display."""
        lines = []
        lines.append(f"DPDK Libraries: {'✓' if verify_data.get('dpdk_libraries') else '✗'}")
        lines.append(f"DPDK Packet Generator (tx_worker): {'✓' if verify_data.get('tx_worker_binary') else '✗'}")
        lines.append(f"Hugepages: {'✓' if verify_data.get('hugepages') else '✗'}")
        lines.append(f"Kernel Modules: {'✓' if verify_data.get('kernel_modules') else '✗'}")
        
        if verify_data.get('messages'):
            lines.append("\nDetails:")
            for msg in verify_data.get('messages', []):
                lines.append(f"  - {msg}")
        
        return "\n".join(lines)
    
    def configure_hugepages(self):
        """Configure hugepages on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for hugepage configuration.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Show hugepage configuration dialog
        dialog = self._create_hugepage_dialog()
        if dialog.exec() == QDialog.Accepted:
            num_pages = dialog.num_pages
            if num_pages:
                self._perform_configure_hugepages(address, num_pages)
    
    def load_vfio_modules(self):
        """Load VFIO kernel modules on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for loading VFIO modules.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # Show custom dialog with Read More option
        dialog = self._create_load_vfio_dialog(address)
        if dialog.exec() == QDialog.Accepted:
            # Perform the load operation
            self._perform_load_modules(address)
            # Offer to refresh status after loading
            refresh_reply = QMessageBox.question(
                self,
                "Refresh Status?",
                "Would you like to refresh the DPDK Status to verify modules are loaded?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if refresh_reply == QMessageBox.Yes:
                self.show_dpdk_status()
    
    def _create_load_vfio_dialog(self, server_address):
        """Create a dialog for loading VFIO modules with Read More option."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Load VFIO Modules")
        dialog.setGeometry(300, 300, 550, 300)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel(
            f"This will load the VFIO kernel modules (vfio-pci and vfio) on:\n\n"
            f"Server: {server_address}\n\n"
            f"These modules are required for DPDK to bind NICs to vfio-pci driver."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
        layout.addWidget(info_label)
        
        # Read More button
        read_more_button = QPushButton("Read More About VFIO Modules")
        read_more_button.setStyleSheet("text-align: left; padding: 5px;")
        read_more_button.clicked.connect(lambda: self._show_vfio_info_dialog())
        layout.addWidget(read_more_button)
        
        # Buttons
        button_layout = QHBoxLayout()
        yes_button = QPushButton("Yes, Load Modules")
        yes_button.clicked.connect(dialog.accept)
        no_button = QPushButton("Cancel")
        no_button.clicked.connect(dialog.reject)
        button_layout.addWidget(yes_button)
        button_layout.addWidget(no_button)
        layout.addLayout(button_layout)
        
        return dialog
    
    def configure_iommu(self):
        """Configure IOMMU on selected server(s)."""
        selected_servers = self._get_selected_servers()
        if not selected_servers:
            QMessageBox.warning(self, "No Server Selected", "Please select a server from the server tree.")
            return
        
        if len(selected_servers) > 1:
            QMessageBox.information(self, "Multiple Servers", "Please select only one server for IOMMU configuration.")
            return
        
        server = selected_servers[0]
        address = server.get("address", "")
        
        # Check if server is online
        if not server.get("online", True):
            QMessageBox.warning(self, "Server Offline", f"Server {address} is offline or unreachable.")
            return
        
        # First, get current IOMMU status from server
        try:
            response = requests.get(f"{address}/api/dpdk/status", timeout=3)
            if response.status_code == 200:
                status_data = response.json()
                current_iommu_enabled = status_data.get('iommu_enabled', False)
                iommu_details = status_data.get('iommu_details', '')
                
                # Detect CPU vendor from server
                cpu_vendor = "intel"  # default
                try:
                    cpu_vendor_response = requests.get(f"{address}/api/dpdk/cpu-vendor", timeout=3)
                    if cpu_vendor_response.status_code == 200:
                        cpu_vendor_data = cpu_vendor_response.json()
                        cpu_vendor = cpu_vendor_data.get('vendor', 'intel').lower()
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    pass
            else:
                current_iommu_enabled = False
                iommu_details = "Could not determine current status"
                cpu_vendor = "intel"
        except requests.exceptions.ConnectionError:
            QMessageBox.warning(self, "Error", f"Server is unreachable: {address}")
            return
        except requests.exceptions.Timeout:
            QMessageBox.warning(self, "Error", f"Server request timed out: {address}")
            return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to get server status: {str(e)}")
            return
        
        # Show IOMMU configuration dialog
        dialog = self._create_iommu_dialog(current_iommu_enabled, iommu_details, cpu_vendor)
        if dialog.exec() == QDialog.Accepted:
            enable_iommu = dialog.enable_iommu
            reboot_after = dialog.reboot_after
            if enable_iommu is not None:
                self._perform_configure_iommu(address, enable_iommu, cpu_vendor, reboot_after)
    
    def _get_selected_servers(self):
        """Get currently selected servers from the server tree."""
        if not hasattr(self, 'server_tree'):
            return []
        
        selected_items = self.server_tree.selectedItems()
        if not selected_items:
            return []
        
        servers = []
        for item in selected_items:
            # Get parent if this is an interface item
            parent = item.parent()
            if parent:
                # This is an interface, get the server from parent
                server_address = parent.text(1)  # Server address column
            else:
                # This is a server item
                server_address = item.text(1)  # Server address column
            
            # Find server in server_interfaces
            for server in getattr(self, 'server_interfaces', []):
                if server.get('address') == server_address:
                    if server not in servers:
                        servers.append(server)
                    break
        
        return servers
    
    def _create_interface_selection_dialog(self, interfaces, title):
        """Create a dialog for selecting an interface."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setGeometry(300, 300, 750, 500)
        
        layout = QVBoxLayout(dialog)
        
        label = QLabel("Select an interface:")
        layout.addWidget(label)
        
        # Add info label explaining what will happen (only for unbind dialog)
        if "Unbind" in title or "Restore" in title:
            info_label = QLabel()
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
            info_label.setText(
                "• DPDK-bound interfaces: Will be unbound and restored to kernel driver (interface will appear in 'ip link show')\n"
                "• Unbound interfaces: Will be restored to kernel driver (interface will appear in 'ip link show')"
            )
            layout.addWidget(info_label)
        elif "Bind" in title:
            info_label = QLabel()
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
            info_label.setText(
                "• Selected interface will be bound to DPDK (vfio-pci)\n"
                "• Interface will disappear from 'ip link show' (bound to DPDK for high-speed packet generation)"
            )
            layout.addWidget(info_label)
        
        list_widget = QListWidget()
        for iface in interfaces:
            name = iface.get('name', 'N/A')
            pci = iface.get('pci', 'N/A')
            driver = iface.get('driver', 'N/A')
            vendor = iface.get('vendor', 'N/A')
            status = iface.get('status', '')
            kernel_driver = iface.get('kernel_driver', '')
            
            # Determine status label based on dialog type
            if "Bind" in title:
                # Bind dialog - show current status
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    status_label = "[Already DPDK-bound]"
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    status_label = "[Unbound → will bind to DPDK]"
                elif status == 'kernel-bound' or (driver and driver != 'unknown' and driver not in ['vfio-pci', 'uio_pci_generic']):
                    status_label = "[Kernel-bound → will bind to DPDK]"
                else:
                    status_label = "[Kernel-bound → will bind to DPDK]"
            else:
                # Unbind/Restore dialog - show what will happen
                if driver in ['vfio-pci', 'uio_pci_generic']:
                    status_label = "[DPDK-bound → will unbind]"
                elif status == 'unbound' or (not driver or driver == 'unknown' or driver == ''):
                    status_label = "[Unbound → will restore to kernel]"
                else:
                    status_label = "[Kernel-bound]"
            
            # Format display text
            item_text = f"{status_label} {name}"
            item_text += f"\n  PCI: {pci}, Current Driver: {driver}, Vendor: {vendor}"
            if kernel_driver and (status == 'unbound' or driver in ['unknown', '']):
                item_text += f", Restore to: {kernel_driver}"
            
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, iface)
            list_widget.addItem(item)
        
        layout.addWidget(list_widget)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        # Store selected interface
        def on_accept():
            selected_items = list_widget.selectedItems()
            if selected_items:
                dialog.selected_interface = selected_items[0].data(Qt.UserRole)
            else:
                dialog.selected_interface = None
        
        ok_button.clicked.connect(on_accept)
        dialog.selected_interface = None
        
        return dialog
    
    def _create_hugepage_dialog(self):
        """Create a dialog for configuring hugepages."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure Hugepages")
        dialog.setGeometry(300, 300, 500, 350)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel("Hugepages are required for DPDK packet generation.\nThey provide high-performance memory allocation for packet buffers.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: blue; font-style: italic; padding: 5px;")
        layout.addWidget(info_label)
        
        label = QLabel("Number of 2MB hugepages:")
        layout.addWidget(label)
        
        combo = QComboBox()
        combo.addItems(["512", "1024", "2048", "4096", "8192", "16384"])
        combo.setEditable(True)
        combo.setCurrentText("4096")
        combo.setToolTip("512 = 1GB, 1024 = 2GB, 2048 = 4GB, 4096 = 8GB, 8192 = 16GB, 16384 = 32GB")
        layout.addWidget(combo)
        
        # Memory calculation helper
        def update_memory_info():
            try:
                pages = int(combo.currentText())
                gb = (pages * 2) / 1024
                memory_label.setText(f"Memory allocation: {gb:.1f} GB ({pages} pages × 2MB)")
            except ValueError:
                memory_label.setText("Memory allocation: Invalid number")
        
        memory_label = QLabel("Memory allocation: 8.0 GB (4096 pages × 2MB)")
        memory_label.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(memory_label)
        
        combo.currentTextChanged.connect(update_memory_info)
        
        # Recommendations
        recommendations = QLabel(
            "Recommendations:\n"
            "  • 100Gbps: 2048 pages (4GB)\n"
            "  • 400Gbps: 4096-8192 pages (8-16GB)\n"
            "  • Multiple instances: 8192+ pages (16GB+)"
        )
        recommendations.setWordWrap(True)
        recommendations.setStyleSheet("color: #666; font-size: 10pt; padding: 5px;")
        layout.addWidget(recommendations)
        
        # Read More button
        read_more_button = QPushButton("Read More About Hugepages")
        read_more_button.setStyleSheet("text-align: left; padding: 5px;")
        read_more_button.clicked.connect(lambda: self._show_hugepage_info_dialog())
        layout.addWidget(read_more_button)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        def on_accept():
            try:
                dialog.num_pages = int(combo.currentText())
            except ValueError:
                dialog.num_pages = None
        
        ok_button.clicked.connect(on_accept)
        dialog.num_pages = None
        
        return dialog
    
    def _show_hugepage_info_dialog(self):
        """Show informational dialog about hugepages."""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
        
        dialog = QDialog(self)
        dialog.setWindowTitle("About Hugepages")
        dialog.setGeometry(300, 300, 700, 600)
        
        layout = QVBoxLayout(dialog)
        
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFontFamily("Courier")
        text_edit.setFontPointSize(10)
        
        hugepage_info = """
WHAT ARE HUGEPAGES?

Hugepages are large memory pages (typically 2MB or 1GB) used by the Linux kernel 
instead of the standard 4KB pages. They are essential for high-performance 
applications like DPDK.

WHY ARE HUGEPAGES NEEDED FOR DPDK?

1. Performance Benefits:
   • Reduces Translation Lookaside Buffer (TLB) misses
   • Improves memory access performance by 10-30%
   • Enables faster packet buffer allocation
   • Reduces memory fragmentation

2. DPDK Requirements:
   • DPDK applications require hugepages to allocate large contiguous memory
   • Without hugepages, DPDK applications will fail to start
   • Multiple DPDK instances need more hugepages

3. Memory Efficiency:
   • Reduces memory overhead for large packet buffers
   • Better cache locality for packet processing
   • Enables zero-copy packet operations

HOW MUCH MEMORY DO YOU NEED?

For Packet Generation Rates:
  • 1-10 Gbps:    512-1024 pages  (1-2 GB)
  • 10-100 Gbps:  1024-2048 pages (2-4 GB)
  • 100-400 Gbps: 2048-8192 pages (4-16 GB)
  • 400+ Gbps:    8192+ pages     (16+ GB)

For Multiple DPDK Instances:
  • Each instance needs ~1-2GB
  • Add 2048 pages (4GB) per additional instance
  • Example: 3 instances = 4096-6144 pages (8-12 GB)

CALCULATION:
  Memory (GB) = (Number of Pages × Page Size) / 1024
  Example: 4096 pages × 2MB = 8192 MB = 8 GB

PERMANENT CONFIGURATION:

To make hugepages persistent across reboots, add to /etc/sysctl.conf:
  vm.nr_hugepages=4096

Or use GRUB kernel parameters:
  default_hugepagesz=2M hugepagesz=2M hugepages=4096

VERIFICATION:

After configuration, verify with:
  cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
  cat /proc/meminfo | grep Huge

TROUBLESHOOTING:

If configuration fails:
  • Check available memory: free -h
  • Ensure sufficient free memory (hugepages reserve memory)
  • Try smaller values first
  • Check kernel logs: dmesg | grep -i huge

If DPDK still fails:
  • Verify hugepages are mounted: mount | grep huge
  • Check DPDK EAL options include hugepage configuration
  • Ensure user has permissions to access hugepages
        """
        
        text_edit.setPlainText(hugepage_info.strip())
        layout.addWidget(text_edit)
        
        button_layout = QHBoxLayout()
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _create_iommu_dialog(self, current_enabled, current_details, cpu_vendor):
        """Create a dialog for configuring IOMMU."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure IOMMU")
        dialog.setGeometry(300, 300, 600, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Current status
        status_label = QLabel("Current IOMMU Status:")
        status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(status_label)
        
        status_text = QLabel(f"{'Enabled' if current_enabled else 'Not Enabled'}\n{current_details}")
        status_text.setWordWrap(True)
        layout.addWidget(status_text)
        
        layout.addWidget(QLabel(""))  # Spacer
        
        # Configuration options
        config_label = QLabel("IOMMU Configuration:")
        config_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(config_label)
        
        info_text = QLabel(
            "IOMMU is required for vfio-pci binding (Broadcom/Intel/AMD NICs).\n"
            f"Detected CPU vendor: {cpu_vendor.upper()}\n\n"
            "This will modify GRUB configuration and requires a server reboot."
        )
        info_text.setWordWrap(True)
        layout.addWidget(info_text)
        
        layout.addWidget(QLabel(""))  # Spacer
        
        # Enable/Disable options
        enable_iommu_checkbox = QComboBox()
        enable_iommu_checkbox.addItems(["Enable IOMMU", "Disable IOMMU"])
        enable_iommu_checkbox.setCurrentIndex(0 if not current_enabled else 1)
        layout.addWidget(QLabel("Action:"))
        layout.addWidget(enable_iommu_checkbox)
        
        # Reboot option
        reboot_checkbox = QComboBox()
        reboot_checkbox.addItems(["Reboot server after configuration", "Do not reboot"])
        reboot_checkbox.setCurrentIndex(0)  # Default to reboot
        layout.addWidget(QLabel(""))
        layout.addWidget(reboot_checkbox)
        
        warning_label = QLabel(
            "WARNING: Server will reboot if you choose to enable IOMMU and reboot.\n"
            "All active connections and streams will be interrupted."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(warning_label)
        
        button_layout = QHBoxLayout()
        ok_button = QPushButton("Configure")
        ok_button.clicked.connect(dialog.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        def on_accept():
            dialog.enable_iommu = (enable_iommu_checkbox.currentIndex() == 0)
            dialog.reboot_after = (reboot_checkbox.currentIndex() == 0)
        
        ok_button.clicked.connect(on_accept)
        dialog.enable_iommu = None
        dialog.reboot_after = False
        
        return dialog
    
    def _perform_configure_iommu(self, server_address, enable_iommu, cpu_vendor, reboot_after):
        """Perform IOMMU configuration via API."""
        try:
            payload = {
                "enable": enable_iommu,
                "cpu_vendor": cpu_vendor,
                "reboot": reboot_after
            }
            
            # Show confirmation dialog
            action = "enable" if enable_iommu else "disable"
            reboot_msg = " and reboot the server" if reboot_after else ""
            reply = QMessageBox.question(
                self,
                "Confirm IOMMU Configuration",
                f"This will {action} IOMMU{reboot_msg}.\n\n"
                f"Server: {server_address}\n"
                f"CPU Vendor: {cpu_vendor.upper()}\n\n"
                f"{'WARNING: Server will reboot and all connections will be lost!' if reboot_after else ''}\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
            response = requests.post(f"{server_address}/api/dpdk/iommu", json=payload, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    if reboot_after:
                        QMessageBox.information(
                            self,
                            "Success",
                            f"IOMMU configuration updated successfully.\n\n"
                            f"Server will reboot in a few seconds.\n\n"
                            f"After reboot, check DPDK Status to verify IOMMU is enabled."
                        )
                    else:
                        QMessageBox.information(
                            self,
                            "Success",
                            f"IOMMU configuration updated successfully.\n\n"
                            f"GRUB configuration has been modified.\n\n"
                            f"Please reboot the server manually to apply changes:\n"
                            f"ssh root@<server> 'reboot'"
                        )
                else:
                    QMessageBox.warning(self, "Failed", f"Failed to configure IOMMU: {result.get('message', 'Unknown error')}")
            else:
                QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to configure IOMMU: {str(e)}")
    
    def _perform_bind(self, server_address, interface, force=False):
        """Perform bind operation via API."""
        try:
            # Only include PCI if it's valid (not "N/A" or empty)
            pci = interface.get('pci', '')
            if pci and pci != 'N/A' and ':' in pci:  # Valid PCI format: 0000:XX:XX.X
                payload = {
                    "interface": interface.get('name'),
                    "pci": pci,
                    "force": force
                }
            else:
                # Use interface name only if PCI is invalid
                payload = {
                    "interface": interface.get('name'),
                    "force": force
                }
            
            response = requests.post(f"{server_address}/api/dpdk/bind", json=payload, timeout=15)
            
            # Get full response text first (for debugging and fallback)
            full_response = response.text
            logger.info(f"[DPDK BIND] Response status: {response.status_code}")
            logger.info(f"[DPDK BIND] Response text (first 300 chars): {full_response[:300]}")
            
            # Try to parse JSON response regardless of status code
            try:
                result = response.json()
                logger.info(f"[DPDK BIND] Parsed JSON - success: {result.get('success')}, has output: {bool(result.get('output'))}")
            except Exception as e:
                # If JSON parsing fails, try to extract info from text
                result = {}
                logger.error(f"[DPDK BIND] Failed to parse JSON: {e}")
            
            # Check for active routes error in both 200 and 500 responses
            # BUT ONLY if force=False (if force=True, we've already asked the user)
            error_msg = result.get('message', '')
            output = result.get('output', '')
            
            # Combine all text sources for checking
            all_text = f"{error_msg} {output} {full_response}".lower()
            logger.info(f"[DPDK BIND] force={force}, Checking for 'active routes': {'active routes' in all_text}")
            
            # Only show dialog if force=False and we detect active routes
            if not force and ('active routes' in all_text or 'disrupt network connectivity' in all_text):
                logger.info(f"[DPDK BIND] ✓ Active routes detected (force=False), showing dialog...")
                # Offer to retry with force
                # Make sure dialog is modal and brings window to front
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Active Routes Detected")
                msg_box.setText(f"Interface {interface.get('name')} has active routes.")
                msg_box.setInformativeText("Binding will disrupt network connectivity.\n\nDo you want to force bind anyway?")
                msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                msg_box.setDefaultButton(QMessageBox.No)
                msg_box.setIcon(QMessageBox.Warning)
                # Bring window to front
                msg_box.activateWindow()
                msg_box.raise_()
                reply = msg_box.exec()
                logger.info(f"[DPDK BIND] Dialog result: {reply} (Yes={QMessageBox.Yes}, No={QMessageBox.No})")
                if reply == QMessageBox.Yes:
                    logger.info(f"[DPDK BIND] User chose to force bind, retrying with force=True...")
                    # Retry with force
                    self._perform_bind(server_address, interface, force=True)
                else:
                    logger.info(f"[DPDK BIND] User cancelled force bind")
                return
            
            # Handle success case
            if response.status_code == 200 and result.get('success'):
                QMessageBox.information(self, "Success", f"Interface {interface.get('name')} bound to DPDK successfully.")
            else:
                # Check if it's a vfio-pci binding failure
                if 'failed to bind to vfio-pci' in all_text.lower() or 'failed to bind' in all_text.lower():
                    # Provide helpful troubleshooting information
                    troubleshooting = (
                        "\n\nTroubleshooting:\n"
                        "1. Check if IOMMU is enabled:\n"
                        "   grep -i iommu /proc/cmdline\n"
                        "   Should show: intel_iommu=on iommu=pt (Intel) or amd_iommu=on iommu=pt (AMD)\n\n"
                        "2. If IOMMU is not enabled, add to GRUB and reboot:\n"
                        "   sudo nano /etc/default/grub\n"
                        "   Add: GRUB_CMDLINE_LINUX=\"... intel_iommu=on iommu=pt\"\n"
                        "   sudo update-grub && sudo reboot\n\n"
                        "3. Check vfio-pci module:\n"
                        "   lsmod | grep vfio\n"
                        "   sudo modprobe vfio-pci\n\n"
                        "4. Verify device is in an IOMMU group:\n"
                        "   find /sys/kernel/iommu_groups/ -name \"0000:c9:00.0\""
                    )
                    QMessageBox.warning(
                        self, 
                        "DPDK Binding Failed", 
                        f"Failed to bind interface {interface.get('name')} to vfio-pci.\n\n"
                        f"Error: {error_msg}\n\n"
                        f"Output:\n{output}\n"
                        f"{troubleshooting}"
                    )
                else:
                    # Show error message for other failures
                    if output:
                        QMessageBox.warning(self, "Failed", f"Failed to bind interface: {error_msg}\n\nOutput:\n{output}")
                    else:
                        QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {full_response}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to bind interface: {str(e)}")
    
    def _perform_unbind(self, server_address, interface):
        """Perform unbind operation via API (unbind from DPDK or restore unbound device to kernel)."""
        try:
            # Extract interface name and PCI - handle devices without interface names
            interface_name = interface.get('name', '')
            pci = interface.get('pci', '')
            driver = interface.get('driver', '')
            status = interface.get('status', '')
            kernel_driver = interface.get('kernel_driver', '')
            
            # Determine if this is an unbound device being restored
            is_unbound = status == 'unbound' or (driver in ['unknown', ''] and kernel_driver)
            is_dpdk_bound = driver in ['vfio-pci', 'uio_pci_generic'] or status == 'dpdk-bound'
            
            # If interface name is PCI address format (e.g., "0000:c9:00.0 (no interface)"), extract PCI
            if '(no interface)' in interface_name:
                pci = interface_name.split()[0]  # Extract PCI from "0000:c9:00.0 (no interface)"
                interface_name = None  # No interface name available
            
            payload = {
                "interface": interface_name if interface_name and interface_name != "N/A" else None,
                "pci": pci if pci and pci != "N/A" else None,
                "kernel_driver": kernel_driver
            }
            
            # Ensure we have at least PCI address
            if not payload.get('pci') and not payload.get('interface'):
                QMessageBox.warning(self, "Error", "Cannot determine PCI address or interface name for unbinding.")
                return
            
            # For unbound devices, ensure kernel_driver is provided
            if is_unbound and not kernel_driver:
                QMessageBox.warning(
                    self, 
                    "Error", 
                    f"Cannot restore device {pci}: kernel driver not available.\n\n"
                    "The device may need to be manually bound to a kernel driver."
                )
                return
            
            # Increased timeout to accommodate retry logic and interface detection (up to 60 seconds)
            response = requests.post(f"{server_address}/api/dpdk/unbind", json=payload, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                # Check for success - handle both boolean True and string "true"
                success = result.get('success', False)
                # Debug logging
                logger.debug(f"[DPDK UNBIND DEBUG] Response status: {response.status_code}, success: {success}, type: {type(success)}")
                if success is True or success == True or (isinstance(success, str) and success.lower() == 'true'):
                    display_name = interface_name if interface_name else pci
                    output = result.get('output', '')
                    
                    # Check if output indicates firmware issue (Broadcom NICs)
                    has_firmware_issue = 'firmware not responding' in output.lower() or 'firmware issue' in output.lower()
                    # Check if script recommends reboot
                    reboot_recommended = 'reboot is required' in output.lower() or \
                                       'reboot may be required' in output.lower() or \
                                       'server reboot' in output.lower() or \
                                       ('firmware not responding' in output.lower() and 'all recovery attempts' in output.lower())
                    
                    if is_unbound:
                        # Check if output indicates binding to kernel driver actually failed
                        binding_failed = 'binding to kernel driver failed' in output.lower() or \
                                        ('bind attempt completed' in output.lower() and 'current driver: unbound' in output.lower()) or \
                                        ('device remains unbound' in output.lower() and 'main goal achieved' in output.lower())
                        
                        if has_firmware_issue or binding_failed:
                            if reboot_recommended:
                                # Show critical message with reboot recommendation
                                QMessageBox.critical(
                                    self, 
                                    "Reboot Required", 
                                    f"Device {display_name} unbound from DPDK successfully.\n\n"
                                    f"⚠️ CRITICAL: Binding to kernel driver '{kernel_driver}' failed due to Broadcom firmware issue.\n\n"
                                    f"All recovery attempts have been exhausted. The firmware is not responding.\n\n"
                                    f"🔄 REBOOT REQUIRED:\n"
                                    f"The server must be rebooted to reset the Broadcom NIC firmware.\n"
                                    f"After reboot, the interface should appear normally in 'ip link show'.\n\n"
                                    f"Current Status:\n"
                                    f"• Device is unbound from DPDK ✓\n"
                                    f"• Device remains unbound (no driver)\n"
                                    f"• Interface will NOT appear in 'ip link show' until reboot\n\n"
                                    f"To reboot the server:\n"
                                    f"ssh root@<server> 'reboot'"
                                )
                            else:
                                QMessageBox.warning(
                                    self, 
                                    "Partially Successful", 
                                    f"Device {display_name} unbound from DPDK successfully.\n\n"
                                    f"⚠️ Warning: Binding to kernel driver '{kernel_driver}' failed.\n\n"
                                    f"The device is no longer bound to DPDK (main goal achieved), but binding to "
                                    f"kernel driver failed due to a Broadcom firmware initialization issue.\n\n"
                                    f"📌 Important:\n"
                                    f"• The device remains unbound (no driver)\n"
                                    f"• Unbound devices do NOT appear in 'ip link show'\n"
                                    f"• The device will still appear in the unbind dialog (this is expected)\n"
                                    f"• You can try restoring it again later\n\n"
                                    f"To fix and make interface visible:\n"
                                    f"1. Reload driver: rmmod {kernel_driver} && modprobe {kernel_driver}\n"
                                    f"2. Then bind: echo {pci} > /sys/bus/pci/drivers/{kernel_driver}/bind\n"
                                    f"3. Reset PCI device (requires server access)\n"
                                    f"4. Reboot if issue persists"
                                )
                        else:
                            QMessageBox.information(
                                self, 
                                "Success", 
                                f"Device {display_name} restored to kernel driver '{kernel_driver}'.\n\n"
                                f"The interface should now appear in 'ip link show'."
                            )
                    else:
                        # Check if script recommends reboot
                        reboot_recommended = 'reboot is required' in output.lower() or \
                                           'reboot may be required' in output.lower() or \
                                           'server reboot' in output.lower() or \
                                           ('firmware not responding' in output.lower() and 'all recovery attempts' in output.lower())
                        # Check if binding to kernel driver actually failed
                        binding_failed = 'binding to kernel driver failed' in output.lower() or \
                                        ('bind attempt completed' in output.lower() and 'current driver: unbound' in output.lower()) or \
                                        ('device remains unbound' in output.lower() and 'main goal achieved' in output.lower())
                        
                        if has_firmware_issue or binding_failed:
                            if reboot_recommended:
                                # Show critical message with reboot recommendation
                                QMessageBox.critical(
                                    self, 
                                    "Reboot Required", 
                                    f"Interface {display_name} unbound from DPDK successfully.\n\n"
                                    f"⚠️ CRITICAL: Binding to kernel driver '{kernel_driver}' failed due to Broadcom firmware issue.\n\n"
                                    f"All recovery attempts have been exhausted. The firmware is not responding.\n\n"
                                    f"🔄 REBOOT REQUIRED:\n"
                                    f"The server must be rebooted to reset the Broadcom NIC firmware.\n"
                                    f"After reboot, the interface should appear normally in 'ip link show'.\n\n"
                                    f"Current Status:\n"
                                    f"• Device is unbound from DPDK ✓\n"
                                    f"• Device remains unbound (no driver)\n"
                                    f"• Interface will NOT appear in 'ip link show' until reboot\n\n"
                                    f"To reboot the server:\n"
                                    f"ssh root@<server> 'reboot'"
                                )
                            else:
                                QMessageBox.warning(
                                    self, 
                                    "Partially Successful", 
                                    f"Interface {display_name} unbound from DPDK successfully.\n\n"
                                    f"⚠️ Warning: Binding to kernel driver '{kernel_driver}' failed.\n\n"
                                    f"The device is no longer bound to DPDK (main goal achieved), but binding to "
                                    f"kernel driver failed due to a Broadcom firmware initialization issue.\n\n"
                                    f"📌 Important:\n"
                                    f"• The device remains unbound (no driver)\n"
                                    f"• Unbound devices do NOT appear in 'ip link show'\n"
                                    f"• The device will still appear in the unbind dialog (this is expected)\n"
                                    f"• You can try restoring it again later\n\n"
                                    f"To fix and make interface visible:\n"
                                    f"1. Reload driver: rmmod {kernel_driver} && modprobe {kernel_driver}\n"
                                    f"2. Then bind: echo {pci} > /sys/bus/pci/drivers/{kernel_driver}/bind\n"
                                    f"3. Reset PCI device (requires server access)\n"
                                    f"4. Reboot if issue persists"
                                )
                        else:
                            QMessageBox.information(
                                self, 
                                "Success", 
                                f"Interface {display_name} unbound from DPDK successfully.\n\n"
                                f"The interface should now appear in 'ip link show'."
                            )
                    
                    # Refresh DPDK status after successful unbind
                    # Also refresh the interface list to update the status
                    if hasattr(self, 'show_dpdk_status'):
                        QApplication.processEvents()  # Process any pending events
                        # Refresh will happen automatically when user checks status again
                    
                    # Note: The device may still appear in the unbind dialog if:
                    # 1. It was successfully unbound from DPDK but binding to kernel driver failed (firmware issue)
                    # 2. In this case, the device is still unbound and can be restored again
                    # This is expected behavior - the device is no longer DPDK-bound, which was the main goal
                else:
                    error_msg = result.get('message', 'Unknown error')
                    output = result.get('output', '')
                    full_msg = f"Failed to {'restore' if is_unbound else 'unbind'} interface: {error_msg}"
                    if output:
                        full_msg += f"\n\nOutput:\n{output}"
                    QMessageBox.warning(self, "Failed", full_msg)
            else:
                QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to unbind interface: {str(e)}")
    
    def _perform_load_modules(self, server_address, refresh_callback=None):
        """Perform VFIO module loading via API."""
        try:
            response = requests.post(f"{server_address}/api/dpdk/load_modules", timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    loaded = result.get('loaded', [])
                    QMessageBox.information(
                        self, 
                        "Success", 
                        f"VFIO modules loaded successfully:\n\n"
                        f"{', '.join(loaded)}\n\n"
                        f"You can now bind interfaces to DPDK."
                    )
                    # If callback provided, refresh the status
                    if refresh_callback:
                        refresh_callback()
                else:
                    # Show detailed error message
                    failed = result.get('failed', [])
                    loaded = result.get('loaded', [])
                    message = result.get('message', 'Unknown error')
                    
                    # Build detailed error message
                    error_details = []
                    for m in failed:
                        error_details.append(f"  • {m['module']}: {m.get('error', 'Unknown error')}")
                    
                    msg = f"{message}\n\n"
                    if error_details:
                        msg += "Module errors:\n" + "\n".join(error_details) + "\n\n"
                    if loaded:
                        msg += f"Successfully loaded: {', '.join(loaded)}\n\n"
                    msg += "Check server logs for more details:\n"
                    msg += f"  journalctl -u ostg-server -n 50"
                    
                    # Use a scrollable dialog for long error messages
                    error_dialog = QDialog(self)
                    error_dialog.setWindowTitle("Failed to Load VFIO Modules")
                    error_dialog.setGeometry(300, 300, 600, 400)
                    
                    layout = QVBoxLayout(error_dialog)
                    
                    error_text = QTextEdit()
                    error_text.setReadOnly(True)
                    error_text.setPlainText(msg)
                    error_text.setFontFamily("Courier")
                    error_text.setFontPointSize(10)
                    layout.addWidget(error_text)
                    
                    button_layout = QHBoxLayout()
                    button_layout.addStretch()
                    ok_button = QPushButton("OK")
                    ok_button.clicked.connect(error_dialog.accept)
                    button_layout.addWidget(ok_button)
                    layout.addLayout(button_layout)
                    
                    error_dialog.exec()
            else:
                # Try to parse error response
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message', response.text)
                except Exception:
                    error_msg = response.text
                
                QMessageBox.critical(
                    self, 
                    "Error", 
                    f"HTTP {response.status_code}\n\n{error_msg[:500]}"
                )
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error", f"Server is unreachable: {server_address}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error", f"Server request timed out: {server_address}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load modules: {str(e)}")
    
    def _perform_configure_hugepages(self, server_address, num_pages):
        """Perform hugepage configuration via API."""
        try:
            payload = {
                "num_pages": num_pages,
                "page_size": "2MB"
            }
            
            response = requests.post(f"{server_address}/api/dpdk/hugepages", json=payload, timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    QMessageBox.information(self, "Success", f"Hugepages configured successfully: {num_pages} x 2MB pages")
                else:
                    QMessageBox.warning(self, "Failed", f"Failed to configure hugepages: {result.get('message', 'Unknown error')}")
            else:
                QMessageBox.warning(self, "Error", f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to configure hugepages: {str(e)}")

