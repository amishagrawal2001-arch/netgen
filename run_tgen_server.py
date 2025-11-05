## here is the server side code for a traffic generator app ##
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from scapy.all import Ether, Dot1Q, IP, IPv6, TCP, UDP, ICMP, ARP, Raw, sendp, wrpcap, sendpfast, rdpcap, sniff
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import logging
import psutil
import time
import os
import json
from datetime import datetime, timezone
import subprocess
import re
import random
import ipaddress
from collections import Counter
import uuid
from multithreaded_traffic_gen import generate_packets, on_stream_stopped, stream_tracker, start_rx_counter
from utils.device_manager import DeviceManager
from utils.helpers import increment_ip, increment_ipv6, increment_mac, is_interface_up
from utils.device_database import DeviceDatabase
from utils.bgp_monitor import BGPStatusManager
from utils.arp_monitor import ARPStatusMonitor


# Initialize Flask app and CORS
app = Flask(__name__)

# Global request/response logging to help trace API calls (including ISIS)
@app.before_request
def _log_request_info():
    try:
        logging.info(f"[REQUEST] {request.method} {request.path} from {request.remote_addr}")
        # For ISIS endpoints, include payload
        if request.method == 'POST' and request.path.startswith('/api/device/isis'):
            try:
                logging.info(f"[REQUEST BODY] {request.get_json(silent=True)}")
            except Exception:
                pass
    except Exception:
        pass

@app.after_request
def _log_response_info(response):
    try:
        logging.info(f"[RESPONSE] {response.status_code} for {request.method} {request.path}")
    except Exception:
        pass
    return response
CORS(app)

# Initialize device database
device_db = DeviceDatabase()

# IPv6 validation functions
def validate_ipv6_subnet(subnet_str):
    """Validate IPv6 subnet format."""
    try:
        network = ipaddress.IPv6Network(subnet_str, strict=False)
        return True, str(network)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as e:
        return False, str(e)

def validate_ipv4_subnet(subnet_str):
    """Validate IPv4 subnet format."""
    try:
        network = ipaddress.IPv4Network(subnet_str, strict=False)
        return True, str(network)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as e:
        return False, str(e)

def detect_address_family(subnet_str):
    """Detect if subnet is IPv4 or IPv6."""
    if ":" in subnet_str:
        return "ipv6"
    else:
        return "ipv4"

def validate_subnet(subnet_str):
    """Validate subnet and return address family."""
    if not subnet_str:
        return False, "Empty subnet", None
    
    address_family = detect_address_family(subnet_str)
    
    if address_family == "ipv6":
        is_valid, result = validate_ipv6_subnet(subnet_str)
    else:
        is_valid, result = validate_ipv4_subnet(subnet_str)
    
    return is_valid, result, address_family

# Initialize BGP status monitor
bgp_monitor = BGPStatusManager(device_db, server_url="http://localhost:5051")

# Initialize OSPF status monitor
from utils.ospf_monitor import OSPFStatusManager
ospf_monitor = OSPFStatusManager(device_db, server_url="http://localhost:5051")

# Initialize ISIS status monitor
from utils.isis_monitor import ISISMonitor
isis_monitor = ISISMonitor(device_db)

# Initialize ARP status monitor
arp_monitor = ARPStatusMonitor(device_db, server_url="http://localhost:5051")

# Add request logging middleware
@app.before_request
def log_request_info():
    logging.info(f"[REQUEST] {request.method} {request.path} from {request.remote_addr}")
    if request.method in ['POST', 'PUT', 'PATCH']:
        try:
            # Log request data (truncated for security)
            data = request.get_json()
            if data:
                # Only log non-sensitive fields
                safe_data = {k: v for k, v in data.items() if 'password' not in k.lower() and 'token' not in k.lower()}
                logging.debug(f"[REQUEST DATA] {safe_data}")
        except Exception as e:
            logging.debug(f"[REQUEST DATA] Could not parse JSON: {e}")

@app.after_request
def log_response_info(response):
    logging.info(f"[RESPONSE] {response.status_code} for {request.method} {request.path}")
    return response

# Thread pool and active streams tracking
executor = ThreadPoolExecutor(max_workers=10)

# Active streams tracking
active_streams = {}
active_streams_lock = Lock()
STREAMS = {}
capture_processes = {}

# Set up logging
log_level = os.environ.get('OSTG_LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format='%(asctime)s - %(levelname)s - %(message)s')
logging.info(f"[SERVER] Starting OSTG server with log level: {log_level}")


@app.route("/api/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"}), 200


def increment_value(base, step, count, is_ip=False):
    """Increment a base value by a step for a specified count."""
    results = []
    try:
        if is_ip:
            # Handle IP address increments
            octets = list(map(int, base.split(".")))
            for i in range(int(count)):
                incremented = octets[:]
                incremented[-1] += step * i
                for j in range(3, -1, -1):  # Handle overflow
                    if incremented[j] > 255:
                        incremented[j] -= 256
                        if j > 0:
                            incremented[j - 1] += 1
                        else:
                            raise ValueError(f"IP address overflow: {base}")
                results.append(".".join(map(str, incremented)))
        elif ":" in base:  # Handle MAC address increments
            mac_parts = base.split(":")
            mac_int = int("".join(mac_parts), 16)
            for i in range(int(count)):
                incremented = mac_int + step * i
                mac_str = f"{incremented:012x}"  # Convert back to hex string
                mac_str = ":".join(mac_str[i:i+2] for i in range(0, 12, 2))
                results.append(mac_str)
        else:
            # Handle numeric increments (e.g., VLAN ID)
            base = int(base)
            for i in range(int(count)):
                incremented = base + step * i
                results.append(str(incremented))
    except Exception as e:
        logging.error(f"Error in increment_value: {e}")
        raise
    return results





# --- Add Save/Load Routes ---
@app.route("/api/streams/save", methods=["GET"])
def save_session():
    import json
    from utils.path_utils import get_ostg_data_directory
    
    data_dir = get_ostg_data_directory()
    session_file = os.path.join(data_dir, "stream_session.json")
    
    with open(session_file, "w") as f:
        json.dump(STREAMS, f)
    return jsonify({"message": "Session saved.", "file": session_file})


@app.route("/api/streams/load", methods=["GET"])
def load_session():
    import json
    from utils.path_utils import get_ostg_data_directory
    
    global STREAMS
    data_dir = get_ostg_data_directory()
    session_file = os.path.join(data_dir, "stream_session.json")
    
    try:
        with open(session_file, "r") as f:
            STREAMS = json.load(f)
        return jsonify({"message": "Session loaded.", "streams": STREAMS, "file": session_file})
    except FileNotFoundError:
        return jsonify({"error": "No session file found.", "file": session_file}), 404

@app.route("/api/streams/stats", methods=["GET"])
def stream_stats():
    stats = stream_tracker.get_stream_stats()
    logging.info(f"[STATS] {stats}")
    return jsonify({"active_streams": stats}), 200


## check and updated if needed. #
@app.route("/api/traffic/rx_monitor", methods=["POST"])
def rx_monitor():
    data = request.get_json()
    interface = data.get("interface")
    stream_name = data.get("stream_name")

    if not interface or not stream_name:
        return jsonify({"error": "Missing interface or stream name"}), 400

    stop_event = Event()

    match_criteria = {
        "mac_src": data.get("mac_source_address"),
        "ip_src": data.get("ipv4_source"),
        "ipv6_src": data.get("ipv6_source")
    }

    logging.info(f"🟢 RX monitor initializing on {interface} for stream '{stream_name}'")
    logging.debug(f"🔎 Match criteria: {match_criteria}")

    stream_tracker.add_stream({
        "interface": interface,
        "stream_name": stream_name,
        "stop_event": stop_event,
        "stream_id": data.get("stream_id", str(uuid.uuid4()))  # ✅ Ensure fallback stream_id
    })

    start_rx_counter(interface, stream_name, stop_event, match_criteria)
    return jsonify({"message": "RX monitoring started"}), 200



@app.route("/api/traffic/restart", methods=["POST"])
def restart_stream():
    data = request.json
    logging.info(f"[RESTART REQUEST] Payload received: {data}")

    port = data.get("port")
    streams = data.get("streams", [])

    if not port or not streams:
        return jsonify({"error": "Missing port or stream list"}), 400

    interface = port.split("Port:")[-1].strip()
    restarted_streams = []

    for stream_data in streams:
        stream_id = stream_data.get("stream_id")
        stream_name = stream_data.get("name", "Unnamed")

        if not stream_id:
            logging.warning(f"Missing stream_id for stream '{stream_name}'. Skipping.")
            continue

        # 🛑 Stop previous stream
        existing = stream_tracker.find_stream_by_id(interface, stream_id)
        if existing:
            logging.info(f"Stopping stream {stream_id} on {interface}")
            existing["stop_event"].set()
            stream_tracker.remove_stream_by_id(interface, stream_id)
        else:
            logging.warning(f"Stream {stream_id} not found on {interface}")

        # 🚀 Restart using updated data
        stream_data["stream_id"] = stream_id  # Reuse existing ID
        result = launch_single_stream(stream_data, interface)
        restarted_streams.append(result)

    return jsonify({
        "status": "restarted",
        "interface": interface,
        "restarted_streams": restarted_streams
    })



def launch_single_stream(stream_data, interface):
    stream_name = stream_data.get("name", "Unnamed Stream")
    stream_id = stream_data.setdefault("stream_id", str(uuid.uuid4()))
    stop_event = Event()

    rx_port = stream_data.get("rx_port") or interface
    rx_interface = str(rx_port).split("Port:")[-1].strip()
    stream_data["rx_interface"] = rx_interface

    flow_tracking = stream_data.get("flow_tracking_enabled", False)
    rx_thread = None

    if flow_tracking and rx_interface != interface:
        if is_interface_up(rx_interface):
            logging.info(f"🟢 RX sniffer launched on '{rx_interface}' for stream '{stream_name}'")
            """match_criteria = {
                "mac_src": stream_data.get("mac_source_address"),
                "ip_src": stream_data.get("ipv4_source"),
                "ipv6_src": stream_data.get("ipv6_source"),
                "stream_signature": f"[{stream_name}]"
            }"""
            #rx_thread = start_rx_counter(rx_interface, stream_name, stop_event, match_criteria)
            rx_thread = start_rx_counter(rx_interface, stream_name, stream_id, stream_tracker, stop_event)

        else:
            logging.warning(f"⚠️ RX interface '{rx_interface}' is DOWN, skipping RX sniffer.")
    else:
        logging.info(f"🔇 RX sniffer skipped (FlowTracking={flow_tracking}, RX='{rx_interface}')")

    stream_tracker.add_stream({
        "interface": interface,
        "stream_name": stream_name,
        "stream_id": stream_id,
        "stop_event": stop_event,
        "rx_thread": rx_thread,
        "rx_interface": rx_interface,
        "flow_tracking_enabled": flow_tracking
    })

    try:
        executor.submit(generate_packets, stream_data, interface, stop_event)
        logging.info(f"🚀 Launched stream '{stream_name}' on interface '{interface}'")
        return {
            "interface": interface,
            "stream_id": stream_id,
            "stream_name": stream_name,
            "status": "started"
        }
    except Exception as e:
        logging.error(f"❌ Failed to start stream '{stream_name}' on '{interface}': {e}")
        stream_tracker.remove_stream(interface, stream_name)
        return {
            "interface": interface,
            "stream_name": stream_name,
            "error": str(e)
        }

@app.route("/api/traffic/start", methods=["POST"])
def start_traffic():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    logging.info(f"📥 Incoming traffic start request: {data}")
    streams = data.get("streams", {})
    if not streams:
        return jsonify({"error": "No streams provided"}), 400

    started_streams = []

    for interface_label, stream_list in streams.items():
        interface_name = interface_label.split(":")[-1].strip()

        for stream_data in stream_list:
            stream_name = stream_data.get("name", "Unnamed Stream")
            stream_id = stream_data.setdefault("stream_id", str(uuid.uuid4()))
            flow_tracking = stream_data.setdefault("flow_tracking_enabled", False)

            if not stream_data.get("enabled", False):
                logging.info(f"⏩ Skipping disabled stream '{stream_name}' on interface '{interface_name}'")
                continue

            stream_data["interface"] = interface_name
            stream_data["stream_name"] = stream_name

            rx_port = stream_data.get("rx_port") or interface_name
            stream_data["rx_interface"] = str(rx_port).split("Port:")[-1].strip()

            # Prevent duplicates
            existing = stream_tracker.find_stream_by_id(interface_name, stream_id)
            if existing:
                logging.warning(f"⚠️ Stream '{stream_name}' already running on {interface_name} with ID {stream_id}")
                continue

            try:
                result = launch_single_stream(stream_data, interface_name)
                started_streams.append(result)
                logging.info(f"🚀 Launched stream '{stream_name}' on {interface_name} (ID: {stream_id})")
            except Exception as e:
                logging.error(f"❌ Failed to launch stream '{stream_name}' on {interface_name}: {e}")

    logging.info(f"✅ {len(started_streams)} stream(s) started")
    return jsonify({
        "message": "Traffic streams started successfully.",
        "started_streams": started_streams
    }), 200

@app.route("/api/traffic/stop", methods=["POST"])
def stop_traffic():
    data = request.get_json()
    if not data or "streams" not in data:
        return jsonify({"error": "Invalid stop request"}), 400

    stop_list = data["streams"]
    stopped = []

    logging.info(f"🛑 Stop request received: {stop_list}")

    for entry in stop_list:
        interface = entry.get("interface")
        stream_id = entry.get("stream_id")

        if not interface or not stream_id:
            logging.warning(f"⚠️ Invalid stop entry: {entry}")
            continue

        logging.info(f"🛑 Attempting to stop stream ID: {stream_id} on interface: {interface}")
        #stream = stream_tracker.get_stream_by_id(interface, stream_id)
        stream = stream_tracker.find_stream_by_id(interface, stream_id)

        if stream:
            stream["stop_event"].set()
            stream_tracker.remove_stream_by_id(interface, stream_id)
            logging.info(f"✅ Stop event set for stream ID: {stream_id}")
            logging.info(f"🛑 Stream stopped: {stream_id} on {interface} (Reason: manual)")
            stopped.append({"interface": interface, "stream_id": stream_id})
        else:
            logging.warning(f"❌ Stream ID '{stream_id}' not found on interface '{interface}'")

    return jsonify({"stopped": stopped}), 200



@app.route("/api/device/start", methods=["POST"])
def start_device():
    data = request.get_json()
    logging.info(f"Start Device Data: {data}")
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400

    try:
        logging.info(f"[DEVICE START] Function entry - starting device processing")
        device_id = data.get("device_id")
        device_name = data.get("device_name", f"device_{device_id}")
        iface = data.get("interface", "")
        # Handle both lowercase and uppercase field names for backward compatibility
        ipv4 = data.get("ipv4") or data.get("IPv4")
        ipv6 = data.get("ipv6") or data.get("IPv6")
        ipv4_mask = data.get("ipv4_mask", "24")
        ipv6_mask = data.get("ipv6_mask", "64")
        vlan = data.get("vlan", "0")
        
        logging.info(f"[DEVICE START] Extracted values: device_id={device_id}, iface={iface}, vlan={vlan}, ipv4={ipv4}, ipv6={ipv6}")

        # Light start: enable interface and configure IP addresses if provided
        result = {"device_id": device_id, "device": device_name, "interface": iface}
        iface_name = f"vlan{vlan}" if (vlan and vlan != "0") else iface
        
        # Step 1: Bring up interface
        try:
            bringup_result = subprocess.run(["ip", "link", "set", iface_name, "up"], capture_output=True, text=True, timeout=5)
            if bringup_result.returncode == 0:
                logging.info(f"[DEVICE START] Interface {iface_name} brought up")
                result["interface_up"] = True
            else:
                logging.warning(f"[DEVICE START] Failed to bring up interface {iface_name}: {bringup_result.stderr}")
                result["interface_up"] = False
        except Exception as e:
            logging.warning(f"[DEVICE START] Interface bring-up failed for {iface_name}: {e}")
            result["interface_up"] = False
        
        # Step 2: Configure IPv4 address if provided
        if ipv4:
            try:
                # Remove existing IPv4 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv4}/{ipv4_mask}", "dev", iface_name], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv4 address
                ipv4_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv4}/{ipv4_mask}", "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if ipv4_result.returncode == 0:
                    logging.info(f"[DEVICE START] Configured IPv4 address {ipv4}/{ipv4_mask} on {iface_name}")
                    result["ipv4_configured"] = True
                else:
                    logging.warning(f"[DEVICE START] Failed to configure IPv4 address {ipv4}/{ipv4_mask}: {ipv4_result.stderr}")
                    result["ipv4_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE START] Error configuring IPv4 address: {e}")
                result["ipv4_configured"] = False
        
        # Step 3: Configure IPv6 address if provided
        if ipv6:
            try:
                # Remove existing IPv6 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv6}/{ipv6_mask}", "dev", iface_name], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv6 address
                ipv6_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv6}/{ipv6_mask}", "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if ipv6_result.returncode == 0:
                    logging.info(f"[DEVICE START] Configured IPv6 address {ipv6}/{ipv6_mask} on {iface_name}")
                    result["ipv6_configured"] = True
                else:
                    logging.warning(f"[DEVICE START] Failed to configure IPv6 address {ipv6}/{ipv6_mask}: {ipv6_result.stderr}")
                    result["ipv6_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE START] Error configuring IPv6 address: {e}")
                result["ipv6_configured"] = False

        # Update only the device status in DB if known
        try:
            if device_id:
                device_db.update_device_status(device_id, "Running")
                logging.info(f"[DEVICE DB] Device {device_id} status updated to Running")
        except Exception as e:
            logging.warning(f"[DEVICE DB] Failed to update device {device_id} status to Running: {e}")
        
        # Auto-restore FRR container and protocols if device was previously configured
        try:
            if device_id:
                device_data = device_db.get_device(device_id)
                if device_data:
                    # Check if device has any protocols configured
                    # Prefer protocols from payload (latest from client), fallback to database
                    protocols = data.get("protocols", [])
                    logging.info(f"[DEVICE START] Protocols from payload: {protocols}")
                    if not protocols:
                        protocols = device_data.get("protocols", [])
                        logging.info(f"[DEVICE START] Protocols from database: {protocols}")
                    if isinstance(protocols, str):
                        import json
                        try:
                            protocols = json.loads(protocols) if protocols else []
                        except:
                            protocols = []
                    
                    # Also check if protocol configs are provided even if protocols list is empty
                    has_bgp_config = bool(data.get("bgp_config") or device_data.get("bgp_config"))
                    has_ospf_config = bool(data.get("ospf_config") or device_data.get("ospf_config"))
                    has_isis_config = bool(data.get("isis_config") or device_data.get("isis_config"))
                    
                    if (protocols and (isinstance(protocols, list) and len(protocols) > 0)) or has_bgp_config or has_ospf_config or has_isis_config:
                        if protocols:
                            logging.info(f"[DEVICE START] Device {device_name} has configured protocols: {protocols} - will restore FRR container")
                        else:
                            logging.info(f"[DEVICE START] Device {device_name} has protocol configs (BGP={has_bgp_config}, OSPF={has_ospf_config}, ISIS={has_isis_config}) - will restore FRR container")
                        
                        # Check if FRR container exists
                        from utils.frr_docker import FRRDockerManager
                        frr_manager = FRRDockerManager()
                        container_name = frr_manager._get_container_name(device_id, device_name)
                        
                        try:
                            container = frr_manager.client.containers.get(container_name)
                            container_was_stopped = (container.status != "running")
                            
                            if container_was_stopped:
                                logging.info(f"[DEVICE START] Container {container_name} exists but not running, starting it...")
                                container.start()
                                # Wait for container to be ready
                                import time
                                time.sleep(5)
                            else:
                                logging.info(f"[DEVICE START] Container {container_name} is already running, restoring protocols and interface")
                            
                            # Get protocol configs from payload first (if provided), otherwise from database
                            import json
                            
                            # BGP config: prefer payload, fallback to database
                            bgp_config = None
                            if "bgp_config" in data:
                                bgp_config = data.get("bgp_config")
                                logging.info(f"[DEVICE START] Using BGP config from payload: {bgp_config is not None}, has content: {bool(bgp_config)}")
                            if bgp_config is None:
                                bgp_config_raw = device_data.get("bgp_config", {})
                                if isinstance(bgp_config_raw, str) and bgp_config_raw:
                                    try:
                                        bgp_config = json.loads(bgp_config_raw)
                                    except:
                                        bgp_config = {}
                                else:
                                    bgp_config = bgp_config_raw if bgp_config_raw else {}
                                logging.info(f"[DEVICE START] Using BGP config from database: {bool(bgp_config)}")
                            
                            # OSPF config: prefer payload, fallback to database
                            ospf_config = None
                            if "ospf_config" in data:
                                ospf_config = data.get("ospf_config")
                                logging.info(f"[DEVICE START] Using OSPF config from payload: {ospf_config is not None}, has content: {bool(ospf_config)}")
                            if ospf_config is None:
                                ospf_config_raw = device_data.get("ospf_config", {})
                                if isinstance(ospf_config_raw, str) and ospf_config_raw:
                                    try:
                                        ospf_config = json.loads(ospf_config_raw)
                                    except:
                                        ospf_config = {}
                                else:
                                    ospf_config = ospf_config_raw if ospf_config_raw else {}
                                logging.info(f"[DEVICE START] Using OSPF config from database: {bool(ospf_config)}")
                            
                            # ISIS config: prefer payload, fallback to database
                            isis_config = None
                            if "isis_config" in data:
                                isis_config = data.get("isis_config")
                                logging.info(f"[DEVICE START] Using ISIS config from payload: {isis_config is not None}, has content: {bool(isis_config)}")
                            if isis_config is None:
                                isis_config_raw = device_data.get("isis_config", {})
                                if isinstance(isis_config_raw, str) and isis_config_raw:
                                    try:
                                        isis_config = json.loads(isis_config_raw)
                                    except:
                                        isis_config = {}
                                else:
                                    isis_config = isis_config_raw if isis_config_raw else {}
                                logging.info(f"[DEVICE START] Using ISIS config from database: {bool(isis_config)}")
                            
                            # Use IP addresses from payload (latest from client) or fallback to database
                            ipv4_for_config = ipv4 if ipv4 else device_data.get('ipv4_address', '')
                            ipv4_mask_for_config = ipv4_mask if ipv4_mask else device_data.get('ipv4_mask', '24')
                            ipv4_full = f"{ipv4_for_config}/{ipv4_mask_for_config}" if ipv4_for_config else ""
                            
                            ipv6_for_config = ipv6 if ipv6 else device_data.get('ipv6_address', '')
                            ipv6_mask_for_config = ipv6_mask if ipv6_mask else device_data.get('ipv6_mask', '64')
                            ipv6_full = f"{ipv6_for_config}/{ipv6_mask_for_config}" if ipv6_for_config else ""
                            
                            # Restore protocols and interface (revert stop changes)
                            logging.info(f"[DEVICE START] Restoring protocols and interface in container {container_name}")
                            success = frr_manager.restore_frr_container(
                                container_name=container_name,
                                bgp_config=bgp_config,
                                ospf_config=ospf_config,
                                isis_config=isis_config,
                                interface=iface,
                                vlan=vlan,
                                ipv4=ipv4_full,
                                ipv6=ipv6_full
                            )
                            
                            if success:
                                logging.info(f"[DEVICE START] Successfully restored protocols and interface for {device_name}")
                                
                                # Clear manual override flags so monitors can immediately update status
                                try:
                                    now = datetime.now(timezone.utc).isoformat()
                                    clear_override_data = {
                                        'bgp_manual_override': False,
                                        'bgp_manual_override_time': None,
                                        'ospf_manual_override': False,
                                        'ospf_manual_override_time': None,
                                        'isis_manual_override': False,
                                        'isis_manual_override_time': None,
                                    }
                                    device_db.update_device(device_id, clear_override_data)
                                    logging.info(f"[DEVICE START] Cleared manual override flags for {device_name} to allow immediate monitor updates")
                                except Exception as e:
                                    logging.warning(f"[DEVICE START] Failed to clear manual override flags: {e}")
                            else:
                                logging.warning(f"[DEVICE START] Failed to restore some protocols for {device_name}")
                        except Exception as container_error:
                            # Check if it's a NotFound error (container doesn't exist)
                            error_str = str(container_error).lower()
                            error_type = type(container_error).__name__
                            
                            if "notfound" in error_str or "not found" in error_str or "NotFound" in error_type:
                                logging.error(f"[DEVICE START] Container {container_name} does not exist - cannot start device without existing container")
                                return jsonify({"error": f"Container not found for device {device_name}. Device must be applied first."}), 404
                            
                            # Log and re-raise other exceptions
                            logging.error(f"[DEVICE START] Error restoring protocols for {device_name}: {container_error}")
                            import traceback
                            logging.error(f"[DEVICE START] Traceback: {traceback.format_exc()}")
                            raise
        except Exception as e:
            logging.error(f"[DEVICE START] Failed to auto-restore protocols: {e}")
            import traceback
            logging.error(traceback.format_exc())

        # Trigger BGP status check for this device after start
        try:
            if device_id:
                logging.info(f"[BGP STATUS] Triggering BGP status check for device {device_id} after start")
                bgp_monitor.force_check()
        except Exception as e:
            logging.warning(f"[BGP STATUS] Failed to trigger BGP status check for device {device_id}: {e}")
        
        # Trigger OSPF status check for this device after start
        try:
            if device_id:
                logging.info(f"[OSPF STATUS] Triggering OSPF status check for device {device_id} after start")
                ospf_monitor.force_check()
        except Exception as e:
            logging.warning(f"[OSPF STATUS] Failed to trigger OSPF status check for device {device_id}: {e}")
        
        # Trigger ISIS status check for this device after start
        try:
            if device_id:
                logging.info(f"[ISIS STATUS] Triggering ISIS status check for device {device_id} after start")
                isis_monitor.force_check()
        except Exception as e:
            logging.warning(f"[ISIS STATUS] Failed to trigger ISIS status check for device {device_id}: {e}")
        
        return jsonify({"status": "started", "details": result}), 200
    except Exception as e:
        logging.error(f"[DEVICE ERROR] Failed to start device: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/ospf/start", methods=["POST"])
def start_ospf():
    """Start OSPF for a device."""
    data = request.get_json()
    logging.info(f"Start OSPF Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing OSPF start configuration"}), 400
    
    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", f"device_{device_id}")
        ospf_config = data.get("ospf_config", {})
        af = data.get("af")  # Extract AF parameter for AF-aware start
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        logging.info(f"[OSPF START] Starting OSPF for device {device_name} (af={af})")
        
        # Start OSPF
        from utils.ospf import start_ospf_neighbor
        success = start_ospf_neighbor(device_id, ospf_config, device_name, af=af)
        
        if not success:
            logging.error(f"[OSPF START] Failed to start OSPF for device {device_name}")
            return jsonify({"error": "Failed to start OSPF"}), 500
        
        # After starting OSPF, restore route pool configurations if they exist
        try:
            # Get route pool attachments from database
            device_route_pools = device_db.get_device_route_pools(device_id)
            if device_route_pools:
                logging.info(f"[OSPF START] Found route pool attachments for {len(device_route_pools)} areas, restoring them")
                
                # device_route_pools is a Dict[str, List[str]] (area_id -> pool_names)
                route_pools_per_area = device_route_pools
                
                # Get all available route pools
                all_pools_db = device_db.get_all_route_pools()
                all_pools = []
                for pool in all_pools_db:
                    all_pools.append({
                        "name": pool["pool_name"],
                        "subnet": pool["subnet"],
                        "count": pool["route_count"],
                        "first_host": pool["first_host_ip"],
                        "last_host": pool["last_host_ip"],
                        "increment_type": pool.get("increment_type", "host")
                    })
                
                # Restore route pool configurations for each area
                for area_key, attached_pools in route_pools_per_area.items():
                    if attached_pools and all_pools:
                        # Parse area_key: could be "area_id" (old format) or "area_id:neighbor_type" (new format)
                        if ":" in area_key:
                            area_id, neighbor_type = area_key.split(":", 1)
                        else:
                            area_id = area_key
                            neighbor_type = "IPv4"  # Default to IPv4 for backward compatibility
                        
                        logging.info(f"[OSPF START] Restoring route pools for area {area_id}, type {neighbor_type}: {attached_pools}")
                        # Run route advertisement configuration in background
                        def _restore_routes(area_id=area_id, af_type=neighbor_type, pools=attached_pools):
                            configure_ospf_route_advertisement(
                                device_id, device_name, area_id, 
                                pools, all_pools, af_type=af_type
                            )
                        import threading
                        threading.Thread(target=_restore_routes, daemon=True).start()
            else:
                logging.info(f"[OSPF START] No route pool attachments found for device {device_id}")
        except Exception as e:
            logging.warning(f"[OSPF START] Failed to restore route pool configurations: {e}")
        
        # Update device status in database
        try:
            device_db.update_device_status(device_id, "Running")
            logging.info(f"[OSPF START] Updated device {device_id} status to Running")
        except Exception as e:
            logging.warning(f"[OSPF START] Failed to update device {device_id} status: {e}")
        
        logging.info(f"[OSPF START] Successfully started OSPF for device {device_name}")
        
        return jsonify({
            "status": "started",
            "device_id": device_id,
            "device_name": device_name
        }), 200
        
    except Exception as e:
        logging.error(f"[OSPF START ERROR] Failed to start OSPF: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/ospf/stop", methods=["POST"])
def stop_ospf():
    """Stop OSPF for a device."""
    data = request.get_json()
    logging.info(f"Stop OSPF Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing OSPF stop configuration"}), 400
    
    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", f"device_{device_id}")
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        af = data.get("af") or data.get("address_family")
        logging.info(f"[OSPF STOP] Stopping OSPF for device {device_name} af={af}")
        
        # Stop OSPF
        from utils.ospf import stop_ospf_neighbor
        success = stop_ospf_neighbor(device_id, device_name, af)
        
        if not success:
            logging.error(f"[OSPF STOP] Failed to stop OSPF for device {device_name}")
            return jsonify({"error": "Failed to stop OSPF"}), 500
        
        # Update device status in database
        try:
            device_db.update_device_status(device_id, "Stopped")
            logging.info(f"[OSPF STOP] Updated device {device_id} status to Stopped")
        except Exception as e:
            logging.warning(f"[OSPF STOP] Failed to update device {device_id} status: {e}")
        
        logging.info(f"[OSPF STOP] Successfully stopped OSPF for device {device_name} af={af}")
        
        return jsonify({
            "status": "stopped",
            "device_id": device_id,
            "device_name": device_name,
            "af": af
        }), 200
        
    except Exception as e:
        logging.error(f"[OSPF STOP ERROR] Failed to stop OSPF: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/status/<device_id>", methods=["GET"])
def get_device_ospf_status(device_id):
    """Get OSPF status for a device."""
    try:
        logging.info(f"[OSPF STATUS] Getting OSPF status for device {device_id}")
        
        from utils.ospf import get_ospf_status
        ospf_status = get_ospf_status(device_id)
        
        if ospf_status is None:
            logging.info(f"[OSPF STATUS] Device {device_id} not found or container missing")
            return jsonify({"error": "Device not found or OSPF not configured"}), 404
        
        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'ospf_status': ospf_status
        }), 200
        
    except Exception as e:
        logging.error(f"[OSPF STATUS ERROR] Failed to get OSPF status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/status/database/<device_id>", methods=["GET"])
def get_device_ospf_status_from_database(device_id):
    """Get OSPF status for a device from database."""
    try:
        logging.info(f"[OSPF DATABASE STATUS] Getting OSPF status from database for device {device_id}")
        
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        
        # Get device from database
        device = device_db.get_device(device_id)
        if not device:
            logging.info(f"[OSPF DATABASE STATUS] Device {device_id} not found in database")
            return jsonify({"error": "Device not found"}), 404
        
        # Extract OSPF status from database
        ospf_status = {
            'ospf_established': device.get('ospf_established', False),
            'ospf_state': device.get('ospf_state', 'Unknown'),
            'ospf_ipv4_running': device.get('ospf_ipv4_running', False),
            'ospf_ipv6_running': device.get('ospf_ipv6_running', False),
            'ospf_ipv4_established': device.get('ospf_ipv4_established', False),
            'ospf_ipv6_established': device.get('ospf_ipv6_established', False),
            'ospf_ipv4_uptime': device.get('ospf_ipv4_uptime', None),
            'ospf_ipv6_uptime': device.get('ospf_ipv6_uptime', None),
            'last_ospf_check': device.get('last_ospf_check', None)
        }
        
        # Parse neighbors from JSON string
        neighbors = []
        ospf_neighbors_str = device.get('ospf_neighbors')
        if ospf_neighbors_str:
            try:
                import json
                neighbors = json.loads(ospf_neighbors_str)
            except:
                neighbors = []
        
        ospf_status['neighbors'] = neighbors
        
        logging.info(f"[OSPF DATABASE STATUS] Retrieved OSPF status for device {device_id}: {ospf_status['ospf_state']}")
        
        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'ospf_status': ospf_status
        }), 200
        
    except Exception as e:
        logging.error(f"[OSPF DATABASE STATUS ERROR] Failed to get OSPF status from database: {e}")
        return jsonify({"error": str(e)}), 500

