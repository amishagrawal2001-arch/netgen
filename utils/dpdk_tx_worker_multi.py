# utils/dpdk_tx_worker_multi.py
# Multi-instance DPDK tx_worker launcher for high-rate traffic (100Gbps+)
"""
Multi-instance DPDK support for achieving line rate on high-speed NICs (100Gbps, 400Gbps).

This module extends the single-instance DPDK backend to support:
- Multiple tx_worker instances per port (for 400Gbps saturation)
- Automatic instance count calculation based on target rate
- Aggregated statistics across instances
- NUMA-aware core distribution
"""

import os
import logging
import threading
import time
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

try:
    from .dpdk_tx_worker import (
        should_use_dpdk,
        _resolve_l2_l3_l4,
        _resolve_target_pps,
        _resolve_duration_seconds,
        _iface_to_bdf,
        _bdf_numa_node,
        _pick_corelist_on_node,
        _resolve_tx_worker_bin,
        _file_prefix,
        _uuid,
        _safe_get_tx,
    )
except ImportError:
    # Fallback if relative import fails
    from utils.dpdk_tx_worker import (
        should_use_dpdk,
        _resolve_l2_l3_l4,
        _resolve_target_pps,
        _resolve_duration_seconds,
        _iface_to_bdf,
        _bdf_numa_node,
        _pick_corelist_on_node,
        _resolve_tx_worker_bin,
        _file_prefix,
        _uuid,
        _safe_get_tx,
    )

LOG = logging.getLogger("dpdk_multi")


def calculate_instance_count(target_pps: int, interface: str, stream_data: Dict[str, Any]) -> int:
    """
    Calculate optimal number of DPDK instances needed for target rate.
    
    Rules:
    - For rates <= 50M pps: 1 instance
    - For rates 50M-200M pps: 2-4 instances
    - For rates > 200M pps: 4-8 instances
    - For 400Gbps line rate: 8-16 instances
    
    Can be overridden by stream_data["dpdk_num_instances"]
    """
    # Check if explicitly set
    explicit = stream_data.get("dpdk_num_instances")
    if explicit:
        try:
            return max(1, int(explicit))
        except (ValueError, TypeError):
            pass
    
    # Auto-calculate based on target rate
    if target_pps == 0:  # Line rate / flood
        # For line rate, use more instances
        # 400Gbps typically needs 8-16 instances
        # 100Gbps typically needs 2-4 instances
        # Estimate based on interface speed if available
        try:
            # Try to detect interface speed from sysfs
            speed_path = f"/sys/class/net/{interface}/speed"
            if os.path.exists(speed_path):
                speed_mbps = int(open(speed_path).read().strip())
                if speed_mbps >= 400000:  # 400Gbps
                    return 16
                elif speed_mbps >= 100000:  # 100Gbps
                    return 4
        except Exception:
            pass
        # Default for line rate: 8 instances
        return 8
    
    # Rate-based calculation
    if target_pps <= 50_000_000:  # <= 50M pps
        return 1
    elif target_pps <= 100_000_000:  # 50M-100M pps
        return 2
    elif target_pps <= 200_000_000:  # 100M-200M pps
        return 4
    else:  # > 200M pps
        return 8


