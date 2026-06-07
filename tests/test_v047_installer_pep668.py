"""Regression test for v0.4.7: fresh install must work on Ubuntu
24.04+ (PEP 668 / EXTERNALLY-MANAGED).

Operator-reported on a fresh Noble VM:

  [INFO] Using pre-built wheel: /tmp/netgen_install/...whl
  [ERROR] Wheel install failed: ... error: externally-managed-environment

  × This environment is externally managed
  ╰─> To install Python packages system-wide, try apt install
      python3-xyz...

  hint: See PEP 668 for the detailed specification.
  [client] installer exit rc=1

Ubuntu 24.04+ (and Debian 12+) ship the EXTERNALLY-MANAGED marker
file at /usr/lib/python3.*/EXTERNALLY-MANAGED, which makes the
system pip refuse `pip3 install` with the error above. Netgen IS
a system-wide install (systemd unit running /usr/bin/python3), so
the correct fix is `--break-system-packages`, not a venv.

The flag was added in pip 23.0 (Jan 2023). Older systems don't
recognize it AND don't enforce PEP 668 — blindly passing it would
break those. So we detect the EXTERNALLY-MANAGED marker on the
target, pass the flag only when enforced.

This test pins:
  1. The detection helper exists and uses the correct marker path.
  2. Every `pip3 install` call site in the installer threads the
     detected flag through.
  3. The wheel-install failure path retries with the flag if
     detection missed but the error message surfaces PEP 668.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALLER = (
    Path(__file__).resolve().parents[1] / "install_ostg_complete.py"
)


def test_pep668_detect_helper_exists():
    """The detection helper must exist with the correct name and
    check for the EXTERNALLY-MANAGED marker file at the canonical
    path. Renames or refactors that drop the helper would re-break
    the Ubuntu 24.04 install path."""
    src = _INSTALLER.read_text()
    assert re.search(
        r"def _detect_pep668_break_flag\(self\)\s*->\s*str:",
        src,
    ), (
        "_detect_pep668_break_flag method missing — without it the "
        "Ubuntu 24.04 install path can't escape PEP 668."
    )
    # The detection must check the EXTERNALLY-MANAGED marker. Any
    # other detection (e.g. parsing os-release) would surface
    # false positives on systems that ship the marker only on
    # certain Python versions.
    assert "EXTERNALLY-MANAGED" in src, (
        "Detection helper doesn't reference the EXTERNALLY-MANAGED "
        "marker — PEP 668 detection becomes unreliable."
    )
    # And the path glob must include /usr/lib/python3*/ so it
    # catches the system Python regardless of minor version.
    assert "/usr/lib/python3" in src and "EXTERNALLY-MANAGED" in src
    # Result must be cached — the helper is called from multiple
    # install steps and we don't want N round-trips to ssh for the
    # same answer.
    assert "_pep668_break_flag_cached" in src, (
        "Detection isn't cached. Multiple pip3 install steps would "
        "each round-trip an `ls` to the remote — slow on high-RTT "
        "links."
    )


def test_wheel_install_threads_the_pep668_flag():
    """The first wheel install (line ~1235 pre-v0.4.7) must pass
    the detected flag. If a refactor strips it, fresh install on
    Noble reverts to the operator-reported failure."""
    src = _INSTALLER.read_text()
    # The pre-v0.4.7 line was:
    #   f"pip3 install --force-reinstall --no-deps {remote_wheel_path}"
    # Post-v0.4.7 it must thread {pep668} (or equivalent) before
    # the wheel path. Pin the structure.
    assert re.search(
        r"pip3 install \{pep668\}--force-reinstall --no-deps \{remote_wheel_path\}",
        src,
    ), (
        "First wheel install no longer threads {pep668} — "
        "Ubuntu 24.04 install will fail with externally-managed-"
        "environment again."
    )


def test_deps_pass_threads_the_pep668_flag():
    """The deps pass must thread {pep668} too — without it, even if
    the wheel install succeeds via fallback retry, this pass blocks
    the install with the same PEP 668 error.

    v0.4.8 note: pre-v0.4.8 the deps pass used `--upgrade-strategy
    only-if-needed` which could silently no-op when the wheel was
    already current (leaving Flask uninstalled — operator-reported
    on san-hp-srv06). The deps pass now uses `--force-reinstall`.
    This test updated to match — the {pep668} thread requirement is
    unchanged."""
    src = _INSTALLER.read_text()
    # Find the deps_result run_command call and verify it includes
    # {pep668} in the same composed string.
    m = re.search(
        r"deps_result\s*=\s*self\.run_command\(\s*\n\s*"
        r"f?\"pip3 install \{pep668\}--force-reinstall \{remote_wheel_path\}\"",
        src,
    )
    assert m, (
        "Deps-pass pip3 install missing {pep668} thread — fresh "
        "install will fail at the dependency-resolution step on "
        "Ubuntu 24.04+."
    )


def test_ai_deps_pass_threads_the_pep668_flag():
    """install_ai_dependencies loops over a packages list with
    plain `pip3 install {package}`. Pre-fix this would fail per
    package on Noble; post-fix it threads the flag."""
    src = _INSTALLER.read_text()
    assert re.search(
        r"pip3 install \{pep668\}\{package\}",
        src,
    ), (
        "AI-deps loop missing {pep668} thread — AI features would "
        "silently fail to install on Ubuntu 24.04+ even after the "
        "wheel install succeeds."
    )


def test_externally_managed_retry_exists():
    """Belt-and-suspenders: even if detection misses (unconventional
    Python install path), the error message itself surfaces 'externally-
    managed'. Pin the retry-with-flag fallback so a future detection
    bug doesn't silently revert the install to broken-on-Noble."""
    src = _INSTALLER.read_text()
    # Find the install_ostg body (function name predates the rebrand
    # — internal install code still uses install_ostg).
    m = re.search(
        r"def install_ostg\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "install_ostg body not found"
    body = m.group(0)
    assert re.search(
        r"if\s+\"externally-managed\"\s+in\s+err\.lower\(\)",
        body,
    ), (
        "install_ostg has no fallback retry for the externally-"
        "managed error. Detection failure would surface as a fresh-"
        "install break on Noble — and the operator already filed "
        "this bug once. Pin the safety net."
    )
    # The retry must use --break-system-packages explicitly
    assert "--break-system-packages" in body, (
        "Fallback retry doesn't include --break-system-packages. "
        "The retry exists in name only — it'd hit the same PEP 668 "
        "wall."
    )


def test_pep668_detected_string_includes_trailing_space():
    """Sneaky-but-important: the helper returns the flag WITH a
    trailing space so the surrounding f-strings compose cleanly:

        f"pip3 install {pep668}--force-reinstall ..."

    When PEP 668 is enforced: 'pip3 install --break-system-packages --force-reinstall ...'
    When not enforced:        'pip3 install --force-reinstall ...'

    A missing trailing space produces malformed argv like
    '--break-system-packages--force-reinstall' which pip rejects.
    Pin the contract."""
    src = _INSTALLER.read_text()
    # Find the return statement that builds the flag string
    m = re.search(
        r'_pep668_break_flag_cached\s*=\s*\(\s*\n?\s*'
        r'"--break-system-packages\s+"\s+if\s+enforced',
        src,
    )
    assert m, (
        "The break-system-packages flag string in the detection "
        "helper doesn't end with a space. The composed f-string "
        "would produce '--break-system-packages--force-reinstall' "
        "(no space), which pip rejects as an unknown option."
    )
