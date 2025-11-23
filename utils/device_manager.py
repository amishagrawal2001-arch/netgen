import logging
import subprocess
import re
import threading
from typing import Dict, Any, List, Tuple
from ipaddress import ip_interface, ip_network, ip_address

from utils import ospf, bgp
from utils import vxlan as vxlan_utils

# --------------------------------------------------------------------
# Globals / state
# --------------------------------------------------------------------
ACTIVE_DEVICES: Dict[str, Dict[str, Any]] = {}
_ACTIVE_LOCK = threading.Lock()

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _normalize_iface(iface: str, vlan: str = "0") -> str:
    s = (iface or "").strip().strip('"').rstrip(",")
    if not s:
        return ""
    if " - " in s:
        s = s.split(" - ", 1)[-1].strip()
    if ":" in s:
        s = s.rsplit(":", 1)[-1].strip()
    parts = s.split()
    base_interface = parts[-1] if parts else ""
    
    # If this is a VLAN device, check which interface naming convention exists
    if vlan and vlan != "0":
        # Check if old naming convention exists (vlan20@enp180s0np0)
        old_vlan_iface = f"vlan{vlan}@{base_interface}"
        new_vlan_iface = f"vlan{vlan}"
        
        # Check which interface actually exists
        try:
            result_old = subprocess.run(["ip", "link", "show", old_vlan_iface], capture_output=True)
            result_new = subprocess.run(["ip", "link", "show", new_vlan_iface], capture_output=True)
            
            if result_old.returncode == 0:
                return old_vlan_iface
            elif result_new.returncode == 0:
                return new_vlan_iface
            else:
                # Neither exists, return new convention as default
                return new_vlan_iface
        except Exception:
            # If we can't check, return new convention as default
            return new_vlan_iface
    else:
        return base_interface




def _remove_iface_addr_exact_ip(iface: str, target_ip: str, is_v6: bool) -> bool:
    """
    Remove the address whose IP equals target_ip (ignoring prefix length).
    This prevents deleting a different host in the same subnet.
    """
    try:
        t_ip = ip_address(target_ip)
    except Exception as e:
        logging.warning(f"[REMOVE] Invalid target IP '{target_ip}' for {iface}: {e}")
        return False

    v4_list, v6_list = _list_iface_addrs(iface)
    cand_list = v6_list if is_v6 else v4_list

    logging.debug(f"[REMOVE] Existing addrs on {iface} -> {'; '.join(cand_list) or '(none)'}")
    for existing in cand_list:
        try:
            ex = ip_interface(existing)
            if ex.ip == t_ip:
                argv = ["ip"]
                if is_v6:
                    argv.append("-6")
                argv += ["addr", "del", ex.with_prefixlen, "dev", iface]
                _run(argv)
                logging.info(f"[REMOVE] Deleted {'IPv6' if is_v6 else 'IPv4'} {ex.with_prefixlen} from {iface}")
                return True
        except Exception as e:
            logging.debug(f"[REMOVE] Skip '{existing}' on {iface}: {e}")

    logging.warning(f"[REMOVE] Exact {'IPv6' if is_v6 else 'IPv4'} {target_ip} not found on {iface}")
    return False

def _run(argv: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """
    Lightweight subprocess runner with logging.
    Accepts argv as a list (preferred).
    """
    try:
        cp = subprocess.run(argv, check=check, text=True, capture_output=True)
        if cp.stdout:
            logging.debug(cp.stdout.strip())
        if cp.stderr:
            logging.debug(cp.stderr.strip())
        return cp
    except subprocess.CalledProcessError as e:
        logging.error(f"[RUN ERROR] rc={e.returncode} cmd={' '.join(argv)}")
        if e.stdout:
            logging.error(e.stdout.strip())
        if e.stderr:
            logging.error(e.stderr.strip())
        raise

def _list_iface_addrs(iface: str) -> Tuple[List[str], List[str]]:
    """
    Return (ipv4 CIDRs, ipv6 CIDRs) configured on iface.
    """
    v4, v6 = [], []
    try:
        out4 = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], text=True)
    except Exception:
        out4 = ""
    try:
        out6 = subprocess.check_output(["ip", "-6", "addr", "show", "dev", iface], text=True)
    except Exception:
        out6 = ""

    for line in out4.splitlines():
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)\b", line)
        if m:
            v4.append(m.group(1))

    for line in out6.splitlines():
        m = re.search(r"\binet6\s+([0-9a-fA-F:]+/\d+)\b", line)
        if m:
            v6.append(m.group(1))

    return v4, v6

