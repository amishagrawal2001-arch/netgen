"""
DHCP client/server lifecycle helpers for OSTG devices.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from types import SimpleNamespace

import docker
from docker.errors import NotFound

import ipaddress

logger = logging.getLogger(__name__)

DHCLIENT_PID_DIR = "/run"
DHCLIENT_LEASE_DIR = "/var/lib/dhcp"
DNSMASQ_PID_DIR = "/run"
DNSMASQ_LEASE_DIR = "/var/lib/misc"
DNSMASQ_CONF_DIR = "/etc/dnsmasq.d"
DNSMASQ_LOG_DIR = "/var/log"

DHCP_CONTAINER_PREFIX = "ostg-dhcp"
DHCP_CLIENT_PREFIX = "dhcp-client"
DHCP_SERVER_PREFIX = "dhcp-server"
DHCP_DOCKER_IMAGE = os.environ.get("OSTG_DHCP_IMAGE", "ostg-frr:latest")


def _ensure_paths(container=None) -> None:
    """Ensure filesystem paths exist for PID/config/lease files."""
    paths = [
        DHCLIENT_PID_DIR,
        DHCLIENT_LEASE_DIR,
        DNSMASQ_PID_DIR,
        DNSMASQ_LEASE_DIR,
        DNSMASQ_CONF_DIR,
        DNSMASQ_LOG_DIR,
    ]
    if container:
        for path in paths:
            try:
                _run_command(["mkdir", "-p", path], container=container, timeout=5)
            except Exception as exc:
                logger.warning("[DHCP] Failed to ensure path %s inside container: %s", path, exc)
    else:
        for path in paths:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as exc:
                logger.warning("[DHCP] Failed to ensure path %s: %s", path, exc)


def _derive_networks_from_pool(pool_start: str, pool_end: str) -> Optional[list]:
    """Return list of IPv4Network objects summarizing the DHCP pool range."""
    if not pool_start or not pool_end:
        return None
    try:
        start_ip = ipaddress.IPv4Address(pool_start)
        end_ip = ipaddress.IPv4Address(pool_end)
        return list(ipaddress.summarize_address_range(start_ip, end_ip))
    except Exception as exc:
        logger.warning("[DHCP] Failed to derive networks from pool %s-%s: %s", pool_start, pool_end, exc)
        return None


def _normalize_routes(route_values) -> Optional[list]:
    """Normalize user provided routes into IPv4Network list."""
    if not route_values:
        return None
    routes = []
    if isinstance(route_values, str):
        tokens = [token.strip() for token in route_values.replace(";", ",").split(",")]
    elif isinstance(route_values, (list, tuple, set)):
        tokens = []
        for item in route_values:
            if isinstance(item, str):
                tokens.extend([token.strip() for token in item.replace(";", ",").split(",")])
            else:
                tokens.append(item)
    else:
        tokens = [route_values]

    for token in tokens:
        if not token:
            continue
        try:
            network = ipaddress.ip_network(token, strict=False)
            if isinstance(network, ipaddress.IPv4Network):
                routes.append(network)
            else:
                logger.warning("[DHCP] Ignoring non-IPv4 route '%s'", token)
        except Exception as exc:
            logger.warning("[DHCP] Failed to parse gateway route '%s': %s", token, exc)
    return routes or None


def _normalize_gateway_tokens(route_values) -> Optional[list]:
    """Normalize gateway route values into a list of CIDR strings."""
    if not route_values:
        return None
    tokens = []
    if isinstance(route_values, str):
        tokens.extend([part.strip() for part in route_values.replace(";", ",").split(",")])
    elif isinstance(route_values, (list, tuple, set)):
        for item in route_values:
            if isinstance(item, str):
                tokens.extend([part.strip() for part in item.replace(";", ",").split(",")])
            else:
                tokens.append(str(item).strip())
    else:
        tokens.append(str(route_values).strip())
    normalized = [token for token in tokens if token]
    return normalized or None


def _normalize_additional_pools(raw_pools) -> list:
    """Normalize optional additional DHCP pool definitions into a list of dicts."""
    if not raw_pools:
        return []

    pools_input = raw_pools
    if isinstance(raw_pools, str):
        try:
            pools_input = json.loads(raw_pools)
        except Exception:
            return []
    elif isinstance(raw_pools, dict):
        pools_input = [raw_pools]
    elif isinstance(raw_pools, (tuple, set)):
        pools_input = list(raw_pools)

    pools: list = []
    for item in pools_input:
        if not isinstance(item, dict):
            continue
        start = item.get("pool_start") or item.get("start")
        end = item.get("pool_end") or item.get("end")
        if not start or not end:
            continue
        normalized = {
            "pool_start": str(start),
            "pool_end": str(end),
        }
        if item.get("pool_name") or item.get("name"):
            normalized["pool_name"] = str(item.get("pool_name") or item.get("name")).strip()
        if item.get("gateway"):
            normalized["gateway"] = str(item.get("gateway")).strip()
        if item.get("lease_time"):
            try:
                normalized["lease_time"] = int(item.get("lease_time"))
            except (TypeError, ValueError):
                normalized["lease_time"] = None
        gateway_routes = (
            item.get("gateway_route")
            or item.get("gateway_routes")
        )
        gateway_tokens = _normalize_gateway_tokens(gateway_routes)
        if gateway_tokens:
            normalized["gateway_route"] = gateway_tokens
        pools.append(normalized)
    return pools


def _collect_pool_networks(
    primary_start: Optional[str], primary_end: Optional[str], additional_pools: list
):
    """Gather IPv4Network objects for the base pool plus any additional pools."""
    networks = []
    base_networks = _derive_networks_from_pool(primary_start, primary_end)
    if base_networks:
        networks.extend(base_networks)
    for pool in additional_pools:
        extra = _derive_networks_from_pool(pool.get("pool_start"), pool.get("pool_end"))
        if extra:
            networks.extend(extra)
    return networks


def _collect_gateway_routes(dhcp_config: Dict, additional_pools: list) -> list:
    """Gather normalized gateway routes from primary and additional pool config."""
    routes = _normalize_routes(
        dhcp_config.get("gateway_route") or dhcp_config.get("gateway_routes")
    ) or []
    for pool in additional_pools:
        pool_routes = _normalize_routes(
            pool.get("gateway_route") or pool.get("gateway_routes")
        )
        if pool_routes:
            routes.extend(pool_routes)

    if not routes:
        return []

    unique = []
    seen = set()
    for route in routes:
        key = str(route)
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)
    return unique


def _run_command(cmd, timeout: int = 10, check: bool = False, container=None):
    """Run a subprocess command (optionally inside a container) and capture output."""
    cmd_display = cmd if isinstance(cmd, str) else " ".join(cmd)
    if container:
        logger.debug("[DHCP CMD][container %s] %s", container.name, cmd_display)
        exec_cmd = cmd if isinstance(cmd, (list, tuple)) else ["/bin/sh", "-c", cmd]
        exec_result = container.exec_run(
            exec_cmd,
            stdout=True,
            stderr=True,
            demux=True,
        )
        stdout, stderr = exec_result.output if isinstance(exec_result.output, tuple) else (exec_result.output, b"")
        stdout = stdout.decode() if isinstance(stdout, (bytes, bytearray)) else (stdout or "")
        stderr = stderr.decode() if isinstance(stderr, (bytes, bytearray)) else (stderr or "")
        result = SimpleNamespace(returncode=exec_result.exit_code, stdout=stdout, stderr=stderr)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, exec_cmd, stdout, stderr)
        return result
    else:
        logger.debug("[DHCP CMD] %s", cmd_display)
        cmd_args = cmd if isinstance(cmd, (list, tuple)) else cmd.split()
        return subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )


def _parse_ipv4(interface: str, container=None) -> Optional[Dict[str, str]]:
    """Return IPv4 address/mask for interface if present."""
    try:
        result = _run_command(["ip", "-o", "-4", "addr", "show", "dev", interface], timeout=5, container=container)
        output = result.stdout.strip()
        if not output:
            return None
        parts = output.split()
        if "inet" in parts:
            idx = parts.index("inet")
            if idx + 1 < len(parts):
                cidr = parts[idx + 1]
                if "/" in cidr:
                    ip, mask = cidr.split("/", 1)
                    return {"ip": ip, "mask": mask}
    except Exception as exc:
        logger.debug("[DHCP] Failed to parse IPv4 for %s: %s", interface, exc)
    return None


def _parse_gateway(interface: str, container=None) -> Optional[str]:
    """Return default gateway for interface if present."""
    try:
        result = _run_command(["ip", "route", "show", "dev", interface], timeout=5, container=container)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("default via"):
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2]
    except Exception as exc:
        logger.debug("[DHCP] Failed to parse gateway for %s: %s", interface, exc)
    return None


def _verify_interface_exists(interface: str, container=None) -> bool:
    """Verify that the interface exists in the container/host."""
    try:
        result = _run_command(["ip", "link", "show", interface], timeout=5, container=container)
        if result.returncode == 0 and interface in result.stdout:
            logger.debug("[DHCP] Interface %s exists", interface)
            return True
        logger.warning("[DHCP] Interface %s not found", interface)
        return False
    except Exception as exc:
        logger.warning("[DHCP] Failed to verify interface %s: %s", interface, exc)
        return False


def _is_dhclient_running(interface: str, container=None) -> bool:
    """Check whether a dhclient process is running for the given interface."""
    try:
        # Prefer pgrep if available; fall back to ps/grep
        cmd = f"pgrep -f 'dhclient.*{interface}' || ps -eo pid,cmd | grep 'dhclient' | grep -v grep | grep -q '{interface}'"
        result = _run_command(["/bin/sh", "-c", cmd], timeout=5, container=container)
        if result.returncode == 0 and result.stdout is not None:
            return True
        return result.returncode == 0 and (result.stdout or "").strip() == ""
    except Exception as exc:
        logger.debug("[DHCP] Failed to determine dhclient status for %s: %s", interface, exc)
        return False


def get_dhcp_client_snapshot(
    device_db,
    device_id: str,
    interface: str,
    dhcp_config: Optional[Dict] = None,
) -> Dict:
    """
    Retrieve the current DHCP client status for the specified device/interface without mutating state.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "dhcp_mode": "client",
        "dhcp_state": "Stopped",
        "dhcp_running": False,
        "dhcp_lease_ip": "",
        "dhcp_lease_mask": "",
        "dhcp_lease_gateway": "",
        "dhcp_lease_server": "",
        "dhcp_lease_expires": None,
        "dhcp_lease_subnet": "",
        "ipv4_address": "",
        "ipv4_mask": "",
        "ipv4_gateway": "",
        "last_dhcp_check": timestamp,
    }

    if not device_id or not interface:
        return snapshot

    container = _get_dhcp_container(device_id, mode="client")
    if not container:
        logger.debug("[DHCP] No DHCP client container found for %s", device_id)
        return snapshot

    try:
        container.reload()
    except Exception as exc:
        logger.debug("[DHCP] Failed to reload container for %s: %s", device_id, exc)

    if getattr(container, "status", None) != "running":
        logger.debug("[DHCP] DHCP client container %s not running", container.name)
        return snapshot

    ip_info = _parse_ipv4(interface, container=container)
    gateway = _parse_gateway(interface, container=container) or ""
    dhclient_running = _is_dhclient_running(interface, container=container)

    if ip_info:
        snapshot["dhcp_state"] = "Leased"
        snapshot["dhcp_running"] = True
        snapshot["dhcp_lease_ip"] = ip_info.get("ip", "")
        snapshot["dhcp_lease_mask"] = ip_info.get("mask", "")
        snapshot["dhcp_lease_gateway"] = gateway
        try:
            if snapshot["dhcp_lease_ip"] and snapshot["dhcp_lease_mask"]:
                snapshot["dhcp_lease_subnet"] = str(
                    ipaddress.IPv4Interface(f"{snapshot['dhcp_lease_ip']}/{snapshot['dhcp_lease_mask']}").network
                )
        except Exception as exc:
            logger.debug("[DHCP] Failed to derive subnet for %s: %s", interface, exc)
        snapshot["ipv4_address"] = (
            f"{snapshot['dhcp_lease_ip']}/{snapshot['dhcp_lease_mask']}"
            if snapshot["dhcp_lease_ip"] and snapshot["dhcp_lease_mask"]
            else snapshot["dhcp_lease_ip"]
        )
        snapshot["ipv4_mask"] = snapshot["dhcp_lease_mask"]
        snapshot["ipv4_gateway"] = gateway
    else:
        snapshot["dhcp_state"] = "Requesting" if dhclient_running else "No Lease"
        snapshot["dhcp_running"] = dhclient_running
        snapshot["dhcp_lease_gateway"] = gateway

    return snapshot


