"""v0.5.324 — scale increment: sane defaults + loopback support.

Operator report on v0.5.323: even after all the dedupe + warning
work, the Upstream Hint STILL shows a collision warning by
default when they scale. Two root causes:

1. Dialog defaulted MAC/IPv4/IPv6/Loopback increment checkboxes
   to UNCHECKED. Combined with count=2 as the default scale, the
   very first scale run guarantees a collision warning.

2. `expand_for_scale` never implemented loopback increment even
   though the dialog has a Loopback checkbox — the `loopback`
   key was missing from the meta dict and from the increment
   logic. Even a user who ticks the Loopback box got identical
   loopbacks across N devices → OSPF/BGP router-id collision.

Fix:
  * Default MAC + IPv4 + IPv6 + Loopback increment checkboxes to
    CHECKED at dialog init. Gateway + VLAN stay UNCHECKED
    (usually shared across a scale set). VXLAN stays UNCHECKED
    (scale-flow-specific).
  * Add loopback support to `expand_for_scale` — reads
    increment_meta["loopback"] with `ipv4_octet_idx` +
    `ipv6_hextet_idx` fields, applies per-i increment to
    `loopback_ipv4` and `loopback_ipv6`.
  * Wire `loopback` key into `_snapshot_for_upstream_hint`.

After: default scale run of any BGP/OSPF/ISIS template produces
N devices with unique MAC/IPv4/IPv6/Loopback → zero collision
warning by default. Operator who wants shared identity across
N devices un-ticks explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import upstream_hints as u  # noqa: E402


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


# ---------- Dialog defaults ----------


def test_mac_increment_checkbox_default_checked():
    """Prevents duplicate MAC on the very first scale run without
    the operator having to tick anything."""
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_mac = QCheckBox("MAC")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_mac.setChecked(True)" in body


def test_ipv4_increment_checkbox_default_checked():
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_ipv4 = QCheckBox("IPv4")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_ipv4.setChecked(True)" in body


def test_ipv6_increment_checkbox_default_checked():
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_ipv6 = QCheckBox("IPv6")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_ipv6.setChecked(True)" in body


def test_loopback_increment_checkbox_default_checked():
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_loopback = QCheckBox("Loopback")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_loopback.setChecked(True)" in body


def test_gateway_increment_checkbox_default_unchecked():
    """Common case: shared gateway across the scale set. Only
    tick if the operator wants a scale of gateways."""
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_gateway = QCheckBox("Gateway")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_gateway.setChecked(True)" not in body


def test_vlan_increment_checkbox_default_unchecked():
    """Common case: one VLAN per interface — devices share it.
    Only tick to spread across multiple VLANs."""
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_vlan = QCheckBox("VLAN")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_vlan.setChecked(True)" not in body


def test_vxlan_increment_checkbox_default_unchecked():
    """VXLAN VNI/UDP increment is a specialized scale-flow
    knob — off by default."""
    src = _dialog_src()
    idx = src.index('self.increment_checkbox_vxlan = QCheckBox("VXLAN")')
    body = src[idx:idx + 300]
    assert "self.increment_checkbox_vxlan.setChecked(True)" not in body


# ---------- Loopback increment implementation ----------


def _base_with_loopbacks():
    return {
        "device_name": "netgen-device", "vlan": "10",
        "loopback_ipv4": "192.255.10.4",
        "loopback_ipv6": "2001:ff00:10::3",
    }


def test_expand_for_scale_increments_loopback_ipv4_when_on():
    """Pre-fix: no loopback logic → loopback_ipv4 stayed constant
    across all N devices even when Loopback checkbox was ticked."""
    devs = u.expand_for_scale(_base_with_loopbacks(), {
        "count": 3,
        "loopback": {"on": True, "ipv4_octet_idx": 0, "ipv6_hextet_idx": 0},
    })
    assert devs[0]["loopback_ipv4"] == "192.255.10.4"
    assert devs[1]["loopback_ipv4"] == "192.255.10.5"
    assert devs[2]["loopback_ipv4"] == "192.255.10.6"


def test_expand_for_scale_increments_loopback_ipv6_when_on():
    devs = u.expand_for_scale(_base_with_loopbacks(), {
        "count": 3,
        "loopback": {"on": True, "ipv4_octet_idx": 0, "ipv6_hextet_idx": 0},
    })
    assert devs[0]["loopback_ipv6"] == "2001:ff00:10::3"
    # ::3 → ::4 → ::5 in the last hextet
    assert devs[1]["loopback_ipv6"] == "2001:ff00:10::4"
    assert devs[2]["loopback_ipv6"] == "2001:ff00:10::5"


def test_expand_for_scale_honors_ipv4_octet_idx():
    """3rd-octet increment (`ipv4_octet_idx=1`): 192.255.10.4 →
    192.255.11.4 → 192.255.12.4."""
    devs = u.expand_for_scale(_base_with_loopbacks(), {
        "count": 3,
        "loopback": {"on": True, "ipv4_octet_idx": 1, "ipv6_hextet_idx": 0},
    })
    assert devs[0]["loopback_ipv4"] == "192.255.10.4"
    assert devs[1]["loopback_ipv4"] == "192.255.11.4"
    assert devs[2]["loopback_ipv4"] == "192.255.12.4"


def test_expand_for_scale_honors_ipv6_hextet_idx():
    """5th-from-last hextet increment (`ipv6_hextet_idx=5`):
    2001:ff00:10::3 → 2001:ff00:11::3 → ..."""
    devs = u.expand_for_scale(_base_with_loopbacks(), {
        "count": 3,
        "loopback": {"on": True, "ipv4_octet_idx": 0, "ipv6_hextet_idx": 5},
    })
    # exploded → "2001:ff00:0010:0000:0000:0000:0000:0003"
    # target hextet index (7 - 5 = 2) is "0010" → 0x11, 0x12
    assert devs[0]["loopback_ipv6"] == "2001:ff00:10::3"
    assert devs[1]["loopback_ipv6"] == "2001:ff00:11::3"
    assert devs[2]["loopback_ipv6"] == "2001:ff00:12::3"


def test_expand_for_scale_loopback_off_keeps_constant():
    """When Loopback is UNCHECKED (opting into shared identity),
    loopback IPs stay identical."""
    devs = u.expand_for_scale(_base_with_loopbacks(), {
        "count": 3,
        "loopback": {"on": False, "ipv4_octet_idx": 0, "ipv6_hextet_idx": 0},
    })
    for dev in devs:
        assert dev["loopback_ipv4"] == "192.255.10.4"
        assert dev["loopback_ipv6"] == "2001:ff00:10::3"


def test_expand_for_scale_missing_loopback_key_is_safe():
    """Snapshot from an older callsite might omit `loopback`
    entirely. Must not KeyError."""
    devs = u.expand_for_scale(_base_with_loopbacks(), {"count": 2})
    # No loopback key → treated as off → constant.
    for dev in devs:
        assert dev["loopback_ipv4"] == "192.255.10.4"


# ---------- Dialog snapshot wires loopback into meta ----------


def test_snapshot_emits_loopback_key():
    """Regression guard: `_snapshot_for_upstream_hint` must
    include a `loopback` sub-dict in the increment_meta dict.
    Pre-v0.5.324 it was missing entirely."""
    src = _dialog_src()
    idx = src.index("v0.5.319 (audit upstream-hint-scale)")
    body = src[idx:idx + 6000]
    assert '"loopback":' in body
    # Must carry both the checkbox state AND the two combo indexes.
    assert 'checked("increment_checkbox_loopback")' in body
    assert "loopback_ipv4_octet_combo" in body
    assert "loopback_ipv6_hextet_combo" in body


# ---------- End-to-end: default scale is collision-free ----------


def test_default_bgp_scale_no_collision_warning():
    """The scenario the operator kept hitting: BGP template,
    count=2 (default), no manual increment tick. Pre-v0.5.324
    fired 4 collision warnings; post-fix, zero."""
    base = {
        "device_name": "netgen-device", "vlan": "10",
        "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
        "ipv4_gateway": "192.168.0.1",
        "ipv6_address": "2001:db8::2", "ipv6_mask": "64",
        "mac_address": "00:11:22:33:44:55",
        "loopback_ipv4": "192.255.10.4",
        "loopback_ipv6": "2001:ff00:10::3",
        "bgp_config": {
            "bgp_local_as": "65000", "bgp_remote_asn": "65000",
            "bgp_hold_time": "90", "bgp_keepalive": "30",
            "ipv4_enabled": True, "ipv6_enabled": False,
        },
    }
    # Emulate the v0.5.324 default meta the dialog now produces:
    # MAC + IPv4 + IPv6 + Loopback all ticked, Gateway + VLAN off.
    devs = u.expand_for_scale(base, {
        "count": 2,
        "mac":      {"on": True, "byte_idx": 0},
        "ipv4":     {"on": True, "octet_idx": 0},
        "ipv6":     {"on": True, "hextet_idx": 0},
        "gateway":  {"on": False, "octet_idx": 0},
        "vlan":     {"on": False},
        "loopback": {"on": True, "ipv4_octet_idx": 1, "ipv6_hextet_idx": 5},
    })
    out = u.render_all(devs)["juniper"]
    assert "SCALE COLLISION WARNING" not in out


def test_default_scale_produces_unique_bgp_neighbors():
    """The whole point — with the new defaults, each device gets
    its own peer IP so the BGP neighbor blocks are actually
    unique (pre-fix operator saw `neighbor 192.168.0.2` on both
    scaled devices)."""
    base = {
        "device_name": "netgen-device", "vlan": "10",
        "ipv4_address": "192.168.0.2", "ipv4_mask": "24",
        "mac_address": "00:11:22:33:44:55",
        "bgp_config": {
            "bgp_local_as": "65000", "bgp_remote_asn": "65000",
            "bgp_hold_time": "90", "bgp_keepalive": "30",
            "ipv4_enabled": True, "ipv6_enabled": False,
        },
    }
    devs = u.expand_for_scale(base, {
        "count": 2,
        "mac":  {"on": True, "byte_idx": 0},
        "ipv4": {"on": True, "octet_idx": 0},
    })
    out = u.render_all(devs)["juniper"]
    # Two DIFFERENT neighbor IPs now.
    assert "neighbor 192.168.0.2" in out
    assert "neighbor 192.168.0.3" in out


# ---------- AST parse ----------


def test_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_upstream_hints_ast_parses():
    import ast
    ast.parse((_REPO / "utils" / "upstream_hints.py").read_text())
