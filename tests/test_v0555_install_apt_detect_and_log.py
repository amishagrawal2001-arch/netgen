"""v0.5.55 — apt-get update success detection by exit code +
install_rdma.sh preserves apt failure log.

Audit findings H6 + H7.

H6: install_dpdk.sh detected `apt-get update` success by:
    apt-get update ... 2>&1 | grep -q "Reading package lists"
False-positives when apt prints that line then fails (DNS, repo
signing, Hash mismatch). False-negatives when newer apt formats
omit the literal string on cached refreshes. With pipefail
already on, the real exit code is available — check $? directly.

H7: install_rdma.sh apt failure went to terminal only:
    if ! eval "$core_apt_cmd" 2>&1; then
       log_error "Core RDMA package install failed."
       exit 2
    fi
The wizard captures `exit 2` but has no log file to tail —
contradicts the v0.5.30 lesson learned in install_dpdk.sh
(preserve /tmp/dpdk_deps_install.log).
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_DPDK = _REPO / "resources" / "dpdk" / "install_dpdk.sh"
_RDMA = _REPO / "resources" / "dpdk" / "install_rdma.sh"


def test_dpdk_apt_update_no_longer_uses_string_match():
    """The `grep -q "Reading package lists"` pattern must be GONE
    from the apt-get update retry loop."""
    s = _DPDK.read_text()
    # Locate the retry loop body.
    loop = re.search(
        r"while\s+\[\[\s+\$RETRY_COUNT\s+-lt\s+\$MAX_RETRIES[\s\S]+?done",
        s,
    )
    assert loop, "apt-update retry loop not located"
    body = loop.group(0)
    assert "Reading package lists" not in body, (
        "Retry loop still uses `grep -q \"Reading package lists\"` "
        "— false-positives on apt-fail and false-negatives on "
        "newer apt versions."
    )


def test_dpdk_apt_update_uses_exit_code_via_pipeline_with_pipefail():
    """The fix uses pipefail-propagated exit code. The pipeline
    tail/sed is for log formatting — pipefail makes the apt-get
    exit propagate, so the `if` block checks the real rc."""
    s = _DPDK.read_text()
    loop = re.search(
        r"while\s+\[\[\s+\$RETRY_COUNT\s+-lt\s+\$MAX_RETRIES[\s\S]+?done",
        s,
    )
    body = loop.group(0)
    # The `if apt-get update ... | tail | sed ...; then` pattern.
    assert re.search(
        r"if\s+apt-get\s+update[\s\S]+?tail[\s\S]+?sed[\s\S]+?;\s*then",
        body,
    ), (
        "Retry loop doesn't use `if apt-get update ... | tail | "
        "sed ...; then` (pipefail-propagated rc check)."
    )


def test_rdma_apt_install_tees_to_log_file():
    """install_rdma.sh must tee apt-get install output to a known
    log file so the wizard can surface it on failure."""
    s = _RDMA.read_text()
    # Find the apt-install branch.
    block = re.search(
        r"if\s+!\s+\([\s\S]+?eval\s+\"\$core_apt_cmd\"[\s\S]+?fi",
        s,
    )
    assert block, (
        "install_rdma.sh apt install block not located. Did the "
        "v0.5.55 edit preserve the if-block structure?"
    )
    body = block.group(0)
    assert "tee" in body, (
        "install_rdma.sh apt install doesn't `tee` output to a "
        "log file — wizard has nothing to grep on failure."
    )
    assert "/tmp/" in body or "RDMA_APT_LOG" in body, (
        "Log path isn't mentioned by name — operator can't find "
        "the file without source-diving."
    )


def test_rdma_apt_failure_tails_log_into_log_error():
    """On failure, install_rdma.sh must tail the log to stderr
    so the wizard log captures the actual apt error, not just
    `exit 2`."""
    s = _RDMA.read_text()
    block = re.search(
        r"if\s+!\s+\([\s\S]+?eval\s+\"\$core_apt_cmd\"[\s\S]+?fi",
        s,
    )
    body = block.group(0)
    assert "tail" in body, (
        "install_rdma.sh failure path doesn't tail the log into "
        "the error output."
    )


def test_rdma_apt_log_path_is_consistent_with_dpdk_log():
    """v0.5.30 introduced `/tmp/dpdk_deps_install.log` for
    install_dpdk.sh. install_rdma.sh's log should follow the same
    convention (`/tmp/<thing>_deps_install.log` or similar) so
    operators can predict where to look."""
    s = _RDMA.read_text()
    assert re.search(
        r"/tmp/rdma[_-]deps[_-]install\.log",
        s,
    ), (
        "install_rdma.sh log path doesn't follow the established "
        "`/tmp/<thing>_deps_install.log` naming convention."
    )


def test_pyproject_version_at_least_0555():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 55), (
        f"Version {m.group(1)} < 0.5.55"
    )
