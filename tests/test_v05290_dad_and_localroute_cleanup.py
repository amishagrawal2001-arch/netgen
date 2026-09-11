"""v0.5.290 — Anchor-DAD + local-table cleanup bundle.

Operator on srv06 2026-09-11 (v0.5.289 running with bind-dynamic
working correctly): DHCP relay from a switch (giaddr=192.16.30.1
on a client subnet) arriving at netgen tagged VLAN 10 for the
DHCP-server device at 172.16.30.2. Frames arrived on vlan10
correctly, dnsmasq socket was in the right VRF (verified via
`ss --extended` showing cgroup=vrf-2ab19c928e6), but dnsmasq
still saw ZERO DHCPDISCOVER events.

Root cause: v0.5.286 (ARP-J6) had installed `local 192.16.30.1
dev vlan10` in the local table earlier when netgen anchored
`192.16.30.1` (v0.5.287 Fix A doesn'''t prevent this because
gateway=172.16.30.1 is on a different subnet, so isn'''t skipped).
Operator manually `ip addr del`-ed the address to break a
self-loop, which the kernel handled cleanly for the address
BUT does NOT auto-remove application-installed local routes.
The `local 192.16.30.1 dev vlan10` entry persisted as a ghost.
Kernel then treated 192.16.30.1 as netgen'''s own IP → packets
with src=192.16.30.1 (the switch'''s relay agent) got dropped
as martian sources (spoofed local IP).

Three parts to the fix:

1. **DAD-before-anchor** in `_ensure_ipv4_address` — before
   claiming an IP, `arping -D` probes the wire. If someone
   else answers, refuse to anchor. Prevents the whole class of
   `server_ip == some-other-device'''s-IP` bugs (v0.5.287
   Fix A only guards against the operator-declared gateway).

2. **Local-table cleanup** in `_remove_ipv4_address` — mirror
   the v0.5.286 explicit-install with an explicit-remove. No
   more ghosts after stop.

3. **Startup local-table drift-detect** in `arp_monitor` —
   scan for `local <ip> dev <iface>` entries whose <ip> is
   not currently on <iface>. WARN with the exact remediation
   command. Same design principle as v0.5.287 Fix C (never
   auto-delete, always tell the operator).
"""

from __future__ import annotations

import ipaddress
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
    str(Path(tempfile.gettempdir()) / f"netgen_v05290_test_{os.getpid()}.db"),
)


def _dhcp_src() -> str:
    return (REPO / "utils" / "dhcp.py").read_text()


def _mon_src() -> str:
    return (REPO / "utils" / "arp_monitor.py").read_text()


# ─────────────────────────────────────────────────────────────────
# Fix 1 — DAD-before-anchor helper
# ─────────────────────────────────────────────────────────────────

def test_probe_ip_conflict_helper_exists():
    from utils import dhcp as m
    assert hasattr(m, "_probe_ip_conflict")


def test_probe_returns_true_when_arping_D_exits_1():
    """arping -D exits 1 when someone else answers (address in
    use). The helper must map that to True (conflict)."""
    from utils import dhcp as m
    fake = MagicMock(returncode=1, stdout="", stderr="ARPING ...")
    with patch.object(m, "_run_command", return_value=fake):
        assert m._probe_ip_conflict("vlan10", "192.16.30.1") is True


def test_probe_returns_false_when_arping_D_exits_0():
    """arping -D exit 0 = no reply, IP free. Return False."""
    from utils import dhcp as m
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(m, "_run_command", return_value=fake):
        assert m._probe_ip_conflict("vlan10", "10.0.0.1") is False


def test_probe_returns_false_on_arping_missing():
    """If arping isn'''t installed, don'''t block anchor operations."""
    from utils import dhcp as m
    with patch.object(m, "_run_command",
                      side_effect=FileNotFoundError("arping")):
        assert m._probe_ip_conflict("vlan10", "10.0.0.1") is False


def test_probe_returns_false_on_other_error():
    """Non-1, non-0 arping return (e.g., iface down = 2) — treat
    as inconclusive, don'''t block."""
    from utils import dhcp as m
    fake = MagicMock(returncode=2, stdout="", stderr="Interface down")
    with patch.object(m, "_run_command", return_value=fake):
        assert m._probe_ip_conflict("vlan10", "10.0.0.1") is False


