"""Process registry for DPDK RX workers.

Wraps utils.dpdk_rx_worker (the launcher primitives) with:
  - A by-stream_id registry so /api/admin/dpdk/rx/list can enumerate
    running workers, /stop can address a specific one, and double-
    starts are caught
  - Thread-safe access (Flask serves multiple requests concurrently)
  - Idempotent stop (no error if stream_id already gone)
  - Graceful all-stop on netgen-server shutdown so we don't leak
    rx_worker processes when systemctl restarts us

Lives at module level rather than wired into app state because the
manager IS the process lifecycle — it must survive across Flask
request boundaries the same way the existing tx side does.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from utils.dpdk_rx_worker import (
    RxHandle, start_rx_worker, stop_rx_worker,
)

LOG = logging.getLogger(__name__)


class RxRegistry:
    """Singleton-by-import process registry. Thread-safe."""

    def __init__(self) -> None:
        self._handles: dict[str, RxHandle] = {}
        self._lock = threading.Lock()

    # v0.5.255 (audit RX-7): sentinel object marking "spawn in flight"
    # for a given stream_id. It occupies the dict slot briefly (from
    # collision check → after Popen), so a concurrent start() sees
    # the reservation without holding the registry lock across the
    # Popen (which itself calls out to systemd-run and blocks for
    # 200-800 ms, sometimes seconds on a slow D-Bus). Any other
    # request (list / stop / latest_for) that lands in that window
    # is no longer head-of-line-blocked.
    _SPAWNING = object()

    def start(
        self,
        *,
        stream_id: str,
        pci_bdf: str,
        lcores: str = "0,1,2,3",
        rx_queues: int = 1,
        vlan: Optional[int] = None,
        dst_port: Optional[int] = None,
        src_port: Optional[int] = None,
        src_ip: Optional[str] = None,
        dst_ip: Optional[str] = None,
        duration_s: int = 0,
    ) -> dict:
        """Start an rx_worker for stream_id. Returns a small status
        dict; caller surfaces it via the HTTP response. Raises
        ValueError if stream_id is already running — operator sees
        a clear error instead of two processes fighting over the
        same PCI port.
        """
        # PHASE 1 — under lock, decide whether to spawn and reserve
        # the slot. Fast — no I/O.
        with self._lock:
            existing = self._handles.get(stream_id)
            if existing is self._SPAWNING:
                raise ValueError(
                    f"rx_worker spawn already in flight for stream "
                    f"{stream_id}. Wait for it to complete."
                )
            if existing and existing.is_running():
                raise ValueError(
                    f"rx_worker already running for stream {stream_id} "
                    f"(pid {existing.proc.pid}). Stop it first or use "
                    f"a different stream_id."
                )
            # Reap a dead-but-not-removed handle before re-launching.
            if existing and not existing.is_running():
                self._handles.pop(stream_id, None)
            self._handles[stream_id] = self._SPAWNING  # reservation

        # PHASE 2 — actual Popen + systemd-run, LOCK RELEASED.
        # Pre-fix this ran inside the lock, serialising every
        # concurrent list()/stop()/latest_for() call against the
        # ~200-800 ms Popen. Two operators clicking Start on
        # different streams then serialised each other AND every
        # viewer request behind them.
        try:
            handle = start_rx_worker(
                stream_id=stream_id, pci_bdf=pci_bdf, lcores=lcores,
                rx_queues=rx_queues, vlan=vlan,
                dst_port=dst_port, src_port=src_port,
                src_ip=src_ip, dst_ip=dst_ip,
                duration_s=duration_s,
            )
        except Exception:
            # Spawn failed — release the reservation so a retry works.
            with self._lock:
                if self._handles.get(stream_id) is self._SPAWNING:
                    self._handles.pop(stream_id, None)
            raise

        # PHASE 3 — swap the reservation for the real handle.
        with self._lock:
            self._handles[stream_id] = handle
        return {
            "stream_id": stream_id,
            "pid": handle.proc.pid,
            "started_at_monotonic": handle.started_at,
            "cmd": handle.cmd,
        }

    def stop(self, stream_id: str, *, timeout_s: float = 5.0) -> dict:
        """Stop the rx_worker for stream_id. Idempotent —
        returns {"status": "stopped"|"not_running"|"unknown"} so
        the caller can react without an error path for the common
        "already stopped" case."""
        with self._lock:
            handle = self._handles.pop(stream_id, None)
        if handle is None:
            return {"status": "unknown", "stream_id": stream_id}
        # v0.5.255 (audit RX-7): a Stop that lands mid-spawn should
        # NOT try to stop the sentinel. Restore the reservation so
        # the concurrent start() finishes; caller retries Stop.
        if handle is self._SPAWNING:
            with self._lock:
                self._handles.setdefault(stream_id, self._SPAWNING)
            return {"status": "spawning", "stream_id": stream_id}
        if not handle.is_running():
            # Reap so we still surface .final() if available.
            return {
                "status": "not_running",
                "stream_id": stream_id,
                "final": handle.final() or {},
            }
        summary = stop_rx_worker(handle, timeout_s=timeout_s)
        return {
            "status": "stopped",
            "stream_id": stream_id,
            "final": summary,
        }

    def list(self) -> list[dict]:
        """Snapshot of all current handles + their latest counters."""
        with self._lock:
            items = list(self._handles.items())
        out = []
        for sid, h in items:
            # v0.5.255 (audit RX-7): skip in-flight spawns from
            # every list/show endpoint.
            if h is self._SPAWNING:
                out.append({"stream_id": sid, "status": "spawning"})
                continue
            entry = {
                "stream_id": sid,
                "pid": h.proc.pid,
                "is_running": h.is_running(),
                "started_at_monotonic": h.started_at,
                "uptime_s": max(0.0, time.monotonic() - h.started_at),
                "latest": h.latest(),
                "final": h.final(),
            }
            # v0.5.118: surface stderr tail for dead-but-not-yet-
            # reaped workers. When `is_running=False` and `final
            # is None`, the worker died without emitting its
            # final summary — stderr is the only place the death
            # cause survives. Live workers also get it (last N
            # lines, useful for "why is rx_pps suddenly 0?"
            # debugging).
            tail = h.stderr_tail(n=20)
            if tail:
                entry["stderr_tail"] = tail
                # Best-effort exit-code surface — if the process
                # exited, `proc.poll()` is its return code; live
                # workers see None.
                entry["exit_code"] = h.proc.poll()
            out.append(entry)
        return out

    def latest_for(self, stream_id: str) -> Optional[dict]:
        """Used by the stream-stats path to fold rx_worker counters
        into the existing /api/streams/stats response when a stream's
        rx_engine is dpdk. Returns None if no handle registered."""
        with self._lock:
            handle = self._handles.get(stream_id)
        # v0.5.255 (audit RX-7): sentinel means "spawn in flight" —
        # no real handle yet, no counters to fold.
        if handle is None or handle is self._SPAWNING:
            return None
        return handle.latest()

    def stop_all(self, *, timeout_s: float = 3.0) -> None:
        """Best-effort shutdown — called from netgen-server's exit
        path. Doesn't raise so a shutdown bug here doesn't wedge
        the server's clean exit."""
        with self._lock:
            sids = list(self._handles.keys())
        for sid in sids:
            try:
                self.stop(sid, timeout_s=timeout_s)
            except Exception as exc:
                LOG.warning(
                    "[dpdk-rx-mgr] stop_all: %s failed: %s", sid, exc,
                )


# Module-level singleton. Pattern matches utils.dpdk_orchestrator's
# global state (which the server already relies on across requests).
_REGISTRY = RxRegistry()


def registry() -> RxRegistry:
    """Public accessor — tests + Flask endpoints get the same
    instance. Lazy-init friendly: never raises."""
    return _REGISTRY


def reset_registry_for_tests() -> None:
    """Test-only helper. Stops all running handles and replaces the
    singleton so per-test fixtures don't leak processes."""
    global _REGISTRY
    try:
        _REGISTRY.stop_all(timeout_s=1.0)
    except Exception:
        pass
    _REGISTRY = RxRegistry()
