"""Periodic DHCP status monitoring (client + server modes).

v0.5.217 audit bundle:
- Bug E: monitor now polls BOTH client and server modes. Pre-fix
  `_get_client_devices` filtered dhcp_mode=="client" only, so a
  dnsmasq crash on a server-mode device stayed invisible until
  the next full server restart.
- Bug G: honours `dhcp_manual_override` written by
  `stop_dhcp_services`. If set within the last 120 s, skip the
  device entirely so a stop-from-UI doesn't get resurrected on
  the next tick. Older overrides are cleared and the check
  proceeds. When the monitor writes an update after taking over,
  the override flag is also cleared.
- Bug H: per-device restart-attempt backoff. After 3 restarts
  within 5 minutes, back off exponentially (2^(attempts-3))
  capped at 30 minutes. Successful "Leased" observation resets
  the counter.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import dhcp as dhcp_utils
from .dhcp import ensure_dhcp_services

logger = logging.getLogger(__name__)


# v0.5.217 (audit fix G): how long a manual-override blocks the
# monitor before it takes over again. Matches the BGP monitor's
# window (see utils/bgp_monitor.py:273).
_MANUAL_OVERRIDE_WINDOW_SECONDS = 120

# v0.5.217 (audit fix H): restart-storm backoff parameters.
# After `_BACKOFF_THRESHOLD` restart attempts within
# `_BACKOFF_WINDOW_SECONDS`, back off exponentially, capped at
# `_BACKOFF_MAX_SECONDS`.
_BACKOFF_THRESHOLD = 3
_BACKOFF_WINDOW_SECONDS = 300  # 5 minutes
_BACKOFF_MAX_SECONDS = 1800    # 30 minutes


class DHCPClientMonitor:
    """Background monitor that periodically refreshes DHCP state
    for both client-mode AND server-mode devices."""

    def __init__(self, device_db, check_interval: int = 60):
        self.device_db = device_db
        self.check_interval = max(10, int(check_interval))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.is_running = False
        # v0.5.217 (audit fix H): per-device restart attempt tracker.
        # Shape: {device_id: {"count": int, "last_attempt": epoch}}
        self._dhcp_restart_attempts: Dict[str, Dict[str, float]] = {}
        logger.info(
            "[DHCP MONITOR] Initialized DHCP monitor (interval=%ss)", self.check_interval
        )

    def start(self) -> None:
        if self.is_running:
            logger.warning("[DHCP MONITOR] Monitor already running")
            return

        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="DHCPClientMonitor", daemon=True)
        self._thread.start()
        logger.info("[DHCP MONITOR] Started DHCP monitoring loop")

    def stop(self) -> None:
        if not self.is_running:
            logger.warning("[DHCP MONITOR] Monitor is not running")
            return

        self.is_running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[DHCP MONITOR] Stopped DHCP monitoring loop")

    def force_check(self) -> None:
        logger.info("[DHCP MONITOR] Manually triggering DHCP status check")
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

    # v0.5.217 (audit fix E): rename + widen to include server-mode
    # devices. Old name preserved as a thin alias so any external
    # caller (there weren't any in-tree at the audit time, but be
    # defensive) doesn't break.
    def _get_dhcp_devices(self) -> List[Dict[str, Any]]:
        try:
            devices = self.device_db.get_all_devices()
            result: List[Dict[str, Any]] = []
            for device in devices:
                # Prefer dhcp_config["mode"] since dhcp_mode column
                # is sometimes blank (see audit fix C for the same
                # blank-column footgun on the remove path).
                dhcp_cfg = device.get("dhcp_config") or {}
                if isinstance(dhcp_cfg, str):
                    try:
                        dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
                    except Exception:
                        dhcp_cfg = {}
                mode_raw = (
                    (dhcp_cfg.get("mode") if isinstance(dhcp_cfg, dict) else None)
                    or device.get("dhcp_mode")
                    or ""
                )
                dhcp_mode = str(mode_raw).lower()
                if dhcp_mode in ("client", "server"):
                    result.append(device)
            return result
        except Exception as exc:
            logger.error("[DHCP MONITOR] Failed to fetch devices: %s", exc)
            return []

    # Back-compat alias (v0.5.217): _get_client_devices used to
    # return only client-mode devices. Some external tooling may
    # call it by name; keep the shim so we don't silently break it.
    def _get_client_devices(self) -> List[Dict[str, Any]]:
        return [
            d for d in self._get_dhcp_devices()
            if ((d.get("dhcp_config") or {}).get("mode") if isinstance(d.get("dhcp_config"), dict) else None
                or d.get("dhcp_mode") or "").lower() == "client"
        ]

    def _manual_override_active(self, device: Dict[str, Any]) -> bool:
        """v0.5.217 (audit fix G): honour dhcp_manual_override.

        Returns True if the device has a manual override younger
        than _MANUAL_OVERRIDE_WINDOW_SECONDS. If the timestamp is
        older, clear the override in place (so the monitor takes
        over on this same tick) and return False.
        """
        device_id = device.get("device_id")
        if not device_id:
            return False
        if not device.get("dhcp_manual_override"):
            return False
        override_time = device.get("dhcp_manual_override_time")
        if not override_time:
            # Flag set without a timestamp — treat as fresh so the
            # operator's stop can never accidentally get overridden.
            return True
        try:
            ts = datetime.fromisoformat(str(override_time).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception as exc:
            logger.warning(
                "[DHCP MONITOR] Failed to parse dhcp_manual_override_time for %s: %s",
                device_id, exc,
            )
            return True
        if age < _MANUAL_OVERRIDE_WINDOW_SECONDS:
            logger.info(
                "[DHCP MONITOR] Skipping device %s — manual override active (%.1fs ago)",
                device_id, age,
            )
            return True
        # Expired — clear it and let the check proceed.
        logger.info(
            "[DHCP MONITOR] Manual override expired for %s (%.1fs ago); taking over",
            device_id, age,
        )
        try:
            self.device_db.update_device(device_id, {
                "dhcp_manual_override": False,
                "dhcp_manual_override_time": None,
            })
        except Exception as exc:
            logger.warning(
                "[DHCP MONITOR] Failed to clear dhcp_manual_override for %s: %s",
                device_id, exc,
            )
        return False

    def _backoff_gate(self, device_id: str) -> bool:
        """v0.5.217 (audit fix H): return True if a restart is
        currently gated by exponential backoff.

        On churn (many restarts in a short window) the delay
        doubles per attempt over the threshold and is capped at
        _BACKOFF_MAX_SECONDS. The counter is reset when
        `_note_leased` records a successful lease.
        """
        state = self._dhcp_restart_attempts.get(device_id)
        if not state:
            return False
        attempts = int(state.get("count") or 0)
        if attempts < _BACKOFF_THRESHOLD:
            return False
        last = float(state.get("last_attempt") or 0.0)
        now = time.time()
        excess = attempts - _BACKOFF_THRESHOLD + 1
        # 2^excess * check_interval, capped.
        delay = min(self.check_interval * (2 ** excess), _BACKOFF_MAX_SECONDS)
        wait_remaining = (last + delay) - now
        if wait_remaining > 0:
            logger.info(
                "[DHCP MONITOR] Backoff gate for %s: attempts=%d, waiting %.0fs more (delay=%.0fs)",
                device_id, attempts, wait_remaining, delay,
            )
            return True
        return False

    def _note_restart_attempt(self, device_id: str) -> None:
        now = time.time()
        state = self._dhcp_restart_attempts.get(device_id)
        if state and (now - float(state.get("last_attempt") or 0.0)) > _BACKOFF_WINDOW_SECONDS:
            # Window expired — reset before recording this attempt.
            state = None
        if not state:
            state = {"count": 0, "last_attempt": 0.0}
        state["count"] = int(state.get("count") or 0) + 1
        state["last_attempt"] = now
        self._dhcp_restart_attempts[device_id] = state
        logger.info(
            "[DHCP MONITOR] Recorded restart attempt for %s (count=%d)",
            device_id, state["count"],
        )

    def _note_leased(self, device_id: str) -> None:
        if device_id in self._dhcp_restart_attempts:
            logger.info(
                "[DHCP MONITOR] Clearing restart-attempt counter for %s (lease observed)",
                device_id,
            )
            self._dhcp_restart_attempts.pop(device_id, None)

    def _check_server_device(self, device: Dict[str, Any], interface: str, dhcp_config: Dict[str, Any]) -> None:
        """v0.5.217 (audit fix E): server-mode health check.

        Verify dnsmasq is running (pidfile + pgrep) inside the DHCP
        server container. Writes dhcp_state="Server Running" /
        "Server Down" based on the observation.
        """
        device_id = device.get("device_id")
        if not device_id:
            return
        try:
            container = dhcp_utils._get_dhcp_container(device_id, mode="server")
        except Exception as exc:
            logger.debug(
                "[DHCP MONITOR] Could not resolve DHCP server container for %s: %s",
                device_id, exc,
            )
            container = None

        # If the container itself is gone, that's Server Down.
        running = False
        detail = "container missing"
        if container is not None:
            conffile = f"/etc/dnsmasq.d/ostg-{interface}.conf"
            pidfile = f"/var/run/dnsmasq/dnsmasq-{interface}.pid"
            try:
                pgrep = dhcp_utils._run_command(
                    ["/bin/sh", "-c",
                     f"pgrep -f 'dnsmasq.*{conffile}' || pgrep -f 'dnsmasq.*{interface}' || true"],
                    container=container,
                    timeout=5,
                )
                if pgrep.stdout.strip():
                    running = True
                    detail = "pgrep matched"
                else:
                    # Try the pid file next.
                    pidout = dhcp_utils._run_command(
                        ["/bin/sh", "-c",
                         f"if [ -f {pidfile} ]; then cat {pidfile}; fi"],
                        container=container,
                        timeout=5,
                    )
                    pid_val = pidout.stdout.strip()
                    if pid_val:
                        # Verify the PID is alive.
                        check = dhcp_utils._run_command(
                            ["/bin/sh", "-c", f"kill -0 {pid_val} 2>/dev/null && echo alive || echo dead"],
                            container=container,
                            timeout=5,
                        )
                        if "alive" in check.stdout:
                            running = True
                            detail = f"pidfile alive (pid={pid_val})"
                        else:
                            detail = f"pidfile stale (pid={pid_val})"
                    else:
                        detail = "no pidfile"
            except Exception as exc:
                detail = f"probe error: {exc}"

        new_state = "Server Running" if running else "Server Down"
        try:
            self.device_db.update_device(device_id, {
                "dhcp_state": new_state,
                "dhcp_running": running,
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
                # Clear any lingering manual override — we've taken
                # over this device.
                "dhcp_manual_override": False,
                "dhcp_manual_override_time": None,
            })
            logger.info(
                "[DHCP MONITOR] Server-mode probe for %s: %s (%s)",
                device_id, new_state, detail,
            )
        except Exception as exc:
            logger.error(
                "[DHCP MONITOR] Failed to write server-mode DHCP state for %s: %s",
                device_id, exc,
            )

        if running:
            self._note_leased(device_id)
            return

        # Server is down — restart via ensure_dhcp_services, subject
        # to the same backoff gate as the client path.
        if self._backoff_gate(device_id):
            return
        logger.info(
            "[DHCP MONITOR] Restarting DHCP server for %s (dnsmasq not running)",
            device_id,
        )
        self._note_restart_attempt(device_id)
        try:
            ensure_dhcp_services(
                self.device_db,
                device_id,
                interface,
                dhcp_config,
            )
        except Exception as restart_exc:
            logger.error(
                "[DHCP MONITOR] Failed to restart DHCP server for %s: %s",
                device_id, restart_exc,
            )

    def _check_clients(self) -> None:
        devices = self._get_dhcp_devices()
        if not devices:
            logger.debug("[DHCP MONITOR] No DHCP devices found")
            return

        logger.info("[DHCP MONITOR] Checking %d DHCP device(s)", len(devices))

        for device in devices:
            device_id = device.get("device_id")
            if not device_id:
                continue

            # v0.5.217 (audit fix G): skip devices with an active
            # manual override; auto-expire older ones.
            if self._manual_override_active(device):
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

            mode = (dhcp_config.get("mode") or device.get("dhcp_mode") or "").lower()

            # v0.5.217 (audit fix E): server-mode branch.
            if mode == "server":
                try:
                    self._check_server_device(device, interface, dhcp_config)
                except Exception as exc:
                    logger.error(
                        "[DHCP MONITOR] Failed to check server-mode device %s: %s",
                        device_id, exc,
                    )
                continue

            # Client-mode: unchanged flow, plus backoff gate and
            # override clearing on successful lease.
            try:
                snapshot = dhcp_utils.get_dhcp_client_snapshot(
                    self.device_db, device_id, interface, dhcp_config
                )
                if snapshot:
                    # Clear manual-override on write since we're
                    # explicitly taking the device over now.
                    write_payload = dict(snapshot)
                    if device.get("dhcp_manual_override"):
                        write_payload["dhcp_manual_override"] = False
                        write_payload["dhcp_manual_override_time"] = None
                    self.device_db.update_device(device_id, write_payload)
                    logger.debug(
                        "[DHCP MONITOR] Updated DHCP snapshot for %s: state=%s, ip=%s",
                        device_id,
                        snapshot.get("dhcp_state"),
                        snapshot.get("dhcp_lease_ip"),
                    )

                    # Per-protocol state-history timeline (de-dup'd).
                    try:
                        self.device_db.add_state_transition(
                            device_id,
                            "dhcp",
                            snapshot.get("dhcp_state") or "Unknown",
                            detail={
                                "lease_ip": snapshot.get("dhcp_lease_ip"),
                                "running": snapshot.get("dhcp_running"),
                            },
                        )
                    except Exception as _e:
                        logger.debug(
                            "[DHCP MONITOR] state-history insert skipped for %s: %s",
                            device_id, _e,
                        )

                    if snapshot.get("dhcp_state") == "Leased" and snapshot.get("dhcp_running"):
                        self._note_leased(device_id)
                        continue

                    needs_restart = True
                    if needs_restart:
                        # v0.5.217 (audit fix H): backoff gate.
                        if self._backoff_gate(device_id):
                            continue
                        logger.info(
                            "[DHCP MONITOR] Restarting dhclient for %s (state=%s, running=%s)",
                            device_id,
                            snapshot.get("dhcp_state"),
                            snapshot.get("dhcp_running"),
                        )
                        self._note_restart_attempt(device_id)
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
                                    if refreshed.get("dhcp_state") == "Leased":
                                        self._note_leased(device_id)
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
