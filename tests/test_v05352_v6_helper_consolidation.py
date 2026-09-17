"""v0.5.352 — v6 helper consolidation + parent-NIC sweep parity.

Four fixes covering three drift categories found in the post-v0.5.351
audit. Each mirrors a guard the v4 side already carried.

D1 + D2 (single implementation): `_ensure_ipv6_address` gains a
    `_ensure_ipv6_post_add_plumbing` post-add step covering the v0.5.275
    VRF connected-route heal AND the v0.5.282/286 local-table
    host-route heal (both v0.5.338 additions that lived only in
    `start_dhcp_server`). Effect: every caller — critically
    `arp_monitor._replay_dhcp_anchor_setup` — now heals both on a
    tick-time replay, not just on initial Apply.

D3: `_remove_ipv6_address` gains a paired local-table delete (mirror
    of v0.5.290 on the v4 side). Without it, every v6 anchor removal
    left a `/128` local ghost that could martian-drop a later address.

D6: `stop_dhcp_server` v6 sweep also touches the parent NIC — mirror
    of v0.5.287 Fix B on the v4 side. A pre-v0.5.335 apply that
    landed the pool-subnet anchor on the parent (e.g. ens2f0np0)
    instead of the subif was left alive by v0.5.351's subif-only sweep.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def test_all_v0_5_352_markers_present():
    src = _dhcp_src()
    assert "v0.5.352 (audit dhcpv6-helper-post-add-plumbing)" in src
    assert "v0.5.352 (audit dhcpv6-local-table-cleanup)" in src
    assert "v0.5.352 (audit stop-server-v6-parent-nic-sweep)" in src


# --- D1 + D2: _ensure_ipv6_address post-add plumbing consolidation ---


def test_D1_D2_post_add_helper_defined_at_module_level():
    """The v0.5.352 helper must exist as a module-level function so
    `_ensure_ipv6_address` AND arp_monitor's replay path can share
    it. Regression guard against someone inlining it back."""
    src = _dhcp_src()
    assert "def _ensure_ipv6_post_add_plumbing(" in src


def test_D1_helper_calls_vrf_connected_route_probe_and_install():
    """The post-add helper must probe `_vrf_has_connected_route_v6`
    (the v0.5.338 probe) and install `ip -6 route add ... vrf`
    when missing. This is the v0.5.275 v4 mirror."""
    src = _dhcp_src()
    idx = src.index("def _ensure_ipv6_post_add_plumbing(")
    body = src[idx:idx + 5000]
    # Probe.
    assert "_vrf_has_connected_route_v6(" in body
    # Explicit install with the right verb + selectors.
    assert '"ip", "-6", "route", "add"' in body
    assert '"vrf"' in body
    assert '"metric", "256"' in body


def test_D2_helper_calls_local_host_route_probe_and_install():
    """The post-add helper must probe `_v6_has_local_host_route`
    (v0.5.338) and install `local <ip>/128 dev <iface> table local`
    when missing. This is the v0.5.282/286 v4 mirror."""
    src = _dhcp_src()
    idx = src.index("def _ensure_ipv6_post_add_plumbing(")
    body = src[idx:idx + 5000]
    assert "_v6_has_local_host_route(" in body
    assert '"local", f"{address}/128"' in body
    assert '"table", "local"' in body


def test_D1_D2_ensure_ipv6_calls_the_helper_on_success_path():
    """The critical wiring: `_ensure_ipv6_address` must call the
    post-add helper AFTER the `ip -6 addr add` succeeds. Otherwise
    the whole consolidation is dead code."""
    src = _dhcp_src()
    fn_idx = src.index("def _ensure_ipv6_address(")
    # Stop at the next top-level `def ` to bound the scan.
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    # Called on both branches: already-present AND freshly-added.
    assert body.count("_ensure_ipv6_post_add_plumbing(") >= 2, (
        "_ensure_ipv6_address must call the post-add helper on BOTH "
        "the already-present branch and the freshly-added branch so "
        "arp_monitor's tick-time replay heals a missing VRF route "
        "or local-table entry mid-life."
    )


def test_D1_D2_helper_is_best_effort_per_step():
    """Each step (VRF probe + install; local-table probe + install)
    must be independently try/excepted so one failure doesn't
    prevent the other from firing. Matches the v0.5.275/v0.5.286 v4
    best-effort pattern."""
    src = _dhcp_src()
    idx = src.index("def _ensure_ipv6_post_add_plumbing(")
    body = src[idx:idx + 5000]
    # At least two independent try/except blocks (one per step).
    assert body.count("except Exception as _v6_route_exc:") >= 1
    assert body.count("except Exception as _v6_local_exc:") >= 1


# --- D3: _remove_ipv6_address local-table cleanup ---


def test_D3_remove_calls_ip6_route_del_local_128():
    """Paired remove for the v0.5.352 local-table install. Without
    this, every v6 anchor rotation leaks a `/128` local ghost."""
    src = _dhcp_src()
    fn_idx = src.index("def _remove_ipv6_address(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    # Marker sits inside the function.
    assert "v0.5.352 (audit dhcpv6-local-table-cleanup)" in body
    # Exact command shape.
    assert '"ip", "-6", "route", "del"' in body
    assert '"local", f"{address}/128"' in body
    assert '"table", "local"' in body


def test_D3_remove_local_cleanup_is_best_effort_and_swallows_enoent():
    """Kernel may have GC'd the local entry already ("No such
    process" / "No such file"). Cleanup must not raise on absent."""
    src = _dhcp_src()
    fn_idx = src.index("def _remove_ipv6_address(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    assert '"No such process"' in body or "'No such process'" in body
    assert "non-fatal" in body


def test_D3_paired_with_D1_install():
    """Structural check that BOTH sides of the pair exist. If the
    install is removed without removing the delete (or vice versa),
    this test catches the drift."""
    src = _dhcp_src()
    # Install lives inside the post-add helper.
    install_idx = src.index("def _ensure_ipv6_post_add_plumbing(")
    install_body = src[install_idx:install_idx + 5000]
    assert '"ip", "-6", "route", "add"' in install_body
    assert '"local", f"{address}/128"' in install_body
    # Delete lives inside _remove_ipv6_address.
    remove_idx = src.index("def _remove_ipv6_address(")
    remove_next = src.index("\ndef ", remove_idx + 1)
    remove_body = src[remove_idx:remove_next]
    assert '"ip", "-6", "route", "del"' in remove_body
    assert '"local", f"{address}/128"' in remove_body


# --- D6: stop_dhcp_server v6 sweep extends to parent NIC ---


def test_D6_stop_dhcp_server_sweeps_v6_parent_nic():
    """v0.5.351's v6 sweep only touched the subif. v0.5.352 must
    also call `_remove_matching_ipv6_anchors(_parent6, ...)` so a
    pre-v0.5.335 anchor that landed on the parent (ens2f0np0)
    instead of the subif (vlan10) gets cleaned. Mirror of v0.5.287
    Fix B on the v4 side."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    marker_idx = body.index("v0.5.352 (audit stop-server-v6-parent-nic-sweep)")
    sweep_body = body[marker_idx:marker_idx + 2500]
    # Structural: parent lookup + v6 sweep called against it.
    assert "_iface_parent(interface" in sweep_body
    assert "_remove_matching_ipv6_anchors(" in sweep_body
    assert "_parent6" in sweep_body


def test_D6_sweep_uses_the_same_candidate_set_as_the_subif_sweep():
    """Both sweeps (subif + parent) must consume the same
    `_v6_candidates` set built earlier in the block. Otherwise the
    parent sweep could either be blind (empty candidates) or over-
    reach (different candidate source)."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    marker_idx = body.index("v0.5.352 (audit stop-server-v6-parent-nic-sweep)")
    sweep_body = body[marker_idx:marker_idx + 2500]
    # The parent-NIC sweep call must pass `_v6_candidates` (the
    # v0.5.351 candidate set), not build a fresh one.
    assert "_v6_candidates" in sweep_body


def test_D6_sweep_is_conditional_on_parent_present():
    """`_iface_parent` returns None for a plain physical iface
    (nothing to sweep). Must guard `if _parent6:` — otherwise the
    call raises on None."""
    src = _dhcp_src()
    fn_idx = src.index("def stop_dhcp_server(")
    next_fn = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_fn]
    marker_idx = body.index("v0.5.352 (audit stop-server-v6-parent-nic-sweep)")
    sweep_body = body[marker_idx:marker_idx + 2500]
    assert "if _parent6:" in sweep_body


