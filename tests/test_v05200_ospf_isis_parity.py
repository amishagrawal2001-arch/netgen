"""v0.5.200: OSPF and ISIS route-advertisement paths get the same
VRF-suffix + prefix-list cleanup guards that BGP received in
v0.5.198–v0.5.199. Plus P1 fixes: drop the RM-EXPORT `permit 20`
catch-all that defeated the prefix-list filter, and swap the
hardcoded seq 5..50 cleanup loop for a wildcard drop.

Not yet a live-fire test on OSPF/ISIS (operator hasn't reported
those paths broken yet) — these are source-level lock-in tests
so a future refactor doesn't silently undo the parity fixes.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05200_test_{os.getpid()}.db"),
)


# ─────────────────────────────────────────────────────────────────────
# OSPF parity: VRF-suffix on advertise and cleanup
# ─────────────────────────────────────────────────────────────────────

def test_ospf_configure_route_adv_scopes_static_routes_to_vrf():
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_ospf_route_advertisement)
    assert "_vrf_route_suffix" in src
    assert "vrf_name_for_device" in src
    assert 'f"ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"ipv6 route {route} null0{_vrf_route_suffix}"' in src


def test_ospf_cleanup_route_adv_removes_from_correct_vrf():
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_ospf_route_advertisement)
    assert "_vrf_route_suffix" in src
    assert 'f"no ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"no ipv6 route {route} null0{_vrf_route_suffix}"' in src


# ─────────────────────────────────────────────────────────────────────
# ISIS parity: same shape
# ─────────────────────────────────────────────────────────────────────

def test_isis_configure_route_adv_scopes_static_routes_to_vrf():
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_isis_route_advertisement)
    assert "_vrf_route_suffix" in src
    assert "vrf_name_for_device" in src
    assert 'f"ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"ipv6 route {route} null0{_vrf_route_suffix}"' in src


def test_isis_cleanup_route_adv_removes_from_correct_vrf():
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_isis_route_advertisement)
    assert "_vrf_route_suffix" in src
    assert 'f"no ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"no ipv6 route {route} null0{_vrf_route_suffix}"' in src


# ─────────────────────────────────────────────────────────────────────
# P1: RM-EXPORT permit 20 catch-all is gone from BGP
# ─────────────────────────────────────────────────────────────────────

def test_bgp_route_adv_no_route_map_permit_20_catch_all():
    """`route-map RM-EXPORT permit 20` (no match clause) permits
    everything — nullifies the prefix-list filter and causes
    routes outside the pool set to be advertised. Same for
    RM-EXPORT-IPV6 permit 20. Both dropped in v0.5.200."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp_route_advertisement)
    # Strip pure-comment lines so the fix's own commentary doesn't
    # false-match against the historical pattern.
    live = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert '"route-map RM-EXPORT permit 20"' not in live, (
        "The RM-EXPORT permit 20 catch-all is back — every "
        "static route in FRR will get advertised regardless of "
        "PL-EXPORT."
    )
    assert '"route-map RM-EXPORT-IPV6 permit 20"' not in live, (
        "RM-EXPORT-IPV6 permit 20 catch-all reappeared."
    )


def test_bgp_route_adv_wipes_route_map_before_rebuilding():
    """Related to the catch-all fix: to guarantee a clean rebuild
    without leaving stale permit-20 sequences from a prior
    version, prefix the route-map redefinition with `no route-map
    RM-EXPORT` (idempotent — no-op if the map doesn't exist)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp_route_advertisement)
    assert '"no route-map RM-EXPORT"' in src
    assert '"no route-map RM-EXPORT-IPV6"' in src


# ─────────────────────────────────────────────────────────────────────
# P1: cleanup prefix-list wildcard drop
# ─────────────────────────────────────────────────────────────────────

def test_bgp_cleanup_wildcards_prefix_list_removal():
    """Previously enumerated seq 5..50 explicitly; configure
    generates seq beyond 50 for large pools, leaving orphans.
    Now cleanup drops the whole prefix-list in one line."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_bgp_route_advertisement)
    # The whole-list wildcard drop is what we want to see.
    assert '"no ip prefix-list PL-EXPORT"' in src
    assert '"no ipv6 prefix-list PL-EXPORT"' in src

    # Guard against the old hardcoded range creeping back.
    live = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "range(5, 55, 5)" not in live, (
        "The hardcoded seq 5..50 loop reappeared — pools with >10 "
        "routes will leak orphan prefix-list entries again."
    )
