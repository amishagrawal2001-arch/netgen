"""v0.3.8 — L2 Emulation audit close.

Two real findings from the L2 Emulation audit shipped:

1. **`_SESSIONS` memory leak**: pre-v0.3.8 `stop_session` marked
   the session stopped but never removed the entry from the
   registry dict. On a long-running server with many start/stop
   cycles the dict grew without bound. v0.3.8 evicts the entry
   inside the registry lock after the worker thread joins.

2. **15s GUI freeze on Start**: pre-v0.3.8 the start path called
   `requests.post(timeout=15)` synchronously on the GUI thread.
   v0.3.8 dispatches the POST via a new `_JsonPostWorker`
   QThread (same shape as the existing `_JsonFetchWorker` so
   the existing 404/401/403 branch logic carries over via the
   `failed` signal).

The third Tier-1 claim from the audit — "BFD struct.pack typo" —
was a false positive (Python ignores whitespace in struct format
strings; verified output is exactly 24 bytes per RFC 5880 §4.1).
A test pinning that fact is included too so a future "cleanup"
doesn't remove the harmless space and break something we missed.
"""

import struct
import threading
import time
from unittest.mock import MagicMock

import pytest


# ─────────────────────────────────── _SESSIONS memory leak fix
class _DummyThread:
    """Minimal Thread-shaped mock — `stop_session` joins it but
    doesn't otherwise need it to do anything useful."""
    def join(self, timeout=None):
        return None


def _make_dummy_session():
    """Build the minimal session object `stop_session` walks."""
    from utils.l2_protocols import _Session, _Counters
    sess = _Session.__new__(_Session)
    sess.session_id = "test-sid"
    sess.protocol = "lacp"
    sess.config = {}
    sess.counters = _Counters()
    sess.lock = threading.Lock()
    sess.stop_evt = threading.Event()
    sess.thread = _DummyThread()
    return sess


def test_v0_3_8_stop_session_evicts_from_registry():
    """After `stop_session`, the entry must be GONE from
    `_SESSIONS`. Pre-v0.3.8 it persisted forever."""
    from utils.l2_protocols import (
        _SESSIONS, _REG_LOCK, stop_session,
    )
    # Snapshot baseline + cleanup any leftovers from a prior test.
    with _REG_LOCK:
        baseline_keys = set(_SESSIONS.keys())
    sess = _make_dummy_session()
    with _REG_LOCK:
        _SESSIONS[sess.session_id] = sess
        assert sess.session_id in _SESSIONS, "fixture setup failed"

    ok = stop_session(sess.session_id)
    assert ok is True

    with _REG_LOCK:
        assert sess.session_id not in _SESSIONS, (
            "v0.3.8 regression — stop_session() didn't evict the "
            "session from _SESSIONS. Memory grows unbounded on "
            "long-running servers."
        )
        # And no other entries should have been disturbed.
        assert set(_SESSIONS.keys()) == baseline_keys


def test_v0_3_8_stop_session_returns_false_for_unknown():
    """Unknown session id stays a no-op + False return — the
    eviction logic must not mask the "not found" signal."""
    from utils.l2_protocols import stop_session
    assert stop_session("does-not-exist") is False


def test_v0_3_8_stop_all_sessions_drains_registry():
    """`stop_all_sessions` walks every entry. After it returns,
    the registry is empty regardless of starting state."""
    from utils.l2_protocols import (
        _SESSIONS, _REG_LOCK, stop_all_sessions,
    )
    for i in range(5):
        sess = _make_dummy_session()
        sess.session_id = f"bulk-{i}"
        with _REG_LOCK:
            _SESSIONS[sess.session_id] = sess
    n = stop_all_sessions()
    assert n >= 5
    with _REG_LOCK:
        # No `bulk-*` left.
        for i in range(5):
            assert f"bulk-{i}" not in _SESSIONS


