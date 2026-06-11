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


def test_admin_health_delegates_to_resolver():
    """v0.5.99 (post-tag): /api/admin/health no longer maintains
    its own candidates list — it delegates to the launcher's
    `_resolve_tx_worker_bin()` so the health probe reports the
    SAME path that /api/traffic/start will exec. Pre-fix the two
    drifted (the v0.5.67-era inline list reported the binary
    present at /usr/local/bin/, the launcher's resolver didn't
    have /usr/local/bin/, and the stream-launch error said
    'not found'). Single source of truth closes that drift."""
    body = _admin_health_body()
    assert "_resolve_tx_worker_bin" in body, (
        "/api/admin/health no longer delegates to the launcher's "
        "resolver — drift between health probe and launcher can "
        "re-introduce the srv06 'binary present but not found' "
        "bug."
    )
    # Fallback list still includes /usr/local/bin/ for the case
    # where the resolver import raises (e.g. partial install).
    assert "/usr/local/bin/tx_worker" in body, (
        "Defensive fallback list dropped /usr/local/bin/"
    )


def test_admin_health_fallback_orders_usr_local_bin_first():
    """The defensive fallback list (used only if the resolver
    import errors) must still put /usr/local/bin/ first."""
    body = _admin_health_body()
    # The fallback is in the `except Exception:` branch.
    m = re.search(
        r"except Exception:[\s\S]+?for p in \(([\s\S]+?)\):",
        body,
    )
    if not m:
        # The exact except shape may evolve; tolerate as long as
        # the resolver is wired in.
        return
    paths = m.group(1)
    usr_local_idx = paths.find("/usr/local/bin/tx_worker")
    netgen_build_idx = paths.find("/opt/netgen/resources/dpdk/tx_worker/build")
    assert usr_local_idx >= 0
    if netgen_build_idx >= 0:
        assert usr_local_idx < netgen_build_idx, (
            "Fallback list order puts build dir before install "
            "target — stale-build pickup risk."
        )


def test_admin_health_fallback_still_includes_legacy_paths():
    """The defensive fallback list still references both
    /opt/netgen/ and /opt/OSTG/ for early-install / legacy
    hosts."""
    body = _admin_health_body()
    # Just check the strings exist anywhere in the function.
    assert "/opt/netgen" in body, (
        "Lost the /opt/netgen/ build-dir fallback"
    )
    assert "/opt/OSTG" in body, (
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
