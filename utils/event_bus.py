"""Tiny in-process pub-sub for server-pushed events.

Producers (the monitors, the apply path, the FRR lifecycle helpers)
call `publish(event_type, payload)` whenever something operator-
visible happens. Consumers (the SSE endpoint `/api/events/stream`)
get a queue per subscriber and pull events off it; when the operator
closes their browser/GUI tab, the subscriber unblocks and we tear
down its queue.

Design constraints
------------------

* **No global event loop**: the rest of the server is sync Flask +
  threads, so this stays the same. Each subscriber gets a
  `queue.Queue` they can `get(timeout=...)` on without spawning a
  selector / asyncio runtime.
* **Backpressure-aware**: each subscriber has a bounded queue
  (`maxsize`). If a slow consumer falls behind, we drop the oldest
  event for them rather than blocking the producer thread. The
  producer never blocks — a frozen client cannot stall the server.
* **No persistence**: events are ephemeral. If a client disconnects
  and reconnects, they get only the events from now-on; reconciling
  past state is the job of the REST API the client already polls
  (e.g. `/api/device/database/devices/<id>/history`).
* **Cheap when nobody is listening**: `publish()` returns immediately
  when there are zero subscribers, so attaching it to every monitor
  poll is fine even on a headless server with no GUI clients.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class _Subscriber:
    """One subscriber's bounded event queue. Producer drops oldest on
    overflow rather than blocking."""

    __slots__ = ("q", "id", "created_at")

    def __init__(self, maxsize: int = 128):
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self.id = id(self)
        self.created_at = time.time()


_LOCK = threading.Lock()
_SUBSCRIBERS: List[_Subscriber] = []


def publish(event_type: str, payload: Dict[str, Any]) -> int:
    """Broadcast an event to every subscriber. Returns the count of
    queues the event was delivered to (0 when no one is listening).

    Producer-side guarantees:
    * Never blocks. Slow consumers get the oldest event dropped.
    * Adds `event_type` and `ts` keys to the payload before delivery
      so consumers can render a uniform "[12:34:56] BGP up" line.
    """
    with _LOCK:
        subs = list(_SUBSCRIBERS)
    if not subs:
        return 0
    envelope = {
        "event_type": event_type,
        "ts": time.time(),
        **(payload or {}),
    }
    delivered = 0
    for s in subs:
        try:
            s.q.put_nowait(envelope)
            delivered += 1
        except queue.Full:
            # Drop oldest, then enqueue. Slow client → stale events.
            try:
                s.q.get_nowait()
            except queue.Empty:
                pass
            try:
                s.q.put_nowait(envelope)
                delivered += 1
            except queue.Full:
                # Even after dropping we couldn't push — the consumer
                # is mid-iteration on get(), pathological case; skip.
                pass
    return delivered


def subscribe() -> _Subscriber:
    sub = _Subscriber()
    with _LOCK:
        _SUBSCRIBERS.append(sub)
    return sub


def unsubscribe(sub: _Subscriber) -> None:
    with _LOCK:
        try:
            _SUBSCRIBERS.remove(sub)
        except ValueError:
            pass


def subscriber_count() -> int:
    with _LOCK:
        return len(_SUBSCRIBERS)


def iter_events(sub: _Subscriber, *, heartbeat_interval: float = 15.0,
                stop_after_idle: Optional[float] = None
                ) -> Iterator[Dict[str, Any]]:
    """Generator that yields events for one subscriber.

    Emits a synthetic `{"event_type": "heartbeat", ...}` event every
    `heartbeat_interval` seconds so SSE/long-poll consumers can detect
    a half-open TCP connection promptly and so proxies don't time the
    response out for inactivity. Exits cleanly when the subscriber's
    iter context closes (e.g. SSE client disconnect).
    """
    last_event_at = time.time()
    try:
        while True:
            try:
                event = sub.q.get(timeout=heartbeat_interval)
                last_event_at = time.time()
                yield event
            except queue.Empty:
                # Idle window — emit a heartbeat so the wire stays warm.
                if stop_after_idle is not None and (
                    time.time() - last_event_at > stop_after_idle
                ):
                    break
                yield {
                    "event_type": "heartbeat",
                    "ts": time.time(),
                    "subscriber_count": subscriber_count(),
                }
    finally:
        unsubscribe(sub)
