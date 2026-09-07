"""v0.5.274 — OSPF state parser uses FRR structured JSON (parity
with v0.5.273's BGP treatment).

Direct unit tests on the two `_parse_ospf_v*_neighbors_json`
helpers — pure functions that don't need a container/docker
daemon. Source-level checks verify the endpoint dispatch prefers
JSON and falls back to text."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
OSPF_SRC = (REPO / "utils" / "ospf.py").read_text()


# --- Helpers exist and are exported at module level ---------------


def test_v4_and_v6_helpers_defined():
    assert "def _parse_ospf_v4_neighbors_json(payload:" in OSPF_SRC
    assert "def _parse_ospf_v6_neighbors_json(payload:" in OSPF_SRC


# --- OSPFv2 (dict-of-lists) shape --------------------------------


def test_v4_parses_full_established_neighbor():
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "neighbors": {
            "1.2.3.4": [
                {
                    "priority": 1,
                    "converged": "Full",
                    "role": "DROther",
                    "upTimeInMsec": 251206000,
                    "upTime": "2d21h47m",
                    "deadTimeMsec": 39912,
                    "address": "192.168.0.1",
                    "ifaceName": "vlan100",
                    "ifaceAddress": "192.168.0.2",
                },
            ],
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert len(out) == 1
    n = out[0]
    assert n["neighbor_id"] == "1.2.3.4"
    assert n["state"] == "Full"          # `converged` verbatim
    assert n["up_time"] == "2d21h47m"
    assert n["address"] == "192.168.0.1"
    assert n["interface"] == "vlan100"
    assert n["type"] == "IPv4"
    # Priority coerced to str (matches text-parser output shape).
    assert n["priority"] == "1"


def test_v4_handles_multiple_adjacencies_per_router_id():
    """OSPFv2 on multi-access links: same neighbor router-id can
    appear via multiple interfaces. Every entry in the array
    lands as its own neighbor dict."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "neighbors": {
            "1.2.3.4": [
                {"converged": "Full", "ifaceName": "vlan100",
                 "address": "192.168.0.1", "upTime": "01:23:45"},
                {"converged": "Full", "ifaceName": "vlan200",
                 "address": "192.168.10.1", "upTime": "01:23:45"},
            ],
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert len(out) == 2
    assert {n["interface"] for n in out} == {"vlan100", "vlan200"}
    # Both share the router-id.
    assert all(n["neighbor_id"] == "1.2.3.4" for n in out)


