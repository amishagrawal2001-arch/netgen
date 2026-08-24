"""v0.5.212: OSPF / IS-IS route-pool advertisement now filters
correctly — attaching pool A to OSPF and pool B to BGP no
longer causes OSPF to advertise pool B's prefixes.

Operator report on JNPR-MAC-HWXVX1 2026-08-23 (after
v0.5.211): attached distinct route pools to OSPF and BGP on
the same device. Both protocols advertised both pools' routes
— OSPF picked up BGP's static routes and vice versa. Static
routes for BOTH pools land in the same VRF's static-RIB
(that's just how FRR works); the per-protocol prefix-list
filter is what's supposed to keep them separate.

Root causes (two bugs stacked):

  1. `ip prefix-list PL-OSPF-EXPORT seq 5 permit 0.0.0.0/0
     le 32` — wildcard at the top of the prefix-list matched
     EVERY IPv4 prefix. Same for `ipv6 prefix-list …seq 5
     permit ::/0 le 128`. The per-pool seq-110+ entries were
     pure decoration; every route matched seq 5 first.

  2. `route-map RM-OSPF-EXPORT permit 20` with no `match`
     clause — a route-map `permit` without any match is a
     catch-all that permits anything. Even if the prefix-list
     had filtered, this clause overrode it for unmatched
     prefixes.

Both bugs existed in configure_ospf_route_advertisement AND
configure_isis_route_advertisement (route-map names differ
but pattern is identical). BGP was already fine — the
v0.5.200 audit fixed BGP's equivalent bugs but didn't reach
OSPF/ISIS.

Fix:
- Drop the seq-5 wildcard prefix-list entry — only per-pool
  prefixes get added.
- Drop the `permit 20` catch-all route-map clause — implicit
  deny at the end correctly rejects unmatched routes.
- Explicit `no ip prefix-list …` + `no route-map …` before
  rebuild so re-apply always produces a deterministic
  filter (same wipe-and-rebuild BGP has used since v0.5.200).
- Reorder: prefix-list entries land in the pool loop (global
  mode); route-map creation happens AFTER the loop (safer
  vtysh mode transitions).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05212_test_{os.getpid()}.db"),
)


def _slice(fn_name: str) -> str:
    src = (REPO / "run_tgen_server.py").read_text()
    idx = src.find(f"def {fn_name}(")
    assert idx >= 0, f"{fn_name} moved or renamed"
    return src[idx:idx + 10000]


# ─────────────────────────────────────────────────────────────────────
# OSPF configure — v4 and v6 filter properly
# ─────────────────────────────────────────────────────────────────────

def test_ospf_configure_no_wildcard_prefix_list():
    """The prefix-list must not start with `seq 5 permit
    0.0.0.0/0 le 32` (or `::/0 le 128`) — that wildcard
    defeats the per-pool filter."""
    body = _slice("configure_ospf_route_advertisement")
    assert 'PL-OSPF-EXPORT seq 5 permit 0.0.0.0/0' not in body, (
        "OSPF v4 prefix-list still has the seq-5 wildcard permit — "
        "every route will pass the filter regardless of pool"
    )
    assert 'PL-OSPF6-EXPORT seq 5 permit ::/0' not in body, (
        "OSPF v6 prefix-list still has the seq-5 wildcard permit"
    )


def test_ospf_configure_no_permit_20_catchall():
    """`route-map X permit 20` with no match clause is a
    permit-everything override — must not appear in the
    emitted vtysh commands."""
    body = _slice("configure_ospf_route_advertisement")
    # Look at emitted vtysh_commands.append/extend targets.
    # A python-string `"route-map RM-OSPF-EXPORT permit 20"`
    # would reach FRR; comments are fine (they explain the
    # removed bug).
    for name in ("RM-OSPF-EXPORT", "RM-OSPF6-EXPORT"):
        assert f'"route-map {name} permit 20"' not in body, (
            f"OSPF route-map {name} still emits permit 20 catch-all"
        )


def test_ospf_configure_wipes_before_rebuild():
    """Idempotent re-apply requires wiping the old
    prefix-list + route-map before adding fresh entries.
    Otherwise stale entries from a prior apply accumulate."""
    body = _slice("configure_ospf_route_advertisement")
    assert '"no ip prefix-list PL-OSPF-EXPORT"' in body
    assert '"no route-map RM-OSPF-EXPORT"' in body
    assert '"no ipv6 prefix-list PL-OSPF6-EXPORT"' in body
    assert '"no route-map RM-OSPF6-EXPORT"' in body


def test_ospf_configure_still_has_permit_10_match():
    """The permit-10 clause with `match … prefix-list` is
    what actually applies the filter — removal would be a
    permit-nothing which is just as bad as permit-all."""
    body = _slice("configure_ospf_route_advertisement")
    assert '"route-map RM-OSPF-EXPORT permit 10"' in body
    assert '" match ip address prefix-list PL-OSPF-EXPORT"' in body
    assert '"route-map RM-OSPF6-EXPORT permit 10"' in body
    assert '" match ipv6 address prefix-list PL-OSPF6-EXPORT"' in body


# ─────────────────────────────────────────────────────────────────────
# ISIS parity
# ─────────────────────────────────────────────────────────────────────

def test_isis_configure_no_wildcard_prefix_list():
    body = _slice("configure_isis_route_advertisement")
    assert 'PL-ISIS-EXPORT seq 5 permit 0.0.0.0/0' not in body
    assert 'PL-ISIS6-EXPORT seq 5 permit ::/0' not in body


def test_isis_configure_no_permit_20_catchall():
    body = _slice("configure_isis_route_advertisement")
    for name in ("RM-ISIS-EXPORT", "RM-ISIS6-EXPORT"):
        assert f'"route-map {name} permit 20"' not in body, (
            f"ISIS route-map {name} still emits permit 20 catch-all"
        )


def test_isis_configure_wipes_before_rebuild():
    body = _slice("configure_isis_route_advertisement")
    assert '"no ip prefix-list PL-ISIS-EXPORT"' in body
    assert '"no route-map RM-ISIS-EXPORT"' in body
    assert '"no ipv6 prefix-list PL-ISIS6-EXPORT"' in body
    assert '"no route-map RM-ISIS6-EXPORT"' in body


def test_isis_configure_still_has_permit_10_match():
    body = _slice("configure_isis_route_advertisement")
    assert '"route-map RM-ISIS-EXPORT permit 10"' in body
    assert '" match ip address prefix-list PL-ISIS-EXPORT"' in body
    assert '"route-map RM-ISIS6-EXPORT permit 10"' in body
    assert '" match ipv6 address prefix-list PL-ISIS6-EXPORT"' in body


# ─────────────────────────────────────────────────────────────────────
# BGP regression guard — v0.5.200 already fixed this for BGP;
# lock in that it didn't drift back.
# ─────────────────────────────────────────────────────────────────────

def test_bgp_configure_no_wildcard_prefix_list():
    body = _slice("configure_bgp_route_advertisement")
    assert 'PL-EXPORT seq 5 permit 0.0.0.0/0' not in body, (
        "BGP v4 prefix-list regressed with a seq-5 wildcard permit"
    )
    assert 'PL-EXPORT seq 5 permit ::/0' not in body, (
        "BGP v6 prefix-list regressed with a seq-5 wildcard permit"
    )


def test_bgp_configure_no_permit_20_catchall():
    body = _slice("configure_bgp_route_advertisement")
    for name in ("RM-EXPORT", "RM-EXPORT-IPV6"):
        assert f'"route-map {name} permit 20"' not in body, (
            f"BGP route-map {name} regressed with permit 20 catch-all"
        )