# --- Regression guards ---


def test_v0_5_275_v4_vrf_route_check_still_intact():
    src = _dhcp_src()
    assert "v0.5.275 (DHCP-J2):" in src


def test_v0_5_286_v4_local_table_install_still_intact():
    src = _dhcp_src()
    assert "v0.5.286 (ARP-J6):" in src


def test_v0_5_290_v4_local_table_cleanup_still_intact():
    src = _dhcp_src()
    assert "v0.5.290 (audit anchor-DAD, part 2)" in src


def test_v0_5_287_fix_B_v4_parent_nic_sweep_still_intact():
    """The v4 mirror of D6 — must still be in place. Regression
    guard so future refactors don't accidentally back out the v4
    side while keeping the v6 side."""
    src = _dhcp_src()
    assert "audit anchor-gateway-collision, fix B" in src


def test_v0_5_338_helpers_still_defined():
    """The two v0.5.338 helpers this fix's plumbing calls must
    still exist."""
    src = _dhcp_src()
    assert "def _vrf_has_connected_route_v6(" in src
    assert "def _v6_has_local_host_route(" in src


def test_v0_5_351_v6_sweep_still_intact():
    """D6 extends v0.5.351's subif sweep. That sweep must still
    exist — if it gets deleted, the parent-NIC sweep is orphan
    code."""
    src = _dhcp_src()
    assert "v0.5.351 (audit stop-server-v6-anchor-sweep-missing)" in src


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
