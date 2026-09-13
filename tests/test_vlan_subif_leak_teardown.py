"""VLAN sub-interface leak on device remove + Apply self-heal.

Bug the operator surfaced on srv06:
  device5 Apply → "VLAN interface vlan20 exists but is linked to a
  different parent interface. Failed to create alternative interface
  name. Error: RTNETLINK answers: File exists"

Even after the operator removed the prior device that had used vlan20,
the kernel sub-interface stuck around forever because
/api/device/remove cleaned up FRR container + VXLAN + DHCP + the DB
row but never ran `ip link del vlan<N>`. The next Apply for the same
vlan on a different parent hit the "linked to a different parent"
branch in the Apply path, then the alt-name path, then errored out
with File exists.

Fix in two parts:

  1. Add `_teardown_stale_vlan_subif(vlan, parent, exclude_device_ids)`
     helper in run_tgen_server.py. Reference-counts against
     device_db.get_all_devices — a vlan sub-interface shared by
     multiple devices (same vlan+parent) isn't torn down until the
     last user is removed.
  2. Call it from remove_device right after the DB delete.
  3. Add an Apply-path self-heal that, when it sees a stale vlan<N>
     with NO device in the DB claiming it, sweeps + re-checks so
     the downstream branching creates fresh on our parent instead
     of falling into the alt-name-then-error path.

Source-level checks only — kernel `ip link` behavior is a lab-server
runtime concern.
"""
from __future__ import annotations

from pathlib import Path

_SERVER_PY = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER_PY.read_text()


def test_teardown_helper_defined():
    assert "def _teardown_stale_vlan_subif(" in _src()


def test_teardown_helper_reference_counts_before_deletion():
    """The helper MUST refuse to delete when another device claims
    the same (vlan_id, parent_iface) pair. Otherwise a 3-device
    vlan10@ens2f0np0 setup would lose vlan10 the first time any
    of the three devices is removed."""
    src = _src()
    marker = "def _teardown_stale_vlan_subif("
    idx = src.index(marker)
    # Grab helper body up to the next top-level def (~150 lines below).
    tail = src[idx:idx + 6000]
    assert "device_db.get_all_devices()" in tail, (
        "helper must query the DB for reference counting"
    )
    # The still-in-use guard emits kept_reason and returns early.
    assert 'kept_reason' in tail
    assert 'still-in-use-by' in tail


def test_teardown_helper_deletes_both_naming_variants():
    """A device that hit the collision path uses the alt name
    `vlan<N>-<parent>` — so the teardown must consider BOTH the
    simple `vlan<N>` name AND the alt name. v0.5.305 routes the
    alt-name computation through `_vlan_alt_name` so it matches
    the truncated on-wire form (Apply-create truncates to
    IFNAMSIZ 15). Bare f-string in v0.5.304 missed the truncated
    variant → operator saw the leak on srv06 as `vlan20-ens2f0np`
    even after the v0.5.304 teardown ran."""
    src = _src()
    idx = src.index("def _teardown_stale_vlan_subif(")
    tail = src[idx:idx + 6000]
    # Simple name candidate.
    assert 'f"vlan{vlan_id}"' in tail
    # Alt candidate derived from the shared helper — NOT a raw
    # f-string (v0.5.305 fix).
    assert "_vlan_alt_name(vlan_id, parent_iface)" in tail
    assert '"ip", "link", "del", candidate' in tail


def test_teardown_helper_guards_against_wrong_parent():
    """Belt-and-suspenders: only delete the kernel sub-interface if
    its actual parent link matches ours. Prevents zapping a same-
    named vlan sitting on a different NIC (shouldn't happen if DB
    is authoritative, but DB isn't authoritative for kernel state)."""
    src = _src()
    idx = src.index("def _teardown_stale_vlan_subif(")
    tail = src[idx:idx + 6000]
    # Mirror of _parent_link_matches in the Apply path.
    assert 'f"@{parent_iface}:"' in tail
    assert 'f"link/{parent_iface} "' in tail


def test_remove_device_calls_teardown_after_db_delete():
    src = _src()
    # The remove_device endpoint must call the helper AFTER the DB
    # row is gone (so the reference-count query naturally sees zero
    # remaining rows for this vlan+parent pair).
    remove_marker = "def remove_device():"
    end_marker = "@app.route(\"/api/devices/clear_all\""
    body = src[src.index(remove_marker):src.index(end_marker)]
    # Order-sensitive: db_removed must appear before the teardown call.
    db_del_idx = body.index("device_db.remove_device(device_id)")
    teardown_idx = body.index("_teardown_stale_vlan_subif(")
    assert db_del_idx < teardown_idx, (
        "vlan teardown must happen AFTER the DB delete so the ref-"
        "count query sees the removed device gone"
    )
    # And the removed device_id is excluded from the ref count (belt-
    # and-suspenders — the DB row is already gone by this point, but
    # exclusion protects the self-heal call path too).
    assert "exclude_device_ids=[device_id]" in body


def test_remove_device_surfaces_teardown_result_in_response():
    src = _src()
    body = src[src.index("def remove_device():"):src.index("@app.route(\"/api/devices/clear_all\"")]
    # Operators debugging "why is vlan20 still there?" get a machine-
    # readable report back in the /api/device/remove response.
    assert '_payload["vlan_teardown"] = vlan_teardown_report' in body


