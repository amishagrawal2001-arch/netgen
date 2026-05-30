"""L2 dialog validation tests (v0.2.81).

Pinned behaviour after the L2 emulation audit:

* MAC + IP address fields validate at submit time, not at
  scapy-frame-building time. Typos surface in the dialog with a
  clear reason, not as opaque "invalid MAC" in the Last Error column.
* VRRPv2 + IPv6 virtual IP combo is rejected up-front (backend
  silently reverted to v3 before).
* PIM generation_id is bounds-checked against the 32-bit field
  width (RFC 7761 §4.9.5).
"""

import pytest
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QWidget


# ────────────────────────────────────────── pure validator unit tests
from widgets.l2_emulation_tab import _validate_mac, _validate_ip


@pytest.mark.parametrize("good_mac", [
    "00:11:22:33:44:55",
    "ff:ff:ff:ff:ff:ff",
    "AB:CD:EF:01:02:03",
    "00:00:5e:00:01:01",   # the VRRP virtual MAC
])
def test_validate_mac_accepts_well_formed(good_mac):
    assert _validate_mac(good_mac) is None


@pytest.mark.parametrize("bad_mac", [
    "",
    "00:11:22:33:44",          # too short
    "00:11:22:33:44:55:66",    # too long
    "00:11:22:33:44:ZZ",       # non-hex
    "00-11-22-33-44-55",       # dashes
    "001122334455",            # bare hex
    "192.168.1.1",             # IP shape
])
def test_validate_mac_rejects_bad_input(bad_mac):
    assert _validate_mac(bad_mac) is not None


@pytest.mark.parametrize("good,family", [
    ("192.168.1.1", "any"),
    ("10.0.0.1",    "v4"),
    ("0.0.0.0",     "v4"),
    ("2001:db8::1", "v6"),
    ("fe80::1",     "v6"),
    ("::1",         "any"),
])
def test_validate_ip_accepts_well_formed(good, family):
    assert _validate_ip(good, family=family) is None


@pytest.mark.parametrize("bad,family", [
    ("",              "any"),
    ("192.168.1.999", "any"),
    ("not.an.ip",     "any"),
    ("gggg::1",       "any"),
    ("2001:db8::1",   "v4"),    # IPv6 in v4-required slot
    ("192.168.1.1",   "v6"),    # IPv4 in v6-required slot
])
def test_validate_ip_rejects_bad_input(bad, family):
    assert _validate_ip(bad, family=family) is not None


# ──────────────────────────────────── dialog-level integration tests
def _open(qapp, monkeypatch):
    """Build the L2 config dialog with QMessageBox silenced so we
    can probe payloads without a modal blocking the test."""
    captured = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        staticmethod(lambda *a, **k: captured.append((a, k)) or 0),
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    from widgets.l2_emulation_tab import _L2ConfigDialog
    parent = QWidget()
    dlg = _L2ConfigDialog(parent, default_iface="ens1f0")
    return parent, dlg, captured


def _select(dlg, proto):
    combo = dlg._proto_combo
    for i in range(combo.count()):
        if combo.itemData(i) == proto:
            combo.setCurrentIndex(i)
            return
    raise AssertionError(f"protocol {proto} missing from combo")


def test_lacp_bad_mac_rejected(qapp, monkeypatch):
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "lacp")
    dlg._lacp_system_mac.setText("00:11:22:33:44:ZZ")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    # First warning message references the System MAC field.
    assert any("System MAC" in str(args) for args, _ in captured)


def test_bfd_bad_dst_ip_rejected(qapp, monkeypatch):
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "bfd")
    dlg._bfd_dst_ip.setText("not-an-ip")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert any("Destination IP" in str(args) for args, _ in captured)


def test_pim_bad_src_mac_rejected(qapp, monkeypatch):
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "pim")
    dlg._pim_src_mac.setText("not-a-mac")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert any("Source MAC" in str(args) for args, _ in captured)


def test_pim_gen_id_overflow_rejected(qapp, monkeypatch):
    """v0.2.81 #3: PIM generation_id > 0xFFFFFFFF is rejected
    explicitly instead of silently truncated by scapy."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "pim")
    dlg._pim_generation_id.setText("0x100000000")  # 2^32, one too big
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert any("out of range" in str(args) or "32-bit" in str(args)
               for args, _ in captured)


def test_vrrp_v2_with_ipv6_virtual_ip_rejected(qapp, monkeypatch):
    """v0.2.81 #2: backend used to silently revert to v3 when v2 +
    IPv6 was submitted. Now rejected at the dialog with a clear
    message naming the RFCs."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    # Pick v2 + an IPv6 vip
    for i in range(dlg._vrrp_version.count()):
        if dlg._vrrp_version.itemData(i) == 2:
            dlg._vrrp_version.setCurrentIndex(i)
            break
    dlg._vrrp_virtual_ips.setText("2001:db8::254")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
    assert any("VRRPv2 is IPv4-only" in str(args) for args, _ in captured)


