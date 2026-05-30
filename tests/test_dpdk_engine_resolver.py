"""Pre-flight DPDK engine resolution + compatibility checks (v0.2.75).

Before this release, the DPDK fallback decision happened invisibly
inside the worker thread: an operator enabling DPDK on a TCP / IPv6 /
MPLS / QinQ stream watched it "run" at Scapy speed and had no clue
why. ``resolve_engine()`` runs the decision *up front* so the start
endpoint can return an explanation the GUI surfaces in a single
end-of-batch dialog.

Pure-function tests — no Qt, no scapy, no network.
"""

import pytest

from utils.dpdk_tx_worker import (
    dpdk_compatibility_check,
    resolve_engine,
    should_use_dpdk,
)


def _base_stream(**overrides):
    """Minimal compatible stream — DPDK + UDP + IPv4 + untagged."""
    s = {
        "dpdk_enable": True,
        "L3": "IPv4",
        "L4": "UDP",
    }
    s.update(overrides)
    return s


# ───────────────────────────────────────── should_use_dpdk (opt-in)
def test_should_use_dpdk_true_on_explicit_flag():
    assert should_use_dpdk({"dpdk_enable": True}) is True
    assert should_use_dpdk({"use_dpdk": True}) is True
    assert should_use_dpdk({"dpdk": True}) is True
    assert should_use_dpdk({"engine": "dpdk"}) is True


def test_should_use_dpdk_handles_string_truthy_values():
    assert should_use_dpdk({"dpdk_enable": "true"}) is True
    assert should_use_dpdk({"dpdk_enable": "yes"}) is True
    assert should_use_dpdk({"dpdk_enable": "1"}) is True


def test_should_use_dpdk_false_when_unset():
    assert should_use_dpdk({}) is False
    assert should_use_dpdk({"dpdk_enable": False}) is False


def test_should_use_dpdk_finds_flag_in_protocol_selection():
    """Some streams store the toggle inside protocol_selection
    (where the form dialog round-trips it)."""
    assert should_use_dpdk(
        {"protocol_selection": {"dpdk_enable": True}}
    ) is True


# ───────────────────────────────────── dpdk_compatibility_check
def test_compat_check_returns_none_for_udp_ipv4_untagged():
    assert dpdk_compatibility_check(_base_stream()) is None


def test_compat_check_rejects_tcp():
    reason = dpdk_compatibility_check(_base_stream(L4="TCP"))
    assert reason is not None
    assert "UDP-only" in reason
    assert "TCP" in reason


def test_compat_check_rejects_icmp():
    reason = dpdk_compatibility_check(_base_stream(L4="ICMP"))
    assert reason is not None
    assert "ICMP" in reason


def test_compat_check_treats_any_as_udp_compatible():
    """'Any' is the form's default and the tx_worker treats it as UDP —
    don't false-positive on that."""
    assert dpdk_compatibility_check(_base_stream(L4="Any")) is None
    assert dpdk_compatibility_check(_base_stream(L4="")) is None


def test_compat_check_rejects_ipv6():
    reason = dpdk_compatibility_check(_base_stream(L3="IPv6"))
    assert reason is not None
    assert "IPv4-only" in reason
    assert "IPv6" in reason


def test_compat_check_rejects_sr_mpls_label_stack_string():
    reason = dpdk_compatibility_check(_base_stream(mpls_labels="16001,16002"))
    assert reason is not None
    assert "MPLS" in reason


def test_compat_check_rejects_sr_mpls_label_stack_list():
    reason = dpdk_compatibility_check(_base_stream(mpls_labels=[16001, 16002]))
    assert reason is not None
    assert "MPLS" in reason


def test_compat_check_rejects_legacy_single_mpls_label():
    reason = dpdk_compatibility_check(_base_stream(mpls_label=100))
    assert reason is not None
    assert "MPLS" in reason


def test_compat_check_rejects_qinq_outer_vlan():
    reason = dpdk_compatibility_check(_base_stream(outer_vlan_id=200))
    assert reason is not None
    assert "QinQ" in reason or "single-VLAN" in reason


def test_compat_check_accepts_single_vlan():
    """Inner Dot1Q alone is fine — tx_worker builds single-tagged
    frames. Only the OUTER tag (QinQ) is rejected."""
    # No outer_vlan_id set → fine.
    assert dpdk_compatibility_check(_base_stream(vlan_id=100)) is None


def test_compat_check_ignores_outer_vlan_zero():
    """outer_vlan_id=0 means 'unset' in the form — not QinQ."""
    assert dpdk_compatibility_check(_base_stream(outer_vlan_id=0)) is None
    assert dpdk_compatibility_check(_base_stream(outer_vlan_id="0")) is None
    assert dpdk_compatibility_check(_base_stream(outer_vlan_id=None)) is None


def test_compat_check_reads_from_protocol_selection():
    """Form-roundtripped streams nest the L4 inside protocol_selection."""
    reason = dpdk_compatibility_check({
        "dpdk_enable": True,
        "protocol_selection": {"L3": "IPv4", "L4": "TCP"},
    })
    assert reason is not None and "TCP" in reason


# ───────────────────────────────────────────── resolve_engine
def test_resolve_engine_dpdk_off_returns_scapy_no_reason():
    """Operator didn't ask for DPDK → no fallback to report."""
    engine, reason = resolve_engine({"L4": "UDP"})
    assert engine == "scapy"
    assert reason is None


def test_resolve_engine_dpdk_on_and_compatible_returns_dpdk():
    engine, reason = resolve_engine(_base_stream())
    assert engine == "dpdk"
    assert reason is None


def test_resolve_engine_dpdk_on_but_incompatible_returns_scapy_with_reason():
    """The whole point: surface a reason the start endpoint can return
    to the GUI so the operator knows why DPDK didn't take."""
    engine, reason = resolve_engine(_base_stream(L4="TCP"))
    assert engine == "scapy"
    assert reason is not None
    assert "TCP" in reason


@pytest.mark.parametrize("override,expected_substring", [
    ({"L4": "TCP"},               "TCP"),
    ({"L4": "ICMP"},              "ICMP"),
    ({"L3": "IPv6"},              "IPv6"),
    ({"mpls_labels": "100,200"},  "MPLS"),
    ({"mpls_labels": [100, 200]}, "MPLS"),
    ({"mpls_label": 42},          "MPLS"),
    ({"outer_vlan_id": 200},      "QinQ"),
])
def test_resolve_engine_known_incompat_combos(override, expected_substring):
    engine, reason = resolve_engine(_base_stream(**override))
    assert engine == "scapy"
    assert reason is not None
    assert expected_substring in reason
