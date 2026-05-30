"""Tests for utils.preflight — the pre-Apply sanity checker (v0.2.68).

Pure-function design pays off here: every check takes a device dict
and returns a list of findings, so no Qt, no Flask, no DB. Each
check + the aggregator gets its own focused test.
"""

import pytest

from utils import preflight as pf


# Reusable minimal device shapes.
def _bgp_device(**overrides):
    base = {
        "Device Name": "R1",
        "Interface":   "ens1f0",
        "IPv4":        "10.0.0.1/24",
        "Loopback IPv4": "1.1.1.1",
        "protocols":   ["BGP"],
        "bgp_config":  {"bgp_asn": "65001",
                        "bgp_remote_asn": "65002"},
    }
    base.update(overrides)
    return base


def _vxlan_device(**overrides):
    base = {
        "Device Name": "VTEP-1",
        "Interface":   "ens1f0",
        "vxlan_config": {"tunnels": [
            {"vni": 100, "local_ip": "10.0.0.1", "remote_ip": "10.0.0.2"},
        ]},
        "protocols": [],
    }
    base.update(overrides)
    return base


# ───────────────────────────────────────────── check_bgp_has_remote_asn
def test_bgp_with_remote_asn_yields_no_finding():
    assert pf.check_bgp_has_remote_asn(_bgp_device()) == []


def test_bgp_missing_remote_asn_is_an_error():
    d = _bgp_device(bgp_config={"bgp_asn": "65001"})
    findings = pf.check_bgp_has_remote_asn(d)
    assert len(findings) == 1
    assert findings[0]["level"] == pf.LEVEL_ERROR
    assert findings[0]["code"]  == "BGP_NO_REMOTE_ASN"
    # Device + interface labels round-trip so the GUI can show them.
    assert findings[0]["device_name"] == "R1"
    assert findings[0]["interface"]   == "ens1f0"


def test_no_bgp_protocol_skips_remote_asn_check():
    d = _bgp_device(protocols=[], bgp_config={})
    assert pf.check_bgp_has_remote_asn(d) == []


# ───────────────────────────────────────────── check_bgp_has_loopback
def test_bgp_with_loopback_yields_no_finding():
    assert pf.check_bgp_has_loopback(_bgp_device()) == []


def test_bgp_with_cidr_loopback_still_counts_as_set():
    """Loopback IPv4 may carry a /32 — _strip_cidr should mean it
    still satisfies the 'configured' test."""
    d = _bgp_device(**{"Loopback IPv4": "1.1.1.1/32"})
    assert pf.check_bgp_has_loopback(d) == []


def test_bgp_without_loopback_is_a_warning():
    d = _bgp_device(**{"Loopback IPv4": ""})
    findings = pf.check_bgp_has_loopback(d)
    assert len(findings) == 1
    assert findings[0]["level"] == pf.LEVEL_WARNING
    assert findings[0]["code"]  == "BGP_NO_LOOPBACK"


def test_no_bgp_protocol_skips_loopback_check():
    d = _bgp_device(protocols=[], **{"Loopback IPv4": ""})
    assert pf.check_bgp_has_loopback(d) == []


# ─────────────────────────────────── check_vxlan_has_required_fields
def test_well_formed_tunnels_list_yields_no_finding():
    assert pf.check_vxlan_has_required_fields(_vxlan_device()) == []


def test_flat_vxlan_shape_also_supported():
    """The codebase tolerates {vni, local_ip, remote_ip} at the top
    level as well as inside a tunnels list — checker must accept
    both."""
    d = {
        "Device Name": "VTEP-flat",
        "vxlan_config": {"vni": 100, "local_ip": "10.0.0.1",
                          "remote_ip": "10.0.0.2"},
    }
    assert pf.check_vxlan_has_required_fields(d) == []


def test_vxlan_missing_remote_ip_is_an_error():
    d = _vxlan_device(vxlan_config={
        "tunnels": [{"vni": 100, "local_ip": "10.0.0.1"}],
    })
    findings = pf.check_vxlan_has_required_fields(d)
    assert len(findings) == 1
    assert findings[0]["code"] == "VXLAN_MISSING_FIELDS"
    assert "remote_ip" in findings[0]["message"]


def test_vxlan_missing_multiple_fields_lists_them_all():
    d = _vxlan_device(vxlan_config={"tunnels": [{"vni": 100}]})
    findings = pf.check_vxlan_has_required_fields(d)
    assert len(findings) == 1
    assert "local_ip" in findings[0]["message"]
    assert "remote_ip" in findings[0]["message"]


def test_vxlan_empty_tunnels_list_is_a_warning():
    d = _vxlan_device(vxlan_config={"tunnels": []})
    findings = pf.check_vxlan_has_required_fields(d)
    assert len(findings) == 1
    assert findings[0]["code"] == "VXLAN_EMPTY"


def test_vxlan_no_config_skips_check():
    d = {"Device Name": "no-vxlan"}
    assert pf.check_vxlan_has_required_fields(d) == []


# ───────────────────────────────────────────── ISIS / OSPF area checks
def test_isis_with_area_yields_no_finding():
    d = {"Device Name": "R", "protocols": ["IS-IS"],
         "isis_config": {"area_id": "49.0001"}}
    assert pf.check_isis_has_area(d) == []


def test_isis_without_area_warns():
    d = {"Device Name": "R", "protocols": ["IS-IS"], "isis_config": {}}
    findings = pf.check_isis_has_area(d)
    assert len(findings) == 1
    assert findings[0]["code"] == "ISIS_NO_AREA"
    assert findings[0]["level"] == pf.LEVEL_WARNING


