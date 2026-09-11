"""v0.5.295 — Anchor replay must propagate relay_return_hop.

Root cause on srv06 2026-09-11 14:01:55: netgen-server restarted
(after v0.5.294 upgrade), v0.5.284 anchor-replay ran for the
relay-mode DHCP-server device (device3, vlan10, pool 192.16.30.10-.200,
relay_return_hop=172.16.30.1), but the replay call site in
utils/arp_monitor.py never read relay_return_hop from dhcp_config.
_ensure_ipv4_address's v0.5.245 relay-mode guard needs the field —
without it, the anchor got re-installed (192.16.30.1 on vlan10),
which happens to match the switch's DHCP-relay giaddr. Kernel then
dropped every relayed DHCP frame with src=192.16.30.1 as martian.
Operator hit the anchor-collision + self-loop drop AGAIN after every
netgen-server restart, despite the v0.5.245 skip having been in
place since three ships ago.

Fix: read relay_return_hop from dhcp_config in the replay loop and
pass it to _ensure_ipv4_address.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def _src() -> str:
    return (REPO / "utils" / "arp_monitor.py").read_text()


def test_v05295_marker_present():
    assert "v0.5.295 (audit anchor-replay-relay-mode)" in _src()


def test_replay_reads_relay_return_hop():
    src = _src()
    idx = src.find("def _replay_dhcp_anchor_setup")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert '_dhcp_cfg.get("relay_return_hop")' in body


def test_replay_passes_relay_return_hop_to_ensure():
    """The critical wiring — the local var must reach the kwarg."""
    src = _src()
    idx = src.find("def _replay_dhcp_anchor_setup")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "relay_return_hop=_relay_return_hop" in body


def test_replay_still_calls_ensure_ipv4_address():
    src = _src()
    idx = src.find("def _replay_dhcp_anchor_setup")
    end = src.find("\n    def ", idx + 1)
    body = src[idx:end]
    assert "_ensure_ipv4_address(" in body


def test_relay_return_hop_var_uses_str_wrap():
    """Defensive: dhcp_cfg may contain non-string values, unify to str
    matching the v0.5.245 signature."""
    src = _src()
    idx = src.find("_relay_return_hop = str(")
    assert idx > 0


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 295)
