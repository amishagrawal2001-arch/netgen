"""v0.5.62 — install_rdma.sh refreshes modules-load file when the
module set has changed since the last install.

Audit finding M9.

Pre-fix:

    if [[ ! -f "$modules_load_file" ]]; then
        # write the canonical list
    else
        log_info "...already exists; leaving unchanged"
    fi

The skip-when-exists check NEVER refreshed an old file. v0.5.28
added `rdma_ucm` + `iw_cm` to the `rdma_modules` array. Hosts
upgraded from v0.5.27 still had only the old three modules in
their boot-time list. Step 2's modprobe loop loaded everything
for the current session, but on next reboot rdma_ucm-needing
tools failed.

Fix: compare desired vs current content; rewrite on mismatch.
"""
from __future__ import annotations

import re
from pathlib import Path


_SHELL = (
    Path(__file__).resolve().parents[1]
    / "resources" / "dpdk" / "install_rdma.sh"
)


def _src() -> str:
    return _SHELL.read_text()


def test_modules_load_compares_content_before_skipping():
    """The skip-when-exists check must be replaced by a content
    compare. The pre-fix `if [[ ! -f ... ]]` form must be gone."""
    s = _src()
    # Find the modules_load_file block.
    block = re.search(
        r"modules_load_file=[\s\S]+?(?=^# Step 3|^log_step)",
        s,
        re.MULTILINE,
    )
    assert block, "modules_load block not located"
    body = block.group(0)
    # Pre-fix branch text must be gone.
    assert "leaving unchanged" not in body, (
        "Pre-fix skip-message 'leaving unchanged' still present — "
        "module set updates would still be missed on re-run"
    )
    # And the rewrite is gated on a content comparison.
    assert re.search(
        r'desired_content[\s\S]{0,200}?current_content',
        body,
    ) or re.search(
        r'"\$desired_content"\s*!=\s*"\$current_content"',
        body,
    ), (
        "No content-compare gate — won't rewrite on module-set "
        "changes"
    )


def test_module_set_includes_rdma_ucm_and_iw_cm():
    """v0.5.28 added rdma_ucm + iw_cm; the array must still
    include them so the refresh writes the correct content."""
    s = _src()
    m = re.search(r"rdma_modules=\(([^)]+)\)", s)
    assert m, "rdma_modules array not found"
    arr = m.group(1)
    for mod in ("ib_uverbs", "rdma_cm", "rdma_ucm", "ib_umad", "iw_cm"):
        assert mod in arr, (
            f"rdma_modules missing {mod!r} — refresh would write "
            f"an incomplete set"
        )


def test_pyproject_version_at_least_0562():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 62), (
        f"Version {m.group(1)} < 0.5.62"
    )
