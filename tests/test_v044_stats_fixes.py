"""Regression tests for v0.4.4 — two operator-reported bugs:

Problem 1 (RX != TX on stream stop): operator saw TX=13,312,
RX=12,983 (2.47% loss) on a stream that should be lossless. Root
cause: stop_event fires for BOTH the TX thread and the RX sniffer
at the same moment, so the sniffer stops before the TX's last-batch
in-flight packets cross to the RX side. Fix: 2-second drain in the
sniffer stopper.

Problem 2 (TX iface shows RX, RX iface shows TX): the /api/interfaces
endpoint was returning random.randint() values for per-iface tx/rx/
bytes/errors, literally documented as "Simulate ...". Random
coincidences made it look like real counters but the wrong way
round. Fix: read REAL counters via psutil.net_io_counters(pernic=True).

These tests pin both fixes."""
from __future__ import annotations

import re


# ─────────────────────────────────── #1: real interface counters ──────


def test_api_interfaces_no_random_simulation():
    """The pre-fix code had random.randint() calls for tx/rx/bytes/errors
    INSIDE the /api/interfaces endpoint. That endpoint is the source
    for the Stream Statistics dock's per-interface table. v0.4.4
    removed the random values; new code uses psutil real counters."""
    src = open(
        "/Users/surajsharma/dev/netgen/run_tgen_server.py"
    ).read()

    # Pre-fix shape — explicitly forbidden now
    bad_patterns = [
        r"tx\s*=\s*random\.randint\(",
        r"rx\s*=\s*random\.randint\(",
        r"sent_bytes\s*=\s*tx\s*\*\s*random\.randint\(",
        r"received_bytes\s*=\s*rx\s*\*\s*random\.randint\(",
        r"errors\s*=\s*random\.randint\(\s*0\s*,\s*10\s*\)",
    ]
    for pat in bad_patterns:
        m = re.search(pat, src)
        assert m is None, (
            f"Pre-fix random.randint pattern still in run_tgen_server.py: "
            f"{pat!r} — operator-reported bug from svl-d-ai-srv04 will "
            f"re-bite."
        )


def test_api_interfaces_uses_net_io_counters():
    """The fix wires real counters via psutil.net_io_counters(pernic=True)."""
    src = open(
        "/Users/surajsharma/dev/netgen/run_tgen_server.py"
    ).read()
    assert "psutil.net_io_counters(pernic=True)" in src, (
        "run_tgen_server.py /api/interfaces handler missing the real "
        "net_io_counters call — random-value bug not fixed"
    )
    # And the field mapping is in place
    for field in ("packets_sent", "packets_recv", "bytes_sent", "bytes_recv"):
        assert field in src, (
            f"psutil counter field {field} not referenced — interface "
            f"stats won't populate"
        )


# ─────────────────────────────────── #2: sniffer drain on stop ────────


def test_sniffer_stopper_has_drain_sleep():
    """The stopper inside start_rx_counter must sleep briefly after
    stop_event fires, before calling sniffer.stop(). Without it, the
    sniffer halts before the TX path's last in-flight packets reach
    the RX side. Operator-reported on srv04: 329-packet shortfall
    (~2.47% "loss") on a lossless lab loopback."""
    src = open(
        "/Users/surajsharma/dev/netgen/multithreaded_traffic_gen.py"
    ).read()
    # Find the stopper definition
    m = re.search(r"def stopper\(\):[\s\S]+?sniffer\.stop\(\)", src)
    assert m, "stopper() not found in start_rx_counter"
    body = m.group(0)
    # A time.sleep must appear between stop_event.wait() and sniffer.stop()
    assert "time" in body and "sleep" in body, (
        "stopper() has no time.sleep() before sniffer.stop() — without "
        "a drain period, in-flight packets won't be counted"
    )
    # And it should be at least 1 second (catches the typical 200-500
    # packet in-flight window at Scapy speeds)
    m2 = re.search(r"_t\.sleep\(([\d.]+)\)", body)
    assert m2, "could not locate sleep call in stopper body"
    assert float(m2.group(1)) >= 1.0, (
        f"drain sleep is {m2.group(1)}s — must be >= 1s to actually "
        f"drain in-flight packets. 2s is the chosen value."
    )


def test_drain_sleep_is_in_stopper_not_main_thread():
    """The drain MUST be inside the stopper closure (background
    thread), NOT inline in start_rx_counter — otherwise it blocks
    the caller for 2 sec on every stream start."""
    src = open(
        "/Users/surajsharma/dev/netgen/multithreaded_traffic_gen.py"
    ).read()
    # The sleep we added has the comment "drain period"
    m = re.search(r"(def stopper\(\):[\s\S]+?)# ---- ", src)
    # The drain sleep should be inside the def stopper() body
    assert m, "stopper() definition end-marker not found"
    stopper_body = m.group(1)
    # Specifically, the sleep should be AFTER stop_event.wait() and
    # BEFORE sniffer.stop()
    wait_idx = stopper_body.find("stop_event.wait()")
    sniffer_stop_idx = stopper_body.find("sniffer.stop()")
    sleep_idx = stopper_body.find("_t.sleep(")
    assert wait_idx >= 0
    assert sniffer_stop_idx >= 0
    assert sleep_idx >= 0
    assert wait_idx < sleep_idx < sniffer_stop_idx, (
        f"sleep position wrong: should be between stop_event.wait() "
        f"and sniffer.stop(). Got positions wait={wait_idx}, "
        f"sleep={sleep_idx}, stop={sniffer_stop_idx}"
    )
