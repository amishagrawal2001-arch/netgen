"""v0.5.286 — Fix broken v0.5.282 (ARP-J1) local-table install
syntax: combining `table local` and `vrf <name>` caused iproute2
to reject with 'either table or vrf, not both', silently."""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()


def test_arp_j6_marker_present():
    assert "v0.5.286 (ARP-J6)" in DHCP


def test_install_command_no_longer_combines_table_and_vrf():
    """The pre-fix install had BOTH `table local` AND `vrf <name>`
    in the same `ip route add` — iproute2 rejects that as
    mutually exclusive. Post-fix install has neither."""
    idx = DHCP.find("v0.5.286 (ARP-J6)")
    end = DHCP.find("except Exception as _local_exc", idx)
    body = DHCP[idx:end]

    # Find the install `_run_command` call.
    install_start = body.find('"ip", "route", "add"')
    assert install_start > 0, "install command missing"
    install_end = body.find("timeout=5", install_start)
    install_block = body[install_start:install_end]

    # These conflict — must NOT be in the install command.
    assert '"table", "local"' not in install_block, (
        "install must not use `table local` (conflicts with `vrf`)"
    )
    assert '"vrf"' not in install_block, (
        "install must not use `vrf <name>` — kernel routes to "
        "correct table via `dev` parameter alone"
    )


def test_install_command_uses_local_type_and_dev():
    """Correct form: `ip route add local <ip>/32 dev <iface>
    proto kernel scope host src <ip>`. Kernel picks the local
    table automatically because the route type is `local`."""
    idx = DHCP.find("v0.5.286 (ARP-J6)")
    body = DHCP[idx:idx + 3000]
    assert '"local", f"{server_ip}/32"' in body
    assert '"dev", interface' in body
    assert '"proto", "kernel"' in body
    assert '"scope", "host"' in body
    assert '"src", server_ip' in body


def test_probe_uses_valid_iproute2_syntax():
    """Pre-fix probe used `local/<ip>` as a route selector which
    iproute2 doesn't understand — always returned empty. Post-
    fix probe uses the standard `ip route show table local` and
    greps for the needle in the output."""
    idx = DHCP.find("v0.5.286 (ARP-J6)")
    body = DHCP[idx:idx + 3000]
    # Standard probe form.
    assert '"ip", "route", "show", "table", "local"' in body
    # Grep pattern for the entry.
    assert 'f"local {server_ip} dev {interface} "' in body
    # Broken pattern is gone.
    assert 'f"local/{server_ip}"' not in body


def test_install_logs_outcome():
    """v0.5.282 didn't check the install command's return code
    at all — that's why the broken syntax was invisible. Post-
    fix logs success, EEXIST (kernel already installed it), and
    failure with stderr."""
    idx = DHCP.find("v0.5.286 (ARP-J6)")
    body = DHCP[idx:idx + 3500]
    # Success path logs.
    assert "table-local: added local" in body
    # EEXIST (kernel already fired) treated as debug, not failure.
    assert "File exists" in body
    assert "already present (probe missed it)" in body
    # Real failure logged at warning with stderr + rc.
    assert "table-local install failed" in body
    assert "_add.returncode" in body


def test_probe_needle_avoids_prefix_collision():
    """`local 172.16.30.2 dev vlan100 ` must not match a needle
    for vlan10 — the trailing space after iface enforces word
    boundary."""
    idx = DHCP.find("v0.5.286 (ARP-J6)")
    body = DHCP[idx:idx + 3000]
    # Comment must call this out so a future author doesn't
    # strip the trailing space. The source has a line-break
    # between "space" and "after", so check the two halves.
    assert "the space" in body
    assert "after iface avoids" in body


# --- Metadata ----------------------------------------------------


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (
        int(m.group(1)), int(m.group(2)), int(m.group(3))
    ) >= (0, 5, 286)
