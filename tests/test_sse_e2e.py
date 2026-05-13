"""End-to-end SSE test through Flask's test_client.

Stands up a minimal Flask app with the events Blueprint registered,
publishes events via the bus, and reads them back through the SSE
endpoint as a streaming HTTP response. Catches integration bugs
between event_bus + Blueprint + Flask streaming that unit tests
on each module can't.

Skipped only if Flask isn't importable — but since the server requires
Flask anyway, that should never happen in a real CI run.
"""

import json
import threading
import time

import pytest

flask = pytest.importorskip("flask")

from server.events_routes import events_bp, configure as configure_events
from utils import event_bus


@pytest.fixture
def app():
    """Build a fresh Flask app per test so subscriber state doesn't
    leak between tests. Also resets the bus' global subscriber list
    so an earlier-test connection doesn't show up here."""
    with event_bus._LOCK:
        event_bus._SUBSCRIBERS.clear()
    a = flask.Flask(__name__)
    configure_events(require_role=None)   # auth off for the test
    a.register_blueprint(events_bp)
    yield a
    with event_bus._LOCK:
        event_bus._SUBSCRIBERS.clear()


def _parse_sse_block(block: str):
    """Return (event_type, data_dict) from one '\\n\\n'-terminated
    SSE block. Robust against the initial ': ok' comment line."""
    etype = "message"
    data_lines = []
    for line in block.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            etype = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
    if not data_lines:
        return etype, None
    try:
        return etype, json.loads("\n".join(data_lines))
    except Exception:
        return etype, {"raw": "\n".join(data_lines)}


def test_subscribe_then_publish_round_trips(app):
    """Publish an event from another thread while the SSE consumer is
    reading. The event should land in the stream within a reasonable
    window — proves the bus → Flask streaming response → reader chain
    works end-to-end."""
    received = []

    def _consume():
        # Use Flask's test_client streaming mode.
        with app.test_client() as c:
            with c.get("/api/events/stream", buffered=False) as resp:
                assert resp.status_code == 200
                # Read enough bytes to cover the initial comment +
                # one event block. iter_encoded() doesn't exist on
                # the response object in all Flask versions; use
                # response.response (the WSGI iterable) directly.
                for raw in resp.response:
                    chunk = raw.decode() if isinstance(raw, bytes) else raw
                    received.append(chunk)
                    if len(received) >= 3:
                        break

    t = threading.Thread(target=_consume, daemon=True)
    t.start()

    # Give the consumer a beat to subscribe.
    deadline = time.time() + 2.0
    while event_bus.subscriber_count() == 0 and time.time() < deadline:
        time.sleep(0.02)
    assert event_bus.subscriber_count() == 1, (
        "consumer should have subscribed by now"
    )

    # Publish a real event.
    event_bus.publish("state_transition", {
        "device_id": "d1", "protocol": "bgp", "state": "Established",
    })

    t.join(timeout=3.0)
    blob = "".join(received)
    # The initial ": ok" comment must appear, then our event block.
    assert ": ok" in blob, blob
    assert "state_transition" in blob, blob
    assert "Established" in blob, blob


def test_no_subscriber_leak_on_consumer_close(app):
    """The fix: subscribe() lives inside the generator's try/finally
    so unsubscribe runs deterministically when Flask closes the
    response stream (client disconnect)."""
    assert event_bus.subscriber_count() == 0
    with app.test_client() as c:
        # Open the stream...
        resp = c.get("/api/events/stream", buffered=False)
        # ...consume one chunk so the generator actually starts...
        first = next(iter(resp.response))
        assert b": ok" in first
        # ...then close it. The generator's finally must unsubscribe.
        resp.close()

    # Give the (cleanup) thread a moment — close() finalizes the
    # generator synchronously in Werkzeug's test client.
    time.sleep(0.05)
    assert event_bus.subscriber_count() == 0, (
        "subscriber leaked after consumer close — finally clause regression"
    )


def test_role_check_runs_before_subscribe(app):
    """Critical invariant: the role decorator must reject BEFORE any
    subscriber is registered. Otherwise a 403'd client still costs
    us a subscriber slot."""
    # Re-configure with a require_role that always rejects.
    rejected = []

    def deny_all(_required: str):
        def decorator(fn):
            from functools import wraps
            @wraps(fn)
            def _w(*args, **kwargs):
                rejected.append(True)
                return ({"ok": False, "error": "forbidden"}, 403)
            return _w
        return decorator

    configure_events(require_role=deny_all)

    assert event_bus.subscriber_count() == 0
    with app.test_client() as c:
        resp = c.get("/api/events/stream")
        # When the wrapper returns a tuple, Flask might still iterate
        # through generators — drain to be sure no subscribe happened.
        body = resp.data
    assert rejected, "role check did not run"
    assert event_bus.subscriber_count() == 0, (
        "subscribe happened even though role check denied — must run after auth"
    )
