"""v0.5.150: RDMA pre-flight check + temporary test-IP lifecycle.

Operator hit "Failed to modify QP to RTR" on a same-host
rocep43s0f0 ↔ rocep43s0f1 setup. Root cause: both kernel ifaces
in the same subnet → kernel routes through `lo` instead of out
the wire → QP can't reach RTR.

v0.5.150 closes this with:

* **`utils/rdma_test_ifaces`** — probe per HCA, allocate test
  CIDRs that don't collide with existing routes, validate
  operator-supplied CIDRs, apply via `ip addr add label
  <iface>:netgen`, cleanup precisely via the label.
* **Server routes** — `/api/rdma/probe`,
  `/api/rdma/test_ifaces/{validate,configure,cleanup,orphans}`.
* **Client** — `RdmaPreflightDialog` with user-editable CIDRs +
  Validate / Apply / Cleanup buttons. Same-subnet trap detector.
* **Both Blast + Topology dialogs** — "🔍 Pre-flight check"
  button that opens the dialog, tracks applied state_id, fires
  cleanup on dialog close.
"""
from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ═════════════════════════════ helpers (pure code) ═════════════════════════


from utils.rdma_test_ifaces import (
    auto_pick_subnets,
    validate_user_ips,
    _label_for,
    _network_for_cidr,
    LABEL_SUFFIX,
)


# ───── auto_pick_subnets


def test_auto_pick_subnets_avoids_existing_routes():
    """Suggested /24s must not overlap any route in the routing
    table. Pre-existing route to 10.42.0.0/16 must push the
    picker past it."""
    routes = [{"dst": "10.42.0.0/16", "dev": "eth0"}]
    pairs = auto_pick_subnets(pair_count=1, avoid_routes=routes)
    assert len(pairs) == 1
    srv_cidr, cli_cidr = pairs[0]
    srv_net = _network_for_cidr(srv_cidr)
    assert srv_net is not None
    # Picker should have walked past 10.42.0.0/24 (overlaps with
    # the /16 above).
    assert not srv_net.overlaps(
        ipaddress.IPv4Network("10.42.0.0/16"))


def test_auto_pick_subnets_pairs_have_same_subnet_different_ips():
    """The point-to-point pair gets ONE subnet with two IPs —
    `.1` and `.2`. Same subnet on different ifaces is the trap on
    a SAME HOST, but for cross-host or cross-port-pair testing
    this is normal."""
    pairs = auto_pick_subnets(pair_count=1, avoid_routes=[])
    srv_cidr, cli_cidr = pairs[0]
    assert _network_for_cidr(srv_cidr) == _network_for_cidr(cli_cidr)
    assert srv_cidr.endswith(".1/24") or srv_cidr.endswith(".0/24")
    assert cli_cidr.endswith(".2/24") or cli_cidr.endswith(".1/24")


def test_auto_pick_subnets_default_block_is_rfc1918():
    """Defaults must come from a RFC 1918 block. 10.42 is the
    intentional choice — uncommon."""
    pairs = auto_pick_subnets(pair_count=1, avoid_routes=[])
    srv_cidr, _ = pairs[0]
    addr = ipaddress.IPv4Network(srv_cidr, strict=False).network_address
    assert addr.is_private


# ───── validate_user_ips


def test_validate_rejects_missing_iface_or_cidr():
    """Empty iface name or empty CIDR → error issue."""
    out = validate_user_ips([
        {"name": "", "cidr": "10.10.0.1/24"},
        {"name": "ens2f0np0", "cidr": ""},
    ])
    assert out["ok"] is False
    msgs = " | ".join(i["message"] for i in out["issues"])
    assert "iface name" in msgs
    assert "missing CIDR" in msgs


def test_validate_rejects_bad_cidr():
    """Garbage CIDR → error issue with a descriptive message."""
    with mock.patch("os.path.isdir", return_value=True):
        out = validate_user_ips([
            {"name": "ens2f0np0", "cidr": "not-a-cidr"},
        ])
    assert out["ok"] is False
    assert any(
        "bad CIDR" in i["message"] for i in out["issues"]
    )


