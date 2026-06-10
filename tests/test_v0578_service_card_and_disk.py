"""v0.5.78 — service status card, disk-space, connection-lost
banner, driver allowlist, PCI class tightening, hugepages JS
bounds.

Continues the audit drive. Covers:
  - HIGH #4: netgen-server service status card
  - MEDIUM #6: connection-lost banner (sticky if >90s)
  - MEDIUM #9: disk-space visibility for /tmp, /var/lib/netgen,
    /opt/netgen-server with warn <1 GB / critical <100 MB
  - HIGH #3: driver-name allowlist regex `^[A-Za-z0-9_-]{1,32}$`
    before label-string interpolation in JS
  - LOW endpoint #8: tighten iface-table PCI class filter from
    `startswith("02")` to `_cls[:4] in ("0200", "0207")` (drops
    rare class 0280 "other network controller")
  - LOW #15: hugepages JS bounds — typed values like 100000 used
    to silently pass to server
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _health_body() -> str:
    src = _src()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    )
    return m.group(0)


# ─── Service status (HIGH #4) ───


def test_health_includes_service_block():
    body = _health_body()
    for k in ("active", "since_iso", "pid", "uptime_sec", "mem_mb"):
        assert f'"{k}"' in body, f"service block missing {k}"
    assert 'out["service"] = service' in body


def test_health_calls_systemctl_show():
    body = _health_body()
    assert "systemctl" in body and "ActiveState" in body, (
        "service status doesn't call systemctl show with ActiveState"
    )


def test_health_reads_uptime_from_proc_stat():
    """Uptime should be computed from /proc/<pid>/stat starttime,
    not from `systemctl status` text scraping (which is brittle
    across distros)."""
    body = _health_body()
    assert "/proc/uptime" in body
    assert "/proc/{service['pid']}/stat" in body or "/stat" in body


def test_admin_html_includes_server_card():
    src = _src()
    assert "<h2>Server</h2>" in src, "Admin HTML missing Server card heading"
    for elem_id in ("p-svc-state", "p-svc-pid", "p-svc-uptime", "p-disk-tmp"):
        assert f'id="{elem_id}"' in src, f"Server card missing #{elem_id}"


def test_admin_js_renders_service_card():
    src = _src()
    assert "d.service" in src, "JS doesn't consume d.service"
    assert "svc.active" in src
    assert "svc.uptime_sec" in src


# ─── Disk space (MEDIUM #9) ───


def test_health_includes_disk_block():
    body = _health_body()
    assert '"disk"' in body or 'out["disk"] = disk' in body
    # All three watched paths.
    assert "/tmp" in body
    assert "/var/lib/netgen" in body
    assert "/opt/netgen-server" in body
    # Uses shutil.disk_usage.
    assert "disk_usage" in body


def test_health_disk_warns_at_low_threshold():
    body = _health_body()
    assert "1024" in body, "No 1 GB warn threshold for disk space"
    assert "Disk low" in body or "Disk critical" in body


def test_admin_js_renders_disk_tmp_with_color_thresholds():
    src = _src()
    assert "d.disk" in src or "diskTmp" in src or "_diskTmp" in src
    # The render branches on the free_mb thresholds.
    assert "free_mb" in src


# ─── Connection-lost banner (MEDIUM #6) ───


def test_admin_js_connection_lost_banner():
    src = _src()
    assert "banner-conn-lost" in src
    assert "_lastHealthOkMs" in src, (
        "No timestamp variable for last successful health"
    )
    # 90s threshold (3× the 30s poll).
    assert "> 90" in src or ">90" in src, (
        "No 90s staleness threshold"
    )


def test_admin_js_stamps_last_health_ok_on_success():
    src = _src()
    # The success path of refreshHealth must update the stamp.
    idx = src.find("async function refreshHealth(")
    body = src[idx:idx + 3000]
    assert "_lastHealthOkMs = Date.now()" in body, (
        "refreshHealth doesn't stamp _lastHealthOkMs on success"
    )


# ─── Driver-name allowlist (HIGH #3) ───


def test_js_driver_name_allowlist_regex():
    src = _src()
    assert "_VALID_DRIVER_RE" in src, (
        "No driver-name allowlist regex"
    )
    # The regex restricts to safe characters.
    assert re.search(
        r"_VALID_DRIVER_RE\s*=\s*/\^\[[A-Za-z0-9_\-]+\]\{1,32\}\$/",
        src,
    ), "Allowlist regex doesn't restrict to [A-Za-z0-9_-]{1,32}"


# ─── PCI class filter tightening (LOW endpoint #8) ───


def test_iface_filter_whitelists_0200_and_0207():
    src = _src()
    # Find the iface append site context.
    idx = src.find('"card_model": _detect_nic_model(pci)')
    pre = src[max(0, idx - 3500):idx]
    # The new tighter check.
    assert re.search(
        r'_cls\[:4\]\s+in\s+\("0200",\s*"0207"\)',
        pre,
    ), (
        "iface filter not tightened to {0200, 0207} — class 0280 "
        "still leaks through"
    )


# ─── Hugepages JS bounds (LOW #15) ───


def test_hugepages_js_enforces_upper_bound():
    src = _src()
    # The bounds check must include n > 65536 rejection.
    assert "n > 65536" in src, (
        "hugepages JS doesn't reject typed values > 65536"
    )
    # Friendly message.
    assert "Pages must be 64..65536" in src


# ─── Version ───


def test_pyproject_version_at_least_0578():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 78)
