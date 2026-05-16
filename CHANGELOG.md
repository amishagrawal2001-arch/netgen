# Changelog

All notable changes to OSTG / Netgen Traffic Generator will be documented in this file.

## [0.2.6] - 2026-05-16

Operator-quality-of-life release. Drives server install and upgrade
from the client GUI, hardens DPDK install-script path handling, and
documents the `/admin` server console + DPDK install paths in the
landing page.

### Added — install / upgrade server from the client GUI
- **`Help → Install / Upgrade Server...`** opens a two-tab dialog:
  - **Upgrade running server** — pick a `.whl`, click Upload &
    Upgrade. Client POSTs to `/api/admin/upgrade_wheel`; server
    pip-installs into its own Python (`sys.executable -m pip install
    --upgrade --force-reinstall --no-deps <wheel>`) and triggers
    `systemctl restart netgen-server`. Client polls
    `/api/admin/upgrade_wheel/log` for live output, then waits for
    `/api/health` to come back. 30–60 s typical round-trip.
  - **Fresh install via SSH** — pick host / user / password OR SSH
    key file / wheel / installer / optional `--no-dpdk` and
    `--skip-dpdk-build` flags. Client paramiko-connects, sftp-copies
    `install_ostg_complete.py` + the wheel to `/tmp/netgen_install/`
    on the target, then streams the installer's output back into the
    log pane. Heavy path (15–45 min full install).
- New server endpoints:
  - `POST /api/admin/upgrade_wheel` (admin role) — multipart wheel
    upload; rejects non-wheel filenames; refuses overlapping uploads.
  - `GET /api/admin/upgrade_wheel/log` — tails the upgrade log,
    schedules the systemd restart automatically when pip exits 0.
- New file `widgets/install_server_dialog.py` (~480 LOC) — both
  worker threads (`WheelUploadWorker`, `SshInstallWorker`) plus the
  tabbed dialog. Background-threaded so the UI stays responsive
  through a 30-min full install.

### Fixed — `install_dpdk.sh` tx_worker path mismatch
- When `install_dpdk.sh` is invoked from a git checkout
  (`/root/netgen/resources/dpdk/install_dpdk.sh`), `SCRIPT_DIR`
  resolves to that checkout — so `tx_worker` lands at
  `/root/netgen/.../tx_worker/build/tx_worker`. But the server's
  `/api/admin/health` probe only checks `/opt/netgen/`, `/opt/OSTG/`,
  and `/usr/local/bin/tx_worker`, so the admin portal reports
  "tx_worker binary Not built" even though the binary works fine.
- `step_build_tx_worker` now drops symlinks into both canonical
  install roots (when their parents exist) and installs a copy at
  `/usr/local/bin/tx_worker`. Self-healing across re-runs.

### Documentation
- `README.md` — new `### Installing DPDK on the Linux server` and
  `### Server Admin Portal (/admin)` sections; refreshed What's New
  to 0.2.5.
- `docs/index.html` (GitHub Pages landing) — bumped to 0.2.5, three
  new feature cards, top-nav gains Install + Admin links, two new
  full sections: "Install — three paths, your call." (download
  prebuilt / turnkey / split) and "Server admin console — `/admin`"
  with curl examples and `/api/admin/health` JSON shape.

### Notes
- No new dependencies on the server side. Client-side `paramiko` is
  already in `requirements.txt`; the new dialog uses it only when
  the user picks the SSH install tab.
- Wheel still ships as `ostg_trafficgen-0.2.6-py3-none-any.whl` for
  pip-install backwards compatibility.

## [0.2.5] - 2026-05-13

Distribution-only release — adds the multi-platform installer pipeline
and CI workflow that builds prebuilt artifacts for every platform on
tag push. No code/feature changes; functionally equivalent to 0.2.4.

### Added — multi-platform installer pipeline
- `build_windows.ps1` + `ostg_client_windows.spec` — PyInstaller
  single-file `.exe` for Windows. Includes VS_VERSION_INFO so the
  Explorer right-click → Properties tab shows the right version, no
  hidden cmd.exe window behind the GUI, `-Folder` flag for a faster-
  startup one-folder build.
