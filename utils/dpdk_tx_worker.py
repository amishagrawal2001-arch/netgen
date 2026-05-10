# utils/dpdk_tx_worker.py
# DPDK tx_worker launcher + stream sync for OSTG.
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import logging
from typing import Optional, Dict, Any

LOG = logging.getLogger("dpdk_tx_worker")

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
    pps = _resolve_target_pps(stream_data)
    vlan_id = fields["vlan_id"]
    frame_size = int(fields["frame_size"] or 64)

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

    # Environment (allow caller to inject EAL/PMD paths etc.)
    child_env = os.environ.copy()
    if env:
        child_env.update(env)

    # Helpful defaults if user didn't set them
    child_env.setdefault("RTE_DISABLE_MEMPOOL_OPS", "1")   # avoids missing shared objs on some hosts
    
    # Set LD_LIBRARY_PATH to include DPDK libraries
    # Try multiple methods to find DPDK library directory
    current_ld_path = child_env.get("LD_LIBRARY_PATH", "")
    dpdk_lib_paths = []
    
    # Method 1: Use pkg-config to find DPDK library directory
    try:
        import subprocess
        pkg_config_paths = [
            "/usr/local/lib/x86_64-linux-gnu/pkgconfig",
            "/usr/local/lib/pkgconfig",
            "/usr/lib/x86_64-linux-gnu/pkgconfig",
            "/usr/lib/pkgconfig"
        ]
        for pc_path in pkg_config_paths:
            if os.path.exists(pc_path):
                env_pc = os.environ.copy()
                env_pc["PKG_CONFIG_PATH"] = f"{pc_path}:{env_pc.get('PKG_CONFIG_PATH', '')}"
                result = subprocess.run(
                    ["pkg-config", "--variable=libdir", "libdpdk"],
                    env=env_pc,
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    libdir = result.stdout.strip()
                    if libdir and os.path.exists(libdir) and libdir not in dpdk_lib_paths:
                        dpdk_lib_paths.append(libdir)
                        LOG.debug("[dpdk] Found DPDK libdir via pkg-config: %s", libdir)
                    break
    except Exception as e:
        LOG.debug("[dpdk] pkg-config method failed: %s", e)
    
    # Method 2: Check common locations for DPDK libraries
    common_paths = [
        "/usr/local/lib/x86_64-linux-gnu",
        "/usr/local/lib",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib"
    ]
    for lib_path in common_paths:
        if os.path.exists(lib_path):
            # Check if this directory contains DPDK libraries
            try:
                files = os.listdir(lib_path)
                if any(f.startswith("librte_") and f.endswith(".so") or ".so." in f for f in files):
                    if lib_path not in dpdk_lib_paths:
                        dpdk_lib_paths.append(lib_path)
                        LOG.debug("[dpdk] Found DPDK libraries in: %s", lib_path)
            except Exception:
                pass
    
    # Method 3: Use ldconfig to find librte_ethdev.so
    try:
        result = subprocess.run(
            ["ldconfig", "-p"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "librte_ethdev.so" in line:
                    # Extract path from ldconfig output (format: "librte_ethdev.so.22 (libc6,x86-64) => /path/to/lib.so.22")
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
    
    # Add paths that aren't already in LD_LIBRARY_PATH
    for lib_path in dpdk_lib_paths:
        if lib_path not in current_ld_path:
            if current_ld_path:
                current_ld_path = f"{lib_path}:{current_ld_path}"
            else:
                current_ld_path = lib_path
    
    if current_ld_path:
        child_env["LD_LIBRARY_PATH"] = current_ld_path
        LOG.info("[dpdk] Setting LD_LIBRARY_PATH=%s", current_ld_path)
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
        _pat = f"--stream-id {stream_id}"
        _check = subprocess.run(["pgrep", "-f", _pat], capture_output=True, text=True, timeout=3)
        if _check.returncode == 0 and _check.stdout.strip():
            _stale_pids = _check.stdout.strip().split()
            LOG.warning(
                "[dpdk] pre-launch: %d stale tx_worker(s) for stream_id=%s "
                "(pids=%s) - terminating before launching new instance",
                len(_stale_pids), stream_id, ",".join(_stale_pids),
            )
            subprocess.run(["pkill", "-TERM", "-f", _pat], capture_output=True, timeout=3)
            import time as _t
            _t.sleep(1.0)
            subprocess.run(["pkill", "-KILL", "-f", _pat], capture_output=True, timeout=3)
    except Exception as _e:
        LOG.debug("[dpdk] pre-launch sweep for %s failed: %s", stream_id, _e)

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

    try:
        for line in proc.stdout:  # type: ignore[arg-type]
            if line is None:
                break
            line = line.rstrip()
            if not line:
                continue
            
            seen_output = True
            
            # Log EAL initialization messages at debug level, but capture errors
            if line.startswith("EAL:"):
                # Check for common EAL errors and log them at warning/error level
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
                continue
            
            # Parse STAT lines for statistics
            m = stat_re.search(line)
            if m:
                tx_abs = int(m.group(2))
                # Convert absolute to deltas for StreamTracker
                current = _safe_get_tx(tracker, interface, stream_id)
                delta = max(tx_abs - current, 0)
                if delta:
                    # Use batch update to reduce lock contention and improve performance
                    tracker.update_tx_by_id(interface, stream_id, count=delta)
                LOG.debug("[dpdk] STAT: stream=%s tx_abs=%s current=%s delta=%s", stream_id, tx_abs, current, delta)
                continue
            
            # Log all other lines (errors, warnings, etc.)
            # Check for common error patterns
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in ["error", "failed", "fail", "cannot", "unable", "invalid", "not found", "missing"]):
                LOG.error("[dpdk] %s", line)
                error_lines.append(line)
            elif any(keyword in line_lower for keyword in ["warning", "warn"]):
                LOG.warning("[dpdk] %s", line)
            else:
                # Log other output at info level (might be useful for debugging)
                LOG.info("[dpdk] %s", line)

            if stop_event.is_set():
                break
        
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
        try:
            sweep_pat = f"--stream-id {stream_id}"
            r = subprocess.run(
                ["pkill", "-TERM", "-f", sweep_pat],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                LOG.warning("[dpdk] reaped orphan tx_worker(s) for stream_id=%s", stream_id)
                import time as _t
                _t.sleep(1.0)
                subprocess.run(
                    ["pkill", "-KILL", "-f", sweep_pat],
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

    # 2-3) install-dir binaries (rebuilt by install_dpdk.sh against current DPDK)
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
    """Turn 'enp13s0f0np0' into '0000:0d:00.0' via sysfs."""
    try:
        dev = os.path.realpath(f"/sys/class/net/{iface}/device")
        bdf = os.path.basename(dev)
        return bdf if ":" in bdf else None
    except Exception:
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

    vlan_id = _first_from((vlan_pd, "vlan_id"), (stream_data, "vlan_id"))
    try:
        vlan_id = int(vlan_id) if vlan_id not in (None, "", "0") else None
    except Exception:
        vlan_id = None

    try:
        frame_size = int(stream_data.get("frame_size", 64))
    except Exception:
        frame_size = 64

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


def _resolve_target_pps(stream_data: Dict[str, Any]) -> int:
    """
    Convert your rate selection into a numeric PPS for tx_worker (0 = flood).
    Supports:
      - Packets Per Second (PPS)
      - Bit Rate (bps/Mbps)
      - Load (%)
      - "Line Rate" => 0
      Reads from stream_rate_control, top-level fields, protocol_selection, and tx_rate.
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
        # naive 1GbE baseline: 100% ≈ 1.25 Mpps
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
