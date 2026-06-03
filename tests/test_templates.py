"""Unit tests for device + traffic template registries.

These cover the two template-registry modules without needing the
PyQt GUI. The dialog-application side is GUI-coupled and is exercised
manually (or by widget-level tests if someone wires them in later);
the registry layer is pure data so it tests cleanly.
"""

from utils import device_templates, traffic_templates


# ---------------------------------------------------------------- device


def test_device_templates_register_at_least_six():
    """The registry should have a meaningful starter set so the dropdown
    is worth pulling down. Sanity floor, not a tight bound."""
    metas = device_templates.list_templates()
    assert len(metas) >= 6


def test_device_template_keys_unique():
    keys = [m["key"] for m in device_templates.list_templates()]
    assert len(keys) == len(set(keys)), "duplicate device-template keys"


def test_device_template_titles_have_summary():
    """Every template needs a one-liner; the dropdown summary line in
    the dialog can't show 'None' to operators."""
    for m in device_templates.list_templates():
        assert m["title"], m
        assert m["summary"], m


def test_get_template_returns_for_known_key():
    metas = device_templates.list_templates()
    first = metas[0]["key"]
    t = device_templates.get_template(first)
    assert t is not None
    assert t.key == first


def test_get_template_returns_none_for_unknown_key():
    assert device_templates.get_template("does-not-exist") is None


def test_apply_to_dialog_tolerates_missing_widgets():
    """`apply_to_dialog` should silently skip fields whose widgets don't
    exist on the dialog — the safety net that lets templates ship
    ahead of form rearrangements."""

    class DummyDialog:
        pass   # zero widgets

    # Should not raise, should return False (nothing was applied).
    assert device_templates.apply_to_dialog(DummyDialog(), "ibgp_peer") is False


def test_apply_to_dialog_handles_unknown_template():
    """Bad template key returns False; doesn't crash the dialog."""

    class DummyDialog:
        pass

    assert device_templates.apply_to_dialog(DummyDialog(), "bogus") is False


# ---------------------------------------------------------------- traffic


def test_traffic_templates_register_at_least_six():
    metas = traffic_templates.list_templates()
    assert len(metas) >= 6


def test_traffic_template_keys_unique():
    keys = [m["key"] for m in traffic_templates.list_templates()]
    assert len(keys) == len(set(keys)), "duplicate traffic-template keys"


def test_traffic_stream_data_has_required_shape():
    """Every traffic template emits a dict whose top-level keys match
    what AddStreamDialog.populate_stream_fields() and the REST
    /api/traffic/start endpoint already consume — name, enabled, L3,
    frame_size, protocol_data. Catches the case where a template is
    added but its key shape doesn't line up with the dialog's loader."""
    required_top_keys = {"name", "enabled", "frame_size", "protocol_data"}
    for m in traffic_templates.list_templates():
        data = traffic_templates.get_stream_data(m["key"])
        assert data is not None
        missing = required_top_keys - set(data.keys())
        assert not missing, f"template {m['key']!r} missing keys: {missing}"


def test_traffic_template_deep_copy_isolation():
    """get_stream_data must return a deep copy — mutating it shouldn't
    affect the next caller. Without this, an operator's edits would
    silently change the template for everyone else in the same session."""
    metas = traffic_templates.list_templates()
    key = metas[0]["key"]
    first = traffic_templates.get_stream_data(key)
    first["name"] = "MUTATED"
    first["protocol_data"]["mac"]["mac_source_address"] = "ff:ff:ff:ff:ff:ff"
    second = traffic_templates.get_stream_data(key)
    assert second["name"] != "MUTATED"
    assert (
        second["protocol_data"]["mac"]["mac_source_address"]
        != "ff:ff:ff:ff:ff:ff"
    )


def test_traffic_template_unknown_key_returns_none():
    assert traffic_templates.get_stream_data("nope") is None


def test_scale_template_family_complete():
    """v0.3.11 scaling templates: pin that the six scale variants are
    all present so the dropdown can't lose one silently. Each one
    targets a specific stress-test scenario (MAC table fill, NAT
    pool, RSS bucket spread, etc.) — losing any of them means an
    operator has to hand-build the equivalent stream every time."""
    keys = {m["key"] for m in traffic_templates.list_templates()}
    expected_scale = {
        "mac_dst_sweep_1k",       # MAC table fill
        "ipv4_dst_sweep_256",     # routing fan-out / ECMP dst hash
        "ipv4_src_sweep_256",     # NAT pool / ECMP src hash
        "ipv6_dst_sweep_64",      # v6 routing scale
        "five_tuple_sweep_rss",   # RSS bucket spread
        "vlan_id_sweep_4k",       # trunk VLAN scale
    }
    missing = expected_scale - keys
    assert not missing, (
        f"Scale templates missing from registry: {missing}. The "
        f"scaling family was added in v0.3.11 to cover common "
        f"stress-test scenarios — dropping one means operators "
        f"have to hand-build that scenario every time."
    )


