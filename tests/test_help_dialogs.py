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
    for ver in ("0.2.41", "0.2.45", "0.2.46", "0.2.57", "0.2.58",
                "0.2.59", "0.2.60", "0.2.61", "0.2.63", "0.2.65",
                "0.2.66", "0.2.67", "0.2.68"):
        assert ver in _FEATURE_GUIDE_HTML, f"missing version tag: {ver}"


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
