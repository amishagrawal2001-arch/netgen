"""IPv6 DHCP server template — one-click parity with the IPv4
`dhcp_server` template.

Operator ask (post-v0.5.302): the template catalog only ships an
IPv4 DHCP-server template; add the IPv6 mirror so operators can
one-click a DHCPv6 server the same way they one-click a DHCPv4 one.

The tests lock in three things:

  1. The template is registered under a stable key.
  2. Every widget name it targets actually exists on
     AddDeviceDialog. Same failure mode as the v0.4.7 audit bugs
     (`apply_to_dialog` silently skips missing widgets and the
     operator gets a template that promised X and did nothing).
  3. The v6 DHCP fields it sets don't collide with the IPv4 v0.5.222
     "no address in subnet on interface" trap — interface IP and
     pool must live in the same /64.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path

from utils import device_templates


_DEVICE_DIALOG = Path(__file__).resolve().parents[1] / "widgets" / "add_device_dialog.py"


def _dialog_has_widget(name: str) -> bool:
    """Source-level check: does AddDeviceDialog declare a `self.<name>`
    attribute anywhere?"""
    return f"self.{name}" in _DEVICE_DIALOG.read_text()


def test_template_registered():
    t = device_templates.get_template("dhcp_server_ipv6")
    assert t is not None, "dhcp_server_ipv6 template missing from _TEMPLATES"
    assert t.key == "dhcp_server_ipv6"
    # Shows up in the dropdown listing.
    titles = {row["key"]: row["title"] for row in device_templates.list_templates()}
    assert "dhcp_server_ipv6" in titles
    assert "DHCPv6" in titles["dhcp_server_ipv6"] or "IPv6" in titles["dhcp_server_ipv6"]


def test_template_enables_dhcp_protocol_only():
    t = device_templates.get_template("dhcp_server_ipv6")
    # Same protocols list as the IPv4 sibling — DHCP alone.
    assert t.protocols == ["DHCP"]


def test_template_widget_names_all_exist_on_dialog():
    """Same failure mode as the v0.4.7 audit — a template that
    references a widget name that doesn't exist has its field
    silently dropped by apply_to_dialog."""
    t = device_templates.get_template("dhcp_server_ipv6")
    for field_name in t.fields:
        assert _dialog_has_widget(field_name), (
            f"dhcp_server_ipv6.fields[{field_name!r}] doesn't match "
            f"any `self.<name>` in AddDeviceDialog — apply_to_dialog "
            f"will silently drop this field."
        )


def test_template_v6_only_configuration():
    """Direct IPv6 mirror of the IPv4 dhcp_server template — the
    IPv4-family checkboxes must be OFF so downstream code doesn't
    try to bring up dnsmasq's IPv4 side with defaulted / empty
    pool fields."""
    t = device_templates.get_template("dhcp_server_ipv6")
    assert t.fields["ipv4_checkbox"] is False
    assert t.fields["ipv6_checkbox"] is True
    assert t.fields["dhcp_ipv4_enabled_checkbox"] is False
    assert t.fields["dhcp_ipv6_enabled_checkbox"] is True
    assert t.fields["dhcp_mode_combo"] == "Server"


def test_template_interface_and_pool_share_the_same_prefix():
    """v0.5.222-shape guard: if the interface IPv6 doesn't sit in
    the same /64 as the pool, dnsmasq's v6 dhcp-range refuses to
    serve and the device flips to State=Failed with the same
    "no address in subnet on interface" error the IPv4 side
    surfaced."""
    t = device_templates.get_template("dhcp_server_ipv6")
    iface_ip = ipaddress.IPv6Address(t.fields["ipv6_input"])
    iface_prefix = int(t.fields["ipv6_mask_input"])
    iface_net = ipaddress.IPv6Network(f"{iface_ip}/{iface_prefix}", strict=False)

    pool_start = ipaddress.IPv6Address(t.fields["dhcp6_pool_start_input"])
    pool_end = ipaddress.IPv6Address(t.fields["dhcp6_pool_end_input"])
    server_ip = ipaddress.IPv6Address(t.fields["dhcp6_server_ip_input"])

    assert pool_start in iface_net, (
        f"pool start {pool_start} outside interface network {iface_net} — "
        "would trigger the v6 equivalent of v0.5.222"
    )
    assert pool_end in iface_net, (
        f"pool end {pool_end} outside interface network {iface_net}"
    )
    assert server_ip in iface_net
    assert pool_start < pool_end
    # Prefix in the dhcp6_prefix_input field must match the mask
    # (dnsmasq uses this as the "constructor" prefix in dhcp-range).
    assert int(t.fields["dhcp6_prefix_input"]) == iface_prefix


def test_template_stays_out_of_the_default_2001_db8_slash_64():
    """The generic ipv6_only_host + default widget IPv6 both live
    in 2001:db8::/64. To match the IPv4 template's isolation
    pattern (172.16.30.0/24 stays out of the 192.168.0.0/24
    default), this DHCPv6 template picks a distinct /64 within
    the RFC 3849 documentation range (2001:db8::/32) so a
    DHCPv6-server device doesn't collide with regular IPv6
    devices on the wire."""
    t = device_templates.get_template("dhcp_server_ipv6")
    iface_ip = ipaddress.IPv6Address(t.fields["ipv6_input"])
    default_regular_iface = ipaddress.IPv6Network("2001:db8::/64", strict=False)
    documentation_range = ipaddress.IPv6Network("2001:db8::/32", strict=False)
    assert iface_ip in documentation_range, (
        "template must stay in the RFC 3849 documentation range so "
        "it's obviously a lab-only address"
    )
    assert iface_ip not in default_regular_iface, (
        "template's /64 collides with the default 2001:db8::/64 used "
        "by regular IPv6 devices — DHCPv6-server device would fight "
        "static-config peers on the same segment"
    )


def test_template_lease_and_route_fields_reasonable():
    t = device_templates.get_template("dhcp_server_ipv6")
    # Same 1-hour default as the IPv4 template.
    assert t.fields["dhcp6_lease_time_input"] == "3600"
    # Route CIDR is well-formed and matches the pool prefix.
    route = ipaddress.IPv6Network(t.fields["dhcp6_gateway_route_input"])
    assert route.prefixlen == 64
