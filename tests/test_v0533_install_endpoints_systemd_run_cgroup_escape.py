"""v0.5.33 — install_dpdk + install_rdma endpoints escape the
netgen-server.service cgroup via systemd-run.

Operator-reported on srv06 (Jun 8 2026, post v0.5.32):

  W: chmod 0700 of directory /var/cache/apt/archives/partial
     failed - SetupAPTPartialDirectory (1: Operation not permitted)
  E: Failed to fetch http://archive.ubuntu.com/.../python3-pyelftools_0.30-1_all.deb
     Could not open file .../partial/python3-pyelftools_0.30-1_all.deb
     - open (13: Permission denied)
  [✗] Dependency installation failed

The v0.5.30 hard gate did its job — operator saw the apt failure
inline instead of a confusing downstream meson error. But the
v0.5.31 `-o APT::Sandbox::User=root` fix wasn't enough by itself:
even running apt as root, the netgen-server.service systemd unit's
own sandbox restrictions (ProtectSystem= / ReadWritePaths= /
RestrictNamespaces= / similar) prevent root inside the cgroup
from writing to /var/cache/apt/archives/partial. That's a
chmod/chown failing as root — pure cgroup-level deny, not an
apt-level deny.

The fix has to escape the cgroup entirely. Same pattern as
v0.5.23's upgrade_wheel fix: wrap the script spawn in `systemd-run
--wait --pipe --collect` so install_dpdk.sh / install_rdma.sh
run in a fresh transient systemd unit with vanilla defaults.
`--wait` blocks until the unit exits (so proc.poll() tracking
keeps working), `--pipe` forwards stdout/stderr (so log capture
keeps working), `--collect` auto-cleans the unit on exit.

These tests pin both endpoints. Anyone removing the systemd-run
wrap earns a test failure here, not the next srv06 sandbox-EPERM
spiral.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _install_dpdk_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_install_dpdk\(\)[\s\S]+?(?=\n@app\.route|\ndef api_)",
        src,
    )
    assert m, "api_admin_install_dpdk body not found"
    return m.group(0)


def _install_rdma_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_install_rdma\(\)[\s\S]+?(?=\n@app\.route|\ndef api_)",
        src,
    )
    assert m, "api_admin_install_rdma body not found"
    return m.group(0)


# ────────────────── install_dpdk endpoint cgroup escape ──────────────


def test_install_dpdk_wraps_in_systemd_run_when_available():
    """The endpoint must check `_systemd_run_available()` and, when
    it returns a path, wrap the bash spawn in `systemd-run`. Pre-
    v0.5.33 the spawn was a plain Popen → child of netgen-server
    → inherits its sandbox → apt can't write to /var/cache/apt."""
    body = _install_dpdk_body()
    assert "_systemd_run_available()" in body, (
        "api_admin_install_dpdk doesn't probe for systemd-run — "
        "always runs as a Popen child of netgen-server, inheriting "
        "its sandbox restrictions."
    )
    assert "systemd-run\"" not in body, (
        "Literal `systemd-run\"` would be a bug — should use the "
        "path returned by _systemd_run_available() (cached + "
        "euid-gated)."
    )


def test_install_dpdk_systemd_run_uses_wait_pipe_collect():
    """--wait keeps proc.poll() tracking working (systemd-run
    blocks until unit exits). --pipe forwards stdout/stderr so log
    capture continues to work via Popen's stdout=log_fh. --collect
    auto-removes the transient unit on exit so we don't accumulate
    stale unit files in `systemctl list-units --all`."""
    body = _install_dpdk_body()
    assert '"--wait"' in body, (
        "systemd-run wrap missing --wait — server would lose track "
        "of install completion (systemd-run --no-block returns "
        "immediately, proc.poll() would report rc=0 in milliseconds)."
    )
    assert '"--pipe"' in body, (
        "systemd-run wrap missing --pipe — install log capture "
        "would break (no stdout/stderr forwarding)."
    )
    assert '"--collect"' in body, (
        "systemd-run wrap missing --collect — transient units "
        "accumulate as 'failed' or 'inactive' in systemctl list-units."
    )


