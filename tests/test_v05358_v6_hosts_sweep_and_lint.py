"""v0.5.358 — post-v0.5.357 sweep + lint guard for
`list(network.hosts())` on v6 networks.

The v0.5.357 hotfix taught us that any `list(network.hosts())` call
site that can receive an `IPv6Network` is a latent process hang
(2^N materialization on prefix ≤ 126). Grep across the codebase
after that ship found MORE sites:

- `run_tgen_server.py::generate_host_routes_from_pool` — v0.5.358 A.
  Called from BGP route-pool generation with an operator-supplied
  subnet that `ipaddress.ip_network(subnet)` auto-detects as v4 OR
  v6. A v6 pool with e.g. `2001:db8::/64` hangs the request thread.

- `run_tgen_server.py` AI-discovery scan at :29132 — v0.5.358 B. The
  pre-fix `prefixlen < 24` size guard was v4-shaped; a v6 subnet
  passed it trivially and reached `list(hosts())[:10]` which
  materializes the full generator BEFORE slicing.

- `add_bgp_route_dialog_updated.py` — buggy pattern in a dead legacy
  dialog file (not imported anywhere). Flagged by the lint test
  below for later cleanup, but left in place to keep this ship
  narrow.

Sites that were verified safe (v4-only, or v4-gated):
- `utils/dhcp.py::_derive_server_ip_from_pool` (v4-only)
- `utils/dhcp.py::_collect_ipv4_anchor_candidates` (v4-only)
- `widgets/add_bgp_route_dialog.py::generate_host_ips` (v4-gated)
- `widgets/add_bgp_route_dialog.py::generate_all_host_routes` (v4-gated)

Fixes use `itertools.islice(network.hosts(), N)` so at most N items
materialize from the generator regardless of subnet size. This
preserves the v0.5.357 arithmetic-first pattern but adapts it to
call sites that WANT `count` hosts (not just the first).

Also adds a lint regression test: any new occurrence of
`list(...hosts())` in production code that isn't in this test's
allowlist fails the suite. That's the "would-have-caught-it"
guardrail the v0.5.357 post-mortem called for.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel):
    return (_REPO / rel).read_text()


def test_all_v0_5_358_markers_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.358 (audit v6-hosts-generator-explosion, sweep)" in src


# --- A. generate_host_routes_from_pool ---


def _fn_call_snippets(fn_name):
    """AST-walk `fn_name` in run_tgen_server.py and return every
    `Call` expression as a source snippet. Docstrings and comments
    are automatically excluded — only real calls appear."""
    import ast
    src = _read("run_tgen_server.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            _snips = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    try:
                        _snips.append(ast.unparse(sub))
                    except Exception:
                        pass
            return _snips
    raise AssertionError(f"function {fn_name!r} not found")


def test_A_generate_host_routes_uses_islice_not_list_hosts():
    """The pre-fix `list(network.hosts())` unbounded call is gone;
    replaced with `itertools.islice(network.hosts(), count)` so at
    most `count` addresses materialize regardless of prefix size.

    Uses AST so the fix's own docstring (which mentions the bugged
    form by name) doesn't false-positive."""
    _calls = _fn_call_snippets("generate_host_routes_from_pool")
    _bugged = [c for c in _calls if c == "list(network.hosts())"]
    assert not _bugged, (
        f"generate_host_routes_from_pool still contains the "
        f"exploding `list(network.hosts())` call as live code: "
        f"{_bugged}"
    )
    assert any("itertools.islice(network.hosts()" in c for c in _calls), (
        "the islice-bounded replacement is missing"
    )


def test_A_generate_host_routes_v6_branch_no_longer_materializes_full_network():
    """The pre-fix `hosts = list(network)` v6 branch was even worse
    than `hosts()` — it iterates every address including the network
    base. Fix removes the branch entirely (the `hosts()` generator
    already excludes Subnet-Router Anycast)."""
    _calls = _fn_call_snippets("generate_host_routes_from_pool")
    # No bare `list(network)` call — only `list(itertools.islice(...))`
    # or `list(<other>)`.
    assert "list(network)" not in _calls, (
        "generate_host_routes_from_pool still calls `list(network)` "
        "which iterates every address in an IPv6 network"
    )


def test_A_generate_host_routes_still_returns_128_for_v6():
    """Regression guard: v6 branch must still emit `/128` suffix
    per-host. Structural check on the return arm."""
    src = _read("run_tgen_server.py")
    fn_idx = src.index("def generate_host_routes_from_pool(")
    body = src[fn_idx:fn_idx + 2500]
    assert '"{host}/128"' in body or '{host}/128' in body


# --- Runtime proof: generator-based fix returns fast even on /64 ---


