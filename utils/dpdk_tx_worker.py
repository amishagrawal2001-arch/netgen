# utils/dpdk_tx_worker.py
# DPDK tx_worker launcher + stream sync for OSTG.
from __future__ import annotations

import os
import re
import select
import shlex
import signal
import subprocess
import logging
from typing import Optional, Dict, Any

LOG = logging.getLogger("dpdk_tx_worker")


def resolve_dpdk_ld_library_path(current_ld_path: str = "") -> str:
    """Discover DPDK library directories and prepend them to
    ``current_ld_path`` (":"-joined), returning the merged value.

    v0.5.253 (audit DPDK-5): extracted from ``run_stream``'s body so
    ``dpdk_tx_worker_multi.py`` can call it too — pre-fix the multi-
    instance launcher inherited only ``os.environ.copy()`` and died
    with ``error while loading shared libraries: librte_ethdev.so.XX``
    on any host where DPDK libs weren't on the default search path
    (e.g. srv06 after ``install_dpdk.sh``).

    Returns "" if nothing was found AND ``current_ld_path`` was empty
    — caller decides whether to LOG.warning or silently proceed.
    """
    dpdk_lib_paths: list = []

    # Method 1: pkg-config
    try:
        pkg_config_paths = [
            "/usr/local/lib/x86_64-linux-gnu/pkgconfig",
            "/usr/local/lib/pkgconfig",
            "/usr/lib/x86_64-linux-gnu/pkgconfig",
            "/usr/lib/pkgconfig",
        ]
        for pc_path in pkg_config_paths:
            if os.path.exists(pc_path):
                env_pc = os.environ.copy()
                env_pc["PKG_CONFIG_PATH"] = f"{pc_path}:{env_pc.get('PKG_CONFIG_PATH', '')}"
                result = subprocess.run(
                    ["pkg-config", "--variable=libdir", "libdpdk"],
                    env=env_pc, capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0:
                    libdir = result.stdout.strip()
                    if libdir and os.path.exists(libdir) and libdir not in dpdk_lib_paths:
                        dpdk_lib_paths.append(libdir)
                        LOG.debug("[dpdk] Found DPDK libdir via pkg-config: %s", libdir)
                    break
    except Exception as e:
        LOG.debug("[dpdk] pkg-config method failed: %s", e)

    # Method 2: common locations
    common_paths = [
        "/usr/local/lib/x86_64-linux-gnu",
        "/usr/local/lib",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib",
    ]
    for lib_path in common_paths:
        if os.path.exists(lib_path):
            try:
                files = os.listdir(lib_path)
                if any(f.startswith("librte_") and f.endswith(".so") or ".so." in f for f in files):
                    if lib_path not in dpdk_lib_paths:
                        dpdk_lib_paths.append(lib_path)
                        LOG.debug("[dpdk] Found DPDK libraries in: %s", lib_path)
            except Exception:
                pass

    # Method 3: ldconfig
    try:
        result = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "librte_ethdev.so" in line:
                    parts = line.split("=>")
                    if len(parts) == 2:
                        lib_file = parts[1].strip()
                        lib_dir = os.path.dirname(lib_file)
                        if lib_dir and os.path.exists(lib_dir) and lib_dir not in dpdk_lib_paths:
                            dpdk_lib_paths.append(lib_dir)
                            LOG.debug("[dpdk] Found DPDK library via ldconfig: %s", lib_dir)
                            break
    except Exception as e:
        LOG.debug("[dpdk] ldconfig method failed: %s", e)

    merged = current_ld_path or ""
    for lib_path in dpdk_lib_paths:
        if lib_path not in merged:
            merged = f"{lib_path}:{merged}" if merged else lib_path
    return merged


# ------------------------- Public API -------------------------

def _resolve_stream_name(stream_data, interface, stream_id):
    ps = (stream_data.get("protocol_selection") or {}) or {}
    for k in ("name", "stream_name", "display_name", "title"):
        v = stream_data.get(k)
        if v:
            return str(v)
    for k in ("name", "stream_name"):
        v = ps.get(k)
        if v:
            return str(v)
    port = stream_data.get("port") or interface
    l4 = (stream_data.get("L4") or ps.get("L4") or "Any")
    sid = (str(stream_id) or "")[:8]
    return f"{port} / {l4} [{sid}]"


def should_use_dpdk(stream_data: Dict[str, Any]) -> bool:
    """
    Decide whether this stream should be driven by the DPDK backend.
    Convention: engine == 'dpdk' OR any of: dpdk_enable, dpdk, use_dpdk (truthy / 'true'/'1'/etc).
    Supports flags under protocol_selection as well.

    NB: this only checks the *opt-in* flags. For the full decision including
    whether the stream is *compatible* with the tx_worker, use
    ``resolve_engine()`` below — that's what the launcher should be calling.
    """
    if not isinstance(stream_data, dict):
        return False

    ps = stream_data.get("protocol_selection", {}) or {}
    engine = str(stream_data.get("engine") or ps.get("engine") or "").strip().lower()
    if engine == "dpdk":
        return True

    for key in ("dpdk_enable", "dpdk", "use_dpdk"):
        v = stream_data.get(key)
        if v is None:
            v = ps.get(key)
        if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
            return True
        if bool(v):
            return True
    return False


def dpdk_compatibility_check(stream_data: Dict[str, Any]) -> Optional[str]:
    """Return None if the stream can run through the tx_worker C binary,
    or a short human-readable reason string explaining why it can't.

    The tx_worker today supports:
      * L3: IPv4 only (no IPv6)
      * L4: UDP only (no TCP / ICMP / IGMP / …)
      * Single 802.1Q tag OR untagged (no 802.1ad QinQ)
      * No MPLS / SR-MPLS label stack (only kernel-emitted MPLS works)

    Anything outside that envelope silently falls back to Scapy inside the
    launcher — which means the operator sees DPDK enabled in the dialog
    but the wire-side runs at Scapy speed and they wonder why. We surface
    the reason here so the start response can hand it back to the GUI.

    Pure function: takes the stream dict as the server stores it, returns
    None or a string. No I/O.
    """
    if not isinstance(stream_data, dict):
        return None
    ps = stream_data.get("protocol_selection") or {}

    def _get(key, default=None):
        v = stream_data.get(key)
        if v in (None, ""):
            v = ps.get(key, default)
        return v

    # ── L4: UDP only ────────────────────────────────────────────────
    l4 = str(_get("L4") or _get("l4") or "").strip().upper()
    # Some streams encode the L4 inside protocol_selection['L4'] as
    # "UDP" / "TCP" / "ICMP" / "Any". "Any" is treated as UDP for
    # compatibility (the tx_worker default), so it's OK.
    if l4 and l4 not in ("UDP", "ANY", ""):
        return f"DPDK tx_worker is UDP-only; this stream uses {l4}"

    # ── L3: IPv4 only ───────────────────────────────────────────────
    l3 = str(_get("L3") or _get("l3") or "IPv4").strip().upper()
    if l3 in ("IPV6", "IP6"):
        return "DPDK tx_worker is IPv4-only; IPv6 streams fall back to Scapy"

    # ── MPLS / SR-MPLS not supported (kernel-side MPLS works, but
    #    tx_worker can't build MPLS headers itself) ──────────────────
    mpls_labels = _get("mpls_labels") or stream_data.get("mpls_labels")
    if mpls_labels:
        # Accept both "16001,16002" and [16001, 16002]; both are truthy
        # only when at least one label is set.
        if isinstance(mpls_labels, str):
            non_empty = [s for s in mpls_labels.replace(";", ",").split(",")
                         if s.strip()]
            if non_empty:
                return ("DPDK tx_worker doesn't build MPLS headers; "
                        "SR-MPLS streams fall back to Scapy")
        elif isinstance(mpls_labels, (list, tuple)) and len(mpls_labels) > 0:
            return ("DPDK tx_worker doesn't build MPLS headers; "
                    "SR-MPLS streams fall back to Scapy")

    # Legacy single MPLS label field — same constraint.
    single_label = _get("mpls_label") or stream_data.get("mpls_label")
    if single_label not in (None, "", 0, "0"):
        return ("DPDK tx_worker doesn't build MPLS headers; "
                "MPLS streams fall back to Scapy")

    # ── QinQ (802.1ad outer tag) not supported ─────────────────────
    outer_vlan = _get("outer_vlan_id") or stream_data.get("outer_vlan_id")
    if outer_vlan not in (None, "", 0, "0"):
        try:
            if int(outer_vlan) > 0:
                return ("DPDK tx_worker builds single-VLAN frames only; "
                        "QinQ (802.1ad) streams fall back to Scapy")
        except (TypeError, ValueError):
            pass

    return None


def resolve_actual_tx_cores(stream_data: Dict[str, Any],
                            interface: str) -> "tuple[int, bool]":
    """Pure-function pre-flight tx_cores resolver — returns
    ``(value, was_auto_picked)``.

    Mirrors the logic inside ``run_stream``: take the operator's
    explicit ``dpdk_tx_cores`` if set; otherwise, when "Line Rate" is
    requested (pps == 0), derive a value from the link speed so a
    single-queue worker doesn't bottleneck a 400G NIC.

    Lives next to ``resolve_engine`` so the start endpoint can include
    the chosen value in its response — operators no longer have to
    grep journalctl to find out whether their ``TX Cores: 1`` setting
    was overridden by the auto-pick. v0.2.77.
    """
    explicit = _user_specified_tx_cores(stream_data)
    tx_cores = _resolve_tx_cores(stream_data)
    frame_size = 64
    try:
        ps = stream_data.get("protocol_selection") or {}
        fs = (ps.get("frame_size")
              or stream_data.get("frame_size") or 64)
        frame_size = int(fs)
    except (TypeError, ValueError):
        frame_size = 64
    # v0.5.253 (audit DPDK-2): plumb iface + frame_size into
    # _resolve_target_pps so Load-% math uses the real link speed.
    pps = _resolve_target_pps(stream_data, iface=interface, frame_size=frame_size)
    if pps == 0 and not explicit and interface:
        auto = _auto_tx_cores_for_line_rate(interface, frame_size)
        if auto > tx_cores:
            return auto, True
    return tx_cores, False


def resolve_engine(stream_data: Dict[str, Any]) -> "tuple[str, Optional[str]]":
    """Synchronous pre-flight engine decision.

    Returns ``(engine, fallback_reason)``:
      * ``("dpdk", None)``  — user opted in AND the stream is compatible
      * ``("scapy", None)`` — user didn't opt in (the common case)
      * ``("scapy", reason)`` — user opted in but the stream is incompatible

    Use this in both the REST start handler (to put the decision into the
    response) and the worker thread (so they pick the same engine). The
    ``reason`` is operator-friendly English suitable for the GUI to show
    in a toast / column tooltip.
    """
    if not should_use_dpdk(stream_data):
        return "scapy", None
    reason = dpdk_compatibility_check(stream_data)
    if reason is None:
        return "dpdk", None
    return "scapy", reason


def run_stream(
    stream_data: Dict[str, Any],
    interface: str,
    stop_event,
    tracker,
    *,
    dpdk_corelist: Optional[str] = None,
    tx_worker_bin: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> int:
    """
    Launch the DPDK tx_worker for this stream and keep StreamTracker in sync.
    Returns the tx_worker exit code (0 on success).

    Required tracker methods:
      - update_tx_by_id(interface, stream_id)
      - get_tx_count_by_id(interface, stream_id)
      - find_stream_by_id(interface, stream_id)     [not required here but commonly present]
      - remove_stream_by_id(interface, stream_id)   [not required here but commonly present]

    stop_event: threading.Event-like with .is_set()
    """
    # Ensure a truly unique stream_id (never derive from name)
    sid = stream_data.get("stream_id")
    if not sid or not str(sid).strip():
        sid = _uuid()
        # Persist back so the rest of the pipeline sees the same id
        try:
            stream_data["stream_id"] = sid
        except Exception:
            pass
    stream_id = str(sid)

    stream_name = _resolve_stream_name(stream_data, interface, stream_id)
    LOG.info("DPDK backend starting for stream '%s' (id=%s) on %s", stream_name, stream_id, interface)

    fields = _resolve_l2_l3_l4(stream_data)
    missing = [k for k in ("src_mac", "dst_mac", "src_ip", "dst_ip") if not fields.get(k)]
    if missing:
        LOG.error("[dpdk] missing required fields: %s", missing)
        return 2

    # Pick device & NUMA
    bdf = _iface_to_bdf(interface)
    numa = _bdf_numa_node(bdf) if bdf else 0

    # Resolve target rate first — needed below to decide whether to
    # auto-bump tx_cores for Line Rate streams.
    # v0.5.253 (audit DPDK-2): pass iface + frame_size so Load-% math
    # scales off the real link speed, not the historical 1 GbE baseline.
    vlan_id = fields["vlan_id"]
    frame_size = int(fields["frame_size"] or 64)
    pps = _resolve_target_pps(stream_data, iface=interface, frame_size=frame_size)

    # Multi-queue scaling: tx_cores TX queues = tx_cores worker threads.
    # EAL needs (1 main + tx_cores workers) lcores total.
    explicit_tx_cores = _user_specified_tx_cores(stream_data)
    tx_cores = _resolve_tx_cores(stream_data)
    if pps == 0 and not explicit_tx_cores:
        # User picked "Line Rate" without overriding tx_cores. Derive a
        # value from the interface speed so a single-queue worker doesn't
        # cap the rate well below the link's actual capacity (e.g. 49 Gbps
        # on a 400G link). Same calibrated math as /api/dpdk/recommend.
        auto = _auto_tx_cores_for_line_rate(interface, frame_size)
        if auto > tx_cores:
            LOG.info(
                "[dpdk] Line Rate auto-picked tx_cores=%d for %s (frame=%dB, "
                "link-derived); set 'TX Cores (queues)' explicitly to override.",
                auto, interface, frame_size,
            )
            tx_cores = auto
    needed_lcores = 1 + tx_cores
    corelist = (dpdk_corelist
                or str(stream_data.get("dpdk_corelist") or "")
                or _pick_corelist_on_node(numa, count=needed_lcores))

    # Resolve tx_worker binary (env → packaged → relative fallbacks)
    bin_path = tx_worker_bin or _resolve_tx_worker_bin()
    if not bin_path or not os.path.exists(bin_path):
        LOG.error("[dpdk] tx_worker binary not found at %s", bin_path)
        return 3
    try:
        os.chmod(bin_path, 0o755)
    except Exception:
        pass
    no_udp_csum = bool(stream_data.get("no_udp_csum", False))
    mem_channels = str(stream_data.get("dpdk_mem_channels") or os.environ.get("DPDK_MEM_CHANNELS") or "4")

    # duration
    duration_seconds = _resolve_duration_seconds(stream_data)

    # Warn if not UDP (worker is UDP-only)
    l4 = (stream_data.get("L4") or stream_data.get("protocol_selection", {}).get("L4") or "").strip().upper()
    if l4 and l4 != "UDP":
        LOG.warning("[dpdk] tx_worker is UDP-only (requested L4=%s) — proceeding with UDP.", l4)

    # Unique EAL file-prefix to avoid collisions across concurrent workers
    file_prefix = _file_prefix(stream_id, interface)
    LOG.debug("[dpdk] using file-prefix: %s", file_prefix)

    # Detect if this is a Broadcom BNXT NIC and apply workarounds for ULP initialization issues
    is_broadcom = False
    if bdf:
        # Check vendor ID to detect Broadcom NICs (vendor ID 14e4)
        try:
            vendor_file = f"/sys/bus/pci/devices/{bdf}/vendor"
            if os.path.exists(vendor_file):
                with open(vendor_file, "r") as f:
                    vendor_id = f.read().strip()
                    if vendor_id == "0x14e4":  # Broadcom
                        is_broadcom = True
                        LOG.info("[dpdk] Broadcom BNXT NIC detected (PCI: %s), applying ULP workarounds", bdf)
                        
                        # Workaround 1: Try to reset the device before DPDK initialization
                        # This can help clear any stale state that causes ULP allocation failures
                        try:
                            reset_file = f"/sys/bus/pci/devices/{bdf}/reset"
                            if os.path.exists(reset_file):
                                LOG.debug("[dpdk] Attempting PCI device reset for Broadcom NIC")
                                with open(reset_file, "w") as rf:
                                    rf.write("1")
                                import time
                                time.sleep(0.5)  # Brief pause after reset
                                LOG.debug("[dpdk] PCI device reset completed")
                        except Exception as e:
                            LOG.debug("[dpdk] PCI reset not available or failed: %s", e)
                        
                        # Workaround 2: Some BNXT PMDs support device parameters via -a option
                        # Format: -a <BDF>,param1=value1,param2=value2
                        # However, we'll try without parameters first and let the error handler suggest alternatives
        except Exception as e:
            LOG.debug("[dpdk] Vendor detection failed: %s", e)
    
    # Build command
    cmd = [bin_path, "-l", str(corelist), "-n", mem_channels, "--file-prefix", file_prefix]
    
    if bdf:
        # On mlx5 you should *not* bind to vfio; passing -a <BDF> with kernel driver is fine.
        cmd += ["-a", bdf]
    cmd += ["--",
            "--src-mac", fields["src_mac"], "--dst-mac", fields["dst_mac"],
            "--src-ip", str(fields["src_ip"]), "--dst-ip", str(fields["dst_ip"]),
            "--src-port", str(fields["udp_sport"]), "--dst-port", str(fields["udp_dport"]),
            "--size", str(frame_size), "--pps", str(pps),
            "--stream-id", stream_id]
    if vlan_id is not None:
        cmd += ["--vlan", str(vlan_id)]
    if no_udp_csum:
        cmd += ["--no-udp-csum"]
    if duration_seconds is not None:
        cmd += ["--duration", str(duration_seconds)]
    if "burst" in stream_data:
        try:
            b = int(stream_data["burst"])
            if b > 0:
                cmd += ["--burst", str(b)]
        except Exception:
            pass
    if tx_cores > 1:
        cmd += ["--tx-cores", str(tx_cores)]

    # Per-flow field randomization. Forwards the four "count" knobs so
    # tx_worker can bump src_ip / dst_ip / src_port / dst_port across N
    # packets — required for hashing/RSS tests where the DUT distributes
    # flows by 5-tuple. count=0 or 1 means "fixed" (the default).
    cmd += _resolve_count_flags(stream_data)

    # One-way latency timestamping. When enabled, tx_worker embeds a
    # 16-byte NLAT header at the start of each UDP payload — the
    # latency_sampler.py RX-side tool decodes it and reports min/avg/
    # p50/p99/max one-way latency. Requires payload_len >= 16, so the
    # frame must be big enough (60B frame leaves 18B payload — fits).
    if stream_data.get("enable_timestamps") or stream_data.get("latency_enabled"):
        cmd += ["--enable-timestamps"]

    # Environment (allow caller to inject EAL/PMD paths etc.)
    child_env = os.environ.copy()
    if env:
        child_env.update(env)

    # Helpful defaults if user didn't set them
    child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")   # avoids missing shared objs on some hosts

    # v0.5.253 (audit DPDK-5): LD_LIBRARY_PATH discovery hoisted out
    # of the launcher body into `resolve_dpdk_ld_library_path()` so
    # the multi-instance launcher (dpdk_tx_worker_multi.py) can call
    # the same code. Pre-fix, multi-instance skipped this entirely
    # and died with "error while loading shared libraries:
    # librte_ethdev.so.XX" on any host where DPDK libs weren't on
    # the default search path (e.g. srv06 after install_dpdk.sh).
    current_ld_path = child_env.get("LD_LIBRARY_PATH", "")
    merged = resolve_dpdk_ld_library_path(current_ld_path)
    if merged:
        child_env["LD_LIBRARY_PATH"] = merged
        LOG.info("[dpdk] Setting LD_LIBRARY_PATH=%s", merged)
    else:
        LOG.warning("[dpdk] Could not find DPDK library directory. LD_LIBRARY_PATH not set.")

    # Pre-launch orphan sweep. If a previous tx_worker for this same
    # stream_id is still alive (Start clicked twice without a successful
    # Stop, server restart between Start and Stop, etc.), it would keep
    # blasting alongside the new one. The new launcher only tracks its
    # own Popen - the orphan is invisible. Result: switch sees 2x line
    # rate, tracker only follows half, the per-stream rate display is
    # 50% of reality. Catch it here before we Popen.
    try:
        # v0.5.119: anchor the match on `tx_worker` in the binary
        # name. Pre-fix the pattern was just "--stream-id <id>",
        # which also matched the rx_worker because both binaries
        # share that argv. Result: every dpdk-rx start would
        # cleanly die ~165ms after launch because the SAME stream's
        # tx-launch sweep would SIGTERM it as a "stale tx_worker".
        # Operator saw RX=0 + dpdk_rx_worker exit_code=0; no error
        # in stderr because the death was a clean signal handler.
        _pat = f"tx_worker.*--stream-id {stream_id}"
        _check = subprocess.run(["pgrep", "-f", "--", _pat], capture_output=True, text=True, timeout=3)
        if _check.returncode == 0 and _check.stdout.strip():
            _stale_pids = _check.stdout.strip().split()
            LOG.warning(
                "[dpdk] pre-launch: %d stale tx_worker(s) for stream_id=%s "
                "(pids=%s) - terminating before launching new instance",
                len(_stale_pids), stream_id, ",".join(_stale_pids),
            )
            subprocess.run(["pkill", "-TERM", "-f", "--", _pat], capture_output=True, timeout=3)
            import time as _t
            _t.sleep(1.0)
            subprocess.run(["pkill", "-KILL", "-f", "--", _pat], capture_output=True, timeout=3)
    except Exception as _e:
        LOG.debug("[dpdk] pre-launch sweep for %s failed: %s", stream_id, _e)

    # v0.5.169: wrap in `systemd-run --scope --unit=netgen-tx-<sid>`
    # when available. Puts tx_worker in its own cgroup unit so:
    #   * `systemctl stop netgen-tx-<sid>.scope` is a kernel-
    #     guaranteed stop — no pgrep racing, no PID guessing.
    #   * If ostg-server dies, the unit survives but is enumerable
    #     via `systemctl list-units 'netgen-tx-*.scope'`, so the
    #     next-startup reaper finds every previous-session orphan
    #     in one call.
    # Empty prefix on hosts without systemd-run — falls back to the
    # naked Popen (v0.5.168's reactive reap still applies).
    try:
        from utils import systemd_scope
        cmd = systemd_scope.build_systemd_run_prefix(
            role="tx", stream_id=stream_id) + cmd
    except Exception as _e:
        LOG.debug("[dpdk] systemd_scope import failed: %s", _e)

    LOG.info("[dpdk] exec: %s", shlex.join(cmd))
    LOG.info("[dpdk] LD_LIBRARY_PATH=%s", child_env.get("LD_LIBRARY_PATH", "not set"))
    # start_new_session=True puts tx_worker in its own session/process group
    # so we can killpg() the entire group on shutdown — defensive against
    # tx_worker spawning helper subprocesses that otherwise survive as
    # orphans when their parent dies.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env,
        start_new_session=True,
    )

    # Parse "STAT ..." lines to sync StreamTracker
    # Match: "STAT stream=... tx=123 drop=0 ..." or "STAT_FINAL stream=... tx=123 drop=0"
    stat_re = re.compile(r"^STAT(?:_FINAL)?\s+.*?\bstream=(\S+)\s+tx=(\d+)\s+drop=(\d+)")
    
    # Track if we've seen any output (to detect immediate failures)
    seen_output = False
    error_lines = []

    # v0.3.2: stdout reader switched from `for line in proc.stdout:`
    # (blocking iteration, can park the thread indefinitely if DPDK
    # EAL hangs on device init) to a select-based poll. The select
    # call wakes every 500 ms even if no data arrives, giving the
    # loop a chance to:
    #   * notice stop_event has been set (operator clicked Stop)
    #   * notice the child process died (proc.poll() != None) and
    #     drain any final output before exiting
    # Pre-v0.3.2 both checks only fired after a line arrived; if EAL
    # was stuck waiting on a never-coming-up vfio-pci device, the
    # operator could not stop the stream without restarting the
    # server. See CHANGELOG for the full audit context.
    def _process_one_line(raw_line):
        """Inner dispatch — extracted so both the select-based main
        loop AND the drain-on-exit path can use the same routing
        logic for EAL / STAT / generic-error lines."""
        nonlocal seen_output
        line = raw_line.rstrip()
        if not line:
            return
        seen_output = True
        if line.startswith("EAL:"):
            if "No free" in line and "hugepages" in line:
                LOG.error("[dpdk] %s", line)
                LOG.error("[dpdk] Hugepages not configured. Configure hugepages using:")
                LOG.error("[dpdk]   echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")
                LOG.error("[dpdk]   echo 'vm.nr_hugepages=1024' >> /etc/sysctl.conf")
            elif "No DPDK ports" in line or "No available" in line:
                LOG.error("[dpdk] %s", line)
                LOG.error("[dpdk] Device not bound to DPDK. Bind the device using:")
                LOG.error("[dpdk]   Use 'Bind Interface to DPDK' in the DPDK menu")
            elif "Error" in line or "error" in line or "failed" in line:
                LOG.error("[dpdk] %s", line)
            else:
                LOG.debug("[dpdk] %s", line)
            return
        m = stat_re.search(line)
        if m:
            tx_abs = int(m.group(2))
            current = _safe_get_tx(tracker, interface, stream_id)
            delta = max(tx_abs - current, 0)
            if delta:
                tracker.update_tx_by_id(interface, stream_id, count=delta)
            LOG.debug("[dpdk] STAT: stream=%s tx_abs=%s current=%s delta=%s",
                      stream_id, tx_abs, current, delta)
            return
        line_lower = line.lower()
        if any(k in line_lower for k in (
            "error", "failed", "fail", "cannot", "unable",
            "invalid", "not found", "missing",
        )):
            LOG.error("[dpdk] %s", line)
            error_lines.append(line)
        elif any(k in line_lower for k in ("warning", "warn")):
            LOG.warning("[dpdk] %s", line)
        else:
            LOG.info("[dpdk] %s", line)

    try:
        # Cache the fd once; proc.stdout's wrapper survives until
        # `proc` is GC'd at end of scope.
        stdout_fd = proc.stdout.fileno() if proc.stdout else -1
        _POLL_TIMEOUT_S = 0.5

        while True:
            # Stop-event check FIRST so a request that came in while
            # we were blocked in the previous select returns control
            # to the caller before we attempt another read.
            if stop_event.is_set():
                LOG.debug("[dpdk] stop_event set — exiting stdout reader")
                break

            # Process-died check. If the child exited (clean or
            # crashed), drain any buffered output then bail. Without
            # this the loop would spin forever calling select on a
            # closed pipe.
            if proc.poll() is not None:
                try:
                    remaining = proc.stdout.read() if proc.stdout else ""
                except Exception:
                    remaining = ""
                if remaining:
                    for line in remaining.splitlines():
                        _process_one_line(line)
                break

            if stdout_fd < 0:
                # No stdout to read — degenerate; just wait for proc
                # to exit so the poll() check above eventually fires.
                if stop_event.wait(_POLL_TIMEOUT_S):
                    break
                continue

            try:
                rlist, _, _ = select.select(
                    [stdout_fd], [], [], _POLL_TIMEOUT_S,
                )
            except (OSError, ValueError):
                # fd closed under us — treat as EOF, let the loop's
                # next poll() check route us out.
                continue

            if not rlist:
                # 500 ms passed with no data — loop back to the
                # stop_event + poll() checks. This is the whole
                # point of the v0.3.2 refactor.
                continue

            # Data available. readline() may still block waiting for
            # the line terminator, but tx_worker's stdout is
            # line-buffered (bufsize=1 in subprocess.Popen above) so
            # each readable event corresponds to a complete line in
            # the common case. The defensive try/except below catches
            # the rare partial-line/EOF scenarios.
            try:
                line = proc.stdout.readline()
            except (OSError, ValueError):
                continue
            if not line:
                # EOF — child probably just exited; let the next
                # poll() check confirm and drain.
                continue

            _process_one_line(line)
        
        # Check if process exited immediately without output (likely a failure)
        if not seen_output:
            # Wait a moment to see if process exits
            import time
            time.sleep(0.5)
            if proc.poll() is not None:
                rc = proc.returncode
                if rc != 0:
                    LOG.error("[dpdk] tx_worker exited immediately with code %d (no output captured)", rc)
                    LOG.error("[dpdk] This usually indicates:")
                    LOG.error("[dpdk]   - Missing libraries (check LD_LIBRARY_PATH)")
                    LOG.error("[dpdk]   - Permission denied (need root/sudo)")
                    LOG.error("[dpdk]   - Invalid command arguments")
                    LOG.error("[dpdk]   - Device not bound to DPDK")
                    error_lines.append(f"Process exited with code {rc} (no output)")
                    # Try to read any remaining output
                    try:
                        remaining = proc.stdout.read() if proc.stdout else ""
                        if remaining:
                            LOG.error("[dpdk] Remaining output: %s", remaining)
                            error_lines.append(f"Output: {remaining}")
                    except Exception:
                        pass
    except Exception as e:  # defensive
        LOG.warning("[dpdk] stdout reader error: %s", e)
        error_lines.append(f"Error reading output: {e}")
    finally:
        # Two-stage shutdown:
        #   1. SIGINT the known process group (graceful — tx_worker's
        #      handler sets keep_running=0 and the workers drain).
        #   2. If it doesn't exit in 3s, escalate to SIGKILL on the group.
        # Then sweep any orphan tx_worker for the SAME stream_id that
        # might have survived a previous launcher (e.g. server SIGKILL).
        # Without the sweep, repeated start/stop cycles would leak
        # tx_worker processes that keep blasting traffic after we're gone.
        if proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
            except Exception:
                pgid = None
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGINT)
                else:
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    LOG.warning("[dpdk] tx_worker pid=%s did not exit on SIGINT, escalating to SIGKILL",
                                proc.pid)
                    try:
                        if pgid is not None:
                            os.killpg(pgid, signal.SIGKILL)
                        else:
                            proc.kill()
                    except Exception:
                        proc.kill()
            except Exception as e:
                LOG.warning("[dpdk] error during shutdown of pid=%s: %s", proc.pid, e)
                try:
                    proc.kill()
                except Exception:
                    pass

        # Orphan reaper: any tx_worker that's still running with our
        # stream_id is a leftover from a prior launcher we lost track of.
        # SIGTERM first (lets DPDK release hugepages cleanly), SIGKILL
        # 1s later as a backstop. Scoped by stream_id so we don't
        # disturb other concurrent streams.
        #
        # v0.5.253 (audit DPDK-1): mirror the pre-launch sweep (line
        # 480) — the previous pattern `--stream-id <sid>` had two
        # bugs. First, pkill treats a pattern that starts with `--`
        # as an unknown option and returns rc=2, so the whole reaper
        # was a silent no-op. Second, adding just `--` re-opens the
        # v0.5.119 friendly-fire regression: `--stream-id <sid>` also
        # matches the rx_worker cmdline (both binaries share it), so
        # cleaning up tx would SIGKILL the paired rx. Anchor on the
        # `tx_worker` binary name AND pass `--` before the pattern.
        try:
            sweep_pat = f"tx_worker.*--stream-id {stream_id}"
            r = subprocess.run(
                ["pkill", "-TERM", "-f", "--", sweep_pat],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                LOG.warning("[dpdk] reaped orphan tx_worker(s) for stream_id=%s", stream_id)
                import time as _t
                _t.sleep(1.0)
                subprocess.run(
                    ["pkill", "-KILL", "-f", "--", sweep_pat],
                    capture_output=True, timeout=3,
                )
        except Exception as e:
            LOG.debug("[dpdk] orphan-reaper for stream_id=%s: %s", stream_id, e)

    rc = proc.returncode if proc.returncode is not None else 0
    
    # Check for BNXT ULP errors - return special code for automatic fallback
    is_broadcom_ulp_error = False
    if rc != 0 and error_lines:
        error_text = "\n".join(error_lines).lower()
        if "bnxt" in error_text and ("ulp" in error_text or "stats cache" in error_text or "timeout" in error_text or "-110" in error_text or "hwrm" in error_text):
            is_broadcom_ulp_error = True
    
    # Log final status with error details if any
    if rc != 0:
        LOG.error("[dpdk] stream '%s' (id=%s) failed with exit code %s", stream_name, stream_id, rc)
        if error_lines:
            LOG.error("[dpdk] Error details:")
            for err_line in error_lines:
                LOG.error("[dpdk]   %s", err_line)
            
            if is_broadcom_ulp_error:
                LOG.error("[dpdk] ⚠️  BNXT ULP initialization error detected")
                LOG.error("[dpdk] This is a known issue with some Broadcom NICs (vendor 14e4)")
                LOG.error("[dpdk] The error 'Failed to allocate stats cache table' indicates a firmware/driver issue.")
                LOG.error("[dpdk] Automatically falling back to Scapy backend...")
                LOG.error("[dpdk] Note: To permanently use Scapy, uncheck 'Use DPDK' in the stream configuration")
    else:
        LOG.info("[dpdk] stream '%s' (id=%s) finished successfully, rc=%s", stream_name, stream_id, rc)
    
    # Return special exit code 100 for Broadcom ULP errors (triggers automatic Scapy fallback)
    if is_broadcom_ulp_error:
        return 100
    
    return rc


# ------------------------- Internals -------------------------

def _uuid() -> str:
    import uuid as _uuid_mod
    return str(_uuid_mod.uuid4())

def _safe_get_tx(tracker, interface: str, stream_id: str) -> int:
    try:
        return int(tracker.get_tx_count_by_id(interface, stream_id))
    except Exception:
        return 0

def _resolve_duration_seconds(stream_data: Dict[str, Any]) -> Optional[int]:
    # Honor tx_duration first (new style)
    txd = stream_data.get("tx_duration", {}) or {}
    if str(txd.get("mode") or "").strip().lower() == "seconds":
        try:
            return int(txd.get("seconds") or 0)
        except Exception:
            return 0

    # Fall back to stream_rate_control / legacy fields
    rc = stream_data.get("stream_rate_control", {}) or {}
    mode = rc.get("stream_duration_mode") or stream_data.get("stream_duration_mode")
    if str(mode or "").strip().lower() == "seconds":
        try:
            return int(rc.get("stream_duration_seconds", stream_data.get("stream_duration_seconds", 10)) or 10)
        except Exception:
            return 10
    return None

def _resolve_tx_worker_bin() -> str:
    """
    Search order — prefer freshly-rebuilt install-dir binaries over the wheel-
    shipped one, because the wheel's tx_worker is built against an old DPDK
    ABI (e.g. so.22) but a fresh `install_dpdk.sh` build links against the
    currently-installed DPDK (e.g. so.24). Picking the wheel binary on a box
    where DPDK was rebuilt produces:
        error while loading shared libraries: librte_ethdev.so.22:
        cannot open shared object file
    and exit code 127.

    Order:
      1) $TX_WORKER_BIN if file exists                             — explicit override
      2) /opt/netgen/resources/dpdk/tx_worker/build/tx_worker      — current install dir
      3) /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker        — legacy install dir
      4) importlib.resources: resources.dpdk.tx_worker (wheel)     — likely stale ABI
      5) path relative to this file
      6) CWD fallback
      7) Legacy local tree (./tx_worker/build/tx_worker)
    """
    # 1) explicit env override
    p = os.environ.get("TX_WORKER_BIN")
    if p and os.path.exists(p):
        LOG.debug("[dpdk] TX_WORKER_BIN=%s", p)
        return os.path.abspath(p)

    # 2) /usr/local/bin/tx_worker — operator-bite caught on srv06
    # (Jun 11 2026): install_dpdk.sh Step 6 `make install` lands the
    # rebuilt binary here, the v0.5.67 health probe checks here,
    # AND it's the first thing on $PATH. But the launcher's search
    # order skipped it, so /api/dpdk/status reported tx_worker_exists
    # while the stream launch said "binary not found". Promoted to
    # priority 2 so install_dpdk.sh's authoritative copy wins over
    # the wheel-shipped stale-ABI fallback.
    cand = "/usr/local/bin/tx_worker"
    if os.path.exists(cand):
        LOG.debug("[dpdk] using /usr/local/bin/tx_worker (install_dpdk.sh)")
        return cand

    # 3-4) install-dir binaries (rebuilt by install_dpdk.sh against current DPDK)
    for install_dir in ("/opt/netgen", "/opt/OSTG"):
        cand = os.path.join(install_dir, "resources", "dpdk", "tx_worker", "build", "tx_worker")
        if os.path.exists(cand):
            LOG.debug("[dpdk] using install-dir tx_worker: %s", cand)
            return os.path.abspath(cand)

    # 4) packaged resource (wheel-shipped — fallback only; ABI may be stale)
    try:
        # Python 3.9+: importlib.resources.files
        try:
            from importlib.resources import files as _res_files
            pkg = "resources.dpdk.tx_worker"
            rp = _res_files(pkg) / "build" / "tx_worker"
            if rp and os.path.exists(rp.as_posix()):
                LOG.debug("[dpdk] falling back to wheel tx_worker: %s (may have stale DPDK ABI)", rp.as_posix())
                return os.path.abspath(rp.as_posix())
        except Exception:
            # Back-compat: importlib.resources.path
            from importlib import resources as _res
            with _res.path("resources.dpdk.tx_worker", "build") as _build_dir:
                cand = os.path.join(str(_build_dir), "tx_worker")
                if os.path.exists(cand):
                    return os.path.abspath(cand)
    except Exception as e:
        LOG.debug("[dpdk] importlib.resources lookup failed: %s", e)

    # 5) relative to this file (site-packages layout has ../resources/…)
    here = os.path.dirname(__file__)
    cand = os.path.abspath(os.path.join(here, "..", "resources", "dpdk", "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # 6) cwd fallback
    cand = os.path.abspath(os.path.join(os.getcwd(), "resources", "dpdk", "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # 7) legacy local tree
    cand = os.path.abspath(os.path.join(os.getcwd(), "tx_worker", "build", "tx_worker"))
    if os.path.exists(cand):
        return cand

    # give a helpful path anyway
    return os.path.abspath(os.path.join(os.getcwd(), "resources", "dpdk", "tx_worker", "build", "tx_worker"))

def _iface_to_bdf(iface: str) -> Optional[str]:
    """Turn 'enp13s0f0np0' into '0000:0d:00.0' via sysfs, falling back
    to the admin bind-history JSON when the iface has already been
    bound to vfio-pci (kernel netdev is gone).

    v0.5.253 (audit DPDK-4): pre-fix a vfio-bound iface returned
    ``None``, and the caller then dropped the ``-a <bdf>`` EAL arg —
    ``RTE_ETH_FOREACH_DEV`` grabbed whichever DPDK port EAL enumerated
    first, so on a host with >=2 vfio-bound NICs the stream went out
    an arbitrary interface. The admin bind-history JSON records the
    original iface name at bind time; we consult it as a fallback.
    """
    try:
        dev = os.path.realpath(f"/sys/class/net/{iface}/device")
        bdf = os.path.basename(dev)
        if ":" in bdf:
            return bdf
    except Exception:
        pass
    # sysfs miss — try admin bind-history (kept in sync by the /api/dpdk/bind
    # endpoint). Two candidate paths, same shape as run_tgen_server.
    for path in (
        "/var/lib/netgen-server/admin_bind_history.json",
        "/tmp/netgen_admin_bind_history.json",
    ):
        try:
            if not os.path.exists(path):
                continue
            import json as _json
            with open(path, "r") as f:
                history = _json.load(f) or {}
            if not isinstance(history, dict):
                continue
            # history is {bdf: {"name": <pre-bind iface name>, ...}}.
            for bdf, entry in history.items():
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("name") or "").strip() == str(iface).strip():
                    return bdf
        except Exception as _e:
            LOG.debug("[dpdk] bind-history lookup for %s failed: %s", iface, _e)
    return None

def _bdf_numa_node(bdf: str) -> int:
    try:
        p = f"/sys/bus/pci/devices/{bdf}/numa_node"
        if os.path.exists(p):
            v = int(open(p).read().strip())
            return v if v >= 0 else 0
    except Exception:
        pass
    return 0

def _pick_corelist_on_node(numa_node: int, count: int = 2) -> str:
    """
    Pick `count` distinct CPUs on the requested NUMA node, comma-joined for EAL -l.
    Falls back to a sensible default ('0,2,4,6,...' truncated to count) if lscpu fails.

    The first core in the returned list is the EAL main lcore; the remaining
    (count - 1) become tx_worker worker lcores driving N TX queues.
    """
    if count < 2:
        count = 2
    try:
        out = subprocess.check_output(["lscpu", "-e=CPU,NODE"], text=True)
        cores = []
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) != 2:
                continue
            cpu, node = parts
            if str(node) == str(numa_node):
                cores.append(int(cpu))
        if len(cores) >= count:
            return ",".join(str(c) for c in cores[:count])
        if len(cores) >= 2:
            # not enough on this node — use what we have
            return ",".join(str(c) for c in cores)
    except Exception:
        pass
    # Generic even-numbered fallback (avoids HT siblings on most x86 layouts)
    return ",".join(str(2 * i) for i in range(count))

def _first_from(*pairs, default=None):
    for d, key in pairs:
        if isinstance(d, dict) and key in d and d[key] not in (None, ""):
            return d[key]
    return default

def _resolve_l2_l3_l4(stream_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract MACs, IPs, UDP ports, VLAN, frame size from stream_data.
    Prefers protocol_data.* with fallbacks to top-level.
    """
    pd = stream_data.get("protocol_data", {}) or {}
    mac_pd = pd.get("mac", {}) or {}
    ipv4_pd = pd.get("ipv4", {}) or {}
    udp_pd = pd.get("udp", {}) or {}
    vlan_pd = pd.get("vlan", {}) or {}

    src_mac = _first_from((mac_pd, "mac_source_address"),
                          (stream_data, "mac_source_address"),
                          (stream_data, "mac_src"), (stream_data, "mac_source"))
    dst_mac = _first_from((mac_pd, "mac_destination_address"),
                          (stream_data, "mac_destination_address"),
                          (stream_data, "mac_dst"), (stream_data, "mac_destination"))

    src_ip = _first_from((ipv4_pd, "ipv4_source"), (stream_data, "src_ip"))
    dst_ip = _first_from((ipv4_pd, "ipv4_destination"), (stream_data, "dst_ip"))

    def _pick_port(*vals, default=None):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if s == "":
                continue
            try:
                x = int(s, 10)
                if x > 0:
                    return x
            except Exception:
                pass
        return default

    # Defaults if unset/zero: sport=1234, dport=4791 (good match with RoCEv2/UDP)
    udp_sport = _pick_port(udp_pd.get("udp_source_port"),
                           stream_data.get("udp_source_port"),
                           stream_data.get("udp_sport"),
                           default=1234)
    udp_dport = _pick_port(udp_pd.get("udp_destination_port"),
                           stream_data.get("udp_destination_port"),
                           stream_data.get("udp_dport"),
                           default=4791)

    # v0.5.124: use the shared resolver. Originally inlined in
    # v0.5.120/121; v0.5.124 hoists to utils/vlan_helpers so the
    # exact same logic governs every TX-side packet builder
    # (generic, uec, rocev2, dpdk_tx_worker). One reviewable
    # source of truth makes the bug shape structurally impossible
    # — see the saga summary in the v0.5.123 CHANGELOG.
    from utils.vlan_helpers import resolve_tx_vlan_id
    vlan_id = resolve_tx_vlan_id(stream_data)

    # v0.5.253 (audit DPDK-8): fall back to protocol_selection.frame_size
    # for parity with _resolve_target_pps and resolve_actual_tx_cores.
    # Pre-fix, a stream whose dialog stored frame size only under
    # protocol_selection got `--size 64` on the wire while the pps math
    # (and auto-tx-cores calc) used the real size — same class of bug
    # as v0.5.121 (VLAN under protocol_selection).
    ps = stream_data.get("protocol_selection", {}) or {}
    frame_size = 64
    for _src in (stream_data.get("frame_size"), ps.get("frame_size")):
        if _src in (None, ""):
            continue
        try:
            frame_size = int(_src)
            break
        except Exception:
            pass

    return dict(
        src_mac=src_mac, dst_mac=dst_mac,
        src_ip=src_ip,   dst_ip=dst_ip,
        udp_sport=udp_sport, udp_dport=udp_dport,
        vlan_id=vlan_id, frame_size=frame_size,
    )

def _user_specified_tx_cores(stream_data: Dict[str, Any]) -> bool:
    """Return True iff the stream JSON has an explicit dpdk_tx_cores
    setting from the user (anywhere we look). Used to distinguish "user
    really wants single-queue" from "user didn't touch the field" so we
    can auto-bump tx_cores for Line Rate streams without overriding an
    explicit choice."""
    if not isinstance(stream_data, dict):
        return False
    if stream_data.get("dpdk_tx_cores") not in (None, ""):
        return True
    ps = stream_data.get("protocol_selection") or {}
    if isinstance(ps, dict) and ps.get("dpdk_tx_cores") not in (None, ""):
        return True
    if os.environ.get("DPDK_TX_CORES"):
        return True
    return False


def _auto_tx_cores_for_line_rate(interface: str, frame_size: int) -> int:
    """Pick a tx_cores value sufficient to saturate the interface's link
    speed at the given frame size. Mirrors /api/dpdk/recommend's math.
    Returns 1 if the link speed can't be read.

    Per-core ceilings calibrated on Mellanox CX-7 + EPYC:
      <=128B:  4.5 Mpps/core   (CPU-bound for tiny pkts)
      <=512B:  4.3 Mpps/core
      else:    4.1 Mpps/core   (mempool/PCIe-bound for large pkts)
    Rounds up to a supported step (1, 2, 4, 8, 12, 16; cap at 16)."""
    try:
        if not interface:
            return 1
        p = f"/sys/class/net/{interface}/speed"
        if not os.path.exists(p):
            return 1
        try:
            link_mbps = int(open(p).read().strip())
        except Exception:
            return 1
        if link_mbps <= 0:
            return 1
        # L1 bytes per frame = frame_size + 20 (preamble + IFG)
        line_pps = max(1, (link_mbps * 1_000_000) // ((max(60, int(frame_size)) + 20) * 8))
        if frame_size <= 128:
            per_core = 4_500_000
        elif frame_size <= 512:
            per_core = 4_300_000
        else:
            per_core = 4_100_000
        need = max(1, (line_pps + per_core - 1) // per_core)
        for s in (1, 2, 4, 8, 12, 16):
            if s >= need:
                return s
        return 16
    except Exception:
        return 1


def _resolve_tx_cores(stream_data: Dict[str, Any]) -> int:
    """
    Number of TX worker threads (= TX queues) inside the tx_worker process.

    Default 1 (single queue, backwards-compatible). Higher values let a single
    NIC port saturate higher line rates by spreading TX across multiple queues
    and lcores.

    Reads (in order):
      stream_data['dpdk_tx_cores']
      stream_data['protocol_selection']['dpdk_tx_cores']
      env DPDK_TX_CORES
    """
    candidates = []
    candidates.append(stream_data.get("dpdk_tx_cores"))
    ps = stream_data.get("protocol_selection", {}) or {}
    candidates.append(ps.get("dpdk_tx_cores"))
    candidates.append(os.environ.get("DPDK_TX_CORES"))
    for v in candidates:
        if v in (None, ""):
            continue
        try:
            n = int(v)
            if n >= 1:
                return min(n, 32)  # MAX_TX_WORKERS in tx_worker.c
        except Exception:
            pass
    return 1


def _resolve_count_flags(stream_data: Dict[str, Any]) -> list:
    """v0.5.128: build the --src/dst-{ip,port}-count cmd flags from
    a stream's config. Look in both top-level (API-direct convention)
    and protocol_data.{ipv4,udp} (dialog convention). Top-level wins
    when set explicitly; otherwise the dialog's nested value applies.

    Pre-v0.5.128 only the top-level short form was read, so dialog-
    driven streams that set dst_port_count=64 (via
    protocol_data.udp.udp_destination_port_count) got no flag → TX
    cycled nothing → RSS landed every packet on queue 0 →
    multi-queue rx_worker provided no benefit.

    Returns a flat list ready to extend the tx_worker argv.
    """
    pd = stream_data.get("protocol_data") or {}
    udp = pd.get("udp") or {}
    ipv4 = pd.get("ipv4") or {}
    pairs = (
        ("--src-ip-count",
         stream_data.get("src_ip_count"),
         ipv4.get("ipv4_source_increment_count")),
        ("--dst-ip-count",
         stream_data.get("dst_ip_count"),
         ipv4.get("ipv4_destination_increment_count")),
        ("--src-port-count",
         stream_data.get("src_port_count"),
         udp.get("udp_source_port_count")),
        ("--dst-port-count",
         stream_data.get("dst_port_count"),
         udp.get("udp_destination_port_count")),
    )
    out = []
    for cli_flag, top_val, pd_val in pairs:
        # Top-level wins when set; fall through to protocol_data.
        chosen = top_val if top_val not in (None, "") else pd_val
        try:
            n = int(chosen or 0)
        except (TypeError, ValueError):
            n = 0
        if n >= 2:
            out += [cli_flag, str(n)]
    return out


def _resolve_target_pps(stream_data: Dict[str, Any],
                        iface: Optional[str] = None,
                        frame_size: Optional[int] = None) -> int:
    """
    Convert your rate selection into a numeric PPS for tx_worker (0 = flood).
    Supports:
      - Packets Per Second (PPS)
      - Bit Rate (bps/Mbps)
      - Load (%)
      - "Line Rate" => 0
      Reads from stream_rate_control, top-level fields, protocol_selection, and tx_rate.

    v0.5.253 (audit DPDK-2): accepts an optional ``iface`` + ``frame_size``.
    Load (%) mode multiplies against the interface's actual line-rate PPS
    when ``iface`` is provided; without it (legacy callers) it falls back
    to the historical 1 GbE baseline (1.25 Mpps at 100%). Pre-fix, Load=50
    on a 100 G NIC returned 625 kpps — ~120x low — with no warning.
    """
    rc = stream_data.get("stream_rate_control", {}) or {}
    ps = stream_data.get("protocol_selection", {}) or {}
    tr = stream_data.get("tx_rate", {}) or {}

    def pick_num(*vals, default=0):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            try:
                return int(float(s))
            except Exception:
                pass
        return default

    rtype = (rc.get("stream_rate_type")
             or stream_data.get("stream_rate_type")
             or ps.get("stream_rate_type")
             or tr.get("mode")
             or "Packets Per Second (PPS)")
    rtype_l = str(rtype).strip().lower()
    if rtype_l == "bit rate":
        rtype_l = "bit rate (mbps)"

    if rtype_l in ("line rate", "line-rate", "linerate", "line"):
        return 0  # flood (worker decides)

    if rtype_l in ("packets per second (pps)", "pps"):
        return pick_num(rc.get("stream_pps_rate"),
                        stream_data.get("stream_pps_rate"),
                        ps.get("stream_pps_rate"),
                        tr.get("pps"),
                        default=0)

    if rtype_l in ("bit rate (mbps)", "mbps", "bitrate"):
        br_mbps = pick_num(rc.get("stream_bit_rate"),
                           stream_data.get("stream_bit_rate"),
                           ps.get("stream_bit_rate"),
                           tr.get("mbps"),
                           default=0)
        frame_size = pick_num(stream_data.get("frame_size"), ps.get("frame_size"), default=64)
        bps = br_mbps * 1_000_000
        bytes_per_packet = max(frame_size + 20, 1)
        return int(bps / (bytes_per_packet * 8))

    if rtype_l in ("bit rate (bps)", "bps"):
        br_bps = pick_num(rc.get("stream_bit_rate"),
                          stream_data.get("stream_bit_rate"),
                          ps.get("stream_bit_rate"),
                          tr.get("bps"),
                          default=0)
        frame_size = pick_num(stream_data.get("frame_size"), ps.get("frame_size"), default=64)
        bytes_per_packet = max(frame_size + 20, 1)
        return int(br_bps / (bytes_per_packet * 8))

    if rtype_l in ("load (%)", "load", "percent"):
        load = pick_num(rc.get("stream_load_percentage"),
                        stream_data.get("stream_load_percentage"),
                        ps.get("stream_load_percentage"),
                        tr.get("percent"),
                        default=0)
        # v0.5.253 (audit DPDK-2): scale off the interface's actual
        # line-rate PPS when we know it. `_compute_line_pps` reads
        # `/sys/class/net/<iface>/speed` and returns 1 Mpps as a safe
        # fallback if that read fails. Only callers that omit `iface`
        # (legacy: unit tests, hand-rolled callers) get the old 1 GbE
        # baseline.
        if iface:
            fs = int(frame_size) if (frame_size and frame_size > 0) else 64
            base_pps = _compute_line_pps(iface, fs)
            return int((base_pps * load) / 100)
        return int((1_250_000 * load) / 100)

    return 0

def _file_prefix(stream_id: str, iface: str) -> str:
    """
    Produce a unique, sanitized EAL file-prefix for each worker to avoid
    collisions in hugepage-backed shared mem regions across processes.
    """
    import time, re, os
    try:
        import uuid as _uuid_mod
        short = str(_uuid_mod.UUID(str(stream_id))).split("-")[0]
    except Exception:
        short = re.sub(r"[^a-zA-Z0-9]+", "", str(stream_id))[:8] or "x"
    ifx = re.sub(r"[^A-Za-z0-9_]+", "_", str(iface))
    return f"txw_{short}_{ifx}_{os.getpid()}_{int(time.time()*1000)}"

# (Optional helpers if you later choose to convert "line" to numeric PPS)
def _read_link_speed_mbps(iface: str) -> int:
    try:
        p = f"/sys/class/net/{iface}/speed"
        if os.path.exists(p):
            v = int(open(p).read().strip())
            if v > 0:
                return v
    except Exception:
        pass
    return 0

def _compute_line_pps(iface: str, frame_size_bytes: int) -> int:
    speed = _read_link_speed_mbps(iface)  # e.g., 100000 for 100G
    if speed <= 0:
        return 1_000_000  # safe fallback
    l1_bytes = max(64, int(frame_size_bytes)) + 20  # preamble+IFG approx
    return max(1, int((speed * 1_000_000) // (l1_bytes * 8)))
