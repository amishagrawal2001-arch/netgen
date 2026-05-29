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

_FRR_BUILD_DIR = "/opt/netgen"
_FRR_BUILD_ATTEMPTED = False  # one-shot per process — don't loop on failures


def _deploy_frr_assets_from_wheel(dest_dir=_FRR_BUILD_DIR):
    """Copy Dockerfile.frr + ostg_docker/ from the wheel-installed
    location into ``dest_dir`` so `docker build` has something to chew on.

    Why this lives here AND in install_ostg_complete.py: the §9a wheel-only
    upgrade path (`pip install --upgrade <new.whl> + systemctl restart`)
    doesn't run install_ostg_complete.py, so /opt/netgen/Dockerfile.frr
    can stay missing or stale after an upgrade. Same self-heal pattern as
    `_ensure_dpdk_tree_deployed` in run_tgen_server.py — the wheel is the
    canonical source.

    Returns True if Dockerfile.frr ends up in place, False otherwise.
    """
    import shutil
    try:
        import ostg_docker as _od
    except Exception as e:
        logger.warning(f"[FRR BUILD] ostg_docker package not importable: {e}")
        return False

    src_dir = os.path.dirname(os.path.abspath(_od.__file__))
    dockerfile_src = os.path.join(src_dir, "Dockerfile.frr")
    if not os.path.isfile(dockerfile_src):
        logger.warning(f"[FRR BUILD] {dockerfile_src} missing in wheel")
        return False

    try:
        os.makedirs(dest_dir, exist_ok=True)
        # 1. The full ostg_docker/ subtree (start-frr.sh + frr.conf.template
        #    are referenced by Dockerfile.frr's COPY directives).
        dst_pkg = os.path.join(dest_dir, "ostg_docker")
        if os.path.isdir(dst_pkg):
            shutil.rmtree(dst_pkg)
        shutil.copytree(src_dir, dst_pkg)
        for f in os.listdir(dst_pkg):
            if f.endswith(".sh"):
                os.chmod(os.path.join(dst_pkg, f), 0o755)
        # 2. Publish Dockerfile.frr + start-frr.sh + frr.conf.template
        #    at the install root — that's where `docker build -f
        #    Dockerfile.frr .` expects them when the build context
        #    is the install root.
        for f in ("Dockerfile.frr", "start-frr.sh", "frr.conf.template"):
            s = os.path.join(src_dir, f)
            if os.path.isfile(s):
                d = os.path.join(dest_dir, f)
                shutil.copy2(s, d)
                if f.endswith(".sh"):
                    os.chmod(d, 0o755)
        logger.info(f"[FRR BUILD] Deployed FRR assets from {src_dir} → {dest_dir}")
        return os.path.isfile(os.path.join(dest_dir, "Dockerfile.frr"))
    except (OSError, PermissionError) as e:
        logger.warning(f"[FRR BUILD] Deploy to {dest_dir} failed: {e}")
        return False


