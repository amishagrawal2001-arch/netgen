"""Regression tests for the v0.3.16 dpkg-conffile-prompt install failure.

Operator hit this on Ubuntu 24.04 (Noble) fresh install:

    Setting up containerd.io (2.2.4-1~ubuntu.24.04~noble) ...
    Configuration file '/etc/containerd/config.toml'
      ==> File on system created by you or by a script.
      ==> File also in package provided by package maintainer.
        What would you like to do about it?
    *** config.toml (Y/I/N/O/D/Z) [default=N] ?
    dpkg: error processing package containerd.io (--configure):
      end of file on stdin at conffile prompt
    dpkg: dependency problems prevent configuration of docker-ce:
      docker-ce depends on containerd.io (>= 1.7.27); however:
        Package containerd.io is not configured yet.

Root cause: ``apt-get install -y`` only auto-answers apt's own
prompts. It does NOT control dpkg's conffile prompt, which fires
whenever a maintainer's default config differs from a file
present on the system. The ``nohup``-detached install has no
stdin → dpkg sees EOF → install fails. DEBIAN_FRONTEND=non-
interactive (already exported by run_command) doesn't help —
dpkg's conffile prompt is independent of debconf.

The fix passes the dpkg-specific flags:
    -o Dpkg::Options::=--force-confdef     auto-resolve if no local edit
    -o Dpkg::Options::=--force-confold     preserve local edits

A new ``_apt_install`` wrapper applies these to every install in
the Python installer; ``install_dpdk.sh`` got the same treatment
inline.

These tests pin the COMMAND STRINGS so anyone reverting to bare
``apt-get install -y`` is caught immediately."""
from __future__ import annotations

import re


_INSTALLER_PATH = "/Users/surajsharma/dev/netgen/install_ostg_complete.py"
_DPDK_SH_PATH = "/Users/surajsharma/dev/netgen/resources/dpdk/install_dpdk.sh"


def _installer_src():
    return open(_INSTALLER_PATH).read()


def test_apt_install_helper_exists():
    """The fix introduced ``_apt_install`` as the single chokepoint for
    every apt-get install in the installer. If it disappears, callers
    will silently regress to the broken behavior."""
    src = _installer_src()
    assert re.search(
        r"def _apt_install\(self,\s+packages",
        src,
    ), "_apt_install helper missing — all apt callers will use bare flags"


