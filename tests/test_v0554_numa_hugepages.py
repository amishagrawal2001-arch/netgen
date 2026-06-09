"""v0.5.54 — /api/dpdk/hugepages allocates NUMA-aware.

Audit finding H5. Pre-fix the endpoint always wrote to the
global path:

  /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

On dual-socket hosts (typical lab DPDK boxes), the kernel
opportunistically allocates whichever NUMA node has contiguous
memory available — often NOT the NIC's NUMA node. DPDK then
fails to allocate mbufs on `--socket-mem` against the NIC's
node with:

    EAL: Cannot allocate memory on socket 1
    EAL: Failed to initialize memory pool

even though `/proc/meminfo` says HugePages_Total is the
requested number. Operator wastes hours wondering why
allocation that "succeeded" doesn't work.

v0.5.54 fix:

  1. Detect online NUMA nodes from
     `/sys/devices/system/node/online` (e.g., "0-1" → [0, 1]).
  2. If >1 node, split requested count evenly across nodes
     and write per-node:
       /sys/devices/system/node/nodeN/hugepages/hugepages-2048kB/nr_hugepages
     Read back to detect kernel short-allocation.
  3. Single-node hosts keep the global-path behavior.
  4. Response includes `numa_split` (per-node actual counts)
     and `numa_nodes` (list of detected nodes) so the operator
     can see how the kernel placed pages.
"""
from __future__ import annotations

import re
from pathlib import Path


_SERVER = Path(__file__).resolve().parents[1] / "run_tgen_server.py"


def _hugepages_body() -> str:
    src = _SERVER.read_text()
    m = re.search(
        r"def dpdk_hugepages\(\)[\s\S]+?(?=\n@app\.route|\ndef [a-z_])",
        src,
    )
    assert m, "dpdk_hugepages() handler not located"
    return m.group(0)


def test_hugepages_reads_numa_online_nodes():
    """Must read `/sys/devices/system/node/online` to detect
    topology. Hardcoding 1 node would silently break multi-
    socket hosts."""
    body = _hugepages_body()
    assert "/sys/devices/system/node/online" in body, (
        "hugepages handler doesn't read NUMA online nodes — "
        "still single-node-blind"
    )


def test_hugepages_parses_range_format():
    """`/sys/devices/system/node/online` returns ranges like
    `0-1` or comma-separated `0,2,3`. The fix must handle
    BOTH — many hosts show `0-1` on dual-socket boxes."""
    body = _hugepages_body()
    # We split on commas first, then check for `-` ranges.
    assert ".split(" in body or "split(" in body, (
        "No split-style parsing of NUMA range string"
    )
    assert '"-"' in body or "'-'" in body, (
        "No dash-range expansion of online NUMA string — would "
        "break `0-1` form"
    )


def test_hugepages_writes_per_node_paths_when_multi_node():
    """When more than one node is online, the fix must write to
    per-node paths, not the global path."""
    body = _hugepages_body()
    assert "/sys/devices/system/node/node" in body, (
        "hugepages handler doesn't reference per-node sysfs "
        "paths — multi-node hosts still get single-write."
    )


def test_hugepages_distributes_evenly():
    """num_pages must be split across nodes as evenly as
    possible. `divmod` is the natural primitive — split into
    base + remainder."""
    body = _hugepages_body()
    assert "divmod" in body, (
        "No divmod-style even split — uneven node allocation "
        "(or all on node 0) would defeat the NUMA fix."
    )


def test_hugepages_reads_back_to_detect_short_alloc():
    """The kernel can short-allocate (return fewer pages than
    requested) under memory fragmentation. The fix must
    read-back the per-node value after writing so the response
    reflects actual allocation, not just the request."""
    body = _hugepages_body()
    # Pattern: open(node_path).read after the write — or any
    # read-back inside the multi-node branch.
    assert re.search(
        r"open\(node_path\)|open\([^,)]*node_path",
        body,
    ), (
        "Per-node write isn't followed by a read-back — response "
        "would lie about actual allocation."
    )


def test_hugepages_falls_back_to_global_on_single_node():
    """Single-socket hosts have only node0 — writing to per-node
    paths there would be pointless overhead. Fall back to the
    global path. Also fall back if a per-node path is missing
    (some kernels in containers don't expose them)."""
    body = _hugepages_body()
    # The else / fallback branch writes to the global hugepage_file.
    assert re.search(
        r"with open\(hugepage_file,\s*[\"']w[\"']\)",
        body,
    ), (
        "Global hugepage_file write path is gone — single-node "
        "hosts would have nothing to write to."
    )
    # And falling back to the global path is gated on numa_split
    # being empty (multi-node branch didn't run or failed).
    assert "if not numa_split" in body, (
        "Global path is run unconditionally — would double-write "
        "or skip when both branches mismanaged."
    )


def test_response_includes_numa_split_and_numa_nodes():
    """Response must include `numa_split` (per-node actual
    counts dict) and `numa_nodes` (list) so the admin chip
    can show the distribution."""
    body = _hugepages_body()
    # Locate the success-jsonify call.
    success = re.search(
        r"jsonify\(\{[\s\S]+?[\"']success[\"']:\s*True[\s\S]+?\}\)",
        body,
    )
    assert success, "success jsonify response not located"
    success_block = success.group(0)
    assert '"numa_split"' in success_block, (
        "Response missing `numa_split` field"
    )
    assert '"numa_nodes"' in success_block, (
        "Response missing `numa_nodes` field"
    )


def test_pyproject_version_at_least_0554():
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    parts = [int(x) for x in m.group(1).split(".")]
    assert (parts[0], parts[1], parts[2]) >= (0, 5, 54), (
        f"Version {m.group(1)} < 0.5.54"
    )
