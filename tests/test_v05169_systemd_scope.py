"""v0.5.169: systemd-scope wrap on tx/rx_worker spawns + status-bar
orphan chip.

v0.5.168 made orphans visible + reapable. v0.5.169 closes the loop:
  * Each tx/rx_worker spawn goes through
    `systemd-run --scope --unit=netgen-{tx,rx}-<sid>.scope`. The
    kernel guarantees lifecycle tracking — a stop is signal
    delivery via cgroup, no PID guessing, no race.
  * Status-bar `🧟 N orphans` chip polls every TG every 10 s and
    surfaces orphans without the operator needing to click Stop
    All to discover them.

Tests cover both.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils import systemd_scope


# ───── systemd_scope pure helpers ────────────────────────────────


def test_sanitise_unit_name_uses_role_and_stream_id():
    name = systemd_scope.sanitise_unit_name(
        "tx", "3ede73ca-79a1-4d1e-adac-e1aa85662fed")
    assert name == "netgen-tx-3ede73ca-79a1-4d1e-adac-e1aa85662fed"


def test_sanitise_unit_name_strips_unsafe_chars():
    name = systemd_scope.sanitise_unit_name(
        "rx", "weird/id with spaces!")
    # Only [A-Za-z0-9-] survives.
    assert all(c.isalnum() or c == "-" for c in name)
    assert name.startswith("netgen-rx-")


def test_sanitise_unit_name_caps_at_200():
    long_id = "a" * 500
    name = systemd_scope.sanitise_unit_name("tx", long_id)
    assert len(name) <= 200


def test_sanitise_unit_name_handles_empty_role_and_id():
    """Defensive against accidental None/empty — must not raise."""
    n = systemd_scope.sanitise_unit_name("", "")
    assert n.startswith("netgen-")


def test_build_prefix_empty_when_systemd_run_absent():
    """On hosts without systemd-run (macOS dev box, container),
    the prefix must be empty so the naked Popen fallback works."""
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which", return_value=None):
        prefix = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id="abc-123")
        assert prefix == []
    systemd_scope._reset_cache_for_tests()


def test_build_prefix_includes_scope_and_unit_when_available():
    """On a systemd host, the prefix must include --scope, --collect,
    --unit=netgen-tx-<id>, --quiet. All three flags matter:
      * --scope   keeps the unit hierarchy flat (no service layer)
      * --collect garbage-collects the unit when the scope exits
      * --unit    makes the unit name predictable
      * --quiet   stops the "Running scope as unit ..." log spam
    """
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which",
                    return_value="/usr/bin/systemd-run"):
        prefix = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id="3ede73ca-79a1-4d1e-adac-e1aa85662fed")
    systemd_scope._reset_cache_for_tests()
    assert "/usr/bin/systemd-run" in prefix
    assert "--scope" in prefix
    assert "--collect" in prefix
    assert "--quiet" in prefix
    unit_args = [a for a in prefix if a.startswith("--unit=")]
    assert len(unit_args) == 1
    assert "netgen-tx-3ede73ca-79a1-4d1e-adac-e1aa85662fed" \
        in unit_args[0]


def test_build_prefix_accepts_extra_properties():
    """Operators sometimes want to constrain a worker
    (MemoryMax, CPUQuota). The prefix builder must pass through
    `-p key=value` pairs."""
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which",
                    return_value="/usr/bin/systemd-run"):
        prefix = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id="abc",
            extra_properties=["MemoryMax=8G", "CPUQuota=400%"],
        )
    systemd_scope._reset_cache_for_tests()
    joined = " ".join(prefix)
    assert "-p MemoryMax=8G" in joined
    assert "-p CPUQuota=400%" in joined


def test_build_prefix_sudo_skipped_when_root():
    """sudo is only injected when the caller isn't already root.
    On a root server (systemd target), `sudo` would just be an
    extra fork — unnecessary."""
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which",
                    return_value="/usr/bin/systemd-run"), \
         mock.patch("os.geteuid", return_value=0):
        prefix = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id="abc", use_sudo=True)
    systemd_scope._reset_cache_for_tests()
    assert "sudo" not in prefix


def test_build_prefix_sudo_added_when_non_root():
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which",
                    return_value="/usr/bin/systemd-run"), \
         mock.patch("os.geteuid", return_value=1000):
        prefix = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id="abc", use_sudo=True)
    systemd_scope._reset_cache_for_tests()
    assert prefix[:2] == ["sudo", "--non-interactive"]


def test_stop_scope_returns_false_when_systemd_run_missing():
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which", return_value=None):
        ok = systemd_scope.stop_scope_for_stream("tx", "abc")
    systemd_scope._reset_cache_for_tests()
    assert ok is False


def test_list_netgen_scopes_returns_empty_without_systemd():
    systemd_scope._reset_cache_for_tests()
    with mock.patch("shutil.which", return_value=None):
        units = systemd_scope.list_netgen_scopes()
    systemd_scope._reset_cache_for_tests()
    assert units == []


# ───── Spawn-site wiring (source-level) ──────────────────────────


def test_tx_worker_spawn_uses_systemd_scope():
    src = (REPO / "utils" / "dpdk_tx_worker.py").read_text()
    # The systemd_scope import must precede the Popen call.
    popen_idx = src.find("subprocess.Popen")
    assert popen_idx > 0
    above = src[:popen_idx]
    assert "from utils import systemd_scope" in above
    assert "systemd_scope.build_systemd_run_prefix" in above
    assert 'role="tx"' in above
    assert "stream_id=stream_id" in above


def test_rx_worker_spawn_uses_systemd_scope():
    src = (REPO / "utils" / "dpdk_rx_worker.py").read_text()
    # Skip the dataclass field type annotation `proc: subprocess.Popen`
    # — we want the actual call.
    popen_idx = src.find("subprocess.Popen(")
    assert popen_idx > 0
    above = src[:popen_idx]
    assert "from utils import systemd_scope" in above
    assert "systemd_scope.build_systemd_run_prefix" in above
    assert 'role="rx"' in above


def test_tx_worker_multi_spawn_uses_systemd_scope():
    src = (REPO / "utils" / "dpdk_tx_worker_multi.py").read_text()
    popen_idx = src.find("subprocess.Popen")
    assert popen_idx > 0
    above = src[:popen_idx]
    assert "from utils import systemd_scope" in above
    # Multi-instance: each instance gets its own scope, so stream_id
    # is `instance_id`, not the parent stream_id.
    assert "stream_id=instance_id" in above


def test_spawn_falls_back_when_systemd_run_missing():
    """The wrapper is import-guarded so a broken systemd_scope or
    missing module doesn't break the worker. Verify each spawn
    site has a try/except wrapper."""
    for rel in ("utils/dpdk_tx_worker.py",
                "utils/dpdk_rx_worker.py",
                "utils/dpdk_tx_worker_multi.py"):
        src = (REPO / rel).read_text()
        idx = src.find("systemd_scope.build_systemd_run_prefix")
        assert idx > 0, f"{rel}: no scope call found"
        # The line above the call must contain `try:` within a small
        # window (we wrap the import + call in a defensive try/except).
        window = src[max(0, idx - 200):idx]
        assert "try:" in window, (
            f"{rel}: scope call not wrapped in try/except")


# ───── Orphan chip (source-level + widget construction) ──────────


def test_orphan_chip_module_exports_chip_and_dialog():
    from widgets.orphan_chip import OrphanChip, OrphanReapDialog
    assert callable(OrphanChip)
    assert callable(OrphanReapDialog)


def test_orphan_chip_hidden_when_no_orphans(qtbot=None):
    """An empty server set should keep the chip hidden — operators
    don't want a permanent dead pixel in the status bar."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.orphan_chip import OrphanChip
    chip = OrphanChip(lambda: [], poll_interval_ms=0)
    assert chip.isVisible() is False
    chip.deleteLater()


