"""v0.5.201: BGP partial-apply must not wipe neighbors from the
address family it wasn't asked to touch.

Operator report on san-hp-srv06: an existing IPv4 BGP session
was configured (192.168.0.1 established). Operator opened the
GUI, added an IPv6 BGP row, hit Apply. The Apply flow sends
`_apply_address_families = ['ipv6']` (partial apply, IPv6
only). Server correctly preserved the v4 enabled flag +
neighbor in the DB — but then the FRR-side diff-reconfig
block at run_tgen_server.py:9335 read
`bgp_config.get("bgp_neighbor_ipv4")` from the raw request
payload (which for a v6-only apply carries an EMPTY v4
neighbor), computed `new_ipv4_list = []`, and diffed against
the existing v4 neighbor list → `ipv4_to_remove = [192.168.0.1]`.
Wiped the live BGP session.

Same shape in the other direction — a v4-only apply would
have wiped an existing v6 session.

Two coupled fixes:

  1. Guard the diff-reconfig block with `is_partial_apply`
     awareness: if IPv4 is not in `apply_address_families`,
     skip the ipv4 diff entirely (and same for ipv6).
  2. Hand `configure_bgp_for_device` the MERGED config
     (existing + payload overlay) so its own view of which
     neighbors are configured matches what the operator
     actually intended, not what the row-scoped payload
     happens to carry.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05201_test_{os.getpid()}.db"),
)


def test_bgp_diff_reconfig_skips_ipv4_on_v6_only_partial_apply():
    """The `if ipv4_enabled and (old_ipv4_list or new_ipv4_list):`
    diff branch must be guarded so it doesn't fire when the
    partial-apply scope excludes ipv4."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Guard clause exists.
    assert 'if is_partial_apply and "ipv4" not in apply_address_families:' in src, (
        "The v4-diff guard for partial-apply is missing — a v6-"
        "only apply will wipe the live v4 neighbor again."
    )
    # Guard comes BEFORE the diff block.
    guard_idx = src.find('if is_partial_apply and "ipv4" not in apply_address_families:')
    diff_idx = src.find("ipv4_to_remove = [n for n in old_ipv4_list if n not in new_ipv4_list]")
    assert 0 < guard_idx < diff_idx


def test_bgp_diff_reconfig_skips_ipv6_on_v4_only_partial_apply():
    """Symmetric guard — a v4-only apply must not touch v6."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    assert 'if is_partial_apply and "ipv6" not in apply_address_families:' in src


def test_bgp_configure_for_device_gets_merged_config_on_partial_apply():
    """The raw payload on a partial apply carries only the
    address family the operator was editing. Handing it to
    configure_bgp_for_device as-is would look like "no v4
    config" and cause the FRR helper to tear v4 down.
    Merge with existing before calling."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # A local `_bgp_config_for_frr` variable is what we pass to
    # configure_bgp_for_device on partial apply.
    assert "_bgp_config_for_frr" in src
    # Ensure the actual call uses it, not raw bgp_config.
    assert "configure_bgp_for_device(device_id, _bgp_config_for_frr" in src


def test_bgp_configure_for_device_preserves_unselected_family_neighbors():
    """The merged config passed to configure_bgp_for_device
    should carry the existing neighbor + update-source for the
    address family that's out of scope, so the FRR path sees
    the true state and re-affirms it (instead of tearing down)."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    # Both preserve blocks should be there
    seg = src[src.find("_bgp_config_for_frr = existing_bgp_config.copy()"):]
    assert 'if "ipv4" not in apply_address_families:' in seg
    assert 'if "ipv6" not in apply_address_families:' in seg
    assert '"bgp_neighbor_ipv4"' in seg
    assert '"bgp_neighbor_ipv6"' in seg
    assert '"bgp_update_source_ipv4"' in seg
    assert '"bgp_update_source_ipv6"' in seg


def test_bgp_configure_for_device_uses_calculated_enabled_flags():
    """The `ipv4_enabled` / `ipv6_enabled` locals at line 9018+
    are the ones that respect the partial-apply scope (they
    fall back to existing.get on unselected AFs). Those are
    what the merged config should carry, not the payload's raw
    values."""
    import run_tgen_server as srv
    src = inspect.getsource(srv.configure_bgp)
    seg = src[src.find("_bgp_config_for_frr = existing_bgp_config.copy()"):]
    assert '_bgp_config_for_frr["ipv4_enabled"] = ipv4_enabled' in seg
    assert '_bgp_config_for_frr["ipv6_enabled"] = ipv6_enabled' in seg
