"""v0.4.7 — gap-fill scale templates.

Operator asked for scale templates covering source MAC and source /
destination UDP / TCP ports, plus any other gaps. The v0.3.11 batch
covered MAC-dst, IPv4 src+dst, IPv6 dst, 5-tuple (UDP), VLAN-ID —
but left source-MAC, UDP src/dst port (individually), the entire
TCP family, and IPv6 src un-templated.

v0.4.7 adds nine templates:

  * mac_src_sweep_1k          — source MAC (operator ask)
  * mac_src_and_dst_sweep_1k  — both-ends MAC learning
  * udp_src_port_sweep_1k     — UDP source port (operator ask)
  * udp_dst_port_sweep_1k     — UDP destination port (operator ask)
  * tcp_baseline              — non-scale; the missing 'I want TCP' starter
  * tcp_src_port_sweep_1k     — TCP source port (operator ask)
  * tcp_dst_port_sweep_1k     — TCP destination port (operator ask)
  * tcp_5tuple_sweep_rss      — TCP RSS bucket spread
  * ipv6_src_sweep_64         — symmetric to v0.3.11 ipv6_dst_sweep

These tests pin the field-shape contract so changes to the dialog's
key names (e.g. `udp_increment_source_port` → something else) break
the templates AT THE TEMPLATE FILE, not at the operator's chair.
"""
from __future__ import annotations

from utils import traffic_templates


# ─────────────────────────── source MAC sweep ─────────────────────


def test_mac_src_sweep_uses_correct_dialog_keys():
    """The dialog persists mac_source_mode / mac_source_count /
    mac_source_step (see widgets/stream_dialog.py:7396-7407). Pin
    those exact field names — if the dialog renames, this test
    breaks first, not the operator's stream."""
    data = traffic_templates.get_stream_data("mac_src_sweep_1k")
    assert data is not None
    mac = data["protocol_data"]["mac"]
    assert mac["mac_source_mode"] == "Increment"
    assert mac["mac_source_count"] == "1024"
    assert mac["mac_source_step"] == "1"
    # Start address must NOT be the unified default (which would
    # collide with non-scale templates' src MAC).
    assert mac["mac_source_address"] != "02:00:00:00:00:01"
    # Dst MAC stays fixed at unified default
    assert mac["mac_destination_mode"] == "Fixed"
    assert mac["mac_destination_address"] == "02:00:00:00:00:02"


def test_mac_src_and_dst_sweep_varies_both():
    """The both-ends template must sweep BOTH src and dst MAC, not
    just one. If a refactor accidentally sets one to Fixed, the
    template silently becomes equivalent to mac_src_sweep_1k or
    mac_dst_sweep_1k."""
    data = traffic_templates.get_stream_data("mac_src_and_dst_sweep_1k")
    mac = data["protocol_data"]["mac"]
    assert mac["mac_source_mode"] == "Increment"
    assert mac["mac_destination_mode"] == "Increment"
    assert mac["mac_source_count"] == "1024"
    assert mac["mac_destination_count"] == "1024"


# ─────────────────────────── UDP port sweeps ──────────────────────


def test_udp_src_port_sweep_uses_correct_keys():
    """The dialog persists udp_increment_source_port (bool) +
    udp_source_port_step + udp_source_port_count
    (widgets/stream_dialog.py:9152-9156). Pin that exact contract."""
    data = traffic_templates.get_stream_data("udp_src_port_sweep_1k")
    udp = data["protocol_data"]["udp"]
    assert udp["udp_increment_source_port"] is True
    assert udp["udp_source_port_step"] == "1"
    assert udp["udp_source_port_count"] == "1024"
    # Dst port should NOT be incrementing
    assert not udp.get("udp_increment_destination_port", False)


def test_udp_dst_port_sweep_uses_correct_keys():
    data = traffic_templates.get_stream_data("udp_dst_port_sweep_1k")
    udp = data["protocol_data"]["udp"]
    assert udp["udp_increment_destination_port"] is True
    assert udp["udp_destination_port_step"] == "1"
    assert udp["udp_destination_port_count"] == "1024"
    assert not udp.get("udp_increment_source_port", False)


# ─────────────────────────── TCP family ───────────────────────────


def test_tcp_baseline_exists_and_is_scapy():
    """Pre-v0.4.7 there was NO TCP template at all — operator picking
    'TCP' from the dropdown got nothing. tcp_baseline fills that gap.
    DPDK TX is UDP-only today; baseline must be Scapy."""
    data = traffic_templates.get_stream_data("tcp_baseline")
    assert data is not None
    assert data["L4"] == "TCP"
    assert data["L3"] == "IPv4"
    assert data.get("dpdk_enable") is False, (
        "tcp_baseline used dpdk_enable=True — DPDK TX worker is "
        "UDP-only, the start path would silently fall back to Scapy "
        "with a confusing toast. Keep dpdk_enable=False explicit."
    )
    tcp = data["protocol_data"]["tcp"]
    assert tcp["tcp_source_port"] == "10000"
    assert tcp["tcp_destination_port"] == "80"


