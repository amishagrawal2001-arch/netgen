"""Ready-made traffic-stream templates for one-click profile creation.

Each template produces a `stream_data` dict in the same shape the
AddStreamDialog already consumes via `populate_stream_fields()` and
the `stream_data=` constructor kwarg. Applying a template is a single
call — no widget-by-widget juggling, no per-tab logic.

Adding a new template
---------------------
1. Append a `_StreamTemplate` to `_TEMPLATES`.
2. The dialog picks it up automatically.

Field names follow the same shape that lands in session.json under
`streams[port][i]`, so a template is essentially "a saved stream you
can recall by name".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# Common building blocks — reused across templates so changing a
# default MAC propagates everywhere.
#
# v0.3.11 cross-module pin: src/dst MAC defaults MUST match the
# Blast a DPDK Flow dialog's DEFAULT_SRC_MAC / DEFAULT_DST_MAC
# (see widgets/dpdk_blast_flow_dialog.py:DEFAULT_SRC_MAC). Both
# the dialog and these templates are operator-facing entry points
# for line-rate DPDK blasts; if they disagree on default MACs,
# packet captures from the two paths won't line up and the
# operator loses trust in the defaults. Pinned by
# tests/test_templates.py::test_udp_line_rate_1500b_matches_blast_flow_default.
_DEFAULT_SRC_MAC = "02:00:00:00:00:01"
_DEFAULT_DST_MAC = "02:00:00:00:00:02"
_DEFAULT_SRC_IP4 = "10.0.0.1"
_DEFAULT_DST_IP4 = "10.0.0.2"


def _udp_eth_ipv4(src_port: str = "1234", dst_port: str = "4791") -> Dict[str, Any]:
    return {
        "mac": {
            "mac_source_address": _DEFAULT_SRC_MAC,
            "mac_destination_address": _DEFAULT_DST_MAC,
        },
        "ipv4": {
            "ipv4_source": _DEFAULT_SRC_IP4,
            "ipv4_destination": _DEFAULT_DST_IP4,
        },
        "udp": {
            "udp_source_port": src_port,
            "udp_destination_port": dst_port,
        },
    }


@dataclass
class _StreamTemplate:
    key: str
    title: str
    summary: str
    stream_data: Dict[str, Any] = field(default_factory=dict)
    post_apply: Optional[Callable[[Any], None]] = None


# ---------------------------------------------------------------- registry


_TEMPLATES: List[_StreamTemplate] = [
    _StreamTemplate(
        key="udp_line_rate_64b",
        title="UDP line rate · 64 B (max-pps stress)",
        summary="Saturate the link with 64-byte UDP frames. DPDK on, "
                "timestamps on, no modifiers. The classic 'how fast "
                "can this NIC actually go in pps?' stream — needs "
                "148.8 Mpps for 100 G line rate, so single-core "
                "tx_worker (~46 Mpps) tops out around 23 Gbps. Bump "
                "dpdk_tx_cores to 4+ to push closer to line bps. "
                "For actually hitting wire-rate on the first try, "
                "use the 1500 B sibling template below.",
        stream_data={
            "name": "udp-line-rate-64",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 64,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 4,
            "enable_timestamps": True,
            "protocol_data": _udp_eth_ipv4(),
        },
    ),
    _StreamTemplate(
        key="udp_line_rate_1500b",
        title="UDP line rate · 1500 B (Ethernet MTU)",
        summary="Hit ACTUAL wire line rate on 10/25/100 G with one "
                "tx_worker core. At MTU, 100 Gbps needs only "
                "8.2 Mpps — well inside single-core tx_worker "
                "capacity (~46 Mpps). Use this for 'did line rate "
                "work?' demos and per-iface parallel blasts. Drop "
                "to the 64 B sibling above when you specifically "
                "want the max-pps stress test.",
        stream_data={
            "name": "udp-line-rate-1500",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 1500,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            # Single core is enough for 8.2 Mpps; leaves the other
            # cores free for parallel streams on sibling interfaces.
            "dpdk_tx_cores": 1,
            "enable_timestamps": True,
            "protocol_data": _udp_eth_ipv4(),
        },
    ),
    _StreamTemplate(
        key="udp_imix",
        title="UDP IMIX mix",
        summary="Three-frame IMIX (64 / 594 / 1518 B at 7:4:1 weighting). "
                "Realistic average frame distribution for WAN-style "
                "performance characterisation.",
        stream_data={
            "name": "udp-imix",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 594,             # avg-weighted single size as starter
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 4,
            "enable_timestamps": True,
            "protocol_data": _udp_eth_ipv4(),
            "_template_note": "Adjust frame_size or use modifiers for true IMIX",
        },
    ),
    _StreamTemplate(
        key="lag_hash_test",
        title="LAG / RSS / ECMP hash test",
        summary="Modifiers cycle src/dst IP and L4 ports across "
                "thousands of flows so the receiving side can prove "
                "5-tuple distribution.",
        stream_data={
            "name": "lag-hash-modifiers",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 512,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 4,
            "enable_timestamps": False,
            "protocol_data": _udp_eth_ipv4(),
            "modifiers": [
                {"field": "ipv4_source",       "type": "increment",
                 "start": _DEFAULT_SRC_IP4, "end": "10.0.0.250", "step": 1},
                {"field": "ipv4_destination",  "type": "increment",
                 "start": _DEFAULT_DST_IP4, "end": "10.0.1.250", "step": 1},
                {"field": "udp_source_port",   "type": "increment",
                 "start": "1024", "end": "65000", "step": 1},
            ],
        },
    ),
    _StreamTemplate(
        key="latency_probe",
        title="Latency probe (NLAT)",
        summary="Small frames with NLAT timestamps in the payload. "
                "Receiver computes one-way min / avg / p50 / p99 / max. "
                "Pair with a corresponding stream on the return path.",
        stream_data={
            "name": "latency-probe",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Packets/sec",
            "stream_rate_value": 1000,
            "dpdk_enable": True,
            "dpdk_tx_cores": 1,
            "enable_timestamps": True,
            "protocol_data": _udp_eth_ipv4(src_port="20001", dst_port="20002"),
        },
    ),
    _StreamTemplate(
        key="vxlan_encap",
        title="VXLAN-encapsulated UDP",
        summary="Inner Ethernet+IP+UDP wrapped in outer UDP/4789 with "
                "a VNI. Sane defaults for VTEP under-test scenarios.",
        stream_data={
            "name": "vxlan-encap",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 1500,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": False,        # scapy path; DPDK VXLAN is roadmap
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv4": {
                    "ipv4_source": "192.168.250.1",
                    "ipv4_destination": "192.168.250.2",
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4789",   # VXLAN
                },
                "vxlan": {
                    "vni": "10000",
                    "inner_src_mac": _DEFAULT_SRC_MAC,
                    "inner_dst_mac": _DEFAULT_DST_MAC,
                    "inner_src_ip":  _DEFAULT_SRC_IP4,
                    "inner_dst_ip":  _DEFAULT_DST_IP4,
                },
            },
        },
    ),
    _StreamTemplate(
        key="icmp_echo",
        title="ICMP echo (ping flood)",
        summary="Scapy-path ICMP echo-request flood. Useful for "
                "validating ARP/ND, basic IP forwarding, and "
                "rate-limiter behaviour on the DUT.",
        stream_data={
            "name": "icmp-flood",
            "enabled": True,
            "L3": "IPv4",
            "L4": "ICMP",
            "frame_size": 64,
            "stream_rate_type": "Packets/sec",
            "stream_rate_value": 100,
            "dpdk_enable": False,            # scapy only for ICMP today
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv4": {
                    "ipv4_source": _DEFAULT_SRC_IP4,
                    "ipv4_destination": _DEFAULT_DST_IP4,
                },
            },
        },
    ),
    _StreamTemplate(
        key="vlan_tagged_udp",
        title="VLAN-tagged UDP",
        summary="802.1Q tag (VLAN 100) over IPv4/UDP. Mirrors what "
                "the Devices tab generates for per-device emulated "
                "routers — useful for VLAN forwarding tests.",
        stream_data={
            "name": "vlan100-udp",
            "enabled": True,
            "L2": "VLAN",
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "vlan": {"vlan_id": "100", "vlan_priority": "0"},
                "ipv4": {
                    "ipv4_source": _DEFAULT_SRC_IP4,
                    "ipv4_destination": _DEFAULT_DST_IP4,
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),

    # ─────────────────────────────── scaling / sweep templates
    # v0.3.11: parametric streams that cycle through N values of one
    # or more header fields. Common in service-provider + DC stress
    # testing — MAC table fill, NAT pool exercise, RSS bucket spread,
    # routing-table fan-out, VLAN trunk scaling. All use the dialog's
    # existing mode/count/step fields (per protocol section), so the
    # operator can edit start address / count after picking.

    _StreamTemplate(
        key="mac_dst_sweep_1k",
        title="Scale · MAC dst sweep · 1024 unique dst MACs",
        summary="Cycle dst MAC across 1024 addresses starting at "
                "02:00:00:00:00:00, step 1. Saturates a switch's MAC "
                "table and exercises MAC-learning paths. Default frame "
                "size 128 B at line rate — bump count to 16k/64k for "
                "larger tables; bump step to spread the hash buckets.",
        stream_data={
            "name": "scale-mac-dst-1k",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_mode": "Fixed",
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_mode": "Increment",
                    "mac_destination_address": "02:00:00:00:00:00",
                    "mac_destination_count": "1024",
                    "mac_destination_step": "1",
                },
                "ipv4": {
                    "ipv4_source": _DEFAULT_SRC_IP4,
                    "ipv4_destination": _DEFAULT_DST_IP4,
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),

    _StreamTemplate(
        key="ipv4_dst_sweep_256",
        title="Scale · IPv4 dst sweep · /24 (256 hosts)",
        summary="Cycle dst IPv4 across a /24 starting at 10.0.0.0, "
                "step 1. Tests routing-table fan-out, ECMP dst-hash "
                "spread, ARP resolution at scale. Bump count to 65536 "
                "(/16) or 1048576 (/12) for larger tables. Source "
                "stays fixed so the receiver can verify each /32 hit.",
        stream_data={
            "name": "scale-ipv4-dst-24",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv4": {
                    "ipv4_source": _DEFAULT_SRC_IP4,
                    "ipv4_source_mode": "Fixed",
                    "ipv4_destination": "10.0.0.0",
                    "ipv4_destination_mode": "Increment",
                    "ipv4_destination_increment_step": "1",
                    "ipv4_destination_increment_count": "256",
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),

    _StreamTemplate(
        key="ipv4_src_sweep_256",
        title="Scale · IPv4 src sweep · NAT/pool (256 srcs)",
        summary="Cycle src IPv4 across 256 addresses starting at "
                "10.10.0.0, step 1. Exercises NAT pool allocation, "
                "stateful firewall connection table, ECMP src-hash "
                "spread, and load-balancer source-affinity. Bump "
                "count for larger NAT pools.",
        stream_data={
            "name": "scale-ipv4-src-256",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv4": {
                    "ipv4_source": "10.10.0.0",
                    "ipv4_source_mode": "Increment",
                    "ipv4_source_increment_step": "1",
                    "ipv4_source_increment_count": "256",
                    "ipv4_destination": _DEFAULT_DST_IP4,
                    "ipv4_destination_mode": "Fixed",
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),

    _StreamTemplate(
        key="ipv6_dst_sweep_64",
        title="Scale · IPv6 dst sweep · 64 hosts",
        summary="Cycle dst IPv6 across 64 addresses starting at "
                "2001:db8::1, step 1. IPv6 analogue of the IPv4 "
                "dst sweep — tests v6 routing table, ND resolution "
                "at scale, ECMP v6 hash distribution. Smaller "
                "default count (64) because v6 forwarding tables "
                "are typically smaller than v4 today; bump as needed.",
        stream_data={
            "name": "scale-ipv6-dst-64",
            "enabled": True,
            "L3": "IPv6",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv6": {
                    "ipv6_source": "2001:db8:1::1",
                    "ipv6_source_mode": "Fixed",
                    "ipv6_destination": "2001:db8::1",
                    "ipv6_destination_mode": "Increment",
                    "ipv6_destination_increment_step": "1",
                    "ipv6_destination_increment_count": "64",
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),

    _StreamTemplate(
        key="five_tuple_sweep_rss",
        title="Scale · 5-tuple sweep · RSS bucket spread",
        summary="Vary src IP, dst IP, src port AND dst port together "
                "(256 each) so the receiver's RSS hash distributes "
                "the flow across all queues. Stress-tests multi-queue "
                "NIC capacity, per-CPU softirq balance, and steering "
                "table entries. Increase counts to push into RSS "
                "indirection-table replacement.",
        stream_data={
            "name": "scale-5tuple-rss",
            "enabled": True,
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 4,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "ipv4": {
                    "ipv4_source": "10.10.0.0",
                    "ipv4_source_mode": "Increment",
                    "ipv4_source_increment_step": "1",
                    "ipv4_source_increment_count": "256",
                    "ipv4_destination": "10.20.0.0",
                    "ipv4_destination_mode": "Increment",
                    "ipv4_destination_increment_step": "1",
                    "ipv4_destination_increment_count": "256",
                },
                "udp": {
                    "udp_source_port": "1024",
                    "udp_increment_source_port": True,
                    "udp_source_port_step": "1",
                    "udp_source_port_count": "256",
                    "udp_destination_port": "10000",
                    "udp_increment_destination_port": True,
                    "udp_destination_port_step": "1",
                    "udp_destination_port_count": "256",
                },
            },
        },
    ),

    _StreamTemplate(
        key="vlan_id_sweep_4k",
        title="Scale · VLAN ID sweep · 4094 tags",
        summary="Cycle VLAN ID across the full 802.1Q range (1..4094) "
                "from one TX port. Tests trunk VLAN-table size, "
                "per-VLAN hash distribution, and L2 forwarding-engine "
                "VLAN scaling. Underlying frame is plain IPv4/UDP — "
                "the receiver sees one frame per VLAN per cycle.",
        stream_data={
            "name": "scale-vlan-4k",
            "enabled": True,
            "L2": "VLAN",
            "L3": "IPv4",
            "L4": "UDP",
            "frame_size": 128,
            "stream_rate_type": "Line Rate",
            "dpdk_enable": True,
            "dpdk_tx_cores": 2,
            "enable_timestamps": False,
            "protocol_data": {
                "mac": {
                    "mac_source_address": _DEFAULT_SRC_MAC,
                    "mac_destination_address": _DEFAULT_DST_MAC,
                },
                "vlan": {
                    "vlan_id": "1",
                    "vlan_priority": "0",
                    "vlan_increment": True,
                    "vlan_increment_value": "1",
                    "vlan_increment_count": "4094",
                },
                "ipv4": {
                    "ipv4_source": _DEFAULT_SRC_IP4,
                    "ipv4_destination": _DEFAULT_DST_IP4,
                },
                "udp": {
                    "udp_source_port": "1234",
                    "udp_destination_port": "4791",
                },
            },
        },
    ),
]


# ---------------------------------------------------------------- public API


def list_templates() -> List[Dict[str, str]]:
    return [
        {"key": t.key, "title": t.title, "summary": t.summary}
        for t in _TEMPLATES
    ]


def get_template(key: str) -> Optional[_StreamTemplate]:
    for t in _TEMPLATES:
        if t.key == key:
            return t
    return None


def get_stream_data(key: str) -> Optional[Dict[str, Any]]:
    """Return a deep-copied stream_data dict ready to hand to the
    dialog. Deep-copy so the operator's edits don't mutate the
    template registry (which would silently change the template for
    everyone else who picks it later in the same session).
    """
    import copy
    t = get_template(key)
    if t is None:
        return None
    return copy.deepcopy(t.stream_data)
