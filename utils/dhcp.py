"""
DHCP client/server lifecycle helpers for OSTG devices.
"""

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
    client = docker.from_env()
    name = _get_dhcp_container_name(device_id, mode=mode)
    try:
        container = client.containers.get(name)
        container.reload()
        if container.status != "running":
            logger.info("[DHCP] Starting existing DHCP container %s", name)
            container.start()
            time.sleep(2)
        return container
    except NotFound:
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
            return container
        except Exception as exc:
            logger.error("[DHCP] Failed to create DHCP container for device %s: %s", device_id, exc)
            return None
    except Exception as exc:
        logger.error("[DHCP] Error ensuring DHCP container for device %s: %s", device_id, exc)
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
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": "Lease timeout"}

    gateway = _parse_gateway(interface, container=container) or ""
    lease_info = {
        "dhcp_mode": "client",
        "dhcp_state": "Leased",
        "dhcp_running": True,
        "dhcp_lease_ip": ip_info.get("ip", ""),
        "dhcp_lease_mask": ip_info.get("mask", ""),
        "dhcp_lease_gateway": gateway,
        "dhcp_lease_server": "",
        "dhcp_lease_expires": None,
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
    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_state": "Stopped",
            "dhcp_running": False,
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
    _ensure_paths(container=container)
    pool_start = dhcp_config.get("pool_start")
    pool_end = dhcp_config.get("pool_end")
    gateway = dhcp_config.get("gateway", "")
    lease_hours = int(dhcp_config.get("lease_time", dhcp_config.get("lease_hours", 24)))
    lease_seconds = max(60, lease_hours * 3600)

    if not pool_start or not pool_end:
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
        f"dhcp-range={pool_start},{pool_end},{lease_seconds}s",
        f"dhcp-leasefile={leasefile}",
        f"pid-file={pidfile}",
        f"log-facility={logfile}",
    ]
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

    additional_routes = _normalize_routes(dhcp_config.get("gateway_route"))
    base_networks = _derive_networks_from_pool(pool_start, pool_end) or []
    if additional_routes:
        # When explicit routes are provided, honor those and skip the
        # synthesized pool summarization to avoid dozens of host routes.
        route_networks = list(additional_routes)
    else:
        route_networks = list(base_networks)

    # Add static routes toward client pool (and any additional routes) if gateway is specified
    if gateway and route_networks:
        for net in route_networks:
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
        if base_networks:
            config_for_db["pool_networks"] = [str(net) for net in base_networks]
        if additional_routes:
            config_for_db["gateway_route_normalized"] = [str(net) for net in additional_routes]
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_config": config_for_db,
            },
        )
    except Exception as exc:
        logger.debug("[DHCP] Unable to persist DHCP pool metadata for %s: %s", device_id, exc)

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
            gateway = dhcp_cfg.get("gateway", "")
            networks = _derive_networks_from_pool(dhcp_cfg.get("pool_start"), dhcp_cfg.get("pool_end")) or []
            extra_routes = _normalize_routes(dhcp_cfg.get("gateway_route"))
            if extra_routes:
                existing = {str(net) for net in networks}
                for extra in extra_routes:
                    if str(extra) not in existing:
                        networks.append(extra)
    except Exception as exc:
        logger.debug("[DHCP] Failed to derive routes for cleanup: %s", exc)
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
        logger.debug("[DHCP] Failed to stop dnsmasq: %s", exc)
    try:
        if container:
            _run_command(["rm", "-f", conffile], container=container, timeout=5)
        else:
            if os.path.exists(conffile):
                os.remove(conffile)
    except Exception as exc:
        logger.debug("[DHCP] Failed to remove dnsmasq config: %s", exc)

    if gateway and networks and container:
        for net in networks:
            try:
                _run_command(
                    ["ip", "route", "del", str(net)],
                    timeout=5,
                    container=container,
                )
                logger.info("[DHCP] Removed static route %s from DHCP container", net)
            except Exception as route_exc:
                logger.debug("[DHCP] Failed to remove static route %s: %s", net, route_exc)

    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_state": "Stopped",
            "dhcp_running": False,
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
    """Ensure DHCP services (client/server) are running as requested."""
    if not dhcp_config:
        return {"success": False, "error": "No DHCP configuration provided"}
    mode = (dhcp_config.get("mode") or "").lower()
    managed_container = container
    if managed_container is None:
        managed_container = _ensure_dhcp_container(device_id, mode=mode)
    if managed_container is None:
        return {"success": False, "error": "Failed to start DHCP container"}
    if mode == "server":
        return start_dhcp_server(
            device_db,
            device_id,
            interface,
            dhcp_config,
            container=managed_container,
        )
    if mode == "client":
        if not force_client_restart:
            ip_info = _parse_ipv4(interface, container=managed_container)
            if ip_info and ip_info.get("ip"):
                gateway = _parse_gateway(interface, container=managed_container) or ""
                lease_payload = {
                    "dhcp_mode": "client",
                    "dhcp_state": "Leased",
                    "dhcp_running": True,
                    "dhcp_lease_ip": ip_info.get("ip", ""),
                    "dhcp_lease_mask": ip_info.get("mask", ""),
                    "dhcp_lease_gateway": gateway,
                    "dhcp_lease_server": "",
                    "dhcp_lease_expires": None,
                    "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
                    "ipv4_address": f"{ip_info.get('ip')}/{ip_info.get('mask')}" if ip_info.get("ip") and ip_info.get("mask") else ip_info.get("ip", ""),
                    "ipv4_mask": ip_info.get("mask", ""),
                    "ipv4_gateway": gateway,
                }
                _update_device_db(device_db, device_id, lease_payload)
                return {"success": True, "ip": ip_info.get("ip"), "mask": ip_info.get("mask"), "gateway": gateway}

        return start_dhcp_client(
            device_db,
            device_id,
            interface,
            dhcp_config,
            container=managed_container,
        )
    return {"success": False, "error": f"Unsupported DHCP mode '{mode}'"}

