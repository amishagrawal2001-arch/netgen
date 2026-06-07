"""v0.4.7 — audit of utils/device_templates.py.

Four silent bugs found in the pre-v0.4.7 device templates. Each one
referenced a widget name that doesn't exist on AddDeviceDialog. The
`apply_to_dialog()` function uses `getattr(dialog, attr, None)` and
silently skips on None — so the operator picked a template that
PROMISED a specific config and got the dialog's defaults instead.

  Pre-v0.4.7 broken field           | Real widget on dialog       | Impact
  ----------------------------------|-----------------------------|---------------------------
  ibgp_peer.bgp_protocol_type       | protocol_dropdown           | iBGP claim silently dropped
  ebgp_peer.bgp_protocol_type       | protocol_dropdown           | eBGP claim silently dropped
  isis_l12.isis_area_input          | isis_area_id_input          | Lucky non-bug (defaults match)
  isis_l12.isis_level_combo         | NO SUCH WIDGET              | L1-L2 claim silently dropped
  vxlan_vtep (multiple)             | All present in dialog       | Summary lied — only vlan was set

v0.4.7 also adds four gap-fill templates: rocev2_target, dhcp_server,
bgp_ospf_pe, ipv6_only_host. The pre-v0.4.7 catalog never covered
these roles — operators rebuilt them by hand every time.
"""
from __future__ import annotations

from pathlib import Path

from utils import device_templates


_DEVICE_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_device_dialog.py"


# ─────────────────────────────── helpers ─────────────────────────────


def _dialog_has_widget(name: str) -> bool:
    """Source-level check: does AddDeviceDialog declare a `self.<name>`
    attribute anywhere? Templates can't depend on widgets that don't
    exist (apply_to_dialog silently drops them, summary lies)."""
    src = _DEVICE_DIALOG.read_text()
    return f"self.{name}" in src


# ─────────────────────────────── bug fixes pin ──────────────────────


def test_ibgp_peer_uses_real_widgets_only():
    """Pre-v0.4.7 `ibgp_peer` set `bgp_protocol_type` which doesn't
    exist on the dialog — protocol selection silently fell back to
    whatever protocol_dropdown defaulted to. Fix: drop that field;
    use post_apply to drive protocol_dropdown after the cascade."""
    t = device_templates.get_template("ibgp_peer")
    assert t is not None
    assert "bgp_protocol_type" not in t.fields, (
        "ibgp_peer still has the phantom `bgp_protocol_type` field — "
        "that widget doesn't exist on AddDeviceDialog. The real "
        "selector is `protocol_dropdown`."
    )
    # Every remaining field must reference a real widget.
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"ibgp_peer.fields[{field_name!r}] doesn't match any "
            f"`self.<name>` widget on AddDeviceDialog — it will be "
            f"silently dropped by apply_to_dialog's getattr() check."
        )
    # And it MUST have a post_apply that drives protocol_dropdown.
    assert t.post_apply is not None, (
        "ibgp_peer has no post_apply — without it, protocol_dropdown "
        "is never set to 'iBGP' and the template silently dups "
        "the bare 'BGP-enabled' default."
    )


def test_ebgp_peer_uses_real_widgets_only():
    """Same bug, same fix, for eBGP."""
    t = device_templates.get_template("ebgp_peer")
    assert "bgp_protocol_type" not in t.fields
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"ebgp_peer.fields[{field_name!r}] not a real widget"
        )
    assert t.post_apply is not None


def test_isis_uses_correct_area_widget_name():
    """Pre-v0.4.7 template set `isis_area_input` — the real widget is
    `isis_area_id_input`. A lucky non-bug: both defaulted to
    '49.0001' so the operator never noticed. Pin the correct name."""
    t = device_templates.get_template("isis_l12")
    assert "isis_area_input" not in t.fields, (
        "isis_l12 still references the phantom `isis_area_input` — "
        "real widget is `isis_area_id_input`."
    )
    assert "isis_area_id_input" in t.fields, (
        "isis_l12 must explicitly set isis_area_id_input — otherwise "
        "a future widget-default change silently drifts the template."
    )
    assert _dialog_has_widget("isis_area_id_input")


def test_isis_drops_phantom_level_combo():
    """`isis_level_combo` doesn't exist on AddDeviceDialog at all
    (the dialog has no Level-1/L2/L1-L2 selector). Pre-v0.4.7 the
    template set it anyway and silently lied in the summary."""
    t = device_templates.get_template("isis_l12")
    assert "isis_level_combo" not in t.fields, (
        "isis_l12 still references `isis_level_combo` — that widget "
        "doesn't exist on AddDeviceDialog. The dialog has no level "
        "selector; the summary text shouldn't promise one."
    )
    # And the summary must not have "Level-1-2" as a promised
    # default — the dialog has no level selector to enforce it. A
    # parenthetical mention of "level" (with no specific value) is
    # acceptable since the engine has its own default.
    assert "Level-1-2" not in t.summary, (
        "isis_l12 summary still promises 'Level-1-2' but the dialog "
        "has no level selector. Tone the summary to match what's "
        "deliverable."
    )