def test_apply_path_self_heal_before_branching():
    src = _src()
    # The self-heal block sits between the initial `check_result`
    # and the `if check_result.returncode != 0:` branching so that,
    # after a successful sweep + re-check, the downstream code
    # naturally takes the "doesn't exist" path and creates fresh
    # on OUR parent — no error, no alt-name shenanigans.
    marker = "v0.5.304 self-heal"
    idx = src.find(marker)
    assert idx != -1, "Apply-path self-heal marker missing"
    # The block must be in the Apply path (near line 4670 area),
    # not somewhere else — cheap sanity via a nearby anchor.
    window = src[max(0, idx - 800):idx + 3000]
    assert "check_result = subprocess.run" in window
    assert "device_db.get_all_devices()" in window
    # v0.5.305: alt-name derived from shared helper (was raw
    # f-string; missed truncated on-wire form).
    assert "_vlan_alt_name(vlan, interface_normalized)" in window


def test_apply_self_heal_fails_closed_on_db_exception():
    """If the DB query raises, treat the vlan as claimed and DON'T
    delete. Better to give the operator the (confusing) File-exists
    error and leave state intact than to silently wipe an interface
    that might still be in use because we couldn't check."""
    src = _src()
    idx = src.index("v0.5.304 self-heal")
    window = src[idx:idx + 2500]
    # Except handler right after the get_all_devices call sets
    # _any_claim = True.
    assert "_any_claim = True" in window
    assert "except Exception" in window


def test_apply_self_heal_only_fires_when_no_device_claims_vlan_id():
    """Guard: if ANY device in the DB claims vlan<N> (on any parent),
    the kernel sub-interface is in use — do NOT delete. Falls
    through to the original alt-name path unchanged."""
    src = _src()
    idx = src.index("v0.5.304 self-heal")
    window = src[idx:idx + 2500]
    # The `if not _any_claim:` guard gates the sweep + re-check.
    assert "if not _any_claim:" in window


def test_vlan_alt_name_helper_matches_apply_truncation():
    """v0.5.305: the alt-name helper MUST produce the same truncated
    form the Apply-create path puts on the wire, otherwise the
    teardown/self-heal sweep silently misses the leaked interface.
    Operator hit this on srv06 with vlan20 leaked as
    `vlan20-ens2f0np@ens2f0np0` — parent `ens2f0np0` (9 chars)
    combined with `vlan20-` (7 chars) = 16 chars > IFNAMSIZ 15,
    so Apply-create had truncated to `vlan20-ens2f0np` (15) but the
    v0.5.304 teardown loop used the raw f-string `vlan20-ens2f0np0`
    and never matched, leaking the interface indefinitely."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_srv_mod", str(_SERVER_PY),
    )
    # Just source-check the function's shape — importing the full
    # module pulls in Flask + docker + everything else.
    src = _src()
    assert "def _vlan_alt_name(" in src
    # Extract the function body and eval() it in a scratch namespace
    # so we can hit the actual truncation math.
    idx = src.index("def _vlan_alt_name(")
    end = src.index("def _teardown_stale_vlan_subif(", idx)
    fn_src = src[idx:end]
    ns = {}
    exec(fn_src, ns)
    _vlan_alt_name = ns["_vlan_alt_name"]
    # The operator's actual srv06 case.
    assert _vlan_alt_name("20", "ens2f0np0") == "vlan20-ens2f0np"
    # No-truncation case (short parent).
    assert _vlan_alt_name("20", "eth0") == "vlan20-eth0"
    # Boundary — exactly 15 chars, no truncation.
    assert _vlan_alt_name("20", "ens2f0np") == "vlan20-ens2f0np"
    # Long parent, long vlan — still fits ≤15 after truncation.
    assert len(_vlan_alt_name("100", "veryLongParent")) <= 15
    assert _vlan_alt_name("100", "veryLongParent") == "vlan100-veryLon"
    # Vlan id alone > 15 - "vlan" - "-" budget → fall back to simple.
    # "vlan99999-" = 10 chars, leaves 5 for parent → truncated.
    assert _vlan_alt_name("99999", "somelongname") == "vlan99999-somel"


def test_teardown_helper_uses_alt_name_helper():
    """Belt-and-suspenders: the teardown loop's candidate list must
    include the truncated alt-name (via _vlan_alt_name), not the raw
    f-string that would miss the truncated on-wire form."""
    src = _src()
    idx = src.index("def _teardown_stale_vlan_subif(")
    tail = src[idx:idx + 6000]
    # The candidate list construction must go through _vlan_alt_name.
    assert "_vlan_alt_name(vlan_id, parent_iface)" in tail
    # And guard against duplicates when the alt equals the simple
    # name (fallback case) — otherwise we'd try to delete `vlanN`
    # twice.
    assert "if _alt not in _candidates" in tail


def test_apply_self_heal_uses_alt_name_helper():
    """Same fix in the Apply-path self-heal loop — v0.5.304 used
    a raw f-string here too and missed truncated leaks."""
    src = _src()
    idx = src.index("v0.5.304 self-heal")
    window = src[idx:idx + 3000]
    assert "_vlan_alt_name(vlan, interface_normalized)" in window


def test_apply_create_uses_alt_name_helper():
    """Consolidation: Apply-create originally inlined the truncation
    math; v0.5.305 routes it through _vlan_alt_name so all three
    sites (create, self-heal, teardown) derive the alt name from
    one authoritative place."""
    src = _src()
    # The old inline truncation math is gone — no more
    # `max_vlan_len = len(f"vlan{vlan}-")` at the Apply-create site.
    assert 'max_vlan_len = len(f"vlan{vlan}-")' not in src
    # And the create-branch call to _vlan_alt_name exists.
    assert "vlan_name_with_parent = _vlan_alt_name(" in src


def test_server_ast_parses():
    """v0.5.300 lesson — never ship an edit without ast.parse."""
    import ast
    ast.parse(_src())
