"""v0.5.399 — device_database.py DB layer audit (5 items).

  P1  cleanup_old_data uses ISO cutoff (matches storage format).
  P2  update_device_status clears live-state + returns False on 0-row.
  P3  add_device / update_device atomic UPSERT + no reincarnation.
  P4  Read-side datetime format-compare fixed in get_device_statistics
      and get_database_info.recent_events_24h.
  P5  get_all_devices LIMIT + default cap.
"""
from __future__ import annotations

import ast
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
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


def test_device_database_ast_parses():
    ast.parse(_read("utils/device_database.py"))


# ─── P1: cleanup datetime ───


def test_p1_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P1)" in src


def test_p1_cleanup_uses_iso_cutoff():
    src = _read("utils/device_database.py")
    _idx = src.index("def cleanup_old_data")
    _end = _idx + 3500
    body = _strip_comments(src[_idx:_end])
    assert "datetime('now'" not in body, (
        "cleanup_old_data still uses SQLite datetime('now') — "
        "the string-compare bug is back"
    )
    assert "timedelta(days=days)" in body
    # And bound param (not unsafe .format)
    assert ".format(days)" not in body


# ─── P2: status field clear + rowcount ───


def test_p2_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P2)" in src


def test_p2_clears_live_state_fields_and_checks_rowcount():
    src = _read("utils/device_database.py")
    _idx = src.index("def update_device_status")
    _end = src.index("def update_arp_status", _idx)
    body = src[_idx:_end]
    # Cleared fields
    for _fld in (
        "bgp_established", "bgp_ipv4_state", "bgp_ipv6_state",
        "ospf_established", "ospf_state",
        "isis_running", "isis_established",
        "dhcp_running", "dhcp_state",
        "arp_ipv4_resolved", "arp_ipv6_resolved",
    ):
        assert _fld in body, f"P2 fix missed clearing {_fld} in update_device_status"
    # Rowcount check
    assert "cursor.rowcount" in body
    assert "return False" in body


def test_p2_runtime_stopped_device_clears_bgp_established():
    from utils.device_database import DeviceDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = DeviceDatabase(db_path=_p)
        db.init_database()
        # Add a device and mark BGP established via direct SQL
        db.add_device({
            "device_id": "d1",
            "device_name": "dev1",
            "interface": "eth0",
            "status": "Running",
        })
        with sqlite3.connect(_p) as _c:
            _c.execute(
                "UPDATE devices SET bgp_established=1, bgp_ipv4_state='Established', "
                "ospf_established=1, dhcp_running=1, arp_ipv4_resolved=1 "
                "WHERE device_id='d1'"
            )
            _c.commit()

        # Stop the device
        assert db.update_device_status("d1", "Stopped") is True

        # Live-state fields cleared
        row = db.get_device("d1")
        assert row["status"] == "Stopped"
        assert not row.get("bgp_established")
        assert not row.get("ospf_established")
        assert not row.get("dhcp_running")
        assert not row.get("arp_ipv4_resolved")

        # Missing device_id returns False (not True)
        assert db.update_device_status("does-not-exist", "Stopped") is False
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── P3: TOCTOU / reincarnation ───


def test_p3_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P3)" in src


def test_p3_add_and_update_use_begin_immediate():
    src = _read("utils/device_database.py")
    _add_idx = src.index("def add_device(")
    _add_end = src.index("def update_device(", _add_idx)
    _add_body = src[_add_idx:_add_end]
    assert "BEGIN IMMEDIATE" in _add_body
    # IntegrityError handled specifically (not just generic Exception)
    assert "sqlite3.IntegrityError" in _add_body

    _upd_idx = src.index("def update_device(")
    _upd_end = src.index("def _extract_column_values", _upd_idx) if "_extract_column_values" in src[_upd_idx:] else _upd_idx + 6000
    _upd_body = src[_upd_idx:_upd_end]
    assert "BEGIN IMMEDIATE" in _upd_body


