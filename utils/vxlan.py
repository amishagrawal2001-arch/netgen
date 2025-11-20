"""
VXLAN lifecycle utilities for OSTG.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import ipaddress
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def normalize_config(raw_config: Any) -> Dict[str, Any]:
    """Return a normalized VXLAN configuration dictionary."""
    if not raw_config:
        return {}

    config: Dict[str, Any]
    try:
        if isinstance(raw_config, str):
            config = json.loads(raw_config) if raw_config else {}
            if isinstance(config, str):
                config = json.loads(config)
        elif isinstance(raw_config, dict):
            config = dict(raw_config)
        else:
            config = {}
    except (json.JSONDecodeError, TypeError):
        logger.debug("[VXLAN] Failed to parse raw config %s", raw_config)
        config = {}

    if not isinstance(config, dict):
        return {}

    def _clean(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    vni = config.get("vni") or config.get("vxlan_id")
    try:
        config["vni"] = int(vni) if vni is not None else None
    except (TypeError, ValueError):
        config["vni"] = None

    remote_values = (
        config.get("remote_peers")
        or config.get("remote_endpoints")
        or config.get("remote")
        or []
    )
    remote_peers: List[str] = []
    if isinstance(remote_values, str):
        remote_peers = [token.strip() for token in remote_values.replace(";", ",").split(",") if token.strip()]
    elif isinstance(remote_values, (list, tuple, set)):
        remote_peers = [_clean(token) for token in remote_values if _clean(token)]
    elif remote_values:
        token = _clean(remote_values)
        if token:
            remote_peers = [token]
    config["remote_peers"] = remote_peers

    config["local_ip"] = _clean(config.get("local_ip") or config.get("vxlan_local_ip") or config.get("source_ip"))
    config["underlay_interface"] = _clean(config.get("underlay_interface") or config.get("vxlan_underlay") or config.get("interface"))
    config["overlay_interface"] = _clean(config.get("overlay_interface") or config.get("vxlan_overlay"))
    config["vxlan_interface"] = _clean(config.get("vxlan_interface"))
    
    # Bridge SVI IP/subnet configuration (separate from VTEP/loopback IP)
    # This allows bridge to use a different subnet than the loopback/VTEP IPs
    config["bridge_svi_ip"] = _clean(config.get("bridge_svi_ip") or config.get("vxlan_bridge_svi_ip") or config.get("bridge_ip"))
    config["bridge_svi_subnet"] = _clean(config.get("bridge_svi_subnet") or config.get("vxlan_bridge_subnet") or config.get("bridge_subnet"))
    
    # VLAN ID for VLAN-aware VXLAN (maps VLAN to VNI)
    vlan_id = config.get("vlan_id") or config.get("vxlan_vlan_id")
    try:
        config["vlan_id"] = int(vlan_id) if vlan_id is not None and str(vlan_id).strip() else None
    except (TypeError, ValueError):
        config["vlan_id"] = None

    udp_port = config.get("udp_port") or config.get("vxlan_udp_port")
    try:
        config["udp_port"] = int(udp_port) if udp_port is not None else 4789
    except (TypeError, ValueError):
        config["udp_port"] = 4789

    config["enabled"] = bool(config.get("enabled", True) and config.get("vni"))
    return config


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Return a list of validation errors for the VXLAN config."""
    errors: List[str] = []
    if not config:
        return errors
    if not config.get("vni"):
        errors.append("VXLAN VNI is required")
    if not config.get("local_ip"):
        errors.append("VXLAN local endpoint (source IP) is required")
    remote_peers = config.get("remote_peers") or []
    if not remote_peers:
        errors.append("At least one remote VXLAN endpoint is required")
    if not config.get("underlay_interface"):
        errors.append("Underlay interface is required for VXLAN")
    return errors


def _remote_in_local_subnet(local_ip: str, remote_ip: str, prefix_len: Any) -> bool:
    """Return True if remote_ip resides in the same subnet as local_ip/prefix."""
    try:
        prefix = int(prefix_len)
        network = ipaddress.ip_network(f"{local_ip}/{prefix}", strict=False)
        return ipaddress.ip_address(remote_ip) in network
    except Exception:
        return False


def ensure_vxlan_interface(
    device_id: str,
    device_name: str,
    vxlan_config: Dict[str, Any],
    *,
    container_name: Optional[str] = None,
    frr_manager: Any = None,
) -> Dict[str, Any]:
    """Create or update the VXLAN interface (via FRR when available, otherwise on the host)."""
    config = normalize_config(vxlan_config)
    if not config or not config.get("enabled"):
        return {"success": False, "error": "VXLAN disabled"}

    errors = validate_config(config)
    if errors:
        return {"success": False, "error": errors[0]}

    vni = config["vni"]
    remote_peers = config.get("remote_peers") or []
    local_ip = config.get("local_ip")
    underlay = config.get("underlay_interface")
    udp_port = config.get("udp_port", 4789)

    if not remote_peers:
        return {"success": False, "error": "At least one remote endpoint is required"}
    if not local_ip:
        return {"success": False, "error": "Local endpoint is required"}
    if not underlay:
        return {"success": False, "error": "Underlay interface is required"}

    if container_name and frr_manager:
        # Configure inside container using iproute2 (FRR 10 no longer exposes 'vxlan id' under interface)
        ifname_seed = device_id.replace("-", "")
        vxlan_iface = config.get("vxlan_interface") or f"vx{vni}-{ifname_seed[:6]}"
        if len(vxlan_iface) > 15:
            vxlan_iface = vxlan_iface[:15]
        config["vxlan_interface"] = vxlan_iface
        remote_ip = remote_peers[0]
        try:
            _ensure_vxlan_in_container_iproute(
                container_name=container_name,
                frr_manager=frr_manager,
                iface=vxlan_iface,
                vni=vni,
                local_ip=local_ip,
                remote_ip=remote_ip,
                underlay=underlay,
                udp_port=udp_port,
                vxlan_config=config,  # Pass full config for VLAN-aware support
            )
            logger.info(
                "[VXLAN] Configured VXLAN %s in container %s (vni=%s, remote=%s, underlay=%s)",
                vxlan_iface, container_name, vni, remote_ip, underlay
            )
            return {"success": True, "interface": vxlan_iface, "config": config}
        except Exception as exc:
            msg = str(exc)
            # Automatic fallback: If kernel/container reports an existing VXLAN with same VNI,
            # try host-level ensure instead of failing outright.
            if "A VXLAN device with the specified VNI already exists" in msg or "File exists" in msg:
                logger.warning("[VXLAN] Container reported existing VXLAN VNI, falling back to host ensure: %s", msg)
                # continue to host fallback below
            else:
                logger.error("[VXLAN] Failed to configure VXLAN in container: %s", msg)
                return {"success": False, "error": msg}

    # Host-level fallback (legacy path)
    ifname_seed = device_id.replace("-", "")
    default_iface = f"vx{vni}-{ifname_seed[:6]}"
    vxlan_iface = config.get("vxlan_interface") or default_iface
    if len(vxlan_iface) > 15:
        vxlan_iface = vxlan_iface[:15]
        logger.debug("[VXLAN] Truncated interface name to %s (IFNAMSIZ limit)", vxlan_iface)
    config["vxlan_interface"] = vxlan_iface
    remote_ip = remote_peers[0]

    try:
        if not _interface_exists(vxlan_iface):
            base_cmd = [
                "ip",
                "link",
                "add",
                vxlan_iface,
                "type",
                "vxlan",
                "id",
                str(vni),
                "local",
                local_ip,
                "remote",
                remote_ip,
                "dev",
                underlay,
                "dstport",
                str(udp_port),
            ]

            def _add_iface(cmd: List[str]) -> bool:
                try:
                    _run(cmd)
                    return True
                except subprocess.CalledProcessError as exc_inner:
                    stderr_inner = (exc_inner.stderr or "").strip()
                    if "File exists" in stderr_inner:
                        logger.warning("[VXLAN] %s already exists, deleting stray interface and retrying", vxlan_iface)
                        _safe_delete_interface(vxlan_iface)
                        _run(cmd)
                        return True
                    if "A VXLAN device with the specified VNI already exists" in stderr_inner:
                        conflicts = _find_vxlan_interfaces_by_vni(vni, local_ip, remote_ip, underlay, udp_port)
                        if not conflicts:
                            logger.warning(
                                "[VXLAN] Kernel reported existing VXLAN for vni=%s but none detected via ip link",
                                vni,
                            )
                        for conflict_iface in conflicts:
                            if conflict_iface == vxlan_iface:
                                logger.warning(
                                    "[VXLAN] Removing stale VXLAN interface %s before recreation", conflict_iface
                                )
                            else:
                                logger.warning(
                                    "[VXLAN] Removing conflicting VXLAN interface %s (same vni=%s)",
                                    conflict_iface,
                                    vni,
                                )
                            _safe_delete_interface(conflict_iface)
                        _run(cmd)
                        return True
                    raise

            # CRITICAL: Do NOT use 'nolearning' flag - MAC learning is required for L2 VNI detection
            # FRR needs to see MAC addresses learned on the bridge to recognize the VNI as L2
            # and generate Type-2 EVPN routes. The 'nolearning' flag prevents this.
            try:
                # Try without nolearning first (enable MAC learning)
                _add_iface(base_cmd)
                logger.info("[VXLAN] Created VXLAN interface %s with MAC learning enabled (required for L2 VNI)", vxlan_iface)
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or "").strip()
                logger.error("[VXLAN] Failed to add interface %s: %s", vxlan_iface, stderr or exc)
                raise

            logger.info(
                "[VXLAN] Created interface %s (vni=%s, remote=%s, underlay=%s)",
                vxlan_iface,
                vni,
                remote_ip,
                underlay,
            )

        _run(["ip", "link", "set", vxlan_iface, "up"])
        logger.info("[VXLAN] Interface %s is up", vxlan_iface)
        return {"success": True, "interface": vxlan_iface, "config": config}
    except subprocess.CalledProcessError as exc:
        logger.error("[VXLAN] Failed to ensure interface %s: %s", vxlan_iface, exc)
        return {"success": False, "error": exc.stderr.strip() if exc.stderr else str(exc)}
    except Exception as exc:
        logger.error("[VXLAN] Unexpected error ensuring %s: %s", vxlan_iface, exc, exc_info=True)
        return {"success": False, "error": str(exc)}


