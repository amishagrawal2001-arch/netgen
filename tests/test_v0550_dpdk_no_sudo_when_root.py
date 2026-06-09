"""v0.5.50 — DPDK endpoints don't call `sudo` from a process
that's already root, sidestepping the systemd-hardening EPERM.

Operator-reported on srv06 (Jun 9 2026), bind ens10f1:

  HTTP 500
  {"message":"sudo: PERM_SUDOERS: setresuid(-1, 1, -1):
   Operation not permitted\\nsudo: unable to open /etc/sudoers:
   Operation not permitted\\nsudo: error initializing audit
   plugin sudoers_audit\\n","output":"","success":false}

Cause: netgen-server.service runs as root but has
`CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN`. The bounding
set CAPS what the process can hold — anything NOT listed is
permanently dropped, even though the process runs as UID 0. So
`CAP_SETUID` is dropped (sudo needs it to setresuid to the
sudoers parser UID), `CAP_DAC_OVERRIDE` is dropped (sudo needs
it to read /etc/sudoers which is mode 0440 root:root), and sudo
fails before it ever runs the wrapped command. The bind never
even starts.

v0.5.50:

  1. Add `_maybe_sudo(cmd)` helper — returns `cmd` as-is when
     `geteuid() == 0`, prepends `sudo` only when actually non-
     root. This sidesteps the EPERM entirely when running under
     the standard systemd unit (which is root anyway).

  2. Replace ALL 6 `["sudo", ...]` literals in the DPDK paths:
     - 2× /api/dpdk/interfaces status reads
     - 1× /api/dpdk/status devbind read
     - 1× /api/dpdk/bind
     - 1× /api/dpdk/unbind
     - 1× /api/dpdk/load_modules sudo fallback

  3. The only legitimate sudo use is when running the server as
     a non-root user (uncommon). That path still works — the
     helper passes through `sudo` when geteuid != 0.

If the operator wants a complete cap fix (add CAP_SYS_MODULE +
CAP_SYS_ADMIN + CAP_SYS_BOOT to the unit so modprobe/mount/
reboot work in-process too), that's audit finding H8 and ships
separately.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_maybe_sudo_helper_defined():
    """`_maybe_sudo()` helper must exist and accept a cmd
    list/iterable."""
    src = _src()
    m = re.search(r"def _maybe_sudo\([\s\S]+?return\s+\[", src)
    assert m, "_maybe_sudo() helper missing from run_tgen_server.py"


def test_maybe_sudo_skips_sudo_when_root():
    """The helper must return the command UNCHANGED when running
    as root (geteuid == 0). Otherwise the EPERM bug stays."""
    src = _src()
    # Locate the helper body.
    m = re.search(
        r"def _maybe_sudo\(cmd\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    # geteuid == 0 check followed by a return that does NOT
    # include the literal "sudo".
    root_branch = re.search(
        r"if\s+os\.geteuid\(\)\s*==\s*0:\s*\n\s+return\s+([^\n]+)",
        body,
    )
    assert root_branch, (
        "_maybe_sudo doesn't check geteuid == 0 — root path "
        "would still prepend sudo and hit the EPERM."
    )
    root_return = root_branch.group(1)
    assert "sudo" not in root_return, (
        "_maybe_sudo root path still includes sudo — defeats "
        "the entire fix."
    )


def test_maybe_sudo_still_prepends_sudo_when_non_root():
    """When the server runs as a non-root user (uncommon), the
    helper must still prepend sudo so the command can elevate.
    The fix isn't 'sudo never works' — it's 'sudo doesn't run
    when we're already root'."""
    src = _src()
    m = re.search(
        r"def _maybe_sudo\(cmd\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # The non-root fallthrough must prepend "sudo" to cmd.
    assert re.search(
        r'return\s+\["sudo"\]\s*\+\s*list\(cmd\)',
        body,
    ), (
        "_maybe_sudo non-root path doesn't prepend sudo — would "
        "break the legitimate non-root install case."
    )


def test_no_literal_sudo_in_dpdk_subprocess_calls():
    """No remaining `["sudo", ...]` literals in subprocess
    invocations across the file (apart from inside the
    `_maybe_sudo` helper itself). A grep-style guard pins the
    fix is complete and doesn't regress."""
    src = _src()
    # Strip the helper body first — its return contains the
    # literal as data.
    sanitised = re.sub(
        r"def _maybe_sudo\(cmd\)[\s\S]+?(?=\ndef [a-z_])",
        "",
        src,
    )
    # Now grep for any remaining `["sudo"` (list-style) or
    # `"sudo",` (positional arg). Both are subprocess.run-style
    # cmd-list patterns.
    remaining = re.findall(r'\["sudo",|"sudo",\s+', sanitised)
    assert not remaining, (
        f"{len(remaining)} subprocess invocation(s) still use "
        f"the `[\"sudo\", ...]` literal directly. Each one will "
        f"EPERM when CAP_SETUID is dropped. Use _maybe_sudo() "
        f"instead."
    )


def test_bind_endpoint_uses_maybe_sudo():
    """`/api/dpdk/bind` must construct its dpdk_bind.sh command
    via the helper, not a literal sudo. This is the endpoint
    the operator hit on srv06."""
    src = _src()
    # Locate the dpdk_bind handler.
    m = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m, "dpdk_bind() handler not located"
    body = m.group(0)
    assert "_maybe_sudo(" in body, (
        "dpdk_bind() doesn't use _maybe_sudo() — bind still "
        "EPERMs on hardened netgen-server.service."
    )


def test_unbind_endpoint_uses_maybe_sudo():
    """Mirror of test_bind: /api/dpdk/unbind also wraps."""
    src = _src()
    m = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m, "dpdk_unbind() handler not located"
    body = m.group(0)
    assert "_maybe_sudo(" in body, (
        "dpdk_unbind() doesn't use _maybe_sudo() — once a NIC "
        "IS bound, the unbind would EPERM the same way."
    )


def test_load_modules_sudo_fallback_uses_helper():
    """The sudo-fallback inside dpdk_load_modules (which only
    fires when geteuid != 0 — see line 14518's
    `not is_root` guard) must also go through the helper. Pre-
    fix it was a literal `["sudo", modprobe_path, module]`,
    which would EPERM on a hardened non-root install."""
    src = _src()
    m = re.search(
        r"def dpdk_load_modules\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # The sudo fallback block must use _maybe_sudo, not literal.
    assert re.search(
        r"if\s+error_msg\s+and\s+module\s+not\s+in\s+loaded_modules"
        r"[\s\S]+?_maybe_sudo\(",
        body,
    ), (
        "load_modules sudo fallback still uses a literal "
        "[\"sudo\", ...] — would EPERM."
    )


def test_pyproject_version_at_least_0550():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 50), (
        f"Version {m.group(1)} < 0.5.50"
    )
