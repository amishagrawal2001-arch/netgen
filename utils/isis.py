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
        
        # Get ISIS neighbor details
        neighbor_cmd = f"docker exec {container_id} vtysh -c 'sh isis nei det json'"
        neighbor_result = subprocess.run(neighbor_cmd, shell=True, capture_output=True, text=True, timeout=10)
        
        # Get ISIS summary
        summary_cmd = f"docker exec {container_id} vtysh -c 'sh isis summary json'"
        summary_result = subprocess.run(summary_cmd, shell=True, capture_output=True, text=True, timeout=10)
        
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
                        
                        neighbor_info = {
                            "state": "Up" if interface_info.get("state") == "Up" else "Down",
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
                
                # Set ISIS established if we have neighbors
                if isis_status["neighbors"]:
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
    """Configure ISIS for a device in FRR container."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[ISIS CONFIGURE] Configuring ISIS for device {device_name} ({device_id})")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        logger.info(f"[ISIS CONFIGURE] Using container {container_name} for device {device_name} ({device_id})")
        container = frr_manager.client.containers.get(container_name)
        
        # Wait for container to be ready and daemons to start
        # Optimized to match BGP performance: fewer retries, faster timeout
        import time
        max_retries = 5  # Reduced from 10 to match BGP (faster for ready containers)
        retry_delay = 2  # Increased from 1 to 2 to match BGP (fewer retries needed)
        
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
                
                if check_result and (check_result.exit_code == 0 or "isisd is not running" not in check_output):
                    isisd_ready = True
                    logger.info(f"[ISIS CONFIGURE] Container and FRR daemons ready for {device_name} (attempt {attempt + 1})")
                    break
                else:
                    # ISIS daemon not ready yet or timeout
                    logger.debug(f"[ISIS CONFIGURE] ISIS daemon not ready yet (attempt {attempt + 1}/{max_retries})")
            except Exception as e:
                # Container exec failed
                logger.debug(f"[ISIS CONFIGURE] Container exec failed (attempt {attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                if not isisd_ready:
                    logger.warning(f"[ISIS CONFIGURE] ISIS daemon not ready after {max_retries} attempts for {device_name}, proceeding anyway (may fail)")
        
        # Extract ISIS configuration (handle None values - use default if None)
        area_id = isis_config.get("area_id") or "49.0001.0000.0000.0001.00"
        system_id = isis_config.get("system_id") or "0000.0000.0001"
        level = isis_config.get("level") or "Level-2"
        hello_interval = isis_config.get("hello_interval") or "10"
        hello_multiplier = isis_config.get("hello_multiplier") or "3"
        metric = isis_config.get("metric") or "10"
        interface = isis_config.get("interface") or "vlan20"
        
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
            # CRITICAL: Validate interface name when VLAN is not used
            if vlan and vlan != "0":
                interface = f"vlan{vlan}"
            elif interface_from_db:
                interface = interface_from_db
            else:
                # Interface is required - log error and use empty string (will cause configuration to fail gracefully)
                logging.error(f"[ISIS CONFIGURE] Interface name is required when VLAN is not specified for device {device_id}")
                interface = ""  # Will cause vtysh commands to fail, but better than silently using wrong interface
        else:
            vlan = device_data.get("vlan", "0") if device_data else "0"
            # CRITICAL: Validate interface name when VLAN is not used
            if vlan and vlan != "0":
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
            # Configure router-level ISIS first
            f"router isis CORE",
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
        
        vtysh_commands.extend([
            f"isis network point-to-point",
            "exit",
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
        
        if result.exit_code != 0:
            logging.error(f"[ISIS CONFIGURE] Command failed: {result.output.decode()}")
            return False
        else:
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
                'isis_state': 'Running',
                'isis_system_id': system_id,
                'isis_net': area_id,
                'last_isis_check': datetime.now(timezone.utc).isoformat(),
                'isis_manual_override': True,  # Flag to prevent monitor from overriding
                'isis_manual_override_time': datetime.now(timezone.utc).isoformat()
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
    
    Args:
        device_id: Device identifier
        device_name: Device name
        container_id: Docker container ID
        isis_config: ISIS configuration
        
    Returns:
        True if successful, False otherwise
    """
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
        if not interfaces_to_enable:
            interfaces_to_enable.append("vlan20")

        # Also ensure router-level config is present to match what stop removes
        # Compute router-level parameters
        area_id = isis_config.get("area_id", "49.0001.0000.0000.0001.00")
        level = isis_config.get("level", "Level-2")
        level_map = {"Level-1": "level-1-only", "Level-2": "level-2-only", "Level-1-2": "level-1-2"}
        frr_level = level_map.get(level, "level-2-only")

        # Determine address families based on device IP configuration
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            enable_ipv4 = bool(device_data and device_data.get('ipv4_address'))
            enable_ipv6 = bool(device_data and device_data.get('ipv6_address'))
        except Exception:
            # Fallback: enable both if unable to determine
            enable_ipv4 = True
            enable_ipv6 = True

        # Ensure router process first, then enable interface (some FRR builds require router before interface attach)
        # Note: Global router-id is configured in frr_docker.py when container is created
        vtysh_commands = [
            "configure terminal",
            "router isis CORE",
            # Best-effort cleanup of common/default NET before setting desired NET
            "no net 49.0001.0000.0000.0001.00",
            f"is-type {frr_level}",
            f"net {area_id}",
            "exit",
        ]
        for iface in interfaces_to_enable:
            vtysh_commands.extend([
                f"interface {iface}",
            ])
            # Add IPv4 or IPv6 based on configured addresses
            if enable_ipv4:
                vtysh_commands.append(" ip router isis CORE")
            if enable_ipv6:
                vtysh_commands.append(" ipv6 router isis CORE")
            vtysh_commands.extend([
                " isis network point-to-point",
                "exit",
            ])
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

        if exit_code == 0:
            logger.info(f"[ISIS START] Successfully started ISIS for {device_name}")
            
            # Update database with ISIS status
            try:
                from .device_database import DeviceDatabase
                device_db = DeviceDatabase()
                
                update_data = {
                    'isis_running': True,
                    'isis_state': 'Running',
                    'isis_established': False,  # Will be updated by monitor
                    'last_isis_check': datetime.now(timezone.utc).isoformat(),
                    'isis_manual_override': True,  # Flag to prevent monitor from overriding
                    'isis_manual_override_time': datetime.now(timezone.utc).isoformat()
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
    
    Args:
        device_id: Device identifier
        device_name: Device name (optional, will be looked up if not provided)
        container_id: Docker container ID (optional, for backward compatibility)
        isis_config: ISIS configuration (optional)
        
    Returns:
        True if successful, False otherwise
    """
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
        net = (isis_config or {}).get("area_id", "49.0001.0000.0000.0001.00")
        level = (isis_config or {}).get("level", "Level-2")
        
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
        except Exception:
            # Fallback: if nothing resolved, default to vlan20 (legacy)
            if not interfaces_to_clean:
                interfaces_to_clean.append("vlan20")
        
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
        # Do NOT remove router-level configuration (preserve router isis CORE, is-type, and net)
        vtysh_commands.extend([
            "end",
            "write",
        ])
        
        # Execute commands using here document
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logger.info(f"[ISIS STOP] Executing ISIS removal commands on container {container_name}")
        logger.debug(f"[ISIS STOP] Commands: {vtysh_commands}")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        logger.info(f"[ISIS STOP] Command exit code: {result.exit_code}")
        logger.info(f"[ISIS STOP] Command output: {result.output.decode()}")
        
        if result.exit_code != 0:
            logger.error(f"[ISIS STOP] Command failed: {result.output.decode()}")
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
                'isis_manual_override': True,  # Flag to prevent monitor from overriding
                'isis_manual_override_time': datetime.now(timezone.utc).isoformat()
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