def _remove_iface_addr_by_prefix(iface: str, cidr: str, is_v6: bool) -> bool:
    """
    Remove the address on iface whose NETWORK matches the provided cidr (e.g., 10.1.2.3/24).
    Returns True if something was removed.
    """
    try:
        target = ip_interface(cidr)
        target_net = target.network
    except Exception as e:
        logging.warning(f"[REMOVE] Invalid CIDR '{cidr}' for iface {iface}: {e}")
        return False

    v4_list, v6_list = _list_iface_addrs(iface)
    cand_list = v6_list if is_v6 else v4_list

    for existing in cand_list:
        try:
            existing_iface = ip_interface(existing)
            if ip_network(existing_iface.with_prefixlen, strict=False) == target_net:
                argv = ["ip"]
                if is_v6:
                    argv.append("-6")
                argv += ["addr", "del", existing_iface.with_prefixlen, "dev", iface]
                _run(argv)
                logging.info(f"[REMOVE] {'IPv6' if is_v6 else 'IPv4'} {existing_iface.with_prefixlen} removed from {iface}")
                return True
        except Exception as e:
            logging.debug(f"[REMOVE] Skipping CIDR '{existing}' on {iface}: {e}")

    logging.warning(f"[REMOVE] No {'IPv6' if is_v6 else 'IPv4'} address in {target_net} found on {iface}")
    return False