- `build_appimage.sh` — Linux universal AppImage. Wraps a PyInstaller
  one-folder dist in an AppDir + AppRun + .desktop and squashes it
  with appimagetool. Works on any modern distro — Ubuntu / Debian /
  RHEL / Rocky / Fedora / Arch / SUSE — without package-manager fuss.
- `build_dmg.sh` already shipped a `.dmg`; this release fixes its
  PyInstaller spec to (a) read version from `pyproject.toml`,
  (b) include the new `server/` package, (c) declare L2-protocol
  scapy contrib modules as hidden imports.
- `.github/workflows/release.yml` — CI matrix that runs on every
  `v*` tag push. Builds wheel (Ubuntu), .dmg (macOS), .exe (Windows),
  .AppImage (Ubuntu) in parallel; pulls release notes from
  CHANGELOG.md; creates the GitHub release with all four artifacts
  attached. Manual `workflow_dispatch` trigger also enabled for
  testing the matrix without cutting a release.

### Added — install docs
- `INSTALL.md` restructured: **Option A** (download a pre-built
  installer from the Releases page) is now the recommended path;
  **Option B** (build from source) kept for devs and operators who
  want control.

### Changed
- `.gitignore` adds an explicit exception for the three project-owned
  PyInstaller specs so `*.spec` doesn't block them every time.

## [0.2.4] - 2026-05-12

L2 frame generators + multicast protocols. Five new periodic-emitter
workers cover the datacenter / enterprise lab essentials —
LAG / discovery / first-hop redundancy / multicast group reports /
PIM adjacency.

### Added — L2 frame generators (new module)
- `utils/l2_protocols.py` — session-registered periodic frame senders
  built on scapy's existing layer definitions. Five protocols:
  - **LACP** (IEEE 802.1AX): Slow-Protocols multicast LACPDU with
    configurable system/port priority, key, port number, state bits,
    and fast (1 s) or slow (30 s) cadence.
  - **LLDP** (IEEE 802.1AB): Chassis-ID + Port-ID + TTL + System-Name +
    System-Description TLVs at 30-second cadence.
  - **VRRP** (RFC 3768 v2, RFC 5798 v3): IPv4 + IPv6 first-hop-
    redundancy advertisements. v2 IPv4-only, v3 IPv4 or IPv6 via
    `family` kwarg.
  - **IGMP** (RFC 2236 v2, RFC 3376 v3): Membership Reports with
    configurable group and override `type_code` for Leave / Query.
  - **PIM Hello** (RFC 7761 §4.3): adjacency-only, with configurable
    hold-time / DR-priority / generation-ID.
- Each session has counters: `frames_sent`, `frames_failed`,
  `bytes_sent`, `last_send_at`, `last_error`. Errors surface clearly
  on `last_error` so root-only `PermissionError` doesn't look like
  silent failure.

### Added — `server/l2_routes.py` Blueprint (#9 continued)
- Fourth Blueprint extracted from the monolith. Routes:
  - `POST /api/l2/<protocol>/start` — operator-only
  - `POST /api/l2/stop` — operator-only, by session_id or all
  - `GET  /api/l2/sessions` — viewer-only
  - `GET  /api/l2/stats/<session_id>` — viewer-only
- Per-protocol kwargs filtered to the factory's known fields so the
  body shape is forward-compatible.

### Added — `netgen-cli l2 ...` subcommands
- `netgen-cli l2 start-lacp --iface eth0 [--fast]`
- `netgen-cli l2 start-lldp --iface eth0 --chassis-id ... --port-id ...`
- `netgen-cli l2 start-vrrp --iface eth0 --vrid 1 --virtual-ips 192.168.1.254 [--version 2|3] [--family ipv4|ipv6]`
- `netgen-cli l2 start-igmp --iface eth0 --group 239.1.1.1 [--version 2|3]`
- `netgen-cli l2 start-pim --iface eth0`
- `netgen-cli l2 stop [--session-id ...]`
- `netgen-cli l2 list`
- `netgen-cli l2 stats --session-id ...`

