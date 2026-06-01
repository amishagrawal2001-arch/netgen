"""v0.3.4 — RX signature matcher pinning.

Closes the silent-data-loss bug where the RX sniffer's signature
matcher only recognised the Scapy TX-side packet format and
silently ignored DPDK-tagged packets. Pre-v0.3.4 the matcher
was a fixed `f"[{stream_id}#".encode()` prefix — it didn't
account for the `/q<queue_id>` segment the C-side tx_worker
embeds between `<stream_id>` and `#`.

Result of the old behaviour: any stream with
`flow_tracking=True` AND `dpdk_enable=True` showed
`rx_count=0` / `loss=100%` in the GUI — the operator saw
"none of my DPDK packets got through" when they actually all
arrived; the sniffer just couldn't recognise them.

The v0.3.4 fix replaces the prefix with a regex that
tolerates the optional `/q\\d+` segment so a single sniffer
matches both backends. These tests pin the regex shape +
behaviour so any future "simplification" can't quietly
re-introduce the silent-zero-RX bug.
"""

import pytest

from multithreaded_traffic_gen import _build_sig_pattern


# ─────────────────────────────────────── pattern shape
def test_v0_3_4_returns_compiled_bytes_regex():
    """The helper must return a compiled regex (we'd be sad if
    a future refactor changed it to a function or a string)."""
    import re as _re
    pat = _build_sig_pattern("stream-x")
    assert isinstance(pat, _re.Pattern), \
        f"expected re.Pattern, got {type(pat).__name__}"
    # Bytes regex (the sniffer matches against raw packet bytes).
    assert pat.pattern.startswith(b"\\["), \
        f"pattern should anchor on '[', got {pat.pattern!r}"


# ─────────────────────────────────────── behavioural — Scapy format
@pytest.mark.parametrize("stream_id, seq", [
    ("stream-abc", 0),
    ("stream-abc", 1),
    ("stream-abc", 999999),
    ("a", 0),
    ("stream.with.dots", 42),   # dots are common in stream IDs
    ("UUID-1234-5678", 7),
])
def test_v0_3_4_matches_scapy_format(stream_id, seq):
    """Scapy emits ``[<stream_id>#<seq>]``."""
    pat = _build_sig_pattern(stream_id)
    payload = f"[{stream_id}#{seq}]".encode()
    assert pat.search(payload), (
        f"v0.3.4 regression — sniffer doesn't recognise Scapy "
        f"packet for {stream_id!r}"
    )


# ─────────────────────────────────────── behavioural — DPDK format
@pytest.mark.parametrize("stream_id, queue_id, seq", [
    ("stream-abc", 0, 0),
    ("stream-abc", 0, 1),
    ("stream-abc", 1, 9999),
    ("stream-abc", 15, 0),    # multi-queue DPDK setup
    ("stream-abc", 127, 0),   # 3-digit queue ID (some hi-perf NICs)
    ("a", 0, 0),
    ("stream.with.dots", 3, 42),
])
def test_v0_3_4_matches_dpdk_format(stream_id, queue_id, seq):
    """DPDK emits ``[<stream_id>/q<queue_id>#<seq>]`` — the
    `/q<queue_id>` is the critical bit the pre-v0.3.4 matcher
    silently ignored, causing rx_count=0 on DPDK streams."""
    pat = _build_sig_pattern(stream_id)
    payload = f"[{stream_id}/q{queue_id}#{seq}]".encode()
    assert pat.search(payload), (
        f"v0.3.4 BUG REGRESSED: sniffer doesn't recognise DPDK "
        f"packet for {stream_id!r} on queue {queue_id} — "
        f"flow-tracking + DPDK silently shows rx_count=0"
    )


# ─────────────────────────────────────── negative — no false matches
def test_v0_3_4_doesnt_match_different_stream_id():
    """The matcher MUST distinguish streams. Two streams on the
    same RX interface with similar names should not bleed into
    each other's RX counts."""
    pat = _build_sig_pattern("stream-alpha")
    # Same prefix but different ID — must NOT match.
    for other in (
        b"[stream-beta#42]",
        b"[stream-alpha-2#42]",   # prefix collision
        b"[alpha#42]",
        b"stream-alpha#42",        # missing opening bracket
        b"[stream-alpha 42]",      # missing # separator
    ):
        assert not pat.search(other), (
            f"matcher false-positive: {other!r} should not match "
            f"stream-alpha's pattern"
        )


def test_v0_3_4_doesnt_match_partial_queue_segment():
    """The /q segment must require digits — `[stream-x/q#0]`
    (queue ID missing) should NOT match either format. If we
    accepted bare `/q#`, a malformed DPDK packet could land in
    the wrong stream's count."""
    pat = _build_sig_pattern("stream-x")
    for malformed in (
        b"[stream-x/q#0]",          # no digits after /q
        b"[stream-x/qa#0]",         # letter instead of digit
        b"[stream-x/queue0#0]",     # full word instead of /q\d+
    ):
        assert not pat.search(malformed), (
            f"matcher accepted malformed DPDK-ish format: {malformed!r}"
        )


# ─────────────────────────────────────── regex-meta safety
def test_v0_3_4_stream_id_is_regex_escaped():
    """stream_id values can legitimately contain regex meta-chars
    (`.`, `+`, `?`, `(`, etc. — UUIDs use `-`; some installs use
    `.` separators). `re.escape` must be applied so a stream_id
    like `s.x` doesn't match `sax` packets too."""
    pat = _build_sig_pattern("s.x")
    # Literal `s.x` matches.
    assert pat.search(b"[s.x#0]")
    # `sax` must NOT match — would have if `.` were treated as
    # regex meta.
    assert not pat.search(b"[sax#0]"), (
        "stream_id not re.escape'd — `.` is being treated as "
        "regex wildcard, leading to cross-stream RX bleed"
    )


def test_v0_3_4_matches_within_larger_payload():
    """Real packets contain the signature embedded in a longer
    payload (UDP body, possibly with padding before/after). The
    matcher must use search, not full-match."""
    pat = _build_sig_pattern("stream-abc")
    payload = b"\x00\x01\x02filler...[stream-abc#42]more padding\xff"
    assert pat.search(payload)
    # And the DPDK variant too.
    payload2 = b"junk[stream-abc/q0#42]\xff\xff"
    assert pat.search(payload2)
