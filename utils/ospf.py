import logging
import subprocess
import threading
from typing import Dict, Any, Optional

OSPF_INSTANCES = {}

def safe_vtysh_command(cmd_list, timeout=10, device_id=None, device_name=None):
    """Safely execute vtysh commands with proper error handling."""
    try:
        result = subprocess.run(
            cmd_list, 
            capture_output=True, 
            text=True, 
            timeout=timeout,
            check=True
        )
        logging.debug(f"[OSPF VTYSH] Command successful: {' '.join(cmd_list)}")
        return True, result.stdout
    except subprocess.TimeoutExpired:
        logging.error(f"[OSPF VTYSH] Command timeout: {' '.join(cmd_list)}")
        return False, "Command timeout"
    except subprocess.CalledProcessError as e:
        logging.error(f"[OSPF VTYSH] Command failed: {' '.join(cmd_list)} - {e.stderr}")
        return False, e.stderr
    except FileNotFoundError:
        logging.error(f"[OSPF VTYSH] vtysh command not found - FRR not installed")
        return False, "FRR not installed"

def configure_ospf_neighbor(device_id: str, ospf_config: Dict[str, Any], device_name: str = None) -> bool:
    """Configure OSPF for a device in FRR container."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[OSPF CONFIGURE] Configuring OSPF for device {device_name} ({device_id})")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Extract OSPF configuration
        area_id = ospf_config.get("area_id", "0.0.0.0")
        router_id = ospf_config.get("router_id", "")
        hello_interval = ospf_config.get("hello_interval", "10")
        dead_interval = ospf_config.get("dead_interval", "40")
        graceful_restart = ospf_config.get("graceful_restart", False)
        interface = ospf_config.get("interface", "eth0")
        ipv4_enabled = ospf_config.get("ipv4_enabled", True)
        ipv6_enabled = ospf_config.get("ipv6_enabled", False)
        
        # Get device IP addresses from database to calculate correct network
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device_data = device_db.get_device(device_id)
        
        logging.info(f"[OSPF CONFIGURE] Database lookup for device {device_id}: {device_data is not None}")
        if device_data:
            logging.info(f"[OSPF CONFIGURE] Device data: ipv4_address={device_data.get('ipv4_address')}, ipv4_mask={device_data.get('ipv4_mask')}")
        
        # Calculate IPv4 network from device IP
        ipv4_network = None
        if device_data and device_data.get("ipv4_address"):
            try:
                import ipaddress
                ipv4_addr = device_data["ipv4_address"]
                ipv4_mask = device_data.get("ipv4_mask", "24")
                network = ipaddress.IPv4Network(f"{ipv4_addr}/{ipv4_mask}", strict=False)
                ipv4_network = str(network)
                logging.info(f"[OSPF CONFIGURE] Calculated IPv4 network: {ipv4_network}")
            except Exception as e:
                logging.warning(f"[OSPF CONFIGURE] Failed to calculate IPv4 network: {e}")
                ipv4_network = "192.168.0.0/24"  # Fallback to default
        else:
            logging.warning(f"[OSPF CONFIGURE] No device data or IPv4 address found, using fallback network")
            ipv4_network = "192.168.0.0/24"  # Fallback to default
        
        # Build OSPF configuration commands
        vtysh_commands = ["configure terminal"]
        
        # Configure IPv4 OSPF if enabled
        if ipv4_enabled:
            vtysh_commands.extend([
                "router ospf",
            ])
            
            # Set router ID if provided
            if router_id:
                vtysh_commands.append(f" ospf router-id {router_id}")
            
            # Configure graceful restart if enabled
            if graceful_restart:
                vtysh_commands.append(" capability graceful-restart")
            
            # Configure network using actual device network
            if ipv4_network:
                vtysh_commands.extend([
                    f" network {ipv4_network} area {area_id}",
                    "exit"
                ])
            else:
                # Fallback if network calculation failed
                vtysh_commands.extend([
                    f" network 192.168.0.0/24 area {area_id}",
                    "exit"
                ])
            
            # Configure interface OSPF settings
            vtysh_commands.extend([
                f"interface {interface}",
                f" ip ospf hello-interval {hello_interval}",
                f" ip ospf dead-interval {dead_interval}",
                f" ip ospf area {area_id}",
                " no ip ospf passive",  # Use modern interface-level command
                "exit"
            ])
        
        # Configure IPv6 OSPF if enabled
        if ipv6_enabled:
            vtysh_commands.extend([
                "router ospf6",
            ])
            
            # Set router ID if provided
            if router_id:
                vtysh_commands.append(f" ospf6 router-id {router_id}")
            
            # Configure graceful restart if enabled
            if graceful_restart:
                vtysh_commands.append(" capability graceful-restart")
            
            # Calculate IPv6 network from device IP if available
            ipv6_network = None
            if device_data and device_data.get("ipv6_address"):
                try:
                    import ipaddress
                    ipv6_addr = device_data["ipv6_address"]
                    ipv6_mask = device_data.get("ipv6_mask", "64")
                    network = ipaddress.IPv6Network(f"{ipv6_addr}/{ipv6_mask}", strict=False)
                    ipv6_network = str(network)
                    logging.info(f"[OSPF CONFIGURE] Calculated IPv6 network: {ipv6_network}")
                except Exception as e:
                    logging.warning(f"[OSPF CONFIGURE] Failed to calculate IPv6 network: {e}")
            
            # Configure area range with calculated IPv6 network if available
            # OSPFv3 advertises all IPv6 addresses on the interface by default
            # The area range is optional and only needed for route summarization
            vtysh_commands.append("exit")
            
            # Configure interface OSPF6 settings
            vtysh_commands.extend([
                f"interface {interface}",
                f" ipv6 ospf6 hello-interval {hello_interval}",
                f" ipv6 ospf6 dead-interval {dead_interval}",
                f" ipv6 ospf6 area {area_id}",
                "exit"
            ])
        
        # Add end command to save configuration
        vtysh_commands.append("end")
        
        # Execute commands using here document
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[OSPF CONFIGURE] Executing OSPF configuration commands")
        logging.info(f"[OSPF CONFIGURE] Commands: {vtysh_commands}")
        logging.info(f"[OSPF CONFIGURE] Full command: {exec_cmd}")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        logging.info(f"[OSPF CONFIGURE] Command exit code: {result.exit_code}")
        logging.info(f"[OSPF CONFIGURE] Command output: {result.output.decode()}")
        
        if result.exit_code != 0:
            logging.error(f"[OSPF CONFIGURE] Command failed: {result.output.decode()}")
            return False
        else:
            logging.info(f"[OSPF CONFIGURE] ✅ OSPF configuration successful")
        
        # Store OSPF instance
        OSPF_INSTANCES[device_id] = {
            "area_id": area_id,
            "router_id": router_id,
            "hello_interval": hello_interval,
            "dead_interval": dead_interval,
            "graceful_restart": graceful_restart,
            "interface": interface,
            "ipv4_enabled": ipv4_enabled,
            "ipv6_enabled": ipv6_enabled,
            "active": True
        }
        
        logging.info(f"[OSPF CONFIGURE] ✅ Successfully configured OSPF for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[OSPF CONFIGURE] Error configuring OSPF: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False

def start_ospf_neighbor(device_id: str, ospf_config: Dict[str, Any], device_name: str = None, af: str = None) -> bool:
    """Start OSPF for a device by adding network configuration."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[OSPF START] Starting OSPF for device {device_name} ({device_id}) af={af}")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Get device IP addresses from database to calculate correct network
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device_data = device_db.get_device(device_id)
        
        # Calculate IPv4 network from device IP
        ipv4_network = None
        if device_data and device_data.get("ipv4_address"):
            try:
                import ipaddress
                ipv4_addr = device_data["ipv4_address"]
                ipv4_mask = device_data.get("ipv4_mask", "24")
                network = ipaddress.IPv4Network(f"{ipv4_addr}/{ipv4_mask}", strict=False)
                ipv4_network = str(network)
                logging.info(f"[OSPF START] Calculated IPv4 network: {ipv4_network}")
            except Exception as e:
                logging.warning(f"[OSPF START] Failed to calculate IPv4 network: {e}")
                ipv4_network = "192.168.0.0/24"  # Fallback to default
        else:
            logging.warning(f"[OSPF START] No device data or IPv4 address found, using fallback network")
            ipv4_network = "192.168.0.0/24"  # Fallback to default
        
        # Extract OSPF configuration
        area_id = ospf_config.get("area_id", "0.0.0.0")
        ipv4_enabled = ospf_config.get("ipv4_enabled", True)
        ipv6_enabled = ospf_config.get("ipv6_enabled", False)
        interface = ospf_config.get("interface", device_data.get("interface", "eth0"))
        
        logging.info(f"[OSPF START] Config: af={af}, area_id={area_id}, interface={interface}, ipv4_enabled={ipv4_enabled}, ipv6_enabled={ipv6_enabled}")
        
        # Build OSPF start commands
        vtysh_commands = ["configure terminal"]
        
        # Normalize AF input
        af_norm = (af or "").strip().lower() if isinstance(af, str) else None
        
        # IPv4-only start: add network statement, no shutdown
        if af_norm in ("ipv4",):
            vtysh_commands.append("router ospf")
            vtysh_commands.append(" no shutdown")
        if ipv4_enabled and ipv4_network:
            vtysh_commands.append(f" network {ipv4_network} area {area_id}")
            logging.info(f"[OSPF START] (IPv4) Adding network: {ipv4_network} area {area_id}")
            vtysh_commands.append("exit")
        
        # IPv6-only start: add interface area binding
        elif af_norm in ("ipv6",):
            vtysh_commands.extend([
                f"interface {interface}",
                f" ipv6 ospf6 area {area_id}",
                "exit",
            ])
            logging.info(f"[OSPF START] (IPv6) Adding interface {interface} area {area_id} binding")
        
        # Start both (legacy behavior)
        else:
            # IPv4
            vtysh_commands.append("router ospf")
            vtysh_commands.append(" no shutdown")
            if ipv4_enabled and ipv4_network:
                vtysh_commands.append(f" network {ipv4_network} area {area_id}")
                logging.info(f"[OSPF START] Adding network: {ipv4_network} area {area_id}")
            vtysh_commands.append("exit")
            
            # IPv6
            if ipv6_enabled:
                vtysh_commands.extend([
                    f"interface {interface}",
                    f" ipv6 ospf6 area {area_id}",
                    "exit",
                ])
        
        # Execute commands
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        
        logging.info(f"[OSPF START] Executing commands for {device_name} (af={af}):\n{config_commands}")
        result = container.exec_run(["bash", "-c", exec_cmd])
        
        if result.exit_code != 0:
            logging.error(f"[OSPF START] Command failed: {result.output.decode()}")
            return False
        
        # Update instance status
        if device_id in OSPF_INSTANCES:
            OSPF_INSTANCES[device_id]["active"] = True
        
        # Update database with OSPF status based on AF
        try:
            from datetime import datetime, timezone
            update_data = {
                'ospf_established': True,
                'ospf_state': 'Running',
                'last_ospf_check': datetime.now(timezone.utc).isoformat(),
                'ospf_manual_override': True,  # Flag to prevent monitor from overriding
                'ospf_manual_override_time': datetime.now(timezone.utc).isoformat()
            }
            
            # Update AF-specific status
            if af_norm in ("ipv4",):
                update_data['ospf_ipv4_running'] = True
                logging.info(f"[OSPF START] Updated OSPF (IPv4 only) status to Running for {device_name}")
            elif af_norm in ("ipv6",):
                update_data['ospf_ipv6_running'] = True
                logging.info(f"[OSPF START] Updated OSPF (IPv6 only) status to Running for {device_name}")
            else:
                # Legacy: update both
                update_data['ospf_ipv4_running'] = True
                if ipv6_enabled:
                    update_data['ospf_ipv6_running'] = True
            
            device_db.update_device(device_id, update_data)
            logging.info(f"[OSPF START] Updated OSPF status in database for device {device_name}")
            
            # Immediately check for neighbors and update database for instant UI feedback
            import time
            time.sleep(1)  # Brief pause for OSPF to establish neighbors
            try:
                actual_status = get_ospf_status(device_id)
                if actual_status:
                    actual_neighbors = actual_status.get('neighbors', [])
                    actual_ipv4_established = actual_status.get('ospf_ipv4_established', False)
                    actual_ipv6_established = actual_status.get('ospf_ipv6_established', False)
                    
                    # Update with actual neighbor information
                    import json
                    update_neighbors_data = {
                        'ospf_neighbors': json.dumps(actual_neighbors) if actual_neighbors else None,
                        'last_ospf_check': datetime.now(timezone.utc).isoformat(),
                    }
                    
                    # Get current device data for computing aggregate
                    current_device_data = device_db.get_device(device_id)
                    
                    # Update AF-specific established status based on actual neighbors
                    if af_norm == "ipv4":
                        update_neighbors_data['ospf_ipv4_established'] = actual_ipv4_established
                        # Recompute aggregate
                        current_ipv6_est = bool(current_device_data.get('ospf_ipv6_established', False) if current_device_data else False)
                        update_neighbors_data['ospf_established'] = actual_ipv4_established or current_ipv6_est
                        update_neighbors_data['ospf_state'] = 'Established' if update_neighbors_data['ospf_established'] else 'Running'
                    elif af_norm == "ipv6":
                        update_neighbors_data['ospf_ipv6_established'] = actual_ipv6_established
                        # Recompute aggregate
                        current_ipv4_est = bool(current_device_data.get('ospf_ipv4_established', False) if current_device_data else False)
                        update_neighbors_data['ospf_established'] = actual_ipv6_established or current_ipv4_est
                        update_neighbors_data['ospf_state'] = 'Established' if update_neighbors_data['ospf_established'] else 'Running'
                    else:
                        # Both AFs
                        update_neighbors_data['ospf_ipv4_established'] = actual_ipv4_established
                        update_neighbors_data['ospf_ipv6_established'] = actual_ipv6_established
                        update_neighbors_data['ospf_established'] = actual_ipv4_established or actual_ipv6_established
                        update_neighbors_data['ospf_state'] = 'Established' if update_neighbors_data['ospf_established'] else 'Running'
                    
                    device_db.update_device(device_id, update_neighbors_data)
                    logging.info(f"[OSPF START] Updated OSPF with immediate neighbor check: {len(actual_neighbors)} neighbors found")
            except Exception as e:
                logging.debug(f"[OSPF START] Could not immediately check neighbors: {e}")
                
        except Exception as e:
            logging.warning(f"[OSPF START] Failed to update OSPF status in database: {e}")
        
        logging.info(f"[OSPF START] ✅ Successfully started OSPF for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[OSPF START] Error starting OSPF: {e}")
        return False

