"""v0.5.226 — upstream-router config hints (Juniper/Cisco/Arista).

Operators peer every netgen device with SOMETHING — usually the
lab's ToR switch or a router upstream — and the vendor's syntax
for the interface + BGP neighbor + OSPF/IS-IS interface stanza
eats ~30 minutes of copy-paste per device. This module generates
paste-ready config from the device's own settings so the operator
can drop it onto their upstream and get an adjacency without
having to re-derive the syntax each time.

These tests pin the shape of the rendered snippets — enough to
catch a regression where somebody edits the template and breaks
the vendor's parser, without asserting every whitespace detail.
"""

from utils.upstream_hints import (
    render_juniper, render_cisco, render_arista, render_all,
    _isis_net_from_loopback,
)


DEV_FULL = {
    "device_name": "device1",
    "interface":   "ens2f0np0",
    "vlan":        "100",
    "mac_address": "00:11:22:33:44:55",
    "ipv4_address":"192.168.0.2",
    "ipv4_mask":   "24",
    "ipv4_gateway":"192.168.0.1",
    "ipv6_address":"2001:db8::2",
    "ipv6_mask":   "64",
    "ipv6_gateway":"2001:db8::1",
    "loopback_ipv4": "192.255.0.1",
    "loopback_ipv6": "2001:ff00::1",
    "bgp_config": {
        "bgp_local_as": "65000",
        "bgp_remote_asn": "65000",
        "bgp_hold_time": "90",
        "bgp_keepalive": "30",
        "ipv4_enabled": True,
        "ipv6_enabled": True,
    },
    "ospf_config": {
        "area_id":        "0.0.0.0",
        "area_id_ipv4":   "0.0.0.0",
        "area_id_ipv6":   "0.0.0.0",
        "hello_interval": "10",
        "dead_interval":  "40",
        "ipv4_enabled":   True,
        "ipv6_enabled":   True,
        "p2p_ipv4":       True,
        "p2p_ipv6":       True,
    },
    "isis_config": {
        "isis_area":  "CORE",
        "isis_net":   "49.0001.0000.0000.0001.00",
        "isis_level": "level-2-only",
    },
}


# --- Header & marker -------------------------------------------------------

def test_juniper_header_uses_hash_marker():
    s = render_juniper(DEV_FULL)
    assert s.startswith("# Upstream config for netgen device 'device1'")


def test_cisco_header_uses_bang_marker():
    s = render_cisco(DEV_FULL)
    assert s.startswith("! Upstream config for netgen device 'device1'")


def test_arista_header_uses_bang_marker():
    s = render_arista(DEV_FULL)
    assert s.startswith("! Upstream config for netgen device 'device1'")


def test_render_all_returns_three_vendors():
    all_ = render_all(DEV_FULL)
    assert set(all_.keys()) == {"juniper", "cisco", "arista"}
    assert all(len(v) > 100 for v in all_.values())


# --- Juniper interface stanza ---------------------------------------------

def test_juniper_iface_uses_set_syntax():
    s = render_juniper(DEV_FULL)
    assert "set interfaces ge-0/0/0 vlan-tagging" in s
    assert "set interfaces ge-0/0/0 unit 100 vlan-id 100" in s
    assert "set interfaces ge-0/0/0 unit 100 family inet address 192.168.0.1/24" in s
    assert "set interfaces ge-0/0/0 unit 100 family inet6 address 2001:db8::1/64" in s
    assert "family iso" in s  # ISIS enabled


def test_juniper_bgp_ibgp_internal():
    """Same local + remote ASN → internal peer group."""
    s = render_juniper(DEV_FULL)
    assert "set protocols bgp group NETGEN-device1 type internal" in s
    assert "set protocols bgp group NETGEN-device1 peer-as 65000" in s
    assert "set protocols bgp group NETGEN-device1 neighbor 192.168.0.2" in s
    assert "set protocols bgp group NETGEN-device1 neighbor 2001:db8::2" in s
    assert "family inet6 unicast" in s


def test_juniper_bgp_external_when_asns_differ():
    d = dict(DEV_FULL)
    d["bgp_config"] = dict(DEV_FULL["bgp_config"], bgp_remote_asn="65001")
    s = render_juniper(d)
    assert "type external" in s
    assert "type internal" not in s


def test_juniper_ospf_p2p_when_flag_set():
    s = render_juniper(DEV_FULL)
    assert "set protocols ospf area 0.0.0.0 interface ge-0/0/0.100 interface-type p2p" in s
    assert "set protocols ospf3 area 0.0.0.0 interface ge-0/0/0.100 interface-type p2p" in s
    assert "hello-interval 10" in s
    assert "dead-interval 40" in s


def test_juniper_isis_net_and_p2p():
    s = render_juniper(DEV_FULL)
    assert "set protocols isis interface ge-0/0/0.100 level-2 enable" in s
    assert "set protocols isis interface ge-0/0/0.100 point-to-point" in s
    assert "set protocols isis net 49.0001.0000.0000.0001.00" in s


# --- Cisco IOS stanza ------------------------------------------------------

def test_cisco_iface_uses_encapsulation_dot1q():
    s = render_cisco(DEV_FULL)
    assert "interface GigabitEthernet0/0.100" in s
    assert " encapsulation dot1Q 100" in s
    assert " ip address 192.168.0.1 255.255.255.0" in s
    assert " ipv6 address 2001:db8::1/64" in s


def test_cisco_bgp_neighbor_form():
    s = render_cisco(DEV_FULL)
    assert "router bgp 65000" in s
    assert " neighbor 192.168.0.2 remote-as 65000" in s
    assert " neighbor 192.168.0.2 timers 30 90" in s
    assert " neighbor 2001:db8::2 remote-as 65000" in s
    assert " address-family ipv6 unicast" in s
    assert "  neighbor 2001:db8::2 activate" in s


