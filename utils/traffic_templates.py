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
_DEFAULT_SRC_MAC = "aa:bb:cc:dd:ee:01"
_DEFAULT_DST_MAC = "aa:bb:cc:dd:ee:02"
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
        title="UDP line rate · 64 B",
        summary="Saturate the link with 64-byte UDP frames. DPDK on, "
                "timestamps on, no modifiers. The classic 'how fast "
                "can this NIC actually go?' stream.",
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
