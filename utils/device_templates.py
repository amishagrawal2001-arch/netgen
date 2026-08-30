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
            # v0.4.7 fix: pre-v0.4.7 set `bgp_protocol_type` which is
            # NOT a widget — the real choice lives on
            # `protocol_dropdown`. The `iBGP` / `eBGP` items only get
            # added to that dropdown AFTER bgp_enable_checkbox is
            # toggled on (line ~1113 in add_device_dialog.py), which
            # apply_to_dialog wires via _on_protocol_enabled_changed().
            # We use post_apply to: (1) force-cascade so the items
            # exist, (2) select the right one, (3) tick
            # use-loopback so the loopback-IP rows light up.
            "bgp_local_as_input": "65000",
            "bgp_remote_as_input": "65000",
            "bgp_remote_loopback_ip_input": "192.168.250.1",
        },
        post_apply=lambda d: _select_bgp_protocol(d, "iBGP"),
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
            # v0.4.7 fix: same pattern as iBGP — use post_apply since
            # protocol_dropdown items are dynamic on BGP-checkbox.
            "bgp_local_as_input": "65000",
            "bgp_remote_as_input": "65001",
            "bgp_remote_loopback_ip_input": "192.168.250.1",
        },
        post_apply=lambda d: _select_bgp_protocol(d, "eBGP"),
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
        title="IS-IS router (Area 49.0001)",
        summary="IS-IS with NET prefix 49.0001 by default. System-ID "
                "auto-assigned by the dialog. The IS-IS level is set "
                "by the underlying engine — there's no GUI selector "
                "for it in the current AddDeviceDialog.",
        protocols=["ISIS"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": True,
            "vlan_input": "10",
            # v0.4.7 fix: pre-v0.4.7 used `isis_area_input` (which
            # doesn't exist) and `isis_level_combo` (no such widget
            # in the dialog at all). Real widget is
            # `isis_area_id_input`. Level selection isn't surfaced
            # in the dialog — summary tones down the L1-L2 claim
            # accordingly.
            "isis_area_id_input": "49.0001",
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
            # v0.4.7 fix: pre-v0.4.7 the template summary promised
            # specific VNI / UDP / Bridge SVI defaults but the
            # fields dict ONLY set vlan_input. Result: operator
            # picked vxlan_vtep, got an empty VXLAN config with
            # the protocol enabled — looked broken. These four
            # widgets exist in AddDeviceDialog (lines 972-1014):
            "vxlan_vni_input": "10000",
            "vxlan_udp_port_input": "4789",
            "vxlan_bridge_svi_ip_input": "10.0.0.100/24",
            "vxlan_local_ip_input": "10.0.250.1",
            "vxlan_remote_input": "10.0.250.2",
        },
    ),

    # ──────────────────────────────── v0.4.7 gap-fill templates
    # The pre-v0.4.7 catalog covered iBGP / eBGP / OSPFv2 / OSPFv2+v3 /
    # IS-IS / DHCP-client / VXLAN VTEP + bare host (8 entries). Four
    # operator-frequent roles were missing — each one would otherwise
    # be hand-built every time.

    _Template(
        key="rocev2_target",
        title="RoCEv2 lossless target",
        summary="RDMA target with DSCP=46 (EF/lossless), Priority=3 "
                "(typical PFC TC), UDP/4791. Pair with a corresponding "
                "client running ib_send_bw / ib_write_bw. Use as the "
                "device side of an RDMA Blast flow.",
        protocols=["ROCEV2"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            # RoCEv2 widgets are at lines 937-951 of add_device_dialog.py
            "rocev2_dscp_input": "46",       # EF / lossless DSCP
            "rocev2_priority_input": "3",    # standard PFC TC for storage
            "rocev2_udp_port_input": "4791", # IANA-assigned RoCEv2 UDP port
        },
    ),

    _Template(
        key="dhcp_server",
        title="DHCP server (pool 172.16.30.10-200)",
        summary="dnsmasq inside the device VRF. Interface + pool land on "
                "172.16.30.0/24 (device at .1, pool .10–.200), 1-hour "
                "lease. The DHCP server template uses the 172.16/12 "
                "private range so a DHCP-server device stays isolated "
                "from regular BGP/OSPF devices (default 192.168.0.0/24) "
                "and doesn't overlap on-wire. Useful for stressing DHCP "
                "relay / IPAM / client churn paths.",
        protocols=["DHCP"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "dhcp_mode_combo": "Server",
            # Device sits at .1 of the DHCP subnet so dnsmasq listens on
            # the same broadcast domain as the pool. Without this override
            # the interface would inherit the 192.168.0.2/24 widget
            # default and dnsmasq would refuse to serve pool addresses
            # (v0.5.222: "no address in subnet on interface").
            "ipv4_input": "172.16.30.1",
            "ipv4_mask_input": "24",
            "ipv4_gateway_input": "172.16.30.1",
            # DHCP server widgets are at lines 845-863
            "dhcp_pool_start_input": "172.16.30.10",
            "dhcp_pool_end_input": "172.16.30.200",
            "dhcp_gateway_route_input": "172.16.30.0/24",
            "dhcp_lease_time_input": "3600",
        },
    ),

    _Template(
        key="bgp_ospf_pe",
        title="PE router (eBGP external + OSPFv2 internal)",
        summary="Classic provider-edge: eBGP to the upstream (AS 65000 "
                "→ AS 65001), OSPFv2 area-0 to the internal fabric. "
                "Loopback used as router-id for both protocols.",
        protocols=["BGP", "OSPF"],
        fields={
            "ipv4_checkbox": True,
            "ipv6_checkbox": False,
            "vlan_input": "10",
            "bgp_local_as_input": "65000",
            "bgp_remote_as_input": "65001",
            "bgp_remote_loopback_ip_input": "192.168.250.1",
            "ospf_area_id_input": "0.0.0.0",
        },
        post_apply=lambda d: _select_bgp_protocol(d, "eBGP"),
    ),

    _Template(
        key="ipv6_only_host",
        title="IPv6-only host (no v4)",
        summary="Plain host with IPv4 OFF, IPv6 ON. Address "
                "2001:db8::2/64 with gateway 2001:db8::1. Useful for "
                "v6-only deployment tests (NAT64, DNS64, MAP-E test "
                "targets, v6-only stream sinks).",
        protocols=[],
        fields={
            "ipv4_checkbox": False,
            "ipv6_checkbox": True,
            "vlan_input": "10",
            # IPv6 widget defaults already match (2001:db8::2 etc.),
            # but pin them explicitly so a future widget-default
            # change doesn't silently drift the template.
            "ipv6_input": "2001:db8::2",
            "ipv6_mask_input": "64",
            "ipv6_gateway_input": "2001:db8::1",
        },
    ),
]


