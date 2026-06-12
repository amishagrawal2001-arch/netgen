"""v0.5.105: DPDK RX worker launcher tests.

Built after srv06's RX=0 saga where 24M pps TX overwhelmed the kernel
netdev (250M cumulative rx_dropped). DPDK RX bypasses the kernel
entirely — line-rate accurate, hardware-level counter source.

These tests cover the Python launcher (`utils/dpdk_rx_worker.py`)
in isolation. The C binary is not tested here (covered separately
by a CI integration test that exercises real DPDK on a CI runner
with a kernel-loopback iface).

The launcher uses subprocess.Popen + a stdout-tailing thread. Tests
stub Popen with a fake that emits scripted lines so we can verify:
  - argv construction (EAL + app args, filter wiring)
  - stdout parsing into latest() / final()
  - signal-driven stop sequence (SIGTERM → SIGKILL fallback)
  - graceful handling of garbage / parse errors / dying worker
"""
from __future__ import annotations

import io
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-rxw.db")

from utils import dpdk_rx_worker as drw


# ─── Source layout sanity ───


def test_rx_worker_source_files_exist():
    """Wheel must ship rx_worker.c + meson.build (mirrors tx_worker
    package-data; netgen-upgrade's rebuild depends on them)."""
    repo = Path(__file__).resolve().parents[1]
    src = repo / "resources" / "dpdk" / "rx_worker"
    assert src.is_dir(), f"rx_worker source dir missing: {src}"
    assert (src / "rx_worker.c").is_file()
    assert (src / "meson.build").is_file()


def test_rx_worker_c_defines_expected_argv_flags():
    """The Python launcher passes --vlan/--dst-port/--src-port/
    --src-ip/--dst-ip/--duration/--rx-queues/--stream-id. The C
    side must accept them all."""
    c_path = (Path(__file__).resolve().parents[1]
              / "resources" / "dpdk" / "rx_worker" / "rx_worker.c")
    src = c_path.read_text()
    for flag in ("\"stream-id\"", "\"vlan\"", "\"dst-port\"",
                 "\"src-port\"", "\"src-ip\"", "\"dst-ip\"",
                 "\"duration\"", "\"rx-queues\""):
        assert flag in src, f"missing argv flag {flag} in rx_worker.c"


def test_rx_worker_c_emits_json_heartbeat_keys():
    """The launcher's stdout reader parses these specific keys. If
    the C side renames them, the launcher silently returns empty
    snapshots. Lock the contract."""
    c_path = (Path(__file__).resolve().parents[1]
              / "resources" / "dpdk" / "rx_worker" / "rx_worker.c")
    src = c_path.read_text()
    for key in ("rx_pkts", "rx_bytes", "rx_pps", "rx_bps",
                "matched_pkts", "hw_imissed", "stream_id"):
        assert f'\\"{key}\\"' in src, f"missing JSON key {key}"
    # Final-line marker the launcher uses to populate .final()
    assert '\\"final\\":true' in src


# ─── Binary resolution ───


def test_resolve_rx_worker_bin_returns_none_when_nothing_found(monkeypatch):
    """Fresh checkout / CI runner: no rx_worker installed
    anywhere. resolve must return None (caller falls back to Scapy)
    instead of returning a bogus path that errors at exec time."""
    monkeypatch.delenv("RX_WORKER_BIN", raising=False)
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    # importlib.resources.files would also return non-existent paths;
    # the function catches that via os.path.exists check.
    assert drw._resolve_rx_worker_bin() is None


def test_resolve_rx_worker_bin_honours_env_override(monkeypatch, tmp_path):
    """RX_WORKER_BIN env wins over all other paths (dev override)."""
    fake = tmp_path / "fake-rx-worker"
    fake.write_text("#!/bin/sh\necho rx\n")
    fake.chmod(0o755)
    monkeypatch.setenv("RX_WORKER_BIN", str(fake))
    assert drw._resolve_rx_worker_bin() == str(fake.resolve())


