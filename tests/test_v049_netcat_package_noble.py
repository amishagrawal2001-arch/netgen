"""v0.4.9 — apt branch must use `netcat-openbsd`, not bare `netcat`.

Operator-reported in the v0.4.8 fresh-install log on Ubuntu 24.04:

  E: Package 'netcat' has no installation candidate
  [WARNING] Package installation encountered issues: ... returned non-zero exit status 100.

On Ubuntu 24.04 (Noble) `netcat` is a VIRTUAL package — there's no
install candidate. It's provided by `netcat-openbsd` or
`netcat-traditional`. Because apt fails the WHOLE batch on a
missing candidate, the entire 60-package system-deps install bailed
with rc=100. The installer continued (rightly — apt failures here
are downgraded to WARNING since the build can still succeed
without every userspace tool), but the failure was confusing.

Pre-v0.4.9 the apt branch (`_install_apt_packages`) had bare
`netcat` while the apk branch (Alpine, also Debian-family naming)
already used `netcat-openbsd`. v0.4.9 aligns the apt branch.

Note: the zypper branch (openSUSE / SLES) uses bare `netcat` and
that's correct — SUSE has a real package by that name, distinct
from the netcat-openbsd / netcat-traditional split on Debian/Ubuntu.
"""
from __future__ import annotations

import re
from pathlib import Path


_INSTALLER = (
    Path(__file__).resolve().parents[1] / "install_ostg_complete.py"
)


def test_apt_branch_uses_netcat_openbsd():
    """Find the `_install_apt_packages` function body and verify
    the package list contains `netcat-openbsd`, NOT bare `netcat`."""
    src = _INSTALLER.read_text()
    m = re.search(
        r"def _install_apt_packages\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_install_apt_packages function not found"
    body = m.group(0)

    # Must include the correct package name
    assert '"netcat-openbsd"' in body, (
        "apt package list missing netcat-openbsd. On Ubuntu 24.04 "
        "(Noble) bare `netcat` is a virtual package with no install "
        "candidate — the whole apt batch fails with rc=100. The fix "
        "is to use `netcat-openbsd` explicitly."
    )

    # Must NOT include bare `netcat` as a PACKAGE LIST ENTRY.
    # Match the entry pattern (`"netcat", ` or `"netcat"]`)
    # specifically — the v0.4.9 fix comment also contains the
    # substring "netcat" inside prose, which is fine.
    assert not re.search(r'"netcat"\s*[,\]]', body), (
        'apt package list still contains bare "netcat" as an entry '
        '— that\'s a virtual package on Ubuntu 24.04+, no install '
        'candidate. Operator-reported in the v0.4.8 fresh-install '
        'log on a clean Noble host:\n'
        '  E: Package \'netcat\' has no installation candidate\n'
        '  [WARNING] ... returned non-zero exit status 100.'
    )


def test_apk_branch_already_uses_netcat_openbsd():
    """Alpine's apk branch was already correct pre-v0.4.9; pin it
    so a future refactor doesn't accidentally regress to bare
    `netcat` (Alpine has the same virtual-package issue)."""
    src = _INSTALLER.read_text()
    m = re.search(
        r"def _install_apk_packages\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_install_apk_packages function not found"
    body = m.group(0)

    assert '"netcat-openbsd"' in body, (
        "apk package list lost netcat-openbsd — Alpine has the "
        "same virtual-package issue as Debian/Ubuntu."
    )
    assert not re.search(r'"netcat"\s*[,\]]', body), (
        "apk package list regressed to bare `netcat` as an entry"
    )


def test_zypper_branch_keeps_bare_netcat():
    """openSUSE/SLES uses a real `netcat` package, distinct from
    the netcat-openbsd / netcat-traditional split on Debian-family
    distros. Pin so a well-intentioned "consistency" refactor
    doesn't break SUSE installs."""
    src = _INSTALLER.read_text()
    m = re.search(
        r"def _install_zypper_packages\(self\)[\s\S]+?(?=\n    def )",
        src,
    )
    assert m, "_install_zypper_packages function not found"
    body = m.group(0)

    assert '"netcat"' in body, (
        "zypper package list lost bare `netcat` — that's the "
        "actual package name on openSUSE. Don't change to "
        "netcat-openbsd here; SUSE doesn't have that split."
    )