def test_tcp_src_port_sweep_uses_correct_keys():
    """The dialog persists tcp_increment_source_port (bool) +
    tcp_source_port_step + tcp_source_port_count
    (widgets/stream_dialog.py:9145-9147)."""
    data = traffic_templates.get_stream_data("tcp_src_port_sweep_1k")
    tcp = data["protocol_data"]["tcp"]
    assert tcp["tcp_increment_source_port"] is True
    assert tcp["tcp_source_port_step"] == "1"
    assert tcp["tcp_source_port_count"] == "1024"
    assert not tcp.get("tcp_increment_destination_port", False)
    # All TCP scale templates use Scapy (DPDK TX is UDP-only)
    assert data.get("dpdk_enable") is False


def test_tcp_dst_port_sweep_uses_correct_keys():
    data = traffic_templates.get_stream_data("tcp_dst_port_sweep_1k")
    tcp = data["protocol_data"]["tcp"]
    assert tcp["tcp_increment_destination_port"] is True
    assert tcp["tcp_destination_port_step"] == "1"
    assert tcp["tcp_destination_port_count"] == "1024"
    assert not tcp.get("tcp_increment_source_port", False)
    assert data.get("dpdk_enable") is False


def test_tcp_5tuple_sweep_varies_all_four_tuple_fields():
    """TCP RSS test must vary all 4 RSS-hash inputs: src IP, dst IP,
    src port, dst port. Missing any one degrades the test to a
    weaker subset (which we already have a template for)."""
    data = traffic_templates.get_stream_data("tcp_5tuple_sweep_rss")
    pd = data["protocol_data"]
    assert pd["ipv4"]["ipv4_source_mode"] == "Increment"
    assert pd["ipv4"]["ipv4_destination_mode"] == "Increment"
    assert pd["tcp"]["tcp_increment_source_port"] is True
    assert pd["tcp"]["tcp_increment_destination_port"] is True
    assert data.get("dpdk_enable") is False


# ─────────────────────────── IPv6 src sweep ───────────────────────


def test_ipv6_src_sweep_symmetric_to_dst_sweep():
    """ipv6_src_sweep_64 must be the mirror of ipv6_dst_sweep_64 —
    same count, same step, same field-shape keys, just swapped
    src↔dst roles."""
    src_t = traffic_templates.get_stream_data("ipv6_src_sweep_64")
    dst_t = traffic_templates.get_stream_data("ipv6_dst_sweep_64")
    src_v6 = src_t["protocol_data"]["ipv6"]
    dst_v6 = dst_t["protocol_data"]["ipv6"]
    # src template increments src, fixes dst
    assert src_v6["ipv6_source_mode"] == "Increment"
    assert src_v6["ipv6_destination_mode"] == "Fixed"
    assert src_v6["ipv6_source_increment_count"] == "64"
    # dst template increments dst, fixes src (sanity, not the focus)
    assert dst_v6["ipv6_destination_mode"] == "Increment"
    assert dst_v6["ipv6_source_mode"] == "Fixed"
    assert dst_v6["ipv6_destination_increment_count"] == "64"


# ─────────────────────────── registry contract ────────────────────


def test_all_v047_templates_registered():
    """Every key the operator can pick must round-trip through the
    public API. If get_template() returns None for any of these,
    the dropdown is hiding a template the registry forgot to expose."""
    v047_keys = [
        "mac_src_sweep_1k", "mac_src_and_dst_sweep_1k",
        "udp_src_port_sweep_1k", "udp_dst_port_sweep_1k",
        "tcp_baseline",
        "tcp_src_port_sweep_1k", "tcp_dst_port_sweep_1k",
        "tcp_5tuple_sweep_rss",
        "ipv6_src_sweep_64",
    ]
    all_keys = {m["key"] for m in traffic_templates.list_templates()}
    missing = set(v047_keys) - all_keys
    assert not missing, (
        f"v0.4.7 templates missing from registry: {missing}. "
        f"Operator picks one from the dropdown → dialog can't "
        f"populate → 'broken template' bug report."
    )

    # Each must also resolve through get_template() (the dialog
    # uses that path, not list_templates() raw).
    for key in v047_keys:
        t = traffic_templates.get_template(key)
        assert t is not None, f"get_template({key!r}) returned None"
        assert t.key == key
        # Title and summary are operator-facing — empty strings would
        # render a blank entry in the dropdown.
        assert t.title.strip(), f"{key!r} has empty title"
        assert t.summary.strip(), f"{key!r} has empty summary"


