"""v0.5.351 — stop-path bundle: three MED stop-side cleanups.

Each of the three fixes has a paired-side history: the START-path
already carried the equivalent guard; the STOP-path was drifting
behind.

A. `stop_dhcp_client` calls `_kill_stale_dhcp6c` after the
   single-pidfile `dhcp6c -x` release. Symmetric with v0.5.240's
   v4 `_kill_stale_dhclients` sweep already sitting a few lines
   below. Pre-fix, restart cycles could accumulate orphan dhcp6c.

B. `stop_dhcp_client` restores `net.ipv6.conf.<iface>.accept_ra=1`
   and `.autoconf=1` after v0.5.346's start-path lockdown
   (`accept_ra=2`, `autoconf=0`). Pre-fix, Stop DHCP left the
   iface in a permanent v6-lockdown state.

C. `stop_dhcp_server` sweeps v6 pool-subnet anchors via
   `_remove_matching_ipv6_anchors` (a new helper mirroring v0.5.239's
   v4 `_remove_matching_ipv4_anchors`). Pre-fix, rotating a
   server's `ipv6_server_ip` / `ipv6_prefix` left the OLD /64
   anchor stuck on the iface.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def test_all_three_markers_present():
    src = _dhcp_src()
    assert "v0.5.351 (audit stop-client-v6-stragglers-sweep)" in src
    assert "v0.5.351 (audit stop-client-accept-ra-restore)" in src
    assert "v0.5.351 (audit stop-server-v6-anchor-sweep-missing)" in src


# --- Fix A: dhcp6c straggler sweep on stop_dhcp_client ---


def test_A_kill_stale_dhcp6c_called_in_stop_dhcp_client():
    """The v0.5.351 A marker must sit inside `stop_dhcp_client` and
    call `_kill_stale_dhcp6c`. Structural check that the sweep is
    actually wired, not just documented."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_client(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "v0.5.351 (audit stop-client-v6-stragglers-sweep)" in body
    # The call must live in this function.
    assert "_kill_stale_dhcp6c(interface" in body


def test_A_dhcp6c_sweep_sits_after_pkill_call():
    """The sweep must run AFTER the single-pkill `dhcp6c -x`
    attempt so any orphan the pkill missed still gets cleaned. If
    ordering flips, a fresh Stop cycle can leave a dhcp6c parent
    lingering."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_client(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    # Find where the pkill / dhcp6c release happens vs where the
    # sweep is called.
    pkill_idx = body.index("dhcp6c pkill error")  # inside the try/except log line
    sweep_idx = body.index("_kill_stale_dhcp6c(interface")
    assert pkill_idx < sweep_idx, (
        "v0.5.351 A: _kill_stale_dhcp6c sweep must run AFTER the "
        "single-pkill attempt"
    )


def test_A_kill_stale_dhcp6c_helper_still_exists():
    """The v0.5.338 helper this fix delegates to must still exist.
    Regression guard against someone removing the helper without
    noticing this new caller."""
    src = _dhcp_src()
    assert "def _kill_stale_dhcp6c(" in src


# --- Fix B: accept_ra + autoconf restore on stop_dhcp_client ---


def test_B_restore_loop_sets_both_sysctls_to_1():
    """The stop-path must restore both `accept_ra` and `autoconf`
    to `1` (the Linux default for a host iface). v0.5.346 set them
    to `2` and `0` respectively; leaving them there permanently
    breaks a subsequent v4 role on the same iface."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_client(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "v0.5.351 (audit stop-client-accept-ra-restore)" in body
    # Both sysctl paths mentioned.
    assert 'net.ipv6.conf.{interface}.accept_ra' in body
    assert 'net.ipv6.conf.{interface}.autoconf' in body
    # And the write is `=1` (not the v0.5.346 start-path values 2/0).
    assert "=1" in body


def test_B_restore_is_best_effort():
    """The restore must not fail Stop DHCP on sysctl error. Best-
    effort: log at debug and continue. Matches v0.5.346's start-
    path philosophy (sysctl failure is non-fatal)."""
    src = _dhcp_src()
    marker_idx = src.index("v0.5.351 (audit stop-client-accept-ra-restore)")
    body = src[marker_idx:marker_idx + 2000]
    assert "except Exception as _sysctl_exc:" in body
    assert "logger.debug" in body
    assert "non-fatal" in body


def test_B_v0_5_346_start_path_still_intact():
    """The start-path lockdown this fix balances must still be in
    place. Regression guard: if v0.5.346 gets reverted, this
    restore becomes redundant AND the semantics silently drift."""
    src = _dhcp_src()
    assert "v0.5.346" in src


