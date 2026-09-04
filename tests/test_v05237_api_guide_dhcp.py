"""v0.5.237 — API_GUIDE.md documents the DHCP endpoints properly.

Pre-fix, the DHCP section had three one-liner rows and zero details
on request/response bodies. `POST /api/device/dhcp/restart` (added
in v0.5.231) wasn't listed at all. This ship expands the DHCP
section with contracts for all four endpoints plus the named-pool
catalog and the v0.5.231 per-device Apply lock.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
GUIDE = (REPO / "API_GUIDE.md").read_text()


def test_all_four_dhcp_endpoints_listed():
    for path in (
        "`/api/device/dhcp/status`",
        "`/api/device/dhcp/server/pool`",
        "`/api/device/dhcp/server/attach_pools`",
        "`/api/device/dhcp/restart`",
    ):
        assert path in GUIDE, f"missing {path}"


def test_restart_endpoint_has_contract():
    """The v0.5.231 restart endpoint must show the request shape,
    the success response shape, and the error codes.

    v0.5.252: widened the slice to 4500 — v0.5.251 rewrote the
    restart contract to document the three response shapes
    (restarted / restarted_pending_lease / HTTP 500 with real
    reason), which pushed the 400/404/500 status-code block well
    past the pre-v0.5.251 1500-char window."""
    idx = GUIDE.find("`POST /api/device/dhcp/restart`")
    body = GUIDE[idx:idx + 4500]
    assert '"device_id"' in body
    assert '"status":           "restarted"' in body
    assert "400" in body and "404" in body and "500" in body


def test_attach_pools_documents_ipv6_preserve_and_dedup():
    idx = GUIDE.find("`POST /api/device/dhcp/server/attach_pools`")
    body = GUIDE[idx:idx + 2000]
    assert "v0.5.235" in body and "preserves ALL IPv6 config" in body
    assert "v0.5.236" in body and "dropped from `additional_pools`" in body


def test_pool_endpoint_documents_validation():
    idx = GUIDE.find("`POST /api/device/dhcp/server/pool`")
    body = GUIDE[idx:idx + 2000]
    assert "gateway is outside the pool subnet" in body
    assert "v0.5.236" in body or "v0.5.229" in body


def test_status_endpoint_documents_new_fields():
    idx = GUIDE.find("`GET /api/device/dhcp/status`")
    body = GUIDE[idx:idx + 2500]
    for field in ("dhcp_config", "server_interface_ip", "pool6_range"):
        assert field in body, f"status response missing {field}"


def test_apply_lock_documented():
    assert "_APPLY_LOCKS" in GUIDE
    assert "HTTP 409" in GUIDE
    assert "still in flight" in GUIDE


def test_named_pool_catalog_documented():
    for path in ("`/api/dhcp/pools`", "`/api/dhcp/pools/<pool_name>`"):
        assert path in GUIDE
    # v0.5.231 IPv6 fields on the pool payload.
    assert '"pool6_start"' in GUIDE
    assert '"prefix6"' in GUIDE


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 237)
