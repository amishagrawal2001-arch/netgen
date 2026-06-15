"""v0.5.143: RDMA Topology dialog gains a multi-server endpoint picker.

Operator report (screenshot of the RDMA Topology Test dialog):
    "allow user to pick server and client endpoints from the existing
     server, also how does interface is selected from the menu?"

Before v0.5.143:
- Operators had to type `http://srv01:5050 mlx5_0` lines by hand for
  every (server, HCA) endpoint. No discovery.
- The second token's role was ambiguous — Ethernet iface? RDMA HCA?
  (It's the RDMA HCA name, but nothing said so.)
- The menu handler had a latent typo (`self._selected_servers()` →
  AttributeError) that silently no-op'd the pre-populate path under
  the surrounding try/except. So even the starter line never showed up.

v0.5.143:
- New optional `known_servers=[(url, label), ...]` kwarg on
  `RdmaTopologyDialog` — populated by the menu handler from
  `self.server_interfaces`.
- "Pick from servers…" button next to each side header opens a
  `_EndpointPickerDialog` that lazily fetches /api/rdma/devices on
  every known TG, shows a tree of (server → checkboxes per HCA), and
  appends `<url> <device>` lines to the parent text area on accept.
- Inline help line under the endpoint editors clarifies that the
  second token is the RDMA HCA name (mlx5_0), NOT an Ethernet iface
  like ens2f0np0. perftest addresses the HCA directly via libibverbs.
- Latent typo fix: `_selected_servers` → `_get_selected_servers`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC_DLG = (REPO / "widgets" / "rdma_topology_dialog.py").read_text()
SRC_MENU = (REPO / "traffic_client" / "rdma_menu_actions.py").read_text()


# ───── constructor accepts known_servers ─────────────────────────────────

def test_constructor_accepts_known_servers_kwarg():
    """RdmaTopologyDialog.__init__ must accept a `known_servers` kwarg
    so the menu handler can hand it the registered-TG list."""
    sig = re.search(
        r"def __init__\(\s*self,[^)]*?known_servers\s*[:=]",
        SRC_DLG, flags=re.DOTALL,
    )
    assert sig is not None, "known_servers kwarg missing from __init__"


def test_constructor_stores_known_servers_as_list():
    """The kwarg should be normalised to a list (so the picker can
    iterate it even if the caller passes None)."""
    assert "self._known_servers" in SRC_DLG
    assert "list(known_servers or [])" in SRC_DLG


# ───── picker button rendered conditionally ──────────────────────────────

def test_picker_button_exists():
    """A 'Pick from servers…' button must be wired into the endpoints
    GroupBox header so the operator sees it next to Server / Client
    labels."""
    assert "Pick from servers…" in SRC_DLG


def test_picker_button_disabled_when_no_known_servers():
    """When no TGs are registered, the button must gray out — clicking
    nothing offers nothing."""
    assert "setEnabled(bool(self._known_servers))" in SRC_DLG


def test_picker_button_wired_to_open_handler():
    """Click → self._open_endpoint_picker(side). Both sides use the
    same slot."""
    assert "_open_endpoint_picker" in SRC_DLG
    # Must dispatch on "server" and "client" sides.
    assert '"server"' in SRC_DLG
    assert '"client"' in SRC_DLG


# ───── _open_endpoint_picker behavior ────────────────────────────────────

def test_open_endpoint_picker_method_exists():
    """The slot must exist on the dialog class."""
    assert re.search(
        r"def _open_endpoint_picker\(self, side[^)]*\)",
        SRC_DLG,
    )


def test_open_endpoint_picker_short_circuits_without_servers():
    """If known_servers is empty, the method must bail before opening
    the dialog — otherwise the picker pops up empty."""
    body = _extract_method(SRC_DLG, "_open_endpoint_picker")
    assert "if not self._known_servers:" in body, (
        "_open_endpoint_picker must guard on known_servers"
    )


def test_open_endpoint_picker_appends_not_replaces():
    """Operator may have already typed a few lines — the picker should
    append, not clobber. v0.5.143 takes existing text + chosen lines."""
    body = _extract_method(SRC_DLG, "_open_endpoint_picker")
    # Existing text is preserved in the merged output.
    assert "existing" in body
    assert "toPlainText" in body


# ───── _EndpointPickerDialog ─────────────────────────────────────────────

def test_endpoint_picker_dialog_class_exists():
    """The picker is its own QDialog (separation of concerns — the
    main dialog stays focused on shape + workload)."""
    assert "class _EndpointPickerDialog(" in SRC_DLG


def test_endpoint_picker_uses_tree_widget():
    """One row per server, children = HCAs. Tree widget is the right
    shape because servers expand independently."""
    assert "QTreeWidget" in SRC_DLG
    assert "QTreeWidgetItem" in SRC_DLG


def test_endpoint_picker_devices_are_checkable():
    """Each HCA row must be ItemIsUserCheckable + start Unchecked
    (operator opts in, doesn't have to opt out)."""
    assert "Qt.ItemIsUserCheckable" in SRC_DLG
    assert "Qt.Unchecked" in SRC_DLG


def test_endpoint_picker_fetches_devices_endpoint():
    """The picker calls /api/rdma/devices on each TG, not some other
    discovery endpoint."""
    assert "/api/rdma/devices" in SRC_DLG


def test_endpoint_picker_uses_async_get():
    """Discovery must be async — synchronous probes would block the
    GUI thread per server, multiplying latency to (N × timeout)."""
    # _get_async lives in the blast dialog module already; the picker
    # reuses it so we don't add a second HTTP-worker primitive.
    assert "_get_async" in SRC_DLG


def test_endpoint_picker_lines_format_is_url_space_device():
    """On accept, lines must be plain `<tg_url> <device>` — that's
    what parse_endpoint_line() expects on the way back in."""
    # Find the format string in _on_accept.
    body = _extract_method(SRC_DLG, "_on_accept")
    assert 'f"{url} {dev}"' in body or "f'{url} {dev}'" in body, (
        "lines must be formatted as '<url> <device>' "
        "(no extras — operator can add port=/gid= by hand if needed)"
    )


def test_endpoint_picker_selected_lines_returns_list():
    """selected_lines() is the API the parent dialog calls — must
    return a list[str], not a generator (consumed once would lose
    data)."""
    body = _extract_method(SRC_DLG, "selected_lines")
    assert "return list(" in body or "return [" in body


# ───── inline help line ───────────────────────────────────────────────────

def test_inline_help_clarifies_device_is_hca():
    """The hint must explicitly contrast 'device = RDMA HCA' against
    'Ethernet interface' so the operator stops conflating them."""
    # The hint label is built in _build_ui.
    assert "RDMA HCA" in SRC_DLG
    assert "mlx5_0" in SRC_DLG
    # And mention Ethernet to make the contrast explicit.
    assert re.search(
        r"Ethernet interface|ens2f0np0",
        SRC_DLG,
    ), "hint should contrast with Ethernet iface naming"
    # And mention libibverbs / perftest so the operator understands
    # why this is different from the DPDK / scapy iface pickers.
    assert "libibverbs" in SRC_DLG or "perftest" in SRC_DLG


# ───── menu handler wires server_interfaces in ───────────────────────────

def test_menu_handler_passes_known_servers():
    """The Tools → RDMA → Topology Test handler must hand the dialog
    the registered-TG set so the picker actually has data to show."""
    # Find the show_rdma_topology_dialog method.
    body = _extract_method(SRC_MENU, "show_rdma_topology_dialog")
    assert "known_servers=" in body, (
        "menu handler must pass known_servers= into RdmaTopologyDialog"
    )
    assert "server_interfaces" in body, (
        "should source from self.server_interfaces (the registered TG list)"
    )


def test_menu_handler_passes_tuples_not_dicts():
    """Dialog signature is List[Tuple[str, str]] — URL + display
    label. The handler must convert the dict shape via the existing
    _server_url_label helper, not pass the raw dicts through."""
    body = _extract_method(SRC_MENU, "show_rdma_topology_dialog")
    assert "_server_url_label" in body


def test_menu_handler_typo_fixed():
    """v0.5.143 also fixes the latent typo `self._selected_servers()`
    (which would AttributeError, silently swallowed by the try/except)
    → `self._get_selected_servers()` so the pre-populate path actually
    runs."""
    body = _extract_method(SRC_MENU, "show_rdma_topology_dialog")
    # The active call site uses the correct accessor.
    assert "selected = self._get_selected_servers()" in body, (
        "pre-populate path should now call the correct accessor"
    )
    # And does NOT use the broken one as live code (only inside a
    # comment is OK — that's the historical note).
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert "self._selected_servers()" not in stripped, (
            "the typo `_selected_servers` is still being called in "
            "live code (not just referenced in a comment)"
        )


# ───── helpers ────────────────────────────────────────────────────────────

def _extract_method(src: str, name: str) -> str:
    """Return the source body of a method/function `def name(...)`.

    Stops at the next top-level or method-level `def`/`class`. Loose,
    but good enough for the v0.5.143 sanity checks."""
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"could not locate def {name}(...) in source"
    return m.group(0)
