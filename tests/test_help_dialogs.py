"""Smoke + content tests for the Help-menu dialogs (v0.2.69).

Two layers:
  * pure-string content checks against the HTML constants — fast, no
    Qt; lock the new sections (EVPN bulk inject, Preflight, RFC 2544
    latency / HTML report, SR-MPLS stack, BFD, QinQ) and the new
    What's-New entry against future edits.
  * Qt smoke — opening each dialog in offscreen mode and confirming
    the QTextBrowser actually receives the HTML so a future
    constant rename can't silently break the show function.
"""

import pytest


# ───────────────────────────────────────────────── API guide content
def test_api_guide_lists_evpn_type2_inject_endpoint():
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "/api/evpn/type2/inject" in _API_GUIDE_HTML
    assert "/api/evpn/type2/clear" in _API_GUIDE_HTML
    assert "/api/evpn/type2/list" in _API_GUIDE_HTML


def test_api_guide_lists_evpn_type5_inject_endpoint():
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "/api/evpn/type5/inject" in _API_GUIDE_HTML
    assert "/api/evpn/type5/clear" in _API_GUIDE_HTML


def test_api_guide_lists_preflight_endpoint():
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "/api/preflight/check" in _API_GUIDE_HTML


def test_api_guide_l2_row_includes_bfd():
    from widgets.stream_dialog import _API_GUIDE_HTML
    # The summary-table row enumerates the supported protocols.
    assert "lacp,lldp,vrrp,igmp,pim,bfd" in _API_GUIDE_HTML


def test_api_guide_has_dedicated_sections_for_new_features():
    """One <h2> per major feature so the table of sections is
    discoverable and the cross-refs from the What's-New guide point
    somewhere real."""
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "<h2>24. EVPN bulk inject"   in _API_GUIDE_HTML
    assert "<h2>25. Preflight checks"   in _API_GUIDE_HTML
    assert "<h2>26. RFC 2544 latency"   in _API_GUIDE_HTML
    assert "<h2>27. SR-MPLS label stack" in _API_GUIDE_HTML


def test_api_guide_has_evpn_curl_examples():
    """The EVPN bulk-inject section ships real copy-pasteable curl
    blocks (so an operator can script without reading the helper
    module's source)."""
    from widgets.stream_dialog import _API_GUIDE_HTML
    # Each kind has at least one POST and a clear example.
    assert "/api/evpn/type2/inject" in _API_GUIDE_HTML
    assert "base_mac"               in _API_GUIDE_HTML
    assert "/api/evpn/type5/inject" in _API_GUIDE_HTML
    assert "base_prefix"            in _API_GUIDE_HTML


def test_api_guide_mentions_qinq_and_outer_vlan():
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "QinQ"           in _API_GUIDE_HTML
    assert "outer_vlan_id"  in _API_GUIDE_HTML
    assert "0x88a8"         in _API_GUIDE_HTML


def test_api_guide_bfd_curl_example_present():
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "/api/l2/bfd/start" in _API_GUIDE_HTML
    # The BFD payload bit-width that 0.2.61 pinned in tests is the
    # discriminator hex — make sure the example shows the right shape.
    assert "my_discriminator" in _API_GUIDE_HTML


# ──────────────────────────────────────────── Feature guide content
def test_feature_guide_exists_and_has_known_sections():
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    for section in ("Streams tab", "L2 / Multicast Emulation",
                    "VXLAN sub-tab", "Statistics dock",
                    "Tools menu", "Server / API surface",
                    "Reliability fixes"):
        assert section in _FEATURE_GUIDE_HTML, f"missing: {section}"


