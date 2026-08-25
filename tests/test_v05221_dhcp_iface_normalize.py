"""v0.5.221: DHCP entry points normalize the interface name so
that callers passing the display form ``vlan200@ens2f0np0``
don't crash dnsmasq/dhclient with ENODEV.

Operator report on JNPR-MAC-HWXVX1 2026-08-24: DHCP-server
device on VLAN 200 showed ``dhcp_state="Server Down"`` in the
DHCP status table even though ``docker ps`` reported the
container as ``(healthy)``. The healthcheck (``exit 0``)
doesn't know whether dnsmasq is alive; the monitor's per-
container pgrep found no dnsmasq process because the launch
had exited on ENODEV.

Root cause: ``apply_device`` in ``run_tgen_server.py``
(line ~5537) and ``start_device`` (line ~2645) pass
``iface_name`` — which for VLAN devices is the DISPLAY form
``vlan200@ens2f0np0`` (see the pair with
``iface_name_for_commands`` = ``vlan200`` at
run_tgen_server.py:4375-4379). The display form is what
``ip link show`` prints for VLAN sub-interfaces, NOT the
kernel interface name. It exceeds ``IFNAMSIZ`` (16 bytes
including NUL), so any ``if_nametoindex()`` lookup returns
ENODEV — dnsmasq's ``bind-interfaces`` fails to attach the
raw socket, dnsmasq exits immediately after launch
(returncode still 0 because the parent forked before the
child died). Same failure mode for ``dhclient <iface>``.

Fix: add ``_normalize_iface_name`` boundary helper in
``utils/dhcp.py`` and call it at the top of every entry point
(``start_dhcp_client``, ``start_dhcp_server``,
``stop_dhcp_client``, ``stop_dhcp_server``). No-op for
correctly-formed inputs.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05221_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Runtime tests — the helper does what we say
# ─────────────────────────────────────────────────────────────────────

def test_normalize_strips_at_parent_suffix():
    from utils.dhcp import _normalize_iface_name
    assert _normalize_iface_name("vlan200@ens2f0np0") == "vlan200"


def test_normalize_noop_on_kernel_form():
    from utils.dhcp import _normalize_iface_name
    assert _normalize_iface_name("vlan200") == "vlan200"
    assert _normalize_iface_name("ens2f0np0") == "ens2f0np0"


def test_normalize_noop_on_empty_or_none():
    from utils.dhcp import _normalize_iface_name
    assert _normalize_iface_name("") == ""


def test_normalize_handles_multi_at():
    """Only the first @ is the display separator — anything
    beyond it (unlikely, but let's be tolerant)."""
    from utils.dhcp import _normalize_iface_name
    assert _normalize_iface_name("vlan200@ens0@extra") == "vlan200"


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins — every entry point calls the helper
# ─────────────────────────────────────────────────────────────────────

def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _slice(src: str, marker: str, width: int = 1000) -> str:
    idx = src.find(marker)
    assert idx >= 0, f"marker {marker!r} not found"
    return src[idx:idx + width]


def test_start_dhcp_client_normalizes():
    body = _slice(_dhcp_src(), "def start_dhcp_client(", 2000)
    assert "_normalize_iface_name(interface)" in body, (
        "start_dhcp_client no longer normalizes — display-form "
        "interface will crash dhclient on VLAN devices"
    )


def test_start_dhcp_server_normalizes():
    body = _slice(_dhcp_src(), "def start_dhcp_server(", 2000)
    assert "_normalize_iface_name(interface)" in body, (
        "start_dhcp_server no longer normalizes — display-form "
        "interface will crash dnsmasq on VLAN devices"
    )


def test_stop_dhcp_client_normalizes():
    body = _slice(_dhcp_src(), "def stop_dhcp_client(", 1500)
    assert "_normalize_iface_name(interface)" in body, (
        "stop_dhcp_client no longer normalizes — pidfile / lease "
        "path lookups will miss the correct file"
    )


def test_stop_dhcp_server_normalizes():
    body = _slice(_dhcp_src(), "def stop_dhcp_server(", 1500)
    assert "_normalize_iface_name(interface)" in body, (
        "stop_dhcp_server no longer normalizes — dnsmasq pidfile "
        "lookup will miss the correct file"
    )


def test_normalize_helper_exists():
    body = _slice(_dhcp_src(), "def _normalize_iface_name(", 3000)
    assert body, "_normalize_iface_name helper missing"
    # Sanity: helper actually reads the @ boundary.
    assert '"@"' in body or "'@'" in body