def test_v4_handles_empty_neighbors_dict():
    """`{"neighbors": {}}` means OSPF is running but has no
    neighbors — valid non-error state. The parser must return
    an empty list, not raise."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    assert _parse_ospf_v4_neighbors_json({"neighbors": {}}) == []
    assert _parse_ospf_v4_neighbors_json({}) == []


def test_v4_handles_2way_state_not_marked_full():
    """DR-election `2-Way` is a valid FSM state per RFC 2328 §10.1
    but doesn't count as established. The parser must emit the
    state verbatim so the downstream `'Full' in n['state']` check
    correctly rejects it."""
    from utils.ospf import _parse_ospf_v4_neighbors_json
    payload = {
        "neighbors": {
            "5.6.7.8": [{"converged": "2-Way", "ifaceName": "vlan100"}],
        },
    }
    out = _parse_ospf_v4_neighbors_json(payload)
    assert out[0]["state"] == "2-Way"
    # Downstream check: `'Full' in state` → False.
    assert "Full" not in out[0]["state"]


# --- OSPFv3 (flat-array) shape ------------------------------------


def test_v6_parses_flat_neighbor_array():
    """OSPFv3's JSON shape is different from v2 — a flat array of
    entries, each with neighborId inside (not the dict key)."""
    from utils.ospf import _parse_ospf_v6_neighbors_json
    payload = {
        "neighbors": [
            {
                "neighborId": "1.2.3.4",
                "priority": 1,
                "state": "Full",
                "duration": "2d21h47m",
                "deadTime": "00:00:39",
                "interfaceName": "vlan100",
            },
        ],
    }
    out = _parse_ospf_v6_neighbors_json(payload)
    assert len(out) == 1
    n = out[0]
    assert n["neighbor_id"] == "1.2.3.4"
    assert n["state"] == "Full"
    assert n["up_time"] == "2d21h47m"
    assert n["interface"] == "vlan100"
    assert n["type"] == "IPv6"


def test_v6_handles_empty_neighbors_array():
    from utils.ospf import _parse_ospf_v6_neighbors_json
    assert _parse_ospf_v6_neighbors_json({"neighbors": []}) == []
    assert _parse_ospf_v6_neighbors_json({}) == []


def test_v6_tolerates_dict_form_from_older_frr():
    """Some older FRR versions emitted OSPFv3 neighbors as a dict
    too. The parser must tolerate that without raising — it
    silently returns an empty list rather than emitting garbage
    (safe fallback path)."""
    from utils.ospf import _parse_ospf_v6_neighbors_json
    # `{"neighbors": {"1.2.3.4": {...}}}` — our parser ignores
    # this shape and returns empty; the caller then falls back
    # to the text parser via the outer OSPF status check.
    out = _parse_ospf_v6_neighbors_json({"neighbors": {"1.2.3.4": {}}})
    assert out == []


# --- Non-dict / garbage inputs never raise -----------------------


def test_v4_helpers_return_empty_on_garbage_input():
    from utils.ospf import _parse_ospf_v4_neighbors_json
    assert _parse_ospf_v4_neighbors_json(None) == []  # type: ignore[arg-type]
    assert _parse_ospf_v4_neighbors_json("not a dict") == []  # type: ignore[arg-type]
    assert _parse_ospf_v4_neighbors_json({"neighbors": None}) == []
    assert _parse_ospf_v4_neighbors_json({"neighbors": []}) == []


def test_v6_helpers_return_empty_on_garbage_input():
    from utils.ospf import _parse_ospf_v6_neighbors_json
    assert _parse_ospf_v6_neighbors_json(None) == []  # type: ignore[arg-type]
    assert _parse_ospf_v6_neighbors_json({"neighbors": None}) == []
    assert _parse_ospf_v6_neighbors_json({"neighbors": [None, "junk"]}) == []


# --- get_ospf_status dispatch structure --------------------------


def test_get_ospf_status_prefers_json_then_falls_back_to_text():
    """`get_ospf_status` must issue the JSON queries FIRST and only
    walk the text parser when both fail."""
    idx = OSPF_SRC.find("def get_ospf_status(device_id:")
    end = OSPF_SRC.find("\ndef ", idx + 1)
    body = OSPF_SRC[idx:end]
    # JSON commands issued.
    assert "neighbor json'" in body
    assert "OSPF-J1" in body or "v0.5.274" in body
    # Fallback marker + guard.
    assert "parse_source = \"json\"" in body
    assert "parse_source = \"text\"" in body
    # JSON commands appear BEFORE the text commands in the
    # function source — enforce ordering so a future edit doesn't
    # accidentally invert the priority.
    v4_json_idx = body.find("show ip ospf{_scope_suffix} neighbor json")
    v4_text_idx = body.find(
        "show ip ospf{_scope_suffix} neighbor'",  # trailing quote → text form
    )
    assert 0 < v4_json_idx < v4_text_idx, (
        "JSON query must precede text query in the source"
    )


def test_get_ospf_status_return_dict_carries_parse_source():
    idx = OSPF_SRC.find("def get_ospf_status(device_id:")
    end = OSPF_SRC.find("\ndef ", idx + 1)
    body = OSPF_SRC[idx:end]
    assert "'parse_source': parse_source" in body


def test_get_ospf_status_still_uses_full_substring_check():
    """The `'Full' in n['state']` check must survive the refactor
    because the JSON `converged` field can be 'Full' or (on some
    FRR builds) 'Full/DR' / 'Full/BDR' — same substring rule
    covers both."""
    idx = OSPF_SRC.find("def get_ospf_status(device_id:")
    end = OSPF_SRC.find("\ndef ", idx + 1)
    body = OSPF_SRC[idx:end]
    assert "'Full' in n['state']" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 274)
