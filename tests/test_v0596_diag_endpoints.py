"""v0.5.96 — audit batch 5: diagnostic endpoints.

  M10 — `/api/admin/health` never probed ethtool / iproute2 /
        lldpcli presence. Operator only found out on first
        action click (e.g. Down → 500 "iproute2 not installed").
        Now `tools_present` dict surfaces it up front.

  M14 — No way to dump or flush per-iface caches. Operator
        debugging a stale-cache bug (e.g. v0.5.87 LLDP wipe)
        had to restart the whole service. New
        GET /api/admin/caches + POST /api/admin/caches/flush.

  M15 — No per-iface sysfs dump. Reading /sys/class/net/<n>/
        statistics required SSH. New GET /api/admin/iface/
        <n>/sysfs returns structured leaves + counter dict.

  M2 / SEC M3 — /details endpoint forked 7 ethtool
        subprocesses uncached server-side. Tight client loop
        wedged Flask workers. 10s TTL cache bounds the burst.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# ─── M10: tools_present in /health ───


def test_health_includes_tools_present():
    src = _src()
    m = re.search(
        r"def api_admin_health[\s\S]+?return jsonify",
        src,
    )
    body = m.group(0)
    assert '"tools_present"' in body
    # Probes the actions paths' required tools.
    for tool in ("ethtool", "ip", "lldpcli"):
        assert f'"{tool}"' in body, f"health doesn't probe {tool}"


def test_health_uses_shutil_which():
    src = _src()
    m = re.search(
        r"def api_admin_health[\s\S]+?return jsonify",
        src,
    )
    body = m.group(0)
    assert "_shutil.which" in body or "shutil.which" in body


# ─── M14: /caches endpoints ───


def test_caches_dump_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/caches"' in src
    assert "def api_admin_caches(" in src


def test_caches_flush_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/caches/flush"' in src
    assert "def api_admin_caches_flush(" in src


def test_caches_dump_is_viewer_gated():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/caches"[\s\S]'
        r'{0,300}?def api_admin_caches',
        src,
    )
    assert m and "@require_role" in m.group(0)


def test_caches_flush_is_admin_gated():
    src = _src()
    m = re.search(
        r'@app\.route\("/api/admin/caches/flush"[\s\S]'
        r'{0,300}?def api_admin_caches_flush',
        src,
    )
    assert m
    assert re.search(r'@require_role\(["\']admin["\']\)', m.group(0))


def test_caches_flush_supports_selective_flush():
    src = _src()
    m = re.search(
        r"def api_admin_caches_flush[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # `which=ethtool` / `which=lldp` / `which=all`.
    assert 'which' in body
    assert '"ethtool"' in body
    assert '"lldp"' in body
    assert '"all"' in body or "all" in body


# ─── M15: /iface/<n>/sysfs ───


def test_iface_sysfs_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/iface/<iface>/sysfs"' in src
    assert "def api_admin_iface_sysfs(" in src


def test_sysfs_helper_reads_canonical_leaves():
    src = _src()
    m = re.search(
        r"def _iface_sysfs_dump[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    for leaf in ("carrier", "operstate", "speed", "mtu", "address"):
        assert f'"{leaf}"' in body, f"sysfs dump missing {leaf}"
    # statistics/* counter dict.
    assert "statistics" in body


def test_sysfs_endpoint_validates_iface_name():
    src = _src()
    m = re.search(
        r"def api_admin_iface_sysfs[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_RE_IFACE_NAME" in body


# ─── M2: details TTL cache ───


def test_iface_details_cache_helper_defined():
    src = _src()
    assert "_IFACE_DETAILS_CACHE" in src
    assert "_IFACE_DETAILS_CACHE_TTL" in src
    assert "_IFACE_DETAILS_LOCK" in src
    assert "def _iface_details_cached(" in src


def test_details_endpoint_uses_cache():
    src = _src()
    m = re.search(
        r"def api_admin_iface_details[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    assert "_iface_details_cached(iface)" in body, (
        "details endpoint still calls _ethtool_full_dump "
        "directly — every request forks 7 subprocesses"
    )


def test_details_cache_ttl_bounded():
    src = _src()
    m = re.search(
        r"_IFACE_DETAILS_CACHE_TTL\s*=\s*([0-9.]+)",
        src,
    )
    ttl = float(m.group(1))
    # 1-60s window; too long hides real iface changes.
    assert 1.0 <= ttl <= 60.0


# ─── Integration: end-to-end via Flask test_client ───


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    flask = pytest.importorskip("flask")
    import os
    tmp = tmp_path_factory.mktemp("netgen_db")
    os.environ["NETGEN_DB_PATH"] = str(tmp / "device.db")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_tgen_server", str(_SERVER),
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    server.app.config["TESTING"] = True
    return server.app.test_client(), server


def test_caches_dump_returns_known_shape(client):
    c, _server = client
    r = c.get("/api/admin/caches")
    assert r.status_code == 200
    data = r.get_json()
    for k in ("ethtool", "drvinfo", "iface_details", "lldp"):
        assert k in data
        assert "count" in data[k]
        assert "ttl_s" in data[k]


def test_caches_flush_clears_state(client):
    c, server = client
    # Seed the ethtool cache so we can verify it gets cleared.
    server._ETHTOOL_CACHE["eth_smoke"] = (0.0, {})
    assert "eth_smoke" in server._ETHTOOL_CACHE
    r = c.post("/api/admin/caches/flush",
               json={"which": "ethtool"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert "ethtool" in body["cleared"]
    assert "eth_smoke" not in server._ETHTOOL_CACHE


def test_iface_sysfs_returns_empty_on_missing_iface(client):
    c, _server = client
    r = c.get("/api/admin/iface/nopenope/sysfs")
    assert r.status_code == 200
    data = r.get_json()
    assert data["interface"] == "nopenope"
    # Helper returns either {error: "no sysfs entry"} or {}.
    sysfs = data["sysfs"]
    assert "error" in sysfs or sysfs == {} or not sysfs


# ─── Version ───


def test_pyproject_version_at_least_0596():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 96)