# ─────────────────────────────── v0.4.7 helpers
#
# `protocol_dropdown` items are populated DYNAMICALLY when BGP is
# enabled (add_device_dialog.py:1095-1113 — "Add iBGP and eBGP as
# separate protocol options when BGP is enabled"). A template that
# wants to select iBGP / eBGP can't just call
# `protocol_dropdown.setCurrentText` in its `fields` dict, because
# apply_to_dialog applies protocols (step 1) BEFORE fields (step 2)
# — and the cascading visibility update (which is what populates the
# dropdown items) doesn't run until AFTER both, at the end of
# apply_to_dialog.
#
# Workaround: this helper force-runs the cascade first, then sets
# the dropdown. Called from `post_apply` of the iBGP / eBGP / PE
# templates. The trailing cascade by apply_to_dialog is then a
# no-op.


def _select_bgp_protocol(dialog, choice: str) -> None:
    """Force the protocol_dropdown to iBGP or eBGP after BGP is on.

    Pre-v0.4.7 the iBGP / eBGP templates set a non-existent
    `bgp_protocol_type` field which was silently dropped — so
    operators picking the "iBGP peer" template ended up with eBGP
    (or whatever the dropdown happened to default to). This helper
    actually does the selection.
    """
    try:
        if hasattr(dialog, "_on_protocol_enabled_changed"):
            dialog._on_protocol_enabled_changed()
        dropdown = getattr(dialog, "protocol_dropdown", None)
        if dropdown is not None and hasattr(dropdown, "setCurrentText"):
            dropdown.setCurrentText(choice)
        # Also tick the "use loopback IP" checkbox so the
        # bgp_remote_loopback_ip_input we filled becomes editable.
        chk = getattr(dialog, "bgp_use_loopback_checkbox", None)
        if chk is not None and hasattr(chk, "setChecked"):
            chk.setChecked(True)
    except Exception:
        # Templates must never break the dialog — silent best-effort.
        pass


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
