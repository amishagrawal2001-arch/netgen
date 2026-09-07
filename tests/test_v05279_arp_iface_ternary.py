"""v0.5.279 — Fix operator-precedence bug in v0.5.278 ARP-H3
arp-warm interface derivation. When VLAN is set, arping must
target vlan<ID> (the tagged sub-interface) — the pre-fix
ternary preferred `server_interface` when it was set, so if
that field held the parent NIC (`ens1f0`), arping went out
untagged and never reached the switch's IRB.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()


def _endpoint_body():
    idx = SRV.find("# Check gateway connectivity")
    end = SRV.find("# v0.5.193: `requires_ipv6`", idx)
    return SRV[idx:end]


# --- ARP-H6: clean if-block replaces the broken ternary ----------


def test_arp_h6_marker_present():
    assert "v0.5.279 (ARP-H6)" in _endpoint_body()


def test_vlan_devices_prefer_vlan_subif_over_server_interface():
    """When a device has a non-zero VLAN, arping MUST target
    `vlan<ID>` (the tagged sub-interface) — NOT the raw
    server_interface, which is often the parent NIC and would
    send untagged frames the switch's IRB never sees."""
    body = _endpoint_body()
    # The v0.5.279 rewrite uses a clear if-block.
    assert 'if _vlan and _vlan != "0":' in body
    # In the vlan-set branch, only vlan<id> is used.
    idx = body.find('if _vlan and _vlan != "0":')
    end = body.find("else:", idx)
    vlan_branch = body[idx:end]
    assert '_iface_for_arp = f"vlan{_vlan}"' in vlan_branch
    # `server_interface` must NOT appear in the vlan-set branch
    # (that was the pre-fix regression path).
    live_lines = [
        ln for ln in vlan_branch.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any(
        "server_interface" in ln for ln in live_lines
    ), (
        "vlan-set branch must not consult server_interface — that "
        "was the v0.5.278 bug that put arping on the wrong iface"
    )


def test_untagged_devices_still_use_server_interface():
    """Devices without a VLAN (vlan == "0" or unset) keep using
    server_interface as the arping target — that's the untagged
    path and it was working."""
    body = _endpoint_body()
    idx = body.find('if _vlan and _vlan != "0":')
    else_idx = body.find("else:", idx)
    else_end = body.find("_warm_kind", else_idx)
    else_branch = body[else_idx:else_end]
    assert 'device.get("server_interface")' in else_branch


def test_broken_ternary_is_gone():
    """The pre-fix compound ternary must be gone from LIVE code.
    Its shape was:
        (device.get("server_interface") or f"vlan{...}")
        if device.get("vlan") ...
        else device.get("server_interface")

    Detect by looking for the specific structural fingerprint:
    a `server_interface` followed by `or f"vlan{`."""
    body = _endpoint_body()
    live_lines = [
        ln for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    # No live line combines `server_interface` and `or f"vlan{`
    # on the same or adjacent lines (that was the broken form).
    stitched = "\n".join(live_lines)
    # The v0.5.279 fix separates the two into explicit branches;
    # the broken combined form must not resurface.
    assert 'server_interface")\n                        or f"vlan{' not in stitched, (
        "the v0.5.278 broken ternary shape has reappeared"
    )


# --- Rationale documented in comments ----------------------------


def test_rationale_comment_calls_out_untagged_bug():
    """The comment must explain the bug so a future edit that
    'simplifies' the if-block back into a ternary doesn't
    reintroduce the same mistake."""
    body = _endpoint_body()
    idx = body.find("v0.5.279 (ARP-H6)")
    comment = body[idx:idx + 1500]
    assert "untagged" in comment.lower() or "UNTAGGED" in comment
    assert "trunk" in comment.lower() or "IRB" in comment


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 279)