def _extract_generate_host_routes():
    """Load `generate_host_routes_from_pool` in isolation. Importing
    `run_tgen_server` triggers module-level side effects (touches
    `/opt/netgen`) that fail in test envs without those paths. Read
    the function body and compile it into a fresh throwaway module
    so we can call it in-process without the surrounding startup."""
    import types, logging as _lg, itertools
    _src = _read("run_tgen_server.py")
    _fn_idx = _src.index("def generate_host_routes_from_pool(")
    _fn_end = _src.index("\ndef ", _fn_idx + 1)
    _fn_src = _src[_fn_idx:_fn_end]
    _mod = types.ModuleType("_v0_5_358_runtime_probe")
    _mod.__dict__["logging"] = _lg
    _mod.__dict__["itertools"] = itertools
    exec(compile(_fn_src, "<v0.5.358 probe>", "exec"), _mod.__dict__)
    return _mod.generate_host_routes_from_pool


def test_A_runtime_bounded_on_v6_64():
    """End-to-end proof: pass a /64 IPv6 network and confirm the
    function returns in well under a second with the requested
    number of /128 routes. The pre-fix version hung eternity."""
    import ipaddress
    _fn = _extract_generate_host_routes()
    net = ipaddress.ip_network("2001:db8:99::/64", strict=False)
    _t0 = time.monotonic()
    out = _fn(net, 5)
    _elapsed = time.monotonic() - _t0
    assert _elapsed < 0.5, (
        f"generate_host_routes_from_pool took {_elapsed:.2f}s "
        f"on a /64 — the list(hosts()) explosion is back"
    )
    assert len(out) == 5
    # First route is 2001:db8:99::1/128 (network_address + 1 — the
    # Subnet-Router Anycast at network_address is skipped by
    # ipaddress.IPv6Network.hosts()).
    assert out[0] == "2001:db8:99::1/128"
    assert all(r.endswith("/128") for r in out)


def test_A_runtime_still_works_on_v4():
    """Regression guard: the v4 path (a /24) must still work."""
    import ipaddress
    _fn = _extract_generate_host_routes()
    net = ipaddress.ip_network("192.168.42.0/24", strict=False)
    out = _fn(net, 3)
    assert len(out) == 3
    assert out == ["192.168.42.1/32", "192.168.42.2/32", "192.168.42.3/32"]


# --- B. AI-discovery scan v6 guard ---


def test_B_ai_discovery_guards_v6_size():
    """The pre-fix `prefixlen < 24` size guard was v4-shaped and let
    every v6 subnet through. Now: gate v6 subnets on `prefixlen <
    120` (limit 256 addresses max, matches the v4-side /24)."""
    src = _read("run_tgen_server.py")
    # Anchor on the demo route so we don't misfire on unrelated
    # `prefixlen < 24` occurrences elsewhere.
    marker_idx = src.index("Subnet too large. Please use /24 or smaller.")
    body = src[max(0, marker_idx - 500):marker_idx + 2000]
    # v4 branch still there.
    assert "network.version == 4 and network.prefixlen < 24" in body
    # v6 branch present with a sensible upper cap.
    assert "network.version == 6 and network.prefixlen < 120" in body


