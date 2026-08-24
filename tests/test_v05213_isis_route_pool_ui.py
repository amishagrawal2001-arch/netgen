"""v0.5.213: IS-IS Attach / Detach Route Pools client-side UI —
parity with OSPF.

Server-side `configure_isis_route_advertisement` has worked
since v0.5.211 (VRF suffix) / v0.5.212 (filter cleanup); the
gap was purely on the client:

- `isis_headers` had 11 columns, no "Route Pools" column.
- The IS-IS toolbar had no Attach / Detach Route Pools
  buttons.
- The apply payload sent only `isis_config`, so the server
  had no `all_route_pools` list to generate per-pool
  prefix-lists / route-maps from.
- `_update_device_protocol`'s IS-IS branch didn't preserve
  `route_pools` across edits (OSPF's branch always has).

These source-level lock-ins guard the four fixes so a
refactor can't quietly regress the UI to pre-v0.5.213 state
where operators had no way to point IS-IS at a route pool.

The renderer + prompt methods are wrapped in ~600 lines of Qt
scaffolding (dialogs, thread workers, defaultdict grouping);
running them end-to-end would need a full QApplication + main-
window mock. Anchor at the source level — same approach the
v0.5.208 OSPF-interface-column tests took.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault(
    "NETGEN_DB_PATH",
    str(Path(tempfile.gettempdir()) / f"netgen_v05213_test_{os.getpid()}.db"),
)


ISIS_SRC = (REPO / "utils" / "devices_tab_isis.py").read_text()
DEVICES_TAB_SRC = (REPO / "widgets" / "devices_tab.py").read_text()


# ─────────────────────────────────────────────────────────────────────
# Column layout — "Route Pools" is the 12th column.
# ─────────────────────────────────────────────────────────────────────

def test_isis_headers_include_route_pools_column():
    """`isis_headers = [...]` in setup_isis_subtab must contain
    a 'Route Pools' entry. Rendering, filtering, and every
    setItem call downstream depends on the list literal being
    the single source of truth for column layout."""
    # Match the list literal that sets up the header row.
    m = re.search(
        r"isis_headers\s*=\s*\[(.*?)\]",
        ISIS_SRC, re.DOTALL,
    )
    assert m, "isis_headers list literal moved or renamed"
    headers = m.group(1)
    assert '"Route Pools"' in headers, (
        "'Route Pools' column missing from isis_headers — the "
        "IS-IS UI would go back to the pre-v0.5.213 state with "
        "no visibility into attached pool names."
    )


def test_isis_route_pools_column_is_index_11():
    """The helper `_set_isis_route_pools_cell` must setItem at
    column index 11. Off-by-one here silently paints the pool
    string into a different column."""
    m = re.search(
        r"def _set_isis_route_pools_cell\(self,.*?(?=\n    def )",
        ISIS_SRC, re.DOTALL,
    )
    assert m, "_set_isis_route_pools_cell helper missing"
    body = m.group(0)
    assert "setItem(row, 11" in body, (
        "_set_isis_route_pools_cell no longer writes to column "
        "11 — the Route Pools cell will land in the wrong "
        "column and the operator will see it in Multiplier or "
        "similar."
    )


# ─────────────────────────────────────────────────────────────────────
# Toolbar buttons — attach + detach exist and connect to the
# right handler methods.
# ─────────────────────────────────────────────────────────────────────

def test_attach_route_pools_button_wired():
    """`attach_isis_route_pools_button` must exist AND its
    clicked signal must connect to `self.prompt_attach_route_
    pools`. Without both, the button is a no-op or missing
    entirely."""
    assert "self.parent.attach_isis_route_pools_button" in ISIS_SRC, (
        "attach_isis_route_pools_button attribute missing on "
        "the parent DevicesTab — the button was dropped."
    )
    assert (
        "self.parent.attach_isis_route_pools_button.clicked.connect(self.prompt_attach_route_pools)"
        in ISIS_SRC
    ), "attach button no longer connects to prompt_attach_route_pools"


def test_detach_route_pools_button_wired():
    """Same check for detach."""
    assert "self.parent.detach_isis_route_pools_button" in ISIS_SRC, (
        "detach_isis_route_pools_button attribute missing on "
        "the parent DevicesTab — the button was dropped."
    )
    assert (
        "self.parent.detach_isis_route_pools_button.clicked.connect(self.prompt_detach_route_pools)"
        in ISIS_SRC
    ), "detach button no longer connects to prompt_detach_route_pools"


def test_route_pools_buttons_added_to_layout():
    """Buttons that aren't added to isis_controls never
    render. Guard the layout loop so the two new buttons stay
    in it."""
    # The layout for-loop grouping the config-side buttons.
    m = re.search(
        r"for b in \(self\.parent\.add_isis_button,.*?\):",
        ISIS_SRC, re.DOTALL,
    )
    assert m, "isis toolbar config-group layout loop moved"
    loop = m.group(0)
    assert "attach_isis_route_pools_button" in loop, (
        "attach button not added to isis_controls layout — "
        "attribute exists but it never appears in the toolbar."
    )
    assert "detach_isis_route_pools_button" in loop, (
        "detach button not added to isis_controls layout."
    )


# ─────────────────────────────────────────────────────────────────────
# Prompt methods exist on ISISHandler.
# ─────────────────────────────────────────────────────────────────────

def test_prompt_attach_route_pools_method_exists():
    """Import ISISHandler and assert both methods are real
    attributes — not just source strings. Catches the case
    where the def is nested inside another method by mistake."""
    from utils.devices_tab_isis import ISISHandler
    assert hasattr(ISISHandler, "prompt_attach_route_pools"), (
        "ISISHandler.prompt_attach_route_pools missing — the "
        "attach button click will AttributeError."
    )
    assert callable(getattr(ISISHandler, "prompt_attach_route_pools")), (
        "ISISHandler.prompt_attach_route_pools is not callable."
    )


def test_prompt_detach_route_pools_method_exists():
    from utils.devices_tab_isis import ISISHandler
    assert hasattr(ISISHandler, "prompt_detach_route_pools"), (
        "ISISHandler.prompt_detach_route_pools missing — the "
        "detach button click will AttributeError."
    )
    assert callable(getattr(ISISHandler, "prompt_detach_route_pools")), (
        "ISISHandler.prompt_detach_route_pools is not callable."
    )


def test_prompt_attach_reads_neighbor_type_from_col_2():
    """Column 2 is Neighbor Type in the ISIS table (OSPF has
    it at col 3). Getting this wrong makes the dialog read
    the ISIS Status icon cell as the AF, producing garbage
    attachments."""
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler.prompt_attach_route_pools)
    # The neighbor-type lookup should be against isis_table col 2.
    assert re.search(
        r"isis_table\.item\(row,\s*2\)", src
    ), (
        "prompt_attach_route_pools no longer reads Neighbor Type "
        "from column 2 — column layout drift will silently attach "
        "pools to the wrong AF."
    )


def test_prompt_detach_reads_neighbor_type_from_col_2():
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler.prompt_detach_route_pools)
    assert re.search(
        r"isis_table\.item\(row,\s*2\)", src
    ), (
        "prompt_detach_route_pools no longer reads Neighbor Type "
        "from column 2."
    )


def test_prompt_attach_uses_isis_config_key():
    """The methods must write into `isis_config`, not
    `ospf_config` — an easy copy-paste bug given the port."""
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler.prompt_attach_route_pools)
    assert "isis_config" in src, (
        "prompt_attach_route_pools no longer references isis_config."
    )
    assert "ospf_config" not in src, (
        "prompt_attach_route_pools still references ospf_config — "
        "copy-paste from the OSPF handler wasn't cleaned up."
    )


def test_prompt_attach_preserves_is_is_config_mirror():
    """Legacy `is_is_config` mirror is still read by
    prompt_delete_isis and other code paths. Attach must keep
    both keys in sync so a subsequent delete/apply doesn't see
    a stale mirror."""
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler.prompt_attach_route_pools)
    assert "is_is_config" in src, (
        "prompt_attach_route_pools no longer maintains the "
        "is_is_config legacy mirror — code paths that still "
        "read is_is_config will get stale route_pools."
    )


# ─────────────────────────────────────────────────────────────────────
# Apply payload includes route_pools_per_area + all_route_pools.
# Both apply paths need this — the async worker in
# _apply_isis_to_devices AND the sync helper for background
# workers in _apply_isis_to_server_sync.
# ─────────────────────────────────────────────────────────────────────

def test_apply_isis_to_devices_sends_route_pool_fields():
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler._apply_isis_to_devices)
    assert '"route_pools_per_area"' in src, (
        "_apply_isis_to_devices payload no longer includes "
        "route_pools_per_area — server-side "
        "configure_isis_route_advertisement won't rebuild the "
        "per-pool prefix-list."
    )
    assert '"all_route_pools"' in src, (
        "_apply_isis_to_devices payload no longer includes "
        "all_route_pools — server can't look up pool subnets."
    )


def test_apply_isis_to_server_sync_sends_route_pool_fields():
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler._apply_isis_to_server_sync)
    assert '"route_pools_per_area"' in src, (
        "_apply_isis_to_server_sync payload no longer includes "
        "route_pools_per_area."
    )
    assert '"all_route_pools"' in src, (
        "_apply_isis_to_server_sync payload no longer includes "
        "all_route_pools."
    )


# ─────────────────────────────────────────────────────────────────────
# _update_device_protocol IS-IS branch preserves route_pools.
# ─────────────────────────────────────────────────────────────────────

def test_update_device_protocol_isis_branch_preserves_route_pools():
    """Without this, any Add / Edit / inline IS-IS edit wipes
    the operator's route-pool attachments — because
    merged_config.update(config) inside the ISIS branch would
    replace the whole `route_pools` dict with whatever the
    caller passed (usually nothing)."""
    # Extract the ISIS branch of _update_device_protocol. Match
    # from the ISIS branch marker down to the block that closes
    # it (the next `elif protocol == "OSPF":` — the OSPF branch
    # is defined BEFORE ISIS in the merge logic, but the "preserve
    # in ISIS block" pattern lives inside `if protocol in ["IS-IS",
    # "ISIS"]:` and we scan a generous window around it).
    m = re.search(
        r'if protocol in \["IS-IS", "ISIS"\]:.*?'
        r'(# For OSPF config|# v0\.5\.209: additive Add)',
        DEVICES_TAB_SRC, re.DOTALL,
    )
    assert m, (
        "IS-IS branch of _update_device_protocol not found — "
        "the surrounding merge logic was restructured."
    )
    isis_block = m.group(0)
    assert '"route_pools" not in config' in isis_block and \
           '"route_pools" in existing_config' in isis_block, (
        "_update_device_protocol IS-IS branch no longer "
        "preserves route_pools across an edit. Operator will "
        "see attached pools silently disappear on the next Add "
        "IS-IS / Edit IS-IS / inline cell edit."
    )
    assert 'merged_config["route_pools"]' in isis_block, (
        "_update_device_protocol IS-IS branch reads the guard "
        "but doesn't write the preserve back into merged_config."
    )


# ─────────────────────────────────────────────────────────────────────
# Sanity: OSPF still has the same preservation (regression
# guard so a refactor targeting the ISIS branch above doesn't
# accidentally break the older OSPF version too).
# ─────────────────────────────────────────────────────────────────────

def test_update_device_protocol_ospf_branch_still_preserves_route_pools():
    m = re.search(
        r'elif protocol == "OSPF":.*?(?=\n\s{28}device\[config_key\] = merged_config)',
        DEVICES_TAB_SRC, re.DOTALL,
    )
    assert m, "OSPF branch of _update_device_protocol not found"
    ospf_block = m.group(0)
    assert '"route_pools" not in config' in ospf_block and \
           'merged_config["route_pools"]' in ospf_block, (
        "OSPF branch lost its route_pools preservation — the "
        "regression that motivated the ISIS parity fix is back."
    )


# ─────────────────────────────────────────────────────────────────────
# Renderer helper: dict format wins over list format so the
# UI never double-counts pools when both AFs render.
# ─────────────────────────────────────────────────────────────────────

def test_set_route_pools_cell_dict_format_reads_af_key():
    """`route_pools` should be read as {"IPv4": [...], "IPv6":
    [...]} — same shape OSPF uses. If someone reintroduces the
    flat-list path for both AFs, both rows would show the same
    pool list."""
    from utils.devices_tab_isis import ISISHandler
    src = inspect.getsource(ISISHandler._set_isis_route_pools_cell)
    assert "isinstance(route_pools, dict)" in src, (
        "_set_isis_route_pools_cell no longer distinguishes "
        "dict vs. list route_pools format."
    )
    assert 'route_pools.get(protocol_type' in src, (
        "_set_isis_route_pools_cell no longer looks up pools "
        "under the AF key — will show the same pools for IPv4 "
        "and IPv6."
    )