def test_vrrp_v3_with_ipv6_accepted(qapp, monkeypatch):
    """The legitimate v3 + IPv6 combo MUST still work."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    # v3 is default; just set family + ipv6 ip
    for i in range(dlg._vrrp_family.count()):
        if dlg._vrrp_family.itemData(i) == "ipv6":
            dlg._vrrp_family.setCurrentIndex(i)
            break
    dlg._vrrp_virtual_ips.setText("fe80::254")
    dlg._vrrp_src_ip.setText("fe80::1")
    dlg._vrrp_src_mac.setText("00:11:22:33:44:01")
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    assert p["body"]["version"] == 3
    assert p["body"]["family"] == "ipv6"
    assert p["body"]["virtual_ips"] == ["fe80::254"]


def test_igmp_zero_group_accepted_as_general_query(qapp, monkeypatch):
    """0.0.0.0 is the RFC-2236 General Query group address — must
    NOT be rejected by the IPv4-validate check."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "igmp")
    dlg._igmp_group.setText("0.0.0.0")
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    assert p["body"]["group"] == "0.0.0.0"


def test_vrrp_auth_fields_present_and_enabled_for_v2(qapp, monkeypatch):
    """v0.2.83: VRRPv2 dialog gains auth_type + auth_data fields,
    enabled when version=v2."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    # Switch to v2 explicitly.
    for i in range(dlg._vrrp_version.count()):
        if dlg._vrrp_version.itemData(i) == 2:
            dlg._vrrp_version.setCurrentIndex(i)
            break
    assert hasattr(dlg, "_vrrp_auth_type")
    assert hasattr(dlg, "_vrrp_auth_data")
    assert dlg._vrrp_auth_type.isEnabled()
    assert dlg._vrrp_auth_data.isEnabled()
    # All 3 RFC 3768 §5.3.6 codes present.
    codes = [dlg._vrrp_auth_type.itemData(i)
             for i in range(dlg._vrrp_auth_type.count())]
    assert codes == [0, 1, 2]


def test_vrrp_auth_fields_disabled_for_v3(qapp, monkeypatch):
    """RFC 5798 §5.1 removed authentication from VRRPv3 — the dialog
    fields must disable when v3 is picked, with a tooltip naming
    the RFC."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    # v3 is the default; explicit pick to be safe.
    for i in range(dlg._vrrp_version.count()):
        if dlg._vrrp_version.itemData(i) == 3:
            dlg._vrrp_version.setCurrentIndex(i)
            break
    assert not dlg._vrrp_auth_type.isEnabled()
    assert not dlg._vrrp_auth_data.isEnabled()
    tip = dlg._vrrp_auth_type.toolTip()
    assert "RFC 5798" in tip or "v3" in tip.lower()


def test_vrrp_auth_data_truncated_to_8_chars_via_maxlength(qapp, monkeypatch):
    """The auth_data field is bounded to 8 chars by QLineEdit's
    maxLength so the operator can't enter more than the RFC allows."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    assert dlg._vrrp_auth_data.maxLength() == 8


def test_vrrp_v2_payload_carries_auth_fields(qapp, monkeypatch):
    """End-to-end through _on_accept: pick v2 + auth_type=1 +
    auth_data='secret', confirm both keys reach the body."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "vrrp")
    for i in range(dlg._vrrp_version.count()):
        if dlg._vrrp_version.itemData(i) == 2:
            dlg._vrrp_version.setCurrentIndex(i)
            break
    # Set IPv4 vips so v2 doesn't trip the IPv6 guard.
    dlg._vrrp_virtual_ips.setText("192.168.1.254")
    dlg._vrrp_src_ip.setText("10.0.0.1")
    dlg._vrrp_src_mac.setText("00:00:5e:00:01:01")
    # Pick Simple Text Password.
    for i in range(dlg._vrrp_auth_type.count()):
        if dlg._vrrp_auth_type.itemData(i) == 1:
            dlg._vrrp_auth_type.setCurrentIndex(i)
            break
    dlg._vrrp_auth_data.setText("secret")
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    assert p["body"]["auth_type"] == 1
    assert p["body"]["auth_data"] == "secret"


def test_igmp_version_combo_offers_v1(qapp, monkeypatch):
    """v0.2.82: IGMPv1 (RFC 1112) is now available alongside v2/v3."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "igmp")
    versions = [dlg._igmp_version.itemData(i)
                for i in range(dlg._igmp_version.count())]
    assert 1 in versions
    assert 2 in versions
    assert 3 in versions


def test_igmp_default_version_unchanged_at_v2(qapp, monkeypatch):
    """v0.2.82: adding v1 to the combo must not change the default
    (v2 has been the default since the protocol shipped — operators
    rely on it)."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "igmp")
    assert dlg._igmp_version.currentData() == 2


def test_igmpv1_dialog_payload_round_trips(qapp, monkeypatch):
    """v0.2.82: selecting v1 ships version=1 in the body so the
    server-side factory takes the v1 branch."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "igmp")
    for i in range(dlg._igmp_version.count()):
        if dlg._igmp_version.itemData(i) == 1:
            dlg._igmp_version.setCurrentIndex(i)
            break
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    assert p["body"]["version"] == 1


def test_lacp_good_input_round_trips(qapp, monkeypatch):
    """Sanity: with all good inputs the dialog accepts and the
    payload carries the MAC verbatim."""
    parent, dlg, captured = _open(qapp, monkeypatch)
    _select(dlg, "lacp")
    dlg._lacp_system_mac.setText("AA:BB:CC:DD:EE:01")
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    assert p["body"]["system_mac"] == "AA:BB:CC:DD:EE:01"