def test_B_ai_discovery_uses_islice_not_list_hosts():
    """The scan itself must use islice(10) — pre-fix `list(hosts())
    [:10]` materializes the full generator before slicing."""
    src = _read("run_tgen_server.py")
    marker_idx = src.index("Scan first 10 hosts")
    body = src[marker_idx:marker_idx + 800]
    _code_only = "\n".join(
        _l for _l in body.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert "islice(network.hosts(), 10)" in _code_only
    assert "list(network.hosts())[:10]" not in _code_only


# --- Repo-wide lint: no NEW list(hosts()) call sites may appear ---


# Anything on this list is either (a) fixed via islice/arithmetic,
# (b) v4-only (safe — /24 → 254 hosts, no explosion), or (c) test-
# scaffolding that intentionally references the pattern. Add to this
# list ONLY after confirming the site is v4-gated OR bounded.
_KNOWN_SAFE_LIVE_SITES = {
    # v4-only: derived from ipaddress.IPv4Network.
    ("utils/dhcp.py", "hosts_iter = list(pool_network.hosts())"),
    # v4-only: _collect_ipv4_anchor_candidates::_add_from_network.
    ("utils/dhcp.py", "hosts = list(net.hosts())"),
    # v4-gated by `if network.version == 4:` immediately above.
    ("widgets/add_bgp_route_dialog.py", "hosts = list(network.hosts())"),
    # ↑ appears twice (generate_host_ips + generate_all_host_routes).
}

# Files we know are dead (not imported by anything). Left for
# separate cleanup ship — the lint below flags them but ignores
# them here so this test can be green while the cleanup lands.
# v0.5.364: `add_bgp_route_dialog_updated.py` was deleted (the
# audit's "cleanup ship" — see git log). The set is empty for
# now; keep the mechanism so a future dead file can be flagged
# without hunting for the pattern again.
_KNOWN_DEAD_FILES: set = set()


def _iter_production_py_files():
    """Yield every .py file under the repo root that isn't test,
    build, venv, or in-tree cache."""
    _root = _REPO
    for path in _root.rglob("*.py"):
        _rel_str = str(path.relative_to(_root))
        if _rel_str.startswith((
            "tests/",
            "venv/",
            "build/",
            "build_env/",
            ".claude/",
            "dist/",
        )):
            continue
        # Third-party subtrees shipped in-tree.
        if "/site-packages/" in _rel_str:
            continue
        yield path


def test_lint_no_unbounded_list_hosts_calls():
    """Repo-wide guard against the class of bug that hung srv06 on
    v0.5.356. Any `list(<expr>.hosts())` call site in production
    code must be either v4-only (a /24 → 254 hosts materializes
    fine) OR bounded via `itertools.islice`. Anything else is a
    latent process hang the moment a v6 network reaches it.

    Uses AST (not regex) so docstrings, comments, and string
    literals that MENTION the pattern don't false-positive — only
    real call expressions do.

    Test allowlist (`_KNOWN_SAFE_LIVE_SITES`) enumerates the exact
    matches that are verified safe. Any new occurrence fails —
    move the fix to islice or add the site to the allowlist with a
    justifying comment BEFORE it ships.

    Dead files (`_KNOWN_DEAD_FILES`) are noted separately."""
    import ast
    _offenders = []
    for path in _iter_production_py_files():
        _rel = str(path.relative_to(_REPO))
        if _rel in _KNOWN_DEAD_FILES:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match `list(...)` where the sole arg is a call to
            # something whose function is an Attribute named
            # 'hosts' (i.e. `foo.hosts()`).
            if not (isinstance(node.func, ast.Name) and node.func.id == "list"):
                continue
            if len(node.args) != 1:
                continue
            _arg = node.args[0]
            if not isinstance(_arg, ast.Call):
                continue
            if not (isinstance(_arg.func, ast.Attribute)
                    and _arg.func.attr == "hosts"):
                continue
            # Reconstruct the source snippet for the allowlist key.
            try:
                _snippet = ast.unparse(node)
            except Exception:
                _snippet = f"list(...hosts())"
            _line = node.lineno
            _matched = False
            for _rel_ok, _needle in _KNOWN_SAFE_LIVE_SITES:
                if _rel != _rel_ok:
                    continue
                # The needle is a suffix/subsnippet of the actual
                # source line — check via ast.unparse for stability.
                _line_src = text.splitlines()[_line - 1]
                if _needle in _line_src:
                    _matched = True
                    break
            if not _matched:
                _offenders.append(f"{_rel}:{_line}: {_snippet[:120]}")
    assert not _offenders, (
        "list(...hosts()) call sites found in production code that "
        "are not in _KNOWN_SAFE_LIVE_SITES:\n\n"
        + "\n".join(_offenders)
        + "\n\nEither replace with itertools.islice(network.hosts(), "
          "N) or add to _KNOWN_SAFE_LIVE_SITES with proof the site "
          "is v4-only."
    )


def test_lint_dead_file_still_carries_bug_awaiting_cleanup():
    """`add_bgp_route_dialog_updated.py` is a legacy dialog file
    (not imported anywhere) that still carries the buggy pattern.
    Regression guard so IF someone re-imports it, this test alerts
    them that they've just re-introduced a hang."""
    _dead = _REPO / "add_bgp_route_dialog_updated.py"
    if not _dead.exists():
        return  # File was cleaned up — good.
    src = _dead.read_text()
    # If the file is still present it still has the bug; that's OK
    # because nothing imports it. Verify the "not imported" claim.
    for path in _iter_production_py_files():
        _rel = str(path.relative_to(_REPO))
        if _rel == "add_bgp_route_dialog_updated.py":
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, PermissionError):
            continue
        assert "add_bgp_route_dialog_updated" not in text, (
            f"{_rel} imports the dead file "
            "`add_bgp_route_dialog_updated` — that file still "
            "carries a v6 `list(hosts())` hang. Either fix the "
            "dead file before importing it or use "
            "`widgets/add_bgp_route_dialog` (v4-gated) instead."
        )


# --- Regression guards ---


def test_v0_5_357_hotfix_still_intact():
    """v0.5.358 builds on v0.5.357's fix — regression guard that
    the hotfix markers didn't get reverted while this sweep was
    landing."""
    src = _read("utils/dhcp.py")
    assert src.count("v0.5.357 (audit v6-hosts-generator-explosion)") >= 3


def test_ast_parses():
    import ast
    ast.parse(_read("run_tgen_server.py"))
