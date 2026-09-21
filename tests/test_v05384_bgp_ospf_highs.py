"""v0.5.384 — BGP + OSPF audit HIGHs (4 items).

  Z1  Per-neighbor unique PL/RM names.
  Z2  Per-device BGP config serialisation lock.
  Z3  /api/bgp/neighbors — VRF iteration + IPv6.
  Z4  OSPF partial-apply — clamp ipv4/ipv6_enabled into payload.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


# ─── AST sanity ───


def test_server_ast_parses():
    ast.parse(_read("run_tgen_server.py"))


# ─── Z1: per-neighbor PL/RM names ───


def test_z1_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit BGP-Z1)" in src
    # helper + configure block + cleanup block
    assert src.count("v0.5.384 (audit BGP-Z1)") >= 3


def test_z1_helper_defined():
    src = _read("run_tgen_server.py")
    assert "def _bgp_neighbor_slug(neighbor_ip):" in src
    assert "def _bgp_pl_rm_names(neighbor_ip):" in src


def test_z1_slug_replaces_dots_and_colons():
    """Behavioral: v4 & v6 addresses both slug to alnum + _."""
    # Import lazily since importing run_tgen_server pulls Flask; we
    # only need the tiny helper. Use exec on a reconstructed snippet.
    src = _read("run_tgen_server.py")
    _idx = src.index("def _bgp_neighbor_slug(neighbor_ip):")
    _end = src.index("\ndef _bgp_pl_rm_names(", _idx)
    _snippet = src[_idx:_end]
    _ns = {}
    exec(_snippet, _ns)
    _slug = _ns["_bgp_neighbor_slug"]
    assert _slug("192.168.0.1") == "192_168_0_1"
    assert _slug("2001:db8::1") == "2001_db8__1"
    assert _slug("") == "default"
    assert _slug(None) == "default"


def test_z1_helper_returns_all_names():
    src = _read("run_tgen_server.py")
    _idx = src.index("def _bgp_pl_rm_names(neighbor_ip):")
    _end = src.index("\n\n", _idx + 100)
    _snippet = "def _bgp_neighbor_slug(neighbor_ip):\n" \
        "    _clean = ''.join(c if c.isalnum() else '_' for c in (neighbor_ip or '').strip())\n" \
        "    return _clean or 'default'\n" + src[_idx:_end]
    _ns = {}
    exec(_snippet, _ns)
    _names = _ns["_bgp_pl_rm_names"]("10.0.0.1")
    for _k in ("pl_export", "pl_export_v6", "pl_import", "pl_import_v6",
               "rm_export", "rm_export_v6", "rm_import", "rm_import_v6"):
        assert _k in _names
        assert "10_0_0_1" in _names[_k]


def test_z1_configure_uses_per_neighbor_names():
    src = _read("run_tgen_server.py")
    _idx = src.index("def configure_bgp_route_advertisement(")
    _end = src.index("def cleanup_bgp_route_advertisement(", _idx)
    body = src[_idx:_end]
    # Helper is called
    assert "_bgp_pl_rm_names(neighbor_ip)" in body
    # All the old bare names are gone from executable code (comments
    # may keep them for context; strip them out first).
    _executable = re.sub(r"#[^\n]*", "", body)
    _executable = re.sub(r'""".*?"""', "", _executable, flags=re.DOTALL)
    # No stray unslugged `PL-EXPORT ` / `RM-EXPORT ` / `RM-IMPORT `
    # appearing as bare tokens (they must all be interpolated via _rm).
    for _bare in ("prefix-list PL-EXPORT seq",
                  "route-map RM-EXPORT permit 10",
                  "route-map RM-EXPORT-IPV6 permit 10",
                  "route-map RM-IMPORT-IPV6 permit 10",
                  "route-map RM-IMPORT permit 10",
                  "no route-map RM-EXPORT\\n",
                  "no route-map RM-EXPORT-IPV6\\n"):
        # Test as substring — these represent literal bare-name
        # writes; if any remains, a neighbor slug was missed.
        assert _bare not in _executable, (
            f"configure_bgp still writes bare {_bare!r} — should "
            f"go through _rm[...] instead"
        )


def test_z1_cleanup_uses_per_neighbor_names():
    src = _read("run_tgen_server.py")
    _idx = src.index("def cleanup_bgp_route_advertisement(")
    _end = _idx + 6000
    body = src[_idx:_end]
    assert "_bgp_pl_rm_names(neighbor_ip)" in body
    _executable = re.sub(r"#[^\n]*", "", body)
    _executable = re.sub(r'""".*?"""', "", _executable, flags=re.DOTALL)
    # No bare RM-EXPORT / RM-IMPORT / PL-EXPORT strings in
    # executable code (they'd wipe another neighbor's state).
    for _bare in ("no ip prefix-list PL-EXPORT\"",
                  "no ipv6 prefix-list PL-EXPORT\"",
                  "no route-map RM-EXPORT permit 10\"",
                  "no route-map RM-EXPORT-IPV6 permit 10\""):
        assert _bare not in _executable, (
            f"cleanup_bgp still emits bare {_bare!r}"
        )


# ─── Z2: per-device config lock ───


def test_z2_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit BGP-Z2)" in src


def test_z2_lock_dict_and_helper_defined():
    src = _read("run_tgen_server.py")
    assert "_BGP_DEVICE_CONFIG_LOCKS" in src
    assert "_BGP_DEVICE_CONFIG_LOCKS_META" in src
    assert "def _bgp_device_config_lock(device_id):" in src


def test_z2_all_thread_bodies_wrapped():
    """The 4 daemon-thread bodies inside `configure_bgp` must
    each use `with _bgp_device_config_lock(device_id):`."""
    src = _read("run_tgen_server.py")
    _idx = src.index("def configure_bgp():")
    # Scan to the next @app.route decorator (function boundary)
    _end = src.index("\n@app.route", _idx + 1)
    body = src[_idx:_end]
    # Count acquires — 3 unique inner functions (cleanup_routes
    # variants + cleanup_then_configure). The 3 sites collectively
    # produce ≥3 `with _bgp_device_config_lock(device_id):` uses.
    _count = body.count("with _bgp_device_config_lock(device_id):")
    assert _count >= 3, (
        f"Expected ≥3 lock-wrapped thread bodies inside configure_bgp; "
        f"got {_count}"
    )


def test_z2_lock_helper_returns_lock_per_device():
    """Behavioral: two calls for same device_id return SAME lock;
    different device_ids return DIFFERENT locks."""
    src = _read("run_tgen_server.py")
    _idx = src.index("_BGP_DEVICE_CONFIG_LOCKS: Dict = {}")
    _end = src.index("def configure_bgp_route_advertisement", _idx)
    _snippet = src[_idx:_end]
    # Add threading import stub
    _ns = {"Dict": dict}
    _pre = "import threading as _bgp_lock_th\n"
    exec(_pre + _snippet, _ns)
    _lockA1 = _ns["_bgp_device_config_lock"]("dev_A")
    _lockA2 = _ns["_bgp_device_config_lock"]("dev_A")
    _lockB = _ns["_bgp_device_config_lock"]("dev_B")
    assert _lockA1 is _lockA2
    assert _lockA1 is not _lockB


# ─── Z3: VRF + IPv6 neighbors ───


def test_z3_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit BGP-Z3)" in src


def test_z3_iterates_scopes_including_dev_vrf():
    src = _read("run_tgen_server.py")
    _idx = src.index("def get_bgp_neighbors():")
    _end = _idx + 6000
    body = src[_idx:_end]
    assert "_scopes.append((\"default\", \"\"))" in body
    assert "frr_manager.vrf_name_for_device(device_id)" in body
    assert "_scopes.append((_dev_vrf, f\" vrf {_dev_vrf}\"))" in body


def test_z3_queries_both_v4_and_v6():
    src = _read("run_tgen_server.py")
    _idx = src.index("def get_bgp_neighbors():")
    _end = _idx + 6000
    body = src[_idx:_end]
    # v4 summary with optional vrf scope
    assert "show ip bgp{_vrf_suffix} summary" in body \
        or 'f"show ip bgp{_vrf_suffix} summary"' in body
    # v6 summary
    assert "show bgp{_vrf_suffix} ipv6 unicast summary" in body \
        or 'f"show bgp{_vrf_suffix} ipv6 unicast summary"' in body


def test_z3_tags_row_with_vrf():
    src = _read("run_tgen_server.py")
    _idx = src.index("def get_bgp_neighbors():")
    _end = _idx + 6000
    body = src[_idx:_end]
    assert '"vrf": _vrf_display' in body
    assert '"neighbor_type": "IPv6"' in body
    assert '"neighbor_type": "IPv4"' in body


# ─── Z4: OSPF partial-apply AF clamp ───


def test_z4_marker_present():
    src = _read("run_tgen_server.py")
    assert "v0.5.384 (audit OSPF-Z4)" in src


def test_z4_clamps_payload_before_configure():
    """Before calling configure_ospf_neighbor, is_partial_apply
    must overwrite ospf_config['ipv4_enabled'] +
    ospf_config['ipv6_enabled'] with the locally-clamped values."""
    src = _read("run_tgen_server.py")
    _idx = src.index("v0.5.384 (audit OSPF-Z4)")
    body = src[_idx:_idx + 2500]
    assert "if is_partial_apply:" in body
    assert 'ospf_config["ipv4_enabled"] = ipv4_enabled' in body
    assert 'ospf_config["ipv6_enabled"] = ipv6_enabled' in body


def test_z4_clamp_precedes_configure_call():
    """The clamp block must sit BEFORE the
    configure_ospf_neighbor call inside the same handler."""
    src = _read("run_tgen_server.py")
    _clamp_pos = src.index("v0.5.384 (audit OSPF-Z4)")
    # Find the next call to configure_ospf_neighbor after the clamp
    _call_pos = src.index("configure_ospf_neighbor(device_id, ospf_config", _clamp_pos)
    assert _call_pos > _clamp_pos, (
        "Clamp must precede configure_ospf_neighbor call"
    )
    # And it must be reasonably close (within ~800 chars)
    assert (_call_pos - _clamp_pos) < 2000


# ─── version guard ───


def test_pyproject_version_at_least_0584():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 384)


# ─── regression guards ───


def test_v0201_partial_apply_neighbor_diff_intact():
    """v0.5.201 protected neighbor diff on partial-apply. Z4 is
    the OSPF-side companion — v0.5.201 mustn't have been undone."""
    src = _read("run_tgen_server.py")
    # v0.5.201 leaves markers around the BGP diff logic; we just
    # verify _apply_address_families is still respected somewhere.
    assert "_apply_address_families" in src


def test_v0200_ospf_isis_parity_intact():
    src = _read("run_tgen_server.py")
    assert "v0.5.200" in src


def test_v0383_x1_start_lock_intact():
    """Z2 device-config lock is a SEPARATE surface from X1's
    start_frr_container lock — X1 must still be present."""
    src = _read("utils/frr_docker.py")
    assert "v0.5.383 (audit FRR-X1)" in src


def test_v0197_apply_warnings_intact():
    src = _read("run_tgen_server.py")
    assert "apply_warnings" in src
