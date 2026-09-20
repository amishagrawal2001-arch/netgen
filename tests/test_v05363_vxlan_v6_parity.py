"""v0.5.363 — VXLAN v6 parity: two fixes for family-blind code paths
in `utils/vxlan.py`.

A4 — `configure_vxlan_arp_fdb_from_evpn` remote SVI derivation
    Pre-fix, `remote_svi_obj = IPv4Address(int(local_svi) + 1)`.
    Three defects hidden behind the outer `except Exception:
    return False` swallow:
      1. Hardcoded IPv4 — dual-stack overlay with v6 SVI raised
         IPv4Address(int) construction error → silent False return
         → ARP+FDB never installed.
      2. No bounds check — local SVI at `.255` produced `.256`
         which IPv4Address rejects.
      3. Point-to-point assumption — 3-VTEP fabric with two remote
         peers still only derived one candidate.
    Fix: `ipaddress.ip_address` (family-aware) + explicit overflow
    check per-family max + optional `remote_peer_svi_ips` config
    map for explicit peer→SVI mapping. Multi-candidate list is
    logged for future extension; current shape returns the first
    candidate to preserve backward-compat.

A6 — veth IP derivation assumes IPv4 dotted-quad
    Pre-fix, `veth_ip = f"{local_ip.rsplit('.', 1)[0]}.{suffix}/24"`.
    IPv6 VTEP (`local_ip=2001:db8::10`) → nonsense address
    (`2001:db8:.11/24`) → `ip addr add` rejects → outer except
    swallows → L2 VNI has no ARP anchor → Type-2 EVPN routes
    never generate (silent failure).
    Fix: detect family via `ipaddress.ip_address(...)`; v4 keeps
    the historical shape; v6 skips the v4-shaped anchor step
    (v0.5.263's IPv6 EVPN plumbing installs ND anchors via a
    different code path).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _vxlan_src():
    return (_REPO / "utils" / "vxlan.py").read_text()


def test_all_markers_present():
    src = _vxlan_src()
    assert "v0.5.363 (audit vxlan-remote-svi-derive-v4-only, A4)" in src
    assert "v0.5.363 (audit vxlan-veth-ip-assumes-v4, A6)" in src


# --- A4: family-aware remote SVI derivation ---


def test_A4_uses_ip_address_not_ipv4address():
    """The pre-fix `ipaddress.IPv4Address(int(local_svi) + 1)`
    line must be gone. Post-fix must use the family-aware
    `ipaddress.ip_address(...)` for local SVI parsing."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-remote-svi-derive-v4-only, A4)")
    body = src[marker_idx:marker_idx + 4000]
    # Strip comment lines so the fix's own docstring/context
    # references to the buggy form don't false-positive.
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "ipaddress.ip_address(local_svi_ip_str)" in _code_only
    # The family-appropriate max is picked at runtime via a
    # `local_svi_obj.version == 4` branch (v6 implicit else).
    assert "local_svi_obj.version == 4" in _code_only


def test_A4_explicit_peer_svi_map_honored_first():
    """The fix must honor an operator-supplied
    `remote_peer_svi_ips` dict on `config` BEFORE falling into
    the +1 derivation. That's the escape hatch for multi-VTEP
    fabrics where `+1` is ambiguous."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-remote-svi-derive-v4-only, A4)")
    body = src[marker_idx:marker_idx + 4000]
    assert 'config.get("remote_peer_svi_ips")' in body


def test_A4_overflow_bounded_per_family():
    """`+1` on `.255` (v4 max) or `ffff:...` (v6 max) must not
    raise IPv4Address/IPv6Address; instead return False with a
    diagnostic warning."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-remote-svi-derive-v4-only, A4)")
    body = src[marker_idx:marker_idx + 4000]
    assert '_max = (' in body
    assert 'IPv4Address("255.255.255.255"' in body
    assert 'ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff' in body
    assert 'if _next_int > _max' in body


def test_A4_no_bare_ipv4address_int_plus_one():
    """The pre-fix `IPv4Address(int(local_svi_obj) + 1)`
    construction MUST be gone as live code. Comment mentions of
    the buggy form for context are fine (strip comment lines)."""
    src = _vxlan_src()
    fn_idx = src.index("configure_vxlan_arp_fdb_from_evpn")
    body = src[fn_idx:]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "IPv4Address(int(local_svi_obj) + 1)" not in _code_only


# --- A6: veth-IP family branch ---


def test_A6_veth_ip_family_aware():
    """The fix must detect family via ipaddress.ip_address, keep
    the historical dotted-quad shape for v4, and skip the v4
    anchor step for v6 (logging that it's routed via the v0.5.263
    IPv6 EVPN path instead)."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-veth-ip-assumes-v4, A6)")
    body = src[marker_idx:marker_idx + 3500]
    assert "_ipa.ip_address(str(local_ip).split(" in body
    assert "_local_addr.version == 4" in body
    assert "_local_addr.version == 6" in body


def test_A6_v6_skip_logs_reason():
    """The v6 skip branch must log WHY it's skipping — future
    maintainers need to see this isn't a silent skip, it's a
    deliberate handoff to the v0.5.263 EVPN path."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-veth-ip-assumes-v4, A6)")
    body = src[marker_idx:marker_idx + 3500]
    assert "v0.5.263" in body
    assert "IPv6" in body


def test_A6_no_bare_rsplit_on_v6_unguarded():
    """Regression guard: the bare
    `f"{local_ip.rsplit('.', 1)[0]}.{_suffix}/24"` form must be
    reachable only inside the v4 branch (`_local_addr.version
    == 4:`), never unconditionally. Strip comment lines before
    the search so the fix's own docstring (which mentions the
    buggy form for context) doesn't false-positive."""
    src = _vxlan_src()
    marker_idx = src.index("v0.5.363 (audit vxlan-veth-ip-assumes-v4, A6)")
    body = src[marker_idx:marker_idx + 3500]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    _v4_branch = _code_only.index("_local_addr.version == 4")
    _rsplit = _code_only.index("local_ip.rsplit")
    assert _rsplit > _v4_branch, (
        "veth_ip rsplit must be inside the v4 branch, not before"
    )


# --- Regression guards ---


def test_v0_5_263_ipv6_evpn_path_still_intact():
    """A6 hands v6 VTEPs off to v0.5.263's IPv6 EVPN plumbing.
    Regression guard so v0.5.263 markers stay in the file."""
    src = _vxlan_src()
    assert "v0.5.263" in src


def test_ast_parses():
    import ast
    ast.parse(_vxlan_src())
