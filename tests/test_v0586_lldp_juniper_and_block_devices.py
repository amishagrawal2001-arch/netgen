"""v0.5.86 — LLDP parser handles lldpd `json0` shape + block-device
enumeration + diagnostic /api/admin/lldp_raw endpoint.

Operator on srv06 (Jun 10 2026):

  "upgraded to rel - 5.85, i am not seeing lldp neighbor on the
   admin console, however i can see on server."
  + paste of `lldpcli show neighbors` showing a Juniper qfx5130-32cd
   neighbor on ens2f1np1.

Root cause: lldpd emits `json0`-style JSON where iface name is the
DICT KEY, not a list of entries with a "name" field. The v0.5.82
parser only handled the older "array-of-interfaces" shape, so
srv06 silently showed (no LLDP) on every row.

Plus operator-requested:

  "also i want to see all the disks available on the server and
   usage."
  + paste of fdisk-style output showing /dev/sda1, sda2, sda3.

v0.5.86 ships:

  1. `_lldp_extract_id_value` — handles bare-string id OR
     {"type", "value"} typed-dict shape.

  2. `_lldp_normalise_chassis` — handles both:
       a) "chassis": [{"name": "sw1", "id": "...", ...}]
       b) "chassis": {"sw1": {"id": {"type": "mac", "value": "..."}, ...}}

  3. `_lldp_normalise_port` — same shape tolerance.

  4. `_refresh_lldp_cache` rewritten to walk both `json` (array)
     and `json0` (iface-name-as-key) shapes.

  5. New `GET /api/admin/lldp_raw` diagnostic returns the raw
     `lldpcli -f json show neighbors` stdout so any future parser
     issue can be inspected without SSH.

  6. `disk.block_devices` enumerates ALL block devices (disks +
     partitions) from `lsblk -J -b`. Skips loop/ram. Includes
     name, parent, type, size_bytes, fstype, mountpoint, model,
     serial. Capped at 64 entries.

  7. Admin HTML adds a Block Devices table under the existing
     Disks (mounted) table. Partitions indented under parent
     disk; sizes formatted GiB/TiB.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


# Pull helpers without importing the whole module (which fails on
# DeviceDatabase init in a dev tree without /opt/netgen).
def _load_helpers():
    src = _src()
    ns = {}
    for m in re.finditer(
        r"^def (_lldp_extract_id_value|_lldp_normalise_chassis|_lldp_normalise_port)\("
        r"[\s\S]+?(?=^def [a-z_]|\Z)",
        src,
        re.MULTILINE,
    ):
        exec(m.group(0), ns)
    return ns


# ─── LLDP parser unit tests ───


def test_extract_id_value_handles_bare_string():
    ns = _load_helpers()
    assert ns["_lldp_extract_id_value"]("abc123") == "abc123"


def test_extract_id_value_handles_typed_dict():
    ns = _load_helpers()
    assert ns["_lldp_extract_id_value"](
        {"type": "mac", "value": "20:ed:47:10:b4:7d"}
    ) == "20:ed:47:10:b4:7d"


def test_normalise_chassis_handles_list_shape():
    """Older lldpd: chassis is a one-element list."""
    ns = _load_helpers()
    out = ns["_lldp_normalise_chassis"]([{
        "name": "sw1",
        "id": "fc:bd:67:00:00:01",
        "descr": "Arista",
        "mgmt-ip": ["10.0.0.1"],
    }])
    assert out["sys_name"] == "sw1"
    assert out["id"] == "fc:bd:67:00:00:01"
    assert out["descr"] == "Arista"
    assert out["mgmt_ips"] == ["10.0.0.1"]


def test_normalise_chassis_handles_json0_juniper_shape():
    """Newer lldpd talking to Juniper qfx5130 — the chassis dict
    has the sys-name as the OUTER key, with id as a typed dict."""
    ns = _load_helpers()
    out = ns["_lldp_normalise_chassis"]({
        "ny-q5130-03.englab.juniper.net": {
            "id": {"type": "mac", "value": "20:ed:47:10:b4:7d"},
            "descr": "Juniper Networks qfx5130-32cd",
            "mgmt-ip": ["10.83.6.63"],
        }
    })
    assert out["sys_name"] == "ny-q5130-03.englab.juniper.net"
    assert out["id"] == "20:ed:47:10:b4:7d"
    assert "Juniper" in out["descr"]
    assert out["mgmt_ips"] == ["10.83.6.63"]


def test_normalise_port_handles_typed_id():
    ns = _load_helpers()
    out = ns["_lldp_normalise_port"]({
        "id": {"type": "local", "value": "689"},
        "descr": "et-0/0/29:0",
    })
    assert out["id"] == "689"
    assert out["descr"] == "et-0/0/29:0"


# ─── /api/admin/lldp_raw ───


def test_lldp_raw_endpoint_defined():
    src = _src()
    assert '@app.route("/api/admin/lldp_raw"' in src
    assert "def admin_lldp_raw(" in src
    # viewer-gated (matches /api/admin/journal pattern).
    m = re.search(
        r'@app\.route\("/api/admin/lldp_raw"[\s\S]{0,400}?def admin_lldp_raw',
        src,
    )
    assert m and "@require_role" in m.group(0), (
        "lldp_raw missing @require_role"
    )


def test_lldp_raw_endpoint_returns_stdout_and_returncode():
    src = _src()
    m = re.search(
        r"def admin_lldp_raw\(\)[\s\S]+?(?=\ndef [a-z_]|\n@app\.route)",
        src,
    )
    body = m.group(0)
    assert "lldpcli" in body and "json" in body
    assert '"stdout"' in body and '"returncode"' in body
    assert "timeout=" in body


# ─── Block devices ───


def test_health_includes_block_devices():
    src = _src()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    )
    body = m.group(0)
    assert '"block_devices"' in body or 'block_devices' in body
    assert "lsblk" in body, "Block devices not enumerated via lsblk"
    # The -J -b flags for JSON output in bytes.
    assert '"-J"' in body
    assert '"-b"' in body
    # Skips loop/ram pseudo-devices.
    assert 'loop' in body and 'ram' in body


def test_health_block_devices_capped_at_64():
    src = _src()
    m = re.search(
        r"def api_admin_health\(\)[\s\S]+?return\s+jsonify\(out\)",
        src,
    )
    body = m.group(0)
    assert "[:64]" in body or "64" in body, (
        "Block devices list isn't capped — could blow up payload"
    )


def test_admin_js_renders_block_devices_table():
    src = _src()
    assert "p-sys-blockdev" in src, (
        "Block devices element ID missing from admin HTML"
    )
    assert "block_devices" in src, (
        "JS doesn't consume d.disk.block_devices"
    )
    # Renders size, fstype, mountpoint, model.
    assert "size_bytes" in src
    assert "mountpoint" in src
    assert "fstype" in src


# ─── Version ───


def test_pyproject_version_at_least_0586():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 86)
