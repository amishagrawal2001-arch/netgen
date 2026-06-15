"""v0.5.146: perftest rc!=0 error tail filters out config-dump noise.

Operator screenshot (RDMA Topology Test status bar):

    1 pair | 0 running | 1 done | err: perftest exited rc=1:
    CQ Moderation : 1 CQE Poll Batch : 16 Mtu : 1024[B]
    Link type : Ethernet CPU freq : 2394[MHz] GID index …

That isn't an error message — it's perftest's CONFIG DUMP banner,
which it prints to stdout BEFORE the data rows. When perftest
fails to even start a transfer, those banner lines are the last
content in stdout, and the previous error builder
(`tail = stdout_tail[-10:]`) surfaced them verbatim.

v0.5.146 introduces `_filter_perftest_noise()` — drops lines
matching the well-known config-dump pattern (Mtu, Link type,
CPU freq, GID index, CQ Moderation, Connection type, …) while
preserving anything with operator-actionable hints (error / fail
/ couldn't / refused / timed out / …). The rc-formatting wrapper
`_format_rc_error()` builds the diagnostic from filtered tail, or
returns a clear "no diagnostic — check server log" message with
common-cause hints when nothing useful remains.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


from utils.rdma_perf import (
    _filter_perftest_noise,
    _format_rc_error,
)


# ───── representative perftest stdout samples ────────────────────────────


# The exact lines from the operator screenshot (with surrounding
# context perftest would also have emitted).
SCREENSHOT_HEADER = [
    "Dual-port       : OFF          Device         : mlx5_0",
    "Number of qps   : 1            Transport type : IB",
    "Connection type : RC           Using SRQ      : OFF",
    "PCIe relax order: ON",
    "ibv_wr* API     : ON",
    "TX depth        : 128",
    "CQ Moderation   : 1",
    "CQE Poll Batch  : 16",
    "Mtu             : 1024[B]",
    "Link type       : Ethernet",
    "GID index       : 3",
    "Max inline data : 0[B]",
    "rdma_cm QPs     : OFF",
    "Data ex. method : Ethernet",
    "TOS             : 0",
    "Outstand reads  : 16",
    "CPU freq        : 2394[MHz]",
]


REAL_ERROR_AFTER_HEADER = SCREENSHOT_HEADER + [
    "Couldn't connect to 10.0.0.5:18515 — Connection refused",
    "Failed to modify QP 0xabcd to RTR",
]


HEADER_ONLY_NO_ERROR = list(SCREENSHOT_HEADER)


# ───── _filter_perftest_noise ────────────────────────────────────────────


def test_filter_drops_pure_header_lines():
    """Every line in the operator-screenshot banner matches the
    header pattern; filter must drop them all."""
    out = _filter_perftest_noise(SCREENSHOT_HEADER)
    assert out == [], (
        f"expected all header lines stripped; got {len(out)} survivors: "
        f"{out[:3]}…"
    )


def test_filter_preserves_real_error_lines():
    """Error lines must survive even when surrounded by header
    banner — that's the whole point of the filter."""
    out = _filter_perftest_noise(REAL_ERROR_AFTER_HEADER)
    assert any("Couldn't connect" in line for line in out)
    assert any("Failed to modify QP" in line for line in out)
    # And nothing else should survive (every other line is header).
    for line in out:
        assert ("Couldn't" in line or "Failed" in line)


def test_filter_preserves_error_lines_even_if_title_value_shape():
    """A line like "Status : Connection refused" structurally
    matches the header pattern but carries an actionable hint —
    must NOT be dropped."""
    out = _filter_perftest_noise([
        "Mtu : 1024[B]",                          # drop (header)
        "Status : Connection refused by peer",    # keep (has hint)
        "Link type : Ethernet",                   # drop (header)
    ])
    assert len(out) == 1
    assert "Connection refused" in out[0]


def test_filter_drops_blank_lines():
    """perftest prints blank lines between sections; those
    aren't diagnostic and shouldn't pad the result."""
    out = _filter_perftest_noise(["", "  ", "\n"])
    assert out == []


def test_filter_keeps_unknown_format_lines():
    """Lines that aren't header-shaped AND don't contain an
    explicit hint word — keep them. Operator may still glean
    something useful from a stray `Aborted (core dumped)` line."""
    out = _filter_perftest_noise([
        "Mtu : 1024[B]",          # drop — header shape, no hint
        "Aborted (core dumped)",  # keep — no colon, not header
    ])
    assert len(out) == 1
    assert "Aborted" in out[0]


