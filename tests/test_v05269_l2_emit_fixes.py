"""v0.5.269 — L2 emit correctness fixes (9 fixes across LACP /
VRRP / IGMP / PIM / BFD, plus client dialog defaults).

Source-level checks (no scapy runtime).
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
L2 = (REPO / "utils" / "l2_protocols.py").read_text()
DLG = (REPO / "widgets" / "l2_emulation_tab.py").read_text()


# --- iface auto-derive helpers ------------------------------------


def test_iface_mac_helper_defined():
    assert "def _iface_mac(iface: str) -> Optional[str]" in L2
    idx = L2.find("def _iface_mac")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # Uses netifaces (preferred) and /sys fallback.
    assert "import netifaces" in body
    assert "/sys/class/net/" in body
    # Returns None on failure — never raises.
    assert "return None" in body


def test_iface_primary_ipv4_helper_defined():
    assert "def _iface_primary_ipv4(iface: str) -> Optional[str]" in L2


def test_resolve_dst_mac_helper_defined():
    assert "def _resolve_dst_mac(iface: str, dst_ip: str" in L2
    idx = L2.find("def _resolve_dst_mac")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # Kernel ARP cache first, scapy fallback.
    assert '"ip", "neigh", "get"' in body
    assert "srp(" in body


# --- L2-C1: IGMPv1/v2 now carry IP Router Alert -------------------


def test_igmp_router_alert_shared_across_v1_v2_v3():
    """The `_ra = [IPOption_Router_Alert()]` list is defined once at
    the top of the IGMP `_factory` and used in every version
    branch's `options=` slot."""
    igmp_idx = L2.find("def start_igmp")
    end = L2.find("\ndef ", igmp_idx + 1)
    body = L2[igmp_idx:end]
    assert "_ra = [IPOption_Router_Alert()]" in body
    # Each of v1, v2, v3 IP layer builds must reference _ra.
    ip_lines_with_ra = [
        ln for ln in body.splitlines()
        if "IP(" in ln and "options=_ra" in ln
    ]
    assert len(ip_lines_with_ra) >= 3, (
        f"expected 3 IP() lines with options=_ra (v1, v2, v3); "
        f"found {len(ip_lines_with_ra)}"
    )


def test_igmp_no_more_options_empty():
    """The pre-fix `options=[]` on IGMPv3 preview is gone (replaced
    by _ra)."""
    preview_start = L2.find("def _igmp_preview")
    preview_end = L2.find("\ndef ", preview_start + 1)
    body = L2[preview_start:preview_end]
    # Live lines only, no comments.
    live = [ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("options=[]" in ln for ln in live)


# --- L2-C2/C3/C5: BFD auto-derive addressing ---------------------


def test_bfd_src_ip_default_blank_and_auto_derived():
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # Signature: blank default.
    assert 'src_ip: str = "",' in body
    # Auto-derive expression present.
    assert (
        "_iface_primary_ipv4(iface) or \"10.0.0.1\"" in body
        or '_iface_primary_ipv4(iface) or "10.0.0.1"' in body
    )


def test_bfd_dst_mac_default_blank_and_arp_resolved():
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'dst_mac: str = "",' in body
    assert "_resolve_dst_mac(iface, dst_ip)" in body
    # Diagnostic surfaced when ARP resolve fails. v0.5.270 (L2-D4)
    # changed the wording to "ARP resolve … failed" and moved it
    # into _arp_fail_diag which is also seeded into
    # counters.last_error — that test lives in the v0.5.270 suite.
    assert "ARP resolve" in body


def test_bfd_src_mac_default_blank_and_iface_derived():
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'src_mac: str = "",' in body
    assert "_iface_mac(iface)" in body


def test_bfd_factory_uses_effective_addressing():
    """The `_factory` closure must reference the eff_* names, not the
    raw parameter names."""
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # The factory line up:
    #   _l2_hdr(eff_src_mac, eff_dst_mac, ...)
    #   IP(src=eff_src_ip, dst=dst_ip, ...)
    assert "_l2_hdr(eff_src_mac, eff_dst_mac" in body
    assert "IP(src=eff_src_ip, dst=dst_ip" in body


# --- L2-C4/C5: VRRP/IGMP/PIM src_ip/src_mac defaults -------------


def test_vrrp_src_ip_default_blank_and_auto_derived():
    idx = L2.find("def start_vrrp")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'src_ip: str = "",' in body
    # v4 branch derives, v6 falls back to 10.0.0.1 (documented).
    assert "_iface_primary_ipv4(iface)" in body
    assert "eff_src_ip" in body


def test_igmp_src_ip_and_src_mac_blank_defaults():
    idx = L2.find("def start_igmp")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'src_ip: str = "",' in body
    assert 'src_mac: str = "",' in body
    assert "_iface_primary_ipv4(iface)" in body
    assert "_iface_mac(iface)" in body


def test_pim_src_ip_and_src_mac_blank_defaults():
    idx = L2.find("def start_pim_hello")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'src_ip: str = "",' in body
    assert 'src_mac: str = "",' in body
    assert "_iface_primary_ipv4(iface)" in body
    assert "_iface_mac(iface)" in body


# --- L2-C6: LACP fast=True OR's Timeout=Short into state ---------


def test_lacp_fast_ors_timeout_bit_into_state():
    idx = L2.find("def start_lacp")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # eff_state applies the fast → Timeout bit.
    assert "eff_state = int(state)" in body
    assert "eff_state |= 0x02" in body
    # And the factory uses eff_state, not raw state.
    assert "actor_state=eff_state" in body


def test_lacp_preview_state_mirrors_fast_bit():
    """`_lacpdu` preview must OR 0x02 when fast=True to match wire."""
    idx = L2.find("def _lacpdu(b):")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # The expression: (int(b.get("state") or 0x05) | (0x02 if b.get("fast") else 0))
    assert 'b.get("fast")' in body
    assert "0x02" in body


# --- L2-C7: LACP system_mac auto-derived -------------------------


def test_lacp_system_mac_default_blank():
    idx = L2.find("def start_lacp")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    assert 'system_mac: str = "",' in body
    assert "eff_system_mac = _iface_mac(iface)" in body or \
           "eff_system_mac = (system_mac or" in body
    # Factory uses eff_system_mac in both L2 header and Actor field.
    assert "_l2_hdr(eff_system_mac," in body
    assert "actor_system=eff_system_mac" in body


# --- L2-C8: BFD source UDP port randomized -----------------------


def test_bfd_src_udp_port_randomized_per_session():
    idx = L2.find("def start_bfd")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end]
    # Uses SystemRandom for the port so two sessions to the same
    # peer don't collide on demux.
    assert "SystemRandom()" in body
    assert "randint(49152, 65535)" in body
    # Fixed 49152 literal must not appear on the sport line any
    # more — check any UDP(sport=…) line in the factory.
    factory_start = body.find("def _factory():")
    factory_end = body.find("_register_and_start")
    factory_body = body[factory_start:factory_end]
    assert "sport=49152" not in factory_body


