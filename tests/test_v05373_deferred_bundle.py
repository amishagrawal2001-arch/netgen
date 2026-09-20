"""v0.5.373 — 5-fix bundle from v0.5.372's deferred queue.

Continues clearing the client + protocol audit backlog. The
bigger DHCP-restart-async QThread refactor is still deferred to
v0.5.374 (needs UI-plumbing design), but the five here are all
in scope for a single ship.

Fixes:
    D1 HIGH — utils/frr_docker.py VRF routing-table id collision.
        Pre-fix `1000 + md5(device_id) % 1000` → birthday paradox
        at ~37 devices. Now: hash-derived initial pick + linear-
        probe over widened 1000..3999 range, backed by a JSON
        state file so netgen-server restart doesn't churn ids
        (kernel VRF state would mismatch a re-derivation).
        `_release_vrf_table(device_id)` freed on device removal.
    D2 — utils/vxlan.py interface-name collision + silent
        truncation. Pre-fix `vx{vni}-{seed[:6]}` then truncated
        to 15 chars — two adjacent UUIDs = same iface. New
        `_pick_unique_vxlan_iface` checks _interface_exists at
        each candidate + extends seed on collision + numeric-
        suffix last-resort. Called from both container + host
        legacy paths.
    D4 — widgets/rfc2544_dialog.py poll wedge. Pre-fix a broad
        `except Exception: return` swallowed every failure → 2s
        timer kept firing forever on server crash → UI stuck at
        "Test running…". Now tracks consecutive failures; after
        5 (10s) stops timer, re-enables Start/Stop, surfaces
        status label + toast.
    D5 — run_tgen_client.py per-server auth-token routing
        completing v0.5.372 C1. New `_NETGEN_HOST_TOKENS` map
        populated from Add Server's `auth_token`. Wrapper lookup
        order: per-host → env-var → none. Multi-server labs with
        distinct bearers per server now route correctly.
    D6 — query_device_database.py 9 `requests.get` calls gain
        `timeout=_GET_TIMEOUT = (5, 30)`. Consistent with v0.5.361
        capture_client pattern; pre-fix an unreachable server
        hung the CLI forever.

All sites carry marker `v0.5.373 (audit …)`.
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


def test_all_files_ast_parse():
    for _f in ("run_tgen_client.py",
               "traffic_client/menu_actions.py",
               "utils/frr_docker.py",
               "utils/vxlan.py",
               "widgets/rfc2544_dialog.py",
               "query_device_database.py"):
        ast.parse(_read(_f))


# ─── D1: VRF table id allocator ───


def test_d1_marker_present():
    src = _read("utils/frr_docker.py")
    assert "v0.5.373 (audit vrf-table-id-collision)" in src


def test_d1_allocator_dict_exists():
    src = _read("utils/frr_docker.py")
    assert "self._vrf_allocated: Dict[str, int] = {}" in src
    assert "self._vrf_alloc_lock" in src


def test_d1_wider_range():
    """Range must widen from 1000-slot (pre-fix) to at least
    3000-slot so 100-device labs have real headroom."""
    src = _read("utils/frr_docker.py")
    assert "_VRF_TABLE_RANGE_LO = 1000" in src
    m = re.search(r"_VRF_TABLE_RANGE_HI\s*=\s*(\d+)", src)
    assert m
    _hi = int(m.group(1))
    assert _hi >= 2999, f"Range HI={_hi} too small; need ≥ 2999"


def test_d1_linear_probe_on_collision():
    """`_vrf_table` must not just return `hash % N` — it must
    check the allocated set and probe forward on collision."""
    src = _read("utils/frr_docker.py")
    fn_start = src.index("def _vrf_table(self, device_id: str) -> int:")
    _end = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:_end]
    assert "_in_use" in body
    assert "collision avoided" in body \
        or "if _tid not in _in_use:" in body


def test_d1_persist_and_load_helpers():
    """State-file persistence + reload on init so netgen-server
    restart doesn't churn ids."""
    src = _read("utils/frr_docker.py")
    assert "def _persist_vrf_allocations" in src
    assert "def _load_vrf_allocations" in src
    assert "def _resolve_vrf_state_path" in src


def test_d1_release_on_device_removal():
    src = _read("utils/frr_docker.py")
    assert "def _release_vrf_table" in src


# ─── D2: VXLAN iface name dedup ───


def test_d2_marker_present():
    src = _read("utils/vxlan.py")
    assert "v0.5.373 (audit vxlan-iface-name-truncation-collision)" in src


def test_d2_unique_picker_helper_exists():
    src = _read("utils/vxlan.py")
    assert "def _pick_unique_vxlan_iface(" in src


def test_d2_picker_checks_interface_exists_on_candidates():
    """The helper must consult _interface_exists on each candidate
    so a hash collision or a truncation collision resolves to a
    different name."""
    src = _read("utils/vxlan.py")
    fn_start = src.index("def _pick_unique_vxlan_iface(")
    _end = src.index("\n\ndef ", fn_start + 1)
    body = src[fn_start:_end]
    assert "_interface_exists" in body
    assert "if len(_name) <= 15" in body


def test_d2_container_path_uses_picker():
    """The container-side name-build site must call
    _pick_unique_vxlan_iface, not construct the name inline."""
    src = _read("utils/vxlan.py")
    # Find the "Configure inside container" block.
    m = re.search(
        r"if container_name and frr_manager:[\s\S]{0,800}?config\.get\(",
        src,
    )
    assert m
    body = m.group(0)
    assert "_pick_unique_vxlan_iface(" in body, (
        "Container-side path still builds the name inline"
    )