# --- Fix C: v6 anchor sweep on stop_dhcp_server ---


def test_C_remove_matching_ipv6_anchors_helper_defined():
    """The new v0.5.351 helper `_remove_matching_ipv6_anchors` must
    exist as a module-level function — v6 mirror of v0.5.239's
    `_remove_matching_ipv4_anchors`."""
    src = _dhcp_src()
    assert "def _remove_matching_ipv6_anchors(" in src
    # And explicitly references the v0.5.239 parity.
    idx = src.index("def _remove_matching_ipv6_anchors(")
    body = src[idx:idx + 2500]
    assert "v0.5.239" in body
    assert "_remove_matching_ipv4_anchors" in body


def test_C_helper_skips_link_local():
    """Link-local IPv6s (fe80::/10) are kernel-managed and must
    never be candidates for anchor removal — including them would
    let a caller accidentally rip the SLAAC link-local off. Same
    guard the v6 sister files (v0.5.335, v0.5.336) already carry."""
    src = _dhcp_src()
    idx = src.index("def _remove_matching_ipv6_anchors(")
    body = src[idx:idx + 2500]
    assert "is_link_local" in body


def test_C_helper_intersects_with_current_iface_addrs():
    """The helper MUST intersect the caller's candidate set with the
    interface's CURRENT v6 assignment list before calling
    `_remove_ipv6_address`. v0.5.239's v4 helper carries the same
    guard (the whole reason it exists is to avoid removing an
    address that isn't there). Structural: the helper must call
    `_parse_ipv6` to read the current list."""
    src = _dhcp_src()
    idx = src.index("def _remove_matching_ipv6_anchors(")
    body = src[idx:idx + 2500]
    assert "_parse_ipv6(interface" in body


def test_C_stop_dhcp_server_wires_the_v6_sweep():
    """The new helper must actually be CALLED from `stop_dhcp_server`.
    A helper with no caller is dead code."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "v0.5.351 (audit stop-server-v6-anchor-sweep-missing)" in body
    assert "_remove_matching_ipv6_anchors(" in body


def test_C_sweep_collects_candidates_from_both_key_spellings():
    """`dhcp_config` can carry the v6 pool under two different key
    spellings depending on which dialog version wrote it
    (`ipv6_pool_start`/`ipv6_prefix` OR legacy `pool6_start`/
    `prefix6`). The sweep must iterate both spellings — pre-fix,
    a config edited across the v0.5.344 boundary could hide the
    OLD /64 anchor from the sweep."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    marker_idx = body.index("v0.5.351 (audit stop-server-v6-anchor-sweep-missing)")
    sweep_body = body[marker_idx:marker_idx + 3000]
    # Both key spellings enumerated.
    assert '"ipv6_pool_start"' in sweep_body
    assert '"pool6_start"' in sweep_body


def test_C_sweep_is_best_effort():
    """A raise inside the sweep must not fail `stop_dhcp_server` —
    the server is already dnsmasq-killed at this point; a broken
    sweep is a warning, not a crash."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    marker_idx = body.index("v0.5.351 (audit stop-server-v6-anchor-sweep-missing)")
    # Body widened 3000 → 5000: v0.5.352 (audit stop-server-v6-
    # parent-nic-sweep) inserted the parent-NIC sweep between the
    # v0.5.351 subif sweep and its except, pushing the except past
    # 3000 chars.
    sweep_body = body[marker_idx:marker_idx + 5000]
    assert "except Exception as _v6_sweep_exc:" in sweep_body
    assert "non-fatal" in sweep_body


# --- Regression guards ---


def test_v0_5_239_v4_sweep_still_intact():
    """The v4 helper this v6 mirror is patterned on must still
    exist. If v0.5.239's `_remove_matching_ipv4_anchors` ever gets
    refactored away, this fix's structural mirror argument breaks."""
    src = _dhcp_src()
    assert "def _remove_matching_ipv4_anchors(" in src


def test_v0_5_240_v4_straggler_sweep_still_intact():
    """The v4 straggler helper `_kill_stale_dhclients` this fix's
    v6 mirror pairs with must still exist AND still be called
    from `stop_dhcp_client`."""
    src = _dhcp_src()
    assert "def _kill_stale_dhclients(" in src
    fn_idx = src.index("def stop_dhcp_client(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert "_kill_stale_dhclients(interface" in body


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
