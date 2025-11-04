"""
BGP Status Monitoring System for OSTG
Multi-threaded BGP status monitoring with database updates
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

class BGPStatusMonitor:
    """Multi-threaded BGP status monitoring system"""
    
    def __init__(self, device_db, server_url: str = "http://localhost:5051", 
                 check_interval: int = 30, max_workers: int = 5):
        """
        Initialize BGP status monitor.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
            check_interval: Interval between BGP status checks (seconds)
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
        
        logger.info(f"[BGP MONITOR] Initialized with interval={check_interval}s, workers={max_workers}")
    
    def start(self):
        """Start the BGP status monitoring thread."""
        if self.is_running:
            logger.warning("[BGP MONITOR] Monitor is already running")
            return
        
        self.is_running = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[BGP MONITOR] Started BGP status monitoring")
    
    def stop(self):
        """Stop the BGP status monitoring thread."""
        if not self.is_running:
            logger.warning("[BGP MONITOR] Monitor is not running")
            return
        
        self.is_running = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        logger.info("[BGP MONITOR] Stopped BGP status monitoring")
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("[BGP MONITOR] Monitoring loop started")
        
        while not self.stop_event.is_set():
            try:
                # Get all devices with BGP protocol
                devices = self._get_bgp_devices()
                
                if devices:
                    logger.info(f"[BGP MONITOR] Checking BGP status for {len(devices)} devices")
                    self._check_bgp_status_batch(devices)
                else:
                    logger.debug("[BGP MONITOR] No BGP devices found")
                
                # Wait for next check interval
                if self.stop_event.wait(self.check_interval):
                    break
                    
            except Exception as e:
                logger.error(f"[BGP MONITOR] Error in monitoring loop: {e}")
                # Continue monitoring even if there's an error
                if self.stop_event.wait(5):  # Wait 5 seconds before retrying
                    break
        
        logger.info("[BGP MONITOR] Monitoring loop ended")
    
    def _get_bgp_devices(self) -> List[Dict[str, Any]]:
        """Get all devices that have BGP protocol enabled."""
        try:
            devices = self.device_db.get_all_devices()
            bgp_devices = []
            
            for device in devices:
                protocols = device.get('protocols', [])
                if 'BGP' in protocols and device.get('status') == 'Running':
                    bgp_devices.append(device)
            
            return bgp_devices
            
        except Exception as e:
            logger.error(f"[BGP MONITOR] Error getting BGP devices: {e}")
            return []
    
    def _check_bgp_status_batch(self, devices: List[Dict[str, Any]]):
        """Check BGP status for multiple devices in parallel."""
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all BGP status check tasks
                future_to_device = {
                    executor.submit(self._check_single_device_bgp_status, device): device 
                    for device in devices
                }
                
                # Process completed tasks
                for future in as_completed(future_to_device):
                    device = future_to_device[future]
                    try:
                        bgp_status = future.result()
                        if bgp_status:
                            self._update_device_bgp_status(device['device_id'], bgp_status)
                    except Exception as e:
                        logger.error(f"[BGP MONITOR] Error checking BGP status for device {device['device_id']}: {e}")
                        
        except Exception as e:
            logger.error(f"[BGP MONITOR] Error in batch BGP status check: {e}")
    
    def _check_single_device_bgp_status(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check BGP status for a single device."""
        device_id = device['device_id']
        
        try:
            # Call the server's BGP status API
            response = requests.get(
                f"{self.server_url}/api/bgp/status/{device_id}",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                neighbors = data.get('neighbors', [])
                
                # Determine overall BGP status and separate IPv4/IPv6 status
                bgp_established = False
                bgp_ipv4_established = False
                bgp_ipv6_established = False
                bgp_state = "Unknown"
                bgp_ipv4_state = "Unknown"
                bgp_ipv6_state = "Unknown"
                bgp_neighbors = []
                
                for neighbor in neighbors:
                    neighbor_ip = neighbor.get('neighbor_ip', '')
                    neighbor_state = neighbor.get('state', 'Unknown')
                    
                    neighbor_status = {
                        'neighbor_ip': neighbor_ip,
                        'neighbor_as': neighbor.get('neighbor_as'),
                        'state': neighbor_state,
                        'uptime': neighbor.get('uptime')
                    }
                    bgp_neighbors.append(neighbor_status)
                    
                    # Determine if this is IPv4 or IPv6 based on IP address
                    is_ipv6 = ':' in neighbor_ip
                    
                    # Check if any neighbor is established (overall status)
                    if neighbor_state == 'Established':
                        bgp_established = True
                        bgp_state = "Established"
                        
                        # Set protocol-specific status
                        if is_ipv6:
                            bgp_ipv6_established = True
                            bgp_ipv6_state = "Established"
                        else:
                            bgp_ipv4_established = True
                            bgp_ipv4_state = "Established"
                    else:
                        # Set protocol-specific state if not established
                        if is_ipv6:
                            bgp_ipv6_state = neighbor_state
                        else:
                            bgp_ipv4_state = neighbor_state
                        
                        # Set overall state if not already established
                        if not bgp_established:
                            bgp_state = neighbor_state
                
                return {
                    'bgp_established': bgp_established,
                    'bgp_ipv4_established': bgp_ipv4_established,
                    'bgp_ipv6_established': bgp_ipv6_established,
                    'bgp_ipv4_state': bgp_ipv4_state,
                    'bgp_ipv6_state': bgp_ipv6_state,
                    'bgp_state': bgp_state,
                    'bgp_neighbors': bgp_neighbors,
                    'last_check': datetime.now(timezone.utc).isoformat(),
                    'total_neighbors': len(neighbors)
                }
            else:
                logger.warning(f"[BGP MONITOR] Failed to get BGP status for device {device_id}: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"[BGP MONITOR] Request error checking BGP status for device {device_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"[BGP MONITOR] Error checking BGP status for device {device_id}: {e}")
            return None
    
    def _update_device_bgp_status(self, device_id: str, bgp_status: Dict[str, Any]):
        """Update BGP status in the database."""
        try:
            # Check if there's a manual override in place
            device_data = self.device_db.get_device(device_id)
            if device_data:
                manual_override = device_data.get('bgp_manual_override', False)
                manual_override_time = device_data.get('bgp_manual_override_time')
                
                if manual_override and manual_override_time:
                    # Check if manual override is recent (within last 2 minutes)
                    from datetime import datetime, timezone
                    try:
                        override_time = datetime.fromisoformat(manual_override_time.replace('Z', '+00:00'))
                        current_time = datetime.now(timezone.utc)
                        time_diff = (current_time - override_time).total_seconds()
                        
                        if time_diff < 120:  # 2 minutes
                            logger.info(f"[BGP MONITOR] Skipping update for device {device_id} - manual override active ({time_diff:.1f}s ago)")
                            return
                        else:
                            logger.info(f"[BGP MONITOR] Manual override expired for device {device_id} ({time_diff:.1f}s ago), proceeding with update")
                    except Exception as e:
                        logger.warning(f"[BGP MONITOR] Error parsing manual override time: {e}")
            
            # Update device statistics
            self.device_db.update_device_statistics(device_id, {
                'bgp_established': bgp_status['bgp_established'],
                'bgp_ipv4_established': bgp_status['bgp_ipv4_established'],
                'bgp_ipv6_established': bgp_status['bgp_ipv6_established'],
                'bgp_ipv4_state': bgp_status['bgp_ipv4_state'],
                'bgp_ipv6_state': bgp_status['bgp_ipv6_state'],
                'bgp_state': bgp_status['bgp_state'],
                'bgp_neighbors': bgp_status['bgp_neighbors'],
                'last_bgp_check': bgp_status['last_check']
            })
            
            # Update main devices table with BGP status
            update_data = {
                'bgp_established': bgp_status['bgp_established'],
                'bgp_ipv4_established': bgp_status['bgp_ipv4_established'],
                'bgp_ipv6_established': bgp_status['bgp_ipv6_established'],
                'bgp_ipv4_state': bgp_status['bgp_ipv4_state'],
                'bgp_ipv6_state': bgp_status['bgp_ipv6_state'],
                'last_bgp_check': bgp_status['last_check']
            }
            
            # Clear manual override flag when monitor takes over
            if device_data and device_data.get('bgp_manual_override', False):
                update_data['bgp_manual_override'] = False
                update_data['bgp_manual_override_time'] = None
                logger.info(f"[BGP MONITOR] Cleared manual override flag for device {device_id}")
            
            self.device_db.update_device(device_id, update_data)
            
            # Log BGP status event
            self.device_db.log_device_event(device_id, "bgp_status_check", {
                'bgp_established': bgp_status['bgp_established'],
                'bgp_ipv4_established': bgp_status['bgp_ipv4_established'],
                'bgp_ipv6_established': bgp_status['bgp_ipv6_established'],
                'bgp_ipv4_state': bgp_status['bgp_ipv4_state'],
                'bgp_ipv6_state': bgp_status['bgp_ipv6_state'],
                'total_neighbors': bgp_status['total_neighbors'],
                'neighbors': bgp_status['bgp_neighbors']
            })
            
            logger.debug(f"[BGP MONITOR] Updated BGP status for device {device_id}: {bgp_status['bgp_established']}")
            
        except Exception as e:
            logger.error(f"[BGP MONITOR] Error updating BGP status for device {device_id}: {e}")
    
    def force_check_all(self):
        """Force an immediate BGP status check for all devices."""
        if not self.is_running:
            logger.warning("[BGP MONITOR] Monitor is not running, cannot force check")
            return
        
        devices = self._get_bgp_devices()
        if devices:
            logger.info(f"[BGP MONITOR] Force checking BGP status for {len(devices)} devices")
            self._check_bgp_status_batch(devices)
        else:
            logger.info("[BGP MONITOR] No BGP devices found for force check")
    
    def get_monitor_status(self) -> Dict[str, Any]:
        """Get current monitor status."""
        return {
            'is_running': self.is_running,
            'check_interval': self.check_interval,
            'max_workers': self.max_workers,
            'server_url': self.server_url,
            'thread_alive': self.monitor_thread.is_alive() if self.monitor_thread else False
        }


class BGPStatusManager:
    """Manager class for BGP status monitoring"""
    
    def __init__(self, device_db, server_url: str = "http://localhost:5051"):
        """
        Initialize BGP status manager.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
        """
        self.device_db = device_db
        self.server_url = server_url
        self.monitor = None
        self._initialize_monitor()
    
    def _initialize_monitor(self):
        """Initialize the BGP status monitor."""
        try:
            self.monitor = BGPStatusMonitor(
                device_db=self.device_db,
                server_url=self.server_url,
                check_interval=10,  # Check every 10 seconds
                max_workers=5       # Use 5 worker threads
            )
            logger.info("[BGP MANAGER] BGP status monitor initialized")
        except Exception as e:
            logger.error(f"[BGP MANAGER] Failed to initialize BGP status monitor: {e}")
    
    def start_monitoring(self):
        """Start BGP status monitoring."""
        if self.monitor:
            self.monitor.start()
        else:
            logger.error("[BGP MANAGER] Monitor not initialized")
    
    def stop_monitoring(self):
        """Stop BGP status monitoring."""
        if self.monitor:
            self.monitor.stop()
        else:
            logger.error("[BGP MANAGER] Monitor not initialized")
    
    def force_check(self):
        """Force an immediate BGP status check."""
        if self.monitor:
            self.monitor.force_check_all()
        else:
            logger.error("[BGP MANAGER] Monitor not initialized")
    
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
            logger.info(f"[BGP MANAGER] Updated check interval to {interval} seconds")
        else:
            logger.error("[BGP MANAGER] Monitor not initialized")
