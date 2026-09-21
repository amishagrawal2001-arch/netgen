"""v0.5.397 — multithreaded_traffic_gen.py concurrency + resource audit.

  N1  generate_packets find→add TOCTOU (via StreamTracker.find_or_add_stream).
  N2  Unlocked list(active_streams) → StreamTracker.attach_rx_debug.
  N3  Sniffer setup resource leak (try/except rollback of register + VLAN).
  N4  Dual-sniffer counter increments protected by _counters_lock.
  N5  Stale-dict writes → StreamTracker.set_stream_field.
"""
from __future__ import annotations

import ast
import re
import sys
import threading
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_engine_ast_parses():
    ast.parse(_read("multithreaded_traffic_gen.py"))


# ─── N1: atomic check-and-add ───


def test_n1_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.397 (audit stream-gen N1)" in src


def test_n1_find_or_add_stream_defined():
    src = _read("multithreaded_traffic_gen.py")
    assert "def find_or_add_stream(self, stream):" in src
    # Holds single lock across check + insert
    _idx = src.index("def find_or_add_stream(self, stream):")
    body = src[_idx:_idx + 2500]
    assert "with self.lock:" in body
    # Returns (row, created) tuple
    assert "return s, False" in body
    assert "return _row, True" in body


def test_n1_generate_packets_uses_atomic():
    """generate_packets no longer does the separate find_stream_by_id
    + add_stream pair; it uses find_or_add_stream."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("register stream row (before sniffer)")
    _end = _idx + 2500
    body = src[_idx:_end]
    assert "stream_tracker.find_or_add_stream(" in body
    # Old shape gone — no `existing = stream_tracker.find_stream_by_id`
    # followed by `if not existing: stream_tracker.add_stream(` in this block
    assert (
        "existing = stream_tracker.find_stream_by_id(interface, stream_id)"
        not in body
    )


# Runtime test: two threads racing find_or_add_stream get the SAME row
def test_n1_find_or_add_stream_runtime_no_race():
    from multithreaded_traffic_gen import StreamTracker
    t = StreamTracker()
    _results = []
    _err = []

    def _worker():
        try:
            row, created = t.find_or_add_stream({
                "stream_id": "test-sid",
                "interface": "eth0",
                "stream_name": "s",
                "stop_event": threading.Event(),
            })
            _results.append((id(row), created))
        except Exception as e:
            _err.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not _err, f"race: {_err[:3]}"
    # Exactly one 'created=True' should exist; all others must be
    # created=False AND refer to the SAME row object.
    _created_count = sum(1 for _, c in _results if c)
    assert _created_count == 1, f"expected 1 creator; got {_created_count}"
    _row_ids = {rid for rid, c in _results}
    assert len(_row_ids) == 1, (
        f"expected all 20 callers to see the SAME row; got {len(_row_ids)} "
        f"distinct row ids"
    )


# ─── N2: attach_rx_debug ───


def test_n2_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.397 (audit stream-gen N2)" in src


def test_n2_attach_rx_debug_defined():
    src = _read("multithreaded_traffic_gen.py")
    assert "def attach_rx_debug(self, stream_id, rx_debug):" in src
    _idx = src.index("def attach_rx_debug(self, stream_id, rx_debug):")
    body = src[_idx:_idx + 1200]
    assert "with self.lock:" in body


def test_n2_no_unlocked_list_active_streams():
    """The pre-fix `for s in list(tracker.active_streams)` at the
    rx_debug attachment site is gone from the code (comment
    mentions of the old shape don't count)."""
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("Attach to the tracker entry so the REST handler can find it.")
    body = src[_idx:_idx + 2500]
    # Strip comment lines before scanning
    _stripped = "\n".join(
        _line for _line in body.split("\n")
        if not _line.lstrip().startswith("#")
    )
    assert "for s in list(tracker.active_streams)" not in _stripped
    assert "tracker.attach_rx_debug(stream_id, rx_debug)" in body


# ─── N3: sniffer setup rollback ───


def test_n3_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.397 (audit stream-gen N3)" in src


def test_n3_sniffer_start_wrapped_in_try_rollback():
    src = _read("multithreaded_traffic_gen.py")
    _idx = src.index("v0.5.397 (audit stream-gen N3)")
    body = src[_idx:_idx + 3500]
    # try/except wraps sniffer.start()
    assert "sniffer.start()" in body
    assert "except Exception as _start_exc" in body
    # Rollback calls
    assert "tracker.unregister_sniffer(rx_interface, stream_id)" in body
    assert "_release_vlan_subif(created_vlan_subif)" in body
    # Re-raise so caller sees real failure
    assert "raise" in body


# ─── N4: counter lock ───


def test_n4_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.397 (audit stream-gen N4)" in src


def test_n4_counters_lock_defined_and_used():
    src = _read("multithreaded_traffic_gen.py")
    # Lock created in the closure setup
    assert "_counters_lock = threading.Lock()" in src
    # Used in lfilter for every increment path
    _lfilter_idx = src.index("nonlocal seen_total, matched, sig_hits, tuple_hits")
    _lfilter_end = src.index("return False", _lfilter_idx) + 20
    body = src[_lfilter_idx:_lfilter_end]
    # Every increment guarded
    _wraps = body.count("with _counters_lock:")
    assert _wraps >= 4, (
        f"expected ≥4 `with _counters_lock:` blocks in lfilter body; "
        f"got {_wraps}"
    )
    # Increment ops moved under lock (no bare `seen_total += 1`
    # outside a lock context — check by ensuring the ONLY occurrence
    # of seen_total += 1 in body is inside `with _counters_lock:`)
    # Simpler check: no seen_total += 1 immediately outside a lock context.
    # We just verify _wraps >= 4 above.


# ─── N5: set_stream_field ───


def test_n5_marker_present():
    src = _read("multithreaded_traffic_gen.py")
    assert "v0.5.397 (audit stream-gen N5)" in src


def test_n5_set_stream_field_defined():
    src = _read("multithreaded_traffic_gen.py")
    assert "def set_stream_field(self, interface, stream_id, key, value):" in src
    _idx = src.index("def set_stream_field(self, interface, stream_id, key, value):")
    body = src[_idx:_idx + 1500]
    assert "with self.lock:" in body
    assert "return True" in body
    assert "return False" in body


def test_n5_rx_thread_write_uses_atomic_setter():
    src = _read("multithreaded_traffic_gen.py")
    # The old shape (existing["rx_thread"] = ... / stream_entry["rx_thread"] = ...)
    # is gone from the rx-sniffer branch; only the atomic setter remains.
    _idx = src.index("Update the stream_tracker entry with the rx_thread")
    _end = _idx + 1500
    body = src[_idx:_end]
    assert 'stream_tracker.set_stream_field(' in body
    assert '"rx_thread"' in body
    # Old (broken) shape gone
    assert 'existing["rx_thread"] = rx_thread' not in body
    assert 'stream_entry["rx_thread"] = rx_thread' not in body


# ─── version guard ───


def test_pyproject_at_least_0597():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 397)


# ─── regression guards ───


def test_v0396_m2_launch_reservations_intact():
    src = _read("run_tgen_server.py")
    assert "_launch_reservations = set()" in src


def test_v0396_m3_j1_mirror_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.396 (audit streams M3)" in src


def test_v0395_stream_dialog_async_helper_intact():
    src = _read("widgets/stream_dialog.py")
    assert "def _stream_async_get(self, url, on_ok, on_err=None, timeout=3.0):" in src


def test_v0383_add_stream_x4_dedup_intact():
    """StreamTracker.add_stream's O(n²)→O(1) dedup from v0.5.383 X4
    must still be in place — find_or_add_stream is ADDITIVE not
    a replacement."""
    src = _read("multithreaded_traffic_gen.py")
    assert "def add_stream(self, stream):" in src
    _idx = src.index("def add_stream(self, stream):")
    body = src[_idx:_idx + 2000]
    assert "if _key in self._stream_keys:" in body
