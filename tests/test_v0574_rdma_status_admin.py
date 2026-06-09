"""v0.5.74 — RDMA status in admin console + auth + strict bool.

Audit findings F1 + F3 + F6 from the RDMA audit. Operator
explicitly asked for the admin console to show RDMA status,
which is exactly F6's recommendation.

F1: 3 destructive RDMA endpoints (/perftest/start, /perftest/stop,
/handshakes/<id> DELETE) had no @require_role. Mirrors v0.5.68
DPDK gating.

F3: bidirectional/use_event/cpu_util/report_gbits/forget_pair
flags used truthy coercion — "false" (string) enabled them.
Mirrors v0.5.68 strict bool fix.

F6: /api/admin/health had ZERO RDMA state. Adds
out["rdma"]={perftest_installed, tools, modules_loaded,
hca_count, ports_active, ports_total} + degraded-state issues
("RDMA installed but kernel modules missing", "0/N ports
active"). Admin HTML gets a new RDMA Stack card.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"
_PERF = _REPO / "utils" / "rdma_perf.py"


def _src() -> str:
    return _SERVER.read_text()


def _health_body() -> str:
    src = _src()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    )
    assert m
    return m.group(0)


# ──────────────── F1: auth ────────────────


def test_rdma_destructive_endpoints_gated():
    src = _src()
    for route in (
        "/api/rdma/perftest/start",
        "/api/rdma/perftest/stop",
        "/api/rdma/handshakes/<handshake_id>",
    ):
        assert re.search(
            r'@app\.route\("' + re.escape(route)
            + r'"[\s\S]{0,200}?@require_role\([\s"\']*operator',
            src,
        ), (
            f"{route} not protected by @require_role(\"operator\")"
        )


# ──────────────── F3: strict bool ────────────────


def test_perftest_cmd_uses_strict_true_for_bools():
    """The argv builder in utils/rdma_perf.py must check
    booleans with `is True`, not truthy coercion."""
    src = _PERF.read_text()
    # Find the _build_perftest_cmd function.
    m = re.search(
        r"def _build_perftest_cmd[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m
    body = m.group(0)
    # Helper `_opt_true(v)` or inline `is True` checks for the
    # flag fields.
    assert re.search(
        r"_opt_true\(opts\.get\([\"']bidirectional",
        body,
    ) or re.search(
        r"opts\.get\([\"']bidirectional[\"']\)\s+is\s+True",
        body,
    ), (
        "bidirectional doesn't use strict bool check — string "
        "'false' still enables -b"
    )


def test_stop_endpoint_strict_bool_on_forget_pair():
    """The stop endpoint reads forget_pair from the JSON body.
    Must use _strict_true."""
    src = _src()
    stop = re.search(
        r"def perftest_stop\(\)|@app\.route\([\"']/api/rdma/perftest/stop[\"'][\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    # Look for _strict_true on forget_pair.
    assert re.search(
        r"_strict_true\(body\.get\([\"']forget_pair",
        src,
    ), (
        "stop endpoint doesn't use _strict_true on forget_pair"
    )


# ──────────────── F6: RDMA in /api/admin/health ────────────────


def test_health_includes_rdma_block():
    body = _health_body()
    assert '"perftest_installed"' in body
    assert '"modules_loaded"' in body
    assert '"hca_count"' in body
    assert '"ports_active"' in body
    assert '"ports_total"' in body


def test_health_probes_perftest_tools():
    """The tools dict should at least probe ib_send_bw — the
    minimum DPDK-class workhorse perftest binary."""
    body = _health_body()
    assert "ib_send_bw" in body, (
        "RDMA tools probe doesn't include ib_send_bw"
    )


def test_health_probes_rdma_kernel_modules():
    """All canonical RDMA modules (matches v0.5.62 install_rdma.sh
    set) should be probed: ib_uverbs, rdma_cm, rdma_ucm, ib_umad,
    iw_cm."""
    body = _health_body()
    for mod in ("ib_uverbs", "rdma_cm", "rdma_ucm", "ib_umad", "iw_cm"):
        assert f'"{mod}"' in body, (
            f"/api/admin/health doesn't probe {mod}"
        )


def test_health_reads_sysfs_for_hca_ports():
    body = _health_body()
    assert "/sys/class/infiniband" in body, (
        "/api/admin/health doesn't enumerate /sys/class/infiniband"
    )


def test_health_issues_list_flags_missing_rdma_modules():
    body = _health_body()
    assert "RDMA installed but kernel modules missing" in body, (
        "issues list doesn't flag missing RDMA modules"
    )


def test_health_issues_list_flags_zero_active_ports():
    body = _health_body()
    assert re.search(
        r"0/\{[^}]+\}\s+ports\s+active|0/\d+\s+ports\s+active|ports active",
        body,
    ), (
        "issues list doesn't flag 0/N RDMA ports active"
    )


def test_admin_html_includes_rdma_card():
    src = _src()
    # The card has a heading "RDMA Stack".
    assert "<h2>RDMA Stack</h2>" in src, (
        "Admin HTML doesn't include an RDMA Stack card"
    )
    # And surfaces the four state elements.
    for elem_id in ("p-rdma-perftest", "p-rdma-mods",
                     "p-rdma-hca-count", "p-rdma-ports"):
        assert f'id="{elem_id}"' in src, (
            f"Admin RDMA card missing element {elem_id}"
        )


def test_admin_js_renders_rdma_card_from_health_response():
    src = _src()
    assert "d.rdma" in src, (
        "Admin JS doesn't reference d.rdma from the health response"
    )
    assert "rdma.perftest_installed" in src
    assert "rdma.modules_loaded" in src
    assert "rdma.hca_count" in src
    assert "rdma.ports_active" in src


def test_pyproject_version_at_least_0574():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 74), (
        f"Version {m.group(1)} < 0.5.74"
    )
