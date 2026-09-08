"""v0.5.287 — Anchor-gateway collision: netgen must never claim
the gateway IP on its own interface.

Operator on srv06 2026-09-07: switch's ARP requests for the DHCP
anchor 172.16.30.2 (on vlan10, VRF-slaved) arrived at netgen's
NIC (confirmed via tcpdump), but netgen sent no ARP replies.
After 16+ ships chasing sysctl / local-table / VRF hypotheses,
the root cause was: a prior DHCP-server device had anchored
172.16.30.1 (the switch's own IP) on the parent NIC ens2f0np0
because pre-fix `_ensure_ipv4_address` explicitly set
`server_ip = gateway` when gateway landed inside the pool. The
kernel then routed netgen's ARP replies via the parent NIC's
connected route out UNTAGGED, and the switch's trunk port for
VID 10 dropped them silently. Removing 172.16.30.1 from
ens2f0np0 immediately restored ARP replies.

Three fixes:
- A: pick the first non-gateway host from the pool (never anchor
  server_ip == gateway).
- B: on device stop, sweep the parent NIC too so anchor drift
  from a prior no-VLAN device gets cleaned up.
- C: at ARP monitor start, WARN when a parent NIC holds an IP
  that belongs on an active subif (drift detection; never
  auto-delete).
"""

from __future__ import annotations

import ipaddress
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05287_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────
# Fix A — server_ip must never equal gateway (behavioral)
# ─────────────────────────────────────────────────────────────────

def test_a_skips_gateway_uses_second_host():
    """gateway=.1 inside pool → server_ip must be .2, not .1."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "172.16.30.10", "172.16.30.200",
                gateway="172.16.30.1", container=None,
            )
    assert picked == "172.16.30.2", (
        f"expected .2 (gateway=.1 skipped), got {picked!r}"
    )


def test_a_gateway_outside_pool_uses_dot1():
    """gateway is NOT in pool → picker is unchanged from pre-fix,
    uses .1 (first host, and it's not the gateway)."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "192.168.30.10", "192.168.30.200",
                gateway="10.0.0.1", container=None,  # outside pool
            )
    assert picked == "192.168.30.1"


def test_a_no_gateway_uses_dot1():
    """No gateway at all → server_ip = .1 (unchanged pre-fix)."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "192.168.30.10", "192.168.30.200",
                gateway="", container=None,
            )
    assert picked == "192.168.30.1"


def test_a_gateway_dot2_makes_server_pick_dot1():
    """gateway=.2 → picker returns .1 (first host in iteration
    order that is NOT the gateway)."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "192.168.30.10", "192.168.30.200",
                gateway="192.168.30.2", container=None,
            )
    assert picked == "192.168.30.1"


def test_a_slash31_pool_picks_non_gateway_endpoint():
    """/31 pool with hosts()==[] uses the two endpoints. If gateway
    is one endpoint, the other is picked."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            # pool 10.0.0.0-10.0.0.1 → /31, endpoints .0 and .1.
            picked = m._ensure_ipv4_address(
                "vlan10", "10.0.0.0", "10.0.0.1",
                gateway="10.0.0.0", container=None,
            )
    assert picked == "10.0.0.1", (
        f"gateway=.0 on /31 → expected .1, got {picked!r}"
    )


def test_a_slash32_pool_that_is_gateway_returns_none():
    """/32 pool whose sole host IS the gateway → nothing usable to
    anchor. Return None (caller surfaces to dhcp_last_error)."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      return_value=MagicMock(returncode=0, stdout="", stderr="")):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "10.0.0.5", "10.0.0.5",
                gateway="10.0.0.5", ipv4_mask="32", container=None,
            )
    assert picked is None


def test_a_source_marker_present():
    """Sanity: the v0.5.287 marker is in the source so future
    audits can grep for it."""
    src = (REPO / "utils" / "dhcp.py").read_text()
    assert "v0.5.287 (audit anchor-gateway-collision)" in src


# ─────────────────────────────────────────────────────────────────
# Fix B — cleanup on stop sweeps parent NIC too
# ─────────────────────────────────────────────────────────────────

def test_b_iface_parent_lifts_parent_from_display_form():
    """`ip -o link show vlan10` outputs a header like
    `56: vlan10@ens2f0np0: <BROADCAST,...> mtu 1500 ...`. The
    helper must lift ens2f0np0 from that."""
    from utils import dhcp as m
    fake = MagicMock(stdout=(
        "56: vlan10@ens2f0np0: <BROADCAST,MULTICAST,UP,LOWER_UP> "
        "mtu 1500 qdisc noqueue master vrf-b7a16713f24 state UP"
    ))
    with patch.object(m, "_run_command", return_value=fake):
        parent = m._iface_parent("vlan10", container=None)
    assert parent == "ens2f0np0"


def test_b_iface_parent_returns_none_for_physical_nic():
    """Parent NICs have no `@parent` in their header — return None
    so the cleanup caller knows there's no parent to sweep."""
    from utils import dhcp as m
    fake = MagicMock(stdout=(
        "8: ens2f0np0: <BROADCAST,MULTICAST,UP,LOWER_UP> "
        "mtu 1500 qdisc mq state UP"
    ))
    with patch.object(m, "_run_command", return_value=fake):
        parent = m._iface_parent("ens2f0np0", container=None)
    assert parent is None


def test_b_iface_parent_returns_none_on_empty_output():
    """Interface doesn't exist → empty stdout → None."""
    from utils import dhcp as m
    fake = MagicMock(stdout="")
    with patch.object(m, "_run_command", return_value=fake):
        parent = m._iface_parent("nonexistent99", container=None)
    assert parent is None


