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
            neighbor_as = bgp_config.get('bgp_remote_asn', 65001)
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
                logger.info(f"[FRR] Configuring IPv4 BGP neighbor {neighbor_ipv4} with update-source {update_source_ipv4}")
                commands.extend([
                    f"neighbor {neighbor_ipv4} remote-as {neighbor_as}",
                    f"neighbor {neighbor_ipv4} update-source {update_source_ipv4}",
                    f"neighbor {neighbor_ipv4} timers {keepalive} {hold_time}",
                ])
            
            # Configure IPv6 BGP if enabled
            neighbor_ipv6 = bgp_config.get('bgp_neighbor_ipv6')
            update_source_ipv6 = bgp_config.get('bgp_update_source_ipv6', ipv6.split('/')[0] if ipv6 else None)
            
            if neighbor_ipv6 and update_source_ipv6:
                logger.info(f"[FRR] Configuring IPv6 BGP neighbor {neighbor_ipv6} with update-source {update_source_ipv6}")
                commands.extend([
                    f"neighbor {neighbor_ipv6} remote-as {neighbor_as}",
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
    
    def stop_frr_container(self, device_id: str, device_name: str = None) -> bool:
        """Stop FRR container"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Stop and remove container
            try:
                container = self.client.containers.get(container_name)
                container.stop()
                container.remove()
                logger.info(f"[FRR] Stopped and removed container {container_name}")
            except docker.errors.NotFound:
                logger.info(f"[FRR] Container {container_name} not found")
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR] Failed to stop FRR container for device {device_id}: {e}")
            return False

# Global instance
frr_manager = FRRDockerManager()

def setup_frr_network():
    """Set up FRR network infrastructure."""
    return frr_manager.setup_network_infrastructure()

def start_frr_container(device_id: str, device_config: Dict) -> Optional[str]:
    """Start FRR container for device."""
    return frr_manager.start_frr_container(device_id, device_config)

def stop_frr_container(device_id: str, device_name: str = None) -> bool:
    """Stop FRR container for device."""
    return frr_manager.stop_frr_container(device_id, device_name)

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