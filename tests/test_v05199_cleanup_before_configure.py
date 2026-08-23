"""v0.5.199: BGP route pool changes now trigger a cleanup pass
BEFORE configure, so old prefixes get withdrawn from the peer
when attached pools change.

Operator report on san-hp-srv06 2026-08-23: attached pools
[p2, p5], applied — peer received 9 prefixes. Then changed
attachment to [p6] only, applied — peer received 30 prefixes
(5 for p2 + 4 for p5 + 20 for p6 + 1 connected). The prefix-
list on the wire contained ONLY 6.6.x entries so config looked
correct, but FRR's BGP RIB still held the old static-route-
sourced prefixes because nobody had withdrawn them.

Root cause: configure_bgp's route-pool branch only ran
cleanup when `attached_pools` became EMPTY. Swapping the
attached set left cleanup un-run and the new configure pass
just added on top of the old state.

Fix: always cleanup-then-configure when known_pools is
non-empty. Cleanup iterates the FULL pool DB (not just what's
attached), so any stale route from any prior pool gets
withdrawn regardless of whether that pool is still attached.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05199_test_{os.getpid()}.db"),
)


def test_configure_bgp_runs_cleanup_before_configure_when_pools_change():
    """The v0.5.199 fix's contract: when known_pools is non-empty
    (i.e. we're going to configure new advertisement), the
    cleanup path must fire FIRST inside the same worker so the
    ordering is guaranteed. Old code just called
    configure_bgp_route_advertisement — new code wraps cleanup +
    configure into a single _cleanup_then_configure thread."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    assert "_cleanup_then_configure" in src, (
        "The single-thread cleanup-then-configure wrapper is "
        "missing — pool changes will leave stale prefixes on the "
        "peer again."
    )
    # And crucially, it calls both in the right order.
    idx_cleanup = src.find(
        "cleanup_bgp_route_advertisement(",
        src.find("_cleanup_then_configure"),
    )
    idx_configure = src.find(
        "configure_bgp_route_advertisement(",
        src.find("_cleanup_then_configure"),
    )
    assert 0 < idx_cleanup < idx_configure, (
        "cleanup call is not before configure call inside "
        "_cleanup_then_configure — the fix is in the wrong "
        "order and stale routes will still land."
    )


def test_configure_bgp_cleanup_is_wrapped_in_try_except():
    """Cleanup should never take down a live BGP apply. Even if
    cleanup fails (e.g. FRR container temporarily unreachable),
    the configure pass still runs so the operator sees SOMETHING
    happen."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # The pattern: cleanup call inside try/except; configure call
    # is OUTSIDE the try so it always runs.
    seg = src[src.find("_cleanup_then_configure"):src.find("_cleanup_then_configure")+2000]
    assert "try:" in seg
    assert "Pre-configure cleanup" in seg


def test_cleanup_iterates_all_pools_db_not_just_attached():
    """The cleanup implementation must remove routes for ALL
    pools known to the server, not just the currently-attached
    set. Otherwise a pool that was attached in a prior apply
    (and has since been dropped from route_pools) never gets
    its routes withdrawn."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_bgp_route_advertisement)
    # Locked in when v0.5.198 landed; this test guards against a
    # future rewrite narrowing the loop scope.
    assert "get_all_route_pools" in src
    assert "for pool in all_pools_db" in src


def test_cleanup_no_ip_route_uses_vrf_suffix():
    """Regression for v0.5.198 lock-in: cleanup must strip routes
    from the same VRF configure put them in, or removals no-op."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.cleanup_bgp_route_advertisement)
    assert 'f"no ip route {route} null0{_vrf_route_suffix}"' in src
    assert 'f"no ipv6 route {route} null0{_vrf_route_suffix}"' in src
