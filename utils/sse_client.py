"""Qt SSE-consumer worker.

Connects to `/api/events/stream` on the netgen server, parses the
text/event-stream format, and emits a Qt signal per event so the GUI
can live-update without polling.

Reconnection: the EventSource spec says a client should retry after
~3s when the connection drops; we mirror that. Server-supplied
`retry: <ms>` lines override the default.

Bearer-token auth: pulled from $NETGEN_AUTH_TOKEN on construction,
matching the requests-monkey-patch path the rest of the GUI uses.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class SSEWorker(QThread):
    """Background SSE consumer. Emits `event(type, payload_dict)` for
    every event received."""

    event = pyqtSignal(str, dict)           # event_type, payload
    connected = pyqtSignal()
    disconnected = pyqtSignal(str)          # reason

    def __init__(self, url: str, *, token: Optional[str] = None,
                 retry_ms: int = 3000):
        super().__init__()
        self._url = url
        self._token = token or os.environ.get("NETGEN_AUTH_TOKEN", "").strip()
        self._retry_ms = retry_ms
        self._stop = False
        # Reference to the in-flight requests Response so stop() can
        # forcibly close it. Without this, requests' `iter_lines()`
        # blocks until the next byte arrives — on a quiet fabric with
        # 15s heartbeats, that's up to a 15-second GUI-close hang.
        self._inflight_resp = None

    def stop(self):
        """Request the worker to exit ASAP.

        Sets the stop flag AND forcibly closes any in-flight HTTP
        response so requests.iter_lines() unblocks immediately. Without
        the close, a worker waiting between heartbeats stays blocked
        for up to `heartbeat_interval` seconds — making the GUI
        close-handler look frozen on quiet fabrics."""
        self._stop = True
        resp = self._inflight_resp
        if resp is not None:
            try:
                # close() drains and closes the connection. raw.close()
                # tears down the underlying urllib3 socket so any
                # blocked recv() returns immediately.
                if resp.raw is not None:
                    resp.raw.close()
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def run(self):
        # `requests` is already a hard dep for the rest of the client,
        # so we don't lazy-import it here.
        import requests
        headers = {"Accept": "text/event-stream"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        while not self._stop:
            try:
                with requests.get(
                    self._url, headers=headers, stream=True,
                    timeout=(5, None),  # 5s connect, no read timeout
                ) as resp:
                    self._inflight_resp = resp
                    try:
                        if resp.status_code != 200:
                            self.disconnected.emit(
                                f"HTTP {resp.status_code}: {resp.text[:120]}"
                            )
                            self._sleep_for_retry()
                            continue
                        self.connected.emit()
                        self._consume(resp)
                    finally:
                        # Drop the inflight ref BEFORE the context
                        # manager closes the response, so a late stop()
                        # call doesn't try to close an already-closed
                        # response (which would log a urllib3 warning).
                        self._inflight_resp = None
            except requests.exceptions.RequestException as exc:
                self.disconnected.emit(f"{type(exc).__name__}: {exc}")
            except Exception as exc:
                logger.debug(f"[SSE] unexpected error: {exc}")
                self.disconnected.emit(f"{type(exc).__name__}: {exc}")
            if not self._stop:
                self._sleep_for_retry()

    # ------------------------------------------------------------------
    def _consume(self, resp):
        """Parse the text/event-stream wire format. Each event is a
        block of `field: value` lines terminated by a blank line.
        Fields we care about: `event:` (type), `data:` (json payload),
        `retry:` (reconnect delay override). Anything else is ignored,
        per the SSE spec's forward-compat rules."""
        cur_type = "message"
        cur_data = []
        for raw_line in resp.iter_lines(decode_unicode=True):
            if self._stop:
                return
            if raw_line is None:
                continue
            if raw_line == "":
                # End of event block — dispatch.
                if cur_data:
                    payload_str = "\n".join(cur_data)
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"raw": payload_str}
                    if not isinstance(payload, dict):
                        payload = {"value": payload}
                    try:
                        self.event.emit(cur_type, payload)
                    except Exception:
                        logger.debug(f"[SSE] signal emit failed for {cur_type}")
                cur_type = "message"
                cur_data = []
                continue
            if raw_line.startswith(":"):
                # Comment — keep-alive line.
                continue
            if ":" in raw_line:
                field, _, value = raw_line.partition(":")
                value = value.lstrip(" ")
                if field == "event":
                    cur_type = value
                elif field == "data":
                    cur_data.append(value)
                elif field == "retry":
                    try:
                        self._retry_ms = max(500, int(value))
                    except ValueError:
                        pass
                # other fields (`id`, etc.) silently ignored.

    def _sleep_for_retry(self):
        """Block for the retry window, but check `_stop` frequently so
        the GUI close path doesn't have to wait a full 3 s on every
        shutdown."""
        deadline = time.time() + (self._retry_ms / 1000.0)
        while not self._stop and time.time() < deadline:
            time.sleep(0.1)
