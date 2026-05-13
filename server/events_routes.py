"""Server-Sent Events endpoint for live operator-visible events.

Why SSE not WebSocket:
* Plain HTTP — no new dependency, no upgrade handshake.
* The existing bearer-token middleware applies automatically.
* The wire format is text-based (`event: ...\\ndata: ...\\n\\n`)
  which `curl -N` can stream directly for debugging.
* EventSource (browser-side) and our QThread client both reconnect
  automatically on drop — no extra ping/pong logic.
* Server-push only, which is all we need; clients still drive
  mutations via the existing REST endpoints.

Event types emitted today:
* `state_transition` — fired from `DeviceDatabase.add_state_transition`
  every time a monitor observes a protocol-state change. Payload:
  `{device_id, protocol, state, detail}`.
* `heartbeat` — emitted every ~15s by the iterator so the GUI knows
  the stream is alive when the fabric is quiet.

Adding a new event type: just call `event_bus.publish("my_event", {...})`
anywhere in the server. Subscribers get the new event automatically.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from flask import Blueprint, Response, request


events_bp = Blueprint("events", __name__)


def _noop_role(_role: str):
    def _decorator(fn):
        return fn
    return _decorator


_require_role: Callable[[str], Callable] = _noop_role


def configure(*, require_role: Optional[Callable] = None) -> None:
    """Wire in the parent app's role decorator. Viewer-only by
    default — anyone with read access can observe live events."""
    global _require_role
    if require_role is not None:
        _require_role = require_role


@events_bp.route("/api/events/stream", methods=["GET"])
def events_stream():
    """Server-Sent Events stream of operator-visible events.

    Wire format per stream item:
        event: <type>\\ndata: <json>\\n\\n

    Use `curl -N -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" ...`
    to consume from the shell. The connection stays open until the
    client disconnects.
    """
    return _require_role("viewer")(_events_stream_impl)()


def _events_stream_impl():
    from utils.event_bus import subscribe, unsubscribe, iter_events

    def _gen():
        # `subscribe()` lives INSIDE the generator so it only runs once
        # Flask actually starts iterating the response. If the role
        # check or Flask itself aborts before iteration, no subscriber
        # is registered → no leak.
        #
        # The try/finally around the whole iteration deterministically
        # unsubscribes when the generator is closed by Flask (client
        # disconnect, server shutdown, etc.) — this is more robust
        # than relying on iter_events' own finally clause, which only
        # runs if iter_events has yielded at least once.
        sub = subscribe()
        try:
            # Initial comment line forces some proxies to commit the
            # response headers immediately instead of buffering.
            yield ": ok\n\n"
            for event in iter_events(sub, heartbeat_interval=15.0):
                etype = event.get("event_type", "message")
                try:
                    body = json.dumps(event, separators=(",", ":"), default=str)
                except Exception:
                    body = json.dumps({"event_type": etype, "_unserializable": True})
                yield f"event: {etype}\ndata: {body}\n\n"
        finally:
            # Always unsubscribe, even if iter_events never yielded.
            # iter_events also calls unsubscribe in its own finally,
            # but the second call is a no-op (set-style remove).
            try:
                unsubscribe(sub)
            except Exception:
                pass
            logging.debug("[SSE] client disconnected (subscriber released)")

    resp = Response(_gen(), mimetype="text/event-stream")
    # Discourage proxy buffering so events flush promptly.
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@events_bp.route("/api/events/status", methods=["GET"])
def events_status():
    """Number of currently-connected SSE subscribers. Useful for
    operators wondering whether anyone is actually consuming events."""
    return _require_role("viewer")(_events_status_impl)()


def _events_status_impl():
    from utils.event_bus import subscriber_count
    return {"subscribers": subscriber_count()}, 200