# ISIS API Endpoints
@app.route("/api/device/isis/start", methods=["POST"], endpoint="device_isis_start")
def device_isis_start():
    """Start ISIS on a device."""
    data = request.get_json()
    logging.info(f"[ISIS START] Incoming request from {request.remote_addr}")
    logging.debug(f"[ISIS START] Headers: {dict(request.headers)}")
    logging.info(f"[ISIS START] Payload: {data}")
    if not data:
        return jsonify({"error": "Missing ISIS configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", "")
        isis_config = data.get("isis_config", {}) or {}
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        logging.info(f"[ISIS START] Starting ISIS for device {device_name} (ID: {device_id})")
        
        # Get device from database
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device = device_db.get_device(device_id)
        
        if not device:
            return jsonify({"error": "Device not found"}), 404
        
        # Ensure interface present in isis_config; derive from VLAN when missing
        if isinstance(isis_config, str):
            import json as _json
            try:
                isis_config = _json.loads(isis_config)
            except Exception:
                isis_config = {}
        if device and not isis_config.get("interface"):
            vlan = device.get("vlan")
            if vlan and str(vlan).isdigit():
                isis_config["interface"] = f"vlan{vlan}"
            elif isinstance(data.get("interface"), str) and data.get("interface").startswith("vlan"):
                isis_config["interface"] = data.get("interface")

        # If NET (area_id) or system_id missing, try to hydrate from DB-stored config
        try:
            if device and (not isis_config.get("area_id") or not isis_config.get("system_id")):
                stored = device.get("isis_config") or device.get("is_is_config")
                if stored:
                    import json as _json
                    if isinstance(stored, str):
                        try:
                            stored = _json.loads(stored)
                        except Exception:
                            stored = {}
                    if isinstance(stored, dict):
                        # Only set values if they're not None (avoid overwriting with None from DB)
                        stored_area_id = stored.get("area_id")
                        stored_system_id = stored.get("system_id")
                        stored_level = stored.get("level")
                        if stored_area_id is not None:
                            isis_config.setdefault("area_id", stored_area_id)
                        if stored_system_id is not None:
                            isis_config.setdefault("system_id", stored_system_id)
                        if stored_level is not None:
                            isis_config.setdefault("level", stored_level)
                        # keep interface previously resolved as priority
        except Exception:
            pass

        # Check if container exists, if not create it
        from utils.frr_docker import FRRDockerManager
        import docker.errors
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        try:
            container = frr_manager.client.containers.get(container_name)
            container_id = container.name  # Use container name for consistency
        except docker.errors.NotFound:
            logging.info(f"[ISIS START] Container {container_name} not found, creating it...")
            # Create container with device configuration
            device_config = {
                "device_name": device_name,
                "ipv4": data.get("ipv4", device.get("ipv4_address", "")),
                "ipv6": data.get("ipv6", device.get("ipv6_address", "")),
                "interface": data.get("interface", device.get("interface", "ens4np0")),
                "vlan": data.get("vlan", str(device.get("vlan", "0")))
            }
            container_name = frr_manager.start_frr_container(device_id, device_config)
            container = frr_manager.client.containers.get(container_name)
            container_id = container_name
            # Wait for FRR daemons to be fully initialized
            import time
            logging.info(f"[ISIS START] Waiting 5 seconds for FRR daemons to initialize...")
            time.sleep(5)
        
        # Start ISIS: if still minimal config, use full configure to ensure router/interface lines
        if not isis_config or not isis_config.get("area_id") or not isis_config.get("system_id"):
            from utils.isis import configure_isis_neighbor
            success = configure_isis_neighbor(device_id, isis_config, device_name, ipv4=device.get("ipv4_address", ""), ipv6=device.get("ipv6_address", ""))
        else:
            from utils.isis import start_isis_neighbor
            success = start_isis_neighbor(device_id, device_name, container_id, isis_config)

        # Force-add interface lines if missing after a Stop
        if success:
            try:
                # Determine interface to enforce
                iface = None
                if isinstance(isis_config, dict):
                    iface = isis_config.get("interface")
                if not iface and device and device.get("vlan") and str(device.get("vlan")).isdigit():
                    iface = f"vlan{device.get('vlan')}"

                if iface:
                    # Determine which address families are configured
                    enable_ipv4 = bool(device and device.get('ipv4_address'))
                    enable_ipv6 = bool(device and device.get('ipv6_address'))
                    
                    logging.info(f"[ISIS START] Verifying interface lines on {iface} for {device_name}")
                    # Build idempotent here-doc to ensure lines exist
                    here_lines = [
                        "vtysh << 'EOF'",
                        "configure terminal",
                        f"interface {iface}",
                    ]
                    if enable_ipv4:
                        here_lines.append(" ip router isis CORE")
                    if enable_ipv6:
                        here_lines.append(" ipv6 router isis CORE")
                    here_lines.extend([
                        " isis network point-to-point",
                        "exit",
                        "end",
                        "write",
                        "EOF"
                    ])
                    here = "\n".join(here_lines)
                    # Use container name; docker exec accepts name
                    import subprocess
                    cmd = ["bash", "-lc", f"docker exec {container_id} bash -lc \"{here}\""]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                    if proc.returncode == 0:
                        logging.info(f"[ISIS START] Ensured interface ISIS lines present on {iface}")
                    else:
                        logging.warning(f"[ISIS START] Failed to enforce interface lines on {iface}: {proc.stderr}")
            except Exception as e:
                logging.warning(f"[ISIS START] Interface enforcement step failed: {e}")
        
        # Restore route pool configurations if they exist
        if success:
            try:
                route_pools_per_area = device_db.get_device_route_pools(device_id)
                if route_pools_per_area:
                    # Get all route pools
                    all_pools_db = device_db.get_all_route_pools()
                    all_pools = []
                    for pool in all_pools_db:
                        all_pools.append({
                            "name": pool["pool_name"],
                            "subnet": pool["subnet"],
                            "count": pool["route_count"],
                            "first_host": pool["first_host_ip"],
                            "last_host": pool["last_host_ip"],
                            "increment_type": pool.get("increment_type", "host")
                        })
                    
                    # Restore route pool configurations for each area
                    for area_key, attached_pools in route_pools_per_area.items():
                        if attached_pools and all_pools:
                            # Parse area_key: could be "area_id" (old format) or "area_id:neighbor_type" (new format)
                            if ":" in area_key:
                                area_id, neighbor_type = area_key.split(":", 1)
                            else:
                                area_id = area_key
                                neighbor_type = "IPv4"  # Default to IPv4 for backward compatibility
                            
                            logging.info(f"[ISIS START] Restoring route pools for area {area_id}, type {neighbor_type}: {attached_pools}")
                            # Run route advertisement configuration in background
                            def _restore_routes(area_id=area_id, af_type=neighbor_type, pools=attached_pools):
                                configure_isis_route_advertisement(
                                    device_id, device_name, area_id, 
                                    pools, all_pools, af_type=af_type
                                )
                            import threading
                            threading.Thread(target=_restore_routes, daemon=True).start()
                else:
                    logging.info(f"[ISIS START] No route pool attachments found for device {device_id}")
            except Exception as e:
                logging.warning(f"[ISIS START] Failed to restore route pool configurations: {e}")
            
            logging.info(f"[ISIS START] Successfully started ISIS for {device_name}")
            return jsonify({
                "status": "success",
                "message": f"ISIS started successfully for {device_name}"
            }), 200
        else:
            logging.error(f"[ISIS START] Failed to start ISIS for {device_name}")
            return jsonify({"error": "Failed to start ISIS"}), 500
            
    except Exception as e:
        logging.error(f"[ISIS START ERROR] Error starting ISIS: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/isis/stop", methods=["POST"])
def stop_isis():
    """Stop ISIS on a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing ISIS configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", "")
        isis_config = data.get("isis_config", {})
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        logging.info(f"[ISIS STOP] Stopping ISIS for device {device_name} (ID: {device_id})")
        
        # Get device from database
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            logging.info(f"[ISIS STOP] DeviceDatabase initialized")
            device = device_db.get_device(device_id)
            logging.info(f"[ISIS STOP] Got device from database: {device is not None}")
        except Exception as e:
            logging.error(f"[ISIS STOP] Failed to get device from database: {e}")
            raise
        
        if not device:
            logging.error(f"[ISIS STOP] Device {device_id} not found in database")
            return jsonify({"error": "Device not found"}), 404
        
        # Get ISIS config from device if not provided
        if not isis_config:
            isis_config = device.get("isis_config", {}) or device.get("is_is_config", {})
        
        logging.info(f"[ISIS STOP] ISIS config from request or device: {isis_config}")
        
        # Ensure FRR container exists - use FRRDockerManager
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Check if container exists
        container_name = frr_manager._get_container_name(device_id, device_name)
        try:
            container = frr_manager.client.containers.get(container_name)
            if container.status != "running":
                logging.info(f"[ISIS STOP] Container {container_name} not running, starting it...")
                container.start()
        except Exception:
            logging.error(f"[ISIS STOP] Container {container_name} not found")
            return jsonify({"error": f"Container not found: {container_name}"}), 404
        
        # Stop ISIS using the updated function
        from utils.isis import stop_isis_neighbor
        # Don't pass container_id - let the function use FRRDockerManager
        success = stop_isis_neighbor(device_id, device_name, None, isis_config)
        
        if success:
            logging.info(f"[ISIS STOP] Successfully stopped ISIS for {device_name}")
            return jsonify({
                "status": "success",
                "message": f"ISIS stopped successfully for {device_name}",
                "device_id": device_id,
                "device_name": device_name
            }), 200
        else:
            logging.error(f"[ISIS STOP] Failed to stop ISIS for {device_name}")
            return jsonify({"error": "Failed to stop ISIS"}), 500
            
    except Exception as e:
        logging.error(f"[ISIS STOP ERROR] Error stopping ISIS: {e}")
        import traceback
        logging.error(f"[ISIS STOP ERROR] Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/isis/status/<device_id>", methods=["GET"])
def get_device_isis_status(device_id):
    """Get ISIS status for a device."""
    try:
        logging.info(f"[ISIS STATUS] Getting ISIS status for device {device_id}")
        
        # Get device from database
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device = device_db.get_device(device_id)
        
        if not device:
            return jsonify({"error": "Device not found"}), 404
        
        container_id = device.get("container_id")
        if not container_id:
            return jsonify({"error": "Device container not found"}), 404
        
        # Get ISIS status from FRR
        from utils.isis import get_isis_status
        isis_status = get_isis_status(device_id, device.get("Device Name", ""), container_id)
        
        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'isis_status': isis_status
        }), 200
        
    except Exception as e:
        logging.error(f"[ISIS STATUS ERROR] Failed to get ISIS status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/isis/status/database/<device_id>", methods=["GET"])
def get_device_isis_status_from_database(device_id):
    """Get ISIS status for a device from database."""
    try:
        logging.info(f"[ISIS DATABASE STATUS] Getting ISIS status from database for device {device_id}")
        
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        
        # Get device from database
        device = device_db.get_device(device_id)
        if not device:
            logging.info(f"[ISIS DATABASE STATUS] Device {device_id} not found in database")
            return jsonify({"error": "Device not found"}), 404
        
        # Extract ISIS status from database
        isis_status = {
            'isis_running': device.get('isis_running', False),
            'isis_established': device.get('isis_established', False),
            'isis_state': device.get('isis_state', 'Unknown'),
            'isis_system_id': device.get('isis_system_id', ''),
            'isis_net': device.get('isis_net', ''),
            'isis_uptime': device.get('isis_uptime', ''),
            'last_isis_check': device.get('last_isis_check', ''),
            'neighbors': [],
            'areas': []
        }
        
        # Parse ISIS neighbors if available
        isis_neighbors = device.get('isis_neighbors')
        if isis_neighbors:
            try:
                if isinstance(isis_neighbors, str):
                    neighbors = json.loads(isis_neighbors)
                else:
                    neighbors = isis_neighbors
                isis_status['neighbors'] = neighbors
            except json.JSONDecodeError:
                neighbors = []
        else:
            neighbors = []
        
        # Parse ISIS areas if available
        isis_areas = device.get('isis_areas')
        if isis_areas:
            try:
                if isinstance(isis_areas, str):
                    areas = json.loads(isis_areas)
                else:
                    areas = isis_areas
                isis_status['areas'] = areas
            except json.JSONDecodeError:
                areas = []
        else:
            areas = []
        
        isis_status['neighbors'] = neighbors
        isis_status['areas'] = areas
        
        logging.info(f"[ISIS DATABASE STATUS] Retrieved ISIS status for device {device_id}: {isis_status['isis_state']}")
        
        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'isis_status': isis_status
        }), 200
        
    except Exception as e:
        logging.error(f"[ISIS DATABASE STATUS ERROR] Failed to get ISIS status from database: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/isis/cleanup", methods=["POST"])
def cleanup_isis():
    """Clean up ISIS configuration for a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing device information"}), 400

    try:
        device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        logging.info(f"[ISIS CLEANUP] Cleaning up ISIS configuration for device {device_id}")
        
        # Get device from database
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        device = device_db.get_device(device_id)
        
        if not device:
            return jsonify({"error": "Device not found"}), 404
        
        container_id = device.get("container_id")
        if not container_id:
            return jsonify({"error": "Device container not found"}), 404
        
        # Stop ISIS on the device
        from utils.isis import stop_isis_neighbor
        isis_config = device.get("is_is_config", {})
        success = stop_isis_neighbor(device_id, device.get("Device Name", ""), container_id, isis_config)
        
        if success:
            logging.info(f"[ISIS CLEANUP] Successfully cleaned up ISIS for device {device_id}")
            return jsonify({
                "status": "success",
                "message": f"ISIS configuration cleaned up successfully for device {device_id}"
            }), 200
        else:
            logging.error(f"[ISIS CLEANUP] Failed to clean up ISIS for device {device_id}")
            return jsonify({"error": "Failed to clean up ISIS configuration"}), 500
            
    except Exception as e:
        logging.error(f"[ISIS CLEANUP ERROR] Error cleaning up ISIS: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/isis/configure", methods=["POST"])
def configure_isis():
    """Configure ISIS for a specific device using FRR."""
    data = request.get_json()
    logging.info(f"[ISIS CONFIGURE] Incoming request from {request.remote_addr}")
    logging.debug(f"[ISIS CONFIGURE] Headers: {dict(request.headers)}")
    logging.info(f"[ISIS CONFIGURE] Payload: {data}")
    
    if not data:
        return jsonify({"error": "Missing ISIS configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name")
        interface = data.get("interface")
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        isis_config = data.get("isis_config", {})
        
        if not device_id or not isis_config:
            return jsonify({"error": "Missing device_id or ISIS configuration"}), 400

        # Import ISIS utilities
        from utils.isis import configure_isis_neighbor
        
        # Configure ISIS neighbor using FRR Docker
        logging.info(f"ISIS Config Debug: {isis_config}")
        logging.info(f"ISIS Config Keys: {list(isis_config.keys())}")
        
        # Ensure FRR container exists before configuring ISIS
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Check if container exists, if not create it
        container_name = frr_manager._get_container_name(device_id, device_name)
        try:
            container = frr_manager.client.containers.get(container_name)
            if container.status != "running":
                logging.info(f"[ISIS CONFIGURE] Container {container_name} exists but not running, starting it...")
                container.start()
        except Exception:
            logging.info(f"[ISIS CONFIGURE] Container {container_name} not found, creating it...")
            # Create container with device configuration
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": data.get("interface", "ens4np0"),
                "vlan": data.get("vlan", "0")
            }
            container_name = frr_manager.start_frr_container(device_id, device_config)
            container = frr_manager.client.containers.get(container_name)
            # Wait for FRR daemons to be fully initialized
            import time
            logging.info(f"[ISIS CONFIGURE] Waiting 5 seconds for FRR daemons to initialize...")
            time.sleep(5)
        
        # Save ISIS route pool attachments to database (similar to BGP and OSPF)
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            route_pools_data = isis_config.get("route_pools", [])
            area_id = isis_config.get("area_id", "49.0001.0000.0000.0001.00")
            
            # Handle both old list format and new dict format (per neighbor type)
            if isinstance(route_pools_data, dict):
                # New format: route_pools = {"IPv4": [pools], "IPv6": [pools]}
                # Store as area_id + neighbor_type (e.g., "49.0001.0000.0000.0001.00:IPv4")
                all_route_pools = []
                for neighbor_type, pools in route_pools_data.items():
                    if pools:
                        area_key = f"{area_id}:{neighbor_type}"
                        device_db.attach_route_pools_to_device(device_id, area_key, pools)
                        all_route_pools.extend(pools)
                        logging.info(f"[ISIS CONFIGURE] Saved {len(pools)} route pool attachments for device {device_id}, area {area_id}, type {neighbor_type}")
                
                if all_route_pools:
                    logging.info(f"[ISIS CONFIGURE] Total {len(all_route_pools)} route pool attachments saved for device {device_id}")
                else:
                    # Remove all attachments for this device/area
                    device_db.remove_device_route_pools(device_id, area_id)
                    logging.info(f"[ISIS CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id}")
            elif isinstance(route_pools_data, list) and len(route_pools_data) > 0:
                # Old format: route_pools = [pools]
                device_db.attach_route_pools_to_device(device_id, area_id, route_pools_data)
                logging.info(f"[ISIS CONFIGURE] Saved {len(route_pools_data)} route pool attachments for device {device_id} and area {area_id} (old format)")
            else:
                # No route pools configured - remove all attachments for this device/area
                device_db.remove_device_route_pools(device_id, area_id)
                logging.info(f"[ISIS CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id}")
        except Exception as e:
            logging.warning(f"[ISIS CONFIGURE] Failed to save route pool attachments: {e}")
        
        # Save device to database if it doesn't exist, or update ISIS config for existing device
        try:
            from datetime import datetime, timezone
            existing_device = device_db.get_device(device_id)
            if not existing_device:
                logging.info(f"[ISIS CONFIGURE] Device {device_id} not found in database, adding it")
                device_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": data.get("interface", "ens4np0"),
                    "vlan": data.get("vlan", "0"),
                    "ipv4_address": ipv4,
                    "ipv6_address": ipv6,
                    "ipv4_mask": data.get("ipv4_mask", "24"),
                    "ipv6_mask": data.get("ipv6_mask", "64"),
                    "ipv4_gateway": data.get("ipv4_gateway", ""),
                    "ipv6_gateway": data.get("ipv6_gateway", ""),
                    "protocols": ["IS-IS"],  # Add IS-IS protocol to the device
                    "isis_config": isis_config,  # Save ISIS configuration
                    "status": "Running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                
                if device_db.add_device(device_data):
                    logging.info(f"[ISIS CONFIGURE] Successfully added device {device_name} to database")
                else:
                    logging.warning(f"[ISIS CONFIGURE] Failed to add device {device_name} to database")
            else:
                logging.info(f"[ISIS CONFIGURE] Device {device_id} already exists in database")
                
                # IMPORTANT: Check for IPv4/IPv6 removal BEFORE updating database
                # Get existing ISIS config before it's overwritten
                existing_isis_config = existing_device.get("isis_config", {})
                if isinstance(existing_isis_config, str):
                    import json
                    try:
                        existing_isis_config = json.loads(existing_isis_config)
                    except:
                        existing_isis_config = {}
                
                # Check if IPv4 was previously enabled but now disabled - remove IPv4 ISIS
                existing_ipv4_enabled = existing_isis_config.get("ipv4_enabled", False)
                ipv4_enabled = isis_config.get("ipv4_enabled", False)
                
                if existing_ipv4_enabled and not ipv4_enabled:
                    logging.info(f"[ISIS CONFIGURE] IPv4 was enabled but now disabled - removing IPv4 ISIS configuration")
                    try:
                        container_name = frr_manager._get_container_name(device_id, device_name)
                        container = frr_manager.client.containers.get(container_name)
                        
                        # Get interface from config
                        isis_interface = isis_config.get("interface", existing_device.get("interface", "eth0"))
                        # If VLAN is configured, use VLAN interface
                        vlan = data.get("vlan", existing_device.get("vlan", "0"))
                        if vlan and vlan != "0":
                            isis_interface = f"vlan{vlan}"
                        
                        # Remove IPv4 ISIS from interface
                        remove_commands = [
                            "configure terminal",
                            f"interface {isis_interface}",
                            " no ip router isis CORE",
                            "exit",
                            "exit",
                            "write"
                        ]
                        
                        config_commands = "\n".join(remove_commands)
                        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                        result = container.exec_run(["bash", "-c", exec_cmd])
                        
                        if result.exit_code == 0:
                            logging.info(f"[ISIS CONFIGURE] Successfully removed IPv4 ISIS configuration from interface {isis_interface}")
                        else:
                            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                            logging.warning(f"[ISIS CONFIGURE] Failed to remove IPv4 ISIS configuration: {output_str}")
                    except Exception as e:
                        logging.warning(f"[ISIS CONFIGURE] Failed to remove IPv4 ISIS configuration: {e}")
                
                # Check if IPv6 was previously enabled but now disabled - remove IPv6 ISIS
                existing_ipv6_enabled = existing_isis_config.get("ipv6_enabled", False)
                ipv6_enabled = isis_config.get("ipv6_enabled", False)
                
                if existing_ipv6_enabled and not ipv6_enabled:
                    logging.info(f"[ISIS CONFIGURE] IPv6 was enabled but now disabled - removing IPv6 ISIS configuration")
                    try:
                        container_name = frr_manager._get_container_name(device_id, device_name)
                        container = frr_manager.client.containers.get(container_name)
                        
                        # Get interface from config
                        isis_interface = isis_config.get("interface", existing_device.get("interface", "eth0"))
                        # If VLAN is configured, use VLAN interface
                        vlan = data.get("vlan", existing_device.get("vlan", "0"))
                        if vlan and vlan != "0":
                            isis_interface = f"vlan{vlan}"
                        
                        # Remove IPv6 ISIS from interface
                        remove_commands = [
                            "configure terminal",
                            f"interface {isis_interface}",
                            " no ipv6 router isis CORE",
                            "exit",
                            "exit",
                            "write"
                        ]
                        
                        config_commands = "\n".join(remove_commands)
                        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                        result = container.exec_run(["bash", "-c", exec_cmd])
                        
                        if result.exit_code == 0:
                            logging.info(f"[ISIS CONFIGURE] Successfully removed IPv6 ISIS configuration from interface {isis_interface}")
                        else:
                            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                            logging.warning(f"[ISIS CONFIGURE] Failed to remove IPv6 ISIS configuration: {output_str}")
                    except Exception as e:
                        logging.warning(f"[ISIS CONFIGURE] Failed to remove IPv6 ISIS configuration: {e}")
                
                # Update device with IS-IS protocol and configuration
                existing_protocols = existing_device.get("protocols", [])
                if "IS-IS" not in existing_protocols and "ISIS" not in existing_protocols:
                    existing_protocols.append("IS-IS")
                    logging.info(f"[ISIS CONFIGURE] Adding IS-IS protocol to device {device_name}")
                
                # Update ISIS configuration
                device_db.update_device(device_id, {
                    "protocols": existing_protocols,
                    "isis_config": isis_config,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[ISIS CONFIGURE] Updated device {device_name} with ISIS configuration in database")
        except Exception as e:
            logging.warning(f"[ISIS CONFIGURE] Error checking/adding device to database: {e}")
        
        # Configure ISIS neighbor
        success = configure_isis_neighbor(device_id, isis_config, device_name, ipv4=ipv4, ipv6=ipv6)
        
        if success:
            logging.info(f"[ISIS CONFIGURE] Successfully configured ISIS for device {device_name}")
            
            # Trigger ISIS status check after configuration
            try:
                logging.info(f"[ISIS STATUS] Triggering ISIS status check for device {device_id} after configuration")
                isis_monitor.force_check()
            except Exception as e:
                logging.warning(f"[ISIS STATUS] Failed to trigger ISIS status check for device {device_id}: {e}")
            
            # After configuring ISIS, apply route pool configurations if they exist
            try:
                route_pools_data = isis_config.get("route_pools", [])
                area_id = isis_config.get("area_id", "49.0001.0000.0000.0001.00")
                
                # Get all available route pools
                from utils.device_database import DeviceDatabase
                device_db = DeviceDatabase()
                all_pools_db = device_db.get_all_route_pools()
                all_pools = []
                for pool in all_pools_db:
                    all_pools.append({
                        "name": pool["pool_name"],
                        "subnet": pool["subnet"],
                        "count": pool["route_count"],
                        "first_host": pool["first_host_ip"],
                        "last_host": pool["last_host_ip"],
                        "increment_type": pool.get("increment_type", "host")
                    })
                
                # Handle both old list format and new dict format (per neighbor type)
                if isinstance(route_pools_data, dict):
                    # New format: apply route pools per neighbor type
                    for neighbor_type, route_pools in route_pools_data.items():
                        if route_pools and len(route_pools) > 0:
                            logging.info(f"[ISIS CONFIGURE] Applying route pools for area {area_id}, type {neighbor_type}: {route_pools}")
                            import threading
                            # Use default parameters to capture values at function definition time (avoid closure issues)
                            def _apply_routes(af_type=neighbor_type, pools=route_pools.copy()):
                                configure_isis_route_advertisement(
                                    device_id, device_name, area_id, 
                                    pools, all_pools, af_type=af_type
                                )
                            threading.Thread(target=_apply_routes, daemon=True).start()
                        else:
                            logging.info(f"[ISIS CONFIGURE] No route pools for area {area_id}, type {neighbor_type} - cleaning up existing routes")
                            import threading
                            # Use default parameter to capture value at function definition time (avoid closure issues)
                            def _cleanup_routes(af_type=neighbor_type):
                                cleanup_isis_route_advertisement(device_id, device_name, area_id, af_type=af_type)
                            threading.Thread(target=_cleanup_routes, daemon=True).start()
                elif isinstance(route_pools_data, list) and len(route_pools_data) > 0:
                    # Old format: apply as IPv4 (backward compatibility)
                    logging.info(f"[ISIS CONFIGURE] Applying route pools for area {area_id}: {route_pools_data} (old format)")
                    import threading
                    def _apply_routes():
                        configure_isis_route_advertisement(
                            device_id, device_name, area_id, 
                            route_pools_data, all_pools, af_type="IPv4"
                        )
                    threading.Thread(target=_apply_routes, daemon=True).start()
                else:
                    # No route pools configured - clean up existing routes
                    logging.info(f"[ISIS CONFIGURE] No route pools configured - cleaning up existing routes for area {area_id}")
                    import threading
                    def _cleanup_routes():
                        cleanup_isis_route_advertisement(device_id, device_name, area_id)
                    threading.Thread(target=_cleanup_routes, daemon=True).start()
            except Exception as e:
                logging.warning(f"[ISIS CONFIGURE] Failed to apply route pool configurations: {e}")
            
            return jsonify({
                "status": "success",
                "message": f"ISIS configured successfully for {device_name}",
                "device_id": device_id,
                "device_name": device_name
            }), 200
        else:
            logging.error(f"[ISIS CONFIGURE] Failed to configure ISIS for device {device_name}")
            return jsonify({"error": "Failed to configure ISIS"}), 500
            
    except Exception as e:
        logging.error(f"[ISIS CONFIGURE ERROR] Error configuring ISIS: {e}")
        return jsonify({"error": str(e)}), 500
@app.route("/api/device/apply", methods=["POST"])
def apply_device():
    """Apply device configuration - configure interface with IP addresses and routes"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", "")
        interface = data.get("interface", "")
        vlan = data.get("vlan", "0")
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        ipv4_mask = data.get("ipv4_mask", "24")
        ipv6_mask = data.get("ipv6_mask", "64")
        ipv4_gateway = data.get("ipv4_gateway", "")
        ipv6_gateway = data.get("ipv6_gateway", "")
        loopback_ipv4 = data.get("loopback_ipv4", "")
        loopback_ipv6 = data.get("loopback_ipv6", "")
        protocols = data.get("protocols", [])
        bgp_config = data.get("bgp_config", {})
        ospf_config = data.get("ospf_config", {})
        isis_config = data.get("isis_config", {})
        
        logging.info(f"[DEVICE APPLY] ID={device_id} Name='{device_name}' Interface='{interface}' VLAN={vlan}")
        logging.info(f"[DEVICE APPLY] IPv4={ipv4}/{ipv4_mask} IPv6={ipv6}/{ipv6_mask}")
        logging.info(f"[DEVICE APPLY] Gateways: IPv4={ipv4_gateway} IPv6={ipv6_gateway}")
        logging.info(f"[DEVICE APPLY] Protocols: {protocols}")
        logging.info(f"[DEVICE APPLY] BGP Config: {bgp_config}")
        logging.info(f"[DEVICE APPLY] OSPF Config: {ospf_config}")
        logging.info(f"[DEVICE APPLY] ISIS Config: {isis_config}")
        
        result = {
            "device_id": device_id,
            "device": device_name,
            "interface": interface,
            "vlan": vlan
        }
        
        # Determine interface name
        iface_name = f"vlan{vlan}" if (vlan and vlan != "0") else interface
        
        # Step 1: Create VLAN interface if needed
        if vlan and vlan != "0":
            try:
                # Check if VLAN interface exists
                check_result = subprocess.run(["ip", "link", "show", iface_name], 
                                            capture_output=True, text=True, timeout=5)
                if check_result.returncode != 0:
                    # Create VLAN interface
                    vlan_result = subprocess.run([
                        "ip", "link", "add", "link", interface, "name", iface_name, 
                        "type", "vlan", "id", vlan
                    ], capture_output=True, text=True, timeout=5)
                    
                    if vlan_result.returncode == 0:
                        logging.info(f"[DEVICE APPLY] Created VLAN interface {iface_name}")
                        result["vlan_created"] = True
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to create VLAN interface {iface_name}: {vlan_result.stderr}")
                        result["vlan_created"] = False
                else:
                    logging.info(f"[DEVICE APPLY] VLAN interface {iface_name} already exists")
                    result["vlan_created"] = True
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error creating VLAN interface {iface_name}: {e}")
                result["vlan_created"] = False
        
        # Step 2: Bring up interface
        try:
            bringup_result = subprocess.run(["ip", "link", "set", iface_name, "up"], 
                                          capture_output=True, text=True, timeout=5)
            if bringup_result.returncode == 0:
                logging.info(f"[DEVICE APPLY] Interface {iface_name} brought up")
                result["interface_up"] = True
            else:
                logging.warning(f"[DEVICE APPLY] Failed to bring up interface {iface_name}: {bringup_result.stderr}")
                result["interface_up"] = False
        except Exception as e:
            logging.warning(f"[DEVICE APPLY] Error bringing up interface {iface_name}: {e}")
            result["interface_up"] = False
        
        # Step 3: Configure IPv4 address
        if ipv4 and ipv4_mask:
            try:
                # Remove existing IPv4 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv4}/{ipv4_mask}", "dev", iface_name], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv4 address
                ipv4_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv4}/{ipv4_mask}", "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if ipv4_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Configured IPv4 address {ipv4}/{ipv4_mask} on {iface_name}")
                    result["ipv4_configured"] = True
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to configure IPv4 address {ipv4}/{ipv4_mask}: {ipv4_result.stderr}")
                    result["ipv4_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring IPv4 address: {e}")
                result["ipv4_configured"] = False
        
        # Step 4: Configure IPv6 address
        if ipv6 and ipv6_mask:
            try:
                # Remove existing IPv6 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv6}/{ipv6_mask}", "dev", iface_name], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv6 address
                ipv6_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv6}/{ipv6_mask}", "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if ipv6_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Configured IPv6 address {ipv6}/{ipv6_mask} on {iface_name}")
                    result["ipv6_configured"] = True
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to configure IPv6 address {ipv6}/{ipv6_mask}: {ipv6_result.stderr}")
                    result["ipv6_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring IPv6 address: {e}")
                result["ipv6_configured"] = False
        
        # Step 5: Add default routes if gateways are provided
        if ipv4_gateway:
            try:
                # Remove existing default route if any
                subprocess.run(["ip", "route", "del", "default", "via", ipv4_gateway], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new default route
                route_result = subprocess.run([
                    "ip", "route", "add", "default", "via", ipv4_gateway, "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if route_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Added IPv4 default route via {ipv4_gateway}")
                    result["ipv4_route_added"] = True
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to add IPv4 default route: {route_result.stderr}")
                    result["ipv4_route_added"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error adding IPv4 default route: {e}")
                result["ipv4_route_added"] = False
        
        if ipv6_gateway:
            try:
                # Remove existing specific route to gateway if any
                subprocess.run(["ip", "-6", "route", "del", f"{ipv6_gateway}/128", "via", ipv6_gateway], 
                             capture_output=True, text=True, timeout=5)
                
                # Add specific route to IPv6 gateway
                route6_result = subprocess.run([
                    "ip", "-6", "route", "add", f"{ipv6_gateway}/128", "via", ipv6_gateway, "dev", iface_name
                ], capture_output=True, text=True, timeout=5)
                
                if route6_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Added IPv6 gateway route {ipv6_gateway}/128 via {ipv6_gateway} dev {iface_name}")
                    result["ipv6_route_added"] = True
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to add IPv6 gateway route: {route6_result.stderr}")
                    result["ipv6_route_added"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error adding IPv6 gateway route: {e}")
                result["ipv6_route_added"] = False
        
        # Step 6: Configure loopback IP addresses on lo interface
        # Check if FRR container exists - if so, configure loopback inside container
        container_exists = False
        container = None
        try:
            from utils.frr_docker import FRRDockerManager
            frr_manager = FRRDockerManager()
            container_name = frr_manager._get_container_name(device_id, device_name)
            try:
                container = frr_manager.client.containers.get(container_name)
                if container.status == "running":
                    container_exists = True
                    logging.info(f"[DEVICE APPLY] FRR container {container_name} exists and is running, will configure loopback inside container")
            except Exception:
                logging.info(f"[DEVICE APPLY] FRR container {container_name} does not exist, will configure loopback on host")
        except Exception as e:
            logging.warning(f"[DEVICE APPLY] Could not check for FRR container: {e}, will configure loopback on host")
        
        if loopback_ipv4:
            try:
                if container_exists and container:
                    # Configure loopback inside FRR container
                    # Remove existing loopback IPv4 address if any (use /32 for host route)
                    container.exec_run(["ip", "addr", "del", f"{loopback_ipv4}/32", "dev", "lo"], timeout=5)
                    
                    # Add new loopback IPv4 address inside container
                    lo_ipv4_result = container.exec_run([
                        "ip", "addr", "add", f"{loopback_ipv4}/32", "dev", "lo"
                    ], timeout=5)
                    
                    if lo_ipv4_result.exit_code == 0:
                        logging.info(f"[DEVICE APPLY] Configured loopback IPv4 address {loopback_ipv4}/32 on lo inside container {container_name}")
                        result["loopback_ipv4_configured"] = True
                    else:
                        output_str = lo_ipv4_result.output.decode('utf-8') if isinstance(lo_ipv4_result.output, bytes) else str(lo_ipv4_result.output)
                        logging.warning(f"[DEVICE APPLY] Failed to configure loopback IPv4 address {loopback_ipv4}/32 in container: {output_str}")
                        result["loopback_ipv4_configured"] = False
                else:
                    # Configure loopback on host (fallback if container doesn't exist)
                    subprocess.run(["ip", "addr", "del", f"{loopback_ipv4}/32", "dev", "lo"], 
                                 capture_output=True, text=True, timeout=5)
                    
                    lo_ipv4_result = subprocess.run([
                        "ip", "addr", "add", f"{loopback_ipv4}/32", "dev", "lo"
                    ], capture_output=True, text=True, timeout=5)
                    
                    if lo_ipv4_result.returncode == 0:
                        logging.info(f"[DEVICE APPLY] Configured loopback IPv4 address {loopback_ipv4}/32 on lo (host)")
                        result["loopback_ipv4_configured"] = True
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to configure loopback IPv4 address {loopback_ipv4}/32: {lo_ipv4_result.stderr}")
                        result["loopback_ipv4_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring loopback IPv4 address: {e}")
                result["loopback_ipv4_configured"] = False
        
        if loopback_ipv6:
            try:
                if container_exists and container:
                    # Configure loopback inside FRR container
                    # Remove existing loopback IPv6 address if any (use /128 for host route)
                    container.exec_run(["ip", "-6", "addr", "del", f"{loopback_ipv6}/128", "dev", "lo"], timeout=5)
                    
                    # Add new loopback IPv6 address inside container
                    lo_ipv6_result = container.exec_run([
                        "ip", "-6", "addr", "add", f"{loopback_ipv6}/128", "dev", "lo"
                    ], timeout=5)
                    
                    if lo_ipv6_result.exit_code == 0:
                        logging.info(f"[DEVICE APPLY] Configured loopback IPv6 address {loopback_ipv6}/128 on lo inside container {container_name}")
                        result["loopback_ipv6_configured"] = True
                    else:
                        output_str = lo_ipv6_result.output.decode('utf-8') if isinstance(lo_ipv6_result.output, bytes) else str(lo_ipv6_result.output)
                        logging.warning(f"[DEVICE APPLY] Failed to configure loopback IPv6 address {loopback_ipv6}/128 in container: {output_str}")
                        result["loopback_ipv6_configured"] = False
                else:
                    # Configure loopback on host (fallback if container doesn't exist)
                    subprocess.run(["ip", "-6", "addr", "del", f"{loopback_ipv6}/128", "dev", "lo"], 
                                 capture_output=True, text=True, timeout=5)
                    
                    lo_ipv6_result = subprocess.run([
                        "ip", "-6", "addr", "add", f"{loopback_ipv6}/128", "dev", "lo"
                    ], capture_output=True, text=True, timeout=5)
                    
                    if lo_ipv6_result.returncode == 0:
                        logging.info(f"[DEVICE APPLY] Configured loopback IPv6 address {loopback_ipv6}/128 on lo (host)")
                        result["loopback_ipv6_configured"] = True
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to configure loopback IPv6 address {loopback_ipv6}/128: {lo_ipv6_result.stderr}")
                        result["loopback_ipv6_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring loopback IPv6 address: {e}")
                result["loopback_ipv6_configured"] = False
        
        # Update device status in database
        try:
            if device_id:
                device_db.update_device_status(device_id, "Running")
                logging.info(f"[DEVICE DB] Device {device_id} status updated to Running")
        except Exception as e:
            logging.warning(f"[DEVICE DB] Failed to update device {device_id} status: {e}")
        
        # Save device to database if it doesn't exist
        try:
            if device_id:
                existing_device = device_db.get_device(device_id)
                if not existing_device:
                    logging.info(f"[DEVICE APPLY] Device {device_id} not found in database, adding it")
                    device_data = {
                        "device_id": device_id,
                        "device_name": device_name,
                        "interface": interface,
                        "vlan": vlan,
                        "ipv4_address": ipv4,
                        "ipv6_address": ipv6,
                        "ipv4_mask": ipv4_mask,
                        "ipv6_mask": ipv6_mask,
                        "ipv4_gateway": ipv4_gateway,
                        "ipv6_gateway": ipv6_gateway,
                        "loopback_ipv4": loopback_ipv4,
                        "loopback_ipv6": loopback_ipv6,
                        "status": "Running",
                        "protocols": protocols,
                        "bgp_config": bgp_config,
                        "ospf_config": ospf_config,
                        "isis_config": isis_config
                    }
                    
                    if device_db.add_device(device_data):
                        logging.info(f"[DEVICE APPLY] Successfully added device {device_name} to database")
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to add device {device_name} to database")
                else:
                    logging.info(f"[DEVICE APPLY] Device {device_id} already exists in database")
                    # Always update IP addresses and related fields if provided (they may have changed)
                    update_data = {}
                    
                    # Update IPv4 address, mask, and gateway if provided
                    if ipv4:
                        existing_ipv4 = existing_device.get("ipv4_address", "")
                        if existing_ipv4 != ipv4:
                            logging.info(f"[DEVICE APPLY] IPv4 address changed from '{existing_ipv4}' to '{ipv4}' for device {device_name}")
                        update_data.update({
                            "ipv4_address": ipv4,
                            "ipv4_mask": ipv4_mask,
                            "ipv4_gateway": ipv4_gateway
                        })
                    else:
                        # If IPv4 is empty, clear it from database
                        if existing_device.get("ipv4_address"):
                            logging.info(f"[DEVICE APPLY] Clearing IPv4 address for device {device_name}")
                            update_data.update({
                                "ipv4_address": None,
                                "ipv4_mask": None,
                                "ipv4_gateway": None
                            })
                    
                    # Update IPv6 address, mask, and gateway if provided
                    if ipv6:
                        existing_ipv6 = existing_device.get("ipv6_address", "")
                        if existing_ipv6 != ipv6:
                            logging.info(f"[DEVICE APPLY] IPv6 address changed from '{existing_ipv6}' to '{ipv6}' for device {device_name}")
                        update_data.update({
                            "ipv6_address": ipv6,
                            "ipv6_mask": ipv6_mask,
                            "ipv6_gateway": ipv6_gateway
                        })
                    else:
                        # If IPv6 is empty, clear it from database
                        if existing_device.get("ipv6_address"):
                            logging.info(f"[DEVICE APPLY] Clearing IPv6 address for device {device_name}")
                            update_data.update({
                                "ipv6_address": None,
                                "ipv6_mask": None,
                                "ipv6_gateway": None
                            })
                    
                    # Update loopback IP addresses if provided
                    if loopback_ipv4:
                        existing_loopback_ipv4 = existing_device.get("loopback_ipv4", "")
                        if existing_loopback_ipv4 != loopback_ipv4:
                            logging.info(f"[DEVICE APPLY] Loopback IPv4 address changed from '{existing_loopback_ipv4}' to '{loopback_ipv4}' for device {device_name}")
                        update_data["loopback_ipv4"] = loopback_ipv4
                    else:
                        # If loopback IPv4 is empty, clear it from database
                        if existing_device.get("loopback_ipv4"):
                            logging.info(f"[DEVICE APPLY] Clearing loopback IPv4 address for device {device_name}")
                            update_data["loopback_ipv4"] = None
                    
                    if loopback_ipv6:
                        existing_loopback_ipv6 = existing_device.get("loopback_ipv6", "")
                        if existing_loopback_ipv6 != loopback_ipv6:
                            logging.info(f"[DEVICE APPLY] Loopback IPv6 address changed from '{existing_loopback_ipv6}' to '{loopback_ipv6}' for device {device_name}")
                        update_data["loopback_ipv6"] = loopback_ipv6
                    else:
                        # If loopback IPv6 is empty, clear it from database
                        if existing_device.get("loopback_ipv6"):
                            logging.info(f"[DEVICE APPLY] Clearing loopback IPv6 address for device {device_name}")
                            update_data["loopback_ipv6"] = None
                    
                    # Also update interface and VLAN if they changed
                    if interface and interface != existing_device.get("interface", ""):
                        update_data["interface"] = interface
                    if vlan and vlan != existing_device.get("vlan", "0"):
                        update_data["vlan"] = vlan
                    
                    # Update database if there are changes
                    if update_data:
                        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                        device_db.update_device(device_id, update_data)
                        logging.info(f"[DEVICE APPLY] Updated device {device_name} in database with: {list(update_data.keys())}")
                    else:
                        logging.info(f"[DEVICE APPLY] No database updates needed for device {device_name}")
        except Exception as e:
            logging.warning(f"[DEVICE APPLY] Error checking/adding device to database: {e}")
        
        logging.info(f"[DEVICE APPLY] Device {device_name} configuration applied successfully")
        
        return jsonify({
            "status": "applied",
            "details": result
        }), 200
        
    except Exception as e:
        logging.error(f"[DEVICE APPLY ERROR] Failed to apply device configuration: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/ospf/configure", methods=["POST"])
def configure_ospf():
    """Configure OSPF for a specific device using FRR."""
    data = request.get_json()
    logging.info(f"OSPF Configuration Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing OSPF configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name")
        interface = data.get("interface")
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        # Handle both 'ospf_config' and 'ospf' field names for backward compatibility
        ospf_config = data.get("ospf_config", data.get("ospf", {}))
        
        if not device_id or not ospf_config:
            return jsonify({"error": "Missing device_id or OSPF configuration"}), 400

        # Import OSPF utilities
        from utils.ospf import configure_ospf_neighbor
        
        # Configure OSPF neighbor using FRR Docker
        logging.info(f"OSPF Config Debug: {ospf_config}")
        logging.info(f"OSPF Config Keys: {list(ospf_config.keys())}")
        
        # Check if IPv4 and/or IPv6 OSPF is enabled
        ipv4_enabled = ospf_config.get("ipv4_enabled", True)  # Default to True for backward compatibility
        ipv6_enabled = ospf_config.get("ipv6_enabled", False)
        
        logging.info(f"IPv4 OSPF enabled: {ipv4_enabled}, IPv6 OSPF enabled: {ipv6_enabled}")
        
        # Ensure FRR container exists before configuring OSPF
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Check if container exists, if not create it
        container_name = frr_manager._get_container_name(device_id, device_name)
        try:
            container = frr_manager.client.containers.get(container_name)
            if container.status != "running":
                logging.info(f"[OSPF CONFIGURE] Container {container_name} exists but not running, removing and recreating")
                container.remove(force=True)
                container = None
        except Exception:
            logging.info(f"[OSPF CONFIGURE] Container {container_name} does not exist, will create it")
            container = None
        
        if container is None:
            # Create device config for container creation
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": data.get("interface", "ens4np0"),
                "vlan": data.get("vlan", "0"),
                "ospf_config": ospf_config
            }
            
            logging.info(f"[OSPF CONFIGURE] Creating FRR container for device {device_name}")
            created_container_name = frr_manager.start_frr_container(device_id, device_config)
            if not created_container_name:
                logging.error(f"[OSPF CONFIGURE] Failed to create FRR container for device {device_name}")
                return jsonify({"error": "Failed to create FRR container"}), 500
            
            logging.info(f"[OSPF CONFIGURE] Successfully created FRR container: {created_container_name}")
        
        # Save device to database if it doesn't exist
        try:
            from datetime import datetime, timezone
            existing_device = device_db.get_device(device_id)
            if not existing_device:
                logging.info(f"[OSPF CONFIGURE] Device {device_id} not found in database, adding it")
                device_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": data.get("interface", "ens4np0"),
                    "vlan": data.get("vlan", "0"),
                    "ipv4_address": ipv4,
                    "ipv6_address": ipv6,
                    "ipv4_mask": data.get("ipv4_mask", "24"),
                    "ipv6_mask": data.get("ipv6_mask", "64"),
                    "ipv4_gateway": data.get("ipv4_gateway", ""),
                    "ipv6_gateway": data.get("ipv6_gateway", ""),
                    "protocols": ["OSPF"],  # Add OSPF protocol to the device
                    "ospf_config": ospf_config,  # Save OSPF configuration
                    "status": "Running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                
                if device_db.add_device(device_data):
                    logging.info(f"[OSPF CONFIGURE] Successfully added device {device_name} to database")
                else:
                    logging.warning(f"[OSPF CONFIGURE] Failed to add device {device_name} to database")
            else:
                logging.info(f"[OSPF CONFIGURE] Device {device_id} already exists in database")
                
                # IMPORTANT: Check for IPv6 removal BEFORE updating database
                # Get existing OSPF config before it's overwritten
                existing_ospf_config = existing_device.get("ospf_config", {})
                if isinstance(existing_ospf_config, str):
                    import json
                    try:
                        existing_ospf_config = json.loads(existing_ospf_config)
                    except:
                        existing_ospf_config = {}
                
                # Check if IPv4 was previously enabled but now disabled - remove IPv4 OSPF
                existing_ipv4_enabled = existing_ospf_config.get("ipv4_enabled", False)
                
                if existing_ipv4_enabled and not ipv4_enabled:
                    logging.info(f"[OSPF CONFIGURE] IPv4 was enabled but now disabled - removing IPv4 OSPF configuration")
                    try:
                        from utils.ospf import stop_ospf_neighbor
                        # Stop IPv4 OSPF
                        stop_ospf_neighbor(device_id, device_name, af="IPv4")
                        logging.info(f"[OSPF CONFIGURE] Successfully removed IPv4 OSPF configuration")
                    except Exception as e:
                        logging.warning(f"[OSPF CONFIGURE] Failed to remove IPv4 OSPF configuration: {e}")
                
                # Check if IPv6 was previously enabled but now disabled - remove IPv6 OSPF
                existing_ipv6_enabled = existing_ospf_config.get("ipv6_enabled", False)
                
                if existing_ipv6_enabled and not ipv6_enabled:
                    logging.info(f"[OSPF CONFIGURE] IPv6 was enabled but now disabled - removing IPv6 OSPF configuration")
                    try:
                        from utils.ospf import stop_ospf_neighbor
                        # Stop IPv6 OSPF
                        stop_ospf_neighbor(device_id, device_name, af="IPv6")
                        logging.info(f"[OSPF CONFIGURE] Successfully removed IPv6 OSPF configuration")
                    except Exception as e:
                        logging.warning(f"[OSPF CONFIGURE] Failed to remove IPv6 OSPF configuration: {e}")
                
                # Update device with OSPF protocol and configuration
                existing_protocols = existing_device.get("protocols", [])
                if "OSPF" not in existing_protocols:
                    existing_protocols.append("OSPF")
                
                device_db.update_device(device_id, {
                    "protocols": existing_protocols,
                    "ospf_config": ospf_config,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[OSPF CONFIGURE] Updated device {device_name} with OSPF configuration")
        except Exception as e:
            logging.warning(f"[OSPF CONFIGURE] Error checking/adding device to database: {e}")
        
        # Save OSPF route pool attachments to database (similar to BGP)
        try:
            route_pools_data = ospf_config.get("route_pools", [])
            area_id = ospf_config.get("area_id", "0.0.0.0")
            
            # Handle both old list format and new dict format (per neighbor type)
            if isinstance(route_pools_data, dict):
                # New format: route_pools = {"IPv4": [pools], "IPv6": [pools]}
                # Store as area_id + neighbor_type (e.g., "0.0.0.0:IPv4")
                all_route_pools = []
                for neighbor_type, pools in route_pools_data.items():
                    if pools:
                        area_key = f"{area_id}:{neighbor_type}"
                        device_db.attach_route_pools_to_device(device_id, area_key, pools)
                        all_route_pools.extend(pools)
                        logging.info(f"[OSPF CONFIGURE] Saved {len(pools)} route pool attachments for device {device_id}, area {area_id}, type {neighbor_type}")
                
                if all_route_pools:
                    logging.info(f"[OSPF CONFIGURE] Total {len(all_route_pools)} route pool attachments saved for device {device_id}")
                else:
                    # Remove all attachments for this device/area
                    device_db.remove_device_route_pools(device_id, area_id)
                    logging.info(f"[OSPF CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id}")
            elif isinstance(route_pools_data, list) and len(route_pools_data) > 0:
                # Old format: route_pools = [pools]
                device_db.attach_route_pools_to_device(device_id, area_id, route_pools_data)
                logging.info(f"[OSPF CONFIGURE] Saved {len(route_pools_data)} route pool attachments for device {device_id} and area {area_id} (old format)")
            else:
                # No route pools configured - remove all attachments for this device/area
                device_db.remove_device_route_pools(device_id, area_id)
                logging.info(f"[OSPF CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id}")
        except Exception as e:
            logging.warning(f"[OSPF CONFIGURE] Failed to save route pool attachments: {e}")
        
        # Configure OSPF neighbor
        try:
            logging.info(f"[OSPF CONFIGURE] Configuring OSPF for device {device_name}")
            success = configure_ospf_neighbor(device_id, ospf_config, device_name)
            
            if success:
                logging.info(f"[OSPF CONFIGURE] Successfully configured OSPF for device {device_name}")
                
                # After configuring OSPF, apply route pool configurations if they exist
                try:
                    route_pools_data = ospf_config.get("route_pools", [])
                    area_id = ospf_config.get("area_id", "0.0.0.0")
                    
                    # Get all available route pools
                    all_pools_db = device_db.get_all_route_pools()
                    all_pools = []
                    for pool in all_pools_db:
                        all_pools.append({
                            "name": pool["pool_name"],
                            "subnet": pool["subnet"],
                            "count": pool["route_count"],
                            "first_host": pool["first_host_ip"],
                            "last_host": pool["last_host_ip"],
                            "increment_type": pool.get("increment_type", "host")
                        })
                    
                    # Handle both old list format and new dict format (per neighbor type)
                    if isinstance(route_pools_data, dict):
                        # New format: apply route pools per neighbor type
                        for neighbor_type, route_pools in route_pools_data.items():
                            if route_pools and len(route_pools) > 0:
                                logging.info(f"[OSPF CONFIGURE] Applying route pools for area {area_id}, type {neighbor_type}: {route_pools}")
                                import threading
                                def _apply_routes(af_type=neighbor_type, pools=route_pools):
                                    configure_ospf_route_advertisement(
                                        device_id, device_name, area_id, 
                                        pools, all_pools, af_type=af_type
                                    )
                                threading.Thread(target=_apply_routes, daemon=True).start()
                            else:
                                logging.info(f"[OSPF CONFIGURE] No route pools for area {area_id}, type {neighbor_type} - cleaning up existing routes")
                                import threading
                                def _cleanup_routes(af_type=neighbor_type):
                                    cleanup_ospf_route_advertisement(device_id, device_name, area_id, af_type=af_type)
                                threading.Thread(target=_cleanup_routes, daemon=True).start()
                    elif isinstance(route_pools_data, list) and len(route_pools_data) > 0:
                        # Old format: apply as IPv4 (backward compatibility)
                        logging.info(f"[OSPF CONFIGURE] Applying route pools for area {area_id}: {route_pools_data} (old format)")
                        import threading
                        def _apply_routes():
                            configure_ospf_route_advertisement(
                                device_id, device_name, area_id, 
                                route_pools_data, all_pools, af_type="IPv4"
                            )
                        threading.Thread(target=_apply_routes, daemon=True).start()
                    else:
                        # No route pools configured - clean up existing routes
                        logging.info(f"[OSPF CONFIGURE] No route pools configured - cleaning up existing routes for area {area_id}")
                        import threading
                        def _cleanup_routes():
                            cleanup_ospf_route_advertisement(device_id, device_name, area_id)
                        threading.Thread(target=_cleanup_routes, daemon=True).start()
                except Exception as e:
                    logging.warning(f"[OSPF CONFIGURE] Failed to apply route pool configurations: {e}")
                
                # Trigger OSPF status check after configuration
                try:
                    logging.info(f"[OSPF STATUS] Triggering OSPF status check for device {device_id} after configuration")
                    ospf_monitor.force_check()
                except Exception as e:
                    logging.warning(f"[OSPF STATUS] Failed to trigger OSPF status check for device {device_id}: {e}")
                
                return jsonify({
                    "status": "success",
                    "message": f"OSPF configured successfully for device {device_name}",
                    "device_id": device_id,
                    "device_name": device_name,
                    "ospf_config": ospf_config
                }), 200
            else:
                logging.error(f"[OSPF CONFIGURE] Failed to configure OSPF for device {device_name}")
                return jsonify({"error": "Failed to configure OSPF"}), 500
                
        except Exception as e:
            logging.error(f"[OSPF CONFIGURE] Error configuring OSPF for device {device_name}: {e}")
            return jsonify({"error": f"OSPF configuration error: {str(e)}"}), 500
            
    except Exception as e:
        logging.error(f"[OSPF CONFIGURE ERROR] Failed to configure OSPF: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/stop", methods=["POST"])
def stop_device():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", "")
        interface = data.get("interface", "")
        vlan = data.get("vlan", "0")
        protocols = data.get("protocols", [])
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        
        logging.info(f"[DEVICE STOP] ID={device_id} Name='{device_name}' Interface='{interface}' Protocols={protocols}")
        
        result = {
            "device_id": device_id,
            "device": device_name,
            "interface": interface,
        }
        
        # Stop FRR protocols and shutdown interface (keep container running)
        try:
            from utils.frr_docker import FRRDockerManager
            import json
            
            # First, check if device exists in database
            device_info = device_db.get_device(device_id)
            if not device_info:
                logging.warning(f"[DEVICE STOP] Device {device_id} not found in database")
                return jsonify({"error": "Device not found"}), 404
            
            frr_manager = FRRDockerManager()
            container_name = frr_manager._get_container_name(device_id, device_name)
            
            logging.info(f"[DEVICE STOP] Shutting down protocols and interface for device {device_name} (container: {container_name})")
            
            # Get protocol configs from payload first (if provided), otherwise from database
            bgp_config = data.get("bgp_config")
            ospf_config = data.get("ospf_config")
            isis_config = data.get("isis_config")
            
            # Parse BGP config if from database
            if not bgp_config:
                bgp_config_raw = device_info.get("bgp_config", {})
                if isinstance(bgp_config_raw, str) and bgp_config_raw:
                    try:
                        bgp_config = json.loads(bgp_config_raw)
                    except:
                        bgp_config = {}
                else:
                    bgp_config = bgp_config_raw if bgp_config_raw else {}
            
            # Parse OSPF config if from database
            if not ospf_config:
                ospf_config_raw = device_info.get("ospf_config", {})
                if isinstance(ospf_config_raw, str) and ospf_config_raw:
                    try:
                        ospf_config = json.loads(ospf_config_raw)
                    except:
                        ospf_config = {}
                else:
                    ospf_config = ospf_config_raw if ospf_config_raw else {}
            
            # Parse ISIS config if from database
            if not isis_config:
                isis_config_raw = device_info.get("isis_config", {})
                if isinstance(isis_config_raw, str) and isis_config_raw:
                    try:
                        isis_config = json.loads(isis_config_raw)
                    except:
                        isis_config = {}
                else:
                    isis_config = isis_config_raw if isis_config_raw else {}
            
            # Only include configs that are not empty
            if bgp_config and not bgp_config.get("bgp_neighbor_ipv4") and not bgp_config.get("bgp_neighbor_ipv6"):
                bgp_config = None
            if ospf_config and not ospf_config.get("area_id"):
                ospf_config = None
            if isis_config and not isis_config.get("area_id"):
                isis_config = None
            
            logging.info(f"[DEVICE STOP] Protocol configs - BGP: {bool(bgp_config)}, OSPF: {bool(ospf_config)}, ISIS: {bool(isis_config)}")
            
            # Stop protocols and shutdown interface (keep container running)
            container_stopped = frr_manager.stop_frr_container(
                device_id=device_id,
                device_name=device_name,
                protocols=protocols,
                bgp_config=bgp_config,
                ospf_config=ospf_config,
                isis_config=isis_config,
                interface=interface,
                vlan=vlan
            )
            
            if container_stopped:
                logging.info(f"[DEVICE STOP] Successfully shut down protocols and interface for {device_name}")
                result["container_stopped"] = True
                result["protocols_shutdown"] = True
                
                # Update all protocol statuses in database to reflect protocols stopped
                try:
                    now = datetime.now(timezone.utc).isoformat()
                    update_data = {
                        # BGP status
                        'bgp_established': False,
                        'bgp_ipv4_established': False,
                        'bgp_ipv4_state': 'Idle',
                        'bgp_ipv6_established': False,
                        'bgp_ipv6_state': 'Idle',
                        'bgp_manual_override': True,  # Prevent monitor from overriding
                        'bgp_manual_override_time': now,
                        # OSPF status
                        'ospf_established': False,
                        'ospf_state': 'Down',
                        'ospf_ipv4_running': False,
                        'ospf_ipv4_established': False,
                        'ospf_ipv6_running': False,
                        'ospf_ipv6_established': False,
                        'ospf_neighbors': None,
                        'ospf_manual_override': True,  # Prevent monitor from overriding
                        'ospf_manual_override_time': now,
                        # ISIS status
                        'isis_running': False,
                        'isis_state': 'Down',
                        'isis_established': False,
                        'isis_neighbors': None,
                        'isis_manual_override': True,  # Prevent monitor from overriding
                        'isis_manual_override_time': now,
                        # Update timestamps
                        'last_bgp_check': now,
                        'last_ospf_check': now,
                        'last_isis_check': now,
                    }
                    device_db.update_device(device_id, update_data)
                    logging.info(f"[DEVICE STOP] Updated all protocol statuses to stopped in database for {device_name} with manual override flags")
                except Exception as e:
                    logging.warning(f"[DEVICE STOP] Failed to update protocol statuses in database: {e}")
            else:
                logging.warning(f"[DEVICE STOP] Failed to shutdown protocols for {device_name}")
                result["container_stopped"] = False
                result["protocols_shutdown"] = False
        except Exception as e:
            logging.error(f"[DEVICE STOP] Error shutting down protocols for {device_name}: {e}")
            import traceback
            logging.error(f"[DEVICE STOP] Traceback: {traceback.format_exc()}")
            result["container_stopped"] = False
            result["protocols_shutdown"] = False
        
        # Note: We do NOT shutdown the interface on the host, as this would affect other devices
        # using the same physical interface with different VLANs. The interface shutdown inside
        # the container is sufficient to stop the device's operation.
        logging.info(f"[DEVICE STOP] Device {device_name} stopped (protocols stopped, interface down inside container)")
        result["interface_shutdown"] = True  # Interface is shut down inside container, not on host
        
        # Update device status in database and ensure all protocol statuses are cleared
        try:
            # First update device status
            device_db.update_device_status(device_id, "Stopped")
            
            # Then ensure all protocol statuses are cleared (in case container wasn't running)
            now = datetime.now(timezone.utc).isoformat()
            update_data = {
                # BGP status
                'bgp_established': False,
                'bgp_ipv4_established': False,
                'bgp_ipv4_state': 'Idle',
                'bgp_ipv6_established': False,
                'bgp_ipv6_state': 'Idle',
                'bgp_manual_override': True,  # Prevent monitor from overriding
                'bgp_manual_override_time': now,
                # OSPF status
                'ospf_established': False,
                'ospf_state': 'Down',
                'ospf_ipv4_running': False,
                'ospf_ipv4_established': False,
                'ospf_ipv6_running': False,
                'ospf_ipv6_established': False,
                'ospf_neighbors': None,
                'ospf_manual_override': True,  # Prevent monitor from overriding
                'ospf_manual_override_time': now,
                # ISIS status
                'isis_running': False,
                'isis_state': 'Down',
                'isis_established': False,
                'isis_neighbors': None,
                'isis_manual_override': True,  # Prevent monitor from overriding
                'isis_manual_override_time': now,
                # Update timestamps
                'last_bgp_check': now,
                'last_ospf_check': now,
                'last_isis_check': now,
            }
            device_db.update_device(device_id, update_data)
            logging.info(f"[DEVICE DB] Device {device_id} status updated to Stopped and all protocol statuses cleared with manual override flags")
        except Exception as e:
            logging.warning(f"[DEVICE DB] Failed to update device {device_id} status: {e}")
            # Don't fail device stop if database operation fails
        
        return jsonify({
            "status": "stopped",
            "details": result
        }), 200
    except Exception as e:
        logging.error(f"[DEVICE ERROR] Failed to stop device: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/remove", methods=["POST"])
def remove_device():
    data = request.get_json()
    device_id = data.get("device_id")
    device_name = data.get("device_name", "")

    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    try:
        # Get device info from database before removing it (needed for cleanup)
        device_info = None
        try:
            device_info = device_db.get_device(device_id)
            if device_info and not device_name:
                device_name = device_info.get("device_name", "")
        except Exception as e:
            logging.warning(f"[DEVICE REMOVE] Failed to get device info from database: {e}")
        
        # Stop and remove FRR Docker container for this device
        container_removed = False
        try:
            from utils.frr_docker import remove_frr_container
            
            success = remove_frr_container(device_id, device_name)
            if success:
                logging.info(f"[DEVICE REMOVE] FRR container stopped and removed for {device_name} ({device_id})")
                container_removed = True
            else:
                logging.warning(f"[DEVICE REMOVE] Failed to stop/remove FRR container for {device_name}")
        except Exception as e:
            logging.error(f"[DEVICE REMOVE] Exception while removing FRR container for {device_name}: {e}")
            import traceback
            logging.error(f"[DEVICE REMOVE] Traceback: {traceback.format_exc()}")

        # Clean up device-to-IP mapping for this device
        print(f"[REMOVE] Cleaning up device-to-IP mapping for device '{device_name}' (ID: {device_id})")
        keys_to_remove = []
        for key, mapped_device_id in DEVICE_IP_MAPPING.items():
            if mapped_device_id == device_id:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del DEVICE_IP_MAPPING[key]
            print(f"[REMOVE] Removed IP mapping: {key}")
        
        print(f"[REMOVE] Cleaned up {len(keys_to_remove)} IP mappings for device {device_id}")
        
        # Call the device manager to handle protocol cleanup
        from utils.device_manager import DeviceManager
        result = DeviceManager.remove_device_protocols(data)

        # Clean up OSPF configuration from server if device has OSPF
        if device_info:
            try:
                protocols = device_info.get("protocols", [])
                if isinstance(protocols, list) and "OSPF" in protocols:
                    logging.info(f"[DEVICE REMOVE] Cleaning up OSPF configuration for device {device_id}")
                    # Directly call OSPF cleanup functions
                    from utils.ospf import cleanup_device_routes, remove_ospf_config
                    cleanup_device_routes(device_id)
                    remove_ospf_config(device_id)
                    logging.info(f"[DEVICE REMOVE] OSPF cleanup completed for device {device_id}")
            except Exception as e:
                logging.warning(f"[DEVICE REMOVE] Failed to cleanup OSPF for device {device_id}: {e}")
                # Don't fail device removal if OSPF cleanup fails

        # Remove device from database
        db_removed = False
        try:
            db_removed = device_db.remove_device(device_id)
            if db_removed:
                logging.info(f"[DEVICE DB] Device {device_id} ({device_name}) removed from database")
            else:
                logging.error(f"[DEVICE DB] Failed to remove device {device_id} ({device_name}) from database")
        except Exception as e:
            logging.error(f"[DEVICE DB] Exception while removing device {device_id} from database: {e}")
            import traceback
            logging.error(f"[DEVICE DB] Traceback: {traceback.format_exc()}")

        # Return status with details
        return jsonify({
            "status": "removed" if db_removed else "partial",
            "details": result,
            "mappings_cleaned": len(keys_to_remove),
            "container_removed": container_removed,
            "database_removed": db_removed
        }), 200

    except Exception as e:
        logging.error(f"[REMOVE ERROR] {e}")
        return jsonify({"error": str(e)}), 500


def add_static_route_background(device_id, device_name, gateway, container_name_prefix="ostg-frr"):
    """Add static route in background after container is ready (non-blocking)."""
    import threading
    import time
    import ipaddress
    
    def _add_route():
        try:
            from utils.frr_docker import FRRDockerManager
            frr_manager = FRRDockerManager()
            
            # Wait for container and staticd to be ready
            logging.info(f"[ROUTE BG] Starting background route addition for {device_name}")
            time.sleep(8)  # Wait for staticd to initialize
            
            try:
                container_name = frr_manager._get_container_name(device_id, device_name)
                container = frr_manager.client.containers.get(container_name)
                
                # Determine if gateway is IPv4 or IPv6
                try:
                    gateway_ip = ipaddress.ip_address(gateway)
                    is_ipv6 = isinstance(gateway_ip, ipaddress.IPv6Address)
                except ValueError:
                    logging.error(f"[ROUTE BG] Invalid gateway address: {gateway}")
                    return
                
                # Add appropriate default route based on gateway type
                if is_ipv6:
                    # Add IPv6 default route
                    route_cmd = f"vtysh -c 'configure terminal' -c 'ipv6 route ::/0 {gateway}' -c 'end' -c 'write memory'"
                    route_type = "IPv6 default route ::/0"
                else:
                    # Add IPv4 default route
                    route_cmd = f"vtysh -c 'configure terminal' -c 'ip route 0.0.0.0/0 {gateway}' -c 'end' -c 'write memory'"
                    route_type = "IPv4 default route 0.0.0.0/0"
                
                route_result = container.exec_run(route_cmd)
                
                if route_result.exit_code == 0:
                    logging.info(f"[ROUTE BG] ✅ Added {route_type} via {gateway} for {device_name}")
                else:
                    output_str = route_result.output.decode('utf-8') if isinstance(route_result.output, bytes) else str(route_result.output)
                    logging.warning(f"[ROUTE BG] Failed to add {route_type} for {device_name}: {output_str}")
            except Exception as e:
                logging.error(f"[ROUTE BG] Error adding route for {device_name}: {e}")
                
        except Exception as e:
            logging.error(f"[ROUTE BG] Background route thread error for {device_name}: {e}")
    
    # Start background thread
    thread = threading.Thread(target=_add_route, daemon=True)
    thread.start()
    logging.info(f"[ROUTE BG] Started background thread for {device_name}")


@app.route("/api/device/arp/check", methods=["POST"])
def check_arp_resolution():
    """Check ARP resolution for a given IP address."""
    data = request.get_json()
    ip_address = data.get("ip_address")
    interface = data.get("interface")  # Optional interface parameter
    vlan = data.get("vlan", "0")  # Optional VLAN parameter
    
    if not ip_address:
        return jsonify({"error": "IP address is required"}), 400
    
    try:
        # Detect if target IP is IPv4 or IPv6
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            is_ipv6 = isinstance(ip_obj, ipaddress.IPv6Address)
        except ValueError:
            return jsonify({"error": f"Invalid IP address: {ip_address}"}), 400
        
        # Determine the actual interface name based on VLAN configuration
        actual_interface = interface
        if interface and vlan != "0" and vlan != "":
            # VLAN is configured - always use VLAN interface for ARP checks
            # Try new naming convention first (vlan20)
            new_interface = f"vlan{vlan}"
            old_interface = f"vlan{vlan}@{interface}"
            
            # Check which interface actually exists
            new_exists = subprocess.run(["ip", "link", "show", new_interface], capture_output=True).returncode == 0
            old_exists = subprocess.run(["ip", "link", "show", old_interface], capture_output=True).returncode == 0
            
            if new_exists:
                actual_interface = new_interface
                logging.debug(f"[{'NDP' if is_ipv6 else 'ARP'} CHECK] VLAN {vlan} configured - using new VLAN interface: {actual_interface}")
            elif old_exists:
                actual_interface = old_interface
                logging.debug(f"[{'NDP' if is_ipv6 else 'ARP'} CHECK] VLAN {vlan} configured - using old VLAN interface: {actual_interface}")
            else:
                actual_interface = new_interface
                logging.debug(f"[{'NDP' if is_ipv6 else 'ARP'} CHECK] VLAN {vlan} configured - VLAN interface doesn't exist, using new naming: {actual_interface}")
        elif interface:
            # No VLAN configured (VLAN ID = 0) - use physical interface
            actual_interface = interface
            logging.debug(f"[{'NDP' if is_ipv6 else 'ARP'} CHECK] No VLAN configured (VLAN ID = 0) - using physical interface: {actual_interface}")
        
        # Build command - check specific interface if provided, otherwise check all
        if is_ipv6:
            # Use IPv6 neighbor discovery commands
            if actual_interface:
                cmd = ["ip", "-6", "neigh", "show", ip_address, "dev", actual_interface]
            else:
                cmd = ["ip", "-6", "neigh", "show", ip_address]
        else:
            # Use IPv4 ARP commands
            if actual_interface:
                cmd = ["ip", "neigh", "show", ip_address, "dev", actual_interface]
            else:
                cmd = ["ip", "neigh", "show", ip_address]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        
        protocol_name = "NDP" if is_ipv6 else "ARP"
        
        if result.returncode == 0 and result.stdout.strip():
            # Check if ARP/NDP entry exists and is in good state
            arp_output = result.stdout.strip()
            if "REACHABLE" in arp_output or "STALE" in arp_output:
                return jsonify({"resolved": True, "status": f"{protocol_name} resolved", "output": arp_output}), 200
            elif "INCOMPLETE" in arp_output or "FAILED" in arp_output:
                return jsonify({"resolved": False, "status": f"{protocol_name} incomplete/failed", "output": arp_output}), 200
            elif "DELAY" in arp_output or "PROBE" in arp_output:
                return jsonify({"resolved": False, "status": f"{protocol_name} in progress", "output": arp_output}), 200
            else:
                return jsonify({"resolved": False, "status": f"{protocol_name} unknown state", "output": arp_output}), 200
        else:
            # If no ARP/NDP entry found and interface was specified, try without interface
            if interface:
                logging.debug(f"[{protocol_name}] No {protocol_name} entry found on {interface}, trying all interfaces")
                if is_ipv6:
                    result_all = subprocess.run(["ip", "-6", "neigh", "show", ip_address], 
                                              capture_output=True, text=True, timeout=5)
                else:
                    result_all = subprocess.run(["ip", "neigh", "show", ip_address], 
                                              capture_output=True, text=True, timeout=5)
                if result_all.returncode == 0 and result_all.stdout.strip():
                    arp_output = result_all.stdout.strip()
                    if "REACHABLE" in arp_output or "STALE" in arp_output:
                        return jsonify({"resolved": True, "status": f"{protocol_name} resolved (on different interface)", "output": arp_output}), 200
                    else:
                        return jsonify({"resolved": False, "status": f"{protocol_name} incomplete/failed", "output": arp_output}), 200
            
            return jsonify({"resolved": False, "status": f"No {protocol_name} entry found", "output": ""}), 200
            
    except subprocess.TimeoutExpired:
        return jsonify({"resolved": False, "status": f"{protocol_name} check timeout", "output": ""}), 200
    except Exception as e:
        return jsonify({"resolved": False, "status": f"{protocol_name} check error: {str(e)}", "output": ""}), 200


@app.route("/api/device/arp/check/batch", methods=["POST"])
def check_arp_resolution_batch():
    """Check ARP resolution for multiple IP addresses in a single request (batching optimization)."""
    data = request.get_json()
    ip_addresses = data.get("ip_addresses", [])
    
    if not ip_addresses:
        return jsonify({"error": "IP addresses list is required"}), 400
    
    results = {}
    try:
        # Get all ARP entries at once
        result = subprocess.run(["ip", "neigh", "show"], 
                              capture_output=True, text=True, timeout=5)
        
        arp_entries = {}
        if result.returncode == 0 and result.stdout.strip():
            # Parse all ARP entries
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 1:
                        ip = parts[0]
                        arp_entries[ip] = line
        
        # Check each requested IP
        for ip_address in ip_addresses:
            if ip_address in arp_entries:
                arp_output = arp_entries[ip_address]
                if "REACHABLE" in arp_output or "STALE" in arp_output:
                    results[ip_address] = {"resolved": True, "status": "ARP resolved", "output": arp_output}
                elif "INCOMPLETE" in arp_output or "FAILED" in arp_output:
                    results[ip_address] = {"resolved": False, "status": "ARP incomplete/failed", "output": arp_output}
                elif "DELAY" in arp_output or "PROBE" in arp_output:
                    results[ip_address] = {"resolved": False, "status": "ARP in progress", "output": arp_output}
                else:
                    results[ip_address] = {"resolved": False, "status": "ARP unknown state", "output": arp_output}
            else:
                results[ip_address] = {"resolved": False, "status": "No ARP entry found", "output": ""}
        
        return jsonify({"results": results, "total": len(ip_addresses)}), 200
            
    except subprocess.TimeoutExpired:
        # Return partial results on timeout
        return jsonify({"results": results, "total": len(ip_addresses), "error": "Timeout"}), 200
    except Exception as e:
        # Return partial results on error
        return jsonify({"results": results, "total": len(ip_addresses), "error": str(e)}), 200


@app.route("/api/device/ping", methods=["POST"])
def ping_device():
    """Ping a given IP address (IPv4 or IPv6) from the server."""
    data = request.get_json()
    ip_address = data.get("ip_address")
    
    if not ip_address:
        return jsonify({"error": "IP address is required"}), 400
    
    try:
        # Detect if it's IPv6 (contains colons) or IPv4
        is_ipv6 = ":" in ip_address
        
        if is_ipv6:
            # Use ping6 for IPv6 addresses
            result = subprocess.run(["ping6", "-c", "3", ip_address], 
                                  capture_output=True, text=True, timeout=15)
        else:
            # Use ping for IPv4 addresses
            result = subprocess.run(["ping", "-c", "3", ip_address], 
                                  capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            return jsonify({
                "success": True, 
                "message": f"Ping successful ({'IPv6' if is_ipv6 else 'IPv4'})", 
                "output": result.stdout,
                "ip_version": "IPv6" if is_ipv6 else "IPv4"
            }), 200
        else:
            return jsonify({
                "success": False, 
                "message": f"Ping failed ({'IPv6' if is_ipv6 else 'IPv4'}): {result.stderr}", 
                "output": result.stderr, 
                "error": result.stderr,
                "ip_version": "IPv6" if is_ipv6 else "IPv4"
            }), 200
            
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False, 
            "message": "Ping timeout", 
            "output": "", 
            "error": "Ping command timed out",
            "ip_version": "IPv6" if is_ipv6 else "IPv4"
        }), 200
    except Exception as e:
        return jsonify({
            "success": False, 
            "message": f"Ping error: {str(e)}", 
            "output": "", 
            "error": str(e),
            "ip_version": "IPv6" if is_ipv6 else "IPv4"
        }), 200


@app.route("/api/device/check", methods=["POST"])
def check_device_interface():
    """Check existing IP configuration on an interface."""
    data = request.get_json()
    interface = data.get("interface")
    vlan = data.get("vlan", "0")
    check_only = data.get("check_only", True)
    
    if not interface:
        return jsonify({"error": "Interface is required"}), 400
    
    try:
        # Determine the actual interface name - check both old and new naming conventions
        if vlan != "0":
            # Try new naming convention first
            new_interface = f"vlan{vlan}"
            old_interface = f"vlan{vlan}@{interface}"
            
            # Check which interface actually exists
            new_exists = subprocess.run(["ip", "link", "show", new_interface], capture_output=True).returncode == 0
            old_exists = subprocess.run(["ip", "link", "show", old_interface], capture_output=True).returncode == 0
            
            if new_exists:
                actual_interface = new_interface
                # Interface naming logic
            elif old_exists:
                actual_interface = old_interface
            else:
                actual_interface = new_interface
        else:
            actual_interface = interface
        
        # Get current IP addresses from the interface
        result = subprocess.run(["ip", "addr", "show", actual_interface], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            return jsonify({
                "success": False, 
                "message": f"Interface {actual_interface} not found or error getting info",
                "error": result.stderr,
                "existing_ips": []
            }), 200
        
        # Parse IP addresses
        lines = result.stdout.split('\n')
        existing_ips = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('inet '):
                # Extract IPv4 address with CIDR
                ip_part = line.split()[1]  # e.g., "192.168.1.1/24"
                existing_ips.append(ip_part)
            
            elif line.startswith('inet6 ') and not line.startswith('inet6 fe80:'):
                # Extract IPv6 address with CIDR (skip link-local)
                ip_part = line.split()[1]  # e.g., "2001:db8::1/64"
                existing_ips.append(ip_part)
        
        return jsonify({
            "success": True, 
            "message": f"Interface {actual_interface} checked successfully",
            "existing_ips": existing_ips,
            "interface": actual_interface
        }), 200
        
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False, 
            "message": f"Check timeout for interface {actual_interface}",
            "error": "Command timed out",
            "existing_ips": []
        }), 200
    except Exception as e:
        return jsonify({
            "success": False, 
            "message": f"Check error for interface {actual_interface}: {str(e)}",
            "error": str(e),
            "existing_ips": []
        }), 200


def send_arp_request_internal(data):
    """Internal function to send ARP request (called from other endpoints)."""
    target_ip = data.get("ip_address") or data.get("target_ip")
    device_ip = data.get("device_ip")
    interface = data.get("interface")
    vlan = data.get("vlan", "0")
    
    if not target_ip:
        return {"error": "IP address is required"}
    
    try:
        # If no device_ip provided, use target_ip
        if not device_ip:
            device_ip = target_ip
        
        # Find device interface if not provided
        if not interface:
            # Try to find in DEVICE_IP_MAPPING
            for ip_addr, (mapped_device_id, iface) in DEVICE_IP_MAPPING.items():
                if ip_addr == device_ip:
                    interface = iface
                    break
        
        if not interface:
            return {"error": "Device interface not found"}
        
        # Determine the actual interface name based on VLAN configuration
        if vlan != "0" and vlan != "":
            # VLAN is configured - always use VLAN interface for ARP requests
            # Try new naming convention first (vlan20)
            new_interface = f"vlan{vlan}"
            old_interface = f"vlan{vlan}@{interface}"
            
            # Check which interface actually exists
            new_exists = subprocess.run(["ip", "link", "show", new_interface], capture_output=True).returncode == 0
            old_exists = subprocess.run(["ip", "link", "show", old_interface], capture_output=True).returncode == 0
            
            if new_exists:
                actual_interface = new_interface
                logging.info(f"[ARP REQUEST] VLAN {vlan} configured - using new VLAN interface: {actual_interface}")
            elif old_exists:
                actual_interface = old_interface
                logging.info(f"[ARP REQUEST] VLAN {vlan} configured - using old VLAN interface: {actual_interface}")
            else:
                actual_interface = new_interface
                logging.info(f"[ARP REQUEST] VLAN {vlan} configured - VLAN interface doesn't exist, using new naming: {actual_interface}")
        else:
            # No VLAN configured (VLAN ID = 0) - use physical interface
            actual_interface = interface
            logging.info(f"[ARP REQUEST] No VLAN configured (VLAN ID = 0) - using physical interface: {actual_interface}")
        
        # Detect if target IP is IPv4 or IPv6
        try:
            ip_obj = ipaddress.ip_address(target_ip)
            is_ipv6 = isinstance(ip_obj, ipaddress.IPv6Address)
        except ValueError:
            return {"error": f"Invalid IP address: {target_ip}"}
        
        if is_ipv6:
            logging.info(f"[NDP REQUEST] Sending NDP request for {target_ip} from {device_ip} on {actual_interface}")
            # Use ping6 for IPv6
            ping_cmd = ["ping6", "-I", actual_interface, "-c", "2", "-W", "3", target_ip]
            ping_cmd_fallback = ["ping6", "-c", "2", "-W", "3", target_ip]
            neigh_cmd = ["ip", "-6", "neigh", "show", target_ip]
            protocol_name = "NDP"
        else:
            logging.info(f"[ARP REQUEST] Sending ARP request for {target_ip} from {device_ip} on {actual_interface}")
            # Use ping for IPv4
            ping_cmd = ["ping", "-I", actual_interface, "-c", "2", "-W", "3", target_ip]
            ping_cmd_fallback = ["ping", "-c", "2", "-W", "3", target_ip]
            neigh_cmd = ["ip", "neigh", "show", target_ip]
            protocol_name = "ARP"
        
        # Send ARP/NDP request using ping/ping6
        ping_result = subprocess.run(
            ping_cmd, 
            capture_output=True, 
            text=True,
            timeout=10
        )
        
        # Also try without interface if interface-specific ping fails
        if ping_result.returncode != 0:
            logging.info(f"[{protocol_name} REQUEST] Interface-specific ping failed, trying without interface")
            ping_result = subprocess.run(
                ping_cmd_fallback, 
                capture_output=True, 
                text=True,
                timeout=10
            )
        
        # Always check the neighbor table regardless of ping result
        # The ping is just to trigger neighbor discovery, not to test connectivity
        arp_result = subprocess.run(neigh_cmd, 
                                      capture_output=True, text=True, timeout=5)
        
        if arp_result.returncode == 0 and arp_result.stdout.strip():
            arp_output = arp_result.stdout.strip()
            if "REACHABLE" in arp_output or "STALE" in arp_output:
                return {
                    "success": True, 
                    "status": f"{protocol_name} request successful", 
                    "output": arp_output
                }
            elif "INCOMPLETE" in arp_output or "FAILED" in arp_output:
                return {
                    "success": False, 
                    "status": f"{protocol_name} request sent but not resolved", 
                    "output": arp_output
                }
            elif "DELAY" in arp_output or "PROBE" in arp_output:
                return {
                    "success": False, 
                    "status": f"{protocol_name} request in progress", 
                    "output": arp_output
                }
            else:
                return {
                    "success": False, 
                    "status": f"{protocol_name} request sent but unknown state", 
                    "output": arp_output
                }
        else:
            # No neighbor entry found - ping was sent but no response
            if ping_result.returncode == 0:
                return {
                    "success": False, 
                    "status": f"{protocol_name} request sent but no {protocol_name} entry found"
                }
            else:
                return {
                    "success": False, 
                    "status": f"{protocol_name} request failed: {ping_result.stderr}"
                }
            
    except Exception as e:
        logging.error(f"[ARP REQUEST ERROR] {e}")
        return {"error": str(e)}

@app.route("/api/device/arp/request", methods=["POST"])
def send_arp_request():
    """Send proactive ARP request to populate ARP table."""
    data = request.get_json()
    result = send_arp_request_internal(data)
    
    if "error" in result:
        return jsonify(result), 400
    elif result.get("success"):
        return jsonify(result), 200
    else:
        return jsonify(result), 200  # Still return 200 for unsuccessful but valid responses

@app.route("/api/device/arp/check", methods=["POST"])
def device_arp():
    """Check ARP resolution for a device."""
    data = request.get_json()
    target_ip = data.get("ip_address") or data.get("target_ip")
    
    if not target_ip:
        return jsonify({"error": "IP address is required"}), 400
    
    # For the client's current implementation, we don't have device_ip
    # So we'll use the target_ip as both target and device IP
    device_ip = target_ip
    
    try:
        # Find the device interface based on device IP
        device_interface = None
        
        # First, try to find in DEVICE_IP_MAPPING
        for ip_addr, (mapped_device_id, iface) in DEVICE_IP_MAPPING.items():
            if ip_addr == device_ip:
                device_interface = iface
                break
        
        # If not found in mapping, check which interface actually has this IP
        if not device_interface:
            result = subprocess.run(["ip", "addr", "show"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                for i, line in enumerate(lines):
                    if device_ip in line and '/24' in line:
                        # Found the IP, now find the interface name from previous lines
                        for j in range(i-1, -1, -1):
                            if ': ' in lines[j] and '@' in lines[j]:
                                # Extract interface name (e.g., "54: vlan10@ens5f1np1")
                                interface_line = lines[j]
                                iface_part = interface_line.split(': ')[1]
                                device_interface = iface_part.split('@')[0]  # Get "vlan10"
                                break
                        break
        
        # Fallback: try ip route get
        if not device_interface:
            result = subprocess.run(["ip", "route", "get", device_ip], capture_output=True, text=True)
            if result.returncode == 0:
                # Parse the output to find the interface
                for line in result.stdout.split('\n'):
                    if 'dev' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'dev' and i + 1 < len(parts):
                                device_interface = parts[i + 1]
                                break
                        break
        
        if not device_interface:
            return jsonify({
                "resolved": False,
                "status": f"No interface found for device IP {device_ip}",
                "error": "Device interface not found"
            }), 400
        
        # Send ARP request and check resolution
        logging.info(f"[ARP] Checking ARP resolution for {target_ip} from device {device_ip} on interface {device_interface}")
        
        # Send ARP request
        try:
            # First, try to ping from the specific interface
            ping_result = subprocess.run(
                ["ping", "-I", device_interface, "-c", "1", "-W", "2", target_ip], 
                capture_output=True, 
                text=True,
                timeout=5
            )
            
            # If interface-specific ping fails, try without interface
            if ping_result.returncode != 0:
                logging.info(f"[ARP] Interface-specific ping failed, trying without interface")
                ping_result = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", target_ip], 
                    capture_output=True, 
                    text=True,
                    timeout=5
                )
            
            # Check ARP table for the target IP
            arp_result = subprocess.run(
                ["arp", "-n", target_ip], 
                capture_output=True, 
                text=True
            )
            
            arp_resolved = False
            arp_message = "ARP not resolved"
            
            # Check if ping was successful
            if ping_result.returncode == 0:
                arp_resolved = True
                arp_message = f"Ping successful to {target_ip} from interface {device_interface}"
                logging.info(f"[ARP] Ping successful: {ping_result.stdout.strip()}")
            elif arp_result.returncode == 0 and target_ip in arp_result.stdout:
                # Parse ARP table entry even if ping failed
                lines = arp_result.stdout.strip().split('\n')
                for line in lines:
                    if target_ip in line and not line.startswith('Address'):
                        parts = line.split()
                        if len(parts) >= 3:
                            mac_addr = parts[2]
                            if mac_addr != "00:00:00:00:00:00" and mac_addr != "<incomplete>":
                                arp_resolved = True
                                arp_message = f"ARP resolved: {target_ip} -> {mac_addr}"
                                break
                        break
            else:
                # No ARP entry found
                arp_message = f"No ARP entry found for {target_ip}"
                logging.warning(f"[ARP] No ARP entry: {arp_result.stdout}")
            
            logging.info(f"[ARP] Result: {arp_message}")
            
            # Update ARP status in database if device exists
            try:
                device_id = None
                # Try to find device_id from DEVICE_IP_MAPPING
                for ip_addr, (mapped_device_id, iface) in DEVICE_IP_MAPPING.items():
                    if ip_addr == device_ip:
                        device_id = mapped_device_id
                        break
                
                if device_id:
                    arp_results = {
                        'overall_status': arp_message,
                        'ipv4_resolved': arp_resolved if ':' not in target_ip else False,
                        'ipv6_resolved': arp_resolved if ':' in target_ip else False,
                        'gateway_resolved': arp_resolved
                    }
                    device_db.update_arp_status(device_id, arp_results)
                    logging.debug(f"[DEVICE DB] Updated ARP status for device {device_id}")
            except Exception as e:
                logging.warning(f"[DEVICE DB] Failed to update ARP status: {e}")
                # Don't fail ARP check if database operation fails
            
            return jsonify({
                "resolved": arp_resolved,
                "status": arp_message,
                "device_ip": device_ip,
                "target_ip": target_ip,
                "interface": device_interface
            }), 200
            
        except subprocess.TimeoutExpired:
            return jsonify({
                "resolved": False,
                "status": f"ARP request timeout for {target_ip}",
                "error": "Timeout"
            }), 200
        except Exception as e:
            logging.error(f"[ARP] Error: {e}")
            return jsonify({
                "resolved": False,
                "status": f"ARP request failed: {str(e)}",
                "error": str(e)
            }), 200
            
    except Exception as e:
        logging.error(f"[ARP] Failed to process ARP request: {e}")
        return jsonify({"resolved": False, "status": f"Error: {str(e)}"}), 500


def generate_host_routes_from_pool(network, count):
    """Generate individual host routes from a network pool."""
    try:
        # Get all host addresses from the network
        hosts = list(network.hosts())
        
        if network.version == 6:
            # For IPv6, use all addresses (no broadcast)
            hosts = list(network)
            # Remove the network address (first address)
            if len(hosts) > 1:
                hosts = hosts[1:]
        
        if len(hosts) < count:
            raise ValueError(f"Not enough host addresses in network {network}")
        
        # Take the first 'count' host addresses and format as /32 or /128 routes
        selected_hosts = hosts[:count]
        
        if network.version == 4:
            # IPv4: use /32 for individual host routes
            return [f"{host}/32" for host in selected_hosts]
        else:
            # IPv6: use /128 for individual host routes
            return [f"{host}/128" for host in selected_hosts]
            
    except Exception as e:
        logging.error(f"[BGP ROUTE ADV] Error generating host routes: {e}")
        return []

def generate_network_routes_from_pool(network, count):
    """Generate network routes from a network pool using increment logic."""
    try:
        import ipaddress
        
        base_addr = network.network_address
        prefix_len = network.prefixlen
        generated_routes = []
        
        for i in range(count):
            if network.version == 4:
                # For IPv4, increment the network portion
                if prefix_len <= 16:
                    # For /16 and larger, increment by 2^8 (one octet)
                    increment = 2 ** 8
                    new_addr = base_addr + (i * increment)
                elif prefix_len <= 24:
                    # For /24, increment by 2^8 (one octet)
                    increment = 2 ** 8
                    new_addr = base_addr + (i * increment)
                else:
                    # For smaller networks, use minimal increment
                    new_addr = base_addr + i
                
                # For IPv4, we need to be more careful about boundary checking
                # For /24 networks, increment by 256 (2^8) to get 1.1.1.0/24 -> 1.1.2.0/24 -> 1.1.3.0/24
                # Don't check against broadcast address as it's too restrictive for network increment
                    
            else:
                # For IPv6, increment the network portion correctly
                if prefix_len <= 64:
                    # For /64 and larger networks, increment by 2^64 (one /64 subnet)
                    subnet_size = 2 ** 64
                    new_addr = base_addr + (i * subnet_size)
                elif prefix_len <= 80:
                    # For /80, increment by 2^48 (one /80 subnet)
                    subnet_size = 2 ** 48
                    new_addr = base_addr + (i * subnet_size)
                elif prefix_len <= 120:
                    # For /120, increment by 2^8 (256 addresses)
                    subnet_size = 2 ** 8
                    new_addr = base_addr + (i * subnet_size)
                else:
                    # For very small networks, use minimal increment
                    new_addr = base_addr + i
                
                # Check if we're still within a reasonable range
                if prefix_len <= 64:
                    # For /64 and larger, limit to reasonable number of routes
                    if i >= count:  # Stop when we've generated the requested number
                        break
                else:
                    # For smaller networks, check against broadcast address
                    if new_addr >= network.broadcast_address:
                        break
            
            route = f"{new_addr}/{prefix_len}"
            generated_routes.append(route)
        
        return generated_routes
            
    except Exception as e:
        logging.error(f"[BGP ROUTE ADV] Error generating network routes: {e}")
        return []

def configure_bgp_route_advertisement(device_id, device_name, bgp_asn, neighbor_ip, route_pools, all_pools):
    """Configure BGP route advertisement using prefix-lists and route-maps in FRR."""
    try:
        from utils.frr_docker import FRRDockerManager
        import ipaddress
        
        logging.info(f"[BGP ROUTE ADV] Starting for device {device_name}, neighbor {neighbor_ip}")
        logging.info(f"[BGP ROUTE ADV] Route pools attached: {route_pools}")
        logging.info(f"[BGP ROUTE ADV] All available pools: {all_pools}")
        
        if not route_pools:
            logging.info(f"[BGP ROUTE ADV] No route pools attached, skipping route advertisement config")
            return True
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Wait a bit for BGP to be fully configured
        import time
        time.sleep(2)
        
        # Generate prefix-list entries from pools
        prefix_list_commands = []
        seq_num = 5
        
        for pool_name in route_pools:
            # Find the pool definition
            pool = next((p for p in all_pools if p["name"] == pool_name), None)
            if not pool:
                logging.warning(f"[BGP ROUTE ADV] Pool '{pool_name}' not found in available pools")
                continue
            
            subnet = pool["subnet"]
            count = pool["count"]
            increment_type = pool.get("increment_type", "host")  # Default to host for backward compatibility
            
            logging.info(f"[BGP ROUTE ADV] Processing pool '{pool_name}': {subnet} with {count} routes, increment_type: {increment_type}")
            
            # Parse the subnet and generate routes based on increment type
            try:
                network = ipaddress.ip_network(subnet, strict=False)
                is_ipv6 = network.version == 6
                
                # Generate routes based on increment type
                if increment_type == "network":
                    # Generate network routes using increment logic
                    generated_routes = generate_network_routes_from_pool(network, count)
                    logging.info(f"[BGP ROUTE ADV] Generated {len(generated_routes)} network routes for pool '{pool_name}'")
                else:
                    # Generate individual host routes (default behavior)
                    generated_routes = generate_host_routes_from_pool(network, count)
                    logging.info(f"[BGP ROUTE ADV] Generated {len(generated_routes)} host routes for pool '{pool_name}'")
                
                for route in generated_routes:
                    if is_ipv6:
                        prefix_list_commands.append(f"ipv6 prefix-list PL-EXPORT seq {seq_num} permit {route}")
                    else:
                        prefix_list_commands.append(f"ip prefix-list PL-EXPORT seq {seq_num} permit {route}")
                    seq_num += 5
                        
            except Exception as e:
                logging.error(f"[BGP ROUTE ADV] Error parsing subnet {subnet}: {e}")
                continue
        
        if not prefix_list_commands:
            logging.warning(f"[BGP ROUTE ADV] No valid prefixes generated from pools")
            return False
        
        logging.info(f"[BGP ROUTE ADV] Generated {len(prefix_list_commands)} prefix-list entries")
        
        # Build FRR configuration commands exactly as shown in user's sample
        vtysh_commands = [
            "configure terminal",
        ]
        logging.info(f"[BGP ROUTE ADV] Starting with {len(vtysh_commands)} base commands")
        
        # Add prefix-list for export (routes to advertise)
        vtysh_commands.extend(prefix_list_commands)
        logging.info(f"[BGP ROUTE ADV] Added {len(prefix_list_commands)} prefix-list commands, total: {len(vtysh_commands)}")
        
        # Add import prefix-list (allow all inbound - adjust as needed)
        vtysh_commands.append("ip prefix-list PL-IMPORT seq 5 permit 0.0.0.0/0 le 32")
        vtysh_commands.append("ipv6 prefix-list PL-IMPORT seq 5 permit ::/0 le 128")
        
        # Determine if neighbor is IPv6
        is_ipv6_neighbor = ':' in neighbor_ip
        
        # Separate IPv4 and IPv6 pools early to know if we need IPv6 next-hop
        ipv4_pools_check = []
        ipv6_pools_check = []
        for pool_name in route_pools:
            pool = next((p for p in all_pools if p["name"] == pool_name), None)
            if pool:
                subnet = pool.get("subnet", "")
                try:
                    network = ipaddress.ip_network(subnet, strict=False)
                    if network.version == 6:
                        ipv6_pools_check.append(pool)
                    else:
                        ipv4_pools_check.append(pool)
                except:
                    # Default to IPv4 if parsing fails
                    ipv4_pools_check.append(pool)
        
        # Get device info to find IPv6 address for next-hop setting
        device_ipv6 = ""
        if ipv6_pools_check and is_ipv6_neighbor:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            device_info = device_db.get_device(device_id)
            if device_info:
                device_ipv6 = device_info.get("ipv6", "").strip()
                if not device_ipv6:
                    # Try getting from bgp_update_source_ipv6 if available
                    bgp_config_from_db = device_info.get("bgp_config", {})
                    if isinstance(bgp_config_from_db, str):
                        import json
                        try:
                            bgp_config_from_db = json.loads(bgp_config_from_db)
                        except:
                            bgp_config_from_db = {}
                    device_ipv6 = bgp_config_from_db.get("bgp_update_source_ipv6", "").strip()
        
        # Configure route-maps
        vtysh_commands.extend([
            "route-map RM-EXPORT permit 10",
            " match ip address prefix-list PL-EXPORT",
            "route-map RM-EXPORT permit 20",
            "route-map RM-IMPORT permit 10",
            " match ip address prefix-list PL-IMPORT",
            "route-map RM-EXPORT-IPV6 permit 10",
            " match ipv6 address prefix-list PL-EXPORT",
        ])
        
        # For IPv6 routes, set the next-hop to the device's IPv6 address
        # This ensures the protocol next-hop is on the interface (fixes "Protocol nexthop is not on the interface")
        # IMPORTANT: This must be INSIDE the route-map RM-EXPORT-IPV6 permit 10 block
        # FRR syntax: "set ipv6 next-hop global <ipv6_address>" (not "nexthop")
        # After setting, we need to exit the route-map permit block before defining permit 20
        if ipv6_pools_check and device_ipv6 and is_ipv6_neighbor:
            vtysh_commands.append(f" set ipv6 next-hop global {device_ipv6}")
            vtysh_commands.append("exit")  # Exit route-map permit 10 block
            logging.info(f"[BGP ROUTE ADV] Setting IPv6 next-hop to {device_ipv6} for route-map RM-EXPORT-IPV6 permit 10")
        elif ipv6_pools_check and is_ipv6_neighbor:
            logging.warning(f"[BGP ROUTE ADV] IPv6 pools configured but device IPv6 address not found - next-hop may be incorrect")
            logging.warning(f"[BGP ROUTE ADV] device_ipv6={device_ipv6}, ipv6_pools_check={bool(ipv6_pools_check)}, is_ipv6_neighbor={is_ipv6_neighbor}")
            # Still need to exit if we didn't set nexthop
            vtysh_commands.append("exit")
        else:
            # No IPv6 pools or not IPv6 neighbor, still need to exit the route-map block
            vtysh_commands.append("exit")
        
        # Continue with remaining route-map configurations
        vtysh_commands.extend([
            "route-map RM-EXPORT-IPV6 permit 20",
            "route-map RM-IMPORT-IPV6 permit 10",
            " match ipv6 address prefix-list PL-IMPORT",
        ])
        
        # Add static routes for each route (so BGP can advertise them)
        for pool_name in route_pools:
            pool = next((p for p in all_pools if p["name"] == pool_name), None)
            if pool:
                subnet = pool["subnet"]
                count = pool["count"]
                increment_type = pool.get("increment_type", "host")  # Default to host for backward compatibility
                
                # Generate routes based on increment type
                try:
                    network = ipaddress.ip_network(subnet, strict=False)
                    
                    if increment_type == "network":
                        # Generate network routes using increment logic
                        generated_routes = generate_network_routes_from_pool(network, count)
                        logging.info(f"[BGP ROUTE ADV] Adding {len(generated_routes)} network static routes for pool {pool_name}")
                    else:
                        # Generate individual host routes (default behavior)
                        generated_routes = generate_host_routes_from_pool(network, count)
                        logging.info(f"[BGP ROUTE ADV] Adding {len(generated_routes)} host static routes for pool {pool_name}")
                    
                    for route in generated_routes:
                        if network.version == 6:
                            vtysh_commands.append(f"ipv6 route {route} null0")
                        else:
                            vtysh_commands.append(f"ip route {route} null0")
                            
                except Exception as e:
                    logging.error(f"[BGP ROUTE ADV] Error generating static routes for pool {pool_name}: {e}")
                    continue
                logging.info(f"[BGP ROUTE ADV] Adding static routes for {subnet} (increment_type: {increment_type})")
        
        # Apply route-maps to BGP neighbor and add network statements
        bgp_commands = [
            f"router bgp {bgp_asn}",
        ]
        
        # Separate IPv4 and IPv6 pools
        ipv4_pools = []
        ipv6_pools = []
        
        for pool_name in route_pools:
            pool = next((p for p in all_pools if p["name"] == pool_name), None)
            if pool:
                subnet = pool["subnet"]
                try:
                    network = ipaddress.ip_network(subnet, strict=False)
                    if network.version == 6:
                        ipv6_pools.append(pool)
                    else:
                        ipv4_pools.append(pool)
                except:
                    # Default to IPv4 if parsing fails
                    ipv4_pools.append(pool)
        
        # Configure IPv4 address family if we have IPv4 pools
        if ipv4_pools:
            bgp_commands.append(" address-family ipv4 unicast")
            # Use redistribute static instead of individual network statements
            bgp_commands.append("  redistribute static route-map RM-EXPORT")
            logging.info(f"[BGP ROUTE ADV] Using redistribute static for IPv4 pools")
            
            # Add IPv4 neighbor route-map configurations
            bgp_commands.extend([
                f"  neighbor {neighbor_ip} route-map RM-EXPORT out",
                f"  neighbor {neighbor_ip} route-map RM-IMPORT in",
            ])
            bgp_commands.append(" exit-address-family")
        
        # Configure IPv6 address family if we have IPv6 pools
        if ipv6_pools:
            bgp_commands.append(" address-family ipv6 unicast")
            # Use redistribute static instead of individual network statements
            bgp_commands.append("  redistribute static route-map RM-EXPORT-IPV6")
            logging.info(f"[BGP ROUTE ADV] Using redistribute static for IPv6 pools")
            
            # Add IPv6 neighbor route-map configurations
            bgp_commands.extend([
                f"  neighbor {neighbor_ip} route-map RM-EXPORT-IPV6 out",
                f"  neighbor {neighbor_ip} route-map RM-IMPORT-IPV6 in",
            ])
            bgp_commands.append(" exit-address-family")
        
        # Add final commands
        bgp_commands.extend([
            "end",
            "write"
        ])
        
        vtysh_commands.extend(bgp_commands)
        
        # Execute configuration using here document approach (same as BGP neighbor config)
        logging.info(f"[BGP ROUTE ADV] About to execute {len(vtysh_commands)} commands using here document approach")
        
        # Log the commands we're about to execute
        logging.info(f"[BGP ROUTE ADV] Commands to execute: {vtysh_commands}")
        
        # Use vtysh with here document to execute all commands at once
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[BGP ROUTE ADV] Executing BGP route advertisement commands using here document")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        logging.info(f"[BGP ROUTE ADV] Command exit code: {result.exit_code}")
        
        if result.exit_code != 0:
            logging.error(f"[BGP ROUTE ADV] Command failed: {result.output.decode()}")
        else:
            logging.info(f"[BGP ROUTE ADV] ✅ All commands executed successfully")
        
        # Clear BGP session to apply new route-maps
        logging.info(f"[BGP ROUTE ADV] Clearing BGP session with {neighbor_ip}")
        if is_ipv6_neighbor:
            # Use IPv6 BGP clear commands for IPv6 neighbors
            container.exec_run(f"vtysh -c 'clear ip bgp ipv6 unicast {neighbor_ip} soft out'")
            container.exec_run(f"vtysh -c 'clear ip bgp ipv6 unicast {neighbor_ip} soft in'")
        else:
            # Use IPv4 BGP clear commands for IPv4 neighbors
            container.exec_run(f"vtysh -c 'clear ip bgp {neighbor_ip} soft out'")
            container.exec_run(f"vtysh -c 'clear ip bgp {neighbor_ip} soft in'")
        
        logging.info(f"[BGP ROUTE ADV] ✅ Successfully configured route advertisement for {device_name} -> {neighbor_ip}")
        return True
        
    except Exception as e:
        logging.error(f"[BGP ROUTE ADV] Error configuring route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False

def configure_ospf_route_advertisement(device_id, device_name, area_id, route_pools, all_pools, af_type="IPv4"):
    """Configure OSPF route advertisement by creating static routes and redistributing them."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[OSPF ROUTE ADV] Configuring route advertisement for device {device_name}, area {area_id}, AF={af_type}")
        logging.info(f"[OSPF ROUTE ADV] Route pools: {route_pools}")
        logging.info(f"[OSPF ROUTE ADV] All pools: {all_pools}")
        
        # Determine address family
        is_ipv6 = af_type == "IPv6"
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Wait a bit for OSPF to be fully configured
        import time
        time.sleep(2)
        
        # Commands to configure route advertisement
        vtysh_commands = [
            "configure terminal",
        ]
        
        if is_ipv6:
            # IPv6 prefix-list for redistribution (base permit all)
            vtysh_commands.extend([
                "ipv6 prefix-list PL-OSPF6-EXPORT seq 5 permit ::/0 le 128",
            ])
            
            # IPv6 route-map for redistribution
            vtysh_commands.extend([
                "route-map RM-OSPF6-EXPORT permit 10",
                " match ipv6 address prefix-list PL-OSPF6-EXPORT",
                "route-map RM-OSPF6-EXPORT permit 20",
            ])
        else:
            # IPv4 prefix-list for redistribution (base permit all)
            vtysh_commands.extend([
                "ip prefix-list PL-OSPF-EXPORT seq 5 permit 0.0.0.0/0 le 32",
            ])
            
            # IPv4 route-map for redistribution
            vtysh_commands.extend([
                "route-map RM-OSPF-EXPORT permit 10",
                " match ip address prefix-list PL-OSPF-EXPORT",
                "route-map RM-OSPF-EXPORT permit 20",
            ])
        
        # Generate and add static routes for each pool
        for pool_name in route_pools:
            # Find pool in all_pools
            pool_data = None
            for pool in all_pools:
                if pool["name"] == pool_name:
                    pool_data = pool
                    break
            
            if not pool_data:
                logging.warning(f"[OSPF ROUTE ADV] Pool '{pool_name}' not found in available pools")
                continue
            
            subnet = pool_data["subnet"]
            count = pool_data["count"]
            increment_type = pool_data.get("increment_type", "host")
            
            logging.info(f"[OSPF ROUTE ADV] Processing pool {pool_name}: {subnet} (count: {count}, type: {increment_type})")
            
            try:
                import ipaddress
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    # Generate network routes using increment logic
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    # Generate individual host routes (default behavior)
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                logging.info(f"[OSPF ROUTE ADV] Generated {len(generated_routes)} routes for pool {pool_name}")
                
                # Add static routes
                for route in generated_routes:
                    if network.version == 6:
                        vtysh_commands.append(f"ipv6 route {route} null0")
                    else:
                        vtysh_commands.append(f"ip route {route} null0")
                
                # Add routes to prefix-list based on address family
                for route in generated_routes:
                    seq_num = len(vtysh_commands) + 100
                    if network.version == 6 and is_ipv6:
                        vtysh_commands.append(f"ipv6 prefix-list PL-OSPF6-EXPORT seq {seq_num} permit {route}")
                    elif network.version == 4 and not is_ipv6:
                        vtysh_commands.append(f"ip prefix-list PL-OSPF-EXPORT seq {seq_num} permit {route}")
                
            except Exception as e:
                logging.error(f"[OSPF ROUTE ADV] Error processing pool {pool_name}: {e}")
                continue
        
        # Configure OSPF/OSPF6 redistribution AFTER all static routes and prefix-list entries are added
        if is_ipv6:
            vtysh_commands.extend([
                "router ospf6",
                f" redistribute static route-map RM-OSPF6-EXPORT",
                "exit"
            ])
        else:
            vtysh_commands.extend([
                "router ospf",
                f" redistribute static route-map RM-OSPF-EXPORT",
                "exit"
            ])
        
        # Use vtysh with here document to execute all commands at once
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[OSPF ROUTE ADV] Executing OSPF route advertisement commands using here document")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        logging.info(f"[OSPF ROUTE ADV] Command exit code: {result.exit_code}")
        
        if result.exit_code != 0:
            logging.error(f"[OSPF ROUTE ADV] Command failed: {result.output.decode()}")
        else:
            logging.info(f"[OSPF ROUTE ADV] ✅ All commands executed successfully")
        
        logging.info(f"[OSPF ROUTE ADV] ✅ Successfully configured route advertisement for {device_name} -> area {area_id}")
        return True
        
    except Exception as e:
        logging.error(f"[OSPF ROUTE ADV] Error configuring route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False

def cleanup_ospf_route_advertisement(device_id, device_name, area_id, af_type=None):
    """Clean up OSPF route advertisement by removing static routes, prefix-lists, and route-maps."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[OSPF ROUTE CLEANUP] Starting cleanup for device {device_name}, area {area_id}, AF={af_type}")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Wait a bit for OSPF to be fully configured
        import time
        time.sleep(2)
        
        # Commands to clean up route pool configurations
        cleanup_commands = []
        
        # Remove all static routes that point to null0 (these are route pool routes)
        cleanup_commands.extend([
            "configure terminal",
        ])
        
        # Get all route pools from database to remove their static routes
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        all_pools_db = device_db.get_all_route_pools()
        
        # Determine if we should filter by address family
        is_ipv6_only = af_type == "IPv6"
        is_ipv4_only = af_type == "IPv4"
        
        # Remove static routes for all pools (both IPv4 and IPv6, or filtered by af_type)
        for pool in all_pools_db:
            pool_name = pool["pool_name"]
            subnet = pool["subnet"]
            count = pool["route_count"]
            increment_type = pool.get("increment_type", "host")
            
            try:
                import ipaddress
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    # Generate network routes using increment logic
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    # Generate individual host routes (default behavior)
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                # Add removal commands for each generated route (only for the specified AF)
                for route in generated_routes:
                    if network.version == 6 and not is_ipv4_only:
                        cleanup_commands.append(f"no ipv6 route {route} null0")
                    elif network.version == 4 and not is_ipv6_only:
                        cleanup_commands.append(f"no ip route {route} null0")
                        
            except Exception as e:
                logging.warning(f"[OSPF ROUTE CLEANUP] Failed to generate routes for pool {pool_name}: {e}")
                continue
        
        # Remove prefix-list and route-map based on AF
        if not is_ipv6_only:
            cleanup_commands.extend([
                "no ip prefix-list PL-OSPF-EXPORT",
                "no route-map RM-OSPF-EXPORT",
            ])
        
        if not is_ipv4_only:
            cleanup_commands.extend([
                "no ipv6 prefix-list PL-OSPF6-EXPORT",
                "no route-map RM-OSPF6-EXPORT",
            ])
        
        # Remove OSPF redistribution based on AF
        if not is_ipv6_only:
            cleanup_commands.extend([
                "router ospf",
                " no redistribute static route-map RM-OSPF-EXPORT",
                "exit"
            ])
        
        if not is_ipv4_only:
            cleanup_commands.extend([
                "router ospf6",
                " no redistribute static route-map RM-OSPF6-EXPORT",
                "exit"
            ])
        
        # Execute cleanup commands
        if cleanup_commands:
            config_commands = "\n".join(cleanup_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            logging.info(f"[OSPF ROUTE CLEANUP] Executing cleanup commands")
            
            result = container.exec_run(["bash", "-c", exec_cmd])
            logging.info(f"[OSPF ROUTE CLEANUP] Command exit code: {result.exit_code}")
            
            if result.exit_code != 0:
                logging.error(f"[OSPF ROUTE CLEANUP] Command failed: {result.output.decode()}")
            else:
                logging.info(f"[OSPF ROUTE CLEANUP] ✅ All cleanup commands executed successfully")
        
        logging.info(f"[OSPF ROUTE CLEANUP] ✅ Successfully cleaned up route advertisement for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[OSPF ROUTE CLEANUP] Error cleaning up route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False


def cleanup_bgp_route_advertisement(device_id, device_name, bgp_asn, neighbor_ip, af_type=None):
    """Clean up BGP route advertisement by removing static routes, prefix-lists, and route-maps."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[BGP ROUTE CLEANUP] Starting cleanup for device {device_name}, neighbor {neighbor_ip}, AF={af_type}")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Wait a bit for BGP to be fully configured
        import time
        time.sleep(2)
        
        # Commands to clean up route pool configurations
        cleanup_commands = []
        
        # Remove all static routes that point to null0 (these are route pool routes)
        cleanup_commands.extend([
            "configure terminal",
        ])
        
        # Determine if we should filter by address family
        # If af_type not specified, infer from neighbor_ip
        if af_type is None:
            # Infer address family from neighbor IP
            try:
                import ipaddress
                neighbor_network = ipaddress.ip_network(f"{neighbor_ip}/32" if ":" not in neighbor_ip else f"{neighbor_ip}/128", strict=False)
                af_type = "IPv6" if neighbor_network.version == 6 else "IPv4"
                logging.info(f"[BGP ROUTE CLEANUP] Inferred AF={af_type} from neighbor IP {neighbor_ip}")
            except:
                af_type = "IPv4"  # Default
                logging.warning(f"[BGP ROUTE CLEANUP] Could not infer AF from neighbor IP {neighbor_ip}, defaulting to IPv4")
        
        is_ipv6_only = af_type == "IPv6"
        is_ipv4_only = af_type == "IPv4"
        
        # Get all route pools from database to remove their static routes
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        all_pools_db = device_db.get_all_route_pools()
        
        # Remove static routes for all pools (both IPv4 and IPv6, or filtered by af_type)
        for pool in all_pools_db:
            pool_name = pool["pool_name"]
            subnet = pool["subnet"]
            count = pool["route_count"]
            increment_type = pool.get("increment_type", "host")
            
            try:
                import ipaddress
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    # Generate network routes using increment logic
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    # Generate individual host routes (default behavior)
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                # Add removal commands for each generated route (only for the specified AF)
                for route in generated_routes:
                    if network.version == 6 and not is_ipv4_only:
                        cleanup_commands.append(f"no ipv6 route {route} null0")
                    elif network.version == 4 and not is_ipv6_only:
                        cleanup_commands.append(f"no ip route {route} null0")
                        
            except Exception as e:
                logging.warning(f"[BGP ROUTE CLEANUP] Failed to generate routes for pool {pool_name}: {e}")
                continue
        
        # Remove prefix-list entries based on AF
        if not is_ipv6_only:
            for seq in range(5, 55, 5):  # seq 5, 10, 15, ..., 50
                cleanup_commands.append(f"no ip prefix-list PL-EXPORT seq {seq}")
        
        if not is_ipv4_only:
            for seq in range(5, 55, 5):  # seq 5, 10, 15, ..., 50
                cleanup_commands.append(f"no ipv6 prefix-list PL-EXPORT seq {seq}")
        
        # Remove route-maps based on AF
        if not is_ipv6_only:
            cleanup_commands.append("no route-map RM-EXPORT permit 10")
        
        if not is_ipv4_only:
            cleanup_commands.append("no route-map RM-EXPORT-IPV6 permit 10")
        
        # Remove BGP redistribution and route-map configurations based on AF
        cleanup_commands.append(f"router bgp {bgp_asn}")
        
        if not is_ipv6_only:
            cleanup_commands.extend([
                " address-family ipv4 unicast",
                "  no redistribute static route-map RM-EXPORT",
                f"  no neighbor {neighbor_ip} route-map RM-EXPORT out",
                f"  no neighbor {neighbor_ip} route-map RM-IMPORT in",
                " exit-address-family"
            ])
        
        if not is_ipv4_only:
            cleanup_commands.extend([
                " address-family ipv6 unicast", 
                "  no redistribute static route-map RM-EXPORT-IPV6",
                f"  no neighbor {neighbor_ip} route-map RM-EXPORT-IPV6 out",
                f"  no neighbor {neighbor_ip} route-map RM-IMPORT-IPV6 in",
                " exit-address-family"
            ])
        
        cleanup_commands.extend([
            "exit",
            "end"
        ])
        
        logging.info(f"[BGP ROUTE CLEANUP] About to execute {len(cleanup_commands)} cleanup commands")
        
        # Execute cleanup commands using here document approach
        import subprocess
        here_doc = "\n".join(cleanup_commands)
        
        cmd = [
            "docker", "exec", container_name, "vtysh", "-c", here_doc
        ]
        
        logging.info(f"[BGP ROUTE CLEANUP] Executing BGP route cleanup commands using here document")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            logging.info(f"[BGP ROUTE CLEANUP] Command exit code: {result.returncode}")
            logging.info(f"[BGP ROUTE CLEANUP] ✅ All cleanup commands executed successfully")
        else:
            logging.warning(f"[BGP ROUTE CLEANUP] Command exit code: {result.returncode}")
            logging.warning(f"[BGP ROUTE CLEANUP] stderr: {result.stderr}")
            logging.warning(f"[BGP ROUTE CLEANUP] stdout: {result.stdout}")
        
        # Clear BGP session to force route withdrawal (only for the specified AF)
        try:
            if not is_ipv6_only:
                clear_cmd = [
                    "docker", "exec", container_name, "vtysh", 
                    "-c", f"clear ip bgp {neighbor_ip}"
                ]
                subprocess.run(clear_cmd, capture_output=True, text=True, timeout=10)
                logging.info(f"[BGP ROUTE CLEANUP] Clearing IPv4 BGP session with {neighbor_ip}")
            if not is_ipv4_only:
                clear_cmd = [
                    "docker", "exec", container_name, "vtysh", 
                    "-c", f"clear ipv6 bgp {neighbor_ip}"
                ]
                subprocess.run(clear_cmd, capture_output=True, text=True, timeout=10)
                logging.info(f"[BGP ROUTE CLEANUP] Clearing IPv6 BGP session with {neighbor_ip}")
        except Exception as e:
            logging.warning(f"[BGP ROUTE CLEANUP] Failed to clear BGP session: {e}")
        
        logging.info(f"[BGP ROUTE CLEANUP] ✅ Successfully cleaned up route advertisement for {device_name} -> {neighbor_ip}")
        return True
        
    except Exception as e:
        logging.error(f"[BGP ROUTE CLEANUP] Error cleaning up route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False


def configure_isis_route_advertisement(device_id, device_name, area_id, route_pools, all_pools, af_type="IPv4"):
    """Configure ISIS route advertisement by creating static routes and redistributing them."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[ISIS ROUTE ADV] Configuring route advertisement for device {device_name}, area {area_id}, AF={af_type}")
        logging.info(f"[ISIS ROUTE ADV] Route pools: {route_pools}")
        logging.info(f"[ISIS ROUTE ADV] All pools: {all_pools}")
        
        # Determine address family
        is_ipv6 = af_type == "IPv6"
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        # Wait a bit for ISIS to be fully configured
        import time
        time.sleep(2)
        
        # Commands to configure route advertisement
        vtysh_commands = [
            "configure terminal",
        ]
        
        if is_ipv6:
            # IPv6 prefix-list for redistribution (base permit all)
            vtysh_commands.extend([
                "ipv6 prefix-list PL-ISIS6-EXPORT seq 5 permit ::/0 le 128",
            ])
            
            # IPv6 route-map for redistribution
            vtysh_commands.extend([
                "route-map RM-ISIS6-EXPORT permit 10",
                " match ipv6 address prefix-list PL-ISIS6-EXPORT",
                "route-map RM-ISIS6-EXPORT permit 20",
            ])
        else:
            # IPv4 prefix-list for redistribution (base permit all)
            vtysh_commands.extend([
                "ip prefix-list PL-ISIS-EXPORT seq 5 permit 0.0.0.0/0 le 32",
            ])
            
            # IPv4 route-map for redistribution
            vtysh_commands.extend([
                "route-map RM-ISIS-EXPORT permit 10",
                " match ip address prefix-list PL-ISIS-EXPORT",
                "route-map RM-ISIS-EXPORT permit 20",
            ])
        
        # Generate and add static routes for each pool
        for pool_name in route_pools:
            # Find pool in all_pools
            pool_data = None
            for pool in all_pools:
                if pool["name"] == pool_name:
                    pool_data = pool
                    break
            
            if not pool_data:
                logging.warning(f"[ISIS ROUTE ADV] Pool '{pool_name}' not found in available pools")
                continue
            
            subnet = pool_data["subnet"]
            count = pool_data["count"]
            increment_type = pool_data.get("increment_type", "host")
            
            logging.info(f"[ISIS ROUTE ADV] Processing pool {pool_name}: {subnet} (count: {count}, type: {increment_type})")
            
            try:
                import ipaddress
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    # Generate network routes using increment logic
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    # Generate individual host routes (default behavior)
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                logging.info(f"[ISIS ROUTE ADV] Generated {len(generated_routes)} routes for pool {pool_name}")
                
                # Add static routes
                for route in generated_routes:
                    if network.version == 6:
                        vtysh_commands.append(f"ipv6 route {route} null0")
                    else:
                        vtysh_commands.append(f"ip route {route} null0")
                
                # Add routes to prefix-list based on address family
                for route in generated_routes:
                    seq_num = len(vtysh_commands) + 100
                    if network.version == 6 and is_ipv6:
                        vtysh_commands.append(f"ipv6 prefix-list PL-ISIS6-EXPORT seq {seq_num} permit {route}")
                    elif network.version == 4 and not is_ipv6:
                        vtysh_commands.append(f"ip prefix-list PL-ISIS-EXPORT seq {seq_num} permit {route}")
                
            except Exception as e:
                logging.error(f"[ISIS ROUTE ADV] Error processing pool {pool_name}: {e}")
                continue
        
        # Configure ISIS redistribution AFTER all static routes and prefix-list entries are added
        vtysh_commands.extend([
            "router isis CORE",
        ])
        
        # Add address-family specific redistribution based on AF type
        if is_ipv6:
            vtysh_commands.append(" redistribute ipv6 static level-2 route-map RM-ISIS6-EXPORT")
        else:
            vtysh_commands.append(" redistribute ipv4 static level-2 route-map RM-ISIS-EXPORT")
        
        vtysh_commands.append("exit")
        
        # Use vtysh with here document to execute all commands at once
        config_commands = "\n".join(vtysh_commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[ISIS ROUTE ADV] Executing ISIS route advertisement commands using here document")
        
        result = container.exec_run(["bash", "-c", exec_cmd])
        logging.info(f"[ISIS ROUTE ADV] Command exit code: {result.exit_code}")
        
        if result.exit_code != 0:
            logging.error(f"[ISIS ROUTE ADV] Command failed: {result.output.decode()}")
        else:
            logging.info(f"[ISIS ROUTE ADV] ✅ All route advertisement commands executed successfully")
        
        logging.info(f"[ISIS ROUTE ADV] ✅ Successfully configured route advertisement for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[ISIS ROUTE ADV] Error configuring route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False


def cleanup_isis_route_advertisement(device_id, device_name, area_id, af_type=None):
    """Clean up ISIS route advertisement by removing static routes, prefix-lists, and route-maps."""
    try:
        from utils.frr_docker import FRRDockerManager
        
        logging.info(f"[ISIS ROUTE CLEANUP] Starting cleanup for device {device_name}, area {area_id}, AF={af_type}")
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)
        
        import time
        time.sleep(2)
        
        cleanup_commands = ["configure terminal"]
        
        is_ipv6_only = af_type == "IPv6"
        is_ipv4_only = af_type == "IPv4"
        
        from utils.device_database import DeviceDatabase
        device_db = DeviceDatabase()
        all_pools_db = device_db.get_all_route_pools()
        
        for pool in all_pools_db:
            pool_name = pool["pool_name"]
            subnet = pool["subnet"]
            count = pool["route_count"]
            increment_type = pool.get("increment_type", "host")
            
            try:
                import ipaddress
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                for route in generated_routes:
                    if network.version == 6 and not is_ipv4_only:
                        cleanup_commands.append(f"no ipv6 route {route} null0")
                    elif network.version == 4 and not is_ipv6_only:
                        cleanup_commands.append(f"no ip route {route} null0")
                        
            except Exception as e:
                logging.warning(f"[ISIS ROUTE CLEANUP] Failed to generate routes for pool {pool_name}: {e}")
                continue
        
        if not is_ipv6_only:
            cleanup_commands.extend([
                "no ip prefix-list PL-ISIS-EXPORT",
                "no route-map RM-ISIS-EXPORT",
            ])
        
        if not is_ipv4_only:
            cleanup_commands.extend([
                "no ipv6 prefix-list PL-ISIS6-EXPORT",
                "no route-map RM-ISIS6-EXPORT",
            ])
        
        cleanup_commands.append("router isis CORE")
        
        # Remove AF-specific redistribution based on af_type
        if not is_ipv6_only:
            cleanup_commands.append(" no redistribute ipv4 static level-2 route-map RM-ISIS-EXPORT")
        if not is_ipv4_only:
            cleanup_commands.append(" no redistribute ipv6 static level-2 route-map RM-ISIS6-EXPORT")
        
        cleanup_commands.append("exit")
        
        if cleanup_commands:
            config_commands = "\n".join(cleanup_commands)
            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
            logging.info(f"[ISIS ROUTE CLEANUP] Executing cleanup commands")
            
            result = container.exec_run(["bash", "-c", exec_cmd])
            logging.info(f"[ISIS ROUTE CLEANUP] Command exit code: {result.exit_code}")
            
            if result.exit_code != 0:
                logging.error(f"[ISIS ROUTE CLEANUP] Command failed: {result.output.decode()}")
            else:
                logging.info(f"[ISIS ROUTE CLEANUP] ✅ All cleanup commands executed successfully")
        
        logging.info(f"[ISIS ROUTE CLEANUP] ✅ Successfully cleaned up route advertisement for {device_name}")
        return True
        
    except Exception as e:
        logging.error(f"[ISIS ROUTE CLEANUP] Error cleaning up route advertisement: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return False


@app.route("/api/device/bgp/configure", methods=["POST"])
def configure_bgp():
    """Configure BGP for a specific device using FRR."""
    data = request.get_json()
    logging.info(f"BGP Configuration Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing BGP configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name")
        interface = data.get("interface")
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        # Handle both 'bgp_config' and 'bgp' field names for backward compatibility
        bgp_config = data.get("bgp_config", data.get("bgp", {}))
        
        if not device_id or not bgp_config:
            return jsonify({"error": "Missing device_id or BGP configuration"}), 400

        # Import FRR Docker utilities
        from utils.frr_docker import configure_bgp_neighbor
        
        # Configure BGP neighbor using FRR Docker
        logging.info(f"BGP Config Debug: {bgp_config}")
        logging.info(f"BGP Config Keys: {list(bgp_config.keys())}")
        
        # Check if IPv4 and/or IPv6 BGP is enabled
        ipv4_enabled = bgp_config.get("ipv4_enabled", True)  # Default to True for backward compatibility
        ipv6_enabled = bgp_config.get("ipv6_enabled", False)
        
        logging.info(f"IPv4 BGP enabled: {ipv4_enabled}, IPv6 BGP enabled: {ipv6_enabled}")
        
        # Ensure FRR container exists before configuring BGP
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Check if container exists, if not create it
        container_name = frr_manager._get_container_name(device_id, device_name)
        try:
            container = frr_manager.client.containers.get(container_name)
            if container.status != "running":
                logging.info(f"[BGP CONFIGURE] Container {container_name} exists but not running, removing and recreating")
                container.remove(force=True)
                container = None
        except Exception:
            logging.info(f"[BGP CONFIGURE] Container {container_name} does not exist, will create it")
            container = None
        
        if container is None:
            # Create device config for container creation
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": data.get("interface", "ens4np0"),
                "vlan": data.get("vlan", "0"),
                "bgp_config": bgp_config
            }
            
            logging.info(f"[BGP CONFIGURE] Creating FRR container for device {device_name}")
            created_container_name = frr_manager.start_frr_container(device_id, device_config)
            if not created_container_name:
                logging.error(f"[BGP CONFIGURE] Failed to create FRR container for device {device_name}")
                return jsonify({"error": "Failed to create FRR container"}), 500
            
            logging.info(f"[BGP CONFIGURE] Successfully created FRR container: {created_container_name}")
        
        # Save device to database if it doesn't exist
        try:
            from datetime import datetime, timezone
            existing_device = device_db.get_device(device_id)
            if not existing_device:
                logging.info(f"[BGP CONFIGURE] Device {device_id} not found in database, adding it")
                device_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": data.get("interface", "ens4np0"),
                    "vlan": data.get("vlan", "0"),
                    "ipv4_address": ipv4,
                    "ipv6_address": ipv6,
                    "ipv4_mask": data.get("ipv4_mask", "24"),
                    "ipv6_mask": data.get("ipv6_mask", "64"),
                    "ipv4_gateway": data.get("ipv4_gateway", ""),
                    "ipv6_gateway": data.get("ipv6_gateway", ""),
                    "protocols": ["BGP"],  # Add BGP protocol to the device
                    "bgp_config": bgp_config,  # Save BGP configuration
                    "status": "Running",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                
                if device_db.add_device(device_data):
                    logging.info(f"[BGP CONFIGURE] Successfully added device {device_name} to database")
                else:
                    logging.warning(f"[BGP CONFIGURE] Failed to add device {device_name} to database")
            else:
                logging.info(f"[BGP CONFIGURE] Device {device_id} already exists in database")
                
                # IMPORTANT: Check for IPv6 removal BEFORE updating database
                # Get existing BGP config before it's overwritten
                existing_bgp_config = existing_device.get("bgp_config", {})
                if isinstance(existing_bgp_config, str):
                    import json
                    try:
                        existing_bgp_config = json.loads(existing_bgp_config)
                    except:
                        existing_bgp_config = {}
                
                # Check if IPv4 was previously enabled but now disabled - need to remove IPv4 neighbors
                existing_ipv4_enabled = existing_bgp_config.get("ipv4_enabled", False)
                existing_ipv4_neighbor = existing_bgp_config.get("bgp_neighbor_ipv4", "")
                
                # Remove IPv4 neighbors from FRR if IPv4 was enabled but now disabled
                if existing_ipv4_enabled and existing_ipv4_neighbor and not ipv4_enabled:
                    logging.info(f"[BGP CONFIGURE] IPv4 was enabled but now disabled - removing IPv4 neighbors {existing_ipv4_neighbor}")
                    try:
                        # Remove IPv4 neighbors using FRR commands (handle comma-separated list)
                        container_name = frr_manager._get_container_name(device_id, device_name)
                        container = frr_manager.client.containers.get(container_name)
                        
                        bgp_asn = bgp_config.get("bgp_asn", existing_bgp_config.get("bgp_asn", 65000))
                        
                        # Split comma-separated neighbor list
                        ipv4_neighbors = [n.strip() for n in existing_ipv4_neighbor.split(",") if n.strip()]
                        
                        # Build commands to remove all IPv4 neighbors
                        remove_commands = [
                            "configure terminal",
                            f"router bgp {bgp_asn}",
                            "address-family ipv4 unicast",
                        ]
                        
                        # Deactivate each IPv4 neighbor
                        for neighbor_ip in ipv4_neighbors:
                            remove_commands.append(f" no neighbor {neighbor_ip} activate")
                        
                        remove_commands.extend([
                            "exit-address-family",
                        ])
                        
                        # Remove neighbor configuration
                        for neighbor_ip in ipv4_neighbors:
                            remove_commands.append(f"no neighbor {neighbor_ip}")
                        
                        remove_commands.extend([
                            "exit",
                            "exit",
                            "write"
                        ])
                        
                        # Execute using here document
                        config_commands = "\n".join(remove_commands)
                        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                        result = container.exec_run(["bash", "-c", exec_cmd])
                        
                        if result.exit_code == 0:
                            logging.info(f"[BGP CONFIGURE] Successfully removed IPv4 neighbors: {ipv4_neighbors}")
                            # Update BGP status in database to reflect IPv4 removal
                            try:
                                device_db.update_device(device_id, {
                                    'bgp_ipv4_established': False,
                                    'bgp_ipv4_state': 'Idle',
                                    'last_bgp_check': datetime.now(timezone.utc).isoformat()
                                })
                                logging.info(f"[BGP CONFIGURE] Updated IPv4 BGP status to Idle in database")
                            except Exception as db_e:
                                logging.warning(f"[BGP CONFIGURE] Failed to update IPv4 BGP status in database: {db_e}")
                        else:
                            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                            logging.warning(f"[BGP CONFIGURE] Failed to remove IPv4 neighbors {ipv4_neighbors}: {output_str}")
                    except Exception as e:
                        logging.warning(f"[BGP CONFIGURE] Failed to remove IPv4 neighbors: {e}")
                
                # Check if IPv6 was previously enabled but now disabled - need to remove IPv6 neighbors
                existing_ipv6_enabled = existing_bgp_config.get("ipv6_enabled", False)
                existing_ipv6_neighbor = existing_bgp_config.get("bgp_neighbor_ipv6", "")
                
                # Remove IPv6 neighbors from FRR if IPv6 was enabled but now disabled
                if existing_ipv6_enabled and existing_ipv6_neighbor and not ipv6_enabled:
                    logging.info(f"[BGP CONFIGURE] IPv6 was enabled but now disabled - removing IPv6 neighbors {existing_ipv6_neighbor}")
                    try:
                        # Remove IPv6 neighbors using FRR commands (handle comma-separated list)
                        container_name = frr_manager._get_container_name(device_id, device_name)
                        container = frr_manager.client.containers.get(container_name)
                        
                        bgp_asn = bgp_config.get("bgp_asn", existing_bgp_config.get("bgp_asn", 65000))
                        
                        # Split comma-separated neighbor list
                        ipv6_neighbors = [n.strip() for n in existing_ipv6_neighbor.split(",") if n.strip()]
                        
                        # Build commands to remove all IPv6 neighbors
                        remove_commands = [
                            "configure terminal",
                            f"router bgp {bgp_asn}",
                            "address-family ipv6 unicast",
                        ]
                        
                        # Deactivate each IPv6 neighbor
                        for neighbor_ip in ipv6_neighbors:
                            remove_commands.append(f" no neighbor {neighbor_ip} activate")
                        
                        remove_commands.extend([
                            "exit-address-family",
                        ])
                        
                        # Remove neighbor configuration
                        for neighbor_ip in ipv6_neighbors:
                            remove_commands.append(f"no neighbor {neighbor_ip}")
                        
                        remove_commands.extend([
                            "exit",
                            "exit",
                            "write"
                        ])
                        
                        # Execute using here document
                        config_commands = "\n".join(remove_commands)
                        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                        result = container.exec_run(["bash", "-c", exec_cmd])
                        
                        if result.exit_code == 0:
                            logging.info(f"[BGP CONFIGURE] Successfully removed IPv6 neighbors: {ipv6_neighbors}")
                            # Update BGP status in database to reflect IPv6 removal
                            try:
                                device_db.update_device(device_id, {
                                    'bgp_ipv6_established': False,
                                    'bgp_ipv6_state': 'Idle',
                                    'last_bgp_check': datetime.now(timezone.utc).isoformat()
                                })
                                logging.info(f"[BGP CONFIGURE] Updated IPv6 BGP status to Idle in database")
                            except Exception as db_e:
                                logging.warning(f"[BGP CONFIGURE] Failed to update IPv6 BGP status in database: {db_e}")
                        else:
                            output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                            logging.warning(f"[BGP CONFIGURE] Failed to remove IPv6 neighbors {ipv6_neighbors}: {output_str}")
                    except Exception as e:
                        logging.warning(f"[BGP CONFIGURE] Failed to remove IPv6 neighbors: {e}")
                
                # DIFF LOGIC: Compare old vs new neighbors and handle changes
                # This handles cases where individual neighbors are edited (IP changed, removed, etc.)
                try:
                    container_name = frr_manager._get_container_name(device_id, device_name)
                    container = frr_manager.client.containers.get(container_name)
                    bgp_asn = bgp_config.get("bgp_asn", existing_bgp_config.get("bgp_asn", 65000))
                    
                    # Parse old and new neighbor lists
                    old_ipv4_neighbors_str = existing_bgp_config.get("bgp_neighbor_ipv4", "")
                    new_ipv4_neighbors_str = bgp_config.get("bgp_neighbor_ipv4", "")
                    old_ipv6_neighbors_str = existing_bgp_config.get("bgp_neighbor_ipv6", "")
                    new_ipv6_neighbors_str = bgp_config.get("bgp_neighbor_ipv6", "")
                    
                    old_ipv4_list = [n.strip() for n in old_ipv4_neighbors_str.split(",") if n.strip()] if old_ipv4_neighbors_str else []
                    new_ipv4_list = [n.strip() for n in new_ipv4_neighbors_str.split(",") if n.strip()] if new_ipv4_neighbors_str else []
                    old_ipv6_list = [n.strip() for n in old_ipv6_neighbors_str.split(",") if n.strip()] if old_ipv6_neighbors_str else []
                    new_ipv6_list = [n.strip() for n in new_ipv6_neighbors_str.split(",") if n.strip()] if new_ipv6_neighbors_str else []
                    
                    # Only process diff if IPv4 is still enabled (not already handled above)
                    if ipv4_enabled and (old_ipv4_list or new_ipv4_list):
                        # Find neighbors to remove (in old but not in new)
                        ipv4_to_remove = [n for n in old_ipv4_list if n not in new_ipv4_list]
                        
                        # Find neighbors to add (in new but not in old)
                        ipv4_to_add = [n for n in new_ipv4_list if n not in old_ipv4_list]
                        
                        # Find neighbors that might need updates (in both, but config might have changed)
                        ipv4_to_check = [n for n in old_ipv4_list if n in new_ipv4_list]
                        
                        if ipv4_to_remove:
                            logging.info(f"[BGP DIFF] Removing IPv4 neighbors that are no longer in config: {ipv4_to_remove}")
                            # Build commands to remove these neighbors
                            remove_commands = [
                                "configure terminal",
                                f"router bgp {bgp_asn}",
                                "address-family ipv4 unicast",
                            ]
                            
                            # Deactivate each neighbor
                            for neighbor_ip in ipv4_to_remove:
                                remove_commands.append(f" no neighbor {neighbor_ip} activate")
                            
                            remove_commands.extend([
                                "exit-address-family",
                            ])
                            
                            # Remove neighbor configuration
                            for neighbor_ip in ipv4_to_remove:
                                remove_commands.append(f"no neighbor {neighbor_ip}")
                            
                            remove_commands.extend([
                                "exit",
                                "exit",
                                "write"
                            ])
                            
                            # Execute removal
                            config_commands = "\n".join(remove_commands)
                            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                            result = container.exec_run(["bash", "-c", exec_cmd])
                            
                            if result.exit_code == 0:
                                logging.info(f"[BGP DIFF] Successfully removed IPv4 neighbors: {ipv4_to_remove}")
                                # Clean up route pools for removed neighbors
                                route_pools = bgp_config.get("route_pools", {})
                                for removed_neighbor in ipv4_to_remove:
                                    if removed_neighbor in route_pools:
                                        del route_pools[removed_neighbor]
                                        device_db.remove_device_route_pools(device_id, removed_neighbor)
                                        logging.info(f"[BGP DIFF] Removed route pools for deleted neighbor {removed_neighbor}")
                            else:
                                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                                logging.warning(f"[BGP DIFF] Failed to remove IPv4 neighbors {ipv4_to_remove}: {output_str}")
                        
                        # Log neighbors that need checking (they'll be updated by configure_bgp_neighbor below)
                        if ipv4_to_check:
                            logging.info(f"[BGP DIFF] IPv4 neighbors to check/update: {ipv4_to_check}")
                        
                        if ipv4_to_add:
                            logging.info(f"[BGP DIFF] New IPv4 neighbors to add: {ipv4_to_add}")
                    
                    # Only process diff if IPv6 is still enabled (not already handled above)
                    if ipv6_enabled and (old_ipv6_list or new_ipv6_list):
                        # Find neighbors to remove (in old but not in new)
                        ipv6_to_remove = [n for n in old_ipv6_list if n not in new_ipv6_list]
                        
                        # Find neighbors to add (in new but not in old)
                        ipv6_to_add = [n for n in new_ipv6_list if n not in old_ipv6_list]
                        
                        # Find neighbors that might need updates (in both, but config might have changed)
                        ipv6_to_check = [n for n in old_ipv6_list if n in new_ipv6_list]
                        
                        if ipv6_to_remove:
                            logging.info(f"[BGP DIFF] Removing IPv6 neighbors that are no longer in config: {ipv6_to_remove}")
                            # Build commands to remove these neighbors
                            remove_commands = [
                                "configure terminal",
                                f"router bgp {bgp_asn}",
                                "address-family ipv6 unicast",
                            ]
                            
                            # Deactivate each neighbor
                            for neighbor_ip in ipv6_to_remove:
                                remove_commands.append(f" no neighbor {neighbor_ip} activate")
                            
                            remove_commands.extend([
                                "exit-address-family",
                            ])
                            
                            # Remove neighbor configuration
                            for neighbor_ip in ipv6_to_remove:
                                remove_commands.append(f"no neighbor {neighbor_ip}")
                            
                            remove_commands.extend([
                                "exit",
                                "exit",
                                "write"
                            ])
                            
                            # Execute removal
                            config_commands = "\n".join(remove_commands)
                            exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                            result = container.exec_run(["bash", "-c", exec_cmd])
                            
                            if result.exit_code == 0:
                                logging.info(f"[BGP DIFF] Successfully removed IPv6 neighbors: {ipv6_to_remove}")
                                # Clean up route pools for removed neighbors
                                route_pools = bgp_config.get("route_pools", {})
                                for removed_neighbor in ipv6_to_remove:
                                    if removed_neighbor in route_pools:
                                        del route_pools[removed_neighbor]
                                        device_db.remove_device_route_pools(device_id, removed_neighbor)
                                        logging.info(f"[BGP DIFF] Removed route pools for deleted neighbor {removed_neighbor}")
                            else:
                                output_str = result.output.decode('utf-8') if isinstance(result.output, bytes) else str(result.output)
                                logging.warning(f"[BGP DIFF] Failed to remove IPv6 neighbors {ipv6_to_remove}: {output_str}")
                        
                        # Log neighbors that need checking (they'll be updated by configure_bgp_neighbor below)
                        if ipv6_to_check:
                            logging.info(f"[BGP DIFF] IPv6 neighbors to check/update: {ipv6_to_check}")
                        
                        if ipv6_to_add:
                            logging.info(f"[BGP DIFF] New IPv6 neighbors to add: {ipv6_to_add}")
                
                except Exception as e:
                    logging.warning(f"[BGP DIFF] Error processing neighbor diff: {e}")
                    import traceback
                    logging.warning(traceback.format_exc())
                
                # Update device with BGP protocol and configuration
                update_data = {}
                
                # Always update IP addresses if provided (they may have changed)
                if ipv4:
                    existing_ipv4 = existing_device.get("ipv4_address", "")
                    if existing_ipv4 != ipv4:
                        logging.info(f"[BGP CONFIGURE] IPv4 address changed from '{existing_ipv4}' to '{ipv4}' for device {device_name}")
                    update_data.update({
                        "ipv4_address": ipv4,
                        "ipv4_mask": data.get("ipv4_mask", "24"),
                        "ipv4_gateway": data.get("ipv4_gateway", "")
                    })
                if ipv6:
                    existing_ipv6 = existing_device.get("ipv6_address", "")
                    if existing_ipv6 != ipv6:
                        logging.info(f"[BGP CONFIGURE] IPv6 address changed from '{existing_ipv6}' to '{ipv6}' for device {device_name}")
                    update_data.update({
                        "ipv6_address": ipv6,
                        "ipv6_mask": data.get("ipv6_mask", "64"),
                        "ipv6_gateway": data.get("ipv6_gateway", "")
                    })
                
                # Always update protocols and BGP config for existing devices
                existing_protocols = existing_device.get("protocols", [])
                if "BGP" not in existing_protocols:
                    existing_protocols.append("BGP")
                    update_data["protocols"] = existing_protocols
                    logging.info(f"[BGP CONFIGURE] Adding BGP protocol to device {device_name}")
                
                # Update BGP configuration
                update_data["bgp_config"] = bgp_config
                logging.info(f"[BGP CONFIGURE] Updating BGP configuration for device {device_name}")
                
                if update_data:
                    device_db.update_device(device_id, update_data)
        except Exception as e:
            logging.warning(f"[BGP CONFIGURE] Error checking/adding device to database: {e}")
        
        success = True
        
        # Configure IPv4 BGP if enabled - handle each neighbor individually
        if ipv4_enabled and bgp_config.get("bgp_neighbor_ipv4"):
            logging.info(f"Configuring IPv4 BGP for {device_name}")
            # Parse comma-separated neighbor list
            ipv4_neighbors_str = bgp_config.get("bgp_neighbor_ipv4", "")
            ipv4_neighbors_list = [n.strip() for n in ipv4_neighbors_str.split(",") if n.strip()] if ipv4_neighbors_str else []
            
            # Configure each IPv4 neighbor individually
            for neighbor_ip in ipv4_neighbors_list:
                # Use IPv4-specific remote ASN if available, otherwise fall back to general remote ASN
                neighbor_as_ipv4 = bgp_config.get("bgp_remote_asn_ipv4") or bgp_config.get("bgp_neighbor_asn") or bgp_config.get("bgp_remote_asn", "")
                neighbor_config_ipv4 = {
                    "neighbor_ip": neighbor_ip,
                    "neighbor_as": neighbor_as_ipv4,
                    "local_as": bgp_config.get("bgp_asn", 65001),
                    "update_source": bgp_config.get("bgp_update_source_ipv4", ipv4),
                    "keepalive": bgp_config.get("bgp_keepalive", "30"),
                    "hold_time": bgp_config.get("bgp_hold_time", "90"),
                    "protocol": "ipv4"
                }
                logging.info(f"IPv4 Neighbor Config for {neighbor_ip}: {neighbor_config_ipv4}")
                
                neighbor_success = configure_bgp_neighbor(device_id, neighbor_config_ipv4, device_name)
                if not neighbor_success:
                    success = False
                    logging.error(f"Failed to configure IPv4 BGP neighbor {neighbor_ip} for {device_name}")
                else:
                    logging.info(f"Successfully configured IPv4 BGP neighbor {neighbor_ip} for {device_name}")
        
        # Configure IPv6 BGP if enabled - handle each neighbor individually
        if ipv6_enabled and bgp_config.get("bgp_neighbor_ipv6"):
            logging.info(f"Configuring IPv6 BGP for {device_name}")
            # Parse comma-separated neighbor list
            ipv6_neighbors_str = bgp_config.get("bgp_neighbor_ipv6", "")
            ipv6_neighbors_list = [n.strip() for n in ipv6_neighbors_str.split(",") if n.strip()] if ipv6_neighbors_str else []
            
            # Configure each IPv6 neighbor individually
            for neighbor_ip in ipv6_neighbors_list:
                # Use IPv6-specific remote ASN if available, otherwise fall back to general remote ASN
                neighbor_as_ipv6 = bgp_config.get("bgp_remote_asn_ipv6") or bgp_config.get("bgp_neighbor_asn") or bgp_config.get("bgp_remote_asn", "")
                neighbor_config_ipv6 = {
                    "neighbor_ip": neighbor_ip,
                    "neighbor_as": neighbor_as_ipv6,
                    "local_as": bgp_config.get("bgp_asn", 65001),
                    "update_source": bgp_config.get("bgp_update_source_ipv6", ipv6),
                    "keepalive": bgp_config.get("bgp_keepalive", "30"),
                    "hold_time": bgp_config.get("bgp_hold_time", "90"),
                    "protocol": "ipv6"
                }
                logging.info(f"IPv6 Neighbor Config for {neighbor_ip}: {neighbor_config_ipv6}")
                
                neighbor_success = configure_bgp_neighbor(device_id, neighbor_config_ipv6, device_name)
                if not neighbor_success:
                    success = False
                    logging.error(f"Failed to configure IPv6 BGP neighbor {neighbor_ip} for {device_name}")
                else:
                    logging.info(f"Successfully configured IPv6 BGP neighbor {neighbor_ip} for {device_name}")
        
        if not success:
            return jsonify({"error": "Failed to configure BGP neighbor(s)"}), 500
        
        logging.info(f"[BGP CONFIGURE] Successfully configured BGP for {device_name} ({device_id})")
        
        # Add static default route via gateway if configured (BACKGROUND - non-blocking)
        logging.info(f"[BGP ROUTE DEBUG] Checking for gateway in data: {data.keys()}")
        gateway = data.get("gateway", "").strip()
        logging.info(f"[BGP ROUTE DEBUG] Gateway value: '{gateway}'")
        route_added = False
        if gateway and device_id:
            # Add route in background thread (returns immediately)
            # Use shorter wait for BGP case since container already exists
            def _add_bgp_route():
                import time
                time.sleep(3)  # Short wait for existing container
                try:
                    from utils.frr_docker import FRRDockerManager
                    frr_manager = FRRDockerManager()
                    container_name = frr_manager._get_container_name(device_id, device_name)
                    container = frr_manager.client.containers.get(container_name)
                    
                    route_cmd = f"vtysh -c 'configure terminal' -c 'ip route 0.0.0.0/0 {gateway}' -c 'end' -c 'write memory'"
                    route_result = container.exec_run(route_cmd)
                    
                    if route_result.exit_code == 0:
                        logging.info(f"[BGP ROUTE BG] ✅ Added static route 0.0.0.0/0 via {gateway} for {device_name}")
                    else:
                        output_str = route_result.output.decode('utf-8') if isinstance(route_result.output, bytes) else str(route_result.output)
                        logging.warning(f"[BGP ROUTE BG] Failed for {device_name}: {output_str}")
                except Exception as e:
                    logging.error(f"[BGP ROUTE BG] Error for {device_name}: {e}")
            
            import threading
            threading.Thread(target=_add_bgp_route, daemon=True).start()
            logging.info(f"[BGP ROUTE] Scheduled background route addition for {device_name}")
            route_added = True  # Mark as scheduled
        else:
            logging.debug(f"[BGP ROUTE] No gateway configured for device {device_name}, skipping default route")
        
        # Configure BGP route advertisement if route pools are attached
        route_pools_per_neighbor = bgp_config.get("route_pools", {})
        # Support both IPv4 and IPv6 neighbors
        neighbor_ip = bgp_config.get("bgp_neighbor_ipv4", "") or bgp_config.get("bgp_neighbor_ipv6", "")
        bgp_asn = bgp_config.get("bgp_asn", "65000")
        
        # Save device-pool relationships to database
        if neighbor_ip and route_pools_per_neighbor:
            for neighbor, attached_pools in route_pools_per_neighbor.items():
                if attached_pools:  # Only save if there are pools attached
                    device_db.attach_route_pools_to_device(device_id, neighbor, attached_pools)
                    logging.info(f"[BGP CONFIGURE] Saved {len(attached_pools)} route pool attachments for device {device_id} and neighbor {neighbor}")
                else:
                    # No pools attached to this neighbor - remove from database
                    device_db.remove_device_route_pools(device_id, neighbor)
                    logging.info(f"[BGP CONFIGURE] Removed route pool attachments for device {device_id} and neighbor {neighbor}")
        else:
            # No route pools configured - remove all attachments for this device
            if neighbor_ip:
                device_db.remove_device_route_pools(device_id, neighbor_ip)
                logging.info(f"[BGP CONFIGURE] Removed all route pool attachments for device {device_id} and neighbor {neighbor_ip}")
        
        # Get route pools from database instead of request data
        all_pools_db = device_db.get_all_route_pools()
        all_pools = []
        for pool in all_pools_db:
            all_pools.append({
                "name": pool["pool_name"],
                "subnet": pool["subnet"],
                "count": pool["route_count"],
                "first_host": pool["first_host_ip"],
                "last_host": pool["last_host_ip"],
                "increment_type": pool.get("increment_type", "host")
            })
        
        logging.info(f"[BGP ROUTE DEBUG] Checking route advertisement conditions:")
        logging.info(f"[BGP ROUTE DEBUG] - route_pools_per_neighbor: {route_pools_per_neighbor}")
        logging.info(f"[BGP ROUTE DEBUG] - all_pools from database: {len(all_pools)} pools")
        
        # Process ALL neighbors that have route pools attached
        for current_neighbor_ip, attached_pools in route_pools_per_neighbor.items():
            logging.info(f"[BGP ROUTE DEBUG] Processing neighbor: {current_neighbor_ip}")
            logging.info(f"[BGP ROUTE DEBUG] - attached_pools: {attached_pools}")
            
            if attached_pools and all_pools:
                logging.info(f"[BGP CONFIGURE] Configuring route advertisement for neighbor {current_neighbor_ip}")
                # Run in background to avoid blocking
                def _configure_routes(neighbor_ip=current_neighbor_ip, pools=attached_pools):
                    configure_bgp_route_advertisement(
                        device_id, device_name, bgp_asn, neighbor_ip, 
                        pools, all_pools
                    )
                import threading
                threading.Thread(target=_configure_routes, daemon=True).start()
            else:
                logging.info(f"[BGP ROUTE DEBUG] No attached pools - cleaning up existing route advertisement for neighbor {current_neighbor_ip}")
                # Run cleanup in background to avoid blocking
                def _cleanup_routes(neighbor_ip=current_neighbor_ip):
                    cleanup_bgp_route_advertisement(
                        device_id, device_name, bgp_asn, neighbor_ip
                    )
                import threading
                threading.Thread(target=_cleanup_routes, daemon=True).start()
        
        # Also handle cleanup for neighbors that are configured but have no route pools
        configured_neighbors = []
        if bgp_config.get("bgp_neighbor_ipv4", "").strip():
            configured_neighbors.append(bgp_config.get("bgp_neighbor_ipv4", "").strip())
        if bgp_config.get("bgp_neighbor_ipv6", "").strip():
            configured_neighbors.append(bgp_config.get("bgp_neighbor_ipv6", "").strip())
        
        for configured_neighbor in configured_neighbors:
            if configured_neighbor not in route_pools_per_neighbor:
                logging.info(f"[BGP ROUTE DEBUG] No route pools attached to configured neighbor {configured_neighbor} - cleaning up existing route advertisement")
                # Run cleanup in background to avoid blocking
                def _cleanup_routes(neighbor_ip=configured_neighbor):
                    cleanup_bgp_route_advertisement(
                        device_id, device_name, bgp_asn, neighbor_ip
                    )
                import threading
                threading.Thread(target=_cleanup_routes, daemon=True).start()
        
        return jsonify({
            "status": "configured",
            "device_id": device_id,
            "device_name": device_name,
            "neighbor_ip": bgp_config.get("bgp_neighbor_ipv4", ""),
            "neighbor_as": bgp_config.get("bgp_remote_asn", ""),
            "route_added": route_added
        }), 200
        
    except Exception as e:
        logging.error(f"[BGP CONFIGURE ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/bgp/stop", methods=["POST"])
def stop_bgp():
    """Stop BGP protocol for a specific device by shutting down BGP neighbors."""
    data = request.get_json()
    logging.info(f"BGP Stop Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing BGP stop configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name")
        # Handle both 'bgp_config' and 'bgp' field names for backward compatibility
        bgp_config = data.get("bgp_config", data.get("bgp", {}))
        
        if not device_id or not bgp_config:
            return jsonify({"error": "Missing device_id or BGP configuration"}), 400

        # Check current BGP status from database
        try:
            device_data = device_db.get_device(device_id)
            if not device_data:
                return jsonify({"error": "Device not found in database"}), 404
            
            # Get current BGP status from database
            bgp_ipv4_established = device_data.get('bgp_ipv4_established', False)
            bgp_ipv6_established = device_data.get('bgp_ipv6_established', False)
            bgp_ipv4_state = device_data.get('bgp_ipv4_state', 'Unknown')
            bgp_ipv6_state = device_data.get('bgp_ipv6_state', 'Unknown')
            
            logging.info(f"[BGP STOP] Current database status - IPv4: {bgp_ipv4_state} (established: {bgp_ipv4_established}), IPv6: {bgp_ipv6_state} (established: {bgp_ipv6_established})")
            
        except Exception as e:
            logging.warning(f"[BGP STOP] Could not get device status from database: {e}")
            # Continue anyway, but log the issue

        # Import FRR Docker utilities
        from utils.frr_docker import FRRDockerManager
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        
        try:
            container = frr_manager.client.containers.get(container_name)
        except Exception as e:
            logging.error(f"[BGP STOP] Container not found: {container_name} - {e}")
            return jsonify({"error": f"Container not found: {container_name}"}), 404

        # Get BGP configuration details
        bgp_asn = bgp_config.get("bgp_asn", 65001)
        neighbor_ipv4 = bgp_config.get("bgp_neighbor_ipv4", "")
        neighbor_ipv6 = bgp_config.get("bgp_neighbor_ipv6", "")
        
        if not neighbor_ipv4 and not neighbor_ipv6:
            return jsonify({"error": "No BGP neighbor IP configured"}), 400

        # Check if specific neighbors were selected in the UI
        selected_neighbors = request.json.get("selected_neighbors", [])
        logging.info(f"[BGP STOP] Selected neighbors from UI: {selected_neighbors}")
        logging.info(f"[BGP STOP] selected_neighbors type: {type(selected_neighbors)}, length: {len(selected_neighbors) if selected_neighbors else 'None'}")
        
        # Determine which neighbors need to be stopped based on database status and UI selection
        neighbors_to_stop = []
        
        # If specific neighbors were selected, only stop those
        if selected_neighbors:
            logging.info(f"[BGP STOP] Processing specific neighbor selection")
            for neighbor_ip in selected_neighbors:
                is_ipv6 = ':' in neighbor_ip
                if is_ipv6 and neighbor_ipv6 and neighbor_ip == neighbor_ipv6 and bgp_ipv6_established:
                    neighbors_to_stop.append(("IPv6", neighbor_ipv6))
                    logging.info(f"[BGP STOP] Selected IPv6 neighbor {neighbor_ipv6} needs to be stopped (current state: {bgp_ipv6_state})")
                elif not is_ipv6 and neighbor_ipv4 and neighbor_ip == neighbor_ipv4 and bgp_ipv4_established:
                    neighbors_to_stop.append(("IPv4", neighbor_ipv4))
                    logging.info(f"[BGP STOP] Selected IPv4 neighbor {neighbor_ipv4} needs to be stopped (current state: {bgp_ipv4_state})")
                else:
                    logging.info(f"[BGP STOP] Selected neighbor {neighbor_ip} is not established or doesn't match configured neighbors")
        else:
            # No specific selection - stop all established neighbors (original behavior)
            logging.info(f"[BGP STOP] No specific neighbors selected, using original behavior (stop all)")
            if neighbor_ipv4 and bgp_ipv4_established:
                neighbors_to_stop.append(("IPv4", neighbor_ipv4))
                logging.info(f"[BGP STOP] IPv4 neighbor {neighbor_ipv4} needs to be stopped (current state: {bgp_ipv4_state})")
            elif neighbor_ipv4 and not bgp_ipv4_established:
                logging.info(f"[BGP STOP] IPv4 neighbor {neighbor_ipv4} is already stopped, skipping")
                
            if neighbor_ipv6 and bgp_ipv6_established:
                neighbors_to_stop.append(("IPv6", neighbor_ipv6))
                logging.info(f"[BGP STOP] IPv6 neighbor {neighbor_ipv6} needs to be stopped (current state: {bgp_ipv6_state})")
            elif neighbor_ipv6 and not bgp_ipv6_established:
                logging.info(f"[BGP STOP] IPv6 neighbor {neighbor_ipv6} is already stopped, skipping")
        
        if not neighbors_to_stop:
            return jsonify({
                "status": "already_stopped",
                "device_id": device_id,
                "device_name": device_name,
                "message": "All BGP neighbors are already stopped"
            }), 200

        # Execute shutdown commands using here document approach (fixed syntax)
        logging.info(f"[BGP STOP] Executing BGP shutdown commands")
        commands = [
            "configure terminal",
            f"router bgp {bgp_asn}",
        ]
        
        # Only add shutdown commands for neighbors that need to be stopped
        for neighbor_type, neighbor_ip in neighbors_to_stop:
            commands.append(f"neighbor {neighbor_ip} shutdown")
            logging.info(f"[BGP STOP] Adding shutdown command for {neighbor_type} neighbor {neighbor_ip}")
            
        commands.extend([
            "end",
            "write"
        ])
        
        # Use here document approach with proper syntax
        config_commands = "\n".join(commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[BGP STOP] Executing: {exec_cmd}")
        result = container.exec_run(["bash", "-c", exec_cmd])
        
        if result.exit_code != 0:
            error_msg = result.output.decode() if result.output else "Unknown error"
            logging.error(f"[BGP STOP] Failed to execute shutdown commands: {error_msg}")
            return jsonify({"error": f"Failed to execute shutdown commands: {error_msg}"}), 500
        
        # All commands succeeded
        stopped_neighbor_ips = [neighbor_ip for _, neighbor_ip in neighbors_to_stop]
        logging.info(f"[BGP STOP] Successfully shut down BGP neighbors {stopped_neighbor_ips} for {device_name}")
        
        # Clear BGP sessions to ensure shutdown takes effect
        for neighbor_type, neighbor_ip in neighbors_to_stop:
            if ":" in neighbor_ip:  # IPv6
                clear_result = container.exec_run(["vtysh", "-c", f"clear ip bgp {neighbor_ip}"])
            else:  # IPv4
                clear_result = container.exec_run(["vtysh", "-c", f"clear ip bgp {neighbor_ip}"])
                
            if clear_result.exit_code == 0:
                logging.info(f"[BGP STOP] Cleared BGP session with {neighbor_type} neighbor {neighbor_ip}")
            else:
                logging.warning(f"[BGP STOP] Failed to clear BGP session: {clear_result.output.decode()}")
        
        # Update BGP status in database after successful stop
        try:
            update_data = {}
            
            # Update IPv4 status if IPv4 neighbor was stopped
            if neighbor_ipv4 and any(neighbor_type == "IPv4" for neighbor_type, _ in neighbors_to_stop):
                update_data.update({
                    'bgp_ipv4_established': False,
                    'bgp_ipv4_state': 'Idle',
                    'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                    'bgp_manual_override': True,  # Flag to prevent monitor from overriding
                    'bgp_manual_override_time': datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[BGP STOP] Updated IPv4 BGP status to Idle in database (manual override)")
            
            # Update IPv6 status if IPv6 neighbor was stopped
            if neighbor_ipv6 and any(neighbor_type == "IPv6" for neighbor_type, _ in neighbors_to_stop):
                update_data.update({
                    'bgp_ipv6_established': False,
                    'bgp_ipv6_state': 'Idle',
                    'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                    'bgp_manual_override': True,  # Flag to prevent monitor from overriding
                    'bgp_manual_override_time': datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[BGP STOP] Updated IPv6 BGP status to Idle in database (manual override)")
            
            if update_data:
                device_db.update_device(device_id, update_data)
                logging.info(f"[BGP STOP] Successfully updated BGP status in database for device {device_name}")
        except Exception as e:
            logging.warning(f"[BGP STOP] Failed to update BGP status in database: {e}")
        
        return jsonify({
            "status": "stopped",
            "device_id": device_id,
            "device_name": device_name,
            "neighbor_ips": stopped_neighbor_ips,
            "neighbors_stopped": [{"type": neighbor_type, "ip": neighbor_ip} for neighbor_type, neighbor_ip in neighbors_to_stop],
            "message": f"BGP neighbors {stopped_neighbor_ips} shut down successfully"
        }), 200
            
    except Exception as e:
        logging.error(f"[BGP STOP ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/bgp/start", methods=["POST"])
def start_bgp():
    """Start BGP protocol for a specific device by removing shutdown commands."""
    data = request.get_json()
    logging.info(f"BGP Start Data: {data}")
    
    if not data:
        return jsonify({"error": "Missing BGP start configuration"}), 400

    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name")
        # Handle both 'bgp_config' and 'bgp' field names for backward compatibility
        bgp_config = data.get("bgp_config", data.get("bgp", {}))
        
        if not device_id or not bgp_config:
            return jsonify({"error": "Missing device_id or BGP configuration"}), 400

        # Check current BGP status from database
        try:
            device_data = device_db.get_device(device_id)
            if not device_data:
                return jsonify({"error": "Device not found in database"}), 404
            
            # Get current BGP status from database
            bgp_ipv4_established = device_data.get('bgp_ipv4_established', False)
            bgp_ipv6_established = device_data.get('bgp_ipv6_established', False)
            bgp_ipv4_state = device_data.get('bgp_ipv4_state', 'Unknown')
            bgp_ipv6_state = device_data.get('bgp_ipv6_state', 'Unknown')
            
            logging.info(f"[BGP START] Current database status - IPv4: {bgp_ipv4_state} (established: {bgp_ipv4_established}), IPv6: {bgp_ipv6_state} (established: {bgp_ipv6_established})")
            
        except Exception as e:
            logging.warning(f"[BGP START] Could not get device status from database: {e}")
            # Continue anyway, but log the issue

        # Import FRR Docker utilities
        from utils.frr_docker import FRRDockerManager
        
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        
        try:
            container = frr_manager.client.containers.get(container_name)
        except Exception as e:
            logging.error(f"[BGP START] Container not found: {container_name} - {e}")
            return jsonify({"error": f"Container not found: {container_name}"}), 404

        # Get BGP configuration details
        bgp_asn = bgp_config.get("bgp_asn", 65001)
        neighbor_ipv4 = bgp_config.get("bgp_neighbor_ipv4", "")
        neighbor_ipv6 = bgp_config.get("bgp_neighbor_ipv6", "")
        
        if not neighbor_ipv4 and not neighbor_ipv6:
            return jsonify({"error": "No BGP neighbor IP configured"}), 400

        # Check if specific neighbors were selected in the UI
        selected_neighbors = request.json.get("selected_neighbors", [])
        logging.info(f"[BGP START] Selected neighbors from UI: {selected_neighbors}")
        
        # Determine which neighbors need to be started based on database status and UI selection
        neighbors_to_start = []
        
        # If specific neighbors were selected, only start those
        if selected_neighbors:
            for neighbor_ip in selected_neighbors:
                is_ipv6 = ':' in neighbor_ip
                if is_ipv6 and neighbor_ipv6 and neighbor_ip == neighbor_ipv6 and not bgp_ipv6_established:
                    neighbors_to_start.append(("IPv6", neighbor_ipv6))
                    logging.info(f"[BGP START] Selected IPv6 neighbor {neighbor_ipv6} needs to be started (current state: {bgp_ipv6_state})")
                elif not is_ipv6 and neighbor_ipv4 and neighbor_ip == neighbor_ipv4 and not bgp_ipv4_established:
                    neighbors_to_start.append(("IPv4", neighbor_ipv4))
                    logging.info(f"[BGP START] Selected IPv4 neighbor {neighbor_ipv4} needs to be started (current state: {bgp_ipv4_state})")
                else:
                    logging.info(f"[BGP START] Selected neighbor {neighbor_ip} is already established or doesn't match configured neighbors")
        else:
            # No specific selection - start all non-established neighbors (original behavior)
            if neighbor_ipv4 and not bgp_ipv4_established:
                neighbors_to_start.append(("IPv4", neighbor_ipv4))
                logging.info(f"[BGP START] IPv4 neighbor {neighbor_ipv4} needs to be started (current state: {bgp_ipv4_state})")
            elif neighbor_ipv4 and bgp_ipv4_established:
                logging.info(f"[BGP START] IPv4 neighbor {neighbor_ipv4} is already established, skipping")
                
            if neighbor_ipv6 and not bgp_ipv6_established:
                neighbors_to_start.append(("IPv6", neighbor_ipv6))
                logging.info(f"[BGP START] IPv6 neighbor {neighbor_ipv6} needs to be started (current state: {bgp_ipv6_state})")
            elif neighbor_ipv6 and bgp_ipv6_established:
                logging.info(f"[BGP START] IPv6 neighbor {neighbor_ipv6} is already established, skipping")
        
        if not neighbors_to_start:
            return jsonify({
                "status": "already_started",
                "device_id": device_id,
                "device_name": device_name,
                "message": "All BGP neighbors are already established"
            }), 200

        # Check if address family configuration is missing and needs to be reapplied
        logging.info(f"[BGP START] Checking BGP configuration completeness for device {device_name}")
        
        # Get current BGP configuration
        config_result = container.exec_run(["vtysh", "-c", "show running-config"])
        if config_result.exit_code == 0:
            current_config = config_result.output.decode('utf-8')
            
            # Check if IPv6 address family is missing
            needs_ipv6_af = neighbor_ipv6 and "address-family ipv6 unicast" not in current_config
            needs_ipv4_af = neighbor_ipv4 and "address-family ipv4 unicast" not in current_config
            
            if needs_ipv6_af or needs_ipv4_af:
                logging.info(f"[BGP START] Missing address family configuration detected. Reapplying complete BGP config.")
                
                # Reapply complete BGP configuration using the FRR manager
                from utils.frr_docker import FRRDockerManager
                frr_manager = FRRDockerManager()
                
                # Get device IPs from the container
                ipv4_result = container.exec_run(["ip", "addr", "show", "eth0"])
                ipv6_result = container.exec_run(["ip", "-6", "addr", "show", "eth0"])
                
                ipv4 = ""
                ipv6 = ""
                
                if ipv4_result.exit_code == 0:
                    ipv4_output = ipv4_result.output.decode('utf-8')
                    import re
                    ipv4_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+/\d+)', ipv4_output)
                    if ipv4_match:
                        ipv4 = ipv4_match.group(1)
                
                if ipv6_result.exit_code == 0:
                    ipv6_output = ipv6_result.output.decode('utf-8')
                    ipv6_match = re.search(r'inet6 (2001:db8::\d+/\d+)', ipv6_output)
                    if ipv6_match:
                        ipv6 = ipv6_match.group(1)
                
                # Reapply BGP configuration
                success = frr_manager._configure_bgp_in_container(container_name, bgp_config, ipv4, ipv6)
                if success:
                    logging.info(f"[BGP START] Successfully reapplied complete BGP configuration")
                else:
                    logging.warning(f"[BGP START] Failed to reapply complete BGP configuration")
            else:
                logging.info(f"[BGP START] BGP configuration is complete, proceeding with standard start")

        # Build vtysh commands to remove shutdown from BGP neighbors that need to be started
        commands = [
            "configure terminal",
            f"router bgp {bgp_asn}",
        ]
        
        # Only add no shutdown commands for neighbors that need to be started
        for neighbor_type, neighbor_ip in neighbors_to_start:
            commands.append(f"no neighbor {neighbor_ip} shutdown")
            logging.info(f"[BGP START] Adding no shutdown command for {neighbor_type} neighbor {neighbor_ip}")
            
        commands.extend([
            "end",
            "write"
        ])
        
        # Use here document approach with proper syntax
        config_commands = "\n".join(commands)
        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
        logging.info(f"[BGP START] Executing: {exec_cmd}")
        result = container.exec_run(["bash", "-c", exec_cmd])
        
        if result.exit_code != 0:
            error_msg = result.output.decode() if result.output else "Unknown error"
            logging.error(f"[BGP START] Failed to execute start commands: {error_msg}")
            return jsonify({"error": f"Failed to execute start commands: {error_msg}"}), 500
        
        # All commands succeeded
        started_neighbor_ips = [neighbor_ip for _, neighbor_ip in neighbors_to_start]
        logging.info(f"[BGP START] Successfully removed shutdown from BGP neighbors {started_neighbor_ips} for {device_name}")
        
        # Update BGP status in database after successful start
        try:
            update_data = {}
            
            # Update IPv4 status if IPv4 neighbor was started
            if neighbor_ipv4 and any(neighbor_type == "IPv4" for neighbor_type, _ in neighbors_to_start):
                update_data.update({
                    'bgp_ipv4_established': True,  # Will be updated by monitor when actually established
                    'bgp_ipv4_state': 'Connect',  # Initial state after removing shutdown
                    'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                    'bgp_manual_override': True,  # Flag to prevent monitor from overriding
                    'bgp_manual_override_time': datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[BGP START] Updated IPv4 BGP status to Connect in database (manual override)")
            
            # Update IPv6 status if IPv6 neighbor was started
            if neighbor_ipv6 and any(neighbor_type == "IPv6" for neighbor_type, _ in neighbors_to_start):
                update_data.update({
                    'bgp_ipv6_established': True,  # Will be updated by monitor when actually established
                    'bgp_ipv6_state': 'Connect',  # Initial state after removing shutdown
                    'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                    'bgp_manual_override': True,  # Flag to prevent monitor from overriding
                    'bgp_manual_override_time': datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[BGP START] Updated IPv6 BGP status to Connect in database (manual override)")
            
            if update_data:
                device_db.update_device(device_id, update_data)
                logging.info(f"[BGP START] Successfully updated BGP status in database for device {device_name}")
        except Exception as e:
            logging.warning(f"[BGP START] Failed to update BGP status in database: {e}")
        
        # Clear BGP sessions to ensure start takes effect
        for neighbor_type, neighbor_ip in neighbors_to_start:
            if ":" in neighbor_ip:  # IPv6
                clear_result = container.exec_run(["vtysh", "-c", f"clear ip bgp {neighbor_ip}"])
            else:  # IPv4
                clear_result = container.exec_run(["vtysh", "-c", f"clear ip bgp {neighbor_ip}"])
                
            if clear_result.exit_code == 0:
                logging.info(f"[BGP START] Cleared BGP session with {neighbor_type} neighbor {neighbor_ip}")
            else:
                logging.warning(f"[BGP START] Failed to clear BGP session: {clear_result.output.decode()}")
        
        # After starting BGP, restore route pool configurations if they exist
        try:
            # Get route pool attachments from database
            device_route_pools = device_db.get_device_route_pools(device_id)
            if device_route_pools:
                logging.info(f"[BGP START] Found route pool attachments for {len(device_route_pools)} neighbors, restoring them")
                
                # device_route_pools is already a Dict[str, List[str]] (neighbor_ip -> pool_names)
                route_pools_per_neighbor = device_route_pools
                
                # Get all available route pools
                all_pools_db = device_db.get_all_route_pools()
                all_pools = []
                for pool in all_pools_db:
                    all_pools.append({
                        "name": pool["pool_name"],
                        "subnet": pool["subnet"],
                        "count": pool["route_count"],
                        "first_host": pool["first_host_ip"],
                        "last_host": pool["last_host_ip"],
                        "increment_type": pool.get("increment_type", "host")
                    })
                
                # Restore route pool configurations for each neighbor
                for neighbor_ip, attached_pools in route_pools_per_neighbor.items():
                    if attached_pools and all_pools:
                        logging.info(f"[BGP START] Restoring route pools for neighbor {neighbor_ip}: {attached_pools}")
                        # Run route advertisement configuration in background
                        def _restore_routes(neighbor_ip=neighbor_ip, pools=attached_pools):
                            configure_bgp_route_advertisement(
                                device_id, device_name, bgp_asn, neighbor_ip, 
                                pools, all_pools
                            )
                        import threading
                        threading.Thread(target=_restore_routes, daemon=True).start()
            else:
                logging.info(f"[BGP START] No route pool attachments found for device {device_id}")
        except Exception as e:
            logging.warning(f"[BGP START] Failed to restore route pool configurations: {e}")
        
        return jsonify({
            "status": "started",
            "device_id": device_id,
            "device_name": device_name,
            "neighbor_ips": started_neighbor_ips,
            "neighbors_started": [{"type": neighbor_type, "ip": neighbor_ip} for neighbor_type, neighbor_ip in neighbors_to_start],
            "message": f"BGP neighbors {started_neighbor_ips} started successfully"
        }), 200
            
    except Exception as e:
        logging.error(f"[BGP START ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/frr/status/<device_id>", methods=["GET"])
def get_device_frr_status(device_id):
    """Get FRR container status for a specific device."""
    try:
        from utils.frr_docker import get_bgp_status
        
        status = get_bgp_status(device_id)
        
        return jsonify({
            "device_id": device_id,
            "status": status
        }), 200
        
    except Exception as e:
        logging.error(f"[FRR STATUS ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/frr/neighbors/<device_id>", methods=["GET"])
def get_device_frr_neighbors(device_id):
    """Get FRR BGP neighbors for a specific device."""
    try:
        from utils.frr_docker import get_bgp_neighbors
        
        neighbors = get_bgp_neighbors(device_id)
        
        return jsonify({
            "device_id": device_id,
            "neighbors": neighbors
        }), 200
        
    except Exception as e:
        logging.error(f"[FRR NEIGHBORS ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/frr/routes/<device_id>", methods=["GET"])
def get_device_frr_routes(device_id):
    """Get FRR BGP routes for a specific device."""
    try:
        from utils.frr_docker import get_bgp_routes
        
        routes = get_bgp_routes(device_id)
        
        return jsonify({
            "device_id": device_id,
            "routes": routes
        }), 200
        
    except Exception as e:
        logging.error(f"[FRR ROUTES ERROR] {e}")
        return jsonify({"error": str(e)}), 500


# Global device-to-IP mapping to track which IPs belong to which devices
DEVICE_IP_MAPPING = {}

def _add_ip_to_device_mapping(ip_addr, device_id, interface):
    """Add an IP address to the device mapping."""
    key = f"{interface}:{ip_addr}"
    DEVICE_IP_MAPPING[key] = device_id

def _remove_ip_from_device_mapping(ip_addr, device_id, interface):
    """Remove an IP address from the device mapping."""
    # Try the exact interface name first
    key = f"{interface}:{ip_addr}"
    removed = False
    
    if key in DEVICE_IP_MAPPING and DEVICE_IP_MAPPING[key] == device_id:
        del DEVICE_IP_MAPPING[key]
        removed = True
    
    # If not found and this is a VLAN interface, try alternative naming conventions
    if not removed and "vlan" in interface:
        # Extract VLAN ID and base interface
        if "@" in interface:
            # Old format: vlan20@enp180s0np0
            vlan_part = interface.split("@")[0]  # vlan20
            alt_key = f"{vlan_part}:{ip_addr}"  # vlan20:ip
        else:
            # New format: vlan20
            alt_key = f"{interface}@enp180s0np0:{ip_addr}"  # vlan20@enp180s0np0:ip
        
        if alt_key in DEVICE_IP_MAPPING and DEVICE_IP_MAPPING[alt_key] == device_id:
            del DEVICE_IP_MAPPING[alt_key]
            removed = True

def _is_ip_owned_by_device(ip_addr, device_id, interface):
    """Check if an IP address belongs to a specific device."""
    # Try the exact interface name first
    key = f"{interface}:{ip_addr}"
    result = DEVICE_IP_MAPPING.get(key) == device_id
    
    # If not found and this is a VLAN interface, try alternative naming conventions
    if not result and "vlan" in interface:
        # Extract VLAN ID and base interface
        if "@" in interface:
            # Old format: vlan20@enp180s0np0
            vlan_part = interface.split("@")[0]  # vlan20
            base_part = interface.split("@")[1]  # enp180s0np0
            alt_key = f"{vlan_part}:{ip_addr}"  # vlan20:ip
        else:
            # New format: vlan20
            # Try to find the base interface and construct old format
            alt_key = f"{interface}@enp180s0np0:{ip_addr}"  # vlan20@enp180s0np0:ip
        
        result = DEVICE_IP_MAPPING.get(alt_key) == device_id
    return result

@app.route("/api/debug/mapping", methods=["GET"])
def debug_mapping():
    """Debug endpoint to check current device-to-IP mapping."""
    return jsonify({
        "device_ip_mapping": DEVICE_IP_MAPPING,
        "total_mappings": len(DEVICE_IP_MAPPING)
    }), 200

@app.route("/api/debug/populate_mapping", methods=["POST"])
def populate_mapping():
    """Debug endpoint to manually populate device-to-IP mapping for existing IPs."""
    data = request.get_json()
    device_id = data.get("device_id")
    device_name = data.get("device_name", "")
    ip_address = data.get("ip_address")
    interface = data.get("interface")
    
    if not all([device_id, ip_address, interface]):
        return jsonify({"error": "Missing required fields: device_id, ip_address, interface"}), 400
    
    # Add to mapping
    _add_ip_to_device_mapping(ip_address, device_id, interface)
    
    return jsonify({
        "success": True,
        "message": f"Added mapping for {device_name} ({device_id}): {ip_address} on {interface}",
        "device_ip_mapping": DEVICE_IP_MAPPING
    }), 200

@app.route("/api/device/cleanup", methods=["POST"])
def cleanup_device_interface():
    """Clean up IP addresses from an interface (remove all IPs) or remove entire VLAN interface."""
    data = request.get_json()
    interface = data.get("interface")
    vlan = data.get("vlan", "0")
    cleanup_only = data.get("cleanup_only", False)
    remove_vlan = data.get("remove_vlan", False)
    device_specific = data.get("device_specific", False)
    device_id = data.get("device_id", "")
    device_name = data.get("device_name", "")
    
    if not interface:
        return jsonify({"error": "Interface is required"}), 400
    
    try:
        # Determine the actual interface name - check both old and new naming conventions
        if vlan != "0":
            # Try new naming convention first
            new_interface = f"vlan{vlan}"
            old_interface = f"vlan{vlan}@{interface}"
            
            # Check which interface actually exists
            new_exists = subprocess.run(["ip", "link", "show", new_interface], capture_output=True).returncode == 0
            old_exists = subprocess.run(["ip", "link", "show", old_interface], capture_output=True).returncode == 0
            
            if new_exists:
                actual_interface = new_interface
                # Interface naming logic
            elif old_exists:
                actual_interface = old_interface
            else:
                actual_interface = new_interface
        else:
            actual_interface = interface
        
        # Check if we should remove the entire VLAN interface
        if remove_vlan and vlan != "0":
            # First, bring down the interface
            down_result = subprocess.run(["ip", "link", "set", actual_interface, "down"], 
                                       capture_output=True, text=True, timeout=5)
            
            # Then remove the VLAN interface
            remove_result = subprocess.run(["ip", "link", "del", actual_interface], 
                                         capture_output=True, text=True, timeout=5)
            
            if remove_result.returncode == 0:
                return jsonify({
                    "success": True, 
                    "message": f"VLAN interface {actual_interface} removed successfully",
                    "removed_vlan": actual_interface,
                    "interface": actual_interface
                }), 200
            else:
                return jsonify({
                    "success": False, 
                    "message": f"Failed to remove VLAN interface {actual_interface}",
                    "error": remove_result.stderr
                }), 200
        
        # Regular cleanup: Remove IP addresses from the interface
        # First, get current IP addresses
        result = subprocess.run(["ip", "addr", "show", actual_interface], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            return jsonify({
                "success": False, 
                "message": f"Interface {actual_interface} not found or error getting info",
                "error": result.stderr
            }), 200
        
        # Parse and remove IP addresses
        lines = result.stdout.split('\n')
        removed_ips = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('inet '):
                # Extract IP address with CIDR
                ip_part = line.split()[1]  # e.g., "192.168.1.1/24"
                ip_addr = ip_part.split('/')[0]  # e.g., "192.168.1.1"
                
                # Check if this is device-specific cleanup
                if device_specific and device_id:
                    # Only remove IPs that belong to this specific device
                    if not _is_ip_owned_by_device(ip_addr, device_id, actual_interface):
                        continue
                
                # Remove the IP address
                remove_cmd = ["ip", "addr", "del", ip_part, "dev", actual_interface]
                remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                
                if remove_result.returncode == 0:
                    removed_ips.append(ip_part)
                    # Remove from device mapping
                    _remove_ip_from_device_mapping(ip_addr, device_id, actual_interface)
            
            elif line.startswith('inet6 ') and not line.startswith('inet6 fe80:'):
                # Extract IPv6 address with CIDR (skip link-local)
                ip_part = line.split()[1]  # e.g., "2001:db8::1/64"
                ip_addr = ip_part.split('/')[0]  # e.g., "2001:db8::1"
                
                # Check if this is device-specific cleanup
                if device_specific and device_id:
                    # Only remove IPs that belong to this specific device
                    if not _is_ip_owned_by_device(ip_addr, device_id, actual_interface):
                        continue
                
                # Remove the IPv6 address
                remove_cmd = ["ip", "addr", "del", ip_part, "dev", actual_interface]
                remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                
                if remove_result.returncode == 0:
                    removed_ips.append(ip_part)
                    # Remove from device mapping
                    _remove_ip_from_device_mapping(ip_addr, device_id, actual_interface)
        
        return jsonify({
            "success": True, 
            "message": f"Interface {actual_interface} cleaned up successfully",
            "removed_ips": removed_ips,
            "interface": actual_interface
        }), 200
        
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False, 
            "message": f"Cleanup timeout for interface {actual_interface}",
            "error": "Command timed out"
        }), 200
    except Exception as e:
        return jsonify({
            "success": False, 
            "message": f"Cleanup error for interface {actual_interface}: {str(e)}",
            "error": str(e)
        }), 200


@app.route("/api/interface/reset", methods=["POST"])
def reset_interface_with_vlans():
    """Reset a physical interface and all its associated VLAN interfaces."""
    data = request.get_json()
    interface = data.get("interface")
    remove_vlans = data.get("remove_vlans", True)  # Default to True - remove VLAN interfaces
    cleanup_physical = data.get("cleanup_physical", True)  # Default to True - cleanup physical interface IPs
    
    if not interface:
        return jsonify({"error": "Interface is required"}), 400
    
    try:
        # Normalize interface name (remove server prefix if present)
        base_interface = interface
        if " - " in base_interface:
            base_interface = base_interface.split(" - ", 1)[-1].strip()
        if ":" in base_interface:
            base_interface = base_interface.rsplit(":", 1)[-1].strip()
        
        # Extract base interface name from any format
        parts = base_interface.split()
        if parts:
            base_interface = parts[-1]
        
        logging.info(f"[INTERFACE RESET] Resetting interface '{base_interface}' (normalized from '{interface}')")
        logging.info(f"[INTERFACE RESET] Looking for VLANs associated with base interface: {base_interface}")
        
        # Find all VLAN interfaces associated with this physical interface
        # Check both naming conventions: vlanXX and vlanXX@{base_interface}
        result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            return jsonify({
                "success": False,
                "message": f"Failed to list interfaces: {result.stderr}",
                "error": result.stderr
            }), 200
        
        # Use ip -d link show to get detailed info including parent interfaces
        detailed_result = subprocess.run(["ip", "-d", "link", "show"], capture_output=True, text=True, timeout=10)
        detailed_output = detailed_result.stdout if detailed_result.returncode == 0 else ""
        
        # Parse interfaces to find VLAN interfaces
        vlan_interfaces = []
        lines = result.stdout.split('\n')
        
        for line in lines:
            # Lines like "5: vlan21@ens4np0: <BROADCAST,MULTICAST,UP,LOWER_UP>"
            # or "2: vlan20: <BROADCAST,MULTICAST,UP,LOWER_UP>"
            if ':' in line and not line.strip().startswith('inet'):
                parts = line.split(':', 2)
                if len(parts) >= 2:
                    current_iface = parts[1].strip()
                    # Check if this is a VLAN interface
                    if current_iface.startswith('vlan'):
                        # Format 1: vlan20@{base_interface} - directly associated
                        if '@' in current_iface:
                            # Extract the parent interface from vlanXX@parent
                            if f"@{base_interface}" in current_iface:
                                vlan_interfaces.append(current_iface)
                                logging.info(f"[INTERFACE RESET] Found VLAN with @ format: {current_iface} (parent: {base_interface})")
                            else:
                                # Extract parent from this VLAN to see what it is
                                parent_part = current_iface.split('@', 1)[1] if '@' in current_iface else None
                                logging.info(f"[INTERFACE RESET] VLAN {current_iface} has parent {parent_part}, doesn't match {base_interface}")
                        else:
                            # Format 2: vlanXX (standalone VLAN, need to check parent via -d option)
                            # Check using ip link show directly for this interface to find parent
                            try:
                                link_check = subprocess.run(["ip", "-d", "link", "show", current_iface],
                                                          capture_output=True, text=True, timeout=5)
                                if link_check.returncode == 0:
                                    # Look for parent interface in output
                                    # The output will contain something like "link/ether ... parent ens4np0" or similar
                                    if base_interface in link_check.stdout:
                                        vlan_interfaces.append(current_iface)
                                        logging.debug(f"[INTERFACE RESET] Found standalone VLAN: {current_iface} (parent: {base_interface})")
                            except Exception as e:
                                logging.warning(f"[INTERFACE RESET] Error checking parent for {current_iface}: {e}")
        
        # Also use a simpler approach: use ip link to list all interfaces and grep for VLANs with this parent
        try:
            # Use ip link show type vlan to find all VLAN interfaces
            vlan_result = subprocess.run(["ip", "link", "show", "type", "vlan"], 
                                       capture_output=True, text=True, timeout=10)
            if vlan_result.returncode == 0:
                for line in vlan_result.stdout.split('\n'):
                    # Look for interface names in the output
                    if ':' in line and 'vlan' in line.lower():
                        parts = line.split(':', 2)
                        if len(parts) >= 2:
                            iface_name = parts[1].strip()
                            # Check if it matches our base interface
                            if iface_name.startswith('vlan'):
                                if '@' in iface_name and f"@{base_interface}" in iface_name:
                                    if iface_name not in vlan_interfaces:
                                        vlan_interfaces.append(iface_name)
                                        logging.debug(f"[INTERFACE RESET] Found VLAN via type vlan: {iface_name}")
                                elif '@' not in iface_name:
                                    # Check parent using ip link show
                                    try:
                                        link_check = subprocess.run(["ip", "-d", "link", "show", iface_name],
                                                                  capture_output=True, text=True, timeout=5)
                                        if link_check.returncode == 0 and base_interface in link_check.stdout:
                                            if iface_name not in vlan_interfaces:
                                                vlan_interfaces.append(iface_name)
                                                logging.debug(f"[INTERFACE RESET] Found standalone VLAN via type vlan: {iface_name}")
                                    except Exception:
                                        pass
        except Exception as e:
            logging.warning(f"[INTERFACE RESET] Error listing VLAN interfaces by type: {e}")
        
        # Deduplicate
        vlan_interfaces = list(set(vlan_interfaces))
        
        # Find all devices associated with this interface (including VLAN interfaces)
        devices_to_remove = []
        try:
            from utils.device_database import DeviceDatabase
            device_db = DeviceDatabase()
            
            # Get devices that match the base interface
            base_devices = device_db.get_devices_by_interface(base_interface, include_vlans=True)
            devices_to_remove.extend(base_devices)
            
            # Also check for devices on any of the VLAN interfaces we found
            for vlan_iface in vlan_interfaces:
                vlan_name_only = vlan_iface.split('@')[0] if '@' in vlan_iface else vlan_iface
                vlan_devices = device_db.get_devices_by_interface(vlan_name_only, include_vlans=False)
                # Add devices that aren't already in the list
                for dev in vlan_devices:
                    if dev['device_id'] not in [d['device_id'] for d in devices_to_remove]:
                        devices_to_remove.append(dev)
            
            # Deduplicate by device_id
            seen_ids = set()
            unique_devices = []
            for dev in devices_to_remove:
                if dev['device_id'] not in seen_ids:
                    seen_ids.add(dev['device_id'])
                    unique_devices.append(dev)
            devices_to_remove = unique_devices
            
            logging.info(f"[INTERFACE RESET] Found {len(devices_to_remove)} device(s) associated with interface {base_interface}")
            for dev in devices_to_remove:
                logging.info(f"[INTERFACE RESET]   - Device: {dev.get('device_name', 'N/A')} (ID: {dev.get('device_id', 'N/A')})")
        except Exception as e:
            logging.warning(f"[INTERFACE RESET] Failed to find devices for interface {base_interface}: {e}")
            devices_to_remove = []
        
        if len(vlan_interfaces) == 0:
            logging.warning(f"[INTERFACE RESET] No VLAN interfaces found for {base_interface}")
            logging.info(f"[INTERFACE RESET] Debug: Checking if any VLANs exist with different parent format...")
            # Debug: List all VLAN interfaces to see what we're missing
            debug_result = subprocess.run(["ip", "link", "show", "type", "vlan"], 
                                        capture_output=True, text=True, timeout=10)
            if debug_result.returncode == 0:
                all_vlans = []
                for line in debug_result.stdout.split('\n'):
                    if ':' in line and 'vlan' in line.lower():
                        parts = line.split(':', 2)
                        if len(parts) >= 2:
                            iface_name = parts[1].strip()
                            if iface_name.startswith('vlan'):
                                all_vlans.append(iface_name)
                logging.info(f"[INTERFACE RESET] Debug: Found {len(all_vlans)} total VLAN interfaces on system: {all_vlans}")
        else:
            logging.info(f"[INTERFACE RESET] Found {len(vlan_interfaces)} VLAN interfaces: {vlan_interfaces}")
        
        reset_results = {
            "base_interface": base_interface,
            "vlan_interfaces": vlan_interfaces,
            "vlan_cleanup": [],
            "vlan_removed": [],
            "devices_removed": [],
            "device_removal_errors": [],
            "physical_cleanup": {"success": False, "removed_ips": []}
        }
        
        # Step 1: Clean up and optionally remove all VLAN interfaces
        for vlan_iface in vlan_interfaces:
            try:
                # For VLAN interfaces with @ format, try both full name and VLAN-only name
                # Linux accepts both "vlan20@ens4np0" and "vlan20" as interface names
                vlan_name_only = vlan_iface.split('@')[0] if '@' in vlan_iface else vlan_iface
                
                # Try full name first, then VLAN-only name if that fails
                check_result = subprocess.run(["ip", "link", "show", vlan_iface], 
                                            capture_output=True, text=True, timeout=5)
                
                if check_result.returncode != 0 and '@' in vlan_iface:
                    # Try with just the VLAN name (without @parent)
                    check_result = subprocess.run(["ip", "link", "show", vlan_name_only], 
                                                capture_output=True, text=True, timeout=5)
                    if check_result.returncode == 0:
                        # Update the interface name to the working one
                        logging.debug(f"[INTERFACE RESET] Using VLAN name without @: {vlan_name_only} (full name: {vlan_iface})")
                        vlan_iface = vlan_name_only
                
                if check_result.returncode != 0:
                    logging.warning(f"[INTERFACE RESET] VLAN interface {vlan_iface} (tried full and VLAN-only) not found, skipping")
                    continue
                
                # Verify parent interface matches (for both formats)
                # Use vlan_name_only for actual commands, but check parent from original name if needed
                original_vlan_name = vlan_iface
                if '@' in original_vlan_name:
                    # For vlanXX@parent format, check that parent matches
                    parent_from_name = original_vlan_name.split('@', 1)[1]
                    if parent_from_name != base_interface:
                        logging.debug(f"[INTERFACE RESET] VLAN {original_vlan_name} parent ({parent_from_name}) doesn't match {base_interface}, skipping")
                        continue
                else:
                    # For standalone vlanXX format, verify parent interface matches using ip link
                    link_result = subprocess.run(["ip", "-d", "link", "show", vlan_iface],
                                               capture_output=True, text=True, timeout=5)
                    if link_result.returncode != 0 or base_interface not in link_result.stdout:
                        logging.debug(f"[INTERFACE RESET] VLAN {vlan_iface} is not linked to {base_interface}, skipping")
                        continue
                
                # Clean up IPs from VLAN interface (use the working interface name)
                vlan_result = subprocess.run(["ip", "addr", "show", vlan_iface],
                                           capture_output=True, text=True, timeout=10)
                
                removed_vlan_ips = []
                if vlan_result.returncode == 0:
                    for vlan_line in vlan_result.stdout.split('\n'):
                        vlan_line = vlan_line.strip()
                        if vlan_line.startswith('inet ') and not vlan_line.startswith('inet 127.'):
                            ip_part = vlan_line.split()[1]
                            remove_cmd = ["ip", "addr", "del", ip_part, "dev", vlan_iface]
                            remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                            if remove_result.returncode == 0:
                                removed_vlan_ips.append(ip_part)
                        elif vlan_line.startswith('inet6 ') and not vlan_line.startswith('inet6 fe80:'):
                            ip_part = vlan_line.split()[1]
                            remove_cmd = ["ip", "addr", "del", ip_part, "dev", vlan_iface]
                            remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                            if remove_result.returncode == 0:
                                removed_vlan_ips.append(ip_part)
                
                reset_results["vlan_cleanup"].append({
                    "interface": vlan_iface,
                    "removed_ips": removed_vlan_ips,
                    "success": True
                })
                
                # Optionally remove the VLAN interface (use the working interface name)
                if remove_vlans:
                    # Bring down first
                    subprocess.run(["ip", "link", "set", vlan_iface, "down"],
                                 capture_output=True, timeout=5)
                    # Remove the VLAN interface
                    remove_result = subprocess.run(["ip", "link", "del", vlan_iface],
                                                  capture_output=True, text=True, timeout=5)
                    if remove_result.returncode == 0:
                        # Store original name for reporting
                        reset_results["vlan_removed"].append(original_vlan_name if original_vlan_name != vlan_iface else vlan_iface)
                        logging.info(f"[INTERFACE RESET] Removed VLAN interface {vlan_iface} (original: {original_vlan_name})")
                    else:
                        logging.warning(f"[INTERFACE RESET] Failed to remove VLAN interface {vlan_iface}: {remove_result.stderr}")
                
            except Exception as e:
                logging.error(f"[INTERFACE RESET] Error processing VLAN interface {vlan_iface}: {e}")
                reset_results["vlan_cleanup"].append({
                    "interface": vlan_iface,
                    "success": False,
                    "error": str(e)
                })
        
        # Step 1.5: Remove all devices associated with this interface
        removed_devices = []
        device_removal_errors = []
        
        for device in devices_to_remove:
            device_id = device.get('device_id')
            device_name = device.get('device_name', 'Unknown')
            
            try:
                logging.info(f"[INTERFACE RESET] Removing device {device_name} (ID: {device_id}) associated with interface {base_interface}")
                
                # Call the device remove endpoint logic directly
                from utils.device_database import DeviceDatabase
                device_db = DeviceDatabase()
                
                # Get device info before removing
                device_info = device_db.get_device(device_id)
                if not device_info:
                    logging.warning(f"[INTERFACE RESET] Device {device_id} not found in database, skipping")
                    continue
                
                # Stop and remove FRR Docker container
                try:
                    from utils.frr_docker import FRRDockerManager
                    frr_manager = FRRDockerManager()
                    frr_manager.stop_frr_container(device_id, device_name)
                    logging.info(f"[INTERFACE RESET] Stopped FRR container for device {device_name}")
                except Exception as e:
                    logging.warning(f"[INTERFACE RESET] Failed to stop FRR container for device {device_name}: {e}")
                
                # Clean up device-to-IP mapping
                ipv4_addr = device_info.get('ipv4_address')
                ipv6_addr = device_info.get('ipv6_address')
                device_interface = device_info.get('interface', base_interface)
                
                if ipv4_addr:
                    _remove_ip_from_device_mapping(ipv4_addr, device_id, device_interface)
                if ipv6_addr:
                    _remove_ip_from_device_mapping(ipv6_addr, device_id, device_interface)
                
                # Clean up protocol configurations
                protocols = device_info.get('protocols', [])
                if isinstance(protocols, str):
                    import json
                    try:
                        protocols = json.loads(protocols)
                    except:
                        protocols = []
                
                # Cleanup OSPF if configured
                if isinstance(protocols, list) and "OSPF" in protocols:
                    try:
                        from utils.ospf import cleanup_device_routes, remove_ospf_config
                        cleanup_device_routes(device_id)
                        remove_ospf_config(device_id)
                        logging.info(f"[INTERFACE RESET] Cleaned up OSPF for device {device_name}")
                    except Exception as e:
                        logging.warning(f"[INTERFACE RESET] Failed to cleanup OSPF for device {device_name}: {e}")
                
                # Cleanup BGP if configured
                if isinstance(protocols, list) and "BGP" in protocols:
                    try:
                        from utils.bgp import remove_bgp_config
                        # Remove BGP configuration
                        remove_bgp_config(device_id)
                        logging.info(f"[INTERFACE RESET] Cleaned up BGP for device {device_name}")
                    except Exception as e:
                        logging.warning(f"[INTERFACE RESET] Failed to cleanup BGP for device {device_name}: {e}")
                
                # Cleanup ISIS if configured
                if isinstance(protocols, list) and ("IS-IS" in protocols or "ISIS" in protocols):
                    try:
                        from utils.isis import stop_isis_neighbor
                        isis_config = device_info.get('isis_config', {})
                        if isinstance(isis_config, str):
                            import json
                            try:
                                isis_config = json.loads(isis_config)
                            except:
                                isis_config = {}
                        stop_isis_neighbor(device_id, device_name, isis_config=isis_config)
                        logging.info(f"[INTERFACE RESET] Cleaned up ISIS for device {device_name}")
                    except Exception as e:
                        logging.warning(f"[INTERFACE RESET] Failed to cleanup ISIS for device {device_name}: {e}")
                
                # Clean up route pools from database (explicit cleanup)
                try:
                    device_db.remove_device_route_pools(device_id)
                    logging.info(f"[INTERFACE RESET] Cleaned up route pools for device {device_name}")
                except Exception as e:
                    logging.warning(f"[INTERFACE RESET] Failed to cleanup route pools for device {device_name}: {e}")
                
                # Remove device from database (this will cascade delete device_stats, device_events, device_route_pools)
                if device_db.remove_device(device_id):
                    removed_devices.append({
                        "device_id": device_id,
                        "device_name": device_name,
                        "success": True
                    })
                    logging.info(f"[INTERFACE RESET] Successfully removed device {device_name} from database")
                else:
                    device_removal_errors.append({
                        "device_id": device_id,
                        "device_name": device_name,
                        "error": "Failed to remove from database"
                    })
                    logging.error(f"[INTERFACE RESET] Failed to remove device {device_name} from database")
                    
            except Exception as e:
                device_removal_errors.append({
                    "device_id": device_id,
                    "device_name": device_name,
                    "error": str(e)
                })
                logging.error(f"[INTERFACE RESET] Error removing device {device_name}: {e}")
                import traceback
                logging.error(f"[INTERFACE RESET] Traceback: {traceback.format_exc()}")
        
        reset_results["devices_removed"] = removed_devices
        reset_results["device_removal_errors"] = device_removal_errors
        
        # Step 2: Clean up physical interface IPs (if requested)
        if cleanup_physical:
            try:
                physical_result = subprocess.run(["ip", "addr", "show", base_interface],
                                               capture_output=True, text=True, timeout=10)
                
                removed_physical_ips = []
                if physical_result.returncode == 0:
                    for phys_line in physical_result.stdout.split('\n'):
                        phys_line = phys_line.strip()
                        if phys_line.startswith('inet ') and not phys_line.startswith('inet 127.'):
                            ip_part = phys_line.split()[1]
                            remove_cmd = ["ip", "addr", "del", ip_part, "dev", base_interface]
                            remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                            if remove_result.returncode == 0:
                                removed_physical_ips.append(ip_part)
                        elif phys_line.startswith('inet6 ') and not phys_line.startswith('inet6 fe80:'):
                            ip_part = phys_line.split()[1]
                            remove_cmd = ["ip", "addr", "del", ip_part, "dev", base_interface]
                            remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                            if remove_result.returncode == 0:
                                removed_physical_ips.append(ip_part)
                    
                    reset_results["physical_cleanup"] = {
                        "success": True,
                        "removed_ips": removed_physical_ips
                    }
                    logging.info(f"[INTERFACE RESET] Cleaned up {len(removed_physical_ips)} IPs from physical interface {base_interface}")
                else:
                    logging.warning(f"[INTERFACE RESET] Physical interface {base_interface} not found or error: {physical_result.stderr}")
                    
            except Exception as e:
                logging.error(f"[INTERFACE RESET] Error cleaning up physical interface {base_interface}: {e}")
                reset_results["physical_cleanup"]["error"] = str(e)
        
        # Build response message
        message_parts = [f"Interface reset completed for {base_interface}"]
        
        if removed_devices:
            message_parts.append(f"{len(removed_devices)} device(s) removed")
        
        if device_removal_errors:
            message_parts.append(f"{len(device_removal_errors)} device removal error(s)")
        
        return jsonify({
            "success": True,
            "message": ". ".join(message_parts),
            "details": reset_results
        }), 200
        
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "message": f"Interface reset timeout for {interface}",
            "error": "Command timed out"
        }), 200
    except Exception as e:
        logging.error(f"[INTERFACE RESET] Error resetting interface {interface}: {e}")
        import traceback
        logging.error(f"[INTERFACE RESET] Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "message": f"Interface reset error for {interface}: {str(e)}",
            "error": str(e)
        }), 200


# Updated FRR BGP status endpoint (included in server app)
@app.route("/api/frr/status", methods=["GET"])
def frr_status():
    try:
        # Check if Docker FRR is available
        from utils.frr_docker import FRRDockerManager, list_all_containers
        frr_manager = FRRDockerManager()
        
        # Get all running FRR containers
        containers = list_all_containers()
        
        all_neighbors = []
        
        for container_info in containers:
            container_name = container_info.get("name", "")
            device_id = container_info.get("device_id", "")
            
            if not container_name:
                continue
                
            try:
                # Get container and execute BGP summary
                container = frr_manager.client.containers.get(container_name)
                
                # Get IPv4 BGP neighbors
                try:
                    result = container.exec_run("vtysh -c 'show ip bgp summary'")
                    if result.exit_code == 0:
                        lines = result.output.decode("utf-8").splitlines()
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 10 and re.match(r"\d+\.\d+\.\d+\.\d+", parts[0]):
                                neighbor_info = {
                                    "device": device_id,
                                    "neighbor_ip": parts[0],
                                    "neighbor_type": "IPv4",
                                    "local_as": "Unknown",
                                    "remote_as": "Unknown", 
                                    "state": parts[9] if len(parts) > 9 else "Unknown",
                                    "routes": "Unknown"
                                }
                                all_neighbors.append(neighbor_info)
                except Exception as e:
                    logging.warning(f"Failed to get IPv4 BGP summary from {container_name}: {e}")
                
                # Get IPv6 BGP neighbors
                try:
                    result = container.exec_run("vtysh -c 'show ipv6 bgp summary'")
                    if result.exit_code == 0:
                        lines = result.output.decode("utf-8").splitlines()
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 10 and ":" in parts[0]:
                                neighbor_info = {
                                    "device": device_id,
                                    "neighbor_ip": parts[0],
                                    "neighbor_type": "IPv6",
                                    "local_as": "Unknown",
                                    "remote_as": "Unknown",
                                    "state": parts[9] if len(parts) > 9 else "Unknown", 
                                    "routes": "Unknown"
                                }
                                all_neighbors.append(neighbor_info)
                except Exception as e:
                    logging.warning(f"Failed to get IPv6 BGP summary from {container_name}: {e}")
                    
            except Exception as e:
                logging.warning(f"Failed to get BGP status from container {container_name}: {e}")
                continue
        
        # If no Docker containers, fall back to system FRR
        if not all_neighbors:
            try:
                # Step 1: Get list of BGP neighbors from summary (both IPv4 and IPv6)
                output = subprocess.check_output(["vtysh", "-c", "show ip bgp summary"], stderr=subprocess.STDOUT)
                lines = output.decode("utf-8").splitlines()

                neighbors = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 10 and re.match(r"\d+\.\d+\.\d+\.\d+", parts[0]):
                        neighbors.append(parts[0])

                # Also get IPv6 neighbors
                try:
                    output_v6 = subprocess.check_output(["vtysh", "-c", "show ipv6 bgp summary"], stderr=subprocess.STDOUT)
                    lines_v6 = output_v6.decode("utf-8").splitlines()
                    for line in lines_v6:
                        parts = line.split()
                        if len(parts) >= 10 and ":" in parts[0]:  # IPv6 address contains colons
                            neighbors.append(parts[0])
                except subprocess.CalledProcessError:
                    # IPv6 BGP might not be configured, that's okay
                    pass

                peer_states = []
                for ip in neighbors:
                    try:
                        # Try IPv4 first, then IPv6
                        neighbor_out = subprocess.check_output(
                            ["vtysh", "-c", f"show ip bgp neighbor {ip}"],
                            stderr=subprocess.STDOUT
                        )
                    except subprocess.CalledProcessError:
                        try:
                            # Try IPv6
                            neighbor_out = subprocess.check_output(
                                ["vtysh", "-c", f"show ipv6 bgp neighbor {ip}"],
                                stderr=subprocess.STDOUT
                            )
                        except subprocess.CalledProcessError:
                            # Both IPv4 and IPv6 failed for this neighbor
                            logging.error(f"[BGP ERROR] Failed to fetch neighbor {ip} status")
                            peer_states.append({"neighbor": ip, "state": "Error", "session": "Error", "prefixes_received": 0})
                            continue
                    
                    decoded = neighbor_out.decode("utf-8")
                    logging.debug(f"[BGP DEBUG] neighbor_out for {ip}:\n{decoded}")

                    # Match BGP state line
                    state_match = re.search(r"BGP state = (\w+)", decoded)
                    uptime_match = re.search(r"BGP neighbor is (?:up|down), the session is (\w+)", decoded)
                    prefix_match = re.search(r"Prefix received count is (\d+)", decoded)

                    state = state_match.group(1) if state_match else "Unknown"
                    session = uptime_match.group(1) if uptime_match else "Unknown"
                    prefixes = int(prefix_match.group(1)) if prefix_match else 0

                    peer_states.append({
                        "neighbor": ip,
                        "state": state,
                        "session": session,
                        "prefixes_received": prefixes
                    })

                all_neighbors = peer_states
            except subprocess.CalledProcessError:
                # System FRR not available
                pass

        return jsonify({"neighbors": all_neighbors})

    except Exception as e:
        logging.error(f"[FRR ERROR] {e}")
        return jsonify({"error": str(e)}), 500







@app.route('/api/streams/register', methods=['POST'])
# ============================================================================
# BGP Route Management API Endpoints
# ============================================================================

@app.route("/api/bgp/routes/advertise", methods=["POST"])
def advertise_bgp_routes():
    """Advertise BGP routes for a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    device_id = data.get("device_id")
    route_config = data.get("route_config", {})
    
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    try:
        from utils import bgp
        result = bgp.advertise_bgp_routes(device_id, route_config)
        
        if "error" in result:
            return jsonify(result), 400
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP ROUTES ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/routes/withdraw", methods=["POST"])
def withdraw_bgp_routes():
    """Withdraw BGP routes for a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    device_id = data.get("device_id")
    prefixes = data.get("prefixes")  # Optional: specific prefixes to withdraw
    
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    try:
        from utils import bgp
        result = bgp.withdraw_bgp_routes(device_id, prefixes)
        
        if "error" in result:
            return jsonify(result), 400
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP WITHDRAW ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/routes", methods=["GET"])
def get_bgp_routes():
    """Get BGP routes for a device or all devices."""
    device_id = request.args.get("device_id")
    
    try:
        from utils import bgp
        result = bgp.get_bgp_routes(device_id)
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP GET ROUTES ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/routes/generate", methods=["POST"])
def generate_bgp_test_routes():
    """Generate and advertise test BGP routes for a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    device_id = data.get("device_id")
    route_count = data.get("route_count", 10)
    base_prefix = data.get("base_prefix", "10.0.0.0/8")
    
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    try:
        from utils import bgp
        result = bgp.generate_bgp_test_routes(device_id, route_count, base_prefix)
        
        if "error" in result:
            return jsonify(result), 400
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP GENERATE ROUTES ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/statistics", methods=["GET"])
def get_bgp_statistics():
    """Get BGP route statistics."""
    try:
        from utils import bgp
        result = bgp.get_bgp_route_statistics()
        
        if "error" in result:
            return jsonify(result), 400
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP STATISTICS ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/status/<device_id>", methods=["GET"])
def get_device_bgp_status(device_id):
    """Get BGP status for a specific device"""
    try:
        from utils.frr_docker import get_bgp_status, get_bgp_neighbors
        
        # Use the device_id directly as the device name for container lookup
        device_name = device_id
        
        # Get BGP status from container
        bgp_status = get_bgp_status(device_id, device_name)
        bgp_neighbors = get_bgp_neighbors(device_id, device_name)
        
        # Parse BGP summary to extract neighbor states
        neighbors_data = []
        if bgp_status.get('status') == 'success':
            summary_output = bgp_status.get('output', '')
            
            # Parse BGP summary output to extract neighbor information
            lines = summary_output.split('\n')
            for line in lines:
                # Look for neighbor lines like: "20.0.0.250      4        300      1132      1017        0    0    0 08:27:28     (Policy) (Policy) N/A"
                # or IPv6 lines like: "2001:db8::1     4      65001        17        17        0    0    0 00:04:52     (Policy) (Policy) N/A"
                parts = line.strip().split()
                if len(parts) >= 8 and (parts[0].count('.') == 3 or ':' in parts[0]):  # IPv4 or IPv6 address
                    neighbor_ip = parts[0]
                    neighbor_as = parts[2]
                    
                    # Find the state - it's usually after the uptime (8th field) and before the description
                    uptime = parts[8] if len(parts) > 8 else "00:00:00"
                    
                    # Look for state in the remaining parts - usually contains parentheses
                    state = "Unknown"
                    for i in range(8, len(parts)):
                        if '(' in parts[i] and ')' in parts[i]:
                            state = parts[i]
                            break
                        elif parts[i] in ['Established', 'Active', 'Idle', 'Connect', 'OpenSent', 'OpenConfirm']:
                            state = parts[i]
                            break
                    
                    # Special handling for (Policy) state - this indicates BGP is established
                    if state == "(Policy)":
                        state = "Established"
                        logging.info(f"[BGP STATUS] Mapped (Policy) to Established for {neighbor_ip}")
                    
                    # If state is still "Unknown" and we have uptime, check if session is actually established
                    # by looking at the uptime - if it's not "00:00:00", the session is likely established
                    if state == "Unknown" and uptime != "00:00:00" and ":" in uptime:
                        # If we have a valid uptime (not 00:00:00), the BGP session is likely Established
                        # even if the summary shows "N/A" for state
                        state = "Established"
                        logging.info(f"[BGP STATUS FIX] Setting state to Established for {neighbor_ip} based on uptime {uptime}")
                    
                    neighbors_data.append({
                        'neighbor_ip': neighbor_ip,
                        'neighbor_as': neighbor_as,
                        'state': state,
                        'uptime': uptime
                    })
        
        # Calculate BGP established status
        bgp_established = False
        bgp_ipv4_established = False
        bgp_ipv6_established = False
        bgp_state = "Unknown"
        
        if neighbors_data:
            # Check if any neighbors are established
            established_neighbors = [n for n in neighbors_data if n.get('state') == 'Established']
            bgp_established = len(established_neighbors) > 0
            
            # Check IPv4 and IPv6 separately
            ipv4_neighbors = [n for n in neighbors_data if '.' in n.get('neighbor_ip', '') and n.get('state') == 'Established']
            ipv6_neighbors = [n for n in neighbors_data if ':' in n.get('neighbor_ip', '') and n.get('state') == 'Established']
            
            bgp_ipv4_established = len(ipv4_neighbors) > 0
            bgp_ipv6_established = len(ipv6_neighbors) > 0
            
            # Set overall BGP state
            if bgp_established:
                bgp_state = "Established"
            else:
                bgp_state = "Not Established"
        
        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'bgp_status': bgp_status,
            'neighbors': neighbors_data,
            'bgp_established': bgp_established,
            'bgp_ipv4_established': bgp_ipv4_established,
            'bgp_ipv6_established': bgp_ipv6_established,
            'bgp_state': bgp_state
        }), 200
        
    except Exception as e:
        logging.error(f"Failed to get BGP status for device {device_id}: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route("/api/bgp/status/batch", methods=["POST"])
def get_device_bgp_status_batch():
    """Get BGP status for multiple devices in a single request (batching optimization)."""
    data = request.get_json()
    device_ids = data.get("device_ids", [])
    
    if not device_ids:
        return jsonify({"error": "Device IDs list is required"}), 400
    
    results = {}
    try:
        from utils.frr_docker import get_bgp_status, get_bgp_neighbors
        
        for device_id in device_ids:
            try:
                device_name = "device1"  # TODO: Make this more dynamic
                
                # Get BGP status from container
                bgp_status = get_bgp_status(device_id, device_name)
                bgp_neighbors = get_bgp_neighbors(device_id, device_name)
                
                # Parse BGP summary to extract neighbor states
                neighbors_data = []
                if bgp_status.get('status') == 'running':
                    summary_output = bgp_status.get('bgp_summary', '')
                    
                    # Parse BGP summary output
                    lines = summary_output.split('\n')
                    for line in lines:
                        parts = line.strip().split()
                        if len(parts) >= 8 and parts[0].count('.') == 3:  # Looks like an IP address
                            neighbor_ip = parts[0]
                            neighbor_as = parts[2]
                            uptime = parts[7] if len(parts) > 7 else "00:00:00"
                            
                            # Find the state
                            state = "Unknown"
                            for i in range(8, len(parts)):
                                if '(' in parts[i] and ')' in parts[i]:
                                    state = parts[i]
                                    break
                                elif parts[i] in ['Established', 'Active', 'Idle', 'Connect', 'OpenSent', 'OpenConfirm']:
                                    state = parts[i]
                                    break
                            
                            neighbors_data.append({
                                'neighbor_ip': neighbor_ip,
                                'neighbor_as': neighbor_as,
                                'state': state,
                                'uptime': uptime
                            })
                
                results[device_id] = {
                    'status': 'success',
                    'bgp_status': bgp_status,
                    'neighbors': neighbors_data
                }
            except Exception as e:
                results[device_id] = {
                    'status': 'error',
                    'error': str(e)
                }
        
        return jsonify({"results": results, "total": len(device_ids)}), 200
        
    except Exception as e:
        logging.error(f"Failed to get batched BGP status: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e),
            'results': results
        }), 500


@app.route("/api/bgp/cleanup", methods=["POST"])
def cleanup_bgp_routes():
    """Clean up BGP routes for a specific device or all devices."""
    data = request.get_json() or {}
    device_id = data.get("device_id")
    
    try:
        if device_id:
            # Clean up specific device - use Docker FRR manager
            logging.info(f"[BGP CLEANUP] Starting BGP cleanup for device {device_id}")
            
            success = False
            try:
                from utils.frr_docker import FRRDockerManager
                logging.info(f"[BGP CLEANUP] Successfully imported FRRDockerManager")
                
                frr_manager = FRRDockerManager()
                logging.info(f"[BGP CLEANUP] Created FrrDockerManager instance")
                
                # Remove BGP neighbors from Docker container
                success = frr_manager.remove_bgp_neighbors(device_id)
                
                if success:
                    logging.info(f"Successfully removed BGP neighbors from Docker container for device {device_id}")
                else:
                    logging.warning(f"Failed to remove BGP neighbors from Docker container for device {device_id}")
            except Exception as docker_e:
                logging.error(f"[BGP CLEANUP] Docker FRR cleanup failed: {docker_e}")
                logging.error(f"[BGP CLEANUP] Exception type: {type(docker_e)}")
                import traceback
                logging.error(f"[BGP CLEANUP] Traceback: {traceback.format_exc()}")
            
            # Also clean up system FRR routes if any
            try:
                from utils import bgp
                bgp.cleanup_device_routes(device_id)
                bgp.remove_bgp_config(device_id)
            except Exception as bgp_e:
                logging.warning(f"System FRR cleanup failed (expected for Docker-only setup): {bgp_e}")
            
            return jsonify({
                "message": f"Cleaned up BGP configuration for device {device_id}",
                "device_id": device_id,
                "docker_cleanup": success
            }), 200
        else:
            # Clean up all devices - stop all FRR containers
            from utils.frr_docker import FRRDockerManager
            
            frr_manager = FRRDockerManager()
            
            # Get all running FRR containers and stop them
            try:
                containers = frr_manager.client.containers.list(filters={"name": frr_manager.container_prefix})
                for container in containers:
                    device_id_from_container = container.name.replace(f"{frr_manager.container_prefix}-", "")
                    frr_manager.stop_frr_container(device_id_from_container)
                    logging.info(f"Stopped FRR container for device {device_id_from_container}")
            except Exception as e:
                logging.warning(f"Failed to stop some FRR containers: {e}")
            
            # Also clean up system FRR if any
            try:
                from utils import bgp
                bgp.cleanup_all_bgp_routes()
            except Exception as bgp_e:
                logging.warning(f"System FRR cleanup failed (expected for Docker-only setup): {bgp_e}")
            
            return jsonify({
                "message": "Cleaned up all BGP routes and configurations"
            }), 200
        
    except Exception as e:
        logging.error(f"[BGP CLEANUP ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ospf/cleanup", methods=["POST"])
def cleanup_ospf_routes():
    """Clean up OSPF routes for a specific device or all devices."""
    data = request.get_json() or {}
    device_id = data.get("device_id")
    
    try:
        if device_id:
            # Clean up specific device - use Docker FRR manager
            logging.info(f"[OSPF CLEANUP] Starting OSPF cleanup for device {device_id}")
            
            success = False
            try:
                from utils.frr_docker import FRRDockerManager
                logging.info(f"[OSPF CLEANUP] Successfully imported FRRDockerManager")
                
                frr_manager = FRRDockerManager()
                logging.info(f"[OSPF CLEANUP] Created FrrDockerManager instance")
                
                # Remove OSPF configuration from Docker container
                # Note: remove_ospf_config method doesn't exist yet, so we'll just stop the container
                success = frr_manager.stop_frr_container(device_id)
                
                if success:
                    logging.info(f"Successfully removed OSPF configuration from Docker container for device {device_id}")
                else:
                    logging.warning(f"Failed to remove OSPF configuration from Docker container for device {device_id}")
            except Exception as docker_e:
                logging.error(f"[OSPF CLEANUP] Docker FRR cleanup failed: {docker_e}")
                logging.error(f"[OSPF CLEANUP] Exception type: {type(docker_e)}")
                import traceback
                logging.error(f"[OSPF CLEANUP] Traceback: {traceback.format_exc()}")
            
            # Also clean up system FRR routes if any
            try:
                from utils import ospf
                ospf.cleanup_device_routes(device_id)
                ospf.remove_ospf_config(device_id)
            except Exception as ospf_e:
                logging.warning(f"System FRR cleanup failed (expected for Docker-only setup): {ospf_e}")
            
            return jsonify({
                "message": f"Cleaned up OSPF configuration for device {device_id}",
                "device_id": device_id,
                "docker_cleanup": success
            }), 200
        else:
            # Clean up all devices - stop all FRR containers
            from utils.frr_docker import FRRDockerManager
            
            frr_manager = FRRDockerManager()
            
            # Get all running FRR containers and stop them
            try:
                containers = frr_manager.client.containers.list(filters={"name": frr_manager.container_prefix})
                for container in containers:
                    device_id_from_container = container.name.replace(f"{frr_manager.container_prefix}-", "")
                    frr_manager.stop_frr_container(device_id_from_container)
                    logging.info(f"Stopped FRR container for device {device_id_from_container}")
            except Exception as e:
                logging.warning(f"Failed to stop some FRR containers: {e}")
            
            # Also clean up system FRR if any
            try:
                from utils import ospf
                ospf.cleanup_all_ospf_routes()
            except Exception as ospf_e:
                logging.warning(f"System FRR cleanup failed (expected for Docker-only setup): {ospf_e}")
            
            return jsonify({
                "message": "Cleaned up all OSPF routes and configurations"
            }), 200
        
    except Exception as e:
        logging.error(f"[OSPF CLEANUP ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/status", methods=["GET"])
def get_bgp_status():
    """Get BGP cleanup status and instance information."""
    try:
        from utils import bgp
        result = bgp.get_bgp_cleanup_status()
        
        # Add Docker container status if available
        if bgp.DOCKER_FRR_AVAILABLE:
            try:
                from utils.frr_docker import list_all_containers
                result["docker_containers"] = list_all_containers()
                result["docker_available"] = True
            except Exception as e:
                logging.warning(f"[BGP STATUS] Failed to get Docker status: {e}")
                result["docker_available"] = False
        else:
            result["docker_available"] = False
        
        return jsonify(result), 200
        
    except Exception as e:
        logging.error(f"[BGP STATUS ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/neighbors", methods=["GET"])
def get_bgp_neighbors():
    """Get BGP neighbors from Docker containers for client UI."""
    try:
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        
        # Get all running FRR containers
        containers = frr_manager.list_containers()
        
        all_neighbors = []
        
        for container_info in containers:
            container_name = container_info.get("name", "")
            device_id = container_info.get("device_id", "")
            
            if not container_name:
                continue
                
            try:
                # Get container and execute BGP summary
                container = frr_manager.client.containers.get(container_name)
                
                # Get IPv4 BGP neighbors
                try:
                    result = container.exec_run("vtysh -c 'show ip bgp summary'")
                    if result.exit_code == 0:
                        lines = result.output.decode("utf-8").splitlines()
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 10 and re.match(r"\d+\.\d+\.\d+\.\d+", parts[0]):
                                neighbor_info = {
                                    "device": device_id,
                                    "neighbor_ip": parts[0],
                                    "neighbor_type": "IPv4",
                                    "local_as": "Unknown",
                                    "remote_as": "Unknown", 
                                    "state": parts[9] if len(parts) > 9 else "Unknown",
                                    "routes": "Unknown"
                                }
                                all_neighbors.append(neighbor_info)
                except Exception as e:
                    logging.warning(f"Failed to get IPv4 BGP summary from {container_name}: {e}")
                    
            except Exception as e:
                logging.warning(f"Failed to get BGP status from container {container_name}: {e}")
                continue
        
        return jsonify({"neighbors": all_neighbors}), 200
        
    except Exception as e:
        logging.error(f"[BGP NEIGHBORS ERROR] {e}")
        return jsonify({"error": str(e), "neighbors": []}), 500

@app.route("/api/device/frr/start", methods=["POST"])
def start_device_frr():
    """Start FRR Docker container for a specific device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400
    
    try:
        device_id = data.get("device_id")
        device_config = data.get("device_config", {})
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        from utils import bgp
        if not bgp.DOCKER_FRR_AVAILABLE:
            return jsonify({"error": "Docker FRR not available"}), 503
        
        from utils.frr_docker import start_frr_container
        
        container_name = start_frr_container(device_id, device_config)
        
        return jsonify({
            "message": f"FRR container started for device {device_id}",
            "container_name": container_name,
            "device_id": device_id
        }), 200
        
    except Exception as e:
        logging.error(f"[FRR START ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/frr/stop", methods=["POST"])
def stop_device_frr():
    """Stop FRR Docker container for a specific device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400
    
    try:
        device_id = data.get("device_id")
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        from utils import bgp
        if not bgp.DOCKER_FRR_AVAILABLE:
            return jsonify({"error": "Docker FRR not available"}), 503
        
        from utils.frr_docker import stop_frr_container
        
        success = stop_frr_container(device_id)
        
        if success:
            return jsonify({
                "message": f"FRR container stopped for device {device_id}",
                "device_id": device_id
            }), 200
        else:
            return jsonify({"error": f"Failed to stop FRR container for device {device_id}"}), 500
        
    except Exception as e:
        logging.error(f"[FRR STOP ERROR] {e}")
        return jsonify({"error": str(e)}), 500





@app.route('/api/streams/register', methods=['POST'])
def register_streams():
    data = request.json
    port = data.get("port")
    streams = data.get("streams", [])
    print(f"*************** {streams}")
    if not port or not isinstance(streams, list):
        return jsonify({"error": "Invalid registration data"}), 400

    STREAMS[port] = streams
    return jsonify({"message": f"Streams registered for {port}"}), 200


@app.route('/api/streams/update', methods=['POST'])
def update_stream():
    data = request.json
    port = data.get("port")
    stream = data.get("stream")

    if not port or not stream or "name" not in stream:
        return jsonify({"error": "Invalid request"}), 400

    # Automatically initialize the port if not found
    if port not in STREAMS:
        STREAMS[port] = []

    # Update stream if name matches, else append
    for i, s in enumerate(STREAMS[port]):
        if s.get("name") == stream["name"]:
            STREAMS[port][i] = stream
            return jsonify({"message": f"Stream '{stream['name']}' updated successfully"}), 200

    STREAMS[port].append(stream)
    return jsonify({"message": f"Stream '{stream['name']}' added successfully"}), 200





@app.route('/api/interfaces', methods=['GET'])
def get_interfaces():
    """
    API endpoint to fetch dynamic network interfaces with traffic statistics.
    Excludes VLAN interfaces to prevent them from appearing as separate ports.
    """
    interfaces = []
    try:
        # Use psutil to fetch network interface details
        for name, stats in psutil.net_if_stats().items():
            # Skip VLAN interfaces (vlan*), loopback (lo*), and other virtual interfaces
            if (name.startswith('vlan') or 
                name.startswith('lo') or 
                name.startswith('docker') or 
                name.startswith('br-') or 
                name.startswith('bridge') or
                name.startswith('virbr') or
                name.startswith('veth') or
                name.startswith('gif') or
                name.startswith('stf') or
                name.startswith('utun') or
                name.startswith('awdl') or
                name.startswith('llw') or
                name.startswith('anpi')):
                continue
                
            is_up = stats.isup
            # Simulate traffic statistics for demonstration purposes
            tx = random.randint(100, 1000) if is_up else 0  # Transmitted packets
            rx = random.randint(50, 800) if is_up else 0   # Received packets
            sent_bytes = tx * random.randint(64, 1500)  # Simulate bytes sent
            received_bytes = rx * random.randint(64, 1500)  # Simulate bytes received
            errors = random.randint(0, 10) if is_up else 0  # Simulate errors

            interfaces.append({
                "name": name,
                "status": "up" if is_up else "down",
                "mtu": stats.mtu,
                "speed": stats.speed if hasattr(stats, 'speed') else "Unknown",
                "ip_addresses": psutil.net_if_addrs().get(name, []),  # Add IP addresses if available
                "tx": tx,
                "rx": rx,
                "sent_bytes": sent_bytes,
                "received_bytes": received_bytes,
                "errors": errors,
            })
        return jsonify(interfaces)
    except Exception as e:
        logging.error(f"Error fetching interfaces: {e}")
        return jsonify({"error": "Unable to fetch interfaces"}), 500



## Packet Capture CODE

@app.route("/api/capture/start", methods=["POST"])
def start_capture():
    data = request.json
    interface = data.get("interface", "eth0")
    filename = data.get("filename", f"{interface}_{int(time.time())}.pcap")

    # Create 'captures' directory if it doesn't exist
    capture_dir = os.path.join(os.getcwd(), "captures")
    os.makedirs(capture_dir, exist_ok=True)

    filepath = os.path.join(capture_dir, filename)

    if interface in capture_processes:
        return jsonify({"error": "Capture already running"}), 400

    cmd = ["tcpdump", "-i", interface, "-w", filepath]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    capture_processes[interface] = {"proc": proc, "filepath": filepath}
    return jsonify({"message": "Capture started", "filepath": filepath})

@app.route("/api/capture/stop", methods=["POST"])
def stop_capture():
    data = request.json
    interface = data.get("interface")

    entry = capture_processes.pop(interface, None)
    if not entry:
        return jsonify({"error": "No capture running on interface"}), 400

    entry["proc"].terminate()
    return jsonify({"message": "Capture stopped", "filepath": entry["filepath"]})


@app.route("/api/capture/download", methods=["GET"])
def download_capture():
    filepath = request.args.get("filepath")
    if not os.path.isfile(filepath):
        return jsonify({"error": "Capture file not found"}), 404
    return send_file(filepath, as_attachment=True)

@app.route("/api/capture/summary", methods=["GET"])
def capture_summary():
    filepath = request.args.get("filepath")
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "Capture file not found"}), 404

    try:
        packets = rdpcap(filepath)
        total = len(packets)

        protocol_counter = Counter()
        ip_summary = []

        for pkt in packets:
            if pkt.haslayer("IP"):
                src = pkt["IP"].src
                dst = pkt["IP"].dst
                proto = pkt["IP"].proto
                ip_summary.append({"src": src, "dst": dst, "proto": proto})
            elif pkt.haslayer("IPv6"):
                src = pkt["IPv6"].src
                dst = pkt["IPv6"].dst
                proto = pkt["IPv6"].nh
                ip_summary.append({"src": src, "dst": dst, "proto": proto})

            # Count protocol layers
            for layer in pkt.layers():
                protocol_counter[layer.__name__] += 1

        return jsonify({
            "total_packets": total,
            "protocols": dict(protocol_counter),
            "ip_flows": ip_summary[:20]  # Return first 20 flows for preview
        })

    except Exception as e:
        logging.error(f"Error summarizing capture: {e}")
        return jsonify({"error": "Failed to parse pcap file"}), 500