def _try_build_frr_image(client):
    """Last-ditch self-heal: build netgen-frr:latest from the wheel's
    bundled Dockerfile.frr when no FRR image is present locally.

    Without this, the very first BGP/OSPF apply on a freshly-installed
    server fails with "Failed to create FRR container — FRR manager
    returned None" because docker tries to pull `ostg-frr:latest` from
    Docker Hub (no such public image exists) and gets a 404. The
    operator then has to SSH in and `docker build` by hand — a
    documented step the install dialog used to do, but which the
    §9a wheel-only upgrade path silently skipped.

    ⚠ Call from ``start_frr_container`` only — NEVER from
    ``_resolve_frr_image`` / ``FRRDockerManager.__init__``. v0.2.18
    learned this the hard way: monitors (bgp/ospf/isis) instantiate
    FRRDockerManager at server startup, which called this helper,
    which blocked Flask from binding port 5050 for 2-3 minutes
    while the build ran. By the time the operator hit the GUI the
    server looked offline.

    Build can take 2–3 minutes (alpine apk install of frr + tools).
    Runs at most once per process (``_FRR_BUILD_ATTEMPTED`` guard) —
    if it fails, subsequent FRR-start calls return None immediately
    rather than retrying a multi-minute build on every device apply.

    The build runs with ``--network=host`` semantics (network_mode='host')
    so the apk fetch inside the build container inherits the host's
    /etc/resolv.conf. Without this, docker's default bridge DNS can
    fail to resolve Alpine CDN mirrors on hosts behind corporate
    DNS (e.g. Juniper internal). Confirmed on svl-hp-ai-srv02:
    APKINDEX fetch failed with "temporary error (try again later)"
    repeatedly until `--network=host` was added.

    Returns the tag string ("netgen-frr:latest") on success, None on
    failure.
    """
    global _FRR_BUILD_ATTEMPTED
    if _FRR_BUILD_ATTEMPTED:
        return None
    _FRR_BUILD_ATTEMPTED = True
    return _build_frr_image_now(client, reason="no FRR image found locally")


# Docker image label that records the SHA-256 of the Dockerfile.frr the
# image was built from. Lets the startup self-heal detect a wheel upgrade
# that changed the Dockerfile (e.g. v0.2.27 adding dhclient/dnsmasq) and
# rebuild the image, instead of silently keeping the stale one.
_FRR_DOCKERFILE_LABEL = "netgen.dockerfile_sha"


