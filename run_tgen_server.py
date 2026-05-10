## here is the server side code for a traffic generator app ##
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from scapy.all import Ether, Dot1Q, IP, IPv6, TCP, UDP, ICMP, ARP, Raw, sendp, wrpcap, sendpfast, rdpcap, sniff
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import threading
import logging

logger = logging.getLogger(__name__)
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
from typing import Any, Dict, List, Optional, Tuple, Union
from multithreaded_traffic_gen import generate_packets, on_stream_stopped, stream_tracker, start_rx_counter
from utils.device_manager import DeviceManager
from utils.helpers import increment_ip, increment_ipv6, increment_mac, is_interface_up
from utils.device_database import DeviceDatabase
from utils.stream_database import StreamDatabase
from utils.bgp_monitor import BGPStatusManager
from utils.arp_monitor import ARPStatusMonitor
from utils.dhcp import ensure_dhcp_services, stop_dhcp_services
from utils import vxlan as vxlan_utils


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

# Initialize stream database
stream_db = StreamDatabase()

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

# Initialize DHCP client monitor
from utils.dhcp_monitor import DHCPClientMonitor
dhcp_client_monitor = DHCPClientMonitor(device_db)

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
    """Get stream statistics from database (preferred) or stream_tracker (fallback)."""
    try:
        # Get from database
        tg_id = request.args.get("tg_id", type=int)
        status = request.args.get("status", "Running")
        
        # If status is "all", don't filter by status
        # Default to "Running" but also include recently stopped streams (within last 5 minutes)
        # This ensures streams are visible immediately after server restart
        status_filter = None if status and status.lower() == "all" else status
        streams = stream_db.get_all_streams(status=status_filter, tg_id=tg_id)
        
        # If filtering by "Running" and no streams found, also check for recently stopped streams
        # This helps show streams that were stopped due to server restart
        if status_filter == "Running" and not streams:
            import time
            from datetime import datetime, timezone
            recent_streams = stream_db.get_all_streams(status="Stopped", tg_id=tg_id)
            # Filter to streams stopped within last 5 minutes (likely due to restart)
            current_time = time.time()
            recent_streams = []
            for s in stream_db.get_all_streams(status="Stopped", tg_id=tg_id):
                updated_at = s.get("updated_at")
                if updated_at:
                    try:
                        # Convert ISO timestamp string to Unix timestamp
                        if isinstance(updated_at, str):
                            # Parse ISO format timestamp
                            dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                            updated_timestamp = dt.timestamp()
                        elif isinstance(updated_at, (int, float)):
                            # Already a Unix timestamp
                            updated_timestamp = float(updated_at)
                        else:
                            continue
                        
                        # Check if within last 5 minutes
                        if (current_time - updated_timestamp) < 300:
                            recent_streams.append(s)
                    except (ValueError, TypeError, AttributeError) as e:
                        logging.debug(f"[STATS] Error parsing updated_at for stream {s.get('stream_id')}: {e}")
                        continue
            if recent_streams:
                logging.info(f"[STATS] No running streams found, but found {len(recent_streams)} recently stopped stream(s) (likely from server restart)")
                # Return recently stopped streams with status="Stopped" so client can display them
                streams = recent_streams
        
        # Get actually running streams from stream_tracker to verify database status
        try:
            active_tracker_streams = stream_tracker.get_stream_stats()
            # Create a set of (interface, stream_id) tuples for quick lookup
            tracker_stream_keys = set()
            for ts in active_tracker_streams:
                iface = ts.get("interface")
                sid = ts.get("stream_id")
                if iface and sid:
                    tracker_stream_keys.add((iface, sid))
        except Exception as e:
            logging.debug(f"[STATS] Error getting stream_tracker stats: {e}")
            tracker_stream_keys = set()
        
        # Convert to format expected by client
        active_streams = []
        for stream in streams:
            # Extract frame_size from the persisted stream config. The DB
            # column is `stream_config` (JSON-decoded to a dict by
            # stream_database.get_all_streams). Older code in this handler
            # read `stream_data`, which doesn't exist on the row — so all
            # field lookups silently failed, frame_size fell through to
            # the 64-byte default, and the client's bps calc was
            # frame_size/64 = ~24x too low at 1500B. Accept either key
            # for forward-compat (in case anything else writes
            # 'stream_data').
            stream_data = stream.get("stream_config") or stream.get("stream_data") or {}
            if isinstance(stream_data, str):
                # Defensive — get_all_streams should already json.loads,
                # but tolerate a raw JSON string just in case.
                try:
                    import json as _json
                    stream_data = _json.loads(stream_data)
                except Exception:
                    stream_data = {}
            if not isinstance(stream_data, dict):
                stream_data = {}
            protocol_selection = stream_data.get("protocol_selection", {}) or {}
            frame_size = (protocol_selection.get("frame_size") or 
                         stream_data.get("frame_size") or 
                         stream.get("frame_size") or 
                         64)
            try:
                frame_size = int(frame_size)
            except (ValueError, TypeError):
                frame_size = 64

            # Extract DPDK engine info so the client can show what's actually
            # running (single-queue scapy/kernel vs multi-queue tx_worker).
            dpdk_enable = bool(
                stream_data.get("dpdk_enable")
                or protocol_selection.get("dpdk_enable")
                or str(stream_data.get("engine") or "").strip().lower() == "dpdk"
            )
            try:
                dpdk_tx_cores = int(stream_data.get("dpdk_tx_cores") or 1)
                if dpdk_tx_cores < 1:
                    dpdk_tx_cores = 1
            except (ValueError, TypeError):
                dpdk_tx_cores = 1

            # Verify stream is actually running in stream_tracker
            stream_id = stream.get("stream_id")
            interface = stream.get("interface")
            db_status = stream.get("status", "Unknown")
            
            # Check if stream is actually running in stream_tracker
            is_actually_running = False
            if interface and stream_id:
                is_actually_running = (interface, stream_id) in tracker_stream_keys
            
            # If database says "Running" but stream_tracker doesn't have it, mark as "Stopped"
            if db_status == "Running" and not is_actually_running:
                logging.warning(f"[STATS] Stream '{stream.get('stream_name')}' (id={stream_id}) on {interface} marked as 'Running' in database but not in stream_tracker - correcting to 'Stopped'")
                actual_status = "Stopped"
                # Also zero out rates since stream is not actually running
                tx_rate = 0.0
                rx_rate = 0.0
            elif db_status == "Running" and is_actually_running:
                # Stream is actually running - use database rates (they're updated by background thread)
                actual_status = "Running"
                tx_rate = stream.get("tx_rate", 0.0)
                rx_rate = stream.get("rx_rate", 0.0)
            else:
                # Stream is already marked as "Stopped" in database, or status is unknown
                actual_status = db_status
                # Zero out rates for stopped streams (they shouldn't have active rates)
                if actual_status == "Stopped":
                    tx_rate = 0.0
                    rx_rate = 0.0
                else:
                    tx_rate = stream.get("tx_rate", 0.0)
                    rx_rate = stream.get("rx_rate", 0.0)
            
            active_streams.append({
                "stream_id": stream_id,
                "interface": interface,
                "stream_name": stream.get("stream_name"),
                "rx_interface": stream.get("rx_interface"),
                "tx_count": stream.get("tx_count", 0),
                "rx_count": stream.get("rx_count", 0),
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
                "flow_tracking_enabled": bool(stream.get("flow_tracking_enabled", False)),
                "status": actual_status,  # Use verified status
                "tg_id": stream.get("tg_id"),
                "started_at": stream.get("started_at"),
                "updated_at": stream.get("updated_at"),
                "frame_size": frame_size,  # Add frame_size for accurate byte calculations
                # Engine surface (so the client can render a multi-queue badge)
                "dpdk_enable": dpdk_enable,
                "dpdk_tx_cores": dpdk_tx_cores,
                # Add internal fields for debugging
                "last_tx_count": stream.get("last_tx_count"),
                "last_rx_count": stream.get("last_rx_count"),
                "last_update": stream.get("last_update")
            })
        
        return jsonify({"active_streams": active_streams}), 200
    except Exception as e:
        logging.error(f"[STATS] Error getting stream statistics: {e}")
        # Fallback to stream_tracker
        stats = stream_tracker.get_stream_stats()
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

        # 🛑 Stop previous stream(s)
        # First try by stream_id (most reliable)
        existing = stream_tracker.find_stream_by_id(interface, stream_id)
        if existing:
            logging.info(f"Stopping stream {stream_id} on {interface} (found by stream_id)")
            existing["stop_event"].set()
            
            # Wait for the thread to actually finish (with timeout)
            future = existing.get("future")
            if future:
                logging.info(f"⏳ Waiting for thread to finish for stream {stream_id}...")
                try:
                    future.result(timeout=5.0)
                    logging.info(f"✅ Thread completed for stream {stream_id}")
                except Exception as e:
                    logging.warning(f"⚠️ Thread for stream {stream_id} did not complete within timeout: {e}")
            
            # Wait a brief moment for RX sniffer cleanup if flow tracking was enabled
            if existing.get("flow_tracking_enabled") and existing.get("rx_interface") != interface:
                import time
                time.sleep(0.5)  # Brief delay to allow sniffer cleanup thread to unregister
            
            stream_tracker.remove_stream_by_id(interface, stream_id)
        else:
            # Fallback: try to find by name (in case stream_id changed or doesn't match)
            logging.warning(f"Stream {stream_id} not found on {interface}, trying to find by name '{stream_name}'")
            streams_by_name = stream_tracker.find_streams_by_name(interface, stream_name)
            if streams_by_name:
                logging.info(f"Found {len(streams_by_name)} stream(s) with name '{stream_name}' on {interface}, stopping all")
                for s in streams_by_name:
                    actual_stream_id = s.get("stream_id")
                    logging.info(f"Stopping stream {actual_stream_id} (name: '{stream_name}') on {interface}")
                    s["stop_event"].set()
                    
                    # Wait for the thread to actually finish (with timeout)
                    future = s.get("future")
                    if future:
                        try:
                            future.result(timeout=5.0)
                            logging.info(f"✅ Thread completed for stream {actual_stream_id}")
                        except Exception as e:
                            logging.warning(f"⚠️ Thread for stream {actual_stream_id} did not complete within timeout: {e}")
                    
                    # Wait for RX sniffer cleanup if needed
                    if s.get("flow_tracking_enabled") and s.get("rx_interface") != interface:
                        import time
                        time.sleep(0.5)
                    
                    # Remove each stream
                    stream_tracker.remove_stream_by_id(interface, actual_stream_id)
                
                # Wait a bit longer to ensure threads have stopped
                import time
                time.sleep(0.5)
            else:
                logging.warning(f"No stream found with stream_id {stream_id} or name '{stream_name}' on {interface}")

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

    # Normalize rx_port - handle "Same as TX Port" and various formats
    rx_port = stream_data.get("rx_port") or interface
    if isinstance(rx_port, str):
        # Handle "Same as TX Port" - use TX interface
        if "Same as TX Port" in rx_port or rx_port.strip() == "Same as TX Port":
            rx_interface = interface
        else:
            # Extract interface name from various formats: "TG X - Port: interface", "Port: interface", "interface"
            rx_interface = str(rx_port).split("Port:")[-1].strip()
            if not rx_interface or rx_interface == rx_port:
                # If no "Port:" found, use the whole string as interface name
                rx_interface = str(rx_port).strip()
    else:
        rx_interface = interface
    
    stream_data["rx_interface"] = rx_interface

    flow_tracking = stream_data.get("flow_tracking_enabled", False)
    rx_thread = None

    # Don't start RX sniffer here - it will be started in generate_packets() with proper selector
    # This prevents duplicate sniffers and ensures proper packet matching
    if flow_tracking and rx_interface != interface:
        if not is_interface_up(rx_interface):
            logging.warning(f"⚠️ RX interface '{rx_interface}' is DOWN, flow tracking may not work.")
    elif flow_tracking:
        logging.info(f"🔇 Flow tracking disabled: RX interface equals TX ('{rx_interface}')")

    # Submit the stream generation task and track the Future
    future = executor.submit(generate_packets, stream_data, interface, stop_event)
    
    # Extract frame_size from stream_data for tracking
    protocol_selection = stream_data.get("protocol_selection", {}) or {}
    frame_size = (protocol_selection.get("frame_size") or 
                 stream_data.get("frame_size") or 
                 64)
    try:
        frame_size = int(frame_size)
    except (ValueError, TypeError):
        frame_size = 64
    
    stream_tracker.add_stream({
        "interface": interface,
        "stream_name": stream_name,
        "stream_id": stream_id,
        "stop_event": stop_event,
        "rx_thread": rx_thread,
        "rx_interface": rx_interface,
        "flow_tracking_enabled": flow_tracking,
        "future": future,  # Track the Future so we can wait for thread completion
        "frame_size": frame_size  # Store frame_size for statistics
    })

    try:
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

    # Debug: Log what streams were received
    for interface_label, stream_list in streams.items():
        stream_names = [s.get("name") or s.get("protocol_selection", {}).get("name", "Unknown") for s in stream_list]
        logging.info(f"[DEBUG START] Received {len(stream_list)} stream(s) for interface '{interface_label}': {stream_names}")

    started_streams = []

    for interface_label, stream_list in streams.items():
        # Normalize interface name to match stop endpoint normalization
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            if "Port:" in s:
                s = s.replace("Port:", "").strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        interface_name = normalize_iface(interface_label)

        for stream_data in stream_list:
            stream_name = stream_data.get("name", "Unnamed Stream")
            stream_id = stream_data.setdefault("stream_id", str(uuid.uuid4()))
            flow_tracking = stream_data.setdefault("flow_tracking_enabled", False)

            # Check enabled flag - it might be at top level or in protocol_selection
            enabled = stream_data.get("enabled", False) or stream_data.get("protocol_selection", {}).get("enabled", False)
            if not enabled:
                logging.info(f"⏩ Skipping disabled stream '{stream_name}' on interface '{interface_name}'")
                continue

            stream_data["interface"] = interface_name
            stream_data["stream_name"] = stream_name

            # Normalize rx_port - handle "Same as TX Port" and various formats
            rx_port = stream_data.get("rx_port") or interface_name
            if isinstance(rx_port, str):
                # Handle "Same as TX Port" - use TX interface
                if "Same as TX Port" in rx_port or rx_port.strip() == "Same as TX Port":
                    rx_interface = interface_name
                else:
                    # Extract interface name from various formats
                    rx_interface = str(rx_port).split("Port:")[-1].strip()
                    if not rx_interface or rx_interface == rx_port:
                        rx_interface = str(rx_port).strip()
            else:
                rx_interface = interface_name
            stream_data["rx_interface"] = rx_interface

            # Prevent duplicates - check by stream_id first
            existing = stream_tracker.find_stream_by_id(interface_name, stream_id)
            if existing:
                logging.warning(f"⚠️ Stream '{stream_name}' already running on {interface_name} with ID {stream_id}")
                continue
            
            # CRITICAL: Check for existing streams with same name (different stream_id)
            # This handles the case where stream was edited/restarted and stream_id changed
            # but old stream with same name is still running
            existing_by_name = stream_tracker.find_streams_by_name(interface_name, stream_name)
            if existing_by_name:
                logging.warning(f"⚠️ Found {len(existing_by_name)} existing stream(s) with name '{stream_name}' on {interface_name}, stopping them first")
                for existing_stream in existing_by_name:
                    existing_stream_id = existing_stream.get("stream_id")
                    logging.info(f"Stopping existing stream {existing_stream_id} (name: '{stream_name}') before starting new one")
                    existing_stream["stop_event"].set()
                    
                    # Wait for the thread to actually finish (with timeout)
                    future = existing_stream.get("future")
                    if future:
                        logging.info(f"⏳ Waiting for thread to finish for stream {existing_stream_id}...")
                        try:
                            future.result(timeout=5.0)
                            logging.info(f"✅ Thread completed for stream {existing_stream_id}")
                        except Exception as e:
                            logging.warning(f"⚠️ Thread for stream {existing_stream_id} did not complete within timeout: {e}")
                    
                    # Wait for RX sniffer cleanup if flow tracking was enabled
                    if existing_stream.get("flow_tracking_enabled") and existing_stream.get("rx_interface") != interface_name:
                        import time
                        time.sleep(0.5)
                    
                    stream_tracker.remove_stream_by_id(interface_name, existing_stream_id)
                
                # Wait a bit to ensure threads have stopped before starting new stream
                import time
                time.sleep(0.5)

            try:
                result = launch_single_stream(stream_data, interface_name)
                
                # Register stream in database only if launch was successful
                if result and result.get("status") == "started" and not result.get("error"):
                    # Extract TG ID from interface_label (format: "TG X - Port: interface" or "TG X - interface")
                    tg_id = None
                    try:
                        if "TG" in interface_label:
                            tg_part = interface_label.split("TG")[1].split(" - ")[0].strip()
                            tg_id = int(tg_part) if tg_part.isdigit() else None
                    except Exception:
                        pass
                    
                    # Get server URL from request
                    server_url = request.url_root.rstrip('/')
                    if not server_url:
                        server_url = request.host_url.rstrip('/')
                    
                    # Get RX interface from stream_data
                    rx_interface = stream_data.get("rx_interface") or interface_name
                    
                    try:
                        stream_db.register_stream(
                            stream_id=stream_id,
                            stream_name=stream_name,
                            interface=interface_name,
                            rx_interface=rx_interface,
                            server_url=server_url,
                            tg_id=tg_id,
                            flow_tracking_enabled=flow_tracking,
                            stream_config=stream_data
                        )
                        logging.info(f"✅ Registered stream '{stream_name}' (ID: {stream_id}) in database")
                    except Exception as db_error:
                        logging.error(f"❌ Failed to register stream '{stream_name}' in database: {db_error}")
                        # Continue even if database registration fails - stream is still running
                
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

        # Normalize interface name (remove "Port: " prefix and "TG X - " prefix if present)
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            if "Port:" in s:
                s = s.replace("Port:", "").strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        interface_normalized = normalize_iface(interface)
        
        logging.info(f"🛑 Attempting to stop stream ID: {stream_id} on interface: {interface} (normalized: {interface_normalized})")
        
        # Debug: Log all active streams for this interface
        all_active = stream_tracker.get_stream_stats()
        matching_interface_streams = [s for s in all_active if s.get("interface") == interface_normalized]
        logging.info(f"🔍 DEBUG: Found {len(matching_interface_streams)} active stream(s) on interface '{interface_normalized}': {[{'id': s.get('stream_id'), 'name': s.get('stream_name')} for s in matching_interface_streams]}")
        
        stream = stream_tracker.find_stream_by_id(interface_normalized, stream_id)

        if stream:
            logging.info(f"✅ Found stream by stream_id: {stream_id}")
            stream["stop_event"].set()

            # Wait for the thread to actually finish (with timeout)
            future = stream.get("future")
            future_completed = False
            if future:
                logging.info(f"⏳ Waiting for thread to finish for stream {stream_id}...")
                try:
                    # Wait up to 5 seconds for thread to complete
                    future.result(timeout=5.0)
                    logging.info(f"✅ Thread completed for stream {stream_id}")
                    future_completed = True
                except Exception as e:
                    # Thread might still be running, but we'll proceed
                    logging.warning(f"⚠️ Thread for stream {stream_id} did not complete within timeout: {e}")

            # Defensive backstop: any tx_worker still running for this
            # stream_id is a process the launcher's finally block didn't
            # reap (could be a stuck reader, the launcher already exited,
            # or a previous-server orphan). pkill -TERM by --stream-id
            # pattern; if anything matched, log + escalate to KILL after 1s.
            try:
                import subprocess as _sp
                pat = f"--stream-id {stream_id}"
                check = _sp.run(["pgrep", "-f", "--", pat], capture_output=True, text=True, timeout=3)
                if check.returncode == 0 and check.stdout.strip():
                    pids = check.stdout.strip().split()
                    logging.warning(
                        f"⚠️ tx_worker(s) still alive for stream {stream_id} after stop "
                        f"(pids={','.join(pids)}, future_completed={future_completed}) — force-killing"
                    )
                    _sp.run(["pkill", "-TERM", "-f", "--", pat], capture_output=True, timeout=3)
                    import time as _t
                    _t.sleep(1.0)
                    _sp.run(["pkill", "-KILL", "-f", "--", pat], capture_output=True, timeout=3)
                    logging.info(f"✅ Force-kill complete for stream {stream_id}")
            except Exception as e:
                logging.warning(f"⚠️ Backstop pkill for stream {stream_id} failed: {e}")

            stream_tracker.remove_stream_by_id(interface_normalized, stream_id)
            # Mark stream as stopped in database
            try:
                stream_db.stop_stream(stream_id)
                logging.info(f"✅ Updated stream {stream_id} status to Stopped in database")
            except Exception as db_error:
                logging.warning(f"⚠️ Failed to update stream {stream_id} status in database: {db_error}")
            logging.info(f"✅ Stop event set for stream ID: {stream_id}")
            logging.info(f"🛑 Stream stopped: {stream_id} on {interface_normalized} (Reason: manual)")
            stopped.append({"interface": interface_normalized, "stream_id": stream_id})
        else:
            # Fallback: try to find by name if we have stream_name in the request
            stream_name = entry.get("stream_name")
            if stream_name:
                logging.warning(f"❌ Stream ID '{stream_id}' not found, trying to find by name '{stream_name}' on interface '{interface_normalized}'")
                streams_by_name = stream_tracker.find_streams_by_name(interface_normalized, stream_name)
                if streams_by_name:
                    logging.info(f"Found {len(streams_by_name)} stream(s) with name '{stream_name}', stopping all")
                    for s in streams_by_name:
                        actual_stream_id = s.get("stream_id")
                        logging.info(f"Stopping stream {actual_stream_id} (name: '{stream_name}') on {interface_normalized}")
                        s["stop_event"].set()

                        # Wait for the thread to actually finish (with timeout)
                        future = s.get("future")
                        if future:
                            try:
                                future.result(timeout=5.0)
                                logging.info(f"✅ Thread completed for stream {actual_stream_id}")
                            except Exception as e:
                                logging.warning(f"⚠️ Thread for stream {actual_stream_id} did not complete within timeout: {e}")

                        # Defensive backstop — same as in the by-id branch above.
                        # Force-kill any tx_worker still alive for this stream_id.
                        try:
                            import subprocess as _sp
                            pat = f"--stream-id {actual_stream_id}"
                            check = _sp.run(["pgrep", "-f", "--", pat], capture_output=True, text=True, timeout=3)
                            if check.returncode == 0 and check.stdout.strip():
                                pids = check.stdout.strip().split()
                                logging.warning(
                                    f"⚠️ tx_worker(s) still alive for stream {actual_stream_id} after stop "
                                    f"(pids={','.join(pids)}) — force-killing"
                                )
                                _sp.run(["pkill", "-TERM", "-f", "--", pat], capture_output=True, timeout=3)
                                import time as _t
                                _t.sleep(1.0)
                                _sp.run(["pkill", "-KILL", "-f", "--", pat], capture_output=True, timeout=3)
                        except Exception as _e:
                            logging.warning(f"⚠️ Backstop pkill for stream {actual_stream_id} failed: {_e}")

                        # Wait for RX sniffer cleanup if needed
                        if s.get("flow_tracking_enabled") and s.get("rx_interface") != interface_normalized:
                            import time
                            time.sleep(0.5)

                        stream_tracker.remove_stream_by_id(interface_normalized, actual_stream_id)
                        
                        # Mark as stopped in database
                        try:
                            stream_db.stop_stream(actual_stream_id)
                        except Exception as db_error:
                            logging.warning(f"⚠️ Failed to update stream {actual_stream_id} status in database: {db_error}")
                        
                        stopped.append({"interface": interface_normalized, "stream_id": actual_stream_id})
                    
                    # Wait a bit to ensure threads have stopped
                    import time
                    time.sleep(0.5)
                else:
                    # Last resort: try to find ANY stream on this interface (in case name doesn't match either)
                    logging.warning(f"❌ Stream ID '{stream_id}' and name '{stream_name}' not found. Checking all streams on interface '{interface_normalized}'")
                    if matching_interface_streams:
                        logging.warning(f"⚠️ Found {len(matching_interface_streams)} other stream(s) on this interface. Stopping all to prevent orphaned streams.")
                        for s in matching_interface_streams:
                            actual_stream_id = s.get("stream_id")
                            actual_name = s.get("stream_name")
                            logging.info(f"Stopping orphaned stream {actual_stream_id} (name: '{actual_name}') on {interface_normalized}")
                            # Find the stream object to set stop_event
                            stream_obj = stream_tracker.find_stream_by_id(interface_normalized, actual_stream_id)
                            if stream_obj:
                                stream_obj["stop_event"].set()
                                
                                # Wait for the thread to actually finish (with timeout)
                                future = stream_obj.get("future")
                                if future:
                                    try:
                                        future.result(timeout=5.0)
                                        logging.info(f"✅ Thread completed for orphaned stream {actual_stream_id}")
                                    except Exception as e:
                                        logging.warning(f"⚠️ Thread for orphaned stream {actual_stream_id} did not complete within timeout: {e}")
                                
                                stream_tracker.remove_stream_by_id(interface_normalized, actual_stream_id)
                                try:
                                    stream_db.stop_stream(actual_stream_id)
                                except Exception:
                                    pass
                                stopped.append({"interface": interface_normalized, "stream_id": actual_stream_id})
                        import time
                        time.sleep(0.5)
                    else:
                        logging.warning(f"❌ Stream ID '{stream_id}' and name '{stream_name}' not found on interface '{interface_normalized}'")
            else:
                logging.warning(f"❌ Stream ID '{stream_id}' not found on interface '{interface_normalized}' and no stream_name provided for fallback")

    return jsonify({"stopped": stopped}), 200



def _configure_routing_protocols(device_id, device_name, bgp_config=None, ospf_config=None, isis_config=None, 
                                   ipv4=None, ipv6=None, ipv4_mask=None, ipv6_mask=None, dhcp_mode=None):
    """
    Unified helper function to configure all routing protocols.
    This is the SINGLE SOURCE OF TRUTH for protocol configuration.
    
    Args:
        device_id: Device identifier
        device_name: Device name
        bgp_config: BGP configuration dict (optional)
        ospf_config: OSPF configuration dict (optional)
        isis_config: ISIS configuration dict (optional)
        ipv4: IPv4 address (optional)
        ipv6: IPv6 address (optional)
        ipv4_mask: IPv4 mask (optional)
        ipv6_mask: IPv6 mask (optional)
        dhcp_mode: DHCP mode (optional, "client" will skip BGP)
    
    Returns:
        dict with success status for each protocol
    """
    results = {
        "bgp": {"success": False, "error": None},
        "ospf": {"success": False, "error": None},
        "isis": {"success": False, "error": None}
    }
    
    # Normalize IP addresses (strip CIDR notation if present)
    ipv4_for_config = ipv4.split("/")[0] if ipv4 and "/" in ipv4 else ipv4
    ipv6_for_config = ipv6.split("/")[0] if ipv6 and "/" in ipv6 else ipv6
    
    # Configure BGP if enabled
    if bgp_config and isinstance(bgp_config, dict) and len(bgp_config) > 0:
        if dhcp_mode == "client":
            logging.info(f"[PROTOCOL CONFIG] Skipping BGP configuration because device is in DHCP client mode")
            results["bgp"]["success"] = False
            results["bgp"]["error"] = "Skipped (DHCP client mode)"
        else:
            try:
                logging.info(f"[PROTOCOL CONFIG] Configuring BGP for device {device_name}")
                from utils.bgp import configure_bgp_for_device
                ipv4_full = f"{ipv4}/{ipv4_mask}" if ipv4 and ipv4_mask else ipv4
                ipv6_full = f"{ipv6}/{ipv6_mask}" if ipv6 and ipv6_mask else ipv6
                configure_bgp_for_device(device_id, bgp_config, ipv4_full, ipv6_full, device_name)
                results["bgp"]["success"] = True
                logging.info(f"[PROTOCOL CONFIG] ✅ BGP configured successfully for device {device_name}")
            except Exception as bgp_exc:
                results["bgp"]["error"] = str(bgp_exc)
                logging.error(f"[PROTOCOL CONFIG] Failed to configure BGP for device {device_name}: {bgp_exc}", exc_info=True)
    else:
        logging.debug(f"[PROTOCOL CONFIG] BGP config is empty or invalid, skipping BGP configuration")
    
    # Configure OSPF if enabled
    if ospf_config and isinstance(ospf_config, dict) and len(ospf_config) > 0:
        try:
            logging.info(f"[PROTOCOL CONFIG] Configuring OSPF for device {device_name}")
            from utils.ospf import configure_ospf_neighbor
            configure_ospf_neighbor(device_id, ospf_config, device_name, ipv4=ipv4_for_config, ipv6=ipv6_for_config)
            results["ospf"]["success"] = True
            logging.info(f"[PROTOCOL CONFIG] ✅ OSPF configured successfully for device {device_name}")
        except Exception as ospf_exc:
            results["ospf"]["error"] = str(ospf_exc)
            logging.error(f"[PROTOCOL CONFIG] Failed to configure OSPF for device {device_name}: {ospf_exc}", exc_info=True)
    else:
        logging.debug(f"[PROTOCOL CONFIG] OSPF config is empty or invalid, skipping OSPF configuration")
    
    # Configure ISIS if enabled
    if isis_config and isinstance(isis_config, dict) and len(isis_config) > 0:
        try:
            logging.info(f"[PROTOCOL CONFIG] Configuring ISIS for device {device_name}")
            from utils.isis import configure_isis_neighbor
            configure_isis_neighbor(device_id, isis_config, device_name, ipv4=ipv4_for_config, ipv6=ipv6_for_config)
            results["isis"]["success"] = True
            logging.info(f"[PROTOCOL CONFIG] ✅ ISIS configured successfully for device {device_name}")
        except Exception as isis_exc:
            results["isis"]["error"] = str(isis_exc)
            logging.error(f"[PROTOCOL CONFIG] Failed to configure ISIS for device {device_name}: {isis_exc}", exc_info=True)
    else:
        logging.debug(f"[PROTOCOL CONFIG] ISIS config is empty or invalid, skipping ISIS configuration")
    
    return results

@app.route("/api/device/start", methods=["POST"])
def start_device():
    data = request.get_json()
    logging.info(f"Start Device Data: {data}")
    if not data:
        return jsonify({"error": "Missing device configuration"}), 400

    try:
        logging.info(f"[DEVICE START] Function entry - starting device processing")
        global device_db
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

        # Mark device as starting to indicate in-progress lifecycle
        try:
            if device_id:
                device_db.update_device_status(device_id, "Starting")
                logging.info(f"[DEVICE DB] Device {device_id} status updated to Starting")
        except Exception as e:
            logging.warning(f"[DEVICE DB] Failed to update device {device_id} status to Starting: {e}")

        # Normalize interface name (extract base interface from labels like "TG 0 - Port: ens4np0")
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        # Normalize interface name
        iface_normalized = normalize_iface(iface)

        # Light start: enable interface and configure IP addresses if provided
        result = {"device_id": device_id, "device": device_name, "interface": iface_normalized}
        # CRITICAL: For VLAN interfaces, use format vlan{vlan}@{interface} to avoid conflicts
        if vlan and vlan != "0":
            iface_name = f"vlan{vlan}@{iface_normalized}"
        else:
            iface_name = iface_normalized
        
        # CRITICAL: Validate interface name when VLAN is not used
        if not iface_name:
            error_msg = "Interface name is required when VLAN is not specified"
            logging.error(f"[DEVICE START] {error_msg}")
            return jsonify({"error": error_msg}), 400
        
        # Prepare protocol and DHCP context before manipulating addresses
        protocols = data.get("protocols", [])
        if isinstance(protocols, str):
            try:
                protocols = json.loads(protocols) if protocols else []
            except Exception:
                protocols = [p.strip() for p in protocols.split(",") if p.strip()]
        elif not isinstance(protocols, list):
            protocols = []
        
        raw_dhcp_config = data.get("dhcp_config")
        dhcp_config = {}
        if isinstance(raw_dhcp_config, str):
            try:
                dhcp_config = json.loads(raw_dhcp_config) if raw_dhcp_config else {}
            except Exception:
                logging.warning(f"[DEVICE START] Failed to parse DHCP config payload: {raw_dhcp_config}")
                dhcp_config = {}
        elif isinstance(raw_dhcp_config, dict):
            dhcp_config = raw_dhcp_config.copy()
        
        device_data = None
        if device_id:
            try:
                device_data = device_db.get_device(device_id)
            except Exception as fetch_exc:
                logging.warning(f"[DEVICE START] Failed to load device {device_id} from database: {fetch_exc}")
        
        if not protocols and device_data:
            protocols = device_data.get("protocols", []) or []
        
        if not dhcp_config and device_data:
            existing_dhcp_config = device_data.get("dhcp_config") or {}
            if isinstance(existing_dhcp_config, dict):
                dhcp_config = existing_dhcp_config.copy()
        
        vxlan_config_raw = data.get("vxlan_config", {})
        if isinstance(vxlan_config_raw, str):
            try:
                vxlan_config = json.loads(vxlan_config_raw) if vxlan_config_raw else {}
            except json.JSONDecodeError:
                logging.warning(f"[DEVICE APPLY] Invalid VXLAN config JSON: {vxlan_config_raw}")
                vxlan_config = {}
        else:
            vxlan_config = vxlan_config_raw or {}
        vxlan_config = vxlan_utils.normalize_config(vxlan_config)
        if vxlan_config and "VXLAN" not in protocols:
            protocols.append("VXLAN")

        dhcp_mode = (dhcp_config.get("mode") or "").lower() if isinstance(dhcp_config, dict) else ""
        if dhcp_config and "DHCP" not in protocols:
            protocols.append("DHCP")
        if dhcp_mode == "client":
            protocols = [p for p in protocols if p in ("OSPF", "ISIS", "DHCP")]
        
        if device_id and dhcp_mode:
            try:
                device_db.update_device(device_id, {
                    "dhcp_mode": dhcp_config.get("mode"),
                    "dhcp_config": dhcp_config,
                    "dhcp_state": "Pending",
                    "dhcp_running": False,
                    "last_dhcp_check": datetime.now(timezone.utc).isoformat()
                })
            except Exception as pending_exc:
                logging.warning(f"[DEVICE START] Failed to mark DHCP state Pending for {device_id}: {pending_exc}")
        
        # Skip static IP assignment for DHCP client devices
        if dhcp_mode == "client":
            ipv4 = ""
            ipv6 = ""
            ipv4_mask = ""
            ipv6_mask = ""
        
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
        
        dhcp_result = None
        if device_id and dhcp_mode in ("client", "server") and dhcp_config:
            try:
                logging.info(f"[DHCP] Ensuring DHCP {dhcp_mode} services for device {device_id} on {iface_name}")
                dhcp_result = ensure_dhcp_services(
                    device_db,
                    device_id,
                    iface_name,
                    dhcp_config,
                    force_client_restart=(dhcp_mode == "client"),
                )
                result["dhcp"] = dhcp_result
                if dhcp_result.get("success"):
                    # Refresh device record to pick up lease/server state
                    try:
                        device_data = device_db.get_device(device_id)
                    except Exception as refresh_exc:
                        logging.warning(f"[DHCP] Failed to refresh device {device_id} after DHCP ensure: {refresh_exc}")
            except Exception as dhcp_error:
                logging.warning(f"[DHCP] Failed to configure DHCP services for device {device_id}: {dhcp_error}")
                result["dhcp"] = {"success": False, "error": str(dhcp_error)}
        
        # Recompute protocol context using latest database state (after potential DHCP refresh)
        dhcp_mode = (dhcp_config.get("mode") or "").lower() if isinstance(dhcp_config, dict) else ""
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
                        except Exception:
                            protocols = []
                    
                    dhcp_config = dhcp_config if dhcp_config else (device_data.get("dhcp_config", {}) if device_data else {})
                    if dhcp_config and "DHCP" not in protocols:
                        protocols.append("DHCP")
                    if isinstance(dhcp_config, dict):
                        dhcp_mode = (dhcp_config.get("mode") or "").lower()
                        if dhcp_mode == "client":
                            protocols = [p for p in protocols if p != "BGP"]
                    
                    # Also check if protocol configs are provided even if protocols list is empty
                    has_bgp_config = bool(data.get("bgp_config") or device_data.get("bgp_config"))
                    has_ospf_config = bool(data.get("ospf_config") or device_data.get("ospf_config"))
                    has_isis_config = bool(data.get("isis_config") or device_data.get("isis_config"))
                    if dhcp_mode == "client":
                        has_bgp_config = False
                    
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
                                logging.info(f"[DEVICE START] Container {container_name} is already running, reconfiguring protocols with updated configs")
                            
                            try:
                                interface_config = {
                                    "interface": iface_normalized,
                                    "vlan": vlan,
                                    "ipv4": ipv4 if dhcp_mode != "client" else "",
                                    "ipv6": ipv6,
                                    "loopback_ipv4": device_data.get("loopback_ipv4") if device_data else "",
                                    "loopback_ipv6": device_data.get("loopback_ipv6") if device_data else "",
                                    "dhcp_mode": dhcp_mode,
                                    "bgp_asn": device_data.get("bgp_asn", 65000) if device_data else 65000,
                                    "router_id": (device_data.get("loopback_ipv4") or device_data.get("ipv4_address", "") if device_data else ""),
                                }
                                frr_manager._configure_interfaces(container_name, device_id, interface_config)
                            except Exception as iface_exc:
                                logging.warning(f"[DEVICE START] Failed to sync interface config for container {container_name}: {iface_exc}")
                        
                            if dhcp_mode in ("client", "server") and dhcp_config:
                                try:
                                    dhcp_result = ensure_dhcp_services(
                                        device_db,
                                        device_id,
                                        iface_name,
                                        dhcp_config,
                                        container=container,
                                        force_client_restart=(dhcp_mode == "client"),
                                    )
                                    result["dhcp"] = dhcp_result
                                    if dhcp_result.get("success"):
                                        try:
                                            device_data = device_db.get_device(device_id)
                                        except Exception as refresh_exc:
                                            logging.warning(f"[DHCP] Failed to refresh device {device_id} after DHCP ensure: {refresh_exc}")
                                except Exception as dhcp_error:
                                    logging.warning(f"[DHCP] Failed to configure DHCP services for device {device_id}: {dhcp_error}")
                                    result["dhcp"] = {"success": False, "error": str(dhcp_error)}
                            
                            # Always configure protocols with latest configs from payload (or database)
                            # This ensures that after device edit, protocols are updated even if container was already running
                            # Get protocol configs from payload first (if provided), otherwise from database
                            import json
                            
                            # BGP config: prefer payload, fallback to database
                            # Check if bgp_config is in payload (even if empty dict, we should check explicitly)
                            bgp_config = None
                            if "bgp_config" in data:
                                bgp_config = data.get("bgp_config")  # Use directly, could be dict or empty dict
                                logging.info(f"[DEVICE START] Using BGP config from payload: {bgp_config is not None}, has content: {bool(bgp_config)}")
                            if bgp_config is None:
                                # Fallback to database
                                bgp_config_raw = device_data.get("bgp_config", {})
                                if isinstance(bgp_config_raw, str) and bgp_config_raw:
                                    try:
                                        bgp_config = json.loads(bgp_config_raw)
                                    except Exception:
                                        bgp_config = {}
                                else:
                                    bgp_config = bgp_config_raw if bgp_config_raw else {}
                                logging.info(f"[DEVICE START] Using BGP config from database: {bool(bgp_config)}")
                            
                            # OSPF config: prefer payload, fallback to database
                            ospf_config = None
                            if "ospf_config" in data:
                                ospf_config = data.get("ospf_config")  # Use directly, could be dict or empty dict
                                logging.info(f"[DEVICE START] Using OSPF config from payload: {ospf_config is not None}, has content: {bool(ospf_config)}")
                            if ospf_config is None:
                                # Fallback to database
                                ospf_config_raw = device_data.get("ospf_config", {})
                                if isinstance(ospf_config_raw, str) and ospf_config_raw:
                                    try:
                                        ospf_config = json.loads(ospf_config_raw)
                                    except Exception:
                                        ospf_config = {}
                                else:
                                    ospf_config = ospf_config_raw if ospf_config_raw else {}
                                logging.info(f"[DEVICE START] Using OSPF config from database: {bool(ospf_config)}")
                            
                            # ISIS config: prefer payload, fallback to database
                            isis_config = None
                            if "isis_config" in data:
                                isis_config = data.get("isis_config")  # Use directly, could be dict or empty dict
                                logging.info(f"[DEVICE START] Using ISIS config from payload: {isis_config is not None}, has content: {bool(isis_config)}")
                            if isis_config is None:
                                # Fallback to database
                                isis_config_raw = device_data.get("isis_config", {})
                                if isinstance(isis_config_raw, str) and isis_config_raw:
                                    try:
                                        isis_config = json.loads(isis_config_raw)
                                    except Exception:
                                        isis_config = {}
                                else:
                                    isis_config = isis_config_raw if isis_config_raw else {}
                                logging.info(f"[DEVICE START] Using ISIS config from database: {bool(isis_config)}")
                            
                            # Configure protocols in the container
                            # Use IP addresses from payload (latest from client) or fallback to database / DHCP lease
                            ipv4_for_config = ""
                            ipv4_mask_for_config = ""
                            if dhcp_mode == "client":
                                lease_ip = ""
                                lease_mask = ""
                                if device_data:
                                    lease_ip = (device_data.get("dhcp_lease_ip") or "").strip()
                                    lease_mask = (device_data.get("dhcp_lease_mask") or "").strip()
                                    if not lease_ip:
                                        ipv4_cidr_db = device_data.get("ipv4_address") or ""
                                        if isinstance(ipv4_cidr_db, str) and "/" in ipv4_cidr_db:
                                            addr_part, mask_part = ipv4_cidr_db.split("/", 1)
                                            lease_ip = addr_part.strip()
                                            lease_mask = lease_mask or mask_part.strip()
                                ipv4_for_config = lease_ip
                                ipv4_mask_for_config = lease_mask or (device_data.get("ipv4_mask") if device_data else None) or "24"
                            else:
                                if ipv4:
                                    ipv4_for_config = ipv4
                                elif device_data:
                                    ipv4_cidr_db = device_data.get("ipv4_address") or ""
                                    if isinstance(ipv4_cidr_db, str) and "/" in ipv4_cidr_db:
                                        addr_part, mask_part = ipv4_cidr_db.split("/", 1)
                                        ipv4_for_config = addr_part.strip()
                                        ipv4_mask_for_config = mask_part.strip()
                                    elif isinstance(ipv4_cidr_db, str) and ipv4_cidr_db:
                                        ipv4_for_config = ipv4_cidr_db.strip()
                                if not ipv4_mask_for_config:
                                    ipv4_mask_for_config = ipv4_mask or (device_data.get("ipv4_mask") if device_data else None) or "24"
                            
                            ipv4_full = f"{ipv4_for_config}/{ipv4_mask_for_config}" if ipv4_for_config and ipv4_mask_for_config else ""
                            
                            ipv6_for_config = ipv6 if ipv6 else device_data.get('ipv6_address', '')
                            ipv6_mask_for_config = ipv6_mask if ipv6_mask else device_data.get('ipv6_mask', '64')
                            ipv6_full = f"{ipv6_for_config}/{ipv6_mask_for_config}" if ipv6_for_config else ""
                            
                            # Extract device_id and device_name from container_name for consistency
                            # This ensures we use the actual container naming, not the request values
                            device_id = container_name.replace(f"{frr_manager.container_prefix}-", "")
                            # CRITICAL: device_name_from_container should be extracted from database using device_id
                            # since container names only contain device_id, not device_name
                            # Try to get device_name from database, fallback to original device_name from request
                            device_name_from_container = device_name  # Default to request value
                            try:
                                from utils.device_database import DeviceDatabase
                                db_lookup = DeviceDatabase()
                                device_data = db_lookup.get_device(device_id) if device_id else None
                                if device_data:
                                    device_name_from_container = device_data.get('device_name', device_name)
                            except Exception as e:
                                logging.debug(f"[DEVICE START] Could not retrieve device_name from database: {e}")
                            
                            # Configure all routing protocols using unified helper
                            protocol_results = _configure_routing_protocols(
                                device_id=device_id,
                                device_name=device_name_from_container,
                                bgp_config=bgp_config,
                                ospf_config=ospf_config,
                                isis_config=isis_config,
                                ipv4=ipv4_for_config,
                                ipv6=ipv6_for_config,
                                ipv4_mask=ipv4_mask_for_config,
                                ipv6_mask=ipv6_mask_for_config,
                                dhcp_mode=dhcp_mode
                            )
                            logging.info(f"[DEVICE START] Protocol configuration results: {protocol_results}")
                        except Exception:
                            logging.info(f"[DEVICE START] Container {container_name} does not exist, creating it...")
                            # Create container with device configuration
                            # CRITICAL: Use normalized interface name (not the original iface from request)
                            device_config = {
                                "device_name": device_name,
                                "ipv4": ipv4,
                                "ipv6": ipv6,
                                "interface": iface_normalized,  # Use normalized interface name
                                "vlan": vlan,
                                "dhcp_mode": dhcp_mode,
                            }
                            
                            # Get protocol configs from payload first (if provided), otherwise from database
                            import json
                            
                            # BGP config: prefer payload, fallback to database
                            bgp_config = data.get("bgp_config")
                            if not bgp_config:
                                bgp_config_raw = device_data.get("bgp_config", {})
                                if isinstance(bgp_config_raw, str) and bgp_config_raw:
                                    try:
                                        bgp_config = json.loads(bgp_config_raw)
                                    except Exception:
                                        bgp_config = {}
                                else:
                                    bgp_config = bgp_config_raw if bgp_config_raw else {}
                            if dhcp_mode == "client":
                                bgp_config = {}
                            device_config["bgp_config"] = bgp_config
                            
                            # OSPF config: prefer payload, fallback to database
                            ospf_config = data.get("ospf_config")
                            if not ospf_config:
                                ospf_config_raw = device_data.get("ospf_config", {})
                                if isinstance(ospf_config_raw, str) and ospf_config_raw:
                                    try:
                                        ospf_config = json.loads(ospf_config_raw)
                                    except Exception:
                                        ospf_config = {}
                                else:
                                    ospf_config = ospf_config_raw if ospf_config_raw else {}
                            device_config["ospf_config"] = ospf_config
                            
                            # ISIS config: prefer payload, fallback to database
                            isis_config = data.get("isis_config")
                            if not isis_config:
                                isis_config_raw = device_data.get("isis_config", {})
                                if isinstance(isis_config_raw, str) and isis_config_raw:
                                    try:
                                        isis_config = json.loads(isis_config_raw)
                                    except Exception:
                                        isis_config = {}
                                else:
                                    isis_config = isis_config_raw if isis_config_raw else {}
                            device_config["isis_config"] = isis_config
                            
                            container_name = frr_manager.start_frr_container(device_id, device_config)
                            if container_name:
                                logging.info(f"[DEVICE START] Successfully created FRR container: {container_name}")
                                # Wait for container to be ready
                                # Note: Individual protocol configuration functions also have retry logic
                                # This initial wait helps, but the protocol functions will retry if needed
                                import time
                                time.sleep(3)  # Reduced from 5 to 3 since protocol functions have retry logic
                                
                                try:
                                    container = frr_manager.client.containers.get(container_name)
                                except Exception as container_exc:
                                    logging.warning(f"[DEVICE START] Unable to retrieve container object {container_name}: {container_exc}")
                                    container = None
                                
                                if dhcp_mode in ("client", "server") and dhcp_config:
                                    try:
                                        dhcp_result = ensure_dhcp_services(
                                            device_db,
                                            device_id,
                                            iface_name,
                                            dhcp_config,
                                            container=container,
                                            force_client_restart=(dhcp_mode == "client"),
                                        )
                                        result["dhcp"] = dhcp_result
                                        if dhcp_result.get("success"):
                                            try:
                                                device_data = device_db.get_device(device_id)
                                            except Exception as refresh_exc:
                                                logging.warning(f"[DHCP] Failed to refresh device {device_id} after DHCP ensure: {refresh_exc}")
                                    except Exception as dhcp_error:
                                        logging.warning(f"[DHCP] Failed to configure DHCP services for device {device_id}: {dhcp_error}")
                                        result["dhcp"] = {"success": False, "error": str(dhcp_error)}
                                
                                # Configure protocols in the newly created container
                                # Use IP addresses from payload (latest from client) or fallback to database / DHCP lease
                                ipv4_for_config = ""
                                ipv4_mask_for_config = ""
                                if dhcp_mode == "client":
                                    lease_ip = ""
                                    lease_mask = ""
                                    if device_data:
                                        lease_ip = (device_data.get("dhcp_lease_ip") or "").strip()
                                        lease_mask = (device_data.get("dhcp_lease_mask") or "").strip()
                                        if not lease_ip:
                                            ipv4_cidr_db = device_data.get("ipv4_address") or ""
                                            if isinstance(ipv4_cidr_db, str) and "/" in ipv4_cidr_db:
                                                addr_part, mask_part = ipv4_cidr_db.split("/", 1)
                                                lease_ip = addr_part.strip()
                                                lease_mask = lease_mask or mask_part.strip()
                                    ipv4_for_config = lease_ip
                                    ipv4_mask_for_config = lease_mask or (device_data.get("ipv4_mask") if device_data else None) or "24"
                                else:
                                    if ipv4:
                                        ipv4_for_config = ipv4
                                    elif device_data:
                                        ipv4_cidr_db = device_data.get("ipv4_address") or ""
                                        if isinstance(ipv4_cidr_db, str) and "/" in ipv4_cidr_db:
                                            addr_part, mask_part = ipv4_cidr_db.split("/", 1)
                                            ipv4_for_config = addr_part.strip()
                                            ipv4_mask_for_config = mask_part.strip()
                                        elif isinstance(ipv4_cidr_db, str) and ipv4_cidr_db:
                                            ipv4_for_config = ipv4_cidr_db.strip()
                                    if not ipv4_mask_for_config:
                                        ipv4_mask_for_config = ipv4_mask or (device_data.get("ipv4_mask") if device_data else None) or "24"
                                
                                ipv4_full = f"{ipv4_for_config}/{ipv4_mask_for_config}" if ipv4_for_config and ipv4_mask_for_config else ""
                                
                                ipv6_for_config = ipv6 if ipv6 else device_data.get('ipv6_address', '')
                                ipv6_mask_for_config = ipv6_mask if ipv6_mask else device_data.get('ipv6_mask', '64')
                                ipv6_full = f"{ipv6_for_config}/{ipv6_mask_for_config}" if ipv6_for_config else ""
                                
                                # Configure all routing protocols using unified helper
                                protocol_results = _configure_routing_protocols(
                                    device_id=device_id,
                                    device_name=device_name,
                                    bgp_config=bgp_config,
                                    ospf_config=ospf_config,
                                    isis_config=isis_config,
                                    ipv4=ipv4_for_config,
                                    ipv6=ipv6_for_config,
                                    ipv4_mask=ipv4_mask_for_config,
                                    ipv6_mask=ipv6_mask_for_config,
                                    dhcp_mode=dhcp_mode
                                )
                                logging.info(f"[DEVICE START] Protocol configuration results: {protocol_results}")
                            else:
                                logging.warning(f"[DEVICE START] Failed to create FRR container for device {device_name}")
        except Exception as e:
            logging.error(f"[DEVICE START] Failed to auto-restore protocols: {e}")
            import traceback
            logging.error(traceback.format_exc())

        def _trigger_monitor_async(label: str, check_fn):
            def _runner():
                try:
                    logging.info(f"[{label} STATUS] (async) Triggering status check for device {device_id} after start")
                    check_fn()
                except Exception as exc:
                    logging.warning(f"[{label} STATUS] (async) Failed to trigger status check for device {device_id}: {exc}")
            threading.Thread(target=_runner, daemon=True).start()
        
        # Trigger protocol status checks asynchronously after start
        if device_id:
            _trigger_monitor_async("BGP", bgp_monitor.force_check)
            _trigger_monitor_async("OSPF", ospf_monitor.force_check)
            _trigger_monitor_async("ISIS", isis_monitor.force_check)
        
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
            except Exception:
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
                # CRITICAL: Normalize interface name before storing (remove "- " or " - " prefixes)
                interface_raw = data.get("interface")
                if interface_raw:
                    interface_raw = interface_raw.strip()
                    if interface_raw.startswith("- "):
                        interface_raw = interface_raw[2:].strip()
                    elif interface_raw.startswith(" - "):
                        interface_raw = interface_raw[3:].strip()
                isis_config["interface"] = interface_raw if interface_raw else ""

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
            # Normalize interface name (extract base interface from labels like "TG 0 - Port: ens4np0")
            def normalize_iface(iface_str):
                """Normalize interface name from UI label format."""
                if not iface_str:
                    return ""
                s = iface_str.strip().strip('"').rstrip(",")
                if " - " in s:
                    s = s.split(" - ", 1)[-1].strip()
                if ":" in s:
                    s = s.rsplit(":", 1)[-1].strip()
                parts = s.split()
                return parts[-1] if parts else ""
            
            # Get interface from data or device, then normalize it
            interface_raw = data.get("interface") or device.get("interface", "ens4np0")
            interface_normalized = normalize_iface(interface_raw)
            
            dhcp_mode = (data.get("dhcp_mode") or device.get("dhcp_mode") or "")
            dhcp_mode = dhcp_mode.lower() if isinstance(dhcp_mode, str) else ""

            # Create container with device configuration
            # CRITICAL: Use normalized interface name (not the original interface from request)
            device_config = {
                "device_name": device_name,
                "ipv4": data.get("ipv4", device.get("ipv4_address", "")),
                "ipv6": data.get("ipv6", device.get("ipv6_address", "")),
                "interface": interface_normalized,  # Use normalized interface name
                "vlan": data.get("vlan", str(device.get("vlan", "0"))),
                "dhcp_mode": dhcp_mode,
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
        ipv4_mask = data.get("ipv4_mask", "24")
        ipv6_mask = data.get("ipv6_mask", "64")
        isis_config = data.get("isis_config", {})
        
        if not device_id or not isis_config:
            return jsonify({"error": "Missing device_id or ISIS configuration"}), 400

        # CRITICAL: Normalize interface name from UI label format
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        interface_normalized = normalize_iface(interface) if interface else ""
        
        # CRITICAL: Normalize interface name in isis_config if present
        # For untagged interfaces (VLAN 0), interface should be just the interface name (e.g., "ens4np0")
        # For tagged interfaces, interface should be the VLAN interface name (e.g., "vlan20")
        if isis_config.get("interface"):
            isis_config_interface = isis_config.get("interface", "")
            if isis_config_interface:
                isis_config_interface_normalized = normalize_iface(isis_config_interface)
                # If VLAN is 0 and interface still has "TG" prefix, extract just the interface name
                vlan = data.get("vlan", "0")
                if vlan == "0" or vlan == 0:
                    # For untagged interfaces, ensure it's just the interface name
                    if "TG" in isis_config_interface_normalized or " - " in isis_config_interface_normalized:
                        # Extract just the interface name after " - "
                        if " - " in isis_config_interface_normalized:
                            isis_config_interface_normalized = isis_config_interface_normalized.split(" - ", 1)[-1].strip()
                        elif "TG" in isis_config_interface_normalized:
                            # Handle "TG 0 - ens4np0" format
                            parts = isis_config_interface_normalized.split()
                            if len(parts) >= 3 and parts[0] == "TG":
                                isis_config_interface_normalized = parts[-1]
                isis_config["interface"] = isis_config_interface_normalized
                logging.info(f"[ISIS CONFIGURE] Normalized interface from '{isis_config_interface}' to '{isis_config_interface_normalized}'")
            else:
                isis_config["interface"] = ""

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
            dhcp_mode = (data.get("dhcp_mode") or "").lower()
            if not dhcp_mode:
                try:
                    from utils.device_database import DeviceDatabase
                    _db_lookup = DeviceDatabase()
                    existing = _db_lookup.get_device(device_id)
                    if existing:
                        dhcp_mode = (existing.get("dhcp_mode") or "").lower()
                except Exception:
                    dhcp_mode = ""
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": interface_normalized or "ens4np0",  # CRITICAL: Use normalized interface name
                "vlan": data.get("vlan", "0"),
                "dhcp_mode": dhcp_mode,
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
        
        # Check if IPv4 was previously configured but now disabled - need to remove IPv4 ISIS
        try:
            existing_device = device_db.get_device(device_id)
            if existing_device:
                existing_ipv4 = existing_device.get("ipv4_address", "")
                # If IPv4 was previously configured but now empty, remove IPv4 ISIS
                if existing_ipv4 and not ipv4:
                    logging.info(f"[ISIS CONFIGURE] IPv4 was configured but now disabled - removing IPv4 ISIS configuration")
                    try:
                        from utils.frr_docker import FRRDockerManager
                        frr_manager = FRRDockerManager()
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
        except Exception as e:
            logging.warning(f"[ISIS CONFIGURE] Error checking for existing IPv4 ISIS removal: {e}")
        
        # Check if IPv6 was previously configured but now disabled - need to remove IPv6 ISIS
        try:
            existing_device = device_db.get_device(device_id)
            if existing_device:
                existing_ipv6 = existing_device.get("ipv6_address", "")
                # If IPv6 was previously configured but now empty, remove IPv6 ISIS
                if existing_ipv6 and not ipv6:
                    logging.info(f"[ISIS CONFIGURE] IPv6 was configured but now disabled - removing IPv6 ISIS configuration")
                    try:
                        from utils.frr_docker import FRRDockerManager
                        frr_manager = FRRDockerManager()
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
        except Exception as e:
            logging.warning(f"[ISIS CONFIGURE] Error checking for existing IPv6 ISIS removal: {e}")
        
        # Configure ISIS neighbor
        success = configure_isis_neighbor(device_id, isis_config, device_name, ipv4=ipv4, ipv6=ipv6)
        
        if success:
            logging.info(f"[ISIS CONFIGURE] Successfully configured ISIS for device {device_name}")
            
            # Save full ISIS config to database (merge with existing to preserve all fields)
            try:
                from datetime import datetime, timezone
                existing_device = device_db.get_device(device_id)
                if existing_device:
                    existing_isis_config = existing_device.get("isis_config", {})
                    if isinstance(existing_isis_config, str) and existing_isis_config:
                        try:
                            import json
                            existing_isis_config = json.loads(existing_isis_config)
                        except Exception:
                            existing_isis_config = {}
                    elif not isinstance(existing_isis_config, dict):
                        existing_isis_config = {}
                    
                    # Merge with existing ISIS config to preserve all fields
                    merged_isis_config = existing_isis_config.copy() if existing_isis_config else {}
                    merged_isis_config.update(isis_config)  # New values override existing ones
                    
                    # Ensure all fields are preserved (area_id, system_id, hello_interval, hello_multiplier, etc.)
                    # These should already be in isis_config from the client, but ensure they're in the merged config
                    if "area_id" in isis_config:
                        merged_isis_config["area_id"] = isis_config["area_id"]
                    if "system_id" in isis_config:
                        merged_isis_config["system_id"] = isis_config["system_id"]
                    if "hello_interval" in isis_config:
                        merged_isis_config["hello_interval"] = isis_config["hello_interval"]
                    if "hello_multiplier" in isis_config:
                        merged_isis_config["hello_multiplier"] = isis_config["hello_multiplier"]
                    if "level" in isis_config:
                        merged_isis_config["level"] = isis_config["level"]
                    if "interface" in isis_config:
                        # CRITICAL: Normalize interface field in IS-IS config before saving to database
                        # For untagged interfaces (VLAN 0), interface should be just the interface name (e.g., "ens4np0")
                        # For tagged interfaces, interface should be the VLAN interface name (e.g., "vlan20")
                        isis_interface = isis_config["interface"]
                        if isis_interface:
                            # Normalize interface name - remove "TG X - " prefix if present
                            isis_interface_normalized = normalize_iface(isis_interface)
                            # If VLAN is 0 and interface still has "TG" prefix, extract just the interface name
                            vlan = data.get("vlan", existing_device.get("vlan", "0") if existing_device else "0")
                            if vlan == "0" or vlan == 0:
                                # For untagged interfaces, ensure it's just the interface name
                                if "TG" in isis_interface_normalized or " - " in isis_interface_normalized:
                                    # Extract just the interface name after " - "
                                    if " - " in isis_interface_normalized:
                                        isis_interface_normalized = isis_interface_normalized.split(" - ", 1)[-1].strip()
                                    elif "TG" in isis_interface_normalized:
                                        # Handle "TG 0 - ens4np0" format
                                        parts = isis_interface_normalized.split()
                                        if len(parts) >= 3 and parts[0] == "TG":
                                            isis_interface_normalized = parts[-1]
                            merged_isis_config["interface"] = isis_interface_normalized
                            logging.info(f"[ISIS CONFIGURE] Normalized interface from '{isis_interface}' to '{isis_interface_normalized}' before saving to database")
                        else:
                            merged_isis_config["interface"] = isis_interface
                    if "metric" in isis_config:
                        merged_isis_config["metric"] = isis_config["metric"]
                    
                    existing_protocols = existing_device.get("protocols", [])
                    if isinstance(existing_protocols, str):
                        try:
                            existing_protocols = json.loads(existing_protocols)
                        except Exception:
                            existing_protocols = []
                    if not isinstance(existing_protocols, list):
                        existing_protocols = []
                    if "ISIS" not in existing_protocols and "IS-IS" not in existing_protocols:
                        existing_protocols.append("ISIS")
                    
                    device_db.update_device(device_id, {
                        "protocols": existing_protocols,
                        "isis_config": merged_isis_config,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
                    logging.info(f"[ISIS CONFIGURE] Updated device {device_name} with full ISIS configuration (area_id: {merged_isis_config.get('area_id')}, system_id: {merged_isis_config.get('system_id')}, hello_interval: {merged_isis_config.get('hello_interval')}, hello_multiplier: {merged_isis_config.get('hello_multiplier')})")
            except Exception as e:
                logging.warning(f"[ISIS CONFIGURE] Error saving full ISIS config to database: {e}")
                import traceback
                logging.warning(traceback.format_exc())
            
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
        import traceback
        logging.error(f"[ISIS CONFIGURE ERROR] Traceback: {traceback.format_exc()}")
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
        mtu = data.get("mtu", "1500")  # MTU field, default to 1500
        ipv4 = data.get("ipv4", "")
        ipv6 = data.get("ipv6", "")
        ipv4_mask = data.get("ipv4_mask", "24")
        ipv6_mask = data.get("ipv6_mask", "64")
        ipv4_gateway = data.get("ipv4_gateway", "")
        ipv6_gateway = data.get("ipv6_gateway", "")
        loopback_ipv4 = data.get("loopback_ipv4", "")
        loopback_ipv6 = data.get("loopback_ipv6", "")
        logging.info(f"[DEVICE APPLY] Received loopback_ipv4={loopback_ipv4}, loopback_ipv6={loopback_ipv6} from client")
        protocols = data.get("protocols", [])
        dhcp_config_raw = data.get("dhcp_config", {})
        if isinstance(protocols, str):
            protocols = [p.strip() for p in protocols.split(",") if p.strip()]
        if isinstance(dhcp_config_raw, str):
            try:
                dhcp_config = json.loads(dhcp_config_raw) if dhcp_config_raw else {}
            except json.JSONDecodeError:
                logging.warning(f"[DEVICE APPLY] Invalid DHCP config JSON: {dhcp_config_raw}")
                dhcp_config = {}
        else:
            dhcp_config = dhcp_config_raw or {}
        if dhcp_config and "DHCP" not in protocols:
            protocols.append("DHCP")
        vxlan_config_raw = data.get("vxlan_config", {})
        if isinstance(vxlan_config_raw, str):
            try:
                vxlan_config = json.loads(vxlan_config_raw) if vxlan_config_raw else {}
            except json.JSONDecodeError:
                logging.warning(f"[DEVICE APPLY] Invalid VXLAN config JSON: {vxlan_config_raw}")
                vxlan_config = {}
        else:
            vxlan_config = vxlan_config_raw or {}
        
        # Handle multiple tunnels format: {"tunnels": [tunnel1, tunnel2, ...]}
        # IMPORTANT: Check for tunnels format BEFORE calling normalize_config
        # because normalize_config expects a single tunnel config, not the tunnels wrapper
        if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
            # Multiple tunnels format - normalize each tunnel
            tunnels = vxlan_config.get("tunnels", [])
            normalized_tunnels = []
            for tunnel in tunnels:
                if isinstance(tunnel, dict):
                    normalized_tunnel = vxlan_utils.normalize_config(tunnel)
                    if normalized_tunnel:
                        normalized_tunnels.append(normalized_tunnel)
            if normalized_tunnels:
                vxlan_config = {"tunnels": normalized_tunnels}
                if "VXLAN" not in protocols:
                    protocols.append("VXLAN")
                logging.info(f"[DEVICE APPLY] Processing {len(normalized_tunnels)} VXLAN tunnel(s)")
            else:
                vxlan_config = {}
        else:
            # Single tunnel format (backward compatibility)
            vxlan_config = vxlan_utils.normalize_config(vxlan_config)
            if vxlan_config and "VXLAN" not in protocols:
                protocols.append("VXLAN")

        bgp_config = data.get("bgp_config", {})
        dhcp_mode = (dhcp_config.get("mode") or "").lower() if isinstance(dhcp_config, dict) else ""
        if dhcp_mode == "client":
            logging.info(f"[DEVICE APPLY] DHCP client mode detected for device {device_id}; ignoring static IPv4/IPv6 values and BGP configuration")
            ipv4 = ""
            ipv6 = ""
            ipv4_mask = ""
            ipv6_mask = ""
            ipv4_gateway = ""
            ipv6_gateway = ""
            protocols = [p for p in protocols if p in ("OSPF", "ISIS", "DHCP")]
            bgp_config = {}
        ospf_config = data.get("ospf_config", {})
        isis_config = data.get("isis_config", {})
        
        logging.info(f"[DEVICE APPLY] ID={device_id} Name='{device_name}' Interface='{interface}' VLAN={vlan}")
        logging.info(f"[DEVICE APPLY] IPv4={ipv4}/{ipv4_mask} IPv6={ipv6}/{ipv6_mask}")
        logging.info(f"[DEVICE APPLY] Gateways: IPv4={ipv4_gateway} IPv6={ipv6_gateway}")
        logging.info(f"[DEVICE APPLY] Protocols: {protocols}")
        logging.info(f"[DEVICE APPLY] BGP Config: {bgp_config}")
        logging.info(f"[DEVICE APPLY] OSPF Config: {ospf_config}")
        logging.info(f"[DEVICE APPLY] ISIS Config: {isis_config}")
        logging.info(f"[DEVICE APPLY] DHCP Config: {dhcp_config}")
        logging.info(f"[DEVICE APPLY] VXLAN Config (raw): {vxlan_config_raw}")
        logging.info(f"[DEVICE APPLY] VXLAN Config (normalized): {vxlan_config}")
        
        # Normalize interface name (extract base interface from labels like "TG 0 - Port: ens4np0")
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        # Normalize interface name
        interface_normalized = normalize_iface(interface)
        
        result = {
            "device_id": device_id,
            "device": device_name,
            "interface": interface_normalized,
            "vlan": vlan
        }
        vxlan_state = "Disabled"
        vxlan_interface = ""
        vxlan_error = ""
        # Consider VXLAN enabled if config provided (with actual content) or protocol explicitly selected
        # Check if vxlan_config has actual content (not just empty dict)
        # Note: bool({}) is False, so empty dict won't enable VXLAN
        # Check for multiple tunnels format or single tunnel format
        vxlan_config_has_content = False
        if isinstance(vxlan_config, dict):
            if "tunnels" in vxlan_config:
                # Multiple tunnels format
                tunnels = vxlan_config.get("tunnels", [])
                vxlan_config_has_content = len(tunnels) > 0
            else:
                # Single tunnel format
                vxlan_config_has_content = (
                    vxlan_config.get("vni") or 
                    vxlan_config.get("remote_peers") or 
                    vxlan_config.get("enabled") is True
                )
        vxlan_enabled = vxlan_config_has_content or ("VXLAN" in protocols)
        if vxlan_enabled:
            logging.info(f"[DEVICE APPLY] VXLAN enabled: config_has_content={vxlan_config_has_content}, in_protocols={'VXLAN' in protocols}, vxlan_config={vxlan_config}")
        else:
            logging.warning(f"[DEVICE APPLY] VXLAN NOT enabled: config_has_content={vxlan_config_has_content}, in_protocols={'VXLAN' in protocols}, vxlan_config={vxlan_config}")
        
        # Determine interface name
        # CRITICAL: For VLAN interfaces, we need to handle the naming carefully:
        # - Linux shows VLAN interfaces as vlan{vlan}@{parent} in ip link show
        # - But when creating with ip link add, we use just vlan{vlan} as the name
        # - We use vlan{vlan}@{interface} format for our internal tracking to avoid conflicts
        # - But for actual Linux commands, we use just vlan{vlan} (Linux will show it with @parent)
        if vlan and vlan != "0":
            vlan_name_only = f"vlan{vlan}"
            # CRITICAL: Initialize iface_name_for_commands to vlan_name_only
            # It will be updated later if we create a unique-named VLAN interface (e.g., vlan20-ens4np0)
            iface_name_for_commands = vlan_name_only  # Use this for ip link commands
            iface_name = f"vlan{vlan}@{interface_normalized}"  # Use this for tracking/logging
        else:
            iface_name = interface_normalized
            iface_name_for_commands = interface_normalized
        
        if vxlan_enabled:
            # Handle multiple tunnels format
            if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
                tunnels = vxlan_config.get("tunnels", [])
                for tunnel in tunnels:
                    if isinstance(tunnel, dict):
                        if not tunnel.get("underlay_interface"):
                            tunnel["underlay_interface"] = interface_normalized
                        if not tunnel.get("overlay_interface"):
                            tunnel["overlay_interface"] = iface_name
                        tunnel.setdefault("underlay_route_interface", iface_name)
                        if ipv4_gateway:
                            tunnel.setdefault("underlay_gateway", ipv4_gateway)
                        if ipv4_mask:
                            tunnel.setdefault("local_prefix_len", ipv4_mask)
                        validation_errors = vxlan_utils.validate_config(tunnel)
                        if validation_errors:
                            error_msg = f"Invalid VXLAN tunnel configuration: {validation_errors[0]}"
                            logging.error(f"[DEVICE APPLY] {error_msg}")
                            return jsonify({"error": error_msg}), 400
            else:
                # Single tunnel format (backward compatibility)
                if not vxlan_config.get("underlay_interface"):
                    vxlan_config["underlay_interface"] = interface_normalized
                if not vxlan_config.get("overlay_interface"):
                    vxlan_config["overlay_interface"] = iface_name
                vxlan_config.setdefault("underlay_route_interface", iface_name)
                if ipv4_gateway:
                    vxlan_config.setdefault("underlay_gateway", ipv4_gateway)
                if ipv4_mask:
                    vxlan_config.setdefault("local_prefix_len", ipv4_mask)
                validation_errors = vxlan_utils.validate_config(vxlan_config)
                if validation_errors:
                    error_msg = f"Invalid VXLAN configuration: {validation_errors[0]}"
                    logging.error(f"[DEVICE APPLY] {error_msg}")
                    return jsonify({"error": error_msg}), 400
        
        # CRITICAL: Validate interface name when VLAN is not used
        if not iface_name:
            error_msg = "Interface name is required when VLAN is not specified"
            logging.error(f"[DEVICE APPLY] {error_msg}")
            return jsonify({"error": error_msg}), 400
        
        # Step 1: Create VLAN interface if needed
        if vlan and vlan != "0":
            try:
                # CRITICAL: Use normalized interface name for VLAN creation
                if not interface_normalized:
                    error_msg = "Interface name is required for VLAN creation"
                    logging.error(f"[DEVICE APPLY] {error_msg}")
                    return jsonify({"error": error_msg}), 400
                
                # Check if VLAN interface exists with the correct parent interface
                # Linux shows VLAN interfaces as vlan{vlan}@{parent}, but we check using just vlan{vlan}
                # Also check if a standalone vlan{vlan} exists and verify its parent
                vlan_name_only = f"vlan{vlan}"
                check_result = subprocess.run(["ip", "link", "show", vlan_name_only], 
                                            capture_output=True, text=True, timeout=5)
                
                if check_result.returncode != 0:
                    # VLAN interface doesn't exist - create new one
                    # CRITICAL: Use vlan{vlan} as the name, Linux will show it as vlan{vlan}@{parent}
                    # The @ format is only for our internal tracking, not for ip link add
                    vlan_result = subprocess.run([
                        "ip", "link", "add", "link", interface_normalized, "name", vlan_name_only, 
                        "type", "vlan", "id", vlan
                    ], capture_output=True, text=True, timeout=5)
                    
                    if vlan_result.returncode == 0:
                        # CRITICAL: Use just the interface name (without @parent) for commands
                        # Linux shows it as vlan{vlan}@{parent} in ip link show, but commands use just the name
                        iface_name_for_commands = vlan_name_only
                        logging.info(f"[DEVICE APPLY] Created VLAN interface {vlan_name_only} (linked to {interface_normalized})")
                        result["vlan_created"] = True
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to create VLAN interface {iface_name}: {vlan_result.stderr}")
                        result["vlan_created"] = False
                else:
                    # VLAN interface already exists - verify it's linked to correct parent
                    link_output = check_result.stdout
                    if f"@{interface_normalized}" in link_output or f"link/{interface_normalized}" in link_output:
                        # CRITICAL: Use just the interface name (without @parent) for commands
                        # Linux shows it as vlan{vlan}@{parent} in ip link show, but commands use just the name
                        iface_name_for_commands = vlan_name_only
                        logging.info(f"[DEVICE APPLY] VLAN interface {vlan_name_only} already exists and linked to {interface_normalized}")
                        result["vlan_created"] = True
                    else:
                        # VLAN interface exists but linked to different parent
                        # Try to create a new VLAN interface with a unique name that includes the parent interface
                        # This allows the same VLAN ID to be used on multiple interfaces
                        vlan_name_with_parent = f"vlan{vlan}-{interface_normalized}"
                        # Linux interface name limit is 15 characters (IFNAMSIZ)
                        if len(vlan_name_with_parent) > 15:
                            # Truncate parent interface name if needed
                            max_vlan_len = len(f"vlan{vlan}-")
                            max_parent_len = 15 - max_vlan_len
                            if max_parent_len > 0:
                                truncated_parent = interface_normalized[:max_parent_len]
                                vlan_name_with_parent = f"vlan{vlan}-{truncated_parent}"
                            else:
                                # Fallback to simple name (will fail if already exists)
                                vlan_name_with_parent = vlan_name_only
                        
                        logging.info(f"[DEVICE APPLY] VLAN {vlan_name_only} exists on different interface, creating {vlan_name_with_parent} on {interface_normalized}")
                        vlan_result = subprocess.run([
                            "ip", "link", "add", "link", interface_normalized, "name", vlan_name_with_parent, 
                            "type", "vlan", "id", vlan
                        ], capture_output=True, text=True, timeout=5)
                        
                        if vlan_result.returncode == 0:
                            # CRITICAL: Use just the interface name (without @parent) for commands
                            # Linux shows it as vlan_name@{parent} in ip link show, but commands use just the name
                            iface_name_for_commands = vlan_name_with_parent
                            logging.info(f"[DEVICE APPLY] Created VLAN interface {vlan_name_with_parent} (VLAN ID {vlan} on {interface_normalized})")
                            result["vlan_created"] = True
                            # Store the actual interface name for use in OSPF/ISIS configuration
                            result["actual_vlan_interface"] = vlan_name_with_parent
                        else:
                            # If unique name creation failed, try the simple name (might work if previous interface was removed)
                            logging.warning(f"[DEVICE APPLY] Failed to create VLAN interface {vlan_name_with_parent}, trying simple name {vlan_name_only}")
                            vlan_result2 = subprocess.run([
                                "ip", "link", "add", "link", interface_normalized, "name", vlan_name_only, 
                                "type", "vlan", "id", vlan
                            ], capture_output=True, text=True, timeout=5)
                            
                            if vlan_result2.returncode == 0:
                                # CRITICAL: Use just the interface name (without @parent) for commands
                                iface_name_for_commands = vlan_name_only
                                logging.info(f"[DEVICE APPLY] Created VLAN interface {vlan_name_only} using simple name (linked to {interface_normalized})")
                                result["vlan_created"] = True
                            else:
                                error_msg = f"VLAN interface {vlan_name_only} exists but is linked to a different parent interface. Failed to create alternative interface name. Error: {vlan_result.stderr}. Try removing the existing VLAN interface first or use a different VLAN ID."
                                logging.error(f"[DEVICE APPLY] {error_msg}")
                                return jsonify({"error": error_msg}), 400
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error creating VLAN interface {iface_name}: {e}")
                result["vlan_created"] = False
        
        # Step 2: Configure MTU (if provided)
        mtu = data.get("mtu", "1500")
        if mtu:
            try:
                mtu_value = str(mtu).strip()
                if mtu_value and mtu_value.isdigit():
                    mtu_result = subprocess.run(["ip", "link", "set", iface_name_for_commands, "mtu", mtu_value], 
                                              capture_output=True, text=True, timeout=5)
                    if mtu_result.returncode == 0:
                        logging.info(f"[DEVICE APPLY] Set MTU to {mtu_value} on {iface_name_for_commands}")
                        result["mtu_configured"] = True
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to set MTU on {iface_name_for_commands}: {mtu_result.stderr}")
                        result["mtu_configured"] = False
                else:
                    logging.warning(f"[DEVICE APPLY] Invalid MTU value: {mtu_value}")
                    result["mtu_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error setting MTU on {iface_name_for_commands}: {e}")
                result["mtu_configured"] = False

        # Step 3: Bring up interface
        try:
            bringup_result = subprocess.run(["ip", "link", "set", iface_name_for_commands, "up"], 
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

        # Step 4: Configure IPv4 address
        if ipv4 and ipv4_mask:
            try:
                # Remove existing IPv4 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv4}/{ipv4_mask}", "dev", iface_name_for_commands], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv4 address
                ipv4_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv4}/{ipv4_mask}", "dev", iface_name_for_commands
                ], capture_output=True, text=True, timeout=5)
                
                if ipv4_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Configured IPv4 address {ipv4}/{ipv4_mask} on {iface_name_for_commands} (interface: {iface_name})")
                    result["ipv4_configured"] = True
                    
                    # CRITICAL: Clean up duplicate IPv4 subnet routes on interfaces that are DOWN/LOWERLAYERDOWN
                    # This prevents routing issues when multiple interfaces have the same subnet but one is down
                    try:
                        import ipaddress
                        ipv4_network = ipaddress.IPv4Network(f"{ipv4}/{ipv4_mask}", strict=False)
                        subnet = str(ipv4_network)
                        
                        # Get all IPv4 routes for this subnet
                        route_check = subprocess.run(
                            ["ip", "route", "show", subnet],
                            capture_output=True, text=True, timeout=5
                        )
                        
                        if route_check.returncode == 0:
                            for route_line in route_check.stdout.split('\n'):
                                route_line = route_line.strip()
                                if not route_line or subnet not in route_line:
                                    continue
                                
                                # Parse route line: "192.168.0.0/24 dev vlan20 proto kernel metric 256 linkdown"
                                if f"dev {iface_name_for_commands}" in route_line:
                                    continue  # Skip our own interface
                                
                                # Extract interface name from route
                                import re
                                dev_match = re.search(r'dev\s+(\S+)', route_line)
                                if dev_match:
                                    other_iface = dev_match.group(1)
                                    
                                    # Check if this interface is DOWN or LOWERLAYERDOWN
                                    link_check = subprocess.run(
                                        ["ip", "link", "show", other_iface],
                                        capture_output=True, text=True, timeout=5
                                    )
                                    
                                    if link_check.returncode == 0:
                                        link_output = link_check.stdout.lower()
                                        # Check for down states
                                        if "state down" in link_output or "lowerlayerdown" in link_output or "linkdown" in route_line:
                                            # Remove the duplicate route on the down interface
                                            route_del = subprocess.run(
                                                ["ip", "route", "del", subnet, "dev", other_iface],
                                                capture_output=True, text=True, timeout=5
                                            )
                                            if route_del.returncode == 0:
                                                logging.info(f"[DEVICE APPLY] ✅ Removed duplicate IPv4 subnet route {subnet} from down interface {other_iface}")
                                            else:
                                                logging.debug(f"[DEVICE APPLY] Could not remove duplicate route from {other_iface}: {route_del.stderr}")
                    except Exception as cleanup_exc:
                        logging.warning(f"[DEVICE APPLY] Error cleaning up duplicate IPv4 routes: {cleanup_exc}")
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to configure IPv4 address {ipv4}/{ipv4_mask} on {iface_name_for_commands}: {ipv4_result.stderr}")
                    result["ipv4_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring IPv4 address: {e}")
                result["ipv4_configured"] = False
        
        # Step 4: Configure IPv6 address
        if ipv6 and ipv6_mask:
            try:
                # Remove existing IPv6 address if any
                subprocess.run(["ip", "addr", "del", f"{ipv6}/{ipv6_mask}", "dev", iface_name_for_commands], 
                             capture_output=True, text=True, timeout=5)
                
                # Add new IPv6 address
                ipv6_result = subprocess.run([
                    "ip", "addr", "add", f"{ipv6}/{ipv6_mask}", "dev", iface_name_for_commands
                ], capture_output=True, text=True, timeout=5)
                
                if ipv6_result.returncode == 0:
                    logging.info(f"[DEVICE APPLY] Configured IPv6 address {ipv6}/{ipv6_mask} on {iface_name_for_commands} (interface: {iface_name})")
                    result["ipv6_configured"] = True
                    
                    # CRITICAL: Clean up duplicate IPv6 subnet routes on interfaces that are DOWN/LOWERLAYERDOWN
                    # This prevents routing issues when multiple interfaces have the same subnet but one is down
                    try:
                        import ipaddress
                        ipv6_network = ipaddress.IPv6Network(f"{ipv6}/{ipv6_mask}", strict=False)
                        subnet = str(ipv6_network)
                        
                        # Get all IPv6 routes for this subnet
                        route_check = subprocess.run(
                            ["ip", "-6", "route", "show", subnet],
                            capture_output=True, text=True, timeout=5
                        )
                        
                        if route_check.returncode == 0:
                            for route_line in route_check.stdout.split('\n'):
                                route_line = route_line.strip()
                                if not route_line or subnet not in route_line:
                                    continue
                                
                                # Parse route line: "2001:db8::/64 dev vlan20 proto kernel metric 256 linkdown pref medium"
                                if f"dev {iface_name_for_commands}" in route_line:
                                    continue  # Skip our own interface
                                
                                # Extract interface name from route
                                import re
                                dev_match = re.search(r'dev\s+(\S+)', route_line)
                                if dev_match:
                                    other_iface = dev_match.group(1)
                                    
                                    # Check if this interface is DOWN or LOWERLAYERDOWN
                                    link_check = subprocess.run(
                                        ["ip", "link", "show", other_iface],
                                        capture_output=True, text=True, timeout=5
                                    )
                                    
                                    if link_check.returncode == 0:
                                        link_output = link_check.stdout.lower()
                                        # Check for down states
                                        if "state down" in link_output or "lowerlayerdown" in link_output or "linkdown" in route_line:
                                            # Remove the duplicate route on the down interface
                                            route_del = subprocess.run(
                                                ["ip", "-6", "route", "del", subnet, "dev", other_iface],
                                                capture_output=True, text=True, timeout=5
                                            )
                                            if route_del.returncode == 0:
                                                logging.info(f"[DEVICE APPLY] ✅ Removed duplicate IPv6 subnet route {subnet} from down interface {other_iface}")
                                            else:
                                                logging.debug(f"[DEVICE APPLY] Could not remove duplicate route from {other_iface}: {route_del.stderr}")
                    except Exception as cleanup_exc:
                        logging.warning(f"[DEVICE APPLY] Error cleaning up duplicate IPv6 routes: {cleanup_exc}")
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to configure IPv6 address {ipv6}/{ipv6_mask} on {iface_name_for_commands}: {ipv6_result.stderr}")
                    result["ipv6_configured"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring IPv6 address: {e}")
                result["ipv6_configured"] = False
        
        # Step 5: Add host routes to gateways (for reachability) but NEVER add default routes on host
        # Default routes should only be added inside FRR containers, not on the host system
        # Adding default routes on the host would overwrite the management default route and break connectivity
        if ipv4_gateway:
            try:
                # Add host route to gateway on the interface to make it directly reachable
                # This allows the device to reach its gateway without affecting the system default route
                # CRITICAL: Specify source IP to ensure correct source address selection when multiple IPs exist on interface
                gateway_host_route = subprocess.run([
                    "ip", "route", "replace", f"{ipv4_gateway}/32", "dev", iface_name_for_commands, "src", ipv4
                ], capture_output=True, text=True, timeout=5)
                if gateway_host_route.returncode == 0:
                    logging.debug(f"[DEVICE APPLY] Added host route to IPv4 gateway {ipv4_gateway}/32 on {iface_name}")
                    result["ipv4_gateway_route_added"] = True
                    
                    # CRITICAL: Ensure gateway ARP entry is kernel-managed, not zebra-managed
                    # Zebra-managed ARP entries can cause ping failures
                    try:
                        # Check if ARP entry exists and is zebra-managed
                        neigh_check = subprocess.run(
                            ["ip", "neigh", "show", ipv4_gateway, "dev", iface_name_for_commands],
                            capture_output=True, text=True, timeout=5
                        )
                        if neigh_check.returncode == 0 and neigh_check.stdout:
                            neigh_output = neigh_check.stdout.strip()
                            # Check if it's zebra-managed or FAILED
                            if "proto zebra" in neigh_output or "FAILED" in neigh_output or "NOARP" in neigh_output:
                                # Delete the zebra-managed/failed ARP entry
                                subprocess.run(
                                    ["ip", "neigh", "del", ipv4_gateway, "dev", iface_name_for_commands],
                                    capture_output=True, text=True, timeout=5
                                )
                                logging.info(f"[DEVICE APPLY] Deleted zebra-managed/failed ARP entry for gateway {ipv4_gateway}")
                                import time
                                time.sleep(0.2)  # Brief delay after deletion
                        
                        # Try to resolve gateway MAC address via ARP
                        arp_resolve = subprocess.run(
                            ["ping", "-c", "1", "-W", "1", ipv4_gateway],
                            capture_output=True, text=True, timeout=3
                        )
                        
                        # Check ARP table again to get the MAC address
                        neigh_check2 = subprocess.run(
                            ["ip", "neigh", "show", ipv4_gateway, "dev", iface_name_for_commands],
                            capture_output=True, text=True, timeout=5
                        )
                        if neigh_check2.returncode == 0 and neigh_check2.stdout:
                            # Extract MAC address from ARP entry
                            import re
                            mac_match = re.search(r'lladdr\s+([0-9a-fA-F:]{17})', neigh_check2.stdout)
                            if mac_match:
                                gateway_mac = mac_match.group(1)
                                # Create permanent kernel-managed ARP entry
                                subprocess.run([
                                    "ip", "neigh", "replace", ipv4_gateway,
                                    "lladdr", gateway_mac,
                                    "dev", iface_name_for_commands,
                                    "nud", "permanent"
                                ], capture_output=True, text=True, timeout=5)
                                logging.info(f"[DEVICE APPLY] ✅ Configured permanent kernel-managed ARP entry for gateway {ipv4_gateway} -> {gateway_mac}")
                            else:
                                logging.warning(f"[DEVICE APPLY] Could not extract MAC address from ARP entry for gateway {ipv4_gateway}")
                    except Exception as arp_exc:
                        logging.warning(f"[DEVICE APPLY] Error configuring gateway ARP entry: {arp_exc}")
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to add host route to IPv4 gateway: {gateway_host_route.stderr}")
                    result["ipv4_gateway_route_added"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error adding host route to IPv4 gateway: {e}")
                result["ipv4_gateway_route_added"] = False
        
        if ipv6_gateway:
            try:
                # Add host route to IPv6 gateway on the interface to make it directly reachable
                # This allows the device to reach its gateway without affecting the system default route
                # CRITICAL: Specify source IP to ensure correct source address selection when multiple IPs exist on interface
                gateway6_host_route = subprocess.run([
                    "ip", "-6", "route", "replace", f"{ipv6_gateway}/128", "dev", iface_name_for_commands, "src", ipv6
                ], capture_output=True, text=True, timeout=5)
                if gateway6_host_route.returncode == 0:
                    logging.debug(f"[DEVICE APPLY] Added host route to IPv6 gateway {ipv6_gateway}/128 on {iface_name}")
                    result["ipv6_gateway_route_added"] = True
                    
                    # CRITICAL: Ensure IPv6 gateway neighbor entry is kernel-managed, not zebra-managed
                    try:
                        # Check if neighbor entry exists and is zebra-managed
                        neigh_check = subprocess.run(
                            ["ip", "-6", "neigh", "show", ipv6_gateway, "dev", iface_name_for_commands],
                            capture_output=True, text=True, timeout=5
                        )
                        if neigh_check.returncode == 0 and neigh_check.stdout:
                            neigh_output = neigh_check.stdout.strip()
                            # Check if it's zebra-managed or FAILED
                            if "proto zebra" in neigh_output or "FAILED" in neigh_output:
                                # Delete the zebra-managed/failed neighbor entry
                                subprocess.run(
                                    ["ip", "-6", "neigh", "del", ipv6_gateway, "dev", iface_name_for_commands],
                                    capture_output=True, text=True, timeout=5
                                )
                                logging.info(f"[DEVICE APPLY] Deleted zebra-managed/failed IPv6 neighbor entry for gateway {ipv6_gateway}")
                                import time
                                time.sleep(0.2)  # Brief delay after deletion
                        
                        # Try to resolve IPv6 gateway MAC address via neighbor discovery
                        nd_resolve = subprocess.run(
                            ["ping6", "-c", "1", "-W", "1", ipv6_gateway],
                            capture_output=True, text=True, timeout=3
                        )
                        
                        # Check neighbor table again to get the MAC address
                        neigh_check2 = subprocess.run(
                            ["ip", "-6", "neigh", "show", ipv6_gateway, "dev", iface_name_for_commands],
                            capture_output=True, text=True, timeout=5
                        )
                        if neigh_check2.returncode == 0 and neigh_check2.stdout:
                            # Extract MAC address from neighbor entry
                            import re
                            mac_match = re.search(r'lladdr\s+([0-9a-fA-F:]{17})', neigh_check2.stdout)
                            if mac_match:
                                gateway_mac = mac_match.group(1)
                                # Create permanent kernel-managed neighbor entry
                                subprocess.run([
                                    "ip", "-6", "neigh", "replace", ipv6_gateway,
                                    "lladdr", gateway_mac,
                                    "dev", iface_name_for_commands,
                                    "nud", "permanent"
                                ], capture_output=True, text=True, timeout=5)
                                logging.info(f"[DEVICE APPLY] ✅ Configured permanent kernel-managed IPv6 neighbor entry for gateway {ipv6_gateway} -> {gateway_mac}")
                            else:
                                logging.warning(f"[DEVICE APPLY] Could not extract MAC address from IPv6 neighbor entry for gateway {ipv6_gateway}")
                    except Exception as arp_exc:
                        logging.warning(f"[DEVICE APPLY] Error configuring IPv6 gateway neighbor entry: {arp_exc}")
                else:
                    logging.warning(f"[DEVICE APPLY] Failed to add host route to IPv6 gateway: {gateway6_host_route.stderr}")
                    result["ipv6_gateway_route_added"] = False
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error adding host route to IPv6 gateway: {e}")
                result["ipv6_gateway_route_added"] = False
        
        # Step 6: Ensure FRR container exists (even for VXLAN-only devices) and configure loopback IPs
        needs_frr_container = bool(protocols) or bool(dhcp_mode) or vxlan_enabled
        container_exists = False
        container = None
        container_name = ""
        frr_manager = None
        if needs_frr_container:
            try:
                from utils.frr_docker import FRRDockerManager
                frr_manager = FRRDockerManager()
                container_name = frr_manager._get_container_name(device_id, device_name)
                try:
                    container = frr_manager.client.containers.get(container_name)
                    if container.status == "running":
                        container_exists = True
                        logging.info(f"[DEVICE APPLY] FRR container {container_name} exists and is running, will configure loopback inside container")
                    else:
                        logging.info(f"[DEVICE APPLY] FRR container {container_name} exists but is not running, recreating")
                        try:
                            container.remove(force=True)
                        except Exception as remove_exc:
                            logging.debug(f"[DEVICE APPLY] Failed to remove stale container {container_name}: {remove_exc}")
                        container = None
                except Exception:
                    logging.info(f"[DEVICE APPLY] FRR container {container_name} does not exist, creating it")
                    container = None

                if not container_exists:
                    container_device_config = {
                        "device_name": device_name or f"device_{device_id}",
                        "interface": interface_normalized,
                        "vlan": vlan,
                        "mtu": mtu,  # Include MTU in container config
                        "ipv4": f"{ipv4}/{ipv4_mask}" if ipv4 and ipv4_mask else ipv4,
                        "ipv6": f"{ipv6}/{ipv6_mask}" if ipv6 and ipv6_mask else ipv6,
                        "loopback_ipv4": loopback_ipv4,
                        "loopback_ipv6": loopback_ipv6,
                        "dhcp_mode": dhcp_mode,
                        "bgp_asn": (bgp_config.get("bgp_asn") if isinstance(bgp_config, dict) and bgp_config.get("bgp_asn") else 65000),
                        "vxlan_config": vxlan_config if vxlan_enabled else {},
                    }
                    created_container_name = frr_manager.start_frr_container(device_id, container_device_config)
                    if created_container_name:
                        try:
                            container = frr_manager.client.containers.get(created_container_name)
                            container_name = created_container_name
                            container_exists = True
                            logging.info(f"[DEVICE APPLY] Created FRR container {created_container_name} for device {device_name}")
                        except Exception as retrieve_exc:
                            logging.warning(f"[DEVICE APPLY] FRR container {created_container_name} started but could not be inspected: {retrieve_exc}")
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to start FRR container for device {device_name}")
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Could not ensure FRR container: {e}, will configure loopback on host")
                container_exists = False
                container = None
        else:
            logging.info(f"[DEVICE APPLY] Skipping FRR container provisioning (protocol-less host configuration)")
        
        # CRITICAL: If client didn't send loopback but IPv4 is available, fallback to IPv4 for loopback
        # This ensures loopback is always set (for FRR router-id and loopback interface)
        original_loopback_ipv4 = loopback_ipv4  # Track if it was originally provided
        if (not loopback_ipv4) and ipv4:
            try:
                loopback_ipv4 = ipv4.split("/")[0] if "/" in ipv4 else ipv4
                logging.info(f"[DEVICE APPLY] Loopback IPv4 not provided; using interface IPv4 as fallback: {loopback_ipv4}/32")
            except Exception:
                pass
        
        # Configure loopback IPs using FRR vtysh commands (if container exists)
        logging.info(f"[DEVICE APPLY] Checking loopback configuration: loopback_ipv4={loopback_ipv4}, loopback_ipv6={loopback_ipv6}, container_exists={container_exists}, container={container}")
        # CRITICAL: Always configure loopback if container exists, even if loopback IPs are not explicitly provided
        # The _configure_interfaces method will use fallback logic (interface IP, router_id, or default)
        if container_exists and container:
            logging.info(f"[DEVICE APPLY] Container exists, will configure loopback (with fallback if needed)")
        elif loopback_ipv4 or loopback_ipv6:
            logging.info(f"[DEVICE APPLY] Loopback IPs provided but container doesn't exist yet")
        
        if (container_exists and container) or (loopback_ipv4 or loopback_ipv6):
            try:
                if container_exists and container:
                    # Reconfigure interfaces (including loopback) inside FRR container
                    logging.info(f"[DEVICE APPLY] Reconfiguring interfaces (including loopback) via vtysh in container {container_name}")
                    
                    # Build device config for interface configuration
                    interface_config = {
                        "device_name": device_name or f"device_{device_id}",
                        "interface": interface_normalized,
                        "vlan": vlan,
                        "mtu": mtu,  # Include MTU in interface config
                        "ipv4": f"{ipv4}/{ipv4_mask}" if ipv4 and ipv4_mask else ipv4,
                        "ipv6": f"{ipv6}/{ipv6_mask}" if ipv6 and ipv6_mask else ipv6,
                        "loopback_ipv4": loopback_ipv4,
                        "loopback_ipv6": loopback_ipv6,
                        "dhcp_mode": dhcp_mode,
                    }
                    
                    # Use _configure_interfaces to ensure loopback is properly configured
                    logging.info(f"[DEVICE APPLY] Calling _configure_interfaces with loopback_ipv4={loopback_ipv4}, loopback_ipv6={loopback_ipv6}")
                    if frr_manager._configure_interfaces(container_name, device_id, interface_config):
                        logging.info(f"[DEVICE APPLY] ✅ Successfully configured loopback {loopback_ipv4}/32 in container {container_name}")
                        if loopback_ipv4:
                            result["loopback_ipv4_configured"] = True
                        if loopback_ipv6:
                            result["loopback_ipv6_configured"] = True
                        
                        # Verify loopback is in running config
                        verify_cmd = f"vtysh -c 'show running-config' | grep -A 3 'interface lo'"
                        verify_result = container.exec_run(["bash", "-c", verify_cmd])
                        verify_output = verify_result.output.decode('utf-8') if isinstance(verify_result.output, bytes) else str(verify_result.output)
                        logging.info(f"[DEVICE APPLY] Loopback verification in running config: {verify_output}")
                    else:
                        logging.warning(f"[DEVICE APPLY] Failed to configure loopback in container {container_name}, trying direct vtysh")
                        
                        # Fallback: Configure loopback directly via vtysh
                        vtysh_commands = [
                            "configure terminal",
                            "interface lo",
                        ]
                        
                        # Configure IPv4 loopback if provided
                        if loopback_ipv4:
                            vtysh_commands.append(f" ip address {loopback_ipv4}/32")
                            logging.info(f"[DEVICE APPLY] Adding loopback IPv4 {loopback_ipv4}/32 via vtysh")
                        
                        # Configure IPv6 loopback if provided
                        if loopback_ipv6:
                            vtysh_commands.append(f" ipv6 address {loopback_ipv6}/128")
                            logging.info(f"[DEVICE APPLY] Adding loopback IPv6 {loopback_ipv6}/128 via vtysh")
                        
                        vtysh_commands.extend([
                            "exit",
                            "exit",
                            "write memory"
                        ])
                        
                        # CRITICAL: Ensure mgmtd is running before attempting vtysh commands
                        # FRR 10.0 with integrated-vtysh-config requires mgmtd to be running
                        mgmtd_check = container.exec_run(["bash", "-c", "pgrep -f mgmtd > /dev/null && echo 'running' || echo 'not_running'"])
                        mgmtd_output = mgmtd_check.output.decode('utf-8') if isinstance(mgmtd_check.output, bytes) else str(mgmtd_check.output)
                        if 'running' not in mgmtd_output.strip():
                            logging.warning(f"[DEVICE APPLY] mgmtd is not running, attempting to start it manually")
                            container.exec_run(["bash", "-c", "/usr/lib/frr/mgmtd -d -A 127.0.0.1 2>&1 || true"])
                            time.sleep(2)  # Give mgmtd time to start
                        
                        # Execute commands using here-doc to maintain context
                        config_commands = "\n".join(vtysh_commands)
                        exec_cmd = f"vtysh << 'EOF'\n{config_commands}\nEOF"
                        
                        logging.info(f"[DEVICE APPLY] Executing loopback configuration via vtysh")
                        loopback_result = container.exec_run(["bash", "-c", exec_cmd])
                        
                        if loopback_result.exit_code == 0:
                            if loopback_ipv4:
                                logging.info(f"[DEVICE APPLY] ✅ Configured loopback IPv4 address {loopback_ipv4}/32 via vtysh in container {container_name}")
                                result["loopback_ipv4_configured"] = True
                            if loopback_ipv6:
                                logging.info(f"[DEVICE APPLY] ✅ Configured loopback IPv6 address {loopback_ipv6}/128 via vtysh in container {container_name}")
                                result["loopback_ipv6_configured"] = True
                        else:
                            output_str = loopback_result.output.decode('utf-8') if isinstance(loopback_result.output, bytes) else str(loopback_result.output)
                            logging.warning(f"[DEVICE APPLY] Failed to configure loopback IPs via vtysh in container: {output_str}")
                            if loopback_ipv4:
                                result["loopback_ipv4_configured"] = False
                            if loopback_ipv6:
                                result["loopback_ipv6_configured"] = False
                else:
                    # Container doesn't exist yet - loopback will be configured later during protocol setup
                    logging.info(f"[DEVICE APPLY] FRR container not available yet, loopback IPs will be configured during protocol setup")
                    if loopback_ipv4:
                        result["loopback_ipv4_configured"] = None  # Will be configured later
                    if loopback_ipv6:
                        result["loopback_ipv6_configured"] = None  # Will be configured later
            except Exception as e:
                logging.warning(f"[DEVICE APPLY] Error configuring loopback IPs via vtysh: {e}")
                import traceback
                logging.warning(f"[DEVICE APPLY] Traceback: {traceback.format_exc()}")
                if loopback_ipv4:
                    result["loopback_ipv4_configured"] = False
                if loopback_ipv6:
                    result["loopback_ipv6_configured"] = False
        
        # Configure VXLAN BEFORE routing protocols to ensure zebra knows about the interface
        # This is critical for BGP EVPN to work correctly with advertise-all-vni
        if vxlan_enabled:
            logging.info(f"[DEVICE APPLY] VXLAN enabled, calling ensure_vxlan_interface for device {device_id}")
            logging.info(f"[DEVICE APPLY] VXLAN config passed to ensure_vxlan_interface: {vxlan_config}")
            logging.info(f"[DEVICE APPLY] Container exists: {container_exists}, container_name: {container_name}")
            try:
                # Handle multiple tunnels format
                if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
                    tunnels = vxlan_config.get("tunnels", [])
                    vxlan_interfaces = []
                    vxlan_errors = []
                    updated_tunnels = []
                    for tunnel in tunnels:
                        if isinstance(tunnel, dict):
                            tunnel_result = vxlan_utils.ensure_vxlan_interface(
                                device_id,
                                device_name or device_id,
                                tunnel,
                                container_name=container_name if container_exists else None,
                                frr_manager=frr_manager,
                            )
                            if tunnel_result.get("success"):
                                # Update tunnel config with the interface name and any other returned config
                                updated_tunnel = tunnel_result.get("config", tunnel)
                                updated_tunnel["vxlan_interface"] = tunnel_result.get("interface", "")
                                updated_tunnels.append(updated_tunnel)
                                vxlan_interfaces.append(tunnel_result.get("interface", ""))
                            else:
                                # Keep original tunnel config even if it failed
                                updated_tunnels.append(tunnel)
                                vxlan_errors.append(tunnel_result.get("error", "Unknown VXLAN error"))
                    # Update vxlan_config with the updated tunnels (including interface names)
                    vxlan_config["tunnels"] = updated_tunnels
                    if vxlan_interfaces:
                        vxlan_interface = ", ".join(vxlan_interfaces)
                        vxlan_state = "Configured" if not vxlan_errors else "Partial"
                        vxlan_error = "; ".join(vxlan_errors) if vxlan_errors else ""
                        result["vxlan_interface"] = vxlan_interface
                    else:
                        vxlan_state = "Error"
                        vxlan_error = "; ".join(vxlan_errors) if vxlan_errors else "All tunnels failed"
                        result["vxlan_error"] = vxlan_error
                else:
                    # Single tunnel format (backward compatibility)
                    vxlan_result = vxlan_utils.ensure_vxlan_interface(
                        device_id,
                        device_name or device_id,
                        vxlan_config,
                        container_name=container_name if container_exists else None,
                        frr_manager=frr_manager,
                    )
                    logging.info(f"[DEVICE APPLY] ensure_vxlan_interface returned: {vxlan_result}")
                    if vxlan_result.get("success"):
                        vxlan_config = vxlan_result.get("config", vxlan_config) or vxlan_config
                        vxlan_interface = vxlan_result.get("interface", "")
                        vxlan_state = "Configured"
                        vxlan_error = ""
                        result["vxlan_interface"] = vxlan_interface
                    else:
                        vxlan_state = "Error"
                        vxlan_error = vxlan_result.get("error", "Unknown VXLAN error")
                        result["vxlan_error"] = vxlan_error
            except Exception as vxlan_exc:
                vxlan_state = "Error"
                vxlan_error = str(vxlan_exc)
                logging.error(f"[DEVICE APPLY] VXLAN setup failed for {device_id}: {vxlan_exc}", exc_info=True)
                result["vxlan_error"] = vxlan_error
        
        # CRITICAL: Save/update device in database BEFORE starting protocol config thread
        # so that BGP EVPN can read VXLAN config from the database
        try:
            if device_id:
                existing_device = device_db.get_device(device_id)
                device_data = {
                    "device_id": device_id,
                    "device_name": device_name,
                    "interface": interface_normalized,  # CRITICAL: Use normalized interface name
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
                    "isis_config": isis_config,
                    "dhcp_config": dhcp_config,
                    "dhcp_mode": dhcp_config.get("mode") if isinstance(dhcp_config, dict) else "",
                    "vxlan_config": vxlan_config,
                    "vxlan_state": vxlan_state,
                    "vxlan_interface": vxlan_interface,
                    "vxlan_enabled": vxlan_enabled,
                    "vxlan_last_error": vxlan_error,
                    "vxlan_updated_at": datetime.now(timezone.utc).isoformat(),
                    # CRITICAL: Store actual VLAN interface name if unique-named interface was created
                    "actual_vlan_interface": result.get("actual_vlan_interface", ""),
                }
                if not existing_device:
                    logging.info(f"[DEVICE APPLY] Device {device_id} not found in database, adding it")
                    device_db.add_device(device_data)
                else:
                    logging.info(f"[DEVICE APPLY] Updating device {device_id} in database with VXLAN config")
                    device_db.update_device(device_id, device_data)
        except Exception as db_exc:
            logging.warning(f"[DEVICE DB] Failed to save device {device_id} to database before protocol config: {db_exc}")
        
        # Configure routing protocols if container exists and configs are provided
        # Use unified helper function to ensure consistency across all code paths
        # Run in background thread to avoid HTTP timeout (protocol config can take 30+ seconds)
        # NOTE: VXLAN must be configured BEFORE this point so zebra knows about the interface
        # Database is now updated above, so BGP EVPN can read VXLAN config
        if container_exists and container:
            import threading
            def _configure_protocols_background():
                try:
                    # Small delay to ensure database is updated with VXLAN config
                    import time
                    # CRITICAL: Wait longer if VXLAN is enabled to ensure bridge SVI is fully configured
                    # and recognized by FRR's EVPN daemon before BGP EVPN enables advertise-all-vni
                    if vxlan_enabled:
                        time.sleep(10)  # Give FRR more time to recognize the bridge SVI (increased from 5 to 10 seconds)
                        # Additional verification: Check if bridge SVI is recognized by querying EVPN VNI
                        # This helps ensure the EVPN daemon has processed the bridge SVI configuration
                        try:
                            from utils.frr_docker import FRRDockerManager
                            frr_mgr = FRRDockerManager()
                            container_name = frr_mgr._get_container_name(device_id, device_name)
                            import docker
                            container = frr_mgr.client.containers.get(container_name)
                            # Query EVPN VNI to see if SVI is recognized (non-blocking check)
                            evpn_check = container.exec_run(["vtysh", "-c", "show evpn vni detail"], timeout=5)
                            evpn_output = evpn_check.output.decode("utf-8", errors="ignore") if isinstance(evpn_check.output, bytes) else str(evpn_check.output)
                            if "SVI interface:" in evpn_output:
                                logging.info(f"[DEVICE APPLY] Bridge SVI appears to be recognized by EVPN daemon")
                            else:
                                logging.warning(f"[DEVICE APPLY] Bridge SVI may not be recognized yet - EVPN daemon may need more time")
                        except Exception as evpn_check_exc:
                            logging.debug(f"[DEVICE APPLY] Could not verify EVPN SVI recognition: {evpn_check_exc}")
                        logging.info(f"[DEVICE APPLY] Waited 10 seconds for VXLAN bridge SVI to be recognized before configuring BGP EVPN")
                    else:
                        time.sleep(1)
                    protocol_results = _configure_routing_protocols(
                        device_id=device_id,
                        device_name=device_name,
                        bgp_config=bgp_config,
                        ospf_config=ospf_config,
                        isis_config=isis_config,
                        ipv4=ipv4,
                        ipv6=ipv6,
                        ipv4_mask=ipv4_mask,
                        ipv6_mask=ipv6_mask,
                        dhcp_mode=dhcp_mode
                    )
                    logging.info(f"[DEVICE APPLY] Protocol configuration results: {protocol_results}")
                    
                    # Configure ARP and FDB entries from EVPN routes after BGP EVPN is configured
                    # This is critical for VXLAN connectivity - wait for EVPN routes to propagate
                    if vxlan_enabled and vxlan_config and bgp_config:
                        try:
                            import time
                            logging.info(f"[DEVICE APPLY] Waiting 10 seconds for EVPN routes to propagate before configuring ARP/FDB")
                            time.sleep(10)  # Wait for EVPN routes to be exchanged
                            
                            # Call configure_vxlan_arp_fdb_from_evpn for each tunnel
                            if isinstance(vxlan_config, dict) and "tunnels" in vxlan_config:
                                tunnels = vxlan_config.get("tunnels", [])
                                for tunnel in tunnels:
                                    if isinstance(tunnel, dict):
                                        try:
                                            vxlan_utils.configure_vxlan_arp_fdb_from_evpn(
                                                device_id=device_id,
                                                vxlan_config=tunnel,
                                                container_name=container_name if container_exists else None,
                                                frr_manager=frr_manager,
                                            )
                                            logging.info(f"[DEVICE APPLY] Configured ARP/FDB from EVPN for tunnel VNI {tunnel.get('vni')}")
                                        except Exception as arp_fdb_exc:
                                            logging.warning(f"[DEVICE APPLY] Failed to configure ARP/FDB from EVPN for tunnel VNI {tunnel.get('vni')}: {arp_fdb_exc}")
                            else:
                                # Single tunnel format (old format)
                                try:
                                    vxlan_utils.configure_vxlan_arp_fdb_from_evpn(
                                        device_id=device_id,
                                        vxlan_config=vxlan_config,
                                        container_name=container_name if container_exists else None,
                                        frr_manager=frr_manager,
                                    )
                                    logging.info(f"[DEVICE APPLY] Configured ARP/FDB from EVPN for VXLAN tunnel")
                                except Exception as arp_fdb_exc:
                                    logging.warning(f"[DEVICE APPLY] Failed to configure ARP/FDB from EVPN: {arp_fdb_exc}")
                        except Exception as evpn_arp_fdb_exc:
                            logging.warning(f"[DEVICE APPLY] Error configuring ARP/FDB from EVPN routes: {evpn_arp_fdb_exc}")
                except Exception as protocol_exc:
                    logging.error(f"[DEVICE APPLY] Error in background protocol configuration: {protocol_exc}", exc_info=True)
            
            # Start protocol configuration in background thread
            protocol_thread = threading.Thread(target=_configure_protocols_background, daemon=True)
            protocol_thread.start()
            logging.info(f"[DEVICE APPLY] Started protocol configuration in background thread for device {device_name}")
        
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
                        "interface": interface_normalized,  # CRITICAL: Use normalized interface name
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
                        "isis_config": isis_config,
                        "dhcp_config": dhcp_config,
                        "dhcp_mode": dhcp_config.get("mode") if isinstance(dhcp_config, dict) else "",
                        "vxlan_config": vxlan_config,
                        "vxlan_state": vxlan_state,
                        "vxlan_interface": vxlan_interface,
                        "vxlan_enabled": vxlan_enabled,
                        "vxlan_last_error": vxlan_error,
                        "vxlan_updated_at": datetime.now(timezone.utc).isoformat(),
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
                    
                    # CRITICAL: Always update loopback IP addresses if they have values
                    # This includes fallback values set from interface IP (see fallback logic above)
                    # The fallback logic ensures loopback_ipv4 is always set if IPv4 is available
                    if loopback_ipv4:
                        existing_loopback_ipv4 = existing_device.get("loopback_ipv4", "")
                        if existing_loopback_ipv4 != loopback_ipv4:
                            logging.info(f"[DEVICE APPLY] Loopback IPv4 address changed from '{existing_loopback_ipv4}' to '{loopback_ipv4}' for device {device_name}")
                        update_data["loopback_ipv4"] = loopback_ipv4
                        logging.info(f"[DEVICE APPLY] Saving loopback_ipv4={loopback_ipv4} to database for device {device_name}")
                    else:
                        # If loopback IPv4 is empty (and no fallback was possible), clear it from database
                        if existing_device.get("loopback_ipv4"):
                            logging.info(f"[DEVICE APPLY] Clearing loopback IPv4 address for device {device_name}")
                            update_data["loopback_ipv4"] = None
                    
                    if loopback_ipv6:
                        existing_loopback_ipv6 = existing_device.get("loopback_ipv6", "")
                        if existing_loopback_ipv6 != loopback_ipv6:
                            logging.info(f"[DEVICE APPLY] Loopback IPv6 address changed from '{existing_loopback_ipv6}' to '{loopback_ipv6}' for device {device_name}")
                        update_data["loopback_ipv6"] = loopback_ipv6
                        logging.info(f"[DEVICE APPLY] Saving loopback_ipv6={loopback_ipv6} to database for device {device_name}")
                    else:
                        # If loopback IPv6 is empty, clear it from database
                        if existing_device.get("loopback_ipv6"):
                            logging.info(f"[DEVICE APPLY] Clearing loopback IPv6 address for device {device_name}")
                            update_data["loopback_ipv6"] = None
                    
                    # Also update interface and VLAN if they changed
                    # CRITICAL: Use normalized interface name for database storage
                    if interface_normalized and interface_normalized != existing_device.get("interface", ""):
                        update_data["interface"] = interface_normalized
                    if vlan and vlan != existing_device.get("vlan", "0"):
                        update_data["vlan"] = vlan
                    
                    # CRITICAL: Store the actual VLAN interface name if a unique-named interface was created
                    # This is needed for OSPF/ISIS configuration when vlan20-ens4np0 is created instead of vlan20
                    if result.get("actual_vlan_interface"):
                        actual_vlan_iface = result["actual_vlan_interface"]
                        update_data["actual_vlan_interface"] = actual_vlan_iface
                        logging.info(f"[DEVICE APPLY] Storing actual VLAN interface name '{actual_vlan_iface}' in database for OSPF/ISIS configuration")
                    
                    # Update protocol configs if provided
                    if bgp_config:
                        update_data["bgp_config"] = bgp_config
                        logging.info(f"[DEVICE APPLY] Updating BGP config for device {device_name}")
                    if ospf_config:
                        # Merge with existing OSPF config to preserve fields like graceful_restart
                        existing_device = device_db.get_device(device_id)
                        existing_ospf_config = existing_device.get("ospf_config", {}) if existing_device else {}
                        if isinstance(existing_ospf_config, str):
                            import json
                            try:
                                existing_ospf_config = json.loads(existing_ospf_config)
                            except Exception:
                                existing_ospf_config = {}
                        
                        merged_ospf_config = existing_ospf_config.copy() if existing_ospf_config else {}
                        merged_ospf_config.update(ospf_config)  # New values override existing ones
                        # Ensure graceful_restart fields are preserved if not explicitly set
                        if "graceful_restart_ipv4" not in ospf_config and "graceful_restart_ipv4" in existing_ospf_config:
                            merged_ospf_config["graceful_restart_ipv4"] = existing_ospf_config["graceful_restart_ipv4"]
                        if "graceful_restart_ipv6" not in ospf_config and "graceful_restart_ipv6" in existing_ospf_config:
                            merged_ospf_config["graceful_restart_ipv6"] = existing_ospf_config["graceful_restart_ipv6"]
                        # Also preserve graceful_restart for backward compatibility
                        if "graceful_restart" not in ospf_config and "graceful_restart" in existing_ospf_config:
                            merged_ospf_config["graceful_restart"] = existing_ospf_config["graceful_restart"]
                        
                        update_data["ospf_config"] = merged_ospf_config
                        logging.info(f"[DEVICE APPLY] Updating OSPF config for device {device_name} (graceful_restart: {merged_ospf_config.get('graceful_restart', False)})")
                    if isis_config:
                        update_data["isis_config"] = isis_config
                        update_data["is_is_config"] = isis_config  # Also update is_is_config for compatibility
                        logging.info(f"[DEVICE APPLY] Updating ISIS config for device {device_name}")
                    if dhcp_config:
                        update_data["dhcp_config"] = dhcp_config
                        update_data["dhcp_mode"] = dhcp_config.get("mode") if isinstance(dhcp_config, dict) else ""
                        logging.info(f"[DEVICE APPLY] Updating DHCP config for device {device_name}: mode={dhcp_config.get('mode')}")
                    elif existing_device.get("dhcp_mode") and ("DHCP" not in protocols):
                        update_data["dhcp_config"] = {}
                        update_data["dhcp_mode"] = ""
                        update_data["dhcp_state"] = "Disabled"
                        update_data["dhcp_running"] = False
                    
                    # Always update VXLAN fields if VXLAN protocol is present or config provided
                    if ("VXLAN" in protocols) or vxlan_config or existing_device.get("vxlan_config"):
                        update_data.update(
                            {
                                "vxlan_config": vxlan_config or existing_device.get("vxlan_config", {}),
                                "vxlan_state": vxlan_state,
                                "vxlan_interface": vxlan_interface,
                                "vxlan_enabled": vxlan_enabled,
                                "vxlan_last_error": vxlan_error,
                                "vxlan_updated_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                    
                    # Update protocols list if provided
                    if protocols:
                        update_data["protocols"] = protocols
                        logging.info(f"[DEVICE APPLY] Updating protocols list for device {device_name}: {protocols}")
                    
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
        
        # Ensure DHCP services are running immediately after apply if requested
        # Note: DHCP server devices need BOTH containers:
        #   - FRR container for routing protocols (BGP, OSPF, ISIS)
        #   - Separate DHCP container for DHCP server functionality
        # ensure_dhcp_services will handle creating the separate DHCP container for server mode
        
        # Try to get DHCP config from database if not provided in request or if it's empty
        # Check if dhcp_config is empty or missing mode
        dhcp_config_empty = False
        if not dhcp_config:
            dhcp_config_empty = True
        elif isinstance(dhcp_config, dict):
            if len(dhcp_config) == 0 or not dhcp_config.get("mode"):
                dhcp_config_empty = True
        
        logging.info(f"[DHCP APPLY] Initial check for device {device_id}: dhcp_config={dhcp_config}, empty={dhcp_config_empty}")
        
        if dhcp_config_empty:
            try:
                existing_device = device_db.get_device(device_id) if device_id else None
                if existing_device:
                    existing_dhcp_config = existing_device.get("dhcp_config", {})
                    existing_dhcp_mode = existing_device.get("dhcp_mode", "")
                    
                    # Check if it's a string that needs parsing
                    if isinstance(existing_dhcp_config, str):
                        try:
                            existing_dhcp_config = json.loads(existing_dhcp_config) if existing_dhcp_config else {}
                        except Exception as parse_exc:
                            logging.debug(f"[DHCP APPLY] Failed to parse DHCP config string for device {device_id}: {parse_exc}")
                            existing_dhcp_config = {}
                    
                    # If we have a mode in the database but not in config, use it
                    if existing_dhcp_mode and not existing_dhcp_config.get("mode"):
                        if not isinstance(existing_dhcp_config, dict):
                            existing_dhcp_config = {}
                        existing_dhcp_config["mode"] = existing_dhcp_mode
                    
                    if existing_dhcp_config and existing_dhcp_config.get("mode"):
                        logging.info(f"[DHCP APPLY] Using DHCP config from database for device {device_id}: mode={existing_dhcp_config.get('mode')}, config={existing_dhcp_config}")
                        dhcp_config = existing_dhcp_config
                    else:
                        logging.debug(f"[DHCP APPLY] Device {device_id} has no valid DHCP config in database: dhcp_config={existing_dhcp_config}, dhcp_mode={existing_dhcp_mode}")
            except Exception as db_exc:
                logging.warning(f"[DHCP APPLY] Could not retrieve DHCP config from database for device {device_id}: {db_exc}", exc_info=True)
        
        dhcp_apply_mode = (dhcp_config.get("mode") or "").lower() if isinstance(dhcp_config, dict) else ""
        logging.info(f"[DHCP APPLY] Checking DHCP config for device {device_id}: dhcp_config={dhcp_config}, mode={dhcp_apply_mode}, device_id={device_id}")
        if device_id and dhcp_apply_mode in ("client", "server"):
            try:
                logging.info(f"[DHCP APPLY] Ensuring DHCP {dhcp_apply_mode} services for device {device_id} during apply on {iface_name}")
                container_for_dhcp = None
                # Try to get FRR container (for client mode, it will be used; for server mode, ensure_dhcp_services will ignore it)
                try:
                    from utils.frr_docker import FRRDockerManager
                    _frr_manager = FRRDockerManager()
                    _container_name = _frr_manager._get_container_name(device_id, device_name)
                    container_for_dhcp = _frr_manager.client.containers.get(_container_name)
                    logging.info(f"[DHCP APPLY] Retrieved FRR container {_container_name} for device {device_id}")
                except Exception as container_exc:
                    logging.debug(f"[DHCP APPLY] Unable to retrieve FRR container during apply for {device_id}: {container_exc}")
                    container_for_dhcp = None

                logging.info(f"[DHCP APPLY] Calling ensure_dhcp_services for device {device_id} with mode={dhcp_apply_mode}, container={'present' if container_for_dhcp else 'None'}")
                dhcp_apply_result = ensure_dhcp_services(
                    device_db,
                    device_id,
                    iface_name,
                    dhcp_config,
                    container=container_for_dhcp,
                    force_client_restart=(dhcp_apply_mode == "client"),
                )
                logging.info(f"[DHCP APPLY] ensure_dhcp_services result for device {device_id}: {dhcp_apply_result}")
                result["dhcp"] = dhcp_apply_result
            except Exception as dhcp_error:
                logging.error(f"[DHCP APPLY] Failed to start DHCP during apply for device {device_id}: {dhcp_error}", exc_info=True)
                result["dhcp"] = {"success": False, "error": str(dhcp_error)}
        else:
            logging.info(f"[DHCP APPLY] Skipping DHCP services for device {device_id}: device_id={device_id}, mode={dhcp_apply_mode}")
        
        if vxlan_enabled:
            result["vxlan"] = {
                "state": vxlan_state,
                "interface": vxlan_interface,
                "error": vxlan_error,
            }
        
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
        logging.info(f"OSPF Area IDs - IPv4: {ospf_config.get('area_id_ipv4')}, IPv6: {ospf_config.get('area_id_ipv6')}, Base: {ospf_config.get('area_id')}")
        
        # Check if specific address families are selected for this apply operation
        # This allows applying only selected address families without affecting others
        apply_address_families = ospf_config.get("_apply_address_families", [])
        is_partial_apply = bool(apply_address_families)
        
        # Check if IPv4 and/or IPv6 OSPF is enabled
        ipv4_enabled = ospf_config.get("ipv4_enabled", True)  # Default to True for backward compatibility
        ipv6_enabled = ospf_config.get("ipv6_enabled", False)
        
        # If specific address families are selected, only configure those
        if is_partial_apply:
            ipv4_enabled = ipv4_enabled and "IPv4" in apply_address_families
            ipv6_enabled = ipv6_enabled and "IPv6" in apply_address_families
            logging.info(f"[OSPF CONFIGURE] Partial apply: only configuring {apply_address_families}")
        
        logging.info(f"IPv4 OSPF enabled: {ipv4_enabled}, IPv6 OSPF enabled: {ipv6_enabled}")
        
        # Normalize interface name (extract base interface from labels like "TG 0 - Port: ens4np0")
        def normalize_iface(iface_str):
            """Normalize interface name from UI label format."""
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
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
            
            # Get interface from data, then normalize it
            interface_raw = data.get("interface", "ens4np0")
            interface_normalized = normalize_iface(interface_raw)
            
            dhcp_mode = (data.get("dhcp_mode") or "").lower()
            if not dhcp_mode:
                try:
                    from utils.device_database import DeviceDatabase
                    _db_lookup = DeviceDatabase()
                    existing = _db_lookup.get_device(device_id)
                    if existing:
                        dhcp_mode = (existing.get("dhcp_mode") or "").lower()
                except Exception:
                    dhcp_mode = ""
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": interface_normalized,  # Use normalized interface name
                "vlan": data.get("vlan", "0"),
                "ospf_config": ospf_config,
                "dhcp_mode": dhcp_mode,
            }
            
            logging.info(f"[OSPF CONFIGURE] Creating FRR container for device {device_name}")
            created_container_name = frr_manager.start_frr_container(device_id, device_config)
            if not created_container_name:
                logging.error(f"[OSPF CONFIGURE] Failed to create FRR container for device {device_name}")
                return jsonify({"error": "Failed to create FRR container"}), 500
            
            logging.info(f"[OSPF CONFIGURE] Successfully created FRR container: {created_container_name}")
            # Wait for FRR daemons to be fully initialized before applying configuration
            # This ensures the container is ready to accept configuration commands (like BGP does)
            import time
            logging.info(f"[OSPF CONFIGURE] Waiting 5 seconds for FRR daemons to initialize...")
            time.sleep(5)
        
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
                    except Exception:
                        existing_ospf_config = {}
                
                # Only check for removal if this is NOT a partial apply (all address families are being updated)
                # If this is a partial apply, don't remove configurations for unselected address families
                if not is_partial_apply:
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
                else:
                    logging.info(f"[OSPF CONFIGURE] Partial apply detected - skipping removal checks for unselected address families")
                
                # Update device with OSPF protocol and configuration
                # Merge with existing OSPF config to preserve fields like graceful_restart
                # that might not be explicitly updated
                merged_ospf_config = existing_ospf_config.copy() if existing_ospf_config else {}
                
                # Remove the _apply_address_families flag before saving (it's only for this apply operation)
                ospf_config_to_save = ospf_config.copy()
                ospf_config_to_save.pop("_apply_address_families", None)
                
                # If this is a partial apply, preserve the enabled flags BEFORE updating
                # This prevents them from being overwritten by the update() call
                if is_partial_apply:
                    preserved_ipv4_enabled = existing_ospf_config.get("ipv4_enabled", False) if existing_ospf_config else False
                    preserved_ipv6_enabled = existing_ospf_config.get("ipv6_enabled", False) if existing_ospf_config else False
                    
                    # Remove enabled flags from config_to_save if they're not in the selected address families
                    if "IPv4" not in apply_address_families:
                        # Don't update ipv4_enabled - preserve existing value
                        ospf_config_to_save.pop("ipv4_enabled", None)
                    if "IPv6" not in apply_address_families:
                        # Don't update ipv6_enabled - preserve existing value
                        ospf_config_to_save.pop("ipv6_enabled", None)
                
                merged_ospf_config.update(ospf_config_to_save)  # New values override existing ones
                
                # CRITICAL: Preserve area_id_ipv4 and area_id_ipv6 if not explicitly updated
                # This ensures editing one address family doesn't affect the other
                # IMPORTANT: Do this BEFORE initialization to preserve existing values
                # CRITICAL: Check if the key exists in ospf_config_to_save, not just truthiness
                # This ensures "0.0.0.0" is treated as a valid value, not as missing
                if "area_id_ipv4" not in ospf_config_to_save and "area_id_ipv4" in existing_ospf_config:
                    merged_ospf_config["area_id_ipv4"] = existing_ospf_config["area_id_ipv4"]
                if "area_id_ipv6" not in ospf_config_to_save and "area_id_ipv6" in existing_ospf_config:
                    merged_ospf_config["area_id_ipv6"] = existing_ospf_config["area_id_ipv6"]
                # Also preserve area_id for backward compatibility, but only if area_id_ipv4/ipv6 are not being updated
                # This prevents area_id from overwriting area_id_ipv4/ipv6 when they're explicitly set
                if "area_id" not in ospf_config_to_save and "area_id" in existing_ospf_config:
                    # Only preserve area_id if neither area_id_ipv4 nor area_id_ipv6 are being updated
                    # This prevents area_id from interfering with explicit area_id_ipv4/ipv6 updates
                    if "area_id_ipv4" not in ospf_config_to_save and "area_id_ipv6" not in ospf_config_to_save:
                        merged_ospf_config["area_id"] = existing_ospf_config["area_id"]
                
                # CRITICAL: Initialize area_id_ipv4 and area_id_ipv6 from area_id ONLY if not explicitly set
                # This ensures they are always set, even for new devices
                # IMPORTANT: Only initialize if they don't exist in merged_ospf_config (after preservation above)
                # This prevents overwriting values that were explicitly set or preserved
                # CRITICAL: Check if the key exists, not just truthiness, since "0.0.0.0" is a valid value
                # If area_id_ipv4/ipv6 are in ospf_config_to_save, they were explicitly set and should NOT be overwritten
                if "area_id_ipv4" not in merged_ospf_config:
                    # Only initialize if it doesn't exist in merged_ospf_config
                    # This means it wasn't in ospf_config_to_save AND wasn't preserved from existing_ospf_config
                    base_area_id = merged_ospf_config.get("area_id", "0.0.0.0")
                    merged_ospf_config["area_id_ipv4"] = base_area_id
                elif "area_id_ipv4" in ospf_config_to_save:
                    # If area_id_ipv4 was explicitly set in ospf_config_to_save, ensure it's preserved
                    # This handles the case where "0.0.0.0" is explicitly set
                    merged_ospf_config["area_id_ipv4"] = ospf_config_to_save["area_id_ipv4"]
                
                if "area_id_ipv6" not in merged_ospf_config:
                    # Only initialize if it doesn't exist in merged_ospf_config
                    # This means it wasn't in ospf_config_to_save AND wasn't preserved from existing_ospf_config
                    base_area_id = merged_ospf_config.get("area_id", "0.0.0.0")
                    merged_ospf_config["area_id_ipv6"] = base_area_id
                elif "area_id_ipv6" in ospf_config_to_save:
                    # If area_id_ipv6 was explicitly set in ospf_config_to_save, ensure it's preserved
                    # This handles the case where "0.0.0.0" is explicitly set
                    merged_ospf_config["area_id_ipv6"] = ospf_config_to_save["area_id_ipv6"]
                
                # DEBUG: Log what we're saving to database
                logging.info(f"[OSPF CONFIGURE] Saving to database for {device_name}: area_id_ipv4={merged_ospf_config.get('area_id_ipv4')}, area_id_ipv6={merged_ospf_config.get('area_id_ipv6')}, area_id={merged_ospf_config.get('area_id')}")
                
                # CRITICAL: Normalize interface field in OSPF config before saving to database
                # For untagged interfaces (VLAN 0), interface should be just the interface name (e.g., "ens4np0")
                # For tagged interfaces, interface should be the VLAN interface name (e.g., "vlan20")
                if "interface" in merged_ospf_config:
                    ospf_interface = merged_ospf_config["interface"]
                    if ospf_interface:
                        # Normalize interface name - remove "TG X - " prefix if present
                        ospf_interface_normalized = normalize_iface(ospf_interface)
                        # If VLAN is 0 and interface still has "TG" prefix, extract just the interface name
                        vlan = data.get("vlan", existing_device.get("vlan", "0") if existing_device else "0")
                        if vlan == "0" or vlan == 0:
                            # For untagged interfaces, ensure it's just the interface name
                            if "TG" in ospf_interface_normalized or " - " in ospf_interface_normalized:
                                # Extract just the interface name after " - "
                                if " - " in ospf_interface_normalized:
                                    ospf_interface_normalized = ospf_interface_normalized.split(" - ", 1)[-1].strip()
                                elif "TG" in ospf_interface_normalized:
                                    # Handle "TG 0 - ens4np0" format
                                    parts = ospf_interface_normalized.split()
                                    if len(parts) >= 3 and parts[0] == "TG":
                                        ospf_interface_normalized = parts[-1]
                        merged_ospf_config["interface"] = ospf_interface_normalized
                        logging.info(f"[OSPF CONFIGURE] Normalized interface from '{ospf_interface}' to '{ospf_interface_normalized}' before saving to database")
                
                # If this is a partial apply, restore the preserved enabled flags for unselected address families
                if is_partial_apply:
                    if "IPv4" not in apply_address_families:
                        # Restore IPv4 enabled flag from existing config
                        merged_ospf_config["ipv4_enabled"] = preserved_ipv4_enabled
                    if "IPv6" not in apply_address_families:
                        # Restore IPv6 enabled flag from existing config
                        merged_ospf_config["ipv6_enabled"] = preserved_ipv6_enabled
                
                # Ensure graceful_restart fields are preserved if not explicitly set
                if "graceful_restart_ipv4" not in ospf_config_to_save and "graceful_restart_ipv4" in existing_ospf_config:
                    merged_ospf_config["graceful_restart_ipv4"] = existing_ospf_config["graceful_restart_ipv4"]
                if "graceful_restart_ipv6" not in ospf_config_to_save and "graceful_restart_ipv6" in existing_ospf_config:
                    merged_ospf_config["graceful_restart_ipv6"] = existing_ospf_config["graceful_restart_ipv6"]
                # Also preserve graceful_restart for backward compatibility
                if "graceful_restart" not in ospf_config_to_save and "graceful_restart" in existing_ospf_config:
                    merged_ospf_config["graceful_restart"] = existing_ospf_config["graceful_restart"]
                
                existing_protocols = existing_device.get("protocols", [])
                if "OSPF" not in existing_protocols:
                    existing_protocols.append("OSPF")
                
                device_db.update_device(device_id, {
                    "protocols": existing_protocols,
                    "ospf_config": merged_ospf_config,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })
                logging.info(f"[OSPF CONFIGURE] Updated device {device_name} with OSPF configuration (graceful_restart: {merged_ospf_config.get('graceful_restart', False)})")
        except Exception as e:
            logging.warning(f"[OSPF CONFIGURE] Error checking/adding device to database: {e}")
        
        # Save OSPF route pool attachments to database (similar to BGP)
        try:
            # Check if route_pools was explicitly provided in the payload
            # CRITICAL: Only update route pools if they are explicitly provided in the payload
            # If not provided, preserve existing route pools from database to prevent accidental removal
            route_pools_provided = "route_pools" in ospf_config or "route_pools_per_area" in data
            
            # Check for route pools in ospf_config first, then in route_pools_per_area payload
            route_pools_data = ospf_config.get("route_pools", [])
            
            # If route_pools_per_area is provided in payload, use it (allows per-area assignment)
            route_pools_per_area = data.get("route_pools_per_area", {})
            if route_pools_per_area and not route_pools_data:
                # Extract route pools from route_pools_per_area
                # For now, use "default" area or first area found
                if "default" in route_pools_per_area:
                    route_pools_data = route_pools_per_area["default"]
                elif route_pools_per_area:
                    # Use first area's pools
                    first_area = list(route_pools_per_area.keys())[0]
                    route_pools_data = route_pools_per_area[first_area]
            
            area_id = ospf_config.get("area_id", "0.0.0.0")
            
            # Only update route pools if they were explicitly provided in the payload
            if route_pools_provided:
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
                        # Explicitly provided empty dict - remove all attachments for this device/area
                        device_db.remove_device_route_pools(device_id, area_id)
                        logging.info(f"[OSPF CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id} (explicitly empty)")
                elif isinstance(route_pools_data, list) and len(route_pools_data) > 0:
                    # Old format: route_pools = [pools]
                    device_db.attach_route_pools_to_device(device_id, area_id, route_pools_data)
                    logging.info(f"[OSPF CONFIGURE] Saved {len(route_pools_data)} route pool attachments for device {device_id} and area {area_id} (old format)")
                else:
                    # Explicitly provided empty list or empty dict - remove all attachments for this device/area
                    device_db.remove_device_route_pools(device_id, area_id)
                    logging.info(f"[OSPF CONFIGURE] Removed all route pool attachments for device {device_id} and area {area_id} (explicitly empty)")
            else:
                # Route pools not provided - preserve existing attachments from database
                logging.info(f"[OSPF CONFIGURE] Route pools not provided in payload, preserving existing attachments for device {device_id} and area {area_id}")
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
                    # Check for route pools in ospf_config first, then in route_pools_per_area payload
                    route_pools_data = ospf_config.get("route_pools", [])
                    
                    # If route_pools_per_area is provided in payload, use it (allows per-area assignment)
                    route_pools_per_area = data.get("route_pools_per_area", {})
                    if route_pools_per_area and not route_pools_data:
                        # Extract route pools from route_pools_per_area
                        # For now, use "default" area or first area found
                        if "default" in route_pools_per_area:
                            route_pools_data = route_pools_per_area["default"]
                        elif route_pools_per_area:
                            # Use first area's pools
                            first_area = list(route_pools_per_area.keys())[0]
                            route_pools_data = route_pools_per_area[first_area]
                    
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
            import traceback
            logging.error(f"[OSPF CONFIGURE] Traceback: {traceback.format_exc()}")
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
        
        def normalize_iface(iface_str):
            if not iface_str:
                return ""
            s = iface_str.strip().strip('"').rstrip(",")
            if " - " in s:
                s = s.split(" - ", 1)[-1].strip()
            if ":" in s:
                s = s.rsplit(":", 1)[-1].strip()
            parts = s.split()
            return parts[-1] if parts else ""
        
        iface_normalized = normalize_iface(interface)
        # CRITICAL: For VLAN interfaces, use format vlan{vlan}@{interface} to avoid conflicts
        if vlan and vlan != "0":
            iface_name = f"vlan{vlan}@{iface_normalized}"
        else:
            iface_name = iface_normalized
        
        from utils.frr_docker import FRRDockerManager
        frr_manager = None
        container_name = None
        if device_id:
            try:
                frr_manager = FRRDockerManager()
                container_name = frr_manager._get_container_name(device_id, device_name)
            except Exception as e:
                logging.debug(f"[DEVICE STOP] Failed to resolve container name: {e}")
                frr_manager = None

        dhcp_config = data.get("dhcp_config")
        if isinstance(dhcp_config, str):
            try:
                dhcp_config = json.loads(dhcp_config) if dhcp_config else {}
            except json.JSONDecodeError:
                dhcp_config = {}
        if (not dhcp_config) and device_id:
            try:
                existing_device = device_db.get_device(device_id)
                if existing_device:
                    dhcp_config = existing_device.get("dhcp_config", {}) or {}
                    if isinstance(dhcp_config, str):
                        dhcp_config = json.loads(dhcp_config) if dhcp_config else {}
            except Exception as e:
                logging.debug(f"[DEVICE STOP] Failed to load DHCP config from database: {e}")
                dhcp_config = {}
        dhcp_mode = ""
        if isinstance(dhcp_config, dict):
            dhcp_mode = (dhcp_config.get("mode") or "").lower()
        
        result = {
            "device_id": device_id,
            "device": device_name,
            "interface": interface,
        }
        
        # Stop DHCP services if configured
        if dhcp_mode in ("client", "server") and iface_name:
            try:
                logging.info(f"[DHCP] Stopping DHCP {dhcp_mode} for device {device_id} on {iface_name}")
                stop_dhcp_services(
                    device_db,
                    device_id,
                    iface_name,
                    dhcp_mode,
                    remove_container=False,
                )
            except Exception as dhcp_error:
                logging.warning(f"[DHCP] Failed to stop DHCP services: {dhcp_error}")
        
        # Stop FRR container (this stops all protocols automatically)
        try:
            if not frr_manager:
                frr_manager = FRRDockerManager()
            container_name = frr_manager._get_container_name(device_id, device_name)
            
            logging.info(f"[DEVICE STOP] Stopping FRR container {container_name} for device {device_name}")
            
            container_stopped = frr_manager.stop_frr_container(device_id, device_name)
            if container_stopped:
                logging.info(f"[DEVICE STOP] Successfully stopped FRR container for {device_name}")
                result["container_stopped"] = True
                
                # Update all protocol statuses in database to reflect container stop
                try:
                    update_data = {
                        # BGP status
                        'bgp_established': False,
                        'bgp_ipv4_established': False,
                        'bgp_ipv4_state': 'Idle',
                        'bgp_ipv6_established': False,
                        'bgp_ipv6_state': 'Idle',
                        # OSPF status
                        'ospf_established': False,
                        'ospf_state': 'Down',
                        'ospf_ipv4_running': False,
                        'ospf_ipv4_established': False,
                        'ospf_ipv6_running': False,
                        'ospf_ipv6_established': False,
                        'ospf_neighbors': None,
                        # ISIS status
                        'isis_running': False,
                        'isis_state': 'Down',
                        'isis_established': False,
                        'isis_neighbors': None,
                        'isis_manual_override': False,
                        'isis_manual_override_time': None,
                        # Update timestamps
                        'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                        'last_ospf_check': datetime.now(timezone.utc).isoformat(),
                        'last_isis_check': datetime.now(timezone.utc).isoformat(),
                        'dhcp_state': 'Stopped',
                        'dhcp_running': False,
                        'dhcp_lease_ip': None,
                        'dhcp_lease_mask': None,
                        'dhcp_lease_gateway': None,
                        'last_dhcp_check': datetime.now(timezone.utc).isoformat(),
                    }
                    device_db.update_device(device_id, update_data)
                    logging.info(f"[DEVICE STOP] Updated all protocol statuses to stopped in database for {device_name}")
                except Exception as e:
                    logging.warning(f"[DEVICE STOP] Failed to update protocol statuses in database: {e}")
            else:
                logging.warning(f"[DEVICE STOP] Failed to stop FRR container for {device_name}")
                result["container_stopped"] = False
        except Exception as e:
            logging.error(f"[DEVICE STOP] Error stopping FRR container for {device_name}: {e}")
            result["container_stopped"] = False
        
        # Interface shutdown is intentionally skipped; container stop is sufficient for light stop
        result["interface_shutdown"] = False
        logging.info(f"[DEVICE STOP] Device {device_name} stopped (container only, interface left up)")
        
        # Update device status in database and ensure all protocol statuses are cleared
        try:
            # First update device status
            device_db.update_device_status(device_id, "Stopped")
            
            # Then ensure all protocol statuses are cleared (in case container wasn't running)
            update_data = {
                # BGP status
                'bgp_established': False,
                'bgp_ipv4_established': False,
                'bgp_ipv4_state': 'Idle',
                'bgp_ipv6_established': False,
                'bgp_ipv6_state': 'Idle',
                # OSPF status
                'ospf_established': False,
                'ospf_state': 'Down',
                'ospf_ipv4_running': False,
                'ospf_ipv4_established': False,
                'ospf_ipv6_running': False,
                'ospf_ipv6_established': False,
                'ospf_neighbors': None,
                # ISIS status
                'isis_running': False,
                'isis_state': 'Down',
                'isis_established': False,
                'isis_neighbors': None,
                'isis_manual_override': False,
                'isis_manual_override_time': None,
                # Update timestamps
                'last_bgp_check': datetime.now(timezone.utc).isoformat(),
                'last_ospf_check': datetime.now(timezone.utc).isoformat(),
                'last_isis_check': datetime.now(timezone.utc).isoformat(),
                'dhcp_state': 'Stopped',
                'dhcp_running': False,
                'dhcp_lease_ip': None,
                'dhcp_lease_mask': None,
                'dhcp_lease_gateway': None,
                'last_dhcp_check': datetime.now(timezone.utc).isoformat(),
            }
            device_db.update_device(device_id, update_data)
            logging.info(f"[DEVICE DB] Device {device_id} status updated to Stopped and all protocol statuses cleared")
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
        container_name = None
        frr_manager = None
        dhcp_mode_remove = ""
        try:
            device_info = device_db.get_device(device_id)
            if device_info and not device_name:
                device_name = device_info.get("device_name", "")
            if device_info:
                base_iface = device_info.get("interface", "")
                vlan = str(device_info.get("vlan", "0"))

                iface_normalized = base_iface
                if base_iface:
                    s = base_iface.strip().strip('"').rstrip(",")
                    if " - " in s:
                        s = s.split(" - ", 1)[-1].strip()
                    if ":" in s:
                        s = s.rsplit(":", 1)[-1].strip()
                    parts = s.split()
                    iface_normalized = parts[-1] if parts else s

                # CRITICAL: For VLAN interfaces, use format vlan{vlan}@{interface} to avoid conflicts
                if vlan and vlan != "0":
                    iface_name = f"vlan{vlan}@{iface_normalized}"
                else:
                    iface_name = iface_normalized

                dhcp_cfg = device_info.get("dhcp_config") or {}
                if isinstance(dhcp_cfg, str):
                    try:
                        dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
                    except Exception:
                        dhcp_cfg = {}
                if isinstance(dhcp_cfg, dict):
                    iface_override = dhcp_cfg.get("interface")
                    if iface_override:
                        iface_name = iface_override
                    dhcp_mode_remove = (dhcp_cfg.get("mode") or device_info.get("dhcp_mode") or "").lower()
                else:
                    dhcp_mode_remove = (device_info.get("dhcp_mode") or "").lower()
                try:
                    if not frr_manager:
                        from utils.frr_docker import FRRDockerManager
                        frr_manager = FRRDockerManager()
                    container_name = frr_manager._get_container_name(device_id, device_name)
                except Exception as container_error:
                    logging.debug(f"[DEVICE REMOVE] Failed to resolve container name: {container_error}")
                vxlan_cfg = device_info.get("vxlan_config")
                if vxlan_cfg:
                    try:
                        # Handle both old format (single tunnel dict) and new format (tunnels list)
                        if isinstance(vxlan_cfg, dict) and "tunnels" in vxlan_cfg:
                            # New format: multiple tunnels
                            tunnels = vxlan_cfg.get("tunnels", [])
                            logging.info(f"[DEVICE REMOVE] Cleaning up {len(tunnels)} VXLAN tunnel(s) for device {device_id}")
                            for tunnel in tunnels:
                                if isinstance(tunnel, dict):
                                    try:
                                        vxlan_utils.tear_down_vxlan_interface(
                                            device_id,
                                            tunnel,
                                            container_name=container_name,
                                            frr_manager=frr_manager,
                                        )
                                        logging.info(f"[DEVICE REMOVE] Successfully cleaned up VXLAN tunnel VNI {tunnel.get('vni')} for device {device_id}")
                                    except Exception as tunnel_exc:
                                        logging.warning(f"[DEVICE REMOVE] Failed to tear down VXLAN tunnel VNI {tunnel.get('vni')} for {device_id}: {tunnel_exc}")
                        else:
                            # Old format: single tunnel dict
                            vxlan_utils.tear_down_vxlan_interface(
                                device_id,
                                vxlan_cfg,
                                container_name=container_name,
                                frr_manager=frr_manager,
                            )
                            logging.info(f"[DEVICE REMOVE] Successfully cleaned up VXLAN for device {device_id}")
                    except Exception as vxlan_exc:
                        logging.warning(f"[DEVICE REMOVE] Failed to tear down VXLAN for {device_id}: {vxlan_exc}")
                dhcp_mode_remove = (device_info.get("dhcp_mode") or "").lower()
                if dhcp_mode_remove in ("client", "server") and iface_name:
                    try:
                        logging.info(f"[DHCP] Stopping DHCP {dhcp_mode_remove} before removing device {device_id}")
                        stop_dhcp_services(
                            device_db,
                            device_id,
                            iface_name,
                            dhcp_mode_remove,
                            remove_container=True,
                        )
                    except Exception as dhcp_error:
                        logging.warning(f"[DHCP] Failed to stop DHCP during device removal: {dhcp_error}")
        except Exception as e:
            logging.warning(f"[DEVICE REMOVE] Failed to get device info from database: {e}")
        
        # CRITICAL: VXLAN cleanup (bridge removal) must happen BEFORE container removal
        # The container removal happens below, but we ensure VXLAN cleanup completed first
        # If VXLAN cleanup failed above, try one more time before container removal
        if device_info:
            vxlan_cfg = device_info.get("vxlan_config")
            if vxlan_cfg and container_name and frr_manager:
                try:
                    # Handle both old format (single tunnel dict) and new format (tunnels list)
                    tunnels_to_verify = []
                    if isinstance(vxlan_cfg, dict) and "tunnels" in vxlan_cfg:
                        # New format: multiple tunnels
                        tunnels_to_verify = vxlan_cfg.get("tunnels", [])
                    else:
                        # Old format: single tunnel dict
                        tunnels_to_verify = [vxlan_cfg] if vxlan_cfg else []
                    
                    # Verify VXLAN cleanup completed - check if bridges still exist
                    for tunnel in tunnels_to_verify:
                        if isinstance(tunnel, dict):
                            try:
                                from utils.vxlan import normalize_config as vxlan_normalize
                                config = vxlan_normalize(tunnel)
                                vni = config.get("vni")
                                bridge_name = f"br{vni}" if vni else None
                                if bridge_name:
                                    try:
                                        container = frr_manager.client.containers.get(container_name)
                                        check_result = container.exec_run(["ip", "link", "show", bridge_name])
                                        if check_result.exit_code == 0:
                                            logging.warning(f"[DEVICE REMOVE] Bridge {bridge_name} still exists, attempting cleanup again before container removal")
                                            vxlan_utils.tear_down_vxlan_interface(
                                                device_id,
                                                tunnel,
                                                container_name=container_name,
                                                frr_manager=frr_manager,
                                            )
                                    except Exception:
                                        # Container might not exist or bridge already removed - that's fine
                                        pass
                            except Exception as tunnel_verify_exc:
                                logging.debug(f"[DEVICE REMOVE] VXLAN tunnel verification failed: {tunnel_verify_exc}")
                except Exception as vxlan_verify_exc:
                    logging.debug(f"[DEVICE REMOVE] VXLAN cleanup verification failed: {vxlan_verify_exc}")
        
        # Stop and remove FRR Docker container for this device
        # NOTE: This will automatically remove all interfaces inside the container, including bridges
        # But we clean them up explicitly above to ensure proper cleanup order
        container_removed = False
        try:
            from utils.frr_docker import stop_frr_container
            
            success = stop_frr_container(device_id, device_name, remove=True)
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
        logger.info(f"[REMOVE] Cleaning up device-to-IP mapping for device '{device_name}' (ID: {device_id})")
        keys_to_remove = []
        for key, mapped_device_id in DEVICE_IP_MAPPING.items():
            if mapped_device_id == device_id:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del DEVICE_IP_MAPPING[key]
            logger.info(f"[REMOVE] Removed IP mapping: {key}")
        
        logger.info(f"[REMOVE] Cleaned up {len(keys_to_remove)} IP mappings for device {device_id}")
        
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


@app.route("/api/device/dhcp/status", methods=["GET"])
def get_dhcp_status():
    """Return DHCP status snapshots for all devices."""
    try:
        devices = device_db.get_all_devices()
        rows = []
        for device in devices:
            dhcp_mode = (device.get("dhcp_mode") or "").lower()
            if not dhcp_mode:
                continue
            device_id = device.get("device_id")

            pool_names = {"primary": None, "additional": []}
            if device_id:
                try:
                    db_pools = device_db.get_device_dhcp_pools(device_id) or {}
                except Exception:
                    db_pools = {}
                if isinstance(db_pools, dict):
                    if db_pools.get("primary"):
                        pool_names["primary"] = db_pools.get("primary")
                    additional_from_db = db_pools.get("additional") or []
                    if isinstance(additional_from_db, (list, tuple, set)):
                        pool_names["additional"] = [
                            str(name)
                            for name in additional_from_db
                            if name and str(name) not in pool_names["additional"]
                        ]

            dhcp_cfg = device.get("dhcp_config") or {}
            if isinstance(dhcp_cfg, str):
                try:
                    dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
                except Exception:
                    dhcp_cfg = {}
            if not isinstance(dhcp_cfg, dict):
                dhcp_cfg = {}

            config_pool_names = dhcp_cfg.get("pool_names")
            if isinstance(config_pool_names, dict):
                primary_candidate = config_pool_names.get("primary")
                if primary_candidate and not pool_names["primary"]:
                    pool_names["primary"] = primary_candidate
                additional_candidates = config_pool_names.get("additional") or []
                if isinstance(additional_candidates, (list, tuple, set)):
                    for name in additional_candidates:
                        if not name:
                            continue
                        name_str = str(name)
                        if (
                            name_str
                            and name_str != pool_names["primary"]
                            and name_str not in pool_names["additional"]
                        ):
                            pool_names["additional"].append(name_str)
            else:
                legacy_primary = dhcp_cfg.get("pool_name")
                if legacy_primary and not pool_names["primary"]:
                    pool_names["primary"] = legacy_primary
                additional_entries = dhcp_cfg.get("additional_pools") or []
                if isinstance(additional_entries, list):
                    for entry in additional_entries:
                        if not isinstance(entry, dict):
                            continue
                        pool_name = entry.get("pool_name")
                        if (
                            pool_name
                            and pool_name != pool_names["primary"]
                            and pool_name not in pool_names["additional"]
                        ):
                            pool_names["additional"].append(pool_name)

            # Ensure additional pools list is sorted for stable display
            if pool_names["additional"]:
                pool_names["additional"] = sorted(pool_names["additional"])

            # Include default pool information (from Add Device dialog) if no named pools are attached
            default_pool = None
            if dhcp_mode == "server" and not pool_names["primary"] and not pool_names["additional"]:
                pool_start = dhcp_cfg.get("pool_start")
                pool_end = dhcp_cfg.get("pool_end")
                if pool_start and pool_end:
                    default_pool = {
                        "pool_start": pool_start,
                        "pool_end": pool_end,
                        "pool_range": f"{pool_start}-{pool_end}",
                    }

            rows.append({
                "device_id": device_id,
                "device_name": device.get("device_name"),
                "interface": device.get("interface"),
                "server_interface": device.get("server_interface"),
                "vlan": device.get("vlan"),
                "mode": dhcp_mode,
                "state": device.get("dhcp_state", "Unknown"),
                "running": bool(device.get("dhcp_running")),
                "lease_ip": device.get("dhcp_lease_ip"),
                "lease_mask": device.get("dhcp_lease_mask"),
                "lease_gateway": device.get("dhcp_lease_gateway"),
                "lease_server": device.get("dhcp_lease_server"),
                "lease_expires": device.get("dhcp_lease_expires"),
                "last_check": device.get("last_dhcp_check"),
                "pool_names": pool_names,
                "default_pool": default_pool,
            })
        return jsonify({"devices": rows}), 200
    except Exception as e:
        logging.error(f"[DHCP STATUS] Failed to gather DHCP status: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/device/dhcp/server/pool", methods=["POST"])
def update_dhcp_server_pool():
    """Attach or replace a DHCP pool for an existing DHCP server device."""
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    device_id = (payload.get("device_id") or "").strip()
    pool_start = (payload.get("pool_start") or "").strip()
    pool_end = (payload.get("pool_end") or "").strip()
    replace_existing = bool(payload.get("replace_existing"))
    gateway_override = (payload.get("gateway") or "").strip()
    gateway_route_input = payload.get("gateway_route")

    gateway_routes_to_add: list = []
    if isinstance(gateway_route_input, (list, tuple, set)):
        for item in gateway_route_input:
            if not item:
                continue
            value = str(item).strip()
            if value:
                gateway_routes_to_add.append(value)
    elif isinstance(gateway_route_input, str):
        value = gateway_route_input.strip()
        if value:
            gateway_routes_to_add.append(value)
    elif gateway_route_input:
        value = str(gateway_route_input).strip()
        if value:
            gateway_routes_to_add.append(value)

    if not device_id or not pool_start or not pool_end:
        return jsonify({"error": "device_id, pool_start, and pool_end are required"}), 400

    try:
        device = device_db.get_device(device_id)
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to load device {device_id}: {exc}")
        return jsonify({"error": str(exc)}), 500

    if not device:
        return jsonify({"error": "Device not found"}), 404

    dhcp_cfg = device.get("dhcp_config") or {}
    if isinstance(dhcp_cfg, str):
        try:
            dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
        except Exception:
            dhcp_cfg = {}

    current_mode = (device.get("dhcp_mode") or dhcp_cfg.get("mode") or "").lower()
    if current_mode != "server":
        return jsonify({"error": "Selected device is not configured as a DHCP server"}), 400

    interface = (
        dhcp_cfg.get("interface")
        or device.get("server_interface")
        or device.get("interface")
    )
    if not interface:
        return jsonify({"error": "Unable to determine interface for DHCP server"}), 400

    # Manual override detaches existing named pool associations
    try:
        device_db.remove_device_dhcp_pools(device_id)
    except Exception as exc:
        logging.debug(f"[DHCP API] Failed to clear DHCP pool attachments for {device_id}: {exc}")

    additional_pools = dhcp_cfg.get("additional_pools") or []
    if isinstance(additional_pools, str):
        try:
            additional_pools = json.loads(additional_pools) if additional_pools else []
        except Exception:
            additional_pools = []
    elif not isinstance(additional_pools, list):
        additional_pools = list(additional_pools) if additional_pools else []
    additional_pools = [pool for pool in additional_pools if isinstance(pool, dict)]

    new_pool_entry = {
        "pool_start": pool_start,
        "pool_end": pool_end,
    }
    if gateway_routes_to_add:
        new_pool_entry["gateway_route"] = gateway_routes_to_add

    if replace_existing or not (dhcp_cfg.get("pool_start") and dhcp_cfg.get("pool_end")):
        logging.info(
            "[DHCP API] Replacing base pool for device %s with %s-%s",
            device_id,
            pool_start,
            pool_end,
        )
        dhcp_cfg["pool_start"] = pool_start
        dhcp_cfg["pool_end"] = pool_end
        if replace_existing:
            # Keep existing additional pools but ensure no duplicate of new range
            additional_pools = [
                pool
                for pool in additional_pools
                if pool.get("pool_start") != pool_start or pool.get("pool_end") != pool_end
            ]
    else:
        logging.info(
            "[DHCP API] Appending additional pool %s-%s to device %s",
            pool_start,
            pool_end,
            device_id,
        )
        duplicate = False
        for pool in additional_pools:
            if pool.get("pool_start") == pool_start and pool.get("pool_end") == pool_end:
                duplicate = True
                break
        if not duplicate:
            additional_pools.append(new_pool_entry)

    dhcp_cfg["additional_pools"] = additional_pools
    dhcp_cfg["mode"] = "server"
    dhcp_cfg["interface"] = interface

    if gateway_override:
        dhcp_cfg["gateway"] = gateway_override
    elif not dhcp_cfg.get("gateway"):
        dhcp_cfg["gateway"] = (
            device.get("dhcp_lease_gateway")
            or device.get("ipv4_gateway")
            or ""
        )

    if gateway_routes_to_add:
        existing_routes = dhcp_cfg.get("gateway_route")
        route_list = []
        if isinstance(existing_routes, str):
            route_list = [existing_routes] if existing_routes else []
        elif isinstance(existing_routes, list):
            route_list = list(existing_routes)
        elif existing_routes:
            route_list = [str(existing_routes)]
        for route in gateway_routes_to_add:
            if route not in route_list:
                route_list.append(route)
        dhcp_cfg["gateway_route"] = route_list

    try:
        result = ensure_dhcp_services(
            device_db,
            device_id,
            interface,
            dhcp_cfg,
        )
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to ensure DHCP server for {device_id}: {exc}")
        return jsonify({"error": str(exc)}), 500

    if not result.get("success"):
        return jsonify({"error": result.get("error", "Failed to update DHCP server")}), 500

    try:
        updated_device = device_db.get_device(device_id) or {}
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to refresh device {device_id}: {exc}")
        updated_device = {}

    return jsonify({"status": "success", "device": updated_device}), 200


@app.route("/api/device/dhcp/server/attach_pools", methods=["POST"])
def attach_dhcp_pools_to_server():
    """Attach named DHCP pools from the database to a DHCP server device."""
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}

    device_id = (data.get("device_id") or "").strip()
    detach_all = bool(data.get("detach_all", False))
    primary_pool_name = (data.get("primary_pool") or "").strip()
    additional_pool_names = data.get("additional_pools") or []
    replace_existing = bool(data.get("replace_existing", True))
    gateway_override = (data.get("gateway") or "").strip()

    if isinstance(additional_pool_names, str):
        additional_pool_names = [additional_pool_names]
    additional_pool_names = [
        str(name).strip()
        for name in additional_pool_names
        if str(name).strip()
    ]

    if not device_id:
        return jsonify({"error": "device_id is required"}), 400

    # Handle detach all pools case
    if detach_all:
        try:
            device = device_db.get_device(device_id)
        except Exception as exc:
            logging.error(f"[DHCP API] Failed to load device {device_id}: {exc}")
            return jsonify({"error": str(exc)}), 500

        if not device:
            return jsonify({"error": "Device not found"}), 404

        # Detach all pools from device
        try:
            device_db.remove_device_dhcp_pools(device_id)
        except Exception as exc:
            logging.error(f"[DHCP API] Failed to detach DHCP pools for {device_id}: {exc}")
            return jsonify({"error": str(exc)}), 500

        # Clear pool configuration from dhcp_config
        dhcp_cfg = device.get("dhcp_config") or {}
        if isinstance(dhcp_cfg, str):
            try:
                dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
            except Exception:
                dhcp_cfg = {}
        if not isinstance(dhcp_cfg, dict):
            dhcp_cfg = {}

        # Save route metadata before clearing pool fields (needed for route cleanup)
        saved_pool_networks = dhcp_cfg.get("pool_networks")
        saved_gateway_routes = dhcp_cfg.get("gateway_route_normalized")
        saved_interface = dhcp_cfg.get("interface") or device.get("server_interface") or device.get("interface")
        saved_gateway = dhcp_cfg.get("gateway", "")

        # Stop DHCP server if no pools remain (before clearing config)
        try:
            interface = saved_interface
            if interface:
                from utils.dhcp import stop_dhcp_server, _get_dhcp_container
                container = _get_dhcp_container(device_id, mode="server")
                # Temporarily restore route metadata for cleanup
                if saved_pool_networks:
                    dhcp_cfg["pool_networks"] = saved_pool_networks
                if saved_gateway_routes:
                    dhcp_cfg["gateway_route_normalized"] = saved_gateway_routes
                stop_dhcp_server(device_db, device_id, interface, container=container)
        except Exception as exc:
            logging.warning(f"[DHCP API] Failed to stop DHCP server after detach: {exc}")

        # Clear pool-related fields but keep other DHCP config (after stopping server)
        dhcp_cfg.pop("pool_name", None)
        dhcp_cfg.pop("pool_names", None)
        dhcp_cfg.pop("pool_start", None)
        dhcp_cfg.pop("pool_end", None)
        dhcp_cfg.pop("additional_pools", None)
        dhcp_cfg.pop("pool_range", None)
        dhcp_cfg.pop("pool_networks", None)
        dhcp_cfg.pop("gateway_route_normalized", None)

        # Update device in database
        try:
            device_db.update_device(device_id, {"dhcp_config": dhcp_cfg})
        except Exception as exc:
            logging.error(f"[DHCP API] Failed to update device {device_id}: {exc}")
            return jsonify({"error": str(exc)}), 500

        try:
            updated_device = device_db.get_device(device_id)
        except Exception:
            updated_device = device

        return jsonify({"status": "success", "message": "All DHCP pools detached", "device": updated_device}), 200

    if not primary_pool_name:
        return jsonify({"error": "primary_pool is required (or set detach_all=true)"}), 400

    try:
        device = device_db.get_device(device_id)
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to load device {device_id}: {exc}")
        return jsonify({"error": str(exc)}), 500

    if not device:
        return jsonify({"error": "Device not found"}), 404

    primary_pool = device_db.get_dhcp_pool(primary_pool_name)
    if not primary_pool:
        return jsonify({"error": f"Primary DHCP pool '{primary_pool_name}' not found"}), 404

    additional_defs = []
    missing_pools = []
    for pool_name in additional_pool_names:
        pool_def = device_db.get_dhcp_pool(pool_name)
        if not pool_def:
            missing_pools.append(pool_name)
        else:
            additional_defs.append(pool_def)
    if missing_pools:
        return jsonify({"error": f"Unknown DHCP pools: {', '.join(sorted(missing_pools))}"}), 404

    dhcp_cfg = {}
    existing_config = device.get("dhcp_config") or {}
    if isinstance(existing_config, str):
        try:
            existing_config = json.loads(existing_config)
        except Exception:
            existing_config = {}
    if not isinstance(existing_config, dict):
        existing_config = {}

    if not replace_existing and existing_config:
        dhcp_cfg = dict(existing_config)
    else:
        dhcp_cfg = {}

    # Establish interface
    interface = (
        dhcp_cfg.get("interface")
        or existing_config.get("interface")
        or device.get("server_interface")
        or device.get("interface")
    )
    if not interface:
        return jsonify({"error": "Unable to determine interface for DHCP server"}), 400
    dhcp_cfg["interface"] = interface

    # Apply primary pool settings
    dhcp_cfg["mode"] = "server"
    dhcp_cfg["pool_start"] = primary_pool.get("pool_start")
    dhcp_cfg["pool_end"] = primary_pool.get("pool_end")
    dhcp_cfg["pool_name"] = primary_pool_name
    dhcp_cfg.pop("pool_range", None)
    dhcp_cfg.pop("pool_networks", None)
    dhcp_cfg.pop("gateway_route_normalized", None)

    if primary_pool.get("lease_time") is not None:
        dhcp_cfg["lease_time"] = primary_pool.get("lease_time")
    elif "lease_time" in dhcp_cfg and replace_existing:
        dhcp_cfg.pop("lease_time", None)

    primary_routes = primary_pool.get("gateway_routes") or []
    if primary_routes:
        dhcp_cfg["gateway_route"] = primary_routes
    else:
        dhcp_cfg.pop("gateway_route", None)

    if gateway_override:
        dhcp_cfg["gateway"] = gateway_override
    elif primary_pool.get("gateway"):
        dhcp_cfg["gateway"] = primary_pool.get("gateway")
    elif replace_existing and "gateway" in dhcp_cfg:
        dhcp_cfg.pop("gateway", None)

    # Merge existing additional pools if requested
    additional_pools_payload = []
    existing_additional_names = set()
    if not replace_existing:
        existing_additional = dhcp_cfg.get("additional_pools") or existing_config.get("additional_pools") or []
        if isinstance(existing_additional, str):
            try:
                existing_additional = json.loads(existing_additional)
            except Exception:
                existing_additional = []
        if isinstance(existing_additional, list):
            for pool_entry in existing_additional:
                if isinstance(pool_entry, dict):
                    additional_pools_payload.append(pool_entry)
                    pool_entry_name = pool_entry.get("pool_name")
                    if pool_entry_name:
                        existing_additional_names.add(pool_entry_name)

    # Add requested additional pools
    for pool in additional_defs:
        pool_entry = {
            "pool_start": pool.get("pool_start"),
            "pool_end": pool.get("pool_end"),
            "pool_name": pool.get("pool_name"),
        }
        if pool.get("gateway"):
            pool_entry["gateway"] = pool.get("gateway")
        if pool.get("lease_time") is not None:
            pool_entry["lease_time"] = pool.get("lease_time")
        if pool.get("gateway_routes"):
            pool_entry["gateway_route"] = pool.get("gateway_routes")
        if pool_entry.get("pool_name") not in existing_additional_names:
            additional_pools_payload.append(pool_entry)
            if pool_entry.get("pool_name"):
                existing_additional_names.add(pool_entry["pool_name"])

    dhcp_cfg["additional_pools"] = additional_pools_payload
    dhcp_cfg["pool_names"] = {
        "primary": primary_pool_name,
        "additional": [
            entry.get("pool_name")
            for entry in additional_pools_payload
            if entry.get("pool_name") and entry.get("pool_name") != primary_pool_name
        ],
    }

    try:
        result = ensure_dhcp_services(
            device_db,
            device_id,
            interface,
            dhcp_cfg,
        )
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to attach DHCP pools for {device_id}: {exc}")
        return jsonify({"error": str(exc)}), 500

    if not result.get("success"):
        return jsonify({"error": result.get("error", "Failed to update DHCP server")}), 500

    # Record attachments
    named_additional = [
        name for name in dhcp_cfg["pool_names"]["additional"] if name and name != primary_pool_name
    ]
    try:
        device_db.attach_dhcp_pools_to_device(device_id, primary_pool_name, named_additional)
    except Exception as exc:
        logging.debug(f"[DHCP API] Failed to persist DHCP pool attachments for {device_id}: {exc}")

    try:
        updated_device = device_db.get_device(device_id) or {}
    except Exception as exc:
        logging.error(f"[DHCP API] Failed to refresh device {device_id}: {exc}")
        updated_device = {}

    return jsonify({"status": "success", "device": updated_device}), 200


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
                
                # Skip adding default route if VXLAN is enabled for this device
                # Check both database and protocols list to be safe
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_record = device_db.get_device(device_id) if device_id else None
                    vxlan_enabled_in_db = device_record and (device_record.get("vxlan_enabled") is True)
                    vxlan_in_protocols = "VXLAN" in (device_record.get("protocols", []) if device_record else [])
                    if vxlan_enabled_in_db or vxlan_in_protocols:
                        logging.info(f"[ROUTE BG] Skipping default route for {device_name} because VXLAN is enabled (DB: {vxlan_enabled_in_db}, Protocols: {vxlan_in_protocols})")
                        return
                except Exception as _vx_exc:
                    logging.debug(f"[ROUTE BG] Could not determine VXLAN status from DB: {_vx_exc}")
                
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
                except Exception:
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
                        except Exception:
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
                except Exception:
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
            except Exception:
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
        
        # Check if this is a partial apply (only selected address families)
        apply_address_families = bgp_config.get("_apply_address_families", [])
        is_partial_apply = bool(apply_address_families)
        
        if is_partial_apply:
            logging.info(f"[BGP CONFIGURE] Partial apply detected for address families: {apply_address_families}")
            # Get existing BGP config to preserve unselected families
            existing_device = device_db.get_device(device_id)
            existing_bgp_config = existing_device.get("bgp_config", {}) if existing_device else {}
            if isinstance(existing_bgp_config, str):
                import json
                try:
                    existing_bgp_config = json.loads(existing_bgp_config)
                except Exception:
                    existing_bgp_config = {}
            
            # Adjust enabled flags based on selected families
            if "ipv4" in apply_address_families:
                ipv4_enabled = bgp_config.get("ipv4_enabled", True)
            else:
                # Preserve existing IPv4 enabled state
                ipv4_enabled = existing_bgp_config.get("ipv4_enabled", False)
            
            if "ipv6" in apply_address_families:
                ipv6_enabled = bgp_config.get("ipv6_enabled", False)
            else:
                # Preserve existing IPv6 enabled state
                ipv6_enabled = existing_bgp_config.get("ipv6_enabled", False)
        else:
            # Full apply - use flags from config
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
            # Normalize interface name (extract base interface from labels like "TG 0 - Port: ens4np0")
            def normalize_iface(iface_str):
                """Normalize interface name from UI label format."""
                if not iface_str:
                    return ""
                s = iface_str.strip().strip('"').rstrip(",")
                if " - " in s:
                    s = s.split(" - ", 1)[-1].strip()
                if ":" in s:
                    s = s.rsplit(":", 1)[-1].strip()
                parts = s.split()
                return parts[-1] if parts else ""
            
            # Get interface from data, then normalize it
            interface_raw = data.get("interface", "ens4np0")
            interface_normalized = normalize_iface(interface_raw)
            
            dhcp_mode = (data.get("dhcp_mode") or "").lower()
            if not dhcp_mode:
                try:
                    existing = device_db.get_device(device_id)
                    if existing:
                        dhcp_mode = (existing.get("dhcp_mode") or "").lower()
                except Exception:
                    dhcp_mode = ""
            device_config = {
                "device_name": device_name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "interface": interface_normalized,  # Use normalized interface name
                "vlan": data.get("vlan", "0"),
                "bgp_config": bgp_config,
                "dhcp_mode": dhcp_mode,
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
                    except Exception:
                        existing_bgp_config = {}
                
                # Check if IPv4 was previously enabled but now disabled - need to remove IPv4 neighbors
                existing_ipv4_enabled = existing_bgp_config.get("ipv4_enabled", False)
                existing_ipv4_neighbor = existing_bgp_config.get("bgp_neighbor_ipv4", "")
                
                # Remove IPv4 neighbors from FRR if IPv4 was enabled but now disabled
                # Skip removal check during partial apply if IPv4 is not in selected families
                if not is_partial_apply or "ipv4" in apply_address_families:
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
                # Skip removal check during partial apply if IPv6 is not in selected families
                if not is_partial_apply or "ipv6" in apply_address_families:
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
                
                # Merge BGP configuration to preserve unselected address families during partial apply
                if is_partial_apply:
                    # Merge with existing config to preserve unselected families
                    merged_bgp_config = existing_bgp_config.copy()
                    merged_bgp_config.update(bgp_config)
                    
                    # Preserve enabled flags for unselected address families
                    if "ipv4" not in apply_address_families:
                        merged_bgp_config["ipv4_enabled"] = existing_bgp_config.get("ipv4_enabled", False)
                        # Also preserve IPv4 neighbor config if not being updated
                        if "bgp_neighbor_ipv4" not in bgp_config:
                            merged_bgp_config["bgp_neighbor_ipv4"] = existing_bgp_config.get("bgp_neighbor_ipv4", "")
                        if "bgp_update_source_ipv4" not in bgp_config:
                            merged_bgp_config["bgp_update_source_ipv4"] = existing_bgp_config.get("bgp_update_source_ipv4", "")
                    
                    if "ipv6" not in apply_address_families:
                        merged_bgp_config["ipv6_enabled"] = existing_bgp_config.get("ipv6_enabled", False)
                        # Also preserve IPv6 neighbor config if not being updated
                        if "bgp_neighbor_ipv6" not in bgp_config:
                            merged_bgp_config["bgp_neighbor_ipv6"] = existing_bgp_config.get("bgp_neighbor_ipv6", "")
                        if "bgp_update_source_ipv6" not in bgp_config:
                            merged_bgp_config["bgp_update_source_ipv6"] = existing_bgp_config.get("bgp_update_source_ipv6", "")
                    
                    # Remove the _apply_address_families flag before saving
                    merged_bgp_config.pop("_apply_address_families", None)
                    
                    update_data["bgp_config"] = merged_bgp_config
                    logging.info(f"[BGP CONFIGURE] Updating BGP configuration for device {device_name} (partial apply for {apply_address_families})")
                else:
                    # Full apply - use config as-is
                    bgp_config_to_save = bgp_config.copy()
                    bgp_config_to_save.pop("_apply_address_families", None)
                    update_data["bgp_config"] = bgp_config_to_save
                logging.info(f"[BGP CONFIGURE] Updating BGP configuration for device {device_name}")
                
                if update_data:
                    device_db.update_device(device_id, update_data)
        except Exception as e:
            logging.warning(f"[BGP CONFIGURE] Error checking/adding device to database: {e}")
        
        # First, configure interface IP addresses and BGP using configure_bgp_for_device
        # This function configures both the interface IPs and BGP neighbors properly
        logging.info(f"[BGP CONFIGURE] Configuring interface and BGP for device {device_name}")
        
        # Get IP addresses with masks for configure_bgp_for_device
        ipv4_full = f"{ipv4}/{data.get('ipv4_mask', '24')}" if ipv4 else None
        ipv6_full = f"{ipv6}/{data.get('ipv6_mask', '64')}" if ipv6 else None
        
        from utils.bgp import configure_bgp_for_device
        bgp_success = configure_bgp_for_device(device_id, bgp_config, ipv4_full, ipv6_full, device_name)
        
        if not bgp_success:
            logging.error(f"[BGP CONFIGURE] Failed to configure BGP for device {device_name}")
            return jsonify({"error": "Failed to configure BGP"}), 500
        
        logging.info(f"[BGP CONFIGURE] Successfully configured interface and BGP for device {device_name}")
        
        # Now handle additional neighbor configuration if needed (for comma-separated neighbor lists)
        # configure_bgp_for_device handles the primary neighbors, but we may need to add additional ones
        success = True
        
        # Configure IPv4 BGP neighbors (handle single or multiple neighbors uniformly)
        if ipv4_enabled and bgp_config.get("bgp_neighbor_ipv4"):
            ipv4_neighbors_str = bgp_config.get("bgp_neighbor_ipv4", "")
            ipv4_neighbors_list = [n.strip() for n in ipv4_neighbors_str.split(",") if n.strip()] if ipv4_neighbors_str else []
            
            if ipv4_neighbors_list:
                logging.info(f"[BGP CONFIGURE] Ensuring {len(ipv4_neighbors_list)} IPv4 BGP neighbor(s) are configured")
                from utils.frr_docker import configure_bgp_neighbor
                
                # Get loopback IPv4 from database for use_loopback_ip check
                loopback_ipv4 = None
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id) if device_id else None
                    if device_data:
                        loopback_ipv4_raw = device_data.get('loopback_ipv4', '')
                        if loopback_ipv4_raw:
                            loopback_ipv4 = loopback_ipv4_raw.strip().split('/')[0]
                except Exception as e:
                    logging.warning(f"[BGP CONFIGURE] Could not retrieve loopback IPv4 from database: {e}")
                
                # Determine update_source_ipv4 based on use_loopback_ip flag (same logic as configure_bgp_for_device)
                use_loopback_ip = bgp_config.get('use_loopback_ip', False)
                if use_loopback_ip and loopback_ipv4:
                    update_source_ipv4 = loopback_ipv4
                    logging.info(f"[BGP CONFIGURE] Using loopback IPv4 {update_source_ipv4} as update-source (use_loopback_ip=True)")
                else:
                    update_source_ipv4 = bgp_config.get("bgp_update_source_ipv4")
                    if not update_source_ipv4:
                        update_source_ipv4 = ipv4.split('/')[0] if ipv4 else None
                        logging.info(f"[BGP CONFIGURE] Using interface IPv4 {update_source_ipv4} as update-source (default)")
                
                for neighbor_ip in ipv4_neighbors_list:
                    neighbor_config_ipv4 = {
                        "neighbor_ip": neighbor_ip,
                        "neighbor_as": bgp_config.get("bgp_neighbor_asn") or bgp_config.get("bgp_remote_asn", ""),
                        "local_as": bgp_config.get("bgp_asn", 65001),
                        "update_source": update_source_ipv4,
                        "keepalive": bgp_config.get("bgp_keepalive", "30"),
                        "hold_time": bgp_config.get("bgp_hold_time", "90"),
                        "protocol": "ipv4"
                    }
                    neighbor_success = configure_bgp_neighbor(device_id, neighbor_config_ipv4, device_name)
                    if not neighbor_success:
                        success = False
                        logging.error(f"[BGP CONFIGURE] Failed to configure IPv4 BGP neighbor {neighbor_ip}")
        
        # Configure IPv6 BGP neighbors (handle single or multiple neighbors uniformly)
        if ipv6_enabled and bgp_config.get("bgp_neighbor_ipv6"):
            ipv6_neighbors_str = bgp_config.get("bgp_neighbor_ipv6", "")
            ipv6_neighbors_list = [n.strip() for n in ipv6_neighbors_str.split(",") if n.strip()] if ipv6_neighbors_str else []
            
            if ipv6_neighbors_list:
                logging.info(f"[BGP CONFIGURE] Ensuring {len(ipv6_neighbors_list)} IPv6 BGP neighbor(s) are configured")
                from utils.frr_docker import configure_bgp_neighbor
                
                # Get loopback IPv6 from database for use_loopback_ip check
                loopback_ipv6 = None
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id) if device_id else None
                    if device_data:
                        loopback_ipv6_raw = device_data.get('loopback_ipv6', '')
                        if loopback_ipv6_raw:
                            loopback_ipv6 = loopback_ipv6_raw.strip().split('/')[0]
                except Exception as e:
                    logging.warning(f"[BGP CONFIGURE] Could not retrieve loopback IPv6 from database: {e}")
                
                # Determine update_source_ipv6 based on use_loopback_ip flag (same logic as configure_bgp_for_device)
                use_loopback_ip = bgp_config.get('use_loopback_ip', False)
                if use_loopback_ip and loopback_ipv6:
                    update_source_ipv6 = loopback_ipv6
                    logging.info(f"[BGP CONFIGURE] Using loopback IPv6 {update_source_ipv6} as update-source (use_loopback_ip=True)")
                else:
                    update_source_ipv6 = bgp_config.get("bgp_update_source_ipv6")
                    if not update_source_ipv6:
                        update_source_ipv6 = ipv6.split('/')[0] if ipv6 else None
                        logging.info(f"[BGP CONFIGURE] Using interface IPv6 {update_source_ipv6} as update-source (default)")
                
                for neighbor_ip in ipv6_neighbors_list:
                    neighbor_config_ipv6 = {
                        "neighbor_ip": neighbor_ip,
                        "neighbor_as": bgp_config.get("bgp_neighbor_asn") or bgp_config.get("bgp_remote_asn", ""),
                        "local_as": bgp_config.get("bgp_asn", 65001),
                        "update_source": update_source_ipv6,
                        "keepalive": bgp_config.get("bgp_keepalive", "30"),
                        "hold_time": bgp_config.get("bgp_hold_time", "90"),
                        "protocol": "ipv6"
                    }
                    neighbor_success = configure_bgp_neighbor(device_id, neighbor_config_ipv6, device_name)
                    if not neighbor_success:
                        success = False
                        logging.error(f"[BGP CONFIGURE] Failed to configure IPv6 BGP neighbor {neighbor_ip}")
        
        if not success:
            logging.warning(f"[BGP CONFIGURE] Some additional BGP neighbors failed to configure, but primary configuration succeeded")
        
        logging.info(f"[BGP CONFIGURE] Successfully configured BGP for {device_name} ({device_id})")
        
        # Add static default route via gateway if configured (BACKGROUND - non-blocking)
        logging.info(f"[BGP ROUTE DEBUG] Checking for gateway in data: {data.keys()}")
        gateway = data.get("gateway", "").strip()
        logging.info(f"[BGP ROUTE DEBUG] Gateway value: '{gateway}'")
        route_added = False
        if gateway and device_id:
            # Skip adding default route in BGP if VXLAN is enabled
            try:
                from utils.device_database import DeviceDatabase
                _db = DeviceDatabase()
                _rec = _db.get_device(device_id) if device_id else None
                if _rec and (_rec.get("vxlan_enabled") is True):
                    logging.info(f"[BGP ROUTE] Skipping default route for {device_name} because VXLAN is enabled")
                    gateway = ""
            except Exception as _vx_exc:
                logging.debug(f"[BGP ROUTE] Could not determine VXLAN status from DB: {_vx_exc}")
        
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

@app.route("/api/device/vxlan/remove", methods=["POST"])
def remove_vxlan_tunnel():
    """Remove a single VXLAN tunnel from a device."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing VXLAN tunnel removal data"}), 400
    
    try:
        device_id = data.get("device_id")
        device_name = data.get("device_name", "")
        vni = data.get("vni")
        vxlan_config = data.get("vxlan_config", {})
        
        if not device_id:
            return jsonify({"error": "Missing device_id"}), 400
        
        if not vni:
            return jsonify({"error": "Missing VNI"}), 400
        
        logging.info(f"[VXLAN REMOVE] Removing tunnel VNI {vni} for device {device_name} (ID: {device_id})")
        
        # Get device from database
        device_info = device_db.get_device(device_id)
        if not device_info:
            return jsonify({"error": f"Device {device_name} not found in database"}), 404
        
        # Get FRR container info
        from utils.frr_docker import FRRDockerManager
        frr_manager = FRRDockerManager()
        container_name = frr_manager._get_container_name(device_id, device_name)
        
        try:
            container = frr_manager.client.containers.get(container_name)
        except Exception:
            container = None
            logging.warning(f"[VXLAN REMOVE] Container {container_name} not found, will only remove from database")
        
        # Tear down the VXLAN tunnel
        try:
            import utils.vxlan as vxlan_utils
            vxlan_utils.tear_down_vxlan_interface(
                device_id,
                vxlan_config,
                container_name=container_name if container else None,
                frr_manager=frr_manager if container else None,
            )
            logging.info(f"[VXLAN REMOVE] Successfully tore down VXLAN tunnel VNI {vni} for device {device_name}")
        except Exception as vxlan_exc:
            logging.warning(f"[VXLAN REMOVE] Failed to tear down VXLAN tunnel VNI {vni}: {vxlan_exc}")
            # Continue to remove from database even if tear down failed
        
        # Remove tunnel from device's vxlan_config in database
        try:
            current_vxlan_config = device_info.get("vxlan_config", {})
            
            # Parse if string
            if isinstance(current_vxlan_config, str):
                try:
                    current_vxlan_config = json.loads(current_vxlan_config) if current_vxlan_config else {}
                except Exception:
                    current_vxlan_config = {}
            
            # Handle multiple tunnels format
            if isinstance(current_vxlan_config, dict) and "tunnels" in current_vxlan_config:
                tunnels = current_vxlan_config.get("tunnels", [])
                # Remove tunnel with matching VNI
                updated_tunnels = [t for t in tunnels if isinstance(t, dict) and t.get("vni") != vni]
                
                if len(updated_tunnels) < len(tunnels):
                    # Tunnel was removed
                    if updated_tunnels:
                        # Update with remaining tunnels
                        current_vxlan_config["tunnels"] = updated_tunnels
                        
                        # Update vxlan_interface to reflect only remaining tunnels
                        remaining_interfaces = []
                        for tunnel in updated_tunnels:
                            tunnel_iface = tunnel.get("vxlan_interface")
                            if tunnel_iface:
                                remaining_interfaces.append(tunnel_iface)
                        
                        update_data = {"vxlan_config": json.dumps(current_vxlan_config)}
                        if remaining_interfaces:
                            update_data["vxlan_interface"] = ", ".join(remaining_interfaces)
                        else:
                            update_data["vxlan_interface"] = None
                        
                        device_db.update_device(device_id, update_data)
                        logging.info(f"[VXLAN REMOVE] Removed tunnel VNI {vni} from device {device_name}, {len(updated_tunnels)} tunnel(s) remaining")
                    else:
                        # No tunnels left, remove VXLAN config entirely
                        # Also remove VXLAN from protocols list
                        protocols = device_info.get("protocols", [])
                        if isinstance(protocols, str):
                            protocols = [p.strip() for p in protocols.split(",") if p.strip()]
                        elif not isinstance(protocols, list):
                            protocols = []
                        
                        if "VXLAN" in protocols:
                            protocols.remove("VXLAN")
                        
                        # Convert back to string if it was originally a string
                        if isinstance(device_info.get("protocols"), str):
                            protocols_str = ",".join(protocols) if protocols else ""
                        else:
                            protocols_str = protocols
                        
                        device_db.update_device(device_id, {
                            "vxlan_config": None,
                            "vxlan_interface": None,
                            "vxlan_state": "Disabled",
                            "protocols": protocols_str
                        })
                        logging.info(f"[VXLAN REMOVE] Removed last tunnel VNI {vni} from device {device_name}, VXLAN config cleared and removed from protocols")
                else:
                    logging.warning(f"[VXLAN REMOVE] Tunnel VNI {vni} not found in device {device_name} config")
            elif isinstance(current_vxlan_config, dict) and current_vxlan_config.get("vni") == vni:
                # Old single tunnel format - remove entire config
                # Also remove VXLAN from protocols list
                protocols = device_info.get("protocols", [])
                if isinstance(protocols, str):
                    protocols = [p.strip() for p in protocols.split(",") if p.strip()]
                elif not isinstance(protocols, list):
                    protocols = []
                
                if "VXLAN" in protocols:
                    protocols.remove("VXLAN")
                
                # Convert back to string if it was originally a string
                if isinstance(device_info.get("protocols"), str):
                    protocols_str = ",".join(protocols) if protocols else ""
                else:
                    protocols_str = protocols
                
                device_db.update_device(device_id, {
                    "vxlan_config": None,
                    "vxlan_interface": None,
                    "vxlan_state": "Disabled",
                    "protocols": protocols_str
                })
                logging.info(f"[VXLAN REMOVE] Removed single tunnel VNI {vni} from device {device_name}, VXLAN config cleared and removed from protocols")
            else:
                logging.warning(f"[VXLAN REMOVE] Tunnel VNI {vni} not found in device {device_name} config")
        except Exception as db_exc:
            logging.error(f"[VXLAN REMOVE] Failed to update database: {db_exc}")
            return jsonify({"error": f"Failed to update database: {str(db_exc)}"}), 500
        
        return jsonify({
            "success": True,
            "message": f"Successfully removed VXLAN tunnel VNI {vni} for device {device_name}",
            "device_id": device_id,
            "device_name": device_name,
            "vni": vni
        }), 200
        
    except Exception as e:
        logging.error(f"[VXLAN REMOVE ERROR] {e}")
        import traceback
        logging.error(f"[VXLAN REMOVE ERROR] Traceback: {traceback.format_exc()}")
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
                from utils.bgp import configure_bgp_for_device
                # Extract device_id from container_name
                device_id = container_name.replace(f"{frr_manager.container_prefix}-", "")
                # CRITICAL: device_name_from_container should be extracted from database using device_id
                # since container names only contain device_id, not device_name
                # Try to get device_name from database, fallback to None
                device_name_from_container = None
                try:
                    from utils.device_database import DeviceDatabase
                    device_db = DeviceDatabase()
                    device_data = device_db.get_device(device_id) if device_id else None
                    if device_data:
                        device_name_from_container = device_data.get('device_name')
                except Exception as e:
                    logging.debug(f"[BGP START] Could not retrieve device_name from database: {e}")
                success = configure_bgp_for_device(device_id, bgp_config, ipv4, ipv6, device_name_from_container)
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
                    except Exception:
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
                            except Exception:
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
        
        # Step 2: Clean up physical interface IPs and reset MTU (if requested)
        if cleanup_physical:
            try:
                physical_result = subprocess.run(["ip", "addr", "show", base_interface],
                                               capture_output=True, text=True, timeout=10)
                
                removed_physical_ips = []
                if physical_result.returncode == 0:
                    # Parse all IP addresses first for reporting
                    for phys_line in physical_result.stdout.split('\n'):
                        phys_line = phys_line.strip()
                        if phys_line.startswith('inet ') and not phys_line.startswith('inet 127.'):
                            ip_part = phys_line.split()[1]
                            removed_physical_ips.append(ip_part)
                        elif phys_line.startswith('inet6 '):
                            # Include ALL IPv6 addresses, including link-local (fe80::)
                            ip_part = phys_line.split()[1]
                            removed_physical_ips.append(ip_part)
                    
                    # Flush all IPv4 addresses (except loopback) - more efficient than individual deletion
                    flush_ipv4_result = subprocess.run(["ip", "addr", "flush", "dev", base_interface, "scope", "global"],
                                                     capture_output=True, text=True, timeout=5)
                    if flush_ipv4_result.returncode == 0:
                        logging.info(f"[INTERFACE RESET] Flushed all IPv4 addresses from {base_interface}")
                    else:
                        # Fallback: remove IPv4 addresses individually
                        logging.warning(f"[INTERFACE RESET] IPv4 flush failed, trying individual removal: {flush_ipv4_result.stderr}")
                        for ip_part in removed_physical_ips[:]:  # Iterate over copy
                            if ':' not in ip_part:  # IPv4 address
                                remove_cmd = ["ip", "addr", "del", ip_part, "dev", base_interface]
                                remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                                if remove_result.returncode != 0:
                                    logging.warning(f"[INTERFACE RESET] Failed to remove IPv4 {ip_part}: {remove_result.stderr}")
                    
                    # Flush all IPv6 addresses (including link-local) - this removes ALL IPv6 addresses
                    flush_ipv6_result = subprocess.run(["ip", "-6", "addr", "flush", "dev", base_interface],
                                                     capture_output=True, text=True, timeout=5)
                    if flush_ipv6_result.returncode == 0:
                        logging.info(f"[INTERFACE RESET] Flushed all IPv6 addresses (including link-local) from {base_interface}")
                    else:
                        # Fallback: remove IPv6 addresses individually
                        logging.warning(f"[INTERFACE RESET] IPv6 flush failed, trying individual removal: {flush_ipv6_result.stderr}")
                        for ip_part in removed_physical_ips[:]:  # Iterate over copy
                            if ':' in ip_part:  # IPv6 address
                                remove_cmd = ["ip", "-6", "addr", "del", ip_part, "dev", base_interface]
                                remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, timeout=5)
                                if remove_result.returncode != 0:
                                    logging.warning(f"[INTERFACE RESET] Failed to remove IPv6 {ip_part}: {remove_result.stderr}")
                    
                    # Reset MTU to default (1500) - this helps avoid MTU mismatch issues
                    # Get current MTU first to check if reset is needed
                    link_result = subprocess.run(["ip", "link", "show", base_interface],
                                               capture_output=True, text=True, timeout=5)
                    current_mtu = None
                    mtu_reset = False
                    if link_result.returncode == 0:
                        # Extract MTU from output (e.g., "mtu 9216" or "mtu 1500")
                        import re
                        mtu_match = re.search(r'mtu\s+(\d+)', link_result.stdout)
                        if mtu_match:
                            current_mtu = int(mtu_match.group(1))
                            # Only reset if MTU is not the default (1500)
                            if current_mtu != 1500:
                                mtu_reset_cmd = ["ip", "link", "set", base_interface, "mtu", "1500"]
                                mtu_result = subprocess.run(mtu_reset_cmd, capture_output=True, text=True, timeout=5)
                                if mtu_result.returncode == 0:
                                    mtu_reset = True
                                    logging.info(f"[INTERFACE RESET] Reset MTU from {current_mtu} to 1500 on {base_interface}")
                                else:
                                    logging.warning(f"[INTERFACE RESET] Failed to reset MTU on {base_interface}: {mtu_result.stderr}")
                            else:
                                logging.debug(f"[INTERFACE RESET] MTU already at default (1500) on {base_interface}, no reset needed")
                    
                    reset_results["physical_cleanup"] = {
                        "success": True,
                        "removed_ips": removed_physical_ips,
                        "mtu_reset": mtu_reset,
                        "previous_mtu": current_mtu if current_mtu and current_mtu != 1500 else None
                    }
                    logging.info(f"[INTERFACE RESET] Cleaned up {len(removed_physical_ips)} IPs from physical interface {base_interface}")
                    if mtu_reset:
                        logging.info(f"[INTERFACE RESET] Reset MTU to 1500 on {base_interface} (was {current_mtu})")
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
        
        # Add MTU reset information if MTU was reset
        if cleanup_physical and reset_results.get("physical_cleanup", {}).get("mtu_reset"):
            previous_mtu = reset_results["physical_cleanup"].get("previous_mtu")
            if previous_mtu:
                message_parts.append(f"MTU reset from {previous_mtu} to 1500")
        
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
                    # Format: Neighbor V AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd PfxSnt Desc
                    # Example: 192.168.0.1 4 65000 23 31 4 0 0 00:05:55 0 2 N/A
                    # parts[8] = uptime (00:05:55)
                    # parts[9] = State/PfxRcd (0 = prefix count when Established, or state name when not Established)
                    uptime = parts[8] if len(parts) > 8 else "00:00:00"
                    
                    # Look for state in the remaining parts - usually contains parentheses or state name
                    state = "Unknown"
                    # First check parts[9] - it could be a state name (Idle, Active, etc.) or a number (prefix count when Established)
                    if len(parts) > 9:
                        # Check if parts[9] is a number (prefix count) - if so, state is Established
                        try:
                            prefix_count = int(parts[9])
                            # If it's a number and we have a valid uptime, the state is Established
                            if uptime != "00:00:00" and ":" in uptime:
                                state = "Established"
                                logging.debug(f"[BGP STATUS] Detected Established state for {neighbor_ip} (prefix count: {prefix_count}, uptime: {uptime})")
                        except ValueError:
                            # parts[9] is not a number, so it's likely a state name
                            state = parts[9]
                    
                    # If state is still "Unknown", check remaining parts for state indicators
                    if state == "Unknown":
                        for i in range(9, len(parts)):
                            if '(' in parts[i] and ')' in parts[i]:
                                state = parts[i]
                                break
                            elif parts[i] in ['Established', 'Active', 'Idle', 'Connect', 'OpenSent', 'OpenConfirm', 'OpenWait']:
                                state = parts[i]
                                break
                    
                    # Special handling for (Policy) state - this indicates BGP is established
                    if state == "(Policy)":
                        state = "Established"
                        logging.info(f"[BGP STATUS] Mapped (Policy) to Established for {neighbor_ip}")
                    
                    # If state is still "Unknown" and we have uptime, check if session is actually established
                    # by looking at the uptime - if it's not "00:00:00" or "never", the session is likely established
                    if state == "Unknown" and uptime != "00:00:00" and uptime != "never" and ":" in uptime:
                        # If we have a valid uptime (not 00:00:00 or never), the BGP session is likely Established
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
    logger.debug(f"*************** {streams}")
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
    Also includes VXLAN bridges and interfaces from FRR containers.
    """
    interfaces = []
    try:
        # Use psutil to fetch network interface details from host
        import re
        for name, stats in psutil.net_if_stats().items():
            # Skip VLAN interfaces (vlan*), loopback (lo*), and other virtual interfaces
            # Skip VXLAN-style bridges (br followed by numbers) on host - these come from containers
            # Skip VXLAN interfaces (vx* followed by numbers and dashes) on host - these come from containers
            is_vxlan_bridge = re.match(r'^br\d+$', name)  # Matches br5000, br5001, etc.
            is_vxlan_interface = re.match(r'^vx\d+-\w+$', name)  # Matches vx5000-9d751a, vx5001-abc123, etc.
            
            if (name.startswith('vlan') or 
                (name.startswith('lo') and name != 'lo') or  # Skip lo* except 'lo' itself
                name.startswith('docker') or 
                name.startswith('br-') or  # Docker bridges
                name.startswith('bridge') or
                name.startswith('virbr') or
                name.startswith('veth') or
                name.startswith('gif') or
                name.startswith('stf') or
                name.startswith('utun') or
                name.startswith('awdl') or
                name.startswith('llw') or
                name.startswith('anpi') or
                is_vxlan_bridge or  # Skip VXLAN bridges on host (they come from containers)
                is_vxlan_interface):  # Skip VXLAN interfaces on host (they come from containers)
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
        
        # Also fetch interfaces from FRR containers (VXLAN bridges and VXLAN interfaces)
        try:
            import docker
            docker_client = docker.from_env()
            
            # Get all running FRR containers (both ostg-frr-* and dhcp-frr-*)
            all_containers = docker_client.containers.list(filters={"status": "running"})
            frr_containers = [
                c for c in all_containers 
                if c.name.startswith("ostg-frr-") or c.name.startswith("dhcp-frr-")
            ]
            
            for container in frr_containers:
                try:
                    # Get interfaces from container using ip link show
                    result = container.exec_run(["ip", "link", "show"], user="root")
                    if result.exit_code == 0:
                        output = result.output.decode('utf-8')
                        # Parse interface names from output
                        import re
                        # Match interface names (e.g., "123: br5000: <BROADCAST,MULTICAST,UP>")
                        interface_pattern = r'^\d+:\s+([^:]+):\s+<([^>]+)>'
                        vxlan_interfaces_found = []
                        for line in output.split('\n'):
                            match = re.match(interface_pattern, line.strip())
                            if match:
                                iface_name = match.group(1)
                                iface_flags = match.group(2)
                                
                                # Only include VXLAN bridges (br*) and VXLAN interfaces (vx*)
                                if iface_name.startswith('br') and not iface_name.startswith('br-'):
                                    vxlan_interfaces_found.append(iface_name)
                                    # VXLAN bridge
                                    is_up = 'UP' in iface_flags or 'LOWER_UP' in iface_flags
                                    
                                    # Get IP address from container
                                    ip_result = container.exec_run(
                                        ["ip", "-4", "addr", "show", iface_name],
                                        user="root"
                                    )
                                    ip_addresses = []
                                    if ip_result.exit_code == 0:
                                        ip_output = ip_result.output.decode('utf-8')
                                        # Extract IP address
                                        ip_match = re.search(r'inet\s+([^\s]+)', ip_output)
                                        if ip_match:
                                            ip_addresses.append(ip_match.group(1))
                                    
                                    # Get MTU
                                    mtu_result = container.exec_run(
                                        ["ip", "link", "show", iface_name],
                                        user="root"
                                    )
                                    mtu = 1500  # Default
                                    if mtu_result.exit_code == 0:
                                        mtu_output = mtu_result.output.decode('utf-8')
                                        mtu_match = re.search(r'mtu\s+(\d+)', mtu_output)
                                        if mtu_match:
                                            mtu = int(mtu_match.group(1))
                                    
                                    interfaces.append({
                                        "name": iface_name,
                                        "status": "up" if is_up else "down",
                                        "mtu": mtu,
                                        "speed": "Unknown",
                                        "ip_addresses": ip_addresses,
                                        "tx": 0,
                                        "rx": 0,
                                        "sent_bytes": 0,
                                        "received_bytes": 0,
                                        "errors": 0,
                                        "container": container.name,  # Mark as container interface
                                    })
                                elif iface_name.startswith('vx'):
                                    # VXLAN interface
                                    vxlan_interfaces_found.append(iface_name)
                                    is_up = 'UP' in iface_flags or 'LOWER_UP' in iface_flags
                                    
                                    # Get MTU
                                    mtu_result = container.exec_run(
                                        ["ip", "link", "show", iface_name],
                                        user="root"
                                    )
                                    mtu = 1450  # Default for VXLAN
                                    if mtu_result.exit_code == 0:
                                        mtu_output = mtu_result.output.decode('utf-8')
                                        mtu_match = re.search(r'mtu\s+(\d+)', mtu_output)
                                        if mtu_match:
                                            mtu = int(mtu_match.group(1))
                                    
                                    interfaces.append({
                                        "name": iface_name,
                                        "status": "up" if is_up else "down",
                                        "mtu": mtu,
                                        "speed": "Unknown",
                                        "ip_addresses": [],
                                        "tx": 0,
                                        "rx": 0,
                                        "sent_bytes": 0,
                                        "received_bytes": 0,
                                        "errors": 0,
                                        "container": container.name,  # Mark as container interface
                                    })
                        
                        if vxlan_interfaces_found:
                            logging.debug(f"[INTERFACES] Found {len(vxlan_interfaces_found)} VXLAN interface(s) in container {container.name}: {', '.join(vxlan_interfaces_found)}")
                except Exception as e:
                    logging.warning(f"Error fetching interfaces from container {container.name}: {e}")
                    continue
        except Exception as e:
            logging.warning(f"Error fetching interfaces from FRR containers: {e}")
        
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
        if ipv6_address or ipv6_gateway:
            try:
                import subprocess
                ipv6_target = ipv6_gateway or ipv6_address
                ping6_cmd = ["ping6", "-c", "1", "-W", "1", ipv6_target]
                result = subprocess.run(ping6_cmd, capture_output=True, text=True, timeout=5)
                arp_results["arp_ipv6_resolved"] = result.returncode == 0
                arp_results["details"]["ipv6_ping_target"] = ipv6_target
                arp_results["details"]["ipv6_ping"] = "success" if result.returncode == 0 else "failed"
                if result.returncode != 0:
                    try:
                        neigh_result = subprocess.run(
                            ["ip", "-6", "neigh", "show", ipv6_target],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        arp_results["details"]["ipv6_neigh"] = neigh_result.stdout.strip() or "no entry"
                    except Exception as neigh_exc:
                        arp_results["details"]["ipv6_neigh"] = f"error: {neigh_exc}"
            except Exception as e:
                arp_results["details"]["ipv6_ping"] = f"error: {e}"
                arp_results["details"]["ipv6_ping_target"] = ipv6_gateway or ipv6_address
        
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
        
        # Determine which address families should be considered mandatory
        requires_ipv4 = bool(ipv4_address)
        requires_ipv6 = bool(ipv6_address or ipv6_gateway)
        try:
            ospf_cfg = device.get("ospf_config") or {}
            if isinstance(ospf_cfg, dict):
                requires_ipv4 = requires_ipv4 or bool(ospf_cfg.get("ipv4_enabled"))
                requires_ipv6 = requires_ipv6 or bool(ospf_cfg.get("ipv6_enabled"))
        except Exception:
            pass
        try:
            isis_cfg = device.get("isis_config") or {}
            if isinstance(isis_cfg, dict):
                requires_ipv4 = requires_ipv4 or bool(isis_cfg.get("ipv4_enabled"))
                requires_ipv6 = requires_ipv6 or bool(isis_cfg.get("ipv6_enabled"))
        except Exception:
            pass
        try:
            bgp_cfg = device.get("bgp_config") or {}
            if isinstance(bgp_cfg, dict):
                requires_ipv4 = requires_ipv4 or bool(bgp_cfg.get("ipv4_enabled"))
                requires_ipv6 = requires_ipv6 or bool(bgp_cfg.get("ipv6_enabled"))
        except Exception:
            pass

        # Determine overall ARP status - all required families must succeed
        overall_ipv4 = (not requires_ipv4) or arp_results["arp_ipv4_resolved"]
        overall_ipv6 = (not requires_ipv6) or arp_results["arp_ipv6_resolved"]
        overall_gateway = (not ipv4_gateway) or arp_results["arp_gateway_resolved"]

        arp_results["arp_resolved"] = overall_ipv4 and overall_ipv6 and overall_gateway
        
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


# ============================================================================
# DHCP Pool Management API Endpoints
# ============================================================================


def _dhcp_pool_to_api(pool: Dict[str, Any]) -> Dict[str, Any]:
    """Convert database DHCP pool record to API representation."""
    return {
        "name": pool.get("pool_name"),
        "pool_start": pool.get("pool_start"),
        "pool_end": pool.get("pool_end"),
        "gateway": pool.get("gateway"),
        "lease_time": pool.get("lease_time"),
        "gateway_routes": pool.get("gateway_routes") or [],
        "description": pool.get("description"),
        "created_at": pool.get("created_at"),
        "updated_at": pool.get("updated_at"),
    }


@app.route("/api/dhcp/pools", methods=["GET"])
def get_dhcp_pools():
    """Return all DHCP pool definitions."""
    try:
        pools = device_db.get_all_dhcp_pools()
        api_pools = [_dhcp_pool_to_api(pool) for pool in pools]
        return jsonify({"pools": api_pools, "count": len(api_pools)}), 200
    except Exception as e:
        logging.error(f"[DHCP POOLS API] Failed to fetch DHCP pools: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dhcp/pools", methods=["POST"])
def create_dhcp_pool():
    """Create a new DHCP pool definition."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        required_fields = ["name", "pool_start", "pool_end"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"Missing required field: {field}"}), 400

        pool_data = {
            "name": data.get("name"),
            "pool_start": data.get("pool_start"),
            "pool_end": data.get("pool_end"),
            "gateway": data.get("gateway"),
            "lease_time": data.get("lease_time"),
            "gateway_routes": data.get("gateway_routes") or data.get("gateway_route"),
            "description": data.get("description"),
        }

        success = device_db.add_dhcp_pool(pool_data)
        if success:
            pool = device_db.get_dhcp_pool(pool_data["name"])
            return jsonify({"message": "DHCP pool created", "pool": _dhcp_pool_to_api(pool)}), 201
        return jsonify({"error": "Failed to create DHCP pool"}), 500
    except Exception as e:
        logging.error(f"[DHCP POOLS API] Failed to create DHCP pool: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dhcp/pools/<pool_name>", methods=["GET"])
def get_dhcp_pool(pool_name):
    """Get a DHCP pool definition by name."""
    try:
        pool = device_db.get_dhcp_pool(pool_name)
        if not pool:
            return jsonify({"error": f"DHCP pool '{pool_name}' not found"}), 404
        return jsonify({"pool": _dhcp_pool_to_api(pool)}), 200
    except Exception as e:
        logging.error(f"[DHCP POOLS API] Failed to fetch DHCP pool '{pool_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dhcp/pools/<pool_name>", methods=["PUT"])
def update_dhcp_pool_endpoint(pool_name):
    """Update an existing DHCP pool definition."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        pool_data = {}
        for key in ["pool_start", "pool_end", "gateway", "lease_time", "description"]:
            if key in data:
                pool_data[key] = data[key]
        if "gateway_routes" in data or "gateway_route" in data:
            pool_data["gateway_routes"] = data.get("gateway_routes") or data.get("gateway_route")

        if not pool_data:
            return jsonify({"error": "No fields to update"}), 400

        success = device_db.update_dhcp_pool(pool_name, pool_data)
        if success:
            pool = device_db.get_dhcp_pool(pool_name)
            return jsonify({"message": "DHCP pool updated", "pool": _dhcp_pool_to_api(pool)}), 200
        return jsonify({"error": "Failed to update DHCP pool"}), 500
    except Exception as e:
        logging.error(f"[DHCP POOLS API] Failed to update DHCP pool '{pool_name}': {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dhcp/pools/<pool_name>", methods=["DELETE"])
def delete_dhcp_pool(pool_name):
    """Delete a DHCP pool definition."""
    try:
        success = device_db.remove_dhcp_pool(pool_name)
        if success:
            return jsonify({"message": f"DHCP pool '{pool_name}' deleted"}), 200
        return jsonify({"error": f"DHCP pool '{pool_name}' not found or could not be deleted"}), 404
    except Exception as e:
        logging.error(f"[DHCP POOLS API] Failed to delete DHCP pool '{pool_name}': {e}")
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


# ============================================================================
# DPDK API Endpoints
# ============================================================================

@app.route("/api/dpdk/status", methods=["GET"])
def dpdk_status():
    """Get DPDK installation and interface status."""
    try:
        import os
        import subprocess
        
        # Check if DPDK is installed
        dpdk_installed = False
        tx_worker_exists = False
        hugepages_configured = False
        hugepages_available = 0
        hugepage_size = "N/A"
        iommu_enabled = False
        iommu_details = ""
        vfio_pci_loaded = False
        vfio_loaded = False
        
        # Check for DPDK libraries
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", "libdpdk"],
                capture_output=True,
                timeout=5
            )
            dpdk_installed = result.returncode == 0
        except Exception:
            pass
        
        # Check for tx_worker binary
        tx_worker_paths = [
            "/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker",
            "/usr/local/bin/tx_worker",
            "./resources/dpdk/tx_worker/build/tx_worker"
        ]
        for path in tx_worker_paths:
            if os.path.exists(path):
                tx_worker_exists = True
                break
        
        # Check hugepages
        try:
            with open("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages", "r") as f:
                hugepages_available = int(f.read().strip())
            hugepages_configured = hugepages_available > 0
            hugepage_size = "2MB"
        except Exception:
            pass
        
        # Check IOMMU status
        try:
            with open("/proc/cmdline", "r") as f:
                cmdline = f.read()
            
            # Check for Intel IOMMU
            if "intel_iommu=on" in cmdline:
                iommu_enabled = True
                iommu_details = "Intel IOMMU enabled"
                if "iommu=pt" in cmdline:
                    iommu_details += " (passthrough mode)"
            # Check for AMD IOMMU
            elif "amd_iommu=on" in cmdline:
                iommu_enabled = True
                iommu_details = "AMD IOMMU enabled"
                if "iommu=pt" in cmdline:
                    iommu_details += " (passthrough mode)"
            else:
                iommu_details = "IOMMU not enabled in kernel (required for vfio-pci)"
        except Exception as e:
            iommu_details = f"Could not check IOMMU: {e}"
        
        # Check if vfio-pci module is loaded or builtin
        try:
            # First check if modules are builtin (always available)
            vfio_builtin = False
            vfio_pci_builtin = False
            try:
                result = subprocess.run(
                    ["modinfo", "vfio"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and "(builtin)" in result.stdout:
                    vfio_builtin = True
                    vfio_loaded = True
            except Exception:
                pass
            
            try:
                result = subprocess.run(
                    ["modinfo", "vfio-pci"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and "(builtin)" in result.stdout:
                    vfio_pci_builtin = True
                    vfio_pci_loaded = True
            except Exception:
                pass
            
            # If not builtin, check lsmod for loadable modules
            if not vfio_pci_builtin or not vfio_loaded:
                result = subprocess.run(
                    ["lsmod"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    output = result.stdout.lower()
                    # lsmod output format: "vfio_pci               45056  0"
                    # Check for vfio_pci module (with underscore, as it appears in lsmod)
                    import re
                    if not vfio_pci_loaded:
                        vfio_pci_loaded = bool(re.search(r'^vfio_pci\s', output, re.MULTILINE))
                    # Check for vfio module (base module) - must be a separate line starting with "vfio"
                    if not vfio_loaded:
                        vfio_loaded = bool(re.search(r'^vfio\s', output, re.MULTILINE))
        except Exception as e:
            logging.warning(f"[DPDK STATUS] Error checking VFIO modules: {e}")
            pass
        
        # Get interface status using dpdk-devbind.py if available
        interfaces = []
        dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
        if os.path.exists(dpdk_bind_script):
            try:
                result = subprocess.run(
                    ["sudo", dpdk_bind_script, "status"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    # Parse output (simplified - would need proper parsing)
                    interfaces = _parse_dpdk_devbind_status(result.stdout)
            except Exception as e:
                logging.debug(f"[DPDK STATUS] Failed to get interface status: {e}")
        
        return jsonify({
            "dpdk_installed": dpdk_installed,
            "tx_worker_exists": tx_worker_exists,
            "hugepages_configured": hugepages_configured,
            "hugepages_available": hugepages_available,
            "hugepage_size": hugepage_size,
            "iommu_enabled": iommu_enabled,
            "iommu_details": iommu_details,
            "vfio_pci_loaded": vfio_pci_loaded,
            "vfio_loaded": vfio_loaded,
            "interfaces": interfaces
        }), 200
        
    except Exception as e:
        logging.error(f"[DPDK STATUS] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/interfaces", methods=["GET"])
def dpdk_interfaces():
    """Get available interfaces for DPDK operations."""
    try:
        import subprocess
        import os
        import glob
        
        interfaces = []
        
        # Method 1: Try dpdk-devbind.py directly (most reliable)
        dpdk_devbind = None
        for path in [
            "/opt/OSTG/resources/dpdk/dpdk/usertools/dpdk-devbind.py",
            "/usr/local/share/dpdk/usertools/dpdk-devbind.py",
            "/usr/share/dpdk/usertools/dpdk-devbind.py",
            "/root/SURAJ/dpdk/usertools/dpdk-devbind.py"
        ]:
            if os.path.exists(path):
                dpdk_devbind = path
                break
        
        if dpdk_devbind:
            try:
                result = subprocess.run(
                    ["sudo", "python3", dpdk_devbind, "--status"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    interfaces = _parse_dpdk_devbind_status(result.stdout)
                    if interfaces:
                        logging.info(f"[DPDK INTERFACES] Found {len(interfaces)} interfaces via dpdk-devbind.py")
            except Exception as e:
                logging.debug(f"[DPDK INTERFACES] dpdk-devbind.py failed: {e}")
        
        # Method 2: Try dpdk_bind.sh status
        if not interfaces:
            dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
            if os.path.exists(dpdk_bind_script):
                try:
                    result = subprocess.run(
                        ["sudo", dpdk_bind_script, "status"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        interfaces = _parse_dpdk_devbind_status(result.stdout)
                        if interfaces:
                            logging.info(f"[DPDK INTERFACES] Found {len(interfaces)} interfaces via dpdk_bind.sh")
                except Exception as e:
                    logging.debug(f"[DPDK INTERFACES] dpdk_bind.sh failed: {e}")
        
        # Method 3: Use /sys filesystem to get interface details
        if not interfaces:
            interfaces = _get_interfaces_from_sys()
            if interfaces:
                logging.info(f"[DPDK INTERFACES] Found {len(interfaces)} interfaces via /sys filesystem")
        
        # Method 4: Fallback to ip link (minimal info)
        if not interfaces:
            try:
                result = subprocess.run(
                    ["ip", "link", "show"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    interfaces = _parse_ip_link_output(result.stdout)
                    # Enhance with PCI info from /sys
                    for iface in interfaces:
                        pci = _get_pci_from_interface(iface["name"])
                        if pci:
                            iface["pci"] = pci
                            iface["driver"] = _get_driver_from_pci(pci) or "unknown"
                            iface["vendor"] = _get_vendor_from_pci(pci) or "unknown"
            except Exception as e:
                logging.debug(f"[DPDK INTERFACES] Failed to get system interfaces: {e}")
        
        return jsonify({"interfaces": interfaces}), 200
        
    except Exception as e:
        logging.error(f"[DPDK INTERFACES] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/bind", methods=["POST"])
def dpdk_bind():
    """Bind an interface to DPDK."""
    try:
        import subprocess
        import os
        
        data = request.get_json() or {}
        interface = data.get("interface")
        pci = data.get("pci")
        force = data.get("force", False)
        
        if not interface and not pci:
            return jsonify({"error": "interface or pci required"}), 400
        
        # Validate PCI format if provided (should be like 0000:XX:XX.X)
        if pci and (pci == "N/A" or ":" not in pci):
            pci = None  # Ignore invalid PCI
        
        # If PCI is invalid, try to get it from interface name
        if not pci and interface:
            pci = _get_pci_from_interface(interface)
        
        dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
        if not os.path.exists(dpdk_bind_script):
            return jsonify({"error": "dpdk_bind.sh not found"}), 404
        
        # Build command - dpdk_bind.sh expects PCI as positional argument
        # If we have interface name but no PCI, convert interface to PCI
        if not pci and interface:
            pci = _get_pci_from_interface(interface)
        
        if not pci or ":" not in pci:
            return jsonify({"error": f"Could not determine PCI address for interface {interface}"}), 400
        
        # dpdk_bind.sh expects: bind <PCI> [--force]
        cmd = ["sudo", dpdk_bind_script, "bind", pci]
        if force:
            cmd.append("--force")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            return jsonify({
                "success": True,
                "message": f"Interface {'bound' if result.returncode == 0 else 'binding attempted'}",
                "output": result.stdout
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": result.stderr or "Binding failed",
                "output": result.stdout
            }), 500
        
    except Exception as e:
        logging.error(f"[DPDK BIND] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/unbind", methods=["POST"])
def dpdk_unbind():
    """Unbind an interface from DPDK."""
    try:
        import subprocess
        import os
        
        data = request.get_json() or {}
        interface = data.get("interface")
        pci = data.get("pci")
        kernel_driver = data.get("kernel_driver", "")
        
        # Validate PCI format if provided
        if pci and (pci == "N/A" or ":" not in pci):
            pci = None
        
        # If we have interface name but no PCI, convert interface to PCI
        if not pci and interface:
            pci = _get_pci_from_interface(interface)
        
        if not pci or ":" not in pci:
            return jsonify({"error": f"Could not determine PCI address for interface {interface}"}), 400
        
        dpdk_bind_script = "/opt/OSTG/resources/dpdk/dpdk_bind.sh"
        if not os.path.exists(dpdk_bind_script):
            return jsonify({"error": "dpdk_bind.sh not found"}), 404
        
        # dpdk_bind.sh expects: unbind <PCI> [--kernel-driver <driver>]
        cmd = ["sudo", dpdk_bind_script, "unbind", pci]
        if kernel_driver:
            cmd.extend(["--kernel-driver", kernel_driver])
        
        # Increased timeout to accommodate retry logic and interface detection (up to 60 seconds)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Check if script output indicates success (even if exit code is non-zero)
        # The script may return success even if binding to kernel driver fails,
        # as long as the device is unbound from DPDK (which is the main goal)
        output_lower = (result.stdout or "").lower()
        success_indicators = [
            "unbind operation successful",
            "device not bound to dpdk",
            "device remains unbound",
            "main goal achieved",
            "✓"
        ]
        is_success = result.returncode == 0 or any(indicator in output_lower for indicator in success_indicators)
        
        if is_success:
            return jsonify({
                "success": True,
                "message": "Interface unbound from DPDK successfully",
                "output": result.stdout
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": result.stderr or "Unbinding failed",
                "output": result.stdout
            }), 500
        
    except Exception as e:
        logging.error(f"[DPDK UNBIND] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/verify", methods=["GET"])
def dpdk_verify():
    """Verify DPDK installation."""
    try:
        import os
        import subprocess
        
        messages = []
        dpdk_libraries = False
        tx_worker_binary = False
        hugepages = False
        kernel_modules = False
        
        # Check DPDK libraries
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", "libdpdk"],
                capture_output=True,
                timeout=5
            )
            dpdk_libraries = result.returncode == 0
            if dpdk_libraries:
                version_result = subprocess.run(
                    ["pkg-config", "--modversion", "libdpdk"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if version_result.returncode == 0:
                    messages.append(f"DPDK version: {version_result.stdout.strip()}")
            else:
                messages.append("DPDK libraries not found")
        except Exception as e:
            messages.append(f"Error checking DPDK libraries: {e}")
        
        # Check tx_worker binary
        tx_worker_paths = [
            "/opt/OSTG/resources/dpdk/tx_worker/build/tx_worker",
            "/usr/local/bin/tx_worker",
            "./resources/dpdk/tx_worker/build/tx_worker"
        ]
        for path in tx_worker_paths:
            if os.path.exists(path):
                tx_worker_binary = True
                messages.append(f"tx_worker found: {path}")
                break
        if not tx_worker_binary:
            messages.append("tx_worker binary not found")
        
        # Check hugepages
        try:
            with open("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages", "r") as f:
                nr_hugepages = int(f.read().strip())
            if nr_hugepages > 0:
                hugepages = True
                messages.append(f"Hugepages configured: {nr_hugepages} x 2MB")
            else:
                messages.append("Hugepages not configured")
        except Exception as e:
            messages.append(f"Error checking hugepages: {e}")
        
        # Check kernel modules (including builtin modules)
        try:
            # Check loaded modules first
            result = subprocess.run(
                ["lsmod"],
                capture_output=True,
                text=True,
                timeout=5
            )
            modules_loaded = False
            if result.returncode == 0:
                output = result.stdout.lower()
                if "vfio" in output or "uio" in output:
                    modules_loaded = True
            
            # Check for builtin modules using modinfo
            builtin_modules = []
            for module in ["vfio-pci", "vfio", "vfio_iommu_type1", "uio_pci_generic"]:
                try:
                    modinfo_result = subprocess.run(
                        ["modinfo", module],
                        capture_output=True,
                        text=True,
                        timeout=3
                    )
                    if modinfo_result.returncode == 0:
                        if "(builtin)" in modinfo_result.stdout or "filename:" not in modinfo_result.stdout:
                            builtin_modules.append(module)
                except Exception:
                    pass
            
            if modules_loaded or builtin_modules:
                kernel_modules = True
                if builtin_modules:
                    messages.append(f"DPDK kernel modules: {', '.join(builtin_modules)} (builtin)")
                if modules_loaded:
                    loaded_list = []
                    if "vfio" in result.stdout.lower():
                        loaded_list.append("vfio")
                    if "uio" in result.stdout.lower():
                        loaded_list.append("uio")
                    if loaded_list:
                        messages.append(f"DPDK kernel modules loaded: {', '.join(loaded_list)}")
            else:
                messages.append("DPDK kernel modules not loaded (vfio-pci, vfio, uio_pci_generic)")
        except Exception as e:
            messages.append(f"Error checking kernel modules: {e}")
        
        return jsonify({
            "dpdk_libraries": dpdk_libraries,
            "tx_worker_binary": tx_worker_binary,
            "hugepages": hugepages,
            "kernel_modules": kernel_modules,
            "messages": messages
        }), 200
        
    except Exception as e:
        logging.error(f"[DPDK VERIFY] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/hugepages", methods=["POST"])
def dpdk_hugepages():
    """Configure hugepages."""
    try:
        import subprocess
        
        data = request.get_json() or {}
        num_pages = data.get("num_pages")
        page_size = data.get("page_size", "2MB")
        
        if not num_pages:
            return jsonify({"error": "num_pages required"}), 400
        
        # Configure hugepages
        if page_size == "2MB":
            hugepage_file = "/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages"
        else:
            return jsonify({"error": f"Unsupported page size: {page_size}"}), 400
        
        try:
            with open(hugepage_file, "w") as f:
                f.write(str(num_pages))
            
            # Mount hugepages if not already mounted
            mount_point = "/mnt/huge"
            result = subprocess.run(
                ["mountpoint", "-q", mount_point],
                capture_output=True
            )
            if result.returncode != 0:
                subprocess.run(
                    ["mkdir", "-p", mount_point],
                    check=True
                )
                subprocess.run(
                    ["mount", "-t", "hugetlbfs", "nodev", mount_point],
                    check=True
                )
            
            return jsonify({
                "success": True,
                "message": f"Configured {num_pages} x {page_size} hugepages"
            }), 200
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Failed to configure hugepages: {str(e)}"
            }), 500
        
    except Exception as e:
        logging.error(f"[DPDK HUGEPAGES] Error: {e}")
        return jsonify({"error": str(e)}), 500


def _parse_dpdk_devbind_status(output):
    """Parse dpdk-devbind.py --status output."""
    interfaces = []
    lines = output.split('\n')
    current_section = None
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('='):
            continue
        
        # Detect section type
        if 'Network devices using DPDK-compatible driver' in line:
            current_section = 'network_dpdk'
            continue
        elif 'Network devices using kernel driver' in line:
            current_section = 'network_kernel'
            continue
        elif 'Other Network devices' in line:
            current_section = 'network_other'
            continue
        elif 'Network devices' in line:
            # Generic "Network devices" - default to kernel
            current_section = 'network_kernel'
            continue
        
        # Parse all network device sections
        if current_section in ['network_kernel', 'network_other', 'network_dpdk']:
            # Parse line like: "0000:8a:00.0 'Device 1760' numa_node=1 if=ens5np0 drv=bnxt_en unused=vfio-pci,uio_pci_generic"
            # Or: "0000:c9:00.0 'Device 1760' numa_node=1 unused=bnxt_en,vfio-pci" (no interface, bound to vfio-pci)
            parts = line.split()
            if len(parts) >= 3:
                # First part should be PCI address (format: 0000:XX:XX.X)
                pci = parts[0]
                if not pci or ':' not in pci:
                    continue
                
                iface = None
                driver = None
                vendor = "unknown"
                unused_drivers = []
                
                for part in parts:
                    if part.startswith('if='):
                        iface = part.split('=')[1]
                    elif part.startswith('drv='):
                        driver = part.split('=')[1]
                    elif part.startswith('vendor='):
                        vendor = part.split('=')[1]
                    elif part.startswith('unused='):
                        unused_drivers = part.split('=')[1].split(',')
                
                # Get vendor from lspci if available
                vendor = _get_vendor_from_pci(pci) or vendor
                
                # Get driver from /sys if not found in output (most reliable)
                sys_driver = _get_driver_from_pci(pci)
                if sys_driver:
                    driver = sys_driver
                elif not driver or driver == "unknown":
                    # Check if device is in DPDK-compatible driver section
                    if current_section == 'network_dpdk':
                        # Device is bound to DPDK driver, check /sys
                        if sys_driver:
                            driver = sys_driver
                        else:
                            driver = "vfio-pci"  # Default assumption for DPDK section
                    # Check if device is bound to vfio-pci (in "Other Network devices" section)
                    elif current_section == 'network_other' and 'vfio-pci' not in unused_drivers:
                        # Check /sys to see if actually bound to vfio-pci
                        if sys_driver == "vfio-pci":
                            driver = "vfio-pci"
                        else:
                            driver = "unknown"
                    else:
                        driver = "unknown"
                
                # Determine status: bound to DPDK driver, kernel driver, or unbound
                is_bound_to_dpdk = driver in ["vfio-pci", "uio_pci_generic"]
                is_bound_to_kernel = driver and driver != "unknown" and not is_bound_to_dpdk
                is_unbound = not driver or driver == "unknown" or driver == ""
                
                # Extract kernel driver from unused_drivers for unbound devices
                kernel_driver = None
                # For unbound devices (not bound to DPDK), extract kernel driver from unused list
                if is_unbound and unused_drivers:
                    # For unbound devices, find the kernel driver (not a DPDK driver)
                    dpdk_drivers = ["vfio-pci", "uio_pci_generic", "igb_uio"]
                    for unused_drv in unused_drivers:
                        if unused_drv not in dpdk_drivers:
                            kernel_driver = unused_drv
                            break
                elif is_bound_to_kernel:
                    # For kernel-bound devices, the driver itself is the kernel driver
                    kernel_driver = driver
                
                # Include interface even if no interface name (for DPDK-bound devices)
                # Use PCI address as display name if no interface name
                display_name = iface if iface else f"{pci} (no interface)"
                
                # Determine status string
                if is_bound_to_dpdk:
                    status_str = "dpdk-bound"
                elif is_bound_to_kernel:
                    status_str = "kernel-bound"
                else:
                    status_str = "unbound"
                
                interfaces.append({
                    "name": display_name,
                    "pci": pci,
                    "driver": driver or "unknown",
                    "vendor": vendor or "unknown",
                    "status": status_str,
                    "kernel_driver": kernel_driver or "",
                    "has_interface": iface is not None  # Track if interface name exists
                })
    
    return interfaces


def _parse_ip_link_output(output):
    """Parse ip link show output to get basic interface info."""
    interfaces = []
    lines = output.split('\n')
    
    for line in lines:
        line = line.strip()
        if ':' in line and not line.startswith(' '):
            # Extract interface name
            parts = line.split(':')
            if len(parts) >= 2:
                iface_name = parts[1].strip().split()[0]
                # Skip loopback and virtual interfaces
                if iface_name.startswith('lo') or iface_name.startswith('docker') or iface_name.startswith('veth'):
                    continue
                interfaces.append({
                    "name": iface_name,
                    "pci": "N/A",
                    "driver": "unknown",
                    "vendor": "unknown",
                    "status": "unknown",
                    "kernel_driver": ""
                })
    
    return interfaces


def _get_interfaces_from_sys():
    """Get interface information from /sys filesystem."""
    import os
    import glob
    
    interfaces = []
    
    # Find all network interfaces in /sys/class/net
    net_dir = "/sys/class/net"
    if not os.path.exists(net_dir):
        return interfaces
    
    for iface_name in os.listdir(net_dir):
        # Skip loopback and virtual interfaces
        if iface_name.startswith('lo') or iface_name.startswith('docker') or iface_name.startswith('veth'):
            continue
        
        # Get PCI address from /sys/class/net/<iface>/device
        device_link = os.path.join(net_dir, iface_name, "device")
        if os.path.exists(device_link):
            pci = os.path.basename(os.readlink(device_link))
            
            # Get driver
            driver = _get_driver_from_pci(pci)
            
            # Get vendor
            vendor = _get_vendor_from_pci(pci)
            
            # Determine status
            status = "bound" if driver in ["vfio-pci", "uio_pci_generic"] else "unbound"
            
            interfaces.append({
                "name": iface_name,
                "pci": pci,
                "driver": driver or "unknown",
                "vendor": vendor or "unknown",
                "status": status,
                "kernel_driver": driver if driver not in ["vfio-pci", "uio_pci_generic"] else ""
            })
    
    return interfaces


def _get_pci_from_interface(iface_name):
    """Get PCI address from interface name using /sys."""
    import os
    
    device_link = f"/sys/class/net/{iface_name}/device"
    if os.path.exists(device_link):
        return os.path.basename(os.readlink(device_link))
    return None


def _get_driver_from_pci(pci):
    """Get driver name from PCI address."""
    import os
    
    driver_link = f"/sys/bus/pci/devices/{pci}/driver"
    if os.path.exists(driver_link):
        return os.path.basename(os.readlink(driver_link))
    return None


def _get_vendor_from_pci(pci):
    """Get vendor name from PCI address using lspci."""
    import subprocess
    
    try:
        result = subprocess.run(
            ["lspci", "-n", "-s", pci],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split()
            if len(parts) >= 3:
                vendor_id = parts[2].split(':')[0]
                vendor_map = {
                    "15b3": "NVIDIA/Mellanox",
                    "14e4": "Broadcom",
                    "8086": "Intel",
                    "1022": "AMD",
                    "1023": "AMD",
                    "1002": "AMD"
                }
                return vendor_map.get(vendor_id, f"Unknown ({vendor_id})")
    except Exception:
        pass
    
    return None


@app.route("/api/dpdk/cpu-vendor", methods=["GET"])
def dpdk_cpu_vendor():
    """Detect CPU vendor (Intel or AMD) for IOMMU configuration."""
    try:
        import subprocess
        
        vendor = "intel"  # default
        
        # Check /proc/cpuinfo for vendor
        try:
            with open("/proc/cpuinfo", "r") as f:
                cpuinfo = f.read()
                if "AuthenticAMD" in cpuinfo or "amd" in cpuinfo.lower():
                    vendor = "amd"
                elif "GenuineIntel" in cpuinfo or "intel" in cpuinfo.lower():
                    vendor = "intel"
        except Exception:
            pass
        
        # Also check lscpu output
        try:
            result = subprocess.run(
                ["lscpu"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                output = result.stdout.lower()
                if "amd" in output or "authenticamd" in output:
                    vendor = "amd"
                elif "intel" in output or "genuineintel" in output:
                    vendor = "intel"
        except Exception:
            pass
        
        return jsonify({
            "vendor": vendor,
            "iommu_param": "amd_iommu=on" if vendor == "amd" else "intel_iommu=on"
        }), 200
        
    except Exception as e:
        logging.error(f"[DPDK CPU VENDOR] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/load_modules", methods=["POST"])
def dpdk_load_modules():
    """Load VFIO kernel modules (vfio-pci and vfio)."""
    logging.info("[DPDK LOAD MODULES] API endpoint called")
    try:
        import subprocess
        import os
        
        modules_to_load = ["vfio", "vfio-pci"]
        loaded_modules = []
        failed_modules = []
        
        # Check if running as root
        is_root = os.geteuid() == 0
        logging.info(f"[DPDK LOAD MODULES] Running as root: {is_root}, UID: {os.geteuid()}")
        
        # Check if modprobe exists - try common paths
        modprobe_path = None
        for path in ["/sbin/modprobe", "/usr/sbin/modprobe", "/bin/modprobe", "modprobe"]:
            if path == "modprobe":
                # Try to find modprobe in PATH
                try:
                    result = subprocess.run(["which", "modprobe"], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        modprobe_path = result.stdout.strip()
                        break
                except Exception:
                    pass
            elif os.path.exists(path):
                modprobe_path = path
                break
        
        if not modprobe_path:
            return jsonify({
                "success": False,
                "message": "modprobe command not found. Cannot load kernel modules.",
                "loaded": [],
                "failed": [{"module": m, "error": "modprobe not found"} for m in modules_to_load]
            }), 500
        
        logging.info(f"[DPDK LOAD MODULES] Using modprobe at: {modprobe_path}")
        
        for module in modules_to_load:
            try:
                # First check if module is builtin (always available, can't be loaded)
                try:
                    modinfo_result = subprocess.run(
                        ["modinfo", module],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if modinfo_result.returncode == 0 and "(builtin)" in modinfo_result.stdout:
                        loaded_modules.append(module)
                        logging.info(f"[DPDK LOAD MODULES] {module} is builtin (always available)")
                        continue
                except Exception as e:
                    logging.debug(f"[DPDK LOAD MODULES] modinfo check for {module} failed: {e}")
                
                # Check if module is already loaded (lsmod shows vfio_pci, not vfio-pci)
                result = subprocess.run(
                    ["lsmod"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    import re
                    module_pattern = module.replace("-", "_")
                    if re.search(rf'^{module_pattern}\s', result.stdout, re.MULTILINE | re.IGNORECASE):
                        loaded_modules.append(module)
                        logging.info(f"[DPDK LOAD MODULES] {module} is already loaded")
                        continue
                
                # Load the module - try modprobe directly first (service runs as root)
                result = None
                error_msg = None
                
                # Try modprobe directly first (since service runs as root)
                try:
                    logging.info(f"[DPDK LOAD MODULES] Attempting to load {module} using {modprobe_path}")
                    result = subprocess.run(
                        [modprobe_path, module],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    logging.info(f"[DPDK LOAD MODULES] modprobe {module} returned: exit_code={result.returncode}, stdout='{result.stdout}', stderr='{result.stderr}'")
                    
                    if result.returncode == 0:
                        # Verify it's actually loaded
                        verify_result = subprocess.run(
                            ["lsmod"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if verify_result.returncode == 0:
                            module_pattern = module.replace("-", "_")
                            if module_pattern in verify_result.stdout.lower():
                                loaded_modules.append(module)
                                logging.info(f"[DPDK LOAD MODULES] Successfully loaded and verified {module}")
                            else:
                                # Module command succeeded but not in lsmod - might be a dependency issue
                                error_msg = f"modprobe succeeded but module {module} not found in lsmod. Check dependencies: {result.stderr or result.stdout}"
                                logging.warning(f"[DPDK LOAD MODULES] {error_msg}")
                                failed_modules.append({
                                    "module": module,
                                    "error": error_msg
                                })
                        else:
                            error_msg = f"Failed to verify module load: lsmod failed"
                            logging.error(f"[DPDK LOAD MODULES] {error_msg}")
                            failed_modules.append({
                                "module": module,
                                "error": error_msg
                            })
                    else:
                        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                        logging.error(f"[DPDK LOAD MODULES] modprobe {module} failed (exit code {result.returncode}): stderr='{result.stderr}', stdout='{result.stdout}'")
                        failed_modules.append({
                            "module": module,
                            "error": error_msg
                        })
                except FileNotFoundError:
                    error_msg = "modprobe command not found"
                    logging.error(f"[DPDK LOAD MODULES] {error_msg}")
                except subprocess.TimeoutExpired:
                    error_msg = "modprobe command timed out"
                    logging.error(f"[DPDK LOAD MODULES] {error_msg}")
                except Exception as e:
                    error_msg = f"modprobe failed: {str(e)}"
                    logging.error(f"[DPDK LOAD MODULES] {error_msg}")
                
                # If direct modprobe failed and not root, try with sudo as fallback
                if error_msg and module not in loaded_modules and not is_root:
                    try:
                        logging.info(f"[DPDK LOAD MODULES] Trying sudo modprobe {module} as fallback")
                        result = subprocess.run(
                            ["sudo", modprobe_path, module],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if result.returncode == 0:
                            loaded_modules.append(module)
                            logging.info(f"[DPDK LOAD MODULES] Successfully loaded {module} with sudo")
                            continue
                        else:
                            error_msg = result.stderr.strip() or result.stdout.strip() or error_msg or "Unknown error"
                            logging.warning(f"[DPDK LOAD MODULES] sudo modprobe {module} also failed: {error_msg}")
                    except Exception as e:
                        error_msg = f"Both modprobe and sudo modprobe failed: {str(e)}"
                        logging.error(f"[DPDK LOAD MODULES] {error_msg}")
                
                # If we get here, loading failed
                failed_modules.append({
                    "module": module,
                    "error": error_msg or "Unknown error - check server logs"
                })
            except subprocess.TimeoutExpired:
                failed_modules.append({
                    "module": module,
                    "error": "Command timed out"
                })
            except Exception as e:
                failed_modules.append({
                    "module": module,
                    "error": str(e)
                })
        
        if failed_modules:
            # Provide detailed error information
            error_details = []
            for fm in failed_modules:
                error_details.append(f"{fm['module']}: {fm['error']}")
            
            error_message = f"Failed to load modules: {', '.join(error_details)}"
            error_message += "\n\nPossible causes:"
            error_message += "\n1. Systemd service has ProtectKernelModules=true (check systemd service file)"
            error_message += "\n2. Service doesn't have sudo permissions"
            error_message += "\n3. Modules not available in kernel (check: lsmod | grep vfio)"
            error_message += "\n4. IOMMU not enabled (required for vfio-pci)"
            error_message += "\n\nTry manually: sudo modprobe vfio && sudo modprobe vfio-pci"
            
            logging.error(f"[DPDK LOAD MODULES] {error_message}")
            return jsonify({
                "success": False,
                "message": error_message,
                "loaded": loaded_modules,
                "failed": failed_modules
            }), 500
        
        return jsonify({
            "success": True,
            "message": f"Successfully loaded modules: {', '.join(loaded_modules)}",
            "loaded": loaded_modules
        }), 200
        
    except Exception as e:
        logging.error(f"[DPDK LOAD MODULES ERROR] {e}")
        return jsonify({
            "success": False,
            "message": f"Failed to load modules: {str(e)}"
        }), 500

@app.route("/api/dpdk/iommu", methods=["POST"])
def dpdk_configure_iommu():
    """Configure IOMMU in GRUB and optionally reboot."""
    try:
        import subprocess
        import os
        import re
        
        data = request.get_json() or {}
        enable = data.get("enable", True)
        cpu_vendor = data.get("cpu_vendor", "intel").lower()
        reboot = data.get("reboot", False)
        
        grub_file = "/etc/default/grub"
        
        # Check if GRUB file exists
        if not os.path.exists(grub_file):
            return jsonify({
                "success": False,
                "message": f"GRUB configuration file not found: {grub_file}"
            }), 404
        
        # Read current GRUB configuration
        try:
            with open(grub_file, "r") as f:
                grub_content = f.read()
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Failed to read GRUB file: {str(e)}"
            }), 500
        
        # Determine IOMMU parameters based on CPU vendor
        if cpu_vendor == "amd":
            iommu_param = "amd_iommu=on"
        else:
            iommu_param = "intel_iommu=on"
        
        iommu_params = f"{iommu_param} iommu=pt"
        
        # Find GRUB_CMDLINE_LINUX line
        lines = grub_content.split('\n')
        cmdline_line_index = None
        for i, line in enumerate(lines):
            if line.startswith("GRUB_CMDLINE_LINUX="):
                cmdline_line_index = i
                break
        
        if cmdline_line_index is None:
            return jsonify({
                "success": False,
                "message": "GRUB_CMDLINE_LINUX not found in GRUB configuration"
            }), 400
        
        # Parse current cmdline
        cmdline_line = lines[cmdline_line_index]
        # Extract content between quotes
        match = re.search(r'GRUB_CMDLINE_LINUX="(.*)"', cmdline_line)
        if match:
            current_cmdline = match.group(1)
        else:
            # Try without quotes
            match = re.search(r'GRUB_CMDLINE_LINUX=(.*)', cmdline_line)
            if match:
                current_cmdline = match.group(1).strip('"\'')
            else:
                current_cmdline = ""
        
        # Modify cmdline
        if enable:
            # Add IOMMU parameters if not present
            if iommu_param not in current_cmdline:
                if current_cmdline:
                    new_cmdline = f"{current_cmdline} {iommu_params}"
                else:
                    new_cmdline = iommu_params
            else:
                # Already enabled, just ensure iommu=pt is there
                if "iommu=pt" not in current_cmdline:
                    new_cmdline = current_cmdline.replace(iommu_param, iommu_params)
                else:
                    new_cmdline = current_cmdline  # Already configured
        else:
            # Remove IOMMU parameters
            new_cmdline = current_cmdline
            # Remove both intel_iommu=on and amd_iommu=on
            new_cmdline = re.sub(r'\b(intel_iommu|amd_iommu)=on\b', '', new_cmdline)
            new_cmdline = re.sub(r'\biommu=pt\b', '', new_cmdline)
            new_cmdline = re.sub(r'\s+', ' ', new_cmdline).strip()
        
        # Update the line
        lines[cmdline_line_index] = f'GRUB_CMDLINE_LINUX="{new_cmdline}"'
        
        # Write back to file
        try:
            # Create backup
            backup_file = f"{grub_file}.backup.{int(time.time())}"
            subprocess.run(["cp", grub_file, backup_file], check=True)
            
            # Write new content
            with open(grub_file, "w") as f:
                f.write('\n'.join(lines))
            
            # Update GRUB
            result = subprocess.run(
                ["update-grub"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                # Restore backup on failure
                subprocess.run(["cp", backup_file, grub_file], check=True)
                return jsonify({
                    "success": False,
                    "message": f"Failed to update GRUB: {result.stderr}"
                }), 500
            
            # Reboot if requested
            if reboot and enable:
                # Schedule reboot in 10 seconds
                subprocess.Popen(["sh", "-c", "sleep 10 && reboot"], 
                              stdout=subprocess.DEVNULL, 
                              stderr=subprocess.DEVNULL)
                return jsonify({
                    "success": True,
                    "message": f"IOMMU configured successfully. Server will reboot in 10 seconds.",
                    "backup_file": backup_file
                }), 200
            else:
                return jsonify({
                    "success": True,
                    "message": f"IOMMU configuration updated. Reboot required to apply changes.",
                    "backup_file": backup_file
                }), 200
                
        except Exception as e:
            # Try to restore backup
            try:
                if 'backup_file' in locals():
                    subprocess.run(["cp", backup_file, grub_file], check=True)
            except Exception:
                pass
            
            return jsonify({
                "success": False,
                "message": f"Failed to write GRUB configuration: {str(e)}"
            }), 500
        
    except Exception as e:
        logging.error(f"[DPDK IOMMU CONFIG] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dpdk/recommend", methods=["GET"])
def dpdk_recommend_tx_cores():
    """
    Recommend a tx_cores value for the multi-queue tx_worker.

    Inputs (query string):
      iface       - network interface name (used to read /sys/class/net/<iface>/speed)
      frame_size  - bytes (default 64)
      pps         - target packets-per-second (0 or omitted = line rate)

    Output:
      {
        "ok": true,
        "iface": "...",
        "link_speed_mbps": 100000,
        "frame_size": 1500,
        "target_pps": 0,
        "estimated_pps_per_core": 4540000,
        "recommended_tx_cores": 2,
        "explanation": "...human-readable reasoning..."
      }

    Methodology (calibrated against Mellanox CX-7 measurements on this server):
      • Per-core ceiling at 64B  ≈ 4.5 Mpps  (CPU-bound for tiny pkts)
      • Per-core ceiling at 1500B ≈ 4.1 Mpps (mempool/PCIe-bound for big pkts)
      • Beyond 12 cores efficiency drops ~80%, so we cap at 16 and keep some
        headroom by recommending the next supported step (1/2/4/8/12/16).
      • If target_pps is 0 (line rate), we compute target as link_speed/frame.
    """
    try:
        iface = (request.args.get("iface") or "").strip()
        try:
            frame_size = int(request.args.get("frame_size") or 64)
            if frame_size < 60:
                frame_size = 60
        except Exception:
            frame_size = 64
        try:
            target_pps = int(request.args.get("pps") or 0)
        except Exception:
            target_pps = 0

        link_mbps = 0
        if iface:
            try:
                p = f"/sys/class/net/{iface}/speed"
                if os.path.exists(p):
                    v = int(open(p).read().strip())
                    if v > 0:
                        link_mbps = v
            except Exception:
                link_mbps = 0

        # Line-rate target if user didn't specify pps
        # L1 bytes per frame = frame_size + 20 (preamble + IFG)
        line_pps = 0
        if link_mbps > 0:
            line_pps = max(1, int((link_mbps * 1_000_000) // ((frame_size + 20) * 8)))
        if target_pps <= 0:
            target_pps = line_pps or 100_000_000  # fallback if no link info

        # Per-core ceiling estimate (calibrated, conservative).
        # Small frames are CPU-bound; large frames are mempool/PCIe-bound and
        # scale nearly the same per-core because frame builds dominate.
        if frame_size <= 128:
            per_core_pps = 4_500_000
        elif frame_size <= 512:
            per_core_pps = 4_300_000
        else:
            per_core_pps = 4_100_000

        # Need this many cores in theory
        need = max(1, (target_pps + per_core_pps - 1) // per_core_pps)

        # Round up to a supported step (1, 2, 4, 8, 12, 16)
        steps = [1, 2, 4, 8, 12, 16]
        recommended = steps[-1]
        for s in steps:
            if s >= need:
                recommended = s
                break

        # Format an explanation the UI can show as a tooltip
        if link_mbps > 0:
            link_str = f"{link_mbps / 1000:g} Gbps"
        else:
            link_str = "unknown"
        explanation = (
            f"Link {link_str}; line rate at {frame_size}B = "
            f"{line_pps:,} pps. Target = {target_pps:,} pps. "
            f"Per-core ceiling on this NIC ≈ {per_core_pps:,} pps. "
            f"Need ~{need} core(s) → recommended {recommended}."
        )

        return jsonify({
            "ok": True,
            "iface": iface,
            "link_speed_mbps": link_mbps,
            "frame_size": frame_size,
            "target_pps": target_pps,
            "line_rate_pps": line_pps,
            "estimated_pps_per_core": per_core_pps,
            "recommended_tx_cores": recommended,
            "explanation": explanation,
        })
    except Exception as e:
        logging.error("[DPDK recommend] %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


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
    
    # Orphan tx_worker sweep on startup. If the previous netgen-server
    # was SIGKILL'd (or this host rebooted into a fresh process while
    # tx_worker was still alive), the launcher's atexit/finally block
    # never ran and tx_worker keeps blasting the wire on its own. Reap
    # any leftovers before we accept new traffic — otherwise old streams
    # appear "stopped" in the DB but are still flooding the network.
    try:
        import subprocess as _sp
        r = _sp.run(["pgrep", "-af", "--", "/opt/netgen/resources/dpdk/tx_worker/build/tx_worker"],
                    capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            n = len(r.stdout.strip().splitlines())
            logging.warning(f"[STARTUP CLEANUP] Found {n} orphan tx_worker process(es) from a previous run — terminating")
            _sp.run(["pkill", "-TERM", "-f", "--", "/opt/netgen/resources/dpdk/tx_worker/build/tx_worker"],
                    capture_output=True, timeout=5)
            import time as _t
            _t.sleep(2.0)
            _sp.run(["pkill", "-KILL", "-f", "--", "/opt/netgen/resources/dpdk/tx_worker/build/tx_worker"],
                    capture_output=True, timeout=5)
            logging.info("[STARTUP CLEANUP] Orphan tx_worker sweep complete")
    except Exception as e:
        logging.warning(f"[STARTUP CLEANUP] Orphan tx_worker sweep failed: {e}")

    # Cleanup stale streams on startup (after reboot, no streams are actually running)
    try:
        running_streams = stream_db.get_all_streams(status="Running")
        if running_streams:
            logging.info(f"[STARTUP CLEANUP] Found {len(running_streams)} stream(s) marked as 'Running' in database - marking as 'Stopped' (server was rebooted)")
            for stream in running_streams:
                stream_id = stream.get("stream_id")
                if stream_id:
                    try:
                        stream_db.stop_stream(stream_id)
                        logging.info(f"[STARTUP CLEANUP] Marked stream {stream_id} ({stream.get('stream_name', 'Unknown')}) as Stopped")
                    except Exception as e:
                        logging.warning(f"[STARTUP CLEANUP] Failed to mark stream {stream_id} as Stopped: {e}")
            logging.info(f"[STARTUP CLEANUP] Cleanup completed - all stale streams marked as Stopped")
        else:
            logging.info("[STARTUP CLEANUP] No stale streams found in database")
        
        # Also clean up old stopped streams on startup (older than 24 hours)
        try:
            deleted = stream_db.cleanup_old_stopped_streams(hours=24)
            if deleted > 0:
                logging.info(f"[STARTUP CLEANUP] Cleaned up {deleted} old stopped stream(s) from database")
        except Exception as cleanup_error:
            logging.warning(f"[STARTUP CLEANUP] Failed to cleanup old stopped streams: {cleanup_error}")
    except Exception as e:
        logging.error(f"[STARTUP CLEANUP] Error cleaning up stale streams: {e}")
    
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

    # Start DHCP client monitoring
    try:
        dhcp_client_monitor.start()
        logging.info("[DHCP MONITOR] DHCP client monitoring started")
    except Exception as e:
        logging.error(f"[DHCP MONITOR] Failed to start DHCP monitoring: {e}")
    
    # Start stream statistics polling thread
    def _poll_stream_statistics():
        """Background thread that polls stream_tracker and updates database every 2 seconds."""
        import time
        while True:
            try:
                time.sleep(2)  # Poll every 2 seconds
                
                # Get active streams from stream_tracker
                active_streams = stream_tracker.get_stream_stats()
                
                # Update database with current TX/RX counts and rates
                for stream in active_streams:
                    stream_id = stream.get("stream_id")
                    if not stream_id:
                        continue
                    
                    tx_count = stream.get("tx_count", 0)
                    rx_count = stream.get("rx_count", 0)
                    
                    # Update counts - rates will be calculated by database based on time delta
                    try:
                        stream_db.update_stream_statistics(
                            stream_id=stream_id,
                            tx_count=tx_count,
                            rx_count=rx_count,
                            tx_rate=None,  # Let database calculate rate based on time delta
                            rx_rate=None
                        )
                    except Exception as db_error:
                        logging.debug(f"[STREAM POLL] Failed to update stream {stream_id}: {db_error}")
                
                # Check for streams in database that are no longer in stream_tracker
                # and mark them as "Stopped"
                try:
                    db_streams = stream_db.get_all_streams(status="Running")
                    db_stream_ids = {s.get("stream_id") for s in db_streams if s.get("stream_id")}
                    tracker_stream_ids = {s.get("stream_id") for s in active_streams if s.get("stream_id")}
                    
                    stopped_stream_ids = db_stream_ids - tracker_stream_ids
                    for stream_id in stopped_stream_ids:
                        try:
                            stream_db.stop_stream(stream_id)
                            logging.info(f"[STREAM POLL] Marked stream {stream_id} as Stopped (not in tracker)")
                        except Exception as stop_error:
                            logging.debug(f"[STREAM POLL] Failed to stop stream {stream_id}: {stop_error}")
                    
                    # Periodically clean up old stopped streams (every 10 polling cycles = ~20 seconds)
                    # This prevents database from accumulating too many old streams
                    if not hasattr(_poll_stream_statistics, "_cleanup_counter"):
                        _poll_stream_statistics._cleanup_counter = 0
                    _poll_stream_statistics._cleanup_counter += 1
                    if _poll_stream_statistics._cleanup_counter >= 10:  # Every ~20 seconds
                        _poll_stream_statistics._cleanup_counter = 0
                        try:
                            deleted = stream_db.cleanup_old_stopped_streams(hours=24)  # Keep stopped streams for 24 hours
                            if deleted > 0:
                                logging.info(f"[STREAM POLL] Cleaned up {deleted} old stopped stream(s)")
                        except Exception as cleanup_error:
                            logging.debug(f"[STREAM POLL] Error cleaning up old streams: {cleanup_error}")
                except Exception as check_error:
                    logging.debug(f"[STREAM POLL] Error checking stopped streams: {check_error}")
                    
            except Exception as e:
                logging.error(f"[STREAM POLL] Error in stream statistics polling: {e}")
                time.sleep(2)  # Wait before retrying
    
    # Start the polling thread
    try:
        poll_thread = threading.Thread(target=_poll_stream_statistics, daemon=True)
        poll_thread.start()
        logging.info("[STREAM POLL] Stream statistics polling thread started")
    except Exception as e:
        logging.error(f"[STREAM POLL] Failed to start stream statistics polling: {e}")
    
    # Global AI settings storage (can be set via API from client)
    # Load from persisted file if it exists
    ai_settings = {
        "openai_api_key": os.environ.get("OPENAI_API_KEY"),
        "openai_api_base": os.environ.get("OPENAI_API_BASE")
    }
    
    # Try to load persisted settings from file
    settings_file = "/opt/OSTG/.ostg_ai_server_settings.env"
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export OPENAI_API_KEY='):
                        # Extract value between single quotes, handling escaped quotes
                        # Format: export OPENAI_API_KEY='value'
                        match = re.match(r"export OPENAI_API_KEY='(.+)'$", line)
                        if match:
                            value = match.group(1).replace("'\"'\"'", "'")  # Unescape quotes
                            ai_settings["openai_api_key"] = value
                            os.environ["OPENAI_API_KEY"] = value
                    elif line.startswith('export OPENAI_API_BASE='):
                        match = re.match(r"export OPENAI_API_BASE='(.+)'$", line)
                        if match:
                            value = match.group(1).replace("'\"'\"'", "'")  # Unescape quotes
                            ai_settings["openai_api_base"] = value
                            os.environ["OPENAI_API_BASE"] = value
            if ai_settings.get("openai_api_key") or ai_settings.get("openai_api_base"):
                logging.info("[AI SETTINGS] Loaded persisted settings from file")
        except Exception as e:
            logging.warning(f"[AI SETTINGS] Could not load persisted settings: {e}")
    
    def get_ai_api_key():
        """Get OpenAI API key from settings or environment, reload from file if needed"""
        key = ai_settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
        
        # If not found, try reloading from file
        if not key:
            settings_file = "/opt/OSTG/.ostg_ai_server_settings.env"
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith('export OPENAI_API_KEY='):
                                match = re.match(r"export OPENAI_API_KEY='(.+)'$", line)
                                if match:
                                    key = match.group(1).replace("'\"'\"'", "'")
                                    ai_settings["openai_api_key"] = key
                                    os.environ["OPENAI_API_KEY"] = key
                                    logging.info("[AI SETTINGS] Reloaded API key from file")
                                    break
                except Exception as e:
                    logging.warning(f"[AI SETTINGS] Could not reload API key from file: {e}")
        
        # Debug logging (warn only once per process to avoid log spam)
        if key:
            logging.debug(f"[AI SETTINGS] API key found: length={len(key)}, source={'ai_settings' if ai_settings.get('openai_api_key') else 'environment'}")
        else:
            if not getattr(get_ai_api_key, '_warned', False):
                logging.warning("[AI SETTINGS] API key not found. Cloud AI features will be disabled. Set OPENAI_API_KEY or configure via client Settings. Local Ollama can still be used.")
                get_ai_api_key._warned = True
        
        return key
    
    def get_ai_api_base():
        """Get OpenAI API base URL from settings or environment, reload from file if needed"""
        base = ai_settings.get("openai_api_base") or os.environ.get("OPENAI_API_BASE")
        
        # If not found, try reloading from file
        if not base:
            settings_file = "/opt/OSTG/.ostg_ai_server_settings.env"
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith('export OPENAI_API_BASE='):
                                match = re.match(r"export OPENAI_API_BASE='(.+)'$", line)
                                if match:
                                    base = match.group(1).replace("'\"'\"'", "'")
                                    ai_settings["openai_api_base"] = base
                                    os.environ["OPENAI_API_BASE"] = base
                                    logging.info("[AI SETTINGS] Reloaded API base from file")
                                    break
                except Exception as e:
                    logging.warning(f"[AI SETTINGS] Could not reload API base from file: {e}")
        
        return base
    
    # AI Settings API Endpoints
    @app.route("/api/ai/settings", methods=["GET"])
    def get_ai_settings():
        """Get current AI settings (without exposing sensitive keys)"""
        try:
            return jsonify({
                "use_ai_api": bool(get_ai_api_key()),
                "has_api_key": bool(get_ai_api_key()),
                "has_api_base": bool(get_ai_api_base()),
                "api_base": get_ai_api_base() if get_ai_api_base() else None
            }), 200
        except Exception as e:
            logging.error(f"[AI SETTINGS] Failed to get settings: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/settings", methods=["POST"])
    def set_ai_settings():
        """Set AI settings from client and persist to file"""
        try:
            data = request.get_json()
            
            # Update settings
            if "openai_api_key" in data:
                ai_settings["openai_api_key"] = data.get("openai_api_key", "").strip()
                if ai_settings["openai_api_key"]:
                    # Also set environment variable for modules that check it directly
                    os.environ["OPENAI_API_KEY"] = ai_settings["openai_api_key"]
                    logging.info("[AI SETTINGS] API key updated from client")
                else:
                    # Clear from environment if empty
                    if "OPENAI_API_KEY" in os.environ:
                        del os.environ["OPENAI_API_KEY"]
                    logging.info("[AI SETTINGS] API key cleared")
            
            if "openai_api_base" in data:
                ai_settings["openai_api_base"] = data.get("openai_api_base", "").strip()
                if ai_settings["openai_api_base"]:
                    # Also set environment variable for modules that check it directly
                    os.environ["OPENAI_API_BASE"] = ai_settings["openai_api_base"]
                    logging.info(f"[AI SETTINGS] API base URL updated: {ai_settings['openai_api_base']}")
                else:
                    # Clear from environment if empty
                    if "OPENAI_API_BASE" in os.environ:
                        del os.environ["OPENAI_API_BASE"]
                    logging.info("[AI SETTINGS] API base URL cleared")
            
            # Persist settings to file for server restarts
            try:
                # Ensure directory exists
                settings_dir = "/opt/OSTG"
                if not os.path.exists(settings_dir):
                    os.makedirs(settings_dir, mode=0o755)
                
                # Save to .env file (can be sourced)
                settings_file = "/opt/OSTG/.ostg_ai_server_settings.env"
                with open(settings_file, 'w') as f:
                    if ai_settings.get("openai_api_key"):
                        # Escape single quotes in the value
                        api_key_escaped = ai_settings['openai_api_key'].replace("'", "'\"'\"'")
                        f.write(f"export OPENAI_API_KEY='{api_key_escaped}'\n")
                    if ai_settings.get("openai_api_base"):
                        api_base_escaped = ai_settings['openai_api_base'].replace("'", "'\"'\"'")
                        f.write(f"export OPENAI_API_BASE='{api_base_escaped}'\n")
                
                # Make file readable only by owner
                os.chmod(settings_file, 0o600)
                
                # Also create a shell script that can be sourced
                script_file = "/opt/OSTG/source_ai_settings.sh"
                with open(script_file, 'w') as f:
                    f.write("#!/bin/bash\n")
                    f.write("# OSTG AI Settings - Source this file to export AI environment variables\n")
                    f.write(f"# Generated automatically by OSTG server\n\n")
                    if ai_settings.get("openai_api_key"):
                        api_key_escaped = ai_settings['openai_api_key'].replace("'", "'\"'\"'")
                        f.write(f"export OPENAI_API_KEY='{api_key_escaped}'\n")
                    if ai_settings.get("openai_api_base"):
                        api_base_escaped = ai_settings['openai_api_base'].replace("'", "'\"'\"'")
                        f.write(f"export OPENAI_API_BASE='{api_base_escaped}'\n")
                
                os.chmod(script_file, 0o755)
                
                logging.info(f"[AI SETTINGS] Settings persisted to {settings_file} and {script_file}")
                logging.info("[AI SETTINGS] To use these settings, run: source /opt/OSTG/.ostg_ai_server_settings.env")
            except Exception as e:
                logging.warning(f"[AI SETTINGS] Could not persist settings to file: {e}")
            
            return jsonify({
                "status": "success",
                "message": "AI settings updated successfully and persisted to server",
                "has_api_key": bool(ai_settings.get("openai_api_key")),
                "has_api_base": bool(ai_settings.get("openai_api_base"))
            }), 200
        except Exception as e:
            logging.error(f"[AI SETTINGS] Failed to set settings: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Initialize AI Troubleshooting (lazy import to avoid errors if not installed)
    try:
        from utils.ai import NetworkTroubleshooter, ConfigKnowledgeBase
        ai_kb = ConfigKnowledgeBase(db_path="/opt/OSTG/ai_knowledge_base.db")
        ai_troubleshooter = NetworkTroubleshooter(
            knowledge_base=ai_kb,
            use_ai_api=bool(get_ai_api_key()),
            api_key=get_ai_api_key()
        )
        logging.info("[AI] Network troubleshooting AI initialized")
    except ImportError:
        logging.warning("[AI] AI modules not available. Install with: pip install scikit-learn pandas numpy")
        ai_troubleshooter = None
        ai_kb = None
    except Exception as e:
        logging.warning(f"[AI] Failed to initialize AI troubleshooting: {e}")
        ai_troubleshooter = None
        ai_kb = None
    
    # AI Troubleshooting API Endpoints
    @app.route("/api/ai/troubleshoot", methods=["POST"])
    def ai_troubleshoot():
        """AI-powered network troubleshooting"""
        if not ai_troubleshooter:
            return jsonify({"error": "AI troubleshooting not available. Install dependencies: pip install scikit-learn pandas numpy"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            symptoms = data.get("symptoms", {})
            current_config = data.get("current_config")
            
            if not device_id:
                return jsonify({"error": "device_id is required"}), 400
            
            diagnosis = ai_troubleshooter.diagnose(device_id, symptoms, current_config)
            return jsonify(diagnosis), 200
        except Exception as e:
            logging.error(f"[AI TROUBLESHOOT] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/add-config", methods=["POST"])
    def ai_add_config():
        """Add device configuration to AI knowledge base"""
        if not ai_kb:
            return jsonify({"error": "AI knowledge base not available"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            device_name = data.get("device_name", device_id)
            config_text = data.get("config_text")
            vendor = data.get("vendor")
            
            if not device_id or not config_text:
                return jsonify({"error": "device_id and config_text are required"}), 400
            
            ai_kb.add_config(device_id, device_name, config_text, vendor)
            return jsonify({"status": "success", "message": f"Configuration added for device {device_id}"}), 200
        except Exception as e:
            logging.error(f"[AI ADD CONFIG] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/train-case", methods=["POST"])
    def ai_train_case():
        """Train AI from a resolved troubleshooting case"""
        if not ai_troubleshooter:
            return jsonify({"error": "AI troubleshooting not available"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            symptoms = data.get("symptoms", {})
            solution = data.get("solution")
            
            if not device_id or not solution:
                return jsonify({"error": "device_id and solution are required"}), 400
            
            ai_troubleshooter.train_from_resolved_case(device_id, symptoms, solution)
            return jsonify({"status": "success", "message": "Case trained successfully"}), 200
        except Exception as e:
            logging.error(f"[AI TRAIN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/import-ostg-configs", methods=["POST"])
    def ai_import_ostg_configs():
        """Import all device configurations from OSTG database"""
        if not ai_kb:
            return jsonify({"error": "AI knowledge base not available"}), 503
        
        try:
            from utils.ai import import_device_configs_from_ostg
            import_device_configs_from_ostg(knowledge_base=ai_kb)
            return jsonify({"status": "success", "message": "Imported all OSTG device configurations"}), 200
        except Exception as e:
            logging.error(f"[AI IMPORT] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/suggest-config-fix", methods=["POST"])
    def ai_suggest_config_fix():
        """Suggest configuration fixes for an issue"""
        if not ai_troubleshooter:
            return jsonify({"error": "AI troubleshooting not available"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            issue = data.get("issue")
            current_config = data.get("current_config")
            
            if not device_id or not issue:
                return jsonify({"error": "device_id and issue are required"}), 400
            
            # Get config from knowledge base if not provided
            if not current_config:
                current_config = ai_kb.get_device_config(device_id) if ai_kb else None
            
            if not current_config:
                return jsonify({"error": "Device configuration not found"}), 404
            
            suggestions = ai_troubleshooter.suggest_config_fix(device_id, issue, current_config)
            return jsonify(suggestions), 200
        except Exception as e:
            logging.error(f"[AI CONFIG FIX] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # External Device Management API Endpoints
    @app.route("/api/device/add-external", methods=["POST"])
    def add_external_device():
        """Add external network device"""
        try:
            from utils.device_database import DeviceDatabase
            from utils.external_device_manager import ExternalDeviceManager
            
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid JSON payload"}), 400
            
            device_db = DeviceDatabase()
            ext_manager = ExternalDeviceManager()
            
            # Generate device ID if not provided
            device_id = data.get("device_id") or str(uuid.uuid4())
            device_name = data.get("device_name")
            device_type = data.get("device_type", "other")
            connection_method = data.get("connection_method", "ssh")
            connection_host = data.get("connection_host")
            connection_port = data.get("connection_port", 22)
            connection_username = data.get("connection_username")
            connection_info = data.get("connection_info", {})
            
            if not device_name or not connection_host:
                return jsonify({"error": "device_name and connection_host are required"}), 400
            
            # Build device data
            device_data = {
                "device_id": device_id,
                "device_name": device_name,
                "device_type": device_type,  # juniper, cisco, etc. (not frr_container)
                "interface": data.get("interface", ""),  # Optional for external devices
                "connection_method": connection_method,
                "connection_host": connection_host,
                "connection_port": connection_port,
                "connection_username": connection_username,
                "connection_info": json.dumps(connection_info) if isinstance(connection_info, dict) else connection_info,
                "ipv4_address": data.get("ipv4_address"),
                "ipv6_address": data.get("ipv6_address"),
                "status": "Stopped"
            }
            
            # Add to database
            success = device_db.add_device(device_data)
            if not success:
                return jsonify({"error": "Failed to add device to database"}), 500
            
            # Register with external device manager
            ext_manager.add_device(device_id, device_type, connection_info)
            
            # Add to AI knowledge base if config available
            if ai_kb and connection_info:
                try:
                    # Try to get device config
                    config_result = ext_manager.get_configuration(device_id)
                    if config_result:
                        ai_kb.add_config(device_id, device_name, config_result, vendor=device_type)
                except Exception as e:
                    logging.warning(f"Failed to add external device config to knowledge base: {e}")
            
            return jsonify({
                "status": "success",
                "device_id": device_id,
                "message": f"External device {device_name} added successfully"
            }), 200
        
        except Exception as e:
            logging.error(f"[ADD EXTERNAL DEVICE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/device/external/status/<device_id>", methods=["GET"])
    def get_external_device_status(device_id):
        """Get status of external device"""
        try:
            from utils.external_device_manager import ExternalDeviceManager
            
            ext_manager = ExternalDeviceManager()
            status = ext_manager.get_device_status(device_id)
            
            return jsonify({
                "status": "success",
                "device_id": device_id,
                "device_status": status
            }), 200
        except Exception as e:
            logging.error(f"[EXTERNAL DEVICE STATUS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/device/external/execute", methods=["POST"])
    def execute_external_device_command():
        """Execute command on external device"""
        try:
            from utils.external_device_manager import ExternalDeviceManager
            
            data = request.get_json()
            device_id = data.get("device_id")
            command = data.get("command")
            connection_method = data.get("connection_method")
            
            if not device_id or not command:
                return jsonify({"error": "device_id and command are required"}), 400
            
            ext_manager = ExternalDeviceManager()
            result = ext_manager.execute_command(device_id, command, connection_method)
            
            return jsonify({
                "status": "success",
                "device_id": device_id,
                "result": result
            }), 200
        except Exception as e:
            logging.error(f"[EXTERNAL DEVICE EXECUTE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/device/external/config/<device_id>", methods=["GET"])
    def get_external_device_config(device_id):
        """Get configuration from external device"""
        try:
            from utils.external_device_manager import ExternalDeviceManager
            
            ext_manager = ExternalDeviceManager()
            config = ext_manager.get_configuration(device_id)
            
            return jsonify({
                "status": "success",
                "device_id": device_id,
                "config": config
            }), 200
        except Exception as e:
            logging.error(f"[EXTERNAL DEVICE CONFIG] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # AI Device Discovery
    @app.route("/api/ai/device/discover", methods=["POST"])
    def ai_discover_devices():
        """AI-powered device discovery on network"""
        try:
            data = request.get_json()
            subnet = data.get("subnet", "192.168.1.0/24")
            
            # Simple network scan (can be enhanced with nmap, etc.)
            import subprocess
            import ipaddress
            
            discovered_devices = []
            
            # Parse subnet
            try:
                network = ipaddress.ip_network(subnet, strict=False)
                # Limit to /24 for safety
                if network.prefixlen < 24:
                    return jsonify({
                        "error": "Subnet too large. Please use /24 or smaller."
                    }), 400
                
                # Scan first 10 hosts (for demo - use nmap for production)
                hosts = list(network.hosts())[:10]
                
                for host in hosts:
                    # Ping test
                    result = subprocess.run(
                        ["ping", "-c", "1", "-W", "1", str(host)],
                        capture_output=True,
                        timeout=2
                    )
                    
                    if result.returncode == 0:
                        # Device is reachable
                        device_info = {
                            "ip": str(host),
                            "reachable": True,
                            "vendor": "Unknown",
                            "type": "Unknown",
                            "suggested_method": "ssh"
                        }
                        
                        # Try to detect device type (simplified)
                        # In production, use nmap, SNMP, banner grabbing, etc.
                        try:
                            # Check SSH port
                            ssh_result = subprocess.run(
                                ["nc", "-z", "-w", "1", str(host), "22"],
                                capture_output=True,
                                timeout=2
                            )
                            if ssh_result.returncode == 0:
                                device_info["suggested_method"] = "ssh"
                                device_info["ports"] = ["22"]
                        except Exception:
                            pass
                        
                        discovered_devices.append(device_info)
                
                return jsonify({
                    "status": "success",
                    "subnet": subnet,
                    "devices": discovered_devices,
                    "count": len(discovered_devices)
                }), 200
            
            except ValueError:
                return jsonify({"error": "Invalid subnet format"}), 400
        
        except Exception as e:
            logging.error(f"[AI DEVICE DISCOVERY] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Initialize Test Framework
    try:
        from utils.ai import NetworkTestFramework, TestReportStorage, UserTestCaseManager
        test_framework = NetworkTestFramework(
            knowledge_base=ai_kb,
            use_ai_api=bool(get_ai_api_key()),
            api_key=get_ai_api_key()
        )
        test_report_storage = TestReportStorage()
        test_case_manager = UserTestCaseManager()
        
        # Load user-defined test cases
        user_test_cases = test_case_manager.get_all_test_cases()
        for tc in user_test_cases:
            test_framework.add_test_case(tc)
        
        logging.info(f"[AI TEST] Test framework initialized with {len(user_test_cases)} user-defined test cases")
    except ImportError:
        logging.warning("[AI TEST] Test framework not available")
        test_framework = None
        test_report_storage = None
        test_case_manager = None
    except Exception as e:
        logging.warning(f"[AI TEST] Failed to initialize test framework: {e}")
        test_framework = None
        test_report_storage = None
        test_case_manager = None
    
    # Test Framework API Endpoints
    @app.route("/api/ai/test/suggest", methods=["POST"])
    def ai_suggest_tests():
        """Suggest test cases for a device"""
        if not test_framework:
            return jsonify({"error": "Test framework not available"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            use_ai = data.get("use_ai", True)
            
            if not device_id:
                return jsonify({"error": "device_id is required"}), 400
            
            suggestions = test_framework.suggest_test_cases(device_id, use_ai=use_ai)
            
            return jsonify({
                "suggestions": [{
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "description": tc.description,
                    "category": tc.category,
                    "severity": tc.severity
                } for tc in suggestions]
            }), 200
        except Exception as e:
            logging.error(f"[AI TEST SUGGEST] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/run", methods=["POST"])
    def ai_run_tests():
        """Run a test suite"""
        if not test_framework:
            return jsonify({"error": "Test framework not available"}), 503
        
        try:
            data = request.get_json()
            device_id = data.get("device_id")
            device_name = data.get("device_name", "")
            test_ids = data.get("test_ids", [])
            suite_name = data.get("suite_name", "Test Suite")
            
            if not device_id or not test_ids:
                return jsonify({"error": "device_id and test_ids are required"}), 400
            
            report = test_framework.run_test_suite(test_ids, device_id, device_name, suite_name)
            
            # Save report
            if test_report_storage:
                test_report_storage.save_report(report)
            
            # Convert report to dict for JSON response
            from dataclasses import asdict
            report_dict = asdict(report)
            
            return jsonify(report_dict), 200
        except Exception as e:
            logging.error(f"[AI TEST RUN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/report/<report_id>", methods=["GET"])
    def ai_get_report(report_id):
        """Get test report by ID"""
        if not test_report_storage:
            return jsonify({"error": "Test report storage not available"}), 503
        
        try:
            format_type = request.args.get("format", "json")
            report = test_report_storage.get_report(report_id)
            
            if not report:
                return jsonify({"error": "Report not found"}), 404
            
            if format_type == "html":
                report_obj = test_framework._generate_html_report(report) if test_framework else None
                if report_obj:
                    return report_obj, 200, {"Content-Type": "text/html"}
            
            return jsonify(report), 200
        except Exception as e:
            logging.error(f"[AI TEST REPORT] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/reports/<device_id>", methods=["GET"])
    def ai_get_device_reports(device_id):
        """Get test reports for a device"""
        if not test_report_storage:
            return jsonify({"error": "Test report storage not available"}), 503
        
        try:
            limit = int(request.args.get("limit", 10))
            reports = test_report_storage.get_reports_for_device(device_id, limit)
            return jsonify({"reports": reports}), 200
        except Exception as e:
            logging.error(f"[AI TEST REPORTS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/case/create", methods=["POST"])
    def ai_create_test_case():
        """Create a user-defined test case"""
        if not test_case_manager:
            return jsonify({"error": "Test case manager not available"}), 503
        
        try:
            data = request.get_json()
            from utils.ai import TestCase
            
            test_case = TestCase(
                test_id=data.get("test_id"),
                name=data.get("name"),
                description=data.get("description", ""),
                category=data.get("category", "custom"),
                test_function=data.get("test_function"),
                parameters=data.get("parameters", {}),
                expected_result=data.get("expected_result"),
                severity=data.get("severity", "medium"),
                vendor_specific=data.get("vendor_specific"),
                prerequisites=data.get("prerequisites", [])
            )
            
            if test_case_manager.create_test_case(test_case):
                # Add to framework
                if test_framework:
                    test_framework.add_test_case(test_case)
                
                return jsonify({"status": "success", "test_id": test_case.test_id}), 200
            else:
                return jsonify({"error": "Failed to create test case"}), 500
        except Exception as e:
            logging.error(f"[AI TEST CASE CREATE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/case/list", methods=["GET"])
    def ai_list_test_cases():
        """List all test cases (built-in and user-defined)"""
        if not test_framework:
            return jsonify({"error": "Test framework not available"}), 503
        
        try:
            test_cases = []
            for test_id, test_case in test_framework.test_cases.items():
                test_cases.append({
                    "test_id": test_case.test_id,
                    "name": test_case.name,
                    "description": test_case.description,
                    "category": test_case.category,
                    "severity": test_case.severity,
                    "vendor_specific": test_case.vendor_specific
                })
            
            return jsonify({"test_cases": test_cases}), 200
        except Exception as e:
            logging.error(f"[AI TEST CASE LIST] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/case/<test_id>", methods=["GET", "PUT", "DELETE"])
    def ai_manage_test_case(test_id):
        """Get, update, or delete a test case"""
        if not test_case_manager:
            return jsonify({"error": "Test case manager not available"}), 503
        
        try:
            if request.method == "GET":
                test_case = test_case_manager.get_test_case(test_id)
                if not test_case:
                    return jsonify({"error": "Test case not found"}), 404
                
                from dataclasses import asdict
                return jsonify(asdict(test_case)), 200
            
            elif request.method == "PUT":
                data = request.get_json()
                from utils.ai import TestCase
                
                test_case = TestCase(
                    test_id=test_id,
                    name=data.get("name"),
                    description=data.get("description", ""),
                    category=data.get("category", "custom"),
                    test_function=data.get("test_function"),
                    parameters=data.get("parameters", {}),
                    expected_result=data.get("expected_result"),
                    severity=data.get("severity", "medium"),
                    vendor_specific=data.get("vendor_specific"),
                    prerequisites=data.get("prerequisites", [])
                )
                
                if test_case_manager.update_test_case(test_id, test_case):
                    if test_framework:
                        test_framework.add_test_case(test_case)
                    return jsonify({"status": "success"}), 200
                else:
                    return jsonify({"error": "Failed to update test case"}), 500
            
            elif request.method == "DELETE":
                if test_case_manager.delete_test_case(test_id):
                    return jsonify({"status": "success"}), 200
                else:
                    return jsonify({"error": "Failed to delete test case"}), 500
        
        except Exception as e:
            logging.error(f"[AI TEST CASE MANAGE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Initialize Pytest Generator and Runner
    try:
        from utils.ai import PytestGenerator, PytestRunner, CodeGenerator
        pytest_generator = PytestGenerator(
            use_ai_api=bool(get_ai_api_key()),
            api_key=get_ai_api_key()
        )
        pytest_runner = PytestRunner()
        code_generator = CodeGenerator(
            use_ai_api=bool(get_ai_api_key()),
            api_key=get_ai_api_key()
        )
        logging.info("[AI PYTEST] Pytest generator and runner initialized")
    except ImportError:
        logging.warning("[AI PYTEST] Pytest modules not available")
        pytest_generator = None
        pytest_runner = None
        code_generator = None
    except Exception as e:
        logging.warning(f"[AI PYTEST] Failed to initialize pytest modules: {e}")
        pytest_generator = None
        pytest_runner = None
        code_generator = None
    
    # Pytest and Code Generation API Endpoints
    @app.route("/api/ai/pytest/generate", methods=["POST"])
    def ai_generate_pytest():
        """Generate pytest script"""
        if not pytest_generator:
            return jsonify({"error": "Pytest generator not available"}), 503
        
        try:
            data = request.get_json()
            test_requirements = data.get("test_requirements", {})
            device_config = data.get("device_config")
            save_file = data.get("save_file", False)
            file_path = data.get("file_path")
            
            script = pytest_generator.generate_pytest_script(test_requirements, device_config)
            
            # Save if requested
            if save_file:
                if not file_path:
                    file_path = f"/opt/OSTG/pytest_scripts/test_{int(time.time())}.py"
                pytest_generator.save_pytest_script(script, file_path)
            
            return jsonify({
                "script": script,
                "file_path": file_path if save_file else None
            }), 200
        except Exception as e:
            logging.error(f"[AI PYTEST GENERATE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/pytest/run", methods=["POST"])
    def ai_run_pytest():
        """Run pytest script"""
        if not pytest_runner:
            return jsonify({"error": "Pytest runner not available"}), 503
        
        try:
            data = request.get_json()
            script_content = data.get("script_content")
            script_name = data.get("script_name")
            script_path = data.get("script_path")
            additional_args = data.get("additional_args", [])
            
            if script_path:
                # Run from file
                result = pytest_runner.run_pytest_file(script_path, additional_args)
            elif script_content:
                # Run from content
                result = pytest_runner.run_pytest_script(script_content, script_name, additional_args)
            else:
                return jsonify({"error": "script_content or script_path is required"}), 400
            
            return jsonify(result), 200
        except Exception as e:
            logging.error(f"[AI PYTEST RUN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/pytest/scripts", methods=["GET"])
    def ai_list_pytest_scripts():
        """List pytest scripts"""
        if not pytest_runner:
            return jsonify({"error": "Pytest runner not available"}), 503
        
        try:
            scripts = pytest_runner.list_scripts()
            return jsonify({"scripts": scripts}), 200
        except Exception as e:
            logging.error(f"[AI PYTEST LIST] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/pytest/script/<script_name>", methods=["GET", "DELETE"])
    def ai_manage_pytest_script(script_name):
        """Get or delete pytest script"""
        if not pytest_runner:
            return jsonify({"error": "Pytest runner not available"}), 503
        
        try:
            if request.method == "GET":
                content = pytest_runner.get_script_content(script_name)
                if content is None:
                    return jsonify({"error": "Script not found"}), 404
                return jsonify({"script": content, "name": script_name}), 200
            
            elif request.method == "DELETE":
                if pytest_runner.delete_script(script_name):
                    return jsonify({"status": "success"}), 200
                else:
                    return jsonify({"error": "Script not found"}), 404
        except Exception as e:
            logging.error(f"[AI PYTEST MANAGE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Code Generation API Endpoints (Cursor.ai-like)
    @app.route("/api/ai/code/generate", methods=["POST"])
    def ai_generate_code():
        """Generate code from prompt using LLM"""
        try:
            from utils.ai.local_ai_engine import LocalAIEngine
            from utils.ai.advanced_code_generator import AdvancedCodeGenerator
            import os
            
            data = request.get_json()
            prompt = data.get("prompt")
            language = data.get("language", "python")
            code_type = data.get("code_type", "").lower()  # Function, Class, Script, Configuration, Template
            context = data.get("context")
            requirements = data.get("requirements")
            
            if not prompt:
                return jsonify({"error": "prompt is required"}), 400
            
            # Use AdvancedCodeGenerator with LLM support (better timeout handling)
            advanced_generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            # Build enhanced prompt for code generation with code_type guidance
            code_type_instructions = {
                "function": "Generate a standalone function (not a class method). Include function definition with parameters, docstring, and return statement.",
                "class": "Generate a complete class with __init__ method, class methods, and proper structure. Include docstrings and type hints where appropriate.",
                "script": "Generate an executable script with if __name__ == '__main__' block. Include command-line argument handling if needed.",
                "configuration": "Generate configuration code (e.g., YAML/JSON structure, config parser, settings management).",
                "template": "Generate a template or boilerplate code structure that can be customized."
            }
            
            enhanced_prompt = f"Generate {language} code: {prompt}"
            
            # Add code type specific instructions
            if code_type and code_type in code_type_instructions:
                enhanced_prompt += f"\n\nCode Structure: {code_type_instructions[code_type]}"
                logging.info(f"[AI CODE GENERATE] Using code_type: {code_type}")
            
            if context:
                enhanced_prompt += f"\n\nContext: {json.dumps(context, indent=2)}"
            if requirements:
                enhanced_prompt += f"\n\nRequirements:\n" + "\n".join(f"- {r}" for r in requirements)
            enhanced_prompt += "\n\nMake it complete, production-ready with proper imports, error handling, and documentation."
            
            # Try LLM first using LocalAIEngine (has better timeout handling)
            try:
                engine = LocalAIEngine(model_dir="/opt/OSTG/ai_models")
                llm_response = engine._try_llm_response(enhanced_prompt, context)
                if llm_response and len(llm_response.strip()) > 50 and "TODO: Implement" not in llm_response:
                    logging.info(f"[AI CODE GENERATE] LLM generated code: {len(llm_response)} chars (type: {code_type})")
                    return jsonify({"code": llm_response}), 200
            except Exception as e:
                logging.debug(f"[AI CODE GENERATE] LLM failed, using generator: {e}")
            
            # Fallback to AdvancedCodeGenerator (also tries LLM internally)
            # Pass code_type as part of context if available
            enhanced_context = context or {}
            if code_type:
                enhanced_context["code_type"] = code_type
            code = advanced_generator.generate_code(language, prompt, enhanced_context, requirements)
            
            # If still template, try one more time with LocalAIEngine chat
            if code and ("TODO: Implement" in code or len(code.strip()) < 100):
                try:
                    engine = LocalAIEngine(model_dir="/opt/OSTG/ai_models")
                    chat_prompt = f"Generate {language} code for: {prompt}"
                    if code_type and code_type in code_type_instructions:
                        chat_prompt += f"\n\n{code_type_instructions[code_type]}"
                    chat_response = engine.chat(chat_prompt, enhanced_context)
                    if chat_response and len(chat_response.strip()) > 50 and "TODO: Implement" not in chat_response:
                        logging.info(f"[AI CODE GENERATE] Chat generated code: {len(chat_response)} chars (type: {code_type})")
                        return jsonify({"code": chat_response}), 200
                except Exception as e:
                    logging.debug(f"[AI CODE GENERATE] Chat fallback failed: {e}")
            
            return jsonify({"code": code}), 200
        except Exception as e:
            logging.error(f"[AI CODE GENERATE] Error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/refactor", methods=["POST"])
    def ai_refactor_code():
        """Refactor code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            refactoring_request = data.get("refactoring_request", "Improve code quality")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            refactored = code_generator.refactor_code(code, refactoring_request)
            return jsonify({"code": refactored}), 200
        except Exception as e:
            logging.error(f"[AI CODE REFACTOR] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/fix", methods=["POST"])
    def ai_fix_code():
        """Fix code errors"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            error_message = data.get("error_message")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            fixed = code_generator.fix_code(code, error_message)
            return jsonify({"code": fixed}), 200
        except Exception as e:
            logging.error(f"[AI CODE FIX] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/explain", methods=["POST"])
    def ai_explain_code():
        """Explain code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            explanation = code_generator.explain_code(code)
            return jsonify({"explanation": explanation}), 200
        except Exception as e:
            logging.error(f"[AI CODE EXPLAIN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/optimize", methods=["POST"])
    def ai_optimize_code():
        """Optimize code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            optimized = code_generator.optimize_code(code)
            return jsonify({"code": optimized}), 200
        except Exception as e:
            logging.error(f"[AI CODE OPTIMIZE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/test", methods=["POST"])
    def ai_generate_test():
        """Generate test script for code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            test_type = data.get("test_type", "pytest")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            test_script = code_generator.generate_test_script(code, test_type)
            return jsonify({"test_script": test_script}), 200
        except Exception as e:
            logging.error(f"[AI CODE TEST] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/documentation", methods=["POST"])
    def ai_code_documentation():
        """Generate documentation for code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            doc_format = data.get("format", "markdown")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            documentation = code_generator.generate_documentation(code, doc_format)
            return jsonify({"documentation": documentation}), 200
        except Exception as e:
            logging.error(f"[AI CODE DOC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Advanced Code Generator Endpoints
    @app.route("/api/ai/code/generate-advanced", methods=["POST"])
    def ai_generate_advanced_code():
        """Generate code in multiple languages with advanced features"""
        try:
            from utils.ai.advanced_code_generator import AdvancedCodeGenerator
            import os
            
            advanced_generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            language = data.get("language", "python")
            prompt = data.get("prompt")
            context = data.get("context")
            requirements = data.get("requirements")
            
            if not prompt:
                return jsonify({"error": "prompt is required"}), 400
            
            code = advanced_generator.generate_code(language, prompt, context, requirements)
            return jsonify({"code": code, "language": language}), 200
        except Exception as e:
            logging.error(f"[AI ADVANCED CODE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/generate-network-script", methods=["POST"])
    def ai_generate_network_script():
        """Generate network automation script"""
        try:
            from utils.ai.advanced_code_generator import AdvancedCodeGenerator
            import os
            
            advanced_generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            requirements = data.get("requirements", {})
            
            if not requirements.get("description"):
                return jsonify({"error": "requirements.description is required"}), 400
            
            script = advanced_generator.generate_network_script(requirements)
            return jsonify({"script": script, "library": requirements.get("library", "netmiko")}), 200
        except Exception as e:
            logging.error(f"[AI NETWORK SCRIPT] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/generate-config", methods=["POST"])
    def ai_generate_config():
        """Generate device configuration template"""
        try:
            from utils.ai.advanced_code_generator import AdvancedCodeGenerator
            import os
            
            advanced_generator = AdvancedCodeGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            vendor = data.get("vendor", "juniper")
            requirements = data.get("requirements", {})
            
            if vendor not in ["juniper", "cisco", "arista", "nokia"]:
                return jsonify({"error": f"Unsupported vendor: {vendor}"}), 400
            
            config = advanced_generator.generate_config_template(vendor, requirements)
            return jsonify({"config": config, "vendor": vendor}), 200
        except Exception as e:
            logging.error(f"[AI CONFIG GENERATE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Code Analyzer Endpoints
    @app.route("/api/ai/code/analyze", methods=["POST"])
    def ai_analyze_code():
        """Analyze code quality and security"""
        try:
            from utils.ai.code_analyzer import CodeAnalyzer
            import os
            
            analyzer = CodeAnalyzer(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            language = data.get("language", "python")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            analysis = analyzer.analyze(code, language)
            return jsonify(analysis), 200
        except Exception as e:
            logging.error(f"[AI CODE ANALYZE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/security-scan", methods=["POST"])
    def ai_security_scan():
        """Scan code for security vulnerabilities"""
        try:
            from utils.ai.code_analyzer import CodeAnalyzer
            import os
            
            analyzer = CodeAnalyzer(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            language = data.get("language", "python")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            vulnerabilities = analyzer.detect_vulnerabilities(code, language)
            return jsonify({"vulnerabilities": vulnerabilities}), 200
        except Exception as e:
            logging.error(f"[AI SECURITY SCAN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/code/optimize-suggestions", methods=["POST"])
    def ai_optimize_suggestions():
        """Get performance optimization suggestions"""
        try:
            from utils.ai.code_analyzer import CodeAnalyzer
            import os
            
            analyzer = CodeAnalyzer(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            language = data.get("language", "python")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            suggestions = analyzer.suggest_optimizations(code, language)
            return jsonify({"suggestions": suggestions}), 200
        except Exception as e:
            logging.error(f"[AI OPTIMIZE SUGGESTIONS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Unified Troubleshooter Endpoints
    @app.route("/api/ai/troubleshoot/unified", methods=["POST"])
    def ai_troubleshoot_unified():
        """Unified troubleshooting for all domains"""
        try:
            from utils.ai.unified_troubleshooter import UnifiedTroubleshooter
            import os
            
            troubleshooter = UnifiedTroubleshooter(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            domain = data.get("domain", "network")
            issue = data.get("issue", {})
            
            if domain not in ["network", "code", "system", "integration"]:
                return jsonify({"error": f"Unsupported domain: {domain}"}), 400
            
            diagnosis = troubleshooter.troubleshoot(domain, issue)
            return jsonify(diagnosis), 200
        except Exception as e:
            logging.error(f"[AI TROUBLESHOOT UNIFIED] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/troubleshoot/code", methods=["POST"])
    def ai_troubleshoot_code():
        """Troubleshoot code issues"""
        try:
            from utils.ai.unified_troubleshooter import UnifiedTroubleshooter
            import os
            
            troubleshooter = UnifiedTroubleshooter(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            issue = data.get("issue", {})
            
            diagnosis = troubleshooter.troubleshoot("code", issue)
            return jsonify(diagnosis), 200
        except Exception as e:
            logging.error(f"[AI TROUBLESHOOT CODE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/troubleshoot/system", methods=["POST"])
    def ai_troubleshoot_system():
        """Troubleshoot system issues"""
        try:
            from utils.ai.unified_troubleshooter import UnifiedTroubleshooter
            import os
            
            troubleshooter = UnifiedTroubleshooter(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            issue = data.get("issue", {})
            
            diagnosis = troubleshooter.troubleshoot("system", issue)
            return jsonify(diagnosis), 200
        except Exception as e:
            logging.error(f"[AI TROUBLESHOOT SYSTEM] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/troubleshoot/integration", methods=["POST"])
    def ai_troubleshoot_integration():
        """Troubleshoot integration issues"""
        try:
            from utils.ai.unified_troubleshooter import UnifiedTroubleshooter
            import os
            
            troubleshooter = UnifiedTroubleshooter(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            issue = data.get("issue", {})
            
            diagnosis = troubleshooter.troubleshoot("integration", issue)
            return jsonify(diagnosis), 200
        except Exception as e:
            logging.error(f"[AI TROUBLESHOOT INTEGRATION] Error: {e}")
            return jsonify({"error": str(e)}), 500
    def ai_generate_documentation():
        """Generate documentation for code"""
        if not code_generator:
            return jsonify({"error": "Code generator not available"}), 503
        
        try:
            data = request.get_json()
            code = data.get("code")
            doc_format = data.get("format", "markdown")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            documentation = code_generator.generate_documentation(code, doc_format)
            return jsonify({"documentation": documentation}), 200
        except Exception as e:
            logging.error(f"[AI CODE DOC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Comprehensive Test Framework Endpoints
    @app.route("/api/ai/test/generate-unit", methods=["POST"])
    def ai_generate_unit_tests():
        """Generate unit tests from code"""
        try:
            from utils.ai.comprehensive_test_framework import ComprehensiveTestFramework
            import os
            
            framework = ComprehensiveTestFramework(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            test_framework = data.get("framework", "pytest")
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            tests = framework.generate_unit_tests(code, test_framework)
            return jsonify({"tests": tests, "count": len(tests)}), 200
        except Exception as e:
            logging.error(f"[AI UNIT TESTS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/generate-integration", methods=["POST"])
    def ai_generate_integration_tests():
        """Generate integration tests"""
        try:
            from utils.ai.comprehensive_test_framework import ComprehensiveTestFramework
            import os
            
            framework = ComprehensiveTestFramework(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            components = data.get("components", [])
            
            if not components:
                return jsonify({"error": "components is required"}), 400
            
            tests = framework.generate_integration_tests(components)
            return jsonify({"tests": tests, "count": len(tests)}), 200
        except Exception as e:
            logging.error(f"[AI INTEGRATION TESTS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/generate-suite", methods=["POST"])
    def ai_generate_test_suite():
        """Generate complete test suite"""
        try:
            from utils.ai.comprehensive_test_framework import ComprehensiveTestFramework
            import os
            
            framework = ComprehensiveTestFramework(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            test_type = data.get("test_type", "unit")
            options = data.get("options", {})
            
            if not code and test_type == "unit":
                return jsonify({"error": "code is required for unit tests"}), 400
            
            suite = framework.generate_test_suite(code, test_type, options)
            return jsonify({"suite": suite, "type": test_type}), 200
        except Exception as e:
            logging.error(f"[AI TEST SUITE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/coverage", methods=["POST"])
    def ai_analyze_test_coverage():
        """Analyze test coverage"""
        try:
            from utils.ai.comprehensive_test_framework import ComprehensiveTestFramework
            import os
            
            framework = ComprehensiveTestFramework(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            code = data.get("code")
            tests = data.get("tests", [])
            
            if not code:
                return jsonify({"error": "code is required"}), 400
            
            coverage = framework.analyze_test_coverage(code, tests)
            return jsonify(coverage), 200
        except Exception as e:
            logging.error(f"[AI TEST COVERAGE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Intelligent Device Manager Endpoints
    @app.route("/api/ai/device/provision", methods=["POST"])
    def ai_provision_device():
        """Automated device provisioning"""
        try:
            from utils.ai.intelligent_device_manager import IntelligentDeviceManager
            import os
            
            manager = IntelligentDeviceManager(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            device_spec = data.get("device_spec", {})
            
            if not device_spec:
                return jsonify({"error": "device_spec is required"}), 400
            
            result = manager.provision_device(device_spec)
            return jsonify(result), 200
        except Exception as e:
            logging.error(f"[AI DEVICE PROVISION] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/device/manage-config", methods=["POST"])
    def ai_manage_device_config():
        """Intelligent configuration management"""
        try:
            from utils.ai.intelligent_device_manager import IntelligentDeviceManager
            import os
            
            manager = IntelligentDeviceManager(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            device_id = data.get("device_id")
            config_changes = data.get("config_changes", {})
            
            if not device_id:
                return jsonify({"error": "device_id is required"}), 400
            
            result = manager.manage_configuration(device_id, config_changes)
            return jsonify(result), 200
        except Exception as e:
            logging.error(f"[AI DEVICE CONFIG] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/device/health/<device_id>", methods=["GET"])
    def ai_device_health(device_id):
        """Get device health status"""
        try:
            from utils.ai.intelligent_device_manager import IntelligentDeviceManager
            import os
            
            manager = IntelligentDeviceManager(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            health = manager.monitor_health(device_id)
            return jsonify(health), 200
        except Exception as e:
            logging.error(f"[AI DEVICE HEALTH] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/device/auto-remediate", methods=["POST"])
    def ai_auto_remediate_device():
        """Automated issue remediation"""
        try:
            from utils.ai.intelligent_device_manager import IntelligentDeviceManager
            import os
            
            manager = IntelligentDeviceManager(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            device_id = data.get("device_id")
            issue = data.get("issue", {})
            
            if not device_id:
                return jsonify({"error": "device_id is required"}), 400
            
            result = manager.auto_remediate(device_id, issue)
            return jsonify(result), 200
        except Exception as e:
            logging.error(f"[AI AUTO REMEDIATE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Proactive AI Assistant Endpoints
    @app.route("/api/ai/assistant/suggest", methods=["POST"])
    def ai_assistant_suggest():
        """Get proactive suggestions"""
        try:
            from utils.ai.proactive_assistant import ProactiveAIAssistant
            import os
            
            assistant = ProactiveAIAssistant(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            context = data.get("context", {})
            
            suggestions = assistant.suggest_actions(context)
            return jsonify({"suggestions": suggestions}), 200
        except Exception as e:
            logging.error(f"[AI ASSISTANT SUGGEST] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/assistant/learn", methods=["POST"])
    def ai_assistant_learn():
        """Learn from user actions"""
        try:
            from utils.ai.proactive_assistant import ProactiveAIAssistant
            import os
            
            assistant = ProactiveAIAssistant(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            user_id = data.get("user_id", "default")
            actions = data.get("actions", [])
            
            assistant.learn_preferences(user_id, actions)
            return jsonify({"status": "success"}), 200
        except Exception as e:
            logging.error(f"[AI ASSISTANT LEARN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/assistant/personalize/<user_id>", methods=["GET"])
    def ai_assistant_personalize(user_id):
        """Get personalized experience settings"""
        try:
            from utils.ai.proactive_assistant import ProactiveAIAssistant
            import os
            
            assistant = ProactiveAIAssistant(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            personalization = assistant.personalize_experience(user_id)
            return jsonify(personalization), 200
        except Exception as e:
            logging.error(f"[AI ASSISTANT PERSONALIZE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/assistant/contextual-help", methods=["POST"])
    def ai_assistant_contextual_help():
        """Get contextual help"""
        try:
            from utils.ai.proactive_assistant import ProactiveAIAssistant
            import os
            
            assistant = ProactiveAIAssistant(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            context = data.get("context", {})
            
            help_info = assistant.get_contextual_help(context)
            return jsonify(help_info), 200
        except Exception as e:
            logging.error(f"[AI ASSISTANT HELP] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Network Analytics Endpoints
    # AI Chat Endpoint
    
    def fix_formatting_with_llm(text, llm_client=None):
        """
        Use local LLM to fix formatting issues in AI responses.
        This is a two-pass approach: first generate content, then fix formatting.
        
        Args:
            text: Text that needs formatting fixes
            llm_client: Optional LocalLLMClient instance. If None, will create one.
            
        Returns:
            Text with improved formatting
        """
        if not text or not isinstance(text, str) or len(text.strip()) < 20:
            return text
        
        try:
            # Only use LLM formatting if client is provided (to avoid extra overhead)
            if llm_client is None:
                return text  # Skip LLM formatting if no client provided
            
            formatting_prompt = f"""Fix the markdown formatting in the following text. 
Ensure proper markdown syntax:
- Convert ** Text**: patterns to ### Text (headings)
- Convert ** Text** patterns at start of lines to ## Text (headings)
- Convert single-word ** Logging** or ** Logging to ### Logging (headings)
- Use ## for main headings, ### for subheadings
- Use **bold** only for emphasis, not headings
- Fix concatenated title words: "SummaryTo" -> "Summary\\n\\nTo"
- Fix list formatting: ensure proper spacing after dashes/bullets
- Remove excessive separators (----, ====)
- Fix numbered list formatting (1.**Text** -> 1. **Text**)
- Remove trailing ** markers
- Fix patterns like "Text:**" -> "Text:"
- Ensure proper spacing in contractions (We'll not We' ll)
- Add proper line breaks after title words (Summary, Plan, Guide, etc.)

Here's the text to fix:

{text}

Return only the corrected text, without any explanation."""
            
            formatted_text = llm_client.generate(formatting_prompt, system_prompt="You are a markdown formatting expert. Fix formatting issues while preserving all content and meaning.")
            
            if formatted_text and len(formatted_text.strip()) > len(text) * 0.5:  # Ensure we got a reasonable response
                logging.debug(f"[AI CHAT] LLM-based formatting fix applied: {len(text)} -> {len(formatted_text)} chars")
                return formatted_text
            else:
                logging.debug("[AI CHAT] LLM formatting fix returned insufficient result, using original")
                return text
        except Exception as e:
            logging.warning(f"[AI CHAT] LLM-based formatting fix failed: {e}, using original text")
            return text
    
    def normalize_ai_response(text):
        """
        Normalize AI response text to fix concatenated words and spacing issues.
        This fixes common issues where LLMs generate text without proper spacing.
        
        Args:
            text: Raw AI response text
            
        Returns:
            Normalized text with proper spacing
        """
        if not text or not isinstance(text, str):
            return text
        
        import re
        import json
        import time
        
        # #region agent log
        try:
            with open('/Users/surajsharma/OSTG/.cursor/debug.log', 'a') as f:
                log_entry = {
                    "id": f"log_{int(time.time() * 1000)}_normalize_before",
                    "timestamp": int(time.time() * 1000),
                    "location": "run_tgen_server.py:normalize_ai_response",
                    "message": "Before normalization",
                    "data": {
                        "text_length": len(text),
                        "text_preview": text[:100] if len(text) > 100 else text,
                        "has_concatenated": bool(re.search(r'([a-z])([A-Z])', text) or re.search(r'(\w+\')([a-z]{2,})', text))
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A"
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass
        # #endregion
        
        # Comprehensive fix for concatenated words - based on actual observed issues
        common_fixes = [
            # Common two-word combinations observed in responses
            (r'\b(something)(specific)\b', r'\1 \2'),
            (r'\b(you)(\'d)(like)\b', r'\1\2 \3'),
            (r'\b(with)(related)\b', r'\1 \2'),
            (r'\b(like)(humans)\b', r'\1 \2'),
            (r'\b(but)(I)(\'m)\b', r'\1 \2\3'),
            (r'\b(with)(any)\b', r'\1 \2'),
            (r'\b(there)(\'s)(any)(thing)\b', r'\1\2 \3\4'),
            (r'\b(feel)(free)\b', r'\1 \2'),
            (r'\b(help)(you)\b', r'\1 \2'),
            (r'\b(help)(with)\b', r'\1 \2'),
            (r'\b(assist)(with)\b', r'\1 \2'),
            (r'\b(here)(to)\b', r'\1 \2'),
            (r'\b(or)(emotions)\b', r'\1 \2'),
            (r'\b(a)(specific)\b', r'\1 \2'),
            (r'\b(can)(I)\b', r'\1 \2'),
            (r'\b(do)(you)\b', r'\1 \2'),
            (r'\b(are)(you)\b', r'\1 \2'),
            (r'\b(is)(there)\b', r'\1 \2'),
            (r'\b(what)(can)\b', r'\1 \2'),
            (r'\b(how)(can)\b', r'\1 \2'),
            (r'\b(would)(you)\b', r'\1 \2'),
            (r'\b(could)(you)\b', r'\1 \2'),
            (r'\b(should)(you)\b', r'\1 \2'),
            (r'\b(will)(you)\b', r'\1 \2'),
            (r'\b(looking)(for)\b', r'\1 \2'),
            (r'\b(need)(help)\b', r'\1 \2'),
            (r'\b(need)(assistance)\b', r'\1 \2'),
            (r'\b(need)(to)\b', r'\1 \2'),
            (r'\b(want)(to)\b', r'\1 \2'),
            (r'\b(try)(to)\b', r'\1 \2'),
            (r'\b(going)(to)\b', r'\1 \2'),
            (r'\b(able)(to)\b', r'\1 \2'),
            (r'\b(ready)(to)\b', r'\1 \2'),
            (r'\b(happy)(to)\b', r'\1 \2'),
            (r'\b(glad)(to)\b', r'\1 \2'),
            (r'\b(sure)(to)\b', r'\1 \2'),
            (r'\b(sure)(I)\b', r'\1 \2'),
            # Fix contractions followed by words (but preserve valid contractions like 'll, 're, 've, 'd, 'm, 's, 't, n't)
            # Only fix if the apostrophe is NOT followed by a valid contraction suffix
            (r'(\w+\')(?!ll|re|ve|d|m|s|t|n\'t\b)([a-z]{2,})', r'\1 \2'),  # "there'sanything" -> "there's anything" but "We'll" stays "We'll"
        ]
        
        normalized = text
        for pattern, replacement in common_fixes:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        
        # Fix lowercase-to-uppercase transitions (word boundaries)
        # But avoid breaking contractions and code blocks
        # Skip if inside code blocks (between ``` markers)
        code_block_pattern = r'```.*?```'
        code_blocks = re.finditer(code_block_pattern, normalized, flags=re.DOTALL)
        non_code_parts = []
        last_end = 0
        for match in code_blocks:
            # Process text before code block
            before_code = normalized[last_end:match.start()]
            before_code = re.sub(r'([a-z])([A-Z])', r'\1 \2', before_code)
            non_code_parts.append(before_code)
            # Keep code block as-is
            non_code_parts.append(match.group(0))
            last_end = match.end()
        # Process remaining text after last code block
        after_code = normalized[last_end:]
        after_code = re.sub(r'([a-z])([A-Z])', r'\1 \2', after_code)
        non_code_parts.append(after_code)
        normalized = ''.join(non_code_parts)
        
        # Fix any spacing issues around apostrophes in contractions that might have been broken
        # Restore common contractions that might have been incorrectly split
        contraction_fixes = [
            (r"(\w+)\s+'\s*(ll|re|ve|d|m|s|t)\b", r"\1'\2"),  # "We ' ll" -> "We'll"
            (r"(\w+)\s+n\s+'\s+t\b", r"\1n't"),  # "do n ' t" -> "don't"
            (r"(\w+)\s+'\s+(ll|re|ve|d|m|s|t)\b", r"\1'\2"),  # "there ' s" -> "there's" (but only if valid contraction)
        ]
        for pattern, replacement in contraction_fixes:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        
        # Clean up markdown formatting issues
        # Fix spacing around markdown syntax (but preserve code blocks)
        code_block_pattern = r'```.*?```'
        
        # Fix spacing around bold markers
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                # Process text before code block
                before_code = normalized[last_end:match.start()]
                before_code = re.sub(r'\s+(\*\*)', r'\1', before_code)  # Remove space before **
                before_code = re.sub(r'(\*\*)\s+', r'\1', before_code)  # Remove space after **
                parts.append(before_code)
                # Keep code block as-is
                parts.append(match.group(0))
                last_end = match.end()
            # Process remaining text after last code block
            after_code = normalized[last_end:]
            after_code = re.sub(r'\s+(\*\*)', r'\1', after_code)  # Remove space before **
            after_code = re.sub(r'(\*\*)\s+', r'\1', after_code)  # Remove space after **
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            # No code blocks, process entire text
            normalized = re.sub(r'\s+(\*\*)', r'\1', normalized)
            normalized = re.sub(r'(\*\*)\s+', r'\1', normalized)
        
        # Fix double spaces (but preserve code blocks)
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                before_code = re.sub(r' {2,}', ' ', before_code)  # Collapse multiple spaces
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r' {2,}', ' ', after_code)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r' {2,}', ' ', normalized)
        
        # Fix markdown formatting issues
        # Fix triple asterisks (***) to double (**) - but preserve code blocks
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                # Fix triple asterisks to double (but not if it's part of a valid pattern)
                before_code = re.sub(r'\*\*\*([^*]+?)\*\*\*', r'**\1**', before_code)  # ***text*** -> **text**
                before_code = re.sub(r'\*\*\*([^*\n]+)', r'**\1**', before_code)  # ***text -> **text**
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r'\*\*\*([^*]+?)\*\*\*', r'**\1**', after_code)
            after_code = re.sub(r'\*\*\*([^*\n]+)', r'**\1**', after_code)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r'\*\*\*([^*]+?)\*\*\*', r'**\1**', normalized)
            normalized = re.sub(r'\*\*\*([^*\n]+)', r'**\1**', normalized)
        
        # Remove excessive separator lines (more than 3 dashes/equals in a row)
        # But preserve code blocks
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                # Remove lines with only dashes/equals (more than 3)
                before_code = re.sub(r'^[-=]{4,}$', '', before_code, flags=re.MULTILINE)
                # Remove separator lines that appear at the start of a section (standalone)
                before_code = re.sub(r'^[-=]{4,}\s*\n', '', before_code, flags=re.MULTILINE)
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r'^[-=]{4,}$', '', after_code, flags=re.MULTILINE)
            after_code = re.sub(r'^[-=]{4,}\s*\n', '', after_code, flags=re.MULTILINE)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r'^[-=]{4,}$', '', normalized, flags=re.MULTILINE)
            normalized = re.sub(r'^[-=]{4,}\s*\n', '', normalized, flags=re.MULTILINE)
        
        # Fix mixed markdown heading formats
        # Fix patterns like "**Title**================### Subtitle" to proper markdown
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                # Fix "**Title**================### Subtitle" -> "## Title\n### Subtitle"
                before_code = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}\s*###\s*([^\n]+)', r'## \1\n### \2', before_code)
                # Fix "**Title**================" -> "## Title"
                before_code = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}', r'## \1', before_code)
                # Fix "**Title**--------------------" -> "## Title"
                before_code = re.sub(r'\*\*([^*]+?)\*\*\s*-{3,}', r'## \1', before_code)
                # Fix "**Title**------------------------" on same line -> "## Title"
                before_code = re.sub(r'\*\*([^*]+?)\*\*[-=]{4,}', r'## \1', before_code)
                # Fix separator lines that appear on the line immediately after a heading
                before_code = re.sub(r'(^##\s+[^\n]+)\n[-=]{4,}\n', r'\1\n', before_code, flags=re.MULTILINE)
                before_code = re.sub(r'(^###\s+[^\n]+)\n[-=]{4,}\n', r'\1\n', before_code, flags=re.MULTILINE)
                before_code = re.sub(r'(^\*\*[^*]+\*\*)\n[-=]{4,}\n', r'\1\n', before_code, flags=re.MULTILINE)
                # Fix text followed by separators on the same line
                # Pattern 1: Plain text headings (starts with capital letter, looks like a title)
                before_code = re.sub(r'^([A-Z][A-Za-z0-9\s]{2,50}?)\s*[-=]{4,}\s*\n', r'## \1\n', before_code, flags=re.MULTILINE)
                # Pattern 2: "** Text**----------------" patterns (with space after **) -> "## Text"
                before_code = re.sub(r'\*\*\s+([^*]+?)\s*\*\*[-=]{4,}', r'## \1', before_code)
                before_code = re.sub(r'\*\*\s+([^*]+?)\s*\*\*\s*\n[-=]{4,}\s*\n', r'## \1\n', before_code, flags=re.MULTILINE)
                # Fix "** Text**" at start of line (likely a heading) -> "## Text"
                before_code = re.sub(r'^\*\*\s+([^*]+?)\s*\*\*\s*$', r'## \1', before_code, flags=re.MULTILINE)
                # Fix bold text immediately followed by ###: "**Text###" -> "### Text" and separate the ### part
                before_code = re.sub(r'\*\*([^*]+?)\*\*\s*###\s*([^\n]+)', r'### \1\n### \2', before_code, flags=re.MULTILINE)
                # Fix numbered items with bold followed by ###: "1.**Text###" -> "1. ### Text"
                before_code = re.sub(r'^(\d+\.)\s*\*\*([^*]+?)\*\*\s*###\s*([^\n]+)', r'\1 ### \2\n### \3', before_code, flags=re.MULTILINE)
                # Fix numbered items with bold: "1.**Text" -> "1. ### Text"
                before_code = re.sub(r'^(\d+\.)\s*\*\*([^*\n]+?)(?:\*\*|$)', r'\1 ### \2', before_code, flags=re.MULTILINE)
                # Fix numbered items with text directly attached: "1.Text" -> "1. Text"
                before_code = re.sub(r'^(\d+\.)([A-Za-z])', r'\1 \2', before_code, flags=re.MULTILINE)
                # Fix numbered items with trailing dash: "1. Text-" -> "1. Text"
                before_code = re.sub(r'^(\d+\.\s+[^\n]+?)-$', r'\1', before_code, flags=re.MULTILINE)
                # Fix numbered items with bold and trailing dash: "1. **Text**-" or "1. ** Text**-" -> "1. Text"
                before_code = re.sub(r'^(\d+\.)\s*\*\*\s*([^*\n]+?)\s*\*\*-$', r'\1 \2', before_code, flags=re.MULTILINE)
                # Fix numbered items with bold (no trailing dash): "1. **Text**" or "1. ** Text**" -> "1. Text"
                before_code = re.sub(r'^(\d+\.)\s*\*\*\s*([^*\n]+?)\s*\*\*\s*$', r'\1 \2', before_code, flags=re.MULTILINE)
                # Fix "** Test Description:" or "** Verify..." patterns (likely subheadings) -> "### ..."
                # Match bold text that starts with common action words or descriptive words
                before_code = re.sub(r'\*\*\s+(Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify|Description|Steps|Requirements)\s*:?\s*([^*\n]*?)(?:\s*\*\*|$)', r'### \1\2', before_code, flags=re.IGNORECASE | re.MULTILINE)
                # Fix "** Verify/Test/Check..." patterns without space after ** (likely subheadings) -> "### ..."
                before_code = re.sub(r'\*\*([A-Z][a-z]+)\s+(Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify)\s+([^*\n]+?)(?:\s*\*\*|$)', r'### \1 \2 \3', before_code, flags=re.IGNORECASE | re.MULTILINE)
                # Fix remaining "** Text" patterns at start of line -> "### Text"
                before_code = re.sub(r'^\*\*\s*([A-Z][^*\n]{3,}?)(?:\s*\*\*|$)', r'### \1', before_code, flags=re.MULTILINE)
                # Fix patterns like "** Text**:" -> "### Text"
                # This handles cases where bold text ends with colon and should be a heading
                before_code = re.sub(r'\*\*\s+([A-Z][^*\n]{2,}?)\s*\*\*:', r'### \1', before_code)
                # Fix single-word headings like "** Logging" -> "### Logging"
                before_code = re.sub(r'\*\*\s+([A-Z][a-z]+)\s*$', r'### \1', before_code, flags=re.MULTILINE)
                # Fix patterns like "SummaryTo" -> "Summary\n\nTo" (common title words followed by capitalized word)
                # Fix both at start of line and in middle of text
                title_words = r'(Summary|Plan|Guide|Overview|Introduction|Conclusion|Example|Note|Warning|Error)'
                before_code = re.sub(rf'^({title_words})([A-Z][a-z]+)', r'\1\n\n\2', before_code, flags=re.MULTILINE)
                # Also fix when title word appears in middle: "Device Log SummaryTo" -> "Device Log Summary\n\nTo"
                before_code = re.sub(rf'({title_words})([A-Z][a-z]+)', r'\1\n\n\2', before_code)
                # Fix patterns like "System Logs**" -> "### System Logs" (text followed by ** at end)
                before_code = re.sub(r'([A-Z][a-zA-Z\s]+)\*\*$', r'### \1', before_code, flags=re.MULTILINE)
                # Fix patterns like "** Identify Root Cause**" -> "### Identify Root Cause" (looks like heading: multiple words or action words)
                # Only match if it's 2+ words or starts with action words to avoid breaking valid bold text
                action_words = r'(Identify|Implement|Monitor|Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify)'
                before_code = re.sub(rf'\*\*\s+({action_words}\s+[^*]+?|\w+\s+\w+[^*]*?)\s*\*\*', r'### \1', before_code, flags=re.IGNORECASE)
                # Fix concatenated words like "flappingBGP" -> "flapping BGP" (lowercase followed by uppercase)
                before_code = re.sub(r'([a-z])([A-Z][a-z]+)', r'\1 \2', before_code)
                # Fix patterns like "Text:**" -> "Text:"
                before_code = re.sub(r'([A-Za-z0-9]):\*\*', r'\1:', before_code)
                # Fix patterns where text is followed by newline(s) and colon: "Error Messages\n\n:" -> "**Error Messages:**"
                before_code = re.sub(r'([A-Z][a-zA-Z\s]+)\n+\s*:\s*', r'**\1**: ', before_code)
                # Fix patterns where bold text is followed by newline(s) and colon: "** Warning\n\n:" -> "**Warning:**"
                before_code = re.sub(r'\*\*\s+([^*\n]+?)\s*\*\*\n+\s*:\s*', r'**\1**: ', before_code)
                # Clean up extra whitespace around colons after bold
                before_code = re.sub(r'\*\*([^*]+?)\*\*\s+:\s+', r'**\1**: ', before_code)
                # Fix patterns like "Text*:" -> "**Text**:"
                before_code = re.sub(r'([A-Z][a-zA-Z\s]+)\*:', r'**\1**:', before_code)
                # Fix patterns like "*Text**:" -> "**Text**:"
                before_code = re.sub(r'\*([^*\n]+?)\*\*:', r'**\1**:', before_code)
                # Fix numbered items with ### immediately after: "2. ### Text" -> "2. Text"
                before_code = re.sub(r'^(\d+\.)\s*###\s+', r'\1 ', before_code, flags=re.MULTILINE)
                # Fix patterns like "** Text**:" -> "### Text"
                # This handles cases where bold text ends with colon and should be a heading
                before_code = re.sub(r'\*\*\s+([A-Z][^*\n]{2,}?)\s*\*\*:', r'### \1', before_code)
                # Fix trailing ** at end of lines (stray bold markers)
                before_code = re.sub(r'([^\*])\*\*\s*$', r'\1', before_code, flags=re.MULTILINE)
                # Fix "+Result:" patterns -> "**Result:**"
                before_code = re.sub(r'\+Result:\s*', r'**Result:** ', before_code, flags=re.IGNORECASE)
                # Fix leading asterisks that should be bullet points: "* text" -> "- text"
                before_code = re.sub(r'^\*\s+([^\*])', r'- \1', before_code, flags=re.MULTILINE)
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}\s*###\s*([^\n]+)', r'## \1\n### \2', after_code)
            after_code = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}', r'## \1', after_code)
            after_code = re.sub(r'\*\*([^*]+?)\*\*\s*-{3,}', r'## \1', after_code)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}\s*###\s*([^\n]+)', r'## \1\n### \2', normalized)
            normalized = re.sub(r'\*\*([^*]+?)\*\*\s*={3,}', r'## \1', normalized)
            normalized = re.sub(r'\*\*([^*]+?)\*\*\s*-{3,}', r'## \1', normalized)
            # Fix "**Title**------------------------" on same line -> "## Title"
            normalized = re.sub(r'\*\*([^*]+?)\*\*[-=]{4,}', r'## \1', normalized)
            # Fix separator lines that appear on the line immediately after a heading
            normalized = re.sub(r'(^##\s+[^\n]+)\n[-=]{4,}\n', r'\1\n', normalized, flags=re.MULTILINE)
            normalized = re.sub(r'(^###\s+[^\n]+)\n[-=]{4,}\n', r'\1\n', normalized, flags=re.MULTILINE)
            normalized = re.sub(r'(^\*\*[^*]+\*\*)\n[-=]{4,}\n', r'\1\n', normalized, flags=re.MULTILINE)
            # Fix text followed by separators on the same line
            # Pattern 1: Plain text headings (starts with capital letter, looks like a title)
            normalized = re.sub(r'^([A-Z][A-Za-z0-9\s]{2,50}?)\s*[-=]{4,}\s*\n', r'## \1\n', normalized, flags=re.MULTILINE)
            # Pattern 2: "** Text**----------------" patterns (with space after **) -> "## Text"
            normalized = re.sub(r'\*\*\s+([^*]+?)\s*\*\*[-=]{4,}', r'## \1', normalized)
            normalized = re.sub(r'\*\*\s+([^*]+?)\s*\*\*\s*\n[-=]{4,}\s*\n', r'## \1\n', normalized, flags=re.MULTILINE)
            # Fix "** Text**" at start of line (likely a heading) -> "## Text"
            normalized = re.sub(r'^\*\*\s+([^*]+?)\s*\*\*\s*$', r'## \1', normalized, flags=re.MULTILINE)
            # Fix bold text immediately followed by ###: "**Text###" -> "### Text" and separate the ### part
            normalized = re.sub(r'\*\*([^*]+?)\*\*\s*###\s*([^\n]+)', r'### \1\n### \2', normalized, flags=re.MULTILINE)
            # Fix numbered items with bold followed by ###: "1.**Text###" -> "1. ### Text"
            normalized = re.sub(r'^(\d+\.)\s*\*\*([^*]+?)\*\*\s*###\s*([^\n]+)', r'\1 ### \2\n### \3', normalized, flags=re.MULTILINE)
            # Fix numbered items with bold: "1.**Text" -> "1. ### Text"
            normalized = re.sub(r'^(\d+\.)\s*\*\*([^*\n]+?)(?:\*\*|$)', r'\1 ### \2', normalized, flags=re.MULTILINE)
            # Fix numbered items with text directly attached: "1.Text" -> "1. Text"
            normalized = re.sub(r'^(\d+\.)([A-Za-z])', r'\1 \2', normalized, flags=re.MULTILINE)
            # Fix numbered items with trailing dash: "1. Text-" -> "1. Text"
            normalized = re.sub(r'^(\d+\.\s+[^\n]+?)-$', r'\1', normalized, flags=re.MULTILINE)
            # Fix numbered items with bold and trailing dash: "1. **Text**-" or "1. ** Text**-" -> "1. Text"
            normalized = re.sub(r'^(\d+\.)\s*\*\*\s*([^*\n]+?)\s*\*\*-$', r'\1 \2', normalized, flags=re.MULTILINE)
            # Fix numbered items with bold (no trailing dash): "1. **Text**" or "1. ** Text**" -> "1. Text"
            normalized = re.sub(r'^(\d+\.)\s*\*\*\s*([^*\n]+?)\s*\*\*\s*$', r'\1 \2', normalized, flags=re.MULTILINE)
            # Fix "** Test Description:" or "** Verify..." patterns (likely subheadings) -> "### ..."
            # Match bold text that starts with common action words or descriptive words
            normalized = re.sub(r'\*\*\s+(Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify|Description|Steps|Requirements)\s*:?\s*([^*\n]*?)(?:\s*\*\*|$)', r'### \1\2', normalized, flags=re.IGNORECASE | re.MULTILINE)
            # Fix "** Verify/Test/Check..." patterns without space after ** (likely subheadings) -> "### ..."
            normalized = re.sub(r'\*\*([A-Z][a-z]+)\s+(Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify)\s+([^*\n]+?)(?:\s*\*\*|$)', r'### \1 \2 \3', normalized, flags=re.IGNORECASE | re.MULTILINE)
            # Fix remaining "** Text" patterns at start of line -> "### Text"
            normalized = re.sub(r'^\*\*\s*([A-Z][^*\n]{3,}?)(?:\s*\*\*|$)', r'### \1', normalized, flags=re.MULTILINE)
            # Fix patterns like "** Text**:" -> "### Text"
            # This handles cases where bold text ends with colon and should be a heading
            normalized = re.sub(r'\*\*\s+([A-Z][^*\n]{2,}?)\s*\*\*:', r'### \1', normalized)
            # Fix single-word headings like "** Logging" -> "### Logging"
            normalized = re.sub(r'\*\*\s+([A-Z][a-z]+)\s*$', r'### \1', normalized, flags=re.MULTILINE)
            # Fix patterns like "SummaryTo" -> "Summary\n\nTo" (common title words followed by capitalized word)
            # Fix both at start of line and in middle of text
            title_words = r'(Summary|Plan|Guide|Overview|Introduction|Conclusion|Example|Note|Warning|Error)'
            normalized = re.sub(rf'^({title_words})([A-Z][a-z]+)', r'\1\n\n\2', normalized, flags=re.MULTILINE)
            # Also fix when title word appears in middle: "Device Log SummaryTo" -> "Device Log Summary\n\nTo"
            normalized = re.sub(rf'({title_words})([A-Z][a-z]+)', r'\1\n\n\2', normalized)
            # Fix patterns like "System Logs**" -> "### System Logs" (text followed by ** at end)
            normalized = re.sub(r'([A-Z][a-zA-Z\s]+)\*\*$', r'### \1', normalized, flags=re.MULTILINE)
            # Fix patterns like "** Identify Root Cause**" -> "### Identify Root Cause" (looks like heading: multiple words or action words)
            # Only match if it's 2+ words or starts with action words to avoid breaking valid bold text
            action_words = r'(Identify|Implement|Monitor|Verify|Test|Check|Validate|Confirm|Ensure|Setup|Configure|Execute|Run|Create|Add|Remove|Delete|Update|Modify)'
            normalized = re.sub(rf'\*\*\s+({action_words}\s+[^*]+?|\w+\s+\w+[^*]*?)\s*\*\*', r'### \1', normalized, flags=re.IGNORECASE)
            # Fix concatenated words like "flappingBGP" -> "flapping BGP" (lowercase followed by uppercase)
            normalized = re.sub(r'([a-z])([A-Z][a-z]+)', r'\1 \2', normalized)
            # Fix patterns like "Text:**" -> "Text:"
            normalized = re.sub(r'([A-Za-z0-9]):\*\*', r'\1:', normalized)
            # Fix patterns where text is followed by newline(s) and colon: "Error Messages\n\n:" -> "**Error Messages:**"
            normalized = re.sub(r'([A-Z][a-zA-Z\s]+)\n+\s*:\s*', r'**\1**: ', normalized)
            # Fix patterns where bold text is followed by newline(s) and colon: "** Warning\n\n:" -> "**Warning:**"
            normalized = re.sub(r'\*\*\s+([^*\n]+?)\s*\*\*\n+\s*:\s*', r'**\1**: ', normalized)
            # Clean up extra whitespace around colons after bold
            normalized = re.sub(r'\*\*([^*]+?)\*\*\s+:\s+', r'**\1**: ', normalized)
            # Fix patterns like "Text*:" -> "**Text**:"
            normalized = re.sub(r'([A-Z][a-zA-Z\s]+)\*:', r'**\1**:', normalized)
            # Fix patterns like "*Text**:" -> "**Text**:"
            normalized = re.sub(r'\*([^*\n]+?)\*\*:', r'**\1**:', normalized)
            # Fix numbered items with ### immediately after: "2. ### Text" -> "2. Text"
            normalized = re.sub(r'^(\d+\.)\s*###\s+', r'\1 ', normalized, flags=re.MULTILINE)
            # Fix trailing ** at end of lines (stray bold markers)
            normalized = re.sub(r'([^\*])\*\*\s*$', r'\1', normalized, flags=re.MULTILINE)
            # Fix "+Result:" patterns -> "**Result:**"
            normalized = re.sub(r'\+Result:\s*', r'**Result:** ', normalized, flags=re.IGNORECASE)
            # Fix leading asterisks that should be bullet points: "* text" -> "- text"
            normalized = re.sub(r'^\*\s+([^\*])', r'- \1', normalized, flags=re.MULTILINE)
        
        # Fix list formatting issues
        # Fix dashes/bullets without space (e.g., "-Step" -> "- Step", "*Item" -> "* Item")
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                # Fix dash/bullet without space before text (but not if it's part of a separator line)
                # Match any non-whitespace character except dash and asterisk (use \S with negative lookahead)
                before_code = re.sub(r'^([-*])(?![-\*])(\S)', r'\1 \2', before_code, flags=re.MULTILINE)
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r'^([-*])(?![-\*])(\S)', r'\1 \2', after_code, flags=re.MULTILINE)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r'^([-*])([A-Za-z])', r'\1 \2', normalized, flags=re.MULTILINE)
        
        # Fix numbered list formatting (e.g., "1.**Text**" -> "1. **Text**")
        code_blocks = list(re.finditer(code_block_pattern, normalized, flags=re.DOTALL))
        if code_blocks:
            parts = []
            last_end = 0
            for match in code_blocks:
                before_code = normalized[last_end:match.start()]
                # Fix numbered lists without space before bold (e.g., "1.**Text**" -> "1. **Text**")
                before_code = re.sub(r'(\d+)\.\*\*([^*]+?)\*\*', r'\1. **\2**', before_code)
                parts.append(before_code)
                parts.append(match.group(0))
                last_end = match.end()
            after_code = normalized[last_end:]
            after_code = re.sub(r'(\d+)\.\*\*([^*]+?)\*\*', r'\1. **\2**', after_code)
            parts.append(after_code)
            normalized = ''.join(parts)
        else:
            normalized = re.sub(r'(\d+)\.\*\*([^*]+?)\*\*', r'\1. **\2**', normalized)
        
        # Clean up excessive blank lines (more than 2 consecutive newlines)
        normalized = re.sub(r'\n{3,}', '\n\n', normalized)
        
        # #region agent log
        try:
            with open('/Users/surajsharma/OSTG/.cursor/debug.log', 'a') as f:
                log_entry = {
                    "id": f"log_{int(time.time() * 1000)}_normalize_after",
                    "timestamp": int(time.time() * 1000),
                    "location": "run_tgen_server.py:normalize_ai_response",
                    "message": "After normalization",
                    "data": {
                        "text_length": len(normalized),
                        "text_preview": normalized[:100] if len(normalized) > 100 else normalized,
                        "was_changed": text != normalized,
                        "has_concatenated": bool(re.search(r'([a-z])([A-Z])', normalized) or re.search(r'(\w+\')([a-z]{2,})', normalized))
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A"
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass
        # #endregion
        
        return normalized
    
    @app.route("/api/ai/chat", methods=["POST"])
    def ai_chat():
        """Handle AI chat messages using LLM (Ollama or Cloud API)"""
        try:
            import os
            import json
            
            data = request.get_json()
            message = data.get("message", "")
            context = data.get("context", {})
            ai_mode_preference = data.get("ai_mode_preference", "hybrid")  # Get preference from client
            normalize_response = data.get("normalize_response", True)  # Default to True for backward compatibility
            
            if not message:
                return jsonify({"error": "Message is required"}), 400
            
            # Determine mode preferences
            use_cloud_only = (ai_mode_preference == "cloud")
            use_local_only = (ai_mode_preference == "local")
            
            if use_cloud_only:
                logging.info("[AI CHAT] Cloud Only mode enabled - will not fall back to local LLM")
            elif use_local_only:
                logging.info("[AI CHAT] Local Only mode enabled - will not use cloud API")
            
            # Try cloud API first (OpenAI/Groq) if available AND not in Local Only mode
            api_key = get_ai_api_key()
            api_base = get_ai_api_base()
            
            logging.info(f"[AI CHAT] Checking API availability - has_key: {bool(api_key)}, has_base: {bool(api_base)}, base: {api_base}, mode: {ai_mode_preference}")
            
            if api_key and not use_local_only:
                try:
                    import openai
                    # Use OpenAI-compatible API (OpenAI, Groq, etc.)
                    client = openai.OpenAI(
                        api_key=api_key,
                        base_url=api_base if api_base else None,
                        timeout=30.0
                    )
                    
                    # Determine which model to use
                    models_to_try = ["gpt-4"]  # Default for OpenAI
                    if api_base and "groq" in api_base.lower():
                        # Try Groq models - try faster ones first
                        models_to_try = [
                            "llama-3.1-8b-instant",      # Fastest, most available
                            "llama-3.1-70b-versatile",   # More capable
                            "mixtral-8x7b-32768"         # Alternative
                        ]
                    
                    # Enhanced system prompt with detailed formatting rules
                    system_prompt = """You are NetGenAI, a networking-focused assistant.
- Always interpret terms like interface, link, flap, BGP, OSPF, VLAN, MTU in the context of computer networking.
- Provide concise, structured answers (bullets/steps) for network ops: troubleshooting, test plans, configs, and automation.

CRITICAL MARKDOWN FORMATTING RULES:
1. Headings: Use ## for main headings, ### for subheadings. NEVER use **bold** for headings.
   Example: ## Test Plan\\n### Test Cases (NOT **Test Plan**)

2. Bold text: Use **bold** only for emphasis within sentences, NOT for headings.
   Correct: This is **important**. Wrong: ** Configure logging**: (use ### Configure logging)

3. Lists: Proper spacing after dashes: - Item (NOT -Item)

4. Numbered lists: Space between number and content: 1. Step (NOT 1.Step or 1.**Step**)

5. Code blocks: Use ```language tags

6. NO separators: Never use ---- or ====

7. Contractions: No spaces before apostrophes: We'll (NOT We' ll)

8. Remove trailing ** markers

If off-topic, ask for clarification."""
                    
                    # Try models in order until one works
                    last_error = None
                    for model in models_to_try:
                        try:
                            logging.info(f"[AI CHAT] Attempting cloud API call with model: {model}, base_url: {api_base}")
                            
                            # Check if client wants streaming (via query parameter or header)
                            stream_requested = request.args.get('stream', 'false').lower() == 'true'
                            
                            if stream_requested:
                                # Streaming response
                                from flask import Response, stream_with_context
                                import json as json_module
                                
                                def generate_stream():
                                    full_response = ""
                                    for chunk in client.chat.completions.create(
                                        model=model,
                                        messages=[
                                            {"role": "system", "content": system_prompt},
                                            {"role": "user", "content": message}
                                        ],
                                        temperature=0.7,
                                        max_tokens=2000,
                                        stream=True
                                    ):
                                        if chunk.choices[0].delta.content:
                                            content = chunk.choices[0].delta.content
                                            full_response += content
                                            try:
                                                logging.info(f"[AI CHAT][RAW_STREAM_CHUNK] {json_module.dumps(chunk.model_dump())}")
                                            except Exception:
                                                logging.info(f"[AI CHAT][RAW_STREAM_CHUNK] {chunk}")
                                            # Send chunk to client
                                            yield f"data: {json_module.dumps({'chunk': content, 'model': model, 'source': 'cloud_api'})}\n\n"
                                    
                                    # Normalize full response to fix concatenated words (if enabled)
                                    if normalize_response:
                                        full_response = normalize_ai_response(full_response)
                                    
                                    # Send final message with full response
                                    yield f"data: {json_module.dumps({'done': True, 'response': full_response, 'model': model, 'source': 'cloud_api'})}\n\n"
                                
                                return Response(
                                    stream_with_context(generate_stream()),
                                    mimetype='text/event-stream',
                                    headers={
                                        'Cache-Control': 'no-cache',
                                        'X-Accel-Buffering': 'no'
                                    }
                                )
                            else:
                                # Non-streaming response (original behavior)
                                response = client.chat.completions.create(
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": message}
                                    ],
                                    temperature=0.7,
                                    max_tokens=2000
                                )
                                
                                try:
                                    logging.info(f"[AI CHAT][RAW_RESPONSE] {response.model_dump_json()}")
                                except Exception:
                                    logging.info(f"[AI CHAT][RAW_RESPONSE] {response}")
                                
                                ai_response = response.choices[0].message.content
                                # Normalize response to fix concatenated words (if enabled)
                                if normalize_response:
                                    ai_response = normalize_ai_response(ai_response)
                                logging.info(f"[AI CHAT] Cloud API response generated successfully (model: {model})")
                                return jsonify({
                                    "response": ai_response,
                                    "model": model,
                                    "source": "cloud_api"
                                }), 200
                        except Exception as model_error:
                            last_error = model_error
                            error_msg_str = str(model_error)
                            # Check for common API URL errors and log helpful message
                            if "404" in error_msg_str or "unknown_url" in error_msg_str or "not found" in error_msg_str.lower():
                                if "groq" in api_base.lower() and "/v2" in api_base:
                                    logging.error(f"[AI CHAT] Invalid Groq API URL detected: {api_base}. Groq uses /openai/v1, not /openai/v2.")
                                    logging.error(f"[AI CHAT] Please update your API Base URL in AI Settings to: https://api.groq.com/openai/v1")
                                else:
                                    logging.warning(f"[AI CHAT] Model {model} failed with 404/URL error: {error_msg_str[:200]}")
                            else:
                                logging.warning(f"[AI CHAT] Model {model} failed: {error_msg_str[:200]}, trying next...")
                            continue
                    
                    # If all models failed, handle gracefully instead of raising
                    # This allows fallback to local LLM in hybrid mode
                    if last_error:
                        error_msg_str = str(last_error)
                        # Check if it's a URL/404 error
                        if "404" in error_msg_str or "unknown_url" in error_msg_str:
                            logging.error(f"[AI CHAT] All cloud API models failed with URL/404 error. Falling back to local LLM in hybrid mode.")
                            if "groq" in api_base.lower() and "/v2" in api_base:
                                logging.error(f"[AI CHAT] For Groq, the correct URL is: https://api.groq.com/openai/v1 (not /v2)")
                            # Don't raise - let it fall through to local LLM in hybrid mode
                        else:
                            # For other errors, log and continue to fallback
                            logging.error(f"[AI CHAT] All cloud API models failed: {error_msg_str[:200]}. Falling back to local LLM in hybrid mode.")
                    else:
                        logging.error("[AI CHAT] All cloud API models failed with unknown error. Falling back to local LLM in hybrid mode.")
                    
                    # Don't raise exception - let it fall through to local LLM fallback
                except Exception as e:
                    error_msg = str(e)
                    logging.error(f"[AI CHAT] Cloud API failed with error: {error_msg}")
                    logging.error(f"[AI CHAT] Exception type: {type(e).__name__}")
                    import traceback
                    logging.error(f"[AI CHAT] Traceback: {traceback.format_exc()}")
                    
                    # Check for URL/404 errors and provide helpful message
                    if "404" in error_msg or "unknown_url" in error_msg or "not found" in error_msg.lower():
                        if "groq" in api_base.lower() and "/v2" in api_base:
                            logging.error(f"[AI CHAT] ERROR: Invalid Groq API URL: {api_base}. Groq uses /openai/v1, not /openai/v2.")
                            logging.error(f"[AI CHAT] Please update your API Base URL in AI Settings to: https://api.groq.com/openai/v1")
                    
                    # Don't raise - let it fall through to local LLM in hybrid mode
                    
                    # If Cloud Only mode, return error instead of falling back to local LLM
                    if use_cloud_only:
                        # Provide more helpful error message
                        error_summary = "Cloud API failed"
                        if "401" in error_msg or "authentication" in error_msg.lower() or "invalid api key" in error_msg.lower():
                            error_summary = "Invalid API key or authentication failed"
                        elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                            error_summary = "Connection to cloud API failed - check network"
                        elif "rate limit" in error_msg.lower() or "429" in error_msg:
                            error_summary = "API rate limit exceeded - please try again later"
                        elif "model" in error_msg.lower() or "not found" in error_msg.lower():
                            error_summary = "Model not available - check model name"
                        
                        return jsonify({
                            "error": "Cloud API failed and Cloud Only mode is enabled",
                            "response": f"{error_summary}. Please check:\n\n1. API key is correct\n2. API base URL is correct (for Groq: https://api.groq.com/openai/v1)\n3. Network connectivity\n4. API service status\n\nError details: {error_msg[:200]}",
                            "model": "error",
                            "source": "cloud_api_error"
                        }), 500
                    
                    # Fall through to local LLM only if not in Cloud Only mode
            
            # Try local LLM (Ollama) if:
            # 1. Local Only mode is enabled, OR
            # 2. Cloud API failed/not available AND Cloud Only mode is not enabled
            if use_local_only or (not use_cloud_only):
                try:
                    from utils.ai.local_ai_engine import LocalLLMClient
                    
                    # Load settings from file if available
                    settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                    ollama_url = "http://localhost:11434"
                    ollama_model = None
                    
                    if os.path.exists(settings_file):
                        try:
                            with open(settings_file, 'r') as f:
                                settings = json.load(f)
                                ollama_url = settings.get("ollama_url", ollama_url)
                                ollama_model = settings.get("ollama_model")
                        except Exception:
                            pass
                    
                    llm_client = LocalLLMClient(
                        llm_type="ollama",
                        base_url=ollama_url,
                        model=ollama_model
                    )
                    
                    # Enhanced system prompt with detailed formatting rules for local LLM
                    system_prompt = """You are NetGenAI, a networking-focused assistant.
- Always interpret terms like interface, link, flap, BGP, OSPF, VLAN, MTU in the context of computer networking.
- Provide concise, structured answers (bullets/steps) for network ops: troubleshooting, test plans, configs, and automation.

CRITICAL MARKDOWN FORMATTING RULES:
1. Headings: Use ## for main headings, ### for subheadings. NEVER use **bold** for headings.
   Example: ## Test Plan\\n### Test Cases (NOT **Test Plan**)

2. Bold text: Use **bold** only for emphasis within sentences, NOT for headings.
   Correct: This is **important**. Wrong: ** Configure logging**: (use ### Configure logging)

3. Lists: Proper spacing after dashes: - Item (NOT -Item)

4. Numbered lists: Space between number and content: 1. Step (NOT 1.Step or 1.**Step**)

5. Code blocks: Use ```language tags

6. NO separators: Never use ---- or ====

7. Contractions: No spaces before apostrophes: We'll (NOT We' ll)

8. Remove trailing ** markers

If off-topic, ask for clarification."""
                    
                    ai_response = llm_client.generate(message, system_prompt=system_prompt)
                    
                    if ai_response and len(ai_response.strip()) > 10:
                        # Optionally use LLM to fix formatting (two-pass approach)
                        # Check if LLM-based formatting is enabled via environment variable or settings
                        use_llm_formatting = os.environ.get("USE_LLM_FORMATTING", "false").lower() == "true"
                        
                        if use_llm_formatting:
                            logging.debug("[AI CHAT] Using LLM-based formatting fix (two-pass)")
                            ai_response = fix_formatting_with_llm(ai_response, llm_client)
                        
                        # Apply regex-based normalization if enabled (faster and catches edge cases)
                        if normalize_response:
                            ai_response = normalize_ai_response(ai_response)
                        logging.info(f"[AI CHAT] Local LLM response generated (model: {llm_client.model})")
                        return jsonify({
                            "response": ai_response,
                            "model": llm_client.model,
                            "source": "local_llm"
                        }), 200
                except Exception as e:
                    logging.warning(f"[AI CHAT] Local LLM failed: {e}")
            
            # Fallback to rule-based if LLM not available
            from utils.ai.local_ai_engine import LocalAIEngine
            engine = LocalAIEngine(model_dir="/opt/OSTG/ai_models")
            response = engine.chat(message, context)
            
            # Normalize response to fix concatenated words if enabled (though rule-based should already be fine)
            if normalize_response:
                response = normalize_ai_response(response)
            
            return jsonify({
                "response": response,
                "model": "rule-based",
                "source": "fallback"
            }), 200
            
        except Exception as e:
            logging.error(f"[AI CHAT] Error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return jsonify({
                "error": str(e),
                "response": "I apologize, but I encountered an error. Please try again."
            }), 500
    
    @app.route("/api/ai/analytics/performance", methods=["POST"])
    def ai_analytics_performance():
        """Analyze network performance"""
        try:
            from utils.ai.network_analytics import NetworkAnalytics
            import os
            from datetime import datetime, timedelta
            
            analytics = NetworkAnalytics(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            start_time_str = data.get("start_time")
            end_time_str = data.get("end_time")
            device_id = data.get("device_id")
            
            # Parse time range
            if start_time_str and end_time_str:
                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            else:
                # Default to last 24 hours
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(hours=24)
            
            analysis = analytics.analyze_performance((start_time, end_time), device_id)
            return jsonify(analysis), 200
        except Exception as e:
            logging.error(f"[AI ANALYTICS PERFORMANCE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/analytics/traffic", methods=["POST"])
    def ai_analytics_traffic():
        """Analyze network traffic"""
        try:
            from utils.ai.network_analytics import NetworkAnalytics
            import os
            
            analytics = NetworkAnalytics(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            filters = data.get("filters", {})
            
            analysis = analytics.analyze_traffic(filters)
            return jsonify(analysis), 200
        except Exception as e:
            logging.error(f"[AI ANALYTICS TRAFFIC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/analytics/protocol/<protocol>", methods=["GET"])
    def ai_analytics_protocol(protocol):
        """Analyze protocol performance"""
        try:
            from utils.ai.network_analytics import NetworkAnalytics
            import os
            from datetime import datetime, timedelta
            
            analytics = NetworkAnalytics(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            # Get time range from query params
            hours = request.args.get("hours", 24, type=int)
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours)
            
            analysis = analytics.analyze_protocols(protocol, (start_time, end_time))
            return jsonify(analysis), 200
        except Exception as e:
            logging.error(f"[AI ANALYTICS PROTOCOL] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/analytics/insights", methods=["POST"])
    def ai_analytics_insights():
        """Generate insights from analytics data"""
        try:
            from utils.ai.network_analytics import NetworkAnalytics
            import os
            
            analytics = NetworkAnalytics(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY")
            )
            
            data = request.get_json()
            analysis_data = data.get("data", {})
            
            insights = analytics.generate_insights(analysis_data)
            return jsonify({"insights": insights}), 200
        except Exception as e:
            logging.error(f"[AI ANALYTICS INSIGHTS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # Test Plan Generator Endpoints
    @app.route("/api/ai/test/plan/generate", methods=["POST"])
    def ai_generate_test_plan():
        """Generate comprehensive test plan from functional specification"""
        try:
            from utils.ai.test_plan_generator import TestPlanGenerator
            import os
            
            data = request.get_json()
            functional_spec = data.get("functional_spec", {})
            ai_mode_preference = data.get("ai_mode_preference", "hybrid")
            
            if not functional_spec:
                return jsonify({"error": "functional_spec is required"}), 400
            
            # Determine AI mode based on preference
            api_key = get_ai_api_key()
            api_base = get_ai_api_base()
            use_cloud_only = (ai_mode_preference == "cloud")
            use_local_only = (ai_mode_preference == "local")
            
            # Log API key status for debugging
            api_key_present = bool(api_key)
            api_key_length = len(api_key) if api_key else 0
            api_key_preview = f"{api_key[:10]}..." if api_key and len(api_key) > 10 else "(empty)"
            logging.info(f"[AI TEST PLAN] API Key Status: present={api_key_present}, length={api_key_length}, preview={api_key_preview}")
            logging.info(f"[AI TEST PLAN] API Base: {api_base or '(default - OpenAI)'}")
            logging.info(f"[AI TEST PLAN] AI Mode Preference: {ai_mode_preference}, cloud_only={use_cloud_only}, local_only={use_local_only}")
            
            # Configure generator based on AI mode preference
            use_ai_api = bool(api_key) and not use_local_only
            use_local_llm = not use_cloud_only  # Use local LLM unless cloud-only is requested
            
            # Note: Standard Mode can work with templates even without LLM
            # Only Agent Mode requires LLM, so we allow cloud-only mode to proceed
            # even without API key - it will fall back to templates
            # The generator will handle the fallback gracefully
            
            generator = TestPlanGenerator(
                use_ai_api=use_ai_api,
                api_key=api_key if use_ai_api else None,
                api_base=api_base if use_ai_api else None,
                use_local_llm=use_local_llm
            )
            
            # Log warning if cloud-only mode but no API key (will use templates)
            if use_cloud_only and not api_key:
                logging.warning(f"[AI TEST PLAN] Cloud-only mode requested but no API key found. Will fall back to template generation.")
                logging.warning(f"[AI TEST PLAN] To use cloud API, ensure API key is set via /api/ai/settings endpoint or OPENAI_API_KEY environment variable.")
            
            logging.info(f"[AI TEST PLAN] Mode preference: {ai_mode_preference}, use_ai_api: {use_ai_api}, use_local_llm: {use_local_llm}, api_key_present: {bool(api_key)}")
            
            test_plan = generator.generate_test_plan(functional_spec)
            # Ensure test_plan is a dict and wrap in response format
            if isinstance(test_plan, dict):
                return jsonify({"test_plan": test_plan}), 200
            elif isinstance(test_plan, str):
                # If it's a string (error message), return as error
                return jsonify({"error": test_plan, "test_plan": {}}), 200
            else:
                return jsonify({"test_plan": test_plan if test_plan else {}}), 200
        except Exception as e:
            logging.error(f"[AI TEST PLAN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/plan/generate-unit", methods=["POST"])
    def ai_generate_unit_tests_from_spec():
        """Generate unit tests from functional specification"""
        try:
            from utils.ai.test_plan_generator import TestPlanGenerator
            import os
            
            generator = TestPlanGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            functional_spec = data.get("functional_spec", {})
            test_framework = data.get("framework", "pytest")
            
            if not functional_spec:
                return jsonify({"error": "functional_spec is required"}), 400
            
            unit_tests = generator.generate_unit_tests_from_spec(functional_spec, test_framework)
            return jsonify({"unit_tests": unit_tests, "count": len(unit_tests)}), 200
        except Exception as e:
            logging.error(f"[AI UNIT TESTS FROM SPEC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/plan/generate-document", methods=["POST"])
    def ai_generate_test_plan_document():
        """Generate detailed test plan document (markdown)"""
        try:
            from utils.ai.test_plan_generator import TestPlanGenerator
            import os
            
            generator = TestPlanGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            functional_spec = data.get("functional_spec", {})
            
            if not functional_spec:
                return jsonify({"error": "functional_spec is required"}), 400
            
            document = generator.generate_detailed_test_plan_document(functional_spec)
            return jsonify({"document": document, "format": "markdown"}), 200
        except Exception as e:
            logging.error(f"[AI TEST PLAN DOC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/plan/generate-pytest", methods=["POST"])
    def ai_generate_pytest_from_test_plan():
        """Generate pytest script from test plan"""
        try:
            from utils.ai.test_plan_generator import TestPlanGenerator
            import os
            
            generator = TestPlanGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            test_plan = data.get("test_plan")
            output_format = data.get("output_format", "file")
            
            if not test_plan:
                return jsonify({"error": "test_plan is required"}), 400
            
            pytest_script = generator.generate_pytest_script_from_test_plan(test_plan, output_format)
            return jsonify({"pytest_script": pytest_script, "format": "python"}), 200
        except Exception as e:
            logging.error(f"[AI PYTEST FROM PLAN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/plan/generate-pytest-from-spec", methods=["POST"])
    def ai_generate_pytest_from_spec():
        """Generate executable pytest script directly from functional specification"""
        try:
            from utils.ai.test_plan_generator import TestPlanGenerator
            import os
            
            generator = TestPlanGenerator(
                use_ai_api=bool(os.environ.get("OPENAI_API_KEY")),
                api_key=os.environ.get("OPENAI_API_KEY"),
                use_local_llm=True
            )
            
            data = request.get_json()
            functional_spec = data.get("functional_spec", {})
            test_framework = data.get("framework", "pytest")
            
            if not functional_spec:
                return jsonify({"error": "functional_spec is required"}), 400
            
            pytest_script = generator.generate_executable_pytest_from_spec(functional_spec, test_framework)
            return jsonify({
                "pytest_script": pytest_script,
                "format": "python",
                "framework": test_framework
            }), 200
        except Exception as e:
            logging.error(f"[AI PYTEST FROM SPEC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/test/plan/agent", methods=["POST"])
    def ai_test_plan_agent():
        """AI Agent endpoint for autonomous test plan generation"""
        try:
            from utils.ai.test_plan_agent import TestPlanAgent, TestPlanAgentToolRegistry
            import os
            
            data = request.get_json()
            user_request = data.get("message", "")
            context = data.get("context", {})
            ai_mode_preference = data.get("ai_mode_preference", "hybrid")
            user_selected_model = data.get("agent_model", "").strip()  # User-selected model from UI
            
            if not user_request:
                return jsonify({"error": "message is required"}), 400
            
            # Get API configuration first
            api_key = get_ai_api_key()
            api_base = get_ai_api_base()
            
            # Initialize tool registry with API configuration
            # We'll set the model later after determining it
            tool_registry = TestPlanAgentToolRegistry(
                api_key=api_key,
                api_base=api_base
            )
            
            # Setup LLM client (similar to chat endpoint)
            use_cloud_only = (ai_mode_preference == "cloud")
            use_local_only = (ai_mode_preference == "local")
            
            llm_client = None
            agent_model = None  # Store model name separately
            
            # Check if cloud-only mode is enabled but API key is missing
            if use_cloud_only and not api_key:
                return jsonify({
                    "error": "Cloud-only mode is enabled but no API key is configured.",
                    "hint": "Please configure OPENAI_API_KEY environment variable or set API key in server settings. Agent mode requires cloud API with function calling support."
                }), 503
            
            # Try cloud API first if available and not in Local Only mode
            if api_key and not use_local_only:
                try:
                    import openai
                    llm_client = openai.OpenAI(
                        api_key=api_key,
                        base_url=api_base if api_base else None,
                        timeout=60.0
                    )
                    
                    # Priority: 1) User-selected from UI, 2) Settings file, 3) Defaults
                    if user_selected_model:
                        agent_model = user_selected_model
                        logging.info(f"[TEST PLAN AGENT] Using UI-selected model: {agent_model}")
                    else:
                        # Try to get from settings file
                        try:
                            settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                            if os.path.exists(settings_file):
                                with open(settings_file, 'r') as f:
                                    client_settings = json.load(f)
                                    settings_model = client_settings.get("cloud_model", "").strip()
                                    if settings_model:
                                        agent_model = settings_model
                                        logging.info(f"[TEST PLAN AGENT] Using settings model: {agent_model}")
                        except Exception as e:
                            logging.debug(f"[TEST PLAN AGENT] Could not read user model preference: {e}")
                    
                    # Fallback to defaults if no model selected
                    if not agent_model:
                        if api_base and "groq" in api_base.lower():
                            # Groq models - default to faster model for agent
                            agent_model = "llama-3.1-8b-instant"
                        else:
                            # OpenAI - use GPT-4 for better function calling
                            agent_model = "gpt-4"
                        logging.info(f"[TEST PLAN AGENT] Using default model: {agent_model}")
                    
                    logging.info(f"[TEST PLAN AGENT] Using cloud model: {agent_model} at {api_base or 'OpenAI'}")
                    
                    # Update tool registry with the selected model
                    tool_registry.model = agent_model
                    
                except Exception as e:
                    logging.warning(f"[TEST PLAN AGENT] Cloud API initialization failed: {e}")
                    llm_client = None
                    # If cloud-only mode, provide specific error
                    if use_cloud_only:
                        return jsonify({
                            "error": f"Cloud API initialization failed: {str(e)}",
                            "hint": "Please check your API key and base URL configuration. Agent mode requires a working cloud API with function calling support."
                        }), 503
            
            # Fallback to local LLM if cloud not available or in Local Only mode
            if not llm_client and not use_cloud_only:
                try:
                    from utils.ai.local_ai_engine import LocalLLMClient
                    import json
                    
                    settings_file = os.path.expanduser("~/.ostg_ai_settings.json")
                    user_model = None
                    user_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
                    
                    if os.path.exists(settings_file):
                        try:
                            with open(settings_file, 'r') as f:
                                settings = json.load(f)
                                user_model = settings.get("ollama_model")
                                user_url = settings.get("ollama_url", user_url)
                        except Exception:
                            pass
                    
                    local_llm = LocalLLMClient(
                        llm_type=os.environ.get("LOCAL_LLM_TYPE", "ollama"),
                        base_url=user_url,
                        model=user_model
                    )
                    
                    # Create a wrapper to make local LLM compatible with OpenAI client interface
                    # Note: Local LLM may not support function calling, so we'll need a fallback
                    # For now, we'll try to use it if available
                    class LocalLLMWrapper:
                        def __init__(self, local_llm):
                            self.local_llm = local_llm
                            self.model = local_llm.model
                        
                        class ChatCompletion:
                            def create(self, **kwargs):
                                # For local LLM without function calling, we need different handling
                                # This is a simplified wrapper - may need enhancement
                                raise NotImplementedError("Local LLM function calling not fully supported yet")
                    
                    # For now, prefer cloud API for agent (function calling)
                    if not llm_client:
                        logging.warning("[TEST PLAN AGENT] Local LLM function calling not fully supported, preferring cloud API")
                except Exception as e:
                    logging.warning(f"[TEST PLAN AGENT] Local LLM not available: {e}")
            
            if not llm_client:
                # Provide specific error message based on mode
                if use_cloud_only:
                    error_msg = "Cloud-only mode is enabled but cloud API is not available."
                    hint_msg = (
                        "Please check:\n"
                        "1. OPENAI_API_KEY environment variable is set\n"
                        "2. API key is valid and has credits\n"
                        "3. OPENAI_API_BASE is correct (if using Groq/Together AI)\n"
                        "4. Network connectivity to API endpoint\n\n"
                        "Alternatively, disable Agent Mode or change AI mode preference to 'hybrid' or 'local'."
                    )
                else:
                    error_msg = "No LLM client available. Please configure OpenAI API key or ensure Ollama is running."
                    hint_msg = "Agent mode requires LLM with function calling support (OpenAI GPT-4 or compatible)"
                
                return jsonify({
                    "error": error_msg,
                    "hint": hint_msg
                }), 503
            
            # Initialize agent with model name
            agent = TestPlanAgent(tool_registry, llm_client, model=agent_model)
            
            # Execute agent request
            result = agent.execute(user_request, context)
            
            return jsonify({
                "response": result.get("response", ""),
                "test_plan": result.get("test_plan"),
                "steps": result.get("steps", []),
                "iterations": result.get("iterations", 0),
                "state": result.get("state", "error")
            }), 200
        
        except Exception as e:
            logging.error(f"[TEST PLAN AGENT] Error: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
    
    # Pytest Device Execution Endpoints
    @app.route("/api/ai/pytest/execute-devices", methods=["POST"])
    def ai_execute_pytest_for_devices():
        """Execute pytest script against external devices"""
        try:
            from utils.ai.pytest_device_runner import PytestDeviceRunner
            
            runner = PytestDeviceRunner()
            
            data = request.get_json()
            pytest_script = data.get("pytest_script")
            device_ids = data.get("device_ids", [])
            test_config = data.get("test_config", {})
            
            if not pytest_script:
                return jsonify({"error": "pytest_script is required"}), 400
            
            if not device_ids:
                return jsonify({"error": "device_ids is required"}), 400
            
            results = runner.execute_pytest_for_devices(pytest_script, device_ids, test_config)
            return jsonify(results), 200
        except Exception as e:
            logging.error(f"[AI PYTEST DEVICES] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/pytest/execute-device-type", methods=["POST"])
    def ai_execute_pytest_for_device_type():
        """Execute pytest script for all devices of a specific type"""
        try:
            from utils.ai.pytest_device_runner import PytestDeviceRunner
            
            runner = PytestDeviceRunner()
            
            data = request.get_json()
            pytest_script = data.get("pytest_script")
            device_type = data.get("device_type")
            test_config = data.get("test_config", {})
            
            if not pytest_script:
                return jsonify({"error": "pytest_script is required"}), 400
            
            if not device_type:
                return jsonify({"error": "device_type is required"}), 400
            
            if device_type not in ["juniper", "cisco", "arista", "nokia"]:
                return jsonify({"error": f"Unsupported device type: {device_type}"}), 400
            
            results = runner.execute_pytest_for_device_type(pytest_script, device_type, test_config)
            return jsonify(results), 200
        except Exception as e:
            logging.error(f"[AI PYTEST DEVICE TYPE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/pytest/generate-device-specific", methods=["POST"])
    def ai_generate_device_specific_pytest():
        """Generate vendor-specific pytest script"""
        try:
            from utils.ai.pytest_device_runner import PytestDeviceRunner
            
            runner = PytestDeviceRunner()
            
            data = request.get_json()
            base_pytest_script = data.get("pytest_script")
            device_type = data.get("device_type")
            
            if not base_pytest_script:
                return jsonify({"error": "pytest_script is required"}), 400
            
            if not device_type:
                return jsonify({"error": "device_type is required"}), 400
            
            enhanced_script = runner.generate_device_specific_pytest(base_pytest_script, device_type)
            return jsonify({
                "pytest_script": enhanced_script,
                "device_type": device_type
            }), 200
        except Exception as e:
            logging.error(f"[AI PYTEST DEVICE SPECIFIC] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    # AI Model Management Endpoints
    @app.route("/api/ai/model/backup", methods=["POST"])
    def ai_model_backup():
        """Backup current AI model"""
        try:
            from pathlib import Path
            import shutil
            from datetime import datetime
            
            MODEL_DIR = Path("/opt/OSTG/ai_models")
            BACKUP_DIR = MODEL_DIR / "backups"
            CURRENT_MODEL = MODEL_DIR / "troubleshooting_classifier.pkl"
            
            if not CURRENT_MODEL.exists():
                return jsonify({"error": "No current model to backup"}), 404
            
            # Load metadata to get current version
            metadata_file = MODEL_DIR / "metadata.json"
            current_version = "1.0"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                    current_version = metadata.get("troubleshooting_classifier", {}).get("current_version", "1.0")
            
            # Create backup
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = BACKUP_DIR / f"troubleshooting_classifier_v{current_version}_{timestamp}.pkl"
            shutil.copy2(CURRENT_MODEL, backup_file)
            
            return jsonify({
                "status": "success",
                "backup_file": str(backup_file),
                "message": "Model backed up successfully"
            }), 200
        except Exception as e:
            logging.error(f"[AI MODEL BACKUP] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/model/train", methods=["POST"])
    def ai_model_train():
        """Train new AI model version"""
        try:
            from utils.ai.local_ai_engine import LocalAIEngine
            import sqlite3
            from pathlib import Path
            import shutil
            
            data = request.get_json()
            version = data.get("version", "2.0")
            
            # Get training data
            kb_path = "/opt/OSTG/ai_knowledge_base.db"
            if not Path(kb_path).exists():
                return jsonify({"error": "Knowledge base not found"}), 404
            
            conn = sqlite3.connect(kb_path)
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    SELECT symptoms, root_cause, solution
                    FROM troubleshooting_cases
                    WHERE resolved = 1
                    ORDER BY resolved_at DESC
                """)
            except sqlite3.OperationalError:
                conn.close()
                return jsonify({"error": "troubleshooting_cases table not found"}), 404
            
            training_data = []
            for row in cursor.fetchall():
                try:
                    symptoms = json.loads(row[0]) if row[0] else {}
                    training_data.append({
                        "symptoms": symptoms,
                        "root_cause": row[1] or "Unknown",
                        "solution": row[2] or ""
                    })
                except Exception:
                    continue
            
            conn.close()
            
            if len(training_data) < 10:
                return jsonify({
                    "error": f"Not enough training data: {len(training_data)} cases (need 10+)"
                }), 400
            
            # Train model
            local_ai = LocalAIEngine()
            model = local_ai._train_troubleshooting_model(training_data)
            
            if not model:
                return jsonify({"error": "Training failed"}), 500
            
            # Save with version
            MODEL_DIR = Path("/opt/OSTG/ai_models")
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            versioned_file = MODEL_DIR / f"troubleshooting_classifier_v{version}.pkl"
            CURRENT_MODEL = MODEL_DIR / "troubleshooting_classifier.pkl"
            shutil.copy2(CURRENT_MODEL, versioned_file)
            
            # Update metadata
            metadata_file = MODEL_DIR / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            else:
                metadata = {"troubleshooting_classifier": {"current_version": "1.0", "versions": {}}}
            
            if "troubleshooting_classifier" not in metadata:
                metadata["troubleshooting_classifier"] = {"current_version": "1.0", "versions": {}}
            
            from datetime import datetime
            metadata["troubleshooting_classifier"]["versions"][version] = {
                "created_at": datetime.now().isoformat(),
                "training_cases": len(training_data),
                "file": str(versioned_file.name)
            }
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            return jsonify({
                "status": "success",
                "version": version,
                "training_cases": len(training_data),
                "message": "Model trained successfully"
            }), 200
        except Exception as e:
            logging.error(f"[AI MODEL TRAIN] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/model/activate", methods=["POST"])
    def ai_model_activate():
        """Activate a model version"""
        try:
            from pathlib import Path
            import shutil
            
            data = request.get_json()
            version = data.get("version")
            
            if not version:
                return jsonify({"error": "version required"}), 400
            
            MODEL_DIR = Path("/opt/OSTG/ai_models")
            versioned_file = MODEL_DIR / f"troubleshooting_classifier_v{version}.pkl"
            CURRENT_MODEL = MODEL_DIR / "troubleshooting_classifier.pkl"
            
            if not versioned_file.exists():
                return jsonify({"error": f"Model version {version} not found"}), 404
            
            # Backup current model
            if CURRENT_MODEL.exists():
                BACKUP_DIR = MODEL_DIR / "backups"
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = BACKUP_DIR / f"troubleshooting_classifier_backup_{timestamp}.pkl"
                shutil.copy2(CURRENT_MODEL, backup_file)
            
            # Activate new version
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(versioned_file, CURRENT_MODEL)
            
            # Update metadata
            metadata_file = MODEL_DIR / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            else:
                metadata = {"troubleshooting_classifier": {"current_version": version, "versions": {}}}
            
            if "troubleshooting_classifier" not in metadata:
                metadata["troubleshooting_classifier"] = {"current_version": version, "versions": {}}
            
            metadata["troubleshooting_classifier"]["current_version"] = version
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            return jsonify({
                "status": "success",
                "version": version,
                "message": "Model activated successfully"
            }), 200
        except Exception as e:
            logging.error(f"[AI MODEL ACTIVATE] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/ai/model/rollback", methods=["POST"])
    def ai_model_rollback():
        """Rollback to a previous model version"""
        # Same as activate
        return ai_model_activate()
    
    @app.route("/api/ai/model/versions", methods=["GET"])
    def ai_model_versions():
        """List all model versions"""
        try:
            from pathlib import Path
            
            MODEL_DIR = Path("/opt/OSTG/ai_models")
            metadata_file = MODEL_DIR / "metadata.json"
            
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                return jsonify(metadata), 200
            else:
                return jsonify({
                    "troubleshooting_classifier": {
                        "current_version": "1.0",
                        "versions": {}
                    }
                }), 200
        except Exception as e:
            logging.error(f"[AI MODEL VERSIONS] Error: {e}")
            return jsonify({"error": str(e)}), 500
    
    app.run(host=args.host, port=args.port)



if __name__ == '__main__':
    #app.run(host='0.0.0.0', port=8501, debug=True)
    main()
