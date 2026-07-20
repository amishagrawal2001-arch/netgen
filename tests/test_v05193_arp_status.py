"""v0.5.193: ARP status logic — two bug fixes.

Bugs, both surfacing as "device chip stays yellow forever":

  1. `get_device_arp_status` skipped `ip vrf exec` when pinging the
     device's own IP, based on a false claim that self-ping loops
     across VRFs. In reality each VRF has its own local table, so
     the bare `ping 192.168.X.Y` returns "Network is unreachable"
     for any address that only lives inside the device VRF. Fix
     wraps the self-ping in the VRF prefix (verified on srv01:
     bare ping → 100% loss, vrf-exec ping → 0.024 ms).

  2. `requires_ipv6` was set True whenever any protocol config had
     `ipv6_enabled=True` (the dual-stack default), even when the
     device had no IPv6 address to probe. That made every IPv4-
     only device sit at Failed/yellow. Fix: derive `requires_ipv6`
     from the presence of `ipv6_address`/`ipv6_gateway` only.

Same class of leak in `bgp_established` at the device_db layer
(bgp_monitor never wrote the column even though it exists in the
`devices` schema). Covered by a separate test below.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Redirect the SQLite DB into a temp dir BEFORE run_tgen_server imports it
# (constructor runs at import time and would try to mkdir /opt/netgen).
os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05193_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# Test 1: self-ping is wrapped in the device VRF
# ─────────────────────────────────────────────────────────────────────

def _run_arp_status(device, vrf_name, subprocess_side_effect):
    """Invoke get_device_arp_status with a fake device + fake VRF.

    Returns (json_body, all_subprocess_call_args_lists).
    """
    from flask import Flask
    import run_tgen_server as srv

    # Wire fake DB and FRR VRF resolver.
    fake_frr = MagicMock()
    fake_frr.vrf_name_for_device.return_value = vrf_name

    calls = []

    def _record(*args, **kwargs):
        # args[0] is the command list.
        calls.append(list(args[0]))
        return subprocess_side_effect(args[0])

    with patch.object(srv, "device_db") as db, \
         patch("run_tgen_server.subprocess.run", side_effect=_record), \
         patch("utils.frr_docker.FRRDockerManager", return_value=fake_frr):
        db.get_device.return_value = device
        app = srv.app
        with app.test_client() as client:
            resp = client.get(f"/api/arp/monitor/status/{device['device_id']}")
            # /api/arp/monitor/status/<id> is the actual route name.
            # If the resolver changed, fall back to the direct call.
            if resp.status_code == 404:
                with app.test_request_context():
                    resp_obj = srv.get_device_arp_status(device["device_id"])
                    # Flask endpoint returns (body, status).
                    body, status = resp_obj
                    return body.get_json(), calls, status
            return resp.get_json(), calls, resp.status_code


def _fake_iface_up(cmd):
    """Return a subprocess-like object for ip link show + ping calls."""
    class R:
        def __init__(self, rc=0, out="", err=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    if cmd[:3] == ["ip", "link", "show"]:
        return R(0, f"5: {cmd[-1]}: <BROADCAST> mtu 1500 state UP", "")
    if cmd[:3] == ["ip", "-o", "link"]:
        # VRF existence probe — say yes.
        return R(0, f"13: {cmd[-1]}: <NOARP,MASTER,UP,LOWER_UP>", "")
    if "ping" in cmd[0] or cmd[0] == "ping":
        return R(0, "1 received", "")
    if len(cmd) > 3 and cmd[3] == "ping":
        # ip vrf exec vrf-X ping ...
        return R(0, "1 received", "")
    if "ping6" in cmd or (len(cmd) > 3 and cmd[3] == "ping6"):
        return R(0, "1 received", "")
    return R(0, "", "")


def test_self_ping_wrapped_in_vrf():
    """Bug 1: self-ping to device IP must run inside the device VRF."""
    device = {
        "device_id": "vrf-test-1",
        "status": "Running",
        "server_interface": "vlan200",
        "ipv4_address": "192.168.200.2",
        "ipv4_gateway": "192.168.200.1",
        "ipv6_address": "",
        "ipv6_gateway": "",
        "bgp_config": {},
        "ospf_config": {},
        "isis_config": {},
    }
    body, calls, status = _run_arp_status(device, "vrf-abc123", _fake_iface_up)
    assert status == 200, body

    # Find any ping calls for 192.168.200.2 (self).
    self_pings = [c for c in calls if "192.168.200.2" in c and ("ping" in c[0] or "ping" in c)]
    assert self_pings, f"no self-ping invoked: {calls}"

    # EVERY self-ping to the device's own IP must be VRF-wrapped.
    for c in self_pings:
        assert c[:4] == ["ip", "vrf", "exec", "vrf-abc123"], (
            f"self-ping NOT wrapped in VRF: {c}"
        )


# ─────────────────────────────────────────────────────────────────────
# Test 2: requires_ipv6 does NOT infer from bgp_config.ipv6_enabled
# ─────────────────────────────────────────────────────────────────────

def test_ipv4_only_device_ignores_bgp_dual_stack_flag():
    """Bug 2: an IPv4-only device with dual-stack BGP config should
    still resolve to arp_status='Resolved' when IPv4 comes up. The
    old code treated `bgp_config.ipv6_enabled=True` as forcing an
    IPv6 requirement even when no v6 address existed → always Failed."""
    device = {
        "device_id": "v4only-dualstack-bgp",
        "status": "Running",
        "server_interface": "vlan200",
        "ipv4_address": "192.168.200.2",
        "ipv4_gateway": "192.168.200.1",
        "ipv6_address": "",  # <- no v6 address
        "ipv6_gateway": "",
        # But BGP dual-stack flag is on — netgen's default.
        "bgp_config": {"ipv4_enabled": True, "ipv6_enabled": True},
        "ospf_config": {"ipv4_enabled": True, "ipv6_enabled": True},
        "isis_config": {},
    }
    body, calls, status = _run_arp_status(device, "vrf-xyz", _fake_iface_up)
    assert status == 200, body

    assert body["arp_status"] == "Resolved", (
        f"IPv4-only device with dual-stack config was marked "
        f"{body['arp_status']!r} — should be Resolved. "
        f"Details: {body.get('details')}"
    )
    assert body["arp_resolved"] is True


# ─────────────────────────────────────────────────────────────────────
# Test 3: bgp_established column is now in the devices table update map
# ─────────────────────────────────────────────────────────────────────

def test_bgp_established_in_update_field_mapping():
    """Bug 3: `bgp_established` was commented out of the update_device
    field mapping. That pinned the top-level rollup at False even
    when IPv4 was Established, driving the BGP chip yellow. This
    test locks the mapping in so a future rewrite doesn't drop it
    again."""
    import inspect
    from utils import device_database

    src = inspect.getsource(device_database.DeviceDatabase.update_device)
    # Field-mapping entry must be uncommented and present.
    assert "'bgp_established': 'bgp_established'" in src, (
        "bgp_established is missing from update_device's field mapping — "
        "the rollup will stay pinned at False forever."
    )
    assert "'bgp_established': 'bgp_established',  # Removed" not in src, (
        "someone re-added the wrong old comment."
    )


def test_bgp_monitor_writes_bgp_established_to_devices_table():
    """Bug 3 companion: bgp_monitor's update_data dict must include
    bgp_established (not commented out) so the rollup lands in the
    `devices` table, which is what `get_all_devices` reads."""
    import inspect
    from utils import bgp_monitor

    src = inspect.getsource(bgp_monitor.BGPStatusMonitor._update_device_bgp_status)
    assert "'bgp_established': bgp_status['bgp_established']" in src, (
        "bgp_monitor no longer writes bgp_established to the devices table."
    )
    assert "# 'bgp_established': bgp_status['bgp_established']" not in src, (
        "the old commented-out line reappeared."
    )
