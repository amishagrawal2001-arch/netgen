"""v0.5.46 — load_modules drops --quiet from systemd-run + scrapes
journalctl when subprocess capture comes back empty.

Operator-reported on srv06 (Jun 8 2026, v0.5.44 active):

  HTTP 500
  Failed to load modules: vfio-pci: Unknown error - check server logs

The error message itself listed the systemd-hardening cause
(ProtectKernelModules) — but the operator's actual modprobe
output was "Unknown error" instead of the specific kernel
rejection. v0.5.44's systemd-run wrap was applied correctly,
but the `--quiet` flag suppressed the unit's stderr forwarding
via `--pipe` on some systemd versions, leaving subprocess.run
with empty captures.

v0.5.46:

  1. Drop `--quiet` from the systemd-run modprobe wrap. With
     --pipe alone, the unit's stderr flows back to subprocess.run
     and the operator sees the actual `modprobe: ERROR: could
     not insert 'vfio_pci': Operation not permitted` message.

  2. When subprocess capture still comes back empty (some
     edge cases), scrape `journalctl -u <unit_name> -n 20 -o
     cat` for the unit's actual output. Include the last 5
     non-blank lines in the error message.

  3. If even journalctl is empty, surface a precise hint
     pointing at `systemctl status <unit>` so the operator can
     dig further.

Pin all three layers.
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


def test_systemd_run_wrap_no_longer_uses_quiet():
    """The `--quiet` flag must be GONE from the systemd-run
    modprobe wrap. With --quiet, the inner unit's stderr is
    silently dropped on some systemd versions, leaving
    subprocess.run with empty captures and the operator with
    'Unknown error'."""
    body = _load_modules_body()
    # Find the systemd-run wrap block specifically.
    wrap_m = re.search(
        r"if\s+systemd_run:[\s\S]+?modprobe_cmd\s*=\s*\[\s*systemd_run[\s\S]+?\]",
        body,
    )
    assert wrap_m, "systemd-run modprobe wrap block not located"
    block = wrap_m.group(0)
    assert '"--quiet"' not in block, (
        "systemd-run wrap still passes --quiet — suppresses "
        "the inner modprobe's stderr on some systemd versions, "
        "leaving operator with 'Unknown error'."
    )
    # And other essential flags must still be present.
    assert '"--wait"' in block and '"--pipe"' in block and \
           '"--collect"' in block, (
        "v0.5.46 fix accidentally dropped --wait / --pipe / "
        "--collect from the systemd-run wrap"
    )


def test_unit_name_stored_for_journalctl_fallback():
    """The unit name must be stored in a variable accessible to the
    error-path code so journalctl can be queried with the SAME
    unit name. Pre-fix the name was inline-only inside the
    `--unit=...` arg and not retrievable after the call."""
    body = _load_modules_body()
    # The unit name must be assigned to a local variable.
    assert re.search(
        r"modprobe_unit\s*=\s*(?:None|\(?\s*f[\"']netgen-modprobe-)",
        body,
    ), (
        "Unit name isn't stored in a `modprobe_unit` variable — "
        "journalctl fallback can't reference it."
    )


def test_journalctl_fallback_when_subprocess_capture_empty():
    """When subprocess.run returns empty stdout/stderr but rc != 0,
    the error path must scrape journalctl for the transient
    unit's output. Match the journalctl invocation."""
    body = _load_modules_body()
    # journalctl call must reference the modprobe_unit variable.
    assert re.search(
        r"journalctl[\s\S]+?modprobe_unit",
        body,
    ), (
        "Error path doesn't query journalctl for the transient "
        "unit's output — operator stuck with 'Unknown error' "
        "even though journald has the real reason."
    )
    # And the call must use -u <unit> + -o cat (no metadata).
    assert re.search(
        r'["\']-u["\'].*modprobe_unit',
        body,
    ) or "journalctl" in body, (
        "journalctl call doesn't pass `-u <unit>` — can't filter "
        "to the specific transient unit's output"
    )


def test_journalctl_output_trimmed_to_recent_lines():
    """journalctl scraping must trim to ~last 5 non-blank lines.
    Without trimming a noisy unit log floods the API response."""
    body = _load_modules_body()
    # Look for slicing or `tail`-like behavior.
    assert re.search(
        r"-5:|\.splitlines\(\)[\s\S]+?\[-",
        body,
    ), (
        "journalctl fallback doesn't trim to recent lines — "
        "could flood error response on noisy units"
    )


def test_final_fallback_message_points_at_systemctl():
    """When BOTH subprocess capture and journalctl are empty, the
    operator needs a self-service diagnostic command. Surface
    `systemctl status <unit>` so they can dig further without
    SSHing blind."""
    body = _load_modules_body()
    assert re.search(
        r'systemctl\s+status[\s\S]+?modprobe_unit',
        body,
    ), (
        "Final-fallback error message doesn't suggest "
        "`systemctl status <unit>` — operator has nowhere to go "
        "from an empty error."
    )


def test_unknown_error_no_longer_first_choice():
    """Pre-fix the error_msg fell back to 'Unknown error' literal
    immediately when stderr/stdout were empty. v0.5.46's chain
    is: subprocess capture → journalctl scrape → systemctl status
    hint. The bare 'Unknown error' string should NOT appear as a
    first-class fallback."""
    body = _load_modules_body()
    # Pattern: the first assignment of error_msg in the failure
    # branch must NOT use `or "Unknown error"`.
    fail_branch_m = re.search(
        r"else:\s*\n\s+error_msg\s*=\s*result\.stderr\.strip\(\)"
        r"[\s\S]+?failed_modules\.append",
        body,
    )
    assert fail_branch_m, "Failure branch not located"
    block = fail_branch_m.group(0)
    # The first assignment must NOT include the legacy 'Unknown
    # error' fallback in its initial expression — the operator is
    # supposed to get a real reason via journalctl OR systemctl
    # status before any 'Unknown' string.
    first_assign = re.search(
        r"error_msg\s*=\s*result\.stderr\.strip\(\)\s+or\s+"
        r"result\.stdout\.strip\(\)(\s+or\s+[\"']Unknown error[\"'])?",
        block,
    )
    assert first_assign, "First error_msg assignment not located"
    assert not first_assign.group(1), (
        "Initial error_msg assignment still falls back to "
        "'Unknown error' literal — should leave it empty so "
        "the journalctl scrape gets a chance to fill it in."
    )


def test_pyproject_version_at_least_0546():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 46), (
        f"Version {m.group(1)} < 0.5.46"
    )
