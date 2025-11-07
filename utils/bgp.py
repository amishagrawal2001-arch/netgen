#bgp.py#
import logging
import subprocess
import random
import time
import shutil
from typing import Dict, List, Any, Optional

BGP_INSTANCES = {}
BGP_ROUTES = {}  # Store advertised routes per device

# Import Docker FRR management
try:
    from .frr_docker import (
        start_frr_container, stop_frr_container, setup_frr_network,
        configure_bgp_neighbor, get_bgp_status, get_bgp_neighbors
    )
    DOCKER_FRR_AVAILABLE = True
except ImportError as e:
    DOCKER_FRR_AVAILABLE = False
    logging.warning(f"[BGP] Docker FRR not available, falling back to system FRR: {e}")

def check_frr_availability():
    """Check if FRR and vtysh are available."""
    if not shutil.which("vtysh"):
        raise RuntimeError("vtysh command not found. Please install FRR (Free Range Routing)")
    
    try:
        # Test basic FRR connectivity
        result = subprocess.run(["vtysh", "-c", "show version"], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise RuntimeError(f"FRR not responding: {result.stderr}")
        return True
    except subprocess.TimeoutExpired:
        raise RuntimeError("FRR connection timeout. Please check if FRR daemon is running")
    except FileNotFoundError:
        raise RuntimeError("vtysh command not found. Please install FRR (Free Range Routing)")

def execute_vtysh_command(device_id, vtysh_commands, timeout=10, device_name=None):
    """Execute vtysh commands inside a Docker container."""
    try:
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Get container name - prefer device_name over device_id
        if device_name:
            container_name = frr_manager._get_container_name(device_id, device_name)
        else:
            container_name = frr_manager._get_container_name(device_id)
        
        # Execute commands in container
        container = frr_manager.client.containers.get(container_name)
        
        # Wait for bgpd to be ready (retry mechanism)
        max_retries = 5
        retry_delay = 2
        bgpd_ready = False
        
        for attempt in range(max_retries):
            # Check if bgpd is running
            check_result = container.exec_run("vtysh -c 'show bgp summary'")
            check_output = check_result.output.decode('utf-8') if isinstance(check_result.output, bytes) else str(check_result.output)
            
            if check_result.exit_code == 0 or "bgpd is not running" not in check_output:
                bgpd_ready = True
                logging.info(f"[BGP] bgpd is ready in container {container_name}")
                break
            else:
                if attempt < max_retries - 1:
                    logging.info(f"[BGP] bgpd not ready yet, waiting {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                else:
                    logging.warning(f"[BGP] bgpd not ready after {max_retries} attempts")
        
        # After bgpd is ready, check for existing BGP configuration and remove if needed
        # This is needed because FRR containers may have pre-configured BGP from template
        if bgpd_ready:
            try:
                # Check if we're trying to configure BGP (router bgp command in vtysh_commands)
                for cmd in vtysh_commands:
                    if 'router bgp' in cmd:
                        # Extract the AS number we want to configure
                        import re
                        target_asn_match = re.search(r'router bgp (\d+)', cmd)
                        if target_asn_match:
                            target_asn = target_asn_match.group(1)
                            
                            # Check existing BGP configuration
                            check_result = container.exec_run("vtysh -c 'show running-config'")
                            if check_result.exit_code == 0:
                                config_output = check_result.output.decode('utf-8') if isinstance(check_result.output, bytes) else str(check_result.output)
                                
                                # Look for existing router bgp with different AS
                                existing_bgp_match = re.search(r'router bgp (\d+)', config_output)
                                if existing_bgp_match:
                                    existing_asn = existing_bgp_match.group(1)
                                    if existing_asn != target_asn:
                                        logging.info(f"[BGP] Removing existing BGP AS {existing_asn} before configuring AS {target_asn}")
                                        # Remove old BGP configuration
                                        remove_result = container.exec_run(f"vtysh -c 'configure terminal' -c 'no router bgp {existing_asn}'")
                                        if remove_result.exit_code == 0:
                                            logging.info(f"[BGP] Successfully removed old BGP AS {existing_asn}")
                                        else:
                                            remove_output = remove_result.output.decode('utf-8') if isinstance(remove_result.output, bytes) else str(remove_result.output)
                                            logging.warning(f"[BGP] Failed to remove old BGP AS {existing_asn}: {remove_output}")
                        break  # Only need to check once
            except Exception as e:
                logging.warning(f"[BGP] Could not check/remove existing BGP config: {e}")
                # Continue anyway
        
        # Execute commands using here-doc to maintain context
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        
        logging.info(f"[BGP] Executing in container {container_name} via here-doc")
        logging.debug(f"[BGP] Commands: {vtysh_commands}")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        
        if result.exit_code != 0:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            logging.error(f"[BGP] vtysh command failed in container: {output_str}")
            
            # If bgpd is not running, provide helpful error message
            if "bgpd is not running" in output_str:
                raise RuntimeError(f"bgpd daemon is not running in container {container_name}. Container may need more time to start.")
            else:
                raise RuntimeError(f"vtysh command failed: {output_str}")
        
        return result
        
    except Exception as e:
        logging.error(f"[BGP] Error executing vtysh in container: {e}")
        raise RuntimeError(f"Failed to execute vtysh in container: {e}")

def safe_vtysh_command(cmd_list, timeout=10, device_id=None, device_name=None):
    """Safely execute vtysh commands with proper error handling."""
    try:
        # If device_id is provided and Docker FRR is available, use Docker container
        if device_id and DOCKER_FRR_AVAILABLE:
            # Extract vtysh commands from the command list
            vtysh_commands = []
            in_vtysh = False
            for cmd in cmd_list:
                if cmd == "vtysh":
                    in_vtysh = True
                    continue
                elif in_vtysh and cmd == "-c":
                    # Skip the "-c" flag, the next item is the command
                    continue
                elif in_vtysh and not cmd.startswith("-"):
                    # This is a vtysh command
                    vtysh_commands.append(cmd)
                elif in_vtysh and cmd.startswith("-"):
                    break
            
            if vtysh_commands:
                return execute_vtysh_command(device_id, vtysh_commands, timeout, device_name)
        
        # Fallback to system FRR
        check_frr_availability()
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logging.error(f"[BGP] vtysh command failed: {result.stderr}")
            # Check if the error is about local system as neighbor (common BGP error)
            if "Can not configure the local system as neighbor" in result.stderr:
                raise RuntimeError("Cannot configure local system as BGP neighbor")
            else:
                raise subprocess.CalledProcessError(result.returncode, cmd_list, result.stdout, result.stderr)
        return result
    except subprocess.TimeoutExpired:
        logging.error(f"[BGP] vtysh command timeout: {' '.join(cmd_list)}")
        raise RuntimeError("FRR command timeout - daemon may be unresponsive")
    except FileNotFoundError:
        logging.error("[BGP] vtysh command not found")
        raise RuntimeError("FRR not installed or not in PATH")

def start_bgp(device_id, iface, config, device_name=None):
    """Start or resume BGP for a device and register it."""
    neighbor = config.get("neighbor_ip")
    asn = config.get("asn")
    remote_asn = config.get("remote_asn")
    update_source = config.get("update_source")
    protocol = config.get("protocol", "ipv4")  # Default to IPv4 for backward compatibility

    if not (asn and remote_asn and neighbor and update_source):
        logging.error(f"[BGP] Missing required BGP config fields")
        return

    # Ensure FRR container is running for this device
    if DOCKER_FRR_AVAILABLE:
        try:
            # Get device configuration from the interface parameter
            device_config = {
                "device_name": f"device_{device_id}",
                "ipv4": update_source if protocol == "ipv4" else "",
                "ipv6": update_source if protocol == "ipv6" else "",
                "vlan": "0"  # Default, will be updated based on actual device config
            }
            
            # Start FRR container for this device
            container_name = start_frr_container(device_id, device_config)
            logging.info(f"[BGP] FRR container started for device {device_id}: {container_name}")
        except Exception as e:
            logging.warning(f"[BGP] Failed to start FRR container for {device_id}: {e}")
            # Continue with system FRR as fallback

    is_restart = device_id in BGP_INSTANCES

    cmd = [
        "vtysh",
        "-c", "configure terminal",
        "-c", f"router bgp {asn}",
    ]

    if is_restart:
        logging.info(f"[BGP] Updating existing BGP session for {device_id}")
    else:
        logging.info(f"[BGP] Starting new BGP session for {device_id}")
    
    # Always configure neighbor (including update-source and timers) to ensure configuration is current
    cmd += [
        "-c", f"  neighbor {neighbor} remote-as {remote_asn}",
        "-c", f"  neighbor {neighbor} update-source {update_source}",
    ]
    
    # Add timer configuration if provided
    keepalive = config.get("keepalive", "30")
    hold_time = config.get("hold_time", "90")
    cmd += [
        "-c", f"  neighbor {neighbor} timers {keepalive} {hold_time}",
    ]

    # Configure address-family based on protocol
    if protocol.lower() == "ipv6":
        # IPv6 address-family configuration
        cmd += [
            "-c", f"  address-family ipv6 unicast",
            "-c", f"    neighbor {neighbor} activate",
            "-c", f"  exit",  # exit ipv6 address-family
            "-c", f"exit"     # exit config terminal
        ]
    else:
        # IPv4 address-family configuration (default)
        cmd += [
            "-c", f"  address-family ipv4 unicast",
            "-c", f"    neighbor {neighbor} activate",
            "-c", f"  exit",  # exit ipv4 address-family
            "-c", f"exit"     # exit config terminal
        ]

    try:
        safe_vtysh_command(cmd, device_id=device_id, device_name=device_name)
        logging.info(f"[BGP] BGP {'resumed' if is_restart else 'started'} for {device_id} ({protocol.upper()})")
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.error(f"[BGP ERROR] Failed to {'resume' if is_restart else 'start'} BGP for {device_id} ({protocol.upper()}): {e}")
        raise e  # Re-raise the exception so the server can handle it properly

    BGP_INSTANCES[device_id] = {
        "iface": iface,
        "asn": asn,
        "neighbor": neighbor,
        "remote_asn": remote_asn,
        "mode": config.get("mode", "eBGP"),
        "protocol": protocol,
        "active": True
    }


def start_dual_stack_bgp(device_id, iface, config):
    """Start or resume dual-stack BGP (both IPv4 and IPv6) for a device."""
    neighbor_ipv4 = config.get("neighbor_ipv4")
    neighbor_ipv6 = config.get("neighbor_ipv6")
    asn = config.get("asn")
    remote_asn = config.get("remote_asn")
    update_source_ipv4 = config.get("update_source_ipv4")
    update_source_ipv6 = config.get("update_source_ipv6")

    if not (asn and remote_asn and neighbor_ipv4 and neighbor_ipv6 and update_source_ipv4 and update_source_ipv6):
        logging.error(f"[BGP] Missing required dual-stack BGP config fields")
        return

    # Ensure FRR container is running for this device
    if DOCKER_FRR_AVAILABLE:
        try:
            # Get device configuration from the interface parameter
            device_config = {
                "device_name": f"device_{device_id}",
                "ipv4": update_source_ipv4,
                "ipv6": update_source_ipv6,
                "vlan": "0"  # Default, will be updated based on actual device config
            }
            
            # Start FRR container for this device
            container_name = start_frr_container(device_id, device_config)
            logging.info(f"[BGP] FRR container started for device {device_id}: {container_name}")
        except Exception as e:
            logging.warning(f"[BGP] Failed to start FRR container for {device_id}: {e}")
            # Continue with system FRR as fallback

    is_restart = device_id in BGP_INSTANCES

    cmd = [
        "vtysh",
        "-c", "configure terminal",
        "-c", f"router bgp {asn}",
    ]

    if is_restart:
        logging.info(f"[BGP] Updating existing dual-stack BGP session for {device_id}")
    else:
        logging.info(f"[BGP] Starting new dual-stack BGP session for {device_id}")
    
    # Add timer configuration if provided
    keepalive = config.get("keepalive", "30")
    hold_time = config.get("hold_time", "90")
    
    # Configure IPv4 neighbor
    cmd += [
        "-c", f"  neighbor {neighbor_ipv4} remote-as {remote_asn}",
        "-c", f"  neighbor {neighbor_ipv4} update-source {update_source_ipv4}",
        "-c", f"  neighbor {neighbor_ipv4} timers {keepalive} {hold_time}",
    ]
    
    # Configure IPv6 neighbor
    cmd += [
        "-c", f"  neighbor {neighbor_ipv6} remote-as {remote_asn}",
        "-c", f"  neighbor {neighbor_ipv6} update-source {update_source_ipv6}",
        "-c", f"  neighbor {neighbor_ipv6} timers {keepalive} {hold_time}",
    ]

    # Configure IPv4 address-family
    cmd += [
        "-c", f"  address-family ipv4 unicast",
        "-c", f"    neighbor {neighbor_ipv4} activate",
        "-c", f"  exit",  # exit ipv4 address-family
    ]
    
    # Try to configure IPv6 address-family, but handle gracefully if not supported
    cmd_ipv6 = cmd + [
        "-c", f"  address-family ipv6 unicast",
        "-c", f"    neighbor {neighbor_ipv6} activate",
        "-c", f"  exit",  # exit ipv6 address-family
        "-c", f"exit"     # exit config terminal
    ]

    try:
        # First try the full dual-stack configuration
        safe_vtysh_command(cmd_ipv6, device_id=device_id)
        logging.info(f"[BGP] Dual-stack BGP {'resumed' if is_restart else 'started'} for {device_id}")
    except (subprocess.CalledProcessError, RuntimeError) as e:
        # If IPv6 fails, try IPv4-only configuration
        logging.warning(f"[BGP] IPv6 address-family not supported, falling back to IPv4-only for {device_id}")
        cmd_ipv4_only = cmd + ["-c", f"exit"]  # exit config terminal

        try:
            safe_vtysh_command(cmd_ipv4_only, device_id=device_id)
            logging.info(f"[BGP] IPv4-only BGP {'resumed' if is_restart else 'started'} for {device_id} (IPv6 not supported)")
        except (subprocess.CalledProcessError, RuntimeError) as e2:
            logging.error(f"[BGP ERROR] Failed to {'resume' if is_restart else 'start'} BGP for {device_id}: {e2}")
            raise e2

    BGP_INSTANCES[device_id] = {
        "iface": iface,
        "asn": asn,
        "neighbor_ipv4": neighbor_ipv4,
        "neighbor_ipv6": neighbor_ipv6,
        "remote_asn": remote_asn,
        "mode": config.get("mode", "eBGP"),
        "protocol": "dual-stack",
        "active": True
    }


"""def start_bgp(device_id, iface, config):
    cmd = build_bgp_cmd(device_id, iface, config)
    try:
        subprocess.run(cmd, check=True)
        logging.info(f"[BGP] Started BGP session for {device_id}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[BGP] Failed to start BGP for {device_id}: {e}")
        return

    # Register in memory
    BGP_INSTANCES[device_id] = {
        "iface": iface,
        "asn": config.get("asn"),
        "neighbor": config.get("neighbor_ip"),
        "remote_asn": config.get("remote_asn"),
        "mode": config.get("mode", "eBGP"),
        "active": True
    }"""

def build_bgp_cmd(device_id, iface, config):
    asn = config.get("asn")
    neighbor = config.get("neighbor_ip")
    remote_asn = config.get("remote_asn")
    mode = config.get("mode", "eBGP")
    update_src = config.get("update_source")

    if not all([asn, neighbor, remote_asn]):
        raise ValueError("Missing BGP configuration values")

    cmd = [
        "vtysh",
        "-c", "configure terminal",
        "-c", f"router bgp {asn}",
        "-c", f"  neighbor {neighbor} remote-as {remote_asn}",
    ]

    if update_src:
        cmd.append(f"-c")
        cmd.append(f"  neighbor {neighbor} update-source {update_src}")

    cmd += [
        "-c", "  address-family ipv4 unicast",
        "-c", f"    neighbor {neighbor} activate",
        "-c", "  exit-address-family",
        "-c", "exit"
    ]
    return cmd



def build_bgp_stop_cmd(device_id):
    instance = BGP_INSTANCES.get(device_id)
    if not instance:
        logging.warning(f"[BGP] No active instance to stop for device {device_id}")
        return [
            "vtysh",
            "-c", "configure terminal",
            "-c", "no router bgp 65000",  # fallback ASN
            "-c", "end",
            "-c", "write memory"
        ]

    asn = instance["asn"]
    return [
        "vtysh",
        "-c", "configure terminal",
        "-c", f"no router bgp {asn}",
        "-c", "end",
        "-c", "write memory"
    ]


def stop_bgp(device_id):
    if device_id in BGP_INSTANCES:
        logging.info(f"[BGP] Marking BGP instance {device_id} as inactive (not removing config)")
        BGP_INSTANCES[device_id]["active"] = False
    else:
        logging.warning(f"[BGP] No active BGP found for device {device_id}")





def cleanup_device_routes(device_id):
    """Clean up all routes and route-maps for a device."""
    if device_id not in BGP_ROUTES:
        return
    
    instance = BGP_INSTANCES.get(device_id)
    if not instance:
        logging.warning(f"[BGP] No BGP instance found for route cleanup: {device_id}")
        return
    
    asn = instance.get("asn")
    if not asn:
        logging.error(f"[BGP] Missing ASN for route cleanup: {device_id}")
        return
    
    routes_to_cleanup = BGP_ROUTES[device_id].copy()
    logging.info(f"[BGP] Cleaning up {len(routes_to_cleanup)} routes for device {device_id}")
    
    # Withdraw all advertised routes
    for route in routes_to_cleanup:
        try:
            withdraw_cmd = [
                "vtysh",
                "-c", "configure terminal",
                "-c", f"router bgp {asn}",
                "-c", f"  address-family ipv4 unicast",
                "-c", f"    no network {route}",
                "-c", "  exit-address-family",
                "-c", "exit"
            ]
            safe_vtysh_command(withdraw_cmd)
            logging.info(f"[BGP] Withdrew route {route} during cleanup")
        except (subprocess.CalledProcessError, RuntimeError) as e:
            logging.warning(f"[BGP] Failed to withdraw route {route} during cleanup: {e}")
    
    # Clean up route-maps (try to remove common route-map names)
    try:
        # Try to remove route-maps that might have been created for this device
        route_map_cleanup_cmd = [
            "vtysh",
            "-c", "configure terminal",
            "-c", f"no route-map RM_{device_id}_*",
            "-c", "exit"
        ]
        safe_vtysh_command(route_map_cleanup_cmd)
        logging.info(f"[BGP] Cleaned up route-maps for device {device_id}")
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.debug(f"[BGP] No route-maps to clean up for device {device_id}: {e}")
    
    # Remove from tracking
    del BGP_ROUTES[device_id]

def remove_bgp_config(device_id):
    """Remove BGP configuration and clean up all associated routes."""
    instance = BGP_INSTANCES.get(device_id)
    if not instance:
        logging.warning(f"[BGP] No instance found for removal: {device_id}")
        return

    asn = instance.get("asn")
    if not asn:
        logging.error(f"[BGP] Missing ASN for device {device_id}")
        return

    # First, clean up all routes
    cleanup_device_routes(device_id)

    # Build command to remove all neighbors for this device
    cmd = [
        "vtysh", "-c", "configure terminal",
        "-c", f"router bgp {asn}",
    ]

    # Handle different BGP instance types
    if instance.get("protocol") == "dual-stack":
        # Dual-stack configuration - remove both IPv4 and IPv6 neighbors
        neighbor_ipv4 = instance.get("neighbor_ipv4")
        neighbor_ipv6 = instance.get("neighbor_ipv6")
        
        if neighbor_ipv4:
            cmd.extend(["-c", f"no neighbor {neighbor_ipv4}"])
        if neighbor_ipv6:
            cmd.extend(["-c", f"no neighbor {neighbor_ipv6}"])
            
        logging.info(f"[BGP] Removing dual-stack neighbors {neighbor_ipv4}, {neighbor_ipv6} from ASN {asn} for device {device_id}")
    else:
        # Single neighbor configuration (IPv4 or IPv6)
        neighbor = instance.get("neighbor")
        if neighbor:
            cmd.extend(["-c", f"no neighbor {neighbor}"])
            logging.info(f"[BGP] Removing neighbor {neighbor} from ASN {asn} for device {device_id}")
        else:
            logging.error(f"[BGP] Missing neighbor for device {device_id}")
            return

    cmd.append("-c")
    cmd.append("exit")

    try:
        safe_vtysh_command(cmd, device_id=device_id)
        del BGP_INSTANCES[device_id]
        logging.info(f"[BGP] Successfully removed BGP configuration for device {device_id}")
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.warning(f"[BGP REMOVE ERROR] {e}")
        # Still remove from tracking even if FRR command failed
        if device_id in BGP_INSTANCES:
            del BGP_INSTANCES[device_id]
    
    # Stop and remove FRR container for this device
    if DOCKER_FRR_AVAILABLE:
        try:
            stop_frr_container(device_id)
            logging.info(f"[BGP] FRR container stopped for device {device_id}")
        except Exception as e:
            logging.warning(f"[BGP] Failed to stop FRR container for {device_id}: {e}")

def cleanup_all_bgp_routes():
    """Clean up all BGP routes and configurations. Useful for system shutdown."""
    logging.info("[BGP] Starting cleanup of all BGP routes and configurations")
    
    # Get list of all device IDs to avoid modification during iteration
    device_ids = list(BGP_INSTANCES.keys())
    
    for device_id in device_ids:
        try:
            cleanup_device_routes(device_id)
            remove_bgp_config(device_id)
        except Exception as e:
            logging.error(f"[BGP] Error during cleanup of device {device_id}: {e}")
    
    # Clear all tracking dictionaries
    BGP_INSTANCES.clear()
    BGP_ROUTES.clear()
    
    # Clean up all Docker FRR containers
    if DOCKER_FRR_AVAILABLE:
        try:
            cleanup_all_containers()
            logging.info("[BGP] All Docker FRR containers cleaned up")
        except Exception as e:
            logging.warning(f"[BGP] Failed to cleanup Docker containers: {e}")
    
    logging.info("[BGP] Completed cleanup of all BGP routes and configurations")

def get_bgp_cleanup_status():
    """Get status of BGP cleanup - useful for monitoring."""
    return {
        "active_instances": len(BGP_INSTANCES),
        "total_routes": sum(len(routes) for routes in BGP_ROUTES.values()),
        "devices_with_routes": len(BGP_ROUTES),
        "instance_details": {
            device_id: {
                "asn": instance.get("asn"),
                "neighbor": instance.get("neighbor"),
                "active": instance.get("active", False),
                "route_count": len(BGP_ROUTES.get(device_id, []))
            }
            for device_id, instance in BGP_INSTANCES.items()
        }
    }


# ============================================================================
# BGP Route Generation Functions
# ============================================================================

def validate_prefix(prefix: str) -> bool:
    """Validate if a prefix is properly formatted and valid."""
    try:
        from ipaddress import ip_network
        network = ip_network(prefix, strict=False)
        
        # Check if it's a valid IPv4 or IPv6 network
        if network.version == 4:
            # IPv4 validation
            if network.prefixlen < 8 or network.prefixlen > 30:
                logging.warning(f"[BGP] IPv4 prefix length out of range (8-30): {prefix}")
                return False
                
            # Check if it's not a reserved/private network that shouldn't be advertised
            if network.is_private and network.prefixlen < 16:
                logging.warning(f"[BGP] Large private IPv4 network may not be suitable for advertisement: {prefix}")
                
        elif network.version == 6:
            # IPv6 validation
            if network.prefixlen < 16 or network.prefixlen > 64:
                logging.warning(f"[BGP] IPv6 prefix length out of range (16-64): {prefix}")
                return False
                
            # Check if it's not a reserved/private network that shouldn't be advertised
            if network.is_private and network.prefixlen < 32:
                logging.warning(f"[BGP] Large private IPv6 network may not be suitable for advertisement: {prefix}")
        else:
            logging.error(f"[BGP] Unsupported IP version: {network.version}")
            return False
            
        return True
    except Exception as e:
        logging.error(f"[BGP] Invalid prefix format: {prefix} - {e}")
        return False

def validate_route_config(route_config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate route configuration parameters."""
    errors = []
    warnings = []
    
    # Validate prefixes
    prefixes = route_config.get("prefixes", [])
    if not prefixes:
        errors.append("No prefixes provided")
    else:
        valid_prefixes = []
        for prefix in prefixes:
            if validate_prefix(prefix):
                valid_prefixes.append(prefix)
            else:
                errors.append(f"Invalid prefix: {prefix}")
        route_config["prefixes"] = valid_prefixes
    
    # Validate AS path
    as_path = route_config.get("as_path", [])
    if as_path:
        for asn in as_path:
            if not isinstance(asn, int) or asn < 1 or asn > 4294967295:
                errors.append(f"Invalid ASN in AS path: {asn}")
    
    # Validate MED
    med = route_config.get("med", 0)
    if not isinstance(med, int) or med < 0 or med > 4294967295:
        errors.append(f"Invalid MED value: {med}")
    
    # Validate local preference
    local_pref = route_config.get("local_pref", 100)
    if not isinstance(local_pref, int) or local_pref < 0 or local_pref > 4294967295:
        errors.append(f"Invalid local preference: {local_pref}")
    
    # Validate origin
    origin = route_config.get("origin", "IGP")
    if origin.upper() not in ["IGP", "EGP", "INCOMPLETE"]:
        errors.append(f"Invalid origin: {origin}")
    
    # Validate communities
    communities = route_config.get("communities", [])
    if communities:
        for community in communities:
            if not isinstance(community, str) or ":" not in community:
                errors.append(f"Invalid community format: {community}")
            else:
                try:
                    parts = community.split(":")
                    if len(parts) != 2:
                        raise ValueError("Invalid format")
                    asn, value = int(parts[0]), int(parts[1])
                    if asn < 0 or asn > 65535 or value < 0 or value > 65535:
                        raise ValueError("Out of range")
                except ValueError:
                    errors.append(f"Invalid community: {community}")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "route_config": route_config
    }

def generate_random_prefixes(base_prefix: str, count: int, prefix_length: int = 24) -> List[str]:
    """Generate random IP prefixes for BGP advertisement."""
    try:
        from ipaddress import ip_network, IPv4Network
        
        # Validate base prefix first
        if not validate_prefix(f"{base_prefix.split('/')[0]}/{prefix_length}"):
            logging.error(f"[BGP] Invalid base prefix: {base_prefix}")
            return []
            
        base_net = ip_network(base_prefix)
        
        # Generate random prefixes within the base network
        prefixes = []
        for _ in range(count):
            # Generate random IP within the base network
            random_ip = str(base_net.network_address + random.randint(1, base_net.num_addresses - 2))
            prefix = f"{random_ip}/{prefix_length}"
            
            # Validate each generated prefix
            if validate_prefix(prefix):
                prefixes.append(prefix)
            else:
                logging.warning(f"[BGP] Generated invalid prefix, skipping: {prefix}")
        
        return prefixes
    except Exception as e:
        logging.error(f"[BGP] Error generating prefixes: {e}")
        return []


def advertise_bgp_routes(device_id: str, route_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Advertise BGP routes for a device.
    
    Args:
        device_id: Device identifier
        route_config: Route configuration containing:
            - prefixes: List of prefixes to advertise
            - as_path: AS path for the routes
            - med: Multi-Exit Discriminator
            - local_pref: Local preference
            - origin: Route origin (IGP, EGP, INCOMPLETE)
            - communities: BGP communities
    """
    instance = BGP_INSTANCES.get(device_id)
    if not instance:
        logging.error(f"[BGP] No active BGP instance for device {device_id}")
        return {"error": "No active BGP instance"}

    # Validate route configuration
    validation_result = validate_route_config(route_config.copy())
    if not validation_result["valid"]:
        logging.error(f"[BGP] Route validation failed for device {device_id}: {validation_result['errors']}")
        return {"error": f"Route validation failed: {', '.join(validation_result['errors'])}"}
    
    # Use validated route configuration
    route_config = validation_result["route_config"]
    
    # Log warnings if any
    if validation_result["warnings"]:
        logging.warning(f"[BGP] Route warnings for device {device_id}: {validation_result['warnings']}")

    asn = instance["asn"]
    neighbor = instance["neighbor"]
    
    # Get route configuration
    prefixes = route_config.get("prefixes", [])
    as_path = route_config.get("as_path", [])
    med = route_config.get("med", 0)
    local_pref = route_config.get("local_pref", 100)
    origin = route_config.get("origin", "IGP")
    communities = route_config.get("communities", [])
    
    if not prefixes:
        logging.warning(f"[BGP] No valid prefixes provided for device {device_id}")
        return {"error": "No valid prefixes provided"}

    advertised_routes = []
    
    try:
        # Configure route-map for custom attributes
        route_map_name = f"RM_{device_id}_{int(time.time())}"
        
        # Build route-map commands
        route_map_cmds = [
            "vtysh",
            "-c", "configure terminal",
            "-c", f"route-map {route_map_name} permit 10"
        ]
        
        # Add AS path prepend if specified
        if as_path:
            as_path_str = " ".join(map(str, as_path))
            route_map_cmds.extend(["-c", f"  set as-path prepend {as_path_str}"])
        
        # Add MED
        if med > 0:
            route_map_cmds.extend(["-c", f"  set metric {med}"])
        
        # Add local preference
        if local_pref != 100:
            route_map_cmds.extend(["-c", f"  set local-preference {local_pref}"])
        
        # Add origin
        if origin.upper() != "IGP":
            route_map_cmds.extend(["-c", f"  set origin {origin.lower()}"])
        
        # Add communities
        if communities:
            comm_str = " ".join(communities)
            route_map_cmds.extend(["-c", f"  set community {comm_str}"])
        
        route_map_cmds.extend(["-c", "exit"])
        
        # Apply route-map to neighbor (both IPv4 and IPv6)
        route_map_cmds.extend([
            "-c", f"router bgp {asn}",
            "-c", f"  address-family ipv4 unicast",
            "-c", f"    neighbor {neighbor} route-map {route_map_name} out",
            "-c", "  exit-address-family",
            "-c", f"  address-family ipv6 unicast",
            "-c", f"    neighbor {neighbor} route-map {route_map_name} out",
            "-c", "  exit-address-family",
            "-c", "exit"
        ])
        
        # Execute route-map configuration
        safe_vtysh_command(route_map_cmds)
        logging.info(f"[BGP] Configured route-map {route_map_name} for device {device_id}")
        
        # Advertise each prefix
        for prefix in prefixes:
            try:
                # Determine if it's IPv4 or IPv6
                from ipaddress import ip_network
                network = ip_network(prefix, strict=False)
                address_family = "ipv4 unicast" if network.version == 4 else "ipv6 unicast"
                
                # Use network command to advertise the prefix
                network_cmd = [
                    "vtysh",
                    "-c", "configure terminal",
                    "-c", f"router bgp {asn}",
                    "-c", f"  address-family {address_family}",
                    "-c", f"    network {prefix}",
                    "-c", "  exit-address-family",
                    "-c", "exit"
                ]
                
                safe_vtysh_command(network_cmd)
                advertised_routes.append(prefix)
                logging.info(f"[BGP] Advertised {address_family} route {prefix} for device {device_id}")
                
            except (subprocess.CalledProcessError, RuntimeError) as e:
                logging.error(f"[BGP] Failed to advertise route {prefix}: {e}")
        
        # Store advertised routes
        if device_id not in BGP_ROUTES:
            BGP_ROUTES[device_id] = []
        
        BGP_ROUTES[device_id].extend(advertised_routes)
        
        return {
            "device_id": device_id,
            "advertised_routes": advertised_routes,
            "route_map": route_map_name,
            "total_routes": len(advertised_routes)
        }
        
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.error(f"[BGP] Failed to configure BGP routes for {device_id}: {e}")
        return {"error": f"Failed to configure routes: {e}"}


def withdraw_bgp_routes(device_id: str, prefixes: List[str] = None) -> Dict[str, Any]:
    """
    Withdraw BGP routes for a device.
    
    Args:
        device_id: Device identifier
        prefixes: Specific prefixes to withdraw (if None, withdraw all)
    """
    instance = BGP_INSTANCES.get(device_id)
    if not instance:
        logging.error(f"[BGP] No active BGP instance for device {device_id}")
        return {"error": "No active BGP instance"}

    asn = instance["asn"]
    
    # Get routes to withdraw
    if prefixes is None:
        prefixes = BGP_ROUTES.get(device_id, [])
    
    if not prefixes:
        logging.warning(f"[BGP] No routes to withdraw for device {device_id}")
        return {"message": "No routes to withdraw"}

    withdrawn_routes = []
    
    try:
        for prefix in prefixes:
            try:
                # Determine if it's IPv4 or IPv6
                from ipaddress import ip_network
                network = ip_network(prefix, strict=False)
                address_family = "ipv4 unicast" if network.version == 4 else "ipv6 unicast"
                
                # Remove network command to withdraw the prefix
                withdraw_cmd = [
                    "vtysh",
                    "-c", "configure terminal",
                    "-c", f"router bgp {asn}",
                    "-c", f"  address-family {address_family}",
                    "-c", f"    no network {prefix}",
                    "-c", "  exit-address-family",
                    "-c", "exit"
                ]
                
                safe_vtysh_command(withdraw_cmd)
                withdrawn_routes.append(prefix)
                logging.info(f"[BGP] Withdrew {address_family} route {prefix} for device {device_id}")
                
            except (subprocess.CalledProcessError, RuntimeError) as e:
                logging.error(f"[BGP] Failed to withdraw route {prefix}: {e}")
        
        # Update stored routes
        if device_id in BGP_ROUTES:
            BGP_ROUTES[device_id] = [r for r in BGP_ROUTES[device_id] if r not in withdrawn_routes]
        
        return {
            "device_id": device_id,
            "withdrawn_routes": withdrawn_routes,
            "total_withdrawn": len(withdrawn_routes)
        }
        
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.error(f"[BGP] Failed to withdraw BGP routes for {device_id}: {e}")
        return {"error": f"Failed to withdraw routes: {e}"}


def get_bgp_routes(device_id: str = None) -> Dict[str, Any]:
    """
    Get BGP routes for a device or all devices.
    
    Args:
        device_id: Specific device ID (if None, return all)
    """
    if device_id:
        if device_id not in BGP_ROUTES:
            return {"device_id": device_id, "routes": []}
        
        return {
            "device_id": device_id,
            "routes": BGP_ROUTES[device_id],
            "total_routes": len(BGP_ROUTES[device_id])
        }
    else:
        return {
            "all_routes": BGP_ROUTES,
            "total_devices": len(BGP_ROUTES)
        }


def generate_bgp_test_routes(device_id: str, route_count: int = 10, 
                           base_prefix: str = "10.0.0.0/8") -> Dict[str, Any]:
    """
    Generate and advertise test BGP routes for a device.
    
    Args:
        device_id: Device identifier
        route_count: Number of routes to generate
        base_prefix: Base prefix for route generation
    """
    # Generate random prefixes
    prefixes = generate_random_prefixes(base_prefix, route_count)
    
    if not prefixes:
        return {"error": "Failed to generate prefixes"}
    
    # Configure test route attributes
    route_config = {
        "prefixes": prefixes,
        "as_path": [65000, 65001],  # Example AS path
        "med": random.randint(0, 100),
        "local_pref": 100,
        "origin": "IGP",
        "communities": ["65000:100", "65000:200"]
    }
    
    return advertise_bgp_routes(device_id, route_config)


def configure_bgp_for_device(device_id: str, bgp_config: Dict, ipv4: str = None, ipv6: str = None, device_name: str = None) -> bool:
    """
    Configure BGP for a device in FRR container.
    This function configures the full BGP setup including router bgp, router-id, neighbors, and address families.
    
    Args:
        device_id: Device identifier
        bgp_config: Full BGP configuration dictionary
        ipv4: Device IPv4 address (optional, for network advertisement)
        ipv6: Device IPv6 address (optional)
        device_name: Device name (optional)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not DOCKER_FRR_AVAILABLE:
            logging.error("[BGP] Docker FRR not available, cannot configure BGP")
            return False
        
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Get router-id (must be loopback IPv4)
        # First, try to get loopback IPv4 from database
        loopback_ipv4 = None
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id) if device_id else None
            if device_data:
                loopback_ipv4 = device_data.get('loopback_ipv4')
                if loopback_ipv4 and loopback_ipv4.strip():
                    loopback_ipv4 = loopback_ipv4.strip().split('/')[0]
        except Exception as e:
            logging.warning(f"[BGP] Could not retrieve loopback IPv4 from database: {e}")
        
        # Router ID must be loopback IPv4
        if loopback_ipv4:
            router_id = loopback_ipv4
            logging.info(f"[BGP] Using loopback IPv4 {router_id} as router-id")
        else:
            # Fallback to interface IPv4 if loopback not available
            if ipv4:
                router_id = ipv4.split('/')[0]
                logging.warning(f"[BGP] Loopback IPv4 not found, using interface IPv4 {router_id} as router-id (fallback)")
            else:
                router_id = "192.168.0.2"
                logging.warning(f"[BGP] No IPv4 available, using default router-id {router_id}")
        
        # Build BGP configuration commands
        local_as = bgp_config.get('bgp_asn', 65000)
        neighbor_as = bgp_config.get('bgp_remote_asn', 65001)
        keepalive = bgp_config.get('bgp_keepalive', 30)
        hold_time = bgp_config.get('bgp_hold_time', 90)
        
        vtysh_commands = [
            "configure terminal",
            f"router bgp {local_as}",
            f"bgp router-id {router_id}",
            "bgp log-neighbor-changes",
            "bgp graceful-restart",
        ]
        
        # Note: Interface IP addresses (IPv4/IPv6) are configured via frr.conf.template
        # when the container is created, not via vtysh commands here
        
        # Configure IPv4 BGP if enabled
        neighbor_ipv4 = bgp_config.get('bgp_neighbor_ipv4')
        update_source_ipv4 = bgp_config.get('bgp_update_source_ipv4', ipv4.split('/')[0] if ipv4 else None)
        
        if neighbor_ipv4 and update_source_ipv4:
            logging.info(f"[BGP] Configuring IPv4 BGP neighbor {neighbor_ipv4} with update-source {update_source_ipv4}")
            vtysh_commands.extend([
                f"neighbor {neighbor_ipv4} remote-as {neighbor_as}",
                f"neighbor {neighbor_ipv4} update-source {update_source_ipv4}",
                f"neighbor {neighbor_ipv4} timers {keepalive} {hold_time}",
            ])
        
        # Configure IPv6 BGP if enabled
        neighbor_ipv6 = bgp_config.get('bgp_neighbor_ipv6')
        update_source_ipv6 = bgp_config.get('bgp_update_source_ipv6', ipv6.split('/')[0] if ipv6 else None)
        
        if neighbor_ipv6 and update_source_ipv6:
            logging.info(f"[BGP] Configuring IPv6 BGP neighbor {neighbor_ipv6} with update-source {update_source_ipv6}")
            vtysh_commands.extend([
                f"neighbor {neighbor_ipv6} remote-as {neighbor_as}",
                f"neighbor {neighbor_ipv6} update-source {update_source_ipv6}",
                f"neighbor {neighbor_ipv6} timers {keepalive} {hold_time}",
            ])
        
        # Check if any BGP neighbors were configured
        if not neighbor_ipv4 and not neighbor_ipv6:
            logging.warning(f"[BGP] No BGP neighbors configured for device {device_id}")
            return False
        
        # Configure IPv4 address family if IPv4 neighbor exists
        if neighbor_ipv4:
            logging.info(f"[BGP] Configuring IPv4 address family for neighbor {neighbor_ipv4}")
            vtysh_commands.extend([
                "address-family ipv4 unicast",
                f"neighbor {neighbor_ipv4} activate",
            ])
            
            # Add network advertisement if IPv4 network is available
            if ipv4:
                # Extract network from IP/mask (e.g., 192.168.0.2/24 -> 192.168.0.0/24)
                ip_addr = ipv4.split('/')[0]
                mask = ipv4.split('/')[1] if '/' in ipv4 else '24'
                # Convert to network address
                import ipaddress
                try:
                    network = ipaddress.IPv4Network(f"{ip_addr}/{mask}", strict=False)
                    vtysh_commands.append(f"network {network}")
                    logging.info(f"[BGP] Advertising IPv4 network {network}")
                except Exception as e:
                    logging.warning(f"[BGP] Failed to calculate IPv4 network for {ipv4}: {e}")
            
            vtysh_commands.append("exit-address-family")
        
        # Configure IPv6 address family if IPv6 neighbor exists
        if neighbor_ipv6:
            logging.info(f"[BGP] Configuring IPv6 address family for neighbor {neighbor_ipv6}")
            vtysh_commands.extend([
                "address-family ipv6 unicast",
                f"neighbor {neighbor_ipv6} activate",
                "exit-address-family"
            ])
        
        vtysh_commands.extend([
            "exit",
            "exit",
        ])
        
        # Execute commands using here-doc to maintain context
        logging.info(f"[BGP] Configuring BGP for device {device_id} in container {container_name}")
        result = execute_vtysh_command(device_id, vtysh_commands, device_name=device_name)
        
        if result.exit_code == 0:
            logging.info(f"[BGP] ✅ Successfully configured BGP for device {device_id} in container {container_name}")
            return True
        else:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            logging.error(f"[BGP] BGP configuration failed in container {container_name}: {output_str}")
            return False
            
    except Exception as e:
        logging.error(f"[BGP] Failed to configure BGP for device {device_id}: {e}")
        import traceback
        logging.error(f"[BGP] Traceback: {traceback.format_exc()}")
        return False


def get_bgp_route_statistics() -> Dict[str, Any]:
    """Get BGP route statistics from FRR."""
    try:
        # Get BGP summary
        summary_cmd = ["vtysh", "-c", "show ip bgp summary"]
        summary_result = safe_vtysh_command(summary_cmd)
        summary_output = summary_result.stdout
        
        # Get BGP routes
        routes_cmd = ["vtysh", "-c", "show ip bgp"]
        routes_result = safe_vtysh_command(routes_cmd)
        routes_output = routes_result.stdout
        
        # Parse summary for neighbor statistics
        neighbors = []
        for line in summary_output.splitlines():
            if "BGP router identifier" in line:
                router_id = line.split()[-1]
            elif "." in line and len(line.split()) >= 10:
                parts = line.split()
                if len(parts) >= 10:
                    neighbors.append({
                        "neighbor": parts[0],
                        "as": parts[2],
                        "state": parts[9] if len(parts) > 9 else "Unknown",
                        "prefixes": parts[10] if len(parts) > 10 else "0"
                    })
        
        # Count total routes
        route_count = len([line for line in routes_output.splitlines() if line.strip() and not line.startswith("BGP")])
        
        return {
            "router_id": router_id if 'router_id' in locals() else "Unknown",
            "neighbors": neighbors,
            "total_routes": route_count,
            "advertised_routes": BGP_ROUTES
        }
        
    except (subprocess.CalledProcessError, RuntimeError) as e:
        logging.error(f"[BGP] Failed to get route statistics: {e}")
        return {"error": f"Failed to get statistics: {e}"}