### Tests — 94 → 103 (+9)
`tests/test_l2_protocols.py`:
- LACP wire format: ethertype + Slow Protocols multicast destination
- LLDP: ethertype + multicast destination + TLV ordering
- VRRPv3 IPv4: destination MAC + IP + protocol byte + VRID round-trip
- VRRPv2: separate packet class verification
- IGMPv2: report target = group, TTL = 1
- IGMPv3: destination 224.0.0.22 regardless of group
- PIM Hello: 224.0.0.13 + proto 103 + type-byte assertion
- Session registry round-trip (stop semantics work even without an
  active worker thread)
- `stop_session` on unknown ID returns False

## [0.2.3] - 2026-05-12

Live updates land in the Devices tab. Multi-device bulk-edit replaces
the "edit 8 devices in a row" loop. The SSE event-producer set covers
device lifecycle + stream lifecycle, not just protocol state.

### Added — Devices tab live SSE refresh
- `DevicesTab` now spins up an `SSEWorker` on first
  `reload_devices_from_server()` and re-fetches the device DB whenever
  one of these events arrives: `state_transition`, `device_applied`,
  `device_started`, `device_stopped`, `device_removed`. A 500 ms
  debounce coalesces fabric-wide bursts (e.g. one BGP flap touching
  every device) into a single refresh.
- `cleanup_threads()` tears the worker down with the same forced-
  close pattern Topology uses — GUI shutdown stays sub-second even on
  a quiet fabric.

### Added — multi-device bulk-edit
- New ⧉ toolbar button on the Devices tab. Operates on the table's
  multi-row selection.
- `_BulkEditDialog` lets the operator pick a field (VLAN, IPv4, IPv4
  gateway, Loopback IPv4, MAC) + start value + step. First selected
  row gets `start`, second gets `start + step`, etc. Saves the
  "8-routers-in-a-batch" edit loop.
- Per-protocol checkboxes (BGP / OSPF / ISIS / DHCP / VXLAN) with
  tri-state: enable / disable / leave-as-is.
- Live preview shows the first-3-and-last computed plan before
  commit so operators can sanity-check the auto-increment.
- 10 new pytest cases: per-field increment, step=0 'same-value',
  VLAN clamp at 4094, protocol checkbox states, multi-field
  combined plan.

### Added — SSE event producers
The previously single-source `state_transition` event got a sibling
set covering operator-visible lifecycle changes. Producers in
`run_tgen_server.py` now emit:

- `device_applied`  — after `/api/device/apply` success
- `device_apply_failed` — after `/api/device/apply` exception
- `device_started`  — after `/api/device/start`
- `device_stopped`  — after `/api/device/stop`
- `device_removed`  — after `/api/device/remove`
- `stream_started`  — after `/api/traffic/start`
- `stream_stopped`  — after `/api/traffic/stop`
- `stream_restarted` — after `/api/traffic/restart`

All publication goes through a new `_emit_event()` helper that
swallows bus failures so a misconfigured bus can never break a route.
11 new pytest cases lock down the event-type strings + payload
shapes so a future typo doesn't silently break Devices-tab live
refresh.

## [0.2.2] - 2026-05-12

Live updates + SIP + broader role coverage. The GUI no longer has to
poll the server to see state changes — a Server-Sent Events stream
pushes protocol transitions in real time. Stateful TCP gains SIP-over-
TCP (RFC 3261). The role-auth surface gains 24 newly-annotated
endpoints. Two more route groups extracted from the monolith.

### Added — Server-Sent Events live updates
- New `/api/events/stream` endpoint pushes operator-visible events
  to subscribers in real time via the SSE wire format. Today emits
  `state_transition` events when any protocol monitor (ARP / BGP /
  OSPF / ISIS / DHCP) observes a change; future producers can plug in
  via `utils.event_bus.publish(event_type, payload)`.
- `/api/events/status` returns the current subscriber count.
- `utils/event_bus.py` — thread-safe pub/sub with bounded queues. Slow
  consumers drop oldest events instead of blocking the producer.
- `utils/sse_client.py` — `SSEWorker(QThread)` for the GUI. Parses
  the `event:`/`data:`/`retry:` wire format, auto-reconnects on drop,
  emits Qt signals.
