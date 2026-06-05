"""Regression tests for v0.3.18 server-side RDMA auto-install.

Operator scenario the feature exists to close:

    1. Server originally fresh-installed pre-v0.3.12 (before
       _install_rdma_userspace existed). perftest never landed.
    2. Operator upgrades to v0.3.17+ via the GUI Upgrade tab
       (wheel-only swap). Python code now knows about perftest
       but the binary is still missing.
    3. Operator opens Tools → RDMA Blast → red "perftest is NOT
       installed" banner. Per standing rule "user will not do such
       manual recovery", they shouldn't have to SSH in.

Fix shape:
    utils.system_deps.ensure_rdma_userspace_installed() — daemon-
    thread-safe self-heal invoked from run_tgen_server.py startup.
    Detects missing perftest, runs apt-get install -y perftest
    rdma-core libibverbs-dev (or distro equivalent), logs result,
    never raises.

These tests pin the 9 design properties so a refactor can't
regress quietly.
"""
from __future__ import annotations

import importlib
import logging
import os
import subprocess
import threading
from unittest.mock import patch, MagicMock

import pytest


def _fresh_module():
    """Re-import utils.system_deps so each test starts with
    _attempted=False. The module-level once-per-uptime guard
    would otherwise leak between tests."""
    import utils.system_deps as sd
    importlib.reload(sd)
    return sd


# ---------------------------------------------------------------------
# Property 1: Async off Flask startup critical path — no work at
# import time. Smoke-tested: the import itself doesn't run subprocess
# or touch files. We assert by importing in isolation.
# ---------------------------------------------------------------------

def test_module_import_does_no_subprocess_work():
    """Importing utils.system_deps must NOT shell out, NOT touch
    /var/log, NOT detect anything. All work is gated behind
    ensure_rdma_userspace_installed()."""
    with patch("subprocess.run") as mock_run, \
         patch("shutil.which") as mock_which:
        _fresh_module()
        assert mock_run.call_count == 0, (
            "subprocess.run called at import time — module must "
            "defer all work until ensure_rdma_userspace_installed() "
            "is called from a daemon thread"
        )
        assert mock_which.call_count == 0, (
            "shutil.which called at import time"
        )


# ---------------------------------------------------------------------
# Property 2: Once-per-uptime guard. Second call after the first
# starts work must short-circuit instantly.
# ---------------------------------------------------------------------

def test_second_call_short_circuits():
    """Calling ensure_rdma_userspace_installed twice must only run
    the apt subprocess once. _attempted guard is the chokepoint."""
    sd = _fresh_module()
    call_count = {"n": 0}

    def fake_which(name):
        if name == "ib_send_bw":
            return None  # force into the install branch
        return "/usr/bin/" + name  # apt-get etc.

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    with patch.object(sd, "_perftest_installed", side_effect=[False, True, True]), \
         patch("shutil.which", side_effect=fake_which), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("os.geteuid", return_value=0):
        sd.ensure_rdma_userspace_installed()
        first_run_count = call_count["n"]
        sd.ensure_rdma_userspace_installed()
        sd.ensure_rdma_userspace_installed()
        # Subsequent calls must NOT have triggered more subprocess.
        assert call_count["n"] == first_run_count, (
            f"second/third call invoked subprocess again "
            f"(count: {call_count['n']} vs first-call: {first_run_count}) "
            "— once-per-uptime guard broken"
        )


# ---------------------------------------------------------------------
# Property 3: Time-bounded — apt subprocess has a timeout.
# ---------------------------------------------------------------------

