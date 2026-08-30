"""v0.5.224 — duplicate loopback / interface-IP / MAC detection.

Regression driver: on srv06 two devices ended up with the same
loopback_ipv4 (`192.255.0.1`). FRR derives OSPF router-id from the
loopback, so both routers had the same router-id and OSPF adjacency
collapsed to Init/DROther (they read each other's Hellos and treated
the peer as themselves). BGP survived because TCP + peer-IP + AS is
what identifies a BGP session; router-id is only informational.

The fix has three layers:
  1. utils/address_collision.py — shared helper
  2. run_tgen_server.py — HTTP 409 on /api/device/apply
  3. widgets/add_device_dialog.py — pre-fill next-available loopback
     + MessageBox on Save before the round-trip

These tests exercise (1) directly. Layers (2) and (3) call the same
helper, so a green (1) means the same decisions land in both places.
"""

from utils.address_collision import (
    find_conflict,
    next_available_loopback_ipv4,
    next_available_loopback_ipv6,
)


# --- next_available_loopback_ipv4 ------------------------------------------

def test_next_ipv4_empty_returns_start():
    assert next_available_loopback_ipv4([]) == "192.255.0.1"


def test_next_ipv4_returns_max_plus_one():
    devs = [
        {"loopback_ipv4": "192.255.0.1"},
        {"loopback_ipv4": "192.255.0.5"},
    ]
    assert next_available_loopback_ipv4(devs) == "192.255.0.6"


def test_next_ipv4_skips_dot_zero():
    devs = [{"loopback_ipv4": "192.255.0.254"}]
    # .255 (broadcast) + .0 (network) both skipped → jumps to 192.255.1.1
    assert next_available_loopback_ipv4(devs) == "192.255.1.1"


def test_next_ipv4_ignores_malformed_peer():
    devs = [
        {"loopback_ipv4": "not-an-ip"},
        {"loopback_ipv4": "192.255.0.3"},
    ]
    assert next_available_loopback_ipv4(devs) == "192.255.0.4"


def test_next_ipv4_srv06_repro():
    """Exactly the srv06 state: one device already at 192.255.0.1.
    The dialog should now suggest .2 instead of blindly reusing .1."""
    devs = [{"device_name": "device1", "loopback_ipv4": "192.255.0.1"}]
    assert next_available_loopback_ipv4(devs) == "192.255.0.2"


# --- next_available_loopback_ipv6 ------------------------------------------

def test_next_ipv6_empty_returns_start():
    assert next_available_loopback_ipv6([]) == "2001:ff00::1"


def test_next_ipv6_returns_max_plus_one():
    devs = [
        {"loopback_ipv6": "2001:ff00::1"},
        {"loopback_ipv6": "2001:ff00::5"},
    ]
    assert next_available_loopback_ipv6(devs) == "2001:ff00::6"


def test_next_ipv6_canonicalizes():
    # Full form and abbreviated form should be treated as equal.
    devs = [{"loopback_ipv6": "2001:ff00:0000:0000:0000:0000:0000:0001"}]
    assert next_available_loopback_ipv6(devs) == "2001:ff00::2"


# --- find_conflict: global-scope fields (loopback) -------------------------

def test_find_conflict_loopback_ipv4_hits():
    devs = [
        {"device_id": "d1", "device_name": "device1", "loopback_ipv4": "192.255.0.1"},
    ]
    hit = find_conflict("loopback_ipv4", "192.255.0.1", devs)
    assert hit == ("d1", "device1")


def test_find_conflict_loopback_ipv4_whitespace():
    """Trailing whitespace shouldn't hide a real collision — the
    dialog trims fields but not always in every path."""
    devs = [{"device_id": "d1", "device_name": "device1", "loopback_ipv4": "192.255.0.1"}]
    hit = find_conflict("loopback_ipv4", "  192.255.0.1  ", devs)
    assert hit == ("d1", "device1")


def test_find_conflict_loopback_ipv4_miss():
    devs = [{"device_id": "d1", "device_name": "device1", "loopback_ipv4": "192.255.0.1"}]
    assert find_conflict("loopback_ipv4", "192.255.0.2", devs) is None


def test_find_conflict_loopback_ipv6_hits():
    devs = [{"device_id": "d1", "device_name": "device1", "loopback_ipv6": "2001:ff00::1"}]
    hit = find_conflict("loopback_ipv6", "2001:ff00::1", devs)
    assert hit == ("d1", "device1")


def test_find_conflict_excludes_self_on_edit():
    """Edit path re-submits the same device with the same loopback —
    must not self-collide."""
    devs = [{"device_id": "d1", "device_name": "device1", "loopback_ipv4": "192.255.0.1"}]
    assert find_conflict("loopback_ipv4", "192.255.0.1", devs, exclude_id="d1") is None


