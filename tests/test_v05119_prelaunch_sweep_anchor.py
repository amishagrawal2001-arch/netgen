"""v0.5.119: TX pre-launch sweep must NOT match rx_worker cmdlines.

Pre-fix the sweep regex was just `--stream-id <id>`, which matches
both tx_worker AND rx_worker because they share the argv. Every
DPDK stream start would pkill -TERM the rx_worker the launcher
had spawned ~1 second earlier, leaving RX=0 with exit_code=0 and
a clean stderr (signal handler triggered the normal exit path
before any error could surface).

Fix: anchor on `tx_worker` substring in the cmdline. Both the
TX binary path (`/usr/local/bin/tx_worker`) and any worktree-local
fallback start with `tx_worker` as the program name, and the
rx_worker binary cmdline never contains the literal string
`tx_worker` anywhere.

Captured on srv06 (san-hp-srv06) at 19:29:14 UTC via the v0.5.118
stderr-capture diagnostic — the rx_worker died at duration_s=0.165
with the LAST stderr line being "rx_worker launched 1 queue
worker(s) on port 0" and the netgen-server log showing
"[dpdk] pre-launch: 1 stale tx_worker(s) ... pids=3086625
terminating" one second after rx_worker spawned.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# Sample cmdlines from real srv06 logs (v0.5.118 era).
RX_WORKER_CMDLINE = (
    "/usr/local/bin/rx_worker -l 0,1,2,3 -n 4 "
    "--file-prefix rxw_e0102865_3072444_1781378617643 "
    "-a 0000:2b:00.1 -- --stream-id e0102865-df92-43d4-ba80-1439fbf07974 "
    "--rx-queues 1"
)
TX_WORKER_CMDLINE = (
    "/usr/local/bin/tx_worker -l 0,1,2,3,4,5,6,7,8,9,10,11,12 -n 4 "
    "--file-prefix txw_e0102865_ens2f0np0_3072444_1781378954092 "
    "-a 0000:2b:00.0 -- --src-mac 5c:25:73:3f:30:56 "
    "--dst-mac 5c:25:73:3f:30:57 --src-ip 10.0.0.1 --dst-ip 10.0.0.2 "
    "--src-port 1234 --dst-port 4791 --size 512 --pps 1000 "
    "--stream-id e0102865-df92-43d4-ba80-1439fbf07974 --vlan 100 "
    "--tx-cores 12 --enable-timestamps"
)
STREAM_ID = "e0102865-df92-43d4-ba80-1439fbf07974"


def _build_pattern(stream_id: str) -> str:
    """Mirror the in-source pattern construction so the test
    stays pinned to the actual code. If someone changes the
    pattern they have to update this and the test fails loudly."""
    return f"tx_worker.*--stream-id {stream_id}"


def test_new_pattern_matches_tx_worker_cmdline():
    """The TX-side sweep must STILL catch a real stale tx_worker.
    Without this check, the fix could over-narrow the regex and
    re-introduce the original bug (orphan tx_worker keeps blasting
    after a restart)."""
    pat = _build_pattern(STREAM_ID)
    assert re.search(pat, TX_WORKER_CMDLINE), (
        f"New pattern {pat!r} must match a real tx_worker cmdline. "
        f"Cmdline: {TX_WORKER_CMDLINE}"
    )


def test_new_pattern_does_not_match_rx_worker_cmdline():
    """The bug. Pre-fix pattern (`--stream-id <id>`) matched both
    binaries; new pattern must reject rx_worker explicitly so the
    sweep can't pkill the rx_worker the launcher just spawned."""
    pat = _build_pattern(STREAM_ID)
    assert re.search(pat, RX_WORKER_CMDLINE) is None, (
        f"New pattern {pat!r} must NOT match an rx_worker cmdline. "
        f"This was the v0.5.119 bug — pkill -TERM hit the rx_worker "
        f"as collateral damage and the operator saw RX=0 with no "
        f"surfaceable error. Cmdline: {RX_WORKER_CMDLINE}"
    )


def test_legacy_pattern_would_match_rx_worker():
    """Pin the bug. If anyone reverts to the old pattern format
    (`--stream-id <id>` without an anchor), this test ensures we
    remember WHY the anchor exists. Removing this test is a flag
    that someone may have lost the lesson."""
    legacy_pat = f"--stream-id {STREAM_ID}"
    assert re.search(legacy_pat, RX_WORKER_CMDLINE), (
        "The legacy pattern DOES match rx_worker (that's the bug "
        "this version fixes). If this assertion fails, your sample "
        "cmdline doesn't actually carry --stream-id any more — fix "
        "the sample, don't drop the anchor."
    )


def test_source_uses_anchored_pattern():
    """Read the actual source line and confirm it carries
    `tx_worker.*` before `--stream-id`. Regression-proof against
    a refactor that silently restores the old broad pattern."""
    src_path = REPO / "utils" / "dpdk_tx_worker.py"
    text = src_path.read_text()
    # The pattern lives on the line constructing _pat. We don't
    # want to require an exact source-line match — only that the
    # anchor appears in the construction site.
    assert 'f"tx_worker.*--stream-id {stream_id}"' in text or \
           "f\"tx_worker.*--stream-id {stream_id}\"" in text, (
        "Source must anchor the pre-launch sweep regex on "
        "`tx_worker` before `--stream-id`. The unanchored form "
        "matches rx_worker cmdlines too and pkills them."
    )


def test_pattern_safe_against_uuid_substring_collisions():
    """A stream_id is a UUID; the regex shouldn't blow up on
    other streams' UUIDs sharing a prefix. The anchor + literal
    stream_id is enough — UUIDs are unique."""
    other_stream = "abcdef12-3456-7890-abcd-ef1234567890"
    other_cmd = TX_WORKER_CMDLINE.replace(STREAM_ID, other_stream)
    pat = _build_pattern(STREAM_ID)
    assert re.search(pat, other_cmd) is None, (
        "Sweep for stream A must not match stream B's cmdline. "
        "If this fires you've introduced a partial-match bug."
    )