def stop_ospf_neighbor(device_id: str, device_name: str = None, af: str = None) -> bool:
    """Stop OSPF for a device by removing network configuration."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[OSPF STOP] Stopping OSPF for device {device_name} ({device_id}) af={af}")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Get device IP addresses from database to calculate correct network
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device_data = device_db.get_device(device_id)
        
        # Calculate IPv4 network from device IP
        ipv4_network = None
        if device_data and device_data.get("ipv4_address"):
            try:
                import ipaddress
                ipv4_addr = device_data["ipv4_address"]
                ipv4_mask = device_data.get("ipv4_mask", "24")
                network = ipaddress.IPv4Network(f"{ipv4_addr}/{ipv4_mask}", strict=False)
                ipv4_network = str(network)
                logging.info(f"[OSPF STOP] Calculated IPv4 network: {ipv4_network}")
            except Exception as e:
                logging.warning(f"[OSPF STOP] Failed to calculate IPv4 network: {e}")
                ipv4_network = "192.168.0.0/24"  # Fallback to default
        else:
            logging.warning(f"[OSPF STOP] No device data or IPv4 address found, using fallback network")
            ipv4_network = "192.168.0.0/24"  # Fallback to default
        
        # Get OSPF configuration to determine area ID and interface
        ospf_config = device_data.get("ospf_config", {}) if device_data else {}
        area_id = ospf_config.get("area_id", "0.0.0.0")
        ipv4_enabled = ospf_config.get("ipv4_enabled", True)
        ipv6_enabled = ospf_config.get("ipv6_enabled", False)
        interface = ospf_config.get("interface", device_data.get("interface", "eth0"))
        
        logging.info(f"[OSPF STOP] Config: af={af}, area_id={area_id}, interface={interface}, ipv4_enabled={ipv4_enabled}, ipv6_enabled={ipv6_enabled}")
        
        # Build OSPF stop commands
        vtysh_commands = [
            "configure terminal",
        ]

        # Normalize AF input
        af_norm = (af or "").strip().lower() if isinstance(af, str) else None

        # IPv4-only stop: remove network statements, avoid global shutdown
        if af_norm in ("ipv4",):
            vtysh_commands.append("router ospf")
            if ipv4_enabled and ipv4_network:
                vtysh_commands.append(f" no network {ipv4_network} area {area_id}")
                logging.info(f"[OSPF STOP] (IPv4) Removing network: {ipv4_network} area {area_id}")
            vtysh_commands.append("exit")

        # IPv6-only stop: remove interface area binding only
        elif af_norm in ("ipv6",):
            # Always try to remove IPv6 interface binding if af=ipv6 is specified, 
            # regardless of ipv6_enabled flag (in case config is out of sync)
            vtysh_commands.extend([
                f"interface {interface}",
                f" no ipv6 ospf6 area {area_id}",
                "exit",
            ])
            logging.info(f"[OSPF STOP] (IPv6) Removing interface {interface} area {area_id} binding")

        # Stop both (legacy behavior)
        else:
            # IPv4
            vtysh_commands.append("router ospf")
        if ipv4_enabled and ipv4_network:
            vtysh_commands.append(f" no network {ipv4_network} area {area_id}")
            logging.info(f"[OSPF STOP] Removing network: {ipv4_network} area {area_id}")
            # Full shutdown when stopping both
            vtysh_commands.extend([" shutdown", "exit"])
        
            # IPv6
            if ipv6_enabled:
                vtysh_commands.extend([
                    "router ospf6",
                    " shutdown",
                    "exit",
                    f"interface {interface}",
                    f" no ipv6 ospf6 area {area_id}",
                    "exit"
                ])
        
        # Execute commands
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        
        logging.info(f"[OSPF STOP] Executing commands for {device_name} (af={af}):\n{config_commands}")
        result = container.exec_run(["bash", "-c", exec_cmd])
        
        output = result.output.decode() if result.output else ""
        if result.exit_code != 0:
            logging.error(f"[OSPF STOP] Command failed with exit code {result.exit_code}: {output}")
            return False
        else:
            logging.info(f"[OSPF STOP] Commands executed successfully. Output: {output}")
        
        # Update instance status
        if device_id in OSPF_INSTANCES:
            OSPF_INSTANCES[device_id]["active"] = False
        
        # Update database with OSPF status depending on AF
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()

            # Pull current flags to compute aggregate
            current_ipv4_est = bool(device_data.get('ospf_ipv4_established', False))
            current_ipv6_est = bool(device_data.get('ospf_ipv6_established', False))
            current_ipv4_run = bool(device_data.get('ospf_ipv4_running', False))
            current_ipv6_run = bool(device_data.get('ospf_ipv6_running', False))

            update_data = {
                'last_ospf_check': now,
                'ospf_manual_override': True,
                'ospf_manual_override_time': now
            }

            # Get current neighbors to filter by AF if needed
            import json
            current_neighbors_json = device_data.get('ospf_neighbors')
            current_neighbors = []
            if current_neighbors_json:
                try:
                    if isinstance(current_neighbors_json, str):
                        current_neighbors = json.loads(current_neighbors_json)
                    else:
                        current_neighbors = current_neighbors_json
                except:
                    current_neighbors = []
            
            if af_norm == "ipv4":
                # Filter out IPv4 neighbors, keep IPv6 neighbors
                filtered_neighbors = [n for n in current_neighbors if n.get("type") != "IPv4"]
                update_data.update({
                    'ospf_ipv4_running': False,
                    'ospf_ipv4_established': False,
                    'ospf_neighbors': json.dumps(filtered_neighbors) if filtered_neighbors else None,
                })
                # Preserve IPv6, recompute aggregate
                agg_est = bool(current_ipv6_est)
                update_data['ospf_established'] = agg_est
                update_data['ospf_state'] = 'Established' if agg_est else 'Down'
                logging.info(f"[OSPF STOP] Updated OSPF (IPv4 only) status to Down for {device_name}, cleared IPv4 neighbors")
            elif af_norm == "ipv6":
                # Filter out IPv6 neighbors, keep IPv4 neighbors
                filtered_neighbors = [n for n in current_neighbors if n.get("type") != "IPv6"]
                update_data.update({
                    'ospf_ipv6_running': False,
                    'ospf_ipv6_established': False,
                    'ospf_neighbors': json.dumps(filtered_neighbors) if filtered_neighbors else None,
                })
                agg_est = bool(current_ipv4_est)
                update_data['ospf_established'] = agg_est
                update_data['ospf_state'] = 'Established' if agg_est else 'Down'
                logging.info(f"[OSPF STOP] Updated OSPF (IPv6 only) status to Down for {device_name}, cleared IPv6 neighbors")
            else:
                # Clear all neighbors when stopping both
                update_data.update({
                    'ospf_established': False,
                    'ospf_state': 'Down',
                    'ospf_ipv4_running': False,
                    'ospf_ipv4_established': False,
                    'ospf_ipv6_running': False,
                    'ospf_ipv6_established': False,
                    'ospf_neighbors': None,  # Clear all neighbors
                })
                logging.info(f"[OSPF STOP] Updated OSPF (IPv4+IPv6) status to Down for {device_name}, cleared all neighbors")
            
            device_db.update_device(device_id, update_data)
            
            # Immediately verify OSPF status from FRR to ensure database reflects actual state
            # This helps catch cases where FRR might still show neighbors for a brief moment
            import time
            time.sleep(0.5)  # Brief pause for FRR to process the stop command
            try:
                actual_status = get_ospf_status(device_id)
                if actual_status:
                    # If actual status shows no neighbors or OSPF not running, ensure database is correct
                    actual_neighbors = actual_status.get('neighbors', [])
                    actual_ipv4_established = actual_status.get('ospf_ipv4_established', False)
                    actual_ipv6_established = actual_status.get('ospf_ipv6_established', False)
                    
                    # Update with actual status if different from what we set
                    if af_norm == "ipv4" and not actual_ipv4_established:
                        # Confirm IPv4 is actually down
                        logging.info(f"[OSPF STOP] Verified IPv4 OSPF is down in FRR")
                    elif af_norm == "ipv6" and not actual_ipv6_established:
                        # Confirm IPv6 is actually down
                        logging.info(f"[OSPF STOP] Verified IPv6 OSPF is down in FRR")
                    elif not af_norm and len(actual_neighbors) == 0:
                        # Both are down, confirm
                        logging.info(f"[OSPF STOP] Verified OSPF is down in FRR (no neighbors)")
            except Exception as e:
                logging.debug(f"[OSPF STOP] Could not verify FRR status immediately: {e}")
                
        except Exception as e:
            logging.warning(f"[OSPF STOP] Failed to update OSPF status in database: {e}")
        
        logging.info(f"[OSPF STOP] ✅ Successfully stopped OSPF for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[OSPF STOP] Error stopping OSPF: {e}")
        return False

def get_ospf_status(device_id: str) -> Optional[Dict[str, Any]]:
    """Get OSPF status for a device."""
    try:
        from utils.frr_docker import FRRDockerManager
        from docker.errors import NotFound
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, f"device_{device_id}")
        
        try:
            container = frr_manager.client.containers.get(container_name)
        except NotFound:
            logging.warning(f"[OSPF STATUS] Container {container_name} not found for device {device_id}")
            return None
        
        # Get OSPF neighbor information for both IPv4 and IPv6
        neighbors = []
        
        # Get IPv4 OSPF neighbors
        result_ipv4 = container.exec_run("vtysh -c 'show ip ospf neighbor'")
        if result_ipv4.exit_code == 0:
            output_ipv4 = result_ipv4.output.decode()
            
            # Check if OSPF is not enabled
            if 'OSPF is not enabled' in output_ipv4 or 'not configured' in output_ipv4.lower():
                logging.info(f"[OSPF STATUS] OSPF not enabled for device {device_id}")
                neighbors = []
            else:
                lines = output_ipv4.strip().split('\n')
                
                for line in lines:
                    # Skip header lines and empty lines
                    if ('Neighbor ID' in line or 'Pri' in line or 
                        line.startswith('Total') or not line.strip() or
                        line.startswith('%') or line.startswith('!')):
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 7:
                        # IPv4 OSPF format: Neighbor ID Pri State Up Time Dead Time Address Interface ...
                        neighbor_id = parts[0]
                        priority = parts[1]
                        state = parts[2]
                        up_time = parts[3]  # This is the neighbor uptime
                        dead_time = parts[4]
                        address = parts[5]
                        interface = parts[6]
                        
                        neighbors.append({
                            'neighbor_id': neighbor_id,
                            'priority': priority,
                            'state': state,
                            'up_time': up_time,  # Add neighbor uptime
                            'dead_time': dead_time,
                            'address': address,
                            'interface': interface,
                            'type': 'IPv4'
                        })
        
        # Get IPv6 OSPF neighbors
        result_ipv6 = container.exec_run("vtysh -c 'show ipv6 ospf6 neighbor'")
        if result_ipv6.exit_code == 0:
            output_ipv6 = result_ipv6.output.decode()
            
            # Check if OSPF6 is not enabled
            if 'OSPF6 is not enabled' in output_ipv6 or 'not configured' in output_ipv6.lower():
                logging.info(f"[OSPF STATUS] OSPF6 not enabled for device {device_id}")
                # Don't clear neighbors here, keep IPv4 neighbors if any
            else:
                lines = output_ipv6.strip().split('\n')
                
                for line in lines:
                    # Skip header lines and empty lines
                    if ('Neighbor ID' in line or 'Pri' in line or 
                        line.startswith('Total') or not line.strip() or
                        line.startswith('%') or line.startswith('!')):
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 6:
                        # IPv6 OSPF format: Neighbor ID Pri DeadTime State/IfState Duration I/F[State]
                        neighbor_id = parts[0]
                        priority = parts[1]
                        dead_time = parts[2]
                        state = parts[3]
                        duration = parts[4]  # This is the neighbor uptime
                        interface = parts[5]
                        
                        neighbors.append({
                            'neighbor_id': neighbor_id,
                            'priority': priority,
                            'state': state,
                            'up_time': duration,  # Add neighbor uptime
                            'dead_time': dead_time,
                            'address': duration,  # Keep duration as address for backward compatibility
                            'interface': interface,
                            'type': 'IPv6'
                        })
        
        # Get OSPF summary for IPv4
        result_ipv4_summary = container.exec_run("vtysh -c 'show ip ospf'")
        ospf_ipv4_summary = result_ipv4_summary.output.decode() if result_ipv4_summary.exit_code == 0 else ""
        
        # Get OSPF summary for IPv6
        result_ipv6_summary = container.exec_run("vtysh -c 'show ipv6 ospf6'")
        ospf_ipv6_summary = result_ipv6_summary.output.decode() if result_ipv6_summary.exit_code == 0 else ""
        logging.info(f"[OSPF STATUS] IPv6 summary exit_code: {result_ipv6_summary.exit_code}, output length: {len(ospf_ipv6_summary)}")
        
        # Extract uptime information
        ospf_ipv4_uptime = None
        ospf_ipv6_uptime = None
        
        # Extract IPv6 OSPF uptime from "Running HH:MM:SS" format
        if ospf_ipv6_summary:
            import re
            ipv6_uptime_match = re.search(r'Running\s+(\d+:\d+:\d+)', ospf_ipv6_summary)
            if ipv6_uptime_match:
                ospf_ipv6_uptime = ipv6_uptime_match.group(1)
                logging.info(f"[OSPF STATUS] IPv6 OSPF uptime: {ospf_ipv6_uptime}")
        
        # Extract IPv4 OSPF uptime from /proc filesystem
        try:
            # Try to find ospfd process by scanning /proc directories
            result_proc = container.exec_run("ls /proc")
            logging.info(f"[OSPF STATUS] Proc ls exit_code: {result_proc.exit_code}")
            logging.info(f"[OSPF STATUS] Proc ls output: {result_proc.output.decode()}")
            
            if result_proc.exit_code == 0:
                proc_dirs = result_proc.output.decode().strip().split('\n')
                ospfd_pid = None
                
                # Look for numeric directories (PIDs) and check their cmdline
                for proc_dir in proc_dirs:
                    if proc_dir.isdigit():
                        try:
                            result_cmdline = container.exec_run(f"cat /proc/{proc_dir}/cmdline")
                            if result_cmdline.exit_code == 0:
                                cmdline = result_cmdline.output.decode().strip()
                                if 'ospfd' in cmdline:
                                    ospfd_pid = proc_dir
                                    logging.info(f"[OSPF STATUS] Found ospfd PID: {ospfd_pid}")
                                    break
                        except:
                            continue
                
                if ospfd_pid:
                    # Get process start time from /proc/PID/stat
                    result_stat = container.exec_run(f"cat /proc/{ospfd_pid}/stat")
                    if result_stat.exit_code == 0:
                        stat_output = result_stat.output.decode().strip()
                        if stat_output:
                            # Parse /proc/PID/stat to get start time
                            # Format: pid comm state ppid pgrp session tty_nr tpgid flags minflt cminflt majflt cmajflt utime stime cutime cstime priority nice num_threads itrealvalue starttime
                            stat_fields = stat_output.split()
                            if len(stat_fields) >= 22:
                                starttime_ticks = int(stat_fields[21])  # starttime is field 22 (index 21)
                                
                                # Get system uptime to calculate process uptime
                                result_uptime = container.exec_run("cat /proc/uptime")
                                if result_uptime.exit_code == 0:
                                    uptime_output = result_uptime.output.decode().strip()
                                    if uptime_output:
                                        system_uptime_seconds = float(uptime_output.split()[0])
                                        
                                        # Calculate process uptime
                                        # starttime is in clock ticks, convert to seconds
                                        # Clock ticks per second is typically 100
                                        clock_ticks_per_second = 100
                                        process_start_seconds = starttime_ticks / clock_ticks_per_second
                                        process_uptime_seconds = system_uptime_seconds - process_start_seconds
                                        
                                        # Convert to HH:MM:SS format
                                        hours = int(process_uptime_seconds // 3600)
                                        minutes = int((process_uptime_seconds % 3600) // 60)
                                        seconds = int(process_uptime_seconds % 60)
                                        ospf_ipv4_uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                                        logging.info(f"[OSPF STATUS] IPv4 OSPF uptime: {ospf_ipv4_uptime}")
                                    else:
                                        logging.warning(f"[OSPF STATUS] No uptime data from /proc/uptime")
                                else:
                                    logging.warning(f"[OSPF STATUS] Could not read /proc/uptime")
                            else:
                                logging.warning(f"[OSPF STATUS] Invalid /proc/PID/stat format")
                        else:
                            logging.warning(f"[OSPF STATUS] No stat data for ospfd process")
                    else:
                        logging.warning(f"[OSPF STATUS] Could not read /proc/{ospfd_pid}/stat")
                else:
                    logging.warning(f"[OSPF STATUS] No ospfd process found in /proc")
            else:
                logging.warning(f"[OSPF STATUS] Proc ls command failed with exit_code: {result_proc.exit_code}")
        except Exception as e:
            logging.warning(f"[OSPF STATUS] Could not get IPv4 OSPF process uptime: {e}")
        
        logging.info(f"[OSPF STATUS] IPv4 OSPF uptime: {ospf_ipv4_uptime}")
        
        # Determine overall OSPF status for IPv4 and IPv6
        ospf_ipv4_established = len([n for n in neighbors if n.get('type') == 'IPv4']) > 0 and any('Full' in n['state'] for n in neighbors if n.get('type') == 'IPv4')
        ospf_ipv6_established = len([n for n in neighbors if n.get('type') == 'IPv6']) > 0 and any('Full' in n['state'] for n in neighbors if n.get('type') == 'IPv6')
        
        # Check if OSPF is running even without neighbors
        ospf_ipv4_running = 'OSPF Routing Process' in ospf_ipv4_summary
        ospf_ipv6_running = 'OSPFv3 Routing Process' in ospf_ipv6_summary
        
        # Overall OSPF status
        ospf_established = ospf_ipv4_established or ospf_ipv6_established
        
        return {
            'ospf_established': ospf_established,
            'ospf_state': 'Established' if ospf_established else 'Not Established',
            'neighbors': neighbors,
            'summary': ospf_ipv4_summary,
            'ospf_ipv4_summary': ospf_ipv4_summary,
            'ospf_ipv6_summary': ospf_ipv6_summary,
            'ospf_ipv4_running': ospf_ipv4_running,
            'ospf_ipv6_running': ospf_ipv6_running,
            'ospf_ipv4_established': ospf_ipv4_established,
            'ospf_ipv6_established': ospf_ipv6_established,
            'ospf_ipv4_uptime': ospf_ipv4_uptime,
            'ospf_ipv6_uptime': ospf_ipv6_uptime
        }
        
    except Exception as e:
        logging.error(f"[OSPF STATUS] Error getting OSPF status: {e}")
        return None

def start_ospf(device_id, iface, config):
    """Legacy placeholder for manual triggering — use `build_ospf_cmd()`."""
    cmd = build_ospf_cmd(device_id, iface, config)
    logging.info(f"[OSPF] Would run: {cmd}")
    OSPF_INSTANCES[device_id] = {
        "iface": iface,
        "area": config.get("area_id", "0.0.0.0"),
        "graceful_restart": config.get("graceful_restart", False)
    }

def build_ospf_cmd(device_id, iface, config):
    area_id = config.get("area_id", "0.0.0.0")
    gr = config.get("graceful_restart", False)

    cmds = [
        "configure terminal",
        f"interface {iface}",
        f"  ip ospf area {area_id}",
        f"exit",
        "router ospf",
        f"  network 0.0.0.0/0 area {area_id}"
    ]

    if gr:
        cmds.append("  capability graceful-restart")

    cmds.extend(["exit", "write"])

    return ['vtysh'] + [f'-c "{line}"' for line in cmds]

def build_ospf_stop_cmd(device_id, iface):
    instance = OSPF_INSTANCES.get(device_id)
    area_id = instance["area"] if instance else "0.0.0.0"

    cmds = [
        "configure terminal",
        f"interface {iface}",
        f"  no ip ospf area {area_id}",
        "exit",
        "no router ospf",
        "write"
    ]
    return ['vtysh'] + [f'-c "{line}"' for line in cmds]

def stop_ospf(device_id, iface):
    if device_id in OSPF_INSTANCES:
        logging.info(f"[OSPF] Stopping OSPF on {iface} for device {device_id}")
        del OSPF_INSTANCES[device_id]
    else:
        logging.warning(f"[OSPF] No active OSPF found for device {device_id}")

def cleanup_device_routes(device_id):
    """Clean up OSPF routes for a specific device."""
    try:
        logging.info(f"[OSPF CLEANUP] Cleaning up OSPF routes for device {device_id}")
        
        # Remove from OSPF instances if exists
        if device_id in OSPF_INSTANCES:
            del OSPF_INSTANCES[device_id]
            logging.info(f"[OSPF CLEANUP] Removed OSPF instance for device {device_id}")
        
        return True
    except Exception as e:
        logging.error(f"[OSPF CLEANUP] Failed to cleanup routes for device {device_id}: {e}")
        return False

def remove_ospf_config(device_id):
    """Remove OSPF configuration for a specific device."""
    try:
        logging.info(f"[OSPF CLEANUP] Removing OSPF configuration for device {device_id}")
        
        # This would typically involve removing OSPF configuration from FRR
        # For now, just clean up the instance
        if device_id in OSPF_INSTANCES:
            del OSPF_INSTANCES[device_id]
            logging.info(f"[OSPF CLEANUP] Removed OSPF configuration for device {device_id}")
        
        return True
    except Exception as e:
        logging.error(f"[OSPF CLEANUP] Failed to remove OSPF config for device {device_id}: {e}")
        return False

def cleanup_all_ospf_routes():
    """Clean up all OSPF routes and configurations."""
    try:
        logging.info("[OSPF CLEANUP] Cleaning up all OSPF routes and configurations")
        
        # Clear all OSPF instances
        OSPF_INSTANCES.clear()
        logging.info("[OSPF CLEANUP] Cleared all OSPF instances")
        
        return True
    except Exception as e:
        logging.error(f"[OSPF CLEANUP] Failed to cleanup all OSPF routes: {e}")
        return False
