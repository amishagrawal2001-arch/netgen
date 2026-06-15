"""v0.5.141: Blast RDMA Flow / Topology Test jobs appear in Streams tab.

Operator report: "I don't see RDMA flows pls check the gap in RDMA"

Root cause: there are two RDMA paths in the server.

1. Per-stream `engine = rdma` (Add Stream dialog → engine combo
   "RDMA"): handled by `utils/rdma_stream_engine.start_rdma_stream`
   which DID register with `stream_tracker.add_stream(...)`. Streams
   tab sees it.

2. Standalone RDMA flows (Tools → RDMA → Blast a RDMA Flow / Topology
   Test): these dialogs POST directly to `/api/rdma/perftest/start`.
   That route only registered with `utils/rdma_perf` job registry and
   `utils/rdma_handshake`. It NEVER touched `stream_tracker`. So the
   Streams tab was blind to them.

v0.5.141 extracts the tracker-registration plumbing from
`start_rdma_stream` into a shared `register_perftest_with_tracker`
helper, and the `/api/rdma/perftest/start` route calls it for
client-role jobs. Server-role jobs are passive listeners; mirroring
them too would duplicate every Blast row.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_SERVER = (REPO / "run_tgen_server.py").read_text()
SRC_ENGINE = (REPO / "utils" / "rdma_stream_engine.py").read_text()


# ----- the helper exists --------------------------------------------------

def test_register_helper_module_level():
    """`register_perftest_with_tracker` must be importable from
    `utils.rdma_stream_engine` so the route in run_tgen_server.py
    can use it."""
    from utils.rdma_stream_engine import register_perftest_with_tracker
    assert callable(register_perftest_with_tracker)


def test_start_rdma_stream_delegates_to_helper():
    """The per-stream `engine=rdma` path must use the same helper
    so the two code paths can't drift."""
    assert "register_perftest_with_tracker(" in SRC_ENGINE
    # And the function must NOT still hold the old inline
    # `tracker.add_stream({` block — verify the refactor.
    occurrences = SRC_ENGINE.count('tracker.add_stream({')
    assert occurrences == 1, (
        f"add_stream should appear once (inside the helper). "
        f"Found {occurrences} — the inline block in start_rdma_stream "
        f"wasn't removed."
    )


# ----- the /api/rdma/perftest/start route wires the helper ----------------

def test_perftest_start_route_calls_helper_for_client_role():
    """The /api/rdma/perftest/start handler must call the helper
    when role == 'client' so Blast RDMA Flow rows surface in
    Streams tab."""
    # Find the route's body
    match = re.search(
        r'def api_rdma_perftest_start\(\):.*?(?=\n@app\.route)',
        SRC_SERVER, flags=re.DOTALL,
    )
    assert match is not None, "couldn't find api_rdma_perftest_start"
    body = match.group(0)
    assert "register_perftest_with_tracker" in body, (
        "perftest/start route must call register_perftest_with_tracker"
    )
    assert 'role == "client"' in body, (
        "registration must be gated on role == 'client'"
    )


def test_perftest_start_route_skips_server_role():
    """Server-role jobs are passive listeners. Registering them too
    would duplicate every Blast pair row (one client + one server
    side of the same flow)."""
    match = re.search(
        r'def api_rdma_perftest_start\(\):.*?(?=\n@app\.route)',
        SRC_SERVER, flags=re.DOTALL,
    )
    assert match is not None
    body = match.group(0)
    # Find where the helper is called and confirm it's inside an
    # `if role == "client":` block, not unconditional.
    assert re.search(
        r'if role == "client":[^@]*?register_perftest_with_tracker\(',
        body, flags=re.DOTALL,
    ), "helper call must be inside `if role == 'client':` guard"


# ----- the helper behavior (mock tracker) ---------------------------------

class _MockTracker:
    """Captures add_stream / mark_runtime_engine / update_tx_by_id
    calls so we can verify the helper invokes the right tracker
    methods with the right shape."""

    def __init__(self):
        self.added = []
        self.runtime_engines = []
        self.tx_updates = []

    def add_stream(self, entry):
        self.added.append(entry)

    def mark_runtime_engine(self, interface, stream_id, *, runtime_engine):
        self.runtime_engines.append(
            (interface, stream_id, runtime_engine))

    def update_tx_by_id(self, interface, stream_id, delta):
        self.tx_updates.append((interface, stream_id, delta))