@app.route("/api/pcap/upload", methods=["POST"])
def upload_pcap():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    pcap_dir = os.path.join(os.getcwd(), "uploads", "pcaps")
    os.makedirs(pcap_dir, exist_ok=True)

    filepath = os.path.join(pcap_dir, file.filename)
    file.save(filepath)

    return jsonify({
        "message": "PCAP uploaded",
        "filepath": f"uploads/pcaps/{file.filename}"
    })


@app.route("/health", methods=["GET"])
def healthz():
    return "Online", 200


# ---- Add a /healthz alias (keep your /health route too) ----
@app.get("/healthz")
def healthz_json():
    return jsonify(status="ok"), 200

# ============================================================================
# DEVICE DATABASE API ENDPOINTS
# ============================================================================

@app.route("/api/device/database/info", methods=["GET"])
def get_database_info():
    """Get database information and statistics."""
    try:
        info = device_db.get_database_info()
        return jsonify(info), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to get database info: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/devices", methods=["GET"])
def get_all_devices_from_db():
    """Get all devices from database."""
    try:
        status_filter = request.args.get('status')
        devices = device_db.get_all_devices(status_filter)
        return jsonify({"devices": devices, "count": len(devices)}), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to get all devices: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/devices/<device_id>", methods=["GET"])
