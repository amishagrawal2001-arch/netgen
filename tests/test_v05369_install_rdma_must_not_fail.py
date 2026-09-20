"""v0.5.369 — install_rdma.sh must never exit non-zero.

Operator on svl-d-ai-srv04 2026-09-20 clicked "Install RDMA…" on
/admin. The Mellanox DOCA apt repo the host had configured was
broken:

  1) GPG signing key `DC726C5E41B9CC50` no longer available
     (`NO_PUBKEY` error on `apt-get update`).
  2) `Release` file still advertises package version `2601.0.7-1`
     for a stack of aux packages (ibverbs-utils, python3-pyverbs,
     mstflint, opensm, etc.) but the actual `.deb` files 404.

`install_rdma.sh` bailed at Step 1 (`apt install`) with `exit 2`
even though `rdma-core` — the ONLY package that owns the RDMA
kernel modules (ib_uverbs, ib_umad, rdma_cm, rdma_ucm, iw_cm) —
was already installed at that version. Step 2 (modprobe) never
ran; admin console reported "install failed".

Operator directive: "make sure to install should not fail for any
reason".

### Fix (v0.5.369)

Step 1 becomes three-tier + tolerant:

  a) Full core install with `--fix-missing` — 404 fetches get
     skipped instead of aborting.
  b) Per-package retry for the essentials only (rdma-core,
     libibverbs-dev, librdmacm-dev, perftest, rdmacm-utils).
  c) Post-condition via `dpkg-query -W -f='${Status}' rdma-core`.
     If rdma-core is installed, Step 1 succeeds; aux-package
     failures become warnings only.

Step 2 gets a "N/M modules loaded" summary. Step 4's `exit 3`
(when `ibv_devices` missing) is downgraded to a warning — the
kernel modules Step 2 loaded ARE the RDMA plane; ibv_devices is
a diagnostic, not the essential path. mlx5 install is wrapped in
`set +e` so an optional Mellanox userspace lib failure can't
abort. All resilient sections toggle `set +e`/`set -e` so bash's
strict `pipefail` doesn't turn one 404 into an immediate exit.

Both fix sites carry the marker
`v0.5.369 (audit rdma-install-must-not-fail)`.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "resources" / "dpdk" / "install_rdma.sh"


def _src() -> str:
    return _SCRIPT.read_text()


# ─── marker + structural ───


def test_marker_present():
    """v0.5.369 marker must appear on every resilience site — the
    apt-update parse, the tiered install, the mlx5 wrap, the Step
    4 downgrade, the summary banner. At least 5 hits."""
    src = _src()
    assert src.count("v0.5.369 (audit rdma-install-must-not-fail)") >= 5


def test_bash_syntax_valid():
    """`bash -n` must accept the script — malformed `set +e`
    blocks or unclosed if/fi would break the install invocation."""
    import subprocess
    r = subprocess.run(
        ["bash", "-n", str(_SCRIPT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"bash -n failed:\n{r.stderr}"
    )


# ─── the critical property: no failure-path `exit`s ───


def test_no_exit_2_or_exit_3_survived():
    """Pre-fix Step 1 exited 2 on apt-install failure; Step 4
    exited 3 on missing ibv_devices. Both were the direct cause
    of the srv04 breakage. Neither may remain as an actual exit
    statement — only text references inside comments are OK."""
    src = _src()
    # Filter comment lines out before checking for exit 2/3.
    non_comment = "\n".join(
        _l for _l in src.splitlines()
        if not _l.lstrip().startswith("#")
    )
    assert re.search(r"\bexit\s+2\b", non_comment) is None, (
        "install_rdma.sh still has `exit 2` outside comments — "
        "srv04 regression risk"
    )
    assert re.search(r"\bexit\s+3\b", non_comment) is None, (
        "install_rdma.sh still has `exit 3` outside comments — "
        "srv04 regression risk"
    )


def test_only_structural_pre_flight_exits_remain():
    """The only allowed non-zero exits are pre-flight environment
    checks: (a) non-root, (b) missing apt-get. Everything past
    Step 1 must complete or warn — never abort."""
    src = _src()
    exit_lines = [
        (i + 1, line)
        for i, line in enumerate(src.splitlines())
        if re.search(r"^\s*exit\s+\d", line)
        and not line.lstrip().startswith("#")
    ]
    # Expected: exit 0 in --help, exit 1 non-root, exit 1 no apt,
    # exit 0 at end of script.
    assert len(exit_lines) == 4, (
        f"Expected exactly 4 top-level exit statements "
        f"(help, non-root, no-apt, EOF); got {len(exit_lines)}: "
        f"{exit_lines}"
    )
    # None of them is exit 2 or exit 3.
    for _, line in exit_lines:
        assert "exit 2" not in line
        assert "exit 3" not in line


# ─── Step 1: fallback tiers ───


def test_core_install_uses_fix_missing():
    """Tier 1: `--fix-missing` on the batched install so a single
    404 doesn't abort. Pre-fix omitted the flag."""
    src = _src()
    assert "core_apt_cmd_fix" in src, (
        "Missing --fix-missing variant of core install command"
    )
    assert "--fix-missing" in src