def test_validate_catches_same_subnet_trap():
    """The whole point: two ifaces sharing a subnet on validate
    must fire a hard error explaining the trap."""
    with mock.patch("os.path.isdir", return_value=True), \
         mock.patch("utils.rdma_test_ifaces._iface_ip_addresses",
                    return_value=[]), \
         mock.patch("utils.rdma_test_ifaces._existing_routes",
                    return_value=[]):
        out = validate_user_ips([
            {"name": "ens2f0np0", "cidr": "10.10.0.1/24"},
            {"name": "ens2f1np1", "cidr": "10.10.0.2/24"},
        ])
    assert out["ok"] is False
    trap_msgs = [
        i["message"] for i in out["issues"]
        if i["severity"] == "error" and "same subnet" in i["message"]
    ]
    assert trap_msgs, f"trap issue missing; got {out}"
    assert "routing trap" in trap_msgs[0]


def test_validate_allows_different_subnets():
    """Two ifaces on different subnets → ok."""
    with mock.patch("os.path.isdir", return_value=True), \
         mock.patch("utils.rdma_test_ifaces._iface_ip_addresses",
                    return_value=[]), \
         mock.patch("utils.rdma_test_ifaces._existing_routes",
                    return_value=[]):
        out = validate_user_ips([
            {"name": "ens2f0np0", "cidr": "10.10.0.1/24"},
            {"name": "ens2f1np1", "cidr": "10.20.0.1/24"},
        ])
    assert out["ok"] is True
    assert out["issues"] == []


def test_validate_flags_iface_not_present():
    """Iface must exist in /sys/class/net to be valid."""
    with mock.patch("os.path.isdir", return_value=False):
        out = validate_user_ips([
            {"name": "ens-does-not-exist",
             "cidr": "10.10.0.1/24"},
        ])
    assert out["ok"] is False
    assert any(
        "not present" in i["message"] for i in out["issues"]
    )


def test_validate_warns_overlapping_route():
    """A CIDR overlapping an existing route on a DIFFERENT iface
    is a warning, not an error — operator may want this, but
    they should know it might steal traffic."""
    routes = [{"dst": "10.10.0.0/16", "dev": "eth0"}]
    with mock.patch("os.path.isdir", return_value=True), \
         mock.patch("utils.rdma_test_ifaces._iface_ip_addresses",
                    return_value=[]), \
         mock.patch("utils.rdma_test_ifaces._existing_routes",
                    return_value=routes):
        out = validate_user_ips([
            {"name": "ens2f0np0", "cidr": "10.10.0.5/24"},
        ])
    assert out["ok"] is True  # warning only
    warns = [i for i in out["issues"] if i["severity"] == "warning"]
    assert warns
    assert "overlaps route" in warns[0]["message"]


# ───── label conv


def test_label_short_iface():
    assert _label_for("ens2f0np0") == f"ens2f0np0:{LABEL_SUFFIX}"


def test_label_skipped_for_long_iface():
    """Linux caps labels at 15 chars; the iface prefix MUST equal
    the iface name. If iface is too long, return None so the
    apply layer skips the label (still applies the IP). With the
    v0.5.150 `:ng` suffix, the budget is 12 chars for the iface
    name."""
    # 13+ chars → label would exceed IFNAMSIZ.
    assert _label_for("verylongifname13") is None


# ═════════════════════════════ server routes ═════════════════════════════


SRC_SERVER = (REPO / "run_tgen_server.py").read_text()


def test_route_probe_exists():
    assert '@app.route("/api/rdma/probe"' in SRC_SERVER


def test_route_validate_exists():
    assert '@app.route("/api/rdma/test_ifaces/validate"' in SRC_SERVER


def test_route_configure_exists():
    assert '@app.route("/api/rdma/test_ifaces/configure"' in SRC_SERVER


def test_route_cleanup_exists():
    assert '@app.route("/api/rdma/test_ifaces/cleanup"' in SRC_SERVER


def test_route_orphans_exists():
    assert '@app.route("/api/rdma/test_ifaces/orphans"' in SRC_SERVER