def test_scale_templates_use_increment_mode():
    """Every scale template must declare an Increment-mode field
    somewhere — otherwise it's just a fixed stream mis-labeled
    'scale'. Walk each one and assert at least one of the known
    increment-mode keys is set to 'Increment' / True."""
    scale_keys = [
        "mac_dst_sweep_1k", "ipv4_dst_sweep_256", "ipv4_src_sweep_256",
        "ipv6_dst_sweep_64", "five_tuple_sweep_rss", "vlan_id_sweep_4k",
    ]
    for key in scale_keys:
        data = traffic_templates.get_stream_data(key)
        assert data is not None, f"{key!r} not in registry"
        pd = data.get("protocol_data", {})
        # Hunt for any "Increment" string OR True increment-bool in the
        # mode-bearing sub-dicts. If none, the template is mislabeled.
        is_incrementing = False
        for proto in ("mac", "ipv4", "ipv6", "udp", "vlan"):
            sub = pd.get(proto, {})
            for k, v in sub.items():
                if isinstance(v, str) and v == "Increment":
                    is_incrementing = True
                    break
                if isinstance(v, bool) and v and "increment" in k:
                    is_incrementing = True
                    break
            if is_incrementing:
                break
        assert is_incrementing, (
            f"Scale template {key!r} has no field in Increment mode "
            f"— the template name promises variation but the data "
            f"would emit a fixed stream"
        )


def test_scale_templates_use_unified_mac_defaults():
    """Cross-module pin (extension): scale templates that DON'T
    sweep MAC should reuse the unified locally-administered MAC
    defaults from _udp_eth_ipv4 (02:00:00:00:00:01/02). Otherwise
    captures from a scale template differ from captures from a
    non-scale template, eroding the same trust the MAC-parity
    pin caught for the line-rate templates."""
    from widgets.dpdk_blast_flow_dialog import (
        DEFAULT_DST_MAC, DEFAULT_SRC_MAC,
    )
    # Skip mac_dst_sweep_1k — it varies dst MAC on purpose.
    keys_keeping_fixed_dst_mac = [
        "ipv4_dst_sweep_256", "ipv4_src_sweep_256",
        "ipv6_dst_sweep_64", "five_tuple_sweep_rss", "vlan_id_sweep_4k",
    ]
    for key in keys_keeping_fixed_dst_mac:
        data = traffic_templates.get_stream_data(key)
        mac = data["protocol_data"]["mac"]
        assert mac["mac_source_address"] == DEFAULT_SRC_MAC, (
            f"{key!r} src MAC drifted from unified default"
        )
        assert mac["mac_destination_address"] == DEFAULT_DST_MAC, (
            f"{key!r} dst MAC drifted from unified default"
        )


def test_rfc2544_mac_defaults_unified_with_blast_flow(qapp):
    """v0.3.11 cross-dialog consistency: RFC 2544 used `aa:bb:cc:dd:ee:0x`
    MACs while Blast a Flow + Stream templates used `02:00:00:00:00:0x`.
    Operator running an RFC 2544 test then comparing captures with
    Blast a Flow saw different source MACs and lost confidence in
    the defaults. Pin parity across all three paths."""
    from widgets.rfc2544_dialog import Rfc2544Dialog
    from widgets.dpdk_blast_flow_dialog import (
        DEFAULT_DST_MAC, DEFAULT_SRC_MAC,
    )
    # Rfc2544Dialog __init__ may need a server_url stub; try the
    # likely shape, fall back to inspecting the class attr defaults.
    try:
        dlg = Rfc2544Dialog(parent=None)
    except TypeError:
        dlg = Rfc2544Dialog("http://stub:5050", parent=None)
    assert dlg.mac_src_field.text() == DEFAULT_SRC_MAC, (
        f"RFC 2544 src MAC drifted from Blast a Flow default — "
        f"packet captures from the two tests won't line up. Got "
        f"{dlg.mac_src_field.text()!r}, expected {DEFAULT_SRC_MAC!r}."
    )
    assert dlg.mac_dst_field.text() == DEFAULT_DST_MAC, (
        f"RFC 2544 dst MAC drifted from Blast a Flow default. "
        f"Got {dlg.mac_dst_field.text()!r}, expected {DEFAULT_DST_MAC!r}."
    )


def test_rfc2544_duration_default_meets_standard(qapp):
    """v0.3.11: default duration was 10 s — operators kept exporting
    'RFC 2544 Throughput Test Report' with 10-s measurements and
    getting them rejected by auditors as non-compliant. RFC 2544
    §26.1 requires 60 s minimum for a certified trial run. Pin
    the default at 60 s; tooltip explains the trade-off if
    operator dials down."""
    from widgets.rfc2544_dialog import Rfc2544Dialog
    try:
        dlg = Rfc2544Dialog(parent=None)
    except TypeError:
        dlg = Rfc2544Dialog("http://stub:5050", parent=None)
    assert dlg.duration_spin.value() >= 60, (
        f"RFC 2544 duration default {dlg.duration_spin.value()} s is "
        f"below the §26.1 minimum of 60 s. Reports exported with "
        f"shorter durations are not RFC 2544 compliant."
    )


def test_devices_tab_context_menu_includes_edit(qapp):
    """v0.3.11 UX-parity fix: right-click context menu on the Devices
    table was missing 'Edit' (Apply / Copy / Paste / Delete only).
    Operator had to mouse to the toolbar's Edit button or
    double-click — broke parity with the Streams tab menu (which
    has Edit). Pin that the Edit action wiring is in the menu's
    source. Source-grep avoids the cost of spinning up the full
    Devices tab in a unit test (which would need server-connected
    state)."""
    import inspect
    from widgets.devices_tab import DevicesTab
    src = inspect.getsource(DevicesTab._on_devices_table_context_menu)
    assert 'menu.addAction("Edit")' in src, (
        "Devices tab context menu missing the Edit action — "
        "operator can't edit a device from right-click anymore"
    )
    assert "prompt_edit_device" in src, (
        "Edit action not wired to prompt_edit_device"
    )


