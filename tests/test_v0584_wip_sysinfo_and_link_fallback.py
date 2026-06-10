"""WIP (post-0.5.83): admin dashboard system-info card + Mellanox
link-speed ethtool fallback.

Operator (Jun 10 2026):

  "pls include additional information about the server in admin
   dashboard, like total memory/free, total cpu/cores, total disk
   and space/free per disk... etc , i also see some interface are
   not showing speed and some are ... do not generate new release
   till asked."

Tracked as WIP — no version bump, no CHANGELOG, no tag.

Two changes:

  1. `_get_link_info` now reads `operstate` + tolerates per-file
     OSError (Mellanox carrier sysfs EINVALs on operstate=unknown)
     + falls back to `ethtool <iface>` for any field sysfs left
     None. New `_ethtool_link_fallback` helper with 30s TTL cache.

  2. `/api/admin/health` adds `system` block (cpu model, cores
     physical+logical, load avg; memory total/free/available/
     used/buffers/cached/swap; kernel, distro, arch, host
     uptime) and `disk.mounts` (enumerated mounts from
     /proc/mounts with total/free/used% per row, real FS only).
     Admin HTML adds a "System Info" card rendering all of it.
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


# ─── Link fallback ───


def test_ethtool_fallback_helper_defined():
    src = _src()
    assert "def _ethtool_link_fallback(" in src, (
        "_ethtool_link_fallback helper missing"
    )


def test_ethtool_fallback_caches_results():
    src = _src()
    m = re.search(
        r"def _ethtool_link_fallback\([\s\S]+?(?=\n\n\ndef [a-z_]|\n\n\n# )",
        src,
    )
    body = m.group(0)
    assert "_ETHTOOL_CACHE" in body
    assert "_ETHTOOL_CACHE_TTL" in body
    assert "timeout=" in body, "No subprocess timeout"


def test_get_link_info_uses_ethtool_fallback():
    src = _src()
    m = re.search(
        r"def _get_link_info\([\s\S]+?(?=\n\n\ndef [a-z_]|\n\n_)",
        src,
    )
    body = m.group(0)
    assert "_ethtool_link_fallback" in body, (
        "_get_link_info doesn't call the ethtool fallback"
    )
    # Reads operstate.
    assert '"operstate"' in body
    # Per-file OSError catch (the v0.5.84 fix that lets sysfs
    # speed still come through when carrier EINVALs).
    assert "OSError" in body, (
        "_get_link_info uses bare Exception — won't surface "
        "specific Mellanox EINVALs separately"
    )


def test_link_info_returns_operstate_field():
    src = _src()
    m = re.search(
        r"def _get_link_info\([\s\S]+?(?=\n\n\ndef [a-z_]|\n\n_)",
        src,
    )
    body = m.group(0)
    assert '"operstate":' in body, (
        "_get_link_info doesn't surface operstate in its return"
    )


def test_admin_js_renders_link_up_when_carrier_null_speed_present():
    """The fix: if Mellanox sysfs left carrier=null but ethtool
    fallback gave us a speed, JS still shows '↑ X Gb/s'."""
    src = _src()
    # The condition for the "link up" branch is now:
    #   link.carrier === true || (link.carrier == null && _hasSpeed)
    assert re.search(
        r"link\.carrier\s*===\s*true\s*\|\|\s*\(link\.carrier\s*==\s*null\s*&&\s*_hasSpeed\)",
        src,
    ), "Link cell still treats carrier=null+speed-present as not-up"


def test_admin_js_handles_operstate_unknown_branch():
    src = _src()
    assert "link.operstate === 'unknown'" in src
    assert "link state unknown" in src or "operstate=unknown" in src


# ─── System info in /api/admin/health ───


def test_health_includes_system_block():
    body = _health_body()
    assert 'out["system"] = system' in body or 'out["system"]' in body
    for k in ("kernel", "distro", "arch", "host_uptime_sec", "cpu", "memory"):
        assert f'"{k}"' in body, f"system block missing {k}"


def test_health_cpu_reads_proc_cpuinfo():
    body = _health_body()
    assert "/proc/cpuinfo" in body
    assert "model name" in body
    assert "physical id" in body and "cpu cores" in body, (
        "Physical core count not derived from cpuinfo"
    )
    assert "processor" in body, (
        "Logical core count not derived from cpuinfo"
    )
    assert "getloadavg" in body


def test_health_memory_reads_proc_meminfo():
    body = _health_body()
    assert "/proc/meminfo" in body
    for k in ("MemTotal", "MemFree", "MemAvailable", "SwapTotal"):
        assert k in body, f"meminfo {k} not parsed"


def test_health_disk_enumerates_mounts():
    body = _health_body()
    assert "/proc/mounts" in body
    assert 'disk["mounts"]' in body or '"mounts"' in body
    # Skips pseudo-FS.
    assert "tmpfs" in body and "_skip_types" in body
    # Caps to a sane upper bound.
    assert "[:16]" in body or "len(mounts) > 16" in body


# ─── Admin HTML ───


def test_admin_html_includes_system_info_card():
    src = _src()
    assert 'id="card-system-info"' in src
    for el in (
        "p-sys-distro", "p-sys-kernel", "p-sys-arch",
        "p-sys-uptime",
        "p-sys-cpu-model", "p-sys-cores", "p-sys-load",
        "p-sys-mem-total", "p-sys-mem-used", "p-sys-mem-free",
        "p-sys-swap", "p-sys-disks",
    ):
        assert f'id="{el}"' in src, (
            f"System Info card missing element #{el}"
        )


def test_admin_js_renders_system_info():
    src = _src()
    assert "d.system" in src or "const sys = d.system" in src
    # CPU model + cores + load.
    assert "cpu.cores_physical" in src
    assert "cpu.cores_logical" in src
    assert "cpu.load_avg" in src
    # Memory.
    assert "mem.total_mb" in src
    assert "mem.available_mb" in src or "mem.free_mb" in src
    # Disks table.
    assert "d.disk" in src and "mounts" in src
    assert "used_pct" in src
