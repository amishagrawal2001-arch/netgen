"""Unit tests for the per-device VRF wiring helpers.

Targets the pure-Python parts of utils.frr_docker that don't touch the
network or Docker — just the naming / table-allocation / state-shape
contracts. The kernel side (`ip link add ... type vrf`) needs a real
host and is intentionally out of scope here; that's covered by the
manual smoke tests in run_tgen_server.

Run with: pytest -v tests/test_vrf_wiring.py
"""

from unittest.mock import MagicMock, patch
import sys

# utils/frr_docker.py instantiates `frr_manager = FRRDockerManager()` at
# module-load time which calls docker.from_env() — that throws on a
# host with no Docker daemon (developer laptops, CI runners). Pre-empt
# by installing a dummy `docker` module before frr_docker is imported.
if "docker" not in sys.modules:
    import types
    _fake_docker = types.ModuleType("docker")
    _fake_docker.from_env = MagicMock(return_value=MagicMock())
    _fake_errors = types.ModuleType("docker.errors")
    _fake_errors.NotFound = type("NotFound", (Exception,), {})
    _fake_docker.errors = _fake_errors
    _fake_types = types.ModuleType("docker.types")
    _fake_types.IPAMConfig = MagicMock()
    _fake_types.IPAMPool = MagicMock()
    _fake_docker.types = _fake_types
    sys.modules["docker"] = _fake_docker
    sys.modules["docker.errors"] = _fake_errors
    sys.modules["docker.types"] = _fake_types


def test_vrf_name_format():
    """vrf_name_for_device() returns 'vrf-<short>' truncated to fit the
    15-char Linux iface-name limit (11 hex chars + 4-char 'vrf-' prefix)."""
    from utils.frr_docker import FRRDockerManager

    with patch("utils.frr_docker.docker.from_env"):
        mgr = FRRDockerManager()
    # Typical UUID
    assert mgr.vrf_name_for_device(
        "47dce96a-348a-43ec-9eed-8223c67378b1"
    ) == "vrf-47dce96a348"
    # Hyphens stripped before truncation
    assert mgr.vrf_name_for_device("aa-bb-cc-dd-ee") == "vrf-aabbccddee"
    # Empty falls back to the sentinel
    assert mgr.vrf_name_for_device("") == "vrf-default"
    assert mgr.vrf_name_for_device(None) == "vrf-default"
    # Output never exceeds Linux IFNAMSIZ - 1 (15 chars)
    long_id = "x" * 64
    assert len(mgr.vrf_name_for_device(long_id)) <= 15


def test_vrf_table_id_is_deterministic_and_in_range():
    """vrf_table() should map a given device_id to the same table number
    on every call, and the number must land inside the 1000..1999 band
    reserved by VRF_TABLE_BASE."""
    from utils.frr_docker import FRRDockerManager

    with patch("utils.frr_docker.docker.from_env"):
        mgr = FRRDockerManager()
    samples = [
        "47dce96a-348a-43ec-9eed-8223c67378b1",
        "9286ba6e-d7b5-4c46-ba79-65b41cd8f4e8",
        "device-1",
        "",  # edge: empty
    ]
    for s in samples:
        t1 = mgr._vrf_table(s)
        t2 = mgr._vrf_table(s)
        assert t1 == t2, f"table id not stable for {s!r}: {t1} vs {t2}"
        assert mgr.VRF_TABLE_BASE <= t1 < mgr.VRF_TABLE_BASE + 1000, (
            f"table {t1} for {s!r} is outside the 1000..1999 band"
        )


def test_managed_container_prefixes():
    """The orphan-cleanup + VRF-args helpers depend on
    _MANAGED_CONTAINER_PREFIXES containing both regular and DHCP-mode
    container prefixes — regressions here would make cleanup either
    miss orphans or wipe live containers (we already shipped the user
    a 'Stop deletes my container' bug from getting this wrong)."""
    from utils.frr_docker import _MANAGED_CONTAINER_PREFIXES

    assert "ostg-frr-" in _MANAGED_CONTAINER_PREFIXES
    assert "dhcp-frr-" in _MANAGED_CONTAINER_PREFIXES
    # Both end with `-` so concatenation with a device_id can't yield
    # ambiguous names like ostg-frrdevice1 (would defeat the prefix
    # check).
    for p in _MANAGED_CONTAINER_PREFIXES:
        assert p.endswith("-"), f"prefix {p!r} missing trailing dash"


def test_bgp_vtysh_scope_falls_back_when_no_device_id():
    """_bgp_vtysh_scope() returns 'vrf all' when given an empty
    device_id. That's the single-device / legacy fallback path used
    by aggregate routes like /api/bgp/statistics."""
    from utils.frr_docker import _bgp_vtysh_scope, frr_manager

    with patch.object(frr_manager, "vrf_name_for_device", return_value=""):
        assert _bgp_vtysh_scope("") == "vrf all"


def test_bgp_vtysh_scope_emits_named_vrf_for_real_device():
    """For a non-empty device_id whose VRF name resolves cleanly,
    _bgp_vtysh_scope() emits the per-device 'vrf <name>' qualifier
    — this is what every BGP show/configure path now relies on."""
    from utils.frr_docker import _bgp_vtysh_scope, frr_manager

    with patch.object(frr_manager, "vrf_name_for_device", return_value="vrf-deadbeef123"):
        assert _bgp_vtysh_scope("deadbeef-1234") == "vrf vrf-deadbeef123"


def test_managed_prefix_round_trip():
    """Container name → device_id → container name should round-trip
    cleanly using the canonical prefix list. Regressions here have
    caused the VXLAN underlay-route code to drop VRF context for
    DHCP-client containers (commit 1bab054)."""
    from utils.frr_docker import _MANAGED_CONTAINER_PREFIXES

    samples = [
        ("ostg-frr-",  "47dce96a-348a-43ec-9eed-8223c67378b1"),
        ("dhcp-frr-",  "9286ba6e-d7b5-4c46-ba79-65b41cd8f4e8"),
    ]
    for pfx, did in samples:
        name = f"{pfx}{did}"
        # Find the matching prefix the way cleanup_all_containers /
        # _device_vrf_args do, and recover the device_id from it.
        matched = next((p for p in _MANAGED_CONTAINER_PREFIXES if name.startswith(p)), None)
        assert matched == pfx
        recovered = name[len(matched):]
        assert recovered == did
