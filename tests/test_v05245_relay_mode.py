"""v0.5.245 — DHCP relay mode: server can serve clients behind a
DHCP relay without stealing the relay's IP or blackholing OFFERs.

Operator on srv06 2026-09-02: DHCP client on vlan30 (192.168.30/24)
routed via Juniper irb.30 (192.168.30.1) which relays to netgen
DHCP server on vlan10 (172.16.30.1). Before this fix:

- Attaching a 192.168.30 pool auto-anchored `192.168.30.1/24` on
  the server's vlan10. That collided with the switch's own irb.30
  = 192.168.30.1. Two devices claiming the same IP → ARP chaos.

- The auto-anchor also added a connected route
  `192.168.30.0/24 dev vlan10 src 192.168.30.1`. When dnsmasq
  replied to giaddr=192.168.30.1, kernel saw the destination as
  a local IP → short-circuited → OFFER never left the box.

- Even without the anchor, netgen would try to install
  `192.168.30.0/24 via 192.168.30.1 dev vlan10` (pool's client
  gateway as next-hop) — which is unreachable from the server.

The fix: one new optional pool field `relay_return_hop` — the
RELAY's IP on the SERVER's L2 segment (e.g. 172.16.30.10 = the
switch's irb.10 SVI). When set:

1. `_ensure_ipv4_address` skips the interface anchor entirely.
2. Pool subnet routes install as `<pool> via <relay_return_hop>
   dev <iface>` so OFFERs traverse back through the relay.
3. dnsmasq option 3 (client's advertised gateway) still uses the
   pool's `gateway` field — that's what CLIENTS need for their
   default route, not what the SERVER needs to reach clients.

Absent (empty string / null): behavior is unchanged from v0.5.244
— direct-attached clients on the pool's L2 segment.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()
DB = (REPO / "utils" / "device_database.py").read_text()
UI = (REPO / "utils" / "devices_tab_dhcp.py").read_text()


# --- Core: _ensure_ipv4_address skips anchor in relay mode ----------


def test_ensure_ipv4_address_takes_relay_return_hop_param():
    idx = DHCP.find("def _ensure_ipv4_address(")
    body = DHCP[idx:idx + 2500]
    assert "relay_return_hop: str = \"\"" in body


def test_ensure_ipv4_address_skips_anchor_when_relay_set():
    """First guard: if relay_return_hop is truthy, return None
    immediately — no interface IP added, no logs about which .1
    to pick."""
    idx = DHCP.find("def _ensure_ipv4_address(")
    body = DHCP[idx:idx + 3500]
    assert "v0.5.245 (audit U relay-mode)" in body
    assert "if relay_return_hop:" in body
    # Uses a log line explaining WHY it's skipping.
    assert "Skipping IPv4 anchor on %s for pool %s-%s" in body


# --- Caller: start_dhcp_server reads + propagates relay_return_hop --


def test_start_dhcp_server_reads_relay_return_hop_from_config():
    """The dhcp_config extract picks up relay_return_hop next to
    the gateway field."""
    idx = DHCP.find('gateway = dhcp_config.get("gateway", "")')
    body = DHCP[idx:idx + 1500]
    assert 'relay_return_hop = (' in body
    assert 'dhcp_config.get("relay_return_hop", "")' in body


def test_primary_pool_anchor_call_passes_relay_return_hop():
    """The primary-pool _ensure_ipv4_address call must forward the
    relay hint, otherwise the guard never triggers on the main
    pool."""
    idx = DHCP.find("[DHCP] Server-mode IPv4 anchor on %s: %s")
    body = DHCP[max(0, idx - 800):idx + 200]
    assert "relay_return_hop=relay_return_hop" in body


def test_additional_pools_anchor_passes_per_pool_or_device_relay():
    """Each additional pool can have its own relay_return_hop; if
    absent, falls back to the device-level one."""
    idx = DHCP.find("v0.5.245: per-pool relay override")
    assert idx > 0
    body = DHCP[idx:idx + 1500]
    assert '_add_relay = _add_pool.get("relay_return_hop") or relay_return_hop' in body
    assert "relay_return_hop=_add_relay" in body


def test_pool_route_next_hop_uses_relay_when_set():
    """Both gateway_routes and pool_networks_unique route-install
    loops must prefer relay_return_hop over gateway."""
    idx = DHCP.find("v0.5.245: in relay mode, all pool-adjacent routes")
    assert idx > 0
    body = DHCP[idx:idx + 2500]
    assert "_route_next_hop = relay_return_hop or gateway" in body
    assert "_pool_next_hop = relay_return_hop or gateway" in body


# --- API: pool CRUD accepts + emits relay_return_hop ----------------


def test_create_pool_api_reads_relay_return_hop():
    idx = SERVER.find("def create_dhcp_pool(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 3000]
    assert 'v0.5.245 (audit U relay-mode)' in body
    assert '"relay_return_hop": data.get("relay_return_hop")' in body


def test_update_pool_api_reads_relay_return_hop():
    idx = SERVER.find("def update_dhcp_pool_endpoint(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 3000]
    assert '"relay_return_hop"' in body


def test_pool_api_serializer_emits_relay_return_hop():
    idx = SERVER.find("def _dhcp_pool_to_api(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 1500]
    assert '"relay_return_hop": pool.get("relay_return_hop") or ""' in body


def test_attach_pools_propagates_relay_hop_from_pool_and_override():
    """attach_dhcp_pools_to_server must copy relay_return_hop from
    the pool catalog into device dhcp_config, and honor a per-
    attach override."""
    # Scope to the attach body specifically (not create_dhcp_pool).
    idx = SERVER.find("def attach_dhcp_pools_to_server(")
    end = SERVER.find("\n@app.route", idx + 1)
    body = SERVER[idx:end if end > 0 else idx + 12000]
    assert '_relay_override = (data.get("relay_return_hop") or "").strip()' in body
    assert 'dhcp_cfg["relay_return_hop"] = _relay_override' in body
    assert 'primary_pool.get("relay_return_hop")' in body


def test_attach_pools_propagates_relay_hop_per_additional_pool():
    idx = SERVER.find("v0.5.245: per-pool relay_return_hop passes through")
    assert idx > 0
    body = SERVER[idx:idx + 500]
    assert 'pool_entry["relay_return_hop"] = pool.get("relay_return_hop")' in body


# --- DB: dhcp_pools table has the column ----------------------------


def test_dhcp_pools_schema_has_relay_return_hop():
    assert "relay_return_hop TEXT" in DB
    # Migration for existing installs.
    assert 'ALTER TABLE dhcp_pools ADD COLUMN relay_return_hop TEXT' in DB


def test_add_dhcp_pool_persists_relay_return_hop():
    idx = DB.find("def add_dhcp_pool(")
    end = DB.find("\n    def ", idx + 1)
    body = DB[idx:end if end > 0 else idx + 4000]
    assert 'relay_return_hop = (pool_data.get("relay_return_hop") or "").strip() or None' in body
    # INSERT column list includes it.
    assert 'gateway_routes, description, relay_return_hop,' in body


def test_update_dhcp_pool_supports_relay_return_hop_field():
    idx = DB.find("def update_dhcp_pool(")
    end = DB.find("\n    def ", idx + 1)
    body = DB[idx:end if end > 0 else idx + 4000]
    assert '"relay_return_hop": "relay_return_hop",' in body


# --- Client UI: DHCPPoolDialog exposes the field --------------------


def test_dhcp_pool_dialog_has_relay_return_hop_input():
    idx = UI.find("class DHCPPoolDialog(")
    end = UI.find("\nclass ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 8000]
    assert 'v0.5.245 (audit U relay-mode)' in body
    assert 'self.relay_return_hop_edit = QLineEdit(' in body
    # Field is added to the form.
    assert '"Relay Return-Hop:"' in body


def test_dhcp_pool_dialog_payload_emits_relay_return_hop():
    idx = UI.find("def get_payload(")
    end = UI.find("\n    def ", idx + 1)
    body = UI[idx:end if end > 0 else idx + 3000]
    assert '"relay_return_hop": self.relay_return_hop_edit.text().strip()' in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 245)