- **Topology tab live-refresh**: the SSE worker spins up on first
  successful Refresh; `state_transition` events trigger a coalesced
  re-fetch (750ms debounce) so LED colours and chip backgrounds
  update without operator intervention.
- Chose SSE over WebSocket: no new dependency, the existing bearer-
  token middleware applies for free, and server-push-only matches
  the workload.

### Added — SIP-over-TCP (#12 L7 expansion)
- New `protocol="sip"` option in `utils/stateful_tcp.py` sends RFC 3261
  REGISTER messages framed over TCP. Client parses the response
  status line and bins per status class: `sip_2xx`, `sip_3xx`,
  `sip_4xx`, `sip_5xx`, `sip_other`.
- Server-side registrar simulator answers with the configured
  `sip_response_status` / `sip_response_reason` (default 200/OK).
  Mirrors Via/From/To/Call-ID/CSeq from the request per RFC 3261
  §8.2.6.2 so real SIP clients accept the response.
- 4 new pytest cases: 2xx counter increment, 4xx counter (401
  Unauthorized) increment, REGISTER builder wire-format
  verification, response-header-mirror verification.

### Added — broader per-role auth coverage
- 24 additional endpoints annotated with `@require_role`:
  - **operator**: `/api/traffic/{start,stop,restart}`,
    `/api/device/{start,stop,apply}`, `/api/device/bgp/{configure,start,stop}`,
    `/api/device/ospf/{configure,start,stop}`,
    `/api/device/isis/{configure,start,stop}`,
    `/api/device/frr/{start,stop}`,
    `/api/bgp/routes/{advertise,withdraw}`
  - **viewer**: `/api/{bgp,ospf,isis}/status/<device_id>`
  - **admin**: `/api/device/{remove,cleanup}`, `/api/{bgp,ospf,isis}/cleanup`
- Total annotated routes now 40 (was 16). Continues the incremental
  migration documented in the per-role auth section.

### Added — modularization (#9 continued)
- New `server/device_db_routes.py` Blueprint with the three
  `/api/device/database/devices/<id>/{events,history,statistics}`
  routes. Auth + DeviceDatabase instance injected via `configure()`.
- New `server/events_routes.py` Blueprint with the SSE stream +
  status endpoints.
- `run_tgen_server.py` now registers three Blueprints (was one);
  ~200 LOC total moved out of the monolith across the two releases.

### Changed
- `DeviceDatabase.add_state_transition()` now publishes a
  `state_transition` event to the in-process bus after every
  successful row insert, so live-update consumers don't have to poll
  the history endpoint.

## [0.2.1] - 2026-05-12

Operator quality-of-life release. One-click templates for devices and
traffic streams, DNS-over-TCP in the stateful generator, per-role auth,
and the start of the server-side modularization. Test count climbs from
36 → 59.

### Added — one-click templates
- **Device templates** (`utils/device_templates.py`) — registry of
  8 pre-baked profiles wired into the Add Device dialog via a new
  "Quick start from template" dropdown. Includes: Bare host, iBGP
  peer, eBGP peer, OSPFv2 backbone, OSPFv2+v3 dual-stack, IS-IS
  Level-1-2, DHCP client, VXLAN VTEP. Templates pre-fill the form
  in one click; operator only edits IP/VLAN to match their lab.
- **Traffic templates** (`utils/traffic_templates.py`) — registry of
  7 pre-baked stream profiles wired into the Add Stream dialog. UDP
  line-rate 64 B, UDP IMIX, LAG/RSS/ECMP hash test (with modifiers),
  NLAT latency probe, VXLAN-encapsulated UDP, ICMP echo flood,
  VLAN-tagged UDP. Templates emit the same `stream_data` shape the
  dialog already consumes via `populate_stream_fields()`, so the
  same dict can be saved to session.json or shipped to
  `/api/traffic/start`.
- Both registries tolerate missing widgets and unknown template keys
  silently so they ship safely ahead of form rearrangements.

