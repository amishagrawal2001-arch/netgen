"""Regression test for the v0.4.2 stream-name resolution fix.

Operator scenario from svl-d-ai-srv04: client sent a Scapy stream
payload where the name 'ICMP' lived ONLY inside protocol_selection
(not at the top level). The server's launch_single_stream and the
streams loop in restart_stream both used the dumb fallback
``stream_data.get("name", "Unnamed Stream")`` and stored the row
as "Unnamed Stream". Operator saw "Unnamed Stream" in the Stream
statistics table even though the dialog showed 'ICMP'.

Fix: both spots now delegate to ``_resolve_stream_name`` from
``multithreaded_traffic_gen``, which walks the fallback chain
(top-level name / stream_name / display_name / title →
protocol_selection.name / stream_name → composite
``<port> / <L4> [<sid>]``).

These tests pin the resolver behavior + a smoke check that
restoring a stream record without a top-level name field still
ends up with a sensible name."""
from __future__ import annotations


def test_top_level_name_takes_precedence():
    """Most direct case — operator set Name='my-stream' in the
    dialog → top-level 'name' is the source of truth."""
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"name": "my-stream", "protocol_selection": {"name": "ICMP"}}
    assert _resolve_stream_name(sd, "eth0", "abc12345-..") == "my-stream"


def test_falls_back_to_protocol_selection_name():
    """Operator scenario from the field: stream restarted from a
    saved config that put the name inside protocol_selection. No
    top-level 'name'. Pre-fix returned 'Unnamed Stream'; now
    returns 'ICMP'."""
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"protocol_selection": {"name": "ICMP"}}
    assert _resolve_stream_name(sd, "eth0", "abc12345-..") == "ICMP"


def test_falls_back_to_stream_name_top_level():
    """Some clients write 'stream_name' instead of 'name'. Walk
    that alias too."""
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"stream_name": "rfc2544-64B"}
    assert _resolve_stream_name(sd, "eth0", "abc12345-..") == "rfc2544-64B"


def test_skips_placeholder_unnamed_stream_value():
    """If 'name' literally is the string 'Unnamed Stream' (e.g.
    persisted from a previous broken version), treat it as unset
    and walk further down the fallback chain."""
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {
        "name": "Unnamed Stream",
        "protocol_selection": {"name": "ICMP"},
    }
    assert _resolve_stream_name(sd, "eth0", "abc12345-..") == "ICMP"


def test_skips_blank_and_whitespace_only_names():
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"name": "   ", "protocol_selection": {"name": "ICMP"}}
    assert _resolve_stream_name(sd, "eth0", "abc12345-..") == "ICMP"


def test_composite_fallback_uses_port_L4_shortid():
    """When NOTHING is set, the resolver builds
    ``<port> / <L4> [<short-id>]`` instead of returning 'Unnamed
    Stream'. Operator-readable + uniquely identifies the row."""
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"L4": "UDP", "port": "eth0"}
    name = _resolve_stream_name(sd, "eth0", "abc12345-deadbeef")
    assert "eth0" in name
    assert "UDP" in name
    assert "abc12345" in name  # short-id prefix


def test_composite_fallback_uses_interface_when_no_port():
    from multithreaded_traffic_gen import _resolve_stream_name
    sd = {"L4": "TCP"}
    name = _resolve_stream_name(sd, "enp181s0f0np0", "xyz12345-..")
    assert "enp181s0f0np0" in name
    assert "TCP" in name


def test_run_tgen_server_uses_resolver_in_both_spots():
    """Pin that BOTH launch_single_stream and the streams-loop in
    restart_stream call _resolve_stream_name — a refactor that
    silently regressed to ``stream_data.get("name", "Unnamed
    Stream")`` would re-bite this bug."""
    src = open("/Users/surajsharma/dev/netgen/run_tgen_server.py").read()
    # No raw '"Unnamed Stream"' default-fallback assignments should
    # remain in the launch path. (Other places may legitimately
    # mention the string in error messages or comments.)
    import re
    assignments = re.findall(
        r'stream_name\s*=\s*stream_data\.get\("name",\s*"Unnamed Stream"\)',
        src,
    )
    assert assignments == [], (
        f"Found {len(assignments)} `stream_name = stream_data.get('name', "
        f"'Unnamed Stream')` assignment(s) in run_tgen_server.py — "
        f"these regress the operator-reported 'Unnamed Stream' bug. "
        f"Use _resolve_stream_name instead."
    )
    # And the resolver IS being called
    assert "_resolve_stream_name(stream_data" in src, (
        "run_tgen_server.py no longer imports / uses "
        "_resolve_stream_name; the v0.4.2 fix has been reverted"
    )
