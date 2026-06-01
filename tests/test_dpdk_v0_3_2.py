"""v0.3.2 — DPDK closing-pass: tx_worker stdout reader + shell guards.

Three pins:
  * The Python `tx_worker` launcher uses `select.select()` to wait
    for stdout data with a timeout, so the worker thread can see
    `stop_event` between read attempts. Pre-v0.3.2 the blocking
    `for line in proc.stdout:` loop could park the thread
    indefinitely if DPDK EAL hung on device init.
  * The bash `dpdk_bind.sh` script carries a `validate_pci_address`
    function and BOTH bind_to_dpdk and unbind_from_dpdk call it
    before any sysfs write.
  * The bash `install_dpdk.sh` script writes `/tmp/dpdk_deps_install.log`
    under `umask 077` (subshell-scoped) AND verifies `meson.build`
    exists after the git clone so a corrupted source tree fails
    early with an actionable message.

The bash assertions are source-grep; the Python one runs an actual
shell-extract test of the `validate_pci_address` function.
"""

import re
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
TX_WORKER = REPO / "utils" / "dpdk_tx_worker.py"
BIND_SH = REPO / "resources" / "dpdk" / "dpdk_bind.sh"
INSTALL_SH = REPO / "resources" / "dpdk" / "install_dpdk.sh"


# ─────────────────────────────────── tx_worker stdout reader
def test_v0_3_2_tx_worker_imports_select():
    src = TX_WORKER.read_text()
    assert re.search(r"^import select\b", src, flags=re.MULTILINE), (
        "utils/dpdk_tx_worker.py must import `select` for the "
        "v0.3.2 stdout-poll refactor"
    )


def test_v0_3_2_tx_worker_uses_select_not_blocking_iter():
    """The blocking `for line in proc.stdout:` loop must be GONE
    and replaced with a `select.select(...)`-based poll inside a
    `while True:` so the loop can check `stop_event` and
    `proc.poll()` between reads."""
    src = TX_WORKER.read_text()
    # Old idiom must be gone from the main reader path.
    # (It may legitimately appear in comments or a docstring
    # explaining the change — the prose anchor below tolerates that.)
    pattern_old = re.search(
        r"^\s*for\s+line\s+in\s+proc\.stdout\s*:",
        src, flags=re.MULTILINE,
    )
    assert pattern_old is None, (
        "blocking `for line in proc.stdout:` iteration must be "
        "removed — v0.3.2 stdout-reader refactor regressed"
    )
    # New idiom present.
    assert "select.select(" in src, (
        "v0.3.2 expected a select.select(...) call in the stdout "
        "reader path"
    )


def test_v0_3_2_tx_worker_loop_checks_stop_event_and_poll():
    """The new reader loop must check both `stop_event.is_set()`
    and `proc.poll()` so an EAL-hung worker AND a clean child
    exit both wake the loop within the 500 ms select timeout."""
    src = TX_WORKER.read_text()
    # Find the launch_with_tracker function body (or whichever
    # function contains the new reader).
    idx_select = src.find("select.select(")
    assert idx_select != -1
    # Look at the ~1000 chars around the select call for the two
    # required state checks.
    window = src[max(0, idx_select - 1500): idx_select + 1500]
    assert "stop_event.is_set()" in window, (
        "v0.3.2 reader loop missing stop_event.is_set() check — "
        "operator-initiated stop won't wake the loop"
    )
    assert "proc.poll()" in window, (
        "v0.3.2 reader loop missing proc.poll() check — clean "
        "child exit won't wake the loop"
    )


# ─────────────────────────────────── dpdk_bind.sh PCI validator
def test_v0_3_2_bind_sh_defines_validate_pci_address():
    src = BIND_SH.read_text()
    assert re.search(
        r"^validate_pci_address\(\)\s*\{", src, flags=re.MULTILINE,
    ), "validate_pci_address function missing from dpdk_bind.sh"
    # The regex itself — anchored hex tuple separated by colons + dot.
    assert "[0-9a-fA-F]" in src, (
        "validator regex must reference hex character class"
    )


@pytest.mark.parametrize("function_name", [
    "bind_to_dpdk", "unbind_from_dpdk",
])
def test_v0_3_2_bind_sh_calls_validator(function_name):
    """Both entry points must call validate_pci_address before any
    destructive operation. Pin both so a future edit can't drop
    the gate from one path."""
    src = BIND_SH.read_text()
    body = re.search(
        rf"^{function_name}\(\)\s*\{{(.*?)^\}}",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert body is not None, f"{function_name}() not found"
    text = body.group(1)
    assert "validate_pci_address" in text, (
        f"{function_name} doesn't call validate_pci_address — "
        f"defence-in-depth gate missing from this entry point"
    )


def test_v0_3_2_bind_sh_validator_behaviour_via_shell():
    """Actually invoke the validator in bash to confirm it accepts
    real PCI strings and rejects garbage. The function is small +
    self-contained so extracting it and sourcing into a sub-shell
    is reliable."""
    # Extract just the function definition.
    src = BIND_SH.read_text()
    m = re.search(
        r"^validate_pci_address\(\)\s*\{.*?^\}",
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert m is not None
    fn_text = m.group(0)

    def runs(pci):
        """Returns the exit code from validate_pci_address $pci."""
        script = fn_text + f'\nvalidate_pci_address "{pci}"\n'
        r = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode

    # Accepted forms.
    for ok in ("0000:00:1f.6", "0000:c9:00.0",
               "abcd:01:02.7", "ffff:ff:1f.f"):
        assert runs(ok) == 0, f"validator rejected real PCI: {ok!r}"
    # Rejected forms.
    for bad in ("", "garbage", "0000:00:00",        # missing .function
                ":00:00.0",                          # empty domain
                "0000:00:1f", "0000:00:1f.6.1",     # too short / too long
                "00:00:1f.6",                        # 2-char domain
                "0000:00:gh.6",                      # non-hex
                ";rm -rf /",                         # shell-meta
                "../etc/shadow"):
        assert runs(bad) != 0, f"validator accepted garbage: {bad!r}"


# ─────────────────────────────────── install_dpdk.sh hardening
def test_v0_3_2_install_sh_tee_uses_umask_077():
    """The temp log write must run under umask 077 in a subshell
    so the file ends up 0600 instead of the default 0644."""
    src = INSTALL_SH.read_text()
    # Must have a subshell that sets umask 077 + tees the log.
    assert re.search(
        r"\(\s*umask\s+077\s*&&[^)]*tee\s+/tmp/dpdk_deps_install\.log",
        src,
    ), (
        "install_dpdk.sh tee should run in a (umask 077 && ...) "
        "subshell to keep the temp log owner-only"
    )


def test_v0_3_2_install_sh_validates_clone_artifacts():
    """After `git clone`, the script must verify the source tree
    looks like DPDK (has meson.build + lib/) so a corrupted clone
    surfaces immediately with an actionable message instead of
    failing mid-build."""
    src = INSTALL_SH.read_text()
    # Look at the clone_dpdk function body specifically so we don't
    # match unrelated meson.build mentions elsewhere.
    body = re.search(
        r"git clone https://dpdk\.org/git/dpdk.*?log_success \"DPDK cloned",
        src, flags=re.DOTALL,
    )
    assert body is not None, "clone block not found"
    text = body.group(0)
    assert "meson.build" in text, (
        "post-clone sanity check missing — corrupted clone will "
        "fail mid-build with a cryptic meson error"
    )
    assert re.search(r"\[\[\s*!\s*-f\s+meson\.build", text), (
        "expected a `[[ ! -f meson.build ]]` guard in the clone "
        "block"
    )
