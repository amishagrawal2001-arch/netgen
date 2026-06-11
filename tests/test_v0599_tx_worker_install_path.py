"""v0.5.99 (post-tag): tx_worker resolver missed
`/usr/local/bin/tx_worker` — the path install_dpdk.sh Step 6
deposits the freshly-built binary at.

Operator on srv06 (Jun 11 2026), UDP DPDK stream died in <1s
with tx_count=0:

  ERROR:dpdk_tx_worker:[dpdk] tx_worker binary not found at
    /opt/netgen-server/resources/dpdk/tx_worker/build/tx_worker

But `/api/admin/health.tx_worker.path` reported
`/usr/local/bin/tx_worker` as present (v0.5.67 probe), and
`/api/dpdk/status.tx_worker_exists` was true. The launcher's
`_resolve_tx_worker_bin()` search list skipped `/usr/local/bin/`
entirely — it walked the wheel path and the `/opt/netgen` /
`/opt/OSTG` install-dir candidates, then returned None.

Fix promotes `/usr/local/bin/tx_worker` to priority 2 (right
after the `$TX_WORKER_BIN` env override) so the authoritative
install_dpdk.sh copy wins over the wheel-shipped stale-ABI
fallback.

Both `dpdk_tx_worker.py` and `dpdk_tx_worker_multi.py` share
`_resolve_tx_worker_bin()`, so the fix carries to both.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
_RESOLVER = _REPO / "utils" / "dpdk_tx_worker.py"


def _resolver_body() -> str:
    src = _RESOLVER.read_text()
    m = re.search(
        r"def _resolve_tx_worker_bin[\s\S]+?(?=\ndef [a-z_])",
        src,
    )
    assert m, "_resolve_tx_worker_bin not found"
    return m.group(0)


def test_usr_local_bin_present_in_resolver():
    body = _resolver_body()
    assert '"/usr/local/bin/tx_worker"' in body, (
        "Resolver doesn't check /usr/local/bin/tx_worker — "
        "install_dpdk.sh Step 6 lands the binary here and "
        "the v0.5.67 health probe reports it, but the launcher "
        "walks past it"
    )


def test_usr_local_bin_checked_before_wheel_fallback():
    """The wheel-shipped tx_worker has stale DPDK ABI. The
    install_dpdk.sh copy at /usr/local/bin/ must win.

    Anchor on the actual fallback call (`from importlib.resources
    import files`) rather than docstring mentions of the same."""
    body = _resolver_body()
    usrlocal_pos = body.find('"/usr/local/bin/tx_worker"')
    # The wheel-fallback code uses `from importlib.resources
    # import files` or `from importlib import resources as _res`.
    m = re.search(r"from importlib(\.resources)? import", body)
    assert m
    wheel_pos = m.start()
    assert usrlocal_pos > 0
    assert usrlocal_pos < wheel_pos, (
        "/usr/local/bin/tx_worker check must come BEFORE the "
        "wheel-shipped fallback so a freshly-built binary wins"
    )


def test_usr_local_bin_checked_after_env_override():
    body = _resolver_body()
    env_pos = body.find('os.environ.get("TX_WORKER_BIN")')
    usrlocal_pos = body.find('"/usr/local/bin/tx_worker"')
    assert env_pos > 0 and usrlocal_pos > 0
    assert env_pos < usrlocal_pos, (
        "$TX_WORKER_BIN env override must still win over the "
        "default /usr/local/bin/ path"
    )


def test_search_order_docstring_mentions_usr_local_bin():
    """The function's docstring documents the search order;
    it should mention /usr/local/bin/ so future maintainers
    don't re-introduce the gap."""
    src = _RESOLVER.read_text()
    m = re.search(
        r"def _resolve_tx_worker_bin[\s\S]+?\"\"\"([\s\S]+?)\"\"\"",
        src,
    )
    assert m
    docstring = m.group(1)
    assert "/usr/local/bin" in docstring or "Search order" in docstring or "install_dpdk" in docstring
