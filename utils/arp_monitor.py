"""
ARP Status Monitoring System for OSTG
Multi-threaded ARP status monitoring with database updates
"""

import threading
import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# Configure logging
logger = logging.getLogger(__name__)

# v0.5.262 (audit ARP-4 + ARP-7): serialize DB writes per device_id
# so the 4-write update sequence in `_update_device_arp_status`
# can't interleave with a concurrent on-demand refresh writer for
# the same device. Prior to the lock two writers could produce
# torn state where `devices.arp_ipv4_resolved` disagreed with
# `device_statistics.arp_ipv4_resolved`.
_ARP_WRITE_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
_ARP_WRITE_LOCKS_META_LOCK = threading.Lock()


def _arp_write_lock_for(device_id: str) -> threading.Lock:
    """Return the process-wide lock for a given device_id. The
    per-key `defaultdict` is not thread-safe against concurrent
    key creation, so we guard the "create-or-return" step with a
    tiny meta-lock. The actual lock is held OUTSIDE the meta-lock,
    so contention is per-device."""
    with _ARP_WRITE_LOCKS_META_LOCK:
        return _ARP_WRITE_LOCKS[device_id]


# v0.5.262 (audit ARP-10): dedup `log_device_event` against the
# last logged arp_status per device_id. Pre-fix every 30s poll
# wrote one row per device — 288k rows/day at 100 devices; the
# `device_events` table dominated DB size within weeks. The
# state-history table already dedup's via `add_state_transition`;
# do the same here so events are emitted only on transition.
_LAST_ARP_STATUS_LOGGED: Dict[str, str] = {}
_LAST_ARP_STATUS_LOGGED_LOCK = threading.Lock()

def _default_self_url():
    """Default self-loopback URL the monitors call back into.

    Reads NETGEN_SERVER_PORT (or legacy OSTG_SERVER_PORT) env var,
    falling back to 5050 — the actual port netgen-server binds to.
    The previous hardcoded `localhost:5051` was a stale carryover from
    an older port assignment and caused every monitor poll to log
    "[ARP/BGP/OSPF MONITOR] ... Connection refused" until run_tgen_server
    was patched to pass an explicit server_url. Belt-and-braces: also
    fix the default here so a caller that forgets the kwarg still
    gets the right port.
    """
    import os as _os
    port = (_os.environ.get("NETGEN_SERVER_PORT")
            or _os.environ.get("OSTG_SERVER_PORT")
            or "5050")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 5050
    return f"http://localhost:{port}"


