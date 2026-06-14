"""v0.5.130: dialog save normalizes increment flags to match count.

Pre-fix scapy and DPDK disagreed on what 'cycle' means:
- DPDK tx_worker: count >= 2 → cycle (ignores checkbox)
- Scapy generic.py: checkbox=True AND count >= 2 → cycle

So a stream saved with (count=64, checkbox=False) cycled under
DPDK but not scapy. And (count=1, checkbox=True) cycled scapy by
one port (no-op) but not DPDK. The persisted data was internally
inconsistent.

v0.5.130 normalizes at save: the checkbox/mode is forced to
match the count value. count >= 2 → cycling on; count == 1 →
cycling off. Both engines then see the same intent.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _normalize(stream_details):
    """Call the module-level normalizer directly — no Qt needed."""
    from widgets.stream_dialog import _normalize_increment_flags
    _normalize_increment_flags(stream_details)
    return stream_details


# ----- UDP --------------------------------------------------------------

def test_udp_count_64_forces_checkbox_true():
    """The reported srv06 bug: dialog persists count=64 but the
    increment checkbox may be False. Force it True so scapy + DPDK
    both cycle."""
    sd = {"protocol_data": {"udp": {
        "udp_source_port_count": "64",
        "udp_increment_source_port": False,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["udp"]["udp_increment_source_port"] is True


def test_udp_count_1_forces_checkbox_false():
    """Inverse: count=1 with checkbox=True is a no-op cycle. Normalize
    to checkbox=False so DPDK + scapy agree the stream doesn't cycle."""
    sd = {"protocol_data": {"udp": {
        "udp_destination_port_count": "1",
        "udp_increment_destination_port": True,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["udp"]["udp_increment_destination_port"] is False


def test_udp_dst_both_directions():
    sd = {"protocol_data": {"udp": {
        "udp_source_port_count": "64",
        "udp_increment_source_port": False,
        "udp_destination_port_count": "32",
        "udp_increment_destination_port": False,
    }}}
    _normalize(sd)
    udp = sd["protocol_data"]["udp"]
    assert udp["udp_increment_source_port"] is True
    assert udp["udp_increment_destination_port"] is True


# ----- TCP --------------------------------------------------------------

def test_tcp_src_count_promotes_checkbox():
    sd = {"protocol_data": {"tcp": {
        "tcp_source_port_count": "16",
        "tcp_increment_source_port": False,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["tcp"]["tcp_increment_source_port"] is True


def test_tcp_dst_count_one_demotes_checkbox():
    sd = {"protocol_data": {"tcp": {
        "tcp_destination_port_count": "1",
        "tcp_increment_destination_port": True,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["tcp"]["tcp_increment_destination_port"] is False


# ----- VLAN -------------------------------------------------------------

def test_vlan_count_promotes_checkbox():
    sd = {"protocol_data": {"vlan": {
        "vlan_increment_count": "8",
        "vlan_increment": False,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["vlan"]["vlan_increment"] is True


def test_vlan_count_one_demotes_checkbox():
    sd = {"protocol_data": {"vlan": {
        "vlan_increment_count": "1",
        "vlan_increment": True,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["vlan"]["vlan_increment"] is False


# ----- IPv4/v6/MAC: combo with Fixed/Increment/Decrement ----------------

def test_ipv4_src_count_promotes_fixed_to_increment():
    """When count >= 2 but mode is Fixed, that's a silent contradiction.
    Promote Fixed → Increment."""
    sd = {"protocol_data": {"ipv4": {
        "ipv4_source_increment_count": "16",
        "ipv4_source_mode": "Fixed",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["ipv4"]["ipv4_source_mode"] == "Increment"


def test_ipv4_dst_count_promotes_fixed_to_increment():
    sd = {"protocol_data": {"ipv4": {
        "ipv4_destination_increment_count": "4",
        "ipv4_destination_mode": "Fixed",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["ipv4"]["ipv4_destination_mode"] == "Increment"


def test_ipv4_decrement_mode_preserved():
    """Don't override an explicit Decrement choice with count >= 2.
    User chose Decrement — they meant it."""
    sd = {"protocol_data": {"ipv4": {
        "ipv4_source_increment_count": "16",
        "ipv4_source_mode": "Decrement",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["ipv4"]["ipv4_source_mode"] == "Decrement"


def test_ipv4_increment_mode_with_count_1_preserved():
    """count=1 with mode=Increment is a no-op cycle, not a bug.
    Leave mode alone — only the count drives engine behavior."""
    sd = {"protocol_data": {"ipv4": {
        "ipv4_source_increment_count": "1",
        "ipv4_source_mode": "Increment",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["ipv4"]["ipv4_source_mode"] == "Increment"


def test_ipv6_combo_promoted():
    sd = {"protocol_data": {"ipv6": {
        "ipv6_destination_increment_count": "32",
        "ipv6_destination_mode": "Fixed",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["ipv6"]["ipv6_destination_mode"] == "Increment"


def test_mac_combo_promoted():
    sd = {"protocol_data": {"mac": {
        "mac_destination_count": "256",
        "mac_destination_mode": "Fixed",
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["mac"]["mac_destination_mode"] == "Increment"


# ----- Defensive --------------------------------------------------------

def test_missing_protocol_data_safe():
    """Defensive: stream config without protocol_data key shouldn't
    raise. Common for fresh streams in test scaffolding."""
    sd = {}
    _normalize(sd)
    assert sd == {}


def test_missing_proto_section_safe():
    """udp/tcp/vlan section absent → no-op."""
    sd = {"protocol_data": {"ipv4": {"ipv4_source": "10.0.0.1"}}}
    _normalize(sd)
    assert sd["protocol_data"] == {"ipv4": {"ipv4_source": "10.0.0.1"}}


def test_garbage_count_coerced_to_one():
    """Non-numeric count = treat as 1 = cycling off."""
    sd = {"protocol_data": {"udp": {
        "udp_source_port_count": "bogus",
        "udp_increment_source_port": True,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["udp"]["udp_increment_source_port"] is False


def test_empty_string_count_coerced_to_one():
    sd = {"protocol_data": {"udp": {
        "udp_source_port_count": "",
        "udp_increment_source_port": True,
    }}}
    _normalize(sd)
    assert sd["protocol_data"]["udp"]["udp_increment_source_port"] is False


# ----- Real dialog round-trip ------------------------------------------

def test_full_dialog_roundtrip_normalizes(qapp):
    """End-to-end: open the real dialog, set count=64 on every count
    widget WITHOUT touching the increment checkboxes, save. Verify
    that the persisted data has the corresponding checkbox=True for
    every binary-gated proto."""
    from widgets.stream_dialog import AddStreamDialog
    d = AddStreamDialog(parent=None, interface="eth0")
    d.l4_tcp.setChecked(True)
    d.l4_udp.setChecked(True)

    # IMPORTANT: do NOT check the increment_*_checkbox boxes. The
    # widget will then be disabled, so we set values programmatically.
    for w in ("udp_source_increment_count", "udp_destination_increment_count",
              "tcp_source_increment_count", "tcp_destination_increment_count",
              "vlan_increment_count"):
        if hasattr(d, w):
            getattr(d, w).setText("64")

    out = d.get_stream_details()
    pd = out["protocol_data"]
    assert pd["udp"]["udp_increment_source_port"] is True
    assert pd["udp"]["udp_increment_destination_port"] is True
    assert pd["tcp"]["tcp_increment_source_port"] is True
    assert pd["tcp"]["tcp_increment_destination_port"] is True
    assert pd["vlan"]["vlan_increment"] is True


# ----- qapp fixture (mirrors other dialog tests) ----------------------

import os
import pytest

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
