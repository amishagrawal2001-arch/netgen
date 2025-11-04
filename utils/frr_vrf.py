"""
VRF-based FRR Container Management for OSTG
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
from typing import Dict, Optional, List

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FRRVRFManager:
    """Manages FRR containers using VRF for network isolation"""
    
    def __init__(self):
        self.client = docker.from_env()
        self.container_prefix = "ostg-frr-vrf"
        self.image_name = "ostg-frr:latest"
        self.vrf_table_base = 1000  # Starting VRF table number
    
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
    
    def _get_vrf_name(self, device_id: str) -> str:
        """Get VRF name for device."""
        return f"vrf-{device_id}"
    
    def _get_vrf_table(self, device_id: str) -> int:
        """Get VRF table number for device (deterministic based on device_id)."""
        # Use hash of device_id to get consistent table number
        import hashlib
        hash_obj = hashlib.md5(device_id.encode())
        hash_int = int(hash_obj.hexdigest()[:8], 16)
        return self.vrf_table_base + (hash_int % 1000)  # Ensure table number is reasonable
    
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
    
    def start_frr_container(self, device_id: str, device_name: str, interface: str, 
                          bgp_config: Dict, ipv4: str = None, ipv6: str = None) -> bool:
        """Start FRR container with VRF networking."""
        try:
            container_name = self._get_container_name(device_id)
            
            # Create VRF for this device
            if not self.create_vrf_for_device(device_id, interface, ipv4, ipv6):
                return False
            
            # Check if container already exists
            try:
                existing_container = self.client.containers.get(container_name)
                if existing_container.status == "running":
                    logger.info(f"[VRF] Container {container_name} already running")
                    return True
                else:
                    existing_container.remove(force=True)
                    logger.info(f"[VRF] Removed existing stopped container {container_name}")
            except docker.errors.NotFound:
                pass
            
            # Environment variables for FRR
            env_vars = {
                'FRR_DAEMONS': 'bgpd ospfd',
                'FRR_BGP_AS': str(bgp_config.get('bgp_asn', 65000)),
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
            if bgp_config:
                self._configure_bgp_in_container(container_name, bgp_config, ipv4, ipv6)
            
            return True
            
        except Exception as e:
            logger.error(f"[VRF] Failed to start FRR container for device {device_id}: {e}")
            return False
    
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
    
    def stop_frr_container(self, device_id: str, interface: str) -> bool:
        """Stop FRR container and clean up VRF."""
        try:
            container_name = self._get_container_name(device_id)
            
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
    
    def get_bgp_status(self, device_id: str) -> Dict:
        """Get BGP status from container."""
        try:
            container_name = self._get_container_name(device_id)
            container = self.client.containers.get(container_name)
            
            result = container.exec_run("vtysh -c 'show bgp summary'")
            
            if result.exit_code == 0:
                output = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                return {"status": "success", "output": output}
            else:
                return {"status": "error", "message": "Failed to get BGP status"}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_bgp_neighbors(self, device_id: str) -> Dict:
        """Get BGP neighbors from container."""
        try:
            container_name = self._get_container_name(device_id)
            container = self.client.containers.get(container_name)
            
            result = container.exec_run("vtysh -c 'show bgp neighbors'")
            
            if result.exit_code == 0:
                output = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                return {"status": "success", "output": output}
            else:
                return {"status": "error", "message": "Failed to get BGP neighbors"}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