class ARPStatusMonitor:
    """Multi-threaded ARP status monitoring system"""

    def __init__(self, device_db, server_url: str = None,
                 check_interval: int = 30, max_workers: int = 5):
        if not server_url:
            server_url = _default_self_url()
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

        # v0.5.281 (ARP-GUARD-1): startup self-check. Report the
        # ARP-plane invariants that the gateway-orange saga (v0.5.254
        # → v0.5.280) taught us to watch. Anything reported as WRONG
        # here is a signal that the endpoint will produce a false-
        # orange (or false-green) result on the next poll.
        try:
            self._log_arp_plane_self_check()
        except Exception as _exc:
            logger.warning(f"[ARP MONITOR] self-check raised: {_exc}")

        # v0.5.282 (ARP-J3): sweep sysctls that block secondary-IP
        # ARP replies. v0.5.280 (DHCP-K1) sets these per-anchor
        # inside `_ensure_ipv4_address` — but that path only runs on
        # DHCP-server (re)start. If an operator upgrades to v0.5.280
        # or v0.5.282 without restarting the DHCP-server device, the
        # sysctl fix never fires and switches still can't ping the
        # anchor. Sweep at monitor start so EXISTING deployments
        # benefit immediately: set `net.ipv4.conf.all.arp_ignore=0`
        # and `net.ipv4.conf.all.arp_announce=0` — those propagate
        # to per-interface via the kernel's OR-with-`all` semantics.
        # Best-effort; log at debug when sysctl isn't available
        # (rootless container, sysctl locked). Non-fatal.
        try:
            self._sweep_arp_sysctls()
        except Exception as _exc:
            logger.warning(f"[ARP MONITOR] sysctl sweep raised: {_exc}")

        self.is_running = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[ARP MONITOR] Started ARP status monitoring")

    def _log_arp_plane_self_check(self) -> None:
        """v0.5.281 (ARP-GUARD-1): dump the four invariants the
        gateway-orange class taught us to enforce. On any
        failure, log a WARNING that names the specific ship
        the regression would look like — so a future operator
        searching the log for `arp_ignore` or `anchor-arp`
        finds this before opening yet another ticket."""
        import subprocess as _subprocess
        # Invariant 1: arp_ignore should be 0 on the "all" scope so
        # secondary-IP replies work. v0.5.280 (DHCP-K1) set this on
        # anchor interfaces explicitly; the "all" default backs it up.
        try:
            _res = _subprocess.run(
                ["sysctl", "-n", "net.ipv4.conf.all.arp_ignore"],
                capture_output=True, text=True, timeout=3,
            )
            _val = (_res.stdout or "").strip()
            if _val and _val != "0":
                logger.warning(
                    f"[ARP MONITOR] SELF-CHECK: "
                    f"net.ipv4.conf.all.arp_ignore={_val} "
                    f"(expected 0). Secondary-IP ARP replies may be "
                    f"dropped — the v0.5.280 class of 'switch can't "
                    f"ping netgen anchor' regression."
                )
        except Exception:
            pass
        # Invariant 2: the anchor re-arp thread is alive when any
        # anchors are registered. Without this the switch's MAC
        # table ages out and the anchor becomes unreachable.
        try:
            from utils import dhcp as _dhcp
            _anchors = list(_dhcp._ANCHOR_ARP_REFRESH.values())
            _thread_alive = (
                _dhcp._ANCHOR_ARP_THREAD is not None
                and _dhcp._ANCHOR_ARP_THREAD.is_alive()
            )
            if _anchors and not _thread_alive:
                logger.warning(
                    f"[ARP MONITOR] SELF-CHECK: "
                    f"{len(_anchors)} DHCP anchors registered but "
                    f"the re-arp refresh thread is not alive — v0.5.280 "
                    f"(DHCP-K2) regression. Switch MAC tables will age "
                    f"out. Restart the DHCP-server device to re-register."
                )
            elif _anchors:
                logger.info(
                    f"[ARP MONITOR] SELF-CHECK: {len(_anchors)} "
                    f"DHCP anchor(s) tracked by re-arp thread."
                )
        except Exception:
            pass
        # Invariant 3: the frr_manager lazy proxy is imported so
        # VRF detection doesn't cold-init Docker on the hot path.
        # v0.5.277 (ARP-H2) — the 2s-Docker-connect race that
        # produced silent-orange.
        try:
            from utils.frr_docker import frr_manager as _fm  # noqa: F401
        except Exception as _fm_exc:
            logger.warning(
                f"[ARP MONITOR] SELF-CHECK: cannot import "
                f"frr_manager lazy proxy ({_fm_exc}); ARP-status "
                f"endpoint will fall back to default netns for "
                f"VRF-scoped devices — v0.5.277 (ARP-H2) regression."
            )
    
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

    def _sweep_arp_sysctls(self) -> None:
        """v0.5.282 (ARP-J3): set the sysctls that block secondary-
        IP ARP replies. Applied at ARP-monitor start so existing
        deployments benefit without needing to restart any DHCP
        device (the DHCP anchor add path already sets these per-
        interface via v0.5.280 DHCP-K1, but that only runs on
        (re)start).

        The Linux kernel semantics for `net.ipv4.conf.<iface>.<key>`
        is `max(all, <iface>)` for arp_ignore and `max(all, <iface>)`
        for arp_announce — so setting `all=0` doesn't force per-
        interface to 0 if the per-interface value is >0. Set BOTH
        `all` and `default` (default = template for new interfaces)
        to establish a safe baseline. The v0.5.280 per-anchor
        override still runs and force-sets specific interfaces to 0.
        """
        import subprocess as _subprocess
        _pairs = [
            ("net.ipv4.conf.all.arp_ignore", "0"),
            ("net.ipv4.conf.all.arp_announce", "0"),
            ("net.ipv4.conf.default.arp_ignore", "0"),
            ("net.ipv4.conf.default.arp_announce", "0"),
        ]
        for _key, _val in _pairs:
            try:
                _res = _subprocess.run(
                    ["sysctl", "-w", f"{_key}={_val}"],
                    capture_output=True, text=True, timeout=3,
                )
                if _res.returncode == 0:
                    logger.info(f"[ARP MONITOR] sysctl {_key}={_val}")
                else:
                    logger.debug(
                        f"[ARP MONITOR] sysctl {_key}={_val} "
                        f"returned {_res.returncode}: {_res.stderr.strip()}"
                    )
            except Exception as _exc:
                logger.debug(
                    f"[ARP MONITOR] sysctl {_key}={_val} skipped: {_exc}"
                )

    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("[ARP MONITOR] Monitoring loop started")

        # Startup settle: the Flask server in the same process binds on
        # port 5050 a moment after the monitor thread is launched. If we
        # poll immediately we hit `Connection refused` for the first
        # iteration and log it as ERROR even though it's a transient
        # race. Wait a short window to give Flask time to come up.
        if self.stop_event.wait(5):
            return
        self._startup_window_until = time.monotonic() + 25  # tolerate refused for 25s more

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
            # During the first few seconds after netgen-server starts,
            # Flask hasn't bound :5050 yet → ConnectionRefused. Don't
            # spam the log with ERROR for that — downgrade to debug
            # while still inside the startup window.
            is_startup_blip = (
                isinstance(e, requests.exceptions.ConnectionError)
                and time.monotonic() < getattr(self, "_startup_window_until", 0)
            )
            if is_startup_blip:
                logger.debug(f"[ARP MONITOR] startup-window connection-refused for {device_id}; ignoring")
            else:
                logger.error(f"[ARP MONITOR] Network error checking ARP status for device {device_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error checking ARP status for device {device_id}: {e}")
            return None
    
    def _update_device_arp_status(self, device_id: str, arp_status: Dict[str, Any]):
        """Update ARP status in the database.

        v0.5.262 (audit ARP-4 + ARP-7): the 4 writes below (device
        statistics, devices row, event log, state transition) each
        hold their own connection + transaction. Interleaved writers
        (monitor + on-demand refresh for the same device) can leave
        torn state where the two tables disagree. Serialize per
        device_id so a single writer completes its 4 steps before
        another starts.
        """
        _lock = _arp_write_lock_for(device_id)
        with _lock:
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

                # v0.5.262 (audit ARP-10): dedup log_device_event —
                # only write when arp_status actually changed since
                # the last logged event for this device_id. Pre-fix
                # this fired every 30 s per device regardless of
                # state, dominating device_events table growth.
                current_status = arp_status.get('arp_status') or "Unknown"
                with _LAST_ARP_STATUS_LOGGED_LOCK:
                    _prev_logged = _LAST_ARP_STATUS_LOGGED.get(device_id)
                    _should_log = _prev_logged != current_status
                    if _should_log:
                        _LAST_ARP_STATUS_LOGGED[device_id] = current_status
                if _should_log:
                    self.device_db.log_device_event(device_id, "arp_status_check", {
                        'arp_resolved': arp_status['arp_resolved'],
                        'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                        'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                        'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                        'arp_status': arp_status['arp_status'],
                        'details': arp_status['details'],
                        'transition_from': _prev_logged,
                    })

                # Per-protocol state-history timeline (de-dup'd against last row).
                try:
                    self.device_db.add_state_transition(
                        device_id,
                        "arp",
                        arp_status.get('arp_status') or "Unknown",
                        detail={
                            "ipv4": arp_status.get('arp_ipv4_resolved'),
                            "ipv6": arp_status.get('arp_ipv6_resolved'),
                            "gateway": arp_status.get('arp_gateway_resolved'),
                        },
                    )
                except Exception as _e:
                    logger.debug(f"[ARP MONITOR] state-history insert skipped: {_e}")

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
