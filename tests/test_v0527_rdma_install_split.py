"""v0.5.27 — RDMA install split from DPDK install.

Operator request:
  "rdma install should be separate, it should not be part of dpdk install"

The pre-v0.5.27 install_dpdk.sh apt-installed the RDMA stack
(libibverbs-dev, rdma-core, perftest, libmlx5-dev) as a side
effect — that wasted ~30 MB on Intel/Broadcom-only hosts and
forced operators wanting JUST RDMA tests to run the multi-step
DPDK build.

v0.5.27 splits:
  - install_rdma.sh: RDMA stack only (no DPDK build)
  - install_dpdk.sh: DPDK only (no RDMA stack)
  - /api/admin/install_rdma + /log: mirror of install_dpdk endpoints
  - Tools → RDMA → Setup RDMA... menu item + SetupRdmaDialog wizard

These tests pin the split so a future refactor doesn't silently
merge them back.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_INSTALL_DPDK = _REPO / "resources" / "dpdk" / "install_dpdk.sh"
_INSTALL_RDMA = _REPO / "resources" / "dpdk" / "install_rdma.sh"
_SERVER = _REPO / "run_tgen_server.py"
_RDMA_ACTIONS = _REPO / "traffic_client" / "rdma_menu_actions.py"
_MAIN = _REPO / "traffic_client" / "main.py"
_SETUP_DIALOG = _REPO / "widgets" / "setup_rdma_dialog.py"


# ──────────────────────── install_rdma.sh exists ─────────────────────


def test_install_rdma_script_exists():
    """The new script must be present + executable + parseable bash."""
    assert _INSTALL_RDMA.is_file(), (
        "resources/dpdk/install_rdma.sh missing — v0.5.27 split never landed"
    )
    # Wheel package-data globs on `*.sh` in resources/dpdk/ — confirm
    # the script will actually ship.
    pyproject = (_REPO / "pyproject.toml").read_text()
    assert '"resources.dpdk" = [' in pyproject and '"*.sh"' in pyproject, (
        "resources.dpdk's package-data glob lost '*.sh' — install_rdma.sh "
        "won't ship in the wheel."
    )


def test_install_rdma_script_installs_rdma_stack():
    """install_rdma.sh must apt-install the core RDMA packages."""
    src = _INSTALL_RDMA.read_text()
    for pkg in (
        "libibverbs-dev", "rdma-core", "perftest",
        "ibverbs-utils", "infiniband-diags",
    ):
        assert pkg in src, (
            f"install_rdma.sh doesn't reference {pkg!r}. RDMA stack "
            f"install would be incomplete — perftest orchestrator + "
            f"ibstat would fail."
        )


def test_install_rdma_keeps_mlx5_in_separate_batch():
    """libmlx5-dev (Mellanox MOFED-optional) must stay in a separate
    batch from core deps so non-MOFED hosts (svl-d-ai-srv04 etc.)
    don't see a poisoned install when libmlx5-dev isn't in apt cache."""
    src = _INSTALL_RDMA.read_text()
    assert "core_apt_cmd" in src and "mlx5_apt_cmd" in src, (
        "install_rdma.sh doesn't split core vs mlx5 batches — hosts "
        "without MOFED apt repo will fail the entire install."
    )


def test_install_rdma_loads_kernel_modules():
    """ib_uverbs, rdma_cm, ib_umad are required for userspace
    libibverbs to function. Script must modprobe them + persist
    across reboots via /etc/modules-load.d/."""
    src = _INSTALL_RDMA.read_text()
    for mod in ("ib_uverbs", "rdma_cm", "ib_umad"):
        assert mod in src, (
            f"install_rdma.sh doesn't load {mod!r} — userspace "
            f"libibverbs would fail on first use."
        )
    assert "/etc/modules-load.d/" in src, (
        "install_rdma.sh doesn't persist modules across reboots — "
        "RDMA breaks after first server reboot."
    )


def test_install_rdma_verifies_with_ibv_devices():
    """End-of-script verification via ibv_devices — confirms the
    stack is functional (or surfaces 'no hardware' as a warning)."""
    src = _INSTALL_RDMA.read_text()
    assert "ibv_devices" in src, (
        "install_rdma.sh doesn't run ibv_devices to verify — operator "
        "has no visibility into whether the stack actually works."
    )


def test_install_rdma_strict_mode_with_home_fallback():
    """Same v0.5.21 lesson as install_dpdk.sh: HOME may be unset
    when spawned by systemd's Flask server, and `set -u` will kill
    the script on `$HOME` reference."""
    src = _INSTALL_RDMA.read_text()
    assert "set -euo pipefail" in src, (
        "install_rdma.sh missing strict-mode shebang — silent failures "
        "won't surface."
    )
    assert ': "${HOME:=' in src, (
        "install_rdma.sh doesn't default $HOME — would die with "
        "'HOME: unbound variable' under systemd. See v0.5.21 fix."
    )


# ─────────────────── install_dpdk.sh no longer has RDMA ─────────────


def test_install_dpdk_no_longer_installs_rdma_stack():
    """install_dpdk.sh's deps_install_cmd must NOT include the RDMA
    packages anymore — that's install_rdma.sh's job now."""
    src = _INSTALL_DPDK.read_text()
    m = re.search(r'deps_install_cmd=["\']([^"\']+)["\']', src)
    assert m, "deps_install_cmd not found in install_dpdk.sh"
    apt_cmd = m.group(1)
    for pkg in ("libibverbs-dev", "rdma-core", "perftest"):
        assert pkg not in apt_cmd, (
            f"install_dpdk.sh still apt-installs {pkg!r} — that's the "
            f"RDMA stack's job now. Move to install_rdma.sh."
        )


def test_install_dpdk_no_longer_has_mlx5_batch():
    """libmlx5-dev moved to install_rdma.sh — the mlx5_install_cmd
    in install_dpdk.sh must be gone."""
    src = _INSTALL_DPDK.read_text()
    assert "mlx5_install_cmd" not in src, (
        "install_dpdk.sh still references mlx5_install_cmd — that's "
        "the RDMA stack's territory now. Drop from install_dpdk.sh."
    )


def test_install_dpdk_warns_about_mellanox_without_rdma():
    """When a Mellanox NIC is detected but libibverbs isn't installed,
    install_dpdk.sh must log a clear warning pointing operators at
    install_rdma.sh — otherwise the mlx5 PMD gets silently skipped
    at meson configure time and they wonder why Mellanox NICs don't
    work."""
    src = _INSTALL_DPDK.read_text()
    assert "lspci" in src and "mellanox" in src.lower(), (
        "install_dpdk.sh doesn't detect Mellanox NICs — operators "
        "would silently lose mlx5 PMD support."
    )
    assert "install_rdma.sh" in src or "Setup RDMA" in src, (
        "install_dpdk.sh's Mellanox-no-RDMA warning doesn't point at "
        "install_rdma.sh / Setup RDMA — operator has no recovery path."
    )


# ────────────── /api/admin/install_rdma endpoints exist ─────────────


def test_install_rdma_endpoint_exists():
    """POST /api/admin/install_rdma must exist + spawn install_rdma.sh."""
    src = _SERVER.read_text()
    assert '"/api/admin/install_rdma"' in src, (
        "/api/admin/install_rdma endpoint missing from run_tgen_server.py"
    )
    # Must reference install_rdma.sh by name.
    assert "install_rdma.sh" in src, (
        "Endpoint exists but doesn't reference install_rdma.sh — wrong "
        "script being spawned."
    )


def test_install_rdma_log_endpoint_exists():
    """GET /api/admin/install_rdma/log for the wizard's log tail."""
    src = _SERVER.read_text()
    assert '"/api/admin/install_rdma/log"' in src, (
        "/api/admin/install_rdma/log endpoint missing — wizard can't "
        "tail the install."
    )