def test_b_iface_parent_returns_none_on_probe_exception():
    """Probe raises → return None cleanly, do not crash."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      side_effect=RuntimeError("subprocess died")):
        parent = m._iface_parent("vlan10", container=None)
    assert parent is None


def test_b_stop_dhcp_server_calls_parent_sweep():
    """Source-level lock-in: stop_dhcp_server must invoke
    `_remove_matching_ipv4_anchors` on BOTH the subif and its
    parent. The parent sweep uses the same candidate set — the
    intersection gate in _remove_matching_ipv4_anchors makes it
    safe (unrelated management IPs on the parent don't match
    candidates and stay put)."""
    src = (REPO / "utils" / "dhcp.py").read_text()
    idx = src.find("def stop_dhcp_server(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    # Both the subif and parent sweep must appear.
    assert body.count("_remove_matching_ipv4_anchors(") >= 2, (
        "stop_dhcp_server must call _remove_matching_ipv4_anchors "
        "twice — once on subif, once on parent (v0.5.287 fix B)"
    )
    # And the parent sweep is guarded by _iface_parent.
    assert "_iface_parent(interface" in body
    # v0.5.287 marker documents the change.
    assert "v0.5.287 (audit anchor-gateway-collision, fix B)" in body


def test_b_parent_sweep_reuses_same_candidate_set():
    """The parent sweep must use the SAME `_candidate_anchors`
    that the subif sweep uses — recomputing would risk drift, and
    _collect_ipv4_anchor_candidates already includes the gateway
    as a candidate so it covers exactly the pre-v0.5.287 anchor
    that leaked onto the parent."""
    src = (REPO / "utils" / "dhcp.py").read_text()
    # Look inside stop_dhcp_server specifically, not the earlier
    # _iface_parent helper (which shares the marker prefix).
    stop_idx = src.find("def stop_dhcp_server(")
    end = src.find("\ndef ", stop_idx + 1)
    body = src[stop_idx:end]
    assert "_remove_matching_ipv4_anchors(" in body
    # Both sweep calls reference the shared variable.
    assert body.count("_candidate_anchors") >= 3, (
        "expected _candidate_anchors used in (a) collection, "
        "(b) subif sweep call, (c) parent sweep call"
    )
    assert "v0.5.287 (audit anchor-gateway-collision, fix B)" in body


# ─────────────────────────────────────────────────────────────────
# Fix C — startup drift-detect (WARN only, never delete)
# ─────────────────────────────────────────────────────────────────

def test_c_drift_scan_wired_into_start():
    """arp_monitor.start() must invoke _scan_parent_nic_drift
    after the replay so the operator sees drift warnings during
    every netgen-server restart."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    idx = src.find("def start(self):")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "self._scan_parent_nic_drift()" in body
    # Guarded so a scan crash never blocks the monitor.
    assert "parent-NIC drift scan raised" in body


def test_c_drift_scan_never_deletes():
    """Delete-safety invariant: the drift scan must not call any
    `ip addr del` / `_remove_ipv4_address` / `_remove_matching_
    ipv4_anchors`. It's a warning-only observer."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    idx = src.find("def _scan_parent_nic_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    for banned in (
        "_remove_ipv4_address",
        "_remove_matching_ipv4_anchors",
        '"ip", "addr", "del"',
        "'ip', 'addr', 'del'",
    ):
        assert banned not in body, (
            f"drift scan must never delete addresses "
            f"(found: {banned!r})"
        )


def test_c_drift_scan_filters_to_running_dhcp_servers():
    """Scan only Running + dhcp_mode=server devices — a stopped
    device's anchor set is stale."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    idx = src.find("def _scan_parent_nic_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert 'd.get("status") == "Running"' in body
    assert '"server"' in body


def test_c_drift_scan_uses_shared_helpers():
    """Reuse `_iface_parent`, `_iface_ipv4_addresses`, and
    `_collect_ipv4_anchor_candidates` — no re-derivation of anchor
    logic (avoids drift from the real cleanup path)."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    idx = src.find("def _scan_parent_nic_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "_iface_parent" in body
    assert "_iface_ipv4_addresses" in body
    assert "_collect_ipv4_anchor_candidates" in body


def test_c_drift_scan_warning_names_the_orphan_and_the_fix():
    """The warning must include: the parent NIC name, the orphan
    IP(s), the affected subif, and the operator's remediation
    (stop→start or manual ip addr del). Actionable telemetry."""
    src = (REPO / "utils" / "arp_monitor.py").read_text()
    idx = src.find("def _scan_parent_nic_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "DRIFT: parent NIC" in body
    assert "stop→start" in body or "stop->start" in body
    assert "ip addr del" in body


# ─────────────────────────────────────────────────────────────────
# Regression: v0.5.245 relay-mode still short-circuits
# ─────────────────────────────────────────────────────────────────

def test_relay_mode_still_skips_anchor():
    """v0.5.287 Fix A refactored the picker but must NOT regress
    the v0.5.245 relay-mode guard: when relay_return_hop is set,
    return None before picking anything."""
    from utils import dhcp as m
    picked = m._ensure_ipv4_address(
        "vlan10", "192.168.30.10", "192.168.30.200",
        gateway="192.168.30.1",
        relay_return_hop="172.16.30.10",
        container=None,
    )
    assert picked is None


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    import re
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m, "no version line in pyproject.toml"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 287), (
        f"version {major}.{minor}.{patch} < 0.5.287"
    )
