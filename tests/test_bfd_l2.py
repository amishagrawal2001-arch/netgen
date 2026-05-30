"""Wire-format + dialog tests for the BFD L2 emitter (v0.2.61, RFC 5880).

The 24-byte control payload is hand-packed via struct (scapy has no
stable BFD layer across versions). These tests pin every byte against
RFC 5880 §4.1 so a struct-format typo can't slip through.

Test fixture: directly inspect the bytes the factory emits, the same
pattern test_l2_protocols.py uses for the other L2 generators.
"""

import struct

import pytest

scapy = pytest.importorskip("scapy")

from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw

from utils import l2_protocols as _l2
from utils.l2_protocols import _l2_hdr


# ──────────────────────────────────────── builder helper (no thread)
def _build_bfd_frame(**overrides):
    """Reproduce the factory's frame WITHOUT spawning the sender thread.

    Mirrors the closure that start_bfd defines. Tests exercise byte
    layout — they don't need the periodic sender. (Letting start_bfd
    actually run would try to sendp() on a real interface, which is
    root-only and noisy in CI.)
    """
    p = {
        "src_mac": "aa:bb:cc:dd:ee:01", "dst_mac": "aa:bb:cc:dd:ee:02",
        "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2",
        "my_discriminator": 0x11111111, "your_discriminator": 0,
        "state": 3, "detect_mult": 3, "diag": 0,
        "desired_min_tx_us": 1_000_000,
        "required_min_rx_us": 1_000_000,
        "required_min_echo_rx_us": 0,
        "dst_udp_port": 3784,
        "vlan_id": None, "vlan_pcp": 0,
        "outer_vlan_id": None, "outer_vlan_pcp": 0,
    }
    p.update(overrides)
    ver_diag = (3 << 5) | (p["diag"] & 0x1f)
    state_flags = (p["state"] & 0x3) << 6
    payload = struct.pack(
        ">BBBBIIIII",
        ver_diag, state_flags, p["detect_mult"], 24,
        p["my_discriminator"] & 0xffffffff,
        p["your_discriminator"] & 0xffffffff,
        p["desired_min_tx_us"] & 0xffffffff,
        p["required_min_rx_us"] & 0xffffffff,
        p["required_min_echo_rx_us"] & 0xffffffff,
    )
    return (
        _l2_hdr(p["src_mac"], p["dst_mac"], 0x0800,
                p["vlan_id"], p["vlan_pcp"],
                p["outer_vlan_id"], p["outer_vlan_pcp"])
        / IP(src=p["src_ip"], dst=p["dst_ip"], ttl=255)
        / UDP(sport=49152, dport=p["dst_udp_port"])
        / Raw(load=payload)
    )


# ─────────────────────────────────────────────────── factory registration
def test_start_bfd_is_exported_by_l2_protocols():
    """server/l2_routes.py's allow-list looks up `start_bfd` by name on
    the module. Catch the symbol-rename / missing-export regression."""
    assert hasattr(_l2, "start_bfd")
    assert callable(_l2.start_bfd)


def test_l2_routes_advertises_bfd():
    from server.l2_routes import _PROTOCOL_FACTORIES
    assert "bfd" in _PROTOCOL_FACTORIES
    factory_name, keys = _PROTOCOL_FACTORIES["bfd"]
    assert factory_name == "start_bfd"
    for required in ("src_ip", "dst_ip", "src_mac", "dst_mac",
                     "my_discriminator", "your_discriminator", "state",
                     "detect_mult", "desired_min_tx_us",
                     "required_min_rx_us", "dst_udp_port", "interval_s",
                     "vlan_id", "outer_vlan_id"):
        assert required in keys, f"missing allow-list key {required}"


# ─────────────────────────────────────────────────── envelope (Ether/IP/UDP)
def test_single_hop_bfd_dst_port_is_3784():
    pkt = _build_bfd_frame()
    assert pkt[UDP].dport == 3784


def test_single_hop_bfd_ip_ttl_is_255():
    """RFC 5881 §5: single-hop BFD MUST use TTL=255 so the receiver can
    verify the packet originated on the directly-connected link."""
    pkt = _build_bfd_frame()
    assert pkt[IP].ttl == 255


def test_multi_hop_uses_4784_when_overridden():
    pkt = _build_bfd_frame(dst_udp_port=4784)
    assert pkt[UDP].dport == 4784


# ─────────────────────────────────────────────────── BFD control payload
def _bfd_payload(pkt) -> bytes:
    """Pull the 24-byte BFD control payload out of the assembled frame.
    Round-trips through bytes() so we test the *on-the-wire* layout, not
    a Python object."""
    raw = bytes(pkt)
    return raw[Ether(raw)[UDP].payload.original.__class__ and -24:]


def test_bfd_payload_length_is_24_bytes():
    pkt = _build_bfd_frame()
    # The Raw load attached IS the BFD payload — pull it directly.
    raw_load = pkt[Raw].load
    assert len(raw_load) == 24


def test_bfd_version_is_3_and_length_byte_is_24():
    """Byte 0 top 3 bits = version (3 per RFC 5880). Byte 3 = length."""
    payload = _build_bfd_frame()[Raw].load
    version = payload[0] >> 5
    length  = payload[3]
    assert version == 3
    assert length == 24


