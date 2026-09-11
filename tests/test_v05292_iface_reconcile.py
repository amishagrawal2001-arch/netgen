"""v0.5.292 — start_dhcp_server: reconcile interface parameter
against dhcp_config.interface so anchor + DAD land on the same
iface as dnsmasq'''s bind.

Operator on srv06 2026-09-11 (v0.5.291 running): DHCP-server
device had `interface=ens2f0np0` (parent) at the top level but
`dhcp_config.interface=vlan10` (subif). start_dhcp_server called
_ensure_ipv4_address with the parent → anchor landed on
ens2f0np0 while dnsmasq bound to vlan10. v0.5.290 DAD probed
ens2f0np0 (untagged), couldn'''t reach the switch'''s relay agent
on VLAN 10 tagged → DAD returned False → anchor proceeded on
wrong iface → the whole cluster of v0.5.287-291 fixes was
defeated by this one bug.

Fix: at start_dhcp_server entry, if dhcp_config.interface is a
distinct subinterface of the passed `interface`, prefer it.
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05292_test_{os.getpid()}.db"),
)


def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


# ─────────────────────────────────────────────────────────────────
# Source-level lock-in: the reconcile block is in start_dhcp_server
# ─────────────────────────────────────────────────────────────────

def test_v05292_marker_in_source():
    src = _dhcp_src()
    assert "v0.5.292 (audit anchor-interface-reconcile)" in src


def test_reconcile_uses_dhcp_config_interface_when_subif():
    """Source lock-in: the reconcile block reads
    dhcp_config.interface and calls _iface_parent to verify it'''s
    a subif of the passed interface."""
    src = _dhcp_src()
    idx = src.find("def start_dhcp_server(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    assert "v0.5.292" in body
    assert 'dhcp_config or {}' in body or 'dhcp_config).get' in body
    assert '"interface"' in body
    assert "_iface_parent(_cfg_iface" in body


def test_reconcile_only_activates_when_parent_matches():
    """The block must only reassign `interface` when
    `_iface_parent(_cfg_iface) == interface` — i.e., the config'''s
    interface is actually a subif of the caller-passed interface.
    Prevents accidentally switching to an unrelated iface."""
    src = _dhcp_src()
    idx = src.find("v0.5.292 (audit anchor-interface-reconcile)")
    body = src[idx:idx + 2500]
    assert "if _parent == interface:" in body


def test_reconcile_leaves_interface_unchanged_when_no_subif():
    """When dhcp_config.interface is empty, equal to the passed
    interface, or unrelated, the reconcile is a no-op."""
    from utils import dhcp as m
    # No dhcp_config.interface — no reassignment expected.
    # We can'''t easily invoke start_dhcp_server end-to-end, but we
    # can verify the source-level guards: check that the reassign
    # is guarded by `if _cfg_iface and _cfg_iface != interface:`.
    src = _dhcp_src()
    idx = src.find("v0.5.292 (audit anchor-interface-reconcile)")
    body = src[idx:idx + 2500]
    assert "if _cfg_iface and _cfg_iface != interface:" in body


def test_reconcile_normalizes_the_cfg_interface():
    """dhcp_config.interface could be `vlan10@ens2f0np0` (display
    form). Must normalize before comparison so we don'''t falsely
    reject the reconcile."""
    src = _dhcp_src()
    idx = src.find("v0.5.292 (audit anchor-interface-reconcile)")
    body = src[idx:idx + 2500]
    assert "_normalize_iface_name(" in body


def test_reconcile_logs_the_swap():
    """When the reconcile fires, log at INFO so operators see WHY
    the anchor iface changed under them."""
    src = _dhcp_src()
    idx = src.find("v0.5.292 (audit anchor-interface-reconcile)")
    body = src[idx:idx + 2500]
    assert "anchor-iface reconcile" in body
    assert "logger.info" in body


# ─────────────────────────────────────────────────────────────────
# Regression — v0.5.287 Fix A + v0.5.289 bind-dynamic still there
# ─────────────────────────────────────────────────────────────────

def test_v05287_fix_a_intact():
    src = _dhcp_src()
    assert "v0.5.287 (audit anchor-gateway-collision)" in src


def test_v05289_bind_dynamic_intact():
    src = _dhcp_src()
    idx = src.find("config_lines = [")
    end = src.find("]", idx)
    body = src[idx:end + 1]
    assert '"bind-dynamic"' in body


def test_v05290_dad_helpers_intact():
    src = _dhcp_src()
    assert "def _probe_ip_conflict" in src
    assert "def _probe_ip_conflict_scapy" in src


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    import re
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 292)