def test_filter_case_insensitive_hint_match():
    """`ERROR:` (uppercase) and `error:` (lowercase) both qualify
    as hints."""
    a = _filter_perftest_noise(["ERROR: out of memory"])
    b = _filter_perftest_noise(["error: out of memory"])
    assert len(a) == 1 == len(b)


# ───── _format_rc_error ──────────────────────────────────────────────────


def test_format_rc_error_uses_filtered_tail_when_real_error_present():
    """When perftest gave a real error after the header, the
    diagnostic must surface that — NOT the banner."""
    msg = _format_rc_error(1, REAL_ERROR_AFTER_HEADER)
    assert "rc=1" in msg
    assert "Couldn't connect" in msg or "Failed to modify QP" in msg
    # And the operator screenshot's banner words must NOT appear.
    assert "CPU freq" not in msg
    assert "CQ Moderation" not in msg


def test_format_rc_error_when_only_header_present_says_no_diagnostic():
    """The exact operator scenario: perftest exited rc=1 but
    nothing past the config dump made it to stdout. The error
    must NOT be a wall of banner text — it must clearly say
    "no diagnostic" and point at the full log."""
    msg = _format_rc_error(1, HEADER_ONLY_NO_ERROR)
    assert "rc=1" in msg
    assert "no diagnostic" in msg.lower()
    # Operator-pointing direction: "check the full log".
    assert "/api/rdma/perftest/job" in msg
    # Banner WORDS from the screenshot must not leak as the
    # supposed diagnostic. (`GID index` is intentionally mentioned
    # in the common-cause hint text — that's not banner leakage.)
    assert "Mtu : 1024" not in msg
    assert "CPU freq" not in msg
    assert "CQ Moderation" not in msg


def test_format_rc_error_when_only_header_includes_common_cause_hints():
    """Since we have no specific diagnostic, give the operator
    SOMETHING to try. Common RoCE/RDMA failure modes — name them."""
    msg = _format_rc_error(1, HEADER_ONLY_NO_ERROR)
    lowered = msg.lower()
    assert "pfc" in lowered or "ecn" in lowered, (
        "common-cause hint should mention PFC/ECN mismatch"
    )
    assert "gid" in lowered, "should mention GID-index mismatch"
    assert "rocev2" in lowered or "roce" in lowered, (
        "should mention RoCEv2 NIC config"
    )


def test_format_rc_error_empty_tail():
    """No stdout at all — perftest died before printing anything."""
    msg = _format_rc_error(1, [])
    assert "rc=1" in msg
    assert "no diagnostic" in msg.lower()


def test_format_rc_error_includes_rc_number_for_nonstandard_codes():
    """rc=127 (perftest binary not found via sh) or rc=-15 (killed
    by SIGTERM) — both must surface the actual rc, not be flattened
    to '1'."""
    assert "rc=127" in _format_rc_error(127, [])
    assert "rc=-15" in _format_rc_error(-15, [])


def test_format_rc_error_clips_long_tail():
    """The error string ends up in a status banner that struggles
    with multi-line output. Clip the final tail block at ~400 chars
    so the GUI doesn't get a wall."""
    long_lines = [
        "Couldn't connect — " + ("xxx " * 100)
        for _ in range(20)
    ]
    msg = _format_rc_error(1, long_lines)
    # The final substring after "rc=1: " should be capped roughly.
    after = msg.split("rc=1:", 1)[1]
    assert len(after) <= 410, (
        f"unfiltered tail leaked through clipping: {len(after)} chars"
    )


# ───── source-level pin: builder uses the new helper ─────────────────────


SRC = (REPO / "utils" / "rdma_perf.py").read_text()


def test_finalize_block_calls_format_rc_error_helper():
    """The rc!=0 finalize block must call the new helper, not
    re-implement the broken 'last 10 raw stdout lines' shape."""
    assert "_format_rc_error(rc, job.stdout_tail)" in SRC


def test_old_broken_stdout_tail_slice_is_gone():
    """The exact stale code shape — `stdout_tail[-10:]` followed by
    a raw `perftest exited rc=` f-string — must not be in the
    finalize block anymore. (Comment lines referencing the old
    shape historically are fine — they document the fix.)"""
    for line in SRC.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert "stdout_tail[-10:]" not in stripped, (
            "v0.5.146 dropped stdout_tail[-10:] in favor of "
            "_format_rc_error(); a non-comment line just "
            "reintroduced it: " + line
        )