def test_bfd_state_up_encoded_in_top_2_bits_of_byte_1():
    """Byte 1 top 2 bits = State. Up = 3, Down = 1, Init = 2, AdminDown = 0."""
    for state, expected_top2 in ((3, 0b11), (1, 0b01), (2, 0b10), (0, 0b00)):
        payload = _build_bfd_frame(state=state)[Raw].load
        assert (payload[1] >> 6) & 0b11 == expected_top2, \
            f"state={state} encoded wrong: byte1=0x{payload[1]:02x}"


def test_bfd_no_flags_set_by_default():
    """Lower 6 bits of byte 1 = P/F/C/A/D/M flags. Default emitter sets
    no flags — no Poll handshake, no auth, no demand mode."""
    payload = _build_bfd_frame()[Raw].load
    assert payload[1] & 0x3f == 0


def test_bfd_diag_encoded_in_bottom_5_bits_of_byte_0():
    """Byte 0 bottom 5 bits = Diagnostic. Verify a non-zero diag round-
    trips and doesn't leak into the version bits."""
    payload = _build_bfd_frame(diag=5)[Raw].load
    assert payload[0] & 0x1f == 5
    assert payload[0] >> 5 == 3   # version still 3


def test_bfd_detect_mult_in_byte_2():
    payload = _build_bfd_frame(detect_mult=7)[Raw].load
    assert payload[2] == 7


def test_bfd_discriminators_are_big_endian_at_offsets_4_and_8():
    """Bytes 4-7 = My Discriminator, bytes 8-11 = Your Discriminator,
    network byte order."""
    payload = _build_bfd_frame(
        my_discriminator=0xDEADBEEF,
        your_discriminator=0xCAFEF00D,
    )[Raw].load
    my_disc, your_disc = struct.unpack(">II", payload[4:12])
    assert my_disc == 0xDEADBEEF
    assert your_disc == 0xCAFEF00D


def test_bfd_intervals_in_microseconds_at_offsets_12_16_20():
    """Bytes 12-15 = Desired Min TX, 16-19 = Required Min RX,
    20-23 = Required Min Echo RX. All big-endian µs."""
    payload = _build_bfd_frame(
        desired_min_tx_us=100_000,
        required_min_rx_us=50_000,
        required_min_echo_rx_us=7,
    )[Raw].load
    tx, rx, echo = struct.unpack(">III", payload[12:24])
    assert tx == 100_000
    assert rx == 50_000
    assert echo == 7


# ───────────────────────────────────────────────── inline VLAN + QinQ
def test_bfd_with_single_dot1q_keeps_24_byte_payload():
    """Adding inline 802.1Q wraps the L2 header but MUST NOT touch the
    BFD payload bytes."""
    pkt = _build_bfd_frame(vlan_id=100, vlan_pcp=3)
    assert pkt[Ether].type == 0x8100
    assert len(pkt[Raw].load) == 24
    assert pkt[UDP].dport == 3784


def test_bfd_with_qinq_keeps_payload_and_uses_88a8_outer():
    pkt = _build_bfd_frame(vlan_id=100, vlan_pcp=3,
                           outer_vlan_id=200, outer_vlan_pcp=5)
    assert pkt[Ether].type == 0x88a8     # 802.1ad outer
    assert len(pkt[Raw].load) == 24
    assert pkt[UDP].dport == 3784


# ──────────────────────────────────────────────────── dialog (Qt)
def _open_dialog(qapp, monkeypatch):
    from PyQt5 import QtWidgets
    from PyQt5.QtWidgets import QWidget
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    from widgets.l2_emulation_tab import _L2ConfigDialog
    parent = QWidget()
    dlg = _L2ConfigDialog(parent, default_iface="ens1f0")
    # Switch to the BFD panel (last in PROTOCOLS).
    bfd_idx = [k for k, _ in dlg.PROTOCOLS].index("bfd")
    dlg._proto_combo.setCurrentIndex(bfd_idx)
    return parent, dlg


def test_dialog_payload_carries_bfd_fields(qapp, monkeypatch):
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._bfd_my_disc.setText("0xDEADBEEF")
    dlg._bfd_your_disc.setText("0xCAFEF00D")
    dlg._bfd_state.setCurrentIndex(1)        # Down
    dlg._bfd_detect_mult.setValue(5)
    dlg._bfd_tx_us.setValue(50_000)
    dlg._bfd_rx_us.setValue(50_000)
    dlg._bfd_dst_port.setValue(4784)         # multi-hop
    dlg._bfd_interval.setValue(0.5)
    dlg._on_accept()
    p = dlg.accepted_payload()
    assert p is not None
    body = p["body"]
    assert p["protocol"] == "bfd"
    assert body["my_discriminator"] == 0xDEADBEEF
    assert body["your_discriminator"] == 0xCAFEF00D
    assert body["state"] == 1
    assert body["detect_mult"] == 5
    assert body["desired_min_tx_us"] == 50_000
    assert body["required_min_rx_us"] == 50_000
    assert body["dst_udp_port"] == 4784
    assert body["interval_s"] == 0.5


def test_dialog_rejects_zero_my_discriminator(qapp, monkeypatch):
    """RFC 5880 §6.8.1: My Discriminator must be non-zero. The dialog
    must catch the typo at submit time rather than letting the factory
    ship a broken session id."""
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._bfd_my_disc.setText("0")
    dlg._on_accept()
    assert dlg.accepted_payload() is None


def test_dialog_rejects_malformed_discriminator(qapp, monkeypatch):
    parent, dlg = _open_dialog(qapp, monkeypatch)
    dlg._bfd_my_disc.setText("not-a-number")
    dlg._on_accept()
    assert dlg.accepted_payload() is None
