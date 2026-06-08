"""v0.5.29 — step_install_dependencies must always run apt install.

Operator-reported on srv06 (Jun 8 2026) after wheel-upgrade to v0.5.28:

  Step 5: Building DPDK
  buildtools/meson.build:58:8: ERROR: Problem encountered:
      missing python module: elftools
  [x] DPDK meson setup failed

But v0.5.25 added python3-pyelftools to install_dpdk.sh's apt
batch back when this error first surfaced. Why did it recur?

Root cause: step_install_dependencies() called
check_dpdk_dependencies() and returned early when the check
returned 0 (no missing deps). The check function ONLY verified
7 packages (meson, ninja, pkg-config, gcc, libnuma-dev, libelf-dev,
libpcap-dev). It did NOT check python3-pyelftools, libssl-dev,
libjansson-dev, libbpf-dev, libxdp-dev, libbsd-dev, zlib1g-dev,
libfdt-dev, libarchive-dev.

So an operator who'd successfully run Make DPDK Ready in v0.5.13
or earlier (when only the 7 packages mattered) would have the
check PASS on a re-run under v0.5.28 → apt-install SKIPPED → the
v0.5.25/v0.5.26 additions never installed → Step 5 errors out
because python3-pyelftools is absent.

The fix: drop the early-return. apt-get install is idempotent for
already-installed packages (cost: ~5-10s of "X is already the
newest version" lines). Operator-trust invariant: "Make DPDK Ready
installs every dep the script knows about, every time".

check_dpdk_dependencies stays — its diagnostic logging is useful —
and is augmented to ALSO log python3-pyelftools status. But it no
longer gates the apt install.

Pin: anyone re-adding the conditional skip earns a test failure
here, not the next srv06 install on a stale host.
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
    assert m, "step_install_dependencies function body not found"
    return m.group(1)


def test_step_4_no_longer_early_returns_on_check_pass():
    """The early-return pattern was:
        if check_dpdk_dependencies; then
            log_success "All DPDK dependencies are already installed"
            return 0
        fi
    That pattern MUST be gone — it caused the v0.5.28 srv06 elftools
    recurrence. Any `return 0` immediately after a successful check
    fails the test."""
    body = _step_install_deps_body()
    # Pattern: `if check_dpdk_dependencies; then` followed within a
    # few lines by `return 0` BEFORE the apt invocation. The new
    # shape calls check with `|| true` (diagnostic only) and never
    # returns based on its result.
    bad = re.search(
        r"if\s+check_dpdk_dependencies;\s*then[\s\S]{0,200}?return\s+0",
        body,
    )
    assert not bad, (
        "step_install_dependencies still has the early-return on "
        "check_dpdk_dependencies success — that's the v0.5.29 fix's "
        "whole point to remove. Pre-fix this caused srv06's v0.5.28 "
        "meson elftools error because python3-pyelftools never got "
        "installed when the check passed for the older 7-package set."
    )


def test_step_4_calls_check_as_diagnostic_only():
    """check_dpdk_dependencies must still be called for its
    informative logging (which packages are found / missing), but
    it must be called with `|| true` or similar to make clear it's
    diagnostic, not gating."""
    body = _step_install_deps_body()
    # The new pattern uses `check_dpdk_dependencies || true` so a
    # nonzero return doesn't propagate via `set -e`.
    assert re.search(
        r"check_dpdk_dependencies\s*\|\|\s*true",
        body,
    ), (
        "check_dpdk_dependencies isn't called with `|| true` — "
        "either it's gone (lost diagnostic value) or its return "
        "code still leaks (set -e would abort step 4)."
    )


def test_step_4_runs_apt_in_auto_mode_without_prompt():
    """When AUTO_MODE=1 (the GUI / endpoint-spawned case), step 4
    must run apt install without any prompt — there's no TTY for
    prompt_yes_no to read. Pre-v0.5.29 the prompt was unconditional
    but bypassed via early-return when check passed; now the early-
    return is gone, the prompt must be gated on AUTO_MODE != 1."""
    body = _step_install_deps_body()
    # The prompt_yes_no call (if any) MUST be inside an AUTO_MODE
    # check that skips it. Match either:
    #   if [[ "$AUTO_MODE" != "1" ]]; then
    #       ... prompt_yes_no ...
    #   fi
    assert re.search(
        r'AUTO_MODE.*!=\s*"1"[\s\S]*?prompt_yes_no',
        body,
    ) or "prompt_yes_no" not in body, (
        "step_install_dependencies still calls prompt_yes_no "
        "unconditionally — would hang the install_dpdk endpoint "
        "(no TTY for prompts)."
    )


def test_step_4_invokes_apt_install_unconditionally():
    """The apt install must run regardless of check result. Match
    the eval of $deps_install_cmd (the actual apt invocation)
    coming after the (now-diagnostic) check."""
    body = _step_install_deps_body()
    # The body must reference deps_install_cmd OR umask + eval which
    # is the apt-running line. Pre-v0.5.29 those happened ONLY in
    # the "missing deps" branch; now they must happen always.
    assert "deps_install_cmd" in body, (
        "step_install_dependencies body doesn't reference "
        "deps_install_cmd — the apt invocation is gone or moved."
    )


# ────────────────── check_dpdk_dependencies pyelftools probe ─────────


def test_check_function_probes_pyelftools():
    """check_dpdk_dependencies must include a `python3 -c "import
    elftools"` probe so its diagnostic log surfaces whether the
    Python module DPDK 23.11 needs is actually present. Pre-fix the
    check ignored Python modules entirely."""
    src = _INSTALL_DPDK.read_text()
    m = re.search(
        r"check_dpdk_dependencies\(\)\s*\{([\s\S]+?)\n\}",
        src,
    )
    assert m, "check_dpdk_dependencies function not found"
    body = m.group(1)
    assert re.search(
        r"python3\s+-c\s+[\"']import\s+elftools[\"']",
        body,
    ), (
        "check_dpdk_dependencies doesn't probe the elftools Python "
        "module — diagnostic log would silently miss the most "
        "common DPDK 23.11 build failure cause."
    )


def test_check_function_probes_pyelftools_as_module_not_pkg():
    """Important: probe the MODULE (`python3 -c "import elftools"`),
    NOT the apt package (`dpkg -l python3-pyelftools`). The package
    name varies across distros (python3-pyelftools on Ubuntu/Debian,
    python3-elftools on some, python-pyelftools-* on RPM-based);
    the importable Python module name is always `elftools`.
    Catching the module by its `import` name is portable across
    distros AND across pip-vs-apt install methods."""
    src = _INSTALL_DPDK.read_text()
    m = re.search(
        r"check_dpdk_dependencies\(\)\s*\{([\s\S]+?)\n\}",
        src,
    )
    body = m.group(1)
    # The probe must use `python3 -c "import elftools"` — not dpkg.
    # dpkg-checking by package name would miss pip-installed
    # pyelftools and miss non-Debian distros. Look for the
    # adjacency: `dpkg ... python3-pyelftools` on the same line.
    bad = re.search(
        r"dpkg[^\n]*python3-pyelftools",
        body,
    )
    assert not bad, (
        "check_dpdk_dependencies dpkg-checks for python3-pyelftools "
        "— use `python3 -c \"import elftools\"` instead (portable "
        "across distros + install methods)."
    )


def test_pyproject_version_at_least_0529():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 29), (
        f"Version {m.group(1)} < 0.5.29"
    )