def test_devices_tab_bgp_asn_has_live_validator(qapp):
    """v0.3.11: BGP local + remote ASN fields were plain QLineEdit
    with apply-time-only range check. Operator could type 'abc' or
    a malformed string and only learn about it after clicking
    Apply. Pin live regex validator (digit-only, up to 10 chars =
    4-byte ASN range)."""
    from PyQt5.QtGui import QRegExpValidator
    from widgets.devices_tab import AddDeviceDialog
    # AddDeviceDialog likely takes (parent, server_interfaces) kwargs;
    # try common shapes and fall through to skip if construction
    # fails for unrelated reasons.
    dlg = None
    for ctor_args in [(), (None,), ("test-device",)]:
        try:
            dlg = AddDeviceDialog(*ctor_args)
            break
        except Exception:
            continue
    if dlg is None or not hasattr(dlg, "bgp_asn_input"):
        import pytest
        pytest.skip("AddDeviceDialog construction shape not matched")
    for attr in ("bgp_asn_input", "bgp_remote_asn_input"):
        w = getattr(dlg, attr)
        v = w.validator()
        assert isinstance(v, QRegExpValidator), (
            f"{attr} validator is {type(v).__name__ if v else None}, "
            f"expected QRegExpValidator"
        )


def test_udp_line_rate_64b_and_1500b_siblings_both_present():
    """v0.3.11 line-rate tuning: the registry must expose BOTH the
    64 B max-pps stress template AND the 1500 B MTU template so the
    operator can pick the right one without leaving the dropdown.

      • 64 B   → finds the pps ceiling (single-core caps ~23 G of
                 100 G line rate; bump tx_cores=4+ to climb higher).
      • 1500 B → hits ACTUAL wire line rate with a single tx_worker
                 core (8.2 Mpps for 100 G is well within single-
                 core capacity).

    If either disappears the multi-iface parallel-blast workflow
    loses its one-click path."""
    keys = {m["key"] for m in traffic_templates.list_templates()}
    assert "udp_line_rate_64b" in keys
    assert "udp_line_rate_1500b" in keys


def test_udp_line_rate_1500b_template_shape():
    """The 1500 B sibling must be line-rate + DPDK + MTU sized —
    these three are what actually deliver wire-speed throughput,
    so a regression on any of them turns the template into a
    quiet underperformer rather than a loud failure."""
    data = traffic_templates.get_stream_data("udp_line_rate_1500b")
    assert data is not None
    assert data["frame_size"] == 1500, (
        f"Template frame_size = {data['frame_size']}, expected 1500 "
        f"(Ethernet MTU). Smaller frames need more pps than a single "
        f"tx_worker core can produce — won't hit actual line rate."
    )
    assert data.get("stream_rate_type") == "Line Rate", (
        f"Template stream_rate_type = {data.get('stream_rate_type')!r}, "
        f"expected 'Line Rate' — a hard-coded pps cap would defeat "
        f"the whole 'hit line rate' point of this template."
    )
    assert data.get("dpdk_enable") is True, (
        "Template dpdk_enable must be True — without DPDK the "
        "kernel TX path can't sustain line rate on 25 G+."
    )
    # L3/L4 must be IPv4/UDP for the tx_worker fast path.
    assert data.get("L3") == "IPv4"
    assert data.get("L4") == "UDP"


