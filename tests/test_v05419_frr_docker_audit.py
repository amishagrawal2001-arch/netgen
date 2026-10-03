"""v0.5.419 — utils/frr_docker.py audit fixes (7 HIGH: GG2-GG8).

Follows up on v0.5.418 which fixed GG1 (shipping-breaker dead code).

  GG2  _strip_container_prefix handles both `ostg-frr-` AND `dhcp-frr-`.
  GG3  Shell-injection-prone verify/diagnostic block in
       _configure_interfaces deleted (6 bash-c sites that f-stringed
       unvalidated loopback IPs into a privileged container).
  GG4  _vtysh_output_has_error scan wired into all 4 legacy BGP
       wrappers (configure_bgp_neighbor, get_bgp_status,
       get_bgp_status_json, get_bgp_neighbors). T2/EE2/FF3 parity.
  GG5  Same error-marker scan wired into the 3 primary vtysh writes
       (_configure_interfaces, _configure_global_router_id, the
       manual frr.conf update + reload). T2/EE2/FF3 parity on the
       bring-up path.
  GG6  _exec_run_with_timeout threading wrapper + wiring across 10+
       exec_run sites (mgmtd checks, loopback ip addr, vtysh writes,
       BGP wrappers). Fixes FF13 parity (ISIS already had its own
       local wrapper; this one is shared across the whole module).
       Also closes GG12 (broken `timeout=10` kwarg at the loopback-
       cleanup site that docker-py silently discarded).
  GG7  Hardcoded `192.168.0.2` and `1.1.1.1` fallbacks removed from
       all 5 sites in start_frr_container / _configure_interfaces /
       _configure_global_router_id / configure_bgp_neighbor. Fail
       loud, or derive a unique per-device router-id, rather than
       silently sharing an IP across devices (OSPF duplicate-RID,
       BGP bad-identifier, ARP conflicts). FF5/EE5/EE6 parity.
  GG8  _ensure_client now called from all 4 legacy BGP module-level
       wrappers so a dockerd restart doesn't leave them APIError-ing
       forever. FRR-B4 parity completion.
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


def _stub_docker():
    if "docker" in sys.modules and hasattr(sys.modules["docker"], "_v05418_stub"):
        return
    _d = types.ModuleType("docker")
    _e = types.ModuleType("docker.errors")

    class _NF(Exception):
        pass

    class _AE(Exception):
        pass

    _e.NotFound = _NF
    _e.APIError = _AE
    _d.errors = _e
    _d.from_env = lambda: types.SimpleNamespace(
        ping=lambda: None,
        containers=types.SimpleNamespace(
            get=lambda n: (_ for _ in ()).throw(_NF()),
        ),
        images=types.SimpleNamespace(get=lambda n: None),
    )
    _d._v05418_stub = True
    sys.modules["docker"] = _d
    sys.modules["docker.errors"] = _e


# ─── AST sanity ───


def test_frr_docker_ast_parses():
    ast.parse(_read("utils/frr_docker.py"))


# ─── GG2: prefix strip helper ───


def test_gg2_marker_present():
    assert "v0.5.419 (audit stream-GG2)" in _read("utils/frr_docker.py")


def test_gg2_helper_handles_both_prefixes():
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import _strip_container_prefix
    assert _strip_container_prefix("ostg-frr-abc12345") == "abc12345"
    assert _strip_container_prefix("dhcp-frr-abc12345") == "abc12345"
    # Unknown prefix: pass-through for stability.
    assert _strip_container_prefix("some-other-abc") == "some-other-abc"
    assert _strip_container_prefix("") == ""


def test_gg2_configure_bgp_uses_the_helper():
    src = _read("utils/frr_docker.py")
    _idx = src.index("def configure_bgp_neighbor")
    body = src[_idx:_idx + 3000]
    assert "_strip_container_prefix(container_name)" in body
    # The old no-op prefix strip must be gone.
    assert 'container_name.replace(f"{frr_manager.container_prefix}-"' not in body


# ─── GG3: shell-injection-prone verify block gone ───


def test_gg3_marker_present():
    assert "v0.5.419 (audit stream-GG3)" in _read("utils/frr_docker.py")


def test_gg3_verify_grep_block_deleted():
    """The pre-fix block had `grep -q '{loopback_ipv4}/32'` inside
    `bash -c` with f-string interpolation of unvalidated input. The
    whole block was removed."""
    src = _read("utils/frr_docker.py")
    assert "'Loopback IPv4 {loopback_ipv4}/32 is configured'" not in src
    assert "'Loopback IPv6 {loopback_ipv6}/128 is configured'" not in src
    assert "grep -A 5 'interface lo'" not in src


# ─── GG4 + GG5: _vtysh_output_has_error scanner ───


def test_gg4_gg5_markers_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.419 (audit stream-GG4" in src
    assert "v0.5.419 (audit stream-GG5" in src


def test_gg4_gg5_scanner_helper_defined():
    src = _read("utils/frr_docker.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
    assert "_VTYSH_ERROR_MARKERS" in src
    assert '"% Unknown command"' in src
    assert '"% Malformed"' in src


def test_gg4_gg5_scanner_wired_into_many_sites():
    """7 vtysh exec paths need the scan: 3 in frr_docker's own
    writes (GG5) + 4 in legacy BGP wrappers (GG4). Allow slack."""
    src = _read("utils/frr_docker.py")
    assert src.count("_vtysh_output_has_error(") >= 7


def test_gg4_gg5_scanner_at_runtime():
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import _vtysh_output_has_error
    assert _vtysh_output_has_error("") is False
    assert _vtysh_output_has_error("ok\nall good") is False
    assert _vtysh_output_has_error("line1\n% Unknown command xyz\n") is True
    # Comment-prefixed `#` and non-marker `%` should not trip.
    assert _vtysh_output_has_error("# ok\n%% ignore") is False


# ─── GG6: exec_run_with_timeout wrapper ───


def test_gg6_marker_present():
    assert "v0.5.419 (audit stream-GG6" in _read("utils/frr_docker.py")


def test_gg6_wrapper_helper_defined():
    src = _read("utils/frr_docker.py")
    assert "def _exec_run_with_timeout(container, cmd, timeout_sec" in src
    assert "import threading" in src


def test_gg6_wrapper_timeout_behaviour():
    """The wrapper returns None when the exec blows past the timeout."""
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import _exec_run_with_timeout
    import time as _time

    class _SlowContainer:
        def exec_run(self, _cmd):
            _time.sleep(5)
            return "nope"

    _t0 = _time.monotonic()
    _r = _exec_run_with_timeout(_SlowContainer(), ["ignored"], timeout_sec=0.2)
    _elapsed = _time.monotonic() - _t0
    assert _r is None, f"expected None on timeout, got {_r!r}"
    assert _elapsed < 1.5, f"wrapper returned after {_elapsed:.2f}s; should be ~0.2s"


def test_gg6_wrapper_passes_through_result():
    _stub_docker()
    sys.modules.pop("utils.frr_docker", None)
    from utils.frr_docker import _exec_run_with_timeout

    class _FastContainer:
        def exec_run(self, _cmd):
            return types.SimpleNamespace(exit_code=0, output=b"hi")

    _r = _exec_run_with_timeout(_FastContainer(), ["ignored"], timeout_sec=5.0)
    assert _r is not None
    assert _r.exit_code == 0
    assert _r.output == b"hi"


def test_gg6_wrapper_wired_into_many_sites():
    """At least 10 sites in the module should go through the timeout
    wrapper now (mgmtd checks + ip addr sites + vtysh writes + BGP
    wrappers)."""
    src = _read("utils/frr_docker.py")
    assert src.count("_exec_run_with_timeout(container,") >= 10


def test_gg6_gg12_broken_timeout_kwarg_fixed():
    """The pre-fix passed `timeout=10` to `container.exec_run` which
    docker-py doesn't accept — the kwarg was silently discarded. The
    fix uses the threading wrapper instead."""
    src = _read("utils/frr_docker.py")
    # The old call shape with timeout=10 should be gone.
    assert "container.exec_run([\"bash\", \"-c\", exec_cmd], timeout=10)" not in src


# ─── GG7: hardcoded 192.168.0.2 / 1.1.1.1 fallbacks gone ───


def test_gg7_marker_present():
    assert "v0.5.419 (audit stream-GG7)" in _read("utils/frr_docker.py")


def test_gg7_no_more_hardcoded_192_168_0_2_assignments():
    """Comments explaining the pre-fix literal are fine; the risk
    was live assignments like `ipv4_addr = '192.168.0.2'` and
    `router_id = \"192.168.0.2\"`."""
    src = _strip_comments(_read("utils/frr_docker.py"))
    assert "ipv4_addr = '192.168.0.2'" not in src
    assert 'router_id = "192.168.0.2"' not in src


def test_gg7_no_more_hardcoded_1_1_1_1_loopback_assignment():
    src = _strip_comments(_read("utils/frr_docker.py"))
    assert "loopback_ipv4 = '1.1.1.1'" not in src


def test_gg7_derive_router_id_still_available_as_fallback_source():
    """GG7 replaces the shared `192.168.0.2` with a per-device
    derivation. The helper that does the derivation must still
    exist."""
    src = _read("utils/frr_docker.py")
    assert "_derive_router_id_from_device_id" in src


# ─── GG8: _ensure_client wired into legacy BGP wrappers ───


def test_gg8_marker_present():
    assert "v0.5.419 (audit stream-GG8)" in _read("utils/frr_docker.py")


def test_gg8_ensure_client_in_all_four_bgp_wrappers():
    """Each of the 4 legacy BGP wrappers must call
    frr_manager._ensure_client() before touching the client."""
    src = _read("utils/frr_docker.py")
    assert src.count("frr_manager._ensure_client()") >= 4


# ─── v0.5.418 regression — GG1 fix must stay intact ───


def test_v0418_gg1_init_still_assigns_all_five_attrs():
    _stub_docker()
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
        assert hasattr(m, _attr), f"v0.5.418 GG1 fix regressed: {_attr} missing"


# ─── version guard ───


def test_pyproject_at_least_0619():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 419)


# ─── regression guards for prior audits ───


def test_v0417_isis_ff3_scanner_intact():
    src = _read("utils/isis.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src


def test_v0416_ospf_ee2_scanner_intact():
    src = _read("utils/ospf.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src


def test_v0403_bgp_t2_scanner_intact():
    src = _read("utils/bgp.py")
    assert "def _vtysh_output_has_error(output: str) -> bool:" in src