def test_resolve_rx_worker_bin_prefers_usr_local(monkeypatch, tmp_path):
    """/usr/local/bin/rx_worker beats install-dir copies (same
    rationale as tx_worker — install_dpdk.sh's authoritative
    drop)."""
    monkeypatch.delenv("RX_WORKER_BIN", raising=False)
    # Pretend /usr/local/bin/rx_worker exists but other paths don't
    def exists(p):
        return p == "/usr/local/bin/rx_worker"
    monkeypatch.setattr(os.path, "exists", exists)
    assert drw._resolve_rx_worker_bin() == "/usr/local/bin/rx_worker"


# ─── start_rx_worker argv construction ───


class _FakePopen:
    """subprocess.Popen stand-in that emits scripted stdout lines
    and tracks signals. Returncode flips to 0 on poll() once we
    inject a `terminated` flag."""
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.signals_received: list[int] = []
        self._stdout_lines: list[str] = []
        self._terminated = False
        self._wait_called = False
        # Default scripted output: nothing yet
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.pid = 99999

    def script(self, lines: list[str]) -> None:
        """Push scripted stdout lines."""
        self._stdout_lines = lines
        self.stdout = io.StringIO("".join(line if line.endswith("\n")
                                          else line + "\n"
                                          for line in lines))

    def poll(self):
        return 0 if self._terminated else None

    def send_signal(self, sig):
        self.signals_received.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self._terminated = True

    def wait(self, timeout=None):
        self._wait_called = True
        return 0

    def kill(self):
        self.send_signal(signal.SIGKILL)


def _make_handle_with_fake(monkeypatch, fake: _FakePopen,
                           binary: str = "/usr/local/bin/rx_worker"):
    """Helper: stub _resolve_rx_worker_bin + Popen to use the fake."""
    monkeypatch.setattr(drw, "_resolve_rx_worker_bin",
                        lambda: binary)
    monkeypatch.setattr(drw.subprocess, "Popen",
                        lambda *a, **kw: fake)


def test_start_rx_worker_argv_contains_eal_and_app(monkeypatch):
    fake = _FakePopen([])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(
        stream_id="1b0d3250-87f4-46e0-b7ef-b30468f9c416",
        pci_bdf="0000:2b:00.1",
        lcores="0,1,2",
        vlan=100, dst_port=4791, src_ip="10.0.0.1",
    )
    # The fake records the cmd argv via Popen positional arg
    # — but we didn't pass it through. Re-check via the handle.
    cmd = h.cmd
    # EAL chunk
    assert cmd[0] == "/usr/local/bin/rx_worker"
    assert "-l" in cmd and "0,1,2" in cmd
    assert "-n" in cmd and "4" in cmd
    assert "--file-prefix" in cmd
    # The file prefix encodes stream id + pid for log greppability
    fp_idx = cmd.index("--file-prefix") + 1
    assert cmd[fp_idx].startswith("rxw_1b0d3250_")
    assert "-a" in cmd and "0000:2b:00.1" in cmd
    # Separator before app args
    assert "--" in cmd
    # App args
    assert "--stream-id" in cmd
    assert "--vlan" in cmd and "100" in cmd
    assert "--dst-port" in cmd and "4791" in cmd
    assert "--src-ip" in cmd and "10.0.0.1" in cmd
    drw.stop_rx_worker(h)


def test_start_rx_worker_omits_unset_filter_args(monkeypatch):
    """If a filter dim isn't passed, the launcher should NOT add
    its argv flag. Pre-fix bug would be passing `--vlan` with no
    value, breaking getopt."""
    fake = _FakePopen([])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(
        stream_id="s",
        pci_bdf="0000:2b:00.1",
    )
    cmd = h.cmd
    # No filter flags present
    for absent in ("--vlan", "--dst-port", "--src-port",
                   "--src-ip", "--dst-ip", "--duration"):
        assert absent not in cmd, f"unexpected {absent} in argv"
    drw.stop_rx_worker(h)


def test_start_rx_worker_raises_without_pci_bdf(monkeypatch):
    monkeypatch.setattr(drw, "_resolve_rx_worker_bin",
                        lambda: "/usr/local/bin/rx_worker")
    with pytest.raises(ValueError, match="pci_bdf"):
        drw.start_rx_worker(stream_id="s", pci_bdf="")