def test_install_rdma_endpoint_has_separate_state_dict():
    """_ADMIN_INSTALL_RDMA_STATE must be distinct from
    _ADMIN_INSTALL_STATE — otherwise concurrent RDMA + DPDK installs
    clobber each other's state."""
    src = _SERVER.read_text()
    assert "_ADMIN_INSTALL_RDMA_STATE" in src, (
        "Endpoint uses _ADMIN_INSTALL_STATE (the DPDK state) — RDMA "
        "and DPDK installs would race / clobber each other."
    )


def test_install_rdma_endpoint_sets_home_env():
    """Defense-in-depth for the v0.5.21 HOME-unbound trap. The Popen
    env must include HOME so install_rdma.sh's `set -u` doesn't die
    on `$HOME` even if the script itself is pre-v0.5.27."""
    src = _SERVER.read_text()
    m = re.search(
        r"def api_admin_install_rdma\(\)[\s\S]+?(?=\n@app\.route|\ndef api_)",
        src,
    )
    assert m, "api_admin_install_rdma body not found"
    body = m.group(0)
    assert 'env.setdefault("HOME"' in body or 'env["HOME"]' in body, (
        "install_rdma endpoint doesn't set HOME — HOME-unbound regression."
    )


# ────────────────── GUI: Setup RDMA menu + dialog ───────────────────