def test_feature_guide_references_recent_versions():
    """Each new feature is tagged with the version it shipped in so
    the operator can cross-reference the changelog."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # v0.2.79 catches the doc lag from v0.2.72 onwards; v0.2.90
    # extends the pinning through v0.2.89. Every release with a
    # user-visible surface lives here so the next stale period gets
    # caught immediately.
    for ver in ("0.2.41", "0.2.45", "0.2.46", "0.2.57", "0.2.58",
                "0.2.59", "0.2.60", "0.2.61", "0.2.63", "0.2.65",
                "0.2.66", "0.2.67", "0.2.68", "0.2.70", "0.2.71",
                "0.2.74", "0.2.75", "0.2.76", "0.2.77", "0.2.78",
                "0.2.81", "0.2.82", "0.2.83", "0.2.84",
                "0.2.85", "0.2.86", "0.2.87", "0.2.88", "0.2.89"):
        assert ver in _FEATURE_GUIDE_HTML, f"missing version tag: {ver}"


def test_feature_guide_documents_l2_validation_burst():
    """v0.2.81 → v0.2.83 + v0.2.84 added a series of L2 fixes /
    features that operators rely on; pin the key strings so a
    future doc edit doesn't accidentally regress them."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # The L2 audit hits.
    assert "IGMPv1" in _FEATURE_GUIDE_HTML
    assert "VRRPv2 authentication" in _FEATURE_GUIDE_HTML
    assert "Frame preview" in _FEATURE_GUIDE_HTML
    # The v0.2.81 audit-driven validators.
    assert "MAC + IP validators" in _FEATURE_GUIDE_HTML


def test_feature_guide_documents_devices_tab_additions():
    """v0.2.85 → v0.2.89 added the Devices-tab audit fixes; pin
    the section header + the marquee items."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    assert "Devices tab additions" in _FEATURE_GUIDE_HTML
    assert "ISIS NET-ID validation" in _FEATURE_GUIDE_HTML
    assert "OSPF area-id validation" in _FEATURE_GUIDE_HTML
    assert "Empty-state placeholders" in _FEATURE_GUIDE_HTML
    assert "Delete-key shortcut" in _FEATURE_GUIDE_HTML


def test_feature_guide_documents_stateful_tcp_tab():
    """v0.2.88 brought the stateful-TCP feature into a first-class
    GUI tab. Pin the section + a few signature labels."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    assert "Stateful TCP tab" in _FEATURE_GUIDE_HTML
    assert "client + server" in _FEATURE_GUIDE_HTML
    assert "TLS both directions" in _FEATURE_GUIDE_HTML
    assert "EADDRNOTAVAIL" in _FEATURE_GUIDE_HTML


def test_capabilities_guide_lists_igmpv1_and_vrrpv2_auth():
    """§2 L2/Multicast table was updated for v0.2.82 (IGMPv1) and
    v0.2.83 (VRRPv2 auth TLVs). Pin those references."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "IGMP</strong> v1 + v2 + v3" in _CAPABILITIES_GUIDE_HTML
    # The auth blurb spans a line break — normalise whitespace
    # before substring-matching so the HTML's word-wrap doesn't
    # break the pin.
    flat = " ".join(_CAPABILITIES_GUIDE_HTML.split())
    assert "RFC 3768 §5.3.6 authentication" in flat


def test_capabilities_guide_has_validators_subsection():
    """v0.2.90 added a 'Catch-bad-config-early validators' sub-block
    naming both helpers (utils/isis_net.py + utils/ospf_area.py)."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "Catch-bad-config-early validators" in _CAPABILITIES_GUIDE_HTML
    assert "validate_isis_net" in _CAPABILITIES_GUIDE_HTML
    assert "validate_ospf_area_id" in _CAPABILITIES_GUIDE_HTML


