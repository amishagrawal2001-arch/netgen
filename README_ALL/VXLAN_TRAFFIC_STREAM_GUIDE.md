# How to Send Traffic Streams Over VXLAN Tunnel

## Overview

To send traffic streams over a VXLAN tunnel in OSTG, you need to:
1. Configure VXLAN on a device (creates bridge interface)
2. Send traffic streams to the **bridge interface** (not the VXLAN interface directly)
3. The bridge automatically encapsulates traffic and sends it through the VXLAN tunnel

## Step-by-Step Guide

### Step 1: Configure VXLAN on Device

First, configure VXLAN when creating or editing a device. This creates:
- A VXLAN interface (e.g., `vx5000-abc123`)
- A bridge interface (e.g., `br5000`)
- EVPN configuration in FRR

**Via UI:**
1. Go to **Devices** tab
2. Add or edit a device
3. Enable **VXLAN** protocol
4. Configure:
   - **VNI**: VXLAN Network Identifier (e.g., `5000`)
   - **Local IP**: VTEP source IP (e.g., `192.255.0.22`)
   - **Remote Peers**: Remote VTEP IPs (comma-separated, e.g., `192.255.0.23,192.255.0.24`)
   - **Underlay Interface**: Physical interface for underlay (e.g., `ens4np0`)
   - **Bridge SVI IP**: Optional IP for bridge interface (e.g., `192.255.0.100/24`)

**Via API:**
```bash
curl -X POST http://svl-hp-ai-srv04:5051/api/device/vxlan/configure \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device-001",
    "device_name": "VXLAN-Device",
    "interface": "ens4np0",
    "vxlan_config": {
      "vni": 5000,
      "local_ip": "192.255.0.22",
      "remote_peers": ["192.255.0.23"],
      "underlay_interface": "ens4np0",
      "bridge_svi_ip": "192.255.0.100",
      "bridge_svi_subnet": "24"
    }
  }'
```

### Step 2: Find the Bridge Interface Name

After VXLAN is configured, find the bridge interface name:

**Method 1: Check FRR Container**
```bash
# SSH to server
ssh root@svl-hp-ai-srv04

# List FRR containers
docker ps | grep frr

# Enter the container
docker exec -it <container_name> bash

# Check bridge interfaces
ip link show | grep br

# Or check bridge FDB
bridge fdb show br br5000
```

**Method 2: Check Device Database**
The bridge interface name is typically `br{vni}` (e.g., `br5000` for VNI 5000).

**Method 3: Via API**
```bash
curl -G http://svl-hp-ai-srv04:5051/api/device/info \
  --data-urlencode "device_id=device-001"
```

Look for `vxlan_config` → `bridge_interface` or `overlay_interface` field.

### Step 3: Create Traffic Stream to Bridge Interface

**Important:** Send traffic to the **bridge interface** (e.g., `br5000`), NOT the VXLAN interface (e.g., `vx5000-abc123`).

**Via UI:**
1. Go to **Streams** tab
2. Click **Add Stream**
3. Select the device with VXLAN configured
4. **Interface**: Select or enter the bridge interface name (e.g., `br5000`)
5. Configure stream parameters:
   - **Source MAC**: MAC address in overlay network
   - **Destination MAC**: Remote MAC or broadcast `ff:ff:ff:ff:ff:ff`
   - **Source IP**: IP in overlay network (e.g., `192.255.0.100`)
   - **Destination IP**: Remote endpoint IP (e.g., `192.255.0.200`)
   - **Frame Size**: Desired packet size
   - **Rate**: PPS, bps, or line rate
6. Click **Start**

**Via API:**
```bash
curl -X POST http://svl-hp-ai-srv04:5051/api/traffic/start \
  -H "Content-Type: application/json" \
  -d '{
    "streams": {
      "TG 0 - Port: br5000": [
        {
          "name": "VXLAN_Stream",
          "enabled": true,
          "frame_size": 512,
          "mac_source_address": "00:11:22:33:44:55",
          "mac_destination_address": "66:77:88:99:aa:bb",
          "ipv4_source": "192.255.0.100",
          "ipv4_destination": "192.255.0.200",
          "L4": "UDP",
          "udp_source_port": 12345,
          "udp_destination_port": 54321,
          "stream_rate_type": "Packets Per Second (PPS)",
          "stream_pps_rate": 1000,
          "stream_duration_mode": "Continuous",
          "flow_tracking_enabled": true,
          "stream_id": "vxlan-stream-001"
        }
      ]
    }
  }'
```

