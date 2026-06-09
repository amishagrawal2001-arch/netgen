"""v0.5.52 — audit H1 + H2 fixes.

H1: /api/dpdk/verify used `'vfio' in output` substring match for
the lsmod-based module-loaded check. Same bug class as v0.5.42
which fixed it in /api/dpdk/load_modules. On srv06 with kernel
6.8's vfio-pci split (where vfio_pci_core / pds_vfio_pci are
loaded but bare vfio is NOT), the substring match returned True
and the endpoint reported `kernel_modules: true`. Diagnostics
would skip the module-load step → operator stuck.

H2: /api/dpdk/hugepages persisted the mount in /etc/fstab with
an idempotency check that used OR:

  if mount_point not in existing or "hugetlbfs" not in existing:
      append

On a host with systemd's default `dev-hugepages.mount` already
writing `none /dev/hugepages hugetlbfs ...` to /proc/mounts (and
sometimes fstab), the first clause is True (our /mnt/huge isn't
present) so we append — but the second clause is also False
(hugetlbfs IS present). Result: duplicate /mnt/huge hugetlbfs
entries pile up on every call.

Correct: per-line AND check — match a line that contains BOTH the
mount point AND hugetlbfs.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _verify_body() -> str:
    src = _src()
    m = re.search(
        r"def dpdk_verify\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m, "dpdk_verify() handler not located"
    return m.group(0)


def _hugepages_body() -> str:
    src = _src()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m, "dpdk_hugepages() handler not located"
    return m.group(0)


def _strip_comments(body: str) -> str:
    """Drop `# ...` portions of each line so commented-out
    pre-fix patterns (which we DOC in the fix comments) don't
    trigger forbidden-pattern checks."""
    out = []
    for ln in body.splitlines():
        # Find first # not inside a string. Simple heuristic: skip
        # if the # appears before any unbalanced quote.
        # Good enough for our hand-written code.
        idx = ln.find("#")
        if idx >= 0:
            # Check that # isn't inside a string literal — count
            # quotes before it.
            before = ln[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                ln = before
        out.append(ln)
    return "\n".join(out)


def test_verify_no_longer_uses_substring_for_vfio():
    """The bare `"vfio" in output` substring check from pre-fix
    code must be GONE from EXECUTABLE code (we DOC the old broken
    pattern in the fix comment block — that's intentional, not a
    regression)."""
    body = _strip_comments(_verify_body())
    forbidden = re.findall(
        r'["\']vfio["\']\s+in\s+\w+',
        body,
    )
    assert not forbidden, (
        f"verify endpoint still uses substring `\"vfio\" in ...` "
        f"in executable code ({len(forbidden)} occurrences). Will "
        f"match vfio_pci_core and pds_vfio_pci even when bare "
        f"vfio isn't loaded."
    )
    forbidden_uio = re.findall(
        r'["\']uio["\']\s+in\s+\w+',
        body,
    )
    assert not forbidden_uio, (
        f"verify endpoint still uses substring `\"uio\" in ...` "
        f"in executable code ({len(forbidden_uio)} occurrences)."
    )


def test_verify_uses_anchored_regex_per_module():
    """The replacement must iterate the explicit module list
    (vfio, vfio_pci, uio_pci_generic) with anchored regex."""
    body = _verify_body()
    # The new code uses re.search(rf'^{_mod}\s', ...). In Python
    # source the `\s` is a single literal backslash + s. To match
    # that in our test pattern, regex needs `\\s` (one literal
    # backslash + s), which in a Python raw string is `r"\\s"`.
    assert re.search(
        r"re\.search\(\s*rf?['\"]\^\{[\w_]+\}\\s['\"]",
        body,
    ), (
        "verify endpoint doesn't use anchored regex "
        "`re.search(rf'^{module}\\s', ...)`. Would still false-"
        "positive on substring matches."
    )
    # Module list must include vfio AND vfio_pci AND
    # uio_pci_generic explicitly.
    assert "vfio_pci" in body and "uio_pci_generic" in body, (
        "verify endpoint's module list doesn't cover the full set."
    )


def test_verify_reports_only_detected_modules():
    """The follow-up `messages.append("DPDK kernel modules loaded:
    ...")` must use the precise per-module detection list, not
    re-do the substring check that lied in the first place."""
    body = _verify_body()
    # The `loaded_list` variable from pre-fix code did substring
    # checks twice. The fix uses the `_detected_modules` (or
    # equivalent) list built during the regex scan above.
    # Concretely: no `loaded_list = []` followed by `if "vfio" in
    # result.stdout.lower()`.
    assert not re.search(
        r"loaded_list\s*=\s*\[\][\s\S]+?[\"']vfio[\"']\s+in\s+result\.stdout",
        body,
    ), (
        "verify endpoint still builds `loaded_list` via the same "
        "broken substring check. Anchored regex must drive both "
        "the kernel_modules flag AND the message."
    )


def test_fstab_idempotency_uses_per_line_and_not_top_level_or():
    """The pre-fix check was:
        if mount_point not in existing or "hugetlbfs" not in existing:
            append
    That OR causes duplicate appends on hosts with a different
    hugetlbfs entry already (systemd's dev-hugepages.mount writes
    /dev/hugepages hugetlbfs ...). The fix walks lines and ANDs
    the two conditions per-line."""
    body = _hugepages_body()
    # Forbidden: `mount_point not in existing or "hugetlbfs" not
    # in existing` at the same nesting level (the OR is the
    # specific bug).
    forbidden = re.search(
        r"mount_point\s+not\s+in\s+existing\s*\n\s*or\s+[\"']hugetlbfs[\"']\s+not\s+in\s+existing",
        body,
    )
    assert not forbidden, (
        "fstab idempotency still uses the broken OR pattern. "
        "Will double-append on hosts with a different hugetlbfs "
        "entry already in fstab."
    )
    # The fix uses an `already_persisted = False` flag walked
    # line-by-line.
    assert re.search(
        r"already_persisted\s*=\s*False",
        body,
    ), (
        "fstab idempotency check doesn't use the new per-line "
        "`already_persisted` flag pattern."
    )
    # And the AND condition on the same line: mount_point in ln
    # AND "hugetlbfs" in ln.
    assert re.search(
        r"mount_point\s+in\s+ln[\s\S]{0,80}?and[\s\S]{0,40}?[\"']hugetlbfs[\"']\s+in\s+ln",
        body,
    ), (
        "Per-line AND match (mount_point AND hugetlbfs in same "
        "line) missing — fix wouldn't be applied."
    )


def test_fstab_skips_comment_lines():
    """The per-line walk must skip comment lines (lines starting
    with `#`). Otherwise an old netgen marker comment containing
    the mount point would be matched as the entry and we'd skip
    appending the actual mount line."""
    body = _hugepages_body()
    assert re.search(
        r"ln\.lstrip\(\)\.startswith\([\"']\#[\"']\)",
        body,
    ) or re.search(
        r"startswith\([\"']\#[\"']\)",
        body,
    ), (
        "Per-line walk doesn't skip comment lines — would "
        "false-positive on stale marker comments."
    )


def test_fstab_writes_atomic_trailing_newline():
    """If the existing fstab doesn't end with a newline, our
    `\\n# netgen-server ...` comment would glue to the last line.
    Pre-fix this happened on any hand-edited fstab. Fix prepends
    a newline before our header in that case."""
    body = _hugepages_body()
    assert re.search(
        r"existing\.endswith\([\"']\\\\n[\"']\)|existing\.endswith\([\"']\\n[\"']\)",
        body,
    ), (
        "fstab append doesn't check that existing ends with "
        "newline — could glue our marker comment to the last "
        "non-newline-terminated line."
    )


def test_pyproject_version_at_least_0552():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 52), (
        f"Version {m.group(1)} < 0.5.52"
    )