def test_orphan_chip_shows_count_on_payload():
    """Mock the fetch thread's payload signal — chip should flip
    visible and show the count."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.orphan_chip import OrphanChip
    chip = OrphanChip(lambda: ["http://srv:5050"], poll_interval_ms=0)
    chip._on_payload("http://srv:5050", [
        {"pid": 100, "role": "tx", "stream_id": "abc",
         "bdf": "0000:2b:00.0", "etime_seconds": 13,
         "cmdline": "..."},
        {"pid": 101, "role": "rx", "stream_id": "abc",
         "bdf": "0000:2b:00.1", "etime_seconds": 13,
         "cmdline": "..."},
    ])
    assert chip.isVisible() is True
    assert "2 orphans" in chip.text()
    chip.deleteLater()


def test_orphan_chip_payload_zero_hides_chip():
    """When a fetch returns zero, the chip must clear."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from widgets.orphan_chip import OrphanChip
    chip = OrphanChip(lambda: ["http://srv:5050"], poll_interval_ms=0)
    chip._on_payload("http://srv:5050", [
        {"pid": 100, "role": "tx", "stream_id": "abc"},
    ])
    assert chip.isVisible() is True
    # Now the fetch returns empty.
    chip._on_payload("http://srv:5050", [])
    assert chip.isVisible() is False
    chip.deleteLater()


def test_main_wires_orphan_chip_into_status_bar():
    src = (REPO / "traffic_client" / "main.py").read_text()
    assert "from widgets.orphan_chip import OrphanChip" in src
    assert "self.orphan_chip = OrphanChip(" in src
    assert "addPermanentWidget(self.orphan_chip)" in src


def test_orphan_reap_dialog_posts_correct_endpoint():
    """The dialog must POST to /api/streams/orphans/reap with the
    expected body shape. Source-level — actual HTTP is mocked
    elsewhere."""
    src = (REPO / "widgets" / "orphan_chip.py").read_text()
    assert "/api/streams/orphans/reap" in src
    assert "json={\"pids\":" in src or 'json={"pids":' in src
