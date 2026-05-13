# OSTG / Netgen Traffic Generator — API Guide

This document describes the REST API provided by the OSTG / Netgen Traffic Generator server. The server runs by default on **port 5051**.

**Base URL:** `http://<server-host>:5051`  
*(If the server was started with `PORT=5050` or `--port 5050`, use port 5050 instead. See [SERVER_STARTUP_MESSAGES.md](SERVER_STARTUP_MESSAGES.md) for startup logs and port details.)*

All API responses are JSON unless noted. Use `Content-Type: application/json` for POST/PUT requests.

## Live event stream (Server-Sent Events)

Long-lived HTTP stream pushing operator-visible events as they happen — no polling.

```bash
curl -N -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     http://<server>:5050/api/events/stream
```

Wire format (one event per blank-line-terminated block):

```
event: state_transition
data: {"event_type":"state_transition","ts":1747044723.4,
       "device_id":"47dce96a-...","protocol":"bgp","state":"Established",
       "detail":{"ipv4":"Established","ipv6":"Established","neighbors":2}}

event: heartbeat
data: {"event_type":"heartbeat","ts":1747044738.4,"subscriber_count":1}
```

Heartbeats fire every 15 s during idle windows so proxies don't time the connection out. The browser-side `EventSource` API and the GUI's `utils.sse_client.SSEWorker` both reconnect automatically on drop.

### Event types emitted today

| Type | Trigger | Payload keys |
|---|---|---|
| `state_transition` | Any protocol monitor sees a state change | `device_id`, `protocol` (bgp/ospf/isis/arp/dhcp), `state`, `detail` |
| `device_applied`   | `POST /api/device/apply` success | `device_id`, `device_name`, `interface` |
| `device_apply_failed` | `POST /api/device/apply` exception | `device_id`, `device_name`, `error` |
| `device_started`   | `POST /api/device/start` success | `device_id` |
| `device_stopped`   | `POST /api/device/stop` success  | `device_id` |
| `device_removed`   | `POST /api/device/remove` success | `device_id`, `device_name`, `db_removed` |
| `stream_started`   | `POST /api/traffic/start` success | `count`, `streams` |
| `stream_stopped`   | `POST /api/traffic/stop` success  | `count`, `stream_ids` |
| `stream_restarted` | `POST /api/traffic/restart` success | `interface`, `count` |
| `heartbeat`        | Every ~15 s during idle windows  | `subscriber_count` |

All envelopes also carry `event_type` and `ts` (unix-epoch float).

| Method | Path | Description |
|---|---|---|
| GET | `/api/events/stream` | Subscribe to the live event feed. Viewer-only. Stays open until the client disconnects. |
| GET | `/api/events/status` | `{"subscribers": N}` — current connection count. |

### Adding a new event type

Producer code anywhere in the server calls:

```python
from utils.event_bus import publish
publish("my_event_type", {"key": "value"})
```

The envelope automatically gains `event_type` and `ts` keys before delivery. The publish is non-blocking and a no-op when nobody is subscribed, so it's safe to drop into hot paths.

---

## Authentication

Two ways to enable auth, both opt-in via env vars:

### Single token (back-compat)

```bash
NETGEN_AUTH_TOKEN=<secret>
```

Every endpoint except `/api/health` requires `Authorization: Bearer <secret>`. The token resolves to **admin** role — full access everywhere.

### Per-role tokens (0.2.1+)

```bash
NETGEN_AUTH_TOKENS_JSON='{"abc...":"admin","def...":"operator","ghi...":"viewer"}'
```

Multiple tokens, each mapped to a role. Three roles in a hierarchy:

| Role | Can do |
|---|---|
| **viewer**   | Read-only: `/api/devices/export`, `/api/...status`, `/api/...history`, `/api/stateful_tcp/sessions`, `/api/stateful_tcp/stats/<id>` |
| **operator** | Everything `viewer` can, plus: mutating endpoints — `/api/devices/import`, `/api/device/apply`, `/api/stateful_tcp/start|stop`, `/api/*/monitor/force-check` |
| **admin**    | Everything (reserved for destructive / global ops) |

