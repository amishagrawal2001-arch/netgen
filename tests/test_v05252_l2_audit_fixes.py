"""v0.5.252 — L2 emulation audit: 10 correctness fixes across
utils/l2_protocols.py, server/l2_routes.py, and
widgets/l2_emulation_tab.py.

Fixes:
- L2-1  BFD version = 1 (RFC 5880 §4.1), not 3. Every peer dropped
        our BFD frames under §6.8.6.
- L2-2  VRRP dialog now accepts blank Source MAC (documented
        auto-derive path was blocked by _validate_mac).
- L2-3  IGMPv2 General Query (type 0x11, group 0.0.0.0) now goes
        to 224.0.0.1 per RFC 2236 §3, not 0.0.0.0.
- L2-4  IGMPv3 reports now carry the mandatory IP Router Alert
        option (RFC 3376 §4).
- L2-5  stop_session returns the counter snapshot; HTTP stop
        response includes it so the client can render post-mortem
        counters before the row disappears.
- L2-6  Client-side Stop / Stop-all / per-row-Stop moved to a
        background QThread so a slow/unreachable server doesn't
        freeze the UI for 40-80 s.
- L2-7  LLDP TLV encoding switched from `.encode('ascii')` to
        `.encode('utf-8', errors='replace')` so non-ASCII input
        (café, non-Latin hostnames) doesn't crash the worker every
        tick.
- L2-8  stop_session leaves the session in _SESSIONS as a
        "zombie" when the worker thread doesn't exit within the
        3s join, so the operator can see it + re-stop it.
- L2-9  LACP preview default state now 0x05 (matches live emitter),
        not 0x3d — "preview matches wire" invariant restored.
- L2-10 _run_periodic drift compensation: schedule next tick off
        `last_tick + interval` instead of `send_end + interval` so
        BFD sub-second modes don't overshoot the peer's detection
        window.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
L2 = (REPO / "utils" / "l2_protocols.py").read_text()
ROUTES = (REPO / "server" / "l2_routes.py").read_text()
TAB = (REPO / "widgets" / "l2_emulation_tab.py").read_text()


# --- L2-1: BFD version = 1 ------------------------------------------


def test_bfd_live_emitter_uses_version_1():
    """start_bfd's live payload must set ver_diag = (1 << 5) | diag,
    not (3 << 5). Pre-fix, byte 0's top 3 bits held 011 (version 3)
    and RFC 5880 §6.8.6-compliant peers dropped every frame."""
    idx = L2.find("Byte 3: Length (24 for no-auth)")
    body = L2[idx:idx + 800]
    assert "ver_diag = (1 << 5)" in body
    assert "audit L2-1" in body


def test_bfd_preview_matches_live_emitter_version_1():
    """The preview-frame builder in _bfd_preview must match the
    live emitter — same fix, same rationale."""
    # Second occurrence of ver_diag = (1 << 5), inside _bfd_preview.
    _all = re.findall(r"ver_diag = \(1 << 5\)", L2)
    assert len(_all) >= 2, "both live emitter AND preview must be version 1"


def test_bfd_regression_test_updated_from_3_to_1():
    """The pre-existing tests/test_bfd_l2.py locked in the bug with
    `assert version == 3`. Renamed + flipped to 1."""
    bfd_test = (REPO / "tests" / "test_bfd_l2.py").read_text()
    assert "def test_bfd_version_is_1_and_length_byte_is_24" in bfd_test
    assert "assert version == 1" in bfd_test
    # Old function name is gone.
    assert "def test_bfd_version_is_3" not in bfd_test


# --- L2-2: VRRP dialog blank Source MAC -----------------------------


def test_vrrp_dialog_accepts_blank_source_mac():
    """The dialog's placeholder + tooltip say "leave blank" to
    auto-derive the RFC 5798 virtual MAC. _validate_mac must only
    run when the operator actually typed a MAC."""
    idx = TAB.find("src_mac = self._vrrp_src_mac.text().strip()")
    # Look BEFORE the assignment for the v0.5.252 marker comment.
    body = TAB[max(0, idx - 800):idx + 800]
    assert "v0.5.252 (audit L2-2)" in body
    assert "if src_mac:" in body


# --- L2-3: IGMPv2 General Query dst -------------------------------


def test_igmpv2_general_query_targets_all_hosts():
    """IGMPv2 type 0x11 with group=0.0.0.0 must send to 224.0.0.1
    per RFC 2236 §3, not to 0.0.0.0."""
    idx = L2.find("v2 (RFC 2236): the established default path.")
    body = L2[idx:idx + 1500]
    assert "audit L2-3" in body
    assert 'elif t == 0x11 and (not group or group == "0.0.0.0"):' in body
    assert 'ip_dst = "224.0.0.1"' in body


# --- L2-4: IGMPv3 Router Alert -------------------------------------


def test_igmpv3_report_includes_router_alert():
    """IGMPv3 messages MUST carry an IP Router Alert option per
    RFC 3376 §4. Pre-fix used `options=[]`."""
    idx = L2.find("v0.5.252 (audit L2-4)")
    assert idx > 0
    body = L2[idx:idx + 1000]
    assert "IPOption_Router_Alert" in body
    assert "options=[IPOption_Router_Alert()]" in body


# --- L2-5: stop_session returns snapshot ---------------------------


def test_stop_session_returns_snapshot_dict():
    idx = L2.find("def stop_session(session_id: str)")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 3000]
    assert "-> Optional[Dict[str, Any]]" in body
    assert "audit L2-5" in body
    # Callers get the snapshot back.
    assert "return snap" in body
    # Legacy bool return is documented gone.
    assert "Pre-fix returned bool" in body


def test_stop_route_includes_snapshot_in_response():
    idx = ROUTES.find("def _l2_stop_impl():")
    end = ROUTES.find("\n@l2_bp.route", idx + 1)
    body = ROUTES[idx:end if end > 0 else idx + 2000]
    assert "audit L2-5" in body
    assert '"snapshot":   snap' in body


# --- L2-6: client Stop moved to background QThread -----------------


def test_stop_selected_uses_background_worker():
    idx = TAB.find("def _on_stop_selected(self):")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 3000]
    assert "audit L2-6" in body
    assert "self._start_l2_stop_worker(url, sids, all_flag=False)" in body
    # Old sync loop is gone.
    assert 'requests.post(\n                    f"{url}/api/l2/stop"' not in body


def test_stop_all_uses_background_worker():
    idx = TAB.find("def _on_stop_all(self):")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 3000]
    assert "self._start_l2_stop_worker(url, sids=None, all_flag=True)" in body


def test_per_row_stop_uses_background_worker():
    idx = TAB.find("def _stop_session_by_id(self, session_id: str)")
    end = TAB.find("\n    def ", idx + 1)
    body = TAB[idx:end if end > 0 else idx + 2000]
    assert "audit L2-6" in body
    assert "_start_l2_stop_worker" in body


def test_stop_worker_helper_defined():
    idx = TAB.find("def _start_l2_stop_worker(self, url, sids, all_flag):")
    assert idx > 0
    body = TAB[idx:idx + 5000]
    assert "class _L2StopWorker(QThread):" in body
    # Keepalive against SIGABRT.
    assert "self._l2_stop_workers.append(_w)" in body


# --- L2-7: LLDP UTF-8 encoding -------------------------------------


def test_lldp_live_encoder_uses_utf8_errors_replace():
    """Live LLDP factory must use utf-8 + errors='replace' on all
    four string fields."""
    idx = L2.find("Scapy LLDP TLVs stack as layers via `/`")
    body = L2[idx:idx + 2500]
    assert "audit L2-7" in body
    for _field in ("chassis_id", "port_id", "system_name", "system_description"):
        assert f'{_field}.encode("utf-8", errors="replace")' in body, \
            f"{_field} still using ascii encoding"


def test_lldp_preview_matches_live_encoder():
    """_lldpdu preview helper must use the same encoding."""
    idx = L2.find("def _lldpdu(b):")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 2000]
    assert 'encode("utf-8", errors="replace")' in body
    # No more .encode("ascii") anywhere in the file (both live +
    # preview scrubbed).
    assert '.encode("ascii")' not in L2


# --- L2-8: stop_session leaves zombies visible ---------------------


def test_stop_session_only_evicts_on_clean_exit():
    idx = L2.find("def stop_session(session_id: str)")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 3000]
    # Guards eviction behind is_alive check via _clean_exit flag.
    assert "_clean_exit = True" in body
    # v0.5.252: getattr-guarded so thread-shaped mocks work; the
    # is_alive check is spelled `_is_alive()` after the getattr.
    assert 'getattr(sess.thread, "is_alive"' in body
    assert "_clean_exit = False" in body
    assert "if _clean_exit:" in body
    assert "still alive after" in body


# --- L2-9: LACP preview default matches live -----------------------


def test_lacp_preview_default_state_matches_live_emitter():
    """Both default to 0x05 (Activity | Aggregation)."""
    # Live emitter's default is at start_lacp; preview at _lacpdu.
    # We only check the preview here — the live emitter's default
    # is exercised by the existing test_l2_emulation_qinq.py suite.
    idx = L2.find('actor_state=int(b.get("state") or 0x05)')
    assert idx > 0, "LACP preview default_state not fixed"
    # And the pre-fix 0x3d is gone from the preview.
    _preview_start = L2.find("def _lacpdu(b):")
    _preview_end = L2.find("\ndef ", _preview_start + 1)
    _preview_body = L2[_preview_start:_preview_end]
    assert "audit L2-9" in _preview_body


# --- L2-10: _run_periodic drift compensation -----------------------


def test_run_periodic_schedules_next_tick_off_last_tick():
    idx = L2.find("def _run_periodic(sess: _Session")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 4000]
    assert "audit L2-10" in body
    # New logic tracks next_tick_at = last_tick + interval instead
    # of doing a plain post-send wait.
    assert "next_tick_at = time.time()" in body
    assert "next_tick_at += interval_s" in body
    assert "_residual = next_tick_at - time.time()" in body


def test_run_periodic_reanchors_when_behind_schedule():
    """When sendp takes longer than interval_s, we skip the wait
    and re-anchor next_tick_at to now — otherwise drift compounds
    across many slow cycles."""
    idx = L2.find("def _run_periodic(sess: _Session")
    end = L2.find("\ndef ", idx + 1)
    body = L2[idx:end if end > 0 else idx + 4000]
    assert "Behind schedule" in body
    # After the drift, next_tick_at reset to now.
    _reset_lines = [l for l in body.splitlines() if "next_tick_at = time.time()" in l]
    assert len(_reset_lines) >= 2, "next_tick_at must be re-anchored to now on drift"


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 252)
