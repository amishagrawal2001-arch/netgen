"""v0.5.58 — bind-history + dpdk-interfaces registry get atomic
writes + a shared lock. Audit findings M1 + M5.

M1: `_save_bind_history` used plain `open("w") + json.dump`.
    Two concurrent POSTs to /api/admin/bind_history (operator UI
    + scripted client) raced:
        thread A: open("w") truncates file
        thread B: open("r") reads — 0-byte file
        thread A: json.dump completes
    Result: thread B got `{}` instead of the real history; on
    the next rebind boot the original-driver memory was gone.

M5: `_dpdk_persist_bind` / `_dpdk_unpersist_bind` had atomic
    write (.tmp + os.replace) but NO lock. Two concurrent
    /api/dpdk/bind calls (admin + scripted) read-modify-write
    against the same file — one's bind got lost.

Fix: single module-level `threading.Lock()` (`_BIND_REGISTRY_LOCK`)
held across read+modify+write in all 4 sites. The bind-history
file also gets atomic write (.tmp + os.replace) to match the
dpdk-interfaces.json pattern.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_shared_lock_declared():
    """Single Lock used by all four functions. Pre-fix there was
    none at all; the wrong fix would be a per-function lock that
    doesn't synchronise across sites."""
    src = _src()
    assert re.search(
        r"_BIND_REGISTRY_LOCK\s*=\s*\w*threading\w*\.Lock\(\)",
        src,
    ) or re.search(
        r"_BIND_REGISTRY_LOCK\s*=\s*_threading[_a-z]*\.Lock\(\)",
        src,
    ), (
        "No shared threading.Lock() for the bind registries — "
        "concurrent writes still race."
    )


def test_save_bind_history_uses_atomic_write():
    """`_save_bind_history` must write to a temp file and
    os.replace into place — not open the destination directly."""
    src = _src()
    m = re.search(
        r"def _save_bind_history\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    # No more plain `open(_ADMIN_BIND_HISTORY_PATH, "w")`.
    assert not re.search(
        r'open\(_ADMIN_BIND_HISTORY_PATH,\s*[\"\']w[\"\']',
        body,
    ), (
        "_save_bind_history still opens the destination directly "
        "for writing — atomic-write missing"
    )
    # And the tmp + os.replace pattern is present.
    assert re.search(r"\.tmp[\"']?", body), (
        "_save_bind_history doesn't reference a .tmp staging file"
    )
    assert "os.replace" in body, (
        "_save_bind_history doesn't os.replace the .tmp into place"
    )


def test_save_bind_history_takes_the_lock():
    """The save function must hold the registry lock."""
    src = _src()
    m = re.search(
        r"def _save_bind_history\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_BIND_REGISTRY_LOCK" in body, (
        "_save_bind_history doesn't hold the lock — concurrent "
        "POSTs still race"
    )


def test_load_bind_history_takes_the_lock():
    """The load path must hold the lock too — otherwise a load
    can happen mid-save (between truncate and finish on legacy
    pre-fix systems, or between os.replace's tmp-write and
    rename in theory)."""
    src = _src()
    m = re.search(
        r"def _load_bind_history\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_BIND_REGISTRY_LOCK" in body, (
        "_load_bind_history doesn't hold the lock — a load can "
        "race with an in-flight save"
    )


def test_persist_bind_takes_the_lock():
    """`_dpdk_persist_bind` read-modify-write block must hold
    the lock so two concurrent binds don't lose each other's
    entries."""
    src = _src()
    m = re.search(
        r"def _dpdk_persist_bind\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_BIND_REGISTRY_LOCK" in body, (
        "_dpdk_persist_bind doesn't hold the lock — two binds "
        "in quick succession can lose one entry."
    )


def test_unpersist_bind_takes_the_lock():
    """Mirror of persist for the unbind path."""
    src = _src()
    m = re.search(
        r"def _dpdk_unpersist_bind\([\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_BIND_REGISTRY_LOCK" in body, (
        "_dpdk_unpersist_bind doesn't hold the lock — concurrent "
        "unbind+bind can lose one operation."
    )


def test_pyproject_version_at_least_0558():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 58), (
        f"Version {m.group(1)} < 0.5.58"
    )