def get_device_from_db(device_id):
    """Get a specific device from database."""
    try:
        device = device_db.get_device(device_id)
        if device:
            return jsonify(device), 200
        else:
            return jsonify({"error": "Device not found"}), 404
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to get device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/devices/<device_id>/events", methods=["GET"])
def get_device_events(device_id):
    """Get device events from database."""
    try:
        limit = request.args.get('limit', 100, type=int)
        events = device_db.get_device_events(device_id, limit)
        return jsonify({"events": events, "count": len(events)}), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to get events for device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/devices/<device_id>/statistics", methods=["GET"])
def get_device_statistics(device_id):
    """Get device statistics from database."""
    try:
        hours = request.args.get('hours', 24, type=int)
        stats = device_db.get_device_statistics(device_id, hours)
        return jsonify({"statistics": stats, "count": len(stats)}), 200
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to get statistics for device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/backup", methods=["POST"])
def backup_database():
    """Create a backup of the database."""
    try:
        success = device_db.backup_database()
        if success:
            return jsonify({"status": "success", "message": "Database backed up successfully"}), 200
        else:
            return jsonify({"error": "Failed to backup database"}), 500
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to backup database: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/restore", methods=["POST"])
def restore_database():
    """Restore database from backup."""
    try:
        success = device_db.restore_database()
        if success:
            return jsonify({"status": "success", "message": "Database restored successfully"}), 200
        else:
            return jsonify({"error": "Failed to restore database"}), 500
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to restore database: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/database/cleanup", methods=["POST"])
def cleanup_database():
    """Clean up old data from database."""
    try:
        data = request.get_json() or {}
        days = data.get('days', 30)
        success = device_db.cleanup_old_data(days)
        if success:
            return jsonify({"status": "success", "message": f"Cleaned up data older than {days} days"}), 200
        else:
            return jsonify({"error": "Failed to cleanup database"}), 500
    except Exception as e:
        logging.error(f"[DEVICE DB] Failed to cleanup database: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/monitor/start", methods=["POST"])
