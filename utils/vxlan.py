"""
VXLAN lifecycle utilities for OSTG.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def normalize_config(raw_config: Any) -> Dict[str, Any]:
    """Return a normalized VXLAN configuration dictionary."""
    if not raw_config:
        return {}

    config: Dict[str, Any]
    try:
        if isinstance(raw_config, str):
            config = json.loads(raw_config) if raw_config else {}
            if isinstance(config, str):
                config = json.loads(config)
        elif isinstance(raw_config, dict):
            config = dict(raw_config)
        else:
            config = {}
    except (json.JSONDecodeError, TypeError):
        logger.debug("[VXLAN] Failed to parse raw config %s", raw_config)
        config = {}

    if not isinstance(config, dict):
        return {}

    def _clean(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    vni = config.get("vni") or config.get("vxlan_id")
    try:
        config["vni"] = int(vni) if vni is not None else None
    except (TypeError, ValueError):
        config["vni"] = None

    remote_values = (
        config.get("remote_peers")
        or config.get("remote_endpoints")
        or config.get("remote")
        or []
    )
    remote_peers: List[str] = []
    if isinstance(remote_values, str):
        remote_peers = [token.strip() for token in remote_values.replace(";", ",").split(",") if token.strip()]
    elif isinstance(remote_values, (list, tuple, set)):
        remote_peers = [_clean(token) for token in remote_values if _clean(token)]
    elif remote_values:
        token = _clean(remote_values)
        if token:
            remote_peers = [token]
    config["remote_peers"] = remote_peers

    config["local_ip"] = _clean(config.get("local_ip") or config.get("vxlan_local_ip") or config.get("source_ip"))
    config["underlay_interface"] = _clean(config.get("underlay_interface") or config.get("vxlan_underlay") or config.get("interface"))
    config["overlay_interface"] = _clean(config.get("overlay_interface") or config.get("vxlan_overlay"))
    config["vxlan_interface"] = _clean(config.get("vxlan_interface"))

    udp_port = config.get("udp_port") or config.get("vxlan_udp_port")
    try:
        config["udp_port"] = int(udp_port) if udp_port is not None else 4789
    except (TypeError, ValueError):
        config["udp_port"] = 4789

    config["enabled"] = bool(config.get("enabled", True) and config.get("vni"))
    return config


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Return a list of validation errors for the VXLAN config."""
    errors: List[str] = []
    if not config:
        return errors
    if not config.get("vni"):
        errors.append("VXLAN VNI is required")
    if not config.get("local_ip"):
        errors.append("VXLAN local endpoint (source IP) is required")
    remote_peers = config.get("remote_peers") or []
    if not remote_peers:
        errors.append("At least one remote VXLAN endpoint is required")
    if not config.get("underlay_interface"):
        errors.append("Underlay interface is required for VXLAN")
    return errors


