"""
FRR Docker Container Management for OSTG
Uses isolated bridge networking for network isolation
"""

import docker
import logging
import json
import time
import subprocess
import os
from typing import Dict, Optional, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FRRDockerManager:
    """Manages FRR Docker containers using isolated bridge networking"""
    
    def __init__(self):
        self.client = docker.from_env()
        self.container_prefix = "ostg-frr"
        self.image_name = "ostg-frr:latest"
        self.network_name = "ostg-frr-network"
    
    def _sanitize_container_name(self, name: str) -> str:
        """Sanitize device name for use in container naming."""
        import re
        sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '-', name)
        sanitized = sanitized.strip('.-')
        if sanitized and not sanitized[0].isalnum():
            sanitized = 'device-' + sanitized
        if len(sanitized) > 50:
            sanitized = sanitized[:50]
        return sanitized
    
    def _get_container_name(self, device_id: str, device_name: str = None) -> str:
        """Get container name from device_id."""
        return f"{self.container_prefix}-{device_id}"
    
    def setup_network_infrastructure(self):
        """Set up Docker network infrastructure for FRR containers."""
        try:
            # Create isolated bridge network for FRR containers
            try:
                network = self.client.networks.get(self.network_name)
                logger.info(f"[FRR] Network {self.network_name} already exists")
            except docker.errors.NotFound:
                network = self.client.networks.create(
                    self.network_name,
                    driver="bridge",
                    ipam=docker.types.IPAMConfig(
                        driver="default",
                        pool_configs=[
                            docker.types.IPAMPool(
                                subnet="172.30.0.0/16",
                                gateway="172.30.0.1"
                            )
                        ]
                    )
                )
                logger.info(f"[FRR] Created network {self.network_name}")
            
            # Set up host routing to allow containers to reach host networks
            self._setup_container_routing()
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR] Failed to set up network infrastructure: {e}")
            return False
    
    def _setup_container_routing(self):
        """Set up host routing to allow containers to reach host networks."""
        try:
            # Add routes on host to allow container network to reach host networks
            routes_to_add = [
                ("192.168.0.0/24", "172.30.0.1"),
                ("192.168.100.0/24", "172.30.0.1"),
                ("192.168.33.0/24", "172.30.0.1"),
            ]
            
            for network, gateway in routes_to_add:
                try:
                    result = subprocess.run([
                        "ip", "route", "add", network, "via", gateway
                    ], capture_output=True, text=True)
                    
                    if result.returncode == 0:
                        logger.info(f"[FRR] Added route {network} via {gateway}")
                    elif "File exists" in result.stderr:
                        logger.info(f"[FRR] Route {network} via {gateway} already exists")
                    else:
                        logger.warning(f"[FRR] Failed to add route {network} via {gateway}: {result.stderr}")
                        
                except Exception as e:
                    logger.warning(f"[FRR] Failed to add route {network} via {gateway}: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR] Failed to set up container routing: {e}")
            return False
    
    def start_frr_container(self, device_id: str, device_config: Dict) -> Optional[str]:
        """Start FRR container with isolated bridge networking"""
        try:
            device_name = device_config.get('device_name', f'device_{device_id}')
            container_name = self._get_container_name(device_id, device_name)
            
            # Check if container already exists
            try:
                existing_container = self.client.containers.get(container_name)
                if existing_container.status == "running":
                    logger.info(f"[FRR] Container {container_name} already running")
                    return container_name
                else:
                    existing_container.remove(force=True)
                    logger.info(f"[FRR] Removed existing stopped container {container_name}")
            except docker.errors.NotFound:
                pass
            
            # Environment variables for FRR
            env_vars = {
                'FRR_DAEMONS': 'bgpd ospfd',
                'LOCAL_ASN': str(device_config.get('bgp_asn', 65000)),  # Fixed: startup script expects LOCAL_ASN
                'ROUTER_ID': device_config.get('ipv4', '192.168.0.2').split('/')[0],  # Fixed: startup script expects ROUTER_ID
                'DEVICE_NAME': device_config.get('device_name', f'device_{device_id}'),
                'NETWORK': device_config.get('ipv4', '192.168.0.2').split('/')[0].rsplit('.', 1)[0] + '.0',
                'NETMASK': device_config.get('ipv4', '192.168.0.2').split('/')[1] if '/' in device_config.get('ipv4', '192.168.0.2') else '24',
                'INTERFACE': 'eth0',
                'IP_ADDRESS': device_config.get('ipv4', '192.168.0.2').split('/')[0],
                'IP_MASK': device_config.get('ipv4', '192.168.0.2').split('/')[1] if '/' in device_config.get('ipv4', '192.168.0.2') else '24'
            }
            
            # Start container with host networking
            container = self.client.containers.run(
                self.image_name,
                name=container_name,
                network_mode='host',
                privileged=True,
                cap_add=['ALL'],
                security_opt=['seccomp:unconfined'],
                restart_policy={"Name": "unless-stopped"},
                volumes={'/var/log/frr': {'bind': '/var/log/frr', 'mode': 'rw'}},
                environment=env_vars,
                detach=True
            )
            
            logger.info(f"[FRR] Started FRR container {container_name} with host networking")
            
            # Wait for container to be ready and BGP daemon to start
            time.sleep(5)
            
            # Configure BGP in the container with retry mechanism
            bgp_config = device_config.get('bgp_config', {})
            if bgp_config:
                self._configure_bgp_in_container_with_retry(container_name, bgp_config, device_config.get('ipv4'), device_config.get('ipv6'))
            
            return container_name
            
        except Exception as e:
            logger.error(f"[FRR] Failed to start FRR container for device {device_id}: {e}")
            return None
    
    def _configure_bgp_in_container(self, container_name: str, bgp_config: Dict, ipv4: str, ipv6: str):
        """Configure BGP in the FRR container."""
        try:
            container = self.client.containers.get(container_name)
            
            # Build BGP configuration commands
            local_as = bgp_config.get('bgp_asn', 65000)
            # Use separate remote ASN for IPv4 and IPv6 if available, otherwise fall back to general remote ASN
            neighbor_as_ipv4 = bgp_config.get('bgp_remote_asn_ipv4') or bgp_config.get('bgp_remote_asn', 65001)
            neighbor_as_ipv6 = bgp_config.get('bgp_remote_asn_ipv6') or bgp_config.get('bgp_remote_asn', 65001)
            keepalive = bgp_config.get('bgp_keepalive', 30)
            hold_time = bgp_config.get('bgp_hold_time', 90)
            
            commands = [
                "configure terminal",
                f"router bgp {local_as}",
            ]
            
            # Add BGP router-id if IPv4 is available
            if ipv4:
                router_id = ipv4.split('/')[0]
                commands.append(f"bgp router-id {router_id}")
                logger.info(f"[FRR] Setting BGP router-id to {router_id}")
            
            # Add essential BGP configuration
            commands.extend([
                "bgp log-neighbor-changes",
                "bgp graceful-restart"
            ])
            
            # Configure IPv4 BGP if enabled
            neighbor_ipv4 = bgp_config.get('bgp_neighbor_ipv4')
            update_source_ipv4 = bgp_config.get('bgp_update_source_ipv4', ipv4.split('/')[0] if ipv4 else None)
            
            if neighbor_ipv4 and update_source_ipv4:
                logger.info(f"[FRR] Configuring IPv4 BGP neighbor {neighbor_ipv4} with update-source {update_source_ipv4} and remote AS {neighbor_as_ipv4}")
                commands.extend([
                    f"neighbor {neighbor_ipv4} remote-as {neighbor_as_ipv4}",
                    f"neighbor {neighbor_ipv4} update-source {update_source_ipv4}",
                    f"neighbor {neighbor_ipv4} timers {keepalive} {hold_time}",
                ])
            
            # Configure IPv6 BGP if enabled
            neighbor_ipv6 = bgp_config.get('bgp_neighbor_ipv6')
            update_source_ipv6 = bgp_config.get('bgp_update_source_ipv6', ipv6.split('/')[0] if ipv6 else None)
            
            if neighbor_ipv6 and update_source_ipv6:
                logger.info(f"[FRR] Configuring IPv6 BGP neighbor {neighbor_ipv6} with update-source {update_source_ipv6} and remote AS {neighbor_as_ipv6}")
                commands.extend([
                    f"neighbor {neighbor_ipv6} remote-as {neighbor_as_ipv6}",
                    f"neighbor {neighbor_ipv6} update-source {update_source_ipv6}",
                    f"neighbor {neighbor_ipv6} timers {keepalive} {hold_time}",
                ])
            
            # Check if any BGP neighbors were configured
            if not neighbor_ipv4 and not neighbor_ipv6:
                logger.warning(f"[FRR] No BGP neighbors configured for container {container_name}")
                return False
            
            # Configure IPv4 address family if IPv4 neighbor exists
            if neighbor_ipv4:
                logger.info(f"[FRR] Configuring IPv4 address family for neighbor {neighbor_ipv4}")
                commands.extend([
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
                        commands.append(f"network {network}")
                        logger.info(f"[FRR] Advertising IPv4 network {network}")
                    except Exception as e:
                        logger.warning(f"[FRR] Failed to calculate IPv4 network for {ipv4}: {e}")
                
                commands.append("exit-address-family")
            
            # Configure IPv6 address family if IPv6 neighbor exists
            if neighbor_ipv6:
                logger.info(f"[FRR] Configuring IPv6 address family for neighbor {neighbor_ipv6}")
                commands.extend([
                    "address-family ipv6 unicast",
                    f"neighbor {neighbor_ipv6} activate",
                    "exit-address-family"
                ])
            
            commands.extend([
                "exit",
                "exit",
                "write"
            ])
            
            # Execute BGP configuration
            vtysh_cmd = "vtysh"
            for cmd in commands:
                vtysh_cmd += f" -c '{cmd}'"
            
            logger.info(f"[FRR] Configuring BGP in container {container_name}: {vtysh_cmd}")
            
            result = container.exec_run(vtysh_cmd)
            
            if result.exit_code == 0:
                logger.info(f"[FRR] Successfully configured BGP in container {container_name}")
                return True
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.error(f"[FRR] BGP configuration failed in container {container_name}: {output_str}")
                return False
            
        except Exception as e:
            logger.error(f"[FRR] Failed to configure BGP in container {container_name}: {e}")
            return False

    def _configure_bgp_in_container_with_retry(self, container_name: str, bgp_config: Dict, ipv4: str, ipv6: str):
        """Configure BGP in the FRR container with retry mechanism."""
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                logger.info(f"[FRR] BGP configuration attempt {attempt + 1}/{max_retries} for container {container_name}")
                
                # Check if BGP daemon is running
                container = self.client.containers.get(container_name)
                bgpd_check = container.exec_run("ps aux | grep bgpd | grep -v grep")
                
                if bgpd_check.exit_code != 0:
                    logger.warning(f"[FRR] BGP daemon not running yet, waiting {retry_delay} seconds...")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.error(f"[FRR] BGP daemon not running after {max_retries} attempts")
                        return
                
                # Try to configure BGP
                if self._configure_bgp_in_container(container_name, bgp_config, ipv4, ipv6):
                    logger.info(f"[FRR] BGP configuration successful on attempt {attempt + 1}")
                    return
                else:
                    if attempt < max_retries - 1:
                        logger.warning(f"[FRR] BGP configuration failed, retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"[FRR] BGP configuration failed after {max_retries} attempts")
                        
            except Exception as e:
                logger.error(f"[FRR] Exception during BGP configuration attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error(f"[FRR] BGP configuration failed after {max_retries} attempts due to exceptions")
    
    def stop_frr_container(self, device_id: str, device_name: str = None, protocols: list = None, bgp_config: dict = None, ospf_config: dict = None, isis_config: dict = None, interface: str = None, vlan: str = None) -> bool:
        """
        Stop FRR protocols and shutdown interface inside container, but keep container running.
        
        Args:
            device_id: Device ID
            device_name: Device name
            protocols: List of protocols (BGP, OSPF, ISIS)
            bgp_config: BGP configuration dict
            ospf_config: OSPF configuration dict
            isis_config: ISIS configuration dict
            interface: Physical interface name
            vlan: VLAN ID (if applicable)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Check if container exists
            try:
                container = self.client.containers.get(container_name)
            except docker.errors.NotFound:
                logger.info(f"[FRR STOP] Container {container_name} not found - nothing to stop")
                return True  # Container doesn't exist, consider it successful
            
            # Check if container is running, if not start it temporarily
            if container.status != "running":
                logger.info(f"[FRR STOP] Container {container_name} is not running, starting it temporarily...")
                container.start()
                time.sleep(3)  # Wait for container to be ready
            
            logger.info(f"[FRR STOP] Keeping container {container_name} running, shutting down protocols and interface")
            
            # Determine interface name (with VLAN if applicable)
            iface_name = f"vlan{vlan}" if (vlan and vlan != "0") else (interface or "eth0")
            logger.info(f"[FRR STOP] Using interface: {iface_name}")
            
            # Build vtysh commands to shutdown protocols and interface
            vtysh_commands = ["configure terminal"]
            
            # Shutdown BGP if configured
            if bgp_config:
                bgp_asn = bgp_config.get("bgp_asn", "65000")
                neighbor_ipv4 = bgp_config.get("bgp_neighbor_ipv4", "")
                neighbor_ipv6 = bgp_config.get("bgp_neighbor_ipv6", "")
                
                # Shutdown IPv4 BGP neighbors
                if neighbor_ipv4:
                    neighbors_ipv4 = [n.strip() for n in neighbor_ipv4.split(",") if n.strip()]
                    vtysh_commands.append(f"router bgp {bgp_asn}")
                    for neighbor_ip in neighbors_ipv4:
                        vtysh_commands.append(f"neighbor {neighbor_ip} shutdown")
                        logger.info(f"[FRR STOP] Shutting down BGP IPv4 neighbor: {neighbor_ip}")
                    vtysh_commands.append("exit")
                
                # Shutdown IPv6 BGP neighbors
                if neighbor_ipv6:
                    neighbors_ipv6 = [n.strip() for n in neighbor_ipv6.split(",") if n.strip()]
                    if not neighbor_ipv4:  # Only enter router bgp if not already there
                        vtysh_commands.append(f"router bgp {bgp_asn}")
                    for neighbor_ip in neighbors_ipv6:
                        vtysh_commands.append(f"neighbor {neighbor_ip} shutdown")
                        logger.info(f"[FRR STOP] Shutting down BGP IPv6 neighbor: {neighbor_ip}")
                    if not neighbor_ipv4:
                        vtysh_commands.append("exit")
                
            # Shutdown OSPF if configured
            if ospf_config:
                area_id = ospf_config.get("area_id", "0.0.0.0")
                ipv4_enabled = ospf_config.get("ipv4_enabled", False)
                ipv6_enabled = ospf_config.get("ipv6_enabled", False)
                
                # Try to get device IP from database to calculate network
                ipv4_network = None
                if ipv4_enabled:
                    try:
                        from utils.device_database import DeviceDatabase
                        device_db = DeviceDatabase()
                        device_data = device_db.get_device(device_id)
                        if device_data and device_data.get("ipv4_address"):
                            import ipaddress
                            ipv4_addr = device_data["ipv4_address"]
                            ipv4_mask = device_data.get("ipv4_mask", "24")
                            network = ipaddress.IPv4Network(f"{ipv4_addr}/{ipv4_mask}", strict=False)
                            ipv4_network = str(network)
                            logger.info(f"[FRR STOP] Calculated IPv4 network from database: {ipv4_network}")
                    except Exception as e:
                        logger.warning(f"[FRR STOP] Failed to get network from database: {e}")
                
                # Shutdown IPv4 OSPF - remove network and shutdown router
                if ipv4_enabled:
                    vtysh_commands.extend([
                        "router ospf",
                    ])
                    if ipv4_network:
                        vtysh_commands.append(f"no network {ipv4_network} area {area_id}")
                        logger.info(f"[FRR STOP] Removing network {ipv4_network} from OSPF area {area_id}")
                    vtysh_commands.extend([
                        "shutdown",
                        "exit"
                    ])
                    logger.info(f"[FRR STOP] Shutting down OSPF IPv4")
                
                # Shutdown IPv6 OSPF - remove interface from area and shutdown router
                if ipv6_enabled:
                    vtysh_commands.extend([
                        "router ospf6",
                        "shutdown",
                        "exit",
                        f"interface {iface_name}",
                        f"no ipv6 ospf6 area {area_id}",
                        "exit"
                    ])
                    logger.info(f"[FRR STOP] Shutting down OSPF IPv6 and removing interface from area")
            
            # Shutdown ISIS if configured
            if isis_config:
                ipv4_enabled = isis_config.get("ipv4_enabled", False)
                ipv6_enabled = isis_config.get("ipv6_enabled", False)
                
                # Remove ISIS from interface first
                vtysh_commands.append(f"interface {iface_name}")
                if ipv4_enabled:
                    vtysh_commands.append("no ip router isis CORE")
                    logger.info(f"[FRR STOP] Removing IPv4 ISIS from interface {iface_name}")
                if ipv6_enabled:
                    vtysh_commands.append("no ipv6 router isis CORE")
                    logger.info(f"[FRR STOP] Removing IPv6 ISIS from interface {iface_name}")
                vtysh_commands.append("exit")
                
                # Shutdown ISIS router process (always shutdown if ISIS is configured)
                vtysh_commands.extend([
                    "router isis CORE",
                    "shutdown",
                    "exit"
                ])
                logger.info(f"[FRR STOP] Shutting down ISIS process")
            
            # Shutdown interface inside container
            vtysh_commands.extend([
                f"interface {iface_name}",
                "shutdown",  # Shutdown interface in FRR
                "exit",
                "end",
                "write"
            ])
            logger.info(f"[FRR STOP] Shutting down interface {iface_name} inside container")
            
            # Also bring down interface using ip command (redundant but ensures it's down)
            try:
                container.exec_run(["ip", "link", "set", iface_name, "down"])
                logger.info(f"[FRR STOP] Brought down interface {iface_name} using ip command")
            except Exception as e:
                logger.warning(f"[FRR STOP] Failed to bring down interface using ip command: {e}")
            
            # Execute vtysh commands
            config_commands = "\n".join(vtysh_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            
            logger.info(f"[FRR STOP] Executing protocol shutdown commands:\n{config_commands}")
            result = container.exec_run(["bash", "-c", exec_cmd])
            
            if result.exit_code == 0:
                logger.info(f"[FRR STOP] Successfully shut down protocols and interface in container {container_name}")
                logger.info(f"[FRR STOP] Container {container_name} remains running (not removed)")
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.warning(f"[FRR STOP] Some commands failed (exit code {result.exit_code}): {output_str}")
                # Still return True as long as container is running
            
            # Clear BGP sessions separately (exec-level command, not config-level)
            if bgp_config:
                neighbor_ipv4 = bgp_config.get("bgp_neighbor_ipv4", "")
                neighbor_ipv6 = bgp_config.get("bgp_neighbor_ipv6", "")
                neighbors_to_clear = []
                if neighbor_ipv4:
                    neighbors_to_clear.extend([n.strip() for n in neighbor_ipv4.split(",") if n.strip()])
                if neighbor_ipv6:
                    neighbors_to_clear.extend([n.strip() for n in neighbor_ipv6.split(",") if n.strip()])
                
                # Clear BGP sessions after shutdown
                for neighbor_ip in neighbors_to_clear:
                    try:
                        clear_result = container.exec_run(["vtysh", "-c", f"clear ip bgp {neighbor_ip}"])
                        if clear_result.exit_code == 0:
                            logger.info(f"[FRR STOP] Cleared BGP session with {neighbor_ip}")
                        else:
                            clear_output = clear_result.output.decode('utf-8') if isinstance(clear_result.output, bytes) else str(clear_result.output)
                            logger.warning(f"[FRR STOP] Failed to clear BGP session with {neighbor_ip}: {clear_output}")
                    except Exception as e:
                        logger.warning(f"[FRR STOP] Exception clearing BGP session with {neighbor_ip}: {e}")
            
            # Verify container is still running
            container.reload()
            if container.status == "running":
                logger.info(f"[FRR STOP] ✅ Container {container_name} is still running (as intended)")
            else:
                logger.warning(f"[FRR STOP] ⚠️ Container {container_name} status is {container.status} (expected: running)")
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR STOP] Failed to stop FRR protocols for device {device_id}: {e}")
            import traceback
            logger.error(f"[FRR STOP] Traceback: {traceback.format_exc()}")
            return False
    
    def restore_frr_container(self, container_name: str, bgp_config: dict = None, ospf_config: dict = None, isis_config: dict = None, interface: str = None, vlan: str = None, ipv4: str = None, ipv6: str = None) -> bool:
        """
        Restore FRR protocols and bring up interface inside container (revert stop changes).
        
        This method reverts the changes made by stop_frr_container:
        - Brings up interface inside container
        - Removes BGP neighbor shutdown commands (no shutdown)
        - Restores OSPF (no shutdown)
        - Restores ISIS (re-add to interface)
        
        Args:
            container_name: Container name
            bgp_config: BGP configuration dict
            ospf_config: OSPF configuration dict
            isis_config: ISIS configuration dict
            interface: Physical interface name
            vlan: VLAN ID (if applicable)
            ipv4: IPv4 address with mask (e.g., "192.168.0.2/24")
            ipv6: IPv6 address with mask (e.g., "2001:db8::2/64")
        
        Returns:
            True if successful, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            
            # Check if container is running, if not start it
            if container.status != "running":
                logger.info(f"[FRR RESTORE] Container {container_name} is not running, starting it...")
                container.start()
                time.sleep(3)  # Wait for container to be ready
            
            logger.info(f"[FRR RESTORE] Restoring protocols and interface in container {container_name}")
            
            # Determine interface name (with VLAN if applicable)
            iface_name = f"vlan{vlan}" if (vlan and vlan != "0") else (interface or "eth0")
            logger.info(f"[FRR RESTORE] Using interface: {iface_name}")
            
            # Build vtysh commands to restore protocols and interface
            vtysh_commands = ["configure terminal"]
            
            # Restore BGP if configured (remove shutdown commands)
            if bgp_config:
                bgp_asn = bgp_config.get("bgp_asn", "65000")
                neighbor_ipv4 = bgp_config.get("bgp_neighbor_ipv4", "")
                neighbor_ipv6 = bgp_config.get("bgp_neighbor_ipv6", "")
                
                # Remove shutdown for IPv4 BGP neighbors
                if neighbor_ipv4:
                    neighbors_ipv4 = [n.strip() for n in neighbor_ipv4.split(",") if n.strip()]
                    vtysh_commands.append(f"router bgp {bgp_asn}")
                    for neighbor_ip in neighbors_ipv4:
                        vtysh_commands.append(f"no neighbor {neighbor_ip} shutdown")
                        logger.info(f"[FRR RESTORE] Removing shutdown for BGP IPv4 neighbor: {neighbor_ip}")
                    vtysh_commands.append("exit")
                
                # Remove shutdown for IPv6 BGP neighbors
                if neighbor_ipv6:
                    neighbors_ipv6 = [n.strip() for n in neighbor_ipv6.split(",") if n.strip()]
                    if not neighbor_ipv4:  # Only enter router bgp if not already there
                        vtysh_commands.append(f"router bgp {bgp_asn}")
                    for neighbor_ip in neighbors_ipv6:
                        vtysh_commands.append(f"no neighbor {neighbor_ip} shutdown")
                        logger.info(f"[FRR RESTORE] Removing shutdown for BGP IPv6 neighbor: {neighbor_ip}")
                    if not neighbor_ipv4:
                        vtysh_commands.append("exit")
            
            # Restore OSPF if configured (re-add network and remove shutdown)
            if ospf_config:
                area_id = ospf_config.get("area_id", "0.0.0.0")
                ipv4_enabled = ospf_config.get("ipv4_enabled", False)
                ipv6_enabled = ospf_config.get("ipv6_enabled", False)
                
                # Try to get device IP from database to calculate network for IPv4 OSPF
                ipv4_network = None
                if ipv4_enabled:
                    try:
                        # Extract device_id from container_name (format: ostg-frr-{device_id})
                        device_id = None
                        if container_name.startswith(self.container_prefix + "-"):
                            device_id = container_name[len(self.container_prefix + "-"):]
                        
                        if device_id:
                            from utils.device_database import DeviceDatabase
                            device_db = DeviceDatabase()
                            device_data = device_db.get_device(device_id)
                            if device_data and device_data.get("ipv4_address"):
                                import ipaddress
                                ipv4_addr = device_data["ipv4_address"]
                                ipv4_mask = device_data.get("ipv4_mask", "24")
                                network = ipaddress.IPv4Network(f"{ipv4_addr}/{ipv4_mask}", strict=False)
                                ipv4_network = str(network)
                                logger.info(f"[FRR RESTORE] Calculated IPv4 network from database: {ipv4_network}")
                        elif ipv4:
                            # Fallback: try to extract network from ipv4 parameter (format: "192.168.0.2/24")
                            try:
                                import ipaddress
                                if "/" in ipv4:
                                    network = ipaddress.IPv4Network(ipv4, strict=False)
                                    ipv4_network = str(network)
                                    logger.info(f"[FRR RESTORE] Calculated IPv4 network from ipv4 parameter: {ipv4_network}")
                            except Exception as e:
                                logger.warning(f"[FRR RESTORE] Failed to calculate network from ipv4 parameter: {e}")
                    except Exception as e:
                        logger.warning(f"[FRR RESTORE] Failed to get network from database: {e}")
                
                # Restore IPv4 OSPF - re-add network and remove shutdown
                if ipv4_enabled:
                    vtysh_commands.extend([
                        "router ospf",
                    ])
                    if ipv4_network:
                        vtysh_commands.append(f"network {ipv4_network} area {area_id}")
                        logger.info(f"[FRR RESTORE] Re-adding network {ipv4_network} to OSPF area {area_id}")
                    vtysh_commands.extend([
                        "no shutdown",
                        "exit"
                    ])
                    logger.info(f"[FRR RESTORE] Restoring OSPF IPv4 (re-added network and removed shutdown)")
                
                # Restore IPv6 OSPF - re-add interface area binding and remove shutdown
                if ipv6_enabled:
                    # First add interface area binding, then remove shutdown
                    vtysh_commands.extend([
                        f"interface {iface_name}",
                        f"ipv6 ospf6 area {area_id}",
                        "exit",
                        "router ospf6",
                        "no shutdown",
                        "exit"
                    ])
                    logger.info(f"[FRR RESTORE] Restoring OSPF IPv6 (re-added interface area binding and removed shutdown)")
            
            # Bring up interface inside container first (before restoring ISIS)
            vtysh_commands.extend([
                f"interface {iface_name}",
                "no shutdown",  # Bring up interface in FRR
            ])
            logger.info(f"[FRR RESTORE] Bringing up interface {iface_name} inside container")
            
            # Restore ISIS if configured (re-add to interface after bringing it up)
            if isis_config:
                ipv4_enabled = isis_config.get("ipv4_enabled", False)
                ipv6_enabled = isis_config.get("ipv6_enabled", False)
                
                if ipv4_enabled:
                    vtysh_commands.append("ip router isis CORE")
                    logger.info(f"[FRR RESTORE] Restoring IPv4 ISIS on interface {iface_name}")
                if ipv6_enabled:
                    vtysh_commands.append("ipv6 router isis CORE")
                    logger.info(f"[FRR RESTORE] Restoring IPv6 ISIS on interface {iface_name}")
            
            # Exit interface configuration
            vtysh_commands.extend([
                "exit",
                "end",
                "write"
            ])
            
            # Also bring up interface using ip command (redundant but ensures it's up)
            try:
                container.exec_run(["ip", "link", "set", iface_name, "up"])
                logger.info(f"[FRR RESTORE] Brought up interface {iface_name} using ip command")
            except Exception as e:
                logger.warning(f"[FRR RESTORE] Failed to bring up interface using ip command: {e}")
            
            # Execute vtysh commands
            config_commands = "\n".join(vtysh_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            
            logger.info(f"[FRR RESTORE] Executing protocol restore commands:\n{config_commands}")
            result = container.exec_run(["bash", "-c", exec_cmd])
            
            if result.exit_code == 0:
                logger.info(f"[FRR RESTORE] Successfully restored protocols and interface in container {container_name}")
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.warning(f"[FRR RESTORE] Some commands failed (exit code {result.exit_code}): {output_str}")
                # Still return True if interface was brought up
            
            # Verify container is still running
            container.reload()
            if container.status == "running":
                logger.info(f"[FRR RESTORE] ✅ Container {container_name} is running and protocols restored")
            else:
                logger.warning(f"[FRR RESTORE] ⚠️ Container {container_name} status is {container.status} (expected: running)")
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR RESTORE] Failed to restore FRR protocols for container {container_name}: {e}")
            import traceback
            logger.error(f"[FRR RESTORE] Traceback: {traceback.format_exc()}")
            return False
    
    def remove_frr_container(self, device_id: str, device_name: str = None) -> bool:
        """
        Stop and remove FRR Docker container for a device.
        
        This method actually stops and removes the container (unlike stop_frr_container which keeps it running).
        
        Args:
            device_id: Device ID
            device_name: Device name (optional)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Try to get and remove container
            try:
                container = self.client.containers.get(container_name)
                
                # Stop container if it's running
                if container.status == "running":
                    logger.info(f"[FRR REMOVE] Stopping container {container_name}...")
                    container.stop()
                    logger.info(f"[FRR REMOVE] Container {container_name} stopped")
                
                # Remove container
                logger.info(f"[FRR REMOVE] Removing container {container_name}...")
                container.remove(force=True)
                logger.info(f"[FRR REMOVE] ✅ Successfully removed container {container_name}")
                return True
                
            except docker.errors.NotFound:
                logger.info(f"[FRR REMOVE] Container {container_name} not found (already removed)")
                return True  # Container doesn't exist, consider it successful
                
        except Exception as e:
            logger.error(f"[FRR REMOVE] Failed to remove FRR container for device {device_id}: {e}")
            import traceback
            logger.error(f"[FRR REMOVE] Traceback: {traceback.format_exc()}")
            return False

# Global instance
frr_manager = FRRDockerManager()

def setup_frr_network():
    """Set up FRR network infrastructure."""
    return frr_manager.setup_network_infrastructure()

def start_frr_container(device_id: str, device_config: Dict) -> Optional[str]:
    """Start FRR container for device."""
    return frr_manager.start_frr_container(device_id, device_config)

def stop_frr_container(device_id: str, device_name: str = None, protocols: list = None, bgp_config: dict = None, ospf_config: dict = None, isis_config: dict = None, interface: str = None, vlan: str = None) -> bool:
    """Stop FRR protocols and shutdown interface (keep container running)."""
    return frr_manager.stop_frr_container(device_id, device_name, protocols, bgp_config, ospf_config, isis_config, interface, vlan)

def remove_frr_container(device_id: str, device_name: str = None) -> bool:
    """Stop and remove FRR container for device."""
    return frr_manager.remove_frr_container(device_id, device_name)

def configure_bgp_neighbor(device_id: str, neighbor_config: Dict, device_name: str = None) -> bool:
    """Configure BGP neighbor in FRR container."""
    try:
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Build BGP configuration commands
        local_as = neighbor_config.get('local_as', 65000)
        neighbor_ip = neighbor_config.get('neighbor_ip')
        neighbor_as = neighbor_config.get('neighbor_as', 65001)
        update_source = neighbor_config.get('update_source', neighbor_ip)
        
        if not neighbor_ip:
            logger.warning(f"[FRR] No BGP neighbor IP configured for container {container_name}")
            return False
        
        # Determine protocol from neighbor IP or explicit protocol setting
        protocol = neighbor_config.get('protocol', 'ipv4')
        is_ipv6 = ':' in neighbor_ip or protocol == 'ipv6'
        
        commands = [
            "configure terminal",
            f"router bgp {local_as}",
        ]
        
        # Add BGP router-id if IPv4 is available
        if not is_ipv6 and update_source:
            router_id = update_source.split('/')[0] if '/' in update_source else update_source
            commands.append(f"bgp router-id {router_id}")
            logger.info(f"[FRR] Setting BGP router-id to {router_id}")
        
        # Add essential BGP configuration
        commands.extend([
            "bgp log-neighbor-changes",
            "bgp graceful-restart"
        ])
        
        # Configure neighbor
        commands.extend([
            f"neighbor {neighbor_ip} remote-as {neighbor_as}",
            f"neighbor {neighbor_ip} update-source {update_source}",
            f"neighbor {neighbor_ip} timers {neighbor_config.get('keepalive', 30)} {neighbor_config.get('hold_time', 90)}",
        ])
        
        # Configure address family based on protocol
        if is_ipv6:
            # IPv6 address family
            commands.extend([
                "address-family ipv6 unicast",
                f"neighbor {neighbor_ip} activate",
                "exit-address-family"
            ])
        else:
            # IPv4 address family
            commands.extend([
                "address-family ipv4 unicast",
                f"neighbor {neighbor_ip} activate",
                "exit-address-family"
            ])
        
        commands.extend([
            "exit",
            "exit",
            "write"
        ])
        
        # Execute BGP configuration
        vtysh_cmd = "vtysh"
        for cmd in commands:
            vtysh_cmd += f" -c '{cmd}'"
        
        logger.info(f"[FRR] Configuring BGP neighbor in container {container_name}: {vtysh_cmd}")
        
        result = container.exec_run(vtysh_cmd)
        
        if result.exit_code == 0:
            logger.info(f"[FRR] Successfully configured BGP neighbor in container {container_name}")
            return True
        else:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            logger.error(f"[FRR] BGP neighbor configuration failed in container {container_name}: {output_str}")
            return False
        
    except Exception as e:
        logger.error(f"[FRR] Failed to configure BGP neighbor for device {device_id}: {e}")
        return False

def get_bgp_status(device_id: str, device_name: str = None) -> Dict:
    """Get BGP status from FRR container."""
    try:
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Get BGP summary
        result = container.exec_run("vtysh -c 'show bgp summary'")
        
        if result.exit_code == 0:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            return {
                "status": "success",
                "output": output_str,
                "container_name": container_name
            }
        else:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            return {
                "status": "error",
                "output": output_str,
                "container_name": container_name
            }
        
    except Exception as e:
        logger.error(f"[FRR] Failed to get BGP status for device {device_id}: {e}")
        return {
            "status": "error",
            "output": str(e),
            "container_name": "unknown"
        }

def get_bgp_neighbors(device_id: str, device_name: str = None) -> Dict:
    """Get BGP neighbors from FRR container."""
    try:
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Get BGP neighbors
        result = container.exec_run("vtysh -c 'show bgp neighbors'")
        
        if result.exit_code == 0:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            return {
                "status": "success",
                "output": output_str,
                "container_name": container_name
            }
        else:
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            return {
                "status": "error",
                "output": output_str,
                "container_name": container_name
            }
        
    except Exception as e:
        logger.error(f"[FRR] Failed to get BGP neighbors for device {device_id}: {e}")
        return {
            "status": "error",
            "output": str(e),
            "container_name": "unknown"
        }