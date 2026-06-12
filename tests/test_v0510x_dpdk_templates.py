"""v0.5.106: DPDK templates wire in the v0.5.105 rx_engine field
so picking a DPDK template auto-spawns rx_worker on start.

Two existing DPDK templates updated:
  - udp_line_rate_64b
  - udp_line_rate_1500b
Both now set rx_engine="dpdk" so the auto-lifecycle helper spawns
rx_worker matching the stream's filter when the operator hits Start.

Two new dedicated templates:
  - dpdk_blast_e2e — line-rate TX + line-rate RX (the natural
    pairing of v0.5.105's tx_worker + rx_worker)
  - dpdk_loopback_check — 100K pps throttled, for safely
    validating that the TX→RX wire path actually delivers
    DPDK-generated frames (srv06 saga debug helper)

These tests pin the contract — a future template refactor can't
silently drop rx_engine and quietly re-introduce the srv06 RX=0
behavior under what looks like a "use a template" workflow.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-dpdk-tmpl.db")

from utils.traffic_templates import list_templates, get_template


# ─── Updated existing templates ───


@pytest.mark.parametrize("key", ["udp_line_rate_64b", "udp_line_rate_1500b"])
def test_existing_dpdk_template_wires_rx_engine(key):
    """Both legacy DPDK line-rate templates pre-date v0.5.105.
    Without rx_engine, picking them defaults to Scapy RX which
    drops at high pps — the srv06 saga. Pin the fix."""
    t = get_template(key)
    assert t is not None, f"{key} template missing"
    assert t.stream_data.get("dpdk_enable") is True, (
        f"{key} should still enable DPDK TX"
    )
    assert t.stream_data.get("rx_engine") == "dpdk", (
        f"{key} must set rx_engine='dpdk' so the auto-lifecycle "
        f"spawns rx_worker on start. Without this, the kernel "
        f"netdev overflow at line rate makes rx_count look stuck "
        f"at 0 (srv06 bite)."
    )


# ─── New dedicated DPDK end-to-end template ───


def test_dpdk_blast_e2e_template_exists():
    """The new e2e template should be discoverable in the dropdown."""
    keys = [t["key"] for t in list_templates()]
    assert "dpdk_blast_e2e" in keys


def test_dpdk_blast_e2e_template_has_both_engines():
    t = get_template("dpdk_blast_e2e")
    assert t is not None
    sd = t.stream_data
    assert sd.get("dpdk_enable") is True
    assert sd.get("rx_engine") == "dpdk"
    # Frame size 128 documented as the pps/bps balance point
    assert sd.get("frame_size") == 128
    # Line rate so it actually exercises the new path
    assert sd.get("stream_rate_type") == "Line Rate"
    # tx_cores >= 4 since 128 B at line rate needs multi-queue
    assert sd.get("dpdk_tx_cores", 1) >= 4


def test_dpdk_blast_e2e_uses_standard_test_mac_and_ips():
    """Consistency with udp_line_rate_*: same MAC + IP defaults
    so operators can swap templates without re-deriving filters."""
    t = get_template("dpdk_blast_e2e")
    proto = t.stream_data["protocol_data"]
    assert proto["mac"]["mac_source_address"] == "02:00:00:00:00:01"
    assert proto["mac"]["mac_destination_address"] == "02:00:00:00:00:02"
    assert proto["ipv4"]["ipv4_source"] == "10.0.0.1"
    assert proto["ipv4"]["ipv4_destination"] == "10.0.0.2"


# ─── New safe-rate loopback validation template ───


def test_dpdk_loopback_check_template_exists():
    keys = [t["key"] for t in list_templates()]
    assert "dpdk_loopback_check" in keys


def test_dpdk_loopback_check_throttles_below_switch_storm_cap():
    """The whole point of this template is to dodge the switch
    storm-control cap that bit srv06. 100K pps is well below
    typical 1-10 Mpps thresholds."""
    t = get_template("dpdk_loopback_check")
    sd = t.stream_data
    assert sd.get("stream_rate_type") == "Custom PPS"
    pps = sd.get("stream_pps_rate")
    assert pps is not None
    assert 1_000 <= pps <= 1_000_000, (
        f"loopback check rate {pps} should be a safe sub-storm-control "
        f"value (1K..1M pps); a higher rate defeats the point"
    )


def test_dpdk_loopback_check_uses_both_dpdk_engines():
    """Same e2e path as the blast template, just throttled."""
    t = get_template("dpdk_loopback_check")
    sd = t.stream_data
    assert sd.get("dpdk_enable") is True
    assert sd.get("rx_engine") == "dpdk"


def test_dpdk_loopback_check_summary_mentions_srv06_use_case():
    """The summary must guide the operator at WHY they'd pick this
    over the blast template — a future maintainer who deletes the
    diagnostic intent leaves the template with no clear niche."""
    t = get_template("dpdk_loopback_check")
    summary = t.summary.lower()
    assert "switch" in summary
    # Should mention either "storm" / "rate-limit" / "100" so the
    # operator understands why it's throttled
    assert any(s in summary for s in
               ("storm", "rate-limit", "rate limit", "100"))


# ─── Cross-template invariants ───


def test_all_dpdk_templates_pair_engines():
    """If a template enables DPDK TX, it should also enable DPDK
    RX — otherwise the operator gets line-rate TX + scapy RX,
    which is exactly the srv06 mismatch we want to prevent."""
    for entry in list_templates():
        t = get_template(entry["key"])
        if t.stream_data.get("dpdk_enable"):
            assert t.stream_data.get("rx_engine") == "dpdk", (
                f"Template {entry['key']!r} enables DPDK TX but "
                f"not DPDK RX — operator will hit kernel netdev "
                f"overflow at high pps. Set rx_engine='dpdk'."
            )


def test_new_templates_discoverable_via_list_templates():
    """Belt-and-suspenders: the dialog walks list_templates() to
    populate the dropdown. New entries must appear in it."""
    keys = {t["key"] for t in list_templates()}
    for needed in ("dpdk_blast_e2e", "dpdk_loopback_check"):
        assert needed in keys, (
            f"{needed} not visible via list_templates() — dropdown "
            f"won't show it"
        )
