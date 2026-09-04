"""Pre-bind safety checks for `/api/dpdk/bind`.

Binding a NIC to ``vfio-pci`` takes it out of the kernel — the
interface disappears from ``ip link``, no routes go over it, no SSH
session that lands on it survives. We've seen operators bind the
*management* interface by mistake and lock themselves out of the
server (1-2× per quarter). Cheap pre-flight to catch the obvious
ones:

  * **Management interface** — the iface carrying the default route,
    or the iface the operator's SSH session arrived on.
  * **Active stream** — any stream in the tracker emitting traffic
    through this iface is going to fail mid-test if the iface
    disappears.

Pure-function so unit-testable. The server passes in the candidate
iface name plus pre-built snapshots of:

  * ``default_route_iface`` — name of the iface that ``ip route show
    default`` resolves to (or None if not detected).
  * ``ssh_client_iface`` — name of the iface SSH_CLIENT IP routes
    back over (or None if no SSH context).
  * ``active_stream_ifaces`` — set / list of iface names with at
    least one active stream.

Returns ``None`` if safe; a short refusal-reason string otherwise.
The endpoint takes that reason, returns HTTP 409 with it, and the
GUI shows it with a "Bind anyway" escape hatch.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Set


def _resolve_underlying_ifaces(iface: str) -> Set[str]:
    """v0.5.253 (audit DPDK-6): return the set of physical interfaces
    that ``iface`` sits on top of, PLUS ``iface`` itself.

    Handles VLAN sub-interfaces (``eno1.100`` → ``{"eno1.100", "eno1"}``)
    and bond masters (``bond0`` with slaves ``eno1``/``eno2`` →
    ``{"bond0", "eno1", "eno2"}``), and bridge masters. Best-effort:
    on read failure returns ``{iface}`` alone.

    Pre-fix, ``check_bind_safe`` did plain string equality between the
    candidate iface and ``default_route_iface`` / ``ssh_client_iface``.
    Management on ``eno1.100`` with candidate ``eno1`` slipped through
    — the operator's ``eno1`` bind then killed the VLAN child and
    booted them off the box.
    """
    out: Set[str] = {iface}
    if not iface:
        return out
    base = "/sys/class/net"
    # VLAN sub-interface: /sys/class/net/<if>/proc/net/vlan/<if> exists;
    # more reliably, /proc/net/vlan/<if> exists with "Device: <parent>".
    try:
        vlan_path = f"/proc/net/vlan/{iface}"
        if os.path.exists(vlan_path):
            with open(vlan_path, "r") as f:
                for line in f:
                    if line.strip().startswith("Device:"):
                        parent = line.split(":", 1)[1].strip()
                        if parent:
                            out.add(parent)
                        break
    except Exception:
        pass
    # Bond master: /sys/class/net/<bond>/bonding/slaves lists slaves.
    try:
        slaves_path = f"{base}/{iface}/bonding/slaves"
        if os.path.exists(slaves_path):
            with open(slaves_path, "r") as f:
                for s in f.read().split():
                    if s:
                        out.add(s)
    except Exception:
        pass
    # Bridge master: /sys/class/net/<br>/brif/* are the bridge ports.
    try:
        brif_dir = f"{base}/{iface}/brif"
        if os.path.isdir(brif_dir):
            for e in os.listdir(brif_dir):
                if e:
                    out.add(e)
    except Exception:
        pass
    # Every other lower_* link (macvlan, team, veth stack…).
    try:
        for e in os.listdir(f"{base}/{iface}"):
            if e.startswith("lower_"):
                out.add(e[len("lower_"):])
    except Exception:
        pass
    return out


def check_bind_safe(
    iface: str,
    *,
    default_route_iface: Optional[str] = None,
    ssh_client_iface: Optional[str] = None,
    active_stream_ifaces: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Return None if it's safe to bind ``iface`` to vfio-pci, or a
    short refusal-reason string explaining why not.

    Pure: takes pre-collected snapshots, returns a string. The server
    builds the snapshots and calls this; the unit tests can exercise
    every combo without touching ``ip route`` or the stream tracker.
    """
    if not iface:
        return None  # nothing to check
    iface = str(iface).strip()
    if not iface:
        return None

    # v0.5.253 (audit DPDK-6): resolve the mgmt/SSH ifaces to their
    # underlying physical devices before the equality check. Binding
    # the *parent* of a VLAN/bond/bridge disables every child that
    # sits on top of it — same lockout risk as binding the child.
    def _mgmt_hits(mgmt: str) -> bool:
        mgmt = str(mgmt).strip()
        if not mgmt:
            return False
        if mgmt == iface:
            return True
        # mgmt could sit on top of iface (mgmt=eno1.100, iface=eno1).
        return iface in _resolve_underlying_ifaces(mgmt)

    if default_route_iface and _mgmt_hits(default_route_iface):
        return (
            f"'{iface}' carries the default route — binding it to "
            f"vfio-pci would drop all kernel networking on this host "
            f"(including the route the GUI is using to talk to it). "
            f"Bind a different NIC, or use Bind anyway if you really "
            f"mean it and have console access."
        )

    if ssh_client_iface and _mgmt_hits(ssh_client_iface):
        return (
            f"'{iface}' is the interface your SSH session is connected "
            f"over — binding it would kill the connection. Bind from "
            f"console, or use Bind anyway."
        )

    # Active stream — explicit set lookup so duplicates don't break us.
    if active_stream_ifaces:
        active = {str(s).strip() for s in active_stream_ifaces if s}
        if iface in active:
            return (
                f"'{iface}' has an active traffic stream running on it. "
                f"Stop the stream first, or use Bind anyway to take it "
                f"down mid-flight."
            )

    return None


def collect_default_route_iface(run=None) -> Optional[str]:
    """Best-effort: parse ``ip route show default`` for the device.

    Injectable `run` callable so the server-side wiring can mock it in
    tests. Real callers pass ``subprocess.run`` (with capture_output,
    text, timeout) and we extract the ``dev <iface>`` field.
    """
    if run is None:
        import subprocess as _sp
        run = lambda cmd: _sp.run(cmd, capture_output=True, text=True,
                                  timeout=5)
    try:
        result = run(["ip", "-o", "route", "show", "default"])
    except Exception:
        return None
    out = getattr(result, "stdout", "") or ""
    # Sample: "default via 10.0.0.1 dev eno1 proto static metric 100"
    parts = out.split()
    if "dev" in parts:
        i = parts.index("dev")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def collect_ssh_client_iface(ssh_client_env: Optional[str],
                             run=None) -> Optional[str]:
    """Resolve the iface that SSH_CLIENT's source IP routes back over.

    ``ssh_client_env`` is the raw ``$SSH_CLIENT`` value (usually
    ``"<src_ip> <src_port> <dst_port>"``). Returns the iface name or
    None if SSH wasn't used / the resolution failed.
    """
    if not ssh_client_env:
        return None
    # ``"   ".split()`` returns ``[]`` so guard before indexing — a
    # whitespace-only SSH_CLIENT shouldn't crash the bind path.
    parts = str(ssh_client_env).split()
    if not parts:
        return None
    src_ip = parts[0]
    if not src_ip:
        return None
    if run is None:
        import subprocess as _sp
        run = lambda cmd: _sp.run(cmd, capture_output=True, text=True,
                                  timeout=5)
    try:
        result = run(["ip", "-o", "route", "get", src_ip])
    except Exception:
        return None
    out = getattr(result, "stdout", "") or ""
    parts = out.split()
    if "dev" in parts:
        i = parts.index("dev")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None
