"""v0.5.246 — dnsmasq dhcp-range emitter no longer duplicates lines
and always carries an explicit netmask.

Operator on srv06 2026-09-02 attached a second pool to a DHCP server
device and saw dnsmasq config emit TWO dhcp-range lines for the same
subnet:

    dhcp-range=172.16.30.20,172.16.30.250,86400s
    dhcp-range=set:pool_172_16_30_20,172.16.30.20,172.16.30.250,86400s
    dhcp-option=tag:pool_172_16_30_20,3,172.16.30.10

dnsmasq rejects two dhcp-range statements that cover the exact
same subnet as a config error (or serves last-wins and drops the
scoping). Root cause: v0.5.229's per-pool-gateway emitter appended
BOTH the untagged and tagged forms when the pool had a gateway.

Second latent issue exposed by v0.5.245 (relay-mode): when the
pool's subnet is NOT on any local interface (which is now the
common case in relay mode — we deliberately skip the anchor),
dnsmasq REQUIRES an explicit netmask in the dhcp-range line to
figure out the network. Pre-fix, dhcp-range=start,end,lease
lines omitted netmask entirely, so dnsmasq guessed or refused
to serve the range.

Fix: v0.5.246 emits ONE dhcp-range per pool (tagged if the pool
has its own gateway, untagged otherwise) and always includes an
explicit netmask derived from the pool's supernet.
"""

from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
DHCP = (REPO / "utils" / "dhcp.py").read_text()


def test_pool_netmask_helper_defined():
    """New _pool_netmask helper derives the supernet mask from
    start/end using the same walk-prefix-lengths logic as
    _ensure_ipv4_address."""
    assert 'v0.5.246 (audit U dnsmasq-range)' in DHCP
    assert "def _pool_netmask(_start: str, _end: str)" in DHCP
    idx = DHCP.find("def _pool_netmask(_start: str, _end: str)")
    body = DHCP[idx:idx + 1000]
    # Walks 32..8 like _ensure_ipv4_address does.
    assert "for _prefixlen in range(32, 7, -1):" in body
    # Returns str(net.netmask) so it lands directly in the dhcp-range.
    assert "return str(_cand.netmask)" in body


def test_additional_pool_emits_only_ONE_dhcp_range():
    """The critical fix: pre-v0.5.246 emitted both an untagged AND
    a tagged dhcp-range for the SAME subnet when the pool had a
    gateway. dnsmasq rejects duplicates. v0.5.246 emits one XOR
    the other."""
    # The old bug: both `config_lines.append(f"dhcp-range={extra_start}...`
    # AND `config_lines.append(f"dhcp-range=set:{_tag}...` firing
    # inside the same branch. The fix branches on extra_gw so at
    # most one fires per pool. Anchor on the v0.5.246 marker to
    # avoid matching the unrelated `for pool in additional_pools:`
    # in _collect_pool_networks earlier in the file.
    _marker = 'v0.5.246 (audit U dnsmasq-range): emit EITHER a tagged'
    idx = DHCP.find(_marker)
    assert idx > 0, "v0.5.246 emit-EITHER marker missing"
    end = DHCP.find("if gateway:", idx)
    body = DHCP[idx:end]
    assert "if extra_gw:" in body
    # Both else-arms exist so we always emit something.
    # In the tagged branch we emit `dhcp-range=set:{_tag}` variants.
    # In the untagged branch we emit `dhcp-range={extra_start},...`
    # variants. Neither branch emits both.
    _tagged_count = body.count("dhcp-range=set:{_tag}")
    _untagged_count = body.count("dhcp-range={extra_start},{extra_end}")
    # Each branch has 2 variants (with/without netmask) → 2 each.
    assert _tagged_count == 2, f"expected 2 tagged variants in gateway branch, got {_tagged_count}"
    assert _untagged_count == 2, f"expected 2 untagged variants in no-gateway branch, got {_untagged_count}"


def test_primary_pool_dhcp_range_carries_netmask_when_derivable():
    """Primary pool line now uses the netmask when _pool_netmask
    succeeds; falls back to the pre-fix no-netmask form only when
    the derivation returns None (never happens for well-formed
    IPv4 pools, but safe fallback)."""
    # Anchor at the _pool_netmask helper marker; the primary pool
    # emission is just below it.
    idx = DHCP.find("def _pool_netmask(_start: str, _end: str)")
    body = DHCP[idx:idx + 2500]
    # New emission path with netmask.
    assert 'f"dhcp-range={pool_start},{pool_end},{_pri_mask},{lease_seconds}s"' in body
    # Fallback path when mask derivation fails.
    assert 'f"dhcp-range={pool_start},{pool_end},{lease_seconds}s"' in body


def test_additional_pool_tagged_range_carries_netmask():
    """Tagged form for a per-pool-gateway pool also gets the mask."""
    idx = DHCP.find("if extra_gw:")
    body = DHCP[idx:idx + 1500]
    assert 'f"dhcp-range=set:{_tag},{extra_start},{extra_end},{_extra_mask},{extra_lease}s"' in body


def test_additional_pool_untagged_range_also_masked():
    """No-gateway path (pool inherits global option-3) still emits
    an untagged dhcp-range, but WITH netmask now — required for
    relay-mode pools."""
    # The untagged branch is the else of `if extra_gw:`.
    idx = DHCP.find("if extra_gw:")
    body = DHCP[idx:idx + 2500]
    # Look for the untagged-with-mask variant.
    assert 'f"dhcp-range={extra_start},{extra_end},{_extra_mask},{extra_lease}s"' in body


def test_version_bumped():
    src = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', src, re.MULTILINE)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 5, 246)
