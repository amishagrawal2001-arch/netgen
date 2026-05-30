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
    # v0.2.79 catches the doc lag from v0.2.72 onwards — every
    # release with a user-visible surface gets pinned here so the
    # next stale period is caught quickly.
    for ver in ("0.2.41", "0.2.45", "0.2.46", "0.2.57", "0.2.58",
                "0.2.59", "0.2.60", "0.2.61", "0.2.63", "0.2.65",
                "0.2.66", "0.2.67", "0.2.68", "0.2.70", "0.2.71",
                "0.2.74", "0.2.75", "0.2.76", "0.2.77", "0.2.78"):
        assert ver in _FEATURE_GUIDE_HTML, f"missing version tag: {ver}"


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
