"""v0.5.258 — ARP subsystem audit: 5 correctness fixes."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
ARP = (REPO / "utils" / "arp.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- ARP-1: VRF-blind endpoints ------------------------------------


def test_arp_vrf_prefix_helper_defined():
    assert "def _arp_vrf_prefix(device_id):" in SERVER
    assert "audit ARP-1" in SERVER


def test_check_arp_resolution_uses_vrf_prefix():
    idx = SERVER.find("def check_arp_resolution():")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 4000]
    assert "_vrf_prefix = _arp_vrf_prefix(data.get(\"device_id\"))" in body
    # And the neigh cmd prepends the prefix (in both IPv4 + IPv6 branches).
    assert body.count('_vrf_prefix + ["ip"') >= 2


def test_send_arp_request_internal_uses_vrf_prefix():
    idx = SERVER.find("def send_arp_request_internal(data):")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 5000]
    assert "audit ARP-1" in body
    assert "_vrf_prefix = _arp_vrf_prefix(" in body
    # ping / ping6 / neigh commands all prepend the prefix.
    assert '_vrf_prefix + ["ping"' in body
    assert '_vrf_prefix + ["ping6"' in body
    assert '_vrf_prefix + ["ip"' in body


def test_arp_vrf_prefix_returns_empty_on_no_device_id():
    """Loading the module + calling with None must return []
    (falsy so it can be spread into an argv without effect)."""
    import importlib.util
    # Read the helper's implementation from source; can't import
    # run_tgen_server without triggering the module-init DB setup.
    # Instead exec the helper stub in isolation.
    import subprocess as _sp
    helper_src = re.search(
        r"def _arp_vrf_prefix\(device_id\):.*?(?=\n\n@app\.route)",
        SERVER, re.DOTALL,
    )
    assert helper_src is not None
    src = "import subprocess, logging\n" + helper_src.group(0)
    ns = {}
    exec(src, ns)
    assert ns["_arp_vrf_prefix"](None) == []
    assert ns["_arp_vrf_prefix"]("") == []


# --- ARP-2: vlan_tpid applied to Ether.type -----------------------


def test_arp_vlan_tpid_applied_to_ether_type():
    assert "audit ARP-2" in ARP
    # The Dot1Q wrap must pass type=vlan_tpid to Ether(...).
    assert "Ether(src=eth_src, dst=eth_dst, type=vlan_tpid)" in ARP


def test_arp_untagged_ether_type_still_arp():
    """Non-VLAN branch keeps type=0x0806 (ARP EtherType)."""
    assert "Ether(src=eth_src, dst=eth_dst, type=0x0806)" in ARP


# --- ARP-3: Request hwdst zeros per RFC 826 ------------------------


def test_arp_request_hwdst_zero_when_target_mac_not_supplied():
    assert "audit ARP-3" in ARP
    assert 'arp_hwdst = "00:00:00:00:00:00"' in ARP
    # Guarded by op==1 (Request) + explicit-target absence.
    assert 'if op == 1 and not (arp_pd.get("arp_target_mac") or "").strip():' in ARP


def test_arp_reply_still_uses_target_mac():
    """Op=2 (Reply) MUST keep target_mac in payload hwdst — the
    reply is unicast and carries the requester's MAC."""
    # Fix only mutates hwdst under `if op == 1`; op=2 (reply) path
    # untouched: arp_hwdst starts as target_mac and stays.
    idx = ARP.find("arp_hwdst = target_mac")
    assert idx > 0


# --- ARP-5: _neigh_state_ok walks all lines ------------------------


def test_neigh_state_ok_walks_all_lines():
    idx = SERVER.find("def _neigh_state_ok(target, family=")
    end = SERVER.find("\n\n        def _strip_mask", idx + 1)
    # If not found, scan larger
    if end < 0:
        end = idx + 3000
    body = SERVER[idx:end]
    assert "audit ARP-5" in body
    # Loop is over every line, not just the first.
    assert "for line in out.splitlines():" in body
    # No more indexing to the first line only.
    assert "out.splitlines()[0]" not in body


# --- ARP-6: ping-success wins over empty neigh --------------------


def test_send_arp_ping_success_returns_success_when_neigh_empty():
    idx = SERVER.find("def send_arp_request_internal(data):")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 5000]
    assert "audit ARP-6" in body
    # The failure branch under "no neigh entry" now returns success
    # when ping succeeded.
    assert '"success": True' in body
    assert "neighbor table entry may still be committing" in body


# --- Metadata ------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 258)
