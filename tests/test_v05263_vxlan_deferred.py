"""v0.5.263 — VXLAN deferred fixes (multi-peer FDB, veth collision,
VTEP allowlist, IPv6 EVPN, cleanup leak)."""

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
VX = (REPO / "utils" / "vxlan.py").read_text()


# --- VXLAN-4: veth suffix from device_id hash ---------------------


def test_veth_ip_uses_hash_based_suffix():
    assert "audit VXLAN-4" in VX
    assert "hashlib" in VX
    # New suffix range is [11, 250].
    assert "_suffix = 11 + (_digest % 240)" in VX
    # Old hardcoded `.10/24` is gone from live code.
    live_old = [
        line for line in VX.splitlines()
        if 'veth_ip = f"{local_ip.rsplit(\'.\', 1)[0]}.10/24"' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"hardcoded .10 suffix still live: {live_old!r}"


# --- VXLAN-6: multi-peer FDB ---------------------------------------


def test_multi_peer_fdb_loop_defined():
    assert "audit VXLAN-6" in VX
    # Every extra peer gets a `bridge fdb append` with the
    # all-zero MAC (BUM head-end).
    assert '"bridge", "fdb", "append"' in VX
    assert '"00:00:00:00:00:00"' in VX
    assert "_extra_peers = [p for p in _all_peers if p and p != remote_ip]" in VX


def test_multi_peer_gated_on_not_multicast():
    """FDB append is unicast head-end replication; skip when the
    VXLAN is already in multicast BUM mode (`use_multicast=True`)."""
    idx = VX.find("audit VXLAN-6")
    body = VX[idx:idx + 2000]
    assert "if _extra_peers and not use_multicast:" in body


# --- VXLAN-8: allowlist replaced by class-based helper ------------


def test_plausible_vtep_helper_defined():
    assert "audit VXLAN-8" in VX
    assert "def _is_plausible_vtep_ip(" in VX
    # Uses ipaddress.ip_address for classification.
    assert "ipaddress.ip_address(s)" in VX


def test_all_allowlist_sites_replaced():
    """No live `startswith(('192.', '10.', '172.'))` remains — the
    docstring may still mention the pre-fix pattern for context."""
    live_old = [
        line for line in VX.splitlines()
        if "startswith(('192.', '10.', '172.'))" in line
        and not line.lstrip().startswith("#")
        and 'accidental' not in line  # docstring context
    ]
    assert live_old == [], f"allowlist still live at {len(live_old)} site(s): {live_old!r}"


def test_helper_accepts_cgnat_and_public_unicast():
    sys.path.insert(0, str(REPO))
    try:
        from utils.vxlan import _is_plausible_vtep_ip
        assert _is_plausible_vtep_ip("100.64.0.1", local_ip="10.0.0.1") is True  # CGNAT
        assert _is_plausible_vtep_ip("8.8.8.8", local_ip="10.0.0.1") is True     # public
        assert _is_plausible_vtep_ip("169.254.1.1", local_ip="10.0.0.1") is True  # link-local
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


def test_helper_rejects_invalid_and_local():
    sys.path.insert(0, str(REPO))
    try:
        from utils.vxlan import _is_plausible_vtep_ip
        assert _is_plausible_vtep_ip("10.0.0.1", local_ip="10.0.0.1") is False   # is local
        assert _is_plausible_vtep_ip("0.0.0.0", local_ip="10.0.0.1") is False
        assert _is_plausible_vtep_ip("127.0.0.1", local_ip="10.0.0.1") is False  # loopback
        assert _is_plausible_vtep_ip("224.0.0.1", local_ip="10.0.0.1") is False  # multicast
        assert _is_plausible_vtep_ip("", local_ip="10.0.0.1") is False
        assert _is_plausible_vtep_ip(None, local_ip="10.0.0.1") is False
        assert _is_plausible_vtep_ip("garbage", local_ip="10.0.0.1") is False
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


def test_helper_accepts_ipv6():
    sys.path.insert(0, str(REPO))
    try:
        from utils.vxlan import _is_plausible_vtep_ip
        assert _is_plausible_vtep_ip("2001:db8::2", local_ip="2001:db8::1") is True
        assert _is_plausible_vtep_ip("::1", local_ip="10.0.0.1") is False  # v6 loopback
    finally:
        if str(REPO) in sys.path:
            sys.path.remove(str(REPO))


# --- VXLAN-10: IPv6 EVPN Type-2 parse -----------------------------


def test_evpn_t2_regex_accepts_ipv4_and_ipv6():
    assert "audit VXLAN-10" in VX
    assert "_EVPN_T2_RE = re.compile(" in VX
    # Prefix-length group accepts 32 (IPv4) or 128 (IPv6).
    assert r"\[(?:32|128)\]" in VX
    # IP capture group accepts colon (IPv6) too.
    assert r"([0-9a-fA-F:.]+)" in VX


def test_evpn_t2_fallback_regex_present():
    assert "_EVPN_T2_FALLBACK_RE = re.compile(" in VX


def test_evpn_t2_regex_matches_ipv4_route():
    _re = re.compile(
        r'\[2\]:\[(\d+)\]:\[48\]:\[([0-9a-fA-F:]+)\]:'
        r'\[(?:32|128)\]:\[([0-9a-fA-F:.]+)\]'
    )
    m = _re.search("[2]:[5000]:[48]:[24:5d:92:a7:65:06]:[32]:[10.0.0.101]")
    assert m and m.group(3) == "10.0.0.101"


def test_evpn_t2_regex_matches_ipv6_route():
    _re = re.compile(
        r'\[2\]:\[(\d+)\]:\[48\]:\[([0-9a-fA-F:]+)\]:'
        r'\[(?:32|128)\]:\[([0-9a-fA-F:.]+)\]'
    )
    m = _re.search("[2]:[5000]:[48]:[24:5d:92:a7:65:06]:[128]:[2001:db8::101]")
    assert m and m.group(3) == "2001:db8::101"


# --- VXLAN-11: teardown removes veth pair -------------------------


def test_teardown_removes_veth_pair():
    assert "audit VXLAN-11" in VX
    # Loops over both peer names.
    assert 'for _veth in (f"veth{vni}", f"veth{vni}-peer"):' in VX
    # Uses `ip link del` inside _run.
    idx = VX.find("audit VXLAN-11")
    body = VX[idx:idx + 1500]
    assert '"ip", "link", "del", _veth' in body


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 263)
