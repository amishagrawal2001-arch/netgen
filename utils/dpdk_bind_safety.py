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

from typing import Iterable, Optional


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

    # Management interface — two ways to detect it.
    if default_route_iface and iface == str(default_route_iface).strip():
        return (
            f"'{iface}' carries the default route — binding it to "
            f"vfio-pci would drop all kernel networking on this host "
            f"(including the route the GUI is using to talk to it). "
            f"Bind a different NIC, or use Bind anyway if you really "
            f"mean it and have console access."
        )

    if ssh_client_iface and iface == str(ssh_client_iface).strip():
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
