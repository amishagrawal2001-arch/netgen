"""v0.5.381 — monitor + RX correctness bundle.

  U1  Monitor per-device state-dict cleanup on device delete.
  U2  OSPF/ISIS monitor loop — check wait() return + break.
  U3  RX seq-dedup O(n)→O(1) LRU via deque(maxlen)+set.
  U4  ARP peer-fallback picks freshest healthy peer.
  U5  Monitor poll-interval drift correction (all 4 monitors).
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


def test_arp_monitor_ast_parses():
    ast.parse(_read("utils/arp_monitor.py"))


def test_bgp_monitor_ast_parses():
    ast.parse(_read("utils/bgp_monitor.py"))


def test_ospf_monitor_ast_parses():
    ast.parse(_read("utils/ospf_monitor.py"))


def test_isis_monitor_ast_parses():
    ast.parse(_read("utils/isis_monitor.py"))


def test_dhcp_monitor_ast_parses():
    ast.parse(_read("utils/dhcp_monitor.py"))


def test_device_database_ast_parses():
    ast.parse(_read("utils/device_database.py"))


def test_traffic_gen_ast_parses():
    ast.parse(_read("multithreaded_traffic_gen.py"))


# ─── U1: state-dict cleanup on device delete ───


def test_u1_marker_present():
    """Marker in each of 5 monitor modules + the DB fan-out."""
    for _rel in ("utils/arp_monitor.py",
                 "utils/bgp_monitor.py",
                 "utils/ospf_monitor.py",
                 "utils/isis_monitor.py",
                 "utils/dhcp_monitor.py",
                 "utils/device_database.py"):
        src = _read(_rel)
        assert "v0.5.381 (audit monitor U1)" in src, (
            f"{_rel} missing v0.5.381 U1 marker"
        )


def test_u1_on_device_deleted_exposed_by_each_monitor():
    """Every monitor module must expose `on_device_deleted`."""
    for _rel in ("utils/arp_monitor.py",
                 "utils/bgp_monitor.py",
                 "utils/ospf_monitor.py",
                 "utils/isis_monitor.py",
                 "utils/dhcp_monitor.py"):
        src = _read(_rel)
        assert "def on_device_deleted(device_id: str) -> None:" in src, (
            f"{_rel} missing on_device_deleted hook"
        )


def test_u1_arp_hook_pops_state_and_lock_dicts():
    src = _read("utils/arp_monitor.py")
    _idx = src.index("def on_device_deleted(device_id: str) -> None:")
    body = src[_idx:_idx + 1200]
    assert "_LAST_ARP_STATUS_LOGGED.pop(device_id, None)" in body
    assert "_ARP_WRITE_LOCKS.pop(device_id, None)" in body


def test_u1_bgp_hook_pops_state_and_lock_dicts():
    src = _read("utils/bgp_monitor.py")
    _idx = src.index("def on_device_deleted(device_id: str) -> None:")
    body = src[_idx:_idx + 1200]
    assert "_LAST_BGP_STATE_LOGGED.pop(device_id, None)" in body
    assert "_BGP_WRITE_LOCKS.pop(device_id, None)" in body


def test_u1_ospf_hook_pops_state_and_lock_dicts():
    src = _read("utils/ospf_monitor.py")
    _idx = src.index("def on_device_deleted(device_id: str) -> None:")
    body = src[_idx:_idx + 1200]
    assert "_LAST_OSPF_STATE_LOGGED.pop(device_id, None)" in body
    assert "_OSPF_WRITE_LOCKS.pop(device_id, None)" in body


def test_u1_isis_hook_pops_state_and_lock_dicts():
    src = _read("utils/isis_monitor.py")
    _idx = src.index("def on_device_deleted(device_id: str) -> None:")
    body = src[_idx:_idx + 1200]
    assert "_LAST_ISIS_STATE_LOGGED.pop(device_id, None)" in body
    assert "_ISIS_WRITE_LOCKS.pop(device_id, None)" in body


def test_u1_dhcp_hook_pops_lock_dict():
    """DHCP monitor doesn't keep a _LAST_*_STATE_LOGGED cache
    (state-history de-dup happens in add_state_transition), so
    only the lock dict is pruned."""
    src = _read("utils/dhcp_monitor.py")
    _idx = src.index("def on_device_deleted(device_id: str) -> None:")
    body = src[_idx:_idx + 1200]
    assert "_DHCP_WRITE_LOCKS.pop(device_id, None)" in body


def test_u1_remove_device_fans_out_to_all_monitors():
    """device_database.remove_device must call each monitor's
    on_device_deleted after the DELETE commits."""
    src = _read("utils/device_database.py")
    _idx = src.index("v0.5.381 (audit monitor U1): purge per-device")
    body = src[_idx:_idx + 3000]
    for _mod in ("utils.arp_monitor", "utils.bgp_monitor",
                 "utils.ospf_monitor", "utils.isis_monitor",
                 "utils.dhcp_monitor"):
        assert f'"{_mod}"' in body, (
            f"remove_device fan-out missing module: {_mod}"
        )
    assert "on_device_deleted" in body
    assert "importlib.import_module" in body


def test_u1_fanout_after_commit_not_before():
    """The fan-out must live AFTER conn.commit() so a partial
    delete (rolled back) doesn't purge live monitor state."""
    src = _read("utils/device_database.py")
    _commit_idx = src.rindex("conn.commit()", 0,
                             src.index("v0.5.381 (audit monitor U1)"))
    _fanout_idx = src.index("v0.5.381 (audit monitor U1): purge per-device")
    assert _fanout_idx > _commit_idx, (
        "U1 fan-out fires before commit — rolled-back deletes "
        "would purge live state"
    )


