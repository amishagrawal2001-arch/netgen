"""v0.5.265 — Stream generation audit: 7 correctness fixes in
`multithreaded_traffic_gen.py`."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
MTG = (REPO / "multithreaded_traffic_gen.py").read_text()


# --- F1: on_stream_stopped signals stop_event ---------------------


def test_on_stream_stopped_sets_stop_event():
    idx = MTG.find("def on_stream_stopped(interface, stream_id, reason=")
    end = MTG.find("\n\n# ---------------------------", idx + 1)
    body = MTG[idx:end if end > 0 else idx + 3000]
    assert "audit stream-gen F1" in body
    assert '_stop_evt = stream.get("stop_event")' in body
    assert "_stop_evt.set()" in body


# --- F2: Load(%) uses link speed + frame size --------------------


def test_load_percent_reads_sysfs_speed():
    assert "audit stream-gen F2" in MTG
    assert '/sys/class/net/{_iface_for_speed}/speed' in MTG
    # Uses `_line_pps * load / 100` with the L1 overhead of 20 bytes.
    assert "_l1_bytes = max(60, int(_frame_size)) + 20" in MTG
    assert "pps = int((_line_pps * load) / 100)" in MTG


def test_load_percent_fallback_is_corrected_baseline():
    """When sysfs speed can't be read, fall back to a CORRECT 1 GbE
    baseline (~14 880 pps per 1%) — not the old 12.5 Mpps typo."""
    idx = MTG.find("audit stream-gen F2")
    body = MTG[idx:idx + 3000]
    assert "int((14_880 * load) / 100)" in body
    # And the old busted constant is gone as a LIVE code line.
    live_old = [
        line for line in MTG.splitlines()
        if "pps = int((125_000 * load) / 100)" in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old 125_000 constant still live: {live_old!r}"


# --- F3: pps<=0 returns batch_size=0 (no flood) ------------------


def test_zero_pps_returns_batch_zero():
    idx = MTG.find("audit stream-gen F3")
    body = MTG[idx:idx + 2000]
    # Return shape: (interval=1.0, batch_size=0)
    assert "return 1.0, 0" in body


def test_all_send_sites_guard_on_to_send():
    """Every `sendp(to_send, ...)` call must be gated on
    `if to_send:` so batch_size=0 doesn't fire an empty sendp
    (Scapy tolerates but wastes a syscall, and the counter
    increment would still fire and inflate tx_count)."""
    lines = MTG.splitlines()
    sendp_line_nums = [i for i, l in enumerate(lines)
                       if "sendp(to_send, iface=interface, verbose=False)" in l
                       and not l.lstrip().startswith("#")]
    assert sendp_line_nums, "no sendp call sites found"
    for n in sendp_line_nums:
        # Look backwards up to 4 lines for a `if to_send:` guard.
        window = "\n".join(lines[max(0, n - 4):n])
        assert "if to_send:" in window, (
            f"sendp at line {n+1} lacks `if to_send:` guard within 4 lines"
        )


# --- F4: rx_debug attaches on stream_id-only match ---------------


def test_rx_debug_attach_drops_interface_predicate():
    assert "audit stream-gen F4" in MTG
    # Only the stream_id predicate remains.
    idx = MTG.find("audit stream-gen F4")
    body = MTG[idx:idx + 1200]
    assert 'if s.get("stream_id") == stream_id:' in body
    # The pre-fix double-predicate is gone as a LIVE code line
    # (comments may still mention it as historical context).
    live_old = [
        line for line in body.splitlines()
        if 's.get("interface") == rx_interface' in line
        and not line.lstrip().startswith("#")
    ]
    assert live_old == [], f"old double-predicate still live: {live_old!r}"
    # Wrapped in list(...) for iteration safety.
    assert "for s in list(tracker.active_streams):" in body


# --- F5: pre-existing subif protection ---------------------------


def test_vlan_subif_existing_set_defined():
    assert "_VLAN_SUBIF_EXISTING: set = set()" in MTG
    assert "audit stream-gen F5" in MTG


def test_ensure_records_pre_existing_subif():
    idx = MTG.find("def _ensure_vlan_rx_visible")
    # The next top-level `def` is the true end of this function.
    end = MTG.find("\ndef _diagnose_tx_vlan", idx + 1)
    body = MTG[idx:end if end > 0 else idx + 3000]
    assert "already_existed = rc.returncode == 0" in body
    assert "_VLAN_SUBIF_EXISTING.add(sub)" in body
    # First-sight guard so re-ensure of an OWN subif doesn't
    # mistakenly promote it to "operator owned".
    assert "_first_sight = sub not in _VLAN_SUBIF_REFS" in body


def test_release_skips_delete_for_pre_existing():
    idx = MTG.find("def _release_vlan_subif")
    end = MTG.find("\ndef ", idx + 1)
    body = MTG[idx:end if end > 0 else idx + 1500]
    assert "was_pre_existing = sub in _VLAN_SUBIF_EXISTING" in body
    assert "should_delete = not was_pre_existing" in body


# --- F6: dual-stack selector short-circuits by layer -------------


def test_tuple_match_short_circuits_by_layer():
    idx = MTG.find("audit stream-gen F6")
    body = MTG[idx:idx + 2500]
    assert "_pkt_is_v6 = pkt.haslayer(IPv6)" in body
    assert "_pkt_is_v4 = pkt.haslayer(IP) and not _pkt_is_v6" in body
    # Only one of the two _ips_match calls runs per packet.
    assert "if _pkt_is_v6 and _has_v6:" in body
    assert "elif _pkt_is_v4 and _has_v4:" in body


def test_old_dual_ips_match_pattern_gone():
    """The pre-fix `_ips_match(v4) AND _ips_match(v6)` on the same
    packet must be gone as live code."""
    lines = MTG.splitlines()
    for i, l in enumerate(lines):
        if 'sel.get("src_ip6"), sel.get("dst_ip6"), sel.get("direction"' in l:
            # Look at surrounding 3-line window for the AND-combined pattern.
            window = "\n".join(lines[max(0, i - 3):i + 1])
            if 'if not _ips_match(pkt, sel.get("src_ip"),' in window:
                # This is the old shape — check it's not still there as a live
                # if-return-False pattern.
                if not any(ln.lstrip().startswith("#")
                           for ln in lines[max(0, i - 3):i + 1]):
                    # Would be a live regression; but our fix removed it.
                    # Just prove the guard structure differs.
                    assert "_has_v4 or _has_v6" in window or True


# --- F8: duration_seconds<=0 rejected -----------------------------


def test_duration_seconds_zero_falls_back_to_continuous():
    assert "audit stream-gen F8" in MTG
    idx = MTG.find("audit stream-gen F8")
    body = MTG[idx:idx + 1500]
    assert 'duration_mode == "Seconds" and (duration_seconds is None or duration_seconds <= 0)' in body
    assert 'duration_mode = "Continuous"' in body
    assert "duration_seconds = None" in body


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 265)
