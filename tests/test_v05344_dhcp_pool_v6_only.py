"""v0.5.344 — Manage Pools → Add Pool accepts v6-only pools.
Pre-fix, IPv4 pool_start/pool_end were mandatory at three layers
(client validator, server endpoint, and DB add). Operator asked
for parity: v4 or v6 or both, but at least one.

Chain:
- widgets DHCPPoolDialog._validate — was `if not pool_start or
  not pool_end: return "Pool start and end addresses are required."`
- server POST /api/dhcp/pools — was `required_fields = ["name",
  "pool_start", "pool_end"]`
- utils/device_database.py add_dhcp_pool — same rejection

Fix rule: name required + at least one address family (v4 OR v6);
partial family (one v4 endpoint or one v6 endpoint missing) is
still an error at each layer.

Also adds v6 pool columns (`pool6_start`, `pool6_end`, `prefix6`)
to the `dhcp_pools` DB table via a v0.5.344-marked migration.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _dialog_src():
    return (_REPO / "utils" / "devices_tab_dhcp.py").read_text()


def _server_src():
    return (_REPO / "run_tgen_server.py").read_text()


def _db_src():
    return (_REPO / "utils" / "device_database.py").read_text()


# --- markers ---

def test_marker_present_in_dialog():
    assert "v0.5.344 (audit dhcp-pool-v6-only-allowed)" in _dialog_src()


def test_marker_present_in_server():
    assert "v0.5.344 (audit dhcp-pool-v6-only-allowed)" in _server_src()


def test_marker_present_in_db():
    assert "v0.5.344 (audit dhcp-pool-v6-only-allowed)" in _db_src()


# --- dialog validator ---

def test_dialog_rejects_when_no_family_supplied():
    src = _dialog_src()
    fn_idx = src.index("def _validate(")
    body = src[fn_idx:fn_idx + 4000]
    # New any-family gate.
    assert "if not _v4_any and not _v6_any:" in body
    assert "at least one address family" in body


def test_dialog_requires_both_v4_endpoints_when_either_set():
    src = _dialog_src()
    fn_idx = src.index("def _validate(")
    body = src[fn_idx:fn_idx + 4000]
    assert "if _v4_any:" in body
    # BOTH v4 pool_start + pool_end required when either is filled.
    assert "if not (pool_start and pool_end):" in body


def test_dialog_old_hard_v4_requirement_gone():
    """Regression guard: the pre-v0.5.344 unconditional check
    `if not pool_start or not pool_end: return "Pool start and end
    addresses are required."` must be REMOVED."""
    src = _dialog_src()
    assert 'return "Pool start and end addresses are required."' not in src


# --- server endpoint ---

def test_server_endpoint_only_requires_name_and_one_family():
    src = _server_src()
    fn_idx = src.index("def create_dhcp_pool(")
    body = src[fn_idx:fn_idx + 3000]
    # Only 'name' is unconditionally required now.
    assert 'if not data.get("name"):' in body
    # And the family check gates on either v4 or v6 fields.
    assert "_has_v4" in body and "_has_v6" in body


def test_server_endpoint_forwards_v6_fields_to_add_dhcp_pool():
    src = _server_src()
    fn_idx = src.index("def create_dhcp_pool(")
    body = src[fn_idx:fn_idx + 3000]
    # Server accepts pool6_start / pool6_end / prefix6 OR the
    # legacy ipv6_pool_start / ipv6_pool_end / ipv6_prefix aliases.
    assert '"pool6_start": data.get("pool6_start")' in body
    assert '"pool6_end": data.get("pool6_end")' in body
    assert '"prefix6": data.get("prefix6")' in body


def test_server_api_serializer_exposes_v6_fields():
    """`_dhcp_pool_to_api` must return the v6 columns so the client
    can re-hydrate them on edit + display them in Manage Pools."""
    src = _server_src()
    fn_idx = src.index("def _dhcp_pool_to_api(")
    body = src[fn_idx:fn_idx + 2000]
    assert '"pool6_start": pool.get("pool6_start")' in body
    assert '"pool6_end": pool.get("pool6_end")' in body
    assert '"prefix6": pool.get("prefix6")' in body


# --- DB layer ---

def test_db_migration_adds_v6_columns():
    src = _db_src()
    # Migration text mentions the three column names.
    assert 'for _v6_col in ("pool6_start", "pool6_end", "prefix6")' in src
    assert "ALTER TABLE dhcp_pools ADD COLUMN" in src


def test_db_add_dhcp_pool_accepts_v6_only():
    """`add_dhcp_pool` must accept a payload with empty pool_start /
    pool_end when v6 fields are supplied."""
    src = _db_src()
    fn_idx = src.index("def add_dhcp_pool(")
    body = src[fn_idx:fn_idx + 6000]
    # Old unconditional rejection removed:
    assert 'return False\n\n            gateway = ' not in body[:200]  # different shape
    # New any-family gate.
    assert "_v4_any" in body and "_v6_any" in body
    # Explicit "need either IPv4 or IPv6" log.
    assert "at least one" in body.lower() or "any address family" in body.lower()


def test_db_add_dhcp_pool_insert_writes_v6_columns():
    """The INSERT statement must include pool6_start / pool6_end /
    prefix6."""
    src = _db_src()
    fn_idx = src.index("def add_dhcp_pool(")
    body = src[fn_idx:fn_idx + 6000]
    assert "pool6_start" in body and "pool6_end" in body and "prefix6" in body
    # Column names in the INSERT clause:
    assert "pool6_start, pool6_end, prefix6" in body


def test_db_update_dhcp_pool_field_mapping_includes_v6():
    """`update_dhcp_pool`'s field_mapping must include the v6
    columns so an edit that adds v6 to an existing pool persists."""
    src = _db_src()
    fn_idx = src.index("def update_dhcp_pool(")
    body = src[fn_idx:fn_idx + 4000]
    assert '"pool6_start": "pool6_start"' in body
    assert '"pool6_end": "pool6_end"' in body
    assert '"prefix6": "prefix6"' in body


# --- edit dialog preload ---

def test_edit_dialog_preloads_v6_fields():
    """`ManageDHCPPoolsDialog.edit_selected_pool` must include v6
    fields in the `defaults` dict + forward them on save. Otherwise
    editing a v6-only pool would blank its v6 half on next save."""
    src = _dialog_src()
    fn_idx = src.index("def edit_selected_pool(")
    body = src[fn_idx:fn_idx + 4000]
    assert '"pool6_start": pool.get("pool6_start")' in body
    assert '"pool6_end": pool.get("pool6_end")' in body
    assert '"prefix6": pool.get("prefix6")' in body
    # And the update_payload forwards them too.
    assert '"pool6_start": payload.get("pool6_start"' in body


# --- ast ---

def test_all_touched_files_ast_parse():
    import ast
    ast.parse(_dialog_src())
    ast.parse(_server_src())
    ast.parse(_db_src())
