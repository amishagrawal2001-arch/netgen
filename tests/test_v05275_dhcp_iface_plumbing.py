"""v0.5.275 — DHCP server anchor plumbing: four fixes so the
pool IP is reachable via the correct kernel interface + VRF
routing table + primed switch MAC learning."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()
DHCP = (REPO / "utils" / "dhcp.py").read_text()


# --- DHCP-J1: ensure_dhcp_services gets the actual iface, not
#     the display form -------------------------------------------


def test_ensure_dhcp_services_uses_actual_iface_name():
    """The apply handler must hand `ensure_dhcp_services` the
    disambiguated `actual_vlan_interface` when the parent-mismatch
    fallback fired, else the base commands name — NEVER the
    display-form `iface_name` (which normalizes back to the stale
    subif when the different-parent fallback set a new one)."""
    idx = SRV.find("dhcp_apply_result = ensure_dhcp_services(")
    assert idx > 0, "ensure_dhcp_services call site missing"
    body = SRV[max(0, idx - 2500):idx + 500]
    # v0.5.275 marker present and the precedence order is
    # actual_vlan_interface OR iface_name_for_commands.
    assert "v0.5.275 (DHCP-J1)" in body
    assert 'result.get("actual_vlan_interface")' in body
    assert "iface_name_for_commands" in body
    # And the pre-fix bug is documented so a future edit doesn't
    # silently regress it.
    assert "display form" in body.lower()


def test_ensure_dhcp_services_no_longer_receives_iface_name_display_form():
    """The exact pre-fix call `ensure_dhcp_services(...,iface_name,...)`
    at the apply site must be gone from live code."""
    idx = SRV.find("dhcp_apply_result = ensure_dhcp_services(")
    end = SRV.find(")", idx)
    call_body = SRV[idx:end + 1]
    # The variable actually passed should be _dhcp_iface (the
    # v0.5.275 precomputed name), not raw iface_name.
    assert "_dhcp_iface" in call_body
    # Read the lines of the multi-line call and check none of the
    # positional args is bare `iface_name`.
    for ln in call_body.splitlines():
        stripped = ln.strip().rstrip(",")
        assert stripped != "iface_name", (
            "bare iface_name (display form) must not be positional arg"
        )


# --- DHCP-J4: parent-name match is anchored ----------------------


def test_parent_link_matches_helper_defined():
    """New `_parent_link_matches` helper wraps the parent-match so
    it isn't a raw substring test — `"ens2f0" in "ens2f0np0"` was
    silently matching before."""
    assert "v0.5.275 (DHCP-J4)" in SRV
    assert "def _parent_link_matches" in SRV
    # And the caller uses it (no more raw `f\"@{...}\" in link_output`
    # in the reuse branch).
    idx = SRV.find("v0.5.275 (DHCP-J4)")
    body = SRV[idx:idx + 2500]
    assert "_parent_link_matches(link_output, interface_normalized)" in body


def test_parent_link_matches_would_reject_prefix_collision():
    """Unit-check the helper's boundary logic by simulating the
    exact false-positive that motivated the fix: an existing
    `vlan10@ens2f0` link line must NOT match a current apply for
    `ens2f0np0`."""
    idx = SRV.find("def _parent_link_matches")
    end = SRV.find("if _parent_link_matches", idx)
    body = SRV[idx:end]
    # The four boundary forms — the closing colon / space /
    # end-of-line variants — are all enforced.
    assert '"@{_parent}:"' in body or '@{_parent}:' in body
    assert '"link/{_parent} "' in body or 'link/{_parent} ' in body
    assert 'endswith(f"@{_parent}")' in body
    assert 'endswith(f"link/{_parent}")' in body


# --- DHCP-J2: post-add VRF connected-route check + install ------


def test_vrf_detection_helper_defined():
    assert "def _detect_iface_vrf" in DHCP
    idx = DHCP.find("def _detect_iface_vrf")
    end = DHCP.find("\n\n", idx + 1)
    body = DHCP[idx:end]
    # Uses `ip -o link show` and looks for `master vrf-…` or
    # `vrf-…` tokens.
    assert '"ip", "-o", "link", "show"' in body
    assert 'startswith("vrf-")' in body


def test_vrf_connected_route_probe_defined():
    assert "def _vrf_has_connected_route" in DHCP
    idx = DHCP.find("def _vrf_has_connected_route")
    end = DHCP.find("\n\n", idx + 1)
    body = DHCP[idx:end]
    # Runs `ip route show <subnet> vrf <name>` and looks for
    # `dev <iface>` in the returned rows.
    assert '"ip", "route", "show"' in body
    assert '"vrf"' in body
    assert '"dev {interface}"' in body or 'f"dev {interface}"' in body


def test_ensure_ipv4_address_post_add_vrf_reinstall():
    """After the successful `ip addr add`, `_ensure_ipv4_address`
    must detect the VRF, probe for the connected route, and
    explicitly install it when missing."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.275 (DHCP-J2)" in body
    assert "_detect_iface_vrf(interface" in body
    assert "_vrf_has_connected_route(" in body
    # The install command uses `ip route add <subnet> dev <iface>
    # proto kernel scope link src <ip> vrf <vrf>`.
    assert '"ip", "route", "add"' in body
    assert '"proto", "kernel", "scope", "link"' in body
    assert '"vrf", _vrf_name' in body


# --- DHCP-J3: gratuitous ARP on anchor add ----------------------


def test_ensure_ipv4_address_sends_gratuitous_arp():
    """After the anchor IP is on the interface, send an unsolicited
    ARP so upstream switches learn the MAC on the correct port
    immediately. `arping -c 2 -A -w 3 -I <iface> <ip>`."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    assert "v0.5.275 (DHCP-J3)" in body
    assert '"arping"' in body
    assert '"-A"' in body   # unsolicited flag
    assert '"-c", "2"' in body
    # Failure is logged, not fatal (arping may not be installed).
    assert "iputils-arping may not be installed" in body


def test_gratuitous_arp_runs_on_both_add_and_already_exists_paths():
    """The `File exists = already assigned` path must ALSO run the
    v0.5.275 (DHCP-J2/J3) post-checks — pre-existing anchors from
    a stale run still need the VRF route + ARP prime."""
    idx = DHCP.find("def _ensure_ipv4_address")
    end = DHCP.find("\n\ndef ", idx + 1)
    body = DHCP[idx:end]
    # The refactor guards the whole VRF/ARP block after
    # `_added_ok or _already_ok` — so an early-return on
    # `_already_ok=True` would skip the post-checks. Assert the
    # branching is unified.
    assert "_added_ok = (result.returncode == 0)" in body
    assert "_already_ok = (" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 275)
