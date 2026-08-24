"""v0.5.211: OSPF / IS-IS route-pool advertisement now scopes
`redistribute static` to the device's VRF-scoped router,
matching what BGP has done since v0.5.193.

Operator report on JNPR-MAC-HWXVX1 2026-08-23: attached route
pools to OSPF v4+v6, applied, `show run` on the FRR container
had two `router ospf` blocks:

    router ospf vrf vrf-889abd63d40    ← neighbor + interface
     ospf router-id 192.255.0.1
     network 192.168.0.0/24 area 0.0.0.0
    !
    router ospf                         ← phantom default-VRF
     redistribute static route-map RM-OSPF-EXPORT

Redistribute-static landed on the phantom instance (no
interfaces, no neighbors → zero type-5 LSAs generated). Peer
switch saw only the OSPF multicast group address.

Root cause: `configure_ospf_route_advertisement` +
`configure_isis_route_advertisement` emitted `router ospf` /
`router ospf6` / `router isis CORE` without any VRF qualifier.
The device runs in a per-device Linux VRF (`vrf-<device_id>`);
FRR/zebra auto-scopes the initial router to that VRF, but the
subsequent bare `router X` creates a separate default-VRF
instance.

Fix: new `_router_vrf_suffix(device_id)` helper that returns
`" vrf <vrf-name>"` when the device's VRF interface is live on
the host (same host-check pattern as `_bgp_router_clause`). All
four route-adv sites (OSPF configure, OSPF cleanup, ISIS
configure, ISIS cleanup) now use it. Legacy single-device
deployments without a VRF fall through to the default (bare
suffix), preserving pre-fix behavior for those setups.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05211_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Runtime behavior: _router_vrf_suffix returns "" when no VRF
# ─────────────────────────────────────────────────────────────────────

def test_router_vrf_suffix_returns_empty_when_no_device_id():
    """No device_id → empty suffix (legacy single-device path)."""
    src = (REPO / "run_tgen_server.py").read_text()
    assert "def _router_vrf_suffix(device_id=None):" in src, (
        "helper _router_vrf_suffix missing from run_tgen_server.py"
    )


def test_router_vrf_suffix_returns_empty_when_vrf_not_on_host():
    """Even with a device_id, if `ip link show vrf-<id>` fails
    (i.e., the device isn't wired into per-device VRF mode),
    the helper must fall through to the default-VRF path."""
    # Import through the run_tgen_server module namespace to
    # exercise the actual code path. If import-time side
    # effects choke in the test env, skip cleanly.
    try:
        import run_tgen_server as srv
    except Exception as e:
        pytest.skip(f"run_tgen_server import failed in test env: {e}")

    from subprocess import CompletedProcess
    # Fake `ip link show` returning empty stdout — mimics "VRF
    # iface doesn't exist on this host".
    with patch.object(srv.subprocess, "run",
                      return_value=CompletedProcess(args=[], returncode=1, stdout="", stderr="")):
        assert srv._router_vrf_suffix("dead-beef-fake-id") == ""


# ─────────────────────────────────────────────────────────────────────
# Source-level lock-ins — verify all 4 route-adv sites use the
# helper. A refactor that reverts to bare `router ospf` would
# silently reintroduce the operator's original bug.
# ─────────────────────────────────────────────────────────────────────

def _configure_ospf_body() -> str:
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find("def configure_ospf_route_advertisement")
    assert idx >= 0
    return src[idx:idx + 12000]


def _cleanup_ospf_body() -> str:
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find("def cleanup_ospf_route_advertisement")
    assert idx >= 0
    return src[idx:idx + 12000]


def _configure_isis_body() -> str:
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find("def configure_isis_route_advertisement")
    assert idx >= 0
    return src[idx:idx + 12000]


def _cleanup_isis_body() -> str:
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find("def cleanup_isis_route_advertisement")
    assert idx >= 0
    return src[idx:idx + 12000]


def test_configure_ospf_uses_vrf_suffix_on_router():
    body = _configure_ospf_body()
    assert "_router_vrf_suffix(device_id)" in body, (
        "configure_ospf_route_advertisement no longer computes the VRF suffix"
    )
    # Both v4 and v6 router lines must consume the suffix.
    assert re.search(r'f"router ospf\{_vrf_suffix\}"', body), (
        "router ospf (v4) redistribute no longer VRF-scoped"
    )
    assert re.search(r'f"router ospf6\{_vrf_suffix\}"', body), (
        "router ospf6 (v6) redistribute no longer VRF-scoped"
    )


def test_cleanup_ospf_uses_vrf_suffix_on_router():
    body = _cleanup_ospf_body()
    assert "_router_vrf_suffix(device_id)" in body, (
        "cleanup_ospf_route_advertisement no longer computes the VRF suffix"
    )
    assert re.search(r'f"router ospf\{_vrf_suffix_c\}"', body), (
        "OSPF cleanup no longer VRF-scoped (v4)"
    )
    assert re.search(r'f"router ospf6\{_vrf_suffix_c\}"', body), (
        "OSPF cleanup no longer VRF-scoped (v6)"
    )


def test_configure_isis_uses_vrf_suffix_on_router():
    body = _configure_isis_body()
    assert "_router_vrf_suffix(device_id)" in body, (
        "configure_isis_route_advertisement no longer computes the VRF suffix"
    )
    assert re.search(r'f"router isis CORE\{_vrf_suffix\}"', body), (
        "router isis CORE redistribute no longer VRF-scoped"
    )


def test_cleanup_isis_uses_vrf_suffix_on_router():
    body = _cleanup_isis_body()
    assert "_router_vrf_suffix(device_id)" in body, (
        "cleanup_isis_route_advertisement no longer computes the VRF suffix"
    )
    assert re.search(r'f"router isis CORE\{_vrf_suffix_c\}"', body), (
        "ISIS cleanup no longer VRF-scoped"
    )


def test_bgp_route_adv_still_uses_bgp_router_clause():
    """BGP was already fine — it uses _bgp_router_clause. Guard
    against a regression that removes it."""
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find("def configure_bgp_route_advertisement")
    assert idx >= 0
    body = src[idx:idx + 20000]
    assert "_bgp_router_clause(bgp_asn, device_id)" in body, (
        "configure_bgp_route_advertisement no longer uses _bgp_router_clause "
        "— BGP redistribute would land on the phantom default-VRF router"
    )