def test_install_dpdk_systemd_run_unit_name_is_unique_per_run():
    """The transient unit name must include a per-invocation suffix
    (timestamp) so back-to-back installs don't conflict on the
    unit name."""
    body = _install_dpdk_body()
    assert re.search(
        r'f["\']netgen-install-dpdk-runner-\{[^}]+\}\.service["\']',
        body,
    ), (
        "Unit name isn't an f-string with a per-invocation suffix — "
        "repeat installs would conflict on the unit name."
    )


def test_install_dpdk_systemd_run_sets_env_via_property():
    """The transient unit doesn't inherit the parent process env.
    install_dpdk.sh needs AUTO_MODE / TERM / DEBIAN_FRONTEND /
    DEBIAN_PRIORITY / HOME. Set them explicitly via --setenv."""
    body = _install_dpdk_body()
    for env_pair in (
        "HOME=/root",
        "AUTO_MODE=1",
        "TERM=xterm",
        "DEBIAN_FRONTEND=noninteractive",
        "DEBIAN_PRIORITY=critical",
    ):
        assert f"--setenv={env_pair}" in body or \
               f'"--setenv={env_pair}"' in body, (
            f"systemd-run wrap doesn't set {env_pair} via --setenv. "
            f"install_dpdk.sh would lose this env var in the "
            f"transient unit."
        )


def test_install_dpdk_falls_back_to_bare_popen_when_no_systemd_run():
    """On non-systemd hosts / non-root processes,
    _systemd_run_available() returns None. The endpoint must still
    work in that mode — fall back to the v0.5.32 bare Popen."""
    body = _install_dpdk_body()
    # The cmd assignment must start as a bash invocation, then be
    # extended with systemd-run only when available.
    assert re.search(
        r'cmd\s*=\s*\[\s*["\']bash["\']',
        body,
    ), (
        "Initial cmd assignment isn't `cmd = [\"bash\", ...]` — "
        "non-systemd-run path is broken."
    )
    # And the systemd-run wrap must be inside `if systemd_run:`.
    assert re.search(
        r"if\s+systemd_run\s*:[\s\S]+?cmd\s*=\s*\[",
        body,
    ), (
        "systemd-run wrap isn't gated on `if systemd_run` — "
        "non-systemd hosts would hit a NameError."
    )


# ────────────────── install_rdma endpoint cgroup escape ──────────────


def test_install_rdma_wraps_in_systemd_run_when_available():
    """Same pattern as install_dpdk — the RDMA install runs under
    the same systemd sandbox so needs the same cgroup escape."""
    body = _install_rdma_body()
    assert "_systemd_run_available()" in body, (
        "api_admin_install_rdma doesn't probe for systemd-run — "
        "apt sandbox failures would recur."
    )


def test_install_rdma_uses_wait_pipe_collect_too():
    body = _install_rdma_body()
    assert '"--wait"' in body, "install_rdma systemd-run wrap missing --wait"
    assert '"--pipe"' in body, "install_rdma systemd-run wrap missing --pipe"
    assert '"--collect"' in body, "install_rdma systemd-run wrap missing --collect"


def test_install_rdma_unit_name_distinct_from_dpdk():
    """The transient unit name should clearly identify it as the
    RDMA install (not the DPDK install) — eases `systemctl
    list-units` reading + journalctl grepping."""
    body = _install_rdma_body()
    assert "netgen-install-rdma-runner" in body, (
        "install_rdma unit name isn't `netgen-install-rdma-runner-*` "
        "— would be confusable with the DPDK install unit."
    )


# ────────────────── Documentation / rationale ───────────────────────


def test_install_dpdk_documents_sandbox_escape_rationale():
    """The systemd-run wrap is non-obvious to a future reader.
    The endpoint must have a comment explaining WHY the wrap is
    there (operator-reported apt EPERM, sandbox inheritance) so
    a refactor doesn't silently drop it."""
    body = _install_dpdk_body()
    assert "sandbox" in body.lower() and "v0.5.33" in body, (
        "api_admin_install_dpdk doesn't document the sandbox "
        "escape rationale. Future refactor may drop the systemd-"
        "run wrap thinking it's redundant."
    )


def test_pyproject_version_at_least_0533():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 33), (
        f"Version {m.group(1)} < 0.5.33"
    )