def _flush_ipv4(interface: str, container=None) -> None:
    """Remove all IPv4 addresses from an interface."""
    try:
        _run_command(["ip", "-4", "addr", "flush", "dev", interface], timeout=5, container=container)
        logger.debug("[DHCP] Flushed IPv4 addresses on %s", interface)
    except Exception as exc:
        logger.debug("[DHCP] Failed to flush IPv4 addresses on %s: %s", interface, exc)


def _update_device_db(device_db, device_id: str, payload: Dict):
    """Wrapper to guard database updates."""
    try:
        if device_id:
            device_db.update_device(device_id, payload)
    except Exception as exc:
        logger.warning("[DHCP] Failed to update device %s: %s", device_id, exc)


def _get_dhcp_container_name(device_id: str, mode: Optional[str] = None) -> str:
    if mode == "client":
        return f"{DHCP_CLIENT_PREFIX}-{device_id}"
    if mode == "server":
        return f"{DHCP_SERVER_PREFIX}-{device_id}"
    return f"{DHCP_CONTAINER_PREFIX}-{device_id}"


def _get_dhcp_container(device_id: str, mode: Optional[str] = None):
    """Return existing DHCP container if it exists."""
    try:
        client = docker.from_env()
        name = _get_dhcp_container_name(device_id, mode=mode)
        container = client.containers.get(name)
        container.reload()
        return container
    except Exception as exc:
        if isinstance(exc, NotFound):
            logger.debug("[DHCP] DHCP container for device %s not found", device_id)
        else:
            logger.error("[DHCP] Failed to locate DHCP container for device %s: %s", device_id, exc)
        return None