def test_configure_route_validates_first():
    """Configure must call validate_user_ips before apply and
    refuse on any error-severity issue."""
    m = re.search(
        r"def api_rdma_test_ifaces_configure\(\):.*?(?=\n@app\.route)",
        SRC_SERVER, flags=re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "validate_user_ips" in body
    assert "apply_test_config" in body
    # Refuses on validation error.
    assert "validation failed" in body


def test_probe_route_rejects_path_traversal():
    """Defense in depth: the device name is interpolated into a
    sysfs path. Reject `..` / `/` before sysfs reads."""
    m = re.search(
        r"def api_rdma_probe\(\):.*?(?=\n@app\.route)",
        SRC_SERVER, flags=re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert ('"/" in device' in body or "'/' in device" in body)
    assert ('".." in device' in body or "'..' in device" in body)


# ═════════════════════════════ client dialog ═════════════════════════════


SRC_PREFLIGHT = (REPO / "widgets" / "rdma_preflight_dialog.py").read_text()
SRC_BLAST = (REPO / "widgets" / "rdma_blast_flow_dialog.py").read_text()
SRC_TOP = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()


def test_preflight_dialog_class_exists():
    assert "class RdmaPreflightDialog(" in SRC_PREFLIGHT


def test_preflight_has_validate_and_apply_buttons():
    """Both buttons must be present — the operator's request was
    explicit about user flexibility + checking subnet
    configuration first."""
    assert '"Validate"' in SRC_PREFLIGHT
    assert '"Apply (temporary)"' in SRC_PREFLIGHT
    assert '"Clean up applied"' in SRC_PREFLIGHT


def test_preflight_exposes_applied_state_id_for_caller():
    """Caller (Blast/Topology) reads `applied_state_id()` on
    close to know if cleanup needs to happen."""
    assert "def applied_state_id" in SRC_PREFLIGHT


def test_preflight_calls_correct_endpoints():
    """The validate / configure / cleanup paths must POST to the
    right server routes."""
    assert "/api/rdma/probe" in SRC_PREFLIGHT
    assert "/api/rdma/test_ifaces/validate" in SRC_PREFLIGHT
    assert "/api/rdma/test_ifaces/configure" in SRC_PREFLIGHT
    assert "/api/rdma/test_ifaces/cleanup" in SRC_PREFLIGHT


def test_preflight_lets_user_edit_cidr():
    """User flexibility: each row uses a QLineEdit for the CIDR
    so the operator can override the auto-suggested value."""
    assert "QLineEdit" in SRC_PREFLIGHT
    # And the apply path picks up the user's edited text, not a
    # frozen default.
    assert 'r["cidr_edit"].text()' in SRC_PREFLIGHT


def test_preflight_detects_same_subnet_trap_in_verdict():
    """The verdict banner must specifically call out the same-
    subnet trap when it sees two ifaces on one host sharing a
    subnet."""
    assert "Same-subnet trap detected" in SRC_PREFLIGHT


def test_preflight_disable_rp_filter_checkbox_present():
    """rp_filter trips perftest's same-host loopback path; the
    dialog must offer to relax it (with cleanup restoring the
    prior value)."""
    assert "disable_rp_filter" in SRC_PREFLIGHT
    assert "rp_filter" in SRC_PREFLIGHT


# ═════════════════════════════ Blast wiring ══════════════════════════════


def test_blast_dialog_has_preflight_button():
    assert "🔍 Pre-flight check" in SRC_BLAST
    assert "_on_preflight_clicked" in SRC_BLAST


def test_blast_tracks_applied_state_ids():
    """The dialog must remember the state_id(s) so the closeEvent
    cleanup can fire them."""
    assert "_preflight_state_ids" in SRC_BLAST


def test_blast_cleans_up_on_close():
    """closeEvent must POST cleanup for every applied state_id."""
    assert "_cleanup_preflight_state_ids" in SRC_BLAST
    assert "/api/rdma/test_ifaces/cleanup" in SRC_BLAST


# ═════════════════════════════ Topology wiring ═══════════════════════════


def test_topology_dialog_has_preflight_button():
    assert "🔍 Pre-flight check" in SRC_TOP
    assert "_on_preflight_clicked" in SRC_TOP


def test_topology_cleans_up_on_close():
    """closeEvent must POST cleanup for every applied state_id
    per TG URL."""
    assert "_preflight_state_ids" in SRC_TOP
    assert "/api/rdma/test_ifaces/cleanup" in SRC_TOP
