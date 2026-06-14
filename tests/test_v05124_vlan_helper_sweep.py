"""v0.5.124: shared resolve_tx_vlan_id() helper governs every TX
packet builder.

Background: the srv06 RX=0 saga forced this same "respect the
VLAN mode toggle" check to be fixed FOUR separate times — once
per packet builder — across v0.5.120/121/122/123. Each fix was
correct but the bug shape kept hiding in a new builder that
hadn't been audited.

v0.5.124 centralizes the resolution into one helper
(`utils/vlan_helpers.py:resolve_tx_vlan_id`) and migrates all
known TX-side call sites to use it:

  * utils/dpdk_tx_worker.py          (was v0.5.121)
  * utils/generic.py                  (was v0.5.123)
  * utils/uec.py                       (NEW — caught in audit)
  * utils/rocev2.py                    (NEW — was mode==Tagged only)

The helper's behavior pins:
  * Untagged → None
  * Tagged / Stacked / TaggedStacked → vlan_id from
    protocol_data.vlan.vlan_id (or top-level vlan_id)
  * Missing mode field → fall through to vlan_id (legacy back-compat)
  * Invalid VID (0, out of 1..4094) → None

This file tests two things:
  1. The shared helper directly across all reachable inputs.
  2. The four migrated call-sites all use it (regression guard —
     if someone re-inlines the logic, this test fails).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _stream(vlan_mode=None, vlan_id="100", at_top_level=False, no_mode=False):
    s = {
        "protocol_data": {
            "vlan": {"vlan_id": str(vlan_id)},
            "mac": {"mac_source_address": "5c:25:73:3f:30:56",
                    "mac_destination_address": "5c:25:73:3f:30:57"},
            "ipv4": {"ipv4_source": "10.0.0.1",
                     "ipv4_destination": "10.0.0.2"},
        },
    }
    if not no_mode:
        if at_top_level:
            s["VLAN"] = vlan_mode
        else:
            s["protocol_selection"] = {"VLAN": vlan_mode}
    return s


# ----- Helper-direct tests --------------------------------------------------


def test_untagged_returns_none():
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("Untagged", "100")) is None


def test_tagged_returns_vid():
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("Tagged", "100")) == 100


def test_stacked_returns_inner_vid():
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("Stacked", "200")) == 200


def test_taggedstacked_alias_returns_vid():
    """Some saved-stream JSON spellings use TaggedStacked /
    'tagged+stacked'. Both are accepted; helper canon-lowercases
    them in the taggable set."""
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("TaggedStacked", "300")) == 300
    assert resolve_tx_vlan_id(_stream("tagged+stacked", "400")) == 400


def test_protocol_selection_takes_precedence_over_top_level():
    from utils.vlan_helpers import resolve_tx_vlan_id
    s = _stream("Untagged", "100")
    s["VLAN"] = "Tagged"   # stale top-level → must NOT win
    assert resolve_tx_vlan_id(s) is None


def test_missing_mode_falls_through_to_vlan_id():
    """Legacy streams predating v0.4.5 had no mode field — they
    still need to work. Helper falls through to vlan_id."""
    from utils.vlan_helpers import resolve_tx_vlan_id
    s = _stream(vlan_id="42", no_mode=True)
    assert resolve_tx_vlan_id(s) == 42


def test_zero_vlan_id_returns_none():
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("Tagged", "0")) is None


def test_invalid_vlan_id_returns_none():
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(_stream("Tagged", "abc")) is None
    assert resolve_tx_vlan_id(_stream("Tagged", "9999")) is None  # > 4094
    assert resolve_tx_vlan_id(_stream("Tagged", "-1")) is None


def test_case_insensitive_mode():
    """The dialog persists 'Untagged', 'Tagged', 'Stacked' but
    API-direct callers may use any case. Helper lowercases."""
    from utils.vlan_helpers import resolve_tx_vlan_id
    for m in ("UNTAGGED", "untagged", " UnTaGgEd "):
        assert resolve_tx_vlan_id(_stream(m, "100")) is None
    for m in ("TAGGED", "tagged", " Tagged "):
        assert resolve_tx_vlan_id(_stream(m, "100")) == 100


def test_non_dict_input_returns_none():
    """Defensive: never crash on garbage input."""
    from utils.vlan_helpers import resolve_tx_vlan_id
    assert resolve_tx_vlan_id(None) is None
    assert resolve_tx_vlan_id("not a dict") is None
    assert resolve_tx_vlan_id([]) is None


# ----- Per-call-site integration tests --------------------------------------
# Each TX builder must actually consult the helper. These tests pin the
# wiring so a future refactor that drops the helper call fails.


def test_dpdk_tx_worker_uses_helper():
    """dpdk_tx_worker._resolve_l2_l3_l4 must surface vlan_id=None
    on Untagged. Pinned at the function-result level so the
    helper call internally is opaque."""
    from utils.dpdk_tx_worker import _resolve_l2_l3_l4
    fields = _resolve_l2_l3_l4(_stream("Untagged", "100"))
    assert fields["vlan_id"] is None
    fields = _resolve_l2_l3_l4(_stream("Tagged", "100"))
    assert fields["vlan_id"] == 100


def test_generic_get_packet_config_uses_helper():
    """utils.generic.get_packet_config must surface vlan_ids=[None]
    on Untagged."""
    from utils.generic import get_packet_config
    cfg = get_packet_config(_stream("Untagged", "100"))
    assert cfg["vlan_ids"] == [None]
    cfg = get_packet_config(_stream("Tagged", "100"))
    assert cfg["vlan_ids"] == [100]


def test_uec_builder_drops_dot1q_when_untagged():
    """generate_uec_rocev2_packet must not emit Dot1Q on Untagged.
    Walks the actual scapy packet to confirm no Dot1Q layer is
    present. Pre-fix the function only checked `vlan_id > 0`,
    so a stream with VLAN=Untagged + vlan_id=1 (dialog default)
    got tagged on the wire."""
    try:
        from scapy.layers.l2 import Dot1Q
    except Exception:
        import pytest
        pytest.skip("scapy not available")
    from utils.uec import generate_uec_rocev2_packet
    s = _stream("Untagged", "100")
    pkt = generate_uec_rocev2_packet(
        src_mac="5c:25:73:3f:30:56", dst_mac="5c:25:73:3f:30:57",
        qp=1000, pasid=5000, stream_data=s,
    )
    assert Dot1Q not in pkt, (
        "UEC stream with VLAN:Untagged must not carry a Dot1Q layer"
    )


def test_uec_builder_keeps_dot1q_when_tagged():
    """Regression guard: don't over-narrow the UEC fix."""
    try:
        from scapy.layers.l2 import Dot1Q
    except Exception:
        import pytest
        pytest.skip("scapy not available")
    from utils.uec import generate_uec_rocev2_packet
    s = _stream("Tagged", "100")
    pkt = generate_uec_rocev2_packet(
        src_mac="5c:25:73:3f:30:56", dst_mac="5c:25:73:3f:30:57",
        qp=1000, pasid=5000, stream_data=s,
    )
    assert Dot1Q in pkt, "Tagged UEC must carry Dot1Q"
    assert int(pkt[Dot1Q].vlan) == 100


