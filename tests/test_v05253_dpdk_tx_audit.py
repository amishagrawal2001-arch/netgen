"""v0.5.253 — DPDK tx_worker audit: 8 correctness fixes across
``utils/dpdk_tx_worker.py``, ``utils/dpdk_tx_worker_multi.py``,
``utils/dpdk_bind_safety.py``, and ``utils/dpdk_orphans.py``.

Fixes:
- DPDK-1  Post-run orphan reaper: pattern needs `--` separator AND
          `tx_worker.*` anchor. Pre-fix pkill returned rc=2 (unknown
          long option) so the whole reaper was silent no-op. Naive
          `--`-only fix re-opens the v0.5.119 friendly-fire of the
          paired rx_worker.
- DPDK-2  `_resolve_target_pps` Load-% baseline hardcoded to 1 GbE
          (1.25 Mpps at 100%). ~120x low on 100 G, ~480x low on
          400 G. Fix: accept iface + frame_size, use `_compute_line_pps`.
- DPDK-3  Multi-instance stop used SIGTERM; tx_worker.c only handles
          SIGINT → hugepage leak. Fix: start_new_session=True at Popen
          + killpg(SIGINT) with SIGKILL escalation.
- DPDK-4  vfio-bound iface → sysfs lookup returned None → `-a <bdf>`
          dropped → wrong port selected. Fix: fall back to admin
          bind-history JSON.
- DPDK-5  Multi-instance skipped LD_LIBRARY_PATH auto-discovery.
          Fix: extract `resolve_dpdk_ld_library_path()` helper,
          call from both launchers.
- DPDK-6  Bind-safety missed VLAN / bond / bridge parents. Fix:
          `_resolve_underlying_ifaces` inspects vlan / bond / brif.
- DPDK-7  Orphan reaper regex required strict UUID form. Fix:
          widened to accept any non-space token.
- DPDK-8  `_resolve_l2_l3_l4` ignored `protocol_selection.frame_size`.
          Fix: fall back to `protocol_selection.frame_size` for
          parity with `_resolve_target_pps`.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
TX = (REPO / "utils" / "dpdk_tx_worker.py").read_text()
MULTI = (REPO / "utils" / "dpdk_tx_worker_multi.py").read_text()
BIND = (REPO / "utils" / "dpdk_bind_safety.py").read_text()
ORPH = (REPO / "utils" / "dpdk_orphans.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- DPDK-1: post-run reaper pattern -------------------------------


def test_post_run_reaper_uses_dash_separator_and_tx_worker_anchor():
    """Reaper's sweep_pat must anchor on `tx_worker` (not just
    `--stream-id`) AND pkill must be called with `--` before the
    pattern — both together, or the fix is either a no-op or
    friendly-fires the paired rx_worker (v0.5.119 regression)."""
    idx = TX.find("Orphan reaper: any tx_worker that's still running")
    assert idx > 0
    body = TX[idx:idx + 2500]
    assert "audit DPDK-1" in body
    assert 'sweep_pat = f"tx_worker.*--stream-id {stream_id}"' in body
    # Both pkill invocations must pass `--` before the pattern.
    assert body.count('"pkill", "-TERM", "-f", "--", sweep_pat') == 1
    assert body.count('"pkill", "-KILL", "-f", "--", sweep_pat') == 1
    # The old bare pattern must be gone.
    assert 'sweep_pat = f"--stream-id {stream_id}"' not in body


# --- DPDK-2: Load-% uses real link speed ---------------------------


def test_resolve_target_pps_accepts_iface_and_frame_size():
    idx = TX.find("def _resolve_target_pps(")
    end = TX.find("\ndef ", idx + 1)
    sig_body = TX[idx:end if end > 0 else idx + 5000]
    # New signature must accept iface + frame_size (optional).
    assert "iface: Optional[str] = None" in sig_body
    assert "frame_size: Optional[int] = None" in sig_body
    assert "audit DPDK-2" in sig_body


def test_resolve_target_pps_load_percent_uses_line_pps_when_iface_supplied():
    from utils.dpdk_tx_worker import _resolve_target_pps
    sd = {"stream_rate_type": "Load (%)", "stream_load_percentage": 100}
    # Legacy caller (no iface) — falls back to the 1 GbE baseline.
    legacy = _resolve_target_pps(sd)
    assert legacy == 1_250_000
    # With iface (fake, so `_compute_line_pps` returns the 1 Mpps
    # safe fallback), the result must differ from the legacy path.
    # Prove it flows through _compute_line_pps, not the hardcoded
    # 1.25 Mpps constant.
    with_iface = _resolve_target_pps(sd, iface="does-not-exist", frame_size=64)
    assert with_iface != legacy
    assert with_iface == 1_000_000  # _compute_line_pps safe fallback


def test_run_stream_call_site_passes_iface_and_frame_size():
    # Both call sites inside dpdk_tx_worker.py (single-instance
    # launcher AND resolve_actual_tx_cores) must pass iface + frame_size.
    single_hits = re.findall(
        r"_resolve_target_pps\(stream_data,\s*iface=interface,\s*frame_size=frame_size\)",
        TX,
    )
    assert len(single_hits) >= 2


def test_multi_instance_call_site_passes_iface_and_frame_size():
    assert "audit DPDK-2" in MULTI
    assert re.search(
        r"_resolve_target_pps\(stream_data,\s*iface=interface,\s*frame_size=_fs_early\)",
        MULTI,
    )


def test_rx_autoscale_call_site_passes_iface_and_frame_size():
    # run_tgen_server's RX-queue autoscaler must also plumb iface
    # through so Load-% under-provisioning doesn't cascade.
    idx = SERVER.find("audit DPDK-2")
    assert idx > 0
    body = SERVER[idx:idx + 800]
    assert "iface=rx_interface" in body
    assert "frame_size=_fs" in body


# --- DPDK-3: multi-instance uses SIGINT via killpg ------------------


def test_multi_instance_popen_starts_new_session():
    idx = MULTI.find("proc = subprocess.Popen(")
    assert idx > 0
    body = MULTI[idx:idx + 800]
    assert "start_new_session=True" in body
    assert "audit DPDK-3" in body


def test_multi_instance_stop_uses_sigint_via_killpg():
    idx = MULTI.find("Cleanup: stop all instances")
    assert idx > 0
    body = MULTI[idx:idx + 2500]
    assert "audit DPDK-3" in body
    assert "os.killpg(_pgid, _sig.SIGINT)" in body
    # Must NOT have a live `proc.terminate()` CALL (SIGTERM, which
    # tx_worker doesn't handle → hugepage leak). The prose comment
    # documenting the pre-fix bug can still mention it, so filter
    # to lines whose first non-whitespace char is `proc.terminate`.
    live_calls = [
        line for line in body.splitlines()
        if line.lstrip().startswith("proc.terminate(")
    ]
    assert live_calls == [], f"live SIGTERM call still present: {live_calls!r}"
    # Escalation to SIGKILL required after the SIGINT wait times out.
    assert "os.killpg(_pgid, _sig.SIGKILL)" in body


def test_multi_instance_failure_cleanup_uses_sigint():
    # The Popen-failure cleanup loop (line ~344 pre-fix used
    # p["process"].terminate()) must also use SIGINT.
    idx = MULTI.find("Cleanup already launched instances")
    assert idx > 0
    body = MULTI[idx:idx + 800]
    assert "audit DPDK-3" in body
    assert "_sig.SIGINT" in body


# --- DPDK-4: bind_history fallback in _iface_to_bdf ----------------


def test_iface_to_bdf_falls_back_to_bind_history_json():
    idx = TX.find("def _iface_to_bdf(")
    end = TX.find("\ndef ", idx + 1)
    body = TX[idx:end if end > 0 else idx + 3000]
    assert "audit DPDK-4" in body
    # Both candidate paths mentioned so scripted installs still work.
    assert "/var/lib/netgen-server/admin_bind_history.json" in body
    assert "/tmp/netgen_admin_bind_history.json" in body


def test_iface_to_bdf_returns_bdf_from_history_shape(tmp_path, monkeypatch):
    """Feed a fake bind-history JSON via a monkey-patched path and
    prove _iface_to_bdf returns the BDF for a name that matches."""
    from utils import dpdk_tx_worker as _mod
    # sysfs lookup will fail for 'ghost0'; simulate the JSON write.
    hist = {"0000:aa:00.0": {"name": "ghost0", "kernel_driver": "mlx5_core"}}
    fake = tmp_path / "admin_bind_history.json"
    import json
    fake.write_text(json.dumps(hist))
    # Rewrite the function to look at our tmp path first via monkeypatch
    # of os.path.exists so it walks our fake instead of the real paths.
    real_open = open
    def _fake_open(path, mode="r", *a, **kw):
        if "admin_bind_history" in path:
            return real_open(str(fake), mode, *a, **kw)
        return real_open(path, mode, *a, **kw)
    import builtins
    monkeypatch.setattr(builtins, "open", _fake_open)
    import os as _os
    real_exists = _os.path.exists
    def _fake_exists(p):
        if "admin_bind_history" in p:
            return True
        return real_exists(p)
    monkeypatch.setattr(_os.path, "exists", _fake_exists)
    got = _mod._iface_to_bdf("ghost0")
    assert got == "0000:aa:00.0"


# --- DPDK-5: LD_LIBRARY_PATH helper extracted ----------------------


def test_resolve_dpdk_ld_library_path_helper_is_importable():
    from utils.dpdk_tx_worker import resolve_dpdk_ld_library_path
    # Signature check: takes current_ld_path str, returns str.
    out = resolve_dpdk_ld_library_path("/foo")
    assert isinstance(out, str)
    assert "/foo" in out  # current path preserved


def test_multi_instance_imports_and_uses_ld_library_helper():
    assert "resolve_dpdk_ld_library_path" in MULTI
    # Find the CALL SITE, not the import comment.
    idx = MULTI.find("resolve_dpdk_ld_library_path(_current_ld)")
    assert idx > 0
    body = MULTI[max(0, idx - 500):idx + 500]
    assert "audit DPDK-5" in body


def test_single_instance_uses_extracted_helper_not_inline():
    # run_stream's body should call resolve_dpdk_ld_library_path()
    # and NOT re-inline the pkg-config / ldconfig scanning that
    # used to live inline (they moved into the helper).
    idx = TX.find('child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")')
    assert idx > 0
    body = TX[idx:idx + 1500]
    assert "resolve_dpdk_ld_library_path(current_ld_path)" in body
    # The inline pkg-config scan is gone from run_stream's body.
    assert 'pkg-config", "--variable=libdir", "libdpdk"' not in body


# --- DPDK-6: bind-safety resolves parents --------------------------


def test_resolve_underlying_ifaces_helper_exists():
    from utils.dpdk_bind_safety import _resolve_underlying_ifaces
    # Unknown iface: returns just {iface} — never crashes.
    got = _resolve_underlying_ifaces("does-not-exist-if")
    assert got == {"does-not-exist-if"}


def test_check_bind_safe_refuses_physical_parent_of_vlan_mgmt(tmp_path, monkeypatch):
    """If mgmt is on eno1.100 (VLAN sub-if) and the candidate is
    eno1 (physical parent), the guard must refuse — pre-fix it
    passed silently and let the operator kill their own mgmt path."""
    # Stub /proc/net/vlan/<if> lookup: pretend eno1.100 is a VLAN
    # sub-if whose parent is eno1.
    from utils import dpdk_bind_safety as _mod
    real_exists = _mod.os.path.exists
    def _fake_exists(p):
        if p == "/proc/net/vlan/eno1.100":
            return True
        return real_exists(p)
    real_open = open
    def _fake_open(path, mode="r", *a, **kw):
        if path == "/proc/net/vlan/eno1.100":
            import io
            return io.StringIO("Device: eno1\nOther: stuff\n")
        return real_open(path, mode, *a, **kw)
    monkeypatch.setattr(_mod.os.path, "exists", _fake_exists)
    import builtins
    monkeypatch.setattr(builtins, "open", _fake_open)
    reason = _mod.check_bind_safe(
        "eno1", default_route_iface="eno1.100"
    )
    assert reason is not None
    assert "default route" in reason


def test_check_bind_safe_still_matches_exact_iface():
    from utils.dpdk_bind_safety import check_bind_safe
    reason = check_bind_safe("eno1", default_route_iface="eno1")
    assert reason is not None


def test_check_bind_safe_returns_none_for_unrelated():
    from utils.dpdk_bind_safety import check_bind_safe
    assert check_bind_safe("eth99", default_route_iface="eno1") is None


# --- DPDK-7: orphan regex widened to non-UUID ----------------------


def test_stream_id_regex_accepts_non_uuid():
    from utils.dpdk_orphans import _RE_STREAM_ID
    m = _RE_STREAM_ID.search("/usr/local/bin/tx_worker --stream-id my-test-slug --other")
    assert m is not None
    assert m.group(1) == "my-test-slug"


def test_stream_id_regex_still_accepts_uuid():
    from utils.dpdk_orphans import _RE_STREAM_ID
    uuid = "12345678-1234-1234-1234-1234567890ab"
    m = _RE_STREAM_ID.search(f"tx_worker --stream-id {uuid} --file-prefix txw_x")
    assert m is not None
    assert m.group(1) == uuid


# --- DPDK-8: frame_size protocol_selection fallback ---------------


def test_resolve_l2_l3_l4_falls_back_to_protocol_selection_frame_size():
    from utils.dpdk_tx_worker import _resolve_l2_l3_l4
    sd = {
        "protocol_data": {
            "mac": {"mac_source_address": "aa:aa:aa:aa:aa:aa",
                    "mac_destination_address": "bb:bb:bb:bb:bb:bb"},
            "ipv4": {"ipv4_source": "1.1.1.1", "ipv4_destination": "2.2.2.2"},
        },
        # Top-level frame_size deliberately absent; only ps has it.
        "protocol_selection": {"frame_size": 1500},
    }
    out = _resolve_l2_l3_l4(sd)
    assert out["frame_size"] == 1500


def test_resolve_l2_l3_l4_prefers_top_level_frame_size():
    from utils.dpdk_tx_worker import _resolve_l2_l3_l4
    sd = {
        "protocol_data": {
            "mac": {"mac_source_address": "aa:aa:aa:aa:aa:aa",
                    "mac_destination_address": "bb:bb:bb:bb:bb:bb"},
            "ipv4": {"ipv4_source": "1.1.1.1", "ipv4_destination": "2.2.2.2"},
        },
        "frame_size": 128,
        "protocol_selection": {"frame_size": 1500},
    }
    out = _resolve_l2_l3_l4(sd)
    assert out["frame_size"] == 128  # top-level wins


def test_resolve_l2_l3_l4_marker_present():
    idx = TX.find("def _resolve_l2_l3_l4(")
    end = TX.find("\ndef ", idx + 1)
    body = TX[idx:end if end > 0 else idx + 4000]
    assert "audit DPDK-8" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 253)
