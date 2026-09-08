"""v0.5.281 — ARP-plane guards: startup self-check + invariant
docstring on the endpoint.

Non-functional ship — asserts the guards ARE in place, not that
they change behaviour. The pass/fail signal to operators is
seeing warnings in the server log instead of opening a new
'gateway orange' ticket.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()
ARP_MON = (REPO / "utils" / "arp_monitor.py").read_text()


# --- ARP-GUARD-1: startup self-check in arp_monitor.start() -----


def test_start_runs_arp_plane_self_check():
    """arp_monitor.start() must invoke the self-check BEFORE
    starting the monitor thread so the operator sees the report
    even if the monitor loop hangs on first iteration."""
    idx = ARP_MON.find("def start(self):")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "v0.5.281 (ARP-GUARD-1)" in body
    assert "self._log_arp_plane_self_check()" in body
    # The self-check call must happen BEFORE the monitor thread
    # starts, so a hang in the loop doesn't hide the report.
    sc_idx = body.find("self._log_arp_plane_self_check()")
    thread_idx = body.find("self.monitor_thread = threading.Thread")
    assert 0 < sc_idx < thread_idx


def test_self_check_probes_arp_ignore_sysctl():
    """Invariant 5: arp_ignore=0 on the all-scope so secondary-IP
    replies work. Self-check probes the sysctl and warns on
    non-zero."""
    idx = ARP_MON.find("def _log_arp_plane_self_check")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "net.ipv4.conf.all.arp_ignore" in body
    # Warning names the ship the regression would look like.
    assert "v0.5.280" in body


def test_self_check_probes_anchor_arp_thread_alive():
    """Invariant 6: if any DHCP anchors are registered, the
    periodic re-arp thread MUST be alive."""
    idx = ARP_MON.find("def _log_arp_plane_self_check")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "_ANCHOR_ARP_REFRESH" in body
    assert "_ANCHOR_ARP_THREAD" in body
    assert "is_alive()" in body
    assert "DHCP-K2" in body


def test_self_check_probes_frr_manager_lazy_proxy():
    """Invariant 7: frr_manager lazy proxy must be importable.
    A failure means VRF detection will cold-init Docker on every
    request — the v0.5.277 (ARP-H2) silent-orange regression."""
    idx = ARP_MON.find("def _log_arp_plane_self_check")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "from utils.frr_docker import frr_manager" in body
    assert "ARP-H2" in body


def test_self_check_failures_are_non_fatal():
    """The self-check probes rooted commands (sysctl, ip). On a
    rootless dev host they may fail — that must not prevent the
    monitor from starting."""
    idx = ARP_MON.find("def start(self):")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    # Wrapped in a try/except that logs a warning but continues.
    assert "except Exception" in body
    assert "self-check raised" in body


# --- ARP-GUARD-2: invariant docstring on the endpoint -----------


def test_arp_endpoint_carries_invariant_docstring():
    """The docstring must enumerate the seven load-bearing
    invariants. Grep-anchored so a future 'simplification' that
    drops the numbered list fails this test."""
    idx = SRV.find("def get_device_arp_status(device_id):")
    end = SRV.find("try:", idx)
    docstring = SRV[idx:end]
    assert "ARP-GUARD-2" in docstring
    # All seven ship markers must be cited in the docstring.
    for marker in (
        "v0.5.254", "v0.5.258", "v0.5.272",   # neigh-first evolution
        "v0.5.277",                            # neigh-first + Docker fix
        "v0.5.278",                            # short-circuit + arping-warm
        "v0.5.279",                            # ternary fix
        "v0.5.280",                            # sysctl + periodic re-arp
    ):
        assert marker in docstring, (
            f"invariant docstring must cite ship {marker!r} so "
            f"future ops-log grep finds the rationale"
        )
    # And the seven numbered invariants each get a summary line.
    for n in range(1, 8):
        assert f"{n}." in docstring, (
            f"invariant #{n} must be numbered so operators can "
            f"reference specific guards by number"
        )


# --- Metadata ---------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 281)
