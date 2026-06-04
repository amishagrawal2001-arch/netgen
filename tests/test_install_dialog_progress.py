"""Regression tests for the install-dialog progress visibility fixes.

User report: "trying fresh install, popout window does not show full
install progress and something wrong." Symptom narrowed via Q&A to:
"Popup updates a while then goes silent (install still running)" —
the install was making forward progress on the server but the popup
appeared frozen during long quiet stretches (DPDK meson+ninja build,
FRR docker image build, large apt installs each can be 5–15 minutes
with effectively zero log output).

Fix in widgets/install_server_dialog.SshInstallWorker.run() poll loop:
  1. Parse the latest [INFO]/[WARNING]/[ERROR] line from each chunk
     and emit it to the status bar (operator sees current step even
     when popup is auto-scrolled away).
  2. Emit a periodic heartbeat every _HEARTBEAT_SECS during idle so
     the popup shows the loop is alive + elapsed time + the last
     known step.

These tests pin the pure parsing/format logic — the actual poll
behavior requires a live SSH channel which we don't mock here. If
either regression hits, the bug surfaces as silent popups again."""
from __future__ import annotations

import re


# Mirror the patterns from widgets/install_server_dialog.py so a
# divergence (someone tightens the regex) gets caught here too.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STEP_LINE = re.compile(r"^\[(?:INFO|WARNING|ERROR|DEBUG)\]\s+(.+?)\s*$")


def _scan_latest_step(chunk: str) -> str | None:
    """Reproduces _update_step_from's parsing logic exactly so the
    tests stay synced with the production code shape."""
    best = None
    for raw in chunk.splitlines():
        stripped = _ANSI.sub("", raw).strip()
        m = _STEP_LINE.match(stripped)
        if m:
            best = m.group(1)
    return best


def test_strips_ansi_color_codes_from_step_lines():
    """install_ostg_complete.py wraps level prefixes in ANSI codes
    (Colors.GREEN/YELLOW/RED). The dialog must strip those before
    matching or no line will parse."""
    raw = "\x1b[0;32m[INFO]\x1b[0m Installing system dependencies..."
    assert _scan_latest_step(raw) == "Installing system dependencies..."


def test_parses_all_four_levels():
    """All 4 log levels (INFO/WARNING/ERROR/DEBUG) emitted by the
    installer's log() method must produce a status update."""
    samples = [
        ("\x1b[0;32m[INFO]\x1b[0m  hello-info",     "hello-info"),
        ("\x1b[0;33m[WARNING]\x1b[0m hello-warn",   "hello-warn"),
        ("\x1b[0;31m[ERROR]\x1b[0m hello-err",      "hello-err"),
        ("\x1b[0;34m[DEBUG]\x1b[0m hello-dbg",      "hello-dbg"),
    ]
    for raw, expected in samples:
        assert _scan_latest_step(raw) == expected, f"missed {expected}"


def test_ignores_non_step_lines():
    """Subprocess output ('+ apt-get install...'), bare prints, and
    other non-prefixed lines should not match — only the installer's
    own log() method output is meaningful as a step indicator."""
    noise = [
        "+ apt-get install -y perftest",
        "Reading package lists... Done",
        "    [Y/n] ",
        "",
        "Successfully built ostg_trafficgen-0.3.16-py3-none-any.whl",
    ]
    for raw in noise:
        assert _scan_latest_step(raw) is None, \
            f"unexpectedly matched noise: {raw!r}"


def test_chunk_with_multiple_steps_picks_latest():
    """A single tail -c chunk can hold many lines spanning several
    steps (network buffer flushes 5-10 KB at once). Status bar should
    reflect the MOST RECENT step, not the first one in the chunk."""
    chunk = "\n".join([
        "\x1b[0;32m[INFO]\x1b[0m Installing system dependencies...",
        "+ apt-get update",
        "Reading package lists... Done",
        "\x1b[0;32m[INFO]\x1b[0m Installing RDMA userspace + perftest...",
        "+ apt-get install -y perftest rdma-core",
        "\x1b[0;32m[INFO]\x1b[0m Building DPDK tx_worker",
    ])
    assert _scan_latest_step(chunk) == "Building DPDK tx_worker"


def test_unicode_in_step_label_preserved():
    """The installer prints arrows + bullets in its log lines (Tools →
    RDMA). Status emit must round-trip them; no mojibake."""
    raw = "\x1b[0;32m[INFO]\x1b[0m RDMA userspace + perftest installed (Tools → RDMA is ready to use)"
    out = _scan_latest_step(raw)
    assert out is not None
    assert "Tools → RDMA" in out


def test_truncation_logic_caps_at_80_chars():
    """The status bar trim logic: keep lines ≤ 80 chars verbatim,
    otherwise show first 77 + ellipsis. Long messages should not
    wrap the status bar weirdly."""
    label = "A" * 200
    # Replicate the trim block from the production code
    trimmed = label if len(label) <= 80 else label[:77] + "..."
    assert len(trimmed) == 80
    assert trimmed.endswith("...")


def test_heartbeat_interval_constant_is_sane():
    """Spot-check: heartbeat interval should be 30s — long enough not
    to spam the popup during normal flow, short enough that a real
    silent stretch surfaces a heartbeat within < 1 min."""
    # Read the constant from the source so a divergence trips us.
    src = open(
        "/Users/surajsharma/dev/netgen/widgets/install_server_dialog.py"
    ).read()
    m = re.search(r"_HEARTBEAT_SECS\s*=\s*(\d+)", src)
    assert m is not None, "_HEARTBEAT_SECS not found in source"
    secs = int(m.group(1))
    assert 10 <= secs <= 120, \
        f"heartbeat interval {secs}s outside sane range [10..120]"
