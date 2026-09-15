"""v0.5.340 — `_create_vrf` installs `default via <gw>` in the VRF's
routing table when the operator declared a gateway on the device.

Operator on srv06 2026-09-15: device5 (DHCPv6 relay-mode server on
vlan20, `2001:db8:20::2/64`, serving pool `2001:db8:30::/64` to
vlan40 clients via QFX relay). QFX correctly relay-forwarded
solicits to `2001:db8:20::2`, dnsmasq processed them and sent
ADVERTISE/REPLY back to `2001:db8:30::1` (the QFX's irb.40 IP).

But the VRF's IPv6 routing table had ONLY:
    2001:db8:20::/64 dev vlan20 proto kernel   ← v0.5.332 fix
    fe80::/64        dev vlan20 proto kernel

No default route. So dnsmasq's reply to `2001:db8:30::1` got
"Network is unreachable" and the kernel silently dropped it. Client
on vlan40 never leased despite everything else being right.

Manual `ip -6 route add default via 2001:db8:20::1 dev vlan20 vrf
vrf-31ed2e776f5` unblocked end-to-end (verified: vlan40 got
`2001:db8:30::18a/128` lease).

Fix: extend `_create_vrf` to accept `ipv4_gateway` and
`ipv6_gateway` and install `default via <gw>` in the VRF's table
for each family when the operator declared one. Both call sites in
`start_frr_container` (fresh-launch AND re-apply reconcile) pass
the gateways through from `device_config`. Idempotent — swallows
"File exists" on re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _frr_src():
    return (_REPO / "utils" / "frr_docker.py").read_text()


def test_marker_present():
    assert "v0.5.340 (audit vrf-default-route-missing)" in _frr_src()


def test_create_vrf_signature_accepts_gateways():
    """`_create_vrf` must accept `ipv4_gateway` and `ipv6_gateway`
    optional kwargs so callers can pass the operator's declared
    gateway values."""
    src = _frr_src()
    idx = src.index("def _create_vrf(")
    signature = src[idx:idx + 300]
    assert "ipv4_gateway" in signature
    assert "ipv6_gateway" in signature
    # Must be OPTIONAL — omitting both preserves the v0.5.333
    # reconcile behavior for callers that don't know the gateway.
    assert "Optional[str]" in signature


def test_install_uses_default_via_gateway():
    """The install command must be `ip -[46] route add default via
    <gw> dev <iface> table <vrf_table>`. Matches how v0.5.310/332
    build their route commands."""
    src = _frr_src()
    idx = src.index("v0.5.340 (audit vrf-default-route-missing)")
    body = src[idx:idx + 4000]
    assert '"default"' in body
    assert '"via", str(_fam_gw)' in body
    assert '"table", str(vrf_table)' in body


def test_install_iterates_both_families():
    """The install must iterate `-4` AND `-6` so BOTH families get
    a default route when operator has declared each. Regression
    guard against a future refactor that gates on one family only."""
    src = _frr_src()
    idx = src.index("v0.5.340 (audit vrf-default-route-missing)")
    body = src[idx:idx + 4000]
    # Loop over both flags:
    assert 'for _fam_flag, _fam_gw in (("-4", ipv4_gateway),' in body
    assert '("-6", ipv6_gateway))' in body


def test_install_skips_when_gateway_absent():
    """Passing `None` (operator didn't declare a gateway for that
    family) must skip that family — install nothing. Otherwise
    `ip route add default via None` would raise."""
    src = _frr_src()
    idx = src.index("v0.5.340 (audit vrf-default-route-missing)")
    body = src[idx:idx + 4000]
    assert "if not _fam_gw:" in body
    assert "continue" in body[body.index("if not _fam_gw:"):body.index("if not _fam_gw:") + 200]


def test_install_swallows_file_exists():
    """Re-running `_create_vrf` on an already-configured device
    must not spam warnings — the default route is already there.
    Swallow `File exists` on stderr."""
    src = _frr_src()
    idx = src.index("v0.5.340 (audit vrf-default-route-missing)")
    body = src[idx:idx + 4000]
    assert '"File exists" not in (_dr.stderr or "")' in body


def test_fresh_launch_call_site_passes_gateways():
    """`start_frr_container`'s fresh-launch path (not the reconcile
    branch) must pass gateways from `device_config`."""
    src = _frr_src()
    # Fresh-launch call is unique — it's the one AFTER "vrf_name = "
    fresh_call_idx = src.index("vrf_name = self._create_vrf(")
    body = src[max(0, fresh_call_idx - 500):fresh_call_idx + 600]
    assert "ipv4_gateway=_fresh_v4_gw" in body
    assert "ipv6_gateway=_fresh_v6_gw" in body
    # Reads from device_config['ipv4_gateway'] / ['ipv6_gateway'].
    assert "device_config.get('ipv4_gateway')" in body
    assert "device_config.get('ipv6_gateway')" in body


def test_reconcile_call_site_passes_gateways():
    """v0.5.333's reconcile branch on the already-running path
    must ALSO pass gateways — otherwise re-apply on an existing
    running device wouldn't install the default route (same class
    of bug v0.5.333 fixed for the v0.5.332 connected-route install)."""
    src = _frr_src()
    # Reconcile call uses `_reconcile_iface` as arg.
    reconcile_idx = src.index("_reconcile_vrf = self._create_vrf(")
    body = src[reconcile_idx:reconcile_idx + 600]
    assert "ipv4_gateway=_reap_v4_gw" in body
    assert "ipv6_gateway=_reap_v6_gw" in body


def test_both_call_sites_have_v0_5_340_marker():
    """The two call sites must each note v0.5.340 in a comment so
    a future refactor sees WHY they compute the gateway vars."""
    src = _frr_src()
    # Both branches have the marker in their prep-comments.
    v340_hits = src.count("v0.5.340")
    # At least 3: helper docstring + 2 call sites + main install site
    assert v340_hits >= 4, f"expected v0.5.340 marker in ≥4 places, got {v340_hits}"


def test_v0_5_332_and_v0_5_310_markers_still_intact():
    """v0.5.340 is stacked on the connected/local route installs —
    those must still be in place."""
    src = _frr_src()
    assert "v0.5.332 (audit vrf-connected-route-missing)" in src
    assert "v0.5.310 (audit vrf-local-host-route-drift)" in src


def test_frr_docker_ast_parses():
    import ast
    ast.parse(_frr_src())