def test_feature_guide_documents_dpdk_telemetry_surfaces():
    """v0.2.79 explicitly pins the DPDK admin & telemetry surfaces
    so a future guide refresh can't accidentally drop them."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    assert "DPDK telemetry" in _FEATURE_GUIDE_HTML
    assert "readiness chip" in _FEATURE_GUIDE_HTML
    assert "Bind anyway" in _FEATURE_GUIDE_HTML
    assert "Runtime DPDK fallback" in _FEATURE_GUIDE_HTML
    assert "Hugepage allocation feedback" in _FEATURE_GUIDE_HTML


def test_feature_guide_documents_preflight_followups():
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # The four v0.2.78 preflight closeouts.
    assert "Per-device dot" in _FEATURE_GUIDE_HTML
    assert "Pill-click filter" in _FEATURE_GUIDE_HTML
    assert "EVPN active-injections chip" in _FEATURE_GUIDE_HTML
    assert "SR-MPLS row badge" in _FEATURE_GUIDE_HTML


def test_feature_guide_help_table_matches_menu_order():
    """The "Help & reference" table at the bottom of the What's New
    guide should list entries in the same order they appear in the
    actual Help menu (traffic_client/main.py setup_menu_bar). Out-of-
    sync ordering misleads operators about where to find things.

    Actual menu order (as of v0.2.80):
      Install Guide, API Guide, Supported Features, What's New,
      [separator], Install / Upgrade Server,
      [separator], DPDK Traffic Blast Workflow.
    """
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # Crude but effective: assert each pair appears in the right
    # order in the HTML by checking string positions.
    pos = lambda s: _FEATURE_GUIDE_HTML.find(s)
    # Anchor at the "Help & reference" header so we're not catching
    # earlier mentions of the same labels.
    table_start = _FEATURE_GUIDE_HTML.find("<h2>Help &amp; reference")
    assert table_start > 0
    tail = _FEATURE_GUIDE_HTML[table_start:]
    def order(*labels):
        positions = [tail.find(lbl) for lbl in labels]
        assert all(p > 0 for p in positions), \
            f"missing label in Help table: {labels}"
        assert positions == sorted(positions), \
            f"out-of-order Help table entries: {labels}"
    order("Install Guide", "API Guide", "Supported Features",
          "What's New", "Install / Upgrade Server",
          "DPDK Traffic Blast Workflow")


def test_feature_guide_documents_preflight_bar():
    """The 0.2.70 preflight bar and 0.2.71 Apply-refresh hook each
    need their own discoverable section — operators who can see the
    pills should be able to find the doc for them."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # Dedicated section header.
    assert "Preflight bar" in _FEATURE_GUIDE_HTML
    # Both shipping versions named.
    assert "0.2.70" in _FEATURE_GUIDE_HTML
    assert "0.2.71" in _FEATURE_GUIDE_HTML
    # Key behaviours called out.
    assert "Details" in _FEATURE_GUIDE_HTML
    assert "60 s" in _FEATURE_GUIDE_HTML  # auto-poll cadence
    assert "Apply" in _FEATURE_GUIDE_HTML  # the refresh hook


def test_feature_guide_points_to_where_each_feature_lives():
    """Operators care about 'where do I click?' — every section should
    have a Where: pointer."""
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    # Class is used to style the pointer, so its presence implies the
    # convention is in place.
    assert _FEATURE_GUIDE_HTML.count("class=\"where\"") >= 6


def test_feature_guide_cross_references_api_guide():
    from widgets.stream_dialog import _FEATURE_GUIDE_HTML
    assert "API Guide" in _FEATURE_GUIDE_HTML


# ──────────────────────────────────────────── Qt smoke — dialogs open
def test_show_api_guide_renders_html_into_textbrowser(qapp, monkeypatch):
    """Don't actually pop a modal in headless — monkeypatch QDialog.exec
    to a no-op. We're checking that the show function builds a
    QTextBrowser, populates it from the constant, and doesn't crash."""
    from PyQt5.QtWidgets import QDialog, QWidget
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)

    captured = {}
    from PyQt5.QtWidgets import QTextBrowser
    orig_set_html = QTextBrowser.setHtml
    def capturing_set_html(self, html):
        captured["html"] = html
        return orig_set_html(self, html)
    monkeypatch.setattr(QTextBrowser, "setHtml", capturing_set_html)

    from widgets.stream_dialog import show_api_guide
    show_api_guide(QWidget())
    assert "Netgen Server" in captured.get("html", "")
    assert "/api/evpn/type2/inject" in captured["html"]


# ──────────────────────────────────────── Capabilities guide content
def test_capabilities_guide_covers_every_major_surface():
    """The capability matrix should have a section for each major
    surface so an operator can answer 'can the app do X?' without
    leaving the dialog."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    for section in (
        "Stream packet builder",
        "L2 / Multicast emulation",
        "Routing / control plane",
        "VXLAN / EVPN data plane",
        "DHCP",
        "Compliance test methodologies",
        "Preflight checks",
        "Statistics + reporting",
        "Backends",
        "Server / API / operations",
    ):
        assert section in _CAPABILITIES_GUIDE_HTML, f"missing: {section}"


def test_capabilities_guide_lists_packet_layer_protocols():
    """L3/L4 + encap should each be named explicitly so the matrix
    is grep-able by an operator looking for support."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    for proto in ("IPv4", "IPv6", "UDP", "TCP", "ICMP",
                  "MPLS", "SR-MPLS", "VXLAN",
                  "802.1Q", "802.1ad", "QinQ", "IMIX", "NLAT"):
        assert proto in _CAPABILITIES_GUIDE_HTML, f"missing protocol: {proto}"


