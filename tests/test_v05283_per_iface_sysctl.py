"""v0.5.283 — Per-interface arp_ignore sweep (fixes v0.5.282
self-inflicted bug where sweep only set `all` / `default`)."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
ARP_MON = (REPO / "utils" / "arp_monitor.py").read_text()


def test_arp_j4_marker_present():
    assert "v0.5.283 (ARP-J4)" in ARP_MON


def test_sweep_iterates_ip_link_show():
    """Per-interface fix needs to know which interfaces exist —
    reads them from `ip -o link show`."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '"ip", "-o", "link", "show"' in body


def test_sweep_probes_current_value_before_setting():
    """`sysctl -n <key>` reads the current value. Only setting
    when non-zero avoids gratuitous kernel writes + gives us
    the BEFORE value for the transition log."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '"sysctl", "-n"' in body
    # Skip when already 0.
    assert 'if _cur_val == "0":' in body


def test_sweep_forces_per_interface_arp_ignore_and_announce():
    """Both arp_ignore AND arp_announce set per-interface."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '"arp_ignore", "arp_announce"' in body
    assert 'f"net.ipv4.conf.{_iface}.{_key}"' in body


def test_sweep_skips_loopback():
    """Setting arp_ignore on `lo` is meaningless and clutters
    the log."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '_name == "lo"' in body


def test_sweep_logs_before_after_transition():
    """Ops-log grep for the fix needs to see the BEFORE→AFTER
    value so operators can confirm which specific interfaces
    got fixed."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '{_cur_val}→0' in body
    # And a summary count at the end.
    assert "per-interface sysctl sweep" in body


def test_sweep_still_sets_all_and_default_baseline():
    """v0.5.282's `all` + `default` sweep is kept as a baseline
    for NEW interfaces created after startup (v0.5.283 J4 covers
    existing ones)."""
    idx = ARP_MON.find("def _sweep_arp_sysctls")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert '"net.ipv4.conf.all.arp_ignore"' in body
    assert '"net.ipv4.conf.default.arp_ignore"' in body


# --- Metadata ---------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 283)
