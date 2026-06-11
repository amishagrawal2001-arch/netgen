"""v0.5.97 — audit batch 6: operability fortification.

  H11 / M9 — Per-iface lock missing. Two operators in two
        browser tabs both clicking Reset on ens6np0 could
        interleave the down→sleep→up sequences. New
        `_iface_lifecycle_lock(iface)` returns a non-blocking
        Lock per iface; second caller gets 409 + IFACE_BUSY.

  H12 — `_ADMIN_BIND_HISTORY_PATH = "/tmp/..."` died on every
        reboot. Bind audit trail (the only persistent record
        of who bound what) vanished. New path is
        /var/lib/netgen-server/admin_bind_history.json with
        /tmp/ fallback for unprivileged deployments.

  L4 — Flash Popen was orphaned. CPython reaps opportunistically
        on subsequent Popen calls, but between calls the zombie
        sits in the process table. Spawn a `threading.Thread(
        target=p.wait, daemon=True)` to reap immediately.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── H11/M9: per-iface lock ───


def test_iface_lifecycle_lock_helper_defined():
    src = _src()
    assert "_IFACE_LIFECYCLE_LOCKS" in src
    assert "_IFACE_LIFECYCLE_LOCKS_MUTEX" in src
    assert "def _iface_lifecycle_lock(" in src


def test_lifecycle_helper_returns_per_iface_lock():
    src = _src()
    m = re.search(
        r"def _iface_lifecycle_lock[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Dict keyed by iface name; double-checked-locking.
    assert "_IFACE_LIFECYCLE_LOCKS_MUTEX" in body
    assert "threading.Lock()" in body


def test_up_endpoint_uses_per_iface_lock():
    src = _src()
    m = re.search(
        r"def api_admin_iface_up\([\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_iface_lifecycle_lock(iface)" in body
    assert "acquire(blocking=False)" in body
    assert "IFACE_BUSY" in body


def test_down_endpoint_uses_per_iface_lock():
    src = _src()
    m = re.search(
        r"def api_admin_iface_down\([\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_iface_lifecycle_lock(iface)" in body
    assert "IFACE_BUSY" in body


def test_reset_endpoint_uses_per_iface_lock():
    src = _src()
    m = re.search(
        r"def api_admin_iface_reset\([\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Lock wraps the entire down→sleep→up sequence.
    assert "_iface_lifecycle_lock(iface)" in body
    assert "IFACE_BUSY" in body


# ─── H12: bind_history → /var/lib ───


def test_bind_history_primary_path_is_persistent():
    src = _src()
    assert (
        '_ADMIN_BIND_HISTORY_PATH = "/var/lib/netgen-server/'
        in src
    ), "bind_history primary path is not under /var/lib"
    # /tmp/ stays as fallback for unprivileged deployments.
    assert "_ADMIN_BIND_HISTORY_FALLBACK" in src
    assert '/tmp/' in src  # the fallback constant


def test_bind_history_path_helper_probes_writability():
    src = _src()
    m = re.search(
        r"def _admin_bind_history_path[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "os.makedirs" in body
    # Falls back gracefully on PermissionError.
    assert "PermissionError" in body
    assert "_ADMIN_BIND_HISTORY_FALLBACK" in body


def test_load_bind_history_tries_both_paths():
    src = _src()
    m = re.search(
        r"def _load_bind_history[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_ADMIN_BIND_HISTORY_PATH" in body
    assert "_ADMIN_BIND_HISTORY_FALLBACK" in body


def test_save_bind_history_uses_path_helper():
    src = _src()
    m = re.search(
        r"def _save_bind_history[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_admin_bind_history_path()" in body


# ─── L4: Flash Popen reap ───


def test_flash_popen_reaped_in_background_thread():
    src = _src()
    m = re.search(
        r"def api_admin_iface_flash[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Spawn + thread to wait + reap.
    assert "threading.Thread" in body
    assert "daemon=True" in body
    assert ".wait" in body


# ─── Integration: per-iface lock blocks concurrent action ───


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    flask = pytest.importorskip("flask")
    import os
    tmp = tmp_path_factory.mktemp("netgen_db")
    os.environ["NETGEN_DB_PATH"] = str(tmp / "device.db")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_tgen_server", str(_SERVER),
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.app.config["TESTING"] = True
    return server.app.test_client(), server


def test_concurrent_up_returns_busy(client):
    """Second iface_up call on the same iface (while the first
    holds the lock) returns 409 IFACE_BUSY."""
    c, server = client
    # Grab the per-iface lock manually + acquire it.
    # Keep under IFNAMSIZ-1 (15 chars).
    target_iface = "iface_busy"
    lock = server._iface_lifecycle_lock(target_iface)
    assert lock.acquire(blocking=False)
    try:
        # Now any handler call for this iface returns 409.
        r = c.post(f"/api/admin/iface/{target_iface}/up")
        assert r.status_code == 409
        body = r.get_json()
        assert body["code"] == "IFACE_BUSY"
    finally:
        lock.release()


def test_different_ifaces_dont_block_each_other(client):
    """Lock on ens6np0 doesn't affect ens6np1."""
    c, server = client
    from unittest import mock
    lock_a = server._iface_lifecycle_lock("iface_a")
    assert lock_a.acquire(blocking=False)
    try:
        with mock.patch.object(
            server, "_run_ip_link",
            return_value=(True, "", ""),
        ):
            # iface_b should NOT be blocked.
            r = c.post("/api/admin/iface/iface_b/up")
        assert r.status_code == 200
    finally:
        lock_a.release()


# ─── Version ───


def test_pyproject_version_at_least_0597():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 97)
