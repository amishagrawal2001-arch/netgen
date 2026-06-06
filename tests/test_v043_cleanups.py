"""Regression tests for v0.4.3 cleanups + observability + TX-VLAN
diagnostic.

v0.4.3 covers six small things:
  1. Removed broken /api/traffic/rx_monitor (was TypeError)
  2. Added /api/rfc2544/status alias for /progress (was 404)
  3. .gitignored runtime state files
  4. pyproject excludes build* from packages.find
  5. New /api/streams/<id>/rx_debug observability endpoint
  6. TX-VLAN diagnostic logs Dot1Q presence + tx-vlan-offload state

These tests cover the things that can be checked without spinning
up the Flask server (which would require setuptools' tracked
package config + a database in /opt/netgen)."""
from __future__ import annotations

import os
import re
from unittest.mock import patch, MagicMock


# ─────────────────────────────────── #1: rx_monitor removed ────────────


def test_rx_monitor_endpoint_removed():
    """The pre-fix endpoint had a wrong-arity call to start_rx_counter
    that would TypeError on hit. v0.4.3 removed the whole endpoint."""
    src = open(
        "/Users/surajsharma/dev/netgen/run_tgen_server.py"
    ).read()
    # No active route definition for the dead endpoint
    assert '@app.route("/api/traffic/rx_monitor"' not in src, (
        "Dead /api/traffic/rx_monitor endpoint still wired up — "
        "it had a TypeError-on-call bug and no client uses it"
    )
    # And no leftover function body with the wrong call
    assert "start_rx_counter(interface, stream_name, stop_event," not in src, (
        "The wrong-arity start_rx_counter call (TypeError) still "
        "present in run_tgen_server.py"
    )


# ─────────────────────────────────── #2: /api/rfc2544/status alias ────


def test_rfc2544_status_alias_present():
    """Operator hit /api/rfc2544/status and got 404; real endpoint
    was /progress. v0.4.3 adds /status as an alias (same view
    function, two routes)."""
    src = open(
        "/Users/surajsharma/dev/netgen/run_tgen_server.py"
    ).read()
    # Both decorators on the same function
    assert '@app.route("/api/rfc2544/progress"' in src
    assert '@app.route("/api/rfc2544/status"' in src
    # And ideally adjacent — pin order so a casual refactor doesn't
    # split them into different handlers with different behaviour.
    # Find both indices and check they're close (within 200 chars).
    p_idx = src.index('@app.route("/api/rfc2544/progress"')
    s_idx = src.index('@app.route("/api/rfc2544/status"')
    assert abs(p_idx - s_idx) < 200, (
        f"/progress and /status routes are far apart ({abs(p_idx-s_idx)} "
        f"chars) — they should be stacked on the same view function"
    )


# ─────────────────────────────────── #3: gitignore ────────────────────


def test_gitignore_covers_runtime_state_files():
    gi = open("/Users/surajsharma/dev/netgen/.gitignore").read()
    assert "session.json" in gi
    assert "server_interfaces.txt" in gi
    assert "recent_sessions.json" in gi, (
        ".gitignore missing recent_sessions.json — touched on every "
        "dev session; should be ignored"
    )


# ─────────────────────────────────── #4: pyproject excludes build ─────


def test_pyproject_excludes_build_dir():
    """Successive `python -m build` invocations on the same checkout
    were nesting build/lib/build/lib/ inside the wheel. Excluding
    build* from packages.find prevents this regardless of which
    build script you ran."""
    pp = open("/Users/surajsharma/dev/netgen/pyproject.toml").read()
    # Find the packages.find exclude pattern
    m = re.search(r"packages\.find\s*=\s*\{\s*exclude\s*=\s*(\[[^\]]+\])", pp)
    assert m, "packages.find exclude pattern not found in pyproject.toml"
    excludes = m.group(1)
    assert '"build*"' in excludes or "'build*'" in excludes, (
        f"pyproject.toml's packages.find exclude doesn't list build*; "
        f"got: {excludes}"
    )


# ─────────────────────────────────── #5: rx_debug endpoint ────────────


def test_rx_debug_endpoint_route_present():
    """The new observability endpoint must be wired into Flask."""
    src = open(
        "/Users/surajsharma/dev/netgen/run_tgen_server.py"
    ).read()
    assert '@app.route("/api/streams/<stream_id>/rx_debug"' in src, (
        "/api/streams/<stream_id>/rx_debug endpoint missing from "
        "run_tgen_server.py — observability fix reverted"
    )
    # And the handler is searching active streams + returning the
    # rx_debug snapshot we populated in start_rx_counter
    assert "stream_tracker.active_streams" in src, (
        "rx_debug handler doesn't read from stream_tracker"
    )


def test_rx_debug_snapshot_populated_at_sniffer_start():
    """start_rx_counter must mirror its counters into the stream
    tracker entry's rx_debug dict so the REST endpoint can read
    them without coordinating with the sniffer thread."""
    src = open(
        "/Users/surajsharma/dev/netgen/multithreaded_traffic_gen.py"
    ).read()
    # Pin the rx_debug structure init
    assert 'rx_debug = {' in src
    for required_key in (
        '"sniff_iface"',  '"base_iface"',  '"vlan_subif_created"',
        '"bpf"',          '"signature_pattern"',
        '"seen_total"',   '"matched"',
        '"sig_hits"',     '"tuple_hits"',
        '"rescue_active"',
    ):
        assert required_key in src, (
            f"rx_debug snapshot missing key {required_key} — REST "
            f"endpoint will return incomplete observability"
        )
    # And the lfilter updates them
    assert 'rx_debug["seen_total"]' in src
    assert 'rx_debug["matched"]' in src