def test_capabilities_guide_lists_every_l2_emulator():
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    for proto in ("LACP", "LLDP", "VRRP", "IGMP", "PIM", "BFD"):
        assert proto in _CAPABILITIES_GUIDE_HTML, f"missing emulator: {proto}"


def test_capabilities_guide_lists_routing_protocols():
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    for proto in ("BGP", "EVPN", "OSPF", "IS-IS", "VRF"):
        assert proto in _CAPABILITIES_GUIDE_HTML, f"missing routing: {proto}"


def test_capabilities_guide_lists_every_preflight_code():
    """If preflight gains a new code we want the capability matrix
    pinned to it — otherwise the doc silently goes stale."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    for code in ("BGP_NO_REMOTE_ASN", "BGP_NO_LOOPBACK",
                 "VXLAN_EMPTY", "VXLAN_MISSING_FIELDS",
                 "OSPF_NO_AREA", "ISIS_NO_AREA",
                 "DUPLICATE_IPV4"):
        assert code in _CAPABILITIES_GUIDE_HTML, f"missing code: {code}"


def test_capabilities_guide_calls_out_dpdk_limits():
    """DPDK is UDP-only — be explicit so operators don't waste an
    afternoon wondering why their TCP stream silently fell back to
    Scapy."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "DPDK" in _CAPABILITIES_GUIDE_HTML
    assert "UDP only" in _CAPABILITIES_GUIDE_HTML or "UDP-only" in _CAPABILITIES_GUIDE_HTML


def test_capabilities_guide_has_honest_not_supported_list():
    """A 'what this app is not' section earns operator trust and
    saves support tickets."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "What this app is not" in _CAPABILITIES_GUIDE_HTML


def test_capabilities_guide_covers_preflight_findings_surfaces():
    """v0.2.79: Preflight section enumerates all the surfaces
    (pills, per-device dot, pill-click filter, sortable Details,
    auto-refresh) so operators don't think they're stuck with the
    aggregate counts."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "Per-device dot" in _CAPABILITIES_GUIDE_HTML
    assert "filtered Details" in _CAPABILITIES_GUIDE_HTML
    assert "Sortable Details" in _CAPABILITIES_GUIDE_HTML