def ensure_vxlan_interface(
    device_id: str,
    device_name: str,
    vxlan_config: Dict[str, Any],
    *,
    container_name: Optional[str] = None,
    frr_manager: Any = None,
) -> Dict[str, Any]:
    """Create or update the VXLAN interface (via FRR when available, otherwise on the host)."""
    config = normalize_config(vxlan_config)
    if not config or not config.get("enabled"):
        return {"success": False, "error": "VXLAN disabled"}

    errors = validate_config(config)
    if errors:
        return {"success": False, "error": errors[0]}

    vni = config["vni"]
    remote_peers = config.get("remote_peers") or []
    local_ip = config.get("local_ip")
    underlay = config.get("underlay_interface")
    udp_port = config.get("udp_port", 4789)

    if not remote_peers:
        return {"success": False, "error": "At least one remote endpoint is required"}
    if not local_ip:
        return {"success": False, "error": "Local endpoint is required"}
    if not underlay:
        return {"success": False, "error": "Underlay interface is required"}

    if container_name and frr_manager:
        # Configure inside container using iproute2 (FRR 10 no longer exposes 'vxlan id' under interface)
        ifname_seed = device_id.replace("-", "")
        vxlan_iface = config.get("vxlan_interface") or f"vx{vni}-{ifname_seed[:6]}"
        if len(vxlan_iface) > 15:
            vxlan_iface = vxlan_iface[:15]
        config["vxlan_interface"] = vxlan_iface
        remote_ip = remote_peers[0]
        try:
            _ensure_vxlan_in_container_iproute(
                container_name=container_name,
                frr_manager=frr_manager,
                iface=vxlan_iface,
                vni=vni,
                local_ip=local_ip,
                remote_ip=remote_ip,
                underlay=underlay,
                udp_port=udp_port,
            )
            logger.info(
                "[VXLAN] Configured VXLAN %s in container %s (vni=%s, remote=%s, underlay=%s)",
                vxlan_iface, container_name, vni, remote_ip, underlay
            )
            return {"success": True, "interface": vxlan_iface, "config": config}
        except Exception as exc:
            logger.error("[VXLAN] Failed to configure VXLAN in container: %s", exc)
            return {"success": False, "error": str(exc)}

    # Host-level fallback (legacy path)
    ifname_seed = device_id.replace("-", "")
    default_iface = f"vx{vni}-{ifname_seed[:6]}"
    vxlan_iface = config.get("vxlan_interface") or default_iface
    if len(vxlan_iface) > 15:
        vxlan_iface = vxlan_iface[:15]
        logger.debug("[VXLAN] Truncated interface name to %s (IFNAMSIZ limit)", vxlan_iface)
    config["vxlan_interface"] = vxlan_iface
    remote_ip = remote_peers[0]

    try:
        if not _interface_exists(vxlan_iface):
            base_cmd = [
                "ip",
                "link",
                "add",
                vxlan_iface,
                "type",
                "vxlan",
                "id",
                str(vni),
                "local",
                local_ip,
                "remote",
                remote_ip,
                "dev",
                underlay,
                "dstport",
                str(udp_port),
            ]

            def _add_iface(cmd: List[str]) -> bool:
                try:
                    _run(cmd)
                    return True
                except subprocess.CalledProcessError as exc_inner:
                    stderr_inner = (exc_inner.stderr or "").strip()
                    if "File exists" in stderr_inner:
                        logger.warning("[VXLAN] %s already exists, deleting stray interface and retrying", vxlan_iface)
                        _safe_delete_interface(vxlan_iface)
                        _run(cmd)
                        return True
                    if "A VXLAN device with the specified VNI already exists" in stderr_inner:
                        conflicts = _find_vxlan_interfaces_by_vni(vni, local_ip, remote_ip, underlay, udp_port)
                        if not conflicts:
                            logger.warning(
                                "[VXLAN] Kernel reported existing VXLAN for vni=%s but none detected via ip link",
                                vni,
                            )
                        for conflict_iface in conflicts:
                            if conflict_iface == vxlan_iface:
                                logger.warning(
                                    "[VXLAN] Removing stale VXLAN interface %s before recreation", conflict_iface
                                )
                            else:
                                logger.warning(
                                    "[VXLAN] Removing conflicting VXLAN interface %s (same vni=%s)",
                                    conflict_iface,
                                    vni,
                                )
                            _safe_delete_interface(conflict_iface)
                        _run(cmd)
                        return True
                    raise

            try:
                cmd_with_nolearning = base_cmd + ["nolearning"]
                try:
                    _add_iface(cmd_with_nolearning)
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or "").strip()
                    if "Attribute failed policy validation" in stderr or "Operation not supported" in stderr:
                        logger.warning(
                            "[VXLAN] Kernel rejected 'nolearning' attribute for %s, retrying without it: %s",
                            vxlan_iface,
                            stderr or exc,
                        )
                        _add_iface(base_cmd)
                    else:
                        raise
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or "").strip()
                logger.error("[VXLAN] Failed to add interface %s: %s", vxlan_iface, stderr or exc)
                raise

            logger.info(
                "[VXLAN] Created interface %s (vni=%s, remote=%s, underlay=%s)",
                vxlan_iface,
                vni,
                remote_ip,
                underlay,
            )

        _run(["ip", "link", "set", vxlan_iface, "up"])
        logger.info("[VXLAN] Interface %s is up", vxlan_iface)
        return {"success": True, "interface": vxlan_iface, "config": config}
    except subprocess.CalledProcessError as exc:
        logger.error("[VXLAN] Failed to ensure interface %s: %s", vxlan_iface, exc)
        return {"success": False, "error": exc.stderr.strip() if exc.stderr else str(exc)}
    except Exception as exc:
        logger.error("[VXLAN] Unexpected error ensuring %s: %s", vxlan_iface, exc, exc_info=True)
        return {"success": False, "error": str(exc)}


