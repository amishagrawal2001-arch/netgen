"""
ARP Status Monitoring System for OSTG
Multi-threaded ARP status monitoring with database updates
"""

import threading
import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# Configure logging
logger = logging.getLogger(__name__)

# v0.5.262 (audit ARP-4 + ARP-7): serialize DB writes per device_id
# so the 4-write update sequence in `_update_device_arp_status`
# can't interleave with a concurrent on-demand refresh writer for
# the same device. Prior to the lock two writers could produce
# torn state where `devices.arp_ipv4_resolved` disagreed with
# `device_statistics.arp_ipv4_resolved`.
_ARP_WRITE_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)
_ARP_WRITE_LOCKS_META_LOCK = threading.Lock()


def _arp_write_lock_for(device_id: str) -> threading.Lock:
    """Return the process-wide lock for a given device_id. The
    per-key `defaultdict` is not thread-safe against concurrent
    key creation, so we guard the "create-or-return" step with a
    tiny meta-lock. The actual lock is held OUTSIDE the meta-lock,
    so contention is per-device."""
    with _ARP_WRITE_LOCKS_META_LOCK:
        return _ARP_WRITE_LOCKS[device_id]


# v0.5.262 (audit ARP-10): dedup `log_device_event` against the
# last logged arp_status per device_id. Pre-fix every 30s poll
# wrote one row per device — 288k rows/day at 100 devices; the
# `device_events` table dominated DB size within weeks. The
# state-history table already dedup's via `add_state_transition`;
# do the same here so events are emitted only on transition.
_LAST_ARP_STATUS_LOGGED: Dict[str, str] = {}
_LAST_ARP_STATUS_LOGGED_LOCK = threading.Lock()

def _default_self_url():
    """Default self-loopback URL the monitors call back into.

    Reads NETGEN_SERVER_PORT (or legacy OSTG_SERVER_PORT) env var,
    falling back to 5050 — the actual port netgen-server binds to.
    The previous hardcoded `localhost:5051` was a stale carryover from
    an older port assignment and caused every monitor poll to log
    "[ARP/BGP/OSPF MONITOR] ... Connection refused" until run_tgen_server
    was patched to pass an explicit server_url. Belt-and-braces: also
    fix the default here so a caller that forgets the kwarg still
    gets the right port.
    """
    import os as _os
    port = (_os.environ.get("NETGEN_SERVER_PORT")
            or _os.environ.get("OSTG_SERVER_PORT")
            or "5050")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 5050
    return f"http://localhost:{port}"


