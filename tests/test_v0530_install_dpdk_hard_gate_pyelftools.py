"""v0.5.30 — hard gate on python3-pyelftools post-apt-install.

Operator-reported on srv06 (Jun 8 2026, post v0.5.29 wheel upgrade):

  Step 5: Building DPDK
  buildtools/meson.build:58:8: ERROR: Problem encountered:
      missing python module: elftools

Same error. THIRD time in a row (v0.5.25 added the package to
apt list; v0.5.29 stopped skipping apt-install on stale check;
yet here it is again).

The new failure mode v0.5.30 closes: even with v0.5.29's always-
run apt-install, the `if/else` block around `eval
"$deps_install_cmd"` falls into a `prompt_yes_no "Continue
anyway?"` path on apt failure. In AUTO_MODE the prompt returns
"y" automatically → script proceeds to Step 5 → meson errors
out 100+ log lines later, by which time the apt install log
has been `rm -f`'d.

v0.5.30 converts the failure mode:

  Before: apt fails → silent continuation → meson dies confusingly
                      → operator sees Step 5 error, no Step 4 trace
                      → /tmp/dpdk_deps_install.log already deleted

  After:  apt fails → `python3 -c "import elftools"` probe runs
                      → fails → exit 1 in Step 4 with explicit
                        error, manual-recovery command, AND
                        last 30 lines of the apt log inlined
                        in the install_dpdk log (which the GUI
                        surfaces as its 'last 30 log lines'
                        view automatically)

Pin: anyone reverting this gate earns a test failure here, not
the next srv06 retry.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALL_DPDK = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_dpdk.sh"
)


def _step_install_deps_body() -> str:
    src = _INSTALL_DPDK.read_text()
    m = re.search(
        r"step_install_dependencies\(\)\s*\{([\s\S]+?)\n\}",
        src,
    )
    assert m
    return m.group(1)


def test_post_apt_elftools_check_exists():
    """After the apt install block, there must be a `python3 -c
    "import elftools"` probe + exit 1 on failure. This is the hard
    gate that turns the silent-continuation failure mode into a
    Step-4-loud failure."""
    body = _step_install_deps_body()
    # Find the `python3 -c "import elftools"` probe.
    probe_match = re.search(
        r"python3\s+-c\s+[\"']import\s+elftools[\"']",
        body,
    )
    assert probe_match, (
        "step_install_dependencies doesn't probe `python3 -c "
        "\"import elftools\"` after apt — the silent continuation "
        "into Step 5 meson failure can recur."
    )
    # The probe MUST be followed by `exit 1` in the failure branch.
    after_probe = body[probe_match.end():]
    # Look for the negated-probe pattern (if ! python3 -c... ; then
    # ... exit 1 ; fi) — match within ~30 lines.
    assert re.search(
        r"exit\s+1",
        after_probe[:2000],
    ), (
        "Elftools probe doesn't `exit 1` on failure — silent "
        "continuation still possible."
    )


def test_post_apt_check_is_negated():
    """The probe must be in a negated condition (`if !
    python3 -c ...; then exit 1`). A non-negated check would exit
    on success, which is exactly backwards."""
    body = _step_install_deps_body()
    assert re.search(
        r"if\s+!\s+python3\s+-c\s+[\"']import\s+elftools[\"']",
        body,
    ), (
        "Elftools probe isn't a negated condition — would exit on "
        "the SUCCESS path, leaving the failure path uncovered."
    )


def test_failure_branch_does_not_rm_apt_log():
    """The original code did `rm -f /tmp/dpdk_deps_install.log` at
    the end of the function unconditionally — destroying the
    apt-failure evidence on every run. The v0.5.30 fix must keep
    the log around when the elftools probe fails so the operator
    has something to diagnose with."""
    body = _step_install_deps_body()
    # Find the elftools probe + exit-1 block.
    m = re.search(
        r"if\s+!\s+python3\s+-c[\s\S]+?exit\s+1\s*\n\s*fi",
        body,
    )
    assert m, "elftools probe + exit block not located"
    block = m.group(0)
    # The probe block must NOT contain `rm -f /tmp/dpdk_deps_install.log`.
    assert "rm -f /tmp/dpdk_deps_install.log" not in block, (
        "Elftools-fail branch deletes /tmp/dpdk_deps_install.log "
        "before exiting — destroys the apt-failure evidence. "
        "Operator has nothing to diagnose with."
    )


def test_failure_branch_inlines_apt_log_tail():
    """The GUI's failure dialog shows the last ~30 lines of the
    install_dpdk log. To make the apt failure visible there, the
    Step 4 hard-gate failure must `tail` the apt install log and
    echo it through the same logger so it ends up in the
    install_dpdk log too."""
    body = _step_install_deps_body()
    m = re.search(
        r"if\s+!\s+python3\s+-c[\s\S]+?exit\s+1\s*\n\s*fi",
        body,
    )
    block = m.group(0)
    # Must reference both `tail` and `/tmp/dpdk_deps_install.log`.
    assert "tail" in block and "dpdk_deps_install.log" in block, (
        "Hard-gate failure branch doesn't inline the apt install "
        "log tail — operator's GUI shows the elftools error but "
        "not the apt error that caused it."
    )


def test_failure_message_includes_manual_recovery_command():
    """The error message must include the literal recovery command
    so an operator who can't / won't ship a new release can
    apt-install pyelftools by hand and retry. Without a paste-able
    command we're forcing operators to think under stress."""
    body = _step_install_deps_body()
    m = re.search(
        r"if\s+!\s+python3\s+-c[\s\S]+?exit\s+1\s*\n\s*fi",
        body,
    )
    block = m.group(0)
    assert "apt-get install" in block and "python3-pyelftools" in block, (
        "Hard-gate failure branch doesn't show the manual recovery "
        "command — operator has to figure it out themselves."
    )


def test_success_path_logs_verification():
    """The success path must log a positive signal so the operator
    log shows `elftools verified` rather than just silently
    advancing to Step 5. Helps confirm the probe actually ran."""
    body = _step_install_deps_body()
    # Must have a log_success call mentioning pyelftools / elftools
    # AFTER the probe.
    probe_match = re.search(
        r"python3\s+-c\s+[\"']import\s+elftools[\"']",
        body,
    )
    after_probe = body[probe_match.end():]
    assert re.search(
        r"log_success.*(pyelftools|elftools)",
        after_probe[:2000],
    ), (
        "Hard-gate success path doesn't log_success on elftools "
        "presence — operator can't confirm the probe even ran."
    )


def test_pyproject_version_at_least_0530():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 30), (
        f"Version {m.group(1)} < 0.5.30"
    )
