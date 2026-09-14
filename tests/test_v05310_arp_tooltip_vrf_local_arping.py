"""v0.5.310 — three fixes for the "device1 gateway ARP failing"
saga:

  A. Tooltip mislabel. Devices tab "IPv4" column tooltip read
     "IPv4 ARP failed: Failed" — operators interpreted as ARP-for-
     gateway failure because there's no separate cell for the
     self-check metric. The status was actually the self-ping to
     the device's OWN IP inside its VRF context (a proxy for L3
     wiring correctness). Rename to "IPv4 self-check failed" and
     expose the underlying `ipv4_ping` result + VRF name from
     the endpoint's details dict.

  B. VRF local host-route drift. On srv06 2026-09-13 for device1
     (192.168.0.2 on vlan100 in vrf-fdde6b42126): the device-apply
     path adds the IPv4 to the interface BEFORE the FRR container
     start enslaves that interface to the device's VRF. The
     kernel installs `local 192.168.0.2 dev lo` in the DEFAULT
     local table (255), and enslavement doesn't auto-migrate it.
     Fix in utils/frr_docker._create_vrf: after enslavement,
     install `ip route add local <ip>/<host-prefix> dev <iface>
     table <vrf_table>` for each address.

  C. iputils-arping not auto-installed. v0.5.278 ARP-H3 uses
     arping as the arp-warm primitive. Ship the dep in the
     netgen-install auto-provision alongside lldpd.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─────────────────────────── Fix A: Tooltip clarity


def test_A_marker_present():
    assert "v0.5.310 (audit arp-tooltip-mislabel)" in _read("widgets/devices_tab.py")


def test_A_tooltip_renames_to_self_check():
    src = _read("widgets/devices_tab.py")
    assert '"IPv4 self-check failed' in src
    # The failure-branch setToolTip call MUST NOT use the old
    # misleading "IPv4 ARP failed:" phrasing. Match by the
    # setToolTip( prefix so comments (which quote the old text
    # for context) aren't caught.
    assert 'setToolTip(f"IPv4 ARP failed:' not in src


def test_A_success_tooltip_matches_metric_semantics():
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.310 (audit arp-tooltip-mislabel)")
    body = src[idx:idx + 3000]
    assert '"IPv4 self-check passed"' in body


def test_A_tooltip_surfaces_details_when_available():
    src = _read("widgets/devices_tab.py")
    idx = src.index("v0.5.310 (audit arp-tooltip-mislabel)")
    body = src[idx:idx + 3000]
    assert '_details.get("ipv4_ping")' in body
    assert '_details.get("vrf")' in body


# ─────────────────────────── Fix B: VRF local host-route


def test_B_marker_present():
    assert "v0.5.310 (audit vrf-local-host-route-drift)" in _read("utils/frr_docker.py")


def test_B_runs_after_vrf_enslavement():
    src = _read("utils/frr_docker.py")
    idx = src.index("v0.5.310 (audit vrf-local-host-route-drift)")
    before = src[:idx]
    enslave_idx = before.rindex('"ip", "link", "set", iface_name, "master", vrf_name')
    assert enslave_idx < idx


def test_B_iterates_both_families():
    src = _read("utils/frr_docker.py")
    idx = src.index("v0.5.310 (audit vrf-local-host-route-drift)")
    body = src[idx:idx + 5000]
    assert 'for _fam_flag in ("-4", "-6"):' in body
    assert "is_link_local" in body


def test_B_installs_local_route_in_vrf_table():
    src = _read("utils/frr_docker.py")
    idx = src.index("v0.5.310 (audit vrf-local-host-route-drift)")
    body = src[idx:idx + 5000]
    assert '"ip", _fam_flag, "route", "add"' in body
    assert '"local", f"{_addr}/{_host_pfx}"' in body
    assert '"table", str(vrf_table)' in body
    assert '_host_pfx = "32" if _fam_flag == "-4" else "128"' in body


def test_B_file_exists_is_swallowed():
    src = _read("utils/frr_docker.py")
    idx = src.index("v0.5.310 (audit vrf-local-host-route-drift)")
    body = src[idx:idx + 5000]
    assert '"File exists" not in (_r.stderr or "")' in body


def test_B_best_effort_no_vrf_creation_abort():
    src = _read("utils/frr_docker.py")
    idx = src.index("v0.5.310 (audit vrf-local-host-route-drift)")
    body = src[idx:idx + 5000]
    assert "try:" in body
    assert "except Exception as _local_route_exc:" in body


# ─────────────────────────── Fix C: iputils-arping in installer


def test_C_setup_arping_function_defined():
    assert "def _setup_arping()" in _read("scripts/tarball/netgen-install")


def test_C_setup_arping_wired_into_main():
    src = _read("scripts/tarball/netgen-install")
    idx = src.index("_setup_lldpd()  # v0.5.82")
    tail = src[idx:idx + 500]
    assert "_setup_arping()" in tail


def test_C_installs_iputils_arping_package():
    src = _read("scripts/tarball/netgen-install")
    idx = src.index("def _setup_arping()")
    body = src[idx:idx + 3000]
    assert '"iputils-arping"' in body
    assert "apt-get" in body


def test_C_best_effort_no_install_abort():
    src = _read("scripts/tarball/netgen-install")
    idx = src.index("def _setup_arping()")
    body = src[idx:idx + 3000]
    assert "check=False" in body
    assert "logger.warning" in body


# ─────────────────────────── AST parse


def test_edited_files_ast_parse():
    import ast
    for rel in (
        "widgets/devices_tab.py",
        "utils/frr_docker.py",
        "scripts/tarball/netgen-install",
    ):
        ast.parse(_read(rel))