def _ensure_dhcp_container(device_id: str, mode: Optional[str] = None):
    """Ensure a dedicated DHCP container exists and is running for the device."""
    try:
        client = docker.from_env()
    except Exception as docker_exc:
        logger.error("[DHCP] Failed to connect to Docker daemon: %s", docker_exc, exc_info=True)
        return None
    
    name = _get_dhcp_container_name(device_id, mode=mode)
    logger.info(f"[DHCP] Ensuring DHCP container '{name}' for device {device_id} (mode={mode})")
    try:
        container = client.containers.get(name)
        container.reload()
        logger.info(f"[DHCP] Found existing DHCP container {name} with status: {container.status}")
        if container.status != "running":
            logger.info("[DHCP] Starting existing DHCP container %s", name)
            try:
                container.start()
                time.sleep(2)
                container.reload()
                if container.status != "running":
                    logger.error("[DHCP] Container %s failed to start, status: %s", name, container.status)
                    # Try to get logs for debugging
                    try:
                        logs = container.logs(tail=50).decode('utf-8', errors='ignore')
                        logger.error("[DHCP] Container %s logs (last 50 lines):\n%s", name, logs)
                    except Exception:
                        pass
                    return None
                logger.info(f"[DHCP] Container {name} started, new status: {container.status}")
            except Exception as start_exc:
                logger.error("[DHCP] Failed to start existing container %s: %s", name, start_exc, exc_info=True)
                return None
        return container
    except NotFound:
        # Check if Docker image exists before trying to create container
        try:
            logger.info("[DHCP] Checking if Docker image %s exists", DHCP_DOCKER_IMAGE)
            client.images.get(DHCP_DOCKER_IMAGE)
            logger.info("[DHCP] Docker image %s found", DHCP_DOCKER_IMAGE)
        except NotFound:
            logger.error("[DHCP] Docker image %s not found. Please build the image first.", DHCP_DOCKER_IMAGE)
            return None
        except Exception as img_exc:
            logger.error("[DHCP] Failed to check Docker image %s: %s", DHCP_DOCKER_IMAGE, img_exc, exc_info=True)
            return None
        
        try:
            logger.info("[DHCP] Creating DHCP container %s using image %s", name, DHCP_DOCKER_IMAGE)
            container = client.containers.run(
                image=DHCP_DOCKER_IMAGE,
                name=name,
                network_mode="host",
                privileged=True,
                cap_add=['NET_ADMIN', 'NET_RAW', 'NET_BIND_SERVICE'],
                security_opt=['seccomp:unconfined'],
                restart_policy={"Name": "unless-stopped"},
                entrypoint=None,
                command=["sleep", "infinity"],
                healthcheck={"Test": ["CMD-SHELL", "exit 0"]},
                detach=True,
            )
            time.sleep(2)
            container.reload()
            if container.status != "running":
                logger.error("[DHCP] Container %s created but not running, status: %s", name, container.status)
                # Try to get logs for debugging
                try:
                    logs = container.logs(tail=50).decode('utf-8', errors='ignore')
                    logger.error("[DHCP] Container %s logs (last 50 lines):\n%s", name, logs)
                except Exception:
                    pass
                return None
            logger.info(f"[DHCP] Successfully created DHCP container {name} with status: {container.status}")
            return container
        except docker.errors.ImageNotFound as img_not_found:
            logger.error("[DHCP] Docker image %s not found: %s", DHCP_DOCKER_IMAGE, img_not_found)
            return None
        except docker.errors.APIError as api_err:
            logger.error("[DHCP] Docker API error creating container %s: %s", name, api_err, exc_info=True)
            return None
        except Exception as exc:
            logger.error("[DHCP] Failed to create DHCP container for device %s: %s", device_id, exc, exc_info=True)
            return None
    except Exception as exc:
        logger.error("[DHCP] Error ensuring DHCP container for device %s: %s", device_id, exc, exc_info=True)
        return None


