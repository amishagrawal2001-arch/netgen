"""v0.5.44 — /api/dpdk/load_modules wraps modprobe in systemd-run
to escape ProtectKernelModules.

Operator-reported on srv06 admin console (Jun 8 2026):
  HTTP 500
  Failed to load modules: vfio-pci: modprobe: ERROR: could not
  insert 'vfio_pci': Operation not permitted

The error message itself listed the cause: the netgen-server
systemd unit has `ProtectKernelModules=true` (or a seccomp filter
blocking init_module()). The kernel rejects module load syscalls
from processes in the locked-down cgroup regardless of UID.

This is the same class of bug as v0.5.33's apt-sandbox failure —
the answer is `systemd-run --wait --pipe --collect` to spawn the
operation in a fresh transient unit with vanilla defaults (no
inherited sandbox). The kernel allows init_module() from there.

The skip-already-loaded check + modinfo-builtin check stay
bare-subprocess (read-only, no sandbox issues). Only the
modprobe invocation itself gets the cgroup escape.

Falls back to direct modprobe on non-systemd hosts (containers,
macOS dev, non-root processes) — `_systemd_run_available()`
returns None there.

Pin both: modprobe wrapped when systemd-run available, falls
back cleanly when not.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _load_modules_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_load_modules\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z])",
        src,
    )
    assert m
    return m.group(0)


def test_modprobe_wraps_in_systemd_run_when_available():
    """The bare `[modprobe_path, module]` invocation must become
    a systemd-run wrapped command when systemd-run is available.
    Without this, ProtectKernelModules=true on the netgen-server
    unit blocks every load with EPERM."""
    body = _load_modules_body()
    # The endpoint must probe for systemd-run.
    assert "_systemd_run_available()" in body, (
        "dpdk_load_modules doesn't check for systemd-run. Without "
        "the wrap, modprobe inherits netgen-server's sandbox and "
        "fails with EPERM on ProtectKernelModules hosts."
    )


def test_modprobe_uses_wait_pipe_collect_flags():
    """--wait keeps proc.poll-style tracking working. --pipe
    forwards stdout/stderr so the modprobe error reaches the
    caller. --collect cleans up the transient unit on exit."""
    body = _load_modules_body()
    # Locate the systemd-run construction near the modprobe call.
    sd_block = re.search(
        r"systemd_run\s*=\s*_systemd_run_available\(\)"
        r"[\s\S]+?modprobe_path,?\s*\n",
        body,
    )
    assert sd_block, "systemd-run modprobe wrap block not located"
    block = sd_block.group(0)
    for flag in ('"--wait"', '"--pipe"', '"--collect"'):
        assert flag in block, (
            f"systemd-run wrap missing {flag} — without it: "
            f"--wait absent → caller doesn't see modprobe exit code, "
            f"--pipe absent → stderr lost, "
            f"--collect absent → transient units pile up in "
            f"systemctl list-units"
        )


def test_modprobe_systemd_run_unit_name_is_unique():
    """The transient unit name must include a per-call suffix
    (e.g. module name + timestamp). Without it, two back-to-back
    loads (vfio + vfio-pci) collide on the unit name and the
    second fails with `Unit ... already exists`. Match relaxed:
    the unit name literal must be an f-string (contains `{`) AND
    include `netgen-modprobe-` prefix."""
    body = _load_modules_body()
    # Find any `--unit=...` line near the modprobe wrap.
    unit_m = re.search(
        r"--unit=netgen-modprobe-[^\"']*",
        body,
    )
    assert unit_m, (
        "No --unit=netgen-modprobe-* in modprobe systemd-run "
        "wrap — operator-visible: `systemctl list-units` won't "
        "show modprobe units clearly."
    )
    # And the unit name must include an f-string interpolation
    # (otherwise it's a fixed name and back-to-back calls collide).
    snippet = body[max(0, unit_m.start() - 50):unit_m.end() + 50]
    assert "{" in unit_m.group(0) and "}" in body[unit_m.start():unit_m.end() + 200], (
        "Unit name has no f-string interpolation — back-to-back "
        "modprobe calls (vfio, vfio-pci) would collide on the "
        "same unit name."
    )
    # Module + timestamp give uniqueness.
    assert "module" in snippet.lower() or "time" in snippet.lower(), (
        "Unit name suffix doesn't reference module or timestamp — "
        "may not be unique enough across rapid retries."
    )


def test_modprobe_falls_back_to_bare_when_no_systemd_run():
    """On non-systemd hosts / non-root / Docker,
    _systemd_run_available() returns None. The endpoint must
    fall back to the bare `[modprobe_path, module]` cmd so
    the load still works on those hosts."""
    body = _load_modules_body()
    # The else branch must construct a bare cmd.
    fallback_m = re.search(
        r"else:\s*\n\s+modprobe_cmd\s*=\s*\[\s*modprobe_path\s*,\s*module\s*\]",
        body,
    )
    assert fallback_m, (
        "No else-branch falls back to bare modprobe — non-systemd "
        "hosts (Docker, macOS dev) would NameError on systemd_run "
        "= None."
    )


def test_modprobe_subprocess_uses_constructed_cmd_var():
    """The subprocess.run call must use the `modprobe_cmd`
    variable (which is either the systemd-run wrap or the bare
    command), NOT the original `[modprobe_path, module]` literal.
    A copy-paste regression to the literal silently undoes the
    fix."""
    body = _load_modules_body()
    # Find the relevant subprocess.run call.
    sp_m = re.search(
        r"result\s*=\s*subprocess\.run\(\s*modprobe_cmd\s*,",
        body,
    )
    assert sp_m, (
        "subprocess.run(modprobe_cmd, ...) not found — the wrap "
        "wasn't actually applied to the load call."
    )


def test_modprobe_timeout_bumped_for_systemd_run_overhead():
    """systemd-run adds ~100-200ms overhead for unit setup +
    teardown. The original 10s timeout is fine for bare modprobe
    but tight when wrapped — bump to ≥ 15s so legitimately-slow
    loads (kernel symbol resolution on aged hosts) don't time out."""
    body = _load_modules_body()
    sp_m = re.search(
        r"subprocess\.run\(\s*modprobe_cmd\s*,[\s\S]+?timeout\s*=\s*(\d+)",
        body,
    )
    assert sp_m, "subprocess.run for modprobe_cmd doesn't set timeout"
    timeout = int(sp_m.group(1))
    assert timeout >= 15, (
        f"modprobe subprocess timeout = {timeout}s. systemd-run "
        f"adds overhead — bump to ≥ 15s for headroom."
    )


def test_pyproject_version_at_least_0544():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 44), (
        f"Version {m.group(1)} < 0.5.44"
    )
