"""v0.5.66 — hugetlbfs mount wrapped in systemd-run to escape
CAP_SYS_ADMIN cap restriction.

Operator-reported on srv06 at v0.5.59 (Jun 9 2026):

  POST /api/dpdk/hugepages → HTTP 500
  Failed to mount hugetlbfs at /mnt/huge: Command ['mount', '-t',
  'hugetlbfs', 'nodev', '/mnt/huge'] returned non-zero exit
  status 32. Sysfs allocation rolled back.

Exit 32 = mount(8) general failure; root cause is `mount(2)`
syscall returning EPERM because the calling process doesn't hold
CAP_SYS_ADMIN.

The v0.5.56 caps drop-in
(/etc/systemd/system/netgen-server.service.d/netgen-caps.conf)
adds CAP_SYS_ADMIN, but it only takes effect for NEWLY-started
processes (caps are set at exec time). The v0.5.55 → v0.5.59
wheel upgrade restarted netgen-server before the drop-in was
written (catch-22 noted in v0.5.49 / v0.5.56 release notes), so
the running process is still using the pre-v0.5.56 cap set.

v0.5.66: wrap the mount call in `systemd-run --wait --pipe
--collect` so it runs in a fresh transient unit with vanilla
caps. Same pattern as v0.5.33 (apt cache chmod) and v0.5.44
(modprobe init_module). The wrap operates in addition to the
v0.5.56 cap drop-in — once the operator restarts netgen-server
the in-process caps would also work, but the wrap means
allocation works WITHOUT requiring that restart.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _hugepages_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    return m.group(0)


def test_mount_call_wrapped_in_systemd_run_when_available():
    """The mount call must go through `systemd-run --wait --pipe
    --collect` when systemd-run is available, escaping the
    netgen-server cgroup's potentially-stale cap restriction."""
    body = _hugepages_body()
    # Locate the mount-hugetlbfs block.
    assert "_systemd_run_available()" in body, (
        "Hugepages handler doesn't probe for systemd-run — mount "
        "still inherits the netgen-server cap set"
    )
    # The mount command list must reference the systemd-run binary.
    assert re.search(
        r"_mount_cmd\s*=\s*\[\s*systemd_run",
        body,
    ), (
        "_mount_cmd doesn't include systemd_run — wrap not "
        "applied"
    )


def test_systemd_run_mount_uses_wait_pipe_collect():
    """The wrap needs --wait (caller sees exit code), --pipe
    (stderr reaches subprocess.run for diagnostics), --collect
    (cleanup of transient unit). Mirror the pattern used in
    v0.5.44 modprobe wrap."""
    body = _hugepages_body()
    sd_block = re.search(
        r"systemd_run\s*=\s*_systemd_run_available\(\)[\s\S]+?mount_point\s*,",
        body,
    )
    assert sd_block, "systemd-run mount wrap block not located"
    block = sd_block.group(0)
    for flag in ('"--wait"', '"--pipe"', '"--collect"'):
        assert flag in block, (
            f"systemd-run wrap missing {flag}"
        )


def test_mount_falls_back_to_bare_when_no_systemd_run():
    """On non-systemd hosts / containers, `_systemd_run_available`
    returns None. The endpoint must still construct a bare
    `['mount', '-t', 'hugetlbfs', 'nodev', mount_point]` so the
    operation works on those hosts."""
    body = _hugepages_body()
    fallback = re.search(
        r"else:\s*\n\s+_mount_cmd\s*=\s*\[\s*[\n\s]*[\"']mount[\"']\s*,\s*[\"']-t[\"']\s*,\s*[\"']hugetlbfs[\"']",
        body,
    )
    assert fallback, (
        "No bare-mount fallback when systemd-run is unavailable — "
        "container / non-systemd hosts break"
    )


def test_mount_subprocess_uses_constructed_cmd_var():
    """The subprocess.run call must use the `_mount_cmd` variable
    (which selects between the wrap and the bare cmd), NOT the
    original literal. Copy-paste regression to the literal would
    silently undo the fix."""
    body = _hugepages_body()
    assert re.search(
        r"subprocess\.run\(\s*_mount_cmd\s*,",
        body,
    ), (
        "subprocess.run doesn't use _mount_cmd — wrap wasn't "
        "actually applied"
    )


def test_systemd_run_mount_uses_unique_unit_name():
    """Each call needs its own transient unit name so back-to-
    back allocations (rare but possible) don't collide. Use a
    timestamp suffix like v0.5.44 modprobe."""
    body = _hugepages_body()
    assert re.search(
        r"netgen-mount-hugetlbfs-",
        body,
    ), "Transient unit name has no netgen-mount-hugetlbfs prefix"
    assert "time" in body.lower(), (
        "Unit name doesn't reference time / timestamp for "
        "uniqueness"
    )


def test_mount_subprocess_timeout_at_least_15():
    """systemd-run adds ~100-200ms overhead. Bump timeout from
    10s to ≥ 15s to match the v0.5.44 modprobe wrap headroom."""
    body = _hugepages_body()
    m = re.search(
        r"subprocess\.run\(\s*_mount_cmd[\s\S]+?timeout\s*=\s*(\d+)",
        body,
    )
    assert m, "Mount subprocess timeout not found"
    assert int(m.group(1)) >= 15, (
        f"Mount timeout = {m.group(1)}s; bump to ≥ 15 for "
        f"systemd-run overhead"
    )


def test_pyproject_version_at_least_0566():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 66), (
        f"Version {m.group(1)} < 0.5.66"
    )
