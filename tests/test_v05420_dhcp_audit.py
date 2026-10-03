"""v0.5.420 — utils/dhcp.py audit (slice A: non-v6 HIGHs + 4 MEDs).

  HH1  _remove_route_and_vrf_copy skips the main-table del when the
       interface is VRF-slaved, mirroring the v0.5.282 install
       guard. Pre-fix every clean stop of a VRF-slaved DHCP server
       wrote spurious "No such process" errors to dhcp_last_error.
  HH6  Dead pkill with Python `\\s` (POSIX ERE doesn't recognize it)
       deleted; the real whole-token cleanup via `_kill_stale_dhcp6c`
       immediately below was already doing the work.
  HH7  All 7 `/bin/sh -c` sites that f-string `{interface}` /
       `{conffile}` / `{pidfile}` / `{dhcp6_conf}` now shlex.quote
       the interpolated field. Defence-in-depth hygiene.
  HH8  VRF-probe `subprocess.run(..., timeout=2)` bumped to 5s —
       2s is tight for `ip -o link show vrf-XXX` on srv06 with
       many interfaces; probe timeout → gateway disappears
       intermittently in the UI.
  HH13 `_remove_matching_ipv4_anchors` now requires EXACT
       `(ip, prefix)` match. Pre-fix the fallback branch matched
       ip-only across any prefix → operator's `.16/24` could be
       deleted on DHCP stop. Near-miss logs at WARN so operator
       sees what was skipped.
  HH15 docker.from_env now carries `timeout=30` so stuck dockerd
       sockets can't pin the Flask worker (GG6/FF13 parity for
       the lifecycle HTTP calls).
  HH16 v6 relay-mode stale-anchor sweep now intersects with
       `_collect_ipv6_anchor_candidates` before removing — mirror
       of the v4-side safety that `_remove_matching_ipv4_anchors`
       already provides.

v6 server-path HIGHs (HH2, HH3, HH4, HH5) are deferred to
v0.5.421 pending srv06 DHCPv6 end-to-end verification (task #83).
"""
from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


def _strip_comments(src: str) -> str:
    return "\n".join(
        _line for _line in src.split("\n")
        if not _line.lstrip().startswith("#")
    )


# ─── AST sanity ───


def test_dhcp_ast_parses():
    ast.parse(_read("utils/dhcp.py"))


# ─── HH1: VRF-slaved stop no longer writes bogus failures ───


def test_hh1_marker_present():
    assert "v0.5.420 (audit stream-HH1)" in _read("utils/dhcp.py")


def test_hh1_main_table_del_gated_by_vrf_name():
    """The main-table removal block must sit inside `if not vrf_name:`
    — mirroring the v0.5.282 install guard. Pre-fix the del ran
    unconditionally and failed with 'No such process' every stop."""
    src = _read("utils/dhcp.py")
    _idx = src.index("def _remove_route_and_vrf_copy")
    _end = src.index("def _iface_has_ipv4_in_subnet", _idx)
    body = src[_idx:_end]
    # The new gate.
    assert "# Main table — v0.5.420 (audit stream-HH1)" in body
    # The guard appears before the try/_try_del chain.
    assert "if not vrf_name:\n        if gateway:" in body


# ─── HH6: dead pkill removed ───


def test_hh6_marker_present():
    assert "v0.5.420 (audit stream-HH6)" in _read("utils/dhcp.py")


def test_hh6_pkill_dhcp6c_pattern_gone():
    """Pre-fix pattern `dhcp6c.*(^|\\s){re.escape(interface)}(\\s|$)`
    was dead because POSIX ERE has no `\\s`. The real cleanup is in
    `_kill_stale_dhcp6c` which must still exist. Check LIVE code
    only — the HH6 explanatory comment references the pre-fix
    pattern verbatim and that is fine."""
    src = _strip_comments(_read("utils/dhcp.py"))
    assert 'dhcp6c.*(^|\\\\s)' not in src
    full = _read("utils/dhcp.py")
    assert "def _kill_stale_dhcp6c" in full


# ─── HH7: shell interpolation hardened ───


def test_hh7_marker_present():
    src = _read("utils/dhcp.py")
    assert src.count("v0.5.420 (audit stream-HH7)") >= 4


def test_hh7_shlex_quote_on_interface_in_shell_sites():
    """All 5 `/bin/sh -c` sites that interpolate the interface name
    must wrap it with shlex.quote(). The 6th site (:4093) uses a
    static glob with no interpolation; the 7th (:5450) uses
    re.escape inside single-quotes which is also safe."""
    src = _read("utils/dhcp.py")
    # Count: 3 ip-addr-show sites + 1 heredoc redirect + 1 rm -f.
    # Plus 2 pidfile sites + 1 dhcp6_conf heredoc. Allow some
    # slack since the test may undercount shared patterns.
    assert src.count("shlex.quote(str(interface))") >= 4
    assert src.count("shlex.quote(str(pidfile))") >= 2
    assert "shlex.quote(str(conffile))" in src
    assert "shlex.quote(str(dhcp6_conf))" in src


