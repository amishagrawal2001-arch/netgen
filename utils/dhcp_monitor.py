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
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import dhcp as dhcp_utils
from .dhcp import ensure_dhcp_services

logger = logging.getLogger(__name__)

# v0.5.267 (audit DHCP-mon F5): per-device write lock — sibling of
# v0.5.262 ARP + v0.5.264 BGP/OSPF/ISIS fixes. The DHCP monitor
# does 1-3 update_device calls plus 1-2 add_state_transition calls
# per tick; on-demand writers (/api/device/dhcp/restart, manual
# override toggle, stop_dhcp_services) can interleave and race
# the flag-clear vs the state-write. Same shape as the sibling
# monitors' _*_WRITE_LOCKS.
_DHCP_WRITE_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
_DHCP_WRITE_LOCKS_META_LOCK = threading.Lock()


def _dhcp_write_lock_for(device_id: str) -> threading.Lock:
    with _DHCP_WRITE_LOCKS_META_LOCK:
        return _DHCP_WRITE_LOCKS[device_id]


# v0.5.219 (audit fix C2): shared argv-parse pgrep helper so the
# server-mode probe stops false-matching ``dnsmasq ... eth10`` when
# the interface is ``eth1``. Bug M (v0.5.218) applied this pattern
# to ``_is_dhclient_running`` but overlooked the server-mode fallback
# ``pgrep -f 'dnsmasq.*{interface}'`` here — same substring collision.
# Prefer the more-specific conffile match when the caller supplies one.
def _has_dhcp_pool(dhcp_config: dict) -> bool:
    """v0.5.227: distinguish "no pool attached" from "server crashed".

    A server-mode device is INCAPABLE of running dnsmasq if the
    config doesn't carry an IPv4 pool (``pool_start`` +
    ``pool_end``) OR an IPv6 pool (``pool6_start`` + ``pool6_end``).
    The monitor uses this to write "No Pool" (config-incomplete)
    instead of "Server Down" (dnsmasq should be running but
    isn't) — and to skip the futile ensure_dhcp_services restart
    that would just re-hit the same no-pool refusal on every poll.
    """
    if not isinstance(dhcp_config, dict):
        return False
    v4 = (str(dhcp_config.get("pool_start") or "").strip() != "" and
          str(dhcp_config.get("pool_end") or "").strip() != "")
    v6 = (str(dhcp_config.get("pool6_start") or "").strip() != "" and
          str(dhcp_config.get("pool6_end") or "").strip() != "")
    return v4 or v6


