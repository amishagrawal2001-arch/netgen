"""v0.5.99 — fix: start_stream looked up the wrong stream and
cross-contaminated stream_id across all same-name streams.

Operator:

  "trying to run two streams, when trying to start the selected
   stream both the stream are starting together. check start
   and stop selected stream."

Root cause (two interacting bugs):

1. `start_stream` matched the selected row by NAME only:

       matched_stream = next(
           (s for s in self.streams.get(port_key, [])
            if s.get("name") == stream_name or ...),
           None
       )

   `next(...)` picks the FIRST stream with that name. If two
   streams on the same port shared a name, the operator-
   selected row 1 could yield row 0's stream object.

2. The sync block that ran AFTER the lookup forced every
   same-name stream on the port to take the new stream_id:

       for s in self.streams.get(port_key, []):
           if s.get("name") == matched_stream.get("name"):
               s["stream_id"] = stream_id   # cross-contamination

   With both streams now sharing one stream_id, the stats-poll
   path bound BOTH rows to the single server-side stream and
   both rows appeared to be running — exactly what the
   operator saw.

Fix — mirror what `stop_stream` has done since v0.2.84:

- Look up `name_item.data(Qt.UserRole)` for the unique
  stream_id stashed on the table cell when the row was built.
- Fall back to name match only if the cell has no stream_id
  (legacy rows / imported configs).
- Sync ONLY the matched_stream object, not every same-name
  sibling.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_LOGIC = _REPO / "traffic_client" / "stream_logic.py"


def _src() -> str:
    return _LOGIC.read_text()


def _start_stream_body() -> str:
    src = _src()
    idx = src.find("def start_stream(self):")
    assert idx > 0
    # Bound on the next top-level def.
    rest = src[idx:]
    next_def = re.search(r"\n    def [a-z_]+\(", rest[100:])
    end = (100 + next_def.start()) if next_def else len(rest)
    return rest[:end]


# ─── Stream_id-first lookup ───


def test_start_stream_reads_stream_id_from_table_cell():
    body = _start_stream_body()
    assert "name_item.data(Qt.UserRole)" in body, (
        "start_stream still doesn't pull the unique stream_id "
        "stashed in the table cell — same bug as pre-fix"
    )


def test_start_stream_uses_stream_id_match_before_name_fallback():
    body = _start_stream_body()
    # The stream_id branch must come BEFORE the name-only next().
    sid_block_idx = body.find("stream_id_from_table")
    next_call_idx = body.find('s.get("name") == stream_name')
    assert sid_block_idx > 0
    assert next_call_idx > sid_block_idx, (
        "stream_id match must run before the name fallback"
    )


def test_start_stream_name_fallback_only_runs_when_id_missing():
    body = _start_stream_body()
    # The name-based `next(...)` block should be reached only
    # if matched_stream is still None from the id branch.
    pre_name_idx = body.find('if not matched_stream:')
    name_match_idx = body.find('s.get("name") == stream_name')
    assert pre_name_idx > 0 and name_match_idx > pre_name_idx, (
        "Name fallback must be gated on `if not matched_stream`"
    )


# ─── Sync block no longer cross-contaminates ───


def test_sync_block_does_not_loop_all_same_name_streams():
    body = _start_stream_body()
    # Pre-fix had:
    #   for s in self.streams.get(port_key, []):
    #       if s.get("name") == matched_stream.get("name"):
    #           s["stream_id"] = stream_id
    # Post-fix should NOT contain a loop that writes stream_id
    # to multiple objects matching by name.
    bad_pattern = re.search(
        r"for s in self\.streams\.get\(port_key, \[\]\)"
        r"[\s\S]{0,200}?"
        r's\["stream_id"\]\s*=\s*stream_id',
        body,
    )
    assert not bad_pattern, (
        "Sync block still loops same-name streams and overwrites "
        "stream_id on all of them — would re-introduce the bug"
    )


def test_sync_block_only_touches_matched_stream():
    body = _start_stream_body()
    # Look for direct assignment to matched_stream (not the loop).
    assert 'matched_stream["interface"] = normalized_interface' in body
    assert 'matched_stream["stream_id"] = stream_id' in body


# ─── stop_stream uses the same pattern (regression guard) ───


def test_stop_stream_still_uses_stream_id_first():
    src = _src()
    idx = src.find("def stop_stream(self):")
    assert idx > 0
    body = src[idx:idx + 6000]
    assert "name_item.data(Qt.UserRole)" in body
    # Both handlers should now use the SAME lookup pattern;
    # they're symmetric.


# ─── Version ───


def test_pyproject_version_at_least_0599():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 99)