### Added — stateful-TCP DNS-over-TCP (#12 L7 expansion)
- New `protocol="dns"` option in `utils/stateful_tcp.py` builds
  RFC 7766-framed DNS-over-TCP queries (2-byte length prefix +
  RFC 1035 header + question section) and parses responses. Client
  tallies per-RCODE buckets: `dns_noerror`, `dns_nxdomain`,
  `dns_servfail`, `dns_other`.
- Server side answers each framed query with the configured RCODE
  (default NXDOMAIN, override with `dns_response_rcode`). Supports
  connection reuse per RFC 7766 §6.2.1 — multiple queries per TCP
  connection.
- New `dns_qname` client knob for the queried name (default
  `netgen.test`).
- 3 new pytest cases: NXDOMAIN-counter round-trip, NOERROR
  round-trip, framing-builder unit test (length prefix + question
  section integrity).

### Added — per-role auth (#11)
- New `NETGEN_AUTH_TOKENS_JSON` env var carrying a `{token: role}`
  mapping. Three roles: **viewer** (read-only), **operator**
  (mutating non-destructive), **admin** (everything).
- `@require_role("viewer"|"operator"|"admin")` decorator. Endpoints
  annotated so far: `/api/devices/export` (viewer), `/api/devices/import`
  (operator), `/api/device/database/devices/<id>/history` (viewer),
  `/api/stateful_tcp/start|stop` (operator), `/api/stateful_tcp/sessions|stats`
  (viewer), all `/api/{arp,bgp,ospf,isis,dhcp}/monitor/force-check`
  (operator).
- Back-compat: `NETGEN_AUTH_TOKEN=<secret>` (the legacy 0.2.0 form)
  is still accepted and resolves to admin. When neither env var is
  set, auth is fully off — every request is implicit admin.
- 7 new pytest cases: hierarchy enforcement (admin > operator > viewer),
  unknown-token rejection, single-token-→admin back-compat, decorator
  rejects invalid role at decoration time.

### Added — server modularization pattern-setter (#9, partial)
- New `server/` package with `server/stateful_tcp_routes.py` —
  the four `/api/stateful_tcp/*` routes extracted into a Flask
  Blueprint. Demonstrates the migration contract for future
  route-group extractions (state-history, monitor-health, device
  export/import, FRR, BGP).
- `run_tgen_server.py` now registers the Blueprint instead of
  defining the routes inline. The role-decorator is injected via
  `configure(require_role=...)` so the Blueprint stays decoupled
  from the auth-state global.
- ~130 lines moved out of the 18,500-line monolith. Pattern in
  place for the rest of the migration.

### Changed
- Stateful-TCP REST body docs updated with the new `dns_qname` and
  `dns_response_rcode` fields.

## [0.2.0] - 2026-05-11

A large multi-area release that lands per-device VRF isolation, an
operator-grade Topology view, real-socket stateful TCP, persistent
state-history timelines, and a headless CLI. End-to-end test coverage
expanded to 36 pytest cases (was 18).

### Added — protocol / routing
- **Per-device VRF isolation**: each managed FRR Docker container now sits
  in its own Linux VRF (`vrf-<short>`) with a deterministic table-ID in
  the `1000..1999` band. Eliminates cross-device route bleed when running
  10s of emulated devices on a single host.
- **DHCP-client default route migration**: `_migrate_dhcp_route_to_vrf()`
  moves the dhclient-installed default out of `main` and into the device
  VRF; subsequent gateway lookups fall back to the VRF table when the
  main table is empty.
- **VXLAN underlay routes per-VRF**: `_device_vrf_args()` now iterates
  the canonical `_MANAGED_CONTAINER_PREFIXES` list so both `ostg-frr-`
  and `dhcp-frr-` container roles get VRF context.
- **BGP control plane via per-VRF `vtysh`**: `advertise_bgp_routes`,
  `withdraw_bgp_routes`, `get_bgp_route_statistics` rewritten for the
  Docker-FRR path; legacy system-FRR helpers early-return when
  `DOCKER_FRR_AVAILABLE` is set.
- **Force-check endpoints** for the DHCP and IS-IS monitors so operators
  can poke a stuck monitor without restarting the server.

### Added — REST API
- `GET /api/health` — auth-exempt liveness probe (paired with the new
  bearer-token middleware so health checks still work without a token).
