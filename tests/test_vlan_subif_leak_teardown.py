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
    simple `vlan<N>` name AND the alt name (line 4711 area
    creates `vlan{vlan}-{interface_normalized}`)."""
    src = _src()
    idx = src.index("def _teardown_stale_vlan_subif(")
    tail = src[idx:idx + 6000]
    # Both candidates listed in the sweep loop.
    assert 'f"vlan{vlan_id}"' in tail
    assert 'f"vlan{vlan_id}-{parent_iface}"' in tail
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
    window = src[max(0, idx - 800):idx + 2000]
    assert "check_result = subprocess.run" in window
    assert "device_db.get_all_devices()" in window
    assert 'f"vlan{vlan}-{interface_normalized}"' in window


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


def test_server_ast_parses():
    """v0.5.300 lesson — never ship an edit without ast.parse."""
    import ast
    ast.parse(_src())
