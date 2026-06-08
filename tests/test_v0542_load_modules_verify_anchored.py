"""v0.5.42 — /api/dpdk/load_modules verify uses anchored regex,
not substring.

Operator-reported on srv06 (Jun 8 2026, kernel 6.8.0-124):
  Admin console "Load VFIO Modules" toast says "VFIO modules
  loaded" but the Status row continues to show
  "vfio_pci module: Not loaded".

Root cause: post-modprobe verify used a SUBSTRING match:

  if module_pattern in verify_result.stdout.lower():

`module_pattern = "vfio_pci"` substring-matches inside:
  - `vfio_pci_core` ← always present when pds_vfio_pci is loaded
  - `pds_vfio_pci`  ← AMD Pensando driver, auto-loaded on this hw

So `modprobe vfio-pci` could rc=0 without actually loading the bare
`vfio_pci` module, and the verify would still report success.

The skip-already-loaded check at the SAME endpoint already used
an anchored regex (`^{module_pattern}\s`) and worked correctly.
The status endpoint (`/api/dpdk/status`) also uses the anchored
regex. Only the post-modprobe verify diverged → admin console
toast lied about success while Status correctly reported
"Not loaded".

v0.5.42 fixes the verify to use the same anchored regex. Plus:
when verify fails, the error message now includes WHICH related
modules ARE loaded (so the operator can self-diagnose from the
admin console without SSHing — e.g. "you have vfio_pci_core but
not vfio_pci; check if pds_vfio_pci is hogging your hardware").

These tests pin both the fix and the diagnostic context.
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
    assert m, "dpdk_load_modules body not found"
    return m.group(0)


def test_verify_uses_anchored_regex_not_substring():
    """The post-modprobe verify must use `re.search(rf'^{module_pattern}\\s', ...)`
    — the same anchored pattern as the skip-already-loaded check.
    A substring `module_pattern in lsmod_output` falsely succeeds
    when `vfio_pci` appears inside `vfio_pci_core` or `pds_vfio_pci`."""
    body = _load_modules_body()
    # Locate the modprobe-rc-0 → verify block.
    verify_block_m = re.search(
        r"if\s+result\.returncode\s*==\s*0:[\s\S]+?failed_modules\.append",
        body,
    )
    assert verify_block_m, "Post-modprobe verify block not located"
    block = verify_block_m.group(0)
    # The anchored regex must be present.
    assert re.search(
        r"re\.search\(\s*rf?['\"]\^\{module_pattern\}\\s['\"]",
        block,
    ), (
        "Post-modprobe verify doesn't use anchored regex "
        "`re.search(rf'^{module_pattern}\\s', ...)`. Without "
        "anchoring, `vfio_pci` substring-matches inside "
        "`vfio_pci_core` and the endpoint lies about success."
    )


def test_verify_does_not_use_naive_substring_in_check():
    """The buggy `module_pattern in verify_result.stdout.lower()`
    line must be GONE. If it sneaks back the kernel-6.8 +
    pds_vfio_pci false-success scenario recurs."""
    body = _load_modules_body()
    bad_pattern = re.search(
        r"if\s+module_pattern\s+in\s+verify_result\.stdout\.lower\(\):",
        body,
    )
    assert not bad_pattern, (
        "The buggy substring check `module_pattern in "
        "verify_result.stdout.lower()` is still present. v0.5.42 "
        "fix wasn't applied — kernel 6.8+ hosts with pds_vfio_pci "
        "(or any module containing the substring) will continue "
        "to get false 'loaded' reports."
    )


def test_verify_failure_message_includes_related_lsmod_entries():
    """When the anchored verify fails, the error message must
    enumerate the related lsmod entries so the operator can
    diagnose from the admin console without SSHing. Match the
    `related_lines` / `related_summary` pattern."""
    body = _load_modules_body()
    # The improved error path must build a related-modules summary.
    assert "related_lines" in body or "related_summary" in body or \
           "Related lsmod" in body, (
        "Verify-failed error message doesn't enumerate related "
        "lsmod entries. Operator can't tell from the admin toast "
        "WHY the module didn't load."
    )


def test_verify_failure_message_includes_modprobe_stderr():
    """The error must surface the modprobe stderr (or note it's
    empty) so kernel-side reasons (signature failure, blacklist,
    etc.) are visible to the operator."""
    body = _load_modules_body()
    # Locate the verify-failed error_msg construction.
    err_m = re.search(
        r"error_msg\s*=\s*\([\s\S]+?related_summary[\s\S]+?\)",
        body,
    )
    if not err_m:
        # Match the f-string form instead.
        err_m = re.search(
            r'f"modprobe rc=0 but[\s\S]+?stderr[\s\S]+?"',
            body,
        )
    assert err_m, (
        "Verify-failed error_msg construction not located — can't "
        "verify it includes stderr"
    )
    err_block = err_m.group(0)
    assert "stderr" in err_block.lower(), (
        "Error message doesn't include modprobe stderr — kernel "
        "rejection reasons (signing, blacklist) won't reach the "
        "operator."
    )


def test_skip_check_still_uses_anchored_regex():
    """Anti-regression: the v0.5.42 fix must NOT change the
    skip-already-loaded check above. That check has always used
    the anchored regex; if it accidentally regressed to substring
    we'd over-eagerly mark vfio_pci as 'already loaded' (skip
    modprobe) and never attempt a fresh load."""
    body = _load_modules_body()
    skip_m = re.search(
        r"#\s*Check if module is already loaded[\s\S]+?if\s+re\.search\([^)]+?\):"
        r"[\s\S]+?continue",
        body,
    )
    assert skip_m, "Skip-already-loaded check not found"
    assert re.search(
        r"re\.search\(\s*rf?['\"]\^\{module_pattern\}\\s['\"]",
        skip_m.group(0),
    ), (
        "Skip-already-loaded check no longer uses the anchored "
        "regex. Would over-skip modprobe based on substring."
    )


def test_pyproject_version_at_least_0542():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 42), (
        f"Version {m.group(1)} < 0.5.42"
    )