def _stop_dhcp_container(device_id: str, mode: Optional[str] = None, remove: bool = False) -> bool:
    """Stop (and optionally remove) the DHCP container for the device."""
    container = _get_dhcp_container(device_id, mode=mode)
    if not container:
        return False
    try:
        logger.info("[DHCP] Stopping DHCP container %s", container.name)
        container.stop(timeout=5)
        if remove:
            logger.info("[DHCP] Removing DHCP container %s", container.name)
            container.remove(force=True)
        return True
    except Exception as exc:
        logger.warning("[DHCP] Failed to stop DHCP container for device %s: %s", device_id, exc)
        return False


def start_dhcp_client(
    device_db,
    device_id: str,
    interface: str,
    dhcp_config: Optional[Dict] = None,
    timeout: int = 20,
    container=None,
) -> Dict:
    """
    Start a DHCP client on the interface (inside the device container if provided).

    Returns a status dict with success flag and metadata.
    """
    # Verify interface exists before proceeding
    if not _verify_interface_exists(interface, container=container):
        error_msg = f"Interface {interface} not found in container/host. Cannot start DHCP client."
        logger.error(f"[DHCP] {error_msg}")
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "client",
                "dhcp_state": "Failed",
                "dhcp_running": False,
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": error_msg}
    
    _ensure_paths(container=container)
    pidfile = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}.pid")
    leasefile = os.path.join(DHCLIENT_LEASE_DIR, f"dhclient-{interface}.leases")

    # Release any existing lease first
    try:
        _run_command(
            ["dhclient", "-4", "-r", "-pf", pidfile, interface],
            timeout=5,
            container=container,
        )
    except Exception as exc:
        logger.debug("[DHCP] dhclient release error (safe to ignore): %s", exc)

    cmd = ["dhclient", "-4", "-nw", "-pf", pidfile, "-lf", leasefile]
    # Optional timeout via configuration
    lease_timeout = int(dhcp_config.get("timeout", timeout)) if dhcp_config else timeout
    # dhclient uses seconds when passed via -timeout but only newer versions support it.
    if dhcp_config and "timeout" in dhcp_config:
        cmd.extend(["-timeout", str(lease_timeout)])
    cmd.append(interface)

    result = _run_command(cmd, timeout=10, container=container)
    if result.returncode != 0:
        logger.error("[DHCP] dhclient failed: %s", result.stderr)
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "client",
                "dhcp_state": "Failed",
                "dhcp_running": False,
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": result.stderr.strip()}

    # Poll for IPv4 assignment
    ip_info = None
    poll_seconds = lease_timeout if lease_timeout else timeout
    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        ip_info = _parse_ipv4(interface, container=container)
        if ip_info:
            break
        time.sleep(1)

    if not ip_info:
        logger.error("[DHCP] Timed out waiting for lease on %s", interface)
        # Attempt to release client
        try:
            _run_command(
                ["dhclient", "-4", "-r", "-pf", pidfile, interface],
                timeout=5,
            )
        except Exception:
            pass
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "client",
                "dhcp_state": "Timeout",
                "dhcp_running": False,
                "dhcp_lease_subnet": "",
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": "Lease timeout"}

    gateway = _parse_gateway(interface, container=container) or ""
    lease_subnet = ""
    try:
        ip_val = ip_info.get("ip")
        mask_val = ip_info.get("mask")
        if ip_val and mask_val:
            lease_subnet = str(ipaddress.IPv4Interface(f"{ip_val}/{mask_val}").network)
    except Exception as exc:
        logger.debug("[DHCP] Failed to derive lease subnet for %s: %s", interface, exc)
    lease_info = {
        "dhcp_mode": "client",
        "dhcp_state": "Leased",
        "dhcp_running": True,
        "dhcp_lease_ip": ip_info.get("ip", ""),
        "dhcp_lease_mask": ip_info.get("mask", ""),
        "dhcp_lease_gateway": gateway,
        "dhcp_lease_server": "",
        "dhcp_lease_expires": None,
        "dhcp_lease_subnet": lease_subnet,
        "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        # Update primary addressing fields so UI sees live address
        "ipv4_address": f"{ip_info.get('ip')}/{ip_info.get('mask')}",
        "ipv4_mask": ip_info.get("mask"),
        "ipv4_gateway": gateway,
    }
    _update_device_db(device_db, device_id, lease_info)
    return {"success": True, "ip": ip_info.get("ip"), "mask": ip_info.get("mask"), "gateway": gateway}


