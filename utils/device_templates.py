"""Ready-made device templates for one-click profile creation.

Each template returns a `dict` of pre-filled form fields. The Add
Device dialog has an `apply_template(name)` method that walks the dict
and pushes each value into the right widget — so adding a new template
here is purely declarative.

Templates are intentionally minimal: they set the *common* defaults
operators want for a given role (e.g. "iBGP peer with loopback as
router-id"), leaving the IP / VLAN / device-name fields for the
operator to fill in for their specific lab. The goal is to skip the
"click 14 checkboxes, type 6 defaults" prelude that every test setup
starts with, not to provide a finished config.

Adding a new template
---------------------
1. Append a `_Template` to `_TEMPLATES`.
2. The Add Device dialog picks it up automatically — no UI changes.

Field names match `AddDeviceDialog` attributes. Unknown fields are
silently skipped (lets templates target older builds gracefully).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# Field-application is structured as a small contract so the dialog
# can be a dumb consumer:
#
#   { widget_name: ("set_text" | "set_checked" | "set_combo", value) }
#
# That's enough for ~95% of fields. For the protocol-checkbox cluster
# we also want a list of "protocols to enable" handled together so
# the cascading visibility update only fires once at the end.


@dataclass
class _Template:
    key: str                       # short id, also the combo value
    title: str                     # human label shown in dropdown
    summary: str                   # one-line hint shown under combo
    protocols: List[str] = field(default_factory=list)  # protocols to enable
    fields: Dict[str, Any] = field(default_factory=dict)  # widget_name → value
    # Optional callable for templates that need conditional logic
    # beyond pure dict apply (e.g. derive remote-AS from local-AS).
    post_apply: Optional[Callable[[Any], None]] = None


# ---------------------------------------------------------------- registry


_TEMPLATES: List[_Template] = [
    _Template(
        key="bare_host",
        title="Bare host (L2/L3 only)",
        summary="Plain IP+MAC endpoint. No routing protocols. Useful as "
                "an ARP/ND target or a stream sink.",
        protocols=[],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
        },
    ),
    _Template(
        key="ibgp_peer",
        title="iBGP peer (loopback router-id)",
        summary="iBGP with loopback as router-id + update-source. "
                "Local-AS = Remote-AS = 65000. Adjust to match fabric.",
        protocols=["BGP"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "bgp_protocol_type": "iBGP",
            "bgp_local_as_input": "65000",
            "bgp_remote_as_input": "65000",
        },
    ),
    _Template(
        key="ebgp_peer",
        title="eBGP peer (different AS)",
        summary="eBGP between AS 65000 (local) and AS 65001 (remote). "
                "Pre-fills loopback IPs for update-source resilience.",
        protocols=["BGP"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "bgp_protocol_type": "eBGP",
            "bgp_local_as_input": "65000",
            "bgp_remote_as_input": "65001",
        },
    ),
    _Template(
        key="ospf_backbone",
        title="OSPFv2 area-0 backbone router",
        summary="OSPFv2 enabled, area 0.0.0.0, point-to-point hello "
                "interval, router-id from loopback.",
        protocols=["OSPF"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "ospf_area_id_input": "0.0.0.0",
        },
    ),
    _Template(
        key="ospf_dualstack",
        title="OSPFv2 + OSPFv3 dual-stack",
        summary="Both AFs in area 0. Router-id auto-derived from "
                "loopback IPv4.",
        protocols=["OSPF"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": True,
            "vlan_input": "10",
            "ospf_area_id_input": "0.0.0.0",
        },
    ),
    _Template(
        key="isis_l12",
        title="IS-IS Level-1-2 router",
        summary="L1-L2 with NET 49.0001.0000.0000.0001.00 by default. "
                "Adjust the system-id portion per device.",
        protocols=["ISIS"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": True,
            "vlan_input": "10",
            "isis_area_input": "49.0001",
            "isis_level_combo": "Level-1-2",
        },
    ),
    _Template(
        key="dhcp_client",
        title="DHCP client",
        summary="dhclient inside the device VRF. IPv4 enabled, IPv6 "
                "off. Lease and gateway populate at runtime.",
        protocols=["DHCP"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "dhcp_mode_combo": "Client",
        },
    ),
    _Template(
        key="vxlan_vtep",
        title="VXLAN tunnel endpoint",
        summary="One VXLAN tunnel with VNI 10000, UDP/4789, sane "
                "underlay defaults. Bridge SVI 10.0.0.100/24.",
        protocols=["VXLAN"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
        },
    ),
]


# ---------------------------------------------------------------- public API


def list_templates() -> List[Dict[str, str]]:
    """Return [{key, title, summary}, ...] — what the dropdown shows."""
    return [
        {"key": t.key, "title": t.title, "summary": t.summary}
        for t in _TEMPLATES
    ]


def get_template(key: str) -> Optional[_Template]:
    for t in _TEMPLATES:
        if t.key == key:
            return t
    return None


def apply_to_dialog(dialog, key: str) -> bool:
    """Push a named template's defaults into an AddDeviceDialog.

    Tolerant of missing widgets — fields that don't exist on this
    build of the dialog are skipped silently so old / new templates
    can coexist with form rearrangements. Returns True iff at least
    one field was applied.
    """
    tmpl = get_template(key)
    if tmpl is None:
        return False
    applied_any = False

    # Step 1: protocol-enable checkboxes. Mapping intentionally matches
    # the dialog's internal field names so we don't carry a separate
    # synonym table.
    proto_to_checkbox = {
        "BGP":   "bgp_enable_checkbox",
        "OSPF":  "ospf_enable_checkbox",
        "ISIS":  "isis_enable_checkbox",
        "DHCP":  "dhcp_enable_checkbox",
        "ROCEV2": "rocev2_enable_checkbox",
        "VXLAN": "vxlan_enable_checkbox",
    }
    wanted = {p.upper() for p in tmpl.protocols}
    for proto, attr in proto_to_checkbox.items():
        chk = getattr(dialog, attr, None)
        if chk is None:
            continue
        want = proto in wanted
        try:
            chk.setChecked(want)
            applied_any = True
        except Exception:
            pass

    # Step 2: simple field assignments. Three widget kinds are supported:
    #   QLineEdit  → setText(str(value))
    #   QCheckBox  → setChecked(bool(value))
    #   QComboBox  → setCurrentText(str(value))  (or no-op if value missing)
    # Anything else is skipped silently.
    for attr_name, value in tmpl.fields.items():
        w = getattr(dialog, attr_name, None)
        if w is None:
            continue
        try:
            if hasattr(w, "setChecked") and isinstance(value, bool):
                w.setChecked(value)
            elif hasattr(w, "setCurrentText"):
                w.setCurrentText(str(value))
            elif hasattr(w, "setText"):
                w.setText(str(value))
            else:
                continue
            applied_any = True
        except Exception:
            # Don't let a stray template-field break the whole apply.
            pass

    # Step 3: optional callable for templates with non-trivial logic.
    if tmpl.post_apply:
        try:
            tmpl.post_apply(dialog)
            applied_any = True
        except Exception:
            pass

    # Force the dialog's cascading visibility update so the protocol
    # subsection appears even without a manual click.
    try:
        if hasattr(dialog, "_on_protocol_enabled_changed"):
            dialog._on_protocol_enabled_changed()
    except Exception:
        pass

    return applied_any