def _pgrep_matching_argv(binary: str, needle: str,
                         *, container=None) -> bool:
    """True if any ``binary`` process has ``needle`` as a WHOLE
    argv token (interface name) OR as a substring of a filesystem
    path token (e.g. a conffile path). The mixed match is deliberate:
    interface names have to be exact tokens (eth1 must not match
    eth10), but conf-file paths like ``/etc/dnsmasq.d/ostg-eth1.conf``
    are unique enough that a substring match is safe.
    """
    if not binary or not needle:
        return False
    try:
        result = dhcp_utils._run_command(
            ["pgrep", "-a", "-f", binary],
            timeout=5, container=container,
        )
        if result.returncode not in (0, 1):
            return False
        needle_str = str(needle)
        for line in (result.stdout or "").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            argv = parts[1].split()
            for tok in argv:
                # Exact whole-token match (interface names).
                if tok == needle_str:
                    return True
                # File path containing the needle — safe because
                # the caller only passes uniquely-named paths.
                if ("/" in tok or "\\" in tok) and needle_str in tok:
                    return True
        return False
    except Exception as exc:
        logger.debug(
            "[DHCP MONITOR] pgrep argv scan for %s (needle=%s) failed: %s",
            binary, needle, exc,
        )
        return False


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
        # v0.5.267 (audit DHCP-mon F3 DEFERRED): parallel per-device
        # polling via ThreadPoolExecutor is the intended fix for
        # DHCP-F3 (sibling parity with BGP/OSPF/ISIS post v0.5.264),
        # but the extraction needs a proper `_check_one_device`
        # method — the existing `_check_clients` body uses
        # `continue` throughout, which requires restructuring to
        # `return` in a per-device helper. Deferred to a follow-up.
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
                if dhcp_mode not in ("client", "server"):
                    continue
                # v0.5.229 (audit U monitor-5): skip devices whose
                # top-level status is Stopped. Pre-fix, once the
                # 120s dhcp_manual_override timer expired,
                # ensure_dhcp_services would be called on a device
                # the operator deliberately stopped — the "Stop
                # DHCP" action was not durable past 120s. Now
                # Stopped stays stopped until the operator brings
                # the device back up via Start / Apply.
                _status = str(device.get("status") or "").lower()
                if _status == "stopped":
                    continue
                result.append(device)
            # v0.5.230 (audit P monitor-8): prune restart-attempt
            # counters for device_ids that no longer exist in the
            # DB. Pre-fix, deleting a device from the UI left its
            # entry in _dhcp_restart_attempts for the process's
            # lifetime — no functional bug, but the dict grew
            # unbounded across long-running sessions.
            _live_ids = {d.get("device_id") for d in devices}
            _orphaned = [
                did for did in list(self._dhcp_restart_attempts.keys())
                if did not in _live_ids
            ]
            for _did in _orphaned:
                self._dhcp_restart_attempts.pop(_did, None)
            if _orphaned:
                logger.debug(
                    "[DHCP MONITOR] Pruned %d orphan restart-attempt entries: %s",
                    len(_orphaned), _orphaned,
                )
            return result
        except Exception as exc:
            logger.error("[DHCP MONITOR] Failed to fetch devices: %s", exc)
            return []

    # Back-compat alias (v0.5.217): _get_client_devices used to
    # return only client-mode devices. Some external tooling may
    # call it by name; keep the shim so we don't silently break it.
    def _get_client_devices(self) -> List[Dict[str, Any]]:
        # v0.5.267 (audit DHCP-mon F6): operator-precedence trap.
        # Pre-fix the expression parsed as
        #   (dhcp_cfg.get("mode") if isinstance(..., dict) else (None or d.get("dhcp_mode") or ""))
        # so when dhcp_config IS a dict but lacks a "mode" key (documented
        # state — dhcp_mode column carries the mode instead), the ternary
        # returned None and `.lower()` raised AttributeError. Parenthesize
        # the ternary so the `or` chain composes correctly, matching the
        # `_get_dhcp_devices` shape at line 198-202.
        out: List[Dict[str, Any]] = []
        for d in self._get_dhcp_devices():
            dhcp_cfg = d.get("dhcp_config")
            mode_from_cfg = (
                dhcp_cfg.get("mode") if isinstance(dhcp_cfg, dict) else None
            )
            mode = (mode_from_cfg or d.get("dhcp_mode") or "").lower()
            if mode == "client":
                out.append(d)
        return out

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
        # v0.5.230 (audit P monitor-7): pre-fix, if the last attempt
        # was older than _BACKOFF_WINDOW_SECONDS (300s), the counter
        # was zeroed on the next attempt — so the effective delay
        # cap was ~240–480s (count 4–5) even though
        # _BACKOFF_MAX_SECONDS advertised 30 min. That defeated
        # the "escalating backoff for chronic failures" intent —
        # a device that failed steadily every ~5 min would restart
        # forever at the low delay tier. Now: window expiry
        # PARTIALLY resets — decrement by 1 (or halve) instead of
        # zeroing — so a genuinely fixed device with an accidental
        # blip still eventually resets via _note_leased, but a
        # chronic failure keeps escalating.
        now = time.time()
        state = self._dhcp_restart_attempts.get(device_id)
        if state and (now - float(state.get("last_attempt") or 0.0)) > _BACKOFF_WINDOW_SECONDS:
            # Window expired — decay the counter by half instead of
            # zeroing so the escalating cap remains reachable.
            _decayed = max(0, int(state.get("count") or 0) // 2)
            state = {"count": _decayed, "last_attempt": 0.0}
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
            # v0.5.236 (audit U2): use the same pidfile path that
            # utils/dhcp.py writes (DNSMASQ_PID_DIR="/run"). Pre-fix
            # the monitor looked under `/var/run/dnsmasq/` (the
            # subdirectory `dnsmasq/` invented here), which never
            # matched — so pgrep was the ONLY fallback. In images
            # where procps is stripped, or in tight PID namespaces,
            # pgrep can miss and dnsmasq is reported "Server Down"
            # even when it's alive.
            pidfile = f"/run/dnsmasq-{interface}.pid"
            try:
                # v0.5.219 (audit fix C2): argv-parse pgrep. Pre-fix,
                # the fallback was ``pgrep -f 'dnsmasq.*{interface}'``
                # — an unanchored substring match that would return
                # true for a dnsmasq bound to ``eth10`` when the
                # interface here is ``eth1``. Now we scan the pgrep
                # output ourselves via _pgrep_matching_argv, which
                # accepts a whole-token match on the interface name
                # OR a substring match inside a path token (so the
                # conffile path — which is uniquely named per
                # interface — still matches). See bug M in v0.5.218
                # for the same fix applied to _is_dhclient_running.
                if _pgrep_matching_argv("dnsmasq", conffile, container=container):
                    running = True
                    detail = "pgrep matched conffile"
                elif _pgrep_matching_argv("dnsmasq", interface, container=container):
                    running = True
                    detail = "pgrep matched interface argv token"
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

        # v0.5.227: preserve the "No Pool" state that
        # start_dhcp_server writes at Apply time (v0.5.223) when the
        # config is missing pool_start/pool_end. Pre-fix, the
        # monitor's next poll would overwrite "No Pool" with
        # "Server Down" (line 373 was unconditional), so the UI
        # showed a state that mismatched dhcp_last_error and made
        # operators think dnsmasq had crashed. Now: if the config
        # has no pool, dnsmasq CAN'T run and "Server Down" is the
        # wrong word — write "No Pool" and let the operator see the
        # matching last-error tooltip.
        has_pool = _has_dhcp_pool(dhcp_config)
        if running:
            new_state = "Server Running"
        elif not has_pool:
            new_state = "No Pool"
        else:
            new_state = "Server Down"
        try:
            update_payload = {
                "dhcp_state": new_state,
                "dhcp_running": running,
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
                # Clear any lingering manual override — we've taken
                # over this device.
                "dhcp_manual_override": False,
                "dhcp_manual_override_time": None,
            }
            # Keep dhcp_last_error in sync with the state we're
            # writing: "No Pool" carries an actionable last-error
            # message so the operator sees the same guidance
            # start_dhcp_server would have written at Apply time.
            if new_state == "No Pool":
                # v0.5.228: mirror the message from
                # utils/dhcp.start_dhcp_server so tooltip content stays
                # consistent whether the monitor or Apply wrote first.
                update_payload["dhcp_last_error"] = (
                    "No DHCP pool attached — click the 'Attach Pool' "
                    "button in the DHCP subtab toolbar to attach a "
                    "named pool, or Edit the device to set a Pool Start "
                    "/ Pool End range directly."
                )
            elif new_state == "Server Running":
                # v0.5.229 (audit U monitor-2): clear the stale
                # dhcp_last_error message when we recover. Pre-fix
                # a previous poll's failure string ("dnsmasq: no
                # address...", "Config write failed: ...") stuck
                # around forever after dnsmasq came back — the
                # tooltip lied. start_dhcp_server clears it on the
                # happy path; the monitor now does the same.
                update_payload["dhcp_last_error"] = ""
            self.device_db.update_device(device_id, update_payload)
            logger.info(
                "[DHCP MONITOR] Server-mode probe for %s: %s (%s)",
                device_id, new_state, detail,
            )
            # v0.5.229 (audit U monitor-3): record server-mode state
            # transitions to the history timeline. Pre-fix, only
            # _check_clients wrote add_state_transition — Server
            # Down → Server Running never showed up in the Ctrl+H
            # timeline. De-dup'd by add_state_transition itself.
            try:
                self.device_db.add_state_transition(
                    device_id,
                    "dhcp",
                    new_state,
                    detail={"running": running, "probe_detail": detail},
                )
            except Exception as _hist_exc:
                logger.debug(
                    "[DHCP MONITOR] server-mode state-history insert "
                    "skipped for %s: %s",
                    device_id, _hist_exc,
                )
        except Exception as exc:
            logger.error(
                "[DHCP MONITOR] Failed to write server-mode DHCP state for %s: %s",
                device_id, exc,
            )

        if running:
            self._note_leased(device_id)
            return

        # v0.5.227: skip the auto-restart when the config has no
        # pool. ensure_dhcp_services would just re-run the same
        # "no pool" refusal every 5s, spamming logs and looking
        # like a real crash loop in the audit. The state is
        # already "No Pool" — wait for the operator to attach one.
        if not has_pool:
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
                    # v0.5.267 (audit DHCP-mon F1): clear
                    # dhcp_last_error on lease recovery. The server-
                    # mode branch (line ~494) already does this in
                    # v0.5.229 monitor-2; the client branch never
                    # did. `get_dhcp_client_snapshot` doesn't
                    # include the field in its template so a stale
                    # "Lease timeout" message stuck in
                    # dhcp_last_error even after subsequent polls
                    # observed a healthy Leased state — UI tooltip
                    # kept lying about a currently-healthy client.
                    if (snapshot.get("dhcp_state") == "Leased"
                            and snapshot.get("dhcp_running")):
                        write_payload["dhcp_last_error"] = ""
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

                    # v0.5.229 (audit B1): don't tear down a dhclient
                    # that's legitimately in DHCP DORA (Requesting /
                    # Renewing) — the previous unconditional
                    # `needs_restart = True` restarted every poll if
                    # the state wasn't "Leased", so a DHCP DORA that
                    # took > 60 s (relay networks, slow servers)
                    # never converged. If dhclient IS running and
                    # its state indicates it's mid-handshake, let it
                    # finish on its own; only intervene when it's
                    # truly stopped or has fallen off the container.
                    _state = snapshot.get("dhcp_state") or ""
                    _running = bool(snapshot.get("dhcp_running"))
                    _mid_handshake = _state in ("Requesting", "Renewing", "Rebinding")
                    if _running and _mid_handshake:
                        logger.debug(
                            "[DHCP MONITOR] Skipping restart for %s: dhclient is running and in %s (DORA in flight)",
                            device_id, _state,
                        )
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
                                    # v0.5.230 (audit P monitor-10):
                                    # match the pre-restart check at
                                    # line 625 — require state=Leased
                                    # AND dhcp_running. Pre-fix, a
                                    # partial refresh where dhcp_running
                                    # was stale (dhclient still bringing
                                    # the lease live) prematurely
                                    # cleared the backoff counter and
                                    # the next real failure hit a fresh
                                    # counter (skipping the escalating
                                    # backoff).
                                    if (
                                        refreshed.get("dhcp_state") == "Leased"
                                        and refreshed.get("dhcp_running")
                                    ):
                                        self._note_leased(device_id)
                                        # v0.5.230 (audit P monitor-11):
                                        # record the recovery
                                        # transition to history — pre-
                                        # fix the monitor-driven restart
                                        # → Leased jump was never
                                        # written to device_state_history
                                        # so the Ctrl+H timeline showed
                                        # only the pre-restart failure.
                                        try:
                                            self.device_db.add_state_transition(
                                                device_id, "dhcp", "Leased",
                                                detail={
                                                    "restart_recovery": True,
                                                    "lease_ip": refreshed.get("dhcp_lease_ip"),
                                                },
                                            )
                                        except Exception:
                                            pass
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
