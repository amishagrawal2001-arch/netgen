"""Diagnostic bundle collector.

Operator-requested after srv06 took 3 release-roundtrips to
sort out (v0.5.101 → v0.5.103). Each round needed me to ask
the operator for one more piece of system state — kernel
version, dpkg list, ethtool output, /proc/<pid>/limits, etc.
A single button that captures EVERYTHING relevant in one
tarball collapses that roundtrip to zero.

The bundle is intentionally noisy: cheaper to include too
much than to need a follow-up round. Per-file size cap (256
KiB) keeps the tarball small enough to attach to a chat /
issue; total cap (16 MiB) guards against runaway journalctl
or dmesg dumps.

Privacy: the bundle includes lspci output, kernel cmdline,
hostname, network iface MACs/IPs, and journal text. It does
NOT include: /etc/shadow, ssh keys, sudoers, env vars from
the running server process, or operator-uploaded pcap files.
The journal is passed through _redact_journal_secrets()
(see run_tgen_server.py) before inclusion to scrub tokens.

The bundle is built in-memory (BytesIO) and streamed to the
client; no temp files left on disk. Each `_capture_*`
function returns (name, bytes_or_str) — None on failure
(which is logged but doesn't kill the bundle).
"""
from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Callable, Iterable, Optional

LOG = logging.getLogger(__name__)

# Per-file cap: anything > 256 KiB is truncated with a
# trailing marker so operators know they're not seeing
# everything. Tail-bias the truncation for log-like content
# (most-recent-first) but head-bias for one-shot snapshot
# output (lspci, dpkg).
_PER_FILE_CAP_BYTES = 256 * 1024
_TOTAL_CAP_BYTES = 16 * 1024 * 1024
_SUBPROCESS_TIMEOUT_S = 8


def _run_capture(
    cmd: list[str],
    *,
    timeout: float = _SUBPROCESS_TIMEOUT_S,
    tail_truncate: bool = False,
) -> Optional[str]:
    """Run a command, return stdout (decoded) or None on
    failure. Stderr is appended after a separator so operators
    can see what went wrong without losing stdout."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        LOG.debug(f"[diag] {' '.join(cmd[:3])} failed: {exc}")
        return None
    out = r.stdout or ""
    if r.stderr:
        out = f"{out}\n\n----- stderr -----\n{r.stderr}"
    out = f"$ {' '.join(cmd)}\n\n{out}"
    if len(out.encode("utf-8", "replace")) > _PER_FILE_CAP_BYTES:
        # tail_truncate keeps the most recent content for
        # log-like commands; head_truncate keeps the start for
        # snapshot-like commands.
        encoded = out.encode("utf-8", "replace")
        if tail_truncate:
            kept = encoded[-(_PER_FILE_CAP_BYTES - 200):]
            marker = b"\n... [head truncated; bundle cap reached] ...\n"
            out = (marker + kept).decode("utf-8", "replace")
        else:
            kept = encoded[: _PER_FILE_CAP_BYTES - 200]
            marker = b"\n... [tail truncated; bundle cap reached] ...\n"
            out = (kept + marker).decode("utf-8", "replace")
    return out


def _read_file_capped(path: str) -> Optional[str]:
    """Read a file, capping size + tolerating missing files /
    permission errors."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(_PER_FILE_CAP_BYTES + 1)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        LOG.debug(f"[diag] read {path} failed: {exc}")
        return None
    if len(data) > _PER_FILE_CAP_BYTES:
        marker = b"\n... [truncated at %d bytes] ...\n" % _PER_FILE_CAP_BYTES
        data = data[:_PER_FILE_CAP_BYTES] + marker
    try:
        return data.decode("utf-8", "replace")
    except Exception:
        return None


def _list_netgen_ifaces() -> list[str]:
    """Iface enumeration via /sys/class/net so we don't import
    psutil here (utils/ stays lightweight). Excludes loopback,
    docker, virtual bridges. Returns empty list if /sys/class/
    net is unreadable."""
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []
    skip = {"lo"}
    skip_prefixes = ("docker", "veth", "br-", "virbr", "tun", "tap")
    return [n for n in names
            if n not in skip
            and not n.startswith(skip_prefixes)]


# ─── Individual captures ───
#
# Each returns (filename, content) or None. Filenames go into
# the tar; the prefix mirrors `dmesg | tail`-style operator
# muscle memory.


