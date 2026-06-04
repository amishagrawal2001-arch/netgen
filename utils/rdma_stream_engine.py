"""RDMA stream engine shim — v0.3.12.

Bridges the per-stream "engine = rdma" path (from widgets/stream_dialog
.py engine_combo) into utils/rdma_perf.start_perftest. The streams
tab can now host RDMA streams: each one is a single perftest client
invocation running for `duration` seconds against a peer address.

Stats flow:
  * start_rdma_stream() registers a stub entry in the stream tracker
    so the Streams tab's row immediately reflects "running".
  * A background thread polls utils.rdma_perf.get_perftest_job() and
    annotates the tracker with the parsed BW / latency values. We
    can't push raw per-frame TX counts (perftest doesn't expose
    them), so we synthesize `tx_count` from
    final_iterations × final_msg_size_bytes when present — gives the
    Streams tab a non-zero count to display.

This is intentionally THIN — the real perftest lifecycle lives in
utils/rdma_perf. This shim is just glue: stream-shape ↔ start_perftest
args, plus tracker-update plumbing.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("rdma_stream_engine")


def should_use_rdma(stream_data: Dict[str, Any]) -> bool:
    """True if this stream is configured for the RDMA engine."""
    if not isinstance(stream_data, dict):
        return False
    engine = str(stream_data.get("engine") or "").strip().lower()
    if engine == "rdma":
        return True
    ps = stream_data.get("protocol_selection") or {}
    if isinstance(ps, dict):
        if str(ps.get("engine") or "").strip().lower() == "rdma":
            return True
    return False


def rdma_compatibility_check(stream_data: Dict[str, Any]) -> Optional[str]:
    """None when OK, otherwise a short operator-readable reason."""
    if not isinstance(stream_data, dict):
        return "stream_data not a dict"
    rdma = stream_data.get("rdma") or {}
    if not isinstance(rdma, dict):
        return "missing rdma config block"
    peer = (rdma.get("peer_addr") or "").strip()
    if not peer:
        return "RDMA stream requires rdma.peer_addr (server-side IP)"
    test = (rdma.get("test") or "send_bw").strip()
    valid_tests = {"send_bw", "write_bw", "read_bw",
                   "send_lat", "write_lat", "read_lat"}
    if test not in valid_tests:
        return f"rdma.test must be one of {sorted(valid_tests)}; got {test!r}"
    return None


def _opts_from_stream(stream_data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate stream_data['rdma'] into start_perftest opts."""
    rdma = stream_data.get("rdma") or {}
    opts: Dict[str, Any] = {
        "peer_addr": rdma.get("peer_addr"),
        "device":    rdma.get("device") or "mlx5_0",
        "ib_port":   int(rdma.get("ib_port") or 1),
        "msg_size":  int(rdma.get("msg_size") or 65536),
        "qp_count":  int(rdma.get("qp_count") or 1),
        "duration":  int(rdma.get("duration") or 30),
        "gid_index": int(rdma.get("gid_index") or 3),
        "bidirectional": bool(rdma.get("bidirectional")),
        "report_gbits": True,
    }
    if rdma.get("mtu") is not None:
        opts["mtu"] = int(rdma["mtu"])
    if rdma.get("tx_depth") is not None:
        opts["tx_depth"] = int(rdma["tx_depth"])
    return opts


