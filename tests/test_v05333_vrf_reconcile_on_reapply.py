"""v0.5.333 — `start_frr_container` reconciles VRF setup on the
already-running container path.

Root cause: v0.5.310 (LOCAL host-route install) and v0.5.332
(CONNECTED subnet-route install) both live in `_create_vrf`, but
`start_frr_container` short-circuits (returns early) when the
container is already running. Result: an operator upgrading netgen
only sees these fixes AFTER deleting and re-adding the device —
plain Apply on an existing running device doesn't reconcile.

Operator on srv06 2026-09-15 hit this on device5 (IPv6-only,
2001:db8:20::2/64 on vlan20 in vrf-b710e366aec): upgraded from
v0.5.331 → v0.5.332, restarted the client, re-applied device5,
still yellow. Because the container was already running, the
v0.5.332 connected-route install never fired.

Fix: on the already-running path, call `_create_vrf` (fully
idempotent — swallows "File exists" at every step) so the fixes
inside it re-apply for existing devices.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _frr_docker_src():
    return (_REPO / "utils" / "frr_docker.py").read_text()


def test_marker_present():
    src = _frr_docker_src()
    assert "v0.5.333 (audit vrf-reconcile-on-reapply)" in src


def test_reconcile_lives_before_the_return():
    """The reconcile block MUST execute before `return container_name`
    on the already-running path — otherwise the fix is dead code."""
    src = _frr_docker_src()
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    # Slice from the marker forward to find the next `return
    # container_name` on that same code path.
    tail = src[marker_idx:marker_idx + 3000]
    # Reconcile call must appear...
    assert "self._create_vrf(device_id, _reconcile_iface)" in tail
    # ...before the return.
    call_idx = tail.index("self._create_vrf(device_id, _reconcile_iface)")
    ret_idx = tail.index("return container_name")
    assert call_idx < ret_idx, (
        "v0.5.333 reconcile call must execute before `return "
        "container_name` on the already-running path"
    )


def test_reconcile_lives_inside_already_running_branch():
    """Reconcile must NOT accidentally land on the fresh-launch path
    (which already calls `_create_vrf` at ~line 752). Verify the
    marker sits between the `already running` log line and the
    `else: existing_container.remove(force=True)` branch, which is
    unique to the already-running check block."""
    src = _frr_docker_src()
    ar_log_idx = src.index('already running"')
    remove_idx = src.index('existing_container.remove(force=True)')
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    assert ar_log_idx < marker_idx < remove_idx, (
        "v0.5.333 reconcile marker must sit inside the "
        "`existing_container.status == 'running'` branch"
    )


def test_reconcile_computes_iface_name_the_same_way_as_fresh_launch():
    """iface_name derivation must match the fresh-launch path (VLAN →
    `vlan{N}`, else the raw `interface` field). Regression guard so
    a future refactor of the fresh-launch derivation stays aligned."""
    src = _frr_docker_src()
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    body = src[marker_idx:marker_idx + 3000]
    # VLAN branch:
    assert '_reconcile_iface = f"vlan{_vlan}"' in body
    # Interface fallback:
    assert '_reconcile_iface = _interface' in body
    # None guard so a device missing both fields doesn't crash:
    assert '_reconcile_iface = None' in body


def test_reconcile_stashes_vrf_name_back_into_device_config():
    """The fresh-launch path stashes `vrf_name` into device_config so
    BGP/OSPF/ISIS configurators can emit `vrf <name>` in their router
    blocks. The reconcile path must do the same — otherwise a running
    device that gets VRF-reconciled would still report no vrf_name to
    downstream code."""
    src = _frr_docker_src()
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    body = src[marker_idx:marker_idx + 3000]
    assert "device_config['vrf_name'] = _reconcile_vrf" in body


def test_reconcile_wraps_the_call_in_try_except():
    """A raise from `_create_vrf` on the reconcile path must NOT
    prevent us from returning the container name — the container is
    already up and serving traffic; a broken reconcile is a warning,
    not a crash."""
    src = _frr_docker_src()
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    body = src[marker_idx:marker_idx + 3000]
    assert "except Exception as _reconcile_exc:" in body
    assert "return container_name" in body


def test_reconcile_uses_the_same_create_vrf_as_fresh_launch():
    """We MUST call the same idempotent `_create_vrf` method — not
    duplicate the route-install logic. That way any future v0.5.334+
    fix inside `_create_vrf` automatically flows to reconcile."""
    src = _frr_docker_src()
    # There should be exactly TWO `self._create_vrf(device_id,` call
    # sites in `start_frr_container` now (one on each path).
    fresh_launch_call = src.count("vrf_name = self._create_vrf(device_id, iface_name)")
    reconcile_call = src.count("self._create_vrf(device_id, _reconcile_iface)")
    assert fresh_launch_call == 1, (
        f"expected exactly 1 fresh-launch _create_vrf call, got {fresh_launch_call}"
    )
    assert reconcile_call == 1, (
        f"expected exactly 1 reconcile _create_vrf call, got {reconcile_call}"
    )


def test_v0_5_310_and_v0_5_332_still_intact():
    """The whole point of v0.5.333 is to make v0.5.310 and v0.5.332
    fire for re-applies. Regression guard that both markers still
    live inside `_create_vrf`."""
    src = _frr_docker_src()
    assert "v0.5.310 (audit vrf-local-host-route-drift)" in src
    assert "v0.5.332 (audit vrf-connected-route-missing)" in src


def test_reconcile_comment_names_operator_incident():
    """The comment must name the srv06 device5 incident and the
    re-apply-doesn't-heal root cause so a future refactor can see
    WHY the reconcile lives on the running-container path."""
    src = _frr_docker_src()
    marker_idx = src.index("v0.5.333 (audit vrf-reconcile-on-reapply)")
    body = src[marker_idx:marker_idx + 3000]
    assert "re-apply" in body.lower()
    assert "device5" in body or "srv06" in body
    assert "idempotent" in body.lower()


def test_frr_docker_ast_parses():
    import ast
    ast.parse(_frr_docker_src())
