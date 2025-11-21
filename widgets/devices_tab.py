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
import os, json,logging,requests,ipaddress,uuid,copy
import subprocess
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.qicon_loader import qicon,r_icon
from utils.devices_tab_bgp import BGPHandler
from utils.devices_tab_ospf import OSPFHandler
from utils.devices_tab_isis import ISISHandler
from utils.devices_tab_dhcp import DHCPHandler
from utils.devices_tab_vxlan import VXLANHandler
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
                    # Immediately reflect starting state in UI
                    self.device_status_updated.emit(row, "Starting", "Device Starting...")
                    
                    # Prepare start payload for light start
                    iface_label = device_info.get("Interface", "")
                    iface_norm = self.parent_tab._normalize_iface_label(iface_label)
                    vlan = device_info.get("VLAN", "0")
                    device_id = device_info.get("device_id", "")
                    
                    protocols = []
                    protocol_data = device_info.get("protocols")
                    if isinstance(protocol_data, dict):
                        protocols = list(protocol_data.keys())
                    elif isinstance(protocol_data, list):
                        protocols = [str(p) for p in protocol_data if p]
                    if not protocols:
                        legacy = device_info.get("Protocols")
                        if isinstance(legacy, str) and legacy:
                            protocols = [p.strip() for p in legacy.split(",") if p.strip()]
                    
                    start_payload = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": iface_norm,
                        "vlan": vlan,
                        "ipv4": device_info.get("IPv4", ""),
                        "ipv6": device_info.get("IPv6", ""),
                        "protocols": protocols
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
                    # Immediately reflect stopping state in UI
                    self.device_status_updated.emit(row, "Stopping", "Device Stopping...")
                    
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
                
                if arp_results.get("needs_retry"):
                    waiting_message_raw = arp_results.get("overall_status", "Waiting for device status...")
                    if isinstance(waiting_message_raw, str) and waiting_message_raw.startswith("__RETRY__|"):
                        waiting_message = waiting_message_raw.split("|", 1)[1] if "|" in waiting_message_raw else "Waiting for device status..."
                    else:
                        waiting_message = waiting_message_raw
                    # Notify main thread to update UI and schedule retry
                    self.device_status_updated.emit(row, False, f"__RETRY__|{waiting_message}")
                    return (waiting_message, None, row, arp_results)
                
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
                    if success is None:
                        results.append(f"⏳ {result_text}")
                    else:
                        results.append(result_text)
                        if success:
                            successful_count += 1
                        else:
                            failed_count += 1
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
        """Apply multiple devices in background thread - parallelized for faster creation."""
        results = []
        successful_count = 0
        failed_count = 0
        
        def process_single_device(row_device_tuple):
            """Process a single device's apply operation."""
            row, device_info = row_device_tuple
            device_name = device_info.get("Device Name", "Unknown")
            
            try:
                self.progress.emit(device_name, "Applying...")
                
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
                    self.device_applied.emit(device_name, True, message)
                    return (message, True)
                else:
                    message = f"❌ {device_name}: Failed to apply to server"
                    self.device_applied.emit(device_name, False, message)
                    return (message, False)
                    
            except Exception as e:
                message = f"❌ {device_name}: Error - {str(e)}"
                self.device_applied.emit(device_name, False, message)
                print(f"[MULTI DEVICE APPLY ERROR] {device_name}: {e}")
                return (message, False)
        
        # Process devices in parallel using ThreadPoolExecutor
        # Limit to 5 concurrent operations to avoid overwhelming the server
        max_workers = min(len(self.devices_to_apply), 5)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all device processing tasks
            future_to_device = {
                executor.submit(process_single_device, (row, device_info)): (row, device_info)
                for row, device_info in self.devices_to_apply
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_device):
                if self._should_stop:
                    break
                    
                try:
                    result = future.result()
                    if result:
                        message, success = result
                        results.append(message)
                        if success:
                            successful_count += 1
                        else:
                            failed_count += 1
                except Exception as e:
                    row, device_info = future_to_device[future]
                    device_name = device_info.get("Device Name", "Unknown")
                    message = f"❌ {device_name}: Exception - {str(e)}"
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
        
        # Check if application is closing (if we have access to main_window)
        if hasattr(self, 'parent_tab') and hasattr(self.parent_tab, 'main_window'):
            if hasattr(self.parent_tab.main_window, '_is_closing') and self.parent_tab.main_window._is_closing:
                print("[SESSION LOAD] Skipping server online check - application is closing")
                return
            
        results = []
        for server in server_data:
            if self._should_stop:
                break
            
            # Check again if application is closing
            if hasattr(self, 'parent_tab') and hasattr(self.parent_tab, 'main_window'):
                if hasattr(self.parent_tab.main_window, '_is_closing') and self.parent_tab.main_window._is_closing:
                    print("[SESSION LOAD] Stopping server checks - application is closing")
                    break
                
            try:
                address = server.get("address")
                print(f"[SESSION LOAD] Checking server online status: {address}")
                # Reduced timeout for server checks
                response = requests.get(f"{address}/api/interfaces", timeout=3)
                
                if response.status_code == 200:
                    server["online"] = True
                    server["interfaces"] = response.json()
                    results.append({"server": server, "success": True})
                    print(f"[SESSION LOAD] ✅ Server {address} is online")
                else:
                    server["online"] = False
                    error_msg = f"HTTP {response.status_code}"
                    results.append({"server": server, "success": False, "error": error_msg})
                    print(f"[SESSION LOAD] ❌ Server {address} check failed: {error_msg}")
                    
            except Exception as e:
                server["online"] = False
                error_msg = str(e)
                results.append({"server": server, "success": False, "error": error_msg})
                print(f"[SESSION LOAD] ❌ Server {address} check error: {error_msg}")
        
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

        # OSPF/BGP pages don't need to be strict here; final check below
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

        # Dedicated timer/flag for lightweight periodic status refreshes triggered after ops
        self.device_status_timer = QTimer()
        self.device_status_timer.timeout.connect(self.poll_device_status)
        self.device_status_monitoring_active = False

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

        # Initialize protocol handlers
        self.bgp_handler = BGPHandler(self)
        self.ospf_handler = OSPFHandler(self)
        self.isis_handler = ISISHandler(self)
        self.dhcp_handler = DHCPHandler(self)
        self.vxlan_handler = VXLANHandler(self)

        # Create Devices sub-tab
        self.devices_subtab = QWidget()
        self.setup_devices_subtab()

        # Create BGP sub-tab
        self.bgp_subtab = QWidget()
        self.bgp_handler.setup_bgp_subtab()

        # Create OSPF sub-tab
        self.ospf_subtab = QWidget()
        self.ospf_handler.setup_ospf_subtab()

        # Create ISIS sub-tab
        self.isis_subtab = QWidget()
        self.isis_handler.setup_isis_subtab()

        # Create DHCP sub-tab
        self.dhcp_subtab = QWidget()
        self.dhcp_handler.setup_dhcp_subtab()

        # Create VXLAN sub-tab
        self.vxlan_subtab = QWidget()
        self.vxlan_handler.setup_vxlan_subtab()

        # Add tabs to tab widget
        self.tab_widget.addTab(self.devices_subtab, "Devices")
        self.tab_widget.addTab(self.bgp_subtab, "BGP")
        self.tab_widget.addTab(self.ospf_subtab, "OSPF")
        self.tab_widget.addTab(self.isis_subtab, "ISIS")
        self.tab_widget.addTab(self.dhcp_subtab, "DHCP")
        self.tab_widget.addTab(self.vxlan_subtab, "VXLAN")


    def setup_devices_subtab(self):
        """Setup the Devices sub-tab with device table and controls."""
        layout = QVBoxLayout(self.devices_subtab)

        # columns
        # Simplified device table - only essential device info
        self.device_headers = [
            "Device Name",
            "Status",
            "IPv4",
            "IPv6",
            "VLAN",
            "IPv4 Gateway",
            "IPv6 Gateway",
            "IPv4 Mask",
            "IPv6 Mask",
            "MAC Address",
            "Loopback IPv4",
            "Loopback IPv6",
            "VXLAN",
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
        self.devices_table.setColumnWidth(self.COL["VXLAN"], 200)

        # optionally hide internal-ish fields (starting from column 12, after Loopback IPv4 and IPv6 at columns 10-11)
        for col in range(12, 16):
            if col >= len(self.device_headers):
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
        return self.bgp_handler.setup_bgp_subtab()
    
    def setup_bgp_subtab_old(self):
        """Setup the BGP sub-tab with BGP-specific functionality (old implementation - kept for reference)."""
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
        return self.ospf_handler.setup_ospf_subtab()
    
    def setup_ospf_subtab_old(self):
        """Setup the OSPF sub-tab with OSPF-specific functionality (old implementation - kept for reference)."""
        layout = QVBoxLayout(self.ospf_subtab)
        
        # OSPF Neighbors Table
        ospf_headers = ["Device", "OSPF Status", "Area ID", "Neighbor Type", "Interface", "Neighbor ID", "State", "Priority", "Dead Timer", "Uptime", "Graceful Restart"]
        self.ospf_table = QTableWidget(0, len(ospf_headers))
        self.ospf_table.setHorizontalHeaderLabels(ospf_headers)
        
        # Enable inline editing for the OSPF table
        self.ospf_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.ospf_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect cell changed signal for inline editing
        self.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
        
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
        
        self.apply_ospf_button = QPushButton()
        self.apply_ospf_button.setIcon(load_icon("apply.png"))
        self.apply_ospf_button.setIconSize(QSize(16, 16))
        self.apply_ospf_button.setFixedSize(32, 28)
        self.apply_ospf_button.setToolTip("Apply OSPF Configuration to FRR")
        self.apply_ospf_button.clicked.connect(self.apply_ospf_configurations)
        
        ospf_controls.addWidget(self.add_ospf_button)
        ospf_controls.addWidget(self.edit_ospf_button)
        ospf_controls.addWidget(self.delete_ospf_button)
        ospf_controls.addWidget(self.apply_ospf_button)
        ospf_controls.addWidget(self.ospf_start_button)
        ospf_controls.addWidget(self.ospf_stop_button)
        ospf_controls.addWidget(self.ospf_refresh_button)
        ospf_controls.addStretch()
        layout.addLayout(ospf_controls)

    def setup_isis_subtab(self):
        """Setup the ISIS sub-tab with ISIS-specific functionality."""
        return self.isis_handler.setup_isis_subtab()
    
    def setup_isis_subtab_old(self):
        """Setup the ISIS sub-tab with ISIS-specific functionality (old implementation - kept for reference)."""
        layout = QVBoxLayout(self.isis_subtab)
        
        # ISIS Neighbors Table with requested columns
        isis_headers = ["Device", "ISIS Status", "Neighbor Type", "Neighbor Hostname", "Interface", "ISIS Area", "Level", "ISIS Net", "System ID", "Hello Interval", "Multiplier"]
        self.isis_table = QTableWidget(0, len(isis_headers))
        self.isis_table.setHorizontalHeaderLabels(isis_headers)
        
        # Set column widths for better visibility
        self.isis_table.setColumnWidth(0, 120)  # Device
        self.isis_table.setColumnWidth(1, 100)  # ISIS Status
        self.isis_table.setColumnWidth(2, 120)  # Neighbor Type
        self.isis_table.setColumnWidth(3, 150)  # Neighbor Hostname
        self.isis_table.setColumnWidth(4, 100)  # Interface
        self.isis_table.setColumnWidth(5, 120)  # ISIS Area
        self.isis_table.setColumnWidth(6, 80)   # Level
        self.isis_table.setColumnWidth(7, 200)  # ISIS Net
        self.isis_table.setColumnWidth(8, 120)  # System ID
        self.isis_table.setColumnWidth(9, 100)  # Hello Interval
        self.isis_table.setColumnWidth(10, 100)  # Multiplier
        
        # Enable inline editing for the ISIS table
        self.isis_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.isis_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        # Connect cell changed signal for inline editing
        self.isis_table.cellChanged.connect(self.on_isis_table_cell_changed)
        
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
        return self.isis_handler.prompt_edit_isis()
    def prompt_delete_isis(self):
        """Delete ISIS configuration for selected device."""
        return self.isis_handler.prompt_delete_isis()
    def apply_isis_configurations(self):
        """Apply ISIS configurations to the server for selected ISIS table rows."""
        return self.isis_handler.apply_isis_configurations()
    def _apply_isis_to_devices(self, devices, server_url):
        """Apply ISIS configuration to the specified devices."""
        return self.isis_handler._apply_isis_to_devices(devices, server_url)
    def _remove_isis_from_devices(self, devices, server_url):
        """Remove ISIS configuration from the specified devices."""
        return self.isis_handler._remove_isis_from_devices(devices, server_url)
    def refresh_bgp_status(self):
        """Refresh BGP neighbor status from database - only update status, don't replace table."""
        return self.bgp_handler.refresh_bgp_status()
    def on_bgp_selection_changed(self):
        """Update attach button tooltip when selection changes."""
        return self.bgp_handler.on_bgp_selection_changed()

    def on_cell_changed(self, row, col):
        """Handle changes to device table cells."""
        # Stub method for device table cell changes
        # Add validation logic here if needed
        pass

    def on_bgp_table_cell_changed(self, row, col):
        """Handle changes to BGP table cells."""
        # Stub method for BGP table cell changes
        # Add validation logic here if needed
        pass

    def on_ospf_table_cell_changed(self, row, col):
        """Handle changes to OSPF table cells."""
        # Stub method for OSPF table cell changes
        # Add validation logic here if needed
        pass

    def on_isis_table_cell_changed(self, row, col):
        """Handle changes to IS-IS table cells."""
        # Stub method for IS-IS table cell changes
        # Add validation logic here if needed
        pass

    def prompt_add_bgp(self):
        """Add BGP configuration to the currently selected device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to add BGP configuration.")
            return

        row = selected_items[0].row()
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()

        device_ipv4 = self.devices_table.item(row, self.COL["IPv4"]).text() if self.devices_table.item(row, self.COL["IPv4"]) else ""
        device_ipv6 = self.devices_table.item(row, self.COL["IPv6"]).text() if self.devices_table.item(row, self.COL["IPv6"]) else ""
        gateway_ipv4 = self.devices_table.item(row, self.COL["IPv4 Gateway"]).text() if self.devices_table.item(row, self.COL["IPv4 Gateway"]) else ""
        gateway_ipv6 = self.devices_table.item(row, self.COL["IPv6 Gateway"]).text() if self.devices_table.item(row, self.COL["IPv6 Gateway"]) else ""

        dialog = AddBgpDialog(
            self,
            device_name,
            edit_mode=False,
            device_ipv4=device_ipv4,
            device_ipv6=device_ipv6,
            gateway_ipv4=gateway_ipv4,
            gateway_ipv6=gateway_ipv6,
        )
        if dialog.exec_() != dialog.Accepted:
            return

        bgp_config = dialog.get_values()
        self._update_device_protocol(row, "BGP", bgp_config)

    def prompt_edit_bgp(self):
        """Edit BGP configuration for the selected neighbor entry."""
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a BGP configuration to edit.")
            return

        selected_rows = {item.row() for item in selected_items}
        if len(selected_rows) > 1:
            QMessageBox.warning(self, "Multiple Selection", "Please select only one BGP configuration to edit.")
            return

        row = next(iter(selected_rows))
        device_name = self.bgp_table.item(row, 0).text()

        neighbor_type_item = self.bgp_table.item(row, 2)
        protocol_type = neighbor_type_item.text().strip() if neighbor_type_item else "IPv4"
        is_ipv6 = protocol_type == "IPv6"

        device_info = self._find_device_by_name(device_name)
        if not device_info or "BGP" not in device_info.get("protocols", []):
            QMessageBox.warning(self, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")
            return

        device_ipv4 = device_info.get("IPv4", "")
        device_ipv6 = device_info.get("IPv6", "")
        gateway_ipv4 = device_info.get("IPv4 Gateway", "")
        gateway_ipv6 = device_info.get("IPv6 Gateway", "")

        current_bgp = device_info.get("bgp_config", {})

        dialog = AddBgpDialog(
            self,
            device_name,
            edit_mode=True,
            device_ipv4=device_ipv4,
            device_ipv6=device_ipv6,
            gateway_ipv4=gateway_ipv4,
            gateway_ipv6=gateway_ipv6,
        )

        dialog.bgp_mode_combo.setCurrentText(current_bgp.get("bgp_mode", "eBGP"))
        dialog.bgp_asn_input.setText(current_bgp.get("bgp_asn", ""))
        dialog.bgp_remote_asn_input.setText(current_bgp.get("bgp_remote_asn", ""))
        dialog.bgp_keepalive_input.setValue(int(current_bgp.get("bgp_keepalive", "30")))
        dialog.bgp_hold_time_input.setValue(int(current_bgp.get("bgp_hold_time", "90")))

        if is_ipv6:
            dialog.ipv4_enabled.setChecked(False)
            dialog.ipv6_enabled.setChecked(True)
            dialog.bgp_neighbor_ipv6_input.setText(current_bgp.get("bgp_neighbor_ipv6", ""))
            dialog.bgp_update_source_ipv6_input.setText(current_bgp.get("bgp_update_source_ipv6", ""))
            dialog.bgp_neighbor_ipv4_input.clear()
            dialog.bgp_update_source_ipv4_input.clear()
        else:
            dialog.ipv4_enabled.setChecked(True)
            dialog.ipv6_enabled.setChecked(False)
            dialog.bgp_neighbor_ipv4_input.setText(current_bgp.get("bgp_neighbor_ipv4", ""))
            dialog.bgp_update_source_ipv4_input.setText(current_bgp.get("bgp_update_source_ipv4", ""))
            dialog.bgp_neighbor_ipv6_input.clear()
            dialog.bgp_update_source_ipv6_input.clear()

        if dialog.exec_() != dialog.Accepted:
            return

        new_bgp_config = dialog.get_values()
        merged_config = current_bgp.copy()

        if is_ipv6:
            merged_config["bgp_neighbor_ipv6"] = new_bgp_config.get("bgp_neighbor_ipv6", "")
            merged_config["bgp_update_source_ipv6"] = new_bgp_config.get("bgp_update_source_ipv6", "")
            merged_config["ipv6_enabled"] = new_bgp_config.get("ipv6_enabled", True)
        else:
            merged_config["bgp_neighbor_ipv4"] = new_bgp_config.get("bgp_neighbor_ipv4", "")
            merged_config["bgp_update_source_ipv4"] = new_bgp_config.get("bgp_update_source_ipv4", "")
            merged_config["ipv4_enabled"] = new_bgp_config.get("ipv4_enabled", True)

        for key in ("bgp_mode", "bgp_asn", "bgp_remote_asn", "bgp_keepalive", "bgp_hold_time"):
            merged_config[key] = new_bgp_config.get(key, merged_config.get(key))

        if "route_pools" in current_bgp:
            merged_config["route_pools"] = current_bgp["route_pools"]

        device_info["bgp_config"] = merged_config
        self._update_device_protocol(device_name, "BGP", merged_config)
        self.update_bgp_table()
        if hasattr(self.main_window, "save_session"):
            self.main_window.save_session()

    def prompt_delete_bgp(self):
        """Delete BGP configuration for selected device(s)."""
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a BGP configuration to delete.")
            return

        row = selected_items[0].row()
        device_name = self.bgp_table.item(row, 0).text()

        if (
            QMessageBox.question(
                self,
                "Confirm Deletion",
                f"Are you sure you want to delete BGP configuration for '{device_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return

        device_info = self._find_device_by_name(device_name)
        if not device_info or "BGP" not in device_info.get("protocols", []):
            QMessageBox.warning(self, "No BGP Configuration", f"No BGP configuration found for device '{device_name}'.")
            return

        device_id = device_info.get("device_id")
        if device_id:
            server_url = self.get_server_url()
            if server_url:
                try:
                    response = requests.post(
                        f"{server_url}/api/bgp/cleanup",
                        json={"device_id": device_id},
                        timeout=10,
                    )
                    if response.status_code == 200:
                        print(f"✅ BGP configuration removed from server for {device_name}")
                    else:
                        error_msg = response.json().get("error", "Unknown error")
                        print(f"⚠️ Server BGP cleanup failed for {device_name}: {error_msg}")
                except requests.exceptions.RequestException as exc:
                    print(f"⚠️ Network error removing BGP from server for {device_name}: {exc}")

        device_info["bgp_config"] = {"_marked_for_removal": True}
        self.update_bgp_table()
        if hasattr(self.main_window, "save_session"):
            self.main_window.save_session()
        QMessageBox.information(
            self,
            "BGP Configuration Marked for Removal",
            f"BGP configuration for '{device_name}' has been marked for removal. Click 'Apply BGP' to remove it from the server.",
        )
    def prompt_attach_route_pools(self):
        """Attach route pools to the selected BGP neighbors."""
        selected_items = self.bgp_table.selectedItems()
        if not selected_items:
            total_rows = self.bgp_table.rowCount()
            if total_rows > 0:
                self.bgp_table.selectAll()
                print(f"[BGP TABLE] All {total_rows} rows selected")
            else:
                QMessageBox.warning(self, "No BGP Neighbors", "No BGP neighbors are configured. Please add BGP neighbors first.")
            return

        if not hasattr(self.main_window, 'bgp_route_pools'):
            self.main_window.bgp_route_pools = []
        available_pools = self.main_window.bgp_route_pools

        if not available_pools:
            QMessageBox.warning(
                self,
                "No Route Pools",
                "No route pools have been defined.\n\nUse 🗂️ 'Manage Route Pools' on the Devices tab to create pools first.",
            )
            return

        selected_neighbors = []
        processed = set()
        for item in selected_items:
            row = item.row()
            device_name = self.bgp_table.item(row, 0).text()
            neighbor_ip = self.bgp_table.item(row, 3).text()

            clean_device_name = device_name.split(" (")[0].strip()
            neighbor_key = f"{clean_device_name}:{neighbor_ip}"
            if neighbor_key in processed:
                continue
            processed.add(neighbor_key)

            device_info = self._find_device_by_name(clean_device_name)
            if not isinstance(device_info, dict) or "BGP" not in device_info.get("protocols", []):
                continue

            bgp_config = device_info.get("bgp_config", {})
            if not bgp_config:
                continue

            selected_neighbors.append(
                {
                    "device_name": clean_device_name,
                    "neighbor_ip": neighbor_ip,
                    "device_info": device_info,
                    "bgp_config": bgp_config,
                }
            )

        if not selected_neighbors:
            QMessageBox.warning(self, "No Valid BGP Neighbors", "No valid BGP neighbors found in the selection.")
            return

        if len(selected_neighbors) == 1:
            neighbor = selected_neighbors[0]
            device_name = neighbor["device_name"]
            neighbor_ip = neighbor["neighbor_ip"]
            bgp_config = neighbor["bgp_config"]

            if "route_pools" not in bgp_config:
                bgp_config["route_pools"] = {}
            attached_pool_names = bgp_config["route_pools"].get(neighbor_ip, [])

            dialog = AttachRoutePoolsDialog(
                self,
                device_name=f"{device_name} → {neighbor_ip}",
                available_pools=available_pools,
                attached_pools=attached_pool_names,
                bgp_config=bgp_config,
            )
            if dialog.exec_() != dialog.Accepted:
                return

            bgp_config["route_pools"][neighbor_ip] = dialog.get_attached_pools()
            neighbor["device_info"]["_needs_apply"] = True
            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            self.update_bgp_table()
            return

        # Multiple neighbors selected: use multi-selection dialog
        dialog = AttachRoutePoolsDialog.multi_select(
            parent=self,
            neighbors=selected_neighbors,
            available_pools=available_pools,
        )
        if dialog and dialog.exec_() == dialog.Accepted:
            updated_configs = dialog.get_updated_configs()
            for device_name, updates in updated_configs.items():
                device_info = self._find_device_by_name(device_name)
                if device_info:
                    device_info["bgp_config"]["route_pools"].update(updates)
                    device_info["_needs_apply"] = True

            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()
            self.update_bgp_table()

    def apply_bgp_configurations(self):
        """Apply (or remove) BGP configurations for the selected BGP neighbors."""
        return self.bgp_handler.apply_bgp_configurations() if hasattr(self.bgp_handler, "apply_bgp_configurations") else None

    def start_bgp_protocol(self):
        """Start BGP protocol for selected devices."""
        self._toggle_protocol_action("BGP", starting=True)

    def stop_bgp_protocol(self):
        """Stop BGP protocol for selected devices."""
        self._toggle_protocol_action("BGP", starting=False)

    def refresh_ospf_status(self):
        """Refresh OSPF neighbor status from server."""
        return self.ospf_handler.refresh_ospf_status()
    def refresh_isis_status(self):
        """Refresh ISIS neighbor status from server."""
        return self.isis_handler.refresh_isis_status()
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
        return self.bgp_handler._get_single_bgp_neighbor_state(device_id, neighbor_ip, device_info)
    def _get_bgp_neighbor_state_from_database(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state from database instead of direct server check"""
        return self.bgp_handler._get_bgp_neighbor_state_from_database(device_id, neighbor_ip, device_info)
    def _get_bgp_neighbor_state(self, device_id, neighbor_ip, device_info=None):
        """Get BGP neighbor state - now uses database instead of direct server check"""
        return self.bgp_handler._get_bgp_neighbor_state(device_id, neighbor_ip, device_info)
    def update_bgp_table(self, neighbors=None):
        """Update the BGP table with neighbor information - one row per neighbor IP."""
        return self.bgp_handler.update_bgp_table(neighbors)
    def update_ospf_table(self):
        """Update OSPF table with data from devices."""
        return self.ospf_handler.update_ospf_table()
    def update_isis_table(self):
        """Update ISIS table with data from devices and ISIS status from database."""
        return self.isis_handler.update_isis_table()
    
    def set_isis_status_icon(self, row, status, tooltip):
        """Set ISIS status icon for a table row."""
        return self.isis_handler.set_isis_status_icon(row, status, tooltip)
    def _get_isis_status_from_database(self, device_id: str) -> dict:
        """Get ISIS status from database for a device."""
        return self.isis_handler._get_isis_status_from_database(device_id)

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
            if hasattr(self, "dhcp_handler") and self.dhcp_handler:
                QTimer.singleShot(250, self.dhcp_handler.refresh_dhcp_status)

            operation_type = getattr(self, '_current_operation_type', None)
            protocols = self._collect_protocols_for_rows(selected_rows)
            if operation_type == 'start':
                # Ensure device/status monitoring resumes for started devices
                self.start_device_status_monitoring()
                if "BGP" in protocols:
                    self.start_bgp_monitoring()
                if "OSPF" in protocols:
                    self.start_ospf_monitoring()
                if "IS-IS" in protocols or "ISIS" in protocols:
                    self.start_isis_monitoring()
            elif operation_type == 'stop':
                # Stop periodic monitoring when device is stopped
                self.stop_device_status_monitoring()
                if "BGP" in protocols:
                    self.stop_bgp_monitoring()
                if "OSPF" in protocols:
                    self.stop_ospf_monitoring()
                if "IS-IS" in protocols or "ISIS" in protocols:
                    self.stop_isis_monitoring()
        
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
                                # Only require ARP resolution for configured addresses (same logic as _check_individual_arp_resolution)
                                ipv6_value = (device_data.get("ipv6_address") or device_data.get("IPv6") or "").strip()
                                ipv6_configured = bool(ipv6_value)
                                gateway_value = (device_data.get("ipv4_gateway") or device_data.get("IPv4 Gateway") or "").strip()
                                gateway_configured = bool(gateway_value)
                                
                                # Determine overall ARP status - require only the components that exist
                                overall_resolved = arp_results["ipv4_resolved"]
                                if ipv6_configured:
                                    overall_resolved = overall_resolved and arp_results["ipv6_resolved"]
                                if gateway_configured:
                                    overall_resolved = overall_resolved and arp_results["gateway_resolved"]
                                
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
            if isinstance(status, str) and status.startswith("__RETRY__|"):
                message = status.split("|", 1)[1] if "|" in status else "Waiting for device status..."
                self._set_device_status_starting(row, status_text=message)
                self._schedule_arp_retry({row}, delay=2000)
                return
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
            if hasattr(self, '_arp_retry_rows') and self._arp_retry_rows:
                print(f"[DEBUG ARP FINISHED] Pending retries for rows {self._arp_retry_rows} - keeping _pending_arp_rows intact")
            else:
                delattr(self, '_pending_arp_rows')
                print(f"[DEBUG ARP FINISHED] Cleared _pending_arp_rows")
        
        # ARP results are now shown via color indicators in the UI
        # No popup needed since status is visible through colored dots and text
    
    def _collect_protocols_for_rows(self, selected_rows):
        """Collect protocol names for devices in the provided rows."""
        protocols = set()
        try:
            for row in selected_rows:
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if not name_item:
                    continue
                device_name = name_item.text()
                found = False
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            device_protocols = device.get("protocols", {})
                            if isinstance(device_protocols, dict):
                                protocols.update(device_protocols.keys())
                                has_protocols = bool(device_protocols)
                            else:
                                has_protocols = False

                            if not has_protocols:
                                legacy = device.get("Protocols")
                                if isinstance(legacy, str) and legacy:
                                    protocols.update({p.strip() for p in legacy.split(",") if p.strip()})
                            found = True
                            break
                    if found:
                        break
        except Exception as e:
            logging.error(f"[PROTOCOL COLLECT ERROR] {e}")
        return protocols

    def _refresh_protocols_for_selected_devices(self, selected_rows):
        """Refresh protocol tabs (BGP, OSPF, ISIS) for devices in selected rows (optimized, non-blocking)."""
        try:
            protocols_to_refresh = self._collect_protocols_for_rows(selected_rows)
            if not protocols_to_refresh:
                return
                
            if "BGP" in protocols_to_refresh:
                self._safe_update_bgp_table()
            if "OSPF" in protocols_to_refresh:
                self._safe_update_ospf_table()
            if "IS-IS" in protocols_to_refresh:
                self._safe_update_isis_table()
            
            print(f"[PROTOCOL REFRESH] Refreshed protocols: {', '.join(protocols_to_refresh)}")
        
        except Exception as e:
            logging.error(f"[PROTOCOL REFRESH ERROR] {e}")
    
    def _safe_update_bgp_table(self):
        """Safely update BGP table (for parallel execution)."""
        return self.bgp_handler._safe_update_bgp_table()
    def _safe_update_ospf_table(self):
        """Safely update OSPF table (for parallel execution)."""
        return self.ospf_handler._safe_update_ospf_table()
    def _safe_update_isis_table(self):
        """Safely update ISIS table (for parallel execution)."""
        return self.isis_handler._safe_update_isis_table()
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
        elif device_status == "Starting":
            # Device is starting - show yellow/orange icon with status text
            icon = self.orange_dot
            tooltip = status_text or "Device Starting..."
            item.setText("Starting...")
            item.setData(Qt.UserRole, "Starting")
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
        return self.ospf_handler.set_ospf_status_icon(row, ospf_status, status_text)
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

    def _get_server_url_from_interface(self, iface_label):
        """Derive the server URL from an interface label (e.g., 'TG 0 - Port: • ens4np0')."""
        if not iface_label:
            return self.get_server_url(silent=True)

        if "TG" in iface_label:
            tg_part = iface_label.split("-")[0].strip()
            parts = tg_part.split()
            tg_id = parts[-1] if parts else None

            if tg_id and hasattr(self.main_window, "server_interfaces"):
                # Prefer matching online servers
                for server in self.main_window.server_interfaces:
                    if str(server.get("tg_id", "")) == tg_id and server.get("online"):
                        return server.get("address")

                for server in self.main_window.server_interfaces:
                    if str(server.get("tg_id", "")) == tg_id:
                        return server.get("address")

        if hasattr(self.main_window, "server_interfaces") and self.main_window.server_interfaces:
            for server in self.main_window.server_interfaces:
                if server.get("online"):
                    return server.get("address")
            return self.main_window.server_interfaces[0].get("address")

        return self.get_server_url(silent=True)

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

            # Update UI to show starting status immediately
            self._set_device_status_starting(row, device_info, status_text="Starting configuration...")

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
            print(f"[DEBUG APPLY] Saving session after successful device application ({successful_count} device(s) applied)")
            try:
                self.main_window.save_session()
                print(f"[DEBUG APPLY] ✅ Session saved successfully after applying {successful_count} device(s)")
            except Exception as save_exc:
                print(f"[DEBUG APPLY] ⚠️ Failed to save session: {save_exc}")
    
    def ping_selected_device(self):
        """Ping the selected device(s) after ensuring ARP has been resolved."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to ping.")
            return

        selected_rows = {item.row() for item in selected_items}
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to ping.")
            return

        results = []
        successful_count = 0
        failed_count = 0
        arp_not_resolved_count = 0

        for row in selected_rows:
            name_item = self.devices_table.item(row, self.COL["Device Name"])
            if not name_item:
                continue
            device_name = name_item.text()

            device_info = self._find_device_by_name(device_name)
            if not device_info:
                results.append(f"❌ {device_name}: Device not found in data structure")
                failed_count += 1
                continue

            arp_resolved, arp_status = self._check_arp_resolution_sync(device_info)
            self.update_device_status_icon(row, arp_resolved, arp_status=arp_status)

            if not arp_resolved:
                results.append(f"⚠️ {device_name}: ARP not resolved - {arp_status}")
                arp_not_resolved_count += 1
                continue

            ipv6 = (device_info.get("IPv6") or "").strip()
            ipv4 = (device_info.get("IPv4") or "").strip()
            gateway = (device_info.get("IPv4 Gateway") or device_info.get("Gateway") or "").strip()

            ping_target = None
            target_type = ""
            ip_version = ""

            if gateway:
                ping_target = gateway
                target_type = "Gateway"
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

            server_url = self._get_server_url_from_interface(device_info.get("Interface", ""))
            if not server_url:
                results.append(f"❌ {device_name}: No server URL found for interface")
                failed_count += 1
                continue

            try:
                response = requests.post(
                    f"{server_url}/api/device/ping",
                    json={"ip_address": ping_target},
                    timeout=15,
                )

                if response.status_code == 200:
                    payload = response.json()
                    success = payload.get("success", False)
                    output = payload.get("output") or ""
                    error = payload.get("error") or ""
                else:
                    success = False
                    output = ""
                    error = f"Server error: {response.status_code}"

                if success:
                    message = output.strip() or "Reachable"
                    results.append(f"✅ {device_name}: {target_type} '{ping_target}' ({ip_version}) - {message}")
                    successful_count += 1
                else:
                    message = error.strip() or "Not reachable"
                    results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - {message}")
                    failed_count += 1
            except requests.exceptions.Timeout:
                results.append(f"⏱️ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Timeout")
                failed_count += 1
            except requests.exceptions.RequestException as exc:
                results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Network error: {exc}")
                failed_count += 1
            except Exception as exc:
                results.append(f"❌ {device_name}: {target_type} '{ping_target}' ({ip_version}) - Error: {exc}")
                failed_count += 1

        total_devices = len(selected_rows)
        summary = (
            f"Ping Results ({total_devices} device{'s' if total_devices > 1 else ''}):\n"
            f"✅ Successful: {successful_count} | ❌ Failed: {failed_count} | ⚠️ ARP Not Resolved: {arp_not_resolved_count}"
        )
        if arp_not_resolved_count:
            results.append("💡 Tip: Refresh ARP after applying configuration to resolve connectivity before pinging.")

        if successful_count == total_devices:
            title = "All Pings Successful"
        elif successful_count > 0:
            title = "Partial Ping Success"
        else:
            title = "All Pings Failed"

        dialog = MultiDeviceResultsDialog(title, summary, results, self)
        dialog.exec_()

    def _on_arp_button_clicked(self):
        """Refresh ARP status when the ARP button is clicked."""
        try:
            self.refresh_arp_selected_device()
        except Exception as exc:
            print(f"[ARP REFRESH] Error: {exc}")

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
                # Clean up finished worker - ensure thread is stopped first
                worker = self.multi_device_apply_worker
                delattr(self, 'multi_device_apply_worker')
                if worker.isRunning():
                    worker.quit()
                    worker.wait(100)
                if not worker.isRunning():
                    worker.deleteLater()

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
                self._set_device_status_starting(row, device_info, status_text="Starting configuration...")
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
                self._set_device_status_starting(row, device_info=None, status_text="Starting configuration...")
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
                # Clean up finished worker - ensure thread is stopped first
                worker = self.arp_operation_worker
                delattr(self, 'arp_operation_worker')
                if worker.isRunning():
                    worker.quit()
                    worker.wait(100)
                if not worker.isRunning():
                    worker.deleteLater()
        
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
    
    def validate_cell_value(self, header_name, value, row=None, column=None):
        """Validate edited table cell values."""
        try:
            if header_name == "Device Name":
                return 0 < len(value) <= 50

            if header_name == "IPv4":
                if not value:
                    return True
                try:
                    ipaddress.IPv4Address(value)
                    return True
                except ipaddress.AddressValueError:
                    return False

            if header_name == "IPv6":
                if not value:
                    return True
                try:
                    ipaddress.IPv6Address(value)
                    return True
                except ipaddress.AddressValueError:
                    return False

            if header_name == "VLAN":
                if not value:
                    return True
                try:
                    vlan_id = int(value)
                    return 0 <= vlan_id <= 4094
                except ValueError:
                    return False

            if header_name == "IPv4 Mask":
                if not value:
                    return True
                try:
                    mask = int(value)
                    return 0 <= mask <= 32
                except ValueError:
                    return False

            if header_name == "IPv6 Mask":
                if not value:
                    return True
                try:
                    mask = int(value)
                    return 0 <= mask <= 128
                except ValueError:
                    return False

            if header_name == "IPv4 Gateway":
                if not value:
                    return True
                try:
                    gateway_ip = ipaddress.IPv4Address(value)
                except ipaddress.AddressValueError:
                    return False

                if row is not None:
                    ipv4_item = self.devices_table.item(row, self.COL.get("IPv4", -1))
                    mask_item = self.devices_table.item(row, self.COL.get("IPv4 Mask", -1))
                    try:
                        ip_addr = ipaddress.IPv4Address(ipv4_item.text().strip()) if ipv4_item and ipv4_item.text().strip() else None
                        mask = int(mask_item.text().strip()) if mask_item and mask_item.text().strip() else None
                        if ip_addr and mask is not None:
                            network = ipaddress.IPv4Network(f"{ip_addr}/{mask}", strict=False)
                            if gateway_ip not in network:
                                return False
                    except (ipaddress.AddressValueError, ValueError):
                        return True
                return True

            if header_name == "IPv6 Gateway":
                if not value:
                    return True
                try:
                    gateway_ip = ipaddress.IPv6Address(value)
                except ipaddress.AddressValueError:
                    return False

                if row is not None:
                    ipv6_item = self.devices_table.item(row, self.COL.get("IPv6", -1))
                    mask_item = self.devices_table.item(row, self.COL.get("IPv6 Mask", -1))
                    try:
                        ip_addr = ipaddress.IPv6Address(ipv6_item.text().strip()) if ipv6_item and ipv6_item.text().strip() else None
                        mask = int(mask_item.text().strip()) if mask_item and mask_item.text().strip() else None
                        if ip_addr and mask is not None:
                            network = ipaddress.IPv6Network(f"{ip_addr}/{mask}", strict=False)
                            if gateway_ip not in network:
                                return False
                    except (ipaddress.AddressValueError, ValueError):
                        return True
                return True

            if header_name == "MAC Address":
                if not value:
                    return True
                import re
                return bool(re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", value))

            if header_name == "Status":
                return False

            return True
        except Exception as exc:
            logging.error(f"[validate_cell_value] Error validating {header_name}: {exc}")
            return False

    def mark_device_for_apply(self, device_id):
        """Mark device as needing reapply after inline edits."""
        try:
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("device_id") == device_id:
                        device["_needs_apply"] = True
                        device["_is_new"] = False
                        self.update_device_name_indicator(device_id, device.get("Device Name", ""))
                        return
        except Exception as exc:
            logging.error(f"[mark_device_for_apply] Error: {exc}")

    def update_device_name_indicator(self, device_id, device_name):
        """Add an asterisk to indicate pending apply."""
        try:
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if name_item and name_item.data(Qt.UserRole) == device_id:
                    if not device_name.endswith(" *"):
                        name_item.setText(f"{device_name} *")
                        name_item.setForeground(QColor(255, 140, 0))
                    return
        except Exception as exc:
            logging.error(f"[update_device_name_indicator] Error: {exc}")

    def highlight_edited_cell(self, row, column):
        """Temporarily highlight edited cells."""
        try:
            item = self.devices_table.item(row, column)
            if not item:
                return
            item.setBackground(QColor(200, 255, 200))
            QTimer.singleShot(2000, lambda: self.remove_cell_highlight(row, column))
        except Exception as exc:
            logging.error(f"[highlight_edited_cell] Error: {exc}")

    def remove_cell_highlight(self, row, column):
        """Clear temporary highlight."""
        try:
            item = self.devices_table.item(row, column)
            if item:
                item.setBackground(QColor(255, 255, 255))
        except Exception as exc:
            logging.error(f"[remove_cell_highlight] Error: {exc}")

    
    def refresh_arp_selected_device(self):
        """Refresh ARP status from the database for the selected device(s)."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
            return

        selected_rows = {item.row() for item in selected_items}
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
            return

        for row in selected_rows:
            try:
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if not name_item:
                    continue
                device_name = name_item.text()
                device_info = self._find_device_by_name(device_name)
                if not device_info:
                    continue

                arp_results = self._check_individual_arp_resolution(device_info)
                self.set_status_icon_with_individual_ips(row, arp_results)
                overall_resolved = arp_results.get("overall_resolved", False)
                overall_status = arp_results.get("overall_status", "Unknown")
                device_status = device_info.get("Status", "Unknown")
                self.set_status_icon(row, resolved=overall_resolved, status_text=overall_status, device_status=device_status)
                print(f"[ARP REFRESH] {device_name}: {overall_status}")
            except Exception as exc:
                print(f"[ARP REFRESH] Error for {device_name}: {exc}")

    def send_immediate_arp_request(self, device_info, server_url):
        """Compatibility shim - ARP operations are handled by the server-side monitor."""
        return True, "ARP handled by server monitor"

    def send_arp_request(self, device_info):
        """Compatibility shim - ARP operations are handled by the server-side monitor."""
        return True, "ARP handled by server monitor"

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
                    # Handle protocols - convert string/list to array if needed
                    "protocols": self._convert_protocols_to_array(
                        device_info.get("protocols") or device_info.get("Protocols", "")
                    ),
                    "protocol_data": device_info.get("protocol_data", {}),
                    "bgp_config": device_info.get("bgp_config", {}),
                    "ospf_config": device_info.get("ospf_config", {}),
                    "isis_config": device_info.get("isis_config", {}) or device_info.get("is_is_config", {}),
                    "dhcp_config": device_info.get("dhcp_config", {}),
                    "dhcp_mode": device_info.get("dhcp_mode", ""),
                    "vxlan_config": device_info.get("vxlan_config", {}),
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
            print(f"[DEBUG APPLY DEVICE] ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
            
            # If device has an ID, fetch complete device data from database
            if device_id:
                try:
                    import requests
                    print(f"[DEBUG APPLY DEVICE] Fetching complete device data from database for {device_name}")
                    response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
                    if response.status_code == 200:
                        db_device_data = response.json()
                        print(f"[DEBUG APPLY DEVICE] Database device data keys: {list(db_device_data.keys())}")
                        
                        db_protocols = self._convert_protocols_to_array(db_device_data.get("protocols", []))
                        existing_protocols = self._convert_protocols_to_array(device_info.get("protocols", []))
                        protocols_list = existing_protocols or db_protocols

                        if not device_info.get("bgp_config"):
                            device_info["bgp_config"] = db_device_data.get("bgp_config", {})
                        if not device_info.get("ospf_config"):
                            device_info["ospf_config"] = db_device_data.get("ospf_config", {})
                        if not device_info.get("isis_config"):
                            device_info["isis_config"] = db_device_data.get("isis_config", {}) or db_device_data.get("is_is_config", {})
                        if not device_info.get("vxlan_config"):
                            device_info["vxlan_config"] = db_device_data.get("vxlan_config", {})

                        existing_dhcp_config = self._normalize_dhcp_config(device_info.get("dhcp_config"))
                        db_dhcp_config = self._normalize_dhcp_config(db_device_data.get("dhcp_config"))
                        device_info["dhcp_config"] = self._merge_dhcp_configs(
                            db_dhcp_config, existing_dhcp_config
                        )

                        device_info["dhcp_mode"] = (device_info.get("dhcp_mode") or db_device_data.get("dhcp_mode") or "").lower()

                        if device_info.get("dhcp_config") and "DHCP" not in protocols_list:
                            protocols_list.append("DHCP")
                        if device_info.get("vxlan_config") and "VXLAN" not in protocols_list:
                            protocols_list.append("VXLAN")
                        device_info["protocols"] = protocols_list
                        
                        print(f"[DEBUG APPLY DEVICE] Updated device info - Protocols: {device_info.get('protocols', [])}")
                        print(f"[DEBUG APPLY DEVICE] Updated device info - BGP config: {device_info.get('bgp_config', {})}")
                        print(f"[DEBUG APPLY DEVICE] Updated device info - OSPF config: {device_info.get('ospf_config', {})}")
                        print(f"[DEBUG APPLY DEVICE] Updated device info - ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
                    else:
                        print(f"[DEBUG APPLY DEVICE] Failed to fetch device data from database: {response.status_code}")
                except Exception as e:
                    print(f"[DEBUG APPLY DEVICE] Error fetching device data from database: {e}")
            
            # Prepare payload for background worker
            # Get ISIS config - handle both isis_config and is_is_config keys
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            protocols_list = self._convert_protocols_to_array(device_info.get("protocols", []))
            if device_info.get("dhcp_config") and "DHCP" not in protocols_list:
                protocols_list.append("DHCP")
            device_info["protocols"] = protocols_list
            device_info["dhcp_mode"] = (device_info.get("dhcp_mode") or "").lower()

            dhcp_config = self._normalize_dhcp_config(device_info.get("dhcp_config"))
            if dhcp_config:
                vlan_value = str(device_info.get("VLAN", "0") or "0")
                if vlan_value != "0":
                    dhcp_config["interface"] = f"vlan{vlan_value}"
                else:
                    dhcp_config["interface"] = iface_norm
                dhcp_config["mode"] = (dhcp_config.get("mode") or device_info.get("dhcp_mode") or "").lower()
                device_info["dhcp_config"] = dhcp_config
                device_info["dhcp_mode"] = dhcp_config.get("mode", "")
            else:
                device_info["dhcp_config"] = {}

            vxlan_config = self._with_vxlan_interfaces(
                device_info.get("vxlan_config"),
                iface_label,
                device_info.get("VLAN", "0"),
            )
            if vxlan_config:
                device_info["vxlan_config"] = vxlan_config
                if "VXLAN" not in protocols_list:
                    protocols_list.append("VXLAN")
            else:
                device_info["vxlan_config"] = {}
            
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
                "loopback_ipv4": device_info.get("Loopback IPv4", ""),
                "loopback_ipv6": device_info.get("Loopback IPv6", ""),
                "protocols": self._convert_protocols_to_array(protocols_list),
                "bgp_config": device_info.get("bgp_config", {}),
                "ospf_config": device_info.get("ospf_config", {}),
                "isis_config": isis_config,
                "dhcp_config": device_info.get("dhcp_config", {}),
                "dhcp_mode": device_info.get("dhcp_mode", ""),
                "protocol_data": device_info.get("protocol_data", {}),
                "vxlan_config": device_info.get("vxlan_config", {}),
            }
            
            print(f"[DEBUG APPLY DEVICE] Payload protocols: {payload['protocols']}")
            print(f"[DEBUG APPLY DEVICE] Payload BGP config: {payload['bgp_config']}")
            print(f"[DEBUG APPLY DEVICE] Payload OSPF config: {payload['ospf_config']}")
            print(f"[DEBUG APPLY DEVICE] Payload ISIS config: {payload['isis_config']}")
            
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
            print(f"[DEBUG DEVICE APPLY] ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
            
            # Step 1: Apply basic device configuration (interface, IP addresses, routes)
            # Get ISIS config - handle both isis_config and is_is_config keys
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            protocols_list = self._convert_protocols_to_array(device_info.get("protocols", []))
            if device_info.get("dhcp_config") and "DHCP" not in protocols_list:
                protocols_list.append("DHCP")
            device_info["protocols"] = protocols_list
            device_info["dhcp_mode"] = (device_info.get("dhcp_mode") or "").lower()

            dhcp_config = self._normalize_dhcp_config(device_info.get("dhcp_config"))
            if dhcp_config:
                vlan_value = str(device_info.get("VLAN", "0") or "0")
                if vlan_value != "0":
                    dhcp_config["interface"] = f"vlan{vlan_value}"
                else:
                    dhcp_config["interface"] = iface_norm
                dhcp_config["mode"] = (dhcp_config.get("mode") or device_info.get("dhcp_mode") or "").lower()
                device_info["dhcp_config"] = dhcp_config
                device_info["dhcp_mode"] = dhcp_config.get("mode", "")
            else:
                device_info["dhcp_config"] = {}

            vxlan_config = self._with_vxlan_interfaces(
                device_info.get("vxlan_config"),
                iface_label,
                device_info.get("VLAN", "0"),
            )
            if vxlan_config:
                device_info["vxlan_config"] = vxlan_config
                if "VXLAN" not in protocols_list:
                    protocols_list.append("VXLAN")
            else:
                device_info["vxlan_config"] = {}
            
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
                "protocols": self._convert_protocols_to_array(protocols_list),
                "bgp_config": device_info.get("bgp_config", {}),
                "ospf_config": device_info.get("ospf_config", {}),
                "isis_config": isis_config,
                "dhcp_config": device_info.get("dhcp_config", {}),
                "dhcp_mode": device_info.get("dhcp_mode", ""),
                "protocol_data": device_info.get("protocol_data", {}),
                "vxlan_config": device_info.get("vxlan_config", {}),
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
            
            # Step 4: Configure ISIS if enabled
            # Get ISIS config - handle both isis_config and is_is_config keys
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            
            print(f"[DEBUG DEVICE APPLY] Checking ISIS - protocols: {protocols}, isis_config: {isis_config}")
            if "IS-IS" in protocols and isis_config:
                print(f"[INFO] Configuring ISIS for device {device_name}")
                isis_success = self._apply_isis_to_server_sync(server_url, device_info)
                if not isis_success:
                    print(f"[ERROR] Failed to configure ISIS for device {device_name}")
                    return False
                print(f"[SUCCESS] ISIS configured for device {device_name}")
            else:
                print(f"[DEBUG DEVICE APPLY] ISIS not configured - protocols: {protocols}, isis_config: {isis_config}")
            
            return True
                
        except Exception as e:
            print(f"[ERROR] Exception in sync device apply for '{device_name}': {e}")
            return False
    
    def _apply_bgp_to_server_sync(self, server_url, device_info):
        """Apply BGP configuration synchronously (for use in background workers)."""
        return self.bgp_handler._apply_bgp_to_server_sync(server_url, device_info)
    def _apply_ospf_to_server_sync(self, server_url, device_info):
        """Apply OSPF configuration synchronously (for use in background workers)."""
        return self.ospf_handler._apply_ospf_to_server_sync(server_url, device_info)
    def _apply_isis_to_server_sync(self, server_url, device_info):
        """Apply ISIS configuration synchronously (for use in background workers)."""
        return self.isis_handler._apply_isis_to_server_sync(server_url, device_info)
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

    def _remove_device_from_server(self, device_info, device_id, device_name):
        """Invoke server APIs to clean up a removed device."""
        try:
            print(f"[DEBUG REMOVE SERVER] Removing device '{device_name}' from server")
            
            server_url = self.get_server_url(silent=True)
            if not server_url:
                print("[DEBUG REMOVE SERVER] No server URL available")
                return

            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            vlan = device_info.get("VLAN", "0")
            ipv4 = device_info.get("IPv4", "")
            ipv6 = device_info.get("IPv6", "")

            cleanup_payload = {
                "interface": iface_norm,
                "vlan": vlan,
                "cleanup_only": True,
                "device_specific": True,
                "device_id": device_id,
                "device_name": device_name,
            }
            print(f"[DEBUG REMOVE SERVER] Calling cleanup API with payload: {cleanup_payload}")
            cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
            if cleanup_resp.status_code == 200:
                removed_ips = cleanup_resp.json().get("removed_ips", [])
                print(f"[DEBUG REMOVE SERVER] Successfully cleaned up IPs: {removed_ips}")
            else:
                print(f"[DEBUG REMOVE SERVER] Cleanup failed: {cleanup_resp.status_code} - {cleanup_resp.text}")

            protocols = device_info.get("protocols", [])
            if isinstance(protocols, dict):
                protocol_list = list(protocols.keys())
            elif isinstance(protocols, list):
                protocol_list = protocols
            else:
                protocol_list = []

            remove_payload = {
                "device_id": device_id,
                "device_name": device_name,
                "interface": iface_norm,
                "vlan": vlan,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "protocols": protocol_list,
            }
            print(f"[DEBUG REMOVE SERVER] Calling remove API with payload: {remove_payload}")
            remove_resp = requests.post(f"{server_url}/api/device/remove", json=remove_payload, timeout=10)
            if remove_resp.status_code == 200:
                print(f"[DEBUG REMOVE SERVER] Successfully removed device '{device_name}' from server")
            else:
                print(f"[DEBUG REMOVE SERVER] Remove API failed: {remove_resp.status_code} - {remove_resp.text}")

        except Exception as exc:
            print(f"[ERROR] Failed to remove device '{device_name}' from server: {exc}")

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
            vlan, ipv4_gateway, ipv6_gateway, incr_mac, incr_ipv4, incr_ipv6, incr_gateway, incr_vlan, incr_vxlan, incr_count, ospf_config, bgp_config, 
            dhcp_config, ipv4_octet_index, ipv6_hextet_index, mac_byte_index, gateway_octet_index, incr_dhcp_pool, dhcp_pool_octet_index,
            incr_loopback, loopback_ipv4_octet_index, loopback_ipv6_hextet_index, loopback_ipv4, loopback_ipv6, isis_config,
            vxlan_vni_increment_index, vxlan_local_octet_index, vxlan_remote_octet_index, vxlan_udp_increment_index
        ) = dialog.get_values()
        vxlan_config = dialog.get_vxlan_config()
        print(f"[DEBUG ADD DEVICE] VXLAN config from dialog: {vxlan_config}")

        ipv4_mask = ipv4_mask or "24"
        ipv6_mask = ipv6_mask or "64"
        normalized_vxlan_config = self._normalize_vxlan_config(vxlan_config)
        print(f"[DEBUG ADD DEVICE] Normalized VXLAN config: {normalized_vxlan_config}")

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
        
        if incr_count > 1 and (incr_mac or incr_ipv4 or incr_ipv6 or incr_gateway or incr_vlan or incr_loopback or incr_vxlan):
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
                    "protocols": [],
                }

                # Always include VXLAN config if it exists (even if incomplete)
                # This ensures VXLAN config is preserved when user enables VXLAN in UI
                if normalized_vxlan_config:
                    # Increment VXLAN fields if enabled
                    per_device_vxlan = normalized_vxlan_config.copy()
                    print(f"[DEBUG ADD DEVICE] Processing VXLAN config for device {i+1}/{incr_count}: {per_device_vxlan}")
                    
                    # Increment VNI if enabled
                    if incr_vxlan and i > 0 and per_device_vxlan.get("vni"):
                        vni_increment_steps = [1, 10, 100, 1000]  # Maps to +1, +10, +100, +1000
                        increment_step = vni_increment_steps[vxlan_vni_increment_index] if (0 <= vxlan_vni_increment_index < len(vni_increment_steps)) else 1
                        per_device_vxlan["vni"] = per_device_vxlan["vni"] + (i * increment_step)
                    
                    # Increment Local Endpoint if enabled
                    if incr_vxlan and i > 0 and per_device_vxlan.get("local_ip"):
                        try:
                            local_ip = per_device_vxlan["local_ip"]
                            incremented_local = self._increment_ipv4(local_ip, i, vxlan_local_octet_index)
                            per_device_vxlan["local_ip"] = incremented_local
                        except (ValueError, AttributeError):
                            pass  # Keep as-is if invalid
                    
                    # Increment Remote Endpoints if enabled
                    if incr_vxlan and i > 0 and per_device_vxlan.get("remote_peers"):
                        incremented_remote_peers = []
                        for remote_ip in per_device_vxlan["remote_peers"]:
                            try:
                                incremented_remote = self._increment_ipv4(remote_ip, i, vxlan_remote_octet_index)
                                incremented_remote_peers.append(incremented_remote)
                            except (ValueError, AttributeError):
                                # If not a valid IP or can't parse, keep as-is
                                incremented_remote_peers.append(remote_ip)
                        per_device_vxlan["remote_peers"] = incremented_remote_peers
                    
                    # Increment UDP Port if enabled
                    if incr_vxlan and i > 0 and per_device_vxlan.get("udp_port"):
                        udp_increment_steps = [1, 10, 100]  # Maps to +1, +10, +100
                        increment_step = udp_increment_steps[vxlan_udp_increment_index] if (0 <= vxlan_udp_increment_index < len(udp_increment_steps)) else 1
                        per_device_vxlan["udp_port"] = per_device_vxlan["udp_port"] + (i * increment_step)
                    
                    per_device_vxlan = self._with_vxlan_interfaces(
                        per_device_vxlan,
                        iface,
                        current_vlan,
                    )
                    device_data["vxlan_config"] = per_device_vxlan
                    device_data["VXLAN"] = self._format_vxlan_summary(per_device_vxlan)
                    if "VXLAN" not in device_data["protocols"]:
                        device_data["protocols"].append("VXLAN")
                    print(f"[DEBUG ADD DEVICE] Added VXLAN config to device {current_name}: {per_device_vxlan}")
                else:
                    # Ensure vxlan_config is always present (even if empty) for consistency
                    device_data["vxlan_config"] = {}
                    print(f"[DEBUG ADD DEVICE] No VXLAN config for device {current_name} - normalized_vxlan_config: {normalized_vxlan_config}")
                
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
                        "mode": bgp_config.get("mode") or bgp_config.get("bgp_mode", "eBGP"),
                        "bgp_keepalive": bgp_config.get("bgp_keepalive", "30"),
                        "bgp_hold_time": bgp_config.get("bgp_hold_time", "90"),
                        "ipv4_enabled": bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False)),
                        "ipv6_enabled": bgp_config.get("ipv6_enabled", False)
                    }
                    
                    # Preserve use_loopback_ip and bgp_remote_loopback_ip from dialog config
                    use_loopback_ip = bgp_config.get("use_loopback_ip", False)
                    bgp_remote_loopback_ip = bgp_config.get("bgp_remote_loopback_ip", "")
                    bgp_remote_loopback_ipv6 = bgp_config.get("bgp_remote_loopback_ipv6", "")
                    if use_loopback_ip:
                        bgp_protocol_config["use_loopback_ip"] = True
                        bgp_protocol_config["bgp_remote_loopback_ip"] = bgp_remote_loopback_ip
                        bgp_protocol_config["bgp_remote_loopback_ipv6"] = bgp_remote_loopback_ipv6
                    
                    # Add IPv4 BGP configuration if enabled (support both old and new formats)
                    ipv4_enabled = bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False))
                    if ipv4_enabled:
                        # Determine neighbor IP and update-source based on use_loopback_ip
                        if use_loopback_ip and bgp_remote_loopback_ip:
                            # Use remote loopback IP as neighbor when use_loopback_ip is checked
                            neighbor_ipv4 = bgp_remote_loopback_ip
                        else:
                            # Default: use incremented gateway
                            neighbor_ipv4 = current_ipv4_gateway
                        
                        # Determine update-source based on use_loopback_ip
                        if use_loopback_ip and current_loopback_ipv4:
                            # Use loopback IP as update-source when use_loopback_ip is checked
                            update_source_ipv4 = current_loopback_ipv4
                        else:
                            # Default: use incremented device IP
                            update_source_ipv4 = current_ipv4
                        
                        bgp_protocol_config["bgp_neighbor_ipv4"] = neighbor_ipv4
                        bgp_protocol_config["bgp_update_source_ipv4"] = update_source_ipv4
                        bgp_protocol_config["protocol"] = "ipv4"
                        print(f"[DEBUG ADD DEVICE] IPv4 BGP configured for device {current_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}, use_loopback_ip={use_loopback_ip}")
                    
                    # Add IPv6 BGP configuration if enabled
                    if bgp_config.get("ipv6_enabled", False):
                        # Determine neighbor IP and update-source based on use_loopback_ip
                        if use_loopback_ip and bgp_remote_loopback_ipv6:
                            # Use remote loopback IPv6 as neighbor when use_loopback_ip is checked
                            neighbor_ipv6 = bgp_remote_loopback_ipv6
                        else:
                            # Default: use incremented gateway
                            neighbor_ipv6 = current_ipv6_gateway
                        
                        # Determine update-source based on use_loopback_ip
                        if use_loopback_ip and current_loopback_ipv6:
                            # Use loopback IPv6 as update-source when use_loopback_ip is checked
                            update_source_ipv6 = current_loopback_ipv6
                        else:
                            # Default: use incremented device IPv6
                            update_source_ipv6 = current_ipv6
                        
                        bgp_protocol_config["bgp_neighbor_ipv6"] = neighbor_ipv6
                        bgp_protocol_config["bgp_update_source_ipv6"] = update_source_ipv6
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
                
                if dhcp_config:
                    print(f"[DEBUG ADD DEVICE] DHCP config for device {i+1}: {dhcp_config}")
                    per_device_dhcp = copy.deepcopy(dhcp_config)
                    dhcp_mode_value = (per_device_dhcp.get("mode") or "client").lower()
                    per_device_dhcp["mode"] = dhcp_mode_value
                    if current_vlan and current_vlan != "0":
                        per_device_dhcp["interface"] = f"vlan{current_vlan}"
                    elif iface_name:
                        per_device_dhcp["interface"] = iface_name

                    device_data["protocols"] = device_data.get("protocols", [])
                    if "DHCP" not in device_data["protocols"]:
                        device_data["protocols"].append("DHCP")

                    device_data["dhcp_config"] = per_device_dhcp
                    device_data["dhcp_mode"] = dhcp_mode_value
                    device_data["dhcp_state"] = "Pending"
                    device_data["dhcp_running"] = False
                    device_data["dhcp_lease_ip"] = ""
                    device_data["dhcp_lease_mask"] = ""
                    device_data["dhcp_lease_gateway"] = ""
                    device_data["dhcp_lease_server"] = ""
                    device_data["dhcp_lease_expires"] = ""
                    device_data["dhcp_lease_subnet"] = ""
                    device_data["last_dhcp_check"] = ""

                protocols_list = device_data.get("protocols", [])
                if protocols_list:
                    unique_protocols = list(dict.fromkeys(protocols_list))
                    device_data["protocols"] = unique_protocols
                    device_data["Protocols"] = ", ".join(unique_protocols)
                else:
                    device_data["Protocols"] = ""
                
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
                "protocols": [],
            }

            # Always include VXLAN config if it exists (even if incomplete)
            # This ensures VXLAN config is preserved when user enables VXLAN in UI
            if normalized_vxlan_config:
                per_device_vxlan = self._with_vxlan_interfaces(
                    normalized_vxlan_config,
                    iface,
                    vlan,
                )
                device_data["vxlan_config"] = per_device_vxlan
                device_data["VXLAN"] = self._format_vxlan_summary(per_device_vxlan)
                if "VXLAN" not in device_data["protocols"]:
                    device_data["protocols"].append("VXLAN")
                print(f"[DEBUG ADD DEVICE] Added VXLAN config to single device {unique_name}: {per_device_vxlan}")
            else:
                # Ensure vxlan_config is always present (even if empty) for consistency
                device_data["vxlan_config"] = {}
                print(f"[DEBUG ADD DEVICE] No VXLAN config for single device {unique_name} - normalized_vxlan_config: {normalized_vxlan_config}")
            
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
                    "mode": bgp_config.get("mode") or bgp_config.get("bgp_mode", "eBGP"),
                    "bgp_keepalive": bgp_config.get("bgp_keepalive", "30"),
                    "bgp_hold_time": bgp_config.get("bgp_hold_time", "90"),
                    "ipv4_enabled": bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False)),
                    "ipv6_enabled": bgp_config.get("ipv6_enabled", False)
                }
                
                # Preserve use_loopback_ip and bgp_remote_loopback_ip from dialog config
                use_loopback_ip = bgp_config.get("use_loopback_ip", False)
                bgp_remote_loopback_ip = bgp_config.get("bgp_remote_loopback_ip", "")
                bgp_remote_loopback_ipv6 = bgp_config.get("bgp_remote_loopback_ipv6", "")
                if use_loopback_ip:
                    bgp_protocol_config["use_loopback_ip"] = True
                    bgp_protocol_config["bgp_remote_loopback_ip"] = bgp_remote_loopback_ip
                    bgp_protocol_config["bgp_remote_loopback_ipv6"] = bgp_remote_loopback_ipv6
                
                # Add IPv4 BGP configuration if enabled (support both old and new formats)
                ipv4_enabled = bgp_config.get("ipv4_enabled", bgp_config.get("enabled", False))
                if ipv4_enabled:
                    # Determine neighbor IP and update-source based on use_loopback_ip
                    if use_loopback_ip and bgp_remote_loopback_ip:
                        # Use remote loopback IP as neighbor when use_loopback_ip is checked
                        neighbor_ipv4 = bgp_remote_loopback_ip
                    else:
                        # Default: use current gateway
                        neighbor_ipv4 = ipv4_gateway
                    
                    # Determine update-source based on use_loopback_ip
                    loopback_ipv4 = device_data.get("Loopback IPv4", "")
                    if use_loopback_ip and loopback_ipv4:
                        # Use loopback IP as update-source when use_loopback_ip is checked
                        update_source_ipv4 = loopback_ipv4
                    else:
                        # Default: use current device IP
                        update_source_ipv4 = ipv4
                    
                    bgp_protocol_config["bgp_neighbor_ipv4"] = neighbor_ipv4
                    bgp_protocol_config["bgp_update_source_ipv4"] = update_source_ipv4
                    bgp_protocol_config["protocol"] = "ipv4"
                    print(f"[DEBUG ADD DEVICE] IPv4 BGP configured for single device {unique_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}, use_loopback_ip={use_loopback_ip}")
                
                # Add IPv6 BGP configuration if enabled
                if bgp_config.get("ipv6_enabled", False):
                    # Determine neighbor IP and update-source based on use_loopback_ip
                    if use_loopback_ip and bgp_remote_loopback_ipv6:
                        # Use remote loopback IPv6 as neighbor when use_loopback_ip is checked
                        neighbor_ipv6 = bgp_remote_loopback_ipv6
                    else:
                        # Default: use current gateway
                        neighbor_ipv6 = ipv6_gateway
                    
                    # Determine update-source based on use_loopback_ip
                    loopback_ipv6 = device_data.get("Loopback IPv6", "")
                    if use_loopback_ip and loopback_ipv6:
                        # Use loopback IPv6 as update-source when use_loopback_ip is checked
                        update_source_ipv6 = loopback_ipv6
                    else:
                        # Default: use current device IPv6
                        update_source_ipv6 = ipv6
                    
                    bgp_protocol_config["bgp_neighbor_ipv6"] = neighbor_ipv6
                    bgp_protocol_config["bgp_update_source_ipv6"] = update_source_ipv6
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
                print(f"[DEBUG ADD DEVICE] BGP added to single device {unique_name}: {device_data['bgp_config']}")
            else:
                print(f"[DEBUG ADD DEVICE] BGP NOT enabled for single device - bgp_config: {bgp_config}")
            
            if dhcp_config:
                print(f"[DEBUG ADD DEVICE] DHCP config for single device: {dhcp_config}")
                per_device_dhcp = copy.deepcopy(dhcp_config)
                dhcp_mode_value = (per_device_dhcp.get("mode") or "client").lower()
                per_device_dhcp["mode"] = dhcp_mode_value
                if vlan and vlan != "0":
                    per_device_dhcp["interface"] = f"vlan{vlan}"
                elif iface_name:
                    per_device_dhcp["interface"] = iface_name

                device_data["protocols"] = device_data.get("protocols", [])
                if "DHCP" not in device_data["protocols"]:
                    device_data["protocols"].append("DHCP")

                device_data["dhcp_config"] = per_device_dhcp
                device_data["dhcp_mode"] = dhcp_mode_value
                device_data["dhcp_state"] = "Pending"
                device_data["dhcp_running"] = False
                device_data["dhcp_lease_ip"] = ""
                device_data["dhcp_lease_mask"] = ""
                device_data["dhcp_lease_gateway"] = ""
                device_data["dhcp_lease_server"] = ""
                device_data["dhcp_lease_expires"] = ""
                device_data["dhcp_lease_subnet"] = ""
                device_data["last_dhcp_check"] = ""

            protocols_list = device_data.get("protocols", [])
            if protocols_list:
                unique_protocols = list(dict.fromkeys(protocols_list))
                device_data["protocols"] = unique_protocols
                device_data["Protocols"] = ", ".join(unique_protocols)
            else:
                device_data["Protocols"] = ""
            
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
        
        # Save session immediately after adding device(s) so they persist even if client is closed before apply
        if hasattr(self.main_window, "save_session"):
            print(f"[DEBUG ADD] Saving session after adding {len(devices_to_create)} device(s)")
            self.main_window.save_session()

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
                "_needs_apply": True,
                "protocols": [],
            }

            vxlan_copy = self._normalize_vxlan_config(copied_device.get("vxlan_config"))
            if vxlan_copy:
                vxlan_copy["underlay_interface"] = self._normalize_iface_label(target_interface)
                new_device["vxlan_config"] = vxlan_copy
                new_device["VXLAN"] = self._format_vxlan_summary(vxlan_copy)
                new_device["protocols"].append("VXLAN")

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
        
        # Save session immediately after pasting device(s) so they persist even if client is closed before apply
        if hasattr(self.main_window, "save_session"):
            print(f"[DEBUG PASTE] Saving session after pasting {len(pasted_devices)} device(s)")
            self.main_window.save_session()

        if len(pasted_devices) == 1:
            QMessageBox.information(self, "Device Pasted", 
                                   f"Device '{pasted_devices[0]}' has been pasted to {target_interface}.\n\n"
                                   f"Click 'Apply' to configure on server and save to session.")
        else:
            QMessageBox.information(self, "Devices Pasted", 
                                   f"{len(pasted_devices)} devices have been pasted to {target_interface}:\n"
                                   f"{', '.join(pasted_devices)}\n\n"
                                   f"Click 'Apply' to configure on server and save to session.")

    def get_device_info_by_name(self, device_name):
        """Get device info by name (wrapper around _find_device_by_name)."""
        return self._find_device_by_name(device_name)

    def prompt_edit_device(self):
        """Open AddDeviceDialog with pre-filled values to edit an existing device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to edit.")
            return

        row = selected_items[0].row()
        name_item = self.devices_table.item(row, self.COL["Device Name"])
        if not name_item:
            QMessageBox.warning(self, "Error", "Could not find device name in table.")
            return

        device_name = name_item.text()
        device_info = self.get_device_info_by_name(device_name)
        
        if not device_info:
            QMessageBox.warning(self, "Device Not Found", 
                              f"Could not find device '{device_name}' in data structure.")
            return

        # Extract device information
        iface = device_info.get("Interface", "")
        mac = device_info.get("MAC Address", "")
        vlan = device_info.get("VLAN", "0")
        ipv4 = device_info.get("IPv4", "")
        ipv6 = device_info.get("IPv6", "")
        ipv4_mask = device_info.get("ipv4_mask", "24")
        ipv6_mask = device_info.get("ipv6_mask", "64")
        ipv4_gateway = device_info.get("IPv4 Gateway", device_info.get("Gateway", ""))
        ipv6_gateway = device_info.get("IPv6 Gateway", "")
        loopback_ipv4 = device_info.get("Loopback IPv4", "")
        loopback_ipv6 = device_info.get("Loopback IPv6", "")

        dialog = AddDeviceDialog(self, default_iface=iface)

        # Pre-fill basics
        dialog.device_name_input.setText(device_name)
        dialog.iface_input.setText(iface)
        dialog.mac_input.setText(mac)
        dialog.vlan_input.setText(vlan)
        dialog.ipv4_input.setText(ipv4)
        dialog.ipv6_input.setText(ipv6)
        dialog.ipv4_mask_input.setText(ipv4_mask)
        dialog.ipv6_mask_input.setText(ipv6_mask)
        dialog.ipv4_gateway_input.setText(ipv4_gateway)
        dialog.ipv6_gateway_input.setText(ipv6_gateway)
        dialog.loopback_ipv4_input.setText(loopback_ipv4)
        dialog.loopback_ipv6_input.setText(loopback_ipv6)
        dialog.set_vxlan_values(device_info.get("vxlan_config"))

        # Set checkboxes based on whether fields have values
        dialog.ipv4_checkbox.setChecked(bool(ipv4.strip()))
        dialog.ipv6_checkbox.setChecked(bool(ipv6.strip()))

        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get updated values from dialog
        (
            new_name, iface, mac, ipv4, ipv6, ipv4_mask, ipv6_mask,
            vlan, ipv4_gateway, ipv6_gateway, inc_mac, inc_ipv4, inc_ipv6, inc_gateway, inc_vlan, count, 
            ospf_config, bgp_config, dhcp_config, ipv4_octet_index, ipv6_hextet_index, mac_byte_index, 
            gateway_octet_index, incr_dhcp_pool, dhcp_pool_octet_index, incr_loopback, loopback_ipv4_octet_index, 
            loopback_ipv6_hextet_index, loopback_ipv4, loopback_ipv6, isis_config
        ) = dialog.get_values()
        new_vxlan_config = dialog.get_vxlan_config()

        ipv4_mask = ipv4_mask or "24"
        ipv6_mask = ipv6_mask or "64"

        # Check if IP addresses or VLAN changed - if so, mark for cleanup
        old_ipv4 = device_info.get("IPv4", "")
        old_ipv6 = device_info.get("IPv6", "")
        old_vlan = device_info.get("VLAN", "0")
        old_interface = device_info.get("Interface", "")
        
        ip_addresses_changed = (
            old_ipv4 != ipv4 or 
            old_ipv6 != ipv6 or 
            old_vlan != vlan
        )
        
        if ip_addresses_changed:
            device_info["_needs_cleanup"] = True
            device_info["_old_config"] = {
                "vlan": old_vlan,
                "interface": old_interface,
                "ipv4": old_ipv4,
                "ipv6": old_ipv6
            }

        # Update device in data structure
        device_info.update({
            "Device Name": new_name or device_name,
            "Interface": iface,
            "MAC Address": mac,
            "IPv4": ipv4,
            "IPv6": ipv6,
            "VLAN": vlan,
            "Gateway": ipv4_gateway,  # Use IPv4 gateway as primary gateway
            "IPv4 Gateway": ipv4_gateway,
            "IPv6 Gateway": ipv6_gateway,
            "ipv4_mask": ipv4_mask,
            "ipv6_mask": ipv6_mask,
            "Loopback IPv4": loopback_ipv4 if loopback_ipv4 else "",
            "Loopback IPv6": loopback_ipv6 if loopback_ipv6 else "",
            "_needs_apply": True  # Mark for server update
        })
        
        # Update protocol configs if provided (but don't overwrite existing if not provided)
        if ospf_config:
            device_info["ospf_config"] = ospf_config
            if "OSPF" not in device_info.get("protocols", []):
                device_info.setdefault("protocols", []).append("OSPF")
        
        if bgp_config:
            device_info["bgp_config"] = bgp_config
            if "BGP" not in device_info.get("protocols", []):
                device_info.setdefault("protocols", []).append("BGP")
        
        if dhcp_config:
            device_info["dhcp_config"] = dhcp_config
            device_info["dhcp_mode"] = (dhcp_config.get("mode") or "client").lower()
            if "DHCP" not in device_info.get("protocols", []):
                device_info.setdefault("protocols", []).append("DHCP")
        
        if isis_config:
            device_info["isis_config"] = isis_config
            device_info["is_is_config"] = isis_config
            if "IS-IS" not in device_info.get("protocols", []):
                device_info.setdefault("protocols", []).append("IS-IS")

        normalized_edit_vxlan = self._with_vxlan_interfaces(
            new_vxlan_config,
            iface,
            vlan,
        )
        existing_protocols = self._convert_protocols_to_array(device_info.get("protocols", []))
        device_info["protocols"] = existing_protocols
        if normalized_edit_vxlan:
            device_info["vxlan_config"] = normalized_edit_vxlan
            device_info["VXLAN"] = self._format_vxlan_summary(normalized_edit_vxlan)
            if "VXLAN" not in existing_protocols:
                existing_protocols.append("VXLAN")
        else:
            device_info.pop("vxlan_config", None)
            device_info["VXLAN"] = ""
            device_info["protocols"] = [p for p in existing_protocols if p != "VXLAN"]

        # Update table display
        self.devices_table.item(row, self.COL["Device Name"]).setText(new_name or device_name)
        self.devices_table.item(row, self.COL["MAC Address"]).setText(mac)
        
        # Update IPv4 with mask
        ipv4_item = self.devices_table.item(row, self.COL["IPv4"])
        if ipv4_item:
            ipv4_item.setText(ipv4)
            ipv4_item.setData(Qt.UserRole + 1, ipv4_mask)
        
        # Update IPv6 with mask  
        ipv6_item = self.devices_table.item(row, self.COL["IPv6"])
        if ipv6_item:
            ipv6_item.setText(ipv6)
            ipv6_item.setData(Qt.UserRole + 1, ipv6_mask)
        
        # Update gateways
        gateway_item = self.devices_table.item(row, self.COL["IPv4 Gateway"])
        if gateway_item:
            gateway_item.setText(ipv4_gateway)
        gateway_item = self.devices_table.item(row, self.COL["IPv6 Gateway"])
        if gateway_item:
            gateway_item.setText(ipv6_gateway)
        
        # Update mask columns
        mask_item = self.devices_table.item(row, self.COL["IPv4 Mask"])
        if mask_item:
            mask_item.setText(ipv4_mask)
        mask_item = self.devices_table.item(row, self.COL["IPv6 Mask"])
        if mask_item:
            mask_item.setText(ipv6_mask)
        
        # Update VLAN column
        vlan_item = self.devices_table.item(row, self.COL["VLAN"])
        if vlan_item:
            vlan_item.setText(vlan)
        
        # Update Loopback IP columns
        loopback_item = self.devices_table.item(row, self.COL["Loopback IPv4"])
        if loopback_item:
            loopback_item.setText(loopback_ipv4 if loopback_ipv4 else "")
        loopback_item = self.devices_table.item(row, self.COL["Loopback IPv6"])
        if loopback_item:
            loopback_item.setText(loopback_ipv6 if loopback_ipv6 else "")

        vxlan_item = self.devices_table.item(row, self.COL.get("VXLAN"))
        if vxlan_item:
            vxlan_item.setText(device_info.get("VXLAN", ""))
        
        # Refresh protocol tables if needed
        if hasattr(self, 'bgp_handler') and self.bgp_handler:
            self.bgp_handler.refresh_bgp_table()
        if hasattr(self, 'ospf_handler') and self.ospf_handler:
            self.ospf_handler.refresh_ospf_table()
        if hasattr(self, 'isis_handler') and self.isis_handler:
            self.isis_handler.refresh_isis_table()

        QMessageBox.information(self, "Device Updated", 
                               f"Device '{new_name or device_name}' updated locally.\n\n"
                               f"Click 'Apply' to update on server and save to session.")

    def copy_selected_device(self):
        """Copy the selected device(s) so they can be pasted to another interface."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to copy.")
            return

        selected_rows = sorted({item.row() for item in selected_items})
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select a device to copy.")
            return
        
        copied_devices = []
        device_names = []
        
        for row in selected_rows:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            if not name_item:
                continue
            device_name = name_item.text()
            device_info = self.get_device_info_by_name(device_name)
            if not device_info:
                QMessageBox.warning(self, "Device Not Found", f"Could not find device '{device_name}' in data structure.")
                return

            copied_devices.append(
                {
                    "Device Name": device_info.get("Device Name", ""),
                    "MAC Address": device_info.get("MAC Address", ""),
                    "IPv4": device_info.get("IPv4", ""),
                    "IPv6": device_info.get("IPv6", ""),
                    "ipv4_mask": device_info.get("ipv4_mask", "24"),
                    "ipv6_mask": device_info.get("ipv6_mask", "64"),
                    "VLAN": device_info.get("VLAN", "0"),
                    "Interface": device_info.get("Interface", ""),
                    "vxlan_config": device_info.get("vxlan_config", {}),
                }
            )
            device_names.append(device_name)
        
        self.main_window.copied_device = copied_devices
        
        if len(device_names) == 1:
            QMessageBox.information(
                self,
                "Device Copied",
                f"Device '{device_names[0]}' has been copied.\n\nSelect a port and use 'Paste Device' to create a copy.",
            )
        else:
            QMessageBox.information(
                self,
                "Devices Copied",
                f"{len(device_names)} devices have been copied:\n"
                f"{', '.join(device_names)}\n\nSelect a port and use 'Paste Device' to create copies.",
            )

    def _normalize_dhcp_config(self, dhcp_config):
        """Ensure DHCP config is a dict with normalized keys and types."""
        if not dhcp_config:
            return {}

        config = dhcp_config

        if isinstance(config, str):
            try:
                config = json.loads(config)
            except Exception:
                return {}

        if not isinstance(config, dict):
            return {}

        normalized = {}
        for key, value in config.items():
            normalized[str(key)] = value

        for numeric_key in ("lease_time", "lease", "lease-time"):
            if numeric_key in normalized:
                try:
                    normalized["lease_time"] = int(normalized.pop(numeric_key))
                except Exception:
                    normalized["lease_time"] = normalized.get(numeric_key)
                break

        def _coerce_bool(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
            return bool(value)

        mode = normalized.get("mode")
        if mode:
            normalized["mode"] = str(mode).lower()

        route_key = "gateway_route_normalized" if "gateway_route_normalized" in normalized else "gateway_route"
        if route_key in normalized and isinstance(normalized[route_key], str):
            normalized[route_key] = [r.strip() for r in normalized[route_key].split(",") if r.strip()]

        for bool_key in ("ipv4_enabled", "ipv6_enabled"):
            if bool_key in normalized:
                normalized[bool_key] = _coerce_bool(normalized[bool_key])

        if "ipv6_lease_time" in normalized:
            try:
                normalized["ipv6_lease_time"] = int(normalized["ipv6_lease_time"])
            except Exception:
                pass

        return normalized

    def _normalize_vxlan_config(self, vxlan_config):
        """Normalize VXLAN configuration payloads."""
        if not vxlan_config:
            return {}
        try:
            config = copy.deepcopy(vxlan_config)
        except Exception:
            config = dict(vxlan_config)

        # Preserve enabled flag if present
        if "enabled" in config:
            config["enabled"] = bool(config["enabled"])

        vni = config.get("vni") or config.get("VNI")
        try:
            config["vni"] = int(vni) if vni is not None else None
        except (TypeError, ValueError):
            config["vni"] = None

        remote = config.get("remote_peers") or config.get("remote_endpoints") or []
        if isinstance(remote, str):
            remote_peers = [
                token.strip()
                for token in remote.replace(";", ",").split(",")
                if token.strip()
            ]
        else:
            remote_peers = [
                str(token).strip()
                for token in (remote or [])
                if str(token).strip()
            ]
        if remote_peers:
            config["remote_peers"] = remote_peers
        else:
            config.pop("remote_peers", None)

        udp_port = config.get("udp_port")
        if udp_port is not None:
            try:
                config["udp_port"] = int(udp_port)
            except (TypeError, ValueError):
                config.pop("udp_port", None)

        # Preserve local_ip if present
        local_ip = config.get("local_ip")
        if local_ip:
            config["local_ip"] = str(local_ip).strip()

        # Preserve vlan_id if present
        vlan_id = config.get("vlan_id") or config.get("vxlan_vlan_id")
        if vlan_id is not None:
            try:
                config["vlan_id"] = int(vlan_id)
            except (TypeError, ValueError):
                pass  # Keep as-is if invalid

        underlay_iface = config.get("underlay_interface") or config.get("interface")
        if underlay_iface:
            config["underlay_interface"] = underlay_iface
        
        # If config has enabled=True or any meaningful content, return it
        # This ensures VXLAN config is preserved even if incomplete
        # CRITICAL: If enabled=True, always preserve the config (user explicitly enabled VXLAN)
        if config.get("enabled") is True:
            return config
        # Otherwise, only return if there's meaningful content
        if config.get("vni") or config.get("local_ip") or config.get("remote_peers") or config.get("vlan_id"):
            return config
        return {}

    def _format_vxlan_summary(self, vxlan_config):
        config = self._normalize_vxlan_config(vxlan_config)
        if not config or not config.get("vni"):
            return ""
        remote_peers = config.get("remote_peers", [])
        if not remote_peers:
            return f"VNI {config['vni']}"
        preview = ", ".join(remote_peers[:2])
        if len(remote_peers) > 2:
            preview = f"{preview}, +{len(remote_peers) - 2}"
        return f"VNI {config['vni']} -> {preview}"

    def _with_vxlan_interfaces(self, vxlan_config, iface_label, vlan_value):
        config = self._normalize_vxlan_config(vxlan_config)
        if not config:
            return {}
        iface_norm = self._normalize_iface_label(iface_label)
        vlan_str = str(vlan_value or "0")
        overlay_iface = iface_norm
        if vlan_str and vlan_str != "0":
            overlay_iface = f"vlan{vlan_str}"
        config["underlay_interface"] = iface_norm
        config["overlay_interface"] = overlay_iface
        return config

    def _merge_gateway_routes(self, base_routes, override_routes):
        """Merge gateway_route values preserving uniqueness."""
        merged = []
        seen = set()

        for source in (base_routes, override_routes):
            if not source:
                continue
            if isinstance(source, str):
                source_iter = [source]
            elif isinstance(source, (list, tuple, set)):
                source_iter = source
            else:
                source_iter = [str(source)]

            for route in source_iter:
                route_str = str(route).strip()
                if route_str and route_str not in seen:
                    seen.add(route_str)
                    merged.append(route_str)
        return merged

    def _merge_additional_pool_lists(self, base_list, override_list):
        """Merge additional_pools lists without dropping server-provided entries."""
        merged = []
        seen = set()

        def _pool_identity(pool_entry):
            if not isinstance(pool_entry, dict):
                return str(pool_entry)
            name = pool_entry.get("pool_name")
            if name:
                return f"name:{name}"
            start = pool_entry.get("pool_start")
            end = pool_entry.get("pool_end")
            return f"range:{start}-{end}"

        for source in (base_list, override_list):
            if not source:
                continue
            if isinstance(source, str):
                try:
                    source = json.loads(source)
                except Exception:
                    source = []
            if not isinstance(source, list):
                continue
            for entry in source:
                if not isinstance(entry, dict):
                    continue
                identity = _pool_identity(entry)
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(entry)
        return merged

    def _merge_dhcp_configs(self, db_config: dict, existing_config: dict) -> dict:
        """Merge DHCP configs while preserving server-provided arrays."""
        if not db_config and not existing_config:
            return {}
        if not db_config:
            return copy.deepcopy(existing_config) if existing_config else {}
        if not existing_config:
            return copy.deepcopy(db_config)

        merged = copy.deepcopy(db_config)

        for key, value in existing_config.items():
            if key == "additional_pools":
                merged["additional_pools"] = self._merge_additional_pool_lists(
                    merged.get("additional_pools"), value
                )
            elif key == "pool_names":
                existing_pool_names = value
                if isinstance(existing_pool_names, str):
                    try:
                        existing_pool_names = json.loads(existing_pool_names)
                    except Exception:
                        existing_pool_names = {}
                if not isinstance(existing_pool_names, dict):
                    existing_pool_names = {}

                merged_pool_names = merged.get("pool_names", {})
                if isinstance(merged_pool_names, str):
                    try:
                        merged_pool_names = json.loads(merged_pool_names)
                    except Exception:
                        merged_pool_names = {}
                if not isinstance(merged_pool_names, dict):
                    merged_pool_names = {}

                primary = existing_pool_names.get("primary") or merged_pool_names.get("primary")
                additional_merged = []
                seen_additional = set()

                for source in (
                    merged_pool_names.get("additional"),
                    existing_pool_names.get("additional"),
                ):
                    if not source:
                        continue
                    if isinstance(source, str):
                        source_iter = [source]
                    elif isinstance(source, (list, tuple, set)):
                        source_iter = source
                    else:
                        source_iter = [str(source)]
                    for name in source_iter:
                        name_str = str(name).strip()
                        if (
                            name_str
                            and name_str != primary
                            and name_str not in seen_additional
                        ):
                            seen_additional.add(name_str)
                            additional_merged.append(name_str)

                merged_pool_names = {
                    "primary": primary,
                    "additional": additional_merged,
                }
                merged["pool_names"] = merged_pool_names
            elif key == "gateway_route":
                merged["gateway_route"] = self._merge_gateway_routes(
                    merged.get("gateway_route"), value
                )
            else:
                merged[key] = value

        return merged

    def update_device_table(self, all_devices=None):
        """Rebuild the device table based on selected interfaces."""
        if all_devices is None:
            all_devices = getattr(self.main_window, "all_devices", {})

        self.devices_table.setRowCount(0)

        try:
            selected_interfaces = set()
            tree = getattr(self.main_window, "server_tree", None)
            if tree:
                for item in tree.selectedItems():
                    parent = item.parent()
                    if parent:
                        tg_id = parent.text(0).strip()
                        port_name = item.text(0).replace("• ", "").strip()
                        selected_interfaces.add(f"{tg_id} - {port_name}")

            interfaces_to_show = selected_interfaces or list(all_devices.keys())
            for iface in interfaces_to_show:
                devices = all_devices.get(iface)
                if not devices:
                    legacy_iface = iface.replace(" - ", " - Port: • ")
                    devices = all_devices.get(legacy_iface, [])
                if not isinstance(devices, list):
                    continue

                for device in devices:
                    row = self.devices_table.rowCount()
                    self.devices_table.insertRow(row)

                    for header in self.device_headers:
                        if header == "IPv4 Mask":
                            value = device.get("ipv4_mask", "24")
                        elif header == "IPv6 Mask":
                            value = device.get("ipv6_mask", "64")
                        elif header == "Loopback IPv4":
                            value = device.get("Loopback IPv4", "")
                        elif header == "Loopback IPv6":
                            value = device.get("Loopback IPv6", "")
                        elif header == "VXLAN":
                            value = self._format_vxlan_summary(
                                device.get("vxlan_config") or device.get("VXLAN")
                            )
                        else:
                            value = device.get(header, "")

                        if header == "Status":
                            item = QTableWidgetItem("")
                            item.setFlags(Qt.ItemIsEnabled)
                        else:
                            item = QTableWidgetItem(str(value))
                            if header == "VXLAN":
                                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)

                        if header == "IPv4":
                            item.setData(Qt.UserRole + 1, device.get("ipv4_mask", "24"))
                        elif header == "IPv6":
                            item.setData(Qt.UserRole + 1, device.get("ipv6_mask", "64"))

                        if header == "Device Name" and device.get("device_id"):
                            item.setData(Qt.UserRole, device["device_id"])

                        item.setData(Qt.UserRole + 2, str(value))
                        self.devices_table.setItem(row, self.COL[header], item)

                    status_value = device.get("Status", "Stopped")
                    resolved = status_value == "Running"
                    tooltip = "Device Running" if resolved else "Device Stopped"
                    self.set_status_icon(row, resolved=resolved, status_text=tooltip, device_status=status_value)

        except Exception as exc:
            logging.error(f"[DEVICE TABLE] Failed to rebuild table: {exc}")

        self._initialize_arp_status_from_database()

    def _initialize_arp_status_from_database(self):
        """Initialize ARP status icons using database values for running devices."""
        try:
            running_devices = []
            for devices in getattr(self.main_window, "all_devices", {}).values():
                if not isinstance(devices, list):
                    continue
                for device in devices:
                    if device.get("Status") == "Running":
                        running_devices.append(device)

            if not running_devices:
                return

            for device in running_devices:
                device_name = device.get("Device Name")
                if not device_name:
                    continue

                arp_results = self._check_individual_arp_resolution(device)

                target_row = None
                for row in range(self.devices_table.rowCount()):
                    name_item = self.devices_table.item(row, self.COL["Device Name"])
                    if name_item and name_item.text() == device_name:
                        target_row = row
                        break

                if target_row is None:
                    continue

                overall_resolved = arp_results.get("overall_resolved", False)
                overall_status = arp_results.get("overall_status", "Unknown")
                self.set_status_icon(target_row, resolved=overall_resolved, status_text=overall_status, device_status=device.get("Status", "Running"))

        except Exception as exc:
            logging.debug(f"[ARP INIT] Skipped ARP initialization: {exc}")

    def _on_individual_arp_result(self, row, arp_results, operation_id=None):
        """Hook for ARP worker to update per-IP colors."""
        try:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            device_name = name_item.text() if name_item else "Unknown"
            print(f"[DEBUG ARP RESULT] Processing ARP result for row {row}, device: {device_name}, operation_id: {operation_id}")

            if hasattr(self, "_pending_arp_rows") and self._pending_arp_rows:
                if row not in self._pending_arp_rows:
                    print(f"[DEBUG ARP RESULT] Skipping row {row} ({device_name}) - not pending")
                    return

            if hasattr(self, "arp_operation_worker") and self.arp_operation_worker:
                current_id = getattr(self.arp_operation_worker, "operation_id", None)
                if operation_id and current_id and operation_id != current_id:
                    print(f"[DEBUG ARP RESULT] Skipping row {row} ({device_name}) - id mismatch {operation_id} != {current_id}")
                    return

            self.set_status_icon_with_individual_ips(row, arp_results)
        except Exception as exc:
            logging.error(f"[INDIVIDUAL ARP RESULT ERROR] Row {row}: {exc}")

    def _on_individual_arp_finished(self):
        """Cleanup after individual ARP worker completes."""
        if hasattr(self, "individual_arp_worker"):
            try:
                worker = self.individual_arp_worker
                delattr(self, "individual_arp_worker")
                if worker.isRunning():
                    worker.quit()
                    worker.wait(100)
                if not worker.isRunning():
                    worker.deleteLater()
            except RuntimeError:
                # Worker already deleted, ignore
                pass
            except Exception:
                pass
        self._arp_check_in_progress = False
        print("[ARP INDIVIDUAL] Individual ARP checks completed")

    def start_selected_devices(self):
        """Start selected devices in background using DeviceOperationWorker."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to start.")
            return

        selected_rows = sorted({item.row() for item in selected_items})
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to start.")
            return

        server_url = self.get_server_url()
        if not server_url:
            return

        devices_to_process = []
        for row in selected_rows:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            if not name_item:
                continue
            device_name = name_item.text()
            device_info = self.get_device_info_by_name(device_name)
            if not device_info:
                logging.warning(f"[DEVICE START] Device '{device_name}' not found in data model")
                continue
            devices_to_process.append((row, device_name, device_info))

        if not devices_to_process:
            QMessageBox.warning(self, "Error", "No valid devices found to start.")
            return

        self.operation_worker = DeviceOperationWorker("start", devices_to_process, server_url, self)
        self._current_operation_type = "start"
        self.operation_worker.progress.connect(self._on_device_operation_progress)
        self.operation_worker.device_status_updated.connect(self._on_device_status_updated)
        self.operation_worker.finished.connect(
            lambda results, succ, fail: self._on_device_operation_finished(results, succ, fail, selected_rows)
        )
        self.operation_worker.start()
        print(f"[DEVICE START] Starting {len(devices_to_process)} device(s) in background...")

    def stop_selected_devices(self):
        """Stop selected devices in background using DeviceOperationWorker."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to stop.")
            return

        selected_rows = sorted({item.row() for item in selected_items})
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to stop.")
            return

        server_url = self.get_server_url()
        if not server_url:
            return

        devices_to_process = []
        for row in selected_rows:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            if not name_item:
                continue
            device_name = name_item.text()
            device_info = self.get_device_info_by_name(device_name)
            if not device_info:
                logging.warning(f"[DEVICE STOP] Device '{device_name}' not found in data model")
                continue
            devices_to_process.append((row, device_name, device_info))

        if not devices_to_process:
            QMessageBox.warning(self, "Error", "No valid devices found to stop.")
            return

        self.operation_worker = DeviceOperationWorker("stop", devices_to_process, server_url, self)
        self._current_operation_type = "stop"
        self.operation_worker.progress.connect(self._on_device_operation_progress)
        self.operation_worker.device_status_updated.connect(self._on_device_status_updated)
        self.operation_worker.finished.connect(
            lambda results, succ, fail: self._on_device_operation_finished(results, succ, fail, selected_rows)
        )
        self.operation_worker.start()
        print(f"[DEVICE STOP] Stopping {len(devices_to_process)} device(s) in background...")

    def remove_selected_device(self):
        """Remove selected devices from the UI, data structures, and server."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Select one or more devices to remove.")
            return

        unique_rows = sorted({item.row() for item in selected_items}, reverse=True)
        confirm = QMessageBox.question(
            self,
            "Confirm Device Removal",
            "Are you sure you want to remove the selected device(s)?\n\n"
            "This will stop protocols, remove containers, and delete the devices from the UI.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        removed_devices = []
        for row in unique_rows:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            if not name_item:
                continue
            device_name = name_item.text()
            device_info = self.get_device_info_by_name(device_name)
            if not device_info:
                logging.warning(f"[REMOVE] Device '{device_name}' not found in data model")
                continue

            device_id = device_info.get("device_id")

            if hasattr(self, "bgp_handler") and self.bgp_handler:
                try:
                    self.bgp_handler._cleanup_bgp_table_for_device(device_id, device_name)
                except Exception as exc:
                    logging.debug(f"[REMOVE] BGP cleanup failed for {device_name}: {exc}")
            if hasattr(self, "ospf_handler") and self.ospf_handler:
                try:
                    self.ospf_handler._cleanup_ospf_table_for_device(device_id, device_name)
                except Exception as exc:
                    logging.debug(f"[REMOVE] OSPF cleanup failed for {device_name}: {exc}")
            if hasattr(self, "isis_handler") and self.isis_handler:
                try:
                    self.isis_handler._cleanup_isis_table_for_device(device_id, device_name)
                except Exception as exc:
                    logging.debug(f"[REMOVE] ISIS cleanup failed for {device_name}: {exc}")

            self.devices_table.removeRow(row)
            self._remove_device_from_data_structure(device_info)

            if hasattr(self.main_window, "removed_devices"):
                self.main_window.removed_devices.append(device_id)
            else:
                self.main_window.removed_devices = [device_id]

            self._remove_device_from_server(device_info, device_id, device_name)

            removed_devices.append(device_name)

        if removed_devices:
            if hasattr(self, "dhcp_handler") and self.dhcp_handler:
                QTimer.singleShot(200, self.dhcp_handler.refresh_dhcp_status)

            if hasattr(self.main_window, "save_session"):
                self.main_window.save_session()

            QMessageBox.information(
                self,
                "Device Removed",
                f"Removed {len(removed_devices)} device(s): {', '.join(removed_devices)}."
            )

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
        return self.bgp_handler._set_bgp_interim_stopping_state(device_name, selected_neighbors)
    def prompt_attach_route_pools(self):
        """Open dialog to attach route pools to selected BGP neighbors (Step 2: Attach to BGP)."""
        return self.bgp_handler.prompt_attach_route_pools()
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
                
                # Determine whether IPv6/Gateway were actually configured
                ipv6_value = (device_data.get("ipv6_address") or device_data.get("IPv6") or "").strip()
                ipv6_configured = bool(ipv6_value)
                gateway_value = (device_data.get("ipv4_gateway") or device_data.get("IPv4 Gateway") or "").strip()
                gateway_configured = bool(gateway_value)

                # Determine overall status - require only the components that exist
                overall_resolved = ipv4_resolved
                if ipv6_configured:
                    overall_resolved = overall_resolved and ipv6_resolved
                if gateway_configured:
                    overall_resolved = overall_resolved and gateway_resolved

                if overall_resolved:
                    return True, "ARP resolved"
                return False, arp_status or "ARP pending"
            else:
                print(f"[DEBUG ARP SYNC DATABASE] Failed to get device data: {response.status_code}")
                return False, "Database error"
        except Exception as e:
            print(f"[DEBUG ARP SYNC DATABASE] Error getting ARP status from database: {e}")
            return False, f"Database error: {str(e)}"

    def _check_individual_arp_resolution(self, device_info):
        """Check ARP resolution for individual IPs from database instead of direct server check."""
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            print("[ARP CHECK] Skipping ARP check - application is closing")
            return {"overall_resolved": False, "overall_status": "Application closing", 
                    "ipv4_resolved": False, "ipv6_resolved": False, "gateway_resolved": False}
        
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
                
                # Convert database values to boolean
                ipv4_resolved = bool(arp_ipv4_resolved)
                ipv6_resolved = bool(arp_ipv6_resolved)
                gateway_resolved = bool(arp_gateway_resolved)
                
                # Determine whether IPv6/Gateway were actually configured
                ipv6_value = (device_data.get("ipv6_address") or device_data.get("IPv6") or "").strip()
                ipv6_configured = bool(ipv6_value)
                gateway_value = (device_data.get("ipv4_gateway") or device_data.get("IPv4 Gateway") or "").strip()
                gateway_configured = bool(gateway_value)

                # Determine overall status - require only the components that exist
                overall_resolved = ipv4_resolved
                if ipv6_configured:
                    overall_resolved = overall_resolved and ipv6_resolved
                if gateway_configured:
                    overall_resolved = overall_resolved and gateway_resolved

                # Provide more descriptive status message when unresolved
                if overall_resolved:
                    status_message = "ARP resolved"
                else:
                    failed_parts = []
                    if not ipv4_resolved:
                        failed_parts.append("IPv4")
                    if ipv6_configured and not ipv6_resolved:
                        failed_parts.append("IPv6")
                    if gateway_configured and not gateway_resolved:
                        failed_parts.append("Gateway")
                    status_message = f"ARP pending: {', '.join(failed_parts) if failed_parts else 'Unknown'}"
                
                return {
                    "overall_resolved": overall_resolved,
                    "overall_status": status_message,
                    "ipv4_resolved": ipv4_resolved,
                    "ipv6_resolved": ipv6_resolved,
                    "gateway_resolved": gateway_resolved,
                    "needs_retry": False,
                }
            else:
                if response.status_code == 404:
                    return {
                        "overall_resolved": False,
                        "overall_status": "__RETRY__|Waiting for device status...",
                        "ipv4_resolved": False,
                        "ipv6_resolved": False,
                        "gateway_resolved": False,
                        "needs_retry": True,
                    }
                print(f"[DEBUG ARP DATABASE] Failed to get device data: {response.status_code}")
                return {
                    "overall_resolved": False,
                    "overall_status": "Database error",
                    "ipv4_resolved": False,
                    "ipv6_resolved": False,
                    "gateway_resolved": False,
                    "needs_retry": False,
                }
        except Exception as e:
            print(f"[DEBUG ARP DATABASE] Error getting ARP status from database: {e}")
            return {
                "overall_resolved": False,
                "overall_status": f"Database error: {str(e)}",
                "ipv4_resolved": False,
                "ipv6_resolved": False,
                "gateway_resolved": False,
                "needs_retry": False,
            }
    
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
        
        # Check if there's already a bulk ARP worker running
        if hasattr(self, 'bulk_arp_worker') and self.bulk_arp_worker:
            if self.bulk_arp_worker.isRunning():
                print("[ARP BULK] ARP check already running, skipping new request")
                return
            else:
                # Clean up finished worker - ensure thread is stopped first
                try:
                    worker = self.bulk_arp_worker
                    delattr(self, 'bulk_arp_worker')
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    if not worker.isRunning():
                        worker.deleteLater()
                except RuntimeError:
                    # Worker already deleted, ignore
                    pass
                except Exception:
                    pass
        
        # Create and start worker
        self.bulk_arp_worker = ArpCheckWorker(devices_data, self)
        self.bulk_arp_worker.arp_result.connect(self._on_bulk_arp_result)
        self.bulk_arp_worker.finished.connect(self._on_bulk_arp_finished)
        self.bulk_arp_worker.start()
        
        print(f"[ARP BULK] Started async ARP check for {len(devices_data)} devices")
    
    def _on_arp_check_result(self, row, resolved, status):
        """Handle ARP check result from worker thread."""
        try:
            if isinstance(status, str) and status.startswith("__RETRY__|"):
                message = status.split("|", 1)[1] if "|" in status else "Waiting for device status..."
                self._set_device_status_starting(row, status_text=message)
                self._schedule_arp_retry({row}, delay=2000)
                return
        except Exception as exc:
            logging.debug(f"[ARP RETRY] Failed to process single retry status for row {row}: {exc}")

        self.set_status_icon(row, resolved=resolved, status_text=status)
    
    def _on_bulk_arp_result(self, row, resolved, status):
        """Handle bulk ARP check result from worker thread."""
        try:
            if isinstance(status, str) and status.startswith("__RETRY__|"):
                message = status.split("|", 1)[1] if "|" in status else "Waiting for device status..."
                self._set_device_status_starting(row, status_text=message)
                self._schedule_arp_retry({row}, delay=2000)
                return
        except Exception as exc:
            logging.debug(f"[ARP RETRY] Failed to process retry status for row {row}: {exc}")

        # Update the status icon for this row
        self.set_status_icon(row, resolved=resolved, status_text=status)
    
    def _on_arp_check_finished(self):
        """Handle ARP check completion."""
        # Clean up worker reference - ensure thread is stopped first
        if hasattr(self, 'arp_check_worker'):
            try:
                worker = self.arp_check_worker
                delattr(self, 'arp_check_worker')
                if worker.isRunning():
                    worker.quit()
                    worker.wait(100)
                if not worker.isRunning():
                    worker.deleteLater()
            except RuntimeError:
                # Worker already deleted, ignore
                pass
            except Exception:
                pass

    def _set_device_status_starting(self, row: int, device_info: dict = None, status_text: str = "Starting device..."):
        """Update the status column to show an in-progress state while apply is running."""
        try:
            if device_info is None:
                device_name_item = self.devices_table.item(row, self.COL["Device Name"])
                if device_name_item:
                    device_name = device_name_item.text()
                    for iface, devices in self.main_window.all_devices.items():
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                device_info = device
                                break
                        if device_info:
                            break
            if isinstance(device_info, dict):
                device_info["Status"] = "Starting"
        except Exception as exc:
            logging.debug(f"[STATUS STARTING] Failed to update device info for row {row}: {exc}")

        try:
            display_text = status_text or "Starting..."
            self.set_status_icon(row, resolved=False, status_text=display_text, device_status="Starting")
        except Exception as exc:
            logging.debug(f"[STATUS STARTING] Failed to set status icon for row {row}: {exc}")

    def _schedule_arp_retry(self, rows, delay=2000):
        """Schedule a retry of ARP checks for the specified table rows."""
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            return
        
        if not rows:
            return

        rows = {row for row in rows if isinstance(row, int) and row >= 0}
        if not rows:
            return

        if not hasattr(self, "_arp_retry_rows"):
            self._arp_retry_rows = set()

        new_rows = rows - self._arp_retry_rows
        if not new_rows:
            return

        self._arp_retry_rows.update(new_rows)

        # Ensure pending ARP rows include the retry rows so we keep tracking them
        if not hasattr(self, "_pending_arp_rows") or self._pending_arp_rows is None:
            self._pending_arp_rows = set()
        self._pending_arp_rows.update(new_rows)

        def retry():
            # Check again if application is closing before retrying
            if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
                return
            try:
                devices_to_process = []
                for row in list(new_rows):
                    if row >= self.devices_table.rowCount():
                        continue
                    name_item = self.devices_table.item(row, self.COL["Device Name"])
                    if not name_item:
                        continue
                    device_info = self.get_device_info_by_name(name_item.text())
                    if device_info:
                        devices_to_process.append((row, device_info))

                if devices_to_process:
                    print(f"[ARP RETRY] Retrying ARP check for {len(devices_to_process)} device(s)")
                    self.check_arp_resolution_bulk_async(devices_to_process)
            finally:
                if hasattr(self, "_arp_retry_rows"):
                    self._arp_retry_rows.difference_update(new_rows)

        QTimer.singleShot(delay, retry)
    
    def _on_device_apply_result(self, operation_type, result_data):
        """Handle successful device apply result from background worker."""
        try:
            device_name = result_data.get("device_name", "Unknown")
            print(f"✅ Successfully applied device configuration for '{device_name}'")

            if hasattr(self, "dhcp_handler") and self.dhcp_handler:
                QTimer.singleShot(200, self.dhcp_handler.refresh_dhcp_status)
            
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
            # Clean up worker reference - ensure thread is stopped first
            if hasattr(self, 'db_worker'):
                try:
                    worker = self.db_worker
                    delattr(self, 'db_worker')
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    if not worker.isRunning():
                        worker.deleteLater()
                except RuntimeError:
                    # Worker already deleted, ignore
                    pass
                except Exception:
                    pass
        except Exception as e:
            print(f"[DEVICE APPLY FINISHED] Error cleaning up: {e}")
    def _on_multi_device_applied(self, device_name, success, message):
        """Handle individual device apply result from multi-device worker."""
        try:
            print(f"[MULTI DEVICE APPLY] {message}")
            
            # If device was successfully applied, trigger ARP status check
            if success:
                if hasattr(self, "dhcp_handler") and self.dhcp_handler:
                    QTimer.singleShot(200, self.dhcp_handler.refresh_dhcp_status)
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
                            response = requests.post(f"{server_url}/api/arp/monitor/force-check", timeout=5)
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
                print(f"[MULTI DEVICE APPLY] Saving session after successful device application ({successful_count} device(s) applied)")
                try:
                    self.main_window.save_session()
                    print(f"[MULTI DEVICE APPLY] ✅ Session saved successfully after applying {successful_count} device(s)")
                except Exception as save_exc:
                    print(f"[MULTI DEVICE APPLY] ⚠️ Failed to save session: {save_exc}")
            
            # Clean up worker reference - ensure thread is stopped first
            if hasattr(self, 'multi_device_apply_worker'):
                try:
                    worker = self.multi_device_apply_worker
                    delattr(self, 'multi_device_apply_worker')
                    if worker.isRunning():
                        worker.quit()
                        worker.wait(100)
                    if not worker.isRunning():
                        worker.deleteLater()
                except RuntimeError:
                    # Worker already deleted, ignore
                    pass
                except Exception:
                    pass
            
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
        # Clean up worker reference - ensure thread is stopped first
        if hasattr(self, 'bulk_arp_worker'):
            try:
                worker = self.bulk_arp_worker
                delattr(self, 'bulk_arp_worker')
                if worker.isRunning():
                    worker.quit()
                    worker.wait(100)
                if not worker.isRunning():
                    worker.deleteLater()
            except RuntimeError:
                # Worker already deleted, ignore
                pass
            except Exception:
                pass

    def cleanup_threads(self):
        """Clean up timers and worker threads before application exit."""
        print("[CLEANUP] Cleaning up all worker threads...")
        
        timer_attrs = [
            "status_timer",
            "bgp_monitoring_timer",
            "ospf_monitoring_timer",
            "isis_monitoring_timer",
            "device_status_timer",
        ]
        for attr in timer_attrs:
            timer = getattr(self, attr, None)
            if timer:
                print(f"[CLEANUP] Stopping {attr}...")
                try:
                    timer.stop()
                except Exception as exc:
                    print(f"[CLEANUP] Failed to stop {attr}: {exc}")

        def _stop_worker(attr_name):
            worker = getattr(self, attr_name, None)
            if not worker:
                return
            print(f"[CLEANUP] Stopping {attr_name}...")
            try:
                if hasattr(worker, "stop"):
                    worker.stop()
                if worker.isRunning():
                    worker.quit()
                    if not worker.wait(1000):
                        print(f"[CLEANUP] Force terminating {attr_name}...")
                        worker.terminate()
                        worker.wait(500)
                
                # Only deleteLater if thread is definitely stopped
                if not worker.isRunning():
                    worker.deleteLater()
                else:
                    print(f"[CLEANUP] WARNING: {attr_name} still running after cleanup attempt")
            except RuntimeError:
                # Worker already deleted, ignore
                pass
            except Exception as exc:
                print(f"[CLEANUP] Error stopping {attr_name}: {exc}")
            finally:
                try:
                    delattr(self, attr_name)
                except Exception:
                    pass

        worker_attrs = [
            "arp_check_worker",
            "bulk_arp_worker",
            "operation_worker",
            "arp_operation_worker",
            "individual_arp_worker",
            "multi_device_apply_worker",
            "multi_device_operation_worker",
        ]
        for attr in worker_attrs:
            _stop_worker(attr)

        # Also wait for protocol apply workers managed in handler lists
        def _drain_worker_list(list_attr_name):
            workers = getattr(self, list_attr_name, None)
            if not workers:
                return
            try:
                for w in list(workers):
                    try:
                        if hasattr(w, "isRunning") and w.isRunning():
                            print(f"[CLEANUP] Waiting for {list_attr_name} worker to finish...")
                            w.quit()  # Request thread to stop
                            if not w.wait(3000):
                                print(f"[CLEANUP] Force terminating {list_attr_name} worker...")
                                w.terminate()
                                w.wait(500)
                        
                        # Only deleteLater if thread is definitely stopped
                        if not w.isRunning():
                            w.deleteLater()
                    except RuntimeError:
                        # Worker might already be deleted, ignore
                        continue
                    except Exception as exc:
                        print(f"[CLEANUP] Error draining {list_attr_name} worker: {exc}")
                # Clear the list
                setattr(self, list_attr_name, [])
            except Exception:
                pass

        _drain_worker_list("_bgp_apply_workers")
        _drain_worker_list("_ospf_apply_workers")
        
        # Also check OSPF handler for workers
        if hasattr(self, "ospf_handler") and hasattr(self.ospf_handler, "_ospf_apply_workers"):
            try:
                ospf_workers = getattr(self.ospf_handler, "_ospf_apply_workers", [])
                for w in list(ospf_workers):
                    try:
                        if hasattr(w, "isRunning") and w.isRunning():
                            print("[CLEANUP] Waiting for OSPF handler worker to finish...")
                            w.quit()  # Request thread to stop
                            if not w.wait(3000):
                                print("[CLEANUP] Force terminating OSPF handler worker...")
                                w.terminate()
                                w.wait(500)
                        
                        # Only deleteLater if thread is definitely stopped
                        if not w.isRunning():
                            w.deleteLater()
                    except RuntimeError:
                        # Worker already deleted, ignore
                        pass
                    except Exception as exc:
                        print(f"[CLEANUP] Error cleaning up OSPF handler worker: {exc}")
                self.ospf_handler._ospf_apply_workers = []
            except Exception as exc:
                print(f"[CLEANUP] Error cleaning up OSPF handler workers: {exc}")
        
        if hasattr(self, "vxlan_handler"):
            try:
                self.vxlan_handler.stop_monitoring()
            except Exception as exc:
                print(f"[CLEANUP] Failed to stop VXLAN monitoring: {exc}")

    def _update_device_protocol(self, row_or_device_name, protocol, config):
        """Update device with protocol configuration.
        
        Args:
            row_or_device_name: Either a row index (int) from devices_table or a device_name (str)
            protocol: Protocol name (e.g., "OSPF", "BGP", "IS-IS")
            config: Protocol configuration dictionary
        """
        # Store protocol configuration in device data for protocol-specific tabs
        # Support both row index (int) and device_name (str)
        if isinstance(row_or_device_name, str):
            device_name = row_or_device_name
        else:
            # It's a row index from devices_table
            device_name_item = self.devices_table.item(row_or_device_name, self.COL["Device Name"])
            if device_name_item is None:
                # If row doesn't exist in devices_table, try to get device_name from protocol tables
                # This handles the case where we're editing from OSPF/BGP/ISIS tables
                QMessageBox.warning(self, "Error", f"Could not find device name for row {row_or_device_name}. Please try again.")
                return
            device_name = device_name_item.text()
        
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
                        # Merge with existing config to preserve fields not in the update
                        config_key = f"{protocol.lower().replace('-', '_')}_config"
                        # For ISIS, check both is_is_config and isis_config for backward compatibility
                        if protocol in ["IS-IS", "ISIS"]:
                            existing_config = device.get(config_key, {}) or device.get("isis_config", {})
                        else:
                            existing_config = device.get(config_key, {})
                        if existing_config:
                            # Merge: new values override existing, but preserve missing fields
                            merged_config = existing_config.copy()
                            merged_config.update(config)  # Update with new values
                            # For ISIS config, ensure ipv4_enabled and ipv6_enabled are preserved if not in update
                            if protocol in ["IS-IS", "ISIS"]:
                                if "ipv4_enabled" not in config and "ipv4_enabled" in existing_config:
                                    merged_config["ipv4_enabled"] = existing_config["ipv4_enabled"]
                                if "ipv6_enabled" not in config and "ipv6_enabled" in existing_config:
                                    merged_config["ipv6_enabled"] = existing_config["ipv6_enabled"]
                            # For OSPF config, ensure area_id_ipv4 and area_id_ipv6 are preserved if not in update
                            elif protocol == "OSPF":
                                # CRITICAL: Only preserve fields that are NOT being updated
                                # This ensures that when updating graceful_restart_ipv4, we don't overwrite graceful_restart_ipv6
                                if "area_id_ipv4" not in config and "area_id_ipv4" in existing_config:
                                    merged_config["area_id_ipv4"] = existing_config["area_id_ipv4"]
                                if "area_id_ipv6" not in config and "area_id_ipv6" in existing_config:
                                    merged_config["area_id_ipv6"] = existing_config["area_id_ipv6"]
                                # CRITICAL: Preserve graceful_restart_ipv4 and graceful_restart_ipv6 separately
                                # Only preserve if NOT being updated (not in config)
                                if "graceful_restart_ipv4" not in config and "graceful_restart_ipv4" in existing_config:
                                    merged_config["graceful_restart_ipv4"] = existing_config["graceful_restart_ipv4"]
                                if "graceful_restart_ipv6" not in config and "graceful_restart_ipv6" in existing_config:
                                    merged_config["graceful_restart_ipv6"] = existing_config["graceful_restart_ipv6"]
                                # CRITICAL: Also preserve generic graceful_restart if not being updated
                                # But only if address-family-specific flags are not present
                                if "graceful_restart" not in config and "graceful_restart" in existing_config:
                                    # Only preserve generic graceful_restart if address-family-specific flags are not being set
                                    if "graceful_restart_ipv4" not in config and "graceful_restart_ipv6" not in config:
                                        merged_config["graceful_restart"] = existing_config["graceful_restart"]
                                # CRITICAL: Preserve route_pools to prevent accidental removal when editing config
                                if "route_pools" not in config and "route_pools" in existing_config:
                                    merged_config["route_pools"] = existing_config["route_pools"]
                                # CRITICAL: Preserve P2P settings to prevent accidental removal when editing config
                                if "p2p_ipv4" not in config and "p2p_ipv4" in existing_config:
                                    merged_config["p2p_ipv4"] = existing_config["p2p_ipv4"]
                                if "p2p_ipv6" not in config and "p2p_ipv6" in existing_config:
                                    merged_config["p2p_ipv6"] = existing_config["p2p_ipv6"]
                                if "p2p" not in config and "p2p" in existing_config:
                                    merged_config["p2p"] = existing_config["p2p"]  # For backward compatibility
                            device[config_key] = merged_config
                            # For ISIS, also update isis_config for backward compatibility
                            if protocol in ["IS-IS", "ISIS"]:
                                device["isis_config"] = merged_config
                        else:
                            # No existing config, use new config as-is
                            device[config_key] = config
                            # For ISIS, also update isis_config for backward compatibility
                            if protocol in ["IS-IS", "ISIS"]:
                                device["isis_config"] = config
                    else:
                        # If protocols is a dict (old format), store config there
                        device["protocols"][protocol] = config
                    
                    # Debug logs disabled
                    break
        
        # Update the protocol-specific tables based on the protocol
        # Temporarily disconnect cellChanged signals to prevent infinite loops
        if protocol == "BGP":
            # Temporarily disconnect to prevent infinite loop
            self.bgp_table.cellChanged.disconnect()
            self.update_bgp_table()
            # Reconnect after update
            self.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)
        elif protocol == "OSPF":
            # Don't refresh the table immediately after editing - let the user finish
            # The table will refresh on the next periodic update or when Apply is clicked
            # This prevents overwriting user edits and ensures the edit is saved first
            # Temporarily disconnect to prevent infinite loop (but don't refresh yet)
            # Just reconnect to prevent issues
            pass
        elif protocol == "IS-IS" or protocol == "ISIS":
            # Don't refresh the table immediately after editing - let the user finish
            # The table will refresh on the next periodic update or when Apply is clicked
            # This prevents infinite loops and overwriting user edits
            # Skip table refresh for ISIS to prevent recursion
            pass
        
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
            target_byte = 5 - byte_index  # 0 -> last byte, 5 -> first byte
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
    def _convert_protocols_to_array(self, protocols):
        """Convert protocols string to array format for database storage."""
        if not protocols:
            return []
        
        if isinstance(protocols, list):
            return protocols
        
        if isinstance(protocols, dict):
            return list(sorted(set(protocols.keys())))
        
        if isinstance(protocols, str):
            # Split by comma and clean up
            return [p.strip() for p in protocols.split(",") if p.strip()]
        
        return []
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
        return self.bgp_handler.apply_bgp_configurations()
    def start_bgp_protocol(self):
        """Start BGP protocol for selected devices."""
        return self.bgp_handler.start_bgp_protocol()
    def stop_bgp_protocol(self):
        """Stop BGP protocol for selected devices."""
        return self.bgp_handler.stop_bgp_protocol()
    def apply_ospf_configurations(self):
        """Apply OSPF configurations to the server for selected OSPF table rows."""
        return self.ospf_handler.apply_ospf_configurations()
    def start_ospf_protocol(self):
        """Start OSPF protocol for selected devices."""
        return self.ospf_handler.start_ospf_protocol()
    def stop_ospf_protocol(self):
        """Stop OSPF protocol for selected devices."""
        return self.ospf_handler.stop_ospf_protocol()
    def start_isis_protocol(self):
        """Start IS-IS protocol for selected devices."""
        return self.isis_handler.start_isis_protocol()
    def stop_isis_protocol(self):
        """Stop IS-IS protocol for selected devices."""
        return self.isis_handler.stop_isis_protocol()
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
        return self.bgp_handler.start_bgp_monitoring()
    def stop_bgp_monitoring(self):
        """Stop periodic BGP status monitoring."""
        return self.bgp_handler.stop_bgp_monitoring()
    def setup_column_tooltips(self):
        """Set up header tooltips indicating whether columns are editable."""
        try:
            tooltips = {
                "Device Name": "Editable: Device name (1-50 characters)",
                "Status": "Read-only: Device lifecycle status",
                "IPv4": "Editable: IPv4 address (e.g., 192.168.0.2)",
                "IPv6": "Editable: IPv6 address (e.g., 2001:db8::1)",
                "VLAN": "Editable: VLAN ID (0-4094, 0 = untagged)",
                "IPv4 Gateway": "Editable: IPv4 gateway used for static routes",
                "IPv6 Gateway": "Editable: IPv6 gateway used for static routes",
                "IPv4 Mask": "Editable: IPv4 mask length (0-32)",
                "IPv6 Mask": "Editable: IPv6 mask length (0-128)",
                "MAC Address": "Editable: MAC address (XX:XX:XX:XX:XX:XX)",
                "Loopback IPv4": "Editable: IPv4 loopback address",
                "Loopback IPv6": "Editable: IPv6 loopback address",
                "VXLAN": "Read-only: VXLAN summary (VNI and remote peers)",
            }

            for header, tooltip in tooltips.items():
                col_index = self.COL.get(header)
                if col_index is None:
                    continue
                header_item = self.devices_table.horizontalHeaderItem(col_index)
                if header_item and tooltip:
                    header_item.setToolTip(tooltip)
        except Exception as e:
            logging.error(f"[setup_column_tooltips] Error: {e}")

    def start_ospf_monitoring(self):
        """Start periodic OSPF status monitoring."""
        return self.ospf_handler.start_ospf_monitoring()
    def stop_ospf_monitoring(self):
        """Stop periodic OSPF status monitoring."""
        return self.ospf_handler.stop_ospf_monitoring()
    def start_isis_monitoring(self):
        """Start periodic ISIS status monitoring."""
        return self.isis_handler.start_isis_monitoring()
    def stop_isis_monitoring(self):
        """Stop periodic ISIS status monitoring."""
        return self.isis_handler.stop_isis_monitoring()
    def periodic_isis_status_check(self):
        """Periodic ISIS status check - called by timer."""
        return self.isis_handler.periodic_isis_status_check()
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
        return self.bgp_handler.periodic_bgp_status_check()
    def periodic_ospf_status_check(self):
        """Periodic OSPF status check for all devices with OSPF configured."""
        return self.ospf_handler.periodic_ospf_status_check()
    def update_device_status_icon(self, row, arp_resolved, arp_status=""):
        """Update the device status icon based on ARP resolution."""
        try:
            # Get the status item in the Status column
            status_item = self.devices_table.item(row, self.COL["Status"])
            if not status_item:
                # Create a new item if it doesn't exist
                status_item = QTableWidgetItem()
                self.devices_table.setItem(row, self.COL["Status"], status_item)
            
            # Set icon and text based on ARP resolution
            if arp_resolved:
                status_item.setIcon(self.green_dot)
                status_item.setText("Running")
                status_item.setToolTip(f"Device running - ARP resolved: {arp_status}")
            else:
                status_item.setIcon(self.orange_dot)
                # Keep current text (might be "Starting..." or "Running")
                # Only update tooltip
                status_item.setToolTip(f"ARP failed: {arp_status}")
                
        except Exception as e:
            print(f"[DEVICE STATUS ICON] Error updating status icon for row {row}: {e}")
    
    def update_device_data_in_memory(self, device_id, header_name, new_value):
        """Update device data in the all_devices structure."""
        try:
            key_mapping = {
                "Device Name": "Device Name",
                "IPv4": "IPv4",
                "IPv6": "IPv6",
                "VLAN": "VLAN",
                "Gateway": "Gateway",
                "IPv4 Mask": "ipv4_mask",
                "IPv6 Mask": "ipv6_mask",
                "MAC Address": "MAC Address",
            }
            
            key = key_mapping.get(header_name)
            if not key:
                return

            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("device_id") == device_id:
                        device[key] = new_value
                        return
        except Exception as exc:
            logging.error(f"[update_device_data_in_memory] Error: {exc}")
    
    def mark_device_for_apply(self, device_id):
        """Mark a device as needing to be applied to the server."""
        try:
            for iface, devices in self.main_window.all_devices.items():
                for device in devices:
                    if device.get("device_id") == device_id:
                        device["_needs_apply"] = True
                        device["_is_new"] = False
                        self.update_device_name_indicator(device_id, device.get("Device Name", ""))
                        return
        except Exception as exc:
            logging.error(f"[mark_device_for_apply] Error: {exc}")
    
    def update_device_name_indicator(self, device_id, device_name):
        """Update the device name in the table to show it needs to be applied."""
        try:
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if name_item and name_item.data(Qt.UserRole) == device_id:
                    if not device_name.endswith(" *"):
                        name_item.setText(device_name + " *")
                        name_item.setForeground(QColor(255, 140, 0))
                    return
        except Exception as exc:
            logging.error(f"[update_device_name_indicator] Error: {exc}")
    
    def poll_device_status(self):
        """Periodic status poll invoked by status_timer."""
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return

            rows_to_refresh = []
            running_count = 0
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL.get("Device Name"))
                if not name_item:
                    continue
                device_name = name_item.text()
                device_info = self.get_device_info_by_name(device_name)
                if not device_info:
                    continue

                status = device_info.get("Status", "")
                if status == "Running":
                    running_count += 1
                    rows_to_refresh.append(row)
                elif status == "Starting":
                    rows_to_refresh.append(row)

            # Adjust polling cadence depending on activity
            if running_count == 0 and rows_to_refresh:
                if self.status_timer.interval() != 60000:
                    self.status_timer.setInterval(60000)
            else:
                if self.status_timer.interval() != 30000:
                    self.status_timer.setInterval(30000)

            if rows_to_refresh:
                self._refresh_device_table_from_database(rows_to_refresh)
        except Exception as exc:
            logging.debug(f"[DEVICE POLL] Error: {exc}")