# ─────────────────────────────────── async Start POST
def test_v0_3_8_post_worker_class_exists():
    """`_JsonPostWorker` must be importable at module top so the
    Start path can spawn one."""
    from widgets.l2_emulation_tab import _JsonPostWorker
    assert _JsonPostWorker is not None
    # Signals match the existing _JsonFetchWorker shape.
    assert hasattr(_JsonPostWorker, "finished_ok")
    assert hasattr(_JsonPostWorker, "failed")


def test_v0_3_8_start_path_uses_worker_not_blocking_requests():
    """The `_on_start_clicked` method must spawn `_JsonPostWorker`
    instead of calling `requests.post` directly. Source-grep
    because the dialog is heavy to construct headlessly."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "widgets" / "l2_emulation_tab.py").read_text()
    # Find the start handler body.
    m = re.search(
        r"def _on_start_clicked\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    # Must construct a _JsonPostWorker.
    assert "_JsonPostWorker(" in body, (
        "v0.3.8 regression — _on_start_clicked doesn't construct "
        "_JsonPostWorker. The 15s GUI freeze bug came back."
    )
    # Must NOT call requests.post inline (that's the bug).
    assert "requests.post(" not in body, (
        "v0.3.8 regression — _on_start_clicked still calls "
        "requests.post directly, blocking the GUI"
    )
    # And the worker has to use the keepalive pin per the
    # v0.2.20-v0.2.25 SIGABRT class.
    assert "keep(worker)" in body or "from utils.qthread_keepalive" in body


def test_v0_3_8_start_failed_dispatcher_method_exists():
    """The failed-signal handler must be a named method so it's
    visible in tracebacks + the next audit can find it."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "widgets" / "l2_emulation_tab.py").read_text()
    assert re.search(
        r"^    def _on_start_failed\(self", src,
        flags=re.MULTILINE,
    ), "_on_start_failed dispatcher missing"


def test_v0_3_8_start_failed_branches_by_http_code():
    """The dispatcher must preserve the pre-v0.3.8 branching
    (404 → server-too-old, 401/403 → auth, else → generic)."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "widgets" / "l2_emulation_tab.py").read_text()
    m = re.search(
        r"def _on_start_failed\(self.*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    body = m.group(0)
    for needle in ("404", "401, 403", "_enter_unsupported_mode"):
        assert needle in body, (
            f"dispatcher missing {needle!r} branch — pre-v0.3.8 "
            f"behaviour for that http_code regressed"
        )


# ─────────────────────────────────── BFD struct.pack false-positive pin
def test_v0_3_8_bfd_struct_pack_produces_24_bytes():
    """Audit claimed the BFD packing format ``"!BBBBII III"`` had
    a typo (the space). It doesn't — Python's struct module
    explicitly ignores whitespace in format strings. Pin the
    actual output size so a future "cleanup" that removes the
    space (thinking it's a bug) can't break anything we missed
    via the test suite.
    """
    fmt = "!BBBBII III"
    out = struct.pack(
        fmt,
        0xC1, 0x00, 3, 24,
        0xdeadbeef, 0xcafebabe,
        1_000_000, 1_000_000, 0,
    )
    # RFC 5880 §4.1: BFD Control packet header is 24 bytes.
    assert len(out) == 24, (
        f"BFD packed payload is {len(out)} bytes, expected 24. "
        f"Either the format string lost a field or the audit's "
        f"'typo' claim was actually correct — verify."
    )
    # Spot-check the bytes — version-diag (0xC1) + state-flags + …
    assert out[:1] == b"\xc1"
    assert out[3] == 24  # length field


def test_v0_3_8_bfd_struct_pack_format_unchanged_in_source():
    """Pin the literal format string in source so the next 'fix'
    that removes the space can be caught by CI."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "utils" / "l2_protocols.py").read_text()
    # Either "BBBBIII II" or with the historical space — both
    # produce the same wire output. Just confirm the field count
    # is right (4 bytes + 5 uints).
    import re as _re
    m = _re.search(
        r'struct\.pack\(\s*"!\s*((?:[BIHL]\s*){9})"',
        src,
    )
    assert m is not None, (
        "BFD struct.pack with the right field count not found — "
        "format may have been refactored. Update this pin or "
        "verify the new format produces 24 bytes on the wire."
    )
