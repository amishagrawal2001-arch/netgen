# DPDK Stream Troubleshooting Guide

If your DPDK-enabled stream is not working, follow this troubleshooting guide to identify and fix the issue.

## Quick Diagnosis Steps

### 1. Check Server Logs

The most important step is to check the server logs for detailed error messages:

```bash
# On the server, check recent logs
journalctl -u ostg-server -n 100 --no-pager | grep -i dpdk

# Or check the full log file if available
tail -100 /var/log/ostg-server.log | grep -i dpdk
```

Look for error messages like:
- `[dpdk] tx_worker binary not found`
- `[dpdk] missing required fields`
- `[dpdk] stream failed with exit code X`
- `EAL: Cannot init memory`
- `EAL: No available hugepages`

### 2. Verify DPDK Prerequisites

Use the client UI to verify all prerequisites:

**DPDK Menu → Verify Installation**

Should show:
- ✅ DPDK Libraries: ✓
- ✅ DPDK Packet Generator (tx_worker): ✓
- ✅ Hugepages: ✓ (configured)
- ✅ Kernel Modules: ✓ (loaded)

**DPDK Menu → Status**

Should show:
- ✅ IOMMU Status: ✓ Enabled (for Broadcom/Intel/AMD NICs)
- ✅ VFIO Modules: ✓ Loaded (for Broadcom/Intel/AMD NICs)
- ✅ Interface bound to DPDK (if using vfio-pci)

### 3. Common Error Codes

When `tx_worker` fails, it returns an exit code. Here's what they mean:

| Exit Code | Meaning | Solution |
|-----------|---------|----------|
| **1** | General error (check logs) | See error messages in server logs |
| **2** | Missing required fields | Ensure MAC addresses and IP addresses are configured |
| **3** | tx_worker binary not found | Verify `tx_worker` is built and accessible |
| **128+** | Signal termination | Process was killed (check system resources) |

---

## Common Issues and Solutions

### Issue 1: "tx_worker binary not found"

**Symptoms:**
- Log shows: `[dpdk] tx_worker binary not found at /path/to/tx_worker`
- Exit code: 3

**Solution:**
1. Verify `tx_worker` is built:
   ```bash
   ls -la /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
   ```

2. If missing, build it:
   ```bash
   cd /opt/OSTG/resources/dpdk
   ./dpdk_tx_worker.sh --rewrite-src
   ```

3. Verify it's executable:
   ```bash
   chmod +x /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
   ```

---

### Issue 2: "Missing required fields"

**Symptoms:**
- Log shows: `[dpdk] missing required fields: ['src_mac', 'dst_mac', ...]`
- Exit code: 2

**Solution:**
1. Ensure stream has all required fields:
   - Source MAC address
   - Destination MAC address
   - Source IP address
   - Destination IP address

2. Check stream configuration in UI:
   - Go to stream dialog
   - Verify L2 (MAC) and L3 (IP) fields are filled
   - Save and try again

---

### Issue 3: "EAL: Cannot init memory" or "No available hugepages"

**Symptoms:**
- Log shows: `EAL: Cannot init memory` or `EAL: No available hugepages`
- Process exits immediately

**Solution:**
1. Configure hugepages:
   ```bash
   # Check current hugepages
   cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
   
   # Configure hugepages (e.g., 4096 pages = 8GB)
   echo 4096 | sudo tee /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
   
   # Verify
   grep HugePages /proc/meminfo
   ```

2. Or use the client UI:
   - DPDK Menu → Configure Hugepages
   - Enter number of pages (e.g., 4096)
   - Click OK

---

### Issue 4: "EAL: Cannot find device" or "No DPDK ports"

**Symptoms:**
- Log shows: `EAL: Cannot find device` or `No DPDK ports`
- Process exits immediately

**Solution:**
1. **For Broadcom/Intel/AMD NICs:**
   - Verify interface is bound to DPDK:
     ```bash
     dpdk-devbind.py --status
     ```
   - Should show interface bound to `vfio-pci`
   - If not bound, use client UI: DPDK Menu → Bind Interface to DPDK

2. **For NVIDIA/Mellanox NICs:**
   - Interface should be bound to `mlx5_core` (kernel driver)
   - DPDK will use the kernel driver (no vfio-pci needed)
   - Verify: `dpdk-devbind.py --status` should show `mlx5_core`

3. **Check PCI address:**
   - Verify interface name matches:
     ```bash
     # Get PCI address from interface
     readlink -f /sys/class/net/<interface>/device
     # Should match what tx_worker is trying to use
     ```

---

### Issue 5: "EAL: Cannot open /dev/vfio" or VFIO errors

**Symptoms:**
- Log shows: `EAL: Cannot open /dev/vfio` or VFIO-related errors
- Process exits immediately