def run_stream_multi_instance(
    stream_data: Dict[str, Any],
    interface: str,
    stop_event,
    tracker,
    *,
    num_instances: Optional[int] = None,
    dpdk_corelist: Optional[str] = None,
    tx_worker_bin: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> int:
    """
    Launch multiple DPDK tx_worker instances for high-rate traffic generation.
    
    This function:
    1. Calculates optimal number of instances
    2. Distributes target rate across instances
    3. Launches instances with different core lists
    4. Aggregates statistics from all instances
    5. Monitors all instances and handles cleanup
    
    Returns: 0 on success, non-zero on error
    """
    stream_id = stream_data.get("stream_id", _uuid())
    stream_name = stream_data.get("name", "Unnamed")
    
    # Calculate instance count
    target_pps = _resolve_target_pps(stream_data)
    if num_instances is None:
        num_instances = calculate_instance_count(target_pps, interface, stream_data)
    
    LOG.info(
        "[DPDK-MULTI] Launching %d instance(s) for stream '%s' (id=%s) on %s, target_pps=%s",
        num_instances, stream_name, stream_id, interface, target_pps
    )
    
    # Validate required fields
    fields = _resolve_l2_l3_l4(stream_data)
    missing = [k for k in ("src_mac", "dst_mac", "src_ip", "dst_ip") if not fields.get(k)]
    if missing:
        LOG.error("[dpdk-multi] missing required fields: %s", missing)
        return 2
    
    # Get device info
    bdf = _iface_to_bdf(interface)
    numa = _bdf_numa_node(bdf) if bdf else 0

    # Single-device guard + delegation:
    # Multi-instance launches N independent PRIMARY DPDK processes, each
    # passing `-a <bdf>` for the same NIC. DPDK only allows ONE primary
    # process to own a given device — secondary processes need
    # `--proc-type=secondary` and a shared mempool with the primary,
    # which the tx_worker C source doesn't currently support. Symptom:
    # instances probe (each with its own --file-prefix) but only the
    # first can configure TX queues, so tx_count stays at 0 across all.
    #
    # On top of that, the multi monitor's tracker.update_tx_by_id() call
    # at the bottom of monitor_instances() is a `count=0` placeholder
    # (search this file) — it never aggregates real stats even in the
    # N=1 case, and it doesn't read tx_worker stdout for STAT lines the
    # way single-instance dpdk_tx_worker.run_stream() does. Net result:
    # any path through this function reports tx_count=0 to the database.
    #
    # Until the tx_worker is reworked for secondary-process mode AND
    # the monitor is rebuilt to actually parse STAT output, the right
    # behaviour for the single-device case is to delegate to the proven
    # single-instance backend. Multi-port setups (one BDF per port,
    # caller-orchestrated) remain the path to scale beyond ~5Mpps.
    if num_instances >= 1 and bdf:
        if num_instances > 1:
            LOG.warning(
                "[DPDK-MULTI] Requested %d instances on single device %s — DPDK "
                "primary-process model can't share one NIC across N processes. "
                "Falling back to single-instance backend. For higher rates use "
                "multiple ports.",
                num_instances, bdf,
            )
        else:
            LOG.info(
                "[DPDK-MULTI] N=1 on single device — delegating to single-instance "
                "backend (multi monitor's stat reporting is a placeholder)."
            )
        # Lazy import to avoid a circular dep with single-instance backend.
        try:
            from .dpdk_tx_worker import run_stream as _run_single
        except ImportError:
            from utils.dpdk_tx_worker import run_stream as _run_single
        return _run_single(stream_data, interface, stop_event, tracker)

    # Resolve binary
    bin_path = tx_worker_bin or _resolve_tx_worker_bin()
    if not bin_path or not os.path.exists(bin_path):
        LOG.error("[dpdk-multi] tx_worker binary not found at %s", bin_path)
        return 3
    
    # Calculate PPS per instance
    if target_pps == 0:
        pps_per_instance = 0  # Each instance runs at line rate
    else:
        pps_per_instance = max(1, target_pps // num_instances)
    
    # Distribute cores across instances
    base_corelist = dpdk_corelist or str(stream_data.get("dpdk_corelist") or _pick_corelist_on_node(numa))
    
    # Parse core list (e.g., "1-4" or "1,2,3,4")
    def parse_corelist(cl_str: str) -> List[int]:
        cores = []
        for part in str(cl_str).split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                cores.extend(range(start, end + 1))
            else:
                cores.append(int(part))
        return sorted(set(cores))
    
    available_cores = parse_corelist(base_corelist)
    if len(available_cores) < num_instances:
        LOG.warning(
            "[dpdk-multi] Only %d cores available but %d instances requested. "
            "Some instances will share cores (may reduce performance).",
            len(available_cores), num_instances
        )
        # Repeat cores if needed
        while len(available_cores) < num_instances:
            available_cores.extend(available_cores[:num_instances - len(available_cores)])
    
    # Distribute cores (round-robin or sequential)
    cores_per_instance = max(1, len(available_cores) // num_instances)
    instance_cores = []
    for i in range(num_instances):
        start_idx = i * cores_per_instance
        end_idx = min(start_idx + cores_per_instance, len(available_cores))
        instance_cores.append(available_cores[start_idx:end_idx])
    
    # Prepare common parameters
    vlan_id = fields["vlan_id"]
    frame_size = int(fields["frame_size"] or 64)
    no_udp_csum = bool(stream_data.get("no_udp_csum", False))
    mem_channels = str(stream_data.get("dpdk_mem_channels") or os.environ.get("DPDK_MEM_CHANNELS") or "4")
    duration_seconds = _resolve_duration_seconds(stream_data)
    
    # Launch instances
    processes = []
    instance_stop_events = []
    
    for i in range(num_instances):
        instance_id = f"{stream_id}_inst{i}"
        instance_stop = threading.Event()
        instance_stop_events.append(instance_stop)
        
        # Create instance-specific stream data
        instance_data = stream_data.copy()
        instance_data["stream_id"] = instance_id
        instance_data["stream_pps_rate"] = pps_per_instance
        
        # Build core list for this instance
        inst_cores = instance_cores[i]
        if len(inst_cores) == 1:
            inst_corelist = str(inst_cores[0])
        else:
            inst_corelist = f"{inst_cores[0]}-{inst_cores[-1]}"
        
        # Build command
        file_prefix = _file_prefix(instance_id, interface)
        cmd = [bin_path, "-l", inst_corelist, "-n", mem_channels, "--file-prefix", file_prefix]
        if bdf:
            cmd += ["-a", bdf]
        cmd += ["--",
                "--src-mac", fields["src_mac"], "--dst-mac", fields["dst_mac"],
                "--src-ip", str(fields["src_ip"]), "--dst-ip", str(fields["dst_ip"]),
                "--src-port", str(fields["udp_sport"]), "--dst-port", str(fields["udp_dport"]),
                "--size", str(frame_size), "--pps", str(pps_per_instance),
                "--stream-id", instance_id]
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
        
        # Launch process (delegate to single-instance runner or launch directly)
        # For now, we'll use a simplified approach - launch subprocess directly
        # In production, you might want to reuse the single-instance launcher
        try:
            import subprocess
            child_env = os.environ.copy()
            if env:
                child_env.update(env)
            
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                text=True,
                bufsize=1
            )
            processes.append({
                "process": proc,
                "instance_id": instance_id,
                "instance_num": i,
                "stop_event": instance_stop,
            })
            LOG.info("[dpdk-multi] Launched instance %d/%d: %s", i + 1, num_instances, instance_id)
        except Exception as e:
            LOG.error("[dpdk-multi] Failed to launch instance %d: %s", i, e)
            # Cleanup already launched instances
            for p in processes:
                try:
                    p["process"].terminate()
                except Exception:
                    pass
            return 4
    
    # Monitor all instances and aggregate statistics
    def monitor_instances():
        """Monitor all instances and update tracker"""
        last_tx_counts = {inst["instance_id"]: 0 for inst in processes}
        last_update_time = time.time()
        
        while not stop_event.is_set():
            try:
                # Aggregate TX counts from all instances
                total_tx = 0
                all_alive = True
                
                for inst in processes:
                    proc = inst["process"]
                    instance_id = inst["instance_id"]
                    
                    # Check if process is still running
                    if proc.poll() is not None:
                        all_alive = False
                        LOG.warning("[dpdk-multi] Instance %s exited with code %d", instance_id, proc.returncode)
                        continue
                    
                    # Get TX count (would need to parse stdout or use shared memory)
                    # For now, we'll update based on process status
                    # In production, you'd parse the tx_worker output or use a shared counter
                    tx_count = _safe_get_tx(tracker, interface, instance_id)
                    total_tx += tx_count
                
                # Update main stream tracker with aggregated count
                # Note: This is a simplified approach. In production, you might want
                # to track each instance separately and aggregate on-demand
                current_time = time.time()
                if current_time - last_update_time >= 1.0:  # Update every second
                    # Calculate rate
                    elapsed = current_time - last_update_time
                    # Update tracker (simplified - in production, aggregate properly)
                    tracker.update_tx_by_id(interface, stream_id, count=0)  # Placeholder
                    last_update_time = current_time
                
                if not all_alive:
                    LOG.warning("[dpdk-multi] Some instances have exited, stopping all")
                    stop_event.set()
                    break
                
                time.sleep(0.1)  # Check every 100ms
            except Exception as e:
                LOG.error("[dpdk-multi] Monitor error: %s", e)
                break
    
    # Start monitor thread
    monitor_thread = threading.Thread(target=monitor_instances, daemon=True)
    monitor_thread.start()
    
    # Wait for stop event or process completion
    try:
        while not stop_event.is_set():
            # Check if any process has exited
            for inst in processes:
                if inst["process"].poll() is not None:
                    LOG.warning("[dpdk-multi] Instance %s exited", inst["instance_id"])
                    stop_event.set()
                    break
            
            if stop_event.is_set():
                break
            
            time.sleep(0.5)
    except KeyboardInterrupt:
        LOG.info("[dpdk-multi] Interrupted, stopping all instances")
        stop_event.set()
    
    # Cleanup: stop all instances
    LOG.info("[dpdk-multi] Stopping all %d instance(s)", num_instances)
    for inst in processes:
        try:
            proc = inst["process"]
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    proc.kill()
        except Exception as e:
            LOG.warning("[dpdk-multi] Error stopping instance %s: %s", inst["instance_id"], e)
    
    # Wait for all processes to finish
    for inst in processes:
        try:
            inst["process"].wait(timeout=5.0)
        except Exception:
            pass
    
    LOG.info("[dpdk-multi] All instances stopped for stream '%s'", stream_name)
    return 0

