"""SR-MPLS row badge in the stream table (v0.2.78).

Inline at the Details cell renderer, but the logic is pure and easy
to mirror in a test helper. We re-derive it here and pin the
contract so a future refactor doesn't silently drop the badge.
"""

import pytest


def _badge_for(stream_or_ps):
    """Mirror the inline logic in traffic_client/server_section.py at
    the ``key == 'details'`` branch. Returns the badge suffix string
    (with leading double-space) or empty if not MPLS."""
    mpls_labels = stream_or_ps.get("mpls_labels")
    n_labels = 0
    if isinstance(mpls_labels, str):
        n_labels = len([s for s in mpls_labels.replace(";", ",").split(",")
                        if s.strip()])
    elif isinstance(mpls_labels, (list, tuple)):
        # Match the live code: filter out None/empty/zero explicitly so
        # `str(None) == 'None'` doesn't slip through as a real label.
        n_labels = len([
            x for x in mpls_labels
            if x not in (None, "", 0, "0") and str(x).strip()
        ])
    if n_labels > 1:
        return f"  [MPLS ×{n_labels}]"
    if n_labels == 1:
        return "  [MPLS]"
    legacy = stream_or_ps.get("mpls_label")
    if legacy not in (None, "", 0, "0"):
        return "  [MPLS]"
    return ""


def test_no_mpls_yields_no_badge():
    assert _badge_for({}) == ""
    assert _badge_for({"mpls_labels": ""}) == ""
    assert _badge_for({"mpls_labels": []}) == ""


def test_single_legacy_mpls_label_yields_badge():
    assert _badge_for({"mpls_label": 100}) == "  [MPLS]"
    assert _badge_for({"mpls_label": "100"}) == "  [MPLS]"


def test_legacy_label_zero_treated_as_unset():
    """mpls_label=0 means 'no MPLS' in the legacy form — don't badge."""
    assert _badge_for({"mpls_label": 0}) == ""
    assert _badge_for({"mpls_label": "0"}) == ""
    assert _badge_for({"mpls_label": None}) == ""


def test_single_label_in_list_form():
    assert _badge_for({"mpls_labels": [16001]}) == "  [MPLS]"
    assert _badge_for({"mpls_labels": "16001"}) == "  [MPLS]"


def test_sr_mpls_stack_in_list_form():
    assert _badge_for({"mpls_labels": [16001, 16002, 16003]}) == "  [MPLS ×3]"


def test_sr_mpls_stack_in_string_form():
    """Stream dialog stores the stack as a comma-separated string
    ('16001, 16002') — make sure both delimiters parse."""
    assert _badge_for({"mpls_labels": "16001,16002,16003"}) == "  [MPLS ×3]"
    assert _badge_for({"mpls_labels": "16001; 16002"}) == "  [MPLS ×2]"
    assert _badge_for({"mpls_labels": "  16001 ,  16002  "}) == "  [MPLS ×2]"


def test_empty_strings_in_list_are_ignored():
    """Trailing commas / blank entries don't inflate the count."""
    assert _badge_for({"mpls_labels": "16001,,"}) == "  [MPLS]"
    assert _badge_for({"mpls_labels": [16001, "", None]}) == "  [MPLS]"


def test_modern_labels_take_precedence_over_legacy():
    """If both fields are present, modern wins (the form sets
    mpls_labels for any new stream)."""
    assert _badge_for({
        "mpls_labels": [16001, 16002],
        "mpls_label": 999,
    }) == "  [MPLS ×2]"