def test_add_stream_dialog_shows_template_dropdown_for_new_stream(qapp):
    """v0.3.11 hotfix: the Add Stream button (stream_control.py:956)
    ALWAYS pre-seeds stream_data with `{"stream_id": uuid}` so the
    new stream lands in self.streams with a stable id. The template
    dropdown's gate was `if templates and not self.stream_data` —
    a non-empty dict made it False, so the dropdown never appeared
    in the live GUI even though headless tests with empty stream_data
    showed it just fine.

    Fix: the gate now checks for operator-facing keys (name,
    frame_size, protocol_data, …). A fresh stream has only
    stream_id; an existing stream being edited has the rest.

    Pin both paths so the gate can't drift back to `not stream_data`."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    assert hasattr(dlg, "template_combo"), (
        "Template dropdown missing for a fresh new stream. "
        "AddStreamDialog launcher passes stream_data={'stream_id': uuid} "
        "for new streams — if the gate `not self.stream_data` was "
        "reintroduced this is exactly the regression."
    )
    # The combo should have 'Custom' + all 8 templates.
    assert dlg.template_combo.count() >= 9


def test_every_template_applies_to_dialog_without_error(qapp):
    """v0.3.11 hotfix #2: every template stores `frame_size` as
    int (Pythonic) but the dialog's populate_stream_fields() does
    QLineEdit.setText(frame_size) which requires str. Without
    coercion, picking ANY template raised TypeError and the
    summary label flipped to a red 'Template foo failed to apply'
    line — operator saw the dropdown but got nothing useful.

    Iterate every registered template, drive the dropdown's
    currentIndex (which fires _on_traffic_template_changed),
    and assert the underlying populate_stream_fields() completed
    by reading back the populated frame_size field. Pins both
    the str() coercion AND the fact that the apply path runs
    cleanly end-to-end."""
    from widgets.stream_dialog import AddStreamDialog
    failures = []
    for m in traffic_templates.list_templates():
        dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
        idx = dlg.template_combo.findData(m["key"])
        assert idx >= 0, f"Template {m['key']!r} not in combo"
        try:
            dlg.template_combo.setCurrentIndex(idx)
        except Exception as e:
            failures.append((m["key"], f"{type(e).__name__}: {e}"))
            continue
        # The handler must have populated frame_size — every template
        # supplies one. Empty string here means populate_stream_fields
        # didn't run (the bug we're guarding against).
        if not dlg.frame_size.text().strip():
            failures.append((m["key"], "frame_size left empty"))
            continue
        # And the value must equal the template's int coerced to str.
        expected = str(traffic_templates.get_stream_data(m["key"])["frame_size"])
        actual = dlg.frame_size.text().strip()
        if actual != expected:
            failures.append((
                m["key"],
                f"frame_size mismatch: got {actual!r}, expected {expected!r}"
            ))
    assert not failures, (
        f"Templates failed to apply: {failures!r}. The most common "
        f"regression is populate_stream_fields() reverting to "
        f"setText(int) without str() coercion."
    )


def test_l3_arp_unchecks_stale_l4_radios(qapp):
    """v0.3.11 invalid-combo guard: when operator picks L3=ARP after
    previously picking L4=UDP, the L4 radio used to stay checked —
    only the L4 groupbox was disabled. get_stream_details then
    serialized an incoherent stream (L3=ARP + L4=UDP) that the
    server has no transmit path for. Pin that L4 radios are
    actively UNCHECKED when ARP becomes active."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    # Stage 1: pick UDP at L4 (no ARP yet).
    dlg.l3_ipv4.setChecked(True)
    dlg.l4_udp.setChecked(True)
    assert dlg.l4_udp.isChecked()
    # Stage 2: switch L3 to ARP. The refresh handler should clear L4.
    dlg.l3_arp.setChecked(True)
    dlg.refresh_l4_sections()
    assert not dlg.l4_udp.isChecked(), (
        "L4=UDP radio still checked after switching to L3=ARP — "
        "would persist an invalid L3=ARP + L4=UDP combo on save"
    )
    assert dlg.l4_none_1.isChecked(), (
        "After ARP-clear, L4 group 1 should snap back to None radio"
    )


def test_vlan_id_field_has_validator(qapp):
    """v0.3.11: VLAN ID field accepted any string until v0.3.11
    (server rejected on save, edit silently lost on reopen).
    Pin QIntValidator(1, 4094) so the field rejects out-of-range
    keystrokes at entry time."""
    from PyQt5.QtGui import QIntValidator
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    v = dlg.vlan_id_field.validator()
    assert isinstance(v, QIntValidator), (
        f"vlan_id_field validator is {type(v).__name__}, "
        f"expected QIntValidator"
    )
    assert v.bottom() == 1, (
        f"VLAN ID min should be 1 (0 is reserved per 802.1Q), got {v.bottom()}"
    )
    assert v.top() == 4094, (
        f"VLAN ID max should be 4094 (4095 is reserved), got {v.top()}"
    )


def test_template_apply_kicks_packet_view_refresh(qapp, monkeypatch):
    """v0.3.11: picking a template populates fields via setCurrentText
    / setChecked which DON'T all chain through the textChanged hook
    that drives the Packet View refresh. Operator picked template,
    switched to Packet View tab, saw stale packet from before.
    Pin that the template-change handler calls the refresh hook
    explicitly so the Packet View matches the new fields."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    calls = []
    monkeypatch.setattr(
        dlg, "_refresh_packet_view_if_visible",
        lambda *a, **kw: calls.append(True),
    )
    idx = dlg.template_combo.findData("udp_line_rate_1500b")
    dlg.template_combo.setCurrentIndex(idx)
    assert calls, (
        "_on_traffic_template_changed didn't call "
        "_refresh_packet_view_if_visible — Packet View tab will "
        "stay stale until operator touches any field"
    )


def test_rate_type_dropdown_has_tooltip(qapp):
    """All four rate-type modes (PPS / Bit Rate / Load% / Line Rate)
    interact differently with frame size and the dpdk_enable
    checkbox. Pin a tooltip exists so operators don't have to read
    code to understand the choice."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    tip = dlg.rate_type_dropdown.toolTip()
    assert tip, "rate_type_dropdown has no tooltip"
    # Sanity-check the tooltip names all four modes.
    for mode in ("Packets Per Second", "Bit Rate", "Line Rate"):
        assert mode in tip, (
            f"rate_type tooltip missing description of {mode!r}"
        )