def stop_dhcp_client(device_db, device_id: str, interface: str, container=None) -> Dict:
    """Stop a running DHCP client on the interface."""
    pidfile = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}.pid")
    try:
        _run_command(["dhclient", "-4", "-r", "-pf", pidfile, interface], timeout=5, container=container)
    except Exception as exc:
        logger.debug("[DHCP] dhclient release error: %s", exc)
    _flush_ipv4(interface, container=container)
    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_state": "Stopped",
            "dhcp_running": False,
            "dhcp_lease_ip": "",
            "dhcp_lease_mask": "",
            "dhcp_lease_gateway": "",
            "dhcp_lease_server": "",
            "dhcp_lease_expires": None,
            "dhcp_lease_subnet": "",
            "ipv4_address": "",
            "ipv4_mask": "",
            "ipv4_gateway": "",
            "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}


def start_dhcp_server(
    device_db,
    device_id: str,
    interface: str,
    dhcp_config: Dict,
    container=None,
) -> Dict:
    """Start a dnsmasq DHCP server bound to interface."""
    # Verify interface exists before proceeding
    if not _verify_interface_exists(interface, container=container):
        error_msg = f"Interface {interface} not found in container/host. Cannot start DHCP server."
        logger.error(f"[DHCP] {error_msg}")
        return {"success": False, "error": error_msg}
    
    _ensure_paths(container=container)
    pool_start = dhcp_config.get("pool_start")
    pool_end = dhcp_config.get("pool_end")
    gateway = dhcp_config.get("gateway", "")
    lease_hours = int(dhcp_config.get("lease_time", dhcp_config.get("lease_hours", 24)))
    lease_seconds = max(60, lease_hours * 3600)
    additional_pools = _normalize_additional_pools(dhcp_config.get("additional_pools"))
    dhcp_config["additional_pools"] = additional_pools

    if not pool_start or not pool_end:
        if not additional_pools:
            return {"success": False, "error": "Pool start/end required for DHCP server"}

    pidfile = os.path.join(DNSMASQ_PID_DIR, f"dnsmasq-{interface}.pid")
    leasefile = os.path.join(DNSMASQ_LEASE_DIR, f"dnsmasq-{interface}.leases")
    conffile = os.path.join(DNSMASQ_CONF_DIR, f"ostg-{interface}.conf")
    logfile = os.path.join(DNSMASQ_LOG_DIR, f"dnsmasq-{interface}.log")

    # Write config file
    config_lines = [
        f"interface={interface}",
        "bind-interfaces",
        "dhcp-authoritative",
        f"dhcp-leasefile={leasefile}",
        f"pid-file={pidfile}",
        f"log-facility={logfile}",
    ]
    if pool_start and pool_end:
        config_lines.append(f"dhcp-range={pool_start},{pool_end},{lease_seconds}s")
    for pool in additional_pools:
        extra_start = pool.get("pool_start")
        extra_end = pool.get("pool_end")
        if extra_start and extra_end:
            config_lines.append(f"dhcp-range={extra_start},{extra_end},{lease_seconds}s")
    if gateway:
        config_lines.append(f"dhcp-option=3,{gateway}")  # option 3 = router
    try:
        if container:
            config_payload = "\n".join(config_lines) + "\n"
            _run_command(
                ["/bin/sh", "-c", f"cat <<'EOF' > {conffile}\n{config_payload}EOF"],
                container=container,
                timeout=5,
            )
        else:
            with open(conffile, "w") as fh:
                fh.write("\n".join(config_lines) + "\n")
    except Exception as exc:
        logger.error("[DHCP] Failed to write dnsmasq config %s: %s", conffile, exc)
        return {"success": False, "error": str(exc)}

    # Stop existing dnsmasq if running
    try:
        if container:
            pid_read = _run_command(
                ["/bin/sh", "-c", f"if [ -f {pidfile} ]; then cat {pidfile}; fi"],
                container=container,
                timeout=5,
            ).stdout.strip()
            if pid_read:
                _run_command(["kill", pid_read], timeout=5, container=container)
        else:
            if os.path.exists(pidfile):
                with open(pidfile, "r") as fh:
                    pid = fh.read().strip()
                    if pid:
                        _run_command(["kill", pid], timeout=5)
    except Exception as exc:
        logger.debug("[DHCP] Failed to stop existing dnsmasq: %s", exc)

    cmd = [
        "dnsmasq",
        f"--conf-file={conffile}",
    ]
    try:
        result = _run_command(cmd, timeout=10, container=container)
        if result.returncode != 0:
            logger.error("[DHCP] dnsmasq failed: %s", result.stderr)
            return {"success": False, "error": result.stderr.strip()}
    except Exception as exc:
        logger.error("[DHCP] dnsmasq launch error: %s", exc)
        return {"success": False, "error": str(exc)}

    pool_networks = _collect_pool_networks(pool_start, pool_end, additional_pools)
    pool_networks_unique = []
    pool_seen = set()
    for net in pool_networks or []:
        if not net:
            continue
        key = str(net)
        if key in pool_seen:
            continue
        pool_seen.add(key)
        pool_networks_unique.append(net)

    gateway_routes = _collect_gateway_routes(dhcp_config, additional_pools)
    route_networks = []
    route_seen = set()
    for net in (pool_networks_unique + gateway_routes):
        if not net:
            continue
        key = str(net)
        if key in route_seen:
            continue
        route_seen.add(key)
        route_networks.append(net)

    # Add static routes for gateway_routes (always create these, even without gateway)
    if gateway_routes:
        # First, ensure gateway is reachable on the interface (prevents Linux from adding it to loopback)
        if gateway and interface:
            try:
                # Add host route to gateway on the interface to make it directly reachable
                gateway_host_route = ["ip", "route", "replace", f"{gateway}/32", "dev", interface]
                _run_command(gateway_host_route, timeout=5, container=container)
                logger.debug("[DHCP] Added host route to gateway %s on %s", gateway, interface)
            except Exception as gateway_route_exc:
                logger.debug("[DHCP] Could not add host route to gateway (may already exist): %s", gateway_route_exc)
        
        for net in gateway_routes:
            try:
                if gateway:
                    route_cmd = ["ip", "route", "replace", str(net), "via", gateway]
                else:
                    route_cmd = ["ip", "route", "replace", str(net)]
                if interface:
                    route_cmd.extend(["dev", interface])
                _run_command(route_cmd, timeout=5, container=container)
                logger.info(
                    "[DHCP] Added gateway route %s%s%s",
                    str(net),
                    f" via {gateway}" if gateway else "",
                    f" dev {interface}" if interface else "",
                )
            except Exception as route_exc:
                logger.warning("[DHCP] Failed to add gateway route %s: %s", net, route_exc)

    # Add static routes toward client pool networks if gateway is specified
    if gateway and pool_networks_unique:
        for net in pool_networks_unique:
            try:
                route_cmd = ["ip", "route", "replace", str(net), "via", gateway]
                if interface:
                    route_cmd.extend(["dev", interface])
                _run_command(route_cmd, timeout=5, container=container)
                logger.info(
                    "[DHCP] Added static route %s via %s%s",
                    net,
                    gateway,
                    f" dev {interface}" if interface else "",
                )
            except Exception as route_exc:
                logger.warning("[DHCP] Failed to add static route %s via %s: %s", net, gateway, route_exc)

    # Persist normalized pool information in the database for visibility
    try:
        config_for_db = dict(dhcp_config)
        if pool_start and pool_end:
            config_for_db.setdefault("pool_range", f"{pool_start}-{pool_end}")
        if pool_networks_unique:
            config_for_db["pool_networks"] = [str(net) for net in pool_networks_unique]
        if gateway_routes:
            config_for_db["gateway_route_normalized"] = [str(net) for net in gateway_routes]
        if dhcp_config.get("pool_name"):
            config_for_db["pool_name"] = dhcp_config.get("pool_name")
        if dhcp_config.get("pool_names"):
            config_for_db["pool_names"] = dhcp_config.get("pool_names")
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_config": config_for_db,
            },
        )
    except Exception as exc:
        logger.debug("[DHCP] Unable to persist DHCP pool metadata for %s: %s", device_id, exc)

    lease_subnet = ""
    lease_sources = [str(net) for net in route_networks] or config_for_db.get("pool_networks", [])
    if lease_sources:
        seen_subnets = []
        seen_keys = set()
        for subnet in lease_sources:
            if subnet in seen_keys:
                continue
            seen_keys.add(subnet)
            seen_subnets.append(subnet)
        lease_subnet = ", ".join(seen_subnets)
    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_mode": "server",
            "dhcp_state": "Server Running",
            "dhcp_running": True,
            "dhcp_lease_ip": "",
            "dhcp_lease_mask": "",
            "dhcp_lease_gateway": gateway,
            "dhcp_lease_server": "",
            "dhcp_lease_expires": None,
            "dhcp_lease_subnet": lease_subnet,
            "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}


