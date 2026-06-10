"""v0.5.83 — Help → Install Guide updated for v0.5.82 LLDP additions.

Operator (Jun 10 2026):

  "update the netgen server installation guide under help menu"

Three updates to widgets/stream_dialog.py's `_INSTALL_GUIDE_HTML`:

  1. The "What the v0.5.13 tarball install drops on disk" table
     gets a new row for `/etc/lldpd.d/netgen-server.conf` + the
     lldpd package, marked as v0.5.82.

  2. The "What the v0.5.x install does NOT touch" section's
     "No apt packages" claim is qualified — v0.5.82 adds a
     single non-fatal `apt install -y lldpd` call.

  3. A new "LLDP neighbor discovery" subsection explains what
     lldpd does for the operator, what the admin console renders,
     and how existing servers can pick it up (Make DPDK Ready
     or manual apt install).

  4. The "Expected end-of-log on success" snippet gains LLDP
     install lines + bumps the verify-OK version to 0.5.82.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_DIALOG = _REPO / "widgets" / "stream_dialog.py"


def _src() -> str:
    return _DIALOG.read_text()


def _guide_html() -> str:
    """Extract the _INSTALL_GUIDE_HTML literal."""
    src = _src()
    m = re.search(
        r'_INSTALL_GUIDE_HTML\s*=\s*r"""(.*?)"""',
        src,
        re.DOTALL,
    )
    assert m, "Couldn't find _INSTALL_GUIDE_HTML"
    return m.group(1)


def test_install_guide_disk_table_has_lldpd_row():
    guide = _guide_html()
    assert "/etc/lldpd.d/netgen-server.conf" in guide, (
        "Disk-footprint table missing the lldpd row"
    )
    assert "v0.5.82" in guide, (
        "lldpd row doesn't note v0.5.82"
    )


def test_install_guide_qualifies_no_apt_claim():
    guide = _guide_html()
    # The "No apt packages" bullet must call out the lldpd exception.
    no_apt_idx = guide.find("No apt packages installed")
    assert no_apt_idx > 0, (
        "Couldn't find the no-apt bullet in install guide"
    )
    # The exception note appears nearby.
    nearby = guide[no_apt_idx:no_apt_idx + 800]
    assert "Exception" in nearby and "lldpd" in nearby, (
        "no-apt bullet doesn't mention the v0.5.82 lldpd "
        "exception — install guide misleading"
    )


def test_install_guide_has_lldp_subsection():
    guide = _guide_html()
    assert "LLDP neighbor discovery" in guide, (
        "No LLDP neighbor discovery section in install guide"
    )
    # Mentions key concepts.
    assert "lldpd" in guide
    assert "/etc/lldpd.d/netgen-server.conf" in guide
    assert "lldpcli" in guide
    # And the upgrade-path note for existing servers.
    assert "Make DPDK Ready" in guide or "wheel upgrade" in guide


def test_install_guide_success_log_includes_lldp():
    guide = _guide_html()
    # The expected-end-of-log <pre> snippet has the LLDP lines.
    assert "[LLDP]" in guide, (
        "Success log snippet missing [LLDP] step output"
    )
    assert "lldpd is active" in guide or "Installing lldpd" in guide


def test_pyproject_version_at_least_0583():
    pyproject = (_REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 83)
