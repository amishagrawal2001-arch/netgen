#!/usr/bin/env python3
"""
ISIS (Intermediate System to Intermediate System) utility functions for OSTG.
Handles ISIS configuration, status monitoring, and neighbor management.
"""

import logging
import json
import re
import threading
import subprocess
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from utils.isis_net import validate_isis_net


# v0.5.417 (audit stream-FF3): vtysh returns exit-code 0 even when
# the output contains error markers like `% Unknown command` or
# `% Malformed`. Pre-fix every ISIS vtysh call trusted the exit code
# alone → silent rejections were logged as success. Same marker set
# as v0.5.403 T2 (bgp.py) and v0.5.416 EE2 (ospf.py).
_VTYSH_ERROR_MARKERS = (
    "% Unknown command",
    "% Malformed",
    "% Configuration failed",
    "% Invalid",
    "% Ambiguous command",
    "% Incomplete command",
    "% Command incomplete",
)


def _vtysh_output_has_error(output: str) -> bool:
    if not output:
        return False
    for _line in output.splitlines():
        _stripped = _line.strip()
        if not _stripped.startswith("%"):
            continue
        for _marker in _VTYSH_ERROR_MARKERS:
            if _marker in _stripped:
                return True
    return False


# v0.5.417 (audit stream-FF8): per-device lock so concurrent
# Apply/Start/Stop clicks on the same device don't step on each
# other's `configure terminal` state. Mirrors v0.5.383 X1 (BGP) and
# v0.5.416 EE11 (OSPF).
_ISIS_DEVICE_LOCKS: Dict[str, threading.Lock] = {}
_ISIS_DEVICE_LOCKS_GUARD = threading.Lock()


class _NullLock:
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False


def _isis_device_lock(device_id: Optional[str]):
    if not device_id:
        return _NullLock()
    with _ISIS_DEVICE_LOCKS_GUARD:
        _lk = _ISIS_DEVICE_LOCKS.get(device_id)
        if _lk is None:
            _lk = threading.Lock()
            _ISIS_DEVICE_LOCKS[device_id] = _lk
    return _lk


class IsisVrfProbeError(RuntimeError):
    """Raised by _isis_vrf_suffix when a VRF name is registered for a
    device but the kernel probe fails — pre-v0.5.417, the code
    silently dropped the VRF and wrote to the default routing
    instance, causing silent misconfig (hellos go out the default
    table, per-VRF iface never sees them, adjacencies stay Down).
    Callers should catch and abort the operation with a clear error
    to the operator."""