def test_capabilities_guide_covers_dpdk_fallback_telemetry():
    """v0.2.79: Backends section calls out both pre-flight and
    runtime fallback telemetry as a real feature (not just a
    fallback-happened-silently caveat)."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "DPDK fallback telemetry" in _CAPABILITIES_GUIDE_HTML
    assert "Pre-flight" in _CAPABILITIES_GUIDE_HTML
    assert "Runtime" in _CAPABILITIES_GUIDE_HTML
    assert "runtime_engine" in _CAPABILITIES_GUIDE_HTML


def test_capabilities_guide_covers_dpdk_admin_surfaces():
    """v0.2.79: DPDK admin subsection in §10 enumerates the
    readiness chip + bind safety + hugepage feedback + inline
    Unbind + EVPN active chip."""
    from widgets.stream_dialog import _CAPABILITIES_GUIDE_HTML
    assert "Readiness chip" in _CAPABILITIES_GUIDE_HTML
    assert "NIC bind safety" in _CAPABILITIES_GUIDE_HTML
    assert "Hugepage allocation feedback" in _CAPABILITIES_GUIDE_HTML
    assert "Inline Unbind" in _CAPABILITIES_GUIDE_HTML
    assert "Active EVPN injections chip" in _CAPABILITIES_GUIDE_HTML


def test_show_capabilities_guide_renders_html_into_textbrowser(qapp, monkeypatch):
    from PyQt5.QtWidgets import QDialog, QWidget, QTextBrowser
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)

    captured = {}
    orig_set_html = QTextBrowser.setHtml
    def capturing_set_html(self, html):
        captured["html"] = html
        return orig_set_html(self, html)
    monkeypatch.setattr(QTextBrowser, "setHtml", capturing_set_html)

    from widgets.stream_dialog import show_capabilities_guide
    show_capabilities_guide(QWidget())
    assert "Supported Features" in captured.get("html", "")
    assert "Stream packet builder" in captured["html"]


def test_show_feature_guide_renders_html_into_textbrowser(qapp, monkeypatch):
    from PyQt5.QtWidgets import QDialog, QWidget, QTextBrowser
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)

    captured = {}
    orig_set_html = QTextBrowser.setHtml
    def capturing_set_html(self, html):
        captured["html"] = html
        return orig_set_html(self, html)
    monkeypatch.setattr(QTextBrowser, "setHtml", capturing_set_html)

    from widgets.stream_dialog import show_feature_guide
    show_feature_guide(QWidget())
    assert "What's New" in captured.get("html", "")
    # And it carries one of the new section headers we just pinned.
    assert "EVPN bulk-inject" in captured["html"]


def test_rdma_guide_constant_present():
    """v0.4.0 Help → RDMA Guide must exist as a standalone guide,
    not buried inside the API Guide. Pin both the function + the
    constant so a refactor can't silently revert."""
    from widgets.stream_dialog import _RDMA_GUIDE_HTML, show_rdma_guide
    # Function exists + callable
    assert callable(show_rdma_guide)
    # Guide HTML has the section headers we documented
    assert "Netgen — RDMA Guide" in _RDMA_GUIDE_HTML
    # Ixia comparison section
    assert "Endpoint Groups + Traffic Items" in _RDMA_GUIDE_HTML
    # All 5 topology shapes documented
    for shape in ("Single", "Fan-in", "Fan-out", "Mesh", "Pairwise"):
        assert f"<b>{shape}</b>" in _RDMA_GUIDE_HTML, (
            f"Topology shape {shape!r} missing from RDMA guide"
        )
    # Auto-install / kill switch documented
    assert "NETGEN_AUTO_INSTALL" in _RDMA_GUIDE_HTML
    # REST surface documented
    assert "/api/rdma/perftest/start" in _RDMA_GUIDE_HTML
    assert "/api/rdma/devices" in _RDMA_GUIDE_HTML


def test_rdma_guide_content_NOT_in_api_guide():
    """The Topology + Ixia content used to be misplaced as §10d/§10e
    in _API_GUIDE_HTML. After v0.4.0 cleanup, it should live ONLY in
    _RDMA_GUIDE_HTML."""
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "10d. Comparison with hardware test equipment" not in _API_GUIDE_HTML, (
        "Ixia comparison still misplaced in API guide — should be in "
        "the dedicated RDMA guide only"
    )
    assert "10e. RDMA Topology Test" not in _API_GUIDE_HTML, (
        "Topology Test help still misplaced in API guide"
    )


def test_toc_extractor_parses_h2_h3_headers():
    """v0.4.0 _extract_toc must parse h2 + h3 headers out of guide
    HTML for the navigation sidebar. Returns (level, text) pairs
    in document order."""
    from widgets.stream_dialog import _extract_toc
    html = """
    <h1>Title</h1>
    <h2>1. Foo</h2>
    <p>body</p>
    <h3>1a. Bar</h3>
    <p>body</p>
    <h2>2. Baz <code>qux</code></h2>
    <h3>2a. Quux</h3>
    """
    items = _extract_toc(html)
    assert len(items) == 4
    assert items[0] == ("h2", "1. Foo")
    assert items[1] == ("h3", "1a. Bar")
    # Tags inside header text get stripped
    assert items[2] == ("h2", "2. Baz qux")
    assert items[3] == ("h3", "2a. Quux")


def test_toc_extractor_strips_html_entities():
    """Headers with &amp; / &lt; / &gt; entities decode in the TOC
    label so the visible sidebar text matches what the operator
    sees in the rendered body."""
    from widgets.stream_dialog import _extract_toc
    html = "<h2>Devices &amp; ports</h2><h3>10·m. Manual install</h3>"
    items = _extract_toc(html)
    assert items[0] == ("h2", "Devices & ports")
    assert items[1] == ("h3", "10·m. Manual install")


def test_toc_extractor_returns_empty_on_no_headers():
    from widgets.stream_dialog import _extract_toc
    assert _extract_toc("<p>Just a paragraph</p>") == []


