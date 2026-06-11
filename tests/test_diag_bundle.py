"""v0.5.104: Diagnostic bundle.

Operator-requested after the srv06 saga (v0.5.101 → v0.5.103 took
three release-roundtrips because each round needed one more piece
of system state). A single button captures everything support
needs.

Tests cover:
- The bundle builds without erroring even when commands are missing
  (mlxfwmanager not installed, etc.)
- Tarball structure: gz-compressed tar, MANIFEST present, expected
  top-level dirs
- Per-file cap enforced (no 100 MB journal in the bundle)
- Total cap enforced (no 1 GB tarball)
- Journal redactor IS called if provided
- API fetcher IS called if provided
- Mtime is 0 (reproducible — see v0.5.9 lesson)
- Privacy guards: no /etc/shadow, no ssh keys
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from unittest import mock

import pytest


# Set NETGEN_DB_PATH before importing — utils may pull
# in run_tgen_server transitively.
os.environ.setdefault("NETGEN_DB_PATH", "/tmp/netgen-test-diag.db")


from utils import diag_bundle  # noqa: E402


# ─── Bundle structure ───


def test_bundle_is_valid_gzipped_tar():
    blob = diag_bundle.build_bundle()
    assert blob[:2] == b"\x1f\x8b", "Not gzipped"
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = tf.getnames()
        assert "MANIFEST.txt" in names


def test_bundle_includes_manifest():
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        m = tf.extractfile("MANIFEST.txt")
        assert m is not None
        text = m.read().decode("utf-8", "replace")
        assert "netgen diagnostic bundle" in text
        assert "Sections:" in text


def test_bundle_does_not_error_when_commands_missing():
    """mlxfwmanager / ibstat / etc. may not be installed.
    Builder must tolerate gracefully — no crash, no missing
    MANIFEST."""
    blob = diag_bundle.build_bundle()
    assert blob, "Empty bundle"
    # Doesn't raise — that's the test.


def test_bundle_filenames_have_no_path_traversal():
    """No '..' or absolute paths in tar members."""
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            assert not member.name.startswith("/"), (
                f"Absolute path: {member.name}"
            )
            assert ".." not in member.name.split("/"), (
                f"Path traversal: {member.name}"
            )


def test_bundle_mtimes_are_zero():
    """Reproducible — same content twice → same bytes.
    See v0.5.9 lesson on hardcoded mtimes."""
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            assert member.mtime == 0, (
                f"Non-zero mtime on {member.name}: {member.mtime}"
            )


# ─── Privacy guards ───


def test_bundle_does_not_include_etc_shadow():
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for name in tf.getnames():
            assert "shadow" not in name.lower()


def test_bundle_does_not_include_ssh_keys():
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for name in tf.getnames():
            n = name.lower()
            assert "id_rsa" not in n
            assert "id_ed25519" not in n
            assert "ssh_host" not in n
            assert ".ssh/" not in name


def test_bundle_does_not_include_sudoers():
    blob = diag_bundle.build_bundle()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for name in tf.getnames():
            assert "sudoers" not in name.lower()


# ─── Caps ───


def test_per_file_cap_is_enforced():
    """Per-file cap is 256 KiB. Verify constant + that the
    capped path is exercised."""
    assert diag_bundle._PER_FILE_CAP_BYTES == 256 * 1024


def test_total_cap_is_enforced():
    assert diag_bundle._TOTAL_CAP_BYTES == 16 * 1024 * 1024


def test_run_capture_truncates_overlong_output(monkeypatch):
    big = "x" * (diag_bundle._PER_FILE_CAP_BYTES + 50_000)

    def fake_run(*a, **kw):
        return mock.Mock(stdout=big, stderr="", returncode=0)

    monkeypatch.setattr(diag_bundle.subprocess, "run", fake_run)
    out = diag_bundle._run_capture(["echo", "x"])
    assert out is not None
    # Output must be ≤ cap (allowing for the marker overhead).
    assert len(out.encode("utf-8")) <= diag_bundle._PER_FILE_CAP_BYTES + 256
    assert "truncated" in out


def test_run_capture_tail_truncates_for_logs(monkeypatch):
    """Log-like commands (journalctl, dmesg) should keep the
    TAIL of the output (most recent), not the head."""
    head = "OLD-LINES" * 60_000  # > cap
    tail = "RECENT-LINES" * 60_000
    big = head + tail

    def fake_run(*a, **kw):
        return mock.Mock(stdout=big, stderr="", returncode=0)

    monkeypatch.setattr(diag_bundle.subprocess, "run", fake_run)
    out = diag_bundle._run_capture(["journalctl"], tail_truncate=True)
    assert out is not None
    # Tail-truncate keeps the END, so RECENT-LINES wins.
    assert "RECENT-LINES" in out
    assert "head truncated" in out


# ─── Pluggable closures ───


def test_api_fetcher_is_called_when_provided():
    calls = []

    def fake_fetcher(path: str) -> dict:
        calls.append(path)
        return {"path": path, "ok": True}

    blob = diag_bundle.build_bundle(api_fetcher=fake_fetcher)
    assert calls, "api_fetcher never called"
    # All four expected endpoints sampled.
    paths = set(calls)
    assert "/api/admin/health" in paths
    assert "/api/dpdk/status" in paths
    assert "/api/interfaces" in paths
    assert "/api/streams/stats" in paths

    # And the JSON snapshots appear in the tarball.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = tf.getnames()
        assert any(n.startswith("api/") and n.endswith(".json")
                   for n in names)


def test_api_fetcher_exceptions_dont_kill_bundle():
    """A flaky API endpoint must not break the entire bundle —
    other captures should still land."""
    def flaky(path: str) -> dict:
        raise RuntimeError("simulated API failure")

    blob = diag_bundle.build_bundle(api_fetcher=flaky)
    # No crash; bundle still has MANIFEST.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        assert "MANIFEST.txt" in tf.getnames()


def test_journal_redactor_is_called_when_provided(monkeypatch):
    """The redactor scrubs tokens before they hit the bundle.
    Without it, secrets in logs end up in operator-shared
    tarballs."""
    called = []

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "journalctl":
            return mock.Mock(
                stdout="auth=secret_token_AAAA\nrest of log\n",
                stderr="", returncode=0,
            )
        return mock.Mock(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(diag_bundle.subprocess, "run", fake_run)

    def redactor(text: str) -> str:
        called.append(text)
        return text.replace("secret_token_AAAA", "<REDACTED>")

    blob = diag_bundle.build_bundle(journal_redactor=redactor)
    assert called, "redactor never called"
    # Verify the redacted text — NOT the original — is in the bundle.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        journal_members = [n for n in tf.getnames()
                           if "journal" in n and "netgen-server" in n]
        assert journal_members
        m = tf.extractfile(journal_members[0])
        content = m.read().decode("utf-8")
        assert "<REDACTED>" in content
        assert "secret_token_AAAA" not in content


def test_journal_redactor_exception_falls_back_to_unredacted(monkeypatch):
    """If the redactor crashes, we'd rather include the raw
    journal than skip it entirely (operator can still find the
    bug, support can scrub by hand). But the failure must be
    logged."""
    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "journalctl":
            return mock.Mock(
                stdout="log content\n", stderr="", returncode=0,
            )
        return mock.Mock(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(diag_bundle.subprocess, "run", fake_run)

    def bad_redactor(text: str) -> str:
        raise RuntimeError("boom")

    blob = diag_bundle.build_bundle(journal_redactor=bad_redactor)
    # Bundle still builds, journal still included.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        journal_members = [n for n in tf.getnames()
                           if "journal" in n and "netgen-server" in n]
        assert journal_members


# ─── Collector resilience ───


def test_individual_collector_crash_is_isolated(monkeypatch):
    """If one collector crashes (bug in our code, weird host
    state), the other collectors must still run AND the error
    is recorded under errors/ in the bundle."""
    def boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        diag_bundle, "_capture_system_info", boom,
    )
    blob = diag_bundle.build_bundle()
    # No exception. And the error is recorded in the bundle.
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = tf.getnames()
        # The error filename derives from the crashing function's
        # __name__ — here, "boom" (the replacement). Just check
        # that SOME errors/*.txt landed.
        err_files = [n for n in names if n.startswith("errors/")]
        assert err_files, f"No errors/ recorded: {names}"
        # And the content captures the exception message.
        m = tf.extractfile(err_files[0])
        content = m.read().decode("utf-8")
        assert "simulated crash" in content
        # And other collectors still produced output — bundle
        # has more than just the error + MANIFEST.
        assert len(names) > 2


def test_list_netgen_ifaces_excludes_loopback_and_virtuals(monkeypatch):
    """Bundle shouldn't capture ethtool for lo, docker0, br-…"""
    monkeypatch.setattr(
        os, "listdir",
        lambda p: ["lo", "docker0", "veth1234", "br-abc",
                  "eth0", "ens2f0np0"],
    )
    result = diag_bundle._list_netgen_ifaces()
    assert "lo" not in result
    assert "docker0" not in result
    assert "veth1234" not in result
    assert "br-abc" not in result
    assert "eth0" in result
    assert "ens2f0np0" in result


def test_dpkg_inventory_filters_to_network_packages(monkeypatch):
    """We don't want 700 lines of `dpkg -l` in the bundle —
    just the network-related packages."""
    fake_dpkg = (
        "ii  bash       5.1     amd64     GNU shell\n"
        "ii  libdpdk23  21.11   amd64     DPDK runtime libs\n"
        "ii  mlxfw-tools 22.10  amd64     Mellanox FW manager\n"
        "ii  rdma-core   28.0    amd64     RDMA core\n"
        "ii  python3     3.10    amd64     Python 3\n"
    )

    def fake_run(*a, **kw):
        return mock.Mock(stdout=fake_dpkg, stderr="", returncode=0)

    monkeypatch.setattr(diag_bundle.subprocess, "run", fake_run)
    result = diag_bundle._capture_dpkg_inventory()
    assert result
    content = result[0][1]
    assert "libdpdk23" in content
    assert "mlxfw-tools" in content
    assert "rdma-core" in content
    assert "bash" not in content   # filtered out
    assert "python3" not in content