def _capture_system_info() -> list[tuple[str, str]]:
    out = []
    for name, cmd in [
        ("system/uname.txt", ["uname", "-a"]),
        ("system/lsb_release.txt", ["lsb_release", "-a"]),
        ("system/uptime.txt", ["uptime"]),
        ("system/free.txt", ["free", "-h"]),
        ("system/cmdline.txt", ["cat", "/proc/cmdline"]),
        ("system/cpuinfo.txt", ["cat", "/proc/cpuinfo"]),
        ("system/meminfo.txt", ["cat", "/proc/meminfo"]),
    ]:
        v = _run_capture(cmd)
        if v is not None:
            out.append((name, v))
    return out


def _capture_dpkg_inventory() -> list[tuple[str, str]]:
    """Just the packages we care about — dpdk, mlx, rdma-core,
    libibverbs. Full `dpkg -l` is too noisy for a bundle."""
    full = _run_capture(["dpkg", "-l"])
    if full is None:
        return []
    keep = []
    for line in full.splitlines():
        if re.search(
            r"dpdk|mlx|libibverbs|rdma-core|ibverbs|libnl|"
            r"meson|ninja|pkg-config|huge",
            line, re.IGNORECASE,
        ):
            keep.append(line)
    return [("packages/dpkg-network-related.txt", "\n".join(keep))]


def _capture_pci() -> list[tuple[str, str]]:
    out = []
    full = _run_capture(["lspci", "-vvv"], timeout=10)
    if full is not None:
        # Extract just the Ethernet / InfiniBand controllers.
        # Full lspci on a multi-NIC server is megabytes.
        keep_blocks = []
        in_block = False
        for line in full.splitlines():
            if re.match(r"^[0-9a-f]{2}:", line):
                in_block = bool(re.search(
                    r"Ethernet controller|Infiniband|Network controller",
                    line, re.IGNORECASE,
                ))
            if in_block:
                keep_blocks.append(line)
        out.append(("pci/lspci-network.txt", "\n".join(keep_blocks)))
    return out


def _capture_dpdk_state() -> list[tuple[str, str]]:
    out = []
    # tx_worker version + help
    v = _run_capture(["/usr/local/bin/tx_worker", "--help"])
    if v is not None:
        out.append(("dpdk/tx_worker-help.txt", v))
    # build date / size
    v = _run_capture(["stat", "/usr/local/bin/tx_worker"])
    if v is not None:
        out.append(("dpdk/tx_worker-stat.txt", v))
    # Hugepages
    try:
        for node_dir in sorted(Path("/sys/devices/system/node").glob("node*")):
            for hp_dir in sorted((node_dir / "hugepages").glob("hugepages-*")):
                try:
                    nr = (hp_dir / "nr_hugepages").read_text().strip()
                    free = (hp_dir / "free_hugepages").read_text().strip()
                    out.append((
                        f"dpdk/hugepages-{node_dir.name}-{hp_dir.name}.txt",
                        f"nr_hugepages={nr}\nfree_hugepages={free}\n",
                    ))
                except OSError:
                    continue
    except OSError:
        pass
    # /mnt/huge mount
    v = _run_capture(["findmnt", "-T", "/mnt/huge"])
    if v is not None:
        out.append(("dpdk/mnt-huge.txt", v))
    return out


def _capture_iface_state() -> list[tuple[str, str]]:
    out = []
    for iface in _list_netgen_ifaces():
        for label, cmd in [
            ("driver", ["ethtool", "-i", iface]),
            ("link", ["ethtool", iface]),
            ("offloads", ["ethtool", "-k", iface]),
            ("ring", ["ethtool", "-g", iface]),
            ("counters", ["ethtool", "-S", iface]),
        ]:
            v = _run_capture(cmd, timeout=4)
            if v is not None:
                out.append((f"interfaces/{iface}/{label}.txt", v))
        # sysfs driver link
        for sysfs in [
            f"/sys/class/net/{iface}/operstate",
            f"/sys/class/net/{iface}/carrier",
            f"/sys/class/net/{iface}/speed",
            f"/sys/class/net/{iface}/address",
            f"/sys/class/net/{iface}/mtu",
        ]:
            v = _read_file_capped(sysfs)
            if v is not None:
                out.append((
                    f"interfaces/{iface}/sysfs-{Path(sysfs).name}.txt",
                    v,
                ))
        # Driver name from sysfs/device/driver symlink
        try:
            drv = os.readlink(f"/sys/class/net/{iface}/device/driver")
            out.append((
                f"interfaces/{iface}/pci-driver.txt",
                f"driver={os.path.basename(drv)}\n",
            ))
        except (OSError, FileNotFoundError):
            pass
    return out