def test_cisco_ospf_uses_wildcard_mask():
    s = render_cisco(DEV_FULL)
    assert " network 192.168.0.2 0.0.0.255 area 0.0.0.0" in s
    assert " ip ospf hello-interval 10" in s
    assert " ip ospf dead-interval 40" in s
    assert " ip ospf network point-to-point" in s
    assert "ipv6 router ospf 1" in s
    assert " ipv6 ospf 1 area 0.0.0.0" in s
    assert " ipv6 ospf network point-to-point" in s


def test_cisco_isis_stanza():
    s = render_cisco(DEV_FULL)
    assert "router isis CORE" in s
    assert " net 49.0001.0000.0000.0001.00" in s
    assert " is-type level-2" in s
    assert " ip router isis CORE" in s
    assert " isis network point-to-point" in s


# --- Arista EOS stanza -----------------------------------------------------

def test_arista_iface_uses_dot1q():
    s = render_arista(DEV_FULL)
    assert "interface Ethernet1.100" in s
    assert "   encapsulation dot1q vlan 100" in s
    assert "   ip address 192.168.0.1/24" in s
    assert "   ipv6 address 2001:db8::1/64" in s


def test_arista_bgp_stanza():
    s = render_arista(DEV_FULL)
    assert "router bgp 65000" in s
    assert "   neighbor 192.168.0.2 remote-as 65000" in s
    assert "   neighbor 192.168.0.2 timers 30 90" in s
    assert "   address-family ipv6" in s
    assert "      neighbor 2001:db8::2 activate" in s


def test_arista_ospf_uses_cidr_network():
    s = render_arista(DEV_FULL)
    assert "   network 192.168.0.0/24 area 0.0.0.0" in s
    assert "   ip ospf hello-interval 10" in s
    assert "   ip ospf network point-to-point" in s


# --- Alternate-key resolution (display keys AND DB keys both work) --------

def test_display_key_dict_works():
    """When called from the client cache, the dict uses display keys
    like 'Device Name', 'IPv4', 'VLAN'. The helper must resolve both."""
    d = {
        "Device Name": "table-device",
        "Interface":   "ens2f1np1",
        "VLAN":        "200",
        "IPv4":        "10.20.0.2",
        "ipv4_mask":   "24",
        "IPv4 Gateway":"10.20.0.1",
        "bgp_config":  {"bgp_local_as": "65000", "ipv4_enabled": True},
    }
    s = render_cisco(d)
    assert "table-device" in s
    assert "interface GigabitEthernet0/0.200" in s
    assert " neighbor 10.20.0.2 remote-as 65000" in s


# --- Partial configs — sections only appear when relevant -----------------

def test_no_bgp_when_disabled():
    d = {k: v for k, v in DEV_FULL.items() if k != "bgp_config"}
    for vendor in (render_juniper, render_cisco, render_arista):
        s = vendor(d)
        assert "bgp" not in s.lower()


def test_no_ospf_when_disabled():
    d = {k: v for k, v in DEV_FULL.items() if k != "ospf_config"}
    for vendor in (render_juniper, render_cisco, render_arista):
        s = vendor(d)
        assert "ospf" not in s.lower()


def test_no_isis_when_disabled():
    d = {k: v for k, v in DEV_FULL.items() if k != "isis_config"}
    for vendor in (render_juniper, render_cisco, render_arista):
        s = vendor(d)
        assert "isis" not in s.lower()
        assert "iso" not in s.lower()  # Juniper family iso only for ISIS


def test_bare_device_still_renders_iface():
    """A device with only interface + VLAN + IPv4 (no protocols) still
    gets its interface stanza rendered — that's the minimal peering
    setup the operator needs on the upstream."""
    d = {
        "device_name": "bare",
        "interface":   "ens2f0np0",
        "vlan":        "10",
        "ipv4_address":"10.10.10.2",
        "ipv4_mask":   "24",
        "ipv4_gateway":"10.10.10.1",
    }
    s = render_juniper(d)
    assert "set interfaces ge-0/0/0 unit 10 family inet address 10.10.10.1/24" in s
    assert "bgp" not in s.lower()
    assert "ospf" not in s.lower()


# --- ISIS NET auto-derivation from loopback --------------------------------

def test_isis_net_derived_from_loopback_when_missing():
    """If the device didn't set isis_net explicitly, the renderer
    synthesizes one from the loopback IPv4 so the operator gets a
    unique NET per device instead of a duplicate."""
    d = dict(DEV_FULL)
    d["isis_config"] = {"isis_area": "CORE", "isis_level": "level-2-only"}
    d["loopback_ipv4"] = "192.255.0.42"
    s = render_cisco(d)
    # 192.255.0.42 → 1922 5500 0042 → 49.0001.1922.5500.0042.00
    assert " net 49.0001.1922.5500.0042.00" in s


def test_isis_net_synth_helper_directly():
    assert _isis_net_from_loopback("192.255.0.1") == "49.0001.1922.5500.0001.00"
    assert _isis_net_from_loopback("10.20.30.40") == "49.0001.0100.2003.0040.00"
    assert _isis_net_from_loopback("garbage") == "49.0001.0000.0000.0001.00"


# --- Sanity: unknown vendor doesn't explode --------------------------------

def test_render_all_shape():
    d = {"device_name": "x", "interface": "eth0", "vlan": "1"}
    result = render_all(d)
    assert set(result.keys()) == {"juniper", "cisco", "arista"}
    for k, v in result.items():
        assert isinstance(v, str)
        assert v.startswith("#" if k == "juniper" else "!")
