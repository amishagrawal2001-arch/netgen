"""
DHCP client/server lifecycle helpers for OSTG devices.
"""

import json
import logging
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
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
# Auto-resolve the FRR image used for DHCP containers — netgen-frr if
# locally available, ostg-frr as legacy fallback. Same logic as the
# FRRDockerManager so a rebrand-only deployment doesn't break here.
def _resolve_dhcp_image():
    explicit = (
        os.environ.get("NETGEN_DHCP_IMAGE")
        or os.environ.get("OSTG_DHCP_IMAGE")
        or os.environ.get("NETGEN_FRR_IMAGE")
        or os.environ.get("OSTG_FRR_IMAGE")
        or ""
    ).strip()
    if explicit:
        return explicit
    try:
        from utils.frr_docker import _resolve_frr_image
        return _resolve_frr_image()
    except Exception:
        return "ostg-frr:latest"

DHCP_DOCKER_IMAGE = _resolve_dhcp_image()


def _normalize_iface_name(interface: str) -> str:
    """Strip the ``@parent`` display suffix from an interface name.

    v0.5.221: several callers in ``run_tgen_server.py`` (notably
    ``apply_device`` at ~line 5537 and ``start_device`` at ~line
    2645) pass the DISPLAY form ``vlan200@ens2f0np0`` — that's the
    string ``ip link show`` prints for VLAN sub-interfaces, NOT the
    kernel interface name. The kernel form is just ``vlan200``,
    stored in ``iface_name_for_commands`` (see the pair at
    run_tgen_server.py:4375-4379). The display form exceeds
    ``IFNAMSIZ`` (16 bytes including NUL) and any
    ``if_nametoindex()`` lookup returns ``ENODEV`` — so dnsmasq's
    ``bind-interfaces`` fails to attach the raw socket and dnsmasq
    exits immediately after launch (returncode still 0 because the
    parent forked before the child died). Same failure mode for
    ``dhclient <iface>``.

    Fixing every call site would be 5+ edits scattered across
    apply_device / start_device / two /api/dhcp endpoints and would
    leave the entry points brittle to any future caller. Normalize
    at the boundary (``start_dhcp_client`` / ``start_dhcp_server`` /
    ``stop_dhcp_client`` / ``stop_dhcp_server``) so all callers are
    safe regardless of which form they pass. No-op for correctly-
    formed inputs.

    Operator symptom on JNPR-MAC-HWXVX1 2026-08-24: DHCP-server
    device on VLAN 200 showed ``dhcp_state="Server Down"`` in the
    DHCP status table even though ``docker ps`` reported the
    container as ``(healthy)``. The healthcheck (``exit 0``) doesn't
    know whether dnsmasq is alive; the monitor's per-container
    pgrep found no dnsmasq process because the launch had exited on
    ENODEV.
    """
    if not interface:
        return interface
    at_idx = interface.find("@")
    if at_idx > 0:
        return interface[:at_idx]
    return interface


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