def _capture_firmware_versions() -> list[tuple[str, str]]:
    """Mellanox firmware via mlxfwmanager. Stale FW is a common
    cause of mlx5dv_dr_create_domain failures; surfacing the
    version lets us correlate against the Mellanox release
    notes."""
    out = []
    v = _run_capture(["mlxfwmanager", "--query"], timeout=15)
    if v is not None:
        out.append(("firmware/mlxfwmanager-query.txt", v))
    # ibstat / ibv_devinfo — RDMA path
    v = _run_capture(["ibstat"])
    if v is not None:
        out.append(("firmware/ibstat.txt", v))
    v = _run_capture(["ibv_devinfo", "-v"])
    if v is not None:
        out.append(("firmware/ibv_devinfo.txt", v))
    return out


def _capture_systemd_state() -> list[tuple[str, str]]:
    out = []
    # netgen-server unit + drop-ins (the rlimits drop-in is
    # the v0.5.103 fix; surfacing it confirms whether it's
    # actually in effect).
    v = _run_capture(["systemctl", "cat", "netgen-server"])
    if v is not None:
        out.append(("systemd/netgen-server-unit.txt", v))
    v = _run_capture(["systemctl", "show", "netgen-server"])
    if v is not None:
        out.append(("systemd/netgen-server-properties.txt", v))
    # MainPID's actual /proc/<pid>/limits — definitive proof
    # of whether LimitMEMLOCK=infinity took effect.
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "MainPID",
             "--value", "netgen-server.service"],
            capture_output=True, text=True, timeout=3,
        )
        pid = r.stdout.strip()
        if pid and pid.isdigit() and pid != "0":
            v = _read_file_capped(f"/proc/{pid}/limits")
            if v is not None:
                out.append((
                    f"systemd/proc-{pid}-limits.txt", v,
                ))
            v = _read_file_capped(f"/proc/{pid}/status")
            if v is not None:
                out.append((
                    f"systemd/proc-{pid}-status.txt", v,
                ))
    except (subprocess.SubprocessError, OSError):
        pass
    return out


def _capture_journal_and_dmesg(
    journal_redactor: Optional[Callable[[str], str]] = None,
) -> list[tuple[str, str]]:
    """Last hour of netgen-server journal + dmesg tail.

    journal_redactor: called on the journal output before
    inclusion. Pass run_tgen_server._redact_journal_secrets
    to scrub tokens before they hit the bundle. None = no
    redaction (used by unit tests)."""
    out = []
    v = _run_capture(
        ["journalctl", "-u", "netgen-server", "--since", "1 hour ago",
         "--no-pager", "--output=short"],
        timeout=10, tail_truncate=True,
    )
    if v is not None:
        if journal_redactor is not None:
            try:
                v = journal_redactor(v)
            except Exception as exc:
                LOG.warning(f"[diag] journal redactor failed: {exc}")
        out.append(("journal/netgen-server-last-hour.txt", v))
    v = _run_capture(["dmesg", "-T"], timeout=10, tail_truncate=True)
    if v is not None:
        # Keep only mlx5/ vfio/ DPDK-relevant lines — full
        # dmesg is too noisy.
        filtered = [
            ln for ln in v.splitlines()
            if re.search(
                r"mlx5|vfio|iommu|hugepage|dpdk|netgen|EAL:|mlx_core",
                ln, re.IGNORECASE,
            )
        ]
        out.append((
            "journal/dmesg-network-relevant.txt",
            "\n".join(filtered),
        ))
    return out


def _capture_netgen_api_snapshots(api_fetcher: Optional[Callable[[str], dict]] = None
                                  ) -> list[tuple[str, str]]:
    """The /api/admin/health, /api/dpdk/status, /api/interfaces
    JSON snapshots. Pass api_fetcher = callable(path) -> dict
    so this can be unit-tested without a live Flask app.

    In production the caller wires api_fetcher to local
    in-process calls (no localhost HTTP roundtrip)."""
    out = []
    if api_fetcher is None:
        return out
    import json
    for path in [
        "/api/admin/health",
        "/api/dpdk/status",
        "/api/interfaces",
        "/api/streams/stats",
    ]:
        try:
            d = api_fetcher(path)
        except Exception as exc:
            LOG.warning(f"[diag] api_fetcher({path}) failed: {exc}")
            continue
        safe_name = path.strip("/").replace("/", "-")
        out.append((
            f"api/{safe_name}.json",
            json.dumps(d, indent=2, default=str),
        ))
    return out


