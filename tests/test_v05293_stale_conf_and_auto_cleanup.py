"""v0.5.293 — Stale-conf sweep + auto-delete local-table ghosts.

Two operator-hit ghost-state bugs that survived the v0.5.287-292
fix chain, both discovered on srv06 2026-09-11 in the same session
that verified v0.5.292 working:

1. **Stale dnsmasq conf**: pre-v0.5.292 anchored on the parent NIC
   (ens2f0np0), so `/etc/dnsmasq.d/ostg-ens2f0np0.conf` got written.
   v0.5.292 reconcile switched anchor to vlan10, wrote a new
   `ostg-vlan10.conf`, but the OLD one persisted. When the docker
   container restarted, its baked entrypoint launched
   `dnsmasq --conf-file=/etc/dnsmasq.d/ostg-ens2f0np0.conf` — dnsmasq
   bound to the parent NIC and ignored the v0.5.289 bind-dynamic
   fix in the vlan10 conf. Every relayed DHCP frame silently dropped.

2. **Ghost local-table entry**: operator did manual `ip addr del
   192.16.30.1/24 dev vlan10` (from earlier debug), which the kernel
   handled cleanly for the ADDRESS but NOT for the explicitly-
   installed `local 192.16.30.1 dev vlan10` route from v0.5.286.
   That ghost made kernel treat 192.16.30.1 as netgen's own IP →
   drops incoming packets with src=192.16.30.1 (the switch's giaddr)
   as martian. v0.5.290 Fix 3 detected these ghosts but only WARNed;
   the operator hit the ghost TWICE in one session before we caught
   on. WARN was too cautious for a state with zero legitimate use.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05293_test_{os.getpid()}.db"),
)


def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _mon_src() -> str:
    return (REPO / "utils" / "arp_monitor.py").read_text()


# ─────────────────────────────────────────────────────────────────
# Fix A — stale-conf sweep in start_dhcp_server
# ─────────────────────────────────────────────────────────────────

def test_v05293_stale_conf_sweep_marker_present():
    src = _dhcp_src()
    assert "v0.5.293 (audit stale-conf-sweep)" in src


def test_sweep_lives_in_start_dhcp_server_before_verify_iface():
    """The sweep must fire in start_dhcp_server BEFORE the
    _verify_interface_exists check, so a stale conf doesn't get
    another life span even if the current iface isn't up yet."""
    src = _dhcp_src()
    idx = src.find("def start_dhcp_server(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    sweep_pos = body.find("v0.5.293 (audit stale-conf-sweep)")
    verify_pos = body.find("_verify_interface_exists(interface, container=container)")
    assert sweep_pos > 0
    assert verify_pos > 0
    assert sweep_pos < verify_pos, (
        "sweep must precede _verify_interface_exists (otherwise "
        "a stale conf could survive the iface-missing path)"
    )


def test_sweep_lists_ostg_conf_files_and_deletes_non_matching():
    """The sweep must list ostg-*.conf files, keep the one for the
    current iface, and rm any others."""
    src = _dhcp_src()
    idx = src.find("v0.5.293 (audit stale-conf-sweep)")
    body = src[idx:idx + 3500]
    assert 'ls /etc/dnsmasq.d/ostg-*.conf' in body
    assert '"rm", "-f", _f' in body
    # And guards to skip the CURRENT conf (don't delete what we're
    # about to write).
    assert "if _basename == _keep_conf:" in body
    assert "continue" in body


def test_sweep_logs_each_removal_at_info():
    """Each stale conf removal must log at INFO with the file path
    + current iface, so operators see what happened."""
    src = _dhcp_src()
    idx = src.find("v0.5.293 (audit stale-conf-sweep)")
    body = src[idx:idx + 3500]
    assert "stale-conf sweep: removed" in body
    assert "logger.info" in body


def test_sweep_is_best_effort():
    """Failures in the sweep (permissions, filesystem, etc.) must
    NOT block dnsmasq launch. Wrapped in try/except at debug."""
    src = _dhcp_src()
    idx = src.find("v0.5.293 (audit stale-conf-sweep)")
    body = src[idx:idx + 3500]
    assert "except Exception as _sweep_exc:" in body
    assert "non-fatal" in body


# ─────────────────────────────────────────────────────────────────
# Fix B — _scan_local_table_drift AUTO-DELETES
# ─────────────────────────────────────────────────────────────────

def test_v05293_auto_cleanup_marker_present():
    src = _mon_src()
    assert "v0.5.293 (audit auto-cleanup-ghost)" in src


def test_drift_scan_now_calls_ip_route_del():
    """v0.5.290 was WARN-only. v0.5.293 upgrades to AUTO-DELETE.
    The scan must actually issue `ip route del local ... table
    local` now."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '"ip", "route", "del", "local"' in body
    assert '"table", "local"' in body


def test_drift_scan_logs_auto_cleaned_on_success():
    """When the auto-cleanup succeeds, log WARN so operators see
    a ghost was found + removed. Not silent."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "LOCAL-TABLE GHOST auto-cleaned" in body


def test_drift_scan_logs_manual_remediation_on_failure():
    """When the auto-cleanup FAILS (permissions, timing), fall back
    to the v0.5.290 pattern: WARN with the exact ip route del
    command for the operator to run manually."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "auto-cleanup FAILED" in body
    assert "Run" in body and "manually" in body
    assert "ip route del local" in body


def test_drift_scan_records_rm_return_code_and_stderr():
    """Failure log must include the actual rc + stderr so
    operators can diagnose why auto-cleanup failed."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "_rm_rc" in body or "returncode" in body
    assert "_rm_err" in body or "stderr" in body


# ─────────────────────────────────────────────────────────────────
# Regression: v0.5.287/289/290/291/292 markers intact
# ─────────────────────────────────────────────────────────────────

def test_v05287_fix_a_intact():
    assert "v0.5.287 (audit anchor-gateway-collision)" in _dhcp_src()


def test_v05289_bind_dynamic_intact():
    src = _dhcp_src()
    idx = src.find("config_lines = [")
    end = src.find("]", idx)
    assert '"bind-dynamic"' in src[idx:end + 1]


def test_v05290_dad_helpers_intact():
    src = _dhcp_src()
    assert "def _probe_ip_conflict" in src
    assert "def _probe_ip_conflict_scapy" in src


def test_v05292_reconcile_intact():
    assert "v0.5.292 (audit anchor-interface-reconcile)" in _dhcp_src()


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    import re
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 293)
