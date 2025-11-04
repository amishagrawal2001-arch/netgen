"""
ARP Status Monitoring System for OSTG
Multi-threaded ARP status monitoring with database updates
"""

import threading
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# Configure logging
logger = logging.getLogger(__name__)

class ARPStatusMonitor:
    """Multi-threaded ARP status monitoring system"""
    
    def __init__(self, device_db, server_url: str = "http://localhost:5051", 
                 check_interval: int = 30, max_workers: int = 5):
        """
        Initialize ARP status monitor.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
            check_interval: Interval between ARP status checks (seconds)
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
        
        logger.info(f"[ARP MONITOR] Initialized with interval={check_interval}s, workers={max_workers}")
    
    def start(self):
        """Start the ARP status monitoring thread."""
        if self.is_running:
            logger.warning("[ARP MONITOR] Monitor is already running")
            return
        
        self.is_running = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[ARP MONITOR] Started ARP status monitoring")
    
    def stop(self):
        """Stop the ARP status monitoring thread."""
        if not self.is_running:
            logger.warning("[ARP MONITOR] Monitor is not running")
            return
        
        self.is_running = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
            if self.monitor_thread.is_alive():
                logger.warning("[ARP MONITOR] Monitor thread did not stop gracefully")
        
        logger.info("[ARP MONITOR] Stopped ARP status monitoring")
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("[ARP MONITOR] Monitoring loop started")
        
        while not self.stop_event.is_set():
            try:
                # Get all devices that need ARP monitoring
                devices = self._get_arp_devices()
                
                if devices:
                    logger.info(f"[ARP MONITOR] Checking ARP status for {len(devices)} devices")
                    self._check_arp_status_batch(devices)
                else:
                    logger.debug("[ARP MONITOR] No ARP devices found")
                
                # Wait for next check interval
                if self.stop_event.wait(self.check_interval):
                    break
                    
            except Exception as e:
                logger.error(f"[ARP MONITOR] Error in monitoring loop: {e}")
                # Continue monitoring even if there's an error
                if self.stop_event.wait(5):  # Wait 5 seconds before retrying
                    break
        
        logger.info("[ARP MONITOR] Monitoring loop ended")
    
    def _get_arp_devices(self) -> List[Dict[str, Any]]:
        """Get all devices that need ARP monitoring."""
        try:
            devices = self.device_db.get_all_devices()
            arp_devices = []
            
            for device in devices:
                # Check if device is running and has IP addresses configured
                if (device.get('status') == 'Running' and 
                    (device.get('ipv4_address') or device.get('ipv6_address'))):
                    arp_devices.append(device)
            
            return arp_devices
            
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error getting ARP devices: {e}")
            return []
    
    def _check_arp_status_batch(self, devices: List[Dict[str, Any]]):
        """Check ARP status for multiple devices in parallel."""
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all ARP status check tasks
                future_to_device = {
                    executor.submit(self._check_single_device_arp_status, device): device 
                    for device in devices
                }
                
                # Process completed tasks
                for future in as_completed(future_to_device):
                    device = future_to_device[future]
                    try:
                        arp_status = future.result()
                        if arp_status:
                            self._update_device_arp_status(device['device_id'], arp_status)
                    except Exception as e:
                        logger.error(f"[ARP MONITOR] Error checking ARP status for device {device['device_id']}: {e}")
                        
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error in batch ARP status check: {e}")
    
    def _check_single_device_arp_status(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check ARP status for a single device."""
        device_id = device['device_id']
        
        try:
            # Call the server's ARP status API
            response = requests.get(
                f"{self.server_url}/api/device/arp/{device_id}",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract ARP status information
                arp_status = {
                    'arp_resolved': data.get('arp_resolved', False),
                    'arp_ipv4_resolved': data.get('arp_ipv4_resolved', False),
                    'arp_ipv6_resolved': data.get('arp_ipv6_resolved', False),
                    'arp_gateway_resolved': data.get('arp_gateway_resolved', False),
                    'arp_status': data.get('arp_status', 'Unknown'),
                    'last_check': datetime.now(timezone.utc).isoformat(),
                    'details': data.get('details', {})
                }
                
                return arp_status
            else:
                logger.warning(f"[ARP MONITOR] Failed to get ARP status for device {device_id}: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"[ARP MONITOR] Network error checking ARP status for device {device_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error checking ARP status for device {device_id}: {e}")
            return None
    
    def _update_device_arp_status(self, device_id: str, arp_status: Dict[str, Any]):
        """Update ARP status in the database."""
        try:
            # Update device statistics
            self.device_db.update_device_statistics(device_id, {
                'arp_resolved': arp_status['arp_resolved'],
                'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                'last_arp_check': arp_status['last_check']
            })
            
            # Update main devices table with ARP status
            self.device_db.update_device(device_id, {
                'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                'arp_status': arp_status['arp_status'],
                'last_arp_check': arp_status['last_check']
            })
            
            # Log ARP status event
            self.device_db.log_device_event(device_id, "arp_status_check", {
                'arp_resolved': arp_status['arp_resolved'],
                'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                'arp_status': arp_status['arp_status'],
                'details': arp_status['details']
            })
            
            logger.debug(f"[ARP MONITOR] Updated ARP status for device {device_id}: {arp_status['arp_status']}")
            
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error updating ARP status for device {device_id}: {e}")
    
    def force_check_all(self):
        """Force an immediate ARP status check for all devices."""
        if not self.is_running:
            logger.warning("[ARP MONITOR] Monitor is not running, cannot force check")
            return
        
        devices = self._get_arp_devices()
        if devices:
            logger.info(f"[ARP MONITOR] Force checking ARP status for {len(devices)} devices")
            self._check_arp_status_batch(devices)
        else:
            logger.info("[ARP MONITOR] No devices found for ARP force check")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current monitor status."""
        return {
            "is_running": self.is_running,
            "check_interval": self.check_interval,
            "max_workers": self.max_workers,
            "server_url": self.server_url,
            "thread_alive": self.monitor_thread.is_alive() if self.monitor_thread else False
        }
