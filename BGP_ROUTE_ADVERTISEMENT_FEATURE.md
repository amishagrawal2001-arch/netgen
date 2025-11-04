# BGP Route Advertisement Feature

## Overview

Two-step process for managing BGP route advertisements:
1. **Step 1**: Define route pools (reusable across devices)
2. **Step 2**: Attach pools to specific devices

---

## UI Components

### New Buttons in Devices Tab

| Button | Icon | Tooltip | Action |
|--------|------|---------|--------|
| **Manage Route Pools** | 🗂️ | Manage BGP Route Pools | Opens dialog to define/edit route pools |
| **Attach Route Pools** | 📍 | Attach Route Pools to Device | Opens dialog to assign pools to selected device |

---

## Step 1: Manage Route Pools

**Button**: 🗂️ "Manage BGP Route Pools"

### Dialog: `ManageRoutePoolsDialog`

```
┌──────────────────────────────────────────────────────────┐
│ Manage BGP Route Pools                                   │
├──────────────────────────────────────────────────────────┤
│ Define reusable route pools that can be attached to      │
│ devices. Each pool can generate multiple routes.         │
│                                                           │
│ Create New Route Pool                                    │
│ ┌────────────────────────────────────────────────────┐  │
│ │ Pool Name:    customer_routes_1                    │  │
│ │ Network Subnet: 10.0.0.0/24                        │  │
│ │ Route Count:    1000                               │  │
│ │                                [Add Pool]           │  │
│ └────────────────────────────────────────────────────┘  │
│                                                           │
│ Defined Route Pools                                      │
│ ┌────────────────────────────────────────────────────┐  │
│ │ Pool Name       │ Subnet       │ Count │ Actions   │  │
│ │ customer_routes │ 10.0.0.0/24  │ 1000  │ [Remove]  │  │
│ │ test_pool       │ 192.168.0.0/16│ 5000 │ [Remove]  │  │
│ │ ipv6_pool       │ 2001:db8::/64│ 500   │ [Remove]  │  │
│ └────────────────────────────────────────────────────┘  │
│                                                           │
│                        [Save Pools]  [Cancel]            │
└──────────────────────────────────────────────────────────┘
```

### Features

- **Pool Name**: Unique identifier for the route pool
- **Network Subnet**: Base network (IPv4 or IPv6)
- **Route Count**: Number of routes to generate
- **Validation**: Checks for duplicate names and valid subnets
- **Persistence**: Saved to `session.json`

### Use Cases

1. **Single Network**
   - Name: `default_routes`
   - Subnet: `192.168.0.0/24`
   - Count: `1`

2. **Large Scale Testing**
   - Name: `stress_test`
   - Subnet: `100.0.0.0/8`
   - Count: `10000`

3. **Multiple Pools**
   - Pool 1: `customer_a` - `10.1.0.0/16` - 1000 routes
   - Pool 2: `customer_b` - `10.2.0.0/16` - 2000 routes
   - Pool 3: `ipv6_routes` - `2001:db8::/48` - 500 routes

---

## Step 2: Attach Route Pools

**Button**: 📍 "Attach Route Pools to Device"

### Dialog: `AttachRoutePoolsDialog`

```
┌──────────────────────────────────────────────────────────┐
│ Attach Route Pools - device1                             │
├──────────────────────────────────────────────────────────┤
│ Select which route pools to advertise from device: device1│
│                                                           │
│ Available Route Pools                                    │
│ ┌────────────────────────────────────────────────────┐  │
│ │ ☑ customer_routes - 10.0.0.0/24 (1000 routes)      │  │
│ │ ☐ test_pool - 192.168.0.0/16 (5000 routes)         │  │
│ │ ☑ ipv6_pool - 2001:db8::/64 (500 routes)           │  │
│ └────────────────────────────────────────────────────┘  │
│                                                           │
│ ✅ Selected 2 pool(s) → Total 1500 routes to advertise   │
│                                                           │
│                          [Apply]  [Cancel]               │
└──────────────────────────────────────────────────────────┘
```

### Features

- **Checkbox List**: All available pools with details
- **Multi-Select**: Can attach multiple pools to one device
- **Live Summary**: Shows total route count
- **Validation**: Only allows if BGP is enabled on device
- **Persistence**: Saved to `session.json`

---

## Data Structure

### Session.json Structure

```json
{
  "bgp_route_pools": [
    {
      "name": "customer_routes",
      "subnet": "10.0.0.0/24",
      "count": 1000
    },
    {
      "name": "ipv6_pool",
      "subnet": "2001:db8::/64",
      "count": 500
    }
  ],
  "TG 0 - Port: ens5f1np1": [
    {
      "Device Name": "device1",
      "protocols": {
        "BGP": {
          "bgp_asn": "65000",
          "bgp_remote_asn": "65001",
          ...
        }
      },
      "bgp_route_pools": [
        "customer_routes",
        "ipv6_pool"
      ]
    }
  ]
}
```

### Device Data

```python
device_info = {
    "Device Name": "device1",
    "protocols": {
        "BGP": { ... }
    },
    "bgp_route_pools": ["customer_routes", "ipv6_pool"]  # Pool names (not full data)
}
```

### Main Window Data

```python
main_window.bgp_route_pools = [
    {"name": "customer_routes", "subnet": "10.0.0.0/24", "count": 1000},
    {"name": "ipv6_pool", "subnet": "2001:db8::/64", "count": 500}
]
```

---

## Workflow

### Typical Usage

