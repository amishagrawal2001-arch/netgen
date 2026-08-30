"""v0.5.225 — DHCP server defaults move to 172.16.30.0/24.

Operator ask 2026-08-30: DHCP-server template should use its own
private range instead of the same 192.168.x.x space regular devices
default to. Moving the DHCP defaults to 172.16.30.0/24 keeps a
DHCP-server device isolated from regular BGP/OSPF devices (which
default to 192.168.0.0/24) so the "which /24 is the DHCP subnet"
question is unambiguous in the lab.

These tests pin the literal defaults in the two places that carry
them (widget constructor and mode-changed reset handler) plus the
one-click template. If somebody flips one and forgets the other,
this catches the drift. The dhcp.py historical comments that
reference the OLD 192.168.30.10-200 example are v0.5.222-era
context and are not re-checked here.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIALOG_SRC = (REPO / "widgets" / "add_device_dialog.py").read_text()
TEMPLATE_SRC = (REPO / "utils" / "device_templates.py").read_text()


# --- Widget defaults --------------------------------------------------------

def test_dhcp_pool_start_default_is_172():
    assert '"172.16.30.10"' in DIALOG_SRC
    assert '"192.168.30.10"' not in DIALOG_SRC


def test_dhcp_pool_end_default_is_172():
    assert '"172.16.30.200"' in DIALOG_SRC
    assert '"192.168.30.200"' not in DIALOG_SRC


def test_dhcp_gateway_route_default_is_172():
    assert '"172.16.30.0/24"' in DIALOG_SRC
    assert '"192.168.30.0/24"' not in DIALOG_SRC


def test_dhcp_gateway_ip_default_is_172():
    """The mode-changed handler pre-fills ipv4_gateway to the .1 of
    the pool subnet when the operator switches to Server mode."""
    assert '"172.16.30.1"' in DIALOG_SRC
    assert '"192.168.30.1"' not in DIALOG_SRC


def test_dhcp_defaults_appear_in_both_sites():
    """Both the constructor default and the mode-changed reset must
    agree — a mismatch here was the v0.5.217-era class of bug where
    the constructor and the reset used different literals and only
    one got updated during a rename."""
    # 172.16.30.10 must appear at least twice: once in the constructor
    # QLineEdit initialiser, once in the _on_dhcp_mode_changed reset.
    assert DIALOG_SRC.count('"172.16.30.10"') >= 2
    assert DIALOG_SRC.count('"172.16.30.200"') >= 2
    assert DIALOG_SRC.count('"172.16.30.0/24"') >= 2


# --- One-click template -----------------------------------------------------

def test_template_title_advertises_new_subnet():
    assert 'DHCP server (pool 172.16.30.10-200)' in TEMPLATE_SRC


def test_template_pool_fields_are_172():
    assert '"dhcp_pool_start_input": "172.16.30.10"' in TEMPLATE_SRC
    assert '"dhcp_pool_end_input": "172.16.30.200"' in TEMPLATE_SRC
    assert '"dhcp_gateway_route_input": "172.16.30.0/24"' in TEMPLATE_SRC


def test_template_interface_ip_pinned_to_pool_subnet():
    """v0.5.222 root cause: DHCP-server device with pool 192.168.30.x
    but interface inherited the widget default 192.168.0.2/24 →
    dnsmasq refused to serve. The template now pins ipv4_input to
    172.16.30.1/24 so the interface + pool land on the same /24.
    Without this, the same v0.5.222 failure returns on the new
    subnet."""
    assert '"ipv4_input": "172.16.30.1"' in TEMPLATE_SRC
    assert '"ipv4_mask_input": "24"' in TEMPLATE_SRC
    assert '"ipv4_gateway_input": "172.16.30.1"' in TEMPLATE_SRC


def test_template_no_stale_192_references_in_fields():
    """Ensure the template body doesn't leave any 192.168.30.x
    literal behind (the summary text is checked separately)."""
    # Extract just the fields dict of the dhcp_server template.
    idx = TEMPLATE_SRC.find('key="dhcp_server"')
    assert idx != -1
    end = TEMPLATE_SRC.find('    _Template(', idx + 1)
    body = TEMPLATE_SRC[idx:end if end != -1 else idx + 4000]
    assert '"192.168.30' not in body


def test_summary_mentions_isolation_rationale():
    """The template summary should explain WHY 172.16 was chosen
    (not just what the numbers are) — the operator needs to see
    "isolated from regular devices" so they understand which
    subnet to expect."""
    idx = TEMPLATE_SRC.find('key="dhcp_server"')
    end = TEMPLATE_SRC.find('    _Template(', idx + 1)
    body = TEMPLATE_SRC[idx:end if end != -1 else idx + 4000]
    assert '172.16' in body
    assert 'isolated' in body.lower() or '192.168' in body  # explains contrast


# --- Version bump -----------------------------------------------------------

def test_pyproject_version_at_or_beyond_225():
    """This test verifies the ship-time bump happened. Once we move on
    to 0.5.226+, the exact-version pin becomes stale — check that we
    didn't accidentally regress BELOW 0.5.225 instead."""
    src = (REPO / "pyproject.toml").read_text()
    import re
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m, f"could not find version in pyproject.toml"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert (major, minor, patch) >= (0, 5, 225), (
        f"pyproject version {major}.{minor}.{patch} is below 0.5.225 "
        "— the v0.5.225 DHCP-template ship must have regressed."
    )