def stop_dhcp_server(device_db, device_id: str, interface: str, container=None) -> Dict:
    """Stop dnsmasq instance bound to interface."""
    pidfile = os.path.join(DNSMASQ_PID_DIR, f"dnsmasq-{interface}.pid")
    conffile = os.path.join(DNSMASQ_CONF_DIR, f"ostg-{interface}.conf")
    gateway = ""
    networks = None
    extra_routes = None
    try:
        device = device_db.get_device(device_id) if device_db else None
        if device:
            dhcp_cfg = device.get("dhcp_config") or {}
            if isinstance(dhcp_cfg, str):
                try:
                    dhcp_cfg = json.loads(dhcp_cfg) if dhcp_cfg else {}
                except Exception:
                    dhcp_cfg = {}
            gateway = dhcp_cfg.get("gateway", "")
            
            # Try to get routes from stored metadata first (before pools were cleared)
            stored_pool_networks = dhcp_cfg.get("pool_networks") or []
            stored_gateway_routes = dhcp_cfg.get("gateway_route_normalized") or []
            
            # If stored metadata exists, use it
            if stored_pool_networks or stored_gateway_routes:
                networks = []
                for net_str in stored_pool_networks:
                    try:
                        from ipaddress import IPv4Network
                        networks.append(IPv4Network(net_str))
                    except Exception:
                        pass
                for net_str in stored_gateway_routes:
                    try:
                        from ipaddress import IPv4Network
                        net = IPv4Network(net_str)
                        if net not in networks:
                            networks.append(net)
                    except Exception:
                        pass
            else:
                # Fallback to deriving from current config
                additional_pools = _normalize_additional_pools(dhcp_cfg.get("additional_pools"))
                networks = _collect_pool_networks(
                    dhcp_cfg.get("pool_start"), dhcp_cfg.get("pool_end"), additional_pools
                ) or []
                extra_routes = _collect_gateway_routes(dhcp_cfg, additional_pools)
                if extra_routes:
                    existing = {str(net) for net in networks}
                    for extra in extra_routes:
                        if str(extra) not in existing:
                            networks.append(extra)
    except Exception as exc:
        logger.debug("[DHCP] Failed to derive routes for cleanup: %s", exc)
    try:
        if container:
            # Try to kill dnsmasq by PID file first
            pid_read = _run_command(
                ["/bin/sh", "-c", f"if [ -f {pidfile} ]; then cat {pidfile}; fi"],
                container=container,
                timeout=5,
            ).stdout.strip()
            if pid_read:
                _run_command(["kill", pid_read], timeout=5, container=container)
            # Also try to kill any dnsmasq process on this interface
            _run_command(
                ["/bin/sh", "-c", f"pkill -f 'dnsmasq.*{interface}' || true"],
                container=container,
                timeout=5,
            )
        else:
            if os.path.exists(pidfile):
                with open(pidfile, "r") as fh:
                    pid = fh.read().strip()
                    if pid:
                        _run_command(["kill", pid], timeout=5)
    except Exception as exc:
        logger.debug("[DHCP] Failed to stop dnsmasq: %s", exc)
    try:
        if container:
            # Remove config file
            _run_command(["rm", "-f", conffile], container=container, timeout=5)
            # Also remove from dnsmasq.d directory if it exists there
            _run_command(
                ["/bin/sh", "-c", f"rm -f /etc/dnsmasq.d/ostg-{interface}.conf || true"],
                container=container,
                timeout=5,
            )
        else:
            if os.path.exists(conffile):
                os.remove(conffile)
    except Exception as exc:
        logger.debug("[DHCP] Failed to remove dnsmasq config: %s", exc)

    # Remove routes from container (regardless of gateway, since gateway_routes may not have gateway)
    if networks and container:
        for net in networks:
            try:
                # Try to remove route with gateway first (if gateway exists)
                if gateway:
                    _run_command(
                        ["ip", "route", "del", str(net), "via", gateway],
                        timeout=5,
                        container=container,
                    )
                # Also try without gateway (for routes created without gateway)
                _run_command(
                    ["ip", "route", "del", str(net)],
                    timeout=5,
                    container=container,
                )
                logger.info("[DHCP] Removed static route %s from DHCP container", net)
            except Exception as route_exc:
                # Try alternative route deletion (route might have been created with dev interface)
                try:
                    interface_from_cfg = dhcp_cfg.get("interface") if device else None
                    if interface_from_cfg:
                        if gateway:
                            _run_command(
                                ["ip", "route", "del", str(net), "via", gateway, "dev", interface_from_cfg],
                                timeout=5,
                                container=container,
                            )
                        _run_command(
                            ["ip", "route", "del", str(net), "dev", interface_from_cfg],
                            timeout=5,
                            container=container,
                        )
                        logger.info("[DHCP] Removed static route %s (with dev) from DHCP container", net)
                except Exception:
                    logger.debug("[DHCP] Failed to remove static route %s: %s", net, route_exc)

    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_state": "Stopped",
            "dhcp_running": False,
            "dhcp_lease_subnet": "",
            "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}