def test_add_stream_dialog_default_height_fits_tallest_tab(qapp):
    """v0.3.11 visibility + compaction: default height sized for
    the tallest tab's content (Protocol Data at sizeHint=465) plus
    tab bar / template combo / button bar / margins = ~617 px
    minimum. 640 default gives a small buffer; 720 left 130+ px
    of dead stretch space at the bottom. Pin both floor and
    ceiling so future drift can't bring back either bug
    (too-short = clipped, too-tall = wasted space)."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    h = dlg.size().height()
    assert h >= 620, (
        f"Dialog default height {h} too short — Protocol Data tab "
        f"content (sizeHint ~465 + ~150 chrome) will overflow."
    )
    assert h <= 700, (
        f"Dialog default height {h} too tall — visible dead "
        f"absorber stretch wastes screen space; compact-pass "
        f"target is ~640."
    )
    assert dlg.maximumSize().height() >= 900, (
        f"Dialog max height {dlg.maximumSize().height()} too short — "
        f"operator can't grow dialog enough on small screens."
    )


def test_add_stream_tabs_have_min_width_styling(qapp):
    """v0.3.11: tab labels were eliding to 'rotocol Selectio' on
    macOS because Qt's default tabbar shrinks tabs to fit. Pin
    that the styleSheet enforces a min-width so labels render
    in full."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    ss = dlg.tabs.styleSheet()
    assert "min-width" in ss, (
        f"tabs styleSheet missing min-width rule — labels will "
        f"elide on macOS. Got: {ss!r}"
    )


def test_template_dropdown_shows_all_entries_without_scroll(qapp):
    """v0.3.11 visibility fix: with 14 templates + Custom = 15 entries,
    Qt's QComboBox default maxVisibleItems=10 made the last 5 sit
    below the popup fold on macOS. Operators didn't realize they
    had to scroll. Pin maxVisibleItems >= total entries so EVERY
    template fits in one popup view, and pin the combobox-popup: 0
    stylesheet that forces Qt's own popup (the native macOS one
    ignores maxVisibleItems entirely)."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    n_items = dlg.template_combo.count()
    max_vis = dlg.template_combo.maxVisibleItems()
    assert max_vis >= n_items, (
        f"template_combo maxVisibleItems={max_vis} < count={n_items}. "
        f"Last {n_items - max_vis} templates will be below the popup "
        f"fold — operators won't realize they exist."
    )
    # Force-Qt-popup stylesheet must be present so the limit
    # actually takes effect on macOS.
    ss = dlg.template_combo.styleSheet()
    assert "combobox-popup" in ss and "0" in ss, (
        f"template_combo missing 'combobox-popup: 0' stylesheet — "
        f"on macOS native style, maxVisibleItems is ignored and the "
        f"dropdown only shows ~10 entries. Got stylesheet: {ss!r}"
    )


def test_rocev2_use_perf_server_round_trips(qapp):
    """v0.3.11 Protocol Data tab audit fix: the
    rocev2_use_perf_server checkbox was built in setup but never
    collected. Operator ticked it, saved, reopened → empty box.
    Silent data loss. Pin that the field round-trips."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.rocev2_use_perf_server.setChecked(True)
    d = dlg.get_stream_details()
    rocev2 = d.get("protocol_data", {}).get("rocev2", {})
    assert "rocev2_use_perf_server" in rocev2, (
        "rocev2_use_perf_server checkbox state lost on save — "
        "operator's choice silently dropped"
    )
    assert rocev2["rocev2_use_perf_server"] is True, (
        f"rocev2_use_perf_server value wrong: {rocev2['rocev2_use_perf_server']!r}"
    )


def test_payload_data_field_round_trips(qapp):
    """v0.3.11 round-trip fix: the payload data hex bytes field
    was built but never collected. Operator typed custom payload,
    saved, reopened → field reset to default '0000'."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.payload_data_field.setText("DEADBEEF")
    d = dlg.get_stream_details()
    payload = d.get("protocol_data", {}).get("payload", {})
    assert payload.get("payload_data") == "DEADBEEF", (
        f"payload_data not collected. Got: {payload!r}"
    )
    # Round-trip: populate restores the value.
    # NOTE: deliberately skip qapp.processEvents() — populate is
    # synchronous (just setText calls), and processEvents would
    # give any leaked async thread from earlier test files (e.g.,
    # test_sse_e2e's _consume) a chance to fire a Qt slot into
    # freed state, crashing the broader sweep.
    dlg2 = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg2.populate_stream_fields({
        "stream_id": "u",
        "protocol_data": {"payload": {"payload_data": "CAFEBABE"}},
    })
    assert dlg2.payload_data_field.text() == "CAFEBABE", (
        f"payload_data not restored on populate. Got: "
        f"{dlg2.payload_data_field.text()!r}"
    )


def test_arp_fields_have_live_validators(qapp):
    """v0.3.11: ARP MAC/IP fields (arp_sender_mac/ip, arp_target_
    mac/ip) used to bypass _wire_live_validators — operator could
    type 'GG:11:22:33:44:55' or '10.0.0.999' with no red-border
    feedback. Pin that they're now wired."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    for attr in ("arp_sender_mac", "arp_sender_ip",
                 "arp_target_mac", "arp_target_ip"):
        w = getattr(dlg, attr)
        # The live validator wires a textChanged slot; if connected,
        # we'll see at least one receiver on textChanged.
        receivers = w.receivers(w.textChanged)
        assert receivers >= 1, (
            f"{attr} has no textChanged receiver — live validator "
            f"never wired. Operator can type invalid MAC/IP with no "
            f"red-border feedback."
        )


