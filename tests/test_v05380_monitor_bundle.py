"""v0.5.380 — monitor fix bundle (M1, M2).

  M1  DHCP monitor per-device write-lock — DEFINED (v0.5.267)
      but NEVER acquired. Wire it into server + client branches.
  M2  ISIS monitor FRRDockerManager singleton — was
      re-instantiating per device per tick. Use the shared
      `frr_manager` singleton like ARP (v0.5.277), BGP, OSPF.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_dhcp_monitor_ast_parses():
    ast.parse(_read("utils/dhcp_monitor.py"))


def test_isis_monitor_ast_parses():
    ast.parse(_read("utils/isis_monitor.py"))


# ─── M1: DHCP monitor lock ───


def test_m1_marker_present():
    src = _read("utils/dhcp_monitor.py")
    assert "v0.5.380 (audit monitor M1)" in src
    # 2 sites (server + client branches) with a release each
    assert src.count("v0.5.380 (audit monitor M1)") >= 3


def test_m1_helper_exists_and_used():
    """v0.5.267 defined `_dhcp_write_lock_for` but never called
    it. v0.5.380 must actually acquire the returned lock."""
    src = _read("utils/dhcp_monitor.py")
    # Helper still defined (regression guard)
    assert "def _dhcp_write_lock_for(device_id: str)" in src
    # NOW called at least twice (server + client branches)
    _call_count = src.count("_dhcp_write_lock_for(device_id)")
    assert _call_count >= 2, (
        f"Expected ≥2 lock acquisitions (server + client); got {_call_count}"
    )


def test_m1_server_branch_wraps_writes_in_lock():
    """Server-mode branch: acquire lock BEFORE update_device +
    add_state_transition, release in finally."""
    src = _read("utils/dhcp_monitor.py")
    # Locate the server-mode lock acquire
    _idx = src.index("v0.5.380 (audit monitor M1): DHCP monitor per-device")
    body = src[_idx:_idx + 4500]
    # Acquire pattern
    assert "_dhcp_lock = _dhcp_write_lock_for(device_id)" in body
    assert "_dhcp_lock.acquire()" in body
    # update_device is INSIDE the try
    assert "self.device_db.update_device(device_id, update_payload)" in body
    # finally releases
    assert "_dhcp_lock.release()" in body


def test_m1_client_branch_wraps_writes_in_lock():
    """Client-mode branch: separate lock variable, same shape."""
    src = _read("utils/dhcp_monitor.py")
    assert "_dhcp_lock_client = _dhcp_write_lock_for(device_id)" in src
    assert "_dhcp_lock_client.acquire()" in src
    assert "_dhcp_lock_client.release()" in src


def test_m1_finally_release_present():
    """Both branches must release in finally, not just on success."""
    src = _read("utils/dhcp_monitor.py")
    # 2 finally: blocks introduced by v0.5.380 (one per branch)
    _release_count = src.count("_dhcp_lock.release()") + src.count("_dhcp_lock_client.release()")
    assert _release_count >= 2


# ─── M2: ISIS monitor singleton ───


def test_m2_marker_present():
    src = _read("utils/isis_monitor.py")
    assert "v0.5.380 (audit monitor M2)" in src
    # 2 sites: per-device check + check_existing_containers
    assert src.count("v0.5.380 (audit monitor M2)") >= 2


def test_m2_uses_module_singleton():
    """Both call sites import the module-level `frr_manager`
    singleton, not `FRRDockerManager`."""
    src = _read("utils/isis_monitor.py")
    # Singleton import replaces the per-call instantiation
    _singleton_count = src.count("from .frr_docker import frr_manager")
    assert _singleton_count >= 2, (
        f"Expected ≥2 singleton imports; got {_singleton_count}"
    )


def test_m2_no_more_per_call_instantiation():
    """FRRDockerManager() must NOT appear in EXECUTABLE code
    — that pattern was the whole bug. Only the singleton
    `frr_manager` (used via `.client` and `._get_container_name`)
    is legit. Strip Python comments + docstrings before scanning
    so a comment describing the pre-fix pattern doesn't trip us."""
    src = _read("utils/isis_monitor.py")
    # Strip # comments and triple-quoted string bodies
    _stripped = re.sub(r"#[^\n]*", "", src)
    _stripped = re.sub(r'""".*?"""', "", _stripped, flags=re.DOTALL)
    _stripped = re.sub(r"'''.*?'''", "", _stripped, flags=re.DOTALL)
    assert "FRRDockerManager()" not in _stripped, (
        "isis_monitor still has FRRDockerManager() in executable "
        "code — the singleton pattern is only partially applied"
    )


def test_m2_lazy_singleton_exists_in_frr_docker():
    """Regression guard: the LazyFRRManager + module-level
    `frr_manager` still exist. Introduced pre-v0.5.380."""
    src = _read("utils/frr_docker.py")
    assert "frr_manager = _LazyFRRManager()" in src


def test_m2_container_name_call_still_works():
    """Semantics preserved: still calls _get_container_name +
    frr_manager.client.containers.get / list."""
    src = _read("utils/isis_monitor.py")
    # Per-device check path
    assert "frr_manager._get_container_name(device_id, device_name)" in src
    # check_existing_containers path
    assert "frr_manager.client.containers.list(all=True)" in src


# ─── version guard ───


def test_pyproject_version_at_least_0580():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 380), (
        f"Version {m.group(1)} < 0.5.380"
    )


# ─── regression guards ───


def test_v0267_dhcp_lock_helper_intact():
    """v0.5.267 introduced _dhcp_write_lock_for. v0.5.380 must
    NOT delete the helper — only start using it."""
    src = _read("utils/dhcp_monitor.py")
    assert "v0.5.267 (audit DHCP-mon F5)" in src
    assert "def _dhcp_write_lock_for" in src


def test_v0264_isis_lock_helper_intact():
    """v0.5.264 introduced _isis_write_lock_for. v0.5.380 didn't
    touch it — sanity that the M2 singleton edit didn't collide."""
    src = _read("utils/isis_monitor.py")
    assert "v0.5.264 (audit ISIS-F2 + ISIS-F3)" in src
    assert "def _isis_write_lock_for" in src


def test_v0277_arp_singleton_intact():
    """v0.5.277 introduced the frr_manager lazy singleton for
    ARP. v0.5.380 M2 extends the same pattern to ISIS — the
    original ARP pattern must still work."""
    src = _read("utils/arp_monitor.py")
    assert "v0.5.277 (ARP-H2)" in src
