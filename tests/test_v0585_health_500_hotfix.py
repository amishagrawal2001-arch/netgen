"""v0.5.85 hot-fix: /api/admin/health 500 caused by v0.5.84 mounts
list mixed into the disk dict.

Operator on srv06 (Jun 10 2026):

  "missing system info, and also move the system info on the
   top , also seeing Loading... on top."

Root cause discovered via the v0.5.80 `/api/admin/journal`
endpoint (eat your own dogfood):

    File ".../run_tgen_server.py", line 16821, in api_admin_health
    TypeError: list indices must be integers or slices, not str

The v0.5.84 mounts-enumeration code added `disk["mounts"] = [...]`
(a list) but the disk-warnings loop iterates ALL of disk.items()
and does `_info["free_mb"]` on each. For mounts (a list), that
raised TypeError, the whole /api/admin/health 500'd, and the
admin console JS:

  * hostname stayed as "Loading…"
  * System Info card stayed at "…" placeholders
  * All pills (DPDK/RDMA/IOMMU/etc.) stayed at "…"

Fix: skip non-dict entries in the warnings loop. The mounts list
has its own per-row used_pct that's surfaced via the rendered
disks table.

Also moves the System Info card to the top of the grid (operator-
requested in the same message).
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def test_disk_warning_loop_skips_non_dict_entries():
    """The warnings loop must check isinstance(_info, dict) before
    doing _info['free_mb'] — otherwise it crashes on the mounts
    list (v0.5.84 regression)."""
    src = _src()
    # Find the disk warnings loop.
    m = re.search(
        r"_disk_issues = \[\][\s\S]+?(?=\n\n[ ]{4}[a-zA-Z]|\nout\[)",
        src,
    )
    assert m, "Couldn't find _disk_issues loop"
    body = m.group(0)
    # The fix: isinstance dict check.
    assert "isinstance(_info, dict)" in body, (
        "Disk warnings loop still iterates all of disk.items() "
        "without checking isinstance — v0.5.84 500 will recur"
    )
    # And the bare-not check is gone (or replaced).
    assert "if not _info:" not in body or "isinstance" in body


def test_system_info_card_is_first_in_grid():
    """Operator: 'move the system info on the top'. The System
    Info card must appear before DPDK Runtime in the grid."""
    src = _src()
    sys_idx = src.find('id="card-system-info"')
    dpdk_idx = src.find('<h2>DPDK Runtime</h2>')
    assert sys_idx > 0 and dpdk_idx > 0
    assert sys_idx < dpdk_idx, (
        "System Info card no longer appears before DPDK Runtime — "
        "operator-requested order broken"
    )


def test_system_info_card_appears_only_once():
    """The card was moved from the middle of the grid to the top;
    make sure we didn't leave the original copy behind."""
    src = _src()
    assert src.count('id="card-system-info"') == 1, (
        "card-system-info element ID is duplicated — move missed "
        "removing the original"
    )


def test_pyproject_version_at_least_0585():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 85)
