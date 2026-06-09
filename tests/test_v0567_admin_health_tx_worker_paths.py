"""v0.5.67 — /api/admin/health probes /usr/local/bin/tx_worker.

Operator-reported on srv06 (v0.5.59) admin console:
  DPDK Runtime
  tx_worker binary   Not built

But:
  /api/dpdk/verify  → "tx_worker found: /usr/local/bin/tx_worker"
  /api/dpdk/status  → tx_worker_exists: true, tx_worker_built: ...
  /api/admin/health → tx_worker.present: false   ← lying

Pre-fix the admin health endpoint only probed:
    /opt/netgen/resources/dpdk/tx_worker/build/tx_worker
    /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker

But install_dpdk.sh's Step 6 installs the built binary to
`/usr/local/bin/tx_worker` — same path the runtime invokes.
v0.5.67 aligns the candidate list with /api/dpdk/status:
include /usr/local/bin/tx_worker first, with build dirs as
fallbacks for early-install hosts before Step 6 has run.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _admin_health_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    return m.group(0)


def test_admin_health_probes_usr_local_bin_tx_worker():
    """The candidates list must include /usr/local/bin/tx_worker
    — that's where install_dpdk.sh Step 6 installs the binary."""
    body = _admin_health_body()
    # Find the candidates list.
    m = re.search(
        r"candidates\s*=\s*\[([\s\S]+?)\]",
        body,
    )
    assert m, "candidates list not located"
    paths_block = m.group(1)
    assert "/usr/local/bin/tx_worker" in paths_block, (
        "/api/admin/health candidates missing "
        "/usr/local/bin/tx_worker — admin console reports "
        "tx_worker missing even when install_dpdk.sh ran "
        "successfully."
    )


def test_admin_health_orders_usr_local_bin_first():
    """`/usr/local/bin/tx_worker` should come FIRST — that's the
    actual install target. Build-dir paths are stale-copies that
    operators sometimes overwrite during install_dpdk.sh re-runs."""
    body = _admin_health_body()
    m = re.search(
        r"candidates\s*=\s*\[([\s\S]+?)\]",
        body,
    )
    paths = m.group(1)
    usr_local_idx = paths.find("/usr/local/bin/tx_worker")
    netgen_build_idx = paths.find("/opt/netgen/resources/dpdk/tx_worker/build")
    assert usr_local_idx >= 0, "missing /usr/local/bin/tx_worker"
    if netgen_build_idx >= 0:
        assert usr_local_idx < netgen_build_idx, (
            "candidate order puts build dir before install target "
            "— may pick up stale build artifacts."
        )


def test_admin_health_still_includes_legacy_paths():
    """Don't drop the existing paths — early-install hosts where
    Step 6 hasn't run yet still need a fallback. And /opt/OSTG/
    is the pre-v0.5 compat symlink."""
    body = _admin_health_body()
    m = re.search(
        r"candidates\s*=\s*\[([\s\S]+?)\]",
        body,
    )
    paths = m.group(1)
    assert "/opt/netgen" in paths, (
        "Lost the /opt/netgen/ build-dir fallback"
    )
    assert "/opt/OSTG" in paths, (
        "Lost the /opt/OSTG/ legacy path"
    )


def test_pyproject_version_at_least_0567():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 67), (
        f"Version {m.group(1)} < 0.5.67"
    )
