"""v0.5.239 — DHCP teardown leaks (container + interface anchor).

Operator on 2026-08-31 (post-v0.5.238 upgrade) reported TWO teardown
bugs that survived the entire 35-finding DHCP audit + the follow-on
9-finding audit:

  1. Disabling DHCP via Edit (unchecking the DHCP protocol row and
     Apply) stopped dnsmasq / dhclient inside the container but did
     NOT remove the `dhcp-client-<uuid>` / `dhcp-server-<uuid>`
     container itself. Docker ps kept showing it as (healthy) an
     hour later — orphan container leak.

  2. Removing a DHCP server device left the pool ANCHOR IPv4
     address (192.168.30.1/24) on the vlan interface — even though
     v0.5.235 wired `_remove_ipv4_address` into stop_dhcp_server.
     Root cause: the anchor-cleanup loop only read
     `dhcp_cfg.get("pool_networks")`. When the operator Detached
     the pool before Remove — a very common flow — the Detach path
     scrubbed pool_networks from dhcp_config, so the anchor sweep
     had no addresses to try and quietly leaked the IP past
     device removal.

Fixes:
  - Edit-disable branch of /api/device/apply now calls
    `_stop_dhcp_container(remove=True)` for the previous mode,
    matching /api/device/remove's behavior.
  - stop_dhcp_server's anchor sweep now derives candidates from
    ALL known sources (pool_networks, additional_pools,
    pool_start/end, gateway), and intersects against the
    interface's CURRENT IPv4 addresses so we never over-delete.
  - /api/device/remove adds a final defensive sweep that also
    picks up any surviving `.1/24` IP on the interface at Remove
    time — the deterministic pattern that _ensure_ipv4_address
    emits — so the anchor still gets cleared even when every
    dhcp_cfg field is empty.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()
SERVER = (REPO / "run_tgen_server.py").read_text()


# --- Bug 1: container leak on disable-DHCP-via-Edit ------------------


def test_disable_dhcp_edit_removes_dhcp_container():
    """The Edit-time disable-DHCP branch must remove the container
    after stopping the daemon (mirror /api/device/remove)."""
    # The branch lives in the /api/device/apply handler in
    # run_tgen_server.py. Landmark on the pre-fix stop calls is the
    # "Disable-DHCP: stop daemons" log line; the new container-remove
    # block sits between the stop call and that except clause.
    idx = SERVER.find("Disable-DHCP: stop daemons")
    assert idx > 0, "landmark log line missing from disable-DHCP branch"
    body = SERVER[max(0, idx - 3000):idx + 500]
    assert "v0.5.239 (audit U-teardown-1)" in body
    assert "_stop_dhcp_container" in body
    assert "remove=True" in body
    # Must run for both prev-modes.
    assert 'if _prev_mode in ("server", "client"):' in body


# --- Bug 2: anchor IP leak past device Remove ------------------------


def test_collect_anchor_candidates_helper_exists():
    assert "def _collect_ipv4_anchor_candidates(" in DHCP
    idx = DHCP.find("def _collect_ipv4_anchor_candidates(")
    body = DHCP[idx:idx + 3500]
    # All the metadata sources we must scan.
    assert 'dhcp_cfg.get("pool_networks")' in body
    assert '_normalize_additional_pools(dhcp_cfg.get("additional_pools"))' in body
    assert 'dhcp_cfg.get("pool_start")' in body
    assert '_gw = dhcp_cfg.get("gateway")' in body
    # Returns (ip, prefix) tuples.
    assert "anchors.add((str(hosts[0]), str(net.prefixlen)))" in body


def test_iface_ipv4_addresses_helper_exists():
    assert "def _iface_ipv4_addresses(" in DHCP
    idx = DHCP.find("def _iface_ipv4_addresses(")
    body = DHCP[idx:idx + 1500]
    assert "ip -4 -o addr show dev" in body
    # Returns list of (ip, prefix) tuples.
    assert 'out.append((ip, pfx))' in body


def test_remove_matching_anchors_helper_intersects_before_deleting():
    """The safety gate: never `ip addr del` an address that isn't
    actually assigned to the interface."""
    assert "def _remove_matching_ipv4_anchors(" in DHCP
    idx = DHCP.find("def _remove_matching_ipv4_anchors(")
    body = DHCP[idx:idx + 2500]
    assert "_current = {(ip, pfx) for ip, pfx in _iface_ipv4_addresses(" in body
    assert "if not _match:" in body
    assert "continue" in body


def test_stop_dhcp_server_uses_new_sweep_helper():
    """The v0.5.235 pool_networks-only loop must be replaced by the
    new multi-source sweep."""
    idx = DHCP.find("def stop_dhcp_server(")
    end = DHCP.find("\ndef ", idx + 1)
    body = DHCP[idx:end if end > 0 else idx + 20000]
    assert "v0.5.239 (audit U-teardown-2)" in body
    assert "_collect_ipv4_anchor_candidates(dhcp_cfg)" in body
    assert "_remove_matching_ipv4_anchors(" in body
    # The pre-fix single-source loop is gone.
    assert 'dhcp_cfg.get("pool_networks") or [] if dhcp_cfg else []' not in body


def test_remove_device_has_defensive_anchor_sweep():
    """/api/device/remove must sweep stray .1/24 anchors even if
    every dhcp_cfg field is empty at Remove time."""
    idx = SERVER.find("v0.5.239 (audit U-teardown-3)")
    assert idx > 0, "remove-time sweep block missing"
    body = SERVER[idx:idx + 6000]
    assert "_dhcp._collect_ipv4_anchor_candidates(dhcp_cfg_for_remove)" in body
    assert "_dhcp._iface_ipv4_addresses(iface_name)" in body
    # The .1 pattern check (deterministic anchor).
    assert "_addr.packed[-1] == 1" in body
    assert "_dhcp._remove_matching_ipv4_anchors(" in body


# --- Metadata --------------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 239)