**Key Points:**
- **Interface**: Use bridge name `br5000` (not `vx5000-abc123`)
- **IP Addresses**: Use overlay network IPs (not underlay IPs)
- **MAC Addresses**: Use overlay MAC addresses
- **VLAN**: If VLAN-aware VXLAN is configured, include VLAN ID in stream

### Step 4: Verify Traffic is Being Sent

**Check Bridge FDB:**
```bash
# Inside FRR container
bridge fdb show br br5000
```

**Check VXLAN Encapsulation:**
```bash
# On server (underlay interface)
tcpdump -i ens4np0 -n "udp port 4789"
```

You should see VXLAN-encapsulated packets with:
- Outer IP: Local VTEP IP → Remote VTEP IP
- UDP port: 4789 (default VXLAN port)
- Inner Ethernet frame: Your stream traffic

**Check EVPN Routes:**
```bash
# Inside FRR container
vtysh -c "show bgp l2vpn evpn route"
vtysh -c "show evpn vni detail"
```

## Example: Complete VXLAN Traffic Flow

### 1. Device Configuration
```json
{
  "device_id": "vxlan-device-1",
  "device_name": "VTEP-1",
  "interface": "ens4np0",
  "ipv4": "192.170.0.2",
  "vlan": "22",
  "vxlan_config": {
    "vni": 5000,
    "local_ip": "192.255.0.22",
    "remote_peers": ["192.255.0.23"],
    "underlay_interface": "ens4np0",
    "bridge_svi_ip": "192.255.0.100",
    "bridge_svi_subnet": "24"
  }
}
```

### 2. Stream Configuration
```json
{
  "name": "VXLAN_Test_Stream",
  "interface": "br5000",
  "mac_source_address": "00:aa:bb:cc:dd:ee",
  "mac_destination_address": "00:11:22:33:44:55",
  "ipv4_source": "192.255.0.100",
  "ipv4_destination": "192.255.0.200",
  "frame_size": 512,
  "stream_rate_type": "Packets Per Second (PPS)",
  "stream_pps_rate": 1000
}
```

### 3. Traffic Flow
```
Stream Packet (br5000)
  ↓
Bridge Interface (br5000)
  ↓
VXLAN Encapsulation
  ↓
VXLAN Interface (vx5000-abc123)
  ↓
Underlay Interface (ens4np0)
  ↓
Network → Remote VTEP
```

## Troubleshooting

### Issue: Stream not sending traffic
**Solution:**
- Verify bridge interface exists: `ip link show br5000`
- Check bridge is up: `ip link set br5000 up`
- Verify VXLAN interface is attached to bridge: `bridge link show`

### Issue: Traffic not reaching remote endpoint
**Solution:**
- Verify underlay connectivity: `ping <remote_vtep_ip>`
- Check VXLAN encapsulation: `tcpdump -i <underlay> udp port 4789`
- Verify remote VTEP is configured correctly
- Check EVPN routes: `vtysh -c "show bgp l2vpn evpn route"`

### Issue: MAC addresses not learned
**Solution:**
- Ensure MAC learning is enabled (default)
- Check bridge FDB: `bridge fdb show br br5000`
- Verify traffic is flowing: `bridge fdb show dev vx5000-abc123`

### Issue: Wrong interface name
**Solution:**
- Use bridge interface (`br5000`), not VXLAN interface (`vx5000-abc123`)
- Bridge interface name format: `br{vni}`
- Check actual interface name in FRR container: `ip link show`

## Best Practices

1. **Always use bridge interface** for sending traffic
2. **Use overlay network IPs** in stream configuration
3. **Enable flow tracking** to monitor packet delivery
4. **Verify underlay connectivity** before configuring VXLAN
5. **Check EVPN routes** to ensure proper MAC learning
6. **Monitor bridge FDB** to see learned MAC addresses

## Additional Resources

- VXLAN Configuration: See device VXLAN configuration dialog
- EVPN Monitoring: Use `vtysh` commands inside FRR container
- Bridge Management: Use `bridge` and `ip` commands
- Traffic Monitoring: Use `tcpdump` on underlay interface

