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
# Audit MED #7: removed `from .unified_add_device_dialog import
# UnifiedAddDeviceDialog`. The class was imported here but never
# instantiated anywhere in the codebase, and its form contained
# placeholder labels like "(Use existing AddDeviceDialog form)" —
# a partial migration that was never finished. Keeping the file
# around as orphan code for now; remove later if no one revives it.
from .add_bgp_dialog import AddBgpDialog
from .add_ospf_dialog import AddOspfDialog
from .add_isis_dialog import AddIsisDialog
from .add_vxlan_dialog import AddVxlanDialog
from .add_bgp_route_dialog import ManageRoutePoolsDialog, AttachRoutePoolsDialog

logger = logging.getLogger(__name__)


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
                    # v0.5.215: also flip in-memory Status BEFORE the
                    # UI emit. `poll_device_status` (line ~10058)
                    # picks rows to refresh based on
                    # `device_info["Status"]` — if it still reads
                    # "Stopped" from the previous stop, the poll
                    # skips this row and the "Starting..." text
                    # gets stuck. Symptom operator reported on
                    # JNPR-MAC-HWXVX1 2026-08-23: after Start
                    # Selected Devices, all protocols came up but
                    # device row stayed on the yellow "Starting..."
                    # dot indefinitely. Only manual Refresh (which
                    # reads the DB) revealed the true state.
                    device_info["Status"] = "Starting"
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
                    # v0.5.215: mirror the start branch — flip
                    # in-memory Status BEFORE the UI emit so the
                    # poll picks up the row for its DB refresh.
                    device_info["Status"] = "Stopping"
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
                    # Mark device as applied. device_mutate_lock
                    # serializes this with any concurrent Edit / Remove
                    # on the same device dict in the main thread —
                    # audit MED #9.
                    lock = getattr(self.parent_tab, "device_mutate_lock", None)
                    if lock is not None:
                        with lock:
                            device_info["_is_new"] = False
                            device_info["_needs_apply"] = False
                            device_info["Status"] = "Running"
                    else:
                        device_info["_is_new"] = False
                        device_info["_needs_apply"] = False
                        device_info["Status"] = "Running"

                    # Protocol configuration is now handled in _apply_device_to_server_sync
                    # No need for duplicate calls here

                    # v0.5.197: server-returned non-fatal warnings
                    # (e.g. attached BGP route pool not defined on
                    # server). Fold them into the success message so
                    # the operator sees them in the Apply Results
                    # dialog without having to dig into logs.
                    warnings = device_info.get("_apply_warnings") or []
                    if warnings:
                        warn_lines = "\n  ".join(
                            w.get("message", str(w)) for w in warnings
                        )
                        message = (
                            f"⚠ {device_name}: Applied with warnings:\n  "
                            f"{warn_lines}"
                        )
                    else:
                        message = f"✅ {device_name}: Device applied successfully"
                    self.device_applied.emit(device_name, True, message)
                    return (message, True)
                else:
                    # Get error message from device_info if available
                    error_msg = device_info.get("_apply_error", "Unknown error")
                    message = f"❌ {device_name}: Failed to apply to server - {error_msg}"
                    self.device_applied.emit(device_name, False, message)
                    return (message, False)
                    
            except Exception as e:
                message = f"❌ {device_name}: Error - {str(e)}"
                self.device_applied.emit(device_name, False, message)
                logger.error(f"{device_name}: {e}")
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
                    logger.error(f"{device_name}: {e}")
        
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
                logger.info("Skipping server online check - application is closing")
                return
            
        results = []
        for server in server_data:
            if self._should_stop:
                break
            
            # Check again if application is closing
            if hasattr(self, 'parent_tab') and hasattr(self.parent_tab, 'main_window'):
                if hasattr(self.parent_tab.main_window, '_is_closing') and self.parent_tab.main_window._is_closing:
                    logger.info("Stopping server checks - application is closing")
                    break
                
            try:
                address = server.get("address")
                logger.info(f"Checking server online status: {address}")
                # Reduced timeout for server checks
                response = requests.get(f"{address}/api/interfaces", timeout=3)
                
                if response.status_code == 200:
                    server["online"] = True
                    server["interfaces"] = response.json()
                    results.append({"server": server, "success": True})
                    logger.info(f"✅ Server {address} is online")
                else:
                    server["online"] = False
                    error_msg = f"HTTP {response.status_code}"
                    results.append({"server": server, "success": False, "error": error_msg})
                    logger.error(f"❌ Server {address} check failed: {error_msg}")
                    
            except Exception as e:
                server["online"] = False
                error_msg = str(e)
                results.append({"server": server, "success": False, "error": error_msg})
                logger.error(f"❌ Server {address} check error: {error_msg}")
        
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

        # v0.3.11: live regex validators on the ASN fields. Previously
        # plain QLineEdit accepted anything; apply-time check at
        # devices_tab_bgp.py:1608 rejected with a generic warning
        # AFTER save attempt. Now: digit-only keystroke filter at
        # entry time (up to 10 digits — covers the full 4-byte ASN
        # range 1..2^32-1=4294967295). Apply-time range check
        # remains as the backstop for the "10 nines" edge case.
        # Avoids the Settings-dialog OverflowError trap by using
        # QRegExpValidator (no Qt int32 limit) instead of QIntValidator.
        from PyQt5.QtCore import QRegExp
        from PyQt5.QtGui import QRegExpValidator
        _asn_validator = QRegExpValidator(QRegExp(r"\d{1,10}"))
        _asn_tooltip = (
            "BGP AS number. Range 1..4,294,967,295 "
            "(RFC 6793 4-byte ASN)."
        )
        self.bgp_asn_input = QLineEdit("65000")
        self.bgp_asn_input.setValidator(_asn_validator)
        self.bgp_asn_input.setToolTip(_asn_tooltip)

        # IPv4 BGP fields
        self.bgp_neighbor_ipv4_input = QLineEdit("192.168.0.2")
        self.bgp_remote_asn_input = QLineEdit("65001")
        self.bgp_remote_asn_input.setValidator(_asn_validator)
        self.bgp_remote_asn_input.setToolTip(_asn_tooltip)
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

        # Audit MED #9: worker threads (MultiDeviceApplyWorker via a
        # ThreadPoolExecutor of 5) mutate device_info dicts that the
        # main thread also reads/writes (Edit dialog Save, Remove,
        # the on_cell_changed inline-edit handler). Python dict ops
        # are individually GIL-safe but cross-thread Edit-during-Apply
        # could clobber each other's writes (last-writer-wins with
        # surprising ordering). RLock both sides take when mutating
        # a device_info dict so updates are serialized. The lock
        # window is microseconds (3 dict assigns); negligible UI
        # impact while ensuring no torn state.
        self.device_mutate_lock = __import__("threading").RLock()

        # Caching layer for status results (5 second TTL)
        self.arp_cache = StatusCache(ttl_seconds=5)
        self.bgp_cache = StatusCache(ttl_seconds=10)

        # Periodic device-status / ARP-color poll. This was historically
        # DISABLED "to prevent QThread crashes" — but those crashes were
        # the QThread-destruction race fixed in v0.2.24/25 (global
        # keepalive) and the poll's HTTP is now off-thread + non-blocking
        # (_refresh_device_table_from_database runs async). Re-enabled so
        # ARP/gateway cells refresh on a passive view (e.g. a gateway
        # going orange when its ARP fails, and back when it resolves)
        # instead of only updating on a manual refresh. poll_device_status
        # self-adjusts the interval (30s active / 60s idle).
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.poll_device_status)
        self.status_timer.start(30000)  # async + QThread-safe (keepalive)

        # Dedicated timer/flag for lightweight periodic status refreshes triggered after ops
        self.device_status_timer = QTimer()
        self.device_status_timer.timeout.connect(self.poll_device_status)
        self.device_status_monitoring_active = False

        self.active_bgp_devices = set()
        self.all_devices = {}
        self.interface_to_device_map = {}
        self.selected_interfaces = set()
        self._arp_check_in_progress = False  # Flag to prevent multiple ARP checks

        # SSE worker — connects on first reload_devices_from_server.
        # Server-pushed events (state_transition, device_applied,
        # device_started/stopped/removed, stream_*) trigger a coalesced
        # full device-table refresh so cells reflect live state without
        # the old 30s poll loop.
        self._sse_worker = None
        self._sse_refresh_pending = False
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

        # Migrate existing interface keys to include TG ID prefix (if needed)
        self._migrate_interface_keys()
        
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
        # Tight chrome — table + action row read as one panel.
        # Matches the streams tab pattern from 9aa54b8.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Preflight bar (0.2.70). Self-contained widget — polls
        # /api/preflight/check on a slow timer and updates colour-coded
        # pills. Click Details to see every finding in a table modal.
        # Defensive: never raises on HTTP failure (silent debug log
        # only), so an unreachable server doesn't pop a modal every
        # minute. Lives ABOVE the filter row so the operator's eye
        # catches it before they scroll the table.
        try:
            from widgets.preflight_bar import PreflightBar
            def _resolve_url():
                try:
                    return self.get_server_url(silent=True) \
                        if hasattr(self, "get_server_url") else None
                except Exception:
                    return None
            self.preflight_bar = PreflightBar(_resolve_url, parent=self.devices_subtab)
            layout.addWidget(self.preflight_bar)
            # v0.2.78: subscribe to per-device breakdown so the Devices
            # table can paint a red/amber/green dot in front of each
            # device name. The bar already polls every 60s; we just
            # mirror its data into the table.
            self.preflight_bar.by_device_updated.connect(
                self._apply_preflight_dots
            )
        except Exception as _e:
            # Bar is purely advisory — failure to construct must NEVER
            # block the Devices tab from rendering. Logged for triage.
            import logging as _lg
            _lg.warning(f"[DEVICES] preflight bar unavailable: {_e}")

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
        # "Device List" label removed — the QTabWidget tab labeled
        # "Devices" already identifies the section; the extra label
        # was redundant chrome that ate vertical space.
        self.devices_table = QTableWidget(0, len(self.device_headers))
        self.devices_table.setHorizontalHeaderLabels(self.device_headers)
        # map header -> index
        self.COL = {h: i for i, h in enumerate(self.device_headers)}
        
        # Enable inline editing
        self.devices_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.devices_table.setSelectionBehavior(QTableWidget.SelectItems)

        # Plain default Qt table chrome — matches the BGP / OSPF /
        # IS-IS / DHCP / VXLAN tables (which never got the custom
        # alternating-rows + blue-selection stylesheet the Devices
        # table used to have). The custom styling was the lone
        # outlier across all six tabs and looked visually
        # inconsistent. Kept the inline-edit triggers and selection
        # behaviour above; just dropped the stylesheet and explicit
        # header sizing.


        # Connect cell change event for validation and updates
        self.devices_table.cellChanged.connect(self.on_cell_changed)
        
        # Set tooltips for editable columns
        self.setup_column_tooltips()

        # Filter input. Once a deployment grows past ~15 devices,
        # scrolling to find one becomes annoying. Hides rows whose
        # Device-Name / Interface / IPv4 / IPv6 / MAC don't contain
        # the (case-insensitive) substring entered. Empty → all rows
        # visible (at-rest state).
        #
        # v0.3.11: inlined onto the preflight bar's row instead of
        # taking its own row. Operators were reporting only one device
        # row visible — the combined preflight + filter chrome was
        # eating ~65 px of vertical space. Falls back to a standalone
        # row if the preflight bar failed to construct (defensive —
        # the filter must work even without preflight).
        from PyQt5.QtWidgets import QLineEdit
        self._device_filter_input = QLineEdit()
        self._device_filter_input.setPlaceholderText(
            "Filter: Device / Interface / IPv4 / IPv6 / MAC …"
        )
        self._device_filter_input.setClearButtonEnabled(True)
        self._device_filter_input.setFixedHeight(22)
        self._device_filter_input.setMaximumWidth(320)
        self._device_filter_input.setStyleSheet(
            "QLineEdit { border: 1px solid #cbd5e1; border-radius: 4px;"
            "  padding: 0 6px; font-size: 12px; background: #ffffff; }"
            "QLineEdit:focus { border-color: #2563eb; }"
        )
        self._device_filter_input.textChanged.connect(self._apply_device_filter)
        _inlined = False
        bar = getattr(self, "preflight_bar", None)
        if bar is not None and hasattr(bar, "add_inline_widget"):
            try:
                bar.add_inline_widget(self._device_filter_input, stretch=0)
                _inlined = True
            except Exception as _e:
                import logging as _lg
                _lg.warning(
                    f"[DEVICES] filter inline-attach failed; "
                    f"falling back to standalone row: {_e}"
                )
        if not _inlined:
            filter_row = QHBoxLayout()
            filter_row.setContentsMargins(0, 0, 0, 4)
            filter_row.setSpacing(6)
            filter_label = QLabel("Filter:")
            filter_label.setStyleSheet("color: #6b7280; font-size: 11px;")
            filter_row.addWidget(filter_label)
            filter_row.addWidget(self._device_filter_input, 1)
            layout.addLayout(filter_row)

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
        # Wrap the action row in a styled QFrame so it has the same
        # grey "footer" background as the streams + TGEN action rows
        # (commits d96c26c / 364c2a6). Unified BTN_BASE styling on
        # every button — white fill, thin gray border, neutral hover,
        # so the row reads as one family instead of a mix of stock
        # QPushButtons and inline-colored ones.
        from PyQt5.QtWidgets import QFrame
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "QFrame { background-color: #f3f4f6; "
            "border-top: 1px solid #e5e7eb; border-radius: 0; }"
        )
        btns = QHBoxLayout(action_bar)
        btns.setAlignment(Qt.AlignLeft)
        btns.setSpacing(6)
        btns.setContentsMargins(6, 4, 6, 4)

        BTN_BASE = (
            "QPushButton {"
            "  border: 1px solid #cbd5e1;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }"
            "QPushButton:pressed { background-color: #e2e8f0; }"
            "QPushButton:disabled { background-color: #f9fafb; border-color: #e5e7eb; }"
        )
        BTN_APPLY = (
            "QPushButton {"
            "  border: 1px solid #2563eb;"
            "  border-radius: 5px;"
            "  background-color: #ffffff;"
            "  color: #1d4ed8;"
            "  padding: 0px;"
            "}"
            "QPushButton:hover { background-color: #eff6ff; border-color: #1d4ed8; }"
            "QPushButton:pressed { background-color: #dbeafe; }"
        )

        BTN_W, BTN_H, ICON_PX = 28, 24, 14

        def _btn(icon_name, tooltip, style=BTN_BASE):
            b = QPushButton()
            if icon_name:
                b.setIcon(load_icon(icon_name))
                b.setIconSize(QSize(ICON_PX, ICON_PX))
            b.setFixedSize(BTN_W, BTN_H)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip)
            b.setStyleSheet(style)
            return b

        self.add_button    = _btn("add.png",     "Add Device")
        self.edit_button   = _btn("edit.png",    "Edit Device")
        self.remove_button = _btn("remove.png",  "Remove Device")

        # Apply gets the blue-accented BTN_APPLY style, matching the
        # streams Apply button.
        self.apply_button = QPushButton("✓")
        self.apply_button.setFixedSize(BTN_W, BTN_H)
        self.apply_button.setCursor(Qt.PointingHandCursor)
        self.apply_button.setToolTip("Check & Reconfigure Selected Devices")
        self.apply_button.setStyleSheet(BTN_APPLY)

        self.ping_button   = _btn("start.png",   "Ping Test")
        self.arp_button    = _btn("refresh.png", "Refresh ARP Status")
        self.copy_button   = _btn("copy.png",    "Copy Device")
        self.paste_button  = _btn("paste.png",   "Paste Device")
        self.start_device_button = _btn("start.png", "Start Selected Devices")
        self.stop_device_button  = _btn("stop.png",  "Stop Selected Devices")

        # BGP Route Pool Management button — emoji label, no icon
        self.manage_route_pools_button = QPushButton("🗂")
        self.manage_route_pools_button.setFixedSize(BTN_W, BTN_H)
        self.manage_route_pools_button.setCursor(Qt.PointingHandCursor)
        self.manage_route_pools_button.setToolTip("Manage BGP Route Pools")
        self.manage_route_pools_button.setStyleSheet(BTN_BASE)

        # Bulk-edit button — operates on the multi-row table selection.
        # Opens a dialog where the operator picks which fields to edit
        # and a per-field auto-increment step, then mutates every
        # selected device in one go. Saves the "edit 8 devices in a
        # row" loop.
        self.bulk_edit_button = QPushButton("⧉")
        self.bulk_edit_button.setFixedSize(BTN_W, BTN_H)
        self.bulk_edit_button.setCursor(Qt.PointingHandCursor)
        self.bulk_edit_button.setToolTip("Bulk-edit selected devices")
        self.bulk_edit_button.setStyleSheet(BTN_BASE)
        self.bulk_edit_button.clicked.connect(self._open_bulk_edit_dialog)

        # NetGenAI button — keep the special blue treatment since it
        # is a marketing/branded entry point, but adopt the same
        # height (BTN_H) so it aligns with the rest of the row.
        try:
            from traffic_client.ai_menu_actions import TrafficGenClientAIMenuActions
            self.ai_assistant_button = QPushButton("🤖 AI")
            self.ai_assistant_button.setToolTip("Open NetGenAI for selected device")
            self.ai_assistant_button.setFixedSize(54, BTN_H)
            self.ai_assistant_button.setCursor(Qt.PointingHandCursor)
            self.ai_assistant_button.setStyleSheet("""
                QPushButton {
                    background-color: #2563eb;
                    color: white;
                    font-weight: 600;
                    font-size: 11px;
                    border: 1px solid #1d4ed8;
                    border-radius: 5px;
                }
                QPushButton:hover { background-color: #1d4ed8; }
            """)
            self.ai_assistant_button.clicked.connect(
                lambda: self.open_ai_assistant_for_selected()
            )
        except ImportError:
            self.ai_assistant_button = None

        # Group buttons logically with a separator (config left,
        # runtime control right), matching the streams action bar.
        button_list_left = [
            self.add_button, self.edit_button, self.remove_button,
            self.copy_button, self.paste_button, self.bulk_edit_button,
        ]
        button_list_right = [
            self.start_device_button, self.stop_device_button,
            self.apply_button, self.ping_button, self.arp_button,
            self.manage_route_pools_button,
        ]
        if self.ai_assistant_button:
            button_list_right.append(self.ai_assistant_button)

        for b in button_list_left:
            btns.addWidget(b)

        # Vertical divider, matching the streams action bar's grouping
        sep = QLabel()
        sep.setFixedSize(1, BTN_H)
        sep.setStyleSheet("background-color: #cbd5e1; margin: 0 6px;")
        btns.addSpacing(4)
        btns.addWidget(sep)
        btns.addSpacing(4)

        for b in button_list_right:
            btns.addWidget(b)

        btns.addStretch(1)

        # Inline apply-progress widget — shown only while a multi-device
        # apply / start / stop is in flight, hidden otherwise. Previously
        # the UI gave only per-row "Applying…" status with no overall
        # picture; on a batch of 5+ devices this felt unresponsive even
        # though work was happening. Now the user sees "Applying 3/5"
        # ticking up in real time.
        from PyQt5.QtWidgets import QProgressBar
        self._apply_progress_label = QLabel("")
        self._apply_progress_label.setStyleSheet("color: #1d4ed8; font-size: 11px;")
        self._apply_progress_label.setVisible(False)
        self._apply_progress_bar = QProgressBar()
        self._apply_progress_bar.setFixedHeight(BTN_H)
        self._apply_progress_bar.setFixedWidth(140)
        self._apply_progress_bar.setTextVisible(False)
        self._apply_progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px;"
            "  background-color: #ffffff; }"
            "QProgressBar::chunk { background-color: #2563eb; border-radius: 3px; }"
        )
        self._apply_progress_bar.setVisible(False)
        btns.addWidget(self._apply_progress_label)
        btns.addWidget(self._apply_progress_bar)

        # Monitor-health indicator — polls /api/monitors/health every
        # 30s and goes amber when any of ARP / BGP / OSPF / IS-IS /
        # DHCP monitors is wedged or stale. Click the label to see
        # which monitors are off. Without this, background poll
        # failures only surface in the server journal; the user
        # could be staring at stale dots and not know why.
        self._monitor_health_label = QLabel("monitors: …")
        self._monitor_health_label.setStyleSheet(
            "color: #6b7280; font-size: 11px; padding: 0 6px;"
        )
        self._monitor_health_label.setToolTip("Background-monitor health: polling…")
        self._monitor_health_label.setCursor(Qt.PointingHandCursor)
        # mousePressEvent — fire a manual refresh on click for impatient users.
        def _click_health(event, lbl=self._monitor_health_label):
            self._refresh_monitor_health()
        self._monitor_health_label.mousePressEvent = _click_health
        btns.addWidget(self._monitor_health_label)

        self._monitor_health_timer = QTimer(self)
        # Read the user-configurable interval from QSettings (set via
        # File → Settings…). Default 30s; minimum 5s to avoid hammering
        # the server.
        try:
            from PyQt5.QtCore import QSettings
            _poll_s = int(QSettings().value("monitor_poll_interval", 30, type=int))
            _poll_s = max(5, min(300, _poll_s))
        except Exception:
            _poll_s = 30
        self._monitor_health_timer.setInterval(_poll_s * 1000)
        self._monitor_health_timer.timeout.connect(self._refresh_monitor_health)
        self._monitor_health_timer.start()
        # First poll runs as soon as the event loop ticks so the user
        # sees a real value within ~1 second instead of waiting 30 s.
        QTimer.singleShot(1500, self._refresh_monitor_health)

        layout.addWidget(action_bar)

        # wiring
        self.add_button.clicked.connect(self.prompt_add_device)
        self.edit_button.clicked.connect(self.prompt_edit_device)
        self.remove_button.clicked.connect(self.remove_selected_device)
        self.start_device_button.clicked.connect(self.start_selected_devices)
        self.stop_device_button.clicked.connect(self.stop_selected_devices)
        self.apply_button.clicked.connect(self.apply_selected_device_with_arp)
        # Audit LOW #17: F5 reloads the device list from the server
        # so devices added out-of-band (curl, /admin web UI, CLI)
        # surface in the GUI. Background polling is disabled for
        # stability reasons, so this is the explicit refresh path.
        from PyQt5.QtWidgets import QShortcut as _QShortcut
        from PyQt5.QtGui import QKeySequence as _QKeySequence
        _reload_shortcut = _QShortcut(_QKeySequence("F5"), self)
        _reload_shortcut.activated.connect(self.reload_devices_from_server)

        # Additional keyboard shortcuts for the most-used actions.
        # Mac- and Linux-friendly (uses Cmd on macOS, Ctrl elsewhere
        # automatically — that's what QKeySequence("Ctrl+…") does).
        # Each binding is a no-op when no row is selected (the
        # underlying handlers already guard on that).
        _apply_shortcut = _QShortcut(_QKeySequence("Ctrl+Return"), self)
        _apply_shortcut.activated.connect(self.apply_selected_device_with_arp)
        _start_shortcut = _QShortcut(_QKeySequence("Ctrl+S"), self)
        _start_shortcut.activated.connect(self.start_selected_devices)
        _stop_shortcut = _QShortcut(_QKeySequence("Ctrl+X"), self)
        _stop_shortcut.activated.connect(self.stop_selected_devices)
        _refresh_shortcut = _QShortcut(_QKeySequence("Ctrl+R"), self)
        _refresh_shortcut.activated.connect(self._on_arp_button_clicked)
        # Ctrl+F focuses the filter box.
        _filter_shortcut = _QShortcut(_QKeySequence("Ctrl+F"), self)
        _filter_shortcut.activated.connect(
            lambda: self._device_filter_input.setFocus()
            if hasattr(self, "_device_filter_input") else None
        )
        # Ctrl+H opens the per-protocol state-history dialog for the
        # currently-selected device. Backed by /api/device/database/
        # devices/<id>/history — the monitors write a row each time a
        # protocol transitions (Established → Active, etc.) so this
        # surface is the *change-only* timeline, not every poll.
        _history_shortcut = _QShortcut(_QKeySequence("Ctrl+H"), self)
        _history_shortcut.activated.connect(self._show_selected_device_history)
        # Ctrl+J shows the full server-side config JSON for the selected
        # row. Cheaper-than-export read of one device — handy when
        # debugging "why doesn't this look like what I typed?" without
        # having to leave the GUI for `netgen-cli status`.
        _viewcfg_shortcut = _QShortcut(_QKeySequence("Ctrl+J"), self)
        _viewcfg_shortcut.activated.connect(self._show_selected_device_config)

        # v0.2.85: Delete key removes the selected device(s) — standard
        # table-keyboard expectation. Scoped to the devices_table so
        # an inline-edit's Backspace/Delete on a single character
        # doesn't accidentally delete the row. The table's
        # SelectionBehaviour is SelectItems but the remove_selected_*
        # methods walk the row-set so multi-row delete works too.
        _del_shortcut = _QShortcut(_QKeySequence(Qt.Key_Delete),
                                   self.devices_table)
        _del_shortcut.setContext(Qt.WidgetShortcut)  # not global
        _del_shortcut.activated.connect(self.remove_selected_device)

        # v0.2.85: right-click context menu on the devices table.
        # Mirrors the toolbar actions so the operator can apply /
        # copy / paste / delete without rolling the mouse all the
        # way up to the action bar. Custom policy (not the Qt
        # default) so we control the menu items.
        self.devices_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.devices_table.customContextMenuRequested.connect(
            self._on_devices_table_context_menu
        )

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

        # Section header removed — the BGP tab itself + table column
        # names already make it clear what's shown.
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

        # Section header removed — see BGP block for rationale.
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

        # Section header removed — see BGP block for rationale.
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
        result = self.isis_handler.apply_isis_configurations()
        # Refresh preflight pills immediately — IS-IS area edits flip
        # the ISIS_NO_AREA finding state and operators expect the bar
        # to track without the 60 s auto-poll wait.
        try:
            from widgets.preflight_bar import kick_refresh
            kick_refresh(self)
        except Exception:
            pass
        return result
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
        """Handle inline edits to device table cells.

        Audit HIGH #2: this used to be a stub `pass`, so any inline
        edit to a cell would update the table widget but NEVER touch
        self.all_devices. The user would Apply and get the pre-edit
        config pushed to the server, silently losing their edits.

        Wired now to:
          1. Resolve device_id from the row's Device Name cell UserRole.
          2. Look up the column's header name.
          3. Validate the new value via validate_cell_value(); if
             invalid, revert the cell to the previous text stashed at
             UserRole+2 and bail.
          4. Persist via update_device_data_in_memory().
          5. Mark the device for re-apply via mark_device_for_apply().
        """
        # Don't recurse on programmatic table population.
        if getattr(self, "_populating_devices_table", False):
            return
        try:
            tbl = self.devices_table
            item = tbl.item(row, col)
            if item is None:
                return
            new_value = (item.text() or "").strip()

            # cellChanged fires for ANY data change on the item —
            # including setForeground (ARP-status color updates) and
            # setToolTip. Those aren't user edits and shouldn't be
            # logged as such or trigger _needs_apply. The previous
            # value is stashed at UserRole+2 every time we persist;
            # if it matches new_value, the signal came from a style
            # change and we bail.
            prev_stash = item.data(Qt.UserRole + 2)
            if prev_stash is not None and str(prev_stash) == new_value:
                return

            # Header name for this column. COL maps header → index; build
            # inverse on the fly (small dict, cheap).
            header_name = None
            for h, c in self.COL.items():
                if c == col:
                    header_name = h
                    break
            if not header_name:
                return

            # device_id lives on the Device Name cell's UserRole.
            name_col = self.COL.get("Device Name")
            if name_col is None:
                return
            name_item = tbl.item(row, name_col)
            device_id = name_item.data(Qt.UserRole) if name_item else None
            if not device_id:
                # New row mid-population — skip.
                return

            # Validate. validate_cell_value returns False for invalid
            # input; on invalid we revert the cell to the previous
            # value (stashed at UserRole+2 when the table was built).
            try:
                ok = self.validate_cell_value(header_name, new_value, row=row, column=col)
            except Exception as e:
                logging.warning(f"[on_cell_changed] validation raised: {e}")
                ok = True  # fail open
            if not ok:
                prev = item.data(Qt.UserRole + 2)
                if prev is not None:
                    from PyQt5.QtCore import QSignalBlocker
                    with QSignalBlocker(tbl):
                        item.setText(str(prev))
                return

            # Persist + mark dirty.
            self.update_device_data_in_memory(device_id, header_name, new_value)
            # Update the UserRole+2 stash so a future revert uses the
            # new value as the baseline.
            item.setData(Qt.UserRole + 2, new_value)
            if hasattr(self, "mark_device_for_apply"):
                self.mark_device_for_apply(device_id)
            logging.info(
                f"[INLINE EDIT] device {device_id} {header_name!r} → {new_value!r}"
            )
        except Exception as e:
            logging.error(f"[on_cell_changed] error: {e}")

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

    def prompt_add_vxlan(self):
        """Add VXLAN configuration to the currently selected device."""
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select a device to add VXLAN tunnel.")
            return

        row = selected_items[0].row()
        device_name = self.devices_table.item(row, self.COL["Device Name"]).text()

        device_ipv4 = self.devices_table.item(row, self.COL["IPv4"]).text() if self.devices_table.item(row, self.COL["IPv4"]) else ""
        loopback_ipv4 = self.devices_table.item(row, self.COL["Loopback IPv4"]).text() if self.devices_table.item(row, self.COL["Loopback IPv4"]) else ""

        dialog = AddVxlanDialog(
            self,
            device_name,
            edit_mode=False,
            device_ipv4=device_ipv4,
            loopback_ipv4=loopback_ipv4,
        )
        if dialog.exec_() != dialog.Accepted:
            return

        vxlan_config = dialog.get_values()
        logger.info(f"Got VXLAN config: {list(vxlan_config.keys())}")
        logger.info(f"VXLAN config values: vni={vxlan_config.get('vni')}, local_ip={vxlan_config.get('local_ip')}, remote_peers={vxlan_config.get('remote_peers')}")
        logging.debug(f"[VXLAN ADD] Got VXLAN config: {list(vxlan_config.keys())}")
        
        # Handle increment if enabled (create multiple tunnels)
        if vxlan_config.get("increment", {}).get("enabled", False):
            increment_config = vxlan_config["increment"]
            count = increment_config["count"]
            vni_increment = increment_config["vni_increment"]
            vlan_id_increment = increment_config.get("vlan_id_increment", 1)
            svi_ip_octet = increment_config.get("svi_ip_octet", "3rd")
            
            # Get base values
            base_vni = vxlan_config["vni"]
            base_vlan_id = vxlan_config.get("vlan_id")
            base_svi_ip = vxlan_config.get("bridge_svi_ip", "10.0.0.100/24")
            
            # Generate all tunnel configurations first
            generated_tunnels = []
            for i in range(count):
                tunnel_config = vxlan_config.copy()
                tunnel_config.pop("increment", None)  # Remove increment config
                
                # CRITICAL: Remove interface-related fields from new tunnels
                # These should only be set when the tunnel is actually applied to the server
                tunnel_config.pop("vxlan_interface", None)
                tunnel_config.pop("overlay_interface", None)
                tunnel_config.pop("underlay_interface", None)
                
                # Increment VNI
                tunnel_config["vni"] = base_vni + (i * vni_increment)
                
                # Increment VLAN ID if provided
                if base_vlan_id:
                    tunnel_config["vlan_id"] = base_vlan_id + (i * vlan_id_increment)
                
                # Increment Bridge SVI IP if provided
                if base_svi_ip:
                    try:
                        # Parse CIDR notation
                        if "/" in base_svi_ip:
                            ip_interface = ipaddress.IPv4Interface(base_svi_ip)
                            ip_address = ip_interface.ip
                            prefix = ip_interface.network.prefixlen
                        else:
                            ip_address = ipaddress.IPv4Address(base_svi_ip)
                            prefix = 24  # Default prefix
                        
                        # Determine octet index
                        octet_map = {"1st": 0, "2nd": 1, "3rd": 2, "4th": 3}
                        octet_index = octet_map.get(svi_ip_octet, 2)
                        
                        # Increment the specified octet
                        ip_parts = str(ip_address).split(".")
                        ip_parts[octet_index] = str(int(ip_parts[octet_index]) + i)
                        new_ip = ".".join(ip_parts)
                        
                        tunnel_config["bridge_svi_ip"] = f"{new_ip}/{prefix}"
                    except Exception as e:
                        logger.error(f"Error incrementing Bridge SVI IP: {e}")
                        tunnel_config["bridge_svi_ip"] = base_svi_ip
                
                generated_tunnels.append(tunnel_config)
                logger.info(f"Generated tunnel {i+1}/{count}, VNI: {tunnel_config.get('vni')}")
                logging.debug(f"[VXLAN ADD] Generated tunnel {i+1}/{count}, VNI: {tunnel_config.get('vni')}")
            
            # Now merge all generated tunnels with existing tunnels
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            device = None
            for iface, devices in self.main_window.all_devices.items():
                for d in devices:
                    if d.get("Device Name") == device_name:
                        device = d
                        break
                if device:
                    break
            
            if device:
                # Get existing vxlan_config
                existing_vxlan = device.get("vxlan_config", {})
                
                # If existing config is a dict, extract tunnels
                if isinstance(existing_vxlan, dict) and existing_vxlan:
                    if "tunnels" in existing_vxlan:
                        existing_tunnels = existing_vxlan.get("tunnels", [])
                    else:
                        # Convert single tunnel dict to list format
                        existing_tunnels = [existing_vxlan]
                else:
                    existing_tunnels = []
                
                # Merge generated tunnels with existing ones
                # Check for duplicate VNIs and update existing or add new
                for new_tunnel in generated_tunnels:
                    new_vni = new_tunnel.get("vni")
                    tunnel_exists = False
                    for i, existing_tunnel in enumerate(existing_tunnels):
                        if isinstance(existing_tunnel, dict) and existing_tunnel.get("vni") == new_vni:
                            # Update existing tunnel with same VNI
                            # Preserve interface-related fields from existing tunnel if they exist
                            # (these are set when tunnel is applied to server)
                            preserved_interface = existing_tunnel.get("vxlan_interface")
                            preserved_overlay = existing_tunnel.get("overlay_interface")
                            preserved_underlay = existing_tunnel.get("underlay_interface")
                            
                            # Update with new config
                            existing_tunnels[i] = new_tunnel.copy()
                            
                            # Restore interface fields if they existed (tunnel was already applied)
                            if preserved_interface:
                                existing_tunnels[i]["vxlan_interface"] = preserved_interface
                            if preserved_overlay:
                                existing_tunnels[i]["overlay_interface"] = preserved_overlay
                            if preserved_underlay:
                                existing_tunnels[i]["underlay_interface"] = preserved_underlay
                            
                            tunnel_exists = True
                            logger.info(f"Updated existing tunnel with VNI {new_vni}")
                            break
                    
                    if not tunnel_exists:
                        # Add new tunnel (no interface name - will be set when applied)
                        # Ensure no interface fields are present
                        new_tunnel_clean = new_tunnel.copy()
                        new_tunnel_clean.pop("vxlan_interface", None)
                        new_tunnel_clean.pop("overlay_interface", None)
                        new_tunnel_clean.pop("underlay_interface", None)
                        existing_tunnels.append(new_tunnel_clean)
                        logger.info(f"Added new tunnel with VNI {new_vni} (no interface - will be set when applied)")
                
                # Update device with merged tunnels
                device["vxlan_config"] = {"tunnels": existing_tunnels}
                logger.info(f"Updated device with {len(existing_tunnels)} total tunnel(s) (generated {len(generated_tunnels)} new)")
                logging.info(f"[VXLAN ADD] Updated device with {len(existing_tunnels)} total tunnel(s)")
                
                # Ensure VXLAN is in protocols
                if "protocols" not in device:
                    device["protocols"] = []
                if "VXLAN" not in device["protocols"]:
                    device["protocols"].append("VXLAN")
                
                # Update device table to reflect changes immediately
                self.update_device_table(self.main_window.all_devices)
                
                # Refresh VXLAN table to show the new tunnels
                if hasattr(self, "vxlan_handler") and self.vxlan_handler:
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(100, self.vxlan_handler.refresh_vxlan_table)
            else:
                # Device not found - this shouldn't happen, but handle gracefully
                logger.error(f"ERROR: Device {device_name} not found in all_devices")
                logging.error(f"[VXLAN ADD] Device {device_name} not found in all_devices - cannot add tunnels")
                QMessageBox.warning(self, "Device Not Found", f"Could not find device '{device_name}' to add VXLAN tunnels. Please ensure the device exists in the devices table.")
                return  # Exit early if device not found
        else:
            # Single tunnel - add to device's tunnel list (support multiple tunnels per device)
            logger.info(f"Adding single tunnel to device, VNI: {vxlan_config.get('vni')}")
            logging.debug(f"[VXLAN ADD] Adding single tunnel to device, VNI: {vxlan_config.get('vni')}")
            
            # Get the device
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            device = None
            for iface, devices in self.main_window.all_devices.items():
                for d in devices:
                    if d.get("Device Name") == device_name:
                        device = d
                        break
                if device:
                    break
            
            if device:
                # Get existing vxlan_config
                existing_vxlan = device.get("vxlan_config", {})
                
                # If existing config is a dict (single tunnel), convert to list
                if isinstance(existing_vxlan, dict) and existing_vxlan:
                    # Check if it's already a list format or single tunnel
                    if "tunnels" in existing_vxlan:
                        tunnels = existing_vxlan.get("tunnels", [])
                    else:
                        # Convert single tunnel dict to list format
                        tunnels = [existing_vxlan]
                        existing_vxlan = {"tunnels": tunnels}
                    
                    # Check if this VNI already exists
                    new_vni = vxlan_config.get("vni")
                    tunnel_exists = any(t.get("vni") == new_vni for t in tunnels)
                    
                    if tunnel_exists:
                        # Update existing tunnel
                        for i, tunnel in enumerate(tunnels):
                            if tunnel.get("vni") == new_vni:
                                tunnels[i] = vxlan_config
                                logger.info(f"Updated existing tunnel with VNI {new_vni}")
                                break
                    else:
                        # Add new tunnel
                        tunnels.append(vxlan_config)
                        logger.info(f"Added new tunnel with VNI {new_vni} (total tunnels: {len(tunnels)})")
                    
                    existing_vxlan["tunnels"] = tunnels
                    device["vxlan_config"] = existing_vxlan
                else:
                    # No existing config, create new list
                    device["vxlan_config"] = {"tunnels": [vxlan_config]}
                    logger.info(f"Created new tunnel list with VNI {vxlan_config.get('vni')}")
                
                # Ensure VXLAN is in protocols
                if "protocols" not in device:
                    device["protocols"] = []
                if "VXLAN" not in device["protocols"]:
                    device["protocols"].append("VXLAN")
        
        # Refresh device table first to ensure device data is updated
        self.update_device_table(self.main_window.all_devices)
        
        # Refresh VXLAN table to show the new tunnel (with a small delay to ensure data is ready)
        if hasattr(self, "vxlan_handler") and self.vxlan_handler:
            # Use QTimer to refresh after a short delay to ensure device data is updated
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(100, self.vxlan_handler.refresh_vxlan_table)

    def apply_vxlan_configurations(self):
        """Apply VXLAN configurations to the server for selected devices."""
        server_url = self.get_server_url()
        if not server_url:
            QMessageBox.critical(self, "No Server", "No server selected.")
            return

        devices_to_apply = []
        selected_device_names = set()
        
        # PRIORITY 1: Check if rows are selected in the VXLAN table
        if hasattr(self, "vxlan_table") and self.vxlan_table:
            selected_vxlan_items = self.vxlan_table.selectedItems()
            if selected_vxlan_items:
                # Get unique device names from selected VXLAN table rows
                device_col_idx = self.VXLAN_COL.get("Device", 0)
                unique_rows = set()
                
                for item in selected_vxlan_items:
                    row = item.row()
                    if row in unique_rows:
                        continue
                    unique_rows.add(row)
                    
                    # Get device name from the Device column
                    device_item = self.vxlan_table.item(row, device_col_idx)
                    if device_item:
                        device_name = device_item.text().strip()
                        # Remove "(Tunnel X/Y)" suffix if present (e.g., "device1 (Tunnel 1/3)" -> "device1")
                        if " (Tunnel " in device_name:
                            device_name = device_name.split(" (Tunnel ")[0].strip()
                        if device_name:
                            selected_device_names.add(device_name)
                            logger.info(f"Selected device from VXLAN table: {device_name}")
                
                if selected_device_names:
                    logger.info(f"Found {len(selected_device_names)} device(s) selected in VXLAN table: {selected_device_names}")
        
        # PRIORITY 2: If no VXLAN table selection, check devices table selection
        if not selected_device_names:
            selected_items = self.devices_table.selectedItems()
            if selected_items:
                # Get unique device names from selected rows
                for item in selected_items:
                    row = item.row()
                    device_name_item = self.devices_table.item(row, self.COL["Device Name"])
                    if device_name_item:
                        device_name = device_name_item.text()
                        selected_device_names.add(device_name)
                        logger.info(f"Selected device from devices table: {device_name}")
        
        # Find the devices in all_devices that have VXLAN config
        if selected_device_names:
            # Apply only to selected devices
            for device_name in selected_device_names:
                for iface, devices in self.main_window.all_devices.items():
                    for device in devices:
                        if device.get("Device Name") == device_name:
                            vxlan_config = device.get("vxlan_config", {})
                            # If local config is empty, try to load from database
                            if not vxlan_config or (isinstance(vxlan_config, dict) and len(vxlan_config) == 0):
                                device_id = device.get("device_id")
                                if device_id:
                                    try:
                                        import requests
                                        server_url = self.get_server_url()
                                        if server_url:
                                            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
                                            if response.status_code == 200:
                                                db_device_data = response.json()
                                                db_vxlan = db_device_data.get("vxlan_config", {})
                                                if isinstance(db_vxlan, str):
                                                    import json
                                                    try:
                                                        db_vxlan = json.loads(db_vxlan)
                                                    except Exception:
                                                        db_vxlan = {}
                                                if db_vxlan and (isinstance(db_vxlan, dict) and len(db_vxlan) > 0):
                                                    device["vxlan_config"] = db_vxlan
                                                    vxlan_config = db_vxlan
                                                    logger.info(f"Loaded VXLAN config from database for {device_name}")
                                    except Exception as db_load_exc:
                                        logger.error(f"Failed to load VXLAN config from database: {db_load_exc}")
                            if vxlan_config:
                                devices_to_apply.append(device)
                            break
        else:
            # If no devices selected in either table, show message asking to select
            QMessageBox.information(
                self,
                "No Selection",
                "Please select one or more devices in the VXLAN table or Devices table before applying VXLAN configuration.\n\n"
                "To apply:\n"
                "1. Select row(s) in the VXLAN Tunnel Status table, OR\n"
                "2. Select row(s) in the Devices table"
            )
            return
        
        if not devices_to_apply:
            QMessageBox.information(self, "No VXLAN Configuration", 
                                  "No devices with VXLAN configuration found to apply.\n\n"
                                  "Please add VXLAN tunnels first using the 'Add' button.")
            return
        
        # Confirm with user
        device_names = [d.get("Device Name", "Unknown") for d in devices_to_apply]
        reply = QMessageBox.question(
            self,
            "Apply VXLAN Configuration",
            f"Apply VXLAN configuration to {len(devices_to_apply)} device(s)?\n\n"
            f"Devices: {', '.join(device_names)}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Apply VXLAN configuration for each device
        success_count = 0
        failed_count = 0
        failed_devices = []
        
        for device in devices_to_apply:
            device_name = device.get("Device Name", "Unknown")
            try:
                # Ensure VXLAN is in protocols list
                protocols = self._convert_protocols_to_array(device.get("protocols", []))
                if "VXLAN" not in protocols:
                    protocols.append("VXLAN")
                
                # Apply device configuration to server
                result = self._apply_device_to_server(server_url, device)
                if result:
                    success_count += 1
                    logging.info(f"[VXLAN APPLY] Successfully applied VXLAN configuration for {device_name}")
                else:
                    failed_count += 1
                    failed_devices.append(device_name)
                    logging.error(f"[VXLAN APPLY] Failed to apply VXLAN configuration for {device_name}")
            except Exception as e:
                failed_count += 1
                failed_devices.append(device_name)
                logging.error(f"[VXLAN APPLY] Exception applying VXLAN configuration for {device_name}: {e}")
        
        # v0.2.93: use MultiDeviceResultsDialog for per-device results —
        # matches BGP / OSPF / ISIS apply UX. The previous
        # QMessageBox.information just gave a count + comma-separated
        # name list; the dialog shows each device's outcome on its own
        # colour-coded line, which scales when there are more than a
        # handful of devices.
        failed_set = set(failed_devices)
        results = []
        for device in devices_to_apply:
            name = device.get("Device Name", "Unknown")
            if name in failed_set:
                results.append(
                    f"❌ {name}: VXLAN configuration failed "
                    f"(check server logs)"
                )
            else:
                results.append(
                    f"✅ {name}: VXLAN configuration applied"
                )

        try:
            summary = (
                f"Applied VXLAN configuration: "
                f"{success_count} succeeded, {failed_count} failed"
            )
            title = (
                "VXLAN Configuration Applied"
                if failed_count == 0
                else "VXLAN Configuration Partially Applied"
            )
            MultiDeviceResultsDialog(title, summary, results, self).exec_()
        except Exception as dlg_exc:
            # Dialog failure should never block the apply path; fall
            # back to the simpler message so the operator still gets
            # confirmation that work happened.
            logging.warning(
                f"[VXLAN APPLY] could not show results dialog: {dlg_exc}"
            )
            if failed_count == 0:
                QMessageBox.information(
                    self, "VXLAN Configuration Applied",
                    f"Successfully applied VXLAN configuration to "
                    f"{success_count} device(s)."
                )
            else:
                QMessageBox.warning(
                    self, "VXLAN Configuration Partially Applied",
                    f"Applied to {success_count} device(s).\n"
                    f"Failed for {failed_count} device(s):\n"
                    + ", ".join(failed_devices)
                )
        
        # Refresh VXLAN table and device table
        if hasattr(self, "vxlan_handler") and self.vxlan_handler:
            self.vxlan_handler.refresh_vxlan_table()
        self.update_device_table(self.main_window.all_devices)

        # Refresh preflight pills — VXLAN edits flip VXLAN_EMPTY /
        # VXLAN_MISSING_FIELDS finding state immediately.
        try:
            from widgets.preflight_bar import kick_refresh
            kick_refresh(self)
        except Exception:
            pass

        # Interface list refresh is now manual only - user can click "Refresh Interface List" button if needed
        # Removed automatic refresh to prevent unnecessary UI updates
        # if success_count > 0 and hasattr(self.main_window, "update_server_tree"):
        #     print(f"[VXLAN APPLY] Scheduling interface list refresh from server after VXLAN tunnel creation (delay: 500ms)")
        #     from PyQt5.QtCore import QTimer
        #     # Define a callback function to ensure it's called correctly
        #     def refresh_interfaces():
        #         print(f"[VXLAN APPLY] Executing interface list refresh callback")
        #         try:
        #             if hasattr(self.main_window, "update_server_tree"):
        #                 # Clear cached interfaces for all servers to force fresh fetch
        #                 # This ensures newly created VXLAN/bridge interfaces are added to the tree
        #                 if hasattr(self.main_window, "server_interfaces"):
        #                     for server in self.main_window.server_interfaces:
        #                         if "interfaces" in server:
        #                             del server["interfaces"]
        #                             print(f"[VXLAN APPLY] Cleared cached interfaces for server: {server.get('address', 'unknown')}")
        #                 
        #                 # Now refresh the server tree with fresh data
        #                 self.main_window.update_server_tree()
        #                 print(f"[VXLAN APPLY] Interface list refresh completed")
        #             else:
        #                 print(f"[VXLAN APPLY] WARNING: update_server_tree not available in callback")
        #         except Exception as e:
        #             print(f"[VXLAN APPLY] ERROR during interface refresh: {e}")
        #             import logging
        #             logging.error(f"[VXLAN APPLY] ERROR during interface refresh: {e}", exc_info=True)
        #     QTimer.singleShot(500, refresh_interfaces)

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
                        logger.info(f"✅ BGP configuration removed from server for {device_name}")
                    else:
                        error_msg = response.json().get("error", "Unknown error")
                        logger.error(f"⚠️ Server BGP cleanup failed for {device_name}: {error_msg}")
                except requests.exceptions.RequestException as exc:
                    logger.error(f"⚠️ Network error removing BGP from server for {device_name}: {exc}")

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
                logger.info(f"All {total_rows} rows selected")
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

    # v0.2.85: removed three dead wrappers (apply_bgp_configurations,
    # start_bgp_protocol, stop_bgp_protocol). Each was redefined later
    # in the class body (the v0.2.74 versions with the kick_refresh
    # hook + the v0.2.41 _toggle_protocol_action variants), so the
    # earlier defs here were dead per Python's last-def-wins rule.
    # Deleting reduces the surface a future refactor can accidentally
    # call the wrong one.

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
        except Exception:
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
        logger.info(f"{device_name}: {status_message}")
    
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
            logger.debug(f"\n{'='*60}")
            logger.info(f"DEVICE OPERATION RESULTS: {successful_count} successful, {failed_count} failed")
            logger.debug(f"{'='*60}")
            for result in results:
                logger.info(f"  {result}")
            logger.debug(f"{'='*60}\n")
        
        # v0.5.215: always refresh the device table from DB after
        # any start/stop attempt, success or fail. Pre-fix the
        # refresh was gated on `if successful_count > 0` — so on
        # a client-side timeout (30s POST) or partial failure,
        # the row's "Starting..." text lingered because nothing
        # ever re-read the DB to reveal the true state. The
        # server may have completed the start and written
        # "Running" but the client never asked.
        # Protocol-tab + DHCP refreshes still gated on success
        # (no point re-drawing OSPF/BGP tables when nothing
        # meaningful changed on the client's understanding).
        QTimer.singleShot(200, lambda: self._refresh_device_table_from_database(selected_rows))
        if successful_count > 0:
            QTimer.singleShot(100, lambda: self._refresh_protocols_for_selected_devices(selected_rows))
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
        """Refresh device table status + ARP colors for the given rows.

        ASYNC: the per-device DB fetch (HTTP) runs in a background QThread
        so the UI never blocks. This is what let us re-enable the periodic
        status poll (status_timer) — it was disabled to dodge the
        QThread-destruction crashes now fixed by the global keepalive
        (utils.qthread_keepalive). Widget updates happen only on the main
        thread, in _apply_device_status_row, via the worker's signal.
        """
        try:
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return

            # Build the (row, device_id) job list on the MAIN thread —
            # this reads widgets/all_devices, which must not be touched
            # off-thread. The worker only does HTTP + emits plain data.
            jobs = []
            for row in selected_rows:
                try:
                    name_item = self.devices_table.item(row, self.COL["Device Name"])
                    if not name_item:
                        continue
                    device_name = name_item.text()
                    device_id = None
                    for iface, devices in self.main_window.all_devices.items():
                        for device in devices:
                            if device.get("Device Name") == device_name:
                                device_id = device.get("device_id")
                                break
                        if device_id:
                            break
                    if device_id:
                        jobs.append((row, device_id))
                except Exception as e:
                    logger.debug(f"[DEVICE POLL] job build error row {row}: {e}")
            if not jobs:
                return

            from PyQt5.QtCore import QThread, pyqtSignal

            class _DeviceStatusFetchWorker(QThread):
                # (row, device_data) — emitted per device once fetched.
                row_data = pyqtSignal(int, dict)

                def __init__(self, url, jobs):
                    super().__init__()
                    self._url = url
                    self._jobs = jobs

                def run(self):
                    import requests as _rq
                    for _row, _dev_id in self._jobs:
                        try:
                            r = _rq.get(
                                f"{self._url}/api/device/database/devices/{_dev_id}",
                                timeout=3,
                            )
                            if r.status_code == 200:
                                self.row_data.emit(_row, r.json() or {})
                        except Exception:
                            # Unreachable/slow device — skip; next poll retries.
                            pass

            worker = _DeviceStatusFetchWorker(server_url, jobs)
            # Pin a strong ref so the QThread can't be GC'd mid-run
            # (the global QThread.start hook also covers this, but be
            # explicit so the Devices tab is safe standalone too).
            try:
                from utils.qthread_keepalive import keep
                keep(worker)
            except Exception:
                pass
            worker.row_data.connect(self._apply_device_status_row)
            worker.start()
        except Exception as e:
            logger.error(f"Error refreshing device table: {e}")

    def _apply_device_status_row(self, row, device_data):
        """Main-thread slot: apply status + ARP colors for one device row
        from freshly-fetched DB data. Mirrors the old synchronous loop
        body of _refresh_device_table_from_database (now async)."""
        try:
            name_item = self.devices_table.item(row, self.COL["Device Name"])
            device_name = name_item.text() if name_item else f"row{row}"

            # Update device status text
            device_status = device_data.get('status', 'Unknown')
            status_item = self.devices_table.item(row, self.COL["Status"])
            if status_item and status_item.text() != device_status:
                status_item.setText(device_status)
            # Keep all_devices in sync so other paths see the fresh status.
            try:
                info = self.get_device_info_by_name(device_name)
                if info is not None:
                    info["Status"] = device_status
            except Exception:
                pass

            # ARP status → individual IP/gateway cell colors
            arp_ipv4_raw = device_data.get('arp_ipv4_resolved', 0)
            arp_ipv6_raw = device_data.get('arp_ipv6_resolved', 0)
            arp_gateway_raw = device_data.get('arp_gateway_resolved', 0)
            arp_results = {
                "ipv4_resolved": bool(arp_ipv4_raw),
                "ipv6_resolved": bool(arp_ipv6_raw),
                "gateway_resolved": bool(arp_gateway_raw),
                "ipv4_status": "Resolved" if arp_ipv4_raw else "Failed",
                "ipv6_status": "Resolved" if arp_ipv6_raw else "Failed",
                "gateway_status": "Resolved" if arp_gateway_raw else "Failed",
                "overall_status": device_data.get('arp_status', 'Unknown'),
            }
            self.set_status_icon_with_individual_ips(row, arp_results)

            # Overall status icon: require only the configured families.
            if device_status == "Running":
                ipv6_configured = bool((device_data.get("ipv6_address") or device_data.get("IPv6") or "").strip())
                gateway_configured = bool((device_data.get("ipv4_gateway") or device_data.get("IPv4 Gateway") or "").strip())
                overall_resolved = arp_results["ipv4_resolved"]
                if ipv6_configured:
                    overall_resolved = overall_resolved and arp_results["ipv6_resolved"]
                if gateway_configured:
                    overall_resolved = overall_resolved and arp_results["gateway_resolved"]
                self.set_status_icon(row, resolved=overall_resolved,
                                     status_text=arp_results["overall_status"], device_status=device_status)
            else:
                self.set_status_icon(row, resolved=False,
                                     status_text=arp_results["overall_status"], device_status=device_status)
        except Exception as e:
            logger.error(f"[DEVICE POLL] apply row {row} failed: {e}")

    def _on_arp_operation_progress(self, device_name, status_message):
        """Handle progress updates from ARP operation worker."""
        logger.info(f"{device_name}: {status_message}")
    
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
            logger.debug(f"\n{'='*60}")
            logger.info(f"ARP OPERATION RESULTS: {successful_count} successful, {failed_count} failed")
            logger.debug(f"{'='*60}")
            for result in results:
                logger.info(f"  {result}")
            logger.debug(f"{'='*60}\n")
        
        # Only restore status icons for devices that were actually processed (have results)
        logger.debug(f"Processing {len(selected_rows)} selected rows: {selected_rows}")
        for row in selected_rows:
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            logger.debug(f"Processing row {row}, device: {device_name}")
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
                    logger.debug(f"Updating status for {device_name} - has result: {device_result}")
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
                    logger.debug(f"Skipping status update for {device_name} - no result found (not processed)")
        
        # Clear the pending ARP rows now that the operation is finished
        if hasattr(self, '_pending_arp_rows'):
            if hasattr(self, '_arp_retry_rows') and self._arp_retry_rows:
                logger.debug(f"Pending retries for rows {self._arp_retry_rows} - keeping _pending_arp_rows intact")
            else:
                delattr(self, '_pending_arp_rows')
                logger.debug(f"Cleared _pending_arp_rows")
        
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
            
            logger.info(f"Refreshed protocols: {', '.join(protocols_to_refresh)}")
        
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
            logger.info(f"IPv6 Gateway: text='{gateway_text}', ipv6_resolved={ipv6_resolved}")
            if not ipv6_resolved and gateway_text:
                ipv6_gateway_item.setForeground(orange_color)
                ipv6_gateway_item.setToolTip(f"IPv6 ARP failed: {arp_results.get('ipv6_status', 'Unknown')}")
                logger.info(f"IPv6 Gateway set to ORANGE")
            else:
                ipv6_gateway_item.setForeground(default_color)
                if ipv6_resolved:
                    ipv6_gateway_item.setToolTip("IPv6 ARP resolved")
                else:
                    ipv6_gateway_item.setToolTip("IPv6 Gateway address")
                logger.info(f"IPv6 Gateway set to DEFAULT")

    def get_server_url(self, silent=False, device_info=None):
        """Get server URL with support for explicit device server association."""
        # Priority 1: From device_info (explicit server association)
        if device_info and hasattr(self.main_window, "server_manager"):
            server_url = self.main_window.server_manager.get_server_url(device_info=device_info)
            if server_url:
                return server_url
        
        # Priority 2: From main_window.server_url
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
                    logger.debug(f"Using tree selection server_url: {server_address}")
                    return server_address

        logger.debug(f"No server URL found - main_window.server_url: {getattr(self.main_window, 'server_url', None)}")
        if not silent:
            QMessageBox.critical(self, "No Server Selected",
                                 "Please select a server before starting/stopping devices.")
        return None

    def _get_server_url_from_interface(self, iface_label, device_info=None):
        """Derive the server URL from an interface label or device_info (e.g., 'TG 0 - Port: • ens4np0')."""
        # Priority 1: Use explicit server info from device_info if available
        if device_info and hasattr(self.main_window, "server_manager"):
            server_url = self.main_window.server_manager.get_server_url(device_info=device_info)
            if server_url:
                return server_url
        
        # Priority 2: Parse from interface label (backward compatibility)
        if not iface_label:
            return self.get_server_url(silent=True)

        if "TG" in iface_label:
            # CRITICAL: Split on " - " (space-dash-space) to handle interface names with dashes
            # This ensures we correctly extract "TG 0" from "TG 0 - ens4np0" even if interface name has dashes
            if " - " in iface_label:
                tg_part = iface_label.split(" - ", 1)[0].strip()
            else:
                # Fallback: if no " - " found, try splitting on first dash
                tg_part = iface_label.split("-", 1)[0].strip()
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

    def _migrate_interface_keys(self):
        """
        Migrate existing interface keys in all_devices to include TG ID prefix.
        This fixes devices that were created before the TG ID prefix fix was applied.
        """
        if not hasattr(self.main_window, "all_devices") or not self.main_window.all_devices:
            return
        
        if not hasattr(self.main_window, "server_interfaces") or not self.main_window.server_interfaces:
            return
        
        # Build a map of port names to TG IDs from server_interfaces
        port_to_tg = {}
        if hasattr(self.main_window, "server_tree") and self.main_window.server_tree:
            from PyQt5.QtWidgets import QLabel
            tree = self.main_window.server_tree
            for i in range(tree.topLevelItemCount()):
                server_item = tree.topLevelItem(i)
                if not server_item:
                    continue
                
                # Extract TG ID from custom widget
                tg_id_widget = tree.itemWidget(server_item, 0)
                tg_id = None
                if tg_id_widget:
                    for child in tg_id_widget.findChildren(QLabel):
                        text = child.text()
                        if text.startswith("TG "):
                            tg_id = text.strip()
                            break
                
                # Fallback to server_interfaces
                if not tg_id and i < len(self.main_window.server_interfaces):
                    server = self.main_window.server_interfaces[i]
                    tg_id = f"TG {server.get('tg_id', '0')}"
                
                if tg_id:
                    # Get all child interfaces for this server
                    for j in range(server_item.childCount()):
                        port_item = server_item.child(j)
                        if port_item:
                            port_name = port_item.text(0).replace("• ", "").strip()
                            if port_name:
                                port_to_tg[port_name] = tg_id
        
        migrated_count = 0
        new_all_devices = {}
        
        for old_key, devices in self.main_window.all_devices.items():
            if not isinstance(devices, list):
                new_all_devices[old_key] = devices
                continue
            
            # Check if key already has TG ID prefix
            if old_key.startswith("TG ") and " - " in old_key:
                # Already migrated, keep as is
                new_all_devices[old_key] = devices
                continue
            
            # Extract port name from old key (e.g., "ens4np0" from " - ens4np0")
            port_name = old_key.strip().lstrip(" - ").strip()
            if not port_name:
                # Invalid key, keep as is
                new_all_devices[old_key] = devices
                continue
            
            # Try to find TG ID from port_to_tg map
            tg_id = port_to_tg.get(port_name)
            
            # Fallback: try to find TG ID from device's Interface field
            if not tg_id:
                for device in devices:
                    device_interface = device.get("Interface", "")
                    if device_interface:
                        # Check if Interface field contains TG ID (e.g., "TG 0 - ens4np0")
                        if "TG " in device_interface and " - " in device_interface:
                            tg_part = device_interface.split(" - ", 1)[0].strip()
                            if tg_part.startswith("TG "):
                                tg_id = tg_part
                                break
            
            # Fallback: use first server's TG ID
            if not tg_id and self.main_window.server_interfaces:
                first_server = self.main_window.server_interfaces[0]
                tg_id = f"TG {first_server.get('tg_id', '0')}"
            
            # If we found a TG ID, create new key with prefix
            if tg_id:
                new_key = f"{tg_id} - {port_name}"
                new_all_devices[new_key] = devices
                # Update interface_key in each device
                for device in devices:
                    device["interface_key"] = new_key
                migrated_count += len(devices)
                logger.info(f"Migrated {len(devices)} device(s) from '{old_key}' to '{new_key}'")
            else:
                # Could not determine TG ID, keep old key
                new_all_devices[old_key] = devices
        
        if migrated_count > 0:
            self.main_window.all_devices = new_all_devices
            logger.info(f"Migrated {migrated_count} device(s) to use TG ID prefix in interface keys")

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
            # UserRole+2 stashes the canonical "current value" so
            # on_cell_changed can distinguish real user edits from
            # spurious cellChanged signals fired by setForeground /
            # setToolTip (the ARP-status color-coding path).
            item.setData(Qt.UserRole + 2, str(val))
            self.devices_table.setItem(row, self.COL[header], item)

        put("Device Name", name, user_data=device_id)
        put("MAC Address", mac)

        ipv4_item = QTableWidgetItem(str(ipv4))
        ipv4_item.setData(Qt.UserRole + 1, ipv4_mask)
        ipv4_item.setData(Qt.UserRole + 2, str(ipv4))
        self.devices_table.setItem(row, self.COL["IPv4"], ipv4_item)

        ipv6_item = QTableWidgetItem(str(ipv6))
        ipv6_item.setData(Qt.UserRole + 1, ipv6_mask)
        ipv6_item.setData(Qt.UserRole + 2, str(ipv6))
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

    def reload_devices_from_server(self):
        """Refresh the device list from the server-side database.

        Audit LOW #17: no code listens for out-of-band device
        changes — a device added via curl, CLI, or the /admin web
        UI doesn't appear in the GUI until the user manually
        triggers something. Background polling is explicitly
        disabled (the timer at __init__ is set up but never
        started; comment cites past QThread crashes), so a manual
        refresh is the safest way to surface those changes.

        Reachable via F5 (see __init__) and any future menu entry.
        Fetches /api/device/database/devices from every online
        server in self.server_interfaces and merges the result
        into self.all_devices, keyed by Interface label. Devices
        present locally but not on the server are KEPT so an
        in-progress edit isn't clobbered by a stale fetch.
        """
        import requests as _req
        from PyQt5.QtCore import QThread, QEventLoop

        servers = getattr(self.main_window, "server_interfaces", []) or []
        if not servers:
            QMessageBox.information(
                self, "Reload Devices",
                "No servers configured — nothing to refresh.",
            )
            return

        # Off-thread fetch per server, same pattern as
        # _check_arp_resolution_sync so the UI keeps repainting.
        class _DevFetchWorker(QThread):
            def __init__(self, url):
                super().__init__()
                self._url = url
                self.devices = []
                self.error = None

            def run(self):
                try:
                    r = _req.get(self._url, timeout=(3, 10))
                    if r.ok:
                        body = r.json() or {}
                        # Endpoint returns either a list or a dict
                        # with a "devices" key — handle both.
                        if isinstance(body, list):
                            self.devices = body
                        elif isinstance(body, dict):
                            self.devices = body.get("devices") or []
                except Exception as exc:
                    self.error = exc

        merged_seen_ids = set()
        for srv in servers:
            if not srv.get("online", True):
                continue
            addr = srv.get("address")
            if not addr:
                continue
            tg_id = srv.get("tg_id", "0")
            url = f"{addr}/api/device/database/devices"
            loop = QEventLoop()
            worker = _DevFetchWorker(url)
            worker.finished.connect(loop.quit)
            worker.start()
            loop.exec_()
            worker.wait()
            worker.deleteLater()
            if worker.error is not None:
                logger.warning(f"[RELOAD] {addr} fetch failed: {worker.error}")
                continue

            for dev in worker.devices:
                if not isinstance(dev, dict):
                    continue
                dev_id = dev.get("device_id") or dev.get("id")
                if dev_id:
                    merged_seen_ids.add(dev_id)
                iface = dev.get("Interface") or dev.get("interface")
                # v0.3.11: previously this only synthesized the
                # canonical form when iface was FALSY. A server
                # returning a malformed truthy value (" - ens5np0",
                # "Port: ens5np0", or just "ens5np0") would flow
                # through verbatim, bucket the device under the
                # malformed key, and make it invisible to every UI
                # lookup that uses the "TG N - port" form. The
                # shared `canonical_iface_key` helper now normalizes
                # uniformly — both the bare-fallback path and the
                # malformed-but-truthy case land in the same canonical
                # bucket.
                if not iface:
                    iface = dev.get("interface_name") or dev.get("port")
                from utils.iface_naming import canonical_iface_key
                iface = canonical_iface_key(iface, tg_id=tg_id)
                if not iface:
                    continue
                # Don't clobber an in-progress local edit: if we
                # already have this device locally with _needs_apply
                # set, skip it.
                with self.device_mutate_lock:
                    bucket = self.main_window.all_devices.setdefault(iface, [])
                    existing = next(
                        (d for d in bucket if d.get("device_id") == dev_id),
                        None,
                    )
                    if existing is not None:
                        if existing.get("_needs_apply") or existing.get("_is_new"):
                            continue  # Preserve user's pending edit
                        existing.update({
                            k: v for k, v in dev.items()
                            if not k.startswith("_")
                        })
                    else:
                        # Server-side device new to us — add it,
                        # mark as already applied (no _is_new).
                        dev.setdefault("device_id", dev_id or str(uuid.uuid4()))
                        dev["Interface"] = iface
                        dev.setdefault("Status", dev.get("Status") or "Stopped")
                        bucket.append(dev)

        # Refresh table after merge.
        self.update_device_table(self.main_window.all_devices)
        logger.info(
            f"[RELOAD] Synced from {len(servers)} server(s); "
            f"{len(merged_seen_ids)} device id(s) merged"
        )

        # Now that the server is confirmed reachable, spin up the SSE
        # consumer for live updates. Reusing the existing worker is
        # cheap; we don't stack workers on repeated reloads.
        try:
            self._ensure_sse_worker()
        except Exception as _exc:
            logger.debug(f"[RELOAD] SSE worker init failed: {_exc}")

    # ------------------------------------------------------------------
    # SSE live-updates
    # ------------------------------------------------------------------
    def _ensure_sse_worker(self):
        """Start the SSE consumer if not already running. Idempotent
        across multiple reload_devices_from_server() calls — reused so
        we don't stack workers when the operator clicks F5 repeatedly."""
        # Guard against a tombstoned QThread wrapper (same pattern the
        # topology tab uses).
        try:
            if self._sse_worker is not None:
                if self._sse_worker.isRunning():
                    return
                self._sse_worker = None
        except RuntimeError:
            self._sse_worker = None

        # Pick the first online server as the event source. With
        # multi-server deployments we'd want one worker per server,
        # but today the single-server case is the operational norm.
        servers = getattr(self.main_window, "server_interfaces", []) or []
        url_base = None
        for s in servers:
            if s.get("online", True):
                url_base = (s.get("address") or "").rstrip("/")
                if url_base:
                    break
        if not url_base:
            return

        try:
            from utils.sse_client import SSEWorker
        except Exception as exc:
            logger.debug(f"[DEVICES] SSE client unavailable: {exc}")
            return

        worker = SSEWorker(f"{url_base}/api/events/stream")
        worker.event.connect(self._on_sse_event)
        worker.disconnected.connect(self._on_sse_disconnected)
        # No finished→deleteLater (QThread teardown race → SIGABRT on
        # PyQt5 5.15.11 + Python 3.14). Global keepalive owns lifetime.
        try:
            from utils.qthread_keepalive import keep
            keep(worker)
        except Exception:
            pass
        self._sse_worker = worker
        worker.start()
        logger.debug(f"[DEVICES] SSE worker started → {url_base}")

    def _on_sse_event(self, event_type: str, payload: dict):
        """Server-pushed event handler. Coalesces multiple events
        within a 500 ms window into a single reload so a fabric-wide
        flap doesn't cascade dozens of /devices fetches.

        Events that warrant a refresh:
          state_transition  — protocol state changed (BGP/OSPF/ARP/…)
          device_applied    — someone (or us) applied a device
          device_started    — lifecycle: started
          device_stopped    — lifecycle: stopped
          device_removed    — lifecycle: removed

        Other events (stream_*, heartbeat) we ignore here — the
        Streams tab can hook them itself.
        """
        if event_type not in {
            "state_transition", "device_applied", "device_started",
            "device_stopped", "device_removed",
        }:
            return
        if self._sse_refresh_pending:
            return
        self._sse_refresh_pending = True
        from PyQt5.QtCore import QTimer

        def _fire():
            self._sse_refresh_pending = False
            try:
                self.reload_devices_from_server()
            except Exception as exc:
                logger.debug(f"[DEVICES] SSE-triggered reload failed: {exc}")

        QTimer.singleShot(500, _fire)

    def _on_sse_disconnected(self, reason: str):
        logger.debug(f"[DEVICES] SSE disconnect: {reason}")

    def populate_device_table(self):
        """Populate the device table from the data structure.

        Sets the populate-guard flag so add_device's per-cell setItem
        writes don't trigger on_cell_changed and falsely mark devices
        as _needs_apply=True. Same guard as update_device_table.
        """
        self._populating_devices_table = True
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
            
            logger.debug(f"Populated table with {self.devices_table.rowCount()} devices")

        except Exception as e:
            logger.error(f"Failed to populate device table: {e}")
            logging.error(f"Failed to populate device table: {e}")
        finally:
            # Restore signal flow so genuine user inline-edits are
            # caught by on_cell_changed (audit HIGH #2 handler).
            self._populating_devices_table = False

    # ---------- Dialogs / actions ----------
    def apply_selected_device(self):
        """[ORPHAN — DO NOT CALL DIRECTLY] Apply selected devices SYNCHRONOUSLY.

        Audit MED #10: this method runs a fully-synchronous for-loop
        with requests.post(timeout=30) per device — applying 10 devices
        freezes the UI for up to 5 minutes. The Apply button is NOT
        wired here (it goes through apply_selected_device_with_arp →
        ... → apply_selected_device_silent, which uses the
        MultiDeviceApplyWorker QThread). This sync method has no
        in-tree callers and is preserved only as a defensive
        emergency path if someone reaches it via getattr or a
        dynamic UI binding.

        For programmatic apply, call apply_selected_device_silent()
        instead — same logic, runs in a worker thread with parallel
        per-device execution (ThreadPoolExecutor, max_workers=5).
        """
        logger.warning(
            "[DEPRECATED] apply_selected_device() called directly. "
            "This blocks the UI thread. Use "
            "apply_selected_device_silent() (worker-threaded) instead."
        )
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

            # Get server URL from device_info (using ServerManager if available)
            server_url = self.get_server_url(device_info=device_info)
            if not server_url:
                results.append(f"❌ {device_name}: No server URL found for device")
                failed_count += 1
                continue

            # Always check and reconfigure selected devices (regardless of _needs_apply flag)
            # This ensures devices are properly configured after UI restart
            logger.debug(f"Checking and reconfiguring device '{device_name}' (selected by user)")

            # Update UI to show starting status immediately
            self._set_device_status_starting(row, device_info, status_text="Starting configuration...")

            try:
                # Use the appropriate method based on whether device is new or existing
                if device_info.get("_is_new", False):
                    # New device - use _add_device_to_server which has proper protocol handling
                    logger.debug(f"Adding new device '{device_name}' to server")
                    if self._add_device_to_server(server_url, device_info):
                        success = True
                    else:
                        success = False
                else:
                    # Existing device - use _apply_device_to_server
                    logger.debug(f"Applying existing device '{device_name}' to server")
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
        
        # Check if any applied devices had VXLAN configuration
        vxlan_applied = False
        if successful_count > 0:
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
                    protocols = device_info.get("protocols", [])
                    vxlan_config = device_info.get("vxlan_config", {})
                    # Check for VXLAN in multiple formats
                    has_vxlan = False
                    if "VXLAN" in protocols:
                        has_vxlan = True
                    elif isinstance(vxlan_config, dict):
                        # Check for new format: {"tunnels": [...]}
                        if "tunnels" in vxlan_config and len(vxlan_config.get("tunnels", [])) > 0:
                            has_vxlan = True
                        # Check for old format: single dict with vni key
                        elif vxlan_config.get("vni") or len(vxlan_config) > 0:
                            has_vxlan = True
                    
                    if has_vxlan:
                        logger.debug(f"Detected VXLAN in device {device_name}")
                        vxlan_applied = True
                        break
        
        # Save session after device application to persist status changes
        if successful_count > 0 and hasattr(self.main_window, "save_session"):
            logger.debug(f"Saving session after successful device application ({successful_count} device(s) applied)")
            try:
                self.main_window.save_session()
                logger.debug(f"✅ Session saved successfully after applying {successful_count} device(s)")
            except Exception as save_exc:
                logger.debug(f"⚠️ Failed to save session: {save_exc}")
        
        # Interface list refresh is now manual only - user can click "Refresh Interface List" button if needed
        # Removed automatic refresh to prevent unnecessary UI updates
        # if vxlan_applied and hasattr(self.main_window, "update_server_tree"):
        #     print(f"[DEBUG APPLY] Refreshing interface list from server after VXLAN tunnel creation")
        #     self.main_window.update_server_tree()
    
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

            server_url = self._get_server_url_from_interface(device_info.get("Interface", ""), device_info=device_info)
            if not server_url:
                results.append(f"❌ {device_name}: No server URL found for interface")
                failed_count += 1
                continue

            try:
                # Send device_id so the server can scope the ping to
                # the device's VRF (multi-device wiring). Older
                # servers ignore the extra field and ping from the
                # default netns, which still works for single-device
                # deployments.
                response = requests.post(
                    f"{server_url}/api/device/ping",
                    json={
                        "ip_address": ping_target,
                        "device_id": device_info.get("device_id", ""),
                    },
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
            logger.error(f"Error: {exc}")

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
                logger.info("Apply operation already running, skipping new request")
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
                logger.debug(f"Will apply device '{device_name}' (selected by user)")

        if not devices_to_apply:
            logger.info("No valid devices found to apply")
            return

        # Create and start multi-device apply worker
        self.multi_device_apply_worker = MultiDeviceApplyWorker(devices_to_apply, server_url, self)

        # Set operation type flag for this operation
        self._current_operation_type = 'apply'

        self.multi_device_apply_worker.device_applied.connect(self._on_multi_device_applied)
        self.multi_device_apply_worker.progress.connect(self._on_multi_device_progress)
        self.multi_device_apply_worker.finished.connect(self._on_multi_device_apply_finished)

        # Show the inline progress widget for the duration of this apply.
        self._show_apply_progress(len(devices_to_apply))

        self.multi_device_apply_worker.start()

        logger.info(f"Started applying {len(devices_to_apply)} devices in background")

    def _show_apply_progress(self, total: int) -> None:
        """Reveal the inline progress widget with `total` devices in flight."""
        if not hasattr(self, "_apply_progress_bar"):
            return
        self._apply_progress_total = max(1, int(total))
        self._apply_progress_done = 0
        self._apply_progress_bar.setRange(0, self._apply_progress_total)
        self._apply_progress_bar.setValue(0)
        self._apply_progress_label.setText(f"Applying 0/{self._apply_progress_total}")
        self._apply_progress_label.setVisible(True)
        self._apply_progress_bar.setVisible(True)

    def _tick_apply_progress(self) -> None:
        """Bump the progress counter by one — called per per-device result."""
        if not hasattr(self, "_apply_progress_bar"):
            return
        if not self._apply_progress_bar.isVisible():
            return
        self._apply_progress_done = min(
            getattr(self, "_apply_progress_done", 0) + 1,
            getattr(self, "_apply_progress_total", 1),
        )
        self._apply_progress_bar.setValue(self._apply_progress_done)
        self._apply_progress_label.setText(
            f"Applying {self._apply_progress_done}/{self._apply_progress_total}"
        )

    def _hide_apply_progress(self) -> None:
        """Tear down the inline progress widget — called from the finished
        handler. Always safe to call (idempotent)."""
        if not hasattr(self, "_apply_progress_bar"):
            return
        self._apply_progress_bar.setVisible(False)
        self._apply_progress_label.setVisible(False)

    def _retry_failed_apply(self, failed_devices):
        """Re-apply just the devices that failed in the previous batch.

        Triggered from the "Retry Failed" button in the apply-failure
        dialog. Spawns a fresh MultiDeviceApplyWorker with only the
        failed (row, device_info) tuples — same plumbing as a normal
        apply, including the inline progress widget and the failure
        dialog (which becomes recursive: retry again, retry again,
        etc., until the user clicks OK or all pass).
        """
        if not failed_devices:
            return
        server_url = self.get_server_url()
        if not server_url:
            return

        # If a worker is somehow still around, refuse — same guard
        # the original apply path uses.
        if hasattr(self, "multi_device_apply_worker") and self.multi_device_apply_worker:
            if self.multi_device_apply_worker.isRunning() or not self.multi_device_apply_worker.isFinished():
                logger.info("[RETRY] apply still running; skipping")
                return

        logger.info(f"[RETRY] re-applying {len(failed_devices)} failed device(s)")

        # Mark them back to "Starting…" so the row state matches.
        for row, device_info in failed_devices:
            try:
                self._set_device_status_starting(
                    row, device_info, status_text="Retrying configuration…",
                )
            except Exception as _exc:
                logger.debug(f"[RETRY] status reset failed for row {row}: {_exc}")

        self.multi_device_apply_worker = MultiDeviceApplyWorker(
            failed_devices, server_url, self,
        )
        self._current_operation_type = "apply"
        self.multi_device_apply_worker.device_applied.connect(self._on_multi_device_applied)
        self.multi_device_apply_worker.progress.connect(self._on_multi_device_progress)
        self.multi_device_apply_worker.finished.connect(self._on_multi_device_apply_finished)
        self._show_apply_progress(len(failed_devices))
        self.multi_device_apply_worker.start()

    def _apply_device_filter(self, text: str = "") -> None:
        """Hide rows whose Name / Interface / IPv4 / IPv6 / MAC don't
        contain `text` (case-insensitive). Empty text shows all rows.

        Called on every keystroke from the filter QLineEdit. Iterates
        the visible table only; safe to call frequently.
        """
        needle = (text or "").strip().lower()
        if not hasattr(self, "devices_table"):
            return

        # Columns to scan. Some installs may not have every column, so
        # look up indexes via self.COL and skip missing ones.
        scan_cols = []
        for col_name in ("Device Name", "Interface", "IPv4", "IPv6", "MAC Address"):
            idx = self.COL.get(col_name) if hasattr(self, "COL") else None
            if idx is not None:
                scan_cols.append(idx)

        if not scan_cols:
            return

        for row in range(self.devices_table.rowCount()):
            if not needle:
                self.devices_table.setRowHidden(row, False)
                continue
            match = False
            for col_idx in scan_cols:
                item = self.devices_table.item(row, col_idx)
                if not item:
                    continue
                if needle in item.text().lower():
                    match = True
                    break
            self.devices_table.setRowHidden(row, not match)

    def _refresh_monitor_health(self):
        """Poll /api/monitors/health in the background and repaint the
        monitor-health indicator. Off-UI HTTP so we never block on a
        slow/offline server."""
        if not hasattr(self, "_monitor_health_label"):
            return
        server_url = self.get_server_url(silent=True)
        if not server_url:
            self._monitor_health_label.setText("monitors: ?")
            self._monitor_health_label.setStyleSheet(
                "color: #9ca3af; font-size: 11px; padding: 0 6px;"
            )
            self._monitor_health_label.setToolTip("No server selected")
            return

        from PyQt5.QtCore import QThread, pyqtSignal

        class _HealthWorker(QThread):
            done = pyqtSignal(bool, object, str)  # (ok, payload_dict_or_None, err_msg)

            def __init__(self, url):
                super().__init__()
                self._url = url

            def run(self):
                try:
                    import requests
                    r = requests.get(f"{self._url}/api/monitors/health", timeout=3)
                    if r.status_code == 200:
                        self.done.emit(True, r.json(), "")
                    else:
                        self.done.emit(False, None, f"HTTP {r.status_code}")
                except Exception as exc:
                    self.done.emit(False, None, str(exc))

        worker = _HealthWorker(server_url)
        if not hasattr(self, "_monitor_health_workers"):
            self._monitor_health_workers = []
        self._monitor_health_workers.append(worker)

        def _apply(ok, payload, err, w=worker):
            try:
                if not ok or not payload:
                    self._monitor_health_label.setText("monitors: ?")
                    self._monitor_health_label.setStyleSheet(
                        "color: #9ca3af; font-size: 11px; padding: 0 6px;"
                    )
                    self._monitor_health_label.setToolTip(
                        f"Health endpoint unreachable: {err}" if err else "no data"
                    )
                    return

                overall_ok = bool(payload.get("ok"))
                monitors = payload.get("monitors") or {}
                # Identify which monitors are off (not running or stale).
                offenders = []
                for name, info in monitors.items():
                    running = info.get("running", False)
                    stale = info.get("stale", False)
                    if not running:
                        offenders.append(f"{name.upper()} down")
                    elif stale:
                        secs = info.get("stale_secs")
                        offenders.append(
                            f"{name.upper()} stale ({int(secs)}s)" if secs else f"{name.upper()} stale"
                        )

                if overall_ok and not offenders:
                    self._monitor_health_label.setText("monitors: OK")
                    self._monitor_health_label.setStyleSheet(
                        "color: #16a34a; font-size: 11px; padding: 0 6px;"
                    )
                    self._monitor_health_label.setToolTip(
                        "All background monitors running and reporting on time."
                    )
                else:
                    self._monitor_health_label.setText(f"monitors: ⚠ {len(offenders)}")
                    self._monitor_health_label.setStyleSheet(
                        "color: #d97706; font-size: 11px; padding: 0 6px; font-weight: 600;"
                    )
                    self._monitor_health_label.setToolTip(
                        "Click to re-poll. Issues:\n• " + "\n• ".join(offenders)
                    )
            except Exception as exc:
                logger.debug(f"[MONITOR HEALTH] apply failed: {exc}")

        worker.done.connect(_apply)
        worker.finished.connect(
            lambda w=worker: self._monitor_health_workers.remove(w)
            if w in self._monitor_health_workers else None
        )
        worker.start()
    
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
        logger.info(f"Setting status to 'Applying...' for {len(selected_rows)} devices")
        for row in selected_rows:
            try:
                self._set_device_status_starting(row, device_info=None, status_text="Starting configuration...")
            except Exception as e:
                logger.error(f"Exception setting status for row {row}: {e}")
        
        logger.info(f"Apply button clicked - will apply {len(selected_rows)} devices and then run ARP operations")
        
        # Use the existing chain method
        self.apply_selected_device_with_arp_chain()

    def apply_selected_device_with_arp_chain(self):
        """Apply selected devices and then run ARP operations if pending."""
        # Check if ARP operation is already running - use more robust check.
        # Guard against a deleted C++ wrapper: a previous worker's C++
        # object may have been released (cleanup path's deleteLater, or
        # the keepalive registry dropping its ref) while this Python
        # attribute still points at the dead wrapper. Touching
        # .isRunning() on it then raises "RuntimeError: wrapped C/C++
        # object ... has been deleted", which — unhandled in a Qt slot —
        # aborts the process. Treat a dead/finished worker as "free to
        # proceed".
        existing = getattr(self, 'arp_operation_worker', None)
        if existing is not None:
            try:
                _busy = existing.isRunning() or not existing.isFinished()
            except RuntimeError:
                # C++ side already gone — stale wrapper, not busy.
                _busy = False
            if _busy:
                logger.info("ARP operation already running, skipping new request")
                return
            # Finished (or a dead wrapper) — just release our reference.
            # Deliberately NOT calling deleteLater() here: if the worker
            # only just finished, its QThread teardown may still be
            # settling and deleteLater would destroy the C++ object
            # mid-teardown → "QThread: Destroyed while thread is still
            # running" SIGABRT. Lifetime is owned by the global keepalive
            # registry, which deletes safely once the race window has
            # closed. Clearing the attribute is all we need here.
            try:
                delattr(self, 'arp_operation_worker')
            except Exception:
                self.arp_operation_worker = None
        
        # First run the apply operation silently (without showing dialog)
        self.apply_selected_device_silent()
        
        # Check if there are pending ARP operations
        if hasattr(self, '_pending_arp_rows') and self._pending_arp_rows:
            logger.info(f"Apply completed, now starting ARP operations for {len(self._pending_arp_rows)} devices...")
            
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
                    logger.info(f"Device {device_name} not found in data structure")

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
                
                logger.info(f"Starting ARP requests for {len(devices_to_process)} devices in background...")
            else:
                logger.info(f"No valid devices found for ARP operation")
            
            # Don't clear pending ARP rows here - they will be cleared when ARP operation finishes
            # delattr(self, '_pending_arp_rows')
    
    def validate_cell_value(self, header_name, value, row=None, column=None):
        """Validate edited table cell values."""
        try:
            if header_name == "Device Name":
                # Audit LOW #15: device name flows into shell-like
                # contexts on the server (FRR container names, sysctl
                # commands). Restrict to a safe charset so an inline
                # edit can't slip in a `;` or space that the
                # AddDeviceDialog already rejects.
                if not (0 < len(value) <= 64):
                    return False
                import re as _re
                return bool(_re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", value))

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
                    # Audit MED #12: the previous except branch did
                    # `return True` on parse failure, silently accepting
                    # the gateway as valid even if the IPv4 or mask
                    # cells were malformed. We can't actually verify
                    # subnet membership if we can't parse the IP or
                    # mask, so the safe call is to LEAVE the result
                    # at "valid format" (the gateway itself parsed
                    # above) and skip the membership check. Same
                    # net behavior but without claiming "yes valid"
                    # explicitly — fall through to the final return
                    # True below.
                    try:
                        ip_text = ipv4_item.text().strip() if ipv4_item else ""
                        mask_text = mask_item.text().strip() if mask_item else ""
                        if ip_text and mask_text:
                            ip_addr = ipaddress.IPv4Address(ip_text)
                            mask = int(mask_text)
                            network = ipaddress.IPv4Network(
                                f"{ip_addr}/{mask}", strict=False
                            )
                            if gateway_ip not in network:
                                return False
                    except (ipaddress.AddressValueError, ValueError):
                        # Can't determine subnet — leave the gateway
                        # at "format-valid" without asserting
                        # membership. Fall through.
                        pass
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
                    # Same fix as IPv4 Gateway above (audit MED #12).
                    try:
                        ip_text = ipv6_item.text().strip() if ipv6_item else ""
                        mask_text = mask_item.text().strip() if mask_item else ""
                        if ip_text and mask_text:
                            ip_addr = ipaddress.IPv6Address(ip_text)
                            mask = int(mask_text)
                            network = ipaddress.IPv6Network(
                                f"{ip_addr}/{mask}", strict=False
                            )
                            if gateway_ip not in network:
                                return False
                    except (ipaddress.AddressValueError, ValueError):
                        pass
                return True

            if header_name == "MAC Address":
                if not value:
                    return True
                import re
                return bool(re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", value))

            if header_name == "Status":
                # Audit MED #12: this used to return False
                # unconditionally — every inline edit to the Status
                # column would be silently reverted. Status is meant
                # to be a server-driven display field (Running /
                # Stopped / Starting), not a user-editable one;
                # rejecting all edits is technically correct but is
                # confusing because the cell still LOOKS editable.
                # Keep returning False (don't accept user edits) but
                # do it explicitly + with a debug log so future
                # readers know it's intentional. Long-term fix is
                # to mark the cell non-editable in update_device_table.
                logger.debug(
                    "[validate_cell_value] Rejected user edit to Status "
                    "column (display-only, server-driven)"
                )
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
        """Refresh ARP status for the selected device(s).

        Two steps:

        1. Ask the server to run a *fresh* ARP/ND probe and persist
           the results. Without this, _check_individual_arp_resolution
           below would just re-read whatever the periodic 30s monitor
           last wrote — clicking the button would silently show stale
           data, which is exactly the failure mode the user reported.
        2. Read the (now-fresh) state from the DB and paint the row.

        Step 1 hits the server's force-check endpoint, which uses the
        VRF-aware probe path (see run_tgen_server.get_device_arp_status)
        so multi-device deployments resolve correctly.
        """
        selected_items = self.devices_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
            return

        selected_rows = {item.row() for item in selected_items}
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select one or more devices to refresh ARP status.")
            return

        # Step 1 — kick a server-side force-check so the DB picks up
        # the live ARP/ND state instead of whatever the periodic
        # monitor last cached. Best-effort: failures don't block the
        # subsequent DB read (we'll show whatever's in the DB).
        try:
            import requests
            # Use the first selected device's row to discover the
            # server URL — all rows are typically on the same server.
            first_row = next(iter(selected_rows))
            first_name_item = self.devices_table.item(first_row, self.COL["Device Name"])
            server_url = None
            if first_name_item:
                first_dev = self._find_device_by_name(first_name_item.text())
                if first_dev:
                    iface_label = first_dev.get("Interface", "")
                    if iface_label:
                        server_url = self._get_server_url_from_interface(iface_label)
            if not server_url:
                server_url = self.get_server_url(silent=True)
            if server_url:
                # Timeout sized for the worst case: force-check probes
                # every running device in parallel, each ping has a 1s
                # timeout, so even 10+ devices should return in <10s.
                fc = requests.post(f"{server_url}/api/arp/monitor/force-check", timeout=15)
                if fc.status_code == 200:
                    logger.info(f"[REFRESH ARP] force-check OK for {len(selected_rows)} selected device(s)")
                else:
                    logger.warning(f"[REFRESH ARP] force-check returned HTTP {fc.status_code}")
            else:
                logger.warning("[REFRESH ARP] no server URL — falling back to cached DB state only")
        except Exception as exc:
            logger.warning(f"[REFRESH ARP] force-check failed (will show cached state): {exc}")

        # Step 2 — read the now-fresh state and paint each row.
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
                logger.info(f"{device_name}: {overall_status}")
            except Exception as exc:
                logger.error(f"Error for {device_name}: {exc}")

    def send_immediate_arp_request(self, device_info, server_url):
        """Compatibility shim - ARP operations are handled by the server-side monitor."""
        return True, "ARP handled by server monitor"

    def send_arp_request(self, device_info):
        """Compatibility shim - ARP operations are handled by the server-side monitor."""
        return True, "ARP handled by server monitor"

    def _calculate_changes(self):
        """Calculate changes between current state and last saved session."""
        logger.debug(f"Calculating changes since last session")
        
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
        
        logger.debug(f"Current devices: {len(current_devices)}")
        logger.debug(f"Last saved devices: {len(last_saved_devices)}")
        
        # First, find devices to remove (devices marked for removal)
        devices_to_remove_names = set()
        if hasattr(self.main_window, 'devices_to_remove'):
            for removal_info in self.main_window.devices_to_remove:
                changes['to_remove'].append(removal_info['device_info'])
                devices_to_remove_names.add(removal_info['name'])
                logger.debug(f"Device marked for removal: '{removal_info['name']}'")
        
        # Find devices to add (new devices that need to be applied to server)
        # Exclude devices that are marked for removal
        for device_name, device_info in current_devices.items():
            if device_name not in devices_to_remove_names and (device_info.get("_is_new", False) or device_info.get("_needs_apply", False)):
                changes['to_add'].append(device_info)
                logger.debug(f"Device to add: '{device_name}'")
        
        return changes
    def _add_device_to_server(self, server_url, device_info, force_reconfigure=False):
        """Add a single device to the server."""
        try:
            device_name = device_info.get("Device Name", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            if force_reconfigure:
                logger.debug(f"Force reconfiguring device '{device_name}' to server: {server_url}")
            else:
                logger.debug(f"Adding device '{device_name}' to server: {server_url}")
            
            # Check what's currently configured on the server and compare with intended config
            logger.debug(f"Checking existing configuration on server for '{device_name}'")
            
            # Check if we need to clean up old VLAN configuration (e.g., VLAN changed)
            old_config = device_info.get("_old_config")
            if old_config and old_config.get("vlan") != device_info.get("VLAN", "0"):
                old_vlan = old_config.get("vlan", "0")
                old_interface = old_config.get("interface", "")
                if old_vlan != "0" and old_interface:
                    old_iface_norm = self._normalize_iface_label(old_interface)
                    logger.debug(f"VLAN changed from {old_vlan} to {device_info.get('VLAN', '0')} - cleaning up old VLAN interface")
                    
                    # Clean up the old VLAN interface
                    old_cleanup_payload = {
                        "interface": old_iface_norm,
                        "vlan": old_vlan,
                        "cleanup_only": True,
                        "remove_vlan": True  # Special flag to remove the entire VLAN interface
                    }
                    
                    old_cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=old_cleanup_payload, timeout=10)
                    if old_cleanup_resp.status_code == 200:
                        logger.debug(f"Successfully cleaned up old VLAN interface vlan{old_vlan}@{old_iface_norm}")
                    else:
                        logger.debug(f"Failed to clean up old VLAN interface: {old_cleanup_resp.status_code} - {old_cleanup_resp.text}")
            
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
            
            logger.debug(f"Intended configuration: {intended_ips}")
            
            # Check what's currently configured on the server
            check_payload = {
                "interface": iface_norm,
                "vlan": device_info.get("VLAN", "0"),
                "check_only": True  # Just check, don't modify
            }
            
            logger.debug(f"Sending check request to server: {server_url}")
            check_resp = requests.post(f"{server_url}/api/device/check", json=check_payload, timeout=10)
            existing_ips = []
            if check_resp.status_code == 200:
                check_data = check_resp.json()
                existing_ips = check_data.get("existing_ips", [])
                logger.debug(f"Found existing IPs on server: {existing_ips}")
            else:
                logger.debug(f"Could not check existing configuration on {server_url}: {check_resp.status_code} - {check_resp.text}")
            
            # Compare intended vs existing configuration
            intended_set = set(intended_ips)
            existing_set = set(existing_ips)
            
            if intended_set == existing_set and not force_reconfigure:
                logger.debug(f"Configuration matches - no changes needed")
                # Configuration is already correct, just mark as applied
                device_info["_is_new"] = False
                device_info["_needs_apply"] = False
                device_info["Status"] = "Running"
                return True
            else:
                if force_reconfigure:
                    logger.debug(f"Force reconfiguration requested - reapplying configuration")
                else:
                    logger.debug(f"Configuration differs - need to reapply")
                logger.debug(f"Missing on server: {intended_set - existing_set}")
                logger.debug(f"Extra on server: {existing_set - intended_set}")
                
                # Clean up existing configuration before applying new one
                logger.debug(f"Cleaning up existing configuration for '{device_name}'")
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
                        logger.debug(f"Successfully cleaned up existing IPs: {removed_ips}")
                    else:
                        logger.debug(f"Interface was already clean - no IPs to remove")
                else:
                    logger.debug(f"Cleanup failed for '{device_name}': {cleanup_resp.status_code} - {cleanup_resp.text}")
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
                    logger.debug(f"Successfully applied new configuration for '{device_name}'")
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
                    logger.error(f"Failed to add '{device_name}': {resp.status_code} - {resp.text}")
                    return False
                
        except Exception as e:
            logger.error(f"Exception adding device '{device_name}' to server '{server_url}': {e}")
            return False
    
    def _apply_device_to_server(self, server_url, device_info):
        """Apply device configuration using the new /api/device/apply endpoint in background."""
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            logger.debug(f"Starting apply for {device_name}")
            logger.debug(f"Device info keys: {list(device_info.keys())}")
            logger.debug(f"Protocols: {device_info.get('protocols', [])}")
            logger.debug(f"BGP config: {device_info.get('bgp_config', {})}")
            logger.debug(f"OSPF config: {device_info.get('ospf_config', {})}")
            logger.debug(f"ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
            
            # If device has an ID, fetch complete device data from database
            if device_id:
                try:
                    import requests
                    logger.debug(f"Fetching complete device data from database for {device_name}")
                    response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=5)
                    if response.status_code == 200:
                        db_device_data = response.json()
                        logger.debug(f"Database device data keys: {list(db_device_data.keys())}")
                        
                        db_protocols = self._convert_protocols_to_array(db_device_data.get("protocols", []))
                        existing_protocols = self._convert_protocols_to_array(device_info.get("protocols", []))
                        protocols_list = existing_protocols or db_protocols

                        if not device_info.get("bgp_config"):
                            device_info["bgp_config"] = db_device_data.get("bgp_config", {})
                        if not device_info.get("ospf_config"):
                            device_info["ospf_config"] = db_device_data.get("ospf_config", {})
                        if not device_info.get("isis_config"):
                            device_info["isis_config"] = db_device_data.get("isis_config", {}) or db_device_data.get("is_is_config", {})
                        # Preserve local vxlan_config if it exists (especially for multiple tunnels)
                        # Only use DB config if local doesn't have any
                        local_vxlan = device_info.get("vxlan_config", {})
                        logger.debug(f"Local vxlan_config type: {type(local_vxlan)}, content: {local_vxlan}")
                        if isinstance(local_vxlan, dict) and "tunnels" in local_vxlan:
                            tunnel_count = len(local_vxlan.get("tunnels", []))
                            logger.debug(f"Local vxlan_config has {tunnel_count} tunnel(s) in tunnels list")
                        if not local_vxlan or (isinstance(local_vxlan, dict) and len(local_vxlan) == 0):
                            device_info["vxlan_config"] = db_device_data.get("vxlan_config", {})
                            logger.debug(f"Using DB vxlan_config (local was empty)")
                        else:
                            # Local config exists - preserve it (it has the multiple tunnels format)
                            tunnel_count = len(local_vxlan.get('tunnels', [])) if isinstance(local_vxlan, dict) and 'tunnels' in local_vxlan else 1
                            logger.debug(f"Preserving local vxlan_config with {tunnel_count} tunnel(s)")
                            logger.debug(f"Local vxlan_config keys: {list(local_vxlan.keys()) if isinstance(local_vxlan, dict) else 'N/A'}")

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
                        
                        logger.debug(f"Updated device info - Protocols: {device_info.get('protocols', [])}")
                        logger.debug(f"Updated device info - BGP config: {device_info.get('bgp_config', {})}")
                        logger.debug(f"Updated device info - OSPF config: {device_info.get('ospf_config', {})}")
                        logger.debug(f"Updated device info - ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
                    else:
                        logger.debug(f"Failed to fetch device data from database: {response.status_code}")
                except Exception as e:
                    logger.debug(f"Error fetching device data from database: {e}")
            
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

            # Handle multiple tunnels format: {"tunnels": [tunnel1, tunnel2, ...]}
            vxlan_config = device_info.get("vxlan_config", {})
            logger.debug(f"Before processing, vxlan_config type: {type(vxlan_config)}, keys: {list(vxlan_config.keys()) if isinstance(vxlan_config, dict) else 'N/A'}")
            if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
                # Multiple tunnels format - process each tunnel
                tunnels = vxlan_config.get("tunnels", [])
                logger.debug(f"Found {len(tunnels)} tunnel(s) in vxlan_config")
                processed_tunnels = []
                for idx, tunnel in enumerate(tunnels):
                    logger.debug(f"Processing tunnel {idx+1}/{len(tunnels)}: VNI={tunnel.get('vni') if isinstance(tunnel, dict) else 'N/A'}, keys={list(tunnel.keys()) if isinstance(tunnel, dict) else 'N/A'}")
                    if isinstance(tunnel, dict):
                        # Ensure required fields are present before processing
                        if not tunnel.get("vni") and not tunnel.get("VNI"):
                            logger.debug(f"Tunnel {idx+1} missing VNI, skipping")
                            continue
                        processed_tunnel = self._with_vxlan_interfaces(
                            tunnel,
                            iface_label,
                            device_info.get("VLAN", "0"),
                            device_id=device_info.get("device_id"),
                        )
                        if processed_tunnel:
                            processed_tunnels.append(processed_tunnel)
                            logger.debug(f"Tunnel {idx+1} processed successfully, VNI={processed_tunnel.get('vni')}")
                        else:
                            # If processing failed but tunnel has VNI, preserve it anyway
                            if tunnel.get("vni") or tunnel.get("VNI"):
                                logger.debug(f"Tunnel {idx+1} processing returned empty but has VNI, preserving original")
                                # Add interface info manually
                                tunnel_copy = dict(tunnel)
                                iface_norm = self._normalize_iface_label(iface_label)
                                vlan_str = str(device_info.get("VLAN", "0") or "0")
                                overlay_iface = iface_norm
                                if vlan_str and vlan_str != "0":
                                    overlay_iface = f"vlan{vlan_str}"
                                tunnel_copy["underlay_interface"] = iface_norm
                                tunnel_copy["overlay_interface"] = overlay_iface
                                processed_tunnels.append(tunnel_copy)
                            else:
                                logger.debug(f"Tunnel {idx+1} processing returned empty and no VNI, skipping")
                    else:
                        logger.debug(f"Tunnel {idx+1} is not a dict, skipping")
                if processed_tunnels:
                    device_info["vxlan_config"] = {"tunnels": processed_tunnels}
                    if "VXLAN" not in protocols_list:
                        protocols_list.append("VXLAN")
                    logger.debug(f"Processing {len(processed_tunnels)} VXLAN tunnel(s)")
                else:
                    logger.debug(f"No tunnels processed successfully, clearing vxlan_config")
                    device_info["vxlan_config"] = {}
            else:
                # Single tunnel format (backward compatibility)
                vxlan_config = self._with_vxlan_interfaces(
                    vxlan_config,
                    iface_label,
                    device_info.get("VLAN", "0"),
                    device_id=device_info.get("device_id"),
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
            
            logger.debug(f"Payload protocols: {payload['protocols']}")
            logger.debug(f"Payload BGP config: {payload['bgp_config']}")
            logger.debug(f"Payload OSPF config: {payload['ospf_config']}")
            logger.debug(f"Payload ISIS config: {payload['isis_config']}")
            
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
            logger.error(f"Exception starting device apply for '{device_name}': {e}")
            return False
    
    def _apply_device_to_server_sync(self, server_url, device_info):
        """Apply device configuration synchronously (for use in background workers)."""
        import requests
        
        try:
            device_name = device_info.get("Device Name", "")
            device_id = device_info.get("device_id", "")
            iface_label = device_info.get("Interface", "")
            iface_norm = self._normalize_iface_label(iface_label)
            
            logger.debug(f"Starting device apply for {device_name}")
            logger.debug(f"Device info keys: {list(device_info.keys())}")
            logger.debug(f"Protocols: {device_info.get('protocols', [])}")
            logger.debug(f"BGP config: {device_info.get('bgp_config', {})}")
            logger.debug(f"OSPF config: {device_info.get('ospf_config', {})}")
            logger.debug(f"ISIS config: {device_info.get('isis_config', {})} or {device_info.get('is_is_config', {})}")
            
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
                device_id=device_info.get("device_id"),
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
                "mtu": device_info.get("MTU", "1500"),  # MTU field, default to 1500
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
            logger.debug(f"Calling /api/device/apply with payload: {basic_payload}")
            response = requests.post(f"{server_url}/api/device/apply", json=basic_payload, timeout=30)
            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict) and "error" in error_data:
                        error_msg = error_data["error"]
                    elif isinstance(error_data, str):
                        error_msg = error_data
                except Exception:
                    error_msg = response.text[:200] if response.text else error_msg
                logger.error(f"Failed to apply basic device configuration: {error_msg}")
                logger.error(f"Response status: {response.status_code}, body: {response.text[:500]}")
                # Store error message for display to user
                device_info["_apply_error"] = error_msg
                return False
            
            logger.info(f"Basic device configuration applied for {device_name}")

            # Step 2: Configure BGP if enabled
            protocols = device_info.get("protocols", [])
            bgp_config = device_info.get("bgp_config", {})

            # Promoted these from debug → info so the user can see in
            # the log exactly which protocols the device claims to
            # have configured. Common gotcha: a device added before
            # the BGP checkbox was unchecked (or pasted from a
            # BGP-enabled source) keeps "BGP" in its protocols list,
            # and apply tries to push a possibly-stale bgp_config
            # the user thought they'd cleared.
            logger.info(
                f"[APPLY DIAG] {device_name} protocols={protocols} "
                f"bgp_config_keys={list((bgp_config or {}).keys())}"
            )
            if "BGP" in protocols and bgp_config:
                logger.info(f"Configuring BGP for device {device_name}")
                bgp_success = self._apply_bgp_to_server_sync(server_url, device_info)
                if not bgp_success:
                    logger.error(f"Failed to configure BGP for device {device_name}")
                    # Stash a descriptive error if the BGP handler
                    # didn't already set one. Without this, the worker
                    # reports a generic "Unknown error" and the user
                    # has no idea which protocol step failed.
                    if not device_info.get("_apply_error"):
                        device_info["_apply_error"] = "BGP configuration failed (check server logs)"
                    return False
                logger.info(f"BGP configured for device {device_name}")
            else:
                logger.debug(f"BGP not configured - protocols: {protocols}, bgp_config: {bgp_config}")

            # Step 3: Configure OSPF if enabled
            ospf_config = device_info.get("ospf_config", {})

            logger.debug(f"Checking OSPF - protocols: {protocols}, ospf_config: {ospf_config}")
            if "OSPF" in protocols and ospf_config:
                logger.info(f"Configuring OSPF for device {device_name}")
                ospf_success = self._apply_ospf_to_server_sync(server_url, device_info)
                if not ospf_success:
                    logger.error(f"Failed to configure OSPF for device {device_name}")
                    if not device_info.get("_apply_error"):
                        device_info["_apply_error"] = "OSPF configuration failed (check server logs)"
                    return False
                logger.info(f"OSPF configured for device {device_name}")
            else:
                logger.debug(f"OSPF not configured - protocols: {protocols}, ospf_config: {ospf_config}")

            # Step 4: Configure ISIS if enabled
            # Get ISIS config - handle both isis_config and is_is_config keys
            isis_config = device_info.get("isis_config", {}) or device_info.get("is_is_config", {})
            
            logger.debug(f"Checking ISIS - protocols: {protocols}, isis_config: {isis_config}")
            if "IS-IS" in protocols and isis_config:
                logger.info(f"Configuring ISIS for device {device_name}")
                isis_success = self._apply_isis_to_server_sync(server_url, device_info)
                if not isis_success:
                    logger.error(f"Failed to configure ISIS for device {device_name}")
                    if not device_info.get("_apply_error"):
                        device_info["_apply_error"] = "ISIS configuration failed (check server logs)"
                    return False
                logger.info(f"ISIS configured for device {device_name}")
            else:
                logger.debug(f"ISIS not configured - protocols: {protocols}, isis_config: {isis_config}")

            return True

        except Exception as e:
            logger.error(f"Exception in sync device apply for '{device_name}': {e}")
            # Surface the exception text so the worker's "Unknown error"
            # message becomes something actionable.
            device_info["_apply_error"] = f"Exception: {e}"
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
            
            logger.debug(f"Removing '{device_name}' from data structure")
            
            # Remove from all_devices
            if iface_label in self.main_window.all_devices:
                self.main_window.all_devices[iface_label] = [
                    d for d in self.main_window.all_devices[iface_label] 
                    if d.get("device_id") != device_id
                ]
                
                # Remove empty interface
                if not self.main_window.all_devices[iface_label]:
                    del self.main_window.all_devices[iface_label]
                    logger.debug(f"Removed empty interface '{iface_label}'")
            
            # Remove from interface_to_device_map
            if device_name in self.interface_to_device_map:
                del self.interface_to_device_map[device_name]
                logger.debug(f"Removed '{device_name}' from device mapping")
                
        except Exception as e:
            logger.error(f"Failed to remove device from data structure: {e}")

    def _remove_device_from_server(self, device_info, device_id, device_name):
        """Invoke server APIs to clean up a removed device.

        Audit HIGH #5: this used to call `get_server_url(silent=True)`
        WITHOUT passing `device_info`, so the priority-1
        ServerManager-by-device lookup was skipped and resolution fell
        through to "currently selected TG" (main_window.server_url).
        Deleting a TG-1 device while a TG-0 port happened to be
        selected sent cleanup + remove POSTs to TG-0 — the actual
        TG-1 server kept the interface configured but the UI showed
        the device gone. Forwarding device_info now so the
        device-to-server affinity is honored.
        """
        try:
            logger.debug(f"Removing device '{device_name}' from server")

            server_url = self.get_server_url(silent=True, device_info=device_info)
            if not server_url:
                logger.debug("No server URL available")
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
            logger.debug(f"Calling cleanup API with payload: {cleanup_payload}")
            cleanup_resp = requests.post(f"{server_url}/api/device/cleanup", json=cleanup_payload, timeout=10)
            if cleanup_resp.status_code == 200:
                removed_ips = cleanup_resp.json().get("removed_ips", [])
                logger.debug(f"Successfully cleaned up IPs: {removed_ips}")
            else:
                logger.debug(f"Cleanup failed: {cleanup_resp.status_code} - {cleanup_resp.text}")

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
            logger.debug(f"Calling remove API with payload: {remove_payload}")
            remove_resp = requests.post(f"{server_url}/api/device/remove", json=remove_payload, timeout=10)
            if remove_resp.status_code == 200:
                logger.debug(f"Successfully removed device '{device_name}' from server")
            else:
                logger.debug(f"Remove API failed: {remove_resp.status_code} - {remove_resp.text}")

        except Exception as exc:
            logger.error(f"Failed to remove device '{device_name}' from server: {exc}")

    def prompt_add_device(self):
        """Open AddDeviceDialog, persist to model, refresh table."""
        selected_items = self.main_window.server_tree.selectedItems()
        if not selected_items or not selected_items[0].parent():
            QMessageBox.warning(self, "No Interface Selected",
                                "Please select a port under a server.")
            return

        # CRITICAL: Extract TG ID from custom widget in column 0 (same logic as VXLAN handler)
        parent_item = selected_items[0].parent()
        tg_id_widget = self.main_window.server_tree.itemWidget(parent_item, 0)
        tg_id = None
        if tg_id_widget:
            # Find the QLabel containing the TG ID text
            from PyQt5.QtWidgets import QLabel
            for child in tg_id_widget.findChildren(QLabel):
                text = child.text()
                if text.startswith("TG "):
                    tg_id = text.strip()
                    break
        
        # Fallback: extract from server_interfaces using parent index
        if not tg_id:
            parent_index = self.main_window.server_tree.indexOfTopLevelItem(parent_item)
            if parent_index >= 0 and hasattr(self.main_window, "server_interfaces"):
                if parent_index < len(self.main_window.server_interfaces):
                    server = self.main_window.server_interfaces[parent_index]
                    tg_id = f"TG {server.get('tg_id', '0')}"
        
        if not tg_id:
            QMessageBox.warning(self, "Invalid Selection",
                                "Could not determine TG ID from selected interface.")
            return
        
        port_name = selected_items[0].text(0).replace("• ", "").strip()  # Remove bullet prefix
        iface = f"{tg_id} - {port_name}"  # Match server tree format

        dialog = AddDeviceDialog(self, default_iface=iface)
        if dialog.exec_() != dialog.Accepted:
            return

        (
            device_name, iface_name, mac, ipv4, ipv6, ipv4_mask, ipv6_mask,
            vlan, mtu, ipv4_gateway, ipv6_gateway, incr_mac, incr_ipv4, incr_ipv6, incr_gateway, incr_vlan, incr_vxlan, incr_count, ospf_config, bgp_config, 
            dhcp_config, ipv4_octet_index, ipv6_hextet_index, mac_byte_index, gateway_octet_index, incr_dhcp_pool, dhcp_pool_octet_index,
            incr_loopback, loopback_ipv4_octet_index, loopback_ipv6_hextet_index, loopback_ipv4, loopback_ipv6, isis_config,
            vxlan_vni_increment_index, vxlan_local_octet_index, vxlan_remote_octet_index, vxlan_udp_increment_index
        ) = dialog.get_values()
        vxlan_config = dialog.get_vxlan_config()
        logger.debug(f"VXLAN config from dialog: {vxlan_config}")

        ipv4_mask = ipv4_mask or "24"
        ipv6_mask = ipv6_mask or "64"
        normalized_vxlan_config = self._normalize_vxlan_config(vxlan_config)
        logger.debug(f"Normalized VXLAN config: {normalized_vxlan_config}")

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
                    "MTU": mtu or "1500",  # MTU field, default to 1500
                    "Gateway": current_ipv4_gateway,  # Keep for backward compatibility
                    "IPv4 Gateway": current_ipv4_gateway,
                    "IPv6 Gateway": current_ipv6_gateway,
                    "Loopback IPv4": current_loopback_ipv4 if current_loopback_ipv4 else "",
                    "Loopback IPv6": current_loopback_ipv6 if current_loopback_ipv6 else "",
                    "Status": "Stopped",
                    "protocols": [],
                }
                
                # Add server information if ServerManager is available
                if hasattr(self.main_window, "server_manager"):
                    from utils.device_server_migration import DeviceServerMigration
                    # Extract TG ID from interface
                    tg_id = DeviceServerMigration.extract_tg_id_from_interface(iface)
                    if tg_id is not None:
                        server_url = self.main_window.server_manager.get_server_url(tg_id=tg_id)
                        if server_url:
                            device_data["server_url"] = server_url
                            device_data["tg_id"] = tg_id
                            device_data["server_id"] = DeviceServerMigration.extract_server_id_from_url(server_url)

                # Always include VXLAN config if it exists (even if incomplete)
                # This ensures VXLAN config is preserved when user enables VXLAN in UI
                if normalized_vxlan_config:
                    # Increment VXLAN fields if enabled
                    per_device_vxlan = normalized_vxlan_config.copy()
                    logger.debug(f"Processing VXLAN config for device {i+1}/{incr_count}: {per_device_vxlan}")
                    
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
                        device_id=device_data.get("device_id"),
                    )
                    # Convert single tunnel config to tunnels format for consistency
                    device_data["vxlan_config"] = {"tunnels": [per_device_vxlan]}
                    device_data["VXLAN"] = self._format_vxlan_summary(per_device_vxlan)
                    if "VXLAN" not in device_data["protocols"]:
                        device_data["protocols"].append("VXLAN")
                    logger.debug(f"Added VXLAN config to device {current_name} in tunnels format: {device_data['vxlan_config']}")
                else:
                    # Ensure vxlan_config is always present (even if empty) for consistency
                    device_data["vxlan_config"] = {}
                    logger.debug(f"No VXLAN config for device {current_name} - normalized_vxlan_config: {normalized_vxlan_config}")
                
                # Add OSPF protocol if enabled
                logger.debug(f"OSPF config for device {i+1}: {ospf_config}")
                if ospf_config:
                    logger.debug(f"Adding OSPF to device {current_name}")
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
                    logger.debug(f"Incremented OSPF config: {incremented_ospf_config}")
                
                # Add BGP protocol if enabled
                logger.debug(f"BGP config for device {i+1}: {bgp_config}")
                # Check if BGP is enabled (support both old and new formats)
                bgp_enabled = bgp_config and (
                    bgp_config.get("enabled", False) or  # Old format
                    bgp_config.get("ipv4_enabled", False) or  # New format
                    bgp_config.get("ipv6_enabled", False)  # New format
                )
                if bgp_enabled:
                    logger.debug(f"Adding BGP to device {current_name}")
                    
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
                        logger.debug(f"IPv4 BGP configured for device {current_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}, use_loopback_ip={use_loopback_ip}")
                    
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
                        logger.debug(f"IPv6 BGP configured for device {current_name}")
                    
                    # Add BGP to protocols list and store config separately
                    device_data["protocols"] = device_data.get("protocols", [])
                    if "BGP" not in device_data["protocols"]:
                        device_data["protocols"].append("BGP")
                    device_data["bgp_config"] = bgp_protocol_config
                    logger.debug(f"BGP added to device {current_name}: {device_data['bgp_config']}")
                else:
                    logger.debug(f"BGP NOT enabled for device {current_name} - bgp_config: {bgp_config}")
                
                # Add ISIS protocol if enabled
                logger.debug(f"ISIS config for device {i+1}: {isis_config}")
                if isis_config:
                    logger.debug(f"Adding ISIS to device {current_name}")
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
                    # (Canonical key is isis_config; legacy is_is_config no longer
                    # written — readers fall back to it when reading old data.)
                    device_data["protocols"] = device_data.get("protocols", [])
                    if "IS-IS" not in device_data["protocols"]:
                        device_data["protocols"].append("IS-IS")
                    device_data["isis_config"] = incremented_isis_config
                    logger.debug(f"Incremented ISIS config: {incremented_isis_config}")
                else:
                    logger.debug(f"ISIS NOT enabled for device {current_name}")
                
                if dhcp_config:
                    logger.debug(f"DHCP config for device {i+1}: {dhcp_config}")
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
            
            # Add server information if ServerManager is available
            if hasattr(self.main_window, "server_manager"):
                from utils.device_server_migration import DeviceServerMigration
                # Extract TG ID from interface
                tg_id = DeviceServerMigration.extract_tg_id_from_interface(iface)
                if tg_id is not None:
                    server_url = self.main_window.server_manager.get_server_url(tg_id=tg_id)
                    if server_url:
                        device_data["server_url"] = server_url
                        device_data["tg_id"] = tg_id
                        device_data["server_id"] = DeviceServerMigration.extract_server_id_from_url(server_url)

            # Always include VXLAN config if it exists (even if incomplete)
            # This ensures VXLAN config is preserved when user enables VXLAN in UI
            if normalized_vxlan_config:
                per_device_vxlan = self._with_vxlan_interfaces(
                    normalized_vxlan_config,
                    iface,
                    vlan,
                    device_id=device_data.get("device_id"),
                )
                device_data["vxlan_config"] = per_device_vxlan
                device_data["VXLAN"] = self._format_vxlan_summary(per_device_vxlan)
                if "VXLAN" not in device_data["protocols"]:
                    device_data["protocols"].append("VXLAN")
                logger.debug(f"Added VXLAN config to single device {unique_name}: {per_device_vxlan}")
            else:
                # Ensure vxlan_config is always present (even if empty) for consistency
                device_data["vxlan_config"] = {}
                logger.debug(f"No VXLAN config for single device {unique_name} - normalized_vxlan_config: {normalized_vxlan_config}")
            
            # Add OSPF protocol if enabled
            logger.debug(f"Single device OSPF config: {ospf_config}")
            if ospf_config:
                logger.debug(f"Adding OSPF to single device {unique_name}")
                # Initialize protocols as list and ospf_config as separate field
                device_data["protocols"] = device_data.get("protocols", [])
                if "OSPF" not in device_data["protocols"]:
                    device_data["protocols"].append("OSPF")
                device_data["ospf_config"] = ospf_config
            
            # Add ISIS protocol if enabled
            logger.debug(f"Single device ISIS config: {isis_config}")
            if isis_config:
                logger.debug(f"Adding ISIS to single device {unique_name}")
                # Initialize protocols as list and isis_config as separate field
                device_data["protocols"] = device_data.get("protocols", [])
                if "IS-IS" not in device_data["protocols"]:
                    device_data["protocols"].append("IS-IS")
                device_data["isis_config"] = isis_config
                logger.debug(f"ISIS added to single device {unique_name}: {device_data['isis_config']}")
            else:
                logger.debug(f"ISIS NOT enabled for single device")
            
            # Add BGP protocol if enabled
            logger.debug(f"Single device BGP config: {bgp_config}")
            # Check if BGP is enabled (support both old and new formats)
            bgp_enabled = bgp_config and (
                bgp_config.get("enabled", False) or  # Old format
                bgp_config.get("ipv4_enabled", False) or  # New format
                bgp_config.get("ipv6_enabled", False)  # New format
            )
            if bgp_enabled:
                logger.debug(f"Adding BGP to single device {unique_name}")
                
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
                    logger.debug(f"IPv4 BGP configured for single device {unique_name}: neighbor={neighbor_ipv4}, source={update_source_ipv4}, use_loopback_ip={use_loopback_ip}")
                
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
                    logger.debug(f"IPv6 BGP configured for single device {unique_name}")
                
                # Add BGP to protocols list and store config separately
                device_data["protocols"] = device_data.get("protocols", [])
                if "BGP" not in device_data["protocols"]:
                    device_data["protocols"].append("BGP")
                device_data["bgp_config"] = bgp_protocol_config
                logger.debug(f"BGP added to single device {unique_name}: {device_data['bgp_config']}")
            else:
                logger.debug(f"BGP NOT enabled for single device - bgp_config: {bgp_config}")
            
            if dhcp_config:
                logger.debug(f"DHCP config for single device: {dhcp_config}")
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
        # CRITICAL: Use the original 'iface' variable (with TG ID) as the key in all_devices,
        # not 'iface_name' which might be normalized without TG ID
        # This ensures interface selection from server_tree correctly matches devices
        interface_key = iface  # Use the full "TG X - portname" format
        
        if interface_key not in self.main_window.all_devices or not isinstance(self.main_window.all_devices[interface_key], list):
            self.main_window.all_devices[interface_key] = []
        
        # Add server information to all devices if ServerManager is available
        if hasattr(self.main_window, "server_manager"):
            from utils.device_server_migration import DeviceServerMigration
            tg_id = DeviceServerMigration.extract_tg_id_from_interface(iface)
            if tg_id is not None:
                server_url = self.main_window.server_manager.get_server_url(tg_id=tg_id)
                if server_url:
                    for device_data in devices_to_create:
                        device_data["server_url"] = server_url
                        device_data["tg_id"] = tg_id
                        device_data["server_id"] = DeviceServerMigration.extract_server_id_from_url(server_url)
        
        for device_data in devices_to_create:
            # Store the interface_key in device_data for proper matching during filtering
            device_data["interface_key"] = interface_key
            self.main_window.all_devices[interface_key].append(device_data)
            
            # Add to device name mapping for easy lookup
            self.interface_to_device_map[device_data["Device Name"]] = device_data
            
            # Mark device as newly added (for change tracking)
            device_data["_is_new"] = True
            device_data["_needs_apply"] = True
            
            logger.debug(f"Added device '{device_data['Device Name']}' locally (pending apply)")

        # Save session immediately after adding device(s) so they persist even if client is closed before apply
        if hasattr(self.main_window, "save_session"):
            logger.debug(f"Saving session after adding {len(devices_to_create)} device(s)")
            self.main_window.save_session()

        # Keep the interface selected to ensure devices are visible
        tree = self.main_window.server_tree
        for i in range(tree.topLevelItemCount()):
            tg_item = tree.topLevelItem(i)
            for j in range(tg_item.childCount()):
                port_item = tg_item.child(j)
                if f"{tg_item.text(0).strip()} - {port_item.text(0).strip()}" == iface:
                    tree.setCurrentItem(port_item)
                    port_item.setSelected(True)
                    break

        # Refresh the table to show new devices - use update_device_table which handles filtering correctly
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
        """Paste the copied device(s) to the selected interface.

        Audit HIGH #4 — rewrote four bugs that made this functionally
        a no-op in most setups:

        (a) TG-ID extraction was `parent_item.text(0).strip()`, but
            TG ID lives in a custom widget on column 0 (text(0) is
            empty). target_interface became " - ens4np0", didn't
            match any all_devices key, pasted devices vanished.
            Now uses the same widget-walking logic as
            prompt_add_device above.

        (b) Only 9 fields were carried over from the copied device,
            dropping gateways, MTU, loopbacks, bgp_config,
            ospf_config, isis_config, dhcp_config, and protocols
            (only VXLAN survived). Paste now deep-copies the full
            device dict and patches in the new identity fields,
            preserving everything the user originally configured.

        (c) The source MAC was copied verbatim. Two devices on the
            same L2 with the same MAC = guaranteed ARP collision.
            Now generates a fresh per-paste MAC by incrementing
            the last byte of the source MAC, falling back to a
            random locally-administered MAC if the source is
            missing/invalid.

        (d) copy_selected_device's 9-field shape was the upstream
            cause of (b) — that's fixed separately above to
            deep-copy the whole device.
        """
        import copy as _copy
        import random as _random

        if not hasattr(self.main_window, 'copied_device') or not self.main_window.copied_device:
            QMessageBox.warning(self, "Nothing to Paste", "No device has been copied. Please copy a device first.")
            return

        # Check if a port is selected
        selected_items = self.main_window.server_tree.selectedItems()
        if not selected_items or not selected_items[0].parent():
            QMessageBox.warning(self, "No Port Selected", "Please select a port to paste the device(s) to.")
            return

        # ---- Target interface resolution (audit HIGH #4a) ----
        # TG ID lives in a custom widget on parent_item column 0;
        # text(0) is empty. Walk the widget tree for the "TG N" QLabel,
        # then fall back to server_interfaces by parent index — same
        # logic prompt_add_device uses (~line 5092).
        parent_item = selected_items[0].parent()
        tg_id = None
        tg_id_widget = self.main_window.server_tree.itemWidget(parent_item, 0)
        if tg_id_widget:
            from PyQt5.QtWidgets import QLabel as _QLabel
            for child in tg_id_widget.findChildren(_QLabel):
                if child.text().startswith("TG "):
                    tg_id = child.text().strip()
                    break
        if not tg_id:
            parent_index = self.main_window.server_tree.indexOfTopLevelItem(parent_item)
            if parent_index >= 0 and hasattr(self.main_window, "server_interfaces"):
                if parent_index < len(self.main_window.server_interfaces):
                    server = self.main_window.server_interfaces[parent_index]
                    tg_id = f"TG {server.get('tg_id', '0')}"
        if not tg_id:
            QMessageBox.warning(
                self, "Invalid Selection",
                "Could not determine TG ID from selected port. "
                "Please pick a port under a TG row in the server tree."
            )
            return

        port_name = selected_items[0].text(0).replace("• ", "").strip()
        target_interface = f"{tg_id} - {port_name}"

        # Get the copied device data (can be single device or list)
        copied_devices = self.main_window.copied_device
        if not isinstance(copied_devices, list):
            copied_devices = [copied_devices]

        # Existing names across the whole model for unique-name generation.
        existing_names = [
            d.get("Device Name", "")
            for dev_list in self.main_window.all_devices.values()
            for d in (dev_list if isinstance(dev_list, list) else [])
        ]

        # Existing MACs (audit HIGH #4c) — to guarantee the freshly
        # incremented MAC doesn't collide with another device on the
        # target interface.
        existing_macs = {
            (d.get("MAC Address") or "").lower()
            for dev_list in self.main_window.all_devices.values()
            for d in (dev_list if isinstance(dev_list, list) else [])
        }

        def _fresh_mac(source_mac):
            """Produce a fresh MAC for a pasted device.

            Strategy: increment the last byte of source_mac and keep
            bumping until we land on something not in existing_macs.
            If source_mac is missing or unparseable, fall back to a
            random locally-administered unicast MAC (LSB-of-first-byte
            = 0 unicast, second-LSB = 1 locally administered).
            """
            try:
                if source_mac and source_mac.count(":") == 5:
                    for step in range(1, 256):
                        candidate = self._increment_mac(source_mac, step, byte_index=0)
                        if candidate and candidate.lower() not in existing_macs:
                            existing_macs.add(candidate.lower())
                            return candidate
            except Exception as e:
                logger.debug(f"[paste] increment fallback: {e}")
            # Fallback: random LA-unicast MAC
            first = 0x02 | (_random.randint(0, 255) & 0xFC)
            rest = [_random.randint(0, 255) for _ in range(5)]
            candidate = ":".join(f"{b:02x}" for b in [first] + rest)
            existing_macs.add(candidate)
            return candidate

        pasted_devices = []

        import re as _re

        for copied_device in copied_devices:
            # Generate a unique name for the pasted device. The previous
            # implementation used .rstrip("_Copy"), but rstrip is
            # character-based — `"x_Copy_2".rstrip("_Copy")` returns
            # `"x_Copy_2"` unchanged because "2" isn't in the strip
            # set. Use a proper suffix regex so we strip an actual
            # trailing `_Copy` or `_Copy_<N>` token. Result:
            #   "router"          → "router_Copy"
            #   "router_Copy"     → "router_Copy_2"   (not "_Copy_Copy")
            #   "router_Copy_5"   → "router_Copy_6"   (not "_Copy_5_Copy")
            raw_name = copied_device.get("Device Name") or "Device"
            base_name = _re.sub(r'_Copy(_\d+)?$', '', raw_name) or "Device"
            new_name = f"{base_name}_Copy"
            counter = 1
            while new_name in existing_names:
                counter += 1
                new_name = f"{base_name}_Copy_{counter}"
            existing_names.append(new_name)

            # Full deep-copy of the source device, then patch identity
            # fields. This preserves gateways, MTU, loopbacks, all
            # protocol configs (bgp/ospf/isis/dhcp), and the protocols
            # list — everything the user configured on the source.
            # Audit HIGH #4b.
            new_device = _copy.deepcopy(copied_device)

            # Strip runtime-only / source-specific fields that must
            # NOT be carried over into the paste:
            for k in (
                "device_id", "Status",
                "_is_new", "_needs_apply", "_needs_cleanup",
                "_was_running", "_old_config",
                "arp_resolved", "ping_status",
                "last_apply_at", "last_status_check",
            ):
                new_device.pop(k, None)

            # Identity / placement fields.
            new_device["Device Name"] = new_name
            new_device["device_id"] = str(uuid.uuid4())
            new_device["Interface"] = target_interface
            new_device["Status"] = "Stopped"
            new_device["_is_new"] = True
            new_device["_needs_apply"] = True

            # Fresh MAC (audit HIGH #4c).
            new_device["MAC Address"] = _fresh_mac(copied_device.get("MAC Address", ""))

            # If the source had a VXLAN config, retarget its
            # underlay_interface to the paste destination so it
            # binds against the new port (matches the pre-rewrite
            # behavior; only difference is we no longer rebuild the
            # config from scratch).
            vxlan_cfg = new_device.get("vxlan_config")
            if vxlan_cfg:
                normalized = self._normalize_vxlan_config(vxlan_cfg)
                if normalized:
                    normalized["underlay_interface"] = self._normalize_iface_label(target_interface)
                    if isinstance(normalized, dict) and "tunnels" in normalized:
                        new_device["vxlan_config"] = normalized
                    else:
                        new_device["vxlan_config"] = {"tunnels": [normalized]}
                    new_device["VXLAN"] = self._format_vxlan_summary(normalized)

            # Ensure protocols list survives the deep-copy (defensive;
            # the deep copy should preserve it already).
            new_device.setdefault("protocols", [])

            # Add to all_devices data structure.
            if target_interface not in self.main_window.all_devices:
                self.main_window.all_devices[target_interface] = []
            self.main_window.all_devices[target_interface].append(new_device)

            # Update interface_to_device_map.
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
            logger.debug(f"Saving session after pasting {len(pasted_devices)} device(s)")
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

    def open_ai_assistant_for_selected(self):
        """Open NetGenAI for selected device"""
        try:
            from widgets.ai_unified_dialog import AIUnifiedDialog
            from PyQt5.QtWidgets import QMessageBox
            
            selected_items = self.devices_table.selectedItems()
            if not selected_items:
                QMessageBox.information(
                    self,
                    "No Selection",
                    "Please select a device first."
                )
                return
            
            # Get device from first selected row
            row = selected_items[0].row()
            device_name = self.devices_table.item(row, self.COL["Device Name"]).text()
            
            # Find device info
            device_info = self.get_device_info_by_name(device_name)
            if not device_info:
                QMessageBox.warning(
                    self,
                    "Device Not Found",
                    f"Could not find device '{device_name}'."
                )
                return
            
            # Open AI assistant dialog with device_id (pre-fills fields automatically)
            device_id = device_info.get("device_id")
            dialog = AIUnifiedDialog(self.main_window, device_id=device_id)
            dialog.exec_()
        except ImportError as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "AI Not Available",
                f"NetGenAI not available.\n{str(e)}"
            )
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open NetGenAI:\n{str(e)}"
            )
    
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

        # mode="edit" makes the dialog title read "Edit Device" and
        # the OK button label read "Update Device" instead of the
        # default "Add Device" — clearer user intent.
        dialog = AddDeviceDialog(self, default_iface=iface, mode="edit")

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

        # Pre-fill the PROTOCOL state from the device's stored config.
        # Without this, opening Edit on a device that has BGP / OSPF /
        # ISIS / DHCP configured shows all those checkboxes UNCHECKED
        # — so the user thinks "BGP isn't enabled here" and may also
        # silently remove the protocol on Save (per the new
        # _set_protocol logic from commit d4d2708). Reflect what's
        # actually on the device so the dialog state matches reality.
        try:
            protocols = device_info.get("protocols", []) or []
            if isinstance(protocols, dict):
                # Legacy dict format → list of keys
                protocols = list(protocols.keys())
            protocols_set = {str(p).upper() for p in protocols}

            existing_bgp  = device_info.get("bgp_config")  or {}
            existing_ospf = device_info.get("ospf_config") or {}
            existing_isis = (device_info.get("isis_config")
                             or device_info.get("is_is_config")
                             or {})
            existing_dhcp = device_info.get("dhcp_config") or {}

            bgp_on  = ("BGP"  in protocols_set) and bool(existing_bgp)
            ospf_on = ("OSPF" in protocols_set) and bool(existing_ospf)
            isis_on = (
                ("ISIS" in protocols_set or "IS-IS" in protocols_set)
                and bool(existing_isis)
            )
            dhcp_on = ("DHCP" in protocols_set) and bool(existing_dhcp)

            if hasattr(dialog, "bgp_enable_checkbox"):
                dialog.bgp_enable_checkbox.setChecked(bgp_on)
            if hasattr(dialog, "ospf_enable_checkbox"):
                dialog.ospf_enable_checkbox.setChecked(ospf_on)
            if hasattr(dialog, "isis_enable_checkbox"):
                dialog.isis_enable_checkbox.setChecked(isis_on)
            if hasattr(dialog, "dhcp_enable_checkbox"):
                dialog.dhcp_enable_checkbox.setChecked(dhcp_on)

            # Pre-fill BGP fields from the stored config so the user
            # sees the actual ASN / neighbor IPs they configured.
            if bgp_on:
                if hasattr(dialog, "bgp_local_as_input"):
                    dialog.bgp_local_as_input.setText(
                        str(existing_bgp.get("bgp_asn", ""))
                    )
                if hasattr(dialog, "bgp_remote_as_input"):
                    dialog.bgp_remote_as_input.setText(
                        str(existing_bgp.get("bgp_remote_asn", ""))
                    )
                if hasattr(dialog, "bgp_toggle_ipv4"):
                    dialog.bgp_toggle_ipv4.setChecked(
                        bool(existing_bgp.get("ipv4_enabled", True))
                    )
                if hasattr(dialog, "bgp_toggle_ipv6"):
                    dialog.bgp_toggle_ipv6.setChecked(
                        bool(existing_bgp.get("ipv6_enabled", False))
                    )

            # Pre-fill OSPF fields similarly.
            if ospf_on:
                if hasattr(dialog, "ospf_area_id_input"):
                    dialog.ospf_area_id_input.setText(
                        str(existing_ospf.get("area_id", ""))
                    )
                if hasattr(dialog, "ospf_toggle_ipv4"):
                    dialog.ospf_toggle_ipv4.setChecked(
                        bool(existing_ospf.get("ipv4_enabled", True))
                    )
                if hasattr(dialog, "ospf_toggle_ipv6"):
                    dialog.ospf_toggle_ipv6.setChecked(
                        bool(existing_ospf.get("ipv6_enabled", False))
                    )

            # Pre-fill ISIS fields similarly.
            if isis_on:
                if hasattr(dialog, "isis_system_id_input"):
                    dialog.isis_system_id_input.setText(
                        str(existing_isis.get("system_id", ""))
                    )
                if hasattr(dialog, "isis_area_id_input"):
                    # Stored area_id may be the long form
                    # "49.0001.0000.0000.0001.00"; keep it as-is.
                    dialog.isis_area_id_input.setText(
                        str(existing_isis.get("area_id", ""))
                    )

            # Refresh the dialog's protocol dropdown + visibility now
            # that the enable-checkboxes reflect actual state.
            if hasattr(dialog, "_on_protocol_enabled_changed"):
                dialog._on_protocol_enabled_changed()
        except Exception as _prefill_exc:
            logger.warning(f"[EDIT] Protocol pre-fill skipped: {_prefill_exc}")

        if dialog.exec_() != dialog.Accepted:
            return
        
        # Get updated values from dialog.
        # The 5 VXLAN-increment fields (incr_vxlan, vxlan_vni_increment_index,
        # vxlan_local_octet_index, vxlan_remote_octet_index, vxlan_udp_increment_index)
        # were added to AddDeviceDialog.get_values() but this Edit-path unpack
        # was never updated — only the Add-path at line 5042 was. Result: the
        # 37-tuple unpacked into 32 names threw `ValueError: too many values
        # to unpack` and the entire Save click silently failed. Matched to
        # the Add-path tuple now.
        (
            new_name, iface, mac, ipv4, ipv6, ipv4_mask, ipv6_mask,
            vlan, mtu, ipv4_gateway, ipv6_gateway,
            inc_mac, inc_ipv4, inc_ipv6, inc_gateway, inc_vlan, incr_vxlan, count,
            ospf_config, bgp_config, dhcp_config,
            ipv4_octet_index, ipv6_hextet_index, mac_byte_index, gateway_octet_index,
            incr_dhcp_pool, dhcp_pool_octet_index,
            incr_loopback, loopback_ipv4_octet_index, loopback_ipv6_hextet_index,
            loopback_ipv4, loopback_ipv6, isis_config,
            vxlan_vni_increment_index, vxlan_local_octet_index,
            vxlan_remote_octet_index, vxlan_udp_increment_index,
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

        # Update device in data structure. Held under device_mutate_lock
        # so a concurrent MultiDeviceApplyWorker mutation on the same
        # device doesn't race with our Edit-Save dict updates (audit
        # MED #9). The lock window covers the bulk update + the
        # subsequent protocol RMW operations so the device's state
        # transitions atomically from the worker's POV.
        with self.device_mutate_lock:
            device_info.update({
                "Device Name": new_name or device_name,
                "Interface": iface,
                "MAC Address": mac,
                "IPv4": ipv4,
                "IPv6": ipv6,
                "VLAN": vlan,
                "MTU": mtu or "1500",  # MTU field, default to 1500
                "Gateway": ipv4_gateway,  # Use IPv4 gateway as primary gateway
                "IPv4 Gateway": ipv4_gateway,
                "IPv6 Gateway": ipv6_gateway,
                "ipv4_mask": ipv4_mask,
                "ipv6_mask": ipv6_mask,
                "Loopback IPv4": loopback_ipv4 if loopback_ipv4 else "",
                "Loopback IPv6": loopback_ipv6 if loopback_ipv6 else "",
                "_needs_apply": True  # Mark for server update
            })

            # Update protocol configs based on what the dialog returned.
            # NEW behaviour (was a bug): an unchecked protocol clears
            # its config + removes it from the device's protocols list.
            # Previously only ADDs happened — once a protocol was on a
            # device you couldn't un-set it from the GUI, which is
            # exactly the trap user hit ("I didn't enable BGP but
            # apply keeps trying to configure BGP"). The protocol got
            # added at some earlier point and the Edit dialog had no
            # way to remove it.
            #
            # Each protocol's checkbox state determines whether the
            # dialog returns a config (truthy) or None. We treat the
            # ABSENCE of a config as the user explicitly disabling
            # the protocol and prune both the config dict and the
            # protocols list entry.
            protocols_list = device_info.setdefault("protocols", [])

            def _set_protocol(label, config_dict, config_key, extra_cleanup=None):
                if config_dict:
                    device_info[config_key] = config_dict
                    if label not in protocols_list:
                        protocols_list.append(label)
                else:
                    # User unchecked this protocol — clear the stored
                    # config and drop the label from the protocols
                    # list. Server will see the device has no BGP
                    # next apply and won't try to create the FRR
                    # container for it.
                    if config_key in device_info:
                        del device_info[config_key]
                    if label in protocols_list:
                        protocols_list.remove(label)
                    if extra_cleanup:
                        extra_cleanup()

            _set_protocol("OSPF", ospf_config, "ospf_config")
            _set_protocol("BGP",  bgp_config,  "bgp_config")
            _set_protocol("IS-IS", isis_config, "isis_config")

            def _dhcp_extra_cleanup():
                device_info.pop("dhcp_mode", None)
            if dhcp_config:
                device_info["dhcp_config"] = dhcp_config
                device_info["dhcp_mode"] = (dhcp_config.get("mode") or "client").lower()
                if "DHCP" not in protocols_list:
                    protocols_list.append("DHCP")
            else:
                _set_protocol("DHCP", None, "dhcp_config", extra_cleanup=_dhcp_extra_cleanup)

        # Lock released — VXLAN normalization touches helpers that
        # don't need the lock, and the subsequent assignments are
        # single-key writes that are individually GIL-safe.
        normalized_edit_vxlan = self._with_vxlan_interfaces(
            new_vxlan_config,
            iface,
            vlan,
            device_id=device_info.get("device_id"),
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
        
        # Refresh protocol tables if needed. The handler methods are
        # named refresh_bgp_status / refresh_ospf_status /
        # refresh_isis_status — the old refresh_*_table names never
        # existed and the calls crashed with AttributeError the
        # moment Edit Device's Save handler reached this block.
        # Each call is also wrapped in a try/except so a regression
        # in one handler doesn't tear down the whole save flow.
        for handler_attr, method_name in (
            ("bgp_handler",  "refresh_bgp_status"),
            ("ospf_handler", "refresh_ospf_status"),
            ("isis_handler", "refresh_isis_status"),
        ):
            handler = getattr(self, handler_attr, None)
            if handler is None:
                continue
            fn = getattr(handler, method_name, None)
            if fn is None:
                logger.debug(
                    f"[EDIT] {handler_attr}.{method_name} not present; skipping"
                )
                continue
            try:
                fn()
            except Exception as _refresh_exc:
                logger.warning(
                    f"[EDIT] {handler_attr}.{method_name} raised: {_refresh_exc}"
                )

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

            # Audit HIGH #4(d): the previous shape only copied 9
            # named fields, dropping gateways, MTU, loopbacks,
            # bgp/ospf/isis/dhcp configs, protocols list, etc. Paste
            # then had nothing to carry over even after its other
            # bugs were fixed. Full deep-copy now; runtime-only
            # fields (status flags, internal "_..." markers, the
            # source's device_id) are stripped at paste time, not
            # here, so a copy still represents the user's full
            # config intent.
            import copy as _copy
            copied_devices.append(_copy.deepcopy(dict(device_info)))
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

    def _with_vxlan_interfaces(self, vxlan_config, iface_label, vlan_value, device_id=None):
        """
        Add interface-related fields to VXLAN configuration.
        
        Args:
            vxlan_config: VXLAN configuration dict
            iface_label: Interface label (e.g., "TG 0 - ens4np0")
            vlan_value: VLAN ID (e.g., "20" or "0")
            device_id: Optional device ID for generating vxlan_interface name
        
        Returns:
            Updated VXLAN configuration with interface fields
        """
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
        
        # Generate vxlan_interface name if device_id and vni are available
        # This matches the logic in utils/vxlan.py ensure_vxlan_interface()
        vni = config.get("vni")
        if device_id and vni:
            ifname_seed = device_id.replace("-", "")
            vxlan_iface = config.get("vxlan_interface") or f"vx{vni}-{ifname_seed[:6]}"
            # Linux interface name limit is 15 characters (IFNAMSIZ)
            if len(vxlan_iface) > 15:
                vxlan_iface = vxlan_iface[:15]
            config["vxlan_interface"] = vxlan_iface
        
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

    def _apply_preflight_dots(self, by_device):
        """Paint a per-device red/amber/green dot in front of each
        Device Name cell, driven by the preflight bar's by_device
        breakdown (v0.2.78).

        ``by_device`` shape: ``{device_name: {error: n, warning: n,
        ok: n, ...}}`` — matches what the /api/preflight/check endpoint
        returns. Devices missing from the dict get no prefix (they
        haven't been touched by any preflight check).

        Idempotent: re-applying overwrites the prefix, so this can run
        every time the bar refreshes without piling up dots.
        """
        try:
            from PyQt5.QtGui import QColor
            if not isinstance(by_device, dict):
                return
            table = getattr(self, "devices_table", None)
            if table is None:
                return
            col = self.COL.get("Device Name", 0)
            for row in range(table.rowCount()):
                item = table.item(row, col)
                if item is None:
                    continue
                # The display string may carry a leading dot from the
                # last apply — strip it so we don't pile up emoji.
                raw_name = item.text()
                for prefix in ("●  ", "○  "):
                    if raw_name.startswith(prefix):
                        raw_name = raw_name[len(prefix):]
                        break
                stats = by_device.get(raw_name)
                if not stats:
                    # No findings for this device — strip any stale dot.
                    item.setText(raw_name)
                    continue
                n_err = int(stats.get("error", 0))
                n_warn = int(stats.get("warning", 0))
                # Severity wins: error > warning > ok-only > unknown.
                if n_err > 0:
                    dot_color = "#b91c1c"  # red
                    tip = (f"{n_err} preflight error"
                           f"{'' if n_err == 1 else 's'}"
                           + (f", {n_warn} warning"
                              f"{'' if n_warn == 1 else 's'}"
                              if n_warn else ""))
                elif n_warn > 0:
                    dot_color = "#b45309"  # amber
                    tip = (f"{n_warn} preflight warning"
                           f"{'' if n_warn == 1 else 's'}")
                else:
                    dot_color = "#166534"  # green
                    tip = "preflight: clean"
                item.setText(f"●  {raw_name}")
                item.setForeground(QColor(dot_color))
                # Keep the existing tooltip if there was one + append
                # preflight info on a new line so users hovering for
                # device meta don't lose context.
                existing_tip = item.toolTip()
                if existing_tip and "preflight" not in existing_tip.lower():
                    item.setToolTip(f"{existing_tip}\nPreflight: {tip}")
                else:
                    item.setToolTip(f"Preflight: {tip}")
        except Exception:
            # Painter is purely advisory — never let a colouring bug
            # block the table from rendering.
            pass

    def update_device_table(self, all_devices=None):
        """Rebuild the device table based on selected interfaces.

        Guarded by self._populating_devices_table so the programmatic
        cell writes below don't trigger on_cell_changed (the
        inline-edit handler from audit HIGH #2). Without this flag,
        every populate emit `[INLINE EDIT]` log lines AND mark the
        device _needs_apply=True, which could cause apply storms or
        unintended re-apply on every refresh tick. The flag is
        cleared in finally so we always restore the signal flow,
        even on exception.
        """
        if all_devices is None:
            all_devices = getattr(self.main_window, "all_devices", {})

        self._populating_devices_table = True
        # OPTIMIZATION: Calculate row count first, then set once instead of clear+insertRow
        try:
            selected_interfaces = set()
            tree = getattr(self.main_window, "server_tree", None)
            if tree:
                # OPTIMIZATION: Cache TG ID extraction to avoid repeated lookups
                tg_id_cache = {}
                
                for item in tree.selectedItems():
                    parent = item.parent()
                    if parent:
                        # OPTIMIZATION: Use cached TG ID if available, otherwise extract once
                        parent_index = tree.indexOfTopLevelItem(parent)
                        if parent_index not in tg_id_cache:
                            tg_id = None
                            # Try fastest method first: server_interfaces lookup
                            if parent_index >= 0 and hasattr(self.main_window, "server_interfaces"):
                                if parent_index < len(self.main_window.server_interfaces):
                                    server = self.main_window.server_interfaces[parent_index]
                                    tg_id = f"TG {server.get('tg_id', '0')}"
                            
                            # Fallback: Extract from custom widget (slower)
                            if not tg_id:
                                tg_id_widget = tree.itemWidget(parent, 0)
                                if tg_id_widget:
                                    tg_id_label = tg_id_widget.findChild(QLabel)
                                    if tg_id_label:
                                        tg_id = tg_id_label.text().strip()
                            
                            # Last resort: text(0)
                            if not tg_id:
                                tg_id = parent.text(0).strip()
                            
                            tg_id_cache[parent_index] = tg_id
                        else:
                            tg_id = tg_id_cache[parent_index]
                        
                        port_name = item.text(0).replace("• ", "").strip()
                        if tg_id and port_name:
                            interface_key = f"{tg_id} - {port_name}"
                            selected_interfaces.add(interface_key)
                            logging.debug(f"[DEVICE TABLE] Selected interface: '{interface_key}'")
            
            logging.debug(f"[DEVICE TABLE] Selected interfaces: {selected_interfaces}, All device keys: {list(all_devices.keys())}")

            interfaces_to_show = selected_interfaces or list(all_devices.keys())
            
            # OPTIMIZATION: Count total rows first, then set once
            total_rows = 0
            devices_to_add = []
            for iface in interfaces_to_show:
                devices = all_devices.get(iface)
                if not devices:
                    legacy_iface = iface.replace(" - ", " - Port: • ")
                    devices = all_devices.get(legacy_iface, [])
                if not isinstance(devices, list):
                    continue
                
                for device in devices:
                    if isinstance(device, dict):
                        devices_to_add.append((iface, device))
                        total_rows += 1
            
            # Set row count once instead of multiple insertRow calls
            self.devices_table.setRowCount(total_rows)
            
            # OPTIMIZATION: Disable sorting temporarily for faster updates.
            # v0.2.91: also snapshot the operator's chosen sort column
            # so a rebuild (Apply / Refresh / device delete) doesn't
            # blow away their pick and snap rows back to insertion order.
            was_sorting_enabled = self.devices_table.isSortingEnabled()
            from utils.table_sort_state import capture_sort_state
            _devices_sort_state = capture_sort_state(self.devices_table)
            if was_sorting_enabled:
                self.devices_table.setSortingEnabled(False)
            
            # OPTIMIZATION: Block table updates during bulk population for better performance
            self.devices_table.setUpdatesEnabled(False)
            
            try:
                # Now populate rows
                for row, (iface, device) in enumerate(devices_to_add):
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

                    # OPTIMIZATION: Set status icon immediately - icons are lightweight and won't block
                    status_value = device.get("Status", "Stopped")
                    resolved = status_value == "Running"
                    tooltip = "Device Running" if resolved else "Device Stopped"
                    self.set_status_icon(row, resolved=resolved, status_text=tooltip, device_status=status_value)
                
                # Re-enable sorting if it was enabled, then restore the
                # operator's pre-rebuild sort column + direction.
                if was_sorting_enabled:
                    self.devices_table.setSortingEnabled(True)
                    from utils.table_sort_state import restore_sort_state
                    restore_sort_state(self.devices_table, _devices_sort_state)
            finally:
                # Re-enable table updates after bulk population
                self.devices_table.setUpdatesEnabled(True)
                # Force a single repaint after all updates
                self.devices_table.viewport().update()

        except Exception as exc:
            logging.error(f"[DEVICE TABLE] Failed to rebuild table: {exc}")
        finally:
            # Always clear the populate-guard flag so subsequent
            # genuine user inline-edits are not ignored. See
            # on_cell_changed (audit HIGH #2) for the consumer.
            self._populating_devices_table = False

        # OPTIMIZATION: Defer ARP initialization to avoid blocking UI on click
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self._initialize_arp_status_from_database)

        # v0.2.78: re-apply the preflight per-device dots after a
        # rebuild (the bar may have been polling all along; we just
        # blew away the cells it last painted, so re-paint from the
        # cached state).
        try:
            bar = getattr(self, "preflight_bar", None)
            if bar is not None and hasattr(bar, "current_by_device"):
                self._apply_preflight_dots(bar.current_by_device())
        except Exception:
            pass

        # Re-apply the filter so newly-rebuilt rows respect whatever
        # the user has typed. Without this, any periodic table refresh
        # would silently clear the filter state.
        try:
            if hasattr(self, "_device_filter_input"):
                self._apply_device_filter(self._device_filter_input.text())
        except Exception as _filter_exc:
            logger.debug(f"[FILTER] re-apply failed: {_filter_exc}")

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
            logger.debug(f"Processing ARP result for row {row}, device: {device_name}, operation_id: {operation_id}")

            if hasattr(self, "_pending_arp_rows") and self._pending_arp_rows:
                if row not in self._pending_arp_rows:
                    logger.debug(f"Skipping row {row} ({device_name}) - not pending")
                    return

            if hasattr(self, "arp_operation_worker") and self.arp_operation_worker:
                current_id = getattr(self.arp_operation_worker, "operation_id", None)
                if operation_id and current_id and operation_id != current_id:
                    logger.debug(f"Skipping row {row} ({device_name}) - id mismatch {operation_id} != {current_id}")
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
        logger.info("Individual ARP checks completed")

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
        logger.info(f"Starting {len(devices_to_process)} device(s) in background...")

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
        logger.info(f"Stopping {len(devices_to_process)} device(s) in background...")

    def _on_devices_table_context_menu(self, pos):
        """v0.2.85: right-click handler for the devices table — opens
        a small QMenu next to the cursor with the same actions the
        toolbar exposes. Selecting the row under the cursor first so
        operators don't have to left-click then right-click.

        ``pos`` is a QPoint relative to the table viewport.
        """
        from PyQt5.QtWidgets import QMenu
        # If the operator right-clicked on a row that isn't currently
        # selected, select it first (most other apps do this; without
        # it, the menu acts on whatever was previously selected and
        # confuses).
        idx = self.devices_table.indexAt(pos)
        if idx.isValid():
            sel_model = self.devices_table.selectionModel()
            if not sel_model.isRowSelected(idx.row(), idx.parent()):
                self.devices_table.selectRow(idx.row())

        menu = QMenu(self.devices_table)
        # Disable everything when there's no selection — a right-click
        # on empty space below the rows shouldn't offer broken actions.
        has_selection = bool(self.devices_table.selectedItems())

        act_apply = menu.addAction("Apply selected")
        act_apply.setEnabled(has_selection)
        act_apply.triggered.connect(self.apply_selected_device_with_arp)

        # v0.3.11: Edit was missing from the context menu — operators
        # had to mouse to the Edit toolbar button or double-click the
        # row to open the Edit dialog, which broke parity with the
        # Streams-tab right-click menu (which exposes Edit). Same
        # handler as the toolbar button (wired at line 2001).
        act_edit = menu.addAction("Edit")
        act_edit.setEnabled(has_selection)
        act_edit.triggered.connect(self.prompt_edit_device)

        menu.addSeparator()

        act_copy = menu.addAction("Copy")
        act_copy.setEnabled(has_selection)
        act_copy.triggered.connect(self.copy_selected_device)

        act_paste = menu.addAction("Paste")
        # Paste enabled only when there's a clipboard payload — match
        # the existing paste-button behaviour (the method itself checks
        # main_window.copied_device(s) and warns if empty).
        clipboard_has_device = bool(
            getattr(self.main_window, "copied_device", None)
            or getattr(self.main_window, "copied_devices", None)
        )
        act_paste.setEnabled(clipboard_has_device)
        act_paste.triggered.connect(self.paste_device_to_interface)

        menu.addSeparator()

        act_delete = menu.addAction("Delete")
        act_delete.setEnabled(has_selection)
        act_delete.triggered.connect(self.remove_selected_device)

        # Map the viewport-relative pos to a global screen pos for popup.
        menu.exec_(self.devices_table.viewport().mapToGlobal(pos))

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

        # Check if any device has VXLAN before removal (to determine if interface refresh is needed)
        # Do this before removing rows from the table, as row indices become invalid after removal
        needs_interface_refresh = False
        for row in unique_rows:
            name_item = self.devices_table.item(row, self.COL.get("Device Name"))
            if not name_item:
                continue
            device_name = name_item.text()
            device_info = self.get_device_info_by_name(device_name)
            if device_info:
                vxlan_config = device_info.get("vxlan_config", {})
                vxlan_interface = device_info.get("vxlan_interface", "")
                # Check if device had VXLAN configuration or interface
                if (vxlan_config and isinstance(vxlan_config, dict) and 
                    (vxlan_config.get("tunnels") or vxlan_config.get("vni") or vxlan_interface)):
                    needs_interface_refresh = True
                    break

        removed_devices = []
        # Process rows in reverse order to avoid index shifting issues when removing
        for row in sorted(unique_rows, reverse=True):
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
            if hasattr(self, "vxlan_handler") and self.vxlan_handler:
                try:
                    self.vxlan_handler._cleanup_vxlan_table_for_device(device_id, device_name)
                except Exception as exc:
                    logging.debug(f"[REMOVE] VXLAN cleanup failed for {device_name}: {exc}")

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

            # Interface list refresh is now manual only - user can click "Refresh Interface List" button if needed
            # Removed automatic refresh to prevent unnecessary UI updates

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
        logger.info(f"Saved {pool_count} route pool(s)")
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
        """Check if ARP/Neighbor resolution is working for the device's target from database.

        Audit MED #11: this used to call `requests.get(timeout=(3,15))`
        directly on the UI thread. populate_device_table calls
        add_device per device, which calls this per device — opening
        a session with 30 devices froze the UI for up to ~9 minutes
        worst case while sync HTTP calls drained.

        Now wraps the HTTP call in a one-shot QThread that pumps a
        local QEventLoop — same pattern as
        traffic_client.stream_logic._post_traffic_async / _get_async.
        Call sites read identically (sync-looking, returns
        (bool, str)) but the UI keeps repainting while the request
        is in flight.
        """
        import requests
        from PyQt5.QtCore import QThread, QEventLoop

        device_name = device_info.get("Device Name", "Unknown")
        device_id = device_info.get("device_id", "")

        iface_label = device_info.get("Interface", "")
        if not iface_label:
            return False, "No interface configured"

        # Get server URL from the interface label
        server_url = self._get_server_url_from_interface(iface_label)
        if not server_url:
            return False, "No server URL found for interface"

        # Run the HTTP call off the UI thread via QThread + QEventLoop.
        class _ArpGetWorker(QThread):
            def __init__(self, url):
                super().__init__()
                self._url = url
                self.response = None
                self.error = None

            def run(self):
                try:
                    self.response = requests.get(self._url, timeout=(3, 15))
                except Exception as exc:
                    self.error = exc

        url = f"{server_url}/api/device/database/devices/{device_id}"
        loop = QEventLoop()
        worker = _ArpGetWorker(url)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec_()
        worker.wait()
        # Release the thread resource immediately rather than relying
        # on Python GC. Matches the _post_traffic_async pattern.
        worker.deleteLater()

        # Adapt back to a fake-Response-style local: existing code below
        # uses `response.status_code` and `response.json()`. If the
        # worker hit a transport error, surface it the same way the
        # original code's try/except did.
        try:
            if worker.error is not None:
                raise worker.error
            response = worker.response
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
                logger.debug(f"Failed to get device data: {response.status_code}")
                return False, "Database error"
        except requests.exceptions.Timeout as e:
            logger.debug(f"Timeout getting ARP status from database: {e}")
            return False, "Server timeout - may be overloaded"
        except requests.exceptions.ConnectionError as e:
            logger.debug(f"Connection error getting ARP status: {e}")
            return False, "Server unreachable"
        except Exception as e:
            logger.debug(f"Error getting ARP status from database: {e}")
            return False, f"Database error: {str(e)}"

    def _check_individual_arp_resolution(self, device_info):
        """Check ARP resolution for individual IPs from database instead of direct server check."""
        # Check if application is closing
        if hasattr(self.main_window, '_is_closing') and self.main_window._is_closing:
            logger.info("Skipping ARP check - application is closing")
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
            # Increased timeout: (connect_timeout, read_timeout) to handle slow database operations
            # Connect timeout of 3s fails fast if server is unreachable
            # Read timeout of 15s allows for slow database queries
            response = requests.get(f"{server_url}/api/device/database/devices/{device_id}", timeout=(3, 15))
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
                logger.debug(f"Failed to get device data: {response.status_code}")
                return {
                    "overall_resolved": False,
                    "overall_status": "Database error",
                    "ipv4_resolved": False,
                    "ipv6_resolved": False,
                    "gateway_resolved": False,
                    "needs_retry": False,
                }
        except requests.exceptions.Timeout as e:
            logger.debug(f"Timeout getting ARP status from database: {e}")
            return {
                "overall_resolved": False,
                "overall_status": "Server timeout - may be overloaded",
                "ipv4_resolved": False,
                "ipv6_resolved": False,
                "gateway_resolved": False,
                "needs_retry": True,  # Retry on timeout
            }
        except requests.exceptions.ConnectionError as e:
            logger.debug(f"Connection error getting ARP status: {e}")
            return {
                "overall_resolved": False,
                "overall_status": "Server unreachable",
                "ipv4_resolved": False,
                "ipv6_resolved": False,
                "gateway_resolved": False,
                "needs_retry": True,  # Retry on connection error
            }
        except Exception as e:
            logger.debug(f"Error getting ARP status from database: {e}")
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
            logger.info("Skipping ARP check - application is closing")
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
            logger.info("Skipping ARP check - application is closing")
            return
        
        # Check if there's already a bulk ARP worker running
        if hasattr(self, 'bulk_arp_worker') and self.bulk_arp_worker:
            if self.bulk_arp_worker.isRunning():
                logger.info("ARP check already running, skipping new request")
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
        
        logger.info(f"Started async ARP check for {len(devices_data)} devices")
    
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
                    logger.info(f"Retrying ARP check for {len(devices_to_process)} device(s)")
                    self.check_arp_resolution_bulk_async(devices_to_process)
            finally:
                if hasattr(self, "_arp_retry_rows"):
                    self._arp_retry_rows.difference_update(new_rows)

        QTimer.singleShot(delay, retry)
    
    def _on_device_apply_result(self, operation_type, result_data):
        """Handle successful device apply result from background worker."""
        try:
            device_name = result_data.get("device_name", "Unknown")
            logger.info(f"✅ Successfully applied device configuration for '{device_name}'")

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
                    # Defer the refresh — give the server a moment to
                    # write the fresh ARP state — using Qt's event
                    # loop instead of time.sleep(3) so we don't freeze
                    # the UI thread.
                    QTimer.singleShot(
                        3000,
                        lambda row=device_row, name=device_name: (
                            self._refresh_device_table_from_database([row]),
                            logger.info(f"Triggered ARP refresh for {name}"),
                        ),
                    )
                else:
                    logger.info(f"Could not find device row for {device_name}")
                
            except Exception as e:
                logger.error(f"Failed to refresh ARP for {device_name}: {e}")
                
        except Exception as e:
            logger.error(f"Error handling result: {e}")
    
    def _on_device_apply_error(self, operation_type, error_message):
        """Handle device apply error from background worker.

        Used by the single-device async path (`_apply_device_to_server`
        → DatabaseQueryWorker). Until now this was log-only, so the
        user got no visible signal — including for the server's HTTP
        409 "(interface, vlan) already in use" gate. Now we also pop
        a dialog so the message reaches the user.
        """
        try:
            logger.error(f"❌ Device apply failed: {error_message}")
            try:
                # Trim very long messages so the dialog stays readable.
                body = str(error_message or "").strip()
                if len(body) > 800:
                    body = body[:800] + "…"
                QMessageBox.warning(self, "Device apply failed", body or "Unknown error")
            except Exception as _dlg_exc:
                logger.warning(f"Could not show apply-error dialog: {_dlg_exc}")
        except Exception as e:
            logger.error(f"Error handling error: {e}")
    
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
            # Repaint preflight pills immediately so the operator sees
            # the impact of their edit instead of waiting up to 60 s
            # for the auto-poll.
            try:
                from widgets.preflight_bar import kick_refresh
                kick_refresh(self)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error cleaning up: {e}")
    def _on_multi_device_applied(self, device_name, success, message):
        """Handle individual device apply result from multi-device worker.

        This slot is invoked by Qt on the *UI thread* every time the
        worker emits a per-device result. The old implementation did:

            time.sleep(2)
            requests.post(force-check, timeout=5)   # sync HTTP
            time.sleep(1)
            self._refresh_device_table_from_database([row])

        all of which run on the UI thread → guaranteed 3-8s freeze
        per applied device. Rewritten to:

          1. fire the ARP force-check in a one-shot QThread (non-blocking
             — server work happens in parallel with the UI event loop)
          2. queue the DB-backed table refresh on QTimer.singleShot
             so the UI gets a moment to render before the refresh runs

        Net effect: the UI stays responsive while the same chain of
        side-effects completes in the background.
        """
        try:
            logger.info(f"{message}")

            # Tick the inline progress widget once per per-device result,
            # regardless of success/failure — both count as "done with
            # this device" from the user's perspective.
            self._tick_apply_progress()

            if not success:
                return

            if hasattr(self, "dhcp_handler") and self.dhcp_handler:
                QTimer.singleShot(200, self.dhcp_handler.refresh_dhcp_status)

            # Find the row for this device (cheap — small table)
            device_row = None
            for row in range(self.devices_table.rowCount()):
                name_item = self.devices_table.item(row, self.COL["Device Name"])
                if name_item and name_item.text() == device_name:
                    device_row = row
                    break

            if device_row is None:
                logger.info(f"Could not find device row for {device_name}")
                return

            server_url = self.get_server_url(silent=True)

            # Fire-and-forget force-check from a worker thread so the
            # UI thread isn't blocked on requests.post().
            if server_url:
                from PyQt5.QtCore import QThread, pyqtSignal

                class _ForceCheckWorker(QThread):
                    done = pyqtSignal(bool)

                    def __init__(self, url):
                        super().__init__()
                        self._url = url

                    def run(self):
                        try:
                            import requests
                            r = requests.post(
                                f"{self._url}/api/arp/monitor/force-check",
                                timeout=5,
                            )
                            self.done.emit(r.status_code == 200)
                        except Exception as exc:
                            logger.warning(f"[APPLY] ARP force-check failed: {exc}")
                            self.done.emit(False)

                # Stash on self so Qt doesn't garbage-collect mid-flight.
                worker = _ForceCheckWorker(server_url)
                if not hasattr(self, "_apply_force_check_workers"):
                    self._apply_force_check_workers = []
                self._apply_force_check_workers.append(worker)
                worker.done.connect(
                    lambda ok, w=worker: (
                        logger.info(f"[APPLY] ARP force-check {'OK' if ok else 'failed'}")
                        if True else None
                    )
                )
                # Drop the reference when the thread finishes so we
                # don't accumulate workers forever.
                worker.finished.connect(
                    lambda w=worker: self._apply_force_check_workers.remove(w)
                    if w in self._apply_force_check_workers else None
                )
                worker.start()

            # Defer the DB-backed table refresh to give the server a
            # moment to write the fresh ARP state to its DB. Uses Qt's
            # event loop instead of time.sleep, so the UI keeps
            # rendering.
            QTimer.singleShot(
                3000,
                lambda row=device_row, name=device_name: (
                    self._refresh_device_table_from_database([row]),
                    logger.info(f"Triggered ARP refresh for {name}"),
                ),
            )

        except Exception as e:
            logger.error(f"Error handling result: {e}")
    
    def _on_multi_device_progress(self, device_name, status_message):
        """Handle progress updates from multi-device worker."""
        try:
            logger.info(f"{device_name}: {status_message}")
        except Exception as e:
            logger.error(f"Error handling progress: {e}")
    
    def _on_multi_device_apply_finished(self, results, successful_count, failed_count):
        """Handle completion of multi-device apply worker."""
        # Hide the inline progress widget regardless of outcome.
        self._hide_apply_progress()
        try:
            # Print results to console
            if results:
                logger.debug(f"\n{'='*60}")
                logger.info(f"MULTI DEVICE APPLY RESULTS: {successful_count} successful, {failed_count} failed")
                logger.debug(f"{'='*60}")
                for result in results:
                    logger.info(f"  {result}")
                logger.debug(f"{'='*60}\n")

            # Surface failures to the user. Previously the only signal
            # an apply had failed was a logger.info() line nobody reads;
            # specific cases like the server's HTTP 409 "(interface,
            # vlan) already in use" gate were effectively silent. Show
            # one aggregated dialog so multi-device batches don't
            # produce a popup storm — and offer a "Retry Failed"
            # button so the user doesn't have to re-select + re-apply
            # by hand.
            if failed_count > 0 and results:
                # results entries from MultiDeviceApplyWorker look like:
                #   "❌ <name>: Failed to apply to server - <server error>"
                # or "✅ <name>: ..." on success. Filter to the failures.
                failures = [r for r in results if isinstance(r, str) and r.lstrip().startswith("❌")]
                if failures:
                    body_lines = failures[:8]  # cap so the dialog stays readable
                    overflow = len(failures) - len(body_lines)
                    body = "\n\n".join(body_lines)
                    if overflow > 0:
                        body += f"\n\n…and {overflow} more (see logs)."
                    title = (
                        "Device apply failed" if failed_count == 1
                        else f"{failed_count} devices failed to apply"
                    )

                    # Capture the failed device_info entries before the
                    # worker is cleaned up below — we need them to
                    # re-spawn the retry worker.
                    failed_device_infos = []
                    if hasattr(self, "multi_device_apply_worker"):
                        try:
                            # Pull names from the "❌ <name>:" prefix; map
                            # to the device_info tuples the worker held.
                            failed_names = set()
                            for line in failures:
                                # Strip "❌ " then take up to ":"
                                stripped = line.lstrip("❌").strip()
                                if ":" in stripped:
                                    failed_names.add(stripped.split(":", 1)[0].strip())
                            for row, device_info in self.multi_device_apply_worker.devices_to_apply:
                                if device_info.get("Device Name") in failed_names:
                                    failed_device_infos.append((row, device_info))
                        except Exception as _exc:
                            logger.debug(f"[RETRY] could not capture failed devices: {_exc}")

                    try:
                        box = QMessageBox(self)
                        box.setIcon(QMessageBox.Warning)
                        box.setWindowTitle(title)
                        box.setText(body)
                        box.setStandardButtons(QMessageBox.Ok)
                        # Only show Retry if we have something to retry on.
                        retry_btn = None
                        if failed_device_infos:
                            retry_btn = box.addButton(
                                f"Retry Failed ({len(failed_device_infos)})",
                                QMessageBox.ActionRole,
                            )
                        box.exec_()
                        if retry_btn is not None and box.clickedButton() is retry_btn:
                            self._retry_failed_apply(failed_device_infos)
                    except Exception as _dlg_exc:
                        logger.warning(f"Could not show apply-failure dialog: {_dlg_exc}")
            
            # Check if any applied devices had VXLAN configuration (before worker is deleted)
            vxlan_applied = False
            if successful_count > 0 and hasattr(self, 'multi_device_apply_worker'):
                try:
                    for row, device_info in self.multi_device_apply_worker.devices_to_apply:
                        protocols = device_info.get("protocols", [])
                        vxlan_config = device_info.get("vxlan_config", {})
                        # Check for VXLAN in multiple formats
                        has_vxlan = False
                        if "VXLAN" in protocols:
                            has_vxlan = True
                        elif isinstance(vxlan_config, dict):
                            # Check for new format: {"tunnels": [...]}
                            if "tunnels" in vxlan_config and len(vxlan_config.get("tunnels", [])) > 0:
                                has_vxlan = True
                            # Check for old format: single dict with vni key
                            elif vxlan_config.get("vni") or len(vxlan_config) > 0:
                                has_vxlan = True
                        
                        if has_vxlan:
                            logger.info(f"Detected VXLAN in device {device_info.get('Device Name', 'Unknown')}")
                            vxlan_applied = True
                            break
                except Exception as e:
                    logger.error(f"Error checking for VXLAN: {e}")
            
            # Save session after device application to persist status changes
            if successful_count > 0 and hasattr(self.main_window, "save_session"):
                logger.info(f"Saving session after successful device application ({successful_count} device(s) applied)")
                try:
                    self.main_window.save_session()
                    logger.info(f"✅ Session saved successfully after applying {successful_count} device(s)")
                except Exception as save_exc:
                    logger.error(f"⚠️ Failed to save session: {save_exc}")
            
            # Interface list refresh is now manual only - user can click "Refresh Interface List" button if needed
            # Removed automatic refresh to prevent unnecessary UI updates
            # if vxlan_applied and hasattr(self.main_window, "update_server_tree"):
            #     print(f"[MULTI DEVICE APPLY] Refreshing interface list from server after VXLAN tunnel creation")
            #     self.main_window.update_server_tree()
            
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

            # Repaint preflight pills immediately so the operator sees
            # the impact of their multi-device push instead of waiting
            # up to 60 s for the auto-poll. Fires regardless of
            # success/fail counts — operators want to see "all clean"
            # confirmation too, not just new findings.
            try:
                from widgets.preflight_bar import kick_refresh
                kick_refresh(self)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error handling completion: {e}")

    def _on_bulk_arp_finished(self):
        """Handle bulk ARP check completion."""
        logger.info("Completed async ARP checks for all devices")
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
        logger.info("Cleaning up all worker threads...")

        # SSE worker first — its stop() forcibly closes the in-flight
        # response, unblocking iter_lines() in milliseconds. Without
        # this the GUI close could hang up to heartbeat_interval (15s).
        try:
            if self._sse_worker is not None:
                logger.info("Stopping devices-tab SSE worker...")
                try:
                    self._sse_worker.stop()
                    if self._sse_worker.isRunning():
                        self._sse_worker.wait(1500)
                except RuntimeError:
                    pass
        except Exception as exc:
            logger.debug(f"SSE worker shutdown failed: {exc}")

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
                logger.info(f"Stopping {attr}...")
                try:
                    timer.stop()
                except Exception as exc:
                    logger.error(f"Failed to stop {attr}: {exc}")

        def _stop_worker(attr_name):
            worker = getattr(self, attr_name, None)
            if not worker:
                return
            logger.info(f"Stopping {attr_name}...")
            try:
                if hasattr(worker, "stop"):
                    worker.stop()
                if worker.isRunning():
                    worker.quit()
                    if not worker.wait(1000):
                        logger.info(f"Force terminating {attr_name}...")
                        worker.terminate()
                        worker.wait(500)
                
                # Only deleteLater if thread is definitely stopped
                if not worker.isRunning():
                    worker.deleteLater()
                else:
                    logger.warning(f"WARNING: {attr_name} still running after cleanup attempt")
            except RuntimeError:
                # Worker already deleted, ignore
                pass
            except Exception as exc:
                logger.error(f"Error stopping {attr_name}: {exc}")
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
                            logger.info(f"Waiting for {list_attr_name} worker to finish...")
                            w.quit()  # Request thread to stop
                            if not w.wait(3000):
                                logger.info(f"Force terminating {list_attr_name} worker...")
                                w.terminate()
                                w.wait(500)
                        
                        # Only deleteLater if thread is definitely stopped
                        if not w.isRunning():
                            w.deleteLater()
                    except RuntimeError:
                        # Worker might already be deleted, ignore
                        continue
                    except Exception as exc:
                        logger.error(f"Error draining {list_attr_name} worker: {exc}")
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
                            logger.info("Waiting for OSPF handler worker to finish...")
                            w.quit()  # Request thread to stop
                            if not w.wait(3000):
                                logger.info("Force terminating OSPF handler worker...")
                                w.terminate()
                                w.wait(500)
                        
                        # Only deleteLater if thread is definitely stopped
                        if not w.isRunning():
                            w.deleteLater()
                    except RuntimeError:
                        # Worker already deleted, ignore
                        pass
                    except Exception as exc:
                        logger.error(f"Error cleaning up OSPF handler worker: {exc}")
                self.ospf_handler._ospf_apply_workers = []
            except Exception as exc:
                logger.error(f"Error cleaning up OSPF handler workers: {exc}")
        
        if hasattr(self, "vxlan_handler"):
            try:
                self.vxlan_handler.stop_monitoring()
            except Exception as exc:
                logger.error(f"Failed to stop VXLAN monitoring: {exc}")

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
        device_found = False
        for iface, devices in self.main_window.all_devices.items():
            for device in devices:
                if device.get("Device Name") == device_name:
                    device_found = True
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
                        # print(f"[UPDATE PROTOCOL] Updating {device_name} with {protocol} config, key: {config_key}")
                        logging.debug(f"[UPDATE PROTOCOL] Updating {device_name} with {protocol} config, key: {config_key}")
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
                                # v0.5.213: preserve route_pools attachments when editing config —
                                # parity with the OSPF branch below. Without this the Attach Route
                                # Pools dialog's pool selections get silently wiped whenever the
                                # operator edits any other ISIS field (system_id, area_id, hello…)
                                # via Add/Edit dialog or inline cell edit.
                                if "route_pools" not in config and "route_pools" in existing_config:
                                    merged_config["route_pools"] = existing_config["route_pools"]
                                # v0.5.209: additive Add — a fresh
                                # Add IS-IS call must not silently
                                # disable an AF that's already up.
                                # Pre-fix: user opens Add IS-IS,
                                # unchecks IPv4 (to indicate "only
                                # add v6, please"), clicks Add — and
                                # the merged_config.update(config)
                                # above set ipv4_enabled=False,
                                # losing the existing IPv4 IS-IS
                                # topology. Now: if existing had it
                                # enabled, it stays enabled — to
                                # remove an AF, use the Delete
                                # button on that row (v0.5.207).
                                if existing_config.get("ipv4_enabled"):
                                    merged_config["ipv4_enabled"] = True
                                if existing_config.get("ipv6_enabled"):
                                    merged_config["ipv6_enabled"] = True
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
                                # v0.5.209: additive Add — same
                                # shape as the ISIS branch above.
                                # Operator report on JNPR-MAC-
                                # HWXVX1 2026-08-23: "tried to add
                                # ospf ipv6, it overwritten the
                                # existing ipv4 ospf in the ospf
                                # table". Root cause: the
                                # `merged_config.update(config)`
                                # above overwrites `ipv4_enabled`
                                # with the dialog's False when the
                                # operator only checks IPv6 for the
                                # new Add. Additive-preserve keeps
                                # the existing True sticky — Add
                                # only adds AFs, never removes them.
                                # To remove an AF, use the per-AF
                                # Delete on that row (v0.5.205).
                                if existing_config.get("ipv4_enabled"):
                                    merged_config["ipv4_enabled"] = True
                                if existing_config.get("ipv6_enabled"):
                                    merged_config["ipv6_enabled"] = True
                            device[config_key] = merged_config
                            # For ISIS, also update isis_config for backward compatibility
                            if protocol in ["IS-IS", "ISIS"]:
                                device["isis_config"] = merged_config
                        else:
                            # No existing config, use new config as-is
                            device[config_key] = config
                            # print(f"[UPDATE PROTOCOL] Stored new {protocol} config for {device_name}: {list(config.keys())}")
                            # print(f"[UPDATE PROTOCOL] Config content: {config}")
                            logging.debug(f"[UPDATE PROTOCOL] Stored new {protocol} config for {device_name}: {list(config.keys())}")
                            # For ISIS, also update isis_config for backward compatibility
                            if protocol in ["IS-IS", "ISIS"]:
                                device["isis_config"] = config
                    else:
                        # If protocols is a dict (old format), store config there
                        device["protocols"][protocol] = config
                    
                    # print(f"[UPDATE PROTOCOL] Device {device_name} now has vxlan_config: {bool(device.get('vxlan_config'))}")
                    # if device.get('vxlan_config'):
                    #     print(f"[UPDATE PROTOCOL] vxlan_config content: {device.get('vxlan_config')}")
                    # print(f"[UPDATE PROTOCOL] Device protocols: {device.get('protocols')}")
                    logging.debug(f"[UPDATE PROTOCOL] Device {device_name} now has vxlan_config: {bool(device.get('vxlan_config'))}")
                    break
        
        if not device_found:
            # print(f"[UPDATE PROTOCOL] WARNING: Device '{device_name}' not found in all_devices")
            # print(f"[UPDATE PROTOCOL] Available devices: {[d.get('Device Name') for iface, devices in self.main_window.all_devices.items() for d in devices]}")
            logging.warning(f"[UPDATE PROTOCOL] Device '{device_name}' not found in all_devices")
        
        # Update the protocol-specific tables based on the protocol
        # Temporarily disconnect cellChanged signals to prevent infinite loops
        if protocol == "BGP":
            # Temporarily disconnect to prevent infinite loop
            self.bgp_table.cellChanged.disconnect()
            self.update_bgp_table()
            # v0.5.202: BGPHandler.__init__ wires the REAL edit
            # handler (self.bgp_handler.on_bgp_table_cell_changed)
            # to cellChanged — that's the one that actually writes
            # timer / neighbor / source edits back into bgp_config.
            # The disconnect() above blows away every connected slot;
            # pre-fix the reconnect below only wired the DevicesTab
            # `pass`-only stub, so every inline edit after the first
            # protocol-update pass got silently dropped (operator
            # symptom: hold-time reverts to default 90 on Apply).
            # Reconnect BOTH the stub (harmless) and the real handler
            # so persistence survives update_bgp_table.
            self.bgp_table.cellChanged.connect(self.on_bgp_table_cell_changed)
            self.bgp_table.cellChanged.connect(self.bgp_handler.on_bgp_table_cell_changed)
        elif protocol == "OSPF":
            # v0.5.203: the old body here was literally `pass` with a
            # comment claiming the table would "refresh on the next
            # periodic update or when Apply is clicked". Result: the
            # operator hit Add OSPF, filled the dialog, clicked add,
            # and saw NOTHING appear in the OSPF table — the added
            # config was silently sitting in device_info["ospf_config"]
            # waiting for a periodic tick. Mirror the BGP fix: refresh
            # the table now, and reconnect both stub + real handler so
            # inline edits still persist after the rebuild.
            try:
                self.ospf_table.cellChanged.disconnect()
            except TypeError:
                pass
            self.update_ospf_table()
            self.ospf_table.cellChanged.connect(self.on_ospf_table_cell_changed)
            self.ospf_table.cellChanged.connect(self.ospf_handler.on_ospf_table_cell_changed)
        elif protocol == "IS-IS" or protocol == "ISIS":
            # v0.5.203: same fix for ISIS — was `pass`, now refresh
            # the table + reconnect both handlers so Add ISIS shows
            # up in the table immediately.
            try:
                self.isis_table.cellChanged.disconnect()
            except TypeError:
                pass
            self.update_isis_table()
            self.isis_table.cellChanged.connect(self.on_isis_table_cell_changed)
            self.isis_table.cellChanged.connect(self.isis_handler.on_isis_table_cell_changed)
        
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
        result = self.bgp_handler.apply_bgp_configurations()
        # Refresh preflight pills — BGP edits flip BGP_NO_REMOTE_ASN /
        # BGP_NO_LOOPBACK finding state immediately.
        try:
            from widgets.preflight_bar import kick_refresh
            kick_refresh(self)
        except Exception:
            pass
        return result
    def start_bgp_protocol(self):
        """Start BGP protocol for selected devices."""
        return self.bgp_handler.start_bgp_protocol()
    def stop_bgp_protocol(self):
        """Stop BGP protocol for selected devices."""
        return self.bgp_handler.stop_bgp_protocol()
    def apply_ospf_configurations(self):
        """Apply OSPF configurations to the server for selected OSPF table rows."""
        result = self.ospf_handler.apply_ospf_configurations()
        # Refresh preflight pills — OSPF area edits flip OSPF_NO_AREA
        # finding state immediately.
        try:
            from widgets.preflight_bar import kick_refresh
            kick_refresh(self)
        except Exception:
            pass
        return result
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
                
                logger.info(f"Selected devices from BGP table: {selected_device_names}")
                logger.info(f"Selected neighbors: {selected_bgp_neighbors}")
                logger.info(f"selected_bgp_neighbors type: {type(selected_bgp_neighbors)}")
                logger.info(f"selected_bgp_neighbors length: {len(selected_bgp_neighbors)}")
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
                logger.info(f"Selected devices from OSPF table: {selected_device_names}")

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
                            logger.info(f"[{protocol} TOGGLE] Including selected device: {device.get('Device Name')}")
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
                    logger.info(f"Checking device_name '{device_name}' against selected_bgp_neighbors keys: {list(selected_bgp_neighbors.keys())}")
                    if device_name in selected_bgp_neighbors:
                        payload["selected_neighbors"] = list(selected_bgp_neighbors[device_name])
                        logger.info(f"Adding selected neighbors for {device_name}: {payload['selected_neighbors']}")
                    else:
                        logger.info(f"Device '{device_name}' not found in selected_bgp_neighbors")
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
        logger.debug(f"\n{'='*60}")
        logger.info(f"{protocol.upper()} {action.upper()} RESULTS: {success_count}/{len(devices_with_protocol)} successful")
        logger.debug(f"{'='*60}")
        if success_count > 0:
            logger.info(f"  ✅ Successfully {action}ed {protocol} for {success_count} device(s)")
        if success_count < len(devices_with_protocol):
            failed = len(devices_with_protocol) - success_count
            logger.error(f"  ❌ Failed to {action} {protocol} for {failed} device(s)")
        logger.debug(f"{'='*60}\n")
        
        # No popup message - status updated silently in table

        # Refresh protocol tables
        if protocol == "BGP":
            # Use QTimer to delay refresh and avoid blocking UI
            from PyQt5.QtCore import QTimer
            def delayed_refresh():
                self.update_bgp_table()
                logger.info(f"Refreshed BGP table after {action} operation")
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
                logger.info(f"Refreshed OSPF table after {action} operation")
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
                logger.info(f"Refreshed ISIS table after {action} operation")
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
            logger.info("Started periodic device status checks")
        else:
            logger.info("Already active - not starting again")
    
    def stop_device_status_monitoring(self):
        """Stop periodic device status monitoring."""
        if self.device_status_monitoring_active:
            self.device_status_monitoring_active = False
            self.device_status_timer.stop()
            logger.info("Stopped periodic device status checks")
        else:
            logger.info("Already stopped - not stopping again")
    
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
            logger.error(f"Error updating status icon for row {row}: {e}")
    
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
                elif status in ("Starting", "Stopping"):
                    # v0.5.215: include transient "Stopping" too.
                    # Pre-fix the poll only refreshed Starting/
                    # Running, so a device that got wedged in
                    # Stopping (or was told Stopping by the worker
                    # but the DB says Running/Stopped) stayed on
                    # the transient dot forever until the operator
                    # clicked a manual refresh.
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

    # ------------------------------------------------------------------
    # State-history dialog (Ctrl+H on the Devices tab)
    #
    # Reads /api/device/database/devices/<id>/history — the monitors
    # de-dup against the previous row, so this surface is the *change-
    # only* timeline (e.g. "BGP: Established → Active at 12:03"). Cheap
    # enough to fetch once per dialog open; no live polling.
    # ------------------------------------------------------------------
    def _show_selected_device_history(self):
        """Open the per-protocol state-history dialog for the selected
        row. No-op when no row is selected or the row has no device_id
        (e.g. a placeholder template row)."""
        try:
            tbl = self.devices_table
            row = tbl.currentRow()
            if row < 0:
                return
            name_col = self.COL.get("Device Name")
            if name_col is None:
                return
            name_item = tbl.item(row, name_col)
            if name_item is None:
                return
            device_id = name_item.data(Qt.UserRole)
            if not device_id:
                # Not-yet-applied row — nothing to show.
                QMessageBox.information(
                    self, "State history",
                    "This device hasn't been applied yet — no history to show.",
                )
                return
            device_name = (name_item.text() or device_id)[:40]
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return
            dlg = _DeviceStateHistoryDialog(
                self, server_url, device_id, device_name,
            )
            dlg.exec_()
        except Exception as exc:
            logging.error(f"[STATE HISTORY] open failed: {exc}")

    # ------------------------------------------------------------------
    # View Device Config (Ctrl+J)
    #
    # Read-only pretty-printed JSON of the server's stored row for the
    # selected device. No new endpoint — just /api/device/database/
    # devices/<id>. The dialog has a Copy button so users can paste
    # the config into a bug report.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Bulk-edit (toolbar ⧉ button)
    # ------------------------------------------------------------------
    def _open_bulk_edit_dialog(self):
        """Open the bulk-edit dialog for the current table selection.

        Resolves selected rows → opens dialog → applies the computed
        per-row plan back into self.main_window.all_devices, repaints,
        marks each device for re-apply, and triggers one save_session.
        """
        try:
            tbl = self.devices_table
            sel_model = tbl.selectionModel()
            if sel_model is None:
                return
            # Unique row indexes from the selection (selectItems may
            # return one QModelIndex per cell, so dedup).
            rows = sorted({idx.row() for idx in sel_model.selectedIndexes()})
            if not rows:
                QMessageBox.information(
                    self, "Bulk-edit",
                    "Select one or more device rows first."
                )
                return

            dlg = _BulkEditDialog(self, rows)
            if dlg.exec_() != QDialog.Accepted:
                return

            plans = dlg.compute_plans()
            if not plans:
                return

            self._apply_bulk_plans(plans)
        except Exception as exc:
            logging.error(f"[BULK EDIT] open failed: {exc}")
            QMessageBox.warning(self, "Bulk-edit", f"Failed: {exc}")

    def _apply_bulk_plans(self, plans):
        """Walk the list of (row_index, plan_dict) tuples and write
        every field into the all_devices structure. Block table
        signals during the writes so cellChanged doesn't fire
        save_session once per cell."""
        if not plans:
            return
        # Build a row → device_id map from the table's UserRole stash.
        name_col = self.COL.get("Device Name", 0)
        row_to_did = {}
        for row_idx, _plan in plans:
            it = self.devices_table.item(row_idx, name_col)
            did = it.data(Qt.UserRole) if it else None
            if did:
                row_to_did[row_idx] = did

        from PyQt5.QtCore import QSignalBlocker
        _blocker = QSignalBlocker(self.devices_table)  # noqa: F841

        # Locate each device once and apply all its updates.
        all_devs = getattr(self.main_window, "all_devices", {}) or {}
        applied_count = 0
        for row_idx, plan in plans:
            did = row_to_did.get(row_idx)
            if not did:
                continue
            for iface, dev_list in all_devs.items():
                for device in dev_list:
                    if device.get("device_id") != did:
                        continue
                    self._apply_plan_to_device(device, plan)
                    applied_count += 1
                    # Mark for re-apply so the next "Apply" button-click
                    # pushes the new values to the server.
                    try:
                        device["_needs_apply"] = True
                    except Exception:
                        pass

        # Repaint + persist.
        try:
            self.update_device_table(self.main_window.all_devices)
        except Exception as exc:
            logging.debug(f"[BULK EDIT] table repaint failed: {exc}")
        if hasattr(self.main_window, "save_session"):
            try:
                self.main_window.save_session()
            except Exception as exc:
                logging.debug(f"[BULK EDIT] save_session failed: {exc}")

        QMessageBox.information(
            self, "Bulk-edit",
            f"Applied changes to {applied_count} device(s). "
            f"Click ✓ (Apply) on the toolbar to push to the server."
        )

    def _apply_plan_to_device(self, device, plan):
        """Write a single plan dict into one device record. Handles
        the protocol-toggle special-case (_proto_XXX keys) separately
        from regular field assignments."""
        protocol_overrides = {}
        for k, v in plan.items():
            if k.startswith("_proto_"):
                protocol_overrides[k[len("_proto_"):]] = bool(v)
                continue
            # Direct column → device-dict key mapping. The all_devices
            # dict uses the table-column names as keys (verified via
            # update_device_data_in_memory's mapping).
            device[k] = v

        if not protocol_overrides:
            return

        # Protocol-list mutation. The device's "protocols" can be a
        # list ["BGP", "OSPF"] or older dict shape; handle both.
        protos = device.get("protocols")
        if isinstance(protos, dict):
            # Legacy shape — convert to list while preserving any
            # config blocks under the protocol-name keys.
            protos = list(protos.keys())
        if not isinstance(protos, list):
            protos = []
        proto_set = {p.upper() for p in protos if isinstance(p, str)}

        for proto, want in protocol_overrides.items():
            if want:
                proto_set.add(proto.upper())
            else:
                proto_set.discard(proto.upper())
        device["protocols"] = sorted(proto_set)

    def _show_selected_device_config(self):
        try:
            tbl = self.devices_table
            row = tbl.currentRow()
            if row < 0:
                return
            name_col = self.COL.get("Device Name")
            if name_col is None:
                return
            name_item = tbl.item(row, name_col)
            if name_item is None:
                return
            device_id = name_item.data(Qt.UserRole)
            if not device_id:
                QMessageBox.information(
                    self, "Device config",
                    "This device hasn't been applied yet — server has no copy to view.",
                )
                return
            device_name = (name_item.text() or device_id)[:40]
            server_url = self.get_server_url(silent=True)
            if not server_url:
                return
            try:
                r = requests.get(
                    f"{server_url}/api/device/database/devices/{device_id}",
                    timeout=5,
                )
            except Exception as exc:
                QMessageBox.warning(self, "Device config", f"Request failed: {exc}")
                return
            if r.status_code != 200:
                QMessageBox.warning(
                    self, "Device config",
                    f"HTTP {r.status_code}: {r.text[:200]}",
                )
                return
            payload = r.json()
            dlg = _DeviceConfigViewerDialog(self, device_name, device_id, payload)
            dlg.exec_()
        except Exception as exc:
            logging.error(f"[VIEW CONFIG] open failed: {exc}")


# =====================================================================
# State-history dialog
# =====================================================================

class _DeviceStateHistoryDialog(QDialog):
    """Per-protocol state-transition timeline for one device.

    One tab per protocol (BGP / OSPF / ISIS / ARP / DHCP), each with a
    small table: timestamp, state, detail (JSON, truncated). Empty tabs
    are still shown so the user can see "this protocol has no recorded
    transitions yet" — that distinction matters when triaging.
    """

    PROTOCOLS = ("bgp", "ospf", "isis", "arp", "dhcp")

    def __init__(self, parent, server_url: str, device_id: str, device_name: str):
        super().__init__(parent)
        self._server_url = server_url.rstrip("/")
        self._device_id = device_id
        self.setWindowTitle(f"State history — {device_name}")
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        header = QLabel(f"<b>{device_name}</b> &nbsp;<small>{device_id}</small>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        self._tabs = QTabWidget(self)
        layout.addWidget(self._tabs, 1)

        # Build one tab per protocol; populate lazily on first show so a
        # device with empty history doesn't pay 5 sequential HTTP calls
        # up front. We just kick the first tab now.
        self._fetched = set()
        for proto in self.PROTOCOLS:
            tab = QWidget()
            v = QVBoxLayout(tab)
            v.setContentsMargins(4, 4, 4, 4)
            tbl = QTableWidget(0, 3, tab)
            tbl.setHorizontalHeaderLabels(["Timestamp (UTC)", "State", "Detail"])
            tbl.horizontalHeader().setStretchLastSection(True)
            tbl.verticalHeader().setVisible(False)
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
            tbl.setSelectionBehavior(QTableWidget.SelectRows)
            tbl.setColumnWidth(0, 180)
            tbl.setColumnWidth(1, 120)
            v.addWidget(tbl)
            tab._history_table = tbl  # stash for population
            self._tabs.addTab(tab, proto.upper())

        self._tabs.currentChanged.connect(self._on_tab_changed)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_current_tab)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # Prime the first tab.
        self._on_tab_changed(0)

    # ------------------------------------------------------------------
    def _on_tab_changed(self, idx: int):
        if idx < 0 or idx >= len(self.PROTOCOLS):
            return
        proto = self.PROTOCOLS[idx]
        if proto in self._fetched:
            return
        self._fetched.add(proto)
        self._populate_tab(idx, proto)

    def _refresh_current_tab(self):
        idx = self._tabs.currentIndex()
        if idx < 0:
            return
        proto = self.PROTOCOLS[idx]
        self._fetched.discard(proto)
        self._on_tab_changed(idx)

    # ------------------------------------------------------------------
    def _populate_tab(self, idx: int, proto: str):
        tab = self._tabs.widget(idx)
        tbl = getattr(tab, "_history_table", None)
        if tbl is None:
            return
        tbl.setRowCount(0)
        url = (
            f"{self._server_url}/api/device/database/devices/"
            f"{self._device_id}/history/{proto}?limit=50"
        )
        # Modal dialog → blocking is acceptable, but the timeout must
        # be short enough that 5 sequential tab-switches don't lock
        # the user out for half a minute on a slow server.
        try:
            r = requests.get(url, timeout=3)
            if r.status_code != 200:
                tbl.setRowCount(1)
                tbl.setItem(0, 0, QTableWidgetItem(f"HTTP {r.status_code}"))
                return
            rows = (r.json() or {}).get("history") or []
        except Exception as exc:
            tbl.setRowCount(1)
            tbl.setItem(0, 0, QTableWidgetItem(f"error: {exc}"))
            return

        if not rows:
            tbl.setRowCount(1)
            item = QTableWidgetItem("(no transitions recorded yet)")
            item.setForeground(QColor("#888"))
            tbl.setItem(0, 0, item)
            tbl.setSpan(0, 0, 1, 3)
            return

        tbl.setRowCount(len(rows))
        for i, row in enumerate(rows):
            ts = str(row.get("timestamp", ""))[:19].replace("T", " ")
            state = str(row.get("state", ""))
            detail = row.get("detail")
            try:
                detail_str = json.dumps(detail, separators=(",", ":")) if detail else ""
            except Exception:
                detail_str = str(detail)
            if len(detail_str) > 120:
                detail_str = detail_str[:117] + "..."
            tbl.setItem(i, 0, QTableWidgetItem(ts))
            tbl.setItem(i, 1, QTableWidgetItem(state))
            tbl.setItem(i, 2, QTableWidgetItem(detail_str))


# =====================================================================
# View-Device-Config dialog (Ctrl+J)
# =====================================================================

class _DeviceConfigViewerDialog(QDialog):
    """Pretty-printed JSON of one device's server-side config.

    Read-only by design — this is a *viewer*, not an editor. To change
    a value, use the inline cell edits on the table (the canonical
    write path that also updates `all_devices` and re-applies).
    """

    def __init__(self, parent, device_name: str, device_id: str, payload: dict):
        super().__init__(parent)
        self.setWindowTitle(f"Device config — {device_name}")
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        header = QLabel(
            f"<b>{device_name}</b> &nbsp;<small>{device_id}</small>"
        )
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        try:
            text = json.dumps(payload, indent=2, sort_keys=True, default=str)
        except Exception:
            text = str(payload)

        self._text = QTextEdit(self)
        self._text.setPlainText(text)
        self._text.setReadOnly(True)
        # Monospace so JSON indentation lines up.
        font = QFont("Menlo")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        self._text.setFont(font)
        layout.addWidget(self._text, 1)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy to clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy_to_clipboard(self):
        try:
            QApplication.clipboard().setText(self._text.toPlainText())
        except Exception as exc:
            logging.debug(f"[VIEW CONFIG] clipboard copy failed: {exc}")


# =====================================================================
# Bulk-edit dialog (operates on multi-row selection in Devices tab)
# =====================================================================
#
# Operator pattern this solves: "I added 8 routers in one batch via
# the Add Device dialog. Now I need to bump each one's VLAN to a
# unique tag, each one's loopback to a unique address, and toggle BGP
# on for half of them." Previously that meant editing each row one by
# one — 8 dialogs, 8 saves. Now: select all 8, click bulk-edit, set
# VLAN=100 step=1 + Loopback IPv4=192.255.0.1 step=1 → apply.

class _BulkEditDialog(QDialog):
    """Multi-device bulk-edit + auto-increment.

    For each editable field, the operator picks a starting value and
    a step. The first selected row gets `start`; the second gets
    `increment(start, step)`; etc. IP/MAC use the existing increment
    helpers so wrap-around, byte-position, and validation are
    consistent with the Add Device dialog's batch mode.
    """

    def __init__(self, parent_tab, selected_rows):
        super().__init__(parent_tab)
        self._parent_tab = parent_tab
        self._rows = list(selected_rows)
        self.setWindowTitle(f"Bulk-edit {len(self._rows)} device(s)")
        self.setMinimumSize(560, 480)

        layout = QVBoxLayout(self)
        header = QLabel(
            f"<b>Bulk-edit {len(self._rows)} selected device(s)</b><br/>"
            f"<span style='color:#6b7280;font-size:11px;'>"
            f"Enable a field, set start + step. First selected row gets the "
            f"start value; each subsequent row increments by step.</span>"
        )
        header.setTextFormat(Qt.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        # ── Field grid ────────────────────────────────────────────────
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self._fields = {}  # key → (enable_checkbox, start_lineedit, step_spin)

        def _row(key, label, default_value, default_step, step_min=0, step_max=10000):
            chk = QCheckBox()
            start = QLineEdit(default_value)
            step = QSpinBox()
            step.setRange(step_min, step_max)
            step.setValue(default_step)
            step.setEnabled(False)
            start.setEnabled(False)
            chk.toggled.connect(start.setEnabled)
            chk.toggled.connect(step.setEnabled)
            inner = QHBoxLayout()
            inner.setContentsMargins(0, 0, 0, 0)
            inner.addWidget(chk)
            inner.addWidget(start, 2)
            inner.addWidget(QLabel("step"))
            inner.addWidget(step)
            holder = QWidget()
            holder.setLayout(inner)
            form.addRow(label, holder)
            self._fields[key] = (chk, start, step)

        _row("vlan",     "VLAN",          "100",          1)
        _row("ipv4",     "IPv4",          "192.168.0.2",  1)
        _row("ipv4_gw",  "IPv4 Gateway",  "192.168.0.1",  0)
        _row("loopback", "Loopback IPv4", "192.255.0.1",  1)
        _row("mac",      "MAC",           "00:11:22:33:44:55", 1)

        layout.addLayout(form)

        # ── Protocol-toggle override (independent of auto-inc) ────────
        proto_box = QGroupBox("Protocol toggles (applied to every selected device)")
        proto_layout = QHBoxLayout(proto_box)
        self._proto_checks = {}
        for p in ("BGP", "OSPF", "ISIS", "DHCP", "VXLAN"):
            cb = QCheckBox(p)
            cb.setTristate(True)
            cb.setCheckState(Qt.PartiallyChecked)  # default: leave as-is
            cb.setToolTip(
                "Unchecked: disable on every selected device.\n"
                "Checked: enable on every selected device.\n"
                "Partial: leave the device's current setting."
            )
            proto_layout.addWidget(cb)
            self._proto_checks[p] = cb
        layout.addWidget(proto_box)

        # ── Preview (what's about to be written) ──────────────────────
        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(120)
        self._preview.setStyleSheet(
            "font-family: monospace; font-size: 11px; background: #f9fafb;"
        )
        layout.addWidget(QLabel("Preview:"))
        layout.addWidget(self._preview)

        for chk, start, step in self._fields.values():
            chk.toggled.connect(self._refresh_preview)
            start.textChanged.connect(self._refresh_preview)
            step.valueChanged.connect(self._refresh_preview)
        for cb in self._proto_checks.values():
            cb.stateChanged.connect(self._refresh_preview)
        self._refresh_preview()

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        ok = QPushButton(f"Apply to {len(self._rows)} device(s)")
        ok.clicked.connect(self.accept)
        ok.setStyleSheet("QPushButton { font-weight: 600; }")
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _refresh_preview(self):
        """Render the first 3 + last 1 rows of what'll be applied so
        the operator can sanity-check the auto-increment before
        committing. Empty when no field is enabled."""
        plans = self.compute_plans()
        if not plans:
            self._preview.setPlainText("(no fields enabled)")
            return
        lines = []
        n = len(plans)
        idxs = list(range(min(n, 3)))
        if n > 4:
            idxs.append(n - 1)
        for i in idxs:
            if i == n - 1 and n > 4 and i > idxs[-2]:
                lines.append(f"  …")
            row_idx, plan = plans[i]
            kv = ", ".join(f"{k}={v}" for k, v in plan.items())
            lines.append(f"  row {row_idx}: {kv}")
        self._preview.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    def compute_plans(self):
        """For each selected row, build the dict of {field: new_value}
        to apply. Returns list of (row_index, plan_dict) tuples in
        selection order. Empty list when no fields are enabled."""
        any_enabled = any(chk.isChecked() for (chk, _, _) in self._fields.values())
        proto_active = any(
            cb.checkState() != Qt.PartiallyChecked
            for cb in self._proto_checks.values()
        )
        if not any_enabled and not proto_active:
            return []

        plans = []
        for i, row_idx in enumerate(self._rows):
            plan = {}

            # VLAN — integer increment, clamped to 1..4094
            chk, start, step = self._fields["vlan"]
            if chk.isChecked():
                try:
                    base = int(start.text().strip() or "0")
                    plan["VLAN"] = str(max(0, min(4094, base + i * step.value())))
                except ValueError:
                    pass

            # IPv4 / IPv4 gateway / Loopback IPv4 — last-octet increment
            for key, col in (
                ("ipv4",     "IPv4"),
                ("ipv4_gw",  "IPv4 Gateway"),
                ("loopback", "Loopback IPv4"),
            ):
                chk, start, step = self._fields[key]
                if chk.isChecked():
                    base = start.text().strip()
                    try:
                        new_v = self._parent_tab._increment_ipv4(
                            base, i * step.value(), octet_index=3,
                        )
                        plan[col] = new_v
                    except Exception:
                        pass

            # MAC — last-byte increment
            chk, start, step = self._fields["mac"]
            if chk.isChecked():
                base = start.text().strip()
                try:
                    new_v = self._parent_tab._increment_mac(
                        base, i * step.value(), byte_index=5,
                    )
                    plan["MAC Address"] = new_v
                except Exception:
                    pass

            # Protocol toggles — applied uniformly, no per-row variation
            for p, cb in self._proto_checks.items():
                state = cb.checkState()
                if state == Qt.PartiallyChecked:
                    continue   # leave as-is
                plan[f"_proto_{p}"] = (state == Qt.Checked)

            if plan:
                plans.append((row_idx, plan))
        return plans
