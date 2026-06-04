"""RDMA pair-handshake registry — v0.3.12.

Lightweight pairing broker for two-sided perftest runs. The GUI
orchestrates a Blast RDMA Flow like this:

    handshake_id = uuid4()  # shared key

    # 1. tell server TG to listen
    POST http://tg-a/api/rdma/perftest/start
        { role: "server", test: "send_bw", handshake_id, ...opts }
      → response includes: { job_id_server, listen_addr, listen_port }

    # 2. tell client TG to dial it
    POST http://tg-b/api/rdma/perftest/start
        { role: "client", test: "send_bw", handshake_id,
          peer_addr: listen_addr, listen_port, ...opts }
      → response includes: { job_id_client }

Both TGs store their half of the pairing here so /api/rdma/perftest/jobs
returns ``handshake_id`` alongside ``job_id`` — the GUI uses that to:
  * draw both halves of the pairing in the same row of its Live panel,
  * stop the pair atomically (one Stop button → DELETE on both sides),
  * show "orphaned half" warnings when one side dies and the other
    keeps running (perftest's recovery is mediocre; the GUI surfaces
    it instead of letting the operator wonder why TX is zero).

This module is intentionally PASSIVE — it does not open any sockets,
spawn any threads, or contact the peer TG. The HTTP plumbing lives
in run_tgen_server.py and the orchestration logic lives in the GUI.
This is just a thread-safe in-memory tag store with TTL eviction.

Rationale for not proxying perftest's actual control channel:
  perftest does its own TCP-socket QP-info exchange on the
  ``-p <port>`` socket (default 18515). Hijacking that would mean
  patching perftest or running a man-in-the-middle. Neither is
  worth it. The handshake_id is a tag that lets netgen REASON about
  pairings without owning the control flow.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rdma_handshake")


# Evict pairing records this long after the last update so a forgotten
# handshake_id doesn't pin memory forever. Long enough that operators
# walking away from a 30-minute test get to come back and inspect the
# stale pairing.
_HANDSHAKE_TTL_SECS = 3600


@dataclass
class HandshakeHalf:
    """One side (server or client) of a perftest pairing as known to
    THIS TG. Each TG stores at most one half per handshake_id — typically
    the side it's playing in the pairing. (Loopback tests run both
    halves on the same TG; we permit two halves per ID for that case.)
    """
    role: str                    # "server" | "client"
    job_id: str                  # local PerftestJob id
    test: str                    # "send_bw", "write_lat", …
    device: str
    ib_port: int
    listen_addr: Optional[str]   # IP the perftest server is reachable on
    listen_port: int             # perftest -p value
    peer_addr: Optional[str]     # set on client side
    registered_at: float
    last_updated_at: float


@dataclass
class HandshakeRecord:
    """All halves of one logical perftest pairing as seen by this TG."""
    handshake_id: str
    created_at: float
    last_updated_at: float
    halves: List[HandshakeHalf] = field(default_factory=list)
    note: Optional[str] = None   # operator-set label, e.g. "100G RC test"

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "handshake_id": self.handshake_id,
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
            "halves": [asdict(h) for h in self.halves],
            "note": self.note,
        }


_registry: Dict[str, HandshakeRecord] = {}
_lock = threading.RLock()


def _gc_expired() -> None:
    """Reap handshake records older than TTL."""
    now = time.time()
    with _lock:
        stale = [
            hid for hid, rec in _registry.items()
            if (now - rec.last_updated_at) > _HANDSHAKE_TTL_SECS
        ]
        for hid in stale:
            _registry.pop(hid, None)


def new_handshake_id() -> str:
    """Generate a fresh handshake_id. Pure helper; doesn't reserve
    anything in the registry — the next register_half() call binds it."""
    return str(uuid.uuid4())


def register_half(
    handshake_id: Optional[str],
    *,
    role: str,
    job_id: str,
    test: str,
    device: str,
    ib_port: int,
    listen_port: int,
    listen_addr: Optional[str] = None,
    peer_addr: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Record this TG's half of a perftest pairing.

    Called by /api/rdma/perftest/start AFTER the perftest job is
    successfully spawned. If handshake_id is None, auto-allocates one
    so single-sided "I just want to run perftest server in isolation"
    invocations still get a tag.

    Returns ``{"handshake_id": <str>, "record": <dict>}``.

    Safe to call twice with the same ``(handshake_id, role)`` — the
    second call overwrites the first half (e.g. operator restarted
    one side; new job_id replaces stale one).
    """
    _gc_expired()
    if not handshake_id:
        handshake_id = new_handshake_id()

    now = time.time()
    half = HandshakeHalf(
        role=role,
        job_id=job_id,
        test=test,
        device=device,
        ib_port=ib_port,
        listen_addr=listen_addr,
        listen_port=listen_port,
        peer_addr=peer_addr,
        registered_at=now,
        last_updated_at=now,
    )
    with _lock:
        rec = _registry.get(handshake_id)
        if rec is None:
            rec = HandshakeRecord(
                handshake_id=handshake_id,
                created_at=now,
                last_updated_at=now,
                note=note,
            )
            _registry[handshake_id] = rec
        else:
            if note:
                rec.note = note
        # Replace any existing half with the same role (operator
        # restart case); permit multiple halves only for loopback
        # where you genuinely want both server and client recorded
        # on the same TG.
        rec.halves = [h for h in rec.halves if h.role != role]
        rec.halves.append(half)
        rec.last_updated_at = now
        snap = rec.to_public_dict()

    logger.info(f"[rdma-broker] registered {role} half job={job_id[:8]} "
                f"under handshake={handshake_id[:8]}")
    return {"handshake_id": handshake_id, "record": snap}


def get_handshake(handshake_id: str) -> Optional[Dict[str, Any]]:
    """Return record as dict, or None."""
    _gc_expired()
    with _lock:
        rec = _registry.get(handshake_id)
        return rec.to_public_dict() if rec else None


def list_handshakes() -> List[Dict[str, Any]]:
    """All known pairings on this TG."""
    _gc_expired()
    with _lock:
        return [rec.to_public_dict() for rec in _registry.values()]


def forget_handshake(handshake_id: str) -> bool:
    """Remove a pairing from the registry. Does NOT stop the underlying
    perftest jobs — callers should stop_perftest() each half first.
    Returns True if a record was removed."""
    with _lock:
        return _registry.pop(handshake_id, None) is not None


def find_job_handshake(job_id: str) -> Optional[str]:
    """Reverse-lookup: given a local perftest job_id, return the
    handshake_id it's part of (or None). Used by /api/rdma/perftest/jobs
    to annotate each job with its pairing tag."""
    with _lock:
        for rec in _registry.values():
            for half in rec.halves:
                if half.job_id == job_id:
                    return rec.handshake_id
    return None


def reset_for_tests() -> None:
    """Test-only: wipe the registry."""
    with _lock:
        _registry.clear()
