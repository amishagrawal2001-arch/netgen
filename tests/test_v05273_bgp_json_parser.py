"""v0.5.273 — BGP state parser uses FRR structured JSON.

The parser tests mock `frr_manager.client.containers.get(...).exec_run`
so no live container (or Docker daemon) is required. The endpoint's
response-shape and fallback-precedence are verified source-level
(the full Flask app can't be imported in tests without /opt/netgen).

Docker-daemon avoidance: `utils.frr_docker.frr_manager` is a
_LazyFRRManager proxy that instantiates the real FRRDockerManager
(→ docker.from_env()) only on first missing attribute. Assigning
`.client` / `.` attrs DIRECTLY on the proxy shadows the fall-through
so the daemon is never contacted. `patch.object` can't be used here
because it performs a `hasattr` check that triggers the fall-through
BEFORE the patch installs."""

from pathlib import Path
from types import SimpleNamespace
import json
import re

import pytest

REPO = Path(__file__).resolve().parents[1]
SRV = (REPO / "run_tgen_server.py").read_text()
FRR = (REPO / "utils" / "frr_docker.py").read_text()


# --- Source-level: helper exists + endpoint prefers JSON ---------


def test_get_bgp_status_json_defined_in_frr_docker():
    assert "def get_bgp_status_json(device_id: str" in FRR
    idx = FRR.find("def get_bgp_status_json")
    end = FRR.find("\ndef ", idx + 1)
    body = FRR[idx:end]
    assert "v0.5.273" in body
    assert "summary json" in body
    assert "ipv6 summary json" in body
    assert "def _unwrap_vrf" in body
    assert 'peers: Dict[str, Dict[str, Any]] = {}' in body
    assert '"status": "error"' in body


def test_endpoint_prefers_json_and_falls_back_on_error():
    idx = SRV.find("def get_device_bgp_status(device_id):")
    end = SRV.find("\n@app.route", idx + 1)
    body = SRV[idx:end]
    assert "v0.5.273" in body
    assert "get_bgp_status_json(device_id, device_name)" in body
    json_ok_idx = body.find('json_payload.get("status") == "success"')
    text_status_call = body.find("bgp_status = get_bgp_status(device_id, device_name)")
    assert 0 < json_ok_idx < text_status_call, (
        "text-parser call must appear AFTER the json-success return"
    )
    assert '"parse_source": "json"' in body
    assert "'parse_source': 'text'" in body


def test_endpoint_response_shape_is_stable_across_paths():
    idx = SRV.find("def get_device_bgp_status(device_id):")
    end = SRV.find("\n@app.route", idx + 1)
    body = SRV[idx:end]
    for field in (
        "neighbors", "bgp_established", "bgp_ipv4_established",
        "bgp_ipv6_established", "bgp_state",
    ):
        assert body.count(f'"{field}"') + body.count(f"'{field}'") >= 2, (
            f"field {field!r} missing from one of the response paths"
        )


# --- Live helper: mock exec_run so no docker container is needed -


def _fixture_established_dual_stack() -> tuple:
    """(vtysh_v4_json_string, vtysh_v6_json_string) — the two answers
    an FRR container would give when queried. Uses the `vrf all`
    envelope which the helper's _unwrap_vrf must strip."""
    v4 = {
        "vrf-abc123": {
            "ipv4Unicast": {
                "routerId": "1.2.3.4",
                "as": 65000,
                "vrfName": "vrf-abc123",
                "peers": {
                    "192.168.0.1": {
                        "remoteAs": 65000,
                        "peerUptime": "2d21h47m",
                        "state": "Established",
                        "pfxRcd": 0,
                    },
                },
            },
        },
    }
    v6 = {
        "vrf-abc123": {
            "ipv6Unicast": {
                "peers": {
                    "2001:db8::1": {
                        "remoteAs": 65000,
                        "peerUptime": "2d21h11m",
                        "state": "Established",
                    },
                },
            },
        },
    }
    return json.dumps(v4), json.dumps(v6)


def _fake_container(v4_json: str, v6_json: str) -> SimpleNamespace:
    """Build a mock container whose exec_run returns the right JSON
    based on which query is issued (v4 vs ipv6)."""
    def _exec(cmd: str) -> SimpleNamespace:
        # Return the ipv6 payload when the command mentions ipv6.
        if "ipv6 summary" in cmd:
            return SimpleNamespace(exit_code=0, output=v6_json.encode())
        return SimpleNamespace(exit_code=0, output=v4_json.encode())
    return SimpleNamespace(exec_run=_exec)