def test_d2_host_path_uses_picker():
    """The host-fallback name-build site must call
    _pick_unique_vxlan_iface too."""
    src = _read("utils/vxlan.py")
    m = re.search(
        r"# Host-level fallback \(legacy path\)[\s\S]{0,600}",
        src,
    )
    assert m
    body = m.group(0)
    assert "_pick_unique_vxlan_iface(" in body


# ─── D4: RFC 2544 poll wedge ───


def test_d4_marker_present():
    src = _read("widgets/rfc2544_dialog.py")
    assert "v0.5.373 (audit rfc2544-poll-swallows-exceptions)" in src


def test_d4_tracks_consecutive_failures():
    src = _read("widgets/rfc2544_dialog.py")
    fn_start = src.index("def _poll_progress(self):")
    _end = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:_end]
    assert "_poll_consecutive_fail" in body
    assert "self._poll_consecutive_fail = 0" in body
    assert "self._poll_consecutive_fail += 1" in body


def test_d4_stops_timer_after_threshold():
    """After 5 consecutive fails the timer must stop, controls
    must re-enable, and a QMessageBox / status update must surface."""
    src = _read("widgets/rfc2544_dialog.py")
    fn_start = src.index("def _poll_progress(self):")
    _end = src.index("\n    def ", fn_start + 1)
    body = src[fn_start:_end]
    assert "self._poll_consecutive_fail >= 5" in body
    assert "self._poll_timer.stop()" in body
    assert "self.start_btn.setEnabled(True)" in body


# ─── D5: per-server auth token routing ───


def test_d5_marker_present():
    src = _read("run_tgen_client.py")
    assert "v0.5.373 (audit client-multi-server-auth-token-routing)" in src


def test_d5_host_tokens_map_exists():
    src = _read("run_tgen_client.py")
    assert "_NETGEN_HOST_TOKENS" in src


def test_d5_register_takes_token_kwarg():
    src = _read("run_tgen_client.py")
    assert "def _netgen_register_server_host(host, token=None):" in src


def test_d5_wrapper_prefers_per_host_over_env():
    """Lookup order: per-host token → env-var → none."""
    src = _read("run_tgen_client.py")
    assert "def _netgen_token_for_url(url):" in src
    fn_start = src.index("def _netgen_token_for_url(url):")
    _end = src.index("\n    _env_auth_token", fn_start + 1) \
        if "\n    _env_auth_token" in src[fn_start:] else fn_start + 800
    body = src[fn_start:_end]
    assert "_NETGEN_HOST_TOKENS" in body
    # Falls back to env-var when not in the per-host map.
    assert 'os.environ.get("NETGEN_AUTH_TOKEN"' in body


def test_d5_add_server_passes_token():
    src = _read("traffic_client/menu_actions.py")
    m = re.search(
        r"def add_server_interface\([\s\S]+?def save_server_interfaces",
        src,
    )
    assert m
    body = m.group(0)
    assert 'token=_tok' in body, (
        "add_server_interface doesn't forward per-server auth_token"
    )
    # And the token comes from the dialog entry.
    assert 'entry.get("auth_token")' in body


# ─── D6: query_device_database CLI timeouts ───


def test_d6_marker_present():
    src = _read("query_device_database.py")
    assert "v0.5.373 (audit query-cli-missing-timeouts)" in src


def test_d6_shared_timeout_constant_defined():
    src = _read("query_device_database.py")
    assert re.search(r"^_GET_TIMEOUT\s*=\s*\(", src, re.MULTILINE)


def test_d6_all_9_diagnostic_calls_have_timeout():
    """The 9 identified diagnostic requests.get sites (lines 535-
    647 pre-fix) must ALL now carry a timeout kwarg. Bounded
    check: count timeout-annotated calls vs total calls in the
    file."""
    src = _read("query_device_database.py")
    _all_gets = re.findall(r"requests\.get\(", src)
    _with_timeout = re.findall(r"requests\.get\([^)]{0,300}?timeout", src)
    assert len(_all_gets) == len(_with_timeout), (
        f"Not every requests.get has a timeout — "
        f"{len(_all_gets)} calls, {len(_with_timeout)} with timeout"
    )


# ─── overall ───


def test_pyproject_version_at_least_0573():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 373), (
        f"Version {m.group(1)} < 0.5.373"
    )


# ─── regression guards ───


def test_v0372_c1_url_guard_still_intact():
    """v0.5.372 C1 URL-guard survives — D5 extended it, not
    replaced."""
    src = _read("run_tgen_client.py")
    assert "v0.5.372 (audit client-auth-token-leak-monkey-patch)" in src
    assert "_NETGEN_ALLOWED_HOSTS" in src
    assert "_netgen_url_allowed" in src


def test_v0371_install_dpdk_marker_intact():
    dpdk = (_REPO / "resources" / "dpdk" / "install_dpdk.sh").read_text()
    assert "v0.5.371 (audit install-dpdk-must-not-fail)" in dpdk


def test_v0369_install_rdma_marker_intact():
    rdma = (_REPO / "resources" / "dpdk" / "install_rdma.sh").read_text()
    assert "v0.5.369 (audit rdma-install-must-not-fail)" in rdma


def test_vrf_name_backward_compat_intact():
    """v0.5.373 D1 must NOT change _vrf_name — the 11-char device-
    id prefix that identifies the VRF interface on the wire is
    part of the operator-visible contract."""
    src = _read("utils/frr_docker.py")
    m = re.search(
        r"def _vrf_name\(self, device_id: str\) -> str:[\s\S]+?return f\"vrf-\{short\}\"",
        src,
    )
    assert m, "_vrf_name signature or return format changed"