The GUI client and `netgen-cli` auto-inject the bearer header when `NETGEN_AUTH_TOKEN` is set client-side. For ad-hoc `curl` calls, pass `-H "Authorization: Bearer $NETGEN_AUTH_TOKEN"`. Health checks (`/api/health`) are deliberately exempt.

**Insufficient role** returns HTTP 403 (not 401 — distinguishes "wrong identity" from "right identity, wrong permission"):

```json
{"ok": false, "error": "insufficient role: this endpoint requires 'operator'"}
```

Older unannotated endpoints (everything pre-0.2.1) still gate on token presence but don't role-check yet. Migration is incremental; see CHANGELOG for which endpoints currently enforce roles.

---

## Table of Contents

1. [Health & Status](#1-health--status)
2. [Monitor Health](#2-monitor-health)
3. [Traffic & Streams](#3-traffic--streams)
4. [Device Lifecycle](#4-device-lifecycle)
5. [Device Configuration](#5-device-configuration)
6. [Device Export / Import](#6-device-export--import)
7. [State History](#7-state-history)
8. [Stateful TCP](#8-stateful-tcp)
9. [BGP](#9-bgp)
10. [OSPF](#10-ospf)
11. [ISIS](#11-isis)
12. [FRR](#12-frr)
13. [DHCP](#13-dhcp)
14. [ARP & Ping](#14-arp--ping)
15. [Capture & PCAP](#15-capture--pcap)
16. [Interfaces](#16-interfaces)
17. [Device Database](#17-device-database)
18. [Pools (BGP / OSPF / DHCP)](#18-pools-bgp--ospf--dhcp)
19. [DPDK](#19-dpdk)
20. [AI & Assistant](#20-ai--assistant)
21. [L2 frame generators & multicast](#21-l2-frame-generators--multicast)
22. [Debug & Utilities](#22-debug--utilities)

---

## 1. Health & Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ping` | Simple liveness check. |
| GET | `/health` | Returns `"Online"` (text). |
| GET | `/healthz` | Returns `{"status": "ok"}` (JSON). |
| GET | `/api/health` | **Auth-exempt** structured health probe. Returns server version + uptime; works without a bearer token so k8s/HAProxy probes don't need credentials. |

**Example:**
```bash
curl http://localhost:5051/api/ping
# {"status": "ok"}

curl http://localhost:5051/api/health
# {"status":"ok","version":"0.2.0","uptime_s":143}
```

---

## 2. Monitor Health

Aggregated status of every background monitor (ARP / BGP / OSPF / ISIS / DHCP). Useful for "is anything still reading state?" dashboards.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/monitors/health` | One-shot snapshot of all monitor threads. |

**Response:**
```json
{
  "ok": true,
  "monitors": {
    "arp":  {"running": true, "stale_secs": 12, "stale": false},
    "bgp":  {"running": true, "stale_secs": 3,  "stale": false},
    "ospf": {"running": true, "stale_secs": 8,  "stale": false},
    "isis": {"running": true, "stale_secs": 9,  "stale": false},
    "dhcp": {"running": true}
  },
  "checked_at": "2026-05-11T22:13:04Z"
}
```

`ok=false` when any monitor isn't running or its DB heartbeat is >90 s stale.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/bgp/monitor/force-check` | Force an immediate BGP poll. |
| POST | `/api/ospf/monitor/force-check` | Force an immediate OSPF poll. |
| POST | `/api/isis/monitor/force-check` | Force an immediate IS-IS poll. |
| POST | `/api/dhcp/monitor/force-check` | Force an immediate DHCP poll. |

---

## 3. Traffic & Streams

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

## 4. Device Lifecycle

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

## 5. Device Configuration

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

## 6. Device Export / Import

Round-trip the entire device topology as JSON. Pairs with `netgen-cli export` and `netgen-cli import` for a quick "snapshot this lab, restore on another box" workflow.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/devices/export` | Export every device's configuration (no runtime state) as JSON. |
| POST | `/api/devices/import` | Apply a previously-exported topology. Body matches the `/export` shape: `{"devices": [...]}`. |

**Export response:**
```json
{
  "count": 3,
  "exported_at": "2026-05-11T22:14:00Z",
  "devices": [ { "device_id": "...", "device_name": "...", ... }, ... ]
}
```

**Import response:**
```json
{ "imported": 3, "failed": 0, "total": 3, "errors": [] }
```

---

## 7. State History

Per-protocol state-transition timeline. Each row is a **state change** — the background monitors de-dup against the previous row so repeated polls in steady-state don't bloat the table. Powers the **Ctrl+H** dialog in the GUI.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/device/database/devices/<device_id>/history` | All-protocol interleaved timeline, newest first. |
| GET | `/api/device/database/devices/<device_id>/history/<protocol>` | Filter by `bgp` / `ospf` / `isis` / `arp` / `dhcp`. Typos return 400. |

Query params: `limit` (default 50).

**Response:**
```json
{
  "device_id": "47dce96a-...",
  "protocol": "bgp",
  "count": 4,
  "history": [
    { "id": 412, "timestamp": "2026-05-11T22:13:04Z",
      "protocol": "bgp", "state": "Established",
      "detail": {"ipv4": "Established", "ipv6": "Established", "neighbors": 2} },
    { "id": 387, "timestamp": "2026-05-11T22:11:48Z",
      "protocol": "bgp", "state": "Active",
      "detail": {"ipv4": "Active", "ipv6": null, "neighbors": 1} },
    ...
  ]
}
```

---

## 8. Stateful TCP

Real-socket TCP traffic generator. Parallel to the scapy-based stateless streams (§3) — sessions here complete an actual 3-way handshake so middleboxes / NAT / proxies see real connections. Workers live in `utils/stateful_tcp.py`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/stateful_tcp/start` | Start a client OR server session. Returns `session_id`. |
| POST | `/api/stateful_tcp/stop` | Stop one session by ID, or all if no `session_id`. |
| GET | `/api/stateful_tcp/sessions` | List every known session (running + finished). |
| GET | `/api/stateful_tcp/stats/<session_id>` | Live counters for one session. |

**Start body — client:**
```json
{
  "role": "client",
  "dst_ip": "10.0.0.5",
  "dst_port": 5001,
  "src_ip": "10.0.0.10",          // optional bind
  "vrf": "vrf-abc12345",          // Linux SO_BINDTODEVICE; no-op on macOS
  "duration_s": 30,
  "payload_bytes": 1024,
  "concurrency": 4,
  "interval_s": 0,
  "expect_echo": true,
  "protocol": "raw",              // "raw" | "http" | "dns" | "sip"
  "dns_qname": "netgen.test",     // only when protocol="dns"
  "sip_host": null,               // only when protocol="sip"; defaults to dst_ip
  "sip_user": "netgen",           // only when protocol="sip"
  "tls": false,
  "tls_verify": false,
  "tls_server_hostname": null
}
```

**Start body — server:**
```json
{
  "role": "server",
  "listen_ip": "0.0.0.0",
  "listen_port": 5001,
  "vrf": null,
  "mode": "echo",                 // "echo" | "discard"
  "protocol": "raw",              // "raw" | "http" | "dns" | "sip"
  "response_bytes": 1024,
  "dns_response_rcode": 3,         // only when protocol="dns" (3=NXDOMAIN, 0=NOERROR, ...)
  "sip_response_status": 200,      // only when protocol="sip" (200/401/503/...)
  "sip_response_reason": "OK",     // SIP reason-phrase paired with the status above
  "tls": false,
  "tls_cert": "/path/cert.pem",   // required when tls=true
  "tls_key":  "/path/key.pem"
}
```

**Stats response (snapshot):**
```json
{
  "session_id": "9286ba6e-...",
  "role": "client",
  "running": true,
  "config": { ... },
  "counters": {
    "uptime_s": 12.3,
    "conns_attempted": 412, "conns_established": 410, "conns_failed": 2,
    "bytes_tx": 419840, "bytes_rx": 419840,
    "avg_handshake_ms": 0.92, "avg_rtt_ms": 1.43, "rtt_samples": 410,
    "avg_kernel_rtt_us": 920.4, "kernel_rtt_samples": 410,
    "retransmits_total": 0,
    "http_status_2xx": 410, "http_status_other": 0,
    "dns_noerror": 0, "dns_nxdomain": 0, "dns_servfail": 0, "dns_other": 0,
    "sip_2xx": 0, "sip_3xx": 0, "sip_4xx": 0, "sip_5xx": 0, "sip_other": 0,
    "last_error": null
  }
}
```

`avg_kernel_rtt_us` and `retransmits_total` come from `TCP_INFO` (Linux only — both are 0 elsewhere). DNS counters bin per-RCODE for `protocol=dns` sessions: `0=NOERROR`, `3=NXDOMAIN`, `2=SERVFAIL`, everything else lumped as `dns_other` with the raw rcode written to `last_error`. Counters are deltas-since-start.

### DNS-over-TCP (RFC 7766)

The `protocol="dns"` mode builds 2-byte-length-prefixed DNS messages — a standard `A` query for `dns_qname` (default `netgen.test`) over TCP. The server answers with the configured `dns_response_rcode` (default 3 = NXDOMAIN). Useful for testing DNS proxies, recursive resolvers handling TCP fallback (queries >512 B), and DNS-aware load balancers.

```bash
# Server returning NOERROR for every query
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"server","listen_port":5353,"protocol":"dns","dns_response_rcode":0}' \
     http://<server>:5050/api/stateful_tcp/start

# Hammer it from the same box (or wherever)
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"client","dst_ip":"127.0.0.1","dst_port":5353,
          "protocol":"dns","duration_s":10,"concurrency":4}' \
     http://<server>:5050/api/stateful_tcp/start
```

### SIP-over-TCP (RFC 3261)

The `protocol="sip"` mode sends well-formed SIP REGISTER messages over TCP and bins the response status by class. Useful for testing SBCs, SIP registrars, and reverse proxies that gate on SIP transactions. The server-side simulator mirrors Via/From/To/Call-ID/CSeq from each request per RFC 3261 §8.2.6.2 so real SIP clients accept the response.

```bash
# Registrar simulator returning 401 Unauthorized (drives auth-retry tests)
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"server","listen_port":5060,"protocol":"sip",
          "sip_response_status":401,"sip_response_reason":"Unauthorized"}' \
     http://<server>:5050/api/stateful_tcp/start

# Run REGISTER traffic at it
curl -X POST -H "Authorization: Bearer $NETGEN_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"role":"client","dst_ip":"127.0.0.1","dst_port":5060,
          "protocol":"sip","sip_host":"registrar.example.com",
          "duration_s":10,"concurrency":2,"interval_s":0.01}' \
     http://<server>:5050/api/stateful_tcp/start
```

`sip_2xx` / `sip_3xx` / `sip_4xx` / `sip_5xx` / `sip_other` counters bin by response-class. `last_error` carries the status code on 4xx/5xx so the operator can spot "every request hit 503" patterns at a glance.

---

## 9. BGP

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

## 10. OSPF

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

## 11. ISIS

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/isis/status/<device_id>` | ISIS status for device. |
| GET | `/api/isis/status/database/<device_id>` | ISIS LSDB for device. |
| POST | `/api/isis/cleanup` | Clean up ISIS. |

---

## 12. FRR

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/frr/status` | Global FRR / container status. |

---

## 13. DHCP

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dhcp/pools` | List DHCP pools. |
| POST | `/api/dhcp/pools` | Create DHCP pool. |
| GET | `/api/dhcp/pools/<pool_name>` | Get DHCP pool. |
| PUT | `/api/dhcp/pools/<pool_name>` | Update DHCP pool. |
| DELETE | `/api/dhcp/pools/<pool_name>` | Delete DHCP pool. |

---

## 14. ARP & Ping

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

## 15. Capture & PCAP

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/capture/start` | Start packet capture. |
| POST | `/api/capture/stop` | Stop packet capture. |
| GET | `/api/capture/download` | Download capture file. |
| GET | `/api/capture/summary` | Capture summary. |
| POST | `/api/pcap/upload` | Upload PCAP file (multipart/form-data). |

---

## 16. Interfaces

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/interfaces` | List server interfaces. |
| POST | `/api/interface/reset` | Reset interface configuration. |

---

## 17. Device Database

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

## 18. Pools (BGP / OSPF / DHCP)

- BGP route pools: see [BGP](#5-bgp) section.
- OSPF pools: see [OSPF](#6-ospf) section.
- DHCP pools: see [DHCP](#9-dhcp) section.

---

## 19. DPDK

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

## 20. AI & Assistant

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

## 21. L2 frame generators &amp; multicast

Periodic-frame emitters for the protocols every datacenter / enterprise lab tests. Built on scapy's existing layer definitions so the wire format follows whatever scapy contrib currently ships.

| Method | Path | Description |
|---|---|---|
| POST | `/api/l2/lacp/start` | LACPDU emitter (IEEE 802.1AX) |
| POST | `/api/l2/lldp/start` | LLDP neighbour-discovery advertiser |
| POST | `/api/l2/vrrp/start` | VRRP v2 or v3 master advertisement |
| POST | `/api/l2/igmp/start` | IGMP v2 or v3 membership-report emitter |
| POST | `/api/l2/pim/start`  | PIM Hello (RFC 7761) — adjacency only |
| POST | `/api/l2/stop` | Stop one session by ID, or all if no ID given |
| GET  | `/api/l2/sessions` | List all sessions |
| GET  | `/api/l2/stats/<session_id>` | Live counters: frames sent / failed / bytes |

Start bodies (every protocol needs at least `iface`):

```json
// LACP — Slow Protocols multicast at 1s (fast) or 30s (slow) cadence
{ "iface": "eth0", "system_mac": "00:11:22:33:44:01", "key": 1, "fast": true }

// LLDP — Chassis/Port/TTL/SystemName TLVs at 30s default
{ "iface": "eth0", "chassis_id": "netgen-host", "port_id": "eth0",
  "ttl_s": 120, "system_name": "netgen" }

// VRRP v3 IPv4 — sends to 224.0.0.18 at 1s default
{ "iface": "eth0", "version": 3, "vrid": 42, "priority": 200,
  "virtual_ips": ["192.168.1.254"], "src_ip": "10.0.0.1" }

// VRRP v3 IPv6 — sends to ff02::12
{ "iface": "eth0", "version": 3, "family": "ipv6", "vrid": 1,
  "virtual_ips": ["fe80::1"], "src_ip": "fe80::aabb:ccdd:eeff:1" }

// IGMP v2 Report — TTL=1, IP-dst = group, type 0x16
{ "iface": "eth0", "version": 2, "group": "239.1.1.1",
  "src_ip": "10.0.0.10" }

// IGMP v2 Leave — override type byte
{ "iface": "eth0", "version": 2, "group": "239.1.1.1",
  "type_code": 23, "src_ip": "10.0.0.10" }

// PIM Hello — 224.0.0.13, IP-proto 103
{ "iface": "eth0", "hold_time": 105, "dr_priority": 1,
  "generation_id": 2882400001 }
```

Stats snapshot:

```json
{
  "session_id": "abc-...",
  "protocol": "lacp",
  "iface": "eth0",
  "config": { ... },
  "running": true,
  "counters": {
    "uptime_s": 14.2,
    "frames_sent": 14, "frames_failed": 0,
    "bytes_sent": 1736,
    "last_send_at": 1747044738.4,
    "last_error": null
  }
}
```

`PermissionError` lands in `last_error` and stops the session — scapy's `sendp()` needs `CAP_NET_RAW` (or root) on Linux, and raw sockets are root-only on macOS BSD. Run the server with the capability or as root if you want L2 frames on the wire.

---

## 22. Debug & Utilities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/debug/mapping` | Get debug mapping. |
| POST | `/api/debug/populate_mapping` | Populate debug mapping. |

---

## General Notes

- **Authentication:** When `NETGEN_AUTH_TOKEN` is set in the server's environment, every endpoint except `/api/health` requires `Authorization: Bearer <token>`. The GUI client and `netgen-cli` auto-inject the header when the same env var is set client-side. See the [Authentication](#authentication) section near the top of this doc for details. For deployments behind a reverse proxy or VPN, use that in addition.
- **Errors:** On failure, endpoints typically return JSON like `{"error": "message"}` with HTTP 4xx/5xx.
- **Logs:** Server logs are written by the server process; use `journalctl -u ostg-server` / `journalctl -u netgen-server` (or equivalent) for debugging.

For request/response examples for a specific endpoint, refer to `run_tgen_server.py` or the GUI client code that calls that endpoint.