def tear_down_vxlan_interface(
    device_id: str,
    vxlan_config: Dict[str, Any],
    *,
    container_name: Optional[str] = None,
    frr_manager: Any = None,
) -> bool:
    """Remove the VXLAN interface, bridge, associated veth interfaces, and VLAN-to-VNI mapping."""
    config = normalize_config(vxlan_config)
    iface = config.get("vxlan_interface")
    vni = config.get("vni")
    vlan_id = config.get("vlan_id")
    
    if not iface and vni:
        iface = f"vxlan{vni}-{device_id[:8]}" if vni else None
    
    # Bridge name format: br{vni} (e.g., br5000)
    bridge_name = f"br{vni}" if vni else None
    
    if container_name and frr_manager:
        try:
            # Step 0: Remove VLAN-to-VNI mapping from FRR (if VLAN-aware mode was configured)
            if vlan_id and vni:
                try:
                    _run_vtysh(frr_manager, container_name, [
                        "configure terminal",
                        f"no vxlan vlan {vlan_id} vni {vni}",
                        "exit",
                        "write"
                    ])
                    logger.info("[VXLAN CLEANUP] Removed VLAN-to-VNI mapping: VLAN %s → VNI %s from FRR", vlan_id, vni)
                except Exception as vlan_vni_remove_exc:
                    # Mapping might not exist, which is fine
                    error_msg = str(vlan_vni_remove_exc).lower()
                    if "not found" in error_msg or "does not exist" in error_msg or "no such" in error_msg:
                        logger.debug("[VXLAN CLEANUP] VLAN-to-VNI mapping VLAN %s → VNI %s does not exist in FRR", vlan_id, vni)
                    else:
                        logger.warning("[VXLAN CLEANUP] Failed to remove VLAN-to-VNI mapping from FRR: %s", vlan_vni_remove_exc)
            
            # Step 1: Remove VXLAN interface from bridge (if attached)
            if iface:
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "nomaster"])
                    logger.debug("[VXLAN CLEANUP] Removed VXLAN interface %s from bridge", iface)
                except Exception:
                    pass
            
            # Step 2: Remove all veth interfaces from bridge
            # Find all veth interfaces attached to the bridge
            if bridge_name:
                try:
                    # Get list of interfaces in the bridge using bridge command
                    result = frr_manager.client.containers.get(container_name).exec_run(
                        ["bridge", "link", "show", "master", bridge_name]
                    )
                    veth_interfaces = []
                    if result.exit_code == 0:
                        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
                        # Parse bridge link output to find veth interfaces
                        import re
                        for line in output.split('\n'):
                            if 'veth' in line.lower():
                                # Extract interface name (format: "NNN: vethA@vethB: ..." or "vethA@vethB")
                                match = re.search(r':\s+([^:@\s]+)', line) or re.search(r'\b(veth[^:@\s]+)', line)
                                if match:
                                    veth_iface = match.group(1)
                                    # Extract base name if it's in format "vethA@vethB"
                                    if '@' in veth_iface:
                                        veth_iface = veth_iface.split('@')[0]
                                    if veth_iface not in veth_interfaces:
                                        veth_interfaces.append(veth_iface)
                    
                    # Also check using ip link show master
                    result2 = frr_manager.client.containers.get(container_name).exec_run(
                        ["ip", "link", "show", "master", bridge_name]
                    )
                    if result2.exit_code == 0:
                        output2 = result2.output.decode("utf-8", errors="ignore") if isinstance(result2.output, bytes) else str(result2.output)
                        import re
                        for line in output2.split('\n'):
                            if ':' in line and 'veth' in line.lower():
                                # Extract interface name (format: "NNN: ifname@peer: ...")
                                match = re.search(r':\s+([^:@\s]+)', line)
                                if match:
                                    veth_iface = match.group(1)
                                    # Extract base name if it's in format "vethA@vethB"
                                    if '@' in veth_iface:
                                        veth_iface = veth_iface.split('@')[0]
                                    if veth_iface not in veth_interfaces:
                                        veth_interfaces.append(veth_iface)
                    
                    # Remove all found veth interfaces
                    for veth_iface in veth_interfaces:
                        try:
                            # Remove from bridge first
                            _container_ip(frr_manager, container_name, ["ip", "link", "set", veth_iface, "nomaster"])
                            logger.debug("[VXLAN CLEANUP] Removed veth interface %s from bridge", veth_iface)
                            # Delete the veth interface
                            _container_ip(frr_manager, container_name, ["ip", "link", "del", veth_iface])
                            logger.info("[VXLAN CLEANUP] Deleted veth interface %s", veth_iface)
                        except Exception as veth_exc:
                            error_msg = str(veth_exc).lower()
                            if "cannot find device" in error_msg or "does not exist" in error_msg:
                                logger.debug("[VXLAN CLEANUP] Veth interface %s already removed", veth_iface)
                            else:
                                logger.debug("[VXLAN CLEANUP] Failed to remove veth %s: %s", veth_iface, veth_exc)
                except Exception as veth_scan_exc:
                    logger.debug("[VXLAN CLEANUP] Failed to scan for veth interfaces: %s", veth_scan_exc)
            
            # Step 3: Remove VXLAN interface
            if iface:
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "down"])
                except Exception:
                    pass
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "del", iface])
                    logger.info("[VXLAN CLEANUP] Removed container VXLAN interface %s (%s)", iface, container_name)
                except Exception as exc:
                    logger.warning("[VXLAN CLEANUP] Failed to remove container VXLAN interface %s: %s", iface, exc)
            
            # Step 4: Remove bridge (this will fail if there are still interfaces attached, which is fine)
            if bridge_name:
                try:
                    # Check if bridge exists first
                    bridge_exists = _container_ip_exists(frr_manager, container_name, bridge_name)
                    if not bridge_exists:
                        logger.debug("[VXLAN CLEANUP] Bridge %s does not exist, skipping removal", bridge_name)
                    else:
                        # First, bring bridge down
                        try:
                            _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "down"])
                            logger.debug("[VXLAN CLEANUP] Brought bridge %s down", bridge_name)
                        except Exception as down_exc:
                            logger.debug("[VXLAN CLEANUP] Failed to bring bridge %s down: %s", bridge_name, down_exc)
                        
                        # Remove all remaining interfaces from bridge (safety check)
                        try:
                            result = frr_manager.client.containers.get(container_name).exec_run(
                                ["ip", "link", "show", "master", bridge_name]
                            )
                            if result.exit_code == 0:
                                output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
                                import re
                                for line in output.split('\n'):
                                    if ':' in line:
                                        match = re.search(r':\s+([^:@\s]+)', line)
                                        if match:
                                            remaining_iface = match.group(1)
                                            if remaining_iface and remaining_iface != bridge_name:
                                                try:
                                                    _container_ip(frr_manager, container_name, ["ip", "link", "set", remaining_iface, "nomaster"])
                                                    logger.debug("[VXLAN CLEANUP] Removed remaining interface %s from bridge", remaining_iface)
                                                except Exception:
                                                    pass
                        except Exception:
                            pass
                        
                        # Then delete the bridge
                        try:
                            _container_ip(frr_manager, container_name, ["ip", "link", "del", bridge_name])
                            logger.info("[VXLAN CLEANUP] ✅ Successfully removed bridge %s from container %s", bridge_name, container_name)
                            
                            # Verify bridge was removed
                            import time
                            time.sleep(0.5)  # Brief wait for kernel to process
                            if not _container_ip_exists(frr_manager, container_name, bridge_name):
                                logger.info("[VXLAN CLEANUP] ✅ Verified bridge %s was removed", bridge_name)
                            else:
                                logger.warning("[VXLAN CLEANUP] ⚠️ Bridge %s still exists after deletion attempt", bridge_name)
                        except Exception as del_exc:
                            error_msg = str(del_exc).lower()
                            if "cannot find device" in error_msg or "does not exist" in error_msg:
                                logger.debug("[VXLAN CLEANUP] Bridge %s does not exist (already removed)", bridge_name)
                            elif "operation not supported" in error_msg or "device is busy" in error_msg or "resource busy" in error_msg:
                                logger.warning("[VXLAN CLEANUP] ⚠️ Bridge %s still has interfaces attached or is busy, cannot delete: %s", bridge_name, error_msg)
                                # Try to force remove by removing all interfaces again
                                try:
                                    result = frr_manager.client.containers.get(container_name).exec_run(
                                        ["bridge", "link", "show", "master", bridge_name]
                                    )
                                    if result.exit_code == 0:
                                        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
                                        logger.warning("[VXLAN CLEANUP] Remaining interfaces on bridge %s: %s", bridge_name, output[:200])
                                except Exception:
                                    pass
                            else:
                                logger.warning("[VXLAN CLEANUP] ❌ Failed to remove bridge %s: %s", bridge_name, del_exc)
                except Exception as bridge_exc:
                    logger.warning("[VXLAN CLEANUP] ❌ Exception while removing bridge %s: %s", bridge_name, bridge_exc)
            
            # Step 5: Remove bridge SVI configuration from FRR (if it exists)
            if bridge_name:
                try:
                    _run_vtysh(frr_manager, container_name, [
                        "configure terminal",
                        f"interface {bridge_name}",
                        "shutdown",
                        "no ip address",
                        "exit",
                        "end"
                    ])
                    logger.info("[VXLAN CLEANUP] Removed bridge %s SVI configuration from FRR", bridge_name)
                except Exception as svi_remove_exc:
                    # SVI might not exist or interface might not be configured, which is fine
                    error_msg = str(svi_remove_exc).lower()
                    if "not found" in error_msg or "does not exist" in error_msg or "no such" in error_msg:
                        logger.debug("[VXLAN CLEANUP] Bridge %s SVI configuration does not exist in FRR", bridge_name)
                    else:
                        logger.debug("[VXLAN CLEANUP] Failed to remove bridge %s SVI from FRR (non-critical): %s", bridge_name, svi_remove_exc)
            
            return True
        except Exception as exc:
            logger.warning("[VXLAN CLEANUP] Failed to tear down VXLAN in container %s: %s", container_name, exc)
            return False

    # Host-level cleanup (fallback)
    if not _interface_exists(iface):
        return False
    try:
        _run(["ip", "link", "set", iface, "down"])
        _run(["ip", "link", "del", iface])
        logger.info("[VXLAN CLEANUP] Removed interface %s", iface)
        
        # Also try to remove bridge if it exists
        if bridge_name and _interface_exists(bridge_name):
            try:
                _run(["ip", "link", "set", bridge_name, "down"])
                _run(["ip", "link", "del", bridge_name])
                logger.info("[VXLAN CLEANUP] Removed bridge %s", bridge_name)
            except Exception as bridge_exc:
                logger.debug("[VXLAN CLEANUP] Failed to remove bridge %s: %s", bridge_name, bridge_exc)
        
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("[VXLAN CLEANUP] Failed to remove interface %s: %s", iface, exc)
        return False
    except Exception as exc:
        logger.warning("[VXLAN CLEANUP] Unexpected error removing %s: %s", iface, exc)
        return False


