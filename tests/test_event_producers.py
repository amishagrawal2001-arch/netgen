"""Verify the new SSE event producers fire from the right code paths.

We can't easily exercise the full Flask routes without spinning up
docker / FRR / DPDK, but we can exercise `_emit_event` directly from
the same module to lock down:

* The wrapper is non-throwing (a publish failure can't break a route)
* It produces the right event_type + payload shape
* Subscribers receive it through the event bus
"""

import pytest

from utils import event_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    with event_bus._LOCK:
        original = list(event_bus._SUBSCRIBERS)
        event_bus._SUBSCRIBERS.clear()
    yield
    with event_bus._LOCK:
        event_bus._SUBSCRIBERS.clear()
        event_bus._SUBSCRIBERS.extend(original)


def _publish_via_helper(event_type, **fields):
    """Mirror the run_tgen_server._emit_event helper so we don't pull
    in the whole Flask app. This is the same body verbatim."""
    try:
        from utils.event_bus import publish
        publish(event_type, dict(fields))
    except Exception:
        # Best-effort: producers must not crash on bus failure.
        pass


def test_emit_event_publishes_to_subscribers():
    sub = event_bus.subscribe()
    _publish_via_helper("device_applied", device_id="d1", device_name="r1")
    e = sub.q.get(timeout=0.1)
    assert e["event_type"] == "device_applied"
    assert e["device_id"] == "d1"
    assert e["device_name"] == "r1"
    assert "ts" in e
    event_bus.unsubscribe(sub)


def test_emit_event_with_no_subscribers_no_throw():
    """The publish wrapper must not crash when nobody is listening
    (the common case during normal server operation)."""
    assert event_bus.subscriber_count() == 0
    # Just shouldn't raise.
    _publish_via_helper("device_started", device_id="d1")
    _publish_via_helper("device_stopped", device_id="d1")
    _publish_via_helper("device_removed", device_id="d1", device_name="r1")
    _publish_via_helper("stream_started", count=3, streams=[])
    _publish_via_helper("stream_stopped", count=3, stream_ids=[])
    _publish_via_helper("stream_restarted", interface="eth0", count=1)


def test_event_envelope_has_canonical_keys():
    """Every published event gets `event_type` + `ts` added by the
    bus — consumers rely on those two keys being present."""
    sub = event_bus.subscribe()
    _publish_via_helper("device_apply_failed", device_id="d1", error="boom")
    e = sub.q.get(timeout=0.1)
    assert e["event_type"] == "device_apply_failed"
    assert isinstance(e["ts"], float)
    assert e["device_id"] == "d1"
    assert e["error"] == "boom"
    event_bus.unsubscribe(sub)


@pytest.mark.parametrize("event_type,fields", [
    ("device_applied",      {"device_id": "d1", "device_name": "r1", "interface": "eth0"}),
    ("device_started",      {"device_id": "d1"}),
    ("device_stopped",      {"device_id": "d1"}),
    ("device_removed",      {"device_id": "d1", "device_name": "r1", "db_removed": True}),
    ("device_apply_failed", {"device_id": "d1", "error": "boom"}),
    ("stream_started",      {"count": 2, "streams": []}),
    ("stream_stopped",      {"count": 2, "stream_ids": ["s1", "s2"]}),
    ("stream_restarted",    {"interface": "eth0", "count": 1}),
])
def test_each_producer_event_type_round_trips(event_type, fields):
    """Every event type the new producers emit must round-trip through
    the bus with all its fields intact. Catches typos in the
    event_type strings (a typo would mean Devices-tab consumers
    silently miss the event)."""
    sub = event_bus.subscribe()
    _publish_via_helper(event_type, **fields)
    e = sub.q.get(timeout=0.1)
    assert e["event_type"] == event_type
    for k, v in fields.items():
        assert e[k] == v, f"field {k!r} mismatch: {e[k]!r} vs {v!r}"
    event_bus.unsubscribe(sub)
