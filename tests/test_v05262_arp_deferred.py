"""v0.5.262 — ARP deferred fixes (transactionality, IPv6 diag, NDP,
event log dedup, batch endpoint VRF)."""

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
ARP = (REPO / "utils" / "arp.py").read_text()
MON = (REPO / "utils" / "arp_monitor.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- ARP-4 + ARP-7: per-device write lock ---------------------------


def test_per_device_write_lock_defined():
    assert "audit ARP-4 + ARP-7" in MON
    assert "_ARP_WRITE_LOCKS: Dict[str, threading.Lock]" in MON
    assert "_ARP_WRITE_LOCKS_META_LOCK = threading.Lock()" in MON


def test_update_device_arp_status_acquires_per_device_lock():
    idx = MON.find("def _update_device_arp_status(self, device_id: str, arp_status")
    end = MON.find("\n    def ", idx + 1)
    body = MON[idx:end if end > 0 else idx + 5000]
    assert "_arp_write_lock_for(device_id)" in body
    assert "with _lock:" in body


def test_arp_write_lock_helper_uses_meta_lock():
    idx = MON.find("def _arp_write_lock_for(device_id: str)")
    body = MON[idx:idx + 500]
    assert "with _ARP_WRITE_LOCKS_META_LOCK:" in body
    assert "return _ARP_WRITE_LOCKS[device_id]" in body


# --- ARP-10: log_device_event dedup by transition ------------------


def test_last_arp_status_logged_map_defined():
    assert "audit ARP-10" in MON
    assert "_LAST_ARP_STATUS_LOGGED: Dict[str, str] = {}" in MON


def test_log_device_event_gated_on_transition():
    idx = MON.find("def _update_device_arp_status(self, device_id: str, arp_status")
    end = MON.find("\n    def ", idx + 1)
    body = MON[idx:end if end > 0 else idx + 5000]
    assert "_prev_logged != current_status" in body
    assert "_should_log = _prev_logged != current_status" in body
    # log_device_event only fires when _should_log
    assert "if _should_log:" in body
    # transition_from included in the payload for auditability
    assert "'transition_from': _prev_logged" in body


# --- ARP-8: IPv6 diag uses `ip vrf exec` uniformly -----------------


def test_ipv6_diag_uses_vrf_exec_not_netlink_filter():
    idx = SERVER.find("audit ARP-8")
    assert idx > 0
    body = SERVER[idx:idx + 2000]
    # Built via `list(ping_prefix) + ["ip", "-6", "neigh", "show", "to", ipv6_target]`
    assert 'list(ping_prefix) + [' in body
    assert '"ip", "-6", "neigh", "show", "to", ipv6_target' in body


def test_old_vrf_netlink_filter_gone():
    """The pre-fix `ip -6 neigh show ... vrf <name> <target>` netlink
    filter is gone from live code."""
    idx = SERVER.find("audit ARP-8")
    body = SERVER[idx:idx + 2000]
    live_old = [
        line for line in body.splitlines()
        if 'neigh_cmd += ["vrf", ping_prefix[3]]' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old netlink-filter form still live: {live_old!r}"


# --- ARP-9: NDP builder --------------------------------------------


def test_ndp_builder_defined():
    assert "audit ARP-9" in ARP
    assert "def generate_ndp_packet(stream_data):" in ARP
    # Helpers for RFC 4291 §2.7.1 (mcast address) and RFC 2464 §7 (mac).
    assert "def _ipv6_solicited_node_multicast(addr: str)" in ARP
    assert "def _ipv6_solicited_node_multicast_mac(ipv6: str)" in ARP


def test_ndp_ns_hits_solicited_node_multicast_and_mac():
    sys.path.insert(0, str(REPO))
    try:
        from utils.arp import (
            generate_ndp_packet,
            _ipv6_solicited_node_multicast,
            _ipv6_solicited_node_multicast_mac,
        )
        # Target 2001:db8::2 → last 24 bits = 0x000002.
        assert _ipv6_solicited_node_multicast("2001:db8::2") == "ff02::1:ff00:0002"
        assert _ipv6_solicited_node_multicast_mac("2001:db8::2") == "33:33:ff:00:00:02"
        sd = {
            "protocol_data": {
                "arp": {
                    "arp_operation": "Request",
                    "arp_sender_ip": "2001:db8::1",
                    "arp_target_ip": "2001:db8::2",
                    "arp_sender_mac": "aa:bb:cc:dd:ee:01",
                },
            },
        }
        pkt = generate_ndp_packet(sd)
        # EtherType 0x86dd for IPv6.
        assert pkt.type == 0x86dd
        # L2 dst is the solicited-node MAC.
        assert pkt.dst.lower() == "33:33:ff:00:00:02"
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


def test_ndp_na_is_unicast_to_requester():
    sys.path.insert(0, str(REPO))
    try:
        from utils.arp import generate_ndp_packet
        sd = {
            "protocol_data": {
                "arp": {
                    "arp_operation": "Reply",
                    "arp_sender_ip": "2001:db8::2",
                    "arp_target_ip": "2001:db8::1",
                    "arp_target_mac": "ff:ff:11:22:33:44",
                    "arp_sender_mac": "aa:bb:cc:dd:ee:02",
                },
            },
        }
        pkt = generate_ndp_packet(sd)
        assert pkt.type == 0x86dd
        # L2 dst is the explicit target MAC (unicast).
        assert pkt.dst.lower() == "ff:ff:11:22:33:44"
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


def test_ndp_ns_enforces_hlim_255():
    """RFC 4861 §7.1.1: NS/NA packets must have Hop Limit = 255 or
    a receiver will reject them as forwarded from off-link."""
    sys.path.insert(0, str(REPO))
    try:
        from utils.arp import generate_ndp_packet
        from scapy.layers.inet6 import IPv6
        sd = {"protocol_data": {"arp": {
            "arp_operation": "Request",
            "arp_sender_ip": "fe80::1",
            "arp_target_ip": "fe80::2",
        }}}
        pkt = generate_ndp_packet(sd)
        assert pkt[IPv6].hlim == 255
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


# --- Batch endpoint VRF -------------------------------------------


def test_batch_endpoint_accepts_device_id_by_ip_mapping():
    idx = SERVER.find("def check_arp_resolution_batch():")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 5000]
    assert "audit ARP batch VRF" in body
    assert 'device_id_by_ip = data.get("device_id_by_ip")' in body
    # Group by prefix + one fetch per unique VRF.
    assert "by_prefix" in body
    assert "_arp_vrf_prefix(dev_id)" in body


def test_batch_lookup_is_vrf_scoped():
    """The lookup dict key is (prefix, ip) so two VRFs with the
    same IP don't collide."""
    idx = SERVER.find("def check_arp_resolution_batch():")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 5000]
    assert "arp_entries[(prefix, _parts[0])]" in body
    assert "arp_entries.get((prefix, ip_address)" in body


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 262)
