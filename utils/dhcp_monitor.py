"""Periodic DHCP client status monitoring."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from . import dhcp as dhcp_utils
from .dhcp import ensure_dhcp_services

logger = logging.getLogger(__name__)


class DHCPClientMonitor:
    """Background monitor that periodically refreshes DHCP client state."""

    def __init__(self, device_db, check_interval: int = 60):
        self.device_db = device_db
        self.check_interval = max(10, int(check_interval))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.is_running = False
        logger.info(
            "[DHCP MONITOR] Initialized DHCP client monitor (interval=%ss)", self.check_interval
        )

    def start(self) -> None:
        if self.is_running:
            logger.warning("[DHCP MONITOR] Monitor already running")
            return

        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="DHCPClientMonitor", daemon=True)
        self._thread.start()
        logger.info("[DHCP MONITOR] Started DHCP client monitoring loop")

    def stop(self) -> None:
        if not self.is_running:
            logger.warning("[DHCP MONITOR] Monitor is not running")
            return

        self.is_running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[DHCP MONITOR] Stopped DHCP client monitoring loop")

    def force_check(self) -> None:
        logger.info("[DHCP MONITOR] Manually triggering DHCP client status check")
        self._check_clients()

    def update_check_interval(self, interval: int) -> None:
        self.check_interval = max(10, int(interval))
        logger.info("[DHCP MONITOR] Updated check interval to %ss", self.check_interval)

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "check_interval": self.check_interval,
            "next_check_in": self.check_interval if self.is_running else None,
        }

    def _loop(self) -> None:
        logger.debug("[DHCP MONITOR] Loop started")
        # Run an initial check immediately
        self._check_clients()

        while not self._stop_event.wait(self.check_interval):
            self._check_clients()

        logger.debug("[DHCP MONITOR] Loop exiting")

    def _get_client_devices(self) -> List[Dict[str, Any]]:
        try:
            devices = self.device_db.get_all_devices()
            result: List[Dict[str, Any]] = []
            for device in devices:
                dhcp_mode = (device.get("dhcp_mode") or "").lower()
                if dhcp_mode == "client":
                    result.append(device)
            return result
        except Exception as exc:
            logger.error("[DHCP MONITOR] Failed to fetch devices: %s", exc)
            return []

    def _check_clients(self) -> None:
        devices = self._get_client_devices()
        if not devices:
            logger.debug("[DHCP MONITOR] No DHCP client devices found")
            return

        logger.info("[DHCP MONITOR] Checking %d DHCP client(s)", len(devices))

        for device in devices:
            device_id = device.get("device_id")
            if not device_id:
                continue

            dhcp_config = device.get("dhcp_config") or {}
            if isinstance(dhcp_config, str):
                try:
                    dhcp_config = json.loads(dhcp_config)
                except Exception as exc:
                    logger.debug(
                        "[DHCP MONITOR] Failed to decode dhcp_config for %s: %s", device_id, exc
                    )
                    dhcp_config = {}

            interface = (
                dhcp_config.get("interface")
                or device.get("server_interface")
                or device.get("interface")
            )

            if not interface:
                logger.debug("[DHCP MONITOR] No interface found for device %s", device_id)
                continue

            try:
                snapshot = dhcp_utils.get_dhcp_client_snapshot(
                    self.device_db, device_id, interface, dhcp_config
                )
                if snapshot:
                    self.device_db.update_device(device_id, snapshot)
                    logger.debug(
                        "[DHCP MONITOR] Updated DHCP snapshot for %s: state=%s, ip=%s",
                        device_id,
                        snapshot.get("dhcp_state"),
                        snapshot.get("dhcp_lease_ip"),
                    )

                    needs_restart = (
                        snapshot.get("dhcp_state") != "Leased"
                        or not snapshot.get("dhcp_running")
                    )
                    if needs_restart:
                        logger.info(
                            "[DHCP MONITOR] Restarting dhclient for %s (state=%s, running=%s)",
                            device_id,
                            snapshot.get("dhcp_state"),
                            snapshot.get("dhcp_running"),
                        )
                        try:
                            ensure_result = ensure_dhcp_services(
                                self.device_db,
                                device_id,
                                interface,
                                dhcp_config,
                                force_client_restart=True,
                            )
                            if ensure_result.get("success"):
                                refreshed = dhcp_utils.get_dhcp_client_snapshot(
                                    self.device_db, device_id, interface, dhcp_config
                                )
                                if refreshed:
                                    self.device_db.update_device(device_id, refreshed)
                                    logger.debug(
                                        "[DHCP MONITOR] Post-restart snapshot for %s: state=%s, ip=%s",
                                        device_id,
                                        refreshed.get("dhcp_state"),
                                        refreshed.get("dhcp_lease_ip"),
                                    )
                        except Exception as restart_exc:
                            logger.error(
                                "[DHCP MONITOR] Failed to restart dhclient for %s: %s",
                                device_id,
                                restart_exc,
                            )
            except Exception as exc:
                logger.error(
                    "[DHCP MONITOR] Failed to update DHCP state for device %s: %s",
                    device_id,
                    exc,
                )