def test_vxlan_vtep_actually_sets_promised_defaults():
    """Pre-v0.4.7 the summary promised "VNI 10000, UDP/4789, sane
    underlay defaults. Bridge SVI 10.0.0.100/24" but only vlan_input
    was set. Operator got VNI=5000 (widget default) and empty Bridge
    SVI / local / remote IPs. Pin the four promised fields."""
    t = device_templates.get_template("vxlan_vtep")
    promised = {
        "vxlan_vni_input": "10000",
        "vxlan_udp_port_input": "4789",
        "vxlan_bridge_svi_ip_input": "10.0.0.100/24",
    }
    for field_name, expected in promised.items():
        assert field_name in t.fields, (
            f"vxlan_vtep doesn't set {field_name!r} — summary "
            f"promises it but apply_to_dialog won't deliver it."
        )
        assert t.fields[field_name] == expected, (
            f"vxlan_vtep.fields[{field_name!r}] = {t.fields[field_name]!r} "
            f"— summary promises {expected!r}. Either match the "
            f"summary or update it."
        )
    # local + remote VTEP IPs also needed for a useful default
    assert "vxlan_local_ip_input" in t.fields
    assert "vxlan_remote_input" in t.fields
    # Every field must be a real widget
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"vxlan_vtep.fields[{field_name!r}] not a real widget"
        )


# ─────────────────────────────── new templates ───────────────────────


def test_v047_new_templates_registered():
    """All four v0.4.7 gap-fill templates must be picked up by the
    list/get APIs the dialog uses."""
    expected = {"rocev2_target", "dhcp_server", "bgp_ospf_pe", "ipv6_only_host"}
    all_keys = {m["key"] for m in device_templates.list_templates()}
    missing = expected - all_keys
    assert not missing, f"v0.4.7 templates missing: {missing}"
    for key in expected:
        t = device_templates.get_template(key)
        assert t is not None
        assert t.title.strip() and t.summary.strip()


def test_rocev2_target_uses_real_rocev2_widgets():
    """RoCEv2 has its own field cluster — pin the widget names so
    a refactor of add_device_dialog.py surfaces here, not at the
    operator's chair."""
    t = device_templates.get_template("rocev2_target")
    assert "ROCEV2" in {p.upper() for p in t.protocols}, (
        "rocev2_target doesn't enable the ROCEV2 protocol — the "
        "checkbox stays off and the field cluster stays disabled."
    )
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"rocev2_target.fields[{field_name!r}] not a real widget"
        )
    # DSCP 46 is EF / lossless — pin the choice so a refactor doesn't
    # set it to 0 (no QoS).
    assert t.fields.get("rocev2_dscp_input") == "46"
    assert t.fields.get("rocev2_udp_port_input") == "4791"


def test_dhcp_server_completes_server_field_set():
    """DHCP server has a pool + gateway + lease that DHCP client
    doesn't. Pin all four so the template actually drops a runnable
    server config, not a bare 'DHCP enabled' switch."""
    t = device_templates.get_template("dhcp_server")
    assert t.fields.get("dhcp_mode_combo") == "Server", (
        "dhcp_server doesn't set mode=Server — picking this template "
        "gets a DHCP client instead."
    )
    for required in (
        "dhcp_pool_start_input", "dhcp_pool_end_input",
        "dhcp_gateway_route_input", "dhcp_lease_time_input",
    ):
        assert required in t.fields, (
            f"dhcp_server missing {required!r} — operator gets an "
            f"empty pool / no gateway / no lease."
        )
        assert _dialog_has_widget(required)


def test_bgp_ospf_pe_enables_both_protocols():
    """PE template must enable BOTH BGP and OSPF. Missing one would
    be no different from the existing ebgp_peer or ospf_backbone
    template."""
    t = device_templates.get_template("bgp_ospf_pe")
    protos = {p.upper() for p in t.protocols}
    assert "BGP" in protos
    assert "OSPF" in protos
    # And it must drive protocol_dropdown to eBGP (like ebgp_peer)
    assert t.post_apply is not None, (
        "bgp_ospf_pe has no post_apply — protocol_dropdown won't "
        "be selected to eBGP, falling back to dialog default."
    )


def test_ipv6_only_host_disables_ipv4():
    """The whole point of the v6-only template is to flip IPv4 OFF.
    A copy-paste mistake setting ipv4_checkbox=True would silently
    make this template identical to bare_host (which enables both)."""
    t = device_templates.get_template("ipv6_only_host")
    assert t.fields.get("ipv4_checkbox") is False, (
        "ipv6_only_host doesn't turn OFF IPv4 — defeats the purpose"
    )
    assert t.fields.get("ipv6_checkbox") is True
    # No protocols — it's a bare endpoint
    assert not t.protocols
    # Every IPv6 field must be a real widget
    for field_name in t.fields:
        assert _dialog_has_widget(field_name)


# ─────────────────────────────── catalog-wide checks ─────────────────


def test_every_template_field_references_a_real_widget():
    """Catalog-wide invariant: NO template may reference a widget
    that doesn't exist on AddDeviceDialog. The whole bug pattern
    behind v0.4.7 was 'silently dropped via getattr None' — pin
    every key against the source so future drift surfaces here."""
    src = _DEVICE_DIALOG.read_text()
    for meta in device_templates.list_templates():
        t = device_templates.get_template(meta["key"])
        for field_name in t.fields:
            assert f"self.{field_name}" in src, (
                f"Template {meta['key']!r} references widget "
                f"{field_name!r} which doesn't exist on "
                f"AddDeviceDialog. apply_to_dialog will silently "
                f"drop it — operator gets dialog defaults, not "
                f"the template's promise. This is exactly the bug "
                f"pattern v0.4.7 fixed for ibgp_peer / ebgp_peer / "
                f"isis_l12. Either add the widget or remove the "
                f"field from the template."
            )