def test_helper_registers_stream_with_expected_shape(monkeypatch):
    """The helper must hand the tracker a dict with the keys the
    stream_tracker.add_stream contract expects (matches the DPDK /
    scapy path so the Streams tab renders without special-casing)."""
    # Stub out the poll thread's perftest module so the helper
    # doesn't actually hit utils.rdma_perf during the test.
    import utils.rdma_perf as _rp
    monkeypatch.setattr(_rp, "get_perftest_job", lambda jid: None)
    monkeypatch.setattr(_rp, "stop_perftest", lambda jid: None)

    from utils.rdma_stream_engine import register_perftest_with_tracker
    tracker = _MockTracker()
    register_perftest_with_tracker(
        tracker=tracker,
        stream_id="job-abc",
        job_id="job-abc",
        interface="mlx5_0",
        stream_name="my blast flow",
        test="send_bw",
        msg_size=65536,
        stop_event=threading.Event(),
        peer_addr="10.0.0.2",
    )
    assert len(tracker.added) == 1
    entry = tracker.added[0]
    assert entry["stream_id"] == "job-abc"
    assert entry["interface"] == "mlx5_0"
    assert entry["stream_name"] == "my blast flow"
    assert entry["frame_size"] == 65536
    assert entry["flow_tracking_enabled"] is False
    assert entry["engine"] == "rdma"
    assert entry["rdma"]["test"] == "send_bw"
    assert entry["rdma"]["peer_addr"] == "10.0.0.2"
    assert entry["rdma"]["msg_size"] == 65536
    assert entry["rdma"]["job_id"] == "job-abc"


def test_helper_marks_runtime_engine_rdma(monkeypatch):
    """The Engine column in Streams tab renders 'RDMA' when
    runtime_engine == 'rdma'. Helper must call mark_runtime_engine."""
    import utils.rdma_perf as _rp
    monkeypatch.setattr(_rp, "get_perftest_job", lambda jid: None)
    monkeypatch.setattr(_rp, "stop_perftest", lambda jid: None)

    from utils.rdma_stream_engine import register_perftest_with_tracker
    tracker = _MockTracker()
    register_perftest_with_tracker(
        tracker=tracker,
        stream_id="job-1",
        job_id="job-1",
        interface="mlx5_0",
        stream_name="blast-1",
        test="write_bw",
        msg_size=1024,
        stop_event=threading.Event(),
    )
    assert tracker.runtime_engines == [("mlx5_0", "job-1", "rdma")]


def test_helper_falls_back_to_synthetic_name(monkeypatch):
    """When the caller doesn't supply a stream_name (or note), the
    helper synthesizes one like 'rdma-send_bw-abcd1234' so the
    Streams tab row isn't blank."""
    import utils.rdma_perf as _rp
    monkeypatch.setattr(_rp, "get_perftest_job", lambda jid: None)
    monkeypatch.setattr(_rp, "stop_perftest", lambda jid: None)

    from utils.rdma_stream_engine import register_perftest_with_tracker
    tracker = _MockTracker()
    register_perftest_with_tracker(
        tracker=tracker,
        stream_id="job-fedcba98",
        job_id="job-fedcba98",
        interface="mlx5_0",
        stream_name="",   # blank — exercise the fallback
        test="read_lat",
        msg_size=128,
        stop_event=threading.Event(),
    )
    name = tracker.added[0]["stream_name"]
    assert "rdma" in name and "read_lat" in name
    # short job-id should appear in the synthetic name for disambiguation
    assert name.endswith("job-fedc"[-len("fedcba98"[:8]):])


def test_helper_swallows_tracker_failure(monkeypatch):
    """If the tracker rejects add_stream (e.g., duplicate id), the
    helper must log + continue — the perftest job is still useful
    even if the Streams row never appears."""
    import utils.rdma_perf as _rp
    monkeypatch.setattr(_rp, "get_perftest_job", lambda jid: None)
    monkeypatch.setattr(_rp, "stop_perftest", lambda jid: None)

    from utils.rdma_stream_engine import register_perftest_with_tracker

    class _BrokenTracker:
        def add_stream(self, entry):
            raise RuntimeError("duplicate stream_id")

        def mark_runtime_engine(self, *a, **kw):
            raise RuntimeError("never reached")

    # Must not raise.
    register_perftest_with_tracker(
        tracker=_BrokenTracker(),
        stream_id="job-x",
        job_id="job-x",
        interface="mlx5_0",
        stream_name="x",
        test="send_bw",
        msg_size=65536,
        stop_event=threading.Event(),
    )


def test_helper_tolerates_missing_tracker_methods(monkeypatch):
    """A tracker that doesn't implement mark_runtime_engine (older
    API) shouldn't break the helper."""
    import utils.rdma_perf as _rp
    monkeypatch.setattr(_rp, "get_perftest_job", lambda jid: None)
    monkeypatch.setattr(_rp, "stop_perftest", lambda jid: None)

    from utils.rdma_stream_engine import register_perftest_with_tracker

    class _MinimalTracker:
        def __init__(self):
            self.added = []

        def add_stream(self, entry):
            self.added.append(entry)

    tracker = _MinimalTracker()
    register_perftest_with_tracker(
        tracker=tracker,
        stream_id="job-y",
        job_id="job-y",
        interface="mlx5_0",
        stream_name="y",
        test="send_bw",
        msg_size=65536,
        stop_event=threading.Event(),
    )
    assert len(tracker.added) == 1
