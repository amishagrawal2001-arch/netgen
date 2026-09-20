"""v0.5.357 — hotfix: v6 `list(net.hosts())` explosion.

**Regression caught on srv06 v0.5.356 upgrade.** netgen-server
started, background monitor threads spun up, but Flask never bound
:5050. py-spy dump caught the main thread stuck at:

    hosts (ipaddress.py:2236)
    _add_first_host (utils/dhcp.py:2232)
    _collect_ipv6_anchor_candidates (utils/dhcp.py:2250)
    _scan_parent_nic_drift_v6 (utils/arp_monitor.py:1147)
    start (utils/arp_monitor.py:180)
    main (run_tgen_server.py:28581)

Root cause: `ipaddress.IPv6Network.hosts()` yields a generator over
every usable host in the network. For prefix ≤ 126 a `list(...)` on
that generator tries to materialize up to 2^N addresses. A /64
pool (device5's `2001:db8:30::100/64`) means 2^64 − 2 items — the
process wedges for practical eternity, and the arp_monitor `start()`
block never returns to `run_tgen_server.main` for Flask's
`app.run()`.

Three call sites had the same bug pattern; two were latent
(triggered only when the operator ran a specific action), one
guaranteed to fire on every startup:

1. `_collect_ipv6_anchor_candidates::_add_first_host` in
   `utils/dhcp.py` — added in v0.5.354, called from
   `_scan_parent_nic_drift_v6` on EVERY startup. **This is what
   hung srv06.**
2. `stop_dhcp_server`'s v6 anchor sweep in `utils/dhcp.py` — added
   in v0.5.351, called only when operator stops a v6 DHCP-server
   device. Latent.
3. `start_dhcp_server`'s v6 auto-derive in `utils/dhcp.py` — added
   in v0.5.230, extended in v0.5.337. Called on Apply only when
   operator LEFT `ipv6_server_ip` empty. Latent (device5 on srv06
   sets it explicitly, so it never fired).

Fix: replace `list(_net.hosts())` with direct arithmetic. For
prefix ≤ 126 the first usable host is `network_address + 1`
(matches what `hosts()` yields as its first item — the Subnet-
Router Anycast at `network_address` is excluded). Prefixes 127
and 128 use `network_address` directly per RFC 6164 / the
`hosts()` docstring.

Also: rollback path. srv06 was pinned back to v0.5.353 via
`netgen-upgrade /tmp/ostg_trafficgen-0.5.353-py3-none-any.whl`
before the fix landed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dhcp_src():
    return (_REPO / "utils" / "dhcp.py").read_text()


def test_marker_present():
    src = _dhcp_src()
    # Marker appears in all three fix sites.
    assert src.count("v0.5.357 (audit v6-hosts-generator-explosion)") >= 3


# --- Site 1: _collect_ipv6_anchor_candidates::_add_first_host ---


def test_site1_add_first_host_uses_direct_arithmetic():
    """The v0.5.354 collector must NOT call `list(_net.hosts())`
    on a v6 network. Structural check on LIVE CODE only (comment
    lines that mention the buggy form for context are excluded)."""
    src = _dhcp_src()
    idx = src.index("def _collect_ipv6_anchor_candidates(")
    body = src[idx:idx + 5000]
    _helper_idx = body.index("def _add_first_host(")
    _helper_end = body.index("\n    _v6_ip = ", _helper_idx)
    helper_body = body[_helper_idx:_helper_end]
    # Strip comment lines so my own historical-context comment
    # (which mentions `.hosts()` by name) doesn't false-positive.
    _code_only = "\n".join(
        _l for _l in helper_body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "list(" not in _code_only, (
        "live code in _add_first_host still contains a `list(...)` "
        "— the exploding form may have leaked back in"
    )
    assert ".hosts()" not in _code_only
    # And the direct arithmetic replacement IS present in live code.
    assert "network_address + 1" in _code_only
    # Special-case handling for prefix 127 / 128 (live code, not
    # just comment).
    assert "prefixlen >= 128" in _code_only
    assert "prefixlen == 127" in _code_only


# --- Site 2: stop_dhcp_server v6 anchor sweep ---


def test_site2_stop_server_sweep_uses_direct_arithmetic():
    """The v0.5.351 sweep candidate collector inside
    `stop_dhcp_server` had the same bug. Fix must be present.

    The marker `v0.5.351 (audit stop-server-v6-anchor-sweep-missing)`
    appears TWICE in dhcp.py — once on the helper docstring for
    `_remove_matching_ipv6_anchors`, once inside `stop_dhcp_server`.
    The buggy code is in the second occurrence. Locate it via the
    inline try-block signature."""
    src = _dhcp_src()
    # Find the second occurrence (in stop_dhcp_server itself).
    first = src.index("v0.5.351 (audit stop-server-v6-anchor-sweep-missing)")
    marker_idx = src.index(
        "v0.5.351 (audit stop-server-v6-anchor-sweep-missing)",
        first + 1,
    )
    body = src[marker_idx:marker_idx + 3500]
    # Strip comment lines so the fix's own historical-context
    # comment doesn't false-positive.
    _code_only = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "list(_net.hosts())" not in _code_only, (
        "live code in the v0.5.351 sweep still contains "
        "list(_net.hosts()) — a /64 pool hangs the process"
    )
    # The new direct-arithmetic form must be present in live code.
    assert "network_address + 1" in _code_only
    # And it references v0.5.357 in the fix comment so the marker
    # count assertion at the top of this file holds.
    assert "v0.5.357 (audit v6-hosts-generator-explosion)" in body


# --- Site 3: start_dhcp_server v6 auto-derive ---


def test_site3_start_server_auto_derive_uses_direct_arithmetic():
    """The v0.5.337 gateway-skip iterator over `_hosts6 = list(...)`
    was also bugged. Only fires when operator leaves
    ipv6_server_ip empty (latent on srv06 because device5 sets it
    explicitly), but fix now anyway."""
    src = _dhcp_src()
    idx = src.index("v0.5.337: iterate hosts and take the first")
    _win = src[max(0, idx - 2000):idx + 1000]
    # Strip comments so my own historical-context comment doesn't
    # false-positive.
    _code_only = "\n".join(
        line for line in _win.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "list(_v6_net.hosts())" not in _code_only
    assert "network_address + 1" in _code_only


# --- Runtime proof: the fixed collector actually returns fast ---


def test_runtime_collector_returns_fast_on_64_pool():
    """End-to-end regression proof: call the fixed collector with a
    /64 dhcp_config and assert it returns in well under a second.
    The bugged version would hang for practical eternity."""
    from utils.dhcp import _collect_ipv6_anchor_candidates
    _cfg = {
        "ipv6_pool_start": "2001:db8:30::100",
        "ipv6_prefix": "64",
        "ipv6_server_ip": "2001:db8:30::1",
        "ipv6_gateway": "2001:db8:30::1",
    }
    _t0 = time.monotonic()
    _out = _collect_ipv6_anchor_candidates(_cfg)
    _elapsed = time.monotonic() - _t0
    assert _elapsed < 0.5, (
        f"collector took {_elapsed:.2f}s on a /64 pool — the "
        f"`list(hosts())` explosion is back"
    )
    # And it derives the first host correctly.
    assert ("2001:db8:30::1", "64") in _out


def test_runtime_collector_handles_64_pool_without_explicit_server_ip():
    """Same runtime proof but the code path that used to hit
    `_add_first_host` — no explicit ipv6_server_ip, so the
    collector falls into the derivation branch."""
    from utils.dhcp import _collect_ipv6_anchor_candidates
    _cfg = {
        "ipv6_pool_start": "2001:db8:99::10",
        "ipv6_prefix": "64",
        # No ipv6_server_ip / ipv6_gateway on purpose — this drives
        # the collector's derivation branch.
    }
    _t0 = time.monotonic()
    _out = _collect_ipv6_anchor_candidates(_cfg)
    _elapsed = time.monotonic() - _t0
    assert _elapsed < 0.5
    # First host of `2001:db8:99::/64` is `::1`.
    assert ("2001:db8:99::1", "64") in _out


def test_runtime_collector_handles_128_pool():
    """Prefix-128 special case: the network address is the only
    valid host. Regression guard so the special-case branch does
    the right thing."""
    from utils.dhcp import _collect_ipv6_anchor_candidates
    _cfg = {
        "ipv6_pool_start": "2001:db8:30::abc",
        "ipv6_prefix": "128",
    }
    _out = _collect_ipv6_anchor_candidates(_cfg)
    assert ("2001:db8:30::abc", "128") in _out


def test_runtime_collector_handles_127_pool():
    """RFC 6164 point-to-point /127. Both endpoints usable —
    collector must return the network address (matches the v6
    hosts() docstring)."""
    from utils.dhcp import _collect_ipv6_anchor_candidates
    _cfg = {
        "ipv6_pool_start": "2001:db8::",
        "ipv6_prefix": "127",
    }
    _out = _collect_ipv6_anchor_candidates(_cfg)
    # network_address for `2001:db8::/127` is `2001:db8::` itself.
    assert ("2001:db8::", "127") in _out


# --- Regression guards on the sister v4 helper (never had the bug) ---


def test_v4_collector_untouched():
    """v0.5.239's `_collect_ipv4_anchor_candidates` was never
    affected (a /24 → 254 hosts materialize fine); guard against
    accidental "consistency" refactor that copies our v6 fix
    onto the v4 side."""
    src = _dhcp_src()
    idx = src.index("def _collect_ipv4_anchor_candidates(")
    body = src[idx:idx + 3000]
    # v4 side still uses hosts() — that's fine and expected.
    assert "hosts()" in body


# --- AST parse safety ---


def test_dhcp_ast_parses():
    import ast
    ast.parse(_dhcp_src())