# ─────────────────────────────────── #6: TX VLAN diagnostic ───────────


def test_diagnose_tx_vlan_present():
    """The TX-side diagnostic helper must exist + be called from the
    Generic stream loop. Detects Dot1Q layer presence + ethtool
    tx-vlan-offload state so operators see the root cause of
    "config says tagged but wire is untagged" cases."""
    import multithreaded_traffic_gen as mtg
    assert hasattr(mtg, "_diagnose_tx_vlan"), (
        "_diagnose_tx_vlan helper missing — TX VLAN diagnostic "
        "fix reverted"
    )

    src = open(
        "/Users/surajsharma/dev/netgen/multithreaded_traffic_gen.py"
    ).read()
    # Wire-in: called near "[Generic] TX loop enter"
    assert "_diagnose_tx_vlan(" in src, (
        "_diagnose_tx_vlan is defined but never called — diagnostic "
        "won't fire"
    )


def test_diagnose_tx_vlan_warns_when_dot1q_missing_but_expected():
    """When stream config says VLAN tagged but the built packet
    doesn't have a Dot1Q layer, _diagnose_tx_vlan must log a
    WARNING. This is the operator's case in disguise — IF the
    builder ever drops the Dot1Q, the warning fires immediately
    at stream startup."""
    import logging
    from multithreaded_traffic_gen import _diagnose_tx_vlan
    from scapy.layers.l2 import Ether
    # Built packet with NO Dot1Q layer
    sample = Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")

    captured = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            captured.append((rec.levelname, rec.getMessage()))

    h = _Cap()
    logging.getLogger().addHandler(h)
    try:
        # Mock subprocess.run so ethtool doesn't actually run
        with patch("subprocess.run", side_effect=FileNotFoundError):
            _diagnose_tx_vlan("eth0", sample, vlan_id_expected=10)
    finally:
        logging.getLogger().removeHandler(h)

    warnings = [m for lv, m in captured if lv == "WARNING"]
    assert any(
        "config says VLAN tagged" in m and "does NOT contain a Dot1Q" in m
        for m in warnings
    ), (
        f"_diagnose_tx_vlan should WARN when config says tagged but "
        f"the built packet lacks Dot1Q. Captured warnings: {warnings}"
    )


def test_diagnose_tx_vlan_warns_on_tx_vlan_offload_on():
    """Mellanox/Intel tx-vlan-offload=on strips Dot1Q from Scapy
    frames before transmission. _diagnose_tx_vlan must call this
    out so operators know to `ethtool -K <iface> txvlan off`."""
    import logging
    from multithreaded_traffic_gen import _diagnose_tx_vlan
    from scapy.layers.l2 import Ether, Dot1Q
    # Built packet WITH Dot1Q (so the Dot1Q-missing warning doesn't
    # fire — we want to isolate the offload warning)
    sample = Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02") \
        / Dot1Q(vlan=10)

    captured = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            captured.append((rec.levelname, rec.getMessage()))

    h = _Cap()
    logging.getLogger().addHandler(h)
    fake_ethtool = MagicMock(
        returncode=0,
        stdout="Features for eth0:\n"
               "tx-vlan-offload: on\n"
               "rx-vlan-offload: on\n",
        stderr="",
    )
    try:
        with patch("subprocess.run", return_value=fake_ethtool):
            _diagnose_tx_vlan("eth0", sample, vlan_id_expected=10)
    finally:
        logging.getLogger().removeHandler(h)

    warnings = [m for lv, m in captured if lv == "WARNING"]
    assert any(
        "tx-vlan-offload=on" in m and "ethtool -K" in m
        for m in warnings
    ), (
        f"_diagnose_tx_vlan should WARN with the ethtool -K fix when "
        f"tx-vlan-offload is on. Captured warnings: {warnings}"
    )


def test_diagnose_tx_vlan_quiet_when_no_vlan_expected():
    """When stream config has no VLAN (vlan_id None/0), the
    diagnostic should be silent — no spurious warnings for the
    untagged use case."""
    import logging
    from multithreaded_traffic_gen import _diagnose_tx_vlan
    from scapy.layers.l2 import Ether
    sample = Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02")

    captured = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            captured.append((rec.levelname, rec.getMessage()))

    h = _Cap()
    logging.getLogger().addHandler(h)
    try:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            _diagnose_tx_vlan("eth0", sample, vlan_id_expected=None)
            _diagnose_tx_vlan("eth0", sample, vlan_id_expected=0)
    finally:
        logging.getLogger().removeHandler(h)

    warnings = [m for lv, m in captured if lv == "WARNING"]
    assert not any("VLAN" in m for m in warnings), (
        f"No VLAN-related warnings should fire when vlan_id_expected "
        f"is None or 0. Captured: {warnings}"
    )