def test_find_conflict_empty_value_no_hit():
    devs = [{"device_id": "d1", "device_name": "device1", "loopback_ipv4": "192.255.0.1"}]
    assert find_conflict("loopback_ipv4", "", devs) is None
    assert find_conflict("loopback_ipv4", None, devs) is None


def test_find_conflict_ignores_empty_peer_loopback():
    devs = [
        {"device_id": "d1", "device_name": "device1", "loopback_ipv4": ""},
        {"device_id": "d2", "device_name": "device2", "loopback_ipv4": None},
    ]
    assert find_conflict("loopback_ipv4", "192.255.0.1", devs) is None


# --- find_conflict: L2-scoped fields (interface IP + MAC) ------------------

def test_ipv4_conflict_same_l2_hits():
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "ens2f0np0", "vlan": "100",
        "ipv4_address": "192.168.0.2",
    }]
    hit = find_conflict(
        "ipv4_address", "192.168.0.2", devs,
        interface="ens2f0np0", vlan_id="100",
    )
    assert hit == ("d1", "device1")


def test_ipv4_same_value_different_vlan_no_hit():
    """Same IP on different VLANs is fine — different broadcast domains."""
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "ens2f0np0", "vlan": "100",
        "ipv4_address": "192.168.0.2",
    }]
    hit = find_conflict(
        "ipv4_address", "192.168.0.2", devs,
        interface="ens2f0np0", vlan_id="200",
    )
    assert hit is None


def test_ipv4_same_value_different_interface_no_hit():
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "ens2f0np0", "vlan": "100",
        "ipv4_address": "192.168.0.2",
    }]
    hit = find_conflict(
        "ipv4_address", "192.168.0.2", devs,
        interface="ens2f1np1", vlan_id="100",
    )
    assert hit is None


def test_l2_field_without_interface_returns_none():
    """The server passes interface + vlan; if a caller forgets to,
    we return None rather than false-positive across all devices."""
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "ens2f0np0", "vlan": "100",
        "ipv4_address": "192.168.0.2",
    }]
    assert find_conflict("ipv4_address", "192.168.0.2", devs) is None


def test_ipv4_display_form_iface_normalized():
    """Server stores interface as `vlan200@ens2f0np0` (display form)
    for some paths. Base iface should still match a bare `ens2f0np0`."""
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "vlan200@ens2f0np0", "vlan": "200",
        "ipv4_address": "192.168.10.2",
    }]
    hit = find_conflict(
        "ipv4_address", "192.168.10.2", devs,
        interface="ens2f0np0", vlan_id="200",
    )
    assert hit == ("d1", "device1")


def test_mac_conflict_same_l2_hits():
    devs = [{
        "device_id": "d1", "device_name": "device1",
        "interface": "ens2f0np0", "vlan": "100",
        "mac_address": "AA:BB:CC:DD:EE:01",
    }]
    hit = find_conflict(
        "mac_address", "aa:bb:cc:dd:ee:01", devs,
        interface="ens2f0np0", vlan_id="100",
    )
    assert hit == ("d1", "device1")


# --- srv06 reproducer ------------------------------------------------------

def test_srv06_two_device_ospf_router_id_collision():
    """The exact srv06 state we found: device1 already exists at
    loopback 192.255.0.1; the dialog defaulted device2 to the same
    value and the server accepted it. The v0.5.224 collision helper
    catches this before it lands in the DB.

    The three DIFFERENT VLANs / IPs are correctly non-colliding on
    the L2-scoped fields — only the shared loopback (which becomes
    OSPF router-id) is the problem, and the helper flags exactly it.
    """
    devs = [{
        "device_id": "9be1dcb8-7fcd-4a71-91c0-ace271e17744",
        "device_name": "device1",
        "interface": "ens2f0np0",
        "vlan": "100",
        "ipv4_address": "192.168.0.2",
        "ipv6_address": "2001:db8::2",
        "loopback_ipv4": "192.255.0.1",
        "loopback_ipv6": "2001:ff00::1",
    }]

    # The client's Add Device seeds these from next-available — pre-fill
    # should suggest .2, not .1.
    assert next_available_loopback_ipv4(devs) == "192.255.0.2"
    assert next_available_loopback_ipv6(devs) == "2001:ff00::2"

    # If the operator overrides and enters .1 anyway, the collision
    # helper (used by both dialog and server 409 gate) catches it.
    hit = find_conflict("loopback_ipv4", "192.255.0.1", devs)
    assert hit is not None
    assert hit[1] == "device1"

    # But the DIFFERENT interface IP on the DIFFERENT VLAN is fine.
    other_ip_hit = find_conflict(
        "ipv4_address", "192.168.10.2", devs,
        interface="ens2f0np0", vlan_id="200",
    )
    assert other_ip_hit is None