def test_start_rx_worker_raises_when_binary_missing(monkeypatch):
    """If no rx_worker binary exists, callers (the stream
    launcher) must know to fall back to Scapy."""
    monkeypatch.setattr(drw, "_resolve_rx_worker_bin", lambda: None)
    with pytest.raises(FileNotFoundError, match="rx_worker"):
        drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")


# ─── stdout parsing ───


def test_handle_parses_heartbeat_into_latest(monkeypatch):
    fake = _FakePopen([])
    fake.script([
        '{"ts":1781274320,"stream_id":"s","rx_pkts":1000,'
        '"rx_bytes":64000,"rx_pps":1000,"rx_bps":512000,'
        '"matched_pkts":1000,"matched_bytes":64000,'
        '"hw_imissed":0,"hw_ierrors":0,"hw_rx_nombuf":0}',
    ])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    # Reader thread is non-deterministic but the stream is in-memory
    # so it drains almost immediately. Brief wait to be safe.
    for _ in range(50):
        if h.latest():
            break
        time.sleep(0.01)
    snap = h.latest()
    assert snap, "reader thread didn't parse the line"
    assert snap["rx_pkts"] == 1000
    assert snap["rx_pps"] == 1000
    assert snap["matched_pkts"] == 1000
    drw.stop_rx_worker(h)


def test_handle_parses_final_line_separately(monkeypatch):
    """The `{"final":true,...}` line goes to .final(), NOT
    .latest() — so callers can distinguish "last seen counter"
    from "what the worker reported on exit"."""
    fake = _FakePopen([])
    fake.script([
        '{"ts":1781274320,"stream_id":"s","rx_pkts":100,'
        '"rx_bytes":6400,"rx_pps":100,"rx_bps":51200,'
        '"matched_pkts":100,"matched_bytes":6400,'
        '"hw_imissed":0,"hw_ierrors":0,"hw_rx_nombuf":0}',
        '{"final":true,"stream_id":"s","rx_pkts":120,'
        '"rx_bytes":7680,"matched_pkts":120,"matched_bytes":7680,'
        '"duration_s":5.02,"hw_imissed":0,"hw_ierrors":0}',
    ])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    for _ in range(50):
        if h.final():
            break
        time.sleep(0.01)
    final = h.final()
    assert final is not None
    assert final["rx_pkts"] == 120
    assert final["duration_s"] == 5.02
    latest = h.latest()
    assert latest["rx_pkts"] == 100, (
        "latest must NOT be overwritten by the final line"
    )
    drw.stop_rx_worker(h)


def test_handle_ignores_garbage_stdout_lines(monkeypatch):
    """EAL prelude on stdout (non-JSON) must not crash the reader.
    The C side stderrs everything that isn't a JSON heartbeat, but
    some DPDK versions splatter through stdout — defend."""
    fake = _FakePopen([])
    fake.script([
        "EAL: VFIO support initialized",
        "TELEMETRY: No legacy callbacks",
        "",
        "{not-json",
        '{"ts":0,"stream_id":"s","rx_pkts":42,'
        '"rx_bytes":2688,"rx_pps":42,"rx_bps":21504,'
        '"matched_pkts":42,"matched_bytes":2688,'
        '"hw_imissed":0,"hw_ierrors":0,"hw_rx_nombuf":0}',
    ])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    for _ in range(50):
        if h.latest():
            break
        time.sleep(0.01)
    assert h.latest()["rx_pkts"] == 42
    drw.stop_rx_worker(h)


# ─── Stop sequence ───


def test_stop_rx_worker_sends_sigterm_first(monkeypatch):
    fake = _FakePopen([])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    drw.stop_rx_worker(h, timeout_s=0.5)
    assert signal.SIGTERM in fake.signals_received, (
        "stop must SIGTERM before SIGKILL"
    )


