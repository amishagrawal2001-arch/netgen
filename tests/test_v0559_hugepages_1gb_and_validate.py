"""v0.5.59 — /api/dpdk/hugepages accepts 1GB pages + validates
num_pages.

Audit finding M2.

Pre-fix:
  if page_size == "2MB":
      hugepage_file = "/sys/.../hugepages-2048kB/nr_hugepages"
  else:
      return jsonify({"error": "Unsupported page size"}), 400

1GB hugepages are standard for >=100 Gbps DPDK on AMD EPYC /
Intel Sapphire Rapids — the sysfs path is
`/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages`,
same shape as the 2MB path. Pre-fix the endpoint flat-rejected
1GB and operators had to set them via GRUB cmdline only (boot-
time allocation, no runtime resize).

Plus num_pages validation:
  num_pages = data.get("num_pages")
  # int(num_pages) accepts strings, negative ints, None silently.

The kernel may clamp negative values to 0 or reject with a
non-obvious errno. Validate up front.
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


def test_supported_page_sizes_include_1gb():
    """The endpoint must accept page_size='1GB' and resolve to
    the right sysfs leaf."""
    body = _hugepages_body()
    assert "1GB" in body, (
        "hugepages handler doesn't accept 1GB page size"
    )
    assert "hugepages-1048576kB" in body, (
        "hugepages handler doesn't reference the 1GB sysfs path"
    )


def test_supported_page_sizes_dict_or_branch():
    """The mapping must be a structured lookup (dict or
    explicit if-branch) so the operator-supplied page_size is
    matched against an allowlist, not blindly substituted into
    the path."""
    body = _hugepages_body()
    # The natural shape is a dict keyed by "2MB" / "1GB".
    assert re.search(
        r"[\"']2MB[\"']\s*:\s*[\"']hugepages-2048kB",
        body,
    ), "No structured 2MB → sysfs leaf mapping"
    assert re.search(
        r"[\"']1GB[\"']\s*:\s*[\"']hugepages-1048576kB",
        body,
    ), "No structured 1GB → sysfs leaf mapping"


def test_unsupported_page_size_returns_400_with_supported_list():
    """When the operator passes an invalid page_size, the error
    message must list what IS supported so they don't guess."""
    body = _hugepages_body()
    # The error response includes the supported list.
    err_block = re.search(
        r"Unsupported page size[\s\S]{0,200}?Supported:",
        body,
    )
    assert err_block, (
        "Unsupported page-size error doesn't list supported "
        "options — operator has to read source to find them."
    )


def test_num_pages_validation_rejects_negative():
    """num_pages must be validated as a non-negative int. Pre-fix
    `int(num_pages)` accepted negative values."""
    body = _hugepages_body()
    # Look for the validation block.
    assert re.search(
        r"num_pages\s*<\s*0[\s\S]{0,200}?ValueError",
        body,
    ) or re.search(
        r"if\s+num_pages\s*<\s*0[\s\S]{0,80}?raise",
        body,
    ), (
        "num_pages negative check missing — kernel may clamp to "
        "0 or return a non-obvious errno."
    )


def test_num_pages_validation_returns_400_on_bad_input():
    """Bad input gets a 400 with a clear error message."""
    body = _hugepages_body()
    # The validation block returns jsonify(...), 400 with a
    # message referencing num_pages.
    assert re.search(
        r"num_pages\s+must\s+be\s+a\s+non-negative\s+integer",
        body,
    ), (
        "No clear error message for bad num_pages input"
    )


def test_pyproject_version_at_least_0559():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 59), (
        f"Version {m.group(1)} < 0.5.59"
    )