def _ensure_vxlan_in_container_iproute(
    *,
    container_name: str,
    frr_manager: Any,
    iface: str,
    vni: int,
    local_ip: str,
    remote_ip: str,
    underlay: str,
    udp_port: int,
    vxlan_config: Optional[Dict[str, Any]] = None,
) -> None:
    # Clean up old bridge and veth interfaces before creating new ones
    # This ensures a clean state when re-applying device configuration
    bridge_name = f"br{vni}"
    veth_name = f"veth{vni}"
    veth_peer = f"{veth_name}-peer"
    
    try:
        # Clean up old veth interfaces
        try:
            _container_ip(frr_manager, container_name, ["ip", "link", "set", veth_name, "down"])
        except Exception:
            pass  # Interface might not exist
        try:
            _container_ip(frr_manager, container_name, ["ip", "link", "del", veth_name])
            logger.debug("[VXLAN] Cleaned up old veth interface %s", veth_name)
        except Exception:
            pass  # Interface might not exist or already deleted
        
        # Clean up old bridge (but keep it if it exists and is configured correctly)
        # We'll check if bridge exists and has correct configuration before deleting
        try:
            bridge_exists = _container_ip_exists(frr_manager, container_name, bridge_name)
            if bridge_exists:
                # Check if bridge has the VXLAN interface attached - if not, it might be orphaned
                try:
                    container = frr_manager.client.containers.get(container_name)
                    bridge_show = container.exec_run(["ip", "link", "show", "master", bridge_name])
                    bridge_output = bridge_show.output.decode("utf-8", errors="ignore") if isinstance(bridge_show.output, bytes) else str(bridge_show.output)
                    # If bridge exists but VXLAN interface is not attached, we might need to clean it up
                    # However, we'll be conservative and only clean up if explicitly needed
                    logger.debug("[VXLAN] Bridge %s exists, will verify configuration", bridge_name)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as cleanup_exc:
        logger.debug("[VXLAN] Cleanup of old interfaces failed (non-critical): %s", cleanup_exc)
    
    # Check existence and configuration
    exists = _container_ip_exists(frr_manager, container_name, iface)
    needs_recreation = False
    
    if exists:
        # Check if the interface has a multicast group (required for Type-3 routes)
        # If it only has 'remote', we need to recreate it with 'group'
        try:
            container = frr_manager.client.containers.get(container_name)
            check_result = container.exec_run(["ip", "-d", "link", "show", iface])
            check_output = check_result.output.decode("utf-8", errors="ignore") if isinstance(check_result.output, bytes) else str(check_result.output)
            
            # Find the VXLAN configuration line (contains "vxlan id")
            vxlan_line = None
            for line in check_output.split('\n'):
                if 'vxlan id' in line.lower():
                    vxlan_line = line.lower()
                    break
            
            # Check if VXLAN line has 'group' (multicast) or only 'remote' (point-to-point)
            # Also check for 'nolearning' flag which prevents MAC learning (required for L2 VNI)
            # Note: We check the VXLAN line specifically, not the entire output, because
            # "group" appears elsewhere (e.g., "group default" in interface flags)
            if vxlan_line:
                vxlan_tokens = vxlan_line.split()
                has_group_in_vxlan = ' group ' in vxlan_line or 'group' in vxlan_tokens  # Check for "group" as a VXLAN parameter
                has_remote_in_vxlan = ' remote ' in vxlan_line or 'remote' in vxlan_tokens  # Check for "remote" as a VXLAN parameter
                has_nolearning = ' nolearning ' in vxlan_line or 'nolearning' in vxlan_tokens  # Check for "nolearning" flag
                has_remote_only = has_remote_in_vxlan and not has_group_in_vxlan
                
                if has_remote_only:
                    logger.info("[VXLAN] Interface %s exists but only has 'remote', needs recreation with multicast group for Type-3 routes", iface)
                    needs_recreation = True
                elif has_nolearning:
                    logger.info("[VXLAN] Interface %s exists with 'nolearning' flag, needs recreation with MAC learning enabled for L2 VNI", iface)
                    needs_recreation = True
                elif has_group_in_vxlan:
                    logger.debug("[VXLAN] Interface %s already has multicast group configured", iface)
            else:
                # Couldn't find VXLAN line, assume it needs recreation to be safe
                logger.warning("[VXLAN] Could not find VXLAN configuration line for interface %s, assuming recreation needed", iface)
                needs_recreation = True
        except Exception as check_exc:
            logger.warning("[VXLAN] Failed to check interface configuration: %s", check_exc)
            # If we can't check, assume it needs recreation to be safe
            needs_recreation = True
    
    if not exists or needs_recreation:
        if needs_recreation:
            # Delete existing interface to recreate with multicast group
            try:
                logger.info("[VXLAN] Deleting existing interface %s to recreate with multicast group", iface)
                _container_ip(frr_manager, container_name, ["ip", "link", "del", iface])
            except Exception as del_exc:
                logger.warning("[VXLAN] Failed to delete existing interface %s: %s", iface, del_exc)
                # Continue anyway - the create might fail and we'll handle it
        cfg_remote_peers = []
        cfg_multicast_group = None
        if vxlan_config:
            cfg_remote_peers = vxlan_config.get("remote_peers") or []
            cfg_multicast_group = vxlan_config.get("multicast_group") or vxlan_config.get("vxlan_multicast_group")
        # Default behavior: use unicast VXLAN when remote peers are provided (ingress replication).
        # Only fall back to multicast when explicitly requested or when no peers exist (legacy behavior).
        use_multicast = bool(cfg_multicast_group)
        if not cfg_remote_peers and not use_multicast:
            cfg_multicast_group = f"239.0.0.{vni % 255}"
            use_multicast = True
        
        if use_multicast:
            multicast_group = cfg_multicast_group or f"239.0.0.{vni % 255}"
            base_cmd = [
                "ip", "link", "add", iface, "type", "vxlan",
                "id", str(vni),
                "local", local_ip,
                "group", multicast_group,
                "dev", underlay,
                "dstport", str(udp_port),
            ]
            logger.debug("[VXLAN] Creating %s with multicast group %s (VNI %s)", iface, multicast_group, vni)
        else:
            base_cmd = [
                "ip",
                "link",
                "add",
                iface,
                "type",
                "vxlan",
                "id",
                str(vni),
                "local",
                local_ip,
                "remote",
                remote_ip,
                "dev",
                underlay,
                "dstport",
                str(udp_port),
            ]
            logger.debug("[VXLAN] Creating %s in unicast mode (remote %s, VNI %s)", iface, remote_ip, vni)
        
        try:
            # CRITICAL: Do NOT use 'nolearning' flag - MAC learning is required
            _container_ip(frr_manager, container_name, base_cmd)
        except Exception as exc:
            msg = str(exc)
            if use_multicast and ("Attribute failed policy validation" in msg or "Operation not supported" in msg):
                # Retry once (some kernels momentarily reject multicast config)
                try:
                    _container_ip(frr_manager, container_name, base_cmd)
                except Exception as exc2:
                    logger.warning("[VXLAN] Multicast group configuration failed, falling back to remote-only: %s", exc2)
                    base_cmd_remote = [
                        "ip",
                        "link",
                        "add",
                        iface,
                        "type",
                        "vxlan",
                        "id",
                        str(vni),
                        "local",
                        local_ip,
                        "remote",
                        remote_ip,
                        "dev",
                        underlay,
                        "dstport",
                        str(udp_port),
                    ]
                    _container_ip(frr_manager, container_name, base_cmd_remote)
                    use_multicast = False
            elif "File exists" in msg or "A VXLAN device with the specified VNI already exists" in msg:
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "del", iface])
                except Exception:
                    pass
                try:
                    _container_ip(frr_manager, container_name, base_cmd)
                except Exception as exc2:
                    if use_multicast:
                        logger.warning("[VXLAN] Multicast group configuration failed after recreate, falling back to remote-only: %s", exc2)
                        base_cmd_remote = [
                            "ip",
                            "link",
                            "add",
                            iface,
                            "type",
                            "vxlan",
                            "id",
                            str(vni),
                            "local",
                            local_ip,
                            "remote",
                            remote_ip,
                            "dev",
                            underlay,
                            "dstport",
                            str(udp_port),
                        ]
                        _container_ip(frr_manager, container_name, base_cmd_remote)
                        use_multicast = False
                    else:
                        raise
            else:
                raise
    # Bring up
    _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "up"])
    
    # Get VLAN ID from config if provided (for VLAN-aware mode)
    vlan_id = None
    if vxlan_config:
        vlan_id = vxlan_config.get("vlan_id")
        try:
            vlan_id = int(vlan_id) if vlan_id is not None and str(vlan_id).strip() else None
        except (TypeError, ValueError):
            vlan_id = None
    
    # Configure VXLAN interface in FRR so zebra knows about it
    # This is required for advertise-all-vni to work in BGP EVPN
    # FRR 10.0 doesn't support 'vxlan id' under interface, but we can still configure the interface
    # so zebra tracks it, which allows EVPN to advertise the VNI
    # For EVPN to generate routes with advertise-svi-ip, we need to associate the VXLAN interface
    # with a bridge and configure the bridge with the VNI
    try:
        # Configure the VXLAN interface in FRR
        _run_vtysh(frr_manager, container_name, [
            "configure terminal",
            f"interface {iface}",
            "no shutdown",
            "exit",
        ])
        
        # Create a bridge for this VNI if it doesn't exist
        # Bridge name format: br{vni} (e.g., br5000)
        bridge_name = f"br{vni}"
        
        # Get VLAN ID for VLAN-aware mode (if configured)
        # vlan_id is already extracted above from vxlan_config
        
        # Check if bridge exists, if not create it
        # Note: Bridge creation via ip link is done at kernel level, not via FRR
        # FRR will automatically recognize bridges created via ip link
        try:
            # Clean up old bridge if it exists (bring down and delete)
            # This ensures a clean state when re-applying device configuration
            bridge_exists = _container_ip_exists(frr_manager, container_name, bridge_name)
            if bridge_exists:
                try:
                    # Remove all interfaces from bridge first
                    try:
                        container = frr_manager.client.containers.get(container_name)
                        bridge_links = container.exec_run(["ip", "link", "show", "master", bridge_name])
                        bridge_output = bridge_links.output.decode("utf-8", errors="ignore") if isinstance(bridge_links.output, bytes) else str(bridge_links.output)
                        # Parse and remove interfaces from bridge
                        for line in bridge_output.split('\n'):
                            if ':' in line and 'state' in line:
                                # Extract interface name (format: "NNN: ifname@...")
                                parts = line.split(':')
                                if len(parts) >= 2:
                                    iface_name = parts[1].split('@')[0].strip()
                                    if iface_name and iface_name != bridge_name:
                                        try:
                                            _container_ip(frr_manager, container_name, ["ip", "link", "set", iface_name, "nomaster"])
                                            logger.debug("[VXLAN] Removed interface %s from old bridge %s", iface_name, bridge_name)
                                        except Exception:
                                            pass
                    except Exception:
                        pass
                    
                    # Bring down and delete old bridge
                    try:
                        _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "down"])
                    except Exception:
                        pass
                    try:
                        _container_ip(frr_manager, container_name, ["ip", "link", "del", bridge_name])
                        logger.debug("[VXLAN] Cleaned up old bridge %s", bridge_name)
                    except Exception:
                        pass
                except Exception as cleanup_exc:
                    logger.debug("[VXLAN] Failed to clean up old bridge (non-critical): %s", cleanup_exc)
            
            # Try to create bridge via ip link (if it doesn't exist)
            # Create plain bridge (no VLAN filtering) by default for better compatibility
            # "File exists" errors are expected and can be ignored
            try:
                _container_ip(frr_manager, container_name, ["ip", "link", "add", "name", bridge_name, "type", "bridge"])
                logger.debug("[VXLAN] Created new plain bridge %s (no VLAN filtering)", bridge_name)
            except Exception as create_exc:
                if "File exists" not in str(create_exc):
                    raise
                logger.debug("[VXLAN] Bridge %s already exists", bridge_name)
            
            # Configure VLAN-aware mode ONLY if VLAN ID is explicitly specified and > 0
            # Default: Plain bridge without VLAN filtering (better for simple VXLAN setups)
            # CRITICAL: VLAN filtering must be enabled BEFORE adding VLANs to interfaces
            if vlan_id and vlan_id > 0:
                try:
                    # Enable VLAN filtering (VLAN-aware mode) - MUST be done before adding VLANs
                    # This must be done while bridge is down or just created
                    _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "type", "bridge", "vlan_filtering", "1"])
                    logger.info("[VXLAN] Enabled VLAN-aware mode on bridge %s (VLAN %s → VNI %s)", bridge_name, vlan_id, vni)
                    
                    # Remove default VLAN 1 from bridge (if it exists) before adding the specified VLAN
                    try:
                        _container_ip(frr_manager, container_name, ["bridge", "vlan", "del", "vid", "1", "dev", bridge_name, "self"])
                        logger.debug("[VXLAN] Removed default VLAN 1 from bridge %s", bridge_name)
                    except Exception:
                        # VLAN 1 might not exist, which is fine
                        pass
                    
                    # Add specified VLAN to bridge
                    _container_ip(
                        frr_manager,
                        container_name,
                        [
                            "bridge",
                            "vlan",
                            "add",
                            "vid",
                            str(vlan_id),
                            "dev",
                            bridge_name,
                            "self",
                            "pvid",
                            "untagged",
                        ],
                    )
                    logger.info("[VXLAN] Added VLAN %s to bridge %s (self pvid)", vlan_id, bridge_name)
                except Exception as vlan_exc:
                    logger.warning("[VXLAN] Failed to configure VLAN-aware mode on bridge %s: %s", bridge_name, vlan_exc)
            
            # Set bridge MAC address (consistent MAC for stability)
            # Format: aa:bb:cc:00:{vni_high_byte}:{vni_low_byte}
            # For VNI 5000: aa:bb:cc:00:13:88 (5000 = 0x1388)
            try:
                bridge_mac = f"aa:bb:cc:00:{(vni >> 8) & 0xff:02x}:{vni & 0xff:02x}"
                _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "addr", bridge_mac])
                logger.debug("[VXLAN] Set bridge MAC address %s for %s", bridge_mac, bridge_name)
            except Exception as mac_exc:
                logger.debug("[VXLAN] Could not set bridge MAC address (non-critical): %s", mac_exc)
            
            # Bring bridge up (before configuring VLANs if VLAN-aware mode)
            try:
                _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "up"])
                logger.debug("[VXLAN] Brought bridge %s up", bridge_name)
            except Exception as up_exc:
                logger.debug("[VXLAN] Bridge %s may already be up: %s", bridge_name, up_exc)
            
            # Re-apply VLAN filtering after bridge is up (only if VLAN-aware mode)
            # For plain bridge mode (no VLAN), skip this step
            if vlan_id and vlan_id > 0:
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "set", bridge_name, "type", "bridge", "vlan_filtering", "1"])
                    logger.debug("[VXLAN] Re-enabled VLAN filtering on bridge %s after bringing it up", bridge_name)
                except Exception as vlan_filter_exc:
                    logger.debug("[VXLAN] Could not re-enable VLAN filtering (non-critical): %s", vlan_filter_exc)
            else:
                logger.debug("[VXLAN] Using plain bridge mode (no VLAN filtering) for %s", bridge_name)
            
            logger.debug("[VXLAN] Created/verified bridge %s for VNI %s", bridge_name, vni)
        except Exception as bridge_exc:
            logger.warning("[VXLAN] Failed to create bridge %s: %s", bridge_name, bridge_exc)
        
        # Optionally create a veth pair attached to bridge to trigger MAC learning and route generation
        # This helps ensure Type-2 routes are generated and can be useful for testing
        try:
            veth_name = f"veth{vni}"
            veth_peer = f"{veth_name}-peer"
            # Try to create veth pair (ignore if it already exists)
            try:
                _container_ip(frr_manager, container_name, ["ip", "link", "add", veth_name, "type", "veth", "peer", "name", veth_peer])
                logger.debug("[VXLAN] Created veth pair %s/%s for VNI %s", veth_name, veth_peer, vni)
            except Exception as veth_create_exc:
                if "File exists" not in str(veth_create_exc):
                    logger.debug("[VXLAN] Could not create veth pair (non-critical): %s", veth_create_exc)
            
            # Attach veth to bridge and bring it up
            try:
                _container_ip(frr_manager, container_name, ["ip", "link", "set", veth_name, "master", bridge_name])
                _container_ip(frr_manager, container_name, ["ip", "link", "set", veth_name, "up"])
                _container_ip(frr_manager, container_name, ["ip", "link", "set", veth_peer, "up"])
                
                # Add IP address to veth interface to trigger MAC learning and route generation
                # Use .10 as suffix (e.g., 192.255.0.10/24) to match the bridge subnet
                # This helps generate ARP entries and Type-2 routes
                try:
                    # Derive veth IP from local_ip (VTEP IP) - use .10 as suffix
                    # e.g., if local_ip is 192.255.0.1, use 192.255.0.10/24
                    veth_ip = f"{local_ip.rsplit('.', 1)[0]}.10/24"
                    _container_ip(frr_manager, container_name, ["ip", "addr", "add", veth_ip, "dev", veth_name])
                    logger.debug("[VXLAN] Added IP address %s to veth interface %s", veth_ip, veth_name)
                except Exception as veth_ip_exc:
                    if "File exists" not in str(veth_ip_exc):
                        logger.debug("[VXLAN] Could not add IP address to veth interface (non-critical): %s", veth_ip_exc)
                
                # Configure VLAN on veth interface if VLAN-aware mode is enabled
                if vlan_id and vlan_id > 0:
                    try:
                        # Remove VLAN 1 from veth, then add VLAN 100 as PVID
                        try:
                            _container_ip(frr_manager, container_name, ["bridge", "vlan", "del", "vid", "1", "dev", veth_name, "pvid"])
                        except Exception:
                            pass
                        _container_ip(frr_manager, container_name, ["bridge", "vlan", "add", "vid", str(vlan_id), "dev", veth_name, "pvid", "untagged"])
                        logger.info("[VXLAN] Added VLAN %s as PVID to veth interface %s", vlan_id, veth_name)
                    except Exception as veth_vlan_exc:
                        logger.debug("[VXLAN] Could not configure VLAN on veth interface: %s", veth_vlan_exc)
                
                logger.debug("[VXLAN] Attached veth pair %s/%s to bridge %s", veth_name, veth_peer, bridge_name)
            except Exception as veth_attach_exc:
                logger.debug("[VXLAN] Could not attach veth pair to bridge (non-critical): %s", veth_attach_exc)
        except Exception as veth_exc:
            logger.debug("[VXLAN] Veth pair creation failed (non-critical): %s", veth_exc)
        
        # Add VXLAN interface to bridge via ip link
        try:
            _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "master", bridge_name])
            logger.info("[VXLAN] Added VXLAN interface %s to bridge %s", iface, bridge_name)
            
            # Ensure VXLAN interface is up after adding to bridge (may have been brought down)
            try:
                _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "up"])
                logger.debug("[VXLAN] Brought up VXLAN interface %s after adding to bridge", iface)
            except Exception as up_iface_exc:
                logger.debug("[VXLAN] VXLAN interface %s may already be up: %s", iface, up_iface_exc)
            
            # If VLAN-aware mode, add VLAN to VXLAN interface with PVID
            # This ensures the VXLAN interface uses the correct VLAN as PVID
            if vlan_id and vlan_id > 0:
                try:
                    # Remove default VLAN 1 PVID first, then add VLAN 100 as PVID
                    _container_ip(frr_manager, container_name, ["bridge", "vlan", "del", "vid", "1", "dev", iface, "pvid"])
                    _container_ip(frr_manager, container_name, ["bridge", "vlan", "add", "vid", str(vlan_id), "dev", iface, "pvid", "untagged"])
                    logger.info("[VXLAN] Added VLAN %s as PVID to VXLAN interface %s", vlan_id, iface)
                except Exception as vlan_iface_exc:
                    logger.warning("[VXLAN] Failed to add VLAN %s to VXLAN interface %s: %s", vlan_id, iface, vlan_iface_exc)
        except Exception as bridge_add_exc:
            # Interface might already be in bridge, which is fine
            if "File exists" not in str(bridge_add_exc) and "already" not in str(bridge_add_exc).lower():
                logger.warning("[VXLAN] Failed to add VXLAN interface %s to bridge %s: %s", iface, bridge_name, bridge_add_exc)
            else:
                logger.debug("[VXLAN] VXLAN interface %s may already be in bridge %s", iface, bridge_name)
        
        # Configure bridge in FRR with SVI (Switched Virtual Interface) IP address
        # This is CRITICAL for FRR to recognize the VNI as L2 and generate Type-2 EVPN routes
        # An SVI (IP address on the bridge) is required for L2 VNI detection
        # IMPORTANT: Bridge SVI should use a DIFFERENT subnet from loopback/VTEP IPs
        # This prevents routing conflicts and allows proper separation of overlay/underlay
        try:
            import ipaddress
            
            # Check if bridge SVI IP/subnet is explicitly configured
            bridge_svi_ip = vxlan_config.get("bridge_svi_ip") if vxlan_config else None
            bridge_svi_subnet = vxlan_config.get("bridge_svi_subnet") if vxlan_config else None
            
            if bridge_svi_ip:
                # Use explicitly configured bridge SVI IP
                # If subnet is provided, use it; otherwise derive from IP or use /24
                if bridge_svi_subnet:
                    # Validate subnet format (e.g., "24" or "192.168.100.0/24")
                    try:
                        if '/' in bridge_svi_subnet:
                            svi_ip_str = f"{bridge_svi_ip}/{bridge_svi_subnet.split('/')[-1]}"
                        else:
                            # Assume it's a prefix length
                            svi_ip_str = f"{bridge_svi_ip}/{bridge_svi_subnet}"
                    except Exception:
                        svi_ip_str = f"{bridge_svi_ip}/24"
                else:
                    # Use /24 as default if subnet not specified
                    svi_ip_str = f"{bridge_svi_ip}/24"
                logger.info("[VXLAN] Using explicitly configured bridge SVI IP: %s", svi_ip_str)
            elif bridge_svi_subnet:
                # Only subnet provided, derive IP from subnet
                try:
                    subnet_network = ipaddress.IPv4Network(bridge_svi_subnet, strict=False)
                    # Use .100 as the host IP in the subnet
                    svi_ip = str(subnet_network.network_address + 100)
                    svi_ip_str = f"{svi_ip}/{subnet_network.prefixlen}"
                    logger.info("[VXLAN] Using bridge SVI subnet %s, derived IP: %s", bridge_svi_subnet, svi_ip_str)
                except Exception:
                    # Fallback to default
                    svi_ip_str = "10.0.0.100/24"
                    logger.warning("[VXLAN] Failed to parse bridge_svi_subnet %s, using default: %s", bridge_svi_subnet, svi_ip_str)
            else:
                # No explicit configuration - use a different subnet from loopback/VTEP
                # Default: Use 10.0.0.0/24 subnet for bridge SVI (different from typical loopback subnets)
                # This ensures bridge SVI is in a separate subnet from VTEP/loopback IPs
                try:
                    local_ip_obj = ipaddress.IPv4Address(local_ip)
                    # Check if local_ip is in common loopback ranges
                    if local_ip_obj in ipaddress.IPv4Network("192.255.0.0/16") or \
                       local_ip_obj in ipaddress.IPv4Network("192.168.0.0/16"):
                        # Use 10.0.0.0/24 for bridge SVI (different subnet)
                        svi_ip_str = f"10.0.{vni // 256}.{100 + (vni % 256)}/24"
                    else:
                        # For other ranges, use a derived subnet
                        # Use 10.0.0.0/24 as base, vary based on VNI
                        svi_ip_str = f"10.0.{vni // 256}.{100 + (vni % 256)}/24"
                    logger.info("[VXLAN] Using default bridge SVI subnet (separate from VTEP): %s", svi_ip_str)
                except Exception:
                    # Fallback: use a default subnet different from common loopback ranges
                    svi_ip_str = f"10.0.{vni // 256}.{100 + (vni % 256)}/24"
                    logger.info("[VXLAN] Using fallback bridge SVI IP: %s", svi_ip_str)
            
            # Flush any existing addresses on bridge before adding SVI IP (ensures clean state)
            try:
                _container_ip(frr_manager, container_name, ["ip", "addr", "flush", "dev", bridge_name])
                logger.debug("[VXLAN] Flushed existing addresses on bridge %s", bridge_name)
            except Exception as flush_exc:
                logger.debug("[VXLAN] Could not flush addresses on bridge (non-critical): %s", flush_exc)
            
            # Add IP address at kernel level first (required for FRR to recognize it)
            try:
                _container_ip(frr_manager, container_name, ["ip", "addr", "add", svi_ip_str, "dev", bridge_name])
                logger.debug("[VXLAN] Added IP address %s to bridge %s at kernel level", svi_ip_str, bridge_name)
            except Exception as ip_add_exc:
                if "File exists" not in str(ip_add_exc):
                    logger.warning("[VXLAN] Failed to add IP address to bridge %s at kernel level: %s", bridge_name, ip_add_exc)
            
            # Also configure in FRR so zebra knows about it
            _run_vtysh(frr_manager, container_name, [
                f"interface {bridge_name}",
                "no shutdown",
                f"ip address {svi_ip_str}",
                "exit",
                "end",
                "write"  # CRITICAL: Save configuration so bridge SVI persists
            ])
            
            # Force EVPN daemon to re-evaluate SVI association by toggling advertise-all-vni
            # This is a workaround for FRR issue where EVPN daemon doesn't immediately recognize SVI
            # Try common ASNs (65000 is the default in OSTG)
            try:
                bgp_asns = ["65000", "65001"]  # Try common ASNs
                toggled = False
                for bgp_asn in bgp_asns:
                    try:
                        _run_vtysh(frr_manager, container_name, [
                            "configure terminal",
                            f"router bgp {bgp_asn}",
                            "address-family l2vpn evpn",
                            "no advertise-all-vni",
                            "advertise-all-vni",
                            "exit-address-family",
                            "end"
                        ])
                        logger.debug("[VXLAN] Toggled advertise-all-vni to force EVPN SVI recognition (ASN: %s)", bgp_asn)
                        toggled = True
                        break
                    except Exception:
                        continue  # Try next ASN
                if not toggled:
                    logger.debug("[VXLAN] Could not toggle advertise-all-vni (ASN not found)")
            except Exception as toggle_exc:
                logger.debug("[VXLAN] Failed to toggle advertise-all-vni (non-critical): %s", toggle_exc)
            
            logger.info("[VXLAN] Configured bridge %s as SVI with IP %s for L2 VNI recognition", bridge_name, svi_ip_str)
            logger.info("[VXLAN] VNI %s should now be recognized as L2 by FRR for EVPN route generation", vni)
        except Exception as bridge_config_exc:
            logger.warning("[VXLAN] Failed to configure bridge %s SVI in FRR (non-critical): %s", bridge_name, bridge_config_exc)
        
        # Configure VLAN-to-VNI mapping in FRR if VLAN-aware mode
        if vlan_id and vlan_id > 0:
            try:
                _run_vtysh(frr_manager, container_name, [
                    "configure terminal",
                    f"vxlan vlan {vlan_id} vni {vni}",
                    "exit",
                    "write"
                ])
                logger.info("[VXLAN] Configured VLAN-aware mapping: VLAN %s → VNI %s in FRR", vlan_id, vni)
            except Exception as vlan_vni_exc:
                logger.warning("[VXLAN] Failed to configure VLAN-to-VNI mapping in FRR: %s", vlan_vni_exc)
    except Exception as exc:
        logger.warning("[VXLAN] Failed to configure VXLAN interface in FRR (non-critical): %s", exc)
    
    # Ensure reachability to remote endpoint: check if route exists via routing protocol (OSPF/BGP/etc)
    # Only add static kernel route if no routing protocol route exists
    # This allows OSPF/BGP to manage the route instead of forcing a static kernel route
    try:
        container = frr_manager.client.containers.get(container_name)
        
        # First, check FRR's routing table via vtysh to see if OSPF/BGP/ISIS has the route
        has_protocol_route = False
        try:
            frr_route_result = container.exec_run(["vtysh", "-c", f"show ip route {remote_ip}"])
            frr_route_output = frr_route_result.output.decode("utf-8", errors="ignore") if isinstance(frr_route_result.output, bytes) else str(frr_route_result.output)
            
            # Check if route exists via a routing protocol (not kernel/connected)
            protocol_keywords = ['ospf', 'bgp', 'isis', 'rip', 'eigrp']
            for keyword in protocol_keywords:
                if f'via "{keyword}"' in frr_route_output.lower() or f'Known via "{keyword}"' in frr_route_output:
                    has_protocol_route = True
                    logger.debug("[VXLAN] Route to remote endpoint %s already exists via %s, skipping static route", remote_ip, keyword.upper())
                    break
        except Exception as frr_check_exc:
            logger.debug("[VXLAN] Could not check FRR routing table for %s: %s", remote_ip, frr_check_exc)
        
        # If protocol route exists, remove any kernel route we might have added previously
        if has_protocol_route:
            try:
                # Try to remove kernel route if it exists
                _container_ip(frr_manager, container_name, ["ip", "route", "del", f"{remote_ip}/32"])
                logger.debug("[VXLAN] Removed kernel route to %s to allow %s route to be used", remote_ip, "protocol")
            except Exception:
                pass  # Route might not exist, ignore
        else:
            # No protocol route found, check kernel routing table and add static route if needed
            # Check if kernel route already exists
            route_result = container.exec_run(["ip", "route", "show", remote_ip])
            route_output = route_result.output.decode("utf-8", errors="ignore") if isinstance(route_result.output, bytes) else str(route_result.output)
            
            route_output = route_output.strip()
            local_prefix = vxlan_config.get("local_prefix_len") if vxlan_config else None
            underlay_gateway = vxlan_config.get("underlay_gateway") if vxlan_config else None
            needs_route = True
            if route_output:
                if underlay_gateway and f"via {underlay_gateway}" in route_output:
                    needs_route = False
                elif (not underlay_gateway) and local_prefix and _remote_in_local_subnet(local_ip, remote_ip, local_prefix):
                    needs_route = False
            
            # Only add/replace static route if required
            if needs_route:
                route_iface = vxlan_config.get("underlay_route_interface") if vxlan_config else None
                if not route_iface:
                    route_iface = underlay
                route_cmd = ["ip", "route", "replace", f"{remote_ip}/32"]
                if local_prefix and _remote_in_local_subnet(local_ip, remote_ip, local_prefix):
                    route_cmd.extend(["dev", route_iface])
                elif underlay_gateway:
                    route_cmd.extend(["via", underlay_gateway, "dev", route_iface])
                else:
                    route_cmd.extend(["dev", route_iface])
                try:
                    _container_ip(frr_manager, container_name, route_cmd)
                    logger.debug("[VXLAN] Added host route to remote endpoint %s (%s)", remote_ip, " ".join(route_cmd))
                except Exception as exc:
                    # If provided gateway failed, fall back to default route lookup
                    try:
                        route_result = container.exec_run(["ip", "route", "show", "default"])
                        route_output = route_result.output.decode("utf-8", errors="ignore") if isinstance(route_result.output, bytes) else str(route_result.output)
                        gateway = None
                        for line in route_output.split('\n'):
                            if 'default via' in line:
                                parts = line.split()
                                if 'via' in parts:
                                    idx = parts.index('via')
                                    if idx + 1 < len(parts):
                                        gateway = parts[idx + 1]
                                        break
                        if gateway:
                            _container_ip(frr_manager, container_name, ["ip", "route", "replace", f"{remote_ip}/32", "via", gateway, "dev", route_iface])
                            logger.debug("[VXLAN] Added host route to remote endpoint %s via gateway %s (fallback)", remote_ip, gateway)
                        else:
                            logger.warning("[VXLAN] Could not determine gateway for remote endpoint %s: %s", remote_ip, exc)
                    except Exception as route_exc:
                        logger.warning("[VXLAN] Failed to add route to remote endpoint %s: %s", remote_ip, route_exc)
    except Exception as check_exc:
        logger.warning("[VXLAN] Could not check existing routes for remote endpoint %s: %s", remote_ip, check_exc)
    
    # Configure ARP and FDB entries for remote VXLAN peers to fix INCOMPLETE ARP issues
    # This is required for proper VXLAN traffic forwarding
    # The MAC address should ideally come from EVPN Type-2 routes, but can be configured manually
    try:
        # Get bridge name for this VNI
        bridge_name = f"br{vni}"
        
        # Check if remote peer MAC is provided in config (for manual configuration)
        # Format: {"remote_peers": ["192.168.250.1"], "remote_peer_macs": {"192.168.250.1": "24:5d:92:a7:65:06"}}
        remote_peer_macs = vxlan_config.get("remote_peer_macs", {}) if vxlan_config else {}
        remote_peer_svi_ips = vxlan_config.get("remote_peer_svi_ips", {}) if vxlan_config else {}
        
        # Try to get MAC and SVI IP for this remote peer
        remote_mac = remote_peer_macs.get(remote_ip)
        remote_svi_ip = remote_peer_svi_ips.get(remote_ip)
        
        # If not provided, try to derive remote SVI IP from local SVI IP pattern
        # Pattern: If local SVI is 192.255.0.100, remote SVI is typically 192.255.0.101
        if not remote_svi_ip:
            try:
                import ipaddress
                # Get local SVI IP from bridge (if configured)
                try:
                    container = frr_manager.client.containers.get(container_name)
                    ip_result = container.exec_run(["ip", "addr", "show", bridge_name])
                    ip_output = ip_result.output.decode("utf-8", errors="ignore") if isinstance(ip_result.output, bytes) else str(ip_result.output)
                    # Extract IP from output (e.g., "inet 192.255.0.100/24")
                    for line in ip_output.split('\n'):
                        if 'inet ' in line and bridge_name in line:
                            parts = line.strip().split()
                            if len(parts) >= 2:
                                local_svi_ip_str = parts[1].split('/')[0]  # Remove /24
                                local_svi_obj = ipaddress.IPv4Address(local_svi_ip_str)
                                # Derive remote SVI by incrementing last octet by 1
                                remote_svi_obj = ipaddress.IPv4Address(int(local_svi_obj) + 1)
                                remote_svi_ip = str(remote_svi_obj)
                                logger.info("[VXLAN] Derived remote SVI IP %s from local SVI IP %s", remote_svi_ip, local_svi_ip_str)
                                break
                except Exception as derive_exc:
                    logger.debug("[VXLAN] Could not derive remote SVI IP: %s", derive_exc)
            except Exception as ip_parse_exc:
                logger.debug("[VXLAN] Could not parse IP addresses: %s", ip_parse_exc)
        
        # Try to query EVPN Type-2 routes to extract remote SVI IPs and MACs
        # First, try to get remote SVI IP from EVPN Type-2 MAC/IP routes if not provided
        if not remote_svi_ip:
            try:
                container = frr_manager.client.containers.get(container_name)
                evpn_result = container.exec_run(["vtysh", "-c", "show bgp l2vpn evpn route type macip"])
                evpn_output = evpn_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_result.output, bytes) else str(evpn_result.output)
                
                # Parse EVPN Type-2 MAC/IP routes to find remote SVI IPs
                # Format: [2]:[EthTag]:[48]:[MAC]:[32]:[IP]
                # Example: [2]:[0]:[48]:[aa:bb:cc:00:13:88]:[32]:[20.0.0.100]
                for line in evpn_output.split('\n'):
                    if '[2]:' in line and '[32]:' in line:
                        # Try to extract IP address from Type-2 MAC/IP route
                        parts = line.split()
                        for part in parts:
                            if '[' in part and ']' in part and ':' in part:
                                route_str = part.strip('[]')
                                route_parts = route_str.split(':')
                                
                                # Type-2 route with IP: [2]:[EthTag]:[48]:[MAC]:[32]:[IP]
                                if len(route_parts) >= 12 and route_parts[0] == '2' and route_parts[2] == '48' and route_parts[9] == '32':
                                    try:
                                        # IPv4 address is at positions 10-13
                                        route_ip = '.'.join(route_parts[10:14])
                                        # Extract MAC address (positions 3-8)
                                        mac_octets = route_parts[3:9]
                                        if len(mac_octets) == 6:
                                            route_mac = ':'.join(f"{int(octet, 16):02x}" for octet in mac_octets)
                                            # This is a remote SVI IP (not local)
                                            # Check if it's different from local SVI IP
                                            try:
                                                container = frr_manager.client.containers.get(container_name)
                                                ip_result = container.exec_run(["ip", "addr", "show", bridge_name])
                                                ip_output = ip_result.output.decode("utf-8", errors="ignore") if isinstance(ip_result.output, bytes) else str(ip_result.output)
                                                is_local = False
                                                for ip_line in ip_output.split('\n'):
                                                    if 'inet ' in ip_line and route_ip in ip_line:
                                                        is_local = True
                                                        break
                                                
                                                if not is_local:
                                                    remote_svi_ip = route_ip
                                                    remote_mac = route_mac
                                                    logger.info("[VXLAN] Found remote SVI IP %s with MAC %s from EVPN Type-2 route", remote_svi_ip, remote_mac)
                                                    break
                                            except Exception:
                                                # If we can't check, assume it's remote
                                                remote_svi_ip = route_ip
                                                remote_mac = route_mac
                                                logger.info("[VXLAN] Found remote SVI IP %s with MAC %s from EVPN Type-2 route", remote_svi_ip, remote_mac)
                                                break
                                    except (ValueError, IndexError):
                                        continue
                        if remote_svi_ip:
                            break
            except Exception as evpn_query_exc:
                logger.debug("[VXLAN] Could not query EVPN routes for remote SVI IP: %s", evpn_query_exc)
        
        # Try to query EVPN Type-2 routes for MAC address if we have remote SVI IP but not MAC
        if not remote_mac and remote_svi_ip:
            try:
                # Query EVPN routes to get MAC address for the remote SVI IP
                container = frr_manager.client.containers.get(container_name)
                evpn_result = container.exec_run(["vtysh", "-c", "show bgp l2vpn evpn route type macip"])
                evpn_output = evpn_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_result.output, bytes) else str(evpn_result.output)
                
                # Parse EVPN output to find MAC for remote SVI IP
                # EVPN Type-2 route format: [2]:[EthTag]:[MAClen]:[MAC]:[IPlen]:[IP]
                for line in evpn_output.split('\n'):
                    # Look for lines containing the remote SVI IP
                    if remote_svi_ip.split('/')[0] in line:
                        # Try to extract MAC address from the route
                        parts = line.split()
                        for part in parts:
                            if '[' in part and ']' in part and ':' in part:
                                route_str = part.strip('[]')
                                route_parts = route_str.split(':')
                                
                                # Type-2 route with IP: [2]:[EthTag]:[48]:[MAC]:[32]:[IP]
                                if len(route_parts) >= 12 and route_parts[0] == '2' and route_parts[2] == '48' and route_parts[9] == '32':
                                    try:
                                        # MAC is at positions 3-8 (6 octets)
                                        mac_octets = route_parts[3:9]
                                        if len(mac_octets) == 6:
                                            # Convert hex to MAC format
                                            route_mac = ':'.join(f"{int(octet, 16):02x}" for octet in mac_octets)
                                            # Verify this route is for the remote SVI IP
                                            route_ip = '.'.join(route_parts[10:14])
                                            if route_ip == remote_svi_ip.split('/')[0]:
                                                remote_mac = route_mac
                                                logger.info("[VXLAN] Found MAC %s for remote SVI IP %s from EVPN routes", remote_mac, remote_svi_ip)
                                                break
                                    except (ValueError, IndexError) as parse_exc:
                                        continue
                        if remote_mac:
                            break
            except Exception as evpn_query_exc:
                logger.debug("[VXLAN] Could not query EVPN routes for MAC: %s", evpn_query_exc)
        
        # If MAC still not found, try to query EVPN MAC table directly
        if not remote_mac and remote_svi_ip:
            try:
                container = frr_manager.client.containers.get(container_name)
                # Query EVPN MAC table for the VNI
                evpn_mac_result = container.exec_run(["vtysh", "-c", f"show evpn mac vni {vni}"])
                evpn_mac_output = evpn_mac_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_mac_result.output, bytes) else str(evpn_mac_result.output)
                
                # Parse MAC table to find remote MACs
                # Format: MAC               Type   Flags Intf/Remote ES/VTEP            VLAN  Seq #'s
                #         24:5d:92:a7:65:06 remote      192.168.0.1                          0/0
                # Note: The VTEP shown here is the BGP next-hop, not the actual VTEP IP
                # We need to get the actual VTEP IP from the Type-3 route
                for line in evpn_mac_output.split('\n'):
                    if 'remote' in line.lower():
                        # Try to extract MAC address (first column)
                        parts = line.split()
                        if len(parts) >= 1:
                            mac_candidate = parts[0].strip()
                            # Validate MAC format (xx:xx:xx:xx:xx:xx)
                            if len(mac_candidate.split(':')) == 6:
                                remote_mac = mac_candidate
                                logger.info("[VXLAN] Found MAC %s from EVPN MAC table", remote_mac)
                                break
            except Exception as evpn_mac_exc:
                logger.debug("[VXLAN] Could not query EVPN MAC table: %s", evpn_mac_exc)
        
        # Get the actual VTEP IP from Type-3 route OrigIP field (not BGP next-hop)
        # Type-3 route format: [3]:[EthTag]:[IPlen]:[OrigIP] where OrigIP is the actual VTEP IP
        # BGP next-hop is the route's next-hop (e.g., BGP neighbor IP), not the VTEP IP
        actual_vtep_ip = remote_ip  # Default to remote_ip from config
        bgp_next_hop = None  # BGP next-hop for the EVPN route
        
        # First, try to get BGP next-hop from Type-2 or Type-3 routes and use it as VTEP IP
        # The BGP next-hop is the remote VTEP IP as advertised by the peer
        try:
            container = frr_manager.client.containers.get(container_name)
            import re
            
            # Try to get BGP next-hop from Type-2 route JSON output (most reliable)
            if remote_svi_ip:
                try:
                    evpn_type2_json_result = container.exec_run(["vtysh", "-c", f"show bgp l2vpn evpn route type macip {remote_svi_ip.split('/')[0]} json"])
                    evpn_type2_json_output = evpn_type2_json_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_type2_json_result.output, bytes) else str(evpn_type2_json_result.output)
                    
                    if evpn_type2_json_output:
                        import json
                        try:
                            route_json = json.loads(evpn_type2_json_output)
                            # Look for BGP next-hop in the route JSON (this is the VTEP IP)
                            for rd_key, rd_value in route_json.items():
                                if isinstance(rd_value, list):
                                    for route_entry in rd_value:
                                        # Check for BGP next-hop (nexthops field)
                                        nexthops = route_entry.get("nexthops") or route_entry.get("nexthop") or []
                                        if isinstance(nexthops, list) and len(nexthops) > 0:
                                            # Get the first next-hop IP
                                            next_hop_entry = nexthops[0] if isinstance(nexthops[0], dict) else nexthops[0]
                                            if isinstance(next_hop_entry, dict):
                                                next_hop_ip = next_hop_entry.get("ip") or next_hop_entry.get("nexthop")
                                            else:
                                                next_hop_ip = str(next_hop_entry) if isinstance(next_hop_entry, str) else None
                                            
                                            if next_hop_ip and next_hop_ip != local_ip:
                                                bgp_next_hop = next_hop_ip
                                                actual_vtep_ip = bgp_next_hop
                                                logger.info("[VXLAN] Using BGP next-hop %s as remote VTEP IP from Type-2 route JSON", actual_vtep_ip)
                                                break
                                        # Fallback: check for next-hop in path_info
                                        elif "path_info" in route_entry:
                                            path_info = route_entry["path_info"]
                                            if isinstance(path_info, list) and len(path_info) > 0:
                                                path_entry = path_info[0]
                                                if isinstance(path_entry, dict):
                                                    next_hop_ip = path_entry.get("nexthop") or path_entry.get("ip")
                                                    if next_hop_ip and next_hop_ip != local_ip:
                                                        bgp_next_hop = next_hop_ip
                                                        actual_vtep_ip = bgp_next_hop
                                                        logger.info("[VXLAN] Using BGP next-hop %s as remote VTEP IP from Type-2 route JSON path_info", actual_vtep_ip)
                                                        break
                                    if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                                        break
                        except json.JSONDecodeError:
                            pass  # Fall back to text parsing
                except Exception:
                    pass  # Fall back to text parsing
            
            # Fallback: Try text parsing of Type-2 route output to get BGP next-hop
            if actual_vtep_ip == remote_ip or actual_vtep_ip is None:
                if remote_svi_ip:
                    evpn_type2_result = container.exec_run(["vtysh", "-c", f"show bgp l2vpn evpn route type macip {remote_svi_ip.split('/')[0]}"])
                    evpn_type2_output = evpn_type2_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_type2_result.output, bytes) else str(evpn_type2_result.output)
                    
                    # Look for BGP next-hop in the route output
                    # Format: "192.168.0.1" in the "Next Hop" column or after "from"
                    for line in evpn_type2_output.split('\n'):
                        # Check for next-hop pattern: "192.168.0.1" after "from" or in next-hop column
                        # Format: "*>  [2]:[5000]:[48]:[MAC]  192.168.0.1  ..."
                        # Or: "from 192.168.0.1"
                        if 'from' in line.lower():
                            # Extract IP after "from"
                            from_match = re.search(r'from\s+(\d+\.\d+\.\d+\.\d+)', line, re.IGNORECASE)
                            if from_match:
                                candidate_next_hop = from_match.group(1)
                                if candidate_next_hop != local_ip and candidate_next_hop.startswith(('192.', '10.', '172.')):
                                    bgp_next_hop = candidate_next_hop
                                    actual_vtep_ip = bgp_next_hop
                                    logger.info("[VXLAN] Using BGP next-hop %s as remote VTEP IP from Type-2 route", actual_vtep_ip)
                                    break
                        # Also check for IP in the Next Hop column (typically 2nd or 3rd column)
                        # Format: "*>  [2]:[5000]:[48]:[MAC]  192.168.0.1  ..."
                        parts = line.split()
                        if len(parts) >= 3 and ('*>' in parts or '>' in parts or '*' in parts):
                            # Look for IP address in the columns after the route prefix
                            for part in parts[1:]:
                                ip_match = re.match(r'^(\d+\.\d+\.\d+\.\d+)$', part)
                                if ip_match:
                                    candidate_next_hop = ip_match.group(1)
                                    if (candidate_next_hop != local_ip and 
                                        candidate_next_hop != remote_ip and
                                        candidate_next_hop.startswith(('192.', '10.', '172.'))):
                                        bgp_next_hop = candidate_next_hop
                                        actual_vtep_ip = bgp_next_hop
                                        logger.info("[VXLAN] Using BGP next-hop %s as remote VTEP IP from Type-2 route (column)", actual_vtep_ip)
                                        break
                            if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                                break
            
            # Also try to get BGP next-hop from all Type-2 routes (in case remote_svi_ip route doesn't have it)
            if actual_vtep_ip == remote_ip or actual_vtep_ip is None:
                try:
                    evpn_all_type2_result = container.exec_run(["vtysh", "-c", "show bgp l2vpn evpn route type macip"])
                    evpn_all_type2_output = evpn_all_type2_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_all_type2_result.output, bytes) else str(evpn_all_type2_result.output)
                    
                    # Look for BGP next-hop in route lines
                    for line in evpn_all_type2_output.split('\n'):
                        # Check for next-hop pattern: "192.168.0.1" after "from"
                        if 'from' in line.lower():
                            from_match = re.search(r'from\s+(\d+\.\d+\.\d+\.\d+)', line, re.IGNORECASE)
                            if from_match:
                                candidate_next_hop = from_match.group(1)
                                if (candidate_next_hop != local_ip and 
                                    candidate_next_hop != remote_ip and
                                    candidate_next_hop.startswith(('192.', '10.', '172.'))):
                                    bgp_next_hop = candidate_next_hop
                                    actual_vtep_ip = bgp_next_hop
                                    logger.info("[VXLAN] Using BGP next-hop %s as remote VTEP IP from Type-2 routes (all routes)", actual_vtep_ip)
                                    break
                        if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                            break
                except Exception:
                    pass  # Continue to Type-3 route check
        except Exception:
            pass  # Continue to Type-3 route check
        
        # Try to get BGP next-hop from Type-3 route and use it as VTEP IP
        try:
            container = frr_manager.client.containers.get(container_name)
            evpn_result = container.exec_run(["vtysh", "-c", "show bgp l2vpn evpn route"])
            evpn_output = evpn_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_result.output, bytes) else str(evpn_result.output)
            
            # Parse Type-3 routes to find the actual VTEP IP (OrigIP field) and BGP next-hop
            # Format: [3]:[EthTag]:[IPlen]:[OrigIP]
            # Example: [3]:[5000]:[32]:[192.168.250.1]  192.168.0.1 from 192.168.0.1
            # The OrigIP (192.168.250.1) is the actual VTEP IP, not the BGP next-hop (192.168.0.1)
            for line in evpn_output.split('\n'):
                if '[3]:' in line and (str(vni) in line or remote_ip.split('.')[0] in line):
                    # Try to extract OrigIP (actual VTEP IP) and BGP next-hop from Type-3 route
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if '[3]:' in part:
                            # Extract OrigIP from the route prefix: [3]:[5000]:[32]:[192.168.250.1]
                            route_str = part.strip('[]')
                            route_parts = route_str.split(':')
                            if len(route_parts) >= 4 and route_parts[0] == '3':
                                try:
                                    # IP length should be 32 for IPv4
                                    if route_parts[2] == '32':
                                        # IPv4 address (OrigIP) follows at positions 3-6
                                        orig_ip = '.'.join(route_parts[3:7])
                                        # OrigIP is the actual VTEP IP
                                        if (orig_ip != local_ip and 
                                            orig_ip.startswith(('192.', '10.', '172.'))):
                                            actual_vtep_ip = orig_ip
                                            logger.info("[VXLAN] Found actual VTEP IP %s from Type-3 route OrigIP", actual_vtep_ip)
                                            
                                            # Also extract BGP next-hop for reference
                                            for j in range(i, min(i+15, len(parts))):
                                                if j+2 < len(parts) and parts[j] == 'from':
                                                    bgp_next_hop = parts[j+1]
                                                    logger.info("[VXLAN] BGP next-hop is %s (not used as VTEP IP)", bgp_next_hop)
                                                    break
                                            break
                                except (ValueError, IndexError):
                                    continue
                            # Fallback: try to get BGP next-hop if OrigIP extraction failed
                            if actual_vtep_ip == remote_ip or actual_vtep_ip is None:
                                for j in range(i, min(i+15, len(parts))):
                                    if j+2 < len(parts) and parts[j] == 'from':
                                        candidate_next_hop = parts[j+1]
                                        if (candidate_next_hop != local_ip and 
                                            candidate_next_hop.startswith(('192.', '10.', '172.'))):
                                            bgp_next_hop = candidate_next_hop
                                            # Only use BGP next-hop as fallback if OrigIP not found
                                            if actual_vtep_ip == remote_ip or actual_vtep_ip is None:
                                                actual_vtep_ip = bgp_next_hop
                                                logger.warning("[VXLAN] Using BGP next-hop %s as VTEP IP (OrigIP not found in Type-3 route)", actual_vtep_ip)
                                            break
                            if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                                break
                    if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                        break
        except Exception as vtep_exc:
            logger.debug("[VXLAN] Could not get BGP next-hop from Type-3 route: %s", vtep_exc)
        
        # Only use BGP next-hop as VTEP IP if we couldn't extract OrigIP from Type-3 routes
        # OrigIP is the actual VTEP IP, BGP next-hop is just the route's next-hop
        if bgp_next_hop and (actual_vtep_ip == remote_ip or actual_vtep_ip is None):
            actual_vtep_ip = bgp_next_hop
            logger.warning("[VXLAN] Using BGP next-hop %s as remote VTEP IP (OrigIP from Type-3 route not found)", actual_vtep_ip)
        
        # If we still don't have the actual VTEP IP from BGP next-hop, try to get it from EVPN VNI details
        # (This is a fallback - BGP next-hop should be the primary source)
        if actual_vtep_ip == remote_ip or actual_vtep_ip is None:
            try:
                container = frr_manager.client.containers.get(container_name)
                evpn_vni_result = container.exec_run(["vtysh", "-c", f"show evpn vni {vni} detail"])
                evpn_vni_output = evpn_vni_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_vni_result.output, bytes) else str(evpn_vni_output)
                
                # Parse remote VTEPs from VNI details
                # Format: "Remote VTEPs for this VNI:" followed by lines like "192.255.0.1 flood: -"
                import re
                in_remote_vteps_section = False
                for line in evpn_vni_output.split('\n'):
                    if 'Remote VTEPs' in line:
                        in_remote_vteps_section = True
                        continue
                    if in_remote_vteps_section:
                        # Look for IP addresses in the line
                        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
                        ips = re.findall(ip_pattern, line)
                        for ip in ips:
                            # Skip local VTEP, BGP next-hop, and remote_ip from config
                            if (ip != local_ip and 
                                (bgp_next_hop is None or ip != bgp_next_hop) and 
                                ip != remote_ip):
                                # This might be the actual remote VTEP
                                actual_vtep_ip = ip
                                logger.info("[VXLAN] Found actual VTEP IP %s from EVPN VNI remote VTEPs", actual_vtep_ip)
                                break
                        if actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                            break
            except Exception as vni_exc:
                logger.debug("[VXLAN] Could not get VTEP IP from EVPN VNI details: %s", vni_exc)
        
        # Ensure route exists to actual VTEP IP (if different from remote_ip)
        if actual_vtep_ip and actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
            try:
                container = frr_manager.client.containers.get(container_name)
                # Check FRR routing table first
                has_protocol_route = False
                try:
                    frr_route_result = container.exec_run(["vtysh", "-c", f"show ip route {actual_vtep_ip}"])
                    frr_route_output = frr_route_result.output.decode("utf-8", errors="ignore") if isinstance(frr_route_result.output, bytes) else str(frr_route_result.output)
                    
                    protocol_keywords = ['ospf', 'bgp', 'isis', 'rip', 'eigrp']
                    for keyword in protocol_keywords:
                        if f'via "{keyword}"' in frr_route_output.lower() or f'Known via "{keyword}"' in frr_route_output:
                            has_protocol_route = True
                            logger.debug("[VXLAN] Route to actual VTEP %s exists via %s", actual_vtep_ip, keyword.upper())
                            break
                except Exception:
                    pass
                
                # Check kernel routing table
                route_check = container.exec_run(["ip", "route", "show", actual_vtep_ip])
                route_check_output = route_check.output.decode("utf-8", errors="ignore") if isinstance(route_check.output, bytes) else str(route_check.output)
                
                if not has_protocol_route and (not route_check_output.strip() or actual_vtep_ip not in route_check_output):
                    # No route exists, try to add one
                    try:
                        # Try direct route via underlay interface
                        _container_ip(frr_manager, container_name, ["ip", "route", "replace", f"{actual_vtep_ip}/32", "dev", underlay])
                        logger.info("[VXLAN] Added route to actual VTEP IP %s via %s", actual_vtep_ip, underlay)
                    except Exception:
                        # If direct route fails, try via gateway
                        try:
                            route_result = container.exec_run(["ip", "route", "show", "default"])
                            route_output = route_result.output.decode("utf-8", errors="ignore") if isinstance(route_result.output, bytes) else str(route_result.output)
                            gateway = None
                            for line in route_output.split('\n'):
                                if 'default via' in line:
                                    parts = line.split()
                                    if 'via' in parts:
                                        idx = parts.index('via')
                                        if idx + 1 < len(parts):
                                            gateway = parts[idx + 1]
                                            break
                            if gateway:
                                _container_ip(frr_manager, container_name, ["ip", "route", "replace", f"{actual_vtep_ip}/32", "via", gateway, "dev", underlay])
                                logger.info("[VXLAN] Added route to actual VTEP IP %s via gateway %s", actual_vtep_ip, gateway)
                        except Exception:
                            logger.debug("[VXLAN] Could not add route to actual VTEP IP %s (non-critical)", actual_vtep_ip)
            except Exception:
                pass  # Non-critical
        
        # Configure ARP entry for remote VTEP IP on underlay interface
        # This is CRITICAL for VXLAN encapsulation to work
        # The remote VTEP IP (e.g., 192.168.250.1) needs to be reachable on the underlay
        try:
            container = frr_manager.client.containers.get(container_name)
            # Check current ARP status
            neigh_result = container.exec_run(["ip", "neigh", "show", "dev", underlay])
            neigh_output = neigh_result.output.decode("utf-8", errors="ignore") if isinstance(neigh_result.output, bytes) else str(neigh_result.output)
            
            vtep_arp_resolved = False
            for line in neigh_output.split('\n'):
                if actual_vtep_ip in line and ('REACHABLE' in line or 'PERMANENT' in line):
                    vtep_arp_resolved = True
                    logger.debug("[VXLAN] ARP for remote VTEP %s is already resolved", actual_vtep_ip)
                    break
            
            if not vtep_arp_resolved:
                # Try to get MAC from BGP next-hop (if available)
                vtep_mac = None
                if bgp_next_hop:
                    for line in neigh_output.split('\n'):
                        if bgp_next_hop in line and ('REACHABLE' in line or 'PERMANENT' in line):
                            parts = line.split()
                            # Find MAC address in ARP entry (format: "IP lladdr MAC ...")
                            for i, part in enumerate(parts):
                                if part == 'lladdr' and i+1 < len(parts):
                                    vtep_mac = parts[i+1]
                                    logger.info("[VXLAN] Using BGP next-hop MAC %s for remote VTEP %s", vtep_mac, actual_vtep_ip)
                                    break
                            if vtep_mac:
                                break
                
                # If we still don't have MAC, try to trigger ARP resolution
                if not vtep_mac:
                    try:
                        # Try to ping the actual VTEP IP to trigger ARP resolution
                        # This works if the route exists (which we ensured above)
                        ping_result = container.exec_run(["ping", "-c", "1", "-W", "1", actual_vtep_ip], timeout=3)
                        # Wait a bit for ARP to resolve
                        import time
                        time.sleep(0.5)
                        # Check ARP again
                        neigh_result2 = container.exec_run(["ip", "neigh", "show", "dev", underlay, actual_vtep_ip])
                        neigh_output2 = neigh_result2.output.decode("utf-8", errors="ignore") if isinstance(neigh_result2.output, bytes) else str(neigh_result2.output)
                        for line in neigh_output2.split('\n'):
                            if actual_vtep_ip in line and 'lladdr' in line:
                                parts = line.split()
                                for i, part in enumerate(parts):
                                    if part == 'lladdr' and i+1 < len(parts):
                                        vtep_mac = parts[i+1]
                                        logger.info("[VXLAN] Resolved MAC %s for remote VTEP %s via ping", vtep_mac, actual_vtep_ip)
                                        break
                                if vtep_mac:
                                    break
                    except Exception:
                        pass  # Ping might fail, continue
                
                # If we have MAC, add static ARP entry
                if vtep_mac:
                    try:
                        _container_ip(frr_manager, container_name, [
                            "ip", "neigh", "replace", actual_vtep_ip,
                            "lladdr", vtep_mac,
                            "dev", underlay,
                            "nud", "permanent"
                        ])
                        logger.info("[VXLAN] Configured static ARP entry for remote VTEP %s -> %s on %s", actual_vtep_ip, vtep_mac, underlay)
                    except Exception as arp_vtep_exc:
                        logger.warning("[VXLAN] Failed to configure ARP for remote VTEP %s: %s", actual_vtep_ip, arp_vtep_exc)
                else:
                    logger.warning("[VXLAN] Could not resolve ARP for remote VTEP %s on %s - VXLAN encapsulation may fail", actual_vtep_ip, underlay)
                    logger.warning("[VXLAN] Route to %s exists, but ARP resolution failed. Try: ip neigh add %s lladdr <MAC> dev %s nud permanent", actual_vtep_ip, actual_vtep_ip, underlay)
        except Exception as arp_check_exc:
            logger.debug("[VXLAN] Could not check/configure ARP for remote VTEP (non-critical): %s", arp_check_exc)
        
        # Note: actual_vtep_ip and bgp_next_hop are already extracted above from Type-3 routes
        
        if remote_mac and remote_svi_ip:
            # Configure permanent ARP entry
            # CRITICAL: Delete any existing zebra-managed ARP entry first to remove NOARP flag
            try:
                remote_svi_ip_clean = remote_svi_ip.split('/')[0]
                container = frr_manager.client.containers.get(container_name)
                
                # Check if ARP entry exists with NOARP flag (zebra-managed)
                neigh_result = container.exec_run(["ip", "neigh", "show", "dev", bridge_name, remote_svi_ip_clean])
                neigh_output = neigh_result.output.decode("utf-8", errors="ignore") if isinstance(neigh_result.output, bytes) else str(neigh_result.output)
                
                # Delete existing entry if it has NOARP or proto zebra
                if remote_svi_ip_clean in neigh_output and ('NOARP' in neigh_output or 'proto zebra' in neigh_output):
                    try:
                        _container_ip(frr_manager, container_name, ["ip", "neigh", "del", remote_svi_ip_clean, "dev", bridge_name])
                        logger.info("[VXLAN] Deleted existing zebra-managed ARP entry for %s", remote_svi_ip_clean)
                    except Exception:
                        pass  # Entry might not exist, continue
                
                # Create new ARP entry without proto zebra (kernel-managed)
                _container_ip(frr_manager, container_name, [
                    "ip", "neigh", "replace", remote_svi_ip_clean,
                    "lladdr", remote_mac,
                    "dev", bridge_name,
                    "nud", "permanent"
                ])
                logger.info("[VXLAN] Configured permanent ARP entry: %s -> %s on %s (kernel-managed)", remote_svi_ip_clean, remote_mac, bridge_name)
            except Exception as arp_exc:
                logger.warning("[VXLAN] Failed to configure ARP entry for %s: %s", remote_svi_ip, arp_exc)
            
            # Configure FDB entry - need to find the VXLAN interface name
            try:
                # Find the VXLAN interface name (format: vx{vni}-{device_id})
                # Try to find it by querying interfaces
                vxlan_iface = None
                try:
                    container = frr_manager.client.containers.get(container_name)
                    vxlan_list = container.exec_run(["ip", "link", "show", "type", "vxlan"])
                    vxlan_output = vxlan_list.output.decode("utf-8", errors="ignore") if isinstance(vxlan_list.output, bytes) else str(vxlan_list.output)
                    for line in vxlan_output.split('\n'):
                        if f'vxlan id {vni}' in line.lower() or f'vx{vni}' in line.lower():
                            # Extract interface name (format: "NNN: ifname@...")
                            if ':' in line:
                                parts = line.split(':')
                                if len(parts) >= 2:
                                    candidate = parts[1].split('@')[0].strip()
                                    if candidate.startswith(f'vx{vni}') or f'vni{vni}' in candidate.lower():
                                        vxlan_iface = candidate
                                        break
                except Exception:
                    pass
                
                # Fallback: use the VXLAN interface name passed to this function
                if not vxlan_iface:
                    vxlan_iface = iface
                    logger.debug("[VXLAN] Using provided VXLAN interface name %s for FDB", vxlan_iface)
                
                # Get actual VTEP IP for FDB entry
                # Use the actual VTEP IP we extracted earlier, or try to extract it now
                if 'actual_vtep_ip' in locals() and actual_vtep_ip and actual_vtep_ip != remote_ip and actual_vtep_ip != local_ip:
                    fdb_dst_ip = actual_vtep_ip
                    logger.debug("[VXLAN] Using extracted actual VTEP IP %s for FDB entry", fdb_dst_ip)
                else:
                    fdb_dst_ip = remote_ip
                
                # If we still don't have the correct VTEP IP, try to get it from Type-2 route details
                if fdb_dst_ip == remote_ip or fdb_dst_ip == bgp_next_hop or fdb_dst_ip == local_ip:
                    try:
                        container = frr_manager.client.containers.get(container_name)
                        # Query Type-2 route with details to get router ID
                        evpn_detail_result = container.exec_run(["vtysh", "-c", f"show bgp l2vpn evpn route type macip {remote_svi_ip_clean} json"])
                        evpn_detail_output = evpn_detail_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_detail_result.output, bytes) else str(evpn_detail_result.output)
                        
                        # Try to extract router ID from the route
                        # If that fails, try to get from EVPN VNI remote VTEPs
                        evpn_vni_result = container.exec_run(["vtysh", "-c", f"show evpn vni {vni} detail"])
                        evpn_vni_output = evpn_vni_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_vni_result.output, bytes) else str(evpn_vni_output)
                        
                        # Parse remote VTEPs from VNI details
                        # Format: "192.168.250.1 flood: HER" or "192.255.0.1 flood: -"
                        for line in evpn_vni_output.split('\n'):
                            if 'Remote VTEPs' in line or ('flood:' in line and remote_svi_ip_clean.split('.')[0] in line):
                                # Look for IP addresses in the line
                                import re
                                ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
                                ips = re.findall(ip_pattern, line)
                                for ip in ips:
                                    # Skip local VTEP and BGP next-hop
                                    if ip != local_ip and ip != bgp_next_hop and ip != remote_ip:
                                        # This might be the actual remote VTEP
                                        fdb_dst_ip = ip
                                        logger.info("[VXLAN] Using remote VTEP IP %s from EVPN VNI details for FDB", fdb_dst_ip)
                                        break
                                if fdb_dst_ip != remote_ip and fdb_dst_ip != bgp_next_hop:
                                    break
                    except Exception as vtep_extract_exc:
                        logger.debug("[VXLAN] Could not extract VTEP IP from route details: %s", vtep_extract_exc)
                
                # Final fallback: if FDB still points to BGP next-hop, try to use the remote_ip from config
                # But log a warning
                if fdb_dst_ip == bgp_next_hop:
                    logger.warning("[VXLAN] FDB entry will use BGP next-hop %s instead of actual VTEP IP - this may cause issues", bgp_next_hop)
                    # Try to use remote_ip from config as fallback
                    if remote_ip and remote_ip != bgp_next_hop:
                        fdb_dst_ip = remote_ip
                        logger.info("[VXLAN] Using remote_ip from config %s for FDB", fdb_dst_ip)
                
                # Check if FDB entry exists with wrong VTEP IP and delete it
                try:
                    container = frr_manager.client.containers.get(container_name)
                    fdb_result = container.exec_run(["bridge", "fdb", "show", "dev", vxlan_iface])
                    fdb_output = fdb_result.output.decode("utf-8", errors="ignore") if isinstance(fdb_result.output, bytes) else str(fdb_result.output)
                    
                    # Check if FDB entry exists for this MAC but with wrong destination
                    for line in fdb_output.split('\n'):
                        if remote_mac.lower() in line.lower() and 'dst' in line:
                            # Extract current destination IP
                            parts = line.split()
                            current_dst = None
                            for i, part in enumerate(parts):
                                if part == 'dst' and i+1 < len(parts):
                                    current_dst = parts[i+1]
                                    break
                            
                            # If destination is wrong (BGP next-hop instead of actual VTEP), delete it
                            if current_dst and current_dst != fdb_dst_ip and (current_dst == bgp_next_hop or current_dst == '192.168.0.1'):
                                try:
                                    # Delete existing FDB entry
                                    del_cmd = ["bridge", "fdb", "del", remote_mac, "dev", vxlan_iface]
                                    if 'dst' in line:
                                        del_cmd.extend(["dst", current_dst])
                                    _container_ip(frr_manager, container_name, del_cmd)
                                    logger.info("[VXLAN] Deleted existing FDB entry with wrong VTEP %s", current_dst)
                                except Exception:
                                    pass  # Continue even if delete fails
                except Exception:
                    pass  # Continue even if FDB check fails
                
                # Build FDB command - include VLAN tag if VLAN-aware mode is enabled
                fdb_cmd = [
                    "bridge",
                    "fdb",
                    "add",
                    remote_mac,
                    "dev",
                    vxlan_iface,
                    "dst",
                    fdb_dst_ip,
                ]
                if vlan_id and vlan_id > 0:
                    fdb_cmd.extend(["vlan", str(vlan_id)])
                    logger.debug("[VXLAN] Adding FDB entry with VLAN %s for VLAN-aware VXLAN", vlan_id)
                fdb_cmd.extend(["self", "permanent"])
                _container_ip(frr_manager, container_name, fdb_cmd)
                logger.info("[VXLAN] Configured FDB entry: %s -> %s via %s (permanent, VTEP: %s%s)", 
                           remote_mac, fdb_dst_ip, vxlan_iface, fdb_dst_ip, 
                           f", VLAN: {vlan_id}" if vlan_id and vlan_id > 0 else "")
                
                # Ensure route exists to actual VTEP IP (for FDB destination) if different from remote_ip
                if fdb_dst_ip and fdb_dst_ip != remote_ip:
                    try:
                        container = frr_manager.client.containers.get(container_name)
                        route_check = container.exec_run(["ip", "route", "show", fdb_dst_ip])
                        route_check_output = route_check.output.decode("utf-8", errors="ignore") if isinstance(route_check.output, bytes) else str(route_check.output)
                        
                        if not route_check_output.strip() or fdb_dst_ip not in route_check_output:
                            # No route exists, try to add one via underlay
                            try:
                                # Try direct route via underlay interface
                                _container_ip(frr_manager, container_name, ["ip", "route", "replace", f"{fdb_dst_ip}/32", "dev", underlay])
                                logger.info("[VXLAN] Added route to actual VTEP IP %s via %s", fdb_dst_ip, underlay)
                            except Exception as route_vtep_exc:
                                # If direct route fails, try via gateway
                                try:
                                    route_result = container.exec_run(["ip", "route", "show", "default"])
                                    route_output = route_result.output.decode("utf-8", errors="ignore") if isinstance(route_result.output, bytes) else str(route_result.output)
                                    gateway = None
                                    for line in route_output.split('\n'):
                                        if 'default via' in line:
                                            parts = line.split()
                                            if 'via' in parts:
                                                idx = parts.index('via')
                                                if idx + 1 < len(parts):
                                                    gateway = parts[idx + 1]
                                                    break
                                    if gateway:
                                        _container_ip(frr_manager, container_name, ["ip", "route", "replace", f"{fdb_dst_ip}/32", "via", gateway, "dev", underlay])
                                        logger.info("[VXLAN] Added route to actual VTEP IP %s via gateway %s", fdb_dst_ip, gateway)
                                except Exception:
                                    logger.debug("[VXLAN] Could not add route to actual VTEP IP %s (non-critical)", fdb_dst_ip)
                    except Exception:
                        pass  # Non-critical
            except Exception as fdb_exc:
                logger.warning("[VXLAN] Failed to configure FDB entry for %s: %s", remote_mac, fdb_exc)
        else:
            # Log instructions for manual configuration or EVPN learning
            logger.info("[VXLAN] ARP/FDB entries for remote peer %s should be configured when EVPN Type-2 routes are received", remote_ip)
            logger.info("[VXLAN] Or configure manually in VXLAN config: remote_peer_macs and remote_peer_svi_ips")
            logger.info("[VXLAN] To fix INCOMPLETE ARP manually: ip neigh replace <remote_svi_ip> lladdr <remote_mac> dev %s nud permanent", bridge_name)
            logger.info("[VXLAN] And: bridge fdb add <remote_mac> dev %s dst %s", iface, remote_ip)
    except Exception as arp_fdb_exc:
        logger.debug("[VXLAN] ARP/FDB configuration note (non-critical): %s", arp_fdb_exc)