def test_stop_rx_worker_sigkill_fallback_on_timeout(monkeypatch):
    """If the worker ignores SIGTERM (wedged), the launcher must
    SIGKILL after timeout_s — otherwise the netgen server would
    leak rx_worker processes across stream-restart cycles."""
    fake = _FakePopen([])
    # Make wait() raise TimeoutExpired the first time so the
    # launcher proceeds to SIGKILL.
    raises = [True]

    def _wait(timeout=None):
        if raises[0]:
            raises[0] = False
            raise subprocess.TimeoutExpired(cmd="rx_worker", timeout=timeout)
        return 0
    fake.wait = _wait
    # Don't auto-terminate on SIGTERM (simulate wedged worker)
    orig_send = fake.send_signal

    def _send(sig):
        fake.signals_received.append(sig)
        if sig == signal.SIGKILL:
            fake._terminated = True
    fake.send_signal = _send
    fake.kill = lambda: _send(signal.SIGKILL)

    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    drw.stop_rx_worker(h, timeout_s=0.2)
    assert signal.SIGTERM in fake.signals_received
    assert signal.SIGKILL in fake.signals_received


def test_stop_rx_worker_returns_final_summary(monkeypatch):
    fake = _FakePopen([])
    fake.script([
        '{"final":true,"stream_id":"s","rx_pkts":555,'
        '"rx_bytes":35520,"matched_pkts":555,"matched_bytes":35520,'
        '"duration_s":1.0,"hw_imissed":0,"hw_ierrors":0}',
    ])
    _make_handle_with_fake(monkeypatch, fake)
    h = drw.start_rx_worker(stream_id="s", pci_bdf="0000:2b:00.1")
    # Give the reader a moment to drain
    for _ in range(50):
        if h.final():
            break
        time.sleep(0.01)
    summary = drw.stop_rx_worker(h)
    assert summary.get("rx_pkts") == 555


# ─── Bundled wheel integration ───


def test_pyproject_ships_rx_worker_sources():
    """v0.5.105: pyproject.toml package-data must include rx_worker
    sources, mirroring tx_worker. Without this, netgen-upgrade's
    _rebuild_rx_worker can't find the sources in the venv."""
    repo = Path(__file__).resolve().parents[1]
    pyproject = (repo / "pyproject.toml").read_text()
    for glob in ('rx_worker/*.c', 'rx_worker/meson.build'):
        assert glob in pyproject, (
            f"pyproject.toml missing rx_worker glob {glob!r} — "
            f"wheel won't include the source; netgen-upgrade rebuild "
            f"will skip with 'wheel predates v0.5.105' warning"
        )


# ─── netgen-upgrade integration ───


def test_netgen_upgrade_rebuilds_rx_worker():
    """netgen-upgrade must call _rebuild_rx_worker, otherwise wheel
    upgrades from v0.5.104 → v0.5.105 wouldn't pick up rx_worker
    even though the source ships."""
    repo = Path(__file__).resolve().parents[1]
    src = (repo / "resources" / "tarball" / "netgen-upgrade").read_text()
    assert "def _rebuild_rx_worker" in src
    assert "_rebuild_rx_worker(install_root" in src


def test_netgen_upgrade_rx_rebuild_targets_canonical_path():
    """Same rationale as tx_worker — /usr/local/bin/rx_worker is
    where utils/dpdk_rx_worker.py looks."""
    repo = Path(__file__).resolve().parents[1]
    src = (repo / "resources" / "tarball" / "netgen-upgrade").read_text()
    import re
    m = re.search(r"def _rebuild_rx_worker[\s\S]+?(?=\ndef [a-z_])", src)
    assert m, "_rebuild_rx_worker function body not found"
    body = m.group(0)
    assert "/usr/local/bin/rx_worker" in body


def test_install_dpdk_sh_has_rx_worker_step():
    """install_dpdk.sh on a fresh box must build rx_worker too —
    not just at wheel-upgrade time. Otherwise fresh installs never
    get DPDK RX until the next upgrade."""
    repo = Path(__file__).resolve().parents[1]
    src = (repo / "resources" / "dpdk" / "install_dpdk.sh").read_text()
    assert "step_build_rx_worker" in src, (
        "install_dpdk.sh missing step_build_rx_worker — fresh "
        "installs won't get DPDK RX"
    )
    # And it must be called in the main run sequence
    assert src.count("step_build_rx_worker") >= 2, (
        "step_build_rx_worker defined but not invoked from main()"
    )