def _dockerfile_sha(path):
    """SHA-256 of a Dockerfile's bytes, or None if unreadable."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _build_frr_image_now(client, reason=""):
    """Deploy the wheel's FRR assets to /opt/netgen and build
    netgen-frr:latest (also tagged ostg-frr:latest). Stamps the image
    with a label recording the Dockerfile's SHA so a later wheel upgrade
    can detect a changed Dockerfile and rebuild.

    Unguarded — callers decide when to invoke (lazy one-shot via
    _try_build_frr_image, or startup stale-check via
    maybe_rebuild_frr_image). Returns the tag on success, None on failure.

    Uses network_mode='host' so apk inside the build container inherits
    the host's resolver — docker's default bridge DNS can't reach Alpine
    CDN mirrors on corporate-DNS hosts (confirmed on svl-hp-ai-srv02).
    """
    if not _deploy_frr_assets_from_wheel(_FRR_BUILD_DIR):
        return None

    dockerfile_path = os.path.join(_FRR_BUILD_DIR, "Dockerfile.frr")
    if not os.path.isfile(dockerfile_path):
        logger.warning(f"[FRR BUILD] {dockerfile_path} still missing after deploy")
        return None

    target_tag = "netgen-frr:latest"
    sha = _dockerfile_sha(dockerfile_path)
    logger.info(
        f"[FRR BUILD] Building {target_tag} from {dockerfile_path} "
        f"with --network=host ({reason}; may take 2–3 minutes)..."
    )
    try:
        # `rm=True`/`forcerm=True` clean up intermediate containers even on
        # failure. `network_mode='host'` == `docker build --network=host`.
        # `labels` stamps the Dockerfile SHA so maybe_rebuild_frr_image()
        # can tell when a wheel upgrade changed the Dockerfile.
        build_kwargs = dict(
            path=_FRR_BUILD_DIR,
            dockerfile="Dockerfile.frr",
            tag=target_tag,
            rm=True,
            forcerm=True,
            pull=False,
            network_mode="host",
        )
        if sha:
            build_kwargs["labels"] = {_FRR_DOCKERFILE_LABEL: sha}
        image, _logs = client.images.build(**build_kwargs)
        try:
            image.tag("ostg-frr", tag="latest")
        except Exception as e:
            logger.debug(f"[FRR BUILD] legacy ostg-frr tag failed: {e}")
        logger.info(f"[FRR BUILD] Built {target_tag} successfully (id={image.short_id})")
        return target_tag
    except docker.errors.BuildError as e:
        try:
            tail = []
            for chunk in e.build_log:
                line = chunk.get("stream") or chunk.get("error") or ""
                if line.strip():
                    tail.append(line.rstrip())
                if len(tail) > 20:
                    tail = tail[-20:]
            logger.error(
                f"[FRR BUILD] BuildError: {e.msg}\n"
                f"[FRR BUILD] Last build log lines:\n" + "\n".join(tail)
            )
        except Exception:
            logger.error(f"[FRR BUILD] BuildError (no log details): {e}")
        return None
    except Exception as e:
        logger.error(f"[FRR BUILD] Unexpected error during build: {e}")
        return None


def maybe_rebuild_frr_image(client=None):
    """Startup self-heal for the 'image exists but is STALE' case.

    The lazy ``_try_build_frr_image`` only builds when NO FRR image
    exists. A §9a wheel-only upgrade that changes Dockerfile.frr (e.g.
    v0.2.27 adding dhclient/dnsmasq for DHCP) leaves the OLD image in
    place, so the change never takes effect. This function compares the
    wheel's current Dockerfile SHA against the label baked into the
    running image and rebuilds if they differ.

    MUST be called off the main/Flask thread (it blocks 2–3 min on the
    build). run_tgen_server spawns it in a daemon thread at startup, so
    Flask binds its port immediately (the v0.2.18 startup-hang lesson).

    Returns: "rebuilt" | "current" | "missing" | "skipped" | "failed".
    """
    if client is None:
        try:
            client = docker.from_env()
        except Exception as e:
            logger.warning(f"[FRR REBUILD] Docker unavailable: {e}")
            return "skipped"

    # Make sure the wheel's Dockerfile is on disk before hashing it.
    _deploy_frr_assets_from_wheel(_FRR_BUILD_DIR)
    dockerfile_path = os.path.join(_FRR_BUILD_DIR, "Dockerfile.frr")
    want_sha = _dockerfile_sha(dockerfile_path)
    if not want_sha:
        logger.debug("[FRR REBUILD] No Dockerfile to hash; skipping stale-check")
        return "skipped"

    try:
        image = client.images.get("netgen-frr:latest")
    except Exception:
        # No image at all — leave it to the lazy build on first apply
        # (don't pay a 2-3 min build at every startup on hosts that
        # never use FRR/DHCP).
        logger.debug("[FRR REBUILD] netgen-frr:latest absent; lazy build will handle it")
        return "missing"

    have_sha = (image.labels or {}).get(_FRR_DOCKERFILE_LABEL)
    if have_sha == want_sha:
        logger.debug("[FRR REBUILD] FRR image matches current Dockerfile; no rebuild")
        return "current"

    logger.info(
        f"[FRR REBUILD] Dockerfile.frr changed since the running image was built "
        f"(image label={have_sha or 'none'}, wheel={want_sha[:12]}…) — rebuilding "
        f"netgen-frr:latest in the background. New FRR/DHCP containers will pick "
        f"up the change; existing containers keep running until recreated."
    )
    # Bypass the lazy one-shot guard — this is the explicit stale-rebuild.
    tag = _build_frr_image_now(client, reason="Dockerfile.frr changed after wheel upgrade")
    return "rebuilt" if tag else "failed"


def _resolve_frr_image(client=None):
    """Pick the FRR docker image to use. Priority (pure lookup, no
    side-effects):

      1. NETGEN_FRR_IMAGE / OSTG_FRR_IMAGE env var (explicit override)
      2. netgen-frr:latest if it exists locally (new branding)
      3. ostg-frr:latest (legacy fallback) if it exists locally
      4. Legacy "ostg-frr:latest" string so the failure message reads sensibly

    ⚠ This function MUST stay side-effect-free. ``FRRDockerManager.__init__``
    calls it, and the monitor threads (bgp/ospf/isis) instantiate that
    manager during server startup. v0.2.18 added an auto-build call here;
    the build blocked startup for 2-3 minutes and the server looked
    offline from the GUI. v0.2.19 moved auto-build into
    ``start_frr_container`` where blocking is expected (operator clicked
    Apply, they're waiting on something to happen anyway).
    """
    import os as _os
    env = (
        _os.environ.get("NETGEN_FRR_IMAGE")
        or _os.environ.get("OSTG_FRR_IMAGE")
        or ""
    ).strip()
    if env:
        return env
    if client is None:
        try:
            client = docker.from_env()
        except Exception:
            return "ostg-frr:latest"
    for candidate in ("netgen-frr:latest", "ostg-frr:latest"):
        try:
            client.images.get(candidate)
            return candidate
        except Exception:
            continue
    # Neither image present locally — return the legacy default. The
    # actual auto-build runs lazily from start_frr_container() when an
    # operator first applies a BGP/OSPF device, at which point a 2-3
    # minute hang is expected by the GUI.
    return "ostg-frr:latest"


class FRRDockerManager:
    """Manages FRR Docker containers on host networking.

    Earlier designs created an isolated docker bridge (`ostg-frr-network`,
    172.30.0.0/16) and rigged static host routes to it. The runtime now
    starts every FRR container with `network_mode='host'` so the container
    sees real interfaces (and VLAN subinterfaces) directly — the bridge
    is no longer needed and the setup helpers were removed.
    """

    def __init__(self):
        self.client = docker.from_env()
        # Container prefix kept at ostg-frr for backwards compatibility
        # with already-running deployments (renaming would orphan
        # in-flight containers); the image name auto-resolves so the
        # new netgen-frr build works without config edits.
        self.container_prefix = "ostg-frr"
        self.image_name = _resolve_frr_image(self.client)
    
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
    
    # NOTE: setup_network_infrastructure() and _setup_container_routing()
    # used to create an isolated docker bridge (`ostg-frr-network`,
    # 172.30.0.0/16) and install host static routes through it. That code
    # was dead — every container starts with `network_mode='host'` (see
    # start_frr_container below), so the bridge was never attached and
    # the static routes pointed at an unreachable next-hop. Both helpers
    # were removed; if you ever need an isolated FRR netns again, the
    # right tool is a docker macvlan or a custom netns, not a bridge.

    # ---------------------------------------------------------------
    # VRF lifecycle — multi-device-on-same-interface isolation
    # ---------------------------------------------------------------
    # Each device gets its own Linux VRF. The device's interface
    # (physical NIC or VLAN subinterface) is moved into the VRF as
    # kernel master; FRR config then carries a matching `vrf <name>`
    # keyword on `router bgp`, `router ospf`, etc. Result: bgpd in
    # container A binds TCP/179 inside vrf-A, container B's bgpd
    # binds TCP/179 inside vrf-B — no port collision in the host
    # netns. Same logic gives OSPF and IS-IS independent socket
    # spaces per device.
    #
    # The single-device case is still correct: it just runs in its
    # own VRF (an extra routing table, no observable change).

    VRF_TABLE_BASE = 1000  # Linux routing table ids start here for our VRFs

    def _vrf_name(self, device_id: str) -> str:
        """Stable VRF name for a device. Linux iface names cap at 15
        chars, so we use the first 11 hex chars of the device id."""
        if not device_id:
            return "vrf-default"
        short = str(device_id).replace("-", "")[:11] or "default"
        return f"vrf-{short}"

    def _vrf_table(self, device_id: str) -> int:
        """Deterministic per-device routing-table id (1000..1999)."""
        import hashlib
        h = hashlib.md5(str(device_id or "").encode()).hexdigest()[:8]
        return self.VRF_TABLE_BASE + (int(h, 16) % 1000)

    def _create_vrf(self, device_id: str, iface_name: str) -> Optional[str]:
        """Create the VRF for a device and move the iface into it.

        Returns the VRF name on success, None on failure. Idempotent:
        re-running on an existing VRF/iface is fine.
        """
        vrf_name = self._vrf_name(device_id)
        vrf_table = self._vrf_table(device_id)
        try:
            # 1. Create the VRF interface (idempotent)
            create = subprocess.run(
                ["ip", "link", "add", vrf_name, "type", "vrf", "table", str(vrf_table)],
                capture_output=True, text=True,
            )
            if create.returncode != 0 and "File exists" not in create.stderr:
                logger.error(f"[VRF] create {vrf_name} failed: {create.stderr.strip()}")
                return None
            # 2. Bring the VRF up
            subprocess.run(["ip", "link", "set", vrf_name, "up"],
                           capture_output=True, text=True)
            # 3. Move the iface into the VRF (only if not already there)
            #    `ip link show <iface>` master field tells us current owner.
            show = subprocess.run(["ip", "-o", "link", "show", iface_name],
                                  capture_output=True, text=True)
            already_in_vrf = f"master {vrf_name}" in (show.stdout or "")
            if not already_in_vrf:
                attach = subprocess.run(
                    ["ip", "link", "set", iface_name, "master", vrf_name],
                    capture_output=True, text=True,
                )
                if attach.returncode != 0:
                    logger.error(
                        f"[VRF] attach {iface_name} to {vrf_name} failed: {attach.stderr.strip()}"
                    )
                    return None
            logger.info(
                f"[VRF] device {device_id}: {iface_name} → {vrf_name} (table {vrf_table})"
            )
            return vrf_name
        except Exception as exc:
            logger.error(f"[VRF] _create_vrf({device_id}, {iface_name}): {exc}")
            return None

    def _remove_vrf(self, device_id: str, iface_name: Optional[str] = None) -> bool:
        """Detach iface from VRF (if known) and delete the VRF.
        Safe to call when nothing exists."""
        vrf_name = self._vrf_name(device_id)
        try:
            if iface_name:
                subprocess.run(["ip", "link", "set", iface_name, "nomaster"],
                               capture_output=True, text=True)
            subprocess.run(["ip", "link", "del", vrf_name],
                           capture_output=True, text=True)
            logger.info(f"[VRF] device {device_id}: removed {vrf_name}")
            return True
        except Exception as exc:
            logger.warning(f"[VRF] _remove_vrf({device_id}): {exc}")
            return False

    def vrf_name_for_device(self, device_id: str) -> str:
        """Public accessor — let OSPF/ISIS/BGP configurators in sibling
        modules look up the device's VRF name without re-implementing
        the naming convention."""
        return self._vrf_name(device_id)

    def start_frr_container(self, device_id: str, device_config: Dict) -> Optional[str]:
        """Start FRR container on host networking"""
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

            # --- Provision the device's VRF before starting FRR ---------
            # `iface_name` is the host interface FRR will attach to
            # (vlanN subif if VLAN-tagged, else the bare NIC). Moving
            # it into a per-device VRF gives this device's bgpd /
            # ospfd / isisd their own routing-table + socket-bind
            # space so a second device on a different VLAN of the
            # same NIC can coexist. We stash vrf_name back into
            # device_config so the BGP/OSPF/ISIS configurators below
            # (and the sibling utils/{bgp,ospf,isis}.py modules) can
            # emit matching `vrf <name>` keywords on their router
            # blocks. Failure here is non-fatal — single-device
            # deployments still work without VRF; we just lose
            # multi-device isolation.
            vrf_name = self._create_vrf(device_id, iface_name)
            if vrf_name:
                device_config['vrf_name'] = vrf_name
            else:
                logger.warning(
                    f"[FRR] device {device_id}: VRF setup failed, falling back to default VRF "
                    f"(multi-device-on-{iface_name} will collide on protocol ports)"
                )

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

            # Lazy auto-build: if the resolver returned a tag that no
            # longer exists locally (fresh §9a wheel-only upgrade with
            # no FRR image on disk), build it now from the wheel's
            # Dockerfile.frr. v0.2.18 tried to do this from __init__
            # and blocked server startup for 2-3 minutes; v0.2.19 does
            # it here where the operator is already waiting on the
            # Apply click. One-shot: _FRR_BUILD_ATTEMPTED guard inside
            # _try_build_frr_image means we only try once per process.
            try:
                self.client.images.get(self.image_name)
            except docker.errors.ImageNotFound:
                logger.info(
                    f"[FRR] Image {self.image_name} not present locally — "
                    f"attempting auto-build from wheel-bundled Dockerfile.frr"
                )
                built_tag = _try_build_frr_image(self.client)
                if built_tag:
                    self.image_name = built_tag
                else:
                    logger.error(
                        f"[FRR] Auto-build failed and no FRR image is available. "
                        f"Manual fix on the server: "
                        f"docker build --network=host -t netgen-frr:latest "
                        f"-f /opt/netgen/Dockerfile.frr /opt/netgen "
                        f"(see server log for [FRR BUILD] BuildError details)"
                    )
                    return None
            except Exception as e:
                # Don't block on a docker daemon glitch — let containers.run
                # raise the canonical error.
                logger.debug(f"[FRR] images.get check raised non-NotFound: {e}")

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
            
            # Get MTU from device_config
            mtu = device_config.get('mtu', '1500') if device_config else '1500'
            
            # Build vtysh commands for interface configuration.
            # If this device was provisioned into a VRF (multi-device
            # isolation), bind the interface to that VRF inside FRR
            # too — FRR needs to mirror the kernel-level VRF master so
            # protocol daemons resolve neighbors via the right table.
            vrf_name = (device_config or {}).get('vrf_name') if device_config else None
            vtysh_commands = ["configure terminal", f"interface {iface_name}"]
            if vrf_name:
                vtysh_commands.append(f" vrf {vrf_name}")

            if ipv4_addr:
                vtysh_commands.append(f" ip address {ipv4_addr}/{ipv4_mask}")
            else:
                vtysh_commands.append(" no ip address")
            
            if ipv6_addr:
                vtysh_commands.append(f" ipv6 address {ipv6_addr}/{ipv6_mask}")
            
            # Set MTU if provided
            if mtu and mtu.isdigit():
                vtysh_commands.append(f" ip mtu {mtu}")
            
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
                    # Only tear down the VRF on a full remove — a plain
                    # stop is treated as a pause and the VRF should
                    # survive so the device can resume cleanly. We
                    # don't know the iface here; nomaster is best-effort
                    # via _remove_vrf which logs and continues.
                    try:
                        # Look up the iface from the device record so we
                        # can detach it before deleting the VRF.
                        _iface = None
                        try:
                            from utils.device_database import DeviceDatabase
                            _rec = DeviceDatabase().get_device(device_id) if device_id else None
                            if _rec:
                                _stored = (_rec.get("interface") or "").strip()
                                # Stored form may be "vlanN@base" — strip
                                # the @base for the kernel iface name.
                                _iface = _stored.split("@", 1)[0] if _stored else None
                        except Exception:
                            _iface = None
                        self._remove_vrf(device_id, _iface)
                    except Exception as _vrf_exc:
                        logger.warning(f"[VRF] cleanup on stop failed for {device_id}: {_vrf_exc}")
                else:
                    logger.info(f"[FRR] Container {container_name} stopped successfully (not removed)")
            except docker.errors.NotFound:
                logger.info(f"[FRR] Container {container_name} not found")
            
            return True
            
        except Exception as e:
            logger.error(f"[FRR] Failed to stop FRR container for device {device_id}: {e}")
            return False

# Global instance — lazily created.
#
# Previously this was `frr_manager = FRRDockerManager()`, which called
# `docker.from_env()` at IMPORT time. That made `import utils.frr_docker`
# require a running Docker daemon (and 1+ second connect) just to load the
# module — it broke headless test/lint environments and added a hard
# Docker dependency to anything that transitively imports this file.
# The lazy proxy defers the Docker connection until the first real method
# call, so import is side-effect-free. All existing `frr_manager.X` call
# sites work unchanged.
class _LazyFRRManager:
    """Transparent proxy that instantiates the real FRRDockerManager on
    first attribute access and forwards everything to it thereafter.

    NB: intentionally NOT using ``__slots__`` — the proxy needs a normal
    ``__dict__`` so callers (and ``unittest.mock.patch.object``) can SET
    attributes on it. Attributes set on the proxy live in its __dict__
    and take precedence; only *unset* attributes fall through to the real
    manager via ``__getattr__`` (which still defers the Docker connect
    until the first real method use — preserving import-without-Docker).
    """
    _real = None

    def _get(self):
        if _LazyFRRManager._real is None:
            _LazyFRRManager._real = FRRDockerManager()
        return _LazyFRRManager._real

    def __getattr__(self, name):
        # __getattr__ only fires for names not found on the instance/class,
        # so a patched/explicitly-set attribute shadows this and we never
        # recurse on _get / class internals.
        return getattr(self._get(), name)


frr_manager = _LazyFRRManager()

# setup_frr_network() was removed along with setup_network_infrastructure();
# FRR runs on host networking and needs no docker network/bridge setup.


# Container-name prefixes we manage. `ostg-frr` is the historical
# prefix kept for backwards compatibility; `dhcp-frr` is the DHCP-
# client variant added later (see _get_container_name).
_MANAGED_CONTAINER_PREFIXES = ("ostg-frr-", "dhcp-frr-")


def list_all_containers() -> List[Dict[str, str]]:
    """Enumerate every FRR container this manager owns.

    Returns a list of `{"name", "device_id", "status", "image"}` dicts.
    Empty list on docker errors so callers (status endpoints) can keep
    working when docker is unreachable.
    """
    out: List[Dict[str, str]] = []
    try:
        client = docker.from_env()
        for c in client.containers.list(all=True):
            name = c.name or ""
            for pfx in _MANAGED_CONTAINER_PREFIXES:
                if name.startswith(pfx):
                    device_id = name[len(pfx):]
                    image_tag = ""
                    try:
                        image_tag = (c.image.tags[0] if c.image and c.image.tags else "")
                    except Exception:
                        pass
                    out.append({
                        "name": name,
                        "device_id": device_id,
                        "status": c.status,
                        "image": image_tag,
                    })
                    break
    except Exception as exc:
        logger.warning(f"[FRR] list_all_containers failed: {exc}")
    return out


def cleanup_all_containers() -> int:
    """Force-remove every FRR container this manager owns.

    Called from the netgen-cleanup systemd unit on shutdown and from
    `utils/bgp.py` on a global reset. Best-effort: per-container errors
    are logged and skipped. Returns the count actually removed.

    IMPORTANT: only ORPHANED containers get removed — i.e. ones whose
    device_id no longer matches a row in the device DB. A running OR
    Exited container that still has a DB record is preserved, because:

      * Running: obviously in use.
      * Exited: the user explicitly Stopped this device; they expect
        to be able to Start it again later. If we wiped it on the
        next netgen-cleanup tick (every 5 minutes), the user reports
        "Stop is deleting the FRR container" — exactly the symptom
        that prompted this safer behaviour.

    The DB-membership check is best-effort: if the DB is unreachable
    we err on the side of caution and skip removal entirely rather
    than risk wiping live containers.
    """
    removed = 0
    try:
        # Build a set of known device_ids up front so we don't query
        # the DB per-container. Fail closed: if we can't read the DB,
        # don't remove anything.
        known_device_ids = None
        try:
            from utils.device_database import DeviceDatabase
            known_device_ids = {
                str(d.get("device_id"))
                for d in (DeviceDatabase().get_all_devices() or [])
                if d.get("device_id")
            }
        except Exception as db_exc:
            logger.warning(
                f"[FRR] cleanup_all_containers: skipping (DB unreadable: {db_exc})"
            )
            return 0

        client = docker.from_env()
        for c in client.containers.list(all=True):
            name = c.name or ""
            if not any(name.startswith(p) for p in _MANAGED_CONTAINER_PREFIXES):
                continue
            # Recover the device_id from the container name.
            device_id = None
            for pfx in _MANAGED_CONTAINER_PREFIXES:
                if name.startswith(pfx):
                    device_id = name[len(pfx):]
                    break
            # Skip containers that still belong to a live device record
            # — that's the user-visible Stop/Start lifecycle, not an
            # orphan.
            if device_id and device_id in known_device_ids:
                logger.debug(
                    f"[FRR] cleanup: keeping {name} (device still in DB)"
                )
                continue
            try:
                c.remove(force=True)
                removed += 1
                logger.info(f"[FRR] cleanup: removed orphaned container {name}")
            except Exception as exc:
                logger.warning(f"[FRR] failed to remove {name}: {exc}")
            if device_id:
                # VRF only belongs to an orphan now — safe to drop.
                try:
                    frr_manager._remove_vrf(device_id, None)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning(f"[FRR] cleanup_all_containers failed: {exc}")
    return removed


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

        # If this device was provisioned with a VRF, scope the BGP
        # instance to it. `router bgp <asn> vrf <name>` makes bgpd
        # bind TCP/179 inside the VRF table, so multiple devices on
        # the same host won't collide on the listen socket.
        # Extract device_id from container_name so we can look up its VRF.
        device_id = container_name.replace(f"{frr_manager.container_prefix}-", "")
        vrf_name = neighbor_config.get('vrf_name') or frr_manager.vrf_name_for_device(device_id)
        # Only use the VRF if it actually exists on the host (lets
        # legacy non-VRF deployments keep working until the device is
        # re-applied through the new code path).
        vrf_exists = False
        try:
            _check = subprocess.run(["ip", "-o", "link", "show", vrf_name],
                                    capture_output=True, text=True)
            vrf_exists = (_check.returncode == 0 and bool((_check.stdout or "").strip()))
        except Exception:
            vrf_exists = False
        router_bgp_cmd = f"router bgp {local_as} vrf {vrf_name}" if vrf_exists else f"router bgp {local_as}"

        commands = [
            "configure terminal",
            router_bgp_cmd,
        ]
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

def _bgp_vtysh_scope(device_id: str) -> str:
    """Return the `vrf <name>` clause to inject into `show bgp` queries
    so we hit the device's per-device VRF instance (where the
    Established session actually lives) instead of the empty default
    VRF. Falls back to `vrf all` so single-device legacy deployments —
    which use the default VRF — keep working unchanged.
    """
    try:
        vrf_name = frr_manager.vrf_name_for_device(device_id)
        if vrf_name:
            return f"vrf {vrf_name}"
    except Exception:
        pass
    return "vrf all"


def get_bgp_status(device_id: str, device_name: str = None) -> Dict:
    """Get BGP status from FRR container.

    Scoped to the device's VRF so multi-device deployments report the
    session that's actually carrying traffic, not the phantom
    default-VRF bgpd instance the netgen-frr image creates at startup
    (which has no neighbors and is never Established).
    """
    try:
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)

        # Get BGP summary, scoped to the device's VRF.
        scope = _bgp_vtysh_scope(device_id)
        result = container.exec_run(f"vtysh -c 'show bgp {scope} summary'")
        
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
    """Get BGP neighbors from FRR container.

    VRF-scoped — see get_bgp_status for the why.
    """
    try:
        container_name = frr_manager._get_container_name(device_id, device_name)
        container = frr_manager.client.containers.get(container_name)

        # Get BGP neighbors, scoped to the device's VRF.
        scope = _bgp_vtysh_scope(device_id)
        result = container.exec_run(f"vtysh -c 'show bgp {scope} neighbors'")
        
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