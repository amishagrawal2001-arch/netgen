"""v0.5.71 — operability batch: hugepages server lock, rc=75
distinct surface, install mutex, kill switch, log pruning.

Audit findings M1, M2, M3, M4, M5.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_hugepages_handler_acquires_lock_non_blocking():
    """M1: the hugepages handler must take a non-blocking try-
    acquire on _DPDK_BIND_LOCK so a racing request gets a clean
    409 instead of queueing for minutes."""
    src = _src()
    body = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert re.search(
        r"_DPDK_BIND_LOCK\.acquire\(blocking=False\)",
        body,
    ), (
        "hugepages handler doesn't non-blocking-acquire "
        "_DPDK_BIND_LOCK"
    )
    # And releases in finally.
    assert re.search(
        r"finally:[\s\S]{0,500}?_DPDK_BIND_LOCK\.release\(\)",
        body,
    ), (
        "hugepages handler doesn't release the lock in finally"
    )


def test_install_dpdk_log_reports_reboot_required_on_rc75():
    """M2: install_dpdk_log response must include
    `reboot_required: True` when return_code == 75."""
    src = _src()
    log_body = re.search(
        r"def api_admin_install_dpdk_log\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert re.search(
        r'"reboot_required"\s*:\s*return_code\s*==\s*75',
        log_body,
    ), (
        "install_dpdk_log doesn't surface reboot_required=True "
        "on rc=75"
    )


def test_install_dpdk_log_treats_rc75_as_success():
    """The `success` flag must also be True for rc=75 — operator
    sees green banner instead of red 'failed (rc=75)'."""
    src = _src()
    log_body = re.search(
        r"def api_admin_install_dpdk_log\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert re.search(
        r"return_code\s*==\s*0\s*or\s*return_code\s*==\s*75",
        log_body,
    ), (
        "success flag doesn't include rc=75 as a success path"
    )


def test_install_dpdk_and_install_rdma_mutually_exclude():
    """M3: install_dpdk must 409 when install_rdma is running,
    and vice versa. dpkg-lock contention wedges otherwise."""
    src = _src()
    dpdk_body = re.search(
        r"def api_admin_install_dpdk\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    rdma_body = re.search(
        r"def api_admin_install_rdma\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert "_ADMIN_INSTALL_RDMA_STATE" in dpdk_body, (
        "install_dpdk doesn't check RDMA state — parallel runs "
        "wedge on dpkg lock"
    )
    assert "_ADMIN_INSTALL_STATE" in rdma_body, (
        "install_rdma doesn't check DPDK state — same issue"
    )


def test_install_handlers_support_force_kill():
    """M4: ?force=1 (or `force: true` in body) must terminate
    the previous process and proceed."""
    src = _src()
    for func in ("api_admin_install_dpdk", "api_admin_install_rdma"):
        body = re.search(
            rf"def {func}\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
            src,
        ).group(0)
        assert "force" in body, (
            f"{func} doesn't accept a force flag"
        )
        assert re.search(
            r"proc\.terminate\(\)",
            body,
        ), (
            f"{func} doesn't SIGTERM the previous process on force=1"
        )


def test_install_dpdk_prunes_old_logs():
    """M5: /tmp/netgen_install_*.log files older than 7 days
    should be pruned when a new install spawns."""
    src = _src()
    body = re.search(
        r"def api_admin_install_dpdk\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert re.search(
        r"netgen_install_\*\.log",
        body,
    ), (
        "install_dpdk doesn't glob /tmp/netgen_install_*.log "
        "for pruning"
    )
    assert re.search(
        r"7\s*\*\s*86400",
        body,
    ), (
        "install_dpdk doesn't apply a 7-day cutoff for log "
        "pruning"
    )


def test_pyproject_version_at_least_0571():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 71), (
        f"Version {m.group(1)} < 0.5.71"
    )
