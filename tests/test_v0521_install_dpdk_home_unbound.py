"""Regression test for v0.5.21: install_dpdk.sh must not die with
"HOME: unbound variable" when systemd spawns it.

Operator-reported on srv06 (via the v0.5.17 polling + v0.5.20 log
tail):

  /opt/netgen/resources/dpdk/install_dpdk.sh: line 23: HOME: unbound variable

Root cause: the script has `set -euo pipefail` (line 9 — strict
mode) + references `$HOME` on line 23 in the default for DPDK_DIR
(`${DPDK_DIR:-$HOME/SURAJ/dpdk}`). Even though `:-` is the default
substitution operator, the bare `$HOME` reference INSIDE the
default expansion dies under `set -u` when HOME is unset.

systemd services start with a minimal environment. The
netgen-server.service unit doesn't `Environment="HOME=/root"`, so
when /api/admin/install_dpdk spawns the script, HOME is missing.

Plus a separate code-hygiene issue: `$HOME/SURAJ/dpdk` is a stale
developer path (SURAJ = the original author's home dir name)
that's been in the code since v0.2.x. Should never have shipped to
production.

v0.5.21 fixes both:
  1. Script: `: "${HOME:=/root}"` defaults HOME before any use.
  2. Script: DPDK_DIR default → /opt/dpdk-build (sane path,
     no developer name).
  3. Server: env.setdefault("HOME", "/root") in the Popen env
     so pre-v0.5.21 scripts on un-upgraded hosts also survive.
"""
from __future__ import annotations

import re
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_dpdk.sh"
)
_SERVER = (
    Path(__file__).resolve().parents[1] / "run_tgen_server.py"
)


def test_script_provides_default_for_home():
    """The script must establish a HOME default BEFORE any reference
    to $HOME under `set -u`. The standard idiom is
    `: "${HOME:=/root}"` (the `:=` operator sets the variable if
    unset, the `:` no-op command makes it a valid statement)."""
    src = _SCRIPT.read_text()
    # The script's `set -euo pipefail` is at line 9; HOME default
    # must come BEFORE the first $HOME reference (which was line 23).
    assert ':"${HOME:=' in src or ': "${HOME:=' in src, (
        "install_dpdk.sh doesn't establish a HOME default. Under "
        "`set -u` (line 9), the next `$HOME` reference dies with "
        "'HOME: unbound variable' when systemd-spawned with no HOME."
    )
    # And it must default to a writable dir — /root for the
    # netgen-server service's root user.
    assert re.search(r'HOME:=/root|HOME:="/root"', src), (
        "HOME default isn't /root. The netgen-server service runs "
        "as root; HOME should match."
    )


def test_script_removes_stale_developer_path():
    """The legacy default `$HOME/SURAJ/dpdk` is a developer's local
    path that shouldn't be in production code. v0.5.21 swaps it
    for /opt/dpdk-build (matches the project's OPT-prefix
    convention: /opt/netgen-server, /opt/OSTG)."""
    src = _SCRIPT.read_text()
    # SURAJ path must not be the active default anywhere.
    # Comments referencing the historical name for changelog
    # context are allowed (they help future grepping), but the
    # path must not appear in an unquoted reference position.
    # Specifically: it must not appear as `"$HOME/SURAJ/dpdk"`
    # in a context that's NOT inside a comment line.
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # comment — historical reference allowed
        assert "SURAJ" not in line, (
            f"Stale developer path 'SURAJ' still present in active "
            f"line:\n  {line!r}\n"
            f"Should be replaced with /opt/dpdk-build."
        )


def test_script_uses_opt_dpdk_build_as_default():
    """The new default DPDK source directory should be
    /opt/dpdk-build — matches the project's /opt/* convention
    (/opt/netgen-server, /opt/OSTG, /opt/netgen)."""
    src = _SCRIPT.read_text()
    assert "/opt/dpdk-build" in src, (
        "install_dpdk.sh doesn't reference /opt/dpdk-build as the "
        "DPDK source dir default. v0.5.21 standardizes on this "
        "path."
    )


def test_server_popen_sets_home_in_env():
    """Belt-and-braces: the server-side Popen call must
    env.setdefault('HOME', '/root') so older script versions on
    un-upgraded servers also survive (they wouldn't have the
    in-script default)."""
    src = _SERVER.read_text()
    # Find the install_dpdk Popen.
    m = re.search(
        r"def api_admin_install_dpdk[\s\S]+?subprocess\.Popen[\s\S]+?\)",
        src,
    )
    assert m, "api_admin_install_dpdk Popen not found"
    body = m.group(0)
    assert re.search(
        r'env\.setdefault\(\s*["\']HOME["\']\s*,\s*["\']/root["\']\s*\)',
        body,
    ), (
        "Server's Popen env doesn't setdefault('HOME', '/root'). "
        "Pre-v0.5.21 scripts on un-upgraded hosts will keep hitting "
        "'HOME: unbound variable'."
    )


def test_pyproject_version_at_least_0521():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 21), (
        f"Version {m.group(1)} < 0.5.21"
    )