def test_per_package_retry_covers_essentials():
    """Tier 2: per-package retry loop for the essentials. Must
    include rdma-core (the true essential) plus the librdmacm /
    libibverbs / perftest / rdmacm-utils user-tier packages."""
    src = _src()
    # Extract the retry loop body.
    m = re.search(
        r"for pkg in ([^;]+); do",
        src,
    )
    assert m, "Per-package retry loop not found"
    pkgs = m.group(1).split()
    assert "rdma-core" in pkgs, (
        "Per-package retry MUST include rdma-core (the only "
        "essential — owns the kernel modules)"
    )
    for _needed in ("libibverbs-dev", "librdmacm-dev", "perftest"):
        assert _needed in pkgs, (
            f"Per-package retry missing {_needed}"
        )


def test_dpkg_query_post_check():
    """Tier 3: post-condition check via `dpkg-query` on rdma-core.
    Only rdma-core determines whether Step 1 "succeeded" for the
    purpose of proceeding to Step 2 modprobe."""
    src = _src()
    assert re.search(
        r"dpkg-query\s+-W\s+-f='\$\{Status\}\\n'\s+rdma-core",
        src,
    ), (
        "Missing dpkg-query post-check for rdma-core"
    )
    assert "install ok installed" in src


def test_set_plus_e_around_core_install():
    """The strict `set -euo pipefail` at L38 turns any non-zero
    pipe stage into an immediate exit. The tiered install MUST
    wrap its apt calls in `set +e ... set -e` blocks so a 404
    can't abort. Structural check: at least 4 `set +e` / `set -e`
    pairs (apt-update, batched install, per-pkg loop, mlx5,
    ibv_devices)."""
    src = _src()
    plus_e = src.count("set +e")
    minus_e = src.count("set -e")
    # -e appears once at the top of file (in set -euo pipefail),
    # plus once per resilient section reset.
    assert plus_e >= 4, (
        f"Expected ≥4 `set +e` blocks, got {plus_e}"
    )
    assert minus_e >= 5, (
        f"Expected ≥5 `set -e` statements "
        f"(1 initial + 4 restores), got {minus_e}"
    )


# ─── stale-repo detection ───


def test_broken_repo_detection_grep_pattern():
    """apt-get update output is parsed for NO_PUBKEY / 404 /
    "no longer signed" so the log stream tells the operator
    exactly which external repo is broken. That was the srv04
    puzzle piece the pre-fix flow didn't surface."""
    src = _src()
    # The grep pattern must cover all three symptoms.
    assert re.search(
        r"grep\s+-qE\s+['\"]NO_PUBKEY[|]no longer signed[|]404",
        src,
    ), (
        "Missing broken-repo detection grep in Step 1 update path"
    )


def test_broken_repo_warning_includes_recovery_hint():
    """When a broken repo is detected, the log stream must tell
    the operator how to disable it — not just that it's broken.
    Pre-fix operator had to invent the recovery on the fly."""
    src = _src()
    assert "sources.list.d" in src
    assert ".list.disabled" in src, (
        "Recovery hint missing the .list.disabled rename step"
    )


