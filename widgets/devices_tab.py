#devices_tab.py#

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QLabel, QHBoxLayout,
    QPushButton, QTableWidgetItem, QGroupBox, QTextEdit, QSplitter,
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QTreeWidget,
    QStackedWidget, QComboBox, QCheckBox,QMessageBox,QWidget, QVBoxLayout, QTableWidget, QLabel, QHBoxLayout,
    QPushButton, QTableWidgetItem, QMessageBox, QInputDialog,QSpinBox,QApplication,
    QTabWidget, QListWidget, QListWidgetItem, QGridLayout, QSlider, QFrame,
    QScrollArea
)

from PyQt5.QtGui import QIcon, QPixmap, QFont, QPalette, QColor
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal, QThread
import os, json,logging,requests,ipaddress,uuid
import subprocess
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.qicon_loader import qicon,r_icon
from .add_device_dialog import AddDeviceDialog
from .add_bgp_dialog import AddBgpDialog
from .add_ospf_dialog import AddOspfDialog
from .add_isis_dialog import AddIsisDialog
from .add_bgp_route_dialog import ManageRoutePoolsDialog, AttachRoutePoolsDialog


class DeviceOperationWorker(QThread):
    """Background worker for device operations to prevent UI blocking."""
    
    # Signals for communication with main thread
    progress = pyqtSignal(str, str)  # (device_name, status_message)
    finished = pyqtSignal(list, int, int)  # (results, successful_count, failed_count)
    device_status_updated = pyqtSignal(int, str, str)  # (row, status, tooltip) - for updating UI
    
    def __init__(self, operation_type, devices_data, server_url, parent_tab):
        super().__init__()
        self.operation_type = operation_type  # 'start' or 'stop'
        self.devices_data = devices_data  # List of (row, device_name, device_info)
        self.server_url = server_url
        self.parent_tab = parent_tab
        self._should_stop = False
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Execute device operations in background thread."""
        results = []
        successful_count = 0
        failed_count = 0
        
        for row, device_name, device_info in self.devices_data:
            if self._should_stop:
                break
            try:
                if self.operation_type == 'start':
                    # Start device (light start - just bring up interface)
                    self.progress.emit(device_name, "Starting...")
                    
                    # Prepare start payload for light start
                    iface_label = device_info.get("Interface", "")
                    iface_norm = self.parent_tab._normalize_iface_label(iface_label)
                    vlan = device_info.get("VLAN", "0")
                    device_id = device_info.get("device_id", "")
                    
                    start_payload = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": iface_norm,
                        "vlan": vlan,
                        "ipv4": device_info.get("IPv4", ""),
                        "ipv6": device_info.get("IPv6", "")
                    }
                    
                    response = requests.post(
                        f"{self.server_url}/api/device/start",
                        json=start_payload,
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        device_info["Status"] = "Running"
                        
                        # Signal UI update
                        self.device_status_updated.emit(row, "Running", "Device Running")
                        
                        results.append(f"✅ {device_name}: Started successfully")
                        successful_count += 1
                    else:
                        results.append(f"❌ {device_name}: Server error {response.status_code}")
                        failed_count += 1
                        
                elif self.operation_type == 'stop':
                    # Stop device
                    self.progress.emit(device_name, "Stopping...")
                    
                    # Prepare stop payload
                    iface_label = device_info.get("Interface", "")
                    iface_norm = self.parent_tab._normalize_iface_label(iface_label)
                    vlan = device_info.get("VLAN", "0")
                    ipv4 = device_info.get("IPv4", "")
                    ipv6 = device_info.get("IPv6", "")
                    device_id = device_info.get("device_id", "")
                    
                    # Build protocols list from device_info
                    protocols = []
                    if "protocols" in device_info:
                        protocols_dict = device_info["protocols"]
                        if isinstance(protocols_dict, dict):
                            protocols = list(protocols_dict.keys())
                    elif "Protocols" in device_info and device_info["Protocols"]:
                        protocols = device_info["Protocols"].split(",") if isinstance(device_info["Protocols"], str) else []
                    
                    stop_payload = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": iface_norm,
                        "vlan": vlan,
                        "ipv4": ipv4,
                        "ipv6": ipv6,
                        "protocols": protocols
                    }
                    
                    response = requests.post(
                        f"{self.server_url}/api/device/stop",
                        json=stop_payload,
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        device_info["Status"] = "Stopped"
                        
                        # Signal UI update
                        self.device_status_updated.emit(row, "Stopped", "Device Stopped")
                        
                        results.append(f"✅ {device_name}: Stopped successfully")
                        successful_count += 1
                    else:
                        results.append(f"❌ {device_name}: Server error {response.status_code}")
                        failed_count += 1
                        
            except Exception as e:
                results.append(f"❌ {device_name}: Error - {str(e)}")
                failed_count += 1
                logging.error(f"[DEVICE OPERATION ERROR] {device_name}: {e}")
        
        # Emit final results
        self.finished.emit(results, successful_count, failed_count)


class ArpOperationWorker(QThread):
    """Background worker for ARP operations to prevent UI blocking when device is down."""
    
    progress = pyqtSignal(str, str)  # (device_name, status_message)
    finished = pyqtSignal(list, int, int)  # (results, successful_count, failed_count)
    device_status_updated = pyqtSignal(int, bool, str)  # (row, arp_resolved, status)
    arp_result = pyqtSignal(int, dict, str)  # (row, detailed_arp_results, operation_id) - for individual IP colors
    
    def __init__(self, devices_data, parent_tab):
        super().__init__()
        self.devices_data = devices_data  # List of (row, device_name, device_info)
        self.parent_tab = parent_tab
        self._should_stop = False
        import time
        self.operation_id = f"arp_{int(time.time() * 1000)}"  # Unique operation ID
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Execute ARP operations in background thread - PARALLEL processing."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        results = []
        successful_count = 0
        failed_count = 0
        
        def process_single_device(device_data):
            """Process a single device's ARP operation."""
            row, device_name, device_info = device_data
            
            if self._should_stop:
                return None
                
            try:
                # Send ARP request
                self.progress.emit(device_name, "Sending ARP request...")
                arp_success, arp_message = self.parent_tab.send_arp_request(device_info)
                
                # Re-check ARP resolution after sending request
                self.progress.emit(device_name, "Checking ARP resolution...")
                arp_results = self.parent_tab._check_individual_arp_resolution(device_info)
                
                # Emit detailed ARP results for individual IP color updates
                self.arp_result.emit(row, arp_results, self.operation_id)
                
                # Consider successful if any IP (IPv4, IPv6, or Gateway) resolves
                if arp_results.get("overall_resolved", False):
                    result = f"✅ {device_name}: ARP resolved - {arp_results.get('overall_status', 'Unknown')}"
                    self.device_status_updated.emit(row, True, arp_results.get('overall_status', 'Unknown'))
                    return (result, True, row, arp_results)
                else:
                    result = f"❌ {device_name}: ARP failed - {arp_results.get('overall_status', 'Unknown')}"
                    self.device_status_updated.emit(row, False, arp_results.get('overall_status', 'Unknown'))
                    return (result, False, row, arp_results)
                    
            except Exception as e:
                result = f"❌ {device_name}: Error - {str(e)}"
                self.device_status_updated.emit(row, False, f"Error: {str(e)}")
                logging.error(f"[ARP OPERATION ERROR] {device_name}: {e}")
                return (result, False, row, None)
        
        # Process devices in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(self.devices_data), 5)) as executor:
            # Submit all device processing tasks
            future_to_device = {
                executor.submit(process_single_device, device_data): device_data 
                for device_data in self.devices_data
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_device):
                if self._should_stop:
                    break
                    
                result = future.result()
                if result:
                    result_text, success, row, arp_results = result
                    results.append(result_text)
                    if success:
                        successful_count += 1
                else:
                    failed_count += 1
                    
        # Emit final results
        self.finished.emit(results, successful_count, failed_count)


class ArpCheckWorker(QThread):
    """Background worker for ARP resolution checks to prevent UI blocking."""
    
    # Signals for communication with main thread
    arp_result = pyqtSignal(int, bool, str)  # (row, resolved, status_message)
    finished = pyqtSignal()  # All checks completed
    
    def __init__(self, devices_to_check, parent_tab):
        super().__init__()
        self.devices_to_check = devices_to_check  # List of (row, device_info)
        self.parent_tab = parent_tab
        self._should_stop = False
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Execute ARP resolution checks in background thread."""
        for row, device_info in self.devices_to_check:
            if self._should_stop:
                break
                
            try:
                # Call the synchronous ARP check method
                resolved, status = self.parent_tab._check_arp_resolution_sync(device_info)
                # Emit result to main thread
                self.arp_result.emit(row, resolved, status)
            except Exception as e:
                # Emit error result
                self.arp_result.emit(row, False, f"ARP check error: {str(e)}")
        
        # Signal completion
        self.finished.emit()


class DatabaseQueryWorker(QThread):
    """Background worker for database queries and other blocking operations."""
    
    # Signals for communication with main thread
    query_result = pyqtSignal(str, dict)  # (operation_type, result_data)
    query_error = pyqtSignal(str, str)  # (operation_type, error_message)
    finished = pyqtSignal(str)  # (operation_type)
    
    def __init__(self, operation_type, query_data, parent_tab):
        super().__init__()
        self.operation_type = operation_type  # 'device_apply', 'database_query', 'session_load', etc.
        self.query_data = query_data  # Data needed for the operation
        self.parent_tab = parent_tab
        self._should_stop = False
    
    def run(self):
        """Execute the database query or blocking operation in background thread."""
        try:
            if self.operation_type == "device_apply":
                self._handle_device_apply()
            elif self.operation_type == "database_query":
                self._handle_database_query()
            elif self.operation_type == "session_load":
                self._handle_session_load()
            else:
                self.query_error.emit(self.operation_type, f"Unknown operation type: {self.operation_type}")
        except Exception as e:
            self.query_error.emit(self.operation_type, f"Operation failed: {str(e)}")
        finally:
            self.finished.emit(self.operation_type)


class MultiDeviceApplyWorker(QThread):
    """Background worker for applying multiple devices to prevent UI blocking."""
    
    # Signals for communication with main thread
    device_applied = pyqtSignal(str, bool, str)  # (device_name, success, message)
    progress = pyqtSignal(str, str)  # (device_name, status_message)
    finished = pyqtSignal(list, int, int)  # (results, successful_count, failed_count)
    
    def __init__(self, devices_to_apply, server_url, parent_tab):
        super().__init__()
        self.devices_to_apply = devices_to_apply  # List of (row, device_info) tuples
        self.server_url = server_url
        self.parent_tab = parent_tab
        self._should_stop = False
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Apply multiple devices in background thread."""
        results = []
        successful_count = 0
        failed_count = 0
        
        for row, device_info in self.devices_to_apply:
            if self._should_stop:
                break
                
            device_name = device_info.get("Device Name", "Unknown")
            self.progress.emit(device_name, "Applying...")
            
            try:
                # Apply device to server
                success = self.parent_tab._apply_device_to_server_sync(self.server_url, device_info)
                
                if success:
                    # Mark device as applied
                    device_info["_is_new"] = False
                    device_info["_needs_apply"] = False
                    device_info["Status"] = "Running"
                    
                    # Protocol configuration is now handled in _apply_device_to_server_sync
                    # No need for duplicate calls here
                    
                    message = f"✅ {device_name}: Device applied successfully"
                    results.append(message)
                    successful_count += 1
                    self.device_applied.emit(device_name, True, message)
                else:
                    message = f"❌ {device_name}: Failed to apply to server"
                    results.append(message)
                failed_count += 1
                self.device_applied.emit(device_name, False, message)
                    
            except Exception as e:
                message = f"❌ {device_name}: Error - {str(e)}"
                results.append(message)
                failed_count += 1
                self.device_applied.emit(device_name, False, message)
                print(f"[MULTI DEVICE APPLY ERROR] {device_name}: {e}")
        
        # Emit final results
        self.finished.emit(results, successful_count, failed_count)
    
    def _handle_device_apply(self):
        """Handle device apply operation in background."""
        import requests
        
        server_url = self.query_data.get("server_url")
        payload = self.query_data.get("payload")
        device_name = self.query_data.get("device_name", "Unknown")
        
        if self._should_stop:
            return
            
        try:
            # Reduced timeout for faster failure detection
            response = requests.post(f"{server_url}/api/device/apply", json=payload, timeout=15)
            
            if response.status_code == 200:
                result_data = {
                    "success": True,
                    "device_name": device_name,
                    "response": response.json()
                }
                self.query_result.emit(self.operation_type, result_data)
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                self.query_error.emit(self.operation_type, f"Device apply failed for {device_name}: {error_msg}")
                
        except requests.exceptions.Timeout:
            self.query_error.emit(self.operation_type, f"Device apply timeout for {device_name}")
        except Exception as e:
            self.query_error.emit(self.operation_type, f"Device apply error for {device_name}: {str(e)}")
    
    def _handle_database_query(self):
        """Handle database query operation in background."""
        import requests
        
        server_url = self.query_data.get("server_url")
        device_id = self.query_data.get("device_id")
        
        if self._should_stop:
            return
            
        try:
            # Reduced timeout for database queries
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=3)
            
            if response.status_code == 200:
                result_data = {
                    "success": True,
                    "device_id": device_id,
                    "data": response.json()
                }
                self.query_result.emit(self.operation_type, result_data)
            else:
                error_msg = f"HTTP {response.status_code}"
                self.query_error.emit(self.operation_type, f"Database query failed for {device_id}: {error_msg}")
                
        except requests.exceptions.Timeout:
            self.query_error.emit(self.operation_type, f"Database query timeout for {device_id}")
        except Exception as e:
            self.query_error.emit(self.operation_type, f"Database query error for {device_id}: {str(e)}")
    
    def _handle_session_load(self):
        """Handle session loading operations in background."""
        import requests
        
        server_data = self.query_data.get("servers", [])
        
        if self._should_stop:
            return
            
        results = []
        for server in server_data:
            if self._should_stop:
                break
                
            try:
                address = server.get("address")
                # Reduced timeout for server checks
                response = requests.get(f"{address}/api/interfaces", timeout=3)
                
                if response.status_code == 200:
                    server["online"] = True
                    server["interfaces"] = response.json()
                    results.append({"server": server, "success": True})
                else:
                    server["online"] = False
                    results.append({"server": server, "success": False, "error": f"HTTP {response.status_code}"})
                    
            except Exception as e:
                server["online"] = False
                results.append({"server": server, "success": False, "error": str(e)})
        
        result_data = {
            "success": True,
            "servers": results
        }
        self.query_result.emit(self.operation_type, result_data)


class IndividualArpCheckWorker(QThread):
    """Background worker for individual IP ARP resolution checks."""
    
    # Signals for communication with main thread
    arp_result = pyqtSignal(int, dict)  # (row, detailed_arp_results)
    finished = pyqtSignal()  # All checks completed
    
    def __init__(self, devices_to_check, parent_tab):
        super().__init__()
        self.devices_to_check = devices_to_check  # List of (row, device_info)
        self.parent_tab = parent_tab
        self._should_stop = False
    
    def stop(self):
        """Request the worker to stop gracefully."""
        self._should_stop = True
    
    def run(self):
        """Execute individual ARP resolution checks in background thread."""
        for row, device_info in self.devices_to_check:
            if self._should_stop:
                break
                
            try:
                # Call the individual ARP check method
                arp_results = self.parent_tab._check_individual_arp_resolution(device_info)
                # Emit result to main thread
                self.arp_result.emit(row, arp_results)
            except Exception as e:
                # Emit error result
                error_results = {
                    "overall_resolved": False,
                    "overall_status": f"ARP check error: {str(e)}",
                    "ipv4_resolved": False,
                    "ipv6_resolved": False,
                    "gateway_resolved": False
                }
                self.arp_result.emit(row, error_results)
        
        # Signal completion
        self.finished.emit()


class MultiDeviceResultsDialog(QDialog):
    """Custom dialog for displaying results of multi-device operations with scrollable content."""
    
    def __init__(self, title, summary, results, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(600, 400)
        
        # Main layout
        layout = QVBoxLayout(self)
        
        # Summary section
        summary_label = QLabel(summary)
        summary_label.setStyleSheet("font-weight: bold; font-size: 14px; margin: 10px;")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)
        
        # Scrollable results section
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Content widget for scroll area
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # Add results with proper formatting
        for result in results:
            result_label = QLabel(result)
            result_label.setWordWrap(True)
            result_label.setMargin(5)
            
            # Color code based on result type
            if result.startswith("✅"):
                result_label.setStyleSheet("color: green; font-weight: bold;")
            elif result.startswith("❌"):
                result_label.setStyleSheet("color: red; font-weight: bold;")
            elif result.startswith("⚠️"):
                result_label.setStyleSheet("color: orange; font-weight: bold;")
            elif result.startswith("ℹ️"):
                result_label.setStyleSheet("color: blue; font-weight: bold;")
            elif result.startswith("⏱️"):
                result_label.setStyleSheet("color: purple; font-weight: bold;")
            else:
                result_label.setStyleSheet("color: black;")
            
            content_layout.addWidget(result_label)
        
        # Add stretch to push content to top
        content_layout.addStretch()
        
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        
        # Close button
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)


from .add_isis_dialog import AddIsisDialog


class BgpRouteManagementDialog(QDialog):
    """Dialog for managing BGP routes for a device."""
    
    def __init__(self, parent=None, device_id="", server_url=""):
        super().__init__(parent)
        self.device_id = device_id
        self.server_url = server_url
        self.setWindowTitle(f"BGP Route Management - {device_id}")
        self.setFixedSize(800, 600)
        
        self.setup_ui()
        self.load_existing_routes()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Create tab widget
        self.tab_widget = QTabWidget()
        
        # Tab 1: Route Advertisement
        self.advertise_tab = self.create_advertise_tab()
        self.tab_widget.addTab(self.advertise_tab, "Advertise Routes")
        
        # Tab 2: Route Management
        self.manage_tab = self.create_manage_tab()
        self.tab_widget.addTab(self.manage_tab, "Manage Routes")
        
        # Tab 3: Statistics
        self.stats_tab = self.create_stats_tab()
        self.tab_widget.addTab(self.stats_tab, "Statistics")
        
        layout.addWidget(self.tab_widget)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_data)
        
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)
        
        layout.addLayout(button_layout)
    
    def create_advertise_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Route Configuration Group
        config_group = QGroupBox("Route Configuration")
        config_layout = QFormLayout(config_group)
        
        # Prefixes
        self.prefixes_input = QTextEdit()
        self.prefixes_input.setPlaceholderText("Enter prefixes (one per line):\n10.0.1.0/24\n10.0.2.0/24\n192.168.1.0/24")
        self.prefixes_input.setMaximumHeight(100)
        config_layout.addRow("Prefixes:", self.prefixes_input)
        
        # AS Path
        self.as_path_input = QLineEdit("65000 65001")
        self.as_path_input.setPlaceholderText("e.g., 65000 65001 65002")
        config_layout.addRow("AS Path:", self.as_path_input)
        
        # MED
        self.med_input = QSpinBox()
        self.med_input.setRange(0, 4294967295)
        self.med_input.setValue(0)
        config_layout.addRow("MED:", self.med_input)
        
        # Local Preference
        self.local_pref_input = QSpinBox()
        self.local_pref_input.setRange(0, 4294967295)
        self.local_pref_input.setValue(100)
        config_layout.addRow("Local Preference:", self.local_pref_input)
        
        # Origin
        self.origin_combo = QComboBox()
        self.origin_combo.addItems(["IGP", "EGP", "INCOMPLETE"])
        config_layout.addRow("Origin:", self.origin_combo)
        
        # Communities
        self.communities_input = QLineEdit("65000:100 65000:200")
        self.communities_input.setPlaceholderText("e.g., 65000:100 65000:200")
        config_layout.addRow("Communities:", self.communities_input)
        
        layout.addWidget(config_group)
        
        # Quick Generation Group
        quick_group = QGroupBox("Quick Route Generation")
        quick_layout = QFormLayout(quick_group)
        
        self.base_prefix_input = QLineEdit("10.0.0.0/8")
        quick_layout.addRow("Base Prefix:", self.base_prefix_input)
        
        self.route_count_input = QSpinBox()
        self.route_count_input.setRange(1, 1000)
        self.route_count_input.setValue(10)
        quick_layout.addRow("Route Count:", self.route_count_input)
        
        self.generate_button = QPushButton("Generate & Advertise Test Routes")
        self.generate_button.clicked.connect(self.generate_test_routes)
        quick_layout.addRow("", self.generate_button)
        
        layout.addWidget(quick_group)
        
        # Action Buttons
        button_layout = QHBoxLayout()
        self.advertise_button = QPushButton("Advertise Routes")
        self.advertise_button.clicked.connect(self.advertise_routes)
        self.advertise_button.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; }")
        
        button_layout.addWidget(self.advertise_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        layout.addStretch()
        
        return widget
    
    def create_manage_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Current Routes Group
        routes_group = QGroupBox("Current Advertised Routes")
        routes_layout = QVBoxLayout(routes_group)
        
        self.routes_list = QListWidget()
        self.routes_list.setSelectionMode(QListWidget.MultiSelection)
        routes_layout.addWidget(self.routes_list)
        
        # Route Actions
        actions_layout = QHBoxLayout()
        self.withdraw_selected_button = QPushButton("Withdraw Selected")
        self.withdraw_selected_button.clicked.connect(self.withdraw_selected_routes)
        self.withdraw_selected_button.setStyleSheet("QPushButton { background-color: #f44336; color: white; }")
        
        self.withdraw_all_button = QPushButton("Withdraw All")
        self.withdraw_all_button.clicked.connect(self.withdraw_all_routes)
        self.withdraw_all_button.setStyleSheet("QPushButton { background-color: #ff9800; color: white; }")
        
        actions_layout.addWidget(self.withdraw_selected_button)
        actions_layout.addWidget(self.withdraw_all_button)
        actions_layout.addStretch()
        
        routes_layout.addLayout(actions_layout)
        layout.addWidget(routes_group)
        
        return widget
    
    def create_stats_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # BGP Statistics Group
        stats_group = QGroupBox("BGP Statistics")
        stats_layout = QVBoxLayout(stats_group)
        
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(300)
        stats_layout.addWidget(self.stats_text)
        
        # Refresh Statistics Button
        self.refresh_stats_button = QPushButton("Refresh Statistics")
        self.refresh_stats_button.clicked.connect(self.refresh_statistics)
        stats_layout.addWidget(self.refresh_stats_button)
        
        layout.addWidget(stats_group)
        layout.addStretch()
        
        return widget
    
    def load_existing_routes(self):
        """Load existing routes for the device."""
        try:
            response = requests.get(f"{self.server_url}/api/bgp/routes", 
                                 params={"device_id": self.device_id}, timeout=5)
            if response.status_code == 200:
                data = response.json()
                routes = data.get("routes", [])
                self.routes_list.clear()
                for route in routes:
                    self.routes_list.addItem(route)
            else:
                logging.error(f"Failed to load routes: {response.text}")
        except Exception as e:
            logging.error(f"Error loading routes: {e}")
    
    def advertise_routes(self):
        """Advertise routes with the configured parameters."""
        prefixes_text = self.prefixes_input.toPlainText().strip()
        if not prefixes_text:
            QMessageBox.warning(self, "Warning", "Please enter at least one prefix to advertise.")
            return
        
        prefixes = [p.strip() for p in prefixes_text.split('\n') if p.strip()]
        
        # Parse AS path
        as_path = []
        as_path_text = self.as_path_input.text().strip()
        if as_path_text:
            try:
                as_path = [int(x.strip()) for x in as_path_text.split()]
            except ValueError:
                QMessageBox.warning(self, "Warning", "Invalid AS path format. Use space-separated AS numbers.")
                return
        
        # Parse communities
        communities = []
        communities_text = self.communities_input.text().strip()
        if communities_text:
            communities = [c.strip() for c in communities_text.split() if c.strip()]
        
        route_config = {
            "prefixes": prefixes,
            "as_path": as_path,
            "med": self.med_input.value(),
            "local_pref": self.local_pref_input.value(),
            "origin": self.origin_combo.currentText(),
            "communities": communities
        }
        
        try:
            response = requests.post(f"{self.server_url}/api/bgp/routes/advertise",
                                   json={
                                       "device_id": self.device_id,
                                       "route_config": route_config
                                   }, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                QMessageBox.information(self, "Success", 
                                      f"Successfully advertised {data.get('total_routes', 0)} routes.")
                self.load_existing_routes()
            else:
                error_data = response.json()
                QMessageBox.critical(self, "Error", f"Failed to advertise routes: {error_data.get('error', 'Unknown error')}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error advertising routes: {e}")
    
    def generate_test_routes(self):
        """Generate and advertise test routes."""
        base_prefix = self.base_prefix_input.text().strip()
        route_count = self.route_count_input.value()
        
        if not base_prefix:
            QMessageBox.warning(self, "Warning", "Please enter a base prefix.")
            return
        
        try:
            response = requests.post(f"{self.server_url}/api/bgp/routes/generate",
                                   json={
                                       "device_id": self.device_id,
                                       "route_count": route_count,
                                       "base_prefix": base_prefix
                                   }, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                QMessageBox.information(self, "Success", 
                                      f"Successfully generated and advertised {data.get('total_routes', 0)} test routes.")
                self.load_existing_routes()
            else:
                error_data = response.json()
                QMessageBox.critical(self, "Error", f"Failed to generate routes: {error_data.get('error', 'Unknown error')}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error generating routes: {e}")
    
    def withdraw_selected_routes(self):
        """Withdraw selected routes."""
        selected_items = self.routes_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "Please select routes to withdraw.")
            return
        
        prefixes = [item.text() for item in selected_items]
        self.withdraw_routes(prefixes)
    
    def withdraw_all_routes(self):
        """Withdraw all routes."""
        if self.routes_list.count() == 0:
            QMessageBox.warning(self, "Warning", "No routes to withdraw.")
            return
        
        reply = QMessageBox.question(self, "Confirm", 
                                   f"Are you sure you want to withdraw all {self.routes_list.count()} routes?",
                                   QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.withdraw_routes()
    
    def withdraw_routes(self, prefixes=None):
        """Withdraw specified routes or all routes."""
        try:
            payload = {"device_id": self.device_id}
            if prefixes:
                payload["prefixes"] = prefixes
            
            response = requests.post(f"{self.server_url}/api/bgp/routes/withdraw",
                                   json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                QMessageBox.information(self, "Success", 
                                      f"Successfully withdrew {data.get('total_withdrawn', 0)} routes.")
                self.load_existing_routes()
            else:
                error_data = response.json()
                QMessageBox.critical(self, "Error", f"Failed to withdraw routes: {error_data.get('error', 'Unknown error')}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error withdrawing routes: {e}")
    
    def refresh_statistics(self):
        """Refresh BGP statistics."""
        try:
            response = requests.get(f"{self.server_url}/api/bgp/statistics", timeout=5)
            if response.status_code == 200:
                data = response.json()
                
                # Format statistics for display
                stats_text = f"BGP Router ID: {data.get('router_id', 'Unknown')}\n"
                stats_text += f"Total Routes: {data.get('total_routes', 0)}\n\n"
                
                stats_text += "Neighbors:\n"
                for neighbor in data.get('neighbors', []):
                    stats_text += f"  {neighbor.get('neighbor', 'Unknown')} (AS {neighbor.get('as', 'Unknown')}) - "
                    stats_text += f"State: {neighbor.get('state', 'Unknown')}, "
                    stats_text += f"Prefixes: {neighbor.get('prefixes', '0')}\n"
                
                stats_text += "\nAdvertised Routes by Device:\n"
                for device_id, routes in data.get('advertised_routes', {}).items():
                    stats_text += f"  {device_id}: {len(routes)} routes\n"
                
                self.stats_text.setText(stats_text)
            else:
                self.stats_text.setText(f"Error loading statistics: {response.text}")
                
        except Exception as e:
            self.stats_text.setText(f"Error loading statistics: {e}")
    
    def refresh_data(self):
        """Refresh all data."""
        self.load_existing_routes()
        self.refresh_statistics()

# AddDeviceDialog is now imported from add_device_dialog.py

    # ---------- Page builders ----------

    def init_device_name(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        self.device_name_input.setPlaceholderText("e.g., Device1")
        layout.addRow("Device Name:", self.device_name_input)
        self.stack.addWidget(widget)
        return widget

    def init_protocol_selection(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Select Protocols:"))
        self.protocol_ospf = QCheckBox("OSPF")
        self.protocol_bgp = QCheckBox("BGP")
        self.protocol_isis = QCheckBox("IS-IS")
        layout.addWidget(self.protocol_ospf)
        layout.addWidget(self.protocol_bgp)
        layout.addWidget(self.protocol_isis)
        self.stack.addWidget(widget)
        return widget

    def init_ip_version_selection(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Select IP Version:"))

        self.ipv4_checkbox = QCheckBox("IPv4")
        self.ipv6_checkbox = QCheckBox("IPv6")
        self.ipv4_checkbox.setChecked(True)

        layout.addWidget(self.ipv4_checkbox)
        layout.addWidget(self.ipv6_checkbox)
        self.stack.addWidget(widget)

        # Toggle inputs when (un)checking
        self.ipv4_checkbox.stateChanged.connect(self._toggle_ip_fields)
        self.ipv6_checkbox.stateChanged.connect(self._toggle_ip_fields)
        return widget

    def init_mac_ip_config(self):
        # Lazy import so you don't have to change your global imports
        from PyQt5.QtGui import QIntValidator, QRegExpValidator
        from PyQt5.QtCore import QRegExp

        widget = QWidget()
        layout = QFormLayout(widget)

        self.iface_input = QLineEdit()
        self.iface_input.setText(self.default_iface)
        self.iface_input.setPlaceholderText("TG X - Port: <iface>")

        self.mac_input = QLineEdit("00:11:22:33:44:55")
        self.mac_input.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        mac_re = QRegExp(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
        self.mac_input.setValidator(QRegExpValidator(mac_re, self))

        self.ipv4_input = QLineEdit("192.168.0.2")
        self.ipv4_input.setPlaceholderText("e.g., 192.168.0.2")
        self.ipv4_mask_input = QLineEdit("24")
        self.ipv4_mask_input.setValidator(QIntValidator(0, 32, self))

        self.ipv6_input = QLineEdit("2001:db8::2")
        self.ipv6_input.setPlaceholderText("e.g., 2001:db8::2")
        self.ipv6_mask_input = QLineEdit("64")
        self.ipv6_mask_input.setValidator(QIntValidator(0, 128, self))

        self.vlan_input = QLineEdit("0")
        self.vlan_input.setValidator(QIntValidator(0, 4094, self))

        layout.addRow("Interface:", self.iface_input)
        layout.addRow("MAC Address:", self.mac_input)
        layout.addRow("IPv4 Address:", self.ipv4_input)
        layout.addRow("IPv4 Mask:", self.ipv4_mask_input)
        layout.addRow("IPv6 Address:", self.ipv6_input)
        layout.addRow("IPv6 Mask:", self.ipv6_mask_input)
        layout.addRow("VLAN ID:", self.vlan_input)

        self.increment_checkbox_mac = QCheckBox("Increment MAC")
        self.increment_checkbox_ipv4 = QCheckBox("Increment IPv4")
        self.increment_checkbox_ipv6 = QCheckBox("Increment IPv6")
        self.increment_checkbox_vlan = QCheckBox("Increment VLAN")

        self.increment_count = QSpinBox()
        self.increment_count.setMinimum(1)
        self.increment_count.setMaximum(10000)
        self.increment_count.setValue(1)
        self.increment_count.setEnabled(False)

        def toggle_count_box():
            any_checked = (
                self.increment_checkbox_mac.isChecked()
                or self.increment_checkbox_ipv4.isChecked()
                or self.increment_checkbox_ipv6.isChecked()
                or self.increment_checkbox_vlan.isChecked()
            )
            self.increment_count.setEnabled(any_checked)

        self.increment_checkbox_mac.stateChanged.connect(toggle_count_box)
        self.increment_checkbox_ipv4.stateChanged.connect(toggle_count_box)
        self.increment_checkbox_ipv6.stateChanged.connect(toggle_count_box)
        self.increment_checkbox_vlan.stateChanged.connect(toggle_count_box)

        layout.addRow(self.increment_checkbox_mac)
        layout.addRow(self.increment_checkbox_ipv4)
        layout.addRow(self.increment_checkbox_ipv6)
        layout.addRow(self.increment_checkbox_vlan)
        layout.addRow("Device Count:", self.increment_count)

        self.stack.addWidget(widget)

        # Initialize fields enable state (based on IP version checkboxes)
        QTimer.singleShot(0, self._toggle_ip_fields)
        return widget

    def build_ospf_config_page(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        self.area_id_input = QLineEdit("0.0.0.0")
        self.graceful_restart_checkbox = QCheckBox("Enable Graceful Restart")

        layout.addRow("Area ID:", self.area_id_input)
        layout.addRow("Graceful Restart:", self.graceful_restart_checkbox)

        return widget

    def build_bgp_config_page(self):
        widget = QWidget()
        layout = QFormLayout(widget)

        self.bgp_mode_combo = QComboBox()
        self.bgp_mode_combo.addItems(["eBGP", "iBGP"])

        self.bgp_asn_input = QLineEdit("65000")
        
        # IPv4 BGP fields
        self.bgp_neighbor_ipv4_input = QLineEdit("192.168.0.2")
        self.bgp_remote_asn_input = QLineEdit("65001")
        self.bgp_update_source_ipv4_input = QLineEdit("192.168.0.2")
        
        # IPv6 BGP fields
        self.bgp_neighbor_ipv6_input = QLineEdit("2001:db8::2")
        self.bgp_update_source_ipv6_input = QLineEdit("2001:db8::1")

        layout.addRow("BGP Mode:", self.bgp_mode_combo)
        layout.addRow("Local ASN:", self.bgp_asn_input)
        layout.addRow("Remote ASN:", self.bgp_remote_asn_input)
        
        # IPv4 section
        layout.addRow(QLabel("IPv4 BGP Configuration:"))
        layout.addRow("IPv4 Neighbor IP:", self.bgp_neighbor_ipv4_input)
        layout.addRow("IPv4 Source IP:", self.bgp_update_source_ipv4_input)
        
        # IPv6 section
        layout.addRow(QLabel("IPv6 BGP Configuration:"))
        layout.addRow("IPv6 Neighbor IP:", self.bgp_neighbor_ipv6_input)
        layout.addRow("IPv6 Source IP:", self.bgp_update_source_ipv6_input)

        return widget

    # ---------- Navigation & validation ----------

    def insert_protocol_specific_pages(self):
        # Remove previously inserted protocol pages
        for i in reversed(range(self.stack.count())):
            w = self.stack.widget(i)
            if getattr(w, "_is_protocol_page", False):
                self.stack.removeWidget(w)

        insert_index = 4  # After MAC/IP config page

        if self.protocol_ospf.isChecked():
            if not self.ospf_page:
                self.ospf_page = self.build_ospf_config_page()
                self.ospf_page._is_protocol_page = True
            self.stack.insertWidget(insert_index, self.ospf_page)
            insert_index += 1

        if self.protocol_bgp.isChecked():
            if not self.bgp_page:
                self.bgp_page = self.build_bgp_config_page()
                self.bgp_page._is_protocol_page = True
            self.stack.insertWidget(insert_index, self.bgp_page)

    def _on_next_clicked(self):
        # When leaving the protocol selection page, inject protocol-specific pages
        if self.current_index == 1:
            self.insert_protocol_specific_pages()

        # Validate the page we are leaving (current page)
        if not self._validate_current_page():
            return

        if self.current_index < self.stack.count() - 1:
            self.current_index += 1
            self.stack.setCurrentIndex(self.current_index)
            self.back_button.setEnabled(True)
            if self.current_index == self.stack.count() - 1:
                self.next_button.setText("Finish")
        else:
            # Final validation (BGP/OSPF if present)
            if not self._validate_final():
                return
            self.accept()

    def prev_page(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.stack.setCurrentIndex(self.current_index)
            self.next_button.setText("Next")
            self.back_button.setEnabled(self.current_index > 0)

    def _toggle_ip_fields(self):
        en4 = self.ipv4_checkbox.isChecked()
        en6 = self.ipv6_checkbox.isChecked()
        self.ipv4_input.setEnabled(en4)
        self.ipv4_mask_input.setEnabled(en4)
        self.ipv6_input.setEnabled(en6)
        self.ipv6_mask_input.setEnabled(en6)

    def _validate_current_page(self) -> bool:
        """
        Validate inputs for the page we are on *before* moving forward.
        Only enforces what's visible/selected.
        """
        page = self.stack.currentWidget()

        # Device name page
        if page is self.device_name_widget:
            name = self.device_name_input.text().strip()
            if not name:
                QMessageBox.warning(self, "Missing Name", "Please enter a device name.")
                return False
            return True

        # IP version selection: nothing to validate (both can be off)
        if page is self.ipver_widget:
            return True

        # MAC/IP config page
        if page is self.mac_ip_widget:
            iface = self.iface_input.text().strip()
            if not iface:
                QMessageBox.warning(self, "Missing Interface", "Please provide an interface.")
                return False

            mac = self.mac_input.text().strip()
            if not mac or ":" not in mac or len(mac) != 17:
                QMessageBox.warning(self, "Invalid MAC", "Please enter a MAC in AA:BB:CC:DD:EE:FF format.")
                return False

            try:
                vlan = int(self.vlan_input.text() or "0")
                if not (0 <= vlan <= 4094):
                    raise ValueError
            except Exception:
                QMessageBox.warning(self, "Invalid VLAN", "VLAN must be an integer between 0 and 4094.")
                return False

            import ipaddress

            if self.ipv4_checkbox.isChecked():
                ip4 = self.ipv4_input.text().strip()
                m4 = int(self.ipv4_mask_input.text() or "24")
                try:
                    ipaddress.IPv4Address(ip4)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv4", "Please provide a valid IPv4 address.")
                    return False
                if not (0 <= m4 <= 32):
                    QMessageBox.warning(self, "Invalid IPv4 Mask", "Mask must be 0–32.")
                    return False

            if self.ipv6_checkbox.isChecked():
                ip6 = self.ipv6_input.text().strip()
                m6 = int(self.ipv6_mask_input.text() or "64")
                try:
                    ipaddress.IPv6Address(ip6)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv6", "Please provide a valid IPv6 address.")
                    return False
                if not (0 <= m6 <= 128):
                    QMessageBox.warning(self, "Invalid IPv6 Mask", "Mask must be 0–128.")
                    return False

            return True

        # OSPF/BGP pages don’t need to be strict here; final check below
        return True

    def _validate_final(self) -> bool:
        """Extra checks when finishing (only if relevant pages exist)."""
        # If BGP selected, make sure ASNs and neighbor look sane
        if self.protocol_bgp.isChecked() and self.bgp_page:
            try:
                asn_local = int(self.bgp_asn_input.text())
                asn_remote = int(self.bgp_remote_asn_input.text())
                if asn_local <= 0 or asn_remote <= 0:
                    raise ValueError
            except Exception:
                QMessageBox.warning(self, "Invalid BGP ASN", "Local and Remote ASN must be positive integers.")
                return False

            import ipaddress
            
            # Validate IPv4 BGP fields if provided
            neigh_ipv4 = self.bgp_neighbor_ipv4_input.text().strip()
            if neigh_ipv4:
                try:
                    ipaddress.IPv4Address(neigh_ipv4)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv4 Neighbor IP", "Please provide a valid IPv4 neighbor address.")
                    return False

            src_ipv4 = self.bgp_update_source_ipv4_input.text().strip()
            if src_ipv4:
                try:
                    ipaddress.IPv4Address(src_ipv4)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv4 Source IP", "IPv4 BGP Source IP must be a valid IPv4 address.")
                    return False

            # Validate IPv6 BGP fields if provided
            neigh_ipv6 = self.bgp_neighbor_ipv6_input.text().strip()
            if neigh_ipv6:
                try:
                    ipaddress.IPv6Address(neigh_ipv6)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv6 Neighbor IP", "Please provide a valid IPv6 neighbor address.")
                    return False

            src_ipv6 = self.bgp_update_source_ipv6_input.text().strip()
            if src_ipv6:
                try:
                    ipaddress.IPv6Address(src_ipv6)
                except Exception:
                    QMessageBox.warning(self, "Invalid IPv6 Source IP", "IPv6 BGP Source IP must be a valid IPv6 address.")
                    return False

            # At least one neighbor IP should be provided for BGP
            if not neigh_ipv4 and not neigh_ipv6:
                QMessageBox.warning(self, "Missing BGP Neighbor IP", "Please provide at least one BGP Neighbor IP (IPv4 or IPv6).")
                return False

        return True

    # ---------- Data extraction ----------

    def get_values(self):
        protocols = []
        if self.protocol_ospf.isChecked():
            protocols.append("OSPF")
        if self.protocol_bgp.isChecked():
            protocols.append("BGP")
        if self.protocol_isis.isChecked():
            protocols.append("IS-IS")

        ipv4 = ""
        if self.ipv4_checkbox.isChecked():
            ipv4 = self.ipv4_input.text().strip() or "192.168.0.2"

        ipv6 = ""
        if self.ipv6_checkbox.isChecked():
            ipv6 = self.ipv6_input.text().strip() or "2001:db8::2"

        return (
            self.device_name_input.text().strip(),
            self.iface_input.text(),
            self.mac_input.text().strip(),
            ipv4,
            ipv6,
            protocols,
            self.area_id_input.text() if hasattr(self, "area_id_input") else "",
            self.graceful_restart_checkbox.isChecked() if hasattr(self, "graceful_restart_checkbox") else False,
            self.bgp_mode_combo.currentText() if hasattr(self, "bgp_mode_combo") else "",
            self.bgp_asn_input.text().strip() if hasattr(self, "bgp_asn_input") else "",
            self.bgp_neighbor_ipv4_input.text().strip() if hasattr(self, "bgp_neighbor_ipv4_input") else "",
            self.bgp_remote_asn_input.text().strip() if hasattr(self, "bgp_remote_asn_input") else "",
            self.vlan_input.text().strip(),
            self.increment_checkbox_mac.isChecked(),
            self.increment_checkbox_ipv4.isChecked(),
            self.increment_checkbox_ipv6.isChecked(),
            self.increment_checkbox_vlan.isChecked(),
            self.increment_count.value(),
            self.bgp_update_source_ipv4_input.text().strip() if hasattr(self, "bgp_update_source_ipv4_input") else "",
            self.bgp_neighbor_ipv6_input.text().strip() if hasattr(self, "bgp_neighbor_ipv6_input") else "",
            self.bgp_update_source_ipv6_input.text().strip() if hasattr(self, "bgp_update_source_ipv6_input") else "",
            self.ipv4_mask_input.text().strip(),
            self.ipv6_mask_input.text().strip(),
        )

class StatusCache:
    """Simple LRU-style cache for ARP and BGP status results."""
    def __init__(self, ttl_seconds=5):
        self.cache = {}
        self.ttl_seconds = ttl_seconds
    
    def get(self, key):
        """Get cached value if not expired."""
        import time
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                return value
            else:
                # Expired - remove it
                del self.cache[key]
        return None
    
    def set(self, key, value):
        """Set cached value with current timestamp."""
        import time
        self.cache[key] = (value, time.time())
    
    def clear(self):
        """Clear all cached values."""
        self.cache.clear()
    
    def remove(self, key):
        """Remove a specific key from cache."""
        if key in self.cache:
            del self.cache[key]


class DevicesTab(QWidget):
    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Caching layer for status results (5 second TTL)
        self.arp_cache = StatusCache(ttl_seconds=5)
        self.bgp_cache = StatusCache(ttl_seconds=10)

        # polling - DISABLED to prevent QThread crashes
        # ARP checks will be manual only via refresh button
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.poll_device_status)
        # self.status_timer.start(30000)  # DISABLED - no automatic polling

        self.active_bgp_devices = set()
        self.all_devices = {}
        self.interface_to_device_map = {}
        self.selected_interfaces = set()
        self._arp_check_in_progress = False  # Flag to prevent multiple ARP checks
        self.selected_iface_name = ""

        # Create simple tab widget like main window
        self.tab_widget = QTabWidget()
        # Align tabs to the left instead of center
        self.tab_widget.setTabPosition(QTabWidget.North)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #c0c0c0;
            }
            QTabWidget::tab-bar {
                alignment: left;
            }
            QTabBar::tab {
                margin-right: 4px;
                padding: 4px 8px;
            }
        """)
        layout.addWidget(self.tab_widget)

        # Create Devices sub-tab
        self.devices_subtab = QWidget()
        self.setup_devices_subtab()

        # Create BGP sub-tab
        self.bgp_subtab = QWidget()
        self.setup_bgp_subtab()

        # Create OSPF sub-tab
        self.ospf_subtab = QWidget()
        self.setup_ospf_subtab()

        # Create ISIS sub-tab
        self.isis_subtab = QWidget()
        self.setup_isis_subtab()

        # Add tabs to tab widget
        self.tab_widget.addTab(self.devices_subtab, "Devices")
        self.tab_widget.addTab(self.bgp_subtab, "BGP")
        self.tab_widget.addTab(self.ospf_subtab, "OSPF")
        self.tab_widget.addTab(self.isis_subtab, "ISIS")


    def setup_devices_subtab(self):
        """Setup the Devices sub-tab with device table and controls."""
        layout = QVBoxLayout(self.devices_subtab)

        # columns
        # Simplified device table - only essential device info
        self.device_headers = [
            "Device Name", "Status", "IPv4", "IPv6", "VLAN", "IPv4 Gateway", "IPv6 Gateway", "IPv4 Mask", "IPv6 Mask", "MAC Address", "Loopback IPv4", "Loopback IPv6"
        ]
        # Add Device List label
        layout.addWidget(QLabel("Device List"))
        
        self.devices_table = QTableWidget(0, len(self.device_headers))
        self.devices_table.setHorizontalHeaderLabels(self.device_headers)
        # map header -> index
        self.COL = {h: i for i, h in enumerate(self.device_headers)}
        
        # Enable inline editing
        self.devices_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.devices_table.setSelectionBehavior(QTableWidget.SelectItems)
        
        # Connect cell change event for validation and updates
        self.devices_table.cellChanged.connect(self.on_cell_changed)
        
        # Set tooltips for editable columns
        self.setup_column_tooltips()

        layout.addWidget(self.devices_table)
        
        # Configure column widths - make Status column smaller and ensure proper alignment
        self.devices_table.setColumnWidth(self.COL["Status"], 80)  # Smaller width for Status column
        self.devices_table.setColumnWidth(self.COL["Device Name"], 150)
        self.devices_table.setColumnWidth(self.COL["IPv4"], 120)
        self.devices_table.setColumnWidth(self.COL["IPv6"], 150)
        self.devices_table.setColumnWidth(self.COL["VLAN"], 60)
        self.devices_table.setColumnWidth(self.COL["IPv4 Gateway"], 120)
        self.devices_table.setColumnWidth(self.COL["IPv6 Gateway"], 150)
        self.devices_table.setColumnWidth(self.COL["IPv4 Mask"], 80)
        self.devices_table.setColumnWidth(self.COL["IPv6 Mask"], 80)
        self.devices_table.setColumnWidth(self.COL["MAC Address"], 150)
        self.devices_table.setColumnWidth(self.COL["Loopback IPv4"], 130)
        self.devices_table.setColumnWidth(self.COL["Loopback IPv6"], 150)

        # optionally hide internal-ish fields (starting from column 12, after Loopback IPv4 and IPv6 at columns 10-11)
        for col in range(12, 16):
            self.devices_table.setColumnHidden(col, True)

        # ---- icons via shared loader ----
        def load_icon(filename: str) -> QIcon:
            return qicon("resources", f"icons/{filename}")

        self.green_dot = load_icon("green_dot.png")  # Round green dot for ARP success
        self.orange_dot = load_icon("arpfail.png")   # Orange dot for ARP failure
        self.red_dot = load_icon("red_dot.png")      # Red dot for errors/failures
        self.yellow_dot = load_icon("yellow_dot.png") # Yellow dot for stopping state
        self.stop_icon = load_icon("stop.png")       # Stop icon for stopped devices
        self.arp_success = load_icon("arpsuccess.png")  # ARP success icon
        self.arp_fail = load_icon("arpfail.png")        # ARP fail icon
        
        # BGP monitoring timer
        self.bgp_monitoring_timer = QTimer()
        self.bgp_monitoring_timer.timeout.connect(self.periodic_bgp_status_check)
        self.bgp_monitoring_active = False
        
        # OSPF monitoring timer
        self.ospf_monitoring_timer = QTimer()
        self.ospf_monitoring_timer.timeout.connect(self.periodic_ospf_status_check)
        self.ospf_monitoring_active = False
        
        # ISIS monitoring timer
        self.isis_monitoring_timer = QTimer()
        self.isis_monitoring_timer.timeout.connect(self.periodic_isis_status_check)
        self.isis_monitoring_active = False
        
        # Note: Removed duplicate device_status_timer - using existing status_timer instead

        # ---- buttons ----
        btns = QHBoxLayout()
        btns.setAlignment(Qt.AlignLeft)


        self.add_button = QPushButton()
        self.add_button.setIcon(load_icon("add.png"))
        self.add_button.setIconSize(QSize(16, 16))
        self.add_button.setFixedSize(32, 28)
        self.add_button.setToolTip("Add Device")

        self.edit_button = QPushButton()
        self.edit_button.setIcon(load_icon("edit.png"))
        self.edit_button.setIconSize(QSize(16, 16))
        self.edit_button.setFixedSize(32, 28)
        self.edit_button.setToolTip("Edit Device")

        self.remove_button = QPushButton()
        self.remove_button.setIcon(load_icon("remove.png"))
        self.remove_button.setIconSize(QSize(16, 16))
        self.remove_button.setFixedSize(32, 28)
        self.remove_button.setToolTip("Remove Device")

        self.apply_button = QPushButton("✓")
        self.apply_button.setFixedSize(32, 28)
        self.apply_button.setToolTip("Check & Reconfigure Selected Devices")

        self.ping_button = QPushButton()
        self.ping_button.setIcon(load_icon("start.png"))
        self.ping_button.setIconSize(QSize(16, 16))
        self.ping_button.setFixedSize(32, 28)
        self.ping_button.setToolTip("Ping Test")
        
        self.arp_button = QPushButton()
        self.arp_button.setIcon(load_icon("refresh.png"))
        self.arp_button.setIconSize(QSize(16, 16))
        self.arp_button.setFixedSize(32, 28)
        self.arp_button.setToolTip("Refresh ARP Status")

        self.copy_button = QPushButton()
        self.copy_button.setIcon(load_icon("copy.png"))
        self.copy_button.setIconSize(QSize(16, 16))
        self.copy_button.setFixedSize(32, 28)
        self.copy_button.setToolTip("Copy Device")

        self.paste_button = QPushButton()
        self.paste_button.setIcon(load_icon("paste.png"))
        self.paste_button.setIconSize(QSize(16, 16))
        self.paste_button.setFixedSize(32, 28)
        self.paste_button.setToolTip("Paste Device")

        # Add dedicated Start/Stop buttons for devices
        self.start_device_button = QPushButton()
        self.start_device_button.setIcon(load_icon("start.png"))
        self.start_device_button.setIconSize(QSize(16, 16))
        self.start_device_button.setFixedSize(32, 28)
        self.start_device_button.setToolTip("Start Selected Devices")

        self.stop_device_button = QPushButton()
        self.stop_device_button.setIcon(load_icon("stop.png"))
        self.stop_device_button.setIconSize(QSize(16, 16))
        self.stop_device_button.setFixedSize(32, 28)
        self.stop_device_button.setToolTip("Stop Selected Devices")

        # BGP Route Pool Management button (global pools - in Devices tab)
        self.manage_route_pools_button = QPushButton("🗂️")
        self.manage_route_pools_button.setFixedSize(32, 28)
        self.manage_route_pools_button.setToolTip("Manage BGP Route Pools")


        # Only add device management buttons to Devices tab
        for b in (self.add_button, self.edit_button, self.remove_button, 
                  self.start_device_button, self.stop_device_button,
                  self.apply_button, self.ping_button, self.arp_button, 
                  self.copy_button, self.paste_button, 
                  self.manage_route_pools_button):
            btns.addWidget(b)

        layout.addLayout(btns)

        # wiring
        self.add_button.clicked.connect(self.prompt_add_device)
        self.edit_button.clicked.connect(self.prompt_edit_device)
        self.remove_button.clicked.connect(self.remove_selected_device)
        self.start_device_button.clicked.connect(self.start_selected_devices)
        self.stop_device_button.clicked.connect(self.stop_selected_devices)
        self.apply_button.clicked.connect(self.apply_selected_device_with_arp)
        self.ping_button.clicked.connect(self.ping_selected_device)
        self.arp_button.clicked.connect(self._on_arp_button_clicked)
        self.copy_button.clicked.connect(self.copy_selected_device)
        self.paste_button.clicked.connect(self.paste_device_to_interface)
        self.manage_route_pools_button.clicked.connect(self.prompt_manage_route_pools)

    def setup_bgp_subtab(self):
        """Setup the BGP sub-tab with BGP-specific functionality."""
        layout = QVBoxLayout(self.bgp_subtab)
        
        # BGP Neighbors Table - each neighbor IP gets its own row
        bgp_headers = ["Device", "BGP Status", "Neighbor Type", "Neighbor IP", "Source IP", "BGP Local AS", "BGP Remote AS", "State", "Routes", "Route Pools", "Keepalive", "Hold-time"]
        self.bgp_table = QTableWidget(0, len(bgp_headers))
        self.bgp_table.setHorizontalHeaderLabels(bgp_headers)
        self.BGP_COL = {h: i for i, h in enumerate(bgp_headers)}
        
        # Enable inline editing for the BGP table
        self.bgp_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.bgp_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect selection changed signal to update attach button state
        self.bgp_table.selectionModel().selectionChanged.connect(self.on_bgp_selection_changed)
        
        # Connect cell changed signal for handling checkbox changes
        self.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)
        
        layout.addWidget(QLabel("BGP Neighbors"))
        layout.addWidget(self.bgp_table)
        
        # BGP Controls
        bgp_controls = QHBoxLayout()
        
        # Add BGP button
        def load_icon(filename: str) -> QIcon:
            return qicon("resources", f"icons/{filename}")
        
        self.add_bgp_button = QPushButton()
        self.add_bgp_button.setIcon(load_icon("add.png"))
        self.add_bgp_button.setIconSize(QSize(16, 16))
        self.add_bgp_button.setFixedSize(32, 28)
        self.add_bgp_button.setToolTip("Add BGP")
        self.add_bgp_button.clicked.connect(self.prompt_add_bgp)
        
        # Edit BGP button
        self.edit_bgp_button = QPushButton()
        self.edit_bgp_button.setIcon(load_icon("edit.png"))
        self.edit_bgp_button.setIconSize(QSize(16, 16))
        self.edit_bgp_button.setFixedSize(32, 28)
        self.edit_bgp_button.setToolTip("Edit BGP Configuration")
        self.edit_bgp_button.clicked.connect(self.prompt_edit_bgp)
        
        # Delete BGP button
        self.delete_bgp_button = QPushButton()
        self.delete_bgp_button.setIcon(load_icon("remove.png"))
        self.delete_bgp_button.setIconSize(QSize(16, 16))
        self.delete_bgp_button.setFixedSize(32, 28)
        self.delete_bgp_button.setToolTip("Delete BGP Configuration")
        self.delete_bgp_button.clicked.connect(self.prompt_delete_bgp)
        
        # Refresh BGP Status button
        self.bgp_refresh_button = QPushButton()
        self.bgp_refresh_button.setIcon(load_icon("refresh.png"))
        self.bgp_refresh_button.setFixedSize(32, 28)
        self.bgp_refresh_button.setToolTip("Refresh BGP Status")
        self.bgp_refresh_button.clicked.connect(self.refresh_bgp_status)
        
        
        # Apply BGP button
        self.apply_bgp_button = QPushButton()
        self.apply_bgp_button.setIcon(load_icon("apply.png"))
        self.apply_bgp_button.setFixedSize(32, 28)
        self.apply_bgp_button.setToolTip("Apply BGP configurations to server")
        self.apply_bgp_button.clicked.connect(self.apply_bgp_configurations)
        
        # BGP Start/Stop buttons
        self.bgp_start_button = QPushButton()
        self.bgp_start_button.setIcon(load_icon("start.png"))
        self.bgp_start_button.setIconSize(QSize(16, 16))
        self.bgp_start_button.setFixedSize(32, 28)
        self.bgp_start_button.setToolTip("Start BGP")
        self.bgp_start_button.clicked.connect(self.start_bgp_protocol)
        
        self.bgp_stop_button = QPushButton()
        self.bgp_stop_button.setIcon(load_icon("stop.png"))
        self.bgp_stop_button.setIconSize(QSize(16, 16))
        self.bgp_stop_button.setFixedSize(32, 28)
        self.bgp_stop_button.setToolTip("Stop BGP")
        self.bgp_stop_button.clicked.connect(self.stop_bgp_protocol)
        
        # Attach Route Pools button (in BGP tab - neighbor-specific)
        self.attach_route_pools_button = QPushButton()
        self.attach_route_pools_button.setIcon(load_icon("readd.png"))
        self.attach_route_pools_button.setFixedSize(32, 28)
        self.attach_route_pools_button.setToolTip("Attach Route Pools to BGP Neighbor")
        self.attach_route_pools_button.clicked.connect(self.prompt_attach_route_pools)
        
        bgp_controls.addWidget(self.add_bgp_button)
        bgp_controls.addWidget(self.edit_bgp_button)
        bgp_controls.addWidget(self.delete_bgp_button)
        bgp_controls.addWidget(self.attach_route_pools_button)
        bgp_controls.addWidget(self.apply_bgp_button)
        bgp_controls.addWidget(self.bgp_start_button)
        bgp_controls.addWidget(self.bgp_stop_button)
        bgp_controls.addWidget(self.bgp_refresh_button)
        bgp_controls.addStretch()
        layout.addLayout(bgp_controls)

    def setup_ospf_subtab(self):
        """Setup the OSPF sub-tab with OSPF-specific functionality."""
        layout = QVBoxLayout(self.ospf_subtab)
        
        # OSPF Neighbors Table
        ospf_headers = ["Device", "OSPF Status", "Neighbor Type", "Interface", "Neighbor ID", "State", "Priority", "Dead Timer", "Uptime"]
        self.ospf_table = QTableWidget(0, len(ospf_headers))
        self.ospf_table.setHorizontalHeaderLabels(ospf_headers)
        layout.addWidget(QLabel("OSPF Neighbors"))
        layout.addWidget(self.ospf_table)
        
        # OSPF Controls
        ospf_controls = QHBoxLayout()
        
        # Add OSPF button
        def load_icon(filename: str) -> QIcon:
            return qicon("resources", f"icons/{filename}")
        
        self.add_ospf_button = QPushButton()
        self.add_ospf_button.setIcon(load_icon("add.png"))
        self.add_ospf_button.setIconSize(QSize(16, 16))
        self.add_ospf_button.setFixedSize(32, 28)
        self.add_ospf_button.setToolTip("Add OSPF")
        self.add_ospf_button.clicked.connect(self.prompt_add_ospf)
        
        self.edit_ospf_button = QPushButton()
        self.edit_ospf_button.setIcon(load_icon("edit.png"))
        self.edit_ospf_button.setIconSize(QSize(16, 16))
        self.edit_ospf_button.setFixedSize(32, 28)
        self.edit_ospf_button.setToolTip("Edit OSPF Configuration")
        self.edit_ospf_button.clicked.connect(self.prompt_edit_ospf)
        
        self.delete_ospf_button = QPushButton()
        self.delete_ospf_button.setIcon(load_icon("remove.png"))
        self.delete_ospf_button.setIconSize(QSize(16, 16))
        self.delete_ospf_button.setFixedSize(32, 28)
        self.delete_ospf_button.setToolTip("Delete OSPF Configuration")
        self.delete_ospf_button.clicked.connect(self.prompt_delete_ospf)
        
        self.ospf_refresh_button = QPushButton()
        self.ospf_refresh_button.setIcon(load_icon("refresh.png"))
        self.ospf_refresh_button.setIconSize(QSize(16, 16))
        self.ospf_refresh_button.setFixedSize(32, 28)
        self.ospf_refresh_button.setToolTip("Refresh OSPF Status")
        self.ospf_refresh_button.clicked.connect(self.refresh_ospf_status)
        
        # OSPF Start/Stop buttons
        self.ospf_start_button = QPushButton()
        self.ospf_start_button.setIcon(load_icon("start.png"))
        self.ospf_start_button.setIconSize(QSize(16, 16))
        self.ospf_start_button.setFixedSize(32, 28)
        self.ospf_start_button.setToolTip("Start OSPF")
        self.ospf_start_button.clicked.connect(self.start_ospf_protocol)
        
        self.ospf_stop_button = QPushButton()
        self.ospf_stop_button.setIcon(load_icon("stop.png"))
        self.ospf_stop_button.setIconSize(QSize(16, 16))
        self.ospf_stop_button.setFixedSize(32, 28)
        self.ospf_stop_button.setToolTip("Stop OSPF")
        self.ospf_stop_button.clicked.connect(self.stop_ospf_protocol)
        
        ospf_controls.addWidget(self.add_ospf_button)
        ospf_controls.addWidget(self.edit_ospf_button)
        ospf_controls.addWidget(self.delete_ospf_button)
        ospf_controls.addWidget(self.ospf_start_button)
        ospf_controls.addWidget(self.ospf_stop_button)
        ospf_controls.addWidget(self.ospf_refresh_button)
        ospf_controls.addStretch()
        layout.addLayout(ospf_controls)

    def setup_isis_subtab(self):
        """Setup the ISIS sub-tab with ISIS-specific functionality."""
        layout = QVBoxLayout(self.isis_subtab)
        
        # ISIS Neighbors Table with requested columns
        isis_headers = ["Device", "ISIS Status", "Neighbor Type", "Interface", "ISIS Area", "Level", "ISIS Net"]
        self.isis_table = QTableWidget(0, len(isis_headers))
        self.isis_table.setHorizontalHeaderLabels(isis_headers)
        
        # Set column widths for better visibility
        self.isis_table.setColumnWidth(0, 120)  # Device
        self.isis_table.setColumnWidth(1, 100)  # ISIS Status
        self.isis_table.setColumnWidth(2, 120)  # Neighbor Type
        self.isis_table.setColumnWidth(3, 100)  # Interface
        self.isis_table.setColumnWidth(4, 120)  # ISIS Area
        self.isis_table.setColumnWidth(5, 80)   # Level
        self.isis_table.setColumnWidth(6, 200)  # ISIS Net
        
        layout.addWidget(QLabel("ISIS Neighbors"))
        layout.addWidget(self.isis_table)
        
        # ISIS Controls
        isis_controls = QHBoxLayout()
        
        # Add ISIS button
        def load_icon(filename: str) -> QIcon:
            return qicon("resources", f"icons/{filename}")
        
        self.add_isis_button = QPushButton()
        self.add_isis_button.setIcon(load_icon("add.png"))
        self.add_isis_button.setIconSize(QSize(16, 16))
        self.add_isis_button.setFixedSize(32, 28)
        self.add_isis_button.setToolTip("Add IS-IS")
        self.add_isis_button.clicked.connect(self.prompt_add_isis)
        
        # Edit ISIS button
        self.edit_isis_button = QPushButton()
        self.edit_isis_button.setIcon(load_icon("edit.png"))
        self.edit_isis_button.setIconSize(QSize(16, 16))
        self.edit_isis_button.setFixedSize(32, 28)
        self.edit_isis_button.setToolTip("Edit ISIS Configuration")
        self.edit_isis_button.clicked.connect(self.prompt_edit_isis)
        
        # Delete ISIS button
        self.delete_isis_button = QPushButton()
        self.delete_isis_button.setIcon(load_icon("remove.png"))
        self.delete_isis_button.setIconSize(QSize(16, 16))
        self.delete_isis_button.setFixedSize(32, 28)
        self.delete_isis_button.setToolTip("Delete ISIS Configuration")
        self.delete_isis_button.clicked.connect(self.prompt_delete_isis)
        
        # ISIS refresh button with icon
        self.isis_refresh_button = QPushButton()
        self.isis_refresh_button.setIcon(load_icon("refresh.png"))
        self.isis_refresh_button.setIconSize(QSize(16, 16))
        self.isis_refresh_button.setFixedSize(32, 28)
        self.isis_refresh_button.setToolTip("Refresh ISIS Status")
        self.isis_refresh_button.clicked.connect(self.refresh_isis_status)
        
        # Apply ISIS button
        self.apply_isis_button = QPushButton()
        self.apply_isis_button.setIcon(load_icon("apply.png"))
        self.apply_isis_button.setFixedSize(32, 28)
        self.apply_isis_button.setToolTip("Apply ISIS configurations to server")
        self.apply_isis_button.clicked.connect(self.apply_isis_configurations)
        
        # IS-IS Start/Stop buttons
        self.isis_start_button = QPushButton()
        self.isis_start_button.setIcon(load_icon("start.png"))
        self.isis_start_button.setIconSize(QSize(16, 16))
        self.isis_start_button.setFixedSize(32, 28)
        self.isis_start_button.setToolTip("Start IS-IS")
        self.isis_start_button.clicked.connect(self.start_isis_protocol)
        
        self.isis_stop_button = QPushButton()
        self.isis_stop_button.setIcon(load_icon("stop.png"))
        self.isis_stop_button.setIconSize(QSize(16, 16))
        self.isis_stop_button.setFixedSize(32, 28)
        self.isis_stop_button.setToolTip("Stop IS-IS")
        self.isis_stop_button.clicked.connect(self.stop_isis_protocol)
        
        isis_controls.addWidget(self.add_isis_button)
        isis_controls.addWidget(self.edit_isis_button)
        isis_controls.addWidget(self.delete_isis_button)
        isis_controls.addWidget(self.apply_isis_button)
        isis_controls.addWidget(self.isis_start_button)
        isis_controls.addWidget(self.isis_stop_button)
        isis_controls.addWidget(self.isis_refresh_button)
        isis_controls.addStretch()
        layout.addLayout(isis_controls)

    def prompt_edit_isis(self):
        """Edit ISIS configuration for selected device."""
        selected_items = self.isis_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an ISIS configuration to edit.")
            return

        # Get unique rows from selection
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if len(selected_rows) > 1:
            QMessageBox.warning(self, "Multiple Selection", "Please select only one ISIS configuration to edit.")
            return
        
        row = list(selected_rows)[0]
        device_name = self.isis_table.item(row, 0).text()  # Device column
        
        # Find the device in all_devices using safe helper
        device_info = self._find_device_by_name(device_name)
        
        if not device_info or "protocols" not in device_info or "IS-IS" not in device_info["protocols"]:
            QMessageBox.warning(self, "No ISIS Configuration", f"No ISIS configuration found for device '{device_name}'.")
            return

        # Get current ISIS configuration
        if isinstance(device_info["protocols"], dict):
            current_isis = device_info["protocols"]["IS-IS"]
        else:
            current_isis = device_info.get("is_is_config", {})

        # Create dialog with current ISIS configuration in edit mode
        dialog = AddIsisDialog(self, device_name, edit_mode=True, isis_config=current_isis)
        
        if dialog.exec_() != dialog.Accepted:
            return

        new_isis_config = dialog.get_values()
        
        # Update the device with new ISIS configuration
        if isinstance(device_info["protocols"], dict):
            device_info["protocols"]["IS-IS"] = new_isis_config
        else:
            device_info["is_is_config"] = new_isis_config
        
        # Update the ISIS table
        self.update_isis_table()
        
        # Save session
        if hasattr(self.main_window, "save_session"):
            self.main_window.save_session()

    def prompt_delete_isis(self):
        """Delete ISIS configuration for selected device."""
        selected_items = self.isis_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an ISIS configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.isis_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self, "Confirm Deletion", 
                                   f"Are you sure you want to delete ISIS configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices using safe helper
        device_info = self._find_device_by_name(device_name)
        
        if device_info and "protocols" in device_info and "IS-IS" in device_info["protocols"]:
            # Check if ISIS is already marked for removal
            isis_config = device_info.get("is_is_config", {})
            if isinstance(isis_config, dict) and isis_config.get("_marked_for_removal"):
                QMessageBox.information(self, "Already Marked for Removal", 
                                      f"ISIS configuration for '{device_name}' is already marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")
                return
            
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove ISIS configuration from server first
                server_url = self.get_server_url()
                if server_url:
                    try:
                        # Call server ISIS cleanup endpoint
                        response = requests.post(f"{server_url}/api/isis/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            print(f"✅ ISIS configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            print(f"⚠️ Server ISIS cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        print(f"⚠️ Network error removing ISIS from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    print("⚠️ No server URL available, removing ISIS configuration locally only")
            
            # Mark ISIS for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if isinstance(device_info["protocols"], dict):
                device_info["protocols"]["IS-IS"] = {"_marked_for_removal": True}
            else:
                device_info["is_is_config"] = {"_marked_for_removal": True}
            
            # Update the ISIS table to show the device as marked for removal
            self.update_isis_table()
            
            # Save session
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            
            QMessageBox.information(self, "ISIS Configuration Marked for Removal", 
                                  f"ISIS configuration for '{device_name}' has been marked for removal. Click 'Apply ISIS Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self, "No ISIS Configuration", f"No ISIS configuration found for device '{device_name}'.")

    def apply_isis_configurations(self):
        """Apply ISIS configurations to the server for selected ISIS table rows."""
        server_url = self.get_server_url()
        if not server_url:
            QMessageBox.critical(self, "No Server", "No server selected.")
            return

        # Get selected rows from the ISIS table
        selected_items = self.isis_table.selectedItems()
        selected_devices = []
        
        if selected_items:
            # Get unique device names from selected ISIS table rows
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.isis_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find the devices in all_devices
            for device_name in selected_device_names:
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            selected_devices.append(device)
                            break

        # Handle both ISIS application and removal
        devices_to_apply_isis = []  # Devices that need ISIS configuration applied
        devices_to_remove_isis = []  # Devices that need ISIS configuration removed
        
        if selected_items:
            # If ISIS table rows are selected, process only those devices
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.isis_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find devices and determine if they need ISIS applied or removed
            for device_name in selected_device_names:
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            isis_config = device.get("is_is_config", {})
                            if isis_config:
                                if isis_config.get("_marked_for_removal"):
                                    # Device is marked for ISIS removal
                                    devices_to_remove_isis.append(device)
                                else:
                                    # Device needs ISIS configuration applied
                                    devices_to_apply_isis.append(device)
        else:
            # If no ISIS table rows are selected, process all devices with ISIS configurations
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    isis_config = device.get("is_is_config", {})
                    if isis_config:
                        if isis_config.get("_marked_for_removal"):
                            devices_to_remove_isis.append(device)
                        else:
                            devices_to_apply_isis.append(device)

        # Apply ISIS configurations
        if devices_to_apply_isis:
            self._apply_isis_to_devices(devices_to_apply_isis, server_url)
        
        # Remove ISIS configurations
        if devices_to_remove_isis:
            self._remove_isis_from_devices(devices_to_remove_isis, server_url)

    def _apply_isis_to_devices(self, devices, server_url):
        """Apply ISIS configuration to the specified devices."""
        try:
            for device in devices:
                device_id = device.get("device_id")
                device_name = device.get("Device Name", "Unknown")
                # Resolve server URL per device based on its TG/interface selection
                per_device_server_url = self._get_server_url_from_interface(device.get("Interface", "")) or server_url
                # Use the canonical key name for ISIS configuration
                isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
                # Fallback: some legacy structures may store under protocols -> ISIS
                if not isis_config and isinstance(device.get("protocols"), dict):
                    proto = device.get("protocols", {})
                    isis_config = proto.get("ISIS", {}) or proto.get("isis", {})
                
                if not device_id or not isis_config:
                    continue
                
                # Prepare ISIS configuration data using the configure endpoint (similar to OSPF)
                isis_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": device.get("Interface", ""),
                    "vlan": device.get("VLAN", "0"),
                    "ipv4": device.get("IPv4", ""),
                    "ipv6": device.get("IPv6", ""),
                    "ipv4_gateway": device.get("IPv4 Gateway", ""),
                    "ipv6_gateway": device.get("IPv6 Gateway", ""),
                    "isis_config": isis_config
                }
                
                # Ensure per-device server URL exists
                if not per_device_server_url:
                    print(f"[ISIS POST] No server URL resolved for device '{device_name}'. Skipping.")
                    continue

                post_url = f"{per_device_server_url}/api/device/isis/configure"
                # Client-side debug of outgoing request
                try:
                    print(f"[ISIS POST] URL: {post_url}")
                    print(f"[ISIS POST] Payload: {isis_data}")
                except Exception:
                    pass

                # Send ISIS configuration to server using configure endpoint
                try:
                    response = requests.post(post_url, json=isis_data, timeout=30)
                except Exception as e:
                    print(f"[ISIS POST] Exception posting to {post_url}: {e}")
                    continue
                
                if response.status_code == 200:
                    print(f"✅ ISIS configuration applied to server for {device_name}")
                else:
                    try:
                        error_msg = response.json().get("error", response.text)
                    except Exception:
                        error_msg = response.text
                    print(f"❌ Failed to apply ISIS configuration for {device_name} (status {response.status_code}): {error_msg}")
            
            # Refresh ISIS table after applying configurations
            self.update_isis_table()
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Network error applying ISIS configurations: {str(e)}")
            QMessageBox.critical(self, "Network Error", f"Failed to apply ISIS configurations: {str(e)}")

    def _remove_isis_from_devices(self, devices, server_url):
        """Remove ISIS configuration from the specified devices."""
        try:
            for device in devices:
                device_id = device.get("device_id")
                device_name = device.get("Device Name", "Unknown")
                isis_config = device.get("is_is_config", {})
                
                if not device_id:
                    continue
                
                # Try to remove ISIS configuration from server first (for Docker-based devices)
                server_removal_success = False
                if server_url:
                    try:
                        # Prepare ISIS removal data
                        isis_data = {
                            "device_id": device_id,
                            "device_name": device_name,
                            "isis_config": isis_config
                        }
                        
                        # Send ISIS removal request to server
                        response = requests.post(f"{server_url}/api/device/isis/stop", 
                                               json=isis_data, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            print(f"✅ ISIS configuration removed from server for {device_name}")
                            server_removal_success = True
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            print(f"⚠️ Server ISIS removal failed for {device_name}: {error_msg}")
                            # Continue with local removal even if server fails
                    except requests.exceptions.RequestException as e:
                        print(f"⚠️ Network error removing ISIS from server for {device_name}: {str(e)}")
                        # Continue with local removal even if server fails
                else:
                    print(f"⚠️ No server URL available, removing ISIS configuration locally only for {device_name}")
                
                # Always remove ISIS configuration from device data (local removal)
                # This ensures the configuration is removed regardless of server status
                if isinstance(device.get("protocols"), dict):
                    device["protocols"].pop("IS-IS", None)
                    print(f"✅ ISIS configuration removed locally for {device_name} (dict format)")
                else:
                    device.pop("is_is_config", None)
                    # Remove IS-IS from protocols list
                    protocols = device.get("protocols", [])
                    if isinstance(protocols, list) and "IS-IS" in protocols:
                        protocols.remove("IS-IS")
                        print(f"✅ ISIS configuration removed locally for {device_name} (list format)")
            
            # Refresh ISIS table after removing configurations
            self.update_isis_table()
            
            # Save session
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            
        except Exception as e:
            print(f"❌ Error removing ISIS configurations: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to remove ISIS configurations: {str(e)}")

    def refresh_bgp_status(self):
        """Refresh BGP neighbor status from database - only update status, don't replace table."""
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return
            
            # Just refresh the table from device configurations (doesn't replace data)
            # This will call _get_bgp_neighbor_state for each row to get fresh status from database
            self.update_bgp_table()
            print("[BGP REFRESH] BGP status refreshed from database")
        except Exception as e:
            print(f"Error refreshing BGP status: {e}")

    def on_bgp_selection_changed(self):
        """Update attach button tooltip when selection changes."""
        selection_model = self.bgp_table.selectionModel()
        total_rows = self.bgp_table.rowCount()
        selected_count = len(selection_model.selectedRows()) if selection_model else 0
        
        # Keep the same icon, just update tooltip
        if selected_count == total_rows and total_rows > 0:
            self.attach_route_pools_button.setToolTip("Attach Route Pools to All BGP Neighbors")
        else:
            self.attach_route_pools_button.setToolTip("Attach Route Pools to BGP Neighbor")


    def refresh_ospf_status(self):
        """Refresh OSPF neighbor status from server."""
        try:
            print("[OSPF REFRESH] Refreshing OSPF status from database...")
            # Update the OSPF table which fetches status from database
            self.update_ospf_table()
            print("[OSPF REFRESH] OSPF status refreshed successfully")
        except Exception as e:
            print(f"[OSPF REFRESH ERROR] Error refreshing OSPF status: {e}")

    def refresh_isis_status(self):
        """Refresh ISIS neighbor status from server."""
        try:
            print("[ISIS REFRESH] Refreshing ISIS status from database...")
            # Update the ISIS table which fetches status from database
            self.update_isis_table()
            print("[ISIS REFRESH] ISIS status refreshed successfully")
        except Exception as e:
            print(f"[ISIS REFRESH ERROR] Error refreshing ISIS status: {e}")

    def _check_arp_status(self, device_info):
        """Check ARP status for a device from database"""
        try:
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            
            if not device_id:
                return False
                
            server_url = self._get_server_url_from_interface(iface_label)
            if not server_url:
                return False
                
            # Get ARP status from database instead of direct server call
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=3)
            if response.status_code == 200:
                device_data = response.json()
                arp_status = device_data.get('arp_status', 'Unknown')
                return arp_status == 'Resolved'
            return False
        except:
            return False

    def _get_single_bgp_neighbor_state(self, device_id, neighbor_ip, device_info=None):
        """Helper function to get BGP state for a single neighbor (used in parallel execution)."""
        try:
            return self._get_bgp_neighbor_state(device_id, neighbor_ip, device_info)
        except Exception as e:
            logging.error(f"[BGP PARALLEL] Error getting state for {neighbor_ip}: {e}")
            return "Error"

    def _get_bgp_neighbor_state_from_database(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state from database instead of direct server check"""
        try:
            # First check if device is started (ARP is successful)
            if device_info:
                gateway = device_info.get("Gateway", "")
                device_ip = device_info.get("IPv4", "")
                
                # If no device IP configured, device is not configured
                if not device_ip:
                    return "Device Not Configured"
                
                # If no gateway configured, show BGP status but indicate connectivity issue
                if not gateway:
                        return "Gateway Not Configured"
            
            server_url = self.get_server_url(silent=True)
            if not server_url or not device_id:
                return "Unknown"
            
            # Get device information from database instead of direct BGP status
            try:
                response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=1)
                if response.status_code == 200:
                    device_data = response.json()
                    
                    # Check if device is running
                    device_status = device_data.get('status', 'Unknown')
                    if device_status != 'Running':
                        return "Device Not Started"
                    
                    # Get BGP status from database
                    bgp_ipv4_established = device_data.get('bgp_ipv4_established', False)
                    bgp_ipv6_established = device_data.get('bgp_ipv6_established', False)
                    bgp_ipv4_state = device_data.get('bgp_ipv4_state', 'Unknown')
                    bgp_ipv6_state = device_data.get('bgp_ipv6_state', 'Unknown')
                    last_bgp_check = device_data.get('last_bgp_check', '')
                    
                    # Debug logging for BGP status (reduced to prevent UI spam)
                    # print(f"[BGP STATUS DEBUG] Device {device_id}, Neighbor {neighbor_ip}")
                    
                    # Determine if this is IPv4 or IPv6 neighbor
                    is_ipv6 = ':' in neighbor_ip
                    
                    if is_ipv6:
                        # IPv6 neighbor
                        if bgp_ipv6_established:
                            result_status = "Established"
                        else:
                            result_status = bgp_ipv6_state if bgp_ipv6_state != 'Unknown' else "Idle"
                    else:
                        # IPv4 neighbor
                        if bgp_ipv4_established:
                            result_status = "Established"
                        else:
                            result_status = bgp_ipv4_state if bgp_ipv4_state != 'Unknown' else "Idle"
                    
                    # print(f"[BGP STATUS DEBUG] Returning status: {result_status}")
                    return result_status
                else:
                    return "Unknown"
            except Exception:
                # Don't print debug errors to reduce spam
                return "Unknown"
                
        except Exception as e:
            print(f"[DEBUG] Error getting BGP state for {neighbor_ip}: {e}")
            return "Unknown"

    def _get_bgp_neighbor_state(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state - now uses database instead of direct server check"""
        return self._get_bgp_neighbor_state_from_database(device_id, neighbor_ip, device_info)

    def update_bgp_table(self, neighbors=None):
        """Update the BGP table with neighbor information - one row per neighbor IP."""
        # Auto-start BGP monitoring if we have BGP devices and monitoring is not active
        if not self.bgp_monitoring_active:
            has_bgp_devices = False
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if "protocols" in device and "BGP" in device["protocols"]:
                        # Handle both old format (dict) and new format (list)
                        if isinstance(device["protocols"], dict):
                            bgp_config = device["protocols"]["BGP"]
                        else:
                            bgp_config = device.get("bgp_config", {})
                        
                        if not bgp_config.get("_marked_for_removal", False):
                            has_bgp_devices = True
                            break
                if has_bgp_devices:
                    break
            
            if has_bgp_devices:
                print("[BGP AUTO-START] Auto-starting BGP monitoring for existing BGP devices")
                self.start_bgp_monitoring()
        
        if neighbors is not None:
            # Update from server data - one row per neighbor
            self.bgp_table.setRowCount(0)
            
            for neighbor in neighbors:
                row = self.bgp_table.rowCount()
                self.bgp_table.insertRow(row)
                
                # Debug: Check if neighbor is a dict or list
                if not isinstance(neighbor, dict):
                    print(f"[BGP TABLE DEBUG] Warning: neighbor is not a dict, it's {type(neighbor)}: {neighbor}")
                    continue
                
                device_name = neighbor.get("device", "Unknown")
                neighbor_ip = neighbor.get("neighbor_ip", "")
                neighbor_type = "IPv6" if ":" in neighbor_ip else "IPv4"
                bgp_status = neighbor.get("state", "Idle")
                
                # Device name (column 0)
                self.bgp_table.setItem(row, 0, QTableWidgetItem(device_name))
                
                # BGP Status (column 1) - Icon only, no text or background color
                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                if bgp_status == "Established":
                    bgp_status_item.setIcon(self.green_dot)
                    bgp_status_item.setToolTip("BGP Established")
                elif bgp_status == "Stopping":
                    bgp_status_item.setIcon(self.yellow_dot)
                    bgp_status_item.setToolTip("BGP Stopping")
                elif bgp_status in ["Idle", "Connect", "Active"]:
                    bgp_status_item.setIcon(self.orange_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                elif bgp_status in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured"]:
                    bgp_status_item.setIcon(self.red_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                else:
                    bgp_status_item.setIcon(self.orange_dot)
                    bgp_status_item.setToolTip(f"BGP {bgp_status}")
                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                self.bgp_table.setItem(row, 1, bgp_status_item)
                
                # Neighbor Type (column 2)
                self.bgp_table.setItem(row, 2, QTableWidgetItem(neighbor_type))
                
                # Neighbor IP (column 3)
                self.bgp_table.setItem(row, 3, QTableWidgetItem(neighbor_ip))
                
                # Source IP (column 4)
                source_ip = neighbor.get("source_ip", "")
                self.bgp_table.setItem(row, 4, QTableWidgetItem(source_ip))
                
                # Local AS (column 5)
                self.bgp_table.setItem(row, 5, QTableWidgetItem(str(neighbor.get("local_as", ""))))
                
                # Remote AS (column 6)
                self.bgp_table.setItem(row, 6, QTableWidgetItem(str(neighbor.get("remote_as", ""))))
                
                # State (column 7)
                self.bgp_table.setItem(row, 7, QTableWidgetItem(neighbor.get("state", "Idle")))
                
                # Routes (column 8)
                self.bgp_table.setItem(row, 8, QTableWidgetItem(str(neighbor.get("routes", 0))))
                
                # Route Pools (column 9) - Try to find device and get route pools
                route_pools_str = ""
                for iface, devices in self.main_window.all_devices.items():
                    for dev in devices:
                        if dev.get("Device Name") == device_name:
                            bgp_cfg = dev.get("bgp_config", {})
                            route_pools = bgp_cfg.get("route_pools", {}).get(neighbor_ip, [])
                            route_pools_str = ", ".join(route_pools) if route_pools else ""
                            break
                    if route_pools_str:
                        break
                pool_item = QTableWidgetItem(route_pools_str)
                pool_item.setToolTip(f"Attached route pools: {route_pools_str if route_pools_str else 'None'}")
                self.bgp_table.setItem(row, 9, pool_item)
                
                # Keepalive (column 10) - Default 30 seconds
                keepalive = neighbor.get("keepalive", "30")
                keepalive_item = QTableWidgetItem(str(keepalive))
                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                self.bgp_table.setItem(row, 10, keepalive_item)
                
                # Hold-time (column 11) - Default 90 seconds
                hold_time = neighbor.get("hold_time", "90")
                hold_time_item = QTableWidgetItem(str(hold_time))
                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                self.bgp_table.setItem(row, 11, hold_time_item)
        else:
            # Update from device configurations - one row per neighbor IP
            try:
                # Updating BGP table from device configurations
                
                # Get selected interfaces from server_tree (same logic as device table)
                selected_interfaces = set()
                if hasattr(self.main_window, 'server_tree') and self.main_window.server_tree:
                    tree = self.main_window.server_tree
                    for item in tree.selectedItems():
                        parent = item.parent()
                        if parent:
                            tg_id = parent.text(0).strip()
                            port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                            selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
                
                # Using selected interfaces or all devices
                
                self.bgp_table.setRowCount(0)
                
                bgp_device_count = 0
                # Use same filtering logic as device table - show only selected interfaces
                interfaces_to_show = selected_interfaces if selected_interfaces else list(self.main_window.all_devices.keys())
                for iface in interfaces_to_show:
                    # Check both new format and old format for backward compatibility
                    devices = self.main_window.all_devices.get(iface, [])
                    if not devices:
                        # Try old format with "Port:" and bullet
                        old_format = iface.replace(" - ", " - Port: • ")
                        devices = self.main_window.all_devices.get(old_format, [])
                    if not devices:
                        continue
                    for device in devices:
                        if "protocols" in device and "BGP" in device["protocols"]:
                            # Handle both old format (dict) and new format (list)
                            if isinstance(device["protocols"], dict):
                                bgp_config = device["protocols"]["BGP"]
                            else:
                                bgp_config = device.get("bgp_config", {})
                            
                            device_name = device.get("Device Name", "")
                            bgp_device_count += 1
                            
                            # Check if device is marked for removal
                            is_marked_for_removal = bgp_config.get("_marked_for_removal", False)
                            if is_marked_for_removal:
                                # Still show the device in the table but mark it as pending removal
                                pass
                            
                            # Get IPv4 neighbor IPs
                            ipv4_neighbors = bgp_config.get("bgp_neighbor_ipv4", "")
                            ipv4_ips = [ip.strip() for ip in ipv4_neighbors.split(",") if ip.strip()] if ipv4_neighbors else []
                            
                            # Get IPv6 neighbor IPs
                            ipv6_neighbors = bgp_config.get("bgp_neighbor_ipv6", "")
                            ipv6_ips = [ip.strip() for ip in ipv6_neighbors.split(",") if ip.strip()] if ipv6_neighbors else []
                            
                            # Create rows for IPv4 neighbors
                            for ipv4_ip in ipv4_ips:
                                row = self.bgp_table.rowCount()
                                self.bgp_table.insertRow(row)
                                
                                # Device name (column 0) - show status for removal
                                display_name = f"{device_name} (Pending Removal)" if is_marked_for_removal else device_name
                                self.bgp_table.setItem(row, 0, QTableWidgetItem(display_name))
                                
                                # BGP Status (column 1) - Icon only, no text or background color
                                bgp_state = self._get_bgp_neighbor_state(device.get("device_id"), ipv4_ip, device)
                                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                                if bgp_state == "Established":
                                    bgp_status_item.setIcon(self.green_dot)
                                    bgp_status_item.setToolTip("BGP Established")
                                elif bgp_state in ["Idle", "Connect", "Active"]:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif bgp_state in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured", "Device Not Started"]:
                                    bgp_status_item.setIcon(self.red_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif "No Gateway" in bgp_state:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(bgp_state)
                                else:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                                self.bgp_table.setItem(row, 1, bgp_status_item)
                                
                                # Neighbor Type (column 2)
                                self.bgp_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                
                                # Neighbor IP (column 3)
                                self.bgp_table.setItem(row, 3, QTableWidgetItem(ipv4_ip))
                                
                                # Source IP (column 4)
                                source_ipv4 = bgp_config.get("bgp_update_source_ipv4", "")
                                self.bgp_table.setItem(row, 4, QTableWidgetItem(source_ipv4))
                                
                                # Local AS (column 5)
                                self.bgp_table.setItem(row, 5, QTableWidgetItem(bgp_config.get("bgp_asn", "")))
                                
                                # Remote AS (column 6)
                                self.bgp_table.setItem(row, 6, QTableWidgetItem(bgp_config.get("bgp_remote_asn", "")))
                                
                                # State (column 7) - get real BGP state
                                self.bgp_table.setItem(row, 7, QTableWidgetItem(bgp_state))
                                
                                # Routes (column 8)
                                self.bgp_table.setItem(row, 8, QTableWidgetItem("0"))
                                
                                # Route Pools (column 9) - show attached pool names
                                route_pools = bgp_config.get("route_pools", {}).get(ipv4_ip, [])
                                pool_names = ", ".join(route_pools) if route_pools else ""
                                pool_item = QTableWidgetItem(pool_names)
                                pool_item.setToolTip(f"Attached route pools: {pool_names if pool_names else 'None'}")
                                self.bgp_table.setItem(row, 9, pool_item)
                                
                                # Keepalive (column 10) - Default 30 seconds
                                keepalive = bgp_config.get("bgp_keepalive", "30")
                                keepalive_item = QTableWidgetItem(str(keepalive))
                                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                                self.bgp_table.setItem(row, 10, keepalive_item)
                                
                                # Hold-time (column 11) - Default 90 seconds
                                hold_time = bgp_config.get("bgp_hold_time", "90")
                                hold_time_item = QTableWidgetItem(str(hold_time))
                                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                                self.bgp_table.setItem(row, 11, hold_time_item)
                            
                            # Create rows for IPv6 neighbors
                            for ipv6_ip in ipv6_ips:
                                row = self.bgp_table.rowCount()
                                self.bgp_table.insertRow(row)
                                
                                # Device name (column 0) - show status for removal
                                display_name = f"{device_name} (Pending Removal)" if is_marked_for_removal else device_name
                                self.bgp_table.setItem(row, 0, QTableWidgetItem(display_name))
                                
                                # BGP Status (column 1) - Icon only, no text or background color
                                bgp_state = self._get_bgp_neighbor_state(device.get("device_id"), ipv6_ip, device)
                                bgp_status_item = QTableWidgetItem("")  # Empty text, icon only
                                if bgp_state == "Established":
                                    bgp_status_item.setIcon(self.green_dot)
                                    bgp_status_item.setToolTip("BGP Established")
                                elif bgp_state in ["Idle", "Connect", "Active"]:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif bgp_state in ["Unknown", "Unknown (No Gateway)", "Gateway Not Configured", "Device Not Configured", "Device Not Started"]:
                                    bgp_status_item.setIcon(self.red_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                elif "No Gateway" in bgp_state:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(bgp_state)
                                else:
                                    bgp_status_item.setIcon(self.orange_dot)
                                    bgp_status_item.setToolTip(f"BGP {bgp_state}")
                                bgp_status_item.setFlags(bgp_status_item.flags() & ~Qt.ItemIsEditable)
                                self.bgp_table.setItem(row, 1, bgp_status_item)
                                
                                # Neighbor Type (column 2)
                                self.bgp_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                                
                                # Neighbor IP (column 3)
                                self.bgp_table.setItem(row, 3, QTableWidgetItem(ipv6_ip))
                                
                                # Source IP (column 4)
                                source_ipv6 = bgp_config.get("bgp_update_source_ipv6", "")
                                self.bgp_table.setItem(row, 4, QTableWidgetItem(source_ipv6))
                                
                                # Local AS (column 5)
                                self.bgp_table.setItem(row, 5, QTableWidgetItem(bgp_config.get("bgp_asn", "")))
                                
                                # Remote AS (column 6)
                                self.bgp_table.setItem(row, 6, QTableWidgetItem(bgp_config.get("bgp_remote_asn", "")))
                                
                                # State (column 7) - get real BGP state
                                self.bgp_table.setItem(row, 7, QTableWidgetItem(bgp_state))
                                
                                # Routes (column 8)
                                self.bgp_table.setItem(row, 8, QTableWidgetItem("0"))
                                
                                # Route Pools (column 9) - show attached pool names
                                route_pools = bgp_config.get("route_pools", {}).get(ipv6_ip, [])
                                pool_names = ", ".join(route_pools) if route_pools else ""
                                pool_item = QTableWidgetItem(pool_names)
                                pool_item.setToolTip(f"Attached route pools: {pool_names if pool_names else 'None'}")
                                self.bgp_table.setItem(row, 9, pool_item)
                                
                                # Keepalive (column 10) - Default 30 seconds
                                keepalive = bgp_config.get("bgp_keepalive", "30")
                                keepalive_item = QTableWidgetItem(str(keepalive))
                                keepalive_item.setToolTip("BGP Keepalive timer in seconds (default: 30)")
                                self.bgp_table.setItem(row, 10, keepalive_item)
                                
                                # Hold-time (column 11) - Default 90 seconds
                                hold_time = bgp_config.get("bgp_hold_time", "90")
                                hold_time_item = QTableWidgetItem(str(hold_time))
                                hold_time_item.setToolTip("BGP Hold-time timer in seconds (default: 90)")
                                self.bgp_table.setItem(row, 11, hold_time_item)
                
                # BGP table updated
            except Exception as e:
                print(f"Error updating BGP table: {e}")
    
    def update_ospf_table(self):
        """Update OSPF table with data from devices."""
        # Auto-start OSPF monitoring if we have OSPF devices and monitoring is not active
        if not self.ospf_monitoring_active:
            has_ospf_devices = False
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    device_protocols = device.get("protocols", [])
                    if isinstance(device_protocols, str):
                        try:
                            import json
                            device_protocols = json.loads(device_protocols)
                        except:
                            device_protocols = []
                    
                    if "OSPF" in device_protocols:
                        has_ospf_devices = True
                        break
                if has_ospf_devices:
                    break
            
            if has_ospf_devices:
                print("[OSPF AUTO-START] Auto-starting OSPF monitoring for existing OSPF devices")
                self.start_ospf_monitoring()
            
            # Auto-start ISIS monitoring if ISIS devices exist
            isis_devices_exist = any(
                device.get("protocols") and "IS-IS" in device.get("protocols", {})
                for devices in self.main_window.all_devices.values()
                for device in devices
            )
            if isis_devices_exist:
                print("[ISIS AUTO-START] Auto-starting ISIS monitoring for existing ISIS devices")
                self.start_isis_monitoring()
        
        try:
            # Get selected interfaces from server_tree (same logic as device table)
            selected_interfaces = set()
            if hasattr(self.main_window, 'server_tree') and self.main_window.server_tree:
                tree = self.main_window.server_tree
                for item in tree.selectedItems():
                    parent = item.parent()
                    if parent:
                        tg_id = parent.text(0).strip()
                        port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                        selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
            
            # print(f"[DEBUG OSPF TABLE] Selected interfaces: {selected_interfaces}")
            # if not selected_interfaces:
            #     print(f"[DEBUG OSPF TABLE] No interfaces selected, showing all devices")
            
            self.ospf_table.setRowCount(0)
            
            # Use same filtering logic as device table - show only selected interfaces
            interfaces_to_show = selected_interfaces if selected_interfaces else list(self.main_window.all_devices.keys())
            for iface in interfaces_to_show:
                # Check both new format and old format for backward compatibility
                devices = self.main_window.all_devices.get(iface, [])
                if not devices:
                    # Try old format with "Port:" and bullet
                    old_format = iface.replace(" - ", " - Port: • ")
                    devices = self.main_window.all_devices.get(old_format, [])
                if not devices:
                    continue
                    
                for device in devices:
                    # Check for OSPF in protocols list (new format) or ospf_config field
                    has_ospf = False
                    ospf_config = {}
                    
                    # Check new format: protocols is a list like ["BGP", "OSPF"]
                    if "protocols" in device and isinstance(device["protocols"], list) and "OSPF" in device["protocols"]:
                        has_ospf = True
                        ospf_config = device.get("ospf_config", {})
                    # Check old format: protocols is a dict like {"OSPF": {...}}
                    elif "protocols" in device and isinstance(device["protocols"], dict) and "OSPF" in device["protocols"]:
                        has_ospf = True
                        ospf_config = device["protocols"]["OSPF"]
                    
                    if has_ospf:
                        device_name = device.get("Device Name", "")
                        device_id = device.get("device_id", "")
                        
                        # Get the actual OSPF interface name from ospf_config
                        ospf_interface = "Unknown"
                        if ospf_config and "interface" in ospf_config:
                            ospf_interface = ospf_config["interface"]
                            
                            # If VLAN is 0, use the physical interface name instead of vlan0
                            vlan_id = device.get("VLAN", "0")
                            if vlan_id == "0" and ospf_interface.startswith("vlan"):
                                # Extract physical interface from the iface string (e.g., "TG 1 - ens4np0" -> "ens4np0")
                                iface_parts = iface.split(" - ")
                                if len(iface_parts) >= 2:
                                    ospf_interface = iface_parts[1]  # Get the physical interface name
                        
                        # Get OSPF configuration flags
                        ipv4_enabled = ospf_config.get("ipv4_enabled", False) if ospf_config else False
                        ipv6_enabled = ospf_config.get("ipv6_enabled", False) if ospf_config else False
                        
                        # Try to get actual OSPF status from database via server
                        ospf_status_data = {}
                        ospf_data = {}  # Initialize ospf_data to avoid NameError
                        try:
                            import requests
                            server_url = self.get_server_url(silent=True)
                            if server_url and device_id:
                                response = requests.get(f"{server_url}/api/ospf/status/database/{device_id}", timeout=5)
                                if response.status_code == 200:
                                    data = response.json()
                                    ospf_data = data.get("ospf_status", {})
                                    if ospf_data:
                                        neighbors = ospf_data.get("neighbors", [])
                                        # Group neighbors by type (IPv4/IPv6)
                                        for neighbor in neighbors:
                                            neighbor_type = neighbor.get("type", "Unknown")
                                            if neighbor_type not in ospf_status_data:
                                                ospf_status_data[neighbor_type] = []
                                            ospf_status_data[neighbor_type].append(neighbor)
                        except Exception:
                            # Don't print debug errors to reduce spam
                            pass
                        
                        # Create separate rows for IPv4 and IPv6 OSPF if enabled
                        protocols_to_show = []
                        if ipv4_enabled:
                            protocols_to_show.append("IPv4")
                        if ipv6_enabled:
                            protocols_to_show.append("IPv6")
                        
                        # If no protocols are explicitly enabled, show as Unknown
                        if not protocols_to_show:
                            protocols_to_show = ["Unknown"]
                        
                        for protocol_type in protocols_to_show:
                            # Get status for this protocol type
                            neighbors_for_type = ospf_status_data.get(protocol_type, [])
                            
                            # Determine OSPF status based on neighbors or overall OSPF status
                            if neighbors_for_type:
                                # Use the first neighbor's information for this type
                                neighbor = neighbors_for_type[0]
                                neighbor_id = neighbor.get("neighbor_id", "N/A")
                                state = neighbor.get("state", "Down")
                                priority = neighbor.get("priority", "1")
                                dead_timer = neighbor.get("dead_time", "40")
                                ospf_status = "Up" if any(state.startswith(s) for s in ["Full", "2-Way"]) else "Down"
                            else:
                                # No neighbors found for this type - check if OSPF is running
                                neighbor_id = "N/A"
                                state = "No Neighbors"
                                priority = "N/A"
                                dead_timer = "N/A"
                                
                                # Check if OSPF is running for this protocol type
                                if protocol_type == "IPv4" and ospf_data.get("ospf_ipv4_running", False):
                                    ospf_status = "Running"
                                elif protocol_type == "IPv6" and ospf_data.get("ospf_ipv6_running", False):
                                    ospf_status = "Running"
                                else:
                                    ospf_status = "Down"
                            
                            # Get uptime for this protocol type
                            uptime = "N/A"
                            if neighbors_for_type:
                                # Use neighbor uptime if available
                                neighbor = neighbors_for_type[0]
                                uptime = neighbor.get("up_time", "N/A")
                            else:
                                # Fall back to process uptime if no neighbors
                                if protocol_type == "IPv4" and ospf_data.get("ospf_ipv4_uptime"):
                                    uptime = ospf_data.get("ospf_ipv4_uptime")
                                elif protocol_type == "IPv6" and ospf_data.get("ospf_ipv6_uptime"):
                                    uptime = ospf_data.get("ospf_ipv6_uptime")
                            
                            row = self.ospf_table.rowCount()
                            self.ospf_table.insertRow(row)
                            
                            self.ospf_table.setItem(row, 0, QTableWidgetItem(device_name))  # Device
                            # Set OSPF status icon instead of text
                            self.set_ospf_status_icon(row, ospf_status, f"OSPF {ospf_status}")
                            self.ospf_table.setItem(row, 2, QTableWidgetItem(protocol_type)) # Neighbor Type
                            self.ospf_table.setItem(row, 3, QTableWidgetItem(ospf_interface)) # Interface
                            self.ospf_table.setItem(row, 4, QTableWidgetItem(neighbor_id))   # Neighbor ID
                            self.ospf_table.setItem(row, 5, QTableWidgetItem(state))         # State
                            self.ospf_table.setItem(row, 6, QTableWidgetItem(priority))     # Priority
                            self.ospf_table.setItem(row, 7, QTableWidgetItem(dead_timer))   # Dead Timer
                            self.ospf_table.setItem(row, 8, QTableWidgetItem(uptime))        # Uptime
        except Exception as e:
            print(f"Error updating OSPF table: {e}")
    
    def update_isis_table(self):
        """Update ISIS table with data from devices and ISIS status from database."""
        try:
            print(f"DEBUG ISIS TABLE: Starting update_isis_table")
            print(f"DEBUG ISIS TABLE: all_devices keys: {list(self.main_window.all_devices.keys())}")
            
            # Get selected interfaces from server_tree (same logic as device table)
            selected_interfaces = set()
            tree = self.main_window.server_tree
            for item in tree.selectedItems():
                parent = item.parent()
                if parent:
                    tg_id = parent.text(0).strip()
                    port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                    selected_interfaces.add(f"{tg_id} - {port_name}")  # Match server tree format
            
            # print(f"[DEBUG ISIS TABLE] Selected interfaces: {selected_interfaces}")
            # if not selected_interfaces:
            #     print(f"[DEBUG ISIS TABLE] No interfaces selected, showing all devices")
            
            self.isis_table.setRowCount(0)
            
            # Use same filtering logic as device table - show only selected interfaces
            interfaces_to_show = selected_interfaces if selected_interfaces else list(self.main_window.all_devices.keys())
            for iface in interfaces_to_show:
                # Check both new format and old format for backward compatibility
                devices = self.main_window.all_devices.get(iface, [])
                if not devices:
                    # Try old format with "Port:" and bullet
                    old_format = iface.replace(" - ", " - Port: • ")
                    devices = self.main_window.all_devices.get(old_format, [])
                if not devices:
                    continue
                    
                for device in devices:
                    # Check if device has IS-IS protocol configured
                    device_protocols = device.get("protocols", [])
                    if isinstance(device_protocols, list) and "IS-IS" in device_protocols:
                        # New format: protocols is a list, config is in separate field
                        isis_config = device.get("is_is_config", {})
                    elif isinstance(device_protocols, dict) and "IS-IS" in device_protocols:
                        # Old format: protocols is a dict
                        isis_config = device_protocols["IS-IS"]
                    else:
                        continue  # Skip devices without IS-IS
                    
                    device_name = device.get("Device Name", "")
                    device_id = device.get("device_id", "")
                    
                    # Check if ISIS is marked for removal
                    is_marked_for_removal = isinstance(isis_config, dict) and isis_config.get("_marked_for_removal", False)
                    
                    print(f"DEBUG ISIS TABLE: Device {device_name}, isis_config={isis_config}")
                    
                    # Get ISIS status from database
                    isis_status_data = self._get_isis_status_from_database(device_id)
                    
                    # Get ISIS configuration flags
                    ipv4_enabled = isis_config.get("ipv4_enabled", False) if isis_config else False
                    ipv6_enabled = isis_config.get("ipv6_enabled", False) if isis_config else False
                    
                    # Get device VLAN interface from ISIS config
                    device_interface = isis_config.get("interface", iface)
                    # If interface is not in config, try to construct from VLAN
                    if not device_interface or device_interface == iface:
                        device_vlan = device.get("VLAN", "0")
                        if device_vlan and device_vlan != "0":
                            device_interface = f"vlan{device_vlan}"
                        else:
                            device_interface = iface
                    
                    print(f"DEBUG ISIS TABLE: Device {device_name}, isis_config={isis_config}, device_interface={device_interface}, ipv4_enabled={ipv4_enabled}, ipv6_enabled={ipv6_enabled}")
                    
                    # Create rows for each ISIS neighbor or device status
                    if isis_status_data and isis_status_data.get("neighbors") and not is_marked_for_removal:
                        # Get neighbors from database
                        neighbors = isis_status_data.get("neighbors", [])
                        
                        # Create separate rows for IPv4 and IPv6 (similar to OSPF)
                        protocols_to_show = []
                        if ipv4_enabled:
                            protocols_to_show.append("IPv4")
                        if ipv6_enabled:
                            protocols_to_show.append("IPv6")
                        
                        # If no protocols are explicitly enabled, show both or Unknown
                        if not protocols_to_show:
                            # Check if neighbor has IPv4 or IPv6 addresses
                            if neighbors:
                                neighbor = neighbors[0]
                                if neighbor.get("ipv4_address"):
                                    protocols_to_show.append("IPv4")
                                if neighbor.get("ipv6_global") or neighbor.get("ipv6_link_local"):
                                    protocols_to_show.append("IPv6")
                            if not protocols_to_show:
                                protocols_to_show = ["Unknown"]
                        
                        # Show each protocol type (IPv4/IPv6) as separate row
                        for protocol_type in protocols_to_show:
                            # Get neighbor info for this protocol type
                            neighbor = neighbors[0] if neighbors else {}
                            
                            # Determine ISIS status based on neighbor state
                            isis_status = neighbor.get("state", "Down")
                            if isis_status.lower() in ["up", "established"]:
                                isis_status_display = "Up"
                            elif isis_status.lower() in ["down"]:
                                isis_status_display = "Down"
                            else:
                                isis_status_display = isis_status
                            
                            # Get neighbor info based on protocol type
                            if protocol_type == "IPv4":
                                neighbor_addr = neighbor.get("ipv4_address", "N/A")
                            elif protocol_type == "IPv6":
                                neighbor_addr = neighbor.get("ipv6_global", neighbor.get("ipv6_link_local", "N/A"))
                            else:
                                neighbor_addr = "N/A"
                            
                            row = self.isis_table.rowCount()
                            self.isis_table.insertRow(row)
                            
                            # Device
                            self.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                            
                            # ISIS Status (with icon)
                            self.set_isis_status_icon(row, isis_status_display, f"ISIS {isis_status_display}")
                            
                            # Neighbor Type (IPv4 or IPv6)
                            self.isis_table.setItem(row, 2, QTableWidgetItem(protocol_type))
                            
                            # Interface - show device VLAN interface
                            self.isis_table.setItem(row, 3, QTableWidgetItem(device_interface))
                            
                            # ISIS Area
                            area = neighbor.get("area", isis_config.get("area_id", ""))
                            self.isis_table.setItem(row, 4, QTableWidgetItem(area))
                            
                            # Level
                            level = neighbor.get("level", isis_config.get("level", "Level-2"))
                            self.isis_table.setItem(row, 5, QTableWidgetItem(level))
                            
                            # ISIS Net
                            isis_net = neighbor.get("net", isis_config.get("area_id", ""))
                            self.isis_table.setItem(row, 6, QTableWidgetItem(isis_net))
                    else:
                        # No neighbors found or marked for removal, show device status
                        row = self.isis_table.rowCount()
                        self.isis_table.insertRow(row)
                        
                        # Device
                        self.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                        
                        # ISIS Status (with icon)
                        if is_marked_for_removal:
                            isis_status = "Marked for Removal"
                            self.set_isis_status_icon(row, "Marked for Removal", "ISIS Marked for Removal")
                        else:
                            isis_status = "Running" if isis_status_data and isis_status_data.get("isis_running") else "Down"
                            self.set_isis_status_icon(row, isis_status, f"ISIS {isis_status}")
                        
                        # Neighbor Type
                        if is_marked_for_removal:
                            self.isis_table.setItem(row, 2, QTableWidgetItem("Pending Removal"))
                        else:
                            # Show separate rows for IPv4 and IPv6 if enabled
                            ipv4_enabled = isis_config.get("ipv4_enabled", False) if isis_config else False
                            ipv6_enabled = isis_config.get("ipv6_enabled", False) if isis_config else False
                            
                            if ipv4_enabled or ipv6_enabled:
                                # Show protocol type based on enabled flags
                                if ipv4_enabled and ipv6_enabled:
                                    # Show first row as IPv4, will create another row for IPv6 below
                                    self.isis_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                elif ipv4_enabled:
                                    self.isis_table.setItem(row, 2, QTableWidgetItem("IPv4"))
                                elif ipv6_enabled:
                                    self.isis_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                                else:
                                    self.isis_table.setItem(row, 2, QTableWidgetItem("No Neighbors"))
                            else:
                                self.isis_table.setItem(row, 2, QTableWidgetItem("No Neighbors"))
                        
                        # Interface - show device VLAN interface instead of physical interface
                        device_interface = isis_config.get("interface", iface)
                        # If interface is not in config, try to construct from VLAN
                        if not device_interface or device_interface == iface:
                            device_vlan = device.get("VLAN", "0")
                            if device_vlan and device_vlan != "0":
                                device_interface = f"vlan{device_vlan}"
                            else:
                                device_interface = iface
                        print(f"DEBUG ISIS: Device {device_name}, iface={iface}, device_interface={device_interface}")
                        self.isis_table.setItem(row, 3, QTableWidgetItem(device_interface))
                        
                        # ISIS Area (for first row)
                        self.isis_table.setItem(row, 4, QTableWidgetItem(isis_config.get("area_id", "")))
                        
                        # Level (for first row)
                        self.isis_table.setItem(row, 5, QTableWidgetItem(isis_config.get("level", "Level-2")))
                        
                        # ISIS Net (for first row)
                        self.isis_table.setItem(row, 6, QTableWidgetItem(isis_config.get("area_id", "")))
                        
                        # If both IPv4 and IPv6 are enabled, create a second row for IPv6
                        if ipv4_enabled and ipv6_enabled and not is_marked_for_removal:
                            row = self.isis_table.rowCount()
                            self.isis_table.insertRow(row)
                            
                            # Device
                            self.isis_table.setItem(row, 0, QTableWidgetItem(device_name))
                            
                            # ISIS Status (with icon) - same as IPv4 row
                            isis_status = "Running" if isis_status_data and isis_status_data.get("isis_running") else "Down"
                            self.set_isis_status_icon(row, isis_status, f"ISIS {isis_status}")
                            
                            # Neighbor Type - IPv6
                            self.isis_table.setItem(row, 2, QTableWidgetItem("IPv6"))
                            
                            # Interface
                            self.isis_table.setItem(row, 3, QTableWidgetItem(device_interface))
                            
                            # ISIS Area
                            self.isis_table.setItem(row, 4, QTableWidgetItem(isis_config.get("area_id", "")))
                            
                            # Level
                            self.isis_table.setItem(row, 5, QTableWidgetItem(isis_config.get("level", "Level-2")))
                            
                            # ISIS Net
                            self.isis_table.setItem(row, 6, QTableWidgetItem(isis_config.get("area_id", "")))
                    
        except Exception as e:
            print(f"Error updating ISIS table: {e}")

    def set_isis_status_icon(self, row, status, tooltip):
        """Set ISIS status icon for a table row."""
        try:
            def load_icon(filename: str) -> QIcon:
                return qicon("resources", f"icons/{filename}")
            
            # Determine icon based on ISIS status
            if status.lower() in ["up", "running", "established"]:
                icon = load_icon("green_dot.png")
            elif status.lower() in ["down", "stopped", "idle"]:
                icon = load_icon("red_dot.png")
            elif status.lower() in ["stopping"]:
                icon = load_icon("yellow_dot.png")
            elif status.lower() in ["marked for removal"]:
                icon = load_icon("orange_dot.png")
            else:
                icon = load_icon("orange_dot.png")
            
            # Create item with icon
            item = QTableWidgetItem()
            item.setIcon(icon)
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignCenter)
            
            # Set the item in the ISIS Status column (column 1)
            self.isis_table.setItem(row, 1, item)
            
        except Exception as e:
            print(f"Error setting ISIS status icon: {e}")
            # Fallback to text
            self.isis_table.setItem(row, 1, QTableWidgetItem(status))

    def _get_isis_status_from_database(self, device_id: str) -> dict:
        """Get ISIS status from database for a device."""
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url or not device_id:
                return {}
            
            # Get device information from database
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=1)
            if response.status_code == 200:
                device_data = response.json()
                
                # Extract ISIS status information
                isis_status = {
                    "isis_running": device_data.get('isis_running', False),
                    "isis_established": device_data.get('isis_established', False),
                    "isis_state": device_data.get('isis_state', 'Unknown'),
                    "neighbors": []
                }
                
                # Parse ISIS neighbors if available
                isis_neighbors = device_data.get('isis_neighbors')
                if isis_neighbors:
                    try:
                        if isinstance(isis_neighbors, str):
                            import json
                            neighbors_data = json.loads(isis_neighbors)
                        else:
                            neighbors_data = isis_neighbors
                        
                        if isinstance(neighbors_data, list):
                            for neighbor in neighbors_data:
                                neighbor_info = {
                                    "state": neighbor.get("state", "Down"),
                                    "type": neighbor.get("type", "Unknown"),
                                    "interface": neighbor.get("interface", ""),
                                    "area": neighbor.get("area", ""),
                                    "level": neighbor.get("level", ""),
                                    "net": neighbor.get("net", ""),
                                    "system_id": neighbor.get("system_id", ""),
                                    "priority": neighbor.get("priority", ""),
                                    "uptime": neighbor.get("uptime", "")
                                }
                                isis_status["neighbors"].append(neighbor_info)
                    except Exception as e:
                        print(f"Error parsing ISIS neighbors: {e}")
                
                return isis_status
            else:
                return {}
                
        except Exception:
            # Don't print debug errors to reduce spam
            return {}

    # ---------- Utilities ----------

    def _on_device_operation_progress(self, device_name, status_message):
        """Handle progress updates from device operation worker."""
        print(f"[DEVICE OPERATION] {device_name}: {status_message}")
    
    def _on_device_status_updated(self, row, status, tooltip):
        """Update device status in table from worker thread."""
        try:
            # Use the unified set_status_icon function to ensure consistent icon usage
            # Show temporary status - ARP status will be updated by database refresh
            if status == "Running":
                self.set_status_icon(row, resolved=False, status_text=tooltip, device_status=status)
            elif status == "Stopped":
                self.set_status_icon(row, resolved=False, status_text=tooltip, device_status=status)
            else:
                self.set_status_icon(row, resolved=False, status_text=tooltip, device_status=status)
        except Exception as e:
            logging.error(f"[DEVICE STATUS UPDATE ERROR] Row {row}: {e}")
    
    def _on_device_operation_finished(self, results, successful_count, failed_count, selected_rows):
        """Handle completion of device operation worker."""
        # Print results to console
        if results:
            print(f"\n{'='*60}")
            print(f"DEVICE OPERATION RESULTS: {successful_count} successful, {failed_count} failed")
            print(f"{'='*60}")
            for result in results:
                print(f"  {result}")
            print(f"{'='*60}\n")
        
        # Refresh protocol tabs if needed (deferred to avoid UI hang)
        if successful_count > 0:
            QTimer.singleShot(100, lambda: self._refresh_protocols_for_selected_devices(selected_rows))
            # Refresh device table from database for all operations to get current ARP status
            # This ensures ARP status is updated after start/stop/apply operations
            QTimer.singleShot(200, lambda: self._refresh_device_table_from_database(selected_rows))
        
        # Clear the operation type flag
        if hasattr(self, '_current_operation_type'):
            delattr(self, '_current_operation_type')
    
    def _refresh_device_table_from_database(self, selected_rows):
        """Refresh device table status from database for selected rows."""
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return
            
            for row in selected_rows:
                try:
                    device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
                    
                    # Find device in all_devices data structure
                    device_info = None
                    for iface, devices in self.main_window.all_devices.items():
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                device_info = device
                                break
                        if device_info:
                            break
                    
                    if device_info and device_info.get("device_id"):
                        device_id = device_info.get("device_id")
                        
                        # Get device data from database
                        response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=3)
                        if response.status_code == 200:
                            device_data = response.json()
                            
                            # Update device status
                            device_status = device_data.get('status', 'Unknown')
                            if device_status != device_info.get("Status", ""):
                                device_info["Status"] = device_status
                                # Update status column in table
                                status_item = self.devices_table.item(row, self.COL["Status"])
                                if status_item:
                                    status_item.setText(device_status)
                            
                            # Update ARP status and colors
                            arp_ipv4_raw = device_data.get('arp_ipv4_resolved', 0)
                            arp_ipv6_raw = device_data.get('arp_ipv6_resolved', 0)
                            arp_gateway_raw = device_data.get('arp_gateway_resolved', 0)
                            
                            print(f"[DATABASE DEBUG] {device_name} - Raw ARP values: IPv4={arp_ipv4_raw}, IPv6={arp_ipv6_raw}, Gateway={arp_gateway_raw}")
                            
                            arp_results = {
                                "ipv4_resolved": bool(arp_ipv4_raw),
                                "ipv6_resolved": bool(arp_ipv6_raw),
                                "gateway_resolved": bool(arp_gateway_raw),
                                "ipv4_status": "Resolved" if arp_ipv4_raw else "Failed",
                                "ipv6_status": "Resolved" if arp_ipv6_raw else "Failed",
                                "gateway_status": "Resolved" if arp_gateway_raw else "Failed",
                                "overall_status": device_data.get('arp_status', 'Unknown')
                            }
                            
                            print(f"[DATABASE DEBUG] {device_name} - Processed ARP values: IPv4={arp_results['ipv4_resolved']}, IPv6={arp_results['ipv6_resolved']}, Gateway={arp_results['gateway_resolved']}")
                            
                            # Debug: Print device and ARP status for troubleshooting
                            print(f"[DEVICE REFRESH] {device_name} Status: {device_status}, ARP: IPv4={arp_results['ipv4_resolved']}, IPv6={arp_results['ipv6_resolved']}, Gateway={arp_results['gateway_resolved']}")
                            
                            # Update IP colors based on ARP status
                            self.set_status_icon_with_individual_ips(row, arp_results)
                            
                            # Update overall status icon based on device status first, then ARP status
                            if device_status == "Running":
                                # Device is running - check ARP status
                                # Show green dot only when both IPv4 and IPv6 ARP are resolved
                                overall_resolved = arp_results["ipv4_resolved"] and arp_results["ipv6_resolved"]
                                self.set_status_icon(row, resolved=overall_resolved, status_text=arp_results["overall_status"], device_status=device_status)
                            else:
                                # Device is stopped or unknown status - pass device status to set_status_icon
                                self.set_status_icon(row, resolved=False, status_text=arp_results["overall_status"], device_status=device_status)
                            
                except Exception as e:
                    print(f"[DEVICE REFRESH] Error refreshing row {row}: {e}")
                    
        except Exception as e:
            print(f"[DEVICE REFRESH] Error refreshing device table: {e}")
    
    def _on_arp_operation_progress(self, device_name, status_message):
        """Handle progress updates from ARP operation worker."""
        print(f"[ARP OPERATION] {device_name}: {status_message}")
    
    def _on_arp_status_updated(self, row, arp_resolved, status):
        """Update device ARP status in table from worker thread."""
        try:
            self.update_device_status_icon(row, arp_resolved, status)
        except Exception as e:
            logging.error(f"[ARP STATUS UPDATE ERROR] Row {row}: {e}")
    
    def _on_arp_operation_finished(self, results, successful_count, failed_count, selected_rows):
        """Handle completion of ARP operation worker."""
        # Print results to console
        if results:
            print(f"\n{'='*60}")
            print(f"ARP OPERATION RESULTS: {successful_count} successful, {failed_count} failed")
            print(f"{'='*60}")
            for result in results:
                print(f"  {result}")
            print(f"{'='*60}\n")
        
        # Only restore status icons for devices that were actually processed (have results)
        print(f"[DEBUG ARP FINISHED] Processing {len(selected_rows)} selected rows: {selected_rows}")
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            print(f"[DEBUG ARP FINISHED] Processing row {row}, device: {device_name}")
            status_item = self.devices_table.item(row, self.COL["Status"])
            
            if status_item:
                # Find the corresponding result for this device
                device_result = None
                for result in results:
                    if device_name in result:
                        device_result = result
                        break
                
                # Only update status if this device was actually processed (has a result)
                if device_result:
                    print(f"[DEBUG ARP FINISHED] Updating status for {device_name} - has result: {device_result}")
                    # Set status based on result
                    if "✅" in device_result:
                        # ARP successful - green dot
                        status_item.setText("Running")
                        status_item.setIcon(self.green_dot)
                        status_item.setToolTip("Device running - ARP resolved")
                    else:
                        # ARP failed - orange dot (device running but ARP issues)
                        status_item.setText("Running")
                        status_item.setIcon(self.orange_dot)
                        status_item.setToolTip("Device running - ARP issues detected")
                else:
                    print(f"[DEBUG ARP FINISHED] Skipping status update for {device_name} - no result found (not processed)")
        
        # Clear the pending ARP rows now that the operation is finished
        if hasattr(self, '_pending_arp_rows'):
            delattr(self, '_pending_arp_rows')
            print(f"[DEBUG ARP FINISHED] Cleared _pending_arp_rows")
        
        # ARP results are now shown via color indicators in the UI
        # No popup needed since status is visible through colored dots and text
    
    def _refresh_protocols_for_selected_devices(self, selected_rows):
        """Refresh protocol tabs (BGP, OSPF, ISIS) for devices in selected rows (optimized, non-blocking)."""
        try:
            # Collect protocols that need refreshing
            protocols_to_refresh = set()
            
            for row in selected_rows:
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if not name_item:
                    continue
                
                device_name = name_item.text()
                
                # Find device in all_devices to check protocols
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            # Check which protocols this device has
                            if "protocols" in device:
                                device_protocols = device.get("protocols", {})
                                if isinstance(device_protocols, dict):
                                    protocols_to_refresh.update(device_protocols.keys())
                            break
            
            if not protocols_to_refresh:
                return  # Nothing to refresh
            
            # Refresh protocol tables in parallel using ThreadPoolExecutor (faster)
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = []
                
                if "BGP" in protocols_to_refresh:
                    futures.append(executor.submit(self._safe_update_bgp_table))
                
                if "OSPF" in protocols_to_refresh:
                    futures.append(executor.submit(self._safe_update_ospf_table))
                
                if "IS-IS" in protocols_to_refresh:
                    futures.append(executor.submit(self._safe_update_isis_table))
                
                # Wait for all refreshes to complete
                for future in as_completed(futures):
                    try:
                        future.result()  # Will raise exception if refresh failed
                    except Exception as e:
                        logging.error(f"[PROTOCOL REFRESH] Error in parallel refresh: {e}")
            
            print(f"[PROTOCOL REFRESH] Refreshed protocols: {', '.join(protocols_to_refresh)}")
        
        except Exception as e:
            logging.error(f"[PROTOCOL REFRESH ERROR] {e}")
    
    def _safe_update_bgp_table(self):
        """Safely update BGP table (for parallel execution)."""
        try:
            print("[PROTOCOL REFRESH] Refreshing BGP table...")
            self.update_bgp_table()
        except Exception as e:
            logging.error(f"[BGP REFRESH ERROR] {e}")
    
    def _safe_update_ospf_table(self):
        """Safely update OSPF table (for parallel execution)."""
        try:
            print("[PROTOCOL REFRESH] Refreshing OSPF table...")
            self.update_ospf_table()
        except Exception as e:
            logging.error(f"[OSPF REFRESH ERROR] {e}")
    
    def _safe_update_isis_table(self):
        """Safely update ISIS table (for parallel execution)."""
        try:
            print("[PROTOCOL REFRESH] Refreshing ISIS table...")
            self.update_isis_table()
        except Exception as e:
            logging.error(f"[ISIS REFRESH ERROR] {e}")

    def set_status_icon(self, row: int, resolved: bool, status_text: str = None, device_status: str = None):
        """Put a colored dot icon in the 'Status' column based on device status and ARP resolution."""
        col = self.COL["Status"]
        
        # Create item with icon only, no text
        item = QTableWidgetItem("")  # Empty text, icon only
        
        # Check device status first
        if device_status == "Stopped":
            # Device is stopped - show stop icon
            icon = self.stop_icon
            tooltip = "Device Stopped"
        elif device_status == "Running":
            # Device is running - check ARP status
            if resolved:
                # ARP successfully resolved - show ARP success icon
                icon = self.arp_success
                tooltip = status_text or "ARP Resolved"
            else:
                # ARP not resolved - show ARP fail icon
                # This provides clear indication that ARP needs attention
                icon = self.arp_fail
                tooltip = status_text or "ARP Failed"
        else:
            # Unknown or other device status - show orange
            icon = self.orange_dot
            tooltip = status_text or f"Status: {device_status or 'Unknown'}"
        
        item.setIcon(icon)
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(Qt.ItemIsEnabled)
        self.devices_table.setItem(row, col, item)

    def set_ospf_status_icon(self, row: int, ospf_status: str, status_text: str = None):
        """Set OSPF status icon in the OSPF Status column based on OSPF status."""
        col = 1  # OSPF Status column (0-indexed)
        
        # Create item with icon only, no text
        item = QTableWidgetItem("")  # Empty text, icon only
        
        # Determine icon based on OSPF status
        if ospf_status == "Up":
            icon = self.green_dot
            tooltip = status_text or "OSPF Up"
        elif ospf_status == "Running":
            icon = self.orange_dot
            tooltip = status_text or "OSPF Running (No Neighbors)"
        elif ospf_status == "Down":
            icon = self.red_dot
            tooltip = status_text or "OSPF Down"
        else:
            icon = self.red_dot
            tooltip = status_text or f"OSPF Status: {ospf_status}"
        
        item.setIcon(icon)
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(Qt.ItemIsEnabled)
        self.ospf_table.setItem(row, col, item)

    def set_status_icon_with_individual_ips(self, row: int, arp_results: dict):
        """Set individual IP colors based on detailed ARP results (does NOT update overall status icon)."""
        from PyQt5.QtGui import QColor
        
        # NOTE: We do NOT update the overall status icon here anymore
        # The overall status icon is updated by _on_arp_operation_finished
        # This method only handles individual IP color updates
        
        # Set individual IP colors
        orange_color = QColor(255, 165, 0)  # Orange color for failed IPs
        default_color = QColor(0, 0, 0)     # Default black color for resolved IPs
        
        # IPv4 column - Show orange if IPv4 ARP failed
        ipv4_item = self.devices_table.item(row, self.COL["IPv4"])
        if ipv4_item:
            ipv4_resolved = arp_results.get("ipv4_resolved", False)
            if not ipv4_resolved and ipv4_item.text().strip():
                ipv4_item.setForeground(orange_color)
                ipv4_item.setToolTip(f"IPv4 ARP failed: {arp_results.get('ipv4_status', 'Unknown')}")
            else:
                ipv4_item.setForeground(default_color)
                if ipv4_resolved:
                    ipv4_item.setToolTip("IPv4 ARP resolved")
                else:
                    ipv4_item.setToolTip("Device IPv4 address")
        
        # IPv6 column - Show orange if IPv6 ARP failed
        ipv6_item = self.devices_table.item(row, self.COL["IPv6"])
        if ipv6_item:
            ipv6_resolved = arp_results.get("ipv6_resolved", False)
            if not ipv6_resolved and ipv6_item.text().strip():
                ipv6_item.setForeground(orange_color)
                ipv6_item.setToolTip(f"IPv6 ARP failed: {arp_results.get('ipv6_status', 'Unknown')}")
            else:
                ipv6_item.setForeground(default_color)
                if ipv6_resolved:
                    ipv6_item.setToolTip("IPv6 ARP resolved")
                else:
                    ipv6_item.setToolTip("Device IPv6 address")
        
        # IPv4 Gateway column
        ipv4_gateway_item = self.devices_table.item(row, self.COL["IPv4 Gateway"])
        if ipv4_gateway_item:
            gateway_resolved = arp_results.get("gateway_resolved", False)
            if not gateway_resolved and ipv4_gateway_item.text().strip():
                ipv4_gateway_item.setForeground(orange_color)
                ipv4_gateway_item.setToolTip(f"Gateway ARP failed: {arp_results.get('gateway_status', 'Unknown')}")
            else:
                ipv4_gateway_item.setForeground(default_color)
                if gateway_resolved:
                    ipv4_gateway_item.setToolTip("Gateway ARP resolved")
                else:
                    ipv4_gateway_item.setToolTip("IPv4 Gateway address")
        
        # IPv6 Gateway column
        ipv6_gateway_item = self.devices_table.item(row, self.COL["IPv6 Gateway"])
        if ipv6_gateway_item:
            # IPv6 gateway uses ipv6_resolved status (shows orange when IPv6 ARP fails)
            ipv6_resolved = arp_results.get("ipv6_resolved", False)
            gateway_text = ipv6_gateway_item.text().strip()
            print(f"[ARP COLOR DEBUG] IPv6 Gateway: text='{gateway_text}', ipv6_resolved={ipv6_resolved}")
            if not ipv6_resolved and gateway_text:
                ipv6_gateway_item.setForeground(orange_color)
                ipv6_gateway_item.setToolTip(f"IPv6 ARP failed: {arp_results.get('ipv6_status', 'Unknown')}")
                print(f"[ARP COLOR DEBUG] IPv6 Gateway set to ORANGE")
            else:
                ipv6_gateway_item.setForeground(default_color)
                if ipv6_resolved:
                    ipv6_gateway_item.setToolTip("IPv6 ARP resolved")
                else:
                    ipv6_gateway_item.setToolTip("IPv6 Gateway address")
                print(f"[ARP COLOR DEBUG] IPv6 Gateway set to DEFAULT")

    def get_server_url(self, silent=False):
        if hasattr(self.main_window, "server_url") and self.main_window.server_url:
            # print(f"[DEBUG SERVER] Using main_window.server_url: {self.main_window.server_url}")
            return self.main_window.server_url

        # Fallback from tree selection
        main_window = self.window()
        if hasattr(main_window, "server_tree"):
            selected_items = main_window.server_tree.selectedItems()
            if selected_items:
                selected_item = selected_items[0]
                server_item = selected_item.parent() if selected_item.parent() else selected_item
                server_address = server_item.text(1)
                if server_address.startswith(("http://", "https://")):
                    print(f"[DEBUG SERVER] Using tree selection server_url: {server_address}")
                    return server_address

        print(f"[DEBUG SERVER] No server URL found - main_window.server_url: {getattr(self.main_window, 'server_url', None)}")
        if not silent:
            QMessageBox.critical(self, "No Server Selected",
                                 "Please select a server before starting/stopping devices.")
        return None

    # ---------- Row creation ----------

    def add_device(self, name, mac, ipv4, ipv6, vlan="0", status="Pending", ipv4_mask="24", ipv6_mask="64", ipv4_gateway="", ipv6_gateway="", loopback_ipv4="", loopback_ipv6=""):
        """Create a GUI row for a device with simplified columns."""
        row = self.devices_table.rowCount()
        self.devices_table.insertRow(row)

        device_id = str(uuid.uuid4())

        def put(header, val, *, icon: QIcon = None, align=Qt.AlignCenter, user_data=None):
            item = QTableWidgetItem(str(val))
            item.setTextAlignment(align)
            if icon is not None:
                item.setIcon(icon)
            if user_data is not None:
                item.setData(Qt.UserRole, user_data)
            self.devices_table.setItem(row, self.COL[header], item)

        put("Device Name", name, user_data=device_id)
        put("MAC Address", mac)

        ipv4_item = QTableWidgetItem(str(ipv4))
        ipv4_item.setData(Qt.UserRole + 1, ipv4_mask)
        self.devices_table.setItem(row, self.COL["IPv4"], ipv4_item)

        ipv6_item = QTableWidgetItem(str(ipv6))
        ipv6_item.setData(Qt.UserRole + 1, ipv6_mask)
        self.devices_table.setItem(row, self.COL["IPv6"], ipv6_item)

        # VLAN column
        put("VLAN", vlan)

        # Gateway columns
        put("IPv4 Gateway", ipv4_gateway)
        put("IPv6 Gateway", ipv6_gateway)

        # status + icon - check ARP resolution if IP addresses are configured
        if ipv4 or ipv6:
            device_info = {
                "IPv4": ipv4,
                "IPv6": ipv6,
                "VLAN": vlan,
                "IPv4 Gateway": ipv4_gateway,
                "IPv6 Gateway": ipv6_gateway,
                "Interface": self.selected_iface_name
            }
            arp_resolved, arp_status = self._check_arp_resolution_sync(device_info)
            self.set_status_icon(row, resolved=arp_resolved, status_text=arp_status)
        else:
            self.set_status_icon(row, resolved=False, status_text="No IP configured")

        # masks
        put("IPv4 Mask", ipv4_mask)
        put("IPv6 Mask", ipv6_mask)
        
        # Loopback IP columns - separate IPv4 and IPv6
        put("Loopback IPv4", loopback_ipv4 if loopback_ipv4 else "")
        put("Loopback IPv6", loopback_ipv6 if loopback_ipv6 else "")

    def populate_device_table(self):
        """Populate the device table from the data structure."""
        try:
            # Clear existing table
            self.devices_table.setRowCount(0)
            
            # Get all devices from all interfaces
            all_devices = getattr(self.main_window, 'all_devices', {})
            if not all_devices:
                return
            
            # Add devices from all interfaces to the table
            for interface, devices in all_devices.items():
                if not isinstance(devices, list):
                    continue
                    
                for device_info in devices:
                    if not isinstance(device_info, dict):
                        continue
                    
                    # Extract device information
                    device_name = device_info.get("Device Name", "")
                    mac = device_info.get("MAC Address", "")
                    ipv4 = device_info.get("IPv4", "")
                    ipv6 = device_info.get("IPv6", "")
                    vlan = device_info.get("VLAN", "0")
                    ipv4_mask = device_info.get("ipv4_mask", "24")
                    ipv6_mask = device_info.get("ipv6_mask", "64")
                    ipv4_gateway = device_info.get("IPv4 Gateway", device_info.get("Gateway", ""))
                    ipv6_gateway = device_info.get("IPv6 Gateway", "")
                    loopback_ipv4 = device_info.get("Loopback IPv4", "")
                    loopback_ipv6 = device_info.get("Loopback IPv6", "")
                    
                    # Add device to table
                    self.add_device(
                        name=device_name,
                        mac=mac,
                        ipv4=ipv4,
                        ipv6=ipv6,
                        vlan=vlan,
                        status="Stopped",  # Default status
                        ipv4_mask=ipv4_mask,
                        ipv6_mask=ipv6_mask,
                        ipv4_gateway=ipv4_gateway,
                        ipv6_gateway=ipv6_gateway,
                        loopback_ipv4=loopback_ipv4,
                        loopback_ipv6=loopback_ipv6
                    )
            
            print(f"[DEBUG DEVICE TABLE] Populated table with {self.devices_table.rowCount()} devices")
            
        except Exception as e:
            print(f"[ERROR] Failed to populate device table: {e}")
            logging.error(f"Failed to populate device table: {e}")

    # ---------- Dialogs / actions ----------

    def apply_selected_device(self):
        """Apply only the selected devices to the server."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to apply.")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to apply.")
            return

        # Get server URL
        server_url = self.get_server_url()
        if not server_url:
            return

        # Process each selected device
        results = []
        successful_count = 0
        failed_count = 0
        
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if not device_info:
                results.append(f"❌ {device_name}: Device not found in data structure")
                failed_count += 1
                continue

            # Always check and reconfigure selected devices (regardless of _needs_apply flag)
            # This ensures devices are properly configured after UI restart
            print(f"[DEBUG APPLY] Checking and reconfiguring device '{device_name}' (selected by user)")

            try:
                # Use the appropriate method based on whether device is new or existing
                if device_info.get("_is_new", False):
                    # New device - use _add_device_to_server which has proper protocol handling
                    print(f"[DEBUG APPLY] Adding new device '{device_name}' to server")
                    if self._add_device_to_server(server_url, device_info):
                        success = True
                    else:
                        success = False
                else:
                    # Existing device - use _apply_device_to_server
                    print(f"[DEBUG APPLY] Applying existing device '{device_name}' to server")
                    if self._apply_device_to_server(server_url, device_info):
                        success = True
                    else:
                        success = False
                
                if success:
                    # Mark device as applied
                    device_info["_is_new"] = False
                    device_info["_needs_apply"] = False
                    device_info["Status"] = "Running"
                    
                    # Report success - protocols are handled by _add_device_to_server
                    results.append(f"✅ {device_name}: Device applied successfully")
                    successful_count += 1
                else:
                    results.append(f"❌ {device_name}: Failed to apply to server")
                    failed_count += 1
            except Exception as e:
                results.append(f"❌ {device_name}: Error applying to server - {str(e)}")
                failed_count += 1

        # Update the device table to reflect status changes
        self.update_device_table(self.main_window.all_devices)
        
        # Clear modification indicators for successfully applied devices
        if successful_count > 0:
            self.clear_modification_indicators()

        # Show summary results using custom dialog
        total_devices = len(selected_rows)
        already_applied_count = total_devices - successful_count - failed_count
        summary = f"Device Reconfiguration Results ({total_devices} device{'s' if total_devices > 1 else ''}):\n"
        summary += f"✅ Successfully Reconfigured: {successful_count} | ❌ Failed: {failed_count} | ℹ️ No Changes Needed: {already_applied_count}"
        
        if successful_count == total_devices:
            title = "All Devices Reconfigured Successfully"
        elif successful_count > 0:
            title = "Partial Reconfiguration Success"
        else:
            title = "All Device Reconfigurations Failed"
        
        dialog = MultiDeviceResultsDialog(title, summary, results, self)
        dialog.exec_()
        
        # Save session after device application to persist status changes
        if successful_count > 0 and hasattr(self.main_window, "save_session"):
            print(f"[DEBUG APPLY] Saving session after successful device application")
            self.main_window.save_session()
    
    def apply_selected_device_silent(self):
        """Apply only the selected devices to the server (silent mode - no dialog)."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            return

        # Get server URL
        server_url = self.get_server_url()
        if not server_url:
            return

        # Check if multi-device apply worker is already running
        if hasattr(self, 'multi_device_apply_worker') and self.multi_device_apply_worker:
            if self.multi_device_apply_worker.isRunning() or not self.multi_device_apply_worker.isFinished():
                print("[MULTI DEVICE APPLY] Apply operation already running, skipping new request")
                return
            else:
                # Clean up finished worker
                self.multi_device_apply_worker.deleteLater()
                delattr(self, 'multi_device_apply_worker')

        # Collect devices to apply
        devices_to_apply = []
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if device_info:
                devices_to_apply.append((row, device_info))
                print(f"[DEBUG APPLY SILENT] Will apply device '{device_name}' (selected by user)")

        if not devices_to_apply:
            print("[MULTI DEVICE APPLY] No valid devices found to apply")
            return

        # Create and start multi-device apply worker
        self.multi_device_apply_worker = MultiDeviceApplyWorker(devices_to_apply, server_url, self)
        
        # Set operation type flag for this operation
        self._current_operation_type = 'apply'
        
        self.multi_device_apply_worker.device_applied.connect(self._on_multi_device_applied)
        self.multi_device_apply_worker.progress.connect(self._on_multi_device_progress)
        self.multi_device_apply_worker.finished.connect(self._on_multi_device_apply_finished)
        self.multi_device_apply_worker.start()
        
        print(f"[MULTI DEVICE APPLY] Started applying {len(devices_to_apply)} devices in background")
    
    def apply_selected_device_with_arp(self):
        """Apply selected devices and automatically trigger ARP operations."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            return

        # Store selected rows for ARP operations
        self._pending_arp_rows = selected_rows
        
        # Set status to "Applying..." for selected devices
        print(f"[APPLY WITH ARP] Setting status to 'Applying...' for {len(selected_rows)} devices")
        for row in selected_rows:
            try:
                status_item = self.devices_table.item(row, self.COL["Status"])
                if status_item:
                    status_item.setText("Applying...")
                    status_item.setIcon(self.orange_dot)  # Use orange dot to indicate in progress
                    status_item.setToolTip("Applying device configuration...")
            except Exception as e:
                print(f"[APPLY WITH ARP] Exception setting status for row {row}: {e}")
        
        print(f"[APPLY WITH ARP] Apply button clicked - will apply {len(selected_rows)} devices and then run ARP operations")
        
        # Use the existing chain method
        self.apply_selected_device_with_arp_chain()

    def apply_selected_device_with_arp_chain(self):
        """Apply selected devices and then run ARP operations if pending."""
        # Check if ARP operation is already running - use more robust check
        if hasattr(self, 'arp_operation_worker') and self.arp_operation_worker:
            if self.arp_operation_worker.isRunning() or not self.arp_operation_worker.isFinished():
                print("[ARP OPERATION] ARP operation already running, skipping new request")
                return
            else:
                # Clean up finished worker
                self.arp_operation_worker.deleteLater()
                delattr(self, 'arp_operation_worker')
        
        # First run the apply operation silently (without showing dialog)
        self.apply_selected_device_silent()
        
        # Check if there are pending ARP operations
        if hasattr(self, '_pending_arp_rows') and self._pending_arp_rows:
            print(f"[ARP OPERATION] Apply completed, now starting ARP operations for {len(self._pending_arp_rows)} devices...")
            
            # Collect devices to process for ARP
            devices_to_process = []
            for row in self._pending_arp_rows:
                device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
                
                # Find device in all_devices data structure
                device_info = None
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            device_info = device
                            break
                    if device_info:
                        break
                
                if device_info:
                    devices_to_process.append((row, device_name, device_info))
                else:
                    print(f"[ARP OPERATION] Device {device_name} not found in data structure")

            if devices_to_process:
                # Store the pending ARP rows in a local variable for the lambda
                pending_rows = self._pending_arp_rows.copy()
                
                # Create and start ARP worker thread
                self.arp_operation_worker = ArpOperationWorker(devices_to_process, self)
                
                # Connect signals
                self.arp_operation_worker.progress.connect(self._on_arp_operation_progress)
                self.arp_operation_worker.device_status_updated.connect(self._on_arp_status_updated)
                self.arp_operation_worker.arp_result.connect(self._on_individual_arp_result)  # For individual IP colors
                self.arp_operation_worker.finished.connect(lambda results, succ, fail: self._on_arp_operation_finished(results, succ, fail, pending_rows))
                
                # Start the worker (non-blocking)
                self.arp_operation_worker.start()
                
                print(f"[ARP OPERATION] Starting ARP requests for {len(devices_to_process)} devices in background...")
            else:
                print(f"[ARP OPERATION] No valid devices found for ARP operation")
            
            # Don't clear pending ARP rows here - they will be cleared when ARP operation finishes
            # delattr(self, '_pending_arp_rows')
    
    def _calculate_changes(self):
        """Calculate changes between current state and last saved session."""
        print(f"[DEBUG CHANGES] Calculating changes since last session")
        
        changes = {
            'to_add': [],
            'to_remove': []
        }
        
        # Get current devices (devices still in UI)
        current_devices = {}
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                device_name = device.get("Device Name", "")
                if device_name:
                    current_devices[device_name] = device
        
        # Get last saved devices from session
        last_saved_devices = {}
        if hasattr(self.main_window, 'last_saved_devices'):
            last_saved_devices = self.main_window.last_saved_devices
        
        print(f"[DEBUG CHANGES] Current devices: {len(current_devices)}")
        print(f"[DEBUG CHANGES] Last saved devices: {len(last_saved_devices)}")
        
        # First, find devices to remove (devices marked for removal)
        devices_to_remove_names = set()
        if hasattr(self.main_window, 'devices_to_remove'):
            for removal_info in self.main_window.devices_to_remove:
                changes['to_remove'].append(removal_info['device_info'])
                devices_to_remove_names.add(removal_info['name'])
                print(f"[DEBUG CHANGES] Device marked for removal: '{removal_info['name']}'")
        
        # Find devices to add (new devices that need to be applied to server)
        # Exclude devices that are marked for removal
        for device_name, device_info in current_devices.items():
            if device_name not in devices_to_remove_names and (device_info.get("_is_new", False) or device_info.get("_needs_apply", False)):
                changes['to_add'].append(device_info)
                print(f"[DEBUG CHANGES] Device to add: '{device_name}'")
        
        return changes
    
    def _add_device_to_server(self, server_url, device_info, force_reconfigure=False):
        """Add a single device to the server."""
        try:
            device_name = device_info.get("Device Name", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            if force_reconfigure:
                print(f"[DEBUG ADD] Force reconfiguring device '{device_name}' to server: {server_url}")
            else:
                print(f"[DEBUG ADD] Adding device '{device_name}' to server: {server_url}")
            
            # Check what's currently configured on the server and compare with intended config
            print(f"[DEBUG ADD] Checking existing configuration on server for '{device_name}'")
            
            # Check if we need to clean up old VLAN configuration (e.g., VLAN changed)
            old_config = device_info.get("_old_config")
            if old_config and old_config.get("vlan") != device_info.get("VLAN", "0"):
                old_vlan = old_config.get("vlan", "0")
                old_interface = old_config.get("interface", "")
                if old_vlan != "0" and old_interface:
                    old_iface_norm = self._normalize_iface_label(old_interface)
                    print(f"[DEBUG ADD] VLAN changed from {old_vlan} to {device_info.get('VLAN', '0')} - cleaning up old VLAN interface")
                    
                    # Clean up the old VLAN interface
                    old_cleanup_payload = {
                        "interface": old_iface_norm,
                        "vlan": old_vlan,
                        "cleanup_only": True,
                        "remove_vlan": True  # Special flag to remove the entire VLAN interface
                    }
                    
                    old_cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=old_cleanup_payload, timeout=10)
                    if old_cleanup_resp.status_code == 200:
                        print(f"[DEBUG ADD] Successfully cleaned up old VLAN interface vlan{old_vlan}@{old_iface_norm}")
                    else:
                        print(f"[DEBUG ADD] Failed to clean up old VLAN interface: {old_cleanup_resp.status_code} - {old_cleanup_resp.text}")
            
            # Get intended configuration from device_info
            intended_ipv4 = device_info.get("IPv4", "").strip()
            intended_ipv6 = device_info.get("IPv6", "").strip()
            intended_ipv4_mask = device_info.get("ipv4_mask", "24")
            intended_ipv6_mask = device_info.get("ipv6_mask", "64")
            
            # Build intended IP list
            intended_ips = []
            if intended_ipv4:
                intended_ips.append(f"{intended_ipv4}/{intended_ipv4_mask}")
            if intended_ipv6:
                intended_ips.append(f"{intended_ipv6}/{intended_ipv6_mask}")
            
            print(f"[DEBUG ADD] Intended configuration: {intended_ips}")
            
            # Check what's currently configured on the server
            check_payload = {
                "interface": iface_norm,
                "vlan": device_info.get("VLAN", "0"),
                "check_only": True  # Just check, don't modify
            }
            
            print(f"[DEBUG ADD] Sending check request to server: {server_url}")
            check_resp = requests.post(f"{server_url}/api/device/check", json=check_payload, timeout=10)
            existing_ips = []
            if check_resp.status_code == 200:
                check_data = check_resp.json()
                existing_ips = check_data.get("existing_ips", [])
                print(f"[DEBUG ADD] Found existing IPs on server: {existing_ips}")
            else:
                print(f"[DEBUG ADD] Could not check existing configuration on {server_url}: {check_resp.status_code} - {check_resp.text}")
            
            # Compare intended vs existing configuration
            intended_set = set(intended_ips)
            existing_set = set(existing_ips)
            
            if intended_set == existing_set and not force_reconfigure:
                print(f"[DEBUG ADD] Configuration matches - no changes needed")
                # Configuration is already correct, just mark as applied
                device_info["_is_new"] = False
                device_info["_needs_apply"] = False
                device_info["Status"] = "Running"
                return True
            else:
                if force_reconfigure:
                    print(f"[DEBUG ADD] Force reconfiguration requested - reapplying configuration")
                else:
                    print(f"[DEBUG ADD] Configuration differs - need to reapply")
                print(f"[DEBUG ADD] Missing on server: {intended_set - existing_set}")
                print(f"[DEBUG ADD] Extra on server: {existing_set - intended_set}")
                
                # Clean up existing configuration before applying new one
                print(f"[DEBUG ADD] Cleaning up existing configuration for '{device_name}'")
                cleanup_payload = {
                    "interface": iface_norm,
                    "vlan": device_info.get("VLAN", "0"),
                    "cleanup_only": True,  # Just cleanup, don't add new IPs
                    "device_specific": True,  # Only remove IPs for this specific device
                    "device_id": device_info.get("device_id", ""),
                    "device_name": device_name
                }
                
                cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
                if cleanup_resp.status_code == 200:
                    cleanup_data = cleanup_resp.json()
                    removed_ips = cleanup_data.get("removed_ips", [])
                    if removed_ips:
                        print(f"[DEBUG ADD] Successfully cleaned up existing IPs: {removed_ips}")
                    else:
                        print(f"[DEBUG ADD] Interface was already clean - no IPs to remove")
                else:
                    print(f"[DEBUG ADD] Cleanup failed for '{device_name}': {cleanup_resp.status_code} - {cleanup_resp.text}")
                    # Continue anyway - maybe the interface was already clean or doesn't exist yet
                
                # Clear any cleanup flags since we've done the cleanup
                device_info["_needs_cleanup"] = False
                
                # Now apply the new configuration
                payload = {
                    "interface": iface_norm,
                    "ipv4": device_info.get("IPv4", ""),
                    "ipv6": device_info.get("IPv6", ""),
                    "ipv4_mask": device_info.get("ipv4_mask", "24"),
                    "ipv6_mask": device_info.get("ipv6_mask", "64"),
                    "vlan": device_info.get("VLAN", "0"),
                    "device_id": device_info.get("device_id", ""),
                    "device_name": device_name,
                    "gateway": device_info.get("Gateway", ""),  # Keep for backward compatibility
                    "ipv4_gateway": device_info.get("IPv4 Gateway", ""),  # Include IPv4 gateway for static route
                    "ipv6_gateway": device_info.get("IPv6 Gateway", ""),  # Include IPv6 gateway for static route
                    "loopback_ipv4": device_info.get("Loopback IPv4", ""),
                    "loopback_ipv6": device_info.get("Loopback IPv6", ""),
                    # Database fields - map client field names to database field names
                    "ipv4_address": device_info.get("IPv4", ""),
                    "ipv6_address": device_info.get("IPv6", ""),
                    "mac_address": device_info.get("MAC Address", ""),
                    # Handle protocols - convert string to array if needed
                    "protocols": self._convert_protocols_to_array(device_info.get("Protocols", "")),
                    "bgp_config": device_info.get("bgp_config", {}),
                    "ospf_config": device_info.get("ospf_config", {}),
                }
                
                resp = requests.post(f"{server_url}/api/device/apply", json=payload, timeout=30)
                if resp.status_code == 200:
                    print(f"[DEBUG ADD] Successfully applied new configuration for '{device_name}'")
                    # Mark as applied
                    device_info["_is_new"] = False
                    device_info["_needs_apply"] = False
                    device_info["Status"] = "Running"
                    
                    # Send immediate ARP request to populate ARP table
                    # DISABLED to prevent QThread crashes - ARP will be manual only
                    # try:
                    #     self.send_immediate_arp_request(device_info, server_url)
                    # except Exception as arp_error:
                    #     print(f"[DEBUG ADD] ARP request failed for '{device_name}': {arp_error}")
                    #     # Don't fail device addition if ARP request fails
                    
                    return True
                else:
                    print(f"[ERROR] Failed to add '{device_name}': {resp.status_code} - {resp.text}")
                    return False
                
        except Exception as e:
            print(f"[ERROR] Exception adding device '{device_name}' to server '{server_url}': {e}")
            return False
    
    def _apply_device_to_server(self, server_url, device_info):
        """Apply device configuration using the new /api/device/apply endpoint in background."""
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            print(f"[DEBUG APPLY DEVICE] Starting apply for {device_name}")
            print(f"[DEBUG APPLY DEVICE] Device info keys: {list(device_info.keys())}")
            print(f"[DEBUG APPLY DEVICE] Protocols: {device_info.get('protocols', [])}")
            print(f"[DEBUG APPLY DEVICE] BGP config: {device_info.get('bgp_config', {})}")
            print(f"[DEBUG APPLY DEVICE] OSPF config: {device_info.get('ospf_config', {})}")
            
            # If device has an ID, fetch complete device data from database
            if device_id:
                try:
                    import requests
                    print(f"[DEBUG APPLY DEVICE] Fetching complete device data from database for {device_name}")
                    response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
                    if response.status_code == 200:
                        db_device_data = response.json()
                        print(f"[DEBUG APPLY DEVICE] Database device data keys: {list(db_device_data.keys())}")
                        
                        # Update device_info with database data
                        device_info.update({
                            "protocols": db_device_data.get("protocols", []),
                            "bgp_config": db_device_data.get("bgp_config", {}),
                            "ospf_config": db_device_data.get("ospf_config", {})
                        })
                        
                        print(f"[DEBUG APPLY DEVICE] Updated device info - Protocols: {device_info.get('protocols', [])}")
                        print(f"[DEBUG APPLY DEVICE] Updated device info - BGP config: {device_info.get('bgp_config', {})}")
                        print(f"[DEBUG APPLY DEVICE] Updated device info - OSPF config: {device_info.get('ospf_config', {})}")
                    else:
                        print(f"[DEBUG APPLY DEVICE] Failed to fetch device data from database: {response.status_code}")
                except Exception as e:
                    print(f"[DEBUG APPLY DEVICE] Error fetching device data from database: {e}")
            
            # Prepare payload for background worker
            payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": iface_norm,
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_mask": device_info.get("ipv4_mask", "24"),
                "ipv6_mask": device_info.get("ipv6_mask", "64"),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "protocols": device_info.get("protocols", []),
                "bgp_config": device_info.get("bgp_config", {}),
                "ospf_config": device_info.get("ospf_config", {}),
            }
            
            print(f"[DEBUG APPLY DEVICE] Payload protocols: {payload['protocols']}")
            print(f"[DEBUG APPLY DEVICE] Payload BGP config: {payload['bgp_config']}")
            print(f"[DEBUG APPLY DEVICE] Payload OSPF config: {payload['ospf_config']}")
            
            # Create and start background worker
            query_data = {
                "server_url": server_url,
                "payload": payload,
                "device_name": device_name
            }
            
            self.db_worker = DatabaseQueryWorker("device_apply", query_data, self)
            self.db_worker.query_result.connect(self._on_device_apply_result)
            self.db_worker.query_error.connect(self._on_device_apply_error)
            self.db_worker.finished.connect(self._on_device_apply_finished)
            self.db_worker.start()
            
            # Return immediately (non-blocking)
            return True
                
        except Exception as e:
            print(f"[ERROR] Exception starting device apply for '{device_name}': {e}")
            return False
    
    def _apply_device_to_server_sync(self, server_url, device_info):
        """Apply device configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            print(f"[DEBUG DEVICE APPLY] Starting device apply for {device_name}")
            print(f"[DEBUG DEVICE APPLY] Device info keys: {list(device_info.keys())}")
            print(f"[DEBUG DEVICE APPLY] Protocols: {device_info.get('protocols', [])}")
            print(f"[DEBUG DEVICE APPLY] BGP config: {device_info.get('bgp_config', {})}")
            print(f"[DEBUG DEVICE APPLY] OSPF config: {device_info.get('ospf_config', {})}")
            
            # Step 1: Apply basic device configuration (interface, IP addresses, routes)
            basic_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": iface_norm,
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_mask": device_info.get("ipv4_mask", "24"),
                "ipv6_mask": device_info.get("ipv6_mask", "64"),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "loopback_ipv4": device_info.get("Loopback IPv4", ""),
                "loopback_ipv6": device_info.get("Loopback IPv6", ""),
                "protocols": device_info.get("protocols", []),
                "bgp_config": device_info.get("bgp_config", {}),
                "ospf_config": device_info.get("ospf_config", {}),
            }
            
            # Apply basic device configuration
            print(f"[DEBUG DEVICE APPLY] Calling /api/device/apply with payload: {basic_payload}")
            response = requests.post(f"{server_url}/api/device/apply", json=basic_payload, timeout=30)
            if response.status_code != 200:
                print(f"[ERROR] Failed to apply basic device configuration: {response.status_code}")
                print(f"[ERROR] Response: {response.text}")
                return False
            
            print(f"[SUCCESS] Basic device configuration applied for {device_name}")
            
            # Step 2: Configure BGP if enabled
            protocols = device_info.get("protocols", [])
            bgp_config = device_info.get("bgp_config", {})
            
            print(f"[DEBUG DEVICE APPLY] Checking BGP - protocols: {protocols}, bgp_config: {bgp_config}")
            if "BGP" in protocols and bgp_config:
                print(f"[INFO] Configuring BGP for device {device_name}")
                bgp_success = self._apply_bgp_to_server_sync(server_url, device_info)
                if not bgp_success:
                    print(f"[ERROR] Failed to configure BGP for device {device_name}")
                    return False
                print(f"[SUCCESS] BGP configured for device {device_name}")
            else:
                print(f"[DEBUG DEVICE APPLY] BGP not configured - protocols: {protocols}, bgp_config: {bgp_config}")
            
            # Step 3: Configure OSPF if enabled
            ospf_config = device_info.get("ospf_config", {})
            
            print(f"[DEBUG DEVICE APPLY] Checking OSPF - protocols: {protocols}, ospf_config: {ospf_config}")
            if "OSPF" in protocols and ospf_config:
                print(f"[INFO] Configuring OSPF for device {device_name}")
                ospf_success = self._apply_ospf_to_server_sync(server_url, device_info)
                if not ospf_success:
                    print(f"[ERROR] Failed to configure OSPF for device {device_name}")
                    return False
                print(f"[SUCCESS] OSPF configured for device {device_name}")
            else:
                print(f"[DEBUG DEVICE APPLY] OSPF not configured - protocols: {protocols}, ospf_config: {ospf_config}")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] Exception in sync device apply for '{device_name}': {e}")
            return False
    
    def _apply_bgp_to_server_sync(self, server_url, device_info):
        """Apply BGP configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            
            # Get BGP config - handle both old dict format and new separate config format
            bgp_config = device_info.get("bgp_config", {})
            if not bgp_config:
                # Try old format for backward compatibility
                protocols = device_info.get("protocols", {})
                if isinstance(protocols, dict):
                    bgp_config = protocols.get("BGP", {})
            
            if not bgp_config:
                return True  # No BGP config to apply
            
            # Prepare BGP payload using the configure endpoint (same as the original)
            bgp_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "gateway": device_info.get("Gateway", ""),  # Keep for backward compatibility
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),  # Include IPv4 gateway for static route
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),  # Include IPv6 gateway for static route
                "bgp_config": bgp_config,
                "all_route_pools": getattr(self.main_window, 'bgp_route_pools', [])  # Include all route pools for generation
            }
            
            # Make synchronous request to the configure endpoint
            response = requests.post(f"{server_url}/api/device/bgp/configure", json=bgp_payload, timeout=30)
            return response.status_code == 200
                
        except Exception as e:
            print(f"[ERROR] Exception in sync BGP apply for '{device_name}': {e}")
            return False
    
    def _apply_ospf_to_server_sync(self, server_url, device_info):
        """Apply OSPF configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            
            # Get OSPF config - handle both old dict format and new separate config format
            ospf_config = device_info.get("ospf_config", {})
            if not ospf_config:
                # Try old format for backward compatibility
                protocols = device_info.get("protocols", {})
                if isinstance(protocols, dict):
                    ospf_config = protocols.get("OSPF", {})
            
            if not ospf_config:
                return True  # No OSPF config to apply
            
            # Prepare OSPF payload using the configure endpoint
            ospf_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": device_info.get("Interface", ""),
                "vlan": device_info.get("VLAN", "0"),
                "ipv4": device_info.get("IPv4", ""),
                "ipv6": device_info.get("IPv6", ""),
                "ipv4_gateway": device_info.get("IPv4 Gateway", ""),
                "ipv6_gateway": device_info.get("IPv6 Gateway", ""),
                "ospf_config": ospf_config,
                "route_pools_per_area": {},  # No route pools attached initially
                "all_route_pools": []  # Include all route pools for generation
            }
            
            # Make synchronous request to the configure endpoint
            response = requests.post(f"{server_url}/api/device/ospf/configure", json=ospf_payload, timeout=30)
            return response.status_code == 200
                
        except Exception as e:
            print(f"[ERROR] Exception in sync OSPF apply for '{device_name}': {e}")
            return False
    
    
    def _remove_device_from_data_structure(self, device_info):
        """Remove device from all_devices data structure."""
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            
            print(f"[DEBUG REMOVE] Removing '{device_name}' from data structure")
            
            # Remove from all_devices
            if iface_label in self.main_window.all_devices:
                self.main_window.all_devices[iface_label] = [
                    d for d in self.main_window.all_devices[iface_label] 
                    if d.get("device_id") != device_id
                ]
                
                # Remove empty interface
                if not self.main_window.all_devices[iface_label]:
                    del self.main_window.all_devices[iface_label]
                    print(f"[DEBUG REMOVE] Removed empty interface '{iface_label}'")
            
            # Remove from interface_to_device_map
            if device_name in self.interface_to_device_map:
                del self.interface_to_device_map[device_name]
                print(f"[DEBUG REMOVE] Removed '{device_name}' from device mapping")
                
        except Exception as e:
            print(f"[ERROR] Failed to remove device from data structure: {e}")

    def prompt_add_device(self):
        """Open AddDeviceDialog, persist to model, refresh table."""
        selected_items = self.main_window.server_tree.selectedItems()
        if not selected_items or not selected_items[0].parent():
            QMessageBox.warning(self, "No Interface Selected",
                                "Please select a port under a server.")
            return

        tg_id = selected_items[0].parent().text(0).strip()
        port_name = selected_items[0].text(0).replace("• ", "").strip()  # Remove bullet prefix
        iface = f"{tg_id} - {port_name}"  # Match server tree format

        dialog = AddDeviceDialog(self, default_iface=iface)
        if dialog.exec_() != dialog.Accepted:
            return

        (
            device_name, iface_name, mac, ipv4, ipv6, ipv4_mask, ipv6_mask,
            vlan, ipv4_gateway, ipv6_gateway, incr_mac, incr_ipv4, incr_ipv6, incr_gateway, incr_vlan, incr_count, ospf_config, bgp_config, 
            ipv4_octet_index, ipv6_hextet_index, mac_byte_index, gateway_octet_index, incr_loopback, loopback_ipv4_octet_index, loopback_ipv6_hextet_index, loopback_ipv4, loopback_ipv6, isis_config
        ) = dialog.get_values()

        ipv4_mask = ipv4_mask or "24"
        ipv6_mask = ipv6_mask or "64"

        # Get base name for incrementing - use "device" as default instead of "Device"
        base_name = (device_name or "").strip() or "device"
        
        # Get all existing device names to ensure uniqueness
        all_existing_names = [
            d.get("Device Name", "")
            for dev_list in getattr(self.main_window, "all_devices", {}).values()
            for d in (dev_list if isinstance(dev_list, list) else [])
        ]
        
        # Create multiple devices if increment is enabled
        devices_to_create = []
        
        if incr_count > 1 and (incr_mac or incr_ipv4 or incr_ipv6 or incr_gateway or incr_vlan or incr_loopback):
            # Create multiple devices with incremented values
            for i in range(incr_count):
                current_mac = mac
                current_ipv4 = ipv4
                current_ipv6 = ipv6
                current_vlan = vlan
                
                # Generate unique name for this device
                if base_name == "device":
                    current_name = f"device{i+1}"
                    n = 1
                    while current_name in all_existing_names:
                        n += 1
                        current_name = f"device{i+1}_{n}"
                else:
                    current_name = f"{base_name}_{i+1}"
                    n = 1
                    while current_name in all_existing_names:
                        n += 1
                        current_name = f"{base_name}_{i+1}_{n}"
                
                # Add to existing names to prevent duplicates within this batch
                all_existing_names.append(current_name)
                
                # Increment MAC if enabled
                if incr_mac and i > 0:
                    current_mac = self._increment_mac(mac, i, mac_byte_index)
                
                # Increment IPv4 if enabled
                if incr_ipv4 and i > 0:
                    current_ipv4 = self._increment_ipv4(ipv4, i, ipv4_octet_index)
                
                # Increment IPv6 if enabled
                if incr_ipv6 and i > 0:
                    current_ipv6 = self._increment_ipv6(ipv6, i, ipv6_hextet_index)
                
                # Increment IPv4 Gateway if enabled (use separate gateway octet index)
                current_ipv4_gateway = ipv4_gateway
                if incr_gateway and i > 0 and ipv4_gateway:
                    current_ipv4_gateway = self._increment_ipv4(ipv4_gateway, i, gateway_octet_index)
                
                # Increment IPv6 Gateway if enabled (use same hextet as IPv6)
                current_ipv6_gateway = ipv6_gateway
                if incr_gateway and i > 0 and ipv6_gateway:
                    current_ipv6_gateway = self._increment_ipv6(ipv6_gateway, i, ipv6_hextet_index)
                
                # Increment VLAN if enabled
                if incr_vlan and i > 0:
                    current_vlan = str(int(vlan) + i)
                
                # Increment Loopback IPv4 if enabled
                current_loopback_ipv4 = loopback_ipv4
                if incr_loopback and i > 0 and loopback_ipv4:
                    current_loopback_ipv4 = self._increment_ipv4(loopback_ipv4, i, loopback_ipv4_octet_index)
                
                # Increment Loopback IPv6 if enabled
                current_loopback_ipv6 = loopback_ipv6
                if incr_loopback and i > 0 and loopback_ipv6:
                    current_loopback_ipv6 = self._increment_ipv6(loopback_ipv6, i, loopback_ipv6_hextet_index)
                
                device_data = {
                    "Device Name": current_name,
                    "device_id": str(uuid.uuid4()),
                    "Interface": iface,
                    "MAC Address": current_mac,
                    "IPv4": current_ipv4,
                    "IPv6": current_ipv6,
                    "ipv4_mask": ipv4_mask,
                    "ipv6_mask": ipv6_mask,
                    "VLAN": current_vlan,
                    "Gateway": current_ipv4_gateway,  # Keep for backward compatibility
                    "IPv4 Gateway": current_ipv4_gateway,
                    "IPv6 Gateway": current_ipv6_gateway,
                    "Loopback IPv4": current_loopback_ipv4 if current_loopback_ipv4 else "",
                    "Loopback IPv6": current_loopback_ipv6 if current_loopback_ipv6 else "",
                    "Status": "Stopped",
                }
                
                # Add OSPF protocol if enabled
                print(f"[DEBUG ADD DEVICE] OSPF config for device {i+1}: {ospf_config}")
                if ospf_config:
                    print(f"[DEBUG ADD DEVICE] Adding OSPF to device {current_name}")
                    # Initialize protocols as list and ospf_config as separate field
                    device_data["protocols"] = device_data.get("protocols", [])
                    if "OSPF" not in device_data["protocols"]:
                        device_data["protocols"].append("OSPF")
                    
                    # Create incremented OSPF configuration
                    incremented_ospf_config = ospf_config.copy()
                    
                    # Update OSPF interface based on incremented VLAN
                    if current_vlan != "0":
                        incremented_ospf_config["interface"] = f"vlan{current_vlan}"
                    else:
                        incremented_ospf_config["interface"] = iface_name
                    
                    # Update OSPF router ID based on incremented IPv4 address
                    if current_ipv4:
                        incremented_ospf_config["router_id"] = current_ipv4
                    
                    # Update IPv4/IPv6 enabled flags based on incremented addresses
                    incremented_ospf_config["ipv4_enabled"] = bool(current_ipv4)
                    incremented_ospf_config["ipv6_enabled"] = bool(current_ipv6)
                    
                    device_data["ospf_config"] = incremented_ospf_config
                    print(f"[DEBUG ADD DEVICE] Incremented OSPF config: {incremented_ospf_config}")
                
                # Add BGP protocol if enabled
                print(f"[DEBUG ADD DEVICE] BGP config for device {i+1}: {bgp_config}")
                # Check if BGP is enabled (support both old and new formats)
                bgp_enabled = bgp_config and (
                    bgp_config.get("enabled", False) or  # Old format
                    bgp_config.get("ipv4_enabled", False) or  # New format
                    bgp_config.get("ipv6_enabled", False)  # New format
                )
                if bgp_enabled:
                    print(f"[DEBUG ADD DEVICE] Adding BGP to device {current_name}")
                    
                    # Build BGP configuration based on enabled protocols
                    # Handle both old and new BGP config formats
                    bgp_protocol_config = {
                        "bgp_asn": bgp_config.get("bgp_asn") or bgp_config.get("local_as", "65000"),
                        "bgp_remote_asn": bgp_config.get("bgp_remote_asn") or bgp_config.get("remote_as", "65001"),
                        "mode": bgp_config.get("bgp_mode", "eBGP"),
                        "bgp_keepalive": bgp_config.get("bgp_keepalive", "30"),
                        "bgp_hold_time": bgp_config.get("bgp_hold_time", "90"),
                        "ipv4_enabled": bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False)),
                        "ipv6_enabled": bgp_config.get("ipv6_enabled", False)
                    }
                    
                    # Add IPv4 BGP configuration if enabled (support both old and new formats)
                    ipv4_enabled = bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False))
                    if ipv4_enabled:
                        # Always use the current incremented gateway and device IP for BGP configuration
                        # This ensures that when multiple devices are created with increment,
                        # each device gets the correct gateway and device IP for its BGP configuration
                        neighbor_ipv4 = current_ipv4_gateway  # Use incremented gateway
                        update_source_ipv4 = current_ipv4     # Use incremented device IP
                        bgp_protocol_config["bgp_neighbor_ipv4"] = neighbor_ipv4
                        bgp_protocol_config["bgp_update_source_ipv4"] = update_source_ipv4
                        bgp_protocol_config["protocol"] = "ipv4"
                        print(f"[DEBUG ADD DEVICE] IPv4 BGP configured for device {current_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}")
                    
                    # Add IPv6 BGP configuration if enabled
                    if bgp_config.get("ipv6_enabled", False):
                        # Use the already calculated IPv6 gateway and device IP for IPv6 BGP
                        bgp_protocol_config["bgp_neighbor_ipv6"] = current_ipv6_gateway
                        bgp_protocol_config["bgp_update_source_ipv6"] = current_ipv6
                        # If both IPv4 and IPv6 are enabled, use "dual-stack", otherwise use the specific protocol
                        if ipv4_enabled:
                            bgp_protocol_config["protocol"] = "dual-stack"
                        else:
                            bgp_protocol_config["protocol"] = "ipv6"
                        print(f"[DEBUG ADD DEVICE] IPv6 BGP configured for device {current_name}")
                    
                    # Add BGP to protocols list and store config separately
                    device_data["protocols"] = device_data.get("protocols", [])
                    if "BGP" not in device_data["protocols"]:
                        device_data["protocols"].append("BGP")
                    device_data["bgp_config"] = bgp_protocol_config
                    device_data["Protocols"] = "BGP"
                    print(f"[DEBUG ADD DEVICE] BGP added to device {current_name}: {device_data['bgp_config']}")
                else:
                    print(f"[DEBUG ADD DEVICE] BGP NOT enabled for device {current_name} - bgp_config: {bgp_config}")
                
                # Add ISIS protocol if enabled
                print(f"[DEBUG ADD DEVICE] ISIS config for device {i+1}: {isis_config}")
                if isis_config:
                    print(f"[DEBUG ADD DEVICE] Adding ISIS to device {current_name}")
                    # Create a copy of ISIS config and update it based on incremented values
                    incremented_isis_config = isis_config.copy()
                    
                    # Update interface in ISIS config if VLAN was incremented
                    if incr_vlan and i > 0:
                        if current_vlan and current_vlan != "0":
                            incremented_isis_config["interface"] = f"vlan{current_vlan}"
                    
                    # Update IPv4/IPv6 enabled flags based on incremented addresses
                    incremented_isis_config["ipv4_enabled"] = bool(current_ipv4)
                    incremented_isis_config["ipv6_enabled"] = bool(current_ipv6)
                    
                    # Initialize protocols as list and isis_config as separate field
                    device_data["protocols"] = device_data.get("protocols", [])
                    if "IS-IS" not in device_data["protocols"]:
                        device_data["protocols"].append("IS-IS")
                    device_data["is_is_config"] = incremented_isis_config  # Use is_is_config for consistency with database
                    device_data["isis_config"] = incremented_isis_config   # Also store as isis_config for compatibility
                    print(f"[DEBUG ADD DEVICE] Incremented ISIS config: {incremented_isis_config}")
                else:
                    print(f"[DEBUG ADD DEVICE] ISIS NOT enabled for device {current_name}")
                
                devices_to_create.append(device_data)
        else:
            # Create single device - ensure unique name
            if base_name == "device":
                unique_name = "device1"
                n = 1
                while unique_name in all_existing_names:
                    n += 1
                    unique_name = f"device{n}"
            else:
                unique_name = base_name
                n = 1
                while unique_name in all_existing_names:
                    n += 1
                    unique_name = f"{base_name}_{n}"
            
            device_data = {
                "Device Name": unique_name,
                "device_id": str(uuid.uuid4()),
                "Interface": iface,
                "MAC Address": mac,
                "IPv4": ipv4,
                "IPv6": ipv6,
                "ipv4_mask": ipv4_mask,
                "ipv6_mask": ipv6_mask,
                "VLAN": vlan,
                "Gateway": ipv4_gateway,  # Keep for backward compatibility
                "IPv4 Gateway": ipv4_gateway,
                "IPv6 Gateway": ipv6_gateway,
                "Loopback IPv4": loopback_ipv4 if loopback_ipv4 else "",
                "Loopback IPv6": loopback_ipv6 if loopback_ipv6 else "",
                "Status": "Stopped",
            }
            
            # Add OSPF protocol if enabled
            print(f"[DEBUG ADD DEVICE] Single device OSPF config: {ospf_config}")
            if ospf_config:
                print(f"[DEBUG ADD DEVICE] Adding OSPF to single device {unique_name}")
                # Initialize protocols as list and ospf_config as separate field
                device_data["protocols"] = device_data.get("protocols", [])
                if "OSPF" not in device_data["protocols"]:
                    device_data["protocols"].append("OSPF")
                device_data["ospf_config"] = ospf_config
            
            # Add ISIS protocol if enabled
            print(f"[DEBUG ADD DEVICE] Single device ISIS config: {isis_config}")
            if isis_config:
                print(f"[DEBUG ADD DEVICE] Adding ISIS to single device {unique_name}")
                # Initialize protocols as list and isis_config as separate field
                device_data["protocols"] = device_data.get("protocols", [])
                if "IS-IS" not in device_data["protocols"]:
                    device_data["protocols"].append("IS-IS")
                device_data["is_is_config"] = isis_config  # Use is_is_config for consistency with database
                device_data["isis_config"] = isis_config   # Also store as isis_config for compatibility
                print(f"[DEBUG ADD DEVICE] ISIS added to single device {unique_name}: {device_data['is_is_config']}")
            else:
                print(f"[DEBUG ADD DEVICE] ISIS NOT enabled for single device")
            
            # Add BGP protocol if enabled
            print(f"[DEBUG ADD DEVICE] Single device BGP config: {bgp_config}")
            # Check if BGP is enabled (support both old and new formats)
            bgp_enabled = bgp_config and (
                bgp_config.get("enabled", False) or  # Old format
                bgp_config.get("ipv4_enabled", False) or  # New format
                bgp_config.get("ipv6_enabled", False)  # New format
            )
            if bgp_enabled:
                print(f"[DEBUG ADD DEVICE] Adding BGP to single device {unique_name}")
                
                # Build BGP configuration based on enabled protocols
                # Handle both old and new BGP config formats
                bgp_protocol_config = {
                    "bgp_asn": bgp_config.get("bgp_asn") or bgp_config.get("local_as", "65000"),
                    "bgp_remote_asn": bgp_config.get("bgp_remote_asn") or bgp_config.get("remote_as", "65001"),
                    "mode": bgp_config.get("bgp_mode", "eBGP"),
                    "bgp_keepalive": bgp_config.get("bgp_keepalive", "30"),
                    "bgp_hold_time": bgp_config.get("bgp_hold_time", "90"),
                    "ipv4_enabled": bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False)),
                    "ipv6_enabled": bgp_config.get("ipv6_enabled", False)
                }
                
                # Add IPv4 BGP configuration if enabled (support both old and new formats)
                ipv4_enabled = bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False))
                if ipv4_enabled:
                    # Always use the current gateway and device IP for BGP configuration
                    # This ensures consistency with the increment logic
                    neighbor_ipv4 = ipv4_gateway  # Use current gateway
                    update_source_ipv4 = ipv4     # Use current device IP
                    bgp_protocol_config["bgp_neighbor_ipv4"] = neighbor_ipv4
                    bgp_protocol_config["bgp_update_source_ipv4"] = update_source_ipv4
                    bgp_protocol_config["protocol"] = "ipv4"
                    print(f"[DEBUG ADD DEVICE] IPv4 BGP configured for single device {unique_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}")
                
                # Add IPv6 BGP configuration if enabled
                if bgp_config.get("ipv6_enabled", False):
                    bgp_protocol_config["bgp_neighbor_ipv6"] = ipv6_gateway
                    bgp_protocol_config["bgp_update_source_ipv6"] = ipv6
                    # If both IPv4 and IPv6 are enabled, use "dual-stack", otherwise use the specific protocol
                    if ipv4_enabled:
                        bgp_protocol_config["protocol"] = "dual-stack"
                    else:
                        bgp_protocol_config["protocol"] = "ipv6"
                    print(f"[DEBUG ADD DEVICE] IPv6 BGP configured for single device {unique_name}")
                
                # Add BGP to protocols list and store config separately
                device_data["protocols"] = device_data.get("protocols", [])
                if "BGP" not in device_data["protocols"]:
                    device_data["protocols"].append("BGP")
                device_data["bgp_config"] = bgp_protocol_config
                device_data["Protocols"] = "BGP"
                print(f"[DEBUG ADD DEVICE] BGP added to single device {unique_name}: {device_data['bgp_config']}")
            else:
                print(f"[DEBUG ADD DEVICE] BGP NOT enabled for single device - bgp_config: {bgp_config}")
            
            devices_to_create.append(device_data)

        # persist in model
        if iface not in self.main_window.all_devices or not isinstance(self.main_window.all_devices[iface], list):
            self.main_window.all_devices[iface] = []
        
        for device_data in devices_to_create:
            self.main_window.all_devices[iface].append(device_data)
            
            # Add to device name mapping for easy lookup
            self.interface_to_device_map[device_data["Device Name"]] = device_data
            
            # Mark device as newly added (for change tracking)
            device_data["_is_new"] = True
            device_data["_needs_apply"] = True
            
            print(f"[DEBUG ADD] Added device '{device_data['Device Name']}' locally (pending apply)")

        # Refresh the table to show new devices
        self.populate_device_table()

        # keep the interface selected
        tree = self.main_window.server_tree
        for i in range(tree.topLevelItemCount()):
            tg_item = tree.topLevelItem(i)
            for j in range(tg_item.childCount()):
                port_item = tg_item.child(j)
                if f"{tg_item.text(0).strip()} - {port_item.text(0).strip()}" == iface:
                    tree.setCurrentItem(port_item)
                    port_item.setSelected(True)
                    break

        self.update_device_table(self.main_window.all_devices)
        
        # Update BGP table if any devices have BGP configured
        self.update_bgp_table()
        
        # Update OSPF table if any devices have OSPF configured
        self.update_ospf_table()
        
        # Update ISIS table if any devices have ISIS configured
        self.update_isis_table()
        
        # Show info message about local addition
        QMessageBox.information(self, "Device Added Locally", 
                               f"Added {len(devices_to_create)} device(s) to the UI.\n\n"
                               f"Click 'Apply' to configure on server and save to session.")

    def prompt_edit_device(self):
        print(f"[DEBUG EDIT] Starting prompt_edit_device()")
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            print(f"[DEBUG EDIT] No items selected")
            return

        row = selected_items[0].row()
        print(f"[DEBUG EDIT] Selected row: {row}")
        
        # Get device name from table
        name = self.devices_table.item(row, self.COL["Device Name"]).text()
        print(f"[DEBUG EDIT] Device name from table: '{name}'")
        
        # Find device in all_devices data structure
        print(f"[DEBUG EDIT] Searching for device '{name}' in all_devices")
        print(f"[DEBUG EDIT] all_devices keys: {list(self.main_window.all_devices.keys())}")
        device_info = None
        for iface, devices in self.main_window.all_devices.items():
            print(f"[DEBUG EDIT] Checking interface '{iface}' with {len(devices)} devices")
            for device in devices:
                device_name = device.get("Device Name", "")
                print(f"[DEBUG EDIT] Found device: '{device_name}'")
                if device_name == name:
                    device_info = device
                    print(f"[DEBUG EDIT] Found matching device: {device_info}")
                    break
            if device_info:
                break
        
        if not device_info:
            print(f"[DEBUG EDIT] Device '{name}' not found in data structure")
            QMessageBox.warning(self, "Device Not Found", f"Could not find device '{name}' in data structure.")
            return

        # Extract device information
        iface = device_info.get("Interface", "")
        mac = device_info.get("MAC Address", "")
        vlan = device_info.get("VLAN", "0")
        ipv4 = device_info.get("IPv4", "")
        ipv6 = device_info.get("IPv6", "")
        ipv4_mask = device_info.get("ipv4_mask", "24")
        ipv6_mask = device_info.get("ipv6_mask", "64")

        print(f"[DEBUG EDIT] Extracted device info:")
        print(f"[DEBUG EDIT]   Interface: '{iface}'")
        print(f"[DEBUG EDIT]   MAC: '{mac}'")
        print(f"[DEBUG EDIT]   VLAN: '{vlan}'")
        print(f"[DEBUG EDIT]   IPv4: '{ipv4}'")
        print(f"[DEBUG EDIT]   IPv6: '{ipv6}'")
        print(f"[DEBUG EDIT]   IPv4 Mask: '{ipv4_mask}'")
        print(f"[DEBUG EDIT]   IPv6 Mask: '{ipv6_mask}'")

        dialog = AddDeviceDialog(self, default_iface=iface)
        print(f"[DEBUG EDIT] Created AddDeviceDialog")

        # Pre-fill basics
        print(f"[DEBUG EDIT] Pre-filling dialog fields")
        dialog.device_name_input.setText(name)
        dialog.iface_input.setText(iface)
        dialog.mac_input.setText(mac)
        dialog.vlan_input.setText(vlan)
        dialog.ipv4_input.setText(ipv4)
        dialog.ipv6_input.setText(ipv6)
        dialog.ipv4_mask_input.setText(ipv4_mask)
        dialog.ipv6_mask_input.setText(ipv6_mask)
        # Set gateway fields - use IPv4 Gateway for the main gateway field
        dialog.ipv4_gateway_input.setText(device.get("IPv4 Gateway", device.get("Gateway", "")))
        dialog.ipv6_gateway_input.setText(device.get("IPv6 Gateway", ""))
        
        # Set loopback IP fields
        dialog.loopback_ipv4_input.setText(device.get("Loopback IPv4", ""))
        dialog.loopback_ipv6_input.setText(device.get("Loopback IPv6", ""))

        # Set checkboxes based on whether fields have values
        # This enables/disables the input fields
        dialog.ipv4_checkbox.setChecked(bool(ipv4.strip()))
        dialog.ipv6_checkbox.setChecked(bool(ipv6.strip()))

        print(f"[DEBUG EDIT] Pre-filled dialog, showing dialog")
        print(f"[DEBUG EDIT] VLAN field value: '{dialog.vlan_input.text()}'")

        # Note: Protocol-specific configuration is not available in simplified table
        # Users can configure protocols separately using the protocol tabs

        if dialog.exec_() != dialog.Accepted:
            print(f"[DEBUG EDIT] Dialog cancelled")
            return
        
        print(f"[DEBUG EDIT] Dialog accepted, getting values")

        # Get updated values from dialog (simplified format)
        (
            new_name, iface, mac, ipv4, ipv6, ipv4_mask, ipv6_mask,
            vlan, ipv4_gateway, ipv6_gateway, inc_mac, inc_ipv4, inc_ipv6, inc_gateway, inc_vlan, count, bgp_config_edit,
            ipv4_octet_index_edit, ipv6_hextet_index_edit, mac_byte_index_edit, gateway_octet_index_edit, loopback_ipv4, loopback_ipv6
        ) = dialog.get_values()

        print(f"[DEBUG EDIT] Got values from dialog:")
        print(f"[DEBUG EDIT]   new_name: '{new_name}'")
        print(f"[DEBUG EDIT]   iface: '{iface}'")
        print(f"[DEBUG EDIT]   mac: '{mac}'")
        print(f"[DEBUG EDIT]   ipv4: '{ipv4}'")
        print(f"[DEBUG EDIT]   ipv6: '{ipv6}'")
        print(f"[DEBUG EDIT]   ipv4_mask: '{ipv4_mask}'")
        print(f"[DEBUG EDIT]   ipv6_mask: '{ipv6_mask}'")
        print(f"[DEBUG EDIT]   vlan: '{vlan}'")

        # Check if IP addresses or VLAN changed - if so, we need to clean up old configuration first
        old_ipv4 = device_info.get("IPv4", "")
        old_ipv6 = device_info.get("IPv6", "")
        old_vlan = device_info.get("VLAN", "0")
        old_interface = device_info.get("Interface", "")
        
        ip_addresses_changed = (
            old_ipv4 != ipv4 or 
            old_ipv6 != ipv6 or 
            old_vlan != vlan
        )
        
        # If configuration changed, mark device for cleanup before applying
        if ip_addresses_changed:
            device_info["_needs_cleanup"] = True
            print(f"[DEBUG EDIT] Configuration changed - will cleanup old configuration before applying new ones")
            print(f"[DEBUG EDIT] Old IPv4: '{old_ipv4}' -> New IPv4: '{ipv4}'")
            print(f"[DEBUG EDIT] Old IPv6: '{old_ipv6}' -> New IPv6: '{ipv6}'")
            print(f"[DEBUG EDIT] Old VLAN: '{old_vlan}' -> New VLAN: '{vlan}'")
            
            # Store old configuration for cleanup
            device_info["_old_config"] = {
                "vlan": old_vlan,
                "interface": old_interface,
                "ipv4": old_ipv4,
                "ipv6": old_ipv6
            }
            print(f"[DEBUG EDIT] Stored old configuration for cleanup: {device_info['_old_config']}")

        # Update device in data structure
        device_info.update({
            "Device Name": new_name or name,
            "Interface": iface,
            "MAC Address": mac,
            "IPv4": ipv4,
            "IPv6": ipv6,
            "VLAN": vlan,
            "Gateway": ipv4_gateway,  # Use IPv4 gateway as primary gateway
            "IPv4 Gateway": ipv4_gateway,
            "IPv6 Gateway": ipv6_gateway,
            "ipv4_mask": ipv4_mask or "24",
            "ipv6_mask": ipv6_mask or "64",
            "Loopback IPv4": loopback_ipv4 if loopback_ipv4 else "",
            "Loopback IPv6": loopback_ipv6 if loopback_ipv6 else "",
            "_needs_apply": True  # Mark for server update
        })
        
        print(f"[DEBUG EDIT] Updated device_info: {device_info}")

        # Update table display
        self.devices_table.item(row, self.COL["Device Name"]).setText(new_name or name)
        self.devices_table.item(row, self.COL["MAC Address"]).setText(mac)
        
        # Update IPv4 with mask
        ipv4_item = self.devices_table.item(row, self.COL["IPv4"])
        if ipv4_item:
            ipv4_item.setText(ipv4)
            ipv4_item.setData(Qt.UserRole + 1, ipv4_mask or "24")
        
        # Update IPv6 with mask  
        ipv6_item = self.devices_table.item(row, self.COL["IPv6"])
        if ipv6_item:
            ipv6_item.setText(ipv6)
            ipv6_item.setData(Qt.UserRole + 1, ipv6_mask or "64")
        
        # Update gateways
        self.devices_table.item(row, self.COL["IPv4 Gateway"]).setText(ipv4_gateway)
        self.devices_table.item(row, self.COL["IPv6 Gateway"]).setText(ipv6_gateway)
        
        # Update mask columns
        self.devices_table.item(row, self.COL["IPv4 Mask"]).setText(ipv4_mask or "24")
        self.devices_table.item(row, self.COL["IPv6 Mask"]).setText(ipv6_mask or "64")
        
        # Update VLAN column
        self.devices_table.item(row, self.COL["VLAN"]).setText(vlan)
        
        # Update Loopback IP columns
        if self.devices_table.item(row, self.COL["Loopback IPv4"]):
            self.devices_table.item(row, self.COL["Loopback IPv4"]).setText(loopback_ipv4 if loopback_ipv4 else "")
        if self.devices_table.item(row, self.COL["Loopback IPv6"]):
            self.devices_table.item(row, self.COL["Loopback IPv6"]).setText(loopback_ipv6 if loopback_ipv6 else "")

        # Refresh the entire table to ensure consistency
        self.update_device_table(self.main_window.all_devices)
        
        # Update BGP table if any devices have BGP configured
        self.update_bgp_table()
        
        # Update OSPF table if any devices have OSPF configured
        self.update_ospf_table()

        QMessageBox.information(self, "Device Updated", 
                               f"Device '{new_name or name}' updated locally.\n\n"
                               f"Click 'Apply' to update on server and save to session.")

    def copy_selected_device(self):
        """Copy the selected device(s) to clipboard for pasting."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to copy.")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        copied_devices = []
        device_names = []
        
        # Process each selected row
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if device_info:
                # Store the copied device data (excluding device_id and status)
                copied_device = {
                    "Device Name": device_info.get("Device Name", ""),
                    "MAC Address": device_info.get("MAC Address", ""),
                    "IPv4": device_info.get("IPv4", ""),
                    "IPv6": device_info.get("IPv6", ""),
                    "ipv4_mask": device_info.get("ipv4_mask", "24"),
                    "ipv6_mask": device_info.get("ipv6_mask", "64"),
                    "VLAN": device_info.get("VLAN", "0"),
                    "Interface": device_info.get("Interface", ""),
                }
                copied_devices.append(copied_device)
                device_names.append(device_name)
            else:
                QMessageBox.warning(self, "Device Not Found", f"Could not find device '{device_name}' in data structure.")
                return
        
        # Store in main window for access by paste function
        self.main_window.copied_device = copied_devices
        
        if len(copied_devices) == 1:
            QMessageBox.information(self, "Device Copied", 
                                   f"Device '{device_names[0]}' has been copied to clipboard.\n\n"
                                   f"Select a port and use 'Paste Device' to create a copy.")
        else:
            QMessageBox.information(self, "Devices Copied", 
                                   f"{len(copied_devices)} devices have been copied to clipboard:\n"
                                   f"{', '.join(device_names)}\n\n"
                                   f"Select a port and use 'Paste Device' to create copies.")

    def paste_device_to_interface(self):
        """Paste the copied device(s) to the selected interface."""
        if not hasattr(self.main_window, 'copied_device') or not self.main_window.copied_device:
            QMessageBox.warning(self, "Nothing to Paste", "No device has been copied. Please copy a device first.")
            return

        # Check if a port is selected
        selected_items = self.main_window.server_tree.selectedItems()
        if not selected_items or not selected_items[0].parent():
            QMessageBox.warning(self, "No Port Selected", "Please select a port to paste the device(s) to.")
            return

        # Get the target interface
        parent_item = selected_items[0].parent()
        tg_id = parent_item.text(0).strip()
        port_name = selected_items[0].text(0).replace("• ", "").strip()  # Remove bullet prefix
        target_interface = f"{tg_id} - {port_name}"  # Match server tree format

        # Get the copied device data (can be single device or list)
        copied_devices = self.main_window.copied_device
        if not isinstance(copied_devices, list):
            copied_devices = [copied_devices]  # Convert single device to list for uniform processing

        # Get all existing device names for unique name generation
        existing_names = [
            d.get("Device Name", "")
            for dev_list in self.main_window.all_devices.values()
            for d in (dev_list if isinstance(dev_list, list) else [])
        ]
        
        pasted_devices = []
        
        # Process each copied device
        for copied_device in copied_devices:
            # Generate a unique name for the pasted device
            base_name = copied_device.get("Device Name", "Device")
            new_name = f"{base_name}_Copy"
            counter = 1
            while new_name in existing_names:
                counter += 1
                new_name = f"{base_name}_Copy_{counter}"
            
            # Add to existing names to prevent duplicates within this batch
            existing_names.append(new_name)

            # Create new device data
            new_device = {
                "Device Name": new_name,
                "device_id": str(uuid.uuid4()),
                "Interface": target_interface,
                "MAC Address": copied_device.get("MAC Address", ""),
                "IPv4": copied_device.get("IPv4", ""),
                "IPv6": copied_device.get("IPv6", ""),
                "ipv4_mask": copied_device.get("ipv4_mask", "24"),
                "ipv6_mask": copied_device.get("ipv6_mask", "64"),
                "VLAN": copied_device.get("VLAN", "0"),
                "Status": "Stopped",
                "_is_new": True,
                "_needs_apply": True
            }

            # Add to all_devices data structure
            if target_interface not in self.main_window.all_devices:
                self.main_window.all_devices[target_interface] = []
            
            self.main_window.all_devices[target_interface].append(new_device)
            
            # Update interface_to_device_map
            if not hasattr(self.main_window, 'interface_to_device_map'):
                self.main_window.interface_to_device_map = {}
            self.main_window.interface_to_device_map[new_name] = new_device
            
            pasted_devices.append(new_name)

        # Refresh the device table
        self.update_device_table(self.main_window.all_devices)
        
        # Update BGP table if any devices have BGP configured
        self.update_bgp_table()
        
        # Update OSPF table if any devices have OSPF configured
        self.update_ospf_table()

        if len(pasted_devices) == 1:
            QMessageBox.information(self, "Device Pasted", 
                                   f"Device '{pasted_devices[0]}' has been pasted to {target_interface}.\n\n"
                                   f"Click 'Apply' to configure on server and save to session.")
        else:
            QMessageBox.information(self, "Devices Pasted", 
                                   f"{len(pasted_devices)} devices have been pasted to {target_interface}:\n"
                                   f"{', '.join(pasted_devices)}\n\n"
                                   f"Click 'Apply' to configure on server and save to session.")

    def prompt_manage_route_pools(self):
        """Open dialog to manage BGP route pools (Step 1: Define pools globally)."""
        # Get server URL
        server_url = self.get_server_url()
        if not server_url:
            return
        
        # Get existing route pools from main window session
        if not hasattr(self.main_window, 'bgp_route_pools'):
            self.main_window.bgp_route_pools = []
        
        existing_pools = self.main_window.bgp_route_pools
        
        # Open dialog with server URL
        dialog = ManageRoutePoolsDialog(self, existing_pools=existing_pools, server_url=server_url)
        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get updated pools
        self.main_window.bgp_route_pools = dialog.get_pools()
        
        # Save to session
        self.main_window.save_session()
        
        pool_count = len(self.main_window.bgp_route_pools)
        print(f"[BGP ROUTE POOLS] Saved {pool_count} route pool(s)")
        QMessageBox.information(self, "Route Pools Saved", 
                              f"Saved {pool_count} route pool(s).\n\n"
                              f"Use 📍 'Attach Route Pools' to assign pools to devices.")
    
    def _find_device_by_name(self, device_name):
        """Safely find a device by name in all_devices, handling data structure issues."""
        if not hasattr(self.main_window, 'all_devices') or not self.main_window.all_devices:
            return None
        
        for iface, devices in self.main_window.all_devices.items():
            if not isinstance(devices, list):
                continue
                
            for device in devices:
                # Handle both dict and list cases
                if isinstance(device, dict):
                    if device.get("Device Name") == device_name:
                        return device
                elif isinstance(device, list) and len(device) > 0:
                    # If device is a list, try to find a dict with matching name
                    for item in device:
                        if isinstance(item, dict) and item.get("Device Name") == device_name:
                            return item
                    # If no match found in list items, don't return the list - this was causing the issue
                    continue
        
        return None

    def _set_bgp_interim_stopping_state(self, device_name, selected_neighbors):
        """Set interim 'Stopping' state for selected BGP neighbors."""
        print(f"[BGP INTERIM] Setting 'Stopping' state for device {device_name}, neighbors: {selected_neighbors}")
        
        # Find rows in BGP table that match the device and selected neighbors
        for row in range(self.bgp_table.rowCount()):
            device_item = self.bgp_table.item(row, 0)  # Device column
            neighbor_item = self.bgp_table.item(row, 3)  # Neighbor IP column
            
            if device_item and neighbor_item:
                table_device_name = device_item.text()
                table_neighbor_ip = neighbor_item.text()
                
                # Remove "(Pending Removal)" suffix if present
                if " (Pending Removal)" in table_device_name:
                    table_device_name = table_device_name.replace(" (Pending Removal)", "")
                
                # Check if this row matches our device
                if table_device_name == device_name:
                    # If specific neighbors are selected, only set stopping for those
                    # If no specific neighbors, set stopping for all neighbors of this device
                    if not selected_neighbors or table_neighbor_ip in selected_neighbors:
                        # Set the status to "Stopping" with yellow dot
                        status_item = QTableWidgetItem("")
                        status_item.setIcon(self.yellow_dot)
                        status_item.setToolTip("BGP Stopping")
                        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
                        self.bgp_table.setItem(row, 1, status_item)
                        
                        print(f"[BGP INTERIM] Set 'Stopping' state for {table_device_name} -> {table_neighbor_ip}")

    def prompt_attach_route_pools(self):
        """Open dialog to attach route pools to selected BGP neighbors (Step 2: Attach to BGP)."""
        # Get selection from BGP table (not devices table)
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            # No rows selected - select all rows
            total_rows = self.bgp_table.rowCount()
            if total_rows > 0:
                self.bgp_table.selectAll()
                print(f"[BGP TABLE] All {total_rows} rows selected")
                return
            else:
                QMessageBox.warning(self, "No BGP Neighbors", "No BGP neighbors are configured. Please add BGP neighbors first.")
                return
        
        # Get available route pools
        if not hasattr(self.main_window, 'bgp_route_pools'):
            self.main_window.bgp_route_pools = []
        
        available_pools = self.main_window.bgp_route_pools
        
        if not available_pools:
            QMessageBox.warning(self, "No Route Pools", 
                              "No route pools have been defined.\n\n"
                              "Please use 🗂️ 'Manage Route Pools' button (in Devices tab) to create pools first.")
            return
        
        # Collect all selected BGP neighbors
        selected_neighbors = []
        processed_devices = set()
        
        for item in selected_items:
            row = item.row()
            device_name = self.bgp_table.item(row, 0).text()  # Device column
            neighbor_ip = self.bgp_table.item(row, 3).text()  # Neighbor IP column
            
            # Clean device name - remove any suffixes like "(Pending Removal)"
            clean_device_name = device_name.split(" (")[0].strip()
            if clean_device_name != device_name:
                device_name = clean_device_name
            
            # Avoid duplicates
            neighbor_key = f"{device_name}:{neighbor_ip}"
            if neighbor_key in processed_devices:
                continue
            processed_devices.add(neighbor_key)
            
            # Find device in all_devices using safe helper
            device_info = self._find_device_by_name(device_name)
            
            if not device_info:
                print(f"[BGP ROUTE POOLS] Warning: Could not find device '{device_name}'")
                continue
            
            # Ensure device_info is a dictionary - handle list case
            if not isinstance(device_info, dict):
                print(f"[BGP ROUTE POOLS] Warning: device_info is not a dict for '{device_name}', it's {type(device_info)}")
                # Try to extract dict from list if it's a list
                if isinstance(device_info, list) and len(device_info) > 0:
                    print(f"[BGP ROUTE POOLS] Attempting to extract dict from list...")
                    device_info = device_info[0] if isinstance(device_info[0], dict) else None
                    if device_info is None:
                        print(f"[BGP ROUTE POOLS] Could not extract dict from list for '{device_name}'")
                        continue
                else:
                    continue
            
            # Final check - ensure device_info is now a dict
            if not isinstance(device_info, dict):
                print(f"[BGP ROUTE POOLS] Final check failed: device_info is still not a dict for '{device_name}'")
                continue
            
            # Get BGP config for this device
            # Check if BGP is in the protocols list
            protocols = device_info.get("protocols", [])
            if not isinstance(protocols, list) or "BGP" not in protocols:
                print(f"[BGP ROUTE POOLS] Warning: Device '{device_name}' does not have BGP configured")
                continue
            
            # Get the actual BGP configuration
            bgp_config = device_info.get("bgp_config", {})
            if not bgp_config:
                print(f"[BGP ROUTE POOLS] Warning: Device '{device_name}' does not have BGP configuration")
                continue
            
            selected_neighbors.append({
                "device_name": device_name,
                "neighbor_ip": neighbor_ip,
                "device_info": device_info,
                "bgp_config": bgp_config
            })
        
        if not selected_neighbors:
            QMessageBox.warning(self, "No Valid BGP Neighbors", 
                              "No valid BGP neighbors found in the selection.")
            return
        
        # If only one neighbor, use the original dialog
        if len(selected_neighbors) == 1:
            neighbor = selected_neighbors[0]
            device_name = neighbor["device_name"]
            neighbor_ip = neighbor["neighbor_ip"]
            bgp_config = neighbor["bgp_config"]
            
            # Get existing attached pool names for this BGP neighbor
            if "route_pools" not in bgp_config:
                bgp_config["route_pools"] = {}
            
            attached_pool_names = bgp_config["route_pools"].get(neighbor_ip, [])
            
            # Open dialog
            dialog = AttachRoutePoolsDialog(self, 
                                            device_name=f"{device_name} → {neighbor_ip}", 
                                            available_pools=available_pools,
                                            attached_pools=attached_pool_names,
                                            bgp_config=bgp_config)
            if dialog.exec_() != dialog.Accepted:
                return
            
            # Get selected pools
            selected_pools = dialog.get_attached_pools()
            
            # Save to BGP config (per neighbor IP)
            bgp_config["route_pools"][neighbor_ip] = selected_pools
            
            # Mark device as needing apply
            neighbor["device_info"]["_needs_apply"] = True
            
            # Save to session
            self.main_window.save_session()
            
            # Refresh BGP table to show updated pool assignments
            self.update_bgp_table()
            
            # Calculate total routes
            total_routes = 0
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
            
            print(f"[BGP ROUTE POOLS] Attached {len(selected_pools)} pool(s) ({total_routes} routes) to BGP neighbor {neighbor_ip} on device '{device_name}'")
            QMessageBox.information(self, "Route Pools Attached", 
                                  f"Attached {len(selected_pools)} route pool(s) to BGP neighbor {neighbor_ip}.\n\n"
                                  f"Device: {device_name}\n"
                                  f"Total routes to advertise: {total_routes}\n\n"
                                  f"Click 'Apply BGP' to configure routes on server.")
            return
        
        # Multiple neighbors selected - show dialog for bulk attachment
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QDialogButtonBox, QCheckBox, QGroupBox
        
        class BulkAttachRoutePoolsDialog(QDialog):
            def __init__(self, parent, selected_neighbors, available_pools):
                super().__init__(parent)
                self.selected_neighbors = selected_neighbors
                self.available_pools = available_pools
                self.setWindowTitle("Attach Route Pools to Multiple BGP Neighbors")
                self.setFixedSize(600, 400)
                self.setup_ui()
            
            def setup_ui(self):
                layout = QVBoxLayout(self)
                
                # Selected neighbors info
                neighbors_group = QGroupBox("Selected BGP Neighbors")
                neighbors_layout = QVBoxLayout(neighbors_group)
                
                neighbors_text = f"Selected {len(self.selected_neighbors)} BGP neighbor(s)"
                neighbors_label = QLabel(neighbors_text)
                neighbors_label.setWordWrap(True)
                neighbors_layout.addWidget(neighbors_label)
                layout.addWidget(neighbors_group)
                
                # Available pools
                pools_group = QGroupBox("Available Route Pools")
                pools_layout = QVBoxLayout(pools_group)
                
                self.pools_list = QListWidget()
                self.pools_list.setSelectionMode(QListWidget.MultiSelection)
                
                for pool in self.available_pools:
                    pool_item = f"{pool['name']} - {pool['subnet']} ({pool['count']} routes)"
                    self.pools_list.addItem(pool_item)
                
                pools_layout.addWidget(self.pools_list)
                layout.addWidget(pools_group)
                
                # Buttons
                button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                button_box.accepted.connect(self.accept)
                button_box.rejected.connect(self.reject)
                layout.addWidget(button_box)
            
            def get_selected_pools(self):
                selected_items = self.pools_list.selectedItems()
                selected_pool_names = []
                for item in selected_items:
                    pool_name = item.text().split(" - ")[0]
                    selected_pool_names.append(pool_name)
                return selected_pool_names
        
        # Open bulk dialog
        dialog = BulkAttachRoutePoolsDialog(self, selected_neighbors, available_pools)
        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get selected pools
        selected_pools = dialog.get_selected_pools()
        
        if not selected_pools:
            QMessageBox.warning(self, "No Pools Selected", "Please select at least one route pool to attach.")
            return
        
        # Apply to all selected neighbors
        total_neighbors = 0
        total_routes = 0
        
        for neighbor in selected_neighbors:
            device_name = neighbor["device_name"]
            neighbor_ip = neighbor["neighbor_ip"]
            bgp_config = neighbor["bgp_config"]
            
            # Initialize route_pools if not exists
            if "route_pools" not in bgp_config:
                bgp_config["route_pools"] = {}
            
            # Save to BGP config (per neighbor IP)
            bgp_config["route_pools"][neighbor_ip] = selected_pools
            
            # Mark device as needing apply
            neighbor["device_info"]["_needs_apply"] = True
            
            total_neighbors += 1
            
            # Calculate routes for this neighbor
            for pool_name in selected_pools:
                for pool in available_pools:
                    if pool["name"] == pool_name:
                        total_routes += pool["count"]
                        break
        
        # Save to session
        self.main_window.save_session()
        
        # Refresh BGP table to show updated pool assignments
        self.update_bgp_table()
        
        print(f"[BGP ROUTE POOLS] Attached {len(selected_pools)} pool(s) to {total_neighbors} BGP neighbor(s)")
        QMessageBox.information(self, "Route Pools Attached", 
                              f"Successfully attached {len(selected_pools)} route pool(s) to {total_neighbors} BGP neighbor(s).\n\n"
                              f"Total routes to advertise: {total_routes}\n\n"
                              f"Click 'Apply BGP' to configure routes on server.")

    def _check_arp_resolution_sync(self, device_info):
        """Check if ARP/Neighbor resolution is working for the device's target from database."""
        import requests
        
        device_name = device_info.get("Device Name", "Unknown")
        device_id = device_info.get("device_id", "")
        
        iface_label = device_info.get("Interface", "")
        if not iface_label:
            return False, "No interface configured"
        
        # Get server URL from the interface label
        server_url = self._get_server_url_from_interface(iface_label)
        if not server_url:
            return False, "No server URL found for interface"
        
        # Get ARP status from database instead of direct server check
        try:
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
            if response.status_code == 200:
                device_data = response.json()
                
                # Get ARP status from database
                arp_ipv4_resolved = device_data.get('arp_ipv4_resolved', 0)
                arp_ipv6_resolved = device_data.get('arp_ipv6_resolved', 0)
                arp_gateway_resolved = device_data.get('arp_gateway_resolved', 0)
                arp_status = device_data.get('arp_status', 'Unknown')
                
                # Convert database values to boolean
                ipv4_resolved = bool(arp_ipv4_resolved)
                ipv6_resolved = bool(arp_ipv6_resolved)
                gateway_resolved = bool(arp_gateway_resolved)
                
                # Determine overall status - success if BOTH IPv4 and IPv6 resolve
                overall_resolved = ipv4_resolved and ipv6_resolved
                
                # Debug info available if needed
                
                return overall_resolved, arp_status
            else:
                print(f"[DEBUG ARP SYNC DATABASE] Failed to get device data: {response.status_code}")
                return False, "Database error"
        except Exception as e:
            print(f"[DEBUG ARP SYNC DATABASE] Error getting ARP status from database: {e}")
            return False, f"Database error: {str(e)}"

    def _check_individual_arp_resolution(self, device_info):
        """Check ARP resolution for individual IPs from database instead of direct server check."""
        import requests
        
        device_name = device_info.get("Device Name", "Unknown")
        device_id = device_info.get("device_id", "")
        
        # Starting ARP check for device
        
        iface_label = device_info.get("Interface", "")
        if not iface_label:
            return {"overall_resolved": False, "overall_status": "No interface configured", 
                    "ipv4_resolved": False, "ipv6_resolved": False, "gateway_resolved": False}
        
        # Get server URL from the interface label
        server_url = self._get_server_url_from_interface(iface_label)
        if not server_url:
            return {"overall_resolved": False, "overall_status": "No server URL found", 
                    "ipv4_resolved": False, "ipv6_resolved": False, "gateway_resolved": False}
        
        # Get ARP status from database instead of direct server check
        try:
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
            if response.status_code == 200:
                device_data = response.json()
                
                # Get ARP status from database
                arp_ipv4_resolved = device_data.get('arp_ipv4_resolved', 0)
                arp_ipv6_resolved = device_data.get('arp_ipv6_resolved', 0)
                arp_gateway_resolved = device_data.get('arp_gateway_resolved', 0)
                arp_status = device_data.get('arp_status', 'Unknown')
                last_arp_check = device_data.get('last_arp_check', '')
                
                # Convert database values to boolean
                ipv4_resolved = bool(arp_ipv4_resolved)
                ipv6_resolved = bool(arp_ipv6_resolved)
                gateway_resolved = bool(arp_gateway_resolved)
                
                # Determine overall status - success if BOTH IPv4 and IPv6 resolve
                overall_resolved = ipv4_resolved and ipv6_resolved
                
                results = {
                    "ipv4_resolved": ipv4_resolved,
                    "ipv6_resolved": ipv6_resolved,
                    "gateway_resolved": gateway_resolved,
                    "ipv4_status": "Resolved" if ipv4_resolved else "Failed",
                    "ipv6_status": "Resolved" if ipv6_resolved else "Failed", 
                    "gateway_status": "Resolved" if gateway_resolved else "Failed",
                    "overall_status": arp_status,
                    "last_check": last_arp_check
                }
                
                # Debug info available if needed
                
                return {
                    "overall_resolved": overall_resolved,
                    "overall_status": arp_status,
                    **results
                }
            else:
                print(f"[DEBUG ARP DATABASE] Failed to get device data: {response.status_code}")
                return {"overall_resolved": False, "overall_status": "Database error", 
                        "ipv4_resolved": False, "ipv6_resolved": False, "gateway_resolved": False}
        except Exception as e:
            print(f"[DEBUG ARP DATABASE] Error getting ARP status from database: {e}")
            return {"overall_resolved": False, "overall_status": f"Database error: {str(e)}", 
                    "ipv4_resolved": False, "ipv6_resolved": False, "gateway_resolved": False}
    
    def check_arp_resolution(self, device_info):
        """Asynchronous ARP resolution check that doesn't block the UI."""
        # For backward compatibility, we'll use a simple approach:
        # Start a worker thread for this single check and return immediately
        # The caller should handle the result via signals
        
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            print("[ARP CHECK] Skipping ARP check - application is closing")
            return False, "Application closing"
        
        # Create a single-item list for the worker
        devices_to_check = [(0, device_info)]  # row 0 is a placeholder
        
        # Create and start worker
        self.arp_check_worker = ArpCheckWorker(devices_to_check, self)
        self.arp_check_worker.arp_result.connect(self._on_arp_check_result)
        self.arp_check_worker.finished.connect(self._on_arp_check_finished)
        self.arp_check_worker.start()
        
        # Return a placeholder result immediately (non-blocking)
        return False, "Checking in background..."
    
    def check_arp_resolution_bulk_async(self, devices_data):
        """Check ARP resolution for multiple devices asynchronously."""
        # devices_data should be a list of (row, device_info) tuples
        
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            print("[ARP BULK] Skipping ARP check - application is closing")
            return
        
        # Create and start worker
        self.bulk_arp_worker = ArpCheckWorker(devices_data, self)
        self.bulk_arp_worker.arp_result.connect(self._on_bulk_arp_result)
        self.bulk_arp_worker.finished.connect(self._on_bulk_arp_finished)
        self.bulk_arp_worker.start()
        
        print(f"[ARP BULK] Started async ARP check for {len(devices_data)} devices")
    
    def _on_arp_check_result(self, row, resolved, status):
        """Handle ARP check result from worker thread."""
        # This can be overridden by callers to handle the actual result
        pass
    
    def _on_bulk_arp_result(self, row, resolved, status):
        """Handle bulk ARP check result from worker thread."""
        # Update the status icon for this row
        self.set_status_icon(row, resolved=resolved, status_text=status)
    
    def _on_arp_check_finished(self):
        """Handle ARP check completion."""
        # Clean up worker reference
        if hasattr(self, 'arp_check_worker'):
            self.arp_check_worker.deleteLater()
            delattr(self, 'arp_check_worker')
    
    def _on_device_apply_result(self, operation_type, result_data):
        """Handle successful device apply result from background worker."""
        try:
            device_name = result_data.get("device_name", "Unknown")
            print(f"✅ Successfully applied device configuration for '{device_name}'")
            
            # Trigger BGP status check after successful apply
            if hasattr(self, 'bgp_monitor') and self.bgp_monitor:
                self.bgp_monitor.force_check()
            
            # Proactive ARP refresh after device apply
            try:
                # Find the device row to refresh ARP for
                device_row = None
                for row in range(self.devices_table.rowCount()):
                    if self.devices_table.item(row, self.COL["Device Name"]):
                        table_device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
                        if table_device_name == device_name:
                            device_row = row
                            break
                
                if device_row is not None:
                    # Wait a moment for the interface to be configured
                    import time
                    time.sleep(3)
                    
                    # Trigger ARP refresh for this specific device
                    self._refresh_device_table_from_database([device_row])
                    print(f"[DEVICE APPLY] Triggered ARP refresh for {device_name}")
                else:
                    print(f"[DEVICE APPLY] Could not find device row for {device_name}")
                
            except Exception as e:
                print(f"[DEVICE APPLY] Failed to refresh ARP for {device_name}: {e}")
                
        except Exception as e:
            print(f"[DEVICE APPLY RESULT] Error handling result: {e}")
    
    def _on_device_apply_error(self, operation_type, error_message):
        """Handle device apply error from background worker."""
        try:
            print(f"❌ Device apply failed: {error_message}")
        except Exception as e:
            print(f"[DEVICE APPLY ERROR] Error handling error: {e}")
    
    def _on_device_apply_finished(self, operation_type):
        """Handle device apply completion from background worker."""
        try:
            # Clean up worker reference
            if hasattr(self, 'db_worker'):
                self.db_worker.deleteLater()
                delattr(self, 'db_worker')
        except Exception as e:
            print(f"[DEVICE APPLY FINISHED] Error cleaning up: {e}")
    
    def _on_multi_device_applied(self, device_name, success, message):
        """Handle individual device apply result from multi-device worker."""
        try:
            print(f"[MULTI DEVICE APPLY] {message}")
            
            # If device was successfully applied, trigger ARP status check
            if success:
                # Find the device row to update ARP status
                device_row = None
                for row in range(self.devices_table.rowCount()):
                    if self.devices_table.item(row, self.COL["Device Name"]):
                        table_device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
                        if table_device_name == device_name:
                            device_row = row
                            break
                
                if device_row is not None:
                    # Wait a moment for the interface to be configured
                    import time
                    time.sleep(2)
                    
                    # Force ARP check on server to update database with current status
                    try:
                        server_url = self.get_server_url(silent=True)
                        if server_url:
                            import requests
                            response = requests.post(f"{server_url}/api/device/arp/force-check", timeout=5)
                            if response.status_code == 200:
                                print(f"[MULTI DEVICE APPLY] Triggered ARP force check on server")
                            else:
                                print(f"[MULTI DEVICE APPLY] Failed to trigger ARP force check: {response.status_code}")
                    except Exception as e:
                        print(f"[MULTI DEVICE APPLY] Error triggering ARP force check: {e}")
                    
                    # Wait a moment for ARP check to complete
                    time.sleep(1)
                    
                    # Trigger ARP refresh for this specific device
                    self._refresh_device_table_from_database([device_row])
                    print(f"[MULTI DEVICE APPLY] Triggered ARP refresh for {device_name}")
                else:
                    print(f"[MULTI DEVICE APPLY] Could not find device row for {device_name}")
                    
        except Exception as e:
            print(f"[MULTI DEVICE APPLY] Error handling result: {e}")
    
    def _on_multi_device_progress(self, device_name, status_message):
        """Handle progress updates from multi-device worker."""
        try:
            print(f"[MULTI DEVICE APPLY] {device_name}: {status_message}")
        except Exception as e:
            print(f"[MULTI DEVICE APPLY] Error handling progress: {e}")
    
    def _on_multi_device_apply_finished(self, results, successful_count, failed_count):
        """Handle completion of multi-device apply worker."""
        try:
            # Print results to console
            if results:
                print(f"\n{'='*60}")
                print(f"MULTI DEVICE APPLY RESULTS: {successful_count} successful, {failed_count} failed")
                print(f"{'='*60}")
                for result in results:
                    print(f"  {result}")
                print(f"{'='*60}\n")
            
            # Save session after device application to persist status changes
            if successful_count > 0 and hasattr(self.main_window, "save_session"):
                print(f"[MULTI DEVICE APPLY] Saving session after successful device application")
                self.main_window.save_session()
            
            # Clean up worker reference
            if hasattr(self, 'multi_device_apply_worker'):
                self.multi_device_apply_worker.deleteLater()
                delattr(self, 'multi_device_apply_worker')
            
            # Clear the operation type flag
            if hasattr(self, '_current_operation_type'):
                delattr(self, '_current_operation_type')
                
        except Exception as e:
            print(f"[MULTI DEVICE APPLY FINISHED] Error handling completion: {e}")
    
    def _on_bulk_arp_finished(self):
        """Handle bulk ARP check completion."""
        print("[ARP BULK] Completed async ARP checks for all devices")
        # Reset the flag to allow new ARP checks
        self._arp_check_in_progress = False
        # Clean up worker reference
        if hasattr(self, 'bulk_arp_worker'):
            self.bulk_arp_worker.deleteLater()
            delattr(self, 'bulk_arp_worker')

    def cleanup_threads(self):
        """Clean up all running threads before application exit."""
        print("[CLEANUP] Cleaning up all worker threads...")
        
        # Stop all timers first to prevent new thread creation
        if hasattr(self, 'status_timer') and self.status_timer:
            print("[CLEANUP] Stopping status_timer...")
            self.status_timer.stop()
        
        if hasattr(self, 'bgp_monitoring_timer') and self.bgp_monitoring_timer:
            print("[CLEANUP] Stopping bgp_monitoring_timer...")
            self.bgp_monitoring_timer.stop()
        
        if hasattr(self, 'ospf_monitoring_timer') and self.ospf_monitoring_timer:
            print("[CLEANUP] Stopping ospf_monitoring_timer...")
            self.ospf_monitoring_timer.stop()
        
        if hasattr(self, 'isis_monitoring_timer') and self.isis_monitoring_timer:
            print("[CLEANUP] Stopping isis_monitoring_timer...")
            self.isis_monitoring_timer.stop()
        
        if hasattr(self, 'device_status_timer') and self.device_status_timer:
            print("[CLEANUP] Stopping device_status_timer...")
            self.device_status_timer.stop()
        
        # Stop and cleanup ARP check worker
        if hasattr(self, 'arp_check_worker') and self.arp_check_worker:
            print("[CLEANUP] Stopping arp_check_worker...")
            try:
                self.arp_check_worker.stop()  # Request graceful stop
                self.arp_check_worker.quit()
                if not self.arp_check_worker.wait(1000):  # Wait up to 1 second
                    print("[CLEANUP] Force terminating arp_check_worker...")
                    self.arp_check_worker.terminate()
                    self.arp_check_worker.wait(500)
            except Exception as e:
                print(f"[CLEANUP] Error stopping arp_check_worker: {e}")
            finally:
                try:
                    self.arp_check_worker.deleteLater()
                    delattr(self, 'arp_check_worker')
                except:
                    pass
        
        # Stop and cleanup bulk ARP worker
        if hasattr(self, 'bulk_arp_worker') and self.bulk_arp_worker:
            print("[CLEANUP] Stopping bulk_arp_worker...")
            try:
                self.bulk_arp_worker.stop()  # Request graceful stop
                self.bulk_arp_worker.quit()
                if not self.bulk_arp_worker.wait(1000):  # Wait up to 1 second
                    print("[CLEANUP] Force terminating bulk_arp_worker...")
                    self.bulk_arp_worker.terminate()
                    self.bulk_arp_worker.wait(500)
            except Exception as e:
                print(f"[CLEANUP] Error stopping bulk_arp_worker: {e}")
            finally:
                try:
                    self.bulk_arp_worker.deleteLater()
                    delattr(self, 'bulk_arp_worker')
                except:
                    pass
        
        # Stop and cleanup device operation worker
        if hasattr(self, 'operation_worker') and self.operation_worker:
            print("[CLEANUP] Stopping operation_worker...")
            try:
                self.operation_worker.stop()  # Request graceful stop
                self.operation_worker.quit()
                if not self.operation_worker.wait(1000):  # Wait up to 1 second
                    print("[CLEANUP] Force terminating operation_worker...")
                    self.operation_worker.terminate()
                    self.operation_worker.wait(500)
            except Exception as e:
                print(f"[CLEANUP] Error stopping operation_worker: {e}")
            finally:
                try:
                    self.operation_worker.deleteLater()
                    delattr(self, 'operation_worker')
                except:
                    pass
        
        # Stop and cleanup ARP operation worker
        if hasattr(self, 'arp_operation_worker') and self.arp_operation_worker:
            print("[CLEANUP] Stopping arp_operation_worker...")
            try:
                self.arp_operation_worker.stop()  # Request graceful stop
                self.arp_operation_worker.quit()
                if not self.arp_operation_worker.wait(1000):  # Wait up to 1 second
                    print("[CLEANUP] Force terminating arp_operation_worker...")
                    self.arp_operation_worker.terminate()
                    self.arp_operation_worker.wait(500)
            except Exception as e:
                print(f"[CLEANUP] Error stopping arp_operation_worker: {e}")
            finally:
                try:
                    self.arp_operation_worker.deleteLater()
                    delattr(self, 'arp_operation_worker')
                except:
                    pass
        
        # Stop and cleanup individual ARP worker
        if hasattr(self, 'individual_arp_worker') and self.individual_arp_worker:
            print("[CLEANUP] Stopping individual_arp_worker...")
            try:
                self.individual_arp_worker.stop()  # Request graceful stop
                self.individual_arp_worker.quit()
                if not self.individual_arp_worker.wait(1000):  # Wait up to 1 second
                    print("[CLEANUP] Force terminating individual_arp_worker...")
                    self.individual_arp_worker.terminate()
                    self.individual_arp_worker.wait(500)
            except Exception as e:
                print(f"[CLEANUP] Error stopping individual_arp_worker: {e}")
            finally:
                try:
                    self.individual_arp_worker.deleteLater()
                    delattr(self, 'individual_arp_worker')
                except:
                    pass
        
        print("[CLEANUP] Thread cleanup completed")

    def closeEvent(self, event):
        """Handle widget close event - cleanup threads."""
        print("[CLEANUP] DevicesTab closing, cleaning up threads...")
        self.cleanup_threads()
        event.accept()

    def send_immediate_arp_request(self, device_info, server_url):
        """ARP requests are now handled by the database - this is a no-op for compatibility."""
        # ARP operations are now handled by the server-side ARP monitor
        # and status is retrieved from the database
        return True, "ARP handled by database"

    def send_arp_request(self, device_info):
        """ARP requests are now handled by the database - this is a no-op for compatibility."""
        # ARP operations are now handled by the server-side ARP monitor
        # and status is retrieved from the database
        return True, "ARP handled by database"

    def update_device_status_icon(self, row, arp_resolved):
        """Update the device status icon based on ARP resolution."""
        from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
        from PyQt5.QtCore import Qt
        
        # Update status icon (Status column is at index 1)
        status_item = self.devices_table.item(row, self.COL["Status"])
        if status_item:
            if arp_resolved:
                # Green icon for resolved ARP
                status_item.setIcon(self.green_dot)
            else:
                # Orange icon for unresolved ARP
                status_item.setIcon(self.orange_dot)
            # Ensure proper center alignment
            status_item.setTextAlignment(Qt.AlignCenter)

    def _on_arp_button_clicked(self):
        """Handle ARP button click - refresh ARP status from database for selected devices."""
        try:
            self.refresh_arp_selected_device()
        except Exception as e:
            print(f"[ARP REFRESH] Error: {e}")

    def send_arp_selected_device(self):
        """Send ARP request for the selected device(s) (non-blocking). First applies any pending configurations."""
        try:
            print(f"[DEBUG ARP] send_arp_selected_device called")
            selected_items = self.devices_table.selectedItems()
            print(f"[DEBUG ARP] Selected items: {len(selected_items)}")
            if not selected_items:
                print(f"[DEBUG ARP] No selected items, showing warning")
                QMessageBox.warning(self, "No Selection", "Please select one or more devices to send ARP request.")
                return
        except Exception as e:
            print(f"[DEBUG ARP] Exception in send_arp_selected_device: {e}")
            import traceback
            traceback.print_exc()
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to send ARP request.")
            return

        # Store selected rows for later use in ARP operation
        self._pending_arp_rows = selected_rows
        
        # Set status to "Applying..." for selected devices
        print(f"[DEBUG ARP] About to set status for {len(selected_rows)} rows: {selected_rows}")
        for row in selected_rows:
            try:
                status_item = self.devices_table.item(row, self.COL["Status"])
                print(f"[DEBUG ARP] Row {row}, Status column index: {self.COL['Status']}, Status item: {status_item}")
                if status_item:
                    print(f"[DEBUG ARP] Setting status to 'Applying...' for row {row}")
                    status_item.setText("Applying...")
                    status_item.setIcon(self.orange_dot)  # Use orange dot to indicate in progress
                    status_item.setToolTip("Applying device configuration...")
                    print(f"[DEBUG ARP] Status set successfully for row {row}")
                else:
                    print(f"[DEBUG ARP] No status item found for row {row}")
            except Exception as e:
                print(f"[DEBUG ARP] Exception setting status for row {row}: {e}")
                import traceback
                traceback.print_exc()
        
        # First, apply any pending configurations for the selected devices
        print(f"[ARP OPERATION] First applying configurations for {len(selected_rows)} devices, then running ARP...")
        self.apply_selected_device_with_arp_chain()

    def refresh_arp_selected_device(self):
        """Refresh ARP status from database for the selected device(s)."""
        try:
            selected_items = self.devices_table.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
                return
        except Exception as e:
            print(f"[ARP REFRESH] Error: {e}")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
            return

        print(f"[ARP REFRESH] Refreshing ARP status for {len(selected_rows)} devices...")
        
        for row in selected_rows:
            try:
                device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
                
                # Find device in all_devices data structure
                device_info = None
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            device_info = device
                            break
                    if device_info:
                        break
                
                if device_info:
                    # Get ARP status from database
                    arp_results = self._check_individual_arp_resolution(device_info)
                    
                    # Update individual IP colors based on ARP results
                    self.set_status_icon_with_individual_ips(row, arp_results)
                    
                    # Update overall status icon
                    overall_resolved = arp_results.get("overall_resolved", False)
                    device_status = device_info.get("Status", "Unknown")
                    self.set_status_icon(row, resolved=overall_resolved, status_text=arp_results.get("overall_status", "Unknown"), device_status=device_status)
                    
                    print(f"[ARP REFRESH] {device_name}: {arp_results.get('overall_status', 'Unknown')}")
                    
            except Exception as e:
                print(f"[ARP REFRESH] Error for {device_name}: {e}")

    def ping_selected_device(self):
        """Ping the selected device(s)."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to ping.")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to ping.")
            return

        # Process each selected device
        results = []
        successful_count = 0
        failed_count = 0
        arp_not_resolved_count = 0
        
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if not device_info:
                results.append(f"❌ {device_name}: Device not found in data structure")
                failed_count += 1
                continue

            # Check ARP resolution first
            arp_resolved, arp_status = self._check_arp_resolution_sync(device_info)
            
            # Update status icon
            self.update_device_status_icon(row, arp_resolved)
            
            if not arp_resolved:
                results.append(f"⚠️ {device_name}: ARP not resolved - {arp_status}")
                arp_not_resolved_count += 1
                continue

            # If ARP is resolved, proceed with ping test
            import requests
            
            # Determine ping target - prefer IPv6 if available, otherwise IPv4
            ipv6 = device_info.get("IPv6", "").strip()
            ipv4 = device_info.get("IPv4", "").strip()
            gateway = device_info.get("Gateway", "").strip()
            
            ping_target = None
            target_type = ""
            ip_version = ""
            
            # Priority: Gateway > IPv6 > IPv4 (Gateway is most important for connectivity)
            if gateway:
                ping_target = gateway
                target_type = "Gateway"
                # Detect gateway IP version
                ip_version = "IPv6" if ":" in gateway else "IPv4"
            elif ipv6:
                ping_target = ipv6
                target_type = "Device IPv6"
                ip_version = "IPv6"
            elif ipv4:
                ping_target = ipv4
                target_type = "Device IPv4"
                ip_version = "IPv4"
            else:
                results.append(f"❌ {device_name}: No IP address or gateway configured")
                failed_count += 1
                continue
            
            # Get server URL from the interface label
            iface_label = device_info.get("Interface", "")
            server_url = self._get_server_url_from_interface(iface_label)
            if not server_url:
                results.append(f"❌ {device_name}: No server URL found for interface")
                failed_count += 1
                continue
            
            try:
                # Call server-side ping API
                response = requests.post(f"{server_url}/api/device/ping", 
                                       json={"ip_address": ping_target}, 
                                       timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    result = type('Result', (), {
                        'returncode': 0 if data.get("success", False) else 1,
                        'stdout': data.get("output", ""),
                        'stderr': data.get("error", "")
                    })()
                else:
                    result = type('Result', (), {
                        'returncode': 1,
                        'stdout': "",
                        'stderr': f"Server error: {response.status_code}"
                    })()
                
                if result.returncode == 0:
                    results.append(f"✅ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Reachable")
                    successful_count += 1
                else:
                    results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Not reachable")
                    failed_count += 1
                    
            except requests.exceptions.Timeout:
                results.append(f"⏱️ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Timeout")
                failed_count += 1
            except requests.exceptions.RequestException as e:
                results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Network error: {str(e)}")
                failed_count += 1
            except Exception as e:
                results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Error: {str(e)}")
                failed_count += 1

        # Show summary results using custom dialog
        total_devices = len(selected_rows)
        summary = f"Ping Results ({total_devices} device{'s' if total_devices > 1 else ''}):\n"
        summary += f"✅ Successful: {successful_count} | ❌ Failed: {failed_count} | ⚠️ ARP Not Resolved: {arp_not_resolved_count}"
        
        if arp_not_resolved_count > 0:
            results.append("💡 Tip: Use the Send ARP button (→) first to resolve ARP for devices that need it.")
        
        if successful_count == total_devices:
            title = "All Pings Successful"
        elif successful_count > 0:
            title = "Partial Ping Success"
        else:
            title = "All Pings Failed"
        
        dialog = MultiDeviceResultsDialog(title, summary, results, self)
        dialog.exec_()

    def prompt_add_bgp(self):
        """Add BGP configuration to selected device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to add BGP configuration.")
            return

        row = selected_items[0].row()
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
        
        # Get device IP addresses and gateway addresses from the table
        device_ipv4 = self.devices_table.item(row, self.COL["IPv4"]).text() if self.devices_table.item(row, self.COL["IPv4"]) else ""
        device_ipv6 = self.devices_table.item(row, self.COL["IPv6"]).text() if self.devices_table.item(row, self.COL["IPv6"]) else ""
        gateway_ipv4 = self.devices_table.item(row, self.COL["IPv4 Gateway"]).text() if self.devices_table.item(row, self.COL["IPv4 Gateway"]) else ""
        gateway_ipv6 = self.devices_table.item(row, self.COL["IPv6 Gateway"]).text() if self.devices_table.item(row, self.COL["IPv6 Gateway"]) else ""
        
        dialog = AddBgpDialog(self, device_name, edit_mode=False, device_ipv4=device_ipv4, device_ipv6=device_ipv6, gateway_ipv4=gateway_ipv4, gateway_ipv6=gateway_ipv6)
        if dialog.exec_() != dialog.Accepted:
            return

        bgp_config = dialog.get_values()
        
        # Update the device with BGP configuration
        self._update_device_protocol(row, "BGP", bgp_config)

    def prompt_edit_bgp(self):
        """Edit BGP configuration for selected device."""
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a BGP configuration to edit.")
            return

        # Get unique rows from selection
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if len(selected_rows) > 1:
            QMessageBox.warning(self, "Multiple Selection", "Please select only one BGP configuration to edit.")
            return
        
        row = list(selected_rows)[0]
        device_name = self.bgp_table.item(row, 0).text()  # Device column
        
        # Find the device in all_devices using safe helper
        device_info = self._find_device_by_name(device_name)
        
        if not device_info or "protocols" not in device_info or "BGP" not in device_info["protocols"]:
            QMessageBox.warning(self, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")
            return

        # Get device IP addresses and gateway addresses from device_info
        device_ipv4 = device_info.get("IPv4", "")
        device_ipv6 = device_info.get("IPv6", "")
        gateway_ipv4 = device_info.get("IPv4 Gateway", "")
        gateway_ipv6 = device_info.get("IPv6 Gateway", "")

        # Create dialog with current BGP configuration in edit mode
        dialog = AddBgpDialog(self, device_name, edit_mode=True, device_ipv4=device_ipv4, device_ipv6=device_ipv6, gateway_ipv4=gateway_ipv4, gateway_ipv6=gateway_ipv6)
        
        # Handle both old format (dict) and new format (list)
        if isinstance(device_info["protocols"], dict):
            current_bgp = device_info["protocols"]["BGP"]
        else:
            current_bgp = device_info.get("bgp_config", {})
        
        # Pre-populate the dialog with current values
        dialog.bgp_mode_combo.setCurrentText(current_bgp.get("bgp_mode", "eBGP"))
        dialog.bgp_asn_input.setText(current_bgp.get("bgp_asn", ""))
        dialog.bgp_remote_asn_input.setText(current_bgp.get("bgp_remote_asn", ""))
        
        # Pre-populate timer fields
        dialog.bgp_keepalive_input.setValue(int(current_bgp.get("bgp_keepalive", "30")))
        dialog.bgp_hold_time_input.setValue(int(current_bgp.get("bgp_hold_time", "90")))
        
        # Set protocol checkboxes based on current configuration
        has_ipv4 = bool(current_bgp.get("bgp_neighbor_ipv4"))
        has_ipv6 = bool(current_bgp.get("bgp_neighbor_ipv6"))
        dialog.ipv4_enabled.setChecked(has_ipv4)
        dialog.ipv6_enabled.setChecked(has_ipv6)
        
        # Pre-populate IPv4 fields - combine all IPv4 neighbor IPs
        if has_ipv4:
            dialog.bgp_neighbor_ipv4_input.setText(current_bgp.get("bgp_neighbor_ipv4", ""))
            dialog.bgp_update_source_ipv4_input.setText(current_bgp.get("bgp_update_source_ipv4", ""))
        
        # Pre-populate IPv6 fields - combine all IPv6 neighbor IPs
        if has_ipv6:
            dialog.bgp_neighbor_ipv6_input.setText(current_bgp.get("bgp_neighbor_ipv6", ""))
            dialog.bgp_update_source_ipv6_input.setText(current_bgp.get("bgp_update_source_ipv6", ""))
        
        if dialog.exec_() != dialog.Accepted:
            return

        new_bgp_config = dialog.get_values()
        
        # Preserve existing route pools when editing
        if "route_pools" in current_bgp:
            new_bgp_config["route_pools"] = current_bgp["route_pools"]
        
        # Update the device with new BGP configuration
        if isinstance(device_info["protocols"], dict):
            device_info["protocols"]["BGP"] = new_bgp_config
        else:
            device_info["bgp_config"] = new_bgp_config
        
        # Update the BGP table
        self.update_bgp_table()
        
        # Save session
        if hasattr(self.main_window, "save_session"):
            self.main_window.save_session()

    def prompt_delete_bgp(self):
        """Delete BGP configuration for selected device."""
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a BGP configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.bgp_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self, "Confirm Deletion", 
                                   f"Are you sure you want to delete BGP configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices using safe helper
        device_info = self._find_device_by_name(device_name)
        
        if device_info and "protocols" in device_info and "BGP" in device_info["protocols"]:
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove BGP configuration from server first
                server_url = self.get_server_url()
                if server_url:
                    try:
                        # Call server BGP cleanup endpoint
                        response = requests.post(f"{server_url}/api/bgp/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            print(f"✅ BGP configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            print(f"⚠️ Server BGP cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        print(f"⚠️ Network error removing BGP from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    print("⚠️ No server URL available, removing BGP configuration locally only")
            
            # Mark BGP for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if isinstance(device_info["protocols"], dict):
                device_info["protocols"]["BGP"] = {"_marked_for_removal": True}
            else:
                device_info["bgp_config"] = {"_marked_for_removal": True}
            
            # Update the BGP table to show the device as marked for removal
            self.update_bgp_table()
            
            # Save session
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            
            QMessageBox.information(self, "BGP Configuration Marked for Removal", 
                                  f"BGP configuration for '{device_name}' has been marked for removal. Click 'Apply BGP Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")

    def prompt_edit_ospf(self):
        """Edit OSPF configuration for selected device."""
        selected_items = self.ospf_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an OSPF configuration to edit.")
            return

        row = selected_items[0].row()
        device_name = self.ospf_table.item(row, 0).text()  # Device column
        
        # Find the device in all_devices
        device_info = None
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        if not device_info:
            QMessageBox.warning(self, "Device Not Found", f"Device '{device_name}' not found.")
            return
        
        # Get current OSPF configuration
        current_ospf_config = device_info.get("protocols", {}).get("OSPF", {})
        
        # Create and show OSPF dialog
        from widgets.add_ospf_dialog import AddOspfDialog
        dialog = AddOspfDialog(self, current_ospf_config)
        
        if dialog.exec_() == QDialog.Accepted:
            ospf_config = dialog.get_values()
            
            # Update the device with OSPF configuration
            self._update_device_protocol(row, "OSPF", ospf_config)

    def prompt_delete_ospf(self):
        """Delete OSPF configuration for selected device."""
        selected_items = self.ospf_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select an OSPF configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.ospf_table.item(row, 0).text()  # Device column
        
        # Confirm deletion
        reply = QMessageBox.question(self, "Confirm Deletion", 
                                   f"Are you sure you want to delete OSPF configuration for '{device_name}'?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Find the device in all_devices and remove OSPF configuration
        device_info = None
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        # Check if device has OSPF configuration (handle both old and new formats)
        has_ospf = False
        if device_info and "protocols" in device_info:
            if isinstance(device_info["protocols"], list) and "OSPF" in device_info["protocols"]:
                has_ospf = True
            elif isinstance(device_info["protocols"], dict) and "OSPF" in device_info["protocols"]:
                has_ospf = True
        
        if has_ospf:
            device_id = device_info.get("device_id")
            
            if device_id:
                # Remove OSPF configuration from server first
                server_url = self.get_server_url()
                if server_url:
                    try:
                        # Call server OSPF cleanup endpoint
                        response = requests.post(f"{server_url}/api/ospf/cleanup", 
                                               json={"device_id": device_id}, 
                                               timeout=10)
                        
                        if response.status_code == 200:
                            print(f"✅ OSPF configuration removed from server for {device_name}")
                        else:
                            error_msg = response.json().get("error", "Unknown error")
                            print(f"⚠️ Server OSPF cleanup failed for {device_name}: {error_msg}")
                            # Continue with client-side cleanup even if server fails
                    except requests.exceptions.RequestException as e:
                        print(f"⚠️ Network error removing OSPF from server for {device_name}: {str(e)}")
                        # Continue with client-side cleanup even if server fails
                else:
                    print("⚠️ No server URL available, removing OSPF configuration locally only")
            
            # Mark OSPF for removal instead of immediately deleting it
            # This allows the user to apply the changes to the server later
            if "protocols" in device_info:
                if isinstance(device_info["protocols"], list):
                    # New format: protocols is a list like ["BGP", "OSPF"]
                    if "OSPF" in device_info["protocols"]:
                        device_info["protocols"].remove("OSPF")
                    # Mark OSPF config for removal
                    device_info["ospf_config"] = {"_marked_for_removal": True}
                elif isinstance(device_info["protocols"], dict):
                    # Old format: protocols is a dict like {"OSPF": {...}}
                    if isinstance(device_info["protocols"], dict):
                        device_info["protocols"]["OSPF"] = {"_marked_for_removal": True}
                    else:
                        device_info["ospf_config"] = {"_marked_for_removal": True}
            
            # Update the OSPF table to show the device as marked for removal
            self.update_ospf_table()
            
            # Save session
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            
            QMessageBox.information(self, "OSPF Configuration Marked for Removal", 
                                  f"OSPF configuration for '{device_name}' has been marked for removal. Click 'Apply OSPF Configuration' to remove it from the server.")
        else:
            QMessageBox.warning(self, "No OSPF Configuration", f"No OSPF configuration found for device '{device_name}'.")

    def on_bgp_table_cell_changed(self, row, column):
        """Handle cell changes in BGP table - handles inline editing with separate rows per neighbor."""
        # Get table items with null checks
        device_item = self.bgp_table.item(row, 0)
        if not device_item:
            return
        device_name = device_item.text()  # Device name column
        
        # Find the device in all_devices
        device_info = None
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_info = device
                    break
            if device_info:
                break
        
        if device_info and "protocols" in device_info and "BGP" in device_info["protocols"]:
            # Handle both old format (dict) and new format (list)
            if isinstance(device_info["protocols"], dict):
                bgp_config = device_info["protocols"]["BGP"]
            else:
                bgp_config = device_info.get("bgp_config", {})
            
            # Get current neighbor IPs
            ipv4_neighbors = bgp_config.get("bgp_neighbor_ipv4", "")
            ipv4_ips = [ip.strip() for ip in ipv4_neighbors.split(",") if ip.strip()] if ipv4_neighbors else []
            
            ipv6_neighbors = bgp_config.get("bgp_neighbor_ipv6", "")
            ipv6_ips = [ip.strip() for ip in ipv6_neighbors.split(",") if ip.strip()] if ipv6_neighbors else []
            
            if column == 3:  # Neighbor IP changed (column 3 after adding BGP Status)
                neighbor_ip_item = self.bgp_table.item(row, 3)
                neighbor_type_item = self.bgp_table.item(row, 2)
                
                if neighbor_ip_item and neighbor_type_item:
                    neighbor_ip = neighbor_ip_item.text().strip()
                    neighbor_type = neighbor_type_item.text()
                    
                    # Validate IP address format
                    try:
                        if neighbor_type == "IPv4":
                            if neighbor_ip:  # Only validate if not empty
                                ipaddress.IPv4Address(neighbor_ip)
                        elif neighbor_type == "IPv6":
                            if neighbor_ip:  # Only validate if not empty
                                ipaddress.IPv6Address(neighbor_ip)
                    except ipaddress.AddressValueError:
                        QMessageBox.warning(self, f"Invalid {neighbor_type} Address", 
                                          f"'{neighbor_ip}' is not a valid {neighbor_type} address.")
                        # Revert to original value
                        if neighbor_type == "IPv4" and ipv4_ips:
                            neighbor_ip_item.setText(ipv4_ips[0] if ipv4_ips else "")
                        elif neighbor_type == "IPv6" and ipv6_ips:
                            neighbor_ip_item.setText(ipv6_ips[0] if ipv6_ips else "")
                        return
                    
                    if neighbor_type == "IPv4":
                        # Update IPv4 neighbor IPs - find the correct IPv4 row index
                        # IPv4 rows come first, so we need to find which IPv4 row this is
                        ipv4_row_index = 0
                        for i in range(row):
                            if self.bgp_table.item(i, 1) and self.bgp_table.item(i, 1).text() == "IPv4":
                                ipv4_row_index += 1
                        
                        # Replace the IP at the correct IPv4 index
                        if ipv4_row_index < len(ipv4_ips):
                            ipv4_ips[ipv4_row_index] = neighbor_ip
                        else:
                            # If index is beyond current list, add new IP
                            ipv4_ips.append(neighbor_ip)
                        
                        bgp_config["bgp_neighbor_ipv4"] = ",".join(ipv4_ips)
                    elif neighbor_type == "IPv6":
                        # Update IPv6 neighbor IPs - find the correct IPv6 row index
                        # IPv6 rows come after IPv4 rows, so we need to find which IPv6 row this is
                        ipv6_row_index = 0
                        for i in range(row):
                            if self.bgp_table.item(i, 1) and self.bgp_table.item(i, 1).text() == "IPv6":
                                ipv6_row_index += 1
                        
                        # Replace the IP at the correct IPv6 index
                        if ipv6_row_index < len(ipv6_ips):
                            ipv6_ips[ipv6_row_index] = neighbor_ip
                        else:
                            # If index is beyond current list, add new IP
                            ipv6_ips.append(neighbor_ip)
                        
                        bgp_config["bgp_neighbor_ipv6"] = ",".join(ipv6_ips)
            
            elif column == 4:  # Source IP changed (column 4 after adding BGP Status)
                source_ip_item = self.bgp_table.item(row, 4)
                neighbor_type_item = self.bgp_table.item(row, 2)
                
                if source_ip_item and neighbor_type_item:
                    source_ip = source_ip_item.text().strip()
                    neighbor_type = neighbor_type_item.text()
                    
                    # Validate source IP address format
                    try:
                        if neighbor_type == "IPv4":
                            if source_ip:  # Only validate if not empty
                                ipaddress.IPv4Address(source_ip)
                        elif neighbor_type == "IPv6":
                            if source_ip:  # Only validate if not empty
                                ipaddress.IPv6Address(source_ip)
                    except ipaddress.AddressValueError:
                        QMessageBox.warning(self, f"Invalid {neighbor_type} Source Address", 
                                          f"'{source_ip}' is not a valid {neighbor_type} address.")
                        # Revert to original value
                        if neighbor_type == "IPv4":
                            original_source = bgp_config.get("bgp_update_source_ipv4", "")
                            source_ip_item.setText(original_source)
                        elif neighbor_type == "IPv6":
                            original_source = bgp_config.get("bgp_update_source_ipv6", "")
                            source_ip_item.setText(original_source)
                        return
                    
                    if neighbor_type == "IPv4":
                        bgp_config["bgp_update_source_ipv4"] = source_ip
                    elif neighbor_type == "IPv6":
                        bgp_config["bgp_update_source_ipv6"] = source_ip
            
            elif column == 5:  # Local AS changed (column 5 after adding BGP Status)
                local_as_item = self.bgp_table.item(row, 5)
                if local_as_item:
                    local_as = local_as_item.text().strip()
                    
                    # Validate AS number
                    try:
                        if local_as:  # Only validate if not empty
                            asn = int(local_as)
                            if asn <= 0 or asn > 4294967295:  # Valid ASN range
                                raise ValueError("ASN out of range")
                    except ValueError:
                        QMessageBox.warning(self, "Invalid Local AS Number", 
                                          f"'{local_as}' is not a valid AS number (must be 1-4294967295).")
                        # Revert to original value
                        original_asn = bgp_config.get("bgp_asn", "")
                        local_as_item.setText(original_asn)
                        return
                    
                    bgp_config["bgp_asn"] = local_as
            
            elif column == 6:  # Remote AS changed (column 6 after adding BGP Status)
                remote_as_item = self.bgp_table.item(row, 6)
                if remote_as_item:
                    remote_as = remote_as_item.text().strip()
                    
                    # Validate AS number
                    try:
                        if remote_as:  # Only validate if not empty
                            asn = int(remote_as)
                            if asn <= 0 or asn > 4294967295:  # Valid ASN range
                                raise ValueError("ASN out of range")
                    except ValueError:
                        QMessageBox.warning(self, "Invalid Remote AS Number", 
                                          f"'{remote_as}' is not a valid AS number (must be 1-4294967295).")
                        # Revert to original value
                        original_remote_asn = bgp_config.get("bgp_remote_asn", "")
                        remote_as_item.setText(original_remote_asn)
                        return
                    
                    bgp_config["bgp_remote_asn"] = remote_as
            
            elif column == 10:  # Keepalive timer changed (column 10)
                keepalive_item = self.bgp_table.item(row, 10)
                if keepalive_item:
                    keepalive = keepalive_item.text().strip()
                    
                    # Validate keepalive timer
                    try:
                        if keepalive:  # Only validate if not empty
                            timer_value = int(keepalive)
                            if timer_value < 1 or timer_value > 65535:  # Valid keepalive range
                                raise ValueError("Keepalive out of range")
                    except ValueError:
                        QMessageBox.warning(self, "Invalid Keepalive Timer", 
                                          f"'{keepalive}' is not a valid keepalive timer (must be 1-65535 seconds).")
                        # Revert to original value
                        original_keepalive = bgp_config.get("bgp_keepalive", "30")
                        keepalive_item.setText(original_keepalive)
                        return
                    
                    bgp_config["bgp_keepalive"] = keepalive
            
            elif column == 11:  # Hold-time timer changed (column 11)
                hold_time_item = self.bgp_table.item(row, 11)
                if hold_time_item:
                    hold_time = hold_time_item.text().strip()
                    
                    # Validate hold-time timer
                    try:
                        if hold_time:  # Only validate if not empty
                            timer_value = int(hold_time)
                            if timer_value < 3 or timer_value > 65535:  # Valid hold-time range
                                raise ValueError("Hold-time out of range")
                    except ValueError:
                        QMessageBox.warning(self, "Invalid Hold-time Timer", 
                                          f"'{hold_time}' is not a valid hold-time timer (must be 3-65535 seconds).")
                        # Revert to original value
                        original_hold_time = bgp_config.get("bgp_hold_time", "90")
                        hold_time_item.setText(original_hold_time)
                        return
                    
                    bgp_config["bgp_hold_time"] = hold_time
            
            # Save session
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()


    def prompt_add_ospf(self):
        """Add OSPF configuration to selected device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to add OSPF configuration.")
            return

        row = selected_items[0].row()
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
        
        dialog = AddOspfDialog(self, device_name)
        if dialog.exec_() != dialog.Accepted:
            return

        ospf_config = dialog.get_values()
        
        # Update the device with OSPF configuration
        self._update_device_protocol(row, "OSPF", ospf_config)

    def prompt_add_isis(self):
        """Add IS-IS configuration to selected device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to add IS-IS configuration.")
            return

        row = selected_items[0].row()
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
        
        # Find the device's interface from all_devices
        device_interface = None
        device_vlan = None
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_vlan = device.get("VLAN", "")
                    # Use VLAN interface (e.g., vlan21) instead of physical interface
                    if device_vlan:
                        device_interface = f"vlan{device_vlan}"
                    else:
                        device_interface = device.get("Interface", "")
                    break
            if device_interface:
                break
        
        # Create ISIS config with the VLAN interface
        isis_config = {"interface": device_interface} if device_interface else {}
        
        dialog = AddIsisDialog(self, device_name, edit_mode=False, isis_config=isis_config)
        if dialog.exec_() != dialog.Accepted:
            return

        isis_config = dialog.get_values()
        
        # Update the device with IS-IS configuration
        self._update_device_protocol(row, "IS-IS", isis_config)

    def _update_device_protocol(self, row, protocol, config):
        """Update device with protocol configuration."""
        # Store protocol configuration in device data for protocol-specific tabs
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
        
        # Find the device in all_devices and update its protocol configuration
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    # Store protocol configuration in device data
                    # Handle both list and dict formats for protocols
                    if "protocols" not in device:
                        device["protocols"] = []
                    
                    # If protocols is a list, add the protocol name to the list
                    if isinstance(device["protocols"], list):
                        if protocol not in device["protocols"]:
                            device["protocols"].append(protocol)
                        # Store the config in a separate field
                        device[f"{protocol.lower().replace('-', '_')}_config"] = config
                    else:
                        # If protocols is a dict (old format), store config there
                        device["protocols"][protocol] = config
                    
                    print(f"[DEBUG PROTOCOL] Added {protocol} config to device '{device_name}'")
                    break
        
        # Update the protocol-specific tables based on the protocol
        if protocol == "BGP":
            self.update_bgp_table()
        elif protocol == "OSPF":
            self.update_ospf_table()
        elif protocol == "IS-IS":
            self.update_isis_table()
        
        # Save session
        if hasattr(self.main_window, "save_session"):
            self.main_window.save_session()


    def _normalize_iface_label(self, text: str) -> str:
        """Convert UI labels like 'TG 0 - Port: enp55s0np0' to 'enp55s0np0'."""
        s = (text or "").strip().strip('"').rstrip(",")
        if not s:
            return ""
        if " - " in s:
            s = s.split(" - ", 1)[-1].strip()
        if ":" in s:
            s = s.rsplit(":", 1)[-1].strip()
        parts = s.split()
        return parts[-1] if parts else ""
    
    def _convert_protocols_to_array(self, protocols):
        """Convert protocols string to array format for database storage."""
        if not protocols:
            return []
        
        if isinstance(protocols, list):
            return protocols
        
        if isinstance(protocols, str):
            # Split by comma and clean up
            return [p.strip() for p in protocols.split(",") if p.strip()]
        
        return []
    
    def _get_server_url_from_interface(self, iface_label):
        """Get server URL from interface label."""
        # Extract TG ID from interface label like "TG 0 - Port: ● enp180s0np0"
        if "TG" in iface_label:
            tg_part = iface_label.split("-")[0].strip()  # "TG 0"
            tg_id = tg_part.split()[-1]  # "0"
            
            # Find server with matching TG ID, prioritizing online servers
            if hasattr(self.main_window, 'server_interfaces'):
                # First, try to find an online server with matching TG ID
                for server in self.main_window.server_interfaces:
                    if (str(server.get('tg_id', '0')) == tg_id and 
                        server.get('online', False)):
                        return server.get('address')
                
                # If no online server found, try any server with matching TG ID
                for server in self.main_window.server_interfaces:
                    if str(server.get('tg_id', '0')) == tg_id:
                        return server.get('address')
        
        # Fallback: return first online server, then any server
        if hasattr(self.main_window, 'server_interfaces') and self.main_window.server_interfaces:
            # Try online servers first
            for server in self.main_window.server_interfaces:
                if server.get('online', False):
                    return server.get('address')
            
            # If no online servers, return first available
            return self.main_window.server_interfaces[0].get('address')
        
        return None
    
    # Commented out old remove_selected_device method
    '''def remove_selected_device(self):
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Select one or more devices to remove.")
            return

        rows = sorted({it.row() for it in selected_items}, reverse=True)

        for row in rows:
            try:
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                iface_item = self.devices_table.item(row, self.COL["Interface"])
                prot_item = self.devices_table.item(row, self.COL["Protocols"])
                ipv4_item = self.devices_table.item(row, self.COL["IPv4"])
                ipv6_item = self.devices_table.item(row, self.COL["IPv6"])

                device_id = name_item.data(Qt.UserRole) if name_item else None
                name = name_item.text() if name_item else "Unknown"
                iface = iface_item.text() if iface_item else ""
                protocol_str = prot_item.text() if prot_item else ""
                protocols = [p.strip() for p in protocol_str.split(",") if p.strip()]
                ipv4 = ipv4_item.text() if ipv4_item else ""
                ipv6 = ipv6_item.text() if ipv6_item else ""
                ipv4_mask = (ipv4_item.data(Qt.UserRole + 1) if ipv4_item else None) or "24"
                ipv6_mask = (ipv6_item.data(Qt.UserRole + 1) if ipv6_item else None) or "64"

                logging.debug(f"Remove device: {name} iface={iface} id={device_id}")

                server_url = self.get_server_url(silent=True)
                if server_url and device_id:
                    try:
                        payload = {
                            "device_id": device_id,
                            "device_name": name,
                            "interface": iface,
                            "protocols": protocols,
                            "ipv4": ipv4,
                            "ipv6": ipv6,
                            "ipv4_mask": ipv4_mask,
                            "ipv6_mask": ipv6_mask,
                        }
                        resp = requests.post(f"{server_url}/api/device/remove", json=payload, timeout=15)
                        logging.info(f"[REMOVE] {device_id} -> {resp.status_code}")
                    except Exception as e:
                        logging.error(f"[REMOVE ERROR] backend: {e}")

                # remove GUI row
                self.devices_table.removeRow(row)

                # remove from model
                if hasattr(self.main_window, "all_devices"):
                    iface_devices = self.main_window.all_devices.get(iface, [])
                    self.main_window.all_devices[iface] = [
                        d for d in iface_devices if d.get("device_id") != device_id
                    ]
                    if not self.main_window.all_devices[iface]:
                        del self.main_window.all_devices[iface]
            except Exception as e:
                logging.error(f"[REMOVE ERROR] row {row}: {e}")'''

    def start_selected_devices(self):
        """Start selected devices by applying their configuration (non-blocking)."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to start.")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to start.")
            return

        # Get server URL
        server_url = self.get_server_url()
        if not server_url:
            return

        # Prepare device data for worker thread
        devices_to_process = []
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if device_info:
                devices_to_process.append((row, device_name, device_info))
        
        if not devices_to_process:
            QMessageBox.warning(self, "Error", "No valid devices found to start.")
            return
        
        # Create and start worker thread
        self.operation_worker = DeviceOperationWorker('start', devices_to_process, server_url, self)
        
        # Set operation type flag for this operation
        self._current_operation_type = 'start'
        
        # Connect signals
        self.operation_worker.progress.connect(self._on_device_operation_progress)
        self.operation_worker.device_status_updated.connect(self._on_device_status_updated)
        self.operation_worker.finished.connect(lambda results, succ, fail: self._on_device_operation_finished(results, succ, fail, selected_rows))
        
        # Start the worker (non-blocking)
        self.operation_worker.start()
        
        print(f"[DEVICE START] Starting {len(devices_to_process)} devices in background...")
    
    def stop_selected_devices(self):
        """Stop selected devices by stopping their services without removing them (non-blocking)."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to stop.")
            return

        # Get unique rows from selected items
        selected_rows = set()
        for item in selected_items:
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to stop.")
            return

        # Get server URL
        server_url = self.get_server_url()
        if not server_url:
            return

        # Prepare device data for worker thread
        devices_to_process = []
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device in all_devices data structure
            device_info = None
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        device_info = device
                        break
                if device_info:
                    break
            
            if device_info:
                devices_to_process.append((row, device_name, device_info))
        
        if not devices_to_process:
            QMessageBox.warning(self, "Error", "No valid devices found to stop.")
            return
        
        # Create and start worker thread
        self.operation_worker = DeviceOperationWorker('stop', devices_to_process, server_url, self)
        
        # Set operation type flag for this operation
        self._current_operation_type = 'stop'
        
        # Connect signals
        self.operation_worker.progress.connect(self._on_device_operation_progress)
        self.operation_worker.device_status_updated.connect(self._on_device_status_updated)
        self.operation_worker.finished.connect(lambda results, succ, fail: self._on_device_operation_finished(results, succ, fail, selected_rows))
        
        # Start the worker (non-blocking)
        self.operation_worker.start()
        
        print(f"[DEVICE STOP] Stopping {len(devices_to_process)} devices in background...")

    def remove_selected_device(self):
        """Remove selected devices from both server and UI."""
        print(f"[DEBUG REMOVE] Starting remove_selected_device() - FULL REMOVAL (SERVER + UI)")
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Select one or more devices to remove.")
            return

        # Confirm removal
        reply = QMessageBox.question(
            self, 
            "Confirm Device Removal", 
            f"Are you sure you want to remove {len(set(item.row() for item in selected_items))} selected device(s)?\n\nThis will:\n- Stop all protocols (BGP, OSPF, etc.)\n- Remove FRR containers\n- Clean up interface IP addresses\n- Remove devices from the UI",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return

        rows = sorted({it.row() for it in selected_items}, reverse=True)
        print(f"[DEBUG REMOVE] Selected rows: {rows}")

        for row in rows:
            try:
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                ipv4_item = self.devices_table.item(row, self.COL["IPv4"])
                ipv6_item = self.devices_table.item(row, self.COL["IPv6"])

                device_id = name_item.data(Qt.UserRole) if name_item else None
                name = name_item.text() if name_item else "Unknown"
                ipv4 = ipv4_item.text() if ipv4_item else ""
                ipv6 = ipv6_item.text() if ipv6_item else ""
                
                print(f"[DEBUG REMOVE] Processing device: name='{name}', id='{device_id}', ipv4='{ipv4}', ipv6='{ipv6}'")

                # Find the device in all_devices to get interface and protocol info
                device_info = None
                device_interface = None
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("device_id") == device_id or device.get("Device Name") == name:
                            device_info = device
                            device_interface = iface
                            break
                    if device_info:
                        break

                if not device_info:
                    print(f"[ERROR] Device {name} not found in all_devices")
                    continue

                # Get interface info
                iface_label = device_info.get("Interface", device_interface)

                # Clean up BGP table entries for this device (before removing from all_devices)
                self._cleanup_bgp_table_for_device(device_id, name)
                
                # Clean up OSPF table entries for this device (before removing from all_devices)
                self._cleanup_ospf_table_for_device(device_id, name)
                
                # Clean up ISIS table entries for this device (before removing from all_devices)
                self._cleanup_isis_table_for_device(device_id, name)

                # Remove from GUI table
                print(f"[DEBUG REMOVE] Removing device '{name}' from UI")
                self.devices_table.removeRow(row)
                print(f"[DEBUG REMOVE] Removed GUI row {row}")

                # Remove from all_devices data structure immediately
                if device_interface in self.main_window.all_devices:
                    device_list = self.main_window.all_devices[device_interface]
                    if isinstance(device_list, list):
                        # Remove the device from the list
                        self.main_window.all_devices[device_interface] = [
                            d for d in device_list 
                            if d.get("device_id") != device_id and d.get("Device Name") != name
                        ]
                        print(f"[DEBUG REMOVE] Removed '{name}' from all_devices data structure")
                        
                        # If no devices left on this interface, remove the interface key
                        if not self.main_window.all_devices[device_interface]:
                            del self.main_window.all_devices[device_interface]
                            print(f"[DEBUG REMOVE] Removed empty interface '{device_interface}' from all_devices")

                # Remove from device name mapping
                if hasattr(self, 'interface_to_device_map'):
                    if name in self.interface_to_device_map:
                        del self.interface_to_device_map[name]
                        print(f"[DEBUG REMOVE] Removed '{name}' from device mapping")

                # Track removed device for session synchronization
                if not hasattr(self.main_window, 'removed_devices'):
                    self.main_window.removed_devices = []
                self.main_window.removed_devices.append(device_id)
                print(f"[DEBUG REMOVE] Added device ID '{device_id}' to removed_devices list")

                # Remove from server immediately
                server_url = self.get_server_url()
                if server_url:
                    self._remove_device_from_server(device_info, device_id, name)
                    
                    # Clean up protocol configurations from server
                    protocols = device_info.get("protocols", [])
                    if not isinstance(protocols, list):
                        protocols = list(protocols.keys()) if isinstance(protocols, dict) else []
                    
                    # Clean up BGP configuration from server
                    if "BGP" in protocols:
                        try:
                            response = requests.post(f"{server_url}/api/device/bgp/cleanup", 
                                                   json={"device_id": device_id}, 
                                                   timeout=10)
                            if response.status_code == 200:
                                print(f"✅ BGP configuration removed from server for {name}")
                            else:
                                print(f"⚠️ Server BGP cleanup failed for {name}: {response.status_code}")
                        except Exception as bgp_e:
                            print(f"⚠️ Error removing BGP from server for {name}: {str(bgp_e)}")
                    
                    # Clean up OSPF configuration from server
                    if "OSPF" in protocols:
                        try:
                            response = requests.post(f"{server_url}/api/ospf/cleanup", 
                                                   json={"device_id": device_id}, 
                                                   timeout=10)
                            if response.status_code == 200:
                                print(f"✅ OSPF configuration removed from server for {name}")
                            else:
                                print(f"⚠️ Server OSPF cleanup failed for {name}: {response.status_code}")
                        except Exception as ospf_e:
                            print(f"⚠️ Error removing OSPF from server for {name}: {str(ospf_e)}")
                    
                    # Clean up ISIS configuration from server
                    if "IS-IS" in protocols or "ISIS" in protocols:
                        try:
                            response = requests.post(f"{server_url}/api/device/isis/cleanup", 
                                                   json={"device_id": device_id}, 
                                                   timeout=10)
                            if response.status_code == 200:
                                print(f"✅ ISIS configuration removed from server for {name}")
                            else:
                                print(f"⚠️ Server ISIS cleanup failed for {name}: {response.status_code}")
                        except Exception as isis_e:
                            print(f"⚠️ Error removing ISIS from server for {name}: {str(isis_e)}")

            except Exception as e:
                logging.error(f"[REMOVE ERROR] row {row}: {e}")
        
        # Update protocol tables after removal
        self.update_bgp_table()
        self.update_ospf_table()
        self.update_isis_table()
        
        # Show info message about removal
        QMessageBox.information(self, "Device Removed", 
                               f"Removed {len(rows)} device(s) from the UI and server.")
        
        # Auto-save session after device removal
        if hasattr(self.main_window, 'save_session'):
            print(f"[DEBUG REMOVE] Auto-saving session after device removal")
            self.main_window.save_session()

    def _remove_device_from_server(self, device_info, device_id, device_name):
        """Remove a device from the server immediately."""
        try:
            print(f"[DEBUG REMOVE SERVER] Removing device '{device_name}' from server")
            
            # Get server URL
            server_url = self.get_server_url(silent=True)
            if not server_url:
                print(f"[DEBUG REMOVE SERVER] No server URL available")
                return
            
            # Get device information
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            vlan = device_info.get("VLAN", "0")
            ipv4 = device_info.get("IPv4", "")
            ipv6 = device_info.get("IPv6", "")
            
            print(f"[DEBUG REMOVE SERVER] Device info: iface='{iface_norm}', vlan='{vlan}', ipv4='{ipv4}', ipv6='{ipv6}'")
            
            # Clean up device-specific IPs from server
            cleanup_payload = {
                "interface": iface_norm,
                "vlan": vlan,
                "cleanup_only": True,
                "device_specific": True,
                "device_id": device_id,
                "device_name": device_name
            }
            
            print(f"[DEBUG REMOVE SERVER] Calling cleanup API with payload: {cleanup_payload}")
            cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
            
            if cleanup_resp.status_code == 200:
                cleanup_data = cleanup_resp.json()
                removed_ips = cleanup_data.get("removed_ips", [])
                print(f"[DEBUG REMOVE SERVER] Successfully cleaned up IPs: {removed_ips}")
            else:
                print(f"[DEBUG REMOVE SERVER] Cleanup failed: {cleanup_resp.status_code} - {cleanup_resp.text}")
            
            # Also call the device remove API for protocol cleanup
            protocols = device_info.get("protocols", [])
            if isinstance(protocols, list):
                protocol_list = protocols
            elif isinstance(protocols, dict):
                protocol_list = list(protocols.keys())
            else:
                protocol_list = []
            
            remove_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": iface_norm,
                "vlan": vlan,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "protocols": protocol_list
            }
            
            print(f"[DEBUG REMOVE SERVER] Calling remove API with payload: {remove_payload}")
            remove_resp = requests.post(f"{server_url}/api/device/remove", json=remove_payload, timeout=10)
            
            if remove_resp.status_code == 200:
                print(f"[DEBUG REMOVE SERVER] Successfully removed device '{device_name}' from server")
            else:
                print(f"[DEBUG REMOVE SERVER] Remove API failed: {remove_resp.status_code} - {remove_resp.text}")
                
        except Exception as e:
            print(f"[ERROR] Failed to remove device '{device_name}' from server: {e}")

    def _cleanup_bgp_table_for_device(self, device_id, device_name):
        """Clean up BGP table entries for a removed device."""
        try:
            print(f"[DEBUG BGP CLEANUP] Cleaning up BGP entries for device '{device_name}' (ID: {device_id})")
            
            # Remove BGP table rows that match this device
            rows_to_remove = []
            for row in range(self.bgp_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.bgp_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    print(f"[DEBUG BGP CLEANUP] Found BGP row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.bgp_table.removeRow(row)
                print(f"[DEBUG BGP CLEANUP] Removed BGP table row {row}")
            
            # Also clean up BGP protocol data from device protocols
            # Remove BGP protocol from the device in all_devices
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove BGP from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list) and "BGP" in device["protocols"]:
                                device["protocols"].remove("BGP")
                                print(f"[DEBUG BGP CLEANUP] Removed BGP protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG BGP CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict) and "BGP" in device["protocols"]:
                                # Handle old format for backward compatibility
                                del device["protocols"]["BGP"]
                                print(f"[DEBUG BGP CLEANUP] Removed BGP protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG BGP CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove bgp_config if it exists
                        if "bgp_config" in device:
                            del device["bgp_config"]
                            print(f"[DEBUG BGP CLEANUP] Removed bgp_config from device '{device_name}'")
                        break
            
            print(f"[DEBUG BGP CLEANUP] Removed {len(rows_to_remove)} BGP entries for device '{device_name}'")
            
        except Exception as e:
            print(f"[ERROR] Failed to cleanup BGP entries for device '{device_name}': {e}")

    def _cleanup_ospf_table_for_device(self, device_id, device_name):
        """Clean up OSPF table entries for a removed device."""
        try:
            print(f"[DEBUG OSPF CLEANUP] Cleaning up OSPF entries for device '{device_name}' (ID: {device_id})")
            
            # Remove OSPF table rows that match this device
            rows_to_remove = []
            for row in range(self.ospf_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.ospf_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    print(f"[DEBUG OSPF CLEANUP] Found OSPF row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.ospf_table.removeRow(row)
                print(f"[DEBUG OSPF CLEANUP] Removed OSPF table row {row}")
            
            # Also clean up OSPF protocol data from device protocols
            # Remove OSPF protocol from the device in all_devices
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove OSPF from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list) and "OSPF" in device["protocols"]:
                                device["protocols"].remove("OSPF")
                                print(f"[DEBUG OSPF CLEANUP] Removed OSPF protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG OSPF CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict) and "OSPF" in device["protocols"]:
                                # Handle old format for backward compatibility
                                del device["protocols"]["OSPF"]
                                print(f"[DEBUG OSPF CLEANUP] Removed OSPF protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG OSPF CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove ospf_config if it exists
                        if "ospf_config" in device:
                            del device["ospf_config"]
                            print(f"[DEBUG OSPF CLEANUP] Removed ospf_config from device '{device_name}'")
                        break
            
            print(f"[DEBUG OSPF CLEANUP] Removed {len(rows_to_remove)} OSPF entries for device '{device_name}'")
            
        except Exception as e:
            print(f"[ERROR] Failed to cleanup OSPF entries for device '{device_name}': {e}")

    def _cleanup_isis_table_for_device(self, device_id, device_name):
        """Clean up ISIS table entries for a removed device."""
        try:
            print(f"[DEBUG ISIS CLEANUP] Cleaning up ISIS entries for device '{device_name}' (ID: {device_id})")
            
            # Remove ISIS table rows that match this device
            rows_to_remove = []
            for row in range(self.isis_table.rowCount()):
                # Check if this row belongs to the removed device
                device_item = self.isis_table.item(row, 0)  # Assuming first column is device name
                if device_item and device_item.text() == device_name:
                    rows_to_remove.append(row)
                    print(f"[DEBUG ISIS CLEANUP] Found ISIS row {row} for device '{device_name}'")
            
            # Remove rows in reverse order to maintain indices
            for row in sorted(rows_to_remove, reverse=True):
                self.isis_table.removeRow(row)
                print(f"[DEBUG ISIS CLEANUP] Removed ISIS table row {row}")
            
            # Also clean up ISIS protocol data from device protocols
            # Remove ISIS protocol from the device in all_devices
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if (device.get("device_id") == device_id or 
                        device.get("Device Name") == device_name):
                        # Remove IS-IS from protocols if it exists (handle both old and new formats)
                        if "protocols" in device:
                            if isinstance(device["protocols"], list):
                                if "IS-IS" in device["protocols"]:
                                    device["protocols"].remove("IS-IS")
                                    print(f"[DEBUG ISIS CLEANUP] Removed IS-IS protocol from device '{device_name}'")
                                elif "ISIS" in device["protocols"]:
                                    device["protocols"].remove("ISIS")
                                    print(f"[DEBUG ISIS CLEANUP] Removed ISIS protocol from device '{device_name}'")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG ISIS CLEANUP] Removed empty protocols from device '{device_name}'")
                            elif isinstance(device["protocols"], dict):
                                # Handle old format for backward compatibility
                                if "IS-IS" in device["protocols"]:
                                    del device["protocols"]["IS-IS"]
                                    print(f"[DEBUG ISIS CLEANUP] Removed IS-IS protocol from device '{device_name}' (old format)")
                                elif "ISIS" in device["protocols"]:
                                    del device["protocols"]["ISIS"]
                                    print(f"[DEBUG ISIS CLEANUP] Removed ISIS protocol from device '{device_name}' (old format)")
                                
                                # If no protocols left, remove the protocols key entirely
                                if not device["protocols"]:
                                    del device["protocols"]
                                    print(f"[DEBUG ISIS CLEANUP] Removed empty protocols from device '{device_name}'")
                        
                        # Also remove isis_config and is_is_config if they exist
                        if "isis_config" in device:
                            del device["isis_config"]
                            print(f"[DEBUG ISIS CLEANUP] Removed isis_config from device '{device_name}'")
                        if "is_is_config" in device:
                            del device["is_is_config"]
                            print(f"[DEBUG ISIS CLEANUP] Removed is_is_config from device '{device_name}'")
                        break
            
            print(f"[DEBUG ISIS CLEANUP] Removed {len(rows_to_remove)} ISIS entries for device '{device_name}'")
            
        except Exception as e:
            print(f"[ERROR] Failed to cleanup ISIS entries for device '{device_name}': {e}")

    # ---------- Table refresh ----------

    def update_device_table(self, all_devices):
        """Rebuild table for currently selected interfaces."""
        self.devices_table.setRowCount(0)

        try:
            # figure selected interfaces from server_tree
            selected = set()
            tree = self.main_window.server_tree
            for item in tree.selectedItems():
                parent = item.parent()
                if parent:
                    tg_id = parent.text(0).strip()
                    port_name = item.text(0).replace("• ", "").strip()  # Remove bullet prefix
                    selected.add(f"{tg_id} - {port_name}")  # Match server tree format

            # fill rows - if no interfaces selected, show devices from all interfaces
            interfaces_to_show = selected if selected else list(all_devices.keys())
            for iface in interfaces_to_show:
                # Check both new format and old format for backward compatibility
                devices = all_devices.get(iface, [])
                if not devices:
                    # Try old format with "Port:" and bullet
                    old_format = iface.replace(" - ", " - Port: • ")
                    devices = all_devices.get(old_format, [])
                
                for device in devices:
                    row = self.devices_table.rowCount()
                    self.devices_table.insertRow(row)
                    for h in self.device_headers:
                        # Handle special cases for mask columns
                        if h == "IPv4 Mask":
                            val = device.get("ipv4_mask", "24")
                        elif h == "IPv6 Mask":
                            val = device.get("ipv6_mask", "64")
                        elif h == "Loopback IPv4":
                            val = device.get("Loopback IPv4", "")
                        elif h == "Loopback IPv6":
                            val = device.get("Loopback IPv6", "")
                        else:
                            val = device.get(h, "")
                        
                        # Special handling for Status column - use icon instead of text
                        if h == "Status":
                            status_value = device.get("Status", "Stopped")
                            item = QTableWidgetItem("")  # Empty text, icon only
                            if status_value == "Running":
                                item.setIcon(self.green_dot)
                                item.setToolTip("Device Running")
                            else:
                                item.setIcon(self.red_dot)
                                item.setToolTip("Device Stopped")
                            item.setFlags(Qt.ItemIsEnabled)  # Read-only
                        else:
                            item = QTableWidgetItem(str(val))
                        
                        # keep masks on the cells too
                        if h == "IPv4":
                            item.setData(Qt.UserRole + 1, device.get("ipv4_mask", "24"))
                        elif h == "IPv6":
                            item.setData(Qt.UserRole + 1, device.get("ipv6_mask", "64"))
                        # store id on name column
                        if h == "Device Name" and "device_id" in device:
                            item.setData(Qt.UserRole, device["device_id"])
                        
                        # Store initial value for change detection
                        item.setData(Qt.UserRole + 2, str(val))
                        
                        self.devices_table.setItem(row, self.COL[h], item)

                    # Set initial status icon (will be updated by async ARP check if needed)
                    status_value = device.get("Status", "Stopped")
                    if status_value == "Running":
                        # Set initial status icon - will be updated by async ARP check
                        self.set_status_icon(row, resolved=False, status_text="Checking ARP...")

        except Exception as e:
            logging.error(f"[update_device_table] {e}")

        # Start async ARP resolution checks for running devices
        # DISABLED to prevent QThread crashes - use manual refresh instead
        # self._start_async_arp_checks()
        
        # Initialize ARP status from database for all running devices
        self._initialize_arp_status_from_database()

    def _initialize_arp_status_from_database(self):
        """Initialize ARP status from database for all running devices when client starts up."""
        try:
            # Get all running devices
            running_devices = []
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Status") == "Running":
                        running_devices.append(device)
            
            if not running_devices:
                return
            
            print(f"[ARP INIT] Initializing ARP status for {len(running_devices)} running devices...")
            
            # Check ARP status for each running device
            for device_info in running_devices:
                try:
                    device_name = device_info.get("Device Name", "Unknown")
                    device_id = device_info.get("device_id", "")
                    
                    if not device_id:
                        continue
                    
                    # Get ARP status from database
                    arp_results = self._check_individual_arp_resolution(device_info)
                    
                    # Find the row for this device in the table
                    device_row = None
                    for row in range(self.devices_table.rowCount()):
                        name_item = self.devices_table.item(row, self.COL["Device Name"])
                        if name_item and name_item.text() == device_name:
                            device_row = row
                            break
                    
                    if device_row is not None:
                        # Update the device status icon based on database results
                        overall_resolved = arp_results.get("overall_resolved", False)
                        overall_status = arp_results.get("overall_status", "Unknown")
                        
                        self.update_device_status_icon(device_row, overall_resolved, overall_status)
                        
                        print(f"[ARP INIT] {device_name}: {overall_status}")
                        
                except Exception as e:
                    print(f"[ARP INIT] Error for {device_name}: {e}")
            
        except Exception as e:
            print(f"[ARP INIT] Error initializing ARP status: {e}")

    def get_device_info_by_name(self, device_name):
        """Get device information by device name from all_devices data structure."""
        try:
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Device Name") == device_name:
                        return device
            return None
        except Exception as e:
            logging.error(f"[get_device_info_by_name] Error getting device info for '{device_name}': {e}")
            return None

    def _start_individual_arp_checks(self):
        """Start individual ARP checks for all running devices."""
        try:
            # Check if application is closing
            if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
                print("[ARP INDIVIDUAL] Skipping ARP check - application is closing")
                return
            
            # Check if ARP check is already in progress
            if self._arp_check_in_progress:
                print("[ARP INDIVIDUAL] ARP check already in progress, skipping")
                return
            
            # Get all running devices
            devices_to_check = []
            for row in range(self.devices_table.rowCount()):
                device_name_item = self.devices_table.item(row, self.COL["Device Name"])
                if device_name_item:
                    device_name = device_name_item.text()
                    device_info = self.get_device_info_by_name(device_name)
                    
                    if device_info and device_info.get("Status") == "Running":
                        devices_to_check.append((row, device_info))
            
            # Start individual ARP checking if we have devices to check
            if devices_to_check:
                self._arp_check_in_progress = True
                print(f"[ARP INDIVIDUAL] Starting individual ARP checks for {len(devices_to_check)} running devices")
                self.check_individual_arp_resolution_bulk_async(devices_to_check)
            else:
                print("[ARP INDIVIDUAL] No running devices to check")
                
        except Exception as e:
            logging.error(f"[_start_individual_arp_checks] {e}")
            self._arp_check_in_progress = False
    
    def check_individual_arp_resolution_bulk_async(self, devices_data):
        """Start individual ARP resolution checks for multiple devices in background."""
        # devices_data should be a list of (row, device_info) tuples
        
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            print("[ARP INDIVIDUAL BULK] Skipping ARP check - application is closing")
            return
        
        # Create and start worker
        self.individual_arp_worker = IndividualArpCheckWorker(devices_data, self)
        self.individual_arp_worker.arp_result.connect(self._on_individual_arp_result)
        self.individual_arp_worker.finished.connect(self._on_individual_arp_finished)
        self.individual_arp_worker.start()
    
    def _on_individual_arp_result(self, row, arp_results, operation_id=None):
        """Handle individual ARP check result from worker thread."""
        try:
            # Debug: Check if this device was actually selected
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            print(f"[DEBUG ARP RESULT] Processing ARP result for row {row}, device: {device_name}, operation_id: {operation_id}")
            
            # Check if this device is in the current pending ARP rows
            if hasattr(self, '_pending_arp_rows') and self._pending_arp_rows:
                if row not in self._pending_arp_rows:
                    print(f"[DEBUG ARP RESULT] Skipping row {row} ({device_name}) - not in current selection: {self._pending_arp_rows}")
                    return
                else:
                    print(f"[DEBUG ARP RESULT] Processing row {row} ({device_name}) - in current selection")
            else:
                print(f"[DEBUG ARP RESULT] No pending ARP rows, processing row {row} ({device_name})")
            
            # Additional validation: Check if this is from the current ARP operation
            if hasattr(self, 'arp_operation_worker') and self.arp_operation_worker:
                current_operation_id = getattr(self.arp_operation_worker, 'operation_id', None)
                if operation_id and current_operation_id and operation_id != current_operation_id:
                    print(f"[DEBUG ARP RESULT] Skipping row {row} ({device_name}) - operation_id mismatch: {operation_id} != {current_operation_id}")
                    return
            
            # Update the status icon and individual IP colors
            self.set_status_icon_with_individual_ips(row, arp_results)
        except Exception as e:
            logging.error(f"[INDIVIDUAL ARP RESULT ERROR] Row {row}: {e}")
    
    def _on_individual_arp_finished(self):
        """Handle individual ARP check completion."""
        # Clean up worker reference
        if hasattr(self, 'individual_arp_worker'):
            self.individual_arp_worker.deleteLater()
            delattr(self, 'individual_arp_worker')
        
        # Reset the in-progress flag
        self._arp_check_in_progress = False
        print("[ARP INDIVIDUAL] Individual ARP checks completed")

    def _start_async_arp_checks(self):
        """Start async ARP resolution checks for all running devices."""
        try:
            # Check if application is closing
            if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
                print("[ARP ASYNC] Skipping ARP check - application is closing")
                return
            
            # Check if ARP check is already in progress
            if self._arp_check_in_progress:
                print("[ARP ASYNC] Skipping ARP check - already in progress")
                return
            
            devices_to_check = []
            
            # Collect all running devices that need ARP checks
            for row in range(self.devices_table.rowCount()):
                device_name_item = self.devices_table.item(row, self.COL["Device Name"])
                if not device_name_item:
                    continue
                
                device_name = device_name_item.text()
                device_info = self.get_device_info_by_name(device_name)
                
                if device_info and device_info.get("Status") == "Running":
                    devices_to_check.append((row, device_info))
            
            # Start async ARP checking if we have devices to check
            if devices_to_check:
                self._arp_check_in_progress = True
                print(f"[ARP ASYNC] Starting async ARP checks for {len(devices_to_check)} running devices")
                self.check_arp_resolution_bulk_async(devices_to_check)
            else:
                print("[ARP ASYNC] No running devices to check")
                
        except Exception as e:
            logging.error(f"[_start_async_arp_checks] {e}")
            self._arp_check_in_progress = False

    # ---------- Poller ----------

    def poll_device_status(self):
        """Poll ARP resolution status for all devices in the table using async approach."""
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return

            # Count running devices for adaptive polling
            running_count = 0
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("Status") == "Running":
                        running_count += 1
            
            # Adaptive polling: slow down when no devices are running
            if running_count == 0:
                # No running devices - check every 60 seconds
                if self.status_timer.interval() != 60000:
                    self.status_timer.setInterval(60000)
                    print("[DEVICE POLL] No running devices - slowing to 60s interval")
                return  # Skip the actual check
            else:
                # Devices running - normal interval (30 seconds)
                if self.status_timer.interval() != 30000:
                    self.status_timer.setInterval(30000)
                    print(f"[DEVICE POLL] {running_count} device(s) running - normal 30s interval")

            # Use the new async ARP checking approach
            # DISABLED: Automatic ARP polling interferes with manual ARP operations
            # print(f"[ARP ASYNC POLL] Starting async ARP check for {running_count} running device(s)...")
            # self._start_async_arp_checks()

        except Exception as e:
            logging.debug(f"[ARP POLL ERROR] {e}")

    def set_selected_interface(self, iface_name):
        self.selected_iface_name = iface_name

    def _increment_mac(self, mac, step, byte_index=0):
        """Increment MAC address by step in the specified byte.
        
        Args:
            mac: MAC address string (e.g., "00:11:22:33:44:55")
            step: Number to increment by
            byte_index: Which byte to increment (0=6th/last, 1=5th, ..., 5=1st)
        """
        try:
            mac_parts = mac.split(":")
            bytes_list = [int(b, 16) for b in mac_parts]
            incremented = bytes_list[:]
            
            # Map byte_index to array index (0=6th -> index 5, 1=5th -> index 4, etc.)
            target_byte = 5 - byte_index
            
            # Increment the specified byte
            incremented[target_byte] += step
            
            # Handle overflow from right to left
            for j in range(5, -1, -1):
                if incremented[j] > 255:
                    incremented[j] -= 256
                    if j > 0:
                        incremented[j - 1] += 1
            
            return ":".join(f"{b:02x}" for b in incremented)
        except Exception:
            return mac

    def _increment_ipv4(self, ipv4, step, octet_index=0):
        """Increment IPv4 address by step in the specified octet.
        
        Args:
            ipv4: IPv4 address string (e.g., "192.168.0.1")
            step: Number to increment by
            octet_index: Which octet to increment (0=4th/last, 1=3rd, 2=2nd, 3=1st)
        """
        try:
            octets = list(map(int, ipv4.split(".")))
            incremented = octets[:]
            
            # Map octet_index to array index (0=4th -> index 3, 1=3rd -> index 2, etc.)
            target_octet = 3 - octet_index
            
            # Increment the specified octet
            incremented[target_octet] += step
            
            # Handle overflow from right to left
            for j in range(3, -1, -1):
                if incremented[j] > 255:
                    incremented[j] -= 256
                    if j > 0:
                        incremented[j - 1] += 1
                        
            return ".".join(map(str, incremented))
        except Exception:
            return ipv4

    def _increment_ipv6(self, ipv6, step, hextet_index=0):
        """Increment IPv6 address by step in the specified hextet.
        
        Args:
            ipv6: IPv6 address string (e.g., "fe80::1" or "2001:db8::1")
            step: Number to increment by
            hextet_index: Which hextet to increment (0=8th/last, 1=7th, ..., 7=1st)
        """
        try:
            import ipaddress
            
            # Expand the IPv6 address to full form
            addr = ipaddress.IPv6Address(ipv6)
            exploded = addr.exploded  # e.g., "2001:0db8:0000:0000:0000:0000:0000:0001"
            
            # Split into hextets
            hextets = exploded.split(":")
            hextets_int = [int(h, 16) for h in hextets]
            
            # Map hextet_index to array index (0=8th/last -> index 7, 1=7th -> index 6, etc.)
            target_hextet = 7 - hextet_index
            
            # Increment the specified hextet
            hextets_int[target_hextet] += step
            
            # Handle overflow from right to left
            for j in range(7, -1, -1):
                if hextets_int[j] > 0xFFFF:
                    hextets_int[j] -= 0x10000
                    if j > 0:
                        hextets_int[j - 1] += 1
            
            # Convert back to IPv6 address string
            ipv6_str = ":".join(f"{h:04x}" for h in hextets_int)
            return str(ipaddress.IPv6Address(ipv6_str))
        except Exception as e:
            return ipv6

    def apply_bgp_configurations(self):
        """Apply BGP configurations to the server for selected BGP table rows."""
        server_url = self.get_server_url()
        if not server_url:
            QMessageBox.critical(self, "No Server", "No server selected.")
            return

        # Get selected rows from the BGP table
        selected_items = self.bgp_table.selectedItems()
        selected_devices = []
        
        if selected_items:
            # Get unique device names from selected BGP table rows
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.bgp_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find the devices in all_devices
            for device_name in selected_device_names:
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            selected_devices.append(device)
                            break

        # Handle both BGP application and removal
        devices_to_apply_bgp = []  # Devices that need BGP configuration applied
        devices_to_remove_bgp = []  # Devices that need BGP configuration removed
        
        if selected_items:
            # If BGP table rows are selected, process only those devices
            selected_device_names = set()
            for item in selected_items:
                row = item.row()
                device_name = self.bgp_table.item(row, 0).text()  # Device column
                selected_device_names.add(device_name)
            
            # Find devices and determine if they need BGP applied or removed
            for device_name in selected_device_names:
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            bgp_config = device.get("bgp_config", {})
                            if bgp_config:
                                if bgp_config.get("_marked_for_removal"):
                                    # Device is marked for BGP removal
                                    devices_to_remove_bgp.append(device)
                                else:
                                    # Device has normal BGP config - needs application
                                    devices_to_apply_bgp.append(device)
                            else:
                                # Device was in BGP table but no longer has BGP config - needs removal
                                devices_to_remove_bgp.append(device)
                            break
        else:
            # If no BGP table rows selected, process all devices
            # Find devices that need BGP applied or removed
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    bgp_config = device.get("bgp_config", {})
                    if bgp_config:
                        if bgp_config.get("_marked_for_removal"):
                            # Device is marked for BGP removal
                            devices_to_remove_bgp.append(device)
                        else:
                            # Device has normal BGP config - needs application
                            devices_to_apply_bgp.append(device)
            
            # Check if we have any work to do
            if not devices_to_apply_bgp and not devices_to_remove_bgp:
                # Check if there are any devices at all
                total_devices = sum(len(devices) for devices in self.main_window.all_devices.values())
                if total_devices == 0:
                    QMessageBox.information(self, "No Devices", 
                                          "No devices found to apply BGP configuration to.")
                    return
                else:
                    # There are devices but none have BGP config
                    QMessageBox.information(self, "No BGP Configuration", 
                                          "No devices have BGP configuration to apply or remove.")
                    return

        # Check if we have any work to do
        if not devices_to_apply_bgp and not devices_to_remove_bgp:
            QMessageBox.information(self, "No BGP Changes", 
                                  "No BGP configurations to apply or remove.")
            return

        # Apply BGP configurations
        success_count = 0
        failed_devices = []
        
        # Handle BGP application
        for device_info in devices_to_apply_bgp:
            device_name = device_info.get("Device Name", "Unknown")
            device_id = device_info.get("device_id")
            
            if not device_id:
                failed_devices.append(f"{device_name}: Missing device ID")
                continue
                
            try:
                # Prepare BGP configuration payload
                bgp_config = device_info.get("bgp_config", {})
                payload = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": device_info.get("Interface", ""),
                    "vlan": device_info.get("VLAN", "0"),
                    "ipv4": device_info.get("IPv4", ""),
                    "ipv6": device_info.get("IPv6", ""),
                    "gateway": device_info.get("Gateway", ""),  # Include gateway for static route
                    "bgp_config": bgp_config,
                    "all_route_pools": getattr(self.main_window, 'bgp_route_pools', [])  # Include all route pools for generation
                }
                
                # Send BGP configuration to server
                response = requests.post(f"{server_url}/api/device/bgp/configure", 
                                       json=payload, timeout=10)
                
                if response.status_code == 200:
                    success_count += 1
                    print(f"✅ BGP configuration applied for {device_name}")
                    
                    # After successful BGP configuration, start the BGP service
                    try:
                        start_payload = {
                            "device_id": device_id,
                            "device_name": device_name,
                            "interface": device_info.get("Interface", ""),
                            "mac": device_info.get("MAC Address", ""),
                            "vlan": device_info.get("VLAN", "0"),
                            "ipv4": device_info.get("IPv4", ""),
                            "ipv6": device_info.get("IPv6", ""),
                            "protocols": ["BGP"],
                            "ipv4_mask": device_info.get("ipv4_mask", "24"),
                            "ipv6_mask": device_info.get("ipv6_mask", "64"),
                            "bgp_config": bgp_config
                        }
                        
                        start_response = requests.post(f"{server_url}/api/device/start", 
                                                    json=start_payload, timeout=10)
                        
                        if start_response.status_code == 200:
                            print(f"✅ BGP service started for {device_name}")
                            
                            # Send immediate ARP request after BGP service is started
                            # DISABLED to prevent QThread crashes - ARP will be manual only
                            # try:
                            #     self.send_immediate_arp_request(device_info, server_url)
                            # except Exception as arp_error:
                            #     print(f"[BGP ARP] ARP request failed for '{device_name}': {arp_error}")
                            #     # Don't fail BGP start if ARP request fails
                            
                            # Note: BGP monitoring will be started when user clicks "Start BGP" button
                        else:
                            print(f"⚠️ BGP configured but failed to start service for {device_name}")
                            
                    except Exception as start_error:
                        print(f"⚠️ BGP configured but failed to start service for {device_name}: {start_error}")
                        
                else:
                    error_msg = response.json().get("error", "Unknown error")
                    failed_devices.append(f"{device_name}: {error_msg}")
                    print(f"❌ Failed to apply BGP for {device_name}: {error_msg}")
                    
            except requests.exceptions.RequestException as e:
                failed_devices.append(f"{device_name}: Network error - {str(e)}")
                print(f"❌ Network error applying BGP for {device_name}: {str(e)}")
            except Exception as e:
                failed_devices.append(f"{device_name}: {str(e)}")
                print(f"❌ Error applying BGP for {device_name}: {str(e)}")

        # Handle BGP removal
        removal_success_count = 0
        removal_failed_devices = []
        
        for device_info in devices_to_remove_bgp:
            device_name = device_info.get("Device Name", "Unknown")
            device_id = device_info.get("device_id")
            
            if not device_id:
                removal_failed_devices.append(f"{device_name}: Missing device ID")
                continue
                
            try:
                # Call BGP cleanup endpoint to remove BGP configuration
                response = requests.post(f"{server_url}/api/bgp/cleanup", 
                                       json={"device_id": device_id}, 
                                       timeout=10)
                
                if response.status_code == 200:
                    removal_success_count += 1
                    print(f"✅ BGP configuration removed for {device_name}")
                    
                    # Remove BGP configuration from client data after successful server removal
                    if "protocols" in device_info:
                        if isinstance(device_info["protocols"], dict):
                            if device_info["protocols"].get("BGP", {}).get("_marked_for_removal"):
                                del device_info["protocols"]["BGP"]
                        else:
                            if device_info.get("bgp_config", {}).get("_marked_for_removal"):
                                del device_info["bgp_config"]
                            # If no other protocols, remove the protocols key entirely
                            if not device_info["protocols"]:
                                del device_info["protocols"]
                else:
                    error_msg = response.json().get("error", "Unknown error")
                    removal_failed_devices.append(f"{device_name}: {error_msg}")
                    print(f"❌ Failed to remove BGP for {device_name}: {error_msg}")
                    
            except requests.exceptions.RequestException as e:
                removal_failed_devices.append(f"{device_name}: Network error - {str(e)}")
                print(f"❌ Network error removing BGP for {device_name}: {str(e)}")
            except Exception as e:
                removal_failed_devices.append(f"{device_name}: {str(e)}")
                print(f"❌ Error removing BGP for {device_name}: {str(e)}")

        # Show results - combine application and removal results
        total_success = success_count + removal_success_count
        total_failed = len(failed_devices) + len(removal_failed_devices)
        total_operations = len(devices_to_apply_bgp) + len(devices_to_remove_bgp)
        
        if total_operations == 0:
            QMessageBox.information(self, "No BGP Operations", "No BGP operations to perform.")
            return
        
        # Build result messages
        all_results = []
        
        # Add BGP application results
        if devices_to_apply_bgp:
            for device_info in devices_to_apply_bgp:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in failed_devices]:
                    all_results.append(f"✅ Applied BGP to {device_name}")
        
        # Add BGP removal results  
        if devices_to_remove_bgp:
            for device_info in devices_to_remove_bgp:
                device_name = device_info.get("Device Name", "Unknown")
                if device_name not in [f.split(":")[0] for f in removal_failed_devices]:
                    all_results.append(f"✅ Removed BGP from {device_name}")
        
        # Add failed operations
        all_results.extend([f"❌ {failed}" for failed in failed_devices])
        all_results.extend([f"❌ {failed}" for failed in removal_failed_devices])
        
        # Show appropriate dialog
        if total_success == total_operations:
            # All successful
            if len(devices_to_apply_bgp) > 0 and len(devices_to_remove_bgp) > 0:
                title = "BGP Operations Completed"
                message = f"Successfully applied BGP to {success_count} device(s) and removed BGP from {removal_success_count} device(s)."
            elif len(devices_to_apply_bgp) > 0:
                title = "BGP Applied Successfully"
                message = f"BGP configuration applied successfully for {success_count} device(s)."
            else:
                title = "BGP Removed Successfully"
                message = f"BGP configuration removed successfully from {removal_success_count} device(s)."
            
            QMessageBox.information(self, title, message)
        elif total_success > 0:
            # Partial success - use scrollable dialog
            dialog = MultiDeviceResultsDialog(
                "BGP Operations Partially Completed", 
                f"Completed {total_success} of {total_operations} BGP operations.",
                all_results,
                self
            )
            dialog.exec_()
        else:
            # All failed - use scrollable dialog
            dialog = MultiDeviceResultsDialog(
                "BGP Operations Failed", 
                "Failed to complete any BGP operations.",
                all_results,
                self
            )
            dialog.exec_()

        # Update BGP table to reflect any changes
        self.update_bgp_table()

    def start_bgp_protocol(self):
        """Start BGP protocol for selected devices."""
        self._toggle_protocol_action("BGP", starting=True)

    def stop_bgp_protocol(self):
        """Stop BGP protocol for selected devices."""
        self._toggle_protocol_action("BGP", starting=False)

    def start_ospf_protocol(self):
        """Start OSPF protocol for selected devices."""
        self._toggle_protocol_action("OSPF", starting=True)

    def stop_ospf_protocol(self):
        """Stop OSPF protocol for selected devices."""
        self._toggle_protocol_action("OSPF", starting=False)

    def start_isis_protocol(self):
        """Start IS-IS protocol for selected devices."""
        self._toggle_protocol_action("IS-IS", starting=True)

    def stop_isis_protocol(self):
        """Stop IS-IS protocol for selected devices."""
        self._toggle_protocol_action("IS-IS", starting=False)

    def _toggle_protocol_action(self, protocol, starting=True):
        """Start or stop a specific protocol for devices that have it configured."""
        server_url = self.get_server_url()
        if not server_url:
            QMessageBox.critical(self, "No Server", "No server selected.")
            return

        # Check if there are selected rows in the protocol table
        selected_device_names = set()
        selected_bgp_neighbors = {}  # device_name -> set of neighbor_ips
        
        if protocol == "BGP":
            selected_items = self.bgp_table.selectedItems()
            if selected_items:
                # Get unique device names and neighbor IPs from selected rows
                for item in selected_items:
                    row = item.row()
                    device_name_item = self.bgp_table.item(row, 0)  # Device column
                    neighbor_ip_item = self.bgp_table.item(row, 3)  # Neighbor IP column
                    
                    if device_name_item and neighbor_ip_item:
                        device_name = device_name_item.text()
                        neighbor_ip = neighbor_ip_item.text()
                        
                        # Remove "(Pending Removal)" suffix if present
                        if " (Pending Removal)" in device_name:
                            device_name = device_name.replace(" (Pending Removal)", "")
                        
                        selected_device_names.add(device_name)
                        
                        # Track specific neighbors for each device
                        if device_name not in selected_bgp_neighbors:
                            selected_bgp_neighbors[device_name] = set()
                        selected_bgp_neighbors[device_name].add(neighbor_ip)
                
                print(f"[BGP TOGGLE] Selected devices from BGP table: {selected_device_names}")
                print(f"[BGP TOGGLE] Selected neighbors: {selected_bgp_neighbors}")
                print(f"[BGP TOGGLE] selected_bgp_neighbors type: {type(selected_bgp_neighbors)}")
                print(f"[BGP TOGGLE] selected_bgp_neighbors length: {len(selected_bgp_neighbors)}")
        elif protocol == "OSPF":
            selected_items = self.ospf_table.selectedItems()
            if selected_items:
                # Get unique device names from selected rows
                for item in selected_items:
                    row = item.row()
                    device_name_item = self.ospf_table.item(row, 0)  # Device column
                    if device_name_item:
                        device_name = device_name_item.text()
                        # Remove "(Pending Removal)" suffix if present
                        if " (Pending Removal)" in device_name:
                            device_name = device_name.replace(" (Pending Removal)", "")
                        selected_device_names.add(device_name)
                print(f"[OSPF TOGGLE] Selected devices from OSPF table: {selected_device_names}")

        # Find devices that have this protocol configured
        devices_with_protocol = []
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                # Check if device has this protocol configured in the protocols dictionary
                device_protocols = device.get("protocols", {})
                if protocol in device_protocols:
                    # If there are selected rows, only include selected devices
                    if selected_device_names:
                        if device.get("Device Name") in selected_device_names:
                            devices_with_protocol.append(device)
                            print(f"[{protocol} TOGGLE] Including selected device: {device.get('Device Name')}")
                    else:
                        # No selection - include all devices with this protocol
                        devices_with_protocol.append(device)

        if not devices_with_protocol:
            if selected_device_names:
                QMessageBox.information(self, f"No {protocol} Devices", 
                                      f"Selected devices don't have {protocol} protocol configured.")
            else:
                QMessageBox.information(self, f"No {protocol} Devices", 
                                      f"No devices have {protocol} protocol configured.")
            return

        action = "start" if starting else "stop"
        success_count = 0
        
        for device_info in devices_with_protocol:
            device_name = device_info.get("Device Name", "Unknown")
            device_id = device_info.get("device_id")
            
            try:
                # Prepare payload for protocol start/stop
                payload = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": self._normalize_iface_label(device_info.get("Interface", "")),
                    "mac": device_info.get("MAC Address", ""),
                    "vlan": device_info.get("VLAN", "0"),
                    "ipv4": device_info.get("IPv4", ""),
                    "ipv6": device_info.get("IPv6", ""),
                    "protocols": [protocol],
                    "ipv4_mask": device_info.get("ipv4_mask", "24"),
                    "ipv6_mask": device_info.get("ipv6_mask", "64"),
                }

                # Add protocol-specific configuration
                if protocol == "BGP" and "protocols" in device_info and "BGP" in device_info["protocols"]:
                    if isinstance(device_info["protocols"], dict):
                        payload["bgp"] = device_info["protocols"]["BGP"]
                    else:
                        payload["bgp"] = device_info.get("bgp_config", {})
                    
                    # Add specific neighbor information if rows were selected
                    print(f"[BGP TOGGLE] Checking device_name '{device_name}' against selected_bgp_neighbors keys: {list(selected_bgp_neighbors.keys())}")
                    if device_name in selected_bgp_neighbors:
                        payload["selected_neighbors"] = list(selected_bgp_neighbors[device_name])
                        print(f"[BGP TOGGLE] Adding selected neighbors for {device_name}: {payload['selected_neighbors']}")
                    else:
                        print(f"[BGP TOGGLE] Device '{device_name}' not found in selected_bgp_neighbors")
                elif protocol == "OSPF" and "protocols" in device_info and "OSPF" in device_info["protocols"]:
                    if isinstance(device_info["protocols"], dict):
                        payload["ospf_config"] = device_info["protocols"]["OSPF"]
                    else:
                        payload["ospf_config"] = device_info.get("ospf_config", {})
                elif protocol == "IS-IS" and "protocols" in device_info and "IS-IS" in device_info["protocols"]:
                    if isinstance(device_info["protocols"], dict):
                        payload["isis_config"] = device_info["protocols"]["IS-IS"]
                    else:
                        payload["isis_config"] = device_info.get("isis_config", {})

                # Call server API - use protocol-specific endpoints
                if protocol == "BGP" and action == "stop":
                    url = f"{server_url}/api/device/bgp/stop"
                    # Set interim "Stopping" state for selected neighbors
                    self._set_bgp_interim_stopping_state(device_name, payload.get("selected_neighbors", []))
                elif protocol == "BGP" and action == "start":
                    url = f"{server_url}/api/device/bgp/start"
                elif protocol == "OSPF" and action == "stop":
                    url = f"{server_url}/api/device/ospf/stop"
                elif protocol == "OSPF" and action == "start":
                    url = f"{server_url}/api/device/ospf/start"
                elif protocol == "IS-IS" and action == "stop":
                    url = f"{server_url}/api/device/isis/stop"
                elif protocol == "IS-IS" and action == "start":
                    url = f"{server_url}/api/device/isis/start"
                else:
                    url = f"{server_url}/api/device/{action}"
                resp = requests.post(url, json=payload, timeout=10)
                
                if resp.status_code == 200:
                    success_count += 1
                    logging.info(f"[{protocol} {action.upper()}] Success: {device_name}")
                else:
                    logging.error(f"[{protocol} {action.upper()}] Failed: {device_name} - {resp.text}")
                    
            except Exception as e:
                logging.error(f"[{protocol} {action.upper()}] Error: {device_name} - {e}")

        # Print results to console instead of popup
        print(f"\n{'='*60}")
        print(f"{protocol.upper()} {action.upper()} RESULTS: {success_count}/{len(devices_with_protocol)} successful")
        print(f"{'='*60}")
        if success_count > 0:
            print(f"  ✅ Successfully {action}ed {protocol} for {success_count} device(s)")
        if success_count < len(devices_with_protocol):
            failed = len(devices_with_protocol) - success_count
            print(f"  ❌ Failed to {action} {protocol} for {failed} device(s)")
        print(f"{'='*60}\n")
        
        # No popup message - status updated silently in table

        # Refresh protocol tables
        if protocol == "BGP":
            # Use QTimer to delay refresh and avoid blocking UI
            from PyQt5.QtCore import QTimer
            def delayed_refresh():
                self.update_bgp_table()
                print(f"[BGP REFRESH] Refreshed BGP table after {action} operation")
            QTimer.singleShot(1000, delayed_refresh)  # Wait 1 second for database update
            # Start/stop periodic BGP monitoring only if operations were successful
            if starting and success_count > 0:
                self.start_bgp_monitoring()
            elif not starting and success_count > 0:
                self.stop_bgp_monitoring()
        elif protocol == "OSPF":
            # Use QTimer to delay refresh and avoid blocking UI
            from PyQt5.QtCore import QTimer
            def delayed_refresh():
                self.update_ospf_table()
                print(f"[OSPF REFRESH] Refreshed OSPF table after {action} operation")
            QTimer.singleShot(1000, delayed_refresh)  # Wait 1 second for database update
            # Start/stop periodic OSPF monitoring only if operations were successful
            if starting and success_count > 0:
                self.start_ospf_monitoring()
            elif not starting and success_count > 0:
                self.stop_ospf_monitoring()
        elif protocol == "IS-IS":
            # Use QTimer to delay refresh and avoid blocking UI
            from PyQt5.QtCore import QTimer
            def delayed_refresh():
                self.update_isis_table()
                print(f"[ISIS REFRESH] Refreshed ISIS table after {action} operation")
            QTimer.singleShot(1000, delayed_refresh)  # Wait 1 second for database update
            # Start/stop periodic ISIS monitoring only if operations were successful
            if starting and success_count > 0:
                self.start_isis_monitoring()
            elif not starting and success_count > 0:
                self.stop_isis_monitoring()
    
    def start_bgp_monitoring(self):
        """Start periodic BGP status monitoring."""
        if not self.bgp_monitoring_active:
            self.bgp_monitoring_active = True
            self.bgp_monitoring_timer.start(30000)  # Check every 30 seconds to reduce UI load
            # BGP monitoring started
        else:
            # BGP monitoring already active
            pass
    
    def stop_bgp_monitoring(self):
        """Stop periodic BGP status monitoring."""
        if self.bgp_monitoring_active:
            self.bgp_monitoring_active = False
            self.bgp_monitoring_timer.stop()
            # BGP monitoring stopped
        else:
            # BGP monitoring already stopped
            pass
    
    def start_ospf_monitoring(self):
        """Start periodic OSPF status monitoring."""
        if not self.ospf_monitoring_active:
            self.ospf_monitoring_active = True
            self.ospf_monitoring_timer.start(20000)  # Check every 20 seconds to reduce UI load
            print("[OSPF MONITORING] Started periodic OSPF status monitoring")
        else:
            print("[OSPF MONITORING] Already active")
    
    def stop_ospf_monitoring(self):
        """Stop periodic OSPF status monitoring."""
        if self.ospf_monitoring_active:
            self.ospf_monitoring_active = False
            self.ospf_monitoring_timer.stop()
            print("[OSPF MONITORING] Stopped periodic OSPF status monitoring")
        else:
            print("[OSPF MONITORING] Already stopped")
    
    def start_isis_monitoring(self):
        """Start periodic ISIS status monitoring."""
        if not self.isis_monitoring_active:
            self.isis_monitoring_active = True
            self.isis_monitoring_timer.start(20000)  # Check every 20 seconds to match OSPF
            print("[ISIS MONITORING] Started periodic ISIS status monitoring")
        else:
            print("[ISIS MONITORING] Already active")
    
    def stop_isis_monitoring(self):
        """Stop periodic ISIS status monitoring."""
        if self.isis_monitoring_active:
            self.isis_monitoring_active = False
            self.isis_monitoring_timer.stop()
            print("[ISIS MONITORING] Stopped periodic ISIS status monitoring")
        else:
            print("[ISIS MONITORING] Already stopped")
    
    def periodic_isis_status_check(self):
        """Periodic ISIS status check - called by timer."""
        try:
            # Get all devices with ISIS configured
            isis_devices = []
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("protocols") and "IS-IS" in device.get("protocols", {}):
                        isis_devices.append(device)
            
            if isis_devices:
                print(f"[ISIS MONITORING] Periodic ISIS status check for {len(isis_devices)} devices")
                # Update ISIS table
                self.update_isis_table()
            
        except Exception as e:
            print(f"[ISIS MONITORING ERROR] Error in periodic ISIS status check: {e}")
    
    def start_device_status_monitoring(self):
        """Start periodic device status monitoring (including ARP)."""
        if not self.device_status_monitoring_active:
            self.device_status_monitoring_active = True
            self.device_status_timer.start(5000)  # Check every 5 seconds
            print("[DEVICE STATUS MONITORING] Started periodic device status checks")
        else:
            print("[DEVICE STATUS MONITORING] Already active - not starting again")
    
    def stop_device_status_monitoring(self):
        """Stop periodic device status monitoring."""
        if self.device_status_monitoring_active:
            self.device_status_monitoring_active = False
            self.device_status_timer.stop()
            print("[DEVICE STATUS MONITORING] Stopped periodic device status checks")
        else:
            print("[DEVICE STATUS MONITORING] Already stopped - not stopping again")
    
    def periodic_bgp_status_check(self):
        """Periodic BGP status check for all devices with BGP configured."""
        if not self.bgp_monitoring_active:
            return
        
        # Check if any devices have BGP configured
        devices_with_bgp = []
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                device_protocols = device.get("protocols", {})
                if "BGP" in device_protocols:
                    devices_with_bgp.append(device)
        
        # If no devices have BGP configured, stop monitoring
        if not devices_with_bgp:
            # No devices with BGP configured - stopping monitoring
            self.stop_bgp_monitoring()
            return
            
        # Update BGP table which will refresh all BGP statuses
        self.update_bgp_table()
        # Periodic BGP status check completed
    
    def periodic_ospf_status_check(self):
        """Periodic OSPF status check for all devices with OSPF configured."""
        if not self.ospf_monitoring_active:
            return
        
        # Check if any devices have OSPF configured
        devices_with_ospf = []
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                device_protocols = device.get("protocols", [])
                if isinstance(device_protocols, str):
                    try:
                        import json
                        device_protocols = json.loads(device_protocols)
                    except:
                        device_protocols = []
                
                if "OSPF" in device_protocols:
                    devices_with_ospf.append(device)
        
        # If no devices have OSPF configured, stop monitoring
        if not devices_with_ospf:
            print("[OSPF MONITORING] No devices with OSPF configured - stopping monitoring")
            self.stop_ospf_monitoring()
            return
            
        # Update OSPF table which will refresh all OSPF statuses
        self.update_ospf_table()
        print(f"[OSPF MONITORING] Periodic OSPF status check completed for {len(devices_with_ospf)} devices")
    
    # Removed duplicate periodic_device_status_check function - using poll_device_status instead
    
    def update_device_status_icon(self, row, arp_resolved, arp_status=""):
        """Update the device status icon based on ARP resolution."""
        try:
            # Get the status item in the Status column
            status_item = self.devices_table.item(row, self.COL["Status"])
            if not status_item:
                # Create a new item if it doesn't exist
                status_item = QTableWidgetItem()
                self.devices_table.setItem(row, self.COL["Status"], status_item)
            
            # Set icon based on ARP resolution
            if arp_resolved:
                status_item.setIcon(self.green_dot)
                status_item.setToolTip(f"ARP resolved: {arp_status}")
            else:
                status_item.setIcon(self.orange_dot)
                status_item.setToolTip(f"ARP failed: {arp_status}")
                
        except Exception as e:
            print(f"[DEVICE STATUS ICON] Error updating status icon for row {row}: {e}")

    def on_cell_changed(self, row, column):
        """Handle cell changes for inline editing."""
        try:
            # Get the header name for this column
            header_name = self.device_headers[column]
            item = self.devices_table.item(row, column)
            if not item:
                return
            
            new_value = item.text().strip()
            old_value = item.data(Qt.UserRole + 2) if item.data(Qt.UserRole + 2) else ""
            
            # Skip if value hasn't actually changed
            if new_value == old_value:
                return
            
            # Get device info
            device_name_item = self.devices_table.item(row, self.COL["Device Name"])
            if not device_name_item:
                return
            
            device_id = device_name_item.data(Qt.UserRole)
            if not device_id:
                return
            
            # Validate the new value based on field type
            if not self.validate_cell_value(header_name, new_value, row, column):
                # Revert to old value if validation fails
                item.setText(old_value)
                return
            
            # Update the device data in memory
            self.update_device_data_in_memory(device_id, header_name, new_value)
            
            # Mark device as needing apply
            self.mark_device_for_apply(device_id)
            
            # Store the new value as the "old" value for next comparison
            item.setData(Qt.UserRole + 2, new_value)
            
            # Visual feedback - change background color temporarily
            self.highlight_edited_cell(row, column)
            
            # Device field updated
            
        except Exception as e:
            logging.error(f"[on_cell_changed] Error: {e}")
    
    def validate_cell_value(self, header_name, value, row=None, column=None):
        """Validate cell values based on field type."""
        try:
            if header_name == "Device Name":
                return len(value) > 0 and len(value) <= 50
            
            elif header_name == "IPv4":
                if not value:  # Empty is allowed
                    return True
                try:
                    ipaddress.IPv4Address(value)
                    return True
                except ipaddress.AddressValueError:
                    return False
            
            elif header_name == "IPv6":
                if not value:  # Empty is allowed
                    return True
                try:
                    ipaddress.IPv6Address(value)
                    return True
                except ipaddress.AddressValueError:
                    return False
            
            elif header_name == "VLAN":
                if not value:  # Empty means no VLAN
                    return True
                try:
                    vlan_id = int(value)
                    return 0 <= vlan_id <= 4094
                except ValueError:
                    return False
            
            elif header_name == "Gateway":
                if not value:  # Empty is allowed
                    return True
                try:
                    # Try IPv4 first
                    gateway_ip = ipaddress.IPv4Address(value)
                    
                    # Get the device's IPv4 address and mask for subnet validation
                    device_ipv4 = None
                    device_mask = None
                    
                    # Use the provided row if available
                    if row is not None:
                        ipv4_item = self.devices_table.item(row, self.COL.get("IPv4", -1))
                        mask_item = self.devices_table.item(row, self.COL.get("IPv4 Mask", -1))
                        
                        if ipv4_item and ipv4_item.text().strip():
                            try:
                                device_ipv4 = ipaddress.IPv4Address(ipv4_item.text().strip())
                            except ipaddress.AddressValueError:
                                pass
                        
                        if mask_item and mask_item.text().strip():
                            try:
                                device_mask = int(mask_item.text().strip())
                            except ValueError:
                                pass
                    
                    # If we have both device IP and mask, validate subnet
                    if device_ipv4 and device_mask is not None:
                        try:
                            device_network = ipaddress.IPv4Network(f"{device_ipv4}/{device_mask}", strict=False)
                            if gateway_ip not in device_network:
                                print(f"[VALIDATION] Gateway {gateway_ip} is not in the same subnet as device IP {device_ipv4}/{device_mask}")
                                return False
                        except (ipaddress.AddressValueError, ValueError):
                            pass  # If network calculation fails, just validate IP format
                    
                    return True
                except ipaddress.AddressValueError:
                    try:
                        # Try IPv6
                        gateway_ip = ipaddress.IPv6Address(value)
                        
                        # Get the device's IPv6 address and mask for subnet validation
                        device_ipv6 = None
                        device_mask = None
                        
                        # Use the provided row if available
                        if row is not None:
                            ipv6_item = self.devices_table.item(row, self.COL.get("IPv6", -1))
                            mask_item = self.devices_table.item(row, self.COL.get("IPv6 Mask", -1))
                            
                            if ipv6_item and ipv6_item.text().strip():
                                try:
                                    device_ipv6 = ipaddress.IPv6Address(ipv6_item.text().strip())
                                except ipaddress.AddressValueError:
                                    pass
                            
                            if mask_item and mask_item.text().strip():
                                try:
                                    device_mask = int(mask_item.text().strip())
                                except ValueError:
                                    pass
                        
                        # If we have both device IP and mask, validate subnet
                        if device_ipv6 and device_mask is not None:
                            try:
                                device_network = ipaddress.IPv6Network(f"{device_ipv6}/{device_mask}", strict=False)
                                if gateway_ip not in device_network:
                                    print(f"[VALIDATION] Gateway {gateway_ip} is not in the same subnet as device IP {device_ipv6}/{device_mask}")
                                    return False
                            except (ipaddress.AddressValueError, ValueError):
                                pass  # If network calculation fails, just validate IP format
                        
                        return True
                    except ipaddress.AddressValueError:
                        return False
            
            elif header_name in ["IPv4 Mask", "IPv6 Mask"]:
                if not value:  # Empty is allowed
                    return True
                try:
                    mask = int(value)
                    if header_name == "IPv4 Mask":
                        return 0 <= mask <= 32
                    else:  # IPv6 Mask
                        return 0 <= mask <= 128
                except ValueError:
                    return False
            
            elif header_name == "MAC Address":
                if not value:  # Empty is allowed
                    return True
                # Basic MAC address validation (XX:XX:XX:XX:XX:XX format)
                import re
                mac_pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
                return bool(re.match(mac_pattern, value))
            
            elif header_name == "Status":
                # Status is read-only, shouldn't be editable
                return False
            
            return True  # Default: allow any value
            
        except Exception as e:
            logging.error(f"[validate_cell_value] Error validating {header_name}: {e}")
            return False
    
    def update_device_data_in_memory(self, device_id, header_name, new_value):
        """Update device data in the all_devices structure."""
        try:
            # Find the device in all_devices
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("device_id") == device_id:
                        # Map header names to device data keys
                        key_mapping = {
                            "Device Name": "Device Name",
                            "IPv4": "IPv4",
                            "IPv6": "IPv6",
                            "VLAN": "VLAN",
                            "Gateway": "Gateway",
                            "IPv4 Mask": "ipv4_mask",
                            "IPv6 Mask": "ipv6_mask",
                            "MAC Address": "MAC Address"
                        }
                        
                        key = key_mapping.get(header_name)
                        if key:
                            device[key] = new_value
                            # Device data updated
                        break
                else:
                    continue
                break
                
        except Exception as e:
            logging.error(f"[update_device_data_in_memory] Error: {e}")
    
    def mark_device_for_apply(self, device_id):
        """Mark a device as needing to be applied to the server."""
        try:
            # Find the device in all_devices and mark it for apply
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("device_id") == device_id:
                        device["_needs_apply"] = True
                        device["_is_new"] = False  # It's an existing device being modified
                        # Device marked for apply
                        
                        # Update the device name in the table to show it needs to be applied
                        self.update_device_name_indicator(device_id, device.get("Device Name", ""))
                        break
                else:
                    continue
                break
                
        except Exception as e:
            logging.error(f"[mark_device_for_apply] Error: {e}")
    
    def update_device_name_indicator(self, device_id, device_name):
        """Update the device name in the table to show it needs to be applied."""
        try:
            # Find the row with this device_id
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if name_item and name_item.data(Qt.UserRole) == device_id:
                    # Add a visual indicator that the device has been modified
                    if not device_name.endswith(" *"):
                        name_item.setText(device_name + " *")
                        name_item.setForeground(QColor(255, 140, 0))  # Orange color
                    break
        except Exception as e:
            logging.error(f"[update_device_name_indicator] Error: {e}")
    
    def highlight_edited_cell(self, row, column):
        """Provide visual feedback for edited cells."""
        try:
            item = self.devices_table.item(row, column)
            if item:
                # Set a light green background to indicate the cell was edited
                item.setBackground(QColor(200, 255, 200))
                
                # Use a timer to remove the highlight after 2 seconds
                QTimer.singleShot(2000, lambda: self.remove_cell_highlight(row, column))
                
        except Exception as e:
            logging.error(f"[highlight_edited_cell] Error: {e}")
    
    def remove_cell_highlight(self, row, column):
        """Remove the highlight from a cell."""
        try:
            item = self.devices_table.item(row, column)
            if item:
                item.setBackground(QColor(255, 255, 255))  # White background
        except Exception as e:
            logging.error(f"[remove_cell_highlight] Error: {e}")
    
    def setup_column_tooltips(self):
        """Set up tooltips for table columns to indicate which are editable."""
        try:
            # Define tooltips for each column
            tooltips = {
                "Device Name": "Editable: Device name (1-50 characters)",
                "IPv4": "Editable: IPv4 address (e.g., 192.168.0.2)",
                "IPv6": "Editable: IPv6 address (e.g., 2001:db8::1)",
                "VLAN": "Editable: VLAN ID (0-4094, 0 = no VLAN)",
                "Gateway": "Editable: Gateway IP address (IPv4 or IPv6)",
                "Status": "Read-only: Device status",
                "IPv4 Mask": "Editable: IPv4 subnet mask (0-32)",
                "IPv6 Mask": "Editable: IPv6 subnet mask (0-128)",
                "MAC Address": "Editable: MAC address (XX:XX:XX:XX:XX:XX format)"
            }
            
            # Set tooltips for each column header
            for i, header in enumerate(self.device_headers):
                tooltip = tooltips.get(header, "")
                if tooltip:
                    self.devices_table.horizontalHeaderItem(i).setToolTip(tooltip)
                    
        except Exception as e:
            logging.error(f"[setup_column_tooltips] Error: {e}")
    
    def clear_modification_indicators(self):
        """Clear the modification indicators (*) from device names after they are applied."""
        try:
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if name_item:
                    current_text = name_item.text()
                    if current_text.endswith(" *"):
                        # Remove the asterisk and reset color
                        clean_name = current_text[:-2]
                        name_item.setText(clean_name)
                        name_item.setForeground(QColor(0, 0, 0))  # Black color
        except Exception as e:
            logging.error(f"[clear_modification_indicators] Error: {e}")