def start_rdma_stream(
    stream_data: Dict[str, Any],
    interface: str,
    stop_event: threading.Event,
    tracker,
) -> Dict[str, Any]:
    """Spawn a perftest client for this stream + start a poll thread
    that mirrors stats into the tracker.

    Returns the same shape as the DPDK / Scapy launch paths:
      {"status": "started" | "error", "stream_id": ..., "error": ...,
       "rdma_job_id": ..., "engine": "rdma"}
    """
    from utils.rdma_perf import start_perftest, get_perftest_job, stop_perftest
    from utils.rdma_handshake import register_half

    reason = rdma_compatibility_check(stream_data)
    if reason:
        return {"status": "error", "error": reason,
                "engine": "rdma", "stream_id": stream_data.get("stream_id")}

    rdma_cfg = stream_data.get("rdma") or {}
    test = (rdma_cfg.get("test") or "send_bw").strip()
    opts = _opts_from_stream(stream_data)

    result = start_perftest("client", test, opts)
    if result.get("status") != "started":
        return {
            "status": "error",
            "error": f"perftest start failed: {result.get('error')}",
            "engine": "rdma",
            "stream_id": stream_data.get("stream_id"),
        }

    job_id = result["job_id"]
    # Register under a per-stream handshake_id so /api/rdma/handshakes
    # can show this stream alongside any Blast-RDMA-Flow pairings.
    handshake_id = f"stream:{stream_data.get('stream_id') or job_id[:8]}"
    register_half(
        handshake_id,
        role="client",
        job_id=job_id,
        test=test,
        device=str(opts.get("device") or ""),
        ib_port=int(opts.get("ib_port") or 1),
        listen_port=result["listen_port"],
        peer_addr=opts.get("peer_addr"),
        note=f"stream {stream_data.get('name') or stream_data.get('stream_id') or '?'}",
    )

    # Register in tracker so the Streams tab sees it as running AND
    # so /api/traffic/stop can reach our stop_event when the operator
    # hits Stop. The DPDK/Scapy path mints its event in
    # run_tgen_server.launch_single_stream and registers it via
    # add_stream({"stop_event": ...}); we mirror that here. (Without
    # this, the orphaned threading.Event() the caller hands us is
    # never set externally — the perftest child runs to its full
    # --duration and the GUI's Stop button silently no-ops.)
    sid = stream_data.get("stream_id") or job_id
    try:
        if hasattr(tracker, "add_stream"):
            tracker.add_stream({
                "stream_id":   sid,
                "interface":   interface,
                "stream_name": stream_data.get("name") or "rdma",
                "stop_event":  stop_event,
                "rx_thread":   None,
                "rx_interface": stream_data.get("rx_interface") or interface,
                "flow_tracking_enabled": False,  # perftest does its own RX
                "frame_size":  int((stream_data.get("rdma") or {}).get("msg_size") or 65536),
            })
        if hasattr(tracker, "mark_runtime_engine"):
            tracker.mark_runtime_engine(
                interface, sid, runtime_engine="rdma",
            )
    except Exception as exc:
        logger.debug(f"[rdma-stream] tracker init: {exc}")

    # Poll thread — runs until the job finishes OR stop_event is set.
    def _poll():
        last_bytes = 0
        while not stop_event.is_set():
            time.sleep(2.0)
            job = get_perftest_job(job_id)
            if job is None:
                break
            try:
                # Synthesize TX byte count from final iterations + size
                # when present so the Streams tab shows non-zero.
                bw_avg = job.get("final_bw_avg_gbps")
                msg = job.get("final_msg_size_bytes")
                iters = job.get("final_iterations")
                if msg and iters:
                    total_bytes = int(msg) * int(iters)
                    delta = max(0, total_bytes - last_bytes)
                    last_bytes = total_bytes
                    if hasattr(tracker, "update_tx_by_id"):
                        # delta is in BYTES, not frames; the tracker
                        # is byte-agnostic so adding it works for
                        # display purposes.
                        try:
                            tracker.update_tx_by_id(interface, sid, delta)
                        except Exception:
                            pass
                if job.get("finished_at") is not None:
                    break
            except Exception as exc:
                logger.debug(f"[rdma-stream] poll: {exc}")
                break
        # Stop event triggered → kill the perftest child.
        if stop_event.is_set():
            try:
                stop_perftest(job_id)
            except Exception as exc:
                logger.debug(f"[rdma-stream] stop: {exc}")

    t = threading.Thread(target=_poll, daemon=True,
                         name=f"rdma-stream-poll-{sid[:8]}")
    t.start()

    return {
        "status": "started",
        "stream_id": sid,
        "rdma_job_id": job_id,
        "handshake_id": handshake_id,
        "engine": "rdma",
        "listen_port": result["listen_port"],
        "tool": result.get("tool"),
    }