class ARPStatusMonitor:
    """Multi-threaded ARP status monitoring system"""

    def __init__(self, device_db, server_url: str = None,
                 check_interval: int = 30, max_workers: int = 5):
        if not server_url:
            server_url = _default_self_url()
        """
        Initialize ARP status monitor.
        
        Args:
            device_db: DeviceDatabase instance
            server_url: OSTG server URL
            check_interval: Interval between ARP status checks (seconds)
            max_workers: Maximum number of worker threads
        """
        self.device_db = device_db
        self.server_url = server_url
        self.check_interval = check_interval
        self.max_workers = max_workers
        self.is_running = False
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.status_queue = queue.Queue()
        
        logger.info(f"[ARP MONITOR] Initialized with interval={check_interval}s, workers={max_workers}")
    
    def start(self):
        """Start the ARP status monitoring thread."""
        if self.is_running:
            logger.warning("[ARP MONITOR] Monitor is already running")
            return

        # v0.5.281 (ARP-GUARD-1): startup self-check. Report the
        # ARP-plane invariants that the gateway-orange saga (v0.5.254
        # → v0.5.280) taught us to watch. Anything reported as WRONG
        # here is a signal that the endpoint will produce a false-
        # orange (or false-green) result on the next poll.
        try:
            self._log_arp_plane_self_check()
        except Exception as _exc:
            logger.warning(f"[ARP MONITOR] self-check raised: {_exc}")

        # v0.5.282 (ARP-J3): sweep sysctls that block secondary-IP
        # ARP replies. v0.5.280 (DHCP-K1) sets these per-anchor
        # inside `_ensure_ipv4_address` — but that path only runs on
        # DHCP-server (re)start. If an operator upgrades to v0.5.280
        # or v0.5.282 without restarting the DHCP-server device, the
        # sysctl fix never fires and switches still can't ping the
        # anchor. Sweep at monitor start so EXISTING deployments
        # benefit immediately: set `net.ipv4.conf.all.arp_ignore=0`
        # and `net.ipv4.conf.all.arp_announce=0` — those propagate
        # to per-interface via the kernel's OR-with-`all` semantics.
        # Best-effort; log at debug when sysctl isn't available
        # (rootless container, sysctl locked). Non-fatal.
        try:
            self._sweep_arp_sysctls()
        except Exception as _exc:
            logger.warning(f"[ARP MONITOR] sysctl sweep raised: {_exc}")

        # v0.5.284 (ARP-J5): replay `_ensure_ipv4_address` for every
        # existing Running DHCP-server device. The whole cluster of
        # anchor-side fixes — v0.5.275 (DHCP-J2 VRF connected-route
        # install), v0.5.280 (DHCP-K1 per-anchor sysctl + K2 anchor
        # registration for periodic re-arp), v0.5.282 (ARP-J1 VRF
        # local-table install) — lives inside `_ensure_ipv4_address`.
        # That helper only runs when a DHCP-server device is
        # (re)started. If the operator upgrades netgen-server but
        # never touches the DHCP device from the UI, NONE of those
        # fixes run on their existing anchors. Replay at monitor
        # start so an operator gets every fix on next
        # `systemctl restart netgen-server` without needing to also
        # remove-and-re-add the DHCP device.
        try:
            self._replay_dhcp_anchor_setup()
        except Exception as _exc:
            logger.warning(f"[ARP MONITOR] DHCP anchor replay raised: {_exc}")

        # v0.5.287 (audit anchor-gateway-collision, fix C): scan
        # parent NICs of active DHCP-server subifs for orphaned
        # anchor IPs. If an anchor drifted onto the parent (from a
        # prior no-VLAN device or the pre-v0.5.287 server_ip==
        # gateway bug), the kernel installs a connected route for
        # its /24 in the default VRF via the parent — and ARP
        # replies for sibling anchors on the vlan subif get routed
        # out UNTAGGED into the switch's trunk (which drops them
        # as untagged garbage). WARN only, never auto-delete:
        # deleting IPs is destructive and an operator may have
        # placed one intentionally. Stop→start on the DHCP device
        # will trigger fix B cleanup and remove the orphan.
        try:
            self._scan_parent_nic_drift()
        except Exception as _exc:
            logger.warning(
                f"[ARP MONITOR] parent-NIC drift scan raised: {_exc}"
            )

        # v0.5.290 (audit anchor-DAD, part 3): scan the local
        # routing table for stale `local <ip> dev <iface>` entries
        # whose <ip> is no longer on <iface>. v0.5.286 (ARP-J6)
        # installs these explicitly; a subsequent `ip addr del`
        # (manual op, or the pre-v0.5.290 _remove_ipv4_address)
        # removed the address but the kernel didn'''t GC the
        # explicit local route. Kernel then treats the ghost IP
        # as netgen'''s own → drops incoming packets claiming
        # that source as martian → DHCP replies never see the
        # relayed request. WARN only, never auto-delete: an
        # operator may have installed a local route intentionally
        # (rare, but possible).
        try:
            self._scan_local_table_drift()
        except Exception as _exc:
            logger.warning(
                f"[ARP MONITOR] local-table drift scan raised: {_exc}"
            )

        self.is_running = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("[ARP MONITOR] Started ARP status monitoring")

    def _log_arp_plane_self_check(self) -> None:
        """v0.5.281 (ARP-GUARD-1): dump the four invariants the
        gateway-orange class taught us to enforce. On any
        failure, log a WARNING that names the specific ship
        the regression would look like — so a future operator
        searching the log for `arp_ignore` or `anchor-arp`
        finds this before opening yet another ticket."""
        import subprocess as _subprocess
        # Invariant 1: arp_ignore should be 0 on the "all" scope so
        # secondary-IP replies work. v0.5.280 (DHCP-K1) set this on
        # anchor interfaces explicitly; the "all" default backs it up.
        try:
            _res = _subprocess.run(
                ["sysctl", "-n", "net.ipv4.conf.all.arp_ignore"],
                capture_output=True, text=True, timeout=3,
            )
            _val = (_res.stdout or "").strip()
            if _val and _val != "0":
                logger.warning(
                    f"[ARP MONITOR] SELF-CHECK: "
                    f"net.ipv4.conf.all.arp_ignore={_val} "
                    f"(expected 0). Secondary-IP ARP replies may be "
                    f"dropped — the v0.5.280 class of 'switch can't "
                    f"ping netgen anchor' regression."
                )
        except Exception:
            pass
        # Invariant 2: the anchor re-arp thread is alive when any
        # anchors are registered. Without this the switch's MAC
        # table ages out and the anchor becomes unreachable.
        try:
            from utils import dhcp as _dhcp
            _anchors = list(_dhcp._ANCHOR_ARP_REFRESH.values())
            _thread_alive = (
                _dhcp._ANCHOR_ARP_THREAD is not None
                and _dhcp._ANCHOR_ARP_THREAD.is_alive()
            )
            if _anchors and not _thread_alive:
                logger.warning(
                    f"[ARP MONITOR] SELF-CHECK: "
                    f"{len(_anchors)} DHCP anchors registered but "
                    f"the re-arp refresh thread is not alive — v0.5.280 "
                    f"(DHCP-K2) regression. Switch MAC tables will age "
                    f"out. Restart the DHCP-server device to re-register."
                )
            elif _anchors:
                logger.info(
                    f"[ARP MONITOR] SELF-CHECK: {len(_anchors)} "
                    f"DHCP anchor(s) tracked by re-arp thread."
                )
        except Exception:
            pass
        # Invariant 3: the frr_manager lazy proxy is imported so
        # VRF detection doesn't cold-init Docker on the hot path.
        # v0.5.277 (ARP-H2) — the 2s-Docker-connect race that
        # produced silent-orange.
        try:
            from utils.frr_docker import frr_manager as _fm  # noqa: F401
        except Exception as _fm_exc:
            logger.warning(
                f"[ARP MONITOR] SELF-CHECK: cannot import "
                f"frr_manager lazy proxy ({_fm_exc}); ARP-status "
                f"endpoint will fall back to default netns for "
                f"VRF-scoped devices — v0.5.277 (ARP-H2) regression."
            )
    
    def stop(self):
        """Stop the ARP status monitoring thread."""
        if not self.is_running:
            logger.warning("[ARP MONITOR] Monitor is not running")
            return
        
        self.is_running = False
        self.stop_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
            if self.monitor_thread.is_alive():
                logger.warning("[ARP MONITOR] Monitor thread did not stop gracefully")
        
        logger.info("[ARP MONITOR] Stopped ARP status monitoring")

    def _sweep_arp_sysctls(self) -> None:
        """v0.5.282 (ARP-J3): set the sysctls that block secondary-
        IP ARP replies. Applied at ARP-monitor start so existing
        deployments benefit without needing to restart any DHCP
        device (the DHCP anchor add path already sets these per-
        interface via v0.5.280 DHCP-K1, but that only runs on
        (re)start).

        The Linux kernel semantics for `net.ipv4.conf.<iface>.<key>`
        is `max(all, <iface>)` for arp_ignore and `max(all, <iface>)`
        for arp_announce — so setting `all=0` doesn't force per-
        interface to 0 if the per-interface value is >0. Set BOTH
        `all` and `default` (default = template for new interfaces)
        to establish a safe baseline. The v0.5.280 per-anchor
        override still runs and force-sets specific interfaces to 0.
        """
        import subprocess as _subprocess
        # First: baseline the `all` and `default` scopes.
        _pairs = [
            ("net.ipv4.conf.all.arp_ignore", "0"),
            ("net.ipv4.conf.all.arp_announce", "0"),
            ("net.ipv4.conf.default.arp_ignore", "0"),
            ("net.ipv4.conf.default.arp_announce", "0"),
        ]
        for _key, _val in _pairs:
            try:
                _res = _subprocess.run(
                    ["sysctl", "-w", f"{_key}={_val}"],
                    capture_output=True, text=True, timeout=3,
                )
                if _res.returncode == 0:
                    logger.info(f"[ARP MONITOR] sysctl {_key}={_val}")
                else:
                    logger.debug(
                        f"[ARP MONITOR] sysctl {_key}={_val} "
                        f"returned {_res.returncode}: {_res.stderr.strip()}"
                    )
            except Exception as _exc:
                logger.debug(
                    f"[ARP MONITOR] sysctl {_key}={_val} skipped: {_exc}"
                )

        # v0.5.283 (ARP-J4): the `all` baseline above is INSUFFICIENT
        # for existing interfaces. The kernel computes the effective
        # arp_ignore for an interface as `max(all, <iface>)` — so
        # setting `all=0` while a per-interface value is >0 leaves
        # the effective value at >0 (still blocking secondary-IP
        # replies). My v0.5.282 comment even called this out but I
        # only set `all` + `default`, and `default` doesn't touch
        # existing interfaces (it's the template for new ones).
        # This ship iterates every existing interface and force-
        # sets per-interface arp_ignore/arp_announce to 0. That's
        # what actually makes the effective value 0.
        try:
            _link = _subprocess.run(
                ["ip", "-o", "link", "show"],
                capture_output=True, text=True, timeout=5,
            )
            if _link.returncode != 0:
                logger.debug(
                    f"[ARP MONITOR] `ip link show` returned "
                    f"{_link.returncode}; per-interface sysctl "
                    f"sweep skipped"
                )
                return
            _ifaces: list = []
            for _line in (_link.stdout or "").splitlines():
                # Line shape: "<idx>: <name>: <BROADCAST,...>"
                if ":" not in _line:
                    continue
                _parts = _line.split(":", 2)
                if len(_parts) < 2:
                    continue
                _name = _parts[1].strip().split("@", 1)[0]
                if not _name or _name == "lo":
                    continue
                _ifaces.append(_name)
            _changed = 0
            for _iface in _ifaces:
                for _key in ("arp_ignore", "arp_announce"):
                    _sysctl_key = f"net.ipv4.conf.{_iface}.{_key}"
                    try:
                        _cur = _subprocess.run(
                            ["sysctl", "-n", _sysctl_key],
                            capture_output=True, text=True, timeout=2,
                        )
                        _cur_val = (_cur.stdout or "").strip()
                        if _cur_val == "0":
                            continue
                        _set = _subprocess.run(
                            ["sysctl", "-w", f"{_sysctl_key}=0"],
                            capture_output=True, text=True, timeout=2,
                        )
                        if _set.returncode == 0:
                            logger.info(
                                f"[ARP MONITOR] sysctl {_sysctl_key} "
                                f"{_cur_val}→0"
                            )
                            _changed += 1
                    except Exception:
                        pass
            logger.info(
                f"[ARP MONITOR] per-interface sysctl sweep: "
                f"{len(_ifaces)} iface(s), {_changed} value(s) "
                f"changed"
            )
        except Exception as _exc:
            logger.debug(
                f"[ARP MONITOR] per-interface sysctl sweep failed: "
                f"{_exc}"
            )

    def _replay_dhcp_anchor_setup(self) -> None:
        """v0.5.284 (ARP-J5): iterate every Running DHCP-server
        device and re-invoke `_ensure_ipv4_address` for its anchor
        so the whole cluster of v0.5.275/280/282 fixes fires on
        existing deployments without needing to restart the DHCP
        device.

        Idempotent by design — `_ensure_ipv4_address` checks
        whether the IP is already on the interface and short-
        circuits the address-add ("File exists"), but the
        post-add plumbing (VRF connected-route probe, VRF local-
        table probe, per-anchor sysctl, gratuitous ARP, anchor
        registration for periodic re-arp) all runs on both the
        fresh-add AND already-assigned paths. So this replay
        does the RIGHT thing for a pre-existing anchor: leaves
        the address alone, but forces every guard to fire.

        Best-effort. Failures logged at warning (rooted commands
        may fail in rootless dev environments) but do NOT prevent
        the monitor from starting.
        """
        try:
            devices = self.device_db.get_all_devices()
        except Exception as _exc:
            logger.warning(
                f"[ARP MONITOR] anchor replay: get_all_devices "
                f"failed: {_exc}"
            )
            return
        _dhcp_servers = [
            d for d in (devices or [])
            if (d.get("status") == "Running"
                and str(d.get("dhcp_mode") or "").lower() == "server")
        ]
        if not _dhcp_servers:
            logger.info(
                "[ARP MONITOR] anchor replay: no Running DHCP-server "
                "devices; nothing to replay"
            )
            return
        try:
            from utils.dhcp import _ensure_ipv4_address
        except Exception as _imp_exc:
            logger.warning(
                f"[ARP MONITOR] anchor replay: cannot import "
                f"_ensure_ipv4_address: {_imp_exc}"
            )
            return
        # Normalize the display-form iface (`vlanN@ensXfY`) → `vlanN`
        # the same way `_normalize_iface_name` does. Avoid importing
        # utils.dhcp._normalize_iface_name here to keep the coupling
        # narrow; the "@" split is stable and the only normalization
        # we need.
        def _norm(_iface: str) -> str:
            return (_iface or "").split("@", 1)[0]
        _replayed = 0
        _failed = 0
        for _dev in _dhcp_servers:
            _dev_id = _dev.get("device_id") or "?"
            _iface = (
                _dev.get("interface")
                or _dev.get("server_interface")
                or ""
            )
            _vlan = str(_dev.get("vlan") or "0").strip()
            # Prefer the vlan sub-interface when a VLAN is set
            # (mirrors v0.5.279 ARP-H6 in the ARP endpoint).
            if _vlan and _vlan != "0":
                _iface = f"vlan{_vlan}"
            _iface = _norm(_iface)
            if not _iface:
                logger.debug(
                    f"[ARP MONITOR] anchor replay: device {_dev_id} "
                    f"has no interface; skipping"
                )
                continue
            _dhcp_cfg = _dev.get("dhcp_config")
            if isinstance(_dhcp_cfg, str):
                try:
                    import json as _json
                    _dhcp_cfg = _json.loads(_dhcp_cfg)
                except Exception:
                    _dhcp_cfg = {}
            if not isinstance(_dhcp_cfg, dict):
                _dhcp_cfg = {}
            _pool_start = (
                _dhcp_cfg.get("pool_start")
                or _dev.get("dhcp_pool_start")
                or ""
            )
            _pool_end = (
                _dhcp_cfg.get("pool_end")
                or _dev.get("dhcp_pool_end")
                or ""
            )
            if not (_pool_start and _pool_end):
                logger.debug(
                    f"[ARP MONITOR] anchor replay: device {_dev_id} "
                    f"has no pool range; skipping"
                )
                continue
            _gateway = (
                _dhcp_cfg.get("gateway")
                or _dev.get("dhcp_gateway")
                or ""
            )
            _mask = (
                _dhcp_cfg.get("mask")
                or _dev.get("ipv4_mask")
                or ""
            )
            try:
                _ensure_ipv4_address(
                    _iface, str(_pool_start), str(_pool_end),
                    gateway=str(_gateway or ""),
                    ipv4_mask=str(_mask or ""),
                    container=None,
                )
                _replayed += 1
                logger.info(
                    f"[ARP MONITOR] anchor replay: device {_dev_id} "
                    f"iface={_iface} pool={_pool_start}-{_pool_end} "
                    f"replayed"
                )
            except Exception as _rep_exc:
                _failed += 1
                logger.warning(
                    f"[ARP MONITOR] anchor replay: device {_dev_id} "
                    f"iface={_iface} failed: {_rep_exc}"
                )
        logger.info(
            f"[ARP MONITOR] anchor replay: {_replayed} replayed, "
            f"{_failed} failed (of {len(_dhcp_servers)} DHCP-server "
            f"devices)"
        )

    def _scan_parent_nic_drift(self) -> None:
        """v0.5.287 (audit anchor-gateway-collision, fix C): scan
        parent physical NICs of active DHCP-server subifs for
        orphaned anchor IPs.

        The failure mode this catches: a pre-v0.5.287 device (or
        the deleted-but-never-cleaned parent-NIC anchor from Fix B
        pre-history) left an IP on the parent NIC. The kernel then
        installs a connected route for its /24 in the default VRF
        via that parent. When the switch pings a sibling anchor
        (say 172.16.30.2 on vlan10), netgen's kernel generates a
        correct ARP reply but consults the routing table for the
        outbound interface, hits the default-VRF connected route
        via the parent, and sends the reply UNTAGGED. Switch trunk
        drops it. Operator sees 100% packet loss with no netgen-
        side error. Took 16+ ships to diagnose on srv06 2026-09-07.

        This method WARNS ONLY. It does not delete — deleting IPs
        is destructive and the operator may have configured one
        intentionally (management, out-of-band, etc.). The
        operator's fix is stop→start on the affected DHCP device,
        which triggers Fix B's parent-NIC cleanup.

        For each active DHCP-server device: derive its expected
        anchor candidates via `_collect_ipv4_anchor_candidates`,
        derive the parent of its subif, list the parent's IPv4
        addresses, and warn on any IP that matches a candidate.
        Unrelated IPs on the parent (management, etc.) are not
        candidates and don't trigger the warning.
        """
        try:
            devices = self.device_db.get_all_devices()
        except Exception as _exc:
            logger.warning(
                f"[ARP MONITOR] drift scan: get_all_devices failed: "
                f"{_exc}"
            )
            return
        _dhcp_servers = [
            d for d in (devices or [])
            if (d.get("status") == "Running"
                and str(d.get("dhcp_mode") or "").lower() == "server")
        ]
        if not _dhcp_servers:
            logger.debug(
                "[ARP MONITOR] drift scan: no Running DHCP-server "
                "devices; nothing to scan"
            )
            return
        try:
            from utils.dhcp import (
                _collect_ipv4_anchor_candidates,
                _iface_ipv4_addresses,
                _iface_parent,
            )
        except Exception as _imp_exc:
            logger.warning(
                f"[ARP MONITOR] drift scan: cannot import DHCP helpers: "
                f"{_imp_exc}"
            )
            return
        _warned_ifaces: set = set()
        for _dev in _dhcp_servers:
            _dev_id = _dev.get("device_id") or "?"
            _iface = (
                _dev.get("interface")
                or _dev.get("server_interface")
                or ""
            )
            _vlan = str(_dev.get("vlan") or "0").strip()
            if _vlan and _vlan != "0":
                _iface = f"vlan{_vlan}"
            _iface = (_iface or "").split("@", 1)[0]
            if not _iface:
                continue
            _parent = _iface_parent(_iface, container=None)
            if not _parent:
                # Device is configured directly on a parent NIC —
                # nothing to compare against a sibling subif. Skip.
                continue
            if _parent in _warned_ifaces:
                # Already warned about this parent for another
                # device on the same NIC — don't double-warn.
                continue
            _dhcp_cfg = _dev.get("dhcp_config")
            if isinstance(_dhcp_cfg, str):
                try:
                    import json as _json
                    _dhcp_cfg = _json.loads(_dhcp_cfg)
                except Exception:
                    _dhcp_cfg = {}
            if not isinstance(_dhcp_cfg, dict):
                _dhcp_cfg = {}
            try:
                _candidates = _collect_ipv4_anchor_candidates(_dhcp_cfg)
            except Exception:
                _candidates = set()
            if not _candidates:
                continue
            try:
                _parent_ips = _iface_ipv4_addresses(_parent, container=None)
            except Exception:
                _parent_ips = []
            _candidate_ips = {ip for ip, _pfx in _candidates}
            _orphans = [
                (ip, pfx) for ip, pfx in _parent_ips
                if ip in _candidate_ips
            ]
            if _orphans:
                _orphan_str = ", ".join(f"{ip}/{pfx}" for ip, pfx in _orphans)
                logger.warning(
                    f"[ARP MONITOR] DRIFT: parent NIC {_parent} carries "
                    f"anchor IP(s) that belong on subif {_iface} "
                    f"(device {_dev_id}): {_orphan_str}. This routes "
                    f"ARP replies for sibling anchors UNTAGGED through "
                    f"the parent, and the switch trunk drops them. "
                    f"Fix: stop→start the DHCP-server device (triggers "
                    f"v0.5.287 fix B cleanup). Manual: "
                    f"'sudo ip addr del <ip>/<pfx> dev {_parent}'."
                )
                _warned_ifaces.add(_parent)


    def _scan_local_table_drift(self) -> None:
        """v0.5.290 (audit anchor-DAD, part 3): find stale
        ``local <ip> dev <iface>`` entries in the local routing
        table whose ``<ip>`` is no longer present on ``<iface>``.

        The failure this catches: v0.5.286 (ARP-J6) installs
        ``ip route add local <ip>/32 dev <iface> ...`` explicitly
        to guarantee the kernel treats the anchor IP as ours,
        even under some VRF-race scenarios where the auto-install
        missed. But when the address is later removed from the
        interface (via ``ip addr del``, whether operator manual
        or pre-v0.5.290 ``_remove_ipv4_address``), the kernel
        does NOT auto-remove application-installed local routes.
        The route persists as a ghost. Kernel still treats the
        ghost IP as netgen'''s own → drops incoming packets
        whose src IP matches the ghost as suspected spoofing
        (martian source) → dnsmasq never sees relayed DHCP
        requests from switches whose IP happens to match a
        previous netgen anchor.

        Operator on srv06 2026-09-11 hit this exact tail after
        v0.5.289 (bind-dynamic) landed: config was correct,
        socket was in the right VRF, packets reached vlan10 —
        but dnsmasq still saw ZERO transactions because
        ``local 192.16.30.1 dev vlan10`` remained after
        ``ip addr del 192.16.30.1/24 dev vlan10``.

        WARN-only (never auto-delete). The remediation the log
        line names: ``sudo ip route del local <ip> dev <iface>
        table local``. Restarting the DHCP-server device also
        triggers v0.5.290 fix in ``_remove_ipv4_address`` which
        cleans the route on the stop path.
        """
        try:
            _res = subprocess.run(
                ["ip", "route", "show", "table", "local"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception as _exc:
            logger.debug(
                f"[ARP MONITOR] local-table drift scan: "
                f"ip route show failed: {_exc}"
            )
            return
        _out = (_res.stdout or "")
        # Parse lines of the form:
        #   local <ip> dev <iface> proto kernel scope host src <ip>
        # We care about `local <ip> dev <iface>`.
        _ghosts = []
        for _line in _out.splitlines():
            _tokens = _line.strip().split()
            if len(_tokens) < 4 or _tokens[0] != "local":
                continue
            try:
                _dev_idx = _tokens.index("dev")
            except ValueError:
                continue
            if _dev_idx + 1 >= len(_tokens):
                continue
            _ip = _tokens[1]
            _iface = _tokens[_dev_idx + 1]
            # Check whether _ip is currently on _iface.
            try:
                _addr = subprocess.run(
                    ["ip", "-4", "-o", "addr", "show", "dev", _iface],
                    capture_output=True, text=True, timeout=3,
                )
                _addr_out = (_addr.stdout or "")
            except Exception:
                continue
            # `inet <ip>/<pfx>` — grep for the ip followed by /
            if f" {_ip}/" not in _addr_out:
                _ghosts.append((_ip, _iface))
        if not _ghosts:
            logger.debug(
                "[ARP MONITOR] local-table drift scan: no ghosts"
            )
            return
        for _ip, _iface in _ghosts:
            logger.warning(
                f"[ARP MONITOR] LOCAL-TABLE GHOST: "
                f"`local {_ip} dev {_iface}` is in table local "
                f"but {_ip} is NOT currently on {_iface}. Kernel "
                f"still treats {_ip} as a local address; incoming "
                f"packets with src={_ip} will be dropped as "
                f"martian, silently breaking any DHCP relay or "
                f"protocol whose peer owns that IP. Fix: "
                f"`sudo ip route del local {_ip} dev {_iface} "
                f"table local` — or stop/start the DHCP-server "
                f"device on {_iface} to trigger v0.5.290 cleanup."
            )
    def _monitor_loop(self):
        """Main monitoring loop."""
        logger.info("[ARP MONITOR] Monitoring loop started")

        # Startup settle: the Flask server in the same process binds on
        # port 5050 a moment after the monitor thread is launched. If we
        # poll immediately we hit `Connection refused` for the first
        # iteration and log it as ERROR even though it's a transient
        # race. Wait a short window to give Flask time to come up.
        if self.stop_event.wait(5):
            return
        self._startup_window_until = time.monotonic() + 25  # tolerate refused for 25s more

        while not self.stop_event.is_set():
            try:
                # Get all devices that need ARP monitoring
                devices = self._get_arp_devices()

                if devices:
                    logger.info(f"[ARP MONITOR] Checking ARP status for {len(devices)} devices")
                    self._check_arp_status_batch(devices)
                else:
                    logger.debug("[ARP MONITOR] No ARP devices found")

                # Wait for next check interval
                if self.stop_event.wait(self.check_interval):
                    break

            except Exception as e:
                logger.error(f"[ARP MONITOR] Error in monitoring loop: {e}")
                # Continue monitoring even if there's an error
                if self.stop_event.wait(5):  # Wait 5 seconds before retrying
                    break

        logger.info("[ARP MONITOR] Monitoring loop ended")
    
    def _get_arp_devices(self) -> List[Dict[str, Any]]:
        """Get all devices that need ARP monitoring."""
        try:
            devices = self.device_db.get_all_devices()
            arp_devices = []
            
            for device in devices:
                # Check if device is running and has IP addresses configured
                if (device.get('status') == 'Running' and 
                    (device.get('ipv4_address') or device.get('ipv6_address'))):
                    arp_devices.append(device)
            
            return arp_devices
            
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error getting ARP devices: {e}")
            return []
    
    def _check_arp_status_batch(self, devices: List[Dict[str, Any]]):
        """Check ARP status for multiple devices in parallel."""
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all ARP status check tasks
                future_to_device = {
                    executor.submit(self._check_single_device_arp_status, device): device 
                    for device in devices
                }
                
                # Process completed tasks
                for future in as_completed(future_to_device):
                    device = future_to_device[future]
                    try:
                        arp_status = future.result()
                        if arp_status:
                            self._update_device_arp_status(device['device_id'], arp_status)
                    except Exception as e:
                        logger.error(f"[ARP MONITOR] Error checking ARP status for device {device['device_id']}: {e}")
                        
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error in batch ARP status check: {e}")
    
    def _check_single_device_arp_status(self, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check ARP status for a single device."""
        device_id = device['device_id']
        
        try:
            # Call the server's ARP status API
            response = requests.get(
                f"{self.server_url}/api/device/arp/{device_id}",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract ARP status information
                arp_status = {
                    'arp_resolved': data.get('arp_resolved', False),
                    'arp_ipv4_resolved': data.get('arp_ipv4_resolved', False),
                    'arp_ipv6_resolved': data.get('arp_ipv6_resolved', False),
                    'arp_gateway_resolved': data.get('arp_gateway_resolved', False),
                    'arp_status': data.get('arp_status', 'Unknown'),
                    'last_check': datetime.now(timezone.utc).isoformat(),
                    'details': data.get('details', {})
                }
                
                return arp_status
            else:
                logger.warning(f"[ARP MONITOR] Failed to get ARP status for device {device_id}: {response.status_code}")
                return None
                
        except requests.exceptions.RequestException as e:
            # During the first few seconds after netgen-server starts,
            # Flask hasn't bound :5050 yet → ConnectionRefused. Don't
            # spam the log with ERROR for that — downgrade to debug
            # while still inside the startup window.
            is_startup_blip = (
                isinstance(e, requests.exceptions.ConnectionError)
                and time.monotonic() < getattr(self, "_startup_window_until", 0)
            )
            if is_startup_blip:
                logger.debug(f"[ARP MONITOR] startup-window connection-refused for {device_id}; ignoring")
            else:
                logger.error(f"[ARP MONITOR] Network error checking ARP status for device {device_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"[ARP MONITOR] Error checking ARP status for device {device_id}: {e}")
            return None
    
    def _update_device_arp_status(self, device_id: str, arp_status: Dict[str, Any]):
        """Update ARP status in the database.

        v0.5.262 (audit ARP-4 + ARP-7): the 4 writes below (device
        statistics, devices row, event log, state transition) each
        hold their own connection + transaction. Interleaved writers
        (monitor + on-demand refresh for the same device) can leave
        torn state where the two tables disagree. Serialize per
        device_id so a single writer completes its 4 steps before
        another starts.
        """
        _lock = _arp_write_lock_for(device_id)
        with _lock:
            try:
                # Update device statistics
                self.device_db.update_device_statistics(device_id, {
                    'arp_resolved': arp_status['arp_resolved'],
                    'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                    'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                    'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                    'last_arp_check': arp_status['last_check']
                })

                # Update main devices table with ARP status
                self.device_db.update_device(device_id, {
                    'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                    'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                    'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                    'arp_status': arp_status['arp_status'],
                    'last_arp_check': arp_status['last_check']
                })

                # v0.5.262 (audit ARP-10): dedup log_device_event —
                # only write when arp_status actually changed since
                # the last logged event for this device_id. Pre-fix
                # this fired every 30 s per device regardless of
                # state, dominating device_events table growth.
                current_status = arp_status.get('arp_status') or "Unknown"
                with _LAST_ARP_STATUS_LOGGED_LOCK:
                    _prev_logged = _LAST_ARP_STATUS_LOGGED.get(device_id)
                    _should_log = _prev_logged != current_status
                    if _should_log:
                        _LAST_ARP_STATUS_LOGGED[device_id] = current_status
                if _should_log:
                    self.device_db.log_device_event(device_id, "arp_status_check", {
                        'arp_resolved': arp_status['arp_resolved'],
                        'arp_ipv4_resolved': arp_status['arp_ipv4_resolved'],
                        'arp_ipv6_resolved': arp_status['arp_ipv6_resolved'],
                        'arp_gateway_resolved': arp_status['arp_gateway_resolved'],
                        'arp_status': arp_status['arp_status'],
                        'details': arp_status['details'],
                        'transition_from': _prev_logged,
                    })

                # Per-protocol state-history timeline (de-dup'd against last row).
                try:
                    self.device_db.add_state_transition(
                        device_id,
                        "arp",
                        arp_status.get('arp_status') or "Unknown",
                        detail={
                            "ipv4": arp_status.get('arp_ipv4_resolved'),
                            "ipv6": arp_status.get('arp_ipv6_resolved'),
                            "gateway": arp_status.get('arp_gateway_resolved'),
                        },
                    )
                except Exception as _e:
                    logger.debug(f"[ARP MONITOR] state-history insert skipped: {_e}")

                logger.debug(f"[ARP MONITOR] Updated ARP status for device {device_id}: {arp_status['arp_status']}")

            except Exception as e:
                logger.error(f"[ARP MONITOR] Error updating ARP status for device {device_id}: {e}")
    
    def force_check_all(self):
        """Force an immediate ARP status check for all devices."""
        if not self.is_running:
            logger.warning("[ARP MONITOR] Monitor is not running, cannot force check")
            return
        
        devices = self._get_arp_devices()
        if devices:
            logger.info(f"[ARP MONITOR] Force checking ARP status for {len(devices)} devices")
            self._check_arp_status_batch(devices)
        else:
            logger.info("[ARP MONITOR] No devices found for ARP force check")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current monitor status."""
        return {
            "is_running": self.is_running,
            "check_interval": self.check_interval,
            "max_workers": self.max_workers,
            "server_url": self.server_url,
            "thread_alive": self.monitor_thread.is_alive() if self.monitor_thread else False
        }