# --- Client dialog: all defaults blank ---------------------------


def test_client_lacp_system_mac_default_blank():
    assert "v0.5.269 (L2-C7)" in DLG
    assert "self._lacp_system_mac = QLineEdit(\"\")" in DLG


def test_client_vrrp_src_ip_default_blank():
    assert "v0.5.269 (L2-C4)" in DLG
    assert "self._vrrp_src_ip = QLineEdit(\"\")" in DLG


def test_client_igmp_src_ip_and_src_mac_blank():
    assert "self._igmp_src_ip = QLineEdit(\"\")" in DLG
    assert "self._igmp_src_mac = QLineEdit(\"\")" in DLG


def test_client_pim_src_ip_and_src_mac_blank():
    assert "self._pim_src_ip = QLineEdit(\"\")" in DLG
    assert "self._pim_src_mac = QLineEdit(\"\")" in DLG


def test_client_bfd_src_ip_src_mac_dst_mac_blank():
    assert "self._bfd_src_ip = QLineEdit(\"\")" in DLG
    assert "self._bfd_src_mac = QLineEdit(\"\")" in DLG
    assert "self._bfd_dst_mac = QLineEdit(\"\")" in DLG


def test_client_lacp_dropdown_flags_bridge_consumed():
    """L2-C9: LACP label carries the '(bridge-consumed)' hint."""
    idx = DLG.find("PROTOCOLS = [")
    end = DLG.find("]", idx)
    proto_list = DLG[idx:end]
    assert "bridge-consumed" in proto_list


# --- Client submit paths: skip validation when blank -------------


def test_client_lacp_skip_mac_validation_when_blank():
    idx = DLG.find('if proto == "lacp":')
    body = DLG[idx:idx + 1500]
    assert "v0.5.269 (L2-C7)" in body
    # The validate call is nested inside `if mac:`.
    guard = body.find("if mac:")
    validate = body.find("_validate_mac(mac)")
    reject = body.find('_reject(f"System MAC:')
    assert 0 < guard < validate < reject


def test_client_vrrp_skip_src_ip_validation_when_blank():
    idx = DLG.find('elif proto == "vrrp":')
    body = DLG[idx:idx + 3000]
    assert "v0.5.269 (L2-C4)" in body
    # `if src_ip:` guards the validate.
    guard = body.find("if src_ip:")
    validate = body.find("_validate_ip(src_ip, family=ip_family)")
    assert 0 < guard < validate


def test_client_bfd_skip_all_optional_validation_when_blank():
    idx = DLG.find('elif proto == "bfd":')
    body = DLG[idx:idx + 3500]
    assert "v0.5.269 (L2-C2/C3/C5)" in body
    # src_ip, src_mac, dst_mac guarded with `if <var>:`. dst_ip
    # stays REQUIRED (no guard).
    assert body.count("if src_ip:") >= 1
    assert body.count("if src_mac:") >= 1
    assert body.count("if dst_mac:") >= 1


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 269)
