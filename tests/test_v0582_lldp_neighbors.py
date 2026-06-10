"""v0.5.82 — LLDP neighbor support.

Operator (Jun 10 2026):

  "also enable lldp in on the server and lldp package should be
   part tar.tgz fresh installer. also allow admin console to see
   the lldp neighbor in the network interface table"

Three changes:

  1. `scripts/tarball/netgen-install` — new `_setup_lldpd()` step
     that installs lldpd, writes `/etc/lldpd.d/netgen-server.conf`
     (TX + RX, sane intervals), enables + starts the service.
     Best-effort: failure logged + skipped (does NOT abort
     install).

  2. `resources/dpdk/install_dpdk.sh` — added `lldpd` to the
     apt-get install deps list so existing servers running
     "Make DPDK Ready" pick up lldpd too.

  3. `run_tgen_server.py`:
     - `_LLDP_CACHE` (30s TTL, threading.Lock guarded) caches
       the parsed `lldpcli show neighbors -f json` output sliced
       per-iface. Avoids forking lldpcli per-row on every
       interfaces poll.
     - `_refresh_lldp_cache()` runs lldpcli with 4s timeout,
       parses the lldpd JSON (handles both flat and wrapped
       shapes across lldpd versions), and stashes per-iface
       summaries (sys_name, chassis_id, port_id, port_descr,
       mgmt_ips).
     - `_get_lldp_neighbor(iface)` — convenience accessor.
     - `/api/dpdk/interfaces` items now include `lldp_neighbor`
       (or None when lldpd isn't installed / no neighbor).
     - Admin JS adds a "LLDP neighbor" column between "IP
       addresses" and "TX queues". Shows switch name on top,
       port descr/id + mgmt IP on the muted second line, with
       full chassis/port/sys details on hover.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"
_INSTALLER = _REPO / "scripts" / "tarball" / "netgen-install"
_DPDK_SH = _REPO / "resources" / "dpdk" / "install_dpdk.sh"


def _src() -> str:
    return _SERVER.read_text()


# ─── Installer step ───


def test_installer_has_setup_lldpd_step():
    src = _INSTALLER.read_text()
    assert "def _setup_lldpd(" in src, (
        "Installer missing _setup_lldpd step"
    )
    # Called from main() before _build_frr_image.
    m = re.search(
        r"_preflight\([^)]+\)\s*\n\s*_setup_lldpd\(\)",
        src,
    )
    assert m, "_setup_lldpd not wired into main()"


def test_installer_lldpd_step_installs_via_apt():
    src = _INSTALLER.read_text()
    body = re.search(
        r"def _setup_lldpd[\s\S]+?(?=\ndef [a-z_])",
        src,
    ).group(0)
    assert '"lldpd"' in body and "apt-get" in body
    assert "systemctl" in body and "enable" in body, (
        "lldpd setup doesn't enable the service"
    )
    # Failure is non-fatal.
    assert "check=False" in body, (
        "_setup_lldpd should be best-effort — install failure "
        "must not abort netgen-install"
    )


def test_installer_writes_lldpd_config_file():
    src = _INSTALLER.read_text()
    body = re.search(
        r"def _setup_lldpd[\s\S]+?(?=\ndef [a-z_])",
        src,
    ).group(0)
    assert "/etc/lldpd.d" in body, (
        "Installer doesn't write the lldpd config file"
    )
    assert "configure system" in body or "configure lldp" in body


# ─── install_dpdk.sh ───


def test_install_dpdk_apt_deps_includes_lldpd():
    sh = _DPDK_SH.read_text()
    # The apt-get install line.
    m = re.search(r"deps_install_cmd=.*$", sh, re.MULTILINE)
    assert m and "lldpd" in m.group(0), (
        "install_dpdk.sh apt deps list missing lldpd"
    )


# ─── Backend cache + helpers ───


def test_lldp_cache_constants_defined():
    src = _src()
    assert "_LLDP_CACHE" in src
    assert "_LLDP_CACHE_TTL" in src
    assert "_LLDP_CACHE_LOCK" in src, (
        "Missing thread-lock around LLDP cache"
    )


def test_refresh_lldp_cache_uses_lldpcli_json():
    src = _src()
    m = re.search(
        r"def _refresh_lldp_cache\(\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    assert '"lldpcli"' in body
    assert '"-f", "json"' in body or "'json'" in body
    assert "show" in body and "neighbors" in body
    # Timeout to prevent Flask worker stall.
    assert "timeout=" in body


def test_refresh_lldp_cache_gracefully_handles_missing_binary():
    """lldpcli isn't installed on every box — the helper must
    catch FileNotFoundError so iface table still renders."""
    src = _src()
    m = re.search(
        r"def _refresh_lldp_cache\(\)[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "FileNotFoundError" in body, (
        "Missing FileNotFoundError catch for absent lldpcli"
    )


def test_get_lldp_neighbor_helper_defined():
    src = _src()
    assert "def _get_lldp_neighbor(" in src


# ─── /api/dpdk/interfaces field ───


def test_iface_response_includes_lldp_neighbor():
    src = _src()
    idx = src.find('"card_model": _detect_nic_model(pci)')
    assert idx > 0
    post = src[idx:idx + 2000]
    assert '"lldp_neighbor"' in post, (
        "iface entry missing lldp_neighbor field"
    )
    assert "_get_lldp_neighbor(iface)" in post


# ─── JS column ───


def test_iface_table_header_has_lldp_column():
    src = _src()
    assert "<th>LLDP neighbor</th>" in src, (
        "Iface table thead missing LLDP neighbor column"
    )


def test_iface_table_row_renders_lldp_cell():
    src = _src()
    # The row template uses ${lldpCell}.
    assert "${lldpCell}" in src
    # And the builder reads i.lldp_neighbor.
    assert "i.lldp_neighbor" in src or "const lldp = i.lldp_neighbor" in src


def test_lldp_cell_fallback_when_no_neighbor():
    src = _src()
    # The muted fallback should mention No LLDP / not running.
    idx = src.find("No LLDP neighbor seen on this port")
    assert idx > 0, (
        "No fallback text for ifaces with no LLDP neighbor"
    )


def test_lldp_cell_tooltip_includes_chassis_and_port():
    src = _src()
    idx = src.find("const lldp = i.lldp_neighbor")
    assert idx > 0
    body = src[idx:idx + 2500]
    assert "Switch:" in body or "sys_name" in body
    assert "Port:" in body or "port_id" in body
    assert "Mgmt IP" in body, "Tooltip doesn't include management IP"


# ─── Version ───


def test_pyproject_version_at_least_0582():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 82)