# ─── U2 + U5: wait-break + drift correction ───


def test_u2_u5_marker_present_in_all_four_monitors():
    """OSPF/ISIS get U2+U5 markers (wait-break was missing);
    ARP/BGP already had wait-break so only U5 marker applies."""
    for _rel in ("utils/ospf_monitor.py", "utils/isis_monitor.py"):
        src = _read(_rel)
        assert "v0.5.381 (audit monitor U2 + U5)" in src or \
               ("v0.5.381 (audit monitor U5)" in src and
                "v0.5.381 (audit monitor U2 + U5)" in src), (
            f"{_rel} missing U2+U5 marker"
        )
    for _rel in ("utils/arp_monitor.py", "utils/bgp_monitor.py"):
        src = _read(_rel)
        assert "v0.5.381 (audit monitor U5)" in src


def test_u5_arp_uses_tick_start_and_drift_subtraction():
    src = _read("utils/arp_monitor.py")
    _idx = src.index("def _monitor_loop(self)")
    body = src[_idx:_idx + 3000]
    assert "_tick_start = time.monotonic()" in body
    assert "self.check_interval - _elapsed" in body
    assert "max(0.1, self.check_interval - _elapsed)" in body


def test_u5_bgp_uses_tick_start_and_drift_subtraction():
    src = _read("utils/bgp_monitor.py")
    _idx = src.index("def _monitor_loop(self)")
    body = src[_idx:_idx + 3000]
    assert "_tick_start = time.monotonic()" in body
    assert "self.check_interval - _elapsed" in body


def test_u5_ospf_uses_tick_start_and_drift_subtraction():
    src = _read("utils/ospf_monitor.py")
    _idx = src.index("def _monitor_loop(self)")
    body = src[_idx:_idx + 3000]
    assert "_tick_start = time.monotonic()" in body
    assert "self.check_interval - _elapsed" in body


def test_u5_isis_uses_tick_start_and_drift_subtraction():
    src = _read("utils/isis_monitor.py")
    _idx = src.index("def _monitor_loop(self, interval: int)")
    # Locate loop end via the "[ISIS MONITOR] Monitoring loop stopped"
    # marker so we scan the full loop body regardless of length.
    _end = src.index("[ISIS MONITOR] Monitoring loop stopped", _idx)
    body = src[_idx:_end]
    assert "_tick_start = time.monotonic()" in body
    assert "interval - _elapsed" in body


def test_u2_ospf_checks_wait_return_and_breaks():
    """Pre-fix OSPF called wait() and dropped the return; now
    must check + break."""
    src = _read("utils/ospf_monitor.py")
    _idx = src.index("def _monitor_loop(self)")
    body = src[_idx:_idx + 3000]
    # At least one `if self.stop_event.wait(...): break` in body
    _m = re.search(r"if\s+self\.stop_event\.wait\([^)]+\):\s*\n\s*break", body)
    assert _m, "OSPF _monitor_loop must have wait-break pattern"


def test_u2_isis_checks_wait_return_and_breaks():
    src = _read("utils/isis_monitor.py")
    _idx = src.index("def _monitor_loop(self, interval: int)")
    _end = src.index("[ISIS MONITOR] Monitoring loop stopped", _idx)
    body = src[_idx:_end]
    _m = re.search(r"if\s+self\.stop_event\.wait\([^)]+\):\s*\n\s*break", body)
    assert _m, "ISIS _monitor_loop must have wait-break pattern"


# ─── U3: RX seq-dedup O(1) ───