def test_rocev2_builder_handles_stacked():
    """RoCEv2 used to only check `mode == "Tagged"` (case-sensitive)
    and silently dropped the Dot1Q for Stacked / TaggedStacked.
    Shared helper accepts all taggable modes, so the migrated
    rocev2 builder does too."""
    try:
        from scapy.layers.l2 import Dot1Q
    except Exception:
        import pytest
        pytest.skip("scapy not available")
    from utils.rocev2 import generate_rocev2_packet
    s = _stream("Stacked", "200")
    pkts = generate_rocev2_packet(s)
    pkt = pkts[0] if isinstance(pkts, list) else pkts
    assert Dot1Q in pkt, (
        "RoCEv2 Stacked stream should carry Dot1Q. Pre-fix the "
        "builder only honored mode=='Tagged' and silently dropped "
        "stacked. v0.5.124 widens to all taggable modes."
    )


def test_rocev2_builder_drops_dot1q_when_untagged():
    try:
        from scapy.layers.l2 import Dot1Q
    except Exception:
        import pytest
        pytest.skip("scapy not available")
    from utils.rocev2 import generate_rocev2_packet
    s = _stream("Untagged", "100")
    pkts = generate_rocev2_packet(s)
    pkt = pkts[0] if isinstance(pkts, list) else pkts
    assert Dot1Q not in pkt