def tear_down_vxlan_interface(
    device_id: str,
    vxlan_config: Dict[str, Any],
    *,
    container_name: Optional[str] = None,
    frr_manager: Any = None,
) -> bool:
    """Remove the VXLAN interface."""
    config = normalize_config(vxlan_config)
    iface = config.get("vxlan_interface")
    if not iface:
        vni = config.get("vni")
        iface = f"vxlan{vni}-{device_id[:8]}" if vni else None
    if not iface:
        return False

    if container_name and frr_manager:
        try:
            _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "down"])
        except Exception:
            pass
        try:
            _container_ip(frr_manager, container_name, ["ip", "link", "del", iface])
            logger.info("[VXLAN] Removed container VXLAN interface %s (%s)", iface, container_name)
            return True
        except Exception as exc:
            logger.warning("[VXLAN] Failed to remove container VXLAN interface %s: %s", iface, exc)
            return False

    if not _interface_exists(iface):
        return False
    try:
        _run(["ip", "link", "set", iface, "down"])
        _run(["ip", "link", "del", iface])
        logger.info("[VXLAN] Removed interface %s", iface)
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("[VXLAN] Failed to remove interface %s: %s", iface, exc)
        return False
    except Exception as exc:
        logger.warning("[VXLAN] Unexpected error removing %s: %s", iface, exc)
        return False


def _ensure_vxlan_in_container_iproute(
    *,
    container_name: str,
    frr_manager: Any,
    iface: str,
    vni: int,
    local_ip: str,
    remote_ip: str,
    underlay: str,
    udp_port: int,
) -> None:
    # Check existence
    exists = _container_ip_exists(frr_manager, container_name, iface)
    if not exists:
        base_cmd = [
            "ip", "link", "add", iface, "type", "vxlan",
            "id", str(vni),
            "local", local_ip,
            "remote", remote_ip,
            "dev", underlay,
            "dstport", str(udp_port),
        ]
        try:
            # Try with 'nolearning' first
            _container_ip(frr_manager, container_name, base_cmd + ["nolearning"])
        except Exception as exc:
            msg = str(exc)
            if "Attribute failed policy validation" in msg or "Operation not supported" in msg:
                _container_ip(frr_manager, container_name, base_cmd)
            elif "File exists" in msg or "A VXLAN device with the specified VNI already exists" in msg:
                try:
                    _container_ip(frr_manager, container_name, ["ip", "link", "del", iface])
                except Exception:
                    pass
                _container_ip(frr_manager, container_name, base_cmd)
            else:
                raise
    # Bring up
    _container_ip(frr_manager, container_name, ["ip", "link", "set", iface, "up"])


def _run_vtysh(frr_manager: Any, container_name: str, commands: List[str]) -> None:
    if not commands:
        return
    container = frr_manager.client.containers.get(container_name)
    payload = "\n".join(commands)
    exec_cmd = f"vtysh <<'EOF'\n{payload}\nEOF"
    result = container.exec_run(["bash", "-c", exec_cmd])
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
        raise RuntimeError(f"vtysh command failed (code {result.exit_code}): {output.strip()}")