- `GET /api/monitors/health` — aggregated background-monitor status
  (ARP / BGP / OSPF / ISIS / DHCP) with per-monitor staleness flag.
- `GET /api/devices/export` and `POST /api/devices/import` — round-trip
  the entire device topology as JSON.
- `GET /api/device/database/devices/<id>/history[/<protocol>]` —
  per-protocol state-transition timeline. Whitelist on protocol so
  typos return 400, not silently-empty.
- `POST /api/dhcp/monitor/force-check` and
  `POST /api/isis/monitor/force-check`.
- **Stateful TCP** surface (`/api/stateful_tcp/start|stop|sessions|stats/<id>`)
  — see below.

### Added — bearer-token auth
- Optional `NETGEN_AUTH_TOKEN` env var enables an `Authorization: Bearer …`
  middleware on every endpoint except `/api/health`.
- Client auto-injection: `run_tgen_client.py` monkey-patches
  `requests.get/post/...` to add the header when the env var is set, so
  the GUI and `netgen-cli` both work transparently.

### Added — netgen-cli (headless companion)
New entry-point `netgen-cli` registered in `pyproject.toml`:

- `health`, `list`, `export`, `import`, `apply`, `status`, `wait`
- `tcp start-client | start-server | stop | list | stats` — wraps the
  stateful-TCP REST surface end-to-end.

### Added — Topology tab (IXNetwork-style)
- New `widgets/topology_tab.py`. Pulled into the main window as a
  third tab after Devices.
- **Port lane**: green port badges (one per server interface) with
  per-port device counts.
- **Device cards** with vertical protocol-stack chips
  (ETH → IPv4 → IPv6 → BGP → OSPF → ISIS → DHCP) coloured by
  per-protocol up/configured/idle state.
- **Status LED** on each card (green / amber / red / grey) driven by
  the rolled-up protocol health.
- **Cables** (cubic-Bezier curves) drawn from each card down to its
  bound port; **peer edges** between cards for BGP / OSPF / IS-IS
  matches.
- **Property panel** on the right (QSplitter) that updates on
  selection with device metadata, per-protocol detail, and an
  async-loaded "Recent transitions" section.
- **Layout toggle**: Hierarchical (port-based, default) and Circular.
  Per-layout-mode position persistence via `QSettings`.
- **Zoom**: proper `QGraphicsView` subclass with cursor-anchored
  wheel zoom, +/− toolbar buttons, Fit button, scale clamped to
  `[0.2, 6.0]`.
- **Async refresh** via `_JsonFetchWorker(QThread)` so the GUI stays
  responsive while `/api/device/database/devices` resolves.
- Double-click a card to view its full server-side JSON config.

### Added — Devices tab quality-of-life
- **Apply progress widget** — inline `QProgressBar` + status label
  during multi-device apply.
- **Monitor-health indicator** — small badge that turns red when any
  background monitor is stale.
- **Filter bar** — `QLineEdit` that hides table rows by substring match.
- **Keyboard shortcuts**:
  - `Ctrl+Return` — Apply selected
  - `Ctrl+S` — Start selected
  - `Ctrl+X` — Stop selected
  - `Ctrl+R` — Refresh ARP
  - `Ctrl+F` — Focus filter
  - `Ctrl+H` — **State-history dialog** (per-protocol tab, change-only
    timeline)
  - `Ctrl+J` — **View Device Config dialog** (read-only JSON viewer
    with copy-to-clipboard)
- **Retry Failed Apply** — re-runs only the failed devices from the
  previous batch via the same worker plumbing.
- **Settings dialog** — server URL, auth token, monitor poll intervals;
  backed by `QSettings("Netgen", "Client")`.

### Added — state history
- New `device_state_history` table with `add_state_transition()` /
  `get_state_history()` DB helpers. De-duped against the most-recent
  row so 5s-poll monitors don't bloat the table while a device sits in
  steady state.
- All five monitors (ARP / BGP / OSPF / ISIS / DHCP) write a row on
  every observed state change.
- Surfaced in the GUI (Ctrl+H) and the Topology property panel.