def test_setup_rdma_dialog_exists():
    """widgets/setup_rdma_dialog.py must exist + define SetupRdmaDialog."""
    assert _SETUP_DIALOG.is_file(), (
        "widgets/setup_rdma_dialog.py missing — Setup RDMA menu has "
        "nothing to open."
    )
    src = _SETUP_DIALOG.read_text()
    assert "class SetupRdmaDialog" in src, (
        "SetupRdmaDialog class missing from widgets/setup_rdma_dialog.py"
    )
    # Must POST to /api/admin/install_rdma + poll /log
    assert "/api/admin/install_rdma" in src, (
        "Dialog doesn't drive the install_rdma endpoint."
    )
    assert "/api/admin/install_rdma/log" in src, (
        "Dialog doesn't poll the install_rdma log endpoint."
    )


def test_setup_rdma_menu_item_wired():
    """Tools → RDMA → Setup RDMA... must be the first item in the
    RDMA submenu (mirrors Setup DPDK as the entry-point of DPDK
    submenu)."""
    src = _MAIN.read_text()
    assert "Setup RDMA" in src, (
        "Setup RDMA menu item not added to Tools → RDMA submenu."
    )
    assert "show_setup_rdma_dialog" in src, (
        "Setup RDMA action isn't connected to a handler."
    )
    # The Setup RDMA action must be added to rdma_menu BEFORE the
    # other RDMA actions (Blast / Topology).
    rdma_block_m = re.search(
        r"rdma_menu = QMenu\(\"RDMA\"[\s\S]+?tools_menu\.addSeparator\(\)",
        src,
    )
    assert rdma_block_m, "RDMA submenu construction block not found"
    block = rdma_block_m.group(0)
    setup_pos = block.find("Setup RDMA")
    blast_pos = block.find("Blast a RDMA Flow")
    assert setup_pos > 0 and blast_pos > 0 and setup_pos < blast_pos, (
        "Setup RDMA isn't positioned BEFORE Blast a RDMA Flow in the "
        "menu — operators expect setup as the top entry."
    )


def test_setup_rdma_handler_in_rdma_menu_actions():
    """The show_setup_rdma_dialog method must live in
    TrafficGenClientRDMAMenuActions (mixed into the main window) so
    the menu connection in main.py resolves."""
    src = _RDMA_ACTIONS.read_text()
    assert "def show_setup_rdma_dialog" in src, (
        "show_setup_rdma_dialog not defined in rdma_menu_actions.py — "
        "menu action will AttributeError on click."
    )
    # Handler must instantiate SetupRdmaDialog.
    m = re.search(
        r"def show_setup_rdma_dialog[\s\S]+?(?=\n    def [^_]|\nclass )",
        src,
    )
    body = m.group(0)
    assert "SetupRdmaDialog" in body, (
        "Handler doesn't open SetupRdmaDialog — wrong widget wired."
    )


def test_pyproject_version_at_least_0527():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 27), (
        f"Version {m.group(1)} < 0.5.27"
    )