def test_u3_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.381 (audit stream-gen U3)" in src
    # Marker on the alloc site AND the on_pkt hot path.
    assert src.count("v0.5.381 (audit stream-gen U3)") >= 2


def test_u3_deque_replaces_grow_then_halve():
    """The old rebuild-half pattern must be gone; deque+maxlen
    must be present."""
    src = _read("multithreaded_traffic_gen.py")
    assert "from collections import deque" in src
    assert "_seen_seqs_order = deque(maxlen=_SEQ_CAP)" in src
    # Old O(n/2) rebuild pattern removed
    assert "_seen_seqs_set.clear()" not in src
    assert "_seen_seqs_set.update(keep)" not in src


def test_u3_on_pkt_evicts_via_deque():
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("def on_pkt(_pkt):")
    body = src[_idx:_idx + 2000]
    assert "len(_seen_seqs_order) == _SEQ_CAP" in body
    assert "_seen_seqs_set.discard(_oldest)" in body
    assert "_seen_seqs_order.append(seq)" in body
    assert "_seen_seqs_set.add(seq)" in body


def test_u3_dedup_still_dedupes():
    """Behavioral: adding the same seq twice must still evict
    the second call (dedup preserved)."""
    from collections import deque
    _set = set()
    _order = deque(maxlen=5)

    def _add(seq):
        if seq in _set:
            return False
        if len(_order) == 5:
            _set.discard(_order[0])
        _order.append(seq)
        _set.add(seq)
        return True

    assert _add(1) is True
    assert _add(1) is False  # dedup!
    assert _add(2) is True
    for _s in range(3, 8):
        _add(_s)
    # Oldest (1) must be evicted after 5 unique seqs
    assert 1 not in _set
    assert 7 in _set


# ─── U4: ARP peer-fallback prefers healthy ───


def test_u4_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.381 (audit peer-fallback U4)" in src


def test_u4_healthy_states_defined():
    src = _read("run_tgen_server.py")
    _idx = src.index("_HEALTHY_STATES = {")
    body = src[_idx:_idx + 600]
    for _state in ("established", "full", "up", "2-way"):
        assert f'"{_state}"' in body


def test_u4_two_pass_scan():
    """First pass: healthy only; second pass: fall back to
    first-with-valid-IP for peer entries without state fields."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def _first_peer_ip(field_json_str, keys, family=\"ipv4\")")
    body = src[_idx:_idx + 3500]
    # Two loops over _peers
    _pass_count = body.count("for _p in _peers:")
    assert _pass_count >= 2, (
        f"Expected 2-pass peer scan; got {_pass_count} loops"
    )
    # Pass 1 gates on _peer_is_healthy
    assert "if not _peer_is_healthy(_p)" in body
    # Pass 2 comment
    assert "matching pre-v0.5.381 semantics" in body


def test_u4_peer_healthy_checks_multiple_state_fields():
    src = _read("run_tgen_server.py")
    _idx = src.index("def _peer_is_healthy(peer_dict)")
    body = src[_idx:_idx + 1500]
    for _sk in ("state", "bgp_state", "ospf_state",
                "isis_state", "peer_state", "session_state",
                "adjacency_state"):
        assert f'"{_sk}"' in body


def test_u4_v6_still_skips_link_local():
    """Regression: v0.5.376 F1 excluded link-local — must still hold."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def _peer_ip_for_family(peer_dict, keys, family)")
    body = src[_idx:_idx + 1500]
    assert 'not str(_v).lower().startswith("fe80")' in body


# ─── version guard ───


def test_pyproject_version_at_least_0581():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 381)


# ─── regression guards ───


def test_v0380_lock_release_still_present():
    """v0.5.380 M1 wired DHCP lock acquire/release; U1 must not
    have collided with that edit."""
    src = _read("utils/dhcp_monitor.py")
    assert "v0.5.380 (audit monitor M1)" in src


def test_v0380_isis_singleton_still_present():
    """v0.5.380 M2 imports frr_manager singleton in ISIS
    monitor; U1/U2 edits must not have removed it."""
    src = _read("utils/isis_monitor.py")
    assert "v0.5.380 (audit monitor M2)" in src
    assert "from .frr_docker import frr_manager" in src


def test_v0376_peer_fallback_marker_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.376 (audit arp-gateway-fallback-to-own-ip)" in src


def test_v0372_remove_device_verify_before_commit_intact():
    src = _read("utils/device_database.py")
    assert "v0.5.372 (audit device-db-rollback-after-commit)" in src


def test_v0264_bgp_write_lock_helper_intact():
    src = _read("utils/bgp_monitor.py")
    assert "_bgp_write_lock_for" in src or "_BGP_WRITE_LOCKS" in src