def start_bgp_monitoring():
    """Start BGP status monitoring."""
    try:
        bgp_monitor.start_monitoring()
        return jsonify({"status": "success", "message": "BGP monitoring started"}), 200
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to start monitoring: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/monitor/stop", methods=["POST"])
def stop_bgp_monitoring():
    """Stop BGP status monitoring."""
    try:
        bgp_monitor.stop_monitoring()
        return jsonify({"status": "success", "message": "BGP monitoring stopped"}), 200
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to stop monitoring: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/monitor/status", methods=["GET"])
def get_bgp_monitor_status():
    """Get BGP monitoring status."""
    try:
        status = bgp_monitor.get_status()
        return jsonify({"status": "success", "monitor_status": status}), 200
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to get monitor status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/monitor/force-check", methods=["POST"])
def force_bgp_check():
    """Force an immediate BGP status check for all devices."""
    try:
        bgp_monitor.force_check()
        return jsonify({"status": "success", "message": "BGP status check initiated"}), 200
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to force BGP check: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/monitor/config", methods=["POST"])
def update_bgp_monitor_config():
    """Update BGP monitoring configuration."""
    try:
        data = request.get_json() or {}
        interval = data.get('check_interval')
        
        if interval and isinstance(interval, int) and interval > 0:
            bgp_monitor.update_check_interval(interval)
            return jsonify({"status": "success", "message": f"Check interval updated to {interval} seconds"}), 200
        else:
            return jsonify({"error": "Invalid check_interval value"}), 400
            
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to update config: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/monitor/start", methods=["POST"])
def start_ospf_monitoring():
    """Start OSPF status monitoring."""
    try:
        ospf_monitor.start_monitoring()
        return jsonify({"status": "success", "message": "OSPF monitoring started"}), 200
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to start monitoring: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ospf/monitor/stop", methods=["POST"])
def stop_ospf_monitoring():
    """Stop OSPF status monitoring."""
    try:
        ospf_monitor.stop_monitoring()
        return jsonify({"status": "success", "message": "OSPF monitoring stopped"}), 200
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to stop monitoring: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ospf/monitor/status", methods=["GET"])
def get_ospf_monitor_status():
    """Get OSPF monitoring status."""
    try:
        status = ospf_monitor.get_status()
        return jsonify({"status": "success", "monitor_status": status}), 200
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to get monitor status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ospf/monitor/force-check", methods=["POST"])
def force_ospf_check():
    """Force an immediate OSPF status check for all devices."""
    try:
        ospf_monitor.force_check()
        return jsonify({"status": "success", "message": "OSPF status check initiated"}), 200
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to force OSPF check: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ospf/monitor/config", methods=["POST"])
def update_ospf_monitor_config():
    """Update OSPF monitoring configuration."""
    try:
        data = request.get_json() or {}
        interval = data.get('check_interval')
        
        if interval and isinstance(interval, int) and interval > 0:
            ospf_monitor.update_check_interval(interval)
            return jsonify({"status": "success", "message": f"Check interval updated to {interval} seconds"}), 200
        else:
            return jsonify({"error": "Invalid check_interval value"}), 400
            
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to update monitor config: {e}")
        return jsonify({"error": str(e)}), 500

