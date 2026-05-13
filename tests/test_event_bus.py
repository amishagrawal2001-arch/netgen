"""Unit tests for the in-process pub/sub event bus that backs SSE.

Tests the producer / consumer contract without spinning up Flask:
* publish() returns 0 when no subscribers exist (cheap-when-quiet)
* publish() delivers to every active subscriber
* Subscriber bounded queue drops oldest on overflow (backpressure)
* unsubscribe() removes the subscriber cleanly
* iter_events() emits heartbeats during idle windows
"""

import queue
import threading
import time

import pytest

from utils import event_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    """Snapshot + restore the bus state so tests don't interfere with
    each other (the module's _SUBSCRIBERS list is process-global)."""
    with event_bus._LOCK:
        original = list(event_bus._SUBSCRIBERS)
        event_bus._SUBSCRIBERS.clear()
    yield
    with event_bus._LOCK:
        event_bus._SUBSCRIBERS.clear()
        event_bus._SUBSCRIBERS.extend(original)


def test_publish_with_no_subscribers_returns_zero():
    """The cheap-when-quiet path is critical — monitors call publish
    on every poll, so an empty subscribe list must be free."""
    assert event_bus.publish("state_transition", {"x": 1}) == 0
    assert event_bus.subscriber_count() == 0


def test_publish_delivers_to_every_subscriber():
    s1 = event_bus.subscribe()
    s2 = event_bus.subscribe()
    delivered = event_bus.publish("state_transition", {"device_id": "d1"})
    assert delivered == 2
    e1 = s1.q.get(timeout=0.1)
    e2 = s2.q.get(timeout=0.1)
    assert e1["event_type"] == "state_transition"
    assert e1["device_id"] == "d1"
    assert e2["device_id"] == "d1"
    # Both saw the same event (different copies / same data)
    event_bus.unsubscribe(s1)
    event_bus.unsubscribe(s2)


def test_envelope_carries_event_type_and_timestamp():
    """publish injects event_type + ts into every payload so
    consumers don't have to look up two fields separately."""
    s = event_bus.subscribe()
    before = time.time()
    event_bus.publish("test_event", {"value": 42})
    after = time.time()
    e = s.q.get(timeout=0.1)
    assert e["event_type"] == "test_event"
    assert e["value"] == 42
    assert before <= e["ts"] <= after
    event_bus.unsubscribe(s)


def test_overflow_drops_oldest():
    """Slow consumer → bounded queue → oldest events drop, not newest.
    Critical to prove the producer never blocks under load."""
    s = event_bus._Subscriber(maxsize=3)
    with event_bus._LOCK:
        event_bus._SUBSCRIBERS.append(s)
    for i in range(10):
        event_bus.publish("burst", {"i": i})
    # Consumer drains in order — should see the LAST 3 events, not the first.
    seen = []
    while True:
        try:
            seen.append(s.q.get_nowait()["i"])
        except queue.Empty:
            break
    assert len(seen) == 3
    assert seen == sorted(seen), "events should remain in insertion order"
    # The last 3 should be the survivors (oldest dropped).
    assert seen[-1] == 9


def test_unsubscribe_stops_delivery():
    s = event_bus.subscribe()
    event_bus.unsubscribe(s)
    delivered = event_bus.publish("after_unsubscribe", {})
    assert delivered == 0
    # double-unsubscribe is a no-op, not a raise
    event_bus.unsubscribe(s)


def test_iter_events_emits_heartbeat_when_idle():
    """The iter_events generator emits a synthetic heartbeat after
    heartbeat_interval seconds with no real events — keeps the SSE
    wire warm so proxies don't time out a slow fabric."""
    s = event_bus.subscribe()
    gen = event_bus.iter_events(s, heartbeat_interval=0.2)
    # First event should be a heartbeat (no other events published).
    event = next(gen)
    assert event["event_type"] == "heartbeat"
    # Now publish a real event — next iter should yield it.
    event_bus.publish("real", {"v": 1})
    event = next(gen)
    assert event["event_type"] == "real"
    assert event["v"] == 1
    gen.close()


def test_iter_events_unsubscribes_on_generator_close():
    """When the SSE consumer disconnects, the generator's finally:
    clause must unregister the subscriber so the list doesn't grow
    unbounded over a long-running server. Iterate once before closing
    so the try: block actually enters — gen.close() on a never-started
    generator skips finally entirely (Python generator semantics), but
    in production Flask always yields the initial `: ok` comment line
    before any disconnect can happen."""
    initial = event_bus.subscriber_count()
    s = event_bus.subscribe()
    assert event_bus.subscriber_count() == initial + 1
    gen = event_bus.iter_events(s, heartbeat_interval=0.1)
    # Drive one iteration so finally: gets armed, then disconnect.
    next(gen)
    gen.close()
    assert event_bus.subscriber_count() == initial
