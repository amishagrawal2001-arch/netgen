# OSTG Traffic Generator — API Guide

This document describes the REST API provided by the OSTG (OSTG Traffic Generator) server. The server runs by default on **port 5051**.

**Base URL:** `http://<server-host>:5051`  
*(If the server was started with `PORT=5050` or `--port 5050`, use port 5050 instead. See [SERVER_STARTUP_MESSAGES.md](SERVER_STARTUP_MESSAGES.md) for startup logs and port details.)*

All API responses are JSON unless noted. Use `Content-Type: application/json` for POST/PUT requests.

---

## Table of Contents

1. [Health & Status](#1-health--status)
2. [Traffic & Streams](#2-traffic--streams)
3. [Device Lifecycle](#3-device-lifecycle)
4. [Device Configuration](#4-device-configuration)
5. [BGP](#5-bgp)
6. [OSPF](#6-ospf)
7. [ISIS](#7-isis)
8. [FRR](#8-frr)
9. [DHCP](#9-dhcp)
10. [ARP & Ping](#10-arp--ping)
11. [Capture & PCAP](#11-capture--pcap)
12. [Interfaces](#12-interfaces)
13. [Device Database](#13-device-database)
14. [Pools (BGP / OSPF / DHCP)](#14-pools-bgp--ospf--dhcp)
15. [DPDK](#15-dpdk)
16. [AI & Assistant](#16-ai--assistant)
17. [Debug & Utilities](#17-debug--utilities)

---

## 1. Health & Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ping` | Simple liveness check. |
| GET | `/health` | Returns `"Online"` (text). |
| GET | `/healthz` | Returns `{"status": "ok"}` (JSON). |

**Example:**
```bash
curl http://localhost:5051/api/ping
# {"status": "ok"}

curl http://localhost:5051/healthz
# {"status": "ok"}
```

---

## 2. Traffic & Streams

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/traffic/start` | Start traffic streams. |
| POST | `/api/traffic/stop` | Stop traffic streams. |
| POST | `/api/traffic/restart` | Restart traffic. |
| POST | `/api/traffic/rx_monitor` | Start/control RX monitoring. |
| GET | `/api/streams/save` | Save stream session to file. |
| GET | `/api/streams/load` | Load stream session from file. |
| GET | `/api/streams/stats` | Get stream statistics. Query: `tg_id`, `status`. |
| POST | `/api/streams/register` | Register streams. |
| POST | `/api/streams/update` | Update stream configuration. |

**Start traffic (POST `/api/traffic/start`):**
- **Body:** `{ "streams": { "<interface_label>": [ { "name": "...", "enabled": true, ... } ] } }`
- Stream list can include protocol options, RX port, flow tracking, etc.

**Stop traffic (POST `/api/traffic/stop`):**
- **Body:** `{ "streams": { "<interface_label>": [ { "name": "..." } ] } }` or similar structure used by the client.

---

## 3. Device Lifecycle

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/device/start` | Start a device (create container, apply basic config). |
| POST | `/api/device/stop` | Stop a device. |
| POST | `/api/device/apply` | Apply full device configuration (interfaces, protocols). |
| POST | `/api/device/remove` | Remove a device. |
| POST | `/api/device/cleanup` | Clean up device resources. |
| POST | `/api/device/check` | Check device connectivity/status. |
| POST | `/api/device/add-external` | Add an external device. |
| GET | `/api/device/external/status/<device_id>` | Get external device status. |
| POST | `/api/device/external/execute` | Execute command on external device. |
| GET | `/api/device/external/config/<device_id>` | Get external device config. |

**Start device (POST `/api/device/start`):**
- **Body:** `device_id`, `device_name`, `interface`, `ipv4`, `ipv6`, `ipv4_mask`, `ipv6_mask`, `vlan`, etc.

**Apply device (POST `/api/device/apply`):**
- **Body:** `device_id`, `device_name`, `interface`, `vlan`, `mtu`, `ipv4`, `ipv6`, `ipv4_mask`, `ipv6_mask`, `ipv4_gateway`, `ipv6_gateway`, `loopback_ipv4`, `loopback_ipv6`, `protocols` (e.g. BGP, OSPF, ISIS, DHCP, VXLAN), `bgp_config`, `dhcp_config`, `vxlan_config`, etc.

---

## 4. Device Configuration

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/device/bgp/configure` | Configure BGP for a device. |
| POST | `/api/device/bgp/start` | Start BGP on device. |
| POST | `/api/device/bgp/stop` | Stop BGP on device. |
| POST | `/api/device/ospf/configure` | Configure OSPF for a device. |
| POST | `/api/device/ospf/start` | Start OSPF on device. |
| POST | `/api/device/ospf/stop` | Stop OSPF on device. |
| POST | `/api/device/isis/configure` | Configure ISIS for a device. |
| POST | `/api/device/isis/start` | Start ISIS on device. |
| POST | `/api/device/isis/stop` | Stop ISIS on device. |
| POST | `/api/device/frr/start` | Start FRR container for device. |
| POST | `/api/device/frr/stop` | Stop FRR container. |
| POST | `/api/device/vxlan/remove` | Remove VXLAN configuration. |
| GET | `/api/device/frr/status/<device_id>` | FRR status for device. |
| GET | `/api/device/frr/neighbors/<device_id>` | FRR neighbors. |
| GET | `/api/device/frr/routes/<device_id>` | FRR routes. |
| GET | `/api/device/dhcp/status` | DHCP status. |
| POST | `/api/device/dhcp/server/pool` | Create/update DHCP server pool. |
| POST | `/api/device/dhcp/server/attach_pools` | Attach DHCP pools to device. |

---

## 5. BGP

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bgp/status` | Overall BGP status. |
| GET | `/api/bgp/status/<device_id>` | BGP status for a device. |
| POST | `/api/bgp/status/batch` | BGP status for multiple devices. |
| GET | `/api/bgp/neighbors` | BGP neighbors. |
| GET | `/api/bgp/routes` | BGP routes. |
| POST | `/api/bgp/routes/advertise` | Advertise BGP routes. |
| POST | `/api/bgp/routes/withdraw` | Withdraw BGP routes. |
| POST | `/api/bgp/routes/generate` | Generate BGP routes. |
| GET | `/api/bgp/statistics` | BGP statistics. |
| POST | `/api/bgp/cleanup` | Clean up BGP. |
| POST | `/api/bgp/monitor/start` | Start BGP monitor. |
| POST | `/api/bgp/monitor/stop` | Stop BGP monitor. |
| GET | `/api/bgp/monitor/status` | BGP monitor status. |
| POST | `/api/bgp/monitor/force-check` | Force BGP monitor check. |
| POST | `/api/bgp/monitor/config` | Configure BGP monitor. |
| GET | `/api/bgp/pools` | List BGP route pools. |
| POST | `/api/bgp/pools` | Create BGP pool. |
| GET | `/api/bgp/pools/<pool_name>` | Get BGP pool. |
| PUT | `/api/bgp/pools/<pool_name>` | Update BGP pool. |
| DELETE | `/api/bgp/pools/<pool_name>` | Delete BGP pool. |
| POST | `/api/bgp/pools/batch` | Batch create/update BGP pools. |
| GET | `/api/bgp/pools/<pool_name>/usage` | Pool usage. |
| GET | `/api/device/<device_id>/route-pools` | Route pools for device. |
| POST | `/api/device/<device_id>/route-pools` | Assign route pools to device. |
| DELETE | `/api/device/<device_id>/route-pools` | Remove route pools from device. |

**BGP configure (POST `/api/device/bgp/configure`):**
- **Body:** `device_id`, `device_name`, `interface`, `ipv4`, `ipv6`, `bgp_config` (or `bgp`).  
- `bgp_config` can include: `ipv4_enabled`, `ipv6_enabled`, neighbors, ASN, timers, address families, etc.

---

## 6. OSPF

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ospf/status/<device_id>` | OSPF status for device. |
| GET | `/api/ospf/status/database/<device_id>` | OSPF LSDB for device. |
| POST | `/api/ospf/cleanup` | Clean up OSPF. |
| POST | `/api/ospf/monitor/start` | Start OSPF monitor. |
| POST | `/api/ospf/monitor/stop` | Stop OSPF monitor. |
| GET | `/api/ospf/monitor/status` | OSPF monitor status. |
| POST | `/api/ospf/monitor/force-check` | Force OSPF monitor check. |
| POST | `/api/ospf/monitor/config` | Configure OSPF monitor. |
| GET | `/api/ospf/pools` | List OSPF pools. |
| POST | `/api/ospf/pools` | Create OSPF pool. |
| PUT | `/api/ospf/pools/<pool_name>` | Update OSPF pool. |
| DELETE | `/api/ospf/pools/<pool_name>` | Delete OSPF pool. |

---

## 7. ISIS

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/isis/status/<device_id>` | ISIS status for device. |
| GET | `/api/isis/status/database/<device_id>` | ISIS LSDB for device. |
| POST | `/api/isis/cleanup` | Clean up ISIS. |

---

## 8. FRR

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/frr/status` | Global FRR / container status. |

---

## 9. DHCP

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dhcp/pools` | List DHCP pools. |
| POST | `/api/dhcp/pools` | Create DHCP pool. |
| GET | `/api/dhcp/pools/<pool_name>` | Get DHCP pool. |
| PUT | `/api/dhcp/pools/<pool_name>` | Update DHCP pool. |
| DELETE | `/api/dhcp/pools/<pool_name>` | Delete DHCP pool. |

---

## 10. ARP & Ping

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/device/ping` | Ping from device. |
| POST | `/api/device/arp/check` | ARP check for device. |
| POST | `/api/device/arp/check/batch` | Batch ARP check. |
| POST | `/api/device/arp/request` | Send ARP request. |
| GET | `/api/device/arp/<device_id>` | Get ARP table for device. |
| POST | `/api/arp/monitor/start` | Start ARP monitor. |
| POST | `/api/arp/monitor/stop` | Stop ARP monitor. |
| GET | `/api/arp/monitor/status` | ARP monitor status. |
| POST | `/api/arp/monitor/force-check` | Force ARP monitor check. |
| POST | `/api/arp/monitor/config` | Configure ARP monitor. |

---

## 11. Capture & PCAP

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/capture/start` | Start packet capture. |
| POST | `/api/capture/stop` | Stop packet capture. |
| GET | `/api/capture/download` | Download capture file. |
| GET | `/api/capture/summary` | Capture summary. |
| POST | `/api/pcap/upload` | Upload PCAP file (multipart/form-data). |

---

## 12. Interfaces

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/interfaces` | List server interfaces. |
| POST | `/api/interface/reset` | Reset interface configuration. |

---

## 13. Device Database

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/device/database/info` | Database info and statistics. |
| GET | `/api/device/database/devices` | All devices. Query: `status`. |
| GET | `/api/device/database/devices/<device_id>` | Single device. |
| GET | `/api/device/database/devices/<device_id>/events` | Device events. Query: `limit`. |
| GET | `/api/device/database/devices/<device_id>/statistics` | Device statistics. Query: `hours`. |
| POST | `/api/device/database/backup` | Backup device database. |
| POST | `/api/device/database/restore` | Restore device database. |
| POST | `/api/device/database/cleanup` | Clean up database. |

---

## 14. Pools (BGP / OSPF / DHCP)

- BGP route pools: see [BGP](#5-bgp) section.
- OSPF pools: see [OSPF](#6-ospf) section.
- DHCP pools: see [DHCP](#9-dhcp) section.

---

## 15. DPDK

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dpdk/status` | DPDK status. |
| GET | `/api/dpdk/interfaces` | DPDK-capable interfaces. |
| POST | `/api/dpdk/bind` | Bind interface to DPDK. |
| POST | `/api/dpdk/unbind` | Unbind interface from DPDK. |
| GET | `/api/dpdk/verify` | Verify DPDK setup. |
| POST | `/api/dpdk/hugepages` | Configure hugepages. |
| GET | `/api/dpdk/cpu-vendor` | CPU vendor info. |
| POST | `/api/dpdk/load_modules` | Load DPDK kernel modules. |
| POST | `/api/dpdk/iommu` | Configure IOMMU. |

---

## 16. AI & Assistant

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ai/settings` | Get AI settings. |
| POST | `/api/ai/settings` | Update AI settings. |
| POST | `/api/ai/troubleshoot` | Run troubleshooting. |
| POST | `/api/ai/troubleshoot/unified` | Unified troubleshoot. |
| POST | `/api/ai/troubleshoot/code` | Code troubleshoot. |
| POST | `/api/ai/troubleshoot/system` | System troubleshoot. |
| POST | `/api/ai/troubleshoot/integration` | Integration troubleshoot. |
| POST | `/api/ai/chat` | AI chat. |
| POST | `/api/ai/add-config` | Add configuration. |
| POST | `/api/ai/train-case` | Train case. |
| POST | `/api/ai/import-ostg-configs` | Import OSTG configs. |
| POST | `/api/ai/suggest-config-fix` | Suggest config fix. |
| POST | `/api/ai/device/discover` | Discover devices. |
| POST | `/api/ai/device/provision` | Provision device. |
| POST | `/api/ai/device/manage-config` | Manage device config. |
| GET | `/api/ai/device/health/<device_id>` | Device health. |
| POST | `/api/ai/device/auto-remediate` | Auto-remediate device. |
| POST | `/api/ai/test/suggest` | Suggest tests. |
| POST | `/api/ai/test/run` | Run tests. |
| GET | `/api/ai/test/report/<report_id>` | Get test report. |
| GET | `/api/ai/test/reports/<device_id>` | Get device test reports. |
| POST | `/api/ai/test/case/create` | Create test case. |
| GET | `/api/ai/test/case/list` | List test cases. |
| GET/PUT/DELETE | `/api/ai/test/case/<test_id>` | Get/update/delete test case. |
| POST | `/api/ai/pytest/generate` | Generate pytest. |
| POST | `/api/ai/pytest/run` | Run pytest. |
| GET | `/api/ai/pytest/scripts` | List pytest scripts. |
| GET/DELETE | `/api/ai/pytest/script/<script_name>` | Get/delete pytest script. |
| POST | `/api/ai/pytest/generate-device-specific` | Generate device-specific pytest. |
| POST | `/api/ai/pytest/execute-devices` | Execute pytest on devices. |
| POST | `/api/ai/pytest/execute-device-type` | Execute pytest by device type. |
| POST | `/api/ai/code/generate` | Generate code. |
| POST | `/api/ai/code/refactor` | Refactor code. |
| POST | `/api/ai/code/fix` | Fix code. |
| POST | `/api/ai/code/explain` | Explain code. |
| POST | `/api/ai/code/optimize` | Optimize code. |
| POST | `/api/ai/code/test` | Generate tests for code. |
| POST | `/api/ai/code/documentation` | Generate documentation. |
| POST | `/api/ai/code/generate-advanced` | Advanced code generation. |
| POST | `/api/ai/code/generate-network-script` | Generate network script. |
| POST | `/api/ai/code/generate-config` | Generate config. |
| POST | `/api/ai/code/analyze` | Analyze code. |
| POST | `/api/ai/code/security-scan` | Security scan. |
| POST | `/api/ai/code/optimize-suggestions` | Optimization suggestions. |
| POST | `/api/ai/assistant/suggest` | Assistant suggestion. |
| POST | `/api/ai/assistant/learn` | Assistant learn. |
| GET | `/api/ai/assistant/personalize/<user_id>` | Get personalized assistant. |
| POST | `/api/ai/assistant/contextual-help` | Contextual help. |
| POST | `/api/ai/analytics/performance` | Performance analytics. |
| POST | `/api/ai/analytics/traffic` | Traffic analytics. |
| GET | `/api/ai/analytics/protocol/<protocol>` | Protocol analytics. |
| POST | `/api/ai/analytics/insights` | Analytics insights. |
| POST | `/api/ai/test/plan/generate` | Generate test plan. |
| POST | `/api/ai/test/plan/generate-unit` | Generate unit test plan. |
| POST | `/api/ai/test/plan/generate-document` | Generate test document. |
| POST | `/api/ai/test/plan/generate-pytest` | Generate pytest from plan. |
| POST | `/api/ai/test/plan/generate-pytest-from-spec` | Generate pytest from spec. |
| POST | `/api/ai/test/plan/agent` | Test plan agent. |
| POST | `/api/ai/test/generate-unit` | Generate unit tests. |
| POST | `/api/ai/test/generate-integration` | Generate integration tests. |
| POST | `/api/ai/test/generate-suite` | Generate test suite. |
| POST | `/api/ai/test/coverage` | Test coverage. |
| POST | `/api/ai/model/backup` | Backup AI model. |
| POST | `/api/ai/model/train` | Train AI model. |
| POST | `/api/ai/model/activate` | Activate model. |
| POST | `/api/ai/model/rollback` | Rollback model. |
| GET | `/api/ai/model/versions` | List model versions. |

---

## 17. Debug & Utilities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/debug/mapping` | Get debug mapping. |
| POST | `/api/debug/populate_mapping` | Populate debug mapping. |

---

## General Notes

- **Authentication:** The API does not document built-in auth; if deployed behind a reverse proxy or VPN, use your organization’s access control.
- **Errors:** On failure, endpoints typically return JSON like `{"error": "message"}` with HTTP 4xx/5xx.
- **Logs:** Server logs are written by the OSTG server process; use `journalctl -u ostg-server` (or equivalent) for debugging.

For request/response examples for a specific endpoint, refer to `run_tgen_server.py` or the OSTG client code that calls that endpoint.
