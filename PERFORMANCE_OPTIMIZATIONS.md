# Client Performance Optimization Plan

## Current Polling Activity

### Active Timers
1. **Main Statistics Timer** (main.py)
   - Interval: 3000ms (3 seconds)
   - Function: `fetch_and_update_statistics()`
   - Calls: `/api/interfaces`, `/api/streams/stats`

2. **Device Status Timer** (devices_tab.py)
   - Interval: 5000ms (5 seconds)
   - Function: `poll_device_status()`
   - Calls: `/api/device/arp/check` (per device), `/api/device/arp/request` (on failure)

3. **BGP Monitoring Timer** (devices_tab.py)
   - Interval: 5000ms (5 seconds)
   - Function: `periodic_bgp_status_check()`
   - Calls: `/api/bgp/status/{device_id}` (per device)

4. **Device Status Monitoring Timer** (devices_tab.py)
   - Interval: 5000ms (5 seconds)  
   - Function: `periodic_device_status_check()`
   - Calls: Same as device status timer (duplicate!)

## Performance Issues Identified

### 🔴 Critical Issues

1. **Duplicate Device Status Timers**
   - `status_timer` and `device_status_timer` both checking ARP
   - Running in parallel every 5 seconds
   - **Impact**: 2x ARP check requests

2. **Per-Device API Calls**
   - ARP check: One request per device
   - BGP status: One request per device
   - **Impact**: N devices = N requests every 5s

3. **No Request Batching**
   - Each device checked individually
   - **Impact**: Network latency multiplied by device count

4. **No Response Caching**
   - Same data requested multiple times
   - **Impact**: Redundant server processing

### 🟡 Medium Issues

5. **Fixed Polling Intervals**
   - All timers run at fixed intervals regardless of activity
   - **Impact**: Wasted cycles when nothing changes

6. **Redundant Table Updates**
   - BGP table updated even when no changes
   - **Impact**: Unnecessary UI redraws

## Optimization Recommendations

### 🚀 High Priority (Quick Wins)

#### 1. Remove Duplicate Timer ⚡ **~50% reduction in ARP requests**
```python
# Keep only one timer for device status
# Remove either status_timer or device_status_timer
```

#### 2. Batch ARP Checks ⚡ **~70% reduction in request count**
```python
# Create new endpoint: /api/devices/arp/check/batch
# Send all device IPs in one request
POST /api/devices/arp/check/batch
{
  "devices": [
    {"device_id": "...", "ip": "192.168.0.1"},
    {"device_id": "...", "ip": "192.168.0.2"},
  ]
}
```

#### 3. Batch BGP Status ⚡ **~70% reduction in request count**
```python
# Create new endpoint: /api/bgp/status/batch
POST /api/bgp/status/batch
{
  "device_ids": ["id1", "id2", "id3"]
}
```

#### 4. Increase Polling Intervals ⚡ **Immediate improvement**
```python
# Current:
device_status_timer.start(5000)  # 5 seconds
bgp_monitoring_timer.start(5000)  # 5 seconds

# Optimized:
device_status_timer.start(10000)  # 10 seconds (ARP doesn't change that fast)
bgp_monitoring_timer.start(10000)  # 10 seconds (BGP is slow to establish)
statistics_timer.start(5000)  # Keep at 5s for responsive stats
```

### 🎯 Medium Priority

#### 5. Conditional Polling
```python
# Only poll when devices are actually running
if running_device_count == 0:
    # Slow down or stop polling
    timer.setInterval(30000)  # 30 seconds
```

#### 6. Response Caching
```python
# Cache ARP responses for 2-3 seconds
# Reuse cached data if requested again within cache period
```

#### 7. Async/Threaded Requests
```python
# Use QThread for network requests
# Don't block UI while waiting for responses
```

### 💡 Low Priority (Advanced)

#### 8. WebSocket for Real-time Updates
```python
# Replace polling with WebSocket push notifications
# Server pushes updates only when state changes
```

#### 9. Lazy Table Updates
```python
# Only update visible rows in large tables
# Defer updates for off-screen rows
```

## Immediate Quick Fixes (Apply Now)

### Fix 1: Remove Duplicate Timer
```python
# In DevicesTab.__init__, remove device_status_timer
# Keep only status_timer (the original one)
```

### Fix 2: Increase Polling Intervals
```python
# Device status: 5s → 10s
self.status_timer.start(10000)

# BGP monitoring: 5s → 15s (BGP is slow)
self.bgp_monitoring_timer.start(15000)

# Stats: keep at 3s (user expects fast updates)
self.timer.start(3000)
```

### Fix 3: Smart Polling
```python
# Adjust interval based on activity
if running_devices == 0:
    self.status_timer.setInterval(30000)  # Slow poll
else:
    self.status_timer.setInterval(10000)  # Normal poll
```

## Expected Performance Improvements

| Optimization | Request Reduction | Perceived Speed |
|--------------|-------------------|-----------------|
| Remove duplicate timer | -50% ARP requests | ✅ Moderate |
| Increase intervals (5s→10s) | -50% all requests | ✅ Moderate |
| Batch ARP checks | -70% ARP requests | ✅✅ Significant |
| Batch BGP status | -70% BGP requests | ✅✅ Significant |
| **Combined** | **-80-90% total** | ✅✅✅ **Major** |

## Implementation Priority

1. **Immediate** (1 minute):
   - ✅ Remove duplicate timer
   - ✅ Increase polling intervals

2. **Short-term** (30 minutes):
   - ⏳ Implement batch ARP endpoint
   - ⏳ Implement batch BGP endpoint

3. **Long-term** (2-4 hours):
   - ⏳ Add response caching
   - ⏳ Implement WebSocket updates
   - ⏳ Async request handling

## Recommendation

Start with **Immediate fixes** - they require minimal code changes but provide ~50-75% improvement in responsiveness!



