"""v0.5.87 — LLDP parser handles HYBRID list-of-single-key-dicts +
collapsible System Info / Disks / Block Devices + UI polish.

Operator on srv06 (Jun 10 2026):

  "lldp neighbor is still not seen after upgrading to 5.86, and
   provide collapse view for System info, and Disk(mounted),
   Block Devices, also make it more professional look."

The v0.5.86 raw-dump diagnostic (`/api/admin/lldp_raw`) revealed
srv06's lldpd actually emits a THIRD shape neither v0.5.82 nor
v0.5.86 handled:

    "lldp": {
      "interface": [
        {"ens10f0": {"via": "LLDP", "chassis": {...}, "port": {...}}},
        {"ens2f0np0": {"via": "LLDP", "chassis": {...}, "port": {...}}}
      ]
    }

A LIST where each element is a single-key dict whose key is the
iface name. v0.5.86's list branch did `entry.get("name")` which
returned None on all 11 rows → silent (no LLDP) every row.

Fix: when a list element is a single-key dict and the key isn't
a known entry field (name/chassis/port/via/rid/age/ttl), treat
the key as the iface name and the value as the entry.

Plus operator-requested UI changes:

  * The three sub-sections of the System Info card (Host hardware,
    Disks mounted, Block devices) wrapped in native <details>
    elements. Host hardware open by default; Disks + Block
    Devices collapsed.

  * Each summary line carries a one-line meta hint (e.g.
    "64 cores · 252 GiB RAM · Ubuntu" / "3 mounts" / "4 disks,
    14 devices") so the operator can read state without
    expanding.

  * `.collapse` CSS — borderless rotating chevron, hover
    background, no native disclosure triangle. Reads professional.

  * Slight type/spacing tweaks across cards: h2 gets a faint
    bottom border, slate-700 text, -0.1px tracking. Cards get a
    second-layer shadow for depth.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_SERVER = _REPO / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _load_helpers():
    src = _src()
    ns = {}
    for m in re.finditer(
        r"^def (_lldp_extract_id_value|_lldp_normalise_chassis|_lldp_normalise_port)\("
        r"[\s\S]+?(?=^def [a-z_]|\Z)",
        src, re.MULTILINE,
    ):
        exec(m.group(0), ns)
    return ns


# ─── LLDP hybrid shape (srv06 actual) ───


def test_lldp_walker_handles_list_of_single_key_dicts():
    """srv06's actual shape: 'interface' is a LIST where each
    element is {iface_name: {...entry...}}. The v0.5.87 walker
    must extract iface name from the key."""
    src = _src()
    # Find the _refresh_lldp_cache function body.
    m = re.search(
        r"def _refresh_lldp_cache\(\)[\s\S]+?(?=\n\ndef [a-z_])",
        src,
    )
    body = m.group(0)
    # The list-of-single-key-dicts heuristic.
    assert "len(entry) == 1" in body, (
        "List branch doesn't detect single-key-dict elements"
    )
    assert "next(iter(entry.keys()))" in body
    assert "_entry_keys" in body, (
        "Walker doesn't compare against known entry fields"
    )


def test_lldp_parser_extracts_juniper_neighbor_from_real_srv06_shape():
    """Round-trip the actual JSON shape we dumped from srv06."""
    ns = _load_helpers()
    SRV06 = {
        "lldp": {
            "interface": [
                {"ens2f0np0": {
                    "via": "LLDP",
                    "chassis": {"ny-q5130-03.englab.juniper.net": {
                        "id": {"type": "mac", "value": "20:ed:47:10:b4:7d"},
                        "descr": "Juniper Networks qfx5130",
                        "mgmt-ip": "10.83.6.63",
                    }},
                    "port": {
                        "id": {"type": "local", "value": "690"},
                        "descr": "et-0/0/29:1",
                    },
                }},
            ]
        }
    }
    # Mirror what _refresh_lldp_cache's walker does.
    pairs = []
    outer = SRV06["lldp"]
    ifaces = outer.get("interface", outer)
    _entry_keys = {"name", "chassis", "port", "via",
                   "rid", "age", "ttl"}
    if isinstance(ifaces, list):
        for entry in ifaces:
            if (isinstance(entry, dict) and len(entry) == 1
                    and next(iter(entry.keys())) not in _entry_keys
                    and isinstance(next(iter(entry.values())), dict)):
                _n, _e = next(iter(entry.items()))
                pairs.append((_n, _e))
    assert len(pairs) == 1
    name, entry = pairs[0]
    assert name == "ens2f0np0"
    chassis = ns["_lldp_normalise_chassis"](entry["chassis"])
    port = ns["_lldp_normalise_port"](entry["port"])
    assert chassis["sys_name"] == "ny-q5130-03.englab.juniper.net"
    assert chassis["id"] == "20:ed:47:10:b4:7d"
    assert chassis["mgmt_ips"] == ["10.83.6.63"]
    assert port["id"] == "690"
    assert port["descr"] == "et-0/0/29:1"


# ─── Collapsibles ───


def test_admin_html_uses_details_collapse_for_three_sections():
    src = _src()
    # Three <details class="collapse"> blocks inside System Info card.
    sys_idx = src.find('id="card-system-info"')
    end_idx = src.find('</div>', sys_idx + 5000)
    block = src[sys_idx:end_idx + 6]
    assert block.count('<details class="collapse"') >= 3, (
        "Not all three System Info sub-sections are collapsible"
    )
    # Host hardware open by default.
    assert '<details class="collapse" open>' in block
    # Each has a summary with a meta hint.
    assert 'p-sys-summary' in block
    assert 'p-sys-disks-meta' in block
    assert 'p-sys-blockdev-meta' in block


def test_collapse_css_styles_defined():
    src = _src()
    # Custom chevron + hover + open-rotated styles.
    assert 'details.collapse' in src
    assert '::-webkit-details-marker { display: none' in src, (
        "Native disclosure triangle still showing — looks unpolished"
    )
    # Rotating chevron.
    assert "rotate(90deg)" in src
    assert "::before" in src


def test_admin_js_populates_summary_meta():
    src = _src()
    # CPU + RAM + distro fragments in the host-hardware summary.
    assert "p-sys-summary" in src
    assert "cores_logical" in src
    assert "GiB RAM" in src
    # Mount + device counts.
    assert "p-sys-disks-meta" in src
    assert "mount${" in src or "mount`" in src or "mounts" in src
    assert "p-sys-blockdev-meta" in src
    assert "disk${" in src or "device${" in src


# ─── Professional polish ───


def test_card_h2_has_subtle_divider():
    src = _src()
    css_m = re.search(
        r"\.card h2 \{[^}]+\}",
        src,
    )
    assert css_m
    css = css_m.group(0)
    assert "border-bottom" in css, (
        "Card h2 no longer has the subtle bottom border (polish lost)"
    )


def test_pyproject_version_at_least_0587():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 87)
