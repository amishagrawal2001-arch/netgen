"""v0.5.259 — VXLAN audit: 7 correctness fixes in utils/vxlan.py."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
VX = (REPO / "utils" / "vxlan.py").read_text()


# --- VXLAN-1: bridge MAC uses all 24 bits --------------------------


def test_bridge_mac_encodes_full_24_bit_vni():
    assert "audit VXLAN-1" in VX
    # New shape has FIVE hex bytes derived from VNI + a static 01 at
    # the end: aa:bb:{high}:{mid}:{low}:01
    assert 'f"aa:bb:{(vni >> 16) & 0xff:02x}:{(vni >> 8) & 0xff:02x}:{vni & 0xff:02x}:01"' in VX


def test_old_bridge_mac_gone():
    """The old 16-bit-only pattern is gone from live code (comments
    may still mention the pre-fix example)."""
    live = [
        line for line in VX.splitlines()
        if 'bridge_mac = f"aa:bb:cc:00:' in line
        and not line.lstrip().startswith("#")
    ]
    assert live == [], f"old bridge MAC still live: {live!r}"


# --- VXLAN-2: VLAN SVI MAC XOR (not add) ---------------------------


def test_vlan_svi_mac_uses_xor_not_add():
    assert "audit VXLAN-2" in VX
    # New shape XORs the two low bytes and masks to 0xff so the
    # last octet can never overflow to 3 hex digits.
    assert '((vni & 0xff) ^ (vlan_id & 0xff)) & 0xff' in VX


def test_old_vlan_svi_add_pattern_gone():
    live = [
        line for line in VX.splitlines()
        if '(vni & 0xff) + (vlan_id & 0xff)' in line
        and not line.lstrip().startswith("#")
    ]
    assert live == [], f"old add-overflow pattern still live: {live!r}"


# --- VXLAN-3: bridge SVI IP bijective ------------------------------


def test_bridge_svi_ip_uses_24_bit_mapping():
    assert "audit VXLAN-3" in VX
    # New formula spreads VNI across the /8; every occurrence uses
    # the same shape.
    assert 'f"10.{(vni >> 16) & 0xff}.{(vni >> 8) & 0xff}.{vni & 0xff}/24"' in VX


def test_old_bridge_svi_overflow_pattern_gone():
    live = [
        line for line in VX.splitlines()
        if 'f"10.0.{vni // 256}.{100 + (vni % 256)}/24"' in line
        and not line.lstrip().startswith("#")
    ]
    assert live == [], f"overflow SVI-IP pattern still live: {live!r}"


# --- VXLAN-5: UnboundLocalError self-reference fixed --------------


def test_evpn_vni_output_self_reference_fixed():
    """Every occurrence of the fallback str() call must reference
    evpn_vni_result.output (not evpn_vni_output — the name being
    assigned). Pre-fix 3 of 4 sites had the self-ref."""
    # Count the total assignments.
    total = VX.count("evpn_vni_output = evpn_vni_result.output.decode")
    assert total >= 3
    # None should still self-reference in the else branch.
    self_ref = re.findall(
        r"evpn_vni_output = evpn_vni_result\.output\.decode.*else str\(evpn_vni_output\)",
        VX,
    )
    assert self_ref == [], f"self-referencing str() still present: {self_ref!r}"


def test_vxlan5_marker_present():
    assert "audit VXLAN-5" in VX


# --- VXLAN-7: multicast group avoids .0 / .255, uses full VNI ----


def test_multicast_helper_defined():
    assert "audit VXLAN-7" in VX
    assert "def _vxlan_default_mcast_group(_v: int) -> str:" in VX
    # Guard against .0 and .255 in the last octet.
    assert "if low == 0:" in VX
    assert "elif low == 255:" in VX


def test_multicast_default_call_sites_use_helper():
    """Both callers use the new helper instead of the old inline
    `239.0.0.{vni % 255}` pattern."""
    live_old = [
        line for line in VX.splitlines()
        if 'f"239.0.0.{vni % 255}"' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old mcast pattern still live: {live_old!r}"
    # Helper is called at least twice (initial-set + fallback).
    assert VX.count("_vxlan_default_mcast_group(") >= 2


# --- VXLAN-9: hardcoded 192.168.0.1 in FDB cleanup gone -----------


def test_hardcoded_lab_ip_removed_from_fdb_cleanup():
    assert "audit VXLAN-9" in VX
    # Not present as a live comparison string.
    live = [
        line for line in VX.splitlines()
        if "'192.168.0.1'" in line
        and not line.lstrip().startswith("#")
    ]
    assert live == [], f"hardcoded 192.168.0.1 still live: {live!r}"


# --- VXLAN-12: word-boundary regex for BGP neighbor match ---------


def test_bgp_neighbor_match_uses_word_boundary_regex():
    assert "audit VXLAN-12" in VX
    assert r"re.compile(rf'\b{re.escape(str(bgp_neighbor_ip))}\b')" in VX
    # Old substring match is gone as a live conditional.
    live = [
        line for line in VX.splitlines()
        if "if bgp_neighbor_ip in line or actual_vtep_ip in line:" in line
        and not line.lstrip().startswith("#")
    ]
    assert live == [], f"substring match still live: {live!r}"


# --- Metadata ------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 259)