```
1. User clicks 🗂️ "Manage Route Pools"
   ↓
2. Creates pools:
   - "customer_a": 10.0.0.0/16, 1000 routes
   - "customer_b": 10.1.0.0/16, 2000 routes
   ↓
3. Pools saved to session.json
   ↓
4. User selects a device with BGP
   ↓
5. User clicks 📍 "Attach Route Pools"
   ↓
6. Selects which pools to advertise from this device
   - ☑ customer_a
   - ☐ customer_b
   ↓
7. Device saved with attached pool names
   ↓
8. User clicks "Apply" to configure on server
   ↓
9. Server reads device.bgp_route_pools
   ↓
10. Server looks up pools in main_window.bgp_route_pools
   ↓
11. Server generates and advertises routes in FRR
```

### Reusability

```
Scenario: 3 devices, 2 route pools

Route Pools (Global):
- pool_A: 10.0.0.0/24, 100 routes
- pool_B: 192.168.0.0/16, 1000 routes

Device Assignments:
- device1: [pool_A, pool_B] → Advertises 1100 routes
- device2: [pool_A] → Advertises 100 routes  
- device3: [pool_B] → Advertises 1000 routes

Total routes advertised: 2200
Without reuse, would need to define 6 times!
```

---

## Server-Side Implementation (TODO)

### When Device is Applied/Started

```python
# In /api/device/bgp/configure or similar

# 1. Get device's attached pool names
attached_pool_names = device_data.get("bgp_route_pools", [])

# 2. Get global route pools from session
all_pools = session_data.get("bgp_route_pools", [])

# 3. For each attached pool
for pool_name in attached_pool_names:
    # Find the pool definition
    pool = next((p for p in all_pools if p["name"] == pool_name), None)
    if not pool:
        continue
    
    subnet = pool["subnet"]
    count = pool["count"]
    
    # 4. Generate routes and advertise in FRR
    # Example for single network:
    vtysh -c 'router bgp 65000' -c 'network 10.0.0.0/24'
    
    # For multiple routes, generate from subnet:
    # 10.0.0.0/24 with count=256 → advertise 10.0.0.0/32, 10.0.0.1/32, ...
```

### FRR Commands

```bash
# Single network
vtysh -c 'configure terminal' \
      -c 'router bgp 65000' \
      -c 'network 10.0.0.0/24' \
      -c 'end' \
      -c 'write memory'

# Multiple /32 routes from subnet
vtysh -c 'configure terminal' \
      -c 'router bgp 65000' \
      -c 'network 10.0.0.0/32' \
      -c 'network 10.0.0.1/32' \
      -c 'network 10.0.0.2/32' \
      ...
      -c 'end' \
      -c 'write memory'
```

---

## Files

| File | Purpose | Lines |
|------|---------|-------|
| `widgets/add_bgp_route_dialog.py` | Both dialogs (Manage + Attach) | ~250 |
| `widgets/devices_tab.py` | Button integration + functions | +100 |
| `session.json` | Persistent storage | N/A |

---

## Benefits

### Reusability
- ✅ Define once, use many times
- ✅ Same pool attached to multiple devices
- ✅ Easy bulk configuration

### Maintainability
- ✅ Change pool definition → affects all devices using it
- ✅ Clear separation: pools vs assignments
- ✅ Easy to understand and manage

### Flexibility
- ✅ Mix and match pools per device
- ✅ Different devices can advertise different routes
- ✅ Easy to test different scenarios

### Performance
- ✅ Generate routes on-demand
- ✅ Don't store all generated routes (just count)
- ✅ Memory efficient for large route counts

---

## Testing

### Test Scenario 1: Single Pool, Single Device

```
1. Click 🗂️ → Add pool "test1": 10.0.0.0/24, count=1
2. Click 📍 on device1 → Attach "test1"
3. Click Apply
4. Verify: device1 advertises 10.0.0.0/24
```

### Test Scenario 2: Multiple Pools, Multiple Devices

```
1. Click 🗂️ → Add pools:
   - "pool_A": 10.0.0.0/16, 1000
   - "pool_B": 192.168.0.0/16, 5000

2. Click 📍 on device1 → Attach "pool_A"
3. Click 📍 on device2 → Attach "pool_A" + "pool_B"
4. Click Apply

Result:
- device1 advertises: 1000 routes (from pool_A)
- device2 advertises: 6000 routes (from pool_A + pool_B)
```

### Test Scenario 3: Large Scale

```
1. Click 🗂️ → Add pool "stress": 100.0.0.0/8, 100000
2. Click 📍 on device1 → Attach "stress"
3. Click Apply
4. Verify: device1 advertises 100,000 routes
```

---

## Next Steps

### Server-Side (TODO)

1. ✅ Read `bgp_route_pools` from device data
2. ✅ Lookup pool definitions from global pools
3. ⏳ Generate routes from subnet + count
4. ⏳ Add BGP network statements to FRR
5. ⏳ Handle IPv4 and IPv6 subnets
6. ⏳ Optimize for large route counts (batch commands)

### Client-Side (Complete)

1. ✅ Manage Route Pools dialog
2. ✅ Attach Route Pools dialog
3. ✅ Save pools to session.json
4. ✅ Two-button workflow
5. ✅ Validation and error handling

---

## Summary

✅ **Two-step workflow**: Define pools, then attach to devices
✅ **Reusable pools**: Define once, use many times
✅ **Saved to session**: Persistent across restarts
✅ **Flexible**: Mix and match pools per device
✅ **Scalable**: Support for 100,000+ routes per pool

**Client-side implementation complete. Server-side route generation coming next!** 🎉