def _isis_vrf_suffix(device_id: Optional[str]) -> str:
    """Return ' vrf <name>' if this device has been provisioned with a
    Linux VRF, else ''.

    Used on the top-level `router isis CORE` block so isisd's RIB and
    neighbor state for this device land in the device's VRF table.
    The per-interface `ip router isis CORE` references just bind the
    iface to the named instance and don't carry a VRF keyword.

    v0.5.417 (audit stream-FF2): fail-CLOSED on probe failure. Pre-
    fix a subprocess hang OR a non-zero return code silently dropped
    the VRF suffix, so `router isis CORE` landed in the DEFAULT
    routing instance — isisd sent hellos out the default table,
    per-VRF interface never saw them, neighbors stayed Down. Same
    class as v0.5.403 BGP T1 and v0.5.416 OSPF EE1.

    New behavior: (a) 5s timeout on the `ip link show` probe;
    (b) if a VRF name IS registered for this device but the probe
    fails (timeout, non-zero rc, exception), RAISE
    `IsisVrfProbeError` so callers fail loudly instead of writing
    config into the wrong routing instance. (c) if no VRF name is
    registered at all (legacy single-device deployment), return
    "" — that's the intended non-VRF path.
    """
    if not device_id:
        return ""
    try:
        from utils.frr_docker import FRRDockerManager
        vrf_name = FRRDockerManager().vrf_name_for_device(device_id)
    except Exception as _exc_reg:
        # Registry lookup itself failed — legacy pre-VRF layout.
        logging.debug(f"[ISIS VRF] registry lookup failed for {device_id}: {_exc_reg}")
        return ""
    if not vrf_name:
        return ""
    try:
        check = subprocess.run(
            ["ip", "-o", "link", "show", vrf_name],
            capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired as _te:
        raise IsisVrfProbeError(
            f"[ISIS VRF] `ip link show {vrf_name}` timed out for device "
            f"{device_id} after 5 s — refusing to configure in the wrong "
            f"routing instance."
        ) from _te
    except Exception as _exc:
        raise IsisVrfProbeError(
            f"[ISIS VRF] probe for {vrf_name} on device {device_id} "
            f"raised {type(_exc).__name__}: {_exc} — refusing to fall "
            f"back to default VRF."
        ) from _exc
    if check.returncode != 0 or not (check.stdout or "").strip():
        raise IsisVrfProbeError(
            f"[ISIS VRF] VRF {vrf_name!r} was registered for device "
            f"{device_id} but `ip link show` returned rc={check.returncode}, "
            f"stdout={(check.stdout or '')!r}, stderr={(check.stderr or '')!r} — "
            f"refusing to fall back to default VRF."
        )
    return f" vrf {vrf_name}"


class IsisNetError(ValueError):
    """v0.5.417 (audit stream-FF5 + FF9): raised when an operator-
    supplied NET is missing or invalid. Pre-fix the code silently
    fell back to the hardcoded 49.0001.0000.0000.0001.00 default in
    FOUR places → two devices in the same lab ended up with the SAME
    system-id in the SAME area, corrupting the LSDB. Callers should
    catch and surface the reason to the operator."""


def _resolve_isis_net(area_id: Any, *, device_id: Optional[str] = None) -> str:
    """v0.5.417 (audit stream-FF5 + FF9): single choke point that
    validates the operator-supplied NET before we hand it to vtysh.

    - Empty / None NET → `IsisNetError` (no hardcoded fallback).
    - Malformed NET → `IsisNetError` with the validator's reason.
    - Valid NET → returned stripped.

    The device_id is included in the exception message for triage.
    """
    _raw = "" if area_id is None else str(area_id).strip()
    if not _raw:
        raise IsisNetError(
            f"ISIS NET is empty for device {device_id or '<unknown>'}. "
            f"v0.5.417 (FF5) no longer falls back to the shared "
            f"`49.0001.0000.0000.0001.00` default — configure a unique "
            f"NET per device before starting ISIS."
        )
    _reason = validate_isis_net(_raw)
    if _reason:
        raise IsisNetError(
            f"ISIS NET {_raw!r} for device {device_id or '<unknown>'} is "
            f"invalid: {_reason}."
        )
    return _raw


def _isis_interface_timer_lines(
    isis_config: Dict[str, Any],
    *,
    circuit_type: Optional[str] = None,
) -> List[str]:
    """v0.5.417 (audit stream-FF1): emit the per-interface ISIS timer
    and metric lines the operator picked in the UI. Pre-fix these
    fields were saved to the DB and displayed back but NEVER made it
    onto the router — a textbook silent-misconfig bug.

    - `hello_interval` → `isis hello-interval <N>` (seconds)
    - `hello_multiplier` → `isis hello-multiplier <N>` (dead = hello × mult)
    - `metric` → `isis metric <N>` (per-AF cost)

    Values are pulled from `isis_config`; blank/None values are
    skipped (FRR default remains in effect). The `circuit_type`
    argument stays optional because FF10 (MED) is out of the HIGH
    slice — kept as a parameter for the follow-up.
    """
    lines: List[str] = []
    _hi = str(isis_config.get("hello_interval") or "").strip()
    _hm = str(isis_config.get("hello_multiplier") or "").strip()
    _mt = str(isis_config.get("metric") or "").strip()
    if _hi:
        lines.append(f" isis hello-interval {_hi}")
    if _hm:
        lines.append(f" isis hello-multiplier {_hm}")
    if _mt:
        lines.append(f" isis metric {_mt}")
    if circuit_type:
        lines.append(f" isis circuit-type {circuit_type}")
    return lines


try:
    import docker.errors
except ImportError:
    # docker package not available
    docker = None

logger = logging.getLogger(__name__)

def get_isis_status(device_id: str, device_name: str, container_id: str) -> Dict[str, Any]:
    """
    Get ISIS status from FRR container.
    
    Args:
        device_id: Device identifier
        device_name: Device name
        container_id: Docker container ID
        
    Returns:
        Dictionary containing ISIS status information
    """
    try:
        import subprocess

        # Scope ISIS show commands to the device's VRF so we read
        # state from the right isisd instance. Same rationale as the
        # BGP/OSPF status-query fixes — without the `vrf <name>`
        # qualifier the queries hit the default-VRF instance which
        # has no IS-IS configured in our per-device-VRF model.
        _scope = _isis_vrf_suffix(device_id).strip()  # "vrf <name>" or ""
        _scope_suffix = f" {_scope}" if _scope else ""

        # v0.5.362 (audit isis-shell-true-interpolation, A7): switch
        # from `shell=True` with an interpolated `container_id` /
        # `_scope_suffix` to argv-list form. `container_id` is
        # currently always a UUID and `_scope_suffix` comes from a
        # derived VRF name, but nothing in this file enforces that
        # — a future refactor that passes user-provided text through
        # would open a shell-injection path. Argv form eliminates
        # that class of bug entirely.
        _scope_argv = (f" {_scope}" if _scope else "")
        neighbor_cmd = [
            "docker", "exec", container_id, "vtysh",
            "-c", f"sh isis{_scope_argv} nei det json",
        ]
        neighbor_result = subprocess.run(
            neighbor_cmd, shell=False, capture_output=True, text=True, timeout=10,
        )

        summary_cmd = [
            "docker", "exec", container_id, "vtysh",
            "-c", f"sh isis{_scope_argv} summary json",
        ]
        summary_result = subprocess.run(
            summary_cmd, shell=False, capture_output=True, text=True, timeout=10,
        )
        
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
        
        # Parse ISIS summary
        if summary_result.returncode == 0 and summary_result.stdout.strip():
            try:
                summary_data = json.loads(summary_result.stdout.strip())
                
                # Only mark ISIS as running if there's actual ISIS configuration
                # Empty dict {} means ISIS is not configured
                if summary_data and isinstance(summary_data, dict) and len(summary_data) > 0:
                    # Extract basic ISIS information
                    isis_status["system_id"] = summary_data.get("system-id", "")
                    isis_status["uptime"] = summary_data.get("up-time", "")
                    
                    # Check if ISIS is actually configured (has system-id or areas)
                    if isis_status["system_id"] or summary_data.get("areas"):
                        isis_status["isis_running"] = True
                        isis_status["isis_state"] = "Running"
                
                # Extract areas information
                areas = summary_data.get("areas", [])
                for area in areas:
                    area_info = {
                        "area": area.get("area", ""),
                        "net": area.get("net", ""),
                        "levels": area.get("levels", [])
                    }
                    isis_status["areas"].append(area_info)
                    
                    # Set ISIS net from first area
                    if not isis_status["net"]:
                        isis_status["net"] = area_info["net"]
                
            except json.JSONDecodeError as e:
                logger.warning(f"[ISIS] Failed to parse ISIS summary JSON: {e}")
        
        # Parse ISIS neighbors
        if neighbor_result.returncode == 0 and neighbor_result.stdout.strip():
            try:
                neighbor_data = json.loads(neighbor_result.stdout.strip())
                
                areas = neighbor_data.get("areas", [])
                for area in areas:
                    circuits = area.get("circuits", [])
                    for circuit in circuits:
                        interface_info = circuit.get("interface", {})
                        adj_info = circuit.get("adj", "")

                        # v0.5.264 (audit ISIS-F7): the circuit's
                        # `interface.state` is the LINK admin state
                        # (Up on any admin-up L2 iface, no matter
                        # whether an ISIS adjacency actually formed).
                        # An admin-up interface with no adjacency
                        # heard-from produces a `state: "Up"` here.
                        # Determine per-adjacency Up-ness by
                        # checking the adjacency's own state
                        # (isisd's JSON exposes it under
                        # `adjacencies[].state` on newer FRR; when
                        # missing, fall back to the presence of a
                        # non-empty system_id — an isolated circuit
                        # has adj == "").
                        _adj_list = circuit.get("adjacencies") or []
                        _adj_state = None
                        if _adj_list and isinstance(_adj_list, list):
                            first = _adj_list[0] or {}
                            _adj_state = str(first.get("state") or "").strip()
                        # Explicit Up flags: FRR variants use "Up",
                        # "Init", "Down", "Failed". Treat only "Up"
                        # as established. When _adj_state is None
                        # (older isisd JSON), fall back to system_id
                        # non-empty AS a weak signal — but ONLY
                        # combined with iface state Up.
                        _iface_state_up = str(interface_info.get("state") or "").strip() == "Up"
                        if _adj_state:
                            _neigh_up = _adj_state == "Up"
                        else:
                            _neigh_up = _iface_state_up and bool(adj_info)

                        neighbor_info = {
                            "state": "Up" if _neigh_up else "Down",
                            "type": "ISIS",
                            "interface": interface_info.get("name", ""),
                            "area": area.get("area", ""),
                            "level": f"Level-{circuit.get('level', 2)}",
                            "net": interface_info.get("area-address", {}).get("isonet", ""),
                            "system_id": adj_info,
                            "priority": "64",  # Default priority
                            "uptime": interface_info.get("last-ago", ""),
                            "circuit_type": interface_info.get("circuit-type", ""),
                            "speaks": interface_info.get("speaks", ""),
                            "snpa": interface_info.get("snpa", ""),
                            "ipv4_address": interface_info.get("ipv4-address", {}).get("ipv4", ""),
                            "ipv6_link_local": interface_info.get("ipv6-link-local", {}).get("ipv6", ""),
                            "ipv6_global": interface_info.get("ipv6-global", {}).get("ipv6", "")
                        }

                        isis_status["neighbors"].append(neighbor_info)

                # v0.5.264 (audit ISIS-F7): only mark Established
                # when at least one adjacency is actually Up. Pre-fix
                # ANY listed circuit (even admin-up-with-no-peer)
                # flipped isis_established → True, turning the UI
                # chip green on isolated interfaces.
                _any_up = any(n.get("state") == "Up" for n in isis_status["neighbors"])
                if _any_up:
                    isis_status["isis_established"] = True
                    isis_status["isis_state"] = "Established"
                else:
                    isis_status["isis_state"] = "Running"
                    
            except json.JSONDecodeError as e:
                logger.warning(f"[ISIS] Failed to parse ISIS neighbor JSON: {e}")
        
        # If no neighbors but ISIS is running, set state to Running
        if isis_status["isis_running"] and not isis_status["neighbors"]:
            isis_status["isis_state"] = "Running"
        
        logger.info(f"[ISIS] Status for {device_name}: {isis_status['isis_state']}, {len(isis_status['neighbors'])} neighbors")
        return isis_status
        
    except subprocess.TimeoutExpired:
        logger.error(f"[ISIS] Timeout getting ISIS status for {device_name}")
        return {
            "isis_running": False,
            "isis_established": False,
            "isis_state": "Timeout",
            "neighbors": [],
            "areas": [],
            "system_id": "",
            "net": "",
            "uptime": None
        }
    except Exception as e:
        logger.error(f"[ISIS] Error getting ISIS status for {device_name}: {e}")
        return {
            "isis_running": False,
            "isis_established": False,
            "isis_state": "Error",
            "neighbors": [],
            "areas": [],
            "system_id": "",
            "net": "",
            "uptime": None
        }

def configure_isis_neighbor(device_id: str, isis_config: Dict[str, Any], device_name: str = None, ipv4: str = None, ipv6: str = None) -> bool:
    """Configure ISIS for a device in FRR container.

    v0.5.417 (audit stream-FF8): public wrapper acquires the per-
    device lock, then dispatches to `_configure_isis_neighbor_locked`.
    """
    with _isis_device_lock(device_id):
        return _configure_isis_neighbor_locked(
            device_id, isis_config, device_name=device_name, ipv4=ipv4, ipv6=ipv6,
        )


def _configure_isis_neighbor_locked(device_id: str, isis_config: Dict[str, Any], device_name: str = None, ipv4: str = None, ipv6: str = None) -> bool:
    """Locked body of configure_isis_neighbor. Never call directly —
    always go through `configure_isis_neighbor` so the per-device
    lock is held for the duration of the vtysh session."""
    try:
        from utils.frr_docker import FRRDockerManager

        logging.info(f"[ISIS CONFIGURE] Configuring ISIS for device {device_name} ({device_id})")

        # v0.5.417 (audit stream-FF5 + FF9): validate NET at the top
        # so we bail before touching the container if the operator
        # never configured one (or gave us garbage).
        try:
            area_id_validated = _resolve_isis_net(
                isis_config.get("area_id"), device_id=device_id,
            )
        except IsisNetError as _net_exc:
            logging.error(f"[ISIS CONFIGURE] {_net_exc}")
            return False

        # v0.5.417 (audit stream-FF2): probe VRF early so a
        # misconfigured device can't push router-level config into
        # the default VRF.
        try:
            _vrf_suffix = _isis_vrf_suffix(device_id)
        except IsisVrfProbeError as _vrf_exc:
            logging.error(f"[ISIS CONFIGURE] {_vrf_exc}")
            return False
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        logger.info(f"[ISIS CONFIGURE] Using container {container_name} for device {device_name} ({device_id})")
        container = frr_manager.client.containers.get(container_name)
        
        # Wait for container to be ready and daemons to start
        # Optimized to match BGP performance: fewer retries, faster timeout
        # v0.5.362 (audit isis-configure-blocks-flask-20s, A3): pre-fix
        # max_retries=10 × retry_delay=2s = 20s of sync `time.sleep`
        # inside a Flask HTTP worker. UI spinner appeared frozen; a
        # concurrent Apply on a second device queued 20s behind. Cap
        # total wait at ~5s (retries × delay + exec_run timeouts)
        # which is enough headroom for the common "container just
        # started" case; slower startups will fail the check and log
        # "not ready … proceeding anyway", same recovery as before.
        import time
        max_retries = 5
        retry_delay = 1
        
        def exec_run_with_timeout(cmd, timeout_sec=3):
            """Execute container.exec_run with a timeout using threading.
            Reduced timeout from 5s to 3s for faster checks (matching BGP speed)."""
            result = [None]
            exception = [None]
            
            def run_exec():
                try:
                    result[0] = container.exec_run(cmd)
                except Exception as e:
                    exception[0] = e
            
            exec_thread = threading.Thread(target=run_exec, daemon=True)
            exec_thread.start()
            exec_thread.join(timeout=timeout_sec)
            
            if exec_thread.is_alive():
                # Thread is still running - timeout occurred
                logger.warning(f"[ISIS CONFIGURE] exec_run timeout after {timeout_sec}s - command may not have completed")
                return None
            elif exception[0]:
                raise exception[0]
            else:
                return result[0]
        
        isisd_ready = False
        for attempt in range(max_retries):
            try:
                # Check if isisd is ready by running an ISIS-specific command (like BGP does)
                # This ensures isisd is actually ready to accept configuration commands
                check_result = exec_run_with_timeout("vtysh -c 'show isis'", timeout_sec=3)
                check_output = check_result.output.decode('utf-8') if isinstance(check_result.output, bytes) else str(check_result.output) if check_result else ""
                output_lower = check_output.lower() if check_output else ""

                if check_result and check_result.exit_code == 0 and "isisd is not running" not in output_lower:
                    isisd_ready = True
                    logger.info(f"[ISIS CONFIGURE] Container and FRR daemons ready for {device_name} (attempt {attempt + 1})")
                    break
                else:
                    # ISIS daemon not ready yet or timeout
                    logger.debug(f"[ISIS CONFIGURE] ISIS daemon not ready yet (attempt {attempt + 1}/{max_retries}) - output: {check_output.strip() if check_output else 'No output'}")
            except Exception as e:
                # Container exec failed
                logger.debug(f"[ISIS CONFIGURE] Container exec failed (attempt {attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                if not isisd_ready:
                    logger.warning(f"[ISIS CONFIGURE] ISIS daemon not ready after {max_retries} attempts for {device_name}, proceeding anyway (may fail)")
        
        # Extract ISIS configuration (handle None values - use default if None)
        # v0.5.417 (audit stream-FF5): NEVER fall back to the shared
        # hardcoded default `49.0001.0000.0000.0001.00`. The NET has
        # already been validated at the top of this function and
        # `area_id_validated` is guaranteed to be a well-formed NET.
        area_id = area_id_validated
        system_id = isis_config.get("system_id") or "0000.0000.0001"
        level = isis_config.get("level") or "Level-2"
        hello_interval = isis_config.get("hello_interval") or "10"
        hello_multiplier = isis_config.get("hello_multiplier") or "3"
        metric = isis_config.get("metric") or "10"
        interface = isis_config.get("interface") or ""
        
        # Convert level to FRR format
        level_map = {
            "Level-1": "level-1-only",
            "Level-2": "level-2-only", 
            "Level-1-2": "level-1-2"
        }
        frr_level = level_map.get(level, "level-2-only")
        
        # Get device data to determine interface and address families
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device_data = device_db.get_device(device_id) if device_id else None
        
        # Determine interface name (with VLAN if applicable) - prioritize database over config
        if not interface and device_data:
            interface_from_db = device_data.get("interface", "")
            vlan = device_data.get("vlan", "0")
            
            # CRITICAL: Normalize interface name - remove leading " - " or "- " prefix if present
            # This handles cases where interface is stored as "- ens4np0" instead of "ens4np0"
            if interface_from_db:
                interface_from_db = interface_from_db.strip()
                if interface_from_db.startswith("- "):
                    interface_from_db = interface_from_db[2:].strip()
                elif interface_from_db.startswith(" - "):
                    interface_from_db = interface_from_db[3:].strip()
            # CRITICAL: Check if a unique-named VLAN interface was created (e.g., vlan20-ens4np0)
            actual_vlan_interface = device_data.get("actual_vlan_interface", "")
            if actual_vlan_interface and actual_vlan_interface.strip():
                actual_vlan_interface = actual_vlan_interface.strip()
                logging.info(f"[ISIS CONFIGURE] Found actual VLAN interface name '{actual_vlan_interface}' in database, will use it instead of 'vlan{vlan}'")
            # CRITICAL: Validate interface name when VLAN is not used
            # NOTE: For VLAN interfaces, use the actual interface name if available (e.g., vlan20-ens4np0),
            # otherwise use just vlan{vlan} - Linux and FRR can reference VLAN interfaces
            # by their base name (vlan21) even though ip link show displays them as vlan21@ens5np0
            if vlan and vlan != "0":
                # Use actual interface name if unique-named interface was created, otherwise use vlan{vlan}
                if actual_vlan_interface:
                    interface = actual_vlan_interface
                else:
                    interface = f"vlan{vlan}"
            elif interface_from_db:
                interface = interface_from_db
            else:
                # Interface is required - log error and use empty string (will cause configuration to fail gracefully)
                logging.error(f"[ISIS CONFIGURE] Interface name is required when VLAN is not specified for device {device_id}")
                interface = ""  # Will cause vtysh commands to fail, but better than silently using wrong interface
        else:
            # CRITICAL: Normalize interface name if it was set from config
            if interface:
                interface = interface.strip()
                if interface.startswith("- "):
                    interface = interface[2:].strip()
                elif interface.startswith(" - "):
                    interface = interface[3:].strip()
            
            vlan = device_data.get("vlan", "0") if device_data else "0"
            
            # CRITICAL: If interface is still invalid (e.g., "- ens4np0" or empty) and VLAN is 0,
            # use the interface from database
            if (not interface or interface.startswith("-")) and vlan == "0" and device_data:
                interface_from_db = device_data.get("interface", "")
                if interface_from_db:
                    interface_from_db = interface_from_db.strip()
                    if interface_from_db.startswith("- "):
                        interface_from_db = interface_from_db[2:].strip()
                    elif interface_from_db.startswith(" - "):
                        interface_from_db = interface_from_db[3:].strip()
                    if interface_from_db:
                        interface = interface_from_db
                        logging.info(f"[ISIS CONFIGURE] Using normalized interface '{interface}' from database for non-VLAN device")
            # CRITICAL: Check if a unique-named VLAN interface was created (e.g., vlan20-ens4np0)
            actual_vlan_interface = None
            if device_data:
                actual_vlan_interface = device_data.get("actual_vlan_interface", "")
                if actual_vlan_interface and actual_vlan_interface.strip():
                    actual_vlan_interface = actual_vlan_interface.strip()
                    logging.info(f"[ISIS CONFIGURE] Found actual VLAN interface name '{actual_vlan_interface}' in database, will use it instead of 'vlan{vlan}'")
            # CRITICAL: Validate interface name when VLAN is not used
            # NOTE: For VLAN interfaces, use the actual interface name if available (e.g., vlan20-ens4np0),
            # otherwise use just vlan{vlan} - Linux and FRR can reference VLAN interfaces
            # by their base name (vlan21) even though ip link show displays them as vlan21@ens5np0
            if vlan and vlan != "0":
                # Use actual interface name if unique-named interface was created, otherwise use vlan{vlan}
                if actual_vlan_interface:
                    interface = actual_vlan_interface
                else:
                    interface = f"vlan{vlan}"
            elif interface:
                # interface already set from config
                pass
            else:
                # Interface is required - log error and use empty string (will cause configuration to fail gracefully)
                logging.error(f"[ISIS CONFIGURE] Interface name is required when VLAN is not specified for device {device_id}")
                interface = ""  # Will cause vtysh commands to fail, but better than silently using wrong interface
        
        # Determine address families based on configured IPs
        enable_ipv4 = bool(ipv4 and ipv4.strip())
        enable_ipv6 = bool(ipv6 and ipv6.strip())
        
        # If IPs not provided, get from database
        if not enable_ipv4 and device_data:
            enable_ipv4 = bool(device_data.get("ipv4_address"))
        if not enable_ipv6 and device_data:
            enable_ipv6 = bool(device_data.get("ipv6_address"))
        
        # Build ISIS configuration commands (configure router first, then interfaces)
        # Note: Global router-id is configured in frr_docker.py when container is created
        # Note: Interface IP addresses (IPv4/IPv6) are configured via frr.conf.template
        # when the container is created, not via vtysh commands here
        logging.info(f"[ISIS CONFIGURE] About to build ISIS commands - enable_ipv4={enable_ipv4}, enable_ipv6={enable_ipv6}, area_id={area_id}, interface={interface}, frr_level={frr_level}")
        vtysh_commands = [
            "configure terminal",
            # Configure router-level ISIS first. The optional ` vrf <name>`
            # suffix scopes this isisd instance to the device's Linux
            # VRF so multi-device-on-same-NIC deployments don't share
            # an IS-IS RIB. The suffix was probed at the top of this
            # function via `_vrf_suffix` (FF2 fail-closed).
            f"router isis CORE{_vrf_suffix}",
            f"is-type {frr_level}",
            f"net {area_id}",
            "exit",
            # Configure interface ISIS
            f"interface {interface}",
        ]

        # Add IPv4 or IPv6 ISIS routing based on configured addresses
        if enable_ipv4:
            vtysh_commands.append(f" ip router isis CORE")
        if enable_ipv6:
            vtysh_commands.append(f" ipv6 router isis CORE")

        vtysh_commands.append(f" isis network point-to-point")
        # v0.5.417 (audit stream-FF1): emit the per-interface timer
        # and metric lines the operator picked in the UI. Pre-fix
        # `hello_interval` / `hello_multiplier` / `metric` were saved
        # to the DB and shown back in the UI but never touched vtysh.
        vtysh_commands.extend(_isis_interface_timer_lines(isis_config))
        vtysh_commands.append("exit")
        
        # Add loopback interface to ISIS if loopback IPs are configured
        loopback_ipv4 = None
        loopback_ipv6 = None
        if device_data:
            loopback_ipv4 = device_data.get('loopback_ipv4')
            if loopback_ipv4 and loopback_ipv4.strip():
                loopback_ipv4 = loopback_ipv4.strip().split('/')[0]
            loopback_ipv6 = device_data.get('loopback_ipv6')
            if loopback_ipv6 and loopback_ipv6.strip():
                loopback_ipv6 = loopback_ipv6.strip().split('/')[0]
            logging.info(f"[ISIS CONFIGURE] Retrieved loopback IPs from database - IPv4: {loopback_ipv4}, IPv6: {loopback_ipv6}")
        else:
            logging.warning(f"[ISIS CONFIGURE] device_data is None, cannot retrieve loopback IPs")
        
        # Configure loopback interface for ISIS if loopback IPs exist
        # Note: Loopback should be configured for ISIS based on loopback IPs, not main interface IPs
        # Configure loopback IPs and ISIS together
        if loopback_ipv4 or loopback_ipv6:
            logging.info(f"[ISIS CONFIGURE] Configuring loopback interface for ISIS - IPv4: {loopback_ipv4}, IPv6: {loopback_ipv6}")
            vtysh_commands.append("interface lo")
            if loopback_ipv4:
                vtysh_commands.append(f" ip address {loopback_ipv4}/32")
                vtysh_commands.append(" ip router isis CORE")
                logging.info(f"[ISIS CONFIGURE] Adding loopback interface with IPv4 {loopback_ipv4}/32 to ISIS")
            if loopback_ipv6:
                vtysh_commands.append(f" ipv6 address {loopback_ipv6}/128")
                vtysh_commands.append(" ipv6 router isis CORE")
                logging.info(f"[ISIS CONFIGURE] Adding loopback interface with IPv6 {loopback_ipv6}/128 to ISIS")
            vtysh_commands.append("exit")
        else:
            logging.info(f"[ISIS CONFIGURE] No loopback IPs configured, skipping loopback interface configuration for ISIS")
        
        vtysh_commands.extend([
            "end",
            "write"
        ])
        
        # Filter out Nones from optional lines
        vtysh_commands = [c for c in vtysh_commands if c]
        
        # Execute commands using here document
        logging.info(f"[ISIS CONFIGURE] About to execute commands - vtysh_commands length: {len(vtysh_commands)}")
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[ISIS CONFIGURE] Executing ISIS configuration commands")
        logging.info(f"[ISIS CONFIGURE] Commands list: {vtysh_commands}")
        logging.info(f"[ISIS CONFIGURE] Full here-doc command:\n{exec_cmd}")
        
        # Use timeout wrapper to prevent hanging if container is not ready
        # Use longer timeout (30s) for full configuration execution (ISIS config can take time)
        result = exec_run_with_timeout(["bash", "-c", exec_cmd], timeout_sec=30)
        if not result:
            logging.error(f"[ISIS CONFIGURE] Command timed out or failed (no result) - container may not be ready or command took too long")
            return False
        
        logging.info(f"[ISIS CONFIGURE] Command exit code: {result.exit_code}")
        try:
            _out = result.output.decode()
        except Exception:
            _out = str(result.output)
        if _out:
            logging.debug(f"[ISIS CONFIGURE] vtysh output:\n{_out}")
            if "isisd is not running" in _out.lower():
                logging.error(f"[ISIS CONFIGURE] Unable to configure ISIS because isisd daemon is not running")
                return False
        
        if result.exit_code != 0:
            logging.error(f"[ISIS CONFIGURE] Command failed: {result.output.decode()}")
            return False
        # v0.5.417 (audit stream-FF3): vtysh returns rc=0 even when
        # commands are rejected — scan output for `%` markers.
        if _vtysh_output_has_error(_out):
            logging.error(
                f"[ISIS CONFIGURE] vtysh output contained error markers "
                f"despite rc=0; refusing to report success. Output:\n{_out}"
            )
            return False
        logging.info(f"[ISIS CONFIGURE] ✅ ISIS configuration successful")
        
        # Update database with ISIS config and status
        try:
            from utils.device_database import DeviceDatabase
            from datetime import datetime, timezone
            device_db = DeviceDatabase()
            # Save the actual config values used (with defaults applied), not the original input
            saved_isis_config = {
                'interface': interface,
                'area_id': area_id,
                'system_id': system_id,
                'level': level,
                'hello_interval': hello_interval,
                'hello_multiplier': hello_multiplier,
                'metric': metric
            }
            update_data = {
                'isis_config': saved_isis_config,  # Save actual ISIS configuration values used
                'isis_running': True,
                'isis_established': False,  # Set to False initially - monitor will update when actually established
                'isis_state': 'Starting',
                'isis_system_id': system_id,
                'isis_net': area_id,
                'last_isis_check': datetime.now(timezone.utc).isoformat(),
                'isis_manual_override': False,
                'isis_manual_override_time': None
            }
            device_db.update_device(device_id, update_data)
            logging.info(f"[ISIS CONFIGURE] Updated ISIS config and status in database for device {device_name}")
        except Exception as e:
            logging.warning(f"[ISIS CONFIGURE] Failed to update ISIS config and status in database: {e}")
        
        logging.info(f"[ISIS CONFIGURE] ✅ Successfully configured ISIS for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[ISIS CONFIGURE] Error configuring ISIS: {e}")
        return False

def start_isis_neighbor(device_id: str, device_name: str, container_id: str, isis_config: Dict[str, Any]) -> bool:
    """
    Start ISIS on a device.

    v0.5.417 (audit stream-FF8): public wrapper acquires the per-
    device lock, then dispatches to `_start_isis_neighbor_locked`.

    Args:
        device_id: Device identifier
        device_name: Device name
        container_id: Docker container ID
        isis_config: ISIS configuration

    Returns:
        True if successful, False otherwise
    """
    with _isis_device_lock(device_id):
        return _start_isis_neighbor_locked(device_id, device_name, container_id, isis_config)


def _start_isis_neighbor_locked(device_id: str, device_name: str, container_id: str, isis_config: Dict[str, Any]) -> bool:
    """Locked body of start_isis_neighbor. Never call directly — go
    through `start_isis_neighbor` so the per-device lock is held for
    the whole vtysh session."""
    try:
        # Normalize isis_config if passed as JSON string
        if isinstance(isis_config, str):
            try:
                parsed = json.loads(isis_config)
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                isis_config = parsed if isinstance(parsed, dict) else {}
            except Exception:
                isis_config = {}

        # Resolve container (always use Docker SDK exec for reliability)
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)

        # Determine interfaces to bring up ISIS on (configured interface + device VLAN)
        interfaces_to_enable = []
        configured_interface = isis_config.get("interface")
        if configured_interface:
            interfaces_to_enable.append(configured_interface)
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            if device_data and device_data.get('vlan'):
                vlan_if = f"vlan{device_data.get('vlan')}"
                if vlan_if and vlan_if not in interfaces_to_enable:
                    interfaces_to_enable.append(vlan_if)
        except Exception:
            pass
        # v0.5.417 (audit stream-FF7): NEVER fall back to hardcoded
        # `vlan20`. Pre-fix this was the sole "start something" path
        # when the device had no configured interface and no VLAN
        # column, meaning device D's Start could reach across the
        # host and bring up device E's shared `vlan20`. Fail-loud
        # instead — the operator must configure an interface.
        if not interfaces_to_enable:
            logger.error(
                f"[ISIS START] No interface configured for device {device_name} "
                f"({device_id}) and no VLAN column in the DB. Refusing to fall "
                f"back to hardcoded `vlan20` (v0.5.417 FF7)."
            )
            return False

        # Also ensure router-level config is present to match what stop removes
        # Compute router-level parameters
        # v0.5.417 (audit stream-FF5 + FF9): validate the operator-
        # supplied NET; no hardcoded fallback.
        try:
            area_id = _resolve_isis_net(
                isis_config.get("area_id"), device_id=device_id,
            )
        except IsisNetError as _net_exc:
            logger.error(f"[ISIS START] {_net_exc}")
            return False
        level = isis_config.get("level", "Level-2")
        level_map = {"Level-1": "level-1-only", "Level-2": "level-2-only", "Level-1-2": "level-1-2"}
        frr_level = level_map.get(level, "level-2-only")

        # v0.5.417 (audit stream-FF2): VRF probe fail-closed so a
        # missing / mis-registered VRF can't route config into the
        # default routing instance.
        try:
            _vrf_suffix = _isis_vrf_suffix(device_id)
        except IsisVrfProbeError as _vrf_exc:
            logger.error(f"[ISIS START] {_vrf_exc}")
            return False

        # Determine address families based on device IP configuration.
        # v0.5.362 (audit isis-db-fallback-double-af, A2): pre-fix, on
        # ANY exception the fallback set both `enable_ipv4=True` and
        # `enable_ipv6=True` — so a transient sqlite lock during
        # Start ISIS on a v4-only device pushed `ipv6 router isis
        # CORE` onto an interface that had no v6 address, adjacency
        # never came up over v6, and `isis_state` stuck at Starting.
        # Now: prefer isis_config's `ipv4_enabled` / `ipv6_enabled`
        # flags (v0.5.205 populates these from the operator's per-AF
        # checkboxes) — that's the operator's stated intent, safe to
        # trust even when the DB read fails. Fall back to v4-only if
        # even the config lacks the flags, matching the historical
        # v4-default and avoiding false-v6 adjacency attempts.
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            dhcp_mode = ""
            if device_data:
                dhcp_mode = (device_data.get("dhcp_mode") or "").lower()
            enable_ipv4 = bool(device_data and device_data.get('ipv4_address'))
            enable_ipv6 = bool(device_data and device_data.get('ipv6_address'))
            if dhcp_mode == "client":
                enable_ipv4 = True
        except Exception as _db_exc:
            logger.warning(
                "[ISIS START] v0.5.362 device_db read failed for %s: %s "
                "— falling back to isis_config's per-AF flags "
                "(operator intent) instead of enabling both", device_id, _db_exc,
            )
            # v0.5.205 stores ipv4_enabled / ipv6_enabled in the
            # per-protocol config dict. Trust that when the DB is
            # unavailable. Final fallback is v4-only, NOT both.
            _cfg_v4 = isis_config.get("ipv4_enabled")
            _cfg_v6 = isis_config.get("ipv6_enabled")
            if _cfg_v4 is not None or _cfg_v6 is not None:
                enable_ipv4 = bool(_cfg_v4) if _cfg_v4 is not None else False
                enable_ipv6 = bool(_cfg_v6) if _cfg_v6 is not None else False
            else:
                enable_ipv4 = True
                enable_ipv6 = False

        # Ensure router process first, then enable interface (some FRR builds require router before interface attach)
        # Note: Global router-id is configured in frr_docker.py when container is created
        # VRF suffix scopes the IS-IS instance to the device's Linux
        # VRF when multi-device-on-same-iface is in use.
        # v0.5.417 (audit stream-FF6): the pre-fix builder unconditionally
        # issued `no net 49.0001.0000.0000.0001.00` — meaning any device
        # whose NET the operator had explicitly set to that value would
        # get it stripped every Start, and any two devices in the same
        # lab that ever fell back to that same hardcoded NET landed
        # with duplicate system-IDs in the same area (LSDB corruption).
        # The new NET is set unconditionally from the validated operator
        # input, and no hardcoded default is touched.
        vtysh_commands = [
            "configure terminal",
            f"router isis CORE{_vrf_suffix}",
            f"is-type {frr_level}",
            f"net {area_id}",
            "exit",
        ]
        for iface in interfaces_to_enable:
            vtysh_commands.append(f"interface {iface}")
            # Add IPv4 or IPv6 based on configured addresses
            if enable_ipv4:
                vtysh_commands.append(" ip router isis CORE")
            if enable_ipv6:
                vtysh_commands.append(" ipv6 router isis CORE")
            vtysh_commands.append(" isis network point-to-point")
            # v0.5.417 (audit stream-FF1): per-interface timer +
            # metric lines that used to be silently dropped.
            vtysh_commands.extend(_isis_interface_timer_lines(isis_config))
            vtysh_commands.append("exit")
        # Remove None entries from optional lines
        vtysh_commands = [c for c in vtysh_commands if c]
        vtysh_commands.extend(["end", "write"])

        cmd_input = "\n".join(vtysh_commands)

        # Use Docker SDK exec with here-doc
        logger.info(f"[ISIS START] Using container SDK for {device_name} (container {container_name})")
        result = container.exec_run(["bash", "-c", f"vtysh << 'EOF'\n{cmd_input}\nEOF" ])
        exit_code = result.exit_code
        stdout = result.output.decode() if isinstance(result.output, (bytes, bytearray)) else str(result.output)
        logger.info(f"[ISIS START] exit_code={exit_code}")
        if stdout:
            logger.debug(f"[ISIS START] vtysh output:\n{stdout}")

        # v0.5.417 (audit stream-FF3): vtysh returns rc=0 even when
        # it rejects individual lines; scan output for `%` markers.
        if exit_code == 0 and _vtysh_output_has_error(stdout):
            logger.error(
                f"[ISIS START] vtysh output for {device_name} contained "
                f"error markers despite rc=0; refusing to report success. "
                f"Output:\n{stdout}"
            )
            return False

        if exit_code == 0:
            logger.info(f"[ISIS START] Successfully started ISIS for {device_name}")

            # Update database with ISIS status
            try:
                from .device_database import DeviceDatabase
                device_db = DeviceDatabase()
                
                update_data = {
                    'isis_running': True,
                    'isis_state': 'Starting',
                    'isis_established': False,  # Will be updated by monitor
                    'last_isis_check': datetime.now(timezone.utc).isoformat(),
                    'isis_manual_override': False,
                    'isis_manual_override_time': None
                }
                device_db.update_device(device_id, update_data)
                logger.info(f"[ISIS START] Updated ISIS status in database for device {device_name}")
            except Exception as e:
                logger.warning(f"[ISIS START] Failed to update ISIS status in database: {e}")
            
            return True
        else:
            logger.error(f"[ISIS START] Failed to start ISIS for {device_name}: exit_code={exit_code}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"[ISIS START] Timeout starting ISIS for {device_name}")
        return False
    except Exception as e:
        logger.error(f"[ISIS START] Error starting ISIS for {device_name}: {e}")
        return False

def stop_isis_neighbor(device_id: str, device_name: str = None, container_id: str = None, isis_config: Dict[str, Any] = None) -> bool:
    """
    Stop ISIS on a device by removing ISIS configuration.
    Uses FRRDockerManager for consistency with configure_isis_neighbor.

    v0.5.417 (audit stream-FF8): public wrapper acquires the per-
    device lock, then dispatches to `_stop_isis_neighbor_locked`.

    Args:
        device_id: Device identifier
        device_name: Device name (optional, will be looked up if not provided)
        container_id: Docker container ID (optional, for backward compatibility)
        isis_config: ISIS configuration (optional)

    Returns:
        True if successful, False otherwise
    """
    with _isis_device_lock(device_id):
        return _stop_isis_neighbor_locked(device_id, device_name, container_id, isis_config)


def _stop_isis_neighbor_locked(device_id: str, device_name: str = None, container_id: str = None, isis_config: Dict[str, Any] = None) -> bool:
    """Locked body of stop_isis_neighbor. Never call directly — go
    through `stop_isis_neighbor` so the per-device lock is held for
    the whole vtysh session."""
    try:
        from utils.frr_docker import FRRDockerManager
        from utils.device_database import DeviceDatabase
        
        logger.info(f"[ISIS STOP] Stopping ISIS for device {device_name} ({device_id})")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        
        # Try to get container - if not found, skip ISIS cleanup and just update database
        try:
            container = frr_manager.client.containers.get(container_name)
        except docker.errors.NotFound:
            logger.info(f"[ISIS STOP] Container {container_name} not found (already removed), skipping vtysh commands and updating database only")
            # Just update database to clear ISIS status
            try:
                device_db = DeviceDatabase()
                update_data = {
                    'isis_running': False,
                    'isis_state': 'Down',
                    'isis_established': False,
                    'isis_neighbors': None,
                    'isis_areas': None,
                    'isis_system_id': None,
                    'isis_net': None,
                    'isis_uptime': None,
                    'last_isis_check': datetime.now(timezone.utc).isoformat()
                }
                device_db.update_device(device_id, update_data)
                logger.info(f"[ISIS STOP] Updated ISIS status in database (container already removed)")
            except Exception as db_error:
                logger.warning(f"[ISIS STOP] Failed to update database: {db_error}")
            return True  # Container already removed, consider ISIS cleanup complete
        
        # Normalize isis_config if provided as JSON string
        if isinstance(isis_config, str):
            try:
                parsed = json.loads(isis_config)
                # Handle double-encoded JSON strings
                if isinstance(parsed, str):
                    try:
                        parsed = json.loads(parsed)
                    except Exception:
                        pass
                isis_config = parsed if isinstance(parsed, dict) else None
            except Exception:
                isis_config = None

        # Get ISIS config and device info from database if not provided
        if not isis_config:
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            if device_data:
                isis_config_str = device_data.get("isis_config") or device_data.get("is_is_config")
                if isis_config_str:
                    if isinstance(isis_config_str, str):
                        try:
                            parsed = json.loads(isis_config_str)
                            if isinstance(parsed, str):
                                try:
                                    parsed = json.loads(parsed)
                                except Exception:
                                    pass
                            isis_config = parsed if isinstance(parsed, dict) else None
                        except Exception:
                            isis_config = None
                    else:
                        isis_config = isis_config_str
        
        # Get interface and net from config if available
        interface = (isis_config or {}).get("interface", None)
        level = (isis_config or {}).get("level", "Level-2")

        # v0.5.417 (audit stream-FF2): probe VRF fail-closed. Same
        # rationale as start/configure — writing `no router isis`
        # into the wrong VRF is worse than a loud failure.
        try:
            _vrf_suffix = _isis_vrf_suffix(device_id)
        except IsisVrfProbeError as _vrf_exc:
            logger.error(f"[ISIS STOP] {_vrf_exc}")
            return False

        # Build a list of interfaces to clean: configured interface and VLAN from device record
        interfaces_to_clean = []
        try:
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            if interface:
                interfaces_to_clean.append(interface)
            if device_data and device_data.get('vlan'):
                vlan_if = f"vlan{device_data.get('vlan')}"
                if vlan_if not in interfaces_to_clean:
                    interfaces_to_clean.append(vlan_if)
        except Exception as _iface_exc:
            logger.warning(f"[ISIS STOP] iface enumeration failed: {_iface_exc}")

        # v0.5.417 (audit stream-FF7): NEVER fall back to hardcoded
        # `vlan20`. If we have no interfaces to clean but we still
        # want the router-level teardown (FF4), proceed with the
        # router-level cleanup alone — that at least stops the
        # isisd instance so it doesn't keep originating LSPs.
        if not interfaces_to_clean:
            logger.warning(
                f"[ISIS STOP] No interfaces resolved for device "
                f"{device_name} ({device_id}); router-level teardown "
                f"only (v0.5.417 FF7 — no hardcoded `vlan20`)."
            )

        # Convert level to FRR format for removal
        level_map = {
            "Level-1": "level-1-only",
            "Level-2": "level-2-only",
            "Level-1-2": "level-1-2"
        }
        frr_level = level_map.get(level, "level-2-only")

        # Build ISIS removal commands - remove from interfaces first, then router
        vtysh_commands = [
            "configure terminal",
        ]
        # Remove from all target interfaces
        for iface in interfaces_to_clean:
            vtysh_commands.extend([
                f"interface {iface}",
                "no ip router isis CORE",
                "no ipv6 router isis CORE",
                "no isis network point-to-point",
                "exit",
            ])
        # v0.5.417 (audit stream-FF4): actually stop ISIS.
        # Pre-fix `stop_isis_neighbor` only stripped the per-interface
        # `ip router isis CORE` lines and DELIBERATELY left `router
        # isis CORE`, `is-type`, and `net` intact — meaning isisd kept
        # running with a NET, kept originating LSPs, and any container
        # restart brought ISIS back up on any interface that had ever
        # been enabled and never explicitly cleared. Same class as
        # v0.5.403 BGP T3 and v0.5.416 OSPF EE3. Issue the full
        # `no router isis CORE` (with VRF suffix) so the instance is
        # actually torn down.
        vtysh_commands.append(f"no router isis CORE{_vrf_suffix}")
        # Persist so container restart doesn't resurrect the config.
        vtysh_commands.extend([
            "end",
            "write memory",
        ])

        # Execute commands using here document
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logger.info(f"[ISIS STOP] Executing ISIS removal commands on container {container_name}")
        logger.debug(f"[ISIS STOP] Commands: {vtysh_commands}")

        result = container.exec_run(["bash", "-c", exec_cmd])
        logger.info(f"[ISIS STOP] Command exit code: {result.exit_code}")
        _stop_output = result.output.decode() if isinstance(result.output, (bytes, bytearray)) else str(result.output)
        logger.info(f"[ISIS STOP] Command output: {_stop_output}")

        if result.exit_code != 0:
            logger.error(f"[ISIS STOP] Command failed: {_stop_output}")
            return False

        # v0.5.417 (audit stream-FF3): vtysh returns rc=0 even when
        # it rejects individual lines; scan output for `%` markers.
        if _vtysh_output_has_error(_stop_output):
            logger.error(
                f"[ISIS STOP] vtysh output for {device_name} contained "
                f"error markers despite rc=0; refusing to report success. "
                f"Output:\n{_stop_output}"
            )
            return False
            
        # Update database with ISIS status - clear ISIS config and status
        try:
            device_db = DeviceDatabase()
            update_data = {
                'isis_running': False,
                'isis_state': 'Down',
                'isis_established': False,
                'isis_neighbors': None,
                'isis_areas': None,
                'isis_system_id': None,
                'isis_net': None,
                'isis_uptime': None,
                'last_isis_check': datetime.now(timezone.utc).isoformat(),
                'isis_manual_override': False,
                'isis_manual_override_time': None
            }
            device_db.update_device(device_id, update_data)
            logger.info(f"[ISIS STOP] Updated ISIS status in database for device {device_name}")
            logger.info(f"[ISIS STOP] Cleared ISIS status fields in database for device {device_name}")
        except Exception as e:
            logger.warning(f"[ISIS STOP] Failed to update ISIS status in database: {e}")
        
        logger.info(f"[ISIS STOP] ✅ Successfully stopped ISIS for {device_name}")
        return True
            
    except Exception as e:
        logger.error(f"[ISIS STOP] Error stopping ISIS: {e}")
        import traceback
        logger.error(f"[ISIS STOP] Traceback: {traceback.format_exc()}")
        return False

def get_isis_neighbor_uptime(container_id: str, neighbor_system_id: str) -> Optional[str]:
    """
    Get ISIS neighbor uptime from FRR container.
    
    Args:
        container_id: Docker container ID
        neighbor_system_id: ISIS neighbor system ID
        
    Returns:
        Uptime string or None if not found
    """
    try:
        import subprocess
        
        # Get ISIS neighbor details
        cmd = f"docker exec {container_id} vtysh -c 'sh isis nei det json'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and result.stdout.strip():
            try:
                neighbor_data = json.loads(result.stdout.strip())
                
                areas = neighbor_data.get("areas", [])
                for area in areas:
                    circuits = area.get("circuits", [])
                    for circuit in circuits:
                        adj_info = circuit.get("adj", "")
                        if adj_info == neighbor_system_id:
                            interface_info = circuit.get("interface", {})
                            return interface_info.get("last-ago", "")
                            
            except json.JSONDecodeError:
                pass
        
        return None
        
    except Exception as e:
        logger.warning(f"[ISIS] Error getting neighbor uptime: {e}")
        return None

