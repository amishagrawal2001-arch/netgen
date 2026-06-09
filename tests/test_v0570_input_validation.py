"""v0.5.70 — input validation hardening on /api/admin/bind_history
POST, /api/admin/interface_ip POST, and /api/dpdk/hugepages.

Audit findings H2 + H3 + M7.

H2: bind_history POST took whatever JSON the client sent and
wrote it to the persistent registry. No PCI BDF validation, no
length cap on name/kernel_driver, no size cap on the registry,
no lock around the read-modify-write window.

H3: interface_ip address validated only against a shell-metas
denylist that was missing CR + LF + tabs. `0.0.0.0\\r\\nGET /`
slipped through and injected a line into the request log via
the `cmd` echo in the error response. Now parse via
ipaddress.ip_interface so garbage is rejected up front.

M7: hugepages num_pages had no upper bound. `999_999_999` was
accepted; the kernel mostly clamped but huge ints surprise the
str(per_node) divmod path. Cap at 65536 (32 GiB / 64 TiB).
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _src() -> str:
    return _SERVER.read_text()


def _bind_history_body() -> str:
    src = _src()
    m = re.search(
        r"def api_admin_bind_history\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    return m.group(0)


def _interface_ip_body() -> str:
    src = _src()
    m = re.search(
        r"def api_admin_interface_ip\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    return m.group(0)


def _hugepages_body() -> str:
    src = _src()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m
    return m.group(0)


# ──────────────── H2: bind_history POST ────────────────


def test_bind_history_validates_pci_bdf():
    """POST must reject non-BDF `pci` values. Pre-fix
    `pci: "../../etc/passwd"` became a registry key."""
    body = _bind_history_body()
    # Same BDF regex used at /api/dpdk/bind+unbind (v0.5.60).
    assert re.search(
        r"\[0-9a-f\]\{4\}:\[0-9a-f\]\{2\}:\[0-9a-f\]\{2\}\\\.\[0-7\]",
        body,
    ), (
        "bind_history POST doesn't validate the PCI BDF regex"
    )


def test_bind_history_caps_field_lengths():
    """`name` and `kernel_driver` must be length-bounded."""
    body = _bind_history_body()
    assert re.search(
        r"len\(_name\)\s*>\s*\d+\s+or\s+len\(_kdrv\)\s*>\s*\d+",
        body,
    ), (
        "bind_history POST doesn't cap name/kernel_driver length"
    )


def test_bind_history_caps_registry_size():
    """Registry size capped so a runaway client can't bloat the
    JSON file."""
    body = _bind_history_body()
    assert re.search(
        r"len\(history\)\s*>=\s*\d+",
        body,
    ), (
        "bind_history POST doesn't cap the total registry size"
    )


def test_bind_history_holds_lock_across_rmw():
    """The whole read-modify-write window must be inside `with
    _BIND_REGISTRY_LOCK:` — v0.5.58 fixed the helpers but the
    public POST handler did the cycle outside the lock."""
    body = _bind_history_body()
    # The pattern: `with _BIND_REGISTRY_LOCK:` followed by a json.load
    # AND an os.replace within the same block.
    assert re.search(
        r"with\s+_BIND_REGISTRY_LOCK:\s*\n[\s\S]+?json\.load[\s\S]+?os\.replace",
        body,
    ), (
        "bind_history POST RMW cycle not held inside lock — "
        "concurrent POSTs still race"
    )


# ──────────────── H3: interface_ip ────────────────


def test_interface_ip_parses_address_via_ipaddress():
    """address must go through ipaddress.ip_interface() so
    garbage is rejected up front."""
    body = _interface_ip_body()
    assert "ipaddress" in body, (
        "interface_ip doesn't import ipaddress"
    )
    assert "ip_interface" in body, (
        "interface_ip doesn't call ipaddress.ip_interface()"
    )


def test_interface_ip_denylist_includes_crlf_and_tab():
    """The shell-metas denylist must include CR, LF, and TAB —
    pre-fix `\\r\\n` slipped through and log-injected."""
    body = _interface_ip_body()
    assert re.search(
        r'"\\\\r"',
        body,
    ) or '"\\r"' in body, (
        "interface_ip denylist missing carriage return"
    )
    assert '"\\n"' in body or '"\\\\n"' in body, (
        "interface_ip denylist missing newline"
    )
    assert '"\\t"' in body or '"\\\\t"' in body, (
        "interface_ip denylist missing tab"
    )


def test_interface_ip_caps_address_length():
    """45 chars is the IPv6 textual upper limit. Pre-fix a 10k-char
    address went all the way to the `ip` subprocess."""
    body = _interface_ip_body()
    assert re.search(
        r"len\(address\)\s*>\s*\d+",
        body,
    ), (
        "interface_ip doesn't cap address length"
    )


# ──────────────── M7: hugepages num_pages cap ────────────────


def test_hugepages_caps_num_pages():
    body = _hugepages_body()
    assert re.search(
        r"num_pages\s*>\s*\d{4,6}",
        body,
    ), (
        "hugepages doesn't enforce an upper bound on num_pages"
    )


def test_hugepages_cap_message_mentions_max():
    body = _hugepages_body()
    assert re.search(
        r"exceeds\s+maximum",
        body,
    ), (
        "hugepages upper-bound error message doesn't mention the "
        "maximum — operator has to guess"
    )


def test_pyproject_version_at_least_0570():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 70), (
        f"Version {m.group(1)} < 0.5.70"
    )