def _capture_netgen_version() -> list[tuple[str, str]]:
    out = []
    # netgen-server version from pip
    v = _run_capture(
        ["/opt/netgen-server/netgen-venv/bin/pip", "show", "ostg-trafficgen"],
        timeout=4,
    )
    if v is not None:
        out.append(("netgen/pip-show-ostg-trafficgen.txt", v))
    # netgen-upgrade script (the v0.5.103 self-update; surfacing
    # it confirms operators are running the version they think
    # they're running)
    v = _read_file_capped("/opt/netgen-server/bin/netgen-upgrade")
    if v is not None:
        # Just the first 100 lines — enough for header/comments
        # to identify version; rest is the same logic everyone
        # already has.
        head = "\n".join(v.splitlines()[:100])
        out.append(("netgen/netgen-upgrade-head.txt", head))
    return out


# ─── Top-level orchestration ───


def build_bundle(
    *,
    api_fetcher: Optional[Callable[[str], dict]] = None,
    journal_redactor: Optional[Callable[[str], str]] = None,
) -> bytes:
    """Build the diagnostic tar.gz in-memory and return as bytes.

    api_fetcher: closure that fetches a netgen API path and
        returns a dict (for in-process calls; bypasses HTTP).
        None = skip API snapshots.
    journal_redactor: closure that scrubs secrets from journal
        text before inclusion. None = no redaction.

    Returns the raw tar.gz bytes — caller wraps in a Flask
    Response with appropriate headers.
    """
    bio = io.BytesIO()
    collectors: list[Callable[[], list[tuple[str, str]]]] = [
        _capture_system_info,
        _capture_dpkg_inventory,
        _capture_pci,
        _capture_dpdk_state,
        _capture_iface_state,
        _capture_firmware_versions,
        _capture_systemd_state,
        _capture_netgen_version,
    ]
    total = 0
    with tarfile.open(fileobj=bio, mode="w:gz", compresslevel=6) as tar:
        # Manifest stub — operators can diff this between
        # bundles to see what changed.
        manifest_lines = [
            "# netgen diagnostic bundle",
            "# captured by utils.diag_bundle.build_bundle",
            f"# api_fetcher: {'yes' if api_fetcher else 'no'}",
            f"# journal_redactor: {'yes' if journal_redactor else 'no'}",
            "",
            "Sections:",
        ]
        all_entries: list[tuple[str, str]] = []
        for fn in collectors:
            try:
                entries = fn() or []
            except Exception as exc:
                LOG.warning(f"[diag] collector {fn.__name__} crashed: {exc}")
                entries = [(f"errors/{fn.__name__}.txt", f"crashed: {exc}\n")]
            all_entries.extend(entries)
        # API + journal/dmesg captured last (more I/O latency).
        try:
            all_entries.extend(
                _capture_netgen_api_snapshots(api_fetcher)
            )
        except Exception as exc:
            LOG.warning(f"[diag] api snapshot crashed: {exc}")
        try:
            all_entries.extend(
                _capture_journal_and_dmesg(journal_redactor)
            )
        except Exception as exc:
            LOG.warning(f"[diag] journal capture crashed: {exc}")

        for name, content in all_entries:
            raw = content.encode("utf-8", "replace")
            if total + len(raw) > _TOTAL_CAP_BYTES:
                manifest_lines.append(
                    f"  - {name} (SKIPPED: total bundle cap hit)"
                )
                continue
            info = tarfile.TarInfo(name=name)
            info.size = len(raw)
            info.mtime = 0  # reproducible — see v0.5.9 lesson
            tar.addfile(info, io.BytesIO(raw))
            total += len(raw)
            manifest_lines.append(f"  - {name} ({len(raw)} bytes)")

        # Manifest goes last so it sees the final entry list.
        manifest_bytes = "\n".join(manifest_lines).encode("utf-8")
        info = tarfile.TarInfo(name="MANIFEST.txt")
        info.size = len(manifest_bytes)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(manifest_bytes))

    return bio.getvalue()
