"""v0.5.284 — Startup replay of `_ensure_ipv4_address` so all the
per-anchor fixes from v0.5.275/280/282 actually reach existing
deployments across a plain netgen-server-only upgrade."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
ARP_MON = (REPO / "utils" / "arp_monitor.py").read_text()


# --- ARP-J5: startup replay ---------------------------------------


def test_arp_j5_marker_present():
    assert "v0.5.284 (ARP-J5)" in ARP_MON


def test_arp_monitor_start_invokes_replay():
    """arp_monitor.start() must call _replay_dhcp_anchor_setup so
    existing anchors get the full v0.5.275/280/282 fix cluster on
    every netgen-server restart."""
    idx = ARP_MON.find("def start(self):")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "self._replay_dhcp_anchor_setup()" in body
    # And the replay call is guarded so failures don't stop the
    # monitor from starting.
    assert "except Exception" in body
    assert "anchor replay raised" in body


def test_replay_iterates_only_running_dhcp_server_devices():
    """Replay must filter to Running + dhcp_mode=server — no
    point re-triggering _ensure_ipv4_address on stopped devices
    or on DHCP-client / non-DHCP devices."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert 'd.get("status") == "Running"' in body
    assert 'dhcp_mode' in body
    assert '"server"' in body


def test_replay_calls_ensure_ipv4_address_from_utils_dhcp():
    """The replay's whole point is to invoke `_ensure_ipv4_address`
    so its post-add plumbing (v0.5.275/280/282 guards) fires."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "from utils.dhcp import _ensure_ipv4_address" in body
    assert "_ensure_ipv4_address(" in body


def test_replay_prefers_vlan_subif_over_server_interface():
    """Same v0.5.279 (ARP-H6) invariant: for VLAN devices, use
    `vlan<ID>` (the tagged sub-interface), NEVER
    `server_interface` which typically holds the parent NIC."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert 'if _vlan and _vlan != "0":' in body
    assert '_iface = f"vlan{_vlan}"' in body


def test_replay_skips_devices_without_pool_range():
    """A DHCP-server device without pool_start/pool_end has
    nothing to anchor — skip cleanly, don't raise."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "if not (_pool_start and _pool_end):" in body


def test_replay_parses_dhcp_config_json_string():
    """SQLite serialises `dhcp_config` as a JSON string. Replay
    must decode it to a dict before reading pool fields."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert "isinstance(_dhcp_cfg, str)" in body
    assert "import json as _json" in body
    assert "_json.loads(_dhcp_cfg)" in body


def test_replay_logs_summary_count():
    """After iterating, log a summary the operator can grep for
    to confirm the replay actually ran."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    assert 'replayed' in body
    assert 'failed' in body
    # The string spans two lines in the f-string source
    # ("DHCP-server " + "devices)") so match each half.
    assert 'DHCP-server' in body
    assert 'devices)' in body


def test_replay_per_device_failures_are_non_fatal():
    """One bad device must not stop the sweep — log at warning
    and move on to the next."""
    idx = ARP_MON.find("def _replay_dhcp_anchor_setup")
    end = ARP_MON.find("\n    def ", idx + 1)
    body = ARP_MON[idx:end]
    # Per-device try/except that increments _failed but continues.
    assert "except Exception as _rep_exc:" in body
    assert "_failed += 1" in body


# --- Metadata -----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 284)
