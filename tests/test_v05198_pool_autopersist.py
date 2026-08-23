"""v0.5.198: server auto-persists BGP route pools sent in the
request payload's `all_route_pools` field.

Operator report on san-hp-srv06 (2026-08-23): attached pools p2,
p5 to a BGP neighbor, applied — no routes advertised. Server log
showed WARNING (from v0.5.197) telling them to "Save them in
Manage Route Pools first". Turned out the client HAD been
sending the pool definitions all along inside the payload
(field: `all_route_pools`), but the server threw them away and
only consulted its DB.

Fix: iterate `all_route_pools` from the payload and call
`add_route_pool()` for each. `add_route_pool` detects an
existing name and delegates to `update_route_pool`, so this is
safe to run every Apply. The subsequent `all_pools_db` load
picks them up transparently.

These tests lock in the auto-persist wiring at the source level.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05198_test_{os.getpid()}.db"),
)


def test_configure_bgp_reads_all_route_pools_from_payload():
    """The fix's contract: configure_bgp must extract the client's
    `all_route_pools` field from the request body — before the
    all_pools_db fetch that follows."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Read the pool list from the request payload...
    assert 'data.get("all_route_pools")' in src, (
        "configure_bgp no longer reads `all_route_pools` from the "
        "request body — v0.5.198 workflow is broken."
    )


def test_configure_bgp_persists_payload_pools_to_db():
    """For each pool in `all_route_pools`, the server must call
    `device_db.add_route_pool(...)` so the DB stays in sync with
    what the client thinks the world looks like."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    assert "device_db.add_route_pool" in src, (
        "configure_bgp doesn't persist payload pools — the auto-"
        "persist step is missing."
    )


def test_configure_bgp_translates_client_field_names():
    """Client sends {name, count, first_host, last_host}; DB
    expects {name, route_count, first_host_ip, last_host_ip}. The
    fix must translate — otherwise add_route_pool silently drops
    fields and every pool ends up as count=1."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Both the client name AND the DB name should be looked at (so
    # a payload from an older or a batch-API source still works).
    assert 'route_count' in src
    assert 'first_host_ip' in src
    assert 'last_host_ip' in src


def test_configure_bgp_persist_is_idempotent():
    """add_route_pool detects an existing name and delegates to
    update_route_pool — so calling configure_bgp twice with the
    same payload must not error out. Lock in that
    add_route_pool is what we call (not INSERT-only)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # We DON'T want INSERT-only path bypassing the exists check.
    assert 'add_route_pool' in src
    # And a defensive try/except around it so a bad row doesn't
    # take down the whole Apply.
    assert 'Failed to auto-persist pool' in src


def test_configure_bgp_translates_count_and_first_last_host():
    """One more source-level guard: the payload's `count`,
    `first_host`, `last_host` client-side field names must be
    mapped into DB fields BEFORE handing to add_route_pool.
    Confirms the mapping (not just that DB field names are
    mentioned somewhere)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Look for lookups that fall back client→db field name.
    assert '_p.get("count"' in src
    assert '_p.get("first_host"' in src
    assert '_p.get("last_host"' in src


# ─────────────────────────────────────────────────────────────────────
# The other v0.5.198 fix — VRF-scoped static routes so the BGP
# instance's redistribute-static actually sees them. Without this
# the routes go into the default routing table but the VRF-BGP
# instance's redistribute pulls only from its own VRF's table →
# nothing advertised. Same class of "config looks fine but
# nothing happens" that motivated the auto-persist fix.
# ─────────────────────────────────────────────────────────────────────

def test_configure_bgp_route_adv_scopes_static_routes_to_vrf():
    """The `ip route X null0` commands must include the device's
    VRF suffix (` vrf <name>`) when the device is wired into a
    per-device VRF. Otherwise redistribute-static in the VRF-BGP
    instance sees nothing.

    Verified live on srv06 2026-08-23: PfxSnt jumped from 1 to 10
    (5 for p2 + 4 for p5 + 1 connected) once the static routes
    landed in the correct VRF's table."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp_route_advertisement)
    # The suffix is computed once at the top of the function and
    # appended to every ip/ipv6 route command in the loop.
    assert "_vrf_route_suffix" in src
    assert "vrf_name_for_device" in src
    # Both v4 and v6 route emits must use the suffix.
    assert 'f"ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"ipv6 route {route} null0{_vrf_route_suffix}"' in src


def test_cleanup_bgp_route_adv_uses_matching_vrf_suffix():
    """Cleanup must remove `ip route X null0 vrf <name>` — not
    `ip route X null0` — otherwise stale VRF-scoped routes
    accumulate every apply cycle."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_bgp_route_advertisement)
    assert "_vrf_route_suffix" in src
    assert 'f"no ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"no ipv6 route {route} null0{_vrf_route_suffix}"' in src