def stop_dhcp_services(
    device_db,
    device_id: str,
    interface: str,
    dhcp_mode: str,
    remove_container: bool = False,
) -> Dict:
    """Stop DHCP services based on mode."""
    container = _get_dhcp_container(device_id, mode=dhcp_mode)
    result: Dict[str, Optional[str]]
    if dhcp_mode == "server":
        result = stop_dhcp_server(device_db, device_id, interface, container=container)
    elif dhcp_mode == "client":
        result = stop_dhcp_client(device_db, device_id, interface, container=container)
    else:
        result = {"success": False, "error": f"Unsupported DHCP mode '{dhcp_mode}'"}

    # Stop container after services have been halted
    if container:
        _stop_dhcp_container(device_id, mode=dhcp_mode, remove=remove_container)
    elif remove_container:
        _stop_dhcp_container(device_id, mode=dhcp_mode, remove=True)

    return result


def ensure_dhcp_services(
    device_db,
    device_id: str,
    interface: str,
    dhcp_config: Optional[Dict],
    container=None,
    force_client_restart: bool = False,
) -> Dict:
    """Ensure DHCP services (client/server) are running as requested.
    
    Note: For DHCP server mode, this always creates a separate DHCP container,
    even if a container is passed. This allows DHCP server devices to have both:
    - FRR container for routing protocols (BGP, OSPF, ISIS)
    - Separate DHCP container for DHCP server functionality
    """
    if not dhcp_config:
        return {"success": False, "error": "No DHCP configuration provided"}
    mode = (dhcp_config.get("mode") or "").lower()
    
    # For server mode, always create a separate DHCP container (don't use passed container)
    # This allows DHCP server devices to have both FRR and DHCP containers
    # For client mode, use passed container if available, otherwise create one
    managed_container = container
    
    # Server mode: always create separate DHCP container (ignore passed container)
    if mode == "server":
        logger.info(f"[DHCP] Server mode detected for device {device_id}, creating separate DHCP container")
        managed_container = _ensure_dhcp_container(device_id, mode=mode)
        if managed_container is None:
            error_msg = (
                f"Failed to create/start DHCP container for server mode device {device_id}. "
                f"Please check: 1) Docker daemon is running, 2) Docker image '{DHCP_DOCKER_IMAGE}' exists, "
                f"3) Check server logs for detailed error messages."
            )
            logger.error(f"[DHCP] {error_msg}")
            return {"success": False, "error": error_msg}
        logger.info(f"[DHCP] Successfully created/retrieved DHCP container {managed_container.name} for server mode device {device_id}")
        return start_dhcp_server(
            device_db,
            device_id,
            interface,
            dhcp_config,
            container=managed_container,
        )
    
    # Client mode: use passed container if available, otherwise create one
    if mode != "client":
        return {"success": False, "error": f"Unsupported DHCP mode '{mode}'"}
    
    # At this point, mode must be "client" (we validated above)
    if managed_container is None:
        managed_container = _ensure_dhcp_container(device_id, mode=mode)
    if managed_container is None:
        error_msg = (
            f"Failed to create/start DHCP container for client mode device {device_id}. "
            f"Please check: 1) Docker daemon is running, 2) Docker image '{DHCP_DOCKER_IMAGE}' exists, "
            f"3) Check server logs for detailed error messages."
        )
        logger.error(f"[DHCP] {error_msg}")
        return {"success": False, "error": error_msg}
    
    # Client mode logic
    if not force_client_restart:
        ip_info = _parse_ipv4(interface, container=managed_container)
        if ip_info and ip_info.get("ip"):
            existing_device = device_db.get_device(device_id) if device_db else None
            existing_state = (existing_device or {}).get("dhcp_state")
            existing_ip = ((existing_device or {}).get("dhcp_lease_ip") or "").strip()
            if existing_state == "Leased" and existing_ip == ip_info.get("ip"):
                gateway = _parse_gateway(interface, container=managed_container) or ""
                lease_subnet = ""
                try:
                    ip_val = ip_info.get("ip")
                    mask_val = ip_info.get("mask")
                    if ip_val and mask_val:
                        lease_subnet = str(ipaddress.IPv4Interface(f"{ip_val}/{mask_val}").network)
                except Exception as exc:
                    logger.debug("[DHCP] Failed to derive lease subnet for %s: %s", interface, exc)
                lease_payload = {
                    "dhcp_mode": "client",
                    "dhcp_state": "Leased",
                    "dhcp_running": True,
                    "dhcp_lease_ip": ip_info.get("ip", ""),
                    "dhcp_lease_mask": ip_info.get("mask", ""),
                    "dhcp_lease_gateway": gateway,
                    "dhcp_lease_server": "",
                    "dhcp_lease_expires": None,
                    "dhcp_lease_subnet": lease_subnet,
                    "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
                    "ipv4_address": f"{ip_info.get('ip')}/{ip_info.get('mask')}" if ip_info.get("ip") and ip_info.get("mask") else ip_info.get("ip", ""),
                    "ipv4_mask": ip_info.get("mask", ""),
                    "ipv4_gateway": gateway,
                }
                _update_device_db(device_db, device_id, lease_payload)
                return {"success": True, "ip": ip_info.get("ip"), "mask": ip_info.get("mask"), "gateway": gateway}
            logger.info(
                "[DHCP] Stale IPv4 address %s detected on %s for device %s; restarting dhclient",
                ip_info.get("ip"),
                interface,
                device_id,
            )
            _flush_ipv4(interface, container=managed_container)
    else:
        _flush_ipv4(interface, container=managed_container)

    return start_dhcp_client(
        device_db,
        device_id,
        interface,
        dhcp_config,
        container=managed_container,
    )

