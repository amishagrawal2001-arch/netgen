"""Regression tests for the duplicate-TG-id bug fixed in v0.3.16.

Operator reported: "Tried to add two servers in the TGEN list, both
picked up TG 1." Root cause: every add-server callsite was computing
``tg_id = len(self.server_interfaces)`` which gives the next ARRAY
INDEX, not the next UNIQUE id. Brittle the moment the existing list
isn't 0-indexed contiguous — which happens routinely on session
reload and post-remove states.

User's actual session.json at the time of the report:
    [{"tg_id": 1, "address": "http://svl-d-ai-srv01:5050"}]

After load, adding any new server hit ``len([1]) = 1``, producing a
second entry also with ``tg_id=1`` — both rendered as "TG 1" in the
server tree.

Fix: ``_next_tg_id`` helper computes ``max(existing) + 1`` with
graceful fallbacks. Pinned here so anyone reverting to the simpler
``len()`` formula gets caught immediately."""
from __future__ import annotations

import pytest

from traffic_client.menu_actions import _next_tg_id


def test_empty_list_starts_at_zero():
    """Fresh install — no servers yet. First add should be TG 0."""
    assert _next_tg_id([]) == 0


def test_single_zero_indexed_returns_one():
    """Standard happy path: list has tg_id=0, next is 1."""
    assert _next_tg_id([{"tg_id": 0}]) == 1


def test_user_session_scenario_with_tg_id_1():
    """Verbatim repro of the v0.3.16 bug report.

    The user's session.json held one server with ``tg_id=1`` (not 0
    — likely because an earlier server was removed). Pre-v0.3.16 the
    next add used ``len([{tg_id:1}]) = 1``, producing a duplicate.
    Post-fix it must produce 2."""
    user_state = [{"tg_id": 1, "address": "http://svl-d-ai-srv01:5050"}]
    assert _next_tg_id(user_state) == 2


def test_post_remove_gap_does_not_collide():
    """Common edit pattern: added two TGs (0, 1), removed the first,
    then added one more. The remaining list is ``[{tg_id:1}]`` and
    the new one must NOT collide on tg_id=1."""
    assert _next_tg_id([{"tg_id": 1}]) == 2


def test_sparse_ids_use_max_plus_one():
    """If for any reason (manual JSON edit, partial cleanup) the list
    contains widely-spaced tg_ids, the next must still be unique."""
    assert _next_tg_id([{"tg_id": 0}, {"tg_id": 1}, {"tg_id": 5}]) == 6


def test_string_tg_id_from_json_coerces_to_int():
    """JSON has no int/string distinction in some serialisers — the
    helper must coerce gracefully."""
    assert _next_tg_id([{"tg_id": "1"}]) == 2


def test_missing_tg_id_key_defaults_to_zero():
    """Defensive: a malformed entry without tg_id is treated as 0 so
    the next ID is 1 (not a crash)."""
    assert _next_tg_id([{"address": "foo"}]) == 1


def test_garbage_tg_id_falls_back_to_zero():
    """Defensive: completely unparseable tg_id (operator hand-edit
    gone wrong) → fall back to 0 rather than ValueError."""
    assert _next_tg_id([{"tg_id": "oops"}]) == 0


def test_multiple_sequential_adds_produce_unique_ids():
    """Simulate the full operator flow: 3 adds in sequence, no removes.
    Each must get a fresh unique tg_id."""
    servers = []
    new_id = _next_tg_id(servers)
    servers.append({"tg_id": new_id})
    assert new_id == 0

    new_id = _next_tg_id(servers)
    servers.append({"tg_id": new_id})
    assert new_id == 1

    new_id = _next_tg_id(servers)
    servers.append({"tg_id": new_id})
    assert new_id == 2

    # Verify uniqueness across the full sequence
    ids = [s["tg_id"] for s in servers]
    assert len(set(ids)) == len(ids), \
        f"duplicate tg_ids in sequence: {ids}"


def test_does_not_renumber_existing_ids():
    """Important: _next_tg_id MUST be additive — it returns a single
    new ID. It must NOT touch the existing entries' tg_ids (that would
    break the session-persistence contract; saved streams + UI state
    reference servers by tg_id)."""
    servers = [{"tg_id": 5}, {"tg_id": 7}]
    snapshot_before = [s.copy() for s in servers]
    _ = _next_tg_id(servers)
    assert servers == snapshot_before, \
        "_next_tg_id must not mutate the input list"