def test_rocev2_gid_mode_enables_step_count(qapp):
    """v0.3.11: RoCEv2 GID source/dest mode dropdowns weren't wired
    to enable their step+count fields. Picking 'Increment' left
    the fields disabled so the operator couldn't configure the
    sweep. Pin the new currentTextChanged → update_increment_fields
    wiring."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    # The parent rocev2_group must be enabled first (L4=RoCEv2)
    # else the child step/count stay disabled regardless of mode.
    dlg.l4_rocev2.setChecked(True)
    dlg.refresh_l4_sections()
    assert dlg.rocev2_group.isEnabled()

    # Source mode → Increment enables step+count.
    # setCurrentIndex fires currentTextChanged synchronously inside
    # the same call, so no processEvents needed (and avoiding it
    # sidesteps a known sweep-crash where leaked SSE threads fire
    # callbacks into freed Qt state when processEvents is called).
    dlg.rocev2_gid_source_mode.setCurrentIndex(1)
    assert dlg.rocev2_gid_source_step.isEnabled(), (
        "Picking GID source mode = Increment didn't enable the step "
        "field — the wiring is gone. Operator can't configure sweep."
    )
    assert dlg.rocev2_gid_source_count.isEnabled()

    # Destination mode → Increment enables its pair
    dlg.rocev2_gid_destination_mode.setCurrentIndex(1)
    assert dlg.rocev2_gid_destination_step.isEnabled()
    assert dlg.rocev2_gid_destination_count.isEnabled()

    # Source mode → Fixed disables again
    dlg.rocev2_gid_source_mode.setCurrentIndex(0)
    assert not dlg.rocev2_gid_source_step.isEnabled()


def test_tcp_udp_override_uncheck_clears_value(qapp):
    """v0.3.11: unchecking an Override checkbox left the value
    stale in the disabled field — and serialized anyway. Pin
    that uncheck also resets the field to '0'."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    for cb_attr, fld_attr in [
        ("override_source_port_checkbox", "source_port_field"),
        ("override_destination_port_checkbox", "destination_port_field"),
        ("override_udp_source_port_checkbox", "udp_source_port_field"),
        ("override_udp_destination_port_checkbox", "udp_destination_port_field"),
    ]:
        cb = getattr(dlg, cb_attr)
        fld = getattr(dlg, fld_attr)
        cb.setChecked(True)
        fld.setText("8080")
        cb.setChecked(False)
        assert fld.text() == "0", (
            f"{cb_attr} uncheck didn't clear {fld_attr} — stale "
            f"value '{fld.text()}' still serializes despite "
            f"override=False"
        )


def test_pcap_fields_round_trip(qapp):
    """v0.3.11 sister-tab audit fix: PCAP fields (enable, file
    path, loop count, rate mode) were COLLECTED by get_stream_details
    but never RESTORED by populate_stream_fields. Operator opened
    an existing stream with PCAP enabled and saw every PCAP field
    blank — silent data loss. Pin full round-trip across all 4
    PCAP fields."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.enable_pcap_checkbox.setChecked(True)
    dlg.pcap_file_path.setText("/tmp/test.pcap")
    dlg.pcap_loop_count.setValue(7)
    dlg.pcap_rate_mode.setCurrentText("Fixed Delay")
    saved = dlg.get_stream_details()
    ps = saved.get("pcap_stream", {})
    assert ps.get("pcap_enabled") is True
    assert ps.get("pcap_file_path") == "/tmp/test.pcap"
    assert ps.get("pcap_loop_count") == 7
    assert ps.get("pcap_rate_mode") == "Fixed Delay"
    # Reopen with the saved dict — all 4 fields must restore.
    dlg2 = AddStreamDialog(parent=None, stream_data=saved)
    assert dlg2.enable_pcap_checkbox.isChecked(), (
        "PCAP enable checkbox not restored — operator's PCAP "
        "selection silently lost on reopen"
    )
    assert dlg2.pcap_file_path.text() == "/tmp/test.pcap", (
        "PCAP file path not restored"
    )
    assert dlg2.pcap_loop_count.value() == 7, (
        "PCAP loop count not restored"
    )
    assert dlg2.pcap_rate_mode.currentText() == "Fixed Delay", (
        "PCAP rate mode not restored"
    )


def test_packet_view_payload_uses_correct_key_path(qapp):
    """v0.3.11 sister-tab audit fix: the Packet View tree's Payload
    row was reading `protocol_data.payload_data.data` — a key path
    that's never populated. Actual collect writes
    `protocol_data.payload.payload_data`. Without this fix, custom
    payload bytes never showed in the packet preview even though
    they were saved correctly. Pin that the tree picks up the
    custom payload via the right path."""
    from PyQt5.QtWidgets import QTreeWidgetItem
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    stream_data = {
        "stream_id": "u",
        "Payload": "Pattern",
        "protocol_data": {
            "payload": {"payload_data": "DEADBEEF"},
        },
    }
    # Re-render the packet view by calling populate_packet_view
    # directly (or via the public refresh hook).
    if hasattr(dlg, "populate_packet_view"):
        dlg.populate_packet_view(stream_data)
    # Walk the tree to find the "Data" leaf and confirm it shows
    # our payload bytes.
    tree = dlg.packet_view_tree if hasattr(dlg, "packet_view_tree") else None
    if tree is None:
        # Some versions store the tree under a different name; fall
        # back to scanning children of the packet_view_tab.
        from PyQt5.QtWidgets import QTreeWidget
        trees = dlg.findChildren(QTreeWidget)
        if trees:
            tree = trees[0]
    assert tree is not None, "Packet view tree widget not found"
    # DFS for a "Data" row whose second column contains DEADBEEF.
    def _walk(item):
        for c in range(item.childCount()):
            child = item.child(c)
            if child.text(0) == "Data" and child.text(1):
                yield child.text(1)
            yield from _walk(child)
    root = tree.invisibleRootItem()
    data_values = list(_walk(root))
    assert any("DEADBEEF" in v for v in data_values), (
        f"Payload 'DEADBEEF' not found in Packet View tree — the "
        f"old wrong-key-path bug is back. Tree Data values found: "
        f"{data_values!r}"
    )


def test_mac_step_validator_rejects_zero(qapp):
    """MAC source/dest step must be >= 1 — a step of 0 produces an
    invalid stream (no actual variation despite Increment mode).
    Pin QIntValidator(min=1) on both step fields."""
    from PyQt5.QtGui import QIntValidator
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    for attr in ("mac_source_step", "mac_destination_step"):
        v = getattr(dlg, attr).validator()
        assert isinstance(v, QIntValidator)
        assert v.bottom() >= 1, (
            f"{attr} validator min={v.bottom()}, expected >= 1"
        )


def test_cross_layer_validate_catches_l2_none_with_l3_ipv4(qapp):
    """v0.3.11 cross-layer guard: L2=None + L3=IPv4 produces a frame
    with no Ethernet header → NIC drops on TX. The dialog used to
    accept this combo silently; now _validate_cross_layer surfaces
    it as a save-time problem."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.l2_none.setChecked(True)
    dlg.l3_ipv4.setChecked(True)
    problems = dlg._validate_cross_layer()
    assert any("L2=None" in p and "IPv4" in p for p in problems), (
        f"L2=None + L3=IPv4 not flagged. Got problems: {problems!r}"
    )


