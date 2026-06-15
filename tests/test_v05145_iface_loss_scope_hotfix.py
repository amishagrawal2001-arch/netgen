"""v0.5.145 hotfix: peer lookup in update_statistics_table must use
the renderer's input dict (`statistics`), not the names
`filtered_statistics` / `merged_statistics` that live in
`_on_stats_fetch_finished`.

Operator hit (cold start, immediately after upgrading to v0.5.144):

    File "traffic_client/statistics_section.py", line 1874, in
    update_statistics_table
        peer = filtered_statistics.get(peer_name) or
               merged_statistics.get(peer_name)
    NameError: name 'filtered_statistics' is not defined

`_on_stats_fetch_finished` builds `merged_statistics` and
`filtered_statistics`, then calls
`self.update_statistics_table(filtered_statistics)`. Inside that
method the dict is `statistics` (the parameter). My v0.5.144 patch
referenced the wrong names — they worked under static analysis but
crashed at the first call site.

Hotfix: `peer = statistics.get(peer_name)`. Same dict everyone else
in the renderer is already using.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


SRC = (REPO / "traffic_client" / "statistics_section.py").read_text()


# ───── source-level pins ────────────────────────────────────────────────


def test_no_filtered_statistics_in_renderer():
    """The renderer must not reference `filtered_statistics` as live
    code — that name lives only in `_on_stats_fetch_finished`.
    Comment lines documenting the historical bug are fine."""
    _assert_name_not_in_code(SRC, "update_statistics_table",
                             "filtered_statistics")


def test_no_merged_statistics_in_renderer():
    """Same for `merged_statistics`. The renderer must use its own
    `statistics` parameter."""
    _assert_name_not_in_code(SRC, "update_statistics_table",
                             "merged_statistics")


def test_peer_lookup_uses_statistics_param():
    """The fix: `peer = statistics.get(peer_name)`."""
    body = _extract_method(SRC, "update_statistics_table")
    assert "statistics.get(peer_name)" in body, (
        "peer lookup should resolve via the renderer's input dict"
    )


# ───── behavioral test on a minimal stub renderer ────────────────────────


def test_renderer_handles_peer_lookup_via_statistics():
    """Stand-in renderer that mirrors the v0.5.145 patch shape and
    walks a `peer_ifaces` set. Confirms no NameError + that the
    peer's phy counters reach the helper."""
    from traffic_client.statistics_section import compute_iface_pair_loss

    statistics = {
        "TG 0 - ens2f0np0": {
            "phy_tx": 830_000_000, "phy_rx": 0,
            "peer_ifaces": {"TG 0 - ens2f1np1"},
        },
        "TG 0 - ens2f1np1": {
            "phy_tx": 0, "phy_rx": 820_000_000,
            "peer_ifaces": {"TG 0 - ens2f0np0"},
        },
    }

    # Mirror the renderer's peer-walk + helper call.
    results = {}
    for iface_name, stats in statistics.items():
        own_phy_tx = stats.get("phy_tx", 0)
        own_phy_rx = stats.get("phy_rx", 0)
        peers = stats.get("peer_ifaces") or set()
        peer_phy_tx = 0
        peer_phy_rx = 0
        for peer_name in peers:
            peer = statistics.get(peer_name)
            if not peer:
                continue
            peer_phy_tx = max(peer_phy_tx, peer.get("phy_tx", 0))
            peer_phy_rx = max(peer_phy_rx, peer.get("phy_rx", 0))
        results[iface_name] = compute_iface_pair_loss(
            own_phy_tx, own_phy_rx, peer_phy_tx, peer_phy_rx,
        )

    # Both halves of the pair converge on the same number.
    lost_tx, pct_tx = results["TG 0 - ens2f0np0"]
    lost_rx, pct_rx = results["TG 0 - ens2f1np1"]
    assert lost_tx == lost_rx == 10_000_000
    assert pct_tx == pct_rx
    assert 1.20 < pct_tx < 1.21


def test_renderer_safe_when_peer_missing_from_dict():
    """If a stream references an rx_iface that's no longer in the
    statistics dict (operator removed it from the view), the peer
    lookup returns None — the `if not peer: continue` branch must
    keep us from crashing."""
    from traffic_client.statistics_section import compute_iface_pair_loss

    statistics = {
        "TG 0 - ens2f0np0": {
            "phy_tx": 100, "phy_rx": 50,
            "peer_ifaces": {"TG 0 - gone"},  # peer not in dict
        },
    }
    for iface_name, stats in statistics.items():
        peers = stats.get("peer_ifaces") or set()
        peer_phy_tx = peer_phy_rx = 0
        for peer_name in peers:
            peer = statistics.get(peer_name)
            if not peer:
                continue
            peer_phy_tx = max(peer_phy_tx, peer.get("phy_tx", 0))
            peer_phy_rx = max(peer_phy_rx, peer.get("phy_rx", 0))
        # No peer found — falls back to own counters via the
        # max() in the helper.
        lost, _ = compute_iface_pair_loss(
            stats["phy_tx"], stats["phy_rx"], peer_phy_tx, peer_phy_rx,
        )
        assert lost == 50  # 100 - 50


# ───── helpers ────────────────────────────────────────────────────────────


def _assert_name_not_in_code(src: str, method: str, needle: str) -> None:
    """Walk the method body; flag `needle` only when it appears on a
    line that isn't a comment."""
    body = _extract_method(src, method)
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert needle not in stripped, (
            f"{method} references {needle!r} as live code "
            f"(not a comment) — out of scope, crashes on cold start"
        )


def _extract_method(src: str, name: str) -> str:
    """Return the source body of a method `def name(...)`. Stops at
    the next method/class boundary."""
    pat = re.compile(
        rf"(    )?def {re.escape(name)}\s*\([^)]*\)[^:]*:[^\n]*\n"
        rf"(?:.*?(?=\n(?:    )?def \w|\nclass \w|\Z))",
        flags=re.DOTALL,
    )
    m = pat.search(src)
    assert m is not None, f"could not locate def {name}(...) in source"
    return m.group(0)
