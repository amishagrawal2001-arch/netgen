"""ISIS NET-ID validator tests (v0.2.86).

Replaces the hardcoded 6-part check that lived inline in
``utils/devices_tab_isis.py``. The validator now supports the
variable-length area IDs RFC 1195 actually allows (8-20 byte NETs)
and enforces NSEL=00 — the old check missed both.
"""

import pytest

from utils.isis_net import validate_isis_net, is_short_area_form


# ─────────────────────────────────────────────── happy paths
@pytest.mark.parametrize("net", [
    "49.0001.0000.0000.0001.00",          # 10 bytes — common Cisco shape
    "49.0001.0002.0000.0000.0000.0001.00",  # 14 bytes — longer area
    "49.0001.AABB.CCDD.EEFF.00",          # mixed-case hex OK
    "49.0001.aabb.ccdd.eeff.00",          # all-lowercase OK
    "47.0001.0000.0000.0001.00",          # AFI 47 (GOSIP) — valid
    "49.0001.1234.5678.9abc.def0.0001.00",  # area + sysid pattern
])
def test_valid_full_nets_accepted(net):
    assert validate_isis_net(net) is None


@pytest.mark.parametrize("net", [
    "49.0001",       # AFI + 2-byte area
    "49.000102",     # without inner dots
    "49.0001.0002",  # AFI + 4-byte area
    "490001",        # no dots at all (2 bytes)
])
def test_short_area_form_accepted_when_flag_set(net):
    assert validate_isis_net(net, allow_short_area=True) is None


def test_short_area_form_rejected_when_flag_off():
    """Inline-edit path uses the strict default — short forms there
    would be padded by the dialog, not the table edit."""
    err = validate_isis_net("49.0001")
    assert err is not None
    assert "too short" in err.lower()


# ─────────────────────────────────────────────── empty / whitespace
@pytest.mark.parametrize("net", ["", "   ", None])
def test_empty_input_rejected(net):
    err = validate_isis_net(net)
    assert err is not None
    assert "empty" in err.lower()


def test_dots_only_rejected():
    err = validate_isis_net(".....")
    assert err is not None
    assert "no hex content" in err.lower()


# ─────────────────────────────────────────────── format errors
def test_non_hex_char_rejected_with_position():
    """The error message should name the offending char and its
    position so operators can fix the typo without guessing."""
    err = validate_isis_net("49.0001.ZZZZ.0000.0001.00")
    assert err is not None
    assert "Z" in err
    # Position 7 in the dot-stripped string '490001ZZZZ00000001 00'
    assert "position" in err


def test_odd_hex_char_count_rejected():
    """Each byte is 2 hex chars; odd total = malformed."""
    err = validate_isis_net("49.0001.0000.0000.001.00")  # 19 hex chars
    assert err is not None
    assert "odd" in err.lower()


def test_too_short_full_net_rejected():
    """7-byte NET (14 hex chars) is below the 8-byte AFI+Area+SysID+NSEL
    minimum even though it's well-formed hex."""
    err = validate_isis_net("49.0001.0000.0000")  # 7 bytes (14 hex chars)
    assert err is not None
    assert "too short" in err.lower()


def test_too_long_net_rejected():
    """21-byte NET (42 hex chars) exceeds the 20-byte max (AFI=1 +
    Area=13 + SysID=6 + NSEL=1)."""
    long_hex = "AA" * 21
    err = validate_isis_net(long_hex)
    assert err is not None
    assert "too long" in err.lower()


# ─────────────────────────────────────────────── NSEL enforcement
@pytest.mark.parametrize("nsel", ["01", "ff", "FF", "AA", "10"])
def test_nonzero_nsel_rejected(nsel):
    """RFC 1195 §3.1 requires NSEL=00 for IS-IS proper. Non-zero
    NSEL would indicate an OSI NSAP for a transport service."""
    err = validate_isis_net(f"49.0001.0000.0000.0001.{nsel}")
    assert err is not None
    assert "NSEL must be 00" in err


def test_zero_nsel_accepted():
    assert validate_isis_net("49.0001.0000.0000.0001.00") is None


# ─────────────────────────────────────────────── classification helper
@pytest.mark.parametrize("net", [
    "49.0001",
    "49",          # 1 byte
    "490001",      # 2 bytes (without dot)
    "49.0001.00",  # 3 bytes
])
def test_is_short_area_form_true_for_under_8_bytes(net):
    assert is_short_area_form(net) is True


@pytest.mark.parametrize("net", [
    "49.0001.0000.0000.0001.00",
    "49.0001.0002.0000.0000.0000.0001.00",
])
def test_is_short_area_form_false_for_full_net(net):
    assert is_short_area_form(net) is False


def test_is_short_area_form_handles_garbage_gracefully():
    """Classifier shouldn't throw on bad input — it just returns
    False (since the input isn't a parseable short form either)."""
    assert is_short_area_form("") is False
    assert is_short_area_form(None) is False
    assert is_short_area_form("garbage") is False


# ─────────────────────────────────────────────── regression: scope of error
def test_error_messages_dont_leak_python_internals():
    """The error strings end up in a QMessageBox the operator reads —
    no 'Traceback' / 'NoneType' / 'attribute' chatter."""
    for bad in ["", "garbage", "49.0001.ZZZZ.0000.0001.00",
                "49.0001.0000.0000.0001.FF"]:
        err = validate_isis_net(bad)
        if err is not None:
            lower = err.lower()
            assert "traceback" not in lower
            assert "nonetype" not in lower
            assert "attribute" not in lower
