"""
VRF-based FRR Docker Container Management for OSTG
Uses Linux VRF (Virtual Routing and Forwarding) for network isolation
Allows duplicate IPs across different VRFs
"""

import docker
import logging
import json
import time
import subprocess
import os
import ipaddress
import hashlib
from typing import Dict, Optional, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FRRDockerManager:
    """Manages FRR Docker containers using VRF for network isolation"""
    
    def __init__(self):
        self.client = docker.from_env()
        self.container_prefix = "ostg-frr-vrf"
        self.image_name = "ostg-frr:latest"
        self.vrf_table_base = 1000  # Starting VRF table number
    
    def _sanitize_container_name(self, name: str) -> str:
        """Sanitize device name for use in container naming."""
        import re
        # Remove or replace special characters that are not allowed in container names
        # Container names must match: [a-zA-Z0-9][a-zA-Z0-9_.-]*
        sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '-', name)
        # Remove leading/trailing dots and hyphens
        sanitized = sanitized.strip('.-')
        # Ensure it starts with alphanumeric character
        if sanitized and not sanitized[0].isalnum():
            sanitized = 'device-' + sanitized
        # Limit length to reasonable container name size
        if len(sanitized) > 50:
            sanitized = sanitized[:50]
        return sanitized
    
    def _get_container_name(self, device_id: str, device_name: str = None) -> str:
        """Get container name from device_id (always use device_id for consistency)."""
        # Always use device_id for container naming to ensure consistency
        # even if device_name changes, the container persists with the same name
        return f"{self.container_prefix}-{device_id}"
    
    def _get_vrf_name(self, device_id: str) -> str:
        """Get VRF name for device."""
        return f"vrf-{device_id}"
    
    def _get_vrf_table(self, device_id: str) -> int:
        """Get VRF table number for device (deterministic based on device_id)."""
        # Use hash of device_id to get consistent table number
        hash_obj = hashlib.md5(device_id.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        return self.vrf_table_base + (hash_int % 1000)  # Ensure table number is reasonable
        
    def setup_network_infrastructure(self):
        """Set up VRF infrastructure for FRR containers"""
        try:
            logger.info("VRF-based networking - no Docker network setup needed")
            logger.info("Each device will get its own VRF for network isolation")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup VRF infrastructure: {e}")
            return False
    
    def create_vrf_for_device(self, device_id: str, interface: str, ipv4: str = None, ipv6: str = None) -> bool:
        """Create VRF and configure interface for device."""
        try:
            vrf_name = self._get_vrf_name(device_id)
            vrf_table = self._get_vrf_table(device_id)
            
            logger.info(f"[VRF] Creating VRF {vrf_name} with table {vrf_table} for device {device_id}")
            
            # Create VRF interface
            result = subprocess.run([
                "ip", "link", "add", vrf_name, "type", "vrf", "table", str(vrf_table)
            ], capture_output=True, text=True)
            
            if result.returncode != 0 and "File exists" not in result.stderr:
                logger.error(f"[VRF] Failed to create VRF {vrf_name}: {result.stderr}")
                return False
            
            # Bring up VRF interface
            subprocess.run(["ip", "link", "set", vrf_name, "up"], check=True)
            
            # Assign interface to VRF
            subprocess.run(["ip", "link", "set", interface, "master", vrf_name], check=True)
            
            # Configure IP addresses in VRF context
            if ipv4:
                subprocess.run(["ip", "addr", "add", ipv4, "dev", interface], check=True)
                logger.info(f"[VRF] Added IPv4 {ipv4} to {interface} in VRF {vrf_name}")
            
            if ipv6:
                subprocess.run(["ip", "-6", "addr", "add", ipv6, "dev", interface], check=True)
                logger.info(f"[VRF] Added IPv6 {ipv6} to {interface} in VRF {vrf_name}")
            
            # Bring up interface
            subprocess.run(["ip", "link", "set", interface, "up"], check=True)
            
            # Add default route in VRF table
            if ipv4:
                # Extract gateway from IP (assume .1 is gateway)
                ip_obj = ipaddress.IPv4Interface(ipv4)
                gateway = str(ip_obj.network.network_address + 1)
                subprocess.run([
                    "ip", "route", "add", "default", "via", gateway, "dev", interface, "table", str(vrf_table)
                ], capture_output=True, text=True)
                logger.info(f"[VRF] Added default route via {gateway} in VRF table {vrf_table}")
            
            logger.info(f"[VRF] Successfully created VRF {vrf_name} for device {device_id}")
            return True
            
        except Exception as e:
            logger.error(f"[VRF] Failed to create VRF for device {device_id}: {e}")
            return False
    
    def remove_vrf_for_device(self, device_id: str, interface: str) -> bool:
        """Remove VRF and clean up interface for device."""
        try:
            vrf_name = self._get_vrf_name(device_id)
            
            logger.info(f"[VRF] Removing VRF {vrf_name} for device {device_id}")
            
            # Remove interface from VRF
            subprocess.run(["ip", "link", "set", interface, "nomaster"], capture_output=True, text=True)
            
            # Remove VRF interface
            subprocess.run(["ip", "link", "del", vrf_name], capture_output=True, text=True)
            
            logger.info(f"[VRF] Successfully removed VRF {vrf_name} for device {device_id}")
            return True
            
        except Exception as e:
            logger.error(f"[VRF] Failed to remove VRF for device {device_id}: {e}")
            return False
    
    def start_frr_container(self, device_id: str, device_config: Dict) -> Optional[str]:
        """Start FRR container with VRF networking"""
        try:
            device_name = device_config.get('device_name', f'device_{device_id}')
            container_name = self._get_container_name(device_id, device_name)
            interface = device_config.get('interface', 'ens4np0')
            ipv4 = device_config.get('ipv4')
            ipv6 = device_config.get('ipv6')
            
            # Create VRF for this device
            if not self.create_vrf_for_device(device_id, interface, ipv4, ipv6):
                return None
            
            # Check if container already exists
            try:
                existing_container = self.client.containers.get(container_name)
                if existing_container.status == "running":
                    logger.info(f"[VRF] Container {container_name} already running")
                    return container_name
                else:
                    existing_container.remove(force=True)
                    logger.info(f"[VRF] Removed existing stopped container {container_name}")
            except docker.errors.NotFound:
                pass
            
            # Environment variables for FRR
            env_vars = {
                'FRR_DAEMONS': 'bgpd ospfd',
                'FRR_BGP_AS': str(device_config.get('bgp_asn', 65000)),
                'FRR_BGP_ROUTER_ID': ipv4.split('/')[0] if ipv4 else '192.168.0.2'
            }
            
            # Start container with host networking (VRF provides isolation)
            container = self.client.containers.run(
                self.image_name,
                name=container_name,
                network_mode='host',  # Use host networking with VRF isolation
                privileged=True,
                cap_add=['ALL'],
                security_opt=['seccomp:unconfined'],
                restart_policy={"Name": "unless-stopped"},
                volumes={'/var/log/frr': {'bind': '/var/log/frr', 'mode': 'rw'}},
                environment=env_vars,
                detach=True
            )
            
            logger.info(f"[VRF] Started FRR container {container_name} with VRF networking")
            
            # Wait for container to be ready
            time.sleep(3)
            
            # Configure BGP in the container
            bgp_config = device_config.get('bgp_config', {})
            if bgp_config:
                self._configure_bgp_in_container(container_name, bgp_config, ipv4, ipv6)
            
            return container_name
            
        except Exception as e:
            logger.error(f"[VRF] Failed to start FRR container for device {device_id}: {e}")
            return None
    
    def _configure_bgp_in_container(self, container_name: str, bgp_config: Dict, ipv4: str, ipv6: str):
        """Configure BGP in the FRR container."""
        try:
            container = self.client.containers.get(container_name)
            
            # Build BGP configuration commands
            local_as = bgp_config.get('bgp_asn', 65000)
            neighbor_ip = bgp_config.get('bgp_neighbor_ipv4')
            neighbor_as = bgp_config.get('bgp_remote_asn', 65001)
            update_source = bgp_config.get('bgp_update_source_ipv4', ipv4.split('/')[0] if ipv4 else '192.168.0.2')
            
            if not neighbor_ip:
                logger.warning(f"[VRF] No BGP neighbor IP configured for container {container_name}")
                return
            
            commands = [
                "configure terminal",
                f"router bgp {local_as}",
                f"neighbor {neighbor_ip} remote-as {neighbor_as}",
                f"neighbor {neighbor_ip} update-source {update_source}",
                f"neighbor {neighbor_ip} timers {bgp_config.get('bgp_keepalive', 30)} {bgp_config.get('bgp_hold_time', 90)}",
                "exit",
                "exit",
                "write"
            ]
            
            # Execute BGP configuration
            vtysh_cmd = "vtysh"
            for cmd in commands:
                vtysh_cmd += f" -c '{cmd}'"
            
            logger.info(f"[VRF] Configuring BGP in container {container_name}: {vtysh_cmd}")
            
            result = container.exec_run(vtysh_cmd)
            
            if result.exit_code == 0:
                logger.info(f"[VRF] Successfully configured BGP in container {container_name}")
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.error(f"[VRF] BGP configuration failed in container {container_name}: {output_str}")
            
        except Exception as e:
            logger.error(f"[VRF] Failed to configure BGP in container {container_name}: {e}")
    
    def _get_network_from_ip(self, ip_address: str) -> str:
        """Extract network from IP address"""
        try:
            parts = ip_address.split('.')
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
        except:
            return "192.168.0.0"
    
    def _verify_frr_running(self, container_name: str) -> bool:
        """Verify that FRR is running in the container"""
        try:
            container = self.client.containers.get(container_name)
            result = container.exec_run("vtysh -c 'show version'")
            return result.exit_code == 0
        except Exception as e:
            logger.error(f"Failed to verify FRR running in {container_name}: {e}")
            return False
    
    def stop_frr_container(self, device_id: str, device_name: str = None) -> bool:
        """Stop FRR container and clean up VRF"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            interface = f"vlan{device_id.split('device')[-1]}" if 'device' in device_id else f"vlan{device_id}"
            
            # Stop and remove container
            try:
                container = self.client.containers.get(container_name)
                container.stop()
                container.remove()
                logger.info(f"[VRF] Stopped and removed container {container_name}")
            except docker.errors.NotFound:
                logger.info(f"[VRF] Container {container_name} not found")
            
            # Remove VRF
            self.remove_vrf_for_device(device_id, interface)
            
            return True
            
        except Exception as e:
            logger.error(f"[VRF] Failed to stop FRR container for device {device_id}: {e}")
            return False
    
    def configure_bgp_neighbor(self, device_id: str, neighbor_config: Dict, device_name: str = None) -> bool:
        """Configure BGP neighbor via FRR vtysh in VRF container"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Get container
            container = self.client.containers.get(container_name)
            
            # Configure neighbor via vtysh
            neighbor_ip = neighbor_config.get('neighbor_ip', '')
            neighbor_as = neighbor_config.get('neighbor_as', '')
            local_as = neighbor_config.get('local_as', 65001)
            update_source = neighbor_config.get('update_source', 'eth0')
            keepalive = neighbor_config.get('keepalive', '30')
            hold_time = neighbor_config.get('hold_time', '90')
            
            # Debug logging
            logger.info(f"[VRF] BGP Configuration: neighbor_ip={neighbor_ip}, neighbor_as={neighbor_as}, local_as={local_as}, update_source={update_source}, keepalive={keepalive}, hold_time={hold_time}")
            
            # Validate required parameters
            if not neighbor_ip or not neighbor_as:
                logger.error(f"[VRF] Missing required BGP parameters: neighbor_ip={neighbor_ip}, neighbor_as={neighbor_as}")
                return False
            
            # Build vtysh commands for BGP configuration
            protocol = neighbor_config.get('protocol', 'ipv4')
            
            commands = [
                "configure terminal",
                f"router bgp {local_as}",
                f"neighbor {neighbor_ip} remote-as {neighbor_as}",
                f"neighbor {neighbor_ip} update-source {update_source}",
                f"neighbor {neighbor_ip} timers {keepalive} {hold_time}",
                "end",
                "write"
            ]
            
            # Execute BGP configuration
            vtysh_cmd = "vtysh"
            for cmd in commands:
                vtysh_cmd += f" -c '{cmd}'"
            
            logger.info(f"[VRF] Executing BGP configuration: {vtysh_cmd}")
            result = container.exec_run(vtysh_cmd)
            
            if result.exit_code == 0:
                logger.info(f"[VRF] Configured BGP neighbor {neighbor_ip} for {device_id}")
                return True
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.error(f"[VRF] Failed to configure BGP neighbor: {output_str}")
                return False
                
        except Exception as e:
            logger.error(f"[VRF] Failed to configure BGP neighbor for {device_id}: {e}")
            return False
    
    def remove_bgp_neighbors(self, device_id: str, device_name: str = None) -> bool:
        """Remove all BGP neighbors from FRR container for a device"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Get container
            container = self.client.containers.get(container_name)
            
            # Get the current BGP AS from the container
            local_as = self._get_container_bgp_as(container)
            if not local_as:
                logger.warning(f"Could not determine BGP AS for device {device_id}, using default")
                local_as = 65000
            
            # Get current BGP neighbors
            result = container.exec_run("vtysh -c 'show ip bgp neighbors'")
            if result.exit_code != 0:
                logger.warning(f"No BGP neighbors found for device {device_id}")
                return True  # Nothing to remove
            
            # Parse neighbors from output (this is a simplified approach)
            # For now, we'll remove all neighbors by getting them from the running config
            config_result = container.exec_run("vtysh -c 'show running-config' | grep 'neighbor '")
            if config_result.exit_code != 0:
                logger.info(f"No BGP neighbor configuration found for device {device_id}")
                return True
            
            # Extract neighbor IPs from the running configuration
            config_output = config_result.output.decode()
            neighbor_ips = []
            for line in config_output.split('\n'):
                if 'neighbor ' in line and 'remote-as' in line:
                    # Extract IP from line like " neighbor 192.168.1.1 remote-as 65001"
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] == 'neighbor':
                        neighbor_ips.append(parts[1])
            
            if not neighbor_ips:
                logger.info(f"No BGP neighbors to remove for device {device_id}")
                return True
            
            # Remove each neighbor
            for neighbor_ip in neighbor_ips:
                commands = [
                    "configure terminal",
                    f"router bgp {local_as}",
                    f"no neighbor {neighbor_ip}",
                    "end",
                    "write"
                ]
                
                cmd_list = ["vtysh"]
                for cmd in commands:
                    cmd_list.extend(["-c", cmd])
                
                logger.info(f"Removing BGP neighbor {neighbor_ip} for device {device_id}")
                result = container.exec_run(cmd_list)
                
                if result.exit_code == 0:
                    logger.info(f"Successfully removed BGP neighbor {neighbor_ip} for device {device_id}")
                else:
                    logger.error(f"Failed to remove BGP neighbor {neighbor_ip}: {result.output.decode()}")
            
            return True
                
        except Exception as e:
            logger.error(f"Failed to remove BGP neighbors for {device_id}: {e}")
            return False
    
    def _get_container_bgp_as(self, container) -> int:
        """Get the actual BGP AS number from the container's configuration."""
        try:
            # Get the running configuration and extract BGP AS
            result = container.exec_run("vtysh -c 'show running-config' | grep 'router bgp'")
            if result.exit_code == 0:
                output = result.output.decode()
                # Look for "router bgp <AS_NUMBER>"
                import re
                match = re.search(r'router bgp (\d+)', output)
                if match:
                    as_number = int(match.group(1))
                    logger.info(f"Found container BGP AS: {as_number}")
                    return as_number
        except Exception as e:
            logger.warning(f"Failed to get container BGP AS: {e}")
        return None
    
    def get_bgp_status(self, device_id: str, device_name: str = None) -> Dict:
        """Get BGP status for a device"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            container = self.client.containers.get(container_name)
            
            # Get BGP summary
            result = container.exec_run("vtysh -c 'show ip bgp summary'")
            
            if result.exit_code == 0:
                return {
                    'status': 'running',
                    'bgp_summary': result.output.decode(),
                    'container_status': container.status
                }
            else:
                return {
                    'status': 'error',
                    'error': result.output.decode(),
                    'container_status': container.status
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'container_status': 'not_found'
            }
    
    def get_bgp_neighbors(self, device_id: str, device_name: str = None) -> Dict:
        """Get BGP neighbors for a device"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            container = self.client.containers.get(container_name)
            
            # Get BGP neighbors
            result = container.exec_run("vtysh -c 'show ip bgp neighbors'")
            
            if result.exit_code == 0:
                return {
                    'status': 'success',
                    'neighbors': result.output.decode()
                }
            else:
                return {
                    'status': 'error',
                    'error': result.output.decode()
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def get_bgp_routes(self, device_id: str, device_name: str = None) -> Dict:
        """Get BGP routes for a device"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            container = self.client.containers.get(container_name)
            
            # Get BGP routes
            result = container.exec_run("vtysh -c 'show ip bgp'")
            
            if result.exit_code == 0:
                return {
                    'status': 'success',
                    'routes': result.output.decode()
                }
            else:
                return {
                    'status': 'error',
                    'error': result.output.decode()
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def list_containers(self) -> List[Dict]:
        """List all FRR containers"""
        try:
            containers = self.client.containers.list(
                filters={'name': self.container_prefix},
                all=True
            )
            
            return [
                {
                    'name': c.name,
                    'status': c.status,
                    'device_id': c.name.replace(f"{self.container_prefix}-", ""),
                    'created': c.attrs['Created']
                }
                for c in containers
            ]
            
        except Exception as e:
            logger.error(f"Failed to list FRR containers: {e}")
            return []

# Global instance
frr_manager = FRRDockerManager()

def start_frr_container(device_id: str, device_config: Dict) -> Optional[str]:
    """Start FRR container for a device"""
    return frr_manager.start_frr_container(device_id, device_config)

def stop_frr_container(device_id: str) -> bool:
    """Stop FRR container for a device"""
    return frr_manager.stop_frr_container(device_id)

def setup_frr_network() -> bool:
    """Set up FRR network infrastructure"""
    return frr_manager.setup_network_infrastructure()

def configure_bgp_neighbor(device_id: str, neighbor_config: Dict, device_name: str = None) -> bool:
    """Configure BGP neighbor for a device"""
    return frr_manager.configure_bgp_neighbor(device_id, neighbor_config, device_name)

def get_bgp_status(device_id: str, device_name: str = None) -> Dict:
    """Get BGP status for a device"""
    return frr_manager.get_bgp_status(device_id, device_name)

def get_bgp_neighbors(device_id: str, device_name: str = None) -> Dict:
    """Get BGP neighbors for a device"""
    return frr_manager.get_bgp_neighbors(device_id, device_name)

def get_bgp_routes(device_id: str, device_name: str = None) -> Dict:
    """Get BGP routes for a device"""
    return frr_manager.get_bgp_routes(device_id, device_name)

def list_all_containers() -> List[Dict]:
    """List all FRR containers"""
    return frr_manager.list_containers()