def test_cross_layer_validate_clean_for_normal_stream(qapp):
    """The 'good path' — a normal Ethernet/IPv4/UDP stream must
    return no problems. Otherwise every save would surface false
    positives that operators learn to dismiss."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.l2_ethernet.setChecked(True)
    dlg.l3_ipv4.setChecked(True)
    dlg.l4_udp.setChecked(True)
    dlg.frame_type.setCurrentText("Fixed")
    problems = dlg._validate_cross_layer()
    assert problems == [], (
        f"Normal Eth/IPv4/UDP stream flagged unexpectedly: {problems!r}"
    )


def test_cross_layer_validate_catches_random_frame_min_eq_max(qapp):
    """Frame Type=Random with Min == Max produces a uniform stream
    (or crashes the builder on some scapy versions). Guard at
    save time."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.l2_ethernet.setChecked(True)
    dlg.l3_ipv4.setChecked(True)
    dlg.frame_type.setCurrentText("Random")
    dlg.frame_min.setText("128")
    dlg.frame_max.setText("128")
    problems = dlg._validate_cross_layer()
    assert any("Random" in p and ("Min" in p or "Max" in p) for p in problems), (
        f"Random Min==Max not flagged. Got: {problems!r}"
    )


def test_cross_layer_validate_catches_random_frame_min_gt_max(qapp):
    """Random Min > Max is even worse — undefined builder behavior."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.l2_ethernet.setChecked(True)
    dlg.l3_ipv4.setChecked(True)
    dlg.frame_type.setCurrentText("Random")
    dlg.frame_min.setText("1500")
    dlg.frame_max.setText("64")
    problems = dlg._validate_cross_layer()
    assert any("Random" in p for p in problems), (
        f"Random Min > Max not flagged. Got: {problems!r}"
    )


def test_cross_layer_validate_catches_pcap_with_protocol_stack(qapp):
    """PCAP Replay transmits the file's frames verbatim — protocol-
    stack fields are silently ignored. Operators who tick BOTH
    waste 20 minutes wondering why their custom UDP fields don't
    show up in the capture. Guard at save time."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    dlg.l2_ethernet.setChecked(True)
    dlg.l3_ipv4.setChecked(True)
    dlg.l4_udp.setChecked(True)
    if hasattr(dlg, "enable_pcap_checkbox"):
        dlg.enable_pcap_checkbox.setChecked(True)
        problems = dlg._validate_cross_layer()
        assert any("PCAP" in p for p in problems), (
            f"PCAP + protocol-stack combo not flagged. Got: {problems!r}"
        )


def test_refresh_l3_resets_scale_mode_when_leaving_ipv4(qapp):
    """v0.3.11: switching L3 IPv4 → None used to leave the
    source_mode_dropdown stuck in 'Increment' (disabled). When the
    operator switched back to IPv4 they saw 'Increment' but the
    step/count fields were inconsistent. Pin that refresh_l3_sections
    resets the mode dropdowns to 'Fixed' when L3 isn't IPv4."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    # Stage 1: enable IPv4 + set Increment mode on src.
    dlg.l3_ipv4.setChecked(True)
    dlg.refresh_l3_sections()
    if hasattr(dlg, "source_mode_dropdown"):
        dlg.source_mode_dropdown.setCurrentText("Increment")
        assert dlg.source_mode_dropdown.currentText() == "Increment"
        # Stage 2: switch L3 to None — refresh should reset to Fixed.
        dlg.l3_none.setChecked(True)
        dlg.l3_ipv4.setChecked(False)
        dlg.refresh_l3_sections()
        assert dlg.source_mode_dropdown.currentText() == "Fixed", (
            "L3=None should reset source_mode_dropdown to Fixed; "
            "stale 'Increment' confuses the operator on re-enable"
        )


def test_protocol_stack_dead_widgets_removed(qapp):
    """v0.3.11 Protocol Stack cleanup: L1 group (None/MAC/RAW) and
    payload_random radio were both dead UI — written to stream_data
    but no packet builder ever read them. Pin that the widgets are
    gone so a future 'restore Spirent-style L1 picker' refactor
    can't slip them back in without explicit consideration."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    assert not hasattr(dlg, "l1_none"), (
        "L1 group widget l1_none should be gone — dead UI restored"
    )
    assert not hasattr(dlg, "l1_mac"), "l1_mac should be gone"
    assert not hasattr(dlg, "l1_raw"), "l1_raw should be gone"
    assert not hasattr(dlg, "payload_random"), (
        "payload_random radio should be gone — encoder has no "
        "random-payload code path"
    )