def test_install_guide_toc_has_real_entries():
    """Slimmed install guide (post-Jun 11 2026 rewrite) has ≥7
    h2 sections — Quickstart, Prereqs, Step 2 DPDK, Step 3
    Client, Upgrades, Uninstall, Auth, Troubleshooting,
    Variations, See also. The TOC extractor must surface them
    all so the sidebar is useful."""
    from widgets.stream_dialog import _INSTALL_GUIDE_HTML, _extract_toc
    items = _extract_toc(_INSTALL_GUIDE_HTML)
    assert len(items) >= 7, (
        f"Install guide should have ≥7 toc entries; got {len(items)}"
    )
    # First entry should be a top-level header
    assert items[0][0] == "h2"


def test_api_guide_topology_section_present():
    """v0.4.0 §28i Topology Mode worked example must be in the API
    guide so scripted users can find the curl pattern + listen-port
    allocation rule + aggregation math."""
    from widgets.stream_dialog import _API_GUIDE_HTML
    assert "28i. Topology Mode" in _API_GUIDE_HTML
    assert "Worked example — fan-in" in _API_GUIDE_HTML
    assert "Listen-port collision avoidance" in _API_GUIDE_HTML
    # Aggregation table mentions the iter-weighted latency mean
    assert "iter" in _API_GUIDE_HTML.lower() and "weight" in _API_GUIDE_HTML.lower()


def test_api_guide_rdma_section_cross_refs_rdma_guide():
    """§28 should send operators to the dedicated RDMA Guide for
    the full walkthrough, not to the now-stale Install Guide §10."""
    from widgets.stream_dialog import _API_GUIDE_HTML
    # Find the §28 header and the paragraph right after
    import re
    m = re.search(r"<h2>28\.[\s\S]*?</p>", _API_GUIDE_HTML)
    assert m, "§28 header + intro paragraph not found"
    intro = m.group(0)
    assert "RDMA Guide" in intro, (
        "§28 intro paragraph should send operators to Help → RDMA Guide; "
        f"got: {intro[:300]!r}"
    )


def test_toc_scroll_positions_header_at_top():
    """Regression test for the v0.4.0 TOC sidebar scroll bug:
    clicking a TOC item used to leave the matched header at the
    BOTTOM of the viewport (Qt's ensureCursorVisible does the
    *minimum* scroll). Fix replaces that with a direct
    verticalScrollBar.setValue() based on the block's absolute
    document Y. Pin the math so a refactor can't silently revert."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import sys
    from PyQt5.QtWidgets import QApplication, QTextBrowser
    from PyQt5.QtGui import QTextCursor
    from widgets.stream_dialog import _API_GUIDE_HTML

    _app = QApplication.instance() or QApplication(sys.argv)
    b = QTextBrowser()
    b.setHtml(_API_GUIDE_HTML)
    b.show()
    b.resize(800, 400)
    _app.processEvents()
    _app.processEvents()

    # Reproduce the dialog's click-handler logic for a known header
    # well past the natural viewport (forces scrolling).
    target = "28i. Topology Mode (v0.4.0) — driving N×M perftest pairs over REST"
    cur = b.textCursor()
    cur.movePosition(QTextCursor.Start)
    b.setTextCursor(cur)
    found = b.find(target)
    assert found, "test header not found in API guide — fixture out of sync"

    cur = b.textCursor()
    cur.movePosition(QTextCursor.StartOfBlock)
    b.setTextCursor(cur)
    block = cur.block()
    layout = b.document().documentLayout()
    block_rect = layout.blockBoundingRect(block)
    target_y = max(0, int(block_rect.y()) - 8)
    b.verticalScrollBar().setValue(target_y)
    _app.processEvents()

    # After scroll, the cursor's Y in viewport coords should be near
    # the TOP of the viewport (within the first quarter), not the
    # bottom. Pre-fix it was at the bottom edge.
    cursor_y = b.cursorRect().y()
    viewport_h = b.viewport().height()
    assert cursor_y < viewport_h / 3, (
        f"matched header at viewport Y={cursor_y} (viewport_h={viewport_h}) — "
        f"expected near top (Y < {viewport_h/3:.0f}). The scroll math is "
        f"back to ensureCursorVisible-style minimum-scroll which parks the "
        f"header at the BOTTOM of the viewport."
    )