# --------------------------------------------------------------------
# DeviceManager
# --------------------------------------------------------------------
class DeviceManager:
    @staticmethod
    def start_device_protocols(device_data: Dict[str, Any]) -> Dict[str, Any]:
        device_id = device_data.get("device_id")
        device_name = device_data.get("device_name")
        iface_raw = device_data.get("interface", "")
        iface = _normalize_iface(iface_raw)
        protocols = device_data.get("protocols", []) or []

        logging.info(f"[DEVICE START] ID={device_id} Name='{device_name}' Interface='{iface}' Protocols={protocols}")

        result: Dict[str, Any] = {
            "device_id": device_id,
            "device": device_name,
            "interface": iface,
            "protocols_started": [],
        }

        vxlan_config = vxlan_utils.normalize_config(device_data.get("vxlan_config"))
        frr_manager = None
        container_name = None
        if device_id:
            try:
                from utils.frr_docker import FRRDockerManager
                frr_manager = FRRDockerManager()
                container_name = frr_manager._get_container_name(device_id, device_name or "")
            except Exception:
                frr_manager = None
                container_name = None
        if "VXLAN" in protocols and vxlan_config:
            try:
                vxlan_utils.ensure_vxlan_interface(
                    device_id,
                    device_name or device_id,
                    vxlan_config,
                    container_name=container_name,
                    frr_manager=frr_manager,
                )
                result.setdefault("vxlan", {})["state"] = "Configured"
            except Exception as exc:
                logging.warning(f"[VXLAN START] Failed to ensure VXLAN for {device_id}: {exc}")
                result.setdefault("vxlan", {})["error"] = str(exc)

        # OSPF
        if "OSPF" in protocols:
            ospf_cfg = device_data.get("ospf", {}) or {}
            try:
                ospf_cmd = ospf.build_ospf_cmd(device_id, iface, ospf_cfg)
                logging.info(f"[OSPF] Starting: {ospf_cmd}")
                _run(ospf_cmd if isinstance(ospf_cmd, list) else ospf_cmd.split())
                result["protocols_started"].append("OSPF")
            except Exception as e:
                logging.error(f"[OSPF ERROR] {e}")

        # BGP (handled in start_device API endpoint, not here)
        # BGP is started via bgp.start_bgp() in the /api/device/start endpoint
        # to ensure proper Docker container integration and field mapping
        if "BGP" in protocols:
            logging.info(f"[BGP] BGP protocol start handled by API endpoint")
            result["protocols_started"].append("BGP")

        with _ACTIVE_LOCK:
            # store normalized iface so later remove() matches
            ACTIVE_DEVICES[device_id] = {**device_data, "interface": iface}

        return result

    @staticmethod
    def stop_device_protocols(device_data: Dict[str, Any]) -> Dict[str, Any]:
        device_id = device_data.get("device_id")
        device_name = device_data.get("device_name")
        iface_raw = device_data.get("interface", "")
        iface = _normalize_iface(iface_raw)
        protocols = device_data.get("protocols", []) or []
        bgp_config = device_data.get("bgp", {}) or {}

        logging.info(f"[DEVICE STOP] ID={device_id} Name='{device_name}' Interface='{iface}' Protocols={protocols}")

        result: Dict[str, Any] = {
            "device_id": device_id,
            "device": device_name,
            "interface": iface,
            "protocols_stopped": [],
        }

        # OSPF stop
        if "OSPF" in protocols:
            try:
                ospf_cmd = ospf.build_ospf_stop_cmd(device_id, iface)
                logging.info(f"[OSPF] Stopping: {ospf_cmd}")
                _run(ospf_cmd if isinstance(ospf_cmd, list) else ospf_cmd.split())
                result["protocols_stopped"].append("OSPF")
            except Exception as e:
                logging.error(f"[OSPF ERROR] {e}")

        # BGP stop (neighbor shutdown)
        if "BGP" in protocols:
            try:
                # Use Docker-based BGP neighbor shutdown
                from utils.frr_docker import FRRDockerManager
                frr_manager = FRRDockerManager()
                
                # Get BGP status first to check if container is running
                bgp_status = frr_manager.get_bgp_status(device_id, device_name)
                logging.info(f"[BGP STOP DEBUG] bgp_status type: {type(bgp_status)}, value: {bgp_status}")
                
                # Check if bgp_status is a dictionary and has the expected structure
                if isinstance(bgp_status, dict) and bgp_status.get('status') == 'running':
                    # Parse BGP summary to extract neighbor information
                    bgp_summary = bgp_status.get('bgp_summary', '')
                    local_as = None
                    neighbors = []
                    
                    # Parse local AS from summary (e.g., "BGP router identifier 20.0.0.1, local AS number 300")
                    import re
                    as_match = re.search(r'local AS number (\d+)', bgp_summary)
                    if as_match:
                        local_as = as_match.group(1)
                    
                    # Parse neighbor information from BGP summary
                    # Look for lines like: "20.0.0.250      4        400       336       312        0    0    0 00:11:25     (Policy) (Policy) N/A"
                    neighbor_pattern = r'(\d+\.\d+\.\d+\.\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+([^\s]+)'
                    neighbor_matches = re.findall(neighbor_pattern, bgp_summary)
                    
                    for neighbor_ip, state in neighbor_matches:
                        neighbors.append({
                            'neighbor_ip': neighbor_ip,
                            'state': state
                        })
                    
                    logging.info(f"[BGP STOP DEBUG] Parsed {len(neighbors)} neighbors from BGP summary")
                    
                    if local_as and neighbors:
                        # Send neighbor shutdown commands for each neighbor
                        shutdown_success = True
                        for neighbor in neighbors:
                            neighbor_ip = neighbor.get('neighbor_ip')
                            neighbor_state = neighbor.get('state', '')
                            
                            # Skip if neighbor is already shut down
                            if 'Idle' in neighbor_state and 'Admin' in neighbor_state:
                                logging.info(f"[BGP] Neighbor {neighbor_ip} already shut down (state: {neighbor_state})")
                                continue
                                
                            if neighbor_ip:
                                logging.info(f"[BGP] Shutting down neighbor {neighbor_ip} under ASN {local_as}")
                                # Send shutdown command via Docker exec
                                container_name = frr_manager._get_container_name(device_id, device_name)
                                try:
                                    container = frr_manager.client.containers.get(container_name)
                                    shutdown_cmd = f"vtysh -c 'configure terminal' -c 'router bgp {local_as}' -c 'neighbor {neighbor_ip} shutdown' -c 'end' -c 'write memory'"
                                    exec_result = container.exec_run(shutdown_cmd)
                                    if exec_result.exit_code == 0:
                                        logging.info(f"[BGP] Successfully shut down neighbor {neighbor_ip}")
                                    else:
                                        # Handle bytes output properly
                                        output_str = exec_result.output.decode('utf-8') if isinstance(exec_result.output, bytes) else str(exec_result.output)
                                        logging.warning(f"[BGP] Failed to shut down neighbor {neighbor_ip}: {output_str}")
                                        shutdown_success = False
                                except Exception as container_error:
                                    logging.error(f"[BGP] Container error for neighbor {neighbor_ip}: {container_error}")
                                    shutdown_success = False
                        
                        if shutdown_success:
                            result["protocols_stopped"].append("BGP")
                            logging.info(f"[BGP] Successfully stopped BGP for device {device_id}")
                    elif not neighbors:
                        logging.warning(f"[BGP] No BGP neighbors found for device {device_id}")
                    else:
                        logging.warning(f"[BGP] Could not determine local AS for device {device_id}")
                else:
                    logging.warning(f"[BGP] BGP container not running for device {device_id}")
                    
            except Exception as e:
                logging.error(f"[BGP ERROR] {e}")

        with _ACTIVE_LOCK:
            ACTIVE_DEVICES.pop(device_id, None)

        vxlan_config = vxlan_utils.normalize_config(device_data.get("vxlan_config"))
        if "VXLAN" in protocols and vxlan_config:
            frr_manager = None
            container_name = None
            if device_id:
                try:
                    from utils.frr_docker import FRRDockerManager
                    frr_manager = FRRDockerManager()
                    container_name = frr_manager._get_container_name(device_id, device_name or "")
                except Exception:
                    frr_manager = None
                    container_name = None
            try:
                vxlan_utils.tear_down_vxlan_interface(
                    device_id,
                    vxlan_config,
                    container_name=container_name,
                    frr_manager=frr_manager,
                )
            except Exception as exc:
                logging.debug(f"[VXLAN STOP] Failed to tear down VXLAN for {device_id}: {exc}")

        # Ensure result only contains JSON-serializable data
        # Check if result is a dictionary before calling .get()
        if isinstance(result, dict):
            clean_result = {
                "device_id": result.get("device_id"),
                "device": result.get("device"),
                "interface": result.get("interface"),
                "protocols_stopped": result.get("protocols_stopped", [])
            }
        else:
            # Fallback if result is not a dictionary
            clean_result = {
                "device_id": device_id,
                "device": device_name,
                "interface": iface,
                "protocols_stopped": []
            }
        
        return clean_result

    @staticmethod
    def remove_device_protocols(device_data: Dict[str, Any]) -> Dict[str, Any]:
        device_id = device_data.get("device_id")
        device_name = device_data.get("device_name", "Device")
        iface_raw = device_data.get("interface", "")
        protocols = device_data.get("protocols", []) or []

        # Prefer the config we actually started with (ACTIVE_DEVICES), fall back to passed data
        with _ACTIVE_LOCK:
            prior = ACTIVE_DEVICES.get(device_id, {})

        ipv4 = (prior.get("ipv4") or device_data.get("ipv4") or "").strip()
        ipv6 = (prior.get("ipv6") or device_data.get("ipv6") or "").strip()
        ipv4_mask = (prior.get("ipv4_mask") or device_data.get("ipv4_mask") or "24").strip()
        ipv6_mask = (prior.get("ipv6_mask") or device_data.get("ipv6_mask") or "64").strip()
        
        # Get VLAN information for proper interface normalization
        vlan = (prior.get("vlan") or device_data.get("vlan") or "0").strip()
        iface = _normalize_iface(iface_raw, vlan)

        logging.info(f"[DEVICE REMOVE] ID={device_id} Name='{device_name}' Interface='{iface}' Protocols={protocols}")

        result: Dict[str, Any] = {
            "device_id": device_id,
            "device": device_name,
            "interface": iface,
            "protocols_removed": [],
            "ip_removed": {"ipv4": False, "ipv6": False},
            "errors": {},
        }

        # OSPF
        if "OSPF" in protocols:
            try:
                import utils.ospf as ospf
                # Use Docker-based OSPF cleanup instead of system vtysh commands
                logging.info(f"[OSPF] Cleaning up OSPF configuration for device {device_id}")
                ospf.cleanup_device_routes(device_id)
                ospf.remove_ospf_config(device_id)
                result["protocols_removed"].append("OSPF")
                logging.info(f"[OSPF] Successfully cleaned up OSPF for device {device_id}")
            except Exception as e:
                msg = f"{e.__class__.__name__}: {e}"
                result["errors"]["OSPF"] = msg
                logging.warning(f"[OSPF REMOVE ERROR] {msg} - continuing with device removal")

        # BGP
        if "BGP" in protocols:
            try:
                import utils.bgp as bgp
                bgp.remove_bgp_config(device_id)
                logging.info(f"[BGP] Config removed from FRR for device {device_id}")
                result["protocols_removed"].append("BGP")
            except Exception as e:
                msg = f"{e.__class__.__name__}: {e}"
                result["errors"]["BGP"] = msg
                logging.warning(f"[BGP REMOVE ERROR] {msg}")

        # Remove interface IPs we configured (EXACT IP match)
        if ipv4:
            try:
                removed4 = _remove_iface_addr_exact_ip(iface, ipv4, is_v6=False)
                result["ip_removed"]["ipv4"] = removed4
                logging.info(f"[REMOVE] IPv4 {ipv4} removed from {iface}: {removed4}")
            except Exception as e:
                msg = f"{e.__class__.__name__}: {e}"
                result["errors"]["IPv4"] = msg
                logging.warning(f"[REMOVE] Failed to remove IPv4 from {iface}: {msg}")

        if ipv6:
            try:
                removed6 = _remove_iface_addr_exact_ip(iface, ipv6, is_v6=True)
                result["ip_removed"]["ipv6"] = removed6
                logging.info(f"[REMOVE] IPv6 {ipv6} removed from {iface}: {removed6}")
            except Exception as e:
                msg = f"{e.__class__.__name__}: {e}"
                result["errors"]["IPv6"] = msg
                logging.warning(f"[REMOVE] Failed to remove IPv6 from {iface}: {msg}")

        with _ACTIVE_LOCK:
            ACTIVE_DEVICES.pop(device_id, None)

        vxlan_config = vxlan_utils.normalize_config(device_data.get("vxlan_config"))
        if "VXLAN" in protocols and vxlan_config:
            frr_manager = None
            container_name = None
            if device_id:
                try:
                    from utils.frr_docker import FRRDockerManager
                    frr_manager = FRRDockerManager()
                    container_name = frr_manager._get_container_name(device_id, device_name or "")
                except Exception:
                    frr_manager = None
                    container_name = None
            try:
                vxlan_utils.tear_down_vxlan_interface(
                    device_id,
                    vxlan_config,
                    container_name=container_name,
                    frr_manager=frr_manager,
                )
            except Exception as exc:
                logging.debug(f"[VXLAN REMOVE] Failed to tear down VXLAN for {device_id}: {exc}")

        return result
