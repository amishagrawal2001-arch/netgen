"""v0.5.323 — regression guard: NO template shape produces
double-rendered warning + shared-banner in the Upstream Hint
scale output.

Operator asked (2026-09-14): "did you check other template
twice rendering issue" — worried a specific template
combination might hit a code path that renders the warning
banner + shared upstream config block twice. Audited by running
every plausible protocol-combination through render_all()
with count=2 and asserting warning + shared banner + device-
header line counts are exactly what they should be. Pins that
property so a future refactor can't regress.

Covers 9 shapes × 3 vendors = 27 renders:
  bare host, iBGP only, eBGP with v6, OSPF v4 only,
  OSPF dualstack, ISIS only, DHCP client, BGP+OSPF combined,
  BGP+OSPF+ISIS+DHCP kitchen-sink.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


_BASE = {
    "device_name": "netgen-device", "vlan": "10",
    "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
    "ipv4_gateway": "192.168.0.1",
    "ipv6_address": "2001:db8::2", "ipv6_mask": "64",
    "ipv6_gateway": "2001:db8::1",
    "mac_address": "00:11:22:33:44:55",
    "loopback_ipv4": "192.255.10.4",
    "loopback_ipv6": "2001:ff00:10::3",
}


def _bare():
    return dict(_BASE)


def _ibgp():
    d = _bare()
    d["bgp_config"] = {
        "bgp_local_as": "65000", "bgp_remote_asn": "65000",
        "bgp_hold_time": "90", "bgp_keepalive": "30",
        "ipv4_enabled": True, "ipv6_enabled": False,
    }
    return d


def _ebgp_v6():
    d = _bare()
    d["bgp_config"] = {
        "bgp_local_as": "65001", "bgp_remote_asn": "65000",
        "bgp_hold_time": "90", "bgp_keepalive": "30",
        "ipv4_enabled": True, "ipv6_enabled": True,
    }
    return d


def _ospf_v4():
    d = _bare()
    d["ospf_config"] = {
        "area_id": "0.0.0.0", "hello_interval": "10",
        "dead_interval": "40", "ipv4_enabled": True, "ipv6_enabled": False,
    }
    return d


def _ospf_dualstack():
    d = _bare()
    d["ospf_config"] = {
        "area_id": "0.0.0.0", "hello_interval": "10",
        "dead_interval": "40", "ipv4_enabled": True, "ipv6_enabled": True,
    }
    return d


def _isis():
    d = _bare()
    d["isis_config"] = {
        "isis_area": "CORE",
        "isis_net": "49.0001.1922.5500.0104.00",
        "isis_level": "level-2-only",
    }
    return d


def _dhcp_client():
    d = _bare()
    d["dhcp_mode"] = "client"
    d["dhcp_config"] = {"mode": "client"}
    return d


def _bgp_ospf_pe():
    d = _ibgp()
    d["ospf_config"] = {
        "area_id": "0.0.0.0", "hello_interval": "10",
        "dead_interval": "40", "ipv4_enabled": True, "ipv6_enabled": False,
    }
    return d


def _kitchen_sink():
    d = _bare()
    d["bgp_config"] = {
        "bgp_local_as": "65001", "bgp_remote_asn": "65000",
        "bgp_hold_time": "90", "bgp_keepalive": "30",
        "ipv4_enabled": True, "ipv6_enabled": True,
    }
    d["ospf_config"] = {
        "area_id": "0.0.0.0", "hello_interval": "10",
        "dead_interval": "40", "ipv4_enabled": True, "ipv6_enabled": True,
    }
    d["isis_config"] = {
        "isis_area": "CORE",
        "isis_net": "49.0001.1922.5500.0104.00",
        "isis_level": "level-2-only",
    }
    d["dhcp_mode"] = "client"
    d["dhcp_config"] = {"mode": "client"}
    return d


_SHAPES = [
    ("bare", _bare),
    ("ibgp_peer", _ibgp),
    ("ebgp_v6", _ebgp_v6),
    ("ospf_backbone_v4", _ospf_v4),
    ("ospf_dualstack", _ospf_dualstack),
    ("isis_l12", _isis),
    ("dhcp_client", _dhcp_client),
    ("bgp_ospf_pe", _bgp_ospf_pe),
    ("kitchen_sink", _kitchen_sink),
]


_VENDORS = ("juniper", "cisco", "arista")


@pytest.mark.parametrize("shape_name,shape_fn", _SHAPES)
@pytest.mark.parametrize("vendor", _VENDORS)
def test_warning_appears_exactly_once(shape_name, shape_fn, vendor):
    """The v0.5.322 SCALE COLLISION WARNING must fire EXACTLY once
    per render, never twice. `_dedupe_and_emit` prepends warnings
    at the top; if any code path appended a second copy, this
    would catch it."""
    devs = u.expand_for_scale(shape_fn(), {"count": 2})
    out = u.render_all(devs)[vendor]
    assert out.count("SCALE COLLISION WARNING") == 1, (
        f"{shape_name}/{vendor}: expected 1 warning, got "
        f"{out.count('SCALE COLLISION WARNING')}. Output:\n{out}"
    )


@pytest.mark.parametrize("shape_name,shape_fn", _SHAPES)
@pytest.mark.parametrize("vendor", _VENDORS)
def test_shared_banner_appears_exactly_once(shape_name, shape_fn, vendor):
    """The v0.5.320 shared-config banner must appear EXACTLY once.
    Two banners means the dedupe was invoked or emitted twice."""
    devs = u.expand_for_scale(shape_fn(), {"count": 2})
    out = u.render_all(devs)[vendor]
    assert out.count("Shared upstream config") == 1, (
        f"{shape_name}/{vendor}: expected 1 shared banner, got "
        f"{out.count('Shared upstream config')}. Output:\n{out}"
    )


@pytest.mark.parametrize("shape_name,shape_fn", _SHAPES)
@pytest.mark.parametrize("vendor", _VENDORS)
def test_device_header_count_matches_scale(shape_name, shape_fn, vendor):
    """One header comment per netgen device — count=2 means
    exactly 2 headers. More = double-render; fewer = dropped
    per-device section."""
    devs = u.expand_for_scale(shape_fn(), {"count": 2})
    out = u.render_all(devs)[vendor]
    marker = "#" if vendor == "juniper" else "!"
    n = out.count(f"{marker} Upstream config for netgen device")
    assert n == 2, (
        f"{shape_name}/{vendor}: expected 2 device headers, got {n}. "
        f"Output:\n{out}"
    )


def test_dialog_calls_render_all_once():
    """Regression guard against `UpstreamHintDialog` calling
    `render_all` more than once per open. Two calls would double
    the paste-body without any code-path bug."""
    src = (_REPO / "widgets" / "upstream_hint_dialog.py").read_text()
    assert src.count("render_all(device_data)") == 1, (
        "UpstreamHintDialog must call render_all exactly once per open"
    )


def test_only_one_upstream_hint_dialog_caller():
    """Regression guard: if multiple entry points invoke the
    dialog (e.g. a devices-table right-click AND the Add-Device
    button), we could inadvertently open it twice on the same
    click. Currently only Add-Device dialog opens it — pin that."""
    import subprocess
    result = subprocess.run(
        ["grep", "-rn", "UpstreamHintDialog(", "--include=*.py",
         str(_REPO / "widgets"), str(_REPO / "run_tgen_client.py")],
        capture_output=True, text=True,
    )
    # Class-def line + one instantiation site.
    class_defs = [l for l in result.stdout.splitlines() if "class UpstreamHintDialog" in l]
    instantiations = [l for l in result.stdout.splitlines()
                      if "UpstreamHintDialog(" in l and "class UpstreamHintDialog" not in l]
    assert len(class_defs) == 1
    # Exactly one instantiation site allowed. If a second is added
    # legitimately, update this test to allow it — but the update
    # must include a review that the two sites can't fire on one click.
    assert len(instantiations) == 1, (
        f"Multiple UpstreamHintDialog instantiation sites found — "
        f"verify they can't both fire on one operator click:\n{instantiations}"
    )


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