def test_p3_update_device_does_not_reincarnate_deleted():
    """update_device on a device that was deleted must return False,
    NOT re-create it via add_device fallback."""
    src = _read("utils/device_database.py")
    _upd_idx = src.index("def update_device(")
    _upd_end = src.index("def _extract_column_values", _upd_idx) if "_extract_column_values" in src[_upd_idx:] else _upd_idx + 6000
    _upd_body = _strip_comments(src[_upd_idx:_upd_end])
    # The old fallback line is gone
    assert "return self.add_device(device_data)" not in _upd_body, (
        "update_device still reincarnates deleted devices via "
        "add_device fallback — P3 fix regressed"
    )


def test_p3_runtime_update_after_delete_returns_false():
    from utils.device_database import DeviceDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = DeviceDatabase(db_path=_p)
        db.init_database()
        db.add_device({
            "device_id": "gone",
            "device_name": "g",
            "interface": "eth0",
        })
        # Delete the device
        db.remove_device("gone")
        # update_device must NOT re-create it
        _ok = db.update_device("gone", {"device_name": "resurrected"})
        assert _ok is False, (
            f"expected False from update_device on deleted device; "
            f"got {_ok}"
        )
        # Verify the row didn't come back
        assert db.get_device("gone") is None
    finally:
        Path(_p).unlink(missing_ok=True)


def test_p3_runtime_concurrent_add_no_dupes():
    """20 concurrent add_device calls for the same device_id must
    all succeed (via race → update_device fallback) and the row
    must exist exactly once."""
    from utils.device_database import DeviceDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = DeviceDatabase(db_path=_p)
        db.init_database()
        _errs = []
        _results = []

        def _worker():
            try:
                ok = db.add_device({
                    "device_id": "race-dev",
                    "device_name": "r",
                    "interface": "eth0",
                })
                _results.append(ok)
            except Exception as e:
                _errs.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not _errs, f"race raised: {_errs[:3]}"
        assert all(_results), (
            f"some add_device returned False: {_results}"
        )
        rows = db.get_all_devices()
        assert len(rows) == 1
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── P4: read-side datetime ───


def test_p4_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P4)" in src


def test_p4_read_side_uses_iso_cutoff():
    src = _read("utils/device_database.py")
    # get_device_statistics
    _idx = src.index("def get_device_statistics")
    _end = src.index("def backup_database", _idx)
    body = _strip_comments(src[_idx:_end])
    assert "datetime('now'" not in body
    assert "timedelta(hours=hours)" in body

    # get_database_info recent_events_24h
    _idx2 = src.index("Get recent events count")
    body2 = _strip_comments(src[_idx2:_idx2 + 800])
    assert "datetime('now'" not in body2
    assert "timedelta(hours=24)" in body2


# ─── P5: get_all_devices LIMIT ───


def test_p5_marker_present():
    src = _read("utils/device_database.py")
    assert "v0.5.399 (audit device-db P5" in src


def test_p5_get_all_devices_has_limit_param():
    src = _read("utils/device_database.py")
    _idx = src.index("def get_all_devices(")
    _end = src.index("def get_devices_by_interface", _idx)
    body = src[_idx:_end]
    assert "limit: int = 1000" in body
    assert "LIMIT" in body


def test_p5_runtime_get_all_devices_caps_at_limit():
    from utils.device_database import DeviceDatabase
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        _p = f.name
    try:
        db = DeviceDatabase(db_path=_p)
        db.init_database()
        for _i in range(10):
            db.add_device({
                "device_id": f"d{_i:03d}",
                "device_name": f"n{_i}",
                "interface": "eth0",
            })
        # Default cap is 1000 so all 10 come back
        assert len(db.get_all_devices()) == 10
        # Explicit tiny cap
        assert len(db.get_all_devices(limit=3)) == 3
    finally:
        Path(_p).unlink(missing_ok=True)


# ─── version guard ───


def test_pyproject_at_least_0599():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 399)


# ─── regression guards ───


def test_v0398_stream_database_o1_intact():
    src = _read("utils/stream_database.py")
    assert "v0.5.398 (audit stream-db O1)" in src


def test_v0398_stream_database_o3_upsert_intact():
    src = _read("utils/stream_database.py")
    assert "ON CONFLICT(stream_id) DO UPDATE SET" in src


def test_v0397_find_or_add_stream_intact():
    src = _read("multithreaded_traffic_gen.py")
    assert "def find_or_add_stream(self, stream):" in src


def test_v0396_launch_reservations_intact():
    src = _read("run_tgen_server.py")
    assert "_launch_reservations = set()" in src
