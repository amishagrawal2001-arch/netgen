"""v0.3.9 — cascade server-offline to iface child dots.

User-reported bug (screenshot): the server-tree's parent TG node
went red ("offline") but the iface children below it kept showing
their last-known green / red dots. Operator saw "TG 1 offline"
next to "lo ✓ up" + "eno8303 ✓ up" — nonsense, because we
can't measure the link state of an iface on a server we can't
reach.

Root cause: `_update_server_led` only swapped the parent's icon;
the iface child items were never touched. They kept whatever
dots `update_server_tree` painted on them from the LAST
successful `/api/interfaces` poll.

v0.3.9 adds `_cascade_offline_to_iface_children(server)` invoked
from `_update_server_led` whenever the server transitions to
offline. Walks the children, switches each dot to red, and
updates the tooltip to explain the cascade ("server offline;
iface state unknown").

The dialog is heavy to construct headlessly so this is a
source-grep pin plus an isolated mock test of the cascade
logic.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO = Path(__file__).resolve().parent.parent
SERVER_SECTION = REPO / "traffic_client" / "server_section.py"


@pytest.fixture(scope="module")
def src():
    return SERVER_SECTION.read_text()


# ─────────────────────────────────────── helper exists
def test_v0_3_9_cascade_helper_defined(src):
    assert re.search(
        r"^    def _cascade_offline_to_iface_children\(self, server\)",
        src, flags=re.MULTILINE,
    ), "_cascade_offline_to_iface_children method missing"


def test_v0_3_9_led_calls_cascade_on_offline(src):
    """`_update_server_led` must call the cascade ONLY when the
    server is offline. The pre-v0.3.9 bug was that the LED update
    didn't touch the children at all — this test pins both that
    the call exists AND that it's gated on the offline branch."""
    body = re.search(
        r"def _update_server_led\(self, server\).*?(?=\n    def |\Z)",
        src, flags=re.DOTALL,
    )
    assert body is not None
    text = body.group(0)
    # Call exists.
    assert "_cascade_offline_to_iface_children" in text, (
        "v0.3.9 regression — _update_server_led no longer calls "
        "_cascade_offline_to_iface_children. Iface children will "
        "stay green/red after the server goes offline."
    )
    # And it's gated on `not online` (we don't want to clobber
    # iface dots when the server is online — the next poll
    # repopulates from authoritative state).
    assert re.search(
        r"if not online:\s*\n.{0,400}?_cascade_offline_to_iface_children",
        text, flags=re.DOTALL,
    ), (
        "cascade must be gated on `if not online:` — calling it "
        "on the online branch would clobber the per-iface "
        "up/down dots with stale red"
    )


# ─────────────────────────────────────── cascade behaviour (mocked tree)
def test_v0_3_9_cascade_sets_red_icon_and_offline_tooltip_on_children(qapp):
    """Exercise the helper directly with a mocked QTreeWidgetItem
    that tracks setIcon / setToolTip calls."""
    from traffic_client.server_section import TrafficGenClientServerSection

    # Mock children — each has text(), setIcon, setToolTip.
    child_a = MagicMock()
    child_a.text.return_value = "eno8303"
    child_b = MagicMock()
    child_b.text.return_value = "enp160s0f0np0"

    # Mock server tree item with 2 children.
    server_item = MagicMock()
    server_item.childCount.return_value = 2
    server_item.child.side_effect = lambda i: [child_a, child_b][i]

    server = {
        "address": "http://svl-d-ai-srv01:5050",
        "status_item": server_item,
    }

    # Call as an unbound method on a minimal stand-in `self` — the
    # method only touches `server` arg + r_icon helper. We don't
    # need a real instance.
    instance = MagicMock()
    TrafficGenClientServerSection._cascade_offline_to_iface_children(
        instance, server,
    )

    # Both children got an icon swap.
    assert child_a.setIcon.called, (
        "cascade didn't call setIcon on child_a"
    )
    assert child_b.setIcon.called, (
        "cascade didn't call setIcon on child_b"
    )

    # Tooltips set with the "server offline" explanation.
    child_a.setToolTip.assert_called_once()
    a_args = child_a.setToolTip.call_args
    assert a_args[0][0] == 0  # column 0
    tip_text = a_args[0][1]
    assert "eno8303" in tip_text, "tooltip missing iface name"
    assert "server offline" in tip_text.lower(), (
        "tooltip missing 'server offline' explanation — operator "
        "won't understand the cascade and will think the link "
        "actually went down"
    )
    assert "stale" in tip_text.lower() or "unknown" in tip_text.lower(), (
        "tooltip should clarify the dot is stale / unknown, not "
        "an authoritative link-down reading"
    )


def test_v0_3_9_cascade_no_op_when_no_status_item():
    """A server that was added but never rendered into the tree
    has no `status_item`. The cascade must no-op gracefully."""
    from traffic_client.server_section import TrafficGenClientServerSection
    instance = MagicMock()
    # No status_item key.
    server = {"address": "http://lab-box:5050"}
    # Must not raise.
    TrafficGenClientServerSection._cascade_offline_to_iface_children(
        instance, server,
    )


def test_v0_3_9_cascade_no_op_when_no_children():
    """A server tree item that's been built but has 0 children
    (e.g. iface fetch hasn't completed yet) — also no-op."""
    from traffic_client.server_section import TrafficGenClientServerSection
    server_item = MagicMock()
    server_item.childCount.return_value = 0
    server = {"address": "x", "status_item": server_item}
    instance = MagicMock()
    TrafficGenClientServerSection._cascade_offline_to_iface_children(
        instance, server,
    )
    # child() never called — there were no children to iterate.
    server_item.child.assert_not_called()