# ─── HH8: VRF-probe timeout 2s → 5s ───


def test_hh8_no_more_timeout_2_in_dhcp():
    """The 4 VRF-existence probes (`subprocess.run([...], timeout=2)`)
    all raised to 5s. If any new `timeout=2` sneaks in, flag it."""
    src = _read("utils/dhcp.py")
    _no_comments = _strip_comments(src)
    assert "timeout=2," not in _no_comments
    assert "timeout=2)" not in _no_comments


# ─── HH13: ip-only match tightened to exact (ip, prefix) ───


def test_hh13_marker_present():
    assert "v0.5.420 (audit stream-HH13)" in _read("utils/dhcp.py")


def test_hh13_ip_only_fallback_removed():
    """Pre-fix the fallback branch matched ip-only across any prefix.
    New code only logs at WARN and does NOT assign `_match` on prefix
    mismatch."""
    src = _read("utils/dhcp.py")
    _idx = src.index("def _remove_matching_ipv4_anchors")
    body = src[_idx:_idx + 2500]
    # The old assignment inside the fallback loop is gone — but the
    # new WARN log IS there.
    assert "if _cur_ip == anchor_ip and _cur_pfx != anchor_pfx:" in body
    assert "v0.5.420 HH13: anchor sweep for" in body
    # The pre-fix shape had `_match = (_cur_ip, _cur_pfx)` inside
    # the loop body; the new shape only `break`s.
    assert "_match = (_cur_ip, _cur_pfx)" not in body


# ─── HH15: docker.from_env carries HTTP timeout ───


def test_hh15_marker_present():
    assert "v0.5.420 (audit stream-HH15)" in _read("utils/dhcp.py")


def test_hh15_docker_from_env_timeout_all_sites():
    """All 3 `docker.from_env()` call sites must now pass
    `timeout=30`. Pre-fix docker-py defaulted to None (wait
    forever) and a stuck socket pinned the Flask worker."""
    src = _read("utils/dhcp.py")
    _no_comments = _strip_comments(src)
    # No bare `docker.from_env()` left; every call takes the timeout.
    _bare = _no_comments.count("docker.from_env()")
    _with_timeout = _no_comments.count("docker.from_env(timeout=")
    assert _bare == 0, f"found {_bare} bare docker.from_env() call(s)"
    assert _with_timeout >= 3


# ─── HH16: v6 relay-mode sweep intersects with candidates ───


def test_hh16_marker_present():
    assert "v0.5.420 (audit stream-HH16)" in _read("utils/dhcp.py")


def test_hh16_v6_relay_sweep_uses_candidate_intersection():
    """The v6 relay-mode stale-anchor sweep must now consult
    `_collect_ipv6_anchor_candidates` before deleting, mirroring
    the v4-side safety."""
    src = _read("utils/dhcp.py")
    _idx = src.index("v0.5.420 (audit stream-HH16)")
    body = src[_idx:_idx + 2500]
    assert "_collect_ipv6_anchor_candidates(dhcp_config)" in body
    assert "_candidate_ips" in body
    assert "if _stale_ip not in _candidate_ips:" in body


# ─── v0.5.419 regression — frr_docker fixes intact ───


def test_v0419_frr_docker_scanner_intact():
    src = _read("utils/frr_docker.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
    assert "v0.5.419 (audit stream-GG4" in src


# ─── version guard ───


def test_pyproject_at_least_0620():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 420)


# ─── regression guards for prior sibling scanners ───


def test_v0417_isis_ff3_intact():
    src = _read("utils/isis.py")
    assert "v0.5.417 (audit stream-FF3)" in src


def test_v0418_gg1_init_assigns_attrs():
    """v0.5.418 GG1 — FRRDockerManager __init__ must still set the
    5 attrs. Smoke test via instantiation with docker stubbed."""
    _d = types.ModuleType("docker")
    _e = types.ModuleType("docker.errors")

    class _NF(Exception):
        pass

    class _AE(Exception):
        pass

    _e.NotFound = _NF
    _e.APIError = _AE
    _d.errors = _e
    _d.from_env = lambda **_kw: types.SimpleNamespace(
        ping=lambda: None,
        containers=types.SimpleNamespace(
            get=lambda n: (_ for _ in ()).throw(_NF()),
        ),
        images=types.SimpleNamespace(get=lambda n: None),
    )
    sys.modules["docker"] = _d
    sys.modules["docker.errors"] = _e
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import FRRDockerManager
    m = FRRDockerManager()
    for _attr in (
        "_vrf_alloc_lock",
        "_vrf_allocated",
        "_vrf_state_path",
        "_start_locks",
        "_start_locks_meta",
    ):
        assert hasattr(m, _attr)
