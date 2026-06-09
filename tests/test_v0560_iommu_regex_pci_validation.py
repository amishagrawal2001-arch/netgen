"""v0.5.60 — /api/dpdk/iommu word-boundary regex + cpu_vendor
allowlist + /api/dpdk/bind+unbind strict PCI BDF validation.

Audit findings M3 + M4.

M3: /api/dpdk/iommu used `iommu_param not in current_cmdline`
substring check. On a kernel cmdline already containing
`intel_iommu=on,igfx_off` (the comma-flags form), the check
matched as "present" but the boundary was wrong. On a cmdline
with a stray substring (typo), we'd append a duplicate. Plus
cpu_vendor was a free string → "intl" silently picked Intel
on an AMD box.

M4: /api/dpdk/bind + unbind only checked `":" in pci`. A value
like `0000:01:00.0; rm -rf /` passes the check. We use
subprocess.run with a list (not shell=True) so shell injection
is impossible, but the value still poisons dpdk_bind.sh's
internal parsing → confusing downstream errors. Strict BDF
regex closes the door.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_iommu_uses_word_boundary_regex():
    """The `iommu_param` presence check must use word boundaries,
    not bare substring."""
    src = _src()
    # The dpdk_iommu handler.
    m = re.search(
        r"def dpdk_configure_iommu\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    # Forbidden pattern: `iommu_param not in current_cmdline`.
    assert not re.search(
        r"iommu_param\s+not\s+in\s+current_cmdline",
        body,
    ), (
        "/api/dpdk/iommu still uses substring `iommu_param not in "
        "current_cmdline` — duplicates accumulate."
    )
    # Required: word-boundary regex.
    assert re.search(
        r"re\.search\([\s\S]{0,80}?\\b[\s\S]{0,30}?iommu_param",
        body,
    ) or re.search(
        r"re\.escape\(iommu_param\)",
        body,
    ), (
        "/api/dpdk/iommu doesn't use \\b-anchored regex against "
        "iommu_param — substring false-positives still possible."
    )


def test_iommu_pt_check_uses_word_boundary():
    """The 'is iommu=pt already present' check must also be
    word-boundary anchored."""
    src = _src()
    m = re.search(
        r"def dpdk_configure_iommu\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # Look for `re.search(r'\biommu=pt\b'`.
    assert re.search(
        r"re\.search\(r['\"]\\biommu=pt\\b['\"]",
        body,
    ), (
        "iommu=pt presence check not word-boundary anchored"
    )


def test_cpu_vendor_allowlisted():
    """cpu_vendor must be one of 'intel' / 'amd'. Anything else
    rejects with 400 instead of silently picking Intel."""
    src = _src()
    m = re.search(
        r"def dpdk_configure_iommu\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert re.search(
        r"cpu_vendor\s+not\s+in\s+\(\s*[\"']intel[\"']\s*,\s*[\"']amd[\"']",
        body,
    ), (
        "cpu_vendor not validated against intel/amd allowlist — "
        "typos fall through to Intel branch and write wrong "
        "params on AMD boxes."
    )
    # And returns 400 with a clear error.
    assert re.search(
        r"Invalid\s+cpu_vendor",
        body,
    ), "Error message for bad cpu_vendor not informative"


def test_bind_validates_pci_with_bdf_regex():
    """/api/dpdk/bind must reject non-BDF PCI strings with a
    strict regex, not just `":" in pci`."""
    src = _src()
    bind_body = re.search(
        r"def dpdk_bind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    # The strict regex anchored.
    assert re.search(
        r"\[0-9a-f\]\{4\}:\[0-9a-f\]\{2\}:\[0-9a-f\]\{2\}\\\.\[0-7\]",
        bind_body,
    ), (
        "/api/dpdk/bind doesn't apply a strict BDF regex — "
        "weird strings still slip through."
    )


def test_unbind_validates_pci_with_bdf_regex():
    """Same for /api/dpdk/unbind — both endpoints must use the
    same strict regex."""
    src = _src()
    unbind_body = re.search(
        r"def dpdk_unbind\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    ).group(0)
    assert re.search(
        r"\[0-9a-f\]\{4\}:\[0-9a-f\]\{2\}:\[0-9a-f\]\{2\}\\\.\[0-7\]",
        unbind_body,
    ), (
        "/api/dpdk/unbind doesn't apply strict BDF regex — "
        "asymmetric with bind."
    )


def test_pyproject_version_at_least_0560():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 60), (
        f"Version {m.group(1)} < 0.5.60"
    )
