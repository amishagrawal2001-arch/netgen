"""OSPF area-id validator + normaliser tests (v0.2.87).

Replaces the inline decimal-or-dotted check that lived in
``utils/devices_tab_ospf.py`` and adds NORMALISATION so the stored
config matches what FRR puts on the wire (``1`` → ``0.0.0.1``).
"""

import pytest

from utils.ospf_area import (
    validate_ospf_area_id,
    normalise_ospf_area_id,
    _int_to_dotted,
)


# ─────────────────────────────────────────── happy paths
@pytest.mark.parametrize("value,expected", [
    # Plain integer form — normalised to dotted-decimal.
    ("0",          "0.0.0.0"),
    ("1",          "0.0.0.1"),
    ("100",        "0.0.0.100"),
    ("256",        "0.0.1.0"),
    ("65535",      "0.0.255.255"),
    ("4294967295", "255.255.255.255"),
    # Dotted form — passes through (canonical, no leading zeros).
    ("0.0.0.0",         "0.0.0.0"),
    ("0.0.0.1",         "0.0.0.1"),
    ("10.0.0.1",        "10.0.0.1"),
    ("255.255.255.255", "255.255.255.255"),
])
def test_valid_inputs_normalise_correctly(value, expected):
    ok, norm, err = validate_ospf_area_id(value)
    assert ok, f"expected ok for {value!r}; got error={err!r}"
    assert err is None
    assert norm == expected


def test_whitespace_stripped():
    """Operators routinely paste with leading / trailing whitespace —
    don't reject."""
    ok, norm, _ = validate_ospf_area_id("  0.0.0.1  ")
    assert ok
    assert norm == "0.0.0.1"


# ─────────────────────────────────────────── int → dotted helper
@pytest.mark.parametrize("n,expected", [
    (0,         "0.0.0.0"),
    (1,         "0.0.0.1"),
    (255,       "0.0.0.255"),
    (256,       "0.0.1.0"),
    (0x01020304, "1.2.3.4"),
    (0xFFFFFFFF, "255.255.255.255"),
])
def test_int_to_dotted_round_trip(n, expected):
    """The conversion is the linchpin; pin every bit-shift."""
    assert _int_to_dotted(n) == expected


# ─────────────────────────────────────────── rejection
@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_input_rejected(bad):
    ok, norm, err = validate_ospf_area_id(bad)
    assert not ok
    assert norm is None
    assert "empty" in err.lower()


def test_negative_integer_rejected():
    ok, _, err = validate_ospf_area_id("-1")
    assert not ok
    # "-1" has no dot so the int branch runs; -1 fails the range check.
    assert "out of range" in err.lower() or "neither" in err.lower()


def test_integer_above_32_bits_rejected():
    ok, _, err = validate_ospf_area_id("4294967296")  # 2**32
    assert not ok
    assert "out of range" in err.lower()


def test_garbage_no_dots_rejected():
    ok, _, err = validate_ospf_area_id("garbage")
    assert not ok
    assert "neither" in err.lower()


@pytest.mark.parametrize("bad", [
    "1.2.3",          # too few octets
    "1.2.3.4.5",      # too many
    "1.2.3.256",      # octet > 255
    "1.2.3.-1",       # negative octet
    "1.2..4",         # empty octet
    "1.2.3.abc",      # non-numeric octet
    "a.b.c.d",        # all non-numeric
])
def test_malformed_dotted_rejected(bad):
    ok, norm, err = validate_ospf_area_id(bad)
    assert not ok, f"expected rejection for {bad!r}"
    assert norm is None
    assert err  # non-empty


# ─────────────────────────────────────────── normalise_ospf_area_id helper
def test_normalise_returns_dotted_on_valid():
    assert normalise_ospf_area_id("1") == "0.0.0.1"
    assert normalise_ospf_area_id("0.0.0.1") == "0.0.0.1"


def test_normalise_returns_none_on_invalid():
    assert normalise_ospf_area_id("garbage") is None
    assert normalise_ospf_area_id("") is None
    assert normalise_ospf_area_id(None) is None


# ─────────────────────────────────────────── error-message quality
def test_dotted_octet_error_names_which_octet():
    """When octet #3 is bad, error should say "octet #3" — operator
    fixes the typo without counting dots."""
    _, _, err = validate_ospf_area_id("1.2.999.4")
    assert err is not None
    assert "#3" in err


def test_dotted_part_count_error_names_actual_count():
    _, _, err = validate_ospf_area_id("1.2.3")
    assert err is not None
    assert "3 parts" in err  # the count from the error


def test_error_messages_dont_leak_python_internals():
    """Errors land in QMessageBox — no Python chatter."""
    for bad in ["", "garbage", "1.2.3", "999.0.0.0", "4294967296"]:
        ok, _, err = validate_ospf_area_id(bad)
        if not ok:
            lower = err.lower()
            assert "traceback" not in lower
            assert "nonetype" not in lower
            assert "valueerror" not in lower


# ─────────────────────────────────────────── special cases
def test_backbone_area_zero_accepted_in_both_forms():
    """Area 0 is the OSPF backbone; both ``0`` and ``0.0.0.0`` MUST
    accept and normalise to the same canonical value."""
    ok1, norm1, _ = validate_ospf_area_id("0")
    ok2, norm2, _ = validate_ospf_area_id("0.0.0.0")
    assert ok1 and ok2
    assert norm1 == norm2 == "0.0.0.0"


def test_leading_zeros_in_dotted_form_handled():
    """`001.002.003.004` is unusual but technically valid per
    everyday usage — operators paste it from some configs. Our
    parser uses int() which strips leading zeros, so it should
    canonicalise to "1.2.3.4"."""
    ok, norm, _ = validate_ospf_area_id("001.002.003.004")
    assert ok
    assert norm == "1.2.3.4"