# ===== OSPF ROUTE POOL MANAGEMENT =====

@app.route("/api/ospf/pools", methods=["GET"])
def get_ospf_route_pools():
    """Get all OSPF route pools."""
    try:
        pools = device_db.get_all_route_pools()
        
        # Convert database format to API format
        api_pools = []
        for pool in pools:
            api_pool = {
                "name": pool["pool_name"],
                "subnet": pool["subnet"],
                "count": pool["route_count"],
                "first_host": pool["first_host_ip"],
                "last_host": pool["last_host_ip"],
                "increment_type": pool.get("increment_type", "host"),
                "created_at": pool["created_at"],
                "updated_at": pool["updated_at"]
            }
            api_pools.append(api_pool)
        
        return jsonify({"pools": api_pools}), 200
        
    except Exception as e:
        logging.error(f"[OSPF POOLS] Error getting route pools: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/pools", methods=["POST"])
def create_ospf_route_pool():
    """Create a new OSPF route pool."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing pool data"}), 400
        
        pool_name = data.get("name")
        subnet = data.get("subnet")
        count = data.get("count")
        increment_type = data.get("increment_type", "host")
        
        if not all([pool_name, subnet, count]):
            return jsonify({"error": "Missing required fields: name, subnet, count"}), 400
        
        # Validate subnet
        is_valid, result, address_family = validate_subnet(subnet)
        if not is_valid:
            return jsonify({"error": f"Invalid subnet: {result}"}), 400
        
        # Generate host IPs
        try:
            import ipaddress
            network = ipaddress.ip_network(subnet, strict=False)
            
            if increment_type == "network":
                # Generate network routes
                generated_routes = generate_network_routes_from_pool(network, count)
                first_host = generated_routes[0] if generated_routes else subnet
                last_host = generated_routes[-1] if generated_routes else subnet
            else:
                # Generate host routes
                generated_routes = generate_host_routes_from_pool(network, count)
                first_host = generated_routes[0] if generated_routes else subnet
                last_host = generated_routes[-1] if generated_routes else subnet
            
        except Exception as e:
            return jsonify({"error": f"Error generating routes: {str(e)}"}), 400
        
        # Create pool in database
        pool_info = {
            "pool_name": pool_name,
            "subnet": subnet,
            "route_count": count,
            "first_host_ip": first_host,
            "last_host_ip": last_host,
            "increment_type": increment_type
        }
        
        success = device_db.add_route_pool(pool_info)
        if success:
            return jsonify({"message": "Route pool created successfully", "pool": pool_info}), 201
        else:
            return jsonify({"error": "Failed to create route pool"}), 500
            
    except Exception as e:
        logging.error(f"[OSPF POOLS] Error creating route pool: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/pools/<pool_name>", methods=["PUT"])
def update_ospf_route_pool(pool_name):
    """Update an existing OSPF route pool."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing pool data"}), 400
        
        # Get existing pool
        existing_pool = device_db.get_route_pool(pool_name)
        if not existing_pool:
            return jsonify({"error": "Route pool not found"}), 404
        
        # Update fields
        update_data = {}
        if "subnet" in data:
            subnet = data["subnet"]
            is_valid, result, address_family = validate_subnet(subnet)
            if not is_valid:
                return jsonify({"error": f"Invalid subnet: {result}"}), 400
            update_data["subnet"] = subnet
        
        if "count" in data:
            update_data["route_count"] = data["count"]
        
        if "increment_type" in data:
            update_data["increment_type"] = data["increment_type"]
        
        # Regenerate host IPs if subnet or count changed
        if "subnet" in update_data or "count" in update_data:
            try:
                import ipaddress
                subnet = update_data.get("subnet", existing_pool["subnet"])
                count = update_data.get("route_count", existing_pool["route_count"])
                increment_type = update_data.get("increment_type", existing_pool.get("increment_type", "host"))
                
                network = ipaddress.ip_network(subnet, strict=False)
                
                if increment_type == "network":
                    generated_routes = generate_network_routes_from_pool(network, count)
                else:
                    generated_routes = generate_host_routes_from_pool(network, count)
                
                update_data["first_host_ip"] = generated_routes[0] if generated_routes else subnet
                update_data["last_host_ip"] = generated_routes[-1] if generated_routes else subnet
                
            except Exception as e:
                return jsonify({"error": f"Error regenerating routes: {str(e)}"}), 400
        
        # Update pool in database
        success = device_db.update_route_pool(pool_name, update_data)
        if success:
            return jsonify({"message": "Route pool updated successfully"}), 200
        else:
            return jsonify({"error": "Failed to update route pool"}), 500
            
    except Exception as e:
        logging.error(f"[OSPF POOLS] Error updating route pool: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ospf/pools/<pool_name>", methods=["DELETE"])