def test_isis_alternative_key_is_is_config_also_recognised():
    """Some old data stores ISIS as `is_is_config`. Accept either."""
    d = {"Device Name": "R", "protocols": ["IS-IS"],
         "is_is_config": {"area_id": "49.0001"}}
    assert pf.check_isis_has_area(d) == []


def test_ospf_without_area_warns():
    d = {"Device Name": "R", "protocols": ["OSPF"], "ospf_config": {}}
    findings = pf.check_ospf_has_area(d)
    assert len(findings) == 1
    assert findings[0]["code"] == "OSPF_NO_AREA"


def test_no_isis_or_ospf_protocol_skips_area_checks():
    d = {"Device Name": "R", "protocols": ["BGP"]}
    assert pf.check_isis_has_area(d) == []
    assert pf.check_ospf_has_area(d) == []


# ─────────────────────────────────────── check_duplicate_ipv4 (cross-device)
def test_unique_ipv4s_yield_no_finding():
    devs = [
        {"Device Name": "A", "IPv4": "10.0.0.1/24"},
        {"Device Name": "B", "IPv4": "10.0.0.2/24"},
        {"Device Name": "C", "IPv4": "10.0.0.3/24"},
    ]
    for d in devs:
        assert pf.check_duplicate_ipv4(d, devs) == []


def test_duplicate_ipv4_across_two_devices_flagged_on_each():
    devs = [
        {"Device Name": "A", "IPv4": "10.0.0.1/24"},
        {"Device Name": "B", "IPv4": "10.0.0.1/24"},
        {"Device Name": "C", "IPv4": "10.0.0.3/24"},
    ]
    a = pf.check_duplicate_ipv4(devs[0], devs)
    assert len(a) == 1
    assert a[0]["level"] == pf.LEVEL_ERROR
    assert "B" in a[0]["message"]
    b = pf.check_duplicate_ipv4(devs[1], devs)
    assert "A" in b[0]["message"]
    # C is unique.
    assert pf.check_duplicate_ipv4(devs[2], devs) == []


def test_duplicate_check_compares_bare_ip_not_cidr():
    """Two devices configured /24 vs /25 on the same address still
    collide — the check must strip the CIDR before comparing."""
    devs = [
        {"Device Name": "A", "IPv4": "10.0.0.1/24"},
        {"Device Name": "B", "IPv4": "10.0.0.1/25"},
    ]
    assert pf.check_duplicate_ipv4(devs[0], devs)[0]["code"] == "DUPLICATE_IPV4"


def test_duplicate_check_ignores_empty_ips():
    """A device with no IPv4 doesn't collide with anyone (including
    other devices that also have no IPv4)."""
    devs = [
        {"Device Name": "A", "IPv4": ""},
        {"Device Name": "B", "IPv4": ""},
    ]
    assert pf.check_duplicate_ipv4(devs[0], devs) == []


# ───────────────────────────────────────────── check_all_devices aggregator
def test_aggregator_groups_by_device_and_counts_levels():
    # Give each device a distinct IPv4 so we isolate the per-device
    # finding shape from the cross-device duplicate check (that
    # behaviour is exercised in test_aggregator_runs_cross_device_checks_too).
    devs = [
        _bgp_device(**{"Device Name": "R1",
                       "IPv4": "10.0.0.1/24",
                       "bgp_config": {"bgp_asn": "65001"}}),     # 1 error
        _bgp_device(**{"Device Name": "R2",
                       "IPv4": "10.0.0.2/24",
                       "Loopback IPv4": ""}),                    # 1 warning
        {"Device Name": "Clean", "IPv4": "10.0.0.99",
         "protocols": []},                                       # 0 findings
    ]
    report = pf.check_all_devices(devs)
    assert report["summary"]["error"]   == 1
    assert report["summary"]["warning"] == 1
    assert report["summary"]["ok"]      == 1   # Clean is finding-free
    assert report["summary"]["total"]   == 3
    # Per-device grouping
    assert "R1" in report["by_device"]
    assert "R2" in report["by_device"]
    assert "Clean" not in report["by_device"]   # only devices with findings


def test_aggregator_runs_cross_device_checks_too():
    devs = [
        {"Device Name": "A", "IPv4": "10.0.0.1"},
        {"Device Name": "B", "IPv4": "10.0.0.1"},
    ]
    report = pf.check_all_devices(devs)
    # Each device gets one DUPLICATE_IPV4 finding, both error-level.
    assert report["summary"]["error"] == 2
    assert report["summary"]["ok"]    == 0
    assert {f["code"] for f in report["findings"]} == {"DUPLICATE_IPV4"}


def test_aggregator_on_empty_deployment_returns_zeros():
    report = pf.check_all_devices([])
    assert report["summary"] == {"error": 0, "warning": 0,
                                  "ok": 0, "total": 0}
    assert report["findings"] == []
    assert report["by_device"] == {}


def test_aggregator_finding_shape_is_stable_for_gui():
    """Each finding has level / code / message at minimum — pin the
    shape so a GUI rewrite can't drift."""
    devs = [_bgp_device(bgp_config={"bgp_asn": "65001"})]
    report = pf.check_all_devices(devs)
    f = report["findings"][0]
    for key in ("level", "code", "message", "device_name"):
        assert key in f
    assert f["level"] in (pf.LEVEL_ERROR, pf.LEVEL_WARNING)