### Added — stateful TCP (new traffic mode)
New module `utils/stateful_tcp.py` — a real-socket parallel to the
scapy-based stateless generator:

- Per-session client / server workers with completed 3-way handshakes,
  graceful close, and bounded shutdown.
- **Counters**: conns attempted / established / failed, bytes tx / rx,
  avg handshake ms, userspace + kernel-reported RTT, total retransmits.
- **VRF binding** via `SO_BINDTODEVICE` (Linux); graceful no-op +
  warning on macOS / Windows.
- **TCP_INFO scraping** for retransmit counts and kernel-smoothed RTT
  (Linux only, falls back cleanly).
- **TLS** opt-in on both sides (client default `tls_verify=False` for
  self-signed test envs).
- **HTTP/1.1 framing** mode — client sends a Content-Length-framed
  POST, server replies 200 OK; new counters `http_status_2xx /
  http_status_other`.
- 7 new pytest cases covering loopback echo, TLS handshake, HTTP
  framing, VRF graceful degrade, TCP_INFO degrade, and the dead-target
  failure path.

### Fixed
- **GUI freeze** on slow `/api/device/database/devices` reads —
  Topology refresh and the per-protocol history fetches were sync
  `requests.get` on the GUI thread; now run on a `QThread` worker.
- **"wrapped C/C++ object has been deleted"** crash on second topology
  refresh — `worker.finished.connect(deleteLater)` left
  `self._fetch_worker` pointing at a tombstoned C++ wrapper. Fixed via
  a dedicated `_on_worker_finished` slot that clears the Python handle
  before `deleteLater`.
- **`AttributeError: setHandlesChildEvents`** on `_DeviceCard` —
  PyQt5 newer builds dropped the method from `QGraphicsItemGroup`;
  rewrote `_DeviceCard` as a `QGraphicsRectItem` with parented children
  so click events route naturally.
- **Topology wheel zoom didn't fire** — an instance-attribute assign to
  `view.wheelEvent` is bypassed by Qt's C++ virtual dispatch. Fixed
  via a proper `_TopologyView(QGraphicsView)` subclass.
- **DHCP-`_parse_gateway` blind to VRF table** after the default-route
  migration — now falls back to the VRF table when the main one is
  empty.
- **`UnboundLocalError: device_db`** in 16 server endpoints — local
  `device_db = DeviceDatabase()` rebinds promoted the module-global
  reference to a local. Stripped all 16.
- **HTTP framing deadlock** in stateful TCP — `_read_http_response`
  read to EOF on both sides; both ends blocked waiting for the other
  to half-close. Rewrote as `_read_http_message` that honours
  `Content-Length`.
- **State-history endpoint** silently returning empty for `/history/bgpp`
  (typo) — now whitelist-validates the protocol and returns 400 with a
  helpful list.
- **QSettings position round-trip** on older PyQt5 builds where lists
  come back wrapped in `QVariant` — defensive coercion via
  `_read_pos_setting()` recovers them.
- **Topology `_rebuild` ordering**: Python caches now cleared *before*
  `scene.clear()` with a `_rebuilding` re-entrancy guard on
  `_redraw_links`.

### Changed
- Stateful TCP server now tracks active handler threads and joins them
  on `stop_session()` with a 2 s budget instead of leaking them.
- State-history dialog timeout lowered from 5 s → 3 s per tab.

## [0.1.52] - 2026-04-13

### Fixed
- ISIS configuration and UEC packet generation issues
- Stream stop: wait for threads to complete before removing from tracker
- MAC Decrement mode and server connectivity checks
- RX packet counting and stream statistics
- BGP IPv4 neighbor state database updates and IPv6 BGP/OSPFv6 configuration
- Interface name handling and OSPF/IS-IS interface normalization
- IPv6 OSPF passive configuration

### Improved
- UI responsiveness: instant device table display and selection preservation
- NVIDIA GPU and DPU diagnostics support
- Link down troubleshooting script and diagnostics
- VXLAN ARP/FDB configuration and SVI IP assignment

### Added
- Comprehensive link down troubleshooting script
- `--nvidia-install-help` command-line option
- Console output for nvidia-smi installation instructions
