"""v0.5.326 — two operator-reported fixes:

1. Scale count default = 1 (single device). Pre-fix defaulted to 2
   which silently opted operators into scale mode and surprised
   them with the SCALE COLLISION WARNING or unintended duplicates.

2. IPv6 same-subnet stale-address cleanup on `/api/device/apply`.
   Mirror of v0.5.236's IPv4 cleanup. Operator report: editing
   vlan20 IPv6 from 2001:db8:30::1/64 to 2001:db8:20::2/64 left
   THREE addresses on the iface — pre-fix only
   `ip addr del {new_ipv6}` ran which is a no-op when the stale
   address DIFFERS from the new one. Fix: enumerate existing
   IPv6 addresses on the iface and remove same-/prefix strays
   before adding the new address. Skips link-local (fe80::/10 —
   kernel-managed) and cross-subnet addresses (deliberate multi-
   net setups shouldn't be disturbed).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dialog_src():
    return (_REPO / "widgets" / "add_device_dialog.py").read_text()


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


# ---------- #1: Scale count default = 1 ----------


def test_scale_count_defaults_to_one():
    """Regression guard: dialog init MUST default the count to 1.
    Any value >= 2 means scale is on out of the box → common
    footgun where operator opens dialog to add a single device
    and unwittingly creates N."""
    src = _dialog_src()
    assert "self.increment_count.setValue(1)" in src
    # And NOT the pre-v0.5.326 default of 2.
    assert "self.increment_count.setValue(2)" not in src


def test_scale_count_default_documented():
    """The v0.5.326 marker must explain the change so future
    refactors don't silently regress to 2 (or higher)."""
    src = _dialog_src()
    assert "v0.5.326 (audit count-default)" in src


# ---------- #2: IPv6 same-subnet stale cleanup ----------


def test_server_ipv6_step_has_same_subnet_cleanup():
    """Step 4 (IPv6 configure) must have the enumerate-and-remove
    logic mirroring v0.5.236's IPv4 cleanup at Step 4 IPv4."""
    src = _server_src()
    assert "v0.5.326 (audit ipv6-stale-cleanup)" in src


def test_server_ipv6_cleanup_uses_ip_dash_6_probe():
    """The probe MUST filter by -6 (IPv6) — a -4 probe would
    scan IPv4 addresses, no-op for our IPv6 cleanup goal."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert '"ip", "-6", "-o", "addr", "show"' in body


def test_server_ipv6_cleanup_reads_inet6_prefix():
    """Parser looks for `inet6 <addr>/<prefix>` tokens (not `inet`
    which would only see IPv4). Regression guard."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert 'if _toks[_i] == "inet6":' in body


def test_server_ipv6_cleanup_skips_link_local():
    """fe80::/10 is kernel-managed and always present on an UP
    interface. If we deleted it, the kernel would immediately
    re-add it → wasted syscalls + log spam. Must skip via
    `is_link_local` check."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert "is_link_local" in body


def test_server_ipv6_cleanup_preserves_cross_subnet():
    """Legitimate multi-subnet configurations (e.g. DHCPv6 server
    interface with a separate management /64) should be left
    alone. Only same-subnet strays get removed."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    # Same-subnet check: existing network address == new network
    # address AND same prefixlen.
    assert "_existing_net6.network_address == _new_net6.network_address" in body
    assert "_existing_net6.prefixlen == _new_net6.prefixlen" in body


def test_server_ipv6_cleanup_skips_exact_new_address():
    """No point deleting-then-re-adding the SAME address. Skip
    the exact-match case to avoid the pointless syscall."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert "_addr_str6 == ipv6" in body


def test_server_ipv6_cleanup_uses_ipv6network():
    """v6 CIDR parsing must go through ipaddress.IPv6Network, not
    IPv4Network. Regression guard for the copy-paste-from-IPv4
    scenario."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert "_ipa.IPv6Network" in body
    # And explicitly NOT IPv4Network (would be a copy-paste bug).
    # The IPv6 block should not reference IPv4Network at all.
    assert "IPv4Network" not in body


def test_server_ipv6_cleanup_runs_before_ip_addr_add():
    """The cleanup MUST fire before the `ip addr add` — otherwise
    the new address gets added first and then the cleanup would
    (incorrectly) not need to remove it. Check ordering."""
    src = _server_src()
    marker_idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    # Find the immediate next `ip addr add` in the same block.
    add_idx = src.index('"ip", "addr", "add"', marker_idx)
    # Find `ip addr del <_cidr6>` (the same-subnet removal call).
    del_idx = src.index('"ip", "addr", "del", _cidr6', marker_idx)
    assert marker_idx < del_idx < add_idx, (
        "Cleanup del MUST fire between the marker and the ip-addr-add"
    )


def test_server_ipv6_cleanup_exception_safe():
    """The cleanup is wrapped in try/except so a parse error or
    edge case doesn't abort the whole Apply — Apply must still
    add the new IPv6 even if enumeration failed."""
    src = _server_src()
    idx = src.index("v0.5.326 (audit ipv6-stale-cleanup)")
    body = src[idx:idx + 4000]
    assert "except Exception as _clean_exc6:" in body


# ---------- v0.5.236 IPv4 parity (regression guard) ----------


def test_ipv4_cleanup_still_present():
    """v0.5.236 IPv4 same-subnet cleanup must not have regressed
    while adding the IPv6 mirror. Pin its marker."""
    src = _server_src()
    assert "v0.5.236: enumerate every IPv4 on the interface" in src


# ---------- AST parse ----------


def test_dialog_ast_parses():
    import ast
    ast.parse(_dialog_src())


def test_server_ast_parses():
    import ast
    ast.parse(_server_src())
