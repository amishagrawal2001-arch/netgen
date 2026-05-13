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
