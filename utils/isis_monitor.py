#!/usr/bin/env python3
"""
ISIS Monitor utility for OSTG.
Monitors ISIS status and updates the database periodically.
"""

import logging
import json
import time
import threading
from collections import defaultdict
from typing import Dict, List, Any
from datetime import datetime, timezone
from threading import Thread, Event
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# v0.5.264 (audit ISIS-F2 + ISIS-F3): sibling of v0.5.262 ARP fixes.
# Per-device write lock + log_device_event dedup.
_ISIS_WRITE_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
_ISIS_WRITE_LOCKS_META_LOCK = threading.Lock()
_LAST_ISIS_STATE_LOGGED: Dict[str, str] = {}
_LAST_ISIS_STATE_LOGGED_LOCK = threading.Lock()


def _isis_write_lock_for(device_id: str) -> threading.Lock:
    with _ISIS_WRITE_LOCKS_META_LOCK:
        return _ISIS_WRITE_LOCKS[device_id]

class ISISMonitor:
    """ISIS status monitor that runs in background."""
    
    def __init__(self, device_db, max_workers: int = 5):
        """
        Initialize ISIS monitor.

        Args:
            device_db: DeviceDatabase instance
            max_workers: v0.5.264 (audit ISIS-F4) parallel worker
                count for the per-tick check. Pre-fix ISIS polled
                sequentially — 100 devices × ~2s each = 3+ min per
                pass, never catching up with the 10s interval.
                Matches BGP/OSPF's default of 5.
        """
        self.device_db = device_db
        self.max_workers = max_workers
        self.monitoring_active = False
        self.monitor_thread = None
        self.stop_event = Event()
        
    def start_monitoring(self, interval: int = 10, check_existing: bool = True):
        """
        Start ISIS monitoring.
        
        Args:
            interval: Monitoring interval in seconds
            check_existing: If True, check existing FRR containers on startup
        """
        if self.monitoring_active:
            logger.info("[ISIS MONITOR] Already monitoring")
            return
            
        self.monitoring_active = True
        self.stop_event.clear()
        self.monitor_thread = Thread(target=self._monitor_loop, args=(interval,))
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        logger.info(f"[ISIS MONITOR] Started monitoring with {interval}s interval")
        
        # Check existing containers on startup if requested
        if check_existing:
            logger.info("[ISIS MONITOR] Checking existing FRR containers on startup")
            try:
                self.check_existing_containers()
            except Exception as e:
                logger.warning(f"[ISIS MONITOR] Error checking existing containers on startup: {e}")
    
    def stop_monitoring(self):
        """Stop ISIS monitoring."""
        if not self.monitoring_active:
            return
            
        self.monitoring_active = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
            
        logger.info("[ISIS MONITOR] Stopped monitoring")
    
    def _monitor_loop(self, interval: int):
        """Main monitoring loop."""
        logger.info("[ISIS MONITOR] Monitoring loop started")
        
        while not self.stop_event.is_set():
            try:
                # Get all devices with ISIS configured
                devices = self.device_db.get_all_devices()
                isis_devices = []
                
                for device in devices:
                    # Check if device has ISIS in protocols list OR has ISIS config
                    protocols = device.get("protocols", [])
                    if isinstance(protocols, str):
                        import json
                        try:
                            protocols = json.loads(protocols)
                        except Exception:
                            protocols = []
                    
                    has_isis_protocol = isinstance(protocols, list) and any(
                        p.upper() in ["IS-IS", "ISIS", "ISIS"] for p in protocols
                    )
                    
                    # Check if device has ISIS configuration
                    isis_config = device.get("isis_config") or device.get("is_is_config")
                    has_isis_config = False
                    if isis_config and isis_config != '{}':
                        try:
                            # Try to parse as JSON if it's a string
                            if isinstance(isis_config, str):
                                import json
                                isis_config = json.loads(isis_config)
                            # If it's a non-empty dict, mark as having config
                            if isis_config and isinstance(isis_config, dict):
                                has_isis_config = True
                        except Exception:
                            # If parsing fails but there's a value, still mark as having config
                            if isis_config:
                                has_isis_config = True
                    
                    # Include device if it has ISIS protocol or ISIS config.
                    # v0.5.264 (audit ISIS-F6): filter on status=='Running'
                    # to mirror BGP/ARP. Pre-fix, stopped devices were
                    # polled every tick and produced Down synths that
                    # clobbered the DB + inflated the event log.
                    if (has_isis_protocol or has_isis_config) and device.get("status") == "Running":
                        isis_devices.append(device)

                if isis_devices:
                    logger.info(f"[ISIS MONITOR] Checking ISIS status for {len(isis_devices)} devices")

                    # v0.5.264 (audit ISIS-F4): parallel polling. Pre-fix
                    # was `for device in isis_devices: check(...)` — with
                    # 100 devices at ~2s per exec_run, one pass took 3+
                    # minutes, far past the 10s check_interval. Mirror
                    # BGP/OSPF's ThreadPoolExecutor pattern.
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        futures = {}
                        for device in isis_devices:
                            if self.stop_event.is_set():
                                break
                            device_id = device.get("device_id")
                            device_name = device.get("device_name") or device.get("Device Name", "Unknown")
                            if not device_id:
                                continue
                            futures[executor.submit(self._check_device_isis_status, device_id, device_name)] = device_id
                        for fut in as_completed(futures):
                            _did = futures[fut]
                            try:
                                fut.result()
                            except Exception as _fe:
                                logger.error(f"[ISIS MONITOR] Check failed for {_did}: {_fe}")
                
                logger.info(f"[ISIS MONITOR] Periodic ISIS status check completed for {len(isis_devices)} devices")
                
            except Exception as e:
                logger.error(f"[ISIS MONITOR] Error in monitoring loop: {e}")
            
            # Wait for next check
            self.stop_event.wait(interval)
        
        logger.info("[ISIS MONITOR] Monitoring loop stopped")
    
    def _check_device_isis_status(self, device_id: str, device_name: str):
        """Check ISIS status for a specific device."""
        try:
            # Check if there's a manual override in place
            device_data = self.device_db.get_device(device_id)
            if device_data:
                manual_override = device_data.get('isis_manual_override', False)
                manual_override_time = device_data.get('isis_manual_override_time')
                
                if manual_override and manual_override_time:
                    # Check if manual override is recent (within last 2 minutes)
                    try:
                        override_time = datetime.fromisoformat(manual_override_time.replace('Z', '+00:00'))
                        current_time = datetime.now(timezone.utc)
                        time_diff = (current_time - override_time).total_seconds()
                        
                        if time_diff < 120:  # 2 minutes
                            logger.info(f"[ISIS MONITOR] Skipping update for device {device_id} - manual override active ({time_diff:.1f}s ago)")
                            return
                        else:
                            logger.info(f"[ISIS MONITOR] Manual override expired for device {device_id} ({time_diff:.1f}s ago), proceeding with update")
                    except Exception as e:
                        logger.warning(f"[ISIS MONITOR] Error parsing manual override time: {e}")
            
            # Import ISIS utility functions and FRR manager
            from .isis import get_isis_status
            from .frr_docker import FRRDockerManager
            import docker.errors
            
            # Get container name using FRRDockerManager
            frr_manager = FRRDockerManager()
            container_name = frr_manager._get_container_name(device_id, device_name)
            
            # Check if container exists
            try:
                container = frr_manager.client.containers.get(container_name)
                if container.status != "running":
                    logger.warning(f"[ISIS MONITOR] Container {container_name} exists but not running, ISIS cannot be running")
                    # Clear ISIS status in database
                    isis_status = {
                        "isis_running": False,
                        "isis_established": False,
                        "isis_state": "Down",
                        "neighbors": [],
                        "areas": [],
                        "system_id": "",
                        "net": "",
                        "uptime": None
                    }
                    self._update_device_isis_status(device_id, isis_status)
                    return
            except docker.errors.NotFound:
                logger.warning(f"[ISIS MONITOR] Container {container_name} not found, ISIS cannot be running")
                # Clear ISIS status in database
                isis_status = {
                    "isis_running": False,
                    "isis_established": False,
                    "isis_state": "Down",
                    "neighbors": [],
                    "areas": [],
                    "system_id": "",
                    "net": "",
                    "uptime": None
                }
                self._update_device_isis_status(device_id, isis_status)
                return
            except Exception as e:
                logger.error(f"[ISIS MONITOR] Error checking container {container_name}: {e}")
                # Still try to get ISIS status, but it will likely fail and return error state
            
            # Get ISIS status from FRR
            isis_status = get_isis_status(device_id, device_name, container_name)
            
            # Update database
            self._update_device_isis_status(device_id, isis_status)
            
        except Exception as e:
            logger.error(f"[ISIS MONITOR] Error checking ISIS status for device {device_id}: {e}")
    
    def _update_device_isis_status(self, device_id: str, isis_status: Dict[str, Any]):
        """Update ISIS status in database for a device.

        v0.5.264 (audit ISIS-F2): per-device write lock serialises
        the 4-write sequence so concurrent writers (monitor pass +
        force_check + manual-override toggle) don't produce torn
        state.
        """
        _lock = _isis_write_lock_for(device_id)
        _lock.acquire()
        try:
            # Check if there's a manual override in place
            device_data = self.device_db.get_device(device_id)
            if device_data:
                manual_override = device_data.get('isis_manual_override', False)
                manual_override_time = device_data.get('isis_manual_override_time')
                
                if manual_override and manual_override_time:
                    # Check if manual override is recent (within last 2 minutes)
                    try:
                        override_time = datetime.fromisoformat(manual_override_time.replace('Z', '+00:00'))
                        current_time = datetime.now(timezone.utc)
                        time_diff = (current_time - override_time).total_seconds()
                        
                        if time_diff < 120:  # 2 minutes
                            logger.info(f"[ISIS MONITOR] Skipping update for device {device_id} - manual override active ({time_diff:.1f}s ago)")
                            return
                        else:
                            logger.info(f"[ISIS MONITOR] Manual override expired for device {device_id} ({time_diff:.1f}s ago), proceeding with update")
                    except Exception as e:
                        logger.warning(f"[ISIS MONITOR] Error parsing manual override time: {e}")
            
            logger.info(f"[ISIS MONITOR] Updating ISIS status for device {device_id}")
            logger.debug(f"[ISIS MONITOR] ISIS status data: {isis_status}")
            
            # Prepare ISIS status data
            isis_running = isis_status.get('isis_running', False)
            isis_established = isis_status.get('isis_established', False)
            isis_state = isis_status.get('isis_state', 'Unknown')
            neighbors = isis_status.get('neighbors', [])
            areas = isis_status.get('areas', [])
            system_id = isis_status.get('system_id', '')
            net = isis_status.get('net', '')
            uptime = isis_status.get('uptime', '')
            last_check = datetime.now(timezone.utc).isoformat()
            
            # Update device statistics table
            self.device_db.update_device_statistics(device_id, {
                'isis_running': isis_running,
                'isis_established': isis_established,
                'isis_state': isis_state,
                'isis_neighbors': json.dumps(neighbors) if neighbors else None,
                'isis_areas': json.dumps(areas) if areas else None,
                'isis_system_id': system_id,
                'isis_net': net,
                'isis_uptime': uptime,
                'last_isis_check': last_check
            })

            # Update main devices table with ISIS status
            update_data = {
                'isis_running': isis_running,
                'isis_established': isis_established,
                'isis_state': isis_state,
                'last_isis_check': last_check,
                'isis_neighbors': json.dumps(neighbors) if neighbors else None,
                'isis_areas': json.dumps(areas) if areas else None,
                'isis_system_id': system_id,
                'isis_net': net,
                'isis_uptime': uptime
            }
            
            # Clear manual override flag when monitor takes over
            if device_data and device_data.get('isis_manual_override', False):
                update_data['isis_manual_override'] = False
                update_data['isis_manual_override_time'] = None
                logger.info(f"[ISIS MONITOR] Cleared manual override flag for device {device_id}")
            
            logger.info(f"[ISIS MONITOR] Update data: {update_data}")
            
            result = self.device_db.update_device(device_id, update_data)
            logger.info(f"[ISIS MONITOR] Database update result: {result}")
            
            # Log ISIS status event.
            # v0.5.264 (audit ISIS-F3): dedup against the last-logged
            # isis_state per device.
            _cur_state = isis_state or "Unknown"
            with _LAST_ISIS_STATE_LOGGED_LOCK:
                _prev_state = _LAST_ISIS_STATE_LOGGED.get(device_id)
                _should_log = _prev_state != _cur_state
                if _should_log:
                    _LAST_ISIS_STATE_LOGGED[device_id] = _cur_state
            if _should_log:
                self.device_db.log_device_event(device_id, "isis_status_check", {
                    'isis_running': isis_running,
                    'isis_established': isis_established,
                    'isis_state': isis_state,
                    'total_neighbors': len(neighbors),
                    'neighbors': neighbors,
                    'areas': areas,
                    'transition_from': _prev_state,
                })

            # Per-protocol state-history timeline (de-dup'd against last row).
            try:
                self.device_db.add_state_transition(
                    device_id,
                    "isis",
                    isis_state or "Unknown",
                    detail={
                        "running": isis_running,
                        "established": isis_established,
                        "neighbors": len(neighbors),
                        "areas": len(areas) if areas else 0,
                    },
                )
            except Exception as _e:
                logger.debug(f"[ISIS MONITOR] state-history insert skipped: {_e}")

            logger.info(f"[ISIS MONITOR] Successfully updated ISIS status for device {device_id}: {isis_state}")
            
        except Exception as e:
            logger.error(f"[ISIS MONITOR] Error updating ISIS status for device {device_id}: {e}")
            import traceback
            logger.error(f"[ISIS MONITOR] Traceback: {traceback.format_exc()}")
        finally:
            _lock.release()

    # Compatibility with the other monitors (ARP / BGP / OSPF / DHCP)
    # which expose `is_running`. Lets /api/monitors/health treat all
    # five uniformly instead of special-casing IS-IS.
    @property
    def is_running(self) -> bool:
        return bool(self.monitoring_active)

    def get_status(self) -> "Dict[str, Any]":
        """Return a status dict matching the other monitors' shape."""
        return {
            "is_running": self.is_running,
            "monitoring_active": self.monitoring_active,
            "thread_alive": bool(self.monitor_thread and self.monitor_thread.is_alive()),
        }

    def force_check(self):
        """Force an immediate ISIS status check for all devices with ISIS configured."""
        if not self.monitoring_active:
            logger.warning("[ISIS MONITOR] Monitor is not active, cannot force check")
            return
        
        try:
            # Get all devices with ISIS configured
            devices = self.device_db.get_all_devices()
            isis_devices = []
            
            for device in devices:
                # Check if device has ISIS in protocols list OR has ISIS config
                protocols = device.get("protocols", [])
                if isinstance(protocols, str):
                    try:
                        protocols = json.loads(protocols)
                    except Exception:
                        protocols = []
                
                has_isis_protocol = isinstance(protocols, list) and any(
                    p.upper() in ["IS-IS", "ISIS", "ISIS"] for p in protocols
                )
                
                # Check if device has ISIS configuration
                isis_config = device.get("isis_config") or device.get("is_is_config")
                has_isis_config = False
                if isis_config and isis_config != '{}':
                    try:
                        if isinstance(isis_config, str):
                            isis_config = json.loads(isis_config)
                        if isis_config and isinstance(isis_config, dict):
                            has_isis_config = True
                    except Exception:
                        if isis_config:
                            has_isis_config = True
                
                # v0.5.264 (audit ISIS-F6): filter on status=='Running'.
                if (has_isis_protocol or has_isis_config) and device.get("status") == "Running":
                    isis_devices.append(device)

            if isis_devices:
                logger.info(f"[ISIS MONITOR] Force checking ISIS status for {len(isis_devices)} devices")
                # v0.5.264 (audit ISIS-F4): parallel force-check so
                # /api/isis/monitor/force-check doesn't wedge the Flask
                # thread for ~2s × N devices.
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {}
                    for device in isis_devices:
                        device_id = device.get("device_id")
                        device_name = device.get("device_name") or device.get("Device Name", "Unknown")
                        if not device_id:
                            continue
                        futures[executor.submit(self._check_device_isis_status, device_id, device_name)] = device_id
                    for fut in as_completed(futures):
                        _did = futures[fut]
                        try:
                            fut.result()
                        except Exception as _fe:
                            logger.error(f"[ISIS MONITOR] Force-check failed for {_did}: {_fe}")
                logger.info(f"[ISIS MONITOR] Force check completed for {len(isis_devices)} devices")
            else:
                logger.info("[ISIS MONITOR] No ISIS devices found for force check")
        except Exception as e:
            logger.error(f"[ISIS MONITOR] Error in force check: {e}")
            import traceback
            logger.error(f"[ISIS MONITOR] Traceback: {traceback.format_exc()}")
    
    def check_existing_containers(self):
        """Check all existing FRR containers and sync ISIS status with database."""
        try:
            from .frr_docker import FRRDockerManager
            import docker.errors
            
            logger.info("[ISIS MONITOR] Checking existing FRR containers")
            
            frr_manager = FRRDockerManager()
            all_containers = frr_manager.client.containers.list(all=True)
            
            # Filter for OSTG FRR containers
            frr_containers = [c for c in all_containers if c.name.startswith('ostg-frr-')]
            
            if not frr_containers:
                logger.info("[ISIS MONITOR] No existing FRR containers found")
                return
            
            logger.info(f"[ISIS MONITOR] Found {len(frr_containers)} existing FRR containers")
            
            # Get all devices from database
            devices = self.device_db.get_all_devices()
            device_id_map = {d.get("device_id"): d for d in devices if d.get("device_id")}
            
            containers_checked = 0
            containers_with_isis = 0
            
            for container in frr_containers:
                try:
                    container_name = container.name
                    
                    # Extract device_id from container name (format: ostg-frr-{device_id})
                    # The container name format is: ostg-frr-{device_id}
                    if container_name.startswith('ostg-frr-'):
                        container_id_part = container_name.replace('ostg-frr-', '')
                        
                        # Try to match with device_id from database
                        # The container name should match exactly or start with device_id
                        matched_device = None
                        matched_device_id = None
                        
                        # v0.5.264 (audit ISIS-F9): exact match ONLY.
                        # Pre-fix the `startswith(device_id)` fallback
                        # cross-matched devices whose IDs share a
                        # prefix (e.g. `abc123` and `abc1234`) — the
                        # loop's dict-iteration-order determined the
                        # winner, so a container `ostg-frr-abc1234`
                        # got bound to the shorter `abc123` device
                        # and that wrong device's ISIS status got
                        # synced from a container it doesn't own.
                        # Container names are deterministic
                        # (`ostg-frr-{device_id}` from
                        # `frr_docker._get_container_name`), so the
                        # exact-match branch is sufficient.
                        if container_id_part in device_id_map:
                            matched_device = device_id_map[container_id_part]
                            matched_device_id = container_id_part
                        
                        if not matched_device or not matched_device_id:
                            logger.debug(f"[ISIS MONITOR] Container {container_name} (extracted ID: {container_id_part}) does not match any device in database")
                            continue
                        
                        device_id = matched_device_id
                        device_name = matched_device.get("device_name") or matched_device.get("Device Name", "Unknown")
                        
                        # Check if this device has ISIS configured
                        protocols = matched_device.get("protocols", [])
                        if isinstance(protocols, str):
                            try:
                                protocols = json.loads(protocols)
                            except Exception:
                                protocols = []
                        
                        has_isis_protocol = isinstance(protocols, list) and any(
                            p.upper() in ["IS-IS", "ISIS", "ISIS"] for p in protocols
                        )
                        
                        isis_config = matched_device.get("isis_config") or matched_device.get("is_is_config")
                        has_isis_config = False
                        if isis_config and isis_config != '{}':
                            try:
                                if isinstance(isis_config, str):
                                    isis_config = json.loads(isis_config)
                                if isis_config and isinstance(isis_config, dict):
                                    has_isis_config = True
                            except Exception:
                                if isis_config:
                                    has_isis_config = True
                        
                        if has_isis_protocol or has_isis_config:
                            containers_with_isis += 1
                            logger.info(f"[ISIS MONITOR] Checking existing container {container_name} for device {device_name} ({device_id})")
                            
                            # Check container status
                            if container.status == "running":
                                # Check ISIS status
                                self._check_device_isis_status(device_id, device_name)
                            else:
                                logger.info(f"[ISIS MONITOR] Container {container_name} exists but not running - clearing ISIS status")
                                # Clear ISIS status in database
                                isis_status = {
                                    "isis_running": False,
                                    "isis_established": False,
                                    "isis_state": "Down",
                                    "neighbors": [],
                                    "areas": [],
                                    "system_id": "",
                                    "net": "",
                                    "uptime": None
                                }
                                self._update_device_isis_status(device_id, isis_status)
                            
                            containers_checked += 1
                        else:
                            logger.debug(f"[ISIS MONITOR] Container {container_name} for device {device_name} does not have ISIS configured")
                    
                except Exception as e:
                    logger.error(f"[ISIS MONITOR] Error checking container {container.name}: {e}")
                    import traceback
                    logger.error(f"[ISIS MONITOR] Traceback: {traceback.format_exc()}")
            
            logger.info(f"[ISIS MONITOR] Completed check of existing containers: {containers_checked} containers with ISIS checked, {containers_with_isis} had ISIS configured")
            
        except Exception as e:
            logger.error(f"[ISIS MONITOR] Error checking existing containers: {e}")
            import traceback
            logger.error(f"[ISIS MONITOR] Traceback: {traceback.format_exc()}")

