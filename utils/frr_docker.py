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
    
    def _get_container_name(self, device_id: str, device_name: str = None, dhcp_mode: Optional[str] = None) -> str:
        """Get container name from device_id. DHCP clients use a dedicated prefix."""
        inferred_mode = (dhcp_mode or "").lower()
        if not inferred_mode and device_id:
            try:
                from utils.device_database import DeviceDatabase
                device_db = DeviceDatabase()
                record = device_db.get_device(device_id)
                if record:
                    inferred_mode = (record.get("dhcp_mode") or "").lower()
            except Exception:
                inferred_mode = ""
        prefix = "dhcp-frr" if inferred_mode == "client" else self.container_prefix
        return f"{prefix}-{device_id}"
    
    def _get_router_id(self, device_id: str, device_config: Dict = None, ipv4: str = None) -> str:
        """
        Get router-id for protocols, preferring loopback IPv4 over interface IPv4.
        
        Args:
            device_id: Device ID
            device_config: Device configuration dict (may contain loopback_ipv4)
            ipv4: Interface IPv4 address as fallback
            
        Returns:
            Router ID (IPv4 address)
        """
        dhcp_mode = ""
        if device_config:
            dhcp_mode = (device_config.get('dhcp_mode') or '').lower()
        
        # First, try to get loopback IPv4 from device_config
        if device_config:
            loopback_ipv4 = device_config.get('loopback_ipv4')
            if loopback_ipv4 and loopback_ipv4.strip():
                router_id = loopback_ipv4.strip().split('/')[0]
                logger.info(f"[FRR] Using loopback IPv4 {router_id} as router-id")
                return router_id
        
        # If not in device_config, try to get from database
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_data = device_db.get_device(device_id)
            if device_data:
                if not dhcp_mode:
                    dhcp_mode = (device_data.get('dhcp_mode') or '').lower()
                loopback_ipv4 = device_data.get('loopback_ipv4')
                if loopback_ipv4 and loopback_ipv4.strip():
                    router_id = loopback_ipv4.strip().split('/')[0]
                    logger.info(f"[FRR] Using loopback IPv4 {router_id} from database as router-id")
                    return router_id
        except Exception as e:
            logger.debug(f"[FRR] Could not retrieve loopback IPv4 from database: {e}")
        
        if dhcp_mode == "client":
            logger.info(f"[FRR] DHCP client device {device_id}: deferring router-id configuration until lease acquired")
            return ""
        
        # Fallback to interface IPv4
        if ipv4:
            router_id = ipv4.split('/')[0]
            logger.info(f"[FRR] Using interface IPv4 {router_id} as router-id (fallback)")
            return router_id
        
        # Last resort: default
        logger.warning(f"[FRR] No IPv4 available, using derived default router-id")
        return self._derive_router_id_from_device_id(device_id)

    def _derive_router_id_from_device_id(self, device_id: Optional[str]) -> str:
        """
        Generate a deterministic router-id from the device UUID so DHCP clients
        have unique, but stable, identifiers until a lease arrives.
        """
        if not device_id:
            return "1.1.1.1"
        cleaned = ''.join(ch for ch in device_id if ch.isalnum())
        if len(cleaned) < 8:
            cleaned = cleaned.ljust(8, '0')
        try:
            raw = bytes.fromhex(cleaned[:8])
        except ValueError:
            return "1.1.1.1"
        octets = list(raw[:4])
        # Ensure we have 4 octets
        while len(octets) < 4:
            octets.append(1)
        # Avoid 0.0.0.0 router-ids
        octets = [octet or 1 for octet in octets]
        return ".".join(str(octet) for octet in octets)
    
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
            dhcp_mode = (device_config.get('dhcp_mode') or '').lower()
            container_name = self._get_container_name(device_id, device_name, dhcp_mode=dhcp_mode)
            
            # Check if container already exists
            try:
                existing_container = self.client.containers.get(container_name)
                if existing_container.status == "running":
                    logger.info(f"[FRR] Container {container_name} already running")
                    # Ensure global router-id is configured (may have been missing)
                    self._configure_global_router_id(container_name, device_id, device_config)
                    return container_name
                else:
                    existing_container.remove(force=True)
                    logger.info(f"[FRR] Removed existing stopped container {container_name}")
            except docker.errors.NotFound:
                pass
            
            # Get router-id (preferring loopback IPv4)
            router_id = self._get_router_id(device_id, device_config, device_config.get('ipv4'))
            
            # Determine interface name (with VLAN if applicable)
            interface = device_config.get('interface', '')
            vlan = device_config.get('vlan', '0')
            
            # CRITICAL: Validate interface name when VLAN is not used
            # Do not fall back to 'eth0' as it's the container's internal interface, not the host interface
            if vlan and vlan != "0":
                iface_name = f"vlan{vlan}"
            elif interface:
                iface_name = interface
            else:
                # Interface is required - log error and return None
                logger.error(f"[FRR] Interface name is required when VLAN is not specified for device {device_id}")
                return None
            
            dhcp_mode = (device_config.get('dhcp_mode') or '').lower()

            # Get IPv4 and IPv6 addresses from device_config
            ipv4 = device_config.get('ipv4', '')
            ipv6 = device_config.get('ipv6', '')
            
            # Extract IPv4 address and mask
            if ipv4 and '/' in ipv4:
                ipv4_addr, ipv4_mask = ipv4.split('/', 1)
            elif ipv4:
                ipv4_addr = ipv4
                ipv4_mask = '24'
            else:
                if dhcp_mode == "client":
                    ipv4_addr = ''
                    ipv4_mask = ''
                else:
                    ipv4_addr = '192.168.0.2'
                    ipv4_mask = '24'
            
            # Extract IPv6 address and mask
            ipv6_addr = ''
            ipv6_mask = ''
            if ipv6:
                if '/' in ipv6:
                    ipv6_addr, ipv6_mask = ipv6.split('/', 1)
                else:
                    ipv6_addr = ipv6
                    ipv6_mask = '64'
            
            # Get loopback IPs from device_config or database
            loopback_ipv4 = device_config.get('loopback_ipv4', '')
            loopback_ipv6 = device_config.get('loopback_ipv6', '')
            
            # If not in device_config, try to get from database
            if not loopback_ipv4 or not loopback_ipv6:
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id) if device_id else None
                    if device_data:
                        if not loopback_ipv4:
                            loopback_ipv4 = device_data.get('loopback_ipv4', '')
                        if not loopback_ipv6:
                            loopback_ipv6 = device_data.get('loopback_ipv6', '')
                except Exception as e:
                    logger.debug(f"[FRR] Could not retrieve loopback IPs from database: {e}")
            
            # Clean loopback IPs (remove /32 or /128 if present)
            if loopback_ipv4:
                loopback_ipv4 = loopback_ipv4.split('/')[0]
            else:
                loopback_ipv4 = router_id  # Use router_id as fallback
            
            if loopback_ipv6:
                loopback_ipv6 = loopback_ipv6.split('/')[0]
            
            # Calculate network from IPv4
            network = ipv4_addr.rsplit('.', 1)[0] + '.0' if ipv4_addr else ''
            
            # Environment variables for FRR template
            env_vars = {
                'FRR_DAEMONS': 'bgpd ospfd isisd',
                'LOCAL_ASN': str(device_config.get('bgp_asn', 65000)),
                'ROUTER_ID': router_id,  # Use loopback IPv4 if available, otherwise interface IPv4
                'DEVICE_NAME': device_config.get('device_name', f'device_{device_id}'),
                'NETWORK': network if dhcp_mode != "client" else '',
                'NETMASK': (ipv4_mask or '') if dhcp_mode != "client" else '',
                'INTERFACE': iface_name,  # Use determined interface name (vlan20, etc.)
                'VLAN': str(vlan or ''),
                'IP_ADDRESS': (ipv4_addr or '') if dhcp_mode != "client" else '',
                'IP_MASK': (ipv4_mask or '') if dhcp_mode != "client" else '',
                'LOOPBACK_IPV4': loopback_ipv4,
            }
            
            # Add DHCP mode for conditional startup logic
            env_vars['DHCP_MODE'] = dhcp_mode

            # Add IPv6 environment variables if IPv6 is configured
            if ipv6_addr:
                env_vars['IPV6_ADDRESS'] = ipv6_addr
                env_vars['IPV6_MASK'] = ipv6_mask
            
            # Add loopback IPv6 if configured
            if loopback_ipv6:
                env_vars['LOOPBACK_IPV6'] = loopback_ipv6
            
            # BGP neighbor config lines will be empty (added dynamically via vtysh)
            env_vars['BGP_NEIGHBOR_CONFIG_LINES'] = ''
            
            # VXLAN config will be empty (not used by default)
            env_vars['VXLAN_CONFIG_LINE'] = ''
            
            # Start container with host networking
            device_config['router_id'] = router_id
            device_config['dhcp_mode'] = dhcp_mode

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
            
            # Configure interfaces (IP addresses and loopback) first
            self._configure_interfaces(container_name, device_id, device_config)
            
            # Configure global router-id (must be loopback IPv4)
            self._configure_global_router_id(container_name, device_id, device_config)
            
            # BGP configuration is now handled by bgp.py, not here
            # Container is ready for protocol configuration
            
            return container_name
            
        except Exception as e:
            logger.error(f"[FRR] Failed to start FRR container for device {device_id}: {e}")
            return None
    
    def _configure_interfaces(self, container_name: str, device_id: str, device_config: Dict = None) -> bool:
        """
        Configure interface IP addresses and loopback in FRR container.
        This ensures interface configuration persists even with integrated-vtysh-config.
        
        Args:
            container_name: Container name
            device_id: Device ID
            device_config: Device configuration dict (optional)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            
            # Get interface information from device_config or database
            interface = device_config.get('interface', '') if device_config else ''
            vlan = device_config.get('vlan', '0') if device_config else '0'
            
            # CRITICAL: Validate interface name when VLAN is not used
            # Do not fall back to 'eth0' as it's the container's internal interface, not the host interface
            if vlan and vlan != "0":
                iface_name = f"vlan{vlan}"
            elif interface:
                iface_name = interface
            else:
                # Interface is required - log error and return False
                logger.error(f"[FRR] Interface name is required when VLAN is not specified for device {device_id}")
                return False
            
            # Get IP addresses
            dhcp_mode = (device_config.get('dhcp_mode') or '').lower() if device_config else ''
            ipv4 = device_config.get('ipv4', '') if device_config else ''
            ipv6 = device_config.get('ipv6', '') if device_config else ''
            
            # Extract IPv4 address and mask
            if ipv4 and '/' in ipv4:
                ipv4_addr, ipv4_mask = ipv4.split('/', 1)
            elif ipv4:
                ipv4_addr = ipv4
                ipv4_mask = '24'
            else:
                if dhcp_mode == "client":
                    ipv4_addr = ''
                    ipv4_mask = ''
                else:
                    ipv4_addr = '192.168.0.2'
                    ipv4_mask = '24'
            
            # Extract IPv6 address and mask
            ipv6_addr = ''
            ipv6_mask = ''
            if ipv6:
                if '/' in ipv6:
                    ipv6_addr, ipv6_mask = ipv6.split('/', 1)
                else:
                    ipv6_addr = ipv6
                    ipv6_mask = '64'
            
            # Get loopback IPs
            loopback_ipv4 = device_config.get('loopback_ipv4', '') if device_config else ''
            loopback_ipv6 = device_config.get('loopback_ipv6', '') if device_config else ''
            
            logger.info(f"[FRR] _configure_interfaces called with loopback_ipv4={loopback_ipv4}, loopback_ipv6={loopback_ipv6} from device_config")
            
            # If not in device_config, try to get from database
            if not loopback_ipv4 or not loopback_ipv6:
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id) if device_id else None
                    if device_data:
                        if not loopback_ipv4:
                            loopback_ipv4 = device_data.get('loopback_ipv4', '')
                            logger.info(f"[FRR] Retrieved loopback_ipv4={loopback_ipv4} from database")
                        if not loopback_ipv6:
                            loopback_ipv6 = device_data.get('loopback_ipv6', '')
                            logger.info(f"[FRR] Retrieved loopback_ipv6={loopback_ipv6} from database")
                except Exception as e:
                    logger.warning(f"[FRR] Could not retrieve loopback IPs from database: {e}")
            
            # Clean loopback IPs
            router_id = ''
            if device_config:
                router_id = (device_config.get('router_id') or '').split('/')[0]

            if loopback_ipv4:
                loopback_ipv4 = loopback_ipv4.split('/')[0]
            elif ipv4_addr:
                loopback_ipv4 = ipv4_addr
                logger.info(f"[FRR] Using interface IPv4 {ipv4_addr} as loopback fallback")
            elif router_id:
                loopback_ipv4 = router_id
                logger.info(f"[FRR] Using router_id {router_id} as loopback fallback")
            else:
                loopback_ipv4 = '1.1.1.1'
                logger.info(f"[FRR] Using default loopback 1.1.1.1")
            
            if loopback_ipv6:
                loopback_ipv6 = loopback_ipv6.split('/')[0]
            
            logger.info(f"[FRR] Final loopback values: loopback_ipv4={loopback_ipv4}, loopback_ipv6={loopback_ipv6}")
            
            # Build vtysh commands for interface configuration
            vtysh_commands = ["configure terminal", f"interface {iface_name}"]

            if ipv4_addr:
                vtysh_commands.append(f" ip address {ipv4_addr}/{ipv4_mask}")
            else:
                vtysh_commands.append(" no ip address")
            
            if ipv6_addr:
                vtysh_commands.append(f" ipv6 address {ipv6_addr}/{ipv6_mask}")
            
            vtysh_commands.extend([
                " no shutdown",
                "exit",
            ])
            
            # Note: Loopback IPs are now configured by OSPF/ISIS protocol configuration, not here
            # This ensures loopback IPs are only configured when OSPF/ISIS are enabled
            # Loopback interface will be configured by the protocol-specific functions
            vtysh_commands.extend([
                "end"
            ])
            
            # CRITICAL: Wait for mgmtd to be running before attempting vtysh commands
            # FRR 10.0 with integrated-vtysh-config requires mgmtd to be running
            max_wait = 10  # Wait up to 10 seconds
            wait_interval = 1  # Check every second
            mgmtd_running = False
            for i in range(max_wait):
                check_result = container.exec_run(["bash", "-c", "pgrep -f mgmtd > /dev/null && echo 'running' || echo 'not_running'"])
                check_output = check_result.output.decode('utf-8') if isinstance(check_result.output, bytes) else str(check_result.output)
                if 'running' in check_output.strip():
                    mgmtd_running = True
                    logger.info(f"[FRR] mgmtd is running (waited {i} seconds)")
                    break
                else:
                    logger.debug(f"[FRR] Waiting for mgmtd to start... ({i+1}/{max_wait})")
                    time.sleep(wait_interval)
            
            if not mgmtd_running:
                logger.warning(f"[FRR] mgmtd is not running after {max_wait} seconds, attempting to start it manually")
                # Try to start mgmtd manually
                start_mgmtd_result = container.exec_run(["bash", "-c", "/usr/lib/frr/mgmtd -d -A 127.0.0.1 2>&1 || true"])
                time.sleep(2)  # Give mgmtd time to start
                # Check again
                check_result = container.exec_run(["bash", "-c", "pgrep -f mgmtd > /dev/null && echo 'running' || echo 'not_running'"])
                check_output = check_result.output.decode('utf-8') if isinstance(check_result.output, bytes) else str(check_result.output)
                if 'running' in check_output.strip():
                    mgmtd_running = True
                    logger.info(f"[FRR] Successfully started mgmtd manually")
                else:
                    logger.warning(f"[FRR] mgmtd still not running, loopback configuration may fail")
            
            # Execute commands
            config_commands = "\n".join(vtysh_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            
            logger.info(f"[FRR] Executing loopback configuration commands in container {container_name} (mgmtd_running={mgmtd_running})")
            logger.debug(f"[FRR] Full command sequence:\n{config_commands}")
            result = container.exec_run(["bash", "-c", exec_cmd])
            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
            if result.exit_code != 0:
                logger.error(f"[FRR] Failed to configure interfaces in container {container_name}: exit_code={result.exit_code}, output={output_str}")
                return False
            else:
                logger.info(f"[FRR] Loopback configuration command executed successfully (exit_code=0)")
                if output_str:
                    logger.info(f"[FRR] vtysh command output: {output_str}")
                
                # CRITICAL: Manually update FRR config file to include loopback IPs
                # FRR 10.0 with integrated-vtysh-config doesn't always persist interface IPs
                # We need to manually edit /etc/frr/frr.conf to ensure loopback IPs are saved
                try:
                    config_file = "/etc/frr/frr.conf"
                    read_result = container.exec_run(["bash", "-c", f"cat {config_file}"])
                    config_content = read_result.output.decode('utf-8') if isinstance(read_result.output, bytes) else str(read_result.output)
                    
                    # Check if loopback interface section exists
                    lines = config_content.split('\n')
                    new_lines = []
                    in_loopback_section = False
                    loopback_has_ipv4 = False
                    loopback_has_ipv6 = False
                    loopback_section_found = False
                    loopback_section_index = -1
                    
                    # First pass: find if loopback section exists and check for IPs
                    for i, line in enumerate(lines):
                        if line.strip() == "interface lo":
                            loopback_section_found = True
                            loopback_section_index = i
                            in_loopback_section = True
                            # Check if IPs are already in the next few lines
                            for j in range(i+1, min(i+20, len(lines))):
                                if lines[j].strip().startswith("interface ") and "lo" not in lines[j]:
                                    break
                                if lines[j].strip() == "!" or lines[j].strip() == "exit":
                                    if j > i + 1:  # Only break if we've seen at least one line after interface lo
                                        break
                                if loopback_ipv4 and f"ip address {loopback_ipv4}/32" in lines[j]:
                                    loopback_has_ipv4 = True
                                if loopback_ipv6 and f"ipv6 address {loopback_ipv6}/128" in lines[j]:
                                    loopback_has_ipv6 = True
                            break
                    
                    # Second pass: build new config
                    in_loopback_section = False
                    for i, line in enumerate(lines):
                        if line.strip() == "interface lo":
                            in_loopback_section = True
                            new_lines.append(line)
                            # Add IPs if not present
                            if loopback_ipv4 and not loopback_has_ipv4:
                                new_lines.append(f" ip address {loopback_ipv4}/32")
                                logger.info(f"[FRR] Manually adding loopback IPv4 {loopback_ipv4}/32 to config file")
                            if loopback_ipv6 and not loopback_has_ipv6:
                                new_lines.append(f" ipv6 address {loopback_ipv6}/128")
                                logger.info(f"[FRR] Manually adding loopback IPv6 {loopback_ipv6}/128 to config file")
                        elif in_loopback_section and (line.strip().startswith("interface ") or (line.strip() == "!" and i > loopback_section_index + 1)):
                            in_loopback_section = False
                            new_lines.append(line)
                        else:
                            new_lines.append(line)
                    
                    # If loopback section doesn't exist, add it before the first router section
                    if not loopback_section_found and (loopback_ipv4 or loopback_ipv6):
                        logger.info(f"[FRR] Loopback interface section not found, creating it")
                        # Find where to insert (before first router section or at end of interfaces)
                        insert_index = -1
                        for i, line in enumerate(new_lines):
                            if line.strip().startswith("router "):
                                insert_index = i
                                break
                        
                        if insert_index > 0:
                            # Insert before router section
                            loopback_section = ["!"]
                            if loopback_ipv4:
                                loopback_section.append("interface lo")
                                loopback_section.append(f" ip address {loopback_ipv4}/32")
                            if loopback_ipv6:
                                if not loopback_ipv4:
                                    loopback_section.append("interface lo")
                                loopback_section.append(f" ipv6 address {loopback_ipv6}/128")
                            loopback_section.append("exit")
                            loopback_section.append("!")
                            new_lines = new_lines[:insert_index] + loopback_section + new_lines[insert_index:]
                            logger.info(f"[FRR] Created new loopback interface section in config file")
                        else:
                            # Append at end
                            new_lines.append("!")
                            if loopback_ipv4:
                                new_lines.append("interface lo")
                                new_lines.append(f" ip address {loopback_ipv4}/32")
                            if loopback_ipv6:
                                if not loopback_ipv4:
                                    new_lines.append("interface lo")
                                new_lines.append(f" ipv6 address {loopback_ipv6}/128")
                            new_lines.append("exit")
                            logger.info(f"[FRR] Appended loopback interface section to config file")
                    
                    # Write updated config back
                    updated_config = '\n'.join(new_lines)
                    write_result = container.exec_run(["bash", "-c", f"cat > {config_file} << 'CONFIGEOF'\n{updated_config}\nCONFIGEOF"])
                    if write_result.exit_code == 0:
                        logger.info(f"[FRR] Successfully updated FRR config file with loopback IPs")
                        # Reload FRR configuration
                        reload_result = container.exec_run(["bash", "-c", "vtysh -c 'configure terminal' -c 'end' -c 'reload' 2>&1 || true"])
                        logger.debug(f"[FRR] FRR reload result: {reload_result.output.decode('utf-8') if isinstance(reload_result.output, bytes) else str(reload_result.output)}")
                    else:
                        logger.warning(f"[FRR] Failed to write updated config file: {write_result.output.decode('utf-8') if isinstance(write_result.output, bytes) else str(write_result.output)}")
                except Exception as e:
                    logger.warning(f"[FRR] Failed to manually update FRR config file: {e}")
                
                # CRITICAL: Also configure loopback IP directly using ip command as a fallback
                # Sometimes FRR's vtysh doesn't immediately apply the IP to the kernel
                # This ensures the IP is actually configured on the interface
                if loopback_ipv4:
                    ip_cmd = f"ip addr add {loopback_ipv4}/32 dev lo 2>&1 || ip addr replace {loopback_ipv4}/32 dev lo 2>&1"
                    ip_result = container.exec_run(["bash", "-c", ip_cmd])
                    ip_output = ip_result.output.decode('utf-8') if isinstance(ip_result.output, bytes) else str(ip_result.output)
                    if ip_result.exit_code == 0:
                        logger.info(f"[FRR] Successfully configured loopback IPv4 {loopback_ipv4}/32 directly via ip command")
                    else:
                        logger.warning(f"[FRR] Failed to configure loopback IPv4 via ip command (may already exist): {ip_output}")
                
                if loopback_ipv6:
                    ip6_cmd = f"ip -6 addr add {loopback_ipv6}/128 dev lo 2>&1 || ip -6 addr replace {loopback_ipv6}/128 dev lo 2>&1"
                    ip6_result = container.exec_run(["bash", "-c", ip6_cmd])
                    ip6_output = ip6_result.output.decode('utf-8') if isinstance(ip6_result.output, bytes) else str(ip6_result.output)
                    if ip6_result.exit_code == 0:
                        logger.info(f"[FRR] Successfully configured loopback IPv6 {loopback_ipv6}/128 directly via ip command")
                    else:
                        logger.warning(f"[FRR] Failed to configure loopback IPv6 via ip command (may already exist): {ip6_output}")
            
            # Verify loopback was configured by checking both running config and saved config
            verify_cmd = "echo '=== Running Config ===' && vtysh -c 'show running-config' | grep -A 5 'interface lo' || echo 'Loopback not found in running config'; echo '=== Saved Config ===' && cat /etc/frr/frr.conf | grep -A 5 'interface lo' || echo 'Loopback not found in saved config'"
            verify_result = container.exec_run(["bash", "-c", verify_cmd])
            verify_output = verify_result.output.decode('utf-8') if isinstance(verify_result.output, bytes) else str(verify_result.output)
            logger.info(f"[FRR] Loopback verification output:\n{verify_output}")
            
            # Also check if loopback IP is actually configured on the interface
            ip_check_cmd = f"ip addr show lo | grep -E '(inet|inet6)' || echo 'No IPs found on lo'; echo '=== Expected IPv4: {loopback_ipv4}/32 ==='; echo '=== Expected IPv6: {loopback_ipv6}/128 ==='"
            ip_check_result = container.exec_run(["bash", "-c", ip_check_cmd])
            ip_check_output = ip_check_result.output.decode('utf-8') if isinstance(ip_check_result.output, bytes) else str(ip_check_result.output)
            logger.info(f"[FRR] Loopback IP check output:\n{ip_check_output}")
            
            # CRITICAL: Check if the loopback IP is actually present
            if loopback_ipv4:
                check_ipv4_cmd = f"ip addr show lo | grep -q '{loopback_ipv4}/32' && echo 'Loopback IPv4 {loopback_ipv4}/32 is configured' || echo 'Loopback IPv4 {loopback_ipv4}/32 is NOT configured'"
                check_ipv4_result = container.exec_run(["bash", "-c", check_ipv4_cmd])
                check_ipv4_output = check_ipv4_result.output.decode('utf-8') if isinstance(check_ipv4_result.output, bytes) else str(check_ipv4_result.output)
                logger.info(f"[FRR] Loopback IPv4 verification: {check_ipv4_output}")
            
            if loopback_ipv6:
                check_ipv6_cmd = f"ip addr show lo | grep -q '{loopback_ipv6}/128' && echo 'Loopback IPv6 {loopback_ipv6}/128 is configured' || echo 'Loopback IPv6 {loopback_ipv6}/128 is NOT configured'"
                check_ipv6_result = container.exec_run(["bash", "-c", check_ipv6_cmd])
                check_ipv6_output = check_ipv6_result.output.decode('utf-8') if isinstance(check_ipv6_result.output, bytes) else str(check_ipv6_result.output)
                logger.info(f"[FRR] Loopback IPv6 verification: {check_ipv6_output}")
            
            logger.info(f"[FRR] ✅ Successfully configured interfaces (including loopback {loopback_ipv4}/32) in container {container_name}")
            return True
            
        except Exception as e:
            logger.error(f"[FRR] Error configuring interfaces in container {container_name}: {e}")
            return False
    
    def _configure_global_router_id(self, container_name: str, device_id: str, device_config: Dict = None) -> bool:
        """
        Configure global router-id in FRR container.
        Router-id must be loopback IPv4 if available.
        
        Args:
            container_name: Container name
            device_id: Device ID
            device_config: Device configuration dict (optional)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            container = self.client.containers.get(container_name)
            
            # Get router-id (must be loopback IPv4)
            loopback_ipv4 = None
            
            # First, try to get loopback IPv4 from device_config
            if device_config:
                loopback_ipv4 = device_config.get('loopback_ipv4')
                if loopback_ipv4 and loopback_ipv4.strip():
                    loopback_ipv4 = loopback_ipv4.strip().split('/')[0]
            
            # If not in device_config, try to get from database
            if not loopback_ipv4:
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id)
                    if device_data:
                        loopback_ipv4 = device_data.get('loopback_ipv4')
                        if loopback_ipv4 and loopback_ipv4.strip():
                            loopback_ipv4 = loopback_ipv4.strip().split('/')[0]
                except Exception as e:
                    logger.debug(f"[FRR] Could not retrieve loopback IPv4 from database: {e}")
            
            dhcp_mode = (device_config.get('dhcp_mode') or '').lower() if device_config else ''
            
            # Router ID must be loopback IPv4
            if loopback_ipv4:
                router_id = loopback_ipv4
                logger.info(f"[FRR] Using loopback IPv4 {router_id} as global router-id")
            else:
                # Fallback to interface IPv4 if loopback not available
                ipv4 = device_config.get('ipv4') if device_config else None
                if ipv4:
                    router_id = ipv4.split('/')[0] if '/' in ipv4 else ipv4
                    logger.warning(f"[FRR] Loopback IPv4 not found, using interface IPv4 {router_id} as global router-id (fallback)")
                elif dhcp_mode == "client":
                    logger.info(f"[FRR] DHCP client device {device_id}: no router-id configured until lease provides an address")
                    return True
                else:
                    router_id = "192.168.0.2"
                    logger.warning(f"[FRR] No IPv4 available, using default router-id {router_id}")
            
            # Configure global router-id using vtysh
            vtysh_commands = [
                "configure terminal",
                f"ip router-id {router_id}",
                "exit",
            ]
            
            # Execute commands using here-doc to maintain context
            config_commands = "\n".join(vtysh_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            
            logger.info(f"[FRR] Configuring global router-id {router_id} in container {container_name}")
            result = container.exec_run(["bash", "-c", exec_cmd])
            
            if result.exit_code == 0:
                logger.info(f"[FRR] ✅ Successfully configured global router-id {router_id} in container {container_name}")
                return True
            else:
                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                logger.warning(f"[FRR] Failed to configure global router-id in container {container_name}: {output_str}")
                return False
                
        except Exception as e:
            logger.error(f"[FRR] Failed to configure global router-id for container {container_name}: {e}")
            import traceback
            logger.error(f"[FRR] Traceback: {traceback.format_exc()}")
            return False
    
    def stop_frr_container(self, device_id: str, device_name: str = None, remove: bool = False) -> bool:
        """Stop (and optionally remove) FRR container"""
        try:
            container_name = self._get_container_name(device_id, device_name)
            
            # Stop container without removing it so configuration/state is preserved
            try:
                container = self.client.containers.get(container_name)
                
                # Before stopping, remove loopback IP addresses if device info is available
                if remove:
                    try:
                        from utils.device_database import DeviceDatabase
                        device_db = DeviceDatabase()
                        device_data = device_db.get_device(device_id) if device_id else None
                        
                        if device_data:
                            loopback_ipv4 = device_data.get('loopback_ipv4', '')
                            loopback_ipv6 = device_data.get('loopback_ipv6', '')
                            
                            if loopback_ipv4 or loopback_ipv6:
                                logger.info(f"[FRR] Removing loopback IPs from container {container_name} before removal")
                                
                                # Build vtysh commands to remove loopback IPs
                                vtysh_commands = [
                                    "configure terminal",
                                    "interface lo",
                                ]
                                
                                # Remove IPv4 loopback if configured
                                if loopback_ipv4:
                                    loopback_ipv4_clean = loopback_ipv4.split('/')[0] if '/' in loopback_ipv4 else loopback_ipv4
                                    vtysh_commands.append(f" no ip address {loopback_ipv4_clean}/32")
                                    logger.info(f"[FRR] Removing loopback IPv4 {loopback_ipv4_clean}/32 from container {container_name}")
                                
                                # Remove IPv6 loopback if configured
                                if loopback_ipv6:
                                    loopback_ipv6_clean = loopback_ipv6.split('/')[0] if '/' in loopback_ipv6 else loopback_ipv6
                                    vtysh_commands.append(f" no ipv6 address {loopback_ipv6_clean}/128")
                                    logger.info(f"[FRR] Removing loopback IPv6 {loopback_ipv6_clean}/128 from container {container_name}")
                                
                                vtysh_commands.extend([
                                    "exit",
                                    "exit",
                                ])
                                
                                # Execute commands using here-doc to maintain context
                                config_commands = "\n".join(vtysh_commands)
                                exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                                
                                try:
                                    loopback_result = container.exec_run(["bash", "-c", exec_cmd], timeout=10)
                                    if loopback_result.exit_code == 0:
                                        logger.info(f"[FRR] Successfully removed loopback IPs from container {container_name}")
                                    else:
                                        output_str = loopback_result.output.decode('utf-8') if isinstance(loopback_result.output, bytes) else str(loopback_result.output)
                                        logger.warning(f"[FRR] Failed to remove loopback IPs from container {container_name}: {output_str}")
                                except Exception as loopback_error:
                                    logger.warning(f"[FRR] Error removing loopback IPs from container {container_name}: {loopback_error}")
                    except Exception as cleanup_error:
                        logger.warning(f"[FRR] Could not remove loopback IPs before container removal: {cleanup_error}")
                        # Continue with container removal even if loopback cleanup fails
                
                logger.info(f"[FRR] Stopping container {container_name}")
                container.stop(timeout=10)
                if remove:
                    logger.info(f"[FRR] Removing container {container_name}")
                    container.remove(force=True)
                    logger.info(f"[FRR] Container {container_name} removed successfully")
                else:
                    logger.info(f"[FRR] Container {container_name} stopped successfully (not removed)")
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

def stop_frr_container(device_id: str, device_name: str = None, remove: bool = False) -> bool:
    """Stop (and optionally remove) FRR container for device."""
    return frr_manager.stop_frr_container(device_id, device_name, remove=remove)

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
        
        # Add BGP router-id (must be loopback IPv4)
        # Extract device_id from container_name
        device_id = container_name.replace(f"{frr_manager.container_prefix}-", "")
        # Get router-id (must be loopback IPv4)
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
            logger.debug(f"[FRR] Could not retrieve loopback IPv4 from database: {e}")
        
        # Router ID must be loopback IPv4
        if loopback_ipv4:
            router_id = loopback_ipv4
            logger.info(f"[FRR] Using loopback IPv4 {router_id} as router-id")
        else:
            # Fallback to update_source if loopback not available
            if update_source:
                router_id = update_source.split('/')[0] if '/' in update_source else update_source
                logger.warning(f"[FRR] Loopback IPv4 not found, using update_source {router_id} as router-id (fallback)")
            else:
                router_id = "192.168.0.2"
                logger.warning(f"[FRR] No IPv4 available, using default router-id {router_id}")
        
        # Router-id and global knobs are managed by configure_bgp_for_device.
        # Avoid re-applying them here because FRR treats repeated graceful-restart
        # statements as config changes that return an error code.
        
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