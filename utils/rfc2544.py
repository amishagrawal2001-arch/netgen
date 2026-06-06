"""Pure helpers for the RFC 2544 throughput test orchestrator.

Extracted from run_tgen_server.py so they can be unit-tested without
triggering the server module's side effects at import time (which
include opening /opt/netgen/database.db that doesn't exist in the
test environment).

Two functions here:

    _decide_step(...)          — pure-function step for the v0.4.0
                                 smart binary search. Detects
                                 TX-rate-limit (Scapy ceiling, peer
                                 unreachable) and uses the actually
                                 achieved rate as the new ceiling
                                 instead of letting the search waste
                                 10+ iterations halving an
                                 unreachable rate.

    _check_reachable(...)      — best-effort ICMP ping pre-flight.
                                 Caller in run_tgen_server.rfc2544_start
                                 invokes this before kicking off the
                                 test thread to catch the
                                 peer-doesn't-exist scenario.

run_tgen_server.py re-exports both functions at the module level via
``from utils.rfc2544 import _decide_step as _rfc2544_decide_step``
and same for _check_reachable, so existing call sites + tests can
still import them by the long names off the server module.
"""
from __future__ import annotations

import subprocess
from typing import Tuple


def _decide_step(tx: int, rx: int, trying_pps: int,
                 lo_pps: int, hi_pps: int, last_good: int,
                 target_loss_pct: float, duration_s: int,
                 ) -> Tuple[int, int, int, str | None]:
    """Pure-function decision step for the v0.4.0 RFC 2544 smart binary
    search.

    Given the just-finished iteration's tx + rx counters and the
    current (lo_pps, hi_pps, last_good) search bounds, decide the
    NEXT (lo, hi, last_good, diagnosis) tuple.

    Returns (new_lo, new_hi, new_last_good, diagnosis) where
    ``diagnosis`` is None on a normal step or ``"tx_rate_limited"``
    when the iteration revealed that the TX path itself can't reach
    the asked rate (Scapy ceiling, CPU bottleneck, etc.).

    The TX-rate-limit detection is the v0.4.0 fix for the operator-
    reported scenario where:
      - Test asked for 297M pps at 64B on a 400G link
      - Scapy actually sent ~80k packets in 60 seconds (~1.3 kpps)
      - RX received 0 (peer unreachable)
      - loss_pct = 100% → naive search sets hi=297M and tries 148M
        next, which also can't be reached, etc. — 13 iterations of
        wasted time before converging to "0 pps no-drop"

    Trigger: tx_actual < 10% of trying_pps AND loss > target. The
    10% threshold is tight enough to skip only obviously-broken
    probes; loose enough to not false-fire on small overshoot.
    """
    tx_pps_actual = (tx / duration_s) if duration_s > 0 else 0
    loss_pct = ((tx - rx) / tx * 100.0) if tx > 0 else 100.0

    # TX-rate-limited fast path. Only triggers on the lossy case
    # because if RX received enough to satisfy target, the rate
    # question was real, not a TX ceiling.
    if (loss_pct > target_loss_pct
            and trying_pps > 0
            and tx_pps_actual < 0.10 * trying_pps):
        # Use the actually-achieved rate as the new ceiling. This
        # converges fast: next iter tries (lo + tx_actual)/2, which
        # is well within Scapy's envelope.
        new_hi = max(int(tx_pps_actual), 1)
        # Clamp to existing hi — don't EXPAND the search range. If
        # tx_actual somehow exceeded the previous hi (shouldn't
        # happen but be defensive), keep the previous hi.
        new_hi = min(new_hi, hi_pps)
        return (lo_pps, new_hi, last_good, "tx_rate_limited")

    # Normal binary search step.
    if loss_pct <= target_loss_pct:
        return (trying_pps, hi_pps, trying_pps, None)
    return (lo_pps, trying_pps, last_good, None)


def _check_reachable(ip_dst: str, tx_iface: str,
                     timeout_sec: int = 2) -> Tuple[bool, str]:
    """Best-effort ICMP ping pre-flight for /api/rfc2544/start.

    Catches the operator scenario from the field: dest IP has no
    peer, so RX=0 on every binary-search probe, so max_no_drop_pps
    converges to 0 after 13+ iterations × 60 sec — an hour of
    wasted test time learning the wire was one-way.

    Returns (reachable, message). reachable=True means ping got a
    response OR we couldn't even run ping (treat unknown as safe —
    only block on a definite "host down" signal).

    Skipped entirely by the caller when:
      - skip_reachability_probe=true in the request body
      - rx_iface == tx_iface (loopback — IP-level ping doesn't apply
        to L2 hardware loopback setups, which are valid)
    """
    if not ip_dst:
        return True, ""
    try:
        cmd = ["ping", "-c", "1", "-W", str(timeout_sec)]
        if tx_iface:
            # -I binds the outgoing interface so the probe goes via
            # the test NIC, not via the default route.
            cmd.extend(["-I", tx_iface])
        cmd.append(ip_dst)
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout_sec + 2)
        if r.returncode == 0:
            return True, ""
        # rc != 0 — host appears unreachable. Surface a short message.
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else f"ping {ip_dst} → rc={r.returncode}"
        return False, (
            f"ping {ip_dst} via {tx_iface or 'default route'} failed: {msg}"
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        # ping binary missing or timed out talking to it — don't
        # block the test on infrastructure noise.
        return True, f"ping skipped ({e.__class__.__name__})"
    except Exception as e:
        return True, f"ping skipped (unexpected error: {e})"
