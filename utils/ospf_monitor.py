"""
OSPF Status Monitoring System for OSTG
Multi-threaded OSPF status monitoring with database updates
"""

import threading
import time
import logging
import requests
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# Configure logging
logger = logging.getLogger(__name__)

class OSPFStatusMonitor:
    """Multi-threaded OSPF status monitoring system"""
    
    def __init__(self, device_db, server_url: str = "http://localhost:5051", 
                 check_interval: int = 30, max_workers: int = 5):
        """
        Initialize OSPF status monitor.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
            check_interval: Interval between OSPF status checks (seconds)
            max_workers: Maximum number of worker threads
        """
        self.device_db = device_db
        self.server_url = server_url
        self.check_interval = check_interval
        self.max_workers = max_workers
        self.is_running = False
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.status_queue = queue.Queue()
        
        logger.info(f"[OSPF MONITOR] Initialized with interval={check_interval}s, workers={max_workers}")
    
    def start(self):
        """Start the OSPF status monitoring thread."""
        if self.is_running:
            logger.warning("[OSPF MONITOR] Monitor is already running")
            return
        
        self.is_running = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[OSPF MONITOR] Started OSPF status monitoring")
    
    def stop(self):
        """Stop the OSPF status monitoring thread."""
        if not self.is_running:
            logger.warning("[OSPF MONITOR] Monitor is not running")
            return
        
        self.is_running = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        logger.info("[OSPF MONITOR] Stopped OSPF status monitoring")
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("[OSPF MONITOR] Monitoring loop started")
        
        while self.is_running and not self.stop_event.is_set():
            try:
                # Get OSPF-enabled devices
                devices = self._get_ospf_devices()
                
                if devices:
                    logger.info(f"[OSPF MONITOR] Checking OSPF status for {len(devices)} devices")
                    self._check_ospf_status_batch(devices)
                else:
                    logger.info("[OSPF MONITOR] No OSPF devices found")
                
                # Wait for next check interval
                self.stop_event.wait(self.check_interval)
                
            except Exception as e:
                logger.error(f"[OSPF MONITOR] Error in monitoring loop: {e}")
                self.stop_event.wait(5)  # Wait 5 seconds before retrying
        
        logger.info("[OSPF MONITOR] Monitoring loop ended")
    
    def _get_ospf_devices(self) -> List[Dict[str, Any]]:
        """Get all devices that have OSPF configured (only running devices, like BGP/ISIS)."""
        try:
            devices = self.device_db.get_all_devices()
            ospf_devices = []
            
            for device in devices:
                # Only check running devices (like BGP/ISIS monitors do)
                if device.get('status') != 'Running':
                    continue
                
                protocols = device.get("protocols", [])
                if isinstance(protocols, str):
                    try:
                        protocols = json.loads(protocols)
                    except:
                        protocols = []
                
                if "OSPF" in protocols:
                    ospf_devices.append(device)
            
            return ospf_devices
            
        except Exception as e:
            logger.error(f"[OSPF MONITOR] Error getting OSPF devices: {e}")
            return []
    
    def _check_ospf_status_batch(self, devices: List[Dict[str, Any]]):
        """Check OSPF status for multiple devices in parallel."""
        try:
            logger.info(f"[OSPF MONITOR] Starting batch OSPF status check for {len(devices)} devices")
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all OSPF status check tasks
                future_to_device = {
                    executor.submit(self._check_single_device_ospf_status, device): device
                    for device in devices
                }
                
                logger.info(f"[OSPF MONITOR] Submitted {len(future_to_device)} OSPF status check tasks")
                
                # Collect results as they complete
                for future in as_completed(future_to_device):
                    device = future_to_device[future]
                    try:
                        ospf_status = future.result()
                        logger.info(f"[OSPF MONITOR] Got OSPF status for device {device.get('device_id', 'unknown')}: {ospf_status is not None}")
                        
                        if ospf_status:
                            logger.info(f"[OSPF MONITOR] OSPF status data: {ospf_status}")
                            logger.info(f"[OSPF MONITOR] Calling _update_device_ospf_status for device {device.get('device_id', 'unknown')}")
                            self._update_device_ospf_status(device["device_id"], ospf_status)
                            logger.info(f"[OSPF MONITOR] Completed _update_device_ospf_status for device {device.get('device_id', 'unknown')}")
                        else:
                            logger.info(f"[OSPF MONITOR] No OSPF status returned for device {device.get('device_id', 'unknown')}")
                            
                    except Exception as e:
                        logger.error(f"[OSPF MONITOR] Error checking OSPF status for device {device.get('device_id', 'unknown')}: {e}")
                        
        except Exception as e:
            logger.error(f"[OSPF MONITOR] Error in batch OSPF status check: {e}")
    
    def _check_single_device_ospf_status(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check OSPF status for a single device."""
        try:
            device_id = device.get("device_id")
            if not device_id:
                logger.warning(f"[OSPF MONITOR] No device_id for device: {device}")
                return None
            
            logger.info(f"[OSPF MONITOR] Checking OSPF status for device {device_id}")
            
            # Make API call to get OSPF status
            url = f"{self.server_url}/api/ospf/status/{device_id}"
            logger.info(f"[OSPF MONITOR] Making API call to: {url}")
            response = requests.get(url, timeout=10)
            
            logger.info(f"[OSPF MONITOR] API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"[OSPF MONITOR] API response data: {data}")
                ospf_status = data.get("ospf_status")
                logger.info(f"[OSPF MONITOR] Extracted OSPF status: {ospf_status}")
                return ospf_status
            elif response.status_code == 404:
                # Device not found or container doesn't exist - this is normal for deleted devices
                logger.info(f"[OSPF MONITOR] Device {device_id} not found or container missing (404)")
                return None
            else:
                logger.warning(f"[OSPF MONITOR] Failed to get OSPF status for device {device_id}: HTTP {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"[OSPF MONITOR] Request error for device {device.get('device_id', 'unknown')}: {e}")
            return None
        except Exception as e:
            logger.error(f"[OSPF MONITOR] Error checking OSPF status for device {device.get('device_id', 'unknown')}: {e}")
            return None
    
    def _update_device_ospf_status(self, device_id: str, ospf_status: Dict[str, Any]):
        """Update OSPF status in database for a device."""
        try:
            # Check if there's a manual override in place
            device_data = self.device_db.get_device(device_id)
            if device_data:
                manual_override = device_data.get('ospf_manual_override', False)
                manual_override_time = device_data.get('ospf_manual_override_time')
                
                if manual_override and manual_override_time:
                    # Check if manual override is recent (within last 2 minutes)
                    try:
                        override_time = datetime.fromisoformat(manual_override_time.replace('Z', '+00:00'))
                        current_time = datetime.now(timezone.utc)
                        time_diff = (current_time - override_time).total_seconds()
                        
                        if time_diff < 120:  # 2 minutes
                            logger.info(f"[OSPF MONITOR] Skipping update for device {device_id} - manual override active ({time_diff:.1f}s ago)")
                            return
                        else:
                            logger.info(f"[OSPF MONITOR] Manual override expired for device {device_id} ({time_diff:.1f}s ago), proceeding with update")
                    except Exception as e:
                        logger.warning(f"[OSPF MONITOR] Error parsing manual override time: {e}")
            
            logger.info(f"[OSPF MONITOR] Updating OSPF status for device {device_id}")
            logger.info(f"[OSPF MONITOR] OSPF status data: {ospf_status}")
            
            # Prepare OSPF status data
            ospf_established = ospf_status.get('ospf_established', False)
            ospf_state = ospf_status.get('ospf_state', 'Unknown')
            neighbors = ospf_status.get('neighbors', [])
            last_check = datetime.now(timezone.utc).isoformat()
            
            # Get IPv4 and IPv6 specific status
            ospf_ipv4_running = ospf_status.get('ospf_ipv4_running', False)
            ospf_ipv6_running = ospf_status.get('ospf_ipv6_running', False)
            ospf_ipv4_established = ospf_status.get('ospf_ipv4_established', False)
            ospf_ipv6_established = ospf_status.get('ospf_ipv6_established', False)
            ospf_ipv4_uptime = ospf_status.get('ospf_ipv4_uptime', None)
            ospf_ipv6_uptime = ospf_status.get('ospf_ipv6_uptime', None)

            # Update device statistics table
            self.device_db.update_device_statistics(device_id, {
                'ospf_established': ospf_established,
                'ospf_state': ospf_state,
                'ospf_neighbors': json.dumps(neighbors) if neighbors else None,
                'last_ospf_check': last_check,
                'ospf_ipv4_running': ospf_ipv4_running,
                'ospf_ipv6_running': ospf_ipv6_running,
                'ospf_ipv4_established': ospf_ipv4_established,
                'ospf_ipv6_established': ospf_ipv6_established,
                'ospf_ipv4_uptime': ospf_ipv4_uptime,
                'ospf_ipv6_uptime': ospf_ipv6_uptime
            })

            # Update main devices table with OSPF status
            update_data = {
                'ospf_established': ospf_established,
                'ospf_state': ospf_state,
                'last_ospf_check': last_check,
                'ospf_neighbors': json.dumps(neighbors) if neighbors else None,
                'ospf_ipv4_running': ospf_ipv4_running,
                'ospf_ipv6_running': ospf_ipv6_running,
                'ospf_ipv4_established': ospf_ipv4_established,
                'ospf_ipv6_established': ospf_ipv6_established,
                'ospf_ipv4_uptime': ospf_ipv4_uptime,
                'ospf_ipv6_uptime': ospf_ipv6_uptime
            }
            
            # Clear manual override flag when monitor takes over
            if device_data and device_data.get('ospf_manual_override', False):
                update_data['ospf_manual_override'] = False
                update_data['ospf_manual_override_time'] = None
                logger.info(f"[OSPF MONITOR] Cleared manual override flag for device {device_id}")
            
            logger.info(f"[OSPF MONITOR] Update data: {update_data}")
            
            result = self.device_db.update_device(device_id, update_data)
            logger.info(f"[OSPF MONITOR] Database update result: {result}")
            
            # Log OSPF status event
            self.device_db.log_device_event(device_id, "ospf_status_check", {
                'ospf_established': ospf_established,
                'ospf_state': ospf_state,
                'total_neighbors': len(neighbors),
                'neighbors': neighbors
            })
            
            logger.info(f"[OSPF MONITOR] Successfully updated OSPF status for device {device_id}: {ospf_state}")
            
        except Exception as e:
            logger.error(f"[OSPF MONITOR] Error updating OSPF status for device {device_id}: {e}")
            import traceback
            logger.error(f"[OSPF MONITOR] Traceback: {traceback.format_exc()}")
    
    def force_check_all(self):
        """Force an immediate OSPF status check for all devices."""
        if not self.is_running:
            logger.warning("[OSPF MONITOR] Monitor is not running, cannot force check")
            return
        
        devices = self._get_ospf_devices()
        if devices:
            logger.info(f"[OSPF MONITOR] Force checking OSPF status for {len(devices)} devices")
            self._check_ospf_status_batch(devices)
        else:
            logger.info("[OSPF MONITOR] No OSPF devices found for force check")
    
    def get_monitor_status(self) -> Dict[str, Any]:
        """Get current monitor status."""
        return {
            'is_running': self.is_running,
            'check_interval': self.check_interval,
            'max_workers': self.max_workers,
            'server_url': self.server_url,
            'thread_alive': self.monitor_thread.is_alive() if self.monitor_thread else False
        }


class OSPFStatusManager:
    """Manager class for OSPF status monitoring"""
    
    def __init__(self, device_db, server_url: str = "http://localhost:5051"):
        """
        Initialize OSPF status manager.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
        """
        self.device_db = device_db
        self.server_url = server_url
        self.monitor = None
        self._initialize_monitor()
    
    def _initialize_monitor(self):
        """Initialize the OSPF status monitor."""
        try:
            self.monitor = OSPFStatusMonitor(
                device_db=self.device_db,
                server_url=self.server_url,
                check_interval=10,  # Check every 10 seconds
                max_workers=5       # Use 5 worker threads
            )
            logger.info("[OSPF MANAGER] OSPF status monitor initialized")
        except Exception as e:
            logger.error(f"[OSPF MANAGER] Failed to initialize OSPF status monitor: {e}")
    
    def start_monitoring(self):
        """Start OSPF status monitoring."""
        if self.monitor:
            self.monitor.start()
        else:
            logger.error("[OSPF MANAGER] Monitor not initialized")
    
    def stop_monitoring(self):
        """Stop OSPF status monitoring."""
        if self.monitor:
            self.monitor.stop()
        else:
            logger.error("[OSPF MANAGER] Monitor not initialized")
    
    def force_check(self):
        """Force an immediate OSPF status check."""
        if self.monitor:
            self.monitor.force_check_all()
        else:
            logger.error("[OSPF MANAGER] Monitor not initialized")
    
    def get_status(self) -> Dict[str, Any]:
        """Get monitor status."""
        if self.monitor:
            return self.monitor.get_monitor_status()
        else:
            return {'error': 'Monitor not initialized'}
    
    def update_check_interval(self, interval: int):
        """Update the check interval."""
        if self.monitor:
            self.monitor.check_interval = interval
            logger.info(f"[OSPF MANAGER] Updated check interval to {interval} seconds")
        else:
            logger.error("[OSPF MANAGER] Monitor not initialized")