def _container_ip(frr_manager: Any, container_name: str, cmd: List[str]) -> None:
    container = frr_manager.client.containers.get(container_name)
    logger.debug("[VXLAN CMD][%s] %s", container_name, " ".join(cmd))
    result = container.exec_run(cmd)
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="ignore") if isinstance(result.output, bytes) else str(result.output)
        raise RuntimeError(output.strip())


def _container_ip_exists(frr_manager: Any, container_name: str, iface: str) -> bool:
    container = frr_manager.client.containers.get(container_name)
    result = container.exec_run(["ip", "link", "show", iface])
    return result.exit_code == 0


def _interface_exists(interface: str) -> bool:
    if not interface:
        return False
    try:
        subprocess.run(
            ["ip", "link", "show", interface],
            capture_output=True,
            check=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False

def _safe_delete_interface(interface: str) -> None:
    if not interface:
        return
    try:
        subprocess.run(
            ["ip", "link", "del", interface],
            capture_output=True,
            check=True,
            text=True,
        )
        logger.info("[VXLAN] Removed leftover interface %s", interface)
    except subprocess.CalledProcessError as exc:
        logger.debug(
            "[VXLAN] Attempted to delete %s but it was not present: %s",
            interface,
            (exc.stderr or "").strip() or exc,
        )


def _find_vxlan_interfaces_by_vni(
    vni: int,
    match_local: Optional[str] = None,
    match_remote: Optional[str] = None,
    match_underlay: Optional[str] = None,
    match_port: Optional[int] = None,
) -> List[str]:
    """Return interface names that already use the given VNI (optionally filtered)."""
    matches: List[str] = []
    for iface in _list_vxlan_interfaces():
        if iface.get("vni") != vni:
            continue
        if match_local and iface.get("local") and iface["local"] != match_local:
            continue
        if match_remote and iface.get("remote") and iface["remote"] != match_remote:
            continue
        if match_underlay and iface.get("underlay") and iface["underlay"] != match_underlay:
            continue
        if match_port and iface.get("dstport") and iface["dstport"] != match_port:
            continue
        matches.append(iface["name"])
    return matches


def _list_vxlan_interfaces() -> List[Dict[str, Any]]:
    """Parse `ip -d link show type vxlan` output into structured data."""
    try:
        proc = subprocess.run(
            ["ip", "-d", "link", "show", "type", "vxlan"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    interfaces: List[Dict[str, Any]] = []
    block: List[str] = []

    def _flush_block(lines: List[str]) -> None:
        if not lines:
            return
        header = lines[0]
        parts = header.split(":", 2)
        if len(parts) < 2:
            return
        name = parts[1].strip().split("@")[0]
        detail = " ".join(line.strip() for line in lines[1:])
        iface_info: Dict[str, Any] = {"name": name, "details": detail}
        vni_match = re.search(r"\bvxlan id (\d+)\b", detail)
        if vni_match:
            iface_info["vni"] = int(vni_match.group(1))
        local_match = re.search(r"\blocal ([0-9a-fA-F:\.]+)", detail)
        if local_match:
            iface_info["local"] = local_match.group(1)
        remote_match = re.search(r"\bremote ([0-9a-fA-F:\.]+)", detail)
        if remote_match:
            iface_info["remote"] = remote_match.group(1)
        underlay_match = re.search(r"\bdev ([\w\.\-]+)", detail)
        if underlay_match:
            iface_info["underlay"] = underlay_match.group(1)
        dstport_match = re.search(r"\bdstport (\d+)", detail)
        if dstport_match:
            iface_info["dstport"] = int(dstport_match.group(1))
        interfaces.append(iface_info)

    for line in proc.stdout.splitlines():
        if not line:
            continue
        if not line.startswith(" "):
            if block:
                _flush_block(block)
            block = [line]
        else:
            block.append(line)
    if block:
        _flush_block(block)
    return interfaces


def _extract_vlan_id(interface_name: Optional[str]) -> Optional[int]:
    if not interface_name:
        return None
    match = re.search(r"vlan(\d+)", interface_name)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    logger.debug("[VXLAN CMD] %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)