def configure_vxlan_arp_fdb_from_evpn(device_id: str, vxlan_config: Dict[str, Any], 
                                      container_name: str = None, frr_manager: Any = None) -> bool:
    """
    Configure ARP and FDB entries for remote VXLAN peers by querying EVPN Type-2 routes.
    This should be called after EVPN routes are received to automatically configure ARP/FDB.
    
    Args:
        device_id: Device ID
        vxlan_config: VXLAN configuration dict
        container_name: FRR container name (optional, will be derived if not provided)
        frr_manager: FRR Docker manager (optional, will be created if not provided)
    
    Returns:
        True if ARP/FDB entries were configured, False otherwise
    """
    try:
        if not frr_manager:
            from utils.frr_docker import FRRDockerManager
            frr_manager = FRRDockerManager()
        
        if not container_name:
            device_name = vxlan_config.get("device_name", f"device_{device_id}")
            container_name = frr_manager._get_container_name(device_id, device_name)
        
        # Get VXLAN config
        config = normalize_config(vxlan_config)
        vni = config.get("vni")
        remote_peers = config.get("remote_peers", [])
        
        if not vni or not remote_peers:
            logger.debug("[VXLAN ARP/FDB] Missing VNI or remote peers")
            return False
        
        bridge_name = f"br{vni}"
        remote_ip = remote_peers[0]  # Use first remote peer
        
        # Get VXLAN interface name
        ifname_seed = device_id.replace("-", "")
        vxlan_iface = config.get("vxlan_interface") or f"vx{vni}-{ifname_seed[:6]}"
        if len(vxlan_iface) > 15:
            vxlan_iface = vxlan_iface[:15]
        
        # Get local SVI IP to derive remote SVI IP
        container = frr_manager.client.containers.get(container_name)
        ip_result = container.exec_run(["ip", "addr", "show", bridge_name])
        ip_output = ip_result.output.decode("utf-8", errors="ignore") if isinstance(ip_result.output, bytes) else str(ip_result.output)
        
        local_svi_ip_str = None
        for line in ip_output.split('\n'):
            if 'inet ' in line and bridge_name in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    local_svi_ip_str = parts[1].split('/')[0]
                    break
        
        if not local_svi_ip_str:
            logger.debug("[VXLAN ARP/FDB] Local SVI IP not found on bridge %s", bridge_name)
            return False
        
        # Derive remote SVI IP
        import ipaddress
        local_svi_obj = ipaddress.IPv4Address(local_svi_ip_str)
        remote_svi_obj = ipaddress.IPv4Address(int(local_svi_obj) + 1)
        remote_svi_ip = str(remote_svi_obj)
        
        # Query EVPN Type-2 routes for MAC address
        evpn_result = container.exec_run(["vtysh", "-c", "show bgp l2vpn evpn route type macip"])
        evpn_output = evpn_result.output.decode("utf-8", errors="ignore") if isinstance(evpn_result.output, bytes) else str(evpn_result.output)
        
        remote_mac = None
        # Parse EVPN output to find MAC for remote SVI IP
        for line in evpn_output.split('\n'):
            if remote_svi_ip in line and 'MAC/IP' in line:
                # Try to extract MAC address from the route
                # Format example: "2:0:0:48:24:5d:92:a7:65:06:128:192.255.0.101"
                parts = line.split()
                for part in parts:
                    if ':' in part and len(part.split(':')) >= 6:
                        route_parts = part.split(':')
                        if len(route_parts) >= 10:
                            try:
                                mac_octets = route_parts[4:10]
                                if len(mac_octets) == 6:
                                    remote_mac = ':'.join(f"{int(octet, 16):02x}" for octet in mac_octets)
                                    logger.info("[VXLAN ARP/FDB] Found MAC %s for remote SVI IP %s from EVPN routes", remote_mac, remote_svi_ip)
                                    break
                            except (ValueError, IndexError):
                                continue
                if remote_mac:
                    break
        
        if remote_mac and remote_svi_ip:
            # Configure permanent ARP entry
            try:
                _container_ip(frr_manager, container_name, [
                    "ip", "neigh", "replace", remote_svi_ip,
                    "lladdr", remote_mac,
                    "dev", bridge_name,
                    "nud", "permanent"
                ])
                logger.info("[VXLAN ARP/FDB] Configured permanent ARP entry: %s -> %s on %s", remote_svi_ip, remote_mac, bridge_name)
            except Exception as arp_exc:
                logger.warning("[VXLAN ARP/FDB] Failed to configure ARP entry: %s", arp_exc)
                return False
            
            # Configure FDB entry
            try:
                fdb_cmd = [
                    "bridge",
                    "fdb",
                    "add",
                    remote_mac,
                    "dev",
                    vxlan_iface,
                    "dst",
                    remote_ip,
                ]
                vlan_id = config.get("vlan_id")
                if vlan_id:
                    fdb_cmd.extend(["vlan", str(vlan_id)])
                fdb_cmd.extend(["self", "permanent"])
                _container_ip(frr_manager, container_name, fdb_cmd)
                logger.info(
                    "[VXLAN ARP/FDB] Configured FDB entry: %s -> %s via %s%s",
                    remote_mac,
                    remote_ip,
                    vxlan_iface,
                    f" (VLAN {vlan_id})" if vlan_id else "",
                )
                return True
            except Exception as fdb_exc:
                logger.warning("[VXLAN ARP/FDB] Failed to configure FDB entry: %s", fdb_exc)
                return False
        else:
            logger.debug("[VXLAN ARP/FDB] Could not find MAC address for remote SVI IP %s in EVPN routes", remote_svi_ip)
            return False
            
    except Exception as exc:
        logger.warning("[VXLAN ARP/FDB] Failed to configure ARP/FDB from EVPN: %s", exc)
        return False