**Solution:**
1. **Load VFIO modules:**
   ```bash
   sudo modprobe vfio
   sudo modprobe vfio-pci
   ```

2. **Or use client UI:**
   - DPDK Menu → Load VFIO Modules

3. **Verify modules loaded:**
   ```bash
   lsmod | grep vfio
   # Should show: vfio_pci, vfio_iommu_type1, vfio
   ```

4. **Check IOMMU:**
   - IOMMU must be enabled for vfio-pci
   - Use client UI: DPDK Menu → Configure IOMMU
   - Or check: `dmesg | grep -i iommu`

---

### Issue 6: "Permission denied" or "Cannot access device"

**Symptoms:**
- Log shows permission errors
- Process exits immediately

**Solution:**
1. **Ensure server runs as root:**
   ```bash
   # Check service user
   systemctl show ostg-server | grep User
   # Should be: User=root
   ```

2. **Check file permissions:**
   ```bash
   ls -la /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
   # Should be executable: -rwxr-xr-x
   ```

3. **Check device permissions:**
   ```bash
   # For vfio-pci devices
   ls -la /dev/vfio/
   # Should be accessible by root
   ```

---

### Issue 7: Stream starts but no packets transmitted

**Symptoms:**
- Stream appears to start successfully
- No TX statistics (packets sent = 0)
- No traffic on wire

**Solution:**
1. **Check if tx_worker is actually running:**
   ```bash
   ps aux | grep tx_worker
   # Should show running process
   ```

2. **Check for rate limiting:**
   - If PPS is set to 0, it means "line rate" (flood)
   - If PPS is very low, packets may be sent slowly
   - Check stream configuration in UI

3. **Check interface status:**
   ```bash
   # For vfio-pci bound interfaces, they won't show in ip link
   # But check if DPDK can see them:
   dpdk-devbind.py --status
   ```

4. **Check for errors in logs:**
   - Look for "drop=" counts in STAT lines
   - High drop counts indicate transmission issues

---

### Issue 8: "Process exited immediately with code X (no output)"

**Symptoms:**
- Log shows: `[dpdk] tx_worker exited immediately with code X (no output)`
- No error messages captured

**Solution:**
1. **Try running tx_worker manually:**
   ```bash
   # Get the command from logs (look for "[dpdk] exec:")
   # Then run it manually to see errors
   sudo /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker -l 0-1 -n 4 -a <PCI_BDF> -- --src-mac ... --dst-mac ...
   ```

2. **Check for missing libraries:**
   ```bash
   ldd /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
   # Should show all libraries resolved
   ```

3. **Check DPDK installation:**
   ```bash
   pkg-config --modversion libdpdk
   # Should show DPDK version
   ```

---

## Diagnostic Commands

Run these commands on the server to gather diagnostic information:

```bash
# 1. Check DPDK installation
pkg-config --modversion libdpdk
dpdk-devbind.py --status

# 2. Check hugepages
grep HugePages /proc/meminfo
cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# 3. Check VFIO modules
lsmod | grep vfio
ls -la /dev/vfio/

# 4. Check IOMMU
dmesg | grep -i iommu | tail -5

# 5. Check tx_worker binary
ls -la /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
file /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker
ldd /opt/OSTG/resources/dpdk/tx_worker/build/tx_worker

# 6. Check server logs
journalctl -u ostg-server -n 100 --no-pager | grep -i dpdk
```

---

## Getting Help

If you're still experiencing issues after following this guide:

1. **Collect diagnostic information:**
   - Run all diagnostic commands above
   - Capture server logs: `journalctl -u ostg-server -n 200 > server_logs.txt`
   - Note the exact error messages

2. **Check stream configuration:**
   - Verify all required fields are set
   - Note the interface name and PCI address
   - Note the target PPS/rate

3. **Provide information:**
   - Server OS and kernel version: `uname -a`
   - DPDK version: `pkg-config --modversion libdpdk`
   - NIC vendor/model: `lspci | grep -i network`
   - Error messages from logs

---

## Prevention Checklist

Before starting a DPDK stream, verify:

- [ ] DPDK libraries installed
- [ ] tx_worker binary built and executable
- [ ] Hugepages configured (4096+ pages recommended)
- [ ] VFIO modules loaded (for Broadcom/Intel/AMD)
- [ ] IOMMU enabled (for Broadcom/Intel/AMD)
- [ ] Interface bound to DPDK (for Broadcom/Intel/AMD)
- [ ] Stream has all required fields (MAC, IP addresses)
- [ ] Server running as root (for device access)

Use the client UI "DPDK → Verify Installation" and "DPDK → Status" to check most of these automatically.




