"""Wire-format tests for utils.mpls.build_mpls_stack (v0.2.64, SR-MPLS).

The MPLS shim header is 4 bytes per label, packed as:

    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
    |               Label (20 bits)                 | TC (3)| S | TTL (8 bits)                     |
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+

We pin every field by parsing the bytes ourselves so a scapy refactor
or a struct-format typo can't slip through. Ether ethertype must be
0x8847 in front of the stack. Only the bottom (last) label carries
S=1.
"""

import pytest

scapy = pytest.importorskip("scapy")

from scapy.layers.inet import IP
from scapy.layers.l2 import Ether

from utils.mpls import (
    ETHERTYPE_MPLS_UNICAST,
    build_mpls_stack,
    extract_mpls_labels,
)


def _decode_mpls_words(raw: bytes, offset: int, n: int) -> list:
    """Parse `n` MPLS shim headers starting at `offset` from raw bytes.
    Returns `[(label, tc, s, ttl), ...]` in transmission order."""
    out = []
    for _ in range(n):
        word = int.from_bytes(raw[offset:offset + 4], "big")
        out.append((
            word >> 12,            # label, 20 bits
            (word >> 9) & 0x7,     # tc, 3 bits
            (word >> 8) & 0x1,     # s (bottom-of-stack), 1 bit
            word & 0xff,           # ttl, 8 bits
        ))
        offset += 4
    return out


# ───────────────────────────────────── single-label stack
def test_single_label_has_bos_set_and_carries_payload():
    stack = build_mpls_stack([1000])
    frame = Ether(src="00:11:22:33:44:55", dst="aa:bb:cc:dd:ee:ff") / stack / IP(src="10.0.0.1", dst="10.0.0.2")
    raw = bytes(frame)
    assert Ether(raw).type == ETHERTYPE_MPLS_UNICAST
    [(lbl, tc, s, ttl)] = _decode_mpls_words(raw, offset=14, n=1)
    assert lbl == 1000 and tc == 0 and s == 1 and ttl == 64


# ───────────────────────────────────── multi-label SR-MPLS stack
def test_three_label_stack_only_bottom_has_s_bit():
    stack = build_mpls_stack([100, 200, 300])
    frame = Ether(src="00:11:22:33:44:55", dst="aa:bb:cc:dd:ee:ff") / stack / IP(src="10.0.0.1", dst="10.0.0.2")
    raw = bytes(frame)
    assert Ether(raw).type == ETHERTYPE_MPLS_UNICAST
    labels = _decode_mpls_words(raw, offset=14, n=3)
    # Order on the wire matches the list order (top of stack first).
    assert [w[0] for w in labels] == [100, 200, 300]
    # Only the bottom (last) label has s=1.
    assert [w[2] for w in labels] == [0, 0, 1]


def test_tc_and_ttl_applied_uniformly_to_every_label():
    """TC + TTL aren't auto-rewritten per-position; they must appear on
    every label in the stack."""
    stack = build_mpls_stack([16, 17, 18, 19], tc=5, ttl=128)
    frame = Ether() / stack / IP()
    labels = _decode_mpls_words(bytes(frame), offset=14, n=4)
    assert all(tc == 5 and ttl == 128 for (_, tc, _, ttl) in labels)


def test_label_n_emits_n_x_4_bytes():
    """N labels → exactly 4N bytes of MPLS header. Plus 14 Ether =
    14 + 4N before any payload."""
    for n in (1, 2, 5, 10):
        labels = list(range(1000, 1000 + n))
        frame = Ether() / build_mpls_stack(labels) / IP()
        # IP header is the first byte after the MPLS stack.
        offset = 14 + 4 * n
        # IPv4 version+IHL in the first byte ≥ 0x40 (v=4)
        assert (bytes(frame)[offset] >> 4) == 4, \
            f"IP didn't start at offset {offset} for n={n}"


# ───────────────────────────────────── validation
def test_empty_labels_raises_value_error():
    with pytest.raises(ValueError, match="non-empty"):
        build_mpls_stack([])


def test_label_out_of_range_raises():
    with pytest.raises(ValueError, match="20 bits"):
        build_mpls_stack([0x100000])   # 21 bits
    with pytest.raises(ValueError, match="20 bits"):
        build_mpls_stack([-1])


def test_tc_out_of_range_raises():
    with pytest.raises(ValueError, match="3 bits"):
        build_mpls_stack([100], tc=8)
    with pytest.raises(ValueError, match="3 bits"):
        build_mpls_stack([100], tc=-1)


def test_ttl_out_of_range_raises():
    with pytest.raises(ValueError, match="1 byte"):
        build_mpls_stack([100], ttl=256)
    with pytest.raises(ValueError, match="1 byte"):
        build_mpls_stack([100], ttl=-1)


# ───────────────────────────────────── extract_mpls_labels (config parser)
def test_extract_prefers_new_list_field():
    assert extract_mpls_labels({"mpls_labels": [10, 20, 30]}) == [10, 20, 30]


def test_extract_accepts_comma_separated_string():
    """GUI input is likely a single text field; tolerate that shape."""
    assert extract_mpls_labels({"mpls_labels": "100, 200,300"}) == [100, 200, 300]


def test_extract_accepts_hex_in_string():
    assert extract_mpls_labels({"mpls_labels": "0x10, 16, 0x20"}) == [16, 16, 32]


def test_extract_falls_back_to_legacy_singular():
    """Back-compat: pre-0.2.64 streams configured with a scalar
    `mpls_label` must still work via the new helper."""
    assert extract_mpls_labels({"mpls_label": 16}) == [16]


def test_extract_legacy_string_value_tolerated():
    """`mpls_label` from JSON is often a string."""
    assert extract_mpls_labels({"mpls_label": "100"}) == [100]


def test_extract_returns_empty_for_missing_or_falsy():
    for cfg in (
        {},
        {"mpls_label": None},
        {"mpls_label": ""},
        {"mpls_label": 0},
        {"mpls_labels": []},
        None,
        42,
    ):
        assert extract_mpls_labels(cfg) == [], f"failed on {cfg!r}"


def test_extract_legacy_invalid_string_returns_empty_not_raises():
    """If a legacy stream has garbage in `mpls_label`, return [] rather
    than blowing up — the caller treats empty as 'no MPLS for this
    stream' and the rest of the frame still goes out fine."""
    assert extract_mpls_labels({"mpls_label": "not-a-number"}) == []