def delete_ospf_route_pool(pool_name):
    """Delete an OSPF route pool."""
    try:
        success = device_db.delete_route_pool(pool_name)
        if success:
            return jsonify({"message": "Route pool deleted successfully"}), 200
        else:
            return jsonify({"error": "Route pool not found"}), 404
            
    except Exception as e:
        logging.error(f"[OSPF POOLS] Error deleting route pool: {e}")
        return jsonify({"error": str(e)}), 500


# ===== ARP MONITORING ENDPOINTS =====

@app.route("/api/arp/monitor/start", methods=["POST"])
def start_arp_monitor():
    """Start ARP status monitoring."""
    try:
        arp_monitor.start()
        return jsonify({"status": "success", "message": "ARP monitoring started"}), 200
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to start: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/arp/monitor/stop", methods=["POST"])
def stop_arp_monitor():
    """Stop ARP status monitoring."""
    try:
        arp_monitor.stop()
        return jsonify({"status": "success", "message": "ARP monitoring stopped"}), 200
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to stop: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/arp/monitor/status", methods=["GET"])
def get_arp_monitor_status():
    """Get ARP monitoring status."""
    try:
        status = arp_monitor.get_status()
        return jsonify({"status": "success", "data": status}), 200
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to get status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/arp/monitor/force-check", methods=["POST"])
def force_arp_check():
    """Force an immediate ARP status check for all devices."""
    try:
        arp_monitor.force_check_all()
        return jsonify({"status": "success", "message": "Force ARP check initiated"}), 200
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to force check: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/arp/monitor/config", methods=["POST"])
def update_arp_monitor_config():
    """Update ARP monitoring configuration."""
    try:
        data = request.get_json()
        interval = data.get("check_interval")
        
        if interval and isinstance(interval, int) and interval > 0:
            arp_monitor.check_interval = interval
            return jsonify({"status": "success", "message": f"Check interval updated to {interval} seconds"}), 200
        else:
            return jsonify({"error": "Invalid check_interval value"}), 400
            
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to update config: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/device/arp/<device_id>", methods=["GET"])
def get_device_arp_status(device_id):
    """Get ARP status for a specific device."""
    try:
        # Get device information from database
        device = device_db.get_device(device_id)
        if not device:
            return jsonify({"error": "Device not found"}), 404
        
        # Check if device is running
        if device.get('status') != 'Running':
            return jsonify({
                "arp_resolved": False,
                "arp_ipv4_resolved": False,
                "arp_ipv6_resolved": False,
                "arp_gateway_resolved": False,
                "arp_status": "Device not running",
                "details": {"error": "Device is not running"}
            }), 200
        
        # Check if the network interface is actually up
        server_interface = device.get('server_interface')
        if server_interface:
            try:
                import subprocess
                # Check interface status using ip link show
                result = subprocess.run(["ip", "link", "show", server_interface], 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Check if interface is UP
                    if "state DOWN" in result.stdout:
                        return jsonify({
                            "arp_resolved": False,
                            "arp_ipv4_resolved": False,
                            "arp_ipv6_resolved": False,
                            "arp_gateway_resolved": False,
                            "arp_status": "Interface down",
                            "details": {"error": f"Interface {server_interface} is down"}
                        }), 200
                else:
                    # Interface doesn't exist
                    return jsonify({
                        "arp_resolved": False,
                        "arp_ipv4_resolved": False,
                        "arp_ipv6_resolved": False,
                        "arp_gateway_resolved": False,
                        "arp_status": "Interface not found",
                        "details": {"error": f"Interface {server_interface} not found"}
                    }), 200
            except Exception as e:
                logging.warning(f"[ARP STATUS] Failed to check interface status for {server_interface}: {e}")
                # Continue with ARP checks even if interface check fails
        
        # Get device IP addresses
        ipv4_address = device.get('ipv4_address')
        ipv6_address = device.get('ipv6_address')
        ipv4_gateway = device.get('ipv4_gateway')
        ipv6_gateway = device.get('ipv6_gateway')
        
        
        # Perform ARP checks
        arp_results = {
            "arp_ipv4_resolved": False,
            "arp_ipv6_resolved": False,
            "arp_gateway_resolved": False,
            "details": {}
        }
        
        # Check IPv4 ARP
        if ipv4_address:
            try:
                import subprocess
                result = subprocess.run(["ping", "-c", "1", "-W", "1", ipv4_address], 
                                      capture_output=True, text=True, timeout=5)
                arp_results["arp_ipv4_resolved"] = result.returncode == 0
                arp_results["details"]["ipv4_ping"] = "success" if result.returncode == 0 else "failed"
            except Exception as e:
                arp_results["details"]["ipv4_ping"] = f"error: {e}"
        
        # Check IPv6 NDP
        if ipv6_address:
            try:
                import subprocess
                result = subprocess.run(["ping6", "-c", "1", "-W", "1", ipv6_address], 
                                      capture_output=True, text=True, timeout=5)
                arp_results["arp_ipv6_resolved"] = result.returncode == 0
                arp_results["details"]["ipv6_ping"] = "success" if result.returncode == 0 else "failed"
            except Exception as e:
                arp_results["details"]["ipv6_ping"] = f"error: {e}"
        
        # Check gateway connectivity
        if ipv4_gateway:
            try:
                import subprocess
                result = subprocess.run(["ping", "-c", "1", "-W", "1", ipv4_gateway], 
                                      capture_output=True, text=True, timeout=5)
                arp_results["arp_gateway_resolved"] = result.returncode == 0
                arp_results["details"]["gateway_ping"] = "success" if result.returncode == 0 else "failed"
            except Exception as e:
                arp_results["details"]["gateway_ping"] = f"error: {e}"
        
        # Determine overall ARP status
        arp_results["arp_resolved"] = (arp_results["arp_ipv4_resolved"] or 
                                      arp_results["arp_ipv6_resolved"] or 
                                      arp_results["arp_gateway_resolved"])
        
        if arp_results["arp_resolved"]:
            arp_results["arp_status"] = "Resolved"
        else:
            arp_results["arp_status"] = "Failed"
        
        return jsonify(arp_results), 200
        
    except Exception as e:
        logging.error(f"[ARP STATUS] Failed to get ARP status for device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# BGP Route Pool Management API Endpoints
# ============================================================================

@app.route("/api/bgp/pools", methods=["GET"])
def get_bgp_route_pools():
    """Get all BGP route pools from the database."""
    try:
        pools = device_db.get_all_route_pools()
        
        # Convert database format to API format
        api_pools = []
        for pool in pools:
            api_pools.append({
                "name": pool["pool_name"],
                "subnet": pool["subnet"],
                "address_family": pool.get("address_family", "ipv4"),
                "count": pool["route_count"],
                "first_host": pool["first_host_ip"],
                "last_host": pool["last_host_ip"],
                "increment_type": pool.get("increment_type", "host"),
                "created_at": pool["created_at"],
                "updated_at": pool["updated_at"]
            })
        
        return jsonify({
            "pools": api_pools,
            "count": len(api_pools)
        }), 200
        
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to get route pools: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools", methods=["POST"])
def create_bgp_route_pool():
    """Create a new BGP route pool in the database."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        # Validate required fields
        required_fields = ["name", "subnet", "count"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Validate subnet format (IPv4 or IPv6)
        subnet = data["subnet"]
        is_valid, result, address_family = validate_subnet(subnet)
        if not is_valid:
            return jsonify({"error": f"Invalid subnet format: {result}"}), 400
        
        # Prepare pool data for database
        pool_data = {
            "name": data["name"],
            "subnet": data["subnet"],
            "route_count": data["count"],
            "first_host_ip": data.get("first_host", ""),
            "last_host_ip": data.get("last_host", ""),
            "increment_type": data.get("increment_type", "host")
        }
        
        # Save to database
        success = device_db.add_route_pool(pool_data)
        
        if success:
            return jsonify({
                "message": f"Route pool '{data['name']}' created successfully",
                "pool": pool_data
            }), 201
        else:
            return jsonify({"error": "Failed to create route pool"}), 500
            
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to create route pool: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools/<pool_name>", methods=["GET"])
def get_bgp_route_pool(pool_name):
    """Get a specific BGP route pool by name."""
    try:
        pool = device_db.get_route_pool(pool_name)
        
        if not pool:
            return jsonify({"error": f"Route pool '{pool_name}' not found"}), 404
        
        # Convert database format to API format
        api_pool = {
            "name": pool["pool_name"],
            "subnet": pool["subnet"],
            "address_family": pool.get("address_family", "ipv4"),
            "count": pool["route_count"],
            "first_host": pool["first_host_ip"],
            "last_host": pool["last_host_ip"],
            "created_at": pool["created_at"],
            "updated_at": pool["updated_at"]
        }
        
        return jsonify({"pool": api_pool}), 200
        
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to get route pool '{pool_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools/<pool_name>", methods=["PUT"])
def update_bgp_route_pool(pool_name):
    """Update an existing BGP route pool."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        # Validate subnet if provided
        if "subnet" in data:
            subnet = data["subnet"]
            is_valid, result, address_family = validate_subnet(subnet)
            if not is_valid:
                return jsonify({"error": f"Invalid subnet format: {result}"}), 400
        
        # Prepare update data
        update_data = {}
        if "subnet" in data:
            update_data["subnet"] = data["subnet"]
        if "count" in data:
            update_data["route_count"] = data["count"]
        if "first_host" in data:
            update_data["first_host_ip"] = data["first_host"]
        if "last_host" in data:
            update_data["last_host_ip"] = data["last_host"]
        if "increment_type" in data:
            update_data["increment_type"] = data["increment_type"]
        
        if not update_data:
            return jsonify({"error": "No fields to update"}), 400
        
        # Update in database
        success = device_db.update_route_pool(pool_name, update_data)
        
        if success:
            return jsonify({
                "message": f"Route pool '{pool_name}' updated successfully",
                "updated_fields": list(update_data.keys())
            }), 200
        else:
            return jsonify({"error": "Failed to update route pool"}), 500
            
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to update route pool '{pool_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools/<pool_name>", methods=["DELETE"])
def delete_bgp_route_pool(pool_name):
    """Delete a BGP route pool from the database."""
    try:
        success = device_db.remove_route_pool(pool_name)
        
        if success:
            return jsonify({
                "message": f"Route pool '{pool_name}' deleted successfully"
            }), 200
        else:
            return jsonify({"error": "Failed to delete route pool"}), 500
            
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to delete route pool '{pool_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools/batch", methods=["POST"])
def save_bgp_route_pools_batch():
    """Save multiple BGP route pools in a batch operation."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        pools_data = data.get("pools", [])
        if not pools_data:
            return jsonify({"error": "No pools provided"}), 400
        
        # Validate and prepare pool data
        validated_pools = []
        for pool in pools_data:
            if not all(field in pool for field in ["name", "subnet", "count"]):
                return jsonify({"error": "Each pool must have 'name', 'subnet', and 'count' fields"}), 400
            
            validated_pools.append({
                "name": pool["name"],
                "subnet": pool["subnet"],
                "route_count": pool["count"],
                "first_host_ip": pool.get("first_host", ""),
                "last_host_ip": pool.get("last_host", "")
            })
        
        # Save to database
        success = device_db.save_route_pools_batch(validated_pools)
        
        if success:
            return jsonify({
                "message": f"Successfully saved {len(validated_pools)} route pools",
                "pools_saved": len(validated_pools)
            }), 201
        else:
            return jsonify({"error": "Failed to save some or all route pools"}), 500
            
    except Exception as e:
        logging.error(f"[BGP POOLS API] Failed to save route pools batch: {e}")
        return jsonify({"error": str(e)}), 500


# Device-Pool Relationship Management API Endpoints

@app.route("/api/device/<device_id>/route-pools", methods=["GET"])
def get_device_route_pools(device_id):
    """Get route pools attached to a specific device."""
    try:
        pools_by_neighbor = device_db.get_device_route_pools(device_id)
        
        return jsonify({
            "device_id": device_id,
            "route_pools": pools_by_neighbor,
            "neighbor_count": len(pools_by_neighbor)
        }), 200
        
    except Exception as e:
        logging.error(f"[DEVICE POOLS API] Failed to get route pools for device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/<device_id>/route-pools", methods=["POST"])
def attach_route_pools_to_device(device_id):
    """Attach route pools to a device for a specific neighbor."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        neighbor_ip = data.get("neighbor_ip")
        pool_names = data.get("pool_names", [])
        
        if not neighbor_ip:
            return jsonify({"error": "Missing required field: neighbor_ip"}), 400
        
        if not pool_names:
            return jsonify({"error": "No pool names provided"}), 400
        
        # Validate that all pools exist
        for pool_name in pool_names:
            pool = device_db.get_route_pool(pool_name)
            if not pool:
                return jsonify({"error": f"Route pool '{pool_name}' not found"}), 404
        
        # Attach pools to device
        success = device_db.attach_route_pools_to_device(device_id, neighbor_ip, pool_names)
        
        if success:
            return jsonify({
                "message": f"Successfully attached {len(pool_names)} route pools to device {device_id} for neighbor {neighbor_ip}",
                "device_id": device_id,
                "neighbor_ip": neighbor_ip,
                "attached_pools": pool_names
            }), 201
        else:
            return jsonify({"error": "Failed to attach route pools to device"}), 500
            
    except Exception as e:
        logging.error(f"[DEVICE POOLS API] Failed to attach route pools to device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/<device_id>/route-pools", methods=["DELETE"])
def remove_device_route_pools(device_id):
    """Remove route pool attachments from a device."""
    try:
        data = request.get_json() or {}
        neighbor_ip = data.get("neighbor_ip")  # Optional
        
        success = device_db.remove_device_route_pools(device_id, neighbor_ip)
        
        if success:
            if neighbor_ip:
                message = f"Successfully removed route pool attachments for device {device_id} and neighbor {neighbor_ip}"
            else:
                message = f"Successfully removed all route pool attachments for device {device_id}"
            
            return jsonify({
                "message": message,
                "device_id": device_id,
                "neighbor_ip": neighbor_ip
            }), 200
        else:
            return jsonify({"error": "Failed to remove route pool attachments"}), 500
            
    except Exception as e:
        logging.error(f"[DEVICE POOLS API] Failed to remove route pool attachments for device {device_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/bgp/pools/<pool_name>/usage", methods=["GET"])
def get_pool_usage(pool_name):
    """Get devices and neighbors using a specific route pool."""
    try:
        usage = device_db.get_pool_usage(pool_name)
        
        return jsonify({
            "pool_name": pool_name,
            "usage": usage,
            "device_count": len(set(item['device_id'] for item in usage)),
            "neighbor_count": len(set(item['neighbor_ip'] for item in usage))
        }), 200
        
    except Exception as e:
        logging.error(f"[POOL USAGE API] Failed to get usage for pool {pool_name}: {e}")
        return jsonify({"error": str(e)}), 500


# ---- Explicit entry point used by 'ostg-server' ----
def main(argv=None):
    import argparse, os
    # Enable DEBUG logging when OSTG_DEBUG=1 is set
    try:
        debug_flag = os.environ.get("OSTG_DEBUG", "0").strip()
        if debug_flag in ("1", "true", "True", "yes", "on"):
            logging.getLogger().setLevel(logging.DEBUG)
            logging.getLogger('werkzeug').setLevel(logging.DEBUG)
            logging.debug("[DEBUG] OSTG_DEBUG enabled: setting logging level to DEBUG")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="ostg-server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5051")))
    args = parser.parse_args(argv)
    
    # Start BGP monitoring
    try:
        bgp_monitor.start_monitoring()
        logging.info("[BGP MONITOR] BGP status monitoring started")
    except Exception as e:
        logging.error(f"[BGP MONITOR] Failed to start BGP monitoring: {e}")
    
    # Start OSPF monitoring
    try:
        ospf_monitor.start_monitoring()
        logging.info("[OSPF MONITOR] OSPF status monitoring started")
    except Exception as e:
        logging.error(f"[OSPF MONITOR] Failed to start OSPF monitoring: {e}")
    
    # Start ISIS monitoring
    try:
        isis_monitor.start_monitoring()
        logging.info("[ISIS MONITOR] ISIS status monitoring started")
    except Exception as e:
        logging.error(f"[ISIS MONITOR] Failed to start ISIS monitoring: {e}")
    
    # Start ARP monitoring
    try:
        arp_monitor.start()
        logging.info("[ARP MONITOR] ARP status monitoring started")
    except Exception as e:
        logging.error(f"[ARP MONITOR] Failed to start ARP monitoring: {e}")
    
    app.run(host=args.host, port=args.port)



if __name__ == '__main__':
    #app.run(host='0.0.0.0', port=8501, debug=True)
    main()