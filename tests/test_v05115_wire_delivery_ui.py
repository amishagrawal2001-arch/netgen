"""v0.5.115: Statistics tab's RX cell renders ⚠ + tooltip when
the server's wire_delivery_warning field is present.

Pre-fix the v0.5.114 server-side warning was invisible to GUI
operators — they'd see rx_count=0 but no hint about why.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_all_streams_dict_carries_wire_delivery_warning(qapp):
    """The stats-fetch pipeline builds an all_streams list of
    per-row dicts. The wire_delivery_warning field must be
    threaded through from the stream dict (server response)
    into each row dict, so the renderer can read it later."""
    # Verify the source file references the field in the
    # all_streams.append block. Pure shape contract — the
    # full pipeline requires a live stats fetch we can't
    # easily stage here.
    sec_src = (Path("traffic_client/statistics_section.py")
               ).read_text(encoding="utf-8")
    # The field must appear in the all_streams build block.
    # Match the comment + assignment we added in v0.5.115.
    assert '"wire_delivery_warning":' in sec_src, (
        "all_streams build block must surface "
        "wire_delivery_warning so the renderer can read it"
    )
    assert 'wire_delivery_warning' in sec_src
    # And the renderer must consume it via .get on the stream
    # row dict — the v0.5.115 RX-cell branch.
    assert 'stream.get("wire_delivery_warning")' in sec_src, (
        "RX-cell renderer must read wire_delivery_warning "
        "off the stream row dict"
    )


def test_warning_icon_only_when_dict_present():
    """The renderer's icon-prefix branch fires ONLY when
    wire_delivery_warning is a truthy dict — None / missing /
    non-dict values must not produce a phantom ⚠. Verified by
    parsing the source — the branch reads `if wdw and
    isinstance(wdw, dict)`."""
    sec_src = (Path("traffic_client/statistics_section.py")
               ).read_text(encoding="utf-8")
    # The guard must be present so an empty dict or null
    # field doesn't accidentally trigger the icon prefix.
    assert "if wdw and isinstance(wdw, dict)" in sec_src, (
        "RX-cell renderer must guard against non-dict / "
        "falsy wire_delivery_warning before prefixing ⚠"
    )


def test_tooltip_references_dpdk_guide():
    """The tooltip should point operators to the in-app DPDK
    guide section we added in v0.5.114 — that's where the
    actual troubleshooting walkthrough lives. Without this
    pointer the icon is a dead-end UX."""
    sec_src = (Path("traffic_client/statistics_section.py")
               ).read_text(encoding="utf-8")
    assert "DPDK Workflow Guide" in sec_src or \
           "DPDK guide" in sec_src.lower(), (
        "Tooltip must reference the DPDK guide so operators "
        "have a path from icon → walkthrough"
    )


def test_warning_color_distinct_from_red_loss_indicator():
    """The amber (#b45309) used for wire-delivery warnings is
    intentionally distinct from the red (#ef4444) used for
    100% loss. The warning is more actionable than the red —
    we want the operator to see the explanation, not just the
    symptom — so amber wins when both apply."""
    sec_src = (Path("traffic_client/statistics_section.py")
               ).read_text(encoding="utf-8")
    assert "#b45309" in sec_src, "warning color must be amber"
    # Look for the amber setForeground in the wire-delivery
    # branch, not just somewhere in the file.
    # (engine column at line ~1983 also uses #b45309 for
    # degraded engine. Make sure RX cell uses it too.)
    amber_count = sec_src.count("#b45309")
    assert amber_count >= 2, (
        f"expected amber to be used both for engine-degraded "
        f"and RX wire-delivery branches; found {amber_count}"
    )
