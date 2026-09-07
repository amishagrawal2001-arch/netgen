"""v0.5.276 — OSPF JSON parser unwraps FRR's per-VRF envelope.

Regression from v0.5.274: `show ip ospf vrf <name> neighbor json`
wraps the answer as `{<vrf>: {"neighbors": {...}}}` in FRR's
VRF-scoped queries. My v0.5.274 parser read `.get("neighbors")`
off the OUTER dict — always None for VRF-scoped queries — so
OSPFv4 collapsed to "No Neighbors" while the switch showed Full.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
OSPF_SRC = (REPO / "utils" / "ospf.py").read_text()


# --- Helper exists ------------------------------------------------


def test_ospf_unwrap_vrf_envelope_helper_defined():
    assert "def _ospf_unwrap_vrf_envelope" in OSPF_SRC
    idx = OSPF_SRC.find("def _ospf_unwrap_vrf_envelope")
    end = OSPF_SRC.find("\ndef ", idx + 1)
    body = OSPF_SRC[idx:end]
    # v0.5.276 marker + rationale cite in the docstring.
    assert "v0.5.276" in body
    # Both branches: passthrough when top-level neighbors present;
    # merge when it's a VRF envelope.
    assert "if \"neighbors\" in payload:" in body
    assert "merged_neighbors" in body


def test_parsers_call_unwrap_before_reading_neighbors():
    """Both `_parse_ospf_v4_neighbors_json` and
    `_parse_ospf_v6_neighbors_json` must pipe the payload through
    the unwrap helper BEFORE reading `.get("neighbors")` — that's
    the ordering the fix depends on."""
    for parser_name in (
        "_parse_ospf_v4_neighbors_json",
        "_parse_ospf_v6_neighbors_json",
    ):
        idx = OSPF_SRC.find(f"def {parser_name}(")
        end = OSPF_SRC.find("\ndef ", idx + 1)
        body = OSPF_SRC[idx:end]
        unwrap_idx = body.find("_ospf_unwrap_vrf_envelope(payload)")
        neighbors_idx = body.find('.get("neighbors")')
        assert 0 < unwrap_idx < neighbors_idx, (
            f"{parser_name}: unwrap must precede .get('neighbors')"
        )


# --- v4 parser: VRF-enveloped payload (the operator's actual case)


def test_v4_parser_unwraps_vrf_envelope():
    """The exact shape FRR returns for `show ip ospf vrf <name>
    neighbor json` — {"vrf-abc": {"neighbors": {...}}}. The
    v0.5.274 parser read `.get("neighbors")` off the outer and
    got None → zero neighbors → UI showed 'No Neighbors' while
    the switch reported Full."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "vrf-5c394c9ebae": {
            "neighbors": {
                "192.255.0.1": [
                    {
                        "priority": 1,
                        "converged": "Full",
                        "role": "DROther",
                        "upTime": "2d21h47m",
                        "deadTimeMsec": 39912,
                        "address": "192.168.0.1",
                        "ifaceName": "vlan100",
                    },
                ],
            },
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert len(out) == 1
    assert out[0]["neighbor_id"] == "192.255.0.1"
    assert out[0]["state"] == "Full"
    assert out[0]["interface"] == "vlan100"
    assert out[0]["type"] == "IPv4"


def test_v4_parser_still_handles_unwrapped_shape():
    """Back-compat: single-scope queries (`show ip ospf neighbor
    json` without vrf) return `{"neighbors": {...}}` at top-level.
    The unwrap helper must be a no-op in that case."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "neighbors": {
            "1.2.3.4": [{"converged": "Full", "ifaceName": "eth0"}],
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert len(out) == 1
    assert out[0]["state"] == "Full"


def test_v4_parser_merges_multiple_vrfs():
    """Defensive: if a `vrf all` query ever landed here (currently
    the endpoint scopes to one VRF, but someday it might change),
    all children's neighbors should merge into one flat dict."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "vrf-a": {
            "neighbors": {"1.1.1.1": [{"converged": "Full"}]},
        },
        "vrf-b": {
            "neighbors": {"2.2.2.2": [{"converged": "Full"}]},
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert len(out) == 2
    assert {n["neighbor_id"] for n in out} == {"1.1.1.1", "2.2.2.2"}


# --- v6 parser: VRF-enveloped array shape -----------------------


def test_v6_parser_unwraps_vrf_envelope():
    """OSPFv3 with `show ipv6 ospf6 vrf <name> neighbor json`
    wraps a flat-array `neighbors` inside the VRF key. The v0.5.274
    parser missed this too — the same fix pattern applies."""
    from utils.ospf import _parse_ospf_v6_neighbors_json
    payload = {
        "vrf-5c394c9ebae": {
            "neighbors": [
                {
                    "neighborId": "192.255.0.1",
                    "priority": 1,
                    "state": "Full",
                    "duration": "2d21h47m",
                    "interfaceName": "vlan100",
                },
            ],
        },
    }
    out = _parse_ospf_v6_neighbors_json(payload)
    assert len(out) == 1
    assert out[0]["neighbor_id"] == "192.255.0.1"
    assert out[0]["state"] == "Full"
    assert out[0]["type"] == "IPv6"


def test_v6_parser_still_handles_unwrapped_shape():
    from utils.ospf import _parse_ospf_v6_neighbors_json
    payload = {
        "neighbors": [
            {"neighborId": "1.2.3.4", "state": "Full"},
        ],
    }
    out = _parse_ospf_v6_neighbors_json(payload)
    assert len(out) == 1
    assert out[0]["state"] == "Full"


# --- Helper corner cases ----------------------------------------


def test_unwrap_helper_leaves_empty_and_garbage_alone():
    from utils.ospf import _ospf_unwrap_vrf_envelope
    assert _ospf_unwrap_vrf_envelope({}) == {}
    assert _ospf_unwrap_vrf_envelope({"neighbors": {}}) == {"neighbors": {}}
    # Non-dict input safely returns empty.
    assert _ospf_unwrap_vrf_envelope(None) == {}  # type: ignore[arg-type]
    # A dict whose values don't contain `neighbors` returns
    # unchanged — the parser will then get None from .get(
    # "neighbors") and emit zero (correct — nothing to report).
    misc = {"vrf-x": {"routerId": "1.1.1.1"}}
    assert _ospf_unwrap_vrf_envelope(misc) == misc


# --- Metadata ---------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 276)
