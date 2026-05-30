"""Wire-format + dialog tests for QinQ (802.1ad) inline encapsulation (v0.2.60).

`_l2_hdr` gets a second tag pair (`outer_vlan_id` / `outer_vlan_pcp`).
We pin the resulting bytes against the IEEE 802.1ad spec — outer TPID
0x88a8, inner TPID 0x8100, original payload ethertype on the inner —
plus length deltas vs the untagged and single-tagged variants. Same
style as the existing test_l2_protocols.py wire-format tests.
"""

import pytest

scapy = pytest.importorskip("scapy")

from utils.l2_protocols import _l2_hdr
from scapy.layers.l2 import Dot1Q, Ether


# ────────────────────────────────────────────────────── wire format
def test_qinq_outer_tpid_is_88a8_inner_is_8100():
    """802.1ad standard: outer S-Tag is 0x88a8, inner C-Tag is 0x8100,
    the protocol's original ethertype rides on the inner Dot1Q's
    ``type`` field."""
    hdr = _l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                  ethertype=0x0800,
                  vlan_id=100, vlan_pcp=3,
                  outer_vlan_id=200, outer_vlan_pcp=5)
    assert isinstance(hdr, Ether)
    # Outer ethertype on the Ether layer.
    assert hdr.type == 0x88a8
    # Two Dot1Q layers, outer first.
    dot1qs = list(hdr.iterpayloads())
    dot1qs = [p for p in [hdr.payload, hdr.payload.payload] if isinstance(p, Dot1Q)]
    assert len(dot1qs) == 2
    outer, inner = dot1qs[0], dot1qs[1]
    assert outer.vlan == 200 and outer.prio == 5
    assert outer.type == 0x8100   # next is the C-Tag
    assert inner.vlan == 100 and inner.prio == 3
    assert inner.type == 0x0800   # original payload ethertype preserved


def test_qinq_frame_is_4_bytes_longer_than_single_tagged():
    """QinQ adds one Dot1Q (4 bytes) over the single-tag case."""
    untagged = bytes(_l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                             ethertype=0x88cc))
    single   = bytes(_l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                             ethertype=0x88cc, vlan_id=100))
    qinq     = bytes(_l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                             ethertype=0x88cc, vlan_id=100,
                             outer_vlan_id=200))
    assert len(single) == len(untagged) + 4
    assert len(qinq)   == len(single)   + 4
    # 22 = 14 (Ether) + 4 (S-Tag) + 4 (C-Tag) for the QinQ header alone.
    assert len(qinq) == 22


def test_qinq_outer_pcp_encoded_in_tci():
    """The TCI byte 0 carries PCP in the top 3 bits + DEI + top 4 bits
    of VID. Verify outer PCP=7 + VID=4094 round-trips correctly."""
    hdr = _l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                  ethertype=0x88cc,
                  vlan_id=1, vlan_pcp=0,
                  outer_vlan_id=4094, outer_vlan_pcp=7)
    # Re-parse the bytes to confirm wire encoding (not just object state).
    raw = bytes(hdr)
    reparsed = Ether(raw)
    assert reparsed.type == 0x88a8
    outer = reparsed.payload
    assert isinstance(outer, Dot1Q)
    assert outer.vlan == 4094
    assert outer.prio == 7
    assert outer.type == 0x8100
    inner = outer.payload
    assert isinstance(inner, Dot1Q)
    assert inner.vlan == 1
    assert inner.type == 0x88cc


def test_outer_without_inner_raises_valueerror():
    """Invalid 802.1ad. Refuse rather than silently emit a confused
    single-tagged frame the operator didn't ask for."""
    with pytest.raises(ValueError, match="QinQ requires both"):
        _l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                ethertype=0x0800,
                outer_vlan_id=200)


def test_untagged_path_unchanged_by_qinq_kwargs():
    """No tags passed → still a bare Ether frame (no regression in the
    most common path)."""
    hdr = _l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                  ethertype=0x88cc)
    assert isinstance(hdr, Ether)
    assert hdr.type == 0x88cc
    assert not isinstance(hdr.payload, Dot1Q)


def test_single_tag_path_unchanged_when_outer_zero():
    """vlan_id set, outer_vlan_id 0 → same single-tag wire format the
    0.2.41 single-Dot1Q tests pinned."""
    hdr = _l2_hdr("aa:bb:cc:dd:ee:01", "ff:ff:ff:ff:ff:ff",
                  ethertype=0x88cc, vlan_id=100, vlan_pcp=3,
                  outer_vlan_id=0)
    assert hdr.type == 0x8100
    assert isinstance(hdr.payload, Dot1Q)
    assert hdr.payload.type == 0x88cc  # next is the original payload
    assert hdr.payload.vlan == 100
    assert hdr.payload.prio == 3