def test_ensure_ipv4_calls_dad_before_add():
    """The critical wiring: _ensure_ipv4_address must call the
    DAD probe BEFORE the `ip addr add` command."""
    src = _dhcp_src()
    idx = src.find("def _ensure_ipv4_address(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    dad_pos = body.find("_probe_ip_conflict(interface, server_ip")
    add_pos = body.find('"ip", "-4", "addr", "add"')
    assert dad_pos > 0, "DAD probe call missing from _ensure_ipv4_address"
    assert add_pos > 0, "ip addr add missing from _ensure_ipv4_address"
    assert dad_pos < add_pos, (
        "DAD probe must precede `ip addr add` — otherwise we'''ve "
        "already claimed the IP by the time we probe for conflicts"
    )


def test_ensure_ipv4_refuses_anchor_on_dad_conflict():
    """When DAD finds a conflict, return None + warn — do not
    fall through to `ip addr add`."""
    from utils import dhcp as m
    mock_run = MagicMock()
    def _fake_run(cmd, **kw):
        if "arping" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        # Any other _run_command call means we didn'''t refuse!
        raise AssertionError(
            f"unexpected _run_command after DAD conflict: {cmd!r}"
        )
    with patch.object(m, "_run_command", side_effect=_fake_run):
        with patch.object(m, "_iface_has_ipv4_in_subnet", return_value=False):
            picked = m._ensure_ipv4_address(
                "vlan10", "192.16.30.10", "192.16.30.200",
                gateway="172.16.30.1", container=None,
            )
    assert picked is None, "must refuse anchor when DAD detects conflict"


# ─────────────────────────────────────────────────────────────────
# Fix 2 — _remove_ipv4_address cleans local-table entry
# ─────────────────────────────────────────────────────────────────

def test_remove_ipv4_also_deletes_local_route():
    """_remove_ipv4_address must issue BOTH `ip addr del` AND
    `ip route del local ... table local` — mirroring v0.5.286
    ARP-J6'''s explicit install."""
    src = _dhcp_src()
    idx = src.find("def _remove_ipv4_address(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    assert '"ip", "-4", "addr", "del"' in body, "addr del still present"
    assert '"ip", "route", "del"' in body, "route del must be added"
    assert '"local"' in body, "must delete a local-type route"
    assert '"table", "local"' in body, "must target local table"
    assert "v0.5.290" in body, "marker missing"


def test_remove_ipv4_local_cleanup_uses_slash32():
    """The v0.5.286 install used /32; the remove must match."""
    src = _dhcp_src()
    idx = src.find("def _remove_ipv4_address(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    assert '"local", f"{address}/32"' in body, (
        "local route delete must specify /32 to match v0.5.286 install"
    )


def test_remove_ipv4_local_cleanup_is_best_effort():
    """The local-route cleanup wraps in try/except and treats
    'No such process' as non-fatal (kernel may have GC'''d it)."""
    src = _dhcp_src()
    idx = src.find("def _remove_ipv4_address(")
    end = src.find("\ndef ", idx + 1)
    body = src[idx:end]
    assert "try:" in body
    assert "except Exception" in body
    assert "No such process" in body
    assert "already absent" in body or "GC" in body


# ─────────────────────────────────────────────────────────────────
# Fix 3 — arp_monitor _scan_local_table_drift
# ─────────────────────────────────────────────────────────────────

def test_local_table_drift_scan_defined():
    src = _mon_src()
    assert "def _scan_local_table_drift" in src


def test_local_table_drift_scan_wired_into_start():
    src = _mon_src()
    idx = src.find("def start(self):")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "self._scan_local_table_drift()" in body
    assert "local-table drift scan raised" in body


def test_local_table_drift_scan_still_warns_on_detection():
    """v0.5.293 supersession: v0.5.290 shipped this as WARN-only,
    but the operator hit the ghost twice in one session (srv06
    2026-09-11). v0.5.293 upgrades to AUTO-DELETE — the scan now
    both WARNs AND calls `ip route del`. Test the invariant that
    WARN still happens on detection (dropping WARN would make the
    auto-delete silent, defeating debuggability)."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    # WARN must still fire — either on successful auto-clean or on
    # auto-clean failure.
    assert "logger.warning" in body, (
        "drift scan must still log at warning (v0.5.293 kept WARN, "
        "added auto-delete on top)"
    )
    assert "LOCAL-TABLE GHOST" in body


def test_local_table_drift_scan_uses_ip_route_show_table_local():
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '"ip", "route", "show", "table", "local"' in body


def test_local_table_drift_scan_names_remediation():
    """The warning must include the exact `ip route del` command
    the operator should run."""
    src = _mon_src()
    idx = src.find("def _scan_local_table_drift")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "LOCAL-TABLE GHOST" in body
    assert "ip route del local" in body
    assert "table local" in body


# ─────────────────────────────────────────────────────────────────
# Regression: v0.5.287 Fix A + v0.5.289 bind-dynamic still there
# ─────────────────────────────────────────────────────────────────

def test_v05287_fix_a_still_present():
    """v0.5.290 additions must not accidentally revert Fix A."""
    src = _dhcp_src()
    assert "v0.5.287 (audit anchor-gateway-collision)" in src


def test_v05289_bind_dynamic_still_present():
    """v0.5.290 additions must not accidentally revert bind-dynamic."""
    src = _dhcp_src()
    idx = src.find("config_lines = [")
    end = src.find("]", idx)
    body = src[idx:end + 1]
    assert '"bind-dynamic"' in body


# ─────────────────────────────────────────────────────────────────
# Metadata
# ─────────────────────────────────────────────────────────────────

def test_version_bumped():
    import re
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 290), (
        f"version {major}.{minor}.{patch} < 0.5.290"
    )
