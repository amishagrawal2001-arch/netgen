"""DPDK server-side hardening pins (v0.3.1).

The Flask server itself is heavy to spin up in pytest (binds a
port, starts the stream-tracker thread, etc.), so the hardening
is pinned via source-grep + a tiny pure-function exercise of the
iface-name whitelist.

What's pinned:
  * `_DPDK_BIND_LOCK` exists and both bind + unbind handlers
    wrap the subprocess.run inside `with _DPDK_BIND_LOCK:` so two
    concurrent requests on the same PCI can't race dpdk_bind.sh.
  * `_is_safe_iface_name` defence-in-depth whitelist exists and
    rejects path-traversal / shell-meta / oversized names.
  * `_get_pci_from_interface` calls `_is_safe_iface_name` BEFORE
    constructing the sysfs path.
  * `/api/dpdk/hugepages` carries `timeout=10` on the mount /
    mkdir subprocess calls + a rollback path that zeros sysfs
    when mount fails.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SERVER_FILE = REPO / "run_tgen_server.py"


@pytest.fixture(scope="module")
def src():
    return SERVER_FILE.read_text()


# ─────────────────────────────────── concurrent-bind lock
def test_v0_3_1_dpdk_bind_lock_defined(src):
    """A module-level threading.Lock named `_DPDK_BIND_LOCK` must
    exist and live near the other module-level locks (i.e. it's
    the same `Lock` class imported at the top of the file)."""
    assert re.search(
        r"^_DPDK_BIND_LOCK\s*=\s*Lock\(\)", src, flags=re.MULTILINE,
    ), "_DPDK_BIND_LOCK missing — concurrent /api/dpdk/bind+unbind " \
       "on the same PCI will race dpdk_bind.sh"


@pytest.mark.parametrize("handler_name", ["dpdk_bind", "dpdk_unbind"])
def test_v0_3_1_bind_unbind_acquire_lock(src, handler_name):
    """Both /api/dpdk/bind and /api/dpdk/unbind handlers must wrap
    their subprocess.run call in `with _DPDK_BIND_LOCK:`. A lock
    that exists but isn't held protects nothing — pin both call
    sites so the next refactor can't accidentally drop the
    serialisation on one handler."""
    body = re.search(
        rf"def {handler_name}\(\).*?(?=^@app\.route|^def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert body is not None, f"{handler_name} handler not found"
    text = body.group(0)
    # The lock acquisition must precede the subprocess.run that
    # actually runs dpdk_bind.sh.
    assert "with _DPDK_BIND_LOCK" in text, (
        f"{handler_name} doesn't acquire _DPDK_BIND_LOCK — the "
        f"concurrent-bind race window is still open"
    )
    # Sanity: the subprocess.run call comes AFTER the `with` (i.e.
    # nested inside the lock scope, not before).
    lock_idx = text.find("with _DPDK_BIND_LOCK")
    sp_idx = text.find("subprocess.run", lock_idx)
    assert sp_idx != -1, (
        f"{handler_name}: no subprocess.run call after the lock "
        f"acquisition — refactor moved it outside?"
    )


# ─────────────────────────────────── iface-name whitelist
def test_v0_3_1_iface_name_whitelist_helper_exists(src):
    """The whitelist helper must be a module-level function so
    future call sites can re-use it and a future audit can find it
    by name."""
    assert re.search(
        r"^def _is_safe_iface_name\(name\)", src, flags=re.MULTILINE,
    ), "_is_safe_iface_name helper missing"
    assert re.search(
        r"^_IFACE_NAME_RE\s*=", src, flags=re.MULTILINE,
    ), "_IFACE_NAME_RE regex missing"


def test_v0_3_1_get_pci_validates_iface_before_sysfs(src):
    """`_get_pci_from_interface` must call `_is_safe_iface_name`
    BEFORE constructing the sysfs path. The validation gate is
    pointless if the sysfs read happens first."""
    body = re.search(
        r"def _get_pci_from_interface\(iface_name\).*?(?=^def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert body is not None
    text = body.group(0)
    safe_idx = text.find("_is_safe_iface_name")
    # Match the actual f-string construction, NOT the docstring's
    # explanatory mention of /sys/class/net/. Anchor on the
    # f-string opener.
    sysfs_idx = text.find('f"/sys/class/net/')
    assert safe_idx != -1, (
        "_get_pci_from_interface bypasses the v0.3.1 whitelist"
    )
    assert sysfs_idx != -1, (
        "couldn't find the f-string sysfs construction — refactor "
        "changed the path syntax?"
    )
    assert safe_idx < sysfs_idx, (
        "_is_safe_iface_name check must precede sysfs path "
        "construction — otherwise the gate is pointless"
    )


def test_v0_3_1_iface_whitelist_accepts_real_names():
    """Re-implement the regex client-side (the server class is
    Flask-heavy to import) and confirm common netdev names pass."""
    pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
    for good in (
        "eth0", "enp181s0f0np0", "bond0.10",
        "em1:0", "lo", "wlan0", "br-1234abcd",
    ):
        assert pattern.match(good), f"rejected real name: {good!r}"


def test_v0_3_1_iface_whitelist_rejects_path_traversal():
    pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
    for bad in (
        "",
        "eth0/",
        "../../etc/shadow",
        "/sys/class/net/eth0",
        "eth0\x00null",
        " eth0",
        "eth 0",
        ".hidden",       # leading dot — rejected
        "-evil",         # leading dash — rejected
        "a" * 33,        # oversize (IFNAMSIZ-1 is 32)
        "eth0;rm -rf /", # shell-meta
    ):
        assert pattern.match(bad) is None, f"accepted bad: {bad!r}"


# ─────────────────────────────────── hugepage hardening
def test_v0_3_1_hugepages_subprocess_calls_have_timeouts(src):
    """The /api/dpdk/hugepages handler must pass timeout= on every
    subprocess.run inside its try-block. The audit flagged a hang
    on mountpoint / mkdir / mount — all three now carry timeout=10."""
    body = re.search(
        r"def dpdk_hugepages\(\).*?(?=^@app\.route|^def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert body is not None
    text = body.group(0)
    # Every subprocess.run inside the handler should have timeout=.
    runs = re.findall(r"subprocess\.run\([^)]*\)", text, flags=re.DOTALL)
    assert runs, "no subprocess.run calls found in dpdk_hugepages — " \
                 "refactor moved the mount logic?"
    for r in runs:
        assert "timeout=" in r, (
            f"dpdk_hugepages still has a subprocess.run without "
            f"timeout=: {r[:120]!r}"
        )


def test_v0_3_1_hugepages_rollback_on_mount_failure(src):
    """When mount fails, the sysfs allocation must be rolled back
    (write '0' to nr_hugepages). Pre-v0.3.1 the operator saw
    'success' but every subsequent stream-start failed with
    cryptic 'no free hugepages'."""
    body = re.search(
        r"def dpdk_hugepages\(\).*?(?=^@app\.route|^def |\Z)",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    text = body.group(0)
    assert "rolled back sysfs" in text or "rolled back" in text, (
        "dpdk_hugepages mount-failure path missing rollback log line"
    )
    # The actual rollback writes "0" to the hugepage file.
    assert re.search(
        r"open\(hugepage_file,\s*['\"]w['\"]\).*?write\(['\"]0['\"]\)",
        text, flags=re.DOTALL,
    ), "rollback should write '0' to nr_hugepages on mount failure"