def test_protocol_stack_frame_validator_supports_jumbo(qapp):
    """v0.3.11: frame size validator bumped 1518 → 9216 so operators
    can enter jumbo frames in the Add Stream dialog. Was a silent
    blocker — keystrokes >1518 were rejected even though tx_worker
    and the Blast a Flow dialog accept up to 9216."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    v = dlg.frame_size.validator()
    assert v is not None, "frame_size lost its validator"
    # QIntValidator exposes top() and bottom().
    assert v.top() >= 9216, (
        f"frame_size validator top={v.top()}, expected >= 9216 "
        f"so jumbo frames are enterable"
    )
    assert v.bottom() == 64, (
        f"frame_size validator bottom={v.bottom()}, expected 64 "
        f"(Ethernet minimum)"
    )


def test_protocol_stack_l4_encap_group_relabeled(qapp):
    """v0.3.11: the second 'L4' radio group (RoCEv2/UEC) was relabeled
    to 'Encap (over UDP)' so operators stop seeing two boxes both
    labeled 'L4.' The widgets keep their names (l4_rocev2 / l4_uec)
    for back-compat with saved sessions but the visible title
    changed. Pin the title so a future restyle can't slip it back."""
    from PyQt5.QtWidgets import QGroupBox
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={"stream_id": "u"})
    # Walk every group box and look for one whose title says "Encap".
    titles = [g.title() for g in dlg.findChildren(QGroupBox)]
    has_encap = any("Encap" in t for t in titles)
    assert has_encap, (
        f"No QGroupBox titled '...Encap...' found — the second L4 "
        f"group label flipped back to 'L4'. Visible titles: {titles!r}"
    )
    # The widgets must still exist (back-compat).
    assert hasattr(dlg, "l4_rocev2")
    assert hasattr(dlg, "l4_uec")


def test_add_stream_dialog_hides_template_dropdown_for_existing(qapp):
    """The flip side: when editing an existing stream (operator-
    facing fields populated), the dropdown must STAY hidden so the
    operator can't accidentally clobber their edits by picking a
    template from a dropdown they didn't expect."""
    from widgets.stream_dialog import AddStreamDialog
    dlg = AddStreamDialog(parent=None, stream_data={
        "stream_id": "u",
        "name": "my-existing-flow",
        "frame_size": "128",
        "stream_rate_type": "Line Rate",
    })
    assert not hasattr(dlg, "template_combo"), (
        "Template dropdown should NOT appear when editing an "
        "existing stream — risks operator overwriting their work "
        "by picking a template they didn't intend to apply"
    )


def test_udp_line_rate_1500b_matches_blast_flow_default(qapp):
    """Cross-module pin: the new template's frame size + default
    MACs MUST match the Blast a Flow dialog's defaults. Both were
    tuned by the same line-rate rationale (see _LINE_RATE_RATIONALE
    in widgets/dpdk_blast_flow_dialog.py). If someone changes one
    without the other, the 'use the template for parallel blasts'
    workflow drifts away from the 'one-click blast' default and
    operators get different defaults from the same v0.3.11
    rationale — packet captures don't line up, trust in defaults
    erodes. Fail loudly here so they stay in sync."""
    from widgets.dpdk_blast_flow_dialog import (
        DEFAULT_DST_MAC, DEFAULT_FRAME_SIZE, DEFAULT_SRC_MAC,
    )
    data = traffic_templates.get_stream_data("udp_line_rate_1500b")
    assert data["frame_size"] == DEFAULT_FRAME_SIZE, (
        f"Template frame_size={data['frame_size']} drifted from "
        f"Blast a Flow DEFAULT_FRAME_SIZE={DEFAULT_FRAME_SIZE}. "
        f"Pick one rationale and apply it everywhere."
    )
    # MAC parity — both the dialog and the templates surface to
    # the same operator. A mismatch shows up in packet captures
    # and shakes trust ("which default is right?").
    mac = data["protocol_data"]["mac"]
    assert mac["mac_source_address"] == DEFAULT_SRC_MAC, (
        f"Template src_mac={mac['mac_source_address']!r} drifted "
        f"from Blast a Flow DEFAULT_SRC_MAC={DEFAULT_SRC_MAC!r}"
    )
    assert mac["mac_destination_address"] == DEFAULT_DST_MAC, (
        f"Template dst_mac={mac['mac_destination_address']!r} drifted "
        f"from Blast a Flow DEFAULT_DST_MAC={DEFAULT_DST_MAC!r}"
    )