def _command_exists(command: str, container=None) -> bool:
    """Return True if the given command exists (optionally inside container)."""
    if not command:
        return False
    try:
        quoted = shlex.quote(command)
        result = _run_command(
            ["/bin/sh", "-c", f"command -v {quoted} >/dev/null 2>&1"],
            timeout=5,
            container=container,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("[DHCP] Command check failed for %s: %s", command, exc)
        return False


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


def _truthy(value) -> bool:
    """Return True if value represents an affirmative boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


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


def _parse_ipv6(interface: str, container=None) -> Optional[list]:
    """Return list of IPv6 address dictionaries present on the interface."""
    try:
        result = _run_command(
            ["ip", "-o", "-6", "addr", "show", "dev", interface],
            timeout=5,
            container=container,
        )
        output = result.stdout.strip()
        if not output:
            return None
        entries = []
        for line in output.splitlines():
            parts = line.split()
            if "inet6" not in parts:
                continue
            idx = parts.index("inet6")
            if idx + 1 >= len(parts):
                continue
            cidr = parts[idx + 1]
            if "/" not in cidr:
                continue
            ip, prefix = cidr.split("/", 1)
            entries.append({"ip": ip, "prefix": prefix})
        return entries or None
    except Exception as exc:
        logger.debug("[DHCP] Failed to parse IPv6 for %s: %s", interface, exc)
    return None


def _parse_gateway(interface: str, container=None, device_id: Optional[str] = None) -> Optional[str]:
    """Return default gateway for interface if present.

    Checks the main routing table first, then the device's VRF table
    (if the device has been provisioned into one). Without the VRF
    fallback, this would silently return None on any device whose
    dhclient-installed default route has already been migrated into
    the per-device VRF table (see _migrate_dhcp_route_to_vrf), even
    though the gateway is perfectly reachable through the VRF.
    """
    def _scan(extra_args):
        try:
            result = _run_command(
                ["ip", "route", "show"] + extra_args + ["dev", interface],
                timeout=5, container=container,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("default via"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return parts[2]
        except Exception as exc:
            logger.debug("[DHCP] route-show failed (%s): %s", " ".join(extra_args) or "main", exc)
        return None

    gw = _scan([])
    if gw:
        return gw

    # Fall back to the device's VRF table when available.
    if device_id:
        try:
            from utils.frr_docker import FRRDockerManager
            vrf_name = FRRDockerManager().vrf_name_for_device(device_id)
            if vrf_name:
                check = subprocess.run(
                    ["ip", "-o", "link", "show", vrf_name],
                    capture_output=True, text=True, timeout=2,
                )
                if check.returncode == 0 and (check.stdout or "").strip():
                    return _scan(["vrf", vrf_name])
        except Exception as exc:
            logger.debug("[DHCP] VRF gateway lookup failed for %s: %s", device_id, exc)
    return None


def _migrate_dhcp_route_to_vrf(
    device_id: str,
    interface: str,
    gateway: str,
    family: str = "ipv4",
    container=None,
) -> bool:
    """Move dhclient's default route from main table → the device's VRF.

    Why this exists: dhclient runs in the host netns and doesn't know
    anything about Linux VRFs. When it gets a lease and installs the
    default route (`ip route add default via <gw> dev <iface>`), that
    route lands in the *main* routing table, even when <iface> is the
    slave of vrf-<device_id>. Sockets that bind to the VRF then look
    up routes in the VRF's table and find nothing → the device's
    container can't reach its gateway.

    This helper:
      1. Resolves the device's VRF name (only if one exists).
      2. Adds the default route to the VRF's table.
      3. Removes the duplicate from main.

    Best-effort — failures are logged at debug, not raised; legacy
    single-device deployments (no VRF) early-return harmlessly.
    """
    if not device_id or not interface or not gateway:
        return False
    try:
        from utils.frr_docker import FRRDockerManager
        vrf_name = FRRDockerManager().vrf_name_for_device(device_id)
        if not vrf_name:
            return False
        check = subprocess.run(
            ["ip", "-o", "link", "show", vrf_name],
            capture_output=True, text=True, timeout=2,
        )
        if check.returncode != 0 or not (check.stdout or "").strip():
            return False  # no VRF on this host
    except Exception as exc:
        logger.debug("[DHCP VRF] could not look up VRF for %s: %s", device_id, exc)
        return False

    ip_flag = "-6" if family == "ipv6" else "-4"
    # Add default to the VRF table (idempotent — `replace` instead of
    # `add` so a retry after partial success doesn't error).
    try:
        add_res = _run_command(
            ["ip", ip_flag, "route", "replace", "default",
             "via", gateway, "dev", interface, "vrf", vrf_name],
            timeout=5, container=container,
        )
        if add_res.returncode != 0:
            logger.warning(
                "[DHCP VRF] add default %s to vrf %s via %s failed: %s",
                family, vrf_name, gateway, (add_res.stderr or "").strip(),
            )
            return False
    except Exception as exc:
        logger.warning("[DHCP VRF] add-to-vrf raised for %s: %s", device_id, exc)
        return False

    # Remove the duplicate from main. Use the exact same shape so
    # iproute2 matches it. Best-effort — silent on absent.
    try:
        _run_command(
            ["ip", ip_flag, "route", "del", "default",
             "via", gateway, "dev", interface],
            timeout=5, container=container,
        )
    except Exception as exc:
        logger.debug("[DHCP VRF] main-table cleanup raised: %s", exc)

    logger.info(
        "[DHCP VRF] device %s: migrated default %s route via %s into %s",
        device_id, family, gateway, vrf_name,
    )
    return True


def _resolve_device_vrf(device_id: str) -> Optional[str]:
    """Return the VRF name for a device if one exists on the host.

    v0.5.218 (audit fix L): mirrors the VRF-lookup half of
    ``_migrate_dhcp_route_to_vrf`` so the server-mode route-install
    path can reuse it without pulling in the client-path migration
    logic. Returns None if no FRR manager is available, the device
    isn't wired to a VRF, or the VRF interface isn't present on the
    host (legacy single-device / no-VRF deployments).
    """
    if not device_id:
        return None
    try:
        from utils.frr_docker import FRRDockerManager
        vrf_name = FRRDockerManager().vrf_name_for_device(device_id)
        if not vrf_name:
            return None
        check = subprocess.run(
            ["ip", "-o", "link", "show", vrf_name],
            capture_output=True, text=True, timeout=2,
        )
        if check.returncode != 0 or not (check.stdout or "").strip():
            return None
        return vrf_name
    except Exception as exc:
        logger.debug(
            "[DHCP VRF] could not look up VRF for device %s: %s",
            device_id, exc,
        )
        return None


def _add_route_and_vrf_copy(
    net: str,
    *,
    gateway: str = "",
    interface: str = "",
    family: str = "ipv4",
    vrf_name: Optional[str] = None,
    container=None,
    log_prefix: str = "[DHCP]",
    label: str = "route",
) -> None:
    """Install a route into the main table, and mirror it into the
    device's VRF table when a VRF exists.

    v0.5.218 (audit fix L): the server-mode DHCP path used to only
    run ``ip route replace`` against host tables. When the DHCP
    server's interface is enslaved to a per-device VRF
    (``vrf-<device_id>``), routes in the main table are invisible to
    VRF-bound sockets and dnsmasq clients on the far side never see
    the return traffic. The client path handles this via
    ``_migrate_dhcp_route_to_vrf``; this helper is the server-path
    equivalent that gets called for each pool + gateway + static
    route so both the main table AND the VRF table hold the route.
    Legacy no-VRF deployments early-return harmlessly (vrf_name is
    None) and only the main-table route is installed — mirrors the
    pre-fix behaviour.
    """
    ip_flag = "-6" if family == "ipv6" else "-4"
    # Main table (unchanged pre-fix behaviour).
    try:
        cmd = ["ip", ip_flag, "route", "replace", str(net)]
        if gateway:
            cmd.extend(["via", gateway])
        if interface:
            cmd.extend(["dev", interface])
        _run_command(cmd, timeout=5, container=container)
        logger.info(
            "%s Added %s %s%s%s",
            log_prefix, label, str(net),
            f" via {gateway}" if gateway else "",
            f" dev {interface}" if interface else "",
        )
    except Exception as route_exc:
        logger.warning(
            "%s Failed to add %s %s: %s",
            log_prefix, label, str(net), route_exc,
        )

    # VRF-scoped mirror (v0.5.218) — only when the device sits in a VRF.
    if not vrf_name:
        return
    try:
        vrf_cmd = ["ip", ip_flag, "route", "replace", str(net)]
        if gateway:
            vrf_cmd.extend(["via", gateway])
        if interface:
            vrf_cmd.extend(["dev", interface])
        vrf_cmd.extend(["vrf", vrf_name])
        _run_command(vrf_cmd, timeout=5, container=container)
        logger.info(
            "%s Added %s %s in vrf %s%s%s",
            log_prefix, label, str(net), vrf_name,
            f" via {gateway}" if gateway else "",
            f" dev {interface}" if interface else "",
        )
    except Exception as vrf_exc:
        logger.warning(
            "%s Failed to add %s %s to vrf %s: %s",
            log_prefix, label, str(net), vrf_name, vrf_exc,
        )


def _remove_route_and_vrf_copy(
    net: str,
    *,
    gateway: str = "",
    interface: str = "",
    family: str = "ipv4",
    vrf_name: Optional[str] = None,
    container=None,
    log_prefix: str = "[DHCP]",
    label: str = "route",
) -> List[str]:
    """Remove a route from the main table AND the device's VRF mirror.

    v0.5.219 (audit fix C1): the v0.5.218 ``_add_route_and_vrf_copy``
    installed every server-mode DHCP route into BOTH the main table
    and the per-device VRF table. ``stop_dhcp_server`` was only
    calling ``ip route del`` against the main table — so on device
    Remove or Stop-then-Start with a different pool, the VRF-side
    copy of the old subnet stayed behind and dnsmasq clients on the
    far side kept seeing return traffic land on the wrong VRF next.

    This helper is the removal counterpart to
    ``_add_route_and_vrf_copy``: it issues ``ip route del`` in both
    the ``<net>`` and ``<net> via <gw>`` forms against the main
    table (mirroring the two shapes that stop_dhcp_server had to
    try pre-fix, because the add path uses either form depending on
    whether a gateway was supplied), then mirrors the same delete
    into the VRF table when one exists. Legacy no-VRF deployments
    pass vrf_name=None and only the main-table cleanup runs, so
    behaviour is identical to pre-fix for them.

    Returns a list of human-readable failure strings (one per
    subcommand whose exception was not "no such route"). Callers
    that thread failure info back to the operator (via
    ``stop_dhcp_server``'s ``failures`` accumulator) can append
    directly.
    """
    ip_flag = "-6" if family == "ipv6" else "-4"
    failures: List[str] = []

    def _try_del(cmd_extra):
        cmd = ["ip", ip_flag, "route", "del", str(net)] + cmd_extra
        try:
            _run_command(cmd, timeout=5, container=container)
        except Exception as exc:
            # Bubble up so the caller can decide — but only the
            # "with gateway" try-then-fall-back-to-bare path treats
            # a failure as fatal; the bare form is the safety net.
            raise exc

    # Main table: try `<net> via <gw>` first if we have a gateway,
    # then fall back to bare `<net>` (mirrors the pre-fix add path
    # which switched shapes based on whether a gateway was set).
    if gateway:
        try:
            _try_del(["via", gateway])
            logger.info(
                "%s Removed %s %s via %s",
                log_prefix, label, str(net), gateway,
            )
        except Exception as exc:
            # Silently fall through — bare form below.
            logger.debug(
                "%s Failed to remove %s %s via %s (falling through): %s",
                log_prefix, label, str(net), gateway, exc,
            )
    try:
        _try_del([])
        logger.info(
            "%s Removed %s %s (bare)", log_prefix, label, str(net),
        )
    except Exception as exc:
        # Try the `dev <iface>` form as a last resort — mirrors
        # the pre-fix "alternative route deletion" fallback that
        # stop_dhcp_server did for routes created with dev
        # interface.
        removed_with_dev = False
        if interface:
            try:
                _try_del(["dev", interface])
                logger.info(
                    "%s Removed %s %s dev %s",
                    log_prefix, label, str(net), interface,
                )
                removed_with_dev = True
            except Exception as dev_exc:
                logger.debug(
                    "%s Also failed with dev %s: %s",
                    log_prefix, str(net), interface, dev_exc,
                )
        if not removed_with_dev:
            logger.warning(
                "%s Failed to remove %s %s: %s",
                log_prefix, label, str(net), exc,
            )
            failures.append(f"remove {family} {label} {net}: {exc}")

    # VRF-scoped mirror (v0.5.219) — only when the device sits in a VRF.
    if not vrf_name:
        return failures

    def _try_del_vrf(cmd_extra):
        cmd = ["ip", ip_flag, "route", "del", str(net)] + cmd_extra + ["vrf", vrf_name]
        try:
            _run_command(cmd, timeout=5, container=container)
        except Exception as exc:
            raise exc

    if gateway:
        try:
            _try_del_vrf(["via", gateway])
            logger.info(
                "%s Removed %s %s via %s from vrf %s",
                log_prefix, label, str(net), gateway, vrf_name,
            )
        except Exception as exc:
            logger.debug(
                "%s Failed to remove %s %s via %s from vrf %s (falling through): %s",
                log_prefix, label, str(net), gateway, vrf_name, exc,
            )
    try:
        _try_del_vrf([])
        logger.info(
            "%s Removed %s %s from vrf %s",
            log_prefix, label, str(net), vrf_name,
        )
    except Exception as exc:
        removed_with_dev = False
        if interface:
            try:
                _try_del_vrf(["dev", interface])
                logger.info(
                    "%s Removed %s %s dev %s from vrf %s",
                    log_prefix, label, str(net), interface, vrf_name,
                )
                removed_with_dev = True
            except Exception as dev_exc:
                logger.debug(
                    "%s Also failed with dev %s in vrf %s: %s",
                    log_prefix, str(net), interface, vrf_name, dev_exc,
                )
        if not removed_with_dev:
            logger.warning(
                "%s Failed to remove %s %s from vrf %s: %s",
                log_prefix, label, str(net), vrf_name, exc,
            )
            failures.append(
                f"remove {family} {label} {net} vrf {vrf_name}: {exc}"
            )

    return failures


def _iface_has_ipv4_in_subnet(interface: str, subnet, container=None) -> bool:
    """True when `interface` has any IPv4 address that falls inside `subnet`."""
    try:
        result = _run_command(
            ["ip", "-o", "-4", "addr", "show", "dev", interface],
            timeout=5, container=container,
        )
        if result.returncode != 0:
            return False
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if "inet" not in parts:
                continue
            idx = parts.index("inet")
            if idx + 1 >= len(parts):
                continue
            cidr = parts[idx + 1]
            try:
                addr = ipaddress.IPv4Interface(cidr)
                if addr.ip in ipaddress.IPv4Network(subnet, strict=False):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _ensure_ipv4_address(
    interface: str,
    pool_start: str,
    pool_end: str,
    gateway: str = "",
    ipv4_mask: str = "",
    container=None,
) -> Optional[str]:
    """Ensure the interface has an IPv4 address in the pool's subnet.

    v0.5.222: pre-fix ``start_dhcp_server`` had ``_ensure_ipv6_address``
    for the v6 side but nothing for v4. If the VLAN interface had no
    IPv4 in the pool's subnet, dnsmasq's launch failed with "no
    interface with matching address" and the operator saw
    ``dhcp_state="Failed"`` with the actual error hidden in server
    logs. Root cause on operator's srv06 setup: DHCP-server device
    added with pool ``192.168.30.10-192.168.30.200`` but no
    ``IPv4`` field set on the device (only the gateway subnet was
    known implicitly via the pool), so ``vlan200`` came up bare.

    Logic:
    - Prefer ``gateway`` when the operator provided one and it falls
      inside the pool subnet — that's the dnsmasq default GW anyway.
    - Otherwise, derive a ``.1`` in the pool's subnet. Not the pool
      range itself (want to leave that free for clients).
    - Skip if the interface already has an IPv4 in the pool subnet
      (idempotent).

    Returns the IPv4 address assigned (or the pre-existing one), or
    None if the caller must fail loudly. Callers should surface the
    None case into ``dhcp_last_error`` so operators see it.
    """
    if not interface or not pool_start or not pool_end:
        return None
    try:
        # Find the SUPERNET (smallest prefix that covers both
        # pool_start and pool_end in one contiguous block).
        # ``summarize_address_range`` returns the minimal set of
        # exact fragments — for 192.168.30.10-200 that's a bunch
        # of /27/28/29 pieces, and .1 wouldn't be inside any of
        # them. What we actually want is the classful/CIDR
        # supernet the operator implicitly means (a /24 for a
        # typical .10-.200 pool). Walk prefix lengths from /32
        # down to /8 and take the first one where both endpoints
        # land in the same subnet.
        start = ipaddress.IPv4Address(pool_start)
        end = ipaddress.IPv4Address(pool_end)
        if ipv4_mask:
            pool_network = ipaddress.IPv4Network(
                f"{pool_start}/{int(ipv4_mask)}", strict=False,
            )
            if end not in pool_network:
                # Operator's mask doesn't actually cover the pool
                # end; fall through to auto-derive so we don't
                # anchor to a bogus subnet.
                pool_network = None
        else:
            pool_network = None
        if pool_network is None:
            pool_network = None
            for prefixlen in range(32, 7, -1):
                candidate = ipaddress.IPv4Network(
                    f"{pool_start}/{prefixlen}", strict=False,
                )
                if end in candidate:
                    pool_network = candidate
                    break
            if pool_network is None:
                return None
    except Exception as exc:
        logger.warning("[DHCP] Cannot derive subnet from pool %s-%s: %s",
                       pool_start, pool_end, exc)
        return None

    # Already have a usable IPv4? Idempotent.
    if _iface_has_ipv4_in_subnet(interface, pool_network, container=container):
        logger.debug("[DHCP] Interface %s already has IPv4 in %s", interface, pool_network)
        return None

    # Pick an address: gateway if it fits, else `.1` of the pool subnet.
    server_ip = ""
    if gateway:
        try:
            if ipaddress.IPv4Address(gateway) in pool_network:
                server_ip = gateway
        except Exception:
            pass
    if not server_ip:
        # v0.5.230 (audit P server-9): pool_network.hosts() is empty
        # on /31 (RFC 3021 point-to-point, 0 usable hosts by the
        # default iterator) and /32 (single host, iterator returns
        # nothing). Pre-fix, `list(...)[0]` raised IndexError which
        # got swallowed by the bare `except Exception: return None`
        # so the operator saw no last_error explaining WHY the
        # server couldn't derive an IP. Fall through to using the
        # first address of the network (or the network address on
        # /32) explicitly, and if the pool truly is that small
        # return None with a clear log line — the caller writes it
        # to dhcp_last_error via _handle_start_failure.
        try:
            hosts_iter = list(pool_network.hosts())
            if hosts_iter:
                server_ip = str(hosts_iter[0])
            elif pool_network.prefixlen == 32:
                # /32 = pool of exactly one host; use it.
                server_ip = str(pool_network.network_address)
            elif pool_network.prefixlen == 31:
                # /31 = two hosts; hosts() returns [] but both
                # addresses are valid endpoints.
                server_ip = str(pool_network.network_address)
            else:
                logger.warning(
                    "[DHCP] Pool network %s has no usable host for the "
                    "server IP — pool is too small (prefix=%d).",
                    pool_network, pool_network.prefixlen,
                )
                return None
        except Exception as exc:
            logger.warning(
                "[DHCP] Could not derive server IP from pool %s: %s",
                pool_network, exc,
            )
            return None

    mask_bits = ipv4_mask or str(pool_network.prefixlen)
    try:
        result = _run_command(
            ["ip", "-4", "addr", "add", f"{server_ip}/{mask_bits}", "dev", interface],
            timeout=5, container=container,
        )
        if result.returncode == 0:
            logger.info("[DHCP] Assigned IPv4 %s/%s to %s for dnsmasq bind",
                        server_ip, mask_bits, interface)
            return server_ip
        # `File exists` = already assigned; treat as success.
        stderr = (result.stderr or "").lower()
        if "file exists" in stderr or "already assigned" in stderr:
            return server_ip
        logger.warning(
            "[DHCP] Failed to assign IPv4 %s/%s to %s: %s",
            server_ip, mask_bits, interface, result.stderr,
        )
    except Exception as exc:
        logger.warning(
            "[DHCP] Exception assigning IPv4 %s/%s to %s: %s",
            server_ip, mask_bits, interface, exc,
        )
    return None


def _ensure_ipv6_address(interface: str, address: str, prefix: str, container=None) -> bool:
    """Ensure the interface has the specified IPv6 address configured."""
    if not interface or not address or prefix is None:
        return False
    existing = _parse_ipv6(interface, container=container) or []
    for entry in existing:
        if entry.get("ip") == address and str(entry.get("prefix")) == str(prefix):
            return True
    try:
        result = _run_command(
            ["ip", "-6", "addr", "add", f"{address}/{prefix}", "dev", interface],
            timeout=5,
            container=container,
        )
        if result.returncode == 0:
            logger.info("[DHCP] Added IPv6 address %s/%s to %s", address, prefix, interface)
            return True
        logger.warning(
            "[DHCP] Failed to add IPv6 address %s/%s to %s: %s",
            address,
            prefix,
            interface,
            result.stderr,
        )
    except Exception as exc:
        logger.warning(
            "[DHCP] Exception while adding IPv6 address %s/%s to %s: %s",
            address,
            prefix,
            interface,
            exc,
        )
    return False


def _remove_ipv6_address(interface: str, address: str, prefix: str, container=None) -> None:
    """Remove an IPv6 address from an interface."""
    if not interface or not address or prefix is None:
        return
    try:
        _run_command(
            ["ip", "-6", "addr", "del", f"{address}/{prefix}", "dev", interface],
            timeout=5,
            container=container,
        )
        logger.info("[DHCP] Removed IPv6 address %s/%s from %s", address, prefix, interface)
    except Exception as exc:
        # v0.5.217 (audit fix D): upgrade debug->warning so operators
        # can see the swallowed failure in server logs. The caller
        # can still ignore it (this helper returns None), but the
        # trail is now visible.
        logger.warning(
            "[DHCP] Failed to remove IPv6 address %s/%s from %s: %s",
            address,
            prefix,
            interface,
            exc,
        )


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
    """Check whether a dhclient process is running for the given interface.

    v0.5.218 (audit fix M): pre-fix used
    ``pgrep -f 'dhclient.*{interface}'`` — an unanchored substring
    match. That returned true for ``dhclient eth10`` when
    ``interface="eth1"``, producing a false-positive "running"
    reading that skewed ``needs_restart`` in the monitor. Fix:
    parse ``pgrep -a -f dhclient`` output ourselves and compare
    each row's argv against the interface name as an *exact*
    whitespace-separated token — no more prefix collisions.
    """
    if not interface:
        return False
    try:
        # -a prints "PID cmd argv..." per matching process.
        result = _run_command(
            ["pgrep", "-a", "-f", "dhclient"],
            timeout=5, container=container,
        )
        if result.returncode not in (0, 1):
            # pgrep returns 1 when there are no matches; anything
            # else is an unexpected error — fall through to False.
            return False
        for line in (result.stdout or "").splitlines():
            # Split off the leading PID, then match argv tokens.
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            argv = parts[1].split()
            # Only match if `interface` appears as a WHOLE token
            # in argv — this rules out ``eth1`` matching ``eth10``
            # and any file-path token that merely contains the name
            # as a substring.
            if interface in argv:
                return True
        return False
    except Exception as exc:
        logger.debug(
            "[DHCP] Failed to determine dhclient status for %s: %s",
            interface, exc,
        )
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
    gateway = _parse_gateway(interface, container=container, device_id=device_id) or ""
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


def _flush_ipv6(interface: str, container=None) -> None:
    """Remove all non-link-local IPv6 addresses from an interface."""
    try:
        # Get all IPv6 addresses on the interface
        result = _run_command(
            ["ip", "-o", "-6", "addr", "show", "dev", interface],
            timeout=5,
            container=container,
        )
        output = result.stdout.strip()
        if output:
            for line in output.splitlines():
                parts = line.split()
                if "inet6" not in parts:
                    continue
                idx = parts.index("inet6")
                if idx + 1 >= len(parts):
                    continue
                cidr = parts[idx + 1]
                if "/" not in cidr:
                    continue
                ip, prefix = cidr.split("/", 1)
                # v0.5.230 (audit P server-11): link-local is
                # fe80::/10, which covers fe80:: through febf::. The
                # pre-fix `startswith("fe80:")` only matched addresses
                # in the fe80::/16 sub-range, missing fe81..febf.
                # Use ipaddress.IPv6Address.is_link_local so the check
                # matches the actual scope the intent describes.
                try:
                    if ipaddress.IPv6Address(ip).is_link_local:
                        continue
                except (ipaddress.AddressValueError, ValueError):
                    continue
                # Remove the address
                try:
                    _run_command(
                        ["ip", "-6", "addr", "del", f"{ip}/{prefix}", "dev", interface],
                        timeout=5,
                        container=container,
                    )
                    logger.debug("[DHCP] Removed IPv6 address %s/%s from %s", ip, prefix, interface)
                except Exception as del_exc:
                    logger.debug("[DHCP] Failed to remove IPv6 address %s/%s: %s", ip, prefix, del_exc)
        logger.debug("[DHCP] Flushed non-link-local IPv6 addresses on %s", interface)
    except Exception as exc:
        logger.debug("[DHCP] Failed to flush IPv6 addresses on %s: %s", interface, exc)


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
            # Image missing — auto-build it from the wheel's Dockerfile.
            # The DHCP container reuses the FRR image (_resolve_dhcp_image
            # → _resolve_frr_image), so building the FRR image covers DHCP
            # too. Previously this just errored "build the image first",
            # which broke DHCP-only deployments that never applied a
            # BGP/OSPF device (the only path that lazily built the image).
            # _build_frr_image_now tags both netgen-frr:latest AND
            # ostg-frr:latest, so DHCP_DOCKER_IMAGE resolves afterward for
            # all non-env-override cases.
            logger.warning(
                "[DHCP] Docker image %s not found — auto-building the FRR/DHCP "
                "image from /opt/netgen/Dockerfile.frr (may take 2-3 minutes)...",
                DHCP_DOCKER_IMAGE,
            )
            built = None
            try:
                from utils.frr_docker import _build_frr_image_now
                built = _build_frr_image_now(client, reason="DHCP container needs the FRR image")
            except Exception as build_exc:
                logger.error("[DHCP] Auto-build failed: %s", build_exc, exc_info=True)
            if not built:
                logger.error(
                    "[DHCP] Image %s not found and auto-build failed. Build manually: "
                    "docker build --network=host -t netgen-frr:latest "
                    "-f /opt/netgen/Dockerfile.frr /opt/netgen",
                    DHCP_DOCKER_IMAGE,
                )
                return None
            # Confirm the resolved name is now present (it is for the
            # netgen-frr / ostg-frr / fallback cases since both tags are
            # written). For an explicit env-var image that we can't build,
            # this will still fail — correctly, since we honour the override.
            try:
                client.images.get(DHCP_DOCKER_IMAGE)
                logger.info("[DHCP] Image %s available after auto-build", DHCP_DOCKER_IMAGE)
            except Exception:
                logger.error(
                    "[DHCP] Auto-build produced %s but the configured DHCP image %s "
                    "is still not resolvable (custom NETGEN_DHCP_IMAGE override?)",
                    built, DHCP_DOCKER_IMAGE,
                )
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
    # v0.5.221: normalize the interface string BEFORE any OS call.
    # Several callers in run_tgen_server.py (`apply_device`,
    # `start_device`) pass the DISPLAY form ``vlan200@ens2f0np0``
    # instead of the kernel form ``vlan200`` (see the pair
    # ``iface_name`` / ``iface_name_for_commands`` at line ~4375-4379
    # of run_tgen_server.py). The display form exceeds Linux
    # IFNAMSIZ (16 bytes including NUL) and ``if_nametoindex()``
    # returns ENODEV — so dhclient/dnsmasq silently fail to bind,
    # exit, and the operator sees ``dhcp_state="Failed"`` (client)
    # or the monitor writes ``dhcp_state="Server Down"`` (server)
    # while ``docker ps`` still shows the DHCP container as
    # ``(healthy)`` (the healthcheck is just ``exit 0``).
    # Fixing every call site would be 5+ edits and would leave the
    # entry-points brittle to new callers — normalise at the
    # boundary instead.
    interface = _normalize_iface_name(interface)
    # v0.5.219 (audit fix C3): explicitly clear dhcp_manual_override
    # here so an operator-initiated Stop->Start doesn't leave the DHCP
    # monitor blind for up to 120s. Pre-fix, ``stop_dhcp_services``
    # stamped ``dhcp_manual_override=True`` at every stop but no start
    # path ever cleared it, so if a subsequent Start silently failed
    # (dhclient exited before lease, etc.) the monitor's 120s guard
    # kept skipping the device instead of noticing and writing
    # dhcp_state="Failed". Writing the clear here — before we even
    # attempt the start — closes that window.
    if device_id:
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_manual_override": False,
                "dhcp_manual_override_time": None,
            },
        )

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
    pidfile_v4 = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}-ipv4.pid")
    leasefile_v4 = os.path.join(DHCLIENT_LEASE_DIR, f"dhclient-{interface}-ipv4.leases")
    pidfile_v6 = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}-ipv6.pid")
    leasefile_v6 = os.path.join(DHCLIENT_LEASE_DIR, f"dhcp6c-{interface}.leases")

    ipv4_enabled = _truthy(dhcp_config.get("ipv4_enabled", True)) if dhcp_config else True
    ipv6_enabled = _truthy(dhcp_config.get("ipv6_enabled", True)) if dhcp_config else True

    ipv4_result = {"success": False, "error": "IPv4 skipped"}
    ipv6_result = {"success": False, "error": "IPv6 skipped"}

    lease_timeout = int(dhcp_config.get("timeout", timeout)) if dhcp_config else timeout

    if ipv4_enabled:
        try:
            _run_command(
                ["dhclient", "-4", "-r", "-pf", pidfile_v4, interface],
                timeout=5,
                container=container,
            )
        except Exception as exc:
            logger.debug("[DHCP] dhclient release error (safe to ignore): %s", exc)

        cmd_v4 = ["dhclient", "-4", "-nw", "-pf", pidfile_v4, "-lf", leasefile_v4]
        if dhcp_config and "timeout" in dhcp_config:
            cmd_v4.extend(["-timeout", str(lease_timeout)])
        cmd_v4.append(interface)

        ipv4_exec = _run_command(cmd_v4, timeout=10, container=container)
        if ipv4_exec.returncode != 0:
            ipv4_result = {"success": False, "error": ipv4_exec.stderr.strip()}
        else:
            ip_info = None
            deadline = time.time() + lease_timeout
            while time.time() < deadline:
                ip_info = _parse_ipv4(interface, container=container)
                if ip_info:
                    break
                time.sleep(1)
            if not ip_info:
                ipv4_result = {"success": False, "error": "Lease timeout"}
            else:
                gateway = _parse_gateway(interface, container=container, device_id=device_id) or ""
                lease_subnet = ""
                try:
                    ip_val = ip_info.get("ip")
                    mask_val = ip_info.get("mask")
                    if ip_val and mask_val:
                        lease_subnet = str(ipaddress.IPv4Interface(f"{ip_val}/{mask_val}").network)
                except Exception as exc:
                    logger.debug("[DHCP] Failed to derive lease subnet for %s: %s", interface, exc)

                # If the device has been provisioned into a Linux VRF,
                # move dhclient's default route out of the main table
                # into the VRF table — see _migrate_dhcp_route_to_vrf
                # for the why.
                if gateway:
                    _migrate_dhcp_route_to_vrf(
                        device_id, interface, gateway,
                        family="ipv4", container=container,
                    )

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
                    "ipv4_address": f"{ip_info.get('ip')}/{ip_info.get('mask')}",
                    "ipv4_mask": ip_info.get("mask"),
                    "ipv4_gateway": gateway,
                }
                _update_device_db(device_db, device_id, lease_info)
                ipv4_result = {"success": True, "ip": ip_info.get("ip"), "mask": ip_info.get("mask"), "gateway": gateway}

    if ipv6_enabled:
        # Flush existing IPv6 addresses (except link-local) to ensure DHCP client actively requests a lease
        _flush_ipv6(interface, container=container)

        lease_deadline = time.time() + lease_timeout
        addr6 = None

        if _command_exists("dhcp6c", container=container):
            # Use wide-DHCPv6 client (dhcp6c) for SLAAC/DHCPv6 PD support
            dhcp6_conf = f"/etc/dhcp/dhcp6c-{interface}.conf"
            dhcp6_cmd = ["dhcp6c", "-c", dhcp6_conf, "-p", pidfile_v6, interface]
            # Write dhcp6c config file
            try:
                dhcp6_conf_content = (
                    'interface {iface} {\n'
                    '    send rapid-commit;\n'
                    '    request domain-name-servers;\n'
                    '    script "/etc/dhcp/dhcp6c-script";\n'
                    '};\n'
                    '\n'
                    'id-assoc pd 0 {\n'
                    '    prefix-interface {iface} {\n'
                    '        sla-id 0;\n'
                    '        sla-len 0;\n'
                    '    };\n'
                    '};\n'
                ).format(iface=interface)
                if container:
                    _run_command(
                        ["/bin/sh", "-c", f"cat <<'EOF' > {dhcp6_conf}\n{dhcp6_conf_content.strip()}\nEOF"],
                        container=container,
                        timeout=5,
                    )
                else:
                    with open(dhcp6_conf, "w") as fh:
                        fh.write(dhcp6_conf_content.strip() + "\n")
            except Exception as exc:
                logger.warning("[DHCP] Failed to write dhcp6c config for %s: %s", interface, exc)

            try:
                _run_command(["pkill", "-f", f"dhcp6c.*{interface}"], timeout=5, container=container)
            except Exception:
                pass

            dhcp6_exec = _run_command(dhcp6_cmd, timeout=10, container=container)
            if dhcp6_exec.returncode != 0:
                ipv6_result = {"success": False, "error": dhcp6_exec.stderr.strip()}
            else:
                addr6 = _parse_ipv6(interface, container=container)
        else:
            logger.info("[DHCP] dhcp6c not found; falling back to dhclient -6 for %s", interface)
            try:
                _run_command(
                    ["dhclient", "-6", "-r", "-pf", pidfile_v6, interface],
                    timeout=5,
                    container=container,
                )
            except Exception as exc:
                logger.debug("[DHCP] dhclient -6 release error (safe to ignore): %s", exc)

            cmd_v6 = ["dhclient", "-6", "-nw", "-pf", pidfile_v6, "-lf", leasefile_v6]
            if dhcp_config and "timeout" in dhcp_config:
                cmd_v6.extend(["-timeout", str(lease_timeout)])
            cmd_v6.append(interface)

            dhcp6_exec = _run_command(cmd_v6, timeout=10, container=container)
            if dhcp6_exec.returncode != 0:
                ipv6_result = {"success": False, "error": dhcp6_exec.stderr.strip()}
            else:
                while time.time() < lease_deadline:
                    parsed = _parse_ipv6(interface, container=container)
                    if parsed:
                        addr6 = parsed
                        break
                    time.sleep(1)

        if not addr6:
            ipv6_result = {"success": False, "error": "IPv6 lease not observed"}
        else:
            ipv6_result = {"success": True, "addresses": addr6}

    success = ipv4_result.get("success") or ipv6_result.get("success")
    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_mode": "client",
            "dhcp_state": "Leased" if success else "Failed",
            "dhcp_running": success,
            "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": success, "ipv4": ipv4_result, "ipv6": ipv6_result}


def stop_dhcp_client(device_db, device_id: str, interface: str, container=None) -> Dict:
    """Stop a running DHCP client on the interface.

    v0.5.218 (audit fix K): pre-fix this only issued
    ``dhclient -4 -r`` and wiped IPv4 DB fields. The matching
    IPv6 daemons (``dhcp6c`` on containers where it's installed,
    or ``dhclient -6`` on the fallback path in start_dhcp_client)
    were never terminated. Symptom: after "Stop DHCP" the DHCPv6
    daemon kept its lease active on the interface indefinitely,
    the IPv6 address stuck around, and the next Start DHCP
    spawned a second daemon that fought the first. Fix: release
    both v4 and v6 dhclient PID files, kill any lingering
    ``dhcp6c`` bound to this interface (anchored pattern —
    ``re.escape`` on the interface name to avoid the "eth1
    matches eth10" collision we track separately in bug M),
    flush non-link-local IPv6 addresses via _flush_ipv6, and
    clear the IPv6-side DB fields (ipv6_address / ipv6_mask /
    ipv6_gateway) alongside the IPv4 ones so the row's IPv6
    columns don't retain the stale lease info.
    """
    # v0.5.221: normalize display form vlan200@ens2f0np0 → vlan200.
    interface = _normalize_iface_name(interface)
    # v0.5.229 (audit B2): pidfile shape mismatch. start_dhcp_client
    # writes `dhclient-{iface}-ipv4.pid` (line 1399); the release
    # here previously read `dhclient-{iface}.pid` (no suffix), so
    # `dhclient -4 -r` couldn't find the running client and
    # silently failed to release the lease. The subsequent
    # _flush_ipv4 yanked the address unilaterally, leaving the
    # server-side lease DB "leased" until natural expiry — a
    # subsequent Start on ANY host would collide until then.
    # Also try the pre-fix path as a fallback for lingering
    # dhclients started before this fix landed.
    pidfile_v4_suffixed = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}-ipv4.pid")
    pidfile_v4_legacy   = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}.pid")
    for _pf in (pidfile_v4_suffixed, pidfile_v4_legacy):
        try:
            _run_command(["dhclient", "-4", "-r", "-pf", _pf, interface], timeout=5, container=container)
        except Exception as exc:
            logger.debug("[DHCP] dhclient -4 release error (pf=%s): %s", _pf, exc)

    # v0.5.218: v6 dhclient release — mirrors the pidfile shape
    # start_dhcp_client uses on the dhclient -6 fallback path.
    pidfile_v6 = os.path.join(DHCLIENT_PID_DIR, f"dhclient-{interface}-ipv6.pid")
    try:
        _run_command(
            ["dhclient", "-6", "-r", "-pf", pidfile_v6, interface],
            timeout=5, container=container,
        )
    except Exception as exc:
        logger.debug("[DHCP] dhclient -6 release error: %s", exc)

    # v0.5.218: kill any wide-DHCPv6 dhcp6c bound to this
    # interface. pkill -f pattern is anchored on the interface
    # name via re.escape (see bug M) — otherwise "eth1" would
    # match "dhcp6c ... eth10".
    try:
        _run_command(
            ["pkill", "-f", f"dhcp6c.*(^|\\s){re.escape(interface)}(\\s|$)"],
            timeout=5, container=container,
        )
    except Exception as exc:
        logger.debug("[DHCP] dhcp6c pkill error (safe to ignore): %s", exc)

    _flush_ipv4(interface, container=container)
    # v0.5.218: also flush non-link-local IPv6 addresses so a
    # stale lease doesn't stick around on the interface.
    _flush_ipv6(interface, container=container)

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
            # v0.5.218 (audit fix K): clear the IPv6 side too —
            # the pre-fix DB write left ipv6_address/mask/gateway
            # holding the stale lease, and the UI kept showing
            # the DHCPv6 address on a "Stopped" row.
            "ipv6_address": "",
            "ipv6_mask": "",
            "ipv6_gateway": "",
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
    # v0.5.221: normalize interface — see start_dhcp_client's
    # equivalent block for the full rationale (display form
    # vlan200@ens2f0np0 exceeds IFNAMSIZ, if_nametoindex fails,
    # dnsmasq silently exits, monitor writes Server Down).
    interface = _normalize_iface_name(interface)
    # v0.5.219 (audit fix C3): mirror the client-path clear — see
    # start_dhcp_client's block for the full rationale. Any explicit
    # server Start supersedes the manual_override guard that
    # stop_dhcp_services stamped, so the monitor can observe the new
    # dnsmasq (or its failure) immediately instead of after 120s.
    if device_id:
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_manual_override": False,
                "dhcp_manual_override_time": None,
            },
        )

    if not _verify_interface_exists(interface, container=container):
        error_msg = f"Interface {interface} not found in container/host. Cannot start DHCP server."
        logger.error(f"[DHCP] {error_msg}")
        # v0.5.217 (audit fix F): mirror start_dhcp_client — write
        # dhcp_state="Failed" + dhcp_running=False before every
        # failure return. Pre-fix, the DB kept the previous
        # "Server Running" reading and the UI showed a green DHCP
        # pill even though dnsmasq never launched.
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "server",
                "dhcp_state": "Failed",
                "dhcp_running": False,
                "dhcp_last_error": error_msg,  # v0.5.222: surface to UI
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": error_msg}

    _ensure_paths(container=container)

    ipv4_enabled = _truthy(dhcp_config.get("ipv4_enabled", True))
    ipv6_enabled = _truthy(dhcp_config.get("ipv6_enabled", False))

    pool_start = dhcp_config.get("pool_start") if ipv4_enabled else None
    pool_end = dhcp_config.get("pool_end") if ipv4_enabled else None
    gateway = dhcp_config.get("gateway", "") if ipv4_enabled else ""

    lease_hours_raw = dhcp_config.get("lease_time", dhcp_config.get("lease_hours", 24))
    try:
        lease_hours = int(lease_hours_raw)
    except (TypeError, ValueError):
        lease_hours = 24
    lease_seconds = max(60, lease_hours * 3600)

    additional_pools = _normalize_additional_pools(
        dhcp_config.get("additional_pools") if ipv4_enabled else []
    )
    dhcp_config["additional_pools"] = additional_pools

    ipv6_pool_start = str(
        dhcp_config.get("ipv6_pool_start")
        or dhcp_config.get("pool_start_v6")
        or ""
    ).strip()
    ipv6_pool_end = str(
        dhcp_config.get("ipv6_pool_end")
        or dhcp_config.get("pool_end_v6")
        or ""
    ).strip()
    ipv6_prefix = str(
        dhcp_config.get("ipv6_prefix")
        or dhcp_config.get("ipv6_prefix_length")
        or dhcp_config.get("ipv6_prefix_len")
        or ""
    ).strip()
    ipv6_server_ip = str(
        dhcp_config.get("ipv6_server_ip")
        or dhcp_config.get("ipv6_server")
        or ""
    ).strip()
    ipv6_gateway = str(dhcp_config.get("ipv6_gateway") or "").strip()
    ipv6_routes_raw = dhcp_config.get("ipv6_gateway_route") or dhcp_config.get("ipv6_gateway_routes")
    ipv6_lease_raw = dhcp_config.get("ipv6_lease_time") or dhcp_config.get("lease_time_v6")
    try:
        ipv6_lease_seconds = int(ipv6_lease_raw) if ipv6_lease_raw is not None else lease_seconds
    except (TypeError, ValueError):
        ipv6_lease_seconds = lease_seconds
    ipv6_lease_seconds = max(60, ipv6_lease_seconds)

    if ipv4_enabled and not (pool_start and pool_end) and not additional_pools:
        ipv4_enabled = False
        logger.info("[DHCP] Disabling IPv4 DHCP for %s due to missing pool range", device_id)

    if ipv6_enabled and (not ipv6_pool_start or not ipv6_pool_end or not ipv6_prefix):
        logger.warning(
            "[DHCP] IPv6 DHCP requested for %s but pool_start/pool_end/prefix missing; disabling IPv6",
            device_id,
        )
        ipv6_enabled = False

    if not ipv4_enabled and not ipv6_enabled:
        # v0.5.223: distinguish "no pool attached" from actual
        # dnsmasq crashes. Pre-fix this branch wrote
        # dhcp_state="Failed" — indistinguishable from a real
        # launch failure (dnsmasq crashed, interface missing,
        # etc.), so operators couldn't tell config-incomplete
        # apart from a real bug. "No Pool" reads correctly:
        # nothing failed, the device just has no pool attached
        # (common after the v0.5.218 Delete-key detach action).
        # v0.5.228: name the visible button ("Attach Pool" in the DHCP
        # subtab toolbar) — pre-fix this named a button label that
        # didn't exist in the UI, and the buttons were icon-only
        # anyway, so operators had no way to find it.
        err = (
            "No DHCP pool attached — click the 'Attach Pool' button in "
            "the DHCP subtab toolbar to attach a named pool, or Edit "
            "the device to set a Pool Start / Pool End range directly."
        )
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "server",
                "dhcp_state": "No Pool",
                "dhcp_running": False,
                "dhcp_last_error": err,  # v0.5.222: surface to UI
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {
            "success": False,
            "error": "DHCP server requires at least one of IPv4 or IPv6 pool to be configured",
        }

    # v0.5.222: ensure the interface actually has an IPv4 address
    # in the pool's subnet before launching dnsmasq. dnsmasq's
    # ``bind-interfaces`` + ``dhcp-range`` requires a matching
    # address on the interface; without one it exits with "no
    # interface with address ..." and start_dhcp_server writes
    # dhcp_state="Failed". Pre-fix this only worked when the
    # operator had explicitly set an IPv4 on the device via Add
    # Device, or when a prior apply had assigned one. Now
    # start_dhcp_server owns the assignment: prefer the operator's
    # gateway (if it fits in the pool subnet), else use the pool
    # subnet's ``.1``.
    if ipv4_enabled and pool_start and pool_end:
        assigned = _ensure_ipv4_address(
            interface, pool_start, pool_end,
            gateway=gateway, ipv4_mask="",
            container=container,
        )
        if assigned:
            logger.info("[DHCP] Server-mode IPv4 anchor on %s: %s",
                        interface, assigned)

    if ipv6_enabled and ipv6_prefix:
        # v0.5.230 (audit P server-10): auto-derive IPv6 server IP
        # from the pool subnet when the operator didn't supply one,
        # matching the IPv4-side v0.5.222 fix. Pre-fix, an IPv6-only
        # pool with no explicit server_ip failed to bind with the
        # same "no interface with matching address" that IPv4 was
        # fixed to avoid.
        _v6_ip = ipv6_server_ip
        if not _v6_ip and ipv6_pool_start:
            try:
                _v6_net = ipaddress.IPv6Network(
                    f"{ipv6_pool_start}/{ipv6_prefix}", strict=False,
                )
                _hosts6 = list(_v6_net.hosts())
                if _hosts6:
                    _v6_ip = str(_hosts6[0])
                else:
                    _v6_ip = str(_v6_net.network_address)
                logger.info(
                    "[DHCP] Derived IPv6 server IP %s from pool %s (no explicit ipv6_server_ip)",
                    _v6_ip, _v6_net,
                )
            except (ipaddress.AddressValueError, ValueError) as _v6_exc:
                logger.warning(
                    "[DHCP] Could not derive IPv6 server IP from pool %s/%s: %s",
                    ipv6_pool_start, ipv6_prefix, _v6_exc,
                )
        if _v6_ip:
            _ensure_ipv6_address(interface, _v6_ip, ipv6_prefix, container=container)

    pidfile = os.path.join(DNSMASQ_PID_DIR, f"dnsmasq-{interface}.pid")
    leasefile = os.path.join(DNSMASQ_LEASE_DIR, f"dnsmasq-{interface}.leases")
    conffile = os.path.join(DNSMASQ_CONF_DIR, f"ostg-{interface}.conf")
    logfile = os.path.join(DNSMASQ_LOG_DIR, f"dnsmasq-{interface}.log")

    config_lines = [
        f"interface={interface}",
        # v0.5.233: skip loopback binding. Even with
        # bind-interfaces + interface=vlan10, dnsmasq's DNS resolver
        # tries to bind to lo's addresses (127.0.0.1 + any other
        # globally-scoped IPs on lo — netgen assigns per-device
        # loopback IPs like 192.255.10.3 to lo). When the whole
        # process is wrapped in `ip vrf exec vrf-<device>` (v0.5.232),
        # those loopback addresses aren't reachable from the VRF's
        # routing table, so bind() returns EADDRNOTAVAIL and dnsmasq
        # exits. Explicitly excluding lo (which we don't want DHCP
        # serving on anyway) sidesteps the whole problem.
        "except-interface=lo",
        "bind-interfaces",
        # v0.5.233: disable the DNS resolver entirely. netgen uses
        # dnsmasq strictly for DHCP; there's no reason for a DNS
        # port to be open per device, and it removes a second source
        # of bind-address confusion.
        "port=0",
        "dhcp-authoritative",
        f"dhcp-leasefile={leasefile}",
        f"pid-file={pidfile}",
        f"log-facility={logfile}",
    ]
    if ipv4_enabled:
        if pool_start and pool_end:
            config_lines.append(f"dhcp-range={pool_start},{pool_end},{lease_seconds}s")
        # v0.5.229 (audit U server-4): honor per-pool lease_time and
        # gateway on additional pools. Pre-fix, both were normalized
        # into the pool dict at line 242-244 and then thrown away
        # here — clients in additional-pool subnets ended up with
        # the primary pool's lease and default gateway, which for a
        # different /24 is the wrong router.
        for pool in additional_pools:
            extra_start = pool.get("pool_start")
            extra_end = pool.get("pool_end")
            if extra_start and extra_end:
                extra_lease = pool.get("lease_time") or lease_seconds
                config_lines.append(f"dhcp-range={extra_start},{extra_end},{extra_lease}s")
                extra_gw = pool.get("gateway")
                if extra_gw:
                    # dnsmasq lets you tag options to a specific range
                    # via a set:tag. Use the pool's numeric endpoint as
                    # the tag suffix so each additional pool gets its
                    # own scoped default gateway advertisement.
                    _tag = f"pool_{extra_start.replace('.', '_')}"
                    config_lines.append(f"dhcp-range=set:{_tag},{extra_start},{extra_end},{extra_lease}s")
                    config_lines.append(f"dhcp-option=tag:{_tag},3,{extra_gw}")
        if gateway:
            config_lines.append("dhcp-option=3," + gateway)

    ipv6_gateway_routes = []
    if ipv6_enabled:
        if "enable-ra" not in config_lines:
            config_lines.append("enable-ra")
        config_lines.append(
            f"dhcp-range={ipv6_pool_start},{ipv6_pool_end},{ipv6_prefix},{ipv6_lease_seconds}s"
        )
        # v0.5.229 (audit U server-3): the second field of ra-param is
        # the RA router lifetime in seconds; RFC 4861 says 0 means
        # "NOT a default router". Pre-fix, every RA told clients not
        # to install a default route via this box regardless of what
        # the operator put in ipv6_gateway. Emit `enable-ra` alone
        # (dnsmasq defaults to 1800s lifetime when ra-param is
        # omitted) unless the operator explicitly opted out via
        # ipv6_gateway="none".
        _v6_gw = (ipv6_gateway or "").strip().lower()
        if _v6_gw == "none":
            # Explicit "do not advertise self as router" — keep the
            # legacy lifetime=0 form so the operator can still get
            # this behavior if they need it.
            config_lines.append(f"ra-param={interface},0,0")
        # else: leave enable-ra with the default 1800s lifetime; the
        # kernel will advertise the interface's link-local as the
        # default router, which is what clients need to install a
        # default route.
        if ipv6_routes_raw:
            if isinstance(ipv6_routes_raw, str):
                ipv6_gateway_routes = [
                    token.strip()
                    for token in ipv6_routes_raw.replace(";", ",").split(",")
                    if token and token.strip()
                ]
            elif isinstance(ipv6_routes_raw, (list, tuple, set)):
                ipv6_gateway_routes = [str(token).strip() for token in ipv6_routes_raw if str(token).strip()]
            else:
                route_token = str(ipv6_routes_raw).strip()
                if route_token:
                    ipv6_gateway_routes = [route_token]

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
        # v0.5.217 (audit fix F): mark as Failed before returning.
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "server",
                "dhcp_state": "Failed",
                "dhcp_running": False,
                "dhcp_last_error": f"Config write failed: {exc}",  # v0.5.222
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": str(exc)}

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

    # v0.5.232: launch dnsmasq inside the device's VRF so bind() can
    # find the pool's server IP on a VRF-scoped interface. Pre-fix,
    # a VRF-scoped vlan interface (vlan<N>@base master vrf-<id>) has
    # its IPv4 addresses globally visible via `ip addr show`, but a
    # bind() call from a process running in the default VRF context
    # returns EADDRNOTAVAIL ("Address not available") because the
    # kernel searches only the default routing table for the source
    # IP. Wrapping dnsmasq in `ip vrf exec <vrf>` puts the process
    # in the VRF's context so bind() succeeds. When the device has
    # no VRF (rare — older devices without VRF isolation), skip the
    # wrapper and launch dnsmasq directly.
    _dnsmasq_vrf = _resolve_device_vrf(device_id)
    if _dnsmasq_vrf:
        cmd = ["ip", "vrf", "exec", _dnsmasq_vrf,
               "dnsmasq", f"--conf-file={conffile}"]
        logger.info(
            "[DHCP] Launching dnsmasq inside VRF %s for device %s",
            _dnsmasq_vrf, device_id,
        )
    else:
        cmd = ["dnsmasq", f"--conf-file={conffile}"]
    try:
        result = _run_command(cmd, timeout=10, container=container)
        if result.returncode != 0:
            logger.error("[DHCP] dnsmasq failed: %s", result.stderr)
            # v0.5.217 (audit fix F): mark as Failed before returning.
            stderr_short = (result.stderr or "").strip()[:400]
            _update_device_db(
                device_db,
                device_id,
                {
                    "dhcp_mode": "server",
                    "dhcp_state": "Failed",
                    "dhcp_running": False,
                    "dhcp_last_error": f"dnsmasq: {stderr_short}",  # v0.5.222
                    "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
                },
            )
            return {"success": False, "error": result.stderr.strip()}
    except Exception as exc:
        logger.error("[DHCP] dnsmasq launch error: %s", exc)
        # v0.5.217 (audit fix F): mark as Failed before returning.
        _update_device_db(
            device_db,
            device_id,
            {
                "dhcp_mode": "server",
                "dhcp_state": "Failed",
                "dhcp_running": False,
                "dhcp_last_error": f"dnsmasq launch: {exc}",  # v0.5.222
                "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"success": False, "error": str(exc)}

    pool_networks_unique = []
    route_networks = []
    gateway_routes = []

    if ipv4_enabled:
        pool_networks = _collect_pool_networks(pool_start, pool_end, additional_pools)
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
        route_seen = set()
        for net in (pool_networks_unique + gateway_routes):
            if not net:
                continue
            key = str(net)
            if key in route_seen:
                continue
            route_seen.add(key)
            route_networks.append(net)

        # v0.5.218 (audit fix L): resolve the device's VRF once,
        # then mirror every server-mode ``ip route replace`` into
        # the VRF table. Legacy no-VRF deployments get vrf_name=None
        # and behave exactly as pre-fix.
        vrf_name = _resolve_device_vrf(device_id)

        if gateway_routes:
            if gateway and interface:
                try:
                    host_route_cmd = ["ip", "route", "replace", f"{gateway}/32", "dev", interface]
                    _run_command(host_route_cmd, timeout=5, container=container)
                    logger.debug("[DHCP] Added host route to gateway %s on %s", gateway, interface)
                except Exception as gateway_route_exc:
                    logger.debug(
                        "[DHCP] Could not add host route to gateway (may already exist): %s",
                        gateway_route_exc,
                    )
                # v0.5.218: mirror the gateway /32 into the VRF too.
                if vrf_name:
                    try:
                        vrf_host_cmd = ["ip", "route", "replace", f"{gateway}/32",
                                        "dev", interface, "vrf", vrf_name]
                        _run_command(vrf_host_cmd, timeout=5, container=container)
                        logger.debug(
                            "[DHCP VRF] Added host route to gateway %s on %s in vrf %s",
                            gateway, interface, vrf_name,
                        )
                    except Exception as vrf_host_exc:
                        logger.debug(
                            "[DHCP VRF] Could not add host route to gateway in vrf (may already exist): %s",
                            vrf_host_exc,
                        )

            for net in gateway_routes:
                _add_route_and_vrf_copy(
                    str(net), gateway=gateway, interface=interface or "",
                    family="ipv4", vrf_name=vrf_name, container=container,
                    log_prefix="[DHCP]", label="gateway route",
                )

        if gateway and pool_networks_unique:
            for net in pool_networks_unique:
                _add_route_and_vrf_copy(
                    str(net), gateway=gateway, interface=interface or "",
                    family="ipv4", vrf_name=vrf_name, container=container,
                    log_prefix="[DHCP]", label="static route",
                )

    ipv6_subnets = []
    if ipv6_enabled:
        if ipv6_pool_start and ipv6_prefix:
            try:
                ipv6_network = str(ipaddress.IPv6Interface(f"{ipv6_pool_start}/{ipv6_prefix}").network)
                ipv6_subnets.append(ipv6_network)
            except Exception as exc:
                logger.debug(
                    "[DHCP] Failed to derive IPv6 subnet for %s/%s: %s",
                    ipv6_pool_start,
                    ipv6_prefix,
                    exc,
                )

        # v0.5.218 (audit fix L): IPv6 side of the VRF-scope mirror.
        # ipv6_vrf_name reuses the ipv4 vrf_name if we already
        # resolved one; otherwise resolves it now (the ipv4 branch
        # is skipped when ipv4 is disabled).
        try:
            _local_vrf_name = vrf_name  # noqa: F821 (defined in ipv4 branch)
        except NameError:
            _local_vrf_name = _resolve_device_vrf(device_id)

        if ipv6_gateway_routes:
            for route in ipv6_gateway_routes:
                _add_route_and_vrf_copy(
                    route, gateway=ipv6_gateway or "",
                    interface=interface or "",
                    family="ipv6", vrf_name=_local_vrf_name,
                    container=container,
                    log_prefix="[DHCP]", label="IPv6 gateway route",
                )

        if ipv6_gateway and ipv6_subnets:
            for subnet in ipv6_subnets:
                _add_route_and_vrf_copy(
                    subnet, gateway=ipv6_gateway,
                    interface=interface or "",
                    family="ipv6", vrf_name=_local_vrf_name,
                    container=container,
                    log_prefix="[DHCP]", label="IPv6 static route",
                )

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
        config_for_db["ipv4_enabled"] = ipv4_enabled
        config_for_db["ipv6_enabled"] = ipv6_enabled
        if ipv6_enabled:
            if ipv6_pool_start and ipv6_pool_end:
                config_for_db["ipv6_pool_start"] = ipv6_pool_start
                config_for_db["ipv6_pool_end"] = ipv6_pool_end
                config_for_db["ipv6_pool_range"] = f"{ipv6_pool_start}-{ipv6_pool_end}"
            if ipv6_prefix:
                config_for_db["ipv6_prefix"] = ipv6_prefix
            if ipv6_server_ip:
                config_for_db["ipv6_server_ip"] = ipv6_server_ip
            if ipv6_gateway:
                config_for_db["ipv6_gateway"] = ipv6_gateway
            if ipv6_gateway_routes:
                config_for_db["ipv6_gateway_route_normalized"] = ipv6_gateway_routes
            config_for_db["ipv6_lease_time"] = ipv6_lease_seconds
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
    lease_sources = [str(net) for net in route_networks] or []
    if config_for_db.get("pool_networks"):
        lease_sources.extend(config_for_db.get("pool_networks", []))
    if ipv6_subnets:
        lease_sources.extend(ipv6_subnets)
    elif ipv6_enabled and config_for_db.get("ipv6_pool_range"):
        lease_sources.append(config_for_db["ipv6_pool_range"])
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
            "dhcp_last_error": "",  # v0.5.222: clear on success
            "dhcp_lease_ip": "",
            "dhcp_lease_mask": "",
            "dhcp_lease_gateway": gateway if ipv4_enabled else ipv6_gateway,
            "dhcp_lease_server": "",
            "dhcp_lease_expires": None,
            "dhcp_lease_subnet": lease_subnet,
            "last_dhcp_check": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True}


def stop_dhcp_server(device_db, device_id: str, interface: str, container=None) -> Dict:
    """Stop dnsmasq instance bound to interface."""
    # v0.5.217 (audit fix D): collect per-step failures instead of
    # returning {"success": True} unconditionally. Individual sub-
    # steps (kill dnsmasq PID, rm conf, del IPv4/IPv6 routes,
    # remove IPv6 address) previously swallowed exceptions at debug
    # level and the function still reported success, so callers
    # (stop_dhcp_services, /api/device/remove) recorded "cleanup OK"
    # while the container held stale routes and dnsmasq kept its
    # config file. Track everything that failed and return it.
    failures: List[str] = []

    # v0.5.221: normalize display form vlan200@ens2f0np0 → vlan200.
    interface = _normalize_iface_name(interface)
    pidfile = os.path.join(DNSMASQ_PID_DIR, f"dnsmasq-{interface}.pid")
    conffile = os.path.join(DNSMASQ_CONF_DIR, f"ostg-{interface}.conf")
    gateway = ""
    networks = None
    extra_routes = None
    ipv6_gateway = ""
    ipv6_gateway_routes = []
    ipv6_server_ip = ""
    ipv6_prefix = ""
    ipv6_subnets = []
    # v0.5.229 (audit U server-2): pre-initialize `device` / `dhcp_cfg`
    # so the downstream `interface_from_cfg = ...if device else...`
    # at ~line 2324 doesn't NameError when the DB lookup raises
    # (transient sqlite lock, corrupt row) — pre-fix that inverse
    # crash aborted the entire stop path before removing routes or
    # the IPv6 anchor, the exact leak audit fix D was supposed to
    # close.
    device = None
    dhcp_cfg: Dict = {}
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
            ipv6_gateway = str(dhcp_cfg.get("ipv6_gateway") or "")
            ipv6_server_ip = str(dhcp_cfg.get("ipv6_server_ip") or dhcp_cfg.get("ipv6_server") or "")
            ipv6_prefix = str(
                dhcp_cfg.get("ipv6_prefix")
                or dhcp_cfg.get("ipv6_prefix_length")
                or dhcp_cfg.get("ipv6_prefix_len")
                or ""
            )

            if not (stored_pool_networks or stored_gateway_routes):
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

            if _truthy(dhcp_cfg.get("ipv6_enabled")):
                start_v6 = str(
                    dhcp_cfg.get("ipv6_pool_start")
                    or dhcp_cfg.get("pool_start_v6")
                    or ""
                ).strip()
                if start_v6 and ipv6_prefix:
                    try:
                        ipv6_network = str(ipaddress.IPv6Interface(f"{start_v6}/{ipv6_prefix}").network)
                        ipv6_subnets.append(ipv6_network)
                    except Exception as exc:
                        logger.debug(
                            "[DHCP] Failed to derive IPv6 subnet for cleanup %s/%s: %s",
                            start_v6,
                            ipv6_prefix,
                            exc,
                        )
                routes_v6 = (
                    dhcp_cfg.get("ipv6_gateway_route_normalized")
                    or dhcp_cfg.get("ipv6_gateway_route")
                    or dhcp_cfg.get("ipv6_gateway_routes")
                    or []
                )
                if isinstance(routes_v6, str):
                    ipv6_gateway_routes = [
                        token.strip()
                        for token in routes_v6.replace(";", ",").split(",")
                        if token and token.strip()
                    ]
                elif isinstance(routes_v6, (list, tuple, set)):
                    ipv6_gateway_routes = [str(token).strip() for token in routes_v6 if str(token).strip()]
                elif routes_v6:
                    route_token = str(routes_v6).strip()
                    if route_token:
                        ipv6_gateway_routes = [route_token]
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
        # v0.5.217 (audit fix D): upgrade debug->warning + record.
        logger.warning("[DHCP] Failed to stop dnsmasq: %s", exc)
        failures.append(f"stop dnsmasq: {exc}")
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
        # v0.5.217 (audit fix D): upgrade debug->warning + record.
        logger.warning("[DHCP] Failed to remove dnsmasq config: %s", exc)
        failures.append(f"remove dnsmasq config: {exc}")

    # v0.5.219 (audit fix C1): resolve the device's VRF once so the
    # route removals below can also clean up the VRF-scoped mirror
    # copies that ``_add_route_and_vrf_copy`` installed in v0.5.218.
    # Pre-fix, ``stop_dhcp_server`` only touched the main table, so
    # every VRF-scoped route leaked past Stop / Remove.
    vrf_name = _resolve_device_vrf(device_id)
    interface_from_cfg = (dhcp_cfg.get("interface") if device else None) or interface

    # Remove IPv4 routes from container (regardless of gateway, since gateway_routes may not have gateway)
    if networks and container:
        for net in networks:
            failures.extend(
                _remove_route_and_vrf_copy(
                    str(net),
                    gateway=gateway,
                    interface=interface_from_cfg or "",
                    family="ipv4",
                    vrf_name=vrf_name,
                    container=container,
                    log_prefix="[DHCP]",
                    label="static route",
                )
            )

    if container and ipv6_gateway_routes:
        for route in ipv6_gateway_routes:
            failures.extend(
                _remove_route_and_vrf_copy(
                    route,
                    gateway=ipv6_gateway,
                    interface=interface_from_cfg or "",
                    family="ipv6",
                    vrf_name=vrf_name,
                    container=container,
                    log_prefix="[DHCP]",
                    label="IPv6 gateway route",
                )
            )

    if container and ipv6_gateway and ipv6_subnets:
        for subnet in ipv6_subnets:
            failures.extend(
                _remove_route_and_vrf_copy(
                    subnet,
                    gateway=ipv6_gateway,
                    interface=interface_from_cfg or "",
                    family="ipv6",
                    vrf_name=vrf_name,
                    container=container,
                    log_prefix="[DHCP]",
                    label="IPv6 static route",
                )
            )

    if ipv6_server_ip and ipv6_prefix:
        _remove_ipv6_address(interface, ipv6_server_ip, ipv6_prefix, container=container)

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
    # v0.5.217 (audit fix D): surface aggregated failures instead of
    # blanket success. Callers previously had no way to know that
    # dnsmasq is still running or that a route is still installed.
    if failures:
        return {
            "success": False,
            "error": "; ".join(failures),
            "failures": failures,
        }
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

    # v0.5.217 (audit fix G): whenever an explicit stop reaches this
    # entry point, stamp dhcp_manual_override=True so the DHCP
    # monitor doesn't turn around on its next tick and restart the
    # service the operator just asked us to stop. Mirrors the
    # BGP/OSPF/ISIS manual_override pattern. The override auto-
    # expires after 120 s inside the monitor (see
    # utils/dhcp_monitor.py) or the moment the monitor observes a
    # successful "Leased" state after taking over.
    _update_device_db(
        device_db,
        device_id,
        {
            "dhcp_manual_override": True,
            "dhcp_manual_override_time": datetime.now(timezone.utc).isoformat(),
        },
    )

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

    # v0.5.229 (audit U server-6): stop the OTHER mode's daemons
    # before starting this mode. Pre-fix, flipping server→client
    # called start_dhcp_client but never stopped the still-running
    # dnsmasq from the previous server mode, so both daemons ended
    # up bound to the same interface and clients could get leases
    # from either. Query the stored mode; if it differs from the
    # incoming request, stop the other side first.
    try:
        _prev = device_db.get_device(device_id) if device_db else None
        _prev_mode = (
            (_prev or {}).get("dhcp_mode")
            or ((_prev or {}).get("dhcp_config") or {}).get("mode")
            or ""
        )
        _prev_mode = str(_prev_mode).lower()
        if _prev_mode and _prev_mode != mode:
            logger.info(
                "[DHCP] Mode transition on %s: %s → %s. Stopping the previous mode's daemons.",
                device_id, _prev_mode, mode,
            )
            try:
                if _prev_mode == "server":
                    stop_dhcp_server(device_db, device_id, interface,
                                     container=_ensure_dhcp_container(device_id, mode="server"))
                elif _prev_mode == "client":
                    stop_dhcp_client(device_db, device_id, interface,
                                     container=_ensure_dhcp_container(device_id, mode="client"),
                                     dhcp_config=_prev.get("dhcp_config") if _prev else None)
            except Exception as _trans_exc:
                logger.warning(
                    "[DHCP] Mode-transition stop for %s (%s → %s) raised: %s "
                    "(continuing with the new-mode start regardless).",
                    device_id, _prev_mode, mode, _trans_exc,
                )
    except Exception as _prev_exc:
        logger.debug("[DHCP] Mode-transition gate: prev-mode lookup failed: %s", _prev_exc)

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
                gateway = _parse_gateway(interface, container=managed_container, device_id=device_id) or ""
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