def test_apt_install_helper_passes_dpkg_flags():
    """The helper must add both ``--force-confdef`` and
    ``--force-confold``. Either alone leaves a class of conffile
    prompt unhandled.

    The flags live on a class constant ``_APT_DPKG_FLAGS`` that the
    helper concatenates into the command. Check the CONSTANT
    (regardless of whether the helper references it directly or
    inlines the flag strings)."""
    src = _installer_src()
    # Find the class constant assignment
    m = re.search(
        r"_APT_DPKG_FLAGS\s*=\s*\(\s*(.+?)\s*\)",
        src,
        re.DOTALL,
    )
    assert m, "_APT_DPKG_FLAGS class constant not found"
    flags_value = m.group(1)
    assert "--force-confdef" in flags_value, \
        "--force-confdef missing from _APT_DPKG_FLAGS — " \
        "package-default conffile diffs with no local edits will prompt"
    assert "--force-confold" in flags_value, \
        "--force-confold missing from _APT_DPKG_FLAGS — " \
        "locally-edited conffiles will prompt"
    # Also confirm the helper references the constant (or inlines
    # the flags). One or the other must be true.
    m2 = re.search(
        r"def _apt_install\(self,.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    body = m2.group(0)
    references_const = "_APT_DPKG_FLAGS" in body
    inlines_flags = (
        "--force-confdef" in body and "--force-confold" in body
    )
    assert references_const or inlines_flags, (
        "_apt_install body must reference _APT_DPKG_FLAGS or inline "
        "both dpkg --force flags directly"
    )


def test_no_bare_apt_get_install_in_installer():
    """Every apt-get install in the installer must go through the
    ``_apt_install`` helper. Any raw ``apt-get install -y`` outside
    the helper definition itself is a regression that will re-open
    the conffile-prompt failure mode."""
    src = _installer_src()
    # Allow docstring references + the actual usage inside the helper.
    # Find every line that mentions "apt-get install -y" but is NOT:
    #   - inside _apt_install's own body
    #   - inside a docstring (triple-quoted string)
    #   - inside a comment
    bare_callers = []
    in_apt_install_body = False
    in_helper_indent = None
    apt_install_def = re.search(
        r"^(\s*)def _apt_install\(", src, re.MULTILINE,
    )
    if apt_install_def:
        in_helper_indent = len(apt_install_def.group(1))

    for line_no, line in enumerate(src.split("\n"), 1):
        # Track entry/exit of _apt_install body by indentation
        if line.lstrip().startswith("def _apt_install("):
            in_apt_install_body = True
            continue
        if in_apt_install_body:
            stripped = line.strip()
            if stripped == "":
                continue
            indent = len(line) - len(line.lstrip())
            # Exit when we hit a sibling def/class at the helper's indent.
            if indent <= (in_helper_indent or 0) and stripped.startswith(("def ", "class ")):
                in_apt_install_body = False
        if in_apt_install_body:
            continue

        # Skip comments + docstring lines (rough heuristic — anything
        # in triple-quotes is hard to parse line-by-line; accept that
        # the check might miss a docstring with the literal string,
        # which would be a false positive at worst).
        s = line.lstrip()
        if s.startswith("#"):
            continue

        # The actual regression we're guarding against:
        #   self.run_command("apt-get install -y ..."  ← BAD
        if re.search(r'run_command\(\s*[fr]?["\']apt-get install -y',
                     line):
            bare_callers.append((line_no, line.rstrip()))

    assert not bare_callers, (
        "Bare `run_command('apt-get install -y ...')` callers found — "
        "these will hit the dpkg-conffile-prompt failure on Ubuntu "
        f"24.04 + similar:\n  " +
        "\n  ".join(f"L{n}: {l}" for n, l in bare_callers)
    )


def test_install_dpdk_sh_has_dpkg_force_flags():
    """install_dpdk.sh's bash-side apt-get install must also include
    the dpkg --force-confdef/--force-confold flags. The Python helper
    can't reach into a shell script, so the script gets its own
    inline treatment."""
    src = open(_DPDK_SH_PATH).read()
    # The line should also have DEBIAN_FRONTEND=noninteractive as
    # the bash script's env doesn't inherit the Python installer's.
    assert "DEBIAN_FRONTEND=noninteractive" in src, \
        "install_dpdk.sh missing DEBIAN_FRONTEND=noninteractive"
    assert "Dpkg::Options::=--force-confdef" in src, \
        "install_dpdk.sh missing --force-confdef"
    assert "Dpkg::Options::=--force-confold" in src, \
        "install_dpdk.sh missing --force-confold"


def test_install_dpdk_sh_no_bare_apt_get_install_y():
    """Same as the Python check but for the bash script: no bare
    ``apt-get install -y`` without the dpkg force flags.

    v0.5.30 update: an `apt-get install -y` inside a quoted string
    that's the argument to log_error / log_warning / log_info /
    log_success / echo / printf isn't an EXECUTED command — it's
    recovery text shown to the operator. v0.5.30 added a literal
    `apt-get install -y python3-pyelftools` to its hard-gate error
    message so operators can paste it; that shouldn't trip this test.
    """
    src = open(_DPDK_SH_PATH).read()
    for line in src.split("\n"):
        if "apt-get install -y" not in line:
            continue
        before = line.split("apt-get install -y")[0]
        if "#" in before:
            continue
        # Skip recovery-instruction text inside log/echo arguments.
        stripped = line.lstrip()
        if re.match(
            r'^(log_(error|warning|info|success|step)|echo|printf)\b',
            stripped,
        ):
            continue
        assert "Dpkg::Options::=" in line, (
            f"install_dpdk.sh has apt-get install -y without "
            f"Dpkg::Options flags: {line.strip()}"
        )


def test_specific_callsites_converted_to_helper():
    """Pin the 7 originally-broken callsites by their package list.

    Find every ``_apt_install(...)`` call in the source and check that
    each expected package list appears in at least one of them. This
    is more robust than the prior proximity heuristic — the package
    name `software-properties-common` also appears in a list literal
    elsewhere (the broad apt baseline at line ~530), which fooled the
    `find first occurrence + look backward` approach into examining
    the wrong location.
    """
    src = _installer_src()
    # Capture every _apt_install(...) call, including line continuations.
    # Multi-line capture: from `_apt_install(` to the matching `)`.
    calls = re.findall(
        r"_apt_install\s*\(\s*(?:[fr]?\"[^\"]*\"|'[^']*'|[^()])+\s*\)",
        src,
        re.DOTALL,
    )
    assert calls, "no _apt_install() calls found at all"

    # Verify each of the 7 originally-broken package lists is now
    # passed via _apt_install (substring match within the call args).
    expected_via_helper = [
        # The actual v0.3.16 user-reported failure site
        ("docker-ce docker-ce-cli containerd.io",
         "Docker install — the v0.3.16 user-reported failure site"),
        # Other system installs
        ("software-properties-common",
         "PPA enabler install (precedes deadsnakes add-apt-repository)"),
        ("python3.10 python3.10-venv",
         "Python 3.10 + venv install (post deadsnakes PPA)"),
        ("ca-certificates curl gnupg lsb-release",
         "Docker repo prereqs install"),
        # v0.3.16+: the original 4-package list was split into
        # CORE (perftest rdma-core libibverbs-dev) + MOFED-OPTIONAL
        # (libmlx5-dev) — see _install_rdma_userspace docstring +
        # tests/test_rdma_install_split.py. Pin the CORE list here;
        # the libmlx5-dev split is covered by the dedicated test
        # file.
        ("perftest rdma-core libibverbs-dev",
         "RDMA userspace install — core package set (v0.3.16+ split)"),
    ]
    for pkg_string, label in expected_via_helper:
        found = any(pkg_string in call for call in calls)
        assert found, (
            f"Callsite for {label} ({pkg_string!r}) is not going "
            f"through _apt_install — likely reverted to bare "
            f"run_command()."
        )