@pytest.fixture
def stub_docker():
    """Yield a helper that installs a fake client + name-getter on
    the lazy FRR proxy WITHOUT triggering the real docker.from_env.

    `patch.object` isn't usable here — it calls `hasattr` on the
    target which fires the proxy's `__getattr__` → `_get()` → real
    manager init → docker daemon connect. Setting attributes
    directly bypasses that; `__getattr__` only fires for missing
    names, so an assigned attr shadows the fall-through cleanly.

    Cleans up after the test so the proxy is restored to its
    unpatched state."""
    from utils import frr_docker as fd
    installed_names: list = []

    def _install(v4_json: str, v6_json: str,
                 raise_on_get=None,
                 container_name: str = "frr-dev1"):
        # Fake client whose containers.get returns our mock container
        # (or raises if raise_on_get is set — for the exec-failure test).
        fake_container = _fake_container(v4_json, v6_json)
        fake_client = SimpleNamespace(
            containers=SimpleNamespace(
                get=(lambda *_a, **_k: (_ for _ in ()).throw(raise_on_get))
                    if raise_on_get else
                    (lambda *_a, **_k: fake_container)
            )
        )
        fd.frr_manager.client = fake_client
        fd.frr_manager._get_container_name = (
            lambda *_a, **_k: container_name
        )
        installed_names.extend(["client", "_get_container_name"])

    yield _install

    # Restore: pop the shadowed instance attrs so subsequent tests
    # (or code paths that DO have real docker) can fall through to
    # the real manager again.
    for name in installed_names:
        try:
            delattr(fd.frr_manager, name)
        except AttributeError:
            pass


def test_helper_flattens_dual_stack_json_into_peers_dict(stub_docker):
    """get_bgp_status_json must merge the v4 + v6 answers into a
    flat peers: {ip → obj} dict, with a `family` marker on each
    entry."""
    from utils import frr_docker as fd
    v4, v6 = _fixture_established_dual_stack()
    stub_docker(v4, v6)

    payload = fd.get_bgp_status_json("dev1", "dev1")

    assert payload["status"] == "success"
    assert set(payload["peers"].keys()) == {"192.168.0.1", "2001:db8::1"}
    assert payload["peers"]["192.168.0.1"]["state"] == "Established"
    assert payload["peers"]["192.168.0.1"]["family"] == "ipv4"
    assert payload["peers"]["2001:db8::1"]["family"] == "ipv6"
    # peerUptime carried through verbatim — no shape guessing.
    assert payload["peers"]["192.168.0.1"]["peerUptime"] == "2d21h47m"


def test_helper_handles_idle_peer(stub_docker):
    """Explicit `"state": "Idle"` comes through verbatim — pre-fix
    text parser sometimes coerced this to "Unknown"."""
    from utils import frr_docker as fd
    v4 = json.dumps({
        "vrf-abc": {
            "ipv4Unicast": {
                "peers": {
                    "192.168.0.1": {
                        "remoteAs": 65000,
                        "state": "Idle",
                        "peerUptime": "never",
                    },
                },
            },
        },
    })
    v6 = json.dumps({})
    stub_docker(v4, v6)

    payload = fd.get_bgp_status_json("dev1")

    assert payload["status"] == "success"
    assert payload["peers"]["192.168.0.1"]["state"] == "Idle"


def test_helper_returns_error_on_exec_failure(stub_docker):
    """A container-gone / vtysh exec failure must return
    status=error so the endpoint layer knows to fall back to the
    text parser (rather than pretending the peers dict is empty)."""
    from utils import frr_docker as fd
    stub_docker("", "", raise_on_get=RuntimeError("container gone"))

    payload = fd.get_bgp_status_json("dev1")

    assert payload["status"] == "error"
    assert "reason" in payload


def test_helper_returns_success_with_empty_peers_on_malformed_json(stub_docker):
    """FRR versions that don't support JSON (pre-7.0) emit the
    text summary instead — that isn't valid JSON. The _run helper
    returns None on parse failure, which unwraps to {} peers.
    Endpoint then sees no neighbors and emits bgp_state="Unknown"
    — the correct legacy-FRR fallback signal (also triggers the
    endpoint's text-parser fallback if we treat empty-peers as
    the JSON path failing)."""
    from utils import frr_docker as fd
    garbage = "BGP router identifier 1.2.3.4, local AS number 65000"
    stub_docker(garbage, garbage)

    payload = fd.get_bgp_status_json("dev1")

    assert payload["status"] == "success"
    assert payload["peers"] == {}


def test_helper_handles_single_query_top_level_shape(stub_docker):
    """When the vtysh query targets ONE vrf (not `vrf all`), FRR
    puts ipv4Unicast at top-level directly (no per-VRF envelope).
    The helper's _unwrap_vrf must leave that shape alone."""
    from utils import frr_docker as fd
    v4 = json.dumps({
        "ipv4Unicast": {
            "peers": {
                "10.0.0.1": {
                    "remoteAs": 100,
                    "state": "Established",
                    "peerUptime": "01:23:45",
                },
            },
        },
    })
    v6 = json.dumps({})
    stub_docker(v4, v6)

    payload = fd.get_bgp_status_json("dev1")

    assert payload["peers"]["10.0.0.1"]["state"] == "Established"
    assert payload["peers"]["10.0.0.1"]["family"] == "ipv4"


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 273)