def _run_vtysh(frr_manager: Any, container_name: str, commands: List[str]) -> None:
    if not commands:
        return
    container = frr_manager.client.containers.get(container_name)
    payload = "\n".join(commands)
    exec_cmd = f"vtysh <<'EOF'\n{payload}\nEOF"
    result = container.exec_run(["bash", "-c", exec_cmd])
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
        raise RuntimeError(f"vtysh command failed (code {result.exit_code}): {output.strip()}")


def _container_ip(frr_manager: Any, container_name: str, cmd: List[str]) -> None:
    container = frr_manager.client.containers.get(container_name)
    logger.debug("[VXLAN CMD][%s] %s", container_name, " ".join(cmd))
    result = container.exec_run(cmd)
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
        raise RuntimeError(output.strip())


def _container_ip_exists(frr_manager: Any, container_name: str, iface: str) -> bool:
    container = frr_manager.client.containers.get(container_name)
    result = container.exec_run(["ip", "link", "show", iface])
    return result.exit_code == 0


def _interface_exists(interface: str) -> bool:
    if not interface:
        return False
    try:
        subprocess.run(
            ["ip", "link", "show", interface],
            capture_output=True,
            check=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False

def _safe_delete_interface(interface: str) -> None:
    if not interface:
        return
    try:
        subprocess.run(
            ["ip", "link", "del", interface],
            capture_output=True,
            check=True,
            text=True,
        )
        logger.info("[VXLAN] Removed leftover interface %s", interface)
    except subprocess.CalledProcessError as exc:
        logger.debug(
            "[VXLAN] Attempted to delete %s but it was not present: %s",
            interface,
            (exc.stderr or "").strip() or exc,
        )


def _find_vxlan_interfaces_by_vni(
    vni: int,
    match_local: Optional[str] = None,
    match_remote: Optional[str] = None,
    match_underlay: Optional[str] = None,
    match_port: Optional[int] = None,
) -> List[str]:
    """Return interface names that already use the given VNI (optionally filtered)."""
    matches: List[str] = []
    for iface in _list_vxlan_interfaces():
        if iface.get("vni") != vni:
            continue
        if match_local and iface.get("local") and iface["local"] != match_local:
            continue
        if match_remote and iface.get("remote") and iface["remote"] != match_remote:
            continue
        if match_underlay and iface.get("underlay") and iface["underlay"] != match_underlay:
            continue
        if match_port and iface.get("dstport") and iface["dstport"] != match_port:
            continue
        matches.append(iface["name"])
    return matches


def _list_vxlan_interfaces() -> List[Dict[str, Any]]:
    """Parse `ip -d link show type vxlan` output into structured data."""
    try:
        proc = subprocess.run(
            ["ip", "-d", "link", "show", "type", "vxlan"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    interfaces: List[Dict[str, Any]] = []
    block: List[str] = []

    def _flush_block(lines: List[str]) -> None:
        if not lines:
            return
        header = lines[0]
        parts = header.split(":", 2)
        if len(parts) < 2:
            return
        name = parts[1].strip().split("@")[0]
        detail = " ".join(line.strip() for line in lines[1:])
        iface_info: Dict[str, Any] = {"name": name, "details": detail}
        vni_match = re.search(r"\bvxlan id (\d+)\b", detail)
        if vni_match:
            iface_info["vni"] = int(vni_match.group(1))
        local_match = re.search(r"\blocal ([0-9a-fA-F:\.]+)", detail)
        if local_match:
            iface_info["local"] = local_match.group(1)
        remote_match = re.search(r"\bremote ([0-9a-fA-F:\.]+)", detail)
        if remote_match:
            iface_info["remote"] = remote_match.group(1)
        underlay_match = re.search(r"\bdev ([\w\.\-]+)", detail)
        if underlay_match:
            iface_info["underlay"] = underlay_match.group(1)
        dstport_match = re.search(r"\bdstport (\d+)", detail)
        if dstport_match:
            iface_info["dstport"] = int(dstport_match.group(1))
        interfaces.append(iface_info)

    for line in proc.stdout.splitlines():
        if not line:
            continue
        if not line.startswith(" "):
            if block:
                _flush_block(block)
            block = [line]
        else:
            block.append(line)
    if block:
        _flush_block(block)
    return interfaces


def _extract_vlan_id(interface_name: Optional[str]) -> Optional[int]:
    if not interface_name:
        return None
    match = re.search(r"vlan(\d+)", interface_name)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    logger.debug("[VXLAN CMD] %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)