def test_template_dropdown_is_searchable(qapp):
    """v0.4.7: the template dropdown grew from 14 → 23 entries.
    Scrolling a 24-item list for the right scenario is tedious —
    operator must be able to TYPE to filter. Pin: combo is editable,
    has a case-insensitive MatchContains completer, and the line
    edit shows a 'search' affordance via placeholder text."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QCompleter
    from widgets.stream_dialog import AddStreamDialog

    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "new"})
    try:
        assert hasattr(dlg, "template_combo"), (
            "AddStreamDialog has no template_combo — search can't be wired"
        )
        combo = dlg.template_combo
        assert combo.isEditable(), (
            "template_combo is not editable — operator can't type to search. "
            "Pre-v0.4.7 the combo was a closed-list dropdown."
        )
        le = combo.lineEdit()
        assert le is not None
        assert "search" in le.placeholderText().lower(), (
            f"placeholder text {le.placeholderText()!r} should hint at "
            f"search affordance — without it, operators don't realise "
            f"the combo accepts typed input."
        )
        completer = combo.completer()
        assert completer is not None, "no completer attached"
        assert completer.caseSensitivity() == Qt.CaseInsensitive, (
            "completer is case-sensitive — operator typing 'tcp' wouldn't "
            "match titles starting with 'TCP'"
        )
        # MatchContains lets ANY substring of the title match — 'rss'
        # finds RSS-bucket spread, 'src' finds the 4 source-side
        # sweeps, '1024' finds all five 1024-element scale templates.
        assert completer.filterMode() == Qt.MatchContains, (
            f"completer filter mode is {completer.filterMode()} — "
            f"only Qt.MatchContains lets mid-string queries work; "
            f"MatchStartsWith would force operators to know which "
            f"word the template title starts with."
        )
        # And the popup mode itself — InlineCompletion would shove
        # a single guess into the line edit without showing a list.
        assert completer.completionMode() == QCompleter.PopupCompletion, (
            "completer mode is not PopupCompletion — operator wouldn't "
            "see the filtered candidate list"
        )
    finally:
        dlg.deleteLater()


def test_template_search_matches_expected_terms(qapp):
    """Spot-check that the searchable terms actually hit the right
    templates. The combo's model is what the completer reads; we
    walk it and assert substring matches land where they should.
    Locks in the title-naming convention so a future rename of one
    template doesn't accidentally break search for an unrelated
    query."""
    from PyQt5.QtCore import Qt
    from widgets.stream_dialog import AddStreamDialog

    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "new"})
    try:
        combo = dlg.template_combo
        # Walk the combo's items, build (title_lower → key) map
        titles = {}
        for i in range(combo.count()):
            t = combo.itemText(i).lower()
            k = combo.itemData(i) or ""
            titles[t] = k

        # Query → at least one matching template key must contain
        # this substring in its title. If a future title rename
        # breaks this, the operator's muscle memory breaks too.
        cases = [
            ("tcp",        "tcp_baseline"),
            ("src",        "mac_src_sweep_1k"),
            ("dst",        "mac_dst_sweep_1k"),
            ("rss",        "five_tuple_sweep_rss"),
            ("vlan",       "vlan_id_sweep_4k"),
            ("ipv6",       "ipv6_src_sweep_64"),
            ("1024",       "mac_src_sweep_1k"),
            ("port",       "udp_src_port_sweep_1k"),
            ("imix",       "udp_imix"),
            ("latency",    "latency_probe"),
        ]
        for query, expected_key in cases:
            matches = [k for t, k in titles.items() if query in t]
            assert expected_key in matches, (
                f"Search query {query!r} did not match expected "
                f"template {expected_key!r}. Matches found: "
                f"{matches}. The title for {expected_key!r} probably "
                f"got renamed and no longer contains {query!r} — "
                f"either restore the keyword in the title or update "
                f"the test cases here."
            )
    finally:
        dlg.deleteLater()


def test_v047_templates_have_unique_names():
    """The 'name' field becomes the stream's display name in the
    streams table. Two templates with the same name would create
    confusable rows after the operator applies them both."""
    v047_keys = [
        "mac_src_sweep_1k", "mac_src_and_dst_sweep_1k",
        "udp_src_port_sweep_1k", "udp_dst_port_sweep_1k",
        "tcp_baseline",
        "tcp_src_port_sweep_1k", "tcp_dst_port_sweep_1k",
        "tcp_5tuple_sweep_rss",
        "ipv6_src_sweep_64",
    ]
    names = []
    for key in v047_keys:
        data = traffic_templates.get_stream_data(key)
        n = data.get("name")
        assert n, f"{key!r} has no `name`"
        names.append(n)
    assert len(set(names)) == len(names), (
        f"v0.4.7 templates have duplicate names: {names}"
    )