# ─── Step 2 summary + Step 4 downgrade ───


def test_step2_reports_module_count():
    """Step 2 must emit "N/M loaded" so the log stream matches
    the admin console's RDMA card shape."""
    src = _src()
    assert "_mods_loaded" in src
    assert "_mods_total" in src
    assert "RDMA kernel modules:" in src
    assert re.search(
        r"modules?:\s+\$\{_mods_loaded\}/\$\{_mods_total\}\s+loaded",
        src,
    ), "Missing N/M module summary line in Step 2"


def test_step4_ibv_devices_missing_is_warning_not_exit():
    """Pre-fix Step 4 exited 3 when `ibv_devices` wasn't on PATH
    (aux package 404 on ibverbs-utils = same srv04 symptom).
    Post-fix must log_warning + continue."""
    src = _src()
    # Locate the ibv_devices check.
    m = re.search(
        r"if ! command -v ibv_devices[\s\S]{0,600}?fi",
        src,
    )
    assert m, "Step 4 ibv_devices guard not found"
    body = m.group(0)
    assert "log_warning" in body
    assert "exit 3" not in body
    assert "exit 2" not in body


def test_mlx5_install_wrapped_in_set_plus_e():
    """mlx5 install is optional and MUST not abort the script.
    Structural check that it's wrapped in set +e ... set -e."""
    src = _src()
    # Locate mlx5 section: from the "Installing Mellanox-specific"
    # info line through the "libmlx5-dev installed." success line
    # (or the log_warning fallback).
    m = re.search(
        r"Installing Mellanox-specific[\s\S]+?libmlx5-dev installed",
        src,
    )
    assert m, "mlx5 install block not found"
    body = m.group(0)
    assert "set +e" in body, (
        "mlx5 install not wrapped in set +e — a stale MOFED repo "
        "will still abort the whole script"
    )
    # And the matching set -e to restore strict mode.
    assert "set -e" in body


# ─── final banner ───


def test_final_banner_reports_module_summary():
    """Final banner reports "N/M RDMA kernel modules loaded" so
    the operator can see plane state at a glance in the log
    stream, matching the admin console's RDMA card."""
    src = _src()
    # Match the final summary in the closing log_step block.
    tail = src[src.rindex("log_step \"RDMA install complete\""):]
    assert re.search(
        r"RDMA kernel modules loaded",
        tail,
    ), "Missing module-count summary in final banner"


# ─── regression guards ───


def test_v0562_persistent_modules_load_file_intact():
    """v0.5.62's per-boot module persistence write must survive
    — regression guard because v0.5.369 also touched Step 2."""
    src = _src()
    assert "/etc/modules-load.d/netgen-rdma.conf" in src
    assert "modules_load_file" in src


def test_v0555_apt_log_capture_intact():
    """v0.5.55's tee to /tmp/rdma_deps_install.log must survive."""
    src = _src()
    assert "RDMA_APT_LOG=/tmp/rdma_deps_install.log" in src


def test_v0528_module_set_intact():
    """v0.5.28's canonical 5-module set must still be the
    modprobe list — regression guard against Step 2 rewrite
    accidentally dropping a module."""
    src = _src()
    m = re.search(
        r'rdma_modules=\(([^)]+)\)',
        src,
    )
    assert m
    modules = m.group(1).split()
    for _mod in ('"ib_uverbs"', '"rdma_cm"', '"rdma_ucm"',
                 '"ib_umad"', '"iw_cm"'):
        assert _mod in modules, (
            f"Module {_mod} dropped from rdma_modules array"
        )


def test_v0574_admin_health_still_probes_all_modules():
    """v0.5.74 F6 registered the canonical 5-module set for
    /api/admin/health probing. Not touched by v0.5.369, but
    regression guard so a future edit here + there stays in
    lockstep."""
    server = (_REPO / "run_tgen_server.py").read_text()
    for _mod in ("ib_uverbs", "rdma_cm", "rdma_ucm",
                 "ib_umad", "iw_cm"):
        assert f'"{_mod}"' in server


def test_pyproject_version_at_least_0569():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 369), (
        f"Version {m.group(1)} < 0.5.369"
    )