def test_apt_install_has_timeout():
    """The apt subprocess must be invoked with a timeout kwarg.
    Without it, a stuck apt mirror hangs the daemon thread
    indefinitely. Same applies to apt-get update."""
    sd = _fresh_module()
    seen_kwargs = []

    def fake_run(*args, **kwargs):
        seen_kwargs.append(kwargs)
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    with patch.object(sd, "_perftest_installed", side_effect=[False, True]), \
         patch.object(sd, "_detect_package_manager", return_value="apt"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("os.geteuid", return_value=0):
        sd.ensure_rdma_userspace_installed()

    assert seen_kwargs, "subprocess.run never called"
    for kwargs in seen_kwargs:
        assert "timeout" in kwargs, (
            f"subprocess.run call missing timeout kwarg: {kwargs}"
        )
        assert isinstance(kwargs["timeout"], (int, float)), (
            f"timeout must be numeric: {kwargs['timeout']!r}"
        )
        assert 0 < kwargs["timeout"] <= 300, (
            f"timeout {kwargs['timeout']}s out of sensible range "
            "(want 30-300s)"
        )


# ---------------------------------------------------------------------
# Property 4: Distro-aware — detect_package_manager covers the 5
# supported families.
# ---------------------------------------------------------------------

def test_distro_detection_covers_all_supported():
    """_detect_package_manager must recognize apt, dnf, yum, apk,
    zypper. Each must map to a non-empty package list in
    _PACKAGES."""
    sd = _fresh_module()
    expected = {"apt", "dnf", "yum", "apk", "zypper"}
    assert set(sd._PACKAGES.keys()) == expected, (
        f"_PACKAGES missing one of the supported distros. "
        f"Has: {set(sd._PACKAGES.keys())}, expected: {expected}"
    )
    for distro, pkgs in sd._PACKAGES.items():
        assert pkgs, f"_PACKAGES[{distro!r}] is empty"
        # perftest + rdma-core always present; the libibverbs name
        # varies (-dev vs -devel) but every distro must offer the
        # verbs headers.
        assert "perftest" in pkgs, f"{distro}: perftest missing"
        assert "rdma-core" in pkgs, f"{distro}: rdma-core missing"
        assert any("libibverbs" in p for p in pkgs), (
            f"{distro}: no libibverbs* in package list — verbs "
            f"headers absent will break compilation"
        )


def test_distro_detection_returns_none_on_unsupported():
    """A host with no recognized package manager returns None and
    the auto-install backs off with a WARNING log rather than
    crashing."""
    sd = _fresh_module()
    with patch("shutil.which", return_value=None):
        assert sd._detect_package_manager() is None


# ---------------------------------------------------------------------
# Property 5: Idempotent — perftest already on PATH means skip.
# ---------------------------------------------------------------------

def test_already_installed_short_circuits_without_apt():
    """If ib_send_bw is on PATH, ensure_rdma_userspace_installed
    must NOT invoke any package manager. Pre-installed servers
    should incur zero apt cost on startup."""
    sd = _fresh_module()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    with patch.object(sd, "_perftest_installed", return_value=True), \
         patch("subprocess.run", side_effect=fake_run):
        sd.ensure_rdma_userspace_installed()

    assert call_count["n"] == 0, (
        f"subprocess.run invoked {call_count['n']} times despite "
        "perftest being installed — idempotency broken"
    )


# ---------------------------------------------------------------------
# Property 6: Dedicated log file.
# ---------------------------------------------------------------------

def test_auto_install_log_constant_defined():
    """The AUTO_INSTALL_LOG path constant must be set so operators
    have a known place to tail. /var/log/ is conventional for
    root-running services."""
    sd = _fresh_module()
    assert sd.AUTO_INSTALL_LOG.startswith("/var/log/"), (
        f"AUTO_INSTALL_LOG should live under /var/log/ — got "
        f"{sd.AUTO_INSTALL_LOG!r}"
    )
    assert "netgen" in sd.AUTO_INSTALL_LOG.lower(), (
        "log filename should identify the service (contains 'netgen')"
    )


# ---------------------------------------------------------------------
# Property 7: Kill-switch env var.
# ---------------------------------------------------------------------

def test_kill_switch_env_var_disables_install():
    """NETGEN_AUTO_INSTALL=0 must short-circuit BEFORE any
    detection or subprocess work. Managed systems set this in
    the systemd unit's Environment= to opt out without changing
    the wheel."""
    sd = _fresh_module()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    with patch.dict(os.environ, {sd.KILL_SWITCH_ENV: "0"}), \
         patch.object(sd, "_perftest_installed", return_value=False), \
         patch.object(sd, "_detect_package_manager", return_value="apt"), \
         patch("subprocess.run", side_effect=fake_run):
        sd.ensure_rdma_userspace_installed()

    assert call_count["n"] == 0, (
        f"NETGEN_AUTO_INSTALL=0 honored: subprocess invoked "
        f"{call_count['n']} times — kill switch broken"
    )


@pytest.mark.parametrize("val", ["0", "false", "False", "no", "off", "OFF"])
def test_kill_switch_accepts_common_false_values(val):
    """Operators write off-values inconsistently. Accept all
    common spellings."""
    sd = _fresh_module()
    with patch.dict(os.environ, {sd.KILL_SWITCH_ENV: val}):
        assert sd._is_killed(), (
            f"kill switch value {val!r} not recognized as off"
        )


# ---------------------------------------------------------------------
# Property 8: Never raises — even if everything below blows up,
# ensure_rdma_userspace_installed must return cleanly.
# ---------------------------------------------------------------------

def test_subprocess_exception_does_not_propagate():
    """A daemon thread's exception goes to stderr but isn't caught
    by anything. Public function must absorb subprocess raises."""
    sd = _fresh_module()
    with patch.object(sd, "_perftest_installed", return_value=False), \
         patch.object(sd, "_detect_package_manager", return_value="apt"), \
         patch("subprocess.run", side_effect=OSError("boom")), \
         patch("os.geteuid", return_value=0):
        # Must not raise.
        sd.ensure_rdma_userspace_installed()


def test_unexpected_module_state_does_not_propagate():
    """Even if a helper raises something exotic, the top-level
    function catches and returns."""
    sd = _fresh_module()
    with patch.object(sd, "_perftest_installed",
                      side_effect=RuntimeError("kaboom")):
        # Must not raise.
        sd.ensure_rdma_userspace_installed()


# ---------------------------------------------------------------------
# Property 9: Needs root — non-root run skips with WARNING (not crash).
# ---------------------------------------------------------------------

def test_non_root_run_skips_with_warning(caplog):
    """ostg-server run as non-root (e.g. dev invocation) must
    skip the apt-get install attempt rather than failing with
    permission denied."""
    sd = _fresh_module()
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    with patch.object(sd, "_perftest_installed", return_value=False), \
         patch.object(sd, "_detect_package_manager", return_value="apt"), \
         patch("os.geteuid", return_value=1000), \
         patch("subprocess.run", side_effect=fake_run), \
         caplog.at_level(logging.WARNING):
        sd.ensure_rdma_userspace_installed()

    assert call_count["n"] == 0, (
        "non-root run still attempted apt-get install"
    )
    assert any("not root" in r.message.lower() or "uid=1000" in r.message
               for r in caplog.records), (
        f"non-root case should log a WARNING explaining the skip; "
        f"got: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------
# Bonus: thread-safety — concurrent callers race on the lock but
# only one actually invokes apt.
# ---------------------------------------------------------------------

def test_concurrent_callers_only_one_runs_subprocess():
    """Two threads calling ensure_rdma_userspace_installed
    simultaneously must result in exactly ONE subprocess invocation
    (the lock + _attempted flag combo). Catches a refactor that
    moves the guard check outside the lock."""
    sd = _fresh_module()
    call_count = {"n": 0}
    started = threading.Barrier(2)

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="",
        )

    def worker():
        started.wait(timeout=2)
        sd.ensure_rdma_userspace_installed()

    with patch.object(sd, "_perftest_installed", side_effect=[False, True, True]), \
         patch.object(sd, "_detect_package_manager", return_value="apt"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("os.geteuid", return_value=0):
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

    assert call_count["n"] <= 2, (
        f"concurrent callers invoked subprocess {call_count['n']} times "
        "— expected at most apt-get update + apt-get install once"
    )


# ---------------------------------------------------------------------
# Bonus: wire-in verification — run_tgen_server.py main() must
# spawn the autoinstall thread.
# ---------------------------------------------------------------------

def test_run_tgen_server_wires_in_autoinstall():
    """The fix is useless if it lives in utils/ but main() never
    calls it. Pin the hook in main() so a refactor removing the
    thread is caught."""
    import re
    src = open("/Users/surajsharma/dev/netgen/run_tgen_server.py").read()
    # Find main()
    m = re.search(
        r"^def main\(.*?(?=\n(?:def |if __name__|class ))",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m, "main() not found in run_tgen_server.py"
    main_body = m.group(0)
    # ensure_rdma_userspace_installed must be referenced inside
    # main() — caller wraps it in a daemon thread.
    assert "ensure_rdma_userspace_installed" in main_body, (
        "main() in run_tgen_server.py doesn't reference "
        "ensure_rdma_userspace_installed — the auto-install "
        "module is dead code without this hook"
    )
    # Must be spawned in a daemon thread (not blocking startup).
    # The typical pattern wraps ensure_rdma_userspace_installed()
    # in a closure passed as Thread(target=...):
    #     def _wrapper():
    #         from utils.system_deps import ensure_rdma_userspace_installed
    #         ensure_rdma_userspace_installed()
    #     Thread(target=_wrapper, daemon=True).start()
    # So Thread( + daemon=True appear AFTER the ensure reference,
    # not before. Use the LAST occurrence (the actual call site,
    # not docstring mentions) and look forward.
    ensure_pos = main_body.rfind("ensure_rdma_userspace_installed")
    assert ensure_pos >= 0, "checked above"
    lo = max(0, ensure_pos - 300)
    hi = min(len(main_body), ensure_pos + 1500)
    window = main_body[lo:hi]
    assert "daemon=True" in window, (
        "ensure_rdma_userspace_installed must be spawned in a "
        "daemon thread (daemon=True) to avoid blocking Flask "
        "startup — see the v0.2.18 startup-hang lesson. "
        f"Looked in {hi-lo} chars around the ensure call; "
        f"no daemon=True found nearby. Window excerpt: "
        f"...{window[-300:]!r}"
    )
    assert "Thread(" in window, (
        "no Thread(...) call near ensure_rdma_userspace_installed — "
        "must be invoked via threading.Thread, not synchronously"
    )
