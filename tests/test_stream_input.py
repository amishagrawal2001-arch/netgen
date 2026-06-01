"""Pure-function validator tests for the Stream dialog (v0.2.96).

These cover `utils/stream_input.py` without Qt — the dialog wires
the same functions live (textChanged → red border) and at
submit-time (accept-override). Pinning the parsers here protects
the dialog's submit guard even if the Qt wiring is later
refactored.
"""

import pytest

from utils.stream_input import (
    validate_mac,
    is_zero_mac,
    validate_ipv4,
    validate_ipv6,
    validate_frame_sizes,
    collect_errors,
)


# ──────────────────────────────────────────── MAC
class TestValidateMac:
    @pytest.mark.parametrize("good", [
        "00:11:22:33:44:55",
        "ff:ff:ff:ff:ff:ff",
        "0a:1b:2c:3d:4e:5f",
        "AA:BB:CC:DD:EE:FF",      # uppercase
        "00-11-22-33-44-55",      # dash separator
        "00:00:00:00:00:00",      # all-zero — accepted (see is_zero_mac)
    ])
    def test_accepts_well_formed_macs(self, good):
        assert validate_mac(good) is None

    @pytest.mark.parametrize("bad", [
        "",
        "garbage",
        "00:11:22:33:44",          # 5 octets — too short
        "00:11:22:33:44:55:66",    # 7 octets — too long
        "00:11:22:33:44:GG",       # non-hex
        "0:1:2:3:4:5",             # single-digit octets — rejected (strict)
        "00.11.22.33.44.55",       # dotted form — rejected per docstring
        "0011.2233.4455",          # Cisco-style — rejected
        " 00:11:22:33:44:55 ",     # stripped check trims it: still bad? — actually leading/trailing whitespace IS stripped
        "00:11:22:café:44:55",     # unicode
    ])
    def test_rejects_garbage(self, bad):
        # Whitespace-trimmed value matches a valid MAC; carve out
        # the one exception that's actually valid after .strip().
        if bad.strip() == "00:11:22:33:44:55":
            assert validate_mac(bad) is None
            return
        assert validate_mac(bad) is not None

    def test_rejects_none(self):
        assert validate_mac(None) is not None

    def test_rejects_non_string(self):
        assert validate_mac(0x001122334455) is not None
        assert validate_mac(["00", "11", "22", "33", "44", "55"]) is not None


class TestIsZeroMac:
    def test_pure_zero_mac_is_zero(self):
        assert is_zero_mac("00:00:00:00:00:00") is True

    def test_dash_zero_mac_is_zero(self):
        assert is_zero_mac("00-00-00-00-00-00") is True

    def test_nonzero_mac_is_not_zero(self):
        assert is_zero_mac("00:11:22:33:44:55") is False

    def test_invalid_mac_is_not_zero(self):
        # Garbage should be False, not raise.
        assert is_zero_mac("garbage") is False
        assert is_zero_mac("") is False


# ──────────────────────────────────────────── IPv4
class TestValidateIpv4:
    @pytest.mark.parametrize("good", [
        "0.0.0.0",          # default — accepted
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "255.255.255.255",
        " 10.0.0.5 ",       # leading/trailing whitespace
    ])
    def test_accepts_well_formed_ipv4(self, good):
        assert validate_ipv4(good) is None

    @pytest.mark.parametrize("bad", [
        "",
        "999.999.999.999",
        "10.0.0.256",
        "10.0.0",
        "10.0.0.0.5",
        "::1",              # v6 in v4-only field
        "10.0.0.a",
        "not-an-ip",
        "10.0.0.-1",
    ])
    def test_rejects_garbage(self, bad):
        assert validate_ipv4(bad) is not None

    def test_rejects_none(self):
        assert validate_ipv4(None) is not None


# ──────────────────────────────────────────── IPv6
class TestValidateIpv6:
    @pytest.mark.parametrize("good", [
        "::",
        "::1",
        "2001:db8::1",
        "fe80::1",
        "2001:0db8:0000:0000:0000:0000:0000:0001",
    ])
    def test_accepts_well_formed_ipv6(self, good):
        assert validate_ipv6(good) is None

    @pytest.mark.parametrize("bad", [
        "",
        "10.0.0.5",         # v4 in v6-only field
        "2001:db8::g::1",
        "garbage",
        "2001::1::2",       # double :: ambiguous
    ])
    def test_rejects_garbage(self, bad):
        assert validate_ipv6(bad) is not None


# ──────────────────────────────────────────── frame sizes
class TestValidateFrameSizes:
    def test_fixed_in_range_passes(self):
        assert validate_frame_sizes(fixed=128, minimum=None, maximum=None,
                                     frame_type="fixed") is None
        assert validate_frame_sizes(fixed=64, minimum=None, maximum=None,
                                     frame_type="fixed") is None
        assert validate_frame_sizes(fixed=1518, minimum=None, maximum=None,
                                     frame_type="fixed") is None

    def test_fixed_out_of_range_fails(self):
        assert validate_frame_sizes(fixed=63, minimum=None, maximum=None,
                                     frame_type="fixed") is not None
        assert validate_frame_sizes(fixed=1519, minimum=None, maximum=None,
                                     frame_type="fixed") is not None

    def test_random_min_le_max_passes(self):
        assert validate_frame_sizes(fixed=None, minimum=64, maximum=1518,
                                     frame_type="random") is None
        # Equal min/max is fine — operator wants a single size in
        # random mode (degenerate but legal).
        assert validate_frame_sizes(fixed=None, minimum=512, maximum=512,
                                     frame_type="random") is None

    def test_random_min_gt_max_fails(self):
        err = validate_frame_sizes(fixed=None, minimum=1518, maximum=64,
                                     frame_type="random")
        assert err is not None
        assert "min" in err.lower() and "max" in err.lower()

    def test_imix_uses_min_max(self):
        assert validate_frame_sizes(fixed=None, minimum=64, maximum=1518,
                                     frame_type="imix") is None

    def test_random_missing_min_max_fails(self):
        assert validate_frame_sizes(fixed=None, minimum=None, maximum=1518,
                                     frame_type="random") is not None
        assert validate_frame_sizes(fixed=None, minimum=64, maximum=None,
                                     frame_type="random") is not None

    def test_unknown_frame_type_defaults_to_fixed(self):
        # Defensive: if a future combo entry slips through with a
        # name the validator doesn't recognise, fall back to the
        # fixed-only branch (least surprise).
        assert validate_frame_sizes(fixed=128, minimum=None, maximum=None,
                                     frame_type="something_new") is None


# ──────────────────────────────────────────── batch helper
class TestCollectErrors:
    def test_returns_empty_list_when_all_valid(self):
        pairs = [
            ("MAC", "00:11:22:33:44:55", validate_mac),
            ("IPv4", "10.0.0.5", validate_ipv4),
        ]
        assert collect_errors(pairs) == []

    def test_returns_only_invalid_entries(self):
        pairs = [
            ("Source MAC", "00:11:22:33:44:55", validate_mac),
            ("Destination MAC", "garbage", validate_mac),
            ("Source IPv4", "10.0.0.5", validate_ipv4),
            ("Destination IPv4", "999.999.999.999", validate_ipv4),
        ]
        errors = collect_errors(pairs)
        assert len(errors) == 2
        labels = [label for label, _ in errors]
        assert "Destination MAC" in labels
        assert "Destination IPv4" in labels
        assert "Source MAC" not in labels
