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
        with self._lock:
            existing = self._handles.get(stream_id)
            if existing and existing.is_running():
                raise ValueError(
                    f"rx_worker already running for stream {stream_id} "
                    f"(pid {existing.proc.pid}). Stop it first or use "
                    f"a different stream_id."
                )
            # Reap a dead-but-not-removed handle before re-launching.
            if existing and not existing.is_running():
                self._handles.pop(stream_id, None)

            handle = start_rx_worker(
                stream_id=stream_id, pci_bdf=pci_bdf, lcores=lcores,
                rx_queues=rx_queues, vlan=vlan,
                dst_port=dst_port, src_port=src_port,
                src_ip=src_ip, dst_ip=dst_ip,
                duration_s=duration_s,
            )
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
            out.append({
                "stream_id": sid,
                "pid": h.proc.pid,
                "is_running": h.is_running(),
                "started_at_monotonic": h.started_at,
                "uptime_s": max(0.0, time.monotonic() - h.started_at),
                "latest": h.latest(),
                "final": h.final(),
            })
        return out

    def latest_for(self, stream_id: str) -> Optional[dict]:
        """Used by the stream-stats path to fold rx_worker counters
        into the existing /api/streams/stats response when a stream's
        rx_engine is dpdk. Returns None if no handle registered."""
        with self._lock:
            handle = self._handles.get(stream_id)
        if handle is None:
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
